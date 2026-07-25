"""Continuously or manually refresh myLOGGER0Arr through UVSC."""

from __future__ import annotations

import argparse
import math
import statistics
import struct
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from read_mylogger_once import (
    DEFAULT_ADDRESS,
    DEFAULT_COUNT,
    ELEMENT_SIZE,
    integer,
)
from uvsc_smoke_test import UvscClient, UvscError, resolve_dll


MIN_INTERVAL_MS = 50
MAX_INTERVAL_MS = 60_000


@dataclass(frozen=True)
class ArraySnapshot:
    sequence: int
    captured_at: datetime
    read_duration_ms: float
    values: tuple[float, ...]

    @property
    def finite_values(self) -> tuple[float, ...]:
        return tuple(value for value in self.values if math.isfinite(value))


class MyLoggerMonitor:
    """Own one UVSC connection and serialize every target-memory read."""

    def __init__(
        self,
        client: UvscClient,
        address: int,
        count: int,
        interval_ms: int,
    ) -> None:
        self.client = client
        self.address = address
        self.count = count
        self._sequence = 0
        self.set_interval_ms(interval_ms)

    @property
    def interval_ms(self) -> int:
        return self._interval_ms

    def set_interval_ms(self, interval_ms: int) -> None:
        if not MIN_INTERVAL_MS <= interval_ms <= MAX_INTERVAL_MS:
            raise ValueError(
                f"刷新间隔必须在 {MIN_INTERVAL_MS}～{MAX_INTERVAL_MS} ms 之间"
            )
        self._interval_ms = interval_ms

    def refresh_once(self) -> ArraySnapshot:
        started = time.perf_counter()
        raw = self.client.read_memory(
            self.address,
            self.count * ELEMENT_SIZE,
        )
        values = struct.unpack(f"<{self.count}f", raw)
        finished = time.perf_counter()
        self._sequence += 1
        return ArraySnapshot(
            sequence=self._sequence,
            captured_at=datetime.now(),
            read_duration_ms=(finished - started) * 1000,
            values=values,
        )

    def run_auto(self, iterations: int) -> None:
        interval_seconds = self.interval_ms / 1000
        next_deadline = time.perf_counter()
        previous_capture: float | None = None
        completed = 0

        print(
            f"[INFO] 自动刷新已启动：interval={self.interval_ms} ms "
            f"({1000 / self.interval_ms:.3g} 次/秒)，"
            f"iterations={iterations or '持续运行'}"
        )
        while iterations == 0 or completed < iterations:
            wait_seconds = next_deadline - time.perf_counter()
            if wait_seconds > 0:
                time.sleep(wait_seconds)

            capture_clock = time.perf_counter()
            snapshot = self.refresh_once()
            actual_interval_ms = (
                None
                if previous_capture is None
                else (capture_clock - previous_capture) * 1000
            )
            print_snapshot(snapshot, actual_interval_ms, "AUTO")
            previous_capture = capture_clock
            completed += 1

            next_deadline += interval_seconds
            now = time.perf_counter()
            if next_deadline < now:
                # If a read overruns, wait one interval instead of issuing bursts.
                next_deadline = now + interval_seconds

    def run_manual(self) -> None:
        print("[INFO] 手动刷新：按 Enter 读取，输入 q 后按 Enter 退出。")
        while True:
            command = input("> ").strip().lower()
            if command in {"q", "quit", "exit"}:
                return
            snapshot = self.refresh_once()
            print_snapshot(snapshot, None, "MANUAL")


def print_snapshot(
    snapshot: ArraySnapshot,
    actual_interval_ms: float | None,
    trigger: str,
) -> None:
    finite = snapshot.finite_values
    interval_text = (
        "first"
        if actual_interval_ms is None
        else f"{actual_interval_ms:.1f} ms"
    )
    if finite:
        stats = (
            f"min={min(finite):.6g}, "
            f"max={max(finite):.6g}, "
            f"mean={statistics.fmean(finite):.6g}"
        )
    else:
        stats = "all values are non-finite"

    print(
        f"[{trigger}] #{snapshot.sequence} "
        f"{snapshot.captured_at.isoformat(timespec='milliseconds')} "
        f"interval={interval_text}, "
        f"read={snapshot.read_duration_ms:.1f} ms, {stats}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="通过 UVSC 自动或手动刷新 myLOGGER0Arr"
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "manual"),
        default="auto",
        help="自动定时刷新或按 Enter 手动刷新（默认：auto）",
    )
    parser.add_argument(
        "--interval-ms",
        type=int,
        default=500,
        help="自动刷新间隔，50～60000 ms（默认：500）",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=0,
        help="自动刷新次数；0 表示持续运行（默认：0）",
    )
    parser.add_argument("--port", type=int, default=35876)
    parser.add_argument("--dll", type=Path)
    parser.add_argument("--address", type=integer, default=DEFAULT_ADDRESS)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace = Path(__file__).resolve().parent

    if not 1 <= args.port <= 65535:
        print(f"[FAIL] 端口超出范围：{args.port}", file=sys.stderr)
        return 2
    if args.count <= 0:
        print(f"[FAIL] 元素数量必须大于 0：{args.count}", file=sys.stderr)
        return 2
    if args.iterations < 0:
        print(f"[FAIL] 刷新次数不能小于 0：{args.iterations}", file=sys.stderr)
        return 2

    client: UvscClient | None = None
    try:
        dll_path = resolve_dll(args.dll, workspace)
        client = UvscClient(dll_path)
        client.initialize()
        handle, connected_port = client.connect(args.port)

        status_result, target_status, status_detail = client.debug_status()
        if status_result != 0:
            raise UvscError(f"无法确认调试状态：{status_detail}")
        target_state = "运行中" if target_status == 1 else "已停止"
        print(
            f"[PASS] 已连接 uVision：port={connected_port}, "
            f"handle={handle}, 目标{target_state}"
        )

        monitor = MyLoggerMonitor(
            client=client,
            address=args.address,
            count=args.count,
            interval_ms=args.interval_ms,
        )
        if args.mode == "manual":
            monitor.run_manual()
        else:
            monitor.run_auto(args.iterations)
        return 0
    except KeyboardInterrupt:
        print("\n[INFO] 已停止刷新。")
        return 0
    except (EOFError, OSError, UvscError, ValueError, struct.error) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    finally:
        if client is not None:
            for warning in client.close():
                print(f"[WARN] 清理阶段：{warning}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
