const STAGES = [
  { id: "pages", title: "Paginas", action: "Elegir paginas del PDF" },
  { id: "boxes", title: "Boxes", action: "Confirmar cajas de problemas" },
  { id: "crops", title: "Staging", action: "Revisar crops trazables" },
  { id: "ocr", title: "OCR", action: "Leer texto y graficos" },
  { id: "review", title: "Revision", action: "Preparar revision desde OCR" },
  { id: "candidate", title: "Candidato", action: "Verificar bloqueo de BD" },
];

const state = {
  view: "boot",
  snapshot: null,
  currentInstance: null,
  library: {
    databases: [],
    selectedDb: "",
    books: [],
    details: {},
    selectedBookId: "",
    selectedInstanceId: "",
    screen: "books",
    query: "",
    status: "all",
    error: "",
    loading: false,
    showBookForm: false,
    showInstanceForm: false,
  },
  stage: "pages",
  pdfPage: 1,
  selectedPages: new Set(),
  selectedPageRecordId: "",
  selectedRecordId: "",
  ocrQueueIds: new Set(),
  ocrJobId: "",
  selectedOcrIndex: 0,
  boxes: [],
  boxMode: "select",
  selectedBox: -1,
  drag: null,
  boxDirty: false,
  boxZoom: 1,
  figureSegments: [],
  selectedFigureSegment: -1,
  figureSegmentMode: "select",
  figureSegmentDirty: false,
  figureDrag: null,
  reviewDraft: null,
  batchMode: "",
  batchText: "",
  batchResults: [],
  ocrEndpoint: null,
  ocrEndpointLoading: false,
  ocrJobPolling: false,
  normalizerTraining: null,
  taskProgress: null,
};

const $ = (id) => document.getElementById(id);
const THEME_STORAGE_KEY = "pdfFactoryTheme";
const FACTORY_UI_STORAGE_PREFIX = "pdfFactoryUiState:v1";
const BOX_ZOOM_MIN = 0.35;
const BOX_ZOOM_MAX = 8;
const BOX_SCALE_MAX = 2;

const sleep = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

function currentTheme() {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

function applyTheme(theme, { persist = true } = {}) {
  const next = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = next;
  if (persist) {
    try { localStorage.setItem(THEME_STORAGE_KEY, next); } catch (_) {}
  }
  syncThemeToggle();
}

function toggleTheme() {
  applyTheme(currentTheme() === "dark" ? "light" : "dark");
}

function syncThemeToggle() {
  const btn = $("themeToggle");
  if (!btn) return;
  const dark = currentTheme() === "dark";
  btn.textContent = dark ? "Modo claro" : "Modo oscuro";
  btn.title = dark ? "Cambiar a modo claro" : "Cambiar a modo oscuro";
  btn.setAttribute("aria-pressed", dark ? "true" : "false");
}

async function api(path, options = {}) {
  const init = { ...options };
  const isFormData = typeof FormData !== "undefined" && init.body instanceof FormData;
  if (init.body && typeof init.body !== "string" && !isFormData) {
    init.body = JSON.stringify(init.body);
    init.headers = { "Content-Type": "application/json", ...(init.headers || {}) };
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (err) {
    throw new Error("No se pudo conectar con el servidor local de Fabrica. Cierra esta ventana y vuelve a abrir el acceso directo para reiniciar la Fabrica.");
  }
  const text = await response.text();
  let payload = {};
  try { payload = text ? JSON.parse(text) : {}; } catch (_) { payload = { raw: text }; }
  if (!response.ok) {
    throw new Error(payload.error || payload.raw || response.statusText);
  }
  return payload;
}

function applyFactorySnapshot(payload) {
  const snapshot = payload?.snapshot || payload;
  if (snapshot && typeof snapshot === "object" && snapshot.schema_version === "pdf_factory_web_snapshot_v1") {
    state.snapshot = snapshot;
  }
  return state.snapshot;
}

async function refresh(message = "") {
  if (state.view === "library") return loadLibrary(message || "Biblioteca actualizada.");
  setBusy("Actualizando...");
  applyFactorySnapshot(await loadFactorySnapshot());
  await refreshNormalizerTrainingStatus({ silent: true });
  restoreFactoryUiState({ preserveCurrentStage: true });
  render();
  setStatus(message || "Listo para trabajar.");
  resumeOcrJobIfRunning({ silent: true });
}

function render() {
  if (state.view === "library") {
    renderLibrary();
    return;
  }
  renderFactoryShell();
  const snap = state.snapshot;
  if (!snap) return;
  const labels = factoryHeaderLabels(snap);
  document.title = labels.browserTitle;
  $("title").textContent = labels.title;
  $("subtitle").textContent = labels.subtitle;
  renderTimeline();
  renderMetrics();
  renderTrainingNotice();
  renderStage();
}

function factoryHeaderLabels(snap) {
  const context = snap?.context || {};
  const pdf = snap?.pdf || {};
  const current = state.currentInstance || {};
  const book = current.book || {};
  const instanceName = String(
    current.title
    || current.name
    || current.tipo
    || current.instance_type
    || context.instance_type
    || context.instance_name
    || context.instance_id
    || "Instancia"
  ).trim();
  const bookName = String(
    book.title
    || book.titulo
    || current.book_title
    || current.book_name
    || context.project_name
    || context.book_code
    || context.book_id
    || "Libro"
  ).trim();
  const pdfName = String(pdf.name || context.pdf_name || "").trim();
  const dbName = String(context.db_name || "").trim();
  const title = instanceName && bookName
    ? `${instanceName} / ${bookName}`
    : (instanceName || bookName || "Fabrica PDF");
  const subtitleParts = [
    pdfName || "PDF",
    dbName ? `BD: ${dbName}` : "",
    "Staging revisable; sin insercion directa en problemas",
  ].filter(Boolean);
  return {
    title: compactText(title, 120),
    subtitle: subtitleParts.join(" | "),
    browserTitle: `${compactText(instanceName || bookName || "Fabrica PDF", 42)} | Fabrica PDF`,
  };
}

function renderFactoryShell() {
  $("workspace").classList.remove("library-mode");
  syncWorkspaceMode();
  const timelineCard = document.querySelector(".timeline-card");
  if (timelineCard && !$("timeline")) {
    timelineCard.innerHTML = `
      <div class="timeline-heading">
        <span class="section-label">Proceso</span>
        <strong>De PDF a staging</strong>
      </div>
      <nav class="timeline" id="timeline"></nav>
    `;
  }
  const inspector = document.querySelector(".inspector");
  if (inspector && !$("metrics")) {
    inspector.innerHTML = `
      <div class="panel">
        <h2>Resumen</h2>
        <div id="metrics" class="metrics"></div>
      </div>
      <div class="panel">
        <h2>Inspector</h2>
        <div id="inspector" class="inspector-body muted">Selecciona una etapa o problema para ver el contexto util.</div>
      </div>
    `;
  }
}

async function refreshNormalizerTrainingStatus({ silent = true } = {}) {
  if (state.view === "library") return null;
  try {
    state.normalizerTraining = await api("/api/training/normalizer/status");
    renderTrainingNotice();
    if (!silent && state.normalizerTraining?.ready_to_train) {
      setStatus(state.normalizerTraining.notification || "Dataset normalizador listo para entrenar.");
    }
    return state.normalizerTraining;
  } catch (err) {
    state.normalizerTraining = {
      schema_version: "normalizer_training_bank_status_v1",
      error: err.message || String(err),
      samples_total: 0,
      threshold: 200,
      ready_to_train: false,
    };
    renderTrainingNotice();
    return state.normalizerTraining;
  }
}

function renderTrainingNotice() {
  const host = $("trainingNotice");
  if (!host) return;
  const bank = state.normalizerTraining;
  if (!bank || bank.error) {
    host.innerHTML = bank?.error ? `<div class="training-notice warning">Base normalizador: ${escapeHtml(bank.error)}</div>` : "";
    return;
  }
  const total = Number(bank.samples_total || 0);
  const threshold = Number(bank.threshold || 200);
  const ready = Boolean(bank.ready_to_train || total >= threshold);
  const remaining = Math.max(0, threshold - total);
  host.innerHTML = `
    <div class="training-notice ${ready ? "ready" : ""}">
      <div>
        <strong>${ready ? "Normalizador listo para entrenar" : "Base normalizador"}</strong>
        <span>${ready
          ? `Ya hay ${total} muestra(s). Podemos entrenar la primera version.`
          : `${total}/${threshold} muestra(s). Faltan ${remaining}.`}</span>
      </div>
      <small>${escapeHtml(bank.samples_jsonl || bank.root || "")}</small>
    </div>
  `;
}

async function loadFactorySnapshot() {
  const instance = state.currentInstance || {};
  const id = instance.id || instance.instance_id || instance.code || "";
  if (id) {
    try {
      return await api(`/api/library/instances/${encodeURIComponent(id)}/bootstrap`);
    } catch (err) {
      setStatus(`No encontre bootstrap de biblioteca para la instancia; probando Fabrica directa. ${err.message}`);
    }
    try {
      return await api(`/api/bootstrap?instance_id=${encodeURIComponent(id)}`);
    } catch (_) {
      return api("/api/bootstrap");
    }
  }
  return api("/api/bootstrap");
}

function factoryUiStorageKey() {
  const snap = state.snapshot || {};
  const context = snap.context || {};
  const pdf = snap.pdf || {};
  const parts = [
    context.db_name || context.database || "",
    context.book_code || context.book_id || context.book || "",
    context.instance_type || context.instance_id || context.instance_name || "",
    pdf.path || pdf.name || "",
  ].map((part) => String(part || "").trim());
  return `${FACTORY_UI_STORAGE_PREFIX}:${parts.join("|")}`;
}

function loadPersistedFactoryUiState() {
  if (!state.snapshot) return {};
  try {
    const raw = localStorage.getItem(factoryUiStorageKey());
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (_) {
    return {};
  }
}

function persistFactoryUiState() {
  if (!state.snapshot || state.view !== "factory") return;
  try {
    localStorage.setItem(factoryUiStorageKey(), JSON.stringify({
      stage: state.stage,
      pdfPage: state.pdfPage,
      selectedPages: [...state.selectedPages].sort((a, b) => a - b),
      selectedPageRecordId: state.selectedPageRecordId,
      selectedRecordId: state.selectedRecordId,
      ocrQueueIds: [...state.ocrQueueIds].sort(),
      ocrJobId: state.ocrJobId,
      selectedOcrIndex: state.selectedOcrIndex,
      savedAt: new Date().toISOString(),
    }));
  } catch (_) {}
}

function restoreFactoryUiState({ preserveCurrentStage = false } = {}) {
  if (!state.snapshot) return;
  const persisted = loadPersistedFactoryUiState();
  const pageCount = Number(state.snapshot.pdf?.page_count || 0);
  const pages = factoryPages();
  const records = state.snapshot.records || [];
  const validRecordIds = new Set(records.map((record) => String(record.record_id || "")));
  const persistedQueueIds = Array.isArray(persisted.ocrQueueIds) ? persisted.ocrQueueIds : [];
  state.ocrQueueIds = new Set(persistedQueueIds.map((id) => String(id || "")).filter((id) => validRecordIds.has(id)));
  state.ocrJobId = String(persisted.ocrJobId || state.ocrJobId || "").trim();
  const detectedPages = detectedPageNumbers(pages);
  const persistedPages = sortedPageNumbers(persisted.selectedPages || []).filter((page) => !pageCount || page <= pageCount);
  const selectedPages = persistedPages.length ? persistedPages : detectedPages;
  state.selectedPages = new Set(selectedPages);
  if (!state.selectedPages.size && pageCount) {
    state.selectedPages.add(boundPageNumber(persisted.pdfPage || state.pdfPage || 1, pageCount));
  }

  const firstSelectedPage = [...state.selectedPages].sort((a, b) => a - b)[0] || 1;
  state.pdfPage = boundPageNumber(persisted.pdfPage || firstSelectedPage, pageCount || Math.max(1, firstSelectedPage));

  const persistedPageRecordId = String(persisted.selectedPageRecordId || state.selectedPageRecordId || "");
  const matchingPage = pages.find((page) => page.record_id === persistedPageRecordId)
    || pages.find((page) => Number(page.page_number || 0) === state.pdfPage)
    || pages[0];
  state.selectedPageRecordId = matchingPage?.record_id || "";
  if (matchingPage) state.pdfPage = Number(matchingPage.page_number || state.pdfPage || 1);

  const persistedRecordId = String(persisted.selectedRecordId || state.selectedRecordId || "");
  const matchingRecord = records.find((record) => record.record_id === persistedRecordId)
    || records.find((record) => recordPageNumber(record) === state.pdfPage)
    || records[0];
  state.selectedRecordId = matchingRecord?.record_id || "";
  state.selectedOcrIndex = Math.max(0, Number(persisted.selectedOcrIndex || state.selectedOcrIndex || 0) || 0);
  const currentStage = normalizeUiStage(state.stage);
  const persistedStage = normalizeUiStage(persisted.stage);
  const inferredStage = inferStageFromSnapshot();
  state.stage = resolveRestoredStage({
    currentStage,
    persistedStage,
    inferredStage,
    preserveCurrentStage,
  });
  persistFactoryUiState();
}

function inferStageFromSnapshot() {
  const summary = state.snapshot?.summary || {};
  const records = state.snapshot?.records || [];
  const pages = factoryPages();
  const recordsTotal = Number(summary.records_total || records.length || 0);
  const normalizedDone = Number(summary.normalized_done || 0);
  const ocrDone = Number(summary.ocr_done || 0);
  const segmentsDone = Number(summary.segments_done || 0);
  if (normalizedDone > 0 || records.some((record) => hasObjectData(record.normalized))) return "review";
  if (ocrDone > 0 || segmentsDone > 0 || records.some((record) => hasObjectData(record.structured_ocr) || hasText(record.raw_ocr) || hasObjectData(record.figure_segmentation))) return "ocr";
  if (recordsTotal > 0) return "crops";
  if (pages.length || Number(summary.pages_total || 0) > 0) return "boxes";
  return "pages";
}

function detectedPageNumbers(pages = factoryPages()) {
  return sortedPageNumbers((pages || []).map((page) => page.page_number));
}

function sortedPageNumbers(values) {
  const seen = new Set();
  (values || []).forEach((value) => {
    const page = Number(value);
    if (Number.isFinite(page) && page > 0) seen.add(Math.floor(page));
  });
  return [...seen].sort((a, b) => a - b);
}

function boundPageNumber(page, count) {
  const max = Math.max(1, Number(count || 1));
  const next = Number(page || 1);
  return Math.max(1, Math.min(max, Number.isFinite(next) ? Math.floor(next) : 1));
}

function normalizeUiStage(stage) {
  const value = String(stage || "");
  return STAGES.some((row) => row.id === value) ? value : "";
}

function resolveRestoredStage({ currentStage, persistedStage, inferredStage, preserveCurrentStage }) {
  const candidates = [
    preserveCurrentStage ? currentStage : "",
    persistedStage,
    inferredStage,
    "pages",
  ].filter(Boolean);
  let next = candidates[0] || "pages";
  if (shouldPreferOcrOverReview(next)) next = "ocr";
  return next;
}

function shouldPreferOcrOverReview(stage) {
  if (stage !== "review") return false;
  const summary = state.snapshot?.summary || {};
  const records = state.snapshot?.records || [];
  const recordsTotal = Number(summary.records_total || records.length || 0);
  const normalizedDone = Number(summary.normalized_done || 0);
  if (!recordsTotal || normalizedDone >= recordsTotal) return false;
  const ocrDone = Number(summary.ocr_done || 0);
  const ready = Number(summary.ready || 0);
  const errors = Number(summary.errors || 0);
  const hasOcrData = ocrDone > 0 || records.some((record) => hasText(record.raw_ocr) || hasObjectData(record.structured_ocr));
  if (!hasOcrData) return false;
  return errors > 0 || ready === 0 || ocrDone < recordsTotal;
}

function recordPageNumber(record) {
  const source = record?.source || {};
  const page = Number(source.page_number || source.source_page_number || record?.page_number || 0);
  return Number.isFinite(page) ? page : 0;
}

function hasObjectData(value) {
  return Boolean(value && typeof value === "object" && Object.keys(value).length);
}

function hasText(value) {
  return String(value || "").trim().length > 0;
}

function recordSourceStale(record) {
  return Boolean(record?.source_stale || record?.source_state === "stale");
}

function recordDownstreamInvalidated(record) {
  return Boolean(record?.downstream_invalidated || record?.downstream_state?.status === "invalidated");
}

function recordCanRunOcr(record) {
  return Boolean(record && !recordSourceStale(record) && record.crop_url);
}

function invalidatedRecords(records = state.snapshot?.records || []) {
  return (records || []).filter((record) => recordSourceStale(record) || recordDownstreamInvalidated(record));
}

function isTaskRunning(type = "") {
  const progress = state.taskProgress || {};
  if (!progress.running) return false;
  return type ? progress.type === type : true;
}

function renderTaskProgress(type = "") {
  const progress = state.taskProgress || {};
  if (!progress.running || (type && progress.type !== type)) return "";
  const total = Math.max(1, Number(progress.total || 1));
  const current = Math.max(0, Math.min(total, Number(progress.current || 0)));
  const percent = Math.round((current / total) * 100);
  const failed = Number(progress.failed || 0);
  const ok = Number(progress.ok || 0);
  return `
    <div class="task-progress panel" role="status" aria-live="polite">
      <div class="task-progress-main">
        <span class="section-label">${escapeHtml(progress.label || "Proceso")}</span>
        <strong>${escapeHtml(progress.message || `${current} de ${total}`)}</strong>
        <span class="muted">${escapeHtml(progress.activeName || progress.activeId || "Preparando siguiente item...")}</span>
      </div>
      <div class="task-progress-meter" aria-hidden="true"><span style="width:${percent}%"></span></div>
      <div class="task-progress-counts">
        <span>${current}/${total}</span>
        <span>${ok} listo(s)</span>
        ${failed ? `<span class="task-progress-error">${failed} con error</span>` : ""}
      </div>
    </div>
  `;
}

function findRecordById(recordId, records = state.snapshot?.records || []) {
  const id = String(recordId || "");
  return (records || []).find((record) => String(record.record_id || "") === id) || null;
}

function recordLabelById(recordId) {
  const records = state.snapshot?.records || [];
  const record = findRecordById(recordId, records);
  if (!record) return String(recordId || "");
  const index = records.findIndex((row) => String(row.record_id || "") === String(recordId || ""));
  return recordOptionLabel(record, Math.max(0, index));
}

function recordErrorComment(record) {
  if (!record) return "";
  const direct = (record.errors || [])
    .map((item) => String(item || "").trim())
    .find(Boolean);
  if (direct) return direct;
  const steps = record.steps || {};
  for (const key of Object.keys(steps)) {
    const step = steps[key] || {};
    if (normalizeStatus(step.status || "") !== "error") continue;
    const detail = String(step.detail || step.message || "").trim();
    return detail || `Error en ${key}`;
  }
  return "";
}

function inferredStartNumberForRecord(record) {
  const source = record?.source || {};
  const candidates = [
    record?.normalized?.numero,
    source.problem_number,
    source.n,
    source.problem_index,
    source.source_order,
    source.box_index,
  ];
  for (const value of candidates) {
    const number = Number.parseInt(String(value || "").replace(/[^\d]/g, ""), 10);
    if (Number.isFinite(number) && number > 0) return number;
  }
  const records = state.snapshot?.records || [];
  const index = record ? records.findIndex((row) => row.record_id === record.record_id) : -1;
  return index >= 0 ? index + 1 : 1;
}

async function loadLibrary(message = "") {
  state.library.loading = true;
  state.library.error = "";
  setBusy("Cargando biblioteca...");
  try {
    if (!state.library.selectedDb) {
      const dbPayload = await api("/api/library/databases");
      state.library.databases = dbPayload.databases || [];
      state.library.selectedDb = dbPayload.selected_db || state.library.databases[0] || "";
    }
    if (!state.library.selectedDb) throw new Error("No hay base de datos disponible.");
    const payload = await api(`/api/library/books?db_name=${encodeURIComponent(state.library.selectedDb)}`);
    state.library.books = normalizeLibraryBooks(payload);
    ensureLibrarySelection();
    renderLibrary();
    setStatus(message || "Biblioteca lista.");
    queueBookDetailLoad(state.library.selectedBookId);
  } catch (err) {
    state.library.error = friendlyLibraryError(err);
    renderLibrary();
    setStatus("Biblioteca lista para conectar con /api/library/books.");
  } finally {
    state.library.loading = false;
    $("busyText").textContent = "";
  }
}

function normalizeLibraryBooks(payload) {
  const rawBooks = Array.isArray(payload) ? payload : (payload.books || payload.items || []);
  return rawBooks.map((book, index) => {
    const id = String(book.id || book.book_id || book.code || book.book_code || `book_${index + 1}`);
    const instances = naturalSortInstances((book.instances || book.instance_list || []).map((instance, instanceIndex) => ({
      ...instance,
      id: String(instance.id || instance.instance_id || instance.code || `${id}_instance_${instanceIndex + 1}`),
      title: instance.title || instance.name || instance.tipo || instance.instance_type || `Instancia ${instanceIndex + 1}`,
      status: normalizeStatus(instance.status || instance.review_status || inferInstanceStatus(instance)),
      summary: instance.summary || instance.metrics || instance.indicators || {},
    })));
    return {
      ...book,
      id,
      title: book.title || book.titulo || book.name || book.nombre || id,
      code: book.code || book.codigo || book.book_code || id,
      author: book.author || book.autor || "",
      subject: book.subject || book.curso || "",
      pdfName: book.pdf_name || book.pdfName || book.filename || "",
      coverPath: book.cover_path || book.coverPath || "",
      coverUrl: book.cover_url || book.coverUrl || "",
      instances,
    };
  });
}

function queueBookDetailLoad(bookId) {
  const id = String(bookId || "");
  if (!id || state.library.details[id]?.loaded || state.library.details[id]?.loading) return;
  state.library.details[id] = { loading: true };
  loadBookDetail(id).catch((err) => {
    state.library.details[id] = { loaded: false, loading: false, error: err.message };
    if (state.view === "library") renderLibraryContent();
  });
}

async function loadBookDetail(bookId) {
  const id = String(bookId || "");
  if (!id || !state.library.selectedDb) return;
  const detail = await api(`/api/library/books/${encodeURIComponent(id)}?db_name=${encodeURIComponent(state.library.selectedDb)}`);
  state.library.details[id] = { ...detail, loaded: true, loading: false };
  const book = (state.library.books || []).find((row) => row.id === id);
  if (book) {
    const normalized = normalizeLibraryBooks({ books: [{ ...book, instances: detail.instances || [] }] })[0];
    book.instances = normalized.instances || [];
    book.dashboard = detail.dashboard || {};
  }
  if (state.view === "library") {
    ensureLibrarySelection();
    renderLibraryContent();
  }
}

function ensureLibrarySelection() {
  const books = filteredBooks(false);
  if (!books.some((book) => book.id === state.library.selectedBookId)) {
    state.library.selectedBookId = books[0]?.id || "";
  }
  const book = selectedLibraryBook();
  if (!book || !book.instances.some((item) => item.id === state.library.selectedInstanceId)) {
    state.library.selectedInstanceId = book?.instances[0]?.id || "";
  }
}

function renderLibrary() {
  document.title = "Biblioteca | Fabrica PDF";
  $("workspace").classList.remove("ocr-focus-mode");
  $("workspace").classList.add("library-mode");
  $("title").textContent = "Biblioteca";
  $("subtitle").textContent = "Registra libros, divide instancias y abre la Fabrica por tramo de trabajo.";
  document.querySelector(".timeline-card").innerHTML = renderLibraryFilters();
  bindLibrarySidebarEvents();
  renderLibraryContent();
}

function renderLibraryContent() {
  document.querySelector(".inspector").innerHTML = renderLibraryInspector();
  $("stageHost").innerHTML = renderLibraryStage();
  bindLibraryContentEvents();
  syncLibraryBottomAction();
}

function renderLibraryFilters() {
  const counts = libraryCounts();
  return `
    <div class="library-sidebar">
      <div class="timeline-heading">
        <span class="section-label">Biblioteca</span>
        <strong>${counts.books} libro(s)</strong>
      </div>
      <label class="library-search">
        <span class="muted">Base de datos</span>
        <select id="libraryDb">
          ${(state.library.databases || []).map((db) => `<option value="${escapeAttr(db)}" ${db === state.library.selectedDb ? "selected" : ""}>${escapeHtml(db)}</option>`).join("")}
        </select>
      </label>
      <label class="library-search">
        <span class="muted">Buscar</span>
        <input id="librarySearch" value="${escapeAttr(state.library.query)}" placeholder="Titulo, codigo, curso" />
      </label>
      <div class="filter-stack" aria-label="Filtros por estado">
        ${[
          ["all", "Todos", counts.instances],
          ["pendiente", "Pendientes", counts.pendiente],
          ["procesando", "En progreso", counts.procesando],
          ["requiere_revision", "Por revisar", counts.requiere_revision],
          ["listo", "Revisadas", counts.listo],
        ].map(([value, label, total]) => `
          <button class="filter-chip ${state.library.status === value ? "active" : ""}" data-library-status="${value}" type="button">
            <span>${label}</span><strong>${total}</strong>
          </button>
        `).join("")}
      </div>
      <button id="newBookBtn" class="primary wide-action" type="button">Registrar libro</button>
      <button id="openFactoryDirectBtn" class="ghost wide-action" type="button">Abrir Fabrica sin biblioteca</button>
      ${state.library.error ? `<div class="library-notice">${escapeHtml(state.library.error)}</div>` : ""}
    </div>
  `;
}

function renderLibraryStage() {
  if (state.library.screen === "book") return renderLibraryBookStage();
  return renderLibraryBooksStage();
}

function renderLibraryBooksStage() {
  const books = filteredBooks();
  return `
    <div class="stage-header library-header">
      <div>
        <h2>Biblioteca de libros</h2>
        <p class="muted">Explora las portadas y metadatos. Selecciona un libro para ver sus instancias en una pagina dedicada.</p>
      </div>
      <button id="showBookFormBtn" class="secondary" type="button">Registrar libro</button>
    </div>
    ${state.library.showBookForm ? renderBookForm() : ""}
    <div class="library-books-page">
      <section class="library-books library-books-grid" aria-label="Libros disponibles">
        ${books.length ? books.map(bookCardHtml).join("") : renderLibraryEmptyState()}
      </section>
    </div>
  `;
}

function renderLibraryBookStage() {
  const selected = selectedLibraryBook();
  if (!selected) {
    state.library.screen = "books";
    return renderLibraryBooksStage();
  }
  return `
    <div class="stage-header library-header">
      <div>
        <button id="backToBooksBtn" class="ghost compact-action" type="button">Volver a libros</button>
        <h2>Instancias del libro</h2>
        <p class="muted">Revisa el avance por tramo y abre la Fabrica PDF solo para la instancia que quieras trabajar.</p>
      </div>
      <button id="createInstanceBtn" class="secondary" type="button">Crear instancia</button>
    </div>
    ${state.library.showInstanceForm ? renderInstanceForm(selected) : ""}
    <section class="library-detail library-detail-page" aria-label="Detalle de instancias">
      ${renderBookDetail(selected)}
    </section>
  `;
}

function renderBookForm() {
  return `
    <form id="bookForm" class="panel form-grid library-form">
      ${field("bookCode", "Codigo", "")}
      ${field("bookTitle", "Titulo", "")}
      ${field("bookAuthor", "Autor", "")}
      ${field("bookEditorial", "Editorial", "")}
      ${field("bookEdition", "Edicion", "")}
      ${field("bookSubject", "Curso/area", "")}
      <label class="wide"><span class="muted">Ruta del PDF</span><input id="bookPdfPath" placeholder="E:\\Libros\\algebra.pdf" /></label>
      <label class="wide"><span class="muted">Carpeta de trabajo</span><input id="bookWorkspace" placeholder="Opcional; si queda vacia se genera automaticamente" /></label>
      <label class="wide"><span class="muted">Portada o imagen</span><input id="bookCover" placeholder="Opcional" /></label>
      <div class="form-actions wide">
        <button type="button" id="cancelBookForm">Cancelar</button>
        <button type="submit" class="primary">Guardar libro</button>
      </div>
    </form>
  `;
}

function renderInstanceForm(book) {
  return `
    <form id="instanceForm" class="panel form-grid library-form">
      <label><span class="muted">Tipo</span><select id="instanceType">
        <option value="capitulo">Capitulo</option>
        <option value="rango">Rango de paginas</option>
        <option value="evaluacion">Evaluacion</option>
        <option value="personalizada">Personalizada</option>
      </select></label>
      ${field("instanceName", "Nombre", book ? `${book.code} - instancia ${(book.instances || []).length + 1}` : "")}
      ${field("instancePages", "Paginas", "")}
      ${field("instanceNotes", "Notas", "")}
      <div class="form-actions wide">
        <button type="button" id="cancelInstanceForm">Cancelar</button>
        <button type="submit" class="primary">Crear instancia</button>
      </div>
    </form>
  `;
}

function bookCardHtml(book) {
  const counts = statusCounts(book.instances || []);
  const isActive = book.id === state.library.selectedBookId;
  return `
    <button class="book-card ${isActive ? "active" : ""}" data-book="${book.id}" type="button">
      ${bookCoverHtml(book)}
      <span class="book-main">
        <strong>${escapeHtml(book.title)}</strong>
        <span>${escapeHtml([book.code, book.author, book.subject].filter(Boolean).join(" | ") || "Sin metadatos")}</span>
      </span>
      <span class="book-stats">
        ${miniStat("Pend.", counts.pendiente)}
        ${miniStat("Prog.", counts.procesando)}
        ${miniStat("Rev.", counts.listo)}
      </span>
      <span class="book-card-action">Ver instancias</span>
    </button>
  `;
}

function bookCoverHtml(book, variant = "") {
  const url = String(book.coverUrl || "").trim();
  const label = bookCoverLabel(book);
  return `
    <span class="book-cover ${variant ? `book-cover-${variant}` : ""} ${url ? "has-image" : ""}">
      ${url ? `<img src="${escapeAttr(url)}" alt="" loading="lazy" decoding="async" />` : `<span>${escapeHtml(label)}</span>`}
    </span>
  `;
}

function bookCoverLabel(book) {
  const source = String(book.code || book.title || "Libro").trim();
  const tokens = source.split(/[\s_-]+/).filter(Boolean);
  const initials = tokens.slice(0, 2).map((token) => token[0]).join("").toUpperCase();
  return initials || "LB";
}

function miniStat(label, value) {
  return `<span><b>${Number(value || 0)}</b>${label}</span>`;
}

function renderBookDetail(book) {
  const instances = filteredInstances(book.instances || []);
  return `
    <div class="panel book-detail-head">
      <div class="book-detail-title">
        ${bookCoverHtml(book, "large")}
        <div>
          <span class="section-label">${escapeHtml(book.code)}</span>
          <h3>${escapeHtml(book.title)}</h3>
          <p class="muted">${escapeHtml([book.author, book.subject, book.pdfName].filter(Boolean).join(" | ") || "Sin PDF asociado todavia.")}</p>
        </div>
      </div>
      <span class="status-pill status-${instances.length ? "listo" : "pendiente"}">${instances.length} instancia(s)</span>
    </div>
    <div class="instance-list">
      ${instances.length ? instances.map(instanceCardHtml).join("") : `<div class="panel muted">No hay instancias con este filtro. Crea una instancia o cambia el estado seleccionado.</div>`}
    </div>
  `;
}

function instanceCardHtml(instance) {
  const status = normalizeStatus(instance.status || "pendiente");
  const isActive = instance.id === state.library.selectedInstanceId;
  const totals = instance.summary || instance.metrics || {};
  return `
    <article class="instance-card ${isActive ? "active" : ""}" data-instance="${instance.id}">
      <button class="instance-select" data-instance-select="${instance.id}" type="button">
        <span>
          <strong>${escapeHtml(instance.title)}</strong>
          <small>${escapeHtml(instance.pages || instance.page_range || instance.range || "Paginas por definir")}</small>
        </span>
        <span class="status-pill status-${status}">${displayStatus(status)}</span>
      </button>
      <div class="instance-progress" aria-label="Indicadores de avance">
        ${progressMetric("Escaneados", totals.escaneados_sesion || totals.total || totals.pages_total || totals.pages || 0)}
        ${progressMetric("Meta", totals.total_esperado || totals.boxes_total || totals.boxes || 0)}
        ${progressMetric("Revisadas", totals.consistentes || totals.ready || totals.reviewed || 0)}
      </div>
      <div class="instance-actions">
        <button data-open-factory="${instance.id}" class="primary" type="button">Abrir Fabrica</button>
      </div>
    </article>
  `;
}

function progressMetric(label, value) {
  return `<span><b>${Number(value || 0)}</b>${label}</span>`;
}

function renderLibraryInspector() {
  const book = selectedLibraryBook();
  const instance = selectedLibraryInstance();
  const counts = libraryCounts();
  return `
    <div class="panel">
      <h2>Estados</h2>
      <div class="metrics">
        <div class="metric"><span class="metric-label">Libros</span><strong>${counts.books}</strong></div>
        <div class="metric"><span class="metric-label">Instancias</span><strong>${counts.instances}</strong></div>
        <div class="metric"><span class="metric-label">Pendientes</span><strong>${counts.pendiente}</strong></div>
        <div class="metric"><span class="metric-label">Revisadas</span><strong>${counts.listo}</strong></div>
      </div>
    </div>
    <div class="panel">
      <h2>Seleccion</h2>
      <div id="inspector" class="inspector-body">
        ${book ? `
          <div class="inspector-line"><strong>Libro</strong><span>${escapeHtml(book.title)}</span></div>
          <div class="inspector-line"><strong>Codigo</strong><span>${escapeHtml(book.code)}</span></div>
          <div class="inspector-line"><strong>Vista</strong><span>${state.library.screen === "book" ? "Instancias" : "Libros"}</span></div>
          ${state.library.screen === "book" ? `
            <div class="inspector-line"><strong>Instancia</strong><span>${escapeHtml(instance?.title || "Sin instancia seleccionada")}</span></div>
            <div class="inspector-line"><strong>Estado</strong><span>${escapeHtml(instance ? displayStatus(normalizeStatus(instance.status)) : "-")}</span></div>
          ` : ""}
        ` : `<span class="muted">Sin libro seleccionado.</span>`}
      </div>
    </div>
  `;
}

function renderLibraryEmptyState() {
  return `
    <div class="empty-state library-empty">
      <div>
        <strong>Aun no hay libros visibles</strong>
        <p>Registra un libro o conecta el endpoint /api/library/books para poblar esta vista.</p>
      </div>
    </div>
  `;
}

function bindLibrarySidebarEvents() {
  if ($("libraryDb")) {
    $("libraryDb").onchange = (event) => {
      state.library.selectedDb = event.target.value;
      state.library.details = {};
      state.library.selectedBookId = "";
      state.library.selectedInstanceId = "";
      state.library.screen = "books";
      loadLibrary("Base de datos cambiada.").catch((err) => setStatus(`Error de biblioteca: ${err.message}`));
    };
  }
  if ($("librarySearch")) $("librarySearch").oninput = (event) => {
    state.library.query = event.target.value;
    state.library.screen = "books";
    state.library.showInstanceForm = false;
    ensureLibrarySelection();
    renderLibraryContent();
    queueBookDetailLoad(state.library.selectedBookId);
  };
  document.querySelectorAll("[data-library-status]").forEach((btn) => {
    btn.onclick = () => {
      state.library.status = btn.dataset.libraryStatus;
      state.library.screen = "books";
      ensureLibrarySelection();
      renderLibrary();
    };
  });
  $("newBookBtn").onclick = () => {
    state.library.screen = "books";
    state.library.showBookForm = true;
    state.library.showInstanceForm = false;
    renderLibraryContent();
  };
  $("openFactoryDirectBtn").onclick = () => openFactoryForInstance("");
}

function bindLibraryContentEvents() {
  document.querySelectorAll("[data-book]").forEach((btn) => {
    btn.onclick = () => {
      state.library.selectedBookId = btn.dataset.book;
      const nextBook = selectedLibraryBook();
      state.library.selectedInstanceId = filteredInstances(nextBook?.instances || [])[0]?.id || nextBook?.instances?.[0]?.id || "";
      state.library.screen = "book";
      state.library.showBookForm = false;
      renderLibraryContent();
      queueBookDetailLoad(state.library.selectedBookId);
    };
  });
  document.querySelectorAll("[data-instance-select]").forEach((btn) => {
    btn.onclick = () => {
      state.library.selectedInstanceId = btn.dataset.instanceSelect;
      renderLibraryContent();
    };
  });
  document.querySelectorAll("[data-open-factory]").forEach((btn) => {
    btn.onclick = () => openFactoryForInstance(btn.dataset.openFactory);
  });
  if ($("showBookFormBtn")) $("showBookFormBtn").onclick = () => {
    state.library.screen = "books";
    state.library.showBookForm = true;
    state.library.showInstanceForm = false;
    renderLibraryContent();
  };
  if ($("backToBooksBtn")) $("backToBooksBtn").onclick = () => {
    state.library.screen = "books";
    state.library.showInstanceForm = false;
    renderLibraryContent();
  };
  if ($("createInstanceBtn")) $("createInstanceBtn").onclick = () => {
    state.library.screen = "book";
    state.library.showInstanceForm = true;
    state.library.showBookForm = false;
    renderLibraryContent();
  };
  $("bookForm")?.addEventListener("submit", submitBookForm);
  $("instanceForm")?.addEventListener("submit", submitInstanceForm);
  if ($("cancelBookForm")) $("cancelBookForm").onclick = () => { state.library.showBookForm = false; renderLibraryContent(); };
  if ($("cancelInstanceForm")) $("cancelInstanceForm").onclick = () => { state.library.showInstanceForm = false; renderLibraryContent(); };
}

function syncLibraryBottomAction() {
  const book = selectedLibraryBook();
  const instance = selectedLibraryInstance();
  $("actionHint").textContent = state.library.screen === "book"
    ? "Selecciona una instancia para abrir su flujo PDF revisable."
    : "Selecciona un libro para ver sus instancias.";
  $("primaryAction").textContent = state.library.screen === "book" && instance ? "Abrir Fabrica" : (book ? "Ver instancias" : "Registrar libro");
  $("primaryAction").onclick = () => {
    if (state.library.screen === "book" && instance) openFactoryForInstance(instance.id);
    else if (book) {
      state.library.screen = "book";
      state.library.showBookForm = false;
      renderLibraryContent();
      queueBookDetailLoad(book.id);
    }
    else {
      state.library.showBookForm = true;
      renderLibraryContent();
    }
  };
}

async function submitBookForm(event) {
  event.preventDefault();
  await runAction("Registrando libro...", async () => {
    await api("/api/library/books", {
      method: "POST",
      body: {
        db_name: state.library.selectedDb || "",
        title: $("bookTitle").value.trim(),
        code: $("bookCode").value.trim(),
        author: $("bookAuthor").value.trim(),
        editorial: $("bookEditorial").value.trim(),
        edition: $("bookEdition").value.trim(),
        subject: $("bookSubject").value.trim(),
        pdf_path: $("bookPdfPath").value.trim(),
        workspace_dir: $("bookWorkspace").value.trim(),
        cover_path: $("bookCover").value.trim(),
      },
    });
    state.library.showBookForm = false;
    state.library.screen = "books";
    await loadLibrary("Libro registrado.");
  }, "Libro registrado.");
}

async function submitInstanceForm(event) {
  event.preventDefault();
  const book = selectedLibraryBook();
  if (!book) return setStatus("Selecciona un libro antes de crear una instancia.");
  await runAction("Creando instancia...", async () => {
    await api(`/api/library/books/${encodeURIComponent(book.id)}/instances`, {
      method: "POST",
      body: {
        db_name: state.library.selectedDb || "",
        type: $("instanceType").value,
        name: $("instanceName").value.trim(),
        pages: $("instancePages").value.trim(),
        notes: $("instanceNotes").value.trim(),
      },
    });
    state.library.showInstanceForm = false;
    state.library.screen = "book";
    await loadLibrary("Instancia creada.");
  }, "Instancia creada.");
}

async function openFactoryForInstance(instanceId) {
  const instance = instanceId ? findLibraryInstance(instanceId) : null;
  if (!instance || !instance.book) {
    state.view = "factory";
    state.snapshot = null;
    state.stage = "pages";
    await refresh("Fabrica directa.");
    return;
  }
  state.library.screen = "book";
  state.library.selectedBookId = instance.book.id;
  state.library.selectedInstanceId = instance.id;
  state.library.showBookForm = false;
  state.library.showInstanceForm = false;
  renderLibraryContent();
  let popup = null;
  try {
    popup = window.open("about:blank", "_blank");
    if (popup) {
      popup.opener = null;
      popup.document.title = `Fabrica PDF - ${instance.title}`;
      popup.document.body.innerHTML = "<p style=\"font-family: system-ui; padding: 20px;\">Preparando Fabrica PDF...</p>";
    }
  } catch (_) {
    popup = null;
  }
  await runAction("Abriendo Fabrica...", async () => {
    let result;
    try {
      result = await api(`/api/library/instances/${encodeURIComponent(instance.id)}/factory`, {
        method: "POST",
        body: {
          db_name: state.library.selectedDb || "",
          book_id: instance.book.id,
          open: false,
        },
      });
    } catch (err) {
      try { if (popup && !popup.closed) popup.close(); } catch (_) {}
      throw err;
    }
    if (result.url) {
      if (popup && !popup.closed) {
        popup.location.href = result.url;
      } else {
        renderLibraryContent();
        return `Popup bloqueado. Abre la Fabrica en una nueva ventana: ${result.url}`;
      }
    }
    renderLibraryContent();
    return `Fabrica abierta en una ventana nueva para ${instance.title}.`;
  }, `Fabrica abierta para ${instance.title}.`);
}

function renderTimeline() {
  const timeline = $("timeline");
  const byName = Object.fromEntries((state.snapshot.timeline || []).map((row) => [normalizeStageName(row.stage), row]));
  timeline.innerHTML = STAGES.map((stage, idx) => {
    const row = byName[stage.id] || {};
    const status = normalizeStatus(row.status || "pendiente");
    const isActive = state.stage === stage.id;
    return `
      <button class="timeline-step ${isActive ? "active" : ""}" data-stage="${stage.id}" type="button">
        <span class="timeline-index">${idx + 1}</span>
        <span class="timeline-copy">
          <span class="timeline-title-row">
            <span class="timeline-title">${stage.title}</span>
            ${isActive ? `<span class="timeline-current">Ahora</span>` : ""}
          </span>
          <span class="timeline-detail">${escapeHtml(row.detail || stageHint(stage.id))}</span>
          <span class="timeline-action">${escapeHtml(stage.action)}</span>
          <span class="status-pill status-${status}">${displayStatus(status)}</span>
        </span>
      </button>
    `;
  }).join("");
  timeline.querySelectorAll("[data-stage]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.stage = btn.dataset.stage;
      closeBatchMode({ rerender: false });
      persistFactoryUiState();
      renderStage();
      renderTimeline();
    });
  });
}

