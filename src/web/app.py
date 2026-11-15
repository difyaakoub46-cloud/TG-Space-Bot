"""FastAPI keep-alive server.

The bot itself runs on long polling, so it needs a process that stays awake.
This tiny HTTP app gives Render a health port and UptimeRobot a URL to ping,
which keeps the free instance from idling.
"""

from __future__ import annotations

import os
import time
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

SERVICE_NAME = "tg-space-bot"
STARTED_AT = time.monotonic()


def _uptime_seconds() -> int:
    return int(time.monotonic() - STARTED_AT)


def create_app() -> FastAPI:
    app = FastAPI(
        title="TG-Space-Bot keep-alive",
        description="Health endpoints used by Render and UptimeRobot.",
        version="1.0.0",
        docs_url="/docs",
        redoc_url=None,
    )

    @app.get("/", include_in_schema=False)
    @app.get("/ping", include_in_schema=False)
    async def ping() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": SERVICE_NAME,
            "uptime_seconds": _uptime_seconds(),
        }

    @app.get("/health", summary="Health probe for Render / UptimeRobot")
    async def health() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "service": SERVICE_NAME,
                "uptime_seconds": _uptime_seconds(),
                "pid": os.getpid(),
            },
            headers={"Cache-Control": "no-store"},
        )

    return app


app = create_app()
