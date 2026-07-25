import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import "./style.css";

interface SourceStatus {
  array_name: string;
  address: number;
  address_hex: string;
  count: number;
  dtype: string;
  sample_rate_hz: number;
}

interface ViewerStatus extends SourceStatus {
  connected: boolean;
  target_state: "running" | "stopped" | "unknown";
  port: number;
  sources: SourceStatus[];
  auto_refresh: boolean;
  interval_ms: number;
  last_error: string | null;
  latest_sequence: number | null;
}

interface SeriesSnapshot extends SourceStatus {
  read_duration_ms: number;
  values: number[];
}

interface Snapshot {
  sequence: number;
  captured_at: string;
  trigger: "auto" | "manual";
  read_duration_ms: number;
  series: SeriesSnapshot[];
}

interface SocketEvent {
  type: "status" | "snapshot";
  data: ViewerStatus | Snapshot;
}

interface SourceOptions {
  data_types: string[];
  map_file: string;
  max_sources: number;
}

interface SourcesUpdateResponse {
  status: ViewerStatus;
  map_file: string;
  resolutions: Array<{
    array_name: string;
    address_hex: string;
    resolved_from_map: boolean;
    resolved_byte_size: number | null;
  }>;
}

interface SourceInput {
  array_name: string;
  count: number;
  dtype: string;
  sample_rate_hz: number;
  address: string | null;
}

const COLORS = [
  "#ff9418",
  "#2f9ee5",
  "#43c86b",
  "#d86ee7",
  "#24c7c9",
  "#f05d68",
  "#e1ca48",
  "#9a83ef",
];

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
const waveform = byId<HTMLDivElement>("waveform");
const legend = document.querySelector<HTMLDivElement>(".legend");

const sourceConfigButton = byId<HTMLButtonElement>("source-config-button");
const sourceDialog = byId<HTMLDialogElement>("source-dialog");
const sourceDialogClose = byId<HTMLButtonElement>("source-dialog-close");
const sourceCancel = byId<HTMLButtonElement>("source-cancel");
const sourceForm = byId<HTMLFormElement>("source-form");
const sourceSave = byId<HTMLButtonElement>("source-save");
const sourceList = byId<HTMLDivElement>("source-list");
const sourceTemplate = byId<HTMLTemplateElement>("source-row-template");
const addSourceButton = byId<HTMLButtonElement>("add-source");
const sourceError = byId<HTMLParagraphElement>("source-error");
const mapFileNote = byId<HTMLParagraphElement>("map-file-note");
const mapFileInput = byId<HTMLInputElement>("map-file");

let latestSnapshot: Snapshot | null = null;
let latestStatus: ViewerStatus | null = null;
let sourceOptions: SourceOptions | null = null;
let reconnectTimer: number | undefined;
let configTimer: number | undefined;
let plot: uPlot | null = null;
let plottedSignature = "";

const pointTooltip = document.createElement("div");
pointTooltip.className = "point-tooltip";
pointTooltip.hidden = true;

const normalizedSources = (status: ViewerStatus): SourceStatus[] =>
  status.sources?.length
    ? status.sources
    : [
        {
          array_name: status.array_name,
          address: status.address,
          address_hex: status.address_hex,
          count: status.count,
          dtype: status.dtype,
          sample_rate_hz: status.sample_rate_hz,
        },
      ];

const seriesSignature = (sources: SourceStatus[]): string =>
  sources
    .map((source) => `${source.array_name}:${source.count}:${source.dtype}`)
    .join("|");

const renderTooltip = (
  instance: uPlot,
  index: number,
  series: SeriesSnapshot[],
): void => {
  pointTooltip.replaceChildren();

  const heading = document.createElement("strong");
  heading.textContent = `Index ${index}`;
  pointTooltip.appendChild(heading);

  series.forEach((item, seriesIndex) => {
    if (index >= item.values.length) return;
    const row = document.createElement("span");
    const dot = document.createElement("i");
    dot.style.backgroundColor = COLORS[seriesIndex % COLORS.length];
    const label = document.createElement("span");
    label.textContent =
      `${item.array_name}: (${index}, ${item.values[index].toFixed(7)})`;
    row.append(dot, label);
    pointTooltip.appendChild(row);
  });

  const left = instance.cursor.left ?? 0;
  const top = instance.cursor.top ?? 0;
  pointTooltip.hidden = false;
  const tooltipWidth = pointTooltip.offsetWidth || 220;
  const tooltipHeight = pointTooltip.offsetHeight || 48;
  const maxLeft = Math.max(instance.over.clientWidth - tooltipWidth - 8, 8);
  const maxTop = Math.max(instance.over.clientHeight - tooltipHeight - 8, 8);
  pointTooltip.style.left = `${Math.min(left + 14, maxLeft)}px`;
  pointTooltip.style.top = `${Math.min(top + 14, maxTop)}px`;
};

