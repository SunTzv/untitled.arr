"""
Audio verification module for untitled.arr.
Uses ffprobe to confirm container integrity and validate recorded duration against metadata.
"""

from __future__ import annotations
import re
import subprocess
from pathlib import Path
from typing import Tuple
from config import CONFIG, RecorderConfig, format_duration, get_ffmpeg_path, get_ffprobe_path


# ==============================================================================
# AUDIO VERIFIER
# ==============================================================================

class AudioVerifier:
    def __init__(self, config: RecorderConfig = CONFIG):
        self.config = config

    # ==========================================================================
    # STREAM PROBING
    # ==========================================================================

    @staticmethod
    def get_file_duration(file_path: Path) -> float:
        """Runs ffprobe to measure the exact stream duration of an audio container."""
        if not file_path.exists() or file_path.stat().st_size < 1024:
            return 0.0

        cmd = [
            get_ffprobe_path(),
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(file_path)
        ]
        try:
            raw = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
            return float(raw)
        except Exception:
            return 0.0

    # ==========================================================================
    # SILENCE & VOLUME DETECTION
    # ==========================================================================

    def check_audio_silence(self, file_path: Path, recorded_len: float) -> Tuple[bool, str]:
        """
        Analyzes audio volume and detects silence periods using ffmpeg.
        Returns (is_valid, failure_reason).
        """
        noise_db = getattr(self.config, "silence_noise_db", -60.0)
        min_sec = getattr(self.config, "silence_min_seconds", 2.0)
        max_ratio = getattr(self.config, "max_silence_ratio", 0.10)

        cmd = [
            get_ffmpeg_path(),
            "-v", "info",
            "-nostdin",
            "-i", str(file_path),
            "-af", f"silencedetect=noise={noise_db}dB:d={min_sec},volumedetect",
            "-f", "null",
            "-"
        ]
        try:
            res = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True
            )
            if res.returncode != 0:
                return True, ""

            stderr = res.stderr

            # Check max volume
            max_vol_match = re.search(r"max_volume:\s*(-?[\d\.]+|-\s*inf)\s*dB", stderr)
            if max_vol_match:
                val_str = max_vol_match.group(1).replace(" ", "")
                if "-inf" in val_str:
                    max_vol = float("-inf")
                else:
                    try:
                        max_vol = float(val_str)
                    except ValueError:
                        max_vol = 0.0

                if max_vol < -50.0:
                    return False, f"silent recording (max volume: {max_vol:.1f} dB)"

            # Check silence duration
            total_silence = 0.0
            current_silence_start = None

            for line in stderr.splitlines():
                start_match = re.search(r"silence_start:\s*(-?[\d\.]+)", line)
                if start_match:
                    current_silence_start = max(0.0, float(start_match.group(1)))

                end_match = re.search(r"silence_end:\s*(-?[\d\.]+)(?:\s*\|\s*silence_duration:\s*(-?[\d\.]+))?", line)
                if end_match:
                    if end_match.group(2) is not None:
                        silence_dur = float(end_match.group(2))
                    elif current_silence_start is not None:
                        silence_dur = max(0.0, float(end_match.group(1)) - current_silence_start)
                    else:
                        silence_dur = 0.0
                    total_silence += silence_dur
                    current_silence_start = None

            if current_silence_start is not None:
                trailing_dur = max(0.0, recorded_len - current_silence_start)
                total_silence += trailing_dur

            silence_ratio = total_silence / recorded_len if recorded_len > 0 else 1.0
            if silence_ratio > max_ratio:
                pct = int(round(silence_ratio * 100))
                limit_pct = int(round(max_ratio * 100))
                return False, f"too much silence ({pct}% of take, max {limit_pct}%)"

            return True, ""
        except Exception:
            # If analysis itself fails, accept
            return True, ""

    # ==========================================================================
    # COMPLETION VALIDATION
    # ==========================================================================

    def verify_recording(self, file_path: Path, expected_duration: float) -> Tuple[bool, float, str]:
        """
        Validates whether the recorded file meets completion thresholds and has valid audio.
        Returns: (is_valid, recorded_duration, reason)
        """
        if not file_path.exists():
            return False, 0.0, "File does not exist"

        file_size = file_path.stat().st_size
        if file_size < 4096:
            return False, 0.0, f"File too small ({file_size} bytes)"

        recorded_len = self.get_file_duration(file_path)
        if recorded_len <= 0.0:
            return False, 0.0, "Failed to read audio duration via ffprobe"

        min_required = expected_duration * self.config.completion_ratio
        if recorded_len < min_required:
            pct = int(round((recorded_len / expected_duration * 100))) if expected_duration > 0 else 0
            return (
                False,
                recorded_len,
                f"only listened to {format_duration(recorded_len)} of {format_duration(expected_duration)} ({pct}%, needed {int(self.config.completion_ratio * 100)}%)"
            )

        if getattr(self.config, "reject_silent_takes", True):
            silence_ok, silence_reason = self.check_audio_silence(file_path, recorded_len)
            if not silence_ok:
                return False, recorded_len, silence_reason

        return True, recorded_len, f"Complete ({format_duration(recorded_len)} / {format_duration(expected_duration)})"