function renderMetrics() {
  const s = state.snapshot.summary || {};
  const fields = [
    ["pages_total", "Paginas"],
    ["boxes_total", "Boxes"],
    ["records_total", "Problemas"],
    ["ocr_done", "OCR"],
    ["segments_done", "Segmentos"],
    ["normalized_done", "Borradores"],
    ["ready", "Listos"],
    ["errors", "Errores"],
  ];
  $("metrics").innerHTML = fields.map(([key, label]) => `
    <div class="metric ${key === "errors" && Number(s[key] || 0) ? "metric-warning" : ""}">
      <span class="metric-label">${label}</span>
      <strong>${Number(s[key] || 0)}</strong>
    </div>
  `).join("");
}

function activeModelPayload() {
  const models = state.snapshot?.models || {};
  return {
    pdf_detector: String(models.pdf_detector || models.stages?.pdf_detector?.model_id || ""),
    ocr: String(models.ocr || models.stages?.ocr?.model_id || ""),
    figure_segmenter: String(models.figure_segmenter || models.stages?.figure_segmenter?.model_id || ""),
  };
}

function modelStageInfo(stage) {
  const models = state.snapshot?.models || {};
  const stages = models.stages || {};
  const row = stages[stage] || {};
  const payload = activeModelPayload();
  return {
    label: {
      pdf_detector: "Segmentacion de problemas",
      ocr: "OCR entrenado",
      figure_segmenter: "Segmentacion de graficos",
    }[stage] || stage,
    model_id: row.model_id || payload[stage] || "",
    provider: row.provider || "",
    source: row.source || "",
    confidence: row.confidence,
    fallback: row.fallback || "",
  };
}

function renderModelStrip(stages) {
  const rows = (stages || []).map((stage) => modelStageInfo(stage)).filter((row) => row.model_id);
  if (!rows.length) return "";
  return `
    <div class="model-strip" aria-label="Modelos entrenados activos">
      ${rows.map((row) => `
        <div class="model-chip">
          <span>${escapeHtml(row.label)}</span>
          <strong title="${escapeAttr(row.model_id)}">${escapeHtml(compactModelName(row.model_id))}</strong>
          <small>${escapeHtml([row.provider, row.source].filter(Boolean).join(" | ") || "modelo activo")}</small>
        </div>
      `).join("")}
    </div>
  `;
}

function compactModelName(value) {
  const raw = String(value || "").replaceAll("\\", "/");
  if (!raw) return "-";
  const parts = raw.split("/").filter(Boolean);
  if (parts.length >= 2 && !raw.includes(":")) return parts.slice(-2).join("/");
  return compactText(parts.slice(-4).join("/") || raw, 58);
}

function endpointStatusLabel(status) {
  const raw = String(status || "consultando").trim();
  const normalized = raw.toLowerCase();
  return {
    running: "running",
    scaledtozero: "scaledToZero",
    paused: "paused",
    pending: "starting",
    initializing: "starting",
    updating: "starting",
    starting: "starting",
    error: "error",
    no_configurado: "no configurado",
    unknown: "desconocido",
  }[normalized] || raw;
}

function endpointStatusClass(status) {
  const normalized = String(status || "").trim().toLowerCase();
  if (normalized === "running") return "ready";
  if (normalized === "scaledtozero" || normalized === "paused") return "idle";
  if (["pending", "initializing", "updating", "starting"].includes(normalized)) return "starting";
  if (normalized === "error") return "error";
  return "unknown";
}

function renderOcrEndpointCard() {
  const endpoint = state.ocrEndpoint || {};
  const statusValue = state.ocrEndpointLoading ? "starting" : (endpoint.status || "unknown");
  const status = endpointStatusLabel(statusValue);
  const configured = endpoint.configured !== false;
  const message = state.ocrEndpointLoading
    ? "Consultando endpoint OCR..."
    : (endpoint.message || (configured ? "Endpoint OCR dedicado para el modelo entrenado." : "Configura HF_TRAINED_OCR_ENDPOINT_NAME o HF_TRAINED_OCR_BASE_URL."));
  return `
    <section id="ocrEndpointCard" class="panel endpoint-card">
      <div>
        <span class="section-label">Endpoint OCR</span>
        <strong>${escapeHtml(endpoint.name || "OCR entrenado")}</strong>
        <p class="muted">${escapeHtml(message)}</p>
      </div>
      <div class="endpoint-actions">
        <span class="endpoint-pill endpoint-${endpointStatusClass(statusValue)}">${escapeHtml(status)}</span>
        <button id="refreshOcrEndpoint" type="button">Actualizar estado</button>
        <button id="resumeOcrEndpoint" type="button" ${configured ? "" : "disabled"}>Encender OCR</button>
        <button id="scaleOcrEndpoint" type="button" ${configured ? "" : "disabled"}>Apagar OCR</button>
      </div>
    </section>
  `;
}

function updateOcrEndpointCard() {
  const host = $("ocrEndpointCard");
  if (!host) return;
  host.outerHTML = renderOcrEndpointCard();
  bindOcrEndpointActions();
}

function bindOcrEndpointActions() {
  const refreshBtn = $("refreshOcrEndpoint");
  if (refreshBtn) refreshBtn.onclick = () => refreshOcrEndpointStatus();
  const resumeBtn = $("resumeOcrEndpoint");
  if (resumeBtn) resumeBtn.onclick = () => resumeOcrEndpoint();
  const scaleBtn = $("scaleOcrEndpoint");
  if (scaleBtn) scaleBtn.onclick = () => scaleOcrEndpointToZero();
}

