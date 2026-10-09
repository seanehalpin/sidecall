from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path

from sidecall.uploads import (
    Image,
    UploadStore,
    is_ref,
    media_type_of,
    sdk_prompt,
    user_message,
)


def test_media_type_of() -> None:
    assert media_type_of("image/jpeg") == "image/jpeg"
    assert media_type_of("Image/PNG; charset=binary") == "image/png"
    assert media_type_of("image/webp") == "image/webp"
    assert media_type_of("image/gif") is None
    assert media_type_of("text/plain") is None
    assert media_type_of(None) is None


def test_save_load_and_resolve(tmp_path: Path) -> None:
    store = UploadStore(tmp_path)
    ref = store.save(b"\xff\xd8jpeg", "image/jpeg")
    assert is_ref(ref)
    assert (tmp_path / "uploads" / f"{ref}.jpg").read_bytes() == b"\xff\xd8jpeg"
    image = store.load(ref)
    assert image == Image(b"\xff\xd8jpeg", "image/jpeg")
    png = store.save(b"png", "image/png")
    assert store.load(png) == Image(b"png", "image/png")
    assert store.load("0" * 32) is None
    assert store.load(None) is None


def test_resolve_rejects_anything_but_exact_refs(tmp_path: Path) -> None:
    store = UploadStore(tmp_path)
    ref = store.save(b"x", "image/jpeg")
    secret = tmp_path / "secret.jpg"
    secret.write_bytes(b"secret")
    for bad in [
        "../secret",
        "../../etc/passwd",
        f"{ref}.jpg",
        f"../uploads/{ref}",
        ref.upper(),
        ref + "0",
        ref[:-1],
        f" {ref}",
        f"{ref}\n",
        "/etc/passwd",
        "",
    ]:
        assert store.resolve(bad) is None, bad
    assert store.resolve(ref) is not None


def test_prune_removes_old_uploads(tmp_path: Path) -> None:
    store = UploadStore(tmp_path)
    assert store.prune() == 0  # no directory yet
    old = store.save(b"old", "image/jpeg")
    new = store.save(b"new", "image/webp")
    old_path = store.resolve(old)
    assert old_path is not None
    eight_days = 8 * 24 * 3600
    stamp = old_path.stat().st_mtime - eight_days
    os.utime(old_path, (stamp, stamp))
    assert store.prune() == 1
    assert store.resolve(old) is None
    assert store.resolve(new) is not None


def test_user_message_shape() -> None:
    assert user_message("hi") == {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        "parent_tool_use_id": None,
    }
    msg = user_message("look", Image(b"abc", "image/jpeg"))
    assert msg["message"]["content"][1] == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.b64encode(b"abc").decode(),
        },
    }


def test_sdk_prompt() -> None:
    assert sdk_prompt("plain", None) == "plain"
    stream = sdk_prompt("look", Image(b"abc", "image/png"))
    assert not isinstance(stream, str)

    async def collect() -> list[object]:
        return [m async for m in stream]

    messages = asyncio.run(collect())
    assert messages == [user_message("look", Image(b"abc", "image/png"))]
