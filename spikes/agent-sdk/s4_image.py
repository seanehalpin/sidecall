"""4. Image input via streaming input on a ClaudeSDKClient (crop-screenshot style)."""

import asyncio
import base64
import io
from collections.abc import AsyncIterator
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from PIL import Image, ImageDraw, ImageFont

from common import CHEAP_MODEL, check_env, drain, log, make_scratch_repo


def make_png() -> bytes:
    img = Image.new("RGB", (240, 160), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, 140, 140], fill=(220, 20, 20))
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 64)
    except OSError:
        font = ImageFont.load_default()
    d.text((160, 45), "42", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


async def user_turn(text: str, png: bytes) -> AsyncIterator[dict[str, Any]]:
    yield {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.b64encode(png).decode(),
                    },
                },
            ],
        },
        "parent_tool_use_id": None,
    }


async def main() -> None:
    check_env()
    png = make_png()
    log(f"png bytes: {len(png)}")
    opts = ClaudeAgentOptions(
        cwd=make_scratch_repo(),
        model=CHEAP_MODEL,
        setting_sources=[],
        allowed_tools=[],
        tools=[],  # no built-in tools: pure vision check
        max_turns=1,
    )
    async with ClaudeSDKClient(options=opts) as client:
        await client.query(
            user_turn("[1] is attached. What colour is the square and what number is shown? One line.", png)
        )
        await drain(client)
        # Follow-up turn without the image: does the image stay in session context?
        await client.query("What was the number in the image I sent? Just the number.")
        await drain(client)


asyncio.run(main())