async function refreshOcrEndpointStatus({ silent = false } = {}) {
  state.ocrEndpointLoading = true;
  updateOcrEndpointCard();
  try {
    state.ocrEndpoint = await api("/api/endpoint/ocr/status");
    if (!silent) setStatus(state.ocrEndpoint.message || `Endpoint OCR: ${endpointStatusLabel(state.ocrEndpoint.status)}`);
    return state.ocrEndpoint;
  } catch (err) {
    state.ocrEndpoint = { status: "error", configured: true, message: err.message };
    if (!silent) setStatus(`Error endpoint OCR: ${err.message}`);
    return state.ocrEndpoint;
  } finally {
    state.ocrEndpointLoading = false;
    updateOcrEndpointCard();
  }
}

async function resumeOcrEndpoint({ silent = false } = {}) {
  state.ocrEndpointLoading = true;
  updateOcrEndpointCard();
  if (!silent) setBusy("Despertando endpoint OCR... puede tomar unos minutos.");
  try {
    state.ocrEndpoint = await api("/api/endpoint/ocr/resume", {
      method: "POST",
      body: { wait: true, timeout_s: 420, poll_s: 8 },
    });
    if (!silent) setStatus(state.ocrEndpoint.message || "Endpoint OCR encendido.");
    return state.ocrEndpoint;
  } catch (err) {
    state.ocrEndpoint = { status: "error", configured: true, message: err.message };
    if (!silent) setStatus(`Error endpoint OCR: ${err.message}`);
    throw err;
  } finally {
    state.ocrEndpointLoading = false;
    updateOcrEndpointCard();
  }
}

async function scaleOcrEndpointToZero({ silent = false } = {}) {
  state.ocrEndpointLoading = true;
  updateOcrEndpointCard();
  try {
    state.ocrEndpoint = await api("/api/endpoint/ocr/scale-to-zero", { method: "POST", body: {} });
    if (!silent) setStatus(state.ocrEndpoint.message || "Endpoint OCR apagado para ahorro.");
    return state.ocrEndpoint;
  } catch (err) {
    state.ocrEndpoint = { status: "error", configured: true, message: err.message };
    if (!silent) setStatus(`Error apagando endpoint OCR: ${err.message}`);
    throw err;
  } finally {
    state.ocrEndpointLoading = false;
    updateOcrEndpointCard();
  }
}

async function prepareOcrEndpointForRun() {
  const status = await refreshOcrEndpointStatus({ silent: true });
  if (!status.configured) {
    throw new Error(status.message || "Endpoint OCR no configurado. Define HF_TRAINED_OCR_ENDPOINT_NAME o HF_TRAINED_OCR_BASE_URL.");
  }
  setStatus("Despertando endpoint OCR... puede tomar unos minutos.");
  return resumeOcrEndpoint({ silent: true });
}

function renderStage() {
  if (state.batchMode) {
    renderBatchEditor();
    syncWorkspaceMode();
    syncPrimaryAction();
    return;
  }
  const renderers = {
    pages: renderPagesStage,
    boxes: renderBoxesStage,
    crops: renderCropsStage,
    ocr: renderOcrStage,
    review: renderReviewStage,
    candidate: renderCandidateStage,
  };
  (renderers[state.stage] || renderPagesStage)();
  syncWorkspaceMode();
  syncPrimaryAction();
}

function syncWorkspaceMode() {
  const workspace = $("workspace");
  if (!workspace) return;
  workspace.classList.toggle("ocr-focus-mode", state.view === "factory" && state.stage === "ocr" && !state.batchMode);
  workspace.classList.toggle("batch-focus-mode", state.view === "factory" && Boolean(state.batchMode));
}

function renderPagesStage() {
  const pdf = state.snapshot.pdf || {};
  const pageCount = Number(pdf.page_count || 0);
  if (!state.selectedPages.size && pageCount) {
    const detected = detectedPageNumbers();
    state.selectedPages = new Set(detected.length ? detected : [state.pdfPage]);
    persistFactoryUiState();
  }
  $("stageHost").innerHTML = `
    <div class="stage-header">
      <div>
        <h2>Elegir paginas del PDF</h2>
        <p class="muted">Define el tramo de trabajo. El detector solo analizara estas paginas y dejara todo en staging.</p>
      </div>
      <span class="status-pill status-${pdf.exists ? "listo" : "error"}">${pdf.exists ? pageCount + " paginas" : "PDF no encontrado"}</span>
    </div>
    <div class="toolbar">
      <button id="prevPdf">Anterior</button>
      <button id="nextPdf">Siguiente</button>
      <div class="field"><input id="pageInput" value="${state.pdfPage}" /></div>
      <button id="goPdf">Ir</button>
      <button id="togglePdf" class="secondary">${state.selectedPages.has(state.pdfPage) ? "Quitar pagina" : "Seleccionar pagina"}</button>
      <div class="field"><input id="rangeInput" value="${selectedRangeText() || (pageCount ? "1-" + pageCount : "")}" /></div>
      <button id="applyRange">Usar rango</button>
      <button id="selectAllPages">Todas</button>
      <button id="clearPages">Limpiar</button>
      <span class="selection-count">${state.selectedPages.size} seleccionada(s)</span>
    </div>
    ${renderModelStrip(["pdf_detector"])}
    <div id="pagePicker" class="page-picker"></div>
    <div class="grid-two">
      <div class="canvas-wrap page-canvas ${state.selectedPages.has(state.pdfPage) ? "is-selected" : ""}">
        <div class="canvas-badge">${state.selectedPages.has(state.pdfPage) ? "Pagina seleccionada" : "Pagina no seleccionada"}</div>
        <canvas id="pdfCanvas"></canvas>
      </div>
      <div class="panel">
        <h3>Seleccionadas</h3>
        <div id="selectedPagesList" class="list"></div>
      </div>
    </div>
  `;
  $("prevPdf").onclick = () => setPdfPage(Math.max(1, state.pdfPage - 1));
  $("nextPdf").onclick = () => setPdfPage(Math.min(pageCount || 1, state.pdfPage + 1));
  $("goPdf").onclick = () => setPdfPage(Number($("pageInput").value || 1));
  $("togglePdf").onclick = () => {
    if (state.selectedPages.has(state.pdfPage)) state.selectedPages.delete(state.pdfPage);
    else state.selectedPages.add(state.pdfPage);
    persistFactoryUiState();
    renderPagesStage();
  };
  $("applyRange").onclick = () => {
    state.selectedPages = parseRange($("rangeInput").value, pageCount);
    persistFactoryUiState();
    renderPagesStage();
  };
  $("selectAllPages").onclick = () => {
    state.selectedPages = new Set(Array.from({ length: pageCount }, (_, index) => index + 1));
    persistFactoryUiState();
    renderPagesStage();
  };
  $("clearPages").onclick = () => {
    state.selectedPages = new Set();
    persistFactoryUiState();
    renderPagesStage();
  };
  renderPagePicker(pageCount);
  renderSelectedPagesList();
  drawImageOnCanvas($("pdfCanvas"), `/api/pdf/page?page=${state.pdfPage}&dpi=150`);
  setInspector({
    "PDF": pdf.path || "-",
    "Pagina actual": state.pdfPage,
    "Paginas elegidas": selectedRangeText() || "-",
  });
}

function renderPagePicker(pageCount) {
  const picker = $("pagePicker");
  if (!picker) return;
  if (!pageCount) {
    picker.innerHTML = `<p class="muted">No hay paginas disponibles.</p>`;
    return;
  }
  const pages = Array.from({ length: pageCount }, (_, index) => index + 1);
  picker.innerHTML = pages.map((page) => `
    <button class="page-chip ${page === state.pdfPage ? "current" : ""} ${state.selectedPages.has(page) ? "selected" : ""}" data-page="${page}" title="Pagina ${page}">
      ${page}
    </button>
  `).join("");
  picker.querySelectorAll("[data-page]").forEach((item) => {
    item.onclick = () => setPdfPage(Number(item.dataset.page));
    item.ondblclick = () => {
      const page = Number(item.dataset.page);
      if (state.selectedPages.has(page)) state.selectedPages.delete(page);
      else state.selectedPages.add(page);
      state.pdfPage = page;
      persistFactoryUiState();
      renderPagesStage();
    };
  });
}

function renderSelectedPagesList() {
  const list = $("selectedPagesList");
  const pages = [...state.selectedPages].sort((a, b) => a - b);
  list.innerHTML = pages.length ? pages.map((page) => `
    <div class="row-card page-row ${page === state.pdfPage ? "active" : ""}" data-page="${page}">
      <div>
        <strong>Pagina ${page}</strong>
        <div class="muted">Seleccionada para deteccion.</div>
      </div>
      <button data-remove-page="${page}" title="Quitar pagina">X</button>
    </div>
  `).join("") : `<p class="muted">Sin paginas seleccionadas.</p>`;
  list.querySelectorAll("[data-page]").forEach((item) => {
    item.onclick = (event) => {
      if (event.target.closest("[data-remove-page]")) return;
      setPdfPage(Number(item.dataset.page));
    };
  });
  list.querySelectorAll("[data-remove-page]").forEach((btn) => {
    btn.onclick = (event) => {
      event.stopPropagation();
      state.selectedPages.delete(Number(btn.dataset.removePage));
      persistFactoryUiState();
      renderPagesStage();
    };
  });
}

function setPdfPage(page) {
  const count = Number(state.snapshot.pdf.page_count || 1);
  state.pdfPage = Math.max(1, Math.min(count, Number(page || 1)));
  persistFactoryUiState();
  renderPagesStage();
}

function renderBoxesStage() {
  const pages = factoryPages();
  syncSelectedBoxPage(pages);
  const page = pages.find((row) => row.record_id === state.selectedPageRecordId) || pages[0];
  syncCurrentPageBoxes(page);
  $("stageHost").innerHTML = `
    <div class="stage-header">
      <div>
        <h2>Revisar boxes de problemas</h2>
        <p class="muted">Ajusta cada caja para que contenga un problema matematico completo.</p>
      </div>
    </div>
    <div class="toolbar">
      <button id="modeSelect" class="${state.boxMode === "select" ? "secondary" : ""}">Seleccionar/mover</button>
      <button id="modeAdd" class="${state.boxMode === "add" ? "secondary" : ""}">Nuevo box</button>
      <button id="deleteBox">Eliminar</button>
      <button id="sortBoxes">Reordenar lectura</button>
      <button id="moveBoxUp">Subir</button>
      <button id="moveBoxDown">Bajar</button>
      <div class="zoom-tools" aria-label="Zoom del editor de boxes">
        <button id="boxZoomOut" type="button" title="Alejar">-</button>
        <span id="boxZoomLabel" class="zoom-label">Auto</span>
        <button id="boxZoomIn" type="button" title="Acercar">+</button>
        <button id="boxZoomFit" type="button" title="Ajustar al panel">Ajustar</button>
        <button id="boxZoomActual" type="button" title="Ver a tamano real">100%</button>
      </div>
      <select id="layoutMode" class="field">
        ${["auto", "una_columna", "dos_columnas"].map((value) => `<option value="${value}" ${page && page.layout_mode === value ? "selected" : ""}>${value}</option>`).join("")}
      </select>
      <button id="saveBoxes" class="primary">Guardar pagina revisada</button>
    </div>
    <div class="grid-two boxes-editor-grid">
      <div class="canvas-wrap boxes-canvas-wrap"><canvas id="boxCanvas"></canvas></div>
      <div class="panel">
        <h3>Paginas detectadas (${pages.length})</h3>
        <div id="pagesList" class="list"></div>
        <h3 class="section-title">Boxes de la pagina</h3>
        <div id="boxesList" class="list compact-list"></div>
      </div>
    </div>
  `;
  renderPageRecordList(pages);
  renderBoxList();
  if (page) setupBoxCanvas(page);
  $("modeSelect").onclick = () => { state.boxMode = "select"; renderBoxesStage(); };
  $("modeAdd").onclick = () => { state.boxMode = "add"; renderBoxesStage(); };
  $("deleteBox").onclick = () => deleteSelectedBox();
  $("sortBoxes").onclick = () => {
    state.boxes.sort((a, b) => (a[1] - b[1]) || (a[0] - b[0]));
    state.selectedBox = Math.min(state.selectedBox, state.boxes.length - 1);
    markBoxesDirty();
  };
  $("moveBoxUp").onclick = () => moveSelectedBox(-1);
  $("moveBoxDown").onclick = () => moveSelectedBox(1);
  $("layoutMode").onchange = () => { state.boxDirty = true; };
  $("saveBoxes").onclick = saveCurrentBoxes;
  bindBoxZoomControls();
  setInspector(page ? {
    "Pagina": page.page_number,
    "Boxes": state.boxes.length,
    "Modo": state.boxMode === "add" ? "Nuevo box" : "Seleccionar y ajustar",
    "Zoom": currentBoxZoomLabel(),
    "Cambios sin guardar": state.boxDirty ? "si" : "no",
  } : "Ejecuta primero la deteccion de paginas.");
}

function renderPageRecordList(pages) {
  const list = $("pagesList");
  list.innerHTML = pages.length ? pages.map((page) => `
    <div class="row-card ${page.record_id === state.selectedPageRecordId ? "active" : ""}" data-id="${page.record_id}">
      <strong>Pagina ${page.page_number}</strong>
      <div class="muted">${page.boxes_total} box(es) | ${page.reviewed ? "revisada" : "pendiente"}</div>
    </div>
  `).join("") : `<p class="muted">Aun no hay paginas detectadas.</p>`;
  list.querySelectorAll("[data-id]").forEach((item) => item.onclick = () => {
    selectBoxPage(item.dataset.id);
  });
}

function factoryPages() {
  return dedupePagesByNumber(state.snapshot.pages || []);
}

function dedupePagesByNumber(pages) {
  const byPage = new Map();
  (pages || []).forEach((page, index) => {
    const pageNumber = Number(page.page_number || 0);
    const key = pageNumber > 0 ? `page:${pageNumber}` : `record:${page.record_id || index}`;
    const current = byPage.get(key);
    if (!current || pageSyncScore(page, index) >= pageSyncScore(current.page, current.index)) {
      byPage.set(key, { page, index });
    }
  });
  return [...byPage.values()]
    .map((item) => item.page)
    .sort((a, b) => (Number(a.page_number || 0) - Number(b.page_number || 0)) || String(a.record_id || "").localeCompare(String(b.record_id || "")));
}

function pageSyncScore(page, index) {
  const detector = String(page.detector_source || "").toLowerCase();
  return (detector.startsWith("pdf_factory") ? 10_000_000_000 : 0)
    + (page.reviewed ? 1_000_000_000 : 0)
    + Number(page.boxes_total || (page.boxes || []).length || 0) * 10_000
    + (page.image_url ? 1_000 : 0)
    + Number(index || 0);
}

function syncSelectedBoxPage(pages) {
  if (!pages.length) {
    state.selectedPageRecordId = "";
    syncCurrentPageBoxes(null);
    return;
  }
  if (!pages.some((row) => row.record_id === state.selectedPageRecordId)) {
    state.selectedPageRecordId = pages[0].record_id;
  }
  const page = pages.find((row) => row.record_id === state.selectedPageRecordId) || pages[0];
  state.pdfPage = Number(page.page_number || state.pdfPage || 1);
}

function syncCurrentPageBoxes(page) {
  const source = page ? String(page.record_id || "") : "";
  const signature = page ? pageBoxesSignature(page) : "";
  const shouldLoadPage = state._boxSource !== source || (!state.boxDirty && state._boxSourceSignature !== signature);
  if (shouldLoadPage) {
    state.boxes = page ? cloneBoxes(page.boxes) : [];
    state._boxSource = source;
    state._boxSourceSignature = signature;
    state.selectedBox = -1;
    state.boxDirty = false;
    state.drag = null;
    boxCanvasState = null;
  }
  if (state.selectedBox >= state.boxes.length) state.selectedBox = state.boxes.length - 1;
}

function syncSelectedRecord() {
  const records = state.snapshot?.records || [];
  if (!records.length) {
    state.selectedRecordId = "";
    state.selectedOcrIndex = 0;
    return;
  }
  if (!records.some((record) => record.record_id === state.selectedRecordId)) {
    state.selectedRecordId = records[0].record_id;
    state.selectedOcrIndex = 0;
  }
}

function isReviewContinuationRecord(record) {
  if (!record) return false;
  const normalized = record.normalized && typeof record.normalized === "object" ? record.normalized : {};
  const continuation = normalized.continuacion && typeof normalized.continuacion === "object"
    ? normalized.continuacion
    : {};
  return Boolean(continuation.es_continuacion || continuation.fusionar_con_anterior || isFinalLatexContinuation(record.raw_ocr));
}

function reviewRecords(allRecords = state.snapshot?.records || []) {
  return (allRecords || []).filter((record) => !isReviewContinuationRecord(record));
}

function findParentForContinuation(record, allRecords = state.snapshot?.records || []) {
  if (!record) return null;
  const normalized = record.normalized && typeof record.normalized === "object" ? record.normalized : {};
  const continuation = normalized.continuacion && typeof normalized.continuacion === "object"
    ? normalized.continuacion
    : {};
  const parentId = String(continuation.parent_record_id || "").trim();
  if (parentId) {
    const parent = findRecordById(parentId, allRecords);
    if (parent && !isReviewContinuationRecord(parent)) return parent;
  }
  const index = allRecords.findIndex((row) => String(row.record_id || "") === String(record.record_id || ""));
  for (let i = index - 1; i >= 0; i -= 1) {
    if (!isReviewContinuationRecord(allRecords[i])) return allRecords[i];
  }
  return reviewRecords(allRecords)[0] || null;
}

function ensureReviewSelectedRecord(visibleRecords, allRecords = state.snapshot?.records || []) {
  if (!visibleRecords.length) {
    state.selectedRecordId = "";
    state.selectedOcrIndex = 0;
    return null;
  }
  const current = findRecordById(state.selectedRecordId, allRecords);
  if (current && isReviewContinuationRecord(current)) {
    const parent = findParentForContinuation(current, allRecords);
    if (parent) {
      state.selectedRecordId = parent.record_id;
      state.selectedOcrIndex = 0;
      return parent;
    }
  }
  const visible = visibleRecords.find((record) => record.record_id === state.selectedRecordId);
  if (visible) return visible;
  state.selectedRecordId = visibleRecords[0].record_id;
  state.selectedOcrIndex = 0;
  return visibleRecords[0];
}

function pageBoxesSignature(page) {
  return [
    page.record_id || "",
    page.layout_mode || "",
    page.reviewed ? "1" : "0",
    JSON.stringify(page.boxes || []),
  ].join("|");
}

function selectBoxPage(recordId) {
  const next = String(recordId || "");
  if (!next || next === state.selectedPageRecordId) return;
  if (state.boxDirty && !window.confirm("Hay cambios sin guardar en esta pagina. Cambiar de pagina descartara esos ajustes.")) {
    return;
  }
  state.selectedPageRecordId = next;
  state._boxSource = "";
  state._boxSourceSignature = "";
  state.boxes = [];
  state.selectedBox = -1;
  state.boxDirty = false;
  state.drag = null;
  persistFactoryUiState();
  renderBoxesStage();
}

function renderBoxList() {
  const list = $("boxesList");
  if (!list) return;
  list.innerHTML = state.boxes.length ? state.boxes.map((box, index) => {
    const [x1, y1, x2, y2] = box;
    return `
      <div class="row-card box-row ${index === state.selectedBox ? "active" : ""}" data-box="${index}">
        <div>
          <strong>Box ${index + 1}</strong>
          <div class="muted">${x1},${y1} -> ${x2},${y2}</div>
        </div>
        <button data-delete-box="${index}" title="Eliminar box">X</button>
      </div>
    `;
  }).join("") : `<p class="muted">Sin boxes. Usa Nuevo box y arrastra sobre la pagina.</p>`;
  list.querySelectorAll("[data-box]").forEach((item) => {
    item.onclick = (event) => {
      if (event.target.closest("[data-delete-box]")) return;
      state.selectedBox = Number(item.dataset.box);
      redrawBoxes();
      renderBoxList();
    };
  });
  list.querySelectorAll("[data-delete-box]").forEach((btn) => {
    btn.onclick = (event) => {
      event.stopPropagation();
      state.selectedBox = Number(btn.dataset.deleteBox);
      deleteSelectedBox();
    };
  });
}

function deleteSelectedBox() {
  if (state.selectedBox < 0 || state.selectedBox >= state.boxes.length) return;
  state.boxes.splice(state.selectedBox, 1);
  state.selectedBox = Math.min(state.selectedBox, state.boxes.length - 1);
  markBoxesDirty();
}

function moveSelectedBox(delta) {
  const from = state.selectedBox;
  const to = from + delta;
  if (from < 0 || to < 0 || from >= state.boxes.length || to >= state.boxes.length) return;
  const [box] = state.boxes.splice(from, 1);
  state.boxes.splice(to, 0, box);
  state.selectedBox = to;
  markBoxesDirty();
}

function markBoxesDirty() {
  state.boxDirty = true;
  redrawBoxes();
  renderBoxList();
}

let boxCanvasState = null;
function setupBoxCanvas(page) {
  const canvas = $("boxCanvas");
  const wrapper = canvas.parentElement;
  const recordId = String(page.record_id || "");
  canvas.dataset.recordId = recordId;
  boxCanvasState = null;
  const img = new Image();
  img.onload = () => {
    if (String(state.selectedPageRecordId || "") !== recordId || canvas.dataset.recordId !== recordId) return;
    boxCanvasState = { canvas, ctx: canvas.getContext("2d"), img, wrapper, fitScale: 1, scale: 1 };
    resizeBoxCanvas({ resetScroll: true });
  };
  img.onerror = () => {
    const ctx = canvas.getContext("2d");
    boxCanvasState = null;
    canvas.width = Math.max(320, Math.floor((wrapper?.clientWidth || 520) - 28));
    canvas.height = 220;
    canvas.style.width = `${canvas.width}px`;
    canvas.style.height = `${canvas.height}px`;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#637386";
    ctx.font = "600 14px Segoe UI";
    ctx.fillText("No se pudo cargar la imagen de esta pagina.", 18, 42);
    syncBoxZoomControls();
  };
  img.src = boxPageImageSrc(page);
  canvas.onmousedown = onBoxMouseDown;
  canvas.onmousemove = onBoxMouseMove;
  canvas.onmouseup = onBoxMouseUp;
  canvas.onmouseleave = onBoxMouseUp;
  canvas.onwheel = onBoxCanvasWheel;
  syncBoxZoomControls();
}

function boxPageImageSrc(page) {
  if (page?.image_url) return page.image_url;
  return `/api/pdf/page?page=${encodeURIComponent(page?.page_number || 1)}&dpi=300`;
}

function bindBoxZoomControls() {
  $("boxZoomOut").onclick = () => setBoxZoom(state.boxZoom / 1.2);
  $("boxZoomIn").onclick = () => setBoxZoom(state.boxZoom * 1.2);
  $("boxZoomFit").onclick = () => setBoxZoom(1);
  $("boxZoomActual").onclick = () => setBoxZoomActual();
  syncBoxZoomControls();
}

function clampBoxZoom(value) {
  const next = Number(value);
  if (!Number.isFinite(next)) return 1;
  return Math.max(BOX_ZOOM_MIN, Math.min(BOX_ZOOM_MAX, next));
}

function boxFitScale(img, wrapper) {
  const availableWidth = Math.max(320, Math.floor((wrapper?.clientWidth || 900) - 28));
  const maxW = Math.min(900, availableWidth);
  return Math.min(1, maxW / Math.max(1, img.naturalWidth));
}

function boxScaleFor(img, wrapper) {
  const fitScale = boxFitScale(img, wrapper);
  return {
    fitScale,
    scale: Math.max(0.05, Math.min(BOX_SCALE_MAX, fitScale * clampBoxZoom(state.boxZoom))),
  };
}

