"""
Metadata and artwork tagging module for untitled.arr.
Applies Vorbis comments and embeds high-resolution cover art into Opus files using mutagen.
"""

from __future__ import annotations
import base64
import urllib.request
from pathlib import Path
from typing import Optional, Tuple
from mutagen.flac import Picture
from mutagen.oggopus import OggOpus
from player_mpris import TrackMetadata


# ==============================================================================
# METADATA & ARTWORK TAGGER
# ==============================================================================

class MetadataTagger:

    # ==========================================================================
    # ARTWORK DOWNLOAD
    # ==========================================================================

    @staticmethod
    def fetch_artwork(art_url: str, timeout: float = 8.0) -> Optional[Tuple[bytes, str]]:
        """Downloads cover art bytes from URL. Returns (image_bytes, mime_type) or None."""
        if not art_url or not art_url.startswith("http"):
            return None

        try:
            req = urllib.request.Request(
                art_url,
                headers={"User-Agent": "untitled.arr/1.0"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                content_type = response.headers.get("Content-Type", "image/jpeg")
                data = response.read()
                if len(data) > 100:
                    return data, content_type
        except Exception as e:
            print(f"Warning: Could not download album art: {e}")
        return None

    # ==========================================================================
    # FILE TAGGING
    # ==========================================================================

    @classmethod
    def tag_file(cls, file_path: Path, meta: TrackMetadata, embed_art: bool = True) -> bool:
        """Embeds Vorbis comments and optional METADATA_BLOCK_PICTURE into the Opus container."""
        if not file_path.exists():
            return False

        try:
            audio = OggOpus(file_path)
            if audio is None:
                return False

            # Embed standard Vorbis tags
            audio["title"] = [meta.title]
            audio["artist"] = [meta.artist]
            audio["album"] = [meta.album]
            audio["albumartist"] = [meta.album_artist]
            audio["tracknumber"] = [meta.track_number]
            audio["discnumber"] = [meta.disc_number]
            audio["spotify_track_id"] = [meta.canonical_id]

            # Embed front cover artwork
            if embed_art and meta.art_url:
                art_result = cls.fetch_artwork(meta.art_url)
                if art_result:
                    img_bytes, mime_type = art_result
                    picture = Picture()
                    picture.type = 3  # Cover (front)
                    picture.mime = mime_type
                    picture.desc = "Front Cover"
                    picture.data = img_bytes
                    encoded_pic = base64.b64encode(picture.write()).decode("ascii")
                    audio["metadata_block_picture"] = [encoded_pic]

            audio.save()
            return True
        except Exception as e:
            print(f"Error: Could not embed metadata tags into {file_path.name}: {e}")
            return False
