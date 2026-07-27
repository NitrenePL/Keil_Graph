import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import "./style.css";
import {
  calculateHarmonics,
  type FftConfig,
  type HarmonicAnalysis,
} from "./harmonics";

type ScaleOperator = "multiply" | "divide";
type PinnedCursorKey = "x1" | "x2";

interface PinnedCursorElements {
  x1: HTMLDivElement;
  x2: HTMLDivElement;
}

interface PinnedCursorState {
  x1: number | null;
  x2: number | null;
}

interface SourceStatus {
  array_name: string;
  address: number;
  address_hex: string;
  count: number;
  dtype: string;
  scale_operator: ScaleOperator;
  scale_factor: number;
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
  keil_path: string;
  sources: SourceInput[];
  settings_file: string;
  export_channel_index: number;
  export_frequency_hz: number;
  fft_config: FftConfig;
  max_sources: number;
}

interface SourcesUpdateResponse {
  status: ViewerStatus;
  map_file: string;
  keil_path: string;
  dll_path: string;
  resolutions: Array<{
    array_name: string;
    address_hex: string;
    resolved_from_map: boolean;
    resolved_byte_size: number | null;
  }>;
}

interface SourceTestResponse {
  port: number;
  target_state: "running" | "stopped";
  dll_path: string;
  total_bytes: number;
  duration_ms: number;
  sources: Array<SourceStatus & {
    byte_count: number;
    read_duration_ms: number;
  }>;
  warnings: string[];
}

interface ScaleUpdateResponse {
  status: ViewerStatus;
  source: SourceInput;
}

interface SourcesPayload {
  map_file: string;
  keil_path: string;
  sources: SourceInput[];
}

interface SourceInput {
  array_name: string;
  count: number;
  dtype: string;
  address: string | number | null;
  scale_operator: ScaleOperator;
  scale_factor: number;
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
const scaleControls = byId<HTMLDivElement>("scale-controls");
const pinnedCursorCards =
  byId<HTMLDivElement>("pinned-cursor-cards");
const pinnedCursorClear =
  byId<HTMLButtonElement>("pinned-cursor-clear");
const pinnedCursorDelta =
  byId<HTMLParagraphElement>("pinned-cursor-delta");
const measurementRows = byId<HTMLDivElement>("measurement-rows");
const legend = document.querySelector<HTMLDivElement>(".legend");

const sourceConfigButton = byId<HTMLButtonElement>("source-config-button");
const sourceTestButton = byId<HTMLButtonElement>("source-test-button");
const sourceDialog = byId<HTMLDialogElement>("source-dialog");
const sourceDialogClose = byId<HTMLButtonElement>("source-dialog-close");
const sourceCancel = byId<HTMLButtonElement>("source-cancel");
const sourceForm = byId<HTMLFormElement>("source-form");
const sourceSave = byId<HTMLButtonElement>("source-save");
const sourceTest = byId<HTMLButtonElement>("source-test");
const sourceList = byId<HTMLDivElement>("source-list");
const sourceTemplate = byId<HTMLTemplateElement>("source-row-template");
const addSourceButton = byId<HTMLButtonElement>("add-source");
const sourceError = byId<HTMLParagraphElement>("source-error");
const mapFileNote = byId<HTMLParagraphElement>("map-file-note");
const mapFileInput = byId<HTMLInputElement>("map-file");
const keilPathInput = byId<HTMLInputElement>("keil-path");
const exportButton = byId<HTMLButtonElement>("export-button");
const exportDialog = byId<HTMLDialogElement>("export-dialog");
const exportForm = byId<HTMLFormElement>("export-form");
const exportDialogClose = byId<HTMLButtonElement>("export-dialog-close");
const exportCancel = byId<HTMLButtonElement>("export-cancel");
const exportSubmit = byId<HTMLButtonElement>("export-submit");
const exportChannel = byId<HTMLSelectElement>("export-channel");
const exportFrequency = byId<HTMLInputElement>("export-frequency");
const exportError = byId<HTMLParagraphElement>("export-error");
const fftWaveform = byId<HTMLDivElement>("fft-waveform");
const fftChannel = byId<HTMLSelectElement>("fft-channel");
const fftSampleFrequency = byId<HTMLInputElement>("fft-sample-frequency");
const fftBaseFrequency = byId<HTMLInputElement>("fft-base-frequency");
const fftHarmonicCount = byId<HTMLInputElement>("fft-harmonic-count");
const fftWindow = byId<HTMLSelectElement>("fft-window");
const fftAmplitude = byId<HTMLSelectElement>("fft-amplitude");
const fftAnalyze = byId<HTMLButtonElement>("fft-analyze");
const fftSnapshot = byId<HTMLSpanElement>("fft-snapshot");
const fftRange = byId<HTMLSpanElement>("fft-range");
const fftDetail = byId<HTMLSpanElement>("fft-detail");
const fftMessage = byId<HTMLParagraphElement>("fft-message");

let latestSnapshot: Snapshot | null = null;
let latestStatus: ViewerStatus | null = null;
let sourceOptions: SourceOptions | null = null;
let reconnectTimer: number | undefined;
let configTimer: number | undefined;
let plot: uPlot | null = null;
let plottedSignature = "";
let pinnedCursorElements: PinnedCursorElements | null = null;
const pinnedCursorState: PinnedCursorState = {
  x1: null,
  x2: null,
};
let fftPlot: uPlot | null = null;
let fftAnalysis: HarmonicAnalysis | null = null;
let fftAnalysisTimer: number | undefined;
let activeFftConfig: FftConfig = {
  channel_index: 0,
  sample_frequency_hz: 20_000,
  base_frequency_hz: 50,
  harmonic_count: 20,
  window: "hann",
  amplitude: "rms",
};

const pointTooltip = document.createElement("div");
pointTooltip.className = "point-tooltip";
pointTooltip.hidden = true;
const fftTooltip = document.createElement("div");
fftTooltip.className = "point-tooltip fft-tooltip";
fftTooltip.hidden = true;

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
          scale_operator: status.scale_operator,
          scale_factor: status.scale_factor,
        },
      ];

