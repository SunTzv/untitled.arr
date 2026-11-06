"""
Audio sink management module for untitled.arr.
Handles virtual null-sink creation, loopback audio routing, and stream redirection.
"""

from __future__ import annotations
import re
import subprocess
from typing import Tuple


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

    def is_sink_active(self) -> bool:
        """Checks if the dedicated sink currently exists in Pulse/PipeWire."""
        try:
            output = subprocess.check_output(
                ["pactl", "list", "short", "sinks"],
                text=True,
                stderr=subprocess.DEVNULL
            )
            for line in output.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1] == self.sink_name:
                    return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
        return False

    def setup_isolated_sink(self) -> Tuple[bool, str]:
        """Creates the virtual null-sink and loopback to maintain audible playback."""
        if self.is_sink_active():
            return True, f"Dedicated audio sink '{self.sink_name}' is already active."

        try:
            # Create isolated null-sink
            subprocess.check_call(
                [
                    "pactl", "load-module", "module-null-sink",
                    f"sink_name={self.sink_name}",
                    f"sink_properties=device.description={self.sink_name}"
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            # Create loopback so audio remains audible through speakers/headphones
            subprocess.check_call(
                [
                    "pactl", "load-module", "module-loopback",
                    f"source={self.monitor_source}"
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return True, f"Created isolated audio sink '{self.sink_name}'."
        except subprocess.CalledProcessError as e:
            return False, f"Failed to set up isolated audio sink: {e}"

    def teardown_isolated_sink(self) -> Tuple[bool, str]:
        """Unloads modules associated with the dedicated sink."""
        unloaded = 0
        try:
            output = subprocess.check_output(
                ["pactl", "list", "short", "modules"],
                text=True,
                stderr=subprocess.DEVNULL
            )
            for line in output.splitlines():
                parts = line.split()
                if len(parts) >= 3 and self.sink_name in line:
                    mod_id = parts[0]
                    subprocess.call(["pactl", "unload-module", mod_id], stderr=subprocess.DEVNULL)
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
        try:
            sink = subprocess.check_output(
                ["pactl", "get-default-sink"],
                text=True,
                stderr=subprocess.DEVNULL
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

    def route_spotify_to_sink(self) -> Tuple[bool, str]:
        """Redirects active Spotify playback streams to the dedicated sink."""
        if not self.is_sink_active():
            return False, f"Isolated sink '{self.sink_name}' is not active."

        try:
            output = subprocess.check_output(
                ["pactl", "list", "sink-inputs"],
                text=True,
                stderr=subprocess.DEVNULL
            )
            current_input_id = None
            is_spotify = False
            moved_count = 0

            for line in output.splitlines():
                match_input = re.match(r"^Sink Input #(\d+)", line)
                if match_input:
                    current_input_id = match_input.group(1)
                    is_spotify = False
                    continue

                if "application.name = \"spotify\"" in line.lower():
                    is_spotify = True

                if is_spotify and current_input_id:
                    try:
                        subprocess.check_call(
                            ["pactl", "move-sink-input", current_input_id, self.sink_name],
                            stderr=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL
                        )
                        moved_count += 1
                    except subprocess.CalledProcessError:
                        pass
                    current_input_id = None
                    is_spotify = False

            if moved_count > 0:
                return True, f"Routed Spotify playback stream to '{self.sink_name}'."
            return False, "No active Spotify playback found to route (play a track first)."
        except Exception as e:
            return False, f"Could not route Spotify audio: {e}"
