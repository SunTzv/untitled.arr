"""
Windows media reader and metadata parser for untitled.arr.
Supports Windows 10/11 via GlobalSystemMediaTransportControls (GSMTC) with winsdk,
PowerShell WinRT fallback, and Spotify window title inspection.
"""

from __future__ import annotations
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from typing import Optional, Tuple
from player_mpris import TrackMetadata

# Cache for resolved Spotify track data on Windows (artist + title -> {track_id, art_url, duration, ...})
_WINDOWS_TRACK_CACHE: dict = {}
_WINDOWS_CACHE_LOCK = threading.Lock()


# ==============================================================================
# WINSDK (GSMTC) PROVIDER
# ==============================================================================

class WinSDKProvider:
    """Uses WinRT via winsdk for fast, native access to Windows Media Controls."""
    _available: Optional[bool] = None
    _loop: Optional[asyncio.AbstractEventLoop] = None
    _thread: Optional[threading.Thread] = None

    @classmethod
    def is_available(cls) -> bool:
        if cls._available is not None:
            return cls._available
        if sys.platform != "win32":
            cls._available = False
            return False
        try:
            from winsdk.windows.media.control import (  # noqa: F401
                GlobalSystemMediaTransportControlsSessionManager as _
            )
            cls._available = True
        except (ImportError, Exception):
            cls._available = False
        return cls._available

    @classmethod
    def _ensure_event_loop(cls) -> asyncio.AbstractEventLoop:
        if cls._loop is None or cls._loop.is_closed():
            cls._loop = asyncio.new_event_loop()
            cls._thread = threading.Thread(target=cls._loop.run_forever, daemon=True, name="WinSDKLoop")
            cls._thread.start()
        return cls._loop

    @classmethod
    async def _async_query(cls) -> Optional[dict]:
        from winsdk.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as SessionManager
        )
        manager = await SessionManager.request_async()
        if not manager:
            return None

        target = None
        # Check all media sessions for Spotify
        try:
            sessions = manager.get_sessions()
            for s in sessions:
                app_id = (s.source_app_user_model_id or "").lower()
                if "spotify" in app_id:
                    target = s
                    break
        except Exception:
            pass

        if not target:
            curr = manager.get_current_session()
            if curr and "spotify" in (curr.source_app_user_model_id or "").lower():
                target = curr

        if not target:
            return None

        # Playback status: 0=Closed, 1=Opened, 2=Changing, 3=Stopped, 4=Playing, 5=Paused
        status_str = "Stopped"
        pb_info = target.get_playback_info()
        if pb_info:
            st = pb_info.playback_status
            if st == 4:
                status_str = "Playing"
            elif st == 5:
                status_str = "Paused"
            elif st == 3:
                status_str = "Stopped"

        props = await target.try_get_media_properties_async()
        if not props:
            return None

        title = props.title or ""
        artist = props.artist or ""
        album = props.album_title or ""
        album_artist = props.album_artist or artist
        track_number = str(props.track_number or 1)

        duration = 0.0
        position = 0.0
        timeline = target.get_timeline_properties()
        if timeline:
            if timeline.end_time:
                duration = timeline.end_time.total_seconds()
            if timeline.position:
                position = timeline.position.total_seconds()
                # Estimate current playback position when playing
                if status_str == "Playing" and hasattr(timeline, "last_updated_time") and timeline.last_updated_time:
                    try:
                        updated_ts = timeline.last_updated_time.timestamp()
                        elapsed = time.time() - updated_ts
                        if 0 <= elapsed < 3600:
                            position = min(duration, position + elapsed)
                    except Exception:
                        pass

        return {
            "status": status_str,
            "title": title,
            "artist": artist,
            "album": album,
            "album_artist": album_artist,
            "track_number": track_number,
            "duration": duration,
            "position": position,
            "art_url": "",
        }

    @classmethod
    def get_track_data(cls) -> Optional[dict]:
        if not cls.is_available():
            return None
        loop = cls._ensure_event_loop()
        future = asyncio.run_coroutine_threadsafe(cls._async_query(), loop)
        try:
            return future.result(timeout=1.5)
        except Exception:
            return None


# ==============================================================================
# POWERSHELL WINRT FALLBACK PROVIDER
# ==============================================================================