function resizeBoxCanvas({ preserveCenter = false, resetScroll = false } = {}) {
  if (!boxCanvasState) {
    syncBoxZoomControls();
    return;
  }
  const { canvas, img, wrapper } = boxCanvasState;
  const oldWidth = Math.max(1, canvas.width || 1);
  const oldHeight = Math.max(1, canvas.height || 1);
  const centerX = wrapper ? wrapper.scrollLeft + wrapper.clientWidth / 2 : oldWidth / 2;
  const centerY = wrapper ? wrapper.scrollTop + wrapper.clientHeight / 2 : oldHeight / 2;
  const ratioX = centerX / oldWidth;
  const ratioY = centerY / oldHeight;
  const { fitScale, scale } = boxScaleFor(img, wrapper);
  boxCanvasState.fitScale = fitScale;
  boxCanvasState.scale = scale;
  canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
  canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
  canvas.style.width = `${canvas.width}px`;
  canvas.style.height = `${canvas.height}px`;
  redrawBoxes();
  if (wrapper) {
    if (resetScroll) {
      wrapper.scrollTop = 0;
      wrapper.scrollLeft = 0;
    } else if (preserveCenter) {
      wrapper.scrollLeft = Math.max(0, ratioX * canvas.width - wrapper.clientWidth / 2);
      wrapper.scrollTop = Math.max(0, ratioY * canvas.height - wrapper.clientHeight / 2);
    }
  }
  syncBoxZoomControls();
}

function setBoxZoom(value) {
  state.boxZoom = clampBoxZoom(value);
  resizeBoxCanvas({ preserveCenter: true });
}

function setBoxZoomActual() {
  const fitScale = boxCanvasState?.fitScale || 1;
  setBoxZoom(1 / Math.max(0.05, fitScale));
}

function currentBoxZoomLabel() {
  if (boxCanvasState?.scale) return `${Math.round(boxCanvasState.scale * 100)}%`;
  return `${Math.round(clampBoxZoom(state.boxZoom) * 100)}%`;
}

function syncBoxZoomControls() {
  const label = $("boxZoomLabel");
  if (label) label.textContent = currentBoxZoomLabel();
  const hasCanvas = Boolean(boxCanvasState);
  ["boxZoomOut", "boxZoomIn", "boxZoomFit", "boxZoomActual"].forEach((id) => {
    const btn = $(id);
    if (btn) btn.disabled = !hasCanvas;
  });
}

function onBoxCanvasWheel(event) {
  if (!boxCanvasState || !event.ctrlKey) return;
  event.preventDefault();
  setBoxZoom(state.boxZoom * (event.deltaY > 0 ? 1 / 1.12 : 1.12));
}

function redrawBoxes() {
  if (!boxCanvasState) return;
  const { canvas, ctx, img, scale } = boxCanvasState;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  state.boxes.forEach((box, idx) => {
    const [x1, y1, x2, y2] = box.map((v) => Math.round(v * scale));
    ctx.lineWidth = idx === state.selectedBox ? 4 : 3;
    ctx.strokeStyle = idx === state.selectedBox ? "#2563eb" : "#d92d20";
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
    ctx.fillStyle = idx === state.selectedBox ? "#2563eb" : "#d92d20";
    ctx.fillRect(x1, y1, 25, 23);
    ctx.fillStyle = "white";
    ctx.font = "bold 13px Segoe UI";
    ctx.fillText(String(idx + 1), x1 + 8, y1 + 16);
    if (idx === state.selectedBox) drawHandles(ctx, x1, y1, x2, y2);
  });
}

function drawHandles(ctx, x1, y1, x2, y2) {
  ctx.fillStyle = "#ffffff";
  ctx.strokeStyle = "#2563eb";
  boxHandlePoints([x1, y1, x2, y2]).forEach(({ x, y }) => {
    ctx.beginPath();
    ctx.rect(x - 5, y - 5, 10, 10);
    ctx.fill();
    ctx.stroke();
  });
}

function boxHandlePoints(box) {
  const [x1, y1, x2, y2] = box;
  const cx = x1 + (x2 - x1) / 2;
  const cy = y1 + (y2 - y1) / 2;
  return [
    { name: "nw", x: x1, y: y1 },
    { name: "n", x: cx, y: y1 },
    { name: "ne", x: x2, y: y1 },
    { name: "e", x: x2, y: cy },
    { name: "se", x: x2, y: y2 },
    { name: "s", x: cx, y: y2 },
    { name: "sw", x: x1, y: y2 },
    { name: "w", x: x1, y: cy },
  ];
}

function canvasPoint(event) {
  const rect = boxCanvasState.canvas.getBoundingClientRect();
  return { x: event.clientX - rect.left, y: event.clientY - rect.top };
}

function imagePoint(event) {
  const p = canvasPoint(event);
  return { x: p.x / boxCanvasState.scale, y: p.y / boxCanvasState.scale };
}

function findBoxAt(point) {
  const scale = boxCanvasState.scale;
  for (let i = state.boxes.length - 1; i >= 0; i -= 1) {
    const [x1, y1, x2, y2] = state.boxes[i].map((v) => v * scale);
    if (point.x >= x1 && point.x <= x2 && point.y >= y1 && point.y <= y2) return i;
  }
  return -1;
}

function findHandleAt(point) {
  if (state.selectedBox < 0 || state.selectedBox >= state.boxes.length) return null;
  const scale = boxCanvasState.scale;
  const box = state.boxes[state.selectedBox].map((value) => value * scale);
  const hitSize = 9;
  for (const handle of boxHandlePoints(box)) {
    if (Math.abs(point.x - handle.x) <= hitSize && Math.abs(point.y - handle.y) <= hitSize) {
      return { index: state.selectedBox, handle: handle.name };
    }
  }
  return null;
}

function onBoxMouseDown(event) {
  if (!boxCanvasState) return;
  const canvasP = canvasPoint(event);
  const imageP = imagePoint(event);
  if (state.boxMode === "add") {
    state.drag = { type: "new", start: imageP, current: imageP };
    return;
  }
  const handleHit = findHandleAt(canvasP);
  if (handleHit) {
    state.drag = {
      type: "resize",
      handle: handleHit.handle,
      start: imageP,
      original: [...state.boxes[handleHit.index]],
    };
    return;
  }
  const hit = findBoxAt(canvasP);
  state.selectedBox = hit;
  if (hit >= 0) {
    state.drag = { type: "move", start: imageP, original: [...state.boxes[hit]] };
  }
  redrawBoxes();
  renderBoxList();
}

function onBoxMouseMove(event) {
  if (!boxCanvasState) return;
  const p = imagePoint(event);
  if (!state.drag) {
    updateBoxCanvasCursor(canvasPoint(event));
    return;
  }
  if (state.drag.type === "new") {
    state.drag.current = p;
    redrawBoxes();
    const box = clampBoxToImage(normalizeBox([state.drag.start.x, state.drag.start.y, p.x, p.y]));
    const ctx = boxCanvasState.ctx;
    const s = boxCanvasState.scale;
    ctx.strokeStyle = "#2563eb";
    ctx.lineWidth = 2;
    ctx.strokeRect(box[0] * s, box[1] * s, (box[2] - box[0]) * s, (box[3] - box[1]) * s);
  } else if (state.drag.type === "move" && state.selectedBox >= 0) {
    const dx = p.x - state.drag.start.x;
    const dy = p.y - state.drag.start.y;
    const box = state.drag.original;
    state.boxes[state.selectedBox] = moveBoxWithinImage(box, dx, dy);
    state.boxDirty = true;
    redrawBoxes();
    renderBoxList();
  } else if (state.drag.type === "resize" && state.selectedBox >= 0) {
    state.boxes[state.selectedBox] = resizeBoxFromHandle(state.drag.original, state.drag.handle, p);
    state.boxDirty = true;
    redrawBoxes();
    renderBoxList();
  }
}

function onBoxMouseUp() {
  if (!state.drag) return;
  if (state.drag.type === "new") {
    const box = clampBoxToImage(normalizeBox([state.drag.start.x, state.drag.start.y, state.drag.current.x, state.drag.current.y]));
    if (box[2] - box[0] >= 20 && box[3] - box[1] >= 20) {
      state.boxes.push(box);
      state.selectedBox = state.boxes.length - 1;
      state.boxDirty = true;
    }
  }
  state.drag = null;
  redrawBoxes();
  renderBoxList();
}

function updateBoxCanvasCursor(point) {
  const canvas = boxCanvasState.canvas;
  const handle = findHandleAt(point);
  if (handle) {
    canvas.style.cursor = {
      n: "ns-resize",
      s: "ns-resize",
      e: "ew-resize",
      w: "ew-resize",
      nw: "nwse-resize",
      se: "nwse-resize",
      ne: "nesw-resize",
      sw: "nesw-resize",
    }[handle.handle] || "crosshair";
    return;
  }
  canvas.style.cursor = findBoxAt(point) >= 0 ? "move" : (state.boxMode === "add" ? "crosshair" : "default");
}

function imageBounds() {
  if (!boxCanvasState) return { width: 1, height: 1 };
  return { width: boxCanvasState.img.naturalWidth, height: boxCanvasState.img.naturalHeight };
}

function clampBoxToImage(box) {
  const bounds = imageBounds();
  const [x1, y1, x2, y2] = normalizeBox(box);
  return [
    Math.max(0, Math.min(bounds.width, x1)),
    Math.max(0, Math.min(bounds.height, y1)),
    Math.max(0, Math.min(bounds.width, x2)),
    Math.max(0, Math.min(bounds.height, y2)),
  ];
}

function moveBoxWithinImage(box, dx, dy) {
  const bounds = imageBounds();
  const width = box[2] - box[0];
  const height = box[3] - box[1];
  const x1 = Math.max(0, Math.min(bounds.width - width, box[0] + dx));
  const y1 = Math.max(0, Math.min(bounds.height - height, box[1] + dy));
  return normalizeBox([x1, y1, x1 + width, y1 + height]);
}

function resizeBoxFromHandle(box, handle, point) {
  let [x1, y1, x2, y2] = box;
  const minSize = 16;
  if (handle.includes("w")) x1 = Math.min(point.x, x2 - minSize);
  if (handle.includes("e")) x2 = Math.max(point.x, x1 + minSize);
  if (handle.includes("n")) y1 = Math.min(point.y, y2 - minSize);
  if (handle.includes("s")) y2 = Math.max(point.y, y1 + minSize);
  return clampBoxToImage([x1, y1, x2, y2]);
}

async function saveCurrentBoxes() {
  const page = factoryPages().find((row) => row.record_id === state.selectedPageRecordId);
  if (!page) return;
  await runAction("Guardando boxes revisados...", async () => {
    state.snapshot = await api("/api/pages/boxes", {
      method: "POST",
      body: {
        record_id: page.record_id,
        boxes: state.boxes,
        layout_mode: $("layoutMode").value,
        reviewed: true,
      },
    });
    state.boxes = [];
    state._boxSource = "";
    state._boxSourceSignature = "";
    state.boxDirty = false;
    persistFactoryUiState();
    render();
    const invalidated = invalidatedRecords(state.snapshot.records || []).length;
    return invalidated
      ? `Boxes guardados. ${invalidated} registro(s) downstream quedaron pendientes de regenerar.`
      : "Boxes guardados.";
  }, "Boxes guardados.");
}

function renderCropsStage() {
  const records = state.snapshot.records || [];
  syncOcrQueueSelection(records);
  syncSelectedRecord();
  const record = selectedRecord();
  const queuedCount = queuedOcrRecordIds(records).length;
  const queueBusy = isTaskRunning("ocr");
  const invalidatedCount = invalidatedRecords(records).length;
  $("stageHost").innerHTML = `
    <div class="stage-header">
      <div>
        <h2>Crops y staging</h2>
        <p class="muted">Elige las imagenes que entraran a la cola de OCR y segmentacion grafica.</p>
      </div>
      <span class="status-pill status-${queuedCount ? "procesando" : (records.length ? "listo" : "pendiente")}">${queuedCount ? `${queuedCount} en cola` : `${records.length} crop(s)`}</span>
    </div>
    ${renderModelStrip(["ocr", "figure_segmenter"])}
    ${invalidatedCount ? `
      <div class="library-notice">
        ${invalidatedCount} registro(s) dependen de boxes modificados. Vuelve a Crear staging para regenerar crops antes de OCR.
      </div>
    ` : ""}
    <div class="queue-toolbar panel">
      <div>
        <h3>Cola OCR + segmentacion</h3>
        <p class="muted">${queuedCount ? `${queuedCount} imagen(es) seleccionada(s).` : "Selecciona una o varias imagenes para procesarlas con los modelos entrenados."}</p>
      </div>
      <div class="queue-actions">
        <button id="queueAllCrops" type="button" ${records.length && !queueBusy ? "" : "disabled"}>Seleccionar todo</button>
        <button id="clearOcrQueue" type="button" ${queuedCount && !queueBusy ? "" : "disabled"}>Limpiar cola</button>
        <button id="runOcrQueue" class="primary" type="button" ${queuedCount && !queueBusy ? "" : "disabled"}>Ejecutar cola</button>
      </div>
    </div>
    ${renderTaskProgress("ocr")}
    <div class="crops-layout">
      <div class="thumb-grid crop-gallery">
        ${records.length ? records.map(recordCardHtml).join("") : `<div class="panel muted">Crea staging desde los boxes revisados.</div>`}
      </div>
      <div class="panel sticky-preview">
        ${record ? renderCropPreview(record) : `<p class="muted">Selecciona un crop para ver su trazabilidad.</p>`}
      </div>
    </div>
  `;
  bindRecordCards();
  bindOcrQueueControls(records);
  setInspector(record ? cropInspectorData(record, records.length) : {
    "Problemas en staging": records.length,
    "Siguiente paso": "Crear staging desde boxes revisados.",
  });
}

function recordCardHtml(record) {
  const source = record.source || {};
  const box = source.bbox_px || [];
  const queued = state.ocrQueueIds.has(String(record.record_id || ""));
  const queueLocked = isTaskRunning("ocr");
  const stale = recordSourceStale(record);
  const invalidated = recordDownstreamInvalidated(record);
  const canQueue = recordCanRunOcr(record);
  const errorComment = recordErrorComment(record);
  return `
    <div class="crop-card ${record.record_id === state.selectedRecordId ? "active" : ""} ${queued ? "queued" : ""} ${stale ? "stale" : ""} ${errorComment ? "has-error" : ""}" data-record="${record.record_id}">
      <label class="queue-check" title="Incluir en cola OCR">
        <input type="checkbox" data-queue-record="${escapeAttr(record.record_id)}" ${queued ? "checked" : ""} ${queueLocked || !canQueue ? "disabled" : ""} />
        <span>${queued ? "En cola" : (stale ? "Regenerar" : "Cola")}</span>
      </label>
      ${record.crop_url ? `<img src="${record.crop_url}" alt="Crop ${escapeHtml(record.record_id)}" loading="lazy" decoding="async" />` : ""}
      <strong>${escapeHtml(record.normalized?.numero || record.crop_name || record.record_id)}</strong>
      <div class="muted">Pag. ${escapeHtml(source.page_number || "-")} | ${escapeHtml(record.status_label || record.status || "pendiente")}</div>
      ${errorComment ? `<p class="meta-error-comment"><strong>Error:</strong> ${escapeHtml(errorComment)}</p>` : ""}
      ${stale ? `<div class="status-pill status-pendiente">Regenerar crop</div>` : (invalidated ? `<div class="status-pill status-requiere_revision">OCR pendiente por box</div>` : "")}
      <div class="crop-meta">${box.length >= 4 ? escapeHtml(box.slice(0, 4).join(", ")) : "sin bbox"}</div>
    </div>
  `;
}

function renderCropPreview(record) {
  const source = record.source || {};
  const box = source.bbox_px || [];
  const errorComment = recordErrorComment(record);
  return `
    <h3>Crop seleccionado</h3>
    ${recordSourceStale(record) ? `<div class="library-notice">Este crop viene de un box modificado. Regenera staging antes de correr OCR.</div>` : ""}
    ${!recordSourceStale(record) && recordDownstreamInvalidated(record) ? `<div class="library-notice">El crop ya fue regenerado; vuelve a ejecutar OCR/segmentacion para actualizar la cadena.</div>` : ""}
    ${errorComment ? `<div class="library-notice error-notice"><strong>Error:</strong> ${escapeHtml(errorComment)}</div>` : ""}
    ${record.crop_url ? `<img class="preview-img" src="${record.crop_url}" alt="Crop seleccionado" loading="lazy" decoding="async" />` : `<p class="muted">Imagen no encontrada.</p>`}
    <div class="metadata-grid">
      <span class="muted">Registro</span><strong>${escapeHtml(record.record_id)}</strong>
      <span class="muted">Pagina</span><strong>${escapeHtml(source.page_number || "-")}</strong>
      <span class="muted">Box</span><strong>${box.length >= 4 ? escapeHtml(box.slice(0, 4).join(", ")) : "-"}</strong>
      <span class="muted">Estado</span><strong>${escapeHtml(record.status_label || record.status || "-")}</strong>
    </div>
  `;
}

function cropInspectorData(record, total) {
  const source = record.source || {};
  const rows = {
    "Problemas en staging": total,
    "Registro": record.record_id,
    "Pagina": source.page_number || "-",
    "Box": (source.bbox_px || []).join(", ") || "-",
    "Crop": record.crop_path || "-",
    "Cadena": recordSourceStale(record) ? "Regenerar crop" : (recordDownstreamInvalidated(record) ? "OCR pendiente por cambio de box" : "Activa"),
  };
  const errorComment = recordErrorComment(record);
  if (errorComment) rows["Error"] = errorComment;
  return rows;
}

function syncOcrQueueSelection(records = state.snapshot.records || []) {
  const valid = new Set((records || []).filter(recordCanRunOcr).map((record) => String(record.record_id || "")));
  state.ocrQueueIds = new Set([...state.ocrQueueIds].filter((id) => valid.has(id)));
}

function queuedOcrRecordIds(records = state.snapshot.records || []) {
  syncOcrQueueSelection(records);
  return (records || [])
    .map((record) => String(record.record_id || ""))
    .filter((id) => id && state.ocrQueueIds.has(id) && recordCanRunOcr(findRecordById(id, records)));
}

function bindOcrQueueControls(records = state.snapshot.records || []) {
  const allBtn = $("queueAllCrops");
  const clearBtn = $("clearOcrQueue");
  const runBtn = $("runOcrQueue");
  if (allBtn) allBtn.onclick = () => {
    state.ocrQueueIds = new Set((records || []).filter(recordCanRunOcr).map((record) => String(record.record_id || "")).filter(Boolean));
    persistFactoryUiState();
    renderCropsStage();
  };
  if (clearBtn) clearBtn.onclick = () => {
    state.ocrQueueIds = new Set();
    persistFactoryUiState();
    renderCropsStage();
  };
  if (runBtn) runBtn.onclick = () => runOcr(queuedOcrRecordIds(records));
  document.querySelectorAll("[data-queue-record]").forEach((checkbox) => {
    checkbox.onclick = (event) => event.stopPropagation();
    checkbox.onchange = (event) => {
      event.stopPropagation();
      const id = String(checkbox.dataset.queueRecord || "");
      if (!id) return;
      if (checkbox.checked) state.ocrQueueIds.add(id);
      else state.ocrQueueIds.delete(id);
      persistFactoryUiState();
      renderCropsStage();
    };
  });
}

function bindRecordCards() {
  document.querySelectorAll("[data-record]").forEach((item) => {
    item.onclick = () => {
      state.selectedRecordId = item.dataset.record;
      state.selectedOcrIndex = 0;
      state.reviewDraft = null;
      persistFactoryUiState();
      renderStage();
    };
  });
}

function recordIndex(records = state.snapshot.records || []) {
  if (!records.length) return 0;
  const currentId = selectedRecord()?.record_id || state.selectedRecordId;
  const index = records.findIndex((record) => record.record_id === currentId);
  return index >= 0 ? index : 0;
}

function selectRecordAt(index, records = state.snapshot.records || []) {
  if (!records.length) return;
  const nextIndex = Math.max(0, Math.min(records.length - 1, Number(index || 0)));
  const next = records[nextIndex];
  if (!next || next.record_id === state.selectedRecordId) return;
  state.selectedRecordId = next.record_id;
  state.selectedOcrIndex = 0;
  state.reviewDraft = null;
  persistFactoryUiState();
  renderStage();
}

function selectRecordByOffset(delta, records = state.snapshot.records || []) {
  if (!records.length) return;
  selectRecordAt(recordIndex(records) + delta, records);
}

function bindRecordNavigation(records = state.snapshot.records || []) {
  const prev = $("prevRecord");
  const next = $("nextRecord");
  const jump = $("recordJump");
  if (prev) prev.onclick = () => selectRecordByOffset(-1, records);
  if (next) next.onclick = () => selectRecordByOffset(1, records);
  if (jump) jump.onchange = () => selectRecordAt(Number(jump.value || 0), records);
}

function selectOcrItemAt(index, record = selectedRecord()) {
  const items = record?.structured_items_web || [];
  if (!items.length) return;
  state.selectedOcrIndex = Math.max(0, Math.min(items.length - 1, Number(index || 0)));
  persistFactoryUiState();
  renderOcrStage();
}

function selectOcrItemByOffset(delta, record = selectedRecord()) {
  selectOcrItemAt(Number(state.selectedOcrIndex || 0) + delta, record);
}

function bindOcrNavigation(record) {
  const prev = $("prevOcrItem");
  const next = $("nextOcrItem");
  if (prev) prev.onclick = () => selectOcrItemByOffset(-1, record);
  if (next) next.onclick = () => selectOcrItemByOffset(1, record);
}

function recordOptionLabel(record, index) {
  const source = record.source || {};
  const number = record.normalized?.numero;
  const base = number ? `Problema ${number}` : (record.crop_name || record.record_id);
  const page = source.page_number ? `Pag. ${source.page_number}` : "";
  return compactText(`${index + 1}. ${base}${page ? ` | ${page}` : ""}`, 84);
}

function renderOcrStage() {
  syncSelectedRecord();
  const record = selectedRecord();
  const records = state.snapshot.records || [];
  const currentRecordIndex = recordIndex(records);
  $("stageHost").innerHTML = `
    <div class="stage-header">
      <div>
        <h2>OCR y segmentacion grafica</h2>
        <p class="muted">Compara la lectura estructurada con el crop antes de cargarla al formulario final.</p>
      </div>
      <div class="stage-actions">
        <button id="openRawOcrBatch" type="button">Modo lote OCR crudo</button>
      </div>
    </div>
    ${renderModelStrip(["ocr", "figure_segmenter"])}
    ${renderOcrEndpointCard()}
    ${renderTaskProgress("ocr")}
    ${record ? renderOcrRecord(record) : `<div class="panel muted">Selecciona un crop de staging.</div>`}
  `;
  bindRecordCards();
  const rawBatchBtn = $("openRawOcrBatch");
  if (rawBatchBtn) rawBatchBtn.onclick = () => openBatchMode("raw_ocr");
  bindOcrEndpointActions();
  if (!state.ocrEndpoint && !state.ocrEndpointLoading) {
    refreshOcrEndpointStatus({ silent: true });
  }
  bindRecordNavigation();
  bindOcrNavigation(record);
  bindFigureSegmentEditor(record);
  bindRawOcrActions(record);
  typesetMath($("ocrLatexPreview"));
  document.querySelectorAll("[data-use-ocr]").forEach((btn) => {
    btn.onclick = () => {
      const payload = selectedOcrPayload(record, btn.dataset.useOcr);
      state.reviewDraft = normalizedFromOcr(record, payload);
      state.stage = "review";
      persistFactoryUiState();
      render();
    };
  });
  setInspector(record ? {
    "Imagen": `${records.length ? currentRecordIndex + 1 : 0} de ${records.length}`,
    "Registro": record.record_id,
    ...(recordErrorComment(record) ? { "Error": recordErrorComment(record) } : {}),
    "OCR crudo": record.raw_ocr ? `si (${String(record.raw_ocr).length} caracteres)` : "no",
    "Lecturas OCR": (record.structured_items_web || []).length,
    "Segmentos graficos": (record.figure_segments_web || []).length,
  } : "");
}

