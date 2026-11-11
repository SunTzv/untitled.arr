"""
Playback state machine and recording supervisor for untitled.arr.
Tracks transitions, validates completion, detects forward/backward seeks, and handles pauses.
"""

from __future__ import annotations
import concurrent.futures
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Set
from config import CONFIG, RecorderConfig, format_duration
from library_index import LibraryManager
from player_mpris import SpotifyMPRIS, TrackMetadata
from recorder import AudioRecorder
from tagger import MetadataTagger
from verifier import AudioVerifier
from artists import ArtistResolver


# ==============================================================================
# ENGINE STATE
# ==============================================================================

class EngineState(Enum):
    IDLE = "IDLE"
    RECORDING = "RECORDING"


# ==============================================================================
# RECORDING SESSION SUPERVISOR
# ==============================================================================

class RecordingSession:
    def __init__(
        self,
        recorder: AudioRecorder,
        verifier: AudioVerifier,
        tagger: MetadataTagger,
        library: LibraryManager,
        config: RecorderConfig = CONFIG,
        on_status_change: Optional[Callable[[str], None]] = None
    ):
        self.recorder = recorder
        self.verifier = verifier
        self.tagger = tagger
        self.library = library
        self.config = config
        self.log_cb = on_status_change or (lambda msg: print(msg))

        self.state = EngineState.IDLE
        self.active_meta: Optional[TrackMetadata] = None
        self.last_pos: float = 0.0
        self.last_wall_time: float = 0.0
        self.invalid_reason: str = ""
        self.audio_source: str = "default"

        # Background finalization worker pool to prevent blocking the recording loop
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="take_finalizer"
        )
        self.artist_resolver = ArtistResolver(config)
        self._in_flight_lock = threading.Lock()
        self._in_flight: Set[str] = set()

    @property
    def is_invalid(self) -> bool:
        return bool(self.invalid_reason)

    @is_invalid.setter
    def is_invalid(self, val: bool) -> None:
        if val:
            if not self.invalid_reason:
                self.invalid_reason = "seek/scrub detected"
        else:
            self.invalid_reason = ""

    def set_audio_source(self, source: str) -> None:
        self.audio_source = source

    def log(self, message: str) -> None:
        self.log_cb(message)

    def is_recorded(self, track_id: str) -> bool:
        """Checks if track is either already in the library or actively being committed."""
        with self._in_flight_lock:
            if track_id in self._in_flight:
                return True
        return self.library.is_recorded(track_id)

    def shutdown(self, wait: bool = True) -> None:
        """Waits for any running background take finalizations to complete before exit."""
        self.executor.shutdown(wait=wait, cancel_futures=False)

    # ==========================================================================
    # TAKE LIFECYCLE (START / FINALIZE / DISCARD)
    # ==========================================================================

    def start_take(self, meta: TrackMetadata) -> None:
        """Initiates recording of a new track starting near position 0."""
        self.log(f"● Recording: {meta.title} · {meta.artist} [{format_duration(meta.duration)}]")
        max_seconds = None
        if getattr(self.config, "cap_recording_to_track", True):
            remaining = meta.duration - meta.position
            if remaining > 0:
                max_seconds = remaining + 5.0  # Safe ceiling to prevent clipping outro/fadeout

        temp_path = self.recorder.get_new_temp_file()
        t_start = time.time()
        started = self.recorder.start_recording(self.audio_source, temp_path=temp_path, max_seconds=max_seconds)
        if not started:
            self.log(f"Error: Failed to start audio recording for '{meta.title}'.")
            self.state = EngineState.IDLE
            self.active_meta = None
            return

        self.state = EngineState.RECORDING
        self.active_meta = meta
        self.last_pos = meta.position
        self.last_wall_time = t_start
        self.invalid_reason = ""

    def discard_current_take(self, reason: str = "") -> None:
        """Immediately aborts the active take, cleans up temp files, and logs."""
        if not self.active_meta:
            self.state = EngineState.IDLE
            return

        meta = self.active_meta
        self.active_meta = None
        self.state = EngineState.IDLE
        self.invalid_reason = ""
        self.recorder.discard_take()
        msg = f"✕ Discarded: {meta.title} · {meta.artist}"
        if reason:
            msg += f" ({reason})"
        self.log(msg)

    def finalize_current_take(self, reason: str = "") -> bool:
        """
        Finalizes active take: immediately stops FFmpeg to release capture (<0.1s),
        and submits verification, tagging, and library commit to the background executor
        so the next track can begin recording without missing any audio.
        """
        if not self.active_meta or not self.recorder.active_temp_path:
            self.state = EngineState.IDLE
            self.active_meta = None
            return False

        meta = self.active_meta
        temp_file = self.recorder.active_temp_path
        invalid_reason = self.invalid_reason

        self.active_meta = None
        self.state = EngineState.IDLE
        self.invalid_reason = ""

        # Detach and stop recorder immediately (~0.05s)
        self.recorder.active_temp_path = None
        ret_code, err_msg = self.recorder.stop_recording()

        # If take was already flagged invalid (e.g. paused or seeked), discard immediately
        if invalid_reason:
            self.log(f"✕ Discarded: {meta.title} · {meta.artist} ({invalid_reason})")
            AudioRecorder.cleanup_file(temp_file)
            return False

        # Mark track as in-flight so duplicate takes aren't started while saving
        with self._in_flight_lock:
            self._in_flight.add(meta.canonical_id)

        # Submit post-processing to background thread
        self.executor.submit(self._finalize_worker, temp_file, meta)
        return True

    def _finalize_worker(self, temp_file: Path, meta: TrackMetadata) -> None:
        """Background worker: verifies duration & audio content, tags metadata/artwork, and commits to library."""
        try:
            # Verify integrity and duration threshold
            is_valid, recorded_len, v_reason = self.verifier.verify_recording(temp_file, meta.duration)
            if not is_valid:
                if recorded_len >= 5.0:
                    self.log(f"✕ Discarded: {meta.title} · {meta.artist} ({v_reason})")
                else:
                    self.log(f"Skipped: {meta.title} · {meta.artist}")
                AudioRecorder.cleanup_file(temp_file)
                return

            # Resolve the full artist list (network lookup happens here, off the recording loop)
            meta, resolved = self.artist_resolver.enrich(meta)
            if not resolved and self.config.resolve_all_artists:
                self.log(f"  ! Could not fetch full artist list for '{meta.title}'; using '{meta.artist}'. "
                         f"Fix later with: python3 main.py refresh-artists")

            # Tag with metadata and album artwork
            tagged = self.tagger.tag_file(temp_file, meta, embed_art=self.config.embed_artwork)
            if not tagged:
                self.log(f"Warning: Failed to embed metadata tags for '{meta.title}'.")

            # Commit to library
            dest_path = self.library.commit_recording(temp_file, meta, recorded_len)
            if dest_path:
                self.log(
                    f"✓ Saved: {meta.title} · {meta.artist} "
                    f"[{format_duration(recorded_len)} / {format_duration(meta.duration)}] -> {dest_path.name}"
                )
            else:
                self.log(f"Error: Failed to commit '{meta.title}' to library.")
                AudioRecorder.cleanup_file(temp_file)
        except Exception as e:
            self.log(f"Error finalizing take for '{meta.title}': {e}")
            AudioRecorder.cleanup_file(temp_file)
        finally:
            with self._in_flight_lock:
                self._in_flight.discard(meta.canonical_id)

    # ==========================================================================
    # PERIODIC TICK EVALUATION
    # ==========================================================================

    def process_tick(self, meta: Optional[TrackMetadata]) -> None:
        """Evaluates player state transitions and updates recording lifecycle."""
        now = time.time()

        # Player disconnected or closed
        if not meta:
            if self.state == EngineState.RECORDING:
                self.finalize_current_take("Player disconnected")
            return

        # Skip ads and non-music interludes
        if meta.is_ad:
            if self.state == EngineState.RECORDING:
                self.finalize_current_take("Advertisement detected")
            return

        # Repeat-one loop detection (position rewound while track remains identical)
        is_same_track = self.active_meta and (meta.canonical_id == self.active_meta.canonical_id)
        if is_same_track and self.state == EngineState.RECORDING:
            if self.last_pos > 15.0 and meta.position <= self.config.max_start_offset:
                self.log(f"Track repeated — saving previous take of '{meta.title}'.")
                self.finalize_current_take("Repeat loop")

        # Track transition
        is_new_track = (not self.active_meta) or (meta.canonical_id != self.active_meta.canonical_id)
        if is_new_track:
            if self.state == EngineState.RECORDING:
                self.finalize_current_take("Track transition")

            # Check if track is already in library or currently in-flight
            if self.is_recorded(meta.canonical_id):
                self.state = EngineState.IDLE
                self.active_meta = None
                return

            # Refresh metadata right before evaluating start to avoid any stale position
            fresh_meta = SpotifyMPRIS.get_track_data()
            if fresh_meta and fresh_meta.canonical_id == meta.canonical_id:
                meta = fresh_meta

            # Start recording if track is near start
            if meta.status == "Playing" and meta.position <= self.config.max_start_offset:
                self.start_take(meta)
            else:
                self.state = EngineState.IDLE
                self.active_meta = None
            return

        # Non-Playing status (Paused, Stopped) while in RECORDING
        if meta.status != "Playing":
            if self.state == EngineState.RECORDING:
                # EXCEPTION: if max(meta.position, last_pos) >= duration * completion_ratio
                # (e.g. the last song in the queue ended and Spotify stopped, with position
                # possibly reset to 0), finalize normally and keep it.
                if max(meta.position, self.last_pos) >= (meta.duration * self.config.completion_ratio):
                    self.finalize_current_take("Finished playback")
                else:
                    self.discard_current_take("paused")
            return

        # Active recording health & seek detection
        if self.state == EngineState.RECORDING:
            if not self.recorder.is_recording():
                if not self.recorder.finished_cleanly():
                    self.log(f"Error: Audio recording process stopped unexpectedly for '{meta.title}'.")
                    self.finalize_current_take("Recorder crashed")
                    return

            wall_delta = max(0.001, now - self.last_wall_time)
            pos_delta = meta.position - self.last_pos

            # 1. Forward skip: position jumps ahead significantly faster than elapsed wall clock time.
            is_forward_skip = (pos_delta - wall_delta) > self.config.max_seek_discrepancy

            # 2. Backward scrub: position jumps backward.
            # Normal playback strictly progresses forward; margin of -1.0s safely accounts for any MPRIS clock jitter.
            is_backward_scrub = pos_delta < -1.0

            if is_forward_skip or is_backward_scrub:
                reason = "forward seek detected" if is_forward_skip else "backward scrub detected"
                self.invalid_reason = reason
                self.discard_current_take(reason)
                return

            self.last_pos = meta.position
            self.last_wall_time = now
