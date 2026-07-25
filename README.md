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
- Node.js 22.23.1 / npm 10.9.8，由 Volta 根据 `frontend/package.json` 自动选择
- Keil µVision 需要进入 Debug 模式，并启用 UVSOCK

Node 不需要额外的虚拟环境。前端依赖安装在项目自己的
`frontend/node_modules` 中。

## 首次安装

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

Set-Location .\frontend
npm install
npm run build
Set-Location ..
```

## 启动

```powershell
.\.venv\Scripts\python.exe -B .\run_server.py
```

浏览器访问：

```text
http://127.0.0.1:35877
```

构建后的前端由 Python 服务直接提供，只需要启动一个进程。

## 前端开发

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
- 在同一张图中显示最多 8 条数组曲线
- 每条曲线独立配置数组名称、元素数量、数据类型、采样频率和地址
- 根据 Arm linker MAP 文件分别自动解析全局数组地址
- WebSocket 实时推送
- 400 点时域波形
- 可配置采样频率，用于计算横轴时间
- 最小值、最大值、平均值和读取耗时

默认参数可通过环境变量修改：

```powershell
$env:UVSC_PORT = "35876"
$env:UVSC_ARRAY_ADDRESS = "0x200041E4"
$env:UVSC_ARRAY_COUNT = "400"
$env:UVSC_ARRAY_NAME = "myLOGGER0Arr"
$env:UVSC_ARRAY_DTYPE = "float32"
$env:UVSC_SAMPLE_RATE_HZ = "20000"
$env:UVSC_MAP_FILE = "D:\path\to\application.map"
$env:UVSC_INTERVAL_MS = "500"
$env:UVSC_AUTO_REFRESH = "1"
```