function renderOcrRecord(record) {
  const records = state.snapshot.records || [];
  const currentRecordIndex = recordIndex(records);
  const totalRecords = records.length;
  const items = record.structured_items_web || [];
  const segments = record.figure_segments_web || [];
  const errorComment = recordErrorComment(record);
  syncFigureSegments(record, segments);
  const selectedIndex = Math.max(0, Math.min(items.length - 1, Number(state.selectedOcrIndex || 0)));
  const selectedPayload = items.length ? selectedOcrPayload(record, selectedIndex) : {};
  return `
    <div class="record-layout ocr-layout">
      <div class="ocr-left-column">
        <div class="record-nav panel" aria-label="Navegacion de problemas">
          <button id="prevRecord" class="nav-arrow" type="button" title="Imagen anterior" ${currentRecordIndex <= 0 ? "disabled" : ""}>&larr;</button>
          <div class="record-nav-main">
            <span class="section-label">Imagen</span>
            <strong>${totalRecords ? currentRecordIndex + 1 : 0} de ${totalRecords}</strong>
            <span class="muted">${escapeHtml(recordOptionLabel(record, currentRecordIndex))}</span>
          </div>
          <button id="nextRecord" class="nav-arrow" type="button" title="Imagen siguiente" ${currentRecordIndex >= totalRecords - 1 ? "disabled" : ""}>&rarr;</button>
          <label class="record-jump-label">
            <span class="muted">Saltar a</span>
            <select id="recordJump">
              ${records.map((row, index) => `<option value="${index}" ${index === currentRecordIndex ? "selected" : ""}>${escapeHtml(recordOptionLabel(row, index))}</option>`).join("")}
            </select>
          </label>
        </div>
        <section class="panel ocr-latex-panel">
          <div class="panel-heading-row">
            <div>
              <h3>Visor LaTeX</h3>
              <p class="muted">${items.length ? "Lectura estructurada lista para revisar antes de editar." : "Sin lectura estructurada todavia."}</p>
            </div>
            ${items.length ? `
              <div class="ocr-nav" aria-label="Navegacion de lecturas OCR">
                <button id="prevOcrItem" class="mini-arrow" type="button" title="Lectura anterior" ${selectedIndex <= 0 ? "disabled" : ""}>&larr;</button>
                <span class="nav-counter">${selectedIndex + 1} de ${items.length}</span>
                <button id="nextOcrItem" class="mini-arrow" type="button" title="Lectura siguiente" ${selectedIndex >= items.length - 1 ? "disabled" : ""}>&rarr;</button>
              </div>
            ` : ""}
          </div>
          ${items.length ? renderOcrLatexViewer(selectedPayload, selectedIndex, items.length) : `
            <div class="empty-state raw-empty">
              Ejecuta OCR sobre la imagen actual. Cuando el modelo devuelva una lectura estructurada, aparecera aqui como vista LaTeX.
            </div>
          `}
          ${items.length ? `<button data-use-ocr="${selectedIndex}" class="primary wide-action">Editar lectura</button>` : ""}
        </section>
        ${renderRawOcrPanel(record)}
        <dl class="meta-list record-meta-compact">
          <div><dt>Registro</dt><dd>${escapeHtml(record.record_id)}</dd></div>
          <div class="${errorComment ? "meta-error-card" : ""}">
            <dt>Estado</dt>
            <dd>${escapeHtml(record.status_label || record.status || "-")}</dd>
            ${errorComment ? `<p class="meta-error-comment"><strong>Error:</strong> ${escapeHtml(errorComment)}</p>` : ""}
          </div>
          <div><dt>Entrenamiento</dt><dd>${(record.training_examples || []).length} correccion(es)</dd></div>
        </dl>
      </div>
      <div class="ocr-image-column">
        <section class="panel ocr-image-panel">
          <div class="panel-heading-row">
            <div>
              <h3>Imagen con boxes</h3>
              <p class="muted">${segments.length ? `${segments.length} segmento(s) grafico(s) detectado(s).` : "Sin segmentos graficos detectados todavia."}</p>
            </div>
          </div>
          ${renderFigureSegmentEditor(record)}
          <div class="thumb-grid compact">
            ${segments.length ? segments.map((segment, idx) => `
              <div class="segment-card ${idx === state.selectedFigureSegment ? "active" : ""}" data-figure-segment="${idx}">
                ${segment.image_url ? `<img src="${segment.image_url}" alt="Segmento grafico ${idx + 1}" loading="lazy" decoding="async" />` : ""}
                <div class="muted">${escapeHtml(segment.image_name || `segmento_${idx + 1}`)}</div>
              </div>
            `).join("") : `<p class="muted">Sin segmentos graficos detectados.</p>`}
          </div>
        </section>
        ${renderTechnicalDetails("Registro staging completo", record)}
      </div>
    </div>
  `;
}

function syncFigureSegments(record, segments) {
  const signature = `${record?.record_id || ""}|${JSON.stringify((segments || []).map((seg) => seg.bbox_px || []))}`;
  if (state._figureSegmentSource === signature && state.figureSegmentDirty) return;
  if (state._figureSegmentSource !== signature || !state.figureSegmentDirty) {
    state.figureSegments = (segments || []).map((seg) => normalizeBox((seg.bbox_px || []).slice(0, 4).map((value) => Number(value))));
    state.figureSegments = state.figureSegments.filter((box) => box.length === 4 && box[2] > box[0] && box[3] > box[1]);
    state.selectedFigureSegment = state.figureSegments.length ? Math.max(0, Math.min(state.selectedFigureSegment, state.figureSegments.length - 1)) : -1;
    state.figureSegmentDirty = false;
    state.figureDrag = null;
    state._figureSegmentSource = signature;
    figureCanvasState = null;
  }
}

function renderFigureSegmentEditor(record) {
  return `
    <div class="figure-segment-editor">
      <div class="segment-editor-toolbar">
        <button id="figModeSelect" class="${state.figureSegmentMode === "select" ? "secondary" : ""}" type="button">Mover</button>
        <button id="figModeAdd" class="${state.figureSegmentMode === "add" ? "secondary" : ""}" type="button">Nuevo box</button>
        <button id="figDelete" type="button">Eliminar</button>
        <button id="figSave" class="primary" type="button" ${state.figureSegmentDirty ? "" : "disabled"}>Guardar segmentos</button>
        <span class="selection-count">${state.figureSegments.length} segmento(s)${state.figureSegmentDirty ? " | sin guardar" : ""}</span>
      </div>
      <div class="figure-canvas-wrap">
        ${record.crop_url ? `<canvas id="figureCanvas"></canvas>` : `<div class="empty-state">Sin crop disponible para editar segmentos.</div>`}
      </div>
    </div>
  `;
}

let figureCanvasState = null;
function bindFigureSegmentEditor(record) {
  const canvas = $("figureCanvas");
  if (!canvas || !record) return;
  $("figModeSelect").onclick = () => {
    state.figureSegmentMode = "select";
    renderOcrStage();
  };
  $("figModeAdd").onclick = () => {
    state.figureSegmentMode = "add";
    renderOcrStage();
  };
  $("figDelete").onclick = deleteSelectedFigureSegment;
  $("figSave").onclick = () => saveFigureSegments(record);
  document.querySelectorAll("[data-figure-segment]").forEach((item) => {
    item.onclick = () => {
      state.selectedFigureSegment = Number(item.dataset.figureSegment || 0);
      redrawFigureSegments();
      renderOcrStage();
    };
  });
  setupFigureCanvas(record);
}

function setupFigureCanvas(record) {
  const canvas = $("figureCanvas");
  if (!canvas) return;
  const wrapper = canvas.parentElement;
  const img = new Image();
  figureCanvasState = null;
  img.onload = () => {
    const availableWidth = Math.max(320, Math.floor((wrapper?.clientWidth || 720) - 20));
    const scale = Math.min(1.35, availableWidth / Math.max(1, img.naturalWidth));
    canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
    canvas.style.width = `${canvas.width}px`;
    canvas.style.height = `${canvas.height}px`;
    figureCanvasState = { canvas, ctx: canvas.getContext("2d"), img, scale };
    redrawFigureSegments();
  };
  img.onerror = () => {
    const ctx = canvas.getContext("2d");
    canvas.width = Math.max(320, Math.floor((wrapper?.clientWidth || 520) - 20));
    canvas.height = 180;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#637386";
    ctx.font = "600 14px Segoe UI";
    ctx.fillText("No se pudo cargar el crop para editar segmentos.", 16, 38);
  };
  img.src = record.crop_url;
  canvas.onmousedown = onFigureMouseDown;
  canvas.onmousemove = onFigureMouseMove;
  canvas.onmouseup = onFigureMouseUp;
  canvas.onmouseleave = onFigureMouseUp;
}

function redrawFigureSegments() {
  if (!figureCanvasState) return;
  const { canvas, ctx, img, scale } = figureCanvasState;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  state.figureSegments.forEach((box, idx) => {
    const [x1, y1, x2, y2] = box.map((value) => Math.round(value * scale));
    ctx.lineWidth = idx === state.selectedFigureSegment ? 4 : 3;
    ctx.strokeStyle = idx === state.selectedFigureSegment ? "#2dd4bf" : "#f59e0b";
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
    ctx.fillStyle = idx === state.selectedFigureSegment ? "#0f766e" : "#b45309";
    ctx.fillRect(x1, y1, 25, 23);
    ctx.fillStyle = "white";
    ctx.font = "bold 13px Segoe UI";
    ctx.fillText(String(idx + 1), x1 + 8, y1 + 16);
    if (idx === state.selectedFigureSegment) drawHandles(ctx, x1, y1, x2, y2);
  });
}

function figureCanvasPoint(event) {
  const rect = figureCanvasState.canvas.getBoundingClientRect();
  return { x: event.clientX - rect.left, y: event.clientY - rect.top };
}

function figureImagePoint(event) {
  const p = figureCanvasPoint(event);
  return { x: p.x / figureCanvasState.scale, y: p.y / figureCanvasState.scale };
}

function figureBounds() {
  if (!figureCanvasState) return { width: 1, height: 1 };
  return { width: figureCanvasState.img.naturalWidth, height: figureCanvasState.img.naturalHeight };
}

function clampFigureBoxToImage(box) {
  const bounds = figureBounds();
  const [x1, y1, x2, y2] = normalizeBox(box);
  return [
    Math.max(0, Math.min(bounds.width, x1)),
    Math.max(0, Math.min(bounds.height, y1)),
    Math.max(0, Math.min(bounds.width, x2)),
    Math.max(0, Math.min(bounds.height, y2)),
  ];
}

function findFigureSegmentAt(point) {
  const scale = figureCanvasState.scale;
  for (let i = state.figureSegments.length - 1; i >= 0; i -= 1) {
    const [x1, y1, x2, y2] = state.figureSegments[i].map((value) => value * scale);
    if (point.x >= x1 && point.x <= x2 && point.y >= y1 && point.y <= y2) return i;
  }
  return -1;
}

function findFigureHandleAt(point) {
  if (state.selectedFigureSegment < 0 || state.selectedFigureSegment >= state.figureSegments.length) return null;
  const scale = figureCanvasState.scale;
  const box = state.figureSegments[state.selectedFigureSegment].map((value) => value * scale);
  for (const handle of boxHandlePoints(box)) {
    if (Math.abs(point.x - handle.x) <= 9 && Math.abs(point.y - handle.y) <= 9) {
      return { index: state.selectedFigureSegment, handle: handle.name };
    }
  }
  return null;
}

function onFigureMouseDown(event) {
  if (!figureCanvasState) return;
  const canvasPoint = figureCanvasPoint(event);
  const imagePoint = figureImagePoint(event);
  if (state.figureSegmentMode === "add") {
    state.figureDrag = { type: "new", start: imagePoint, current: imagePoint };
    return;
  }
  const handleHit = findFigureHandleAt(canvasPoint);
  if (handleHit) {
    state.figureDrag = {
      type: "resize",
      handle: handleHit.handle,
      start: imagePoint,
      original: [...state.figureSegments[handleHit.index]],
    };
    return;
  }
  const hit = findFigureSegmentAt(canvasPoint);
  state.selectedFigureSegment = hit;
  if (hit >= 0) {
    state.figureDrag = { type: "move", start: imagePoint, original: [...state.figureSegments[hit]] };
  }
  redrawFigureSegments();
}

function onFigureMouseMove(event) {
  if (!figureCanvasState) return;
  const point = figureImagePoint(event);
  if (!state.figureDrag) {
    const canvas = figureCanvasState.canvas;
    canvas.style.cursor = findFigureHandleAt(figureCanvasPoint(event)) ? "nwse-resize" : (findFigureSegmentAt(figureCanvasPoint(event)) >= 0 ? "move" : (state.figureSegmentMode === "add" ? "crosshair" : "default"));
    return;
  }
  if (state.figureDrag.type === "new") {
    state.figureDrag.current = point;
    redrawFigureSegments();
    const box = clampFigureBoxToImage(normalizeBox([state.figureDrag.start.x, state.figureDrag.start.y, point.x, point.y]));
    const ctx = figureCanvasState.ctx;
    const scale = figureCanvasState.scale;
    ctx.strokeStyle = "#2dd4bf";
    ctx.lineWidth = 2;
    ctx.strokeRect(box[0] * scale, box[1] * scale, (box[2] - box[0]) * scale, (box[3] - box[1]) * scale);
  } else if (state.figureDrag.type === "move" && state.selectedFigureSegment >= 0) {
    const dx = point.x - state.figureDrag.start.x;
    const dy = point.y - state.figureDrag.start.y;
    state.figureSegments[state.selectedFigureSegment] = moveFigureBoxWithinImage(state.figureDrag.original, dx, dy);
    state.figureSegmentDirty = true;
    redrawFigureSegments();
  } else if (state.figureDrag.type === "resize" && state.selectedFigureSegment >= 0) {
    state.figureSegments[state.selectedFigureSegment] = resizeFigureBoxFromHandle(state.figureDrag.original, state.figureDrag.handle, point);
    state.figureSegmentDirty = true;
    redrawFigureSegments();
  }
}

function onFigureMouseUp() {
  if (!state.figureDrag) return;
  if (state.figureDrag.type === "new") {
    const box = clampFigureBoxToImage(normalizeBox([state.figureDrag.start.x, state.figureDrag.start.y, state.figureDrag.current.x, state.figureDrag.current.y]));
    if (box[2] - box[0] >= 12 && box[3] - box[1] >= 12) {
      state.figureSegments.push(box);
      state.selectedFigureSegment = state.figureSegments.length - 1;
      state.figureSegmentDirty = true;
    }
  }
  state.figureDrag = null;
  redrawFigureSegments();
  renderOcrStage();
}

function moveFigureBoxWithinImage(box, dx, dy) {
  const bounds = figureBounds();
  const width = box[2] - box[0];
  const height = box[3] - box[1];
  const x1 = Math.max(0, Math.min(bounds.width - width, box[0] + dx));
  const y1 = Math.max(0, Math.min(bounds.height - height, box[1] + dy));
  return normalizeBox([x1, y1, x1 + width, y1 + height]);
}

function resizeFigureBoxFromHandle(box, handle, point) {
  let [x1, y1, x2, y2] = box;
  const minSize = 12;
  if (handle.includes("w")) x1 = Math.min(point.x, x2 - minSize);
  if (handle.includes("e")) x2 = Math.max(point.x, x1 + minSize);
  if (handle.includes("n")) y1 = Math.min(point.y, y2 - minSize);
  if (handle.includes("s")) y2 = Math.max(point.y, y1 + minSize);
  return clampFigureBoxToImage([x1, y1, x2, y2]);
}

function deleteSelectedFigureSegment() {
  if (state.selectedFigureSegment < 0 || state.selectedFigureSegment >= state.figureSegments.length) return;
  state.figureSegments.splice(state.selectedFigureSegment, 1);
  state.selectedFigureSegment = Math.min(state.selectedFigureSegment, state.figureSegments.length - 1);
  state.figureSegmentDirty = true;
  renderOcrStage();
}

async function saveFigureSegments(record) {
  if (!record) return;
  await runAction("Guardando segmentos graficos revisados...", async () => {
    state.snapshot = await api("/api/ocr/segments/boxes", {
      method: "POST",
      body: {
        record_id: record.record_id,
        boxes: state.figureSegments,
      },
    });
    state.figureSegmentDirty = false;
    state._figureSegmentSource = "";
    persistFactoryUiState();
    render();
  }, "Segmentos graficos revisados guardados.");
}

function renderOcrLatexViewer(payload, index, total) {
  const options = payload.options || {};
  const optionRows = ["A", "B", "C", "D", "E"].map((key) => `
    <div class="option-row"><strong>${key}</strong><span>${formatLatexPreviewText(options[key] || "-")}</span></div>
  `).join("");
  return `
    <div id="ocrLatexPreview" class="latex-preview ocr-latex-preview">
      <article class="preview-problem">
        <header>
          <strong>${escapeHtml(payload.n || `Lectura ${index + 1}`)}</strong>
          <span>${escapeHtml(`Item ${index + 1} de ${total}`)}</span>
        </header>
        <div class="statement-preview">${formatPreviewText(payload.statement || "")}</div>
      </article>
      <div class="options-preview">${optionRows}</div>
      <footer class="ocr-latex-footer">
        <span><strong>Clave:</strong> ${formatLatexPreviewText(payload.answer_key || "-")}</span>
        <span>${payload.has_figure ? "con grafico" : "sin grafico"}</span>
      </footer>
    </div>
  `;
}

function renderRawOcrPanel(record) {
  const raw = String(record?.raw_ocr || "").trim();
  return `
    <section class="panel raw-ocr-panel">
      <div class="panel-heading-row">
        <div>
          <h3>OCR crudo editable</h3>
          <p class="muted">${raw ? `${raw.length} caracter(es) guardados.` : "Todavia no hay texto crudo guardado para esta imagen."}</p>
        </div>
        <div class="raw-ocr-actions">
          <button id="copyRawOcr" type="button" ${raw ? "" : "disabled"}>Copiar OCR</button>
          <button id="saveRawOcr" class="primary" type="button">Guardar OCR crudo</button>
        </div>
      </div>
      <textarea id="rawOcrEditor" class="raw-ocr-editor" spellcheck="false" placeholder="Pega o corrige aqui el OCR crudo de esta imagen.">${escapeHtml(raw)}</textarea>
    </section>
  `;
}

function bindRawOcrActions(record) {
  const copyBtn = $("copyRawOcr");
  const saveBtn = $("saveRawOcr");
  const editor = $("rawOcrEditor");
  if (copyBtn) copyBtn.onclick = async () => {
    const raw = String(editor?.value || record?.raw_ocr || "");
    if (!raw.trim()) return;
    try {
      await navigator.clipboard.writeText(raw);
      setStatus("OCR crudo copiado.");
    } catch (_) {
      setStatus("No se pudo copiar automaticamente el OCR crudo.");
    }
  };
  if (saveBtn) saveBtn.onclick = () => saveRawOcr(record);
}

async function saveRawOcr(record) {
  if (!record?.record_id) return;
  const raw = $("rawOcrEditor")?.value || "";
  await runAction("Guardando OCR crudo revisado...", async () => {
    const payload = await api("/api/ocr/raw", {
      method: "POST",
      body: {
        record_id: record.record_id,
        raw_ocr: raw,
      },
    });
    applyFactorySnapshot(payload);
    const updated = findRecordById(record.record_id, state.snapshot?.records || []);
    if (updated) {
      updated.raw_ocr = raw;
    }
    state.stage = "ocr";
    state.selectedRecordId = record.record_id;
    state.reviewDraft = null;
    persistFactoryUiState();
    render();
    return "OCR crudo guardado en pantalla y en staging. El paso 5 preparara un borrador revisable desde este texto.";
  }, "OCR crudo guardado. El paso 5 preparara un borrador revisable desde este texto.");
}

function batchModeConfig(mode = state.batchMode) {
  if (mode === "final_latex") {
    return {
      mode: "final_latex",
      title: "Formato final en bloque",
      description: "Pega el formato LaTeX final por imagen. Se guarda como salida final para futura BD y como ejemplo de entrenamiento.",
      action: "Guardar formato final",
      empty: "No hay registros de staging para guardar formato final.",
      placeholder: "----imagen.png-----\n\\item[\\textbf{01.}] [[curso=Geometria]] [[tema=Triangulos]] [[Estado=sin_revisar]] [[Clave=C]] Enunciado... [[Imagen=img-01]] £A)...æB)...æC)...£D)...ææE)...£",
      saving: "Guardando formato final por lote...",
      done: "Formato final por lote terminado.",
    };
  }
  if (mode === "normalization") {
    return {
      mode: "normalization",
      title: "Normalizar en grupo",
      description: "Edita varios borradores de revision desde OCR en un solo texto. Estas correcciones serviran para entrenar el normalizador futuro.",
      action: "Guardar validos",
      empty: "No hay registros de staging con OCR para revisar.",
      placeholder: "----imagen.png-----\n{\n  \"schema_version\": \"normalized_problem_staging_v1\",\n  \"numero\": \"01\",\n  \"respuesta_final\": \"\"\n}",
      saving: "Guardando normalizacion por lote...",
      done: "Normalizacion por lote terminada.",
    };
  }
  return {
    mode: "raw_ocr",
    title: "Modo lote OCR crudo",
    description: "Edita el OCR crudo de todos los crops. Al guardar, cada bloque regenera su lectura LaTeX.",
    action: "Guardar validos",
    empty: "No hay registros de staging para editar OCR crudo.",
    placeholder: "----imagen.png-----\n<01.> Texto... A) ... B) ...",
    saving: "Guardando OCR crudo por lote...",
    done: "OCR crudo por lote terminado.",
  };
}

function openBatchMode(mode) {
  state.batchMode = mode;
  state.batchText = buildBatchText(mode);
  state.batchResults = [];
  state.taskProgress = null;
  render();
  setStatus(batchModeConfig(mode).title);
}

function closeBatchMode({ rerender = true } = {}) {
  state.batchMode = "";
  state.batchText = "";
  state.batchResults = [];
  state.taskProgress = null;
  if (rerender) render();
}

function renderBatchEditor() {
  const config = batchModeConfig();
  const records = state.snapshot?.records || [];
  if (!state.batchText && !state.batchResults.length && records.length) {
    state.batchText = buildBatchText(config.mode);
  }
  $("stageHost").innerHTML = `
    <div class="stage-header">
      <div>
        <h2>${escapeHtml(config.title)}</h2>
        <p class="muted">${escapeHtml(config.description)}</p>
      </div>
      <div class="stage-actions">
        <button id="closeBatchMode" type="button">Volver</button>
      </div>
    </div>
    ${renderTaskProgress("batch")}
    <section class="panel batch-editor-panel">
      <div class="panel-heading-row">
        <div>
          <h3>Editor por lote</h3>
          <p class="muted">${records.length ? `${records.length} imagen(es) en el lote. El guardado usa el orden actual de staging.` : config.empty}</p>
        </div>
        <div class="batch-actions">
          <button id="copyBatchText" type="button" ${records.length ? "" : "disabled"}>Copiar lote</button>
          <button id="resetBatchText" type="button" ${records.length ? "" : "disabled"}>Regenerar desde staging</button>
          <button id="saveBatchText" class="primary" type="button" ${records.length ? "" : "disabled"}>${escapeHtml(config.action)}</button>
        </div>
      </div>
      <textarea id="batchEditor" class="batch-editor" spellcheck="false" placeholder="${escapeAttr(config.placeholder || "")}">${escapeHtml(state.batchText || "")}</textarea>
      <div id="batchProgressInline" class="batch-progress-inline muted">${records.length ? `0 de ${records.length}` : "Sin registros."}</div>
      ${renderBatchResults()}
    </section>
  `;
  bindBatchEditorActions(config.mode);
  setInspector({
    "Modo": config.title,
    "Registros": records.length,
    "Mapeo": "orden de staging",
  });
  syncWorkspaceMode();
  syncPrimaryAction();
}

function bindBatchEditorActions(mode) {
  const editor = $("batchEditor");
  if (editor) {
    editor.oninput = () => {
      state.batchText = editor.value;
    };
  }
  const closeBtn = $("closeBatchMode");
  if (closeBtn) closeBtn.onclick = () => closeBatchMode();
  const copyBtn = $("copyBatchText");
  if (copyBtn) copyBtn.onclick = copyBatchText;
  const resetBtn = $("resetBatchText");
  if (resetBtn) resetBtn.onclick = () => {
    state.batchText = buildBatchText(mode);
    state.batchResults = [];
    renderBatchEditor();
    setStatus("Lote regenerado desde staging.");
  };
  const saveBtn = $("saveBatchText");
  if (saveBtn) saveBtn.onclick = () => saveBatchEditor(mode);
}