class PowerShellWinRTProvider:
    """Uses PowerShell to query GlobalSystemMediaTransportControls when winsdk is not installed."""
    _available: Optional[bool] = None
    _last_query_time: float = 0.0
    _cached_result: Optional[dict] = None

    POWERSHELL_SCRIPT = """
[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media.Control,ContentType=WindowsRuntime] | Out-Null
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { 
    $_.Name -eq 'AsTask' -and 
    $_.GetParameters().Count -eq 1 -and 
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' 
})[0]
function Await-Task($task, $type) {
    $asTask = $asTaskGeneric.MakeGenericMethod($type)
    $netTask = $asTask.Invoke($null, @($task))
    $netTask.Wait(1200) | Out-Null
    return $netTask.Result
}
try {
    $mgr = Await-Task ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
    if ($null -eq $mgr) { exit 0 }
    $session = $mgr.GetSessions() | Where-Object { $_.SourceAppUserModelId -like "*Spotify*" } | Select-Object -First 1
    if ($null -eq $session) {
        $curr = $mgr.GetCurrentSession()
        if ($curr -and $curr.SourceAppUserModelId -like "*Spotify*") { $session = $curr }
    }
    if ($null -eq $session) { exit 0 }

    $props = Await-Task ($session.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
    if ($null -eq $props) { exit 0 }

    $pb = $session.GetPlaybackInfo()
    $status = if ($pb) { $pb.PlaybackStatus.ToString() } else { "Unknown" }

    $tl = $session.GetTimelineProperties()
    $pos = if ($tl -and $tl.Position) { $tl.Position.TotalSeconds } else { 0.0 }
    $dur = if ($tl -and $tl.EndTime) { $tl.EndTime.TotalSeconds } else { 0.0 }

    $out = @{
        status = $status
        title = $props.Title
        artist = $props.Artist
        album = $props.AlbumTitle
        album_artist = $props.AlbumArtist
        track_number = $props.TrackNumber
        position = $pos
        duration = $dur
    }
    $out | ConvertTo-Json -Compress
} catch {
    exit 0
}
"""

    @classmethod
    def get_track_data(cls) -> Optional[dict]:
        if sys.platform != "win32":
            return None

        # Rate-limit process spawns if needed
        now = time.time()
        if now - cls._last_query_time < 0.2 and cls._cached_result is not None:
            return cls._cached_result

        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-Command", cls.POWERSHELL_SCRIPT
        ]
        try:
            cls._last_query_time = now
            raw = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=2.0).strip()
            if not raw:
                cls._cached_result = None
                return None
            data = json.loads(raw)
            # Map playback status to title case
            st = str(data.get("status", "")).capitalize()
            if st not in ("Playing", "Paused", "Stopped"):
                st = "Playing" if st in ("Opened", "Changing") else "Stopped"
            data["status"] = st
            data["track_number"] = str(data.get("track_number") or 1)
            data["duration"] = float(data.get("duration") or 0.0)
            data["position"] = float(data.get("position") or 0.0)
            data["art_url"] = ""
            cls._cached_result = data
            return data
        except Exception:
            cls._cached_result = None
            return None


# ==============================================================================
# WINDOW TITLE FALLBACK (ZERO-DEPENDENCY)
# ==============================================================================

