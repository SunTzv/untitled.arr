#!/usr/bin/env python3
"""
Single-command launcher for untitled.arr.
Automatically configures the audio sink, routes Spotify, and starts the daemon.
"""

from __future__ import annotations
import sys
from pathlib import Path

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from audio_sink import AudioSinkManager
from config import CONFIG
from main import DaemonSupervisor


# ==============================================================================
# MAIN LAUNCHER
# ==============================================================================

def main():
    # 1. Setup isolated audio sink
    sink_mgr = AudioSinkManager(CONFIG.sink_name)
    if not sink_mgr.is_sink_active():
        _, msg = sink_mgr.setup_isolated_sink()
        print(f"✓ {msg}")

    # 2. Redirect active Spotify audio stream
    sink_mgr.route_spotify_to_sink()

    # 3. Launch recording daemon
    daemon = DaemonSupervisor()
    daemon.run()


if __name__ == "__main__":
    main()
