"""
Safe song remover for untitled.arr.

Deleting a song means two things: the .opus file AND its entry in library.json.
Doing only one leaves the library inconsistent, so this module does both, in a safe order:

  1. Move the file to the Trash (recoverable) -- or hard-delete only with --permanent.
  2. Only if step 1 worked, drop the index entry.

Safety rules:
  * Only plain, non-hidden *.opus files that live directly inside the output folder are touched
    (no path traversal via a tampered index, no symlink following, never the temp takes).
  * Nothing happens without confirmation unless --yes is given; --dry-run changes nothing.
  * If a file is missing but still indexed (or the reverse) each half is handled on its own.
"""

from __future__ import annotations
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple
from config import CONFIG, RecorderConfig
from library_index import LibraryManager

_ID_RE = re.compile(r"(?:spotify:track:|open\.spotify\.com/(?:intl-[a-z]+/)?track/)([A-Za-z0-9]{22})")


@dataclass
class Candidate:
    track_id: Optional[str]      # None => file on disk that isn't indexed
    file_name: Optional[str]     # None => indexed but file is gone
    label: str


class SongRemover:
    def __init__(self, config: RecorderConfig = CONFIG, library: Optional[LibraryManager] = None):
        self.config = config
        self.library = library or LibraryManager(config)
        self.out = config.output_dir.resolve()

    # ------------------------------------------------------------------ safety
    def safe_file(self, file_name: Optional[str]) -> Tuple[Optional[Path], str]:
        """Returns (path, "") if file_name is a legitimate library file, else (None, reason)."""
        if not file_name:
            return None, "no file recorded"
        if Path(file_name).name != file_name or file_name.startswith("."):
            return None, f"refusing unsafe file name {file_name!r}"
        if not file_name.lower().endswith(".opus"):
            return None, "not an .opus file"
        path = self.out / file_name
        if path.is_symlink():
            return None, "is a symlink (refusing to follow)"
        if not path.exists():
            return None, "file not found on disk"
        if not path.is_file() or path.resolve().parent != self.out:
            return None, "outside the library folder"
        return path, ""

    # ------------------------------------------------------------------ search
    def all_candidates(self) -> List[Candidate]:
        self.library._refresh_from_disk()
        cands: List[Candidate] = []
        indexed_files = set()
        for tid, e in self.library.index.items():
            fname = e.get("file")
            indexed_files.add(fname)
            artists = e.get("artists") or [e.get("artist", "")]
            label = f"{e.get('title', '?')} · {', '.join(artists)}"
            if not fname or not (self.out / fname).exists():
                label += "  [file missing]"
            cands.append(Candidate(tid, fname, label))
        if self.out.exists():
            for f in sorted(self.out.glob("*.opus")):
                if f.name.startswith(".") or f.name in indexed_files:
                    continue
                cands.append(Candidate(None, f.name, f"{f.stem}  [not in index]"))
        return sorted(cands, key=lambda c: c.label.lower())

    def find(self, query: str) -> List[Candidate]:
        query = (query or "").strip()
        cands = self.all_candidates()
        if not query:
            return cands
        m = _ID_RE.search(query)
        tid = f"spotify:track:{m.group(1)}" if m else (
            f"spotify:track:{query}" if re.fullmatch(r"[A-Za-z0-9]{22}", query) else None)
        if tid:
            return [c for c in cands if c.track_id == tid]
        tokens = query.lower().split()
        out = []
        for c in cands:
            e = self.library.index.get(c.track_id or "", {})
            hay = " ".join([c.label, c.file_name or "", e.get("album", ""),
                            " ".join(e.get("composers", []))]).lower()
            if all(t in hay for t in tokens):
                out.append(c)
        return out

    # ------------------------------------------------------------------ removal
    def _trash(self, path: Path) -> Tuple[bool, str]:
        """Moves to the desktop Trash/Recycle Bin when possible, else to <library>/.removed/."""
        # 1. Windows Recycle Bin
        if sys.platform == "win32":
            try:
                import send2trash
                send2trash.send2trash(str(path))
                if not path.exists():
                    return True, "moved to Recycle Bin (send2trash)"
            except Exception:
                pass

            try:
                import ctypes
                from ctypes import wintypes

                class SHFILEOPSTRUCTW(ctypes.Structure):
                    _fields_ = [
                        ("hwnd", wintypes.HWND),
                        ("wFunc", wintypes.UINT),
                        ("pFrom", wintypes.LPCWSTR),
                        ("pTo", wintypes.LPCWSTR),
                        ("fFlags", wintypes.WORD),
                        ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", wintypes.LPVOID),
                        ("lpszProgressTitle", wintypes.LPCWSTR),
                    ]

                file_op = SHFILEOPSTRUCTW()
                file_op.wFunc = 0x0003  # FO_DELETE
                file_op.pFrom = str(path.resolve()) + "\0\0"
                file_op.fFlags = 0x0040 | 0x0010 | 0x0004  # FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
                result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(file_op))
                if result == 0 and not path.exists():
                    return True, "moved to Recycle Bin"
            except Exception:
                pass

        # 2. macOS Trash via Finder
        elif sys.platform == "darwin":
            try:
                script = f'tell application "Finder" to delete POSIX file "{path.resolve()}"'
                r = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
                if r.returncode == 0 and not path.exists():
                    return True, "moved to Trash (Finder)"
            except Exception:
                pass

        # 3. Linux desktop Trash
        else:
            for cmd in (["gio", "trash", str(path)], ["trash-put", str(path)]):
                if shutil.which(cmd[0]):
                    try:
                        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                        if r.returncode == 0 and not path.exists():
                            return True, f"moved to Trash ({cmd[0]})"
                    except Exception:
                        pass

        # 4. Fallback: move to <library>/.removed/
        try:
            hold = self.out / ".removed"
            hold.mkdir(exist_ok=True)
            dest = hold / f"{path.stem}.{int(time.time())}.opus"
            shutil.move(str(path), str(dest))
            return True, f"moved to {dest.relative_to(self.out)} (no system Trash found)"
        except Exception as e:
            return False, f"could not move to Trash: {e}"

    def remove(self, c: Candidate, permanent: bool = False, unindex_only: bool = False,
               dry_run: bool = False) -> Tuple[bool, str]:
        steps = []
        if c.file_name and not unindex_only:
            path, why = self.safe_file(c.file_name)
            if path is None:
                if why == "file not found on disk":
                    steps.append("file already gone")
                else:
                    return False, f"skipped, {why}; nothing changed"
            elif dry_run:
                steps.append("would " + ("permanently delete" if permanent else "move to Trash") + f" {path.name}")
            elif permanent:
                try:
                    path.unlink()
                    steps.append("file permanently deleted")
                except OSError as e:
                    return False, f"could not delete file ({e}); index left untouched"
            else:
                ok, msg = self._trash(path)
                if not ok:
                    return False, f"{msg}; index left untouched"
                steps.append(msg)

        if c.track_id:
            if dry_run:
                steps.append("would remove from library index")
            elif self.library.remove_entry(c.track_id):
                steps.append("removed from library index")
            else:
                steps.append("was not in index")
        return True, "; ".join(steps) or "nothing to do"
