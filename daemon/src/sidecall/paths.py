"""Path helpers shared by the worker, talker tools and (from M2) pick normalisation."""

from __future__ import annotations

import contextlib
from pathlib import Path

from .protocol.models import Brief


def rel_path(path: str, repo: Path) -> str:
    """Repo-relative POSIX path when `path` is inside the repo, else `path` unchanged."""
    p = Path(path)
    if not p.is_absolute():
        return p.as_posix().removeprefix("./")
    for root in (repo, repo.resolve()):
        with contextlib.suppress(ValueError):
            return p.relative_to(root).as_posix()
    with contextlib.suppress(ValueError, OSError):
        return p.resolve().relative_to(repo.resolve()).as_posix()
    return path


def normalise_brief(brief: Brief, repo: Path) -> Brief:
    """Copy of `brief` with target files made repo-relative."""
    out = brief.model_copy(deep=True)
    for target in out.targets:
        if target.file:
            target.file = rel_path(target.file, repo)
    return out
