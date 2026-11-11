# untitled.arr

Automatically records songs you listen to on Spotify and saves them directly to your music folder with tags and album art.

## Features
- **Auto-Detection**: Records songs in the background as you listen to them.
- **Completion Check**: Only keeps songs you fully listened to (skips, scrubs, and ads are discarded).
- **Clean Audio**: Isolates Spotify playback so desktop alerts and system sounds never end up in your recordings.
- **No Duplicates**: Keeps track of saved songs so it won't re-record what you already have.
- **Tagged & Organized**: Automatically names files as `Track (Artist, Artist).opus` and embeds metadata and cover art.
- **Multi-Artist Detection**: Spotify only tells Linux apps the first credited artist, so the full credits are looked up by track ID. Music directors (e.g. Pritam) are moved to the `composer` tag when a singer is also credited.
- **Safe Remover**: Delete a song and unindex it in one step, via Trash by default.

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
- **List songs**: `python3 main.py list [words]`
- **Remove a song** (moves to Trash, then unindexes):
  ```bash
  python3 main.py remove "tum hi ho"            # asks to confirm
  python3 main.py remove <spotify-url> --yes    # exact match by link/URI
  python3 main.py remove "tum" --dry-run        # preview only
  python3 main.py remove "tum" --unindex-only   # keep the file, drop from library.json
  python3 main.py remove "tum" --permanent      # hard delete (asks you to type DELETE)
  ```
- **Fix artists on songs you already recorded** (retags, renames, updates the index):
  ```bash
  python3 main.py refresh-artists --dry-run
  python3 main.py refresh-artists
  ```
- **Reset audio routing** (when finished):
  ```bash
  python3 main.py teardown-sink
  ```

## Artist settings (`config.json`)

```json
{
  "composers": ["Pritam", "Vishal-Shekhar", "Amit Trivedi"],
  "artist_separator": ", ",
  "resolve_all_artists": true,
  "spotify_client_id": "",
  "spotify_client_secret": ""
}
```
`composers` is the list of names hidden from the artist whenever someone else is credited (add or remove names freely). Artist lookup works without any login; adding a free Spotify developer client id/secret (or `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` env vars) makes it more reliable.

## Requirements
- Linux (PipeWire / PulseAudio)
- Python 3.8+
- `ffmpeg`, `ffprobe`, `playerctl`
- `python3-mutagen` (`pip install mutagen`)
