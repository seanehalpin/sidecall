"""Picks on the daemon side (SPEC §5.3, §5.4): path normalisation, the in-memory
store, and per-task snapshots.

Everything here is plain functions and small classes so it can be unit-tested
without a session.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from pydantic import TypeAdapter, ValidationError

from .config import atomic_write_text
from .protocol.models import ChainEntry, Pick

log = logging.getLogger(__name__)

MAX_PICKS = 100
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


# --- path normalisation (SPEC §5.4) --------------------------------------------------


def _clean(raw: str) -> tuple[str, bool]:
    """Strip the scheme/host, query and fragment from a raw location.

    Returns (path, is_url). Vite's `/@fs/<abs>` and `/@id/<id>` prefixes are
    unwrapped to the absolute or root-relative path they wrap.
    """
    s = raw.strip()
    is_url = False
    if _SCHEME.match(s):
        is_url = True
        s = unquote(urlsplit(s).path)
    else:
        s = s.split("#", 1)[0].split("?", 1)[0]
    if s.startswith("/@fs/"):
        s = "/" + s.removeprefix("/@fs/").lstrip("/")
        is_url = False  # an absolute filesystem path
    elif s.startswith("/@id/"):
        s = s.removeprefix("/@id/")
        if s.startswith("__x00__"):  # virtual modules have no file
            return "", is_url
    return s, is_url


def _inside(candidate: Path, root: Path) -> str | None:
    """Repo-relative POSIX path of `candidate` if it is an existing file inside `root`
    (symlinks and `..` resolved) and not under node_modules."""
    try:
        resolved = candidate.resolve()
        rel = resolved.relative_to(root)
    except (ValueError, OSError):
        return None
    if "node_modules" in rel.parts or not resolved.is_file():
        return None
    return rel.as_posix()


def in_node_modules(raw: str) -> bool:
    path, _ = _clean(raw)
    return "node_modules" in PurePosixPath(path).parts


def normalise_path(raw: str, repo_root: Path) -> str | None:
    """Map a raw source location to a repo-relative POSIX path, or None.

    Accepts project-relative paths, absolute filesystem paths, dev-server URLs and
    root-relative URL paths (query strings and fragments are dropped). Returns None
    if the file is outside the repo, under node_modules, or doesn't exist.
    """
    path, is_url = _clean(raw)
    if not path or "\x00" in path:
        return None
    root = repo_root.resolve()
    if path.startswith("/"):
        under_root = root / path.lstrip("/")
        # A URL path is root-relative to the dev server; a bare absolute path is a
        # filesystem path first, with the root-relative reading as the fallback.
        order = [under_root, Path(path)] if is_url else [Path(path), under_root]
        for candidate in order:
            found = _inside(candidate, root)
            if found is not None:
                return found
        return None
    return _inside(root / path, root)


def _major(version: str | None) -> int | None:
    if not version:
        return None
    m = re.match(r"\s*v?(\d+)", version)
    return int(m.group(1)) if m else None


def column_offset(name: str, version: str | None) -> int:
    """Amount to add to a reported column to make it 1-based. Svelte and React 19
    report 0-based columns; React 18 and earlier (and unknown) are already 1-based."""
    if name == "svelte":
        return 1
    if name == "react":
        major = _major(version)
        return 1 if major is not None and major >= 19 else 0
    return 0


def normalise_pick(pick: Pick, repo_root: Path) -> Pick:
    """A copy of `pick` with repo-relative source paths and 1-based columns.

    Unverifiable sources are dropped (the chain keeps its names). Chain entries
    under node_modules are removed. Not idempotent: call once, when the pick is added.
    """
    out = pick.model_copy(deep=True)
    fw = out.framework
    offset = column_offset(fw.name, fw.version)

    src = fw.source
    if src is not None:
        rel = normalise_path(src.file, repo_root)
        if rel is None:
            fw.source = None
        else:
            src.file = rel
            src.column = src.column + offset

    chain: list[ChainEntry] = []
    for entry in fw.component_chain:
        if entry.file is not None and in_node_modules(entry.file):
            continue
        rel = normalise_path(entry.file, repo_root) if entry.file else None
        if rel is None:
            entry.file = entry.line = entry.column = None
        else:
            entry.file = rel
            if entry.column is not None:
                entry.column += offset
        chain.append(entry)
    fw.component_chain = chain
    return out


# --- store -----------------------------------------------------------------------------


class PickStore:
    """Picks by id, in memory. Newest wins on a duplicate id; the oldest are evicted
    beyond the cap."""

    def __init__(self, cap: int = MAX_PICKS) -> None:
        self.cap = cap
        self._picks: dict[int, Pick] = {}

    def add(self, pick: Pick) -> None:
        self._picks.pop(pick.id, None)  # re-insert at the end
        self._picks[pick.id] = pick
        while len(self._picks) > self.cap:
            del self._picks[next(iter(self._picks))]

    def remove(self, pick_id: int) -> bool:
        return self._picks.pop(pick_id, None) is not None

    def clear(self) -> None:
        self._picks.clear()

    def get(self, pick_id: int) -> Pick | None:
        return self._picks.get(pick_id)

    def resolve(self, ids: Iterable[int]) -> list[Pick | int]:
        """Each id as its pick, or the bare id when unknown. Order and repeats are
        collapsed to first occurrence."""
        out: list[Pick | int] = []
        seen: set[int] = set()
        for i in ids:
            if i in seen:
                continue
            seen.add(i)
            pick = self._picks.get(i)
            out.append(pick if pick is not None else i)
        return out

    def __len__(self) -> int:
        return len(self._picks)

    def __contains__(self, pick_id: object) -> bool:
        return pick_id in self._picks


# --- task snapshots --------------------------------------------------------------------

_PICK_LIST = TypeAdapter(list[Pick])


def save_snapshot(directory: Path, picks: list[Pick]) -> None:
    """Write `picks.json` into `directory` (a task's state dir)."""
    directory.mkdir(parents=True, exist_ok=True)
    data = _PICK_LIST.dump_json(picks, by_alias=True, indent=1).decode("utf-8")
    atomic_write_text(directory / "picks.json", data + "\n")


def load_snapshot(directory: Path) -> list[Pick]:
    path = directory / "picks.json"
    if not path.is_file():
        return []
    try:
        return _PICK_LIST.validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, OSError):
        log.warning("ignoring unreadable %s", path)
        return []
