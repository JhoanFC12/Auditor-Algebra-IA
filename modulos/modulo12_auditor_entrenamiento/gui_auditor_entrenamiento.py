from __future__ import annotations

from io import BytesIO
from pathlib import Path
import os
import re
import shutil
import struct
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from utils.styles import apply_openai_theme
from utils.preview_window import PreviewWindow

from .controlador_auditor_entrenamiento import (
    OcrGoldenRecord,
    OcrNormalizationGoldenRecord,
    ProblemCropTrainingRecord,
    SegmentGoldenRecord,
    SessionTrainingAudit,
    TrainingAuditController,
)

try:
    from PIL import Image, ImageDraw, ImageTk
except Exception:  # pragma: no cover - Pillow puede no estar disponible en instalaciones minimas.
    Image = None
    ImageDraw = None
    ImageTk = None

try:
    import win32clipboard
    import win32con
except Exception:  # pragma: no cover - PyWin32 es opcional fuera de Windows.
    win32clipboard = None
    win32con = None


class TrainingAuditWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Modulo 12 - Auditor de entrenamiento IA")
        self.geometry("1280x760")
        self.minsize(1060, 640)

        self.controller = TrainingAuditController()
        self.palette = apply_openai_theme(self)
        self.root_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Elige una carpeta con sesiones del Transcriptor IA.")
        self.summary_var = tk.StringVar(value="")
        self.golden_status_var = tk.StringVar(value="Crea o carga una golden base para ver todos los segmentos.")
        self.golden_summary_var = tk.StringVar(value="")
        self.golden_dir_var = tk.StringVar(value="")
        self.ocr_status_var = tk.StringVar(value="Crea o carga una golden base OCR para revisar transcripciones fieles.")
        self.ocr_summary_var = tk.StringVar(value="")
        self.ocr_dir_var = tk.StringVar(value=str(self.controller.DEFAULT_OCR_GOLDEN_DIR))
        self.ocr_field_var = tk.StringVar(value="Geometria")
        self.norm_dir_var = tk.StringVar(value=str(self.controller.DEFAULT_OCR_NORMALIZATION_GOLDEN_DIR))
        self.norm_status_var = tk.StringVar(value="Crea la Golden de normalizacion desde la Golden OCR revisada.")
        self.norm_summary_var = tk.StringVar(value="")
        self.ocr_model_var = tk.StringVar(value=self._default_ocr_scan_model())
        self.problem_crops_dir_var = tk.StringVar(value=str(self.controller.DEFAULT_PROBLEM_CROPS_LIVE_DIR))
        self.problem_crops_status_var = tk.StringVar(value="Carga los problemas completos recortados desde PDF.")
        self.problem_crops_filter_var = tk.StringVar(value="Todos")
        self._audits: list[SessionTrainingAudit] = []
        self._golden_records: list[SegmentGoldenRecord] = []
        self._ocr_records: list[OcrGoldenRecord] = []
        self._norm_records: list[OcrNormalizationGoldenRecord] = []
        self._problem_crop_records: list[ProblemCropTrainingRecord] = []
        self._scan_running = False
        self._golden_running = False
        self._ocr_running = False
        self._preview_photo = None
        self._crop_photo = None
        self._ocr_photo = None
        self._problem_crop_photo = None
        self._segment_editor_window = None
        self._segment_editor_photo = None
        self._segment_editor_pending: dict[str, list[tuple[int, int, int, int]]] = {}
        self._ocr_model_text_pending: dict[str, str] = {}
        self._ocr_latex_preview = PreviewWindow(title="Comparador OCR LaTeX", width=720, height=860)
        self._ocr_preview_after: str | None = None
        self._ocr_preview_poll_after: str | None = None
        self._ocr_preview_block_mode = False
        self._ocr_preview_record_ids: list[str] = []

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._close_window)
        self.after(350, self._autoload_latest_golden_base)
        self.after(450, self._load_ocr_golden)
        self.after(550, self._load_normalization_golden_if_exists)
        self.after(650, self._load_problem_crops_live)

    def _default_ocr_scan_model(self) -> str:
        self._reload_trained_ocr_env()
        provider = (os.getenv("SCAN_PROVIDER", "") or "hf").strip().lower()
        if provider in {"hf", "huggingface", "hfh", "hf_api"}:
            return os.getenv("HF_MODEL", "").strip() or self.controller.TRAINED_OCR_VISION_MODEL
        return os.getenv("OCR_FORMAT_MODEL", "").strip() or self.controller.DEFAULT_OPENAI_FORMAT_MODEL

    @staticmethod
    def _reload_trained_ocr_env() -> None:
        repo_root = Path(__file__).resolve().parents[2]
        allowed = {
            "SCAN_PROVIDER",
            "HF_MODEL",
            "HF_TRAINED_OCR_BASE_URL",
            "HF_TRAINED_OCR_ENDPOINT_NAME",
            "HF_TRAINED_OCR_MAX_TOKENS",
        }
        for env_path in (repo_root / ".env", repo_root / ".env.local"):
            if not env_path.exists():
                continue
            for line in env_path.read_text(encoding="utf-8-sig").splitlines():
                raw = line.strip()
                if not raw or raw.startswith("#") or "=" not in raw:
                    continue
                key, value = raw.split("=", 1)
                key = key.strip()
                if key in allowed:
                    os.environ[key] = value.strip().strip('"').strip("'")

    def _build_ui(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x", padx=18, pady=(16, 4))
        ttk.Label(header, text="Modulo 12 - Auditor de entrenamiento IA", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Revisa y corrige bases Golden para entrenar segmentacion y OCR.",
            style="SubHeader.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=18, pady=(10, 18))
        problem_crops_tab = ttk.Frame(notebook)
        golden_tab = ttk.Frame(notebook)
        ocr_tab = ttk.Frame(notebook)
        norm_tab = ttk.Frame(notebook)
        notebook.add(problem_crops_tab, text="Problemas PDF")
        notebook.add(ocr_tab, text="Golden OCR")
        notebook.add(norm_tab, text="Golden normalizacion")
        notebook.add(golden_tab, text="Golden segmentos")

        self._build_problem_crops_tab(problem_crops_tab)
        self._build_golden_tab(golden_tab)
        self._build_ocr_tab(ocr_tab)
        self._build_normalization_tab(norm_tab)

    def _build_problem_crops_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=2)
        parent.columnconfigure(1, weight=3)
        parent.rowconfigure(1, weight=1)
        controls = ttk.Frame(parent, style="Card.TFrame", padding=12)
        controls.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="Bandeja de problemas PDF", style="FieldLabel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.problem_crops_dir_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(controls, text="Actualizar", command=self._load_problem_crops_live, style="Accent.TButton").grid(row=0, column=2, padx=(0, 8))
        ttk.Combobox(
            controls,
            textvariable=self.problem_crops_filter_var,
            state="readonly",
            width=22,
            values=("Todos", "Pendientes OCR", "OCR corregido", "Pendientes grafico", "Con grafico", "Sin grafico"),
        ).grid(row=0, column=3)
        self.problem_crops_filter_var.trace_add("write", lambda *_args: self._populate_problem_crops_table())
        ttk.Label(controls, textvariable=self.problem_crops_status_var, style="Muted.TLabel").grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))

        table = ttk.Frame(parent, style="Card.TFrame")
        table.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        columns = ("page", "crop", "layout", "ocr", "figure")
        self.problem_crops_tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse")
        for key, label, width in (
            ("page", "Pag.", 48), ("crop", "Recorte", 220), ("layout", "Columnas", 85),
            ("ocr", "OCR", 95), ("figure", "Grafico", 145),
        ):
            self.problem_crops_tree.heading(key, text=label)
            self.problem_crops_tree.column(key, width=width, stretch=key == "crop")
        self.problem_crops_tree.grid(row=0, column=0, sticky="nsew")
        self.problem_crops_tree.bind("<<TreeviewSelect>>", lambda _event: self._show_selected_problem_crop())
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.problem_crops_tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.problem_crops_tree.configure(yscrollcommand=scroll.set)

        editor = ttk.Frame(parent, style="Card.TFrame", padding=12)
        editor.grid(row=1, column=1, sticky="nsew")
        editor.columnconfigure(0, weight=1)
        editor.rowconfigure(1, weight=2)
        editor.rowconfigure(4, weight=1)
        ttk.Label(editor, text="Problema completo extraido del PDF", style="LabelTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.problem_crop_preview = ttk.Label(editor, anchor="center")
        self.problem_crop_preview.grid(row=1, column=0, sticky="nsew", pady=(5, 8))
        self.problem_crop_meta_var = tk.StringVar(value="")
        ttk.Label(editor, textvariable=self.problem_crop_meta_var, style="Muted.TLabel", wraplength=680).grid(row=2, column=0, sticky="w")
        ttk.Label(editor, text="OCR corregido", style="LabelTitle.TLabel").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.problem_crop_corrected_text = tk.Text(editor, wrap="word", height=8, bg="#ffffff", fg="#0f172a", relief="solid", bd=1)
        self.problem_crop_corrected_text.grid(row=4, column=0, sticky="nsew", pady=(5, 8))
        actions = ttk.Frame(editor, style="Card.TFrame")
        actions.grid(row=5, column=0, sticky="ew")
        ttk.Button(actions, text="Guardar OCR", command=self._save_selected_problem_crop_ocr, style="Accent.TButton").pack(side="left")
        ttk.Button(actions, text="Tiene grafico", command=lambda: self._save_problem_crop_figure_state("con_grafico"), style="Ghost.TButton").pack(side="left", padx=6)
        ttk.Button(actions, text="Sin grafico", command=lambda: self._save_problem_crop_figure_state("sin_grafico"), style="Ghost.TButton").pack(side="left")
        ttk.Button(actions, text="Detectar grafico", command=self._detect_selected_problem_crop_figures, style="Ghost.TButton").pack(side="left", padx=6)
        ttk.Button(actions, text="Abrir imagen", command=self._open_selected_problem_crop_image, style="Ghost.TButton").pack(side="left", padx=6)

    def _load_problem_crops_live(self) -> None:
        try:
            self._problem_crop_records = self.controller.load_problem_crops_live(Path(self.problem_crops_dir_var.get()))
            self._populate_problem_crops_table()
            pending_ocr = sum(record.ocr_status == "pending_ocr" for record in self._problem_crop_records)
            pending_figures = sum(record.figure_segmentation_status == "pending_figure_segmentation" for record in self._problem_crop_records)
            self.problem_crops_status_var.set(
                f"Problemas: {len(self._problem_crop_records)} | OCR pendientes: {pending_ocr} | Graficos pendientes: {pending_figures}"
            )
        except Exception as exc:
            messagebox.showerror("Problemas recortados", f"No se pudo cargar la bandeja.\n{exc}")

    def _filtered_problem_crop_records(self) -> list[ProblemCropTrainingRecord]:
        mode = self.problem_crops_filter_var.get()
        if mode == "Pendientes OCR":
            return [record for record in self._problem_crop_records if record.ocr_status == "pending_ocr"]
        if mode == "OCR corregido":
            return [record for record in self._problem_crop_records if record.ocr_status == "ocr_corregido"]
        if mode == "Pendientes grafico":
            return [record for record in self._problem_crop_records if record.figure_segmentation_status == "pending_figure_segmentation"]
        if mode == "Con grafico":
            return [record for record in self._problem_crop_records if record.figure_segmentation_status == "con_grafico"]
        if mode == "Sin grafico":
            return [record for record in self._problem_crop_records if record.figure_segmentation_status == "sin_grafico"]
        return list(self._problem_crop_records)

    def _populate_problem_crops_table(self) -> None:
        if not hasattr(self, "problem_crops_tree"):
            return
        self.problem_crops_tree.delete(*self.problem_crops_tree.get_children())
        for record in self._filtered_problem_crop_records():
            self.problem_crops_tree.insert(
                "",
                "end",
                iid=record.crop_id,
                values=(
                    record.source_page_number,
                    record.crop_id,
                    record.layout_mode,
                    record.ocr_status,
                    record.figure_segmentation_status,
                ),
            )

    def _selected_problem_crop(self) -> ProblemCropTrainingRecord | None:
        if not hasattr(self, "problem_crops_tree"):
            return None
        selected = self.problem_crops_tree.selection()
        if not selected:
            return None
        crop_id = selected[0]
        return next((record for record in self._problem_crop_records if record.crop_id == crop_id), None)

    def _show_selected_problem_crop(self) -> None:
        record = self._selected_problem_crop()
        if record is None:
            return
        self.problem_crop_corrected_text.delete("1.0", "end")
        self.problem_crop_corrected_text.insert("1.0", record.corrected_text or record.ocr_text)
        self.problem_crop_meta_var.set(
            f"PDF: {Path(record.source_pdf_path).name} | pagina: {record.source_page_number} | "
            f"box: {record.bbox_px} | layout: {record.layout_mode}"
        )
        if Image is None or ImageTk is None or not Path(record.image_path).exists():
            self.problem_crop_preview.configure(text="Imagen no disponible", image="")
            return
        with Image.open(record.image_path) as source:
            preview = source.convert("RGB")
        preview.thumbnail((720, 330))
        self._problem_crop_photo = ImageTk.PhotoImage(preview)
        self.problem_crop_preview.configure(image=self._problem_crop_photo, text="")

    def _save_selected_problem_crop_ocr(self) -> None:
        record = self._selected_problem_crop()
        if record is None:
            return
        corrected = self.problem_crop_corrected_text.get("1.0", "end-1c").strip()
        updated = self.controller.save_problem_crop_review(
            record,
            corrected_text=corrected,
            ocr_status="ocr_corregido" if corrected else "pending_ocr",
            root=Path(self.problem_crops_dir_var.get()),
        )
        self._replace_problem_crop_record(updated)
        try:
            _, added = self.controller.import_problem_crops_into_ocr_golden(
                crops_root=Path(self.problem_crops_dir_var.get()),
                out_dir=Path(self.ocr_dir_var.get()),
            )
            self._load_ocr_golden()
            self.problem_crops_status_var.set(
                f"OCR guardado y sincronizado con Golden OCR. Nuevos importados: {added}."
            )
        except Exception as exc:
            self.problem_crops_status_var.set(f"OCR guardado, pero no se pudo sincronizar Golden OCR: {exc}")

    def _save_problem_crop_figure_state(self, state: str) -> None:
        record = self._selected_problem_crop()
        if record is None:
            return
        updated = self.controller.save_problem_crop_review(
            record,
            figure_segmentation_status=state,
            root=Path(self.problem_crops_dir_var.get()),
        )
        self._replace_problem_crop_record(updated)
        try:
            self.controller.import_problem_crops_into_segment_golden(crops_root=Path(self.problem_crops_dir_var.get()))
        except Exception as exc:
            self.problem_crops_status_var.set(f"Estado guardado, pero no se pudo sincronizar Golden Segmentos: {exc}")

    def _replace_problem_crop_record(self, updated: ProblemCropTrainingRecord) -> None:
        self._problem_crop_records = [
            updated if record.crop_id == updated.crop_id else record for record in self._problem_crop_records
        ]
        self._populate_problem_crops_table()
        if self.problem_crops_tree.exists(updated.crop_id):
            self.problem_crops_tree.selection_set(updated.crop_id)
            self.problem_crops_tree.see(updated.crop_id)
        self._show_selected_problem_crop()

    def _open_selected_problem_crop_image(self) -> None:
        record = self._selected_problem_crop()
        if record is not None and Path(record.image_path).exists():
            os.startfile(record.image_path)

    def _detect_selected_problem_crop_figures(self) -> None:
        record = self._selected_problem_crop()
        if record is None:
            return
        image_path = Path(record.image_path)
        if not image_path.exists():
            messagebox.showerror("Graficos internos", f"No existe la imagen:\n{image_path}")
            return
        try:
            from modulos.modulo0_transcriptor.segmentador_v2 import SegmentadorProblemasV2

            out_root = self.controller.DEFAULT_GOLDEN_ROOT / "problem_crop_figure_segments"
            segments = SegmentadorProblemasV2(out_root).segmentar(image_path)
            boxes = [list(segment.bbox) for segment in segments]
            state = "con_grafico" if boxes else "sin_grafico"
            updated = self.controller.save_problem_crop_review(
                record,
                figure_segmentation_status=state,
                figure_boxes_px=boxes,
                root=Path(self.problem_crops_dir_var.get()),
            )
            self._replace_problem_crop_record(updated)
            self.problem_crops_status_var.set(
                f"{record.crop_id}: se detectaron {len(boxes)} grafico(s) interno(s)."
            )
        except Exception as exc:
            messagebox.showerror("Graficos internos", f"No se pudo ejecutar el segmentador.\n{exc}")

    def _build_normalization_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=2)
        parent.columnconfigure(1, weight=3)
        parent.rowconfigure(1, weight=1)
        controls = ttk.Frame(parent, style="Card.TFrame", padding=12)
        controls.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="Golden normalizacion", style="FieldLabel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.norm_dir_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(controls, text="Crear desde Golden OCR", command=self._build_normalization_golden, style="Accent.TButton").grid(row=0, column=2, padx=(0, 8))
        ttk.Button(controls, text="Cargar", command=self._load_normalization_golden, style="Ghost.TButton").grid(row=0, column=3, padx=(0, 8))
        ttk.Button(controls, text="Preparar dataset", command=self._prepare_normalization_dataset, style="Ghost.TButton").grid(row=0, column=4)
        ttk.Label(controls, textvariable=self.norm_status_var, style="Section.TLabel").grid(row=1, column=0, columnspan=5, sticky="w", pady=(10, 0))
        ttk.Label(controls, textvariable=self.norm_summary_var, style="Muted.TLabel").grid(row=2, column=0, columnspan=5, sticky="w", pady=(3, 0))

        table = ttk.Frame(parent, style="Card.TFrame")
        table.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        columns = ("status", "source", "raw", "normalized")
        self.norm_tree = ttk.Treeview(table, columns=columns, show="headings")
        for key, label, width in (("status", "Estado", 90), ("source", "Imagen", 190), ("raw", "Crudo", 65), ("normalized", "Normalizado", 85)):
            self.norm_tree.heading(key, text=label)
            self.norm_tree.column(key, width=width, stretch=key == "source")
        self.norm_tree.grid(row=0, column=0, sticky="nsew")
        self.norm_tree.bind("<<TreeviewSelect>>", lambda _e: self._show_selected_normalization())
        self.norm_tree.tag_configure("confirmed", background="#dcfce7")
        self.norm_tree.tag_configure("excluded", background="#fee2e2")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.norm_tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.norm_tree.configure(yscrollcommand=scroll.set)

        editor = ttk.Frame(parent, style="Card.TFrame", padding=12)
        editor.grid(row=1, column=1, sticky="nsew")
        editor.columnconfigure(0, weight=1)
        editor.rowconfigure(1, weight=1)
        editor.rowconfigure(3, weight=1)
        ttk.Label(editor, text="OCR crudo", style="LabelTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.norm_raw_text = tk.Text(editor, wrap="word", bg="#f8fafc", fg="#0f172a", relief="flat")
        self.norm_raw_text.grid(row=1, column=0, sticky="nsew", pady=(5, 10))
        ttk.Label(editor, text="Texto normalizado para entrenamiento", style="LabelTitle.TLabel").grid(row=2, column=0, sticky="w")
        self.norm_corrected_text = tk.Text(editor, wrap="word", bg="#ffffff", fg="#0f172a", relief="solid", bd=1)
        self.norm_corrected_text.grid(row=3, column=0, sticky="nsew", pady=(5, 10))
        actions = ttk.Frame(editor, style="Card.TFrame")
        actions.grid(row=4, column=0, sticky="ew")
        ttk.Button(actions, text="Confirmar", command=lambda: self._save_normalization_review("confirmed"), style="Accent.TButton").pack(side="left")
        ttk.Button(actions, text="Guardar pendiente", command=lambda: self._save_normalization_review("pending"), style="Ghost.TButton").pack(side="left", padx=8)
        ttk.Button(actions, text="Excluir", command=lambda: self._save_normalization_review("excluded"), style="Ghost.TButton").pack(side="left")

    def _build_normalization_golden(self) -> None:
        try:
            target = self.controller.build_ocr_normalization_golden_base(
                ocr_golden_dir=Path(self.ocr_dir_var.get().strip() or self.controller.DEFAULT_OCR_GOLDEN_DIR),
                out_dir=Path(self.norm_dir_var.get().strip() or self.controller.DEFAULT_OCR_NORMALIZATION_GOLDEN_DIR),
            )
            self.norm_dir_var.set(str(target))
            self._load_normalization_golden()
        except Exception as exc:
            messagebox.showerror("Golden normalizacion", f"No se pudo crear la base.\n{exc}")

    def _load_normalization_golden(self) -> None:
        try:
            self._norm_records = self.controller.load_ocr_normalization_golden_base(Path(self.norm_dir_var.get()))
            self._populate_normalization_table()
        except Exception as exc:
            messagebox.showerror("Golden normalizacion", f"No se pudo cargar la base.\n{exc}")

    def _load_normalization_golden_if_exists(self) -> None:
        if Path(self.norm_dir_var.get()).exists():
            self._load_normalization_golden()

    def _populate_normalization_table(self) -> None:
        self.norm_tree.delete(*self.norm_tree.get_children())
        for idx, record in enumerate(self._norm_records):
            self.norm_tree.insert("", "end", iid=str(idx), values=(record.status, record.source_label, len(record.raw_ocr), len(record.normalized_text)), tags=(record.status,))
        confirmed = sum(record.status == "confirmed" for record in self._norm_records)
        excluded = sum(record.status == "excluded" for record in self._norm_records)
        self.norm_status_var.set(f"Pares de normalizacion: {len(self._norm_records)}")
        self.norm_summary_var.set(f"Confirmados: {confirmed} | Pendientes: {len(self._norm_records) - confirmed - excluded} | Excluidos: {excluded}")
        if self._norm_records:
            self.norm_tree.selection_set("0")
            self._show_selected_normalization()

    def _selected_normalization(self) -> OcrNormalizationGoldenRecord | None:
        selection = self.norm_tree.selection()
        if not selection:
            return None
        index = int(selection[0])
        return self._norm_records[index] if 0 <= index < len(self._norm_records) else None

    def _show_selected_normalization(self) -> None:
        record = self._selected_normalization()
        self.norm_raw_text.delete("1.0", "end")
        self.norm_corrected_text.delete("1.0", "end")
        if record:
            self.norm_raw_text.insert("1.0", record.raw_ocr)
            self.norm_corrected_text.insert("1.0", record.normalized_text)

    def _save_normalization_review(self, status: str) -> None:
        record = self._selected_normalization()
        if not record:
            return
        self.controller.save_ocr_normalization_review(
            record_id=record.record_id,
            normalized_text=self.norm_corrected_text.get("1.0", "end").strip(),
            status=status,
            golden_dir=Path(self.norm_dir_var.get()),
        )
        self._load_normalization_golden()

    def _prepare_normalization_dataset(self) -> None:
        try:
            out_dir = self.controller.prepare_ocr_normalization_dataset(golden_dir=Path(self.norm_dir_var.get()))
            self.norm_status_var.set(f"Dataset preparado: {out_dir}")
        except Exception as exc:
            messagebox.showerror("Golden normalizacion", f"No se pudo preparar el dataset.\n{exc}")

    def _close_window(self) -> None:
        if self._ocr_preview_poll_after:
            try:
                self.after_cancel(self._ocr_preview_poll_after)
            except Exception:
                pass
        try:
            self._ocr_latex_preview.close()
        except Exception:
            pass
        self.destroy()

    def _build_golden_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=3)
        parent.columnconfigure(1, weight=2)
        parent.rowconfigure(1, weight=1)

        controls = ttk.Frame(parent, style="Card.TFrame", padding=12)
        controls.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="Golden base", style="FieldLabel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.golden_dir_var).grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(controls, text="Crear desde raiz", command=self._build_golden_async, style="Accent.TButton").grid(
            row=0, column=2, padx=(0, 8)
        )
        ttk.Button(controls, text="Cargar", command=self._choose_golden_dir, style="Ghost.TButton").grid(
            row=0, column=3, padx=(0, 8)
        )
        ttk.Button(controls, text="Ultima", command=self._load_latest_golden, style="Ghost.TButton").grid(row=0, column=4)
        ttk.Button(
            controls,
            text="Preparar HF",
            command=self._prepare_hf_dataset_async,
            style="Accent.TButton",
        ).grid(row=0, column=5, padx=(8, 0))
        ttk.Button(
            controls,
            text="Incorporar recortes PDF",
            command=self._import_problem_crops_into_segment_golden_async,
            style="Ghost.TButton",
        ).grid(row=0, column=6, padx=(8, 0))
        ttk.Button(
            controls,
            text="Detectar seleccionados",
            command=self._detect_selected_golden_segments_async,
            style="Accent.TButton",
        ).grid(row=0, column=7, padx=(8, 0))
        ttk.Label(controls, textvariable=self.golden_status_var, style="Section.TLabel").grid(
            row=1, column=0, columnspan=8, sticky="w", pady=(10, 0)
        )
        ttk.Label(controls, textvariable=self.golden_summary_var, style="Muted.TLabel").grid(
            row=2, column=0, columnspan=8, sticky="w", pady=(3, 0)
        )

        table_card = ttk.Frame(parent, style="Card.TFrame", padding=0)
        table_card.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        table_card.rowconfigure(0, weight=1)
        table_card.columnconfigure(0, weight=1)

        columns = (
            "status",
            "split",
            "book_code",
            "instance_type",
            "source_stem",
            "segment_idx",
            "item_num",
            "slot",
            "image",
        )
        self.golden_tree = ttk.Treeview(table_card, columns=columns, show="headings", height=18)
        headings = {
            "status": "Estado",
            "split": "Split",
            "book_code": "Libro",
            "instance_type": "Instancia",
            "source_stem": "Fuente",
            "segment_idx": "Seg.",
            "item_num": "Item",
            "slot": "Slot",
            "image": "Imagen",
        }
        widths = {
            "status": 100,
            "split": 58,
            "book_code": 170,
            "instance_type": 190,
            "source_stem": 150,
            "segment_idx": 54,
            "item_num": 54,
            "slot": 60,
            "image": 180,
        }
        for key in columns:
            self.golden_tree.heading(key, text=headings[key])
            self.golden_tree.column(key, width=widths[key], stretch=key in {"book_code", "instance_type", "source_stem", "image"})
        self.golden_tree.grid(row=0, column=0, sticky="nsew")
        self.golden_tree.bind("<<TreeviewSelect>>", lambda _e: self._show_selected_golden_detail())
        self.golden_tree.bind("<Double-1>", lambda _e: self._open_selected_golden_image())

        scroll = ttk.Scrollbar(table_card, orient="vertical", command=self.golden_tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.golden_tree.configure(yscrollcommand=scroll.set)

        detail_card = ttk.Frame(parent, style="Card.TFrame", padding=12)
        detail_card.grid(row=1, column=1, sticky="nsew")
        detail_card.columnconfigure(0, weight=1)
        detail_card.rowconfigure(2, weight=1)
        ttk.Label(detail_card, text="Segmento seleccionado", style="LabelTitle.TLabel").grid(row=0, column=0, sticky="w")

        buttons = ttk.Frame(detail_card, style="Card.TFrame")
        buttons.grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Button(buttons, text="Abrir imagen", command=self._open_selected_golden_image, style="Ghost.TButton").pack(side="left")
        ttk.Button(buttons, text="Editor V2", command=self._open_selected_source_segment_editor, style="Accent.TButton").pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(buttons, text="Abrir fuente", command=self._open_selected_golden_source, style="Ghost.TButton").pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(buttons, text="Abrir carpeta", command=self._open_selected_golden_folder, style="Ghost.TButton").pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(buttons, text="Abrir sesion", command=self._open_selected_golden_session, style="Ghost.TButton").pack(
            side="left", padx=(8, 0)
        )

        preview_wrap = ttk.Frame(detail_card, style="Card.TFrame")
        preview_wrap.grid(row=2, column=0, sticky="nsew", pady=(10, 8))
        preview_wrap.columnconfigure(0, weight=1)
        preview_wrap.columnconfigure(1, weight=1)
        preview_wrap.rowconfigure(1, weight=1)
        ttk.Label(preview_wrap, text="Imagen fuente + box", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(preview_wrap, text="Recorte segmentado", style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.preview_label = ttk.Label(preview_wrap, text="Sin vista previa", anchor="center")
        self.preview_label.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.crop_preview_label = ttk.Label(preview_wrap, text="Sin recorte", anchor="center")
        self.crop_preview_label.grid(row=1, column=1, sticky="nsew", padx=(10, 0), pady=(6, 0))
        self.golden_detail_text = tk.Text(detail_card, wrap="word", height=8, bg="#ffffff", fg="#0f172a", relief="flat")
        self.golden_detail_text.grid(row=3, column=0, sticky="ew")

    def _build_ocr_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=2)
        parent.columnconfigure(1, weight=3)
        parent.rowconfigure(1, weight=1)

        controls = ttk.Frame(parent, style="Card.TFrame", padding=12)
        controls.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="Golden OCR", style="FieldLabel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.ocr_dir_var).grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(controls, text="Crear 200", command=lambda: self._build_ocr_async(200), style="Accent.TButton").grid(
            row=0, column=2, padx=(0, 8)
        )
        ttk.Button(controls, text="Crear 100", command=lambda: self._build_ocr_async(100), style="Ghost.TButton").grid(
            row=0, column=3, padx=(0, 8)
        )
        ttk.Button(controls, text="Cargar", command=self._load_ocr_golden, style="Ghost.TButton").grid(row=0, column=4, padx=(0, 8))
        ttk.Button(controls, text="Copiar OCR fiel", command=self._copy_all_ocr_block, style="Ghost.TButton").grid(row=0, column=5, padx=(0, 8))
        ttk.Button(controls, text="Copiar OCR crudo", command=self._copy_all_raw_model_ocr_block, style="Ghost.TButton").grid(row=0, column=6, padx=(0, 8))
        ttk.Button(controls, text="Pegar bloque corregido", command=self._paste_corrected_ocr_block, style="Accent.TButton").grid(row=0, column=7)
        ttk.Button(
            controls,
            text="Incorporar recortes PDF",
            command=self._import_problem_crops_into_current_ocr_golden,
            style="Ghost.TButton",
        ).grid(row=0, column=8, padx=(8, 0))
        ttk.Label(controls, text="Campo", style="FieldLabel.TLabel").grid(row=3, column=4, sticky="e", padx=(8, 0), pady=(10, 0))
        field_combo = ttk.Combobox(
            controls,
            textvariable=self.ocr_field_var,
            values=("Geometria",),
            state="readonly",
            width=20,
        )
        field_combo.grid(row=3, column=5, sticky="w", padx=(8, 0), pady=(10, 0))
        field_combo.bind("<<ComboboxSelected>>", lambda _e: self._select_ocr_training_field())
        ttk.Button(
            controls,
            text="Crear campo 100",
            command=lambda: self._build_selected_field_ocr_async(100),
            style="Accent.TButton",
        ).grid(row=3, column=6, padx=(8, 0), pady=(10, 0))
        ttk.Button(
            controls,
            text="Cargar campo",
            command=self._load_selected_field_ocr_golden,
            style="Ghost.TButton",
        ).grid(row=3, column=7, padx=(8, 0), pady=(10, 0))
        ttk.Label(controls, text="Modelo", style="FieldLabel.TLabel").grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(controls, textvariable=self.ocr_model_var, width=28).grid(row=3, column=1, sticky="w", padx=(8, 8), pady=(10, 0))
        ttk.Button(
            controls,
            text="Escanear seleccionados",
            command=self._normalize_selected_ocr_queue,
            style="Accent.TButton",
        ).grid(row=3, column=2, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Button(
            controls,
            text="Seleccionar pendientes",
            command=lambda: self._select_ocr_rows_by_status({"pending"}),
            style="Ghost.TButton",
        ).grid(row=4, column=2, sticky="w", pady=(8, 0))
        ttk.Button(
            controls,
            text="Seleccionar errores",
            command=lambda: self._select_ocr_rows_by_status({"error"}),
            style="Ghost.TButton",
        ).grid(row=4, column=3, sticky="w", padx=(8, 0), pady=(8, 0))
        ttk.Button(
            controls,
            text="Apagar endpoint OCR",
            command=self._scale_ocr_endpoint_to_zero_async,
            style="Ghost.TButton",
        ).grid(row=4, column=4, columnspan=2, sticky="w", padx=(8, 0), pady=(8, 0))
        ttk.Button(
            controls,
            text="Clasificar secciones",
            command=self._classify_ocr_sections,
            style="Accent.TButton",
        ).grid(row=4, column=6, columnspan=2, sticky="w", padx=(8, 0), pady=(8, 0))
        ttk.Button(
            controls,
            text="Enlazar sesiones",
            command=self._link_ocr_records_to_sessions,
            style="Ghost.TButton",
        ).grid(row=4, column=8, sticky="w", padx=(8, 0), pady=(8, 0))
        ttk.Label(controls, textvariable=self.ocr_status_var, style="Section.TLabel").grid(
            row=1, column=0, columnspan=8, sticky="w", pady=(10, 0)
        )
        ttk.Label(controls, textvariable=self.ocr_summary_var, style="Muted.TLabel").grid(
            row=2, column=0, columnspan=8, sticky="w", pady=(4, 0)
        )

        table_card = ttk.Frame(parent, style="Card.TFrame", padding=0)
        table_card.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        table_card.rowconfigure(0, weight=1)
        table_card.columnconfigure(0, weight=1)
        columns = ("status", "section", "book", "instance", "source", "chars")
        self.ocr_tree = ttk.Treeview(table_card, columns=columns, show="headings", height=18, selectmode="extended")
        headings = {
            "status": "Estado",
            "section": "Seccion",
            "book": "Libro",
            "instance": "Instancia",
            "source": "Imagen",
            "chars": "Chars",
        }
        widths = {"status": 90, "section": 120, "book": 165, "instance": 165, "source": 180, "chars": 70}
        for key in columns:
            self.ocr_tree.heading(key, text=headings[key])
            self.ocr_tree.column(key, width=widths[key], stretch=key in {"book", "instance", "source"})
        self.ocr_tree.grid(row=0, column=0, sticky="nsew")
        self.ocr_tree.bind("<<TreeviewSelect>>", lambda _e: self._show_selected_ocr_record())
        self.ocr_tree.tag_configure("ocr_pending", background="#ffffff")
        self.ocr_tree.tag_configure("ocr_processing", background="#fef3c7")
        self.ocr_tree.tag_configure("ocr_done", background="#dcfce7")
        self.ocr_tree.tag_configure("ocr_reviewed", background="#dbeafe")
        self.ocr_tree.tag_configure("ocr_error", background="#fee2e2")
        self.ocr_tree.tag_configure("ocr_section_geometria", background="#dcfce7")
        self.ocr_tree.tag_configure("ocr_section_geometria_analitica", background="#ede9fe")
        self.ocr_tree.tag_configure("ocr_section_algebra", background="#e0f2fe")
        self.ocr_tree.tag_configure("ocr_section_aritmetica", background="#fef3c7")
        self.ocr_tree.tag_configure("ocr_section_trigonometria", background="#fce7f3")
        scroll = ttk.Scrollbar(table_card, orient="vertical", command=self.ocr_tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.ocr_tree.configure(yscrollcommand=scroll.set)

        editor = ttk.Frame(parent, style="Card.TFrame", padding=12)
        editor.grid(row=1, column=1, sticky="nsew")
        editor.columnconfigure(0, weight=1)
        editor.columnconfigure(1, weight=1)
        editor.rowconfigure(1, weight=1)
        editor.rowconfigure(3, weight=1)

        ttk.Label(editor, text="Imagen", style="LabelTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(editor, text="Correccion OCR", style="LabelTitle.TLabel").grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.ocr_image_label = ttk.Label(editor, text="Sin imagen", anchor="center")
        self.ocr_image_label.grid(row=1, column=0, sticky="nsew", pady=(8, 8))

        right = ttk.Frame(editor, style="Card.TFrame")
        right.grid(row=1, column=1, rowspan=3, sticky="nsew", padx=(10, 0), pady=(8, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        right.rowconfigure(3, weight=1)
        ttk.Label(right, text="OCR fiel del modelo", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.ocr_raw_text = tk.Text(right, wrap="word", height=9, bg="#f8fafc", fg="#0f172a", relief="flat")
        self.ocr_raw_text.grid(row=1, column=0, sticky="nsew", pady=(4, 8))
        ttk.Label(right, text="Texto corregido para entrenamiento", style="Muted.TLabel").grid(row=2, column=0, sticky="w")
        self.ocr_corrected_text = tk.Text(right, wrap="word", height=9, bg="#ffffff", fg="#0f172a", relief="solid", bd=1)
        self.ocr_corrected_text.grid(row=3, column=0, sticky="nsew", pady=(4, 8))
        buttons = ttk.Frame(right, style="Card.TFrame")
        buttons.grid(row=4, column=0, sticky="ew")
        for col in range(4):
            buttons.columnconfigure(col, weight=1, uniform="ocr_actions")
        ttk.Button(buttons, text="Usar OCR", command=self._ocr_copy_raw_to_corrected, style="Ghost.TButton").grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(
            buttons,
            text="Escanear imagen",
            command=self._normalize_selected_ocr_with_model,
            style="Ghost.TButton",
        ).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(buttons, text="Guardar", command=self._save_selected_ocr_correction, style="Accent.TButton").grid(
            row=0, column=2, sticky="ew", padx=4
        )
        ttk.Button(buttons, text="Abrir imagen", command=self._open_selected_ocr_image, style="Ghost.TButton").grid(
            row=0, column=3, sticky="ew", padx=(4, 0)
        )
        ttk.Button(buttons, text="Ver imagenes", command=self._open_ocr_images_preview, style="Ghost.TButton").grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=(0, 4), pady=(6, 0)
        )
        ttk.Button(buttons, text="Ver/editar bloque LaTeX", command=self._open_ocr_latex_block_preview, style="Accent.TButton").grid(
            row=1, column=2, columnspan=2, sticky="ew", padx=(4, 0), pady=(6, 0)
        )
        ttk.Button(
            buttons,
            text="Marcar revisado",
            command=self._mark_selected_ocr_reviewed,
            style="Accent.TButton",
        ).grid(row=2, column=0, columnspan=2, sticky="ew", padx=(0, 4), pady=(6, 0))
        ttk.Button(
            buttons,
            text="Seleccionar por revisar",
            command=self._select_ocr_rows_needing_review,
            style="Ghost.TButton",
        ).grid(row=2, column=2, columnspan=2, sticky="ew", padx=(4, 0), pady=(6, 0))
        ttk.Button(
            buttons,
            text="Importar bloque ChatGPT",
            command=self._open_chatgpt_block_import_window,
            style="Accent.TButton",
        ).grid(row=3, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        ttk.Button(
            buttons,
            text="Editar Golden Base en bloque",
            command=self._open_ocr_plain_block_editor,
            style="Ghost.TButton",
        ).grid(row=4, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        ttk.Button(
            buttons,
            text="Copiar archivos seleccionados",
            command=self._copy_selected_ocr_images,
            style="Ghost.TButton",
        ).grid(row=5, column=0, columnspan=2, sticky="ew", padx=(0, 4), pady=(6, 0))
        ttk.Button(
            buttons,
            text="Preparar para arrastrar",
            command=self._prepare_selected_ocr_images_for_drag,
            style="Accent.TButton",
        ).grid(row=5, column=2, columnspan=2, sticky="ew", padx=(4, 0), pady=(6, 0))
        self.ocr_corrected_text.bind("<KeyRelease>", lambda _e: self._schedule_ocr_latex_preview())

    def _choose_root(self) -> None:
        initial = self.root_var.get().strip() or "E:\\Banco de Preguntas"
        path = filedialog.askdirectory(title="Elegir raiz de sesiones", initialdir=initial if Path(initial).exists() else None)
        if path:
            self.root_var.set(path)

    def _build_ocr_async(self, limit: int) -> None:
        if self._ocr_running:
            return
        raw_root = self.root_var.get().strip()
        if not raw_root:
            messagebox.showwarning("Golden OCR", "Elige primero la raiz de sesiones arriba.")
            return
        root = Path(raw_root)
        if not root.exists():
            messagebox.showerror("Golden OCR", f"La ruta no existe:\n{root}")
            return
        out_dir = Path(self.ocr_dir_var.get().strip() or self.controller.DEFAULT_OCR_GOLDEN_DIR)
        self._ocr_running = True
        self.ocr_status_var.set(f"Recolectando hasta {limit} muestras OCR...")
        self.ocr_summary_var.set("Se copiaran imagenes fuente y OCR fiel a ocr_golden_live.")

        def worker() -> None:
            try:
                result_dir = self.controller.collect_ocr_samples_from_sessions([root], limit=limit, out_dir=out_dir)
                records = self.controller.load_ocr_golden_base(result_dir)
                self.after(0, lambda: self._on_ocr_loaded(result_dir, records, None))
            except Exception as exc:
                self.after(0, lambda: self._on_ocr_loaded(None, [], exc))

        threading.Thread(target=worker, daemon=True).start()

    def _import_problem_crops_into_current_ocr_golden(self) -> None:
        try:
            target, added = self.controller.import_problem_crops_into_ocr_golden(
                out_dir=Path(self.ocr_dir_var.get().strip() or self.controller.DEFAULT_OCR_GOLDEN_DIR)
            )
            self.ocr_dir_var.set(str(target))
            self._load_ocr_golden()
            self.ocr_status_var.set(f"Recortes PDF incorporados: {added}. Golden OCR actualizada.")
        except Exception as exc:
            messagebox.showerror("Golden OCR", f"No se pudieron incorporar los recortes PDF.\n{exc}")

    def _select_ocr_training_field(self) -> None:
        field = self.ocr_field_var.get().strip() or "General"
        root, golden_dir = self.controller.OCR_TRAINING_FIELDS[field]
        self.root_var.set(str(root))
        self.ocr_dir_var.set(str(golden_dir))
        self._load_ocr_golden()
        if field == "General":
            self.ocr_status_var.set("Campo activo: General. Golden OCR general cargada.")
        else:
            self.ocr_status_var.set(f"Campo activo: {field}. Golden OCR del campo cargada sin mezclar con la general.")

    def _build_selected_field_ocr_async(self, limit: int) -> None:
        self._select_ocr_training_field()
        self._build_ocr_async(limit)

    def _load_selected_field_ocr_golden(self) -> None:
        self._select_ocr_training_field()
        self._load_ocr_golden()

    def _load_ocr_golden(self) -> None:
        raw_dir = self.ocr_dir_var.get().strip() or str(self.controller.DEFAULT_OCR_GOLDEN_DIR)
        target = Path(raw_dir)
        try:
            records = self.controller.load_ocr_golden_base(target)
        except Exception as exc:
            messagebox.showerror("Golden OCR", f"No se pudo cargar la Golden OCR.\n{exc}")
            return
        self._on_ocr_loaded(target, records, None)

    def _on_ocr_loaded(
        self,
        golden_dir: Path | None,
        records: list[OcrGoldenRecord],
        error: Exception | None,
    ) -> None:
        self._ocr_running = False
        if error is not None:
            self.ocr_status_var.set("No se pudo preparar/cargar la Golden OCR.")
            self.ocr_summary_var.set(str(error))
            messagebox.showerror("Golden OCR", f"No se pudo preparar la Golden OCR.\n{error}")
            return
        self._ocr_records = records
        if golden_dir is not None:
            self.ocr_dir_var.set(str(golden_dir))
        self._populate_ocr_table()
        corrected = sum(1 for record in records if str(record.corrected_text or "").strip())
        reviewed = sum(1 for record in records if str(record.status or "").strip().lower() == "reviewed")
        self.ocr_status_var.set(f"Muestras OCR: {len(records)}")
        self.ocr_summary_var.set(
            f"Corregidas: {corrected} | Revisadas: {reviewed} | Por revisar: {max(0, len(records) - reviewed)}"
        )

    def _populate_ocr_table(self) -> None:
        self.ocr_tree.delete(*self.ocr_tree.get_children())
        visible_indices = self._visible_ocr_indices()
        for idx in visible_indices:
            record = self._ocr_records[idx]
            status = str(record.status or "-")
            self.ocr_tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    status,
                    self._display_ocr_training_section(record),
                    record.book_code or "-",
                    record.instance_type or "-",
                    record.source_label or Path(record.image_path).name or "-",
                    len(record.corrected_text or record.ocr_text or ""),
                ),
                tags=self._ocr_tags_for_record(record),
            )
        if visible_indices:
            self.ocr_tree.selection_set(str(visible_indices[0]))
            self._show_selected_ocr_record()
        else:
            self._clear_ocr_editor()

    def _visible_ocr_indices(self) -> list[int]:
        selected_field = self.ocr_field_var.get().strip() or "General"
        if selected_field == "General":
            return list(range(len(self._ocr_records)))
        return [
            idx
            for idx, record in enumerate(self._ocr_records)
            if self._ocr_training_section_key(record) == selected_field
        ]

    def _ocr_training_section_key(self, record: OcrGoldenRecord) -> str:
        section = str(record.training_section or "").strip()
        if section:
            return section
        result = self.controller.classify_training_section_from_fields(
            curso="",
            tema="",
            book_code=record.book_code,
            instance_type=record.instance_type,
            source_label=record.source_label,
            text=record.corrected_text or record.ocr_text,
        )
        return str(result.get("section") or "General")

    def _clear_ocr_editor(self) -> None:
        self.ocr_image_label.configure(image="", text="Sin muestras para el filtro seleccionado")
        self._ocr_photo = None
        self.ocr_raw_text.delete("1.0", "end")
        self.ocr_corrected_text.delete("1.0", "end")

    def _display_ocr_training_section(self, record: OcrGoldenRecord) -> str:
        section = self._ocr_training_section_key(record)
        confidence = str(record.training_section_confidence or "").strip()
        label = self.controller.SECTION_DISPLAY.get(section, section or "General")
        return f"{label} ({confidence})" if confidence and confidence != "baja" else label

    @staticmethod
    def _ocr_tag_for_status(status: str) -> str:
        normalized = str(status or "").strip().lower()
        if normalized == "reviewed":
            return "ocr_reviewed"
        if normalized in {"corrected", "normalized", "rescanned", "rescanned_raw"}:
            return "ocr_done"
        if normalized in {"error", "failed"}:
            return "ocr_error"
        if normalized in {"procesando", "processing", "reintentando"}:
            return "ocr_processing"
        return "ocr_pending"

    @staticmethod
    def _ocr_tag_for_section(section: str) -> str:
        normalized = str(section or "").strip().lower()
        normalized = normalized.replace(" ", "_")
        normalized = normalized.replace("í", "i").replace("é", "e").replace("á", "a")
        mapping = {
            "geometria": "ocr_section_geometria",
            "geometria_analitica": "ocr_section_geometria_analitica",
            "algebra": "ocr_section_algebra",
            "aritmetica": "ocr_section_aritmetica",
            "trigonometria": "ocr_section_trigonometria",
        }
        return mapping.get(normalized, "")

    def _ocr_tags_for_record(self, record: OcrGoldenRecord) -> tuple[str, ...]:
        status_tag = self._ocr_tag_for_status(record.status)
        if status_tag in {"ocr_error", "ocr_processing"}:
            return (status_tag,)
        section = self._ocr_training_section_key(record)
        section_tag = self._ocr_tag_for_section(section)
        if section_tag:
            return (section_tag,)
        return (status_tag,)

    def _classify_ocr_sections(self) -> None:
        golden_dir = Path(self.ocr_dir_var.get().strip() or self.controller.DEFAULT_OCR_GOLDEN_DIR)
        if not golden_dir.exists():
            messagebox.showwarning("Golden OCR", f"No existe la Golden OCR:\n{golden_dir}")
            return
        try:
            counts = self.controller.classify_ocr_golden_base(golden_dir)
            self._ocr_records = self.controller.load_ocr_golden_base(golden_dir)
        except Exception as exc:
            messagebox.showerror("Golden OCR", f"No se pudo clasificar la Golden OCR.\n{exc}")
            return
        self._populate_ocr_table()
        ordered = ["Geometria", "Geometria analitica", "Algebra", "Aritmetica", "Trigonometria", "General"]
        summary = " | ".join(
            f"{self.controller.SECTION_DISPLAY.get(key, key)}={int(counts.get(key, 0))}" for key in ordered if int(counts.get(key, 0))
        )
        self.ocr_status_var.set(f"Secciones clasificadas: {sum(int(v) for v in counts.values())} muestra(s).")
        self.ocr_summary_var.set(summary or "No se detectaron muestras clasificables.")

    def _link_ocr_records_to_sessions(self) -> None:
        golden_dir = Path(self.ocr_dir_var.get().strip() or self.controller.DEFAULT_OCR_GOLDEN_DIR)
        if not golden_dir.exists():
            messagebox.showwarning("Golden OCR", f"No existe la Golden OCR:\n{golden_dir}")
            return
        try:
            result = self.controller.link_ocr_golden_records_to_sessions(golden_dir)
            pdf_result = {"instances": 0, "pages": 0, "boxes": 0}
            pdf_error = ""
            try:
                from modulos.modulo13_laboratorio_pdf_segmentacion.controlador_laboratorio_pdf import (
                    PdfProblemGoldenController,
                )

                instance_names = self.controller.pdf_instance_names_from_ocr_golden(golden_dir)
                if instance_names:
                    pdf_result = PdfProblemGoldenController().sync_pdf_golden_from_problem_crops(
                        instance_names=instance_names,
                        aggregate_name="piloto_recortes_problemas",
                    )
            except Exception as pdf_exc:
                pdf_error = str(pdf_exc)
        except Exception as exc:
            messagebox.showerror("Golden OCR", f"No se pudieron enlazar las sesiones.\n{exc}")
            return
        self.ocr_status_var.set(
            f"Enlace OCR -> sesiones completado: {int(result.get('linked', 0))}/{int(result.get('total', 0))}."
        )
        summary = (
            f"Enlazadas: {int(result.get('linked', 0))} | Sin sesion resoluble: {int(result.get('skipped', 0))} | "
            f"Golden PDF: {int(pdf_result.get('instances', 0))} actualizada(s), "
            f"{int(pdf_result.get('unchanged', 0))} ya sincronizada(s), "
            f"agregada={'actualizada' if int(pdf_result.get('aggregate_updated', 0)) else 'sin cambios'}, "
            f"{int(pdf_result.get('pages', 0))} pagina(s), {int(pdf_result.get('boxes', 0))} box(es)"
        )
        if pdf_error:
            summary += f" | Aviso Golden PDF: {pdf_error}"
        self.ocr_summary_var.set(summary)

    def _select_ocr_rows_by_status(self, statuses: set[str]) -> None:
        wanted = {str(status or "").strip().lower() for status in statuses}
        selected = [
            str(idx)
            for idx in self._visible_ocr_indices()
            for record in [self._ocr_records[idx]]
            if str(record.status or "pending").strip().lower() in wanted
        ]
        self.ocr_tree.selection_set(selected)
        if selected:
            self.ocr_tree.see(selected[0])
        self.ocr_status_var.set(f"Seleccionadas para cola OCR: {len(selected)} muestra(s).")

    def _select_ocr_rows_needing_review(self) -> None:
        final_statuses = {"reviewed"}
        selected = [
            str(idx)
            for idx in self._visible_ocr_indices()
            for record in [self._ocr_records[idx]]
            if str(record.status or "pending").strip().lower() not in final_statuses
        ]
        self.ocr_tree.selection_set(selected)
        if selected:
            self.ocr_tree.see(selected[0])
        self.ocr_status_var.set(f"Seleccionadas por revisar: {len(selected)} muestra(s).")

    def _mark_selected_ocr_reviewed(self) -> None:
        records = self._selected_ocr_records()
        if not records:
            messagebox.showwarning("Golden OCR", "Selecciona una o varias muestras OCR para marcar como revisadas.")
            return
        golden_dir = Path(self.ocr_dir_var.get().strip() or self.controller.DEFAULT_OCR_GOLDEN_DIR)
        selected_ids = {record.record_id for record in records}
        current_single_text = ""
        if len(records) == 1:
            try:
                current_single_text = self.ocr_corrected_text.get("1.0", "end").strip()
            except Exception:
                current_single_text = ""
        errors: list[str] = []
        updated = 0
        for record in records:
            corrected = current_single_text if len(records) == 1 else str(record.corrected_text or record.ocr_text or "").strip()
            try:
                self.controller.save_ocr_correction(
                    record_id=record.record_id,
                    corrected_text=corrected,
                    status="reviewed",
                    ocr_text=record.ocr_text,
                    ocr_model_text=record.raw.get("ocr_model_text"),
                    notes=record.notes,
                    golden_dir=golden_dir,
                )
                updated += 1
            except Exception as exc:
                errors.append(f"{record.source_label or record.record_id}: {exc}")
        self._ocr_records = self.controller.load_ocr_golden_base(golden_dir)
        self._populate_ocr_table()
        ids_after = {str(idx) for idx, record in enumerate(self._ocr_records) if record.record_id in selected_ids}
        if ids_after:
            self.ocr_tree.selection_set(sorted(ids_after, key=int))
            self.ocr_tree.see(sorted(ids_after, key=int)[0])
        reviewed = sum(1 for record in self._ocr_records if str(record.status or "").strip().lower() == "reviewed")
        self.ocr_status_var.set(f"Marcadas como revisadas: {updated} muestra(s).")
        self.ocr_summary_var.set(
            f"Revisadas: {reviewed} | Por revisar: {max(0, len(self._ocr_records) - reviewed)}"
            + (f" | Errores: {len(errors)}" if errors else "")
        )

    def _copy_all_ocr_block(self) -> None:
        if not self._ocr_records:
            messagebox.showwarning("Golden OCR", "No hay muestras OCR cargadas para copiar.")
            return
        blocks: list[str] = []
        for idx, record in enumerate(self._ocr_records, start=1):
            source = record.source_label or Path(record.image_path).name or record.record_id
            header = (
                f"======================== OCR {idx} | "
                f"libro={record.book_code or '-'} | "
                f"instancia={record.instance_type or '-'} | "
                f"imagen={source} ========================"
            )
            text = self._repair_mojibake_text(str(record.ocr_text or "").strip())
            blocks.append(f"{header}\n{text}")
        payload = "\n\n".join(blocks).strip()
        self.clipboard_clear()
        self.clipboard_append(payload)
        self.update_idletasks()
        self.ocr_status_var.set(f"OCR en bloque copiado al portapapeles: {len(self._ocr_records)} muestra(s).")
        self.ocr_summary_var.set("Se copio el OCR fiel, no las correcciones.")

    def _copy_all_raw_model_ocr_block(self) -> None:
        if not self._ocr_records:
            messagebox.showwarning("Golden OCR", "No hay muestras OCR cargadas para copiar.")
            return
        blocks: list[str] = []
        missing_raw = 0
        for idx, record in enumerate(self._ocr_records, start=1):
            source = record.source_label or Path(record.image_path).name or record.record_id
            raw_model = self._repair_mojibake_text(str(record.raw.get("ocr_model_text") or "").strip())
            if not raw_model:
                missing_raw += 1
                raw_model = self._repair_mojibake_text(str(record.ocr_text or "").strip())
            header = (
                f"======================== OCR_CRUDO {idx} | "
                f"libro={record.book_code or '-'} | "
                f"instancia={record.instance_type or '-'} | "
                f"imagen={source} ========================"
            )
            blocks.append(f"{header}\n{raw_model}")
        payload = "\n\n".join(blocks).strip()
        self.clipboard_clear()
        self.clipboard_append(payload)
        self.update_idletasks()
        self.ocr_status_var.set(f"OCR crudo copiado al portapapeles: {len(self._ocr_records)} muestra(s).")
        if missing_raw:
            self.ocr_summary_var.set(
                f"{missing_raw} muestra(s) no tenian salida cruda guardada; se uso ocr_text como respaldo."
            )
        else:
            self.ocr_summary_var.set("Se copio exactamente la salida cruda guardada del modelo OCR.")

    def _paste_corrected_ocr_block(self) -> None:
        if not self._ocr_records:
            messagebox.showwarning("Golden OCR", "No hay muestras OCR cargadas para actualizar.")
            return
        try:
            payload = self.clipboard_get()
        except Exception as exc:
            messagebox.showerror("Golden OCR", f"No se pudo leer el portapapeles.\n{exc}")
            return
        blocks = self._parse_ocr_clipboard_blocks(payload)
        if not blocks:
            messagebox.showwarning("Golden OCR", "No encontre bloques OCR corregidos en el portapapeles.")
            return
        by_source = self._ocr_record_lookup_by_source()
        golden_dir = Path(self.ocr_dir_var.get().strip() or self.controller.DEFAULT_OCR_GOLDEN_DIR)
        updated = 0
        missing: list[str] = []
        for idx, (source, text) in enumerate(blocks, start=1):
            record = by_source.get(self._normalize_ocr_source_key(source)) if source else None
            if record is None and len(blocks) == len(self._ocr_records):
                record = self._ocr_records[idx - 1]
            if record is None:
                missing.append(source or f"bloque#{idx}")
                continue
            try:
                self.controller.save_ocr_correction(
                    record_id=record.record_id,
                    corrected_text=text.strip(),
                    status="corrected",
                    ocr_text=record.ocr_text,
                    ocr_model_text=record.raw.get("ocr_model_text"),
                    golden_dir=golden_dir,
                )
                updated += 1
                self._update_ocr_row_status(record.record_id, "corrected", tag="ocr_done", chars=len(record.ocr_text or ""))
            except Exception as exc:
                missing.append(f"{source or record.record_id}: {exc}")
        try:
            self._ocr_records = self.controller.load_ocr_golden_base(golden_dir)
            self._populate_ocr_table()
        except Exception:
            pass
        self.ocr_status_var.set(f"Bloque corregido pegado. Actualizadas: {updated}")
        if missing:
            preview = ", ".join(missing[:5])
            self.ocr_summary_var.set(f"No se pudieron asociar {len(missing)} bloque(s): {preview}")
        else:
            self.ocr_summary_var.set("Todas las correcciones del bloque fueron guardadas.")

    def _parse_ocr_clipboard_blocks(self, payload: str) -> list[tuple[str, str]]:
        raw = str(payload or "").strip()
        if not raw:
            return []
        simple_pattern = re.compile(r"(?m)^\s*<<<\s*(?P<source>[^<>\r\n]+?)\s*>>>\s*$")
        simple_matches = list(simple_pattern.finditer(raw))
        if simple_matches:
            blocks: list[tuple[str, str]] = []
            for idx, match in enumerate(simple_matches):
                start = match.end()
                end = simple_matches[idx + 1].start() if idx + 1 < len(simple_matches) else len(raw)
                source = str(match.group("source") or "").strip()
                text = raw[start:end].strip()
                blocks.append((source, text))
            return blocks
        pattern = re.compile(
            r"(?m)^=+\s*(?:OCR|OCR_CRUDO)\s+\d+\s*\|.*?imagen=(?P<source>.*?)\s*=+\s*$"
        )
        matches = list(pattern.finditer(raw))
        if not matches:
            return [("", raw)]
        blocks: list[tuple[str, str]] = []
        for idx, match in enumerate(matches):
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
            source = str(match.group("source") or "").strip()
            text = raw[start:end].strip()
            blocks.append((source, text))
        return blocks

    def _selected_ocr_record(self) -> OcrGoldenRecord | None:
        selection = self.ocr_tree.selection()
        if not selection:
            return None
        try:
            index = int(selection[0])
        except Exception:
            return None
        if 0 <= index < len(self._ocr_records):
            return self._ocr_records[index]
        return None

    def _selected_ocr_records(self) -> list[OcrGoldenRecord]:
        records: list[OcrGoldenRecord] = []
        selected = set(self.ocr_tree.selection())
        for iid in self.ocr_tree.get_children(""):
            if iid not in selected:
                continue
            try:
                index = int(iid)
            except Exception:
                continue
            if 0 <= index < len(self._ocr_records):
                records.append(self._ocr_records[index])
        return records

    def _ocr_records_for_selected_view(self) -> list[OcrGoldenRecord]:
        selected = self._selected_ocr_records()
        return selected if selected else list(self._ocr_records)

    def _copy_selected_ocr_images(self) -> None:
        records = self._selected_ocr_records()
        if not records:
            messagebox.showwarning("Golden OCR", "Selecciona una o varias imagenes en el listado.")
            return
        if win32clipboard is None or win32con is None:
            messagebox.showerror("Golden OCR", "No esta disponible el portapapeles de archivos de Windows (PyWin32).")
            return
        paths: list[str] = []
        missing: list[str] = []
        for record in records:
            image_path = Path(record.copied_image_path or record.image_path).expanduser().resolve()
            if image_path.exists():
                paths.append(str(image_path))
            else:
                missing.append(str(image_path))
        if not paths:
            messagebox.showerror("Golden OCR", "Ninguna de las imagenes seleccionadas existe en disco.")
            return
        try:
            payload = struct.pack("IiiII", 20, 0, 0, 0, 1)
            payload += ("\0".join(paths) + "\0\0").encode("utf-16le")
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_HDROP, payload)
        except Exception as exc:
            messagebox.showerror("Golden OCR", f"No se pudieron copiar las imagenes.\n{exc}")
            return
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass
        self.ocr_status_var.set(f"Imagenes copiadas para pegar en el navegador: {len(paths)}")
        if missing:
            self.ocr_summary_var.set(f"Se omitieron {len(missing)} imagen(es) inexistentes. Se respeto el orden visible del listado.")
        else:
            self.ocr_summary_var.set("Se copiaron como archivos reales y en el orden visible del listado.")

    def _prepare_selected_ocr_images_for_drag(self) -> None:
        records = self._selected_ocr_records()
        if not records:
            messagebox.showwarning("Golden OCR", "Selecciona una o varias imagenes en el listado.")
            return
        base_dir = self.controller.DEFAULT_GOLDEN_ROOT / "ocr_drag_outbox"
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out_dir = base_dir / f"seleccion_{stamp}"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            messagebox.showerror("Golden OCR", f"No se pudo crear la carpeta temporal.\n{exc}")
            return

        copied = 0
        missing: list[str] = []
        order_lines: list[str] = []
        for idx, record in enumerate(records, start=1):
            src = Path(record.copied_image_path or record.image_path).expanduser().resolve()
            if not src.exists():
                missing.append(str(src))
                continue
            safe_stem = self._safe_drag_filename(record.source_label or src.stem or record.record_id)
            suffix = src.suffix if src.suffix else ".png"
            dst = out_dir / f"{idx:03d}_{safe_stem}{suffix}"
            counter = 2
            while dst.exists():
                dst = out_dir / f"{idx:03d}_{safe_stem}_{counter}{suffix}"
                counter += 1
            try:
                shutil.copy2(src, dst)
            except Exception as exc:
                missing.append(f"{src}: {exc}")
                continue
            copied += 1
            order_lines.append(f"{idx:03d}\t{dst.name}\t{src}")
        try:
            (out_dir / "_orden_imagenes.txt").write_text("\n".join(order_lines) + "\n", encoding="utf-8")
        except Exception:
            pass
        if copied <= 0:
            messagebox.showerror("Golden OCR", "No se pudo preparar ninguna imagen seleccionada.")
            return
        try:
            subprocess.Popen(["explorer", str(out_dir)])
        except Exception as exc:
            messagebox.showwarning("Golden OCR", f"Carpeta preparada, pero no se pudo abrir Explorer.\n{out_dir}\n\n{exc}")
        self.ocr_status_var.set(f"Carpeta lista para arrastrar a ChatGPT: {copied} imagen(es).")
        detail = f"Abre la carpeta, selecciona las imagenes y arrastralas al navegador: {out_dir}"
        if missing:
            detail += f" | Omitidas: {len(missing)}"
        self.ocr_summary_var.set(detail)

    @staticmethod
    def _safe_drag_filename(value: str) -> str:
        text = str(value or "").strip()
        text = Path(text).stem if text else "imagen"
        text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text)
        text = re.sub(r"\s+", "_", text).strip("._ ")
        return (text or "imagen")[:90]

    def _open_chatgpt_block_import_window(self) -> None:
        if not self._ocr_records:
            messagebox.showwarning("Golden OCR", "Carga primero una Golden OCR.")
            return
        window = tk.Toplevel(self)
        window.title("Importar bloque corregido de ChatGPT")
        window.geometry("980x700")
        window.minsize(760, 520)
        window.transient(self)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(1, weight=1)
        ttk.Label(
            window,
            text=(
                "Pega el bloque con encabezados como <<<nombre>>> o <<<nombre.png>>>. "
                "Tambien se acepta: \\item[\\textbf{1.}] \\textbf{OCR 1: nombre.png}"
            ),
            style="Muted.TLabel",
        ).grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 8))
        editor = tk.Text(window, wrap="word", undo=True, bg="#ffffff", fg="#0f172a", relief="solid", bd=1)
        editor.grid(row=1, column=0, sticky="nsew", padx=14)
        status_var = tk.StringVar(value="Pega el resultado completo y pulsa Actualizar Scan OCR.")
        ttk.Label(window, textvariable=status_var, style="Muted.TLabel").grid(
            row=2, column=0, sticky="ew", padx=14, pady=(8, 4)
        )
        actions = ttk.Frame(window)
        actions.grid(row=3, column=0, sticky="e", padx=14, pady=(4, 14))

        def paste_clipboard() -> None:
            try:
                text = self.clipboard_get()
            except Exception as exc:
                messagebox.showerror("Importar bloque", f"No se pudo leer el portapapeles.\n{exc}", parent=window)
                return
            editor.delete("1.0", "end")
            editor.insert("1.0", text)

        def apply_import() -> None:
            updated, missing = self._import_chatgpt_latex_ocr_block(editor.get("1.0", "end"))
            status_var.set(f"Actualizadas: {updated} | No localizadas: {len(missing)}")
            if missing:
                messagebox.showwarning(
                    "Importar bloque",
                    "No se localizaron estos archivos:\n" + "\n".join(missing[:12]),
                    parent=window,
                )
            elif updated:
                messagebox.showinfo("Importar bloque", f"Se actualizaron {updated} archivos.", parent=window)

        ttk.Button(actions, text="Pegar portapapeles", command=paste_clipboard, style="Ghost.TButton").pack(side="left")
        ttk.Button(actions, text="Actualizar Scan OCR", command=apply_import, style="Accent.TButton").pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(actions, text="Cerrar", command=window.destroy, style="Ghost.TButton").pack(side="left", padx=(8, 0))
        editor.focus_set()

    def _open_ocr_plain_block_editor(self) -> None:
        if not self._ocr_records:
            messagebox.showwarning("Golden OCR", "Carga primero una Golden OCR.")
            return
        top = tk.Toplevel(self)
        top.title("Editor masivo de Golden OCR")
        top.geometry("1120x780")
        top.minsize(820, 580)
        top.transient(self)
        top.columnconfigure(0, weight=1)
        top.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(top, padding=(12, 10))
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(1, weight=1)
        toolbar.columnconfigure(3, weight=1)
        ttk.Label(toolbar, text="Buscar", style="FieldLabel.TLabel").grid(row=0, column=0, sticky="w")
        find_var = tk.StringVar(value="")
        replace_var = tk.StringVar(value="")
        ttk.Entry(toolbar, textvariable=find_var).grid(row=0, column=1, sticky="ew", padx=(6, 10))
        ttk.Label(toolbar, text="Reemplazar por", style="FieldLabel.TLabel").grid(row=0, column=2, sticky="w")
        ttk.Entry(toolbar, textvariable=replace_var).grid(row=0, column=3, sticky="ew", padx=(6, 10))
        count_var = tk.StringVar(value="")
        ttk.Label(toolbar, textvariable=count_var, style="Muted.TLabel").grid(row=1, column=0, columnspan=5, sticky="w", pady=(6, 0))

        editor_frame = ttk.Frame(top, padding=(12, 0))
        editor_frame.grid(row=1, column=0, sticky="nsew")
        editor_frame.columnconfigure(0, weight=1)
        editor_frame.rowconfigure(0, weight=1)
        editor = tk.Text(editor_frame, wrap="word", undo=True, bg="#ffffff", fg="#0f172a", relief="solid", bd=1)
        editor.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(editor_frame, orient="vertical", command=editor.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        editor.configure(yscrollcommand=scroll.set)

        actions = ttk.Frame(top, padding=(12, 10))
        actions.grid(row=2, column=0, sticky="ew")
        status_var = tk.StringVar(
            value="Edita el bloque completo. Cada encabezado <<<nombre>>> o <<<nombre.png>>> identifica un archivo."
        )
        ttk.Label(actions, textvariable=status_var, style="Muted.TLabel").pack(side="left", fill="x", expand=True)

        def replace_all() -> None:
            source = find_var.get()
            if not source:
                messagebox.showwarning("Editor masivo", "Escribe primero el texto que deseas buscar.", parent=top)
                return
            content = editor.get("1.0", "end-1c")
            occurrences = content.count(source)
            if occurrences:
                editor.delete("1.0", "end")
                editor.insert("1.0", content.replace(source, replace_var.get()))
            count_var.set(f"Reemplazos aplicados en memoria: {occurrences}")

        def save_all() -> None:
            updated, missing = self._import_chatgpt_latex_ocr_block(editor.get("1.0", "end"))
            status_var.set(f"Guardadas: {updated} | No localizadas: {len(missing)}")
            if missing:
                messagebox.showwarning("Editor masivo", "No se localizaron:\n" + "\n".join(missing[:12]), parent=top)
            elif updated:
                messagebox.showinfo("Editor masivo", f"Se guardaron {updated} registros OCR.", parent=top)

        ttk.Button(toolbar, text="Reemplazar todo", command=replace_all, style="Accent.TButton").grid(row=0, column=4)
        ttk.Button(actions, text="Guardar bloque completo", command=save_all, style="Accent.TButton").pack(side="right")
        ttk.Button(actions, text="Cerrar", command=top.destroy, style="Ghost.TButton").pack(side="right", padx=(0, 8))
        editor.insert("1.0", self._build_ocr_plain_block())
        editor.focus_set()

    def _build_ocr_plain_block(self) -> str:
        blocks: list[str] = []
        for record in self._ocr_records:
            source = record.source_label or Path(record.image_path).name or record.record_id
            body = self._repair_mojibake_text(str(record.corrected_text or record.ocr_text or "").strip())
            blocks.append(f"<<<{source}>>>\n{body}".strip())
        return "\n\n".join(blocks).strip()

    @staticmethod
    def _canonical_ocr_source_name(value: str) -> str:
        name = Path(str(value or "").strip()).name
        # El arrastre por lotes antepone 001_, 002_, etc.; ChatGPT a veces conserva ese prefijo.
        name = re.sub(r"^\d{3,4}_", "", name)
        # Windows/ChatGPT puede devolver copias como problem_01(1).png.
        return re.sub(r"\(\d+\)(?=\.[^.]+$)", "", name)

    def _normalize_ocr_source_key(self, value: str) -> str:
        text = str(value or "").strip().strip("<> \t\r\n")
        if not text:
            return ""
        text = text.replace("\\", "/").split("/")[-1].strip()
        text = self._canonical_ocr_source_name(text)
        if re.search(r"\.(png|jpe?g|webp|bmp|tiff?)$", text, flags=re.IGNORECASE):
            text = Path(text).stem
        return text.casefold()

    def _ocr_record_source_aliases(self, record: OcrGoldenRecord) -> set[str]:
        raw_values = [
            record.source_label,
            Path(record.image_path).name if record.image_path else "",
            Path(record.image_path).stem if record.image_path else "",
            Path(record.copied_image_path).name if record.copied_image_path else "",
            Path(record.copied_image_path).stem if record.copied_image_path else "",
            record.record_id,
        ]
        aliases: set[str] = set()
        for raw in raw_values:
            name = str(raw or "").strip()
            if not name:
                continue
            canonical = self._canonical_ocr_source_name(name)
            stem = Path(canonical).stem
            aliases.update(
                {
                    name,
                    Path(name).name,
                    canonical,
                    stem,
                    f"{stem}.png",
                }
            )
        return {self._normalize_ocr_source_key(alias) for alias in aliases if alias}

    def _ocr_record_lookup_by_source(self) -> dict[str, OcrGoldenRecord]:
        lookup: dict[str, OcrGoldenRecord] = {}
        for record in self._ocr_records:
            for alias in self._ocr_record_source_aliases(record):
                if alias:
                    lookup.setdefault(alias, record)
        return lookup

    @staticmethod
    def _repair_mojibake_text(value: str) -> str:
        text = str(value or "")
        replacements = {
            "\u00e2\u20ac\u0153": "\u201c",
            "\u00e2\u20ac\u009d": "\u201d",
            "\u00e2\u20ac\u2122": "\u2019",
            "\u00e2\u20ac\u201c": "\u2013",
            "\u00e2\u20ac\u201d": "\u2014",
        }
        for broken, fixed in replacements.items():
            text = text.replace(broken, fixed)
        markers = (
            "\u00c3",
            "\u00c2",
            "\u00e2\u20ac",
            "\u00e2\u20ac\u0153",
            "\u00e2\u20ac\u009d",
            "\u00e2\u20ac\u2122",
            "\u00e2\u20ac\u201c",
            "\u00e2\u20ac\u201d",
        )
        if not any(marker in text for marker in markers):
            return text

        def score(candidate: str) -> int:
            return sum(candidate.count(marker) for marker in markers)

        best = text
        best_score = score(text)
        for _ in range(2):
            changed = False
            for encoding in ("cp1252", "latin1"):
                try:
                    repaired = best.encode(encoding).decode("utf-8")
                except UnicodeError:
                    continue
                repaired_score = score(repaired)
                if repaired_score < best_score:
                    best = repaired
                    best_score = repaired_score
                    changed = True
                    break
            if not changed:
                break
        return best

    def _import_chatgpt_latex_ocr_block(self, payload: str) -> tuple[int, list[str]]:
        blocks = self._parse_chatgpt_latex_ocr_blocks(payload)
        if not blocks:
            messagebox.showwarning(
                "Importar bloque",
                "No encontre encabezados del tipo <<<nombre>>> / <<<nombre.png>>> o \\item[\\textbf{1.}] \\textbf{OCR 1: nombre.png}.",
            )
            return 0, []
        by_name = self._ocr_record_lookup_by_source()
        golden_dir = Path(self.ocr_dir_var.get().strip() or self.controller.DEFAULT_OCR_GOLDEN_DIR)
        updated = 0
        missing: list[str] = []
        for source, text in blocks:
            record = by_name.get(self._normalize_ocr_source_key(source))
            if record is None:
                missing.append(source)
                continue
            try:
                clean_text = self._repair_mojibake_text(text.strip())
                self.controller.save_ocr_correction(
                    record_id=record.record_id,
                    corrected_text=clean_text,
                    status="corrected",
                    ocr_text=clean_text,
                    ocr_model_text=record.raw.get("ocr_model_text"),
                    golden_dir=golden_dir,
                )
                updated += 1
            except Exception as exc:
                missing.append(f"{source}: {exc}")
        self._ocr_records = self.controller.load_ocr_golden_base(golden_dir)
        self._populate_ocr_table()
        self.ocr_status_var.set(f"Scan OCR actualizado. Archivos: {updated} | No localizados: {len(missing)}")
        self.ocr_summary_var.set("El Scan OCR y su correccion inicial se actualizaron usando el nombre de cada imagen.")
        return updated, missing

    def _parse_chatgpt_latex_ocr_blocks(self, payload: str) -> list[tuple[str, str]]:
        raw = str(payload or "").strip()
        if not raw:
            return []
        item_pattern = re.compile(
            r"(?m)^\\item\s*\[\\textbf\{\d+\.\}\]\s*\\textbf\{OCR\s+\d+\s*:\s*(?P<source>[^}]+)\}\s*$"
        )
        simple_pattern = re.compile(r"(?m)^\s*<<<\s*(?P<source>[^<>\r\n]+?)\s*>>>\s*$", re.IGNORECASE)
        matches = list(simple_pattern.finditer(raw)) or list(item_pattern.finditer(raw))
        blocks: list[tuple[str, str]] = []
        for idx, match in enumerate(matches):
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
            source = str(match.group("source") or "").strip()
            text = raw[start:end].strip()
            if source and text:
                blocks.append((source, text))
        return blocks

    def _copy_selected_ocr_visual_block(self) -> None:
        records = self._selected_ocr_records()
        if not records:
            messagebox.showwarning("Golden OCR", "Selecciona una o varias imagenes en el listado.")
            return
        if Image is None or ImageDraw is None or win32clipboard is None or win32con is None:
            messagebox.showerror("Golden OCR", "No estan disponibles Pillow o el portapapeles de Windows.")
            return
        panels: list[tuple[int, object]] = []
        missing = 0
        max_width = 1400
        for idx, record in enumerate(records, start=1):
            image_path = Path(record.copied_image_path or record.image_path).expanduser().resolve()
            if not image_path.exists():
                missing += 1
                continue
            try:
                with Image.open(image_path) as source:
                    image = source.convert("RGB")
            except Exception:
                missing += 1
                continue
            if image.width > max_width:
                height = max(1, round(image.height * max_width / image.width))
                image = image.resize((max_width, height))
            panels.append((idx, image))
        if not panels:
            messagebox.showerror("Golden OCR", "No se pudo abrir ninguna imagen seleccionada.")
            return
        margin = 20
        header = 42
        canvas_width = max(image.width for _, image in panels) + margin * 2
        canvas_height = margin + sum(header + image.height + margin for _, image in panels)
        canvas = Image.new("RGB", (canvas_width, canvas_height), "white")
        draw = ImageDraw.Draw(canvas)
        y = margin
        for idx, image in panels:
            draw.rectangle((0, y - 8, canvas_width, y + header - 8), fill="#e2e8f0")
            draw.text((margin, y + 4), f"<<< IMAGEN {idx} >>>", fill="black")
            y += header
            canvas.paste(image, (margin, y))
            y += image.height + margin
        output = BytesIO()
        canvas.save(output, format="BMP")
        dib = output.getvalue()[14:]
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_DIB, dib)
        except Exception as exc:
            messagebox.showerror("Golden OCR", f"No se pudo copiar el bloque visual.\n{exc}")
            return
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass
        self.ocr_status_var.set(f"Bloque visual copiado para ChatGPT: {len(panels)} imagen(es).")
        detail = "Pega una sola vez en el navegador; la lamina conserva el orden visible."
        if missing:
            detail += f" Se omitieron {missing} imagen(es) que no pudieron abrirse."
        self.ocr_summary_var.set(detail)

    def _copy_selected_ocr_chatgpt_prompt(self) -> None:
        records = self._selected_ocr_records()
        if not records:
            messagebox.showwarning("Golden OCR", "Selecciona primero las mismas imagenes que enviaras a ChatGPT.")
            return
        prompt = (
            "Transcribe fielmente TODAS las imagenes adjuntas en el orden recibido. "
            "No resumas, no resuelvas y no inventes contenido. Usa LaTeX para las expresiones matematicas. "
            "Si una imagen contiene un dibujo, grafico o diagrama asociado al problema n, coloca "
            "[[Imagen=img-n]] antes de sus alternativas. Devuelve exactamente un bloque por imagen, incluso si "
            "una imagen contiene continuaciones o varios problemas. No agregues explicaciones fuera de los bloques.\n\n"
            "REGLAS OBLIGATORIAS PARA LA GOLDEN BASE:\n"
            "1. Si ves el numero de un problema, escribe exactamente <n.> al inicio de una linea nueva. "
            "Ejemplo: <3.>. No escribas 'Problema 3', <3> ni N.3.\n"
            "2. Si la imagen empieza con texto, una formula o alternativas que continuan el problema anterior y "
            "todavia no aparece un nuevo numero visible, escribe exactamente [CONT.] al inicio del bloque.\n"
            "3. Si una imagen comienza con alternativas sueltas, conserva [CONT.] y luego escribe cada alternativa "
            "visible en su propia linea como A), B), C), D), E).\n"
            "4. No inventes numeros de problema. Si aparece un nuevo encabezado visible dentro de la misma imagen, "
            "escribe su <n.> en una linea nueva.\n"
            "5. Si hay un grafico asociado al problema <n.>, coloca [[Imagen=img-n]] en la posicion correspondiente. "
            "Si el bloque empieza con [CONT.] y contiene una figura del problema anterior, usa [[Imagen=img-continuacion]]. "
            "Si no puedes asociarlo con seguridad, usa [[Imagen=img-pendiente]].\n\n"
            "EJEMPLO DE CONTINUACION:\n"
            "<<<IMAGEN 1>>>\n"
            "[CONT.]\n"
            "[[Imagen=img-continuacion]]\n"
            "D) $40^\\circ$\n"
            "E) $50^\\circ$\n\n"
            "EJEMPLO DE PROBLEMA NUEVO:\n"
            "<<<IMAGEN 2>>>\n"
            "<7.> En el grafico, calcule $x$.\n"
            "[[Imagen=img-7]]\n"
            "A) $10^\\circ$\n"
            "B) $20^\\circ$\n\n"
            "Formato obligatorio:\n"
            + "\n\n".join(f"<<<IMAGEN {idx}>>>\n[transcripcion completa de la imagen {idx}]" for idx in range(1, len(records) + 1))
        )
        self.clipboard_clear()
        self.clipboard_append(prompt)
        self.update_idletasks()
        self.ocr_status_var.set(f"Prompt para ChatGPT copiado: {len(records)} imagen(es).")
        self.ocr_summary_var.set("Pega el prompt en el navegador despues de adjuntar las imagenes seleccionadas.")

    def _paste_selected_ocr_chatgpt_response(self) -> None:
        records = self._selected_ocr_records()
        if not records:
            messagebox.showwarning("Golden OCR", "Selecciona las mismas imagenes cuya respuesta devolvio ChatGPT.")
            return
        try:
            payload = self.clipboard_get()
        except Exception as exc:
            messagebox.showerror("Golden OCR", f"No se pudo leer el portapapeles.\n{exc}")
            return
        blocks = self._parse_chatgpt_image_blocks(payload)
        if not blocks:
            messagebox.showwarning(
                "Golden OCR",
                "No encontre bloques <<<IMAGEN n>>>. Usa primero el boton 'Copiar prompt ChatGPT'.",
            )
            return
        if len(blocks) != len(records):
            messagebox.showerror(
                "Golden OCR",
                f"La respuesta contiene {len(blocks)} bloque(s), pero seleccionaste {len(records)} imagen(es). "
                "Selecciona exactamente las mismas filas y conserva su orden.",
            )
            return
        golden_dir = Path(self.ocr_dir_var.get().strip() or self.controller.DEFAULT_OCR_GOLDEN_DIR)
        updated = 0
        errors: list[str] = []
        for record, text in zip(records, blocks):
            try:
                self.controller.save_ocr_correction(
                    record_id=record.record_id,
                    corrected_text=text,
                    status="corrected",
                    ocr_text=record.ocr_text,
                    ocr_model_text=record.raw.get("ocr_model_text"),
                    golden_dir=golden_dir,
                )
                updated += 1
            except Exception as exc:
                errors.append(f"{record.source_label or record.record_id}: {exc}")
        self._ocr_records = self.controller.load_ocr_golden_base(golden_dir)
        self._populate_ocr_table()
        self.ocr_status_var.set(f"Respuesta ChatGPT pegada en bloque. Actualizadas: {updated}/{len(records)}")
        self.ocr_summary_var.set(
            f"Errores: {len(errors)} | {errors[0]}" if errors else "Cada bloque se guardo en su imagen segun el orden visible del listado."
        )

    def _parse_chatgpt_image_blocks(self, payload: str) -> list[str]:
        raw = str(payload or "").strip()
        if not raw:
            return []
        pattern = re.compile(r"(?mi)^\s*<<<\s*IMAGEN\s+(\d+)\s*>>>\s*$")
        matches = list(pattern.finditer(raw))
        if not matches:
            return []
        indexed: list[tuple[int, str]] = []
        for idx, match in enumerate(matches):
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
            text = raw[start:end].strip()
            if text:
                indexed.append((int(match.group(1)), text))
        indexed.sort(key=lambda item: item[0])
        return [text for _, text in indexed]

    def _update_ocr_row_status(self, record_id: str, status: str, *, tag: str | None = None, chars: int | None = None) -> None:
        for idx, record in enumerate(self._ocr_records):
            if record.record_id != record_id:
                continue
            iid = str(idx)
            if not self.ocr_tree.exists(iid):
                return
            values = list(self.ocr_tree.item(iid, "values"))
            if values:
                values[0] = status
            if chars is not None and len(values) >= 6:
                values[5] = chars
            record.status = status
            if chars is not None:
                values[1] = self._display_ocr_training_section(record)
            tags = (tag,) if tag else self._ocr_tags_for_record(record)
            self.ocr_tree.item(iid, values=values, tags=tags)
            self.ocr_tree.see(iid)
            break

    def _show_selected_ocr_record(self) -> None:
        record = self._selected_ocr_record()
        self.ocr_raw_text.delete("1.0", "end")
        self.ocr_corrected_text.delete("1.0", "end")
        if record is None:
            self.ocr_image_label.configure(image="", text="Sin imagen")
            self._ocr_photo = None
            return
        self.ocr_raw_text.insert("1.0", record.ocr_text or "")
        self.ocr_corrected_text.insert("1.0", record.corrected_text or record.ocr_text or "")
        if str(record.notes or "").strip():
            self.ocr_summary_var.set(f"Nota/error: {record.notes}")
        else:
            section = self._display_ocr_training_section(record)
            reason = str(record.training_section_reason or "").strip()
            self.ocr_summary_var.set(f"Seccion: {section}" + (f" | {reason}" if reason else ""))
        self._render_ocr_image(record)
        self._schedule_ocr_latex_preview()

    def _render_ocr_image(self, record: OcrGoldenRecord) -> None:
        self._ocr_photo = None
        image_path = Path(record.copied_image_path or record.image_path)
        if Image is None or ImageTk is None or not image_path.exists():
            self.ocr_image_label.configure(image="", text="Sin vista previa")
            return
        try:
            with Image.open(image_path) as img:
                img.thumbnail((520, 520))
                photo = ImageTk.PhotoImage(img.copy())
        except Exception:
            self.ocr_image_label.configure(image="", text="No se pudo abrir imagen")
            return
        self._ocr_photo = photo
        self.ocr_image_label.configure(image=photo, text="")

    def _ocr_copy_raw_to_corrected(self) -> None:
        raw = self.ocr_raw_text.get("1.0", "end").strip()
        self.ocr_corrected_text.delete("1.0", "end")
        self.ocr_corrected_text.insert("1.0", raw)

    def _normalize_selected_ocr_with_model(self) -> None:
        if self._ocr_running:
            return
        record = self._selected_ocr_record()
        if record is None:
            return
        image_path = Path(record.copied_image_path or record.image_path)
        if not image_path.exists():
            messagebox.showwarning("Golden OCR", f"No existe la imagen para escanear:\n{image_path}")
            return
        self._ocr_running = True
        self.ocr_status_var.set("Escaneando toda la imagen con OCR puro...")
        self.ocr_summary_var.set("Se transcribira toda la imagen sin normalizar ni modificar el contenido.")

        def worker() -> None:
            try:
                raw_ocr, normalized, meta = self.controller.scan_image_ocr_and_normalize(
                    image_path,
                    model=self.ocr_model_var.get().strip() or None,
                    book_code=record.book_code,
                    instance_type=record.instance_type,
                )
                self.after(0, lambda: self._on_ocr_model_normalized(raw_ocr, normalized, meta, None))
            except Exception as exc:
                self.after(0, lambda: self._on_ocr_model_normalized("", "", {}, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_ocr_model_normalized(self, raw_ocr: str, normalized: str, meta: dict, error: Exception | None) -> None:
        self._ocr_running = False
        if error is not None:
            self.ocr_status_var.set("No se pudo escanear la imagen con el modelo.")
            self.ocr_summary_var.set(str(error))
            messagebox.showerror("Golden OCR", f"No se pudo escanear la imagen con el modelo.\n{error}")
            return
        self.ocr_raw_text.delete("1.0", "end")
        self.ocr_raw_text.insert("1.0", raw_ocr)
        self.ocr_corrected_text.delete("1.0", "end")
        self.ocr_corrected_text.insert("1.0", normalized)
        record = self._selected_ocr_record()
        if record is not None and str(meta.get("raw_model_text", "") or "").strip():
            self._ocr_model_text_pending[record.record_id] = str(meta.get("raw_model_text", "") or "")
        model = str(meta.get("model", "") or "").strip()
        provider = str(meta.get("provider", "") or "").strip()
        suffix = "modelo usado" if meta.get("used_model") else "fallback local"
        self.ocr_status_var.set("Imagen escaneada completa. Revisa y guarda si esta correcto.")
        self.ocr_summary_var.set(f"{provider} | {model} | {suffix}".strip(" |"))

    def _normalize_selected_ocr_queue(self) -> None:
        if self._ocr_running:
            return
        selected = self._selected_ocr_records()
        if not selected:
            messagebox.showwarning("Golden OCR", "Selecciona una o varias muestras OCR en la tabla.")
            return
        model = self.ocr_model_var.get().strip() or self.controller.DEFAULT_OPENAI_FORMAT_MODEL
        golden_dir = Path(self.ocr_dir_var.get().strip() or self.controller.DEFAULT_OCR_GOLDEN_DIR)
        self._ocr_running = True
        self.ocr_status_var.set(f"Cola OCR iniciada: {len(selected)} muestra(s).")
        self.ocr_summary_var.set(f"Modelo: {model}")

        def worker() -> None:
            ok = 0
            errors: list[str] = []
            pending = list(selected)
            try:
                batch_size = int(str(os.getenv("MOD12_OCR_BATCH_SIZE", "10") or "10").strip())
            except Exception:
                batch_size = 10
            batch_size = max(1, min(100, batch_size))
            try:
                batch_pause_seconds = float(
                    str(os.getenv("MOD12_OCR_BATCH_PAUSE_SECONDS", "15") or "15").strip()
                )
            except Exception:
                batch_pause_seconds = 15.0
            batch_pause_seconds = max(0.0, min(180.0, batch_pause_seconds))
            completed = 0
            for pass_index in range(2):
                retry_pending: list[OcrGoldenRecord] = []
                for idx, record in enumerate(pending, start=1):
                    batch_number = ((idx - 1) // batch_size) + 1
                    batch_total = max(1, (len(pending) + batch_size - 1) // batch_size)
                    if idx > 1 and (idx - 1) % batch_size == 0:
                        self.after(
                            0,
                            lambda b=batch_number, bt=batch_total, done=completed: self.ocr_status_var.set(
                                f"Cola OCR: lote {b}/{bt} | guardadas={done}. Pausa preventiva..."
                            ),
                        )
                        if batch_pause_seconds > 0:
                            time.sleep(batch_pause_seconds)
                    if pass_index > 0:
                        time.sleep(4)
                    elif idx > 1:
                        time.sleep(1)
                    self.after(0, lambda rid=record.record_id: self._update_ocr_row_status(rid, "procesando", tag="ocr_processing"))
                    image_path = Path(record.copied_image_path or record.image_path)
                    if not image_path.exists():
                        errors.append(f"{record.record_id}: imagen no encontrada")
                        self.after(
                            0,
                            lambda i=idx, rid=record.record_id: (
                                self._update_ocr_row_status(rid, "error", tag="ocr_error"),
                                self.ocr_status_var.set(f"Cola OCR: {i}/{len(pending)} procesadas..."),
                            ),
                        )
                        continue
                    try:
                        raw_ocr, normalized, _meta = self.controller.scan_image_ocr_and_normalize(
                            image_path,
                            model=model,
                            book_code=record.book_code,
                            instance_type=record.instance_type,
                            retries=8,
                        )
                        self.controller.save_ocr_correction(
                            record_id=record.record_id,
                            corrected_text=normalized,
                            status="rescanned_raw",
                            ocr_text=raw_ocr,
                            ocr_model_text=str(_meta.get("raw_model_text", "") or raw_ocr),
                            golden_dir=golden_dir,
                        )
                        ok += 1
                        completed += 1
                        self.after(
                            0,
                            lambda rid=record.record_id, text=raw_ocr: self._update_ocr_row_status(
                                rid,
                                "rescanned_raw",
                                tag="ocr_done",
                                chars=len(text or ""),
                            ),
                        )
                    except Exception as exc:
                        error_text = str(exc)
                        if pass_index == 0 and self.controller._is_retryable_model_error(exc):
                            retry_pending.append(record)
                            self.after(0, lambda rid=record.record_id: self._update_ocr_row_status(rid, "reintentando", tag="ocr_processing"))
                            continue
                        errors.append(f"{record.source_label or record.record_id}: {error_text}")
                        try:
                            self.controller.save_ocr_correction(
                                record_id=record.record_id,
                                corrected_text=record.corrected_text,
                                status="error",
                                notes=error_text,
                                golden_dir=golden_dir,
                            )
                        except Exception:
                            pass
                        self.after(0, lambda rid=record.record_id: self._update_ocr_row_status(rid, "error", tag="ocr_error"))
                    self.after(
                        0,
                        lambda i=idx, n=len(pending), done=ok, p=pass_index: self.ocr_status_var.set(
                            f"Cola OCR ronda {p + 1}: {i}/{n} procesadas | OK={done}"
                        ),
                    )
                if not retry_pending:
                    break
                pending = retry_pending
                self.after(
                    0,
                    lambda n=len(pending): self.ocr_status_var.set(
                        f"Endpoint temporalmente ocupado. Segunda ronda automatica: {n} muestra(s)."
                    ),
                )
                time.sleep(45)
            try:
                records = self.controller.load_ocr_golden_base(golden_dir)
            except Exception as exc:
                self.after(0, lambda: self._on_ocr_queue_done([], ok, errors + [str(exc)], golden_dir))
                return
            self.after(0, lambda: self._on_ocr_queue_done(records, ok, errors, golden_dir))

        threading.Thread(target=worker, daemon=True).start()

    def _on_ocr_queue_done(
        self,
        records: list[OcrGoldenRecord],
        ok: int,
        errors: list[str],
        golden_dir: Path,
    ) -> None:
        self._ocr_running = False
        if records:
            self._ocr_records = records
            self.ocr_dir_var.set(str(golden_dir))
            self._populate_ocr_table()
        total_errors = len(errors)
        self.ocr_status_var.set(f"Cola OCR terminada. OK={ok} | Errores={total_errors}")
        if total_errors:
            preview = "\n".join(errors[:5])
            self.ocr_summary_var.set(f"Primeros errores:\n{preview}")
            messagebox.showwarning("Golden OCR", f"Cola terminada con {total_errors} error(es).\n\n{preview}")
        else:
            self.ocr_summary_var.set("Todas las muestras seleccionadas fueron escaneadas y guardadas.")
        self._scale_ocr_endpoint_to_zero_async(silent=True)

    def _scale_ocr_endpoint_to_zero_async(self, silent: bool = False) -> None:
        if not silent:
            self.ocr_status_var.set("Apagando endpoint OCR dedicado...")

        def worker() -> None:
            try:
                status = self.controller.scale_trained_ocr_endpoint_to_zero()
            except Exception as exc:
                if not silent:
                    self.after(0, lambda e=exc: messagebox.showerror("Golden OCR", f"No se pudo apagar el endpoint OCR.\n{e}"))
                return
            self.after(
                0,
                lambda s=status: self.ocr_status_var.set(
                    f"Endpoint OCR apagado ({s}). Se reactivara automaticamente en el siguiente escaneo."
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    def _save_selected_ocr_correction(self) -> None:
        record = self._selected_ocr_record()
        if record is None:
            return
        corrected = self.ocr_corrected_text.get("1.0", "end").strip()
        try:
            self.controller.save_ocr_correction(
                record_id=record.record_id,
                corrected_text=corrected,
                status="corrected" if corrected else "pending",
                ocr_text=self.ocr_raw_text.get("1.0", "end").strip(),
                ocr_model_text=self._ocr_model_text_pending.pop(record.record_id, None) or record.raw.get("ocr_model_text"),
                golden_dir=Path(self.ocr_dir_var.get().strip() or self.controller.DEFAULT_OCR_GOLDEN_DIR),
            )
            records = self.controller.load_ocr_golden_base(Path(self.ocr_dir_var.get().strip()))
        except Exception as exc:
            messagebox.showerror("Golden OCR", f"No se pudo guardar la correccion.\n{exc}")
            return
        self._ocr_records = records
        self._populate_ocr_table()
        self.ocr_summary_var.set(
            f"Corregidas: {sum(1 for row in records if str(row.corrected_text or '').strip())} | Total: {len(records)}"
        )

    def _open_selected_ocr_image(self) -> None:
        record = self._selected_ocr_record()
        if record is None:
            return
        image_path = Path(record.copied_image_path or record.image_path)
        self._open_path(image_path)

    def _schedule_ocr_latex_preview(self) -> None:
        if self._ocr_preview_after:
            try:
                self.after_cancel(self._ocr_preview_after)
            except Exception:
                pass
        self._ocr_preview_after = self.after(220, self._push_ocr_latex_preview)

    def _push_ocr_latex_preview(self) -> None:
        self._ocr_preview_after = None
        if not hasattr(self, "ocr_corrected_text"):
            return
        if self._ocr_preview_block_mode:
            records = self._ocr_records_for_selected_view()
            self._ocr_preview_record_ids = [record.record_id for record in records]
            text = self._build_ocr_latex_block_preview(records)
            images = {}
        else:
            record = self._selected_ocr_record()
            self._ocr_preview_record_ids = [record.record_id] if record is not None else []
            text = self.ocr_corrected_text.get("1.0", "end").strip()
            images = {}
        self._ocr_latex_preview.set_images(images)
        self._ocr_latex_preview.set_corrected_items(
            [
                idx
                for idx, record in enumerate(self._ocr_records_for_selected_view(), start=1)
                if str(record.corrected_text or "").strip()
            ]
        )
        self._ocr_latex_preview.set_text(text)

    def _open_ocr_latex_preview(self) -> None:
        self._ocr_preview_block_mode = False
        self._push_ocr_latex_preview()
        try:
            x = self.winfo_rootx() + max(30, self.winfo_width() - 80)
            y = self.winfo_rooty() + 20
            self._ocr_latex_preview.ensure_open_at(x=x, y=y, on_top=False)
        except Exception as exc:
            messagebox.showerror("Golden OCR", f"No se pudo abrir la vista LaTeX.\n{exc}")

    def _open_ocr_latex_block_preview(self) -> None:
        self._ocr_preview_block_mode = True
        self._push_ocr_latex_preview()
        try:
            x = self.winfo_rootx() + max(30, self.winfo_width() - 80)
            y = self.winfo_rooty() + 20
            self._ocr_latex_preview.ensure_open_at(x=x, y=y, on_top=False)
            self._schedule_ocr_preview_poll()
        except Exception as exc:
            messagebox.showerror("Golden OCR", f"No se pudo abrir la vista del bloque LaTeX.\n{exc}")

    def _schedule_ocr_preview_poll(self) -> None:
        if self._ocr_preview_poll_after:
            try:
                self.after_cancel(self._ocr_preview_poll_after)
            except Exception:
                pass
        self._ocr_preview_poll_after = self.after(350, self._poll_ocr_preview_edits)

    def _poll_ocr_preview_edits(self) -> None:
        self._ocr_preview_poll_after = None
        try:
            edit_requests = self._ocr_latex_preview.pop_edit_requests()
        except Exception:
            edit_requests = []
        changed = False
        for request in edit_requests:
            try:
                item_num = int(request.get("item") or 0)
            except (TypeError, ValueError):
                continue
            if not (1 <= item_num <= len(self._ocr_preview_record_ids)):
                continue
            record_id = self._ocr_preview_record_ids[item_num - 1]
            record = next((row for row in self._ocr_records if row.record_id == record_id), None)
            if record is None:
                continue
            corrected = self._strip_ocr_preview_item_header(str(request.get("text") or ""), item_num)
            if not corrected:
                continue
            try:
                self.controller.save_ocr_correction(
                    record_id=record.record_id,
                    corrected_text=corrected,
                    status="corrected",
                    ocr_text=record.ocr_text,
                    ocr_model_text=record.raw.get("ocr_model_text"),
                    golden_dir=Path(self.ocr_dir_var.get().strip() or self.controller.DEFAULT_OCR_GOLDEN_DIR),
                )
            except Exception as exc:
                self.ocr_status_var.set(f"No se pudo guardar OCR {item_num}: {exc}")
                continue
            record.corrected_text = corrected
            record.status = "corrected"
            record.raw["corrected_text"] = corrected
            record.raw["status"] = "corrected"
            self._update_ocr_row_status(record.record_id, "corrected")
            changed = True
        if changed:
            self.ocr_status_var.set("Correcciones guardadas desde el visor OCR.")
            self._push_ocr_latex_preview()
        if self.winfo_exists():
            self._ocr_preview_poll_after = self.after(350, self._poll_ocr_preview_edits)

    @staticmethod
    def _strip_ocr_preview_item_header(text: str, item_num: int) -> str:
        cleaned = re.sub(
            rf"^\s*\\item\s*\[\s*\\textbf\{{\s*{int(item_num)}\.?\s*\}}\s*\]\s*",
            "",
            str(text or ""),
            count=1,
            flags=re.IGNORECASE,
        )
        return re.sub(
            rf"^\s*\\textbf\{{OCR\s+{int(item_num)}\s*:[^}}]*\}}\s*",
            "",
            cleaned,
            count=1,
            flags=re.IGNORECASE,
        ).strip()

    def _open_ocr_images_preview(self) -> None:
        if not self._ocr_records:
            messagebox.showwarning("Golden OCR", "No hay imagenes OCR cargadas.")
            return
        records = self._ocr_records_for_selected_view()
        if not records:
            messagebox.showwarning("Golden OCR", "Selecciona una o varias imagenes OCR.")
            return
        top = tk.Toplevel(self)
        top.title(f"Imagenes OCR seleccionadas ({len(records)})")
        top.geometry("920x760")
        top.minsize(720, 520)
        toolbar = ttk.Frame(top, padding=(10, 8))
        toolbar.pack(fill="x")
        canvas = tk.Canvas(top, bg="#f8fafc", highlightthickness=0)
        scroll = ttk.Scrollbar(top, orient="vertical", command=canvas.yview)
        host = ttk.Frame(canvas)
        host.columnconfigure(0, weight=1)
        host_window = canvas.create_window((0, 0), window=host, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        page_size = 15
        page = {"value": 0}
        page_label = ttk.Label(toolbar, text="", style="Section.TLabel")
        page_label.pack(side="left", padx=(10, 0))

        def render_page() -> None:
            for child in host.winfo_children():
                child.destroy()
            photos: list = []
            start = page["value"] * page_size
            end = min(len(records), start + page_size)
            total_pages = max(1, (len(records) + page_size - 1) // page_size)
            page_label.configure(text=f"Pagina {page['value'] + 1}/{total_pages} | seleccionadas {start + 1}-{end} de {len(records)}")
            for row, idx in enumerate(range(start, end)):
                record = records[idx]
                source = record.source_label or Path(record.image_path).name or record.record_id
                card = ttk.Frame(host, style="Card.TFrame", padding=10)
                card.grid(row=row, column=0, sticky="ew", padx=10, pady=(10, 0))
                ttk.Label(card, text=f"OCR {idx + 1}: {source}", style="Section.TLabel").pack(anchor="w")
                image_path = Path(record.copied_image_path or record.image_path)
                label = ttk.Label(card, text="Imagen no encontrada", anchor="center")
                label.pack(fill="x", pady=(6, 0))
                if Image is not None and ImageTk is not None and image_path.exists():
                    try:
                        with Image.open(image_path) as img:
                            img.thumbnail((840, 560))
                            photo = ImageTk.PhotoImage(img.copy())
                        photos.append(photo)
                        label.configure(image=photo, text="")
                    except Exception:
                        pass
            top._ocr_image_photos = photos  # type: ignore[attr-defined]
            canvas.yview_moveto(0)
            top.after_idle(lambda: canvas.configure(scrollregion=canvas.bbox("all")))

        def move_page(delta: int) -> None:
            total_pages = max(1, (len(records) + page_size - 1) // page_size)
            page["value"] = max(0, min(total_pages - 1, page["value"] + delta))
            render_page()

        ttk.Button(toolbar, text="Anterior", command=lambda: move_page(-1), style="Ghost.TButton").pack(side="left")
        ttk.Button(toolbar, text="Siguiente", command=lambda: move_page(1), style="Ghost.TButton").pack(side="left", padx=(8, 0))
        host.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(host_window, width=event.width))

        def on_mousewheel(event) -> str:
            delta = -1 if event.delta > 0 else 1
            canvas.yview_scroll(delta * 3, "units")
            return "break"

        def bind_mousewheel(_event=None) -> None:
            canvas.bind_all("<MouseWheel>", on_mousewheel)

        def unbind_mousewheel(_event=None) -> None:
            canvas.unbind_all("<MouseWheel>")

        top.bind("<Enter>", bind_mousewheel)
        top.bind("<Leave>", unbind_mousewheel)
        top.protocol("WM_DELETE_WINDOW", lambda: (unbind_mousewheel(), top.destroy()))
        render_page()

    def _build_ocr_latex_block_preview(self, records: list[OcrGoldenRecord] | None = None) -> str:
        blocks: list[str] = []
        visible_records = records if records is not None else self._ocr_records_for_selected_view()
        for idx, record in enumerate(visible_records, start=1):
            source = record.source_label or Path(record.image_path).name or record.record_id
            body = str(record.corrected_text or record.ocr_text or "").strip()
            blocks.append(
                "\n".join(
                    [
                        f"\\item[\\textbf{{{idx}.}}] \\textbf{{OCR {idx}: {source}}}",
                        body,
                    ]
                ).strip()
            )
        return "\n\n".join(blocks).strip()

    def _open_ocr_items_editor(self) -> None:
        if not self._ocr_records:
            messagebox.showwarning("Golden OCR", "No hay items OCR cargados para editar.")
            return
        top = tk.Toplevel(self)
        top.title("Editor de items OCR")
        top.geometry("1080x720")
        top.minsize(860, 560)
        top.columnconfigure(1, weight=1)
        top.rowconfigure(0, weight=1)

        left = ttk.Frame(top, style="Card.TFrame", padding=8)
        left.grid(row=0, column=0, sticky="nsw", padx=(10, 5), pady=10)
        right = ttk.Frame(top, style="Card.TFrame", padding=10)
        right.grid(row=0, column=1, sticky="nsew", padx=(5, 10), pady=10)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)

        ttk.Label(left, text="Items OCR", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        item_list = tk.Listbox(left, width=42, height=28, exportselection=False)
        item_list.pack(side="left", fill="y")
        item_scroll = ttk.Scrollbar(left, orient="vertical", command=item_list.yview)
        item_scroll.pack(side="right", fill="y")
        item_list.configure(yscrollcommand=item_scroll.set)
        def item_label(index: int) -> str:
            record = self._ocr_records[index]
            source = record.source_label or Path(record.image_path).name or record.record_id
            return f"{index + 1:03d} | {record.status or '-'} | {source}"

        for idx in range(len(self._ocr_records)):
            item_list.insert("end", item_label(idx))

        title_var = tk.StringVar(value="")
        ttk.Label(right, textvariable=title_var, style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            right,
            text="Edita la correccion para entrenamiento. El OCR crudo original se conserva.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 6))
        text_box = tk.Text(right, wrap="word", bg="#ffffff", fg="#0f172a", relief="solid", bd=1)
        text_box.grid(row=2, column=0, sticky="nsew")
        actions = ttk.Frame(right, style="Card.TFrame")
        actions.grid(row=3, column=0, sticky="ew", pady=(8, 0))

        current_index = {"value": 0}

        def load_index(index: int) -> None:
            if not (0 <= index < len(self._ocr_records)):
                return
            current_index["value"] = index
            record = self._ocr_records[index]
            source = record.source_label or Path(record.image_path).name or record.record_id
            title_var.set(f"Item {index + 1}/{len(self._ocr_records)} | {source}")
            text_box.delete("1.0", "end")
            text_box.insert("1.0", record.corrected_text or record.ocr_text or "")
            item_list.selection_clear(0, "end")
            item_list.selection_set(index)
            item_list.see(index)

        def save_current() -> bool:
            index = current_index["value"]
            if not (0 <= index < len(self._ocr_records)):
                return False
            record = self._ocr_records[index]
            corrected = text_box.get("1.0", "end").strip()
            try:
                new_status = "corrected" if corrected else "pending"
                self.controller.save_ocr_correction(
                    record_id=record.record_id,
                    corrected_text=corrected,
                    status=new_status,
                    ocr_text=record.ocr_text,
                    ocr_model_text=record.raw.get("ocr_model_text"),
                    golden_dir=Path(self.ocr_dir_var.get().strip() or self.controller.DEFAULT_OCR_GOLDEN_DIR),
                )
                record.corrected_text = corrected
                record.status = new_status
                record.raw["corrected_text"] = corrected
                record.raw["status"] = new_status
                item_list.delete(index)
                item_list.insert(index, item_label(index))
                item_list.selection_set(index)
                self._update_ocr_row_status(record.record_id, new_status)
                self._push_ocr_latex_preview()
                return True
            except Exception as exc:
                messagebox.showerror("Golden OCR", f"No se pudo guardar el item OCR.\n{exc}")
                return False

        def move(delta: int) -> None:
            if save_current():
                load_index(max(0, min(len(self._ocr_records) - 1, current_index["value"] + delta)))

        def on_select(_event=None) -> None:
            selection = item_list.curselection()
            if selection:
                load_index(int(selection[0]))

        ttk.Button(actions, text="Anterior", command=lambda: move(-1), style="Ghost.TButton").pack(side="left")
        ttk.Button(actions, text="Guardar", command=save_current, style="Accent.TButton").pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Guardar y siguiente", command=lambda: move(1), style="Ghost.TButton").pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Cerrar", command=top.destroy, style="Ghost.TButton").pack(side="right")
        item_list.bind("<<ListboxSelect>>", on_select)
        load_index(0)

    def _scan_async(self) -> None:
        if self._scan_running:
            return
        raw_root = self.root_var.get().strip()
        if not raw_root:
            messagebox.showwarning("Auditor", "Elige una carpeta raiz para auditar.")
            return
        root = Path(raw_root)
        if not root.exists():
            messagebox.showerror("Auditor", f"La ruta no existe:\n{root}")
            return
        if not hasattr(self, "scan_btn") or not hasattr(self, "tree") or not hasattr(self, "detail_text"):
            messagebox.showinfo("Auditor", "La vista de sesiones fue retirada. Usa Golden OCR o Golden segmentos.")
            return

        self._scan_running = True
        self.scan_btn.state(["disabled"])
        self.status_var.set("Auditando sesiones...")
        self.summary_var.set("Esto puede tardar si la raiz contiene muchas carpetas.")
        self.tree.delete(*self.tree.get_children())
        self.detail_text.delete("1.0", "end")

        def worker() -> None:
            try:
                audits = self.controller.audit_root(root)
                self.after(0, lambda: self._on_scan_done(audits, None))
            except Exception as exc:
                self.after(0, lambda: self._on_scan_done([], exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_scan_done(self, audits: list[SessionTrainingAudit], error: Exception | None) -> None:
        self._scan_running = False
        self.scan_btn.state(["!disabled"])
        if error is not None:
            self.status_var.set("No se pudo completar la auditoria.")
            self.summary_var.set(str(error))
            messagebox.showerror("Auditor", f"No se pudo auditar.\n{error}")
            return
        self._audits = audits
        self._populate_table()
        ready = sum(1 for audit in audits if audit.status == "listo")
        review = sum(1 for audit in audits if audit.status == "revisar")
        blocked = sum(1 for audit in audits if audit.status == "bloqueado")
        self.status_var.set(f"Sesiones auditadas: {len(audits)}")
        self.summary_var.set(f"Listas: {ready} | Revisar: {review} | Bloqueadas: {blocked}")

    def _populate_table(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for idx, audit in enumerate(self._audits):
            self.tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    audit.status,
                    audit.global_score,
                    audit.segmentation_score,
                    audit.ocr_score,
                    audit.book_code or "-",
                    audit.instance_type or "-",
                    audit.items,
                    f"{audit.training_pairs_ready}/{audit.training_pairs}",
                    audit.segment_boxes or audit.manifest_segments,
                    len(audit.issues),
                ),
            )
        if self._audits:
            self.tree.selection_set("0")
            self._show_selected_detail()

    def _selected_audit(self) -> SessionTrainingAudit | None:
        selection = self.tree.selection()
        if not selection:
            return None
        try:
            index = int(selection[0])
        except Exception:
            return None
        if 0 <= index < len(self._audits):
            return self._audits[index]
        return None

    def _show_selected_detail(self) -> None:
        audit = self._selected_audit()
        self.detail_text.delete("1.0", "end")
        if audit is None:
            return
        lines = [
            f"Estado: {audit.status}",
            f"Puntaje global: {audit.global_score}",
            f"Segmentacion: {audit.segmentation_score}",
            f"OCR: {audit.ocr_score}",
            "",
            f"Proyecto: {audit.project or '-'}",
            f"Libro: {audit.book_code or '-'}",
            f"Instancia: {audit.instance_type or '-'}",
            f"Sesion: {audit.session_path}",
            "",
            "Metricas:",
            f"- Imagenes fuente: {audit.source_images} | faltantes: {audit.missing_source_images}",
            f"- Items finales: {audit.items} | corregidos: {audit.corrected_items}",
            f"- Vinculos imagen confirmados: {audit.image_bindings_confirmed} | por revisar: {audit.image_bindings_review}",
            f"- Segmentos/cajas: {audit.segment_boxes} | manifiestos: {audit.manifest_segments}",
            f"- OCR bruto: {audit.ocr_raw_blocks} | JSON estructurado: {audit.ocr_structured_blocks}",
            f"- Pares OCR: {audit.training_pairs_ready}/{audit.training_pairs} listos | imagen faltante: {audit.training_pairs_missing_image}",
            "",
            "Alertas:",
        ]
        if audit.issues:
            for issue in audit.issues:
                lines.append(f"- [{issue.level}] {issue.category}: {issue.message}")
        else:
            lines.append("- Sin alertas criticas para esta primera revision.")
        self.detail_text.insert("1.0", "\n".join(lines))

    def _export_json(self) -> None:
        self._export(kind="json")

    def _export_csv(self) -> None:
        self._export(kind="csv")

    def _export(self, *, kind: str) -> None:
        if not self._audits:
            messagebox.showwarning("Auditor", "No hay resultados para exportar.")
            return
        suffix = ".json" if kind == "json" else ".csv"
        path = filedialog.asksaveasfilename(
            title="Guardar reporte",
            defaultextension=suffix,
            filetypes=[("JSON", "*.json")] if kind == "json" else [("CSV", "*.csv")],
            initialfile=f"auditoria_entrenamiento{suffix}",
        )
        if not path:
            return
        try:
            if kind == "json":
                saved = self.controller.export_json(self._audits, Path(path))
            else:
                saved = self.controller.export_csv(self._audits, Path(path))
        except Exception as exc:
            messagebox.showerror("Auditor", f"No se pudo exportar.\n{exc}")
            return
        messagebox.showinfo("Auditor", f"Reporte guardado:\n{saved}")

    def _open_selected_folder(self) -> None:
        audit = self._selected_audit()
        if audit is None:
            return
        folder = Path(audit.session_path).parent
        try:
            subprocess.Popen(["explorer", str(folder)])
        except Exception as exc:
            messagebox.showerror("Auditor", f"No se pudo abrir la carpeta.\n{exc}")

    def _build_golden_async(self) -> None:
        if self._golden_running:
            return
        raw_root = self.root_var.get().strip()
        if not raw_root:
            messagebox.showwarning("Golden base", "Elige primero la raiz donde estan tus libros/sesiones.")
            return
        root = Path(raw_root)
        if not root.exists():
            messagebox.showerror("Golden base", f"La ruta no existe:\n{root}")
            return
        self._golden_running = True
        self.golden_status_var.set("Construyendo golden base global de segmentos...")
        self.golden_summary_var.set("Se copiaran los recortes a .cache/transcriptor_runs/datasets.")
        self.golden_tree.delete(*self.golden_tree.get_children())
        self.golden_detail_text.delete("1.0", "end")
        self.preview_label.configure(text="Procesando...", image="")
        self.crop_preview_label.configure(text="Procesando...", image="")

        def worker() -> None:
            try:
                out_dir = self.controller.build_segment_golden_base([root])
                records = self.controller.load_segment_golden_base(out_dir)
                self.after(0, lambda: self._on_golden_loaded(out_dir, records, None))
            except Exception as exc:
                self.after(0, lambda: self._on_golden_loaded(None, [], exc))

        threading.Thread(target=worker, daemon=True).start()

    def _import_problem_crops_into_segment_golden_async(self) -> None:
        if self._golden_running:
            return
        self._golden_running = True
        self.golden_status_var.set("Analizando recortes PDF para incorporar graficos internos...")
        self.golden_summary_var.set("La sincronizacion es incremental e incluye negativos sin grafico.")

        def progress(index: int, total: int, processed: int, positives: int, boxes: int) -> None:
            if index == 1 or index % 25 == 0 or index == total:
                self.after(
                    0,
                    lambda: self.golden_status_var.set(
                        f"Segmentacion PDF: {index}/{total} revisados | nuevos={processed} | positivos={positives} | boxes={boxes}"
                    ),
                )

        def worker() -> None:
            try:
                out_dir, processed, positives, boxes = self.controller.import_problem_crops_into_segment_golden(
                    progress_callback=progress
                )
                records = self.controller.load_segment_golden_base(out_dir)
                self.after(0, lambda: self._on_golden_loaded(out_dir, records, None))
                self.after(
                    0,
                    lambda: self.golden_status_var.set(
                        f"Recortes PDF incorporados: nuevos={processed} | positivos={positives} | boxes={boxes}"
                    ),
                )
            except Exception as exc:
                self.after(0, lambda: self._on_golden_loaded(None, [], exc))

        threading.Thread(target=worker, daemon=True).start()

    def _detect_selected_golden_segments_async(self) -> None:
        if self._golden_running:
            return
        sources = self._selected_golden_source_paths()
        if not sources:
            messagebox.showwarning(
                "Golden segmentos",
                "Selecciona una o varias filas con imagen fuente para ejecutar el modelo de segmentacion.",
            )
            return
        raw_dir = self.golden_dir_var.get().strip()
        golden_dir = Path(raw_dir) if raw_dir else self.controller.DEFAULT_SEGMENT_LIVE_DIR
        try:
            batch_size = int((os.getenv("SEGMENT_GOLDEN_BATCH_SIZE", "") or "25").strip())
        except Exception:
            batch_size = 25
        batch_size = max(1, min(100, batch_size))
        try:
            batch_pause = float((os.getenv("SEGMENT_GOLDEN_BATCH_PAUSE", "") or "0.8").strip())
        except Exception:
            batch_pause = 0.8
        batch_pause = max(0.0, min(10.0, batch_pause))
        batches = [sources[index : index + batch_size] for index in range(0, len(sources), batch_size)]
        self._golden_running = True
        self.golden_status_var.set(
            f"Ejecutando modelo en {len(sources)} imagen(es), por lotes de {batch_size}..."
        )
        self.golden_summary_var.set(
            "Se ignorara el cache para estas fuentes; cada lote actualiza la Golden Segmentos cargada."
        )

        def worker() -> None:
            out_dir = golden_dir
            processed_total = 0
            positives_total = 0
            boxes_total = 0
            errors: list[str] = []
            try:
                for batch_index, batch in enumerate(batches, start=1):
                    batch_label = f"{batch_index}/{len(batches)}"
                    already_processed = processed_total
                    self.after(
                        0,
                        lambda label=batch_label, done=processed_total: self.golden_status_var.set(
                            f"Modelo segmentos: iniciando lote {label} | avance={done}/{len(sources)}"
                        ),
                    )

                    def progress(index: int, total: int, processed: int, positives: int, boxes: int) -> None:
                        overall = already_processed + index
                        if index == 1 or index % 5 == 0 or index == total:
                            self.after(
                                0,
                                lambda label=batch_label, overall=overall, processed=processed, positives=positives, boxes=boxes: self.golden_status_var.set(
                                    f"Lote {label}: {index}/{total} | total={overall}/{len(sources)} | "
                                    f"lote procesadas={processed} positivas={positives} boxes={boxes}"
                                ),
                            )

                    try:
                        batch_out, processed, positives, boxes = self.controller.run_segment_model_on_sources(
                            batch,
                            golden_dir=golden_dir,
                            progress_callback=progress,
                        )
                        out_dir = batch_out
                        processed_total += processed
                        positives_total += positives
                        boxes_total += boxes
                    except Exception as exc:
                        errors.append(f"Lote {batch_label}: {exc}")
                        self.after(
                            0,
                            lambda label=batch_label, exc=exc: self.golden_status_var.set(
                                f"Error en lote {label}; se continua con el siguiente. Detalle: {exc}"
                            ),
                        )

                    if batch_index < len(batches) and batch_pause > 0:
                        time.sleep(batch_pause)

                records = self.controller.load_segment_golden_base(out_dir)
                self.after(0, lambda: self._on_golden_loaded(out_dir, records, None))
                self.after(
                    0,
                    lambda: self.golden_status_var.set(
                        f"Modelo aplicado por lotes: imagenes={processed_total} | "
                        f"con grafico={positives_total} | boxes={boxes_total} | errores={len(errors)}"
                    ),
                )
                if errors:
                    preview = "\n".join(errors[:5])
                    self.after(
                        0,
                        lambda preview=preview, total=len(errors): messagebox.showwarning(
                            "Golden segmentos",
                            f"La cola termino con {total} lote(s) con error.\n\nPrimeros errores:\n{preview}",
                        ),
                    )
            except Exception as exc:
                self.after(0, lambda: self._on_golden_loaded(None, [], exc))

        threading.Thread(target=worker, daemon=True).start()

    def _choose_golden_dir(self) -> None:
        initial = self.golden_dir_var.get().strip() or str(self.controller.DEFAULT_GOLDEN_ROOT)
        path = filedialog.askdirectory(
            title="Elegir carpeta golden base",
            initialdir=initial if Path(initial).exists() else None,
        )
        if path:
            self._load_golden_dir(Path(path))

    def _load_latest_golden(self) -> None:
        latest = self.controller.find_latest_golden_base()
        if latest is None:
            messagebox.showwarning("Golden base", "No encontre una golden base previa en .cache/transcriptor_runs/datasets.")
            return
        self._load_golden_dir(latest)

    def _autoload_latest_golden_base(self) -> None:
        latest = self.controller.find_latest_golden_base()
        if latest is None:
            self.golden_status_var.set("No hay golden base previa cargada.")
            self.golden_summary_var.set("Usa 'Crear desde raiz' para construir la primera base global de segmentos.")
            return
        self.golden_status_var.set("Cargando ultima golden base disponible...")
        self.golden_dir_var.set(str(latest))
        self._load_golden_dir(latest)

    def _load_golden_dir(self, golden_dir: Path) -> None:
        try:
            records = self.controller.load_segment_golden_base(golden_dir)
        except Exception as exc:
            messagebox.showerror("Golden base", f"No se pudo cargar la golden base.\n{exc}")
            return
        self._on_golden_loaded(golden_dir, records, None)

    def _on_golden_loaded(
        self,
        golden_dir: Path | None,
        records: list[SegmentGoldenRecord],
        error: Exception | None,
    ) -> None:
        self._golden_running = False
        if error is not None:
            self.golden_status_var.set("No se pudo construir/cargar la golden base.")
            self.golden_summary_var.set(str(error))
            messagebox.showerror("Golden base", f"No se pudo preparar la golden base.\n{error}")
            return
        self._golden_records = records
        if golden_dir is not None:
            self.golden_dir_var.set(str(golden_dir))
        self._populate_golden_table()
        summary = self.controller.summarize_segment_records(records)
        self.golden_status_var.set(f"Segmentos en golden base: {summary['total']}")
        self.golden_summary_var.set(
            f"Libros: {summary['libros']} | Confirmados: {summary['confirmados']} | "
            f"Sin vinculo: {summary['sin_vinculo']} | Imagenes faltantes: {summary['imagenes_faltantes']}"
        )

    def _prepare_hf_dataset_async(self) -> None:
        raw_dir = self.golden_dir_var.get().strip()
        if not raw_dir:
            messagebox.showwarning("Hugging Face", "Carga primero una golden base.")
            return
        golden_dir = Path(raw_dir)
        if not golden_dir.exists():
            messagebox.showerror("Hugging Face", f"No existe la golden base:\n{golden_dir}")
            return
        self.golden_status_var.set("Preparando dataset YOLO para Hugging Face...")
        self.golden_summary_var.set("Creando images/train-val-test, labels y dataset.yaml.")

        def worker() -> None:
            try:
                out_dir = self.controller.prepare_huggingface_yolo_dataset(golden_dir=golden_dir)
                self.after(0, lambda: self._on_hf_dataset_ready(out_dir, None))
            except Exception as exc:
                self.after(0, lambda: self._on_hf_dataset_ready(None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_hf_dataset_ready(self, out_dir: Path | None, error: Exception | None) -> None:
        if error is not None:
            self.golden_status_var.set("No se pudo preparar el dataset para Hugging Face.")
            self.golden_summary_var.set(str(error))
            messagebox.showerror("Hugging Face", f"No se pudo preparar el dataset.\n{error}")
            return
        if out_dir is None:
            return
        try:
            manifest = self.controller._load_json(out_dir / "manifest.json")
        except Exception:
            manifest = {}
        images = manifest.get("images_total", "-")
        boxes = manifest.get("boxes_total", "-")
        self.golden_status_var.set(f"Dataset HF preparado: {out_dir}")
        self.golden_summary_var.set(f"Imagenes: {images} | Boxes: {boxes} | Archivo: dataset.yaml")
        messagebox.showinfo(
            "Hugging Face",
            f"Dataset YOLO preparado:\n{out_dir}\n\nImagenes: {images}\nBoxes: {boxes}",
        )
        self._open_path(out_dir)

    def _populate_golden_table(self) -> None:
        self.golden_tree.delete(*self.golden_tree.get_children())
        for idx, record in enumerate(self._golden_records):
            image_name = Path(record.display_image_path).name if record.display_image_path else "-"
            self.golden_tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    record.status,
                    record.split or "-",
                    record.book_code or "-",
                    record.instance_type or "-",
                    record.source_stem or "-",
                    record.segment_idx if record.segment_idx is not None else "-",
                    record.item_num if record.item_num is not None else "-",
                    record.slot or "-",
                    image_name,
                ),
            )
        if self._golden_records:
            self.golden_tree.selection_set("0")
            self._show_selected_golden_detail()

    def _selected_golden_record(self) -> SegmentGoldenRecord | None:
        selection = self.golden_tree.selection()
        if not selection:
            return None
        try:
            index = int(selection[0])
        except Exception:
            return None
        if 0 <= index < len(self._golden_records):
            return self._golden_records[index]
        return None

    def _selected_golden_records(self) -> list[SegmentGoldenRecord]:
        records: list[SegmentGoldenRecord] = []
        for item_id in self.golden_tree.selection():
            try:
                index = int(item_id)
            except Exception:
                continue
            if 0 <= index < len(self._golden_records):
                records.append(self._golden_records[index])
        return records

    def _selected_golden_source_paths(self) -> list[Path]:
        seen: set[str] = set()
        sources: list[Path] = []
        for record in self._selected_golden_records():
            if not record.source_path:
                continue
            try:
                path = Path(record.source_path).expanduser().resolve()
            except Exception:
                continue
            key = str(path).lower()
            if key in seen or not path.exists() or not path.is_file():
                continue
            seen.add(key)
            sources.append(path)
        return sources

    def _show_selected_golden_detail(self) -> None:
        record = self._selected_golden_record()
        self.golden_detail_text.delete("1.0", "end")
        if record is None:
            self.preview_label.configure(text="Sin vista previa", image="")
            self.crop_preview_label.configure(text="Sin recorte", image="")
            return
        lines = [
            f"ID: {record.record_id}",
            f"Estado: {record.status}",
            f"Libro: {record.book_code or '-'}",
            f"Instancia: {record.instance_type or '-'}",
            f"Fuente: {record.source_stem or '-'} | segmento: {record.segment_idx or '-'}",
            f"Box px: {record.segment_bbox_px or '-'}",
            f"Item: {record.item_num or '-'} | slot: {record.slot or '-'} | marcador: {record.marker_name or '-'}",
            f"Curso/Tema: {record.curso or '-'} / {record.tema or '-'}",
            f"Imagen fuente: {record.source_path or '-'}",
            f"Imagen: {record.display_image_path or '-'}",
            f"Sesion: {record.session_json or '-'}",
            "",
            record.debug_statement or record.item_text or "",
        ]
        self.golden_detail_text.insert("1.0", "\n".join(lines))
        self._render_golden_preview(record)

    def _render_golden_preview(self, record: SegmentGoldenRecord) -> None:
        source_path = Path(record.source_path) if record.source_path else Path("")
        crop_path = Path(record.display_image_path) if record.display_image_path else Path("")
        if not source_path.exists() and not crop_path.exists():
            self.preview_label.configure(text="Imagen fuente no encontrada", image="")
            self.crop_preview_label.configure(text="Recorte no encontrado", image="")
            self._preview_photo = None
            self._crop_photo = None
            return
        if Image is None or ImageTk is None:
            self.preview_label.configure(text=str(source_path or crop_path), image="")
            self.crop_preview_label.configure(text=str(crop_path), image="")
            self._preview_photo = None
            self._crop_photo = None
            return
        try:
            if source_path.exists():
                img = Image.open(source_path).convert("RGB")
                self._draw_segment_box(img, record.segment_bbox_px)
                img.thumbnail((430, 300))
                self._preview_photo = ImageTk.PhotoImage(img)
                self.preview_label.configure(image=self._preview_photo, text="")
            else:
                self.preview_label.configure(text="Imagen fuente no encontrada", image="")
                self._preview_photo = None

            if crop_path.exists():
                crop = Image.open(crop_path).convert("RGB")
                crop.thumbnail((280, 300))
                self._crop_photo = ImageTk.PhotoImage(crop)
                self.crop_preview_label.configure(image=self._crop_photo, text="")
            else:
                self.crop_preview_label.configure(text="Recorte no encontrado", image="")
                self._crop_photo = None
        except Exception:
            self.preview_label.configure(text=str(source_path or crop_path), image="")
            self.crop_preview_label.configure(text=str(crop_path), image="")
            self._preview_photo = None
            self._crop_photo = None

    def _draw_segment_box(self, img, bbox: list[float]) -> None:
        if ImageDraw is None or len(bbox) < 4:
            return
        x1, y1, x2, y2 = bbox[:4]
        draw = ImageDraw.Draw(img)
        width = max(3, round(min(img.size) * 0.006))
        for offset in range(width):
            draw.rectangle((x1 - offset, y1 - offset, x2 + offset, y2 + offset), outline=(220, 38, 38))

    def _open_selected_golden_image(self) -> None:
        record = self._selected_golden_record()
        if record is None or not record.display_image_path:
            return
        self._open_path(Path(record.display_image_path))

    def _open_selected_golden_folder(self) -> None:
        record = self._selected_golden_record()
        if record is None or not record.display_image_path:
            return
        self._open_path(Path(record.display_image_path).parent)

    def _open_selected_golden_source(self) -> None:
        record = self._selected_golden_record()
        if record is None or not record.source_path:
            return
        self._open_path(Path(record.source_path))

    def _open_selected_golden_session(self) -> None:
        record = self._selected_golden_record()
        if record is None or not record.session_json:
            return
        self._open_path(Path(record.session_json).parent)

    def _open_selected_source_segment_editor(self) -> None:
        record = self._selected_golden_record()
        if record is None or not record.source_path:
            messagebox.showwarning("Segmentacion V2", "Selecciona un segmento con imagen fuente.")
            return
        self._open_segment_review_editor(Path(record.source_path))

    def _records_for_source(self, source_path: Path) -> list[SegmentGoldenRecord]:
        try:
            source_key = str(Path(source_path).expanduser().resolve()).lower()
        except Exception:
            source_key = str(Path(source_path)).lower()
        records: list[SegmentGoldenRecord] = []
        for record in self._golden_records:
            if not record.source_path or not record.segment_bbox_px:
                continue
            try:
                record_key = str(Path(record.source_path).expanduser().resolve()).lower()
            except Exception:
                record_key = str(Path(record.source_path)).lower()
            if record_key == source_key:
                records.append(record)
        return records

    def _source_order(self) -> list[Path]:
        seen: set[str] = set()
        out: list[Path] = []
        for record in self._golden_records:
            if not record.source_path:
                continue
            key = str(Path(record.source_path)).lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(Path(record.source_path))
        return out

    def _adjacent_source(self, source_path: Path, direction: int) -> Path | None:
        sources = self._source_order()
        if not sources:
            return None
        key = str(Path(source_path)).lower()
        index = next((idx for idx, path in enumerate(sources) if str(path).lower() == key), -1)
        if index < 0:
            return None
        next_index = index + direction
        if 0 <= next_index < len(sources):
            return sources[next_index]
        return None

    def _open_segment_review_editor(self, source_path: Path) -> None:
        if Image is None or ImageTk is None:
            messagebox.showwarning("Segmentacion V2", "Falta Pillow para abrir el editor de segmentos.")
            return
        source_path = Path(source_path)
        if not source_path.exists():
            messagebox.showerror("Segmentacion V2", f"No existe la imagen fuente:\n{source_path}")
            return

        records = self._records_for_source(source_path)
        boxes: list[list[float]] = [list(record.segment_bbox_px[:4]) for record in records if len(record.segment_bbox_px) >= 4]
        pending_key = str(source_path.resolve()).lower()
        if pending_key in self._segment_editor_pending:
            boxes = [list(box) for box in self._segment_editor_pending[pending_key]]
        boxes.sort(key=lambda box: (box[1], box[0], box[3], box[2]))

        top = self._segment_editor_window
        if top is None or not bool(top.winfo_exists()):
            top = tk.Toplevel(self)
            top.geometry("1180x780")
            top.minsize(920, 620)
            self._segment_editor_window = top
        for child in list(top.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass
        top.title(f"Editor Segmentacion V2 - {source_path.name}")

        root = ttk.Frame(top, padding=10)
        root.pack(fill="both", expand=True)
        info_var = tk.StringVar(value="")
        ttk.Label(root, textvariable=info_var, style="Section.TLabel").pack(anchor="w", pady=(0, 8))

        frame = ttk.Frame(root)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        canvas = tk.Canvas(frame, bg="#f8fafc", highlightthickness=1, highlightbackground="#cbd5e1")
        xsb = ttk.Scrollbar(frame, orient="horizontal", command=canvas.xview)
        ysb = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        canvas.configure(xscrollcommand=xsb.set, yscrollcommand=ysb.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")

        controls = ttk.Frame(root)
        controls.pack(fill="x", pady=(8, 0))

        try:
            img = Image.open(source_path).convert("RGB")
        except Exception as exc:
            messagebox.showerror("Segmentacion V2", f"No se pudo abrir la fuente.\n{exc}")
            return
        ow, oh = img.size
        max_w = max(760, int(self.winfo_screenwidth() or 1280) - 280)
        max_h = max(480, int(self.winfo_screenheight() or 900) - 350)
        scale = min(max_w / max(1, ow), max_h / max(1, oh), 1.0)
        dw = max(1, int(round(ow * scale)))
        dh = max(1, int(round(oh * scale)))
        display_img = img.resize((dw, dh)) if scale != 1.0 else img.copy()
        self._segment_editor_photo = ImageTk.PhotoImage(display_img)
        canvas.create_image(0, 0, anchor="nw", image=self._segment_editor_photo)
        canvas.configure(scrollregion=(0, 0, dw, dh))

        ctx = {
            "source_path": source_path,
            "boxes": boxes,
            "selected": 0 if boxes else -1,
            "mode": "",
            "edge": (False, False, False, False),
            "start_xy": (0.0, 0.0),
            "start_box": None,
            "dirty": False,
        }

        def to_canvas(x: float, y: float) -> tuple[float, float]:
            return (x * scale, y * scale)

        def to_image(cx: float, cy: float) -> tuple[float, float]:
            return (cx / max(scale, 1e-8), cy / max(scale, 1e-8))

        def clean_box(box: list[float]) -> list[float]:
            x1, y1, x2, y2 = [float(v) for v in box[:4]]
            x1 = max(0.0, min(float(ow - 1), min(x1, x2)))
            y1 = max(0.0, min(float(oh - 1), min(y1, y2)))
            x2 = max(x1 + 18.0, min(float(ow), max(x1, x2)))
            y2 = max(y1 + 18.0, min(float(oh), max(y1, y2)))
            return [x1, y1, x2, y2]

        def draw() -> None:
            canvas.delete("box")
            for idx, box in enumerate(ctx["boxes"]):
                x1, y1, x2, y2 = clean_box(box)
                cx1, cy1 = to_canvas(x1, y1)
                cx2, cy2 = to_canvas(x2, y2)
                selected = idx == int(ctx["selected"])
                color = "#2563eb" if selected else "#dc2626"
                canvas.create_rectangle(cx1, cy1, cx2, cy2, outline=color, width=3 if selected else 2, tags=("box",))
                canvas.create_text(cx1 + 8, max(12, cy1 - 9), text=f"seg-{idx + 1}", fill=color, anchor="w", tags=("box",))
                if selected:
                    handle = max(5, int(round(6 * scale)))
                    for hx, hy in ((cx1, cy1), (cx2, cy1), (cx1, cy2), (cx2, cy2)):
                        canvas.create_rectangle(
                            hx - handle,
                            hy - handle,
                            hx + handle,
                            hy + handle,
                            outline=color,
                            fill=color,
                            tags=("box",),
                        )
            count = len(ctx["boxes"])
            selected = int(ctx["selected"])
            dirty = " | cambios pendientes" if bool(ctx.get("dirty")) else ""
            if 0 <= selected < count:
                b = [int(round(v)) for v in clean_box(ctx["boxes"][selected])]
                info_var.set(f"{source_path.name} | segmentos={count} | seleccionado=seg-{selected + 1} bbox={tuple(b)}{dirty}")
            else:
                info_var.set(f"{source_path.name} | segmentos={count}{dirty}")

        def hit_test(ix: float, iy: float) -> tuple[int, tuple[bool, bool, bool, bool]]:
            tolerance = max(8.0, 12.0 / max(scale, 1e-8))
            for idx in range(len(ctx["boxes"]) - 1, -1, -1):
                x1, y1, x2, y2 = clean_box(ctx["boxes"][idx])
                if x1 <= ix <= x2 and y1 <= iy <= y2:
                    left = abs(ix - x1) <= tolerance
                    right = abs(ix - x2) <= tolerance
                    top_edge = abs(iy - y1) <= tolerance
                    bottom = abs(iy - y2) <= tolerance
                    return idx, (left, right, top_edge, bottom)
            return -1, (False, False, False, False)

        def event_xy(event) -> tuple[float, float]:
            return (float(canvas.canvasx(event.x)), float(canvas.canvasy(event.y)))

        def on_down(event) -> None:
            cx, cy = event_xy(event)
            ix, iy = to_image(cx, cy)
            idx, edge = hit_test(ix, iy)
            ctx["selected"] = idx
            if idx >= 0:
                ctx["mode"] = "resize" if any(edge) else "move"
                ctx["edge"] = edge
                ctx["start_xy"] = (ix, iy)
                ctx["start_box"] = list(ctx["boxes"][idx])
            else:
                ctx["mode"] = ""
                ctx["edge"] = (False, False, False, False)
                ctx["start_box"] = None
            draw()

        def on_drag(event) -> None:
            if ctx.get("mode") not in {"move", "resize"}:
                return
            idx = int(ctx["selected"])
            if idx < 0 or idx >= len(ctx["boxes"]):
                return
            cx, cy = event_xy(event)
            ix, iy = to_image(cx, cy)
            sx, sy = ctx["start_xy"]
            start = list(ctx["start_box"] or ctx["boxes"][idx])
            dx, dy = ix - sx, iy - sy
            x1, y1, x2, y2 = start
            if ctx.get("mode") == "move":
                bw, bh = x2 - x1, y2 - y1
                nx1 = max(0.0, min(float(ow) - bw, x1 + dx))
                ny1 = max(0.0, min(float(oh) - bh, y1 + dy))
                ctx["boxes"][idx] = clean_box([nx1, ny1, nx1 + bw, ny1 + bh])
            else:
                left, right, top_edge, bottom = ctx.get("edge", (False, False, False, False))
                nx1, ny1, nx2, ny2 = x1, y1, x2, y2
                if left:
                    nx1 = x1 + dx
                if right:
                    nx2 = x2 + dx
                if top_edge:
                    ny1 = y1 + dy
                if bottom:
                    ny2 = y2 + dy
                ctx["boxes"][idx] = clean_box([nx1, ny1, nx2, ny2])
            ctx["dirty"] = True
            draw()

        def on_up(_event) -> None:
            ctx["mode"] = ""
            ctx["edge"] = (False, False, False, False)
            ctx["start_box"] = None

        def add_box() -> None:
            margin = 40.0
            new_box = clean_box([margin, margin, min(float(ow), margin + 360), min(float(oh), margin + 220)])
            ctx["boxes"].append(new_box)
            ctx["selected"] = len(ctx["boxes"]) - 1
            ctx["dirty"] = True
            draw()

        def delete_box() -> None:
            idx = int(ctx["selected"])
            if 0 <= idx < len(ctx["boxes"]):
                ctx["boxes"].pop(idx)
                ctx["selected"] = min(idx, len(ctx["boxes"]) - 1)
                ctx["dirty"] = True
            draw()

        def current_clean_boxes() -> list[tuple[int, int, int, int]]:
            clean_boxes = [
                tuple(int(round(v)) for v in clean_box(box))
                for box in ctx["boxes"]
            ]
            return sorted(clean_boxes, key=lambda box: (box[1], box[0], box[3], box[2]))

        def remember_current() -> None:
            self._segment_editor_pending[str(source_path.resolve()).lower()] = current_clean_boxes()
            ctx["dirty"] = True
            draw()

        def save_confirmed(*, show_message: bool = True) -> None:
            remember_current()
            self._persist_segment_boxes(source_path, self._segment_editor_pending[str(source_path.resolve()).lower()])
            ctx["dirty"] = False
            if not show_message:
                draw()
                return
            messagebox.showinfo(
                "Segmentacion V2",
                "Fuente confirmada y Golden Segmentos actualizada.",
                parent=top,
            )
            draw()

        def save_all_pending() -> None:
            remember_current()
            total = 0
            for raw_path, pending_boxes in list(self._segment_editor_pending.items()):
                path = Path(raw_path)
                if not path.exists():
                    continue
                self._persist_segment_boxes(path, pending_boxes, reload_after=False)
                total += 1
            ctx["dirty"] = False
            self._reload_golden_after_segment_update(source_path)
            messagebox.showinfo(
                "Segmentacion V2",
                f"Guardado final completado: {total} fuente(s) persistidas y Golden Segmentos actualizada.",
                parent=top,
            )
            draw()

        def nudge_selected(dx: float = 0.0, dy: float = 0.0, grow_dx: float = 0.0, grow_dy: float = 0.0) -> None:
            idx = int(ctx["selected"])
            if idx < 0 or idx >= len(ctx["boxes"]):
                return
            x1, y1, x2, y2 = clean_box(ctx["boxes"][idx])
            ctx["boxes"][idx] = clean_box([x1 + dx, y1 + dy, x2 + dx + grow_dx, y2 + dy + grow_dy])
            ctx["dirty"] = True
            draw()

        def load_adjacent(direction: int) -> None:
            save = messagebox.askyesnocancel(
                "Segmentacion V2",
                "¿Quieres guardar/confirmar esta fuente antes de cambiar?",
                parent=top,
            )
            if save is None:
                return
            if save:
                save_confirmed()
            adjacent = self._adjacent_source(source_path, direction)
            if adjacent is None:
                messagebox.showinfo("Segmentacion V2", "No hay mas fuentes en esa direccion.", parent=top)
                return
            self._open_segment_review_editor(adjacent)

        def load_adjacent_without_saving_to_disk(direction: int) -> None:
            remember_current()
            adjacent = self._adjacent_source(source_path, direction)
            if adjacent is None:
                messagebox.showinfo("Segmentacion V2", "No hay mas fuentes en esa direccion.", parent=top)
                return
            self._open_segment_review_editor(adjacent)

        ttk.Label(
            root,
            text="Atajos: N nuevo | Del eliminar | A/S anterior/siguiente | Flechas mover | Shift+flechas redimensionar | +/- crecer/reducir | Ctrl+S guardar todo",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(6, 0))

        ttk.Button(controls, text="Confirmar fuente", command=save_confirmed, style="Ghost.TButton").pack(side="left")
        ttk.Button(controls, text="Guardar todo al final", command=save_all_pending, style="Accent.TButton").pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Agregar box (N)", command=add_box, style="Ghost.TButton").pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Eliminar (Del)", command=delete_box, style="Secondary.TButton").pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Anterior (A)", command=lambda: load_adjacent_without_saving_to_disk(-1), style="Ghost.TButton").pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Siguiente (S)", command=lambda: load_adjacent_without_saving_to_disk(1), style="Ghost.TButton").pack(side="left", padx=(8, 0))

        canvas.bind("<Button-1>", on_down)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_up)
        top.bind("<Delete>", lambda _e: delete_box())
        top.bind("<KeyPress-n>", lambda _e: add_box())
        top.bind("<KeyPress-N>", lambda _e: add_box())
        top.bind("<KeyPress-a>", lambda _e: load_adjacent_without_saving_to_disk(-1))
        top.bind("<KeyPress-A>", lambda _e: load_adjacent_without_saving_to_disk(-1))
        top.bind("<KeyPress-s>", lambda _e: load_adjacent_without_saving_to_disk(1))
        top.bind("<KeyPress-S>", lambda _e: load_adjacent_without_saving_to_disk(1))
        top.bind("<Control-s>", lambda _e: save_all_pending())
        top.bind("<Control-S>", lambda _e: save_all_pending())
        top.bind("<Left>", lambda _e: nudge_selected(dx=-5))
        top.bind("<Right>", lambda _e: nudge_selected(dx=5))
        top.bind("<Up>", lambda _e: nudge_selected(dy=-5))
        top.bind("<Down>", lambda _e: nudge_selected(dy=5))
        top.bind("<Shift-Left>", lambda _e: nudge_selected(grow_dx=-8))
        top.bind("<Shift-Right>", lambda _e: nudge_selected(grow_dx=8))
        top.bind("<Shift-Up>", lambda _e: nudge_selected(grow_dy=-8))
        top.bind("<Shift-Down>", lambda _e: nudge_selected(grow_dy=8))
        top.bind("<plus>", lambda _e: nudge_selected(dx=-4, dy=-4, grow_dx=8, grow_dy=8))
        top.bind("<KP_Add>", lambda _e: nudge_selected(dx=-4, dy=-4, grow_dx=8, grow_dy=8))
        top.bind("<minus>", lambda _e: nudge_selected(dx=4, dy=4, grow_dx=-8, grow_dy=-8))
        top.bind("<KP_Subtract>", lambda _e: nudge_selected(dx=4, dy=4, grow_dx=-8, grow_dy=-8))
        top.protocol("WM_DELETE_WINDOW", lambda: (remember_current(), top.destroy()))
        draw()

    def _persist_segment_boxes(
        self,
        source_path: Path,
        boxes: list[tuple[int, int, int, int]],
        *,
        reload_after: bool = True,
    ) -> None:
        from modulos.modulo0_transcriptor.segmentador_v2 import SegmentadorProblemasV2

        out_root = self._segments_out_root_for_source(source_path)
        raw_golden_dir = self.golden_dir_var.get().strip()
        previous_live = os.environ.get("SEGMENT_LIVE_GOLDEN_BASE")
        if raw_golden_dir:
            os.environ["SEGMENT_LIVE_GOLDEN_BASE"] = str(Path(raw_golden_dir).expanduser().resolve())
        try:
            segmentador = SegmentadorProblemasV2(out_root)
            clean_boxes = segmentador._sort_boxes_reading_order(list(boxes or []))
            payload = {
                "detector_source": "golden_manual_review",
                "review_status": "confirmed",
                "final_boxes": [{"bbox_px": [int(v) for v in box], "conf": 1.0} for box in clean_boxes],
                "diagram_presence_label": "yes" if clean_boxes else "no",
                "diagram_presence_source": "golden_manual_review",
            }
            segmentador._save_segments_from_boxes(source_path, clean_boxes, detector_payload=payload)
        finally:
            if raw_golden_dir:
                if previous_live is None:
                    os.environ.pop("SEGMENT_LIVE_GOLDEN_BASE", None)
                else:
                    os.environ["SEGMENT_LIVE_GOLDEN_BASE"] = previous_live
        if reload_after:
            self._reload_golden_after_segment_update(source_path)

    def _reload_golden_after_segment_update(self, source_path: Path | None = None) -> None:
        raw_dir = self.golden_dir_var.get().strip()
        if not raw_dir:
            return
        golden_dir = Path(raw_dir)
        try:
            records = self.controller.load_segment_golden_base(golden_dir)
        except Exception as exc:
            self.golden_summary_var.set(f"Guardado realizado, pero no se pudo recargar la tabla: {exc}")
            return
        self._golden_records = records
        self._populate_golden_table()
        summary = self.controller.summarize_segment_records(records)
        self.golden_status_var.set(f"Segmentos en golden base: {summary['total']}")
        self.golden_summary_var.set(
            f"Libros: {summary['libros']} | Confirmados: {summary['confirmados']} | "
            f"Sin vinculo: {summary['sin_vinculo']} | Imagenes faltantes: {summary['imagenes_faltantes']}"
        )
        if source_path is None:
            return
        try:
            source_key = str(Path(source_path).expanduser().resolve()).lower()
        except Exception:
            source_key = str(Path(source_path)).lower()
        for index, record in enumerate(self._golden_records):
            try:
                record_key = str(Path(record.source_path).expanduser().resolve()).lower()
            except Exception:
                record_key = str(Path(record.source_path)).lower()
            if record_key == source_key:
                item_id = str(index)
                self.golden_tree.selection_set(item_id)
                self.golden_tree.see(item_id)
                self._show_selected_golden_detail()
                break

    def _segments_out_root_for_source(self, source_path: Path) -> Path:
        records = self._records_for_source(source_path)
        for record in records:
            raw_segment_path = str(record.raw.get("segment_image_path") or "").strip()
            if not raw_segment_path:
                continue
            segment_path = Path(raw_segment_path)
            if segment_path.parent.name == Path(source_path).stem:
                return segment_path.parent.parent
            if segment_path.parent.exists():
                return segment_path.parent.parent
        return Path(source_path).parent.parent / "segments"

    def _open_path(self, path: Path) -> None:
        try:
            if path.exists() and path.is_file():
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["explorer", str(path)])
        except Exception as exc:
            messagebox.showerror("Auditor", f"No se pudo abrir:\n{path}\n\n{exc}")