const formatScaleFactor = (factor: number): string => {
  if (factor >= 1_000_000 || factor < 0.0001) {
    return factor.toExponential(4);
  }
  return String(Number(factor.toPrecision(8)));
};

const sourceDisplayName = (source: SourceStatus): string => {
  if (source.scale_factor === 1) return source.array_name;
  const operator = source.scale_operator === "divide" ? "÷" : "×";
  return `${source.array_name} ${operator}${formatScaleFactor(source.scale_factor)}`;
};

const seriesSignature = (sources: SourceStatus[]): string =>
  sources
    .map(
      (source) =>
        `${source.array_name}:${source.count}:${source.dtype}:`
          + `${source.scale_operator}:${source.scale_factor}`,
    )
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
      `${sourceDisplayName(item)}: `
        + `(${index}, ${item.values[index].toFixed(7)})`;
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

const formatPinnedCursorValue = (value: number): string => {
  const magnitude = Math.abs(value);
  if (magnitude >= 1_000_000 || (magnitude > 0 && magnitude < 0.0001)) {
    return value.toExponential(6);
  }
  return value.toFixed(7);
};

const renderPinnedCursorCards = (): void => {
  pinnedCursorCards.replaceChildren();
  const keys: PinnedCursorKey[] = ["x1", "x2"];
  let cursorCount = 0;

  keys.forEach((key) => {
    const index = pinnedCursorState[key];
    if (index === null) return;
    cursorCount += 1;

    const card = document.createElement("article");
    card.className = `pinned-cursor-card ${key}`;

    const header = document.createElement("header");
    const title = document.createElement("strong");
    const tag = document.createElement("span");
    tag.className = "pinned-cursor-card-tag";
    tag.textContent = key.toUpperCase();
    const indexLabel = document.createElement("span");
    indexLabel.textContent = `Index ${index}`;
    title.append(tag, indexLabel);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "pinned-cursor-remove";
    remove.textContent = "删除";
    remove.setAttribute("aria-label", `删除 ${key.toUpperCase()} 游标`);
    remove.addEventListener("click", () => {
      pinnedCursorState[key] = null;
      renderPinnedCursorCards();
      updatePinnedCursorPositions();
    });
    header.append(title, remove);
    card.appendChild(header);

    latestSnapshot?.series.forEach((series, seriesIndex) => {
      const row = document.createElement("div");
      row.className = "pinned-cursor-channel";
      const dot = document.createElement("i");
      dot.style.backgroundColor = COLORS[seriesIndex % COLORS.length];
      const name = document.createElement("span");
      name.className = "pinned-cursor-channel-name";
      name.textContent = sourceDisplayName(series);
      const value = document.createElement("strong");
      value.className = "pinned-cursor-channel-value";
      const sample = series.values[index];
      value.textContent =
        sample === undefined ? "—" : formatPinnedCursorValue(sample);
      row.append(dot, name, value);
      card.appendChild(row);
    });
    pinnedCursorCards.appendChild(card);
  });

  if (cursorCount === 0) {
    const empty = document.createElement("p");
    empty.className = "pinned-cursor-empty";
    empty.textContent = latestSnapshot
      ? "左键单击曲线图固定 X1"
      : "等待波形数据";
    pinnedCursorCards.appendChild(empty);
  }

  pinnedCursorClear.disabled = cursorCount === 0;
  pinnedCursorDelta.textContent =
    pinnedCursorState.x1 === null || pinnedCursorState.x2 === null
      ? "ΔX —"
      : `ΔX ${pinnedCursorState.x2 - pinnedCursorState.x1} pts`;
};

