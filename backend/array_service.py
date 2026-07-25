"""Thread-owned UVSC connection and array refresh scheduling."""

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
from typing import Any, Callable

from read_mylogger_once import DEFAULT_ADDRESS, DEFAULT_COUNT, ELEMENT_SIZE
from uvsc_smoke_test import UvscClient, UvscError, resolve_dll


MIN_INTERVAL_MS = 50
MAX_INTERVAL_MS = 60_000
EventListener = Callable[[dict[str, Any]], None]


class UvscArrayService:
    """Keep all UVSC calls on one worker thread."""

    def __init__(
        self,
        workspace: Path,
        port: int = 35876,
        address: int = DEFAULT_ADDRESS,
        count: int = DEFAULT_COUNT,
        interval_ms: int = 500,
        auto_refresh: bool = True,
        dll_path: Path | None = None,
    ) -> None:
        self.workspace = workspace
        self.port = port
        self.address = address
        self.count = count
        self.dll_path = dll_path

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._manual_requests: queue.Queue[Future[dict[str, Any]]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._listener: EventListener | None = None
        self._client: UvscClient | None = None
        self._sequence = 0
        self._latest: dict[str, Any] | None = None
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
            return {
                "connected": self._connected,
                "target_state": self._target_state,
                "port": self.port,
                "address": self.address,
                "address_hex": f"0x{self.address:08X}",
                "count": self.count,
                "dtype": "float32",
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

    def refresh_now(self, timeout: float = 5.0) -> dict[str, Any]:
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

    def _run(self) -> None:
        next_refresh = time.monotonic()
        retry_at = time.monotonic()
        try:
            while not self._stop_event.is_set():
                now = time.monotonic()
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
                    self._capture("auto")
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
        dll_path = resolve_dll(self.dll_path, self.workspace)
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
                future.set_exception(UvscError(self._last_error or "UVSC 未连接"))
                continue
            try:
                future.set_result(self._capture("manual"))
            except Exception as exc:
                future.set_exception(exc)

    def _capture(self, trigger: str) -> dict[str, Any]:
        if self._client is None:
            raise UvscError("UVSC 未连接")

        started = time.perf_counter()
        try:
            raw = self._client.read_memory(
                self.address,
                self.count * ELEMENT_SIZE,
            )
            values = struct.unpack(f"<{self.count}f", raw)
        except Exception as exc:
            self._record_error(str(exc), connected=True)
            raise

        duration_ms = (time.perf_counter() - started) * 1000
        finite = [value for value in values if math.isfinite(value)]
        self._sequence += 1
        snapshot = {
            "sequence": self._sequence,
            "captured_at": datetime.now().astimezone().isoformat(
                timespec="milliseconds"
            ),
            "trigger": trigger,
            "address": self.address,
            "count": self.count,
            "read_duration_ms": round(duration_ms, 3),
            "values": values,
            "stats": {
                "min": min(finite) if finite else None,
                "max": max(finite) if finite else None,
                "mean": statistics.fmean(finite) if finite else None,
                "non_finite": len(values) - len(finite),
            },
        }
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
