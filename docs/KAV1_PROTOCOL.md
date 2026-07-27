# KAV1 串口数组帧协议（Version 2）

KAV1（Keil Array Viewer v1）是本项目用于在串口上发送多通道波形数组的
二进制帧协议。接收端将一帧数据转换为网页中的一次波形快照。

本协议当前由 [serial_array_simulator.py](../serial_array_simulator.py) 发送，
并由 [backend/serial_service.py](../backend/serial_service.py) 接收。

## 传输约定

- 串口参数：建议 `8N1`、无流控。
- 所有多字节整数和 `float32` 都使用 **little-endian（小端）**。
- 浮点数使用 IEEE 754 单精度格式。
- 数据帧之间无需额外的换行、转义或分隔符。
- 接收端通过固定帧头 `KAV1` 重新同步，因此允许从任意字节位置开始接收。

## 帧格式

帧由 16 字节固定帧头、可变长数据区及 4 字节 CRC32 组成。

| 偏移 | 字段 | 类型 | 字节数 | 说明 |
|---:|---|---|---:|---|
| 0 | `magic` | `char[4]` | 4 | 固定 ASCII：`KAV1`（`0x4B 0x41 0x56 0x31`） |
| 4 | `version` | `uint8_t` | 1 | 当前固定为 `2` |
| 5 | `channel_count` | `uint8_t` | 1 | 帧内通道数，必须大于 0 |
| 6 | `sample_count` | `uint16_t` | 2 | 每个通道的采样点数 |
| 8 | `sequence` | `uint32_t` | 4 | 发送端单调递增帧序号；回绕允许 |
| 12 | `payload_bytes` | `uint32_t` | 4 | 数据区字节数 |
| 16 | `payload` | `float32[]` | 可变 | 多通道数组数据，见下文 |
| 16 + `payload_bytes` | `crc32` | `uint32_t` | 4 | 帧头和数据区的 CRC32 |

固定帧头可写成：

```c
struct KAV1Header {
    char     magic[4];        // "KAV1"
    uint8_t  version;         // 2
    uint8_t  channel_count;
    uint16_t sample_count;    // little-endian
    uint32_t sequence;        // little-endian
    uint32_t payload_bytes;   // little-endian
};
```

不要直接依赖 MCU 编译器对上述 C 结构体的默认对齐；发送时应逐字段写入，或使用
明确的 packed 结构并确认其长度恰好为 16 字节。

## 数据区

数据区按 **通道优先（channel-major）** 排列。假设有 `C` 个通道、每通道 `N`
个点，数据区为：

```text
channel 0: sample[0], sample[1], ... sample[N - 1]
channel 1: sample[0], sample[1], ... sample[N - 1]
...
channel C - 1: sample[0], ..., sample[N - 1]
```

接收端中配置的“串口通道号”即这里的通道索引，范围为 `0` 至
`channel_count - 1`。同一通道号不能被配置给两条曲线。

Version 2 的约束：

```text
payload_bytes == channel_count × sample_count × 4
```

`4` 是一个 `float32` 的字节数。当前网页接收端仅支持 `float32`，并要求每条已
配置曲线的点数等于帧内 `sample_count`。

## CRC32

末尾 `crc32` 使用标准 IEEE CRC-32（与 Python `zlib.crc32()` 一致）。CRC 的
输入范围为：

```text
header[0:16] + payload[0:payload_bytes]
```

即 CRC 字段本身不参与计算。发送时写入的 CRC 值也采用小端字节序。

若 CRC、版本、帧头或数据区长度校验失败，接收端丢弃当前候选帧，并继续搜索下一个
`KAV1` 帧头。

## 帧长与波特率

总帧长：

```text
frame_bytes = 16 + channel_count × sample_count × 4 + 4
```

以常用的 `8N1` 串口为例，一个字节在线路上约需 10 bit，因此理论发送时间为：

```text
wire_seconds = frame_bytes × 10 / baudrate
```

默认模拟器发送 `3 × float32[400]`：

```text
frame_bytes = 16 + 3 × 400 × 4 + 4 = 4820 B
```

在 `115200 baud` 时理论发送时间约为 `418 ms`。若每秒发送一帧，链路占用约 42%；
建议预留至少 30% 的带宽余量。提高发送频率时可使用 `230400` 或更高波特率。

## 发送流程

1. 填入固定帧头，`magic` 为 `KAV1`、`version` 为 `2`。
2. 以通道优先顺序连续写入全部 `float32` 数据。
3. 令 `payload_bytes = channel_count × sample_count × 4`。
4. 对帧头和数据区计算 CRC32。
5. 以小端 `uint32_t` 追加 CRC32，并将整帧写入串口。

## Python 编码示例

```python
import struct
import zlib

HEADER = struct.Struct("<4sBBHII")
CRC = struct.Struct("<I")

channels = [array0, array1, array2]  # 每项均为长度 400 的 float 序列
channel_count = len(channels)
sample_count = len(channels[0])
payload = struct.pack(
    f"<{channel_count * sample_count}f",
    *(value for channel in channels for value in channel),
)
header = HEADER.pack(
    b"KAV1", 2, channel_count, sample_count, sequence, len(payload),
)
frame = header + payload + CRC.pack(zlib.crc32(header + payload))
serial_port.write(frame)
```

## MCU 发送建议

- 不要将文本格式的浮点数（例如 CSV）用于高频数组传输；KAV1 的二进制 `float32`
  可显著减少带宽和解析开销。
- DMA 发送前应保证整个数组快照在发送期间不被并发修改；可使用双缓冲、复制快照或
  临界区保护。
- `sequence` 每发送一帧递增，便于主机识别丢帧。
- 协议不携带时间戳；网页快照时间使用主机收到完整帧的本地时间。

## 接收端行为

本项目的接收端有如下限制：

- 当前最多配置 8 条网页曲线。
- 单帧数据区最大接受 `65536 B`。
- 会缓存不完整串口数据，并在后续 `read()` 中继续拼帧。
- 在网页中关闭“自动刷新”时，接收端不发布新的自动波形快照；重新开启后继续使用后续
  有效帧。
