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
- 在同一张图中显示最多 8 条数组曲线
- 每条曲线独立配置数组名称、元素数量、数据类型和地址
- 在数据源配置中修改 Keil 安装目录或 UVSC DLL 路径
- 在数据源配置中修改 MAP 文件路径
- 根据 Arm linker MAP 文件分别自动解析全局数组地址
- WebSocket 实时推送
- 400 点时域波形
- 最小值、最大值、平均值和读取耗时

默认参数可通过环境变量修改：

```powershell
$env:UVSC_PORT = "35876"
$env:VIEWER_PORT = "35886" # 可选；不设置时自动使用 UVSC_PORT + 10
$env:UVSC_ARRAY_ADDRESS = "0x200041E4"
$env:UVSC_ARRAY_COUNT = "400"
$env:UVSC_ARRAY_NAME = "myLOGGER0Arr"
$env:UVSC_ARRAY_DTYPE = "float32"
$env:UVSC_KEIL_PATH = "E:\Keil\Keil_v5"
$env:UVSC_MAP_FILE = "D:\path\to\application.map"
$env:UVSC_INTERVAL_MS = "500"
$env:UVSC_AUTO_REFRESH = "1"
```