const updateCursor = (instance: uPlot): void => {
  const index = instance.cursor.idx;
  if (
    index === null ||
    index === undefined ||
    instance.cursor.left === null ||
    instance.cursor.left === undefined ||
    instance.cursor.top === null ||
    instance.cursor.top === undefined ||
    !latestSnapshot
  ) {
    pointTooltip.hidden = true;
    return;
  }
  renderTooltip(instance, index, latestSnapshot.series);
};

const renderLegend = (sources: SourceStatus[]): void => {
  if (!legend) return;
  legend.replaceChildren();
  sources.forEach((source, index) => {
    const item = document.createElement("span");
    item.className = "legend-item";
    const line = document.createElement("i");
    line.style.backgroundColor = COLORS[index % COLORS.length];
    const label = document.createElement("span");
    label.textContent = source.array_name;
    item.append(line, label);
    legend.appendChild(item);
  });
};

const createPlot = (sources: SourceStatus[]): void => {
  plot?.destroy();
  waveform.replaceChildren();
  pointTooltip.hidden = true;

  plot = new uPlot(
    {
      width: 1200,
      height: 620,
      cursor: {
        drag: { x: true, y: false },
        points: {
          size: 8,
          width: 2,
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
        x: { time: false },
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
        ...sources.map((source, index) => ({
          label: source.array_name,
          stroke: COLORS[index % COLORS.length],
          width: 3,
          points: { show: false },
        })),
      ],
    },
    [[], ...sources.map(() => [])],
    waveform,
  );
  plottedSignature = seriesSignature(sources);
  renderLegend(sources);
  resizePlot();
};

const resizePlot = (): void => {
  if (!plot) return;
  plot.setSize({
    width: Math.max(waveform.clientWidth, 320),
    height: Math.max(waveform.clientHeight, 360),
  });
};

new ResizeObserver(resizePlot).observe(waveform);

const setMessage = (message: string, error = false): void => {
  controlMessage.textContent = message;
  controlMessage.classList.toggle("error", error);
};

const formatRate = (sampleRate: number): string =>
  sampleRate >= 1000
    ? `${(sampleRate / 1000).toFixed(3).replace(/\.?0+$/, "")} kHz`
    : `${sampleRate} Hz`;

const updateStatus = (status: ViewerStatus): void => {
  latestStatus = status;
  const sources = normalizedSources(status);
  connectionDot.classList.toggle("connected", status.connected);
  connectionLabel.textContent = status.connected ? "UVSC 已连接" : "UVSC 未连接";
  connectionDetail.textContent = status.last_error ?? `UVSOCK :${status.port}`;
  autoRefresh.checked = status.auto_refresh;
  intervalInput.value = String(status.interval_ms);

  byId("array-name-title").textContent =
    sources.length === 1 ? sources[0].array_name : "Array Waveforms";
  const maxCount = Math.max(...sources.map((source) => source.count));
  byId("array-meta").textContent =
    `${sources.length} curves · max ${maxCount} points`;
  byId("target-state").textContent =
    status.target_state === "running"
      ? "Running"
      : status.target_state === "stopped"
        ? "Stopped"
        : "Unknown";
  byId("memory-address").textContent = `${sources.length}`;
  byId("uvsock-port").textContent = String(status.port);

  if (seriesSignature(sources) !== plottedSignature) createPlot(sources);
};

const updateSnapshot = (snapshot: Snapshot): void => {
  latestSnapshot = snapshot;
  const sources = snapshot.series;
  if (seriesSignature(sources) !== plottedSignature) createPlot(sources);

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

  const maxCount = Math.max(...sources.map((source) => source.values.length));
  const indices = Array.from({ length: maxCount }, (_, index) => index);
  const alignedSeries = sources.map((source) => [
    ...source.values,
    ...Array<number | null>(maxCount - source.values.length).fill(null),
  ]);
  plot?.setData([indices, ...alignedSeries]);
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
  const [status, options] = await Promise.all([
    requestJson<ViewerStatus>("/api/status"),
    requestJson<SourceOptions>("/api/source/options"),
  ]);
  sourceOptions = options;
  updateStatus(status);
  mapFileNote.textContent =
    `留空的数组地址将从此 MAP 文件解析 · 最多 ${options.max_sources} 条曲线`;
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
  setMessage("正在更新刷新设置…");
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
    setMessage(
      `手动刷新完成：${snapshot.series.length} 条曲线，`
        + `Snapshot #${snapshot.sequence}`,
    );
  } catch (error) {
    setMessage((error as Error).message, true);
  } finally {
    manualButton.disabled = false;
  }
});

