"""Read-only smoke test for Keil uVision's UVSC interface.

The script verifies the UVSC/UVSOCK DLL versions, connects to an already
running uVision instance, and queries whether the debug target is running.
It never starts/stops the target, enters/leaves debug mode, or writes memory.
"""

from __future__ import annotations

import argparse
import ctypes
import platform
import sys
from pathlib import Path


UVSC_STATUS_NAMES = {
    0: "SUCCESS",
    1: "FAILED",
    2: "NOT_SUPPORTED",
    3: "NOT_INIT",
    4: "TIMEOUT",
    5: "INVALID_CONTEXT",
    6: "INVALID_PARAM",
    7: "BUFFER_TOO_SMALL",
    8: "CALLBACK_IN_USE",
    9: "COMMAND_ERROR",
}

UVSC_SUCCESS = 0
UVSC_RUNMODE_NORMAL = 0
UVSC_AUTO_PORT_MIN = 5101
UVSC_AUTO_PORT_MAX = 5110
UVSC_MAX_API_STR_SIZE = 1024

UVSC_CALLBACK = ctypes.CFUNCTYPE(
    None,
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_void_p,
)
UVSC_LOG_CALLBACK = ctypes.CFUNCTYPE(
    None,
    ctypes.c_char_p,
    ctypes.c_int,
)


class UvscError(RuntimeError):
    """Raised when an UVSC API call fails."""


def status_text(status: int) -> str:
    return f"{UVSC_STATUS_NAMES.get(status, 'UNKNOWN')} ({status})"


def decode_uvsc_text(data: bytes) -> str:
    for encoding in ("mbcs", "utf-8"):
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            pass
    return data.decode("latin-1", errors="replace")


def default_dll_candidates(workspace: Path) -> list[Path]:
    dll_name = "UVSC64.dll" if ctypes.sizeof(ctypes.c_void_p) == 8 else "UVSC.dll"
    return [
        Path(r"E:\Keil\Keil_v5\UV4") / dll_name,
        workspace / "apnt_198" / "UVSOCK" / "bin" / dll_name,
    ]


def resolve_dll(explicit_path: Path | None, workspace: Path) -> Path:
    candidates = [explicit_path] if explicit_path else default_dll_candidates(workspace)
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate.resolve()

    checked = "\n".join(f"  - {path}" for path in candidates if path is not None)
    raise UvscError(f"找不到 UVSC DLL，已检查：\n{checked}")


