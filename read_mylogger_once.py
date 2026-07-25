"""Read one snapshot of myLOGGER0Arr from the connected STM32 target."""

from __future__ import annotations

import argparse
import csv
import ctypes
import math
import statistics
import struct
import sys
from datetime import datetime
from pathlib import Path

from uvsc_smoke_test import UvscClient, UvscError, resolve_dll


DEFAULT_ADDRESS = 0x200041E4
DEFAULT_COUNT = 400
ELEMENT_SIZE = 4


def integer(value: str) -> int:
    return int(value, 0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="通过 UVSC 只读抓取一次 myLOGGER0Arr float 数组"
    )
    parser.add_argument("--port", type=int, default=35876)
    parser.add_argument("--dll", type=Path)
    parser.add_argument(
        "--address",
        type=integer,
        default=DEFAULT_ADDRESS,
        help="数组首地址，支持 0x 前缀",
    )
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument(
        "--output",
        type=Path,
        help="CSV 输出路径；默认保存到 captures/时间戳.csv",
    )
    return parser.parse_args()


def default_output_path(workspace: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return workspace / "captures" / f"myLOGGER0Arr_{timestamp}.csv"


def write_csv(path: Path, values: tuple[float, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(("index", "value"))
        writer.writerows(enumerate(values))


def preview(values: tuple[float, ...], width: int = 8) -> str:
    if len(values) <= width * 2:
        return ", ".join(f"{value:.7g}" for value in values)
    head = ", ".join(f"{value:.7g}" for value in values[:width])
    tail = ", ".join(f"{value:.7g}" for value in values[-width:])
    return f"{head}, ... , {tail}"


def main() -> int:
    args = parse_args()
    workspace = Path(__file__).resolve().parent
    output_path = (
        args.output.resolve()
        if args.output
        else default_output_path(workspace)
    )

    if not 1 <= args.port <= 65535:
        print(f"[FAIL] 端口超出范围：{args.port}", file=sys.stderr)
        return 2
    if args.count <= 0:
        print(f"[FAIL] 元素数量必须大于 0：{args.count}", file=sys.stderr)
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

        byte_count = args.count * ELEMENT_SIZE
        raw = client.read_memory(args.address, byte_count)
        values = struct.unpack(f"<{args.count}f", raw)
        write_csv(output_path, values)

        finite_values = [value for value in values if math.isfinite(value)]
        print(
            f"[PASS] 已读取 myLOGGER0Arr：地址=0x{args.address:08X}, "
            f"float32={len(values)}, 字节={len(raw)}"
        )
        print(f"[DATA] {preview(values)}")
        if finite_values:
            print(
                "[STAT] "
                f"min={min(finite_values):.7g}, "
                f"max={max(finite_values):.7g}, "
                f"mean={statistics.fmean(finite_values):.7g}, "
                f"non-finite={len(values) - len(finite_values)}"
            )
        else:
            print(f"[WARN] {len(values)} 个值全部为 NaN 或 Infinity")
        print(f"[PASS] CSV 已保存：{output_path}")
        return 0
    except (OSError, UvscError, struct.error) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    finally:
        if client is not None:
            for warning in client.close():
                print(f"[WARN] 清理阶段：{warning}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
