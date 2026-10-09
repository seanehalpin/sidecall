"""Approval broker (SPEC §9.2 Approvals): park a worker step until the user answers."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from .protocol.messages import (
    ApprovalResolved,
    ApprovalResolvedPayload,
    Notify,
    NotifyPayload,
    ServerMessage,
    TaskBlocked,
    TaskBlockedPayload,
)
from .protocol.models import Approval

Emit = Callable[[ServerMessage], None]


@dataclass
class _Pending:
    approval: Approval
    future: asyncio.Future[bool]


class ApprovalBroker:
    """One per repo. `request` blocks (no timeout) until `respond` or `cancel_task`.

    `on_blocked(task_id)` / `on_unblocked(task_id)` let the owner update task status;
    they run before the corresponding messages are emitted.
    """

    def __init__(
        self,
        emit: Emit,
        on_blocked: Callable[[str], None] | None = None,
        on_unblocked: Callable[[str], None] | None = None,
    ) -> None:
        self._emit = emit
        self._on_blocked = on_blocked
        self._on_unblocked = on_unblocked
        self._pending: dict[str, _Pending] = {}

    def pending(self) -> list[Approval]:
        return [p.approval for p in self._pending.values()]

    def has(self, approval_id: str) -> bool:
        return approval_id in self._pending

    async def request(self, task_id: str, command: str, reason: str) -> bool:
        approval = Approval(
            id=uuid.uuid4().hex[:12], task_id=task_id, command=command, reason=reason
        )
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[approval.id] = _Pending(approval, future)
        if self._on_blocked:
            self._on_blocked(task_id)
        self._emit(TaskBlocked(payload=TaskBlockedPayload(task_id=task_id, approval=approval)))
        self._emit(
            Notify(
                payload=NotifyPayload(title="Sidecall needs your OK", body=reason, action="open")
            )
        )
        try:
            return await future
        finally:
            self._pending.pop(approval.id, None)

    def respond(self, approval_id: str, allow: bool) -> bool:
        """Resolve a pending approval. Returns False if the id is unknown."""
        pending = self._pending.pop(approval_id, None)
        if pending is None or pending.future.done():
            return False
        self._resolve(pending, allow)
        return True

    def cancel_task(self, task_id: str) -> None:
        """Deny every pending approval of a task (used when the task is cancelled)."""
        for approval_id, pending in list(self._pending.items()):
            if pending.approval.task_id == task_id:
                del self._pending[approval_id]
                if not pending.future.done():
                    self._resolve(pending, False)

    def _resolve(self, pending: _Pending, allow: bool) -> None:
        pending.future.set_result(allow)
        if self._on_unblocked:
            self._on_unblocked(pending.approval.task_id)
        self._emit(
            ApprovalResolved(payload=ApprovalResolvedPayload(id=pending.approval.id, allow=allow))
        )
