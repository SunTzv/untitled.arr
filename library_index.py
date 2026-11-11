"""
Library index and file management module for untitled.arr.
Maintains a crash-safe JSON index of recorded tracks and guarantees collision-free file naming.
"""

from __future__ import annotations
import json
import os
import re
import threading
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
        self._lock = threading.RLock()
        self._index_mtime: float = 0.0
        self.load_index()

    def _refresh_from_disk(self) -> None:
        """Re-reads library.json if another process (e.g. `main.py remove`) changed it,
        so the running daemon never resurrects an entry that was just removed."""
        try:
            mtime = self.index_file.stat().st_mtime
        except OSError:
            return
        if mtime == self._index_mtime:
            return
        try:
            with open(self.index_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.index = data
                self._index_mtime = mtime
        except Exception:
            pass

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
        Treats any existing file not owned by this track ID as a collision,
        appending [<shortid>] and then -2, -3... until a free path is found.
        """
        with self._lock:
            clean_title = self.sanitize_filename(meta.title)
            clean_artist = self.sanitize_filename(meta.artist)
            base_name = f"{clean_title} ({clean_artist})"
            target_path = self.output_dir / f"{base_name}.opus"

            def is_owned_by_this_track(path: Path) -> bool:
                entry = self.index.get(meta.canonical_id)
                return bool(entry and entry.get("file") == path.name)

            if not target_path.exists() or is_owned_by_this_track(target_path):
                return target_path

            # Collision with file not owned by this track
            short_id = meta.canonical_id.split(":")[-1][:6] if meta.canonical_id else "track"
            if not short_id:
                short_id = "track"

            candidate = self.output_dir / f"{base_name} [{short_id}].opus"
            if not candidate.exists() or is_owned_by_this_track(candidate):
                return candidate

            counter = 2
            while True:
                candidate = self.output_dir / f"{base_name} [{short_id}]-{counter}.opus"
                if not candidate.exists() or is_owned_by_this_track(candidate):
                    return candidate
                counter += 1

    # ==========================================================================
    # INDEX PERSISTENCE (ATOMIC) & REBUILD
    # ==========================================================================

    def rebuild_from_files(self) -> int:
        """
        Scans output directory for non-hidden *.opus files, reads spotify_track_id
        from Vorbis comments via mutagen, and rebuilds the library index.
        """
        from mutagen.oggopus import OggOpus

        if not self.output_dir.exists():
            return 0

        with self._lock:
            rebuilt_count = 0
            for file_path in sorted(self.output_dir.glob("*.opus")):
                if file_path.name.startswith("."):
                    continue

                try:
                    audio = OggOpus(file_path)
                    if audio is None:
                        continue
                    track_id_tags = audio.get("spotify_track_id")
                    if not track_id_tags or not track_id_tags[0]:
                        continue

                    track_id = track_id_tags[0]
                    title = audio.get("title", [file_path.stem])[0]
                    artists = [a for a in audio.get("artist", []) if a] or ["Unknown Artist"]
                    composers = [c for c in audio.get("composer", []) if c]
                    artist = self.config.artist_separator.join(artists)
                    album = audio.get("album", ["Unknown Album"])[0]
                    duration = round(audio.info.length, 2) if (audio.info and audio.info.length) else 0.0
                    mtime = int(file_path.stat().st_mtime)

                    self.index[track_id] = {
                        "file": file_path.name,
                        "title": title,
                        "artist": artist,
                        "artists": artists,
                        "composers": composers,
                        "album": album,
                        "duration": duration,
                        "recorded_duration": duration,
                        "recorded_at": mtime,
                    }
                    rebuilt_count += 1
                except Exception:
                    pass

            if rebuilt_count > 0:
                self.save_index()

            return rebuilt_count

    def load_index(self) -> None:
        """Loads track index from library.json, or rebuilds if missing/corrupt."""
        with self._lock:
            if not self.index_file.exists():
                self.index = {}
                self.rebuild_from_files()
                return

            try:
                with open(self.index_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("Library index format invalid (expected dictionary)")
                self.index = data
                self._index_mtime = self.index_file.stat().st_mtime
            except Exception as e:
                timestamp = int(time.time())
                corrupt_file = self.index_file.with_name(f"{self.index_file.name}.corrupt-{timestamp}")
                try:
                    self.index_file.rename(corrupt_file)
                except Exception:
                    pass
                print(f"Warning: Corrupt library index backed up to {corrupt_file.name}: {e}")
                self.index = {}
                self.rebuild_from_files()

    def save_index(self) -> bool:
        """Writes track index atomically via temporary file and replace."""
        with self._lock:
            tmp_file = self.index_file.with_name(f".library_{os.getpid()}_{int(time.time() * 1000)}.tmp")
            try:
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(self.index, f, indent=2, ensure_ascii=False)
                tmp_file.replace(self.index_file)
                self._index_mtime = self.index_file.stat().st_mtime
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
        with self._lock:
            self._refresh_from_disk()
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

        with self._lock:
            self._refresh_from_disk()
            dest_path = self.generate_destination_path(meta)

            try:
                temp_file.replace(dest_path)

                self.index[meta.canonical_id] = {
                    "file": dest_path.name,
                    "title": meta.title,
                    "artist": meta.artist,
                    "artists": list(meta.artists) or [meta.artist],
                    "composers": list(meta.composers),
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

    # ==========================================================================
    # REMOVAL (used by remover.py)
    # ==========================================================================

    def remove_entry(self, track_id: str) -> bool:
        """Drops a track from the index and saves. Does NOT touch files."""
        with self._lock:
            self._refresh_from_disk()
            if track_id not in self.index:
                return False
            del self.index[track_id]
            return self.save_index()
