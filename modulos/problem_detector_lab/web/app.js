const state = {
  view: "dataset",
  dataset: null,
  samples: [],
  filtered: [],
  currentIndex: -1,
  sample: null,
  image: null,
  baselineBoxes: [],
  boxes: [],
  selectedId: null,
  activeClass: 0,
  drawMode: false,
  dirty: false,
  dragging: null,
  scale: 1,
  loadSeq: 0,
  saveSeq: 0,
  loadingSampleId: null,
  datasets: [],
  audit: {
    readOnly: true,
    stage: "pre_h_ps1",
    catalog: null,
    visualCatalog: null,
    sessions: [],
    instances: [],
    detail: null,
    pages: [],
    filteredPages: [],
    relations: [],
    relationIndex: -1,
    activeRelation: null,
    pageIndex: -1,
    scale: 1,
    fitScale: 1,
    naturalWidth: 0,
    naturalHeight: 0,
    loadSeq: 0,
    sessionDecisions: {},
  },
};

const classNames = {
  0: "problem",
  1: "problem_number",
  2: "answer_block",
};

const classLabels = {
  0: "Problema",
  1: "Numero",
  2: "Alternativas",
};

const colors = {
  0: "#ef4444",
  1: "#f59e0b",
  2: "#3b82f6",
};

const AUDIT_PRECISION_VALIDATION_ENDPOINT = "/api/library-audit/precision/validate";
const AUDIT_VISUAL_SESSIONS_ENDPOINT = "/api/library-audit/sessions";
const AUDIT_VISUAL_SESSION_ENDPOINT = "/api/library-audit/session";

const INITIAL_ZOOM = 0.5;
const MIN_ZOOM = 0.1;
const MAX_ZOOM = 3;
const ZOOM_STEP = 1.15;

const canvas = document.getElementById("labelCanvas");
const ctx = canvas.getContext("2d");
const baselineCanvas = document.getElementById("baselineCanvas");
const baselineCtx = baselineCanvas.getContext("2d");
const editorShell = document.getElementById("editorShell");
const baselineShell = document.getElementById("baselineShell");

function log(message) {
  const statusLog = document.getElementById("statusLog");
  const statusPreview = document.getElementById("statusPreview");
  statusLog.textContent = `${new Date().toLocaleTimeString()}  ${message}\n${statusLog.textContent}`.slice(0, 5000);
  if (statusPreview) statusPreview.textContent = message;
}

async function apiGet(path) {
  const res = await fetch(path, { cache: "no-store" });
  const data = await res.json();
  if (!res.ok || data.ok === false) throw new Error(data.error || `GET ${path}`);
  return data;
}

async function apiPost(path, payload) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  const data = await res.json();
  if (!res.ok || data.ok === false) throw new Error(data.error || `POST ${path}`);
  return data;
}

function resetCurrentSample() {
  state.currentIndex = -1;
  state.sample = null;
  state.image = null;
  state.baselineBoxes = [];
  state.boxes = [];
  state.selectedId = null;
  state.dragging = null;
  state.dirty = false;
  state.loadingSampleId = null;
  canvas.width = 1;
  canvas.height = 1;
  baselineCanvas.width = 1;
  baselineCanvas.height = 1;
  renderBoxList();
  renderCurrentInfo();
  updateDimensionPanel();
}

