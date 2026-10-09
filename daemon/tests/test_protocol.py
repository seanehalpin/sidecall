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
from sidecall.protocol.models import Approval, Brief, Target


def test_client_message_round_trip_uses_camel_case_on_the_wire() -> None:
    msg = UserText(payload=UserTextPayload(text="make [1] red", pick_ids=[1]))
    wire = msg.model_dump_json()
    data = json.loads(wire)
    assert data["type"] == "user.text"
    assert data["payload"] == {"text": "make [1] red", "pickIds": [1]}
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
