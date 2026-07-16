const state = { snapshot: null, items: [], selectedId: null, page: 1, pageSize: 100, requestSerial: 0 };
const el = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  const payload = await response.json();
  if (!response.ok || payload.ok === false) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function option(value, label = value) {
  const node = document.createElement("option");
  node.value = value;
  node.textContent = label;
  return node;
}

function setupSelects(snapshot) {
  el("courseFilter").replaceChildren(
    option("todos", "Todos los cursos"),
    ...snapshot.filter_courses.map((course) => option(course, course === "Pendiente" ? "Sin clasificar" : course)),
  );
  el("confirmedCourse").replaceChildren(option("", "Selecciona un curso"), ...snapshot.courses.map((course) => option(course)));
  const labels = {
    libro_problemas: "Libro de problemas", libro_mixto: "Teoria y problemas", consulta: "Libro de consulta",
    practica: "Practica o separata", solucionario: "Solucionario", otro: "Otro",
  };
  el("materialType").replaceChildren(...snapshot.material_types.map((type) => option(type, labels[type] || type)));
}

function renderCounts() {
  const selected = el("courseFilter").value;
  el("courseCounts").replaceChildren(
    ...["Pendiente", ...state.snapshot.courses].map((course) => {
      const button = document.createElement("button");
      button.className = `course-count${selected === course ? " active" : ""}`;
      button.textContent = `${course === "Pendiente" ? "Sin clasificar" : course} ${state.snapshot.course_counts[course] || 0}`;
      button.addEventListener("click", () => { el("courseFilter").value = course; state.page = 1; loadCatalog(); });
      return button;
    }),
  );
}

function renderList() {
  el("visibleLabel").textContent = `${state.snapshot.total_filtered.toLocaleString("es-PE")} PDF(s)`;
  el("bookList").replaceChildren(...state.items.map((item) => {
    const button = document.createElement("button");
    button.className = `book-item${item.id === state.selectedId ? " selected" : ""}`;
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", String(item.id === state.selectedId));
    const title = document.createElement("span");
    title.className = "book-item-title";
    title.textContent = `${item.is_priority ? "PRIORIDAD - " : ""}${item.title}`;
    const meta = document.createElement("span");
    meta.className = "book-item-meta";
    const left = document.createElement("span");
    const dot = document.createElement("span");
    dot.className = `dot ${item.review_state}`;
    left.append(dot, ` ${item.confirmed_course === "Pendiente" ? "Sin clasificar" : item.confirmed_course}`);
    const right = document.createElement("span");
    right.textContent = item.multiple_choice === "si" ? "Opcion multiple" : item.review_state;
    meta.append(left, right);
    button.append(title, meta);
    button.addEventListener("click", () => selectBook(item.id));
    return button;
  }));
  el("pageLabel").textContent = `Pagina ${state.snapshot.page.toLocaleString("es-PE")} de ${state.snapshot.pages.toLocaleString("es-PE")}`;
  el("previousPageButton").disabled = state.snapshot.page <= 1;
  el("nextPageButton").disabled = state.snapshot.page >= state.snapshot.pages;
}

function currentItem() { return state.items.find((item) => item.id === state.selectedId) || null; }

function selectBook(id) {
  state.selectedId = id;
  renderList();
  const item = currentItem();
  const index = item ? state.items.findIndex((entry) => entry.id === item.id) : -1;
  const absolute = index >= 0 ? ((state.snapshot.page - 1) * state.snapshot.page_size) + index + 1 : 0;
  el("viewerPosition").textContent = item ? `${absolute.toLocaleString("es-PE")} de ${state.snapshot.total_filtered.toLocaleString("es-PE")}` : "0 de 0";
  el("viewerBookTitle").textContent = item?.title || "Selecciona un PDF";
  el("previousButton").disabled = index <= 0 && state.snapshot.page <= 1;
  el("nextButton").disabled = index < 0 || (index >= state.items.length - 1 && state.snapshot.page >= state.snapshot.pages);
  el("reviewForm").querySelectorAll("input, select, textarea, button").forEach((node) => { node.disabled = !item; });
  if (!item) {
    el("pdfFrame").classList.remove("visible");
    el("viewerEmpty").classList.remove("hidden");
    el("inspectorTitle").textContent = "Sin seleccion";
    return;
  }
  el("pdfFrame").src = item.preview_url;
  el("pdfFrame").classList.add("visible");
  el("viewerEmpty").classList.add("hidden");
  el("openDriveLink").href = item.url;
  el("openDriveLink").classList.remove("disabled");
  el("inspectorTitle").textContent = item.title;
  el("confirmedCourse").value = state.snapshot.courses.includes(item.confirmed_course) ? item.confirmed_course : "";
  el("materialType").value = item.material_type_review;
  el("notesInput").value = item.notes || "";
  const radio = document.querySelector(`input[name="multipleChoice"][value="${item.multiple_choice}"]`);
  if (radio) radio.checked = true;
  el("originalCourse").textContent = item.original_course || "Pendiente";
  el("courseScope").textContent = item.course_scope === "mixto" ? "Libro mixto" : (item.course_scope || "Por verificar");
  el("driveId").textContent = item.id;
  el("statusBadge").textContent = item.review_state;
  el("statusBadge").className = `status-badge ${item.review_state}`;
}

