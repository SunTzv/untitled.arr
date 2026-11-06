#!/usr/bin/env python3
"""
CLI entrypoint and daemon supervisor for untitled.arr.
Handles CLI commands, single-instance PID locking, and signal handling.
"""

from __future__ import annotations
import argparse
import os
import signal
import sys
import time
from pathlib import Path

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from audio_sink import AudioSinkManager
from config import CONFIG, RecorderConfig, format_duration, format_human_time
from library_index import LibraryManager
from player_mpris import SpotifyMPRIS
from recorder import AudioRecorder
from state_machine import RecordingSession
from tagger import MetadataTagger
from verifier import AudioVerifier


# ==============================================================================
# DAEMON SUPERVISOR
# ==============================================================================

class DaemonSupervisor:
    PID_FILE = Path("/tmp/untitled_arr.pid")

    def __init__(self, config: RecorderConfig = CONFIG):
        self.config = config
        self.sink_manager = AudioSinkManager(config.sink_name)
        self.recorder = AudioRecorder(config)
        self.verifier = AudioVerifier(config)
        self.tagger = MetadataTagger()
        self.library = LibraryManager(config)
        self.session = RecordingSession(
            recorder=self.recorder,
            verifier=self.verifier,
            tagger=self.tagger,
            library=self.library,
            config=self.config
        )
        self.running = False

    # ==========================================================================
    # PID LOCKING
    # ==========================================================================

    def acquire_lock(self) -> bool:
        """Guarantees only one daemon instance runs at a time."""
        if self.PID_FILE.exists():
            try:
                with open(self.PID_FILE, "r") as f:
                    old_pid = int(f.read().strip())
                os.kill(old_pid, 0)
                print(f"Error: Another untitled.arr instance is already running (PID {old_pid}).")
                return False
            except (OSError, ValueError):
                pass  # Stale lock file

        try:
            with open(self.PID_FILE, "w") as f:
                f.write(str(os.getpid()))
            return True
        except Exception as e:
            print(f"Warning: Could not write PID lock file: {e}")
            return True

    def release_lock(self) -> None:
        """Removes the PID file upon termination."""
        if self.PID_FILE.exists():
            try:
                self.PID_FILE.unlink(missing_ok=True)
            except OSError:
                pass

    # ==========================================================================
    # AUDIO SOURCE INITIALIZATION
    # ==========================================================================

    def setup_audio_source(self) -> str:
        """Resolves capture source and routes Spotify audio to dedicated sink if available."""
        source, is_isolated = self.sink_manager.get_recording_source(self.config.prefer_isolated_sink)

        if is_isolated:
            print(f"Audio Isolation : Active ({self.config.sink_name})")
            success, msg = self.sink_manager.route_spotify_to_sink()
            if success:
                print(f"Audio Routing   : {msg}")
        else:
            print(f"Audio Isolation : Inactive (capturing system default '{source}')")
            print("                  Tip: Run 'python3 start.py' to isolate Spotify from desktop alerts.")

        self.session.set_audio_source(source)
        return source

    # ==========================================================================
    # MAIN LOOP
    # ==========================================================================

    def run(self) -> None:
        """Executes the daemon event loop."""
        if not self.acquire_lock():
            sys.exit(1)

        self.running = True

        def handle_shutdown(signum, frame):
            print("\nStopping recorder daemon... Done.")
            self.running = False
            self.session.finalize_current_take("Daemon stopped")
            self.release_lock()
            sys.exit(0)

        signal.signal(signal.SIGINT, handle_shutdown)
        signal.signal(signal.SIGTERM, handle_shutdown)

        saved_count = len(self.library.index)
        song_word = "song" if saved_count == 1 else "songs"

        print("untitled.arr — Spotify Auto-Recorder")
        print("─────────────────────────────────────────────")
        print(f"Output Folder   : {self.config.output_dir}")
        print(f"Saved Music     : {saved_count} {song_word} in library")
        print(f"Audio Format    : Opus {self.config.audio_bitrate} (48 kHz)")
        print(f"Listen Required : {int(self.config.completion_ratio * 100)}% of track duration")

        self.setup_audio_source()
        print("─────────────────────────────────────────────")
        print("Listening for Spotify playback... (Ctrl+C to stop)\n")

        try:
            while self.running:
                meta = SpotifyMPRIS.get_track_data()
                self.session.process_tick(meta)
                time.sleep(self.config.poll_interval)
        finally:
            self.release_lock()


# ==============================================================================
# CLI COMMAND HANDLERS
# ==============================================================================

