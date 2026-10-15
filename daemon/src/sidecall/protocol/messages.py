"""WebSocket message envelopes (SPEC §9).

Every message is ``{type, id, ts, payload}``. ``ClientMessage`` is everything the
extension sends; ``ServerMessage`` is everything the daemon sends. Both are
discriminated on ``type``.
"""

from __future__ import annotations

import time
import uuid
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter

from .models import (
    Approval,
    CallState,
    Costs,
    Empty,
    Model,
    PageContext,
    Pick,
    Preset,
    RepoInfo,
    RepoState,
    Role,
    Settings,
    Task,
    WorkerModel,
)


def _id() -> str:
    return uuid.uuid4().hex[:12]


def _now_ms() -> float:
    return time.time() * 1000


class Envelope(Model):
    id: str = Field(default_factory=_id)
    ts: float = Field(default_factory=_now_ms)


# --- Extension -> daemon payloads ---------------------------------------------


class HelloPayload(Model):
    token: str
    ext_version: str
    origin: str | None = None
    clock_sample: float


class PairRequestPayload(Model):
    code: str


class RepoMapPayload(Model):
    origin: str
    path: str


class RepoBrowsePayload(Model):
    origin: str


class PickRemovePayload(Model):
    id: int


class UserTextPayload(Model):
    text: str
    pick_ids: list[int] = Field(default_factory=list[int])
    page: PageContext | None = None


class CallMutePayload(Model):
    muted: bool


class CallPagePayload(Model):
    page: PageContext


class TaskCancelPayload(Model):
    id: str


class TaskReorderPayload(Model):
    ids: list[str]


class ApprovalRespondPayload(Model):
    id: str
    allow: bool


class SettingsUpdatePayload(Model):
    preset: Preset | None = None
    clear_chat_daily: bool | None = None
    worker_model: WorkerModel | None = None


class WebRTCOfferPayload(Model):
    sdp: str


class WebRTCIcePayload(Model):
    candidate: dict[str, Any] | None


class SeqPayload(Model):
    seq: int


# --- Extension -> daemon messages ---------------------------------------------


class Hello(Envelope):
    type: Literal["hello"] = "hello"
    payload: HelloPayload


class PairRequest(Envelope):
    type: Literal["pair.request"] = "pair.request"
    payload: PairRequestPayload


class RepoMap(Envelope):
    type: Literal["repo.map"] = "repo.map"
    payload: RepoMapPayload


class RepoBrowse(Envelope):
    type: Literal["repo.browse"] = "repo.browse"
    payload: RepoBrowsePayload


class PickAdd(Envelope):
    type: Literal["pick.add"] = "pick.add"
    payload: Pick


class PickRemove(Envelope):
    type: Literal["pick.remove"] = "pick.remove"
    payload: PickRemovePayload


class ChatClear(Envelope):
    """Empty the chat and start the typed-chat talker on a fresh session."""

    type: Literal["chat.clear"] = "chat.clear"
    payload: Empty = Field(default_factory=Empty)


class PicksClear(Envelope):
    type: Literal["picks.clear"] = "picks.clear"
    payload: Empty = Field(default_factory=Empty)


class UserText(Envelope):
    type: Literal["user.text"] = "user.text"
    payload: UserTextPayload


class CallStart(Envelope):
    type: Literal["call.start"] = "call.start"
    payload: Empty = Field(default_factory=Empty)


class CallEnd(Envelope):
    type: Literal["call.end"] = "call.end"
    payload: Empty = Field(default_factory=Empty)


class CallMute(Envelope):
    type: Literal["call.mute"] = "call.mute"
    payload: CallMutePayload


class CallPage(Envelope):
    """The page in view during a call; spoken turns use the latest one (SPEC §5.6)."""

    type: Literal["call.page"] = "call.page"
    payload: CallPagePayload


class TaskCancel(Envelope):
    type: Literal["task.cancel"] = "task.cancel"
    payload: TaskCancelPayload


class TaskReorder(Envelope):
    type: Literal["task.reorder"] = "task.reorder"
    payload: TaskReorderPayload


class ApprovalRespond(Envelope):
    type: Literal["approval.respond"] = "approval.respond"
    payload: ApprovalRespondPayload


class SettingsUpdate(Envelope):
    type: Literal["settings.update"] = "settings.update"
    payload: SettingsUpdatePayload


class WebRTCOffer(Envelope):
    type: Literal["webrtc.offer"] = "webrtc.offer"
    payload: WebRTCOfferPayload


class WebRTCIceIn(Envelope):
    type: Literal["webrtc.ice"] = "webrtc.ice"
    payload: WebRTCIcePayload


class Pong(Envelope):
    type: Literal["pong"] = "pong"
    payload: SeqPayload


ClientMessage = Annotated[
    Hello
    | PairRequest
    | RepoMap
    | RepoBrowse
    | PickAdd
    | PickRemove
    | PicksClear
    | ChatClear
    | UserText
    | CallStart
    | CallEnd
    | CallMute
    | CallPage
    | TaskCancel
    | TaskReorder
    | ApprovalRespond
    | SettingsUpdate
    | WebRTCOffer
    | WebRTCIceIn
    | Pong,
    Field(discriminator="type"),
]


# --- Daemon -> extension payloads ---------------------------------------------


