"""KAV1 serial-frame receiver used by the waveform viewer."""

from __future__ import annotations

import math
import statistics
import struct
import threading
import time
import zlib
from datetime import datetime
from typing import Any, Callable, Iterable

import serial

from backend.array_service import MAX_INTERVAL_MS, MAX_SOURCES, MIN_INTERVAL_MS
from backend.source_config import ArraySource, DATA_TYPES


MAGIC = b"KAV1"
VERSION = 2
HEADER = struct.Struct("<4sBBHII")
CRC = struct.Struct("<I")
MAX_PAYLOAD_BYTES = 65_536
EventListener = Callable[[dict[str, Any]], None]


class SerialArrayService:
    """Continuously parse KAV1 frames and publish the selected channels."""

    def __init__(
        self,
        port: str,
        baudrate: int,
        sources: Iterable[ArraySource],
        interval_ms: int = 500,
        auto_refresh: bool = True,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self._sources = tuple(sources)
        self._validate_sources(self._sources)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._listener: EventListener | None = None
        self._serial: serial.Serial | None = None
        self._buffer = bytearray()
        self._latest: dict[str, Any] | None = None
        self._connected = False
        self._last_error: str | None = None
        self._auto_refresh = bool(auto_refresh)
        self._interval_ms = interval_ms
        self._sequence = 0

    def set_listener(self, listener: EventListener | None) -> None:
        self._listener = listener

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="serial-array-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            first = self._sources[0]
            return {
                "connected": self._connected,
                "target_state": "running" if self._connected else "unknown",
                "port": self.port,
                "data_source": "serial",
                "serial_port": self.port,
                "serial_baudrate": self.baudrate,
                "keil_path": None,
                "sources": [source.as_status() for source in self._sources],
                **first.as_status(),
                "auto_refresh": self._auto_refresh,
                "interval_ms": self._interval_ms,
                "last_error": self._last_error,
                "latest_sequence": None if self._latest is None else self._latest["sequence"],
            }

    def get_latest(self) -> dict[str, Any] | None:
        with self._lock:
            return self._latest

    def configure(self, *, auto_refresh: bool | None = None, interval_ms: int | None = None) -> dict[str, Any]:
        with self._lock:
            if interval_ms is not None:
                if not MIN_INTERVAL_MS <= interval_ms <= MAX_INTERVAL_MS:
                    raise ValueError(f"刷新间隔必须在 {MIN_INTERVAL_MS}～{MAX_INTERVAL_MS} ms 之间")
                self._interval_ms = interval_ms
            if auto_refresh is not None:
                self._auto_refresh = bool(auto_refresh)
            status = self.get_status()
        self._emit({"type": "status", "data": status})
        return status

    def configure_sources(self, sources: Iterable[ArraySource]) -> dict[str, Any]:
        source_tuple = tuple(sources)
        self._validate_sources(source_tuple)
        with self._lock:
            self._sources = source_tuple
            self._latest = None
            self._last_error = None
            status = self.get_status()
        self._emit({"type": "status", "data": status})
        return status

    def configure_scale(self, channel_index: int, scale_operator: str, scale_factor: float) -> dict[str, Any]:
        with self._lock:
            if not 0 <= channel_index < len(self._sources):
                raise ValueError("缩放通道不存在")
            source = self._sources[channel_index]
            updated = ArraySource(source.name, source.address, source.count, source.data_type, scale_operator, scale_factor)
            return self.configure_sources((*self._sources[:channel_index], updated, *self._sources[channel_index + 1:]))

    def refresh_now(self, timeout: float = 5.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        initial = self.get_latest()
        while time.monotonic() < deadline:
            latest = self.get_latest()
            if latest is not None and latest is not initial:
                return latest
            time.sleep(0.02)
        raise TimeoutError("等待串口数组帧超时")

    def _validate_sources(self, sources: tuple[ArraySource, ...]) -> None:
        if not 1 <= len(sources) <= MAX_SOURCES:
            raise ValueError(f"串口通道数量必须在 1～{MAX_SOURCES} 之间")
        seen: set[int] = set()
        for source in sources:
            if not source.name.strip():
                raise ValueError("串口通道名称不能为空")
            if source.data_type != "float32":
                raise ValueError("KAV1 串口协议只支持 float32")
            if source.address in seen or source.address < 0:
                raise ValueError("串口通道编号必须唯一且不小于 0")
            seen.add(source.address)

    def _run(self) -> None:
        retry_at = 0.0
        while not self._stop.is_set():
            if self._serial is None:
                if time.monotonic() < retry_at:
                    self._stop.wait(0.2)
                    continue
                try:
                    self._serial = serial.Serial(self.port, self.baudrate, timeout=0.2)
                    with self._lock:
                        self._connected = True
                        self._last_error = None
                    self._emit({"type": "status", "data": self.get_status()})
                except serial.SerialException as exc:
                    with self._lock:
                        self._connected = False
                        self._last_error = str(exc)
                    self._emit({"type": "status", "data": self.get_status()})
                    retry_at = time.monotonic() + 2.0
                    continue
            try:
                chunk = self._serial.read(4096)
                if chunk:
                    self._buffer.extend(chunk)
                    self._consume_frames()
            except serial.SerialException as exc:
                self._close_serial(str(exc))
                retry_at = time.monotonic() + 2.0
        self._close_serial(None)

    def _consume_frames(self) -> None:
        while True:
            start = self._buffer.find(MAGIC)
            if start < 0:
                del self._buffer[:-3]
                return
            if start:
                del self._buffer[:start]
            if len(self._buffer) < HEADER.size:
                return
            magic, version, channels, samples, sequence, payload_size = HEADER.unpack(self._buffer[:HEADER.size])
            expected = channels * samples * 4
            total = HEADER.size + payload_size + CRC.size
            if magic != MAGIC or version != VERSION or not channels or payload_size != expected or payload_size > MAX_PAYLOAD_BYTES:
                del self._buffer[0]
                continue
            if len(self._buffer) < total:
                return
            frame = bytes(self._buffer[:total])
            del self._buffer[:total]
            if zlib.crc32(frame[:-CRC.size]) != CRC.unpack(frame[-CRC.size:])[0]:
                continue
            values = struct.unpack(f"<{channels * samples}f", frame[HEADER.size:-CRC.size])
            self._publish_frame(sequence, channels, samples, values)

    def _publish_frame(self, sequence: int, channels: int, samples: int, values: tuple[float, ...]) -> None:
        with self._lock:
            if not self._auto_refresh:
                return
            sources = self._sources
        started = time.perf_counter()
        series: list[dict[str, Any]] = []
        for source in sources:
            if source.address >= channels or source.count != samples:
                self._last_error = f"KAV1 帧为 {channels} 通道 × {samples} 点，与串口通道配置不匹配"
                return
            raw = values[source.address * samples:(source.address + 1) * samples]
            multiplier = source.scale_multiplier
            scaled = raw if multiplier == 1 else tuple(value * multiplier for value in raw)
            finite = [value for value in scaled if math.isfinite(value)]
            series.append({**source.as_status(), "read_duration_ms": 0.0, "values": scaled, "stats": {"min": min(finite) if finite else None, "max": max(finite) if finite else None, "mean": statistics.fmean(finite) if finite else None, "non_finite": len(scaled) - len(finite)}})
        snapshot = {"sequence": sequence, "captured_at": datetime.now().astimezone().isoformat(timespec="milliseconds"), "trigger": "auto", "read_duration_ms": round((time.perf_counter() - started) * 1000, 3), "series": series}
        first = series[0]
        snapshot.update({key: first[key] for key in ("array_name", "address", "count", "dtype", "scale_operator", "scale_factor", "values", "stats")})
        with self._lock:
            self._latest = snapshot
            self._sequence = sequence
            self._last_error = None
        self._emit({"type": "snapshot", "data": snapshot})

    def _close_serial(self, error: str | None) -> None:
        port, self._serial = self._serial, None
        if port is not None:
            port.close()
        with self._lock:
            self._connected = False
            if error is not None:
                self._last_error = error

    def _emit(self, event: dict[str, Any]) -> None:
        if self._listener is not None:
            self._listener(event)
