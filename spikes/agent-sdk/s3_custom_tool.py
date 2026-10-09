"""3. In-process SDK MCP tool `dispatch_task(brief)` with a Pydantic-derived schema.

Run A: tool listed in allowed_tools as mcp__sidecall__dispatch_task (no prompt), and the
       server config carries "alwaysLoad": True so the tool is not deferred behind ToolSearch.
Run B: tool NOT in allowed_tools, default (deferred) loading -> can_use_tool consulted,
       model must call ToolSearch first.
Run C: env ENABLE_TOOL_SEARCH=false instead of alwaysLoad.
"""

import asyncio
import json
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    ToolPermissionContext,
    create_sdk_mcp_server,
    tool,
)
from pydantic import BaseModel

from common import CHEAP_MODEL, check_env, drain, log, make_scratch_repo


class Target(BaseModel):
    pick_id: int | None = None
    file: str | None = None
    line: int | None = None
    component: str | None = None
    description: str


class Brief(BaseModel):
    title: str
    goal: str
    targets: list[Target]
    changes: list[str]
    acceptance_criteria: list[str]
    out_of_scope: list[str] = []
    notes: str | None = None


# Full JSON Schema (with "type"+"properties") is passed through verbatim by the SDK.
# GOTCHA: Pydantic emits "$defs" + "$ref": "#/$defs/Target". Nesting that schema under a
# property breaks the refs (CLI-side validation fails with PointerToNowhere), so hoist
# $defs to the root of the tool schema.
_brief_schema = Brief.model_json_schema()
_defs = _brief_schema.pop("$defs", {})
BRIEF_TOOL_SCHEMA = {
    "type": "object",
    "properties": {"brief": _brief_schema},
    "required": ["brief"],
    "$defs": _defs,
}

received: list[dict[str, Any]] = []


@tool("dispatch_task", "Queue a coding task for the worker. Returns a task id.", BRIEF_TOOL_SCHEMA)
async def dispatch_task(args: dict[str, Any]) -> dict[str, Any]:
    brief = Brief.model_validate(args["brief"])  # validates in-process
    received.append(args)
    log("  IN-PROCESS dispatch_task called; parsed Brief:", brief.model_dump_json()[:400])
    return {"content": [{"type": "text", "text": json.dumps({"task_id": "t-001"})}]}


@tool("list_tasks", "List queued tasks", {})
async def list_tasks(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "[]"}]}


server = create_sdk_mcp_server(name="sidecall", version="0.0.1", tools=[dispatch_task, list_tasks])


async def can_use_tool(tool_name: str, inp: dict[str, Any], ctx: ToolPermissionContext):
    log(f"  can_use_tool consulted for {tool_name} (reason={ctx.decision_reason!r})")
    return PermissionResultAllow()


PROMPT = (
    "Dispatch a task with dispatch_task: title 'Card spacing', goal 'Match card padding to sidebar', "
    "one target (pick_id 1, file src/card.css, line 2, description 'card padding'), "
    "changes ['padding 16px 24px'], acceptance_criteria ['padding is 16px 24px']. "
    "Then tell me the task id in one sentence."
)


async def run(
    label: str, allowed: list[str], always_load: bool = False, env: dict[str, str] | None = None
) -> None:
    log(f"===== {label}: allowed_tools={allowed} alwaysLoad={always_load} env={env}")
    srv: dict[str, Any] = dict(server)
    if always_load:
        srv["alwaysLoad"] = True  # extra keys (minus "instance") are forwarded to the CLI
    received.clear()
    opts = ClaudeAgentOptions(
        cwd=make_scratch_repo(),
        model=CHEAP_MODEL,
        setting_sources=[],
        mcp_servers={"sidecall": srv},  # type: ignore[dict-item]
        env=env or {},
        allowed_tools=allowed,
        can_use_tool=can_use_tool,
        max_turns=4,
    )
    async with ClaudeSDKClient(options=opts) as client:
        await client.query(PROMPT)
        await drain(client)
    log(f"  handler invocations: {len(received)}")


async def main() -> None:
    check_env()
    allowed = ["mcp__sidecall__dispatch_task", "mcp__sidecall__list_tasks"]
    await run("A", allowed, always_load=True)
    await run("B", [])
    await run("C", allowed, env={"ENABLE_TOOL_SEARCH": "false"})


asyncio.run(main())
