"""FastAPI application exposing UVSC array data to the browser."""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.array_service import (
    MAX_INTERVAL_MS,
    MAX_SOURCES,
    MIN_INTERVAL_MS,
    UvscArrayService,
)
from backend.source_config import (
    MAX_SCALE_FACTOR,
    MIN_SCALE_FACTOR,
    ArraySource,
    DATA_TYPES,
    MapSymbolResolver,
)
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


def format_csv_time(index: int, frequency_hz: int) -> str:
    return f"{index / frequency_hz:.12f}".rstrip("0").rstrip(".") or "0"


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
    scale_operator: Literal["multiply", "divide"] = "multiply"
    scale_factor: float = Field(
        default=1.0,
        ge=MIN_SCALE_FACTOR,
        le=MAX_SCALE_FACTOR,
        allow_inf_nan=False,
    )


class SourcesUpdate(BaseModel):
    map_file: str | None = Field(default=None, max_length=4096)
    keil_path: str | None = Field(default=None, max_length=4096)
    sources: list[SourceUpdate] = Field(
        min_length=1,
        max_length=MAX_SOURCES,
    )


class ScaleUpdate(BaseModel):
    channel_index: int = Field(ge=0)
    scale_operator: Literal["multiply", "divide"]
    scale_factor: float = Field(
        ge=MIN_SCALE_FACTOR,
        le=MAX_SCALE_FACTOR,
        allow_inf_nan=False,
    )


class CsvExportRequest(BaseModel):
    channel_index: int = Field(ge=0)
    frequency_hz: int = Field(ge=1, le=100_000_000)


class FftConfig(BaseModel):
    channel_index: int = Field(default=0, ge=0)
    sample_frequency_hz: int = Field(
        default=20_000,
        ge=1,
        le=100_000_000,
    )
    base_frequency_hz: int = Field(
        default=50,
        ge=1,
        le=100_000_000,
    )
    harmonic_count: int = Field(default=20, ge=1, le=1000)
    window: Literal["rectangular", "hann", "hamming"] = "hann"
    amplitude: Literal["rms", "amp"] = "rms"


class SavedSettings(BaseModel):
    version: int = 1
    auto_refresh: bool
    interval_ms: int = Field(ge=MIN_INTERVAL_MS, le=MAX_INTERVAL_MS)
    map_file: str = Field(min_length=1, max_length=4096)
    keil_path: str = Field(min_length=1, max_length=4096)
    sources: list[SourceUpdate] = Field(
        min_length=1,
        max_length=MAX_SOURCES,
    )
    export_channel_index: int = Field(default=0, ge=0)
    export_frequency_hz: int = Field(
        default=20_000,
        ge=1,
        le=100_000_000,
    )
    fft: FftConfig = Field(default_factory=FftConfig)


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
SETTINGS_FILE = Path(
    os.getenv("VIEWER_CONFIG_FILE", str(WORKSPACE / ".keil-array-viewer.json"))
).expanduser().resolve()
current_source_configs = [
    SourceUpdate(
        array_name=os.getenv("UVSC_ARRAY_NAME", "myLOGGER0Arr"),
        count=env_int("UVSC_ARRAY_COUNT", 400),
        dtype=os.getenv("UVSC_ARRAY_DTYPE", "float32"),
        address=env_int("UVSC_ARRAY_ADDRESS", 0x200041E4),
    )
]
current_export_channel_index = 0
current_export_frequency_hz = 20_000
current_fft_config = FftConfig()
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

    try:
        restore_saved_settings()
    except Exception as exc:
        print(f"[WARN] 无法恢复上次配置，将使用默认配置：{exc}")

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


@app.post("/api/export/csv")
async def export_csv(config: CsvExportRequest) -> Response:
    global current_export_channel_index, current_export_frequency_hz
    status = service.get_status()
    if status["auto_refresh"]:
        raise HTTPException(
            status_code=409,
            detail="请先关闭自动刷新，再导出 CSV",
        )

    snapshot = service.get_latest()
    if snapshot is None:
        raise HTTPException(status_code=404, detail="尚无可导出的数组快照")
    series = snapshot["series"]
    if config.channel_index >= len(series):
        raise HTTPException(status_code=422, detail="导出通道不存在")

    channel = series[config.channel_index]
    frequency = config.frequency_hz
    lines = [
        f"{format_csv_time(index, frequency)},{format(value, '.12g')}"
        for index, value in enumerate(channel["values"])
    ]
    content = "\r\n".join(lines) + "\r\n"
    safe_name = re.sub(
        r"[^A-Za-z0-9_.-]+",
        "_",
        channel["array_name"],
    ).strip("._") or f"channel_{config.channel_index + 1}"

    current_export_channel_index = config.channel_index
    current_export_frequency_hz = frequency
    try:
        save_current_settings()
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"保存导出配置失败：{exc}",
        ) from exc

    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}.csv"',
        },
    )


@app.put("/api/config")
async def update_config(config: ConfigUpdate) -> dict[str, Any]:
    try:
        status = service.configure(
            auto_refresh=config.auto_refresh,
            interval_ms=config.interval_ms,
        )
        save_current_settings()
        return status
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"保存配置失败：{exc}",
        ) from exc