const updateSourceNumbers = (): void => {
  const rows = Array.from(sourceList.querySelectorAll<HTMLElement>(".source-row"));
  rows.forEach((row, index) => {
    const number = row.querySelector<HTMLElement>(".source-number");
    if (number) number.textContent = `曲线 ${index + 1}`;
    const remove = row.querySelector<HTMLButtonElement>(".remove-source");
    if (remove) remove.disabled = rows.length === 1;
  });
  addSourceButton.disabled =
    rows.length >= (sourceOptions?.max_sources ?? COLORS.length);
};

const addSourceRow = (source?: SourceStatus): void => {
  const fragment = sourceTemplate.content.cloneNode(true) as DocumentFragment;
  const row = fragment.querySelector<HTMLElement>(".source-row");
  if (!row) throw new Error("Missing source row template");

  const field = <T extends HTMLInputElement | HTMLSelectElement>(
    name: string,
  ): T => {
    const element = row.querySelector<T>(`[data-field="${name}"]`);
    if (!element) throw new Error(`Missing source field ${name}`);
    return element;
  };
  field<HTMLInputElement>("name").value = source?.array_name ?? "";
  field<HTMLInputElement>("count").value = String(source?.count ?? 400);
  field<HTMLSelectElement>("dtype").value = source?.dtype ?? "float32";
  field<HTMLInputElement>("sample-rate").value = String(
    source?.sample_rate_hz ?? 20_000,
  );
  const address = field<HTMLInputElement>("address");
  address.value = "";
  address.placeholder = source
    ? `自动解析；当前 ${source.address_hex}`
    : "留空按 MAP 解析";
  row.querySelector(".remove-source")?.addEventListener("click", () => {
    row.remove();
    updateSourceNumbers();
  });
  sourceList.appendChild(fragment);
  updateSourceNumbers();
};

const renderSourceRows = (sources: SourceStatus[]): void => {
  sourceList.replaceChildren();
  sources.forEach((source) => addSourceRow(source));
};

const collectSourceRows = (): SourceInput[] =>
  Array.from(sourceList.querySelectorAll<HTMLElement>(".source-row")).map(
    (row) => {
      const value = (name: string): string => {
        const element = row.querySelector<
          HTMLInputElement | HTMLSelectElement
        >(`[data-field="${name}"]`);
        if (!element) throw new Error(`Missing source field ${name}`);
        return element.value;
      };
      const address = value("address").trim();
      return {
        array_name: value("name").trim(),
        count: Number(value("count")),
        dtype: value("dtype"),
        sample_rate_hz: Number(value("sample-rate")),
        address: address || null,
      };
    },
  );

const closeSourceDialog = (): void => {
  sourceDialog.close();
  sourceError.textContent = "";
};

sourceConfigButton.addEventListener("click", () => {
  mapFileInput.value = sourceOptions?.map_file ?? "";
  if (latestStatus) {
    renderSourceRows(normalizedSources(latestStatus));
  } else {
    renderSourceRows([]);
    addSourceRow();
  }
  sourceError.textContent = "";
  sourceDialog.showModal();
});
addSourceButton.addEventListener("click", () => addSourceRow());
sourceDialogClose.addEventListener("click", closeSourceDialog);
sourceCancel.addEventListener("click", closeSourceDialog);
sourceDialog.addEventListener("click", (event) => {
  if (event.target === sourceDialog) closeSourceDialog();
});

sourceForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  sourceSave.disabled = true;
  sourceError.textContent = "";
  try {
    const response = await requestJson<SourcesUpdateResponse>("/api/sources", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        map_file: mapFileInput.value.trim(),
        sources: collectSourceRows(),
      }),
    });
    if (sourceOptions) sourceOptions.map_file = response.map_file;
    updateStatus(response.status);
    const snapshot = await requestJson<Snapshot>("/api/refresh", {
      method: "POST",
    });
    updateSnapshot(snapshot);
    const mapCount = response.resolutions.filter(
      (item) => item.resolved_from_map,
    ).length;
    setMessage(
      `已配置 ${snapshot.series.length} 条曲线，`
        + `${mapCount} 条由 MAP 解析`,
    );
    closeSourceDialog();
  } catch (error) {
    sourceError.textContent = (error as Error).message;
  } finally {
    sourceSave.disabled = false;
  }
});

void loadInitialState().catch((error: Error) => {
  setMessage(error.message, true);
});
connectSocket();
