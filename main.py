#!/usr/bin/env python3
"""
CLI entrypoint and daemon supervisor for untitled.arr.
Handles CLI commands, single-instance PID locking, and signal handling.
"""

from __future__ import annotations
import argparse
import os
import re
import signal
import sys
import time
from pathlib import Path

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from artists import ArtistResolver
from audio_sink import AudioSinkManager
from config import CONFIG, RecorderConfig, format_duration, format_human_time
from library_index import LibraryManager
from player_mpris import SpotifyMPRIS
from recorder import AudioRecorder
from remover import SongRemover
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
        self.is_isolated: bool = False
        self.last_reroute_time: float = 0.0
        self.last_track_id: Optional[str] = None

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
        self.is_isolated = is_isolated

        if is_isolated:
            print(f"Audio Isolation : Active ({self.config.sink_name})")
            all_on_sink, moved, msg = self.sink_manager.route_spotify()
            if all_on_sink:
                print(f"Audio Routing   : {msg}")
            else:
                print(f"Audio Routing   : {msg} (will keep retrying)")
        else:
            print(f"Audio Isolation : Inactive (capturing system default '{source}')")
            print("                  Tip: Run 'python3 start.py' to isolate Spotify from desktop alerts.")

        self.session.set_audio_source(source)
        return source

    def maintain_routing(self, meta: Optional[TrackMetadata]) -> None:
        """Periodically checks and maintains Spotify routing to the isolated sink."""
        if not self.is_isolated:
            return

        now = time.time()
        current_track_id = meta.canonical_id if meta else None
        is_track_change = (current_track_id != self.last_track_id)
        reroute_interval = getattr(self.config, "reroute_interval", 3.0)

        if not is_track_change and (now - self.last_reroute_time < reroute_interval):
            return

        self.last_reroute_time = now
        all_on_sink, moved, msg = self.sink_manager.route_spotify()

        if not is_track_change and moved > 0:
            if meta and meta.status == "Playing":
                if self.session.active_meta and (self.session.active_meta.canonical_id == current_track_id):
                    self.session.discard_current_take("audio stream rerouted")

        self.last_track_id = current_track_id

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
            self.session.shutdown()
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

        # Clean any leftover temporary takes from previous abruptly killed sessions
        AudioRecorder.cleanup_orphaned_takes(self.config.output_dir)

        try:
            while self.running:
                meta = SpotifyMPRIS.get_track_data()
                self.maintain_routing(meta)
                self.session.process_tick(meta)
                time.sleep(self.config.poll_interval)
        finally:
            self.session.shutdown()
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
        shown, _ = ArtistResolver(config).enrich(meta)
        print(f"Now Playing    : {shown.title} · {shown.artist}")
        if shown.composers:
            print(f"Composer       : {', '.join(shown.composers)}")
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


def cmd_list(query: str) -> None:
    """Lists saved songs, optionally filtered."""
    rm = SongRemover(CONFIG)
    found = rm.find(query)
    if not found:
        print("No matching songs.")
        return
    for i, c in enumerate(found, 1):
        print(f"{i:3d}. {c.label}")
    print(f"\n{len(found)} song(s).")


def cmd_remove(args) -> int:
    """Safely deletes song(s) from disk (Trash) and unindexes them."""
    rm = SongRemover(CONFIG)
    query = " ".join(args.query)
    found = rm.find(query)
    if not found:
        print(f"No songs match {query!r}." if query else "Library is empty.")
        return 1

    interactive = sys.stdin.isatty()
    if len(found) > 1 and not args.all:
        for i, c in enumerate(found, 1):
            print(f"{i:3d}. {c.label}")
        if not interactive:
            print("\nMultiple matches. Narrow the query, or pass --all.")
            return 2
        pick = input("\nRemove which? (numbers like 1,3 · 'a' = all · Enter = cancel): ").strip().lower()
        if not pick:
            print("Cancelled.")
            return 0
        if pick == "a":
            chosen = found
        else:
            try:
                idx = {int(x) for x in re.split(r"[,\s]+", pick) if x}
                chosen = [found[i - 1] for i in sorted(idx) if 1 <= i <= len(found)]
            except ValueError:
                print("Invalid selection. Nothing removed.")
                return 1
    else:
        chosen = found
    if not chosen:
        print("Nothing selected.")
        return 1

    action = "PERMANENTLY DELETE" if args.permanent else "move to Trash"
    if args.unindex_only:
        action = "unindex only (file kept)"
    print(f"\nAbout to {action}:")
    for c in chosen:
        print(f"  - {c.label}")

    if not args.yes and not args.dry_run:
        if not interactive:
            print("Not a terminal: pass --yes to confirm.")
            return 2
        if args.permanent:
            if input(f"Type DELETE to confirm permanent deletion of {len(chosen)} song(s): ").strip() != "DELETE":
                print("Cancelled.")
                return 0
        elif input(f"Proceed with {len(chosen)} song(s)? [y/N]: ").strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return 0

    failures = 0
    for c in chosen:
        ok, msg = rm.remove(c, permanent=args.permanent, unindex_only=args.unindex_only, dry_run=args.dry_run)
        print(f"{'✓' if ok else '✕'} {c.label}: {msg}")
        failures += 0 if ok else 1
    return 1 if failures else 0


