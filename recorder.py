"""
Audio recording process manager for untitled.arr.
Spawns and supervises the FFmpeg capture subprocess, ensuring clean container finalization.
"""

from __future__ import annotations
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional, Tuple
from config import CONFIG, RecorderConfig


# ==============================================================================
# AUDIO RECORDER
# ==============================================================================

class AudioRecorder:
    def __init__(self, config: RecorderConfig = CONFIG):
        self.config = config
        self.proc: Optional[subprocess.Popen] = None
        self.active_temp_path: Optional[Path] = None
        self.stderr_log: Optional[Path] = None

    # ==========================================================================
    # PROCESS LIFECYCLE
    # ==========================================================================

    def is_recording(self) -> bool:
        """Returns True if the FFmpeg subprocess is currently running."""
        return self.proc is not None and self.proc.poll() is None

    def finished_cleanly(self) -> bool:
        """Returns True if the FFmpeg subprocess has exited cleanly with return code 0."""
        return self.proc is not None and self.proc.poll() == 0

    def get_new_temp_file(self) -> Path:
        """Generates a collision-free temporary file path for a recording take."""
        timestamp_ms = int(time.time() * 1000)
        return self.config.output_dir / f".current_recording_{os.getpid()}_{timestamp_ms}.opus"

    @staticmethod
    def cleanup_file(path: Optional[Path]) -> None:
        """Removes temporary recording file and any associated FFmpeg log."""
        if path and path.exists():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            log_path = path.with_suffix(".ffmpeg.log")
            if log_path.exists():
                try:
                    log_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @classmethod
    def cleanup_orphaned_takes(cls, output_dir: Path) -> int:
        """Purges any leftover temporary recordings from previous sessions."""
        cleaned = 0
        if not output_dir.exists():
            return 0
        for p in output_dir.glob(".current_recording_*"):
            try:
                p.unlink(missing_ok=True)
                cleaned += 1
            except OSError:
                pass
        return cleaned

    def _input_args(self, source: str) -> list[str]:
        """Returns ffmpeg input arguments for audio capture."""
        return ["-f", "pulse", "-i", source]

    def start_recording(
        self,
        source: str,
        temp_path: Optional[Path] = None,
        max_seconds: Optional[float] = None
    ) -> bool:
        """Spawns FFmpeg to capture audio from the designated source."""
        if self.is_recording():
            self.stop_recording()

        self.active_temp_path = temp_path or self.get_new_temp_file()
        if self.active_temp_path.exists():
            try:
                self.active_temp_path.unlink()
            except OSError:
                pass

        self.stderr_log = self.active_temp_path.with_suffix(".ffmpeg.log")

        cmd = [
            "ffmpeg",
            "-y",
            "-nostdin",
            *self._input_args(source),
        ]
        if max_seconds is not None and max_seconds > 0:
            cmd.extend(["-t", f"{max_seconds:.3f}"])

        cmd.extend([
            "-c:a", self.config.audio_codec,
            "-b:a", self.config.audio_bitrate,
            "-vbr", "on",
            "-application", self.config.opus_application,
            "-compression_level", str(self.config.opus_compression_level),
            "-ac", str(self.config.audio_channels),
            "-ar", self.config.audio_sample_rate,
            str(self.active_temp_path)
        ])

        log_file = None
        try:
            log_file = open(self.stderr_log, "w", encoding="utf-8")
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=log_file,
                preexec_fn=os.setsid
            )
            return True
        except Exception as e:
            print(f"Error: Failed to start FFmpeg: {e}")
            self.proc = None
            return False
        finally:
            if log_file is not None:
                log_file.close()

    def stop_recording(self) -> Tuple[int, str]:
        """
        Sends SIGINT to allow Opus container headers to finalize cleanly.
        Returns: (return_code, error_diagnostic)
        """
        if not self.proc:
            return 0, ""

        ret_code = 0
        error_msg = ""
        try:
            if self.proc.poll() is None:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGINT)
                try:
                    self.proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                    self.proc.wait(timeout=2.0)
            ret_code = self.proc.returncode or 0
        except Exception as e:
            error_msg = f"Exception stopping recorder: {e}"
        finally:
            self.proc = None

        # Capture stderr diagnostic if exit was unexpected
        if self.stderr_log and self.stderr_log.exists():
            try:
                with open(self.stderr_log, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                    if ret_code != 0 and ret_code != -signal.SIGINT and ret_code != 255:
                        error_msg = "".join(lines[-10:])
                self.stderr_log.unlink(missing_ok=True)
            except Exception:
                pass

        return ret_code, error_msg

    def discard_take(self) -> None:
        """Stops capture and purges the temporary file."""
        target_path = self.active_temp_path
        self.stop_recording()
        self.active_temp_path = None
        self.cleanup_file(target_path)
