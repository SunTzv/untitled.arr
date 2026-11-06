"""
MPRIS reader and metadata parser for untitled.arr.
Communicates with Spotify via playerctl to fetch track data and status.
"""

from __future__ import annotations
import subprocess
from dataclasses import dataclass
from typing import Optional


# ==============================================================================
# METADATA DATA CLASS
# ==============================================================================

@dataclass
class TrackMetadata:
    status: str            # "Playing", "Paused", "Stopped"
    track_id: str          # Spotify URI or MPRIS track ID
    artist: str
    title: str
    album: str
    album_artist: str
    track_number: str
    disc_number: str
    duration: float        # Duration in seconds
    position: float        # Current playback position in seconds
    art_url: str           # Cover art URL
    is_ad: bool            # True if advertisement or invalid track

    @property
    def canonical_id(self) -> str:
        """Standardizes URI to spotify:track:<id>."""
        if self.track_id.startswith("/com/spotify/track/"):
            return f"spotify:track:{self.track_id.split('/')[-1]}"
        return self.track_id


# ==============================================================================
# MPRIS QUERY ENGINE
# ==============================================================================

class SpotifyMPRIS:
    DELIMITER = "\x1f"
    FORMAT = (
        "{{status}}" + DELIMITER +
        "{{mpris:trackid}}" + DELIMITER +
        "{{xesam:artist}}" + DELIMITER +
        "{{xesam:title}}" + DELIMITER +
        "{{xesam:album}}" + DELIMITER +
        "{{xesam:albumArtist}}" + DELIMITER +
        "{{xesam:trackNumber}}" + DELIMITER +
        "{{xesam:discNumber}}" + DELIMITER +
        "{{mpris:length}}" + DELIMITER +
        "{{mpris:artUrl}}" + DELIMITER +
        "{{position}}"
    )

    @classmethod
    def get_track_data(cls, player_name: str = "spotify") -> Optional[TrackMetadata]:
        """Queries the active player for current track metadata."""
        cmd = ["playerctl", "-p", player_name, "metadata", "--format", cls.FORMAT]
        try:
            raw = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
            if not raw:
                return None

            parts = raw.split(cls.DELIMITER)
            if len(parts) < 11:
                return None

            (
                status,
                raw_track_id,
                artist,
                title,
                album,
                album_artist,
                track_no,
                disc_no,
                length_us,
                art_url,
                pos_us
            ) = [p.strip() for p in parts[:11]]

            # Convert duration and position from microseconds to seconds
            duration = float(length_us) / 1_000_000.0 if length_us.isdigit() else 0.0
            position = float(pos_us) / 1_000_000.0 if pos_us.isdigit() else 0.0

            # Normalize URI
            clean_track_id = raw_track_id
            if "/com/spotify/track/" in raw_track_id:
                clean_track_id = f"spotify:track:{raw_track_id.split('/')[-1]}"

            # Detect ads and non-music interludes
            is_ad = (
                ":ad:" in clean_track_id
                or "/ad/" in raw_track_id
                or title.lower() in ("advertisement", "spotify")
                or (0.0 < duration < 15.0)
                or not ("track:" in clean_track_id or "/track/" in raw_track_id)
            )

            return TrackMetadata(
                status=status,
                track_id=clean_track_id,
                artist=artist or "Unknown Artist",
                title=title or "Unknown Title",
                album=album or "Unknown Album",
                album_artist=album_artist or artist or "Unknown Artist",
                track_number=track_no or "1",
                disc_number=disc_no or "1",
                duration=duration,
                position=position,
                art_url=art_url,
                is_ad=is_ad
            )

        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
        except Exception:
            return None
