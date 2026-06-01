const currentYear = new Date().getFullYear();

const state = {
  rows: [],
  corrections: [],
  dataScope: "year",
  granularity: "day",
  year: currentYear,
  availableYears: [],
  apiAvailable: false,
  csvPath: "",
  visibleSeries: {
    ttb: true,
    ttm: true,
    tts: true,
  },
  hoverIndex: null,
  chartMeta: null,
  viewport: { start: 0, end: 1 },
  drag: null,
};

const els = {
  yearInput: document.querySelector("#year-input"),
  latestRate: document.querySelector("#latest-rate"),
  latestDate: document.querySelector("#latest-date"),
  refreshButton: document.querySelector("#refresh-button"),
  reloadButton: document.querySelector("#reload-button"),
  exportButton: document.querySelector("#export-button"),
  scopeButtons: document.querySelectorAll("[data-scope]"),
  progressCard: document.querySelector("#progress-card"),
  progressTitle: document.querySelector("#progress-title"),
  progressPercent: document.querySelector("#progress-percent"),
  progressBar: document.querySelector("#progress-bar"),
  progressMessage: document.querySelector("#progress-message"),
  metricCount: document.querySelector("#metric-count"),
  metricRange: document.querySelector("#metric-range"),
  metricHigh: document.querySelector("#metric-high"),
  metricLow: document.querySelector("#metric-low"),
  chartSubtitle: document.querySelector("#chart-subtitle"),
  canvas: document.querySelector("#rate-chart"),
  resetZoomButton: document.querySelector("#reset-zoom-button"),
  seriesButtons: document.querySelectorAll("[data-series]"),
  table: document.querySelector("#rate-table"),
  dataSource: document.querySelector("#data-source"),
  editCard: document.querySelector("#edit-card"),
  editForm: document.querySelector("#edit-form"),
  cancelEditButton: document.querySelector("#cancel-edit-button"),
  editDate: document.querySelector("#edit-date"),
  editTtb: document.querySelector("#edit-ttb"),
  editTtm: document.querySelector("#edit-ttm"),
  editTts: document.querySelector("#edit-tts"),
  editReason: document.querySelector("#edit-reason"),
  correctionLog: document.querySelector("#correction-log"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function numberText(value) {
  if (!Number.isFinite(value)) return "--";
  return value.toFixed(2);
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function parseCsv(text) {
  const lines = text.trim().split(/\r?\n/).filter(Boolean);
  if (!lines.length) return [];
  const headers = splitCsvLine(lines[0]);
  return lines.slice(1).map((line) => {
    const cells = splitCsvLine(line);
    const row = Object.fromEntries(headers.map((header, index) => [header, cells[index] ?? ""]));
    return normalizeRow(row);
  }).filter(Boolean);
}

function splitCsvLine(line) {
  const cells = [];
  let current = "";
  let inQuotes = false;
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if (char === '"' && line[index + 1] === '"') {
      current += '"';
      index += 1;
    } else if (char === '"') {
      inQuotes = !inQuotes;
    } else if (char === "," && !inQuotes) {
      cells.push(current);
      current = "";
    } else {
      current += char;
    }
  }
  cells.push(current);
  return cells;
}

function normalizeRow(row) {
  const date = row.date || row.Date || row["日期"];
  const ttb = parseRateValue(row.ttb ?? row.TTB);
  const ttm = parseRateValue(row.ttm ?? row.TTM);
  const tts = parseRateValue(row.tts ?? row.TTS);
  if (!date || !Number.isFinite(ttb) || !Number.isFinite(ttm) || !Number.isFinite(tts)) return null;
  return {
    date,
    ttb,
    ttm,
    tts,
    url: row.url || row.URL || mizuhoUrl(date),
  };
}

function parseRateValue(value) {
  const text = String(value ?? "").trim();
  if (!text) return Number.NaN;
  return Number(text);
}

function mizuhoUrl(dateText) {
  const [year, month, day] = dateText.split("-");
  return `https://www.mizuhobank.co.jp/market/historical/backnumber_b/pdf/fx-quotation${year.slice(2)}${month}${day}.pdf`;
}

function isoDate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function parseLocalDate(dateText) {
  const [year, month, day] = dateText.split("-").map(Number);
  return new Date(year, month - 1, day);
}

function weekdayText(dateText) {
  const date = parseLocalDate(dateText);
  if (Number.isNaN(date.getTime())) return "--";
  return ["周日", "周一", "周二", "周三", "周四", "周五", "周六"][date.getDay()];
}

function addDays(date, days) {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

function selectedYearEnd() {
  if (state.year === currentYear) return new Date();
  return new Date(state.year, 11, 31);
}

async function loadData() {
  state.year = Number(els.yearInput.value || 2026);
  if (state.dataScope === "all") {
    await loadAllData();
    return;
  }
  await loadYearData();
}

async function loadYearData() {
  els.dataSource.textContent = "正在载入数据...";
  try {
    const response = await fetch(`./api/rates?year=${state.year}`, { cache: "no-store" });
    if (!response.ok) throw new Error("local api unavailable");
    const payload = await response.json();
    if (!payload.ok) throw new Error(payload.error || "API 返回失败");
    state.rows = payload.rows.map(normalizeRow).filter(Boolean);
    state.corrections = payload.corrections || [];
    state.apiAvailable = true;
    state.csvPath = payload.csvPath;
    els.refreshButton.disabled = false;
    els.dataSource.textContent = `本地服务模式 · ${payload.csvPath}`;
  } catch {
    const csvPath = `./${state.year}/usd-jpy-${state.year}.csv`;
    try {
      const response = await fetch(csvPath, { cache: "no-store" });
      if (!response.ok) throw new Error(`找不到 ${csvPath}`);
      state.rows = parseCsv(await response.text());
      state.corrections = [];
      state.apiAvailable = false;
      state.csvPath = csvPath;
      els.refreshButton.disabled = true;
      els.dataSource.textContent = `静态模式 · ${csvPath} · 启动 python3 server.py 后可更新和修正`;
    } catch (error) {
      state.rows = [];
      state.corrections = [];
      state.apiAvailable = false;
      els.refreshButton.disabled = true;
      els.dataSource.textContent = `载入失败：${error.message}`;
    }
  }
  state.rows.sort((left, right) => left.date.localeCompare(right.date));
  resetViewport();
  render();
}

async function loadAllData() {
  els.dataSource.textContent = "正在载入全部年份数据...";
  try {
    const response = await fetch("./api/rates?scope=all", { cache: "no-store" });
    if (!response.ok) throw new Error("local api unavailable");
    const payload = await response.json();
    if (!payload.ok) throw new Error(payload.error || "API 返回失败");
    if (payload.scope !== "all") throw new Error("local api does not support all-data scope yet");
    state.rows = payload.rows.map(normalizeRow).filter(Boolean);
    state.corrections = payload.corrections || [];
    state.availableYears = payload.years || [];
    state.apiAvailable = true;
    state.csvPath = payload.csvPath;
    els.refreshButton.disabled = false;
    els.dataSource.textContent = `本地服务模式 · 全部年份 · ${payload.stats.count.toLocaleString()} 条`;
  } catch {
    const rows = [];
    const years = [];
    for (let year = 2019; year <= currentYear; year += 1) {
      try {
        const response = await fetch(`./${year}/usd-jpy-${year}.csv`, { cache: "no-store" });
        if (!response.ok) continue;
        rows.push(...parseCsv(await response.text()));
        years.push(year);
      } catch {
        // Static mode cannot list directories, so missing future/partial CSVs are ignored.
      }
    }
    state.rows = rows;
    state.corrections = [];
    state.availableYears = years;
    state.apiAvailable = false;
    state.csvPath = years.map((year) => `${year}/usd-jpy-${year}.csv`).join(", ");
    els.refreshButton.disabled = true;
    els.dataSource.textContent = years.length
      ? `静态模式 · 全部年份 ${years[0]}-${years.at(-1)} · 启动 python3 server.py 后可更新和修正`
      : "静态模式未找到年度 CSV。";
  }
  state.rows.sort((left, right) => left.date.localeCompare(right.date));
  resetViewport();
  render();
}

function resetViewport() {
  state.hoverIndex = null;
  state.viewport = { start: 0, end: 1 };
  state.drag = null;
}

function aggregateRows(rows, granularity) {
  if (granularity === "day") return rows.map((row) => ({ ...row, label: row.date, count: 1 }));
  const groups = new Map();
  for (const row of rows) {
    const key = granularity === "week" ? weekKey(row.date) : row.date.slice(0, 7);
    const group = groups.get(key) || { label: key, ttb: 0, ttm: 0, tts: 0, count: 0, date: key };
    group.ttb += row.ttb;
    group.ttm += row.ttm;
    group.tts += row.tts;
    group.count += 1;
    groups.set(key, group);
  }
  return [...groups.values()].map((group) => ({
    ...group,
    ttb: group.ttb / group.count,
    ttm: group.ttm / group.count,
    tts: group.tts / group.count,
  }));
}

function weekKey(dateText) {
  const date = new Date(`${dateText}T00:00:00`);
  const day = date.getDay() || 7;
  date.setDate(date.getDate() - day + 1);
  return date.toISOString().slice(0, 10);
}

function render() {
  renderSummary();
  renderChart();
  renderTable();
  renderCorrections();
  updateSeriesButtons();
  updateScopeButtons();
}

function renderSummary() {
  const rows = state.rows;
  els.metricCount.textContent = rows.length.toLocaleString();
  if (!rows.length) {
    els.latestRate.textContent = "--";
    els.latestDate.textContent = "没有数据";
    els.metricRange.textContent = "--";
    els.metricHigh.textContent = "--";
    els.metricLow.textContent = "--";
    return;
  }
  const latest = rows.at(-1);
  const high = rows.reduce((best, row) => (row.ttm > best.ttm ? row : best), rows[0]);
  const low = rows.reduce((best, row) => (row.ttm < best.ttm ? row : best), rows[0]);
  els.latestRate.textContent = numberText(latest.ttm);
  els.latestDate.textContent = latest.date;
  els.metricRange.textContent = `${rows[0].date} ~ ${latest.date}`;
  els.metricHigh.textContent = `${numberText(high.ttm)} · ${high.date}`;
  els.metricLow.textContent = `${numberText(low.ttm)} · ${low.date}`;
}

function renderChart() {
  const allPoints = aggregateRows(state.rows, state.granularity);
  const windowed = chartWindow(allPoints);
  const points = windowed.points;
  const series = activeSeries();
  const labels = { day: "每日趋势", week: "周平均趋势", month: "月平均趋势" };
  const rangeText = points.length ? ` · ${points[0].label} ~ ${points.at(-1).label}` : "";
  const windowText = allPoints.length && points.length < allPoints.length ? ` · 显示 ${points.length}/${allPoints.length} 个点` : ` · ${points.length} 个点`;
  els.chartSubtitle.textContent = `${labels[state.granularity]}${windowText}${rangeText}`;
  state.chartMeta = null;

  const canvas = els.canvas;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(640, rect.width) * dpr;
  const height = 360 * dpr;
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);

  const cssWidth = width / dpr;
  const cssHeight = height / dpr;
  ctx.clearRect(0, 0, cssWidth, cssHeight);
  ctx.fillStyle = "rgba(255,255,255,0.56)";
  ctx.fillRect(0, 0, cssWidth, cssHeight);

  if (points.length < 2) {
    ctx.fillStyle = "#6c7782";
    ctx.font = "16px Avenir Next";
    ctx.fillText("数据不足，无法绘制曲线。", 28, 42);
    return;
  }
  if (!series.length) {
    ctx.fillStyle = "#6c7782";
    ctx.font = "16px Avenir Next";
    ctx.fillText("请选择至少一种汇率曲线。", 28, 42);
    return;
  }

  const padding = { left: 58, right: 24, top: 24, bottom: 48 };
  const values = points.flatMap((point) => series.map((key) => point[key]));
  const min = Math.floor(Math.min(...values) - 0.8);
  const max = Math.ceil(Math.max(...values) + 0.8);
  const chartWidth = cssWidth - padding.left - padding.right;
  const chartHeight = cssHeight - padding.top - padding.bottom;

  const x = (index) => padding.left + (index / (points.length - 1)) * chartWidth;
  const y = (value) => padding.top + (1 - (value - min) / (max - min)) * chartHeight;

  ctx.strokeStyle = "rgba(40,58,73,0.12)";
  ctx.lineWidth = 1;
  ctx.fillStyle = "#6c7782";
  ctx.font = "12px Avenir Next";
  for (let step = 0; step <= 4; step += 1) {
    const value = min + ((max - min) * step) / 4;
    const yy = y(value);
    ctx.beginPath();
    ctx.moveTo(padding.left, yy);
    ctx.lineTo(cssWidth - padding.right, yy);
    ctx.stroke();
    ctx.fillText(numberText(value), 10, yy + 4);
  }

  const meta = { points, allPoints, series, padding, cssWidth, cssHeight, chartWidth, chartHeight, x, y, ...windowed };
  state.chartMeta = meta;
  for (const key of series) {
    drawLine(ctx, points, x, y, key, seriesConfig[key].color, key === "ttm" ? 3.5 : 2);
  }
  if (state.visibleSeries.ttm) {
    drawExtremaMarkers(ctx, meta);
  }

  const labelCount = Math.min(6, points.length);
  for (let index = 0; index < labelCount; index += 1) {
    const pointIndex = Math.round((index / (labelCount - 1)) * (points.length - 1));
    const point = points[pointIndex];
    ctx.fillStyle = "#6c7782";
    ctx.fillText(point.label, x(pointIndex) - 32, cssHeight - 18);
  }

  if (state.hoverIndex !== null) {
    drawHover(ctx, meta, state.hoverIndex);
  }
}

function drawLine(ctx, points, x, y, key, color, width) {
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.beginPath();
  points.forEach((point, index) => {
    const xx = x(index);
    const yy = y(point[key]);
    if (index === 0) ctx.moveTo(xx, yy);
    else ctx.lineTo(xx, yy);
  });
  ctx.stroke();
}

function chartWindow(allPoints) {
  if (allPoints.length <= 1) {
    return { points: allPoints, startIndex: 0, endIndex: Math.max(0, allPoints.length - 1), total: allPoints.length };
  }
  const total = allPoints.length;
  const startIndex = clamp(Math.floor(state.viewport.start * (total - 1)), 0, total - 2);
  const endIndex = clamp(Math.ceil(state.viewport.end * (total - 1)), startIndex + 1, total - 1);
  return {
    points: allPoints.slice(startIndex, endIndex + 1),
    startIndex,
    endIndex,
    total,
  };
}

function setViewportIndices(total, startIndex, endIndex) {
  if (total <= 1) {
    state.viewport = { start: 0, end: 1 };
    return;
  }
  const minWindow = Math.min(total - 1, 8);
  let start = clamp(startIndex, 0, total - 1);
  let end = clamp(endIndex, 0, total - 1);
  if (end - start < minWindow) {
    const center = (start + end) / 2;
    start = center - minWindow / 2;
    end = center + minWindow / 2;
  }
  if (start < 0) {
    end -= start;
    start = 0;
  }
  if (end > total - 1) {
    start -= end - (total - 1);
    end = total - 1;
  }
  start = clamp(start, 0, total - 1);
  end = clamp(end, start + 1, total - 1);
  state.viewport = {
    start: start / (total - 1),
    end: end / (total - 1),
  };
}

function drawExtremaMarkers(ctx, meta) {
  const highIndex = meta.points.reduce((bestIndex, point, index) => (point.ttm > meta.points[bestIndex].ttm ? index : bestIndex), 0);
  const lowIndex = meta.points.reduce((bestIndex, point, index) => (point.ttm < meta.points[bestIndex].ttm ? index : bestIndex), 0);
  drawExtremaMarker(ctx, meta, highIndex, "最高 TTM", "#a94f45", "above");
  if (lowIndex !== highIndex) {
    drawExtremaMarker(ctx, meta, lowIndex, "最低 TTM", "#23715d", "below");
  }
}

function drawExtremaMarker(ctx, meta, index, title, color, preferredSide) {
  const point = meta.points[index];
  const xx = meta.x(index);
  const yy = meta.y(point.ttm);
  const chartRight = meta.cssWidth - meta.padding.right;
  const chartBottom = meta.cssHeight - meta.padding.bottom;

  ctx.save();
  ctx.setLineDash([6, 6]);
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.25;
  ctx.beginPath();
  ctx.moveTo(meta.padding.left, yy);
  ctx.lineTo(chartRight, yy);
  ctx.stroke();

  ctx.setLineDash([4, 5]);
  ctx.beginPath();
  ctx.moveTo(xx, yy);
  ctx.lineTo(xx, chartBottom);
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(xx, yy, 5, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = "rgba(255,255,255,0.95)";
  ctx.lineWidth = 2.5;
  ctx.stroke();

  const lines = [title, `${point.label} · ${numberText(point.ttm)}`];
  ctx.font = "600 12px Avenir Next";
  const width = Math.max(...lines.map((line) => ctx.measureText(line).width)) + 22;
  const height = 44;
  const labelX = Math.min(Math.max(xx + 12, 12), meta.cssWidth - width - 12);
  const proposedY = preferredSide === "above" ? yy - height - 10 : yy + 10;
  const labelY = Math.min(Math.max(proposedY, meta.padding.top + 6), chartBottom - height - 6);

  ctx.fillStyle = "rgba(255, 252, 245, 0.94)";
  roundRect(ctx, labelX, labelY, width, height, 13);
  ctx.fill();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  ctx.stroke();

  ctx.fillStyle = color;
  ctx.fillText(lines[0], labelX + 11, labelY + 18);
  ctx.fillStyle = "#1b2733";
  ctx.font = "12px Avenir Next";
  ctx.fillText(lines[1], labelX + 11, labelY + 35);
  ctx.restore();
}

const seriesConfig = {
  ttb: { label: "TTB", color: "#23715d" },
  ttm: { label: "TTM", color: "#173b5f" },
  tts: { label: "TTS", color: "#a94f45" },
};

function activeSeries() {
  return Object.keys(seriesConfig).filter((key) => state.visibleSeries[key]);
}

function drawHover(ctx, meta, index) {
  const safeIndex = Math.max(0, Math.min(meta.points.length - 1, index));
  const point = meta.points[safeIndex];
  const xx = meta.x(safeIndex);

  ctx.save();
  ctx.strokeStyle = "rgba(27, 39, 51, 0.28)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(xx, meta.padding.top);
  ctx.lineTo(xx, meta.cssHeight - meta.padding.bottom);
  ctx.stroke();

  for (const key of meta.series) {
    ctx.fillStyle = seriesConfig[key].color;
    ctx.beginPath();
    ctx.arc(xx, meta.y(point[key]), key === "ttm" ? 4.5 : 3.8, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.9)";
    ctx.lineWidth = 2;
    ctx.stroke();
  }

  const lines = [
    point.label,
    ...meta.series.map((key) => `${seriesConfig[key].label}: ${numberText(point[key])}`),
  ];
  ctx.font = "12px Avenir Next";
  const width = Math.max(...lines.map((line) => ctx.measureText(line).width)) + 24;
  const height = 22 + lines.length * 18;
  const boxX = Math.min(Math.max(xx + 12, 12), meta.cssWidth - width - 12);
  const boxY = Math.max(meta.padding.top + 6, Math.min(meta.y(point.ttm) - height / 2, meta.cssHeight - height - 16));

  ctx.fillStyle = "rgba(27, 39, 51, 0.92)";
  roundRect(ctx, boxX, boxY, width, height, 14);
  ctx.fill();

  ctx.fillStyle = "#fffaf2";
  ctx.font = "600 12px Avenir Next";
  ctx.fillText(lines[0], boxX + 12, boxY + 20);
  ctx.font = "12px Avenir Next";
  lines.slice(1).forEach((line, lineIndex) => {
    ctx.fillText(line, boxX + 12, boxY + 40 + lineIndex * 18);
  });
  ctx.restore();
}

function roundRect(ctx, x, y, width, height, radius) {
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + width, y, x + width, y + height, radius);
  ctx.arcTo(x + width, y + height, x, y + height, radius);
  ctx.arcTo(x, y + height, x, y, radius);
  ctx.arcTo(x, y, x + width, y, radius);
  ctx.closePath();
}

function renderTable() {
  if (!state.rows.length) {
    els.table.innerHTML = `<tr><td class="empty-row" colspan="7">没有数据。请先生成年度 CSV 或启动本地服务更新。</td></tr>`;
    return;
  }
  els.table.innerHTML = state.rows
    .slice()
    .reverse()
    .map(
      (row) => `
        <tr>
          <td>${escapeHtml(row.date)}</td>
          <td>${escapeHtml(weekdayText(row.date))}</td>
          <td>${numberText(row.ttb)}</td>
          <td>${numberText(row.ttm)}</td>
          <td>${numberText(row.tts)}</td>
          <td><a href="${escapeHtml(row.url)}" target="_blank" rel="noreferrer">PDF</a></td>
          <td><button class="edit-row-button" type="button" data-edit="${escapeHtml(row.date)}" ${state.apiAvailable ? "" : "disabled"}>修改</button></td>
        </tr>
      `,
    )
    .join("");
  document.querySelectorAll("[data-edit]").forEach((button) => {
    button.addEventListener("click", () => startEdit(button.dataset.edit));
  });
}

function renderCorrections() {
  if (!state.corrections.length) {
    els.correctionLog.textContent = "还没有修正记录。";
    return;
  }
  els.correctionLog.innerHTML = state.corrections
    .slice()
    .reverse()
    .slice(0, 12)
    .map((entry) => {
      const before = entry.before ? `原 TTM ${entry.before.ttm}` : "原始记录为空";
      const after = entry.after ? `新 TTM ${entry.after.ttm}` : "";
      return `<div class="log-entry"><strong>${escapeHtml(entry.date)}</strong> · ${escapeHtml(before)} → ${escapeHtml(after)}<br />${escapeHtml(entry.reason || "未填写原因")}</div>`;
    })
    .join("");
}

function startEdit(dateText) {
  const row = state.rows.find((item) => item.date === dateText);
  if (!row) return;
  els.editDate.value = row.date;
  els.editTtb.value = numberText(row.ttb);
  els.editTtm.value = numberText(row.ttm);
  els.editTts.value = numberText(row.tts);
  els.editReason.value = "";
  els.editCard.hidden = false;
  els.editCard.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function submitCorrection(event) {
  event.preventDefault();
  if (!state.apiAvailable) {
    alert("请先通过 python3 server.py 启动本地服务，静态页面不能写入数据。");
    return;
  }
  const payload = {
    date: els.editDate.value,
    ttb: els.editTtb.value,
    ttm: els.editTtm.value,
    tts: els.editTts.value,
    reason: els.editReason.value,
  };
  const response = await fetch("./api/correct", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok || !result.ok) {
    alert(result.error || "修正失败");
    return;
  }
  els.editCard.hidden = true;
  await loadData();
}

async function startUpdate() {
  if (!state.apiAvailable) {
    alert("静态展示模式无法写入数据。请在项目目录运行 python3 server.py 后再点击获取最新汇率。");
    return;
  }
  els.progressCard.hidden = false;
  setProgress(0, "正在启动更新任务...", "正在更新...");
  els.refreshButton.disabled = true;
  const response = await fetch("./api/update", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ year: state.dataScope === "all" ? currentYear : Number(els.yearInput.value || currentYear) }),
  });
  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    setProgress(100, payload.error || "更新任务启动失败", "更新失败");
    els.refreshButton.disabled = false;
    return;
  }
  pollUpdate(payload.job.id);
}

