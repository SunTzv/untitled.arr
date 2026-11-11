"""
Multi-artist resolver for untitled.arr.

Spotify's MPRIS interface only exposes the FIRST credited artist (for Bollywood that is
usually the music director, e.g. Pritam). This module looks up the full credit list by track
id and separates performers (singers) from composers (music directors).

Lookup order (first success wins, results cached on disk):
  1. Spotify Web API  - only if client id/secret are configured (most reliable)
  2. open.spotify.com/embed/track/<id>  - no login, parses the embedded page JSON
  3. open.spotify.com/track/<id>        - og:description meta fallback
If everything fails, the caller keeps the single MPRIS artist.
"""

from __future__ import annotations
import base64
import html
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from config import CONFIG, RecorderConfig
from player_mpris import TrackMetadata

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _dedupe(names: List[str]) -> List[str]:
    seen, out = set(), []
    for n in names:
        n = re.sub(r"\s+", " ", n.replace("\u00a0", " ")).strip()
        if n and _norm(n) not in seen:
            seen.add(_norm(n))
            out.append(n)
    return out


def _http_get(url: str, headers: Optional[dict] = None, data: Optional[bytes] = None, timeout: float = 8.0) -> str:
    h = {"User-Agent": UA, "Accept-Language": "en"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


# ==============================================================================
# PARSERS (pure functions - easy to test)
# ==============================================================================

def parse_embed_html(page: str) -> List[str]:
    """Extracts artist names from the embed page's __NEXT_DATA__ JSON."""
    m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return []

    found: List[List[str]] = []
    subtitle: List[str] = []

    def walk(node):
        if isinstance(node, dict):
            arts = node.get("artists")
            if isinstance(arts, list) and arts and all(isinstance(a, dict) and "name" in a for a in arts):
                found.append([str(a["name"]) for a in arts])
            sub = node.get("subtitle")
            if isinstance(sub, str) and not subtitle:
                subtitle.append(sub)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    if found:
        return _dedupe(max(found, key=len))
    if subtitle:  # "Arijit Singh, Pritam" style string
        return _dedupe(re.split(r",\s*", subtitle[0]))
    return []


def parse_og_description(page: str) -> List[str]:
    """Fallback: <meta property="og:description" content="Artist1, Artist2 · Song · 2013">."""
    m = re.search(r'property="og:description"\s+content="([^"]+)"', page) or \
        re.search(r'content="([^"]+)"\s+property="og:description"', page)
    if not m:
        return []
    desc = html.unescape(m.group(1))
    first = re.split(r"\s+[·•|]\s+", desc)[0]
    return _dedupe(re.split(r",\s*", first))


# ==============================================================================
# RESOLVER
# ==============================================================================

class ArtistResolver:
    def __init__(self, config: RecorderConfig = CONFIG):
        self.config = config
        self.cache_file = config.output_dir / ".artist_cache.json"
        self._lock = threading.RLock()
        self._cache: Dict[str, List[str]] = {}
        self._token: Optional[Tuple[str, float]] = None
        self._load()

    # ---- cache ----
    def _load(self) -> None:
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._cache = {k: v for k, v in data.items() if isinstance(v, list)}
        except Exception:
            self._cache = {}

    def _save(self) -> None:
        tmp = self.cache_file.with_name(f".artist_cache_{os.getpid()}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, indent=1, ensure_ascii=False)
            tmp.replace(self.cache_file)
        except Exception:
            tmp.unlink(missing_ok=True)

    # ---- providers ----
    def _api_token(self) -> Optional[str]:
        cid = self.config.spotify_client_id or os.environ.get("SPOTIFY_CLIENT_ID", "")
        sec = self.config.spotify_client_secret or os.environ.get("SPOTIFY_CLIENT_SECRET", "")
        if not (cid and sec):
            return None
        if self._token and self._token[1] > time.time() + 30:
            return self._token[0]
        auth = base64.b64encode(f"{cid}:{sec}".encode()).decode()
        body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
        raw = _http_get("https://accounts.spotify.com/api/token",
                        {"Authorization": f"Basic {auth}"}, data=body)
        j = json.loads(raw)
        self._token = (j["access_token"], time.time() + int(j.get("expires_in", 3600)))
        return self._token[0]

    def _from_api(self, tid: str) -> List[str]:
        token = self._api_token()
        if not token:
            return []
        raw = _http_get(f"https://api.spotify.com/v1/tracks/{tid}", {"Authorization": f"Bearer {token}"})
        return _dedupe([a["name"] for a in json.loads(raw).get("artists", [])])

    def _from_embed(self, tid: str) -> List[str]:
        return parse_embed_html(_http_get(f"https://open.spotify.com/embed/track/{tid}"))

    def _from_page(self, tid: str) -> List[str]:
        return parse_og_description(_http_get(f"https://open.spotify.com/track/{tid}"))

    def search_track(self, title: str, artist: str) -> Optional[dict]:
        """Searches Spotify Web API for track metadata if API creds are configured."""
        token = self._api_token()
        if not token:
            return None
        try:
            q = urllib.parse.quote(f"track:{title} artist:{artist}")
            raw = _http_get(
                f"https://api.spotify.com/v1/search?q={q}&type=track&limit=1",
                {"Authorization": f"Bearer {token}"}
            )
            items = json.loads(raw).get("tracks", {}).get("items", [])
            if not items:
                return None
            item = items[0]
            tid = f"spotify:track:{item.get('id')}"
            names = _dedupe([a["name"] for a in item.get("artists", [])])
            images = item.get("album", {}).get("images", [])
            art_url = images[0].get("url", "") if images else ""
            duration = float(item.get("duration_ms", 0)) / 1000.0
            album = item.get("album", {}).get("name", "")
            if names:
                with self._lock:
                    self._cache[tid] = names
                    self._save()
            return {
                "track_id": tid,
                "artists": names,
                "album": album,
                "duration": duration,
                "art_url": art_url
            }
        except Exception:
            return None

    def lookup(self, canonical_id: str) -> List[str]:
        """Returns the full ordered credit list for a spotify:track:<id>, or [] on failure."""
        tid = canonical_id.split(":")[-1]
        if not re.fullmatch(r"[A-Za-z0-9]{10,30}", tid or ""):
            return []
        with self._lock:
            if canonical_id in self._cache:
                return list(self._cache[canonical_id])
        for provider in (self._from_api, self._from_embed, self._from_page):
            try:
                names = provider(tid)
            except Exception:
                names = []
            if names:
                with self._lock:
                    self._cache[canonical_id] = names
                    self._save()
                return names
        return []

    # ---- performer / composer split ----
    def split_roles(self, names: List[str]) -> Tuple[List[str], List[str]]:
        """Hides configured composers if at least one other artist remains."""
        comp_set = {_norm(c) for c in self.config.composers}
        performers = [n for n in names if _norm(n) not in comp_set]
        composers = [n for n in names if _norm(n) in comp_set]
        if not performers:          # only composers credited -> keep them as the artist
            return list(names), []
        return performers, composers

    def enrich(self, meta: TrackMetadata) -> Tuple[TrackMetadata, bool]:
        """Returns (meta with full artists filled in, lookup_succeeded)."""
        names: List[str] = []
        if self.config.resolve_all_artists:
            names = self.lookup(meta.canonical_id)
            if not names and meta.title and meta.artist:
                searched = self.search_track(meta.title, meta.artist)
                if searched:
                    names = searched.get("artists", [])
                    if searched.get("track_id") and ":win_" in meta.track_id:
                        meta = replace(meta, track_id=searched["track_id"])
                    if not meta.art_url and searched.get("art_url"):
                        meta = replace(meta, art_url=searched["art_url"])
                    if (not meta.album or meta.album == "Unknown Album") and searched.get("album"):
                        meta = replace(meta, album=searched["album"])
                    if meta.duration <= 0.0 and searched.get("duration"):
                        meta = replace(meta, duration=searched["duration"])

        ok = bool(names)
        if not names:
            names = [meta.artist] if meta.artist and meta.artist != "Unknown Artist" else ["Unknown Artist"]
        performers, composers = self.split_roles(names)
        sep = self.config.artist_separator
        display = sep.join(performers)

        album_artist = meta.album_artist
        comp_set = {_norm(c) for c in self.config.composers}
        if composers and (not album_artist or _norm(album_artist) in comp_set
                          or _norm(album_artist) in {_norm(c) for c in composers}):
            album_artist = display
        return replace(meta, artist=display, artists=performers, composers=composers,
                       album_artist=album_artist), ok
