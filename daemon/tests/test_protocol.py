import json

import pytest
from pydantic import ValidationError

from sidecall.protocol.export import build_schema
from sidecall.protocol.messages import (
    AgentTextDelta,
    AgentTextDeltaPayload,
    Hello,
    TaskBlocked,
    TaskBlockedPayload,
    UserText,
    UserTextPayload,
    parse_client,
    parse_server,
)
from sidecall.protocol.models import Approval, Brief, PageContext, Target, Task, Viewport


def test_client_message_round_trip_uses_camel_case_on_the_wire() -> None:
    msg = UserText(payload=UserTextPayload(text="make [1] red", pick_ids=[1]))
    wire = msg.model_dump_json()
    data = json.loads(wire)
    assert data["type"] == "user.text"
    assert data["payload"] == {"text": "make [1] red", "pickIds": [1], "page": None}
    assert set(data) == {"type", "id", "ts", "payload"}
    parsed = parse_client(wire)
    assert isinstance(parsed, UserText)
    assert parsed == msg


def test_parse_client_dispatches_on_type() -> None:
    raw = json.dumps(
        {
            "type": "hello",
            "id": "a1",
            "ts": 1.0,
            "payload": {"token": "t", "extVersion": "0.1.0", "origin": None, "clockSample": 5.0},
        }
    )
    msg = parse_client(raw)
    assert isinstance(msg, Hello)
    assert msg.payload.clock_sample == 5.0


def test_server_message_round_trip() -> None:
    approval = Approval(id="ap1", task_id="t1", command="pnpm add zod", reason="package install")
    msg = TaskBlocked(payload=TaskBlockedPayload(task_id="t1", approval=approval))
    parsed = parse_server(msg.model_dump_json())
    assert isinstance(parsed, TaskBlocked)
    assert parsed.payload.approval.command == "pnpm add zod"

    delta = AgentTextDelta(payload=AgentTextDeltaPayload(turn_id="u1", delta="Sure"))
    assert parse_server(delta.model_dump_json()) == delta


def test_unknown_type_and_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_client(json.dumps({"type": "nope", "id": "x", "ts": 0, "payload": {}}))
    with pytest.raises(ValidationError):
        parse_client(
            json.dumps(
                {"type": "user.text", "id": "x", "ts": 0, "payload": {"text": "a", "bogus": 1}}
            )
        )


def test_brief_defaults() -> None:
    brief = Brief(
        title="Red heading",
        goal="Make the home page heading red",
        targets=[Target(description="h1 on /")],
        changes=["color: red"],
        acceptance_criteria=["h1 is red"],
    )
    assert brief.out_of_scope == []
    assert brief.notes is None


def test_schema_has_both_unions_and_required_envelope_fields() -> None:
    schema = build_schema()
    assert set(schema["properties"]) == {"client", "server"}
    hello = schema["$defs"]["Hello"]
    assert set(hello["required"]) == {"type", "id", "ts", "payload"}
    assert "outerHTML" in schema["$defs"]["ElementInfo"]["properties"]


PAGE_WIRE = {
    "url": "http://localhost:5173/settings?tab=a#top",
    "route": "/settings?tab=a#top",
    "title": "Settings",
    "viewport": {"w": 1440, "h": 900, "dpr": 2.0},
    "screenshot": "0123456789abcdef0123456789abcdef",
}


def test_user_text_with_page_round_trip() -> None:
    raw = json.dumps(
        {
            "type": "user.text",
            "id": "x",
            "ts": 0,
            "payload": {"text": "magenta", "pickIds": [], "page": PAGE_WIRE},
        }
    )
    msg = parse_client(raw)
    assert isinstance(msg, UserText)
    page = msg.payload.page
    assert page is not None
    assert page.viewport == Viewport(w=1440, h=900, dpr=2.0)
    assert page.screenshot == PAGE_WIRE["screenshot"]
    assert json.loads(msg.model_dump_json())["payload"]["page"] == PAGE_WIRE
    # page is optional on the wire and the screenshot may be null
    no_page = parse_client(raw.replace(', "page": ' + json.dumps(PAGE_WIRE), ""))
    assert isinstance(no_page, UserText) and no_page.payload.page is None
    shot_less = {**PAGE_WIRE, "screenshot": None}
    assert PageContext.model_validate(shot_less).screenshot is None


def test_task_carries_page() -> None:
    task = Task(
        id="t1",
        title="Magenta",
        brief=Brief(title="M", goal="g", targets=[], changes=[], acceptance_criteria=[]),
        created_at=1.0,
        page=PageContext.model_validate(PAGE_WIRE),
    )
    data = json.loads(task.model_dump_json())
    assert data["page"] == PAGE_WIRE
    assert Task.model_validate_json(task.model_dump_json()) == task
    bare = Task(id="t2", title="x", brief=task.brief, created_at=1.0)
    assert json.loads(bare.model_dump_json())["page"] is None


def test_call_page_round_trip() -> None:
    from sidecall.protocol.messages import CallPage

    raw = json.dumps({"type": "call.page", "id": "x", "ts": 0, "payload": {"page": PAGE_WIRE}})
    msg = parse_client(raw)
    assert isinstance(msg, CallPage)
    assert json.loads(msg.model_dump_json())["payload"]["page"] == PAGE_WIRE
