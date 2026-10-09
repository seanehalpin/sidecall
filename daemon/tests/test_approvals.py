from __future__ import annotations

import asyncio

from sidecall.approvals import ApprovalBroker
from sidecall.protocol.messages import ApprovalResolved, Notify, ServerMessage, TaskBlocked


async def test_request_and_respond() -> None:
    sent: list[ServerMessage] = []
    events: list[str] = []
    broker = ApprovalBroker(
        sent.append,
        on_blocked=lambda t: events.append(f"blocked {t}"),
        on_unblocked=lambda t: events.append(f"unblocked {t}"),
    )
    waiter = asyncio.create_task(broker.request("t1", "pnpm add zod", "package install"))
    await asyncio.sleep(0)
    assert [type(m) for m in sent] == [TaskBlocked, Notify]
    blocked = sent[0]
    assert isinstance(blocked, TaskBlocked)
    approval = blocked.payload.approval
    assert broker.pending() == [approval]
    assert approval.command == "pnpm add zod"
    assert not waiter.done()

    assert broker.respond(approval.id, True)
    assert await waiter is True
    assert isinstance(sent[-1], ApprovalResolved) and sent[-1].payload.allow is True
    assert broker.pending() == []
    assert events == ["blocked t1", "unblocked t1"]
    assert not broker.respond(approval.id, True)  # already resolved


async def test_deny_and_unknown() -> None:
    sent: list[ServerMessage] = []
    broker = ApprovalBroker(sent.append)
    waiter = asyncio.create_task(broker.request("t1", "brew install jq", "package install"))
    await asyncio.sleep(0)
    approval = broker.pending()[0]
    assert not broker.respond("nope", True)
    broker.respond(approval.id, False)
    assert await waiter is False


async def test_cancel_task_denies_its_approvals() -> None:
    sent: list[ServerMessage] = []
    broker = ApprovalBroker(sent.append)
    a = asyncio.create_task(broker.request("t1", "x", "r"))
    b = asyncio.create_task(broker.request("t2", "y", "r"))
    await asyncio.sleep(0)
    broker.cancel_task("t1")
    assert await a is False
    assert [p.task_id for p in broker.pending()] == ["t2"]
    broker.cancel_task("t2")
    assert await b is False


async def test_no_timeout() -> None:
    broker = ApprovalBroker(lambda m: None)
    waiter = asyncio.create_task(broker.request("t1", "x", "r"))
    await asyncio.sleep(0.05)
    assert not waiter.done()
    waiter.cancel()
