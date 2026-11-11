"""
Unit tests for untitled.arr cross-platform and Windows support.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import RecorderConfig, load_config
from library_index import LibraryManager
from player_mpris import TrackMetadata, SpotifyMPRIS
from player import SpotifyPlayer
from recorder import AudioRecorder
from audio_sink import AudioSinkManager
from remover import SongRemover, Candidate


class TestUniversalSanitization(unittest.TestCase):
    def test_windows_reserved_names_sanitization(self):
        # Test standard filenames
        self.assertEqual(LibraryManager.sanitize_filename("Tum Hi Ho"), "Tum Hi Ho")
        self.assertEqual(LibraryManager.sanitize_filename("Hello/World:Test?"), "HelloWorldTest")
        
        # Test Windows reserved names
        self.assertEqual(LibraryManager.sanitize_filename("CON"), "_CON")
        self.assertEqual(LibraryManager.sanitize_filename("prn"), "_prn")
        self.assertEqual(LibraryManager.sanitize_filename("aux"), "_aux")
        self.assertEqual(LibraryManager.sanitize_filename("NUL"), "_NUL")
        self.assertEqual(LibraryManager.sanitize_filename("com1"), "_com1")
        self.assertEqual(LibraryManager.sanitize_filename("LPT3"), "_LPT3")
        self.assertEqual(LibraryManager.sanitize_filename("CON.txt"), "_CON.txt")
        self.assertEqual(LibraryManager.sanitize_filename("   "), "Unknown")
        self.assertEqual(LibraryManager.sanitize_filename("..."), "Unknown")


class TestRecorderArguments(unittest.TestCase):
    def setUp(self):
        self.config = RecorderConfig()
        self.recorder = AudioRecorder(self.config)

    def test_linux_input_args(self):
        with patch("sys.platform", "linux"):
            args = self.recorder._input_args("SpotifySink.monitor")
            self.assertEqual(args, ["-f", "pulse", "-i", "SpotifySink.monitor"])

    def test_windows_input_args(self):
        with patch("sys.platform", "win32"):
            # DirectShow device name
            args = self.recorder._input_args("CABLE Output (VB-Audio Virtual Cable)")
            self.assertEqual(args, ["-f", "dshow", "-i", "audio=CABLE Output (VB-Audio Virtual Cable)"])

            # Already prefixed
            args2 = self.recorder._input_args("audio=Stereo Mix")
            self.assertEqual(args2, ["-f", "dshow", "-i", "audio=Stereo Mix"])

            # Pipe mode
            args3 = self.recorder._input_args("pipe")
            self.assertEqual(args3, ["-f", "s16le", "-ar", "48000", "-ac", "2", "-i", "pipe:0"])

    def test_macos_input_args(self):
        with patch("sys.platform", "darwin"):
            args = self.recorder._input_args(":0")
            self.assertEqual(args, ["-f", "avfoundation", "-i", ":0"])
            args2 = self.recorder._input_args("0")
            self.assertEqual(args2, ["-f", "avfoundation", "-i", ":0"])


class TestAudioSinkManagerUniversal(unittest.TestCase):
    def test_windows_sink_detection(self):
        sink_mgr = AudioSinkManager("SpotifySink")
        with patch("sys.platform", "win32"):
            with patch.object(sink_mgr, "get_windows_audio_devices", return_value=["CABLE Output (VB-Audio)", "Microphone"]):
                self.assertTrue(sink_mgr.is_sink_active())
                source, is_iso = sink_mgr.get_recording_source(prefer_isolated=True)
                self.assertEqual(source, "CABLE Output (VB-Audio)")
                self.assertTrue(is_iso)

    def test_windows_setup_and_teardown(self):
        sink_mgr = AudioSinkManager("SpotifySink")
        with patch("sys.platform", "win32"):
            with patch.object(sink_mgr, "get_windows_audio_devices", return_value=["CABLE Output (VB-Audio)"]):
                ok, msg = sink_mgr.setup_isolated_sink()
                self.assertTrue(ok)
                self.assertIn("VB-Audio", msg)

            with patch.object(sink_mgr, "get_windows_audio_devices", return_value=[]):
                ok, msg = sink_mgr.setup_isolated_sink()
                self.assertFalse(ok)
                self.assertIn("virtual audio cable", msg)

            ok, msg = sink_mgr.teardown_isolated_sink()
            self.assertTrue(ok)


class TestTrackMetadataCanonicalization(unittest.TestCase):
    def test_canonical_id_normalization(self):
        meta = TrackMetadata(
            status="Playing",
            track_id="/com/spotify/track/4iV5W9uYEdYUVa79Axb7Rh",
            artist="Arijit Singh",
            title="Tum Hi Ho",
            album="Aashiqui 2",
            album_artist="Mithoon",
            track_number="1",
            disc_number="1",
            duration=262.0,
            position=10.0,
            art_url="https://i.scdn.co/...",
            is_ad=False
        )
        self.assertEqual(meta.canonical_id, "spotify:track:4iV5W9uYEdYUVa79Axb7Rh")

        meta2 = TrackMetadata(
            status="Playing",
            track_id="spotify:track:12345",
            artist="Artist",
            title="Song",
            album="Album",
            album_artist="Artist",
            track_number="1",
            disc_number="1",
            duration=180.0,
            position=0.0,
            art_url="",
            is_ad=False
        )
        self.assertEqual(meta2.canonical_id, "spotify:track:12345")


class TestUniversalPlayerDelegation(unittest.TestCase):
    def test_player_delegates_by_platform(self):
        with patch("sys.platform", "win32"):
            with patch("player_windows.WindowsSpotifyPlayer.get_track_data", return_value=None) as mock_win:
                res = SpotifyPlayer.get_track_data()
                mock_win.assert_called_once()
                self.assertIsNone(res)

        with patch("sys.platform", "darwin"):
            with patch("player_macos.MacOSSpotifyPlayer.get_track_data", return_value=None) as mock_mac:
                res = SpotifyPlayer.get_track_data()
                mock_mac.assert_called_once()
                self.assertIsNone(res)

        with patch("sys.platform", "linux"):
            with patch("player_mpris.SpotifyMPRIS.get_linux_track_data", return_value=None) as mock_linux:
                res = SpotifyPlayer.get_track_data()
                mock_linux.assert_called_once()
                self.assertIsNone(res)


class TestConfigLoading(unittest.TestCase):
    def test_config_defaults(self):
        cfg = RecorderConfig()
        self.assertEqual(cfg.audio_codec, "libopus")
        self.assertEqual(cfg.audio_sample_rate, "48000")
        self.assertEqual(cfg.windows_audio_device, "")
        self.assertIn("Pritam", cfg.composers)


class TestDaemonSupervisorLocking(unittest.TestCase):
    def test_pid_check(self):
        from main import DaemonSupervisor
        # Current process is running
        self.assertTrue(DaemonSupervisor._is_pid_running(os.getpid()))
        # Negative / zero PID is not running
        self.assertFalse(DaemonSupervisor._is_pid_running(0))
        self.assertFalse(DaemonSupervisor._is_pid_running(-1))


class TestWindowsTitleParsing(unittest.TestCase):
    def test_title_split_logic(self):
        # Test simulated window titles
        test_title = "Arijit Singh - Tum Hi Ho"
        parts = test_title.split(" - ", 1)
        self.assertEqual(parts[0], "Arijit Singh")
        self.assertEqual(parts[1], "Tum Hi Ho")


class TestSongRemoverSafety(unittest.TestCase):
    def test_safe_file_validation(self):
        cfg = RecorderConfig()
        remover = SongRemover(cfg)
        
        # Test path traversal prevention
        path, reason = remover.safe_file("../secret.opus")
        self.assertIsNone(path)
        self.assertIn("refusing unsafe file name", reason)

        path, reason = remover.safe_file(".hidden.opus")
        self.assertIsNone(path)
        self.assertIn("refusing unsafe file name", reason)

        path, reason = remover.safe_file("track.mp3")
        self.assertIsNone(path)
        self.assertIn("not an .opus file", reason)


if __name__ == "__main__":
    unittest.main()
