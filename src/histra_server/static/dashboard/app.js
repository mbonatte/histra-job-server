(() => {
  "use strict";

  const API = "/api/v1/dashboard";
  const state = {
    view: "overview",
    catalog: null,
    summary: null,
    jobs: {items: [], total: 0, offset: 0, limit: 50, sort: "created_at", direction: "desc"},
    filters: {},
    completedJobs: [],
    resultJob: null,
    statistics: null,
    workers: [],
  };

  const titles = {
    overview: ["Overview", "Job throughput, worker availability and analysis health."],
    jobs: ["Jobs", "Search, filter and inspect every numerical-analysis job."],
    results: ["Results explorer", "Inspect response histories, scour mutations, validation and run metadata."],
    statistics: ["Statistics", "Compare response metrics across analyses, scenarios and workers."],
    workers: ["Workers", "Execution capacity, availability, versions and reliability."],
  };

  const palette = ["#176b87", "#35a57b", "#db8b28", "#8d63c7", "#d15b56", "#5b7cdb"];
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function showLoading(show) {
    $("#loading").classList.toggle("hidden", !show);
  }

  function toast(message, error = false) {
    const node = $("#toast");
    node.textContent = message;
    node.classList.toggle("error", error);
    node.classList.add("visible");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => node.classList.remove("visible"), 3500);
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {headers: {Accept: "application/json"}, ...options});
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const body = await response.json();
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || body);
      } catch (_) {}
      throw new Error(detail);
    }
    if (response.status === 204) return null;
    return response.json();
  }

  function queryString(params) {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") query.set(key, value);
    });
    return query.toString();
  }

  function formatNumber(value, digits = 3) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    const magnitude = Math.abs(number);
    if (magnitude >= 1e9) return `${(number / 1e9).toFixed(2)}B`;
    if (magnitude >= 1e6) return `${(number / 1e6).toFixed(2)}M`;
    if (magnitude >= 1e3) return `${(number / 1e3).toFixed(2)}k`;
    if (magnitude !== 0 && magnitude < 0.001) return number.toExponential(2);
    return number.toLocaleString(undefined, {maximumFractionDigits: digits});
  }

  function formatPercent(value) {
    return value === null || value === undefined ? "—" : `${(Number(value) * 100).toFixed(1)}%`;
  }

  function formatDuration(seconds) {
    if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))) return "—";
    const total = Math.max(0, Math.round(Number(seconds)));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    if (hours) return `${hours}h ${minutes}m`;
    if (minutes) return `${minutes}m ${secs}s`;
    return `${secs}s`;
  }

  function formatBytes(bytes) {
    if (bytes === null || bytes === undefined) return "—";
    const value = Number(bytes);
    const units = ["B", "KB", "MB", "GB", "TB"];
    let index = 0;
    let scaled = value;
    while (scaled >= 1024 && index < units.length - 1) { scaled /= 1024; index += 1; }
    return `${scaled.toFixed(index ? 1 : 0)} ${units[index]}`;
  }

  function formatDate(value, includeTime = true) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString(undefined, includeTime
      ? {year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"}
      : {year: "numeric", month: "short", day: "numeric"});
  }

  function statusPill(status) {
    const value = String(status || "unknown");
    return `<span class="status-pill status-${escapeHtml(value)}">${escapeHtml(value.replaceAll("_", " "))}</span>`;
  }

  function kpi(label, value, note = "") {
    return `<div class="kpi-card"><div class="kpi-label">${escapeHtml(label)}</div><div class="kpi-value">${escapeHtml(value)}</div><div class="kpi-note">${escapeHtml(note)}</div></div>`;
  }

  function setOptions(select, items, valueKey = null, labelKey = null, first = null) {
    const previous = select.value;
    const html = [];
    if (first) html.push(`<option value="${escapeHtml(first.value)}">${escapeHtml(first.label)}</option>`);
    items.forEach((item) => {
      const value = valueKey ? item[valueKey] : item;
      const label = labelKey ? item[labelKey] : item;
      html.push(`<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`);
    });
    select.innerHTML = html.join("");
    if ($$(`option`, select).some((option) => option.value === previous)) select.value = previous;
  }

  function makeSvg(container, height = 290) {
    container.innerHTML = "";
    const width = Math.max(360, container.clientWidth || 640);
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("role", "img");
    container.appendChild(svg);
    return {svg, width, height};
  }

  function svgNode(name, attrs = {}, text = null) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", name);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
    if (text !== null) node.textContent = text;
    return node;
  }

  function numericExtent(values) {
    const clean = values.filter((value) => Number.isFinite(value));
    if (!clean.length) return [0, 1];
    let min = Math.min(...clean);
    let max = Math.max(...clean);
    if (min === max) {
      const padding = Math.abs(min || 1) * 0.15;
      min -= padding;
      max += padding;
    }
    const padding = (max - min) * 0.08;
    return [min - padding, max + padding];
  }

  function drawAxes(svg, width, height, margins, xTicks, yTicks, xMap, yMap, xFormatter = formatNumber, yFormatter = formatNumber) {
    const {left, right, top, bottom} = margins;
    const chartWidth = width - left - right;
    const chartHeight = height - top - bottom;
    yTicks.forEach((tick) => {
      const y = yMap(tick);
      svg.appendChild(svgNode("line", {x1: left, y1: y, x2: left + chartWidth, y2: y, stroke: "#dce3ee", "stroke-width": .8, opacity: .7}));
      svg.appendChild(svgNode("text", {x: left - 8, y: y + 4, "text-anchor": "end", fill: "#738397", "font-size": 10}, yFormatter(tick)));
    });
    xTicks.forEach((tick) => {
      const x = xMap(tick.value ?? tick);
      const label = tick.label ?? xFormatter(tick.value ?? tick);
      svg.appendChild(svgNode("text", {x, y: height - bottom + 20, "text-anchor": "middle", fill: "#738397", "font-size": 10}, label));
    });
    svg.appendChild(svgNode("line", {x1: left, y1: top + chartHeight, x2: left + chartWidth, y2: top + chartHeight, stroke: "#9aabba", "stroke-width": 1}));
  }

  function ticks(min, max, count = 5) {
    if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
    const step = (max - min) / Math.max(1, count - 1);
    return Array.from({length: count}, (_, index) => min + index * step);
  }

  function drawLineChart(container, series, options = {}) {
    const usable = series.filter((item) => item.points && item.points.some((point) => Number.isFinite(Number(point.y))));
    if (!usable.length) {
      container.innerHTML = `<div class="chart-empty">No data available</div>`;
      return;
    }
    const {svg, width, height} = makeSvg(container, options.height || 290);
    const margins = {left: 58, right: 22, top: 22, bottom: 45};
    const allX = usable.flatMap((item) => item.points.map((point) => Number(point.x)));
    const allY = usable.flatMap((item) => item.points.map((point) => Number(point.y)));
    const [xMin, xMax] = numericExtent(allX);
    let [yMin, yMax] = numericExtent(allY);
    if (options.zeroBaseline && Math.min(...allY) >= 0) yMin = 0;
    const chartWidth = width - margins.left - margins.right;
    const chartHeight = height - margins.top - margins.bottom;
    const xMap = (value) => margins.left + ((Number(value) - xMin) / (xMax - xMin)) * chartWidth;
    const yMap = (value) => margins.top + chartHeight - ((Number(value) - yMin) / (yMax - yMin)) * chartHeight;
    let xTickValues;
    if (options.xLabels) {
      const indexes = Object.keys(options.xLabels).map(Number).filter(Number.isFinite);
      const maxTicks = 7;
      const step = Math.max(1, Math.ceil(indexes.length / maxTicks));
      xTickValues = indexes.filter((_, index) => index % step === 0 || index === indexes.length - 1).map((value) => ({value, label: options.xLabels[value]}));
    } else {
      xTickValues = ticks(xMin, xMax, 6).map((value) => ({value, label: formatNumber(value, 2)}));
    }
    drawAxes(svg, width, height, margins, xTickValues, ticks(yMin, yMax, 5), xMap, yMap, formatNumber, (value) => formatNumber(value, 2));
    usable.forEach((item, index) => {
      const color = palette[index % palette.length];
      const sorted = [...item.points].filter((point) => Number.isFinite(Number(point.x)) && Number.isFinite(Number(point.y))).sort((a, b) => Number(a.x) - Number(b.x));
      const path = sorted.map((point, pointIndex) => `${pointIndex ? "L" : "M"}${xMap(point.x).toFixed(2)},${yMap(point.y).toFixed(2)}`).join(" ");
      svg.appendChild(svgNode("path", {d: path, fill: "none", stroke: color, "stroke-width": 2.2, "stroke-linecap": "round", "stroke-linejoin": "round"}));
      sorted.forEach((point) => {
        const circle = svgNode("circle", {cx: xMap(point.x), cy: yMap(point.y), r: 2.8, fill: color, stroke: "#fff", "stroke-width": 1});
        circle.appendChild(svgNode("title", {}, `${item.name}: ${formatNumber(point.y)} · ${options.xName || "x"}: ${options.xLabels ? options.xLabels[point.x] : formatNumber(point.x)}`));
        svg.appendChild(circle);
      });
    });
    const legend = document.createElement("div");
    legend.className = "chart-legend";
    legend.innerHTML = usable.map((item, index) => `<span class="legend-item"><i class="legend-swatch" style="background:${palette[index % palette.length]}"></i>${escapeHtml(item.name)}</span>`).join("");
    container.appendChild(legend);
  }

  function drawHorizontalBars(container, rows, options = {}) {
    const usable = rows.filter((row) => Number.isFinite(Number(row.value)));
    if (!usable.length) { container.innerHTML = `<div class="chart-empty">No data available</div>`; return; }
    const height = Math.max(options.height || 290, usable.length * 34 + 55);
    const {svg, width} = makeSvg(container, height);
    const margins = {left: Math.min(170, Math.max(90, Math.max(...usable.map((row) => String(row.label).length)) * 7)), right: 45, top: 15, bottom: 28};
    const max = Math.max(...usable.map((row) => Number(row.value)), 1);
    const chartWidth = width - margins.left - margins.right;
    const rowHeight = (height - margins.top - margins.bottom) / usable.length;
    usable.forEach((row, index) => {
      const y = margins.top + index * rowHeight + rowHeight * .18;
      const barHeight = rowHeight * .64;
      const barWidth = (Number(row.value) / max) * chartWidth;
      svg.appendChild(svgNode("text", {x: margins.left - 8, y: y + barHeight * .72, "text-anchor": "end", fill: "#738397", "font-size": 11}, String(row.label)));
      const rect = svgNode("rect", {x: margins.left, y, width: Math.max(1, barWidth), height: barHeight, rx: 4, fill: palette[index % palette.length]});
      rect.appendChild(svgNode("title", {}, `${row.label}: ${formatNumber(row.value)}`));
      svg.appendChild(rect);
      svg.appendChild(svgNode("text", {x: margins.left + barWidth + 7, y: y + barHeight * .72, fill: "#738397", "font-size": 10}, formatNumber(row.value)));
    });
  }

  function drawHistogram(container, values) {
    const clean = values.map(Number).filter(Number.isFinite);
    if (!clean.length) { container.innerHTML = `<div class="chart-empty">No observations</div>`; return; }
    const binsCount = Math.max(5, Math.min(16, Math.ceil(Math.sqrt(clean.length))));
    let min = Math.min(...clean);
    let max = Math.max(...clean);
    if (min === max) { min -= .5; max += .5; }
    const binWidth = (max - min) / binsCount;
    const bins = Array.from({length: binsCount}, (_, index) => ({start: min + index * binWidth, end: min + (index + 1) * binWidth, count: 0}));
    clean.forEach((value) => {
      const index = Math.min(binsCount - 1, Math.floor((value - min) / binWidth));
      bins[index].count += 1;
    });
    const {svg, width, height} = makeSvg(container, 290);
    const margins = {left: 48, right: 18, top: 18, bottom: 45};
    const chartWidth = width - margins.left - margins.right;
    const chartHeight = height - margins.top - margins.bottom;
    const maxCount = Math.max(...bins.map((bin) => bin.count), 1);
    const barWidth = chartWidth / binsCount;
    ticks(0, maxCount, 5).forEach((tick) => {
      const y = margins.top + chartHeight - (tick / maxCount) * chartHeight;
      svg.appendChild(svgNode("line", {x1: margins.left, y1: y, x2: width - margins.right, y2: y, stroke: "#dce3ee", "stroke-width": .8}));
      svg.appendChild(svgNode("text", {x: margins.left - 7, y: y + 4, "text-anchor": "end", fill: "#738397", "font-size": 10}, formatNumber(tick, 0)));
    });
    bins.forEach((bin, index) => {
      const h = (bin.count / maxCount) * chartHeight;
      const rect = svgNode("rect", {x: margins.left + index * barWidth + 1, y: margins.top + chartHeight - h, width: Math.max(1, barWidth - 2), height: h, rx: 2, fill: palette[0]});
      rect.appendChild(svgNode("title", {}, `${formatNumber(bin.start)} to ${formatNumber(bin.end)}: ${bin.count}`));
      svg.appendChild(rect);
    });
    const labelIndexes = [0, Math.floor((binsCount - 1) / 2), binsCount - 1];
    labelIndexes.forEach((index) => {
      const bin = bins[index];
      svg.appendChild(svgNode("text", {x: margins.left + (index + .5) * barWidth, y: height - 17, "text-anchor": "middle", fill: "#738397", "font-size": 10}, formatNumber((bin.start + bin.end) / 2, 2)));
    });
  }

  function drawScatter(container, points, options = {}) {
    const clean = points.filter((point) => Number.isFinite(Number(point.x)) && Number.isFinite(Number(point.y)));
    if (!clean.length) { container.innerHTML = `<div class="chart-empty">No observations</div>`; return; }
    const {svg, width, height} = makeSvg(container, 290);
    const margins = {left: 58, right: 22, top: 20, bottom: 45};
    const [xMin, xMax] = numericExtent(clean.map((point) => Number(point.x)));
    const [yMin, yMax] = numericExtent(clean.map((point) => Number(point.y)));
    const chartWidth = width - margins.left - margins.right;
    const chartHeight = height - margins.top - margins.bottom;
    const xMap = (value) => margins.left + ((Number(value) - xMin) / (xMax - xMin)) * chartWidth;
    const yMap = (value) => margins.top + chartHeight - ((Number(value) - yMin) / (yMax - yMin)) * chartHeight;
    drawAxes(svg, width, height, margins, ticks(xMin, xMax, 6).map((value) => ({value, label: formatNumber(value, 2)})), ticks(yMin, yMax, 5), xMap, yMap);
    clean.forEach((point) => {
      const circle = svgNode("circle", {cx: xMap(point.x), cy: yMap(point.y), r: 4, fill: palette[0], opacity: .78, stroke: "#fff", "stroke-width": 1});
      circle.appendChild(svgNode("title", {}, `${point.label || "Observation"}\nX: ${formatNumber(point.x)}\nY: ${formatNumber(point.y)}`));
      svg.appendChild(circle);
    });
  }

  function switchView(view) {
    state.view = view;
    $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
    $$(".view").forEach((section) => section.classList.toggle("active", section.id === `view-${view}`));
    $("#page-title").textContent = titles[view][0];
    $("#page-subtitle").textContent = titles[view][1];
    if (view === "jobs" && !state.jobs.items.length) loadJobs();
    if (view === "results" && !state.completedJobs.length) loadCompletedJobs();
    if (view === "statistics" && !state.statistics) runStatistics();
    if (view === "workers" && !state.workers.length) loadWorkers();
  }

  function renderOverview() {
    const data = state.summary;
    if (!data) return;
    const jobs = data.jobs;
    const duration = data.duration_seconds;
    const workers = data.workers;
    $("#overview-kpis").innerHTML = [
      kpi("Total jobs", jobs.total, `${jobs.active} currently active`),
      kpi("Completion rate", formatPercent(jobs.completion_rate), `${jobs.by_status.completed || 0} completed`),
      kpi("Median duration", formatDuration(duration.median), `${duration.count} completed runs`),
      kpi("Workers online", `${workers.online}/${workers.enabled}`, `${workers.capacity} execution slots`),
    ].join("");
    const xLabels = {};
    const points = data.throughput.map((row, index) => {
      xLabels[index] = new Date(`${row.date}T00:00:00`).toLocaleDateString(undefined, {month: "short", day: "numeric"});
      return {x: index, y: row.completed};
    });
    drawLineChart($("#throughput-chart"), [{name: "Completed", points}], {xLabels, xName: "Date", zeroBaseline: true});
    drawHorizontalBars($("#status-chart"), Object.entries(jobs.by_status).map(([label, value]) => ({label, value})));
    $("#recent-jobs-body").innerHTML = data.recent_jobs.length ? data.recent_jobs.map((job) => jobTableRow(job)).join("") : `<tr><td colspan="7" class="empty-cell">No jobs have been created.</td></tr>`;
    bindJobRowClicks($("#recent-jobs-body"));
  }

  function jobTableRow(job, includeActions = false) {
    return `<tr data-job-id="${escapeHtml(job.id)}">
      <td class="job-cell"><span class="job-name">${escapeHtml(job.id)}</span><span class="job-sub">${escapeHtml(job.model_filename || "")}</span></td>
      <td>${escapeHtml(job.scenario_id || "—")}</td>
      <td>${statusPill(job.status)}</td>
      <td>${escapeHtml(job.worker_name || "—")}</td>
      <td>${escapeHtml(job.analysis_count ?? "—")}</td>
      <td>${escapeHtml(formatDuration(job.duration_seconds))}</td>
      ${includeActions ? `<td>${escapeHtml(`${job.attempt_count}/${job.max_attempts}`)}</td>` : ""}
      <td>${escapeHtml(formatDate(job.created_at))}</td>
      ${includeActions ? `<td><button class="text-button inspect-job" data-job-id="${escapeHtml(job.id)}">Inspect</button></td>` : ""}
    </tr>`;
  }

  function collectFilters() {
    return {
      search: $("#filter-search").value.trim(),
      status: $("#filter-status").value,
      scenario: $("#filter-scenario").value,
      worker_id: $("#filter-worker").value,
      created_from: $("#filter-from").value,
      created_to: $("#filter-to").value,
    };
  }

  async function loadJobs(resetOffset = false) {
    if (resetOffset) state.jobs.offset = 0;
    state.filters = collectFilters();
    const params = {
      ...state.filters,
      sort: state.jobs.sort,
      direction: state.jobs.direction,
      limit: state.jobs.limit,
      offset: state.jobs.offset,
    };
    try {
      const data = await api(`${API}/jobs?${queryString(params)}`);
      state.jobs = {...state.jobs, ...data};
      renderJobs();
    } catch (error) { toast(`Could not load jobs: ${error.message}`, true); }
  }

  function renderJobs() {
    const body = $("#jobs-body");
    body.innerHTML = state.jobs.items.length
      ? state.jobs.items.map((job) => jobTableRow(job, true)).join("")
      : `<tr><td colspan="9" class="empty-cell">No jobs match the selected filters.</td></tr>`;
    $("#jobs-count").textContent = `${state.jobs.total.toLocaleString()} jobs`;
    const currentPage = Math.floor(state.jobs.offset / state.jobs.limit) + 1;
    const pages = Math.max(1, Math.ceil(state.jobs.total / state.jobs.limit));
    $("#jobs-page-label").textContent = `Page ${currentPage} of ${pages}`;
    $("#jobs-prev").disabled = state.jobs.offset === 0;
    $("#jobs-next").disabled = state.jobs.offset + state.jobs.limit >= state.jobs.total;
    bindJobRowClicks(body);
  }

  function bindJobRowClicks(root) {
    $$(`tr[data-job-id]`, root).forEach((row) => row.addEventListener("dblclick", () => openJobDialog(row.dataset.jobId)));
    $$(".inspect-job", root).forEach((button) => button.addEventListener("click", (event) => {
      event.stopPropagation();
      openJobDialog(button.dataset.jobId);
    }));
  }

  async function openJobDialog(jobId) {
    const dialog = $("#job-dialog");
    const content = $("#job-dialog-content");
    content.innerHTML = `<div class="dialog-header"><div><h2>${escapeHtml(jobId)}</h2><p class="muted">Loading job detail…</p></div><button class="dialog-close" aria-label="Close">×</button></div>`;
    $(".dialog-close", content).addEventListener("click", () => dialog.close());
    dialog.showModal();
    try {
      const detail = await api(`${API}/jobs/${encodeURIComponent(jobId)}`);
      renderJobDetail(detail, content, true);
    } catch (error) {
      content.innerHTML = `<div class="dialog-header"><div><h2>${escapeHtml(jobId)}</h2><p class="muted">Could not load job.</p></div><button class="dialog-close">×</button></div><div class="dialog-body"><p>${escapeHtml(error.message)}</p></div>`;
      $(".dialog-close", content).addEventListener("click", () => dialog.close());
    }
  }

  async function loadCompletedJobs() {
    try {
      const data = await api(`${API}/jobs?${queryString({status: "completed", limit: 500, sort: "completed_at", direction: "desc"})}`);
      state.completedJobs = data.items;
      renderResultJobList();
    } catch (error) { toast(`Could not load completed jobs: ${error.message}`, true); }
  }

  function renderResultJobList() {
    const search = $("#result-job-search").value.trim().toLowerCase();
    const rows = state.completedJobs.filter((job) => !search || `${job.id} ${job.scenario_id || ""}`.toLowerCase().includes(search));
    $("#result-job-list").innerHTML = rows.length ? rows.map((job) => `<button class="result-job-item ${state.resultJob === job.id ? "active" : ""}" data-job-id="${escapeHtml(job.id)}"><strong>${escapeHtml(job.id)}</strong><span><b>${escapeHtml(job.scenario_id || "No scenario")}</b><i>${escapeHtml(formatDuration(job.duration_seconds))}</i></span></button>`).join("") : `<div class="empty-cell">No completed jobs.</div>`;
    $$(".result-job-item", $("#result-job-list")).forEach((button) => button.addEventListener("click", () => selectResultJob(button.dataset.jobId)));
  }

  async function selectResultJob(jobId) {
    state.resultJob = jobId;
    renderResultJobList();
    $("#result-detail").innerHTML = `<article class="card empty-state"><h2>Loading ${escapeHtml(jobId)}</h2><p>Reading results from the database.</p></article>`;
    try {
      const detail = await api(`${API}/jobs/${encodeURIComponent(jobId)}`);
      renderJobDetail(detail, $("#result-detail"), false);
    } catch (error) {
      $("#result-detail").innerHTML = `<article class="card empty-state"><h2>Could not load job</h2><p>${escapeHtml(error.message)}</p></article>`;
    }
  }

  function renderJobDetail(detail, container, dialogMode) {
    const job = detail.job;
    const attempt = detail.selected_attempt;
    const results = attempt?.results;
    const analyses = results?.analyses && typeof results.analyses === "object" ? results.analyses : {};
    const analysisNames = Object.keys(analyses);
    const prefix = `detail-${Math.random().toString(36).slice(2, 9)}`;
    const wrapperStart = dialogMode ? `<div class="dialog-header"><div><h2>${escapeHtml(job.id)}</h2><p>${escapeHtml(job.scenario_id || "No scenario")}</p></div><button class="dialog-close" aria-label="Close">×</button></div><div class="dialog-body">` : "";
    const wrapperEnd = dialogMode ? "</div>" : "";
    const metadata = [
      ["Status", job.status], ["Worker", attempt?.worker_name || "—"], ["Duration", formatDuration(attempt?.duration_seconds)], ["Attempts", `${job.attempt_count}/${job.max_attempts}`],
      ["Created", formatDate(job.created_at)], ["Completed", formatDate(job.completed_at)], ["Model", job.model_filename], ["Model size", formatBytes(job.model_size_bytes)],
    ];
    container.innerHTML = `${wrapperStart}
      <article class="card">
        <div class="result-header"><div><h2>${escapeHtml(job.id)}</h2><p>${escapeHtml(job.scenario_id || "No scenario identifier")}</p></div>${statusPill(job.status)}</div>
        <div class="metadata-grid">${metadata.map(([label, value]) => `<div class="metadata-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}</div>
      </article>
      ${analysisNames.length ? `<article class="card">
        <div class="card-heading"><div><h2>Analysis response</h2><p>Reaction and displacement histories extracted by the worker.</p></div></div>
        <div class="analysis-toolbar"><label><span>Analysis</span><select id="${prefix}-analysis">${analysisNames.map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join("")}</select></label><label><span>Model point</span><select id="${prefix}-point"></select></label><label><span>Modal direction</span><select id="${prefix}-modal"><option value="X">X</option><option value="Y">Y</option><option value="Z">Z</option></select></label></div>
        <div class="grid two-columns"><div><h3>Reaction history</h3><div id="${prefix}-reaction" class="chart"></div></div><div><h3>Displacement history</h3><div id="${prefix}-displacement" class="chart"></div></div></div>
        <div class="grid two-columns"><div><h3>Modal contribution</h3><div id="${prefix}-modal-chart" class="chart"></div></div><div><h3>Scour/interface mutation</h3><div id="${prefix}-mutation"></div></div></div>
      </article>` : `<article class="card empty-state"><h2>No extracted results</h2><p>${escapeHtml(attempt?.failure_reason || "This attempt does not contain results.json data.")}</p></article>`}
      <article class="card"><div class="card-heading"><div><h2>Execution</h2><p>Runner provenance and solver stages.</p></div></div>${renderRun(attempt?.run)}</article>
      <article class="card"><div class="card-heading"><div><h2>Attempts</h2><p>All execution attempts for this job.</p></div></div>${renderAttempts(detail.attempts)}</article>
      <article class="card"><div class="card-heading"><div><h2>Artifacts</h2><p>Input packages, models, result files and logs.</p></div></div>${renderArtifacts(detail.artifacts)}</article>
      <article class="card details-list"><details><summary>Job definition</summary><pre class="raw-json">${escapeHtml(JSON.stringify(job.job_definition, null, 2))}</pre></details><details><summary>Results JSON</summary><pre class="raw-json">${escapeHtml(JSON.stringify(results, null, 2))}</pre></details><details><summary>Run JSON</summary><pre class="raw-json">${escapeHtml(JSON.stringify(attempt?.run, null, 2))}</pre></details><details><summary>Validation JSON</summary><pre class="raw-json">${escapeHtml(JSON.stringify(attempt?.validation, null, 2))}</pre></details></article>
      ${wrapperEnd}`;
    if (dialogMode) $(".dialog-close", container).addEventListener("click", () => $("#job-dialog").close());
    if (!analysisNames.length) return;

    const analysisSelect = $(`#${prefix}-analysis`, container);
    const pointSelect = $(`#${prefix}-point`, container);
    const modalSelect = $(`#${prefix}-modal`, container);

    const renderAnalysis = () => {
      const analysis = analyses[analysisSelect.value] || {};
      const outputs = analysis.outputs || {};
      const reactions = Array.isArray(outputs.reactions) ? outputs.reactions : [];
      const reactionComponents = ["R1", "R2", "R3"].filter((component) => reactions.some((row) => Number.isFinite(Number(row[component]))));
      drawLineChart($(`#${prefix}-reaction`, container), reactionComponents.map((component) => ({name: component, points: reactions.map((row) => ({x: Number(row.Step), y: Number(row[component])}))})), {xName: "Step"});

      const displacements = Array.isArray(outputs.displacements) ? outputs.displacements : [];
      const points = [...new Set(displacements.map((row) => row.IdElement).filter((value) => value !== undefined && value !== null))];
      const previousPoint = pointSelect.value;
      pointSelect.innerHTML = points.length ? points.map((point) => `<option value="${escapeHtml(point)}">${escapeHtml(point)}</option>`).join("") : `<option value="">No model points</option>`;
      if (points.map(String).includes(previousPoint)) pointSelect.value = previousPoint;
      const drawDisplacement = () => {
        const selected = pointSelect.value;
        const rows = displacements.filter((row) => String(row.IdElement) === selected);
        const components = ["Ux", "Uy", "Uz"].filter((component) => rows.some((row) => Number.isFinite(Number(row[component]))));
        drawLineChart($(`#${prefix}-displacement`, container), components.map((component) => ({name: component, points: rows.map((row) => ({x: Number(row.Step), y: Number(row[component])}))})), {xName: "Step"});
      };
      pointSelect.onchange = drawDisplacement;
      drawDisplacement();

      const modal = outputs.modal_contributions || {};
      const drawModal = () => {
        const direction = modalSelect.value;
        const rows = Array.isArray(modal[direction]) ? modal[direction] : [];
        const field = direction === "X" ? "Mx_pcent" : direction === "Y" ? "My_pcent" : "Mz_pcent";
        drawHorizontalBars($(`#${prefix}-modal-chart`, container), rows.map((row, index) => ({label: row.Fn !== undefined ? `Mode ${row.Fn}` : `Mode ${index + 1}`, value: Number(row[field])})), {height: 250});
      };
      modalSelect.onchange = drawModal;
      drawModal();
      renderMutation($(`#${prefix}-mutation`, container), analysis.interfaces);
    };
    analysisSelect.addEventListener("change", renderAnalysis);
    renderAnalysis();
  }

  function renderMutation(container, mutation) {
    const piers = mutation?.piers;
    if (!piers || typeof piers !== "object" || !Object.keys(piers).length) {
      container.innerHTML = `<div class="chart-empty">No interface mutation for this analysis</div>`;
      return;
    }
    const rows = Object.entries(piers).map(([pier, data]) => `<tr><td>${escapeHtml(pier)}</td><td>${escapeHtml((data.scoured_interface_keys || []).join(", ") || "—")}</td><td>${escapeHtml((data.reset_interface_keys || []).join(", ") || "—")}</td></tr>`).join("");
    container.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Pier</th><th>Scoured interfaces</th><th>Reset interfaces</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function renderRun(run) {
    if (!run) return `<p class="muted">No run metadata available.</p>`;
    const runner = run.runner || {};
    const executions = Array.isArray(run.executions) ? run.executions : [];
    return `<div class="metadata-grid"><div class="metadata-item"><span>Runner</span><strong>${escapeHtml(runner.version || "—")}</strong></div><div class="metadata-item"><span>Host</span><strong>${escapeHtml(runner.hostname || "—")}</strong></div><div class="metadata-item"><span>Platform</span><strong>${escapeHtml(runner.platform || "—")}</strong></div><div class="metadata-item"><span>Results DB</span><strong>${escapeHtml(formatBytes(run.results_database?.size_bytes))}</strong></div></div>
      ${executions.length ? `<div class="table-wrap" style="margin-top:16px"><table><thead><tr><th>Stage</th><th>Duration</th><th>Return code</th><th>Started</th></tr></thead><tbody>${executions.map((execution) => `<tr><td>${escapeHtml(execution.analysis || execution.command?.[0] || "Solver")}</td><td>${escapeHtml(formatDuration(execution.duration_seconds))}</td><td>${escapeHtml(execution.return_code ?? "—")}</td><td>${escapeHtml(formatDate(execution.started_at))}</td></tr>`).join("")}</tbody></table></div>` : ""}`;
  }

  function renderAttempts(attempts) {
    if (!attempts?.length) return `<p class="muted">No attempts.</p>`;
    return `<div class="table-wrap"><table><thead><tr><th>Attempt</th><th>Status</th><th>Worker</th><th>Duration</th><th>Exit code</th><th>Started</th><th>Finished</th><th>Failure</th></tr></thead><tbody>${attempts.map((attempt) => `<tr><td>${escapeHtml(attempt.id)}</td><td>${statusPill(attempt.status)}</td><td>${escapeHtml(attempt.worker_name || "—")}</td><td>${escapeHtml(formatDuration(attempt.duration_seconds))}</td><td>${escapeHtml(attempt.exit_code ?? "—")}</td><td>${escapeHtml(formatDate(attempt.started_at))}</td><td>${escapeHtml(formatDate(attempt.finished_at))}</td><td title="${escapeHtml(attempt.failure_reason || "")}">${escapeHtml((attempt.failure_reason || "—").slice(0, 80))}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function renderArtifacts(artifacts) {
    if (!artifacts?.length) return `<p class="muted">No artifacts.</p>`;
    return `<div class="table-wrap"><table><thead><tr><th>Kind</th><th>File</th><th>Attempt</th><th>Size</th><th>SHA-256</th><th></th></tr></thead><tbody>${artifacts.map((artifact) => `<tr><td>${escapeHtml(artifact.kind)}</td><td>${escapeHtml(artifact.filename)}</td><td>${escapeHtml(artifact.attempt_id || "Job")}</td><td>${escapeHtml(formatBytes(artifact.size_bytes))}</td><td title="${escapeHtml(artifact.sha256)}">${escapeHtml(artifact.sha256.slice(0, 12))}…</td><td><a href="${escapeHtml(artifact.download_url)}">Download</a></td></tr>`).join("")}</tbody></table></div>`;
  }

  function configureStatisticsControls() {
    if (!state.catalog) return;
    setOptions($("#stats-metric"), state.catalog.metrics, "id", "label");
    setOptions($("#stats-analysis"), state.catalog.analyses, null, null, {value: "", label: "All analyses"});
    setOptions($("#stats-point"), state.catalog.model_points, null, null, {value: "", label: "All model points"});
    setOptions($("#stats-scenario"), state.catalog.scenarios, null, null, {value: "", label: "All scenarios"});
    setOptions($("#stats-worker"), state.catalog.workers, "id", "name", {value: "", label: "All workers"});
    const group = $("#stats-group");
    state.catalog.metadata_paths.forEach((path) => group.insertAdjacentHTML("beforeend", `<option value="metadata:${escapeHtml(path)}">Metadata: ${escapeHtml(path)}</option>`));
    const x = $("#stats-x");
    state.catalog.metadata_paths.forEach((path) => x.insertAdjacentHTML("beforeend", `<option value="${escapeHtml(path)}">${escapeHtml(path)}</option>`));
    updateStatsMetricControls();
  }

  function updateStatsMetricControls() {
    const metric = $("#stats-metric").value;
    const family = state.catalog?.metrics.find((item) => item.id === metric)?.family;
    const component = $("#stats-component");
    const point = $("#stats-point");
    const analysis = $("#stats-analysis");
    if (family === "reaction") {
      setOptions(component, state.catalog.reaction_components.length ? state.catalog.reaction_components : ["R1", "R2", "R3"]);
      point.disabled = true;
      analysis.disabled = false;
    } else if (family === "displacement") {
      setOptions(component, state.catalog.displacement_components.length ? state.catalog.displacement_components : ["Ux", "Uy", "Uz"]);
      point.disabled = false;
      analysis.disabled = false;
    } else {
      setOptions(component, ["N/A"]);
      component.disabled = true;
      point.disabled = true;
      analysis.disabled = true;
      return;
    }
    component.disabled = false;
  }

  function statisticsParams() {
    const component = $("#stats-component");
    const point = $("#stats-point");
    return {
      metric: $("#stats-metric").value,
      analysis: $("#stats-analysis").disabled ? "" : $("#stats-analysis").value,
      component: component.disabled ? "" : component.value,
      model_point_id: point.disabled ? "" : point.value,
      group_by: $("#stats-group").value,
      scenario: $("#stats-scenario").value,
      worker_id: $("#stats-worker").value,
      created_from: $("#stats-from").value,
      created_to: $("#stats-to").value,
    };
  }

  async function runStatistics() {
    try {
      const params = statisticsParams();
      const data = await api(`${API}/statistics?${queryString(params)}`);
      state.statistics = data;
      $("#stats-export").href = `${API}/statistics.csv?${queryString(params)}`;
      renderStatistics();
    } catch (error) { toast(`Could not calculate statistics: ${error.message}`, true); }
  }

  function renderStatistics() {
    const data = state.statistics;
    if (!data) return;
    const summary = data.summary;
    $("#stats-kpis").innerHTML = [
      kpi("Observations", summary.count, "Valid numeric values"),
      kpi("Mean", formatNumber(summary.mean), "Arithmetic mean"),
      kpi("Median", formatNumber(summary.median), "50th percentile"),
      kpi("Std. deviation", formatNumber(summary.std_dev), "Sample standard deviation"),
      kpi("Minimum", formatNumber(summary.minimum), "Smallest observation"),
      kpi("Maximum", formatNumber(summary.maximum), "Largest observation"),
    ].join("");
    const observations = data.observations || [];
    drawHistogram($("#histogram-chart"), observations.map((row) => row.value));
    const xPath = $("#stats-x").value;
    const points = observations.map((row, index) => ({
      x: xPath ? row.metadata?.[xPath] : index + 1,
      y: row.value,
      label: `${row.job_id}${row.analysis ? ` · ${row.analysis}` : ""}`,
    })).filter((point) => Number.isFinite(Number(point.x)));
    $("#scatter-description").textContent = xPath ? `Selected response versus metadata field “${xPath}”.` : "Observation value by sequence.";
    drawScatter($("#scatter-chart"), points);
    $("#stats-groups-body").innerHTML = data.groups?.length ? data.groups.map((group) => `<tr><td>${escapeHtml(group.group)}</td><td>${escapeHtml(group.count)}</td><td>${escapeHtml(formatNumber(group.mean))}</td><td>${escapeHtml(formatNumber(group.median))}</td><td>${escapeHtml(formatNumber(group.std_dev))}</td><td>${escapeHtml(formatNumber(group.minimum))}</td><td>${escapeHtml(formatNumber(group.q1))}</td><td>${escapeHtml(formatNumber(group.q3))}</td><td>${escapeHtml(formatNumber(group.maximum))}</td></tr>`).join("") : `<tr><td colspan="9" class="empty-cell">No grouped observations.</td></tr>`;
    $("#stats-observation-count").textContent = `${observations.length.toLocaleString()} observations; showing up to 500.`;
    $("#stats-observations-body").innerHTML = observations.length ? observations.slice(0, 500).map((row) => `<tr><td>${escapeHtml(row.job_id)}</td><td>${escapeHtml(row.scenario_id || "—")}</td><td>${escapeHtml(row.worker_name || "—")}</td><td>${escapeHtml(row.analysis || "—")}</td><td>${escapeHtml(row.model_point_id ?? "—")}</td><td>${escapeHtml(row.component || "—")}</td><td>${escapeHtml(row.step ?? "—")}</td><td>${escapeHtml(formatNumber(row.value, 6))}</td></tr>`).join("") : `<tr><td colspan="8" class="empty-cell">No observations match the selection.</td></tr>`;
  }

  async function loadWorkers() {
    try {
      const data = await api(`${API}/workers`);
      state.workers = data.items;
      renderWorkers();
    } catch (error) { toast(`Could not load workers: ${error.message}`, true); }
  }

  function renderWorkers() {
    const workers = state.workers;
    const online = workers.filter((worker) => worker.online).length;
    const capacity = workers.filter((worker) => worker.enabled).reduce((sum, worker) => sum + worker.max_parallel_jobs, 0);
    const active = workers.reduce((sum, worker) => sum + worker.active_attempts, 0);
    const completed = workers.reduce((sum, worker) => sum + worker.completed_attempts, 0);
    $("#worker-kpis").innerHTML = [kpi("Workers", workers.length, `${online} online`), kpi("Capacity", capacity, "Configured parallel slots"), kpi("Active attempts", active, "Running or transferring"), kpi("Completed attempts", completed, "Across all workers")].join("");
    $("#workers-body").innerHTML = workers.length ? workers.map((worker) => `<tr><td class="job-cell"><span class="job-name">${escapeHtml(worker.name)}</span><span class="job-sub">${escapeHtml(worker.id)}</span></td><td><span class="state-pill ${worker.online ? "state-online" : "state-offline"}">${worker.online ? "Online" : "Offline"}</span>${worker.enabled ? "" : " <span class=\"status-pill status-cancelled\">Disabled</span>"}</td><td>${escapeHtml(worker.max_parallel_jobs)}</td><td>${escapeHtml(worker.active_attempts)}</td><td>${escapeHtml(worker.completed_attempts)}</td><td>${escapeHtml(worker.failed_attempts)}</td><td>${escapeHtml(formatPercent(worker.success_rate))}</td><td>${escapeHtml(formatDuration(worker.average_duration_seconds))}</td><td><span class="job-sub">Worker ${escapeHtml(worker.worker_version || "—")}<br>Solver ${escapeHtml(worker.solver_version || "—")}</span></td><td>${escapeHtml(formatDate(worker.last_seen_at))}</td></tr>`).join("") : `<tr><td colspan="10" class="empty-cell">No workers registered.</td></tr>`;
  }

  async function refreshCurrentView() {
    if (state.view === "overview") {
      state.summary = await api(`${API}/summary`);
      renderOverview();
    } else if (state.view === "jobs") await loadJobs();
    else if (state.view === "results") await loadCompletedJobs();
    else if (state.view === "statistics") await runStatistics();
    else if (state.view === "workers") await loadWorkers();
    $("#last-refresh").textContent = `Updated ${new Date().toLocaleTimeString(undefined, {hour: "2-digit", minute: "2-digit"})}`;
  }

  function populateCatalog() {
    const catalog = state.catalog;
    setOptions($("#filter-status"), catalog.statuses, null, null, {value: "", label: "All statuses"});
    setOptions($("#filter-scenario"), catalog.scenarios, null, null, {value: "", label: "All scenarios"});
    setOptions($("#filter-worker"), catalog.workers, "id", "name", {value: "", label: "All workers"});
    configureStatisticsControls();
  }

  function bindEvents() {
    $$(".nav-item").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
    $$('[data-go="jobs"]').forEach((button) => button.addEventListener("click", () => switchView("jobs")));
    $("#refresh-button").addEventListener("click", async () => {
      try { showLoading(true); await refreshCurrentView(); toast("Dashboard refreshed"); }
      catch (error) { toast(`Refresh failed: ${error.message}`, true); }
      finally { showLoading(false); }
    });
    $("#apply-filters").addEventListener("click", () => loadJobs(true));
    $("#clear-filters").addEventListener("click", () => {
      ["#filter-search", "#filter-status", "#filter-scenario", "#filter-worker", "#filter-from", "#filter-to"].forEach((selector) => { $(selector).value = ""; });
      loadJobs(true);
    });
    $("#filter-search").addEventListener("keydown", (event) => { if (event.key === "Enter") loadJobs(true); });
    $("#jobs-page-size").addEventListener("change", (event) => { state.jobs.limit = Number(event.target.value); loadJobs(true); });
    $("#jobs-prev").addEventListener("click", () => { state.jobs.offset = Math.max(0, state.jobs.offset - state.jobs.limit); loadJobs(); });
    $("#jobs-next").addEventListener("click", () => { state.jobs.offset += state.jobs.limit; loadJobs(); });
    $$("th[data-sort]").forEach((header) => header.addEventListener("click", () => {
      const key = header.dataset.sort;
      if (state.jobs.sort === key) state.jobs.direction = state.jobs.direction === "asc" ? "desc" : "asc";
      else { state.jobs.sort = key; state.jobs.direction = "asc"; }
      loadJobs();
    }));
    $("#result-job-search").addEventListener("input", renderResultJobList);
    $("#stats-metric").addEventListener("change", updateStatsMetricControls);
    $("#stats-run").addEventListener("click", runStatistics);
    $("#stats-x").addEventListener("change", () => { if (state.statistics) renderStatistics(); });
    $("#job-dialog").addEventListener("click", (event) => {
      if (event.target === $("#job-dialog")) $("#job-dialog").close();
    });
    let resizeTimer;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (state.view === "overview" && state.summary) renderOverview();
        if (state.view === "statistics" && state.statistics) renderStatistics();
      }, 180);
    });
  }

  async function init() {
    bindEvents();
    showLoading(true);
    try {
      const [health, catalog, summary] = await Promise.all([
        api("/health/ready"),
        api(`${API}/catalog`),
        api(`${API}/summary`),
      ]);
      state.catalog = catalog;
      state.summary = summary;
      $("#server-version").textContent = `Server ${health.version}`;
      populateCatalog();
      renderOverview();
      $("#last-refresh").textContent = `Updated ${new Date().toLocaleTimeString(undefined, {hour: "2-digit", minute: "2-digit"})}`;
    } catch (error) {
      toast(`Dashboard could not connect to the server: ${error.message}`, true);
      $("#overview-kpis").innerHTML = kpi("Connection error", "Unavailable", error.message);
    } finally {
      showLoading(false);
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
