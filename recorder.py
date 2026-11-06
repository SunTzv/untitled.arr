"""
Audio recording process manager for untitled.arr.
Spawns and supervises the FFmpeg capture subprocess, ensuring clean container finalization.
"""

from __future__ import annotations
import os
import signal
import subprocess
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

    def start_recording(self, source: str, temp_path: Optional[Path] = None) -> bool:
        """Spawns FFmpeg to capture audio from the designated source."""
        if self.is_recording():
            self.stop_recording()

        self.active_temp_path = temp_path or self.config.temp_file
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
            "-f", "pulse",
            "-i", source,
            "-c:a", self.config.audio_codec,
            "-b:a", self.config.audio_bitrate,
            "-vbr", "on",
            "-application", self.config.opus_application,
            "-compression_level", str(self.config.opus_compression_level),
            "-ac", str(self.config.audio_channels),
            "-ar", self.config.audio_sample_rate,
            str(self.active_temp_path)
        ]

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
            print(f"[ERROR] Failed to start FFmpeg: {e}")
            self.proc = None
            return False

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
        self.stop_recording()
        if self.active_temp_path and self.active_temp_path.exists():
            try:
                self.active_temp_path.unlink()
            except OSError:
                pass
        if self.stderr_log and self.stderr_log.exists():
            try:
                self.stderr_log.unlink()
            except OSError:
                pass
