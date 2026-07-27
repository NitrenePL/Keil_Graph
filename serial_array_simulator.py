"""Send deterministic float arrays to a virtual serial port for development.

The default COM30 endpoint is intended to be paired with COM31 by a virtual
serial-port driver. This program owns the sending endpoint only; the future
viewer-side serial reader should open COM31.
"""

from __future__ import annotations

import argparse
import math
import struct
import time
import zlib

import serial


MAGIC = b"KAV1"
PROTOCOL_VERSION = 2
# magic, version, channel count, samples/channel, sequence, payload byte
# count. All multi-byte fields are little-endian.
HEADER = struct.Struct("<4sBBHII")
CRC = struct.Struct("<I")
BITS_PER_BYTE_8N1 = 10
DEFAULT_PORT = "COM30"
DEFAULT_BAUDRATE = 460_800
DEFAULT_CHANNELS = 3
DEFAULT_SAMPLES = 400
DEFAULT_INTERVAL_SECONDS = 1.0


def build_values(
    sequence: int,
    channel_count: int,
    samples_per_channel: int,
) -> tuple[float, ...]:
    """Build stable, recognisable waveforms in channel-major order."""
    values: list[float] = []
    frame_phase = sequence * 0.06
    for channel in range(channel_count):
        for sample in range(samples_per_channel):
            position = sample / max(samples_per_channel - 1, 1)
            if channel == 0:
                value = 5.0 * (2.0 * ((position + frame_phase) % 1.0) - 1.0)
            elif channel == 1:
                value = -0.8 + 1.45 * math.sin(
                    2.0 * math.pi * (position + frame_phase)
                )
            elif channel == 2:
                value = 0.0
            else:
                value = (channel - 2) * math.cos(
                    2.0 * math.pi * (position + frame_phase)
                )
            values.append(value)
    return tuple(values)


def build_frame(
    sequence: int,
    channel_count: int,
    samples_per_channel: int,
) -> bytes:
    """Encode one CRC-protected KAV1 binary frame."""
    values = build_values(sequence, channel_count, samples_per_channel)
    payload = struct.pack(f"<{len(values)}f", *values)
    header = HEADER.pack(
        MAGIC,
        PROTOCOL_VERSION,
        channel_count,
        samples_per_channel,
        sequence,
        len(payload),
    )
    return header + payload + CRC.pack(zlib.crc32(header + payload))


def frame_size(channel_count: int, samples_per_channel: int) -> int:
    return HEADER.size + channel_count * samples_per_channel * 4 + CRC.size


def minimum_baudrate(frame_bytes: int, interval_seconds: float) -> int:
    """Return a 30% headroom estimate for 8N1 serial transport."""
    return math.ceil(frame_bytes * BITS_PER_BYTE_8N1 / interval_seconds / 0.7)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="通过虚拟串口发送 KAV1 float32 数组帧",
    )
    parser.add_argument("--port", default=DEFAULT_PORT, help="发送端串口")
    parser.add_argument(
        "--baudrate", type=int, default=DEFAULT_BAUDRATE, help="波特率"
    )
    parser.add_argument(
        "--channels", type=int, default=DEFAULT_CHANNELS, help="float32 通道数"
    )
    parser.add_argument(
        "--samples", type=int, default=DEFAULT_SAMPLES, help="每通道采样点数"
    )
    parser.add_argument(
        "--interval", type=float, default=DEFAULT_INTERVAL_SECONDS,
        help="发帧间隔（秒）",
    )
    parser.add_argument(
        "--frames", type=int, default=0,
        help="发送帧数；0 表示持续发送",
    )
    args = parser.parse_args()
    if not 1 <= args.channels <= 255:
        parser.error("--channels 必须在 1 到 255 之间")
    if not 1 <= args.samples <= 65_535:
        parser.error("--samples 必须在 1 到 65535 之间")
    if args.interval <= 0:
        parser.error("--interval 必须大于 0")
    if args.baudrate <= 0:
        parser.error("--baudrate 必须大于 0")
    if args.frames < 0:
        parser.error("--frames 不能小于 0")
    return args


def main() -> None:
    args = parse_args()
    size = frame_size(args.channels, args.samples)
    wire_seconds = size * BITS_PER_BYTE_8N1 / args.baudrate
    recommended = minimum_baudrate(size, args.interval)
    utilization = wire_seconds / args.interval
    print(
        "[INFO] KAV1 帧："
        f"{args.channels} 通道 × {args.samples} 点 × float32，{size} B/帧"
    )
    print(
        f"[INFO] {args.baudrate} baud、8N1 的理论在线时间："
        f"{wire_seconds * 1000:.1f} ms/帧（链路占用 {utilization:.1%}）"
    )
    print(
        f"[INFO] 在 {args.interval:g} s 发帧周期下，建议波特率至少 {recommended}"
    )
    if utilization > 0.7:
        print("[WARN] 当前波特率余量不足 30%，建议提高 --baudrate 或增大发送间隔")

    sequence = 0
    next_deadline = time.monotonic()
    try:
        with serial.Serial(
            port=args.port,
            baudrate=args.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=1,
            write_timeout=max(2.0, wire_seconds * 2.0),
        ) as port:
            print(f"[INFO] 已打开 {port.name}，按 Ctrl+C 停止")
            while args.frames == 0 or sequence < args.frames:
                now = time.monotonic()
                if now < next_deadline:
                    time.sleep(next_deadline - now)

                started = time.perf_counter()
                frame = build_frame(
                    sequence=sequence,
                    channel_count=args.channels,
                    samples_per_channel=args.samples,
                )
                written = port.write(frame)
                port.flush()
                elapsed_ms = (time.perf_counter() - started) * 1000
                if written != len(frame):
                    raise serial.SerialTimeoutException(
                        f"仅写入 {written}/{len(frame)} B"
                    )
                print(
                    f"[TX] seq={sequence} bytes={written} "
                    f"write={elapsed_ms:.1f} ms"
                )
                sequence += 1
                next_deadline += args.interval
                if next_deadline < time.monotonic():
                    next_deadline = time.monotonic() + args.interval
    except serial.SerialException as exc:
        raise SystemExit(f"[ERROR] 无法使用 {args.port}: {exc}") from exc
    except KeyboardInterrupt:
        print("\n[INFO] 已停止发送")


if __name__ == "__main__":
    main()
