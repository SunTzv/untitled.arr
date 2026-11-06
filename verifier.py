"""
Audio verification module for untitled.arr.
Uses ffprobe to confirm container integrity and validate recorded duration against metadata.
"""

from __future__ import annotations
import subprocess
from pathlib import Path
from typing import Tuple
from config import CONFIG, RecorderConfig, format_duration


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
            "ffprobe",
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
    # COMPLETION VALIDATION
    # ==========================================================================

    def verify_recording(self, file_path: Path, expected_duration: float) -> Tuple[bool, float, str]:
        """
        Validates whether the recorded file meets completion thresholds.
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

        return True, recorded_len, f"Complete ({format_duration(recorded_len)} / {format_duration(expected_duration)})"