async function loadCatalog() {
  const serial = ++state.requestSerial;
  setMessage("Cargando inventario...");
  const params = new URLSearchParams({
    page: String(state.page), page_size: String(state.pageSize), search: el("searchInput").value.trim(),
    course: el("courseFilter").value || "todos", review_state: el("stateFilter").value || "pendiente",
  });
  const snapshot = await api(`/api/catalog?${params}`);
  if (serial !== state.requestSerial) return;
  const firstLoad = !state.snapshot;
  state.snapshot = snapshot;
  state.items = snapshot.items;
  state.page = snapshot.page;
  if (firstLoad) setupSelects(snapshot);
  renderCounts();
  updateProgress();
  selectBook(state.items[0]?.id || null);
  setMessage(`${snapshot.total.toLocaleString("es-PE")} PDF(s) disponibles.`, "success");
}

async function saveReview(reviewState) {
  const item = currentItem();
  if (!item) return;
  const course = el("confirmedCourse").value;
  if (reviewState !== "excluido" && !course) throw new Error("Selecciona el curso antes de confirmar.");
  const payload = {
    review_state: reviewState, confirmed_course: course || item.original_course,
    material_type: el("materialType").value,
    multiple_choice: document.querySelector('input[name="multipleChoice"]:checked')?.value || "por_verificar",
    notes: el("notesInput").value,
  };
  if (reviewState !== "excluido" && payload.confirmed_course !== item.original_course) payload.review_state = "reasignado";
  setMessage("Guardando clasificacion...");
  await api(`/api/books/${encodeURIComponent(item.id)}/review`, { method: "POST", body: JSON.stringify(payload) });
  await loadCatalog();
  setMessage("Clasificacion guardada.", "success");
}

async function move(delta) {
  const index = state.items.findIndex((item) => item.id === state.selectedId);
  const target = state.items[index + delta];
  if (target) return selectBook(target.id);
  if (delta > 0 && state.page < state.snapshot.pages) { state.page += 1; await loadCatalog(); }
  else if (delta < 0 && state.page > 1) { state.page -= 1; await loadCatalog(); selectBook(state.items.at(-1)?.id || null); }
}

function updateProgress() {
  const counts = state.snapshot.counts;
  const reviewed = counts.confirmado + counts.reasignado + counts.excluido;
  el("progressLabel").textContent = `${reviewed.toLocaleString("es-PE")} de ${state.snapshot.total.toLocaleString("es-PE")} revisados`;
}

function setMessage(text, kind = "") { el("message").textContent = text; el("message").className = `message ${kind}`; }

async function exportFolders() {
  setMessage("Generando carpetas por curso...");
  try {
    const result = await api("/api/export", { method: "POST", body: "{}" });
    setMessage(`${result.manifest.total} libro(s) exportados en carpetas por curso.`, "success");
  } catch (error) { setMessage(error.message, "error"); }
}

let searchTimer;
el("searchInput").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.page = 1; loadCatalog(); }, 250);
});
el("courseFilter").addEventListener("change", () => { state.page = 1; loadCatalog(); });
el("stateFilter").addEventListener("change", () => { state.page = 1; loadCatalog(); });
el("previousPageButton").addEventListener("click", () => { state.page -= 1; loadCatalog(); });
el("nextPageButton").addEventListener("click", () => { state.page += 1; loadCatalog(); });
el("previousButton").addEventListener("click", () => move(-1));
el("nextButton").addEventListener("click", () => move(1));
el("reviewForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try { await saveReview("confirmado"); } catch (error) { setMessage(error.message, "error"); }
});
el("excludeButton").addEventListener("click", async () => {
  try { await saveReview("excluido"); } catch (error) { setMessage(error.message, "error"); }
});
el("exportButton").addEventListener("click", exportFolders);
document.addEventListener("keydown", (event) => {
  if (event.target.matches("input, textarea, select")) return;
  if (event.key === "ArrowLeft") move(-1);
  if (event.key === "ArrowRight") move(1);
});

loadCatalog().catch((error) => setMessage(error.message, "error"));
