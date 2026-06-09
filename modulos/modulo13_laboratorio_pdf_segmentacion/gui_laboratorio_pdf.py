from __future__ import annotations

import os
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import fitz
from PIL import Image, ImageTk

from utils.styles import apply_openai_theme
from .controlador_laboratorio_pdf import PdfProblemGoldenController, ProblemPageRecord


def _parse_pages(raw: str, total: int) -> list[int]:
    pages: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            left, right = token.split("-", 1)
            start, end = int(left), int(right)
            if end < start:
                start, end = end, start
            pages.update(range(start, end + 1))
        else:
            pages.add(int(token))
    invalid = sorted(page for page in pages if page < 1 or page > total)
    if invalid:
        raise ValueError(f"Paginas fuera del PDF: {invalid}")
    return sorted(pages)


class PdfSegmentationLabWindow(tk.Toplevel):
    def __init__(
        self,
        parent,
        *,
        initial_instance_name: str = "",
        initial_pdf_path: str = "",
        initial_title: str = "",
        linked_session_path: str = "",
        book_code: str = "",
        instance_type: str = "",
        project_name: str = "",
    ):
        super().__init__(parent)
        self.title(initial_title or "Modulo 13 - Golden Base de recortes de problemas")
        self.geometry("1040x720")
        self.minsize(860, 620)
        apply_openai_theme(self)
        self.controller = PdfProblemGoldenController()
        self.instance_var = tk.StringVar(value=initial_instance_name or "piloto_recortes_problemas")
        self.pdf_var = tk.StringVar(value=initial_pdf_path)
        self.pages_var = tk.StringVar()
        self.dpi_var = tk.StringVar(value="300")
        self.status_var = tk.StringVar(value="Elige un PDF, indica sus paginas y procesalo con IA. Luego corrige los boxes y guarda.")
        self.summary_var = tk.StringVar(value="Golden independiente: detector de problemas completos por pagina.")
        self.rows: list[ProblemPageRecord] = []
        self.linked_session_path = linked_session_path
        self.book_code = book_code
        self.instance_type = instance_type
        self.project_name = project_name
        self._editor: ProblemBoxEditorWindow | None = None
        self._build_ui()
        self.after(150, self._initialize_from_context)

    def _initialize_from_context(self) -> None:
        loaded = self.controller.load_instance(self.instance_var.get())
        if loaded:
            self.rows = loaded
            self._refresh()
            self.status_var.set("Instancia Golden cargada desde Biblioteca. Puedes continuar corrigiendo o agregar paginas.")
        pdf_path = Path(self.pdf_var.get()).expanduser()
        if pdf_path.exists() and not self.pages_var.get().strip():
            try:
                with fitz.open(pdf_path) as document:
                    self.pages_var.set(f"1-{document.page_count}")
            except Exception:
                pass

    def _build_ui(self) -> None:
        header = ttk.Frame(self, padding=12)
        header.pack(fill="x")
        ttk.Label(header, text="Golden Base: cajas de problemas matematicos", font=("Segoe UI", 17, "bold")).pack(anchor="w")
        ttk.Label(header, text="Flujo: elegir PDF -> detectar boxes con IA -> corregir en el editor -> guardar en Golden Base.").pack(anchor="w")
        controls = ttk.LabelFrame(self, text="Instancia de entrenamiento", padding=9)
        controls.pack(fill="x", padx=12)
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="Instancia").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.instance_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(controls, text="Cargar instancia", command=self._load_instance).grid(row=0, column=2)
        ttk.Button(controls, text="Guardar cambios", command=self._save_instance).grid(row=0, column=3, padx=(6, 0))
        ttk.Label(controls, text="PDF").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(controls, textvariable=self.pdf_var).grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))
        ttk.Button(controls, text="Elegir PDF", command=self._choose_pdf).grid(row=1, column=2, pady=(6, 0))
        ttk.Button(controls, text="Agregar imagenes", command=self._add_images).grid(row=1, column=3, padx=(6, 0), pady=(6, 0))
        ttk.Label(controls, text="Paginas").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(controls, textvariable=self.pages_var).grid(row=2, column=1, sticky="ew", padx=6, pady=(6, 0))
        ttk.Label(controls, text="Ej.: 3-7, 12, 18").grid(row=2, column=2, sticky="w", pady=(6, 0))
        ttk.Combobox(controls, textvariable=self.dpi_var, values=("200", "300", "400"), width=6, state="readonly").grid(row=2, column=3, pady=(6, 0))
        ttk.Button(controls, text="Procesar PDF con IA", command=self._add_pages, style="Accent.TButton").grid(row=2, column=4, padx=(6, 0), pady=(6, 0))
        ttk.Label(self, textvariable=self.status_var).pack(fill="x", padx=14, pady=(6, 0))
        ttk.Label(self, textvariable=self.summary_var).pack(fill="x", padx=14, pady=(0, 6))
        body = ttk.LabelFrame(self, text="Paginas acumuladas", padding=8)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.page_tree = ttk.Treeview(body, columns=("pdf", "page", "layout", "boxes"), show="headings")
        for key, label, width in (("pdf", "PDF", 460), ("page", "Pagina", 70), ("layout", "Composicion", 120), ("boxes", "Boxes", 80)):
            self.page_tree.heading(key, text=label)
            self.page_tree.column(key, width=width)
        self.page_tree.pack(fill="both", expand=True)
        self.page_tree.bind("<Double-Button-1>", lambda _event: self._open_editor())
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="Editar boxes de pagina", command=self._open_editor, style="Accent.TButton").pack(side="left")
        ttk.Button(buttons, text="Detectar boxes con IA", command=self._detect_selected).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Detectar IA en todas", command=self._detect_all).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Eliminar pagina", command=self._delete_page).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Preparar recortes OCR + segmentacion", command=self._prepare_downstream_crops).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Abrir carpeta Golden", command=self._open_folder).pack(side="right")

    def _choose_pdf(self) -> None:
        raw = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf")])
        if not raw:
            return
        self.pdf_var.set(raw)
        with fitz.open(raw) as document:
            self.pages_var.set(f"1-{document.page_count}")
        self.status_var.set("PDF listo. Reduce el rango para agregar una muestra variada.")

    def _add_pages(self) -> None:
        pdf_path = Path(self.pdf_var.get()).expanduser().resolve()
        if not pdf_path.exists():
            messagebox.showwarning("Golden PDF", "Elige un PDF valido.")
            return
        first_new_index = len(self.rows)
        try:
            with fitz.open(pdf_path) as document:
                pages = _parse_pages(self.pages_var.get(), document.page_count)
                if not pages:
                    raise ValueError("Indica al menos una pagina del PDF.")
                matrix = fitz.Matrix(int(self.dpi_var.get()) / 72.0, int(self.dpi_var.get()) / 72.0)
                temp = Path(tempfile.mkdtemp(prefix="pdf_problem_pages_"))
                for position, page_number in enumerate(pages, start=1):
                    self.status_var.set(f"Procesando PDF con IA: {position}/{len(pages)} | pagina {page_number}")
                    self.update_idletasks()
                    rendered = temp / f"page_{page_number:04d}.png"
                    document[page_number - 1].get_pixmap(matrix=matrix, alpha=False).save(str(rendered))
                    row = self.controller.add_rendered_page(self.instance_var.get(), pdf_path=pdf_path, page_number=page_number, rendered_path=rendered)
                    row.boxes = self.controller.predict_boxes(row.image_path, layout_mode=row.layout_mode)
                    row.detector_source = "modelo_entrenado_corregible"
                    row.reviewed = False
                    self.rows.append(row)
        except Exception as exc:
            messagebox.showerror("Golden PDF", str(exc))
            return
        self._refresh(first_new_index)
        self.status_var.set(f"IA termino {len(pages)} pagina(s). Corrige los boxes y guarda la Golden Base.")
        self._open_editor()

    def _add_images(self) -> None:
        raw_paths = filedialog.askopenfilenames(filetypes=[("Imagenes", "*.png *.jpg *.jpeg *.webp *.bmp")])
        if not raw_paths:
            return
        for raw_path in raw_paths:
            self.rows.append(self.controller.add_image(self.instance_var.get(), image_path=Path(raw_path)))
        self._refresh(len(self.rows) - 1)
        self.status_var.set(f"Se agregaron {len(raw_paths)} imagen(es). Usa Detectar boxes con IA y corrige si hace falta.")

    def _detect_selected(self) -> None:
        if not self.rows:
            return
        self._detect_indexes([self._selected_index()])

    def _detect_all(self) -> None:
        if self.rows:
            self._detect_indexes(list(range(len(self.rows))))

    def _detect_indexes(self, indexes: list[int]) -> None:
        self.status_var.set("Cargando modelo y detectando boxes...")
        self.update_idletasks()
        try:
            for position, index in enumerate(indexes, start=1):
                row = self.rows[index]
                row.boxes = self.controller.predict_boxes(row.image_path, layout_mode=row.layout_mode)
                row.detector_source = "modelo_entrenado_corregible"
                row.reviewed = False
                self.status_var.set(f"IA: {position}/{len(indexes)} | {Path(row.image_path).name} | boxes={len(row.boxes)}")
                self.update_idletasks()
        except Exception as exc:
            messagebox.showerror("Golden PDF", f"No se pudo ejecutar el detector:\n{exc}")
            return
        self._refresh(indexes[-1])
        if self._editor and self._editor.winfo_exists():
            self._editor.set_index(indexes[-1])
        self.status_var.set(f"Deteccion terminada en {len(indexes)} imagen(es). Revisa y ajusta los boxes antes de guardar.")

    def _load_instance(self) -> None:
        self.rows = self.controller.load_instance(self.instance_var.get())
        self._refresh()
        self.status_var.set("Instancia cargada.")

    def _save_instance(self, *, show_message: bool = True) -> None:
        folder = self.controller.save_instance(self.instance_var.get(), self.rows)
        self.status_var.set(f"Cambios guardados en {folder}")
        if show_message:
            messagebox.showinfo("Golden PDF", f"Golden Base guardada:\n{folder}")

    def _refresh(self, selected_index: int | None = None) -> None:
        for item in self.page_tree.get_children():
            self.page_tree.delete(item)
        for index, row in enumerate(self.rows):
            self.page_tree.insert("", "end", iid=str(index), values=(Path(row.pdf_path).name, row.page_number, row.layout_mode, len(row.boxes)))
        self.summary_var.set(
            f"Paginas: {len(self.rows)} | Boxes: {sum(len(row.boxes) for row in self.rows)} | "
            f"Revisadas: {sum(1 for row in self.rows if row.reviewed)}"
        )
        if self.rows:
            index = max(0, min(len(self.rows) - 1, selected_index if selected_index is not None else 0))
            self.page_tree.selection_set(str(index))
            self.page_tree.focus(str(index))

    def _selected_index(self) -> int:
        selected = self.page_tree.selection()
        return int(selected[0]) if selected else 0

    def _open_editor(self) -> None:
        if not self.rows:
            messagebox.showinfo("Golden PDF", "Agrega paginas antes de abrir el editor.")
            return
        index = self._selected_index()
        if self._editor and self._editor.winfo_exists():
            self._editor.set_index(index)
            self._editor.deiconify()
            self._editor.lift()
            return
        self._editor = ProblemBoxEditorWindow(self, index=index)

    def _delete_page(self) -> None:
        if not self.rows:
            return
        index = self._selected_index()
        del self.rows[index]
        self._refresh(max(0, index - 1))

    def _open_folder(self) -> None:
        folder = self.controller.instance_dir(self.instance_var.get())
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))

    def _prepare_downstream_crops(self) -> None:
        if not self.rows:
            messagebox.showinfo("Golden PDF", "No hay paginas corregidas para preparar.")
            return
        total = sum(len(row.boxes) for row in self.rows)
        if total <= 0:
            messagebox.showinfo("Golden PDF", "No hay boxes para materializar. Ejecuta la IA o crea boxes manuales.")
            return
        self.status_var.set("Materializando recortes y sincronizando con Modulo 12...")

        def worker() -> None:
            try:
                target, crop_ids = self.controller.materialize_problem_crops_for_downstream(
                    self.instance_var.get(),
                    self.rows,
                    return_crop_ids=True,
                    session_path=Path(self.linked_session_path) if str(self.linked_session_path or "").strip() else None,
                    book_code=self.book_code,
                    instance_type=self.instance_type,
                    project_name=self.project_name,
                    pdf_path=self.pdf_var.get(),
                )
                from modulos.modulo12_auditor_entrenamiento.controlador_auditor_entrenamiento import (
                    TrainingAuditController,
                )

                audit = TrainingAuditController()
                self.after(0, lambda: self.status_var.set("Sincronizando recortes con Golden OCR..."))
                ocr_target, ocr_added = audit.import_problem_crops_into_ocr_golden(
                    crops_root=target,
                    crop_ids=crop_ids,
                    session_json=str(Path(self.linked_session_path)) if str(self.linked_session_path or "").strip() else "",
                    book_code=self.book_code,
                    instance_type=self.instance_type,
                    project_name=self.project_name,
                )

                def progress(index: int, total_count: int, processed: int, positives: int, boxes: int) -> None:
                    self.after(
                        0,
                        lambda index=index, total_count=total_count, processed=processed: self.status_var.set(
                            f"Sincronizando Golden Segmentos: {index}/{total_count} | nuevos={processed}"
                        ),
                    )

                segment_target, segment_added, _positives, _boxes = audit.import_problem_crops_into_segment_golden(
                    crops_root=target,
                    crop_ids=crop_ids,
                    progress_callback=progress,
                )
                session_sources = None
                session_added = 0
                if str(self.linked_session_path or "").strip():
                    self.after(0, lambda: self.status_var.set("Agregando recortes como imagenes normales de la sesion..."))
                    session_sources, session_added = self.controller.sync_problem_crops_to_transcriptor_session(
                        self.instance_var.get(),
                        self.rows,
                        session_path=Path(self.linked_session_path),
                        book_code=self.book_code,
                        instance_type=self.instance_type,
                        project_name=self.project_name,
                        pdf_path=self.pdf_var.get(),
                    )
            except Exception as exc:
                self.after(0, lambda exc=exc: messagebox.showerror("Golden PDF", f"No se pudieron preparar los recortes:\n{exc}"))
                self.after(0, lambda: self.status_var.set("Preparacion detenida por error."))
                return

            def done() -> None:
                self.status_var.set(
                    f"Recortes preparados: {total} | OCR nuevos={ocr_added} | Segmentos nuevos={segment_added} | Sesion={session_added}"
                )
                session_line = (
                    f"Imagenes de sesion: {session_sources} | agregadas/sincronizadas: {session_added}\n"
                    if session_sources is not None
                    else "Imagenes de sesion: no sincronizadas porque no hay sesion enlazada.\n"
                )
                messagebox.showinfo(
                    "Golden PDF",
                    "Recortes listos y reflejados en Modulo 12.\n\n"
                    f"Problemas PDF: {target}\n"
                    f"Golden OCR: {ocr_target} | nuevos: {ocr_added}\n"
                    f"Golden Segmentos: {segment_target} | nuevos: {segment_added}\n\n"
                    f"{session_line}"
                    f"Total de recortes: {total}",
                )

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()


