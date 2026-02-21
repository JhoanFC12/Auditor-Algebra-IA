from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import shutil
import threading
import time
import traceback
import tkinter as tk
import webbrowser
from pathlib import Path
from datetime import datetime
import tempfile
import unicodedata
import uuid
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Dict, List, Optional, Set, Tuple

from openai import OpenAI

from .controlador_transcriptor import TranscriptorController
from .latex_normalizer import (
    normalize_option as normalize_latex_option,
    normalize_scan_item_text as normalize_latex_scan_item_text,
    normalize_statement as normalize_latex_statement,
)
from .scan_pipeline.key_classifier import classify_key_image, classify_key_text
from .scan_pipeline.pipeline import ScanPipeline
from .scan_pipeline.prompts import build_extract_prompt
from .segmentador_v2 import SegmentadorProblemasV2, SegmentoProblemaV2
from utils.env_validation import validate_scan_provider_env
from utils.preview_window import PreviewWindow
from utils.styles import apply_openai_theme

try:
    from tkinterdnd2 import DND_FILES  # type: ignore

    DND_AVAILABLE = True
    DND_IMPORT_ERROR = ""
except Exception as exc:
    DND_FILES = None
    DND_AVAILABLE = False
    DND_IMPORT_ERROR = str(exc)


def _load_dotenv_if_present() -> None:
    """
    Load variables from `.env.local` (preferred) or `.env` without overriding
    existing process env vars.
    """
    candidates: List[Path] = []
    try:
        repo_root = Path(__file__).resolve().parents[2]
        candidates.append(repo_root / ".env.local")
        candidates.append(repo_root / ".env")
    except Exception:
        pass
    try:
        candidates.append(Path.cwd() / ".env.local")
        candidates.append(Path.cwd() / ".env")
    except Exception:
        pass

    env_path = next((p for p in candidates if p.exists()), None)
    if env_path is None:
        return

    try:
        try:
            text = env_path.read_text(encoding="utf-8")
        except Exception:
            # Windows editors may leave BOM; keep load resilient.
            text = env_path.read_text(encoding="utf-8-sig")

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        return


_load_dotenv_if_present()


PROVIDER_OPENAI = "OpenAI Vision"
PROVIDER_HF = "HuggingFace Vision"
PROVIDER_OCR = "OCR Local (Tesseract)"
TAG_MODE_MANUAL = "Manual"
TAG_MODE_AUTO = "Auto (IA)"
TAG_MODE_MIXED = "Mixto (manual + IA)"

# Flujo fijo de proveedor (HuggingFace), con selector de modelos HF.
FIXED_PROVIDER = PROVIDER_HF
DEFAULT_HF_VISION_MODEL = "Qwen/Qwen2.5-VL-72B-Instruct"
DEFAULT_HF_FORMAT_MODEL = "Qwen/QwQ-32B"
FIXED_TAG_MODE = TAG_MODE_MANUAL
HF_BASE_URL_DEFAULT = "https://router.huggingface.co/v1"

OPENAI_MODELS = [
    "gpt-4o-mini",
    "gpt-4.1-mini",
    "gpt-4o",
    "o4-mini",
]
OPENAI_FORMAT_MODELS = [
    "gpt-4.1-mini",
    "gpt-4o-mini",
    "gpt-4o",
    "o4-mini",
]
HF_MODELS = [
    "Qwen/Qwen2.5-VL-7B-Instruct",
    "Qwen/Qwen2.5-VL-72B-Instruct",
    "Qwen/Qwen2.5-VL-32B-Instruct",
    "Qwen/Qwen2.5-VL-3B-Instruct",
    "zai-org/GLM-4.5V",
    "zai-org/GLM-4.5V-FP8",
    "google/gemma-3-27b-it",
    "meta-llama/Llama-3.2-11B-Vision-Instruct",
]
HF_VISION_PROBE_MODELS = list(HF_MODELS)
HF_FORMAT_MODELS = [
    "Qwen/QwQ-32B",
    "Qwen/Qwen3-32B",
    "Qwen/Qwen3-30B-A3B",
    "Qwen/Qwen3-8B",
    "Qwen/Qwen3-4B",
    "Qwen/Qwen2.5-Math-72B-Instruct",
    "Qwen/Qwen2.5-Math-7B-Instruct",
    "Qwen/Qwen2.5-Math-1.5B-Instruct",
    "Qwen/Qwen2-Math-72B-Instruct",
    "Qwen/Qwen2-Math-7B-Instruct",
    "Qwen/Qwen2-Math-1.5B-Instruct",
    "Qwen/Qwen2.5-72B-Instruct",
    "Qwen/Qwen2.5-32B-Instruct",
    "deepseek-ai/DeepSeek-R1",
    "deepseek-ai/DeepSeek-V3",
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B",
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B",
]
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

SEP_LINE = "\u00a3"
SEP_OPT = "\u00e6"

BRACKET_TAG_RE = re.compile(r"\[\[\s*([^\]]+?)\s*\]\]")
IMAGE_TAG_RE = re.compile(r"\[\[\s*imagen\s*=\s*[^\]]+\]\]", re.IGNORECASE)
MARKER_VALUE_RE = re.compile(r"^(?P<base>.+?)[-_](?P<num>\d+)(?:[-_](?P<opt>[A-Za-z0-9]+))?$", re.IGNORECASE)
PLACEHOLDER_IMAGE_MARKER_RE = re.compile(
    r"\[\[\s*imagen\s*=\s*img-(?P<num>\d+)(?:-(?P<opt>[A-Za-z0-9]+))?\s*\]\]",
    re.IGNORECASE,
)
ITEM_HEADER_NUM_RE = re.compile(r"\\item\s*\[\s*\\textbf\s*\{\s*(\d+)\s*\.?\s*\}\s*\]", re.IGNORECASE)
ITEM_HEADER_RE = re.compile(r"(\\item\s*\[\s*\\textbf\s*\{\s*\d+\s*\.?\s*\}\s*\])", re.IGNORECASE)
STRUCTURED_ITEM_HEADER_RE = re.compile(
    r"\bITEM(?:\s+\d+\s*:|\s*:\s*\d+)\b", re.IGNORECASE
)
TAG_CURSO_RE = re.compile(r"\[\[\s*curso\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)
TAG_TEMA_RE = re.compile(r"\[\[\s*tema\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)
TAG_SUBTEMA_RE = re.compile(r"\[\[\s*subtema\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)
TAG_CLAVE_RE = re.compile(r"\[\[\s*clave\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)
TAG_SOLUCION_RE = re.compile(r"\[\[\s*solucion(?:ario)?\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)
ORPHAN_OPTIONS_PREFIX = "[[orphan_options]]"
ITEM_BLOCK_RE = re.compile(
    r"(\\item\s*\[\s*\\textbf\s*\{\s*\d+\s*\.?\s*\}\s*\].*?)(?=\s*\\item\s*\[\s*\\textbf|\Z)",
    re.IGNORECASE,
)
IMAGE_HINT_RE = re.compile(
    r"\b(grafico|gráfico|figura|diagrama|esquema|dibujo|imagen)\b",
    re.IGNORECASE,
)
IMAGE_HINT_PHRASES: Tuple[str, ...] = (
    "en el grafico",
    "en el gráfico",
    "del grafico",
    "del gráfico",
    "segun el grafico",
    "según el gráfico",
    "en la figura",
    "de la figura",
    "del siguiente grafico",
    "del siguiente gráfico",
)
GREEK_LATEX_NAMES = (
    "alpha",
    "beta",
    "gamma",
    "delta",
    "theta",
    "omega",
    "phi",
    "psi",
    "lambda",
    "mu",
    "pi",
    "sigma",
    "tau",
    "eta",
    "kappa",
    "rho",
)
UNICODE_GREEK_TO_LATEX = {
    "α": r"\alpha",
    "β": r"\beta",
    "γ": r"\gamma",
    "δ": r"\delta",
    "θ": r"\theta",
    "ω": r"\omega",
    "φ": r"\phi",
    "ψ": r"\psi",
    "λ": r"\lambda",
    "μ": r"\mu",
    "π": r"\pi",
    "σ": r"\sigma",
    "τ": r"\tau",
    "η": r"\eta",
    "κ": r"\kappa",
    "ρ": r"\rho",
}


class TranscriptorWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Modulo 0 - Transcripcion (Imagen -> LaTeX)")
        self.geometry("1120x720")
        self.minsize(840, 560)
        self._fit_initial_window()

        self.controller = TranscriptorController()

        self.db_name_var = tk.StringVar(value="")
        self.provider_var = tk.StringVar(value=FIXED_PROVIDER)
        hf_model_default = (os.getenv("HF_MODEL", "") or "").strip()
        if hf_model_default not in HF_MODELS:
            hf_model_default = DEFAULT_HF_VISION_MODEL
        hf_format_default = (os.getenv("HF_FORMAT_MODEL", "") or "").strip()
        if hf_format_default not in HF_FORMAT_MODELS:
            hf_format_default = DEFAULT_HF_FORMAT_MODEL
        self.model_var = tk.StringVar(value=hf_model_default)
        self.format_model_var = tk.StringVar(value=hf_format_default)
        self.timeout_var = tk.IntVar(value=180)
        self.retries_var = tk.IntVar(value=2)
        self.skip_done_var = tk.BooleanVar(value=False)
        self.ocr_lang_var = tk.StringVar(value=os.getenv("TESS_LANG", "spa+eng"))
        self.auto_format_var = tk.BooleanVar(value=True)
        self.format_with_llm_var = tk.BooleanVar(value=True)
        self.auto_crop_var = tk.BooleanVar(value=True)
        self.debug_detect_var = tk.BooleanVar(value=False)
        self.hf_token_var = tk.StringVar(value=os.getenv("HF_TOKEN", ""))
        self.detect_figure_var = tk.BooleanVar(value=True)
        self.segmentation_v2_var = tk.BooleanVar(value=False)
        self.ocr_exclude_box_var = tk.BooleanVar(value=False)
        self.tag_mode_var = tk.StringVar(value=FIXED_TAG_MODE)
        self.project_name_var = tk.StringVar(value="Proyecto")
        self.curso_var = tk.StringVar(value="")
        self.tema_var = tk.StringVar(value="")
        self.subtema_var = tk.StringVar(value="")
        self.step3_solucion_var = tk.StringVar(value="pendiente")
        self.step3_agregar_solucion_var = tk.BooleanVar(value=False)
        self._step3_last_claves_input = ""
        self.rate_in_var = tk.StringVar(value=os.getenv("EST_INPUT_USD_PER_1M", "0.0"))
        self.rate_out_var = tk.StringVar(value=os.getenv("EST_OUTPUT_USD_PER_1M", "0.0"))
        self.progress_status_var = tk.StringVar(value="Progreso: listo")
        self.loading_status_var = tk.StringVar(value="Estado: inactivo")
        self._final_crop_dir = Path(
            os.getenv("FIG_CROP_DIR", str((Path.cwd() / "figuras_recortadas").resolve()))
        )
        self.usage_summary_var = tk.StringVar(value="Sesion: in=0 | out=0 | total=0 | est_usd=0.000000")

        self._file_map: Dict[str, Path] = {}
        self._items: List[Tuple[str, str, List[str]]] = []  # (archivo_origen, item, imagenes)
        self._transcribed_by_label: Dict[str, str] = {}
        self._ocr_raw_first_by_label: Dict[str, str] = {}
        self._geometry_pass_by_label: Dict[str, str] = {}
        self._geometry_pass_payload_by_label: Dict[str, Dict[str, Any]] = {}
        self._ocr_merge_applied_by_label: Dict[str, str] = {}
        self._training_pairs_by_item: Dict[str, Dict[str, Any]] = {}
        self._direct_item_diagnostics_by_label: Dict[str, List[Dict[str, Any]]] = {}
        self._yolo_figure_suggestions_by_source: Dict[str, Dict[str, Any]] = {}
        self._segment_item_bindings_by_source: Dict[str, Dict[int, Dict[str, Any]]] = {}
        self._vision_direct_prompt_version = "vision_direct_v1"
        self._bbox_detector_version = "bbox_detector_v1"
        self._yolo_detector = None
        self._yolo_detector_path = ""
        self._preview_images: Dict[str, str] = {}
        self._missing_marker_warned: Set[str] = set()
        self._corrected_item_numbers: Set[int] = set()
        self._log_buffer: List[str] = []
        self._session_input_tokens = 0
        self._session_output_tokens = 0
        self._session_total_tokens = 0
        self._session_estimated_usd = 0.0
        self._progress_total_images = 0
        self._progress_done_images = 0
        self._progress_detected_items = 0
        self._runs_dir = Path.cwd() / ".cache" / "transcriptor_runs"
        self._runs_dir.mkdir(parents=True, exist_ok=True)
        self._sessions_dir = self._runs_dir / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._session_file_path: Optional[Path] = None
        self._session_last_dir: Path = self._sessions_dir
        self._datasets_dir = self._runs_dir / "datasets"
        self._datasets_dir.mkdir(parents=True, exist_ok=True)
        self._segmentador_v2 = SegmentadorProblemasV2(self._runs_dir / "v2_segments")
        self._segmentacion_v2_overrides: Dict[str, List[Tuple[int, int, int, int]]] = {}
        self._segmentation_v2_used_segments: Dict[str, Set[int]] = {}
        self._figure_boxes_by_source: Dict[str, List[Dict[str, Any]]] = {}
        # Legacy mirror of the primary manual figure box for backward compatibility.
        self._ocr_exclusion_boxes: Dict[str, Tuple[int, int, int, int]] = {}
        self._catalog_cache: Dict[str, Dict[str, List[Dict[str, str]]]] = {}
        self._tmp_crop_root = Path(tempfile.gettempdir()) / "auditor_ia_tmp_crops"
        self._tmp_crop_root.mkdir(parents=True, exist_ok=True)
        self._preview = PreviewWindow(title="Vista previa - Modulo 0", width=820, height=900)
        self._preview_debounce_after: str | None = None
        self._preview_nav_after: str | None = None
        self._suppress_output_sync = False
        self._transcribing = False
        self._ui_disposed = False
        self._segmentation_review_done = False
        self._segmentation_route_active = False
        self._segmentation_reviewed_sources: Set[str] = set()
        self._segmentation_scope_labels: Optional[Set[str]] = None
        self._hf_probe_running = False
        self._hf_available_vision_models: List[str] = []
        self._hf_unavailable_vision_models: Dict[str, str] = {}
        self._hf_probe_last_ts = ""

        self._apply_light_theme()
        self._build_ui()
        self._init_drag_and_drop()
        self._bind_shortcuts()
        self.bind("<Destroy>", self._on_destroy, add=True)
        self.report_callback_exception = self._report_callback_exception
        self._on_provider_change()
        self._listar_dbs()
        self._schedule_preview_nav_poll()
        self.after(1200, lambda: self._probe_hf_vision_models_async(auto=True))

    def _on_destroy(self, event=None) -> None:
        try:
            if event is None or getattr(event, "widget", None) is self:
                self._ui_disposed = True
                if self._preview_nav_after:
                    try:
                        self.after_cancel(self._preview_nav_after)
                    except Exception:
                        pass
                    self._preview_nav_after = None
        except Exception:
            self._ui_disposed = True

    def _ui_alive(self) -> bool:
        if self._ui_disposed:
            return False
        try:
            return bool(self.winfo_exists())
        except Exception:
            return False

    def _widget_alive(self, widget) -> bool:
        if widget is None:
            return False
        try:
            return bool(widget.winfo_exists())
        except Exception:
            return False

    def _report_callback_exception(self, exc, val, tb) -> None:
        msg = str(val or "")
        # Window was closed while async callbacks were still queued.
        if (not self._ui_alive()) and ("invalid command name" in msg.lower()):
            return
        traceback.print_exception(exc, val, tb)

    def _progress_start(self, total_images: int) -> None:
        if not self._ui_alive():
            return
        progress = getattr(self, "progress", None)
        if not self._widget_alive(progress):
            return
        total = max(0, int(total_images or 0))
        self._progress_total_images = total
        self._progress_done_images = 0
        self._progress_detected_items = 0
        try:
            progress.configure(mode="determinate", maximum=max(1, total), value=0)
            if total > 0:
                self.progress_status_var.set(f"Progreso: 0/{total} imagenes (0%) | items detectados: 0")
            else:
                self.progress_status_var.set("Progreso: sin elementos para procesar")
        except Exception:
            return

    def _progress_update(self, done_images: int, total_images: int, detected_items: int, *, current_label: str = "") -> None:
        if not self._ui_alive():
            return
        progress = getattr(self, "progress", None)
        if not self._widget_alive(progress):
            return
        total = max(1, int(total_images or 0))
        done = max(0, min(int(done_images or 0), total))
        items = max(0, int(detected_items or 0))
        self._progress_total_images = total
        self._progress_done_images = done
        self._progress_detected_items = items
        try:
            progress.configure(mode="determinate", maximum=total, value=done)
            pct = int(round((done / total) * 100))
            if current_label:
                self.progress_status_var.set(
                    f"Progreso: {done}/{total} imagenes ({pct}%) | items detectados: {items} | actual: {current_label}"
                )
            else:
                self.progress_status_var.set(
                    f"Progreso: {done}/{total} imagenes ({pct}%) | items detectados: {items}"
                )
        except Exception:
            return

    def _progress_finish(self, *, ok: int, errors: int, total_images: int, detected_items: int) -> None:
        if not self._ui_alive():
            return
        progress = getattr(self, "progress", None)
        if not self._widget_alive(progress):
            return
        total = max(1, int(total_images or 0))
        done = min(total, max(0, int(ok or 0) + int(errors or 0)))
        items = max(0, int(detected_items or 0))
        try:
            progress.configure(mode="determinate", maximum=total, value=done)
            self.progress_status_var.set(
                f"Completado: imagenes {done}/{total} | items detectados: {items} | OK={ok} | errores={errors}"
            )
        except Exception:
            return

    def _apply_light_theme(self) -> None:
        self.palette = apply_openai_theme(self)
        try:
            style = ttk.Style(self)
            style.configure(
                "LoadingGreen.Horizontal.TProgressbar",
                troughcolor=self.palette.get("border", "#e5e7eb"),
                background="#22c55e",
                lightcolor="#22c55e",
                darkcolor="#16a34a",
            )
        except Exception:
            pass

    def _fit_initial_window(self) -> None:
        try:
            self.update_idletasks()
            sw = max(900, int(self.winfo_screenwidth()))
            sh = max(700, int(self.winfo_screenheight()))
            w = min(1380, max(980, sw - 80))
            h = min(900, max(640, sh - 120))
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 2)
            self.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass

    def _build_ui(self) -> None:
        header = ttk.Label(self, text="Modulo 0 - Transcripcion (Imagen -> LaTeX)", style="Header.TLabel")
        header.pack(anchor="w", padx=16, pady=(14, 6))
        ttk.Label(
            self,
            text="Flujo fijo: Segmentacion V2 + OCR Vision (HuggingFace Qwen) + formateo + resultado previo.",
            style="SubHeader.TLabel",
        ).pack(anchor="w", padx=16, pady=(0, 8))

        top_actions = ttk.Frame(self)
        top_actions.pack(fill="x", padx=16, pady=(0, 8))
        ttk.Label(top_actions, text="Proyecto").pack(side="left")
        ttk.Entry(top_actions, textvariable=self.project_name_var, width=28).pack(side="left", padx=(8, 12))
        ttk.Button(top_actions, text="Guardar", command=self._save_session_quick, style="Ghost.TButton").pack(
            side="left"
        )
        ttk.Button(top_actions, text="Guardar como...", command=self._save_session_dialog, style="Ghost.TButton").pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(top_actions, text="Cargar sesion", command=self._load_session_dialog, style="Ghost.TButton").pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(
            top_actions,
            text="Exportar dataset (entrenamiento)",
            command=self._export_training_dataset_dialog,
            style="Ghost.TButton",
        ).pack(side="left", padx=(8, 0))

        cfg_card = ttk.Frame(self, style="Card.TFrame", padding=12)
        cfg_card.pack(fill="x", padx=16)
        cfg = ttk.Frame(cfg_card)
        cfg.pack(fill="x")

        ttk.Label(cfg, text="Base de datos").grid(row=0, column=0, sticky="w")
        self.combo_db = ttk.Combobox(cfg, state="readonly", textvariable=self.db_name_var, values=[])
        self.combo_db.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(cfg, text="Refrescar", command=self._listar_dbs, style="Ghost.TButton").grid(row=0, column=2, sticky="ew")

        ttk.Label(cfg, text="Motor").grid(row=0, column=3, sticky="w", padx=(12, 0))
        ttk.Label(cfg, text=FIXED_PROVIDER, style="SubHeader.TLabel").grid(row=0, column=4, sticky="w")
        ttk.Label(cfg, text="Modelo OCR HF").grid(row=0, column=5, sticky="w", padx=(12, 0))
        self.combo_model = ttk.Combobox(
            cfg,
            textvariable=self.model_var,
            values=HF_MODELS,
            width=40,
            state="readonly",
        )
        self.combo_model.grid(row=0, column=6, sticky="ew")
        ttk.Button(
            cfg,
            text="Ver disponibilidad HF",
            command=self._probe_hf_vision_models_async,
            style="Ghost.TButton",
        ).grid(row=0, column=7, sticky="ew", padx=(8, 0))

        ttk.Label(cfg, text="Timeout (s)").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Spinbox(cfg, from_=30, to=600, textvariable=self.timeout_var, width=8).grid(
            row=1, column=1, sticky="w", padx=(8, 8), pady=(10, 0)
        )
        ttk.Label(cfg, text="Reintentos").grid(row=1, column=2, sticky="w", pady=(10, 0))
        ttk.Spinbox(cfg, from_=0, to=5, textvariable=self.retries_var, width=8).grid(
            row=1, column=3, sticky="w", padx=(8, 0), pady=(10, 0)
        )
        ttk.Label(cfg, text="OCR idioma").grid(row=1, column=4, sticky="w", padx=(12, 0), pady=(10, 0))
        ttk.Entry(cfg, textvariable=self.ocr_lang_var, width=14).grid(row=1, column=5, sticky="w", pady=(10, 0))
        ttk.Checkbutton(cfg, text="Omitir ya transcritos", variable=self.skip_done_var).grid(
            row=1, column=6, sticky="w", padx=(12, 0), pady=(10, 0)
        )
        ttk.Label(
            cfg,
            text="Ruta fija: 1) Segmentar V2 -> 2) OCR + Modelo -> 3) Claves/Solucion (opcional) -> 4) Guardar BD",
            style="SubHeader.TLabel",
        ).grid(row=2, column=0, columnspan=7, sticky="w", pady=(10, 0))
        ttk.Label(cfg, text="Modelo formateo HF").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.combo_format_model = ttk.Combobox(
            cfg,
            textvariable=self.format_model_var,
            values=HF_FORMAT_MODELS,
            width=28,
            state="readonly",
        )
        self.combo_format_model.grid(row=3, column=1, columnspan=2, sticky="ew", padx=(8, 8), pady=(8, 0))
        ttk.Label(cfg, text="HF token").grid(row=3, column=3, sticky="e", padx=(12, 0), pady=(8, 0))
        ttk.Entry(cfg, textvariable=self.hf_token_var, width=28, show="*").grid(
            row=3, column=4, columnspan=3, sticky="ew", pady=(8, 0)
        )
        ttk.Label(cfg, text="Costo in USD/1M").grid(row=5, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(cfg, textvariable=self.rate_in_var, width=10).grid(
            row=5, column=1, sticky="w", padx=(8, 8), pady=(8, 0)
        )
        ttk.Label(cfg, text="Costo out USD/1M").grid(row=5, column=2, sticky="w", pady=(8, 0))
        ttk.Entry(cfg, textvariable=self.rate_out_var, width=10).grid(
            row=5, column=3, sticky="w", padx=(8, 8), pady=(8, 0)
        )
        ttk.Label(cfg, textvariable=self.usage_summary_var, style="SubHeader.TLabel").grid(
            row=6, column=0, columnspan=6, sticky="w", pady=(8, 0)
        )
        ttk.Button(cfg, text="Reiniciar contador", command=self._reset_usage_stats, style="Ghost.TButton").grid(
            row=6, column=5, sticky="e", pady=(8, 0)
        )
        ttk.Button(cfg, text="Ver consumo HF", command=self._open_hf_usage_pages, style="Ghost.TButton").grid(
            row=6, column=6, sticky="e", padx=(8, 0), pady=(8, 0)
        )
        ttk.Label(cfg, text="Curso").grid(row=7, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(cfg, textvariable=self.curso_var).grid(
            row=7, column=1, columnspan=2, sticky="ew", padx=(8, 8), pady=(8, 0)
        )
        ttk.Label(cfg, text="Tema").grid(row=7, column=3, sticky="e", padx=(12, 0), pady=(8, 0))
        ttk.Entry(cfg, textvariable=self.tema_var).grid(row=7, column=4, sticky="ew", pady=(8, 0))
        ttk.Label(cfg, text="Subtema").grid(row=7, column=5, sticky="e", padx=(12, 0), pady=(8, 0))
        ttk.Entry(cfg, textvariable=self.subtema_var).grid(row=7, column=6, sticky="ew", pady=(8, 0))
        ttk.Label(cfg, text="Modo etiquetas").grid(row=8, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(
            cfg,
            state="readonly",
            textvariable=self.tag_mode_var,
            values=[TAG_MODE_MANUAL, TAG_MODE_AUTO, TAG_MODE_MIXED],
            width=24,
        ).grid(row=8, column=1, columnspan=2, sticky="w", padx=(8, 8), pady=(8, 0))

        cfg.columnconfigure(1, weight=1)
        cfg.columnconfigure(6, weight=1)

        # Barra de acciones principal (posicion original: encima del area de trabajo).
        actions_main = ttk.Frame(self)
        actions_main.pack(fill="x", padx=16, pady=(8, 0))
        actions_main_row1 = ttk.Frame(actions_main)
        actions_main_row1.pack(fill="x")
        actions_main_row2 = ttk.Frame(actions_main)
        actions_main_row2.pack(fill="x", pady=(6, 0))

        ttk.Button(actions_main_row1, text="Paso 1: Segmentar", command=self._start_segmentation_review, style="Ghost.TButton").pack(side="left")
        ttk.Button(
            actions_main_row1,
            text="Ver recortes segmentos",
            command=self._open_segment_crops_view,
            style="Ghost.TButton",
        ).pack(side="left", padx=(8, 0))
        self.btn_transcribir = ttk.Button(
            actions_main_row1, text="Paso 2: OCR directo", command=self._transcribir_async, style="Accent.TButton"
        )
        self.btn_transcribir.pack(side="left", padx=(8, 0))
        ttk.Button(
            actions_main_row1,
            text="Paso 3 (opcional): Claves y solucion",
            command=self._open_step3_dialog,
            style="Ghost.TButton",
        ).pack(side="left", padx=(8, 0))
        ttk.Button(actions_main_row1, text="Copiar salida", command=self._copiar_salida, style="Ghost.TButton").pack(side="left", padx=(8, 0))
        ttk.Button(actions_main_row1, text="Ver OCR crudo", command=self._open_ocr_raw_view, style="Ghost.TButton").pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(actions_main_row1, text="Ver log", command=self._open_log_view, style="Ghost.TButton").pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(actions_main_row2, text="Guardar .tex", command=self._guardar_tex, style="Secondary.TButton").pack(side="left", padx=(8, 0))
        ttk.Button(actions_main_row2, text="Guardar a BD", command=self._guardar_bd, style="Secondary.TButton").pack(side="left", padx=(8, 0))
        ttk.Button(actions_main_row2, text="Vista previa LaTeX", command=self._open_preview, style="Ghost.TButton").pack(
            side="left", padx=(8, 0)
        )

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=16, pady=(14, 16))

        left = ttk.Frame(body)
        left.pack(side="left", fill="y")
        ttk.Label(left, text="Imagenes").pack(anchor="w")
        self.list_files = tk.Listbox(
            left,
            selectmode=tk.EXTENDED,
            width=44,
            bg=self.palette["surface"],
            fg=self.palette["text"],
            selectbackground=self.palette["select"],
            selectforeground=self.palette["text"],
            highlightthickness=1,
            highlightbackground=self.palette["border"],
        )
        self.list_files.pack(fill="y", expand=True, pady=(6, 0))

        actions = ttk.Frame(left)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="Agregar imagenes", command=self._add_images, style="Ghost.TButton").pack(side="left")
        ttk.Button(actions, text="Quitar", command=self._remove_selected, style="Secondary.TButton").pack(side="left", padx=(8, 0))

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(16, 0))

        ttk.Label(right, text="Salida (cada linea es un \\item)").pack(anchor="w")
        self.txt_out = tk.Text(
            right,
            bg=self.palette["surface"],
            fg=self.palette["text"],
            insertbackground=self.palette["text"],
            relief="solid",
            bd=1,
            wrap="word",
            highlightthickness=1,
            highlightbackground=self.palette["border"],
        )
        self.txt_out.pack(fill="both", expand=True, pady=(6, 10))
        self.txt_out.bind("<<Modified>>", self._on_out_modified)

        self.loading_bar = ttk.Progressbar(
            right,
            mode="indeterminate",
            maximum=100,
            style="LoadingGreen.Horizontal.TProgressbar",
        )
        self.loading_bar.pack(fill="x", pady=(0, 8))
        ttk.Label(right, textvariable=self.loading_status_var, style="SubHeader.TLabel").pack(anchor="w", pady=(0, 8))

        ttk.Label(right, text="Log").pack(anchor="w")
        self.txt_log = tk.Text(
            right,
            height=8,
            bg=self.palette["surface"],
            fg=self.palette["text"],
            insertbackground=self.palette["text"],
            relief="solid",
            bd=1,
            wrap="word",
            highlightthickness=1,
            highlightbackground=self.palette["border"],
        )
        self.txt_log.pack(fill="x")
        self.progress = ttk.Progressbar(right, mode="determinate", maximum=100, style="Accent.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(10, 0))
        ttk.Label(right, textvariable=self.progress_status_var, style="SubHeader.TLabel").pack(
            anchor="w", pady=(6, 0)
        )

    def _set_loading_bar(self, active: bool, message: str = "") -> None:
        bar = getattr(self, "loading_bar", None)
        if bar is None:
            return
        try:
            if active:
                self.loading_status_var.set(message or "Estado: ejecutando OCR...")
                bar.configure(mode="indeterminate")
                bar.start(10)
            else:
                bar.stop()
                bar.configure(mode="determinate", value=0)
                self.loading_status_var.set(message or "Estado: inactivo")
        except Exception:
            pass

    def _bind_shortcuts(self) -> None:
        # Intenta pegar imagen solo si el portapapeles contiene una.
        self.bind_all("<Control-v>", self._on_ctrl_v, add=True)

    def _init_drag_and_drop(self) -> None:
        if not DND_AVAILABLE:
            msg = "Drag & Drop no disponible (tkinterdnd2 no cargado)."
            if DND_IMPORT_ERROR:
                msg = f"{msg} Detalle: {DND_IMPORT_ERROR}"
            self._log(msg)
            return

        def register() -> None:
            registered = False
            for widget in (self, self.list_files):
                if not hasattr(widget, "drop_target_register"):
                    continue
                try:
                    widget.drop_target_register(DND_FILES)
                    widget.dnd_bind("<<Drop>>", self._on_drop_images)
                    registered = True
                except Exception as exc:
                    self._log(f"Drag & Drop: error registrando widget: {exc}")
            if registered:
                self._log("Arrastra imagenes a la lista para agregarlas al lote.")
            else:
                self._log("Drag & Drop no se pudo activar en esta ventana.")

        try:
            self.after(200, register)
        except Exception:
            register()

    def _parse_drop_files(self, data: str) -> List[str]:
        if not data:
            return []
        try:
            parts = self.tk.splitlist(data)
        except Exception:
            parts = re.findall(r"\{[^}]+\}|[^\s]+", data)
        out: List[str] = []
        for raw in parts:
            txt = str(raw).strip()
            if not txt:
                continue
            if txt.startswith("{") and txt.endswith("}"):
                txt = txt[1:-1].strip()
            txt = txt.strip().strip('"')
            if txt:
                out.append(txt)
        return out

    def _natural_path_key(self, path: Path) -> Tuple[Any, ...]:
        """
        Natural sort key: img2.png < img10.png.
        """
        raw = f"{str(path.parent).lower()}|{path.name.lower()}"
        parts = re.split(r"(\d+)", raw)
        key: List[Any] = []
        for part in parts:
            if part.isdigit():
                key.append(int(part))
            else:
                key.append(part)
        return tuple(key)

    def _collect_image_paths(self, raw_paths: List[str]) -> List[Path]:
        image_paths: List[Path] = []
        for raw in raw_paths:
            try:
                path = Path(raw).expanduser()
            except Exception:
                continue
            if path.is_dir():
                for candidate in sorted(path.iterdir()):
                    if candidate.is_file() and candidate.suffix.lower() in IMAGE_EXTS:
                        image_paths.append(candidate)
                continue
            if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
                image_paths.append(path)
        seen: Set[str] = set()
        unique: List[Path] = []
        for path in image_paths:
            try:
                key = str(path.resolve()).lower()
            except Exception:
                key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)
        unique.sort(key=self._natural_path_key)
        return unique

    def _add_images_from_paths(self, image_paths: List[Path]) -> Tuple[int, int]:
        if not image_paths:
            return (0, 0)
        existing: Set[str] = set()
        for p in self._file_map.values():
            try:
                existing.add(str(p.resolve()).lower())
            except Exception:
                existing.add(str(p).lower())
        added = 0
        skipped = 0
        for path in image_paths:
            if not path.exists() or not path.is_file():
                continue
            try:
                key = str(path.resolve()).lower()
            except Exception:
                key = str(path).lower()
            if key in existing:
                skipped += 1
                continue
            self._add_image_to_list(path, invalidate=False)
            existing.add(key)
            added += 1
        if added > 0:
            self._invalidate_segmentation_review_state()
        else:
            self._refresh_image_list_colors()
        return (added, skipped)

    def _on_drop_images(self, event) -> None:
        raw_paths = self._parse_drop_files(getattr(event, "data", "") or "")
        if not raw_paths:
            self._log("Drop recibido, pero sin rutas detectadas.")
            return
        image_paths = self._collect_image_paths(raw_paths)
        added, skipped = self._add_images_from_paths(image_paths)
        if added > 0:
            self._log(f"Drag & Drop: {added} imagen(es) agregadas.")
        if skipped > 0:
            self._log(f"Drag & Drop: {skipped} imagen(es) omitidas (duplicadas).")
        if added == 0 and skipped == 0:
            self._log("Drag & Drop: no se detectaron imagenes validas.")

    def _on_ctrl_v(self, event) -> str | None:
        # Si el foco estÃ¡ en un Text, permitimos el pegado normal de texto
        # a menos que detectemos una imagen en portapapeles.
        try:
            pasted = self._paste_from_clipboard(silent=True)
            if pasted:
                return "break"
        except Exception:
            pass
        return None

    def _log(self, msg: str) -> None:
        line = str(msg or "")
        self._log_buffer.append(line)
        if len(self._log_buffer) > 5000:
            self._log_buffer = self._log_buffer[-5000:]
        if not self._ui_alive():
            return
        txt = getattr(self, "txt_log", None)
        if not self._widget_alive(txt):
            return
        try:
            txt.insert("end", line + "\n")
            txt.see("end")
        except Exception:
            # Ignore writes after window teardown.
            return

    def _open_log_view(self) -> None:
        dlg = tk.Toplevel(self)
        dlg.title("Log completo")
        dlg.geometry("1020x680")
        dlg.minsize(760, 420)
        try:
            dlg.transient(self)
        except Exception:
            pass

        body = ttk.Frame(dlg)
        body.pack(fill="both", expand=True, padx=12, pady=(10, 10))
        txt = tk.Text(
            body,
            wrap="word",
            bg=self.palette["surface"],
            fg=self.palette["text"],
            insertbackground=self.palette["text"],
            relief="solid",
            bd=1,
            highlightthickness=1,
            highlightbackground=self.palette["border"],
            highlightcolor=self.palette["border"],
            font=("Consolas", 10),
        )
        scr = ttk.Scrollbar(body, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=scr.set)
        txt.pack(side="left", fill="both", expand=True)
        scr.pack(side="right", fill="y")

        content = "\n".join(self._log_buffer).strip()
        if not content:
            content = "(sin logs por ahora)"
        txt.insert("1.0", content + "\n")
        txt.configure(state="disabled")

        btns = ttk.Frame(dlg)
        btns.pack(fill="x", padx=12, pady=(0, 12))

        def on_copy() -> None:
            data = "\n".join(self._log_buffer).strip()
            if not data:
                data = "(sin logs por ahora)"
            try:
                self.clipboard_clear()
                self.clipboard_append(data + "\n")
                self.update_idletasks()
                messagebox.showinfo("Log", "Log copiado al portapapeles.")
            except Exception as exc:
                messagebox.showerror("Log", f"No se pudo copiar:\n{exc}")

        ttk.Button(btns, text="Copiar", command=on_copy, style="Ghost.TButton").pack(side="left")
        ttk.Button(btns, text="Cerrar", command=dlg.destroy, style="Secondary.TButton").pack(side="right")

    def _new_run_log_path(self, *, provider: str, model: str, detect_model: str, total: int) -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_id = f"{ts}_{uuid.uuid4().hex[:8]}"
        path = self._runs_dir / f"run_{run_id}.jsonl"
        self._append_run_event(
            path,
            {
                "event": "run_started",
                "run_id": run_id,
                "provider": provider,
                "model": model,
                "detect_model": detect_model,
                "total_images": total,
                "ts": datetime.now().isoformat(timespec="seconds"),
            },
        )
        return path

    def _append_run_event(self, run_path: Path, payload: Dict[str, object]) -> None:
        try:
            event = dict(payload)
            event.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
            run_path.parent.mkdir(parents=True, exist_ok=True)
            with run_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception:
            # Checkpointing must never break the transcription flow.
            return

    def _parse_non_negative_float(self, raw: str) -> float:
        text = (raw or "").strip().replace(",", ".")
        try:
            value = float(text)
            if value < 0:
                return 0.0
            return value
        except Exception:
            return 0.0

    def _estimate_cost_usd(self, input_tokens: Optional[int], output_tokens: Optional[int]) -> float:
        in_rate = self._parse_non_negative_float(self.rate_in_var.get())
        out_rate = self._parse_non_negative_float(self.rate_out_var.get())
        in_tokens = input_tokens or 0
        out_tokens = output_tokens or 0
        return ((in_tokens * in_rate) + (out_tokens * out_rate)) / 1_000_000.0

    def _refresh_usage_summary(self) -> None:
        self.usage_summary_var.set(
            "Sesion: "
            f"in={self._session_input_tokens} | "
            f"out={self._session_output_tokens} | "
            f"total={self._session_total_tokens} | "
            f"est_usd={self._session_estimated_usd:.6f}"
        )

    def _reset_usage_stats(self) -> None:
        self._session_input_tokens = 0
        self._session_output_tokens = 0
        self._session_total_tokens = 0
        self._session_estimated_usd = 0.0
        self._refresh_usage_summary()
        self._log("Contador de uso reiniciado.")

    def _open_hf_usage_pages(self) -> None:
        urls = [
            "https://huggingface.co/settings/billing",
            "https://huggingface.co/settings/inference-providers",
        ]
        opened = 0
        for url in urls:
            try:
                if webbrowser.open_new_tab(url):
                    opened += 1
            except Exception:
                continue
        if opened:
            self._log("Hugging Face: abriendo panel de consumo/facturacion.")
            return
        self._log(
            "No se pudo abrir navegador automaticamente. Revisa manualmente: "
            "https://huggingface.co/settings/billing"
        )

    def _as_bool(self, value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value or "").strip().lower()
        if text in {"1", "true", "yes", "si", "sí", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return default

    def _safe_int(self, value: Any, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default

    def _safe_fs_name(self, text: str, fallback: str = "item") -> str:
        safe = re.sub(r"[^\w\-.]+", "_", (text or "").strip(), flags=re.UNICODE).strip("_")
        return safe or fallback

    def _parse_boxes_payload(self, raw_boxes: Any) -> List[Tuple[int, int, int, int]]:
        boxes: List[Tuple[int, int, int, int]] = []
        if not isinstance(raw_boxes, list):
            return boxes
        for row in raw_boxes:
            if not isinstance(row, (list, tuple)) or len(row) != 4:
                continue
            try:
                x1, y1, x2, y2 = [int(v) for v in row]
                boxes.append((x1, y1, x2, y2))
            except Exception:
                continue
        return boxes

    def _normalize_figure_box_entry(
        self,
        raw: Dict[str, Any],
        *,
        default_source: str = "manual",
        default_confirmed: bool = True,
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, dict):
            return None
        bbox_raw = raw.get("bbox_px")
        if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) < 4:
            return None
        try:
            x1, y1, x2, y2 = [int(v) for v in bbox_raw[:4]]
        except Exception:
            return None
        if x2 <= x1 or y2 <= y1:
            return None
        source_name = str(raw.get("source", default_source) or default_source).strip().lower()
        if source_name not in {"manual", "yolo"}:
            source_name = default_source
        try:
            conf = float(raw.get("conf", 0.0) or 0.0)
        except Exception:
            conf = 0.0
        item_hint_raw = raw.get("item_hint", None)
        item_hint: Optional[int] = None
        if item_hint_raw is not None:
            try:
                parsed_item_hint = int(item_hint_raw)
            except Exception:
                parsed_item_hint = 0
            if parsed_item_hint > 0:
                item_hint = parsed_item_hint
        confirmed = self._as_bool(raw.get("confirmed"), default_confirmed)
        updated_at = str(raw.get("updated_at", "") or "").strip()
        if not updated_at:
            updated_at = datetime.now().isoformat(timespec="seconds")
        return {
            "bbox_px": [x1, y1, x2, y2],
            "source": source_name,
            "conf": max(0.0, min(1.0, conf)),
            "item_hint": item_hint,
            "confirmed": bool(confirmed),
            "updated_at": updated_at,
        }

    def _get_figure_boxes(self, path: Path) -> List[Dict[str, Any]]:
        key = self._seg_v2_source_key(path)
        raw = self._figure_boxes_by_source.get(key)
        if not isinstance(raw, list):
            return []
        out: List[Dict[str, Any]] = []
        for entry in raw:
            normalized = self._normalize_figure_box_entry(
                entry if isinstance(entry, dict) else {},
                default_source="manual",
                default_confirmed=True,
            )
            if normalized is not None:
                out.append(normalized)
        return out

    def _set_figure_boxes(self, path: Path, boxes: List[Dict[str, Any]]) -> None:
        key = self._seg_v2_source_key(path)
        normalized: List[Dict[str, Any]] = []
        for raw in boxes:
            entry = self._normalize_figure_box_entry(raw, default_source="manual", default_confirmed=True)
            if entry is not None:
                normalized.append(entry)
        if normalized:
            self._figure_boxes_by_source[key] = normalized
        else:
            self._figure_boxes_by_source.pop(key, None)
        self._sync_legacy_ocr_box_for_source_key(key)

    def _sync_legacy_ocr_box_for_source_key(self, source_key: str) -> None:
        primary: Optional[Tuple[int, int, int, int]] = None
        entries = self._figure_boxes_by_source.get(str(source_key), [])
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("source", "")).strip().lower() != "manual":
                    continue
                if not self._as_bool(entry.get("confirmed"), True):
                    continue
                bbox_raw = entry.get("bbox_px")
                if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) < 4:
                    continue
                try:
                    x1, y1, x2, y2 = [int(v) for v in bbox_raw[:4]]
                except Exception:
                    continue
                if x2 <= x1 or y2 <= y1:
                    continue
                primary = (x1, y1, x2, y2)
                break
        if primary is None:
            self._ocr_exclusion_boxes.pop(str(source_key), None)
        else:
            self._ocr_exclusion_boxes[str(source_key)] = primary

    def _replace_manual_figure_box(self, path: Path, box_px: Tuple[int, int, int, int]) -> bool:
        try:
            from PIL import Image  # type: ignore
        except Exception:
            self._log("Caja figura: falta Pillow para guardar la caja.")
            return False
        try:
            with Image.open(path) as im:
                normalized = self._normalize_box_px(
                    tuple(int(v) for v in box_px),
                    width=int(im.size[0]),
                    height=int(im.size[1]),
                )
        except Exception as exc:
            self._log(f"Caja figura: no se pudo abrir {path.name}: {exc}")
            return False
        if not normalized:
            self._log(f"Caja figura: cuadro invalido para {path.name}.")
            return False
        existing = self._get_figure_boxes(path)
        kept = [
            entry
            for entry in existing
            if str(entry.get("source", "")).strip().lower() != "manual"
        ]
        kept.insert(
            0,
            {
                "bbox_px": [int(v) for v in normalized],
                "source": "manual",
                "conf": 1.0,
                "item_hint": None,
                "confirmed": True,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
        self._set_figure_boxes(path, kept)
        return True

    def _clear_manual_figure_boxes(self, path: Path) -> int:
        existing = self._get_figure_boxes(path)
        if not existing:
            self._set_figure_boxes(path, [])
            return 0
        kept = [
            entry
            for entry in existing
            if str(entry.get("source", "")).strip().lower() != "manual"
        ]
        removed = len(existing) - len(kept)
        self._set_figure_boxes(path, kept)
        return max(0, removed)

    def _normalize_binding_slot(self, raw_slot: str) -> str:
        slot = str(raw_slot or "").strip().upper()
        if not slot:
            return "ENUNCIADO"
        if slot in {"ENUN", "ENUNCIADO", "PREGUNTA", "STEM"}:
            return "ENUNCIADO"
        if slot in {"A", "B", "C", "D", "E"}:
            return slot
        # Permite etiquetas manuales adicionales (p. ej. F, CLAVE, ALT1).
        slot = re.sub(r"[^A-Z0-9_]", "", slot)
        if not slot:
            return "ENUNCIADO"
        if len(slot) > 16:
            slot = slot[:16]
        return slot

    def _build_binding_marker_name(self, *, item_num: int, slot: str) -> str:
        n = max(1, int(item_num or 1))
        norm_slot = self._normalize_binding_slot(slot)
        if norm_slot == "ENUNCIADO":
            return f"img-{n}"
        return f"img-{n}-{norm_slot}"

    def _get_segment_bindings_by_source_key(self, source_key: str) -> Dict[int, Dict[str, Any]]:
        raw = self._segment_item_bindings_by_source.get(str(source_key), {})
        out: Dict[int, Dict[str, Any]] = {}
        if not isinstance(raw, dict):
            return out
        for seg_k, payload in raw.items():
            try:
                seg_idx = int(seg_k)
            except Exception:
                continue
            if seg_idx < 0 or not isinstance(payload, dict):
                continue
            item_num = int(self._safe_int(payload.get("item_num", 0), 0))
            if item_num <= 0:
                continue
            slot = self._normalize_binding_slot(str(payload.get("slot", "ENUNCIADO") or "ENUNCIADO"))
            marker_name = str(payload.get("marker_name", "") or "").strip()
            if not marker_name:
                marker_name = self._build_binding_marker_name(item_num=item_num, slot=slot)
            crop_path = str(payload.get("crop_path", "") or "").strip()
            confirmed = self._as_bool(payload.get("confirmed"), True)
            updated_at = str(payload.get("updated_at", "") or "").strip() or datetime.now().isoformat(timespec="seconds")
            out[seg_idx] = {
                "item_num": item_num,
                "slot": slot,
                "marker_name": marker_name,
                "crop_path": crop_path,
                "confirmed": bool(confirmed),
                "updated_at": updated_at,
            }
        return out

    def _set_segment_bindings_by_source_key(self, source_key: str, bindings: Dict[int, Dict[str, Any]]) -> None:
        key = str(source_key or "").strip()
        if not key:
            return
        bucket: Dict[int, Dict[str, Any]] = {}
        for seg_idx, raw in (bindings or {}).items():
            try:
                seg = int(seg_idx)
            except Exception:
                continue
            if seg < 0 or not isinstance(raw, dict):
                continue
            item_num = int(self._safe_int(raw.get("item_num", 0), 0))
            if item_num <= 0:
                continue
            slot = self._normalize_binding_slot(str(raw.get("slot", "ENUNCIADO") or "ENUNCIADO"))
            marker_name = str(raw.get("marker_name", "") or "").strip()
            if not marker_name:
                marker_name = self._build_binding_marker_name(item_num=item_num, slot=slot)
            crop_path = str(raw.get("crop_path", "") or "").strip()
            confirmed = self._as_bool(raw.get("confirmed"), True)
            updated_at = str(raw.get("updated_at", "") or "").strip() or datetime.now().isoformat(timespec="seconds")
            bucket[seg] = {
                "item_num": item_num,
                "slot": slot,
                "marker_name": marker_name,
                "crop_path": crop_path,
                "confirmed": bool(confirmed),
                "updated_at": updated_at,
            }
        if bucket:
            self._segment_item_bindings_by_source[key] = bucket
        else:
            self._segment_item_bindings_by_source.pop(key, None)

    def _set_segment_item_binding(
        self,
        *,
        source_path: Path,
        segment_idx: int,
        item_num: int,
        slot: str,
        crop_path: str,
        confirmed: bool = True,
    ) -> Dict[str, Any]:
        source_key = self._seg_v2_source_key(source_path)
        slot_norm = self._normalize_binding_slot(slot)
        marker_name = self._build_binding_marker_name(item_num=item_num, slot=slot_norm)
        bucket = self._get_segment_bindings_by_source_key(source_key)

        replaced_slots: List[int] = []
        for old_seg, old_payload in list(bucket.items()):
            if old_seg == int(segment_idx):
                continue
            if int(self._safe_int(old_payload.get("item_num", 0), 0)) != int(item_num):
                continue
            old_slot = self._normalize_binding_slot(str(old_payload.get("slot", "ENUNCIADO") or "ENUNCIADO"))
            if old_slot != slot_norm:
                continue
            bucket.pop(old_seg, None)
            replaced_slots.append(old_seg)

        payload = {
            "item_num": int(item_num),
            "slot": slot_norm,
            "marker_name": marker_name,
            "crop_path": str(crop_path or "").strip(),
            "confirmed": bool(confirmed),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        bucket[int(segment_idx)] = payload
        self._set_segment_bindings_by_source_key(source_key, bucket)
        for old_seg in replaced_slots:
            self._log(
                f"binding_replaced source={source_path.name} seg_old={old_seg} -> item={item_num} slot={slot_norm}"
            )
        self._log(
            f"binding_set source={source_path.name} seg={int(segment_idx)} -> item={item_num} slot={slot_norm} marker={marker_name}"
        )
        return payload

    def _source_has_confirmed_bindings(self, *, source_stem: str) -> bool:
        src = self._find_source_path_by_stem(source_stem)
        if src is None:
            return False
        source_key = self._seg_v2_source_key(src)
        bucket = self._get_segment_bindings_by_source_key(source_key)
        for payload in bucket.values():
            if self._as_bool(payload.get("confirmed"), False):
                return True
        return False

    def _collect_confirmed_binding_marker_paths(self) -> Dict[str, str]:
        marker_map: Dict[str, str] = {}
        for source_key in list(self._segment_item_bindings_by_source.keys()):
            bucket = self._get_segment_bindings_by_source_key(source_key)
            for payload in bucket.values():
                if not self._as_bool(payload.get("confirmed"), False):
                    continue
                marker = str(payload.get("marker_name", "") or "").strip()
                crop_path = str(payload.get("crop_path", "") or "").strip()
                if not marker or not self._is_valid_image_path(crop_path):
                    continue
                marker_map[marker] = crop_path
        return marker_map

    def _insert_explicit_marker_in_slot(
        self,
        *,
        text: str,
        marker_name: str,
        slot: str,
        path: Path,
        numero: int,
    ) -> str:
        raw = str(text or "").strip()
        marker = f"[[Imagen={str(marker_name or '').strip()}]]"
        if not raw or not marker_name:
            return raw
        if marker in raw:
            return raw

        slot_norm = self._normalize_binding_slot(slot)
        if slot_norm == "ENUNCIADO":
            if f"{SEP_LINE}A)" in raw:
                return raw.replace(f"{SEP_LINE}A)", f" {marker} {SEP_LINE}A)", 1)
            if "Â£A)" in raw:
                return raw.replace("Â£A)", f" {marker} Â£A)", 1)
            m_a = re.search(r"\bA\)\s*", raw)
            if m_a:
                return f"{raw[:m_a.start()].rstrip()} {marker} {raw[m_a.start():].lstrip()}".strip()
            return f"{raw} {marker}".strip()

        pat_sep = re.compile(rf"([{re.escape(SEP_LINE)}{re.escape(SEP_OPT)}]\s*{slot_norm}\)\s*)", re.IGNORECASE)
        if pat_sep.search(raw):
            return pat_sep.sub(rf"\1{marker} ", raw, count=1)
        pat_plain = re.compile(rf"(?<![A-Za-z0-9])({slot_norm}\)\s*)", re.IGNORECASE)
        if pat_plain.search(raw):
            return pat_plain.sub(rf"\1{marker} ", raw, count=1)

        # Fallback conservador: si no encontramos slot, insertamos marker global.
        return self._insert_image_marker(raw, path=path, numero=numero)

    def _migrate_legacy_preview_bindings(self) -> int:
        """
        Best-effort migration from legacy marker->path mappings when no explicit
        segment bindings exist. Migrated entries are unconfirmed.
        """
        if self._segment_item_bindings_by_source:
            return 0
        migrated = 0
        for archivo, item_text, imgs in self._items:
            item = str(item_text or "").strip()
            if not item:
                continue
            item_num = self.controller.parsear_numero_original(item) or 0
            if item_num <= 0:
                continue
            src = self._find_source_path_by_stem(str(archivo or "").strip())
            if src is None:
                continue
            source_key = self._seg_v2_source_key(src)
            try:
                segs = self._get_segments_v2_for_source(src)
            except Exception:
                segs = []
            if not segs:
                continue
            bucket = self._get_segment_bindings_by_source_key(source_key)
            used_seg = set(bucket.keys())
            next_seg = next((i for i in range(len(segs)) if i not in used_seg), None)
            if next_seg is None:
                continue
            markers = self._extract_image_marker_names(item)
            if not markers:
                continue
            img_candidates = [str(p) for p in (imgs or []) if self._is_valid_image_path(str(p))]
            for mk in markers:
                if next_seg is None:
                    break
                crop_path = ""
                if img_candidates:
                    crop_path = img_candidates[0]
                else:
                    mapped = str(self._preview_images.get(mk) or "").strip()
                    if self._is_valid_image_path(mapped):
                        crop_path = mapped
                if not crop_path:
                    continue
                slot = "ENUNCIADO"
                p = MARKER_VALUE_RE.match(mk)
                if p:
                    opt = str(p.group("opt") or "").strip().upper()
                    if opt in {"A", "B", "C", "D", "E"}:
                        slot = opt
                bucket[int(next_seg)] = {
                    "item_num": int(item_num),
                    "slot": slot,
                    "marker_name": str(mk),
                    "crop_path": crop_path,
                    "confirmed": False,
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
                migrated += 1
                used_seg.add(int(next_seg))
                next_seg = next((i for i in range(len(segs)) if i not in used_seg), None)
            self._set_segment_bindings_by_source_key(source_key, bucket)
        return migrated

    def _collect_dataset_sources(self) -> List[Tuple[str, Path]]:
        reviewed_sources = self._collect_review_sources()
        if reviewed_sources:
            return [(label, src) for (_idx, label, src) in reviewed_sources if src and src.exists()]

        # Fallback: use all loaded files, skipping derived segment/masked entries.
        out: List[Tuple[str, Path]] = []
        seen: Set[str] = set()
        for label, src in self._file_map.items():
            if "[v2-" in label.lower() or "[v2c-" in label.lower():
                continue
            if not src or not src.exists():
                continue
            key = self._seg_v2_source_key(src)
            if key in seen:
                continue
            seen.add(key)
            out.append((label, src))
        return out

    def _group_items_by_source(self) -> Dict[str, List[Dict[str, Any]]]:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for archivo_origen, item, image_paths in self._items:
            source_name = (archivo_origen or "").strip()
            if not source_name:
                continue
            bucket = grouped.setdefault(source_name, [])
            bucket.append(
                {
                    "item_text": str(item or "").strip(),
                    "images": [str(p) for p in (image_paths or []) if str(p or "").strip()],
                    "has_image_marker": bool(self._has_image_marker(item or "")),
                }
            )
        return grouped

    def _build_training_pairs_jsonl_rows(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        prompt_text = (
            "Transcribe el problema en formato scan final. "
            "Devuelve una sola linea con estructura \\item y separadores £/æ."
        )
        for key in sorted(self._training_pairs_by_item.keys()):
            pair = self._training_pairs_by_item.get(key, {})
            if not isinstance(pair, dict):
                continue
            human_raw = self.controller.normalizar_item_una_linea(str(pair.get("human_final_output", "") or ""))
            completion = self._strip_taxonomy_tags_for_training(human_raw)
            metadata = dict(pair.get("metadata", {}) or {})
            figure_confirmed = self._as_bool(metadata.get("figure_confirmed"), False)
            if not figure_confirmed:
                completion = self.controller.normalizar_item_una_linea(IMAGE_TAG_RE.sub(" ", completion))
            if not completion:
                continue
            image_path = ""
            raw_paths = metadata.get("human_image_paths", [])
            if isinstance(raw_paths, list):
                for raw_path in raw_paths:
                    candidate = str(raw_path or "").strip()
                    if candidate:
                        image_path = candidate
                        break
            if not image_path:
                image_path = str(metadata.get("source_path", "") or "").strip()
            rows.append(
                {
                    "id": str(key),
                    "input_image_path": image_path,
                    "prompt": prompt_text,
                    "completion": completion,
                    "input_structured": str(pair.get("input_structured", "") or ""),
                    "model_raw_output": str(pair.get("model_raw_output", "") or ""),
                    "model_validated_output": str(pair.get("model_validated_output", "") or ""),
                    "human_final_output": completion,
                    "metadata": {
                        "status": str(pair.get("status", "") or ""),
                        "model": str(pair.get("model", "") or ""),
                        "provider": str(pair.get("provider", "") or ""),
                        "prompt_version": str(pair.get("prompt_version", self._vision_direct_prompt_version) or ""),
                        "run_id": str(pair.get("run_id", "") or ""),
                        "timestamp": str(pair.get("timestamp", "") or ""),
                        "source_labels": list(pair.get("source_labels", []) or []),
                        "item_num": int(self._safe_int(metadata.get("item_num", 0), 0)),
                        "source_stem": str(metadata.get("source_stem", "") or ""),
                        "figure_confirmed": bool(figure_confirmed),
                        "figure_bbox_source": str(metadata.get("figure_bbox_source", "") or ""),
                        "segmentation_reviewed": bool(metadata.get("segmentation_reviewed", False)),
                        "segment_idx": metadata.get("segment_idx", None),
                        "slot": str(metadata.get("slot", "") or ""),
                        "marker_name": str(metadata.get("marker_name", "") or ""),
                        "binding_confirmed": bool(metadata.get("binding_confirmed", False)),
                        "binding_source": str(metadata.get("binding_source", "") or ""),
                    },
                }
            )
        return rows

    def _strip_taxonomy_tags_for_training(self, text: str) -> str:
        clean = str(text or "").strip()
        if not clean:
            return ""
        clean = TAG_CURSO_RE.sub(" ", clean)
        clean = TAG_TEMA_RE.sub(" ", clean)
        clean = TAG_SUBTEMA_RE.sub(" ", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean

    def _export_yolo_bbox_dataset(
        self,
        *,
        dataset_dir: Path,
        sources: List[Tuple[str, Path]],
    ) -> Dict[str, int]:
        stats = {"images": 0, "labels": 0, "boxes": 0}
        yolo_dir = dataset_dir / "yolo_bbox"
        for split in ("train", "val", "test"):
            (yolo_dir / "images" / split).mkdir(parents=True, exist_ok=True)
            (yolo_dir / "labels" / split).mkdir(parents=True, exist_ok=True)

        total = max(1, len(sources))
        for idx, (_label, src) in enumerate(sources, start=1):
            ratio = float(idx) / float(total)
            if ratio <= 0.8:
                split = "train"
            elif ratio <= 0.9:
                split = "val"
            else:
                split = "test"

            try:
                from PIL import Image  # type: ignore
            except Exception:
                break
            try:
                with Image.open(src) as im:
                    width, height = im.size
            except Exception:
                continue
            if width <= 0 or height <= 0:
                continue

            sample_id = f"{idx:04d}_{self._safe_fs_name(src.stem, f'source_{idx:04d}')}"
            ext = src.suffix if src.suffix else ".png"
            dst_img = yolo_dir / "images" / split / f"{sample_id}{ext}"
            try:
                shutil.copy2(src, dst_img)
            except Exception:
                continue
            stats["images"] += 1

            lines: List[str] = []
            for entry in self._get_figure_boxes(src):
                if str(entry.get("source", "")).strip().lower() != "manual":
                    continue
                if not self._as_bool(entry.get("confirmed"), True):
                    continue
                bbox_raw = entry.get("bbox_px")
                if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) < 4:
                    continue
                try:
                    x1, y1, x2, y2 = [int(v) for v in bbox_raw[:4]]
                except Exception:
                    continue
                x1 = max(0, min(width - 1, x1))
                x2 = max(0, min(width, x2))
                y1 = max(0, min(height - 1, y1))
                y2 = max(0, min(height, y2))
                if x2 <= x1 or y2 <= y1:
                    continue
                bw = float(x2 - x1) / float(width)
                bh = float(y2 - y1) / float(height)
                cx = (float(x1) + float(x2)) / 2.0 / float(width)
                cy = (float(y1) + float(y2)) / 2.0 / float(height)
                lines.append(f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
            dst_lbl = yolo_dir / "labels" / split / f"{sample_id}.txt"
            try:
                dst_lbl.write_text("\n".join(lines), encoding="utf-8")
                stats["labels"] += 1
                stats["boxes"] += len(lines)
            except Exception:
                continue
        classes_path = yolo_dir / "classes.txt"
        try:
            classes_path.write_text("figura_problema\n", encoding="utf-8")
        except Exception:
            pass
        return stats

    def _save_masked_copy_for_dataset(
        self,
        *,
        source_path: Path,
        boxes_px: List[Tuple[int, int, int, int]],
        out_path: Path,
    ) -> Optional[Path]:
        try:
            from PIL import Image, ImageDraw  # type: ignore
        except Exception:
            self._log("Dataset: falta Pillow para generar OCR enmascarado.")
            return None
        if not boxes_px:
            return None
        try:
            with Image.open(source_path) as im:
                out = im.convert("RGB")
                draw = ImageDraw.Draw(out)
                for box in boxes_px:
                    normalized = self._normalize_box_px(
                        tuple(int(v) for v in box),
                        width=int(im.size[0]),
                        height=int(im.size[1]),
                    )
                    if not normalized:
                        continue
                    left, top, right, bottom = normalized
                    draw.rectangle((left, top, right, bottom), fill=(255, 255, 255))
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out.save(out_path, format="PNG")
            return out_path
        except Exception as exc:
            self._log(f"Dataset: no se pudo generar imagen OCR enmascarada para {source_path.name}: {exc}")
            return None

    def _normalize_project_name(self, value: str) -> str:
        name = (value or "").strip()
        if not name:
            return "Proyecto"
        return re.sub(r"\s+", " ", name)

    def _default_session_filename(self) -> str:
        project = self._normalize_project_name(self.project_name_var.get())
        slug = self._sanitize_storage_base_name(project).lower() or "proyecto"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"sesion_{slug}_{ts}.json"

    def _session_temp_dir_for_path(self, session_path: Path) -> Path:
        return session_path.parent / f"{session_path.stem}_tmp"

    def _resolve_session_resource_path(self, raw_path: str, *, session_path: Path) -> Path:
        value = str(raw_path or "").strip()
        if not value:
            return Path(value)
        p = Path(value)
        candidates: List[Path] = []
        if p.is_absolute():
            candidates.append(p)
        else:
            candidates.append((session_path.parent / p))
            candidates.append(p)
        for cand in candidates:
            try:
                if cand.exists():
                    return cand.resolve()
            except Exception:
                continue
        return candidates[0] if candidates else p

    def _export_session_temp_bundle(self, *, out_path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
        tmp_root = self._session_temp_dir_for_path(out_path)
        sources_dir = tmp_root / "sources"
        crops_dir = tmp_root / "crops"
        segments_dir = tmp_root / "segments"
        sources_dir.mkdir(parents=True, exist_ok=True)
        crops_dir.mkdir(parents=True, exist_ok=True)
        segments_dir.mkdir(parents=True, exist_ok=True)

        source_map: Dict[str, str] = {}
        crop_map: Dict[str, str] = {}
        source_stems: Set[str] = set()

        def _copy_to_bundle(path_str: str, *, dest_dir: Path, cache: Dict[str, str], fallback_name: str) -> str:
            raw = str(path_str or "").strip()
            if not raw:
                return raw
            src = Path(raw)
            try:
                src_resolved = src.resolve()
            except Exception:
                src_resolved = src
            if (not src_resolved.exists()) or (not src_resolved.is_file()):
                return raw

            key = str(src_resolved).lower()
            cached = cache.get(key)
            if cached:
                return cached

            safe = self._safe_fs_name(src_resolved.stem, fallback_name)
            ext = src_resolved.suffix.lower() or ".png"
            target = dest_dir / f"{safe}{ext}"
            idx = 1
            while target.exists():
                idx += 1
                target = dest_dir / f"{safe}_{idx:02d}{ext}"
            try:
                shutil.copy2(src_resolved, target)
            except Exception:
                return raw

            rel = "./" + str(target.relative_to(out_path.parent)).replace("\\", "/")
            cache[key] = rel
            return rel

        files_payload = payload.get("files", [])
        if isinstance(files_payload, list):
            for row in files_payload:
                if not isinstance(row, dict):
                    continue
                current = str(row.get("path", "") or "").strip()
                rewritten = _copy_to_bundle(
                    current,
                    dest_dir=sources_dir,
                    cache=source_map,
                    fallback_name="source",
                )
                row["path"] = rewritten
                resolved_source = self._resolve_session_resource_path(current, session_path=out_path)
                stem = str(resolved_source.stem or "").strip()
                if stem:
                    source_stems.add(stem)

        items_payload = payload.get("items", [])
        if isinstance(items_payload, list):
            for row in items_payload:
                if not isinstance(row, dict):
                    continue
                imgs = row.get("imagenes", [])
                if not isinstance(imgs, list):
                    continue
                rewritten_imgs: List[str] = []
                for img in imgs:
                    rewritten_imgs.append(
                        _copy_to_bundle(
                            str(img or ""),
                            dest_dir=crops_dir,
                            cache=crop_map,
                            fallback_name="crop",
                        )
                    )
                row["imagenes"] = rewritten_imgs

        preview_payload = payload.get("preview_images", {})
        if isinstance(preview_payload, dict):
            for marker, img_path in list(preview_payload.items()):
                preview_payload[str(marker)] = _copy_to_bundle(
                    str(img_path or ""),
                    dest_dir=crops_dir,
                    cache=crop_map,
                    fallback_name="preview",
                )

        bindings_payload = payload.get("segment_item_bindings_by_source", {})
        if isinstance(bindings_payload, dict):
            for source_key, bucket in bindings_payload.items():
                if not isinstance(bucket, dict):
                    continue
                for seg_key, raw_payload in bucket.items():
                    if not isinstance(raw_payload, dict):
                        continue
                    crop_path = str(raw_payload.get("crop_path", "") or "").strip()
                    if crop_path:
                        raw_payload["crop_path"] = _copy_to_bundle(
                            crop_path,
                            dest_dir=crops_dir,
                            cache=crop_map,
                            fallback_name="binding",
                        )

        segments_root = self._runs_dir / "v2_segments"
        if segments_root.exists():
            for stem in sorted(source_stems):
                src_dir = segments_root / stem
                if not src_dir.exists() or (not src_dir.is_dir()):
                    continue
                dst_dir = segments_dir / self._safe_fs_name(stem, "source")
                try:
                    dst_dir.mkdir(parents=True, exist_ok=True)
                    for seg_file in src_dir.glob("*.*"):
                        if not seg_file.is_file():
                            continue
                        if seg_file.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                            continue
                        shutil.copy2(seg_file, dst_dir / seg_file.name)
                except Exception:
                    continue

        payload["session_bundle"] = {
            "mode": "portable_tmp",
            "root": "./" + str(tmp_root.relative_to(out_path.parent)).replace("\\", "/"),
            "sources_dir": "./" + str(sources_dir.relative_to(out_path.parent)).replace("\\", "/"),
            "crops_dir": "./" + str(crops_dir.relative_to(out_path.parent)).replace("\\", "/"),
            "segments_dir": "./" + str(segments_dir.relative_to(out_path.parent)).replace("\\", "/"),
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        return payload

    def _save_session_to_path(self, out_path: Path, *, show_success_popup: bool) -> bool:
        try:
            self.project_name_var.set(self._normalize_project_name(self.project_name_var.get()))
            out_path = out_path.expanduser().resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            payload = self._build_session_payload()
            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            messagebox.showerror("Sesion", f"No se pudo guardar la sesion:\n{exc}")
            return False

        self._session_file_path = out_path
        self._session_last_dir = out_path.parent
        self._log(f"Sesion guardada: {out_path}")
        if show_success_popup:
            messagebox.showinfo("Sesion", f"Sesion guardada en:\n{out_path}")
        return True

    def _build_session_payload(self) -> Dict[str, Any]:
        file_entries: List[Dict[str, str]] = []
        total = int(self.list_files.size())
        for i in range(total):
            label = self.list_files.get(i)
            src = self._file_map.get(label)
            if not src:
                continue
            file_entries.append({"label": str(label), "path": str(src)})

        items_payload: List[Dict[str, Any]] = []
        for archivo_origen, item, image_paths in self._items:
            items_payload.append(
                {
                    "archivo_origen": str(archivo_origen or "").strip(),
                    "item": str(item or "").strip(),
                    "imagenes": [str(p) for p in (image_paths or []) if str(p or "").strip()],
                }
            )

        segment_overrides: Dict[str, List[List[int]]] = {}
        for key, boxes in self._segmentacion_v2_overrides.items():
            segment_overrides[str(key)] = [[int(v) for v in box] for box in self._parse_boxes_payload(boxes)]
        used_segments_payload: Dict[str, List[int]] = {}
        for key, values in self._segmentation_v2_used_segments.items():
            nums_set: Set[int] = set()
            for v in (values or set()):
                try:
                    iv = int(v)
                except Exception:
                    continue
                if iv >= 0:
                    nums_set.add(iv)
            nums = sorted(nums_set)
            if nums:
                used_segments_payload[str(key)] = nums

        ocr_boxes: Dict[str, List[int]] = {}
        for key, box in self._ocr_exclusion_boxes.items():
            parsed = self._parse_boxes_payload([box])
            if parsed:
                ocr_boxes[str(key)] = [int(v) for v in parsed[0]]
        figure_boxes_payload: Dict[str, List[Dict[str, Any]]] = {}
        for key, entries in self._figure_boxes_by_source.items():
            if not isinstance(entries, list):
                continue
            bucket: List[Dict[str, Any]] = []
            for raw_entry in entries:
                entry = self._normalize_figure_box_entry(
                    raw_entry if isinstance(raw_entry, dict) else {},
                    default_source="manual",
                    default_confirmed=True,
                )
                if entry is not None:
                    bucket.append(dict(entry))
            if bucket:
                figure_boxes_payload[str(key)] = bucket
        segment_bindings_payload: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for key in list(self._segment_item_bindings_by_source.keys()):
            bucket = self._get_segment_bindings_by_source_key(str(key))
            if not bucket:
                continue
            serial_bucket: Dict[str, Dict[str, Any]] = {}
            for seg_idx, payload in bucket.items():
                serial_bucket[str(int(seg_idx))] = {
                    "item_num": int(self._safe_int(payload.get("item_num", 0), 0)),
                    "slot": self._normalize_binding_slot(str(payload.get("slot", "ENUNCIADO") or "ENUNCIADO")),
                    "marker_name": str(payload.get("marker_name", "") or "").strip(),
                    "crop_path": str(payload.get("crop_path", "") or "").strip(),
                    "confirmed": bool(self._as_bool(payload.get("confirmed"), True)),
                    "updated_at": str(payload.get("updated_at", "") or "").strip(),
                }
            if serial_bucket:
                segment_bindings_payload[str(key)] = serial_bucket

        try:
            output_text = (self.txt_out.get("1.0", "end") or "").strip()
        except Exception:
            output_text = ""

        payload: Dict[str, Any] = {
            "schema_version": 2,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "ui": {
                "db_name": (self.db_name_var.get() or "").strip(),
                "provider": FIXED_PROVIDER,
                "model": (self.model_var.get() or "").strip(),
                "format_model": (self.format_model_var.get() or "").strip(),
                "project_name": self._normalize_project_name(self.project_name_var.get()),
                "timeout_s": int(self.timeout_var.get()),
                "retries": int(self.retries_var.get()),
                "skip_done": bool(self.skip_done_var.get()),
                "ocr_lang": (self.ocr_lang_var.get() or "").strip(),
                "auto_format": bool(self.auto_format_var.get()),
                "format_with_llm": bool(self.format_with_llm_var.get()),
                "auto_crop": bool(self.auto_crop_var.get()),
                "debug_detect": bool(self.debug_detect_var.get()),
                "detect_figure": bool(self.detect_figure_var.get()),
                "tag_mode": (self.tag_mode_var.get() or "").strip(),
                "step3_solucion": (self.step3_solucion_var.get() or "").strip(),
                "step3_add_solucion": bool(self.step3_agregar_solucion_var.get()),
                "curso": (self.curso_var.get() or "").strip(),
                "tema": (self.tema_var.get() or "").strip(),
                "subtema": (self.subtema_var.get() or "").strip(),
                "rate_in": (self.rate_in_var.get() or "").strip(),
                "rate_out": (self.rate_out_var.get() or "").strip(),
            },
            "usage": {
                "input_tokens": int(self._session_input_tokens),
                "output_tokens": int(self._session_output_tokens),
                "total_tokens": int(self._session_total_tokens),
                "estimated_usd": float(self._session_estimated_usd),
            },
            "files": file_entries,
            "items": items_payload,
            "output_text": output_text,
            "step3_last_claves_input": str(self._step3_last_claves_input or ""),
            "transcribed_by_label": {str(k): str(v) for k, v in self._transcribed_by_label.items()},
            "ocr_raw_first_by_label": {str(k): str(v) for k, v in self._ocr_raw_first_by_label.items()},
            "geometry_pass_by_label": {str(k): str(v) for k, v in self._geometry_pass_by_label.items()},
            "geometry_pass_payload_by_label": {
                str(k): dict(v) for k, v in self._geometry_pass_payload_by_label.items()
            },
            "ocr_merge_applied_by_label": {
                str(k): str(v) for k, v in self._ocr_merge_applied_by_label.items()
            },
            "training_pairs_by_item": {
                str(k): dict(v) for k, v in self._training_pairs_by_item.items()
            },
            "direct_item_diagnostics_by_label": {
                str(k): list(v) for k, v in self._direct_item_diagnostics_by_label.items()
            },
            "yolo_figure_suggestions_by_source": {
                str(k): dict(v) for k, v in self._yolo_figure_suggestions_by_source.items()
            },
            "preview_images": {str(k): str(v) for k, v in self._preview_images.items()},
            "corrected_items": sorted(int(n) for n in self._corrected_item_numbers if int(n) > 0),
            "segmentation": {
                "overrides": segment_overrides,
                "reviewed_sources": sorted(str(v) for v in self._segmentation_reviewed_sources),
                "review_done": bool(self._segmentation_review_done),
                "used_segments": used_segments_payload,
            },
            "figure_boxes_by_source": figure_boxes_payload,
            "ocr_exclusion_boxes": ocr_boxes,
            "segment_item_bindings_by_source": segment_bindings_payload,
        }
        return payload

    def _save_session_quick(self) -> None:
        if self._transcribing:
            messagebox.showwarning("Sesion", "Espera a que termine la transcripcion actual para guardar sesion.")
            return
        if self._session_file_path is None:
            self._save_session_dialog()
            return
        self._save_session_to_path(self._session_file_path, show_success_popup=False)

    def _save_session_dialog(self) -> None:
        if self._transcribing:
            messagebox.showwarning("Sesion", "Espera a que termine la transcripcion actual para guardar sesion.")
            return
        default_name = self._session_file_path.name if self._session_file_path else self._default_session_filename()
        path = filedialog.asksaveasfilename(
            title="Guardar sesion",
            initialdir=str(self._session_last_dir),
            initialfile=default_name,
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("Todos", "*.*")],
        )
        if not path:
            return
        self._save_session_to_path(Path(path), show_success_popup=True)

    def _load_session_dialog(self) -> None:
        if self._transcribing:
            messagebox.showwarning("Sesion", "Espera a que termine la transcripcion actual para cargar sesion.")
            return
        path = filedialog.askopenfilename(
            title="Cargar sesion",
            initialdir=str(self._session_last_dir),
            filetypes=[("JSON", "*.json"), ("Todos", "*.*")],
        )
        if not path:
            return
        session_path = Path(path)
        try:
            payload = json.loads(session_path.read_text(encoding="utf-8"))
        except Exception as exc:
            messagebox.showerror("Sesion", f"No se pudo leer la sesion:\n{exc}")
            return
        self._apply_loaded_session(payload=payload, session_path=session_path)

    def _apply_loaded_session(self, *, payload: Dict[str, Any], session_path: Path) -> None:
        ui = payload.get("ui", {}) if isinstance(payload, dict) else {}
        usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
        files_data = payload.get("files", []) if isinstance(payload, dict) else []
        items_data = payload.get("items", []) if isinstance(payload, dict) else []
        output_text = (payload.get("output_text", "") if isinstance(payload, dict) else "") or ""
        step3_last_input = (payload.get("step3_last_claves_input", "") if isinstance(payload, dict) else "") or ""
        transcribed_map = payload.get("transcribed_by_label", {}) if isinstance(payload, dict) else {}
        ocr_raw_map = payload.get("ocr_raw_first_by_label", {}) if isinstance(payload, dict) else {}
        geometry_map = payload.get("geometry_pass_by_label", {}) if isinstance(payload, dict) else {}
        geometry_payload_map = payload.get("geometry_pass_payload_by_label", {}) if isinstance(payload, dict) else {}
        ocr_merge_map = payload.get("ocr_merge_applied_by_label", {}) if isinstance(payload, dict) else {}
        training_pairs_map = payload.get("training_pairs_by_item", {}) if isinstance(payload, dict) else {}
        direct_diag_map = payload.get("direct_item_diagnostics_by_label", {}) if isinstance(payload, dict) else {}
        yolo_suggestions_map = payload.get("yolo_figure_suggestions_by_source", {}) if isinstance(payload, dict) else {}
        preview_map = payload.get("preview_images", {}) if isinstance(payload, dict) else {}
        corrected_raw = payload.get("corrected_items", []) if isinstance(payload, dict) else []
        segmentation = payload.get("segmentation", {}) if isinstance(payload, dict) else {}
        overrides_raw = segmentation.get("overrides", {}) if isinstance(segmentation, dict) else {}
        reviewed_raw = segmentation.get("reviewed_sources", []) if isinstance(segmentation, dict) else []
        review_done_raw = segmentation.get("review_done", False) if isinstance(segmentation, dict) else False
        used_segments_raw = segmentation.get("used_segments", {}) if isinstance(segmentation, dict) else {}
        figure_boxes_raw = payload.get("figure_boxes_by_source", {}) if isinstance(payload, dict) else {}
        ocr_boxes_raw = payload.get("ocr_exclusion_boxes", {}) if isinstance(payload, dict) else {}
        segment_bindings_raw = payload.get("segment_item_bindings_by_source", {}) if isinstance(payload, dict) else {}

        self._file_map.clear()
        self.list_files.delete(0, "end")
        self._items.clear()
        self._transcribed_by_label.clear()
        self._ocr_raw_first_by_label.clear()
        self._geometry_pass_by_label.clear()
        self._geometry_pass_payload_by_label.clear()
        self._ocr_merge_applied_by_label.clear()
        self._training_pairs_by_item.clear()
        self._direct_item_diagnostics_by_label.clear()
        self._yolo_figure_suggestions_by_source.clear()
        self._preview_images.clear()
        self._missing_marker_warned.clear()
        self._corrected_item_numbers.clear()
        self._segmentacion_v2_overrides.clear()
        self._segmentation_v2_used_segments.clear()
        self._figure_boxes_by_source.clear()
        self._ocr_exclusion_boxes.clear()
        self._segment_item_bindings_by_source.clear()
        self._segmentation_reviewed_sources.clear()
        self._segmentation_review_done = False
        self._segmentation_route_active = False
        self._segmentation_scope_labels = None
        self._step3_last_claves_input = ""

        # Restore UI configuration first.
        self.db_name_var.set(str(ui.get("db_name", self.db_name_var.get()) or "").strip())
        model = str(ui.get("model", self.model_var.get()) or "").strip()
        format_model = str(ui.get("format_model", self.format_model_var.get()) or "").strip()
        project_raw = str(ui.get("project_name", "") or "").strip()
        loaded_project = self._normalize_project_name(project_raw if project_raw else session_path.stem)
        self.project_name_var.set(loaded_project)
        self.model_var.set(model if model in HF_MODELS else DEFAULT_HF_VISION_MODEL)
        self.format_model_var.set(format_model if format_model in HF_FORMAT_MODELS else DEFAULT_HF_FORMAT_MODEL)
        self.timeout_var.set(max(30, min(self._safe_int(ui.get("timeout_s"), 180), 600)))
        self.retries_var.set(max(0, min(self._safe_int(ui.get("retries"), 2), 5)))
        self.skip_done_var.set(self._as_bool(ui.get("skip_done"), False))
        self.ocr_lang_var.set(str(ui.get("ocr_lang", self.ocr_lang_var.get()) or "spa+eng").strip())
        self.auto_format_var.set(self._as_bool(ui.get("auto_format"), True))
        self.format_with_llm_var.set(self._as_bool(ui.get("format_with_llm"), True))
        self.auto_crop_var.set(self._as_bool(ui.get("auto_crop"), True))
        self.debug_detect_var.set(self._as_bool(ui.get("debug_detect"), False))
        self.detect_figure_var.set(self._as_bool(ui.get("detect_figure"), True))
        tag_mode = str(ui.get("tag_mode", self.tag_mode_var.get()) or "").strip()
        if tag_mode not in {TAG_MODE_MANUAL, TAG_MODE_AUTO, TAG_MODE_MIXED}:
            tag_mode = FIXED_TAG_MODE
        self.tag_mode_var.set(tag_mode)
        self.step3_solucion_var.set(str(ui.get("step3_solucion", self.step3_solucion_var.get()) or "pendiente").strip())
        self.step3_agregar_solucion_var.set(self._as_bool(ui.get("step3_add_solucion"), False))
        self.curso_var.set(str(ui.get("curso", self.curso_var.get()) or "").strip())
        self.tema_var.set(str(ui.get("tema", self.tema_var.get()) or "").strip())
        self.subtema_var.set(str(ui.get("subtema", self.subtema_var.get()) or "").strip())
        self.rate_in_var.set(str(ui.get("rate_in", self.rate_in_var.get()) or "0.0").strip())
        self.rate_out_var.set(str(ui.get("rate_out", self.rate_out_var.get()) or "0.0").strip())

        missing_files = 0
        loaded_files = 0
        if isinstance(files_data, list):
            for row in files_data:
                if not isinstance(row, dict):
                    continue
                path_raw = str(row.get("path", "") or "").strip()
                if not path_raw:
                    continue
                src = Path(path_raw)
                if not src.exists():
                    missing_files += 1
                    continue
                base_label = str(row.get("label", "") or src.name).strip() or src.name
                label = base_label
                suffix = 1
                while label in self._file_map:
                    suffix += 1
                    label = f"{base_label} ({suffix})"
                self._file_map[label] = src
                self.list_files.insert("end", label)
                loaded_files += 1

        if isinstance(overrides_raw, dict):
            for key, boxes_raw in overrides_raw.items():
                parsed = self._parse_boxes_payload(boxes_raw)
                self._segmentacion_v2_overrides[str(key)] = parsed
        if isinstance(used_segments_raw, dict):
            for key, vals in used_segments_raw.items():
                bucket: Set[int] = set()
                if isinstance(vals, list):
                    for v in vals:
                        try:
                            iv = int(v)
                        except Exception:
                            continue
                        if iv >= 0:
                            bucket.add(iv)
                if bucket:
                    self._segmentation_v2_used_segments[str(key)] = bucket

        if isinstance(figure_boxes_raw, dict):
            for key, entries_raw in figure_boxes_raw.items():
                bucket: List[Dict[str, Any]] = []
                if not isinstance(entries_raw, list):
                    continue
                for raw_entry in entries_raw:
                    entry = self._normalize_figure_box_entry(
                        raw_entry if isinstance(raw_entry, dict) else {},
                        default_source="manual",
                        default_confirmed=True,
                    )
                    if entry is not None:
                        bucket.append(entry)
                if bucket:
                    self._figure_boxes_by_source[str(key)] = bucket

        if isinstance(ocr_boxes_raw, dict):
            for key, box_raw in ocr_boxes_raw.items():
                parsed = self._parse_boxes_payload([box_raw])
                if parsed:
                    self._ocr_exclusion_boxes[str(key)] = parsed[0]
                    if str(key) not in self._figure_boxes_by_source:
                        x1, y1, x2, y2 = [int(v) for v in parsed[0]]
                        self._figure_boxes_by_source[str(key)] = [
                            {
                                "bbox_px": [x1, y1, x2, y2],
                                "source": "manual",
                                "conf": 1.0,
                                "item_hint": None,
                                "confirmed": True,
                                "updated_at": datetime.now().isoformat(timespec="seconds"),
                            }
                        ]
        for key in set(self._figure_boxes_by_source.keys()) | set(self._ocr_exclusion_boxes.keys()):
            self._sync_legacy_ocr_box_for_source_key(str(key))

        if isinstance(segment_bindings_raw, dict):
            for source_key, bucket_raw in segment_bindings_raw.items():
                if not isinstance(bucket_raw, dict):
                    continue
                parsed_bucket: Dict[int, Dict[str, Any]] = {}
                for seg_k, payload_raw in bucket_raw.items():
                    if not isinstance(payload_raw, dict):
                        continue
                    try:
                        seg_idx = int(seg_k)
                    except Exception:
                        continue
                    if seg_idx < 0:
                        continue
                    item_num = int(self._safe_int(payload_raw.get("item_num", 0), 0))
                    if item_num <= 0:
                        continue
                    slot = self._normalize_binding_slot(str(payload_raw.get("slot", "ENUNCIADO") or "ENUNCIADO"))
                    marker_name = str(payload_raw.get("marker_name", "") or "").strip()
                    if not marker_name:
                        marker_name = self._build_binding_marker_name(item_num=item_num, slot=slot)
                    crop_path = str(payload_raw.get("crop_path", "") or "").strip()
                    parsed_bucket[seg_idx] = {
                        "item_num": item_num,
                        "slot": slot,
                        "marker_name": marker_name,
                        "crop_path": crop_path,
                        "confirmed": bool(self._as_bool(payload_raw.get("confirmed"), True)),
                        "updated_at": str(payload_raw.get("updated_at", "") or "").strip() or datetime.now().isoformat(timespec="seconds"),
                    }
                if parsed_bucket:
                    self._set_segment_bindings_by_source_key(str(source_key), parsed_bucket)

        if isinstance(reviewed_raw, list):
            for key in reviewed_raw:
                key_text = str(key or "").strip().lower()
                if key_text:
                    self._segmentation_reviewed_sources.add(key_text)
        self._segmentation_review_done = self._as_bool(review_done_raw, False)
        self._refresh_segmentation_done_state()

        if isinstance(items_data, list):
            for row in items_data:
                if not isinstance(row, dict):
                    continue
                archivo_origen = str(row.get("archivo_origen", "") or "").strip()
                item_txt = str(row.get("item", "") or "").strip()
                if not item_txt:
                    continue
                imgs_raw = row.get("imagenes", [])
                imgs = [str(p) for p in imgs_raw] if isinstance(imgs_raw, list) else []
                self._items.append((archivo_origen, item_txt, imgs))

        if isinstance(transcribed_map, dict):
            self._transcribed_by_label = {str(k): str(v) for k, v in transcribed_map.items()}
        if isinstance(ocr_raw_map, dict):
            self._ocr_raw_first_by_label = {str(k): str(v) for k, v in ocr_raw_map.items()}
        if isinstance(geometry_map, dict):
            self._geometry_pass_by_label = {str(k): str(v) for k, v in geometry_map.items()}
        if isinstance(geometry_payload_map, dict):
            parsed_payload_map: Dict[str, Dict[str, Any]] = {}
            for key, raw_val in geometry_payload_map.items():
                if not isinstance(raw_val, dict):
                    continue
                parsed_payload_map[str(key)] = {
                    "razonamiento_es": self.controller.normalizar_item_una_linea(
                        str(raw_val.get("razonamiento_es", "") or "")
                    ),
                    "elementos_geometricos": self._extract_json_list_field(raw_val, "elementos_geometricos"),
                    "expresiones_sin_dolares": self._extract_json_list_field(raw_val, "expresiones_sin_dolares"),
                    "alertas": self._extract_json_list_field(raw_val, "alertas"),
                }
            self._geometry_pass_payload_by_label = parsed_payload_map
        if isinstance(ocr_merge_map, dict):
            self._ocr_merge_applied_by_label = {str(k): str(v) for k, v in ocr_merge_map.items()}
        if isinstance(training_pairs_map, dict):
            parsed_pairs: Dict[str, Dict[str, Any]] = {}
            for key, value in training_pairs_map.items():
                if not isinstance(value, dict):
                    continue
                parsed_pairs[str(key)] = dict(value)
            self._training_pairs_by_item = parsed_pairs
        if isinstance(direct_diag_map, dict):
            parsed_diag: Dict[str, List[Dict[str, Any]]] = {}
            for key, value in direct_diag_map.items():
                if not isinstance(value, list):
                    continue
                bucket: List[Dict[str, Any]] = []
                for entry in value:
                    if isinstance(entry, dict):
                        bucket.append(dict(entry))
                parsed_diag[str(key)] = bucket
            self._direct_item_diagnostics_by_label = parsed_diag
        if isinstance(yolo_suggestions_map, dict):
            parsed_suggestions: Dict[str, Dict[str, Any]] = {}
            for key, value in yolo_suggestions_map.items():
                if not isinstance(value, dict):
                    continue
                try:
                    conf = float(value.get("conf", 0.0) or 0.0)
                except Exception:
                    conf = 0.0
                bbox_norm_raw = value.get("bbox_norm")
                bbox_norm: Optional[Tuple[float, float, float, float]] = None
                if isinstance(bbox_norm_raw, (list, tuple)) and len(bbox_norm_raw) >= 4:
                    try:
                        x1, y1, x2, y2 = [float(v) for v in bbox_norm_raw[:4]]
                        bbox_norm = (
                            max(0.0, min(1.0, x1)),
                            max(0.0, min(1.0, y1)),
                            max(0.0, min(1.0, x2)),
                            max(0.0, min(1.0, y2)),
                        )
                    except Exception:
                        bbox_norm = None
                source_name = str(value.get("source", "none") or "none").strip().lower()
                if source_name not in {"yolo", "yolo_cache", "manual", "none", "manual_box", "manual_marker"}:
                    source_name = "none"
                parsed_suggestions[str(key)] = {
                    "has_figure": bool(value.get("has_figure", False)),
                    "bbox_norm": bbox_norm,
                    "conf": max(0.0, min(1.0, conf)),
                    "source": source_name,
                    "updated_at": str(value.get("updated_at", "") or "").strip(),
                }
            self._yolo_figure_suggestions_by_source = parsed_suggestions
        if isinstance(preview_map, dict):
            self._preview_images = {str(k): str(v) for k, v in preview_map.items()}
        migrated_bindings = self._migrate_legacy_preview_bindings()
        if migrated_bindings > 0:
            self._log(
                f"Migracion legacy: {migrated_bindings} binding(s) marker->item/slot creados (confirmed=false)."
            )
        if isinstance(corrected_raw, list):
            for v in corrected_raw:
                try:
                    n = int(v)
                except Exception:
                    continue
                if n > 0:
                    self._corrected_item_numbers.add(n)
        self._step3_last_claves_input = str(step3_last_input or "").strip()

        self._session_input_tokens = max(0, self._safe_int(usage.get("input_tokens"), 0))
        self._session_output_tokens = max(0, self._safe_int(usage.get("output_tokens"), 0))
        self._session_total_tokens = max(0, self._safe_int(usage.get("total_tokens"), 0))
        try:
            self._session_estimated_usd = max(0.0, float(usage.get("estimated_usd", 0.0) or 0.0))
        except Exception:
            self._session_estimated_usd = 0.0
        self._refresh_usage_summary()

        if self._items:
            self._render_output_from_items()
        else:
            self.txt_out.delete("1.0", "end")
            if str(output_text).strip():
                self.txt_out.insert("end", str(output_text).strip() + "\n")
            self._push_preview_text(force=True)
        self._refresh_training_pairs_from_items()

        self._on_provider_change()
        self._session_file_path = session_path.resolve()
        self._session_last_dir = self._session_file_path.parent
        self._refresh_image_list_colors()
        self._log(
            f"Sesion cargada: {session_path} | archivos={loaded_files} | items={len(self._items)} | faltantes={missing_files}"
        )
        messagebox.showinfo(
            "Sesion",
            f"Sesion cargada.\nArchivos: {loaded_files}\nItems: {len(self._items)}\nFaltantes: {missing_files}",
        )

    def _export_training_dataset_dialog(self) -> None:
        if self._transcribing:
            messagebox.showwarning("Dataset", "Espera a que termine la transcripcion para exportar dataset.")
            return
        sources = self._collect_dataset_sources()
        if not sources:
            messagebox.showwarning("Dataset", "No hay imagenes base para exportar dataset.")
            return

        base_dir = filedialog.askdirectory(
            title="Selecciona carpeta destino para el dataset",
            initialdir=str(self._datasets_dir),
            mustexist=True,
        )
        if not base_dir:
            return
        export_yolo = messagebox.askyesno(
            "Dataset",
            "¿Exportar tambien dataset YOLO bbox (figura_problema)?",
            default=messagebox.YES,
        )

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dataset_dir = Path(base_dir) / f"dataset_transcriptor_{ts}"
        suffix = 1
        while dataset_dir.exists():
            suffix += 1
            dataset_dir = Path(base_dir) / f"dataset_transcriptor_{ts}_{suffix}"

        try:
            sources_dir = dataset_dir / "sources"
            masks_dir = dataset_dir / "ocr_masked"
            segments_dir = dataset_dir / "segments"
            sources_dir.mkdir(parents=True, exist_ok=True)
            masks_dir.mkdir(parents=True, exist_ok=True)
            segments_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            messagebox.showerror("Dataset", f"No se pudo crear carpeta destino:\n{exc}")
            return

        items_by_source = self._group_items_by_source()
        samples: List[Dict[str, Any]] = []
        exported_segments = 0
        exported_masks = 0
        failed_sources = 0

        for idx, (label, src) in enumerate(sources, start=1):
            try:
                sample_id = f"{idx:04d}_{self._safe_fs_name(src.stem, f'source_{idx:04d}')}"
                ext = src.suffix if src.suffix else ".png"
                source_copy = sources_dir / f"{sample_id}{ext}"
                shutil.copy2(src, source_copy)

                source_key = self._seg_v2_source_key(src)
                seg_objs = self._get_segments_v2_for_source(src)
                segment_records: List[Dict[str, Any]] = []
                seg_boxes_for_mask: List[Tuple[int, int, int, int]] = []
                seg_subdir = segments_dir / sample_id
                seg_subdir.mkdir(parents=True, exist_ok=True)

                for s_idx, seg in enumerate(seg_objs, start=1):
                    box = tuple(int(v) for v in seg.bbox)
                    seg_boxes_for_mask.append(box)
                    dst = seg_subdir / f"seg_{s_idx:02d}.png"
                    try:
                        shutil.copy2(seg.image_path, dst)
                        exported_segments += 1
                        seg_rel = str(dst.relative_to(dataset_dir)).replace("\\", "/")
                    except Exception:
                        seg_rel = ""
                    segment_records.append(
                        {
                            "idx": s_idx,
                            "bbox_px": [int(v) for v in box],
                            "crop_path": seg_rel,
                        }
                    )

                figure_boxes = self._get_figure_boxes(src)
                manual_boxes: List[Tuple[int, int, int, int]] = []
                for entry in figure_boxes:
                    if str(entry.get("source", "")).strip().lower() != "manual":
                        continue
                    if not self._as_bool(entry.get("confirmed"), True):
                        continue
                    bbox_raw = entry.get("bbox_px")
                    if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) < 4:
                        continue
                    try:
                        manual_boxes.append(tuple(int(v) for v in bbox_raw[:4]))
                    except Exception:
                        continue
                all_mask_boxes = list(seg_boxes_for_mask)
                all_mask_boxes.extend(manual_boxes)

                masked_rel = ""
                if all_mask_boxes:
                    masked_out = masks_dir / f"{sample_id}_masked.png"
                    masked_path = self._save_masked_copy_for_dataset(
                        source_path=src,
                        boxes_px=all_mask_boxes,
                        out_path=masked_out,
                    )
                    if masked_path and masked_path.exists():
                        exported_masks += 1
                        masked_rel = str(masked_path.relative_to(dataset_dir)).replace("\\", "/")

                samples.append(
                    {
                        "sample_id": sample_id,
                        "source_label": label,
                        "source_path_original": str(src),
                        "source_key": source_key,
                        "source_copy_path": str(source_copy.relative_to(dataset_dir)).replace("\\", "/"),
                        "segmentation_reviewed": source_key in self._segmentation_reviewed_sources,
                        "manual_figure_boxes_px": [[int(v) for v in box] for box in manual_boxes],
                        "manual_figure_box_count": len(manual_boxes),
                        "masked_ocr_input_path": masked_rel or None,
                        "segments": segment_records,
                        "items": items_by_source.get(src.stem, []),
                    }
                )
            except Exception as exc:
                failed_sources += 1
                self._log(f"Dataset: fallo exportando {src.name}: {exc}")
                continue

        manifest: Dict[str, Any] = {
            "schema_version": 2,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "dataset_kind": "ocr_segmentation_training",
            "source_count": len(samples),
            "failed_sources": failed_sources,
            "exported_segments": exported_segments,
            "exported_masks": exported_masks,
            "settings": {
                "provider": FIXED_PROVIDER,
                "ocr_model": (self.model_var.get() or "").strip(),
                "format_model": (self.format_model_var.get() or "").strip(),
                "tag_mode": (self.tag_mode_var.get() or "").strip(),
                "curso_hint": (self.curso_var.get() or "").strip(),
                "tema_hint": (self.tema_var.get() or "").strip(),
                "subtema_hint": (self.subtema_var.get() or "").strip(),
            },
            "samples": samples,
        }

        try:
            self._sync_items_from_output_text()
        except Exception:
            pass
        self._refresh_training_pairs_from_items()
        text_rows = self._build_training_pairs_jsonl_rows()
        text_pairs_path = dataset_dir / "pairs_texto.jsonl"
        yolo_stats = {"images": 0, "labels": 0, "boxes": 0}
        if export_yolo:
            yolo_stats = self._export_yolo_bbox_dataset(dataset_dir=dataset_dir, sources=sources)
        manifest["text_pairs_count"] = len(text_rows)
        manifest["yolo_exported"] = bool(export_yolo)
        manifest["yolo_stats"] = dict(yolo_stats)

        try:
            with text_pairs_path.open("w", encoding="utf-8") as fh:
                for row in text_rows:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            (dataset_dir / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (dataset_dir / "session_snapshot.json").write_text(
                json.dumps(self._build_session_payload(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            messagebox.showerror("Dataset", f"Error escribiendo archivos de dataset:\n{exc}")
            return

        self._log(
            f"Dataset exportado: {dataset_dir} | fuentes={len(samples)} | segmentos={exported_segments} | mascaras={exported_masks} | pares_texto={len(text_rows)} | yolo_boxes={yolo_stats.get('boxes', 0)} | fallos={failed_sources}"
        )
        messagebox.showinfo(
            "Dataset",
            f"Dataset exportado en:\n{dataset_dir}\n\nFuentes: {len(samples)}\nSegmentos: {exported_segments}\nMascaras OCR: {exported_masks}\nPares texto JSONL: {len(text_rows)}\nYOLO boxes: {yolo_stats.get('boxes', 0)}\nFallos: {failed_sources}",
        )

    def _usage_triplet(self, usage) -> tuple[Optional[int], Optional[int], Optional[int]]:
        if usage is None:
            return (None, None, None)

        def _read(name: str, alt: str | None = None):
            if isinstance(usage, dict):
                if name in usage:
                    return usage.get(name)
                if alt and alt in usage:
                    return usage.get(alt)
                return None
            val = getattr(usage, name, None)
            if val is not None:
                return val
            if alt:
                return getattr(usage, alt, None)
            return None

        def _to_int(value) -> Optional[int]:
            try:
                if value is None:
                    return None
                return int(value)
            except Exception:
                return None

        input_tokens = _to_int(_read("input_tokens", "prompt_tokens"))
        output_tokens = _to_int(_read("output_tokens", "completion_tokens"))
        total_tokens = _to_int(_read("total_tokens"))
        return (input_tokens, output_tokens, total_tokens)

    def _log_usage(self, *, provider: str, model: str, label: str, usage) -> None:
        in_tokens, out_tokens, total_tokens = self._usage_triplet(usage)
        if in_tokens is None and out_tokens is None and total_tokens is None:
            return

        call_cost = self._estimate_cost_usd(in_tokens, out_tokens)
        self.after(
            0,
            lambda p=provider, m=model, l=label, i=in_tokens, o=out_tokens, t=total_tokens, c=call_cost: self._apply_usage_event(
                provider=p,
                model=m,
                label=l,
                input_tokens=i,
                output_tokens=o,
                total_tokens=t,
                call_cost=c,
            ),
        )

    def _apply_usage_event(
        self,
        *,
        provider: str,
        model: str,
        label: str,
        input_tokens: Optional[int],
        output_tokens: Optional[int],
        total_tokens: Optional[int],
        call_cost: float,
    ) -> None:
        self._session_input_tokens += input_tokens or 0
        self._session_output_tokens += output_tokens or 0
        self._session_total_tokens += total_tokens or 0
        self._session_estimated_usd += call_cost
        self._refresh_usage_summary()

        parts: List[str] = []
        parts.append(f"in={input_tokens or 0}")
        parts.append(f"out={output_tokens or 0}")
        if total_tokens is not None:
            parts.append(f"total={total_tokens}")
        parts.append(f"est_usd={call_cost:.6f}")
        details = ", ".join(parts)
        self._log(f"Uso tokens [{provider} | {model} | {label}]: {details}")

    def _on_provider_change(self, _event=None) -> None:
        # UI sin selectores: forzar flujo fijo en todo momento.
        self.provider_var.set(FIXED_PROVIDER)
        current_model = (self.model_var.get() or "").strip()
        if current_model not in HF_MODELS:
            self.model_var.set(DEFAULT_HF_VISION_MODEL)
        current_format = (self.format_model_var.get() or "").strip()
        if current_format not in HF_FORMAT_MODELS:
            self.format_model_var.set(DEFAULT_HF_FORMAT_MODEL)
        current_tag = (self.tag_mode_var.get() or "").strip()
        if current_tag not in {TAG_MODE_MANUAL, TAG_MODE_AUTO, TAG_MODE_MIXED}:
            self.tag_mode_var.set(FIXED_TAG_MODE)

    def _resolve_hf_token(self) -> str:
        return (
            (self.hf_token_var.get() or "").strip()
            or (os.getenv("HF_TOKEN", "") or "").strip()
            or (os.getenv("HUGGINGFACEHUB_API_TOKEN", "") or "").strip()
        )

    def _resolve_hf_base_url(self) -> str:
        # Flujo fijo por router HF (sin endpoint dedicado configurable).
        return HF_BASE_URL_DEFAULT

    def _short_hf_probe_error(self, err: Exception | str) -> str:
        msg = str(err or "").strip()
        low = msg.lower()
        if "not_found" in low or "model not found" in low:
            return "NOT_FOUND"
        if "model_not_supported" in low or "not supported by any provider" in low:
            return "NOT_SUPPORTED"
        if "401" in low:
            return "401_UNAUTHORIZED"
        if "403" in low:
            return "403_FORBIDDEN"
        if "429" in low:
            return "429_RATE_LIMIT"
        if "timeout" in low or "timed out" in low:
            return "TIMEOUT"
        return "ERROR"

    def _probe_hf_vision_models_async(self, auto: bool = False) -> None:
        if self._hf_probe_running:
            if not auto:
                self._log("Verificacion HF ya esta en curso.")
            return
        token = self._resolve_hf_token()
        if not token:
            self._log("Verificacion HF omitida: falta HF_TOKEN.")
            return

        models = list(dict.fromkeys(HF_VISION_PROBE_MODELS))
        if not models:
            self._log("Verificacion HF omitida: no hay modelos configurados.")
            return

        self._hf_probe_running = True
        started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._log(f"Verificando disponibilidad HF ({len(models)} modelos) - {started_at} ...")

        def worker() -> None:
            available: List[str] = []
            unavailable: Dict[str, str] = {}
            base_url = self._resolve_hf_base_url()
            probe_image = "https://endpoints.hf.co/media-examples/img1.png"
            try:
                client = OpenAI(base_url=base_url, api_key=token, timeout=45)
            except Exception as exc:
                self.after(0, lambda e=exc: self._log(f"Verificacion HF fallo: {e}"))
                self.after(0, lambda: setattr(self, "_hf_probe_running", False))
                return

            for model_name in models:
                try:
                    _resp = client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": "Describe in five words."},
                                    {"type": "image_url", "image_url": {"url": probe_image}},
                                ],
                            }
                        ],
                        temperature=0,
                        max_tokens=20,
                    )
                    available.append(model_name)
                except Exception as exc:
                    unavailable[model_name] = self._short_hf_probe_error(exc)

            def finalize() -> None:
                if not self._ui_alive():
                    self._hf_probe_running = False
                    return
                self._hf_probe_running = False
                self._hf_available_vision_models = list(available)
                self._hf_unavailable_vision_models = dict(unavailable)
                self._hf_probe_last_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                combo_model = getattr(self, "combo_model", None)
                if available and self._widget_alive(combo_model):
                    try:
                        combo_model.configure(values=available)
                        current = (self.model_var.get() or "").strip()
                        if current not in available:
                            self.model_var.set(available[0])
                    except Exception:
                        pass

                avail_text = ", ".join(available) if available else "(ninguno)"
                self._log(f"HF disponibles ({len(available)}): {avail_text}")
                if unavailable:
                    parts = [f"{m}={reason}" for m, reason in unavailable.items()]
                    self._log(f"HF no disponibles ({len(unavailable)}): " + " | ".join(parts))
                self._log(f"Disponibilidad HF actualizada: {self._hf_probe_last_ts}")

            try:
                self.after(0, finalize)
            except Exception:
                self._hf_probe_running = False

        threading.Thread(target=worker, daemon=True).start()

    def _resolve_hf_fallback_base_url(self) -> str:
        return HF_BASE_URL_DEFAULT

    def _resolve_hf_fallback_model(self) -> str:
        model = (os.getenv("HF_FALLBACK_MODEL", "") or "").strip()
        return model or "Qwen/Qwen2.5-VL-7B-Instruct"

    def _is_hf_dedicated_endpoint_url(self, base_url: str) -> bool:
        return "endpoints.huggingface.cloud" in (base_url or "").strip().lower()

    def _is_deepseek_ocr_model(self, model: str) -> bool:
        # Proyecto actual: DeepSeek deshabilitado por decision de flujo.
        return False

    def _prefer_hf_native_ocr_endpoint(self) -> bool:
        # Endpoint dedicado deshabilitado en este proyecto.
        return False

    def _use_hf_native_ocr_endpoint(self, model: str) -> bool:
        return False

    def _resolve_hf_endpoint_native_url(self) -> str:
        base = HF_BASE_URL_DEFAULT.rstrip("/")
        if base.lower().endswith("/v1"):
            base = base[:-3].rstrip("/")
        return base

    def _resolve_hf_format_model(self, vision_model: str) -> str:
        model = (self.format_model_var.get() or "").strip()
        if model in HF_FORMAT_MODELS:
            return model
        return DEFAULT_HF_FORMAT_MODEL

    def _resolve_openai_format_model(self, vision_model: str) -> str:
        explicit = (self.format_model_var.get() or "").strip()
        if explicit:
            return explicit
        explicit = (os.getenv("OPENAI_FORMAT_MODEL", "") or "").strip()
        if explicit:
            return explicit
        base = (vision_model or "").strip()
        if not base:
            return OPENAI_FORMAT_MODELS[0]
        return base

    def _resolve_openai_key(self) -> str:
        return (os.getenv("OPENAI_API_KEY", "") or "").strip()

    def _is_credit_depleted_error(self, err: Exception | str) -> bool:
        msg = str(err or "").lower()
        if "402" not in msg:
            return False
        keywords = (
            "credit",
            "credits",
            "balance",
            "depleted",
            "insufficient",
            "quota",
            "billing",
            "pre-paid",
        )
        return any(k in msg for k in keywords)

    def _is_hf_model_unavailable_error(self, err: Exception | str) -> bool:
        msg = str(err or "").lower()
        signals = (
            "model not found",
            "not_found",
            "model_not_supported",
            "not supported by any provider",
            "inaccessible",
            "not deployed",
            "param': 'model'",
        )
        return any(s in msg for s in signals)

    def _resolve_detect_model(self, provider: str, vision_model: str) -> str:
        model = (vision_model or "").strip()
        if model in HF_MODELS:
            return model
        return DEFAULT_HF_VISION_MODEL

    def _resolve_figure_min_conf(self) -> float:
        raw = (os.getenv("FIG_DETECT_MIN_CONF", "0.45") or "0.45").strip().replace(",", ".")
        try:
            val = float(raw)
        except Exception:
            val = 0.45
        return max(0.0, min(1.0, val))

    def _resolve_bbox_detector_model_path(self) -> str:
        candidates = (
            (os.getenv("YOLO_FIGURE_MODEL", "") or "").strip(),
            (os.getenv("YOLO_BBOX_MODEL", "") or "").strip(),
            (os.getenv("FIGURE_DETECTOR_MODEL", "") or "").strip(),
        )
        for value in candidates:
            if not value:
                continue
            try:
                if Path(value).expanduser().resolve().exists():
                    return str(Path(value).expanduser().resolve())
            except Exception:
                continue
        return ""

    def _get_cached_yolo_suggestion(self, source_path: Path) -> Optional[Dict[str, Any]]:
        key = self._seg_v2_source_key(source_path)
        raw = self._yolo_figure_suggestions_by_source.get(key)
        if not isinstance(raw, dict):
            return None
        bbox_norm_raw = raw.get("bbox_norm")
        bbox_norm: Optional[Tuple[float, float, float, float]] = None
        if isinstance(bbox_norm_raw, (list, tuple)) and len(bbox_norm_raw) >= 4:
            try:
                x1, y1, x2, y2 = [float(v) for v in bbox_norm_raw[:4]]
                bbox_norm = (
                    max(0.0, min(1.0, x1)),
                    max(0.0, min(1.0, y1)),
                    max(0.0, min(1.0, x2)),
                    max(0.0, min(1.0, y2)),
                )
            except Exception:
                bbox_norm = None
        try:
            conf = float(raw.get("conf", 0.0) or 0.0)
        except Exception:
            conf = 0.0
        source = str(raw.get("source", "none") or "none").strip().lower()
        if source not in {"yolo", "yolo_cache", "manual", "manual_box", "manual_marker", "none"}:
            source = "none"
        return {
            "has_figure": bool(raw.get("has_figure", False)),
            "bbox_norm": bbox_norm,
            "conf": max(0.0, min(1.0, conf)),
            "source": source,
            "updated_at": str(raw.get("updated_at", "") or "").strip(),
        }

    def _set_cached_yolo_suggestion(self, source_path: Path, payload: Dict[str, Any]) -> None:
        key = self._seg_v2_source_key(source_path)
        bbox_norm_raw = payload.get("bbox_norm")
        bbox_norm: Optional[Tuple[float, float, float, float]] = None
        if isinstance(bbox_norm_raw, (list, tuple)) and len(bbox_norm_raw) >= 4:
            try:
                x1, y1, x2, y2 = [float(v) for v in bbox_norm_raw[:4]]
                bbox_norm = (
                    max(0.0, min(1.0, x1)),
                    max(0.0, min(1.0, y1)),
                    max(0.0, min(1.0, x2)),
                    max(0.0, min(1.0, y2)),
                )
            except Exception:
                bbox_norm = None
        try:
            conf = float(payload.get("conf", 0.0) or 0.0)
        except Exception:
            conf = 0.0
        source = str(payload.get("source", "none") or "none").strip().lower()
        if source not in {"yolo", "yolo_cache", "manual", "manual_box", "manual_marker", "none"}:
            source = "none"
        has_figure = bool(payload.get("has_figure", False)) and bbox_norm is not None
        self._yolo_figure_suggestions_by_source[key] = {
            "has_figure": has_figure,
            "bbox_norm": bbox_norm,
            "conf": max(0.0, min(1.0, conf)),
            "source": source,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _prefetch_yolo_figure_suggestions(self, selected_labels: Optional[Set[str]] = None) -> None:
        sources = self._collect_review_sources(selected_labels=selected_labels)
        if not sources:
            return
        model_path = self._resolve_bbox_detector_model_path()
        if not model_path:
            self._log("YOLO figura: modelo no configurado (YOLO_FIGURE_MODEL). Se continua sin sugerencias.")
            return
        try:
            from ultralytics import YOLO  # type: ignore  # noqa: F401
        except Exception:
            self._log("YOLO figura: ultralytics no disponible. Se continua sin sugerencias.")
            return

        for _idx, label, src in sources:
            if not src.exists():
                continue
            manual_box = self._get_ocr_exclusion_box(src)
            if manual_box is not None:
                manual_norm = self._box_px_to_norm(path=src, box_px=manual_box)
                self._set_cached_yolo_suggestion(
                    src,
                    {
                        "has_figure": bool(manual_norm),
                        "bbox_norm": manual_norm,
                        "conf": 1.0,
                        "source": "manual",
                    },
                )
                self._log(f"{label}: yolo_figura=manual conf=1.00")
                continue
            cached = self._get_cached_yolo_suggestion(src)
            if cached and cached.get("has_figure") and cached.get("bbox_norm") is not None:
                conf = float(cached.get("conf", 0.0) or 0.0)
                self._log(f"{label}: yolo_figura=cache conf={conf:.2f}")
                continue
            bbox, conf, has_fig = self._detect_figure_bbox_yolo(path=src)
            self._set_cached_yolo_suggestion(
                src,
                {
                    "has_figure": bool(has_fig and bbox is not None),
                    "bbox_norm": bbox,
                    "conf": conf,
                    "source": "yolo" if has_fig and bbox is not None else "none",
                },
            )
            self._log(f"{label}: yolo_figura={'SI' if has_fig else 'NO'} conf={conf:.2f}")

    def _detect_figure_bbox_yolo(
        self,
        *,
        path: Path,
    ) -> Tuple[Optional[Tuple[float, float, float, float]], float, bool]:
        model_path = self._resolve_bbox_detector_model_path()
        if not model_path:
            return (None, 0.0, False)
        try:
            from ultralytics import YOLO  # type: ignore
        except Exception:
            return (None, 0.0, False)

        try:
            if self._yolo_detector is None or self._yolo_detector_path != model_path:
                self._yolo_detector = YOLO(model_path)
                self._yolo_detector_path = model_path
        except Exception:
            return (None, 0.0, False)

        try:
            results = self._yolo_detector.predict(  # type: ignore[union-attr]
                source=str(path),
                verbose=False,
                conf=0.01,
            )
        except Exception:
            return (None, 0.0, False)
        if not results:
            return (None, 0.0, False)

        best_bbox: Optional[Tuple[float, float, float, float]] = None
        best_conf = 0.0
        for res in results:
            boxes = getattr(res, "boxes", None)
            if boxes is None:
                continue
            xyxy = getattr(boxes, "xyxy", None)
            conf = getattr(boxes, "conf", None)
            if xyxy is None or conf is None:
                continue
            try:
                rows = xyxy.cpu().tolist()
                conf_rows = conf.cpu().tolist()
            except Exception:
                continue
            try:
                img_h, img_w = res.orig_shape  # type: ignore[attr-defined]
            except Exception:
                img_h, img_w = (0, 0)
            if img_h <= 0 or img_w <= 0:
                continue
            for row, c in zip(rows, conf_rows):
                if not isinstance(row, list) or len(row) < 4:
                    continue
                try:
                    score = float(c or 0.0)
                    x1, y1, x2, y2 = [float(v) for v in row[:4]]
                except Exception:
                    continue
                if x2 <= x1 or y2 <= y1:
                    continue
                nbox = (
                    max(0.0, min(1.0, x1 / float(img_w))),
                    max(0.0, min(1.0, y1 / float(img_h))),
                    max(0.0, min(1.0, x2 / float(img_w))),
                    max(0.0, min(1.0, y2 / float(img_h))),
                )
                if score > best_conf:
                    best_conf = score
                    best_bbox = nbox
        if best_bbox is None:
            return (None, 0.0, False)
        min_conf = self._resolve_figure_min_conf()
        if best_conf < min_conf:
            return (None, best_conf, False)
        return (best_bbox, best_conf, True)

    def _detect_figure_bbox_separate(
        self,
        *,
        path: Path,
        segment_boxes: List[Tuple[int, int, int, int]],
    ) -> Tuple[Optional[Tuple[float, float, float, float]], float, bool, str]:
        manual_box = self._get_ocr_exclusion_box(path)
        if manual_box is not None:
            manual_norm = self._box_px_to_norm(path=path, box_px=manual_box)
            if manual_norm is not None:
                self._set_cached_yolo_suggestion(
                    path,
                    {
                        "has_figure": True,
                        "bbox_norm": manual_norm,
                        "conf": 1.0,
                        "source": "manual",
                    },
                )
                return (manual_norm, 1.0, True, "manual_box")

        cached = self._get_cached_yolo_suggestion(path)
        if cached and bool(cached.get("has_figure")) and cached.get("bbox_norm") is not None:
            try:
                conf = float(cached.get("conf", 0.0) or 0.0)
            except Exception:
                conf = 0.0
            return (
                tuple(float(v) for v in cached["bbox_norm"]),  # type: ignore[arg-type]
                max(0.0, min(1.0, conf)),
                True,
                "yolo_cache",
            )

        bbox, conf, has_fig = self._detect_figure_bbox_yolo(path=path)
        if has_fig and bbox is not None:
            self._set_cached_yolo_suggestion(
                path,
                {
                    "has_figure": True,
                    "bbox_norm": bbox,
                    "conf": conf,
                    "source": "yolo",
                },
            )
            return (bbox, conf, True, "yolo")
        self._set_cached_yolo_suggestion(
            path,
            {
                "has_figure": False,
                "bbox_norm": None,
                "conf": max(0.0, min(1.0, float(conf or 0.0))),
                "source": "none",
            },
        )
        return (None, 0.0, False, "none")

    def _resolve_effective_provider(self) -> str:
        if self._resolve_hf_token():
            return FIXED_PROVIDER
        self.after(
            0,
            lambda: self._log(
                "Falta HF_TOKEN. Define el token en .env.local/.env o en variables de entorno."
            ),
        )
        return FIXED_PROVIDER

    def _listar_dbs(self) -> None:
        dbs = self.controller.listar_bases_datos()
        self.combo_db["values"] = dbs
        if self.db_name_var.get() in dbs:
            return
        if dbs:
            self.db_name_var.set(dbs[0])

    def _norm_key(self, value: str) -> str:
        raw = (value or "").strip().lower()
        if not raw:
            return ""
        raw = unicodedata.normalize("NFKD", raw)
        raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
        raw = re.sub(r"\s+", " ", raw)
        return raw.strip()

    def _get_catalog(self, db_name: str) -> Dict[str, List[Dict[str, str]]]:
        key = (db_name or "").strip()
        if not key:
            return {"areas": [], "temas": [], "subtemas": []}
        if key in self._catalog_cache:
            return self._catalog_cache[key]
        try:
            catalog = self.controller.obtener_catalogo_temas_subtemas(key)
        except Exception:
            catalog = {"areas": [], "temas": [], "subtemas": []}
        self._catalog_cache[key] = catalog
        return catalog

    def _build_catalog_prompt_block(self, catalog: Dict[str, List[Dict[str, str]]]) -> str:
        areas = [row.get("curso", "").strip() for row in catalog.get("areas", []) if (row.get("curso") or "").strip()]
        temas = [
            f"{(row.get('curso') or '').strip()} | {(row.get('tema') or '').strip()}"
            for row in catalog.get("temas", [])
            if (row.get("tema") or "").strip()
        ]
        subtemas = [
            f"{(row.get('curso') or '').strip()} | {(row.get('tema') or '').strip()} | {(row.get('subtema') or '').strip()}"
            for row in catalog.get("subtemas", [])
            if (row.get("subtema") or "").strip()
        ]
        # Limit to avoid expensive prompts on huge catalogs.
        areas = areas[:120]
        temas = temas[:240]
        subtemas = subtemas[:300]
        return (
            "Catalogo disponible (usa estos valores cuando aplique):\n"
            f"Cursos: {areas}\n"
            f"Temas (curso | tema): {temas}\n"
            f"Subtemas (curso | tema | subtema): {subtemas}\n"
        )

    def _extract_json_meta(self, text: str) -> tuple[str, str, str]:
        raw = (text or "").strip()
        if not raw:
            return ("", "", "")
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        payload = match.group(0) if match else raw
        curso = ""
        tema = ""
        subtema = ""
        try:
            data = json.loads(payload)
            if isinstance(data, dict):
                curso = str(data.get("curso", "") or "").strip()
                tema = str(data.get("tema", "") or "").strip()
                subtema = str(data.get("subtema", "") or "").strip()
                return (curso, tema, subtema)
        except Exception:
            pass

        # Fallback: key-value extraction when model returns near-JSON text.
        for key, target in (("curso", "curso"), ("tema", "tema"), ("subtema", "subtema")):
            m = re.search(rf'"?{key}"?\s*[:=]\s*"?(.*?)"?(?:[,}}]|$)', raw, re.IGNORECASE)
            if not m:
                continue
            value = (m.group(1) or "").strip().strip('"')
            if target == "curso":
                curso = value
            elif target == "tema":
                tema = value
            else:
                subtema = value
        return (curso, tema, subtema)

    def _extract_existing_tags(self, item: str) -> tuple[str, str, str]:
        curso = ""
        tema = ""
        subtema = ""
        for m in TAG_CURSO_RE.finditer(item or ""):
            curso = self._sanitize_tag_value(m.group(1) or "")
        for m in TAG_TEMA_RE.finditer(item or ""):
            tema = self._sanitize_tag_value(m.group(1) or "")
        for m in TAG_SUBTEMA_RE.finditer(item or ""):
            subtema = self._sanitize_tag_value(m.group(1) or "")
        return (curso, tema, subtema)

    def _sanitize_tag_value(self, value: str) -> str:
        txt = (value or "").strip()
        if not txt:
            return ""
        txt = self._decode_scan_escapes(txt)
        txt = txt.replace("Â£", " ").replace("Ã¦", " ")
        txt = txt.replace(SEP_LINE, " ").replace(SEP_OPT, " ")
        txt = BRACKET_TAG_RE.sub(" ", txt)
        txt = re.sub(r"(?<![A-Za-z0-9])[A-Ea-e]\s*[\)\].:]", " ", txt)
        txt = re.sub(r"\\item\s*\[\s*\\textbf\s*\{.*$", " ", txt, flags=re.IGNORECASE)
        txt = re.sub(r"\s+", " ", txt).strip(" ,;:-")
        if not txt:
            return ""
        lowered = txt.lower()
        if ("\\item" in lowered) or ("[[" in txt) or ("]]" in txt):
            return ""
        return txt

    def _canonicalize_with_catalog(
        self,
        *,
        curso: str,
        tema: str,
        subtema: str,
        catalog: Dict[str, List[Dict[str, str]]],
    ) -> tuple[str, str, str]:
        area_map: Dict[str, str] = {}
        for row in catalog.get("areas", []):
            area = (row.get("curso") or "").strip()
            if area:
                area_map[self._norm_key(area)] = area

        tema_map_global: Dict[str, str] = {}
        tema_map_by_area: Dict[tuple[str, str], str] = {}
        for row in catalog.get("temas", []):
            area = (row.get("curso") or "").strip()
            t = (row.get("tema") or "").strip()
            if not t:
                continue
            tema_map_global[self._norm_key(t)] = t
            if area:
                tema_map_by_area[(self._norm_key(area), self._norm_key(t))] = t

        sub_map_global: Dict[str, str] = {}
        sub_map_by_ctx: Dict[tuple[str, str, str], str] = {}
        for row in catalog.get("subtemas", []):
            area = (row.get("curso") or "").strip()
            t = (row.get("tema") or "").strip()
            s = (row.get("subtema") or "").strip()
            if not s:
                continue
            sub_map_global[self._norm_key(s)] = s
            if area or t:
                sub_map_by_ctx[(self._norm_key(area), self._norm_key(t), self._norm_key(s))] = s

        c = (curso or "").strip()
        t = (tema or "").strip()
        s = (subtema or "").strip()
        c_norm = self._norm_key(c)
        t_norm = self._norm_key(t)
        s_norm = self._norm_key(s)

        if c_norm in area_map:
            c = area_map[c_norm]
            c_norm = self._norm_key(c)
        if c_norm and t_norm and (c_norm, t_norm) in tema_map_by_area:
            t = tema_map_by_area[(c_norm, t_norm)]
        elif t_norm in tema_map_global:
            t = tema_map_global[t_norm]
        t_norm = self._norm_key(t)

        if c_norm and t_norm and s_norm and (c_norm, t_norm, s_norm) in sub_map_by_ctx:
            s = sub_map_by_ctx[(c_norm, t_norm, s_norm)]
        elif s_norm in sub_map_global:
            s = sub_map_global[s_norm]
        return (c, t, s)

    def _invalidate_segmentation_review_state(self) -> None:
        self._segmentation_review_done = False
        self._segmentation_route_active = False
        self._segmentation_reviewed_sources.clear()
        self._segmentation_scope_labels = None
        self._refresh_image_list_colors()

    def _is_base_image_label(self, label: str) -> bool:
        low = str(label or "").lower()
        return ("[v2-" not in low) and ("[v2c-" not in low)

    def _refresh_image_list_colors(self) -> None:
        if not self._ui_alive():
            return
        lb = getattr(self, "list_files", None)
        if not self._widget_alive(lb):
            return
        default_fg = self.palette.get("text", "#0f172a")
        color_done = "#16a34a"       # segmentado + OCR
        color_seg_only = "#d97706"   # solo segmentado
        color_ocr_only = "#2563eb"   # solo OCR
        color_aux = "#64748b"        # derivados [v2-*]
        try:
            total = int(self.list_files.size())
        except Exception:
            return
        for i in range(total):
            try:
                label = str(self.list_files.get(i))
            except Exception:
                continue
            fg = default_fg
            if not self._is_base_image_label(label):
                fg = color_aux
            else:
                src = self._file_map.get(label)
                src_key = self._seg_v2_source_key(src) if src else ""
                seg_done = bool(src_key) and (src_key in self._segmentation_reviewed_sources)
                ocr_done = label in self._transcribed_by_label
                if seg_done and ocr_done:
                    fg = color_done
                elif seg_done and not ocr_done:
                    fg = color_seg_only
                elif ocr_done and not seg_done:
                    fg = color_ocr_only
            try:
                self.list_files.itemconfig(i, foreground=fg)
            except Exception:
                # Some Tk builds may not support per-item fg; fail softly.
                return

    def _get_selected_labels(self) -> Set[str]:
        selected_labels: Set[str] = set()
        try:
            for idx in self.list_files.curselection():
                selected_labels.add(str(self.list_files.get(idx)))
        except Exception:
            return set()
        return selected_labels

    def _collect_review_sources(
        self, *, selected_labels: Optional[Set[str]] = None
    ) -> List[Tuple[int, str, Path]]:
        sources: List[Tuple[int, str, Path]] = []
        seen: Set[str] = set()
        try:
            v2_root = (self._runs_dir / "v2_segments").resolve()
        except Exception:
            v2_root = self._runs_dir / "v2_segments"
        try:
            mask_root = (self._runs_dir / "ocr_masked").resolve()
        except Exception:
            mask_root = self._runs_dir / "ocr_masked"
        total = int(self.list_files.size())
        for i in range(total):
            label = self.list_files.get(i)
            if selected_labels is not None and label not in selected_labels:
                continue
            if "[v2-" in label.lower() or "[v2c-" in label.lower():
                continue
            src = self._file_map.get(label)
            if not src or not src.exists():
                continue
            try:
                src_res = src.resolve()
            except Exception:
                src_res = src
            src_low = str(src_res).lower()
            if str(v2_root).lower() in src_low or str(mask_root).lower() in src_low:
                continue
            key = self._seg_v2_source_key(src)
            if key in seen:
                continue
            seen.add(key)
            sources.append((i, label, src))
        return sources

    def _mark_source_reviewed(self, source_path: Path) -> None:
        self._segmentation_reviewed_sources.add(self._seg_v2_source_key(source_path))
        self._refresh_image_list_colors()

    def _next_unreviewed_source(
        self, *, start_after_idx: int = -1, selected_labels: Optional[Set[str]] = None
    ) -> Optional[Tuple[int, str, Path]]:
        sources = self._collect_review_sources(selected_labels=selected_labels)
        if not sources:
            return None
        first_pass = [entry for entry in sources if entry[0] > start_after_idx]
        second_pass = [entry for entry in sources if entry[0] <= start_after_idx]
        for idx, label, src in first_pass + second_pass:
            key = self._seg_v2_source_key(src)
            if key not in self._segmentation_reviewed_sources:
                return (idx, label, src)
        return None

    def _refresh_segmentation_done_state(self, *, selected_labels: Optional[Set[str]] = None) -> bool:
        sources = self._collect_review_sources(selected_labels=selected_labels)
        if not sources:
            self._segmentation_review_done = False
            return False
        for _idx, _label, src in sources:
            if self._seg_v2_source_key(src) not in self._segmentation_reviewed_sources:
                self._segmentation_review_done = False
                return False
        self._segmentation_review_done = True
        return True

    def _segmentation_progress(self, *, selected_labels: Optional[Set[str]] = None) -> Tuple[int, int, List[str]]:
        sources = self._collect_review_sources(selected_labels=selected_labels)
        total = len(sources)
        reviewed = 0
        pending_labels: List[str] = []
        for _idx, label, src in sources:
            key = self._seg_v2_source_key(src)
            if key in self._segmentation_reviewed_sources:
                reviewed += 1
            else:
                pending_labels.append(label)
        return (reviewed, total, pending_labels)

    def _start_segmentation_review(self, *, reset_progress: bool = True) -> None:
        if reset_progress:
            self._segmentation_review_done = False
            self._segmentation_route_active = False
            self._segmentation_reviewed_sources.clear()
            self._refresh_image_list_colors()
        selected_now = self._get_selected_labels()
        scope_labels: Optional[Set[str]] = set(selected_now) if selected_now else None
        if scope_labels is not None:
            self._segmentation_scope_labels = scope_labels
        elif reset_progress:
            self._segmentation_scope_labels = None
        active_scope = self._segmentation_scope_labels
        total = int(self.list_files.size())
        if total <= 0:
            messagebox.showwarning("Segmentacion V2", "Agrega al menos una imagen para segmentar.")
            return
        self._prefetch_yolo_figure_suggestions(selected_labels=active_scope)
        pending = self._next_unreviewed_source(start_after_idx=-1, selected_labels=active_scope)
        if pending is None:
            if self._refresh_segmentation_done_state(selected_labels=active_scope):
                if active_scope:
                    self._log("Paso 1 ya estaba completo para la seleccion actual.")
                else:
                    self._log("Paso 1 ya estaba completo para el lote actual.")
            else:
                if active_scope:
                    self._log("Paso 1: no hay imagenes validas en la seleccion actual.")
                else:
                    self._log("Paso 1: no hay imagenes validas para revisar.")
            return
        first_idx, _first_label, first_path = pending
        if first_path is None:
            messagebox.showwarning("Segmentacion V2", "No hay imagenes validas para segmentar.")
            return
        self._segmentation_route_active = True
        self.list_files.selection_clear(0, "end")
        if active_scope:
            for i in range(total):
                label_i = self.list_files.get(i)
                if label_i in active_scope:
                    self.list_files.selection_set(i)
        else:
            self.list_files.selection_set(first_idx)
        self.list_files.activate(first_idx)
        self.list_files.see(first_idx)
        reviewed, total_review, pending = self._segmentation_progress(selected_labels=active_scope)
        scope_msg = "seleccion actual" if active_scope else "lote completo"
        self._log(
            f"Paso 1 iniciado ({scope_msg}): revisa cuadros de cada imagen y pulsa 'Siguiente imagen' hasta terminar."
        )
        self._log(
            f"Segmentacion progreso: {reviewed}/{total_review} revisadas."
        )
        if pending:
            self._log(f"Pendiente actual: {pending[0]}")
        self._open_segment_editor_for_source(first_path)

    def _add_images(self) -> None:
        selected = filedialog.askopenfilenames(
            title="Selecciona imagenes",
            filetypes=[
                ("Imagenes", "*.png;*.jpg;*.jpeg;*.webp;*.bmp"),
                ("Todos", "*.*"),
            ],
        )
        if not selected:
            return
        image_paths = self._collect_image_paths([str(p) for p in selected])
        added, skipped = self._add_images_from_paths(image_paths)
        if added > 0:
            self._log(f"Imagenes cargadas: {added}")
        if skipped > 0:
            self._log(f"Imagenes omitidas (duplicadas): {skipped}")
        if added == 0 and skipped == 0:
            self._log("No se agregaron imagenes validas.")

    def _paste_from_clipboard(self, silent: bool = False) -> bool:
        """
        Intenta pegar una imagen desde el portapapeles.
        Retorna True si se pegÃ³ algo (imagen o ruta de imagen), False si no habÃ­a imagen.
        """

        try:
            from PIL import ImageGrab  # type: ignore
        except Exception:
            if not silent:
                messagebox.showwarning(
                    "Portapapeles",
                    "Para pegar imÃ¡genes desde el portapapeles instala Pillow:\n\npython -m pip install pillow",
                )
            return False

        grabbed = ImageGrab.grabclipboard()
        if grabbed is None:
            if not silent:
                messagebox.showinfo("Portapapeles", "No hay imagen en el portapapeles.")
            return False

        # A veces devuelve una lista de paths si copiaste archivos.
        if isinstance(grabbed, list):
            image_paths = self._collect_image_paths([str(p) for p in grabbed])
            added, skipped = self._add_images_from_paths(image_paths)
            if added and not silent:
                self._log(f"Pegadas {added} imagen(es) desde portapapeles.")
            if skipped and not silent:
                self._log(f"Portapapeles: {skipped} imagen(es) duplicadas omitidas.")
            return added > 0

        # PIL Image
        try:
            img = grabbed
            cache_dir = Path.cwd() / ".cache" / "clipboard_images"
            cache_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            tmp_name = f"clipboard_{ts}.png"
            tmp_path = cache_dir / tmp_name

            # Guardar como PNG
            img.save(tmp_path, format="PNG")

            label = tmp_path.name
            suffix = 1
            base = label
            while label in self._file_map:
                suffix += 1
                label = f"{base} ({suffix})"
            self._file_map[label] = tmp_path
            self.list_files.insert("end", label)
            self._invalidate_segmentation_review_state()
            if not silent:
                self._log(f"Imagen pegada: {label}")
            return True
        except Exception:
            # Fallback: intentar guardar en temp si falla el cwd
            try:
                tmp_dir = Path(tempfile.gettempdir())
                tmp_path = tmp_dir / f"clipboard_{int(time.time())}.png"
                grabbed.save(tmp_path, format="PNG")
                label = tmp_path.name
                self._file_map[label] = tmp_path
                self.list_files.insert("end", label)
                self._invalidate_segmentation_review_state()
                if not silent:
                    self._log(f"Imagen pegada: {label}")
                return True
            except Exception as exc:
                if not silent:
                    messagebox.showerror("Portapapeles", f"No se pudo pegar la imagen.\n\n{exc}")
                return False

    def _remove_selected(self) -> None:
        sel = list(self.list_files.curselection())
        if not sel:
            return
        for idx in reversed(sel):
            label = self.list_files.get(idx)
            src = self._file_map.pop(label, None)
            self.list_files.delete(idx)
            self._transcribed_by_label.pop(str(label), None)
            self._ocr_raw_first_by_label.pop(str(label), None)
            if src is not None:
                self._yolo_figure_suggestions_by_source.pop(self._seg_v2_source_key(src), None)
            base = str(label)
            for key in list(self._geometry_pass_by_label.keys()):
                if key == base or key.startswith(f"{base}#"):
                    self._geometry_pass_by_label.pop(key, None)
            for key in list(self._geometry_pass_payload_by_label.keys()):
                if key == base or key.startswith(f"{base}#"):
                    self._geometry_pass_payload_by_label.pop(key, None)
            self._ocr_merge_applied_by_label.pop(str(label), None)
        self._invalidate_segmentation_review_state()
        self._refresh_image_list_colors()

    def _add_image_to_list(self, path: Path, *, label_hint: str = "", invalidate: bool = True) -> str:
        base = label_hint.strip() or path.name
        label = base
        suffix = 1
        while label in self._file_map:
            suffix += 1
            label = f"{base} ({suffix})"
        self._file_map[label] = path
        self.list_files.insert("end", label)
        if invalidate:
            self._invalidate_segmentation_review_state()
        else:
            self._refresh_image_list_colors()
        return label

    def _find_list_index_for_path(self, target_path: Path) -> int:
        try:
            target_key = str(target_path.resolve()).lower()
        except Exception:
            target_key = str(target_path).lower()
        total = int(self.list_files.size())
        for i in range(total):
            label = self.list_files.get(i)
            src = self._file_map.get(label)
            if not src:
                continue
            try:
                src_key = str(src.resolve()).lower()
            except Exception:
                src_key = str(src).lower()
            if src_key == target_key:
                return i
        return -1

    def _open_segment_editor_for_source(self, source_path: Path) -> None:
        if not source_path or not source_path.exists():
            self._log("Segmentacion V2: imagen no disponible para editor.")
            return
        key = self._seg_v2_source_key(source_path)
        using_override = key in self._segmentacion_v2_overrides
        if self._get_ocr_exclusion_box(source_path) is None:
            cached = self._get_cached_yolo_suggestion(source_path)
            if not cached:
                bbox, conf, has_fig = self._detect_figure_bbox_yolo(path=source_path)
                self._set_cached_yolo_suggestion(
                    source_path,
                    {
                        "has_figure": bool(has_fig and bbox is not None),
                        "bbox_norm": bbox,
                        "conf": conf,
                        "source": "yolo" if has_fig and bbox is not None else "none",
                    },
                )
        segments = self._get_segments_v2_for_source(source_path)
        if using_override:
            self._log(f"Segmentacion V2 fuente {source_path.name}: override_manual.")
        else:
            source_mode = str(getattr(self._segmentador_v2, "last_detector_source", "projection_v2") or "projection_v2").strip()
            self._log(f"Segmentacion V2 fuente {source_path.name}: {source_mode}.")
        boxes = [tuple(int(v) for v in seg.bbox) for seg in segments]
        self._open_segment_editor_v2(source_path=source_path, initial_boxes=boxes)

    def _seg_v2_source_key(self, source_path: Path) -> str:
        try:
            return str(source_path.resolve()).lower()
        except Exception:
            return str(source_path).lower()

    def _get_used_segment_indices(self, source_path: Path) -> Set[int]:
        key = self._seg_v2_source_key(source_path)
        return set(self._segmentation_v2_used_segments.get(key, set()))

    def _mark_segment_used(self, source_path: Path, seg_idx: int) -> None:
        if seg_idx < 0:
            return
        key = self._seg_v2_source_key(source_path)
        bucket = self._segmentation_v2_used_segments.setdefault(key, set())
        bucket.add(int(seg_idx))

    def _extract_structured_item_number(self, text: str) -> Optional[int]:
        raw = self.controller.normalizar_item_una_linea(self._decode_scan_escapes(text or ""))
        if not raw:
            return None
        patterns = (
            r"(?i)\bITEM\s*:\s*(\d{1,4})\b",
            r"(?i)\bITEM\s+(\d{1,4})\s*:",
        )
        for pat in patterns:
            m = re.search(pat, raw)
            if not m:
                continue
            try:
                val = int(m.group(1))
            except Exception:
                continue
            if val > 0:
                return val
        return None

    def _extract_structured_leading_payload(self, text: str) -> tuple[str, Dict[str, str], str]:
        """
        Parse optional structured leading payload emitted by vision model:
          LEADING_CONTINUATION: ...
          LEADING_OPTIONS: A)...E)...
          ENDLEADING
        Returns: (continuation_text, options_dict, cleaned_text_without_leading_block)
        """
        base = self.controller.normalizar_item_una_linea(self._decode_scan_escapes(text or ""))
        if not base:
            return ("", {}, "")

        work = base
        cont_text = ""
        options: Dict[str, str] = {}

        cont_match = re.search(
            r"(?is)\bLEADING_CONTINUATION\s*:\s*(.*?)(?=\bLEADING_OPTIONS\s*:|\bENDLEADING\b|\bITEM\s*:|\\item\b|\bproblema\b|\Z)",
            work,
        )
        if cont_match:
            cont_text = self.controller.normalizar_item_una_linea(cont_match.group(1) or "")
            work = (work[: cont_match.start()] + " " + work[cont_match.end() :]).strip()
            if cont_text.upper() == "VACIO":
                cont_text = ""

        opt_match = re.search(
            r"(?is)\bLEADING_OPTIONS\s*:\s*(.*?)(?=\bENDLEADING\b|\bITEM\s*:|\\item\b|\bproblema\b|\Z)",
            work,
        )
        if opt_match:
            opt_block = self.controller.normalizar_item_una_linea(opt_match.group(1) or "")
            _enu, options = self._extract_options_loose(opt_block)
            work = (work[: opt_match.start()] + " " + work[opt_match.end() :]).strip()

        work = re.sub(r"(?i)\bENDLEADING\b", " ", work)
        work = re.sub(r"\s+", " ", work).strip()
        return (cont_text, options, work)

    def _extract_leading_continuation_payload(
        self, text: str, *, force_prefix: bool = False
    ) -> tuple[str, str]:
        """
        When an image starts with continuation text/options of previous item and
        then starts a new item, split as (prefix_continuation, remaining_text).
        """
        base = self.controller.normalizar_item_una_linea(self._decode_scan_escapes(text or ""))
        if not base:
            return ("", "")

        first_item = ITEM_HEADER_RE.search(base)
        first_problem = re.search(r"\bproblema\b", base, re.IGNORECASE)
        first_structured = STRUCTURED_ITEM_HEADER_RE.search(base)
        cut: Optional[int] = None
        if first_item:
            cut = first_item.start()
        if first_problem:
            cut = min(cut, first_problem.start()) if cut is not None else first_problem.start()
        if first_structured:
            cut = min(cut, first_structured.start()) if cut is not None else first_structured.start()
        if cut is None or cut <= 0:
            return ("", base)

        prefix = (base[:cut] or "").strip()
        rest = (base[cut:] or "").strip()
        if not prefix:
            return ("", base)

        # If there is an unresolved pending item, any non-empty prefix before
        # the first new header should be treated as continuation payload.
        if force_prefix:
            return (prefix, rest)

        # Prefix is continuation if it has enough signal: options, figure hints,
        # math symbols, or short plain continuation sentence.
        enu, opts = self._extract_options_loose(prefix)
        if len(opts) >= 1:
            return (prefix, rest)
        if self._item_image_hint_score(prefix) > 0:
            return (prefix, rest)
        if re.search(r"(\\angle|\^\\circ|∠|\\sqrt|\\dfrac|\\tfrac|\\frac|[=+\-*/])", prefix):
            return (prefix, rest)
        if enu and len(enu) <= 220:
            return (prefix, rest)
        return ("", base)

    def _extract_prefix_before_known_item_number(
        self,
        text: str,
        *,
        next_item_number: Optional[int],
    ) -> tuple[str, str]:
        """
        Extract prefix continuation before a known next item number.
        Useful when OCR degrades the "PROBLEMA" token and generic header
        detection fails, but the next item number is known from model output.
        """
        base = self.controller.normalizar_item_una_linea(self._decode_scan_escapes(text or ""))
        if not base:
            return ("", "")
        if not next_item_number or next_item_number <= 0:
            return ("", base)

        n = int(next_item_number)
        patterns = (
            rf"(?i)\bITEM\s*:\s*{n}\b",
            rf"(?i)\bITEM\s+{n}\s*:",
            rf"(?i)\\item\s*\[\s*\\textbf\s*\{{\s*{n}\.?\s*\}}\s*\]",
            rf"(?i)\bproblema\b[^0-9]{{0,20}}\b{n}\b",
        )
        cut: Optional[int] = None
        for pat in patterns:
            m = re.search(pat, base)
            if not m:
                continue
            pos = int(m.start())
            if pos <= 0:
                continue
            cut = pos if cut is None else min(cut, pos)
        if cut is None:
            return ("", base)
        prefix = (base[:cut] or "").strip()
        rest = (base[cut:] or "").strip()
        if not prefix:
            return ("", base)
        return (prefix, rest)

    def _normalize_merge_note_text(self, text: str, *, max_len: int = 360) -> str:
        value = self.controller.normalizar_item_una_linea(self._decode_scan_escapes(text or ""))
        value = re.sub(r"\s+", " ", value).strip()
        if max_len > 0 and len(value) > max_len:
            value = value[: max_len - 3].rstrip() + "..."
        return value

    def _build_merge_options_note(self, *, stage: str, options: Dict[str, str]) -> str:
        if not options:
            return ""
        parts: List[str] = []
        for key in ("A", "B", "C", "D", "E"):
            raw_val = str(options.get(key, "") or "").strip()
            if not raw_val:
                continue
            val = self._normalize_merge_note_text(raw_val, max_len=120)
            if not val:
                continue
            parts.append(f"{key}) {val}")
        if not parts:
            return ""
        return f"{stage}: {' | '.join(parts)}"

    def _build_merge_text_note(self, *, stage: str, text: str, max_len: int = 360) -> str:
        value = self._normalize_merge_note_text(text, max_len=max_len)
        if not value:
            return ""
        return f"{stage}: {value}"

    def _set_merge_notes_for_label(self, label: str, notes: List[str]) -> None:
        key = str(label)
        compact = [str(n).strip() for n in notes if str(n).strip()]
        if compact:
            self._ocr_merge_applied_by_label[key] = "\n".join(compact)
        else:
            self._ocr_merge_applied_by_label.pop(key, None)

    def _pending_item_number(self, pending_idx: Optional[int], *, fallback: int = 0) -> int:
        if pending_idx is None or pending_idx < 0 or pending_idx >= len(self._items):
            return fallback
        try:
            item = str(self._items[pending_idx][1] or "")
        except Exception:
            return fallback
        return (
            self.controller.parsear_numero_original(item)
            or self._extract_structured_item_number(item)
            or fallback
        )

    def _attach_segment_to_existing_item(
        self,
        *,
        source_path: Path,
        segment_box: Optional[Tuple[int, int, int, int]],
        item_idx: int,
        fallback_numero: int,
    ) -> bool:
        if segment_box is None:
            return False
        if item_idx < 0 or item_idx >= len(self._items):
            return False

        archivo, item, imgs = self._items[item_idx]
        updated_item = str(item or "")
        numero = self.controller.parsear_numero_original(updated_item) or fallback_numero
        had_marker = self._has_image_marker(updated_item)
        if not had_marker:
            updated_item = self._insert_image_marker(updated_item, path=source_path, numero=numero)
            updated_item = self._move_image_marker_before_options(updated_item)
        marker_name = self._extract_first_image_marker_name(updated_item)
        image_paths = list(imgs or [])
        if marker_name and self.auto_crop_var.get():
            bbox_norm = self._box_px_to_norm(path=source_path, box_px=segment_box)
            if bbox_norm is not None:
                crop_saved = self._save_figure_crop(
                    image_path=source_path,
                    marker_name=marker_name,
                    bbox_norm=bbox_norm,
                )
                if crop_saved:
                    image_paths = [crop_saved]
                    self._preview_images[marker_name] = crop_saved
        self._items[item_idx] = (str(archivo or ""), updated_item, image_paths)
        return True

    def _normalize_box_px(
        self,
        box_px: Tuple[int, int, int, int],
        *,
        width: int,
        height: int,
    ) -> Optional[Tuple[int, int, int, int]]:
        if width <= 1 or height <= 1:
            return None
        x1, y1, x2, y2 = [int(v) for v in box_px]
        left = max(0, min(width - 1, min(x1, x2)))
        top = max(0, min(height - 1, min(y1, y2)))
        right = max(left + 1, min(width, max(x1, x2)))
        bottom = max(top + 1, min(height, max(y1, y2)))
        if (right - left) < 8 or (bottom - top) < 8:
            return None
        return (left, top, right, bottom)

    def _get_ocr_exclusion_box(self, path: Path) -> Optional[Tuple[int, int, int, int]]:
        for entry in self._get_figure_boxes(path):
            if str(entry.get("source", "")).strip().lower() != "manual":
                continue
            if not self._as_bool(entry.get("confirmed"), True):
                continue
            bbox_raw = entry.get("bbox_px")
            if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) < 4:
                continue
            try:
                x1, y1, x2, y2 = [int(v) for v in bbox_raw[:4]]
            except Exception:
                continue
            if x2 > x1 and y2 > y1:
                return (x1, y1, x2, y2)
        key = self._seg_v2_source_key(path)
        box = self._ocr_exclusion_boxes.get(key)
        if box:
            return tuple(int(v) for v in box)
        return None

    def _set_ocr_exclusion_box(self, path: Path, box_px: Tuple[int, int, int, int]) -> bool:
        if not self._replace_manual_figure_box(path, box_px):
            return False
        normalized = self._get_ocr_exclusion_box(path)
        manual_norm = self._box_px_to_norm(path=path, box_px=normalized) if normalized is not None else None
        self._set_cached_yolo_suggestion(
            path,
            {
                "has_figure": bool(manual_norm),
                "bbox_norm": manual_norm,
                "conf": 1.0,
                "source": "manual",
            },
        )
        return True

    def _clear_ocr_exclusion_box_selected(self) -> None:
        sel = list(self.list_files.curselection())
        if not sel:
            messagebox.showwarning("Caja OCR", "Selecciona una o mas imagenes.")
            return
        removed = 0
        for idx in sel:
            label = self.list_files.get(idx)
            src = self._file_map.get(label)
            if not src:
                continue
            removed_here = self._clear_manual_figure_boxes(src)
            if removed_here > 0:
                self._set_cached_yolo_suggestion(
                    src,
                    {
                        "has_figure": False,
                        "bbox_norm": None,
                        "conf": 0.0,
                        "source": "none",
                    },
                )
                removed += removed_here
        if removed:
            self._log(f"Caja OCR: eliminada ({removed} caja(s) manual(es)).")
        else:
            self._log("Caja OCR: no habia caja configurada en la seleccion.")

    def _run_ocr_exclusion_box_ui(
        self,
        image_path: Path,
        *,
        initial_box: Optional[Tuple[int, int, int, int]] = None,
    ) -> Optional[Tuple[int, int, int, int]]:
        try:
            from PIL import Image, ImageTk  # type: ignore
        except Exception:
            messagebox.showwarning("Caja OCR", "Falta Pillow. Instala: python -m pip install pillow")
            return None

        try:
            image = Image.open(image_path)
        except Exception as exc:
            messagebox.showerror("Caja OCR", f"No se pudo abrir la imagen:\n{exc}")
            return None

        max_w, max_h = 1280, 820
        scale = min(max_w / max(image.width, 1), max_h / max(image.height, 1), 1.0)
        disp_w = max(1, int(round(image.width * scale)))
        disp_h = max(1, int(round(image.height * scale)))
        preview_im = image.resize((disp_w, disp_h)) if scale < 1.0 else image.copy()

        top = tk.Toplevel(self)
        top.title(f"Caja OCR (excluir figura) - {image_path.name}")
        top.geometry(f"{min(disp_w + 90, 1360)}x{min(disp_h + 170, 980)}")
        top.transient(self)
        top.grab_set()

        frm = ttk.Frame(top, padding=10)
        frm.pack(fill="both", expand=True)
        ttk.Label(
            frm,
            text="Dibuja la caja de la figura. El OCR se aplicara al resto de la imagen (fuera de este cuadro).",
        ).pack(anchor="w")

        canvas = tk.Canvas(
            frm,
            width=disp_w,
            height=disp_h,
            bg="#111827",
            highlightthickness=1,
            highlightbackground="#374151",
        )
        canvas.pack(fill="both", expand=True, pady=(8, 0))
        tk_img = ImageTk.PhotoImage(preview_im)
        canvas.create_image(0, 0, anchor="nw", image=tk_img)
        canvas.image = tk_img

        state: Dict[str, object] = {"x0": None, "y0": None, "x1": None, "y1": None, "rect": None}
        result: Dict[str, object] = {"bbox": None}

        if initial_box:
            ix1, iy1, ix2, iy2 = [int(v) for v in initial_box]
            sx1 = max(0, min(disp_w, int(round(ix1 * scale))))
            sy1 = max(0, min(disp_h, int(round(iy1 * scale))))
            sx2 = max(0, min(disp_w, int(round(ix2 * scale))))
            sy2 = max(0, min(disp_h, int(round(iy2 * scale))))
            state["x0"] = sx1
            state["y0"] = sy1
            state["x1"] = sx2
            state["y1"] = sy2
            state["rect"] = canvas.create_rectangle(sx1, sy1, sx2, sy2, outline="#f59e0b", width=2)

        def on_down(event) -> None:
            state["x0"] = max(0, min(disp_w, int(event.x)))
            state["y0"] = max(0, min(disp_h, int(event.y)))
            state["x1"] = state["x0"]
            state["y1"] = state["y0"]
            if state.get("rect"):
                try:
                    canvas.delete(state["rect"])  # type: ignore[arg-type]
                except Exception:
                    pass
            state["rect"] = canvas.create_rectangle(
                int(state["x0"]),
                int(state["y0"]),
                int(state["x1"]),
                int(state["y1"]),
                outline="#22c55e",
                width=2,
            )

        def on_move(event) -> None:
            if state.get("x0") is None or state.get("rect") is None:
                return
            state["x1"] = max(0, min(disp_w, int(event.x)))
            state["y1"] = max(0, min(disp_h, int(event.y)))
            canvas.coords(
                state["rect"],  # type: ignore[arg-type]
                int(state["x0"]),
                int(state["y0"]),
                int(state["x1"]),
                int(state["y1"]),
            )

        canvas.bind("<Button-1>", on_down)
        canvas.bind("<B1-Motion>", on_move)

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(8, 0))

        def on_cancel() -> None:
            top.destroy()

        def on_accept() -> None:
            x0 = state.get("x0")
            y0 = state.get("y0")
            x1 = state.get("x1")
            y1 = state.get("y1")
            if None in (x0, y0, x1, y1):
                messagebox.showwarning("Caja OCR", "Dibuja un rectangulo para la figura.")
                return
            xa, xb = sorted([int(x0), int(x1)])
            ya, yb = sorted([int(y0), int(y1)])
            if (xb - xa) < 8 or (yb - ya) < 8:
                messagebox.showwarning("Caja OCR", "Caja muy pequena; dibuja un area mayor.")
                return
            result["bbox"] = (
                max(0, min(image.width, int(round(xa / max(scale, 1e-8))))),
                max(0, min(image.height, int(round(ya / max(scale, 1e-8))))),
                max(0, min(image.width, int(round(xb / max(scale, 1e-8))))),
                max(0, min(image.height, int(round(yb / max(scale, 1e-8))))),
            )
            top.destroy()

        ttk.Button(btns, text="Cancelar", command=on_cancel, style="Ghost.TButton").pack(side="right")
        ttk.Button(btns, text="Guardar caja", command=on_accept, style="Accent.TButton").pack(side="right", padx=(0, 8))

        top.wait_window()
        bbox = result.get("bbox")
        if not bbox:
            return None
        return tuple(int(v) for v in bbox)  # type: ignore[return-value]

    def _set_ocr_exclusion_box_selected(self) -> None:
        sel = list(self.list_files.curselection())
        if not sel:
            messagebox.showwarning("Caja OCR", "Selecciona una imagen.")
            return
        saved = 0
        for idx in sel:
            label = self.list_files.get(idx)
            src = self._file_map.get(label)
            if not src or not src.exists():
                continue
            initial = self._get_ocr_exclusion_box(src)
            if initial is None:
                suggestion = self._get_cached_yolo_suggestion(src)
                bbox_norm = suggestion.get("bbox_norm") if isinstance(suggestion, dict) else None
                if bbox_norm is not None:
                    try:
                        initial = self._box_norm_to_px(path=src, box_norm=tuple(float(v) for v in bbox_norm))
                    except Exception:
                        initial = None
            chosen = self._run_ocr_exclusion_box_ui(src, initial_box=initial)
            if not chosen:
                continue
            if self._set_ocr_exclusion_box(src, chosen):
                saved += 1
                x1, y1, x2, y2 = chosen
                self._log(f"Caja OCR guardada en {label}: ({x1},{y1},{x2},{y2})")
        if saved == 0:
            self._log("Caja OCR: sin cambios.")

    def _box_px_to_norm(
        self,
        *,
        path: Path,
        box_px: Tuple[int, int, int, int],
    ) -> Optional[Tuple[float, float, float, float]]:
        try:
            from PIL import Image  # type: ignore
        except Exception:
            return None
        try:
            with Image.open(path) as im:
                normalized = self._normalize_box_px(
                    tuple(int(v) for v in box_px),
                    width=int(im.size[0]),
                    height=int(im.size[1]),
                )
                if not normalized:
                    return None
                left, top, right, bottom = normalized
                w = float(im.size[0])
                h = float(im.size[1])
                if w <= 1.0 or h <= 1.0:
                    return None
                return (left / w, top / h, right / w, bottom / h)
        except Exception:
            return None

    def _box_norm_to_px(
        self,
        *,
        path: Path,
        box_norm: Tuple[float, float, float, float],
    ) -> Optional[Tuple[int, int, int, int]]:
        try:
            from PIL import Image  # type: ignore
        except Exception:
            return None
        try:
            with Image.open(path) as im:
                w = int(im.size[0])
                h = int(im.size[1])
                if w <= 1 or h <= 1:
                    return None
                x1, y1, x2, y2 = [float(v) for v in box_norm]
                box_px = (
                    int(round(max(0.0, min(1.0, x1)) * w)),
                    int(round(max(0.0, min(1.0, y1)) * h)),
                    int(round(max(0.0, min(1.0, x2)) * w)),
                    int(round(max(0.0, min(1.0, y2)) * h)),
                )
                return self._normalize_box_px(box_px, width=w, height=h)
        except Exception:
            return None

    def _build_masked_image_for_ocr(
        self,
        *,
        path: Path,
        box_px: Tuple[int, int, int, int],
        run_idx: int,
    ) -> Optional[Path]:
        try:
            from PIL import Image, ImageDraw  # type: ignore
        except Exception:
            self._log("Caja OCR: falta Pillow para enmascarar imagen.")
            return None

        try:
            with Image.open(path) as im:
                normalized = self._normalize_box_px(
                    box_px,
                    width=int(im.size[0]),
                    height=int(im.size[1]),
                )
                if not normalized:
                    return None
                out = im.convert("RGB")
                draw = ImageDraw.Draw(out)
                left, top, right, bottom = normalized
                # Exclude figure region from OCR by painting it white.
                draw.rectangle((left, top, right, bottom), fill=(255, 255, 255))
                out_dir = self._runs_dir / "ocr_masked"
                out_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                out_path = out_dir / f"{path.stem}_ocrmask_{run_idx:04d}_{ts}.png"
                out.save(out_path, format="PNG")
                return out_path
        except Exception as exc:
            self._log(f"Caja OCR: no se pudo enmascarar {path.name}: {exc}")
            return None

    def _build_masked_image_for_ocr_boxes(
        self,
        *,
        path: Path,
        boxes_px: List[Tuple[int, int, int, int]],
        run_idx: int,
    ) -> Optional[Path]:
        try:
            from PIL import Image, ImageDraw  # type: ignore
        except Exception:
            self._log("OCR: falta Pillow para enmascarar segmentos.")
            return None

        if not boxes_px:
            return path
        try:
            with Image.open(path) as im:
                out = im.convert("RGB")
                draw = ImageDraw.Draw(out)
                for box in boxes_px:
                    normalized = self._normalize_box_px(
                        tuple(int(v) for v in box),
                        width=int(im.size[0]),
                        height=int(im.size[1]),
                    )
                    if not normalized:
                        continue
                    left, top, right, bottom = normalized
                    # OCR sobre el remanente: ocultar segmentos detectados.
                    draw.rectangle((left, top, right, bottom), fill=(255, 255, 255))
                out_dir = self._runs_dir / "ocr_masked"
                out_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                out_path = out_dir / f"{path.stem}_ocrmask_all_{run_idx:04d}_{ts}.png"
                out.save(out_path, format="PNG")
                return out_path
        except Exception as exc:
            self._log(f"OCR: no se pudo enmascarar segmentos de {path.name}: {exc}")
            return None

    def _save_v2_segments_from_boxes(
        self,
        *,
        source_path: Path,
        boxes: List[Tuple[int, int, int, int]],
        tag: str,
    ) -> List[SegmentoProblemaV2]:
        try:
            from PIL import Image  # type: ignore
        except Exception:
            self._log("Segmentacion V2: falta Pillow para recortar segmentos.")
            return []

        src = Path(source_path)
        if not src.exists() or not boxes:
            return []
        try:
            im = Image.open(src)
        except Exception:
            return []

        w, h = im.size
        out_dir = self._runs_dir / "v2_segments" / src.stem
        out_dir.mkdir(parents=True, exist_ok=True)
        result: List[SegmentoProblemaV2] = []
        for i, b in enumerate(boxes, start=1):
            x1, y1, x2, y2 = [int(v) for v in b]
            x1 = max(0, min(w - 1, x1))
            y1 = max(0, min(h - 1, y1))
            x2 = max(x1 + 1, min(w, x2))
            y2 = max(y1 + 1, min(h, y2))
            if (x2 - x1) < 16 or (y2 - y1) < 16:
                continue
            crop = im.crop((x1, y1, x2, y2))
            seg_path = out_dir / f"{src.stem}_{tag}_{i:02d}.png"
            if seg_path.exists():
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                seg_path = out_dir / f"{src.stem}_{tag}_{i:02d}_{ts}.png"
            try:
                crop.save(seg_path, format="PNG")
            except Exception:
                continue
            result.append(
                SegmentoProblemaV2(
                    idx=i,
                    bbox=(x1, y1, x2, y2),
                    image_path=seg_path,
                    source_path=src,
                )
            )
        return result

    def _get_segments_v2_for_source(self, source_path: Path) -> List[SegmentoProblemaV2]:
        key = self._seg_v2_source_key(source_path)
        # If an override exists (even empty), respect it exactly and do not fallback.
        if key in self._segmentacion_v2_overrides:
            override_boxes = self._segmentacion_v2_overrides.get(key, [])
            return self._save_v2_segments_from_boxes(
                source_path=source_path,
                boxes=override_boxes,
                tag="custom",
            )
        return self._segmentador_v2.segmentar(source_path)

    def _segmentar_v2_selected(self) -> None:
        sel = list(self.list_files.curselection())
        if not sel:
            messagebox.showwarning("Segmentacion V2", "Selecciona una o mas imagenes.")
            return
        added = 0
        src_count = 0
        for idx in sel:
            label = self.list_files.get(idx)
            src = self._file_map.get(label)
            if not src or not src.exists():
                continue
            src_count += 1
            segments = self._get_segments_v2_for_source(src)
            if not segments:
                self._log(f"Segmentacion V2: sin segmentos utiles para {label}.")
                continue
            if len(segments) == 1:
                self._log(f"Segmentacion V2: 1 bloque detectado en {label}.")
            else:
                self._log(f"Segmentacion V2: {len(segments)} bloques detectados en {label}.")
            for seg in segments:
                seg_label = f"{src.stem} [v2-{seg.idx}] {seg.image_path.name}"
                self._add_image_to_list(seg.image_path, label_hint=seg_label)
                added += 1
        if added > 0:
            self._log(f"Segmentacion V2: {added} segmento(s) agregados a la lista desde {src_count} imagen(es).")
        else:
            self._log("Segmentacion V2: no se agregaron segmentos.")

    def _build_segmentacion_overlay_v2(self, source_path: Path, segments: List) -> object | None:
        try:
            from PIL import Image, ImageDraw  # type: ignore
        except Exception:
            self._log("Segmentacion V2: falta Pillow para vista de cuadros.")
            return None

        try:
            im = Image.open(source_path).convert("RGB")
        except Exception as exc:
            self._log(f"Segmentacion V2: no se pudo abrir imagen: {exc}")
            return None

        draw = ImageDraw.Draw(im)
        palette = [
            (255, 80, 80),
            (80, 180, 255),
            (80, 200, 120),
            (255, 170, 70),
            (210, 120, 255),
            (255, 120, 180),
        ]
        for seg in segments:
            try:
                x1, y1, x2, y2 = seg.bbox
                color = palette[(max(1, int(seg.idx)) - 1) % len(palette)]
                draw.rectangle((x1, y1, x2, y2), outline=color, width=4)
                draw.text((x1 + 8, max(0, y1 - 18)), f"seg-{seg.idx}", fill=color)
            except Exception:
                continue
        return im

    def _show_image_window(self, title: str, image_obj) -> None:
        try:
            from PIL import Image, ImageTk  # type: ignore
        except Exception:
            return
        if not self._ui_alive():
            return
        try:
            im = image_obj
            max_side = 1800
            w, h = im.size
            scale = min(1.0, max_side / float(max(w, h))) if max(w, h) > 0 else 1.0
            if scale < 1.0:
                im = im.resize((max(1, int(round(w * scale))), max(1, int(round(h * scale)))), Image.Resampling.LANCZOS)
        except Exception:
            im = image_obj

        top = tk.Toplevel(self)
        top.title(title)
        top.geometry("1100x780")
        top.minsize(700, 500)

        frame = ttk.Frame(top)
        frame.pack(fill="both", expand=True)

        canvas = tk.Canvas(
            frame,
            bg=self.palette["surface"],
            highlightthickness=1,
            highlightbackground=self.palette["border"],
        )
        xsb = ttk.Scrollbar(frame, orient="horizontal", command=canvas.xview)
        ysb = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        canvas.configure(xscrollcommand=xsb.set, yscrollcommand=ysb.set)

        canvas.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        tk_img = ImageTk.PhotoImage(im)
        canvas.create_image(0, 0, anchor="nw", image=tk_img)
        canvas.image_ref = tk_img
        canvas.configure(scrollregion=(0, 0, im.size[0], im.size[1]))

    def _preview_segmentacion_v2_selected(self) -> None:
        sel = list(self.list_files.curselection())
        if not sel:
            messagebox.showwarning("Segmentacion V2", "Selecciona una imagen para previsualizar.")
            return

        label = self.list_files.get(sel[0])
        source_path = self._file_map.get(label)
        if not source_path or not source_path.exists():
            messagebox.showwarning("Segmentacion V2", "No se encontro la imagen seleccionada.")
            return
        self._open_segment_editor_for_source(source_path)

    def _open_segment_crops_view(self) -> None:
        try:
            from PIL import Image, ImageTk  # type: ignore
        except Exception:
            messagebox.showwarning("Recortes", "Falta Pillow para mostrar recortes.")
            return

        self._sync_items_from_output_text()

        selected = self._get_selected_labels()
        selected_scope = set(selected) if selected else None
        sources = self._collect_review_sources(selected_labels=selected_scope)
        if not sources:
            messagebox.showwarning("Recortes", "No hay imagenes base para mostrar recortes.")
            return

        if not self._ui_alive():
            return

        top = tk.Toplevel(self)
        top.title("Recortes de segmentos")
        top.geometry("1240x800")
        top.minsize(920, 600)
        top.transient(self)

        wrapper = ttk.Frame(top, padding=10)
        wrapper.pack(fill="both", expand=True)

        title_scope = "seleccion" if selected_scope else "lote completo"
        ttk.Label(wrapper, text=f"Recortes Segmentacion V2 ({title_scope})", style="Header.TLabel").pack(anchor="w", pady=(0, 8))

        controls = ttk.Frame(wrapper)
        controls.pack(fill="x", pady=(0, 8))

        ttk.Label(controls, text="Item destino").pack(side="left")
        item_var = tk.StringVar(value="")
        item_combo = ttk.Combobox(controls, textvariable=item_var, state="normal", width=18)
        item_combo.pack(side="left", padx=(8, 8))
        ttk.Label(controls, text="Slot/etiqueta").pack(side="left")
        slot_var = tk.StringVar(value="ENUNCIADO")
        slot_combo = ttk.Combobox(
            controls,
            textvariable=slot_var,
            state="normal",
            width=14,
            values=["ENUNCIADO", "A", "B", "C", "D", "E", "F", "CLAVE"],
        )
        slot_combo.pack(side="left", padx=(8, 8))

        status_var = tk.StringVar(value="Selecciona un recorte y asigna item + slot (puedes escribir valores manuales).")
        ttk.Label(controls, textvariable=status_var, style="SubHeader.TLabel").pack(side="right")

        frame = ttk.Frame(wrapper)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        canvas = tk.Canvas(
            frame,
            bg=self.palette["surface"],
            highlightthickness=1,
            highlightbackground=self.palette["border"],
        )
        ysb = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        xsb = ttk.Scrollbar(frame, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")

        inner = ttk.Frame(canvas)
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.columnconfigure(0, weight=1)

        def _on_inner_configure(_event=None) -> None:
            try:
                canvas.configure(scrollregion=canvas.bbox("all"))
            except Exception:
                pass

        def _on_canvas_configure(event) -> None:
            try:
                canvas.itemconfigure(inner_id, width=max(1, int(event.width)))
            except Exception:
                return

        inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        def _parse_item_num(raw: str) -> int:
            txt = str(raw or "").strip()
            if not txt:
                return 0
            m = re.search(r"\d+", txt)
            if not m:
                return 0
            try:
                return int(m.group(0))
            except Exception:
                return 0

        def _collect_item_values_for_source(source_stem: str) -> List[str]:
            seen: Set[int] = set()
            vals: List[int] = []
            for entry in self._items:
                if len(entry) < 2:
                    continue
                if str(entry[0] or "").strip() != source_stem:
                    continue
                num = self.controller.parsear_numero_original(str(entry[1] or "")) or 0
                if num > 0 and num not in seen:
                    seen.add(num)
                    vals.append(num)
            vals.sort()
            return [str(n) for n in vals]

        def _collect_global_item_values() -> List[str]:
            seen: Set[int] = set()
            vals: List[int] = []
            for entry in self._items:
                if len(entry) < 2:
                    continue
                num = self.controller.parsear_numero_original(str(entry[1] or "")) or 0
                if num > 0 and num not in seen:
                    seen.add(num)
                    vals.append(num)
            vals.sort()
            return [str(n) for n in vals]

        refs: List[Any] = []
        card_widgets: Dict[str, tk.Frame] = {}
        card_info_vars: Dict[str, tk.StringVar] = {}
        card_payloads: Dict[str, Dict[str, Any]] = {}
        source_item_values: Dict[str, List[str]] = {}
        selected_key: Dict[str, str] = {"value": ""}

        def _refresh_item_combo(source_stem: str = "") -> None:
            values = list(source_item_values.get(source_stem, [])) if source_stem else []
            if not values:
                values = _collect_global_item_values()
            item_combo.configure(values=values)
            cur = str(item_var.get() or "").strip()
            if values and (not cur or cur not in values):
                if _parse_item_num(cur) > 0:
                    return
                item_var.set(values[0])
            elif not values:
                item_var.set("")

        def _set_card_selected(seg_key: str) -> None:
            base_border = self.palette.get("border", "#334155")
            active_border = "#2563eb"
            for key, widget in card_widgets.items():
                try:
                    color = active_border if key == seg_key else base_border
                    widget.configure(highlightbackground=color, highlightcolor=color)
                except Exception:
                    continue
            selected_key["value"] = seg_key
            payload = card_payloads.get(seg_key, {})
            src_name = str(payload.get("source_name", "") or "")
            source_stem = str(payload.get("source_stem", "") or "").strip()
            seg_idx = int(payload.get("seg_idx", 0) or 0)
            _refresh_item_combo(source_stem)
            binding = payload.get("binding", {})
            if isinstance(binding, dict):
                b_num = int(self._safe_int(binding.get("item_num", 0), 0))
                b_slot = self._normalize_binding_slot(str(binding.get("slot", "ENUNCIADO") or "ENUNCIADO"))
                if b_num > 0:
                    item_var.set(str(b_num))
                    slot_var.set(b_slot)
                    status_var.set(
                        f"Seleccionado: {src_name} seg-{seg_idx + 1} | asignado item={b_num} slot={b_slot}"
                    )
                    return
            status_var.set(f"Seleccionado: {src_name} seg-{seg_idx + 1}")

        def _assign_selected_crop() -> None:
            seg_key = str(selected_key.get("value", "") or "").strip()
            if not seg_key or seg_key not in card_payloads:
                messagebox.showwarning("Recortes", "Selecciona un recorte primero.")
                return
            item_num = _parse_item_num(item_var.get())
            if item_num <= 0:
                messagebox.showwarning("Recortes", "Selecciona un item destino valido.")
                return
            slot_name = self._normalize_binding_slot(str(slot_var.get() or "ENUNCIADO"))
            payload = card_payloads[seg_key]
            src_path = payload.get("source_path")
            bbox_px = payload.get("bbox_px")
            # Nota: seg_idx puede ser 0 (primer segmento), no usar "or -1".
            seg_idx = self._safe_int(payload.get("seg_idx", -1), -1)
            if not isinstance(src_path, Path) or not isinstance(bbox_px, tuple) or len(bbox_px) != 4:
                messagebox.showerror("Recortes", "No se pudo resolver el recorte seleccionado.")
                return
            if seg_idx < 0:
                messagebox.showerror("Recortes", "Indice de segmento invalido.")
                return

            bbox_norm = self._box_px_to_norm(path=src_path, box_px=bbox_px)
            if bbox_norm is None:
                messagebox.showerror("Recortes", "BBox invalido para ese recorte.")
                return

            marker_name = self._build_binding_marker_name(item_num=item_num, slot=slot_name)
            crop_saved = self._save_figure_crop(
                image_path=src_path,
                marker_name=marker_name,
                bbox_norm=bbox_norm,
            )
            if not crop_saved:
                messagebox.showerror("Recortes", "No se pudo guardar el recorte.")
                return

            self._set_segment_item_binding(
                source_path=src_path,
                segment_idx=int(seg_idx),
                item_num=int(item_num),
                slot=slot_name,
                crop_path=crop_saved,
                confirmed=True,
            )

            source_stem = str(src_path.stem or "").strip()
            linked = 0
            rebuilt_items: List[Tuple[str, str, List[str]]] = []
            for entry in self._items:
                archivo = str(entry[0] or "").strip() if len(entry) > 0 else ""
                item_text = str(entry[1] or "") if len(entry) > 1 else ""
                img_paths = list(entry[2] or []) if len(entry) > 2 else []
                num = self.controller.parsear_numero_original(item_text) or 0
                if archivo == source_stem and num == item_num:
                    item_text = self._insert_explicit_marker_in_slot(
                        text=item_text,
                        marker_name=marker_name,
                        slot=slot_name,
                        path=src_path,
                        numero=item_num,
                    )
                    item_text = self._normalize_math_delimiters(self.controller.normalizar_item_una_linea(item_text))
                    unique_imgs: List[str] = []
                    for p in img_paths + [crop_saved]:
                        p_txt = str(p or "").strip()
                        if p_txt and p_txt not in unique_imgs:
                            unique_imgs.append(p_txt)
                    img_paths = unique_imgs
                    linked += 1
                rebuilt_items.append((archivo, item_text, img_paths))
            self._items = rebuilt_items

            self._preview_images[marker_name] = crop_saved
            self._missing_marker_warned.discard(marker_name)
            self._render_output_from_items()
            self._refresh_training_pairs_from_items()

            info_var = card_info_vars.get(seg_key)
            if info_var is not None:
                info_var.set(f"Asignado item={item_num} slot={slot_name} [[Imagen={marker_name}]]")
            payload["binding"] = {
                "item_num": int(item_num),
                "slot": slot_name,
                "marker_name": marker_name,
            }

            if linked == 0:
                status_var.set(f"Asignado seg-{seg_idx + 1} -> item={item_num} slot={slot_name} (sin item en salida)")
                self._log(
                    f"Recorte asignado: source={src_path.name} seg={seg_idx} -> item={item_num} slot={slot_name} marker={marker_name} (sin item en salida actual)."
                )
            else:
                status_var.set(f"Asignado seg-{seg_idx + 1} -> item={item_num} slot={slot_name} | items={linked}")
                self._log(
                    f"binding_applied item={item_num} slots=[{slot_name}] marker={marker_name} crop={crop_saved} items={linked}"
                )
            _refresh_item_combo(source_stem)

        ttk.Button(
            controls,
            text="Asignar recorte a item/slot",
            command=_assign_selected_crop,
            style="Accent.TButton",
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            controls,
            text="Refrescar items",
            command=lambda: (self._sync_items_from_output_text(), _refresh_item_combo("")),
            style="Ghost.TButton",
        ).pack(side="left")

        max_thumb_w = 320
        max_thumb_h = 220
        grid_cols = 3
        row_idx = 0
        total_segments = 0

        for _idx, label, src in sources:
            src_key = self._seg_v2_source_key(src)
            try:
                segments = self._get_segments_v2_for_source(src)
            except Exception:
                segments = []
            boxes: List[Tuple[int, int, int, int]] = []
            for seg in segments:
                try:
                    x1, y1, x2, y2 = [int(v) for v in seg.bbox]
                except Exception:
                    continue
                if x2 <= x1 or y2 <= y1:
                    continue
                boxes.append((x1, y1, x2, y2))
            source_stem = str(src.stem or "").strip()
            source_item_values[source_stem] = _collect_item_values_for_source(source_stem)
            source_bindings = self._get_segment_bindings_by_source_key(src_key)

            section = ttk.LabelFrame(inner, text=f"{label}  |  segmentos: {len(boxes)}")
            section.grid(row=row_idx, column=0, sticky="ew", padx=4, pady=(8, 6))
            for c in range(grid_cols):
                section.columnconfigure(c, weight=1)
            row_idx += 1

            if not boxes:
                ttk.Label(section, text="(sin recortes)").grid(row=0, column=0, sticky="w", padx=10, pady=(6, 8))
                continue

            try:
                source_im = Image.open(src).convert("RGB")
            except Exception:
                ttk.Label(section, text="(no se pudo abrir imagen fuente)").grid(row=0, column=0, sticky="w", padx=10, pady=(6, 8))
                continue

            for idx_box, (x1, y1, x2, y2) in enumerate(boxes):
                crop = source_im.crop((x1, y1, x2, y2))
                cw, ch = crop.size
                if cw <= 1 or ch <= 1:
                    continue
                thumb_scale = min(max_thumb_w / float(cw), max_thumb_h / float(ch), 1.0)
                if thumb_scale < 1.0:
                    crop = crop.resize(
                        (max(1, int(round(cw * thumb_scale))), max(1, int(round(ch * thumb_scale)))),
                        Image.Resampling.LANCZOS,
                    )
                tk_img = ImageTk.PhotoImage(crop)
                refs.append(tk_img)

                seg_key = f"{src_key}::{idx_box}:{x1}:{y1}:{x2}:{y2}"
                binding_payload = source_bindings.get(int(idx_box), {})
                card_payloads[seg_key] = {
                    "source_path": src,
                    "source_name": label,
                    "source_stem": source_stem,
                    "bbox_px": (x1, y1, x2, y2),
                    "seg_idx": idx_box,
                    "binding": dict(binding_payload) if isinstance(binding_payload, dict) else {},
                }

                r = idx_box // grid_cols
                c = idx_box % grid_cols
                card = tk.Frame(
                    section,
                    bg=self.palette["surface"],
                    highlightthickness=2,
                    highlightbackground=self.palette.get("border", "#334155"),
                    highlightcolor=self.palette.get("border", "#334155"),
                    bd=0,
                    padx=4,
                    pady=4,
                )
                card.grid(row=r, column=c, sticky="nw", padx=6, pady=6)
                card_widgets[seg_key] = card

                img_lbl = tk.Label(card, image=tk_img, bg=self.palette["surface"])
                img_lbl.pack(anchor="w")
                tk.Label(
                    card,
                    text=f"seg-{idx_box + 1}: ({x1},{y1},{x2},{y2})",
                    bg=self.palette["surface"],
                    fg=self.palette["text"],
                ).pack(anchor="w", pady=(4, 0))
                if isinstance(binding_payload, dict) and int(self._safe_int(binding_payload.get("item_num", 0), 0)) > 0:
                    b_num = int(self._safe_int(binding_payload.get("item_num", 0), 0))
                    b_slot = self._normalize_binding_slot(str(binding_payload.get("slot", "ENUNCIADO") or "ENUNCIADO"))
                    b_marker = str(binding_payload.get("marker_name", "") or "").strip() or self._build_binding_marker_name(item_num=b_num, slot=b_slot)
                    info_txt = f"Asignado item={b_num} slot={b_slot} [[Imagen={b_marker}]]"
                else:
                    info_txt = "Sin asignacion"
                info_var = tk.StringVar(value=info_txt)
                card_info_vars[seg_key] = info_var
                ttk.Label(card, textvariable=info_var, style="SubHeader.TLabel").pack(anchor="w", pady=(2, 0))

                def _bind_card(widget, key_sel: str) -> None:
                    widget.bind("<Button-1>", lambda _e, _k=key_sel: _set_card_selected(_k))
                    widget.bind(
                        "<Double-Button-1>",
                        lambda _e, _k=key_sel: (_set_card_selected(_k), _assign_selected_crop()),
                    )

                _bind_card(card, seg_key)
                _bind_card(img_lbl, seg_key)
                total_segments += 1

        top._segment_crop_refs = refs
        if card_payloads:
            first_key = next(iter(card_payloads.keys()))
            _set_card_selected(first_key)
        else:
            _refresh_item_combo("")
        self._log(f"Recortes: {len(sources)} imagen(es), {total_segments} segmento(s) mostrados.")

    def _open_segment_editor_v2(self, *, source_path: Path, initial_boxes: List[Tuple[int, int, int, int]]) -> None:
        try:
            from PIL import Image, ImageTk  # type: ignore
        except Exception:
            messagebox.showwarning("Segmentacion V2", "Falta Pillow para editor de segmentos.")
            return

        if not self._ui_alive():
            return
        try:
            src_img = Image.open(source_path).convert("RGB")
        except Exception as exc:
            messagebox.showerror("Segmentacion V2", f"No se pudo abrir imagen:\n{exc}")
            return

        ow, oh = src_img.size
        if ow <= 1 or oh <= 1:
            messagebox.showwarning("Segmentacion V2", "Imagen invalida para editar segmentos.")
            return

        screen_w = max(1024, int(self.winfo_screenwidth() or 1920))
        screen_h = max(700, int(self.winfo_screenheight() or 1080))
        # Keep some room for toolbars/buttons so controls stay visible.
        max_w = max(900, screen_w - 260)
        max_h = max(440, screen_h - 420)
        scale = min(1.0, max_w / float(ow), max_h / float(oh))
        dw = max(1, int(round(ow * scale)))
        dh = max(1, int(round(oh * scale)))
        disp = src_img.resize((dw, dh), Image.Resampling.LANCZOS) if scale < 1.0 else src_img.copy()

        top = tk.Toplevel(self)
        top.title(f"Editor Segmentacion V2 - {source_path.name}")
        top_w = int(min(screen_w - 40, max(900, min(dw + 110, 1600))))
        top_h = int(min(screen_h - 60, max(600, min(dh + 230, 1100))))
        top.geometry(f"{top_w}x{top_h}+20+20")
        top.minsize(min(860, top_w), min(560, top_h))
        top.transient(self)

        state = {
            "boxes": [list(b) for b in initial_boxes],
            "selected": 0 if initial_boxes else -1,
            "mode": None,
            "edge": (False, False, False, False),  # L,R,T,B
            "start_xy": (0.0, 0.0),
            "start_box": None,
            "edit_enabled": False,
            "edit_index": -1,
            "min_size": 24.0,
            "scale": float(scale),
            "ow": float(ow),
            "oh": float(oh),
        }

        root = ttk.Frame(top, padding=10)
        root.pack(fill="both", expand=True)

        toolbar = ttk.Frame(root)
        toolbar.pack(fill="x", pady=(0, 8))

        info_var = tk.StringVar(value="")
        ttk.Label(toolbar, textvariable=info_var).pack(side="left")
        figure_var = tk.StringVar(value="")
        ttk.Label(toolbar, textvariable=figure_var, style="SubHeader.TLabel").pack(side="right")

        def to_canvas(x: float, y: float) -> Tuple[float, float]:
            return (x * scale, y * scale)

        def to_image(cx: float, cy: float) -> Tuple[float, float]:
            return (cx / max(scale, 1e-8), cy / max(scale, 1e-8))

        def _norm_to_px_local(box_norm: Tuple[float, float, float, float]) -> Optional[Tuple[int, int, int, int]]:
            try:
                x1, y1, x2, y2 = [float(v) for v in box_norm]
            except Exception:
                return None
            px_box = (
                int(round(max(0.0, min(1.0, x1)) * float(ow))),
                int(round(max(0.0, min(1.0, y1)) * float(oh))),
                int(round(max(0.0, min(1.0, x2)) * float(ow))),
                int(round(max(0.0, min(1.0, y2)) * float(oh))),
            )
            return self._normalize_box_px(px_box, width=ow, height=oh)

        frame = ttk.Frame(root)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        canvas = tk.Canvas(
            frame,
            width=max(1, min(dw, int(max_w))),
            height=max(1, min(dh, int(max_h))),
            bg=self.palette["surface"],
            highlightthickness=1,
            highlightbackground=self.palette["border"],
        )
        xsb = ttk.Scrollbar(frame, orient="horizontal", command=canvas.xview)
        ysb = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        canvas.configure(xscrollcommand=xsb.set, yscrollcommand=ysb.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")

        tk_img = ImageTk.PhotoImage(disp)
        canvas.create_image(0, 0, anchor="nw", image=tk_img)
        canvas.image_ref = tk_img
        canvas.config(scrollregion=(0, 0, dw, dh))

        def on_mousewheel(event) -> None:
            try:
                delta = int(getattr(event, "delta", 0))
                step = -1 if delta > 0 else 1
                if bool(getattr(event, "state", 0) & 0x1):
                    canvas.xview_scroll(step, "units")
                else:
                    canvas.yview_scroll(step, "units")
            except Exception:
                return

        canvas.bind("<MouseWheel>", on_mousewheel)
        canvas.bind("<Shift-MouseWheel>", on_mousewheel)
        canvas.bind("<Button-4>", lambda _e: canvas.yview_scroll(-1, "units"))
        canvas.bind("<Button-5>", lambda _e: canvas.yview_scroll(1, "units"))

        def clamp_box(box: List[float]) -> List[float]:
            x1, y1, x2, y2 = box
            x1 = max(0.0, min(state["ow"] - 1.0, x1))
            y1 = max(0.0, min(state["oh"] - 1.0, y1))
            x2 = max(x1 + state["min_size"], min(state["ow"], x2))
            y2 = max(y1 + state["min_size"], min(state["oh"], y2))
            return [x1, y1, x2, y2]

        def draw_boxes() -> None:
            canvas.delete("segbox")
            canvas.delete("figbox")
            boxes = state["boxes"]
            sel = int(state["selected"])
            used_now = self._get_used_segment_indices(source_path)
            used_count = 0
            for i, b in enumerate(boxes):
                x1, y1, x2, y2 = [float(v) for v in b]
                cx1, cy1 = to_canvas(x1, y1)
                cx2, cy2 = to_canvas(x2, y2)
                is_used = i in used_now
                is_edit_active = bool(state.get("edit_enabled")) and int(state.get("edit_index", -1)) == i
                if is_used:
                    used_count += 1
                if i == sel:
                    if is_edit_active:
                        color = "#2563eb"
                    else:
                        color = "#16a34a" if is_used else "#f97316"
                else:
                    color = "#22c55e" if is_used else "#f59e0b"
                width = 3 if i == sel else 2
                status = "OK" if is_used else "PEND"
                if is_edit_active:
                    status = f"{status} EDIT"
                canvas.create_rectangle(cx1, cy1, cx2, cy2, outline=color, width=width, tags=("segbox",))
                canvas.create_text(
                    cx1 + 8,
                    max(10, cy1 - 8),
                    text=f"seg-{i + 1} {status}",
                    fill=color,
                    anchor="w",
                    tags=("segbox",),
                )
                if i == sel:
                    hs = max(4, int(round(6 * scale)))
                    for hx, hy in ((cx1, cy1), (cx2, cy1), (cx1, cy2), (cx2, cy2)):
                        canvas.create_rectangle(hx - hs, hy - hs, hx + hs, hy + hs, outline=color, fill=color, tags=("segbox",))

            manual_boxes: List[Tuple[int, int, int, int]] = []
            for entry in self._get_figure_boxes(source_path):
                if str(entry.get("source", "")).strip().lower() != "manual":
                    continue
                if not self._as_bool(entry.get("confirmed"), True):
                    continue
                bbox_raw = entry.get("bbox_px")
                if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) < 4:
                    continue
                try:
                    manual_boxes.append(tuple(int(v) for v in bbox_raw[:4]))
                except Exception:
                    continue
            yolo_payload = self._get_cached_yolo_suggestion(source_path)
            yolo_box_px: Optional[Tuple[int, int, int, int]] = None
            if yolo_payload and bool(yolo_payload.get("has_figure")) and yolo_payload.get("bbox_norm") is not None:
                yolo_box_px = _norm_to_px_local(tuple(float(v) for v in yolo_payload["bbox_norm"]))  # type: ignore[arg-type]

            if yolo_box_px is not None:
                x1, y1, x2, y2 = [float(v) for v in yolo_box_px]
                cx1, cy1 = to_canvas(x1, y1)
                cx2, cy2 = to_canvas(x2, y2)
                conf = float(yolo_payload.get("conf", 0.0) or 0.0) if yolo_payload else 0.0
                canvas.create_rectangle(
                    cx1,
                    cy1,
                    cx2,
                    cy2,
                    outline="#06b6d4",
                    width=2,
                    dash=(6, 4),
                    tags=("figbox",),
                )
                canvas.create_text(
                    cx1 + 8,
                    max(10, cy1 - 10),
                    text=f"fig-yolo {conf:.2f}",
                    fill="#06b6d4",
                    anchor="w",
                    tags=("figbox",),
                )

            for m_idx, manual_box in enumerate(manual_boxes, start=1):
                x1, y1, x2, y2 = [float(v) for v in manual_box]
                cx1, cy1 = to_canvas(x1, y1)
                cx2, cy2 = to_canvas(x2, y2)
                canvas.create_rectangle(
                    cx1,
                    cy1,
                    cx2,
                    cy2,
                    outline="#a855f7",
                    width=3,
                    tags=("figbox",),
                )
                canvas.create_text(
                    cx1 + 8,
                    min(float(dh) - 12.0, cy2 + 12.0),
                    text=f"fig-manual-{m_idx}",
                    fill="#a855f7",
                    anchor="w",
                    tags=("figbox",),
                )

            if 0 <= sel < len(boxes):
                x1, y1, x2, y2 = [int(round(v)) for v in boxes[sel]]
                st = "OK" if sel in used_now else "PEND"
                edit_state = (
                    "ON"
                    if bool(state.get("edit_enabled")) and int(state.get("edit_index", -1)) == sel
                    else "OFF (doble clic para activar)"
                )
                info_var.set(
                    f"Seleccion: seg-{sel + 1} [{st}]  bbox=({x1},{y1},{x2},{y2})  tamaño={x2-x1}x{y2-y1} | edicion={edit_state} | usados={used_count}/{len(boxes)}"
                )
            else:
                info_var.set(f"Segmentos: {len(boxes)} | usados={used_count}/{len(boxes)}")
            if manual_boxes:
                figure_var.set("Figura: caja manual activa")
            elif yolo_payload and bool(yolo_payload.get("has_figure")) and yolo_box_px is not None:
                try:
                    conf = float(yolo_payload.get("conf", 0.0) or 0.0)
                except Exception:
                    conf = 0.0
                figure_var.set(f"Figura: sugerencia YOLO ({conf:.2f})")
            else:
                figure_var.set("Figura: sin caja")

        def hit_test(ix: float, iy: float) -> Tuple[int, Tuple[bool, bool, bool, bool]]:
            tol = max(6.0, 10.0 / max(scale, 1e-8))
            for i in range(len(state["boxes"]) - 1, -1, -1):
                x1, y1, x2, y2 = [float(v) for v in state["boxes"][i]]
                inside = (x1 <= ix <= x2) and (y1 <= iy <= y2)
                if not inside:
                    continue
                l = abs(ix - x1) <= tol
                r = abs(ix - x2) <= tol
                t = abs(iy - y1) <= tol
                b = abs(iy - y2) <= tol
                return i, (l, r, t, b)
            return -1, (False, False, False, False)

        def on_down(event) -> None:
            cx, cy = canvas.canvasx(event.x), canvas.canvasy(event.y)
            ix, iy = to_image(cx, cy)
            idx, edge = hit_test(ix, iy)
            if idx < 0:
                if bool(state.get("edit_enabled")):
                    state["mode"] = None
                    state["edge"] = (False, False, False, False)
                    state["start_box"] = None
                    draw_boxes()
                    return
                state["selected"] = -1
                state["mode"] = None
                draw_boxes()
                return
            if bool(state.get("edit_enabled")) and int(state.get("edit_index", -1)) != idx:
                state["mode"] = None
                state["edge"] = (False, False, False, False)
                state["start_box"] = None
                draw_boxes()
                return
            state["selected"] = idx
            if not bool(state.get("edit_enabled")):
                state["mode"] = None
                state["edge"] = (False, False, False, False)
                state["start_box"] = None
                draw_boxes()
                return
            state["start_xy"] = (ix, iy)
            state["start_box"] = list(state["boxes"][idx])
            if any(edge):
                state["mode"] = "resize"
                state["edge"] = edge
            else:
                state["mode"] = "move"
                state["edge"] = (False, False, False, False)
            draw_boxes()

        def on_move(event) -> None:
            sel = int(state["selected"])
            mode = state["mode"]
            if sel < 0 or sel >= len(state["boxes"]) or not mode:
                return
            cx, cy = canvas.canvasx(event.x), canvas.canvasy(event.y)
            ix, iy = to_image(cx, cy)
            sx, sy = state["start_xy"]
            dx, dy = ix - sx, iy - sy
            sb = list(state["start_box"] or state["boxes"][sel])
            x1, y1, x2, y2 = [float(v) for v in sb]
            if mode == "move":
                nx1, ny1, nx2, ny2 = x1 + dx, y1 + dy, x2 + dx, y2 + dy
                bw, bh = nx2 - nx1, ny2 - ny1
                if nx1 < 0:
                    nx1, nx2 = 0.0, bw
                if ny1 < 0:
                    ny1, ny2 = 0.0, bh
                if nx2 > state["ow"]:
                    nx2 = state["ow"]
                    nx1 = nx2 - bw
                if ny2 > state["oh"]:
                    ny2 = state["oh"]
                    ny1 = ny2 - bh
                state["boxes"][sel] = [nx1, ny1, nx2, ny2]
            else:
                l, r, t, b = state["edge"]
                nx1, ny1, nx2, ny2 = x1, y1, x2, y2
                if l:
                    nx1 = x1 + dx
                if r:
                    nx2 = x2 + dx
                if t:
                    ny1 = y1 + dy
                if b:
                    ny2 = y2 + dy
                if nx2 - nx1 < state["min_size"]:
                    if l:
                        nx1 = nx2 - state["min_size"]
                    else:
                        nx2 = nx1 + state["min_size"]
                if ny2 - ny1 < state["min_size"]:
                    if t:
                        ny1 = ny2 - state["min_size"]
                    else:
                        ny2 = ny1 + state["min_size"]
                state["boxes"][sel] = clamp_box([nx1, ny1, nx2, ny2])
            draw_boxes()

        def on_up(_event) -> None:
            state["mode"] = None
            state["edge"] = (False, False, False, False)
            state["start_box"] = None

        def on_double_click(event) -> None:
            cx, cy = canvas.canvasx(event.x), canvas.canvasy(event.y)
            ix, iy = to_image(cx, cy)
            idx, _edge = hit_test(ix, iy)
            if idx < 0:
                was_enabled = bool(state.get("edit_enabled"))
                state["selected"] = -1
                state["edit_enabled"] = False
                state["edit_index"] = -1
                state["mode"] = None
                state["edge"] = (False, False, False, False)
                state["start_box"] = None
                draw_boxes()
                if was_enabled:
                    self._log(f"Editor V2: edicion desactivada (doble clic fuera) en {source_path.name}.")
                return
            state["selected"] = idx
            state["edit_enabled"] = True
            state["edit_index"] = idx
            state["mode"] = None
            state["edge"] = (False, False, False, False)
            state["start_box"] = None
            draw_boxes()
            self._log(f"Editor V2: edicion activada (doble clic) en seg-{idx + 1} de {source_path.name}.")

        def add_box() -> None:
            bw = max(120.0, state["ow"] * 0.5)
            bh = max(100.0, state["oh"] * 0.24)
            x1 = max(0.0, (state["ow"] - bw) / 2.0)
            y1 = max(0.0, (state["oh"] - bh) / 2.0)
            x2 = min(state["ow"], x1 + bw)
            y2 = min(state["oh"], y1 + bh)
            state["boxes"].append([x1, y1, x2, y2])
            state["selected"] = len(state["boxes"]) - 1
            draw_boxes()

        def delete_selected() -> None:
            sel = int(state["selected"])
            if sel < 0 or sel >= len(state["boxes"]):
                return
            state["boxes"].pop(sel)
            if state["boxes"]:
                state["selected"] = min(sel, len(state["boxes"]) - 1)
            else:
                state["selected"] = -1
            draw_boxes()

        def save_override() -> None:
            boxes = []
            for b in state["boxes"]:
                x1, y1, x2, y2 = [int(round(v)) for v in b]
                if (x2 - x1) >= 16 and (y2 - y1) >= 16:
                    boxes.append((x1, y1, x2, y2))
            key = self._seg_v2_source_key(source_path)
            self._segmentacion_v2_overrides[key] = boxes
            self._mark_source_reviewed(source_path)
            self._log(f"Segmentacion V2: override guardado para {source_path.name} ({len(boxes)} cuadro(s)).")

        def edit_figure_box() -> None:
            initial_box = self._get_ocr_exclusion_box(source_path)
            if initial_box is None:
                suggestion = self._get_cached_yolo_suggestion(source_path)
                bbox_norm = suggestion.get("bbox_norm") if isinstance(suggestion, dict) else None
                if bbox_norm is not None:
                    initial_box = _norm_to_px_local(tuple(float(v) for v in bbox_norm))
            chosen = self._run_ocr_exclusion_box_ui(source_path, initial_box=initial_box)
            if not chosen:
                return
            if self._set_ocr_exclusion_box(source_path, chosen):
                manual_norm = self._box_px_to_norm(path=source_path, box_px=chosen)
                self._set_cached_yolo_suggestion(
                    source_path,
                    {
                        "has_figure": bool(manual_norm),
                        "bbox_norm": manual_norm,
                        "conf": 1.0,
                        "source": "manual",
                    },
                )
                x1, y1, x2, y2 = chosen
                self._log(f"Caja OCR guardada en {source_path.name}: ({x1},{y1},{x2},{y2})")
            draw_boxes()

        def clear_figure_box() -> None:
            removed = self._clear_manual_figure_boxes(source_path)
            if removed:
                self._log(f"Caja OCR eliminada en {source_path.name} ({removed} caja(s)).")
            cached = self._get_cached_yolo_suggestion(source_path)
            if not cached or cached.get("source") in {"manual", "manual_box", "manual_marker", "none"}:
                bbox_norm, conf, has_fig = self._detect_figure_bbox_yolo(path=source_path)
                self._set_cached_yolo_suggestion(
                    source_path,
                    {
                        "has_figure": bool(has_fig and bbox_norm is not None),
                        "bbox_norm": bbox_norm,
                        "conf": conf,
                        "source": "yolo" if has_fig and bbox_norm is not None else "none",
                    },
                )
            draw_boxes()

        def go_next_image() -> None:
            save_override()
            active_scope = self._segmentation_scope_labels
            reviewed_now, total_now, pending_now = self._segmentation_progress(selected_labels=active_scope)
            self._log(f"Segmentacion progreso: {reviewed_now}/{total_now} revisadas.")
            if pending_now:
                self._log(f"Siguiente pendiente: {pending_now[0]}")
            current_idx = self._find_list_index_for_path(source_path)
            if current_idx < 0:
                self._log("Segmentacion V2: no se encontro la posicion de la imagen actual en la lista.")
                return
            pending = self._next_unreviewed_source(start_after_idx=current_idx, selected_labels=active_scope)
            if pending is None:
                if self._segmentation_route_active:
                    self._segmentation_route_active = False
                    self._refresh_segmentation_done_state(selected_labels=active_scope)
                    if active_scope:
                        self._log(
                            "Paso 1 completado: segmentacion revisada en la seleccion actual. Ya puedes ejecutar 'Paso 2: OCR directo'."
                        )
                    else:
                        self._log(
                            "Paso 1 completado: segmentacion revisada en todo el lote. Ya puedes ejecutar 'Paso 2: OCR directo'."
                        )
                else:
                    self._log("Segmentacion V2: no hay siguiente imagen en la lista.")
                top.destroy()
                return
            next_idx, _next_label, next_path = pending
            if not next_path.exists():
                self._log("Segmentacion V2: la siguiente imagen no esta disponible.")
                return
            self.list_files.selection_clear(0, "end")
            if active_scope:
                total_list = int(self.list_files.size())
                for i in range(total_list):
                    label_i = self.list_files.get(i)
                    if label_i in active_scope:
                        self.list_files.selection_set(i)
            else:
                self.list_files.selection_set(next_idx)
            self.list_files.activate(next_idx)
            self.list_files.see(next_idx)
            top.destroy()
            self.after(40, lambda p=next_path: self._open_segment_editor_for_source(p))

        controls = ttk.Frame(root)
        controls.pack(fill="x", pady=(8, 0))
        ttk.Button(controls, text="Agregar cuadro", command=add_box, style="Ghost.TButton").pack(side="left")
        ttk.Button(controls, text="Eliminar seleccionado", command=delete_selected, style="Secondary.TButton").pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Editar caja figura...", command=edit_figure_box, style="Ghost.TButton").pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(controls, text="Limpiar caja figura", command=clear_figure_box, style="Secondary.TButton").pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(controls, text="Guardar cambios V2", command=save_override, style="Accent.TButton").pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Siguiente imagen", command=go_next_image, style="Ghost.TButton").pack(side="left", padx=(8, 0))

        canvas.bind("<Button-1>", on_down)
        canvas.bind("<Double-Button-1>", on_double_click)
        canvas.bind("<B1-Motion>", on_move)
        canvas.bind("<ButtonRelease-1>", on_up)
        top.bind("<Delete>", lambda _e: delete_selected())
        top.protocol("WM_DELETE_WINDOW", lambda: (save_override(), top.destroy()))
        draw_boxes()
        self._log(
            f"Editor Segmentacion V2 abierto: {source_path.name} ({len(initial_boxes)} cuadro(s) iniciales). "
            "Color: verde=usado en OCR, naranja=pending, azul=edicion activa, cian=YOLO figura, morado=caja figura manual."
        )

    def _expand_paths_with_segmentation_v2(self, paths: List[Tuple[str, Path]]) -> List[Tuple[str, Path]]:
        expanded: List[Tuple[str, Path]] = []
        for label, src in paths:
            segments = self._get_segments_v2_for_source(src)
            source_box = self._get_ocr_exclusion_box(src)
            if len(segments) >= 1:
                self._log(f"[V2] {label}: {len(segments)} segmento(s) (problemas) para transcripcion.")
                for seg in segments:
                    expanded.append((f"{label} [v2-{seg.idx}]", seg.image_path))
                    if source_box is None:
                        continue
                    sx1, sy1, sx2, sy2 = [int(v) for v in seg.bbox]
                    bx1, by1, bx2, by2 = [int(v) for v in source_box]
                    ix1 = max(sx1, bx1)
                    iy1 = max(sy1, by1)
                    ix2 = min(sx2, bx2)
                    iy2 = min(sy2, by2)
                    if ix2 - ix1 < 8 or iy2 - iy1 < 8:
                        continue
                    seg_box = (ix1 - sx1, iy1 - sy1, ix2 - sx1, iy2 - sy1)
                    self._replace_manual_figure_box(seg.image_path, seg_box)
            else:
                self._log(f"[V2] {label}: sin segmentos detectados; se usa imagen original.")
                expanded.append((label, src))
        return expanded

    def _extract_image_marker_names(self, item: str) -> List[str]:
        names: List[str] = []
        for match in BRACKET_TAG_RE.finditer(item or ""):
            token = (match.group(1) or "").strip()
            if not token:
                continue
            name = ""
            if "=" in token:
                key, value = token.split("=", 1)
                if key.strip().lower() == "imagen":
                    name = (value or "").strip()
            else:
                parsed = MARKER_VALUE_RE.match(token)
                if parsed:
                    base = (parsed.group("base") or "").strip()
                    num = (parsed.group("num") or "").strip()
                    opt = (parsed.group("opt") or "").strip()
                    if base and num:
                        name = f"{base}-{num}{('-' + opt) if opt else ''}"
            name = (name or "").strip()
            if name and name not in names:
                names.append(name)
        return names

    def _guess_marker_for_source(self, source_stem: str) -> str:
        for entry in self._items:
            if len(entry) < 2:
                continue
            archivo = str(entry[0])
            item = str(entry[1])
            if archivo != source_stem:
                continue
            markers = self._extract_image_marker_names(item)
            if markers:
                return markers[0]
        return f"{source_stem}-1"

    def _sync_preview_images_from_items(self) -> None:
        """
        Keep preview image map aligned with current items + stored crop paths.
        """
        # Fuente de verdad: bindings confirmados por segmento->item/slot.
        binding_marker_map = self._collect_confirmed_binding_marker_paths()
        for mk, p in binding_marker_map.items():
            self._preview_images[str(mk)] = str(p)

        rebuilt_items: List[Tuple[str, str, List[str]]] = []
        for idx, entry in enumerate(self._items):
            archivo = str(entry[0]) if len(entry) > 0 else ""
            item = str(entry[1] or "") if len(entry) > 1 else ""
            image_paths = list(entry[2] or []) if len(entry) > 2 else []
            markers = self._extract_image_marker_names(item)
            source_has_bindings = self._source_has_confirmed_bindings(source_stem=archivo)
            if source_has_bindings:
                src_path = self._find_source_path_by_stem(archivo)
                item_num = self.controller.parsear_numero_original(item) or 0
                if src_path is not None and item_num > 0:
                    source_key = self._seg_v2_source_key(src_path)
                    source_bindings = self._get_segment_bindings_by_source_key(source_key)
                    applied_slots: List[str] = []
                    for payload in source_bindings.values():
                        if not self._as_bool(payload.get("confirmed"), False):
                            continue
                        if int(self._safe_int(payload.get("item_num", 0), 0)) != int(item_num):
                            continue
                        slot_name = self._normalize_binding_slot(str(payload.get("slot", "ENUNCIADO") or "ENUNCIADO"))
                        marker_bound = str(payload.get("marker_name", "") or "").strip()
                        if not marker_bound:
                            marker_bound = self._build_binding_marker_name(item_num=item_num, slot=slot_name)
                        if marker_bound in markers:
                            continue
                        item = self._insert_explicit_marker_in_slot(
                            text=item,
                            marker_name=marker_bound,
                            slot=slot_name,
                            path=src_path,
                            numero=item_num,
                        )
                        applied_slots.append(slot_name)
                    if applied_slots:
                        item = self.controller.normalizar_item_una_linea(self._normalize_math_delimiters(item))
                        markers = self._extract_image_marker_names(item)
                        self._log(f"binding_applied item={item_num} slots={applied_slots}")

            resolved_paths: List[str] = []
            for mk_raw in markers:
                mk = str(mk_raw or "").strip()
                if not mk:
                    continue
                cur = str(binding_marker_map.get(mk) or self._preview_images.get(mk) or "").strip()
                if self._is_valid_image_path(cur):
                    if cur not in resolved_paths:
                        resolved_paths.append(cur)
                    self._preview_images[mk] = cur
                    self._missing_marker_warned.discard(mk)
                    continue
                if source_has_bindings:
                    if mk and mk not in self._missing_marker_warned:
                        self._missing_marker_warned.add(mk)
                        self._log(f"Aviso: [[Imagen={mk}]] sin binding/crop confirmado.")
                    continue
                if self.auto_crop_var.get():
                    auto_crop = self._auto_assign_crop_for_item_marker(
                        source_stem=archivo,
                        marker_name=mk,
                        item_idx=idx,
                    )
                    if auto_crop:
                        self._preview_images[mk] = auto_crop
                        if auto_crop not in resolved_paths:
                            resolved_paths.append(auto_crop)
                        self._missing_marker_warned.discard(mk)
                        continue
                if mk and mk not in self._missing_marker_warned:
                    self._missing_marker_warned.add(mk)
                    self._log(
                        f"Aviso: [[Imagen={mk}]] sin archivo asociado. "
                        "Usa recorte manual o revisa segmentacion V2."
                    )

            # Si no hubo resolucion por marker, conservar rutas validas existentes.
            if not resolved_paths:
                for p in image_paths:
                    p_txt = str(p or "").strip()
                    if self._is_valid_image_path(p_txt) and p_txt not in resolved_paths:
                        resolved_paths.append(p_txt)

            image_paths = resolved_paths

            rebuilt_items.append((archivo, item, image_paths))
        self._items = rebuilt_items

    def _is_valid_image_path(self, path_str: str) -> bool:
        p = Path(str(path_str or "").strip())
        if not p.exists() or not p.is_file():
            return False
        return p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

    def _find_source_path_by_stem(self, source_stem: str) -> Optional[Path]:
        stem = (source_stem or "").strip()
        if not stem:
            return None
        for src in self._file_map.values():
            try:
                if src and src.exists() and src.stem == stem:
                    return src
            except Exception:
                continue
        return None

    def _marker_to_safe_name(self, marker_name: str) -> str:
        safe = re.sub(r"[^\w\-. ]", "_", (marker_name or "").strip()).strip()
        return safe

    def _find_crop_by_marker_name(self, marker_name: str) -> Optional[str]:
        mk = (marker_name or "").strip()
        if not mk:
            return None
        safe = self._marker_to_safe_name(mk)
        if not safe:
            return None
        candidates: List[Path] = []
        safe_low = safe.lower()

        # Existing preview mappings and item paths.
        for p_txt in list(self._preview_images.values()):
            p = Path(str(p_txt or "").strip())
            if self._is_valid_image_path(str(p)) and p.stem.lower().startswith(safe_low):
                candidates.append(p)
        for entry in self._items:
            if len(entry) < 3:
                continue
            for p_txt in (entry[2] or []):
                p = Path(str(p_txt or "").strip())
                if self._is_valid_image_path(str(p)) and p.stem.lower().startswith(safe_low):
                    candidates.append(p)

        # Common crop roots.
        roots = [self._tmp_crop_root, Path(self._final_crop_dir)]
        exts = [".png", ".jpg", ".jpeg", ".webp", ".bmp"]
        for root in roots:
            try:
                if not root.exists():
                    continue
                for ext in exts:
                    p_exact = root / f"{safe}{ext}"
                    if p_exact.exists() and p_exact.is_file():
                        candidates.append(p_exact)
                for p_glob in root.glob(f"{safe}_*.*"):
                    if p_glob.exists() and p_glob.is_file() and p_glob.suffix.lower() in set(exts):
                        candidates.append(p_glob)
            except Exception:
                continue

        if not candidates:
            return None
        # Pick latest file to match most recent correction.
        try:
            best = max(candidates, key=lambda p: p.stat().st_mtime)
            return str(best)
        except Exception:
            return str(candidates[0])

    def _auto_assign_crop_for_item_marker(self, *, source_stem: str, marker_name: str, item_idx: int) -> Optional[str]:
        src = self._find_source_path_by_stem(source_stem)
        if src is None:
            return None
        try:
            segments = self._get_segments_v2_for_source(src)
        except Exception:
            segments = []
        if not segments:
            return None

        marker_item_indices: List[int] = []
        for j, entry in enumerate(self._items):
            if len(entry) < 2:
                continue
            if str(entry[0] or "").strip() != (source_stem or "").strip():
                continue
            item_text = str(entry[1] or "")
            if self._extract_image_marker_names(item_text):
                marker_item_indices.append(j)

        try:
            seg_idx = marker_item_indices.index(item_idx)
        except Exception:
            seg_idx = 0
        seg_idx = max(0, min(seg_idx, len(segments) - 1))
        seg = segments[seg_idx]
        try:
            bbox_px = tuple(int(v) for v in seg.bbox)
        except Exception:
            return None
        bbox_norm = self._box_px_to_norm(path=src, box_px=bbox_px)
        if bbox_norm is None:
            return None
        crop = self._save_figure_crop(
            image_path=src,
            marker_name=marker_name,
            bbox_norm=bbox_norm,
        )
        return str(crop) if crop else None

    def _assign_crop_to_items(self, *, source_stem: str, marker_name: str, crop_path: str) -> int:
        updated = 0
        new_items: List[Tuple[str, str, List[str]]] = []
        for entry in self._items:
            archivo = str(entry[0])
            item = str(entry[1]) if len(entry) > 1 else ""
            imgs = list(entry[2]) if len(entry) > 2 else []
            if archivo == source_stem:
                markers = self._extract_image_marker_names(item)
                if marker_name in markers:
                    imgs = [crop_path]
                    updated += 1
            new_items.append((archivo, item, imgs))
        self._items = new_items
        try:
            self._refresh_training_pairs_from_items()
        except Exception:
            pass
        return updated

    def _assign_image_to_marker(self, *, marker_name: str, image_path: str) -> int:
        mk = str(marker_name or "").strip()
        img = str(image_path or "").strip()
        if not mk or not img:
            return 0
        updated = 0
        new_items: List[Tuple[str, str, List[str]]] = []
        for entry in self._items:
            archivo = str(entry[0]) if len(entry) > 0 else ""
            item = str(entry[1] or "") if len(entry) > 1 else ""
            imgs = list(entry[2] if len(entry) > 2 else [])
            if mk in self._extract_image_marker_names(item):
                imgs = [img]
                updated += 1
            new_items.append((archivo, item, imgs))
        self._items = new_items
        try:
            self._refresh_training_pairs_from_items()
        except Exception:
            pass
        return updated

    def _guess_marker_from_cursor(self) -> str:
        try:
            idx = self.txt_out.index("insert")
            line_no = int(str(idx).split(".", 1)[0])
            line_text = self.txt_out.get(f"{line_no}.0", f"{line_no}.end")
        except Exception:
            line_text = ""
        markers = self._extract_image_marker_names(line_text)
        if markers:
            return str(markers[0] or "").strip()
        num = self.controller.parsear_numero_original(line_text) or 0
        if num > 0:
            return f"img-{num}"
        missing = self._find_unlinked_markers()
        if missing:
            return missing[0]
        return ""

    def _find_unlinked_markers(self) -> List[str]:
        markers: List[str] = []
        linked: Set[str] = set()
        for entry in self._items:
            if len(entry) < 2:
                continue
            item = str(entry[1] or "")
            for mk in self._extract_image_marker_names(item):
                mkc = str(mk or "").strip()
                if mkc and mkc not in markers:
                    markers.append(mkc)
            if len(entry) >= 3:
                img_list = entry[2] or []
                valid = False
                for p in img_list:
                    if self._is_valid_image_path(str(p or "").strip()):
                        valid = True
                        break
                if valid:
                    for mk in self._extract_image_marker_names(item):
                        mkc = str(mk or "").strip()
                        if mkc:
                            linked.add(mkc)
        for mk, p in (self._preview_images or {}).items():
            mkc = str(mk or "").strip()
            if mkc and self._is_valid_image_path(str(p or "").strip()):
                linked.add(mkc)
        return [mk for mk in markers if mk not in linked]

    def _materialize_selected_segment_image(self, *, image_path: Path, marker_name: str) -> Optional[str]:
        if not image_path.exists() or not image_path.is_file():
            return None
        target_dir = self._tmp_crop_root
        target_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w\-. ]", "_", (marker_name or "").strip()).strip() or image_path.stem
        target = target_dir / f"{safe}.png"
        try:
            if image_path.resolve() == target.resolve():
                return str(target)
        except Exception:
            pass
        try:
            if target.exists():
                target.unlink()
        except Exception:
            pass
        try:
            shutil.copy2(str(image_path), str(target))
            return str(target)
        except Exception:
            try:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                alt = target_dir / f"{safe}_{ts}{image_path.suffix.lower() or '.png'}"
                shutil.copy2(str(image_path), str(alt))
                return str(alt)
            except Exception:
                return None

    def _assign_segmented_image_to_marker(self) -> None:
        self._sync_items_from_output_text()
        default_marker = self._guess_marker_from_cursor()
        marker_name = simpledialog.askstring(
            "Asignar imagen segmentada",
            "Marker de imagen (sin [[Imagen= ]]):",
            initialvalue=default_marker or "",
            parent=self,
        )
        if marker_name is None:
            return
        marker_name = str(marker_name or "").strip()
        if not marker_name:
            messagebox.showwarning("Imagen segmentada", "Marker vacio.")
            return

        initial_dir = self._segmentador_v2.out_root
        if not initial_dir.exists():
            initial_dir = self._runs_dir / "v2_segments"
        selected = filedialog.askopenfilename(
            title=f"Selecciona segmento para [[Imagen={marker_name}]]",
            initialdir=str(initial_dir),
            filetypes=[
                ("Imagenes", "*.png;*.jpg;*.jpeg;*.webp;*.bmp"),
                ("Todos", "*.*"),
            ],
        )
        if not selected:
            return
        src_img = Path(selected)
        if not src_img.exists():
            messagebox.showwarning("Imagen segmentada", "No se encontro la imagen seleccionada.")
            return

        stored = self._materialize_selected_segment_image(image_path=src_img, marker_name=marker_name)
        if not stored:
            messagebox.showerror("Imagen segmentada", "No se pudo copiar la imagen seleccionada.")
            return

        linked = self._assign_image_to_marker(marker_name=marker_name, image_path=stored)
        self._preview_images[marker_name] = stored
        self._missing_marker_warned.discard(marker_name)
        self._push_preview_text(force=True)
        if linked == 0:
            self._log(
                f"Imagen segmentada asociada a [[Imagen={marker_name}]], "
                "pero no hay item con ese marker en la salida actual."
            )
            messagebox.showwarning(
                "Imagen segmentada",
                f"No se encontro [[Imagen={marker_name}]] en la salida actual.",
            )
            return
        self._log(
            f"Imagen segmentada vinculada: [[Imagen={marker_name}]] -> {stored} (items actualizados={linked})."
        )

    def _run_manual_crop_ui(self, image_path: Path, default_marker: str) -> Tuple[Optional[str], Optional[Tuple[float, float, float, float]]]:
        try:
            from PIL import Image, ImageTk  # type: ignore
        except Exception:
            messagebox.showwarning("Recorte manual", "Falta Pillow. Instala: python -m pip install pillow")
            return (None, None)

        try:
            image = Image.open(image_path)
        except Exception as exc:
            messagebox.showerror("Recorte manual", f"No se pudo abrir la imagen:\n{exc}")
            return (None, None)

        max_w, max_h = 1200, 780
        scale = min(max_w / max(image.width, 1), max_h / max(image.height, 1), 1.0)
        disp_w = max(1, int(round(image.width * scale)))
        disp_h = max(1, int(round(image.height * scale)))
        preview_im = image.resize((disp_w, disp_h)) if scale < 1.0 else image.copy()

        top = tk.Toplevel(self)
        top.title(f"Recorte manual - {image_path.name}")
        top.geometry(f"{min(disp_w + 80, 1280)}x{min(disp_h + 170, 920)}")
        top.transient(self)
        top.grab_set()

        frm = ttk.Frame(top, padding=10)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Dibuja el recorte de la figura con el mouse.").pack(anchor="w")

        row = ttk.Frame(frm)
        row.pack(fill="x", pady=(8, 6))
        ttk.Label(row, text="Marker Imagen").pack(side="left")
        marker_var = tk.StringVar(value=(default_marker or "").strip())
        ttk.Entry(row, textvariable=marker_var, width=46).pack(side="left", padx=(8, 0))

        canvas = tk.Canvas(frm, width=disp_w, height=disp_h, bg="#111827", highlightthickness=1, highlightbackground="#374151")
        canvas.pack(fill="both", expand=True)
        tk_img = ImageTk.PhotoImage(preview_im)
        canvas.create_image(0, 0, anchor="nw", image=tk_img)
        canvas.image = tk_img

        state: Dict[str, object] = {"x0": None, "y0": None, "x1": None, "y1": None, "rect": None, "done": False}
        result: Dict[str, object] = {"marker": None, "bbox": None}

        def on_down(event):
            state["x0"] = max(0, min(disp_w, int(event.x)))
            state["y0"] = max(0, min(disp_h, int(event.y)))
            state["x1"] = state["x0"]
            state["y1"] = state["y0"]
            if state.get("rect"):
                try:
                    canvas.delete(state["rect"])  # type: ignore[arg-type]
                except Exception:
                    pass
            state["rect"] = canvas.create_rectangle(
                int(state["x0"]),
                int(state["y0"]),
                int(state["x1"]),
                int(state["y1"]),
                outline="#22c55e",
                width=2,
            )

        def on_move(event):
            if state.get("x0") is None or state.get("rect") is None:
                return
            state["x1"] = max(0, min(disp_w, int(event.x)))
            state["y1"] = max(0, min(disp_h, int(event.y)))
            canvas.coords(
                state["rect"],  # type: ignore[arg-type]
                int(state["x0"]),
                int(state["y0"]),
                int(state["x1"]),
                int(state["y1"]),
            )

        def on_up(_event):
            pass

        canvas.bind("<Button-1>", on_down)
        canvas.bind("<B1-Motion>", on_move)
        canvas.bind("<ButtonRelease-1>", on_up)

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(8, 0))

        def on_cancel():
            state["done"] = True
            top.destroy()

        def on_accept():
            mname = (marker_var.get() or "").strip()
            if not mname:
                messagebox.showwarning("Recorte manual", "Ingresa un nombre de marker [[Imagen=...]].")
                return
            x0 = state.get("x0")
            y0 = state.get("y0")
            x1 = state.get("x1")
            y1 = state.get("y1")
            if None in (x0, y0, x1, y1):
                messagebox.showwarning("Recorte manual", "Dibuja un rectangulo de recorte.")
                return
            xa, xb = sorted([int(x0), int(x1)])
            ya, yb = sorted([int(y0), int(y1)])
            if (xb - xa) < 6 or (yb - ya) < 6:
                messagebox.showwarning("Recorte manual", "Recorte muy pequeno, dibuja un area mayor.")
                return
            bbox = (
                max(0.0, min(1.0, xa / max(disp_w, 1))),
                max(0.0, min(1.0, ya / max(disp_h, 1))),
                max(0.0, min(1.0, xb / max(disp_w, 1))),
                max(0.0, min(1.0, yb / max(disp_h, 1))),
            )
            result["marker"] = mname
            result["bbox"] = bbox
            state["done"] = True
            top.destroy()

        ttk.Button(btns, text="Cancelar", command=on_cancel, style="Ghost.TButton").pack(side="right")
        ttk.Button(btns, text="Guardar recorte", command=on_accept, style="Accent.TButton").pack(side="right", padx=(0, 8))

        top.wait_window()
        return (result.get("marker"), result.get("bbox"))  # type: ignore[return-value]

    def _manual_crop_selected(self) -> None:
        sel = list(self.list_files.curselection())
        if not sel:
            messagebox.showwarning("Recorte manual", "Selecciona una imagen de la lista para recortar.")
            return
        label = self.list_files.get(sel[0])
        src_path = self._file_map.get(label)
        if not src_path or not src_path.exists():
            messagebox.showwarning("Recorte manual", "No se encontro la imagen seleccionada.")
            return

        default_marker = self._guess_marker_for_source(src_path.stem)
        marker_name = simpledialog.askstring(
            "Recorte manual",
            "Marker de imagen (sin [[Imagen= ]]):",
            initialvalue=default_marker,
            parent=self,
        )
        if marker_name is None:
            return
        marker_name = marker_name.strip()
        if not marker_name:
            messagebox.showwarning("Recorte manual", "Marker vacio. Operacion cancelada.")
            return

        marker_name_ui, bbox = self._run_manual_crop_ui(src_path, marker_name)
        if not marker_name_ui or not bbox:
            return
        marker_name = marker_name_ui.strip()

        crop_saved = self._save_figure_crop(
            image_path=src_path,
            marker_name=marker_name,
            bbox_norm=bbox,
        )
        if not crop_saved:
            messagebox.showerror("Recorte manual", "No se pudo guardar el recorte.")
            return

        updated = self._assign_crop_to_items(source_stem=src_path.stem, marker_name=marker_name, crop_path=crop_saved)
        self._preview_images[marker_name] = crop_saved
        self._push_preview_text(force=True)
        self._log(f"Recorte manual guardado: {crop_saved}")
        if updated == 0:
            self._log("Aviso: no se encontro item con ese marker en la salida actual.")

    def _encode_image_data_url(self, path: Path) -> str:
        data = path.read_bytes()
        mime, _ = mimetypes.guess_type(path.name)
        mime = mime or "image/png"
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"

    def _infer_numero(self, path: Path, fallback_idx: int) -> int:
        stem = path.stem or ""
        patterns = [
            r"(?:^|[-_ ])(?:problema|prob|item|img|nro|num|nº|n°)\s*[-_ ]*(\d{1,3})(?:$|[-_ ])",
            r"(?:[-_])(\d{1,3})$",
        ]
        for pat in patterns:
            m = re.search(pat, stem, re.IGNORECASE)
            if not m:
                continue
            try:
                val = int(m.group(1))
                if 0 < val <= 500:
                    return val
            except Exception:
                continue
        return max(1, fallback_idx)

    def _normalize_plain_ocr(self, text: str) -> str:
        txt = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        txt = txt.replace("\t", " ")
        txt = txt.replace("Â£", " ").replace("Ã¦", " ")
        txt = re.sub(r"\s+", " ", txt)
        return txt.strip()

    def _is_false_option_marker_in_angle_context(self, text: str, marker_pos: int) -> bool:
        """
        Avoid interpreting geometric notation like 'm\\angle C)' as option marker 'C)'.
        """
        if marker_pos < 0:
            return False
        left = (text[max(0, marker_pos - 28) : marker_pos] or "").lower()
        return re.search(r"(\\angle|∠)\s*$", left) is not None

    def _extract_options_loose(self, text: str) -> tuple[str, Dict[str, str]]:
        """
        Extract A-E options allowing partial sets (useful for continuation blocks).
        Returns (enunciado_prefix, options_found).
        """
        raw_text = text or ""
        option_re = re.compile(r"(?<![A-Za-z0-9])([A-Ea-e])\s*[\)\].:]\s*")
        matches_all = list(option_re.finditer(raw_text))
        matches = [
            m
            for m in matches_all
            if not self._is_false_option_marker_in_angle_context(raw_text, m.start(1))
        ]
        if not matches:
            return (text or "").strip(), {}

        first = matches[0]
        enunciado = (raw_text[: first.start()] or "").strip()
        enunciado = enunciado.replace(SEP_LINE, " ").replace(SEP_OPT, " ")
        enunciado = re.sub(r"\s+", " ", enunciado).strip()
        options: Dict[str, str] = {}
        for i, m in enumerate(matches):
            label = m.group(1).upper()
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_text)
            chunk = (raw_text[start:end] or "").strip()
            chunk = chunk.replace(SEP_LINE, " ").replace(SEP_OPT, " ")
            chunk = chunk.replace("Â£", " ").replace("Ã¦", " ")
            chunk = re.sub(r"\s+", " ", chunk).strip()
            # Keep first seen for each label.
            if label not in options and chunk:
                options[label] = chunk
        return enunciado, options

    def _extract_options(self, text: str) -> tuple[str, Dict[str, str]]:
        """
        Strict extraction for normal item parsing.
        Requires at least A and B to reduce false positives in enunciados.
        """
        enunciado, options = self._extract_options_loose(text)
        if not options:
            return (text or "").strip(), {}
        labels = set(options.keys())
        if "A" not in labels or "B" not in labels:
            return (text or "").strip(), {}
        return enunciado, options

    def _extract_options_segments(self, text: str) -> tuple[str, Dict[str, str]]:
        """
        Parse options directly from a scan-like string body using labels A)-E),
        tolerant to broken separators/spaces from OCR/LLM.
        """
        raw = self._decode_scan_escapes(text or "")
        if not raw:
            return ("", {})

        # Prefer explicit scan separator start (e.g. '£A)') to avoid capturing
        # notation like 'm\\angle C)' from the enunciado as option label.
        options_start = -1
        m_sep_a = re.search(rf"{re.escape(SEP_LINE)}\s*A\)\s*", raw)
        if m_sep_a:
            options_start = m_sep_a.start()
        else:
            m_plain_a = re.search(r"(?<![A-Za-z0-9])A\)\s*", raw)
            if m_plain_a:
                tail = raw[m_plain_a.start() :]
                has_b = re.search(r"(?<![A-Za-z0-9])B\)\s*", tail) is not None
                has_c = re.search(r"(?<![A-Za-z0-9])C\)\s*", tail) is not None
                if has_b and has_c:
                    options_start = m_plain_a.start()

        enunciado_src = raw
        options_src = raw
        if options_start > 0:
            enunciado_src = (raw[:options_start] or "").strip()
            options_src = (raw[options_start:] or "").strip()

        label_re = re.compile(r"(?<![A-Za-z0-9])([A-Ea-e])\)\s*")
        matches_all = list(label_re.finditer(options_src))
        matches = [
            m
            for m in matches_all
            if not self._is_false_option_marker_in_angle_context(options_src, m.start(1))
        ]
        if not matches:
            return ((raw or "").strip(), {})

        if options_start > 0:
            enunciado = enunciado_src
        else:
            enunciado = (options_src[: matches[0].start()] or "").strip()
        enunciado = enunciado.replace(SEP_LINE, " ").replace(SEP_OPT, " ")
        enunciado = re.sub(r"\s+", " ", enunciado).strip()

        options: Dict[str, str] = {}
        for i, m in enumerate(matches):
            label = (m.group(1) or "").upper()
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(options_src)
            chunk = (options_src[start:end] or "").strip()
            chunk = chunk.replace(SEP_LINE, " ").replace(SEP_OPT, " ")
            chunk = chunk.replace("Â£", " ").replace("Ã¦", " ")
            chunk = re.sub(r"\s+", " ", chunk).strip()
            if label not in options and chunk:
                options[label] = chunk
        return enunciado, options

    def _body_without_tags_and_markers(self, item: str) -> str:
        txt = (item or "").strip()
        m = ITEM_HEADER_RE.search(txt)
        body = txt[m.end():].strip() if m else txt
        body = TAG_CURSO_RE.sub(" ", body)
        body = TAG_TEMA_RE.sub(" ", body)
        body = TAG_SUBTEMA_RE.sub(" ", body)
        body = self._remove_image_markers(body)
        body = re.sub(r"\s+", " ", body).strip()
        return body

    def _item_has_complete_options(self, item: str) -> bool:
        body = self._body_without_tags_and_markers(item)
        _enu, options = self._extract_options_loose(body)
        required = ("A", "B", "C", "D", "E")
        if not all(k in options for k in required):
            return False
        # Treat placeholder-only options as incomplete so continuation can be merged
        # from subsequent images.
        for label in required:
            if self._is_placeholder_option_value(options.get(label, "")):
                return False
        return True

    def _is_placeholder_option_value(self, value: str) -> bool:
        txt = (value or "").strip()
        if not txt:
            return True
        txt = txt.replace(SEP_LINE, " ").replace(SEP_OPT, " ")
        txt = txt.replace("Â£", " ").replace("Ã¦", " ")
        txt = re.sub(r"\s+", " ", txt).strip()
        if txt.startswith("$") and txt.endswith("$") and len(txt) >= 2:
            txt = txt[1:-1].strip()
        compact = txt.replace(" ", "").lower()
        if not compact:
            return True
        if compact in {"...", "…", "....", "..", "?", "??", "???", r"\ldots", r"\cdots", r"\dots"}:
            return True
        if re.fullmatch(r"[.\u2026]+", compact):
            return True
        if re.fullmatch(r"[?]+", compact):
            return True
        return False

    def _item_option_count(self, item: str) -> int:
        body = self._body_without_tags_and_markers(item)
        _enu, options = self._extract_options_loose(body)
        return len(options)

    def _merge_orphan_options_into_item(self, base_item: str, orphan_item: str, *, fallback_numero: int) -> str:
        base = (base_item or "").strip()
        orphan = (orphan_item or "").strip()
        if not base or not orphan:
            return base

        orphan_body = self._body_without_tags_and_markers(orphan)
        _orphan_enunciado, orphan_options = self._extract_options_loose(orphan_body)
        return self._merge_options_into_item(base_item=base, extra_options=orphan_options, fallback_numero=fallback_numero)

    def _merge_options_into_item(self, *, base_item: str, extra_options: Dict[str, str], fallback_numero: int) -> str:
        base = (base_item or "").strip()
        if not base:
            return base
        if not extra_options:
            return base

        numero = self.controller.parsear_numero_original(base) or fallback_numero
        curso, tema, subtema = self._extract_existing_tags(base)
        marker = self._extract_first_image_marker_name(base)
        base_body = self._body_without_tags_and_markers(base)
        base_enunciado, base_options = self._extract_options_loose(base_body)

        merged_options: Dict[str, str] = dict(base_options)
        for label in ("A", "B", "C", "D", "E"):
            value = (extra_options.get(label) or "").strip()
            if not value:
                continue
            current = (merged_options.get(label) or "").strip()
            if (label not in merged_options) or self._is_placeholder_option_value(current):
                merged_options[label] = value

        rebuilt = self._build_scan_item_strict(numero=numero, enunciado=base_enunciado or base_body, options=merged_options)
        rebuilt = self._inject_metadata_tags(rebuilt, curso=curso, tema=tema, subtema=subtema)
        if marker and not self._has_image_marker(rebuilt):
            rebuilt = f"{rebuilt} [[Imagen={marker}]]".strip()
        rebuilt = self._move_image_marker_before_options(rebuilt)
        rebuilt = self._normalize_math_delimiters(rebuilt)
        rebuilt = self._standardize_item_options(rebuilt, fallback_numero=numero)
        return rebuilt

    def _merge_continuation_into_item(
        self,
        *,
        base_item: str,
        continuation_text: str,
        fallback_numero: int,
    ) -> str:
        """
        Merge a continuation block (usually from next image) into the pending item.
        Continuation may contain:
        - remaining statement text
        - options A-E (partial or full)
        """
        base = (base_item or "").strip()
        cont_raw = self.controller.normalizar_item_una_linea(self._decode_scan_escapes(continuation_text or ""))
        if not base or not cont_raw:
            return base

        numero = self.controller.parsear_numero_original(base) or fallback_numero
        curso, tema, subtema = self._extract_existing_tags(base)
        marker = self._extract_first_image_marker_name(base)

        base_body = self._body_without_tags_and_markers(base)
        base_enunciado, base_options = self._extract_options_loose(base_body)

        cont_body = cont_raw
        m_cont = ITEM_HEADER_RE.search(cont_body)
        if m_cont:
            cont_body = cont_body[m_cont.end() :].strip()
        cont_body = TAG_CURSO_RE.sub(" ", cont_body)
        cont_body = TAG_TEMA_RE.sub(" ", cont_body)
        cont_body = TAG_SUBTEMA_RE.sub(" ", cont_body)
        cont_body = self._remove_image_markers(cont_body)
        cont_body = re.sub(r"\s+", " ", cont_body).strip()
        cont_enunciado, cont_options = self._extract_options_loose(cont_body)

        merged_enunciado = (base_enunciado or base_body or "").strip()
        add_enunciado = (cont_enunciado or "").strip()
        if add_enunciado:
            base_key = self._norm_key(merged_enunciado)
            add_key = self._norm_key(add_enunciado)
            if add_key and (add_key not in base_key):
                if merged_enunciado:
                    merged_enunciado = f"{merged_enunciado} {add_enunciado}".strip()
                else:
                    merged_enunciado = add_enunciado

        merged_options: Dict[str, str] = dict(base_options)
        for label in ("A", "B", "C", "D", "E"):
            val = (cont_options.get(label) or "").strip()
            if not val:
                continue
            current = (merged_options.get(label) or "").strip()
            if (label not in merged_options) or self._is_placeholder_option_value(current):
                merged_options[label] = val

        rebuilt = self._build_scan_item_strict(
            numero=numero,
            enunciado=merged_enunciado or base_enunciado or base_body,
            options=merged_options,
        )
        rebuilt = self._inject_metadata_tags(rebuilt, curso=curso, tema=tema, subtema=subtema)
        if marker and not self._has_image_marker(rebuilt):
            rebuilt = f"{rebuilt} [[Imagen={marker}]]".strip()
        rebuilt = self._move_image_marker_before_options(rebuilt)
        rebuilt = self._normalize_math_delimiters(rebuilt)
        rebuilt = self._standardize_item_options(rebuilt, fallback_numero=numero)
        return rebuilt

    def _option_to_math(self, text: str) -> str:
        normalized = normalize_latex_option(text)
        if normalized.text:
            return normalized.text
        return "$?$"

    def _normalize_degree_notation(self, text: str) -> str:
        txt = (text or "").strip()
        if not txt or "°" not in txt:
            return txt
        # Common case: 30° -> 30^\circ
        txt = re.sub(r"(\d)\s*°", r"\1^\\circ", txt)
        # Variable angles: x° -> x^\circ
        txt = re.sub(r"([A-Za-z])\s*°", r"\1^\\circ", txt)
        # Fallback for any remaining degree symbols.
        txt = txt.replace("°", r"\circ")
        return txt

    def _normalize_enunciado_math(self, text: str) -> str:
        normalized = normalize_latex_statement(text)
        if normalized.text:
            return normalized.text
        return (text or "").strip()

    def _build_scan_item_strict(self, *, numero: int, enunciado: str, options: Dict[str, str]) -> str:
        enu = (enunciado or "").strip()
        if not enu:
            enu = "[[ocr_sin_texto]]"
        enu = self._normalize_enunciado_math(enu)
        has_all = all(k in options for k in ("A", "B", "C", "D", "E"))
        if has_all:
            return (
                f"\\item[\\textbf{{{numero}.}}] {enu} "
                f"{SEP_LINE}A) {self._option_to_math(options['A'])}"
                f"{SEP_OPT}B) {self._option_to_math(options['B'])}"
                f"{SEP_OPT}C) {self._option_to_math(options['C'])}"
                f"{SEP_LINE}D) {self._option_to_math(options['D'])}"
                f"{SEP_OPT}{SEP_OPT}E) {self._option_to_math(options['E'])}{SEP_LINE}"
            )
        if options:
            parts: List[str] = []
            for label in ("A", "B", "C", "D", "E"):
                if label in options:
                    parts.append(f"{label}) {self._option_to_math(options[label])}")
            return f"\\item[\\textbf{{{numero}.}}] {enu} {SEP_LINE}{SEP_OPT.join(parts)}{SEP_LINE}"
        return f"\\item[\\textbf{{{numero}.}}] {enu}"

    def _build_scan_item(self, *, numero: int, enunciado: str, options: Dict[str, str]) -> str:
        return self._build_scan_item_strict(numero=numero, enunciado=enunciado, options=options)

    def _item_from_plain_ocr(self, text: str, path: Path, idx: int) -> str:
        normalized = self.controller.normalizar_item_una_linea(text)
        if normalized.startswith("\\item"):
            return normalized
        numero = self._infer_numero(path, idx)
        plain = self._normalize_plain_ocr(text)
        if not plain:
            plain = f"[[ocr_sin_texto={path.name}]]"
        if not self.auto_format_var.get():
            return f"\\item[\\textbf{{{numero}.}}] {plain}"
        enunciado, options = self._extract_options(plain)
        return self._build_scan_item(numero=numero, enunciado=enunciado, options=options)

    def _plan_image_tag_positions(
        self,
        *,
        raw_items: List[str],
        segment_count: int,
    ) -> Dict[int, int]:
        """
        Map item position (1-based) -> segment index (0-based).
        Conservative policy to avoid false positives:
        1) Prioritize items with explicit image hints (grafico/figura/...).
        2) If there are no hints:
           - only auto-assign when there is a single item in the image.
           - for multi-item images, assign none (manual/continuation logic can still attach).
        """
        plan: Dict[int, int] = {}
        if segment_count <= 0 or not raw_items:
            return plan
        item_count = len(raw_items)
        scored: List[Tuple[int, int]] = []  # (score, pos)
        for pos, raw in enumerate(raw_items, start=1):
            txt = self._decode_scan_escapes(raw or "")
            score = self._item_image_hint_score(txt)
            if score > 0:
                scored.append((score, pos))
        scored.sort(key=lambda t: (-t[0], t[1]))
        chosen: List[int] = []
        for _score, pos in scored:
            if len(chosen) >= segment_count:
                break
            if pos not in chosen:
                chosen.append(pos)
        if not chosen:
            if item_count == 1:
                chosen.append(1)
            else:
                return plan
        for seg_idx, pos in enumerate(chosen):
            if seg_idx >= segment_count:
                break
            plan[pos] = seg_idx
        return plan

    def _item_image_hint_score(self, raw_text: str) -> int:
        txt = (raw_text or "").lower()
        if not txt:
            return 0
        score = 0
        if IMAGE_HINT_RE.search(txt):
            score += 1
        for phrase in IMAGE_HINT_PHRASES:
            if phrase in txt:
                score += 2
        return score

    def _should_attach_segment_on_merge(self, *, base_item: str, continuation_text: str) -> bool:
        if self._has_image_marker(base_item):
            return True
        txt = self._decode_scan_escapes(continuation_text or "")
        if self._item_image_hint_score(txt) > 0:
            return True
        if re.search(r"\[\[\s*imagen\s*=", txt, re.IGNORECASE):
            return True
        return False

    def _has_image_marker(self, text: str) -> bool:
        if not text:
            return False
        for match in BRACKET_TAG_RE.finditer(text):
            token = (match.group(1) or "").strip()
            if not token:
                continue
            if "=" in token:
                key = token.split("=", 1)[0].strip().lower()
                if key == "imagen":
                    return True
                continue
            if MARKER_VALUE_RE.match(token):
                return True
        return False

    def _standard_image_marker(self, *, path: Path, numero: int, opt: str = "") -> str:
        # Placeholder marker while transcribing; converted on DB save.
        suffix = f"-{opt}" if opt else ""
        return f"[[Imagen=img-{numero}{suffix}]]"

    def _normalize_image_markers(self, text: str, *, path: Path, fallback_numero: int) -> str:
        if not text:
            return text

        def repl(match: re.Match) -> str:
            token = (match.group(1) or "").strip()
            if not token:
                return match.group(0)
            value = token
            if "=" in token:
                key, raw_value = token.split("=", 1)
                if key.strip().lower() != "imagen":
                    return match.group(0)
                value = raw_value.strip()
            parsed = MARKER_VALUE_RE.match(value)
            if not parsed:
                return match.group(0)
            numero = int(parsed.group("num"))
            opt = (parsed.group("opt") or "").strip()
            return self._standard_image_marker(path=path, numero=numero, opt=opt)

        normalized = BRACKET_TAG_RE.sub(repl, text)
        # If there were malformed markers, ensure at least a normalized one exists when token "Imagen=" appears.
        if ("[[Imagen=" in normalized or "[[imagen=" in normalized) and not self._has_image_marker(normalized):
            marker = self._standard_image_marker(path=path, numero=fallback_numero)
            normalized = f"{normalized} {marker}".strip()
        return normalized

    def _insert_image_marker(self, text: str, *, path: Path, numero: int) -> str:
        marker = self._standard_image_marker(path=path, numero=numero)
        if f"{SEP_LINE}A)" in text:
            return text.replace(f"{SEP_LINE}A)", f" {marker} {SEP_LINE}A)", 1)
        if "Â£A)" in text:
            return text.replace("Â£A)", f" {marker} Â£A)", 1)
        if re.search(r"\bA\)", text):
            return re.sub(r"\bA\)", f"{marker} A)", text, count=1)
        return f"{text} {marker}".strip()

    def _decode_scan_escapes(self, text: str) -> str:
        if not text:
            return text
        out = text
        replacements = {
            r"\\u00a3": SEP_LINE,
            r"\u00a3": SEP_LINE,
            "Â£": SEP_LINE,
            "Ã‚Â£": SEP_LINE,
            r"\\u00e6": SEP_OPT,
            r"\u00e6": SEP_OPT,
            "Ã¦": SEP_OPT,
            "ÃƒÂ¦": SEP_OPT,
        }
        for src, dst in replacements.items():
            out = out.replace(src, dst)
        return out

    def _normalize_math_delimiters(self, text: str) -> str:
        if not text:
            return text

        # In this pipeline we prefer inline math; malformed double-dollars
        # from OCR/LLM often break equations, so normalize early.
        out = (text or "").replace("$$", "$")
        out = re.sub(r"\$[ \t]+\$", "$", out)
        out = re.sub(r"\${3,}", "$", out)
        # Number + unit split by OCR/LLM: $12$u -> $12u$
        out = re.sub(
            r"\$([0-9]+(?:[.,][0-9]+)?)\$\s*(cm|mm|km|dm|m|u|kg|g|mg|rad|h|min|s)\b",
            r"$\1\2$",
            out,
            flags=re.IGNORECASE,
        )
        # Common OCR/format artifacts: fragmented math around coefficients/parentheses.
        out = re.sub(r"\$([0-9]+(?:[.,][0-9]+)?)\$\s*\(\s*\$([^$]+)\$\s*\)", r"$\1(\2)$", out)
        out = re.sub(r"\$([0-9]+(?:[.,][0-9]+)?)\$\s*\(\s*([A-Z]{1,3})\s*\)", r"$\1(\2)$", out)
        out = re.sub(
            r"\b([A-Z]{1,3})\s*=\s*\$([0-9]+(?:[.,][0-9]+)?)\$\s*\(\s*\$([^$]+)\$\s*\)",
            r"$\1 = \2(\3)$",
            out,
        )
        out = re.sub(
            r"\b([A-Z]{1,3})\s*=\s*\$([0-9]+(?:[.,][0-9]+)?)\$\s*\(\s*([A-Z]{1,3})\s*\)",
            r"$\1 = \2(\3)$",
            out,
        )
        out = re.sub(r"\b([A-Z]{1,3})\s*=\s*\$([0-9]+(?:[.,][0-9]+)?)\$", r"$\1 = \2$", out)
        # Rejoin truncated math before a parenthesized factor: $AC = 2$(AB) -> $AC = 2(AB)$
        out = re.sub(
            r"\$([^$]+?)\$\s*\(\s*([A-Za-z0-9\\][A-Za-z0-9\\\s]{0,40})\s*\)",
            lambda m: f"${(m.group(1) or '').strip()}({(m.group(2) or '').strip()})$",
            out,
        )
        # Fix broken angle equality fragments, including partial tokenization.
        out = re.sub(
            r"\$?m\\angle\s*\$?([A-Z]{3})\$?\s*=\s*\$?([0-9]+(?:[.,][0-9]+)?)\$?\s*\(\s*\$?m\\angle\s*([A-Z]{3})\$?\s*\)",
            r"$m\\angle \1 = \2(m\\angle \3)$",
            out,
            flags=re.IGNORECASE,
        )
        # Fix tokenized prompt target: Calcule $m$\angle BCA -> Calcule $m\angle BCA$
        out = re.sub(
            r"\b(calcule|halle|determine)\s+\$?m\$?\s*(?:\\angle|∠)\s*([A-Z]{3})\b",
            lambda m: f"{(m.group(1) or '').strip()} $m\\angle {(m.group(2) or '').strip()}$",
            out,
            flags=re.IGNORECASE,
        )
        out = re.sub(r"\$m\$\s*(?:\\angle|∠)\s*([A-Z]{3})", r"$m\\angle \1$", out, flags=re.IGNORECASE)

        def repl_inline(match: re.Match) -> str:
            body = (match.group(1) or "").strip()
            return f"${body}$"

        def repl_block(match: re.Match) -> str:
            body = (match.group(1) or "").strip()
            return f"${body}$"

        out = re.sub(r"\\\(\s*(.*?)\s*\\\)", repl_inline, out)
        out = re.sub(r"\\\[\s*(.*?)\s*\\\]", repl_block, out)
        # Join common broken relation: $expr$ = k($expr$)
        out = re.sub(
            r"\$([^$]+)\$\s*=\s*([^\$£æ]+?)\(\s*\$([^$]+)\$\s*\)",
            r"$\1 = \2(\3)$",
            out,
        )
        # Merge split math around operators: $a$ + $b$ -> $a + b$
        out = re.sub(r"\$([^$]+)\$\s*([=+\-*/])\s*\$([^$]+)\$", r"$\1 \2 \3$", out)
        # Remove separator noise accidentally wrapped as math.
        out = re.sub(r"\$\s*([£æ])\s*\$", r" \1 ", out)
        # Normalize degree notation only inside math fragments.
        out = re.sub(r"\$\$(.*?)\$\$", lambda m: f"${self._normalize_degree_notation(m.group(1))}$", out)
        out = re.sub(
            r"(?<!\$)\$(?!\$)(.*?)(?<!\$)\$(?!\$)",
            lambda m: f"${self._normalize_degree_notation(m.group(1))}$",
            out,
        )
        # Common OCR artifact: trailing math closer after prompt variable.
        out = re.sub(r"\b(calcule|halle|determine)\s+([A-Za-z])\$", r"\1 \2", out, flags=re.IGNORECASE)
        out = re.sub(r"\b(calcule|halle|determine)\$(\S)", r"\1 $\2", out, flags=re.IGNORECASE)
        out = re.sub(r"\$(el|la|los|las|de|del|al|y|o)\$", r"\1", out, flags=re.IGNORECASE)
        # If OCR leaves broken/odd '$', drop unmatched delimiters instead of stretching math to EOL.
        rebuilt: List[str] = []
        open_idx: Optional[int] = None
        for ch in out:
            if ch != "$":
                if open_idx is not None and ch in (SEP_LINE, SEP_OPT):
                    # Prevent crossing math fragments into option separators.
                    if 0 <= open_idx < len(rebuilt) and rebuilt[open_idx] == "$":
                        rebuilt.pop(open_idx)
                    open_idx = None
                rebuilt.append(ch)
                continue
            prev = rebuilt[-1] if rebuilt else ""
            if open_idx is None:
                if prev in (SEP_LINE, SEP_OPT):
                    continue
                open_idx = len(rebuilt)
                rebuilt.append("$")
            else:
                if prev in (SEP_LINE, SEP_OPT):
                    # Keep the content and drop the unmatched opener.
                    if 0 <= open_idx < len(rebuilt) and rebuilt[open_idx] == "$":
                        rebuilt.pop(open_idx)
                    open_idx = None
                    continue
                rebuilt.append("$")
                open_idx = None
        if open_idx is not None and 0 <= open_idx < len(rebuilt) and rebuilt[open_idx] == "$":
            rebuilt.pop(open_idx)
        out = "".join(rebuilt)
        # Last-resort guard: never leave odd '$' count.
        while out.count("$") % 2 != 0:
            last = out.rfind("$")
            if last < 0:
                break
            out = out[:last] + out[last + 1 :]
        out = re.sub(r"\s+", " ", out).strip()
        normalized = normalize_latex_scan_item_text(out)
        return normalized.text or out

    def _enforce_scan_item_contract(self, item: str, *, fallback_numero: int) -> str:
        """
        Final hard normalization of scan format:
        - one header
        - options A-E rebuilt with strict £/æ separators
        - each option fully wrapped in $...$
        """
        txt = (item or "").strip()
        if not txt:
            return txt
        header_match = ITEM_HEADER_RE.search(txt)
        if not header_match:
            return txt

        numero = self.controller.parsear_numero_original(txt) or fallback_numero
        curso, tema, subtema = self._extract_existing_tags(txt)
        marker_name = self._extract_first_image_marker_name(txt)
        body = self._body_without_tags_and_markers(txt)

        enunciado, options = self._extract_options_segments(body)
        if len(options) < 2:
            # keep item without forcing fake options
            base = f"\\item[\\textbf{{{numero}.}}] {enunciado or body}".strip()
            rebuilt = self._inject_metadata_tags(base, curso=curso, tema=tema, subtema=subtema)
            if marker_name and not self._has_image_marker(rebuilt):
                rebuilt = f"{rebuilt} [[Imagen={marker_name}]]".strip()
            rebuilt = self._move_image_marker_before_options(rebuilt)
            return self._normalize_math_delimiters(rebuilt)

        rebuilt = self._build_scan_item_strict(numero=numero, enunciado=enunciado or body, options=options)
        rebuilt = self._inject_metadata_tags(rebuilt, curso=curso, tema=tema, subtema=subtema)
        if marker_name and not self._has_image_marker(rebuilt):
            rebuilt = f"{rebuilt} [[Imagen={marker_name}]]".strip()
        rebuilt = self._move_image_marker_before_options(rebuilt)
        rebuilt = self._normalize_math_delimiters(rebuilt)
        return rebuilt

    def _normalize_item_header(self, text: str, *, fallback_numero: int) -> str:
        if not text:
            return text

        def repl(match: re.Match) -> str:
            numero = int(match.group(1))
            return f"\\item[\\textbf{{{numero}.}}]"

        out = ITEM_HEADER_NUM_RE.sub(repl, text, count=1)
        if out.startswith("\\item"):
            return out
        return f"\\item[\\textbf{{{fallback_numero}.}}] {out}".strip()

    def _is_image_token(self, token: str) -> bool:
        value = (token or "").strip()
        if not value:
            return False
        if "=" in value:
            key = value.split("=", 1)[0].strip().lower()
            return key == "imagen"
        return MARKER_VALUE_RE.match(value) is not None

    def _move_image_marker_before_options(self, text: str) -> str:
        if not text:
            return text
        global_markers: List[str] = []

        def strip_markers(match: re.Match) -> str:
            token = (match.group(1) or "").strip()
            if not self._is_image_token(token):
                return match.group(0)
            marker_name = ""
            if "=" in token:
                k, v = token.split("=", 1)
                if k.strip().lower() == "imagen":
                    marker_name = (v or "").strip()
            else:
                marker_name = token
            parsed = MARKER_VALUE_RE.match(marker_name)
            if parsed:
                opt = str(parsed.group("opt") or "").strip().upper()
                if opt in {"A", "B", "C", "D", "E"}:
                    # Markers de opciones se mantienen en su posicion.
                    return match.group(0)
            global_markers.append(match.group(0))
            return " "

        base = BRACKET_TAG_RE.sub(strip_markers, text)
        base = re.sub(r"\s+", " ", base).strip()
        if not global_markers:
            return base
        marker = global_markers[0]
        pos = base.find(f"{SEP_LINE}A)")
        if pos >= 0:
            return f"{base[:pos].rstrip()} {marker} {base[pos:].lstrip()}".strip()
        match = re.search(r"\bA\)", base)
        if match:
            return f"{base[:match.start()].rstrip()} {marker} {base[match.start():].lstrip()}".strip()
        return f"{base} {marker}".strip()

    def _remove_image_markers(self, text: str) -> str:
        if not text:
            return text

        def repl(match: re.Match) -> str:
            token = (match.group(1) or "").strip()
            if self._is_image_token(token):
                return " "
            return match.group(0)

        out = BRACKET_TAG_RE.sub(repl, text)
        out = re.sub(r"\s+", " ", out).strip()
        return out

    def _inject_metadata_tags(
        self,
        text: str,
        *,
        curso: Optional[str] = None,
        tema: Optional[str] = None,
        subtema: Optional[str] = None,
    ) -> str:
        item = (text or "").strip()
        if not item:
            return item
        curso_val = self._sanitize_tag_value((curso if curso is not None else self.curso_var.get() or "").strip())
        tema_val = self._sanitize_tag_value((tema if tema is not None else self.tema_var.get() or "").strip())
        subtema_val = self._sanitize_tag_value((subtema if subtema is not None else self.subtema_var.get() or "").strip())

        cleaned = TAG_CURSO_RE.sub(" ", item)
        cleaned = TAG_TEMA_RE.sub(" ", cleaned)
        cleaned = TAG_SUBTEMA_RE.sub(" ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()

        tags: List[str] = []
        if curso_val:
            tags.append(f"[[curso={curso_val}]]")
        if tema_val:
            tags.append(f"[[tema={tema_val}]]")
        if subtema_val:
            tags.append(f"[[subtema={subtema_val}]]")
        if not tags:
            return cleaned

        tag_text = " ".join(tags)
        header_match = ITEM_HEADER_RE.search(cleaned)
        if not header_match:
            return f"{tag_text} {cleaned}".strip()
        header = header_match.group(1)
        rest = cleaned[header_match.end() :].lstrip()
        if rest:
            return f"{header} {tag_text} {rest}".strip()
        return f"{header} {tag_text}".strip()

    def _split_items_from_text(self, text: str, *, path: Path, idx: int) -> List[str]:
        base = self.controller.normalizar_item_una_linea(text)
        base = re.sub(r"(?i)\bENDLEADING\b", " ", base)
        base = re.sub(r"\s+", " ", base).strip()
        if not base:
            return []
        structured_markers = list(STRUCTURED_ITEM_HEADER_RE.finditer(base))
        if structured_markers:
            chunks: List[str] = []
            for pos, match in enumerate(structured_markers):
                start = match.start()
                end = structured_markers[pos + 1].start() if pos + 1 < len(structured_markers) else len(base)
                chunk = (base[start:end] or "").strip(" ;")
                chunk = re.sub(r"(?i)\bENDITEM\b", " ", chunk)
                chunk = re.sub(r"\s+", " ", chunk).strip()
                if chunk:
                    chunks.append(chunk)
            if chunks:
                return chunks
        first_header = ITEM_HEADER_RE.search(base)
        if first_header:
            prefix = (base[: first_header.start()] or "").strip()
            tail = (base[first_header.start() :] or "").strip()
            items = [m.group(1).strip() for m in ITEM_BLOCK_RE.finditer(tail)]
            if items:
                if prefix:
                    return [f"{ORPHAN_OPTIONS_PREFIX} {prefix}".strip()] + items
                return items
        if not base.startswith("\\item"):
            # Fallback: some OCR/model outputs contain several "Problema N..." blocks
            # without explicit \item headers. Split them to avoid dropping items.
            problem_markers = list(
                re.finditer(r"(?i)\\bproblema\\s*(?:n[°ºo]\\s*)?\\d+\\b", base)
            )
            if len(problem_markers) >= 2:
                chunks: List[str] = []
                for pos, match in enumerate(problem_markers):
                    start = match.start()
                    end = problem_markers[pos + 1].start() if pos + 1 < len(problem_markers) else len(base)
                    chunk = (base[start:end] or "").strip(" ;")
                    if chunk:
                        chunks.append(self._item_from_plain_ocr(chunk, path, idx + pos))
                if chunks:
                    return chunks
            return [self._item_from_plain_ocr(base, path, idx)]
        items = [m.group(1).strip() for m in ITEM_BLOCK_RE.finditer(base)]
        if items:
            return items
        return [base]

    def _extract_leading_options_payload(self, text: str) -> tuple[Dict[str, str], str]:
        """
        Detect orphan options at the beginning of an image output, before the
        first item/header of the current image. Returns (options, stripped_text).
        """
        base = self.controller.normalizar_item_una_linea(self._decode_scan_escapes(text or ""))
        if not base:
            return ({}, "")

        first_item = ITEM_HEADER_RE.search(base)
        first_problem = re.search(r"\bproblema\b", base, re.IGNORECASE)
        first_structured = STRUCTURED_ITEM_HEADER_RE.search(base)
        cut: Optional[int] = None
        if first_item:
            cut = first_item.start()
        if first_problem:
            cut = min(cut, first_problem.start()) if cut is not None else first_problem.start()
        if first_structured:
            cut = min(cut, first_structured.start()) if cut is not None else first_structured.start()
        if cut is None or cut <= 0:
            # Case: image only contains remaining options from previous problem.
            enu2, opts2 = self._extract_options_loose(base)
            if len(opts2) >= 2:
                if not enu2 or len(enu2) <= 28:
                    return (opts2, "")
            return ({}, base)

        prefix = (base[:cut] or "").strip()
        rest = (base[cut:] or "").strip()
        if not prefix or not rest:
            return ({}, base)

        enunciado, options = self._extract_options_loose(prefix)
        if len(options) < 2:
            return ({}, base)
        # If prefix has long prose, it's likely not a pure orphan-options block.
        if enunciado and len(enunciado) > 28:
            return ({}, base)
        return (options, rest)

    def _collapse_noisy_separators(self, text: str) -> str:
        if not text:
            return text
        out = text
        # Runs like ££££... from OCR noise become a single separator.
        out = re.sub(rf"{re.escape(SEP_LINE)}{{3,}}", SEP_LINE, out)
        out = re.sub(rf"{re.escape(SEP_OPT)}{{4,}}", f"{SEP_OPT}{SEP_OPT}", out)
        # Remove malformed low unicode fragments often produced by OCR/encoding.
        out = out.replace("\\u0", " ")
        out = re.sub(r"\s+", " ", out).strip()
        return out

    def _standardize_item_options(self, item: str, *, fallback_numero: int) -> str:
        txt = (item or "").strip()
        header_match = ITEM_HEADER_RE.search(txt)
        if not header_match:
            return txt
        header = header_match.group(1)
        marker_name = self._extract_first_image_marker_name(txt)
        ex_curso, ex_tema, ex_subtema = self._extract_existing_tags(txt)
        body = self._body_without_tags_and_markers(txt)
        enunciado, options = self._extract_options(body)
        enunciado = enunciado.replace(SEP_LINE, " ").replace(SEP_OPT, " ")
        enunciado = re.sub(r"\s+", " ", enunciado).strip()
        option_markers = re.findall(r"(?<![A-Za-z0-9])([A-Ea-e])\s*[\)\].:]", body)

        def with_marker(base_item: str) -> str:
            out = (base_item or "").strip()
            if marker_name and not self._has_image_marker(out):
                out = f"{out} [[Imagen={marker_name}]]".strip()
                out = self._move_image_marker_before_options(out)
            return out

        if option_markers and len(set(x.upper() for x in option_markers)) < 3:
            # Likely bleed from previous/next problem; keep only enunciado.
            if enunciado:
                stripped = f"{header} {enunciado}".strip()
                return with_marker(
                    self._inject_metadata_tags(stripped, curso=ex_curso, tema=ex_tema, subtema=ex_subtema)
                )
            return with_marker(
                self._inject_metadata_tags(header, curso=ex_curso, tema=ex_tema, subtema=ex_subtema)
            )
        if len(options) < 2:
            return with_marker(txt)
        numero = self.controller.parsear_numero_original(header) or fallback_numero
        rebuilt = self._build_scan_item_strict(numero=numero, enunciado=enunciado, options=options)
        return with_marker(
            self._inject_metadata_tags(rebuilt, curso=ex_curso, tema=ex_tema, subtema=ex_subtema)
        )

    def _is_orphan_options_item(self, item: str) -> bool:
        txt = (item or "").strip()
        if not txt.startswith("\\item"):
            return False
        m = ITEM_HEADER_RE.search(txt)
        body = txt[m.end():].strip() if m else txt
        body = TAG_CURSO_RE.sub(" ", body)
        body = TAG_TEMA_RE.sub(" ", body)
        body = TAG_SUBTEMA_RE.sub(" ", body)
        body = self._remove_image_markers(body)
        body = body.lstrip(f"{SEP_LINE}{SEP_OPT} ").strip()
        if not body:
            return True
        starts_with_option = re.match(r"^[A-Ea-e]\s*[\)\].:]", body) is not None
        option_markers = re.findall(r"(?<![A-Za-z0-9])([A-Ea-e])\s*[\)\].:]", body)
        no_prompt_words = re.search(r"\b(calcule|halle|determine|si|desde|en\s+el|en\s+la)\b", body, re.IGNORECASE) is None
        if starts_with_option and len(option_markers) >= 2 and no_prompt_words:
            return True
        if "[[ocr_sin_texto]]" in body and len(option_markers) >= 2:
            return True
        return False

    def _finalize_item_format(self, raw_item: str, *, path: Path, fallback_numero: int) -> str:
        item = self.controller.normalizar_item_una_linea(raw_item)
        item = self._decode_scan_escapes(item)
        item = self._collapse_noisy_separators(item)
        item = self._normalize_math_delimiters(item)
        item = self._normalize_item_header(item, fallback_numero=fallback_numero)
        item = self._standardize_item_options(item, fallback_numero=fallback_numero)
        numero = self.controller.parsear_numero_original(item) or fallback_numero
        item = self._normalize_image_markers(item, path=path, fallback_numero=numero)
        item = self._move_image_marker_before_options(item)
        item = self.controller.normalizar_item_una_linea(item)
        return item

    def _finalize_item_prompt_first(self, raw_item: str, *, path: Path, fallback_numero: int) -> str:
        """
        Light post-processing for LLM-formatted output:
        keep model output structure with minimal cleanup.
        """
        item = self.controller.normalizar_item_una_linea(raw_item)
        item = self._decode_scan_escapes(item)
        item = self._collapse_noisy_separators(item)
        item = self.controller.normalizar_item_una_linea(item)
        return item

    def _resolve_metadata_for_mode(
        self,
        *,
        mode: str,
        existing: tuple[str, str, str],
        manual: tuple[str, str, str],
        auto: tuple[str, str, str],
    ) -> tuple[str, str, str]:
        ex_c, ex_t, ex_s = existing
        man_c, man_t, man_s = manual
        aut_c, aut_t, aut_s = auto

        if mode == TAG_MODE_MANUAL:
            return (
                man_c or ex_c,
                man_t or ex_t,
                man_s or ex_s,
            )
        if mode == TAG_MODE_AUTO:
            return (
                aut_c or ex_c,
                aut_t or ex_t,
                aut_s or ex_s,
            )
        # Mixed: manual has priority, then auto, then existing.
        return (
            man_c or aut_c or ex_c,
            man_t or aut_t or ex_t,
            man_s or aut_s or ex_s,
        )

    def _is_geometry_course(self, curso: str) -> bool:
        norm = self._norm_key(curso or "")
        return "geometr" in norm

    def _looks_geometry_content(self, text: str) -> bool:
        raw = text or ""
        norm = self._norm_key(raw)
        if not norm:
            return False
        keywords = (
            "triangulo",
            "angulo",
            "grafico",
            "figura",
            "ceviana",
            "bisectriz",
            "mediana",
            "poligono",
            "congru",
            "semej",
        )
        if any(k in norm for k in keywords):
            return True
        if "∠" in raw or "\\angle" in raw:
            return True
        if re.search(r"\b[A-Z]{2,3}\s*=\s*[A-Z0-9]", raw):
            return True
        return False

    def _replace_unicode_greek(self, text: str) -> str:
        out = text or ""
        for src, dst in UNICODE_GREEK_TO_LATEX.items():
            out = out.replace(src, dst)
        return out

    def _normalize_trig_function_names(self, text: str) -> str:
        out = text or ""
        # Accept compact forms like ctg\alpha or tgx (OCR often removes spaces).
        out = re.sub(r"\bctg(?=\s*(?:\\[A-Za-z]+|[A-Za-z]))", r"\\cot", out, flags=re.IGNORECASE)
        out = re.sub(r"\btg(?=\s*(?:\\[A-Za-z]+|[A-Za-z]))", r"\\tan", out, flags=re.IGNORECASE)
        out = re.sub(r"\bsen(?=\s*(?:\\[A-Za-z]+|[A-Za-z]))", r"\\sin", out, flags=re.IGNORECASE)
        out = re.sub(r"\bctg\b", r"\\cot", out, flags=re.IGNORECASE)
        out = re.sub(r"\btg\b", r"\\tan", out, flags=re.IGNORECASE)
        out = re.sub(r"\bsen\b", r"\\sin", out, flags=re.IGNORECASE)
        return out

    def _apply_global_math_conventions(self, item: str) -> str:
        text = (item or "").strip()
        if not text:
            return text

        text = self._replace_unicode_greek(text)
        text = self._normalize_trig_function_names(text)

        math_re = re.compile(r"(\$\$.*?\$\$|(?<!\$)\$(?!\$).*?(?<!\$)\$(?!\$))", re.DOTALL)
        greek_alt = "|".join(GREEK_LATEX_NAMES)
        trig_arg_re = re.compile(
            rf"(\\(?:cot|tan|sin|cos|sec|csc)\s*(?:\\(?:{greek_alt})|[A-Za-z]))",
            re.IGNORECASE,
        )
        greek_cmd_re = re.compile(rf"(\\(?:{greek_alt}))(?![A-Za-z])")
        eq_re = re.compile(r"([A-Z])\s*=\s*([^£æ$]+)")

        def repl_prompt_target(match: re.Match) -> str:
            cmd = (match.group(1) or "").strip()
            target = (match.group(2) or "").strip()
            if re.fullmatch(r"[A-Za-z]", target):
                return f"{cmd} ${target}$"
            if re.fullmatch(r"[A-Z]{2,3}", target):
                return f"{cmd} ${target}$"
            return match.group(0)

        parts = math_re.split(text)
        for i, part in enumerate(parts):
            if i % 2 == 1:
                continue
            p = part
            p = re.sub(rf'"\s*(\\(?:{greek_alt}))\s*"', r"$\1$", p)

            def repl_eq(match: re.Match) -> str:
                left = (match.group(1) or "").strip()
                right = (match.group(2) or "").strip()
                if re.search(r"\\(?:cot|tan|sin|cos|sec|csc)", right, re.IGNORECASE):
                    return f"${left} = {right}$"
                return match.group(0)

            p = eq_re.sub(repl_eq, p)

            # Re-split to avoid touching math added by repl_eq.
            subparts = math_re.split(p)
            for j, sub in enumerate(subparts):
                if j % 2 == 1:
                    continue
                s = sub
                s = re.sub(
                    r"\b(calcule|halle|determine)\s+m\s*(?:\\angle|∠)\s*([A-Z]{3})\b",
                    lambda m: f"{(m.group(1) or '').strip()} $m\\angle {(m.group(2) or '').strip()}$",
                    s,
                    flags=re.IGNORECASE,
                )
                s = trig_arg_re.sub(lambda m: f"${m.group(1)}$", s)
                s = greek_cmd_re.sub(r"$\1$", s)
                s = re.sub(
                    r"\b(calcule|halle|determine)\s+([A-Za-z]{1,3})(?!\s*(?:\\angle|∠))\b",
                    repl_prompt_target,
                    s,
                    flags=re.IGNORECASE,
                )
                s = re.sub(
                    r"(?<![\$\\])(\d+(?:[.,]\d+)?)\s*(?:°|\^\s*\\?circ)\b",
                    lambda m: f"${self._normalize_degree_notation(m.group(0))}$",
                    s,
                )
                s = re.sub(r"\b([A-Z]{2,3})\s*=\s*([A-Z]{2,3})\b", r"$\1 = \2$", s)
                s = re.sub(
                    r"\b([A-Z]{1,3})\s*=\s*(\d+(?:[.,]\d+)?(?:\s*\([A-Z]{1,3}\))?)\b",
                    r"$\1 = \2$",
                    s,
                )
                subparts[j] = s
            parts[i] = "".join(subparts)

        text = "".join(parts)
        text = self.controller.normalizar_item_una_linea(text)
        normalized = normalize_latex_scan_item_text(text)
        return normalized.text or text

    def _apply_geometry_conventions(self, item: str) -> str:
        text = (item or "").strip()
        if not text:
            return text

        tag_curso, _tag_tema, _tag_subtema = self._extract_existing_tags(text)
        curso = tag_curso or (self.curso_var.get() or "")
        if not (self._is_geometry_course(curso) or self._looks_geometry_content(text)):
            return text

        text = self._replace_unicode_greek(text)
        text = self._normalize_trig_function_names(text)

        math_re = re.compile(r"(\$\$.*?\$\$|(?<!\$)\$(?!\$).*?(?<!\$)\$(?!\$))", re.DOTALL)
        split_pos = text.find(f"{SEP_LINE}A)")
        if split_pos >= 0:
            enunciado = text[:split_pos]
            opciones = text[split_pos:]
        else:
            enunciado = text
            opciones = ""

        parts = math_re.split(enunciado)
        greek_alt = "|".join(GREEK_LATEX_NAMES)
        trig_pattern = re.compile(
            rf"\b(tg|tan|sen|sin|cos|cot|sec|csc)\s*(\\(?:{greek_alt}))\b",
            re.IGNORECASE,
        )
        greek_cmd_pattern = re.compile(rf"(\\(?:{greek_alt}))(?![A-Za-z])")
        def repl_prompt_target_geo(match: re.Match) -> str:
            cmd = (match.group(1) or "").strip()
            target = (match.group(2) or "").strip()
            if re.fullmatch(r"[A-Za-z]", target):
                return f"{cmd} ${target}$"
            if re.fullmatch(r"[A-Z]{2,3}", target):
                return f"{cmd} ${target}$"
            return match.group(0)

        for i, part in enumerate(parts):
            if i % 2 == 1:
                # Already inside math delimiters.
                continue
            p = part
            p = re.sub(rf'"\s*(\\(?:{greek_alt}))\s*"', r'$\1$', p)
            p = re.sub(r'"([A-Z])"', r'$\1$', p)
            p = re.sub(r"\b([Tt]ri[áa]ngulo)\s+([A-Z]{3})\b", r"\1 $\2$", p)
            p = re.sub(r"\b([A-Z])\s*(?:\\in|∈)\s*([A-Z]{2,3})\b", r"$\1 \\in \2$", p)
            p = re.sub(r"\$([0-9]+(?:[.,][0-9]+)?)\$\s*(cm|mm|km|dm|m|u|kg|g|mg|rad|h|min|s)\b", r"$\1\2$", p, flags=re.IGNORECASE)
            p = re.sub(r"\$([A-Z]{2,3}\s*=\s*[A-Z]{2,3})\$\s*=\s*([A-Z]{2,3})\b", r"$\1 = \2$", p)

            # Angle relations: m∠ABC = ...  -> $m\angle ABC = ...$
            def repl_angle_rel(match: re.Match) -> str:
                lhs = (match.group(1) or "").strip()
                rhs = (match.group(2) or "").strip()
                rhs = rhs.replace("∠", r"\angle ")
                rhs = re.sub(r"m\s*\\angle\s*([A-Z]{3})", r"m\\angle \1", rhs)
                rhs = self._normalize_degree_notation(rhs)
                return f"$m\\angle {lhs} = {rhs}$"

            p = re.sub(
                r"\bm\s*(?:∠|\\angle)\s*([A-Z]{1,3})\s*=\s*([^£æ\.,;]+)",
                repl_angle_rel,
                p,
                flags=re.IGNORECASE,
            )

            # Standalone angle measures.
            p = re.sub(r"\bm\s*(?:∠|\\angle)\s*([A-Z]{1,3})\b", r"$m\\angle \1$", p, flags=re.IGNORECASE)

            # Segment/length equalities.
            def repl_seg_eq(match: re.Match) -> str:
                left = (match.group(1) or "").strip()
                right = (match.group(2) or "").strip()
                right = self._normalize_degree_notation(right)
                return f"${left} = {right}$"

            p = re.sub(
                r"\b([A-Z]{2,3})\s*=\s*([A-Z0-9\(\)\+\-\*/]+(?:\s*[A-Z0-9\(\)\+\-\*/]+)*)",
                repl_seg_eq,
                p,
            )

            # Common geometry tokens outside math.
            p = re.sub(r"\b(ceviana(?:\s+interior)?|lado|segmento|bisectriz|mediana|altura)\s+([A-Z]{2,3})\b", r"\1 $\2$", p, flags=re.IGNORECASE)
            p = re.sub(
                r"(?<![\$\\])(\d+(?:[.,]\d+)?)\s*(?:°|\^\s*\\?circ)\b",
                lambda m: f"${self._normalize_degree_notation(m.group(0))}$",
                p,
            )
            p = re.sub(
                r"\b(calcule|halle|determine)\s+m\s*(?:\\angle|∠)\s*([A-Z]{3})\b",
                lambda m: f"{(m.group(1) or '').strip()} $m\\angle {(m.group(2) or '').strip()}$",
                p,
                flags=re.IGNORECASE,
            )
            p = re.sub(
                r"\b(calcule|halle|determine)\s+([A-Za-z]{1,3})(?!\s*(?:\\angle|∠))\b",
                repl_prompt_target_geo,
                p,
                flags=re.IGNORECASE,
            )
            p = trig_pattern.sub(lambda m: f"${m.group(1)}{m.group(2)}$", p)
            p = greek_cmd_pattern.sub(r'$\1$', p)
            parts[i] = p

        text = "".join(parts) + opciones
        text = self._normalize_math_delimiters(text)
        text = self.controller.normalizar_item_una_linea(text)
        return text

    def _reasoning_target_token(self, value: str) -> str:
        token = self.controller.normalizar_item_una_linea(str(value or ""))
        if not token:
            return ""
        if token.startswith("$") and token.endswith("$") and len(token) >= 2:
            token = token[1:-1].strip()
        token = token.strip()
        return token

    def _wrap_target_outside_math(self, text: str, target: str) -> str:
        base = str(text or "")
        token = self._reasoning_target_token(target)
        if not base or not token:
            return base

        pattern = re.escape(token).replace(r"\ ", r"\s+")
        if re.fullmatch(r"[A-Za-z0-9_\\]+", token):
            regex = re.compile(rf"(?<![A-Za-z0-9_\\])({pattern})(?![A-Za-z0-9_])")
        else:
            regex = re.compile(rf"({pattern})")
        math_re = re.compile(r"(\$\$.*?\$\$|(?<!\$)\$(?!\$).*?(?<!\$)\$(?!\$))", re.DOTALL)
        parts = math_re.split(base)
        for i, chunk in enumerate(parts):
            if i % 2 == 1:
                continue

            def repl(match: re.Match) -> str:
                value = self.controller.normalizar_item_una_linea((match.group(1) or "").strip())
                if not value:
                    return match.group(0)
                return f"${value}$"

            parts[i] = regex.sub(repl, chunk)
        return "".join(parts)

    def _apply_reasoning_wrap_hints(self, item: str, reasoning_payload: Optional[Dict[str, Any]]) -> str:
        text = str(item or "")
        if not text or not isinstance(reasoning_payload, dict):
            return text
        targets: List[str] = []
        seen: set[str] = set()
        for key in ("expresiones_sin_dolares", "elementos_geometricos"):
            for raw in self._extract_json_list_field(reasoning_payload, key):
                token = self._reasoning_target_token(raw)
                if not token:
                    continue
                norm = self._norm_key(token)
                if norm and norm not in seen:
                    seen.add(norm)
                    targets.append(token)
        if not targets:
            return text
        targets.sort(key=len, reverse=True)
        out = text
        for token in targets:
            out = self._wrap_target_outside_math(out, token)
        out = self._normalize_math_delimiters(out)
        return self.controller.normalizar_item_una_linea(out)

    def _extract_first_image_marker_name(self, item: str) -> str:
        for match in BRACKET_TAG_RE.finditer(item or ""):
            token = (match.group(1) or "").strip()
            if not token:
                continue
            if "=" in token:
                key, value = token.split("=", 1)
                if key.strip().lower() == "imagen":
                    value = (value or "").strip()
                    parsed = MARKER_VALUE_RE.match(value)
                    if parsed:
                        base = (parsed.group("base") or "").strip()
                        num = (parsed.group("num") or "").strip()
                        opt = (parsed.group("opt") or "").strip()
                        if base and num:
                            return f"{base}-{num}{('-' + opt) if opt else ''}"
                    return value
            else:
                parsed = MARKER_VALUE_RE.match(token)
                if parsed:
                    base = (parsed.group("base") or "").strip()
                    num = (parsed.group("num") or "").strip()
                    opt = (parsed.group("opt") or "").strip()
                    if base and num:
                        return f"{base}-{num}{('-' + opt) if opt else ''}"
        return ""

    def _extract_figure_detection_json(self, text: str) -> Tuple[Optional[Tuple[float, float, float, float]], float, bool]:
        raw = (text or "").strip()
        if not raw:
            return (None, 0.0, False)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        payload = match.group(0) if match else raw
        try:
            data = json.loads(payload)
        except Exception:
            return (None, 0.0, False)
        if not isinstance(data, dict):
            return (None, 0.0, False)

        has_conf_key = "confidence" in data
        conf_raw = data.get("confidence", 0.0)
        try:
            confidence = float(conf_raw)
        except Exception:
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        has_raw = data.get("has_figure", None)
        if isinstance(has_raw, bool):
            has_figure = has_raw
        elif isinstance(has_raw, str):
            has_figure = has_raw.strip().lower() in {"1", "true", "si", "sí", "yes"}
        else:
            has_figure = False

        # If model declares figure but omits confidence, trust the flag by default.
        if has_figure and (not has_conf_key or confidence <= 0.0):
            confidence = 1.0

        bbox: Optional[Tuple[float, float, float, float]] = None
        if all(k in data for k in ("x1", "y1", "x2", "y2")):
            try:
                x1 = float(data.get("x1"))
                y1 = float(data.get("y1"))
                x2 = float(data.get("x2"))
                y2 = float(data.get("y2"))
                vals = [x1, y1, x2, y2]
                if not any(v < 0.0 or v > 1.0 for v in vals) and x2 > x1 and y2 > y1:
                    bbox = (x1, y1, x2, y2)
            except Exception:
                bbox = None

        # Backward compatibility: old payload with only bbox means figure exists.
        if bbox is not None and not has_figure:
            has_figure = True
            if confidence <= 0.0:
                confidence = 1.0

        min_conf = self._resolve_figure_min_conf()
        if has_figure and confidence < min_conf:
            has_figure = False
            bbox = None

        return (bbox, confidence, has_figure)

    def _detect_figure_bbox_openai(
        self,
        client: OpenAI,
        *,
        model: str,
        timeout_s: int,
        retries: int,
        label: str,
        path: Path,
    ) -> Tuple[Optional[Tuple[float, float, float, float]], float, bool]:
        img_url = self._encode_image_data_url(path)
        prompt = (
            "Devuelve SOLO JSON valido para detectar figura matematica.\n"
            'Formato: {"has_figure":true|false,"confidence":0.0-1.0,"x1":0.0,"y1":0.0,"x2":1.0,"y2":1.0}\n'
            "Si no hay figura, devuelve: {\"has_figure\":false,\"confidence\":0.0}\n"
            "Si hay figura pero no puedes delimitar bbox exacto, devuelve has_figure=true con confidence y sin bbox.\n"
            "No incluyas texto extra."
        )
        text = ""
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                kwargs = {
                    "model": model,
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": prompt},
                                {"type": "input_image", "image_url": img_url},
                            ],
                        }
                    ],
                }
                try:
                    resp = client.responses.create(**kwargs, timeout=timeout_s)
                except TypeError:
                    resp = client.responses.create(**kwargs)
                text = (resp.output_text or "").strip()
                self._log_usage(
                    provider=PROVIDER_OPENAI,
                    model=model,
                    label=f"{label} [bbox-pass]",
                    usage=getattr(resp, "usage", None),
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                msg = str(exc).lower()
                is_retryable = ("timeout" in msg) or ("timed out" in msg) or ("rate" in msg) or ("429" in msg)
                if attempt < retries and is_retryable:
                    time.sleep(min(8, 2 ** attempt))
                    continue
                break
        if last_exc is not None:
            return (None, 0.0, False)
        return self._extract_figure_detection_json(text)

    def _detect_figure_bbox_hf(
        self,
        *,
        model: str,
        timeout_s: int,
        retries: int,
        label: str,
        path: Path,
    ) -> Tuple[Optional[Tuple[float, float, float, float]], float, bool]:
        token = self._resolve_hf_token()
        if not token:
            return (None, 0.0, False)
        base_url = self._resolve_hf_base_url()
        client = OpenAI(base_url=base_url, api_key=token, timeout=timeout_s)
        img_url = self._encode_image_data_url(path)
        prompt = (
            "Devuelve SOLO JSON valido para detectar figura matematica.\n"
            'Formato: {"has_figure":true|false,"confidence":0.0-1.0,"x1":0.0,"y1":0.0,"x2":1.0,"y2":1.0}\n'
            "Si no hay figura, devuelve: {\"has_figure\":false,\"confidence\":0.0}\n"
            "Si hay figura pero no puedes delimitar bbox exacto, devuelve has_figure=true con confidence y sin bbox.\n"
            "No incluyas texto extra."
        )
        text = ""
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": img_url}}]}],
                    temperature=0,
                    max_tokens=120,
                )
                content = resp.choices[0].message.content if resp and resp.choices else ""
                text = self._extract_chat_text(content)
                self._log_usage(
                    provider=PROVIDER_HF,
                    model=model,
                    label=f"{label} [bbox-pass]",
                    usage=getattr(resp, "usage", None),
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                msg = str(exc).lower()
                is_retryable = ("timeout" in msg) or ("timed out" in msg) or ("rate" in msg) or ("429" in msg)
                if attempt < retries and is_retryable:
                    time.sleep(min(8, 2 ** attempt))
                    continue
                break
        if last_exc is not None:
            return (None, 0.0, False)
        return self._extract_figure_detection_json(text)

    def _save_figure_crop(
        self,
        *,
        image_path: Path,
        marker_name: str,
        bbox_norm: Optional[Tuple[float, float, float, float]],
    ) -> Optional[str]:
        try:
            from PIL import Image  # type: ignore
        except Exception:
            self.after(0, lambda: self._log("Recorte auto omitido: falta Pillow."))
            return None

        out_dir = self._tmp_crop_root
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^\w\-. ]", "_", marker_name or image_path.stem).strip()
        if not safe_name:
            safe_name = image_path.stem
        out_path = out_dir / f"{safe_name}.png"
        if out_path.exists():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            out_path = out_dir / f"{safe_name}_{ts}.png"
        try:
            im = Image.open(image_path)
            w, h = im.size
            if w <= 1 or h <= 1:
                return None
            if bbox_norm is None:
                cropped = im.copy()
            else:
                x1, y1, x2, y2 = bbox_norm
                left = max(0, min(w - 1, int(round(x1 * w))))
                top = max(0, min(h - 1, int(round(y1 * h))))
                right = max(left + 1, min(w, int(round(x2 * w))))
                bottom = max(top + 1, min(h, int(round(y2 * h))))
                cropped = im.crop((left, top, right, bottom))
            cropped.save(out_path, format="PNG")
            return str(out_path)
        except Exception:
            return None

    def _save_detection_overlay(
        self,
        *,
        image_path: Path,
        bbox_norm: Optional[Tuple[float, float, float, float]],
        has_figure: bool,
        confidence: float,
        label: str,
    ) -> Optional[str]:
        try:
            from PIL import Image, ImageDraw  # type: ignore
        except Exception:
            return None
        try:
            im = Image.open(image_path).convert("RGB")
            draw = ImageDraw.Draw(im)
            w, h = im.size
            if bbox_norm is not None:
                x1, y1, x2, y2 = bbox_norm
                left = max(0, min(w - 1, int(round(x1 * w))))
                top = max(0, min(h - 1, int(round(y1 * h))))
                right = max(left + 1, min(w, int(round(x2 * w))))
                bottom = max(top + 1, min(h, int(round(y2 * h))))
                color = (32, 180, 80) if has_figure else (230, 80, 80)
                draw.rectangle((left, top, right, bottom), outline=color, width=4)
            else:
                # No bbox: draw an outer frame to indicate detector output only.
                color = (255, 170, 0) if has_figure else (230, 80, 80)
                draw.rectangle((2, 2, max(3, w - 3), max(3, h - 3)), outline=color, width=3)
            status = "YES" if has_figure else "NO"
            draw.text((8, 8), f"{status} conf={confidence:.2f}", fill=(0, 0, 0))
            out_dir = self._runs_dir / "debug_detect"
            out_dir.mkdir(parents=True, exist_ok=True)
            safe_label = re.sub(r"[^\w\-. ]", "_", label).strip() or "detect"
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            out_path = out_dir / f"{safe_label}_{ts}.png"
            im.save(out_path, format="PNG")
            return str(out_path)
        except Exception:
            return None

    def _materialize_temp_crop(self, path_str: str) -> str:
        src = Path((path_str or "").strip())
        if not src.exists():
            return path_str
        try:
            src_res = src.resolve()
            tmp_res = self._tmp_crop_root.resolve()
        except Exception:
            src_res = src
            tmp_res = self._tmp_crop_root
        if not str(src_res).lower().startswith(str(tmp_res).lower()):
            return str(src)

        final_dir = Path(self._final_crop_dir)
        final_dir.mkdir(parents=True, exist_ok=True)
        target = final_dir / src.name
        if target.exists():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            target = final_dir / f"{src.stem}_{ts}{src.suffix}"
        try:
            src.replace(target)
            return str(target)
        except Exception:
            return str(src)

    def _sanitize_storage_base_name(self, source_name: str) -> str:
        raw = (source_name or "").strip()
        if not raw:
            return "archivo"
        safe = re.sub(r"[^\w\-]+", "_", raw, flags=re.UNICODE).strip("_")
        return safe or "archivo"

    def _rewrite_placeholder_markers_for_storage(self, item_text: str, source_name: str) -> str:
        txt = (item_text or "").strip()
        if not txt:
            return txt
        base = self._sanitize_storage_base_name(source_name)

        def repl(match: re.Match) -> str:
            num = (match.group("num") or "").strip()
            opt = (match.group("opt") or "").strip()
            suffix = f"-{opt}" if opt else ""
            return f"[[Imagen={base}-{num}{suffix}]]"

        return PLACEHOLDER_IMAGE_MARKER_RE.sub(repl, txt)

    def _detect_figure_openai(
        self,
        client: OpenAI,
        *,
        model: str,
        timeout_s: int,
        retries: int,
        label: str,
        path: Path,
    ) -> bool:
        img_url = self._encode_image_data_url(path)
        prompt = (
            "Responde solo SI o NO. "
            "Hay una figura, diagrama, grafico o dibujo necesario para resolver el problema?"
        )
        text = ""
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                kwargs = {
                    "model": model,
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": prompt},
                                {"type": "input_image", "image_url": img_url},
                            ],
                        }
                    ],
                    "max_output_tokens": 3,
                }
                try:
                    resp = client.responses.create(**kwargs, timeout=timeout_s)
                except TypeError:
                    kwargs.pop("max_output_tokens", None)
                    resp = client.responses.create(**kwargs)
                text = (resp.output_text or "").strip()
                self._log_usage(
                    provider=PROVIDER_OPENAI,
                    model=model,
                    label=f"{label} [figure-check]",
                    usage=getattr(resp, "usage", None),
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                msg = str(exc).lower()
                is_retryable = ("timeout" in msg) or ("timed out" in msg) or ("rate" in msg) or ("429" in msg)
                if attempt < retries and is_retryable:
                    time.sleep(min(8, 2 ** attempt))
                    continue
                break
        if last_exc is not None:
            return False
        ans = (text or "").strip().upper()
        return ans.startswith("SI") or ans.startswith("S")

    def _detect_figure_hf(
        self,
        *,
        model: str,
        timeout_s: int,
        retries: int,
        label: str,
        path: Path,
    ) -> bool:
        token = self._resolve_hf_token()
        if not token:
            return False
        base_url = self._resolve_hf_base_url()
        client = OpenAI(base_url=base_url, api_key=token, timeout=timeout_s)
        img_url = self._encode_image_data_url(path)
        prompt = (
            "Responde solo SI o NO. "
            "Hay una figura, diagrama, grafico o dibujo necesario para resolver el problema?"
        )

        text = ""
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": img_url}},
                            ],
                        }
                    ],
                    temperature=0,
                    max_tokens=3,
                )
                content = resp.choices[0].message.content if resp and resp.choices else ""
                text = self._extract_chat_text(content)
                self._log_usage(
                    provider=PROVIDER_HF,
                    model=model,
                    label=f"{label} [figure-check]",
                    usage=getattr(resp, "usage", None),
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                msg = str(exc).lower()
                is_retryable = ("timeout" in msg) or ("timed out" in msg) or ("rate" in msg) or ("429" in msg)
                if attempt < retries and is_retryable:
                    time.sleep(min(8, 2 ** attempt))
                    continue
                break
        if last_exc is not None:
            return False
        ans = (text or "").strip().upper()
        return ans.startswith("SI") or ans.startswith("S")

    def _debug_detect_selected(self) -> None:
        sel = list(self.list_files.curselection())
        if not sel:
            messagebox.showwarning("Detector", "Selecciona una imagen en la lista.")
            return
        label = self.list_files.get(sel[0])
        path = self._file_map.get(label)
        if not path or not path.exists():
            messagebox.showwarning("Detector", "No se encontro la imagen seleccionada.")
            return

        provider = self._resolve_effective_provider()
        model = (self.model_var.get() or "").strip() or DEFAULT_HF_VISION_MODEL
        detect_model = self._resolve_detect_model(provider, model)
        try:
            timeout_s = int(self.timeout_var.get())
        except Exception:
            timeout_s = 180
        timeout_s = max(30, min(timeout_s, 600))
        try:
            retries = int(self.retries_var.get())
        except Exception:
            retries = 2
        retries = max(0, min(retries, 5))

        self._log(f"[debug-detector] {label} provider={provider} model={detect_model}")

        def worker() -> None:
            try:
                bbox: Optional[Tuple[float, float, float, float]] = None
                conf = 0.0
                has_fig = False
                if provider == PROVIDER_OPENAI:
                    try:
                        cli = OpenAI(timeout=timeout_s)
                    except TypeError:
                        cli = OpenAI()
                    bbox, conf, has_fig = self._detect_figure_bbox_openai(
                        cli,
                        model=detect_model,
                        timeout_s=timeout_s,
                        retries=retries,
                        label=f"{label}#debug",
                        path=path,
                    )
                elif provider == PROVIDER_HF:
                    bbox, conf, has_fig = self._detect_figure_bbox_hf(
                        model=detect_model,
                        timeout_s=timeout_s,
                        retries=retries,
                        label=f"{label}#debug",
                        path=path,
                    )
                else:
                    self.after(0, lambda: self._log("Debug detector requiere proveedor IA (OpenAI/HuggingFace)."))
                    return

                overlay = self._save_detection_overlay(
                    image_path=path,
                    bbox_norm=bbox,
                    has_figure=has_fig,
                    confidence=conf,
                    label=label,
                )
                self.after(
                    0,
                    lambda: self._log(
                        f"[debug-detector] has_figure={has_fig} conf={conf:.2f} bbox={bbox if bbox else '{}'}"
                    ),
                )
                if overlay:
                    self.after(0, lambda p=overlay: self._log(f"[debug-detector] overlay: {p}"))
            except Exception as exc:
                self.after(0, lambda e=exc: self._log(f"[debug-detector] error: {e}"))

        threading.Thread(target=worker, daemon=True).start()

    def _transcribir_openai(self, client: OpenAI, *, model: str, timeout_s: int, retries: int, label: str, path: Path) -> str:
        img_url = self._encode_image_data_url(path)
        text = ""
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                kwargs = {
                    "model": model,
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": self._get_vision_ocr_prompt()},
                                {"type": "input_image", "image_url": img_url},
                            ],
                        }
                    ],
                }
                try:
                    resp = client.responses.create(**kwargs, timeout=timeout_s)
                except TypeError:
                    resp = client.responses.create(**kwargs)
                text = (resp.output_text or "").strip()
                self._ocr_raw_first_by_label[str(label)] = str(text or "")
                self._log_usage(
                    provider=PROVIDER_OPENAI,
                    model=model,
                    label=label,
                    usage=getattr(resp, "usage", None),
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                msg = str(exc)
                is_timeout = ("timed out" in msg.lower()) or ("timeout" in msg.lower())
                if attempt < retries and is_timeout:
                    wait_s = min(30, 2 ** attempt)
                    self.after(
                        0,
                        lambda l=label, a=attempt + 1, r=retries, w=wait_s: self._log(
                            f"Timeout IA {l}. Reintentando {a}/{r} en {w}s..."
                        ),
                    )
                    time.sleep(wait_s)
                    continue
                break
        if last_exc is not None or not text:
            raise Exception(last_exc or "Sin texto de salida")
        return text

    def _extract_chat_text(self, content, *, include_reasoning: bool = True) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content.strip()
        # Some SDKs return typed content blocks (pydantic/dataclass objects).
        # Prefer reading known text-bearing attributes before fallback to repr().
        attrs = ("text", "content", "output_text", "reasoning_content") if include_reasoning else ("text", "content", "output_text")
        for attr in attrs:
            try:
                if hasattr(content, attr):
                    txt = self._extract_chat_text(getattr(content, attr), include_reasoning=include_reasoning)
                    if txt:
                        return txt
            except Exception:
                pass
        if hasattr(content, "model_dump"):
            try:
                dumped = content.model_dump()
                txt = self._extract_chat_text(dumped, include_reasoning=include_reasoning)
                if txt:
                    return txt
            except Exception:
                pass
        if isinstance(content, dict):
            keys = ("text", "content", "output_text", "reasoning_content") if include_reasoning else ("text", "content", "output_text")
            for key in keys:
                val = content.get(key)
                txt = self._extract_chat_text(val, include_reasoning=include_reasoning)
                if txt:
                    return txt
            if include_reasoning:
                return str(content).strip()
            return ""
        if isinstance(content, list):
            parts: List[str] = []
            for block in content:
                if isinstance(block, dict):
                    block_type = str(block.get("type", "") or "").strip().lower()
                    if (not include_reasoning) and block_type in {"reasoning", "reasoning_content", "thinking"}:
                        continue
                    txt = self._extract_chat_text(block, include_reasoning=include_reasoning)
                    if txt:
                        parts.append(str(txt))
                else:
                    txt = self._extract_chat_text(block, include_reasoning=include_reasoning)
                    if txt:
                        parts.append(str(txt))
            return "\n".join(parts).strip()
        return str(content).strip()

    def _is_low_signal_model_text(self, text: str) -> bool:
        val = self.controller.normalizar_item_una_linea(str(text or ""))
        if not val:
            return True
        low = val.strip().lower()
        if low in {"true", "false", "null", "none", "ok", "done", "{}", "[]"}:
            return True
        if len(low) <= 3 and not any(ch in low for ch in "{}[]\":,"):
            return True
        return False

    def _build_deepseek_ocr_prompt(self) -> str:
        return self._get_vision_ocr_prompt()

    def _get_vision_ocr_prompt(self) -> str:
        curso = (self.curso_var.get() or "").strip() or "SIN_CURSO"
        tema = (self.tema_var.get() or "").strip() or "SIN_TEMA"
        return build_extract_prompt(curso=curso, tema=tema, start_n=1)

    def _extract_direct_scan_items(self, text: str) -> List[str]:
        raw = self.controller.normalizar_item_una_linea(self._decode_scan_escapes(text or ""))
        if not raw:
            return []
        blocks = [
            self.controller.normalizar_item_una_linea(m.group(1))
            for m in ITEM_BLOCK_RE.finditer(raw)
            if self.controller.normalizar_item_una_linea(m.group(1))
        ]
        if blocks:
            return blocks
        if raw.startswith("\\item"):
            return [raw]
        return []

    def _validate_direct_scan_item(self, item: str) -> Tuple[bool, List[str]]:
        raw = self.controller.normalizar_item_una_linea(self._decode_scan_escapes(item or ""))
        errors: List[str] = []
        if not raw:
            return (False, ["item_vacio"])

        num = self.controller.parsear_numero_original(raw) or 0
        if num <= 0:
            errors.append("numero_no_detectado")

        required_snippets = (
            f"{SEP_LINE}A)",
            f"{SEP_OPT}B)",
            f"{SEP_OPT}C)",
            f"{SEP_LINE}D)",
            f"{SEP_OPT}{SEP_OPT}E)",
        )
        for snippet in required_snippets:
            if snippet not in raw:
                errors.append(f"faltante_{snippet}")

        body = re.sub(
            r"^\s*\\item\s*\[\s*\\textbf\{\s*\d+\.?\s*\}\s*\]\s*",
            "",
            raw,
            flags=re.IGNORECASE,
        ).strip()
        _enu, options = self._extract_options_loose(body)
        for label in ("A", "B", "C", "D", "E"):
            val = str(options.get(label, "") or "").strip()
            if not val:
                errors.append(f"opcion_{label}_faltante")
                continue
            if not (val.startswith("$") and val.endswith("$")):
                errors.append(f"opcion_{label}_sin_bloque_math")

        if raw.count("$") % 2 != 0:
            errors.append("dolares_desbalanceados")

        return (len(errors) == 0, errors)

    def _build_structured_input_from_scan_item(self, item_text: str) -> str:
        raw = self.controller.normalizar_item_una_linea(self._decode_scan_escapes(item_text or ""))
        num = self.controller.parsear_numero_original(raw) or 0
        body = re.sub(
            r"^\s*\\item\s*\[\s*\\textbf\{\s*\d+\.?\s*\}\s*\]\s*",
            "",
            raw,
            flags=re.IGNORECASE,
        ).strip()
        enu, options = self._extract_options_loose(body)
        structured = {
            "num": num,
            "enunciado": self._clean_summary_continuation_text(enu or "...") or "...",
            "figura": "SI" if self._has_image_marker(raw) else "NO",
            "options": {
                "A": str(options.get("A", "...") or "...").strip(),
                "B": str(options.get("B", "...") or "...").strip(),
                "C": str(options.get("C", "...") or "...").strip(),
                "D": str(options.get("D", "...") or "...").strip(),
                "E": str(options.get("E", "...") or "...").strip(),
            },
        }
        return self._build_structured_item_summary(structured)

    def _build_training_pair_key(
        self,
        item_num: int,
        *,
        source_stem: str = "",
        run_id: str = "",
        input_structured: str = "",
    ) -> str:
        num_part = str(item_num) if item_num > 0 else "unk"
        src_part = self._safe_fs_name(source_stem or "src", "src")
        run_part = self._safe_fs_name(run_id or "run", "run")
        raw = self.controller.normalizar_item_una_linea(input_structured or "")
        if not raw:
            raw = f"{source_stem}|{num_part}|{uuid.uuid4().hex[:8]}"
        digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"{run_part}::{src_part}::n{num_part}::{digest}"

    def _find_training_pair_key(
        self,
        *,
        item_num: int,
        source_stem: str,
    ) -> Optional[str]:
        if item_num <= 0:
            return None
        source_norm = self._safe_fs_name(source_stem or "", "")
        fallback_key = f"item#{item_num}"
        best_key: Optional[str] = None
        best_ts = ""
        for key, pair in self._training_pairs_by_item.items():
            if key == fallback_key:
                best_key = key
                best_ts = "9999-99-99T99:99:99"
                continue
            if not isinstance(pair, dict):
                continue
            meta = dict(pair.get("metadata", {}) or {})
            pair_num = int(self._safe_int(meta.get("item_num", 0), 0))
            if pair_num != item_num:
                continue
            pair_source = self._safe_fs_name(str(meta.get("source_stem", "") or ""), "")
            if source_norm and pair_source and pair_source != source_norm:
                continue
            ts = str(pair.get("timestamp", "") or "")
            if best_key is None or ts > best_ts:
                best_key = str(key)
                best_ts = ts
        return best_key

    def _upsert_training_pair(self, key: str, patch: Dict[str, Any]) -> None:
        if not key:
            return
        current = dict(self._training_pairs_by_item.get(key, {}))
        merged = dict(current)
        for field in (
            "input_structured",
            "model_raw_output",
            "model_validated_output",
            "human_final_output",
            "status",
            "prompt_version",
            "run_id",
            "timestamp",
            "source_labels",
            "errors",
            "provider",
            "model",
            "detector_source",
        ):
            if field in patch:
                merged[field] = patch.get(field)
        meta = dict(current.get("metadata", {})) if isinstance(current.get("metadata", {}), dict) else {}
        meta_patch = patch.get("metadata", {})
        if isinstance(meta_patch, dict):
            meta.update(meta_patch)
        merged["metadata"] = meta
        self._training_pairs_by_item[key] = merged

    def _refresh_training_pairs_from_items(self) -> None:
        now_iso = datetime.now().isoformat(timespec="seconds")
        for archivo, item_text, imgs in self._items:
            item = self.controller.normalizar_item_una_linea(str(item_text or ""))
            if not item:
                continue
            num = self.controller.parsear_numero_original(item) or 0
            if num <= 0:
                continue
            source_stem = self._safe_fs_name(str(archivo or "").strip(), "src")
            key = self._find_training_pair_key(item_num=num, source_stem=source_stem)
            if not key:
                key = self._build_training_pair_key(
                    num,
                    source_stem=source_stem,
                    run_id="manual",
                    input_structured=self._build_structured_input_from_scan_item(item),
                )
            current = self._training_pairs_by_item.get(key, {})
            model_validated = self.controller.normalizar_item_una_linea(str(current.get("model_validated_output", "") or ""))
            status = str(current.get("status", "") or "")
            if model_validated and model_validated == item and status == "OK_DIRECTO":
                human_status = "OK_DIRECTO"
            else:
                human_status = "CORREGIDO_MANUAL"
            metadata_current = (
                dict(current.get("metadata", {}))
                if isinstance(current.get("metadata", {}), dict)
                else {}
            )
            image_markers = [str(m) for m in self._extract_image_marker_names(item) if str(m or "").strip()]
            image_paths = [str(p) for p in (imgs or []) if str(p or "").strip()]
            has_marker = bool(image_markers)
            bound_segments: List[int] = []
            bound_slots: List[str] = []
            bound_marker_names: List[str] = []
            src_path = self._find_source_path_by_stem(source_stem)
            if src_path is not None:
                source_key = self._seg_v2_source_key(src_path)
                for seg_idx, payload in self._get_segment_bindings_by_source_key(source_key).items():
                    if int(self._safe_int(payload.get("item_num", 0), 0)) != int(num):
                        continue
                    bound_segments.append(int(seg_idx))
                    slot_name = self._normalize_binding_slot(str(payload.get("slot", "ENUNCIADO") or "ENUNCIADO"))
                    bound_slots.append(slot_name)
                    marker_bound = str(payload.get("marker_name", "") or "").strip()
                    if marker_bound:
                        bound_marker_names.append(marker_bound)
            binding_confirmed = bool(bound_segments)
            existing_has_figure = bool(metadata_current.get("has_figure", False))
            existing_bbox_source = str(
                metadata_current.get("figure_bbox_source", current.get("detector_source", "") or "")
            ).strip().lower()
            manual_image_override = bool(
                has_marker and (not existing_has_figure or existing_bbox_source in {"", "none"})
            )

            metadata_patch: Dict[str, Any] = {
                "item_num": int(num),
                "source_stem": source_stem,
                "human_has_image_marker": has_marker,
                "human_image_markers": image_markers,
                "human_image_paths": image_paths,
                "manual_image_override": manual_image_override,
                "segment_idx": sorted(set(bound_segments)),
                "slot": ",".join(sorted(set(bound_slots))),
                "marker_name": ",".join(sorted(set(bound_marker_names))),
                "binding_confirmed": bool(binding_confirmed),
                "binding_source": "manual_segment_binding" if binding_confirmed else "",
            }
            patch: Dict[str, Any] = {
                "human_final_output": item,
                "status": human_status,
                "metadata": metadata_patch,
            }
            if not str(current.get("input_structured", "") or "").strip():
                patch["input_structured"] = self._build_structured_input_from_scan_item(item)
            if not str(current.get("timestamp", "") or "").strip():
                patch["timestamp"] = now_iso
            if manual_image_override:
                patch["detector_source"] = "manual_marker"
                metadata_patch["has_figure"] = True
                metadata_patch["figure_confirmed"] = True
                metadata_patch["figure_bbox_source"] = "manual_marker"
            self._upsert_training_pair(
                key,
                patch,
            )

    def _build_direct_error_draft(
        self,
        *,
        raw_item: str,
        fallback_numero: int,
    ) -> str:
        raw = self.controller.normalizar_item_una_linea(self._decode_scan_escapes(raw_item or ""))
        body = re.sub(
            r"^\s*\\item\s*\[\s*\\textbf\{\s*\d+\.?\s*\}\s*\]\s*",
            "",
            raw,
            flags=re.IGNORECASE,
        ).strip()
        enu, opts_loose = self._extract_options_loose(body if body else raw)
        options: Dict[str, str] = {}
        for label in ("A", "B", "C", "D", "E"):
            val = str(opts_loose.get(label, "") or "").strip()
            options[label] = val if val else "..."
        enunciado = enu if enu else (self._normalize_plain_ocr(raw)[:280] or "...")
        return self._build_scan_item_strict(
            numero=max(1, int(fallback_numero or 1)),
            enunciado=enunciado,
            options=options,
        )

    def _select_direct_figure_item_positions(
        self,
        *,
        direct_items: List[str],
        has_figure: bool,
    ) -> Set[int]:
        if (not has_figure) or (not direct_items):
            return set()
        scored: List[Tuple[int, int]] = []
        for pos, raw_item in enumerate(direct_items, start=1):
            score = self._item_image_hint_score(self._decode_scan_escapes(raw_item))
            if score > 0:
                scored.append((score, pos))
        scored.sort(key=lambda t: (-t[0], t[1]))
        if scored:
            return {scored[0][1]}
        if len(direct_items) == 1:
            return {1}
        return set()

    def _build_deepseek_ocr_prompt_compact(self) -> str:
        return self._get_vision_ocr_prompt()

    def _looks_like_instruction_echo(self, text: str) -> bool:
        raw = (text or "").strip()
        if not raw:
            return False
        low = raw.lower()
        tokens = (
            "regla fundamental",
            "estructura general",
            "prohibiciones absolutas",
            "validacion final",
            "validación final",
            "patron obligatorio",
            "patrón obligatorio",
            "tu unica funcion",
            "tu única función",
            "respuesta:",
            "no incluyas",
            "no incluyes",
            "no agregue texto",
            "no agregues texto",
            "no agregue",
            "no agregues",
        )
        hits = sum(1 for t in tokens if t in low)
        if hits >= 2:
            return True
        if low.count("\\item[") >= 10 and ("no incluy" in low):
            return True
        return False

    def _looks_like_repetitive_garbage(self, text: str) -> bool:
        raw = (text or "").strip()
        if not raw:
            return False
        low = raw.lower()
        if low.count("no agregue texto") >= 2:
            return True
        if low.count("no agregues texto") >= 2:
            return True
        # Repeated 2-6 word phrase many times, e.g. "No agregue texto."
        if re.search(r"(?i)(\b[\wáéíóúñ]{2,}\b(?:\s+\b[\wáéíóúñ]{2,}\b){1,5})\s+(?:\1\b[\s\.,;:!?-]*){3,}", low):
            return True
        words = re.findall(r"[A-Za-zÁÉÍÓÚáéíóúÑñ0-9]+", raw)
        if len(words) >= 40:
            uniq_ratio = len(set(w.lower() for w in words)) / max(1, len(words))
            if uniq_ratio < 0.25:
                return True
        return False

    def _strip_instruction_echo(self, text: str) -> str:
        out = (text or "").strip()
        if not out:
            return out
        # Remove common instruction fragments when models echo prompt content.
        patterns = [
            r"(?i)actua exclusivamente como[^£æ\n\r]*",
            r"(?i)tu unica funcion[^£æ\n\r]*",
            r"(?i)regla fundamental[^£æ\n\r]*",
            r"(?i)estructura general[^£æ\n\r]*",
            r"(?i)prohibiciones absolutas[^£æ\n\r]*",
            r"(?i)validaci[oó]n final[^£æ\n\r]*",
            r"(?i)patr[oó]n obligatorio[^£æ\n\r]*",
            r"(?i)respuesta:[^£æ\n\r]*",
            r"(?i)no incluy(?:as|es)[^£æ\n\r]*",
        ]
        for pat in patterns:
            out = re.sub(pat, " ", out)
        out = re.sub(r"\s+", " ", out).strip()
        return out

    def _build_leading_options_prompt(self) -> str:
        return (
            "Analiza la imagen y devuelve SOLO JSON valido.\n"
            "Objetivo: extraer el CONTENIDO INICIAL de continuidad ANTES del primer encabezado de nuevo problema "
            "(por ejemplo: 'PROBLEMA', 'N°', '\\item', 'ITEM:').\n"
            "Ese contenido inicial puede incluir:\n"
            "- continuation: texto de enunciado que continua del problema anterior.\n"
            "- opciones A-E del problema anterior.\n"
            'Formato de salida: {"continuation":"","A":"","B":"","C":"","D":"","E":""}\n'
            "Reglas:\n"
            "- Si no hay continuidad inicial, devuelve {}.\n"
            "- No inventes valores.\n"
            "- No agregues explicaciones ni markdown.\n"
        )

    def _parse_options_payload(self, text: str) -> Dict[str, str]:
        raw = (text or "").strip()
        if not raw:
            return {}
        payload = raw
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            payload = m.group(0)
        result: Dict[str, str] = {}
        try:
            data = json.loads(payload)
            if isinstance(data, dict):
                for k, v in data.items():
                    key = str(k or "").strip().upper()
                    if key in {"A", "B", "C", "D", "E"}:
                        val = str(v or "").strip()
                        if val:
                            result[key] = val
        except Exception:
            pass
        if result:
            return result

        for match in re.finditer(r"(?<![A-Za-z0-9])([A-Ea-e])\s*[:=]\s*([^\n\r,;]+)", raw):
            key = (match.group(1) or "").upper()
            val = (match.group(2) or "").strip()
            if key in {"A", "B", "C", "D", "E"} and val and key not in result:
                result[key] = val
        return result

    def _parse_leading_prefill_payload(self, text: str) -> tuple[str, Dict[str, str]]:
        raw = (text or "").strip()
        if not raw:
            return ("", {})

        continuation = ""
        options: Dict[str, str] = {}
        payload = raw
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            payload = m.group(0)

        try:
            data = json.loads(payload)
        except Exception:
            data = None

        if isinstance(data, dict):
            for key_name in ("continuation", "leading_continuation", "enunciado", "texto"):
                value = str(data.get(key_name, "") or "").strip()
                if value:
                    continuation = value
                    break
            for k, v in data.items():
                key = str(k or "").strip().upper()
                if key in {"A", "B", "C", "D", "E"}:
                    val = str(v or "").strip()
                    if val:
                        options[key] = val
            nested = data.get("options")
            if isinstance(nested, dict):
                for k, v in nested.items():
                    key = str(k or "").strip().upper()
                    if key in {"A", "B", "C", "D", "E"}:
                        val = str(v or "").strip()
                        if val and key not in options:
                            options[key] = val

        if not options:
            options = self._parse_options_payload(raw)

        normalized = self.controller.normalizar_item_una_linea(self._decode_scan_escapes(raw))
        first_header = re.search(r"(?i)\b(?:problema|item)\b|\\item", normalized)
        if first_header and first_header.start() > 0:
            prefix = normalized[: first_header.start()].strip()
        else:
            prefix = normalized

        if not continuation:
            enu, _opts = self._extract_options_loose(prefix)
            continuation = (enu or "").strip()

        continuation = re.sub(r"\s+", " ", continuation).strip()
        if continuation.upper() in {"VACIO", "NULO", "NONE"}:
            continuation = ""
        return (continuation, options)

    def _extract_leading_options_openai(
        self,
        client: OpenAI,
        *,
        model: str,
        timeout_s: int,
        retries: int,
        label: str,
        path: Path,
    ) -> tuple[str, Dict[str, str]]:
        img_url = self._encode_image_data_url(path)
        prompt = self._build_leading_options_prompt()
        text = ""
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                kwargs = {
                    "model": model,
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": prompt},
                                {"type": "input_image", "image_url": img_url},
                            ],
                        }
                    ],
                }
                try:
                    resp = client.responses.create(**kwargs, timeout=timeout_s)
                except TypeError:
                    resp = client.responses.create(**kwargs)
                text = (resp.output_text or "").strip()
                self._log_usage(
                    provider=PROVIDER_OPENAI,
                    model=model,
                    label=f"{label} [leading-prefill-pass]",
                    usage=getattr(resp, "usage", None),
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                msg = str(exc).lower()
                is_retryable = ("timeout" in msg) or ("timed out" in msg) or ("rate" in msg) or ("429" in msg)
                if attempt < retries and is_retryable:
                    time.sleep(min(8, 2 ** attempt))
                    continue
                break
        if last_exc is not None:
            return ("", {})
        return self._parse_leading_prefill_payload(text)

    def _extract_leading_options_hf(
        self,
        *,
        model: str,
        timeout_s: int,
        retries: int,
        label: str,
        path: Path,
    ) -> tuple[str, Dict[str, str]]:
        token = self._resolve_hf_token()
        if not token:
            return ("", {})
        base_url = self._resolve_hf_base_url()
        client = OpenAI(base_url=base_url, api_key=token, timeout=timeout_s)
        img_url = self._encode_image_data_url(path)
        prompt = self._build_leading_options_prompt()
        text = ""
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": img_url}}]}],
                    temperature=0,
                    max_tokens=420,
                )
                content = resp.choices[0].message.content if resp and resp.choices else ""
                text = self._extract_chat_text(content)
                self._log_usage(
                    provider=PROVIDER_HF,
                    model=model,
                    label=f"{label} [leading-prefill-pass]",
                    usage=getattr(resp, "usage", None),
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                msg = str(exc).lower()
                is_retryable = ("timeout" in msg) or ("timed out" in msg) or ("rate" in msg) or ("429" in msg)
                if attempt < retries and is_retryable:
                    time.sleep(min(8, 2 ** attempt))
                    continue
                break
        if last_exc is not None:
            return ("", {})
        return self._parse_leading_prefill_payload(text)

    def _build_format_system_prompt(self) -> str:
        return (
            "Eres un editor matematico determinista para items de examen.\n"
            "Piensa internamente, pero NO muestres razonamiento.\n"
            "Salida obligatoria: SOLO JSON valido exacto con forma {\"item\":\"...\"}.\n"
            "No agregues texto fuera del JSON. No markdown. No comentarios.\n"
            "PROHIBIDO:\n"
            "- Inventar contenido.\n"
            "- Resolver el problema.\n"
            "- Mezclar contenido entre items distintos.\n"
            "PRIORIDADES (en orden):\n"
            "1) Preservar semantica del item original.\n"
            "2) Estructura scan exacta.\n"
            "3) Matematica valida y cerrada.\n"
            "Si hay conflicto, preserva semantica y no resuelvas el problema.\n"
        )

    def _build_geometry_pass_system_prompt(self) -> str:
        return (
            "Eres un analista de enunciados matematicos en espanol.\n"
            "Tu tarea es diagnosticar, NO formatear ni reescribir.\n"
            "Devuelve SOLO JSON valido con esta estructura exacta:\n"
            "{\n"
            "  \"razonamiento_es\":\"...\",\n"
            "  \"elementos_geometricos\":[\"...\"],\n"
            "  \"expresiones_sin_dolares\":[\"...\"],\n"
            "  \"alertas\":[\"...\"]\n"
            "}\n"
            "Reglas:\n"
            "- No inventes datos.\n"
            "- No devuelvas markdown ni texto fuera del JSON.\n"
            "- expresiones_sin_dolares debe incluir SOLO expresiones matematicas del ENUNCIADO fuera de $...$.\n"
            "- NO generar alertas sobre falta de figura, grafico no visible o corte visual.\n"
            "- alertas solo para OCR ambiguo textual, ecuacion/expresion incompleta, simbolo roto o inconsistencia matematica textual.\n"
        )

    def _build_geometry_pass_prompt(self, *, raw_item: str, curso_hint: str, tema_hint: str, subtema_hint: str) -> str:
        return (
            "Entrada: solo ENUNCIADO.\n"
            "Analiza SOLO ese enunciado. No reescribas ni resuelvas.\n"
            "Objetivo:\n"
            "1) razonamiento_es breve (max 120 caracteres).\n"
            "2) elementos_geometricos detectados (puntos, segmentos, rectas, triangulos, angulos, medidas, relaciones), maximo 5 elementos.\n"
            "3) expresiones_sin_dolares del enunciado, maximo 5 elementos.\n"
            "4) alertas tecnicas reales (OCR ambiguo textual, ecuacion incompleta, simbolo roto, inconsistencia matematica), maximo 2 elementos.\n"
            "No incluyas alertas sobre figura/grafico/corte visual.\n"
            "Cada string debe ser corto (max 60 caracteres).\n"
            "Salida estrictamente JSON, sin texto adicional.\n"
            "Salida obligatoria: "
            "{\"razonamiento_es\":\"...\",\"elementos_geometricos\":[\"...\"],\"expresiones_sin_dolares\":[\"...\"],\"alertas\":[\"...\"]}\n"
            f"Entrada:\n{raw_item}\n"
        )

    def _build_format_prompt(
        self,
        *,
        raw_item: str,
        curso_hint: str,
        tema_hint: str,
        subtema_hint: str,
        reasoning_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        reasoning_block = ""
        ctx = reasoning_context or {}
        if isinstance(ctx, dict):
            razonamiento = self.controller.normalizar_item_una_linea(str(ctx.get("razonamiento_es", "") or ""))
            elementos = [str(v) for v in self._extract_json_list_field(ctx, "elementos_geometricos")]
            expresiones = [str(v) for v in self._extract_json_list_field(ctx, "expresiones_sin_dolares")]
            alertas = [str(v) for v in self._extract_json_list_field(ctx, "alertas")]
            if razonamiento or elementos or expresiones or alertas:
                lines: List[str] = []
                lines.append("CONTEXTO DE RAZONAMIENTO (opcional, no inventar):")
                if razonamiento:
                    lines.append(f"- razonamiento_es: {razonamiento}")
                if elementos:
                    lines.append(f"- elementos_geometricos: {', '.join(elementos)}")
                if expresiones:
                    lines.append(f"- expresiones_sin_dolares: {', '.join(expresiones)}")
                if alertas:
                    lines.append(f"- alertas: {', '.join(alertas)}")
                reasoning_block = "\n".join(lines) + "\n"

        return (
            "Convierte UN item al formato scan LaTeX.\n"
            "Devuelve SOLO JSON: {\"item\":\"\\\\item[\\\\textbf{n.}] ...\"}\n"
            "No inventes contenido. No resuelvas el problema.\n"
            "Si la entrada viene como ITEM n:/ENUNCIADO:/OPCIONES: (FIGURA opcional), usa esos campos.\n"
            "Si la entrada ya viene como \\item..., normalizala sin cambiar la semantica.\n"
            "Si no hay contexto de razonamiento, continua con solo la entrada.\n"
            "\n"
            "FASE 1 - ESTRUCTURA SCAN (obligatoria):\n"
            "A) Construye exactamente:\n"
            "\\item[\\textbf{n.}] [[curso=...]] [[tema=...]] [[subtema=...]] (opcional) <ENUNCIADO_NORMALIZADO> [[Imagen=img-n]] £A) $...$æB) $...$æC) $...$£D) $...$ææE) $...$£\n"
            "B) [[subtema=...]] es opcional: incluir solo si hay valor; si no, omitir.\n"
            "C) Si FIGURA: SI (cuando exista), incluir [[Imagen=img-n]] al final del enunciado y antes de £A).\n"
            "D) Opciones A-E en un unico bloque $...$ cada una.\n"
            "E) Si una opcion esta ausente/desconocida, usar exactamente $...$.\n"
            "F) Separadores exactos: £A) ...æB) ...æC) ...£D) ...ææE) ...£\n"
            "\n"
            "FASE 2 - NORMALIZACION MATEMATICA (obligatoria):\n"
            "1) NO redetectes expresiones matematicas desde cero.\n"
            "2) Usa el CONTEXTO DE RAZONAMIENTO (expresiones_sin_dolares y elementos_geometricos) como fuente principal.\n"
            "3) Cada expresion indicada por ese contexto debe quedar en un solo bloque $...$ (sin partir ni anidar).\n"
            "4) Repara expresiones rotas con correccion moderada, preservando semantica:\n"
            "   - $PB = AQ$ = AB -> $PB = AQ = AB$\n"
            "   - m\\angle B = $60^\\circ$ -> $m\\angle B = 60^\\circ$\n"
            "   - Calcule$m\\angle BCA -> Calcule $m\\angle BCA$\n"
            "5) No dejes '$' desbalanceados.\n"
            "6) Dentro de $...$ normaliza:\n"
            "   - \\frac y \\tfrac -> \\dfrac\n"
            "   - ° -> ^\\circ\n"
            "   - ∠ -> \\angle\n"
            "   - tg/sen/ctg -> \\tan/\\sin/\\cot cuando corresponda\n"
            "7) Geometria:\n"
            "   - puntos, triangulos y medidas en $...$ cuando aparezcan como simbolos.\n"
            "   - usar \\overline{AB} solo para segmento geometrico; para medida usar $AB$.\n"
            "\n"
            "USA CONTEXTO DE RAZONAMIENTO (si existe):\n"
            "- cada elemento de expresiones_sin_dolares debe quedar envuelto en un solo $...$.\n"
            "- los elementos geometricos detectados deben quedar bien envueltos cuando apliquen.\n"
            "\n"
            "MICRO-EJEMPLOS:\n"
            "Ej1 IN: En un triangulo ABC, m\\angle B = $60^\\circ$. Calcule$m\\angle C$.\n"
            "Ej1 OUT: En un triangulo $ABC$, $m\\angle B = 60^\\circ$. Calcule $m\\angle C$.\n"
            "Ej2 IN: Si $PB = AQ$ = AB, halle x.\n"
            "Ej2 OUT: Si $PB = AQ = AB$, halle $x$.\n"
            "Ej3 IN: A) 60° - \\frac{\\theta}{10}\n"
            "Ej3 OUT: A) $60^\\circ - \\dfrac{\\theta}{10}$\n"
            "\n"
            "VALIDACION FINAL:\n"
            "- item en una sola linea.\n"
            "- '$' balanceados.\n"
            "- patron scan exacto de opciones.\n"
            "- ninguna opcion fuera de $...$.\n"
            f"{reasoning_block}"
            f"Entrada RAW:\n{raw_item}\n"
        )

    def _extract_first_item(self, text: str) -> str:
        raw = self.controller.normalizar_item_una_linea(text or "")
        if not raw:
            return ""
        m = ITEM_BLOCK_RE.search(raw)
        if m:
            return self.controller.normalizar_item_una_linea(m.group(1))
        if raw.startswith("\\item"):
            return raw
        return ""

    def _extract_json_text_field(self, text: str, field: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return ""
        raw = re.sub(r"<think>.*?</think>", " ", raw, flags=re.IGNORECASE | re.DOTALL).strip()
        if not raw:
            return ""

        candidates: List[str] = []
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.IGNORECASE | re.DOTALL)
        if fenced:
            candidates.append((fenced.group(1) or "").strip())

        depth = 0
        start = -1
        in_str = False
        esc = False
        for i, ch in enumerate(raw):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
                continue
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start >= 0:
                        candidates.append(raw[start : i + 1])
                        start = -1

        for payload in reversed(candidates):
            try:
                data = json.loads(payload)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            value = str(data.get(field, "") or "").strip()
            if value:
                return value
        return ""

    def _extract_json_object(self, text: str) -> Dict[str, Any]:
        raw = (text or "").strip()
        if not raw:
            return {}
        raw = re.sub(r"<think>.*?</think>", " ", raw, flags=re.IGNORECASE | re.DOTALL).strip()
        if not raw:
            return {}

        candidates: List[str] = []
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.IGNORECASE | re.DOTALL)
        if fenced:
            candidates.append((fenced.group(1) or "").strip())

        depth = 0
        start = -1
        in_str = False
        esc = False
        for i, ch in enumerate(raw):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
                continue
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start >= 0:
                        candidates.append(raw[start : i + 1])
                        start = -1

        for payload in reversed(candidates):
            data = self._parse_jsonish_object(payload)
            if isinstance(data, dict):
                return data
        return {}

    def _parse_jsonish_object(self, payload: str) -> Dict[str, Any]:
        raw = str(payload or "").strip()
        if not raw:
            return {}
        attempts: List[str] = [raw]

        # Recovery pass for common malformed JSON patterns from LLMs.
        fixed = raw
        fixed = fixed.replace("“", '"').replace("”", '"').replace("’", "'")
        fixed = re.sub(r",\s*([}\]])", r"\1", fixed)  # trailing commas
        fixed = re.sub(r"([{\[,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)", r'\1"\2"\3', fixed)  # bare keys

        def _single_quote_to_json(m: re.Match) -> str:
            inner = str(m.group(1) or "")
            inner = inner.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{inner}"'

        fixed = re.sub(r"'([^'\\]*(?:\\.[^'\\]*)*)'", _single_quote_to_json, fixed)
        if fixed != raw:
            attempts.append(fixed)

        for cand in attempts:
            try:
                data = json.loads(cand)
            except Exception:
                continue
            if isinstance(data, dict):
                return data
        return {}

    def _extract_json_list_field(self, data: Dict[str, Any], field: str) -> List[str]:
        if not isinstance(data, dict):
            return []
        value = data.get(field, [])
        out: List[str] = []
        if isinstance(value, list):
            for entry in value:
                txt = self.controller.normalizar_item_una_linea(str(entry or ""))
                if txt:
                    out.append(txt)
        elif isinstance(value, str):
            for part in re.split(r"[;\n]+", value):
                txt = self.controller.normalizar_item_una_linea(str(part or ""))
                if txt:
                    out.append(txt)
        return out

    def _extract_reasoning_payload(self, text: str) -> Dict[str, Any]:
        data = self._extract_json_object(text or "")
        if not data:
            data = self._extract_reasoning_payload_loose(text or "")
        if not data:
            return {
                "razonamiento_es": "",
                "elementos_geometricos": [],
                "expresiones_sin_dolares": [],
                "alertas": ["salida_no_json_valida"],
            }
        payload = {
            "razonamiento_es": self.controller.normalizar_item_una_linea(str(data.get("razonamiento_es", "") or "")),
            "elementos_geometricos": self._extract_json_list_field(data, "elementos_geometricos"),
            "expresiones_sin_dolares": self._extract_json_list_field(data, "expresiones_sin_dolares"),
            "alertas": self._extract_json_list_field(data, "alertas"),
        }
        if not payload["alertas"] and (
            (not payload["razonamiento_es"])
            and (not payload["elementos_geometricos"])
            and (not payload["expresiones_sin_dolares"])
        ):
            payload["alertas"] = ["salida_json_sin_campos"]
        return payload

    def _extract_reasoning_payload_loose(self, text: str) -> Dict[str, Any]:
        raw = str(text or "").strip()
        if not raw:
            return {}
        raw = re.sub(r"<think>.*?</think>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
        raw = re.sub(r"```(?:json)?", " ", raw, flags=re.IGNORECASE)
        raw = raw.replace("```", " ")
        raw = re.sub(r"\s+", " ", raw).strip()
        if not raw:
            return {}

        def _json_unescape(value: str) -> str:
            v = str(value or "").strip()
            if not v:
                return ""
            try:
                return str(json.loads(f"\"{v}\""))
            except Exception:
                return v

        key_names = (
            "razonamiento_es",
            "elementos_geometricos",
            "expresiones_sin_dolares",
            "alertas",
        )

        def _segment_for_key(key: str) -> str:
            m = re.search(rf"(?is)(?:\"{re.escape(key)}\"|{re.escape(key)})\s*:\s*", raw)
            if not m:
                return ""
            start = m.end()
            tail = raw[start:]
            next_hits: List[int] = []
            for other in key_names:
                if other == key:
                    continue
                m2 = re.search(rf"(?is)(?:\"{re.escape(other)}\"|{re.escape(other)})\s*:\s*", tail)
                if m2:
                    next_hits.append(m2.start())
            if next_hits:
                tail = tail[: min(next_hits)]
            return tail.strip(" ,")

        def _extract_string_field(key: str) -> str:
            seg = _segment_for_key(key)
            if not seg:
                return ""
            m = re.search(r'^"((?:\\.|[^"\\])*)"', seg)
            if m:
                return self.controller.normalizar_item_una_linea(_json_unescape(m.group(1)))
            m = re.search(r"^'([^']*)'", seg)
            if m:
                return self.controller.normalizar_item_una_linea(str(m.group(1) or ""))
            # Fallback: until first comma/closing brace.
            plain = re.split(r"[,\}]", seg, maxsplit=1)[0]
            plain = plain.strip(" \"'")
            return self.controller.normalizar_item_una_linea(plain)

        def _extract_list_field(key: str) -> List[str]:
            seg = _segment_for_key(key)
            if not seg:
                return []
            items: List[str] = []
            for quoted in re.findall(r'"((?:\\.|[^"\\])*)"', seg):
                txt = self.controller.normalizar_item_una_linea(_json_unescape(quoted))
                if txt:
                    items.append(txt)
            if not items:
                for quoted in re.findall(r"'([^']*)'", seg):
                    txt = self.controller.normalizar_item_una_linea(str(quoted or ""))
                    if txt:
                        items.append(txt)
            if not items:
                buf = seg
                if "[" in buf:
                    buf = buf.split("[", 1)[1]
                if "]" in buf:
                    buf = buf.split("]", 1)[0]
                for part in buf.split(","):
                    txt = self.controller.normalizar_item_una_linea(part.strip(" \"'"))
                    if txt:
                        items.append(txt)
            return items

        razonamiento = _extract_string_field("razonamiento_es")
        elementos = _extract_list_field("elementos_geometricos")
        expresiones = _extract_list_field("expresiones_sin_dolares")
        alertas = _extract_list_field("alertas")

        if not razonamiento and not elementos and not expresiones and not alertas:
            return {}
        return {
            "razonamiento_es": razonamiento,
            "elementos_geometricos": elementos,
            "expresiones_sin_dolares": expresiones,
            "alertas": alertas,
        }

    def _is_reasoning_payload_json_invalid(self, payload: Dict[str, Any]) -> bool:
        alertas = [self._norm_key(str(v or "")) for v in self._extract_json_list_field(payload, "alertas")]
        return "salida_no_json_valida" in alertas

    def _build_geometry_json_retry_prompt(self, raw_item: str, model_output: str = "") -> str:
        base = (
            "Convierte la salida a SOLO JSON valido exacto con esta forma:\n"
            "{\"razonamiento_es\":\"...\",\"elementos_geometricos\":[\"...\"],\"expresiones_sin_dolares\":[\"...\"],\"alertas\":[\"...\"]}\n"
            "Sin markdown. Sin explicaciones. Sin texto adicional.\n"
            "No incluyas alertas de figura o grafico.\n"
            "Salida compacta: razonamiento_es <= 120 caracteres; listas cortas.\n"
            "Si falta un campo, devuelvelo vacio ([] o \"\").\n"
        )
        prev = self.controller.normalizar_item_una_linea(str(model_output or ""))
        if prev:
            return (
                f"{base}"
                "Texto a convertir:\n"
                f"{prev}\n"
                "Enunciado de referencia:\n"
                f"{raw_item}\n"
            )
        return (
            f"{base}"
            "Entrada:\n"
            f"{raw_item}\n"
        )

    def _extract_reasoning_enunciado_text(self, raw_item: str) -> str:
        text = self.controller.normalizar_item_una_linea(str(raw_item or ""))
        if not text:
            return ""
        m = re.search(r"(?is)\bENUNCIADO\s*:\s*(.*)$", text)
        if m:
            return self.controller.normalizar_item_una_linea(m.group(1) or "")
        return text

    def _build_local_reasoning_fallback(self, raw_item: str) -> Dict[str, Any]:
        enu = self._extract_reasoning_enunciado_text(raw_item)
        math_re = re.compile(r"(\$\$.*?\$\$|(?<!\$)\$(?!\$).*?(?<!\$)\$(?!\$))", re.DOTALL)

        # Heuristic expressions outside $...$.
        expr_candidates: List[str] = []
        parts = math_re.split(enu)
        eq_pat = re.compile(r"([A-Za-z0-9\\\(\)\+\-\*/\^\s]{1,40}[=<>][A-Za-z0-9\\\(\)\+\-\*/\^\s]{1,40})")
        ang_pat = re.compile(r"(m\s*(?:∠|\\angle)\s*[A-Z]{1,3}(?:\s*[=<>]\s*[^,;:.]{1,40})?)")
        deg_pat = re.compile(r"(\d+(?:[.,]\d+)?\s*(?:°|\^\s*\\?circ))")
        for i, chunk in enumerate(parts):
            if i % 2 == 1:
                continue
            for pat in (ang_pat, eq_pat, deg_pat):
                for m in pat.finditer(chunk):
                    cand = self.controller.normalizar_item_una_linea(m.group(1) or "")
                    if cand:
                        expr_candidates.append(cand)

        # Heuristic geometric elements.
        elem_candidates: List[str] = []
        for m in re.finditer(r"\b([A-Z]{3})\b", enu):
            tri = (m.group(1) or "").strip()
            if tri:
                elem_candidates.append(f"triangulo {tri}")
        for m in re.finditer(r"\bpunto\s+([A-Z])\b", enu, re.IGNORECASE):
            p = (m.group(1) or "").strip().upper()
            if p:
                elem_candidates.append(f"punto {p}")
        for m in re.finditer(r"\b(?:segmento|lado|recta|sobre)\s+([A-Z]{2,3})\b", enu, re.IGNORECASE):
            s = (m.group(1) or "").strip().upper()
            if s:
                elem_candidates.append(f"segmento {s}")
        for m in re.finditer(r"(m\s*(?:∠|\\angle)\s*[A-Z]{1,3})", enu):
            a = self.controller.normalizar_item_una_linea(m.group(1) or "")
            if a:
                elem_candidates.append(a)

        payload = {
            "razonamiento_es": "Fallback local aplicado por salida JSON invalida del modelo.",
            "elementos_geometricos": elem_candidates,
            "expresiones_sin_dolares": expr_candidates,
            "alertas": ["fallback_local_aplicado"],
            "__meta": {
                "json_invalid_count": 1,
                "filtered_figure_alert_count": 0,
                "retry_json_count": 0,
                "local_fallback_count": 1,
            },
        }
        return self._sanitize_reasoning_payload(payload)

    def _sanitize_reasoning_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        src = payload if isinstance(payload, dict) else {}
        src_meta = src.get("__meta", {}) if isinstance(src, dict) else {}
        if not isinstance(src_meta, dict):
            src_meta = {}
        prev_retry_count = int(src_meta.get("retry_json_count", 0) or 0)
        prev_json_invalid_count = int(src_meta.get("json_invalid_count", 0) or 0)
        prev_filtered_figure_count = int(src_meta.get("filtered_figure_alert_count", 0) or 0)
        prev_local_fallback_count = int(src_meta.get("local_fallback_count", 0) or 0)
        prev_raw_model_text = self._clip_debug_text(str(src_meta.get("raw_model_text", "") or ""))
        prev_raw_retry_text = self._clip_debug_text(str(src_meta.get("raw_retry_text", "") or ""))
        razonamiento = self.controller.normalizar_item_una_linea(str(src.get("razonamiento_es", "") or ""))
        if len(razonamiento) > 180:
            razonamiento = razonamiento[:180].rstrip()

        def _dedup_limited(values: List[str], limit: int) -> List[str]:
            out: List[str] = []
            seen: Set[str] = set()
            for val in values:
                txt = self.controller.normalizar_item_una_linea(str(val or ""))
                if not txt:
                    continue
                key = self._norm_key(txt)
                if key in seen:
                    continue
                seen.add(key)
                out.append(txt)
                if len(out) >= limit:
                    break
            return out

        elementos = _dedup_limited(self._extract_json_list_field(src, "elementos_geometricos"), 5)
        expresiones = _dedup_limited(self._extract_json_list_field(src, "expresiones_sin_dolares"), 5)
        raw_alertas = self._extract_json_list_field(src, "alertas")
        figure_terms = (
            "figura",
            "grafico",
            "gráfico",
            "falta figura",
            "no se proporciona figura",
            "grafico no visible",
            "gráfico no visible",
            "corte de enunciado",
            "corte visual",
        )
        filtered_figure_alert_count = 0
        alertas_tmp: List[str] = []
        for alert in raw_alertas:
            norm = self._norm_key(alert)
            if any(term in norm for term in figure_terms):
                filtered_figure_alert_count += 1
                continue
            alertas_tmp.append(alert)
        alertas = _dedup_limited(alertas_tmp, 2)

        if not alertas and (not razonamiento) and (not elementos) and (not expresiones):
            alertas = ["salida_json_sin_campos"]

        out = {
            "razonamiento_es": razonamiento,
            "elementos_geometricos": elementos,
            "expresiones_sin_dolares": expresiones,
            "alertas": alertas,
            "__meta": {
                "json_invalid_count": max(
                    int(prev_json_invalid_count),
                    1 if self._is_reasoning_payload_json_invalid(src) else 0,
                ),
                "filtered_figure_alert_count": int(prev_filtered_figure_count + filtered_figure_alert_count),
                "retry_json_count": int(prev_retry_count),
                "local_fallback_count": int(prev_local_fallback_count),
                "raw_model_text": prev_raw_model_text,
                "raw_retry_text": prev_raw_retry_text,
            },
        }
        return out

    def _clip_debug_text(self, text: str, limit: int = 4000) -> str:
        raw = str(text or "")
        if not raw:
            return ""
        if len(raw) <= limit:
            return raw
        return raw[:limit].rstrip() + " ...[truncado]"

    def _build_geometry_pass_display(
        self,
        *,
        razonamiento_es: str,
        elementos_geometricos: List[str],
        expresiones_sin_dolares: List[str],
        alertas: List[str],
        raw_model_text: str = "",
        raw_retry_text: str = "",
    ) -> str:
        rows: List[str] = []
        rows.append("RAZONAMIENTO_ES:")
        rows.append(self.controller.normalizar_item_una_linea(razonamiento_es or "") or "(sin razonamiento reportado)")
        rows.append("ELEMENTOS_GEOMETRICOS:")
        if elementos_geometricos:
            rows.extend([f"- {v}" for v in elementos_geometricos])
        else:
            rows.append("- (sin elementos reportados)")
        rows.append("EXPRESIONES_SIN_DOLARES:")
        if expresiones_sin_dolares:
            rows.extend([f"- {v}" for v in expresiones_sin_dolares])
        else:
            rows.append("- (ninguna)")
        rows.append("ALERTAS:")
        if alertas:
            rows.extend([f"- {v}" for v in alertas])
        else:
            rows.append("- (ninguna)")
        if raw_model_text:
            rows.append("RAW_MODELO_P1:")
            rows.append(raw_model_text.strip())
        if raw_retry_text:
            rows.append("RAW_MODELO_P1_RETRY:")
            rows.append(raw_retry_text.strip())
        return "\n".join(rows).strip()

    def _replace_enunciado_in_structured_item(self, raw_item: str, new_enunciado: str) -> str:
        item = self.controller.normalizar_item_una_linea(raw_item or "")
        enu = self.controller.normalizar_item_una_linea(new_enunciado or "")
        if not item or not enu:
            return item

        pat = re.compile(
            r"(?is)(\bENUNCIADO\s*:\s*)(.*?)(\s*(?:FIGURA\s*:|OPCIONES\s*:))"
        )
        m = pat.search(item)
        if not m:
            return item
        return f"{item[:m.start()]}{m.group(1)}{enu}{m.group(3)}{item[m.end():]}".strip()

    def _extract_structured_enunciado(self, raw_item: str) -> str:
        item = self.controller.normalizar_item_una_linea(raw_item or "")
        if not item:
            return ""
        m = re.search(
            r"(?is)\bENUNCIADO\s*:\s*(.*?)(?:\s*\bFIGURA\s*:|\s*\bOPCIONES\s*:|$)",
            item,
        )
        if m:
            return self.controller.normalizar_item_una_linea(m.group(1) or "")
        return ""

    def _extract_formatted_item(self, text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return ""
        # Drop explicit reasoning blocks from reasoning models (e.g. Qwen <think> ... </think>).
        raw = re.sub(r"<think>.*?</think>", " ", raw, flags=re.IGNORECASE | re.DOTALL).strip()
        if not raw:
            return ""

        def _item_from_payload(payload: str) -> str:
            try:
                data = json.loads(payload)
            except Exception:
                return ""
            if not isinstance(data, dict):
                return ""
            item = str(data.get("item", "") or "").strip()
            if not item:
                return ""
            return self._extract_first_item(item) or self.controller.normalizar_item_una_linea(item)

        # Prefer fenced JSON blocks if present.
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.IGNORECASE | re.DOTALL)
        if fenced:
            parsed = _item_from_payload((fenced.group(1) or "").strip())
            if parsed:
                return parsed

        # Parse all top-level JSON object candidates and keep the last valid one.
        candidates: List[str] = []
        depth = 0
        start = -1
        in_str = False
        esc = False
        for i, ch in enumerate(raw):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
                continue
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start >= 0:
                        candidates.append(raw[start : i + 1])
                        start = -1
        for payload in reversed(candidates):
            parsed = _item_from_payload(payload)
            if parsed:
                return parsed

        # Final fallback only if the content itself is already a clean \item line.
        compact = self.controller.normalizar_item_una_linea(raw)
        if compact.startswith("\\item") and ("i need to" not in compact.lower()):
            return self._extract_first_item(compact)
        return ""

    def _format_item_openai(
        self,
        client: OpenAI,
        *,
        model: str,
        timeout_s: int,
        retries: int,
        label: str,
        raw_item: str,
        curso_hint: str,
        tema_hint: str,
        subtema_hint: str,
        run_geometry_pass: bool = True,
        reasoning_payload: Optional[Dict[str, Any]] = None,
    ) -> str:
        format_model = self._resolve_openai_format_model(model)
        working_raw = raw_item
        local_reasoning_payload: Dict[str, Any] = dict(reasoning_payload or {})
        if run_geometry_pass:
            # Pass 1: reasoning/diagnostic pass.
            try:
                geo_system_prompt = self._build_geometry_pass_system_prompt()
                geo_prompt = self._build_geometry_pass_prompt(
                    raw_item=working_raw,
                    curso_hint=curso_hint,
                    tema_hint=tema_hint,
                    subtema_hint=subtema_hint,
                )
                geo_kwargs = {
                    "model": format_model,
                    "input": [
                        {"role": "system", "content": [{"type": "input_text", "text": geo_system_prompt}]},
                        {"role": "user", "content": [{"type": "input_text", "text": geo_prompt}]},
                    ],
                }
                try:
                    geo_resp = client.responses.create(**geo_kwargs, timeout=timeout_s)
                except TypeError:
                    geo_resp = client.responses.create(**geo_kwargs)
                geo_text = (geo_resp.output_text or "").strip()
                self._log_usage(
                    provider=PROVIDER_OPENAI,
                    model=format_model,
                    label=f"{label} [geometry-pass]",
                    usage=getattr(geo_resp, "usage", None),
                )
                local_reasoning_payload = self._extract_reasoning_payload(geo_text)
            except Exception:
                pass

        geo_key = str(label or "").strip()
        if geo_key and local_reasoning_payload:
            p1_text = self._build_geometry_pass_display(
                razonamiento_es=str(local_reasoning_payload.get("razonamiento_es", "") or ""),
                elementos_geometricos=list(local_reasoning_payload.get("elementos_geometricos", []) or []),
                expresiones_sin_dolares=list(local_reasoning_payload.get("expresiones_sin_dolares", []) or []),
                alertas=list(local_reasoning_payload.get("alertas", []) or []),
            )
            self._geometry_pass_by_label[f"{geo_key} [modelo-p1]"] = (
                p1_text or "[sin salida modelo p1]"
            )
            self._geometry_pass_payload_by_label[geo_key] = {
                "razonamiento_es": self.controller.normalizar_item_una_linea(
                    str(local_reasoning_payload.get("razonamiento_es", "") or "")
                ),
                "elementos_geometricos": list(local_reasoning_payload.get("elementos_geometricos", []) or []),
                "expresiones_sin_dolares": list(local_reasoning_payload.get("expresiones_sin_dolares", []) or []),
                "alertas": list(local_reasoning_payload.get("alertas", []) or []),
            }

        system_prompt = self._build_format_system_prompt()
        prompt = self._build_format_prompt(
            raw_item=working_raw,
            curso_hint=curso_hint,
            tema_hint=tema_hint,
            subtema_hint=subtema_hint,
            reasoning_context=local_reasoning_payload,
        )
        text = ""
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                kwargs = {
                    "model": format_model,
                    "input": [
                        {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                        {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
                    ],
                }
                try:
                    resp = client.responses.create(**kwargs, timeout=timeout_s)
                except TypeError:
                    resp = client.responses.create(**kwargs)
                text = (resp.output_text or "").strip()
                self._log_usage(
                    provider=PROVIDER_OPENAI,
                    model=format_model,
                    label=f"{label} [format-pass]",
                    usage=getattr(resp, "usage", None),
                )
                out_key = str(label or "").strip()
                if out_key:
                    p2_text = text or self._extract_structured_enunciado(working_raw) or "[sin salida modelo p2]"
                    self._geometry_pass_by_label[f"{out_key} [modelo-p2]"] = p2_text
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                msg = str(exc).lower()
                is_retryable = ("timeout" in msg) or ("timed out" in msg) or ("rate" in msg) or ("429" in msg)
                if attempt < retries and is_retryable:
                    time.sleep(min(8, 2 ** attempt))
                    continue
                break
        if last_exc is not None:
            return ""
        formatted_item = self._extract_formatted_item(text)
        if formatted_item:
            key = str(label or "").strip()
            if key:
                # "Ver pasada 1" must show what the formatting model produced.
                self._geometry_pass_by_label[key] = formatted_item
        return formatted_item

    def _format_item_hf(
        self,
        *,
        model: str,
        timeout_s: int,
        retries: int,
        label: str,
        raw_item: str,
        curso_hint: str,
        tema_hint: str,
        subtema_hint: str,
        run_geometry_pass: bool = True,
        reasoning_payload: Optional[Dict[str, Any]] = None,
    ) -> str:
        token = self._resolve_hf_token()
        if not token:
            return ""
        base_url = self._resolve_hf_base_url()
        client = OpenAI(base_url=base_url, api_key=token, timeout=timeout_s)
        format_model = self._resolve_hf_format_model(model)
        working_raw = raw_item
        local_reasoning_payload: Dict[str, Any] = dict(reasoning_payload or {})
        if run_geometry_pass:
            # Pass 1: reasoning/diagnostic pass.
            try:
                geo_system_prompt = self._build_geometry_pass_system_prompt()
                geo_prompt = self._build_geometry_pass_prompt(
                    raw_item=working_raw,
                    curso_hint=curso_hint,
                    tema_hint=tema_hint,
                    subtema_hint=subtema_hint,
                )
                try:
                    geo_resp = client.chat.completions.create(
                        model=format_model,
                        messages=[
                            {"role": "system", "content": geo_system_prompt},
                            {"role": "user", "content": [{"type": "text", "text": geo_prompt}]},
                        ],
                        temperature=0,
                        max_tokens=700,
                        response_format={"type": "json_object"},
                    )
                except Exception as geo_json_exc:
                    geo_err = str(geo_json_exc).lower()
                    if "response_format" not in geo_err and "json_object" not in geo_err:
                        raise
                    geo_resp = client.chat.completions.create(
                        model=format_model,
                        messages=[
                            {"role": "system", "content": geo_system_prompt},
                            {"role": "user", "content": [{"type": "text", "text": geo_prompt}]},
                        ],
                        temperature=0,
                        max_tokens=700,
                    )
                geo_msg = geo_resp.choices[0].message if geo_resp and geo_resp.choices else None
                geo_content = geo_msg.content if geo_msg is not None else ""
                geo_text = self._extract_chat_text(geo_content)
                if not geo_text and geo_msg is not None:
                    geo_text = self._extract_chat_text(getattr(geo_msg, "reasoning_content", None))
                self._log_usage(
                    provider=PROVIDER_HF,
                    model=format_model,
                    label=f"{label} [geometry-pass]",
                    usage=getattr(geo_resp, "usage", None),
                )
                local_reasoning_payload = self._extract_reasoning_payload(geo_text)
            except Exception:
                pass

        geo_key = str(label or "").strip()
        if geo_key and local_reasoning_payload:
            p1_text = self._build_geometry_pass_display(
                razonamiento_es=str(local_reasoning_payload.get("razonamiento_es", "") or ""),
                elementos_geometricos=list(local_reasoning_payload.get("elementos_geometricos", []) or []),
                expresiones_sin_dolares=list(local_reasoning_payload.get("expresiones_sin_dolares", []) or []),
                alertas=list(local_reasoning_payload.get("alertas", []) or []),
            )
            self._geometry_pass_by_label[f"{geo_key} [modelo-p1]"] = (
                p1_text or "[sin salida modelo p1]"
            )
            self._geometry_pass_payload_by_label[geo_key] = {
                "razonamiento_es": self.controller.normalizar_item_una_linea(
                    str(local_reasoning_payload.get("razonamiento_es", "") or "")
                ),
                "elementos_geometricos": list(local_reasoning_payload.get("elementos_geometricos", []) or []),
                "expresiones_sin_dolares": list(local_reasoning_payload.get("expresiones_sin_dolares", []) or []),
                "alertas": list(local_reasoning_payload.get("alertas", []) or []),
            }

        system_prompt = self._build_format_system_prompt()
        prompt = self._build_format_prompt(
            raw_item=working_raw,
            curso_hint=curso_hint,
            tema_hint=tema_hint,
            subtema_hint=subtema_hint,
            reasoning_context=local_reasoning_payload,
        )
        text = ""
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                try:
                    resp = client.chat.completions.create(
                        model=format_model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": [{"type": "text", "text": prompt}]},
                        ],
                        temperature=0,
                        max_tokens=700,
                        response_format={"type": "json_object"},
                    )
                except Exception as json_exc:
                    err_json = str(json_exc).lower()
                    if "response_format" not in err_json and "json_object" not in err_json:
                        raise
                    resp = client.chat.completions.create(
                        model=format_model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": [{"type": "text", "text": prompt}]},
                        ],
                        temperature=0,
                        max_tokens=700,
                    )
                msg_obj = resp.choices[0].message if resp and resp.choices else None
                content = msg_obj.content if msg_obj is not None else ""
                text = self._extract_chat_text(content)
                if not text and msg_obj is not None:
                    text = self._extract_chat_text(getattr(msg_obj, "reasoning_content", None))
                self._log_usage(
                    provider=PROVIDER_HF,
                    model=format_model,
                    label=f"{label} [format-pass]",
                    usage=getattr(resp, "usage", None),
                )
                out_key = str(label or "").strip()
                if out_key:
                    p2_text = text or self._extract_structured_enunciado(working_raw) or "[sin salida modelo p2]"
                    self._geometry_pass_by_label[f"{out_key} [modelo-p2]"] = p2_text
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                msg = str(exc).lower()
                is_retryable = ("timeout" in msg) or ("timed out" in msg) or ("rate" in msg) or ("429" in msg)
                if attempt < retries and is_retryable:
                    time.sleep(min(8, 2 ** attempt))
                    continue
                break
        if last_exc is not None:
            return ""
        formatted_item = self._extract_formatted_item(text)
        if formatted_item:
            key = str(label or "").strip()
            if key:
                # "Ver pasada 1" must show what the formatting model produced.
                self._geometry_pass_by_label[key] = formatted_item
        return formatted_item

    def _reason_item_openai(
        self,
        client: OpenAI,
        *,
        model: str,
        timeout_s: int,
        retries: int,
        label: str,
        raw_item: str,
        curso_hint: str,
        tema_hint: str,
        subtema_hint: str,
    ) -> Dict[str, Any]:
        format_model = self._resolve_openai_format_model(model)
        geo_system_prompt = self._build_geometry_pass_system_prompt()
        geo_prompt = self._build_geometry_pass_prompt(
            raw_item=raw_item,
            curso_hint=curso_hint,
            tema_hint=tema_hint,
            subtema_hint=subtema_hint,
        )
        json_retry_count = 0
        raw_model_text = ""
        raw_retry_text = ""

        def _call_reasoner(prompt_text: str, label_suffix: str, max_tokens: int) -> str:
            text = ""
            last_exc: Exception | None = None
            for attempt in range(retries + 1):
                try:
                    kwargs = {
                        "model": format_model,
                        "input": [
                            {"role": "system", "content": [{"type": "input_text", "text": geo_system_prompt}]},
                            {"role": "user", "content": [{"type": "input_text", "text": prompt_text}]},
                        ],
                        "max_output_tokens": int(max_tokens),
                    }
                    try:
                        resp = client.responses.create(**kwargs, timeout=timeout_s)
                    except TypeError:
                        try:
                            resp = client.responses.create(**kwargs)
                        except TypeError:
                            kwargs.pop("max_output_tokens", None)
                            resp = client.responses.create(**kwargs)
                    text = (resp.output_text or "").strip()
                    self._log_usage(
                        provider=PROVIDER_OPENAI,
                        model=format_model,
                        label=f"{label} [{label_suffix}]",
                        usage=getattr(resp, "usage", None),
                    )
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    msg = str(exc).lower()
                    is_retryable = ("timeout" in msg) or ("timed out" in msg) or ("rate" in msg) or ("429" in msg)
                    if attempt < retries and is_retryable:
                        time.sleep(min(8, 2 ** attempt))
                        continue
                    break
            if last_exc is not None:
                raise last_exc
            return text

        try:
            text = _call_reasoner(geo_prompt, "geometry-pass", 360)
            raw_model_text = text
            payload = self._extract_reasoning_payload(text)
            if self._is_reasoning_payload_json_invalid(payload):
                json_retry_count = 1
                retry_prompt = self._build_geometry_json_retry_prompt(raw_item, text)
                try:
                    retry_text = _call_reasoner(retry_prompt, "geometry-pass-json-retry", 220)
                    raw_retry_text = retry_text
                    retry_payload = self._extract_reasoning_payload(retry_text)
                    if not self._is_reasoning_payload_json_invalid(retry_payload):
                        payload = retry_payload
                    else:
                        payload = self._build_local_reasoning_fallback(raw_item)
                except Exception:
                    payload = self._build_local_reasoning_fallback(raw_item)
            meta = payload.get("__meta", {}) if isinstance(payload, dict) else {}
            if not isinstance(meta, dict):
                meta = {}
            meta["raw_model_text"] = self._clip_debug_text(raw_model_text)
            meta["raw_retry_text"] = self._clip_debug_text(raw_retry_text)
            payload["__meta"] = meta
            payload = self._sanitize_reasoning_payload(payload)
            meta = payload.get("__meta", {}) if isinstance(payload, dict) else {}
            if not isinstance(meta, dict):
                meta = {}
            meta["retry_json_count"] = int(json_retry_count)
            payload["__meta"] = meta
            return payload
        except Exception as exc:
            payload = self._build_local_reasoning_fallback(raw_item)
            alerts = list(payload.get("alertas", []) or [])
            alerts.append(f"error_modelo:{exc}")
            payload["alertas"] = alerts
            meta = payload.get("__meta", {}) if isinstance(payload, dict) else {}
            if not isinstance(meta, dict):
                meta = {}
            meta["raw_model_text"] = self._clip_debug_text(raw_model_text)
            meta["raw_retry_text"] = self._clip_debug_text(raw_retry_text)
            payload["__meta"] = meta
            payload = self._sanitize_reasoning_payload(payload)
            meta = payload.get("__meta", {}) if isinstance(payload, dict) else {}
            if not isinstance(meta, dict):
                meta = {}
            meta["retry_json_count"] = int(json_retry_count)
            payload["__meta"] = meta
            return payload

    def _reason_item_hf(
        self,
        *,
        model: str,
        timeout_s: int,
        retries: int,
        label: str,
        raw_item: str,
        curso_hint: str,
        tema_hint: str,
        subtema_hint: str,
    ) -> Dict[str, Any]:
        token = self._resolve_hf_token()
        if not token:
            return {
                "razonamiento_es": "",
                "elementos_geometricos": [],
                "expresiones_sin_dolares": [],
                "alertas": ["hf_token_ausente"],
            }
        base_url = self._resolve_hf_base_url()
        client = OpenAI(base_url=base_url, api_key=token, timeout=timeout_s)
        format_model = self._resolve_hf_format_model(model)
        geo_system_prompt = self._build_geometry_pass_system_prompt()
        geo_prompt = self._build_geometry_pass_prompt(
            raw_item=raw_item,
            curso_hint=curso_hint,
            tema_hint=tema_hint,
            subtema_hint=subtema_hint,
        )
        json_retry_count = 0
        raw_model_text = ""
        raw_retry_text = ""

        def _call_reasoner(prompt_text: str, label_suffix: str, max_tokens: int) -> str:
            text = ""
            last_exc: Exception | None = None
            for attempt in range(retries + 1):
                try:
                    messages_payload = [
                        {"role": "system", "content": geo_system_prompt},
                        {"role": "user", "content": prompt_text},
                    ]
                    json_schema = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "geometry_pass",
                            "schema": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "razonamiento_es": {"type": "string"},
                                    "elementos_geometricos": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "expresiones_sin_dolares": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "alertas": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                },
                                "required": [
                                    "razonamiento_es",
                                    "elementos_geometricos",
                                    "expresiones_sin_dolares",
                                    "alertas",
                                ],
                            },
                        },
                    }
                    resp = None
                    schema_exc: Exception | None = None
                    try:
                        resp = client.chat.completions.create(
                            model=format_model,
                            messages=messages_payload,
                            temperature=0,
                            max_tokens=int(max_tokens),
                            response_format=json_schema,
                        )
                    except Exception as exc_schema:
                        schema_exc = exc_schema
                    if resp is None:
                        try:
                            resp = client.chat.completions.create(
                                model=format_model,
                                messages=messages_payload,
                                temperature=0,
                                max_tokens=int(max_tokens),
                                response_format={"type": "json_object"},
                            )
                        except Exception as geo_json_exc:
                            geo_err = str(geo_json_exc).lower()
                            schema_err = str(schema_exc).lower() if schema_exc is not None else ""
                            if (
                                ("response_format" not in geo_err and "json_object" not in geo_err)
                                and ("response_format" not in schema_err and "json_schema" not in schema_err)
                            ):
                                raise
                            resp = client.chat.completions.create(
                                model=format_model,
                                messages=messages_payload,
                                temperature=0,
                                max_tokens=int(max_tokens),
                            )
                    msg_obj = resp.choices[0].message if resp and resp.choices else None
                    content = msg_obj.content if msg_obj is not None else ""
                    content_text = self._extract_chat_text(content, include_reasoning=False)
                    reasoning_text = ""
                    if msg_obj is not None:
                        reasoning_text = self._extract_chat_text(
                            getattr(msg_obj, "reasoning_content", None),
                            include_reasoning=True,
                        )
                    if self._is_low_signal_model_text(content_text) and (not self._is_low_signal_model_text(reasoning_text)):
                        text = reasoning_text
                    else:
                        text = content_text
                    if (not text) and reasoning_text:
                        text = reasoning_text
                    self._log_usage(
                        provider=PROVIDER_HF,
                        model=format_model,
                        label=f"{label} [{label_suffix}]",
                        usage=getattr(resp, "usage", None),
                    )
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    msg = str(exc).lower()
                    is_retryable = ("timeout" in msg) or ("timed out" in msg) or ("rate" in msg) or ("429" in msg)
                    if attempt < retries and is_retryable:
                        time.sleep(min(8, 2 ** attempt))
                        continue
                    break
            if last_exc is not None:
                raise last_exc
            return text

        try:
            text = _call_reasoner(geo_prompt, "geometry-pass", 360)
            raw_model_text = text
            payload = self._extract_reasoning_payload(text)
            if self._is_reasoning_payload_json_invalid(payload):
                json_retry_count = 1
                retry_prompt = self._build_geometry_json_retry_prompt(raw_item, text)
                try:
                    retry_text = _call_reasoner(retry_prompt, "geometry-pass-json-retry", 220)
                    raw_retry_text = retry_text
                    retry_payload = self._extract_reasoning_payload(retry_text)
                    if not self._is_reasoning_payload_json_invalid(retry_payload):
                        payload = retry_payload
                    else:
                        payload = self._build_local_reasoning_fallback(raw_item)
                except Exception:
                    payload = self._build_local_reasoning_fallback(raw_item)
            meta = payload.get("__meta", {}) if isinstance(payload, dict) else {}
            if not isinstance(meta, dict):
                meta = {}
            meta["raw_model_text"] = self._clip_debug_text(raw_model_text)
            meta["raw_retry_text"] = self._clip_debug_text(raw_retry_text)
            payload["__meta"] = meta
            payload = self._sanitize_reasoning_payload(payload)
            meta = payload.get("__meta", {}) if isinstance(payload, dict) else {}
            if not isinstance(meta, dict):
                meta = {}
            meta["retry_json_count"] = int(json_retry_count)
            payload["__meta"] = meta
            return payload
        except Exception as exc:
            payload = self._build_local_reasoning_fallback(raw_item)
            alerts = list(payload.get("alertas", []) or [])
            alerts.append(f"error_modelo:{exc}")
            payload["alertas"] = alerts
            meta = payload.get("__meta", {}) if isinstance(payload, dict) else {}
            if not isinstance(meta, dict):
                meta = {}
            meta["raw_model_text"] = self._clip_debug_text(raw_model_text)
            meta["raw_retry_text"] = self._clip_debug_text(raw_retry_text)
            payload["__meta"] = meta
            payload = self._sanitize_reasoning_payload(payload)
            meta = payload.get("__meta", {}) if isinstance(payload, dict) else {}
            if not isinstance(meta, dict):
                meta = {}
            meta["retry_json_count"] = int(json_retry_count)
            payload["__meta"] = meta
            return payload

    def _build_metadata_prompt(self, *, item_text: str, catalog: Dict[str, List[Dict[str, str]]]) -> str:
        catalog_block = self._build_catalog_prompt_block(catalog)
        return (
            "Clasifica este problema matematico y responde SOLO JSON valido.\n"
            'Formato exacto: {"curso":"","tema":"","subtema":""}\n'
            "Reglas:\n"
            "- Si no hay certeza, deja el campo vacio.\n"
            "- Prioriza valores del catalogo cuando coincidan.\n"
            "- No agregues explicaciones, markdown ni texto extra.\n\n"
            f"{catalog_block}\n"
            f"Problema:\n{item_text}\n"
        )

    def _clasificar_metadata_openai(
        self,
        client: OpenAI,
        *,
        model: str,
        timeout_s: int,
        retries: int,
        label: str,
        item_text: str,
        catalog: Dict[str, List[Dict[str, str]]],
    ) -> tuple[str, str, str]:
        prompt = self._build_metadata_prompt(item_text=item_text, catalog=catalog)
        text = ""
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                kwargs = {
                    "model": model,
                    "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
                }
                try:
                    resp = client.responses.create(**kwargs, timeout=timeout_s)
                except TypeError:
                    resp = client.responses.create(**kwargs)
                text = (resp.output_text or "").strip()
                self._log_usage(
                    provider=PROVIDER_OPENAI,
                    model=model,
                    label=f"{label} [meta-classify]",
                    usage=getattr(resp, "usage", None),
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                msg = str(exc).lower()
                is_retryable = ("timeout" in msg) or ("timed out" in msg) or ("rate" in msg) or ("429" in msg)
                if attempt < retries and is_retryable:
                    time.sleep(min(8, 2 ** attempt))
                    continue
                break
        if last_exc is not None:
            return ("", "", "")
        return self._extract_json_meta(text)

    def _clasificar_metadata_hf(
        self,
        *,
        model: str,
        timeout_s: int,
        retries: int,
        label: str,
        item_text: str,
        catalog: Dict[str, List[Dict[str, str]]],
    ) -> tuple[str, str, str]:
        token = self._resolve_hf_token()
        if not token:
            return ("", "", "")
        base_url = self._resolve_hf_base_url()
        client = OpenAI(base_url=base_url, api_key=token, timeout=timeout_s)
        prompt = self._build_metadata_prompt(item_text=item_text, catalog=catalog)

        text = ""
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
                    temperature=0,
                    max_tokens=220,
                )
                content = resp.choices[0].message.content if resp and resp.choices else ""
                text = self._extract_chat_text(content)
                self._log_usage(
                    provider=PROVIDER_HF,
                    model=model,
                    label=f"{label} [meta-classify]",
                    usage=getattr(resp, "usage", None),
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                msg = str(exc).lower()
                is_retryable = ("timeout" in msg) or ("timed out" in msg) or ("rate" in msg) or ("429" in msg)
                if attempt < retries and is_retryable:
                    time.sleep(min(8, 2 ** attempt))
                    continue
                break
        if last_exc is not None:
            return ("", "", "")
        return self._extract_json_meta(text)

    def _apply_metadata_mode(
        self,
        *,
        item: str,
        mode: str,
        provider: str,
        model: str,
        timeout_s: int,
        retries: int,
        label: str,
        catalog: Dict[str, List[Dict[str, str]]],
        openai_client: Optional[OpenAI] = None,
    ) -> str:
        existing = self._extract_existing_tags(item)
        manual = (
            (self.curso_var.get() or "").strip(),
            (self.tema_var.get() or "").strip(),
            (self.subtema_var.get() or "").strip(),
        )
        auto = ("", "", "")

        if mode in (TAG_MODE_AUTO, TAG_MODE_MIXED):
            if provider == PROVIDER_OPENAI and openai_client is not None:
                auto = self._clasificar_metadata_openai(
                    openai_client,
                    model=model,
                    timeout_s=timeout_s,
                    retries=retries,
                    label=label,
                    item_text=item,
                    catalog=catalog,
                )
            elif provider == PROVIDER_HF:
                auto = self._clasificar_metadata_hf(
                    model=model,
                    timeout_s=timeout_s,
                    retries=retries,
                    label=label,
                    item_text=item,
                    catalog=catalog,
                )
            else:
                self.after(
                    0,
                    lambda: self._log(
                        "Clasificacion Auto/Mixto requiere proveedor IA (OpenAI/HuggingFace). "
                        "Se aplican solo valores manuales/existentes."
                    ),
                )

        resolved = self._resolve_metadata_for_mode(mode=mode, existing=existing, manual=manual, auto=auto)
        resolved = self._canonicalize_with_catalog(
            curso=resolved[0],
            tema=resolved[1],
            subtema=resolved[2],
            catalog=catalog,
        )
        return self._inject_metadata_tags(item, curso=resolved[0], tema=resolved[1], subtema=resolved[2])

    def _extract_hf_image_to_text_output(self, out) -> str:
        if out is None:
            return ""
        if isinstance(out, str):
            return out.strip()
        if isinstance(out, dict):
            txt = out.get("generated_text") or out.get("text") or ""
            return str(txt).strip()
        txt = getattr(out, "generated_text", "") or getattr(out, "text", "")
        if txt:
            return str(txt).strip()
        return str(out).strip()

    def _transcribir_hf_native_endpoint(
        self,
        *,
        timeout_s: int,
        label: str,
        path: Path,
    ) -> str:
        token = self._resolve_hf_token()
        if not token:
            raise Exception("Falta HF token. Define HF_TOKEN en .env.local/.env o en el campo HF token.")
        endpoint_url = self._resolve_hf_endpoint_native_url()
        try:
            from huggingface_hub import InferenceClient  # type: ignore
        except Exception as exc:
            raise Exception(
                "Falta dependencia para endpoint dedicado de Hugging Face.\n"
                "Instala: python -m pip install huggingface_hub\n"
                f"Detalle: {exc}"
            )

        client = InferenceClient(model=endpoint_url, token=token, timeout=timeout_s)
        try:
            out = client.image_to_text(image=path)
            text = self._extract_hf_image_to_text_output(out)
            if not text:
                raise Exception("DeepSeek-OCR devolvio texto vacio.")
            self._ocr_raw_first_by_label[str(label)] = str(text or "")
            return text
        except Exception as exc:
            err = str(exc or "")
            low = err.lower()
            if "inference.endpoints.infer.write" in low:
                raise Exception(
                    "Token HF sin permiso para endpoint dedicado.\n"
                    "En Settings -> Access Tokens, habilita el permiso: inference.endpoints.infer.write"
                )
            raise Exception(f"Fallo endpoint dedicado HF ({label}): {err}")

    def _transcribir_huggingface(
        self,
        *,
        model: str,
        timeout_s: int,
        retries: int,
        label: str,
        path: Path,
    ) -> str:
        if self._use_hf_native_ocr_endpoint(model):
            return self._transcribir_hf_native_endpoint(timeout_s=timeout_s, label=label, path=path)

        token = self._resolve_hf_token()
        if not token:
            raise Exception("Falta HF token. Define HF_TOKEN en .env.local/.env o en el campo HF token.")

        base_url = self._resolve_hf_base_url()
        client = OpenAI(base_url=base_url, api_key=token, timeout=timeout_s)
        img_url = self._encode_image_data_url(path)

        prompt_candidates: List[str] = []
        if self._is_deepseek_ocr_model(model):
            prompt_candidates.append(self._build_deepseek_ocr_prompt())
            prompt_candidates.append(self._build_deepseek_ocr_prompt_compact())
        else:
            prompt_candidates.append(self._get_vision_ocr_prompt())
            prompt_candidates.append(
                self._get_vision_ocr_prompt()
                + "\nTu salida debe ser SOLO JSON valido con clave 'items'."
            )

        text = ""
        last_exc: Exception | None = None
        for pidx, prompt_text in enumerate(prompt_candidates, start=1):
            for attempt in range(retries + 1):
                try:
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt_text},
                                    {"type": "image_url", "image_url": {"url": img_url}},
                                ],
                            }
                        ],
                        temperature=0,
                        max_tokens=3200,
                    )
                    content = resp.choices[0].message.content if resp and resp.choices else ""
                    candidate_raw = self._extract_chat_text(content)
                    if candidate_raw.strip():
                        self._ocr_raw_first_by_label[str(label)] = str(candidate_raw or "")
                    candidate = self._strip_instruction_echo(candidate_raw)
                    self._log_usage(
                        provider=PROVIDER_HF,
                        model=model,
                        label=f"{label} [ocr-pass-{pidx}]",
                        usage=getattr(resp, "usage", None),
                    )
                    if candidate and not self._looks_like_instruction_echo(candidate) and not self._looks_like_repetitive_garbage(candidate):
                        text = candidate
                        last_exc = None
                        break
                    last_exc = Exception("Salida invalida: eco de instrucciones del prompt.")
                except Exception as exc:
                    last_exc = exc
                    msg = str(exc).lower()
                    is_retryable = ("timeout" in msg) or ("timed out" in msg) or ("rate" in msg) or ("429" in msg)
                    if attempt < retries and is_retryable:
                        wait_s = min(30, 2 ** attempt)
                        self.after(
                            0,
                            lambda l=label, a=attempt + 1, r=retries, w=wait_s: self._log(
                                f"HF retry {l} {a}/{r} en {w}s..."
                            ),
                        )
                        time.sleep(wait_s)
                        continue
                    break
            if text:
                break

        # DeepSeek OCR can occasionally return prompt-echo loops.
        # If that happens, fallback to a stable router vision model for this image.
        if (not text) and self._is_deepseek_ocr_model(model):
            fb_base = self._resolve_hf_fallback_base_url()
            fb_model = self._resolve_hf_fallback_model()
            self.after(
                0,
                lambda l=label, m=fb_model: self._log(
                    f"{l}: fallback OCR remoto -> {m}"
                ),
            )
            fb_client = OpenAI(base_url=fb_base, api_key=token, timeout=timeout_s)
            fb_prompt = (
                "Transcribe literalmente el contenido textual de la imagen en una sola linea. "
                "No expliques ni agregues instrucciones."
            )
            fb_exc: Exception | None = None
            for attempt in range(retries + 1):
                try:
                    resp = fb_client.chat.completions.create(
                        model=fb_model,
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": fb_prompt},
                                    {"type": "image_url", "image_url": {"url": img_url}},
                                ],
                            }
                        ],
                        temperature=0,
                        max_tokens=3200,
                    )
                    content = resp.choices[0].message.content if resp and resp.choices else ""
                    candidate_raw = self._extract_chat_text(content)
                    if candidate_raw.strip():
                        self._ocr_raw_first_by_label[str(label)] = str(candidate_raw or "")
                    candidate = candidate_raw.strip()
                    self._log_usage(
                        provider=PROVIDER_HF,
                        model=fb_model,
                        label=f"{label} [ocr-fallback-pass]",
                        usage=getattr(resp, "usage", None),
                    )
                    if candidate and not self._looks_like_instruction_echo(candidate) and not self._looks_like_repetitive_garbage(candidate):
                        text = candidate
                        fb_exc = None
                        break
                    fb_exc = Exception("Salida invalida en fallback remoto.")
                except Exception as exc:
                    fb_exc = exc
                    msg = str(exc).lower()
                    is_retryable = ("timeout" in msg) or ("timed out" in msg) or ("rate" in msg) or ("429" in msg)
                    if attempt < retries and is_retryable:
                        time.sleep(min(30, 2 ** attempt))
                        continue
                    break
            if not text and fb_exc is not None:
                last_exc = fb_exc

        if last_exc is not None or not text:
            err_text = str(last_exc or "")
            err_lower = err_text.lower()
            if "model_not_supported" in err_lower or "not a chat model" in err_lower:
                raise Exception(
                    "El modelo seleccionado no soporta chat.completions.\n"
                    "Selecciona un modelo Vision compatible del router de Hugging Face."
                )
            if "401" in err_text or "invalid username or password" in err_lower:
                raise Exception(
                    "HuggingFace 401: token invalido.\n"
                    "Genera un nuevo token `hf_...` y colocalo en HF_TOKEN."
                )
            if "403" in err_text and "inference providers" in err_lower:
                raise Exception(
                    "HuggingFace 403: token sin permiso para Inference Providers.\n"
                    "En Hugging Face -> Settings -> Access Tokens, habilita el permiso "
                    "'Make calls to Inference Providers' para ese token."
                )
            raise Exception(last_exc or "Sin texto de HuggingFace")
        return text

    def _transcribir_local_ocr(self, path: Path, *, label: Optional[str] = None) -> str:
        try:
            import pytesseract  # type: ignore
            from PIL import Image, ImageEnhance, ImageFilter, ImageOps  # type: ignore
        except Exception as exc:
            raise Exception(
                "Falta OCR local. Instala:\n"
                "1) python -m pip install pytesseract\n"
                "2) Tesseract OCR para Windows\n"
                f"Detalle: {exc}"
            )

        lang = (self.ocr_lang_var.get() or "spa+eng").strip() or "spa+eng"
        image = Image.open(path)
        # Pre-proceso basico para OCR: grayscale + contrast + denoise.
        image = ImageOps.grayscale(image)
        image = ImageOps.autocontrast(image)
        image = ImageEnhance.Contrast(image).enhance(1.8)
        image = image.filter(ImageFilter.MedianFilter(size=3))
        raw = pytesseract.image_to_string(image, lang=lang)
        text = " ".join((raw or "").replace("\r\n", "\n").split())
        if label is not None:
            self._ocr_raw_first_by_label[str(label)] = str(text or "")
        return text.strip()

    def _ingest_direct_ocr_output_for_label(
        self,
        *,
        label: str,
        path: Path,
        raw_output: str,
        image_index: int,
        provider: str,
        model: str,
        timeout_s: int,
        retries: int,
        tag_mode: str,
        catalog: Dict[str, Any],
        openai_client: Optional[OpenAI],
        run_id: str,
        bbox_norm: Optional[Tuple[float, float, float, float]],
        has_figure: bool,
        detector_source: str,
        segment_boxes: Optional[List[Tuple[int, int, int, int]]] = None,
    ) -> Tuple[int, int]:
        provider_key = "ocr"
        if provider == PROVIDER_OPENAI:
            provider_key = "openai"
        elif provider == PROVIDER_HF:
            provider_key = "hf"

        key_cls = classify_key_text(raw_output, path=path)
        if key_cls.is_key_image:
            self._direct_item_diagnostics_by_label[str(label)] = [
                {
                    "status": "SKIPPED_KEY",
                    "reason": key_cls.reason,
                    "confidence": key_cls.confidence,
                }
            ]
            self._log(
                f"{label}: imagen clasificada como CLAVES/RESPUESTAS (reason={key_cls.reason}, conf={key_cls.confidence:.2f}); se ignora."
            )
            return (0, 0)

        curso_now = (self.curso_var.get() or "").strip()
        tema_now = (self.tema_var.get() or "").strip()
        pipeline = ScanPipeline(
            provider=provider_key,
            model=model,
            max_retries=retries,
            parse_max_retries=retries,
            timeout_s=timeout_s,
            debug_dir=str(self._runs_dir / "scan_pipeline_debug"),
            ocr_lang=(self.ocr_lang_var.get() or "spa+eng"),
            temperature=0.0,
            top_p=1.0,
            max_tokens=3200,
            seed=42,
            strict_json=(provider_key in {"hf", "openai"}),
        )
        pipeline_run = pipeline.process_raw_output(
            raw_output=raw_output,
            image_path=path,
            start_n=max(1, int(image_index)),
            curso=curso_now,
            tema=tema_now,
            has_figure_hint=bool(has_figure),
        )
        if pipeline_run.json_parse_failed_count > 0:
            for parse_diag in pipeline_run.parse_failures:
                self._log(
                    f"{label}: parse JSON fallo tras {int(parse_diag.get('parse_retries_used', 0))} reintentos "
                    f"({', '.join(parse_diag.get('parse_errors', []))}); item marcado para revision."
                )
        pipeline_rows = list(pipeline_run.items)
        direct_items = [row.rendered for row in pipeline_rows]
        created_ok = 0
        created_err = 0
        label_key = str(label)
        source_key = self._seg_v2_source_key(path)
        source_stem = self._safe_fs_name(path.stem, "src")
        segmentation_reviewed = source_key in self._segmentation_reviewed_sources
        figure_confirmed = detector_source in {"manual", "manual_box", "manual_marker"}
        source_segment_boxes: List[Tuple[int, int, int, int]] = []
        for raw_box in list(segment_boxes or []):
            try:
                x1, y1, x2, y2 = [int(v) for v in raw_box]
            except Exception:
                continue
            if x2 <= x1 or y2 <= y1:
                continue
            source_segment_boxes.append((x1, y1, x2, y2))
        diagnostics: List[Dict[str, Any]] = []
        figure_positions = self._select_direct_figure_item_positions(
            direct_items=direct_items,
            has_figure=has_figure,
        )
        segment_plan: Dict[int, int] = {}
        if direct_items and source_segment_boxes:
            segment_plan = self._plan_image_tag_positions(
                raw_items=direct_items,
                segment_count=len(source_segment_boxes),
            )
            if not segment_plan:
                # Fallback deterministico: mapear por orden item->segmento.
                limit = min(len(direct_items), len(source_segment_boxes))
                for pos in range(1, limit + 1):
                    segment_plan[pos] = pos - 1
                if segment_plan:
                    self._log(
                        f"{label}: asignacion item->segmento aplicada por orden ({len(segment_plan)} mapeo(s))."
                    )
        source_bindings = self._get_segment_bindings_by_source_key(source_key)

        if not direct_items:
            fallback_num = self._infer_numero(path, image_index)
            draft = self._build_direct_error_draft(
                raw_item=raw_output,
                fallback_numero=fallback_num,
            )
            if has_figure and not self._has_image_marker(draft):
                draft = self._insert_image_marker(draft, path=path, numero=fallback_num)
                draft = self._move_image_marker_before_options(draft)

            draft = self._apply_metadata_mode(
                item=draft,
                mode=tag_mode,
                provider=provider,
                model=model,
                timeout_s=timeout_s,
                retries=retries,
                label=f"{label}#direct-fallback",
                catalog=catalog,
                openai_client=openai_client if provider == PROVIDER_OPENAI else None,
            )
            draft = TAG_SUBTEMA_RE.sub(" ", draft)
            draft = self._move_image_marker_before_options(self.controller.normalizar_item_una_linea(draft))

            image_paths: List[str] = []
            marker_name = self._extract_first_image_marker_name(draft)
            fallback_bbox_norm = bbox_norm
            fallback_bbox_source = detector_source
            if fallback_bbox_norm is None and source_segment_boxes:
                fallback_bbox_norm = self._box_px_to_norm(path=path, box_px=source_segment_boxes[0])
                if fallback_bbox_norm is not None:
                    fallback_bbox_source = "segment_item"
            bbox_norm_payload: Optional[List[float]] = None
            if fallback_bbox_norm is not None:
                try:
                    bbox_norm_payload = [float(v) for v in fallback_bbox_norm]
                except Exception:
                    bbox_norm_payload = None
            if marker_name and fallback_bbox_norm is not None and self.auto_crop_var.get():
                crop_saved = self._save_figure_crop(
                    image_path=path,
                    marker_name=marker_name,
                    bbox_norm=fallback_bbox_norm,
                )
                if crop_saved:
                    image_paths = [crop_saved]
                    self._preview_images[marker_name] = crop_saved

            self._items.append((path.stem, draft, image_paths))
            created_err += 1
            num = self.controller.parsear_numero_original(draft) or fallback_num
            draft_structured = self._build_structured_input_from_scan_item(draft)
            key = self._build_training_pair_key(
                num,
                source_stem=source_stem,
                run_id=run_id,
                input_structured=draft_structured,
            )
            self._upsert_training_pair(
                key,
                {
                    "input_structured": draft_structured,
                    "model_raw_output": str(raw_output or ""),
                    "model_validated_output": "",
                    "human_final_output": draft,
                    "status": "ERROR_FORMATO_DIRECTO",
                    "prompt_version": self._vision_direct_prompt_version,
                    "run_id": run_id,
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "source_labels": [label_key],
                    "errors": ["sin_item_scan_directo"],
                    "provider": provider,
                    "model": model,
                    "detector_source": detector_source,
                    "metadata": {
                        "item_num": int(num),
                        "source_stem": source_stem,
                        "source_path": str(path),
                        "detector_source": detector_source,
                        "has_figure": bool(has_figure),
                        "figure_confirmed": bool(figure_confirmed),
                        "figure_bbox_source": fallback_bbox_source,
                        "figure_bbox_norm": bbox_norm_payload,
                        "segmentation_reviewed": bool(segmentation_reviewed),
                        "segment_idx": None,
                        "slot": "ENUNCIADO",
                        "marker_name": marker_name,
                        "binding_confirmed": False,
                        "binding_source": "",
                    },
                },
            )
            diagnostics.append(
                {
                    "item_num": int(num),
                    "status": "ERROR_FORMATO_DIRECTO",
                    "errors": ["sin_item_scan_directo"],
                }
            )
            self._log(f"{label}: ERROR_FORMATO_DIRECTO (sin \\item valido).")
        else:
            for pos, raw_item in enumerate(direct_items, start=1):
                pipeline_row = pipeline_rows[pos - 1] if (pos - 1) < len(pipeline_rows) else None
                candidate = self.controller.normalizar_item_una_linea(self._decode_scan_escapes(raw_item or ""))
                ok, errors = self._validate_direct_scan_item(candidate)
                if pipeline_row is not None:
                    for err in pipeline_row.errors:
                        if err not in errors:
                            errors.append(err)
                base_num = (
                    int(pipeline_row.item.n)
                    if pipeline_row is not None
                    else (
                        self.controller.parsear_numero_original(candidate)
                        or self._infer_numero(path, image_index + pos - 1)
                    )
                )
                working = candidate
                seg_idx = segment_plan.get(pos, -1)
                item_bbox_norm: Optional[Tuple[float, float, float, float]] = None
                if 0 <= seg_idx < len(source_segment_boxes):
                    item_bbox_norm = self._box_px_to_norm(path=path, box_px=source_segment_boxes[seg_idx])
                pre_binding = source_bindings.get(int(seg_idx)) if seg_idx >= 0 else None
                has_confirmed_pre_binding = isinstance(pre_binding, dict) and self._as_bool(pre_binding.get("confirmed"), False)
                if not ok and pipeline_row is None:
                    working = self._build_direct_error_draft(
                        raw_item=raw_item,
                        fallback_numero=base_num,
                    )

                if pos in figure_positions and (not has_confirmed_pre_binding) and not self._has_image_marker(working):
                    working = self._insert_image_marker(working, path=path, numero=base_num)
                    working = self._move_image_marker_before_options(working)

                working = self._apply_metadata_mode(
                    item=working,
                    mode=tag_mode,
                    provider=provider,
                    model=model,
                    timeout_s=timeout_s,
                    retries=retries,
                    label=f"{label}#direct-{pos}",
                    catalog=catalog,
                    openai_client=openai_client if provider == PROVIDER_OPENAI else None,
                )
                working = TAG_SUBTEMA_RE.sub(" ", working)
                working = self._move_image_marker_before_options(
                    self.controller.normalizar_item_una_linea(self._normalize_math_delimiters(working))
                )
                item_num = self.controller.parsear_numero_original(working) or base_num
                binding_entry: Optional[Dict[str, Any]] = None
                binding_slot = "ENUNCIADO"
                binding_marker = ""
                binding_crop_path = ""
                if seg_idx >= 0:
                    payload = source_bindings.get(int(seg_idx))
                    if isinstance(payload, dict) and self._as_bool(payload.get("confirmed"), False):
                        binding_entry = dict(payload)
                        binding_slot = self._normalize_binding_slot(str(binding_entry.get("slot", "ENUNCIADO") or "ENUNCIADO"))
                        expected_marker = self._build_binding_marker_name(item_num=item_num, slot=binding_slot)
                        binding_marker = expected_marker
                        binding_crop_path = str(binding_entry.get("crop_path", "") or "").strip()
                        if (
                            int(self._safe_int(binding_entry.get("item_num", 0), 0)) != int(item_num)
                            or str(binding_entry.get("marker_name", "") or "").strip() != expected_marker
                        ):
                            updated_binding = dict(binding_entry)
                            updated_binding["item_num"] = int(item_num)
                            updated_binding["marker_name"] = expected_marker
                            updated_binding["updated_at"] = datetime.now().isoformat(timespec="seconds")
                            source_bindings[int(seg_idx)] = updated_binding
                            self._set_segment_bindings_by_source_key(source_key, source_bindings)
                            self._log(
                                f"binding_replaced source={path.name} seg={int(seg_idx)} -> item={item_num} slot={binding_slot} marker={expected_marker}"
                            )
                        working = self._insert_explicit_marker_in_slot(
                            text=working,
                            marker_name=expected_marker,
                            slot=binding_slot,
                            path=path,
                            numero=item_num,
                        )
                        working = self._normalize_math_delimiters(self.controller.normalizar_item_una_linea(working))

                image_paths: List[str] = []
                marker_name = self._extract_first_image_marker_name(working)
                if seg_idx < 0 and marker_name and source_segment_boxes:
                    guess = min(max(pos - 1, 0), len(source_segment_boxes) - 1)
                    item_bbox_norm = self._box_px_to_norm(path=path, box_px=source_segment_boxes[guess])
                crop_bbox_norm = item_bbox_norm if item_bbox_norm is not None else bbox_norm
                bbox_source = "segment_item" if item_bbox_norm is not None else detector_source
                if binding_entry is not None:
                    bbox_source = "manual_segment_binding"
                bbox_norm_payload: Optional[List[float]] = None
                if crop_bbox_norm is not None:
                    try:
                        bbox_norm_payload = [float(v) for v in crop_bbox_norm]
                    except Exception:
                        bbox_norm_payload = None
                if binding_entry is not None and binding_marker and self._is_valid_image_path(binding_crop_path):
                    image_paths = [binding_crop_path]
                    self._preview_images[binding_marker] = binding_crop_path
                    marker_name = binding_marker
                elif marker_name and crop_bbox_norm is not None and self.auto_crop_var.get() and (pos in figure_positions):
                    crop_saved = self._save_figure_crop(
                        image_path=path,
                        marker_name=marker_name,
                        bbox_norm=crop_bbox_norm,
                    )
                    if crop_saved:
                        image_paths = [crop_saved]
                        self._preview_images[marker_name] = crop_saved

                self._items.append((path.stem, working, image_paths))
                needs_review_item = bool(pipeline_row.item.needs_review) if pipeline_row is not None else False
                retry_count = int(pipeline_row.retries_used) if pipeline_row is not None else 0
                if needs_review_item:
                    status = "NEEDS_REVIEW"
                    created_err += 1
                elif ok:
                    status = "OK_DIRECTO"
                    created_ok += 1
                else:
                    status = "ERROR_FORMATO_DIRECTO"
                    created_err += 1
                    self._log(
                        f"{label}: ERROR_FORMATO_DIRECTO item {item_num} -> {', '.join(errors) if errors else 'validacion_fallida'}"
                    )

                structured_input = self._build_structured_input_from_scan_item(working)
                key = self._build_training_pair_key(
                    item_num,
                    source_stem=source_stem,
                    run_id=run_id,
                    input_structured=structured_input,
                )
                self._upsert_training_pair(
                    key,
                    {
                        "input_structured": structured_input,
                        "model_raw_output": str(raw_item or ""),
                        "model_validated_output": working if status in {"OK_DIRECTO", "NEEDS_REVIEW"} else "",
                        "human_final_output": working,
                        "status": status,
                        "prompt_version": self._vision_direct_prompt_version,
                        "run_id": run_id,
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                        "source_labels": [label_key],
                        "errors": list(errors),
                        "provider": provider,
                        "model": model,
                        "detector_source": detector_source,
                        "metadata": {
                            "item_num": int(item_num),
                            "source_stem": source_stem,
                            "source_path": str(path),
                            "detector_source": detector_source,
                            "has_figure": bool(has_figure),
                            "position_in_image": pos,
                            "figure_confirmed": bool(figure_confirmed),
                            "figure_bbox_source": bbox_source,
                            "figure_bbox_norm": bbox_norm_payload,
                            "segmentation_reviewed": bool(segmentation_reviewed),
                            "segment_idx": int(seg_idx) if seg_idx >= 0 else None,
                            "slot": binding_slot if binding_entry is not None else "ENUNCIADO",
                            "marker_name": binding_marker if binding_entry is not None else marker_name,
                            "binding_confirmed": bool(binding_entry is not None),
                            "binding_source": "manual_segment_binding" if binding_entry is not None else "",
                            "needs_review": bool(needs_review_item),
                            "retry_count": int(retry_count),
                        },
                    },
                )
                diagnostics.append(
                    {
                        "item_num": int(item_num),
                        "status": status,
                        "errors": list(errors),
                        "needs_review": bool(needs_review_item),
                        "retry_count": int(retry_count),
                    }
                )

        self._direct_item_diagnostics_by_label[label_key] = diagnostics
        return (created_ok, created_err)

    def _transcribir_async_raw_only(self) -> None:
        if self._transcribing:
            messagebox.showinfo("Transcripcion", "Ya hay una transcripcion en curso.")
            return

        # Paso 2 en modo vision-directa: OCR crudo + validacion de formato por item.
        self.provider_var.set(FIXED_PROVIDER)
        current_model = (self.model_var.get() or "").strip()
        if current_model not in HF_MODELS:
            self.model_var.set(DEFAULT_HF_VISION_MODEL)
        self.segmentation_v2_var.set(True)
        self.ocr_exclude_box_var.set(True)

        total_list = self.list_files.size()
        selected_indices = list(self.list_files.curselection())
        if selected_indices:
            sel = [self.list_files.get(i) for i in selected_indices]
            selected_scope: Optional[Set[str]] = set(sel)
        else:
            sel = [self.list_files.get(i) for i in range(total_list)]
            selected_scope = None
        if not sel:
            messagebox.showwarning("Imagenes", "Agrega al menos una imagen.")
            return

        keep_state = messagebox.askyesnocancel(
            "Paso 2 - estado previo",
            "¿Conservar estado previo?\n\n"
            "Si = conservar salida/items previos\n"
            "No = limpiar salida/items previos\n"
            "Cancelar = no ejecutar",
            default=messagebox.YES,
        )
        if keep_state is None:
            return
        if keep_state is False:
            self._items.clear()
            self._training_pairs_by_item.clear()
            self._direct_item_diagnostics_by_label.clear()
            self._render_output_from_items()
            self._log("Paso 2: estado previo limpiado (_items/salida/training_pairs).")
        else:
            self._log("Paso 2: estado previo conservado (_items/salida).")

        if selected_scope is not None:
            self._log(f"Paso 2 (vision directa): seleccion actual ({len(sel)} imagen(es)).")
        else:
            self._log(f"Paso 2 (vision directa): lote completo ({len(sel)} imagen(es)).")

        if not self._refresh_segmentation_done_state(selected_labels=selected_scope):
            reviewed, total_review, pending = self._segmentation_progress(selected_labels=selected_scope)
            self._log(f"Paso 2 bloqueado: completa Paso 1 en el alcance actual ({reviewed}/{total_review}).")
            if pending:
                preview = ", ".join(pending[:3])
                extra = "" if len(pending) <= 3 else f" (+{len(pending)-3} mas)"
                self._log(f"Pendientes: {preview}{extra}")
            messagebox.showwarning(
                "Paso 2 bloqueado",
                "Completa la segmentacion (Paso 1) antes de ejecutar OCR directo.",
            )
            return
        else:
            reviewed, total_review, _pending = self._segmentation_progress(selected_labels=selected_scope)
            self._log(f"Segmentacion completa: {reviewed}/{total_review}.")

        provider = FIXED_PROVIDER
        model = (self.model_var.get() or "").strip() or DEFAULT_HF_VISION_MODEL
        detect_model = self._resolve_bbox_detector_model_path() or "segmentacion_v2_fallback"
        try:
            provider_key = "hf" if provider == PROVIDER_HF else ("openai" if provider == PROVIDER_OPENAI else "ocr")
            validate_scan_provider_env(provider_key)
        except Exception as exc:
            messagebox.showerror("ENV", str(exc))
            return
        try:
            timeout_s = int(self.timeout_var.get())
        except Exception:
            timeout_s = 180
        timeout_s = max(30, min(timeout_s, 600))
        try:
            retries = int(self.retries_var.get())
        except Exception:
            retries = 2
        retries = max(0, min(retries, 5))
        tag_mode = (self.tag_mode_var.get() or FIXED_TAG_MODE).strip()
        db_name = (self.db_name_var.get() or "").strip()
        catalog = self._get_catalog(db_name)
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]

        review_sources = self._collect_review_sources(selected_labels=selected_scope)
        if review_sources:
            paths = [(label, src) for (_idx, label, src) in review_sources]
        else:
            paths = [(label, self._file_map[label]) for label in sel if label in self._file_map]
        original_count = len(paths)
        self._log(f"Segmentos V2 se guardan en: {self._runs_dir / 'v2_segments'}")
        source_segment_boxes: Dict[str, List[Tuple[int, int, int, int]]] = {}
        total_segments = 0
        for _label, src in paths:
            try:
                key = self._seg_v2_source_key(src)
                segments = self._get_segments_v2_for_source(src)
                boxes = [tuple(int(v) for v in seg.bbox) for seg in segments]
                source_segment_boxes[key] = boxes
                total_segments += len(boxes)
            except Exception:
                continue
        self._log(
            f"Segmentacion V2 lista: {total_segments} segmento(s) guardados desde {original_count} imagen(es)."
        )

        self._progress_start(len(paths))
        self._set_loading_bar(True, "Estado: preparando OCR...")
        self._transcribing = True
        try:
            self.btn_transcribir.configure(state="disabled")
        except Exception:
            pass
        run_ctx: Dict[str, Optional[Path]] = {"path": None}

        def worker() -> None:
            client = None
            if provider == PROVIDER_OPENAI:
                try:
                    try:
                        client = OpenAI(timeout=timeout_s)
                    except TypeError:
                        client = OpenAI()
                except Exception as exc:
                    self.after(0, lambda: messagebox.showerror("OpenAI", str(exc)))
                    return

            skip_done = bool(self.skip_done_var.get())
            to_process = [(label, path) for (label, path) in paths if not (skip_done and label in self._transcribed_by_label)]
            total = len(to_process)
            if total == 0:
                self.after(0, lambda: self._progress_start(0))
                self.after(0, lambda: self._set_loading_bar(False, "Estado: inactivo"))
                self.after(
                    0,
                    lambda sd=skip_done: messagebox.showinfo(
                        "Transcripcion",
                        "No hay nuevas imagenes para transcribir.\n"
                        + ("(Tip: desmarca 'Omitir ya transcritos' para reprocesar.)" if sd else ""),
                    ),
                )
                return

            self.after(0, lambda t=total: self._progress_start(t))
            self.after(0, lambda t=total: self._set_loading_bar(True, f"Estado: OCR crudo ({t} imagen(es))..."))
            run_path = self._new_run_log_path(
                provider=provider,
                model=model,
                detect_model=detect_model,
                total=total,
            )
            run_ctx["path"] = run_path
            self.after(
                0,
                lambda t=total, dm=detect_model, rp=str(run_path): self._log(
                    f"Lote OCR crudo iniciado: {t} imagen(es) | detector={dm} | ocr_masking=off | segmentation_active=true | log={rp}"
                ),
            )
            self._append_run_event(
                run_path,
                {
                    "event": "run_started",
                    "mode": "raw_only",
                    "run_id": run_id,
                    "total": total,
                    "provider": provider,
                    "model": model,
                    "ocr_masking": False,
                    "segmentation_active": True,
                },
            )

            ok_count = 0
            err_count = 0
            ai_fallback_to_ocr = False

            for idx, (label, path) in enumerate(to_process, start=1):
                self._ocr_merge_applied_by_label.pop(str(label), None)
                self._direct_item_diagnostics_by_label.pop(str(label), None)
                effective_provider = provider
                if ai_fallback_to_ocr and provider in (PROVIDER_OPENAI, PROVIDER_HF):
                    effective_provider = PROVIDER_OCR

                key = self._seg_v2_source_key(path)
                segment_boxes = list(source_segment_boxes.get(key, []))
                self.after(
                    0,
                    lambda l=label, n=len(segment_boxes): self._log(
                        f"{l}: segmentos detectados={n} (solo guardado/etiquetado; OCR masking OFF)."
                    ),
                )
                bbox_norm: Optional[Tuple[float, float, float, float]] = None
                bbox_conf = 0.0
                has_figure = False
                detector_source = "none"
                if self.detect_figure_var.get():
                    bbox_norm, bbox_conf, has_figure, detector_source = self._detect_figure_bbox_separate(
                        path=path,
                        segment_boxes=segment_boxes,
                    )
                    self.after(
                        0,
                        lambda l=label, h=has_figure, c=bbox_conf, s=detector_source: self._log(
                            f"{l}: detector_bbox={s} has_figure={h} conf={c:.2f}"
                        ),
                    )

                self.after(
                    0,
                    lambda l=label, p=effective_provider, i=idx, t=total: self._log(
                        f"[{i}/{t}] OCR crudo ({p}): {l}"
                    ),
                )
                self._append_run_event(
                    run_path,
                    {
                        "event": "image_started",
                        "mode": "raw_only",
                        "idx": idx,
                        "total": total,
                        "label": label,
                        "path": str(path),
                        "provider": effective_provider,
                        "ocr_masking": False,
                        "segmentation_active": True,
                        "segment_count": len(segment_boxes),
                    },
                )

                key_img_cls = classify_key_image(
                    path=path,
                    text_hint="",
                    ocr_lang=(self.ocr_lang_var.get() or "spa+eng"),
                )
                if key_img_cls.is_key_image:
                    self._direct_item_diagnostics_by_label[str(label)] = [
                        {
                            "status": "SKIPPED_KEY",
                            "reason": key_img_cls.reason,
                            "confidence": key_img_cls.confidence,
                        }
                    ]
                    self._transcribed_by_label[str(label)] = "SKIPPED_KEY"
                    self._set_merge_notes_for_label(str(label), [])
                    self.after(0, self._refresh_image_list_colors)
                    self.after(
                        0,
                        lambda l=label, r=key_img_cls.reason, c=key_img_cls.confidence: self._log(
                            f"{l}: imagen de claves/respuestas detectada antes de extraer (reason={r}, conf={c:.2f}); se ignora."
                        ),
                    )
                    self._append_run_event(
                        run_path,
                        {
                            "event": "image_skipped_key",
                            "mode": "raw_only",
                            "idx": idx,
                            "label": label,
                            "reason": key_img_cls.reason,
                            "confidence": key_img_cls.confidence,
                        },
                    )
                    self.after(
                        0,
                        lambda i=idx, t=total, n=len(self._items), l=label: self._progress_update(
                            i, t, n, current_label=l
                        ),
                    )
                    continue

                try:
                    if effective_provider == PROVIDER_OPENAI:
                        _ = self._transcribir_openai(
                            client,  # type: ignore[arg-type]
                            model=model,
                            timeout_s=timeout_s,
                            retries=retries,
                            label=label,
                            path=path,
                        )
                    elif effective_provider == PROVIDER_HF:
                        _ = self._transcribir_huggingface(
                            model=model,
                            timeout_s=timeout_s,
                            retries=retries,
                            label=label,
                            path=path,
                        )
                    else:
                        _ = self._transcribir_local_ocr(path, label=label)
                except Exception as trans_exc:
                    if effective_provider in (PROVIDER_OPENAI, PROVIDER_HF):
                        reason = str(trans_exc)
                        recovered_with_model_fallback = False
                        # Auto fallback de modelo HF cuando el seleccionado no existe/soporta.
                        if (
                            effective_provider == PROVIDER_HF
                            and self._is_hf_model_unavailable_error(trans_exc)
                            and model != DEFAULT_HF_VISION_MODEL
                        ):
                            try:
                                self.after(
                                    0,
                                    lambda l=label, m=model, fb=DEFAULT_HF_VISION_MODEL: self._log(
                                        f"{l}: modelo HF no disponible ({m}). Reintento automatico con {fb}."
                                    ),
                                )
                                _ = self._transcribir_huggingface(
                                    model=DEFAULT_HF_VISION_MODEL,
                                    timeout_s=timeout_s,
                                    retries=retries,
                                    label=label,
                                    path=path,
                                )
                                self.after(
                                    0,
                                    lambda l=label, fb=DEFAULT_HF_VISION_MODEL: self._log(
                                        f"{l}: OCR crudo registrado con fallback de modelo ({fb})."
                                    ),
                                )
                                effective_provider = PROVIDER_HF
                                recovered_with_model_fallback = True
                            except Exception as fallback_model_exc:
                                reason = f"{reason} | fallback_model={fallback_model_exc}"
                                self._ocr_raw_first_by_label[str(label)] = f"[ERROR_IA] {reason}"
                                err_count += 1
                                continue
                        if recovered_with_model_fallback:
                            pass
                        elif self._is_credit_depleted_error(trans_exc):
                            self.after(
                                0,
                                lambda l=label: self._log(
                                    f"{l}: credito IA agotado (402). Se registra error y se continua con la siguiente imagen."
                                ),
                            )
                        else:
                            self.after(
                                0,
                                lambda l=label, r=reason: self._log(
                                    f"{l}: fallo IA ({r})."
                                ),
                            )
                        if not recovered_with_model_fallback:
                            self._append_run_event(
                                run_path,
                                {
                                    "event": "provider_fallback",
                                    "mode": "raw_only",
                                    "idx": idx,
                                    "label": label,
                                    "from": effective_provider,
                                    "to": "none",
                                    "reason": reason,
                                },
                            )
                            self._ocr_raw_first_by_label[str(label)] = f"[ERROR_IA] {reason}"
                            err_count += 1
                            continue
                    else:
                        self._ocr_raw_first_by_label[str(label)] = f"[ERROR] {trans_exc}"
                        self.after(0, lambda e=trans_exc, l=label: self._log(f"Error transcripcion {l}: {e}"))
                        self._append_run_event(
                            run_path,
                            {
                                "event": "image_error",
                                "mode": "raw_only",
                                "idx": idx,
                                "label": label,
                                "stage": "transcription",
                                "error": str(trans_exc),
                            },
                        )
                        err_count += 1
                        continue

                if not str(self._ocr_raw_first_by_label.get(str(label), "") or "").strip():
                    self._ocr_raw_first_by_label[str(label)] = "[SIN TEXTO] El modelo devolvio salida vacia."
                raw_for_label = str(self._ocr_raw_first_by_label.get(str(label), "") or "")
                ok_items, err_items = self._ingest_direct_ocr_output_for_label(
                    label=str(label),
                    path=path,
                    raw_output=raw_for_label,
                    image_index=idx,
                    provider=effective_provider,
                    model=model,
                    timeout_s=timeout_s,
                    retries=retries,
                    tag_mode=tag_mode,
                    catalog=catalog,
                    openai_client=client if effective_provider == PROVIDER_OPENAI else None,  # type: ignore[arg-type]
                    run_id=run_id,
                    bbox_norm=bbox_norm,
                    has_figure=has_figure,
                    detector_source=detector_source,
                    segment_boxes=segment_boxes,
                )
                label_diagnostics = list(self._direct_item_diagnostics_by_label.get(str(label), []) or [])
                skipped_key = any(str(d.get("status", "")).upper() == "SKIPPED_KEY" for d in label_diagnostics)
                self._transcribed_by_label[str(label)] = "RAW_OK"
                self._set_merge_notes_for_label(str(label), [])
                self.after(0, self._refresh_image_list_colors)
                self.after(0, self._render_output_from_items)
                self.after(
                    0,
                    lambda i=idx, t=total, n=len(self._items), l=label: self._progress_update(
                        i, t, n, current_label=l
                    ),
                )
                self.after(
                    0,
                    lambda l=label, o=ok_items, e=err_items, sk=skipped_key: self._log(
                        f"{l}: OCR crudo registrado. items_directos_ok={o}, items_error_formato={e}, skipped_key={sk}"
                    ),
                )
                self._append_run_event(
                    run_path,
                    {
                        "event": "image_completed",
                        "mode": "raw_only",
                        "run_id": run_id,
                        "idx": idx,
                        "label": label,
                        "items_ok_direct": ok_items,
                        "items_error_formato": err_items,
                        "detector_source": detector_source,
                        "detector_has_figure": bool(has_figure),
                        "skipped_key": bool(skipped_key),
                    },
                )
                if not skipped_key:
                    ok_count += 1

            self.after(0, self._refresh_image_list_colors)
            self.after(
                0,
                lambda o=ok_count, e=err_count, t=total: self._log(
                    f"OCR crudo completado. Lote={t}, OK={o}, Errores={e}."
                ),
            )
            self.after(
                0,
                lambda o=ok_count, e=err_count, t=total: self._progress_finish(
                    ok=o, errors=e, total_images=t, detected_items=len(self._items)
                ),
            )
            self._append_run_event(
                run_path,
                {
                    "event": "run_completed",
                    "mode": "raw_only",
                    "run_id": run_id,
                    "total": total,
                    "ok": ok_count,
                    "errors": err_count,
                    "items_total_estado": len(self._items),
                },
            )

        def safe_worker() -> None:
            try:
                worker()
            except Exception as exc:
                self.after(0, lambda e=exc: self._log(f"Fallo inesperado del lote OCR crudo: {e}"))
                if run_ctx.get("path") is not None:
                    try:
                        self._append_run_event(
                            run_ctx["path"],  # type: ignore[arg-type]
                            {
                                "event": "run_fatal_error",
                                "mode": "raw_only",
                                "error": str(exc),
                                "traceback": traceback.format_exc(),
                            },
                        )
                    except Exception:
                        pass
            finally:
                def _unlock() -> None:
                    self._transcribing = False
                    try:
                        self.btn_transcribir.configure(state="normal")
                    except Exception:
                        pass
                    self._set_loading_bar(False, "Estado: inactivo")

                self.after(0, _unlock)

        threading.Thread(target=safe_worker, daemon=True).start()

    def _transcribir_async(self) -> None:
        # Modo actual: vision directa + validacion/formato por item.
        self._transcribir_async_raw_only()
        return

        if self._transcribing:
            messagebox.showinfo("Transcripcion", "Ya hay una transcripcion en curso.")
            return
        # Ruta fija de procesamiento (sin toggles de flujo).
        self.provider_var.set(FIXED_PROVIDER)
        current_model = (self.model_var.get() or "").strip()
        if current_model not in HF_MODELS:
            self.model_var.set(DEFAULT_HF_VISION_MODEL)
        current_format = (self.format_model_var.get() or "").strip()
        if current_format not in HF_FORMAT_MODELS:
            self.format_model_var.set(DEFAULT_HF_FORMAT_MODEL)
        current_tag = (self.tag_mode_var.get() or "").strip()
        if current_tag not in {TAG_MODE_MANUAL, TAG_MODE_AUTO, TAG_MODE_MIXED}:
            self.tag_mode_var.set(FIXED_TAG_MODE)
        self.auto_format_var.set(True)
        self.format_with_llm_var.set(True)
        self.auto_crop_var.set(True)
        self.detect_figure_var.set(True)
        self.segmentation_v2_var.set(True)
        self.ocr_exclude_box_var.set(True)
        # Regla operativa: las pasadas IA (1 y 2) se ejecutan sobre OCR unificado.
        apply_llm_after_unified = True
        total_list = self.list_files.size()
        selected_indices = list(self.list_files.curselection())
        if selected_indices:
            sel = [self.list_files.get(i) for i in selected_indices]
            selected_scope: Optional[Set[str]] = set(sel)
        else:
            sel = [self.list_files.get(i) for i in range(total_list)]
            selected_scope = None
        if not sel:
            messagebox.showwarning("Imagenes", "Agrega al menos una imagen.")
            return
        if selected_scope is not None:
            self._log(f"Paso 2: OCR directo sobre seleccion actual ({len(sel)} imagen(es)).")
        else:
            self._log(f"Paso 2: OCR directo sobre lote completo ({len(sel)} imagen(es)).")

        if not self._refresh_segmentation_done_state(selected_labels=selected_scope):
            reviewed, total_review, pending = self._segmentation_progress(selected_labels=selected_scope)
            self._log(
                "Aviso: segmentacion no completada en el alcance actual. "
                "Paso 2 continuara con los cuadros/overrides actualmente guardados."
            )
            self._log(f"Segmentacion progreso actual: {reviewed}/{total_review}.")
            if pending:
                preview = ", ".join(pending[:3])
                extra = "" if len(pending) <= 3 else f" (+{len(pending)-3} mas)"
                self._log(f"Pendientes: {preview}{extra}")
        else:
            reviewed, total_review, _pending = self._segmentation_progress(selected_labels=selected_scope)
            self._log(f"Segmentacion completa: {reviewed}/{total_review}.")

        provider = FIXED_PROVIDER
        model = (self.model_var.get() or "").strip() or DEFAULT_HF_VISION_MODEL
        detect_model = self._resolve_detect_model(provider, model)
        hf_native_ocr_mode = False
        tag_mode = (self.tag_mode_var.get() or FIXED_TAG_MODE).strip()
        db_name = (self.db_name_var.get() or "").strip()
        try:
            timeout_s = int(self.timeout_var.get())
        except Exception:
            timeout_s = 180
        timeout_s = max(30, min(timeout_s, 600))
        try:
            retries = int(self.retries_var.get())
        except Exception:
            retries = 2
        retries = max(0, min(retries, 5))
        # Build OCR input only from base source images (not already-segmented temp PNGs).
        review_sources = self._collect_review_sources(selected_labels=selected_scope)
        if review_sources:
            paths = [(label, src) for (_idx, label, src) in review_sources]
        else:
            paths = [(label, self._file_map[label]) for label in sel if label in self._file_map]
        original_count = len(paths)
        self._log(f"Segmentos V2 se guardan en: {self._runs_dir / 'v2_segments'}")
        source_segment_boxes: Dict[str, List[Tuple[int, int, int, int]]] = {}
        total_segments = 0
        for _label, src in paths:
            try:
                key = self._seg_v2_source_key(src)
                segments = self._get_segments_v2_for_source(src)
                boxes = [tuple(int(v) for v in seg.bbox) for seg in segments]
                source_segment_boxes[key] = boxes
                total_segments += len(boxes)
            except Exception:
                continue
        self._log(
            f"Segmentacion V2 lista: {total_segments} segmento(s) guardados desde {original_count} imagen(es)."
        )

        self._progress_start(len(paths))
        self._set_loading_bar(True, "Estado: preparando OCR...")
        self._transcribing = True
        try:
            self.btn_transcribir.configure(state="disabled")
        except Exception:
            pass
        # No borrar la salida: queremos acumular items para poder exportar un archivo final.
        run_ctx: Dict[str, Optional[Path]] = {"path": None}

        def worker():
            client = None
            if provider == PROVIDER_OPENAI:
                try:
                    try:
                        client = OpenAI(timeout=timeout_s)
                    except TypeError:
                        client = OpenAI()
                except Exception as exc:
                    self.after(0, lambda: messagebox.showerror("OpenAI", str(exc)))
                    return
            catalog = self._get_catalog(db_name) if db_name else {"areas": [], "temas": [], "subtemas": []}

            skip_done = bool(self.skip_done_var.get())
            to_process = [(label, path) for (label, path) in paths if not (skip_done and label in self._transcribed_by_label)]
            total = len(to_process)
            if total == 0:
                self.after(0, lambda: self._progress_start(0))
                self.after(0, lambda: self._set_loading_bar(False, "Estado: inactivo"))
                self.after(0, lambda: messagebox.showinfo("Transcripcion", "No hay nuevas imagenes para transcribir."))
                return
            self.after(0, lambda t=total: self._progress_start(t))
            self.after(0, lambda t=total: self._set_loading_bar(True, f"Estado: ejecutando OCR ({t} imagen(es))..."))
            run_path = self._new_run_log_path(
                provider=provider,
                model=model,
                detect_model=detect_model,
                total=total,
            )
            run_ctx["path"] = run_path
            self.after(
                0,
                lambda t=total, dm=detect_model, rp=str(run_path): self._log(
                    f"Lote iniciado: {t} imagen(es) | detector={dm} | log={rp}"
                ),
            )
            self.after(
                0,
                lambda: self._log(
                    "Ruta fija activa: 1) segmentar por problemas, 2) OCR+modelo por bloque, "
                    "3) claves/solucion (opcional), 4) resultado previo."
                ),
            )
            ok_count = 0
            err_count = 0
            detected_items_total = 0
            pending_incomplete_idx: Optional[int] = None
            ai_fallback_to_ocr = False
            logged_native_format_skip = False
            logged_native_meta_skip = False
            logged_unified_pass_defer = False
            used_segments_run: Dict[str, Set[int]] = {
                str(k): set(v or set()) for k, v in self._segmentation_v2_used_segments.items()
            }
            self._geometry_pass_by_label.clear()
            run_start_item_idx = len(self._items)

            for idx, (label, path) in enumerate(to_process, start=1):
                merge_notes: List[str] = []
                self._ocr_merge_applied_by_label.pop(str(label), None)
                pending_idx_start = (
                    pending_incomplete_idx
                    if pending_incomplete_idx is not None and 0 <= pending_incomplete_idx < len(self._items)
                    else None
                )
                fusion_applied_this_label = False
                local_prefill_attempted = False
                local_fallback_attempted = False
                effective_provider = provider
                if ai_fallback_to_ocr and provider in (PROVIDER_OPENAI, PROVIDER_HF):
                    effective_provider = PROVIDER_OCR
                key = self._seg_v2_source_key(path)
                segment_boxes = list(source_segment_boxes.get(key, []))
                segment_offset = 0
                manual_ocr_box_px = self._get_ocr_exclusion_box(path)
                if manual_ocr_box_px is not None:
                    segment_boxes.append(tuple(int(v) for v in manual_ocr_box_px))
                ocr_path = path
                if segment_boxes:
                    masked = self._build_masked_image_for_ocr_boxes(path=path, boxes_px=segment_boxes, run_idx=idx)
                    if masked is not None:
                        ocr_path = masked
                        self.after(
                            0,
                            lambda l=label, n=len(segment_boxes): self._log(
                                f"{l}: OCR sobre remanente activo ({n} segmento(s) excluido(s))."
                            ),
                        )
                self.after(
                    0,
                    lambda l=label, p=effective_provider, i=idx, t=total: self._log(
                        f"[{i}/{t}] Transcribiendo ({p}): {l}"
                    ),
                )
                self._append_run_event(
                    run_path,
                    {
                        "event": "image_started",
                        "idx": idx,
                        "total": total,
                        "label": label,
                        "path": str(path),
                        "provider": effective_provider,
                    },
                )
                try:
                    if pending_incomplete_idx is not None and 0 <= pending_incomplete_idx < len(self._items):
                        leading_cont = ""
                        leading_opts: Dict[str, str] = {}
                        if effective_provider == PROVIDER_OPENAI and client is not None:
                            leading_cont, leading_opts = self._extract_leading_options_openai(
                                client,
                                model=model,
                                timeout_s=timeout_s,
                                retries=retries,
                                label=label,
                                path=ocr_path,
                            )
                        elif effective_provider == PROVIDER_HF:
                            leading_cont, leading_opts = self._extract_leading_options_hf(
                                model=model,
                                timeout_s=timeout_s,
                                retries=retries,
                                label=label,
                                path=ocr_path,
                            )
                        # Fallback robusto: si el proveedor vision no devolvio prefijo,
                        # intentar con OCR local sobre la imagen ORIGINAL (sin mascara)
                        # para no perder continuaciones/opciones del borde superior.
                        if (not leading_cont) and (not leading_opts):
                            try:
                                local_prefill_attempted = True
                                local_prefill_raw = self._transcribir_local_ocr(path)
                                local_prefix, _local_rest = self._extract_leading_continuation_payload(
                                    local_prefill_raw,
                                    force_prefix=True,
                                )
                                if local_prefix:
                                    local_enu, local_opts = self._extract_options_loose(local_prefix)
                                    if local_opts:
                                        leading_opts.update(local_opts)
                                        leading_cont = (local_enu or "").strip()
                                    else:
                                        leading_cont = local_prefix
                                if not leading_opts:
                                    local_only_opts, _local_after_opts = self._extract_leading_options_payload(
                                        local_prefill_raw
                                    )
                                    if local_only_opts:
                                        leading_opts.update(local_only_opts)
                                if (not leading_cont) and (not leading_opts):
                                    # Ultimo fallback: usar parseo suelto del OCR local completo.
                                    # Sirve cuando el OCR no reconoce bien el header del siguiente problema.
                                    loose_enu, loose_opts = self._extract_options_loose(local_prefill_raw)
                                    if len(loose_opts) >= 3:
                                        leading_cont = (loose_enu or "").strip()
                                        leading_opts.update(loose_opts)
                            except Exception:
                                pass
                        if leading_cont and pending_incomplete_idx is not None and 0 <= pending_incomplete_idx < len(self._items):
                            target_num = self._pending_item_number(pending_incomplete_idx, fallback=idx)
                            note = self._build_merge_text_note(
                                stage=f"prepass_continuacion_inicio -> item {target_num}",
                                text=leading_cont,
                            )
                            if note:
                                merge_notes.append(note)
                            base_archivo, base_item, base_imgs = self._items[pending_incomplete_idx]
                            merged = self._merge_continuation_into_item(
                                base_item=base_item,
                                continuation_text=leading_cont,
                                fallback_numero=target_num,
                            )
                            self._items[pending_incomplete_idx] = (base_archivo, merged, base_imgs)
                            if segment_offset < len(segment_boxes) and self._should_attach_segment_on_merge(
                                base_item=merged,
                                continuation_text=leading_cont,
                            ):
                                attached = self._attach_segment_to_existing_item(
                                    source_path=path,
                                    segment_box=segment_boxes[segment_offset],
                                    item_idx=pending_incomplete_idx,
                                    fallback_numero=target_num,
                                )
                                if attached:
                                    used_segments_run.setdefault(key, set()).add(segment_offset)
                                    segment_offset += 1
                            if self._item_has_complete_options(merged):
                                pending_incomplete_idx = None
                            fusion_applied_this_label = True
                            self.after(
                                0,
                                lambda l=label: self._log(
                                    f"{l}: continuidad de cabecera detectada por prepass y aplicada al problema anterior."
                                ),
                            )
                            self._render_output_from_items()
                        if leading_opts and pending_incomplete_idx is not None and 0 <= pending_incomplete_idx < len(self._items):
                            target_num = self._pending_item_number(pending_incomplete_idx, fallback=idx)
                            note = self._build_merge_options_note(
                                stage=f"prepass_claves_inicio -> item {target_num}",
                                options=leading_opts,
                            )
                            if note:
                                merge_notes.append(note)
                            base_archivo, base_item, base_imgs = self._items[pending_incomplete_idx]
                            merged = self._merge_options_into_item(
                                base_item=base_item,
                                extra_options=leading_opts,
                                fallback_numero=target_num,
                            )
                            self._items[pending_incomplete_idx] = (base_archivo, merged, base_imgs)
                            if self._item_has_complete_options(merged):
                                pending_incomplete_idx = None
                            fusion_applied_this_label = True
                            self.after(
                                0,
                                lambda l=label: self._log(
                                    f"{l}: claves de cabecera detectadas por vision y aplicadas al problema anterior."
                                ),
                            )
                            self._render_output_from_items()

                    try:
                        if effective_provider == PROVIDER_OPENAI:
                            text = self._transcribir_openai(
                                client,  # type: ignore[arg-type]
                                model=model,
                                timeout_s=timeout_s,
                                retries=retries,
                                label=label,
                                path=ocr_path,
                            )
                        elif effective_provider == PROVIDER_HF:
                            text = self._transcribir_huggingface(
                                model=model,
                                timeout_s=timeout_s,
                                retries=retries,
                                label=label,
                                path=ocr_path,
                            )
                        else:
                            text = self._transcribir_local_ocr(ocr_path, label=label)
                    except Exception as trans_exc:
                        if effective_provider in (PROVIDER_OPENAI, PROVIDER_HF):
                            reason = str(trans_exc)
                            if self._is_credit_depleted_error(trans_exc):
                                ai_fallback_to_ocr = True
                                self.after(
                                    0,
                                    lambda l=label: self._log(
                                        f"{l}: credito IA agotado (402). Cambio automatico a OCR local para terminar el lote."
                                    ),
                                )
                            else:
                                self.after(
                                    0,
                                    lambda l=label, r=reason: self._log(
                                        f"{l}: fallo IA ({r}). Fallback automatico a OCR local."
                                    ),
                                )
                            self._append_run_event(
                                run_path,
                                {
                                    "event": "provider_fallback",
                                    "idx": idx,
                                    "label": label,
                                    "from": effective_provider,
                                    "to": PROVIDER_OCR,
                                    "reason": reason,
                                },
                            )
                            text = self._transcribir_local_ocr(ocr_path, label=label)
                            effective_provider = PROVIDER_OCR
                        else:
                            raise
                    if pending_incomplete_idx is not None and 0 <= pending_incomplete_idx < len(self._items):
                        merged_pending = False
                        prefix_cont = ""
                        leading_options: Dict[str, str] = {}

                        structured_cont, structured_opts, text = self._extract_structured_leading_payload(text)
                        if structured_cont:
                            target_num = self._pending_item_number(pending_incomplete_idx, fallback=idx)
                            note = self._build_merge_text_note(
                                stage=f"leading_continuation_modelo -> item {target_num}",
                                text=structured_cont,
                            )
                            if note:
                                merge_notes.append(note)
                            base_archivo, base_item, base_imgs = self._items[pending_incomplete_idx]
                            merged = self._merge_continuation_into_item(
                                base_item=base_item,
                                continuation_text=structured_cont,
                                fallback_numero=target_num,
                            )
                            self._items[pending_incomplete_idx] = (base_archivo, merged, base_imgs)
                            if segment_offset < len(segment_boxes) and self._should_attach_segment_on_merge(
                                base_item=merged,
                                continuation_text=structured_cont,
                            ):
                                attached = self._attach_segment_to_existing_item(
                                    source_path=path,
                                    segment_box=segment_boxes[segment_offset],
                                    item_idx=pending_incomplete_idx,
                                    fallback_numero=target_num,
                                )
                                if attached:
                                    used_segments_run.setdefault(key, set()).add(segment_offset)
                                    segment_offset += 1
                            if self._item_has_complete_options(merged):
                                pending_incomplete_idx = None
                            fusion_applied_this_label = True
                            merged_pending = True
                            self.after(
                                0,
                                lambda l=label: self._log(
                                    f"{l}: LEADING_CONTINUATION aplicado al problema pendiente."
                                ),
                            )
                            self._render_output_from_items()

                        if (
                            structured_opts
                            and pending_incomplete_idx is not None
                            and 0 <= pending_incomplete_idx < len(self._items)
                        ):
                            target_num = self._pending_item_number(pending_incomplete_idx, fallback=idx)
                            note = self._build_merge_options_note(
                                stage=f"leading_options_modelo -> item {target_num}",
                                options=structured_opts,
                            )
                            if note:
                                merge_notes.append(note)
                            base_archivo, base_item, base_imgs = self._items[pending_incomplete_idx]
                            merged = self._merge_options_into_item(
                                base_item=base_item,
                                extra_options=structured_opts,
                                fallback_numero=target_num,
                            )
                            self._items[pending_incomplete_idx] = (base_archivo, merged, base_imgs)
                            if self._item_has_complete_options(merged):
                                pending_incomplete_idx = None
                            fusion_applied_this_label = True
                            merged_pending = True
                            self.after(
                                0,
                                lambda l=label: self._log(
                                    f"{l}: LEADING_OPTIONS aplicado al problema pendiente."
                                ),
                            )
                            self._render_output_from_items()

                        # Prefer full continuation merge before splitting new items.
                        prefix_cont, stripped_after_prefix = self._extract_leading_continuation_payload(
                            text,
                            force_prefix=(
                                pending_incomplete_idx is not None
                                and 0 <= pending_incomplete_idx < len(self._items)
                            ),
                        )
                        if prefix_cont:
                            target_num = self._pending_item_number(pending_incomplete_idx, fallback=idx)
                            note = self._build_merge_text_note(
                                stage=f"continuacion_prefijo -> item {target_num}",
                                text=prefix_cont,
                            )
                            if note:
                                merge_notes.append(note)
                            base_archivo, base_item, base_imgs = self._items[pending_incomplete_idx]
                            merged = self._merge_continuation_into_item(
                                base_item=base_item,
                                continuation_text=prefix_cont,
                                fallback_numero=target_num,
                            )
                            self._items[pending_incomplete_idx] = (base_archivo, merged, base_imgs)
                            if segment_offset < len(segment_boxes) and self._should_attach_segment_on_merge(
                                base_item=merged,
                                continuation_text=prefix_cont,
                            ):
                                attached = self._attach_segment_to_existing_item(
                                    source_path=path,
                                    segment_box=segment_boxes[segment_offset],
                                    item_idx=pending_incomplete_idx,
                                    fallback_numero=target_num,
                                )
                                if attached:
                                    used_segments_run.setdefault(key, set()).add(segment_offset)
                                    segment_offset += 1
                            if self._item_has_complete_options(merged):
                                pending_incomplete_idx = None
                            fusion_applied_this_label = True
                            text = stripped_after_prefix
                            merged_pending = True
                            self.after(
                                0,
                                lambda l=label: self._log(
                                    f"{l}: continuacion detectada (enunciado/figura/claves) y aplicada al problema pendiente."
                                ),
                            )
                            self._render_output_from_items()

                        leading_options, stripped_text = self._extract_leading_options_payload(text)
                        if leading_options and pending_incomplete_idx is not None and 0 <= pending_incomplete_idx < len(self._items):
                            target_num = self._pending_item_number(pending_incomplete_idx, fallback=idx)
                            note = self._build_merge_options_note(
                                stage=f"claves_prefijo_ocr -> item {target_num}",
                                options=leading_options,
                            )
                            if note:
                                merge_notes.append(note)
                            base_archivo, base_item, base_imgs = self._items[pending_incomplete_idx]
                            merged = self._merge_options_into_item(
                                base_item=base_item,
                                extra_options=leading_options,
                                fallback_numero=target_num,
                            )
                            self._items[pending_incomplete_idx] = (base_archivo, merged, base_imgs)
                            if self._item_has_complete_options(merged):
                                pending_incomplete_idx = None
                            fusion_applied_this_label = True
                            text = stripped_text
                            merged_pending = True
                            self.after(
                                0,
                                lambda l=label: self._log(
                                    f"{l}: claves al inicio detectadas y aplicadas al problema incompleto anterior."
                                ),
                            )
                            self._render_output_from_items()

                        # Fallback robusto: si hay item pendiente y el OCR de vision no dio prefijo,
                        # intentar extraer continuidad desde OCR local de la misma imagen.
                        if (
                            pending_incomplete_idx is not None
                            and 0 <= pending_incomplete_idx < len(self._items)
                            and not merged_pending
                            and (not structured_cont)
                            and (not structured_opts)
                            and (not prefix_cont)
                            and (not leading_options)
                        ):
                            text_now = self.controller.normalizar_item_una_linea(self._decode_scan_escapes(text or ""))
                            has_new_header = (
                                ITEM_HEADER_RE.search(text_now) is not None
                                or STRUCTURED_ITEM_HEADER_RE.search(text_now) is not None
                                or re.search(r"\bproblema\b", text_now, re.IGNORECASE) is not None
                            )
                            if has_new_header:
                                try:
                                    local_fallback_attempted = True
                                    # Usar imagen original para recuperar texto de continuidad
                                    # que pudo quedar fuera del remanente enmascarado.
                                    local_raw = self._transcribir_local_ocr(path)
                                    next_item_num = self._extract_structured_item_number(text_now)
                                    if next_item_num is None:
                                        m_next = ITEM_HEADER_RE.search(text_now)
                                        if m_next:
                                            try:
                                                next_item_num = int(m_next.group(2))
                                            except Exception:
                                                next_item_num = None
                                    fb_cont, _fb_rest = self._extract_prefix_before_known_item_number(
                                        local_raw,
                                        next_item_number=next_item_num,
                                    )
                                    if not fb_cont:
                                        fb_cont, _fb_rest = self._extract_leading_continuation_payload(
                                            local_raw, force_prefix=True
                                        )
                                    fb_opts: Dict[str, str] = {}
                                    if fb_cont:
                                        fb_enu, fb_opts_in_cont = self._extract_options_loose(fb_cont)
                                        if fb_opts_in_cont:
                                            fb_opts.update(fb_opts_in_cont)
                                            fb_cont = (fb_enu or "").strip()
                                    if not fb_opts:
                                        fb_leading_opts, _fb_after_opts = self._extract_leading_options_payload(local_raw)
                                        if fb_leading_opts:
                                            fb_opts.update(fb_leading_opts)
                                    if (not fb_cont) and (not fb_opts):
                                        # Ultimo fallback: parseo suelto de todo el OCR local.
                                        # Captura casos donde las claves del item pendiente estan al inicio
                                        # pero el header del nuevo problema sale degradado.
                                        fb_enu2, fb_opts2 = self._extract_options_loose(local_raw)
                                        if len(fb_opts2) >= 3:
                                            fb_cont = (fb_enu2 or "").strip()
                                            fb_opts.update(fb_opts2)
                                    if fb_cont:
                                        target_num = self._pending_item_number(pending_incomplete_idx, fallback=idx)
                                        note = self._build_merge_text_note(
                                            stage=f"prepass_continuacion_inicio -> item {target_num}",
                                            text=fb_cont,
                                        )
                                        if note:
                                            merge_notes.append(note)
                                        base_archivo, base_item, base_imgs = self._items[pending_incomplete_idx]
                                        merged = self._merge_continuation_into_item(
                                            base_item=base_item,
                                            continuation_text=fb_cont,
                                            fallback_numero=target_num,
                                        )
                                        self._items[pending_incomplete_idx] = (base_archivo, merged, base_imgs)
                                        if self._item_has_complete_options(merged):
                                            pending_incomplete_idx = None
                                        merged_pending = True
                                        fusion_applied_this_label = True
                                        self.after(
                                            0,
                                            lambda l=label: self._log(
                                                f"{l}: continuidad recuperada por fallback OCR local y fusionada al pendiente."
                                            ),
                                        )
                                        self._render_output_from_items()
                                    if (
                                        fb_opts
                                        and pending_incomplete_idx is not None
                                        and 0 <= pending_incomplete_idx < len(self._items)
                                    ):
                                        target_num = self._pending_item_number(pending_incomplete_idx, fallback=idx)
                                        note = self._build_merge_options_note(
                                            stage=f"prepass_claves_inicio -> item {target_num}",
                                            options=fb_opts,
                                        )
                                        if note:
                                            merge_notes.append(note)
                                        base_archivo, base_item, base_imgs = self._items[pending_incomplete_idx]
                                        merged = self._merge_options_into_item(
                                            base_item=base_item,
                                            extra_options=fb_opts,
                                            fallback_numero=target_num,
                                        )
                                        self._items[pending_incomplete_idx] = (base_archivo, merged, base_imgs)
                                        if self._item_has_complete_options(merged):
                                            pending_incomplete_idx = None
                                        merged_pending = True
                                        fusion_applied_this_label = True
                                        self.after(
                                            0,
                                            lambda l=label: self._log(
                                                f"{l}: claves recuperadas por fallback OCR local y fusionadas al pendiente."
                                            ),
                                        )
                                        self._render_output_from_items()
                                except Exception:
                                    pass

                        # Continuation image with no explicit new item header.
                        if pending_incomplete_idx is not None and 0 <= pending_incomplete_idx < len(self._items):
                            plain_now = self.controller.normalizar_item_una_linea(self._decode_scan_escapes(text or ""))
                            has_item_header_now = ITEM_HEADER_RE.search(plain_now) is not None
                            has_problem_header_now = re.search(r"\bproblema\b", plain_now, re.IGNORECASE) is not None
                            has_structured_header_now = STRUCTURED_ITEM_HEADER_RE.search(plain_now) is not None
                            if plain_now and (not has_item_header_now) and (not has_problem_header_now) and (not has_structured_header_now):
                                target_num = self._pending_item_number(pending_incomplete_idx, fallback=idx)
                                note = self._build_merge_text_note(
                                    stage=f"continuacion_bloque_sin_header -> item {target_num}",
                                    text=plain_now,
                                )
                                if note:
                                    merge_notes.append(note)
                                base_archivo, base_item, base_imgs = self._items[pending_incomplete_idx]
                                merged = self._merge_continuation_into_item(
                                    base_item=base_item,
                                    continuation_text=plain_now,
                                    fallback_numero=target_num,
                                )
                                self._items[pending_incomplete_idx] = (base_archivo, merged, base_imgs)
                                if segment_offset < len(segment_boxes) and self._should_attach_segment_on_merge(
                                    base_item=merged,
                                    continuation_text=plain_now,
                                ):
                                    attached = self._attach_segment_to_existing_item(
                                        source_path=path,
                                        segment_box=segment_boxes[segment_offset],
                                        item_idx=pending_incomplete_idx,
                                        fallback_numero=target_num,
                                    )
                                    if attached:
                                        used_segments_run.setdefault(key, set()).add(segment_offset)
                                        segment_offset += 1
                                if self._item_has_complete_options(merged):
                                    pending_incomplete_idx = None
                                merged_pending = True
                                fusion_applied_this_label = True
                                self.after(
                                    0,
                                    lambda l=label: self._log(
                                        f"{l}: bloque sin header aplicado como continuacion al problema pendiente."
                                    ),
                                )
                                self._render_output_from_items()
                                percent = int(idx / total * 100)
                                self.after(0, lambda v=percent: self.progress.configure(value=v))
                                self.after(
                                    0,
                                    lambda l=label: self._log(
                                        f"{l}: usado como continuacion (sin crear item nuevo)."
                                    ),
                                )
                                self._transcribed_by_label[label] = ""
                                self._set_merge_notes_for_label(label, merge_notes)
                                self.after(0, self._refresh_image_list_colors)
                                self._append_run_event(
                                    run_path,
                                    {
                                        "event": "image_continuation_merged",
                                        "idx": idx,
                                        "label": label,
                                        "merged_into_pending": True,
                                    },
                                )
                                ok_count += 1
                                continue

                        if merged_pending and not (text or "").strip():
                            percent = int(idx / total * 100)
                            self.after(0, lambda v=percent: self.progress.configure(value=v))
                            self.after(
                                0,
                                lambda l=label: self._log(
                                    f"{l}: continuacion aplicada; sin nuevo item en esta imagen."
                                ),
                            )
                            self._transcribed_by_label[label] = ""
                            self._set_merge_notes_for_label(label, merge_notes)
                            self.after(0, self._refresh_image_list_colors)
                            self._append_run_event(
                                run_path,
                                {
                                    "event": "image_continuation_only",
                                    "idx": idx,
                                    "label": label,
                                },
                            )
                            ok_count += 1
                            continue

                    raw_items = self._split_items_from_text(text, path=path, idx=idx)
                    if not raw_items:
                        raw_items = [self._item_from_plain_ocr(text, path, idx)]
                    available_segment_boxes = (
                        segment_boxes[segment_offset:] if segment_offset < len(segment_boxes) else []
                    )
                    tag_plan = self._plan_image_tag_positions(
                        raw_items=raw_items,
                        segment_count=len(available_segment_boxes),
                    )
                    if available_segment_boxes:
                        self.after(
                            0,
                            lambda l=label, c=len(available_segment_boxes), m=len(raw_items), tp=len(tag_plan), off=segment_offset: self._log(
                                f"{l}: segmentos disponibles={c} (offset={off}), items_ocr={m}, items_etiquetables={tp}."
                            ),
                        )

                    final_items: List[Tuple[str, List[str]]] = []
                    multi_item = len(raw_items) > 1
                    if multi_item:
                        self.after(0, lambda l=label, n=len(raw_items): self._log(f"{l}: se detectaron {n} items en una sola imagen."))

                    for item_pos, raw_item in enumerate(raw_items, start=1):
                        raw_norm = self.controller.normalizar_item_una_linea(self._decode_scan_escapes(raw_item))
                        if raw_norm.startswith(ORPHAN_OPTIONS_PREFIX):
                            orphan_text = raw_norm[len(ORPHAN_OPTIONS_PREFIX) :].strip()
                            _enu, extra_options = self._extract_options_loose(orphan_text)
                            if pending_incomplete_idx is not None and 0 <= pending_incomplete_idx < len(self._items):
                                target_num = self._pending_item_number(pending_incomplete_idx, fallback=idx)
                                note = self._build_merge_options_note(
                                    stage=f"opciones_huerfanas_prefijo -> item {target_num}",
                                    options=extra_options,
                                )
                                if note:
                                    merge_notes.append(note)
                                base_archivo, base_item, base_imgs = self._items[pending_incomplete_idx]
                                merged = self._merge_options_into_item(
                                    base_item=base_item,
                                    extra_options=extra_options,
                                    fallback_numero=target_num,
                                )
                                self._items[pending_incomplete_idx] = (base_archivo, merged, base_imgs)
                                if self._item_has_complete_options(merged):
                                    pending_incomplete_idx = None
                                self.after(
                                    0,
                                    lambda l=label, p=item_pos: self._log(
                                        f"{l}#{p}: opciones en cabecera aplicadas al item incompleto anterior."
                                    ),
                                )
                                self._render_output_from_items()
                            else:
                                self.after(
                                    0,
                                    lambda l=label, p=item_pos: self._log(
                                        f"{l}#{p}: opciones sueltas detectadas sin item pendiente; se omiten."
                                    ),
                                )
                            continue

                        # If the next image repeats the same item number while the
                        # previous one is incomplete, treat it as continuation and merge.
                        if (
                            pending_incomplete_idx is not None
                            and 0 <= pending_incomplete_idx < len(self._items)
                            and item_pos == 1
                        ):
                            base_archivo, base_item, base_imgs = self._items[pending_incomplete_idx]
                            pending_num = (
                                self.controller.parsear_numero_original(base_item)
                                or self._extract_structured_item_number(base_item)
                                or 0
                            )
                            incoming_num = (
                                self.controller.parsear_numero_original(raw_norm)
                                or self._extract_structured_item_number(raw_norm)
                                or 0
                            )
                            should_force_continuation = (
                                pending_num > 0
                                and incoming_num > 0
                                and (incoming_num == pending_num or incoming_num < pending_num)
                            )
                            if should_force_continuation:
                                note = self._build_merge_text_note(
                                    stage=f"continuacion_por_numero_pending_{pending_num}_incoming_{incoming_num}",
                                    text=raw_norm,
                                )
                                if note:
                                    merge_notes.append(note)
                                merged = self._merge_continuation_into_item(
                                    base_item=base_item,
                                    continuation_text=raw_norm,
                                    fallback_numero=pending_num,
                                )
                                self._items[pending_incomplete_idx] = (base_archivo, merged, base_imgs)
                                if segment_offset < len(segment_boxes) and self._should_attach_segment_on_merge(
                                    base_item=merged,
                                    continuation_text=raw_norm,
                                ):
                                    attached = self._attach_segment_to_existing_item(
                                        source_path=path,
                                        segment_box=segment_boxes[segment_offset],
                                        item_idx=pending_incomplete_idx,
                                        fallback_numero=pending_num,
                                    )
                                    if attached:
                                        used_segments_run.setdefault(key, set()).add(segment_offset)
                                        segment_offset += 1
                                if self._item_has_complete_options(merged):
                                    pending_incomplete_idx = None
                                self.after(
                                    0,
                                    lambda l=label, n=pending_num, i=incoming_num: self._log(
                                        f"{l}: continuacion por numero detectada (pending={n}, incoming={i}) y fusionada con el pendiente."
                                    ),
                                )
                                self._render_output_from_items()
                                continue

                        numero = (
                            self.controller.parsear_numero_original(raw_item)
                            or self._extract_structured_item_number(raw_item)
                            or self._infer_numero(path, idx + item_pos - 1)
                        )
                        working_item = raw_item
                        used_llm_output = False
                        if self.format_with_llm_var.get() and not hf_native_ocr_mode and (not apply_llm_after_unified):
                            curso_hint = (self.curso_var.get() or "").strip()
                            tema_hint = (self.tema_var.get() or "").strip()
                            subtema_hint = (self.subtema_var.get() or "").strip()
                            if effective_provider == PROVIDER_OPENAI and client is not None:
                                formatted = self._format_item_openai(
                                    client,
                                    model=model,
                                    timeout_s=timeout_s,
                                    retries=retries,
                                    label=f"{label}#{item_pos}" if multi_item else label,
                                    raw_item=raw_item,
                                    curso_hint=curso_hint,
                                    tema_hint=tema_hint,
                                    subtema_hint=subtema_hint,
                                )
                                if formatted:
                                    working_item = formatted
                                    used_llm_output = True
                            elif effective_provider == PROVIDER_HF:
                                formatted = self._format_item_hf(
                                    model=model,
                                    timeout_s=timeout_s,
                                    retries=retries,
                                    label=f"{label}#{item_pos}" if multi_item else label,
                                    raw_item=raw_item,
                                    curso_hint=curso_hint,
                                    tema_hint=tema_hint,
                                    subtema_hint=subtema_hint,
                                )
                                if formatted:
                                    working_item = formatted
                                    used_llm_output = True
                            else:
                                self.after(
                                    0,
                                    lambda: self._log(
                                        "Formateo IA requiere proveedor OpenAI/HuggingFace. "
                                        "Se usa formateo local para este item."
                                    ),
                                )
                        elif (
                            self.format_with_llm_var.get()
                            and (not hf_native_ocr_mode)
                            and apply_llm_after_unified
                            and (not logged_unified_pass_defer)
                        ):
                            logged_unified_pass_defer = True
                            self.after(
                                0,
                                lambda: self._log(
                                    "Pasadas IA (1 y 2) diferidas: se aplicaran sobre OCR unificado al finalizar el lote."
                                ),
                            )
                        elif self.format_with_llm_var.get() and hf_native_ocr_mode and not logged_native_format_skip:
                            logged_native_format_skip = True
                            self.after(
                                0,
                                lambda: self._log(
                                    "DeepSeek-OCR en endpoint dedicado: se omite pase de formateo IA remoto "
                                    "(el endpoint no es chat). Se aplica formateo local."
                                ),
                            )

                        if used_llm_output:
                            item = self._finalize_item_prompt_first(
                                working_item, path=path, fallback_numero=numero
                            )
                        else:
                            # Keep a soft path (no hard rebuild) when LLM formatting is not used.
                            item = self._finalize_item_prompt_first(
                                working_item, path=path, fallback_numero=numero
                            )
                        numero = self.controller.parsear_numero_original(item) or numero

                        # Regla por plan: solo algunos items reciben etiqueta, segun hints y cantidad de segmentos.
                        seg_idx = tag_plan.get(item_pos, -1)
                        seg_idx_abs = segment_offset + seg_idx if seg_idx >= 0 else -1
                        item_segment_box = (
                            tuple(int(v) for v in segment_boxes[seg_idx_abs])
                            if 0 <= seg_idx_abs < len(segment_boxes)
                            else None
                        )
                        bbox_for_item: Optional[Tuple[float, float, float, float]] = None
                        bbox_conf = 0.0
                        has_figure_detected = False
                        if item_segment_box is not None:
                            bbox_for_item = self._box_px_to_norm(path=path, box_px=item_segment_box)
                            has_figure_detected = bbox_for_item is not None
                            bbox_conf = 1.0 if has_figure_detected else 0.0
                        if self.debug_detect_var.get() and not multi_item:
                            self.after(
                                0,
                                lambda l=label, p=item_pos, h=has_figure_detected, c=bbox_conf, b=bbox_for_item: self._log(
                                    f"[detector] {l}#{p} has={h} conf={c:.2f} bbox={b if b else '{}'}"
                                ),
                            )
                            overlay = self._save_detection_overlay(
                                image_path=path,
                                bbox_norm=bbox_for_item,
                                has_figure=has_figure_detected,
                                confidence=bbox_conf,
                                label=f"{label}_{item_pos}",
                            )
                            if overlay:
                                self.after(0, lambda p=overlay: self._log(f"[detector] overlay: {p}"))

                        if self.detect_figure_var.get():
                            # Enforce segmentation-driven markers: only items mapped to
                            # a real segment keep/get [[Imagen=...]].
                            if item_segment_box is None and self._has_image_marker(item):
                                item = self._remove_image_markers(item)
                                self.after(
                                    0,
                                    lambda l=label, p=item_pos: self._log(
                                        f"{l}#{p}: etiqueta de imagen removida (sin segmento asociado)."
                                    ),
                                )
                            has_marker = self._has_image_marker(item)
                            if item_segment_box is not None and (not has_marker):
                                item = self._insert_image_marker(item, path=path, numero=numero)
                                item = self._move_image_marker_before_options(item)
                                self.after(
                                    0,
                                    lambda l=label, n=numero: self._log(
                                        f"Segmento detectado en {l}; etiqueta [[Imagen=img-{n}]] aplicada."
                                    ),
                                )
                        elif self._has_image_marker(item):
                            # Figure detection disabled: strip any model-injected marker.
                            item = self._remove_image_markers(item)

                        meta_mode = tag_mode
                        if hf_native_ocr_mode and tag_mode in (TAG_MODE_AUTO, TAG_MODE_MIXED):
                            meta_mode = TAG_MODE_MANUAL
                            if not logged_native_meta_skip:
                                logged_native_meta_skip = True
                                self.after(
                                    0,
                                    lambda: self._log(
                                        "DeepSeek-OCR endpoint dedicado: clasificacion Auto/Mixto IA desactivada "
                                        "porque el endpoint no es chat. Se usan etiquetas manuales/existentes."
                                    ),
                                )

                        if not apply_llm_after_unified:
                            item = self._apply_metadata_mode(
                                item=item,
                                mode=meta_mode,
                                provider=effective_provider,
                                model=model,
                                timeout_s=timeout_s,
                                retries=retries,
                                label=f"{label}#{item_pos}" if multi_item else label,
                                catalog=catalog,
                                openai_client=client if effective_provider == PROVIDER_OPENAI else None,
                            )
                        if used_llm_output:
                            # Prompt-first path, but protect against truncated/partial options.
                            item = self.controller.normalizar_item_una_linea(item)
                            raw_opt_count = self._item_option_count(raw_item)
                            llm_opt_count = self._item_option_count(item)
                            if raw_opt_count >= 2 and llm_opt_count < raw_opt_count:
                                recovered = self._finalize_item_prompt_first(
                                    raw_item, path=path, fallback_numero=numero
                                )
                                recovered_opt_count = self._item_option_count(recovered)
                                if recovered_opt_count >= raw_opt_count:
                                    item = recovered
                                    self.after(
                                        0,
                                        lambda l=label, p=item_pos, o=llm_opt_count, r=raw_opt_count, q=recovered_opt_count: self._log(
                                            f"{l}#{p}: salida IA recortada ({o}/{r} opciones); "
                                            f"se recupero formato local ({q}/{r})."
                                        ),
                                    )
                            # Soft finalize for LLM output: keep model structure.
                            item = self._move_image_marker_before_options(item)
                            item = self._normalize_math_delimiters(item)
                        elif apply_llm_after_unified:
                            # En modo OCR unificado, aqui solo dejamos item "crudo" estable.
                            item = self._move_image_marker_before_options(item)
                            item = self.controller.normalizar_item_una_linea(item)
                        else:
                            item = self._apply_global_math_conventions(item)
                            item = self._apply_geometry_conventions(item)
                            item = self._normalize_math_delimiters(item)
                            # Soft finalize for non-LLM fallback path.
                            item = self._move_image_marker_before_options(item)
                            item = self._normalize_math_delimiters(item)
                        # Failsafe: if this item was mapped to a real segment, keep image marker.
                        if self.detect_figure_var.get() and item_segment_box is not None and (not self._has_image_marker(item)):
                            item = self._insert_image_marker(item, path=path, numero=numero)
                            item = self._move_image_marker_before_options(item)
                            self.after(
                                0,
                                lambda l=label, p=item_pos, n=numero: self._log(
                                    f"{l}#{p}: marcador [[Imagen=img-{n}]] reinsertado (failsafe)."
                                ),
                            )

                        if self._is_orphan_options_item(item):
                            if pending_incomplete_idx is not None and 0 <= pending_incomplete_idx < len(self._items):
                                orphan_body = self._body_without_tags_and_markers(item)
                                _orphan_enu, orphan_payload = self._extract_options_loose(orphan_body)
                                target_num = self._pending_item_number(pending_incomplete_idx, fallback=numero)
                                note = self._build_merge_options_note(
                                    stage=f"opciones_huerfanas_item -> item {target_num}",
                                    options=orphan_payload,
                                )
                                if note:
                                    merge_notes.append(note)
                                base_archivo, base_item, base_imgs = self._items[pending_incomplete_idx]
                                merged = self._merge_orphan_options_into_item(
                                    base_item,
                                    item,
                                    fallback_numero=target_num,
                                )
                                self._items[pending_incomplete_idx] = (base_archivo, merged, base_imgs)
                                if self._item_has_complete_options(merged):
                                    pending_incomplete_idx = None
                                self.after(
                                    0,
                                    lambda l=label, p=item_pos: self._log(
                                        f"{l}#{p}: claves de continuacion aplicadas al item anterior."
                                    ),
                                )
                                self._render_output_from_items()
                                continue
                            self.after(
                                0,
                                lambda l=label, p=item_pos: self._log(
                                    f"{l}#{p}: bloque de opciones huerfano detectado; se omite."
                                ),
                            )
                            continue
                        image_paths: List[str] = []
                        if self.auto_crop_var.get() and self._has_image_marker(item):
                            marker_name = self._extract_first_image_marker_name(item) or f"{path.stem}-{numero}"
                            # Save crop whenever this item is mapped to a segment box,
                            # even when OCR yielded multiple items from the same source.
                            bbox = bbox_for_item
                            if bbox is None:
                                self.after(
                                    0,
                                    lambda l=label, n=numero: self._log(
                                        f"{l}: marcador [[Imagen=img-{n}]] sin bbox de figura; recorte auto omitido (usar ajuste manual)."
                                    ),
                                )
                            else:
                                crop_saved = self._save_figure_crop(
                                    image_path=path,
                                    marker_name=marker_name,
                                    bbox_norm=bbox,
                                )
                                if crop_saved:
                                    image_paths.append(crop_saved)
                                    self._preview_images[marker_name] = crop_saved
                                    self.after(0, lambda p=crop_saved: self._log(f"Recorte guardado: {p}"))
                                    self.after(0, lambda: self._push_preview_text(force=True))
                        if item_segment_box is not None and self._has_image_marker(item) and seg_idx_abs >= 0:
                            used_segments_run.setdefault(key, set()).add(seg_idx_abs)
                        final_items.append((item, image_paths))
                except Exception as exc:
                    self.after(0, lambda e=exc, l=label: self._log(f"Error transcripcion {l}: {e}"))
                    self._set_merge_notes_for_label(label, merge_notes)
                    self._append_run_event(
                        run_path,
                        {
                            "event": "image_error",
                            "idx": idx,
                            "label": label,
                            "stage": "transcription",
                            "error": str(exc),
                            "traceback": traceback.format_exc(),
                        },
                    )
                    err_count += 1
                    continue

                try:
                    archivo_origen = path.stem
                    for item, image_paths in final_items:
                        had_pending_before_append = (
                            pending_incomplete_idx is not None
                            and 0 <= pending_incomplete_idx < len(self._items)
                        )
                        self._items.append((archivo_origen, item, image_paths))
                        if self._item_has_complete_options(item):
                            # Keep previous unresolved pending item until it is merged.
                            if not had_pending_before_append:
                                pending_incomplete_idx = None
                        else:
                            pending_incomplete_idx = len(self._items) - 1
                            self.after(
                                0,
                                lambda l=label: self._log(
                                    f"{l}: item incompleto detectado; esperando posibles claves en la siguiente imagen."
                                ),
                            )
                        self._render_output_from_items()
                        self._transcribed_by_label[label] = "\n".join([it for it, _imgs in final_items])
                        if pending_idx_start is not None and not fusion_applied_this_label:
                            target_num = self._pending_item_number(pending_idx_start, fallback=idx)
                            merge_notes.append(
                                f"sin_fusion -> item {target_num}: no se detecto continuidad/claves para fusion en esta imagen."
                            )
                            try:
                                text_now_dbg = self.controller.normalizar_item_una_linea(self._decode_scan_escapes(text or ""))
                            except Exception:
                                text_now_dbg = ""
                            if text_now_dbg:
                                has_new_header_dbg = (
                                    ITEM_HEADER_RE.search(text_now_dbg) is not None
                                    or STRUCTURED_ITEM_HEADER_RE.search(text_now_dbg) is not None
                                    or re.search(r"\bproblema\b", text_now_dbg, re.IGNORECASE) is not None
                                )
                                if has_new_header_dbg:
                                    merge_notes.append(
                                        "sin_fusion_detalle: la salida del modelo inicio en un nuevo item sin LEADING_CONTINUATION/LEADING_OPTIONS utilizables."
                                    )
                            if local_prefill_attempted:
                                merge_notes.append(
                                    "sin_fusion_detalle: fallback OCR local (prefill) ejecutado sin payload util."
                                )
                            if local_fallback_attempted:
                                merge_notes.append(
                                    "sin_fusion_detalle: fallback OCR local (post-header) ejecutado sin payload util."
                                )
                        self._set_merge_notes_for_label(label, merge_notes)
                        self.after(0, self._refresh_image_list_colors)
                except Exception as exc:
                    self.after(0, lambda e=exc, l=label: self._log(f"Error post-proceso {l}: {e}"))
                    self._set_merge_notes_for_label(label, merge_notes)
                    self._append_run_event(
                        run_path,
                        {
                            "event": "image_error",
                            "idx": idx,
                            "label": label,
                            "stage": "postprocess",
                            "error": str(exc),
                            "traceback": traceback.format_exc(),
                        },
                    )
                    err_count += 1
                    continue

                percent = int(idx / total * 100)
                detected_items_total += len(final_items)
                self.after(
                    0,
                    lambda i=idx, t=total, n=detected_items_total, l=label: self._progress_update(
                        i, t, n, current_label=l
                    ),
                )
                self.after(0, lambda l=label, n=len(final_items): self._log(f"{l}: procesado ({n} item(s))."))
                if len(final_items) == 0:
                    self.after(0, lambda l=label: self._log(f"{l}: procesado sin items nuevos."))
                tagged_items = 0
                for item, _imgs in final_items:
                    if self._has_image_marker(item):
                        tagged_items += 1
                self._append_run_event(
                    run_path,
                    {
                        "event": "image_completed",
                        "idx": idx,
                        "label": label,
                        "items": len(final_items),
                        "tagged_items": tagged_items,
                    },
                )
                ok_count += 1

            if apply_llm_after_unified and self.format_with_llm_var.get():
                provider_for_unified = PROVIDER_OCR if ai_fallback_to_ocr else provider
                fallback_path = paths[0][1] if paths else Path.cwd()
                try:
                    unified_total, unified_llm = self._apply_two_pass_on_ocr_unified(
                        provider=provider_for_unified,
                        model=model,
                        timeout_s=timeout_s,
                        retries=retries,
                        tag_mode=tag_mode,
                        catalog=catalog,
                        openai_client=client if provider_for_unified == PROVIDER_OPENAI else None,
                        hf_native_ocr_mode=hf_native_ocr_mode,
                        run_start_item_idx=run_start_item_idx,
                        fallback_path=fallback_path,
                        labels=[str(lb) for (lb, _p) in to_process],
                    )
                    self.after(
                        0,
                        lambda t=unified_total, u=unified_llm: self._log(
                            f"OCR unificado: pasada 1+2 aplicada sobre {t} item(s); con IA={u}."
                        ),
                    )
                except Exception as exc:
                    self.after(0, lambda e=exc: self._log(f"OCR unificado: error en pasada 1+2: {e}"))

            self._segmentation_v2_used_segments = {
                str(k): set(v or set()) for k, v in used_segments_run.items()
            }
            self.after(0, self._refresh_image_list_colors)
            self.after(
                0,
                lambda o=ok_count, e=err_count, t=total: self._log(
                    f"Transcripcion completada. Lote={t}, OK={o}, Errores={e}."
                ),
            )
            self.after(
                0,
                lambda o=ok_count, e=err_count, t=total, n=detected_items_total: self._progress_finish(
                    ok=o, errors=e, total_images=t, detected_items=n
                ),
            )
            self.after(
                0,
                lambda: self._log("Resultado previo actualizado. Revisa la salida y, si esta bien, guarda a BD."),
            )
            self._append_run_event(
                run_path,
                {
                    "event": "run_completed",
                    "total": total,
                    "ok": ok_count,
                    "errors": err_count,
                },
            )
        def safe_worker():
            try:
                worker()
            except Exception as exc:
                self.after(0, lambda e=exc: self._log(f"Fallo inesperado del lote: {e}"))
                if run_ctx.get("path") is not None:
                    try:
                        self._append_run_event(
                            run_ctx["path"],  # type: ignore[arg-type]
                            {
                                "event": "run_fatal_error",
                                "error": str(exc),
                                "traceback": traceback.format_exc(),
                            },
                        )
                    except Exception:
                        pass
            finally:
                def _unlock():
                    self._transcribing = False
                    try:
                        self.btn_transcribir.configure(state="normal")
                    except Exception:
                        pass
                    self._set_loading_bar(False, "Estado: inactivo")
                self.after(0, _unlock)

        threading.Thread(target=safe_worker, daemon=True).start()

    def _sanitize_inline_tag_value(self, value: str) -> str:
        txt = self._decode_scan_escapes(value or "")
        txt = txt.replace("[", "(").replace("]", ")")
        txt = re.sub(r"\s+", " ", txt).strip(" ,;")
        return txt

    def _upsert_named_tag(self, item_text: str, *, tag_name: str, tag_value: str, tag_re: re.Pattern) -> str:
        txt = self.controller.normalizar_item_una_linea(item_text or "")
        txt = tag_re.sub(" ", txt)
        txt = re.sub(r"\s+", " ", txt).strip()
        clean_value = self._sanitize_inline_tag_value(tag_value)
        if not clean_value:
            return txt
        marker = f"[[{tag_name}={clean_value}]]"
        head = ITEM_HEADER_RE.search(txt)
        if not head:
            return f"{marker} {txt}".strip()

        header = head.group(1)
        rest = (txt[head.end() :] or "").lstrip()
        leading_tags: List[str] = []
        while rest.startswith("[["):
            m = re.match(r"^\[\[\s*([^\]]+?)\s*\]\]\s*", rest)
            if not m:
                break
            token = (m.group(1) or "").strip()
            key = token.split("=", 1)[0].strip().lower() if "=" in token else token.lower()
            if key == "imagen":
                break
            leading_tags.append(m.group(0).strip())
            rest = (rest[m.end() :] or "").lstrip()

        tags = " ".join(leading_tags + [marker]).strip()
        if rest:
            return f"{header} {tags} {rest}".strip()
        return f"{header} {tags}".strip()

    def _parse_claves_text(self, raw_text: str) -> Dict[int, str]:
        txt = (raw_text or "").replace("\r\n", "\n").replace("\r", "\n")
        txt = self._decode_scan_escapes(txt).replace(",", ";")
        txt = txt.replace("—", "-").replace("–", "-")
        pairs: Dict[int, str] = {}

        # Formas frecuentes: 1.A / 1) A / 1 - C / 12 = D
        for m in re.finditer(r"(?<!\d)(\d{1,4})\s*(?:[)\].:\-=]{0,4}\s*)?([A-Ea-e])(?:\s*[)\].])?\b", txt):
            try:
                numero = int((m.group(1) or "").strip())
            except Exception:
                continue
            if numero <= 0:
                continue
            clave = (m.group(2) or "").strip().upper()
            if clave in {"A", "B", "C", "D", "E"}:
                pairs[numero] = clave

        # Fallback for compact patterns like "4C 5D 6A".
        for m in re.finditer(r"(?<!\d)(\d{1,4})\s*([A-Ea-e])\b", txt):
            try:
                numero = int((m.group(1) or "").strip())
            except Exception:
                continue
            if numero <= 0:
                continue
            clave = (m.group(2) or "").strip().upper()
            if clave in {"A", "B", "C", "D", "E"}:
                pairs[numero] = clave
        return pairs

    def _format_claves_pairs(self, pairs: Dict[int, str]) -> str:
        if not pairs:
            return ""
        chunks = [f"{n}.{pairs[n]}" for n in sorted(pairs.keys())]
        return "; ".join(chunks)

    def _build_claves_ocr_prompt(self) -> str:
        return (
            "Extrae TODAS las claves de respuesta visibles en la imagen.\n"
            "Devuelve SOLO pares numero-clave con formato: 1.A; 2.C; 3.D\n"
            "Reglas:\n"
            "- Incluye desde la primera hasta la ultima clave visible (no omitas las iniciales).\n"
            "- Clave siempre A, B, C, D o E.\n"
            "- No agregues explicaciones, markdown ni texto extra.\n"
            "- Si no detectas claves, devuelve texto vacio."
        )

    def _build_claves_ocr_raw_prompt(self) -> str:
        return (
            "Transcribe literalmente TODO el texto visible de la imagen, de arriba hacia abajo.\n"
            "No resumas ni omitas lineas.\n"
            "Sin explicaciones ni markdown."
        )

    def _extract_claves_from_image_hf(self, image_path: Path) -> str:
        token = self._resolve_hf_token()
        if not token:
            raise Exception("Falta HF token. Define HF_TOKEN en .env.local/.env o en el campo HF token.")

        model = (self.model_var.get() or DEFAULT_HF_VISION_MODEL).strip()
        timeout_s = max(30, int(self.timeout_var.get() or 180))
        retries = max(0, int(self.retries_var.get() or 2))
        prompt_pairs = self._build_claves_ocr_prompt()
        prompt_raw = self._build_claves_ocr_raw_prompt()

        if self._use_hf_native_ocr_endpoint(model):
            # Endpoint nativo no soporta prompt de chat; usamos OCR bruto y parser local.
            raw_native = self._transcribir_hf_native_endpoint(timeout_s=timeout_s, label="claves", path=image_path)
            parsed_native = self._parse_claves_text(raw_native)
            if parsed_native:
                return self._format_claves_pairs(parsed_native)
            return raw_native

        base_url = self._resolve_hf_base_url()
        client = OpenAI(base_url=base_url, api_key=token, timeout=timeout_s)
        img_url = self._encode_image_data_url(image_path)

        def call_prompt(prompt_text: str, label_suffix: str, max_tokens: int) -> str:
            text = ""
            last_exc: Exception | None = None
            for attempt in range(retries + 1):
                try:
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt_text},
                                    {"type": "image_url", "image_url": {"url": img_url}},
                                ],
                            }
                        ],
                        temperature=0,
                        max_tokens=max_tokens,
                    )
                    content = resp.choices[0].message.content if resp and resp.choices else ""
                    text = self._extract_chat_text(content)
                    self._log_usage(
                        provider=PROVIDER_HF,
                        model=model,
                        label=f"{image_path.name} [{label_suffix}]",
                        usage=getattr(resp, "usage", None),
                    )
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    msg = str(exc).lower()
                    is_retryable = ("timeout" in msg) or ("timed out" in msg) or ("rate" in msg) or ("429" in msg)
                    if attempt < retries and is_retryable:
                        time.sleep(min(8, 2 ** attempt))
                        continue
                    break
            if last_exc is not None:
                raise Exception(f"{label_suffix}: {last_exc}")
            return (text or "").strip()

        errors: List[str] = []
        outputs: List[str] = []
        for prompt_text, label_suffix, max_t in (
            (prompt_pairs, "keys-ocr-pairs", 420),
            (prompt_raw, "keys-ocr-raw", 1000),
        ):
            try:
                out = call_prompt(prompt_text, label_suffix, max_t)
            except Exception as exc:
                errors.append(str(exc))
                continue
            if out:
                outputs.append(out)

        if not outputs:
            raise Exception(
                "No se pudieron extraer claves desde imagen."
                + (f" Detalle: {' | '.join(errors)}" if errors else "")
            )

        merged: Dict[int, str] = {}
        for out in outputs:
            parsed = self._parse_claves_text(out)
            for n, c in parsed.items():
                merged[n] = c

        if merged:
            normalized = self._format_claves_pairs(merged)
            self._log(f"Paso 3: claves OCR detectadas={len(merged)} ({normalized[:120]}...)")
            return normalized
        return outputs[0]

    def _dictar_claves_microfono(self) -> str:
        try:
            import speech_recognition as sr  # type: ignore
        except Exception:
            raise Exception(
                "Falta dependencia para dictado por microfono.\n"
                "Instala: python -m pip install SpeechRecognition pyaudio"
            )

        rec = sr.Recognizer()
        with sr.Microphone() as source:
            self._log("Paso 3: escuchando dictado de claves (microfono)...")
            rec.adjust_for_ambient_noise(source, duration=0.6)
            audio = rec.listen(source, timeout=10, phrase_time_limit=30)

        try:
            spoken = rec.recognize_google(audio, language="es-ES")
        except sr.UnknownValueError:
            raise Exception("No se entendio el dictado. Intenta hablar mas claro.")
        except sr.RequestError as exc:
            raise Exception(f"Error del servicio de reconocimiento: {exc}")
        return (spoken or "").strip()

    def _apply_claves_to_items(
        self,
        *,
        claves_map: Dict[int, str],
        add_solucion_tag: bool,
        solucion_template: str,
    ) -> Dict[str, int]:
        updated = 0
        updated_sol = 0
        present_nums: Set[int] = set()
        out_items: List[Tuple[str, str, List[str]]] = []

        for entry in self._items:
            if len(entry) < 2:
                continue
            archivo = entry[0]
            item_txt = entry[1]
            imgs = entry[2] if len(entry) > 2 else []
            numero = self.controller.parsear_numero_original(item_txt) or 0
            if numero > 0:
                present_nums.add(numero)

            if numero > 0 and numero in claves_map:
                clave = (claves_map.get(numero) or "").strip().upper()
                if clave in {"A", "B", "C", "D", "E"}:
                    item_txt = self._upsert_named_tag(
                        item_txt,
                        tag_name="Clave",
                        tag_value=clave,
                        tag_re=TAG_CLAVE_RE,
                    )
                    updated += 1
                    if add_solucion_tag:
                        base = (solucion_template or "pendiente").strip()
                        base = base.replace("{n}", str(numero)).replace("{clave}", clave)
                        item_txt = self._upsert_named_tag(
                            item_txt,
                            tag_name="Solucion",
                            tag_value=base,
                            tag_re=TAG_SOLUCION_RE,
                        )
                        updated_sol += 1

            out_items.append((archivo, item_txt, list(imgs or [])))

        self._items = out_items
        self._render_output_from_items()

        missing = len([n for n in claves_map.keys() if n not in present_nums])
        return {
            "updated_claves": int(updated),
            "updated_solucion": int(updated_sol),
            "missing_items": int(missing),
            "total_pairs": int(len(claves_map)),
        }

    def _open_step3_dialog(self) -> None:
        if self._transcribing:
            messagebox.showwarning("Paso 3", "Espera a que termine el Paso 2 antes de aplicar claves.")
            return
        self._sync_items_from_output_text()
        if not self._items:
            messagebox.showwarning("Paso 3", "Aun no hay items. Ejecuta primero el Paso 2.")
            return

        dlg = tk.Toplevel(self)
        dlg.title("Paso 3 (opcional) - Claves y solucion")
        dlg.geometry("760x520")
        dlg.minsize(640, 420)
        dlg.transient(self)

        frame = ttk.Frame(dlg, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text="Ingresa claves en formato: 4.C; 5.D; 6.A (tambien acepta lineas separadas).",
            style="SubHeader.TLabel",
        ).pack(anchor="w")

        txt = tk.Text(
            frame,
            height=10,
            bg=self.palette["surface"],
            fg=self.palette["text"],
            insertbackground=self.palette["text"],
            relief="solid",
            bd=1,
            wrap="word",
            highlightthickness=1,
            highlightbackground=self.palette["border"],
        )
        txt.pack(fill="both", expand=True, pady=(8, 8))
        if self._step3_last_claves_input:
            txt.insert("1.0", self._step3_last_claves_input)

        tools = ttk.Frame(frame)
        tools.pack(fill="x", pady=(0, 8))

        def append_input(extra: str) -> None:
            extra_txt = (extra or "").strip()
            if not extra_txt:
                return
            current = (txt.get("1.0", "end") or "").strip()
            if current:
                txt.insert("end", "\n" + extra_txt)
            else:
                txt.insert("1.0", extra_txt)

        def on_ocr_image() -> None:
            path = filedialog.askopenfilename(
                parent=dlg,
                title="Selecciona imagen con claves",
                filetypes=[("Imagenes", "*.png;*.jpg;*.jpeg;*.webp;*.bmp"), ("Todos", "*.*")],
            )
            if not path:
                return
            src = Path(path)
            self._log(f"Paso 3: OCR de claves desde {src.name}...")

            def worker() -> None:
                try:
                    raw = self._extract_claves_from_image_hf(src)
                except Exception as exc:
                    self.after(0, lambda e=exc: messagebox.showerror("Paso 3", str(e)))
                    return
                self.after(0, lambda t=raw: append_input(t))
                self.after(0, lambda t=raw: self._log(f"Paso 3: OCR claves => {t[:200]}"))

            threading.Thread(target=worker, daemon=True).start()

        def on_dictar() -> None:
            self._log("Paso 3: iniciando dictado por microfono...")

            def worker() -> None:
                try:
                    spoken = self._dictar_claves_microfono()
                except Exception as exc:
                    self.after(0, lambda e=exc: messagebox.showerror("Paso 3", str(e)))
                    return
                self.after(0, lambda t=spoken: append_input(t))
                self.after(0, lambda t=spoken: self._log(f"Paso 3: dictado capturado => {t}"))

            threading.Thread(target=worker, daemon=True).start()

        ttk.Button(tools, text="OCR imagen de claves...", command=on_ocr_image, style="Ghost.TButton").pack(side="left")
        ttk.Button(tools, text="Dictar microfono", command=on_dictar, style="Ghost.TButton").pack(
            side="left", padx=(8, 0)
        )

        sol_frame = ttk.Frame(frame)
        sol_frame.pack(fill="x", pady=(0, 8))
        ttk.Checkbutton(
            sol_frame,
            text="Agregar etiqueta [[Solucion=...]] a items con clave",
            variable=self.step3_agregar_solucion_var,
        ).pack(side="left")
        ttk.Entry(sol_frame, textvariable=self.step3_solucion_var, width=34).pack(side="left", padx=(8, 0))
        ttk.Label(sol_frame, text="(usa {n} y {clave})").pack(side="left", padx=(8, 0))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")

        def on_apply() -> None:
            self._sync_items_from_output_text()
            raw = (txt.get("1.0", "end") or "").strip()
            if not raw:
                messagebox.showwarning("Paso 3", "Ingresa claves o usa OCR/dictado.")
                return
            claves_map = self._parse_claves_text(raw)
            if not claves_map:
                messagebox.showwarning("Paso 3", "No se pudieron parsear claves. Formato esperado: 4.C; 5.D; 6.A")
                return
            self._step3_last_claves_input = raw
            stats = self._apply_claves_to_items(
                claves_map=claves_map,
                add_solucion_tag=bool(self.step3_agregar_solucion_var.get()),
                solucion_template=(self.step3_solucion_var.get() or "").strip(),
            )
            self._log(
                "Paso 3 aplicado: "
                f"claves={stats['updated_claves']}/{stats['total_pairs']}, "
                f"solucion={stats['updated_solucion']}, "
                f"sin_item={stats['missing_items']}."
            )
            messagebox.showinfo(
                "Paso 3",
                "Claves aplicadas.\n"
                f"Items con clave: {stats['updated_claves']}\n"
                f"Items con solucion: {stats['updated_solucion']}\n"
                f"Numeros sin item: {stats['missing_items']}",
            )

        ttk.Button(buttons, text="Aplicar claves", command=on_apply, style="Accent.TButton").pack(side="left")
        ttk.Button(buttons, text="Cerrar", command=dlg.destroy, style="Ghost.TButton").pack(side="left", padx=(8, 0))

    def _copiar_salida(self) -> None:
        txt = (self.txt_out.get("1.0", "end") or "").strip()
        if not txt:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(txt + "\n")
            self.update_idletasks()
        except Exception:
            pass
        messagebox.showinfo("Copiar", "Salida copiada al portapapeles.")

    def _get_raw_ocr_view_labels(self) -> List[str]:
        selected_indices = list(self.list_files.curselection())
        if selected_indices:
            return [str(self.list_files.get(i)) for i in selected_indices]
        labels: List[str] = []
        total = int(self.list_files.size())
        for i in range(total):
            labels.append(str(self.list_files.get(i)))
        if labels:
            return labels
        return sorted(str(k) for k in self._ocr_raw_first_by_label.keys())

    def _parse_structured_items_from_raw(self, raw_text: str) -> List[Dict[str, Any]]:
        raw = self.controller.normalizar_item_una_linea(self._decode_scan_escapes(raw_text or ""))
        if not raw:
            return []
        raw = re.sub(r"(?is)\[FUSION_APLICADA\].*$", "", raw).strip()
        if not raw:
            return []

        block_re = re.compile(
            r"(?is)\bITEM\s*:\s*(?P<num>\d+)\s*"
            r"ENUNCIADO\s*:\s*(?P<enu>.*?)\s*"
            r"(?:FIGURA\s*:\s*(?P<fig>SI|NO)\s*)?"
            r"OPCIONES\s*:\s*"
            r"A\)\s*(?P<A>.*?)\s*"
            r"B\)\s*(?P<B>.*?)\s*"
            r"C\)\s*(?P<C>.*?)\s*"
            r"D\)\s*(?P<D>.*?)\s*"
            r"E\)\s*(?P<E>.*?)\s*"
            r"ENDITEM"
        )

        out: List[Dict[str, Any]] = []
        for m in block_re.finditer(raw):
            try:
                num = int(m.group("num"))
            except Exception:
                continue
            enu = self._normalize_merge_note_text(m.group("enu") or "", max_len=0)
            fig = "SI" if str(m.group("fig") or "").strip().upper() == "SI" else "NO"
            options = {
                "A": self._clean_summary_option_value(m.group("A") or ""),
                "B": self._clean_summary_option_value(m.group("B") or ""),
                "C": self._clean_summary_option_value(m.group("C") or ""),
                "D": self._clean_summary_option_value(m.group("D") or ""),
                "E": self._clean_summary_option_value(m.group("E") or ""),
            }
            out.append({"num": num, "enunciado": enu or "...", "figura": fig, "options": options})
        if out:
            return out

        # Fallback: parse direct scan lines (\item...) produced by vision-direct mode.
        direct_items = self._extract_direct_scan_items(raw)
        for item_text in direct_items:
            item_norm = self.controller.normalizar_item_una_linea(self._decode_scan_escapes(item_text or ""))
            if not item_norm:
                continue
            num = self.controller.parsear_numero_original(item_norm) or 0
            if num <= 0:
                continue
            body = re.sub(
                r"^\s*\\item\s*\[\s*\\textbf\{\s*\d+\.?\s*\}\s*\]\s*",
                "",
                item_norm,
                flags=re.IGNORECASE,
            ).strip()
            enu, opts_loose = self._extract_options_loose(body)
            options = {
                "A": self._clean_summary_option_value(str(opts_loose.get("A", "...") or "...")),
                "B": self._clean_summary_option_value(str(opts_loose.get("B", "...") or "...")),
                "C": self._clean_summary_option_value(str(opts_loose.get("C", "...") or "...")),
                "D": self._clean_summary_option_value(str(opts_loose.get("D", "...") or "...")),
                "E": self._clean_summary_option_value(str(opts_loose.get("E", "...") or "...")),
            }
            fig = "SI" if self._has_image_marker(item_norm) else "NO"
            out.append(
                {
                    "num": int(num),
                    "enunciado": self._clean_summary_continuation_text(enu or "...") or "...",
                    "figura": fig,
                    "options": options,
                }
            )
        return out

    def _clean_summary_continuation_text(self, text: str) -> str:
        value = self._normalize_merge_note_text(text or "", max_len=0)
        if not value:
            return ""
        # Drop markdown code fences often injected by model.
        value = re.sub(r"(?is)```(?:json)?\s*.*?```", " ", value)
        # Drop leaked JSON payload blocks used in intermediate passes.
        value = re.sub(r'(?is)\{\s*"continuation"\s*:.*?\}', " ", value)
        value = re.sub(r'(?is)\{\s*"A"\s*:.*?\}', " ", value)
        # Remove leaked structured headers that sometimes appear inside continuation payload.
        value = re.sub(r"(?i)\bITEM\s*:\s*\d+\b", " ", value)
        value = re.sub(r"(?i)\bENUNCIADO\s*:\s*", " ", value)
        value = re.sub(r"(?i)\bFIGURA\s*:\s*(?:SI|NO)\b", " ", value)
        value = re.sub(r"(?i)\bOPCIONES\s*:\s*", " ", value)
        value = re.sub(r"(?i)\bENDITEM\b", " ", value)
        # Drop leading option labels accidentally embedded into continuation lines.
        value = re.sub(r"(?i)^\s*[A-E]\)\s*", " ", value)
        # Remove loose separators left by merge notes.
        value = re.sub(r"\s*\|\s*", " ", value)
        value = re.sub(r"\s+", " ", value).strip()
        return value

    def _clean_summary_option_value(self, text: str) -> str:
        value = self._normalize_merge_note_text(text or "", max_len=0)
        if not value:
            return ""
        value = re.sub(r"(?is)```(?:json)?\s*.*?```", " ", value)
        value = re.sub(r'(?is)\{\s*"continuation"\s*:.*?\}', " ", value)
        value = re.sub(r'(?is)\{\s*"A"\s*:.*?\}', " ", value)
        value = re.sub(r"\s*\|\s*$", "", value)
        value = re.sub(r"\s+", " ", value).strip()
        return value

    def _summary_item_is_complete(self, item: Dict[str, Any]) -> bool:
        opts = item.get("options", {}) if isinstance(item, dict) else {}
        for label in ("A", "B", "C", "D", "E"):
            val = str(opts.get(label, "") or "").strip()
            if not val or self._is_placeholder_option_value(val):
                return False
        return True

    def _summary_merge_continuation(self, item: Dict[str, Any], continuation: str) -> None:
        add = self._clean_summary_continuation_text(continuation or "")
        if not add:
            return
        current = self._clean_summary_continuation_text(str(item.get("enunciado", "") or ""))
        if not current:
            item["enunciado"] = add
            return
        base_key = self._norm_key(current)
        add_key = self._norm_key(add)
        if add_key and add_key in base_key:
            return
        item["enunciado"] = f"{current} {add}".strip()

    def _summary_merge_options(self, item: Dict[str, Any], extra_options: Dict[str, str]) -> None:
        if not extra_options:
            return
        opts = item.setdefault("options", {})
        for label in ("A", "B", "C", "D", "E"):
            val = self._clean_summary_option_value(str(extra_options.get(label, "") or ""))
            if not val:
                continue
            current = str(opts.get(label, "") or "").strip()
            if (not current) or self._is_placeholder_option_value(current):
                opts[label] = val

    def _build_structured_item_summary(self, item: Dict[str, Any]) -> str:
        num = int(item.get("num", 0) or 0)
        figura = "SI" if str(item.get("figura", "NO") or "").strip().upper() == "SI" else "NO"
        enunciado = self._clean_summary_continuation_text(str(item.get("enunciado", "") or "")) or "..."
        options = item.get("options", {}) if isinstance(item, dict) else {}

        rows = [
            f"ITEM: {num if num > 0 else '?'}",
            f"ENUNCIADO: {enunciado}",
            f"FIGURA: {figura}",
            "OPCIONES:",
        ]
        for label in ("A", "B", "C", "D", "E"):
            val = self._clean_summary_option_value(str(options.get(label, "") or ""))
            rows.append(f"{label}) {val if val else '...'}")
        rows.append("ENDITEM")
        return "\n".join(rows)

    def _build_reasoning_input(self, block: Dict[str, Any]) -> str:
        enunciado = self._clean_summary_continuation_text(str((block or {}).get("enunciado", "") or ""))
        if not enunciado:
            enunciado = "..."
        return f"ENUNCIADO: {enunciado}"

    def _figure_flags_from_unified_items(self) -> Dict[int, bool]:
        flags: Dict[int, bool] = {}
        for _archivo_origen, item_text, image_paths in self._items:
            item = self.controller.normalizar_item_una_linea(self._decode_scan_escapes(item_text or ""))
            num = self.controller.parsear_numero_original(item) or self._extract_structured_item_number(item) or 0
            if num <= 0:
                continue
            has_fig = self._has_image_marker(item) or bool(image_paths)
            if has_fig:
                flags[num] = True
            else:
                flags.setdefault(num, False)
        return flags

    def _log_figure_assignment_debug(self, message: str) -> None:
        """
        Avoid touching Tk widgets from worker threads.
        Figure-assignment diagnostics are only emitted when caller runs on main thread.
        """
        try:
            if threading.current_thread() is threading.main_thread():
                self._log(str(message or ""))
        except Exception:
            pass

    def _compute_figure_flags_sequence(self, labels: List[str]) -> Dict[int, bool]:
        """
        Sequence-aware figure assignment for OCR-unified summary.
        Balance policy:
        - if there is a pending incomplete item and current image starts with continuation/options,
          consume one segment for that pending item;
        - remaining segments are assigned to parsed items by hint score,
          with fallback only when there is a single parsed item.
        """
        flags: Dict[int, bool] = {}
        pending_num: Optional[int] = None

        for lb in labels:
            label = str(lb or "").strip()
            if not label:
                continue

            raw = str(self._ocr_raw_first_by_label.get(label, "") or "").strip()
            merge_applied = str(self._ocr_merge_applied_by_label.get(label, "") or "").strip()

            src = self._file_map.get(label)
            seg_count = 0
            if src is not None:
                try:
                    seg_count = len(self._get_segments_v2_for_source(src))
                except Exception:
                    seg_count = 0

            if not raw and not merge_applied:
                if seg_count <= 0:
                    self._log_figure_assignment_debug(
                        f"{label}: figura_no_asignada -> imagen {label} (sin segmentos)"
                    )
                else:
                    self._log_figure_assignment_debug(
                        f"{label}: figura_no_asignada -> imagen {label} (sin OCR util)"
                    )
                continue

            cont, lead_opts, cleaned = self._extract_structured_leading_payload(raw)
            parsed_items = self._parse_structured_items_from_raw(cleaned if cleaned else raw)

            has_merge_cont = bool(
                re.search(r"(?i)prepass_continuacion_inicio|continuacion_", merge_applied)
            )
            has_merge_opts = bool(
                re.search(r"(?i)prepass_claves_inicio|claves_|opciones_", merge_applied)
            )
            has_continuation = bool(cont) or bool(lead_opts) or has_merge_cont or has_merge_opts

            assigned_any = False

            if pending_num is not None and seg_count > 0 and has_continuation:
                flags[pending_num] = True
                seg_count -= 1
                assigned_any = True
                self._log_figure_assignment_debug(
                    f"{label}: figura_asignada_pending -> item {pending_num} (continuacion detectada)"
                )

            if seg_count > 0 and parsed_items:
                item_count = len(parsed_items)
                scored: List[Tuple[int, int]] = []
                for pos, it in enumerate(parsed_items, start=1):
                    enu = self._decode_scan_escapes(str(it.get("enunciado", "") or ""))
                    score = self._item_image_hint_score(enu)
                    if score > 0:
                        scored.append((score, pos))
                scored.sort(key=lambda t: (-t[0], t[1]))

                chosen_pos: List[int] = []
                assign_mode = "hint"
                for _score, pos in scored:
                    if len(chosen_pos) >= seg_count:
                        break
                    if pos not in chosen_pos:
                        chosen_pos.append(pos)

                if not chosen_pos and item_count == 1:
                    chosen_pos.append(1)
                    assign_mode = "fallback"

                for pos in chosen_pos:
                    if pos < 1 or pos > item_count:
                        continue
                    num = int(parsed_items[pos - 1].get("num", 0) or 0)
                    if num <= 0:
                        continue
                    flags[num] = True
                    assigned_any = True
                    reason = "hint score" if assign_mode == "hint" else "fallback unico item"
                    self._log_figure_assignment_debug(
                        f"{label}: figura_asignada_item -> item {num} ({reason})"
                    )

                seg_count = max(0, seg_count - len(chosen_pos))

            if not assigned_any:
                if seg_count <= 0:
                    self._log_figure_assignment_debug(
                        f"{label}: figura_no_asignada -> imagen {label} (sin segmentos)"
                    )
                else:
                    self._log_figure_assignment_debug(
                        f"{label}: figura_no_asignada -> imagen {label} (sin candidato)"
                    )

            for it in parsed_items:
                num = int(it.get("num", 0) or 0)
                if num <= 0:
                    continue
                pending_num = num if not self._summary_item_is_complete(it) else None

        return flags

    def _figure_flags_from_raw_labels(self, labels: List[str]) -> Dict[int, bool]:
        """
        Infer figure presence from segmentation + OCR-raw structured items.
        Conservative behavior for multi-item images:
        - prioritize enunciados with image hints (grafico/figura/diagrama)
        - if no hints, do not assign automatically in multi-item pages
        - single-item page with >=1 segment -> mark as figure
        """
        flags: Dict[int, bool] = {}
        for lb in labels:
            raw = str(self._ocr_raw_first_by_label.get(lb, "") or "").strip()
            if not raw:
                continue
            cont, lead_opts, cleaned = self._extract_structured_leading_payload(raw)
            parsed_items = self._parse_structured_items_from_raw(cleaned if cleaned else raw)
            if not parsed_items:
                continue
            src = self._file_map.get(lb)
            if src is None:
                continue
            try:
                seg_count = len(self._get_segments_v2_for_source(src))
            except Exception:
                seg_count = 0
            if seg_count <= 0:
                continue

            item_count = len(parsed_items)
            chosen_pos: List[int] = []
            scored: List[Tuple[int, int]] = []
            for pos, it in enumerate(parsed_items, start=1):
                enu = str(it.get("enunciado", "") or "")
                score = self._item_image_hint_score(self._decode_scan_escapes(enu))
                if score > 0:
                    scored.append((score, pos))
            scored.sort(key=lambda t: (-t[0], t[1]))
            for _score, pos in scored:
                if len(chosen_pos) >= seg_count:
                    break
                if pos not in chosen_pos:
                    chosen_pos.append(pos)
            if not chosen_pos and item_count == 1:
                chosen_pos.append(1)

            for pos in chosen_pos:
                if pos < 1 or pos > item_count:
                    continue
                num = int(parsed_items[pos - 1].get("num", 0) or 0)
                if num > 0:
                    flags[num] = True
        return flags

    def _build_ocr_item_summary_text(self, labels: Optional[List[str]] = None) -> str:
        if labels is None:
            labels = self._get_raw_ocr_view_labels()
        else:
            labels = [str(lb) for lb in labels if str(lb).strip()]
            # Mantener orden y quitar duplicados.
            seen: Set[str] = set()
            ordered: List[str] = []
            for lb in labels:
                if lb in seen:
                    continue
                seen.add(lb)
                ordered.append(lb)
            labels = ordered
        if not labels:
            return ""
        ordered_nums: List[int] = []
        summary_by_num: Dict[int, Dict[str, Any]] = {}
        pending_num: Optional[int] = None

        for lb in labels:
            raw = str(self._ocr_raw_first_by_label.get(lb, "") or "").strip()
            merge_applied = str(self._ocr_merge_applied_by_label.get(lb, "") or "").strip()
            if not raw and not merge_applied:
                continue

            cont, lead_opts, cleaned = self._extract_structured_leading_payload(raw)
            if pending_num is not None and pending_num in summary_by_num:
                target = summary_by_num[pending_num]
                if cont:
                    self._summary_merge_continuation(target, cont)
                if lead_opts:
                    self._summary_merge_options(target, lead_opts)
                if self._summary_item_is_complete(target):
                    pending_num = None

            parsed_items = self._parse_structured_items_from_raw(cleaned if cleaned else raw)
            for it in parsed_items:
                num = int(it.get("num", 0) or 0)
                if num <= 0:
                    continue
                if num in summary_by_num:
                    target = summary_by_num[num]
                    if it.get("figura") == "SI":
                        target["figura"] = "SI"
                    self._summary_merge_continuation(target, str(it.get("enunciado", "") or ""))
                    self._summary_merge_options(target, it.get("options", {}))
                else:
                    summary_by_num[num] = it
                    ordered_nums.append(num)
                pending_num = num if not self._summary_item_is_complete(summary_by_num[num]) else None

            if merge_applied:
                for line in [ln.strip() for ln in merge_applied.splitlines() if ln.strip()]:
                    m = re.search(r"->\s*item\s*(\d+)\s*:\s*(.*)$", line)
                    if m:
                        try:
                            target_num = int(m.group(1))
                        except Exception:
                            target_num = pending_num or 0
                        payload = str(m.group(2) or "").strip()
                    else:
                        target_num = pending_num or 0
                        payload = line
                    if target_num <= 0:
                        continue
                    if target_num not in summary_by_num:
                        summary_by_num[target_num] = {
                            "num": target_num,
                            "enunciado": "...",
                            "figura": "NO",
                            "options": {"A": "...", "B": "...", "C": "...", "D": "...", "E": "..."},
                        }
                        ordered_nums.append(target_num)
                    target = summary_by_num[target_num]
                    payload_clean = re.sub(
                        r"(?is)^\s*(?:prepass_[^:]+|leading_[^:]+|continuacion_[^:]+|claves_[^:]+|opciones_[^:]+)\s*:\s*",
                        "",
                        payload,
                    ).strip()
                    enu, opts = self._extract_options_loose(payload_clean)
                    if opts:
                        self._summary_merge_options(target, opts)
                    if enu and not opts:
                        self._summary_merge_continuation(target, enu)
                    if target_num == pending_num and self._summary_item_is_complete(target):
                        pending_num = None

        blocks: List[str] = []
        figure_flags: Dict[int, bool] = {}
        sequence_flags = self._compute_figure_flags_sequence(labels)
        unified_flags = self._figure_flags_from_unified_items()
        for k, v in sequence_flags.items():
            if v:
                figure_flags[k] = True
            else:
                figure_flags.setdefault(k, False)
        for k, v in unified_flags.items():
            if v:
                figure_flags[k] = True
            else:
                figure_flags.setdefault(k, False)
        for num in ordered_nums:
            if figure_flags.get(num, False):
                summary_by_num[num]["figura"] = "SI"
            block = self._build_structured_item_summary(summary_by_num[num])
            if block:
                blocks.append(block)
        return "\n\n".join(blocks).strip()

    def _apply_two_pass_on_ocr_unified(
        self,
        *,
        provider: str,
        model: str,
        timeout_s: int,
        retries: int,
        tag_mode: str,
        catalog: Dict[str, Any],
        openai_client: Optional[OpenAI],
        hf_native_ocr_mode: bool,
        run_start_item_idx: int,
        fallback_path: Path,
        labels: Optional[List[str]] = None,
    ) -> Tuple[int, int]:
        summary_text = self._build_ocr_item_summary_text(labels)
        if not summary_text:
            return (0, 0)
        parsed = self._parse_structured_items_from_raw(summary_text)
        if not parsed:
            return (0, 0)

        # Conserva recortes ya generados en el lote actual por numero de item.
        image_paths_by_num: Dict[int, List[str]] = {}
        for _src, item_txt, image_paths in self._items[max(0, int(run_start_item_idx)) :]:
            num = self.controller.parsear_numero_original(str(item_txt or "")) or 0
            if num <= 0:
                continue
            bucket = image_paths_by_num.setdefault(num, [])
            for p in image_paths or []:
                sp = str(p or "").strip()
                if sp and sp not in bucket:
                    bucket.append(sp)

        unified_items: List[Tuple[str, str, List[str]]] = []
        llm_used_count = 0
        curso_hint = (self.curso_var.get() or "").strip()
        tema_hint = (self.tema_var.get() or "").strip()
        subtema_hint = (self.subtema_var.get() or "").strip()
        can_use_llm = bool(self.format_with_llm_var.get()) and (not hf_native_ocr_mode)

        for block in parsed:
            numero = int(block.get("num", 0) or 0)
            if numero <= 0:
                continue
            raw_struct = self._build_structured_item_summary(block)
            base_enu = self._clean_summary_continuation_text(str(block.get("enunciado", "") or "")) or "..."
            self._geometry_pass_by_label[f"ocr_unificado#{numero}"] = base_enu
            item = ""
            used_llm = False

            if can_use_llm and provider == PROVIDER_OPENAI and openai_client is not None:
                formatted = self._format_item_openai(
                    openai_client,
                    model=model,
                    timeout_s=timeout_s,
                    retries=retries,
                    label=f"ocr_unificado#{numero}",
                    raw_item=raw_struct,
                    curso_hint=curso_hint,
                    tema_hint=tema_hint,
                    subtema_hint=subtema_hint,
                )
                if formatted:
                    item = self._finalize_item_prompt_first(
                        formatted, path=fallback_path, fallback_numero=numero
                    )
                    used_llm = True
            elif can_use_llm and provider == PROVIDER_HF:
                formatted = self._format_item_hf(
                    model=model,
                    timeout_s=timeout_s,
                    retries=retries,
                    label=f"ocr_unificado#{numero}",
                    raw_item=raw_struct,
                    curso_hint=curso_hint,
                    tema_hint=tema_hint,
                    subtema_hint=subtema_hint,
                )
                if formatted:
                    item = self._finalize_item_prompt_first(
                        formatted, path=fallback_path, fallback_numero=numero
                    )
                    used_llm = True

            if not item:
                enunciado = str(block.get("enunciado", "") or "").strip()
                options = block.get("options", {}) if isinstance(block, dict) else {}
                item = self._build_scan_item_strict(
                    numero=numero,
                    enunciado=enunciado,
                    options=options if isinstance(options, dict) else {},
                )

            reasoning_payload = self._geometry_pass_payload_by_label.get(f"ocr_unificado#{numero}", {})
            item = self._apply_reasoning_wrap_hints(item, reasoning_payload)
            item = self._apply_global_math_conventions(item)
            item = self._apply_geometry_conventions(item)
            item = self._normalize_math_delimiters(item)

            figura = str(block.get("figura", "NO") or "").strip().upper() == "SI"
            if figura and not self._has_image_marker(item):
                item = self._insert_image_marker(item, path=fallback_path, numero=numero)
                item = self._move_image_marker_before_options(item)

            item = self._apply_metadata_mode(
                item=item,
                mode=tag_mode,
                provider=provider,
                model=model,
                timeout_s=timeout_s,
                retries=retries,
                label=f"ocr_unificado#{numero}",
                catalog=catalog,
                openai_client=openai_client if provider == PROVIDER_OPENAI else None,
            )
            item = self._move_image_marker_before_options(item)
            item = self.controller.normalizar_item_una_linea(item)

            if used_llm:
                llm_used_count += 1

            unified_items.append(
                (
                    "ocr_unificado",
                    item,
                    list(image_paths_by_num.get(numero, [])),
                )
            )

        if not unified_items:
            return (0, 0)

        prefix = self._items[: max(0, int(run_start_item_idx))]
        self._items = prefix + unified_items
        self._render_output_from_items()
        return (len(unified_items), llm_used_count)

    def _open_ocr_raw_view(self) -> None:
        labels = self._get_raw_ocr_view_labels()
        if not labels:
            messagebox.showwarning("OCR crudo", "No hay imagenes para mostrar.")
            return
        if not self._ocr_raw_first_by_label:
            messagebox.showwarning(
                "OCR crudo",
                "Aun no hay respuestas OCR registradas. Ejecuta Paso 2 primero.",
            )
            return

        dlg = tk.Toplevel(self)
        dlg.title("OCR crudo - salida fiel del modelo de vision por imagen")
        dlg.geometry("1080x760")
        dlg.minsize(860, 520)
        try:
            dlg.transient(self)
        except Exception:
            pass

        top = ttk.Frame(dlg)
        top.pack(fill="x", padx=12, pady=(10, 8))
        scope = "seleccion" if self.list_files.curselection() else "lote"
        available = sum(1 for lb in labels if str(self._ocr_raw_first_by_label.get(lb, "")).strip())
        ttk.Label(
            top,
            text=(
                f"OCR crudo fiel ({scope}) - imagenes: {len(labels)} | con respuesta: {available}"
            ),
            style="SubHeader.TLabel",
        ).pack(side="left")

        body = ttk.Frame(dlg)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        txt = tk.Text(
            body,
            wrap="word",
            bg=self.palette["surface"],
            fg=self.palette["text"],
            insertbackground=self.palette["text"],
            relief="solid",
            bd=1,
            highlightthickness=1,
            highlightbackground=self.palette["border"],
            highlightcolor=self.palette["border"],
            font=("Consolas", 10),
        )
        scr = ttk.Scrollbar(body, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=scr.set)
        txt.pack(side="left", fill="both", expand=True)
        scr.pack(side="right", fill="y")

        sections: List[str] = []
        for lb in labels:
            raw = str(self._ocr_raw_first_by_label.get(lb, "") or "").strip()
            if not raw:
                raw = "(sin respuesta OCR cruda para esta imagen)"
            section = f"{'=' * 24} {lb} {'=' * 24}\n{raw}"
            sections.append(section + "\n")
        txt.insert("1.0", "\n".join(sections).strip() + "\n")
        txt.configure(state="disabled")

        btns = ttk.Frame(dlg)
        btns.pack(fill="x", padx=12, pady=(0, 12))

        def on_copy() -> None:
            data = "\n".join(sections).strip()
            try:
                self.clipboard_clear()
                self.clipboard_append(data + "\n")
                self.update_idletasks()
                messagebox.showinfo("OCR crudo", "Texto OCR crudo copiado al portapapeles.")
            except Exception as exc:
                messagebox.showerror("OCR crudo", f"No se pudo copiar:\n{exc}")

        ttk.Button(btns, text="Copiar", command=on_copy, style="Ghost.TButton").pack(side="left")
        ttk.Button(btns, text="Cerrar", command=dlg.destroy, style="Secondary.TButton").pack(side="right")

    def _open_vision_model_raw_view(self) -> None:
        labels = self._get_raw_ocr_view_labels()
        if not labels:
            messagebox.showwarning("Modelo puro", "No hay imagenes para mostrar.")
            return
        if not self._ocr_raw_first_by_label:
            messagebox.showwarning(
                "Modelo puro",
                "Aun no hay respuestas del modelo registradas. Ejecuta Paso 2 primero.",
            )
            return

        dlg = tk.Toplevel(self)
        dlg.title("Modelo puro - respuesta directa del modelo de vision por imagen")
        dlg.geometry("1080x760")
        dlg.minsize(860, 520)
        try:
            dlg.transient(self)
        except Exception:
            pass

        top = ttk.Frame(dlg)
        top.pack(fill="x", padx=12, pady=(10, 8))
        scope = "seleccion" if self.list_files.curselection() else "lote"
        available = sum(1 for lb in labels if str(self._ocr_raw_first_by_label.get(lb, "")).strip())
        ttk.Label(
            top,
            text=f"Respuesta directa del modelo ({scope}) - imagenes: {len(labels)} | con respuesta: {available}",
            style="SubHeader.TLabel",
        ).pack(side="left")

        body = ttk.Frame(dlg)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        txt = tk.Text(
            body,
            wrap="word",
            bg=self.palette["surface"],
            fg=self.palette["text"],
            insertbackground=self.palette["text"],
            relief="solid",
            bd=1,
            highlightthickness=1,
            highlightbackground=self.palette["border"],
            highlightcolor=self.palette["border"],
            font=("Consolas", 10),
        )
        scr = ttk.Scrollbar(body, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=scr.set)
        txt.pack(side="left", fill="both", expand=True)
        scr.pack(side="right", fill="y")

        sections: List[str] = []
        for lb in labels:
            raw = str(self._ocr_raw_first_by_label.get(lb, "") or "").strip()
            if not raw:
                raw = "(sin respuesta del modelo para esta imagen)"
            sections.append(f"{'=' * 24} {lb} {'=' * 24}\n{raw}\n")

        txt.insert("1.0", "\n".join(sections).strip() + "\n")
        txt.configure(state="disabled")

        btns = ttk.Frame(dlg)
        btns.pack(fill="x", padx=12, pady=(0, 12))

        def on_copy() -> None:
            data = "\n".join(sections).strip()
            try:
                self.clipboard_clear()
                self.clipboard_append(data + "\n")
                self.update_idletasks()
                messagebox.showinfo("Modelo puro", "Texto copiado al portapapeles.")
            except Exception as exc:
                messagebox.showerror("Modelo puro", f"No se pudo copiar:\n{exc}")

        ttk.Button(btns, text="Copiar", command=on_copy, style="Ghost.TButton").pack(side="left")
        ttk.Button(btns, text="Cerrar", command=dlg.destroy, style="Secondary.TButton").pack(side="right")

    def _open_geometry_pass_view(self) -> None:
        if not self._geometry_pass_by_label:
            messagebox.showwarning(
                "Pasada 1",
                "Aun no hay resultados de razonamiento. Ejecuta Aplicar razonamiento primero.",
            )
            return

        selected_indices = list(self.list_files.curselection())
        selected_labels = [str(self.list_files.get(i)) for i in selected_indices] if selected_indices else []

        entries: List[Tuple[str, str]] = []
        if selected_labels:
            for key, value in self._geometry_pass_by_label.items():
                if key.startswith("ocr_unificado#") or key.startswith("ocr_modificado#"):
                    entries.append((str(key), str(value)))
                    continue
                for lb in selected_labels:
                    if key == lb or key.startswith(f"{lb}#"):
                        entries.append((str(key), str(value)))
                        break
        else:
            entries = [(str(k), str(v)) for k, v in self._geometry_pass_by_label.items()]

        if not entries:
            messagebox.showwarning(
                "Pasada 1",
                "No hay resultados de pasada 1 para la seleccion actual.",
            )
            return

        # Prioridad: mostrar la salida real del primer pase del modelo.
        p1_entries = [it for it in entries if "[modelo-p1]" in str(it[0]).strip().lower()]
        if p1_entries:
            entries = p1_entries

        def _sort_key(item: Tuple[str, str]) -> Tuple[str, int, str]:
            key = item[0]
            k = str(key or "")
            scope_match = re.search(r"\b(ocr_unificado|ocr_modificado)#(\d+)", k, re.IGNORECASE)
            scope = (scope_match.group(1).lower() if scope_match else k.split("#", 1)[0].lower())
            num = int(scope_match.group(2)) if scope_match else 0
            seq_match = re.search(r"@(\d+)", k)
            seq = int(seq_match.group(1)) if seq_match else 0
            return (scope, num, seq, k.lower())

        entries.sort(key=_sort_key)
        sections = [f"{'=' * 24} {k} {'=' * 24}\n{v}\n" for k, v in entries]

        dlg = tk.Toplevel(self)
        dlg.title("Pasada 1 - diagnostico de razonamiento por item")
        dlg.geometry("1080x760")
        dlg.minsize(860, 520)
        try:
            dlg.transient(self)
        except Exception:
            pass

        top = ttk.Frame(dlg)
        top.pack(fill="x", padx=12, pady=(10, 8))
        scope = "seleccion" if selected_labels else "lote"
        ttk.Label(
            top,
            text=f"Pasada 1 (razonamiento, {scope}) - items: {len(entries)}",
            style="SubHeader.TLabel",
        ).pack(side="left")

        body = ttk.Frame(dlg)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        txt = tk.Text(
            body,
            wrap="word",
            bg=self.palette["surface"],
            fg=self.palette["text"],
            insertbackground=self.palette["text"],
            relief="solid",
            bd=1,
            highlightthickness=1,
            highlightbackground=self.palette["border"],
            highlightcolor=self.palette["border"],
            font=("Consolas", 10),
        )
        scr = ttk.Scrollbar(body, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=scr.set)
        txt.pack(side="left", fill="both", expand=True)
        scr.pack(side="right", fill="y")
        text = "\n".join(sections).strip() + "\n"
        txt.insert("1.0", text)
        txt.configure(state="disabled")

        btns = ttk.Frame(dlg)
        btns.pack(fill="x", padx=12, pady=(0, 12))

        def on_copy() -> None:
            try:
                self.clipboard_clear()
                self.clipboard_append(text)
                self.update_idletasks()
                messagebox.showinfo("Pasada 1", "Diagnostico de razonamiento copiado al portapapeles.")
            except Exception as exc:
                messagebox.showerror("Pasada 1", f"No se pudo copiar:\n{exc}")

        ttk.Button(btns, text="Copiar", command=on_copy, style="Ghost.TButton").pack(side="left")
        ttk.Button(btns, text="Cerrar", command=dlg.destroy, style="Secondary.TButton").pack(side="right")

    def _open_ocr_item_summary_view(self) -> None:
        text = self._build_ocr_item_summary_text()
        if not text:
            messagebox.showwarning(
                "Fusion de problemas",
                "Aun no hay respuestas OCR del modelo de vision para unificar. Ejecuta Paso 2 primero.",
            )
            return
        count = len(re.findall(r"(?m)^ITEM:\s*\d+", text))

        dlg = tk.Toplevel(self)
        dlg.title("Fusion de problemas (desde OCR crudo)")
        dlg.geometry("1080x760")
        dlg.minsize(860, 520)
        try:
            dlg.transient(self)
        except Exception:
            pass

        top = ttk.Frame(dlg)
        top.pack(fill="x", padx=12, pady=(10, 8))
        ttk.Label(
            top,
            text=f"Resumen fusionado por item (desde OCR crudo) - total: {count}",
            style="SubHeader.TLabel",
        ).pack(side="left")

        body = ttk.Frame(dlg)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        txt = tk.Text(
            body,
            wrap="word",
            bg=self.palette["surface"],
            fg=self.palette["text"],
            insertbackground=self.palette["text"],
            relief="solid",
            bd=1,
            highlightthickness=1,
            highlightbackground=self.palette["border"],
            highlightcolor=self.palette["border"],
            font=("Consolas", 10),
        )
        scr = ttk.Scrollbar(body, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=scr.set)
        txt.pack(side="left", fill="both", expand=True)
        scr.pack(side="right", fill="y")
        txt.insert("1.0", text + "\n")
        txt.configure(state="disabled")

        btns = ttk.Frame(dlg)
        btns.pack(fill="x", padx=12, pady=(0, 12))

        def on_copy() -> None:
            try:
                self.clipboard_clear()
                self.clipboard_append(text + "\n")
                self.update_idletasks()
                messagebox.showinfo("Fusion de problemas", "Resumen fusionado copiado al portapapeles.")
            except Exception as exc:
                messagebox.showerror("Fusion de problemas", f"No se pudo copiar:\n{exc}")

        ttk.Button(btns, text="Copiar", command=on_copy, style="Ghost.TButton").pack(side="left")
        ttk.Button(btns, text="Cerrar", command=dlg.destroy, style="Secondary.TButton").pack(side="right")

    def _apply_reasoning_on_ocr_unified(self) -> None:
        if self._transcribing:
            messagebox.showinfo("Razonamiento", "Ya hay un proceso en curso.")
            return

        labels = self._get_raw_ocr_view_labels()
        summary_text = self._build_ocr_item_summary_text(labels)
        if not summary_text:
            messagebox.showwarning(
                "Razonamiento",
                "No hay OCR fusionado disponible. Ejecuta Paso 2 y luego Fusionar problemas.",
            )
            return

        parsed = self._parse_structured_items_from_raw(summary_text)
        total = len(parsed)
        if total <= 0:
            messagebox.showwarning(
                "Razonamiento",
                "No se detectaron ITEMS estructurados para procesar.",
            )
            return

        provider = (self.provider_var.get() or FIXED_PROVIDER).strip()
        if provider not in {PROVIDER_OPENAI, PROVIDER_HF}:
            provider = FIXED_PROVIDER
        model = (self.model_var.get() or "").strip() or DEFAULT_HF_VISION_MODEL

        try:
            timeout_s = int(self.timeout_var.get())
        except Exception:
            timeout_s = 180
        timeout_s = max(30, min(timeout_s, 600))
        try:
            retries = int(self.retries_var.get())
        except Exception:
            retries = 2
        retries = max(0, min(retries, 5))

        self._geometry_pass_by_label.clear()
        self._geometry_pass_payload_by_label.clear()
        self._transcribing = True
        try:
            self.btn_transcribir.configure(state="disabled")
        except Exception:
            pass

        self._progress_start(total)
        self._set_loading_bar(True, f"Estado: razonamiento sobre OCR fusionado ({total} item(s))...")
        self._log(
            f"Razonamiento iniciado: items={total}, proveedor={provider}, modelo_razonador={self._resolve_hf_format_model(model)}."
        )

        def worker() -> None:
            ok = 0
            err = 0
            items_con_alertas = 0
            total_alertas = 0
            json_invalid_count = 0
            filtered_figure_alert_count = 0
            retry_json_count = 0
            curso_hint = (self.curso_var.get() or "").strip()
            tema_hint = (self.tema_var.get() or "").strip()
            subtema_hint = (self.subtema_var.get() or "").strip()
            client: Optional[OpenAI] = None

            if provider == PROVIDER_OPENAI:
                try:
                    try:
                        client = OpenAI(timeout=timeout_s)
                    except TypeError:
                        client = OpenAI()
                except Exception as exc:
                    self.after(0, lambda e=exc: self._log(f"Razonamiento: no se pudo iniciar cliente OpenAI: {e}"))
                    err = total

            try:
                if provider == PROVIDER_OPENAI and client is None:
                    raise Exception("cliente_openai_no_disponible")

                for idx, block in enumerate(parsed, start=1):
                    num = int(block.get("num", 0) or 0)
                    if num <= 0:
                        err += 1
                        self.after(
                            0,
                            lambda i=idx, t=total: self._progress_update(i, t, i, current_label=f"item {i}/{t}"),
                        )
                        continue

                    raw_reason = self._build_reasoning_input(block)
                    label = f"ocr_unificado#{num}"
                    payload: Dict[str, Any]

                    try:
                        if provider == PROVIDER_OPENAI and client is not None:
                            payload = self._reason_item_openai(
                                client,
                                model=model,
                                timeout_s=timeout_s,
                                retries=retries,
                                label=label,
                                raw_item=raw_reason,
                                curso_hint=curso_hint,
                                tema_hint=tema_hint,
                                subtema_hint=subtema_hint,
                            )
                        else:
                            payload = self._reason_item_hf(
                                model=model,
                                timeout_s=timeout_s,
                                retries=retries,
                                label=label,
                                raw_item=raw_reason,
                                curso_hint=curso_hint,
                                tema_hint=tema_hint,
                                subtema_hint=subtema_hint,
                            )
                    except Exception as exc:
                        payload = {
                            "razonamiento_es": "",
                            "elementos_geometricos": [],
                            "expresiones_sin_dolares": [],
                            "alertas": [f"error_modelo:{exc}"],
                        }

                    payload = self._sanitize_reasoning_payload(payload)
                    meta = payload.get("__meta", {}) if isinstance(payload, dict) else {}
                    if not isinstance(meta, dict):
                        meta = {}
                    json_invalid_count += int(meta.get("json_invalid_count", 0) or 0)
                    filtered_figure_alert_count += int(meta.get("filtered_figure_alert_count", 0) or 0)
                    retry_json_count += int(meta.get("retry_json_count", 0) or 0)

                    alertas = list(payload.get("alertas", []) or [])
                    if alertas:
                        items_con_alertas += 1
                        total_alertas += len(alertas)
                        joined = ", ".join(str(a) for a in alertas if str(a).strip())
                        self.after(0, lambda n=num, txt=joined: self._log(f"[item {n}] alertas: {txt}"))

                    p1_text = self._build_geometry_pass_display(
                        razonamiento_es=str(payload.get("razonamiento_es", "") or ""),
                        elementos_geometricos=list(payload.get("elementos_geometricos", []) or []),
                        expresiones_sin_dolares=list(payload.get("expresiones_sin_dolares", []) or []),
                        alertas=alertas,
                        raw_model_text=self._clip_debug_text(str(meta.get("raw_model_text", "") or "")),
                        raw_retry_text=self._clip_debug_text(str(meta.get("raw_retry_text", "") or "")),
                    )
                    view_key = f"{label}@{idx} [modelo-p1]"
                    self._geometry_pass_by_label[view_key] = p1_text or "[sin salida modelo p1]"
                    payload_norm = {
                        "razonamiento_es": self.controller.normalizar_item_una_linea(
                            str(payload.get("razonamiento_es", "") or "")
                        ),
                        "elementos_geometricos": list(payload.get("elementos_geometricos", []) or []),
                        "expresiones_sin_dolares": list(payload.get("expresiones_sin_dolares", []) or []),
                        "alertas": alertas,
                    }
                    # Canonical key by item number for downstream formatting lookup.
                    self._geometry_pass_payload_by_label[label] = payload_norm
                    # Keep per-occurrence payload too (duplicate item numbers in OCR).
                    self._geometry_pass_payload_by_label[f"{label}@{idx}"] = payload_norm
                    ok += 1
                    self.after(
                        0,
                        lambda i=idx, t=total: self._progress_update(i, t, i, current_label=f"item {i}/{t}"),
                    )

                err = max(0, total - ok)
            except Exception as exc:
                self.after(0, lambda e=exc: self._log(f"Razonamiento: error procesando OCR fusionado: {e}"))
                err = total
            finally:
                def finish() -> None:
                    self._set_loading_bar(False, "Estado: inactivo")
                    self._progress_finish(ok=ok, errors=err, total_images=total, detected_items=ok)
                    self._log(
                        "Razonamiento completado: "
                        f"items={total}, con_alertas={items_con_alertas}, total_alertas={total_alertas}, "
                        f"salida_no_json_valida={json_invalid_count}, "
                        f"alertas_figura_filtradas={filtered_figure_alert_count}, "
                        f"retry_json={retry_json_count}."
                    )
                    self._transcribing = False
                    try:
                        self.btn_transcribir.configure(state="normal")
                    except Exception:
                        pass

                self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_final_format_on_ocr_unified(self) -> None:
        if self._transcribing:
            messagebox.showinfo("Formateo final", "Ya hay un proceso en curso.")
            return

        labels = self._get_raw_ocr_view_labels()
        summary_text = self._build_ocr_item_summary_text(labels)
        if not summary_text:
            messagebox.showwarning(
                "Formateo final",
                "No hay OCR unificado disponible. Ejecuta Paso 2 y Fusionar problemas.",
            )
            return

        parsed = self._parse_structured_items_from_raw(summary_text)
        total = len(parsed)
        if total <= 0:
            messagebox.showwarning(
                "Formateo final",
                "No se detectaron ITEMS estructurados para formatear.",
            )
            return

        provider = (self.provider_var.get() or FIXED_PROVIDER).strip()
        if provider not in {PROVIDER_OPENAI, PROVIDER_HF}:
            provider = FIXED_PROVIDER
        model = (self.model_var.get() or "").strip() or DEFAULT_HF_VISION_MODEL
        tag_mode = (self.tag_mode_var.get() or FIXED_TAG_MODE).strip()
        db_name = (self.db_name_var.get() or "").strip()
        can_use_llm = bool(self.format_with_llm_var.get())

        try:
            timeout_s = int(self.timeout_var.get())
        except Exception:
            timeout_s = 180
        timeout_s = max(30, min(timeout_s, 600))
        try:
            retries = int(self.retries_var.get())
        except Exception:
            retries = 2
        retries = max(0, min(retries, 5))

        catalog = self._get_catalog(db_name) if db_name else {"areas": [], "temas": [], "subtemas": []}
        curso_hint = (self.curso_var.get() or "").strip()
        tema_hint = (self.tema_var.get() or "").strip()
        subtema_hint = (self.subtema_var.get() or "").strip()

        fallback_path: Optional[Path] = None
        for lb in labels:
            p = self._file_map.get(str(lb))
            if p is not None:
                fallback_path = p
                break
        if fallback_path is None and self._file_map:
            try:
                fallback_path = next(iter(self._file_map.values()))
            except Exception:
                fallback_path = None
        if fallback_path is None:
            fallback_path = Path.cwd()

        image_paths_by_num: Dict[int, List[str]] = {}
        for _src, item_txt, image_paths in self._items:
            num = self.controller.parsear_numero_original(str(item_txt or "")) or 0
            if num <= 0:
                continue
            bucket = image_paths_by_num.setdefault(num, [])
            for p in image_paths or []:
                sp = str(p or "").strip()
                if sp and sp not in bucket:
                    bucket.append(sp)

        self._transcribing = True
        try:
            self.btn_transcribir.configure(state="disabled")
        except Exception:
            pass
        self._progress_start(total)
        self._set_loading_bar(True, f"Estado: formateo final sobre OCR unificado ({total} item(s))...")
        self._log(
            f"Formateo final iniciado: items={total}, proveedor={provider}, modelo_formateo={self._resolve_hf_format_model(model)}."
        )

        def worker() -> None:
            ok = 0
            err = 0
            llm_used = 0
            new_items: List[Tuple[str, str, List[str]]] = []
            client: Optional[OpenAI] = None

            if provider == PROVIDER_OPENAI:
                try:
                    try:
                        client = OpenAI(timeout=timeout_s)
                    except TypeError:
                        client = OpenAI()
                except Exception as exc:
                    self.after(0, lambda e=exc: self._log(f"Formateo final: no se pudo iniciar cliente OpenAI: {e}"))
                    err = total

            try:
                if provider == PROVIDER_OPENAI and client is None and can_use_llm:
                    raise Exception("cliente_openai_no_disponible")

                for idx, block in enumerate(parsed, start=1):
                    num = int(block.get("num", 0) or 0)
                    if num <= 0:
                        err += 1
                        self.after(
                            0,
                            lambda i=idx, t=total: self._progress_update(i, t, i, current_label=f"item {i}/{t}"),
                        )
                        continue

                    raw_struct = self._build_structured_item_summary(block)
                    label = f"ocr_unificado#{num}"
                    reasoning_payload = self._geometry_pass_payload_by_label.get(label, {})
                    item = ""

                    try:
                        if can_use_llm and provider == PROVIDER_OPENAI and client is not None:
                            formatted = self._format_item_openai(
                                client,
                                model=model,
                                timeout_s=timeout_s,
                                retries=retries,
                                label=label,
                                raw_item=raw_struct,
                                curso_hint=curso_hint,
                                tema_hint=tema_hint,
                                subtema_hint=subtema_hint,
                                run_geometry_pass=False,
                                reasoning_payload=reasoning_payload,
                            )
                            if formatted:
                                item = self._finalize_item_prompt_first(
                                    formatted, path=fallback_path if fallback_path is not None else Path.cwd(), fallback_numero=num
                                )
                                llm_used += 1
                        elif can_use_llm and provider == PROVIDER_HF:
                            formatted = self._format_item_hf(
                                model=model,
                                timeout_s=timeout_s,
                                retries=retries,
                                label=label,
                                raw_item=raw_struct,
                                curso_hint=curso_hint,
                                tema_hint=tema_hint,
                                subtema_hint=subtema_hint,
                                run_geometry_pass=False,
                                reasoning_payload=reasoning_payload,
                            )
                            if formatted:
                                item = self._finalize_item_prompt_first(
                                    formatted, path=fallback_path if fallback_path is not None else Path.cwd(), fallback_numero=num
                                )
                                llm_used += 1
                    except Exception as exc:
                        self.after(0, lambda n=num, e=exc: self._log(f"Formateo final item {n}: {e}"))

                    if not item:
                        enunciado = str(block.get("enunciado", "") or "").strip()
                        options = block.get("options", {}) if isinstance(block, dict) else {}
                        item = self._build_scan_item_strict(
                            numero=num,
                            enunciado=enunciado,
                            options=options if isinstance(options, dict) else {},
                        )

                    item = self._apply_reasoning_wrap_hints(item, reasoning_payload)
                    item = self._apply_global_math_conventions(item)
                    item = self._apply_geometry_conventions(item)
                    item = self._normalize_math_delimiters(item)

                    figura = str(block.get("figura", "NO") or "").strip().upper() == "SI"
                    if figura and not self._has_image_marker(item):
                        item = self._insert_image_marker(
                            item, path=fallback_path if fallback_path is not None else Path.cwd(), numero=num
                        )
                        item = self._move_image_marker_before_options(item)

                    item = self._apply_metadata_mode(
                        item=item,
                        mode=tag_mode,
                        provider=provider,
                        model=model,
                        timeout_s=timeout_s,
                        retries=retries,
                        label=label,
                        catalog=catalog,
                        openai_client=client if provider == PROVIDER_OPENAI else None,
                    )
                    item = self._move_image_marker_before_options(item)
                    item = self._normalize_math_delimiters(item)
                    item = self.controller.normalizar_item_una_linea(item)

                    new_items.append(("ocr_unificado", item, list(image_paths_by_num.get(num, []))))
                    ok += 1
                    self.after(
                        0,
                        lambda i=idx, t=total: self._progress_update(i, t, i, current_label=f"item {i}/{t}"),
                    )
            except Exception as exc:
                self.after(0, lambda e=exc: self._log(f"Formateo final: error procesando OCR unificado: {e}"))
                err = total if err <= 0 else err
            finally:
                err = max(0, total - ok) if total > 0 else err

                def finish() -> None:
                    if new_items:
                        self._items = new_items
                        self._render_output_from_items()
                    self._set_loading_bar(False, "Estado: inactivo")
                    self._progress_finish(ok=ok, errors=err, total_images=total, detected_items=ok)
                    self._log(
                        f"Formateo final completado. Total={total}, OK={ok}, Errores={err}, IA={llm_used}."
                    )
                    self._transcribing = False
                    try:
                        self.btn_transcribir.configure(state="normal")
                    except Exception:
                        pass

                self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_format_model_on_modified_ocr(self) -> None:
        if self._transcribing:
            messagebox.showinfo("Formateo", "Ya hay un proceso en curso.")
            return

        self._sync_items_from_output_text()
        if not self._items:
            messagebox.showwarning("Formateo", "No hay items en salida para formatear.")
            return

        provider = (self.provider_var.get() or FIXED_PROVIDER).strip().lower()
        if provider not in {PROVIDER_OPENAI, PROVIDER_HF}:
            provider = FIXED_PROVIDER
        model = (self.model_var.get() or "").strip() or DEFAULT_HF_VISION_MODEL
        db_name = (self.db_name_var.get() or "").strip()
        tag_mode = (self.tag_mode_var.get() or FIXED_TAG_MODE).strip()

        try:
            timeout_s = int(self.timeout_var.get())
        except Exception:
            timeout_s = 180
        timeout_s = max(30, min(timeout_s, 600))
        try:
            retries = int(self.retries_var.get())
        except Exception:
            retries = 2
        retries = max(0, min(retries, 5))

        total = len(self._items)
        if total <= 0:
            messagebox.showwarning("Formateo", "No hay items para procesar.")
            return

        self._transcribing = True
        try:
            self.btn_transcribir.configure(state="disabled")
        except Exception:
            pass
        self._progress_start(total)
        self._set_loading_bar(True, f"Estado: aplicando modelo de formateo a {total} item(s)...")
        self._log(f"Formateo IA sobre OCR modificado: {total} item(s).")

        def worker() -> None:
            client: Optional[OpenAI] = None
            if provider == PROVIDER_OPENAI:
                try:
                    try:
                        client = OpenAI(timeout=timeout_s)
                    except TypeError:
                        client = OpenAI()
                except Exception as exc:
                    self.after(0, lambda e=exc: messagebox.showerror("OpenAI", str(e)))
                    return

            catalog = self._get_catalog(db_name) if db_name else {"areas": [], "temas": [], "subtemas": []}
            curso_hint = (self.curso_var.get() or "").strip()
            tema_hint = (self.tema_var.get() or "").strip()
            subtema_hint = (self.subtema_var.get() or "").strip()
            fallback_path = None
            if self._file_map:
                try:
                    fallback_path = next(iter(self._file_map.values()))
                except Exception:
                    fallback_path = None
            if fallback_path is None:
                fallback_path = Path.cwd()

            ok = 0
            err = 0
            llm_used = 0
            new_items: List[Tuple[str, str, List[str]]] = []

            for idx, entry in enumerate(list(self._items), start=1):
                archivo = str(entry[0] if len(entry) > 0 else f"manual_{idx}")
                raw_item = str(entry[1] if len(entry) > 1 else "")
                imgs = list(entry[2] if len(entry) > 2 else [])
                numero = self.controller.parsear_numero_original(raw_item) or idx
                label = f"ocr_modificado#{numero}"
                try:
                    formatted = ""
                    if provider == PROVIDER_OPENAI and client is not None:
                        formatted = self._format_item_openai(
                            client,
                            model=model,
                            timeout_s=timeout_s,
                            retries=retries,
                            label=label,
                            raw_item=raw_item,
                            curso_hint=curso_hint,
                            tema_hint=tema_hint,
                            subtema_hint=subtema_hint,
                        )
                    elif provider == PROVIDER_HF:
                        formatted = self._format_item_hf(
                            model=model,
                            timeout_s=timeout_s,
                            retries=retries,
                            label=label,
                            raw_item=raw_item,
                            curso_hint=curso_hint,
                            tema_hint=tema_hint,
                            subtema_hint=subtema_hint,
                        )

                    item = raw_item
                    if formatted:
                        item = self._finalize_item_prompt_first(
                            formatted, path=fallback_path, fallback_numero=numero
                        )
                        llm_used += 1

                    if self._has_image_marker(raw_item) and (not self._has_image_marker(item)):
                        marker_name = self._extract_first_image_marker_name(raw_item)
                        if marker_name:
                            item = f"{item} [[Imagen={marker_name}]]".strip()
                            item = self._move_image_marker_before_options(item)

                    reasoning_payload = self._geometry_pass_payload_by_label.get(label, {})
                    item = self._apply_reasoning_wrap_hints(item, reasoning_payload)
                    item = self._apply_global_math_conventions(item)
                    item = self._apply_geometry_conventions(item)
                    item = self._normalize_math_delimiters(item)

                    item = self._apply_metadata_mode(
                        item=item,
                        mode=tag_mode,
                        provider=provider,
                        model=model,
                        timeout_s=timeout_s,
                        retries=retries,
                        label=label,
                        catalog=catalog,
                        openai_client=client if provider == PROVIDER_OPENAI else None,
                    )
                    item = self._move_image_marker_before_options(item)
                    item = self._normalize_math_delimiters(item)
                    item = self.controller.normalizar_item_una_linea(item)
                    new_items.append((archivo, item, imgs))
                    ok += 1
                except Exception as exc:
                    self.after(0, lambda e=exc, n=numero: self._log(f"Formateo item {n}: {e}"))
                    new_items.append((archivo, raw_item, imgs))
                    err += 1
                finally:
                    self.after(
                        0,
                        lambda i=idx, t=total: self._progress_update(i, t, i, current_label=f"item {i}/{t}"),
                    )

            def finish() -> None:
                self._items = new_items
                self._render_output_from_items()
                self._log(
                    f"Formateo IA completado sobre OCR modificado. Total={total}, IA={llm_used}, OK={ok}, Errores={err}."
                )
                self._set_loading_bar(False, "Estado: inactivo")
                self._progress_finish(ok=ok, errors=err, total_images=total, detected_items=total)
                self._sync_items_from_output_then_preview()

            self.after(0, finish)

        def safe_worker() -> None:
            try:
                worker()
            finally:
                def unlock() -> None:
                    self._transcribing = False
                    try:
                        self.btn_transcribir.configure(state="normal")
                    except Exception:
                        pass
                self.after(0, unlock)

        threading.Thread(target=safe_worker, daemon=True).start()

    def _guardar_tex(self) -> None:
        txt = (self.txt_out.get("1.0", "end") or "").strip()
        if not txt:
            messagebox.showwarning("Guardar", "No hay salida para guardar.")
            return
        path = filedialog.asksaveasfilename(
            title="Guardar .tex",
            defaultextension=".tex",
            filetypes=[("TeX", "*.tex"), ("Todos", "*.*")],
        )
        if not path:
            return
        out_path = Path(path)
        items = [line.strip() for line in txt.splitlines() if line.strip()]
        self.controller.exportar_a_tex(items=items, out_path=out_path)
        messagebox.showinfo("Guardar", f"Guardado: {out_path}")

    def _guardar_bd(self) -> None:
        db = (self.db_name_var.get() or "").strip()
        if not db:
            messagebox.showwarning("BD", "Selecciona una base de datos.")
            return
        txt = (self.txt_out.get("1.0", "end") or "").strip()
        if not txt:
            messagebox.showwarning("BD", "No hay items para guardar.")
            return

        # Siempre usamos la salida actual del textbox. Si el usuario editÃ³ lÃ­neas,
        # intentamos preservar archivo_origen por matching exacto; si no, asignamos uno nuevo.
        lines = [l.strip() for l in txt.splitlines() if l.strip()]
        remaining = list(self._items)
        items_for_db: List[Tuple[str, str, List[str]]] = []
        unmatched = 0
        moved_paths: Dict[str, str] = {}

        for i, line in enumerate(lines, start=1):
            archivo_origen = f"transcripcion_{i}"
            image_paths: List[str] = []
            match_idx = None
            for j, entry in enumerate(remaining):
                a = entry[0]
                it = entry[1]
                imgs = entry[2] if len(entry) > 2 else []
                if it == line:
                    match_idx = j
                    archivo_origen = a
                    image_paths = list(imgs or [])
                    break
            if match_idx is not None:
                remaining.pop(match_idx)
            else:
                unmatched += 1
            final_paths: List[str] = []
            for p in image_paths:
                final_p = self._materialize_temp_crop(p)
                moved_paths[p] = final_p
                final_paths.append(final_p)
            line_for_db = self._rewrite_placeholder_markers_for_storage(line, archivo_origen)
            items_for_db.append((archivo_origen, line_for_db, final_paths))

        # Refresh preview image map to final paths if files were moved.
        if self._preview_images:
            refreshed: Dict[str, str] = {}
            for name, p in self._preview_images.items():
                refreshed[name] = moved_paths.get(p, p)
            self._preview_images = refreshed
            self._push_preview_text(force=True)

        try:
            stats = self.controller.insertar_items(db, items=items_for_db)
        except Exception as exc:
            messagebox.showerror("BD", str(exc))
            return
        messagebox.showinfo(
            "BD",
            f"BD: {db}\nInsertados: {stats['inserted']}\nDuplicados: {stats['skipped']}\nInvalidos: {stats['invalid']}\nEditados sin origen: {unmatched}",
        )
    def _open_preview(self) -> None:
        self._push_preview_text(force=True)
        try:
            self._preview.ensure_open()
            self._schedule_preview_nav_poll()
            self._log("Vista previa: usa 'Corregir #n' para editar en ventana flotante o clic en item para saltar al editor.")
        except Exception as exc:
            messagebox.showerror("Vista previa", str(exc))

    def _schedule_preview_nav_poll(self) -> None:
        if not self._ui_alive():
            return
        if self._preview_nav_after:
            try:
                self.after_cancel(self._preview_nav_after)
            except Exception:
                pass
            self._preview_nav_after = None
        self._preview_nav_after = self.after(350, self._poll_preview_navigation)

    def _poll_preview_navigation(self) -> None:
        self._preview_nav_after = None
        if not self._ui_alive():
            return
        target_item: Optional[int] = None
        edit_requests: List[Dict[str, Any]] = []
        try:
            target_item = self._preview.pop_goto_item()
        except Exception:
            target_item = None
        try:
            edit_requests = self._preview.pop_edit_requests()
        except Exception:
            edit_requests = []
        if isinstance(target_item, int) and target_item > 0:
            self._focus_output_item(target_item)
        changed = False
        for req in edit_requests:
            try:
                item_num = int((req or {}).get("item") or 0)
            except Exception:
                item_num = 0
            new_text = str((req or {}).get("text") or "").strip()
            if item_num <= 0 or not new_text:
                continue
            if self._replace_output_item_text(item_num=item_num, new_item_text=new_text):
                changed = True
        if changed:
            self._sync_items_from_output_then_preview()
        self._preview_nav_after = self.after(350, self._poll_preview_navigation)

    def _focus_output_item(self, item_num: int) -> None:
        try:
            raw = self.txt_out.get("1.0", "end-1c")
        except Exception:
            return
        if not raw:
            return
        lines = raw.splitlines()
        # Match: \item[\textbf{7.}] or \item[\textbf{7}]
        pat = re.compile(
            rf"""\\item\s*\[\s*\\textbf\{{\s*{int(item_num)}\.?\s*\}}\s*\]""",
            re.IGNORECASE,
        )
        line_no: Optional[int] = None
        for idx, line in enumerate(lines, start=1):
            if pat.search(str(line or "")):
                line_no = idx
                break
        if line_no is None:
            self._log(f"Vista previa -> item {item_num}: no se encontro en la salida actual.")
            return
        idx_start = f"{line_no}.0"
        idx_end = f"{line_no}.end"
        try:
            self.txt_out.focus_set()
            self.txt_out.mark_set("insert", idx_start)
            self.txt_out.see(idx_start)
            self.txt_out.tag_remove("preview_jump", "1.0", "end")
            self.txt_out.tag_configure("preview_jump", background="#FFF59D")
            self.txt_out.tag_add("preview_jump", idx_start, idx_end)
            self.after(1800, lambda: self.txt_out.tag_remove("preview_jump", "1.0", "end"))
            self._log(f"Vista previa -> item {item_num}: cursor movido a linea {line_no}.")
        except Exception:
            return

    def _replace_output_item_text(self, *, item_num: int, new_item_text: str) -> bool:
        try:
            raw = self.txt_out.get("1.0", "end-1c")
        except Exception:
            return False
        if not raw.strip():
            return False

        raw_norm = raw.replace("\r\n", "\n").replace("\r", "\n")
        if "\\item[" in raw_norm:
            items = re.split(r"(?=\\item\s*\[)", raw_norm)
            items = [str(x or "").strip() for x in items if str(x or "").strip()]
        else:
            items = [str(x or "").strip() for x in raw_norm.splitlines() if str(x or "").strip()]
        if not items:
            return False

        target_idx: Optional[int] = None
        for idx, it in enumerate(items):
            n = self.controller.parsear_numero_original(it) or 0
            if n == item_num:
                target_idx = idx
                break
        if target_idx is None:
            self._log(f"Vista previa -> item {item_num}: no encontrado para aplicar edicion.")
            return False

        old_item = items[target_idx]
        replacement = self.controller.normalizar_item_una_linea(new_item_text)
        if not replacement:
            self._log(f"Vista previa -> item {item_num}: edicion vacia ignorada.")
            return False

        old_prefix_match = re.match(
            r"^\s*(\\item\s*\[\s*\\textbf\{\s*\d+\.?\s*\}\s*\]\s*)",
            old_item,
            flags=re.IGNORECASE,
        )
        old_prefix = old_prefix_match.group(1) if old_prefix_match else ""
        if "\\item[" not in replacement and old_prefix:
            replacement = f"{old_prefix}{replacement}".strip()

        repl_num = self.controller.parsear_numero_original(replacement) or 0
        if repl_num != item_num and old_prefix:
            body = re.sub(
                r"^\s*\\item\s*\[\s*\\textbf\{\s*\d+\.?\s*\}\s*\]\s*",
                "",
                replacement,
                flags=re.IGNORECASE,
            ).strip()
            replacement = f"{old_prefix}{body}".strip() if body else old_item

        items[target_idx] = replacement
        merged = "\n".join(items).strip()
        try:
            self.txt_out.delete("1.0", "end")
            if merged:
                self.txt_out.insert("1.0", merged + "\n")
            self._mark_item_corrected(item_num)
            self._focus_output_item(item_num)
            self._log(f"Vista previa -> item {item_num}: edicion aplicada.")
            return True
        except Exception:
            return False

    def _sync_items_from_output_text(self) -> int:
        """
        Sync internal `_items` with the current output textbox.
        This prevents deleted/edited lines from reappearing in later steps.
        Returns count of removed items.
        """
        try:
            raw = (self.txt_out.get("1.0", "end") or "").strip()
        except Exception:
            return 0

        lines = [self.controller.normalizar_item_una_linea(l) for l in raw.splitlines() if str(l).strip()]
        old_items = list(self._items)
        old_lines = [self.controller.normalizar_item_una_linea(str(e[1] or "")) for e in old_items if len(e) > 1]
        if lines == old_lines:
            return 0

        remaining = list(old_items)
        rebuilt: List[Tuple[str, str, List[str]]] = []

        for idx, line in enumerate(lines, start=1):
            match_idx: Optional[int] = None
            # 1) Exact text match first (strongest).
            for j, entry in enumerate(remaining):
                if len(entry) < 2:
                    continue
                if self.controller.normalizar_item_una_linea(str(entry[1] or "")) == line:
                    match_idx = j
                    break

            # 2) Fallback by original item number.
            if match_idx is None:
                target_num = self.controller.parsear_numero_original(line) or 0
                if target_num > 0:
                    for j, entry in enumerate(remaining):
                        if len(entry) < 2:
                            continue
                        num = self.controller.parsear_numero_original(str(entry[1] or "")) or 0
                        if num == target_num:
                            match_idx = j
                            break

            if match_idx is not None:
                src_entry = remaining.pop(match_idx)
                archivo = str(src_entry[0] or "").strip() or f"manual_{idx}"
                imgs = list(src_entry[2] if len(src_entry) > 2 else [])
            else:
                archivo = f"manual_{idx}"
                imgs = []

            # Binding truth: ensure configured segment->item/slot markers are present.
            line_num = self.controller.parsear_numero_original(line) or 0
            if line_num > 0 and archivo and (archivo != f"manual_{idx}"):
                src = self._find_source_path_by_stem(archivo)
                if src is not None:
                    source_key = self._seg_v2_source_key(src)
                    source_bindings = self._get_segment_bindings_by_source_key(source_key)
                    for payload in source_bindings.values():
                        if not self._as_bool(payload.get("confirmed"), False):
                            continue
                        if int(self._safe_int(payload.get("item_num", 0), 0)) != int(line_num):
                            continue
                        slot_name = self._normalize_binding_slot(str(payload.get("slot", "ENUNCIADO") or "ENUNCIADO"))
                        marker_name = str(payload.get("marker_name", "") or "").strip()
                        if not marker_name:
                            marker_name = self._build_binding_marker_name(item_num=line_num, slot=slot_name)
                        line = self._insert_explicit_marker_in_slot(
                            text=line,
                            marker_name=marker_name,
                            slot=slot_name,
                            path=src,
                            numero=line_num,
                        )
                        crop_path = str(payload.get("crop_path", "") or "").strip()
                        if self._is_valid_image_path(crop_path) and crop_path not in imgs:
                            imgs.append(crop_path)
                            self._preview_images[marker_name] = crop_path

            # Preserve explicit marker->image bindings selected by the user.
            markers_line = self._extract_image_marker_names(line)
            if markers_line:
                mapped_imgs: List[str] = []
                for mk_raw in markers_line:
                    mk = str(mk_raw or "").strip()
                    if not mk:
                        continue
                    mapped = str(self._preview_images.get(mk) or "").strip()
                    if self._is_valid_image_path(mapped) and mapped not in mapped_imgs:
                        mapped_imgs.append(mapped)
                if mapped_imgs:
                    imgs = mapped_imgs

            rebuilt.append((archivo, line, imgs))

        removed_count = max(0, len(old_items) - len(rebuilt))
        self._items = rebuilt
        self._reconcile_corrected_items()

        # Drop stale preview mappings for removed markers.
        valid_markers: Set[str] = set()
        for entry in self._items:
            if len(entry) < 2:
                continue
            for mk in self._extract_image_marker_names(str(entry[1] or "")):
                valid_markers.add(str(mk))
        if self._preview_images:
            self._preview_images = {k: v for k, v in self._preview_images.items() if k in valid_markers}

        self._refresh_training_pairs_from_items()
        return removed_count

    def _reconcile_corrected_items(self) -> None:
        present: Set[int] = set()
        for entry in self._items:
            if len(entry) < 2:
                continue
            num = self.controller.parsear_numero_original(str(entry[1] or "")) or 0
            if num > 0:
                present.add(num)
        if not present:
            self._corrected_item_numbers.clear()
            self.after(0, self._refresh_corrected_item_highlights)
            return
        self._corrected_item_numbers = {n for n in self._corrected_item_numbers if n in present}
        self.after(0, self._refresh_corrected_item_highlights)

    def _mark_item_corrected(self, item_num: int) -> None:
        try:
            n = int(item_num)
        except Exception:
            return
        if n > 0:
            self._corrected_item_numbers.add(n)
            self.after(0, self._refresh_corrected_item_highlights)

    def _find_output_item_line(self, item_num: int) -> Optional[int]:
        try:
            raw = self.txt_out.get("1.0", "end-1c")
        except Exception:
            return None
        if not raw:
            return None
        lines = raw.splitlines()
        pat = re.compile(
            rf"""\\item\s*\[\s*\\textbf\{{\s*{int(item_num)}\.?\s*\}}\s*\]""",
            re.IGNORECASE,
        )
        for idx, line in enumerate(lines, start=1):
            if pat.search(str(line or "")):
                return idx
        return None

    def _refresh_corrected_item_highlights(self) -> None:
        if not self._ui_alive():
            return
        txt = getattr(self, "txt_out", None)
        if not self._widget_alive(txt):
            return
        try:
            self.txt_out.tag_remove("corrected_item", "1.0", "end")
            self.txt_out.tag_configure("corrected_item", background="#DCFCE7")
        except Exception:
            return
        for n in sorted(self._corrected_item_numbers):
            if n <= 0:
                continue
            line_no = self._find_output_item_line(n)
            if line_no is None:
                continue
            idx_start = f"{line_no}.0"
            idx_end = f"{line_no}.end"
            try:
                self.txt_out.tag_add("corrected_item", idx_start, idx_end)
            except Exception:
                continue

    def _sync_items_from_output_then_preview(self) -> None:
        try:
            removed = self._sync_items_from_output_text()
            if removed > 0:
                self._log(f"Salida editada: {removed} item(s) removido(s) del estado interno.")
        except Exception:
            pass
        try:
            current_text = self.controller.normalizar_item_una_linea(
                (self.txt_out.get("1.0", "end") or "").strip()
            )
        except Exception:
            current_text = ""
        expected_text = self.controller.normalizar_item_una_linea(
            "\n".join(
                [str(entry[1] or "").strip() for entry in self._items if len(entry) > 1 and str(entry[1] or "").strip()]
            ).strip()
        )
        if expected_text and expected_text != current_text:
            self._render_output_from_items()
            return
        self._push_preview_text(force=True)

    def _on_out_modified(self, _event=None) -> None:
        try:
            self.txt_out.edit_modified(False)
        except Exception:
            pass
        if bool(getattr(self, "_suppress_output_sync", False)):
            return
        # Debounce: evita spamear el servidor mientras escribe
        if self._preview_debounce_after:
            try:
                self.after_cancel(self._preview_debounce_after)
            except Exception:
                pass
        self._preview_debounce_after = self.after(250, self._sync_items_from_output_then_preview)

    def _push_preview_text(self, force: bool = False) -> None:
        if not self._ui_alive():
            return
        txt = getattr(self, "txt_out", None)
        if not self._widget_alive(txt):
            return
        try:
            self._sync_preview_images_from_items()
            self._reconcile_corrected_items()
            self._refresh_corrected_item_highlights()
            text = (self.txt_out.get("1.0", "end") or "").strip()
            self._preview.set_images(dict(self._preview_images))
            self._preview.set_corrected_items(sorted(self._corrected_item_numbers))
            self._preview.set_text(text)
        except Exception:
            return

    def _render_output_from_items(self) -> None:
        def apply():
            if not self._ui_alive():
                return
            txt = getattr(self, "txt_out", None)
            if not self._widget_alive(txt):
                return
            self._suppress_output_sync = True
            try:
                lines = [entry[1] for entry in self._items if len(entry) > 1 and str(entry[1]).strip()]
                text = "\n".join(lines).strip()
                self.txt_out.delete("1.0", "end")
                if text:
                    self.txt_out.insert("end", text + "\n")
            finally:
                self._suppress_output_sync = False
            self._refresh_corrected_item_highlights()
            self._push_preview_text(force=True)

        if not self._ui_alive():
            return
        try:
            self.after(0, apply)
        except Exception:
            return
