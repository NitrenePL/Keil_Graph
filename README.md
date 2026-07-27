# Keil Array Viewer

通过 Keil µVision UVSOCK/UVSC 读取 STM32 目标内存，并在本地浏览器显示数组波形。

当前默认数据源：

- 工程：`D:\STM32_prj\APF_Display_F4`
- 数组：`myLOGGER0Arr`
- 类型：`float32[400]`
- 地址：`0x200041E4`
- UVSOCK 端口：`35876`

## 环境

- Python 3.12，使用仓库现有的 `.venv`
- Keil µVision 需要进入 Debug 模式，并启用 UVSOCK

仓库已包含构建后的 `frontend/dist`，普通使用不需要安装 Node.js
或 npm。只有修改并重新构建前端时，才需要 Node.js 22.23.1 /
npm 10.9.8；Volta 会根据 `frontend/package.json` 自动选择版本。

## 首次安装

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 启动

```powershell
.\.venv\Scripts\python.exe -B .\run_server.py
```

浏览器访问：

```text
http://127.0.0.1:35886
```

网页服务端口默认为 Keil UVSOCK 端口加 10。例如
`UVSC_PORT=35876` 时网页地址为 `http://127.0.0.1:35886`。
可通过 `VIEWER_PORT` 或启动参数 `--port` 单独覆盖。构建后的前端由
Python 服务直接提供，只需要启动一个进程。

数据源、MAP 文件、Keil/DLL 路径、自动刷新开关和刷新间隔会自动
保存到仓库根目录的 `.keil-array-viewer.json`，下次启动时自动恢复。
该文件包含本机路径，已被 Git 忽略。可通过 `VIEWER_CONFIG_FILE`
指定其他保存位置。

## 虚拟串口数组发送模拟器

在接入单片机串口前，可先使用虚拟串口对（例如 COM30 ↔ COM31）验证数组
传输。发送端运行在 COM30，后续网页服务的串口接收端将连接 COM31：

```powershell
.\.venv\Scripts\python.exe -B .\serial_array_simulator.py --port COM30
```

默认每秒发送一帧：3 个 `float32[400]` 通道。帧为小端二进制 `KAV1` 格式：
`magic(4) + version(1) + channel_count(1) + sample_count(2) + sequence(4) +
captured_at_ms(8) + payload_bytes(4) + float32 payload + crc32(4)`。

默认帧长为 `4828 B`。串口使用 8N1 时每字节在线传输需要 10 bit，因此
`115200 baud` 理论传输时间约 `419 ms`，1 秒发送一次时链路占用约 42%；
有充足余量。若以后提高刷新频率，可改用 `230400` 或更高波特率：

```powershell
.\.venv\Scripts\python.exe -B .\serial_array_simulator.py `
  --port COM30 --baudrate 230400 --interval 0.2
```

## 前端开发

前端开发依赖安装在项目自己的 `frontend/node_modules` 中，Node
不需要额外的虚拟环境。首次开发前执行：

```powershell
Set-Location .\frontend
npm install
npm run build
Set-Location ..
```

终端 1：

```powershell
.\.venv\Scripts\python.exe -B .\run_server.py
```

终端 2：

```powershell
Set-Location .\frontend
npm run dev
```

浏览器访问 `http://127.0.0.1:5173`。Vite 会把 `/api` 和 `/ws`
代理到 Python 服务。

## 功能

- 自动刷新开关
- `50～60000 ms` 刷新间隔设置
- 手动立即刷新
- 暂停自动刷新后，将指定通道按整数采样频率导出为无表头 CSV
- 在时域图下方显示 H0 直流分量及最多 1000 次谐波的 Fourier 柱状图
- Fourier 支持整数采样/基波频率、Rectangular/Hann/Hamming 和 RMS/Amp
- 整数周期同步采样时自动使用 Rectangular，避免窗函数产生相邻谐波泄漏
- 在同一张图中显示最多 8 条数组曲线
- 每条曲线独立配置数组名称、元素数量、数据类型和地址
- 在波形左侧为每条曲线独立选择乘或除一个倍率，统一缩放波形、测量、CSV 和 Fourier
- 数据源配置可直接保存而不读取目标，弹窗内可测试未保存配置，页面顶部可测试已保存配置
- 在数据源配置中修改 Keil 安装目录或 UVSC DLL 路径
- 在数据源配置中修改 MAP 文件路径
- 根据 Arm linker MAP 文件分别自动解析全局数组地址
- WebSocket 实时推送
- 400 点时域波形
- 鼠标左键可固定最多两条 X1/X2 纵向游标，并在侧边显示索引及各通道数值
- 最小值、最大值、平均值和读取耗时

默认参数可通过环境变量修改：

```powershell
$env:UVSC_PORT = "35876"
$env:VIEWER_PORT = "35886" # 可选；不设置时自动使用 UVSC_PORT + 10
$env:VIEWER_CONFIG_FILE = "D:\path\to\viewer-config.json" # 可选
$env:UVSC_ARRAY_ADDRESS = "0x200041E4"
$env:UVSC_ARRAY_COUNT = "400"
$env:UVSC_ARRAY_NAME = "myLOGGER0Arr"
$env:UVSC_ARRAY_DTYPE = "float32"
$env:UVSC_KEIL_PATH = "E:\Keil\Keil_v5"
$env:UVSC_MAP_FILE = "D:\path\to\application.map"
$env:UVSC_INTERVAL_MS = "500"
$env:UVSC_AUTO_REFRESH = "1"
```
