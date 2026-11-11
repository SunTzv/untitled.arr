"""
macOS media reader and metadata parser for untitled.arr.
Communicates with Spotify via AppleScript (osascript) to fetch track data and status.
"""

from __future__ import annotations
import subprocess
import sys
from typing import Optional
from player_mpris import TrackMetadata


class MacOSSpotifyPlayer:
    """macOS Spotify player integration using AppleScript."""

    DELIMITER = "\x1f"
    SCRIPT = f"""
    if application "Spotify" is running then
        tell application "Spotify"
            set pState to player state as string
            set tId to id of current track as string
            set tArtist to artist of current track as string
            set tName to name of current track as string
            set tAlbum to album of current track as string
            set tDuration to (duration of current track as string)
            set tPos to (player position as string)
            set tArt to (artwork url of current track as string)
            set tTrackNum to (track number of current track as string)
            set tDiscNum to (disc number of current track as string)
            return pState & "{DELIMITER}" & tId & "{DELIMITER}" & tArtist & "{DELIMITER}" & tName & "{DELIMITER}" & tAlbum & "{DELIMITER}" & tDuration & "{DELIMITER}" & tPos & "{DELIMITER}" & tArt & "{DELIMITER}" & tTrackNum & "{DELIMITER}" & tDiscNum
        end tell
    else
        return ""
    end if
    """

    @classmethod
    def get_track_data(cls) -> Optional[TrackMetadata]:
        if sys.platform != "darwin":
            return None

        try:
            cmd = ["osascript", "-e", cls.SCRIPT]
            raw = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
            if not raw:
                return None

            parts = raw.split(cls.DELIMITER)
            if len(parts) < 10:
                return None

            (
                status_raw,
                track_id,
                artist,
                title,
                album,
                duration_ms_raw,
                pos_sec_raw,
                art_url,
                track_no,
                disc_no,
            ) = [p.strip() for p in parts[:10]]

            # Map status: "playing", "paused", "stopped"
            status = status_raw.capitalize()

            try:
                duration = float(duration_ms_raw) / 1000.0
            except (ValueError, TypeError):
                duration = 0.0

            try:
                position = float(pos_sec_raw)
            except (ValueError, TypeError):
                position = 0.0

            clean_track_id = track_id
            if not clean_track_id.startswith("spotify:track:"):
                clean_track_id = f"spotify:track:{clean_track_id.split(':')[-1]}"

            is_ad = (
                ":ad:" in clean_track_id
                or title.lower() in ("advertisement", "spotify")
                or (0.0 < duration < 15.0)
            )

            return TrackMetadata(
                status=status,
                track_id=clean_track_id,
                artist=artist or "Unknown Artist",
                title=title or "Unknown Title",
                album=album or "Unknown Album",
                album_artist=artist or "Unknown Artist",
                track_number=track_no or "1",
                disc_number=disc_no or "1",
                duration=duration,
                position=position,
                art_url=art_url,
                is_ad=is_ad
            )
        except Exception:
            return None
