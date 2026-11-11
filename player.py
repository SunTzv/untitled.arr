"""
Universal player interface for untitled.arr.
Automatically delegates metadata extraction to the platform-native player provider:
  - Windows: GlobalSystemMediaTransportControls (winsdk / PowerShell / ctypes)
  - Linux: MPRIS via playerctl
  - macOS: AppleScript via osascript
"""

from __future__ import annotations
import sys
from typing import Optional
from player_mpris import TrackMetadata


class SpotifyPlayer:
    """Universal Spotify metadata provider across Linux, Windows, and macOS."""

    @classmethod
    def get_track_data(cls, player_name: str = "spotify") -> Optional[TrackMetadata]:
        """Queries the active Spotify player for current track metadata on the host OS."""
        if sys.platform == "win32":
            from player_windows import WindowsSpotifyPlayer
            return WindowsSpotifyPlayer.get_track_data()
        elif sys.platform == "darwin":
            from player_macos import MacOSSpotifyPlayer
            return MacOSSpotifyPlayer.get_track_data()
        else:
            # Linux / Unix
            from player_mpris import SpotifyMPRIS
            return SpotifyMPRIS.get_linux_track_data(player_name)