const updatePinnedCursorPositions = (instance: uPlot = plot!): void => {
  if (!instance || !pinnedCursorElements) return;
  const scale = instance.scales.x;

  (["x1", "x2"] as PinnedCursorKey[]).forEach((key) => {
    const element = pinnedCursorElements![key];
    const index = pinnedCursorState[key];
    if (
      index === null
      || scale.min === null
      || scale.min === undefined
      || scale.max === null
      || scale.max === undefined
    ) {
      element.hidden = true;
      return;
    }

    const position = instance.valToPos(index, "x");
    const visible =
      Number.isFinite(position)
      && position >= 0
      && position <= instance.over.clientWidth;
    element.hidden = !visible;
    if (visible) element.style.left = `${position}px`;
  });
};

const createPinnedCursorElement = (
  key: PinnedCursorKey,
): HTMLDivElement => {
  const element = document.createElement("div");
  element.className =
    `pinned-cursor-line pinned-cursor-line-${key}`;
  element.hidden = true;
  const tag = document.createElement("span");
  tag.className = "pinned-cursor-line-tag";
  tag.textContent = key.toUpperCase();
  element.appendChild(tag);
  return element;
};

const addPinnedCursor = (index: number): void => {
  if (
    pinnedCursorState.x1 === index
    || pinnedCursorState.x2 === index
  ) {
    setMessage(`Index ${index} 已固定`);
    return;
  }

  const emptyKey = (["x1", "x2"] as PinnedCursorKey[]).find(
    (key) => pinnedCursorState[key] === null,
  );
  if (!emptyKey) {
    setMessage("最多固定两条纵线，请先删除一条或清除全部", true);
    return;
  }

  pinnedCursorState[emptyKey] = index;
  renderPinnedCursorCards();
  updatePinnedCursorPositions();
  setMessage(`${emptyKey.toUpperCase()} 已固定在 Index ${index}`);
};

const mountPinnedCursors = (instance: uPlot): void => {
  pinnedCursorElements = {
    x1: createPinnedCursorElement("x1"),
    x2: createPinnedCursorElement("x2"),
  };
  instance.over.append(
    pinnedCursorElements.x1,
    pinnedCursorElements.x2,
  );

  let pointerStart:
    { pointerId: number; clientX: number; clientY: number }
    | null = null;
  instance.over.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || !latestSnapshot) return;
    pointerStart = {
      pointerId: event.pointerId,
      clientX: event.clientX,
      clientY: event.clientY,
    };
  });
  instance.over.addEventListener("pointerup", (event) => {
    if (!pointerStart || pointerStart.pointerId !== event.pointerId) return;
    const distance = Math.hypot(
      event.clientX - pointerStart.clientX,
      event.clientY - pointerStart.clientY,
    );
    pointerStart = null;
    if (distance > 4 || !latestSnapshot) return;

    const bounds = instance.over.getBoundingClientRect();
    const position = Math.max(
      0,
      Math.min(bounds.width, event.clientX - bounds.left),
    );
    const maxIndex = Math.max(
      0,
      ...latestSnapshot.series.map((series) => series.values.length - 1),
    );
    const index = Math.max(
      0,
      Math.min(maxIndex, Math.round(instance.posToVal(position, "x"))),
    );
    addPinnedCursor(index);
  });
  instance.over.addEventListener("pointercancel", () => {
    pointerStart = null;
  });
  updatePinnedCursorPositions(instance);
};

const clearPinnedCursors = (): void => {
  pinnedCursorState.x1 = null;
  pinnedCursorState.x2 = null;
  renderPinnedCursorCards();
  updatePinnedCursorPositions();
};

pinnedCursorClear.addEventListener("click", clearPinnedCursors);
renderPinnedCursorCards();

const renderLegend = (sources: SourceStatus[]): void => {
  if (!legend) return;
  legend.replaceChildren();
  sources.forEach((source, index) => {
    const item = document.createElement("span");
    item.className = "legend-item";
    const line = document.createElement("i");
    line.style.backgroundColor = COLORS[index % COLORS.length];
    const label = document.createElement("span");
    label.textContent = sourceDisplayName(source);
    item.append(line, label);
    legend.appendChild(item);
  });
};

