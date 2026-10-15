"""Per-turn cost and token usage for talker and worker logs.

`ResultMessage.total_cost_usd` is the running total for the client's session, so a
turn's own cost is the change since the session's previous result. Pure: no SDK imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

USAGE_KEYS = (
    ("input", "input_tokens"),
    ("cache_read", "cache_read_input_tokens"),
    ("cache_write", "cache_creation_input_tokens"),
    ("output", "output_tokens"),
)


@dataclass(frozen=True)
class TurnCost:
    session_cost: float | None
    turn_cost: float | None
    usage: dict[str, int] | None
    """Token counts by short name (input, cache_read, cache_write, output), or None."""

    @property
    def context_tokens(self) -> int | None:
        """Tokens the turn's last request read: the session's context size, roughly."""
        if self.usage is None:
            return None
        return self.usage["input"] + self.usage["cache_read"] + self.usage["cache_write"]

    def log_fields(self) -> str:
        """`session_cost=$… turn_cost=$… usage=input:… cache_read:… …` (or usage=unknown)."""
        return (
            f"session_cost={_money(self.session_cost)} turn_cost={_money(self.turn_cost)} "
            f"usage={format_usage(self.usage)}"
        )


def _money(value: float | None) -> str:
    return "unknown" if value is None else f"${value:.4f}"


def parse_usage(usage: dict[str, Any] | None) -> dict[str, int] | None:
    """The four token counts from a result's `usage`, or None when it has none of them."""
    if not usage or not any(isinstance(usage.get(k), int) for _, k in USAGE_KEYS):
        return None
    out: dict[str, int] = {}
    for short, key in USAGE_KEYS:
        value = usage.get(key)
        out[short] = value if isinstance(value, int) else 0
    return out


def format_usage(usage: dict[str, int] | None) -> str:
    if usage is None:
        return "unknown"
    return " ".join(f"{k}:{usage[k]}" for k, _ in USAGE_KEYS)


class CostTracker:
    """Turns session totals into per-turn costs. One per client session: call `reset()`
    when the session changes (a new client or a rotation)."""

    def __init__(self) -> None:
        self._last: float | None = None

    def reset(self) -> None:
        self._last = None

    def record(self, session_cost: float | None, usage: dict[str, Any] | None) -> TurnCost:
        turn: float | None = None
        if session_cost is not None:
            # A total lower than the last one means the session restarted underneath us.
            last = self._last if self._last is not None and self._last <= session_cost else 0.0
            turn = session_cost - last
            self._last = session_cost
        return TurnCost(session_cost, turn, parse_usage(usage))

    @staticmethod
    def unknown() -> TurnCost:
        """A turn that ended without a result (cancelled or failed mid-stream)."""
        return TurnCost(None, None, None)
