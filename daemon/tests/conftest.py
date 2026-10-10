from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="run tests that talk to Claude Code (costs usage)",
    )
    parser.addoption(
        "--voice",
        action="store_true",
        default=False,
        help="run slow tests that load the voice models (MLX, ~3 GB)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for marker in ("integration", "voice"):
        if config.getoption(f"--{marker}"):
            continue
        skip = pytest.mark.skip(reason=f"needs --{marker}")
        for item in items:
            if marker in item.keywords:
                item.add_marker(skip)