function boxId() {
  return `box-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function normalizeBox(box) {
  const x1 = Math.min(box.x1, box.x2);
  const y1 = Math.min(box.y1, box.y2);
  const x2 = Math.max(box.x1, box.x2);
  const y2 = Math.max(box.y1, box.y2);
  const width = state.sample?.width || 1;
  const height = state.sample?.height || 1;
  return {
    ...box,
    x1: Math.max(0, Math.min(width, x1)),
    y1: Math.max(0, Math.min(height, y1)),
    x2: Math.max(0, Math.min(width, x2)),
    y2: Math.max(0, Math.min(height, y2)),
  };
}

function classCount(cls) {
  return state.boxes.filter((box) => Number(box.cls) === Number(cls)).length;
}

function selectedBox() {
  return state.boxes.find((box) => box.id === state.selectedId) || null;
}

function numberInput(id) {
  return document.getElementById(id);
}

function updateDimensionPanel() {
  const box = selectedBox();
  const hint = document.getElementById("dimensionHint");
  const ids = ["dimX1", "dimY1", "dimX2", "dimY2", "dimW", "dimH"];
  if (!box) {
    hint.textContent = "Selecciona un box para editar sus medidas.";
    for (const id of ids) {
      numberInput(id).value = "";
      numberInput(id).disabled = true;
    }
    document.getElementById("applyDimensionsBtn").disabled = true;
    return;
  }
  const normalized = normalizeBox(box);
  hint.textContent = `${classLabels[normalized.cls] || normalized.cls} seleccionado.`;
  for (const id of ids) numberInput(id).disabled = false;
  document.getElementById("applyDimensionsBtn").disabled = false;
  numberInput("dimX1").value = Math.round(normalized.x1);
  numberInput("dimY1").value = Math.round(normalized.y1);
  numberInput("dimX2").value = Math.round(normalized.x2);
  numberInput("dimY2").value = Math.round(normalized.y2);
  numberInput("dimW").value = Math.round(normalized.x2 - normalized.x1);
  numberInput("dimH").value = Math.round(normalized.y2 - normalized.y1);
}

function syncDimensionInputs(changedId) {
  const x1 = Number(numberInput("dimX1").value || 0);
  const y1 = Number(numberInput("dimY1").value || 0);
  const x2 = Number(numberInput("dimX2").value || 0);
  const y2 = Number(numberInput("dimY2").value || 0);
  const w = Number(numberInput("dimW").value || 0);
  const h = Number(numberInput("dimH").value || 0);
  if (changedId === "dimW") numberInput("dimX2").value = Math.round(x1 + w);
  if (changedId === "dimH") numberInput("dimY2").value = Math.round(y1 + h);
  if (["dimX1", "dimX2"].includes(changedId)) numberInput("dimW").value = Math.max(0, Math.round(x2 - x1));
  if (["dimY1", "dimY2"].includes(changedId)) numberInput("dimH").value = Math.max(0, Math.round(y2 - y1));
}

function applyDimensions() {
  const box = selectedBox();
  if (!box || !state.sample) return;
  box.x1 = Number(numberInput("dimX1").value || 0);
  box.y1 = Number(numberInput("dimY1").value || 0);
  box.x2 = Number(numberInput("dimX2").value || 0);
  box.y2 = Number(numberInput("dimY2").value || 0);
  const normalized = normalizeBox(box);
  Object.assign(box, normalized);
  state.dirty = true;
  draw();
  renderBoxList();
  updateDimensionPanel();
  log(`Dimensiones actualizadas: ${classLabels[box.cls] || box.cls}`);
}

function updateStats() {
  const dataset = state.dataset || {};
  document.getElementById("totalCount").textContent = dataset.samples_total || 0;
  document.getElementById("reviewedCount").textContent = dataset.approved_total ?? dataset.reviewed_total ?? 0;
  document.getElementById("pendingCount").textContent = dataset.pending_human_total ?? dataset.pending_total ?? 0;
  const exportBtn = document.getElementById("exportYamlBtn");
  exportBtn.disabled = dataset.supports_export === false;
  exportBtn.title = dataset.supports_export === false ? "Deshabilitado durante la auditoria comparativa" : "";
  const approveBtn = document.getElementById("approveBtn");
  approveBtn.hidden = !dataset.comparison_mode;
}

function shortDatasetName(name) {
  return String(name || "")
    .replace(/^problem_detector_multiclass_100_lab_/, "")
    .replace(/^problem_detector_multiclass_ingrid_review_/, "Ingrid ")
    .replace(/_/g, " ");
}

function renderDatasetSelector() {
  const select = document.getElementById("datasetSelect");
  const currentRoot = state.dataset?.dataset_root || "";
  select.innerHTML = "";
  for (const item of state.datasets) {
    const option = document.createElement("option");
    option.value = item.path;
    option.textContent = `${shortDatasetName(item.name)} (${item.samples_total} muestras)`;
    option.selected = item.path === currentRoot || item.current;
    select.appendChild(option);
  }
}

function buildGroupFilter() {
  const groupFilter = document.getElementById("groupFilter");
  const current = groupFilter.value;
  const groups = [...new Set(state.samples.map((sample) => sample.group))].sort();
  groupFilter.innerHTML = '<option value="">Todas las fuentes</option>';
  for (const group of groups) {
    const option = document.createElement("option");
    option.value = group;
    option.textContent = group;
    groupFilter.appendChild(option);
  }
  groupFilter.value = groups.includes(current) ? current : "";
}

function applyFilters() {
  const query = document.getElementById("searchInput").value.trim().toLowerCase();
  const group = document.getElementById("groupFilter").value;
  const review = document.getElementById("reviewFilter").value;
  state.filtered = state.samples.filter((sample) => {
    const haystack = `${sample.sample_id} ${sample.instance} ${sample.page_number}`.toLowerCase();
    if (query && !haystack.includes(query)) return false;
    if (group && sample.group !== group) return false;
    if (review === "pending" && sample.reviewed) return false;
    if (review === "reviewed" && !sample.reviewed) return false;
    if (review === "changed" && !sample.has_changes) return false;
    if (review === "unchanged" && sample.has_changes) return false;
    return true;
  }).sort((a, b) => Number(a.order || 0) - Number(b.order || 0));
  state.currentIndex = state.sample
    ? state.filtered.findIndex((sample) => sample.sample_key === state.sample.sample_key)
    : -1;
  renderSampleList();
  updateNavigationButtons();
}

function renderSampleList() {
  const list = document.getElementById("sampleList");
  list.innerHTML = "";
  for (const [index, sample] of state.filtered.entries()) {
    const button = document.createElement("button");
    const active = state.sample?.sample_key === sample.sample_key;
    const badgeClass = sample.reviewed ? "reviewed" : sample.has_changes ? "changed" : "pending";
    const badgeText = sample.reviewed
      ? "Aprobada"
      : sample.has_changes
        ? `Cambio de Ingrid (${sample.delta_box_count >= 0 ? "+" : ""}${sample.delta_box_count})`
        : "Sin cambios · pendiente";
    button.className = `sample-card ${active ? "active" : ""}`;
    button.innerHTML = `
      <span class="title">${index + 1}. ${sample.group} | pag. ${sample.page_number}</span>
      <span class="line">${sample.split ? `${sample.split} · ` : ""}${sample.instance || sample.sample_id}</span>
      <span class="badge ${badgeClass}">${badgeText}</span>
    `;
    button.addEventListener("click", () => loadSampleByFilteredIndex(index));
    list.appendChild(button);
  }
}

function renderBoxList() {
  const list = document.getElementById("boxList");
  const countLabel = document.getElementById("boxCountLabel");
  list.innerHTML = "";
  if (countLabel) countLabel.textContent = String(state.boxes.length);
  if (!state.boxes.length) {
    list.innerHTML = '<p class="empty-boxes">No hay boxes en esta pagina.</p>';
    return;
  }
  for (const [index, box] of state.boxes.entries()) {
    const button = document.createElement("button");
    button.className = `box-card ${state.selectedId === box.id ? "active" : ""}`;
    button.innerHTML = `
      <span class="title">${index + 1}. ${classLabels[box.cls] || box.cls}</span>
      <span class="line">${Math.round(box.x1)},${Math.round(box.y1)} -> ${Math.round(box.x2)},${Math.round(box.y2)}</span>
    `;
    button.style.borderLeft = `5px solid ${colors[box.cls] || "#999"}`;
    button.addEventListener("click", () => {
      state.selectedId = box.id;
      setDrawMode(false);
      draw();
      renderBoxList();
      updateDimensionPanel();
    });
    list.appendChild(button);
  }
}

function boxSignature(box) {
  const normalized = normalizeBox(box);
  return [
    Number(normalized.cls),
    Number(normalized.x1).toFixed(2),
    Number(normalized.y1).toFixed(2),
    Number(normalized.x2).toFixed(2),
    Number(normalized.y2).toFixed(2),
  ].join("|");
}

function liveComparison() {
  const before = new Map();
  const after = new Map();
  for (const box of state.baselineBoxes) {
    const key = boxSignature(box);
    before.set(key, (before.get(key) || 0) + 1);
  }
  for (const box of state.boxes) {
    const key = boxSignature(box);
    after.set(key, (after.get(key) || 0) + 1);
  }
  let added = 0;
  let removed = 0;
  const addedByClass = { 0: 0, 1: 0, 2: 0 };
  const removedByClass = { 0: 0, 1: 0, 2: 0 };
  for (const [key, count] of after) {
    const delta = Math.max(0, count - (before.get(key) || 0));
    if (delta) {
      added += delta;
      addedByClass[Number(key.split("|")[0])] += delta;
    }
  }
  for (const [key, count] of before) {
    const delta = Math.max(0, count - (after.get(key) || 0));
    if (delta) {
      removed += delta;
      removedByClass[Number(key.split("|")[0])] += delta;
    }
  }
  return {
    hasChanges: Boolean(added || removed),
    added,
    removed,
    addedByClass,
    removedByClass,
    delta: state.boxes.length - state.baselineBoxes.length,
  };
}

function renderComparisonInfo() {
  const summary = document.getElementById("comparisonSummary");
  const badge = document.getElementById("comparisonBadge");
  const baselineCount = document.getElementById("baselineBoxCount");
  const approveBtn = document.getElementById("approveBtn");
  if (!state.sample) {
    summary.textContent = "Carga una muestra para comparar.";
    baselineCount.textContent = "0 boxes";
    badge.textContent = "Sin muestra";
    badge.className = "comparison-badge unchanged";
    approveBtn.disabled = true;
    return;
  }
  const comparison = liveComparison();
  const approved = !state.dirty && (state.sample.review?.human_review === "approved" || state.sample.review?.status === "human_approved");
  baselineCount.textContent = `${state.baselineBoxes.length} boxes`;
  badge.textContent = approved ? "Aprobada" : comparison.hasChanges ? "Modificada" : "Sin cambios";
  badge.className = `comparison-badge ${approved ? "approved" : comparison.hasChanges ? "changed" : "unchanged"}`;
  approveBtn.disabled = approved || !state.sample.comparison?.has_baseline;
  approveBtn.textContent = approved ? "Comparacion aprobada" : "Aprobar comparacion";
  summary.innerHTML = `
    <div class="comparison-metrics">
      <div class="comparison-metric"><span>Anterior</span><strong>${state.baselineBoxes.length}</strong></div>
      <div class="comparison-metric"><span>Ingrid</span><strong>${state.boxes.length}</strong></div>
      <div class="comparison-metric"><span>Delta</span><strong>${comparison.delta >= 0 ? "+" : ""}${comparison.delta}</strong><small>+${comparison.added} / -${comparison.removed}</small></div>
    </div>
    ${state.dirty ? '<div class="unsaved-banner">Cambios humanos sin guardar. Se guardaran antes de aprobar.</div>' : ""}
    <div class="class-deltas">
      <div class="class-delta"><b>Problema</b><span>+${comparison.addedByClass[0]} / -${comparison.removedByClass[0]}</span></div>
      <div class="class-delta"><b>Numero</b><span>+${comparison.addedByClass[1]} / -${comparison.removedByClass[1]}</span></div>
      <div class="class-delta"><b>Alternativas</b><span>+${comparison.addedByClass[2]} / -${comparison.removedByClass[2]}</span></div>
    </div>
  `;
}

function updateNavigationButtons() {
  const prevBtn = document.getElementById("prevBtn");
  const nextBtn = document.getElementById("nextBtn");
  const hasCurrent = state.currentIndex >= 0 && state.currentIndex < state.filtered.length;
  prevBtn.disabled = !hasCurrent || state.currentIndex === 0;
  nextBtn.disabled = !hasCurrent || state.currentIndex === state.filtered.length - 1;
  prevBtn.textContent = "← Anterior";
  nextBtn.textContent = "Siguiente →";
}

function renderCurrentInfo() {
  const sample = state.sample;
  document.getElementById("sampleTitle").textContent = sample ? `${sample.split ? `${sample.split}/` : ""}${sample.sample_id}` : "Sin muestra";
  document.getElementById("sampleMeta").textContent = sample
    ? `${state.currentIndex + 1} de ${state.filtered.length} | ${sample.width} x ${sample.height} | problemas ${classCount(0)} | numeros ${classCount(1)} | alternativas ${classCount(2)}`
    : "";
  document.getElementById("zoomLabel").textContent = `${Math.round(state.scale * 100)}%`;
  renderComparisonInfo();
  updateNavigationButtons();
}

function fitToView() {
  if (!state.sample) return;
  const availableW = Math.max(260, Math.min(editorShell.clientWidth, baselineShell.clientWidth) - 40);
  const availableH = Math.max(280, Math.min(editorShell.clientHeight, baselineShell.clientHeight) - 40);
  state.scale = Math.min(1.15, Math.max(0.15, Math.min(availableW / state.sample.width, availableH / state.sample.height)));
  draw();
  editorShell.scrollTo(0, 0);
  baselineShell.scrollTo(0, 0);
}

function setInitialZoom() {
  state.scale = INITIAL_ZOOM;
  fitToView();
}

function clampZoom(value) {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, value));
}

function setZoom(nextScale) {
  if (!state.sample || !state.image) return;
  const previousScale = state.scale;
  const targetScale = clampZoom(nextScale);
  if (Math.abs(targetScale - previousScale) < 0.0001) return;
  const editorCenter = {
    x: (editorShell.scrollLeft + editorShell.clientWidth / 2) / previousScale,
    y: (editorShell.scrollTop + editorShell.clientHeight / 2) / previousScale,
  };
  state.scale = targetScale;
  draw();
  editorShell.scrollLeft = Math.max(0, editorCenter.x * state.scale - editorShell.clientWidth / 2);
  editorShell.scrollTop = Math.max(0, editorCenter.y * state.scale - editorShell.clientHeight / 2);
  baselineShell.scrollLeft = editorShell.scrollLeft;
  baselineShell.scrollTop = editorShell.scrollTop;
}

function onCanvasWheel(event) {
  if (!event.ctrlKey || !state.sample || !state.image) return;
  event.preventDefault();
  const factor = event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
  setZoom(state.scale * factor);
}

function drawCanvas(targetCanvas, targetCtx, boxes, editable) {
  if (!state.sample || !state.image) return;
  targetCanvas.width = Math.max(1, Math.round(state.sample.width * state.scale));
  targetCanvas.height = Math.max(1, Math.round(state.sample.height * state.scale));
  targetCtx.clearRect(0, 0, targetCanvas.width, targetCanvas.height);
  targetCtx.drawImage(state.image, 0, 0, targetCanvas.width, targetCanvas.height);
  targetCtx.lineWidth = Math.max(2, 3 * state.scale);
  targetCtx.font = `${Math.max(11, 13 * state.scale)}px Segoe UI`;
  for (const [index, rawBox] of boxes.entries()) {
    const box = normalizeBox(rawBox);
    const color = colors[box.cls] || "#999";
    const x = box.x1 * state.scale;
    const y = box.y1 * state.scale;
    const w = (box.x2 - box.x1) * state.scale;
    const h = (box.y2 - box.y1) * state.scale;
    targetCtx.strokeStyle = color;
    targetCtx.fillStyle = `${color}22`;
    targetCtx.fillRect(x, y, w, h);
    targetCtx.strokeRect(x, y, w, h);
    targetCtx.fillStyle = color;
    targetCtx.fillRect(x, Math.max(0, y - 18), Math.min(180, 74 + String(index + 1).length * 8), 18);
    targetCtx.fillStyle = "#fff";
    targetCtx.fillText(`${index + 1} ${classLabels[box.cls] || box.cls}`, x + 5, Math.max(13, y - 5));
    if (editable && box.id === state.selectedId) drawHandles(targetCtx, box);
  }
}

function draw() {
  if (!state.sample || !state.image) return;
  drawCanvas(baselineCanvas, baselineCtx, state.baselineBoxes, false);
  drawCanvas(canvas, ctx, state.boxes, true);
  renderCurrentInfo();
  updateDimensionPanel();
}

function drawHandles(targetCtx, box) {
  const points = [
    [box.x1, box.y1],
    [box.x2, box.y1],
    [box.x2, box.y2],
    [box.x1, box.y2],
  ];
  targetCtx.fillStyle = "#fff";
  targetCtx.strokeStyle = "#00111f";
  for (const [x, y] of points) {
    const sx = x * state.scale;
    const sy = y * state.scale;
    targetCtx.fillRect(sx - 5, sy - 5, 10, 10);
    targetCtx.strokeRect(sx - 5, sy - 5, 10, 10);
  }
}

function canvasPoint(event) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: (event.clientX - rect.left) / state.scale,
    y: (event.clientY - rect.top) / state.scale,
  };
}

function hitTest(point) {
  const tolerance = 10 / state.scale;
  for (let i = state.boxes.length - 1; i >= 0; i -= 1) {
    const box = normalizeBox(state.boxes[i]);
    const corners = [
      ["nw", box.x1, box.y1],
      ["ne", box.x2, box.y1],
      ["se", box.x2, box.y2],
      ["sw", box.x1, box.y2],
    ];
    for (const [handle, x, y] of corners) {
      if (Math.abs(point.x - x) <= tolerance && Math.abs(point.y - y) <= tolerance) {
        return { box, action: "resize", handle };
      }
    }
    if (point.x >= box.x1 && point.x <= box.x2 && point.y >= box.y1 && point.y <= box.y2) {
      return { box, action: "move" };
    }
  }
  return null;
}

function onMouseDown(event) {
  if (!state.sample) return;
  if (state.loadingSampleId) {
    log(`Espera a que termine de cargar: ${state.loadingSampleId}`);
    return;
  }
  const point = canvasPoint(event);
  if (state.drawMode) {
    const box = {
      id: boxId(),
      cls: state.activeClass,
      x1: point.x,
      y1: point.y,
      x2: point.x,
      y2: point.y,
    };
    state.boxes.push(box);
    state.selectedId = box.id;
    state.dragging = { action: "draw", start: point, box };
    draw();
    renderBoxList();
    updateDimensionPanel();
    return;
  }
  const hit = hitTest(point);
  if (!hit) {
    state.selectedId = null;
    draw();
    renderBoxList();
    updateDimensionPanel();
    return;
  }
  state.selectedId = hit.box.id;
  state.dragging = {
    action: hit.action,
    handle: hit.handle,
    start: point,
    box: hit.box,
    original: { ...hit.box },
  };
  draw();
  renderBoxList();
  updateDimensionPanel();
}

function onMouseMove(event) {
  if (!state.dragging) return;
  const point = canvasPoint(event);
  const drag = state.dragging;
  const box = state.boxes.find((item) => item.id === drag.box.id);
  if (!box) return;
  if (drag.action === "draw") {
    box.x2 = point.x;
    box.y2 = point.y;
  }
  if (drag.action === "move") {
    const dx = point.x - drag.start.x;
    const dy = point.y - drag.start.y;
    const original = drag.original;
    const w = original.x2 - original.x1;
    const h = original.y2 - original.y1;
    box.x1 = Math.max(0, Math.min(state.sample.width - w, original.x1 + dx));
    box.y1 = Math.max(0, Math.min(state.sample.height - h, original.y1 + dy));
    box.x2 = box.x1 + w;
    box.y2 = box.y1 + h;
  }
  if (drag.action === "resize") {
    const handle = drag.handle;
    if (handle.includes("n")) box.y1 = point.y;
    if (handle.includes("s")) box.y2 = point.y;
    if (handle.includes("w")) box.x1 = point.x;
    if (handle.includes("e")) box.x2 = point.x;
  }
  draw();
  updateDimensionPanel();
}

function onMouseUp() {
  if (!state.dragging) return;
  state.dirty = true;
  state.boxes = state.boxes
    .map(normalizeBox)
    .filter((box) => box.x2 - box.x1 >= 4 && box.y2 - box.y1 >= 4);
  state.dragging = null;
  draw();
  renderBoxList();
  updateDimensionPanel();
}

async function loadDataset() {
  state.dataset = await apiGet("/api/dataset");
  state.samples = state.dataset.samples || [];
  document.getElementById("datasetPath").textContent = state.dataset.dataset_root || "";
  updateStats();
  renderDatasetSelector();
  buildGroupFilter();
  applyFilters();
  if (state.filtered.length && !state.sample) {
    const changedIndex = state.filtered.findIndex((sample) => sample.has_changes);
    await loadSampleByFilteredIndex(changedIndex >= 0 ? changedIndex : 0);
  }
}

async function loadDatasets() {
  const payload = await apiGet("/api/datasets");
  state.datasets = payload.datasets || [];
  renderDatasetSelector();
}

async function switchDataset() {
  const select = document.getElementById("datasetSelect");
  const datasetRoot = select.value;
  if (!datasetRoot) {
    log("No hay dataset seleccionado.");
    return;
  }
  const switchBtn = document.getElementById("switchDatasetBtn");
  switchBtn.disabled = true;
  try {
    log(`Cargando dataset: ${datasetRoot}`);
    resetCurrentSample();
    state.dataset = await apiPost("/api/dataset/select", { dataset_root: datasetRoot });
    state.samples = state.dataset.samples || [];
    document.getElementById("datasetPath").textContent = state.dataset.dataset_root || "";
    await loadDatasets();
    updateStats();
    buildGroupFilter();
    document.getElementById("reviewFilter").value = "";
    applyFilters();
    if (state.filtered.length) {
      const changedIndex = state.filtered.findIndex((sample) => sample.has_changes);
      await loadSampleByFilteredIndex(changedIndex >= 0 ? changedIndex : 0);
    }
    log(`Dataset activo: ${datasetRoot}`);
  } finally {
    switchBtn.disabled = false;
  }
}

async function loadSampleByFilteredIndex(index) {
  const sample = state.filtered[index];
  if (!sample) return;
  const loadSeq = state.loadSeq + 1;
  state.loadSeq = loadSeq;
  state.dragging = null;
  state.loadingSampleId = sample.sample_key || sample.sample_id;
  log(`Cargando muestra: ${sample.split ? `${sample.split}/` : ""}${sample.sample_id}`);
  let data;
  try {
    const splitQuery = sample.split ? `&split=${encodeURIComponent(sample.split)}` : "";
    data = await apiGet(`/api/sample?id=${encodeURIComponent(sample.sample_id)}${splitQuery}`);
  } catch (err) {
    if (loadSeq === state.loadSeq) {
      state.loadingSampleId = null;
      log(`Error cargando muestra: ${err.message}`);
    }
    return;
  }
  if (loadSeq !== state.loadSeq) return;
  const img = new Image();
  img.onload = () => {
    if (loadSeq !== state.loadSeq) return;
    state.currentIndex = index;
    state.sample = data;
    state.image = img;
    state.baselineBoxes = (data.baseline_boxes || data.boxes || []).map((box) => ({ ...box, id: `baseline-${box.id || boxId()}` }));
    state.boxes = (data.boxes || []).map((box) => ({ ...box, id: box.id || boxId() }));
    state.dirty = false;
    state.selectedId = null;
    state.dragging = null;
    state.loadingSampleId = null;
    setInitialZoom();
    renderSampleList();
    renderBoxList();
    updateDimensionPanel();
    const comparison = data.comparison || {};
    log(`Muestra cargada: ${sample.sample_id} | anterior ${comparison.baseline_box_count ?? state.baselineBoxes.length} | Ingrid ${comparison.current_box_count ?? state.boxes.length}`);
  };
  img.onerror = () => {
    if (loadSeq === state.loadSeq) {
      state.loadingSampleId = null;
      log(`Error cargando imagen: ${sample.sample_id}`);
    }
  };
  img.src = data.image_url;
}

async function saveCurrent() {
  if (!state.sample) return;
  if (state.loadingSampleId) {
    log(`No se guarda mientras carga: ${state.loadingSampleId}`);
    return;
  }
  const saveSeq = state.saveSeq + 1;
  state.saveSeq = saveSeq;
  const activeSampleId = state.sample.sample_id;
  const activeSplit = state.sample.split || "";
  const activeSampleKey = state.sample.sample_key || activeSampleId;
  const payload = {
    sample_id: activeSampleId,
    split: activeSplit,
    boxes: state.boxes.map(normalizeBox).map((box) => ({
      cls: Number(box.cls),
      x1: box.x1,
      y1: box.y1,
      x2: box.x2,
      y2: box.y2,
    })),
  };
  const saveBtn = document.getElementById("saveBtn");
  saveBtn.disabled = true;
  try {
    log(`Guardando labels de: ${activeSampleId}`);
    const data = await apiPost("/api/save", payload);
    await loadDataset();
    const newIndex = state.filtered.findIndex((sample) => sample.sample_key === data.sample_key);
    const stillViewingSameSample = state.sample?.sample_key === activeSampleKey;
    if (stillViewingSameSample && saveSeq === state.saveSeq) {
      state.sample = data;
      state.baselineBoxes = (data.baseline_boxes || []).map((box) => ({ ...box, id: `baseline-${box.id || boxId()}` }));
      state.boxes = (data.boxes || []).map((box) => ({ ...box, id: box.id || boxId() }));
      state.dirty = false;
      if (newIndex >= 0) state.currentIndex = newIndex;
      draw();
      renderBoxList();
      updateDimensionPanel();
    } else {
      renderSampleList();
    }
    log(`Guardado correcto en su muestra: ${data.sample_id}`);
  } finally {
    saveBtn.disabled = false;
  }
}

async function approveCurrent() {
  if (!state.sample || !state.sample.comparison?.has_baseline) return;
  const approveBtn = document.getElementById("approveBtn");
  approveBtn.disabled = true;
  try {
    if (state.dirty) {
      log("Guardando ajustes humanos antes de aprobar...");
      await saveCurrent();
    }
    const data = await apiPost("/api/review/approve", {
      sample_id: state.sample.sample_id,
      split: state.sample.split || "",
    });
    state.sample = data;
    state.baselineBoxes = (data.baseline_boxes || []).map((box) => ({ ...box, id: `baseline-${box.id || boxId()}` }));
    state.boxes = (data.boxes || []).map((box) => ({ ...box, id: box.id || boxId() }));
    state.dirty = false;
    await loadDataset();
    draw();
    renderBoxList();
    log(`Comparacion aprobada: ${data.split ? `${data.split}/` : ""}${data.sample_id}`);
    if (data.approval_gate?.status === "ready_for_database") {
      log(`Revision completa: ${data.approval_gate.approved_total}/${data.approval_gate.samples_total}. Dataset listo para la siguiente etapa de base de datos.`);
    } else if (data.approval_gate) {
      log(`Progreso humano: ${data.approval_gate.approved_total}/${data.approval_gate.samples_total} aprobadas.`);
    }
  } finally {
    renderComparisonInfo();
  }
}

function deleteSelected() {
  if (!state.selectedId) return;
  state.boxes = state.boxes.filter((box) => box.id !== state.selectedId);
  state.dirty = true;
  state.selectedId = null;
  draw();
  renderBoxList();
  updateDimensionPanel();
}

function setActiveClass(cls) {
  state.activeClass = Number(cls);
  for (const button of document.querySelectorAll("#classSelector button")) {
    button.classList.toggle("active", Number(button.dataset.class) === state.activeClass);
  }
  const selected = state.boxes.find((box) => box.id === state.selectedId);
  if (selected) {
    selected.cls = state.activeClass;
    state.dirty = true;
    draw();
    renderBoxList();
    updateDimensionPanel();
  }
}

function setDrawMode(enabled) {
  state.drawMode = Boolean(enabled);
  const drawBtn = document.getElementById("drawBtn");
  const selectBtn = document.getElementById("selectBtn");
  drawBtn.classList.toggle("primary", state.drawMode);
  drawBtn.classList.toggle("secondary", !state.drawMode);
  drawBtn.textContent = state.drawMode ? "Dibujando... (D)" : "Dibujar box";
  selectBtn.classList.toggle("primary", !state.drawMode);
  selectBtn.classList.toggle("secondary", state.drawMode);
}

function isEditableTarget(target) {
  if (!target) return false;
  const tag = String(target.tagName || "").toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || Boolean(target.isContentEditable);
}

async function nextSample(delta) {
  if (!state.filtered.length) return;
  if (state.dirty) {
    log("Guardando ajustes antes de cambiar de pagina...");
    await saveCurrent();
  }
  if (state.currentIndex < 0) {
    await loadSampleByFilteredIndex(0);
    return;
  }
  const next = Math.max(0, Math.min(state.filtered.length - 1, state.currentIndex + delta));
  if (next === state.currentIndex) return;
  await loadSampleByFilteredIndex(next);
}

let syncingScroll = false;

function mirrorScroll(source, target) {
  if (syncingScroll) return;
  syncingScroll = true;
  target.scrollLeft = source.scrollLeft;
  target.scrollTop = source.scrollTop;
  requestAnimationFrame(() => {
    syncingScroll = false;
  });
}

function bindEvents() {
  canvas.addEventListener("mousedown", onMouseDown);
  editorShell.addEventListener("wheel", onCanvasWheel, { passive: false });
  baselineShell.addEventListener("wheel", onCanvasWheel, { passive: false });
  editorShell.addEventListener("scroll", () => mirrorScroll(editorShell, baselineShell));
  baselineShell.addEventListener("scroll", () => mirrorScroll(baselineShell, editorShell));
  window.addEventListener("mousemove", onMouseMove);
  window.addEventListener("mouseup", onMouseUp);
  document.getElementById("saveBtn").addEventListener("click", () => saveCurrent().catch((err) => log(`Error guardando: ${err.message}`)));
  document.getElementById("approveBtn").addEventListener("click", () => approveCurrent().catch((err) => log(`Error aprobando: ${err.message}`)));
  document.getElementById("switchDatasetBtn").addEventListener("click", () => switchDataset().catch((err) => log(`Error cambiando dataset: ${err.message}`)));
  document.getElementById("prevBtn").addEventListener("click", () => nextSample(-1).catch((err) => log(`Error navegando: ${err.message}`)));
  document.getElementById("nextBtn").addEventListener("click", () => nextSample(1).catch((err) => log(`Error navegando: ${err.message}`)));
  document.getElementById("deleteBtn").addEventListener("click", deleteSelected);
  document.getElementById("fitBtn").addEventListener("click", fitToView);
  document.getElementById("selectBtn").addEventListener("click", () => setDrawMode(false));
  document.getElementById("zoomInBtn").addEventListener("click", () => {
    setZoom(state.scale * ZOOM_STEP);
  });
  document.getElementById("zoomOutBtn").addEventListener("click", () => {
    setZoom(state.scale / ZOOM_STEP);
  });
  document.getElementById("drawBtn").addEventListener("click", () => setDrawMode(!state.drawMode));
  document.getElementById("applyDimensionsBtn").addEventListener("click", applyDimensions);
  for (const id of ["dimX1", "dimY1", "dimX2", "dimY2", "dimW", "dimH"]) {
    document.getElementById(id).addEventListener("input", () => syncDimensionInputs(id));
    document.getElementById(id).addEventListener("keydown", (event) => {
      if (event.key === "Enter") applyDimensions();
    });
  }
  document.getElementById("exportYamlBtn").addEventListener("click", async () => {
    try {
      const data = await apiPost("/api/export-yaml", { val_ratio: 0.2, seed: 20260624 });
      log(`dataset.yaml creado: ${data.dataset_yaml} | train ${data.train_count} | val ${data.val_count}`);
    } catch (err) {
      log(`Error exportando YAML: ${err.message}`);
    }
  });
  for (const button of document.querySelectorAll("#classSelector button")) {
    button.addEventListener("click", () => setActiveClass(button.dataset.class));
  }
  for (const id of ["searchInput", "groupFilter", "reviewFilter"]) {
    document.getElementById(id).addEventListener("input", applyFilters);
    document.getElementById(id).addEventListener("change", applyFilters);
  }
  window.addEventListener("keydown", (event) => {
    if (state.view !== "dataset") return;
    if (event.ctrlKey && event.key.toLowerCase() === "s") {
      event.preventDefault();
      saveCurrent().catch((err) => log(`Error guardando: ${err.message}`));
      return;
    }
    if (isEditableTarget(event.target)) return;
    if (!event.ctrlKey && !event.metaKey && !event.altKey && event.key.toLowerCase() === "d") {
      event.preventDefault();
      setDrawMode(!state.drawMode);
      log(state.drawMode ? "Modo dibujar activado con tecla D." : "Modo seleccionar activado con tecla D.");
      return;
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      nextSample(-1).catch((err) => log(`Error navegando: ${err.message}`));
      return;
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      nextSample(1).catch((err) => log(`Error navegando: ${err.message}`));
      return;
    }
    if (event.key === "Delete") deleteSelected();
  });
}

const AUDIT_ROLE_LABELS = {
  theory: "Teoría",
  problem: "Problemas",
  solution: "Soluciones",
};

const CONTENT_ROLE_LABELS = {
  theory: "theory",
  definition_property_theorem: "definición / propiedad",
  worked_example: "worked_example",
  proposed_problem: "proposed_problem",
  solved_problem: "solved_problem",
  answer_key: "answer_key",
  solution: "solution",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setActiveView(view) {
  state.view = view === "library-audit" ? "library-audit" : "dataset";
  const auditActive = state.view === "library-audit";
  document.getElementById("datasetView").hidden = auditActive;
  document.getElementById("libraryAuditView").hidden = !auditActive;
  document.getElementById("datasetTopActions").hidden = auditActive;
  document.getElementById("auditTopMeta").hidden = !auditActive;
  document.getElementById("auditWorkflowRail").hidden = !auditActive;
  document.getElementById("datasetPath").hidden = auditActive;
  document.getElementById("datasetViewTab").classList.toggle("active", !auditActive);
  document.getElementById("libraryAuditTab").classList.toggle("active", auditActive);
  if (auditActive && !state.audit.catalog) {
    loadLibraryAuditCatalog().catch((error) => renderAuditFailure(error));
  }
}

function renderAuditFailure(error) {
  const empty = document.getElementById("auditEmptyState");
  empty.hidden = false;
  empty.innerHTML = `<strong>No se pudo cargar la auditoría</strong><span>${escapeHtml(error.message)}</span>`;
  document.getElementById("auditPageImage").hidden = true;
  document.getElementById("auditOverlayLayer").replaceChildren();
  document.getElementById("auditDecisionNote").textContent = `Lectura detenida: ${error.message}`;
  log(`Auditoría Biblioteca: ${error.message}`);
}

async function loadLibraryAuditCatalog() {
  state.audit.stage = document.getElementById("auditStageSelect").value || "pre_h_ps1";
  configureAuditStage();
  if (state.audit.stage === "pre_h_ps1") {
    await loadLibraryVisualAuditCatalog();
    return;
  }
  const catalog = await apiGet("/api/library-audit");
  state.audit.catalog = catalog;
  state.audit.readOnly = catalog.read_only !== false;
  state.audit.instances = catalog.instances || [];
  document.getElementById("auditInstanceCount").textContent = catalog.summary?.assignment_count ?? state.audit.instances.length;
  populateAuditBookSelect();
  const preferred = state.audit.instances.find((item) => item.book_id === 190 && item.ingrid_status !== "not_started")
    || state.audit.instances.find((item) => item.ingrid_status !== "not_started")
    || state.audit.instances[0];
  if (!preferred) throw new Error("La campaña no contiene instancias exactas para auditar.");
  document.getElementById("auditBookSelect").value = preferred.book_code;
  populateAuditInstanceSelect(preferred.assignment_id);
  await loadAuditInstance(preferred.assignment_id);
}

function configureAuditStage() {
  const preHps1 = state.audit.stage === "pre_h_ps1";
  document.querySelector(".audit-center")?.classList.toggle("pre-hps1", preHps1);
  document.getElementById("auditSessionField").hidden = !preHps1;
  document.getElementById("auditBookField").hidden = preHps1;
  document.getElementById("auditInstanceField").hidden = preHps1;
  document.getElementById("auditRelationSection").hidden = !preHps1;
  document.getElementById("auditRelationCompare").hidden = !preHps1;
  const sessionTab = document.querySelector('#auditInspectorTabs [data-audit-panel="session"]');
  const sessionPanel = document.querySelector('[data-audit-panel-content="session"]');
  if (sessionTab) sessionTab.hidden = !preHps1;
  if (sessionPanel) sessionPanel.hidden = !preHps1;
  document.getElementById("auditDecisionTitle").textContent = preHps1 ? "Revisión visual pre-H-PS1" : "Decisión H-PS2";
  document.getElementById("auditApprovePageBtn").textContent = preHps1 ? "Marcar relación conforme" : "Aprobar boxes de página";
  document.getElementById("auditCorrectBoxBtn").textContent = preHps1 ? "Marcar relación incorrecta" : "Corregir box";
  document.getElementById("auditAbstainBtn").textContent = preHps1 ? "Relación dudosa" : "Mantener abstención";
  const workflowHps1 = document.getElementById("auditWorkflowHps1Step");
  workflowHps1.textContent = preHps1 ? "H-PS1 pendiente" : "H-PS1 aprobado";
  workflowHps1.className = `workflow-step ${preHps1 ? "pending" : "approved"}`;
  if (!preHps1 && document.querySelector('#auditInspectorTabs [data-audit-panel="session"].active')) {
    selectAuditInspectorPanel("summary");
  }
}

async function loadLibraryVisualAuditCatalog() {
  const catalog = await apiGet(AUDIT_VISUAL_SESSIONS_ENDPOINT);
  state.audit.catalog = catalog;
  state.audit.visualCatalog = catalog;
  state.audit.readOnly = catalog.read_only !== false;
  state.audit.sessions = catalog.sessions || [];
  state.audit.instances = [];
  document.getElementById("auditInstanceCount").textContent = catalog.summary?.session_count ?? state.audit.sessions.length;
  const select = document.getElementById("auditSessionSelect");
  select.innerHTML = state.audit.sessions.map((session) => {
    const scope = session.scope || {};
    const status = session.status === "ready_for_visual_audit" ? "lista" : "BLOQUEADA";
    return `<option value="${escapeHtml(session.session_id)}">${escapeHtml(scope.book_code || "libro")} · ${escapeHtml(scope.instance_type || "instancia")} · r${escapeHtml(session.map_revision)} · ${status}</option>`;
  }).join("");
  const preferred = state.audit.sessions.find((session) => session.status === "ready_for_visual_audit") || state.audit.sessions[0];
  if (!preferred) {
    throw new Error("No hay sesiones visuales pre-H-PS1 materializadas por Gottfried.");
  }
  select.value = preferred.session_id;
  await loadVisualAuditSession(preferred.session_id);
}

async function loadVisualAuditSession(sessionId) {
  if (!sessionId) return;
  const loadSeq = state.audit.loadSeq + 1;
  state.audit.loadSeq = loadSeq;
  const empty = document.getElementById("auditEmptyState");
  empty.hidden = false;
  empty.innerHTML = "<strong>Cargando sesión visual…</strong><span>Revalidando mapa, revisión, huellas y referencias P/S/R.</span>";
  document.getElementById("auditPageImage").hidden = true;
  document.getElementById("auditOverlayLayer").replaceChildren();
  const detail = await apiGet(`${AUDIT_VISUAL_SESSION_ENDPOINT}?id=${encodeURIComponent(sessionId)}`);
  if (loadSeq !== state.audit.loadSeq) return;
  state.audit.detail = detail;
  state.audit.pages = detail.pages || [];
  state.audit.filteredPages = [];
  state.audit.relations = detail.relations || [];
  state.audit.relationIndex = -1;
  state.audit.activeRelation = null;
  state.audit.pageIndex = -1;
  state.audit.sessionDecisions = {};
  renderVisualAuditSessionHeader();
  renderVisualSessionHashes();
  renderAuditComposition();
  renderAuditEligibilityChips();
  renderAuditRelationList();
  if (detail.session?.status === "visual_audit_blocked") {
    empty.hidden = false;
    empty.innerHTML = `<strong>visual_audit_blocked</strong><span>${escapeHtml((detail.integrity?.blockers || []).join(" · ") || "La sesión no coincide con sus artefactos vivos.")}</span>`;
    document.getElementById("auditPageList").replaceChildren();
    document.getElementById("auditPageCount").textContent = "0/0";
    document.getElementById("auditDecisionNote").textContent = "Auditoría detenida: no se puede solicitar H-PS1 mientras la integridad visual esté bloqueada.";
    for (const button of document.querySelectorAll(".audit-decision-actions button")) button.disabled = true;
    return;
  }
  applyAuditPageFilter();
  if (state.audit.relations.length) {
    await selectAuditRelation(0, { focusPage: true });
  } else if (state.audit.filteredPages.length) {
    await selectAuditPageByIndex(0);
  }
}

function renderVisualAuditSessionHeader() {
  const detail = state.audit.detail || {};
  const scope = detail.scope || {};
  const session = detail.session || {};
  document.getElementById("auditHeaderTitle").textContent = scope.book_code || "Mapa V2";
  document.getElementById("auditHeaderScope").textContent = `Instancia ${scope.instance_id ?? "—"} · mapa ${detail.map?.map_id || "—"} · r${detail.map?.map_revision ?? "—"}`;
  const hps1 = document.getElementById("auditHps1Badge");
  hps1.textContent = "H-PS1 pendiente";
  hps1.className = "gate-badge pending";
  const workflowHps1 = document.getElementById("auditWorkflowHps1Step");
  workflowHps1.textContent = "H-PS1 pendiente";
  workflowHps1.className = "workflow-step pending";
  const hps2 = document.getElementById("auditHps2Badge");
  hps2.textContent = "H-PS2 no iniciado";
  hps2.className = "gate-badge pending";
  document.getElementById("auditWorkflowMapStep").textContent = "Mapa V2 · auditoría visual";
  document.getElementById("auditWorkflowIngridStep").textContent = "Ingrid no activada";
  document.getElementById("auditPageListTitle").textContent = `Páginas del mapa · ${scope.instance_type || "instancia"}`;
  document.getElementById("auditDecisionScope").textContent = ` · sesión ${session.session_id || "—"}`;
  for (const button of document.querySelectorAll(".audit-decision-actions button")) button.disabled = false;
}

function shortHash(value) {
  const text = String(value || "");
  return text.length > 18 ? `${text.slice(0, 10)}…${text.slice(-8)}` : text || "—";
}

function renderVisualSessionHashes() {
  const detail = state.audit.detail || {};
  const integrity = detail.integrity || {};
  const status = document.getElementById("auditSessionIntegrityStatus");
  status.textContent = integrity.status || "—";
  status.className = `status-value ${integrity.status === "passed" ? "precision-ready" : "precision-blocked"}`;
  const rows = [
    ["session_id", detail.session?.session_id],
    ["batch_id", detail.session?.batch_id],
    ["map_id / revisión", `${detail.map?.map_id || "—"} / r${detail.map?.map_revision ?? "—"}`],
    ["map_sha256", detail.map?.map_sha256],
    ["pdf_sha256", detail.source?.pdf_sha256],
    ["scope_fingerprint", detail.map?.scope_fingerprint],
    ["context_fingerprint", detail.map?.context_fingerprint],
    ["session_fingerprint", detail.session?.session_fingerprint],
    ["artifact_hashes_sha256", integrity.artifact_hashes_sha256],
    ["structural_ledger_sha256", integrity.structural_ledger_sha256],
  ];
  document.getElementById("auditSessionHashes").innerHTML = rows
    .map(([key, value]) => `<div class="hash-row"><span>${escapeHtml(key)}</span><code title="${escapeHtml(value || "")}">${escapeHtml(shortHash(value))}</code></div>`)
    .join("") + ((integrity.blockers || []).length
      ? `<div class="integrity-blockers">${integrity.blockers.map((item) => `<code>${escapeHtml(item)}</code>`).join("")}</div>`
      : "");
}

function renderAuditRelationList() {
  const list = document.getElementById("auditRelationList");
  document.getElementById("auditRelationCount").textContent = state.audit.relations.length;
  list.innerHTML = state.audit.relations.map((relation, index) => {
    const problem = relation.problem?.provisional_unit_id || "P?";
    const solution = relation.solution?.provisional_unit_id || "S?";
    const decision = state.audit.sessionDecisions[relation.relation_id];
    return `<button class="audit-relation-row ${index === state.audit.relationIndex ? "active" : ""}" data-audit-relation-index="${index}">
      <span class="relation-id">${escapeHtml(relation.relation_id || `R${index + 1}`)}</span>
      <strong>${escapeHtml(problem)} ↔ ${escapeHtml(solution)}</strong>
      <small>${escapeHtml(relation.editorial_number_raw || "sin número")} · ${Number(relation.confidence || 0).toFixed(2)}</small>
      <i class="relation-review-state">${escapeHtml(decision || relation.visual_review_state || "pending")}</i>
    </button>`;
  }).join("") || '<p class="empty-detail">La sesión no contiene relaciones representables.</p>';
  for (const button of list.querySelectorAll("[data-audit-relation-index]")) {
    button.addEventListener("click", () => selectAuditRelation(Number(button.dataset.auditRelationIndex), { focusPage: true }).catch(renderAuditFailure));
  }
}

function relationPaneHtml(unit, sectionIds, kind) {
  if (!unit || !unit.provisional_unit_id) return '<p class="empty-detail">Unidad provisional no disponible.</p>';
  const pageNumbers = unit.source_pages || [];
  const sectionSet = new Set(sectionIds || []);
  const pageCards = pageNumbers.map((pageNumber) => {
    const page = state.audit.pages.find((item) => item.page_number === pageNumber);
    if (!page) return `<p class="empty-detail">Página ${escapeHtml(pageNumber)} no representable.</p>`;
    const overlays = (page.page_sections || []).filter((section) => sectionSet.has(section.section_id)).map((section) => {
      const [x1, y1, x2, y2] = section.bbox_norm_xyxy || [0, 0, 0, 0];
      return `<span class="relation-coarse-overlay ${escapeHtml(kind)}" style="left:${x1 * 100}%;top:${y1 * 100}%;width:${(x2 - x1) * 100}%;height:${(y2 - y1) * 100}%" title="${escapeHtml(section.section_id)} · coarse"></span>`;
    }).join("");
    return `<figure class="relation-page-card">
      <div class="relation-page-media"><img src="${escapeHtml(page.image_url)}" alt="Página ${escapeHtml(pageNumber)}" />${overlays}</div>
      <figcaption>PDF p.${escapeHtml(pageNumber)} · ${escapeHtml((page.audit_roles?.roles || []).join(" + "))}</figcaption>
    </figure>`;
  }).join("");
  const evidence = (unit.evidence || []).map((item) => `<li>${escapeHtml(typeof item === "string" ? item : JSON.stringify(item))}</li>`).join("");
  return `<header class="relation-unit-header">
      <span>${kind === "problem" ? "Problema" : "Solución"}</span>
      <strong>${escapeHtml(unit.provisional_unit_id)} · editorial ${escapeHtml(unit.editorial_number_raw || "—")}</strong>
      <code>${escapeHtml(shortHash(unit.unit_fingerprint))}</code>
    </header>
    <div class="relation-page-stack">${pageCards}</div>
    <dl class="relation-unit-meta"><div><dt>orden</dt><dd>${escapeHtml(unit.reading_order ?? "—")}</dd></div><div><dt>confianza</dt><dd>${Number(unit.confidence || 0).toFixed(2)}</dd></div><div><dt>secciones</dt><dd>${escapeHtml((sectionIds || []).join(" · ") || "—")}</dd></div></dl>
    ${evidence ? `<ul class="relation-evidence">${evidence}</ul>` : ""}`;
}

async function selectAuditRelation(index, options = {}) {
  const relation = state.audit.relations[index];
  if (!relation) return;
  state.audit.relationIndex = index;
  state.audit.activeRelation = relation;
  renderAuditRelationList();
  document.getElementById("auditRelationTitle").textContent = `${relation.relation_id} · ${relation.problem?.provisional_unit_id || "P?"} ↔ ${relation.solution?.provisional_unit_id || "S?"}`;
  const relationStatus = document.getElementById("auditRelationStatus");
  relationStatus.textContent = state.audit.sessionDecisions[relation.relation_id] || relation.visual_review_state || "pending";
  relationStatus.className = "status-value pending";
  document.getElementById("auditRelationProblemPane").innerHTML = relationPaneHtml(relation.problem, relation.problem_section_ids, "problem");
  document.getElementById("auditRelationSolutionPane").innerHTML = relationPaneHtml(relation.solution, relation.solution_section_ids, "solution");
  renderVisualRelationDetail();
  document.getElementById("auditDecisionScope").textContent = ` · ${relation.relation_id} · ${relation.problem?.provisional_unit_id || "P?"} ↔ ${relation.solution?.provisional_unit_id || "S?"}`;
  const decision = state.audit.sessionDecisions[relation.relation_id];
  document.getElementById("auditDecisionNote").textContent = decision
    ? `Marca local de sesión para ${relation.relation_id}: ${decision}. No aprueba H-PS1 ni escribe datos.`
    : "Revisa ambas páginas y sus regiones coarse. Las marcas son locales: no aprueban H-PS1 ni activan a Ingrid.";
  if (options.focusPage) {
    const targetPage = (relation.problem_page_numbers || [])[0] ?? (relation.solution_page_numbers || [])[0];
    const pageIndex = state.audit.filteredPages.findIndex((page) => page.page_number === targetPage);
    if (pageIndex >= 0) await selectAuditPageByIndex(pageIndex);
  } else {
    const page = state.audit.filteredPages[state.audit.pageIndex];
    if (page) renderAuditOverlays(page);
  }
}

function renderVisualRelationDetail() {
  const relation = state.audit.activeRelation;
  const element = document.getElementById("auditRelationDetail");
  if (!relation) {
    element.innerHTML = '<p class="empty-detail">Sin relación seleccionada.</p>';
    return;
  }
  const rows = [
    ["relation_id", relation.relation_id],
    ["tipo", relation.relation_type],
    ["problema", relation.problem?.provisional_unit_ref],
    ["solución", relation.solution?.provisional_unit_ref],
    ["número editorial", relation.editorial_number_raw],
    ["confianza", Number(relation.confidence || 0).toFixed(2)],
    ["relation_fingerprint", relation.relation_fingerprint],
    ["revisión", relation.review_status || "pending"],
  ];
  element.innerHTML = rows.map(([key, value]) => `<div class="traceability-row"><span>${escapeHtml(key)}</span><code title="${escapeHtml(value || "")}">${escapeHtml(key.includes("fingerprint") ? shortHash(value) : value || "—")}</code></div>`).join("");
  if ((relation.uncertainties || []).length) {
    element.innerHTML += `<div class="relation-uncertainties">${relation.uncertainties.map((item) => `<p>${escapeHtml(typeof item === "string" ? item : JSON.stringify(item))}</p>`).join("")}</div>`;
  }
}

function populateAuditBookSelect() {
  const select = document.getElementById("auditBookSelect");
  const books = new Map();
  for (const instance of state.audit.instances) {
    if (!books.has(instance.book_code)) books.set(instance.book_code, instance.title);
  }
  select.innerHTML = [...books.entries()]
    .sort((left, right) => left[1].localeCompare(right[1], "es"))
    .map(([bookCode, title]) => `<option value="${escapeHtml(bookCode)}">${escapeHtml(title)}</option>`)
    .join("");
}

function populateAuditInstanceSelect(preferredAssignmentId = "") {
  const bookCode = document.getElementById("auditBookSelect").value;
  const select = document.getElementById("auditInstanceSelect");
  const instances = state.audit.instances.filter((item) => item.book_code === bookCode);
  select.innerHTML = instances
    .map((item) => {
      const status = item.ingrid_status === "not_started" ? "sin salida Ingrid" : "Ingrid disponible";
      return `<option value="${escapeHtml(item.assignment_id)}">${escapeHtml(item.instance_type)} · ${escapeHtml(item.instance_id)} · ${status}</option>`;
    })
    .join("");
  if (preferredAssignmentId && instances.some((item) => item.assignment_id === preferredAssignmentId)) {
    select.value = preferredAssignmentId;
  }
}

async function loadAuditInstance(assignmentId) {
  if (!assignmentId) return;
  const loadSeq = state.audit.loadSeq + 1;
  state.audit.loadSeq = loadSeq;
  const empty = document.getElementById("auditEmptyState");
  empty.hidden = false;
  empty.innerHTML = "<strong>Cargando instancia…</strong><span>Normalizando artefactos de Gottfried e Ingrid.</span>";
  document.getElementById("auditPageImage").hidden = true;
  document.getElementById("auditOverlayLayer").replaceChildren();
  const detail = await apiGet(`/api/library-audit/instance?id=${encodeURIComponent(assignmentId)}`);
  if (loadSeq !== state.audit.loadSeq) return;
  state.audit.detail = detail;
  state.audit.pages = detail.pages || [];
  state.audit.relations = [];
  state.audit.relationIndex = -1;
  state.audit.activeRelation = null;
  state.audit.pageIndex = -1;
  state.audit.sessionDecisions = {};
  renderAuditInstanceHeader();
  renderAuditComposition();
  renderAuditEligibilityChips();
  applyAuditPageFilter();
  const preferredIndex = state.audit.filteredPages.findIndex((page) =>
    (page.audit_roles?.roles || []).length > 1 && (page.precise_boxes || []).some((box) => box.role === "solution"));
  const solutionIndex = state.audit.filteredPages.findIndex((page) => (page.precise_boxes || []).some((box) => box.role === "solution"));
  await selectAuditPageByIndex(preferredIndex >= 0 ? preferredIndex : solutionIndex >= 0 ? solutionIndex : 0);
}

function renderAuditInstanceHeader() {
  const detail = state.audit.detail;
  if (!detail) return;
  const scope = detail.scope || {};
  document.getElementById("auditHeaderTitle").textContent = detail.title || scope.book_code || "Biblioteca";
  document.getElementById("auditHeaderScope").textContent = `Instancia ${scope.instance_id ?? "—"} · revisión ${detail.map?.map_revision ?? "—"} · ${detail.map?.status || "sin mapa"}`;
  const hps1 = document.getElementById("auditHps1Badge");
  const hps1Label = detail.gates?.h_ps1 === "approved" ? "aprobado" : detail.gates?.h_ps1 || "—";
  hps1.textContent = `H-PS1 ${hps1Label}`;
  hps1.className = `gate-badge ${detail.gates?.h_ps1 === "approved" ? "approved" : "pending"}`;
  const workflowHps1 = document.getElementById("auditWorkflowHps1Step");
  workflowHps1.textContent = `H-PS1 ${hps1Label}`;
  workflowHps1.className = `workflow-step ${detail.gates?.h_ps1 === "approved" ? "approved" : "pending"}`;
  const hps2 = document.getElementById("auditHps2Badge");
  const hps2Label = detail.gates?.h_ps2 === "pending" ? "pendiente" : detail.gates?.h_ps2 === "approved" ? "aprobado" : detail.gates?.h_ps2 || "—";
  hps2.textContent = `H-PS2 ${hps2Label}`;
  hps2.className = `gate-badge ${detail.gates?.h_ps2 === "approved" ? "approved" : "pending"}`;
  const mapSchema = detail.map?.schema_version || "";
  document.getElementById("auditWorkflowMapStep").textContent = mapSchema.includes("_v2") ? "Mapa V2" : "Mapa V1 adaptado";
  document.getElementById("auditWorkflowIngridStep").textContent = detail.ingrid?.status === "not_started" ? "Ingrid sin iniciar" : "Ingrid activa";
  document.getElementById("auditPageListTitle").textContent = `Páginas autorizadas · ${scope.instance_type || "instancia"}`;
}

function renderAuditComposition() {
  const pages = state.audit.pages;
  const roleCount = (role) => pages.filter((page) => (page.audit_roles?.roles || []).includes(role)).length;
  document.getElementById("auditTheoryCount").textContent = roleCount("theory");
  document.getElementById("auditProblemCount").textContent = roleCount("problem");
  document.getElementById("auditSolutionCount").textContent = roleCount("solution");
  document.getElementById("auditMixedCount").textContent = pages.filter((page) => (page.audit_roles?.roles || []).length > 1).length;
}

function renderAuditEligibilityChips() {
  if (state.audit.stage === "pre_h_ps1") {
    const sessionStatus = state.audit.detail?.session?.status || "visual_audit_blocked";
    document.getElementById("auditEligibilitySource").textContent = "Sesión visual V1";
    document.getElementById("auditEligibilityChips").innerHTML = ["ready_for_visual_audit", "visual_audit_blocked"]
      .map((status) => `<span class="eligibility-chip ${status} ${sessionStatus === status ? "active" : ""}">${status}</span>`)
      .join("");
    return;
  }
  const eligibility = state.audit.detail?.eligibility || {};
  const statuses = ["eligible_full", "eligible_partial", "pending_review", "not_eligible"];
  document.getElementById("auditEligibilitySource").textContent = eligibility.contract_source === "explicit" ? "Contrato V2" : "Adaptador legado";
  document.getElementById("auditEligibilityChips").innerHTML = statuses
    .map((status) => `<span class="eligibility-chip ${status} ${eligibility.status === status ? "active" : ""}">${status}</span>`)
    .join("");
}

function auditPageMatchesFilter(page, filter) {
  const roles = page.audit_roles?.roles || [];
  if (!filter) return true;
  if (["theory", "problem", "solution"].includes(filter)) return roles.includes(filter);
  if (filter === "mixed") return roles.length > 1;
  if (filter === "with_boxes") return (page.precise_boxes || []).length > 0;
  if (filter === "pending_traceability") return page.traceability?.status === "legacy_missing_provisional_links";
  if (filter === "precision_blocked") return page.precision_validation?.h_ps2_ready !== true;
  return true;
}

function applyAuditPageFilter() {
  const filter = document.getElementById("auditPageFilter").value;
  state.audit.filteredPages = state.audit.pages.filter((page) => auditPageMatchesFilter(page, filter));
  document.getElementById("auditPageCount").textContent = `${state.audit.filteredPages.length}/${state.audit.pages.length}`;
  renderAuditPageList();
  if (!state.audit.filteredPages.length) {
    state.audit.pageIndex = -1;
    renderAuditFailure(new Error("Ninguna página coincide con este filtro."));
  }
}

function auditRoleChip(role, label = "") {
  return `<span class="role-chip ${escapeHtml(role)}">${escapeHtml(label || AUDIT_ROLE_LABELS[role] || CONTENT_ROLE_LABELS[role] || role)}</span>`;
}

function renderAuditPageList() {
  const list = document.getElementById("auditPageList");
  list.innerHTML = state.audit.filteredPages.map((page, index) => {
    const roles = page.audit_roles?.roles || [];
    const chips = roles.length ? roles.map((role) => auditRoleChip(role)).join("") : auditRoleChip("unknown", "Sin rol V2");
    const preciseCount = (page.precise_boxes || []).length;
    const layerLabel = state.audit.stage === "pre_h_ps1"
      ? `${(page.page_sections || []).length} coarse`
      : preciseCount ? `${preciseCount} px` : "—";
    return `<button class="audit-page-row ${index === state.audit.pageIndex ? "active" : ""}" data-audit-page-index="${index}">
      <i class="audit-page-dot"></i>
      <span class="page-number">${page.page_number}</span>
      <span class="page-role-chips">${chips}</span>
      <span class="audit-page-box-count">${layerLabel}</span>
    </button>`;
  }).join("");
  for (const button of list.querySelectorAll("[data-audit-page-index]")) {
    button.addEventListener("click", () => selectAuditPageByIndex(Number(button.dataset.auditPageIndex)));
  }
}

async function selectAuditPageByIndex(index) {
  const page = state.audit.filteredPages[index];
  if (!page) return;
  state.audit.pageIndex = index;
  renderAuditPageList();
  const activeRow = document.querySelector(`.audit-page-row[data-audit-page-index="${index}"]`);
  activeRow?.scrollIntoView({ block: "nearest" });
  document.getElementById("auditPageTitle").textContent = `Página ${page.page_number} de ${state.audit.detail?.source?.page_count || state.audit.pages.length}`;
  document.getElementById("auditPageInput").value = page.page_number;
  document.getElementById("auditPageTotal").textContent = `de ${state.audit.detail?.source?.page_count || state.audit.pages.length}`;
  document.getElementById("auditPrevPageBtn").disabled = index <= 0;
  document.getElementById("auditNextPageBtn").disabled = index >= state.audit.filteredPages.length - 1;
  document.getElementById("auditDecisionScope").textContent = state.audit.stage === "pre_h_ps1"
    ? ` · ${state.audit.activeRelation?.relation_id || "sin relación"} · página ${page.page_number}`
    : ` · boxes de la página ${page.page_number}`;
  const approveButton = document.getElementById("auditApprovePageBtn");
  const precisionReady = page.precision_validation?.h_ps2_ready === true;
  const visualReady = state.audit.detail?.session?.status === "ready_for_visual_audit" && Boolean(state.audit.activeRelation);
  approveButton.disabled = state.audit.stage === "pre_h_ps1" ? !visualReady : !precisionReady;
  approveButton.title = state.audit.stage === "pre_h_ps1"
    ? "Marca local de revisión; H-PS1 requiere una orden humana posterior."
    : precisionReady
      ? "Precision checks passed; human approval remains non-persistent."
      : "H-PS2 blocked until every precision check passes.";
  const decisionKey = state.audit.stage === "pre_h_ps1" ? state.audit.activeRelation?.relation_id : page.page_number;
  const decision = state.audit.sessionDecisions[decisionKey];
  const note = document.getElementById("auditDecisionNote");
  note.textContent = decision
    ? `Marca local de sesión: ${decision}. No se escribió staging, app ni BD.`
    : state.audit.stage === "pre_h_ps1"
      ? "Revisa la relación lado a lado. Ninguna marca aprueba H-PS1 ni activa a Ingrid."
      : "Estas acciones solo marcan la sesión visual. La persistencia H-PS2 requiere el aplicador controlado y revisión humana.";
  note.classList.toggle("session-marked", Boolean(decision));
  renderAuditPageDetail(page);
  await loadAuditPageImage(page);
}

async function loadAuditPageImage(page) {
  const image = document.getElementById("auditPageImage");
  const empty = document.getElementById("auditEmptyState");
  document.getElementById("auditOverlayLayer").replaceChildren();
  if (!page.image_url) {
    image.hidden = true;
    empty.hidden = false;
    empty.innerHTML = "<strong>Imagen no disponible</strong><span>El registro estructural no expone una evidencia visual válida dentro del catálogo permitido.</span>";
    return;
  }
  const expectedPage = page.page_number;
  await new Promise((resolve) => {
    image.onload = () => {
      if (state.audit.filteredPages[state.audit.pageIndex]?.page_number !== expectedPage) return resolve();
      state.audit.naturalWidth = image.naturalWidth;
      state.audit.naturalHeight = image.naturalHeight;
      image.hidden = false;
      empty.hidden = true;
      fitAuditPage();
      resolve();
    };
    image.onerror = () => {
      image.hidden = true;
      empty.hidden = false;
      empty.innerHTML = "<strong>No se pudo abrir la página</strong><span>La evidencia visual registrada ya no está disponible.</span>";
      resolve();
    };
    image.src = page.image_url;
  });
}

function fitAuditPage() {
  const shell = document.querySelector(".audit-stage-shell");
  if (!state.audit.naturalWidth || !state.audit.naturalHeight || !shell) return;
  const widthScale = Math.max(0.1, (shell.clientWidth - 30) / state.audit.naturalWidth);
  const heightScale = Math.max(0.1, (shell.clientHeight - 30) / state.audit.naturalHeight);
  state.audit.fitScale = Math.min(widthScale, heightScale, 1);
  setAuditZoom(state.audit.fitScale);
}

function setAuditZoom(scale) {
  if (!state.audit.naturalWidth || !state.audit.naturalHeight) return;
  state.audit.scale = Math.max(0.1, Math.min(2.5, scale));
  const stage = document.getElementById("auditPageStage");
  stage.style.width = `${Math.round(state.audit.naturalWidth * state.audit.scale)}px`;
  stage.style.height = `${Math.round(state.audit.naturalHeight * state.audit.scale)}px`;
  document.getElementById("auditZoomLabel").textContent = `${Math.round((state.audit.scale / state.audit.fitScale) * 100)}%`;
  const page = state.audit.filteredPages[state.audit.pageIndex];
  if (page) renderAuditOverlays(page);
}

function renderAuditOverlays(page) {
  const layer = document.getElementById("auditOverlayLayer");
  layer.replaceChildren();
  const width = state.audit.naturalWidth;
  const height = state.audit.naturalHeight;
  const scale = state.audit.scale;
  if (!width || !height) return;
  const sourceWidth = page.image_width || width;
  const sourceHeight = page.image_height || height;
  const pixelScaleX = width / sourceWidth;
  const pixelScaleY = height / sourceHeight;

  for (const section of page.page_sections || []) {
    const [x1, y1, x2, y2] = section.bbox_norm_xyxy || [];
    const box = document.createElement("div");
    const problemSections = state.audit.activeRelation?.problem_section_ids || [];
    const solutionSections = state.audit.activeRelation?.solution_section_ids || [];
    const activeKind = problemSections.includes(section.section_id)
      ? "relation-problem"
      : solutionSections.includes(section.section_id)
        ? "relation-solution"
        : "";
    box.className = `audit-overlay-box coarse ${activeKind ? "relation-active" : ""} ${activeKind}`;
    box.dataset.label = `${(section.audit_roles || []).map((role) => AUDIT_ROLE_LABELS[role] || role).join(" + ") || "Región"} · Gottfried · coarse`;
    box.style.left = `${x1 * width * scale}px`;
    box.style.top = `${y1 * height * scale}px`;
    box.style.width = `${(x2 - x1) * width * scale}px`;
    box.style.height = `${(y2 - y1) * height * scale}px`;
    layer.appendChild(box);
  }

  for (const precise of page.precise_boxes || []) {
    const [x1, y1, x2, y2] = precise.bbox_xyxy || [];
    const box = document.createElement("div");
    box.className = `audit-overlay-box ${precise.role}`;
    const roleLabel = precise.role === "problem_number" ? "Número" : precise.role === "solution" ? "Solución" : "Problema";
    box.dataset.label = `${roleLabel} · Ingrid`;
    if (precise.role === "answer_block") box.dataset.label = "Alternativas - Ingrid";
    box.style.left = `${x1 * pixelScaleX * scale}px`;
    box.style.top = `${y1 * pixelScaleY * scale}px`;
    box.style.width = `${(x2 - x1) * pixelScaleX * scale}px`;
    box.style.height = `${(y2 - y1) * pixelScaleY * scale}px`;
    layer.appendChild(box);
  }
}

function renderRoleChips(elementId, roles, content = false) {
  const element = document.getElementById(elementId);
  element.innerHTML = roles.length
    ? roles.map((role) => auditRoleChip(role, content ? CONTENT_ROLE_LABELS[role] || role : "")).join("")
    : '<span class="role-chip">No disponible</span>';
}

function renderAuditPageDetail(page) {
  const detail = state.audit.detail;
  const auditRoles = page.audit_roles?.roles || [];
  renderRoleChips("auditContentRoles", page.content_roles || [], true);
  renderRoleChips("auditRoles", auditRoles);
  document.getElementById("auditMappingVersion").textContent = page.audit_roles?.mapping_version || "—";
  document.getElementById("auditConfidence").textContent = page.confidence == null ? "—" : Number(page.confidence).toFixed(2);
  document.getElementById("auditPageSourceBadge").textContent = page.structural_schema_version === "not_available" ? "Sin registro estructural" : page.structural_schema_version;
  document.getElementById("auditPageFootnote").textContent = page.page_sections?.length
    ? `${page.page_sections.length} regiones coarse visibles; no son boxes finales.`
    : "Este artefacto heredado no incluye page_sections V2; no se fabrican regiones.";

  const counts = page.observed_counts || {};
  const layerRows = state.audit.stage === "pre_h_ps1"
    ? [
        [page.page_sections?.length || 0, "Regiones coarse de Gottfried"],
        [(page.provisional_unit_refs || []).filter((ref) => /:P\d+$/.test(ref)).length, "Unidades P en página"],
        [(page.provisional_unit_refs || []).filter((ref) => /:S\d+$/.test(ref)).length, "Unidades S en página"],
        [0, "Boxes/crops finales"],
      ]
    : [
        [page.page_sections?.length || 0, "Regiones coarse de Gottfried"],
        [counts.problem_boxes || 0, "Boxes de problema"],
        [counts.problem_number_boxes || 0, "Boxes de número"],
        [counts.solution_fragments || 0, "Fragmentos de solución"],
      ];
  document.getElementById("auditLayerSummary").innerHTML = layerRows
    .map(([value, label]) => `<span class="layer-metric"><strong>${value}</strong><span>${label}</span></span>`)
    .join("");

  const uncertainties = page.uncertainty_reasons || [];
  document.getElementById("auditUncertaintyCard").hidden = uncertainties.length === 0;
  document.getElementById("auditUncertainties").innerHTML = uncertainties.map((item) => `<p>${escapeHtml(typeof item === "string" ? item : JSON.stringify(item))}</p>`).join("");
  renderAuditStatistics(page);
  renderAuditEligibility(detail);
  renderAuditTraceability(page, detail);
  renderAuditPrecision(page);
}

function renderAuditPrecision(page) {
  if (state.audit.stage === "pre_h_ps1") {
    const status = document.getElementById("auditPrecisionStatus");
    status.textContent = "no iniciado";
    status.className = "status-value pending";
    document.getElementById("auditPrecisionQuality").innerHTML = `
      <div class="precision-summary-grid">
        <span><strong>0</strong><small>boxes Ingrid</small></span>
        <span><strong>0</strong><small>crops finales</small></span>
        <span><strong>H-PS1</strong><small>gate pendiente</small></span>
      </div>
      <p class="empty-detail">Esta sesión audita estructura y relaciones provisionales. Ingrid no está activada y las regiones coarse no son boxes finales.</p>`;
    document.getElementById("auditPrecisionIssues").innerHTML = '<p class="empty-detail">La precisión H-PS2 no corresponde a la etapa pre-H-PS1.</p>';
    return;
  }
  const precision = page.precision_validation || {};
  const status = document.getElementById("auditPrecisionStatus");
  const ready = precision.h_ps2_ready === true;
  const applicable = precision.applicable === true;
  status.textContent = ready ? "ready" : applicable ? "blocked" : "sin V2";
  status.className = `status-value ${ready ? "precision-ready" : "precision-blocked"}`;

  const summary = precision.summary || {};
  const unitResults = precision.unit_results || [];
  const qualityRows = [];
  for (const unit of unitResults) {
    for (const [regionId, checks] of Object.entries(unit.quality_checks || {})) {
      for (const [check, value] of Object.entries(checks || {})) {
        qualityRows.push(`<div class="precision-check-row"><code>${escapeHtml(regionId)}</code><span>${escapeHtml(check)}</span><strong class="validation-chip ${escapeHtml(value)}">${escapeHtml(value)}</strong></div>`);
      }
    }
  }
  document.getElementById("auditPrecisionQuality").innerHTML = `
    <div class="precision-summary-grid">
      <span><strong>${summary.unit_count ?? 0}</strong><small>unidades</small></span>
      <span><strong>${summary.answer_block_count ?? 0}</strong><small>answer blocks</small></span>
      <span><strong>${summary.blocking_issue_count ?? 0}</strong><small>bloqueos</small></span>
    </div>
    ${qualityRows.length ? `<div class="precision-check-list">${qualityRows.join("")}</div>` : '<p class="empty-detail">La salida actual no contiene controles ingrid_geometry_quality_v1; no se infieren.</p>'}`;

  const issues = precision.issues || [];
  const warnings = precision.warnings || [];
  document.getElementById("auditPrecisionIssues").innerHTML = [
    ...issues.map((issue) => `<div class="precision-issue blocking"><span>bloqueo</span><code>${escapeHtml(issue)}</code></div>`),
    ...warnings.map((warning) => `<div class="precision-issue warning"><span>advertencia</span><code>${escapeHtml(warning)}</code></div>`),
  ].join("") || '<p class="empty-detail">Sin bloqueos ni advertencias declaradas.</p>';
}

async function validateAuditPrecision(annotation) {
  return apiPost(AUDIT_PRECISION_VALIDATION_ENDPOINT, { annotation });
}

function metricEstimate(metric) {
  if (!metric || metric.estimate == null) return "—";
  const range = metric.minimum_estimate != null && metric.maximum_estimate != null
    ? ` [${metric.minimum_estimate}–${metric.maximum_estimate}]`
    : "";
  const confidence = metric.confidence != null ? ` · ${Number(metric.confidence).toFixed(2)}` : "";
  return `${metric.estimate}${range}${confidence}`;
}

function renderAuditStatistics(page) {
  const statistics = page.page_statistics;
  const structural = document.getElementById("auditStructuralStatistics");
  if (!statistics) {
    structural.innerHTML = '<p class="empty-detail">No disponible en el registro V1. La app no infiere estadísticas contractuales ni inventa conteos de Gottfried.</p>';
  } else {
    const keys = ["problem_units", "proposed_problems", "solved_problems", "solution_units", "worked_examples"];
    structural.innerHTML = keys.map((key) => `<div class="statistics-row"><span>${key}</span><strong>${escapeHtml(metricEstimate(statistics[key]))}</strong></div>`).join("");
    const validations = statistics.validations || {};
    structural.innerHTML += `<div class="statistics-validations">${["problem_partition_ok", "solution_count_valid", "statistics_consistent"]
      .map((key) => `<span class="validation-chip ${escapeHtml(validations[key] || "uncertain")}">${key}: ${escapeHtml(validations[key] || "uncertain")}</span>`).join("")}</div>`;
  }
  if (state.audit.stage === "pre_h_ps1") {
    document.getElementById("auditObservedStatistics").innerHTML = [
      ["provisional_units_on_page", (page.provisional_unit_refs || []).length],
      ["coarse_regions", (page.page_sections || []).length],
      ["precise_boxes", 0],
      ["final_crops", 0],
    ].map(([key, value]) => `<div class="statistics-row"><span>${key}</span><strong>${value}</strong></div>`).join("");
    return;
  }
  const observed = page.observed_counts || {};
  document.getElementById("auditObservedStatistics").innerHTML = [
    ["problem_boxes", observed.problem_boxes],
    ["problem_number_boxes", observed.problem_number_boxes],
    ["solution_units", observed.solution_units],
    ["solution_fragments", observed.solution_fragments],
  ].map(([key, value]) => `<div class="statistics-row"><span>${key}</span><strong>${value ?? 0}</strong></div>`).join("");
}

function booleanLabel(value) {
  if (value === "unknown") return "unknown";
  return value ? "true" : "false";
}

function renderAuditEligibility(detail) {
  if (state.audit.stage === "pre_h_ps1") {
    const visualStatus = detail.session?.status || "visual_audit_blocked";
    const integrityStatus = detail.integrity?.status || "failed";
    const status = document.getElementById("auditEligibilityStatus");
    status.textContent = visualStatus;
    status.className = `status-value ${visualStatus}`;
    const summaryStatus = document.getElementById("auditSummaryEligibilityStatus");
    summaryStatus.textContent = visualStatus;
    summaryStatus.className = `status-value ${visualStatus}`;
    const rows = [
      ["integridad visual", integrityStatus],
      ["map status", detail.map?.status || "—"],
      ["H-PS1", detail.gates?.h_ps1 || "pending"],
      ["activate_ingrid", booleanLabel(detail.gates?.activate_ingrid)],
    ];
    document.getElementById("auditSummaryEligibility").innerHTML = rows
      .map(([key, value]) => `<div class="eligibility-row"><span>${key}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
    document.getElementById("auditEligibilityDetail").innerHTML = [
      ...rows,
      ["revisión del mapa", `r${detail.map?.map_revision ?? "—"}`],
      ["persistencia", detail.canonical_writes || "disabled"],
    ].map(([key, value]) => `<div class="eligibility-row"><span>${key}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
    document.getElementById("auditGateDetail").innerHTML = [
      ["H-PS1", "pending · requiere orden humana posterior"],
      ["H-PS2", "not_started"],
      ["Ingrid", "no activada"],
      ["escrituras", "disabled"],
    ].map(([key, value]) => `<div class="gate-row"><span>${key}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
    return;
  }
  const eligibility = detail.eligibility || {};
  const status = document.getElementById("auditEligibilityStatus");
  status.textContent = `${eligibility.status || "—"}${eligibility.confidence != null ? ` · ${Number(eligibility.confidence).toFixed(2)}` : ""}`;
  status.className = `status-value ${eligibility.status || ""}`;
  const summaryStatus = document.getElementById("auditSummaryEligibilityStatus");
  summaryStatus.textContent = eligibility.status || "—";
  summaryStatus.className = `status-value ${eligibility.status || ""}`;
  document.getElementById("auditSummaryEligibility").innerHTML = [
    ["can_generate_map", booleanLabel(eligibility.can_generate_map)],
    ["generate_map", booleanLabel(eligibility.generate_map)],
    ["activate_ingrid", booleanLabel(eligibility.activate_ingrid)],
  ].map(([key, value]) => `<div class="eligibility-row"><span>${key}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
  document.getElementById("auditEligibilityDetail").innerHTML = [
    ["can_generate_map", booleanLabel(eligibility.can_generate_map)],
    ["should_generate_now", booleanLabel(eligibility.should_generate_now)],
    ["generate_map", `${booleanLabel(eligibility.generate_map)} · Euler`],
    ["activate_ingrid", `${booleanLabel(eligibility.activate_ingrid)} · H-PS1`],
    ["fuente", eligibility.contract_source || "—"],
  ].map(([key, value]) => `<div class="eligibility-row"><span>${key}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
  if (eligibility.reason) {
    document.getElementById("auditEligibilityDetail").innerHTML += `<p class="card-note">${escapeHtml(eligibility.reason)}</p>`;
  }
  document.getElementById("auditGateDetail").innerHTML = [
    ["H-PS1", detail.gates?.h_ps1 || "—"],
    ["H-PS2", detail.gates?.h_ps2 || "—"],
    ["siguiente gate", detail.gates?.next_gate || "—"],
    ["persistencia", detail.canonical_writes || "disabled"],
  ].map(([key, value]) => `<div class="gate-row"><span>${key}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
}

function renderAuditTraceability(page, detail) {
  if (state.audit.stage === "pre_h_ps1") {
    const ids = page.provisional_unit_refs || [];
    const relation = state.audit.activeRelation;
    const traceabilityElement = document.getElementById("auditTraceabilityDetail");
    traceabilityElement.innerHTML = ids.length
      ? ids.map((id) => `<div class="traceability-row"><span>unidad provisional</span><code>${escapeHtml(id)}</code></div>`).join("")
      : '<p class="empty-detail">Esta página no participa en una unidad P/S del mapa.</p>';
    document.getElementById("auditSummaryTraceability").innerHTML = relation
      ? `<div class="traceability-row"><span>relación</span><code>${escapeHtml(relation.relation_id)}</code></div><div class="traceability-row"><span>P ↔ S</span><code>${escapeHtml(relation.problem?.provisional_unit_id || "P?")} ↔ ${escapeHtml(relation.solution?.provisional_unit_id || "S?")}</code></div>`
      : '<p class="empty-detail">Selecciona una relación provisional.</p>';
    const uncertainties = relation?.uncertainties || detail.uncertainties || [];
    document.getElementById("auditIssueDetail").innerHTML = uncertainties.length
      ? uncertainties.map((item) => `<div class="traceability-row"><span>incertidumbre</span><code>${escapeHtml(typeof item === "string" ? item : JSON.stringify(item))}</code></div>`).join("")
      : '<p class="empty-detail">Sin incertidumbres declaradas para la relación activa.</p>';
    return;
  }
  const traceability = page.traceability || {};
  const ids = traceability.source_provisional_unit_ids || [];
  const relationTypes = traceability.relation_types || [];
  const traceabilityElement = document.getElementById("auditTraceabilityDetail");
  traceabilityElement.innerHTML = `<div class="traceability-row"><span>estado</span><code>${escapeHtml(traceability.status || "—")}</code></div>`;
  if (ids.length) {
    traceabilityElement.innerHTML += ids.map((id) => `<div class="traceability-row"><span>unidad provisional</span><code>${escapeHtml(id)}</code></div>`).join("");
    traceabilityElement.innerHTML += relationTypes.map((type) => `<div class="traceability-row"><span>refinamiento</span><code>${escapeHtml(type)}</code></div>`).join("");
  } else {
    traceabilityElement.innerHTML += '<p class="empty-detail">La salida actual es V1 y no contiene source_provisional_unit_ids. Se muestra como brecha contractual, no como relación inferida.</p>';
  }
  document.getElementById("auditSummaryTraceability").innerHTML = ids.length
    ? `<div class="traceability-row"><span>unidades fuente</span><code>${escapeHtml(ids.join(" · "))}</code></div><div class="traceability-row"><span>relación</span><code>${escapeHtml(relationTypes.join(" · ") || "declarada")}</code></div>`
    : `<div class="traceability-row"><span>estado</span><code>${escapeHtml(traceability.status || "—")}</code></div><p class="empty-detail">Salida V1: falta source_provisional_unit_ids; no se infiere una relación.</p>`;
  const issues = detail.ingrid?.issues || [];
  document.getElementById("auditIssueDetail").innerHTML = issues.length
    ? issues.map((issue) => `<div class="traceability-row"><span>incidencia</span><code>${escapeHtml(typeof issue === "string" ? issue : JSON.stringify(issue))}</code></div>`).join("")
    : '<p class="empty-detail">Sin incidencias globales declaradas para esta salida.</p>';
}

function selectAuditInspectorPanel(panelName) {
  for (const button of document.querySelectorAll("#auditInspectorTabs [data-audit-panel]")) {
    button.classList.toggle("active", button.dataset.auditPanel === panelName);
  }
  for (const panel of document.querySelectorAll("[data-audit-panel-content]")) {
    panel.classList.toggle("active", panel.dataset.auditPanelContent === panelName);
  }
}

function markAuditSessionDecision(action) {
  const page = state.audit.filteredPages[state.audit.pageIndex];
  const relation = state.audit.activeRelation;
  if (state.audit.stage === "pre_h_ps1") {
    if (!relation || state.audit.detail?.session?.status !== "ready_for_visual_audit") return;
    state.audit.sessionDecisions[relation.relation_id] = action;
    renderAuditRelationList();
    renderVisualRelationDetail();
    document.getElementById("auditRelationStatus").textContent = action;
    const note = document.getElementById("auditDecisionNote");
    note.textContent = `Marca local para ${relation.relation_id}: ${action}. No aprueba H-PS1, no activa a Ingrid y no escribe datos.`;
    note.classList.add("session-marked");
    log(`Auditoría pre-H-PS1 · ${relation.relation_id}: ${action} (solo navegador, sin escritura).`);
    return;
  }
  if (!page) return;
  state.audit.sessionDecisions[page.page_number] = action;
  const note = document.getElementById("auditDecisionNote");
  note.textContent = `Marca local de sesión: ${action}. No se escribió staging, app ni BD.`;
  note.classList.add("session-marked");
  log(`Auditoría Biblioteca · página ${page.page_number}: ${action} (solo sesión, sin escritura).`);
}

function bindAuditEvents() {
  document.getElementById("datasetViewTab").addEventListener("click", () => setActiveView("dataset"));
  document.getElementById("libraryAuditTab").addEventListener("click", () => setActiveView("library-audit"));
  document.getElementById("auditStageSelect").addEventListener("change", (event) => {
    state.audit.stage = event.target.value;
    state.audit.catalog = null;
    state.audit.detail = null;
    state.audit.pages = [];
    state.audit.filteredPages = [];
    state.audit.relations = [];
    state.audit.activeRelation = null;
    loadLibraryAuditCatalog().catch(renderAuditFailure);
  });
  document.getElementById("auditSessionSelect").addEventListener("change", (event) => loadVisualAuditSession(event.target.value).catch(renderAuditFailure));
  document.getElementById("auditBookSelect").addEventListener("change", () => {
    populateAuditInstanceSelect();
    loadAuditInstance(document.getElementById("auditInstanceSelect").value).catch(renderAuditFailure);
  });
  document.getElementById("auditInstanceSelect").addEventListener("change", (event) => loadAuditInstance(event.target.value).catch(renderAuditFailure));
  document.getElementById("auditPageFilter").addEventListener("change", () => {
    applyAuditPageFilter();
    if (state.audit.filteredPages.length) selectAuditPageByIndex(0);
  });
  document.getElementById("auditPrevPageBtn").addEventListener("click", () => selectAuditPageByIndex(Math.max(0, state.audit.pageIndex - 1)));
  document.getElementById("auditNextPageBtn").addEventListener("click", () => selectAuditPageByIndex(Math.min(state.audit.filteredPages.length - 1, state.audit.pageIndex + 1)));
  document.getElementById("auditPageInput").addEventListener("change", (event) => {
    const pageNumber = Number(event.target.value);
    const index = state.audit.filteredPages.findIndex((page) => page.page_number === pageNumber);
    if (index >= 0) selectAuditPageByIndex(index);
  });
  document.getElementById("auditZoomOutBtn").addEventListener("click", () => setAuditZoom(state.audit.scale / 1.15));
  document.getElementById("auditZoomInBtn").addEventListener("click", () => setAuditZoom(state.audit.scale * 1.15));
  document.getElementById("auditFitBtn").addEventListener("click", fitAuditPage);
  for (const button of document.querySelectorAll("#auditInspectorTabs [data-audit-panel]")) {
    button.addEventListener("click", () => selectAuditInspectorPanel(button.dataset.auditPanel));
  }
  document.getElementById("auditApprovePageBtn").addEventListener("click", () => markAuditSessionDecision(state.audit.stage === "pre_h_ps1" ? "conforme_solo_sesion" : "aprobación visual pendiente de aplicar"));
  document.getElementById("auditCorrectBoxBtn").addEventListener("click", () => markAuditSessionDecision(state.audit.stage === "pre_h_ps1" ? "relacion_incorrecta_solo_sesion" : "corrección solicitada al aplicador H-PS2"));
  document.getElementById("auditAbstainBtn").addEventListener("click", () => markAuditSessionDecision(state.audit.stage === "pre_h_ps1" ? "relacion_dudosa_solo_sesion" : "abstención conservada"));
  document.getElementById("auditReturnStructureBtn").addEventListener("click", () => markAuditSessionDecision("structure_mismatch_requires_gottfried"));
  window.addEventListener("resize", () => {
    if (state.view === "library-audit" && state.audit.filteredPages[state.audit.pageIndex]) fitAuditPage();
  });
}

bindEvents();
bindAuditEvents();
setActiveView("dataset");
loadDatasets()
  .then(() => loadDataset())
  .catch((err) => log(`Error cargando dataset: ${err.message}`));
