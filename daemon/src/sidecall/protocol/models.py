"""Shared data models for the protocol (SPEC §5.3, §6.4, §9).

These Pydantic models are the source of truth. Field names are snake_case in
Python and camelCase on the wire.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class Model(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
        extra="forbid",
        # Serialised messages always carry defaulted fields, so the generated
        # TypeScript types should mark them required.
        json_schema_serialization_defaults_required=True,
    )


class Empty(Model):
    pass


# --- Brief (SPEC §6.4) -------------------------------------------------------


class Target(Model):
    pick_id: int | None = None
    file: str | None = None
    line: int | None = None
    component: str | None = None
    description: str


class Brief(Model):
    title: str
    goal: str
    targets: list[Target]
    changes: list[str]
    acceptance_criteria: list[str]
    out_of_scope: list[str] = Field(default_factory=list[str])
    notes: str | None = None


# --- Page context (attached to every user turn) --------------------------------


class Viewport(Model):
    w: int
    h: int
    dpr: float


class PageContext(Model):
    """The page the user was looking at when they sent a turn."""

    url: str
    route: str
    """pathname + search + hash"""
    title: str
    viewport: Viewport
    screenshot: str | None = None
    """Upload ref (POST /api/upload) of the visible viewport, or None."""


# --- Tasks and approvals ------------------------------------------------------

TaskStatus = Literal["queued", "running", "blocked", "completed", "failed", "cancelled"]


class Approval(Model):
    id: str
    task_id: str
    command: str
    reason: str


class Task(Model):
    id: str
    title: str
    brief: Brief
    status: TaskStatus = "queued"
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    summary: str | None = None
    files: list[str] = Field(default_factory=list[str])
    error: str | None = None
    last_event: str | None = None
    # Picks (SPEC §5.3) this task was dispatched with: the turn's picks plus any the brief targets.
    pick_ids: list[int] = Field(default_factory=list[int])
    page: PageContext | None = None
    """The page of the user turn that dispatched this task."""


# --- Transcript ---------------------------------------------------------------

Role = Literal["user", "agent", "system"]


class TranscriptEntry(Model):
    turn_id: str
    role: Role
    text: str
    pick_ids: list[int] = Field(default_factory=list[int])
    ts: float
    task_id: str | None = None


# --- Picks (SPEC §5.3; produced from Milestone 2) -----------------------------


class Rect(Model):
    x: float
    y: float
    w: float
    h: float


class Scroll(Model):
    x: float
    y: float


class PageInfo(Model):
    url: str
    route: str
    title: str
    viewport: Viewport
    scroll: Scroll
    color_scheme: Literal["light", "dark"]
    console_errors: list[str] = Field(default_factory=list[str])


class ElementSummary(Model):
    selector: str
    tag: str
    text: str
    rect: Rect


class ElementInfo(Model):
    selector: str
    alt_selectors: list[str] = Field(default_factory=list[str])
    tag: str
    attributes: dict[str, str] = Field(default_factory=dict[str, str])
    text: str
    outer_html: str = Field(alias="outerHTML")
    rect: Rect
    computed_styles: dict[str, str] = Field(default_factory=dict[str, str])


class RegionInfo(Model):
    rect: Rect
    elements: list[ElementSummary]


class SourceLocation(Model):
    file: str
    line: int
    column: int
    precision: Literal["element", "component"]


class ChainEntry(Model):
    name: str
    type: str | None = None
    file: str | None = None
    line: int | None = None
    column: int | None = None


class FrameworkInfo(Model):
    name: Literal["svelte", "react", "unknown"]
    version: str | None = None
    source: SourceLocation | None = None
    component_chain: list[ChainEntry] = Field(default_factory=list[ChainEntry])
    props: Any = None


class Repeat(Model):
    count: int
    index: int


class Screenshots(Model):
    crop: str
    viewport: str


class Pick(Model):
    id: int
    created_at: float
    kind: Literal["element", "region"]
    page: PageInfo
    element: ElementInfo | None = None
    region: RegionInfo | None = None
    framework: FrameworkInfo
    repeat: Repeat | None = None
    screenshots: Screenshots | None = None


# --- Repo and state -----------------------------------------------------------

Preset = Literal["cautious", "default", "permissive"]


class RepoInfo(Model):
    name: str
    path: str
    origin: str


class Settings(Model):
    preset: Preset = "default"


class RepoState(Model):
    tasks: list[Task] = Field(default_factory=list[Task])
    approvals: list[Approval] = Field(default_factory=list[Approval])
    transcript: list[TranscriptEntry] = Field(default_factory=list[TranscriptEntry])
    settings: Settings = Field(default_factory=Settings)


CallState = Literal["idle", "connecting", "listening", "thinking", "speaking"]
