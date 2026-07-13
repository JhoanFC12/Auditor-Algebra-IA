const state = {
  dataset: null,
  samples: [],
  filtered: [],
  currentIndex: -1,
  sample: null,
  image: null,
  boxes: [],
  selectedId: null,
  activeClass: 0,
  drawMode: false,
  dragging: null,
  scale: 1,
  loadSeq: 0,
  saveSeq: 0,
  loadingSampleId: null,
  datasets: [],
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

const INITIAL_ZOOM = 0.5;
const MIN_ZOOM = 0.1;
const MAX_ZOOM = 3;
const ZOOM_STEP = 1.15;

const canvas = document.getElementById("labelCanvas");
const ctx = canvas.getContext("2d");
const shell = document.querySelector(".canvas-shell");

function log(message) {
  const statusLog = document.getElementById("statusLog");
  statusLog.textContent = `${new Date().toLocaleTimeString()}  ${message}\n${statusLog.textContent}`.slice(0, 5000);
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
  state.boxes = [];
  state.selectedId = null;
  state.dragging = null;
  state.loadingSampleId = null;
  canvas.width = 1;
  canvas.height = 1;
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
  draw();
  renderBoxList();
  updateDimensionPanel();
  log(`Dimensiones actualizadas: ${classLabels[box.cls] || box.cls}`);
}

function updateStats() {
  const dataset = state.dataset || {};
  document.getElementById("totalCount").textContent = dataset.samples_total || 0;
  document.getElementById("reviewedCount").textContent = dataset.reviewed_total || 0;
  document.getElementById("pendingCount").textContent = dataset.pending_total || 0;
}

function shortDatasetName(name) {
  return String(name || "")
    .replace(/^problem_detector_multiclass_100_lab_/, "")
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
    return true;
  });
  renderSampleList();
}

function renderSampleList() {
  const list = document.getElementById("sampleList");
  list.innerHTML = "";
  for (const [index, sample] of state.filtered.entries()) {
    const button = document.createElement("button");
    button.className = `sample-card ${state.sample?.sample_id === sample.sample_id ? "active" : ""}`;
    button.innerHTML = `
      <span class="title">${index + 1}. ${sample.group} | pag. ${sample.page_number}</span>
      <span class="line">${sample.instance || sample.sample_id}</span>
      <span class="badge ${sample.reviewed ? "reviewed" : "pending"}">${sample.reviewed ? "Revisada" : "Pendiente"}</span>
    `;
    button.addEventListener("click", () => loadSampleByFilteredIndex(index));
    list.appendChild(button);
  }
}

function renderBoxList() {
  const list = document.getElementById("boxList");
  list.innerHTML = "";
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

function renderCurrentInfo() {
  const sample = state.sample;
  document.getElementById("sampleTitle").textContent = sample ? sample.sample_id : "Sin muestra";
  document.getElementById("sampleMeta").textContent = sample
    ? `${sample.width} x ${sample.height} | problemas ${classCount(0)} | numeros ${classCount(1)} | alternativas ${classCount(2)}`
    : "";
  document.getElementById("zoomLabel").textContent = `${Math.round(state.scale * 100)}%`;
}

function fitToView() {
  if (!state.sample) return;
  const availableW = Math.max(320, shell.clientWidth - 40);
  const availableH = Math.max(280, shell.clientHeight - 40);
  state.scale = Math.min(1.15, Math.max(0.15, Math.min(availableW / state.sample.width, availableH / state.sample.height)));
  draw();
}

function setInitialZoom() {
  state.scale = INITIAL_ZOOM;
  draw();
}

function clampZoom(value) {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, value));
}

function setZoom(nextScale, anchorEvent = null) {
  if (!state.sample || !state.image) return;
  const previousScale = state.scale;
  const targetScale = clampZoom(nextScale);
  if (Math.abs(targetScale - previousScale) < 0.0001) return;

  let anchor = null;
  if (anchorEvent) {
    const shellRect = shell.getBoundingClientRect();
    anchor = {
      imagePoint: canvasPoint(anchorEvent),
      viewportX: anchorEvent.clientX - shellRect.left,
      viewportY: anchorEvent.clientY - shellRect.top,
    };
  }

  state.scale = targetScale;
  draw();

  if (anchor) {
    const shellRect = shell.getBoundingClientRect();
    const canvasRect = canvas.getBoundingClientRect();
    const canvasLeft = canvasRect.left - shellRect.left + shell.scrollLeft;
    const canvasTop = canvasRect.top - shellRect.top + shell.scrollTop;
    shell.scrollLeft = canvasLeft + anchor.imagePoint.x * state.scale - anchor.viewportX;
    shell.scrollTop = canvasTop + anchor.imagePoint.y * state.scale - anchor.viewportY;
  }
}