class UvscClient:
    def __init__(self, dll_path: Path) -> None:
        try:
            self._dll = ctypes.CDLL(str(dll_path))
        except OSError as exc:
            raise UvscError(f"无法加载 {dll_path}: {exc}") from exc

        self._configure_api()
        self._initialized = False
        self._handle: int | None = None
        self.callback_events: list[int] = []
        self.log_messages: list[str] = []

        # Keep callback objects alive for the complete connection lifetime.
        self._callback = UVSC_CALLBACK(self._on_callback)
        self._log_callback = UVSC_LOG_CALLBACK(self._on_log)

    def _configure_api(self) -> None:
        dll = self._dll

        dll.UVSC_Version.argtypes = [
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
        ]
        dll.UVSC_Version.restype = None

        dll.UVSC_Init.argtypes = [ctypes.c_int, ctypes.c_int]
        dll.UVSC_Init.restype = ctypes.c_int

        dll.UVSC_UnInit.argtypes = []
        dll.UVSC_UnInit.restype = ctypes.c_int

        dll.UVSC_OpenConnection.argtypes = [
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_char_p,
            ctypes.c_int,
            UVSC_CALLBACK,
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_int,
            UVSC_LOG_CALLBACK,
        ]
        dll.UVSC_OpenConnection.restype = ctypes.c_int

        dll.UVSC_CloseConnection.argtypes = [ctypes.c_int, ctypes.c_int]
        dll.UVSC_CloseConnection.restype = ctypes.c_int

        dll.UVSC_GetLastError.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        dll.UVSC_GetLastError.restype = ctypes.c_int

        dll.UVSC_DBG_STATUS.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
        ]
        dll.UVSC_DBG_STATUS.restype = ctypes.c_int

    def _on_callback(
        self,
        _custom_data: int | None,
        callback_type: int,
        _callback_data: int | None,
    ) -> None:
        # Never call an UVSC API from this callback; it can run on a DLL thread.
        self.callback_events.append(callback_type)

    def _on_log(self, message: bytes | None, length: int) -> None:
        if message and length > 0:
            raw = ctypes.string_at(message, length)
            self.log_messages.append(decode_uvsc_text(raw).rstrip())

    def versions(self) -> tuple[int, int]:
        uvsc_version = ctypes.c_uint()
        uvsock_version = ctypes.c_uint()
        self._dll.UVSC_Version(
            ctypes.byref(uvsc_version),
            ctypes.byref(uvsock_version),
        )
        return uvsc_version.value, uvsock_version.value

    def initialize(self) -> None:
        result = self._dll.UVSC_Init(UVSC_AUTO_PORT_MIN, UVSC_AUTO_PORT_MAX)
        if result != UVSC_SUCCESS:
            raise UvscError(f"UVSC_Init 失败：{status_text(result)}")
        self._initialized = True

    def connect(self, port: int) -> tuple[int, int]:
        handle = ctypes.c_int()
        selected_port = ctypes.c_int(port)
        result = self._dll.UVSC_OpenConnection(
            None,
            ctypes.byref(handle),
            ctypes.byref(selected_port),
            None,
            UVSC_RUNMODE_NORMAL,
            self._callback,
            None,
            None,
            0,
            self._log_callback,
        )
        if result != UVSC_SUCCESS:
            detail = ""
            if self.log_messages:
                detail = f"\nUVSC 日志：{self.log_messages[-1]}"
            raise UvscError(
                f"连接 uVision 端口 {port} 失败：{status_text(result)}{detail}"
            )

        self._handle = handle.value
        return handle.value, selected_port.value

    def debug_status(self) -> tuple[int, int | None, str | None]:
        if self._handle is None:
            raise UvscError("尚未连接 uVision")

        target_status = ctypes.c_int()
        result = self._dll.UVSC_DBG_STATUS(
            self._handle,
            ctypes.byref(target_status),
        )
        if result == UVSC_SUCCESS:
            return result, target_status.value, None
        return result, None, self.last_error()

    def last_error(self) -> str | None:
        if self._handle is None:
            return None

        operation = ctypes.c_int()
        uv_status = ctypes.c_int()
        message = ctypes.create_string_buffer(UVSC_MAX_API_STR_SIZE)
        result = self._dll.UVSC_GetLastError(
            self._handle,
            ctypes.byref(operation),
            ctypes.byref(uv_status),
            message,
            len(message),
        )
        if result != UVSC_SUCCESS:
            return f"UVSC_GetLastError 失败：{status_text(result)}"

        text = decode_uvsc_text(message.value).strip()
        fields = f"operation={operation.value}, uv_status={uv_status.value}"
        return f"{text} ({fields})" if text else fields

    def close(self) -> list[str]:
        warnings: list[str] = []

        if self._handle is not None:
            # terminate=0 keeps the user's existing uVision process open.
            result = self._dll.UVSC_CloseConnection(self._handle, 0)
            if result != UVSC_SUCCESS:
                warnings.append(f"UVSC_CloseConnection：{status_text(result)}")
            self._handle = None

        if self._initialized:
            result = self._dll.UVSC_UnInit()
            if result != UVSC_SUCCESS:
                warnings.append(f"UVSC_UnInit：{status_text(result)}")
            self._initialized = False

        return warnings


def format_version(version: int) -> str:
    return f"{version // 100}.{version % 100:02d}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="只读测试 Python 到 Keil uVision 的 UVSC 连接"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=35876,
        help="uVision 中配置的 UVSOCK 端口（默认：35876）",
    )
    parser.add_argument(
        "--dll",
        type=Path,
        help="UVSC64.dll/UVSC.dll 路径；默认优先使用 Keil 安装目录",
    )
    parser.add_argument(
        "--version-only",
        action="store_true",
        help="仅加载 DLL 并读取版本，不连接 uVision",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace = Path(__file__).resolve().parent

    if not 1 <= args.port <= 65535:
        print(f"[FAIL] 端口超出范围：{args.port}", file=sys.stderr)
        return 2

    client: UvscClient | None = None
    try:
        dll_path = resolve_dll(args.dll, workspace)
        print(
            f"[INFO] Python {platform.python_version()} "
            f"({ctypes.sizeof(ctypes.c_void_p) * 8}-bit)"
        )
        print(f"[INFO] DLL: {dll_path}")

        client = UvscClient(dll_path)
        uvsc_version, uvsock_version = client.versions()
        print(
            "[PASS] DLL 加载成功："
            f"UVSC {format_version(uvsc_version)}, "
            f"UVSOCK {format_version(uvsock_version)}"
        )

        if args.version_only:
            return 0

        client.initialize()
        print(
            "[PASS] UVSC 初始化成功："
            f"自动端口范围 {UVSC_AUTO_PORT_MIN}-{UVSC_AUTO_PORT_MAX}"
        )

        handle, connected_port = client.connect(args.port)
        print(
            f"[PASS] 已连接 uVision：port={connected_port}, handle={handle}"
        )

        result, target_status, detail = client.debug_status()
        if result == UVSC_SUCCESS:
            state = "运行中" if target_status == 1 else "已停止"
            print(f"[PASS] 调试状态可读取：目标{state}")
        else:
            print(
                "[WARN] 已建立 UVSC 连接，但无法读取调试状态："
                f"{status_text(result)}"
            )
            if detail:
                print(f"[WARN] uVision 返回：{detail}")
            print("[INFO] 通常是因为 uVision 当前尚未进入 Debug 模式。")

        return 0
    except UvscError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    finally:
        if client is not None:
            for warning in client.close():
                print(f"[WARN] 清理阶段：{warning}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
