"""Thread-owned UVSC connection and multi-array refresh scheduling."""

from __future__ import annotations

import math
import queue
import statistics
import struct
import threading
import time
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from backend.source_config import ArraySource, DATA_TYPES
from read_mylogger_once import DEFAULT_ADDRESS, DEFAULT_COUNT
from uvsc_smoke_test import UvscClient, UvscError, resolve_dll


MIN_INTERVAL_MS = 50
MAX_INTERVAL_MS = 60_000
MAX_READ_BYTES = 65_536
MAX_SOURCES = 8
EventListener = Callable[[dict[str, Any]], None]


class UvscArrayService:
    """Keep every UVSC call on one worker thread."""

    def __init__(
        self,
        workspace: Path,
        port: int = 35876,
        address: int = DEFAULT_ADDRESS,
        count: int = DEFAULT_COUNT,
        array_name: str = "myLOGGER0Arr",
        data_type: str = "float32",
        interval_ms: int = 500,
        auto_refresh: bool = True,
        dll_path: Path | None = None,
    ) -> None:
        self.workspace = workspace
        self.port = port
        self.dll_path = dll_path

        initial_source = ArraySource(
            name=array_name.strip(),
            address=address,
            count=count,
            data_type=data_type,
        )
        self._validate_sources((initial_source,))

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._manual_requests: queue.Queue[Future[dict[str, Any]]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._listener: EventListener | None = None
        self._client: UvscClient | None = None
        self._reconnect_requested = False
        self._sequence = 0
        self._latest: dict[str, Any] | None = None
        self._sources = (initial_source,)
        self._connected = False
        self._target_state = "unknown"
        self._last_error: str | None = None
        self._auto_refresh = bool(auto_refresh)
        self._interval_ms = 500
        self._set_interval_unlocked(interval_ms)

    def set_listener(self, listener: EventListener | None) -> None:
        self._listener = listener

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="uvsc-array-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._fail_pending_requests(UvscError("UVSC 服务已停止"))

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            first = self._sources[0]
            return {
                "connected": self._connected,
                "target_state": self._target_state,
                "port": self.port,
                "keil_path": (
                    None if self.dll_path is None else str(self.dll_path)
                ),
                "sources": [source.as_status() for source in self._sources],
                # Keep the first source at the top level for older clients.
                **first.as_status(),
                "auto_refresh": self._auto_refresh,
                "interval_ms": self._interval_ms,
                "last_error": self._last_error,
                "latest_sequence": (
                    None if self._latest is None else self._latest["sequence"]
                ),
            }

    def get_latest(self) -> dict[str, Any] | None:
        with self._lock:
            return self._latest

    def configure(
        self,
        *,
        auto_refresh: bool | None = None,
        interval_ms: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if interval_ms is not None:
                self._set_interval_unlocked(interval_ms)
            if auto_refresh is not None:
                self._auto_refresh = bool(auto_refresh)
            status = self.get_status()
        self._wake_event.set()
        self._emit({"type": "status", "data": status})
        return status

    def configure_sources(
        self,
        sources: Iterable[ArraySource],
    ) -> dict[str, Any]:
        source_tuple = tuple(sources)
        self._validate_sources(source_tuple)
        with self._lock:
            self._sources = source_tuple
            self._latest = None
            self._last_error = None
            status = self.get_status()
        self._wake_event.set()
        self._emit({"type": "status", "data": status})
        return status

    def configure_dll_path(self, path: Path) -> dict[str, Any]:
        normalized = path.expanduser().resolve()
        resolved_dll = resolve_dll(normalized, self.workspace)
        with self._lock:
            changed = normalized != self.dll_path
            self.dll_path = normalized
            if changed:
                self._reconnect_requested = True
            status = self.get_status()
        self._wake_event.set()
        self._emit({"type": "status", "data": status})
        return {
            "status": status,
            "dll_path": str(resolved_dll),
        }

    def configure_sources_and_dll(
        self,
        sources: Iterable[ArraySource],
        dll_path: Path,
    ) -> dict[str, Any]:
        source_tuple = tuple(sources)
        self._validate_sources(source_tuple)
        normalized = dll_path.expanduser().resolve()
        resolved_dll = resolve_dll(normalized, self.workspace)
        with self._lock:
            path_changed = normalized != self.dll_path
            self.dll_path = normalized
            self._sources = source_tuple
            self._latest = None
            self._last_error = None
            if path_changed:
                self._reconnect_requested = True
            status = self.get_status()
        self._wake_event.set()
        self._emit({"type": "status", "data": status})
        return {
            "status": status,
            "dll_path": str(resolved_dll),
        }

    def configure_source(
        self,
        *,
        array_name: str,
        address: int,
        count: int,
        data_type: str,
    ) -> dict[str, Any]:
        """Backward-compatible single-source configuration."""
        return self.configure_sources(
            (
                ArraySource(
                    name=array_name.strip(),
                    address=address,
                    count=count,
                    data_type=data_type,
                ),
            )
        )

    def refresh_now(self, timeout: float = 10.0) -> dict[str, Any]:
        if self._thread is None or not self._thread.is_alive():
            raise UvscError("UVSC 服务尚未启动")
        future: Future[dict[str, Any]] = Future()
        self._manual_requests.put(future)
        self._wake_event.set()
        return future.result(timeout=timeout)

    def _set_interval_unlocked(self, interval_ms: int) -> None:
        if not MIN_INTERVAL_MS <= interval_ms <= MAX_INTERVAL_MS:
            raise ValueError(
                f"刷新间隔必须在 {MIN_INTERVAL_MS}～{MAX_INTERVAL_MS} ms 之间"
            )
        self._interval_ms = interval_ms

    def _validate_sources(self, sources: tuple[ArraySource, ...]) -> None:
        if not sources:
            raise ValueError("至少需要配置一条曲线")
        if len(sources) > MAX_SOURCES:
            raise ValueError(f"最多支持 {MAX_SOURCES} 条曲线")

        names: set[str] = set()
        for source in sources:
            name = source.name.strip()
            if not name:
                raise ValueError("数组名称不能为空")
            if name in names:
                raise ValueError(f"数组名称不能重复：{name}")
            names.add(name)
            if not 0 <= source.address <= 0xFFFFFFFFFFFFFFFF:
                raise ValueError(f"{name} 地址超出范围：0x{source.address:X}")
            if source.data_type not in DATA_TYPES:
                raise ValueError(f"{name} 使用了不支持的数据类型：{source.data_type}")
            if source.count <= 0:
                raise ValueError(f"{name} 的元素数量必须大于 0")
            byte_count = (
                source.count * DATA_TYPES[source.data_type].byte_size
            )
            if byte_count > MAX_READ_BYTES:
                raise ValueError(
                    f"{name} 单次读取不能超过 {MAX_READ_BYTES} 字节，"
                    f"当前配置为 {byte_count} 字节"
                )

    def _run(self) -> None:
        next_refresh = time.monotonic()
        retry_at = time.monotonic()
        try:
            while not self._stop_event.is_set():
                now = time.monotonic()
                with self._lock:
                    reconnect_requested = self._reconnect_requested
                    self._reconnect_requested = False
                if reconnect_requested:
                    self._disconnect()
                    retry_at = now

                if self._client is None and now >= retry_at:
                    try:
                        self._connect()
                        next_refresh = time.monotonic()
                    except Exception as exc:
                        self._record_error(str(exc), connected=False)
                        self._fail_pending_requests(exc)
                        retry_at = time.monotonic() + 2.0

                self._process_manual_requests()

                with self._lock:
                    auto_refresh = self._auto_refresh
                    interval_seconds = self._interval_ms / 1000

                now = time.monotonic()
                if self._client is not None and auto_refresh and now >= next_refresh:
                    try:
                        self._capture("auto")
                    except Exception:
                        # Error state is already published by _capture.
                        pass
                    next_refresh += interval_seconds
                    if next_refresh < time.monotonic():
                        next_refresh = time.monotonic() + interval_seconds

                if self._client is None:
                    timeout = max(0.05, min(0.25, retry_at - time.monotonic()))
                elif auto_refresh:
                    timeout = max(0.01, min(0.25, next_refresh - time.monotonic()))
                else:
                    timeout = 0.25
                self._wake_event.wait(timeout)
                self._wake_event.clear()
        finally:
            self._disconnect()

    def _connect(self) -> None:
        with self._lock:
            configured_path = self.dll_path
        dll_path = resolve_dll(configured_path, self.workspace)
        client = UvscClient(dll_path)
        try:
            client.initialize()
            client.connect(self.port)
            result, target_status, detail = client.debug_status()
            if result != 0:
                raise UvscError(f"无法确认调试状态：{detail}")
        except Exception:
            client.close()
            raise

        self._client = client
        with self._lock:
            self._connected = True
            self._target_state = "running" if target_status == 1 else "stopped"
            self._last_error = None
            status = self.get_status()
        self._emit({"type": "status", "data": status})

    def _disconnect(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            client.close()
        with self._lock:
            self._connected = False
            self._target_state = "unknown"

    def _process_manual_requests(self) -> None:
        while True:
            try:
                future = self._manual_requests.get_nowait()
            except queue.Empty:
                return

            if self._client is None:
                future.set_exception(
                    UvscError(self._last_error or "UVSC 未连接")
                )
                continue
            try:
                future.set_result(self._capture("manual"))
            except Exception as exc:
                future.set_exception(exc)

    def _capture(self, trigger: str) -> dict[str, Any]:
        if self._client is None:
            raise UvscError("UVSC 未连接")

        with self._lock:
            sources = self._sources

        capture_started = time.perf_counter()
        series: list[dict[str, Any]] = []
        try:
            for source in sources:
                data_type = DATA_TYPES[source.data_type]
                read_started = time.perf_counter()
                raw = self._client.read_memory(
                    source.address,
                    source.count * data_type.byte_size,
                )
                values = struct.unpack(
                    f"<{source.count}{data_type.struct_code}",
                    raw,
                )
                finite = [value for value in values if math.isfinite(value)]
                series.append(
                    {
                        **source.as_status(),
                        "read_duration_ms": round(
                            (time.perf_counter() - read_started) * 1000,
                            3,
                        ),
                        "values": values,
                        "stats": {
                            "min": min(finite) if finite else None,
                            "max": max(finite) if finite else None,
                            "mean": (
                                statistics.fmean(finite) if finite else None
                            ),
                            "non_finite": len(values) - len(finite),
                        },
                    }
                )
        except Exception as exc:
            self._record_error(str(exc), connected=True)
            raise

        self._sequence += 1
        snapshot = {
            "sequence": self._sequence,
            "captured_at": datetime.now().astimezone().isoformat(
                timespec="milliseconds"
            ),
            "trigger": trigger,
            "read_duration_ms": round(
                (time.perf_counter() - capture_started) * 1000,
                3,
            ),
            "series": series,
        }
        # Preserve legacy fields for existing single-series consumers.
        first = series[0]
        snapshot.update(
            {
                "array_name": first["array_name"],
                "address": first["address"],
                "count": first["count"],
                "dtype": first["dtype"],
                "values": first["values"],
                "stats": first["stats"],
            }
        )
        with self._lock:
            self._latest = snapshot
            self._last_error = None
        self._emit({"type": "snapshot", "data": snapshot})
        return snapshot

    def _record_error(self, message: str, connected: bool) -> None:
        with self._lock:
            self._connected = connected
            self._last_error = message
            status = self.get_status()
        self._emit({"type": "status", "data": status})

    def _fail_pending_requests(self, exc: BaseException) -> None:
        while True:
            try:
                future = self._manual_requests.get_nowait()
            except queue.Empty:
                return
            if not future.done():
                future.set_exception(exc)

    def _emit(self, event: dict[str, Any]) -> None:
        listener = self._listener
        if listener is not None:
            listener(event)