async function copyBatchText() {
  const text = $("batchEditor")?.value || state.batchText || "";
  if (!text.trim()) return;
  try {
    await navigator.clipboard.writeText(text);
    setStatus("Lote copiado.");
  } catch (_) {
    setStatus("No se pudo copiar automaticamente el lote.");
  }
}

function buildBatchText(mode) {
  const records = state.snapshot?.records || [];
  return records.map((record, index) => {
    let body = String(record.raw_ocr || "").trim();
    if (mode === "normalization") body = reviewJsonFromNormalized(normalizedForBatchRecord(record), record);
    if (mode === "final_latex") body = finalLatexFromRecord(record);
    return `----${batchRecordTitle(record, index)}-----\n${body}`.trimEnd();
  }).join("\n\n");
}

function finalLatexFromRecord(record) {
  const normalized = normalizedForBatchRecord(record);
  const rendered = String(normalized.latex_rendered_item || "").trim();
  if (rendered) return rendered;
  const number = String(normalized.numero || "").trim();
  const curso = String(normalized.curso || "SIN_CURSO").trim() || "SIN_CURSO";
  const tema = String(normalized.tema || "SIN_TEMA").trim() || "SIN_TEMA";
  const estado = String(normalized.estado || "sin_revisar").trim() || "sin_revisar";
  const clave = String(normalized.respuesta_correcta || "").trim();
  const statement = String(normalized.enunciado_latex || "").trim();
  const image = normalized.tiene_grafico
    ? ` [[Imagen=${String(normalized.figure_tag || `img-${number || record.record_id}`).trim()}]]`
    : "";
  const alternatives = normalized.alternativas && typeof normalized.alternativas === "object" ? normalized.alternativas : {};
  const optionText = `£A)${String(alternatives.A || "").trim()}æB)${String(alternatives.B || "").trim()}æC)${String(alternatives.C || "").trim()}£D)${String(alternatives.D || "").trim()}ææE)${String(alternatives.E || "").trim()}£`;
  return `\\item[\\textbf{${number || ""}.}] [[curso=${curso}]] [[tema=${tema}]] [[Estado=${estado}]] [[Clave=${clave}]] ${statement}${image} ${optionText}`.trim();
}

function finalReviewTextFromRecord(record) {
  const normalized = normalizedForBatchRecord(record);
  const rendered = String(normalized.latex_rendered_item || "").trim();
  if (rendered) return rendered;
  const continuation = continuationTextFromRecord(record);
  if (continuation) return continuation;
  return finalLatexFromRecord(record);
}

function normalizedForBatchRecord(record) {
  if (hasObjectData(record?.normalized)) return record.normalized;
  const firstItem = selectedOcrPayload(record, 0);
  if (hasObjectData(firstItem)) return normalizedFromOcr(record, firstItem);
  return {
    numero: "",
    curso: "",
    tema: "",
    enunciado_latex: "",
    alternativas: { A: "", B: "", C: "", D: "", E: "" },
    respuesta_correcta: "",
    tiene_grafico: Boolean((record?.figure_segments_web || []).length),
    figure_tag: "",
  };
}

function batchRecordTitle(record, index) {
  const name = String(record?.crop_name || "").trim()
    || String(record?.crop_path || "").split(/[\\/]/).filter(Boolean).pop()
    || String(record?.record_id || "").trim()
    || `imagen_${index + 1}`;
  return name.replace(/\s+/g, " ").trim();
}

function parseBatchBlocks(text) {
  const lines = String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  const blocks = [];
  const preamble = [];
  let current = null;
  lines.forEach((line) => {
    const match = line.match(/^-{4,}\s*(.*?)\s*-{4,}\s*$/);
    if (match) {
      if (current) blocks.push({ ...current, body: current.lines.join("\n").trim() });
      current = { title: match[1].trim(), lines: [] };
      return;
    }
    if (current) current.lines.push(line);
    else if (line.trim()) preamble.push(line);
  });
  if (current) blocks.push({ ...current, body: current.lines.join("\n").trim() });
  return { blocks, preamble: preamble.join("\n").trim() };
}

function normalizeBatchTitle(value) {
  return String(value || "")
    .trim()
    .replace(/\s+/g, " ")
    .toLowerCase();
}

function takeBatchBlockForRecord(blocks, usedBlocks, record, recordIndex, allowSequentialFallback = true) {
  const titleKey = normalizeBatchTitle(batchRecordTitle(record, recordIndex));
  const directIndex = (blocks || []).findIndex((block, blockIndex) => (
    !usedBlocks.has(blockIndex) && normalizeBatchTitle(block.title) === titleKey
  ));
  if (directIndex >= 0) {
    usedBlocks.add(directIndex);
    return { block: blocks[directIndex], index: directIndex, matchedTitle: true };
  }
  if (!allowSequentialFallback) return null;
  const nextIndex = (blocks || []).findIndex((_, blockIndex) => !usedBlocks.has(blockIndex));
  if (nextIndex < 0) return null;
  usedBlocks.add(nextIndex);
  return { block: blocks[nextIndex], index: nextIndex, matchedTitle: false };
}

function takeNextContinuationBlock(blocks, usedBlocks) {
  const nextIndex = (blocks || []).findIndex((_, blockIndex) => !usedBlocks.has(blockIndex));
  if (nextIndex < 0) return null;
  if (!isFinalLatexContinuation(blocks[nextIndex].body)) return null;
  usedBlocks.add(nextIndex);
  return { block: blocks[nextIndex], index: nextIndex, matchedTitle: false };
}

function renderBatchResults() {
  const results = state.batchResults || [];
  if (!results.length) {
    return `<div id="batchResults" class="batch-results muted">Sin resultados todavia.</div>`;
  }
  return `
    <div id="batchResults" class="batch-results">
      ${results.map((row) => `
        <div class="batch-result ${escapeAttr(row.status || "info")}">
          <strong>${escapeHtml(row.title || row.record_id || "-")}</strong>
          <span>${escapeHtml(row.message || "")}</span>
        </div>
      `).join("")}
    </div>
  `;
}

function updateBatchResultsInline() {
  const host = $("batchResults");
  if (!host) return;
  host.outerHTML = renderBatchResults();
}

function updateBatchProgressInline({ current, total, ok, failed, skipped, active }) {
  const host = $("batchProgressInline");
  if (!host) return;
  const parts = [`${current} de ${total}`, `${ok} guardado(s)`];
  if (skipped) parts.push(`${skipped} omitido(s)`);
  if (failed) parts.push(`${failed} error(es)`);
  if (active) parts.push(active);
  host.textContent = parts.join(" | ");
}

function replaceRecordInSnapshot(updated) {
  if (!updated?.record_id || !state.snapshot?.records) return;
  const records = state.snapshot.records || [];
  const index = records.findIndex((record) => String(record.record_id || "") === String(updated.record_id || ""));
  if (index >= 0) records[index] = updated;
}

async function saveBatchEditor(mode = state.batchMode) {
  const records = state.snapshot?.records || [];
  if (!records.length) return setStatus("No hay registros de staging para guardar.");
  const editor = $("batchEditor");
  state.batchText = editor?.value || state.batchText || "";
  const parsed = parseBatchBlocks(state.batchText);
  const total = Math.max(records.length, parsed.blocks.length);
  const results = [];
  let ok = 0;
  let failed = 0;
  let skipped = 0;
  const saveBtn = $("saveBatchText");
  if (saveBtn) saveBtn.disabled = true;
  setBusy(batchModeConfig(mode).saving);
  try {
    if (parsed.preamble) {
      failed += 1;
      results.push({ status: "error", title: "Texto sin separador", message: "Hay texto antes del primer separador; no se guardo." });
      state.batchResults = results.slice();
      updateBatchResultsInline();
    }
    if (mode === "final_latex") {
      await saveFinalLatexBatchEditor(records, parsed, { results, failed, saveBtn });
      return;
    }
    for (let index = 0; index < total; index += 1) {
      const record = records[index];
      const block = parsed.blocks[index];
      const title = record ? batchRecordTitle(record, index) : (block?.title || `Bloque extra ${index + 1}`);
      updateBatchProgressInline({ current: index, total, ok, failed, skipped, active: title });
      if (!record) {
        failed += 1;
        results.push({ status: "error", title, message: "Bloque sobrante: no existe un registro de staging en esa posicion." });
        state.batchResults = results.slice();
        updateBatchResultsInline();
        continue;
      }
      if (!block) {
        failed += 1;
        results.push({ status: "error", title, message: "Falta el bloque para esta imagen." });
        state.batchResults = results.slice();
        updateBatchResultsInline();
        continue;
      }
      if (recordSourceStale(record) || recordDownstreamInvalidated(record)) {
        skipped += 1;
        results.push({ status: "skipped", title, message: "Regenera staging antes de editar este registro." });
        state.batchResults = results.slice();
        updateBatchResultsInline();
        continue;
      }
      if (!String(block.body || "").trim()) {
        skipped += 1;
        results.push({ status: "skipped", title, message: "Bloque vacio; no se guardo para evitar borrar el registro." });
        state.batchResults = results.slice();
        updateBatchResultsInline();
        continue;
      }
      try {
        const updated = mode === "normalization"
          ? await saveBatchNormalizationRecord(record, block.body)
          : await saveBatchRawOcrRecord(record, block.body);
        replaceRecordInSnapshot(updated);
        ok += 1;
        results.push({ status: "ok", title, message: "Guardado." });
      } catch (err) {
        failed += 1;
        results.push({ status: "error", title, message: err.message || String(err) });
      }
      state.batchResults = results.slice();
      updateBatchResultsInline();
      updateBatchProgressInline({ current: index + 1, total, ok, failed, skipped, active: title });
    }
    try {
      applyFactorySnapshot(await loadFactorySnapshot());
      await refreshNormalizerTrainingStatus({ silent: true });
    } catch (err) {
      failed += 1;
      results.push({ status: "error", title: "Actualizacion final", message: err.message || String(err) });
    }
    state.batchResults = results;
    state.taskProgress = null;
    renderBatchEditor();
    setStatus(`Lote terminado: ${ok} guardado(s), ${skipped} omitido(s), ${failed} error(es).`);
  } catch (err) {
    setStatus(`Error: ${err.message || String(err)}`);
  } finally {
    if (saveBtn) saveBtn.disabled = false;
    const busy = $("busyText");
    if (busy) busy.textContent = "";
  }
}

async function saveFinalLatexBatchEditor(records, parsed, initial = {}) {
  const results = Array.isArray(initial.results) ? initial.results : [];
  let ok = 0;
  let failed = Number(initial.failed || 0);
  let skipped = 0;
  let lastFinalLatexPrimary = null;
  let lastFinalLatexBody = "";
  let lastFinalLatexTitle = "";
  const usedBlocks = new Set();
  const total = records.length;
  for (let index = 0; index < records.length; index += 1) {
    const record = records[index];
    const title = batchRecordTitle(record, index);
    updateBatchProgressInline({ current: index, total, ok, failed, skipped, active: title });
    if (recordSourceStale(record) || recordDownstreamInvalidated(record)) {
      skipped += 1;
      results.push({ status: "skipped", title, message: "Regenera staging antes de editar este registro." });
      state.batchResults = results.slice();
      updateBatchResultsInline();
      continue;
    }
    const recordContinuation = continuationTextFromRecord(record);
    let picked = takeBatchBlockForRecord(parsed.blocks, usedBlocks, record, index, !recordContinuation);
    if (!picked && recordContinuation) {
      picked = takeNextContinuationBlock(parsed.blocks, usedBlocks);
    }
    if (!picked && recordContinuation) {
      try {
        if (!lastFinalLatexPrimary) {
          throw new Error("[CONT.] no tiene un problema anterior para fusionar.");
        }
        const continuationBody = stripFinalContinuationMarker(recordContinuation) || "[sin texto OCR visible]";
        const updatedPrimary = await saveBatchFinalLatexRecord(
          lastFinalLatexPrimary,
          lastFinalLatexBody,
          {
            notes: `Formato final conserva continuacion omitida como bloque: ${record.record_id}.`,
            continuationRecord: record,
            continuationText: continuationBody,
          },
        );
        replaceRecordInSnapshot(updatedPrimary);
        lastFinalLatexPrimary = updatedPrimary;
        const updated = await saveBatchContinuationRecord(record, recordContinuation, lastFinalLatexPrimary);
        replaceRecordInSnapshot(updated);
        ok += 1;
        results.push({ status: "ok", title, message: `Continuacion fusionada con ${lastFinalLatexTitle || lastFinalLatexPrimary.record_id}.` });
      } catch (err) {
        failed += 1;
        results.push({ status: "error", title, message: err.message || String(err) });
      }
      state.batchResults = results.slice();
      updateBatchResultsInline();
      updateBatchProgressInline({ current: index + 1, total, ok, failed, skipped, active: title });
      continue;
    }
    if (!picked) {
      failed += 1;
      results.push({ status: "error", title, message: "Falta el bloque para esta imagen." });
      state.batchResults = results.slice();
      updateBatchResultsInline();
      continue;
    }
    const block = picked.block;
    if (!String(block.body || "").trim()) {
      skipped += 1;
      results.push({ status: "skipped", title, message: "Bloque vacio; no se guardo para evitar borrar el registro." });
      state.batchResults = results.slice();
      updateBatchResultsInline();
      continue;
    }
    try {
      if (isFinalLatexContinuation(block.body)) {
        if (!lastFinalLatexPrimary) {
          throw new Error("[CONT.] no tiene un problema anterior para fusionar.");
        }
        const continuationBody = stripFinalContinuationMarker(block.body);
        if (!continuationBody) {
          throw new Error("[CONT.] no contiene texto para fusionar.");
        }
        lastFinalLatexBody = mergeFinalLatexContinuation(lastFinalLatexBody, continuationBody);
        const updatedPrimary = await saveBatchFinalLatexRecord(
          lastFinalLatexPrimary,
          lastFinalLatexBody,
          {
            notes: `Formato final actualizado con continuacion de ${record.record_id}.`,
            continuationRecord: record,
            continuationText: continuationBody,
          },
        );
        replaceRecordInSnapshot(updatedPrimary);
        lastFinalLatexPrimary = updatedPrimary;
        const updated = await saveBatchContinuationRecord(record, block.body, lastFinalLatexPrimary);
        replaceRecordInSnapshot(updated);
        results.push({ status: "ok", title, message: `Fusionado con ${lastFinalLatexTitle || lastFinalLatexPrimary.record_id}.` });
      } else {
        const updated = await saveBatchFinalLatexRecord(record, block.body);
        replaceRecordInSnapshot(updated);
        lastFinalLatexPrimary = updated;
        lastFinalLatexBody = String(block.body || "").trim();
        lastFinalLatexTitle = title;
        results.push({ status: "ok", title, message: picked.matchedTitle ? "Guardado." : "Guardado por orden." });
      }
      ok += 1;
    } catch (err) {
      failed += 1;
      results.push({ status: "error", title, message: err.message || String(err) });
    }
    state.batchResults = results.slice();
    updateBatchResultsInline();
    updateBatchProgressInline({ current: index + 1, total, ok, failed, skipped, active: title });
  }
  (parsed.blocks || []).forEach((block, blockIndex) => {
    if (usedBlocks.has(blockIndex)) return;
    failed += 1;
    results.push({ status: "error", title: block.title || `Bloque extra ${blockIndex + 1}`, message: "Bloque sobrante: no corresponde a ningun registro de staging." });
  });
  try {
    applyFactorySnapshot(await loadFactorySnapshot());
    await refreshNormalizerTrainingStatus({ silent: true });
  } catch (err) {
    failed += 1;
    results.push({ status: "error", title: "Actualizacion final", message: err.message || String(err) });
  }
  state.batchResults = results;
  state.taskProgress = null;
  renderBatchEditor();
  setStatus(`Lote terminado: ${ok} guardado(s), ${skipped} omitido(s), ${failed} error(es).`);
}

async function saveBatchRawOcrRecord(record, raw) {
  const payload = await api("/api/ocr/raw", {
    method: "POST",
    body: {
      record_id: record.record_id,
      raw_ocr: raw,
      compact: true,
    },
  });
  return payload.record;
}

async function saveBatchNormalizationRecord(record, text) {
  const parsed = parseReviewJsonText(text, record);
  const payload = await api("/api/review/save", {
    method: "POST",
    body: {
      record_id: record.record_id,
      normalized: {
        ...parsed.normalized,
        status: parsed.markReady ? "listo" : String(parsed.normalized.status || "requiere_revision"),
      },
      notes: parsed.notes,
      mark_ready: parsed.markReady,
      compact: true,
      defer_golden_sync: true,
    },
  });
  return payload.record;
}

async function saveBatchFinalLatexRecord(record, text, options = {}) {
  const finalLatex = String(text || "").trim();
  if (!finalLatex) throw new Error("Formato final vacio.");
  const parsed = parseFinalLatexItem(finalLatex, normalizedForBatchRecord(record), record);
  const normalized = {
    ...parsed.normalized,
    latex_rendered_item: finalLatex,
    status: "listo",
  };
  if (options.continuationRecord) {
    const continuationRecord = options.continuationRecord;
    const source = continuationRecord?.source && typeof continuationRecord.source === "object"
      ? continuationRecord.source
      : {};
    const previous = Array.isArray(normalized.continuaciones_fusionadas)
      ? normalized.continuaciones_fusionadas.filter((item) => String(item?.record_id || "") !== String(continuationRecord?.record_id || ""))
      : [];
    normalized.continuaciones_fusionadas = [
      ...previous,
      {
        record_id: String(continuationRecord?.record_id || ""),
        crop_id: String(continuationRecord?.crop_id || ""),
        crop_name: batchRecordTitle(continuationRecord, previous.length),
        page_number: source.page_number ?? null,
        bbox_px: Array.isArray(source.bbox_px) ? source.bbox_px : null,
        texto_fusionado: String(options.continuationText || "").trim(),
      },
    ];
  }
  const payload = await api("/api/review/save", {
    method: "POST",
    body: {
      record_id: record.record_id,
      normalized,
      notes: String(options.notes || parsed.notes || ""),
      mark_ready: true,
      compact: true,
      defer_golden_sync: true,
    },
  });
  return payload.record;
}

async function saveBatchContinuationRecord(record, text, parentRecord) {
  const continuationText = stripFinalContinuationMarker(text);
  const normalized = normalizedJsonForReview(normalizedForBatchRecord(record), record);
  normalized.status = "listo";
  normalized.continuacion = {
    ...(normalized.continuacion && typeof normalized.continuacion === "object" ? normalized.continuacion : {}),
    es_continuacion: true,
    fusionar_con_anterior: true,
    parent_record_id: parentRecord?.record_id || "",
  };
  normalized.enunciado_latex = continuationText;
  normalized.latex_rendered_item = "";
  const payload = await api("/api/review/save", {
    method: "POST",
    body: {
      record_id: record.record_id,
      normalized,
      notes: `Continuacion fusionada con ${parentRecord?.record_id || "registro anterior"}.`,
      mark_ready: true,
      compact: true,
      defer_golden_sync: true,
    },
  });
  return payload.record;
}

function isFinalLatexContinuation(text) {
  return /^\s*\[CONT\.?\]/i.test(String(text || ""));
}

function continuationTextFromRecord(record) {
  const normalized = record?.normalized && typeof record.normalized === "object" ? record.normalized : {};
  const continuation = normalized.continuacion && typeof normalized.continuacion === "object"
    ? normalized.continuacion
    : {};
  const candidates = [
    record?.raw_ocr,
    normalized.enunciado_latex,
    normalized.latex_rendered_item,
  ].map((value) => String(value || "").trim()).filter(Boolean);
  const explicit = candidates.find((value) => isFinalLatexContinuation(value));
  if (explicit) return explicit;
  if (continuation.es_continuacion || continuation.fusionar_con_anterior) {
    return `[CONT.] ${candidates[0] || "[sin texto OCR visible]"}`.trim();
  }
  return "";
}

function stripFinalContinuationMarker(text) {
  return String(text || "").replace(/^\s*\[CONT\.?\]\s*/i, "").trim();
}

function mergeFinalLatexContinuation(primaryText, continuationText) {
  const primary = String(primaryText || "").trim();
  const continuation = String(continuationText || "").trim();
  if (!primary) return continuation;
  if (!continuation) return primary;
  return `${primary}\n${continuation}`.trim();
}

function parseFinalLatexItem(text, base, record) {
  const normalized = normalizedJsonForReview(base, record);
  const finalLatex = String(text || "").trim();
  const numberMatch = finalLatex.match(/\\item\s*\[\s*\\textbf\{\s*([^}.]+)\.?\s*\}\s*\]/i);
  if (numberMatch) normalized.numero = numberMatch[1].trim();
  const tags = [...finalLatex.matchAll(/\[\[\s*([^=\]]+?)\s*=\s*([^\]]*?)\s*\]\]/g)];
  tags.forEach((match) => {
    const key = normalizePlainLabel(match[1]);
    const value = String(match[2] || "").trim();
    if (key === "curso") normalized.curso = value || "SIN_CURSO";
    else if (key === "tema") normalized.tema = value || "SIN_TEMA";
    else if (key === "subtema") normalized.subtema = value;
    else if (key === "estado") normalized.estado = value || "sin_revisar";
    else if (key === "clave") normalized.respuesta_correcta = normalizeAnswerValue(value);
    else if (key === "imagen") {
      normalized.tiene_grafico = true;
      normalized.figure_tag = value;
    }
  });
  normalized.respuesta_final = String(normalized.respuesta_final || "").trim()
    || String(normalized.alternativas?.[normalized.respuesta_correcta] || "").trim();
  normalized.latex_rendered_item = finalLatex;
  normalized.status = "listo";
  return {
    normalized,
    notes: "Formato final pegado en bloque.",
    markReady: true,
  };
}

function renderTechnicalDetails(title, payload, mode = "json") {
  const body = mode === "text" ? String(payload ?? "") : JSON.stringify(payload || {}, null, 2);
  return `
    <details class="technical-details">
      <summary>${escapeHtml(title)}</summary>
      <div class="codebox">${escapeHtml(body)}</div>
    </details>
  `;
}

function continuationRecordsForParent(parent, allRecords = state.snapshot?.records || []) {
  if (!parent) return [];
  const parentId = String(parent.record_id || "");
  const normalized = parent.normalized && typeof parent.normalized === "object" ? parent.normalized : {};
  const fused = Array.isArray(normalized.continuaciones_fusionadas) ? normalized.continuaciones_fusionadas : [];
  const wantedIds = new Set(fused.map((item) => String(item?.record_id || "").trim()).filter(Boolean));
  const rows = [];
  const seen = new Set();
  const addRecord = (record) => {
    if (!record || !isReviewContinuationRecord(record)) return;
    const id = String(record.record_id || "");
    if (!id || seen.has(id)) return;
    rows.push(record);
    seen.add(id);
  };
  wantedIds.forEach((id) => addRecord(findRecordById(id, allRecords)));
  allRecords.forEach((record) => {
    const continuation = record?.normalized?.continuacion;
    const continuationParentId = continuation && typeof continuation === "object"
      ? String(continuation.parent_record_id || "").trim()
      : "";
    if (continuationParentId === parentId) addRecord(record);
  });
  const parentIndex = allRecords.findIndex((record) => String(record.record_id || "") === parentId);
  if (parentIndex >= 0) {
    for (let index = parentIndex + 1; index < allRecords.length; index += 1) {
      const candidate = allRecords[index];
      if (!isReviewContinuationRecord(candidate)) break;
      addRecord(candidate);
    }
  }
  return rows;
}

function renderReviewImageStack(record, allRecords = state.snapshot?.records || []) {
  const images = [record, ...continuationRecordsForParent(record, allRecords)].filter(Boolean);
  if (!images.length) return `<p class="muted">Imagen no encontrada.</p>`;
  return `
    <div class="review-image-stack">
      ${images.map((item, index) => `
        <figure class="review-image-frame ${index > 0 ? "continuation" : "primary"}">
          <figcaption>${index === 0 ? "Imagen principal" : `Continuacion ${index}`}</figcaption>
          ${item.crop_url
            ? `<img class="preview-img" src="${item.crop_url}" alt="${index === 0 ? "Problema" : "Continuacion"}" loading="lazy" decoding="async" />`
            : `<p class="muted">Imagen no encontrada.</p>`}
        </figure>
      `).join("")}
    </div>
  `;
}

