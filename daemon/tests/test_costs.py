from __future__ import annotations

from sidecall.costs import CostTracker, format_usage, parse_usage

USAGE = {
    "input_tokens": 12,
    "cache_read_input_tokens": 9000,
    "cache_creation_input_tokens": 300,
    "output_tokens": 40,
}


def test_turn_cost_is_the_change_in_the_session_total() -> None:
    costs = CostTracker()
    first = costs.record(0.05, USAGE)
    second = costs.record(0.08, USAGE)
    assert first.turn_cost == 0.05
    assert second.turn_cost is not None and abs(second.turn_cost - 0.03) < 1e-9
    assert second.session_cost == 0.08
    assert second.context_tokens == 9312


def test_a_lower_total_means_a_new_session() -> None:
    costs = CostTracker()
    costs.record(1.0, None)
    assert costs.record(0.2, None).turn_cost == 0.2
    costs.reset()
    assert costs.record(0.5, None).turn_cost == 0.5


def test_missing_usage_is_logged_as_unknown() -> None:
    cost = CostTracker().record(None, {})
    assert cost.turn_cost is None
    assert cost.log_fields() == "session_cost=unknown turn_cost=unknown usage=unknown"
    assert CostTracker.unknown().log_fields().endswith("usage=unknown")


def test_usage_fields() -> None:
    assert format_usage(parse_usage(USAGE)) == "input:12 cache_read:9000 cache_write:300 output:40"
    assert parse_usage({"output_tokens": 5}) == {
        "input": 0,
        "cache_read": 0,
        "cache_write": 0,
        "output": 5,
    }
