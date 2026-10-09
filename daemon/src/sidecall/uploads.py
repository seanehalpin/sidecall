"""Screenshot uploads (SPEC §9): `POST /api/upload` stores images under
`<home>/uploads/<32-hex>.<ext>`; messages refer to them by the hex ref."""

from __future__ import annotations

import base64
import logging
import re
import secrets
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_AGE_SECONDS = 7 * 24 * 3600
MEDIA_TYPES: dict[str, str] = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
_EXT_MEDIA = {ext: media for media, ext in MEDIA_TYPES.items()}
_REF = re.compile(r"[0-9a-f]{32}")


def is_ref(value: str) -> bool:
    return _REF.fullmatch(value) is not None


def media_type_of(content_type: str | None) -> str | None:
    """The supported image media type of a Content-Type header, or None."""
    if not content_type:
        return None
    media = content_type.split(";", 1)[0].strip().lower()
    return media if media in MEDIA_TYPES else None


@dataclass(frozen=True)
class Image:
    data: bytes
    media_type: str

    def base64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")


class UploadStore:
    def __init__(self, home: Path) -> None:
        self.dir = home / "uploads"

    def save(self, data: bytes, media_type: str) -> str:
        ext = MEDIA_TYPES[media_type]
        self.dir.mkdir(parents=True, exist_ok=True)
        ref = secrets.token_hex(16)
        (self.dir / f"{ref}.{ext}").write_bytes(data)
        return ref

    def resolve(self, ref: str | None) -> Path | None:
        """The file for `ref`, or None. Only exact 32-hex refs are looked up, so the
        input never becomes part of a path unless it's known to be safe."""
        if not ref or not is_ref(ref):
            return None
        for ext in _EXT_MEDIA:
            path = self.dir / f"{ref}.{ext}"
            if path.is_file():
                return path
        return None

    def load(self, ref: str | None) -> Image | None:
        path = self.resolve(ref)
        if path is None:
            return None
        try:
            return Image(path.read_bytes(), _EXT_MEDIA[path.suffix.removeprefix(".")])
        except OSError:
            log.warning("could not read upload %s", ref)
            return None

    def prune(self, max_age: float = MAX_AGE_SECONDS, now: float | None = None) -> int:
        """Delete uploads older than `max_age` seconds. Returns how many were removed."""
        if not self.dir.is_dir():
            return 0
        cutoff = (now if now is not None else time.time()) - max_age
        removed = 0
        for path in self.dir.iterdir():
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed


# --- SDK streaming input -----------------------------------------------------------


def user_message(text: str, image: Image | None = None) -> dict[str, Any]:
    """A streaming-input user message: text, plus an image block when given
    (shape verified in spikes/agent-sdk/s4_image.py)."""
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    if image is not None:
        source = {"type": "base64", "media_type": image.media_type, "data": image.base64()}
        content.append({"type": "image", "source": source})
    return {
        "type": "user",
        "message": {"role": "user", "content": content},
        "parent_tool_use_id": None,
    }


async def one_message(message: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    yield message


def sdk_prompt(text: str, image: Image | None) -> str | AsyncIterator[dict[str, Any]]:
    """What to pass to `ClaudeSDKClient.query`: plain text, or a one-message stream
    with the image attached."""
    return text if image is None else one_message(user_message(text, image))