class WindowsTitleProvider:
    """Uses ctypes to inspect Spotify top-level window titles (zero external dependencies)."""

    _last_title: str = ""
    _play_start_time: float = 0.0
    _estimated_pos: float = 0.0

    @classmethod
    def get_track_data(cls) -> Optional[dict]:
        if sys.platform != "win32":
            return None

        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            spotify_titles = []

            WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def enum_windows_callback(hwnd, _):
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buff, length + 1)
                        title = buff.value
                        class_buff = ctypes.create_unicode_buffer(256)
                        user32.GetClassNameW(hwnd, class_buff, 256)
                        class_name = class_buff.value
                        # Spotify's main window uses Chrome_WidgetWin_0
                        if class_name == "Chrome_WidgetWin_0" and title:
                            spotify_titles.append(title)
                return True

            user32.EnumWindows(WNDENUMPROC(enum_windows_callback), 0)

            if not spotify_titles:
                return None

            # Look for a title with " - "
            active_title = ""
            for t in spotify_titles:
                if " - " in t and not t.lower().startswith("spotify"):
                    active_title = t
                    break

            if not active_title:
                # Check for idle/paused titles
                for t in spotify_titles:
                    t_lower = t.lower()
                    if t_lower in ("spotify", "spotify free", "spotify premium"):
                        return {
                            "status": "Paused",
                            "title": "",
                            "artist": "",
                            "album": "",
                            "album_artist": "",
                            "track_number": "1",
                            "duration": 0.0,
                            "position": 0.0,
                            "art_url": "",
                            "is_ad": False,
                        }
                    elif t_lower == "advertisement":
                        return {
                            "status": "Playing",
                            "title": "Advertisement",
                            "artist": "Spotify",
                            "album": "",
                            "album_artist": "",
                            "track_number": "1",
                            "duration": 30.0,
                            "position": 0.0,
                            "art_url": "",
                            "is_ad": True,
                        }
                return None

            parts = active_title.split(" - ", 1)
            artist = parts[0].strip()
            title = parts[1].strip()

            now = time.time()
            if active_title != cls._last_title:
                cls._last_title = active_title
                cls._play_start_time = now
                cls._estimated_pos = 0.0
            else:
                cls._estimated_pos = max(0.0, now - cls._play_start_time)

            return {
                "status": "Playing",
                "title": title,
                "artist": artist,
                "album": "Unknown Album",
                "album_artist": artist,
                "track_number": "1",
                "duration": 0.0,  # Will be enriched by ArtistResolver/cache if available
                "position": cls._estimated_pos,
                "art_url": "",
                "is_ad": False,
            }
        except Exception:
            return None


# ==============================================================================
# WINDOWS SPOTIFY PLAYER
# ==============================================================================

class WindowsSpotifyPlayer:
    """Universal Spotify metadata reader for Windows."""

    @classmethod
    def get_track_data(cls) -> Optional[TrackMetadata]:
        if sys.platform != "win32":
            return None

        # 1. Try winsdk (GSMTC - fast, native)
        raw_data = None
        if WinSDKProvider.is_available():
            raw_data = WinSDKProvider.get_track_data()

        # 2. Try PowerShell WinRT (GSMTC fallback)
        if not raw_data:
            raw_data = PowerShellWinRTProvider.get_track_data()

        # 3. Try Window Title (ctypes fallback)
        if not raw_data:
            raw_data = WindowsTitleProvider.get_track_data()

        if not raw_data:
            return None

        status = raw_data.get("status", "Stopped")
        title = raw_data.get("title", "") or "Unknown Title"
        artist = raw_data.get("artist", "") or "Unknown Artist"
        album = raw_data.get("album", "") or "Unknown Album"
        album_artist = raw_data.get("album_artist", "") or artist
        track_number = str(raw_data.get("track_number", "1"))
        duration = float(raw_data.get("duration", 0.0))
        position = float(raw_data.get("position", 0.0))
        art_url = raw_data.get("art_url", "")

        # Generate deterministic track ID for library indexing
        cache_key = f"{artist.lower()}:{title.lower()}"
        with _WINDOWS_CACHE_LOCK:
            cached = _WINDOWS_TRACK_CACHE.get(cache_key)
            if cached:
                canonical_id = cached.get("track_id", "")
                if not art_url:
                    art_url = cached.get("art_url", "")
                if duration <= 0.0:
                    duration = cached.get("duration", 0.0)
                if album == "Unknown Album" and cached.get("album"):
                    album = cached.get("album")
            else:
                digest = hashlib.sha1(f"{artist.lower()}|{title.lower()}".encode("utf-8")).hexdigest()[:22]
                canonical_id = f"spotify:track:{digest}"
                _WINDOWS_TRACK_CACHE[cache_key] = {
                    "track_id": canonical_id,
                    "art_url": art_url,
                    "duration": duration,
                    "album": album
                }

        # Detect ads and interludes
        is_ad = (
            raw_data.get("is_ad", False)
            or title.lower() in ("advertisement", "spotify")
            or (0.0 < duration < 15.0)
            or artist.lower() == "spotify"
        )

        return TrackMetadata(
            status=status,
            track_id=canonical_id,
            artist=artist,
            title=title,
            album=album,
            album_artist=album_artist,
            track_number=track_number,
            disc_number="1",
            duration=duration,
            position=position,
            art_url=art_url,
            is_ad=is_ad
        )