function onCanvasWheel(event) {
  if (!event.ctrlKey || !state.sample || !state.image) return;
  event.preventDefault();
  const factor = event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
  setZoom(state.scale * factor, event);
}

function draw() {
  if (!state.sample || !state.image) return;
  canvas.width = Math.max(1, Math.round(state.sample.width * state.scale));
  canvas.height = Math.max(1, Math.round(state.sample.height * state.scale));
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(state.image, 0, 0, canvas.width, canvas.height);
  ctx.lineWidth = Math.max(2, 3 * state.scale);
  ctx.font = `${Math.max(11, 13 * state.scale)}px Segoe UI`;
  for (const [index, rawBox] of state.boxes.entries()) {
    const box = normalizeBox(rawBox);
    const color = colors[box.cls] || "#999";
    const x = box.x1 * state.scale;
    const y = box.y1 * state.scale;
    const w = (box.x2 - box.x1) * state.scale;
    const h = (box.y2 - box.y1) * state.scale;
    ctx.strokeStyle = color;
    ctx.fillStyle = `${color}22`;
    ctx.fillRect(x, y, w, h);
    ctx.strokeRect(x, y, w, h);
    ctx.fillStyle = color;
    ctx.fillRect(x, Math.max(0, y - 18), Math.min(180, 74 + String(index + 1).length * 8), 18);
    ctx.fillStyle = "#fff";
    ctx.fillText(`${index + 1} ${classLabels[box.cls] || box.cls}`, x + 5, Math.max(13, y - 5));
    if (box.id === state.selectedId) drawHandles(box);
  }
  renderCurrentInfo();
  updateDimensionPanel();
}

function drawHandles(box) {
  const points = [
    [box.x1, box.y1],
    [box.x2, box.y1],
    [box.x2, box.y2],
    [box.x1, box.y2],
  ];
  ctx.fillStyle = "#fff";
  ctx.strokeStyle = "#00111f";
  for (const [x, y] of points) {
    const sx = x * state.scale;
    const sy = y * state.scale;
    ctx.fillRect(sx - 5, sy - 5, 10, 10);
    ctx.strokeRect(sx - 5, sy - 5, 10, 10);
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
  if (state.filtered.length && !state.sample) await loadSampleByFilteredIndex(0);
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
    applyFilters();
    if (state.filtered.length) await loadSampleByFilteredIndex(0);
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
  state.loadingSampleId = sample.sample_id;
  log(`Cargando muestra: ${sample.sample_id}`);
  let data;
  try {
    data = await apiGet(`/api/sample?id=${encodeURIComponent(sample.sample_id)}`);
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
    state.boxes = (data.boxes || []).map((box) => ({ ...box, id: box.id || boxId() }));
    state.selectedId = null;
    state.dragging = null;
    state.loadingSampleId = null;
    setInitialZoom();
    renderSampleList();
    renderBoxList();
    updateDimensionPanel();
    log(`Muestra cargada: ${sample.sample_id}`);
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
  const payload = {
    sample_id: activeSampleId,
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
    const newIndex = state.filtered.findIndex((sample) => sample.sample_id === data.sample_id);
    const stillViewingSameSample = state.sample?.sample_id === activeSampleId;
    if (stillViewingSameSample && saveSeq === state.saveSeq) {
      state.sample = data;
      state.boxes = (data.boxes || []).map((box) => ({ ...box, id: box.id || boxId() }));
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

function deleteSelected() {
  if (!state.selectedId) return;
  state.boxes = state.boxes.filter((box) => box.id !== state.selectedId);
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

function nextSample(delta) {
  if (!state.filtered.length) return;
  const next = Math.max(0, Math.min(state.filtered.length - 1, state.currentIndex + delta));
  loadSampleByFilteredIndex(next);
}

function bindEvents() {
  canvas.addEventListener("mousedown", onMouseDown);
  shell.addEventListener("wheel", onCanvasWheel, { passive: false });
  window.addEventListener("mousemove", onMouseMove);
  window.addEventListener("mouseup", onMouseUp);
  document.getElementById("saveBtn").addEventListener("click", () => saveCurrent().catch((err) => log(`Error guardando: ${err.message}`)));
  document.getElementById("switchDatasetBtn").addEventListener("click", () => switchDataset().catch((err) => log(`Error cambiando dataset: ${err.message}`)));
  document.getElementById("prevBtn").addEventListener("click", () => nextSample(-1));
  document.getElementById("nextBtn").addEventListener("click", () => nextSample(1));
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
    if (event.key === "Delete") deleteSelected();
  });
}

bindEvents();
loadDatasets()
  .then(() => loadDataset())
  .catch((err) => log(`Error cargando dataset: ${err.message}`));