const renderScaleControls = (sources: SourceStatus[]): void => {
  scaleControls.replaceChildren();
  sources.forEach((source, index) => {
    const row = document.createElement("article");
    row.className = "scale-control-row";

    const heading = document.createElement("header");
    const dot = document.createElement("i");
    dot.style.backgroundColor = COLORS[index % COLORS.length];
    const name = document.createElement("strong");
    name.textContent = source.array_name;
    name.title = source.array_name;
    heading.append(dot, name);

    const fields = document.createElement("div");
    fields.className = "scale-control-fields";
    const operator = document.createElement("select");
    operator.ariaLabel = `${source.array_name} 缩放操作`;
    [
      ["multiply", "×"],
      ["divide", "÷"],
    ].forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      operator.appendChild(option);
    });
    operator.value = source.scale_operator;

    const factor = document.createElement("input");
    factor.type = "number";
    factor.min = "0.000000000001";
    factor.max = "1000000000000";
    factor.step = "any";
    factor.value = String(source.scale_factor);
    factor.ariaLabel = `${source.array_name} 缩放倍率`;

    const apply = document.createElement("button");
    apply.type = "button";
    apply.textContent = "应用";
    apply.addEventListener("click", async () => {
      const scaleFactor = Number(factor.value);
      if (
        !Number.isFinite(scaleFactor)
        || scaleFactor < 1e-12
        || scaleFactor > 1e12
      ) {
        setMessage("缩放倍率必须在 1e-12～1e12 之间", true);
        factor.focus();
        return;
      }

      apply.disabled = true;
      let saved = false;
      try {
        setMessage(`正在保存 ${source.array_name} 的垂直倍率…`);
        const response = await requestJson<ScaleUpdateResponse>(
          "/api/source/scale",
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              channel_index: index,
              scale_operator: operator.value,
              scale_factor: scaleFactor,
            }),
          },
        );
        saved = true;
        if (sourceOptions) sourceOptions.sources[index] = response.source;
        updateStatus(response.status);
        clearSnapshotView(normalizedSources(response.status));
        setMessage("倍率已保存，正在重新读取目标数组…");
        const snapshot = await requestJson<Snapshot>("/api/refresh", {
          method: "POST",
        });
        updateSnapshot(snapshot);
        setMessage(
          `${source.array_name} 已设置为 `
            + `${operator.value === "divide" ? "÷" : "×"}`
            + formatScaleFactor(scaleFactor),
        );
      } catch (error) {
        const prefix = saved ? "倍率已保存，但重新读取失败：" : "";
        setMessage(`${prefix}${(error as Error).message}`, true);
      } finally {
        apply.disabled = false;
      }
    });

    fields.append(operator, factor, apply);
    row.append(heading, fields);
    scaleControls.appendChild(row);
  });
};

interface ChannelMeasurement {
  average: number;
  maximum: number;
  minimum: number;
  rms: number;
}

const calculateMeasurement = (
  values: number[],
): ChannelMeasurement | null => {
  let count = 0;
  let sum = 0;
  let squareSum = 0;
  let maximum = Number.NEGATIVE_INFINITY;
  let minimum = Number.POSITIVE_INFINITY;

  values.forEach((value) => {
    if (!Number.isFinite(value)) return;
    count += 1;
    sum += value;
    squareSum += value * value;
    maximum = Math.max(maximum, value);
    minimum = Math.min(minimum, value);
  });

  if (count === 0) return null;
  return {
    average: sum / count,
    maximum,
    minimum,
    rms: Math.sqrt(squareSum / count),
  };
};

const formatMeasurement = (value: number): string => {
  const magnitude = Math.abs(value);
  if (magnitude >= 1_000_000 || (magnitude > 0 && magnitude < 0.0001)) {
    return value.toExponential(5);
  }
  return value
    .toFixed(6)
    .replace(/(\.\d*?[1-9])0+$|\.0+$/, "$1");
};

const renderMeasurements = (series: SeriesSnapshot[]): void => {
  measurementRows.replaceChildren();

  series.forEach((channel, index) => {
    const measurement = calculateMeasurement(channel.values);
    const row = document.createElement("div");
    row.className = "measurement-row";
    row.setAttribute("role", "row");

    const name = document.createElement("span");
    name.className = "measurement-channel";
    name.setAttribute("role", "cell");
    const dot = document.createElement("i");
    dot.style.backgroundColor = COLORS[index % COLORS.length];
    const label = document.createElement("span");
    label.textContent = sourceDisplayName(channel);
    name.append(dot, label);
    row.appendChild(name);

    const values = measurement
      ? [
          measurement.average,
          measurement.maximum,
          measurement.minimum,
          measurement.rms,
        ].map(formatMeasurement)
      : ["—", "—", "—", "—"];

    values.forEach((value) => {
      const cell = document.createElement("span");
      cell.setAttribute("role", "cell");
      cell.textContent = value;
      row.appendChild(cell);
    });
    measurementRows.appendChild(row);
  });
};

