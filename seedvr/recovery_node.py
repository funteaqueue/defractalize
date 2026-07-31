"""Private ComfyUI control endpoint used to recover from uninterruptible nodes."""

from __future__ import annotations

import asyncio
import os

from aiohttp import web
from server import PromptServer


@PromptServer.instance.routes.post("/defractalize/restart")
async def restart_comfyui(_request: web.Request) -> web.Response:
    # Return the acknowledgement before terminating PID 1. Docker's
    # ``restart: unless-stopped`` policy then starts a clean SeedVR worker.
    asyncio.get_running_loop().call_later(0.25, os._exit, 70)
    return web.json_response({"status": "restarting"})


NODE_CLASS_MAPPINGS: dict[str, object] = {}
NODE_DISPLAY_NAME_MAPPINGS: dict[str, str] = {}
