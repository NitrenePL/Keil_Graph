"""Run the local Keil Array Viewer API and built frontend."""

from __future__ import annotations

import argparse
import os

import uvicorn


def env_port(name: str, default: int) -> int:
    value = os.getenv(name)
    port = default if value is None else int(value, 0)
    if not 1 <= port <= 65535:
        raise ValueError(f"{name} 端口必须在 1～65535 之间：{port}")
    return port


def parse_args() -> argparse.Namespace:
    uvsock_port = env_port("UVSC_PORT", 35876)
    default_viewer_port = env_port("VIEWER_PORT", uvsock_port + 10)
    parser = argparse.ArgumentParser(description="启动 Keil Array Viewer 本地服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port",
        type=int,
        default=default_viewer_port,
        help=(
            "网页服务端口（默认：UVSC_PORT + 10；"
            "可通过 VIEWER_PORT 覆盖）"
        ),
    )
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port 必须在 1～65535 之间")
    return args


def main() -> None:
    args = parse_args()
    uvicorn.run(
        "backend.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