function renderReviewStage() {
  syncSelectedRecord();
  const allRecords = state.snapshot.records || [];
  const records = reviewRecords(allRecords);
  const record = ensureReviewSelectedRecord(records, allRecords);
  const currentRecordIndex = recordIndex(records);
  const totalRecords = records.length;
  const normalized = state.reviewDraft || (record ? record.normalized || {} : {});
  const finalLatexText = record ? finalReviewTextFromRecord(record) : "";
  const errorComment = recordErrorComment(record);
  $("stageHost").innerHTML = `
    <div class="stage-header">
      <div>
        <h2>Revision del formato final</h2>
        <p class="muted">Corrige el item LaTeX final, revisa el render matematico y guarda la salida que quedara lista para futura BD.</p>
      </div>
      <div class="stage-actions">
        <button id="openReviewBatch" type="button">Normalizar en grupo</button>
        <button id="openFinalLatexBatch" type="button">Formato final en bloque</button>
      </div>
    </div>
    ${renderTaskProgress("normalize")}
    ${record ? `
      <div class="library-notice">
        Esta etapa guarda el formato final en staging y conserva la correccion como dato de entrenamiento.
      </div>
      ${errorComment ? `<div class="library-notice error-notice"><strong>Error:</strong> ${escapeHtml(errorComment)}</div>` : ""}
      <div class="record-nav panel review-record-nav" aria-label="Navegacion de problemas en revision">
        <button id="prevRecord" class="nav-arrow" type="button" title="Problema anterior" ${currentRecordIndex <= 0 ? "disabled" : ""}>&larr;</button>
        <div class="record-nav-main">
          <span class="section-label">Problema en revision</span>
          <strong>${totalRecords ? currentRecordIndex + 1 : 0} de ${totalRecords}</strong>
          <span class="muted">${escapeHtml(recordOptionLabel(record, currentRecordIndex))}</span>
        </div>
        <button id="nextRecord" class="nav-arrow" type="button" title="Problema siguiente" ${currentRecordIndex >= totalRecords - 1 ? "disabled" : ""}>&rarr;</button>
        <label class="record-jump-label">
          <span class="muted">Saltar a</span>
          <select id="recordJump">
            ${records.map((row, index) => `<option value="${index}" ${index === currentRecordIndex ? "selected" : ""}>${escapeHtml(recordOptionLabel(row, index))}</option>`).join("")}
          </select>
        </label>
      </div>
      <div class="record-layout">
        <div>
          ${renderReviewImageStack(record, allRecords)}
          ${renderTechnicalDetails("OCR crudo", record.raw_ocr || "(sin OCR crudo)", "text")}
          <details class="technical-details">
            <summary>Contexto tecnico del registro</summary>
            <div class="codebox">${escapeHtml(JSON.stringify({
              record_id: record.record_id,
              status: record.status,
              training_examples_total: (record.training_examples || []).length,
              normalized_metadata: record.normalized?.metadata_tecnica || {},
            }, null, 2))}</div>
          </details>
        </div>
        <form id="reviewForm" class="panel final-review-form">
          <div class="panel-heading-row">
            <div>
              <h3>Formato final</h3>
              <p class="muted">Texto que quedara como salida final revisable para futura BD.</p>
            </div>
            <span id="finalLatexReviewStatus" class="nav-counter">Formato pendiente</span>
          </div>
          <textarea id="finalLatexText" class="final-review-text" spellcheck="false">${escapeHtml(finalLatexText)}</textarea>
          <section id="finalLatexPreview" class="latex-preview final-latex-preview" aria-label="Vista renderizada del formato final">
            ${renderFinalLatexPreviewHtml(finalLatexText)}
          </section>
          <button type="submit" class="primary wide-action">Guardar formato final en staging</button>
        </form>
      </div>
    ` : `<div class="panel muted">Selecciona un problema.</div>`}
  `;
  if (record) {
    const reviewBatchBtn = $("openReviewBatch");
    if (reviewBatchBtn) reviewBatchBtn.onclick = () => openBatchMode("normalization");
    const finalLatexBatchBtn = $("openFinalLatexBatch");
    if (finalLatexBatchBtn) finalLatexBatchBtn.onclick = () => openBatchMode("final_latex");
    bindRecordNavigation(records);
    $("reviewForm").onsubmit = saveReviewForm;
    $("reviewForm").addEventListener("input", () => {
      updateFinalLatexReviewStatus();
      updateFinalLatexPreview();
    });
    $("reviewForm").addEventListener("change", () => {
      updateFinalLatexReviewStatus();
      updateFinalLatexPreview();
    });
    updateFinalLatexReviewStatus();
    updateFinalLatexPreview();
  }
  setInspector(record ? {
    "Registro": record.record_id,
    "Correcciones guardadas": (record.training_examples || []).length,
    "Estado": record.status_label || record.status || "-",
    ...(errorComment ? {"Error": errorComment} : {}),
  } : "");
}

function field(id, label, value) {
  return `<label><span class="muted">${label}</span><input id="${id}" value="${escapeAttr(value)}" /></label>`;
}

function updateLatexPreview() {
  const preview = $("latexPreview");
  if (!preview) return;
  const data = collectReviewForm();
  const options = ["A", "B", "C", "D", "E"].map((key) => `
    <div class="option-row"><strong>${key}</strong><span>${formatLatexPreviewText(data.alternativas[key] || "")}</span></div>
  `).join("");
  preview.innerHTML = `
    <article class="preview-problem">
      <header>
        <strong>${escapeHtml(data.numero || "Sin numero")}</strong>
        <span>${escapeHtml([data.curso, data.tema].filter(Boolean).join(" - "))}</span>
      </header>
      <div class="statement-preview">${formatPreviewText(data.enunciado_latex || "")}</div>
      <div class="options-preview">${options}</div>
      <footer><strong>Clave:</strong> ${formatLatexPreviewText(data.respuesta_correcta || "-")} ${data.tiene_grafico ? "<span>- con grafico</span>" : ""}</footer>
    </article>
  `;
  typesetMath(preview);
}

function typesetMath(preview) {
  if (!preview || !window.MathJax || !MathJax.typesetPromise) return;
  MathJax.typesetClear([preview]);
  MathJax.typesetPromise([preview]).catch(() => {});
}

function normalizeFinalLatexForStorage(value) {
  return String(value || "")
    .replaceAll("Â£", "£")
    .replaceAll("Ã¦", "æ")
    .replaceAll("Â¦", "æ")
    .replaceAll("¦", "æ")
    .trim();
}

function updateFinalLatexPreview() {
  const preview = $("finalLatexPreview") || document.querySelector(".final-latex-preview");
  if (!preview) return;
  preview.innerHTML = renderFinalLatexPreviewHtml($("finalLatexText")?.value || "");
  typesetMath(preview);
}

function renderFinalLatexPreviewHtml(value) {
  const raw = normalizeFinalLatexForStorage(value);
  if (!raw) return `<div class="muted">Sin formato final para renderizar.</div>`;
  if (isFinalLatexContinuation(raw)) {
    return `
      <article class="preview-problem continuation-preview">
        <header><strong>[CONT.]</strong><span>Continuacion del problema anterior</span></header>
        <div class="statement-preview">${formatPreviewText(stripFinalContinuationMarker(raw) || "[sin texto OCR visible]")}</div>
      </article>
    `;
  }
  const numberMatch = raw.match(/\\item\s*\[\s*\\textbf\{\s*([^}.]+)\.?\s*\}\s*\]/i);
  const number = numberMatch ? numberMatch[1].trim() : "";
  const tags = {};
  raw.replace(/\[\[\s*([^=\]]+?)\s*=\s*([^\]]*?)\s*\]\]/g, (_match, key, tagValue) => {
    tags[normalizePlainLabel(key)] = String(tagValue || "").trim();
    return "";
  });
  let body = raw
    .replace(/\\item\s*\[\s*\\textbf\{\s*([^}.]+)\.?\s*\}\s*\]/i, "")
    .replace(/\[\[\s*([^=\]]+?)\s*=\s*([^\]]*?)\s*\]\]/g, "")
    .trim();
  const optionsMatch = body.match(/£A\)([\s\S]*?)æB\)([\s\S]*?)æC\)([\s\S]*?)£D\)([\s\S]*?)ææE\)([\s\S]*?)£/);
  let optionsHtml = "";
  if (optionsMatch) {
    const labels = ["A", "B", "C", "D", "E"];
    optionsHtml = `
      <div class="options-preview">
        ${labels.map((label, index) => `
          <div class="option-row"><strong>${label}</strong><span>${formatLatexPreviewText(optionsMatch[index + 1] || "")}</span></div>
        `).join("")}
      </div>
    `;
    body = body.replace(optionsMatch[0], "").trim();
  }
  const headerMeta = [
    tags.curso || "",
    tags.tema || "",
    tags.clave ? `Clave ${tags.clave}` : "",
  ].filter(Boolean).join(" - ");
  return `
    <article class="preview-problem">
      <header>
        <strong>${escapeHtml(number || "Sin numero")}</strong>
        <span>${escapeHtml(headerMeta || "Formato final")}</span>
      </header>
      <div class="statement-preview">${formatPreviewText(body || raw)}</div>
      ${optionsHtml}
      ${tags.imagen ? `<footer><strong>Imagen:</strong><span>${escapeHtml(tags.imagen)}</span></footer>` : ""}
    </article>
  `;
}

function formatLatexPreviewText(value) {
  const raw = normalizeLatexPreviewValueSafe(String(value || "").trim());
  if (!raw) return "-";
  if (raw.includes("$")) return formatPreviewText(raw);
  const mathOnly = /^[0-9A-Za-z\s\\^_{}()[\].,+\-*/=<>:;|°º]+$/.test(raw) && /[\\^_{}=<>°º]/.test(raw);
  return mathOnly ? `$${escapeHtml(raw)}$` : formatPreviewText(raw);
}

function normalizeLatexPreviewValue(value) {
  return decodeLatexTextAccents(value)
    .replace(/(\d+(?:[.,]\d+)?)\s*(?:\^o|º|°)(?=\s|$|[.,;:)])/gi, "$1^\\circ")
    .replace(/(\d+(?:[.,]\d+)?)\s*\\degree\b/gi, "$1^\\circ");
}

function normalizeLatexPreviewValueSafe(value) {
  const degreePattern = new RegExp("(\\d+(?:[.,]\\d+)?)\\s*(?:\\^o|\\u00ba|\\u00b0)(?=\\s|$|[.,;:)])", "gi");
  return decodeLatexTextAccents(value)
    .replace(degreePattern, "$1^\\circ")
    .replace(/(\d+(?:[.,]\d+)?)\s*\\degree\b/gi, "$1^\\circ");
}

async function saveReviewForm(event) {
  event.preventDefault();
  const record = selectedRecord();
  if (!record) return;
  await runAction("Guardando revision...", async () => {
    const payload = collectReviewPayload();
    const result = await api("/api/review/save", {
      method: "POST",
      body: {
        record_id: record.record_id,
        normalized: payload.normalized,
        notes: payload.notes,
        mark_ready: payload.markReady,
      },
    });
    state.snapshot = result.snapshot;
    state.reviewDraft = null;
    await refreshNormalizerTrainingStatus({ silent: true });
    persistFactoryUiState();
    render();
  }, "Revision guardada en staging como entrenamiento futuro.");
}

function collectReviewForm() {
  return collectReviewPayload().normalized;
}

function collectReviewPayload() {
  const record = selectedRecord();
  const finalLatex = normalizeFinalLatexForStorage($("finalLatexText")?.value || "");
  if (!finalLatex) throw new Error("Formato final vacio.");
  if (isFinalLatexContinuation(finalLatex)) {
    const normalized = normalizedJsonForReview(normalizedForBatchRecord(record), record);
    normalized.status = "listo";
    normalized.continuacion = {
      ...(normalized.continuacion && typeof normalized.continuacion === "object" ? normalized.continuacion : {}),
      es_continuacion: true,
      fusionar_con_anterior: true,
    };
    normalized.enunciado_latex = stripFinalContinuationMarker(finalLatex);
    normalized.latex_rendered_item = "";
    return {
      normalized,
      notes: String(record?.review?.notes || "Continuacion revisada en formato final.").trim(),
      markReady: true,
    };
  }
  const parsed = parseFinalLatexItem(finalLatex, normalizedForBatchRecord(record), record);
  return {
    normalized: {
      ...parsed.normalized,
      latex_rendered_item: finalLatex,
      status: "listo",
    },
    notes: parsed.notes,
    markReady: true,
  };
}

function reviewJsonFromNormalized(normalized, record) {
  return JSON.stringify(normalizedJsonForReview(normalized, record), null, 2);
}

function normalizedJsonForReview(normalized, record) {
  const base = normalized && typeof normalized === "object" ? normalized : {};
  const alternatives = base.alternativas && typeof base.alternativas === "object" ? base.alternativas : {};
  const answerKey = String(base.respuesta_correcta || "").trim();
  const normalizedAnswerKey = normalizeAnswerValue(answerKey);
  const respuestaFinal = String(base.respuesta_final || "").trim()
    || String(alternatives[normalizedAnswerKey] || "").trim();
  const source = record?.source || base.metadata_tecnica?.source || {};
  return {
    ...base,
    schema_version: base.schema_version || "normalized_problem_staging_v1",
    normalizer: base.normalizer || "manual_json_review",
    status: String(base.status || (record?.status === "listo" ? "listo" : "requiere_revision")),
    numero: String(base.numero || ""),
    curso: String(base.curso || "SIN_CURSO"),
    tema: String(base.tema || "SIN_TEMA"),
    subtema: String(base.subtema || ""),
    estado: String(base.estado || "sin_revisar"),
    respuesta_correcta: answerKey,
    respuesta_final: respuestaFinal,
    enunciado_latex: String(base.enunciado_latex || ""),
    tiene_grafico: Boolean(base.tiene_grafico || (record?.figure_segments_web || []).length),
    figure_tag: String(base.figure_tag || ""),
    alternativas: {
      A: String(alternatives.A || ""),
      B: String(alternatives.B || ""),
      C: String(alternatives.C || ""),
      D: String(alternatives.D || ""),
      E: String(alternatives.E || ""),
    },
    continuacion: base.continuacion && typeof base.continuacion === "object"
      ? base.continuacion
      : { es_continuacion: false, fusionar_con_anterior: false },
    classification: base.classification && typeof base.classification === "object"
      ? base.classification
      : {
          curso_confidence: 0,
          tema_confidence: 0,
          requires_human_review: true,
          candidate_new_topic: "",
        },
    latex_rendered_item: String(base.latex_rendered_item || ""),
    metadata_tecnica: base.metadata_tecnica && typeof base.metadata_tecnica === "object"
      ? base.metadata_tecnica
      : {
          crop_path: record?.crop_path || "",
          source,
          models: record?.models || {},
          confidence: record?.confidence || {},
        },
  };
}

function parseReviewJsonText(text, record) {
  let parsed;
  try {
    parsed = JSON.parse(String(text || "").trim() || "{}");
  } catch (err) {
    throw new Error(`JSON invalido: ${err.message}`);
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("JSON invalido: la normalizacion debe ser un objeto.");
  }
  const normalized = normalizedJsonForReview(parsed, record);
  normalized.respuesta_correcta = normalizeAnswerValue(normalized.respuesta_correcta);
  const notes = String(parsed.review_notes || parsed.notas || record?.review?.notes || "").trim();
  const markReady = normalizedStatusIsReady(normalized);
  return { normalized, notes, markReady };
}

function normalizedStatusIsReady(normalized) {
  const values = [normalized.status, normalized.estado, normalized.review_status]
    .map((value) => String(value || "").trim().toLowerCase());
  return values.some((value) => ["listo", "ready", "revisado", "reviewed", "done"].includes(value));
}

function updateJsonReviewStatus() {
  const host = $("jsonReviewStatus");
  if (!host) return;
  try {
    const parsed = parseReviewJsonText($("normalizedJsonText")?.value || "", selectedRecord());
    host.textContent = parsed.markReady ? "JSON listo" : "JSON valido";
    host.classList.remove("status-error");
  } catch (err) {
    host.textContent = "JSON invalido";
    host.classList.add("status-error");
  }
}

function updateFinalLatexReviewStatus() {
  const host = $("finalLatexReviewStatus");
  if (!host) return;
  const text = normalizeFinalLatexForStorage($("finalLatexText")?.value || "");
  host.classList.remove("status-error");
  if (!text) {
    host.textContent = "Formato vacio";
    host.classList.add("status-error");
    return;
  }
  if (isFinalLatexContinuation(text)) {
    host.textContent = "Continuacion";
    return;
  }
  const hasItem = /\\item\s*\[\s*\\textbf\{/i.test(text);
  const hasOptions = /£A\)[\s\S]*?æB\)[\s\S]*?æC\)[\s\S]*?£D\)[\s\S]*?ææE\)[\s\S]*?£/.test(text);
  if (hasItem && hasOptions) {
    host.textContent = "Formato listo";
    return;
  }
  host.textContent = hasItem ? "Revisar opciones" : "Revisar item";
  host.classList.add("status-error");
}

function reviewTextFromNormalized(normalized, record) {
  const alternatives = normalized.alternativas || {};
  const statement = String(normalized.enunciado_latex || "").trim();
  const number = String(normalized.numero || "").trim();
  const lines = [];
  lines.push(`${number ? `${number}. ` : ""}${statement}`.trim());
  ["A", "B", "C", "D", "E"].forEach((key) => {
    lines.push(`${key}) ${String(alternatives[key] || "").trim()}`);
  });
  lines.push("");
  lines.push(`Respuesta: ${String(normalized.respuesta_correcta || "").trim()}`);
  lines.push(`Curso: ${String(normalized.curso || "").trim()}`);
  lines.push(`Tema: ${String(normalized.tema || "").trim()}`);
  lines.push(`Grafico: ${normalized.tiene_grafico ? "si" : "no"}`);
  lines.push(`Etiqueta grafico: ${String(normalized.figure_tag || "").trim()}`);
  lines.push(`Listo: ${record?.status === "listo" ? "si" : "no"}`);
  lines.push("Notas:");
  lines.push(String(record?.review?.notes || "").trim());
  return lines.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

function parsePlainReviewText(text, base, record) {
  const baseAlternatives = base.alternativas && typeof base.alternativas === "object" ? base.alternativas : {};
  const normalized = {
    ...base,
    schema_version: base.schema_version || "normalized_problem_staging_v1",
    numero: String(base.numero || ""),
    curso: String(base.curso || ""),
    tema: String(base.tema || ""),
    enunciado_latex: String(base.enunciado_latex || ""),
    alternativas: {
      A: String(baseAlternatives.A || ""),
      B: String(baseAlternatives.B || ""),
      C: String(baseAlternatives.C || ""),
      D: String(baseAlternatives.D || ""),
      E: String(baseAlternatives.E || ""),
    },
    respuesta_correcta: String(base.respuesta_correcta || ""),
    tiene_grafico: Boolean(base.tiene_grafico),
    figure_tag: String(base.figure_tag || ""),
  };
  const rawLines = String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  const statementLines = [];
  const noteLines = [];
  let currentOption = "";
  let readingNotes = false;
  let markReady = record?.status === "listo";
  let explicitStatementLabel = false;

  rawLines.forEach((rawLine) => {
    const line = rawLine.trim();
    if (readingNotes) {
      noteLines.push(rawLine);
      return;
    }
    const notesMatch = line.match(/^notas?\s*:\s*(.*)$/i);
    if (notesMatch) {
      readingNotes = true;
      if (notesMatch[1]) noteLines.push(notesMatch[1]);
      currentOption = "";
      return;
    }
    if (!line) {
      currentOption = "";
      return;
    }
    const labelMatch = line.match(/^(numero|n[uú]mero|curso|tema|enunciado|respuesta|clave|grafico|gr[aá]fico|tiene grafico|tiene gr[aá]fico|etiqueta grafico|etiqueta gr[aá]fico|listo|revision lista|revisi[oó]n lista)\s*:\s*(.*)$/i);
    if (labelMatch) {
      const plainLabel = normalizePlainLabel(labelMatch[1]);
      explicitStatementLabel = explicitStatementLabel || plainLabel === "enunciado";
      if (plainLabel === "enunciado" && labelMatch[2].trim()) statementLines.push(labelMatch[2].trim());
      applyPlainReviewLabel(normalized, labelMatch[1], labelMatch[2], (ready) => { markReady = ready; });
      currentOption = "";
      return;
    }
    const optionMatch = line.match(/^([A-E])[\)\.]\s*(.*)$/i);
    if (optionMatch) {
      currentOption = optionMatch[1].toUpperCase();
      normalized.alternativas[currentOption] = optionMatch[2].trim();
      return;
    }
    if (currentOption) {
      normalized.alternativas[currentOption] = `${normalized.alternativas[currentOption]} ${line}`.trim();
      return;
    }
    const itemMatch = line.match(/^(?:\\item\s*\[\s*\\textbf\{\s*)?(\d+)\s*[\.\)](?:\s*\}\s*\])?\s*(.*)$/i);
    if (itemMatch) {
      normalized.numero = itemMatch[1].trim();
      if (itemMatch[2]) statementLines.push(itemMatch[2].trim());
      return;
    }
    statementLines.push(rawLine.trim());
  });

  const parsedStatement = statementLines.join("\n").trim();
  if (parsedStatement || !explicitStatementLabel) normalized.enunciado_latex = parsedStatement;
  normalized.respuesta_correcta = normalizeAnswerValue(normalized.respuesta_correcta);
  return {
    normalized,
    notes: noteLines.join("\n").trim(),
    markReady,
  };
}

function applyPlainReviewLabel(normalized, label, value, setMarkReady) {
  const key = normalizePlainLabel(label);
  const val = String(value || "").trim();
  if (key === "numero") normalized.numero = val;
  else if (key === "curso") normalized.curso = val;
  else if (key === "tema") normalized.tema = val;
  else if (key === "enunciado") normalized.enunciado_latex = val;
  else if (key === "respuesta" || key === "clave") normalized.respuesta_correcta = val;
  else if (key === "grafico" || key === "tiene grafico") normalized.tiene_grafico = truthyText(val);
  else if (key === "etiqueta grafico") normalized.figure_tag = val;
  else if (key === "listo" || key === "revision lista") setMarkReady(truthyText(val));
}

function normalizePlainLabel(label) {
  return String(label || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
}

function normalizeAnswerValue(value) {
  const original = String(value || "").trim();
  const raw = original.toUpperCase();
  if (/^[A-E]$/.test(raw)) return raw;
  const labeled = raw.match(/^(?:CLAVE|RESPUESTA)?\s*[:=-]?\s*([A-E])[\).]?\s*$/);
  return labeled ? labeled[1] : original;
}

function truthyText(value) {
  const raw = String(value || "").trim().toLowerCase();
  return ["1", "si", "sí", "s", "true", "yes", "y", "listo", "ok"].includes(raw);
}

function renderCandidateStage() {
  const record = selectedRecord();
  $("stageHost").innerHTML = `
    <div class="stage-header">
      <div>
        <h2>Candidato futuro a BD</h2>
        <p class="muted">Revisa si el registro esta completo. La escritura real en problemas permanece bloqueada.</p>
      </div>
    </div>
    <div id="candidateHost" class="panel">${record ? "Cargando candidato..." : "Selecciona un registro."}</div>
  `;
  if (record) {
    api(`/api/promotion?record_id=${encodeURIComponent(record.record_id)}`).then((candidate) => {
      $("candidateHost").innerHTML = `
        <h3>${candidate.ready_for_future_promotion ? "Listo para promocion futura" : "Aun falta revisar"}</h3>
        <div class="candidate-grid">
          <div class="candidate-cell"><span>Promocion habilitada</span><strong>${candidate.promotion_enabled ? "Si" : "No"}</strong></div>
          <div class="candidate-cell"><span>SQL preparado</span><strong>${candidate.sql ? "Si" : "No"}</strong></div>
          <div class="candidate-cell"><span>Escrituras</span><strong>${(candidate.write_operations || []).length}</strong></div>
        </div>
        <h3>Bloqueos pendientes</h3>
        ${blockingIssuesHtml(candidate.blocking_issues || [])}
        ${renderTechnicalDetails("Detalle tecnico del candidato", candidate)}
      `;
    }).catch((err) => setStatus(err.message));
  }
  setInspector(record ? {
    "Registro": record.record_id,
    "Destino actual": "staging",
    "Promocion real": "desactivada",
  } : "");
}

