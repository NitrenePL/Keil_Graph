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
const manualButton = byId<HTMLButtonElement>("manual-refresh");
const controlMessage = byId<HTMLParagraphElement>("control-message");

let latestSnapshot: Snapshot | null = null;
let reconnectTimer: number | undefined;
let configTimer: number | undefined;

const pointTooltip = document.createElement("div");
pointTooltip.className = "point-tooltip";
pointTooltip.hidden = true;

const updateCursor = (plot: uPlot): void => {
  const index = plot.cursor.idx;
  const left = plot.cursor.left;
  const top = plot.cursor.top;
  const values = latestSnapshot?.values;

  if (
    index === null ||
    index === undefined ||
    left === null ||
    left === undefined ||
    top === null ||
    top === undefined ||
    !values ||
    index < 0 ||
    index >= values.length
  ) {
    pointTooltip.hidden = true;
    return;
  }

  const value = values[index];
  pointTooltip.textContent = `(${index}, ${value.toFixed(7)})`;
  pointTooltip.hidden = false;

  const tooltipWidth = pointTooltip.offsetWidth || 155;
  const tooltipHeight = pointTooltip.offsetHeight || 32;
  const maxLeft = Math.max(plot.over.clientWidth - tooltipWidth - 8, 8);
  const maxTop = Math.max(plot.over.clientHeight - tooltipHeight - 8, 8);
  pointTooltip.style.left = `${Math.min(left + 14, maxLeft)}px`;
  pointTooltip.style.top = `${Math.min(top + 14, maxTop)}px`;
};

const plot = new uPlot(
  {
    width: 1200,
    height: 620,
    cursor: {
      drag: { x: true, y: false },
      points: {
        size: 8,
        width: 2,
        stroke: "#ff9f32",
        fill: "#0b151f",
      },
    },
    hooks: {
      ready: [
        (instance) => {
          instance.over.appendChild(pointTooltip);
        },
      ],
      setCursor: [updateCursor],
    },
    scales: {
      x: { time: false, range: [0, 399] },
      y: { auto: true },
    },
    axes: [
      {
        stroke: "#8190a0",
        grid: { stroke: "rgba(161, 175, 190, 0.2)", width: 1 },
        ticks: { stroke: "rgba(161, 175, 190, 0.28)", width: 1 },
        label: "Index",
        labelSize: 32,
      },
      {
        stroke: "#8190a0",
        grid: { stroke: "rgba(161, 175, 190, 0.2)", width: 1 },
        ticks: { stroke: "rgba(161, 175, 190, 0.28)", width: 1 },
        label: "Value",
        labelSize: 42,
        size: 64,
      },
    ],
    series: [
      {},
      {
        label: "myLOGGER0Arr",
        stroke: "#ff9418",
        width: 3,
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
    height: Math.max(container.clientHeight, 360),
  });
};

new ResizeObserver(resizePlot).observe(byId("waveform"));

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
      ? "Running"
      : status.target_state === "stopped"
        ? "Stopped"
        : "Unknown";
  byId("memory-address").textContent = status.address_hex;
  byId("uvsock-port").textContent = String(status.port);
};

const updateSnapshot = (snapshot: Snapshot): void => {
  latestSnapshot = snapshot;
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

  const indices = snapshot.values.map((_, index) => index);
  plot.setScale("x", { min: 0, max: Math.max(snapshot.values.length - 1, 1) });
  plot.setData([indices, snapshot.values]);
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
    // The first WebSocket snapshot arrives after UVSC is connected.
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

const applyRefreshConfig = async (): Promise<void> => {
  const interval = Number(intervalInput.value);
  setMessage("正在更新刷新设置…");
  try {
    const status = await requestJson<ViewerStatus>("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        auto_refresh: autoRefresh.checked,
        interval_ms: interval,
      }),
    });
    updateStatus(status);
    setMessage(
      status.auto_refresh
        ? `自动刷新 ${status.interval_ms} ms`
        : "自动刷新已关闭",
    );
  } catch (error) {
    setMessage((error as Error).message, true);
  }
};

autoRefresh.addEventListener("change", () => {
  void applyRefreshConfig();
});

intervalInput.addEventListener("change", () => {
  void applyRefreshConfig();
});

intervalInput.addEventListener("input", () => {
  if (configTimer !== undefined) window.clearTimeout(configTimer);
  configTimer = window.setTimeout(() => {
    if (intervalInput.checkValidity()) void applyRefreshConfig();
  }, 700);
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

void loadInitialState().catch((error: Error) => {
  setMessage(error.message, true);
});
connectSocket();
resizePlot();
