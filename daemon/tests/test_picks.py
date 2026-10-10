from __future__ import annotations

import os
from pathlib import Path

import pytest
from helpers import make_pick

from sidecall.picks import (
    PickStore,
    column_offset,
    load_snapshot,
    normalise_path,
    normalise_pick,
    save_snapshot,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    for rel in [
        "src/routes/+page.svelte",
        "src/lib/Card.svelte",
        "src/components/Card.tsx",
        "src/with space/a b.tsx",
        "node_modules/pkg/index.js",
        "node_modules/.vite/deps/react.js",
        "README.md",
    ]:
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    (root / "src/dir").mkdir()
    outside = tmp_path / "outside.ts"
    outside.write_text("x")
    (root / "src/link-out.ts").symlink_to(outside)
    (root / "src/link-in.tsx").symlink_to(root / "src/components/Card.tsx")
    return root.resolve()


def case(raw: str, expected: str | None) -> tuple[str, str | None]:
    return raw, expected


CARD = "src/components/Card.tsx"

PATH_CASES = [
    # project-relative (Svelte)
    case("src/routes/+page.svelte", "src/routes/+page.svelte"),
    case("./src/routes/+page.svelte", "src/routes/+page.svelte"),
    case("src/routes/../lib/Card.svelte", "src/lib/Card.svelte"),
    case("src/routes/missing.svelte", None),
    case("src/dir", None),
    case("README.md", "README.md"),
    case("", None),
    case("   ", None),
    case("../outside.ts", None),
    case("src/../../outside.ts", None),
    # symlinks
    case("src/link-out.ts", None),
    case("src/link-in.tsx", CARD),
    # query strings and fragments on plain paths
    case("src/lib/Card.svelte?svelte&type=style&lang.css", "src/lib/Card.svelte"),
    case("src/lib/Card.svelte#frag", "src/lib/Card.svelte"),
    # root-relative URL paths
    case("/src/lib/Card.svelte", "src/lib/Card.svelte"),
    case("/src/components/Card.tsx?t=1700000000", CARD),
    case("/nope/x.tsx", None),
    # dev-server URLs
    case("http://localhost:5175/src/components/Card.tsx?t=123", CARD),
    case("http://localhost:5175/src/components/Card.tsx", CARD),
    case(
        "http://127.0.0.1:3000/src/lib/Card.svelte?svelte&type=script&lang.ts",
        "src/lib/Card.svelte",
    ),
    case("http://localhost:5173/src/components/Card.tsx#hash", CARD),
    case("http://localhost:5173/src/with%20space/a%20b.tsx?t=9", "src/with space/a b.tsx"),
    case("http://localhost:5173/src/missing.tsx?t=1", None),
    case("http://localhost:5173/", None),
    case("http://localhost:5173/node_modules/pkg/index.js", None),
    case("http://localhost:5173/node_modules/.vite/deps/react.js?v=abc", None),
    case("http://localhost:5173/src/../../etc/passwd", None),
    # Vite special forms
    case("http://localhost:5173/@id/__x00__virtual:foo", None),
    case("/@id/src/lib/Card.svelte", "src/lib/Card.svelte"),
    # node_modules
    case("node_modules/pkg/index.js", None),
    case("src/../node_modules/pkg/index.js", None),
]


@pytest.mark.parametrize(("raw", "expected"), PATH_CASES)
def test_normalise_path(repo: Path, raw: str, expected: str | None) -> None:
    assert normalise_path(raw, repo) == expected


def test_normalise_path_absolute_forms(repo: Path, tmp_path: Path) -> None:
    assert normalise_path(str(repo / CARD), repo) == CARD
    assert normalise_path(f"{repo}/src/lib/../lib/Card.svelte", repo) == "src/lib/Card.svelte"
    assert normalise_path(str(repo / "src/missing.tsx"), repo) is None
    assert normalise_path(str(tmp_path / "outside.ts"), repo) is None
    assert normalise_path(str(repo / "node_modules/pkg/index.js"), repo) is None
    assert normalise_path("/etc/passwd", repo) is None
    assert normalise_path(f"{repo}/{CARD}?t=5", repo) == CARD
    # Vite /@fs/ wraps an absolute filesystem path
    assert normalise_path(f"http://localhost:5173/@fs{repo}/{CARD}", repo) == CARD
    assert normalise_path(f"/@fs{tmp_path}/outside.ts", repo) is None
    # file:// URLs
    assert normalise_path(f"file://{repo}/{CARD}", repo) == CARD


def test_normalise_path_through_a_symlinked_repo_root(repo: Path, tmp_path: Path) -> None:
    alias = tmp_path / "alias"
    alias.symlink_to(repo)
    assert normalise_path(str(alias / CARD), alias) == CARD
    assert normalise_path(str(alias / CARD), repo) == CARD
    assert normalise_path(str(repo / CARD), alias) == CARD


@pytest.mark.parametrize(
    ("name", "version", "offset"),
    [
        ("svelte", "5", 1),
        ("svelte", None, 1),
        ("react", "19.3.0", 1),
        ("react", "19", 1),
        ("react", "20.0.0", 1),
        ("react", "18.3.1", 0),
        ("react", "17.0.2", 0),
        ("react", None, 0),
        ("react", "weird", 0),
        ("unknown", "19.0.0", 0),
    ],
)
def test_column_offset(name: str, version: str | None, offset: int) -> None:
    assert column_offset(name, version) == offset


def test_normalise_pick_svelte(repo: Path) -> None:
    pick = make_pick(
        file="src/lib/Card.svelte",
        line=14,
        column=2,
        chain=[
            {
                "name": "CardHeader",
                "type": "component",
                "file": "src/routes/+page.svelte",
                "line": 8,
                "column": 4,
            },
            {"name": "root", "type": "component", "file": "node_modules/@sveltejs/kit/root.svelte"},
            {
                "name": "each",
                "type": "each",
                "file": "src/lib/Missing.svelte",
                "line": 3,
                "column": 1,
            },
            {"name": "Anon"},
        ],
    )
    out = normalise_pick(pick, repo)
    assert out.framework.source is not None
    assert (out.framework.source.file, out.framework.source.line, out.framework.source.column) == (
        "src/lib/Card.svelte",
        14,
        3,
    )
    names = [c.name for c in out.framework.component_chain]
    assert names == ["CardHeader", "each", "Anon"]  # node_modules entry dropped
    first, unverified, anon = out.framework.component_chain
    assert (first.file, first.line, first.column) == ("src/routes/+page.svelte", 8, 5)
    assert (unverified.file, unverified.line, unverified.column) == (None, None, None)
    assert anon.file is None
    assert (
        pick.framework.source is not None and pick.framework.source.column == 2
    )  # input untouched


def test_normalise_pick_react19_url_and_react18_absolute(repo: Path) -> None:
    p19 = make_pick(
        file="http://localhost:5275/src/components/Card.tsx?t=17",
        framework="react",
        version="19.3.0",
        line=10,
        column=8,
        chain=[
            {
                "name": "Card",
                "file": "http://localhost:5275/src/components/Card.tsx?t=17",
                "line": 8,
                "column": 4,
            }
        ],
    )
    out = normalise_pick(p19, repo)
    assert out.framework.source is not None
    assert out.framework.source.file == CARD and out.framework.source.column == 9
    assert out.framework.component_chain[0].column == 5

    p18 = make_pick(
        file=str(repo / CARD),
        framework="react",
        version="18.3.1",
        line=10,
        column=9,
        chain=[{"name": "Card", "file": str(repo / CARD), "line": 8, "column": 5}],
    )
    out = normalise_pick(p18, repo)
    assert out.framework.source is not None
    assert out.framework.source.file == CARD and out.framework.source.column == 9
    assert out.framework.component_chain[0].column == 5


def test_normalise_pick_drops_unverifiable_source_but_keeps_chain(repo: Path) -> None:
    pick = make_pick(
        file="/somewhere/else/Card.tsx",
        framework="react",
        version="18.3.1",
        chain=[{"name": "Card", "file": "/somewhere/else/Card.tsx", "line": 1, "column": 1}],
    )
    out = normalise_pick(pick, repo)
    assert out.framework.source is None
    assert [(c.name, c.file) for c in out.framework.component_chain] == [("Card", None)]


def test_normalise_pick_unknown_framework_and_region(repo: Path) -> None:
    pick = make_pick(kind="region", file=None, framework="unknown", version=None, chain=[])
    out = normalise_pick(pick, repo)
    assert out.framework.source is None and out.region is not None
    assert out.model_dump() == pick.model_dump()


# --- store ---------------------------------------------------------------------------


def test_store_add_get_remove_clear() -> None:
    store = PickStore()
    store.add(make_pick(1))
    store.add(make_pick(2))
    assert len(store) == 2 and 1 in store
    assert store.get(2) is not None and store.get(3) is None
    assert store.remove(1) and not store.remove(1)
    store.clear()
    assert len(store) == 0


def test_store_newest_wins_on_duplicate_id() -> None:
    store = PickStore()
    store.add(make_pick(1, line=10))
    store.add(make_pick(1, line=99))
    assert len(store) == 1
    got = store.get(1)
    assert got is not None and got.framework.source is not None
    assert got.framework.source.line == 99


def test_store_cap_evicts_oldest() -> None:
    store = PickStore(cap=3)
    for i in range(1, 6):
        store.add(make_pick(i))
    assert [i for i in range(1, 6) if i in store] == [3, 4, 5]
    store.add(make_pick(3))  # refreshed: now the newest
    store.add(make_pick(6))
    assert [i for i in range(1, 7) if i in store] == [3, 5, 6]


def test_store_default_cap_is_100() -> None:
    store = PickStore()
    for i in range(150):
        store.add(make_pick(i))
    assert len(store) == 100 and 49 not in store and 50 in store


def test_store_resolve_marks_unknown_and_dedupes() -> None:
    store = PickStore()
    store.add(make_pick(2))
    out = store.resolve([2, 7, 2, 7])
    assert [o if isinstance(o, int) else o.id for o in out] == [2, 7]
    assert isinstance(out[1], int)


# --- snapshots -----------------------------------------------------------------------


def test_snapshot_round_trip(tmp_path: Path) -> None:
    picks = [
        make_pick(1, repeat=(12, 2), screenshots=("a" * 32, "b" * 32)),
        make_pick(2, kind="region", file=None),
    ]
    d = tmp_path / "tasks" / "t1"
    save_snapshot(d, picks)
    assert load_snapshot(d) == picks
    assert load_snapshot(tmp_path / "tasks" / "nope") == []
    (d / "picks.json").write_text("not json")
    assert load_snapshot(d) == []


def test_snapshot_keeps_wire_names(tmp_path: Path) -> None:
    save_snapshot(tmp_path, [make_pick(1)])
    text = (tmp_path / "picks.json").read_text()
    assert '"outerHTML"' in text and '"componentChain"' in text
    assert os.path.isfile(tmp_path / "picks.json")
