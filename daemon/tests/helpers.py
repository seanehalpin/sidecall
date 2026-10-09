from __future__ import annotations

import itertools

from sidecall.protocol.models import Brief, Target, Task

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
