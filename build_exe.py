"""
PyInstaller build script for creating a standalone Windows executable of untitled.arr.
Bundles Python runtime, all dependencies, and ffmpeg/ffprobe binaries.
"""

from __future__ import annotations
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def build():
    print("=======================================================")
    print("Building standalone untitled.arr executable")
    print("=======================================================")

    # 1. Ensure pyinstaller is installed
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[*] Installing pyinstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # 2. Look for ffmpeg and ffprobe binaries to bundle
    binaries = []
    bin_dir = ROOT / "bin"
    ext = ".exe" if sys.platform == "win32" else ""
    for tool in ("ffmpeg", "ffprobe"):
        tool_name = f"{tool}{ext}"
        # Check local bin/
        src = bin_dir / tool_name
        if not src.is_file():
            # Check system PATH
            which_path = shutil.which(tool)
            if which_path:
                src = Path(which_path)

        if src.is_file():
            print(f"[+] Found {tool}: {src}")
            binaries.append(f"{src}{os.pathsep}.")
        else:
            print(f"[!] Warning: {tool_name} not found in bin/ or PATH. Bundle will rely on external FFmpeg.")

    # 3. PyInstaller options
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name=untitled.arr",
        "--onefile",
        "--console",
        "--clean",
    ]

    for b in binaries:
        cmd.extend(["--add-binary", b])

    # Include hidden imports for mutagen, winsdk, and urllib
    hidden_imports = [
        "mutagen",
        "mutagen.oggopus",
        "mutagen.flac",
        "player_windows",
        "player_mpris",
        "player_macos",
        "player",
    ]
    if sys.platform == "win32":
        hidden_imports.extend(["winsdk", "ctypes"])

    for imp in hidden_imports:
        cmd.extend(["--hidden-import", imp])

    cmd.append(str(ROOT / "start.py"))

    print("\n[*] Running PyInstaller...")
    subprocess.check_call(cmd)

    dist_exe = ROOT / "dist" / (f"untitled.arr{ext}")
    if dist_exe.is_file():
        size_mb = dist_exe.stat().st_size / (1024 * 1024)
        print(f"\n✓ Successfully built: {dist_exe} ({size_mb:.1f} MB)")
        print("A Windows user can double-click this file without installing Python or FFmpeg!")


if __name__ == "__main__":
    build()
