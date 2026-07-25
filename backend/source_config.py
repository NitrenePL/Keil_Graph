"""Array data types and Keil linker MAP symbol resolution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DataType:
    name: str
    struct_code: str
    byte_size: int


DATA_TYPES = {
    item.name: item
    for item in (
        DataType("float32", "f", 4),
        DataType("float64", "d", 8),
        DataType("int8", "b", 1),
        DataType("uint8", "B", 1),
        DataType("int16", "h", 2),
        DataType("uint16", "H", 2),
        DataType("int32", "i", 4),
        DataType("uint32", "I", 4),
    )
}


@dataclass(frozen=True)
class ArraySource:
    name: str
    address: int
    count: int
    data_type: str
    sample_rate_hz: float

    def as_status(self) -> dict[str, object]:
        return {
            "array_name": self.name,
            "address": self.address,
            "address_hex": f"0x{self.address:08X}",
            "count": self.count,
            "dtype": self.data_type,
            "sample_rate_hz": self.sample_rate_hz,
        }


@dataclass(frozen=True)
class MapSymbol:
    name: str
    address: int
    byte_size: int


class MapSymbolResolver:
    """Resolve global data symbols from an Arm linker MAP file."""

    _DATA_SYMBOL = re.compile(
        r"^\s*(?P<name>\S+)\s+"
        r"(?P<address>0x[0-9A-Fa-f]+)\s+"
        r"Data\s+"
        r"(?P<size>\d+)\b"
    )

    def __init__(self, map_file: Path) -> None:
        self.map_file = map_file

    def resolve(self, symbol_name: str) -> MapSymbol:
        normalized = symbol_name.strip()
        if not normalized:
            raise ValueError("数组名称不能为空")
        if not self.map_file.is_file():
            raise ValueError(f"找不到 MAP 文件：{self.map_file}")

        with self.map_file.open("r", encoding="utf-8", errors="replace") as file:
            for line in file:
                match = self._DATA_SYMBOL.match(line)
                if match is None or match.group("name") != normalized:
                    continue
                return MapSymbol(
                    name=normalized,
                    address=int(match.group("address"), 16),
                    byte_size=int(match.group("size")),
                )

        raise ValueError(
            f"MAP 文件中找不到数据符号 {normalized!r}：{self.map_file}"
        )
