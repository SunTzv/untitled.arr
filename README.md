# untitled.arr

Automatically records songs you listen to on Spotify and saves them directly to your music folder with tags and album art.

## Features
- **Auto-Detection**: Records songs in the background as you listen to them.
- **Completion Check**: Only keeps songs you fully listened to (skips, scrubs, and ads are discarded).
- **Clean Audio**: Isolates Spotify playback so desktop alerts and system sounds never end up in your recordings.
- **No Duplicates**: Keeps track of saved songs so it won't re-record what you already have.
- **Tagged & Organized**: Automatically names files as `Track (Artist).opus` and embeds metadata and cover art.

## Quick Start

Make sure Spotify is open and playing, then run:

```bash
python3 start.py
```

This will automatically create the isolated audio sink, route Spotify to it, and start recording. Press `Ctrl+C` to stop.

Songs are saved to: `~/Music/Spotify/`

## Useful Commands

- **Check status**:
  ```bash
  python3 main.py status
  ```
- **Audit your library**:
  ```bash
  python3 main.py verify-library
  ```
- **Reset audio routing** (when finished):
  ```bash
  python3 main.py teardown-sink
  ```

## Requirements
- Linux (PipeWire / PulseAudio)
- Python 3.8+
- `ffmpeg`, `ffprobe`, `playerctl`
- `python3-mutagen` (`pip install mutagen`)
