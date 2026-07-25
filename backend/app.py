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
    MAX_SOURCES,
    MIN_INTERVAL_MS,
    UvscArrayService,
)
from backend.source_config import ArraySource, DATA_TYPES, MapSymbolResolver
from uvsc_smoke_test import UvscError


WORKSPACE = Path(__file__).resolve().parents[1]
FRONTEND_DIST = WORKSPACE / "frontend" / "dist"
DEFAULT_MAP_FILE = Path(
    os.getenv(
        "UVSC_MAP_FILE",
        (
            r"D:\STM32_prj\APF_Display_F4\MDK-ARM"
            r"\APF_Display_F4\APF_Display_F4.map"
        ),
    )
)


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


class SourceUpdate(BaseModel):
    array_name: str = Field(min_length=1, max_length=256)
    count: int = Field(ge=1)
    dtype: str
    address: int | str | None = None


class SourcesUpdate(BaseModel):
    map_file: str | None = Field(default=None, max_length=4096)
    keil_path: str | None = Field(default=None, max_length=4096)
    sources: list[SourceUpdate] = Field(
        min_length=1,
        max_length=MAX_SOURCES,
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
current_map_file = DEFAULT_MAP_FILE
current_keil_path = Path(
    os.getenv("UVSC_KEIL_PATH", r"E:\Keil\Keil_v5")
).expanduser().resolve()
service = UvscArrayService(
    workspace=WORKSPACE,
    port=env_int("UVSC_PORT", 35876),
    address=env_int("UVSC_ARRAY_ADDRESS", 0x200041E4),
    count=env_int("UVSC_ARRAY_COUNT", 400),
    array_name=os.getenv("UVSC_ARRAY_NAME", "myLOGGER0Arr"),
    data_type=os.getenv("UVSC_ARRAY_DTYPE", "float32"),
    interval_ms=env_int("UVSC_INTERVAL_MS", 500),
    auto_refresh=os.getenv("UVSC_AUTO_REFRESH", "1") != "0",
    dll_path=current_keil_path,
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


def parse_address(value: int | str | None) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value.strip(), 0)
    except ValueError as exc:
        raise ValueError(f"无效的数组地址：{value!r}") from exc


@app.get("/api/source/options")
async def get_source_options() -> dict[str, Any]:
    return {
        "data_types": list(DATA_TYPES),
        "map_file": str(current_map_file),
        "keil_path": str(current_keil_path),
        "max_sources": MAX_SOURCES,
    }


def resolve_source(
    config: SourceUpdate,
    symbol_resolver: MapSymbolResolver,
) -> tuple[ArraySource, dict[str, Any]]:
    data_type = DATA_TYPES.get(config.dtype)
    if data_type is None:
        raise ValueError(f"不支持的数据类型：{config.dtype}")

    address = parse_address(config.address)
    resolved_size: int | None = None
    resolved_from_map = address is None
    if address is None:
        symbol = symbol_resolver.resolve(config.array_name)
        address = symbol.address
        resolved_size = symbol.byte_size
        requested_size = config.count * data_type.byte_size
        if requested_size > symbol.byte_size:
            raise ValueError(
                f"{config.array_name} 在 MAP 中占 {symbol.byte_size} 字节，"
                f"当前配置需要读取 {requested_size} 字节"
            )

    source = ArraySource(
        name=config.array_name.strip(),
        address=address,
        count=config.count,
        data_type=config.dtype,
    )
    resolution = {
        "array_name": source.name,
        "address": source.address,
        "address_hex": f"0x{source.address:08X}",
        "resolved_from_map": resolved_from_map,
        "resolved_byte_size": resolved_size,
    }
    return source, resolution


@app.put("/api/sources")
async def update_sources(config: SourcesUpdate) -> dict[str, Any]:
    global current_keil_path, current_map_file
    try:
        map_file_text = (config.map_file or str(current_map_file)).strip()
        if not map_file_text:
            raise ValueError("MAP 文件路径不能为空")
        map_file = Path(map_file_text).expanduser().resolve()
        symbol_resolver = MapSymbolResolver(map_file)
        symbol_resolver.validate()
        keil_path_text = (config.keil_path or str(current_keil_path)).strip()
        if not keil_path_text:
            raise ValueError("Keil 安装目录不能为空")
        keil_path = Path(keil_path_text).expanduser().resolve()
        resolved = [
            resolve_source(item, symbol_resolver) for item in config.sources
        ]
        runtime = service.configure_sources_and_dll(
            (item[0] for item in resolved),
            keil_path,
        )
        current_map_file = map_file
        current_keil_path = keil_path
        return {
            "status": runtime["status"],
            "resolutions": [item[1] for item in resolved],
            "map_file": str(current_map_file),
            "keil_path": str(current_keil_path),
            "dll_path": runtime["dll_path"],
        }
    except (ValueError, UvscError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.put("/api/source")
async def update_source(config: SourceUpdate) -> dict[str, Any]:
    """Backward-compatible single-source endpoint."""
    result = await update_sources(SourcesUpdate(sources=[config]))
    resolution = result["resolutions"][0]
    return {
        "status": result["status"],
        "resolved_from_map": resolution["resolved_from_map"],
        "resolved_byte_size": resolution["resolved_byte_size"],
    }


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
