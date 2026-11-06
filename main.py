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
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Spotifies.audio_sink import AudioSinkManager
from Spotifies.config import CONFIG, RecorderConfig
from Spotifies.library_index import LibraryManager
from Spotifies.player_mpris import SpotifyMPRIS
from Spotifies.recorder import AudioRecorder
from Spotifies.state_machine import RecordingSession
from Spotifies.tagger import MetadataTagger
from Spotifies.verifier import AudioVerifier


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
                print(f"[ERROR] Another daemon is already running (PID: {old_pid}).")
                return False
            except (OSError, ValueError):
                pass  # Stale lock file

        try:
            with open(self.PID_FILE, "w") as f:
                f.write(str(os.getpid()))
            return True
        except Exception as e:
            print(f"[WARN] Failed to write PID lock file: {e}")
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
            print(f"[*] Audio Isolation: ACTIVE using dedicated sink '{self.config.sink_name}'")
            success, msg = self.sink_manager.route_spotify_to_sink()
            if success:
                print(f"[*] {msg}")
        else:
            print(f"[!] Audio Isolation: INACTIVE (capturing default monitor '{source}').")
            print("    Run 'python3 start.py' to isolate Spotify from system and browser alerts.")

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
            print("\n[*] Stopping daemon gracefully...")
            self.running = False
            self.session.finalize_current_take("Daemon terminated")
            self.release_lock()
            sys.exit(0)

        signal.signal(signal.SIGINT, handle_shutdown)
        signal.signal(signal.SIGTERM, handle_shutdown)

        print("=" * 60)
        print("          UNTITLED.ARR - AUTO-RECORDER DAEMON")
        print("=" * 60)
        print(f"[*] Output Directory: {self.config.output_dir}")
        print(f"[*] Audio Codec     : Opus @ {self.config.audio_bitrate} VBR (48 kHz)")
        print(f"[*] Min Listen Req  : {int(self.config.completion_ratio * 100)}% of song duration")
        print(f"[*] Saved Tracks    : {len(self.library.index)} in library")

        self.setup_audio_source()
        print("\n[*] Waiting for Spotify playback...\n")

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

    print("=== untitled.arr Status ===")
    print(f"Destination: {config.output_dir}")
    print(f"Saved Songs: {len(lib.index)}")
    print(f"Isolated Sink ({config.sink_name}): {'Active' if sink_mgr.is_sink_active() else 'Not running'}")

    if meta:
        print(f"Spotify Player : {meta.status}")
        print(f"Current Track  : {meta.title} ({meta.artist})")
        print(f"Album          : {meta.album}")
        print(f"Position       : {int(meta.position)}s / {int(meta.duration)}s")
        print(f"In Library     : {'Yes' if lib.is_recorded(meta.canonical_id) else 'No'}")
    else:
        print("Spotify Player : Not detected")


def cmd_setup_sink() -> None:
    """Creates the isolated null-sink in PipeWire and routes Spotify audio."""
    sink_mgr = AudioSinkManager(CONFIG.sink_name)
    ok, msg = sink_mgr.setup_isolated_sink()
    print(msg)
    if ok:
        _, r_msg = sink_mgr.route_spotify_to_sink()
        print(f"[*] {r_msg}")


def cmd_teardown_sink() -> None:
    """Removes the isolated null-sink and loopback modules."""
    sink_mgr = AudioSinkManager(CONFIG.sink_name)
    _, msg = sink_mgr.teardown_isolated_sink()
    print(msg)


def cmd_route() -> None:
    """Routes active Spotify stream to the isolated sink."""
    sink_mgr = AudioSinkManager(CONFIG.sink_name)
    _, msg = sink_mgr.route_spotify_to_sink()
    print(msg)


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
    hours = total_duration / 3600
    print(f"Verified {total_files} audio files ({mb:.1f} MB, {hours:.2f} hours of music).")
    if missing:
        print(f"[!] {len(missing)} indexed file(s) missing on disk.")


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
