"""Write the protocol's JSON Schema to /protocol.

    uv run python -m sidecall.protocol.export [out_dir]

The extension generates its TypeScript types from this file
(`pnpm gen:protocol`); never hand-edit those types.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from .messages import ClientMessage, ServerMessage
from .models import Model

DEFAULT_OUT = Path(__file__).resolve().parents[4] / "protocol"


class Protocol(Model):
    """Container so both unions share one set of $defs."""

    client: ClientMessage
    server: ServerMessage


def build_schema() -> dict[str, Any]:
    schema = TypeAdapter(Protocol).json_schema(mode="serialization", by_alias=True)
    schema["title"] = "SidecallProtocol"
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return schema


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "sidecall.schema.json"
    path.write_text(json.dumps(build_schema(), indent=2, sort_keys=True) + "\n")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
