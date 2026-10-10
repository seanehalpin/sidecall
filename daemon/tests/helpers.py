from __future__ import annotations

import itertools
from typing import Any

from sidecall.protocol.models import Brief, Pick, Target, Task

_ids = itertools.count(1)


def make_brief(title: str = "Red heading") -> Brief:
    return Brief(
        title=title,
        goal=f"Goal for {title}",
        targets=[Target(file="src/routes/+page.svelte", line=10, description="the h1")],
        changes=["make it red"],
        acceptance_criteria=["the h1 is red"],
    )


def make_task(title: str = "Task", task_id: str | None = None) -> Task:
    n = next(_ids)
    return Task(id=task_id or f"t{n}", title=title, brief=make_brief(title), created_at=float(n))


def make_pick(
    pick_id: int = 1,
    *,
    kind: str = "element",
    file: str | None = "src/lib/Card.svelte",
    line: int = 14,
    column: int = 3,
    framework: str = "svelte",
    version: str | None = "5",
    repeat: tuple[int, int] | None = None,
    tag: str = "div",
    classes: str = "card-header",
    styles: dict[str, str] | None = None,
    chain: list[dict[str, Any]] | None = None,
    screenshots: tuple[str, str] | None = None,
) -> Pick:
    """A pick as the extension would send it (raw: not yet normalised)."""
    page: dict[str, Any] = {
        "url": "http://localhost:5173/cards",
        "route": "/cards",
        "title": "Cards",
        "viewport": {"w": 1280, "h": 800, "dpr": 2},
        "scroll": {"x": 0, "y": 0},
        "colorScheme": "light",
        "consoleErrors": [],
    }
    data: dict[str, Any] = {
        "id": pick_id,
        "createdAt": 1000.0 + pick_id,
        "kind": kind,
        "page": page,
        "framework": {
            "name": framework,
            "version": version,
            "source": (
                {"file": file, "line": line, "column": column, "precision": "element"}
                if file
                else None
            ),
            "componentChain": chain
            if chain is not None
            else [
                {"name": "CardHeader", "type": "component", "file": file, "line": 8, "column": 2}
            ],
            "props": {"title": "Hello"},
        },
        "repeat": {"count": repeat[0], "index": repeat[1]} if repeat else None,
        "screenshots": {"crop": screenshots[0], "viewport": screenshots[1]}
        if screenshots
        else None,
    }
    if kind == "element":
        data["element"] = {
            "selector": f"article > {tag}.{classes}",
            "altSelectors": ["[data-testid=x]"],
            "tag": tag,
            "attributes": {"class": classes, "data-testid": "x"},
            "text": "Hello",
            "outerHTML": f'<{tag} class="{classes}">Hello</{tag}>',
            "rect": {"x": 10, "y": 20, "w": 300, "h": 40},
            "computedStyles": styles
            if styles is not None
            else {"padding": "12px 16px", "display": "block", "box-shadow": "0 1px 2px #000"},
        }
    else:
        data["region"] = {
            "rect": {"x": 40, "y": 120, "w": 320, "h": 180},
            "elements": [
                {
                    "selector": f"main > button.btn-{i}",
                    "tag": "button",
                    "text": f"Button number {i} with a rather long label text",
                    "rect": {"x": 0, "y": 0, "w": 10, "h": 10},
                }
                for i in range(7)
            ],
        }
    return Pick.model_validate(data)
