from __future__ import annotations

import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

import fitz

from utils.styles import apply_openai_theme

from .models import InstancePipelineContext, PipelineStep, StageStatus, StagingProblemRecord
from .pipeline import InstancePdfPipelineService


class InstancePdfFactoryWindow(tk.Toplevel):
    def __init__(self, parent, *, context: InstancePipelineContext) -> None:
        super().__init__(parent)
        self.context = context
        self.service = InstancePdfPipelineService(context)
        self.title(f"Fabrica PDF - {context.book_code} / {context.instance_type}")
        self.geometry("1320x820")
        self.minsize(1080, 680)
        self.palette = apply_openai_theme(self)

        self.pages_var = tk.StringVar(value="")
        self.dpi_var = tk.StringVar(value="300")
        self.provider_var = tk.StringVar(value="hf")
        self.curso_var = tk.StringVar(value="SIN_CURSO")
        self.tema_var = tk.StringVar(value="SIN_TEMA")
        self.status_var = tk.StringVar(value="Lista para trabajar sobre staging por instancia.")
        self.models_var = tk.StringVar(value="")
        self.detail_var = tk.StringVar(value="Selecciona un problema de staging para ver su detalle.")
        self.review_ready_var = tk.BooleanVar(value=False)
        self._selected_record_id = ""
        self._action_buttons: list[ttk.Button] = []
        self._workflow_buttons: dict[str, ttk.Button] = {}
        self._form_vars: dict[str, tk.Variable] = {}
        self._text_fields: dict[str, tk.Text] = {}
        self._record_detail_text: tk.Text | None = None
        self._summary_vars: dict[str, tk.StringVar] = {}
        self._source_vars: dict[str, tk.StringVar] = {}
        self._review_status_var = tk.StringVar(value="Selecciona un registro de staging.")
        self._page_tree: ttk.Treeview | None = None
        self._ocr_items_tree: ttk.Treeview | None = None
        self._segments_tree: ttk.Treeview | None = None
        self._ocr_text: tk.Text | None = None
        self._selected_segment_path = ""
        self._ocr_item_payloads: dict[str, dict[str, Any]] = {}
        self._busy = False

        self._build_ui()
        self._initialize()

    def _build_ui(self) -> None:
        header = ttk.Frame(self, padding=14)
        header.pack(fill="x")
        ttk.Label(header, text="Fabrica PDF -> Staging", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text=f"{self.context.book_code} / {self.context.instance_type} | nada se inserta directo en problemas",
            style="SubHeader.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        controls = ttk.Frame(self, style="Card.TFrame", padding=12)
        controls.pack(fill="x", padx=14, pady=(0, 10))
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="Paginas").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.pages_var).grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Label(controls, text="DPI").grid(row=0, column=2, sticky="w")
        ttk.Combobox(controls, textvariable=self.dpi_var, values=("200", "300", "400"), width=6, state="readonly").grid(
            row=0,
            column=3,
            sticky="w",
            padx=(8, 14),
        )
        self._grid_action_button(
            controls,
            text="1. Paginas + boxes",
            command=self._detect_pages,
            row=0,
            column=4,
            style="Accent.TButton",
            key="detect",
        )
        self._grid_action_button(
            controls,
            text="2. Crops -> staging",
            command=self._materialize_staging,
            row=0,
            column=5,
            key="materialize",
        )
        self._grid_action_button(
            controls,
            text="3. OCR + segmentar",
            command=self._run_ocr_segments,
            row=0,
            column=6,
            key="ocr",
        )
        self._grid_action_button(
            controls,
            text="4. Normalizar",
            command=self._normalize_existing,
            row=0,
            column=7,
            padx=(0, 0),
            key="normalize",
        )

        ttk.Label(controls, text="Curso").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(controls, textvariable=self.curso_var, width=22).grid(row=1, column=1, sticky="w", padx=(8, 8), pady=(8, 0))
        ttk.Label(controls, text="Tema").grid(row=1, column=2, sticky="w", pady=(8, 0))
        ttk.Entry(controls, textvariable=self.tema_var, width=24).grid(row=1, column=3, sticky="w", padx=(8, 14), pady=(8, 0))
        ttk.Label(controls, text="Proveedor OCR").grid(row=1, column=4, sticky="e", pady=(8, 0))
        ttk.Combobox(controls, textvariable=self.provider_var, values=("hf", "openai", "ocr"), width=9, state="readonly").grid(
            row=1,
            column=5,
            sticky="w",
            pady=(8, 0),
        )
        self._grid_action_button(
            controls,
            text="Abrir revisor de boxes",
            command=self._open_pdf_box_reviewer,
            row=1,
            column=6,
            pady=(8, 0),
            key="box_reviewer",
        )
        ttk.Button(controls, text="Carpeta staging", command=self._open_staging_folder).grid(row=1, column=7, sticky="ew", pady=(8, 0))

        ttk.Label(self, textvariable=self.status_var, style="Muted.TLabel").pack(fill="x", padx=16)
        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", padx=16, pady=(4, 0))
        ttk.Label(self, textvariable=self.models_var, style="Muted.TLabel", wraplength=1200).pack(fill="x", padx=16, pady=(2, 8))

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        left = ttk.Frame(body)
        body.add(left, weight=3)
        right = ttk.Notebook(body)
        body.add(right, weight=2)

        steps = ttk.LabelFrame(left, text="Pasos de la instancia", padding=8)
        steps.pack(fill="x", pady=(0, 8))
        self.steps_tree = ttk.Treeview(steps, columns=("etapa", "estado", "detalle"), show="headings", height=6)
        self.steps_tree.heading("etapa", text="Etapa")
        self.steps_tree.heading("estado", text="Estado")
        self.steps_tree.heading("detalle", text="Detalle")
        self.steps_tree.column("etapa", width=150)
        self.steps_tree.column("estado", width=130)
        self.steps_tree.column("detalle", width=590)
        self.steps_tree.pack(fill="x")
        self._configure_status_tags(self.steps_tree)

        records = ttk.LabelFrame(left, text="Staging por problema", padding=8)
        records.pack(fill="both", expand=True)
        self.records_tree = ttk.Treeview(
            records,
            columns=("estado", "pagina", "crop_estado", "ocr", "segmentos", "normalizado", "revision", "errores", "numero", "crop"),
            show="headings",
        )
        for key, label, width in (
            ("estado", "Estado", 120),
            ("pagina", "Pagina", 70),
            ("crop_estado", "Crop", 80),
            ("ocr", "OCR", 70),
            ("segmentos", "Seg.", 70),
            ("normalizado", "Norm.", 90),
            ("revision", "Revision", 90),
            ("errores", "Err.", 55),
            ("numero", "N", 50),
            ("crop", "Archivo", 330),
        ):
            self.records_tree.heading(key, text=label)
            self.records_tree.column(key, width=width)
        self.records_tree.pack(fill="both", expand=True)
        self.records_tree.bind("<<TreeviewSelect>>", lambda _e: self._load_selected_record())
        self._configure_status_tags(self.records_tree)

        summary_tab = ttk.Frame(right, padding=10)
        review_tab = ttk.Frame(right, padding=10)
        ocr_tab = ttk.Frame(right, padding=10)
        detail_tab = ttk.Frame(right, padding=10)
        right.add(summary_tab, text="Tablero")
        right.add(review_tab, text="Revision")
        right.add(ocr_tab, text="OCR y segmentos")
        right.add(detail_tab, text="Detalle")
        self._build_summary_tab(summary_tab)
        self._build_review_form(review_tab)
        self._build_ocr_segments_tab(ocr_tab)
        self._build_record_details(detail_tab)

    def _grid_action_button(
        self,
        parent: ttk.Frame,
        *,
        text: str,
        command,
        row: int,
        column: int,
        style: str | None = None,
        padx=(0, 6),
        pady=0,
        key: str = "",
    ) -> None:
        options = {"text": text, "command": command}
        if style:
            options["style"] = style
        button = ttk.Button(parent, **options)
        button.grid(row=row, column=column, sticky="ew", padx=padx, pady=pady)
        self._action_buttons.append(button)
        if key:
            self._workflow_buttons[key] = button

    def _status_label(self, status: object) -> str:
        normalized = StageStatus.normalize(str(status or ""))
        return {
            StageStatus.PENDING: "pendiente",
            StageStatus.PROCESSING: "procesando",
            StageStatus.READY: "listo",
            StageStatus.NEEDS_REVIEW: "requiere_revision",
            StageStatus.ERROR: "error",
        }.get(normalized, str(status or ""))

    def _status_colors(self, status: object) -> tuple[str, str]:
        normalized = StageStatus.normalize(str(status or ""))
        return {
            StageStatus.PENDING: ("#f8fafc", "#334155"),
            StageStatus.PROCESSING: ("#fef3c7", "#78350f"),
            StageStatus.READY: ("#dcfce7", "#14532d"),
            StageStatus.NEEDS_REVIEW: ("#dbeafe", "#1e3a8a"),
            StageStatus.ERROR: ("#fee2e2", "#7f1d1d"),
        }.get(normalized, ("#f8fafc", "#334155"))

    def _configure_status_tags(self, tree: ttk.Treeview) -> None:
        for status in StageStatus.values():
            bg, fg = self._status_colors(status)
            tree.tag_configure(status, background=bg, foreground=fg)

    def _build_summary_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        metrics = ttk.LabelFrame(parent, text="Avance de instancia", padding=8)
        metrics.grid(row=0, column=0, sticky="ew")
        metric_fields = [
            ("pages_total", "Paginas"),
            ("boxes_total", "Boxes"),
            ("crops_found", "Crops"),
            ("ocr_done", "OCR"),
            ("segments_done", "Segmentacion"),
            ("normalized_done", "Normalizados"),
            ("ready", "Listos"),
            ("errors", "Errores"),
        ]
        for idx, (name, label) in enumerate(metric_fields):
            var = tk.StringVar(value="0")
            self._summary_vars[name] = var
            cell = ttk.Frame(metrics, style="Card.TFrame", padding=(6, 4))
            cell.grid(row=idx // 4, column=idx % 4, sticky="ew", padx=4, pady=4)
            ttk.Label(cell, text=label, style="FieldLabel.TLabel").pack(anchor="w")
            ttk.Label(cell, textvariable=var, style="LabelTitle.TLabel").pack(anchor="w", pady=(2, 0))
            metrics.columnconfigure(idx % 4, weight=1)

        legend = ttk.LabelFrame(parent, text="Estados", padding=8)
        legend.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self._build_status_legend(legend)

        pages = ttk.LabelFrame(parent, text="Paginas y boxes", padding=8)
        pages.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        parent.rowconfigure(2, weight=1)
        pages.rowconfigure(1, weight=1)
        pages.columnconfigure(0, weight=1)
        actions = ttk.Frame(pages)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(actions, text="Abrir revisor de boxes", command=self._open_pdf_box_reviewer, style="Accent.TButton").pack(side="left")
        ttk.Button(actions, text="Abrir pagina", command=self._open_selected_page_image).pack(side="left", padx=(8, 0))
        self._page_tree = ttk.Treeview(
            pages,
            columns=("pagina", "estado", "boxes", "revisada", "layout", "origen"),
            show="headings",
            height=8,
        )
        for key, label, width in (
            ("pagina", "Pagina", 70),
            ("estado", "Estado", 110),
            ("boxes", "Boxes", 60),
            ("revisada", "Rev.", 55),
            ("layout", "Layout", 100),
            ("origen", "Detector", 260),
        ):
            self._page_tree.heading(key, text=label)
            self._page_tree.column(key, width=width)
        self._page_tree.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(pages, orient="vertical", command=self._page_tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self._page_tree.configure(yscrollcommand=scroll.set)
        self._configure_status_tags(self._page_tree)

    def _build_status_legend(self, parent: ttk.Frame) -> None:
        for idx, status in enumerate(
            (
                StageStatus.PENDING,
                StageStatus.PROCESSING,
                StageStatus.READY,
                StageStatus.NEEDS_REVIEW,
                StageStatus.ERROR,
            )
        ):
            bg, fg = self._status_colors(status)
            label = tk.Label(
                parent,
                text=self._status_label(status),
                bg=bg,
                fg=fg,
                padx=8,
                pady=3,
                relief="solid",
                bd=1,
                font=("Segoe UI", 8, "bold"),
            )
            label.grid(row=0, column=idx, sticky="w", padx=(0, 6))

    def _build_ocr_segments_tab(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(1, weight=1)
        parent.rowconfigure(4, weight=1)
        parent.rowconfigure(6, weight=1)
        parent.columnconfigure(0, weight=1)

        ttk.Label(parent, text="Items OCR estructurados", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self._ocr_items_tree = ttk.Treeview(
            parent,
            columns=("numero", "estado", "clave", "enunciado"),
            show="headings",
            height=5,
        )
        for key, label, width in (
            ("numero", "N", 50),
            ("estado", "Estado", 110),
            ("clave", "Clave", 60),
            ("enunciado", "Enunciado", 360),
        ):
            self._ocr_items_tree.heading(key, text=label)
            self._ocr_items_tree.column(key, width=width)
        self._ocr_items_tree.grid(row=1, column=0, sticky="nsew", pady=(6, 10))
        self._ocr_items_tree.bind("<<TreeviewSelect>>", lambda _e: self._load_selected_ocr_item_into_form())
        self._configure_status_tags(self._ocr_items_tree)
        ttk.Button(parent, text="Usar OCR seleccionado en formulario", command=self._load_selected_ocr_item_into_form).grid(
            row=2,
            column=0,
            sticky="w",
            pady=(0, 10),
        )

        ttk.Label(parent, text="Segmentos graficos internos", style="Section.TLabel").grid(row=3, column=0, sticky="w")
        segment_wrap = ttk.Frame(parent)
        segment_wrap.grid(row=4, column=0, sticky="nsew", pady=(6, 10))
        segment_wrap.rowconfigure(0, weight=1)
        segment_wrap.columnconfigure(0, weight=1)
        self._segments_tree = ttk.Treeview(
            segment_wrap,
            columns=("idx", "bbox", "archivo"),
            show="headings",
            height=5,
        )
        for key, label, width in (
            ("idx", "#", 40),
            ("bbox", "Box px", 160),
            ("archivo", "Archivo", 340),
        ):
            self._segments_tree.heading(key, text=label)
            self._segments_tree.column(key, width=width)
        self._segments_tree.grid(row=0, column=0, sticky="nsew")
        self._segments_tree.bind("<<TreeviewSelect>>", lambda _e: self._remember_selected_segment())
        ttk.Button(segment_wrap, text="Abrir segmento", command=self._open_selected_segment).grid(row=1, column=0, sticky="w", pady=(8, 0))

        ttk.Label(parent, text="OCR fiel", style="Section.TLabel").grid(row=5, column=0, sticky="w")
        self._ocr_text = tk.Text(parent, height=7, wrap="word")
        self._ocr_text.grid(row=6, column=0, sticky="nsew", pady=(6, 0))
        self._ocr_text.configure(state="disabled")

    def _build_review_form(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(2, weight=1)
        parent.columnconfigure(0, weight=1)

        source = ttk.LabelFrame(parent, text="Origen, box y crop", padding=8)
        source.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        source.columnconfigure(1, weight=1)
        source_fields = [
            ("record_id", "Registro"),
            ("page_number", "Pagina"),
            ("bbox_px", "Box px"),
            ("crop_path", "Crop"),
            ("record_status", "Estado"),
        ]
        for idx, (name, label) in enumerate(source_fields):
            var = tk.StringVar(value="")
            self._source_vars[name] = var
            ttk.Label(source, text=label, style="FieldLabel.TLabel").grid(row=idx, column=0, sticky="w", pady=(0, 4))
            ttk.Entry(source, textvariable=var, state="readonly").grid(row=idx, column=1, sticky="ew", padx=(8, 0), pady=(0, 4))

        ttk.Label(parent, textvariable=self._review_status_var, style="Muted.TLabel", wraplength=560).grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(0, 8),
        )

        form = ttk.Frame(parent)
        form.grid(row=2, column=0, sticky="nsew")
        form.columnconfigure(1, weight=1)
        fields = [
            ("numero", "Numero", "entry"),
            ("curso", "Curso", "entry"),
            ("tema", "Tema", "entry"),
            ("respuesta_correcta", "Respuesta", "entry"),
            ("tiene_grafico", "Tiene grafico", "check"),
            ("figure_tag", "Etiqueta grafico", "entry"),
            ("enunciado_latex", "Enunciado", "text"),
            ("A", "Alternativa A", "entry"),
            ("B", "Alternativa B", "entry"),
            ("C", "Alternativa C", "entry"),
            ("D", "Alternativa D", "entry"),
            ("E", "Alternativa E", "entry"),
            ("notas", "Notas", "text"),
        ]
        for row, (name, label, kind) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="nw", pady=(0, 6))
            if kind == "text":
                widget = tk.Text(form, height=4 if name == "enunciado_latex" else 3, wrap="word")
                widget.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=(0, 6))
                self._text_fields[name] = widget
            elif kind == "check":
                var = tk.BooleanVar(value=False)
                widget = ttk.Checkbutton(form, variable=var)
                widget.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=(0, 6))
                self._form_vars[name] = var
            else:
                var = tk.StringVar(value="")
                widget = ttk.Entry(form, textvariable=var)
                widget.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=(0, 6))
                self._form_vars[name] = var
        actions = ttk.Frame(form)
        actions.grid(row=len(fields), column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Checkbutton(actions, text="revision lista", variable=self.review_ready_var).pack(side="left")
        ttk.Button(actions, text="Guardar correccion en staging", command=self._save_review_form, style="Accent.TButton").pack(
            side="left",
            padx=(10, 0),
        )
        ttk.Button(actions, text="Abrir crop", command=self._open_selected_crop).pack(side="left", padx=(8, 0))

    def _build_record_details(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)
        ttk.Label(parent, textvariable=self.detail_var, style="Muted.TLabel", wraplength=520).grid(row=0, column=0, sticky="ew")
        text = tk.Text(parent, height=20, wrap="word")
        text.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=text.yview)
        scrollbar.grid(row=1, column=1, sticky="ns", pady=(8, 0))
        text.configure(yscrollcommand=scrollbar.set, state="disabled")
        self._record_detail_text = text

    def _initialize(self) -> None:
        pdf_path = self.context.resolved_pdf_path()
        if pdf_path.exists():
            try:
                with fitz.open(pdf_path) as document:
                    self.pages_var.set(f"1-{document.page_count}")
            except Exception:
                pass
        models = self.service.models.to_dict()
        self.models_var.set(
            "Modelos default: "
            f"PDF={models.get('pdf_detector')} | OCR={models.get('ocr')} | "
            f"Segmentos={models.get('figure_segmenter')} | Normalizador={models.get('normalizer')}"
        )
        self._refresh_all()

    def _refresh_all(self) -> None:
        summary = self._refresh_summary()
        self._refresh_pages()
        self._refresh_steps()
        self._refresh_records()
        self._sync_workflow_buttons(summary)

    def _refresh_summary(self) -> dict[str, int]:
        try:
            summary = self.service.build_instance_summary()
        except Exception:
            summary = {}
        for name, var in self._summary_vars.items():
            var.set(str(summary.get(name, 0)))
        return {str(key): self._safe_int(value) for key, value in summary.items()}

    def _refresh_pages(self) -> None:
        if self._page_tree is None:
            return
        for item in self._page_tree.get_children():
            self._page_tree.delete(item)
        try:
            rows = self.service.build_page_box_overview()
        except Exception:
            rows = []
        for row in rows:
            record_id = str(row.get("record_id") or "")
            status = StageStatus.normalize(str(row.get("status") or ""))
            self._page_tree.insert(
                "",
                "end",
                iid=record_id,
                values=(
                    row.get("page_number") or "",
                    self._status_label(status),
                    row.get("boxes_total") or 0,
                    "si" if row.get("reviewed") else "no",
                    row.get("layout_mode") or "",
                    row.get("detector_source") or "",
                ),
                tags=(status,),
            )

    def _refresh_steps(self) -> None:
        for item in self.steps_tree.get_children():
            self.steps_tree.delete(item)
        for row in self.service.build_stage_overview():
            name = str(row.get("stage") or "")
            status = StageStatus.normalize(str(row.get("status") or ""))
            self.steps_tree.insert(
                "",
                "end",
                iid=name,
                values=(name, self._status_label(status), row.get("detail") or ""),
                tags=(status,),
            )

    def _refresh_records(self) -> None:
        for item in self.records_tree.get_children():
            self.records_tree.delete(item)
        for row in self.service.build_record_stage_rows():
            steps = dict(row.get("steps") or {})
            status = StageStatus.normalize(str(row.get("status") or ""))
            self.records_tree.insert(
                "",
                "end",
                iid=str(row.get("record_id") or ""),
                values=(
                    self._status_label(status),
                    row.get("page_number") or "",
                    self._status_label(steps.get(PipelineStep.CROPS)),
                    f"{self._status_label(steps.get(PipelineStep.OCR))} ({row.get('ocr_items') or 0})",
                    f"{self._status_label(steps.get(PipelineStep.SEGMENTATION))} ({row.get('segments_total') or 0})",
                    self._status_label(steps.get(PipelineStep.NORMALIZATION)),
                    self._status_label(steps.get(PipelineStep.REVIEW)),
                    str(row.get("errors_total") or 0),
                    row.get("normalized_number") or "",
                    row.get("crop_name") or "",
                ),
                tags=(status,),
            )

    def _sync_workflow_buttons(self, summary: dict[str, int]) -> None:
        if self._busy:
            return
        pages_total = self._safe_int(summary.get("pages_total"))
        boxes_total = self._safe_int(summary.get("boxes_total"))
        records_total = self._safe_int(summary.get("records_total"))
        ocr_done = self._safe_int(summary.get("ocr_done"))
        pdf_exists = self.context.resolved_pdf_path().exists()
        desired = {
            "detect": True,
            "box_reviewer": pdf_exists or pages_total > 0 or boxes_total > 0,
            "materialize": boxes_total > 0,
            "ocr": records_total > 0,
            "normalize": records_total > 0 and ocr_done > 0,
        }
        for key, enabled in desired.items():
            button = self._workflow_buttons.get(key)
            if button is not None:
                button.configure(state="normal" if enabled else "disabled")

    def _safe_int(self, value: object, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default

    def _run_in_thread(self, label: str, action, done_message: str) -> None:
        self.status_var.set(label)
        self._set_busy(True)

        def worker() -> None:
            try:
                result = action()
            except Exception as exc:
                self.after(0, lambda exc=exc: self._show_error(exc))
                return
            self.after(0, lambda result=result: self._done(done_message, result))

        threading.Thread(target=worker, daemon=True).start()

    def _set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        state = "disabled" if busy else "normal"
        for button in self._action_buttons:
            try:
                button.configure(state=state)
            except Exception:
                pass
        try:
            if busy:
                self.progress.start(12)
            else:
                self.progress.stop()
        except Exception:
            pass
        if not busy:
            try:
                self._sync_workflow_buttons(self.service.build_instance_summary())
            except Exception:
                pass

    def _show_error(self, exc: Exception) -> None:
        self._set_busy(False)
        self.status_var.set("Error en la fabrica PDF.")
        messagebox.showerror("Fabrica PDF", str(exc))
        self._refresh_all()

    def _done(self, message: str, _result=None) -> None:
        self._set_busy(False)
        self.status_var.set(message)
        self._refresh_all()

    def _detect_pages(self) -> None:
        pdf_path = self.context.resolved_pdf_path()
        if not pdf_path.exists():
            messagebox.showwarning("Fabrica PDF", f"No se encontro el PDF:\n{pdf_path}")
            return
        try:
            pages = self.service.resolve_page_selection(self.pages_var.get())
        except Exception as exc:
            messagebox.showerror("Fabrica PDF", str(exc))
            return
        self._run_in_thread(
            f"Detectando boxes en {len(pages)} pagina(s)...",
            lambda: self.service.detect_pdf_pages(pages, dpi=int(self.dpi_var.get() or 300)),
            "Boxes detectados. Revisa/corrige antes de crear staging.",
        )

    def _materialize_staging(self) -> None:
        self._run_in_thread(
            "Creando crops y registros staging...",
            lambda: self.service.materialize_crops_to_staging(),
            "Staging creado desde boxes/crops.",
        )

    def _run_ocr_segments(self) -> None:
        self._run_in_thread(
            "Ejecutando OCR y segmentacion de graficos...",
            lambda: self.service.run_ocr_and_segmentation(
                provider=self.provider_var.get(),
                curso=self.curso_var.get(),
                tema=self.tema_var.get(),
            ),
            "OCR + segmentacion guardados en staging.",
        )

    def _normalize_existing(self) -> None:
        self._run_in_thread(
            "Normalizando resultados existentes...",
            lambda: self.service.normalize_existing_ocr(),
            "Normalizacion lista para revision en formulario.",
        )

    def _open_pdf_box_reviewer(self) -> None:
        try:
            from modulos.modulo13_laboratorio_pdf_segmentacion.gui_laboratorio_pdf import PdfSegmentationLabWindow

            PdfSegmentationLabWindow(
                self,
                initial_instance_name=self.context.instance_name,
                initial_pdf_path=str(self.context.resolved_pdf_path()),
                initial_title=f"Revisar boxes - {self.context.book_code} / {self.context.instance_type}",
                linked_session_path=str(self.context.resolved_session_path() or ""),
                book_code=self.context.book_code,
                instance_type=self.context.instance_type,
                project_name=self.context.project_name,
            )
        except Exception as exc:
            messagebox.showerror("Fabrica PDF", f"No se pudo abrir el revisor de boxes.\n{exc}")

    def _load_selected_record(self) -> None:
        selected = self.records_tree.selection()
        if not selected:
            return
        record_id = str(selected[0])
        record = self.service.staging.get_record(record_id)
        if record is None:
            return
        self._selected_record_id = record_id
        self._load_record_source_summary(record)
        normalized = dict(record.normalized or {})
        alternatives = dict(normalized.get("alternativas") or {})
        for name, var in self._form_vars.items():
            if name == "tiene_grafico":
                var.set(bool(normalized.get(name)))
            elif name in alternatives:
                var.set(str(alternatives.get(name) or ""))
            else:
                var.set(str(normalized.get(name) or ""))
        review = dict(record.review or {})
        is_ready = record.status == StageStatus.READY or review.get("review_status") == StageStatus.READY
        self.review_ready_var.set(is_ready)
        review_status = StageStatus.normalize(str(review.get("review_status") or record.status or ""))
        training_total = len(record.training_examples or [])
        self._review_status_var.set(
            f"Revision: {self._status_label(review_status)} | "
            f"correcciones guardadas como entrenamiento: {training_total}"
        )
        for name, text in self._text_fields.items():
            text.delete("1.0", "end")
            if name == "notas":
                text.insert("1.0", str(review.get("notes") or ""))
            else:
                text.insert("1.0", str(normalized.get(name) or ""))
        self._render_ocr_segment_review(record)
        self._render_record_detail(record)

    def _load_record_source_summary(self, record: StagingProblemRecord) -> None:
        source = dict(record.source or {})
        values = {
            "record_id": record.record_id,
            "page_number": source.get("page_number") or "",
            "bbox_px": source.get("bbox_px") or "",
            "crop_path": record.crop_path,
            "record_status": self._status_label(record.status),
        }
        for name, var in self._source_vars.items():
            var.set(str(values.get(name) or ""))

    def _render_ocr_segment_review(self, record: StagingProblemRecord) -> None:
        if self._ocr_items_tree is not None:
            for item in self._ocr_items_tree.get_children():
                self._ocr_items_tree.delete(item)
            self._ocr_item_payloads.clear()
            for idx, item in enumerate(self._extract_structured_items(record), start=1):
                payload = dict(item.get("item") or item)
                options = dict(payload.get("options") or {})
                option_count = sum(1 for label in ("A", "B", "C", "D", "E") if str(options.get(label, "") or "").strip())
                status = StageStatus.normalize(str(item.get("status") or payload.get("status") or StageStatus.PENDING))
                iid = f"ocr_{idx}"
                self._ocr_item_payloads[iid] = payload
                self._ocr_items_tree.insert(
                    "",
                    "end",
                    iid=iid,
                    values=(
                        payload.get("n") or "",
                        self._status_label(status),
                        payload.get("answer_key") or "",
                        f"{str(payload.get('statement') or '')[:180]} | alternativas={option_count}",
                    ),
                    tags=(status,),
                )
        if self._segments_tree is not None:
            for item in self._segments_tree.get_children():
                self._segments_tree.delete(item)
            self._selected_segment_path = ""
            segments = []
            if isinstance(record.figure_segmentation, dict):
                segments = list(record.figure_segmentation.get("segments") or [])
            for idx, segment in enumerate(segments, start=1):
                if not isinstance(segment, dict):
                    continue
                path = str(segment.get("image_path") or "")
                iid = f"segment_{idx}"
                self._segments_tree.insert(
                    "",
                    "end",
                    iid=iid,
                    values=(
                        segment.get("idx") or idx,
                        segment.get("bbox_px") or "",
                        Path(path).name if path else "",
                    ),
                )
                self._segments_tree.set(iid, "archivo", path)
        self._set_ocr_text(str(record.raw_ocr or "").strip() or "(sin OCR fiel para este crop)")

    def _load_selected_ocr_item_into_form(self) -> None:
        if self._ocr_items_tree is None:
            return
        selected = self._ocr_items_tree.selection()
        if not selected:
            return
        payload = self._ocr_item_payloads.get(str(selected[0]))
        if not payload:
            return
        mapping = {
            "numero": payload.get("n"),
            "curso": payload.get("curso"),
            "tema": payload.get("tema"),
            "respuesta_correcta": payload.get("answer_key"),
            "tiene_grafico": bool(payload.get("has_figure")),
            "figure_tag": payload.get("figure_tag"),
        }
        for name, value in mapping.items():
            var = self._form_vars.get(name)
            if var is not None:
                var.set(value if isinstance(value, bool) else str(value or ""))
        statement = str(payload.get("statement") or "")
        if statement and "enunciado_latex" in self._text_fields:
            text = self._text_fields["enunciado_latex"]
            text.delete("1.0", "end")
            text.insert("1.0", statement)
        options = dict(payload.get("options") or {})
        for label in ("A", "B", "C", "D", "E"):
            if label in self._form_vars:
                self._form_vars[label].set(str(options.get(label) or ""))
        self.status_var.set("OCR seleccionado cargado en el formulario de revision.")

    def _extract_structured_items(self, record: StagingProblemRecord) -> list[dict[str, Any]]:
        if not isinstance(record.structured_ocr, dict):
            return []
        items = record.structured_ocr.get("items") or []
        return [dict(item) for item in items if isinstance(item, dict)]

    def _set_ocr_text(self, value: str) -> None:
        if self._ocr_text is None:
            return
        self._ocr_text.configure(state="normal")
        self._ocr_text.delete("1.0", "end")
        self._ocr_text.insert("1.0", value)
        self._ocr_text.configure(state="disabled")

    def _remember_selected_segment(self) -> None:
        if self._segments_tree is None:
            return
        selected = self._segments_tree.selection()
        if not selected:
            self._selected_segment_path = ""
            return
        values = self._segments_tree.item(selected[0], "values")
        self._selected_segment_path = str(values[2] or "") if len(values) >= 3 else ""

    def _render_record_detail(self, record: StagingProblemRecord) -> None:
        source = dict(record.source or {})
        segmentation = dict(record.figure_segmentation or {})
        normalized = dict(record.normalized or {})
        crop_path = Path(record.crop_path)
        bbox = source.get("bbox_px") or []
        segments = list(segmentation.get("segments") or [])
        structured_items = []
        if isinstance(record.structured_ocr, dict):
            structured_items = list(record.structured_ocr.get("items") or [])
        self.detail_var.set(f"{record.record_id} | {record.status} | pagina {source.get('page_number') or '-'}")
        lines = [
            "Origen",
            f"  Libro: {source.get('book_code') or self.context.book_code}",
            f"  Instancia: {source.get('instance_type') or self.context.instance_type}",
            f"  Pagina: {source.get('page_number') or '-'}",
            f"  Box px: {bbox if bbox else '-'}",
            f"  Crop: {crop_path}",
            f"  Crop existe: {'si' if crop_path.exists() else 'no'}",
            "",
            "OCR y segmentacion",
            f"  OCR: {'si' if record.raw_ocr or record.structured_ocr else 'no'}",
            f"  Items estructurados: {len(structured_items)}",
            f"  Segmentos graficos: {segmentation.get('segments_total') or len(segments)}",
            f"  Tiene grafico: {'si' if normalized.get('tiene_grafico') else 'no'}",
            "",
            "Revision",
            f"  Estado revision: {dict(record.review or {}).get('review_status') or '-'}",
            f"  Numero: {normalized.get('numero') or '-'}",
            f"  Respuesta: {normalized.get('respuesta_correcta') or '-'}",
        ]
        if record.steps:
            lines.extend(["", "Estados por etapa"])
            for step in PipelineStep.ORDER:
                payload = dict(record.steps.get(step) or {})
                if not payload:
                    continue
                lines.append(f"  {step}: {payload.get('status') or '-'} | {payload.get('detail') or '-'}")
        if record.artifacts:
            lines.extend(["", "Artefactos staging"])
            for key in ("raw_ocr", "structured_ocr", "normalized", "figure_segmentation"):
                value = str(record.artifacts.get(key) or "").strip()
                if value:
                    lines.append(f"  {key}: {value}")
        if record.errors:
            lines.extend(["", "Errores", *[f"  - {item}" for item in record.errors]])
        if segments:
            lines.extend(["", "Segmentos detectados"])
            for seg in segments[:8]:
                if isinstance(seg, dict):
                    lines.append(f"  #{seg.get('idx')}: {seg.get('bbox_px')} | {seg.get('image_path')}")
            if len(segments) > 8:
                lines.append(f"  ... {len(segments) - 8} segmento(s) mas")
        raw_preview = str(record.raw_ocr or "").strip()
        if raw_preview:
            lines.extend(["", "OCR crudo (vista previa)", raw_preview[:4000]])
        self._set_record_detail_text("\n".join(lines))

    def _set_record_detail_text(self, value: str) -> None:
        if self._record_detail_text is None:
            return
        self._record_detail_text.configure(state="normal")
        self._record_detail_text.delete("1.0", "end")
        self._record_detail_text.insert("1.0", value)
        self._record_detail_text.configure(state="disabled")

    def _collect_form_normalized(self, record: StagingProblemRecord) -> tuple[dict, str]:
        base = dict(record.normalized or {})
        alternatives = {
            label: str(self._form_vars[label].get() or "").strip()
            for label in ("A", "B", "C", "D", "E")
            if label in self._form_vars
        }
        base.update(
            {
                "numero": str(self._form_vars["numero"].get() or "").strip(),
                "curso": str(self._form_vars["curso"].get() or "").strip(),
                "tema": str(self._form_vars["tema"].get() or "").strip(),
                "respuesta_correcta": str(self._form_vars["respuesta_correcta"].get() or "").strip(),
                "tiene_grafico": bool(self._form_vars["tiene_grafico"].get()),
                "figure_tag": str(self._form_vars["figure_tag"].get() or "").strip(),
                "enunciado_latex": self._text_fields["enunciado_latex"].get("1.0", "end").strip(),
                "alternativas": alternatives,
            }
        )
        notes = self._text_fields["notas"].get("1.0", "end").strip()
        return base, notes

    def _save_review_form(self) -> None:
        if not self._selected_record_id:
            messagebox.showinfo("Fabrica PDF", "Selecciona un problema de staging.")
            return
        record = self.service.staging.get_record(self._selected_record_id)
        if record is None:
            messagebox.showwarning("Fabrica PDF", "El registro seleccionado ya no existe.")
            return
        normalized, notes = self._collect_form_normalized(record)
        mark_ready = bool(self.review_ready_var.get())
        self.service.staging.update_review(self._selected_record_id, normalized, notes, mark_ready=mark_ready)
        self.status_var.set("Correccion guardada en staging." if not mark_ready else "Correccion guardada y marcada lista.")
        self._refresh_all()

    def _open_selected_crop(self) -> None:
        if not self._selected_record_id:
            return
        record = self.service.staging.get_record(self._selected_record_id)
        if record is None:
            return
        path = Path(record.crop_path)
        if path.exists():
            os.startfile(str(path))

    def _open_selected_segment(self) -> None:
        path = Path(str(self._selected_segment_path or "").strip())
        if path.exists():
            os.startfile(str(path))
        elif str(self._selected_segment_path or "").strip():
            messagebox.showwarning("Fabrica PDF", f"No se encontro el segmento:\n{self._selected_segment_path}")

    def _open_selected_page_image(self) -> None:
        if self._page_tree is None:
            return
        selected = self._page_tree.selection()
        if not selected:
            messagebox.showinfo("Fabrica PDF", "Selecciona una pagina en el tablero.")
            return
        record_id = str(selected[0])
        target = ""
        for row in self.service.build_page_box_overview():
            if str(row.get("record_id") or "") == record_id:
                target = str(row.get("image_path") or "")
                break
        path = Path(target)
        if path.exists():
            os.startfile(str(path))
        else:
            messagebox.showwarning("Fabrica PDF", f"No se encontro la pagina renderizada:\n{target}")

    def _open_staging_folder(self) -> None:
        self.service.staging.root.mkdir(parents=True, exist_ok=True)
        os.startfile(str(self.service.staging.root))