@app.put("/api/fft/config")
async def update_fft_config(config: FftConfig) -> dict[str, Any]:
    global current_fft_config
    if config.channel_index >= len(current_source_configs):
        raise HTTPException(status_code=422, detail="FFT 分析通道不存在")
    if config.base_frequency_hz * 2 > config.sample_frequency_hz:
        raise HTTPException(
            status_code=422,
            detail="基波频率不能超过采样频率的一半",
        )
    current_fft_config = config
    try:
        save_current_settings()
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"保存 FFT 配置失败：{exc}",
        ) from exc
    return config.model_dump(mode="json")


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
        "sources": [
            item.model_dump(mode="json") for item in current_source_configs
        ],
        "settings_file": str(SETTINGS_FILE),
        "export_channel_index": current_export_channel_index,
        "export_frequency_hz": current_export_frequency_hz,
        "fft_config": current_fft_config.model_dump(mode="json"),
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
        scale_operator=config.scale_operator,
        scale_factor=config.scale_factor,
    )
    resolution = {
        "array_name": source.name,
        "address": source.address,
        "address_hex": f"0x{source.address:08X}",
        "resolved_from_map": resolved_from_map,
        "resolved_byte_size": resolved_size,
    }
    return source, resolution


def resolve_sources_config(
    config: SourcesUpdate,
) -> tuple[
    Path,
    Path,
    list[tuple[ArraySource, dict[str, Any]]],
]:
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
    return map_file, keil_path, resolved


def apply_sources(config: SourcesUpdate) -> dict[str, Any]:
    global current_export_channel_index
    global current_fft_config
    global current_keil_path, current_map_file, current_source_configs
    map_file, keil_path, resolved = resolve_sources_config(config)
    runtime = service.configure_sources_and_dll(
        (item[0] for item in resolved),
        keil_path,
    )
    current_map_file = map_file
    current_keil_path = keil_path
    current_source_configs = list(config.sources)
    current_export_channel_index = min(
        current_export_channel_index,
        len(current_source_configs) - 1,
    )
    if current_fft_config.channel_index >= len(current_source_configs):
        current_fft_config = current_fft_config.model_copy(
            update={"channel_index": 0}
        )
    return {
        "status": runtime["status"],
        "resolutions": [item[1] for item in resolved],
        "map_file": str(current_map_file),
        "keil_path": str(current_keil_path),
        "dll_path": runtime["dll_path"],
    }


@app.post("/api/sources/test")
async def test_sources(config: SourcesUpdate) -> dict[str, Any]:
    try:
        map_file, keil_path, resolved = resolve_sources_config(config)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        result = await asyncio.to_thread(
            service.test_configuration,
            tuple(item[0] for item in resolved),
            keil_path,
            30.0,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        **result,
        "map_file": str(map_file),
        "resolutions": [item[1] for item in resolved],
    }


@app.put("/api/source/scale")
async def update_source_scale(config: ScaleUpdate) -> dict[str, Any]:
    global current_source_configs
    if config.channel_index >= len(current_source_configs):
        raise HTTPException(status_code=422, detail="缩放通道不存在")

    updated_source = current_source_configs[
        config.channel_index
    ].model_copy(
        update={
            "scale_operator": config.scale_operator,
            "scale_factor": config.scale_factor,
        }
    )
    try:
        status = service.configure_scale(
            config.channel_index,
            config.scale_operator,
            config.scale_factor,
        )
        current_source_configs[config.channel_index] = updated_source
        save_current_settings()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"保存倍率失败：{exc}",
        ) from exc

    return {
        "status": status,
        "source": updated_source.model_dump(mode="json"),
    }


def current_saved_settings() -> SavedSettings:
    status = service.get_status()
    return SavedSettings(
        auto_refresh=status["auto_refresh"],
        interval_ms=status["interval_ms"],
        map_file=str(current_map_file),
        keil_path=str(current_keil_path),
        sources=current_source_configs,
        export_channel_index=current_export_channel_index,
        export_frequency_hz=current_export_frequency_hz,
        fft=current_fft_config,
    )


def save_current_settings() -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS_FILE.with_name(f"{SETTINGS_FILE.name}.tmp")
    payload = current_saved_settings().model_dump(mode="json")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, SETTINGS_FILE)


def restore_saved_settings() -> bool:
    global current_export_channel_index, current_export_frequency_hz
    global current_fft_config
    if not SETTINGS_FILE.is_file():
        return False
    payload = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    settings = SavedSettings.model_validate(payload)
    apply_sources(
        SourcesUpdate(
            map_file=settings.map_file,
            keil_path=settings.keil_path,
            sources=settings.sources,
        )
    )
    service.configure(
        auto_refresh=settings.auto_refresh,
        interval_ms=settings.interval_ms,
    )
    current_export_channel_index = min(
        settings.export_channel_index,
        len(settings.sources) - 1,
    )
    current_export_frequency_hz = settings.export_frequency_hz
    current_fft_config = settings.fft.model_copy(
        update={
            "channel_index": min(
                settings.fft.channel_index,
                len(settings.sources) - 1,
            )
        }
    )
    return True


@app.put("/api/sources")
async def update_sources(config: SourcesUpdate) -> dict[str, Any]:
    try:
        result = apply_sources(config)
        save_current_settings()
        return result
    except (ValueError, UvscError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"保存配置失败：{exc}",
        ) from exc


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