async function pollUpdate(jobId) {
  const response = await fetch(`./api/update-status?id=${encodeURIComponent(jobId)}`, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    setProgress(100, payload.error || "无法读取更新进度", "更新失败");
    els.refreshButton.disabled = false;
    return;
  }
  const job = payload.job;
  setProgress(job.progress || 0, job.message || "更新中...", job.status === "done" ? "更新完成" : "正在更新...");
  if (job.status === "done") {
    els.refreshButton.disabled = false;
    await loadData();
    return;
  }
  if (job.status === "error") {
    els.refreshButton.disabled = false;
    setProgress(100, job.message || "更新失败", "更新失败");
    return;
  }
  window.setTimeout(() => pollUpdate(jobId), 600);
}

function setProgress(percent, message, title) {
  els.progressTitle.textContent = title;
  els.progressPercent.textContent = `${Math.round(percent)}%`;
  els.progressBar.style.width = `${Math.min(100, Math.max(0, percent))}%`;
  els.progressMessage.textContent = message;
}

function exportCsv() {
  const headers = ["日期", "TTB", "TTM", "TTS", "URL"];
  const body = exportRows().map((row) => [row.date, row.ttb, row.ttm, row.tts, row.url].map(csvCell).join(","));
  const blob = new Blob([[headers.join(","), ...body].join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = state.dataScope === "all" ? "usd-jpy-all.csv" : `usd-jpy-${state.year}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

function exportRows() {
  const byDate = new Map(state.rows.map((row) => [row.date, row]));
  const rows = [];
  const startYear = state.dataScope === "all" && state.rows.length ? Number(state.rows[0].date.slice(0, 4)) : state.year;
  const start = new Date(startYear, 0, 1);
  const lastDataDate = state.rows.length ? parseLocalDate(state.rows.at(-1).date) : start;
  const end = state.dataScope === "all" ? new Date() : selectedYearEnd();
  const safeEnd = lastDataDate > end ? lastDataDate : end;
  for (let current = start; current <= safeEnd; current = addDays(current, 1)) {
    const dateText = isoDate(current);
    const row = byDate.get(dateText);
    rows.push({
      date: dateText,
      ttb: row ? numberText(row.ttb) : "",
      ttm: row ? numberText(row.ttm) : "",
      tts: row ? numberText(row.tts) : "",
      url: row ? row.url : "",
    });
  }
  return rows;
}

function csvCell(value) {
  return `"${String(value ?? "").replaceAll('"', '""')}"`;
}

function updateSeriesButtons() {
  els.seriesButtons.forEach((button) => {
    button.classList.toggle("is-off", !state.visibleSeries[button.dataset.series]);
  });
}

function updateScopeButtons() {
  els.scopeButtons.forEach((button) => {
    button.classList.toggle("is-active", button.dataset.scope === state.dataScope);
  });
  els.yearInput.disabled = state.dataScope === "all";
}

document.querySelectorAll("[data-granularity]").forEach((button) => {
  button.addEventListener("click", () => {
    state.granularity = button.dataset.granularity;
    document.querySelectorAll("[data-granularity]").forEach((node) => node.classList.toggle("is-active", node === button));
    resetViewport();
    renderChart();
  });
});

els.scopeButtons.forEach((button) => {
  button.addEventListener("click", async () => {
    state.dataScope = button.dataset.scope;
    resetViewport();
    updateScopeButtons();
    await loadData();
  });
});

els.seriesButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const key = button.dataset.series;
    state.visibleSeries[key] = !state.visibleSeries[key];
    state.hoverIndex = null;
    updateSeriesButtons();
    renderChart();
  });
});