def cmd_refresh_artists(dry_run: bool) -> None:
    """Re-resolves the full artist list for saved songs, then fixes tags, filenames and index."""
    from dataclasses import replace
    from mutagen.oggopus import OggOpus
    from player_mpris import TrackMetadata

    lib = LibraryManager(CONFIG)
    resolver = ArtistResolver(CONFIG)
    sep = CONFIG.artist_separator
    changed = failed = 0

    for tid, e in list(lib.index.items()):
        path = CONFIG.output_dir / (e.get("file") or "")
        if not path.is_file():
            continue
        names = resolver.lookup(tid)
        if not names:
            failed += 1
            print(f"? {e.get('title')}: lookup failed, left as is")
            continue
        base = TrackMetadata("", tid, e.get("artist", ""), e.get("title", ""), e.get("album", ""),
                             e.get("artist", ""), "1", "1", 0.0, 0.0, "", False)
        new, _ = resolver.enrich(base)
        old_artists = e.get("artists") or [e.get("artist", "")]
        if new.artists == old_artists and e.get("composers", []) == new.composers:
            continue

        meta_for_name = replace(base, artist=new.artist)
        dest = lib.generate_destination_path(meta_for_name)
        print(f"{'~' if dry_run else '✓'} {e.get('title')}: {', '.join(old_artists)} -> {new.artist}"
              + (f"  (composer: {', '.join(new.composers)})" if new.composers else "")
              + (f"  [rename -> {dest.name}]" if dest.name != path.name else ""))
        changed += 1
        if dry_run:
            continue
        try:
            audio = OggOpus(path)
            audio["artist"] = list(new.artists)
            if new.composers:
                audio["composer"] = list(new.composers)
            elif "composer" in audio:
                del audio["composer"]
            alb = audio.get("albumartist", [""])[0]
            if new.album_artist != base.album_artist or not alb:
                audio["albumartist"] = [new.album_artist]
            audio.save()
            if dest.name != path.name:
                path.rename(dest)
            e.update({"file": dest.name, "artist": new.artist, "artists": list(new.artists),
                      "composers": list(new.composers)})
            lib.save_index()
        except Exception as ex:
            print(f"  ✕ failed to update {path.name}: {ex}")
    print(f"\n{changed} song(s) {'would change' if dry_run else 'updated'}, {failed} lookup failure(s).")


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
    p_list = subparsers.add_parser("list", help="List saved songs (optionally filtered)")
    p_list.add_argument("query", nargs="*")
    p_rm = subparsers.add_parser("remove", help="Safely delete a song (to Trash) and unindex it")
    p_rm.add_argument("query", nargs="*", help="title/artist words, filename, or Spotify URL/URI")
    p_rm.add_argument("--yes", "-y", action="store_true", help="skip confirmation")
    p_rm.add_argument("--all", action="store_true", help="remove every match")
    p_rm.add_argument("--permanent", action="store_true", help="hard-delete instead of Trash")
    p_rm.add_argument("--unindex-only", action="store_true", help="drop from library.json but keep the file")
    p_rm.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    p_ra = subparsers.add_parser("refresh-artists", help="Re-detect all artists for saved songs and fix tags/filenames")
    p_ra.add_argument("--dry-run", action="store_true")

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
    elif cmd == "list":
        cmd_list(" ".join(args.query))
    elif cmd == "remove":
        sys.exit(cmd_remove(args))
    elif cmd == "refresh-artists":
        cmd_refresh_artists(args.dry_run)


if __name__ == "__main__":
    main()
