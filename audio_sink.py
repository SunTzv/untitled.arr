"""
Audio sink management module for untitled.arr.
Handles virtual null-sink creation, loopback audio routing, and stream redirection.
"""

from __future__ import annotations
import os
import re
import subprocess
from typing import Optional, Tuple


# ==============================================================================
# AUDIO SINK MANAGER
# ==============================================================================

class AudioSinkManager:
    def __init__(self, sink_name: str = "SpotifySink"):
        self.sink_name = sink_name
        self.monitor_source = f"{sink_name}.monitor"

    # ==========================================================================
    # SINK LIFECYCLE MANAGEMENT
    # ==========================================================================

    def get_sink_index(self) -> Optional[str]:
        """Returns the numerical sink index of the dedicated sink if active."""
        env = {**os.environ, "LC_ALL": "C"}
        try:
            output = subprocess.check_output(
                ["pactl", "list", "short", "sinks"],
                text=True,
                stderr=subprocess.DEVNULL,
                env=env
            )
            for line in output.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1] == self.sink_name:
                    return parts[0]
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
        return None

    def is_sink_active(self) -> bool:
        """Checks if the dedicated sink currently exists in Pulse/PipeWire."""
        return self.get_sink_index() is not None

    def setup_isolated_sink(self) -> Tuple[bool, str]:
        """Creates the virtual null-sink and loopback to maintain audible playback."""
        if self.is_sink_active():
            return True, f"Dedicated audio sink '{self.sink_name}' is already active."

        env = {**os.environ, "LC_ALL": "C"}
        try:
            # Create isolated null-sink
            subprocess.check_call(
                [
                    "pactl", "load-module", "module-null-sink",
                    f"sink_name={self.sink_name}",
                    f"sink_properties=device.description={self.sink_name}"
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env
            )

            # Create loopback so audio remains audible through speakers/headphones
            subprocess.check_call(
                [
                    "pactl", "load-module", "module-loopback",
                    f"source={self.monitor_source}"
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env
            )
            return True, f"Created isolated audio sink '{self.sink_name}'."
        except subprocess.CalledProcessError as e:
            return False, f"Failed to set up isolated audio sink: {e}"

    def teardown_isolated_sink(self) -> Tuple[bool, str]:
        """Unloads modules associated with the dedicated sink."""
        unloaded = 0
        env = {**os.environ, "LC_ALL": "C"}
        try:
            output = subprocess.check_output(
                ["pactl", "list", "short", "modules"],
                text=True,
                stderr=subprocess.DEVNULL,
                env=env
            )
            for line in output.splitlines():
                parts = line.split()
                if len(parts) >= 3 and self.sink_name in line:
                    mod_id = parts[0]
                    subprocess.call(["pactl", "unload-module", mod_id], stderr=subprocess.DEVNULL, env=env)
                    unloaded += 1
            if unloaded > 0:
                return True, f"Removed isolated sink '{self.sink_name}' and restored normal routing."
            return True, "No isolated audio sink found to remove."
        except Exception as e:
            return False, f"Failed to tear down audio sink: {e}"

    # ==========================================================================
    # SOURCE DETECTION & QUERY
    # ==========================================================================

    def get_default_monitor_source(self) -> str:
        """Returns the monitor source of the current system default audio sink."""
        env = {**os.environ, "LC_ALL": "C"}
        try:
            sink = subprocess.check_output(
                ["pactl", "get-default-sink"],
                text=True,
                stderr=subprocess.DEVNULL,
                env=env
            ).strip()
            if sink:
                return f"{sink}.monitor"
        except Exception:
            pass
        return "default"

    def get_recording_source(self, prefer_isolated: bool = True) -> Tuple[str, bool]:
        """
        Resolves the audio capture monitor source.
        Returns: (source_name, is_isolated)
        """
        if prefer_isolated and self.is_sink_active():
            return self.monitor_source, True

        return self.get_default_monitor_source(), False

    # ==========================================================================
    # STREAM REDIRECTION
    # ==========================================================================

    def route_spotify(self) -> Tuple[bool, int, str]:
        """
        Redirects Spotify playback streams to the dedicated sink if not already on it.
        Returns: (all_on_sink, moved_count, message)
        """
        sink_index = self.get_sink_index()
        if not sink_index:
            return False, 0, f"Isolated sink '{self.sink_name}' is not active."

        env = {**os.environ, "LC_ALL": "C"}
        try:
            output = subprocess.check_output(
                ["pactl", "list", "sink-inputs"],
                text=True,
                stderr=subprocess.DEVNULL,
                env=env
            )
        except Exception as e:
            return False, 0, f"Could not list audio streams: {e}"

        blocks = re.split(r"(?m)^Sink Input #", output)
        spotify_streams = 0
        moved_count = 0
        move_failures = 0

        for block in blocks:
            if not block.strip():
                continue

            match_id = re.match(r"^(\d+)", block)
            if not match_id:
                continue
            input_id = match_id.group(1)

            # Match application.name or application.process.binary case-insensitively
            app_name_m = re.search(r'application\.name\s*=\s*"([^"]+)"', block, re.IGNORECASE)
            app_bin_m = re.search(r'application\.process\.binary\s*=\s*"([^"]+)"', block, re.IGNORECASE)

            if not app_name_m:
                app_name_m = re.search(r'application\.name\s*=\s*(\S+)', block, re.IGNORECASE)
            if not app_bin_m:
                app_bin_m = re.search(r'application\.process\.binary\s*=\s*(\S+)', block, re.IGNORECASE)

            is_spotify = False
            if app_name_m and app_name_m.group(1).strip().lower() == "spotify":
                is_spotify = True
            elif app_bin_m and app_bin_m.group(1).strip().lower() == "spotify":
                is_spotify = True

            if not is_spotify:
                continue

            spotify_streams += 1

            # Extract current sink
            sink_m = re.search(r"^\s*Sink:\s*(\S+)", block, re.MULTILINE)
            current_sink = sink_m.group(1) if sink_m else ""

            if current_sink != sink_index and current_sink != self.sink_name:
                try:
                    subprocess.check_call(
                        ["pactl", "move-sink-input", input_id, self.sink_name],
                        stderr=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        env=env
                    )
                    moved_count += 1
                except subprocess.CalledProcessError:
                    move_failures += 1

        if spotify_streams == 0:
            return False, 0, "No active Spotify playback found to route (play a track first)."

        all_on_sink = (move_failures == 0)
        if moved_count > 0:
            return all_on_sink, moved_count, f"Routed Spotify playback stream to '{self.sink_name}'."
        else:
            return all_on_sink, 0, f"Spotify playback stream is already routed to '{self.sink_name}'."

    def route_spotify_to_sink(self) -> Tuple[bool, str]:
        """Backward-compatible wrapper around route_spotify."""
        all_on_sink, _, msg = self.route_spotify()
        return all_on_sink, msg