els.canvas.addEventListener("mousemove", (event) => {
  const meta = state.chartMeta;
  if (!meta) return;
  const rect = els.canvas.getBoundingClientRect();
  const mouseX = event.clientX - rect.left;
  if (state.drag) {
    const deltaX = mouseX - state.drag.mouseX;
    const deltaPoints = -(deltaX / meta.chartWidth) * (state.drag.endIndex - state.drag.startIndex);
    setViewportIndices(meta.total, state.drag.startIndex + deltaPoints, state.drag.endIndex + deltaPoints);
    state.hoverIndex = null;
    renderChart();
    return;
  }
  if (mouseX < meta.padding.left || mouseX > meta.cssWidth - meta.padding.right) {
    if (state.hoverIndex !== null) {
      state.hoverIndex = null;
      renderChart();
    }
    return;
  }
  const index = Math.round(((mouseX - meta.padding.left) / meta.chartWidth) * (meta.points.length - 1));
  if (index !== state.hoverIndex) {
    state.hoverIndex = index;
    renderChart();
  }
});

els.canvas.addEventListener("wheel", (event) => {
  const meta = state.chartMeta;
  if (!meta || meta.total <= 2) return;
  event.preventDefault();
  const rect = els.canvas.getBoundingClientRect();
  const mouseX = clamp(event.clientX - rect.left, meta.padding.left, meta.cssWidth - meta.padding.right);
  const focus = (mouseX - meta.padding.left) / meta.chartWidth;
  const focusIndex = meta.startIndex + focus * (meta.endIndex - meta.startIndex);
  const factor = event.deltaY < 0 ? 0.78 : 1.22;
  const newStart = focusIndex - (focusIndex - meta.startIndex) * factor;
  const newEnd = focusIndex + (meta.endIndex - focusIndex) * factor;
  setViewportIndices(meta.total, newStart, newEnd);
  state.hoverIndex = null;
  renderChart();
}, { passive: false });