class WelcomePayload(Model):
    repo: RepoInfo
    state: RepoState
    clock_offset: float


class PairResultPayload(Model):
    token: str


class RepoUnknownPayload(Model):
    origin: str


class CallStatePayload(Model):
    state: CallState


class TranscriptPartialPayload(Model):
    text: str


class TranscriptFinalPayload(Model):
    turn_id: str
    role: Role
    text: str
    pick_ids: list[int] = Field(default_factory=list[int])
    task_id: str | None = None


class AgentTextDeltaPayload(Model):
    turn_id: str
    delta: str


class AgentTextDonePayload(Model):
    turn_id: str


TaskEventKind = Literal["status", "tool", "text", "edit"]


class TaskEventPayload(Model):
    task_id: str
    kind: TaskEventKind
    text: str


class TaskCompletedPayload(Model):
    task_id: str
    summary: str
    files: list[str]


class TaskFailedPayload(Model):
    task_id: str
    reason: str


class TaskBlockedPayload(Model):
    task_id: str
    approval: Approval


class ApprovalResolvedPayload(Model):
    id: str
    allow: bool


class QueueStatePayload(Model):
    tasks: list[Task]


class NotifyPayload(Model):
    title: str
    body: str
    action: Literal["open", "call"] | None = None


class WebRTCAnswerPayload(Model):
    sdp: str


class ErrorPayload(Model):
    code: str
    message: str


# --- Daemon -> extension messages ---------------------------------------------


class Welcome(Envelope):
    type: Literal["welcome"] = "welcome"
    payload: WelcomePayload


class PairResult(Envelope):
    type: Literal["pair.result"] = "pair.result"
    payload: PairResultPayload


class RepoUnknown(Envelope):
    type: Literal["repo.unknown"] = "repo.unknown"
    payload: RepoUnknownPayload


class CallStateMsg(Envelope):
    type: Literal["call.state"] = "call.state"
    payload: CallStatePayload


class TranscriptPartial(Envelope):
    type: Literal["transcript.partial"] = "transcript.partial"
    payload: TranscriptPartialPayload


class TranscriptFinal(Envelope):
    type: Literal["transcript.final"] = "transcript.final"
    payload: TranscriptFinalPayload


class AgentTextDelta(Envelope):
    type: Literal["agent.text.delta"] = "agent.text.delta"
    payload: AgentTextDeltaPayload


class AgentTextDone(Envelope):
    type: Literal["agent.text.done"] = "agent.text.done"
    payload: AgentTextDonePayload


class TaskCreated(Envelope):
    type: Literal["task.created"] = "task.created"
    payload: Task


class TaskEvent(Envelope):
    type: Literal["task.event"] = "task.event"
    payload: TaskEventPayload


class TaskCompleted(Envelope):
    type: Literal["task.completed"] = "task.completed"
    payload: TaskCompletedPayload


class TaskFailed(Envelope):
    type: Literal["task.failed"] = "task.failed"
    payload: TaskFailedPayload


class TaskBlocked(Envelope):
    type: Literal["task.blocked"] = "task.blocked"
    payload: TaskBlockedPayload


class ApprovalResolved(Envelope):
    type: Literal["approval.resolved"] = "approval.resolved"
    payload: ApprovalResolvedPayload


class QueueState(Envelope):
    type: Literal["queue.state"] = "queue.state"
    payload: QueueStatePayload


class Notify(Envelope):
    type: Literal["notify"] = "notify"
    payload: NotifyPayload


class WebRTCAnswer(Envelope):
    type: Literal["webrtc.answer"] = "webrtc.answer"
    payload: WebRTCAnswerPayload


class WebRTCIceOut(Envelope):
    type: Literal["webrtc.ice"] = "webrtc.ice"
    payload: WebRTCIcePayload


class ErrorMsg(Envelope):
    type: Literal["error"] = "error"
    payload: ErrorPayload


class ChatCleared(Envelope):
    type: Literal["chat.cleared"] = "chat.cleared"
    payload: Empty = Field(default_factory=Empty)


class SettingsState(Envelope):
    type: Literal["settings.state"] = "settings.state"
    payload: Settings


class CostsState(Envelope):
    type: Literal["costs.state"] = "costs.state"
    payload: Costs


class Ping(Envelope):
    type: Literal["ping"] = "ping"
    payload: SeqPayload


ServerMessage = Annotated[
    Welcome
    | PairResult
    | RepoUnknown
    | CallStateMsg
    | TranscriptPartial
    | TranscriptFinal
    | AgentTextDelta
    | AgentTextDone
    | TaskCreated
    | TaskEvent
    | TaskCompleted
    | TaskFailed
    | TaskBlocked
    | ApprovalResolved
    | QueueState
    | Notify
    | WebRTCAnswer
    | WebRTCIceOut
    | ErrorMsg
    | CostsState
    | ChatCleared
    | SettingsState
    | Ping,
    Field(discriminator="type"),
]

client_message_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)
server_message_adapter: TypeAdapter[ServerMessage] = TypeAdapter(ServerMessage)


def parse_client(raw: str | bytes) -> ClientMessage:
    return client_message_adapter.validate_json(raw)


def parse_server(raw: str | bytes) -> ServerMessage:
    return server_message_adapter.validate_json(raw)