function blockingIssuesHtml(issues) {
  if (!issues.length) {
    return `<p class="muted">Sin bloqueos reportados por el validador.</p>`;
  }
  return `<ul class="issue-list">${issues.map((issue) => `<li>${escapeHtml(String(issue))}</li>`).join("")}</ul>`;
}

function syncPrimaryAction() {
  const btn = $("primaryAction");
  if (state.batchMode) {
    const config = batchModeConfig();
    btn.textContent = config.action;
    btn.onclick = () => saveBatchEditor(state.batchMode);
    btn.disabled = isTaskRunning();
    $("actionHint").textContent = "Guarda el lote de uno en uno y reporta errores por imagen.";
    return;
  }
  const queuedCount = state.stage === "crops" ? queuedOcrRecordIds().length : 0;
  const actions = {
    pages: ["Detectar con modelo", "Usa el detector entrenado sobre las paginas elegidas.", detectSelectedPages],
    boxes: ["Crear staging", "Materializa crops solo desde boxes revisados.", materializeStaging],
    crops: [
      queuedCount ? `OCR cola (${queuedCount})` : "OCR imagen actual",
      queuedCount ? "Procesa las imagenes seleccionadas con OCR y segmentacion grafica." : "Usa OCR entrenado y segmentador grafico en el crop seleccionado.",
      () => runOcr(),
    ],
    ocr: ["Preparar revision completa", "Crea borradores editables para todos los problemas con OCR; no ejecuta normalizador IA final.", normalizeRecords],
    review: ["Guardar revision", "Persiste correcciones en staging como entrenamiento futuro.", () => $("reviewForm")?.requestSubmit()],
    candidate: ["Actualizar candidato", "Recalcula bloqueos sin escribir en problemas.", renderCandidateStage],
  };
  const [label, hint, handler] = actions[state.stage] || actions.pages;
  btn.textContent = label;
  btn.onclick = handler;
  btn.disabled = isTaskRunning();
  $("actionHint").textContent = isTaskRunning() ? "Proceso en curso; se actualiza al terminar cada imagen." : hint;
}

async function detectSelectedPages() {
  const pages = selectedRangeText();
  if (!pages) return setStatus("Selecciona al menos una pagina.");
  const models = activeModelPayload();
  await runAction("Detectando boxes con modelo entrenado...", async () => {
    state.snapshot = await api("/api/pages/detect", {
      method: "POST",
      body: {
        pages,
        dpi: 300,
        confidence: 0.25,
        detector_model: models.pdf_detector,
      },
    });
    state.stage = "boxes";
    state.selectedPageRecordId = "";
    state.boxes = [];
    state._boxSource = "";
    state._boxSourceSignature = "";
    state.selectedBox = -1;
    state.boxDirty = false;
    state.drag = null;
    persistFactoryUiState();
    render();
    persistFactoryUiState();
  }, "Boxes detectados. Revisa antes de crear staging.");
}

async function materializeStaging() {
  await runAction("Creando crops y staging...", async () => {
    state.snapshot = await api("/api/staging/materialize", { method: "POST", body: {} });
    state.stage = "crops";
    restoreFactoryUiState();
    state.stage = "crops";
    persistFactoryUiState();
    render();
  }, "Staging creado desde boxes.");
}

async function runOcr(recordIds = null) {
  const models = activeModelPayload();
  const record = selectedRecord();
  const records = state.snapshot.records || [];
  const queuedIds = Array.isArray(recordIds) ? recordIds : queuedOcrRecordIds(records);
  const fallbackIds = record?.record_id ? [record.record_id] : [];
  const targetIds = queuedIds.length ? queuedIds : fallbackIds;
  const pending = [...new Set(targetIds.map((id) => String(id || "").trim()).filter(Boolean))]
    .filter((id) => recordCanRunOcr(findRecordById(id, records)));
  if (!pending.length) {
    const blocked = targetIds.some((id) => {
      const target = findRecordById(id, records);
      return target && !recordCanRunOcr(target);
    });
    return setStatus(blocked
      ? "Regenera staging para los crops afectados antes de ejecutar OCR."
      : "Selecciona al menos una imagen de staging.");
  }
  const total = pending.length;
  state.ocrQueueIds = new Set(pending);
  state.taskProgress = {
    type: "ocr",
    running: true,
    label: "OCR + segmentacion",
    total,
    current: 0,
    ok: 0,
    failed: 0,
    message: `Preparando cola 0 de ${total}`,
    activeId: pending[0] || "",
    activeName: recordLabelById(pending[0]),
  };
  state.stage = "crops";
  persistFactoryUiState();
  render();
  setBusy(`Ejecutando modelos 0 de ${total}...`);
  try {
    const job = await startOcrJob(pending, models);
    state.ocrJobId = String(job.job_id || "");
    persistFactoryUiState();
    await pollOcrJob(job.job_id || "");
  } finally {
    state.taskProgress = null;
    $("busyText").textContent = "";
  }
}

async function startOcrJob(recordIds, models) {
  return api("/api/ocr/jobs/start", {
    method: "POST",
    body: {
      provider: "hf",
      curso: "SIN_CURSO",
      tema: "SIN_TEMA",
      start_n: 1,
      record_ids: recordIds,
      ocr_model: models.ocr,
      figure_model: models.figure_segmenter,
      force_figure_model: true,
    },
  });
}

async function pollOcrJob(jobId = "") {
  if (state.ocrJobPolling) return null;
  state.ocrJobPolling = true;
  state.ocrJobId = String(jobId || state.ocrJobId || "").trim();
  persistFactoryUiState();
  let lastActiveId = "";
  try {
    while (true) {
      const query = jobId ? `?job_id=${encodeURIComponent(jobId)}` : "";
      const job = await api(`/api/ocr/jobs/status${query}`);
      if (!job || job.status === "idle") return job;
      const total = Number(job.total || 0);
      const current = Number(job.current || 0);
      const activeId = String(job.active_id || "");
      if (activeId) {
        lastActiveId = activeId;
        state.selectedRecordId = activeId;
        state.ocrQueueIds.delete(activeId);
      }
      state.taskProgress = {
        type: "ocr",
        running: Boolean(job.running),
        label: "OCR + segmentacion",
        total,
        current,
        ok: Number(job.ok || 0),
        failed: Number(job.failed || 0),
        message: String(job.message || "Procesando OCR..."),
        activeId,
        activeName: activeId ? recordLabelById(activeId) : "",
      };
      setBusy(job.running ? `Ejecutando modelos ${current} de ${total}...` : "");
      render();
      if (!job.running) {
        applyFactorySnapshot(await loadFactorySnapshot());
        state.stage = "ocr";
        state.selectedRecordId = lastActiveId || activeId || state.selectedRecordId;
        state.selectedOcrIndex = 0;
        state.taskProgress = null;
        state.ocrQueueIds.clear();
        state.ocrJobId = "";
        persistFactoryUiState();
        render();
        const endpointText = job.endpoint_shutdown?.message
          ? ` ${job.endpoint_shutdown.message}`
          : (job.endpoint_shutdown?.error ? ` No se pudo apagar el endpoint OCR: ${job.endpoint_shutdown.error}` : "");
        setStatus(`${job.message || "OCR terminado."}${endpointText}`);
        return job;
      }
      await sleep(1500);
    }
  } finally {
    state.ocrJobPolling = false;
  }
}

async function resumeOcrJobIfRunning({ silent = true } = {}) {
  if (state.view !== "factory" || state.ocrJobPolling) return;
  try {
    const savedJobId = String(state.ocrJobId || "").trim();
    const savedQuery = savedJobId ? `?job_id=${encodeURIComponent(savedJobId)}` : "";
    let job = await api(`/api/ocr/jobs/status${savedQuery}`);
    if (!job?.running && savedJobId) {
      state.ocrJobId = "";
      persistFactoryUiState();
      job = await api("/api/ocr/jobs/status");
    }
    if (!job?.running) return;
    state.stage = "crops";
    state.ocrJobId = String(job.job_id || state.ocrJobId || "").trim();
    state.ocrQueueIds = new Set((job.record_ids || []).map((id) => String(id || "")).filter(Boolean));
    persistFactoryUiState();
    if (!silent) setStatus("OCR sigue ejecutandose en segundo plano; reconectando progreso.");
    pollOcrJob(job.job_id || "").catch((err) => setStatus(`Error consultando OCR: ${err.message}`));
  } catch (_) {
    // El servidor puede ser una version anterior; en ese caso no bloqueamos el arranque.
  }
}

async function runOcrForRecord(recordId, models) {
  const current = findRecordById(recordId);
  const startN = inferredStartNumberForRecord(current);
  return api("/api/ocr/run", {
    method: "POST",
    body: {
      provider: "hf",
      curso: "SIN_CURSO",
      tema: "SIN_TEMA",
      start_n: startN,
      record_id: recordId,
      ocr_model: models.ocr,
      figure_model: models.figure_segmenter,
      force_figure_model: true,
    },
  });
}

async function normalizeRecords() {
  const records = normalizableRecords();
  if (!records.length) return setStatus("No hay OCR crudo o estructurado para preparar revision todavia.");
  const total = records.length;
  const failures = [];
  state.stage = "review";
  state.taskProgress = {
    type: "normalize",
    running: true,
    label: "Revision",
    total,
    current: 0,
    ok: 0,
    failed: 0,
    message: `Preparando revision 0 de ${total}`,
    activeId: records[0]?.record_id || "",
    activeName: recordLabelById(records[0]?.record_id),
  };
  render();
  setBusy(`Preparando revision 0 de ${total}...`);
  try {
    for (let index = 0; index < records.length; index += 1) {
      const record = records[index];
      const id = record.record_id;
      state.taskProgress = {
        ...state.taskProgress,
        current: index,
        message: `Preparando revision ${index + 1} de ${total}`,
        activeId: id,
        activeName: recordLabelById(id),
      };
      state.selectedRecordId = id;
      render();
      setBusy(`Preparando revision ${index + 1} de ${total}...`);
      try {
        state.snapshot = await api("/api/normalize", { method: "POST", body: { record_id: id } });
        state.taskProgress = {
          ...state.taskProgress,
          current: index + 1,
          ok: Number(state.taskProgress.ok || 0) + 1,
          message: `Revision preparada ${index + 1} de ${total}`,
        };
      } catch (err) {
        failures.push({ id, message: err.message });
        state.taskProgress = {
          ...state.taskProgress,
          current: index + 1,
          failed: Number(state.taskProgress.failed || 0) + 1,
          message: `Error en ${index + 1} de ${total}`,
        };
        setStatus(`Error preparando revision ${recordLabelById(id)}: ${err.message}`);
      }
      state.stage = "review";
      state.selectedRecordId = id;
      state.selectedOcrIndex = 0;
      restoreFactoryUiState({ preserveCurrentStage: true });
      state.stage = "review";
      state.selectedRecordId = id;
      state.selectedOcrIndex = 0;
      persistFactoryUiState();
      render();
    }
    state.taskProgress = null;
    persistFactoryUiState();
    render();
    setStatus(failures.length ? `Revision preparada con ${failures.length} error(es).` : `Revision preparada para ${total} imagen(es).`);
  } finally {
    state.taskProgress = null;
    $("busyText").textContent = "";
  }
}

function normalizableRecords() {
  const records = state.snapshot?.records || [];
  const hasNormalizableData = (record) => !recordSourceStale(record)
    && !recordDownstreamInvalidated(record)
    && (hasText(record.raw_ocr) || hasObjectData(record.structured_ocr));
  return records.filter(hasNormalizableData);
}

async function runAction(busy, action, done) {
  try {
    setBusy(busy);
    const result = await action();
    setStatus(typeof result === "string" && result.trim() ? result : done);
  } catch (err) {
    setStatus(`Error: ${err.message}`);
  } finally {
    $("busyText").textContent = "";
  }
}

function setBusy(text) {
  $("busyText").textContent = text;
  $("statusText").textContent = text;
}

function setStatus(text) {
  $("statusText").textContent = text;
  $("busyText").textContent = "";
}

function setInspector(text) {
  const host = $("inspector");
  if (!text) {
    host.textContent = "Sin detalle.";
    return;
  }
  if (typeof text === "object" && !Array.isArray(text)) {
    host.innerHTML = Object.entries(text).map(([key, value]) => `
      <div class="inspector-line">
        <strong>${escapeHtml(key)}</strong>
        <span>${escapeHtml(value ?? "-")}</span>
      </div>
    `).join("");
    return;
  }
  const rows = String(text).split("\n").filter(Boolean);
  host.innerHTML = rows.length ? rows.map((row) => {
    const [label, ...rest] = row.split(":");
    const hasLabel = rest.length > 0;
    return `
      <div class="inspector-line">
        <strong>${escapeHtml(hasLabel ? label : "Detalle")}</strong>
        <span>${escapeHtml(hasLabel ? rest.join(":").trim() : row)}</span>
      </div>
    `;
  }).join("") : "Sin detalle.";
}

function selectedRecord() {
  return (state.snapshot.records || []).find((record) => record.record_id === state.selectedRecordId) || (state.snapshot.records || [])[0];
}

function filteredBooks(applyStatus = true) {
  const query = state.library.query.trim().toLowerCase();
  return (state.library.books || []).filter((book) => {
    const haystack = [book.title, book.code, book.author, book.subject, book.pdfName].join(" ").toLowerCase();
    const matchesQuery = !query || haystack.includes(query);
    const matchesStatus = !applyStatus || state.library.status === "all" || (book.instances || []).some((item) => normalizeStatus(item.status) === state.library.status);
    return matchesQuery && matchesStatus;
  });
}

function filteredInstances(instances) {
  const rows = naturalSortInstances(instances || []);
  if (state.library.status === "all") return rows;
  return rows.filter((item) => normalizeStatus(item.status) === state.library.status);
}

function naturalSortInstances(instances) {
  return [...(instances || [])].sort((a, b) => {
    const keyA = instanceNaturalSortKey(a);
    const keyB = instanceNaturalSortKey(b);
    for (let index = 0; index < Math.max(keyA.length, keyB.length); index += 1) {
      const left = keyA[index];
      const right = keyB[index];
      if (left === right) continue;
      if (typeof left === "number" && typeof right === "number") return left - right;
      if (typeof left === "number") return -1;
      if (typeof right === "number") return 1;
      return String(left || "").localeCompare(String(right || ""), "es", { sensitivity: "base" });
    }
    return String(a?.id || "").localeCompare(String(b?.id || ""), "es", { numeric: true, sensitivity: "base" });
  });
}

function instanceNaturalSortKey(instance) {
  const label = String(instance?.title || instance?.tipo || instance?.instance_type || instance?.code || instance?.id || "").toLowerCase();
  const tokens = [];
  const pattern = /(\d+)|([a-záéíóúñ]+)|([^a-záéíóúñ\d]+)/gi;
  let match;
  while ((match = pattern.exec(label)) !== null) {
    if (match[1]) tokens.push(Number(match[1]));
    else if (match[2]) tokens.push(match[2]);
  }
  return tokens.length ? tokens : [label];
}

function selectedLibraryBook() {
  const book = (state.library.books || []).find((row) => row.id === state.library.selectedBookId) || filteredBooks(false)[0] || null;
  if (!book) return null;
  const detail = state.library.details[String(book.id || "")];
  if (detail?.loaded) {
    return {
      ...book,
      coverUrl: book.coverUrl || detail.book?.cover_url || detail.book?.coverUrl || "",
      instances: book.instances || detail.instances || [],
      dashboard: detail.dashboard || book.dashboard || {},
    };
  }
  return book;
}

function selectedLibraryInstance() {
  const book = selectedLibraryBook();
  if (!book) return null;
  return (book.instances || []).find((item) => item.id === state.library.selectedInstanceId) || filteredInstances(book.instances || [])[0] || null;
}

function findLibraryInstance(id) {
  for (const book of state.library.books || []) {
    const instance = (book.instances || []).find((item) => item.id === id);
    if (instance) return { ...instance, book };
  }
  return null;
}

function libraryCounts() {
  const books = state.library.books || [];
  const instances = books.flatMap((book) => book.instances || []);
  const counts = statusCounts(instances);
  return {
    books: books.length,
    instances: instances.length,
    ...counts,
  };
}

function statusCounts(items) {
  return (items || []).reduce((acc, item) => {
    const status = normalizeStatus(item.status || "pendiente");
    acc[status] = (acc[status] || 0) + 1;
    return acc;
  }, { pendiente: 0, procesando: 0, requiere_revision: 0, listo: 0, error: 0 });
}

function inferInstanceStatus(instance) {
  const indicators = instance.indicators || instance.summary || instance.metrics || {};
  if (Number(indicators.subidos_bd_sin_revisar || indicators.sin_revisar || 0) > 0) return "requiere_revision";
  if (Number(indicators.subidos_bd_inconsistentes || indicators.inconsistentes || 0) > 0) return "error";
  if (Number(indicators.faltantes || 0) <= 0 && Number(indicators.total_esperado || 0) > 0) return "listo";
  if (Number(indicators.escaneados_sesion || indicators.pages_total || indicators.boxes_total || 0) > 0) return "procesando";
  return "pendiente";
}

function selectedOcrPayload(record, index) {
  const item = (record.structured_items_web || [])[Number(index || 0)] || {};
  return item.item || item;
}

function normalizedFromOcr(record, payload) {
  const options = payload.options || {};
  return {
    ...(record.normalized || {}),
    numero: payload.n || "",
    curso: payload.curso || "",
    tema: payload.tema || "",
    enunciado_latex: payload.statement || "",
    alternativas: {
      A: options.A || "",
      B: options.B || "",
      C: options.C || "",
      D: options.D || "",
      E: options.E || "",
    },
    respuesta_correcta: payload.answer_key || "",
    tiene_grafico: Boolean(payload.has_figure || (record.figure_segments_web || []).length),
    figure_tag: payload.figure_tag || "",
  };
}

function compactText(value, maxLength) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (text.length <= maxLength) return text || "-";
  return `${text.slice(0, Math.max(0, maxLength - 1))}...`;
}

function formatPreviewText(value) {
  return escapeHtml(decodeLatexTextAccents(value) || "-").replace(/\n/g, "<br>");
}

function decodeLatexTextAccents(value) {
  const acute = {
    a: "\u00e1", e: "\u00e9", i: "\u00ed", o: "\u00f3", u: "\u00fa",
    A: "\u00c1", E: "\u00c9", I: "\u00cd", O: "\u00d3", U: "\u00da",
  };
  const grave = {
    a: "\u00e0", e: "\u00e8", i: "\u00ec", o: "\u00f2", u: "\u00f9",
    A: "\u00c0", E: "\u00c8", I: "\u00cc", O: "\u00d2", U: "\u00d9",
  };
  const diaeresis = {
    a: "\u00e4", e: "\u00eb", i: "\u00ef", o: "\u00f6", u: "\u00fc",
    A: "\u00c4", E: "\u00cb", I: "\u00cf", O: "\u00d6", U: "\u00dc",
  };
  const circumflex = {
    a: "\u00e2", e: "\u00ea", i: "\u00ee", o: "\u00f4", u: "\u00fb",
    A: "\u00c2", E: "\u00ca", I: "\u00ce", O: "\u00d4", U: "\u00db",
  };
  return String(value || "")
    .replace(/\\'\{?([aeiouAEIOU])\}?/g, (_match, ch) => acute[ch] || ch)
    .replace(/\\`\{?([aeiouAEIOU])\}?/g, (_match, ch) => grave[ch] || ch)
    .replace(/\\"\{?([aeiouAEIOU])\}?/g, (_match, ch) => diaeresis[ch] || ch)
    .replace(/\\\^\{?([aeiouAEIOU])\}?/g, (_match, ch) => circumflex[ch] || ch)
    .replace(/\\~\{?([nN])\}?/g, (_match, ch) => (ch === "N" ? "\u00d1" : "\u00f1"));
}

function normalizeStageName(value) {
  const text = String(value || "").toLowerCase();
  if (text.includes("pagina")) return "pages";
  if (text.includes("box")) return "boxes";
  if (text.includes("crop")) return "crops";
  if (text.includes("ocr")) return "ocr";
  if (text.includes("normal")) return "review";
  if (text.includes("staging")) return "candidate";
  return text;
}

function normalizeStatus(value) {
  const status = String(value || "pendiente").trim().toLowerCase().replaceAll(" ", "_");
  if (["en_progreso", "in_progress", "working"].includes(status)) return "procesando";
  if (["reviewed", "revisada", "revisado", "ready", "done"].includes(status)) return "listo";
  if (["needs_review", "por_revisar"].includes(status)) return "requiere_revision";
  if (["pending", "todo"].includes(status)) return "pendiente";
  return status;
}

function displayStatus(status) {
  return {
    pendiente: "Pendiente",
    procesando: "Procesando",
    en_progreso: "En progreso",
    listo: "Listo",
    requiere_revision: "Requiere revision",
    error: "Error",
  }[status] || status.replaceAll("_", " ");
}

function friendlyLibraryError(err) {
  return `No encontre el contrato de biblioteca todavia (${err.message}). Puedes abrir la Fabrica directa o conectar GET /api/library/books, POST /api/library/books y POST /api/library/books/{book_id}/instances.`;
}

function stageHint(id) {
  return {
    pages: "Selecciona un rango pequeno y verificable.",
    boxes: "Ajusta cada problema antes de crear crops.",
    crops: "Confirma que cada crop existe y tiene trazabilidad.",
    ocr: "Corrige OCR crudo y segmentos graficos.",
    review: "Revision humana experimental; el normalizador IA aun queda pendiente.",
    candidate: "Solo valida preparacion; la BD sigue cerrada.",
  }[id] || "";
}

function drawImageOnCanvas(canvas, src) {
  const img = new Image();
  img.onload = () => {
    const maxW = 980;
    const scale = Math.min(1, maxW / img.naturalWidth);
    canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
    canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
  };
  img.src = src;
}

function parseRange(raw, total) {
  const set = new Set();
  String(raw || "").split(",").forEach((token) => {
    const part = token.trim();
    if (!part) return;
    if (part.includes("-")) {
      let [a, b] = part.split("-", 2).map((v) => Number(v.trim()));
      if (Number.isFinite(a) && Number.isFinite(b)) {
        if (b < a) [a, b] = [b, a];
        for (let page = a; page <= b; page += 1) if (page >= 1 && page <= total) set.add(page);
      }
    } else {
      const page = Number(part);
      if (Number.isFinite(page) && page >= 1 && page <= total) set.add(page);
    }
  });
  return set;
}

function selectedRangeText() {
  return [...state.selectedPages].sort((a, b) => a - b).join(",");
}

let boxCanvasResizeTimer = null;
window.addEventListener("resize", () => {
  if (!boxCanvasState) return;
  window.clearTimeout(boxCanvasResizeTimer);
  boxCanvasResizeTimer = window.setTimeout(() => resizeBoxCanvas({ preserveCenter: true }), 120);
});

function cloneBoxes(boxes) {
  return (boxes || []).map((box) => box.slice(0, 4).map((value) => Number(value)));
}

function normalizeBox(box) {
  const x1 = Math.round(Math.min(box[0], box[2]));
  const y1 = Math.round(Math.min(box[1], box[3]));
  const x2 = Math.round(Math.max(box[0], box[2]));
  const y2 = Math.round(Math.max(box[1], box[3]));
  return [x1, y1, x2, y2];
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("\n", " ");
}

async function bootApp() {
  setBusy("Detectando contexto...");
  if (window.__PDF_APP_MODE__ === "library") {
    state.view = "library";
    renderLibrary();
    await loadLibrary();
    return;
  }
  try {
    state.snapshot = await api("/api/bootstrap");
    state.view = "factory";
    await refreshNormalizerTrainingStatus({ silent: true });
    restoreFactoryUiState();
    render();
    setStatus("Fabrica lista.");
    resumeOcrJobIfRunning({ silent: true });
    return;
  } catch (_) {
    state.view = "library";
  }
  await loadLibrary();
}

$("libraryBtn").onclick = () => {
  state.view = "library";
  renderLibrary();
  loadLibrary("Biblioteca actualizada.").catch((err) => setStatus(`Error de biblioteca: ${err.message}`));
};
$("refreshBtn").onclick = () => refresh("Actualizado.").catch((err) => setStatus(`Error: ${err.message}`));
if ($("themeToggle")) {
  $("themeToggle").onclick = () => toggleTheme();
  syncThemeToggle();
}
bootApp().catch((err) => setStatus(`Error inicial: ${err.message}`));