els.canvas.addEventListener("mousedown", (event) => {
  const meta = state.chartMeta;
  if (!meta || meta.total <= meta.points.length) return;
  const rect = els.canvas.getBoundingClientRect();
  const mouseX = event.clientX - rect.left;
  const mouseY = event.clientY - rect.top;
  if (mouseX < meta.padding.left || mouseX > meta.cssWidth - meta.padding.right || mouseY < meta.padding.top || mouseY > meta.cssHeight - meta.padding.bottom) {
    return;
  }
  state.drag = {
    mouseX,
    startIndex: meta.startIndex,
    endIndex: meta.endIndex,
  };
  els.canvas.classList.add("is-panning");
});

window.addEventListener("mouseup", () => {
  if (!state.drag) return;
  state.drag = null;
  els.canvas.classList.remove("is-panning");
});

els.canvas.addEventListener("mouseleave", () => {
  if (state.drag) return;
  if (state.hoverIndex !== null) {
    state.hoverIndex = null;
    renderChart();
  }
});

els.resetZoomButton.addEventListener("click", () => {
  resetViewport();
  renderChart();
});

els.yearInput.value = String(currentYear);
els.yearInput.addEventListener("change", loadData);
els.refreshButton.addEventListener("click", startUpdate);
els.reloadButton.addEventListener("click", loadData);
els.exportButton.addEventListener("click", exportCsv);
els.cancelEditButton.addEventListener("click", () => {
  els.editCard.hidden = true;
});
els.editForm.addEventListener("submit", submitCorrection);
window.addEventListener("resize", () => renderChart());

loadData();