const createPlot = (sources: SourceStatus[]): void => {
  plot?.destroy();
  pinnedCursorElements = null;
  waveform.replaceChildren();
  pointTooltip.hidden = true;

  plot = new uPlot(
    {
      width: 1200,
      height: 620,
      cursor: {
        bind: {
          dblclick: () => null,
        },
        drag: {
          x: false,
          y: false,
          setScale: false,
        },
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
            mountPinnedCursors(instance);
          },
        ],
        draw: [updatePinnedCursorPositions],
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
          label: sourceDisplayName(source),
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

const drawFftBars = (instance: uPlot): void => {
  if (!fftAnalysis) return;
  const ratio = window.devicePixelRatio || 1;
  const zero = instance.valToPos(0, "y", true);
  const spacing =
    fftAnalysis.frequencies.length > 1
      ? Math.abs(
          instance.valToPos(fftAnalysis.frequencies[1], "x", true)
            - instance.valToPos(0, "x", true),
        )
      : 20 * ratio;
  const width = Math.max(1 * ratio, Math.min(10 * ratio, spacing * 0.55));
  const context = instance.ctx;
  context.save();
  context.beginPath();
  context.rect(
    instance.bbox.left,
    instance.bbox.top,
    instance.bbox.width,
    instance.bbox.height,
  );
  context.clip();
  context.fillStyle = "#35d04f";
  fftAnalysis.amplitudes.forEach((amplitude, index) => {
    const x = instance.valToPos(fftAnalysis!.frequencies[index], "x", true);
    const y = instance.valToPos(amplitude, "y", true);
    context.fillRect(
      Math.round(x - width / 2),
      Math.min(y, zero),
      width,
      Math.max(Math.abs(zero - y), ratio),
    );
  });
  context.restore();
};

const updateFftCursor = (instance: uPlot): void => {
  const index = instance.cursor.idx;
  if (
    index === null
    || index === undefined
    || !fftAnalysis
    || index >= fftAnalysis.amplitudes.length
  ) {
    fftTooltip.hidden = true;
    return;
  }
  const harmonic = index === 0 ? "H0 / DC" : `H${index}`;
  const unit = fftAnalysis.config.amplitude === "rms" ? "RMS" : "Amp";
  fftTooltip.textContent =
    `${harmonic} · ${fftAnalysis.frequencies[index]} Hz · `
      + `${fftAnalysis.amplitudes[index].toPrecision(8)} ${unit}`;
  const left = instance.cursor.left ?? 0;
  const top = instance.cursor.top ?? 0;
  fftTooltip.hidden = false;
  const maxLeft = Math.max(
    instance.over.clientWidth - fftTooltip.offsetWidth - 8,
    8,
  );
  const maxTop = Math.max(
    instance.over.clientHeight - fftTooltip.offsetHeight - 8,
    8,
  );
  fftTooltip.style.left = `${Math.min(left + 14, maxLeft)}px`;
  fftTooltip.style.top = `${Math.min(top + 14, maxTop)}px`;
};

const createFftPlot = (): void => {
  fftPlot?.destroy();
  fftWaveform.replaceChildren();
  fftTooltip.hidden = true;
  fftPlot = new uPlot(
    {
      width: Math.max(fftWaveform.clientWidth, 320),
      height: Math.max(fftWaveform.clientHeight, 360),
      cursor: {
        drag: { x: true, y: false },
        points: { size: 0 },
      },
      hooks: {
        ready: [
          (instance) => {
            instance.over.appendChild(fftTooltip);
          },
        ],
        draw: [drawFftBars],
        setCursor: [updateFftCursor],
      },
      scales: {
        x: { time: false },
        y: {
          auto: true,
          range: (_instance, minimum, maximum) => {
            const low = Math.min(0, minimum);
            const high = Math.max(0, maximum);
            const span = Math.max(high - low, 1e-9);
            return [low - span * 0.05, high + span * 0.08];
          },
        },
      },
      axes: [
        {
          stroke: "#8190a0",
          grid: { stroke: "rgba(161, 175, 190, 0.2)", width: 1 },
          ticks: { stroke: "rgba(161, 175, 190, 0.28)", width: 1 },
          label: "Frequency / Hz",
          labelSize: 32,
        },
        {
          stroke: "#8190a0",
          grid: { stroke: "rgba(161, 175, 190, 0.2)", width: 1 },
          ticks: { stroke: "rgba(161, 175, 190, 0.28)", width: 1 },
          label: "Amplitude",
          labelSize: 42,
          size: 64,
        },
      ],
      series: [
        {},
        {
          label: "Harmonics",
          stroke: "rgba(53, 208, 79, 0)",
          width: 0,
          points: { show: false },
        },
      ],
    },
    [[], []],
    fftWaveform,
  );
};

const resizeFftPlot = (): void => {
  if (!fftPlot) return;
  fftPlot.setSize({
    width: Math.max(fftWaveform.clientWidth, 320),
    height: Math.max(fftWaveform.clientHeight, 360),
  });
};

new ResizeObserver(resizeFftPlot).observe(fftWaveform);

const applyFftConfigToControls = (config: FftConfig): void => {
  fftSampleFrequency.value = String(config.sample_frequency_hz);
  fftBaseFrequency.value = String(config.base_frequency_hz);
  fftHarmonicCount.value = String(config.harmonic_count);
  fftWindow.value = config.window;
  fftAmplitude.value = config.amplitude;
};

const readFftConfig = (): FftConfig => {
  const integer = (input: HTMLInputElement, label: string): number => {
    const value = Number(input.value);
    if (!input.checkValidity() || !Number.isInteger(value)) {
      throw new Error(`${label}必须是有效整数`);
    }
    return value;
  };
  const config: FftConfig = {
    channel_index: Number(fftChannel.value),
    sample_frequency_hz: integer(fftSampleFrequency, "采样频率"),
    base_frequency_hz: integer(fftBaseFrequency, "基波频率"),
    harmonic_count: integer(fftHarmonicCount, "谐波次数"),
    window: fftWindow.value as FftConfig["window"],
    amplitude: fftAmplitude.value as FftConfig["amplitude"],
  };
  if (config.base_frequency_hz * 2 > config.sample_frequency_hz) {
    throw new Error("基波频率不能超过采样频率的一半");
  }
  return config;
};

const updateFftChannelOptions = (snapshot: Snapshot): void => {
  const channelIndex = Math.min(
    activeFftConfig.channel_index,
    snapshot.series.length - 1,
  );
  fftChannel.replaceChildren();
  snapshot.series.forEach((series, index) => {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = `${index + 1} · ${sourceDisplayName(series)}`;
    fftChannel.appendChild(option);
  });
  fftChannel.value = String(channelIndex);
  activeFftConfig = { ...activeFftConfig, channel_index: channelIndex };
};

const runFftAnalysis = (): void => {
  if (!latestSnapshot?.series.length) {
    fftMessage.textContent = "尚无可分析的波形快照";
    fftMessage.classList.add("error");
    return;
  }
  const channelIndex = Math.min(
    activeFftConfig.channel_index,
    latestSnapshot.series.length - 1,
  );
  const channel = latestSnapshot.series[channelIndex];
  fftAnalysis = calculateHarmonics(channel.values, {
    ...activeFftConfig,
    channel_index: channelIndex,
  });
  if (!fftPlot) createFftPlot();
  fftPlot?.setData([
    fftAnalysis.frequencies,
    fftAnalysis.amplitudes,
  ]);
  fftSnapshot.textContent = `Snapshot #${latestSnapshot.sequence}`;
  fftSnapshot.classList.toggle(
    "manual",
    latestSnapshot.trigger === "manual",
  );
  fftRange.textContent =
    `H0–H${fftAnalysis.effectiveHarmonics} · `
      + `0–${fftAnalysis.effectiveHarmonics
        * activeFftConfig.base_frequency_hz} Hz`;
  const windowLabel = {
    rectangular: "Rectangular",
    hann: "Hann",
    hamming: "Hamming",
  }[activeFftConfig.window];
  const appliedWindowLabel = {
    rectangular: "Rectangular",
    hann: "Hann",
    hamming: "Hamming",
  }[fftAnalysis.appliedWindow];
  const displayedWindow =
    fftAnalysis.appliedWindow === activeFftConfig.window
      ? windowLabel
      : `${windowLabel} → ${appliedWindowLabel}`;
  fftDetail.textContent =
    `${sourceDisplayName(channel)} · ${displayedWindow} · `
      + `${activeFftConfig.amplitude === "rms" ? "RMS" : "Amp"}`;

  const warnings: string[] = [];
  const notes: string[] = [];
  if (
    fftAnalysis.coherentCycles !== null
    && fftAnalysis.appliedWindow !== activeFftConfig.window
  ) {
    notes.push(
      `检测到 ${fftAnalysis.coherentCycles} 个完整周期，`
        + `已自动使用 Rectangular，避免 ${windowLabel} `
        + "造成相邻谐波泄漏",
    );
  }
  if (
    fftAnalysis.effectiveHarmonics
    < fftAnalysis.requestedHarmonics
  ) {
    warnings.push(
      `受 Nyquist 限制，配置 H${fftAnalysis.requestedHarmonics}，`
        + `当前实际分析到 H${fftAnalysis.effectiveHarmonics}`,
    );
  }
  if (fftAnalysis.nonFiniteSamples > 0) {
    warnings.push(
      `${fftAnalysis.nonFiniteSamples} 个非有限采样值按 0 处理`,
    );
  }
  fftMessage.textContent =
    [...warnings, ...notes].join("；")
      || `${channel.values.length} 点谐波分析完成`;
  fftMessage.classList.toggle("error", warnings.length > 0);
};

const scheduleFftAnalysis = (): void => {
  if (fftAnalysisTimer !== undefined) return;
  fftAnalysisTimer = window.setTimeout(() => {
    fftAnalysisTimer = undefined;
    runFftAnalysis();
  }, 200);
};

const clearSnapshotView = (sources: SourceStatus[]): void => {
  latestSnapshot = null;
  pointTooltip.hidden = true;
  plot?.setData([[], ...sources.map(() => [])]);
  clearPinnedCursors();
  measurementRows.replaceChildren();
  byId("sequence").textContent = "Snapshot —";
  byId("read-duration").textContent = "Read — ms";
  byId("capture-time").textContent = "—";
  const badge = byId("trigger-badge");
  badge.textContent = "等待刷新";
  badge.classList.remove("manual");
  fftAnalysis = null;
  fftPlot?.setData([[], []]);
  fftSnapshot.textContent = "等待数据";
  fftRange.textContent = "H0 · DC";
  fftDetail.textContent = "等待波形快照";
  fftMessage.textContent = "";
  updateExportAvailability();
};

fftAnalyze.addEventListener("click", async () => {
  fftAnalyze.disabled = true;
  fftMessage.textContent = "正在更新谐波分析…";
  fftMessage.classList.remove("error");
  try {
    const config = readFftConfig();
    activeFftConfig = await requestJson<FftConfig>("/api/fft/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    if (sourceOptions) sourceOptions.fft_config = activeFftConfig;
    runFftAnalysis();
  } catch (error) {
    fftMessage.textContent = (error as Error).message;
    fftMessage.classList.add("error");
  } finally {
    fftAnalyze.disabled = false;
  }
});

const setMessage = (message: string, error = false): void => {
  controlMessage.textContent = message;
  controlMessage.classList.toggle("error", error);
};

const updateExportAvailability = (): void => {
  const canExport = Boolean(
    latestStatus
      && !latestStatus.auto_refresh
      && latestSnapshot?.series.length,
  );
  exportButton.disabled = !canExport;
  exportButton.title = latestStatus?.auto_refresh
    ? "请先关闭自动刷新"
    : latestSnapshot
      ? "导出当前快照"
      : "尚无可导出的快照";
};

const updateStatus = (status: ViewerStatus): void => {
  latestStatus = status;
  const sources = normalizedSources(status);
  connectionDot.classList.toggle("connected", status.connected);
  connectionLabel.textContent = status.connected ? "UVSC 已连接" : "UVSC 未连接";
  connectionDetail.textContent = status.last_error ?? `UVSOCK :${status.port}`;
  autoRefresh.checked = status.auto_refresh;
  intervalInput.value = String(status.interval_ms);

  byId("array-name-title").textContent =
    sources.length === 1 ? sourceDisplayName(sources[0]) : "Array Waveforms";
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
  renderScaleControls(sources);
  updateExportAvailability();
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
  (["x1", "x2"] as PinnedCursorKey[]).forEach((key) => {
    const index = pinnedCursorState[key];
    if (index !== null && index >= maxCount) pinnedCursorState[key] = null;
  });
  renderPinnedCursorCards();
  updatePinnedCursorPositions();
  renderMeasurements(sources);
  updateExportAvailability();
  updateFftChannelOptions(snapshot);
  scheduleFftAnalysis();
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
  activeFftConfig = options.fft_config;
  applyFftConfigToControls(activeFftConfig);
  updateStatus(status);
  mapFileNote.textContent =
    `保存不读取 · 留空的数组地址将从 MAP 解析 · `
      + `最多 ${options.max_sources} 条曲线`;
  mapFileNote.title = `配置文件：${options.settings_file}`;
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

const closeExportDialog = (): void => {
  exportDialog.close();
  exportError.textContent = "";
};

exportButton.addEventListener("click", () => {
  if (!latestStatus || latestStatus.auto_refresh || !latestSnapshot) {
    setMessage("请先关闭自动刷新，并确保已有快照", true);
    return;
  }

  exportChannel.replaceChildren();
  latestSnapshot.series.forEach((series, index) => {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent =
      `${index + 1} · ${sourceDisplayName(series)} `
        + `(${series.values.length} points)`;
    exportChannel.appendChild(option);
  });
  const savedChannel = Math.min(
    sourceOptions?.export_channel_index ?? 0,
    latestSnapshot.series.length - 1,
  );
  exportChannel.value = String(savedChannel);
  exportFrequency.value = String(
    sourceOptions?.export_frequency_hz ?? 20_000,
  );
  exportError.textContent = "";
  exportDialog.showModal();
});

exportDialogClose.addEventListener("click", closeExportDialog);
exportCancel.addEventListener("click", closeExportDialog);
exportDialog.addEventListener("click", (event) => {
  if (event.target === exportDialog) closeExportDialog();
});

exportForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  exportSubmit.disabled = true;
  exportError.textContent = "";
  const channelIndex = Number(exportChannel.value);
  const frequencyHz = Number(exportFrequency.value);
  try {
    const response = await fetch("/api/export/csv", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        channel_index: channelIndex,
        frequency_hz: frequencyHz,
      }),
    });
    if (!response.ok) {
      const payload = await response.json();
      const detail =
        typeof payload.detail === "string"
          ? payload.detail
          : response.statusText;
      throw new Error(detail);
    }

    const disposition = response.headers.get("Content-Disposition") ?? "";
    const filename =
      disposition.match(/filename="([^"]+)"/)?.[1] ?? "waveform.csv";
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);

    if (sourceOptions) {
      sourceOptions.export_channel_index = channelIndex;
      sourceOptions.export_frequency_hz = frequencyHz;
    }
    closeExportDialog();
    setMessage(
      `已导出通道 ${channelIndex + 1}，采样频率 ${frequencyHz} Hz`,
    );
  } catch (error) {
    exportError.textContent = (error as Error).message;
  } finally {
    exportSubmit.disabled = false;
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

const addSourceRow = (
  source?: SourceStatus,
  configured?: SourceInput,
): void => {
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
  field<HTMLInputElement>("name").value =
    configured?.array_name ?? source?.array_name ?? "";
  field<HTMLInputElement>("count").value = String(
    configured?.count ?? source?.count ?? 400,
  );
  field<HTMLSelectElement>("dtype").value =
    configured?.dtype ?? source?.dtype ?? "float32";
  row.dataset.scaleOperator =
    configured?.scale_operator ?? source?.scale_operator ?? "multiply";
  row.dataset.scaleFactor = String(
    configured?.scale_factor ?? source?.scale_factor ?? 1,
  );
  const address = field<HTMLInputElement>("address");
  address.value =
    configured?.address === null || configured?.address === undefined
      ? ""
      : typeof configured.address === "number"
        ? `0x${configured.address.toString(16).toUpperCase()}`
        : configured.address;
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

const renderSourceRows = (
  sources: SourceStatus[],
  configuredSources: SourceInput[] = [],
): void => {
  sourceList.replaceChildren();
  sources.forEach((source, index) => {
    addSourceRow(source, configuredSources[index]);
  });
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
        address: address || null,
        scale_operator:
          (row.dataset.scaleOperator as ScaleOperator | undefined)
            ?? "multiply",
        scale_factor: Number(row.dataset.scaleFactor ?? 1),
      };
    },
  );

