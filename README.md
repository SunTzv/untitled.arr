# untitled.arr

Universal, automated Spotify audio recorder. Seamlessly records songs you listen to on Spotify and saves them directly to your music folder with metadata, Vorbis tags, and high-resolution cover art.

Supports **Windows**, **Linux**, and **macOS**.

---

## Features
- **Cross-Platform & Universal**: Native support for Windows (WinRT GSMTC / DirectShow / WASAPI), Linux (PipeWire / PulseAudio / MPRIS), and macOS (AppleScript / AVFoundation).
- **Auto-Detection**: Automatically detects track transitions and captures playback in the background.
- **Completion Check**: Only keeps songs you fully listened to (skips, scrubs, and advertisements are discarded).
- **Clean Audio Isolation**:
  - **Linux**: Creates an isolated PipeWire/PulseAudio null-sink with loopback so desktop sounds never leak into recordings.
  - **Windows**: Supports virtual audio cables (e.g., VB-Audio Virtual Cable) or system audio loopback (Stereo Mix / DirectShow).
- **No Duplicates**: Maintains an atomic, crash-safe `library.json` index so previously recorded songs are never duplicated.
- **Tagged & Organized**: Automatically names files as `Track (Artist, Artist).opus` with embedded Vorbis comments and cover art.
- **Multi-Artist Detection**: Full credits are resolved so each performer is credited, and music directors/composers (e.g. Pritam) are organized into the `composer` tag.
- **Safe Remover**: Removes songs and cleans the index in a single step (moves to Recycle Bin / Trash by default).

---

## Quick Start

### For Windows Users (No Python or FFmpeg Required)

Choose either option:

- **Option A: Pre-built Release (Recommended)**
  1. Download `untitled.arr-windows-x64.zip` from [Releases](../../releases).
  2. Extract the folder and double-click `untitled.arr.exe`.
  *(No Python, FFmpeg, or manual installation required!)*

- **Option B: One-Click Launcher (`start.bat`)**
  1. Download or clone this repository.
  2. Double-click `start.bat`.
  3. The script will automatically detect if Python or FFmpeg are missing and prompt to install them for you via Windows Package Manager (`winget`) or download portable FFmpeg into `bin/`.

Songs are saved directly to: `C:\Users\<YourUser>\Music\Spotify\`

---

### For Linux & macOS Users

#### 1. Requirements
- **Python 3.8+**
- **FFmpeg & FFprobe**:
  - **Linux**: `sudo apt install ffmpeg playerctl` (Debian/Ubuntu) or `sudo pacman -S ffmpeg playerctl` (Arch).
  - **macOS**: `brew install ffmpeg`

#### 2. Install Dependencies & Run
```bash
pip install -r requirements.txt
python3 start.py
```

Press `Ctrl+C` at any time to cleanly finalize the current take and stop.

Songs are saved to:
- **Windows**: `C:\Users\<User>\Music\Spotify\`
- **Linux**: `~/Music/Spotify/`
- **macOS**: `~/Music/Spotify/`

---

## Windows Audio Isolation & Routing

untitled.arr works out-of-the-box on Windows using standard system loopback audio. For isolated recordings free of notification sounds:

1. Install [VB-Audio Virtual Cable](https://vb-audio.com/Cable/) (free).
2. Open Windows **Settings** → **System** → **Sound** → **Volume mixer**.
3. Under **Apps**, find **Spotify** and change its **Output device** to **CABLE Input (VB-Audio Virtual Cable)**.
4. untitled.arr will automatically detect `CABLE Output` as the active isolated audio source!

You can also specify a custom audio device name in `config.json`:
```json
{
  "windows_audio_device": "CABLE Output (VB-Audio Virtual Cable)"
}
```

---

## Useful Commands

Replace `python` with `python3` on Linux/macOS:

- **Check status**:
  ```bash
  python main.py status
  ```
- **Audit your library**:
  ```bash
  python main.py verify-library
  ```
- **List songs**:
  ```bash
  python main.py list [words]
  ```
- **Remove a song** (moves to Recycle Bin / Trash, then unindexes):
  ```bash
  python main.py remove "tum hi ho"            # asks to confirm
  python main.py remove <spotify-url> --yes    # exact match by link/URI
  python main.py remove "tum" --dry-run        # preview only
  python main.py remove "tum" --unindex-only   # keep the file, drop from library.json
  python main.py remove "tum" --permanent      # hard delete (asks you to type DELETE)
  ```
- **Fix artists on recorded songs** (retags, renames, updates index):
  ```bash
  python main.py refresh-artists --dry-run
  python main.py refresh-artists
  ```
- **Reset audio routing** (Linux PipeWire teardown):
  ```bash
  python main.py teardown-sink
  ```

---

## Configuration (`config.json`)

```json
{
  "composers": ["Pritam", "Vishal-Shekhar", "Amit Trivedi"],
  "artist_separator": ", ",
  "resolve_all_artists": true,
  "windows_audio_device": "",
  "spotify_client_id": "",
  "spotify_client_secret": ""
}
```

- `composers`: List of producer/director names hidden from the main artist string when another singer is credited.
- `windows_audio_device`: (Optional) Explicit DirectShow audio device name on Windows.
- `spotify_client_id` / `spotify_client_secret`: Optional credentials from the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) (or set `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` env vars) for faster, full-catalog credit lookups.
