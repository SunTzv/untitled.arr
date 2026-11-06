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
    print("=" * 60)
    print("          UNTITLED.ARR - AUTO STARTUP")
    print("=" * 60)

    # 1. Setup isolated audio sink
    sink_mgr = AudioSinkManager(CONFIG.sink_name)
    if not sink_mgr.is_sink_active():
        print("[*] Setting up isolated audio sink...")
        _, msg = sink_mgr.setup_isolated_sink()
        print(f"[*] {msg}")
    else:
        print(f"[*] Dedicated sink '{CONFIG.sink_name}' is already active.")

    # 2. Redirect active Spotify audio stream
    print("[*] Checking Spotify stream routing...")
    _, r_msg = sink_mgr.route_spotify_to_sink()
    print(f"[*] {r_msg}")

    # 3. Launch recording daemon
    print(f"[*] Destination directory: {CONFIG.output_dir}/")
    print("[*] Starting recorder... (Press Ctrl+C to stop)\n")

    daemon = DaemonSupervisor()
    daemon.run()


if __name__ == "__main__":
    main()
