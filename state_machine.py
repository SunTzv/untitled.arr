"""
Playback state machine and recording supervisor for untitled.arr.
Tracks transitions, validates completion, detects forward/backward seeks, and handles pauses.
"""

from __future__ import annotations
import time
from enum import Enum
from typing import Callable, Optional
from config import CONFIG, RecorderConfig, format_duration
from library_index import LibraryManager
from player_mpris import TrackMetadata
from recorder import AudioRecorder
from tagger import MetadataTagger
from verifier import AudioVerifier


# ==============================================================================
# ENGINE STATE
# ==============================================================================

class EngineState(Enum):
    IDLE = "IDLE"
    RECORDING = "RECORDING"
    PAUSED = "PAUSED"


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
        self.pause_start_time: float = 0.0
        self.is_invalid: bool = False
        self.audio_source: str = "default"

    def set_audio_source(self, source: str) -> None:
        self.audio_source = source

    def log(self, message: str) -> None:
        self.log_cb(message)

    # ==========================================================================
    # TAKE LIFECYCLE (START / FINALIZE)
    # ==========================================================================

    def start_take(self, meta: TrackMetadata) -> None:
        """Initiates recording of a new track starting near position 0."""
        self.log(f"● Recording: {meta.title} · {meta.artist} [{format_duration(meta.duration)}]")
        started = self.recorder.start_recording(self.audio_source)
        if not started:
            self.log(f"Error: Failed to start audio recording for '{meta.title}'.")
            self.state = EngineState.IDLE
            self.active_meta = None
            return

        self.state = EngineState.RECORDING
        self.active_meta = meta
        self.last_pos = meta.position
        self.last_wall_time = time.time()
        self.is_invalid = False

    def finalize_current_take(self, reason: str = "") -> bool:
        """
        Finalizes active take: stops FFmpeg, verifies duration with ffprobe,
        embeds tags and artwork, and commits to library if valid.
        """
        if not self.active_meta or not self.recorder.active_temp_path:
            self.state = EngineState.IDLE
            self.active_meta = None
            return False

        meta = self.active_meta
        temp_file = self.recorder.active_temp_path
        self.active_meta = None
        self.state = EngineState.IDLE

        ret_code, err_msg = self.recorder.stop_recording()

        if self.is_invalid:
            self.log(f"✕ Discarded: {meta.title} · {meta.artist} (seek/scrub detected)")
            self.recorder.discard_take()
            return False

        # Verify integrity and duration threshold
        is_valid, recorded_len, v_reason = self.verifier.verify_recording(temp_file, meta.duration)
        if not is_valid:
            if recorded_len >= 5.0:
                self.log(f"✕ Discarded: {meta.title} · {meta.artist} ({v_reason})")
            else:
                self.log(f"Skipped: {meta.title} · {meta.artist}")
            self.recorder.discard_take()
            return False

        # Tag with metadata and album artwork
        self.tagger.tag_file(temp_file, meta, embed_art=self.config.embed_artwork)

        # Commit to library
        dest_path = self.library.commit_recording(temp_file, meta, recorded_len)
        if dest_path:
            self.log(f"✓ Saved: {meta.title} · {meta.artist} [{format_duration(recorded_len)} / {format_duration(meta.duration)}] -> {dest_path.name}")
            return True
        else:
            self.log(f"Error: Failed to commit '{meta.title}' to library.")
            self.recorder.discard_take()
            return False

    # ==========================================================================
    # PERIODIC TICK EVALUATION
    # ==========================================================================

    def process_tick(self, meta: Optional[TrackMetadata]) -> None:
        """Evaluates player state transitions and updates recording lifecycle."""
        now = time.time()

        # Player disconnected or closed
        if not meta:
            if self.state in (EngineState.RECORDING, EngineState.PAUSED):
                self.finalize_current_take("Player disconnected")
            return

        # Skip ads and non-music interludes
        if meta.is_ad:
            if self.state in (EngineState.RECORDING, EngineState.PAUSED):
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
            if self.state in (EngineState.RECORDING, EngineState.PAUSED):
                self.finalize_current_take("Track transition")

            # Check if track is already in library
            if self.library.is_recorded(meta.canonical_id):
                self.state = EngineState.IDLE
                self.active_meta = None
                return

            # Start recording if track is near start
            if meta.status == "Playing" and meta.position <= self.config.max_start_offset:
                self.start_take(meta)
            else:
                self.state = EngineState.IDLE
                self.active_meta = None
            return

        # Paused state handling
        if meta.status != "Playing":
            if self.state == EngineState.RECORDING:
                self.state = EngineState.PAUSED
                self.pause_start_time = now
                self.log(f"⏸ Paused: {meta.title} · {meta.artist} [{format_duration(meta.position)}]")

            elif self.state == EngineState.PAUSED:
                pause_duration = now - self.pause_start_time
                if pause_duration > self.config.max_pause_seconds:
                    if meta.position >= (meta.duration * self.config.completion_ratio):
                        self.finalize_current_take("Paused near completion")
                    else:
                        self.finalize_current_take("Pause timed out")
            return

        # Playback resumed from pause
        if self.state == EngineState.PAUSED:
            self.state = EngineState.RECORDING
            self.last_wall_time = now
            self.last_pos = meta.position
            self.log(f"▶ Resumed: {meta.title} · {meta.artist} [{format_duration(meta.position)}]")
            return

        # Active recording health & seek detection
        if self.state == EngineState.RECORDING:
            if not self.recorder.is_recording():
                self.log(f"Error: Audio recording process stopped unexpectedly for '{meta.title}'.")
                self.finalize_current_take("Recorder crashed")
                return

            wall_delta = now - self.last_wall_time
            pos_delta = meta.position - self.last_pos

            # Invalidate take on forward skips or backward scrubs
            if abs(pos_delta - wall_delta) > self.config.max_seek_discrepancy:
                if not self.is_invalid:
                    self.log(f"Seek detected in '{meta.title}' — take invalidated.")
                    self.is_invalid = True

            self.last_pos = meta.position
            self.last_wall_time = now