def cmd_status() -> None:
    """Displays player status, active audio routing, and library count."""
    config = CONFIG
    sink_mgr = AudioSinkManager(config.sink_name)
    lib = LibraryManager(config)
    meta = SpotifyMPRIS.get_track_data()

    is_sink_active = sink_mgr.is_sink_active()
    sink_status = f"Active ({config.sink_name})" if is_sink_active else "Inactive (system default)"

    saved_count = len(lib.index)
    song_word = "song" if saved_count == 1 else "songs"

    print("untitled.arr Status")
    print("─────────────────────────────────────────────")
    print(f"Destination    : {config.output_dir}")
    print(f"Library        : {saved_count} {song_word} saved")
    print(f"Audio Sink     : {sink_status}")
    print()

    if meta:
        status_icon = "▶" if meta.status == "Playing" else ("⏸" if meta.status == "Paused" else "⏹")
        pct = int(round((meta.position / meta.duration * 100))) if meta.duration > 0 else 0
        in_lib = "Yes (already recorded)" if lib.is_recorded(meta.canonical_id) else "No (will record on full listen)"
        print(f"Spotify Player : {status_icon} {meta.status}")
        print(f"Now Playing    : {meta.title} · {meta.artist}")
        if meta.album and meta.album != "Unknown Album":
            print(f"Album          : {meta.album}")
        print(f"Progress       : {format_duration(meta.position)} / {format_duration(meta.duration)} ({pct}%)")
        print(f"Saved in Music : {in_lib}")
    else:
        print("Spotify Player : Not detected or Spotify is closed")


def cmd_setup_sink() -> None:
    """Creates the isolated null-sink in PipeWire and routes Spotify audio."""
    sink_mgr = AudioSinkManager(CONFIG.sink_name)
    ok, msg = sink_mgr.setup_isolated_sink()
    prefix = "✓" if ok else "Error:"
    print(f"{prefix} {msg}")
    if ok:
        _, r_msg = sink_mgr.route_spotify_to_sink()
        print(f"  {r_msg}")


def cmd_teardown_sink() -> None:
    """Removes the isolated null-sink and loopback modules."""
    sink_mgr = AudioSinkManager(CONFIG.sink_name)
    ok, msg = sink_mgr.teardown_isolated_sink()
    prefix = "✓" if ok else "Error:"
    print(f"{prefix} {msg}")


def cmd_route() -> None:
    """Routes active Spotify stream to the isolated sink."""
    sink_mgr = AudioSinkManager(CONFIG.sink_name)
    ok, msg = sink_mgr.route_spotify_to_sink()
    prefix = "✓" if ok else "Info:"
    print(f"{prefix} {msg}")


def cmd_verify_library() -> None:
    """Audits saved files on disk against the library index."""
    config = CONFIG
    lib = LibraryManager(config)
    verifier = AudioVerifier(config)

    print(f"Auditing library at {config.output_dir}...")
    total_files = 0
    total_bytes = 0
    total_duration = 0.0
    missing = []

    for track_id, info in list(lib.index.items()):
        file_path = config.output_dir / info.get("file", "")
        if not file_path.exists():
            missing.append(track_id)
        else:
            total_files += 1
            total_bytes += file_path.stat().st_size
            total_duration += verifier.get_file_duration(file_path)

    mb = total_bytes / (1024 * 1024)
    human_time = format_human_time(total_duration)
    song_word = "song" if total_files == 1 else "songs"
    print(f"✓ Verified {total_files} {song_word} ({mb:.1f} MB, {human_time} of music).")
    if missing:
        print(f"⚠ Warning: {len(missing)} indexed song(s) missing from disk.")


# ==============================================================================
# CLI ENTRYPOINT
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="untitled.arr: Automated Spotify Audio Recorder.")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("run", help="Start the recorder daemon (default)")
    subparsers.add_parser("status", help="Show current Spotify playback and sink status")
    subparsers.add_parser("setup-sink", help="Create isolated PipeWire audio sink")
    subparsers.add_parser("teardown-sink", help="Remove isolated audio sink")
    subparsers.add_parser("route", help="Route Spotify audio to isolated sink")
    subparsers.add_parser("verify-library", help="Audit existing recordings and print statistics")

    args = parser.parse_args()
    cmd = args.command or "run"

    if cmd == "run":
        DaemonSupervisor().run()
    elif cmd == "status":
        cmd_status()
    elif cmd == "setup-sink":
        cmd_setup_sink()
    elif cmd == "teardown-sink":
        cmd_teardown_sink()
    elif cmd == "route":
        cmd_route()
    elif cmd == "verify-library":
        cmd_verify_library()


if __name__ == "__main__":
    main()
