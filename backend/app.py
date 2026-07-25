"""FastAPI application exposing UVSC array data to the browser."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.array_service import (
    MAX_INTERVAL_MS,
    MIN_INTERVAL_MS,
    UvscArrayService,
)


WORKSPACE = Path(__file__).resolve().parents[1]
FRONTEND_DIST = WORKSPACE / "frontend" / "dist"


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None else int(value, 0)


class ConfigUpdate(BaseModel):
    auto_refresh: bool | None = None
    interval_ms: int | None = Field(
        default=None,
        ge=MIN_INTERVAL_MS,
        le=MAX_INTERVAL_MS,
    )


class WebSocketHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)

    async def broadcast(self, event: dict[str, Any]) -> None:
        async with self._lock:
            clients = tuple(self._clients)
        dead: list[WebSocket] = []
        for client in clients:
            try:
                await client.send_json(event)
            except Exception:
                dead.append(client)
        if dead:
            async with self._lock:
                for client in dead:
                    self._clients.discard(client)


hub = WebSocketHub()
service = UvscArrayService(
    workspace=WORKSPACE,
    port=env_int("UVSC_PORT", 35876),
    address=env_int("UVSC_ARRAY_ADDRESS", 0x200041E4),
    count=env_int("UVSC_ARRAY_COUNT", 400),
    interval_ms=env_int("UVSC_INTERVAL_MS", 500),
    auto_refresh=os.getenv("UVSC_AUTO_REFRESH", "1") != "0",
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    loop = asyncio.get_running_loop()

    def publish(event: dict[str, Any]) -> None:
        asyncio.run_coroutine_threadsafe(hub.broadcast(event), loop)

    service.set_listener(publish)
    service.start()
    try:
        yield
    finally:
        service.set_listener(None)
        await asyncio.to_thread(service.stop)


app = FastAPI(
    title="Keil Array Viewer API",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/api/status")
async def get_status() -> dict[str, Any]:
    return service.get_status()


@app.get("/api/snapshot")
async def get_snapshot() -> dict[str, Any]:
    snapshot = service.get_latest()
    if snapshot is None:
        raise HTTPException(status_code=404, detail="尚无数组快照")
    return snapshot


@app.post("/api/refresh")
async def refresh_now() -> dict[str, Any]:
    try:
        return await asyncio.to_thread(service.refresh_now, 5.0)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.put("/api/config")
async def update_config(config: ConfigUpdate) -> dict[str, Any]:
    try:
        return service.configure(
            auto_refresh=config.auto_refresh,
            interval_ms=config.interval_ms,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await hub.connect(websocket)
    await websocket.send_json({"type": "status", "data": service.get_status()})
    snapshot = service.get_latest()
    if snapshot is not None:
        await websocket.send_json({"type": "snapshot", "data": snapshot})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect(websocket)


if FRONTEND_DIST.is_dir():
    assets = FRONTEND_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str) -> FileResponse:
        requested = (FRONTEND_DIST / path).resolve()
        if (
            path
            and requested.is_file()
            and FRONTEND_DIST.resolve() in requested.parents
        ):
            return FileResponse(requested)
        return FileResponse(FRONTEND_DIST / "index.html")
