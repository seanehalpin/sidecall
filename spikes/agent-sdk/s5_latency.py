"""5. Connect time + first-token latency on a persistent ClaudeSDKClient.

Usage: uv run python s5_latency.py [variant ...]
Variants: haiku, default, talker-default, talker-haiku (default: all)
"""

import asyncio
import statistics
import sys
import time
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
)

from common import check_env, log, make_scratch_repo

TURNS = 6
TALKER_PROMPT = (
    "You are a voice assistant for prototyping. Output is spoken: short sentences, "
    "no markdown, natural contractions."
)


def variant_opts(name: str) -> ClaudeAgentOptions:
    base: dict[str, Any] = dict(
        cwd=make_scratch_repo(), setting_sources=[], include_partial_messages=True, max_turns=3
    )
    if name == "haiku":
        return ClaudeAgentOptions(model="haiku", **base)
    if name == "default":
        return ClaudeAgentOptions(**base)  # CLI default model, full Claude Code preset
    talker: dict[str, Any] = dict(
        tools=["Read", "Grep", "Glob"],
        allowed_tools=["Read", "Grep", "Glob"],
        disallowed_tools=["Edit", "Write", "Bash"],
        system_prompt=TALKER_PROMPT,
        thinking={"type": "disabled"},
    )
    if name == "talker-default":
        return ClaudeAgentOptions(**base, **talker)
    if name == "talker-haiku":
        return ClaudeAgentOptions(model="haiku", **base, **talker)
    if name == "talker-sonnet":
        return ClaudeAgentOptions(model="sonnet", **base, **talker)
    raise SystemExit(f"unknown variant {name}")


async def run_variant(name: str) -> dict[str, Any]:
    log(f"===== variant {name}")
    opts = variant_opts(name)
    t = time.monotonic()
    client = ClaudeSDKClient(options=opts)
    await client.connect()
    connect_s = time.monotonic() - t
    log(f"connect: {connect_s:.2f}s")
    rows: list[dict[str, float | None]] = []
    model = None
    try:
        for i in range(TURNS):
            prompt = f"In one short sentence, name a colour that starts with the letter {'BGRPOY'[i]}."
            t0 = time.monotonic()
            await client.query(prompt)
            first_event = first_text_delta = first_assistant = None
            text = ""
            async for msg in client.receive_response():
                now = time.monotonic() - t0
                if isinstance(msg, StreamEvent):
                    ev = msg.event
                    if first_event is None and ev.get("type") == "message_start":
                        first_event = now
                    if (
                        first_text_delta is None
                        and ev.get("type") == "content_block_delta"
                        and ev.get("delta", {}).get("type") == "text_delta"
                    ):
                        first_text_delta = now
                elif isinstance(msg, AssistantMessage):
                    model = msg.model
                    if any(isinstance(b, TextBlock) for b in msg.content):
                        first_assistant = first_assistant or now
                        text += "".join(b.text for b in msg.content if isinstance(b, TextBlock))
                elif isinstance(msg, SystemMessage):
                    pass
                elif isinstance(msg, ResultMessage):
                    row = dict(
                        message_start=first_event,
                        first_text_delta=first_text_delta,
                        first_assistant_text=first_assistant,
                        result=now,
                        api_ms=msg.duration_api_ms / 1000,
                    )
                    rows.append(row)
                    log(
                        f"turn {i + 1}: msg_start={first_event:.2f}s first_delta={first_text_delta or -1:.2f}s "
                        f"assistant={first_assistant or -1:.2f}s result={now:.2f}s "
                        f"cost=${msg.total_cost_usd:.4f} text={text.strip()[:60]!r}"
                    )
    finally:
        t = time.monotonic()
        await client.disconnect()
        log(f"disconnect: {time.monotonic() - t:.2f}s")

    def med(key: str, rs: list[dict[str, float | None]]) -> float:
        vals = [r[key] for r in rs if r[key] is not None]
        return statistics.median(vals) if vals else float("nan")  # type: ignore[arg-type]

    summary = {
        "variant": name,
        "model": model,
        "connect": connect_s,
        "t1_first_delta": rows[0]["first_text_delta"],
        "t2plus_first_delta_median": med("first_text_delta", rows[1:]),
        "t1_result": rows[0]["result"],
        "t2plus_result_median": med("result", rows[1:]),
        "t2plus_first_delta_min_max": (
            min(r["first_text_delta"] or 0 for r in rows[1:]),
            max(r["first_text_delta"] or 0 for r in rows[1:]),
        ),
    }
    log("SUMMARY", summary)
    return summary


async def main() -> None:
    check_env()
    variants = sys.argv[1:] or ["haiku", "default", "talker-haiku", "talker-default"]
    out = [await run_variant(v) for v in variants]
    print("\n| variant | model | connect s | turn1 first-delta s | turns2+ first-delta median s (min-max) | turn1 result s | turns2+ result median s |")
    print("|---|---|---|---|---|---|---|")
    for s in out:
        lo, hi = s["t2plus_first_delta_min_max"]
        print(
            f"| {s['variant']} | {s['model']} | {s['connect']:.2f} | {s['t1_first_delta']:.2f} | "
            f"{s['t2plus_first_delta_median']:.2f} ({lo:.2f}-{hi:.2f}) | {s['t1_result']:.2f} | {s['t2plus_result_median']:.2f} |"
        )


asyncio.run(main())
