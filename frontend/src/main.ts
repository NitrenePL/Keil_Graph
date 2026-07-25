import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import "./style.css";

interface ViewerStatus {
  connected: boolean;
  target_state: "running" | "stopped" | "unknown";
  port: number;
  address: number;
  address_hex: string;
  count: number;
  dtype: string;
  auto_refresh: boolean;
  interval_ms: number;
  last_error: string | null;
  latest_sequence: number | null;
}

interface Snapshot {
  sequence: number;
  captured_at: string;
  trigger: "auto" | "manual";
  address: number;
  count: number;
  read_duration_ms: number;
  values: number[];
  stats: {
    min: number | null;
    max: number | null;
    mean: number | null;
    non_finite: number;
  };
}

interface SocketEvent {
  type: "status" | "snapshot";
  data: ViewerStatus | Snapshot;
}

const byId = <T extends HTMLElement>(id: string): T => {
  const element = document.getElementById(id);
  if (!element) throw new Error(`Missing element #${id}`);
  return element as T;
};

const connectionDot = byId<HTMLSpanElement>("connection-dot");
const connectionLabel = byId<HTMLSpanElement>("connection-label");
const connectionDetail = byId<HTMLElement>("connection-detail");
const autoRefresh = byId<HTMLInputElement>("auto-refresh");
const intervalInput = byId<HTMLInputElement>("interval-ms");
const sampleRateInput = byId<HTMLInputElement>("sample-rate");
const applyButton = byId<HTMLButtonElement>("apply-config");
const manualButton = byId<HTMLButtonElement>("manual-refresh");
const controlMessage = byId<HTMLParagraphElement>("control-message");

let latestSnapshot: Snapshot | null = null;
let reconnectTimer: number | undefined;

const plot = new uPlot(
  {
    width: 960,
    height: 410,
    cursor: {
      drag: { x: true, y: false },
    },
    scales: {
      x: { time: false },
      y: { auto: true },
    },
    axes: [
      {
        stroke: "#718096",
        grid: { stroke: "rgba(132, 150, 171, 0.12)" },
        label: "时间 (ms)",
        labelSize: 28,
      },
      {
        stroke: "#718096",
        grid: { stroke: "rgba(132, 150, 171, 0.12)" },
        label: "幅值",
        labelSize: 38,
        size: 58,
      },
    ],
    series: [
      {},
      {
        label: "myLOGGER0Arr",
        stroke: "#24d6a0",
        width: 2,
        points: { show: false },
      },
    ],
  },
  [[], []],
  byId<HTMLDivElement>("waveform"),
);

const resizePlot = (): void => {
  const container = byId<HTMLDivElement>("waveform");
  plot.setSize({
    width: Math.max(container.clientWidth, 320),
    height: Math.max(container.clientHeight, 300),
  });
};

new ResizeObserver(resizePlot).observe(byId("waveform"));

const formatValue = (value: number | null): string =>
  value === null ? "—" : value.toFixed(6);

const setMessage = (message: string, error = false): void => {
  controlMessage.textContent = message;
  controlMessage.classList.toggle("error", error);
};

const updateStatus = (status: ViewerStatus): void => {
  connectionDot.classList.toggle("connected", status.connected);
  connectionLabel.textContent = status.connected ? "UVSC 已连接" : "UVSC 未连接";
  connectionDetail.textContent = status.last_error ?? `UVSOCK :${status.port}`;
  autoRefresh.checked = status.auto_refresh;
  intervalInput.value = String(status.interval_ms);
  byId("array-meta").textContent = `${status.dtype} · ${status.count} points`;
  byId("target-state").textContent =
    status.target_state === "running"
      ? "运行中"
      : status.target_state === "stopped"
        ? "已停止"
        : "未知";
  byId("memory-address").textContent = status.address_hex;
  byId("uvsock-port").textContent = String(status.port);
};

const updateSnapshot = (snapshot: Snapshot): void => {
  latestSnapshot = snapshot;
  byId("min-value").textContent = formatValue(snapshot.stats.min);
  byId("max-value").textContent = formatValue(snapshot.stats.max);
  byId("mean-value").textContent = formatValue(snapshot.stats.mean);
  byId("sequence").textContent = `Snapshot #${snapshot.sequence}`;
  byId("read-duration").textContent =
    `Read ${snapshot.read_duration_ms.toFixed(1)} ms`;

  const captureTime = new Date(snapshot.captured_at);
  byId("capture-time").textContent = captureTime.toLocaleTimeString("zh-CN", {
    hour12: false,
    fractionalSecondDigits: 3,
  });
  const badge = byId("trigger-badge");
  badge.textContent = snapshot.trigger === "manual" ? "手动刷新" : "自动刷新";
  badge.classList.toggle("manual", snapshot.trigger === "manual");

  const sampleRate = Math.max(Number(sampleRateInput.value) || 20_000, 1);
  const xValues = snapshot.values.map((_, index) => (index / sampleRate) * 1000);
  plot.setData([xValues, snapshot.values]);
  const windowMs = (snapshot.values.length / sampleRate) * 1000;
  byId("sample-window").textContent = `Window ${windowMs.toFixed(2)} ms`;
};

const requestJson = async <T>(
  url: string,
  options?: RequestInit,
): Promise<T> => {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) {
    const detail =
      typeof payload.detail === "string" ? payload.detail : response.statusText;
    throw new Error(detail);
  }
  return payload as T;
};

const loadInitialState = async (): Promise<void> => {
  updateStatus(await requestJson<ViewerStatus>("/api/status"));
  try {
    updateSnapshot(await requestJson<Snapshot>("/api/snapshot"));
  } catch {
    // The worker may still be connecting; WebSocket will deliver the first sample.
  }
};

const connectSocket = (): void => {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${protocol}//${location.host}/ws`);

  socket.addEventListener("open", () => {
    if (reconnectTimer !== undefined) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = undefined;
    }
  });
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data) as SocketEvent;
    if (message.type === "status") {
      updateStatus(message.data as ViewerStatus);
    } else if (message.type === "snapshot") {
      updateSnapshot(message.data as Snapshot);
    }
  });
  socket.addEventListener("close", () => {
    connectionDot.classList.remove("connected");
    connectionLabel.textContent = "服务连接中断";
    reconnectTimer = window.setTimeout(connectSocket, 1500);
  });
};

applyButton.addEventListener("click", async () => {
  applyButton.disabled = true;
  setMessage("正在应用设置…");
  try {
    const status = await requestJson<ViewerStatus>("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        auto_refresh: autoRefresh.checked,
        interval_ms: Number(intervalInput.value),
      }),
    });
    updateStatus(status);
    setMessage(
      status.auto_refresh
        ? `自动刷新：${status.interval_ms} ms`
        : "自动刷新已关闭",
    );
  } catch (error) {
    setMessage((error as Error).message, true);
  } finally {
    applyButton.disabled = false;
  }
});

manualButton.addEventListener("click", async () => {
  manualButton.disabled = true;
  setMessage("正在读取目标内存…");
  try {
    const snapshot = await requestJson<Snapshot>("/api/refresh", {
      method: "POST",
    });
    updateSnapshot(snapshot);
    setMessage(`手动刷新完成：Snapshot #${snapshot.sequence}`);
  } catch (error) {
    setMessage((error as Error).message, true);
  } finally {
    manualButton.disabled = false;
  }
});

sampleRateInput.addEventListener("change", () => {
  if (latestSnapshot) updateSnapshot(latestSnapshot);
});

void loadInitialState().catch((error: Error) => {
  setMessage(error.message, true);
});
connectSocket();
resizePlot();