class ProblemBoxEditorWindow(tk.Toplevel):
    def __init__(self, lab: PdfSegmentationLabWindow, *, index: int):
        super().__init__(lab)
        self.lab = lab
        self.index = index
        self.title("Editor manual de boxes - problemas completos")
        self.geometry("1480x900")
        self.minsize(1080, 700)
        apply_openai_theme(self)
        self.status_var = tk.StringVar()
        self.layout_var = tk.StringVar(value="auto")
        self._photo = None
        self._scale = 1.0
        self._zoom = 1.0
        self._offset = (0, 0)
        self._drag_start: tuple[int, int] | None = None
        self._draft_id = None
        self._new_box_mode = False
        self._selected_box_index: int | None = None
        self._resize_corner: int | None = None
        self._build_ui()
        self._bind_shortcuts()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(50, self._refresh)

    def _build_ui(self) -> None:
        controls = ttk.Frame(self, padding=8)
        controls.pack(fill="x")
        ttk.Button(controls, text="Guardar bloque", command=self._save, style="Accent.TButton").pack(side="left")
        ttk.Button(controls, text="Nuevo cuadro (N)", command=self._start_new_box).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Eliminar seleccionado (Del)", command=self._delete_box).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Subir orden", command=lambda: self._move_box_order(-1)).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Bajar orden", command=lambda: self._move_box_order(1)).pack(side="left", padx=(8, 0))
        ttk.Label(controls, text="Composicion").pack(side="left", padx=(14, 4))
        layout_combo = ttk.Combobox(
            controls,
            textvariable=self.layout_var,
            values=("auto", "una_columna", "dos_columnas"),
            width=14,
            state="readonly",
        )
        layout_combo.pack(side="left")
        layout_combo.bind("<<ComboboxSelected>>", self._change_layout)
        ttk.Button(controls, text="Reordenar", command=self._reorder_boxes).pack(side="left", padx=(6, 0))
        ttk.Button(controls, text="Anterior pagina (A)", command=lambda: self._move(-1)).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Siguiente pagina (S)", command=lambda: self._move(1)).pack(side="left", padx=(8, 0))
        ttk.Label(controls, textvariable=self.status_var).pack(side="right")
        ttk.Label(
            self,
            text="Anota solo problemas completos. Ignora subtitulos aislados y marcas de agua. Vista: Ctrl+rueda = zoom | rueda = subir/bajar | Shift+rueda = izquierda/derecha",
        ).pack(fill="x", padx=10, pady=(0, 4))
        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        canvas_frame = ttk.Frame(body)
        side = ttk.LabelFrame(body, text="Boxes", padding=6)
        body.add(canvas_frame, weight=5)
        body.add(side, weight=1)
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(canvas_frame, bg="#e5e7eb", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        xbar = ttk.Scrollbar(canvas_frame, orient="horizontal", command=self.canvas.xview)
        ybar = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.canvas.yview)
        xbar.grid(row=1, column=0, sticky="ew")
        ybar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(xscrollcommand=xbar.set, yscrollcommand=ybar.set)
        self.canvas.bind("<Configure>", lambda _event: self._render())
        self.canvas.bind("<ButtonPress-1>", self._drag_begin)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._drag_end)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.box_tree = ttk.Treeview(side, columns=("idx", "bbox"), show="headings")
        self.box_tree.heading("idx", text="#")
        self.box_tree.heading("bbox", text="Box px")
        self.box_tree.column("idx", width=36)
        self.box_tree.column("bbox", width=190)
        self.box_tree.pack(fill="both", expand=True)
        self.box_tree.bind("<<TreeviewSelect>>", self._select_tree_box)
        ttk.Button(side, text="Vaciar pagina", command=self._clear_boxes).pack(fill="x", pady=(6, 0))

    def _bind_shortcuts(self) -> None:
        for target in (self, self.canvas):
            target.bind("<KeyPress-n>", lambda _event: self._shortcut(self._start_new_box))
            target.bind("<KeyPress-N>", lambda _event: self._shortcut(self._start_new_box))
            target.bind("<KeyPress-a>", lambda _event: self._shortcut(lambda: self._move(-1)))
            target.bind("<KeyPress-A>", lambda _event: self._shortcut(lambda: self._move(-1)))
            target.bind("<KeyPress-s>", lambda _event: self._shortcut(lambda: self._move(1)))
            target.bind("<KeyPress-S>", lambda _event: self._shortcut(lambda: self._move(1)))
            target.bind("<Delete>", lambda _event: self._shortcut(self._delete_box))
            target.bind("<Control-s>", lambda _event: self._shortcut(self._save))
            target.bind("<Control-S>", lambda _event: self._shortcut(self._save))
            target.bind("<Control-Up>", lambda _event: self._shortcut(lambda: self._move_box_order(-1)))
            target.bind("<Control-Down>", lambda _event: self._shortcut(lambda: self._move_box_order(1)))

    @staticmethod
    def _shortcut(action) -> str:
        action()
        return "break"

    def _row(self) -> ProblemPageRecord:
        return self.lab.rows[self.index]

    def set_index(self, index: int) -> None:
        self.index = max(0, min(len(self.lab.rows) - 1, index))
        self._zoom = 1.0
        self._refresh()

    def _refresh(self) -> None:
        row = self._row()
        self._new_box_mode = False
        self.layout_var.set(row.layout_mode)
        self.status_var.set(f"{Path(row.pdf_path).name} | pagina {row.page_number} | {self.index + 1}/{len(self.lab.rows)}")
        for item in self.box_tree.get_children():
            self.box_tree.delete(item)
        for index, box in enumerate(row.boxes):
            self.box_tree.insert("", "end", iid=str(index), values=(index + 1, ", ".join(str(value) for value in box)))
        self._render()
        self.canvas.focus_set()
        self.lab._refresh(self.index)

    def _render(self) -> None:
        self.canvas.delete("all")
        image = Image.open(self._row().image_path).convert("RGB")
        cw, ch = max(50, self.canvas.winfo_width()), max(50, self.canvas.winfo_height())
        fit_scale = min((cw - 10) / image.width, (ch - 10) / image.height)
        self._scale = max(0.05, fit_scale * self._zoom)
        resized = image.resize((max(1, int(image.width * self._scale)), max(1, int(image.height * self._scale))))
        self._offset = (max(5, (cw - resized.width) // 2), max(5, (ch - resized.height) // 2))
        self._photo = ImageTk.PhotoImage(resized)
        self.canvas.create_image(*self._offset, image=self._photo, anchor="nw")
        for index, box in enumerate(self._row().boxes, start=1):
            x1, y1, x2, y2 = self._to_canvas_box(box)
            selected = self._selected_box_index == index - 1
            self.canvas.create_rectangle(x1, y1, x2, y2, outline="#2563eb" if selected else "#ef4444", width=4 if selected else 3)
            self.canvas.create_text(x1 + 6, y1 + 6, text=str(index), fill="#dc2626", anchor="nw", font=("Segoe UI", 12, "bold"))
            if selected:
                for hx, hy in ((x1, y1), (x2, y1), (x2, y2), (x1, y2)):
                    self.canvas.create_rectangle(hx - 6, hy - 6, hx + 6, hy + 6, fill="#2563eb", outline="white")
        self.canvas.configure(scrollregion=(0, 0, max(cw, self._offset[0] + resized.width + 5), max(ch, self._offset[1] + resized.height + 5)))

    def _to_canvas_box(self, box) -> tuple[int, int, int, int]:
        ox, oy = self._offset
        return tuple(int(round(value * self._scale + (ox if index % 2 == 0 else oy))) for index, value in enumerate(box))

    def _start_new_box(self) -> None:
        self._new_box_mode = True
        self.canvas.focus_set()
        self.status_var.set("Nuevo cuadro: arrastra sobre el problema completo.")

    def _drag_begin(self, event) -> None:
        self.canvas.focus_set()
        x, y = int(self.canvas.canvasx(event.x)), int(self.canvas.canvasy(event.y))
        if not self._new_box_mode:
            selection = self._find_box_corner(x, y)
            if selection is None:
                return
            self._selected_box_index, self._resize_corner = selection
            self.box_tree.selection_set(str(self._selected_box_index))
            self._drag_start = (x, y)
            self._render()
            return
        self._drag_start = (x, y)
        self._draft_id = self.canvas.create_rectangle(x, y, x, y, outline="#2563eb", width=2)

    def _drag_move(self, event) -> None:
        if self._resize_corner is not None and self._selected_box_index is not None:
            self._resize_selected(self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
            self._render()
            return
        if self._drag_start and self._draft_id:
            self.canvas.coords(self._draft_id, *self._drag_start, self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))

    def _drag_end(self, event) -> None:
        if self._resize_corner is not None:
            self._resize_selected(self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
            self._resize_corner = None
            self._drag_start = None
            self._refresh()
            return
        if not self._drag_start:
            return
        ox, oy = self._offset
        x1, y1 = self._drag_start
        x2, y2 = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        box = tuple(int(round(value)) for value in ((min(x1, x2) - ox) / self._scale, (min(y1, y2) - oy) / self._scale, (max(x1, x2) - ox) / self._scale, (max(y1, y2) - oy) / self._scale))
        self._drag_start = None
        self._new_box_mode = False
        if box[2] - box[0] >= 20 and box[3] - box[1] >= 20:
            self._row().boxes.append(box)
        self._refresh()

    def _delete_box(self) -> None:
        selected = self.box_tree.selection()
        if selected:
            del self._row().boxes[int(selected[0])]
            self._selected_box_index = None
            self._refresh()

    def _clear_boxes(self) -> None:
        self._row().boxes.clear()
        self._selected_box_index = None
        self._refresh()

    def _move_box_order(self, delta: int) -> None:
        selected = self.box_tree.selection()
        if not selected:
            return
        current = int(selected[0])
        target = max(0, min(len(self._row().boxes) - 1, current + delta))
        if current == target:
            return
        box = self._row().boxes.pop(current)
        self._row().boxes.insert(target, box)
        self._selected_box_index = target
        self._refresh()
        self.box_tree.selection_set(str(target))
        self.box_tree.focus(str(target))

    def _change_layout(self, _event=None) -> None:
        self._row().layout_mode = self.layout_var.get()
        self._reorder_boxes()

    def _reorder_boxes(self) -> None:
        self.lab.controller.reorder_boxes(self._row())
        self._selected_box_index = None
        self._refresh()

    def _select_tree_box(self, _event=None) -> None:
        selected = self.box_tree.selection()
        self._selected_box_index = int(selected[0]) if selected else None
        self._render()

    def _find_box_corner(self, x: float, y: float) -> tuple[int, int] | None:
        radius = 16
        for box_index, box in enumerate(self._row().boxes):
            x1, y1, x2, y2 = self._to_canvas_box(box)
            for corner, (hx, hy) in enumerate(((x1, y1), (x2, y1), (x2, y2), (x1, y2))):
                if abs(x - hx) <= radius and abs(y - hy) <= radius:
                    return box_index, corner
        return None

    def _resize_selected(self, canvas_x: float, canvas_y: float) -> None:
        if self._selected_box_index is None or self._resize_corner is None:
            return
        ox, oy = self._offset
        image_x = int(round((canvas_x - ox) / self._scale))
        image_y = int(round((canvas_y - oy) / self._scale))
        x1, y1, x2, y2 = self._row().boxes[self._selected_box_index]
        if self._resize_corner == 0:
            x1, y1 = image_x, image_y
        elif self._resize_corner == 1:
            x2, y1 = image_x, image_y
        elif self._resize_corner == 2:
            x2, y2 = image_x, image_y
        else:
            x1, y2 = image_x, image_y
        self._row().boxes[self._selected_box_index] = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))

    def _on_mousewheel(self, event) -> str:
        delta = int(getattr(event, "delta", 0) or 0)
        step = -1 if delta > 0 else 1
        state = int(getattr(event, "state", 0) or 0)
        if state & 0x4:
            factor = 1.15 if delta > 0 else (1.0 / 1.15)
            self._zoom = max(0.5, min(6.0, self._zoom * factor))
            self._render()
            return "break"
        if state & 0x1:
            self.canvas.xview_scroll(step * 3, "units")
            return "break"
        self.canvas.yview_scroll(step * 3, "units")
        return "break"

    def _move(self, delta: int) -> None:
        self.set_index(self.index + delta)

    def _save(self) -> None:
        self.lab._save_instance(show_message=False)
        self.status_var.set("Bloque guardado.")

    def _close(self) -> None:
        self.lab._refresh(self.index)
        self.destroy()
