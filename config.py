"""
Configuration module for untitled.arr.
Defines storage paths, audio encoding parameters, and playback thresholds.
"""

from __future__ import annotations
import json
import os
from dataclasses import dataclass
from pathlib import Path


# ==============================================================================
# CONFIGURATION DATA CLASS
# ==============================================================================

@dataclass
class RecorderConfig:
    # Storage settings
    output_dir: Path = Path.home() / "Music" / "Spotify"
    naming_pattern: str = "{track} ({artist})"
    embed_artwork: bool = True

    # Audio encoding settings
    audio_codec: str = "libopus"
    audio_bitrate: str = "96k"
    audio_sample_rate: str = "48000"
    audio_channels: int = 2
    opus_application: str = "audio"
    opus_compression_level: int = 10

    # Detection thresholds
    completion_ratio: float = 0.95        # Minimum percentage of track listened (95%)
    max_start_offset: float = 2.5         # Maximum allowable start delay (seconds)
    max_seek_discrepancy: float = 2.5     # Seek tolerance between checks (seconds)
    max_pause_seconds: float = 60.0       # Max pause tolerance before drop (seconds)
    poll_interval: float = 0.5            # Interval between MPRIS polls (seconds)

    # Audio isolation
    sink_name: str = "SpotifySink"
    prefer_isolated_sink: bool = True

    @property
    def index_file(self) -> Path:
        return self.output_dir / "library.json"

    @property
    def temp_file(self) -> Path:
        return self.output_dir / f".current_recording_{os.getpid()}.opus"

    def ensure_directories(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)


# ==============================================================================
# CONFIGURATION LOADER
# ==============================================================================

def load_config(config_path: Path | None = None) -> RecorderConfig:
    """Loads configuration overrides from config.json if present."""
    cfg = RecorderConfig()

    if config_path is None:
        config_path = Path(__file__).resolve().parent / "config.json"

    if config_path.is_file():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "output_dir" in data:
                    data["output_dir"] = Path(os.path.expanduser(data["output_dir"]))
                for key, val in data.items():
                    if hasattr(cfg, key):
                        setattr(cfg, key, val)
        except Exception as e:
            print(f"Warning: Failed to load configuration from {config_path}: {e}")

    cfg.ensure_directories()
    return cfg


# ==============================================================================
# HUMAN TIME FORMATTING HELPERS
# ==============================================================================

def format_duration(seconds: float) -> str:
    """Formats seconds into human-readable duration like 3:45 or 1:04:20."""
    total_sec = max(0, int(round(seconds)))
    hrs = total_sec // 3600
    mins = (total_sec % 3600) // 60
    secs = total_sec % 60
    if hrs > 0:
        return f"{hrs}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def format_human_time(seconds: float) -> str:
    """Formats seconds into conversational phrasing like '33 mins' or '2 hrs 15 mins'."""
    total_sec = max(0, int(round(seconds)))
    hrs = total_sec // 3600
    mins = (total_sec % 3600) // 60
    if hrs > 0 and mins > 0:
        return f"{hrs} hr{'s' if hrs != 1 else ''} {mins} min{'s' if mins != 1 else ''}"
    if hrs > 0:
        return f"{hrs} hr{'s' if hrs != 1 else ''}"
    return f"{mins} min{'s' if mins != 1 else ''}"


# ==============================================================================
# SINGLETON INSTANCE
# ==============================================================================

CONFIG = load_config()