const draftSourcesPayload = (): SourcesPayload => ({
  map_file: mapFileInput.value.trim(),
  keil_path: keilPathInput.value.trim(),
  sources: collectSourceRows(),
});

const formatSourceTestResult = (result: SourceTestResponse): string => {
  const target = result.target_state === "running" ? "Running" : "Stopped";
  const summary =
    `测试通过：${result.sources.length} 条曲线，`
      + `${result.total_bytes} 字节，目标 ${target}，`
      + `${result.duration_ms.toFixed(1)} ms`;
  return result.warnings.length > 0
    ? `${summary}；清理警告：${result.warnings.join("；")}`
    : summary;
};

const runSourceTest = async (
  config: SourcesPayload,
  button: HTMLButtonElement,
  report: (message: string, isError: boolean) => void,
): Promise<void> => {
  button.disabled = true;
  report("正在验证 MAP、UVSC DLL 并读取数组…", false);
  try {
    const result = await requestJson<SourceTestResponse>("/api/sources/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    report(formatSourceTestResult(result), false);
  } catch (error) {
    report((error as Error).message, true);
  } finally {
    button.disabled = false;
  }
};

const closeSourceDialog = (): void => {
  sourceDialog.close();
  sourceError.textContent = "";
  sourceError.classList.remove("success");
};

sourceConfigButton.addEventListener("click", () => {
  mapFileInput.value = sourceOptions?.map_file ?? "";
  keilPathInput.value = sourceOptions?.keil_path ?? "";
  if (latestStatus) {
    renderSourceRows(
      normalizedSources(latestStatus),
      sourceOptions?.sources ?? [],
    );
  } else {
    renderSourceRows([]);
    addSourceRow();
  }
  sourceError.textContent = "";
  sourceError.classList.remove("success");
  sourceDialog.showModal();
});
addSourceButton.addEventListener("click", () => addSourceRow());
sourceDialogClose.addEventListener("click", closeSourceDialog);
sourceCancel.addEventListener("click", closeSourceDialog);
sourceDialog.addEventListener("click", (event) => {
  if (event.target === sourceDialog) closeSourceDialog();
});

sourceTestButton.addEventListener("click", async () => {
  if (!sourceOptions) {
    setMessage("数据源配置尚未加载", true);
    return;
  }
  await runSourceTest(
    {
      map_file: sourceOptions.map_file,
      keil_path: sourceOptions.keil_path,
      sources: sourceOptions.sources,
    },
    sourceTestButton,
    (message, isError) => setMessage(message, isError),
  );
});

sourceTest.addEventListener("click", async () => {
  if (!sourceForm.reportValidity()) return;
  sourceSave.disabled = true;
  await runSourceTest(
    draftSourcesPayload(),
    sourceTest,
    (message, isError) => {
      sourceError.textContent = message;
      sourceError.classList.toggle("success", !isError);
    },
  );
  sourceSave.disabled = false;
});

sourceForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  sourceSave.disabled = true;
  sourceTest.disabled = true;
  sourceError.textContent = "";
  sourceError.classList.remove("success");
  try {
    const payload = draftSourcesPayload();
    const response = await requestJson<SourcesUpdateResponse>("/api/sources", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (sourceOptions) {
      sourceOptions.map_file = response.map_file;
      sourceOptions.keil_path = response.keil_path;
      sourceOptions.sources = payload.sources;
    }
    updateStatus(response.status);
    clearSnapshotView(normalizedSources(response.status));
    const mapCount = response.resolutions.filter(
      (item) => item.resolved_from_map,
    ).length;
    setMessage(
      `已保存 ${payload.sources.length} 条曲线，`
        + `${mapCount} 条由 MAP 解析；可测试或立即刷新`,
    );
    closeSourceDialog();
  } catch (error) {
    sourceError.textContent = (error as Error).message;
  } finally {
    sourceSave.disabled = false;
    sourceTest.disabled = false;
  }
});

void loadInitialState().catch((error: Error) => {
  setMessage(error.message, true);
});
connectSocket();
