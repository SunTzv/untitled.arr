"""
Library index and file management module for untitled.arr.
Maintains a crash-safe JSON index of recorded tracks and guarantees collision-free file naming.
"""

from __future__ import annotations
import json
import os
import re
import time
from pathlib import Path
from typing import Dict, Optional
from config import CONFIG, RecorderConfig
from player_mpris import TrackMetadata


# ==============================================================================
# LIBRARY MANAGER
# ==============================================================================

class LibraryManager:
    def __init__(self, config: RecorderConfig = CONFIG):
        self.config = config
        self.index_file = self.config.index_file
        self.output_dir = self.config.output_dir
        self.index: Dict[str, dict] = {}
        self.load_index()

    # ==========================================================================
    # FILENAME SANITIZATION & PATH RESOLUTION
    # ==========================================================================

    @staticmethod
    def sanitize_filename(name: str) -> str:
        """Strips invalid filesystem characters and trims trailing whitespace/dots."""
        cleaned = re.sub(r'[\\/*?:"<>|\x00-\x1f]', "", name).strip(" .")
        return cleaned[:120] if cleaned else "Unknown"

    def generate_destination_path(self, meta: TrackMetadata) -> Path:
        """
        Generates target file path in format: Track (Artist).opus
        Appends track ID disambiguator only if a name collision occurs with a different track.
        """
        clean_title = self.sanitize_filename(meta.title)
        clean_artist = self.sanitize_filename(meta.artist)
        base_name = f"{clean_title} ({clean_artist})"
        target_path = self.output_dir / f"{base_name}.opus"

        if target_path.exists():
            existing_id = None
            for tid, entry in self.index.items():
                if entry.get("file") == target_path.name:
                    existing_id = tid
                    break

            if existing_id and existing_id != meta.canonical_id:
                short_id = meta.canonical_id.split(":")[-1][:6]
                target_path = self.output_dir / f"{base_name} [{short_id}].opus"

        return target_path

    # ==========================================================================
    # INDEX PERSISTENCE (ATOMIC)
    # ==========================================================================

    def load_index(self) -> None:
        """Loads track index from library.json."""
        if not self.index_file.exists():
            self.index = {}
            return

        try:
            with open(self.index_file, "r", encoding="utf-8") as f:
                self.index = json.load(f)
        except Exception as e:
            print(f"Warning: Could not read library index: {e}")
            self.index = {}

    def save_index(self) -> bool:
        """Writes track index atomically via temporary file and replace."""
        tmp_file = self.index_file.with_suffix(".tmp")
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(self.index, f, indent=2, ensure_ascii=False)
            tmp_file.replace(self.index_file)
            return True
        except Exception as e:
            print(f"Error: Could not save library index: {e}")
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)
            return False

    # ==========================================================================
    # RECORDING COMMITS & CHECKS
    # ==========================================================================

    def is_recorded(self, track_id: str) -> bool:
        """
        Checks if track ID has already been recorded and file exists on disk.
        Purges missing references to allow re-recording if file was deleted.
        """
        if track_id not in self.index:
            return False

        entry = self.index[track_id]
        file_name = entry.get("file")
        if not file_name:
            return False

        target_file = self.output_dir / file_name
        if not target_file.exists():
            del self.index[track_id]
            self.save_index()
            return False

        return True

    def commit_recording(self, temp_file: Path, meta: TrackMetadata, recorded_duration: float) -> Optional[Path]:
        """Atomically moves temp file to destination and commits track metadata to index."""
        if not temp_file.exists():
            return None

        dest_path = self.generate_destination_path(meta)

        try:
            temp_file.replace(dest_path)

            self.index[meta.canonical_id] = {
                "file": dest_path.name,
                "title": meta.title,
                "artist": meta.artist,
                "album": meta.album,
                "duration": meta.duration,
                "recorded_duration": round(recorded_duration, 2),
                "recorded_at": int(time.time()),
            }
            self.save_index()
            return dest_path
        except Exception as e:
            print(f"Error: Could not save recording to {dest_path}: {e}")
            return None
