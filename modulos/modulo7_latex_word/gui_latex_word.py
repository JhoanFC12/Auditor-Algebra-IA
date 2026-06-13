from __future__ import annotations

from datetime import datetime
from difflib import SequenceMatcher
import json
import io
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import unicodedata
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from database.connection import DatabaseManager
from modulos.modulo6_practicas.controlador_practicas import PracticeBuilderController
from modulos.modulo9_organizador_libros.controlador_organizador_libros import BookProgressController
from utils.preview_window import PreviewWindow
from utils.project_layout import infer_workspace_from_session_path, normalize_instance_name, project_dirs, remap_legacy_drive_path
from utils.styles import apply_openai_theme

IMAGE_MARKER_RE = re.compile(r"\[\[\s*Imagen\s*=\s*([^\]\r\n]+?)\s*\]\]", re.IGNORECASE)
CLAVE_TAG_RE = re.compile(r"\[\[\s*clave\s*=\s*([^\]\r\n]+?)\s*\]\]", re.IGNORECASE)
ESTADO_TAG_RE = re.compile(r"\[\[\s*estado\s*=\s*([^\]\r\n]+?)\s*\]\]", re.IGNORECASE)
GENERIC_TAG_RE = re.compile(r"\[\[\s*([^\]\r\n]+?)\s*\]\]")
TEX_ITEM_BLOCK_RE = re.compile(
    r"(?is)(\\item\s*\[\s*\\textbf\{\s*(\d+)\.?\s*\}\s*\].*?)"
    r"(?=(?:\n\s*\\item\s*\[\s*\\textbf\{)|(?:\n\s*\\end\{enumerate\})|\Z)"
)
ANSWER_KEY_SECTION_RE = re.compile(
    r"(?is)(?:^|\n)\s*(?:"
    r"\\(?:section|subsection|subsubsection)\*?\{\s*claves?\s+de\s+respuestas?\s*\}"
    r"|\\textbf\{\s*claves?\s+de\s+respuestas?\s*\}"
    r"|claves?\s+de\s+respuestas?\s*:?"
    r")"
)
ANSWER_KEY_ENTRY_RE = re.compile(r"(?<!\d)(\d{1,4})\)\s*([A-Za-z])\b")

try:
    from tkinterdnd2 import DND_FILES  # type: ignore

    DND_AVAILABLE = True
    DND_IMPORT_ERROR = ""
except Exception as exc:
    DND_FILES = None
    DND_AVAILABLE = False
    DND_IMPORT_ERROR = str(exc)

try:
    from PIL import Image, ImageTk  # type: ignore
except Exception:
    Image = None  # type: ignore[assignment]
    ImageTk = None  # type: ignore[assignment]


class LatexWordBridgeWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Modulo 7 - LaTeX a Word")
        self.geometry("980x700")
        self.minsize(900, 620)
        self._maximize_window()

        self.repo_var = tk.StringVar(value=str(self._default_editor_repo()))
        self.python_var = tk.StringVar(value=self._detect_python(str(self._default_editor_repo())))
        self.source_mode_var = tk.StringVar(value="db")
        self.input_tex_var = tk.StringVar(value="")
        self.session_path_var = tk.StringVar(value="")
        self.session_root_var = tk.StringVar(value=str(self._default_sessions_root()))
        self.session_book_var = tk.StringVar(value="")
        self.session_instance_var = tk.StringVar(value="")
        self._session_catalog_status_var = tk.StringVar(value="Sin explorar")
        self.output_docx_var = tk.StringVar(value="")
        self.template_var = tk.StringVar(value=self._default_template(str(self._default_editor_repo())))
        self.images_dir_var = tk.StringVar(value="")
        self.style_var = tk.StringVar(value="Estilo_plantilla")
        self.db_name_var = tk.StringVar(value="")
        self.curso_var = tk.StringVar(value="Todos")
        self.tema_var = tk.StringVar(value="Todos")
        self.subtema_var = tk.StringVar(value="Todos")
        self.autor_var = tk.StringVar(value="Todos")
        self.editorial_var = tk.StringVar(value="Todos")
        self.estado_var = tk.StringVar(value="Todos")
        self.clave_var = tk.StringVar(value="Todos")
        self.cantidad_var = tk.IntVar(value=20)
        self.titulo_var = tk.StringVar(value="Practica")
        self.incluir_clave_final_var = tk.BooleanVar(value=True)
        self.aleatorio_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Listo")
        self._running = False
        self._tema_label_to_id: dict[str, object] = {}
        self._subtema_label_to_id: dict[str, object] = {}
        self._db_source_count_var = tk.StringVar(value="Problemas disponibles: 0")
        self._db_preview_count_var = tk.StringVar(value="Vista previa: 0 | Seleccion actual: 0 | Acumulados: 0")
        self._db_schema_notice_key: tuple[str, str] | None = None
        self._db_preview_signature: tuple[object, ...] | None = None
        self._db_preview_items: list[dict[str, object]] = []
        self._db_selected_problem_ids: set[int] = set()
        self._db_selected_problem_order: list[int] = []
        self._db_selected_problem_map: dict[int, dict[str, object]] = {}
        self._db_selection_json_path: Path | None = None
        self._db_preview_window: tk.Toplevel | None = None
        self._db_practice_editor_window: tk.Toplevel | None = None
        self.db_preview_tree: ttk.Treeview | None = None
        self.txt_db_preview: tk.Text | None = None
        self.txt_practice_structure: tk.Text | None = None
        self._db_source_window: tk.Toplevel | None = None
        self._db_source_structure_text: tk.Text | None = None
        self._db_preview_render_canvas: tk.Canvas | None = None
        self._db_preview_render_status_var = tk.StringVar(value="Sin vista previa")
        self._db_preview_render_image: list[object] = []
        self._db_open_pdf_btn: ttk.Button | None = None
        self._db_render_poll_after: str | None = None
        self._generated_tex_path: Path | None = None
        self._prepared_session_images_key: tuple[str, str, int] | None = None
        self._session_book_map: dict[str, Path] = {}
        self._session_instances_by_book: dict[str, list[dict[str, object]]] = {}
        self._session_instance_path_map: dict[tuple[str, str], Path] = {}
        self._session_book_record_map: dict[str, dict[str, object]] = {}
        self._session_tree_items: dict[str, dict[str, object]] = {}
        self._syncing_session_tree_selection = False
        self._session_browser_window: tk.Toplevel | None = None
        self._session_browser_canvas: tk.Canvas | None = None
        self._session_browser_cards_host: ttk.Frame | None = None
        self._session_browser_cards_window: int | None = None
        self._session_browser_search_var = tk.StringVar(value="")
        self._session_browser_status_var = tk.StringVar(value="Sin catalogo cargado.")
        self._session_browser_state_var = tk.StringVar(value="todos")
        self._session_browser_course_var = tk.StringVar(value="todos")
        self._session_browser_editorial_var = tk.StringVar(value="todos")
        self._session_browser_author_var = tk.StringVar(value="todos")
        self._session_browser_word_var = tk.StringVar(value="todos")
        self._session_browser_active_key: tuple[str, str] | None = None
        self._session_conversion_queue: list[tuple[str, str]] = []
        self._session_queue_listbox: tk.Listbox | None = None
        self._session_queue_status_var = tk.StringVar(value="Cola: 0 instancia(s)")
        self._session_browser_thumb_refs: list[object] = []
        self._db_book_workspace_cache: dict[tuple[str, str], str] = {}
        self._prepared_db_images_dir: Path | None = None
        self.practice_controller = PracticeBuilderController()
        self.book_progress_controller = BookProgressController()
        self.db_manager = DatabaseManager()
        self._db_render_preview = PreviewWindow(title="Vista previa BD - Modulo 7", width=760, height=900)

        self._apply_light_theme()
        self._build_ui()
        self.estado_var.trace_add("write", lambda *_args: self._on_db_preview_config_change(refresh_count=True))
        self.clave_var.trace_add("write", lambda *_args: self._on_db_preview_config_change(refresh_count=True))
        self.aleatorio_var.trace_add("write", lambda *_args: self._on_db_preview_config_change(refresh_count=False))
        self._session_browser_search_var.trace_add("write", lambda *_args: self._render_session_browser_books())
        self._init_drag_and_drop()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._listar_dbs()

    def _apply_light_theme(self) -> None:
        self.palette = apply_openai_theme(self)

    def _maximize_window(self) -> None:
        try:
            self.state("zoomed")
            return
        except Exception:
            pass
        try:
            self.attributes("-zoomed", True)
        except Exception:
            pass

    def _build_ui(self) -> None:
        root = ttk.Frame(self)
        root.pack(fill="both", expand=True, padx=16, pady=14)

        ttk.Label(root, text="Modulo 7 - Integracion LaTeX a Word", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            root,
            text="Usa tu repo Editor_de_practicas sin duplicar codigo.",
            style="SubHeader.TLabel",
        ).pack(anchor="w", pady=(4, 12))

        cfg = ttk.LabelFrame(root, text="Configuracion", style="Card.TLabelframe")
        cfg.pack(fill="x")
        ttk.Label(cfg, text="Repo Editor_de_practicas").grid(row=0, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(cfg, textvariable=self.repo_var).grid(row=0, column=1, sticky="ew", padx=8, pady=8)
        ttk.Button(cfg, text="Examinar", command=self._pick_repo, style="Ghost.TButton").grid(row=0, column=2, sticky="ew", padx=(0, 8), pady=8)

        ttk.Label(cfg, text="Python ejecutable").grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))
        ttk.Entry(cfg, textvariable=self.python_var).grid(row=1, column=1, sticky="ew", padx=8, pady=(0, 8))
        ttk.Button(cfg, text="Elegir", command=self._pick_python, style="Ghost.TButton").grid(row=1, column=2, sticky="ew", padx=(0, 8), pady=(0, 8))
        cfg.columnconfigure(1, weight=1)

        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=(12, 8))
        ttk.Button(
            actions,
            text="Abrir GUI original (Editor_de_practicas)",
            command=self._open_external_gui,
            style="Secondary.TButton",
        ).pack(side="left")

        quick = ttk.LabelFrame(root, text="Conversion rapida", style="Card.TLabelframe")
        quick.pack(fill="x", pady=(8, 0))

        source = ttk.Frame(quick, style="Panel.TFrame", padding=12)
        source.grid(row=0, column=0, columnspan=3, sticky="ew", padx=8, pady=(8, 8))
        source.columnconfigure(0, weight=1)
        source.columnconfigure(1, weight=1)
        ttk.Label(source, text="Elige una fuente de conversion", style="Section.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        db_card = ttk.Frame(source, style="Card.TFrame", padding=12)
        db_card.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        ttk.Label(db_card, text="Seleccionador de problemas", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            db_card,
            text="Busca, filtra, acumula y ordena problemas desde la base de datos en una ventana dedicada.",
            style="Muted.TLabel",
            wraplength=420,
        ).pack(anchor="w", pady=(4, 10))
        ttk.Button(
            db_card,
            text="Abrir seleccionador",
            command=self._open_db_source_window,
            style="Accent.TButton",
        ).pack(anchor="w")

        session_card = ttk.Frame(source, style="Card.TFrame", padding=12)
        session_card.grid(row=1, column=1, sticky="nsew", padx=(8, 0))
        ttk.Label(session_card, text="Sesion Transcriptor IA", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            session_card,
            text="Abre solo la biblioteca visual de sesiones. Click selecciona; doble click abre Word exportado.",
            style="Muted.TLabel",
            wraplength=420,
        ).pack(anchor="w", pady=(4, 10))
        ttk.Button(
            session_card,
            text="Abrir biblioteca de sesiones",
            command=self._open_session_source_window,
            style="Accent.TButton",
        ).pack(anchor="w")

        self.tex_frame = ttk.Frame(quick)
        self.tex_frame.grid(row=1, column=0, columnspan=3, sticky="ew", padx=0, pady=(4, 0))
        self.tex_frame.grid_remove()

        self.db_frame = ttk.Frame(quick)
        self.db_frame.grid(row=2, column=0, columnspan=3, sticky="ew", padx=0, pady=(4, 0))

        ttk.Label(self.db_frame, text="Seleccionador").grid(row=0, column=0, sticky="w", padx=8, pady=8)
        self.combo_db = ttk.Combobox(self.db_frame, state="readonly", textvariable=self.db_name_var, values=[])
        self.combo_db.grid(row=0, column=1, sticky="ew", padx=8, pady=8)
        self.combo_db.bind("<<ComboboxSelected>>", lambda _e: self._on_db_change())
        ttk.Button(self.db_frame, text="Refrescar", command=self._listar_dbs, style="Ghost.TButton").grid(
            row=0, column=2, sticky="ew", padx=(0, 8), pady=8
        )

        ttk.Label(self.db_frame, text="Curso").grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))
        self.combo_curso = ttk.Combobox(self.db_frame, textvariable=self.curso_var, values=["Todos"])
        self.combo_curso.grid(row=1, column=1, sticky="ew", padx=8, pady=(0, 8))
        self.combo_curso.bind("<<ComboboxSelected>>", lambda _e: self._on_curso_change())

        ttk.Label(self.db_frame, text="Tema").grid(row=1, column=2, sticky="w", padx=8, pady=(0, 8))
        self.combo_tema = ttk.Combobox(self.db_frame, textvariable=self.tema_var, values=["Todos"])
        self.combo_tema.grid(row=1, column=3, sticky="ew", padx=(0, 8), pady=(0, 8))
        self.combo_tema.bind("<<ComboboxSelected>>", lambda _e: self._on_tema_change())

        ttk.Label(self.db_frame, text="Subtema").grid(row=2, column=0, sticky="w", padx=8, pady=(0, 8))
        self.combo_subtema = ttk.Combobox(self.db_frame, textvariable=self.subtema_var, values=["Todos"])
        self.combo_subtema.grid(row=2, column=1, sticky="ew", padx=8, pady=(0, 8))
        self.combo_subtema.bind("<<ComboboxSelected>>", lambda _e: self._on_subtema_change())

        ttk.Label(self.db_frame, text="Estado").grid(row=2, column=2, sticky="w", padx=8, pady=(0, 8))
        ttk.Combobox(
            self.db_frame,
            textvariable=self.estado_var,
            state="readonly",
            values=["Todos", "Sin revisar", "Consistente", "Inconsistente"],
        ).grid(row=2, column=3, sticky="ew", padx=(0, 8), pady=(0, 8))

        ttk.Label(self.db_frame, text="Autor").grid(row=3, column=0, sticky="w", padx=8, pady=(0, 8))
        self.combo_autor = ttk.Combobox(self.db_frame, textvariable=self.autor_var, values=["Todos"])
        self.combo_autor.grid(row=3, column=1, sticky="ew", padx=8, pady=(0, 8))
        self.combo_autor.bind("<<ComboboxSelected>>", lambda _e: self._on_autor_change())

        ttk.Label(self.db_frame, text="Editorial").grid(row=3, column=2, sticky="w", padx=8, pady=(0, 8))
        self.combo_editorial = ttk.Combobox(self.db_frame, textvariable=self.editorial_var, values=["Todos"])
        self.combo_editorial.grid(row=3, column=3, sticky="ew", padx=(0, 8), pady=(0, 8))
        self.combo_editorial.bind("<<ComboboxSelected>>", lambda _e: self._on_editorial_change())

        ttk.Checkbutton(self.db_frame, text="Seleccion aleatoria (desmarcar = orden por numero)", variable=self.aleatorio_var).grid(
            row=4, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 8)
        )
        ttk.Label(self.db_frame, text="Clave").grid(row=4, column=2, sticky="w", padx=8, pady=(0, 8))
        ttk.Combobox(
            self.db_frame,
            textvariable=self.clave_var,
            state="readonly",
            values=["Todos", "Con clave", "Sin clave", "Abiertos"],
        ).grid(row=4, column=3, sticky="ew", padx=(0, 8), pady=(0, 8))

        ttk.Label(self.db_frame, text="Titulo del lote").grid(row=5, column=0, sticky="w", padx=8, pady=(0, 8))
        ttk.Entry(self.db_frame, textvariable=self.titulo_var).grid(
            row=5, column=1, columnspan=3, sticky="ew", padx=(8, 8), pady=(0, 8)
        )

        ttk.Label(self.db_frame, text="Estructura").grid(row=6, column=0, sticky="nw", padx=8, pady=(0, 8))
        structure_box = ttk.Frame(self.db_frame)
        structure_box.grid(row=6, column=1, columnspan=3, sticky="ew", padx=(8, 8), pady=(0, 8))
        self.txt_practice_structure = tk.Text(
            structure_box,
            height=4,
            bg=self.palette["surface"],
            fg=self.palette["text"],
            insertbackground=self.palette["text"],
            relief="solid",
            bd=1,
            wrap="word",
            highlightthickness=1,
            highlightbackground=self.palette["border"],
        )
        self.txt_practice_structure.pack(side="left", fill="both", expand=True)
        structure_scroll = ttk.Scrollbar(structure_box, orient="vertical", command=self.txt_practice_structure.yview)
        structure_scroll.pack(side="right", fill="y")
        self.txt_practice_structure.configure(yscrollcommand=structure_scroll.set)
        ttk.Label(
            structure_box,
            text="Ejemplo: # Factorizacion | ## Factor comun: 1-8",
            style="Muted.TLabel",
        ).pack(side="bottom", anchor="w", pady=(4, 0))

        ttk.Checkbutton(
            self.db_frame,
            text="Agregar hoja final de clave",
            variable=self.incluir_clave_final_var,
        ).grid(row=7, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 8))
        ttk.Label(self.db_frame, textvariable=self._db_source_count_var).grid(
            row=7, column=2, columnspan=2, sticky="e", padx=(8, 8), pady=(0, 8)
        )

        preview_actions = ttk.Frame(self.db_frame)
        preview_actions.grid(row=8, column=0, columnspan=4, sticky="ew", padx=8, pady=(4, 8))
        ttk.Button(
            preview_actions,
            text="Abrir visualizador",
            command=self._open_db_preview_window,
            style="Ghost.TButton",
        ).pack(side="left")
        ttk.Label(preview_actions, textvariable=self._db_preview_count_var).pack(side="right")

        self.db_frame.columnconfigure(1, weight=1)
        self.db_frame.columnconfigure(3, weight=1)

        self.session_frame = ttk.Frame(quick)
        self.session_frame.grid(row=3, column=0, columnspan=3, sticky="ew", padx=0, pady=(4, 0))
        ttk.Label(self.session_frame, text="Archivo de sesion").grid(row=0, column=0, sticky="w", padx=8, pady=8)
        self.entry_session_path = ttk.Entry(self.session_frame, textvariable=self.session_path_var)
        self.entry_session_path.grid(row=0, column=1, sticky="ew", padx=8, pady=8)
        ttk.Button(
            self.session_frame,
            text="Elegir",
            command=self._pick_session_file,
            style="Ghost.TButton",
        ).grid(row=0, column=2, sticky="ew", padx=(0, 8), pady=8)
        ttk.Button(
            self.session_frame,
            text="Limpiar",
            command=self._clear_session_selection,
            style="Ghost.TButton",
        ).grid(row=0, column=3, sticky="ew", padx=(0, 8), pady=8)
        ttk.Label(self.session_frame, text="Raiz libros/sesiones").grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))
        ttk.Entry(self.session_frame, textvariable=self.session_root_var).grid(row=1, column=1, sticky="ew", padx=8, pady=(0, 8))
        ttk.Button(
            self.session_frame,
            text="Elegir raiz",
            command=self._pick_session_root,
            style="Ghost.TButton",
        ).grid(row=1, column=2, sticky="ew", padx=(0, 8), pady=(0, 8))
        ttk.Button(
            self.session_frame,
            text="Escanear",
            command=self._refresh_session_catalog,
            style="Ghost.TButton",
        ).grid(row=1, column=3, sticky="ew", padx=(0, 8), pady=(0, 8))
        ttk.Label(self.session_frame, text="Libro").grid(row=2, column=0, sticky="w", padx=8, pady=(0, 8))
        self.combo_session_book = ttk.Combobox(
            self.session_frame,
            textvariable=self.session_book_var,
            state="readonly",
            values=[],
        )
        self.combo_session_book.grid(row=2, column=1, sticky="ew", padx=8, pady=(0, 8))
        self.combo_session_book.bind("<<ComboboxSelected>>", lambda _e: self._on_session_book_change())
        ttk.Label(self.session_frame, text="Instancia / sesion").grid(row=2, column=2, sticky="w", padx=8, pady=(0, 8))
        self.combo_session_instance = ttk.Combobox(
            self.session_frame,
            textvariable=self.session_instance_var,
            state="readonly",
            values=[],
        )
        self.combo_session_instance.grid(row=2, column=3, sticky="ew", padx=(0, 8), pady=(0, 8))
        self.combo_session_instance.bind("<<ComboboxSelected>>", lambda _e: self._on_session_instance_change())
        ttk.Label(
            self.session_frame,
            text="Puedes arrastrar y soltar aqui un archivo .json de sesion.",
        ).grid(row=3, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 4))
        ttk.Label(
            self.session_frame,
            text="Se preserva la salida final de la sesion tal como esta almacenada.",
        ).grid(row=4, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 2))
        ttk.Label(
            self.session_frame,
            textvariable=self._session_catalog_status_var,
        ).grid(row=5, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 8))
        ttk.Checkbutton(
            self.session_frame,
            text="Agregar hoja final de clave",
            variable=self.incluir_clave_final_var,
        ).grid(row=6, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 8))
        session_browser_actions = ttk.Frame(self.session_frame)
        session_browser_actions.grid(row=7, column=0, columnspan=4, sticky="ew", padx=8, pady=(0, 8))
        ttk.Button(
            session_browser_actions,
            text="Abrir biblioteca de sesiones",
            command=self._open_session_browser_window,
            style="Accent.TButton",
        ).pack(side="left")
        ttk.Button(
            session_browser_actions,
            text="Abrir Word seleccionado",
            command=self._open_selected_session_word,
            style="Ghost.TButton",
        ).pack(side="left", padx=(8, 0))
        ttk.Label(
            session_browser_actions,
            text="La biblioteca muestra libros e instancias; verde = Word exportado.",
        ).pack(side="left", padx=(12, 0))
        self.session_frame.columnconfigure(1, weight=1)
        self.session_frame.columnconfigure(3, weight=1)

        ttk.Label(quick, text="Output .docx").grid(row=4, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(quick, textvariable=self.output_docx_var).grid(row=4, column=1, sticky="ew", padx=8, pady=8)
        ttk.Button(quick, text="Guardar como", command=self._pick_output_docx, style="Ghost.TButton").grid(
            row=4, column=2, sticky="ew", padx=(0, 8), pady=8
        )

        ttk.Label(quick, text="Template .docx").grid(row=5, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(quick, textvariable=self.template_var).grid(row=5, column=1, sticky="ew", padx=8, pady=8)
        ttk.Button(quick, text="Elegir", command=self._pick_template, style="Ghost.TButton").grid(
            row=5, column=2, sticky="ew", padx=(0, 8), pady=8
        )

        ttk.Label(quick, text="Carpeta imagenes").grid(row=6, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(quick, textvariable=self.images_dir_var).grid(row=6, column=1, sticky="ew", padx=8, pady=8)
        ttk.Button(quick, text="Elegir", command=self._pick_images_dir, style="Ghost.TButton").grid(
            row=6, column=2, sticky="ew", padx=(0, 8), pady=8
        )

        ttk.Label(quick, text="Style").grid(row=7, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(quick, textvariable=self.style_var).grid(row=7, column=1, sticky="ew", padx=8, pady=8)
        ttk.Button(quick, text="Convertir", command=self._convert_async, style="Accent.TButton").grid(
            row=7, column=2, sticky="ew", padx=(0, 8), pady=8
        )

        quick.columnconfigure(1, weight=1)
        self.tex_frame.grid_remove()
        self.db_frame.grid_remove()
        self.session_frame.grid_remove()
        self._on_source_mode_change()

        ttk.Label(root, text="Log", style="Section.TLabel").pack(anchor="w", pady=(12, 4))
        self.log = tk.Text(
            root,
            height=15,
            bg=self.palette["surface"],
            fg=self.palette["text"],
            insertbackground=self.palette["text"],
            relief="solid",
            bd=1,
            wrap="word",
            highlightthickness=1,
            highlightbackground=self.palette["border"],
        )
        self.log.pack(fill="both", expand=True)

        ttk.Label(root, textvariable=self.status_var).pack(anchor="w", pady=(8, 0))

    def _default_editor_repo(self) -> Path:
        candidates = [
            Path(__file__).resolve().parents[3] / "Editor_de_practicas",
            Path(r"K:\Github\Editor_de_practicas"),
            Path(r"E:\Github\Editor_de_practicas"),
        ]
        for default in candidates:
            if default.exists():
                return default
        return Path.cwd()

    def _default_template(self, repo: str) -> str:
        candidate = Path(repo) / "plantilla.docx"
        return str(candidate) if candidate.exists() else ""

    def _default_sessions_root(self) -> Path:
        candidates = [
            Path(r"E:\Banco de Preguntas"),
            Path(r"K:\Banco de Preguntas"),
            Path.home() / "Documents" / "Banco de Preguntas",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return Path.cwd()

    def _python_candidates(self, repo: str, preferred: str = "") -> list[str]:
        out: list[str] = []
        seen: set[str] = set()

        def add(value: str) -> None:
            v = str(value or "").strip()
            if not v:
                return
            key = v.lower()
            if key in seen:
                return
            seen.add(key)
            out.append(v)

        repo_path = Path(repo)
        add(preferred)
        add(str(repo_path / ".venv" / "Scripts" / "python.exe"))
        add(str(repo_path / "venv" / "Scripts" / "python.exe"))
        add(str(Path.cwd() / ".venv" / "Scripts" / "python.exe"))
        add(str(Path.cwd() / "venv" / "Scripts" / "python.exe"))
        add(str(Path(sys.executable)))
        add("python")
        return out

    def _probe_python(self, exe: str) -> tuple[bool, str]:
        cmd = [str(exe), "-c", "import sys; print(sys.executable)"]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
            )
        except Exception as exc:
            return (False, str(exc))
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout or f"exit={proc.returncode}").strip()
            return (False, msg)
        ok_msg = (proc.stdout or "").strip() or str(exe)
        return (True, ok_msg)

    def _resolve_python(self, repo: str, preferred: str = "") -> tuple[str, list[str]]:
        errors: list[str] = []
        for candidate in self._python_candidates(repo, preferred):
            is_path_like = ("\\" in candidate) or ("/" in candidate) or candidate.lower().endswith(".exe")
            if is_path_like:
                p = Path(candidate)
                if not p.exists():
                    errors.append(f"{candidate}: no existe")
                    continue
            ok, msg = self._probe_python(candidate)
            if ok:
                return (candidate, errors)
            errors.append(f"{candidate}: {msg}")
        # Fallback final (deberia existir siempre en esta app)
        return (str(Path(sys.executable)), errors)

    def _detect_python(self, repo: str) -> str:
        py, _errs = self._resolve_python(repo, "")
        return py

    def _log(self, text: str) -> None:
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.update_idletasks()

    def _pick_repo(self) -> None:
        selected = filedialog.askdirectory(title="Selecciona Editor_de_practicas")
        if not selected:
            return
        self.repo_var.set(selected)
        self.python_var.set(self._detect_python(selected))
        self.template_var.set(self._default_template(selected))

    def _pick_python(self) -> None:
        selected = filedialog.askopenfilename(
            title="Selecciona python.exe",
            filetypes=[("Python", "python.exe"), ("Todos", "*.*")],
        )
        if selected:
            self.python_var.set(selected)

    def _pick_input_tex(self) -> None:
        selected = filedialog.askopenfilename(title="Selecciona archivo .tex", filetypes=[("TeX", "*.tex")])
        if not selected:
            return
        self.input_tex_var.set(selected)
        if not self.output_docx_var.get().strip():
            self.output_docx_var.set(str(Path(selected).with_suffix(".docx")))

    def _pick_session_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="Selecciona archivo de sesion",
            filetypes=[("Sesion JSON", "*.json"), ("Todos", "*.*")],
        )
        if not selected:
            return
        self._set_session_selection(Path(selected))

    def _pick_session_root(self) -> None:
        selected = filedialog.askdirectory(title="Selecciona raiz de libros / sesiones")
        if not selected:
            return
        self.session_root_var.set(selected)
        self._refresh_session_catalog()

    def _clear_session_selection(self) -> None:
        self.session_path_var.set("")
        self.images_dir_var.set("")
        self._prepared_session_images_key = None
        self.session_instance_var.set("")
        tree = getattr(self, "session_tree", None)
        if tree is not None:
            self._syncing_session_tree_selection = True
            try:
                tree.selection_remove(tree.selection())
            finally:
                self._syncing_session_tree_selection = False

    def _selected_session_path(self) -> Path | None:
        raw = (self.session_path_var.get() or "").strip()
        return remap_legacy_drive_path(Path(raw), prefer_existing=True) if raw else None

    def _set_session_selection(self, session_path: Path | None, *, sync_browser: bool = True) -> None:
        if session_path is None:
            self._clear_session_selection()
            return
        session_path = remap_legacy_drive_path(Path(str(session_path).strip()), prefer_existing=True)
        if not str(session_path):
            self._clear_session_selection()
            return
        self.session_path_var.set(str(session_path))
        self.output_docx_var.set(str(session_path.with_suffix(".docx")))
        self._prepared_session_images_key = None
        if sync_browser:
            self._sync_session_browser_to_path(session_path)
        self._apply_session_metadata(session_path)

    def _session_word_candidates(self, session_path: Path | None) -> list[Path]:
        if session_path is None:
            return []
        candidates: list[Path] = []

        def add(path: Path) -> None:
            normalized = remap_legacy_drive_path(path, prefer_existing=True)
            if normalized not in candidates:
                candidates.append(normalized)

        add(session_path.with_suffix(".docx"))
        name = session_path.name
        if name.endswith(".session.json"):
            add(session_path.with_name(name.removesuffix(".session.json") + ".docx"))
        if session_path.stem.endswith(".session"):
            add(session_path.with_name(session_path.stem.removesuffix(".session") + ".docx"))
        return candidates

    def _session_word_path_for(self, session_path: Path | None) -> Path | None:
        candidates = self._session_word_candidates(session_path)
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0] if candidates else None

    def _session_word_exists(self, session_path: Path | None) -> bool:
        return any(path.exists() for path in self._session_word_candidates(session_path))

    def _refresh_session_tree(self) -> None:
        tree = getattr(self, "session_tree", None)
        if tree is None:
            return
        for item_id in tree.get_children():
            tree.delete(item_id)
        self._session_tree_items = {}
        book_labels = sorted(self._session_instances_by_book.keys(), key=str.casefold)
        current_path = self._selected_session_path()
        selected_iid = ""
        row_index = 0
        for book_label in book_labels:
            rows = self._session_instances_by_book.get(book_label, [])
            for row in rows:
                instance_label = str(row.get("label") or "").strip()
                if not instance_label:
                    continue
                session_path = row.get("path")
                if session_path is None:
                    session_path = self._session_instance_path_map.get((book_label, instance_label))
                session_path = Path(session_path) if session_path is not None else None
                word_path = self._session_word_path_for(session_path)
                has_word = bool(word_path and word_path.exists())
                iid = f"session-{row_index}"
                row_index += 1
                tree.insert(
                    "",
                    "end",
                    iid=iid,
                    values=(book_label, instance_label, "Listo" if has_word else "Pendiente"),
                    tags=("word_ready" if has_word else "word_missing",),
                )
                self._session_tree_items[iid] = {
                    "book_label": book_label,
                    "instance_label": instance_label,
                    "session_path": session_path,
                    "word_path": word_path,
                }
                if current_path is not None and session_path is not None:
                    try:
                        if remap_legacy_drive_path(session_path, prefer_existing=True) == remap_legacy_drive_path(current_path, prefer_existing=True):
                            selected_iid = iid
                    except Exception:
                        pass
        if selected_iid:
            self._syncing_session_tree_selection = True
            try:
                tree.selection_set(selected_iid)
                tree.focus(selected_iid)
                tree.see(selected_iid)
            finally:
                self._syncing_session_tree_selection = False

    def _refresh_session_tree_word_states(self) -> None:
        self._refresh_session_tree()
        self._render_session_browser_books()

    def _select_session_instance_from_catalog(
        self,
        *,
        book_label: str,
        instance_label: str,
        session_path: Path | None,
    ) -> None:
        if book_label:
            self.session_book_var.set(book_label)
            rows = self._session_instances_by_book.get(book_label, [])
            labels = [str(row.get("label") or "").strip() for row in rows if str(row.get("label") or "").strip()]
            self.combo_session_instance["values"] = labels
        if instance_label:
            self.session_instance_var.set(instance_label)
        if session_path is not None:
            self._set_session_selection(Path(session_path), sync_browser=False)
        else:
            self.status_var.set("Instancia sin archivo de sesion resoluble.")
        self._session_browser_active_key = (book_label, instance_label)
        self._render_session_browser_books()

    def _select_session_tree_item(self, iid: str, *, open_word: bool = False) -> None:
        payload = self._session_tree_items.get(iid)
        if not payload:
            return
        book_label = str(payload.get("book_label") or "").strip()
        instance_label = str(payload.get("instance_label") or "").strip()
        session_path = payload.get("session_path")
        self._select_session_instance_from_catalog(
            book_label=book_label,
            instance_label=instance_label,
            session_path=Path(session_path) if session_path is not None else None,
        )
        if open_word:
            self._open_session_word_payload(payload)

    def _on_session_tree_select(self, _event=None) -> None:
        if self._syncing_session_tree_selection:
            return
        tree = getattr(self, "session_tree", None)
        if tree is None:
            return
        selected = tree.selection()
        if selected:
            self._select_session_tree_item(selected[0], open_word=False)

    def _on_session_tree_open_word(self, _event=None) -> None:
        self._open_selected_session_word()

    def _open_selected_session_word(self) -> None:
        tree = getattr(self, "session_tree", None)
        if tree is not None:
            selected = tree.selection()
            if selected:
                payload = self._session_tree_items.get(selected[0])
                if payload:
                    self._open_session_word_payload(payload)
                    return
        book_label = (self.session_book_var.get() or "").strip()
        instance_label = (self.session_instance_var.get() or "").strip()
        session_path = self._selected_session_path()
        if session_path is None:
            messagebox.showinfo("Modulo 7", "Selecciona una instancia de la biblioteca de sesiones.")
            return
        self._open_session_word_payload(
            {
                "book_label": book_label,
                "instance_label": instance_label,
                "session_path": session_path,
            }
        )

    def _open_session_word_payload(self, payload: dict[str, object]) -> None:
        session_path = payload.get("session_path")
        word_path = self._session_word_path_for(Path(session_path) if session_path is not None else None)
        if word_path is None or not word_path.exists():
            instance_label = str(payload.get("instance_label") or "esta instancia")
            messagebox.showinfo("Modulo 7", f"Todavia no hay Word exportado para:\n{instance_label}")
            return
        try:
            os.startfile(str(word_path))  # type: ignore[attr-defined]
            self.status_var.set(f"Word abierto: {word_path.name}")
        except Exception as exc:
            messagebox.showerror("Modulo 7", f"No se pudo abrir el Word:\n{word_path}\n\n{exc}")

    def _convert_selected_session_from_browser(self) -> None:
        session_path = self._selected_session_path()
        if session_path is None:
            messagebox.showinfo("Modulo 7", "Selecciona primero una instancia en la biblioteca de sesiones.")
            return
        self.source_mode_var.set("session")
        self._on_source_mode_change()
        output_docx = self._session_word_path_for(session_path) or session_path.with_suffix(".docx")
        self.output_docx_var.set(str(output_docx))
        self._convert_async()

    def _session_browser_selected_payload(self) -> dict[str, object] | None:
        key = self._session_browser_active_key
        if key is None:
            return None
        book_label, instance_label = key
        session_path = self._session_instance_path_map.get(key)
        if session_path is None:
            return None
        return {
            "book_label": book_label,
            "instance_label": instance_label,
            "session_path": session_path,
            "word_path": self._session_word_path_for(session_path),
        }

    def _session_queue_label(self, key: tuple[str, str]) -> str:
        book_label, instance_label = key
        session_path = self._session_instance_path_map.get(key)
        word_ready = self._session_word_exists(session_path)
        marker = "Word" if word_ready else "Pendiente"
        title = book_label.split(" | ", 1)[1] if " | " in book_label else book_label
        return f"[{marker}] {title} -> {instance_label}"

    def _refresh_session_queue_view(self) -> None:
        listbox = self._session_queue_listbox
        valid_queue: list[tuple[str, str]] = []
        for key in self._session_conversion_queue:
            if key in self._session_instance_path_map and key not in valid_queue:
                valid_queue.append(key)
        self._session_conversion_queue = valid_queue
        self._session_queue_status_var.set(f"Cola: {len(valid_queue)} instancia(s)")
        if listbox is not None and listbox.winfo_exists():
            listbox.delete(0, "end")
            for key in valid_queue:
                listbox.insert("end", self._session_queue_label(key))
        self._render_session_browser_books()

    def _add_session_key_to_queue(self, key: tuple[str, str]) -> None:
        if key not in self._session_instance_path_map:
            return
        if key not in self._session_conversion_queue:
            self._session_conversion_queue.append(key)
        self._refresh_session_queue_view()

    def _add_selected_session_to_queue(self) -> None:
        payload = self._session_browser_selected_payload()
        if payload is None:
            messagebox.showinfo("Modulo 7", "Selecciona una instancia para agregarla a la cola.")
            return
        key = (str(payload.get("book_label") or ""), str(payload.get("instance_label") or ""))
        self._add_session_key_to_queue(key)

    def _remove_selected_session_from_queue(self) -> None:
        listbox = self._session_queue_listbox
        index: int | None = None
        if listbox is not None and listbox.winfo_exists():
            selected = listbox.curselection()
            if selected:
                index = int(selected[0])
        if index is not None and 0 <= index < len(self._session_conversion_queue):
            del self._session_conversion_queue[index]
        elif self._session_browser_active_key in self._session_conversion_queue:
            self._session_conversion_queue.remove(self._session_browser_active_key)  # type: ignore[arg-type]
        else:
            messagebox.showinfo("Modulo 7", "Selecciona una instancia de la cola para quitarla.")
            return
        self._refresh_session_queue_view()

    def _clear_session_conversion_queue(self) -> None:
        self._session_conversion_queue.clear()
        self._refresh_session_queue_view()

    def _convert_session_queue(self) -> None:
        if self._running:
            messagebox.showwarning("Modulo 7", "Ya hay una conversion en curso.")
            return
        if not self._session_conversion_queue and self._session_browser_active_key is not None:
            self._add_session_key_to_queue(self._session_browser_active_key)
        jobs: list[tuple[tuple[str, str], Path, Path]] = []
        for key in self._session_conversion_queue:
            session_path = self._session_instance_path_map.get(key)
            if session_path is None or not session_path.exists():
                continue
            output_docx = self._session_word_path_for(session_path) or session_path.with_suffix(".docx")
            jobs.append((key, session_path, output_docx))
        if not jobs:
            messagebox.showinfo("Modulo 7", "Agrega una o mas instancias validas a la cola.")
            return

        repo = Path(self.repo_var.get().strip())
        py, errors = self._resolve_python(str(repo), self.python_var.get().strip())
        self.python_var.set(py)
        script = repo / "latex_to_word.py"
        template = Path(self.template_var.get().strip()) if self.template_var.get().strip() else None
        style = (self.style_var.get() or "").strip() or "Estilo_plantilla"
        if not script.exists():
            messagebox.showerror("Modulo 7", f"No existe script:\n{script}")
            return
        if errors:
            self._log("Aviso: algunos python candidatos fallaron. Se usa fallback valido.")
            for err in errors[:4]:
                self._log(f" - {err}")

        self.source_mode_var.set("session")
        self._on_source_mode_change()
        self._running = True
        self.status_var.set(f"Convirtiendo cola: 0/{len(jobs)}")

        def worker() -> None:
            produced: list[Path] = []
            failures: list[str] = []
            for index, (key, session_path, raw_output_docx) in enumerate(jobs, start=1):
                book_label, instance_label = key
                try:
                    output_docx = self._normalize_output_docx_path(str(raw_output_docx))
                    output_docx.parent.mkdir(parents=True, exist_ok=True)
                    self.after(
                        0,
                        lambda i=index, total=len(jobs), name=instance_label: self.status_var.set(
                            f"Convirtiendo cola: {i}/{total} -> {name}"
                        ),
                    )
                    self.after(0, lambda path=output_docx: self.output_docx_var.set(str(path)))
                    input_tex = self._resolve_session_input_tex(
                        session_path=session_path,
                        output_docx=output_docx,
                        emit_log=False,
                    )
                    self.after(
                        0,
                        lambda generated=input_tex, sess=session_path: self._log(
                            f"Fuente sesion -> .tex generado sin alterar estructura: {generated} | sesion={sess}"
                        ),
                    )
                    images_dir: Path | None = None
                    try:
                        images_dir = self._prepare_images_dir_for_session(
                            session_path,
                            update_ui=False,
                            output_docx=output_docx,
                        )
                    except Exception as exc:
                        self.after(0, lambda e=exc: self._log(f"Aviso preparando imagenes de sesion: {e}"))
                    ok, paths = self._run_tex_to_word_conversion(
                        repo=repo,
                        py=py,
                        script=script,
                        input_tex=input_tex,
                        output_docx=output_docx,
                        template=template,
                        images_dir=images_dir,
                        style=style,
                    )
                    if not ok:
                        self.after(
                            0,
                            lambda name=instance_label: self._log(
                                f"Conversion cola: reintentando '{name}' tras enfriar Word COM..."
                            ),
                        )
                        time.sleep(5.0)
                        ok, paths = self._run_tex_to_word_conversion(
                            repo=repo,
                            py=py,
                            script=script,
                            input_tex=input_tex,
                            output_docx=output_docx,
                            template=template,
                            images_dir=images_dir,
                            style=style,
                        )
                    if ok:
                        produced.extend(paths)
                    else:
                        failures.append(f"{book_label} -> {instance_label}")
                except Exception as exc:
                    failures.append(f"{book_label} -> {instance_label}: {exc}")
                finally:
                    # Word COM necesita respirar entre lotes grandes; sin esta pausa
                    # puede rechazar llamadas o dejar temporales bloqueados.
                    time.sleep(2.0)

            def done() -> None:
                self._running = False
                self._refresh_session_tree_word_states()
                self._refresh_session_queue_view()
                if failures:
                    self.status_var.set("Cola terminada con errores")
                    detail = "\n".join(failures[:8])
                    extra = "\n..." if len(failures) > 8 else ""
                    messagebox.showwarning(
                        "Modulo 7",
                        f"Word generados: {len(produced)}\nFallaron: {len(failures)}\n\n{detail}{extra}",
                    )
                else:
                    self.status_var.set("Cola convertida")
                    messagebox.showinfo("Modulo 7", f"Conversion de cola completada.\nWord generados: {len(produced)}")

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _open_session_browser_window(self) -> None:
        if not self._session_instances_by_book:
            try:
                self._refresh_session_catalog()
            except Exception as exc:
                messagebox.showerror("Modulo 7", f"No se pudo cargar el catalogo de sesiones.\n{exc}")
                return
        if self._session_browser_window is not None and self._session_browser_window.winfo_exists():
            self._session_browser_window.lift()
            self._render_session_browser_books()
            return
        current_book = (self.session_book_var.get() or "").strip()
        current_instance = (self.session_instance_var.get() or "").strip()
        self._session_browser_active_key = (current_book, current_instance) if current_book and current_instance else None

        window = tk.Toplevel(self)
        window.title("Biblioteca de Sesiones Transcriptor IA")
        window.geometry("1320x820")
        window.minsize(1100, 680)
        self._session_browser_window = window

        def on_close() -> None:
            self._session_browser_window = None
            self._session_browser_canvas = None
            self._session_browser_cards_host = None
            self._session_browser_cards_window = None
            self._session_queue_listbox = None
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", on_close)

        header = ttk.Frame(window)
        header.pack(fill="x", padx=18, pady=(16, 4))
        ttk.Label(header, text="Sesion Transcriptor IA -> Word", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Click en una instancia para seleccionarla y convertirla. Doble click abre el Word exportado si ya existe.",
            style="SubHeader.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        top = ttk.Frame(window, style="Card.TFrame", padding=14)
        top.pack(fill="x", padx=18, pady=(10, 0))
        ttk.Label(top, text="Buscar libro o instancia").grid(row=0, column=0, sticky="w")
        search = ttk.Entry(top, textvariable=self._session_browser_search_var)
        search.grid(row=0, column=1, sticky="ew", padx=(8, 12))
        search.bind("<KeyRelease>", lambda _e: self._render_session_browser_books())
        ttk.Button(
            top,
            text="Refrescar",
            command=self._refresh_session_catalog,
            style="Ghost.TButton",
        ).grid(row=0, column=2, sticky="ew", padx=(0, 8))
        ttk.Button(
            top,
            text="Convertir instancia seleccionada",
            command=self._convert_selected_session_from_browser,
            style="Accent.TButton",
        ).grid(row=0, column=3, sticky="ew", padx=(0, 8))
        ttk.Button(
            top,
            text="Abrir Word",
            command=self._open_selected_session_word,
            style="Ghost.TButton",
        ).grid(row=0, column=4, sticky="ew", padx=(0, 8))
        ttk.Button(
            top,
            text="Cerrar",
            command=on_close,
            style="Ghost.TButton",
        ).grid(row=0, column=5, sticky="ew")
        ttk.Label(top, text="Estado").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.combo_session_browser_state = ttk.Combobox(
            top,
            state="readonly",
            textvariable=self._session_browser_state_var,
            values=["todos"],
            width=16,
        )
        self.combo_session_browser_state.grid(row=1, column=1, sticky="ew", padx=(8, 12), pady=(10, 0))
        self.combo_session_browser_state.bind("<<ComboboxSelected>>", lambda _e: self._render_session_browser_books())

        ttk.Label(top, text="Curso").grid(row=1, column=2, sticky="w", pady=(10, 0))
        self.combo_session_browser_course = ttk.Combobox(
            top,
            state="readonly",
            textvariable=self._session_browser_course_var,
            values=["todos"],
        )
        self.combo_session_browser_course.grid(row=1, column=3, columnspan=3, sticky="ew", pady=(10, 0))
        self.combo_session_browser_course.bind("<<ComboboxSelected>>", lambda _e: self._render_session_browser_books())

        ttk.Label(top, text="Editorial").grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.combo_session_browser_editorial = ttk.Combobox(
            top,
            state="readonly",
            textvariable=self._session_browser_editorial_var,
            values=["todos"],
        )
        self.combo_session_browser_editorial.grid(row=2, column=1, sticky="ew", padx=(8, 12), pady=(10, 0))
        self.combo_session_browser_editorial.bind("<<ComboboxSelected>>", lambda _e: self._render_session_browser_books())

        ttk.Label(top, text="Autor").grid(row=2, column=2, sticky="w", pady=(10, 0))
        self.combo_session_browser_author = ttk.Combobox(
            top,
            state="readonly",
            textvariable=self._session_browser_author_var,
            values=["todos"],
        )
        self.combo_session_browser_author.grid(row=2, column=3, columnspan=3, sticky="ew", pady=(10, 0))
        self.combo_session_browser_author.bind("<<ComboboxSelected>>", lambda _e: self._render_session_browser_books())

        ttk.Label(top, text="Word").grid(row=3, column=0, sticky="w", pady=(10, 0))
        self.combo_session_browser_word = ttk.Combobox(
            top,
            state="readonly",
            textvariable=self._session_browser_word_var,
            values=["todos", "listo", "pendiente"],
            width=16,
        )
        self.combo_session_browser_word.grid(row=3, column=1, sticky="ew", padx=(8, 12), pady=(10, 0))
        self.combo_session_browser_word.bind("<<ComboboxSelected>>", lambda _e: self._render_session_browser_books())
        top.columnconfigure(1, weight=1)
        top.columnconfigure(3, weight=1)

        queue_frame = ttk.Frame(window, style="Card.TFrame", padding=12)
        queue_frame.pack(fill="x", padx=18, pady=(10, 0))
        queue_frame.columnconfigure(0, weight=1)
        queue_frame.columnconfigure(1, weight=0)
        ttk.Label(queue_frame, textvariable=self._session_queue_status_var, style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            queue_frame,
            text="Agrega varias instancias y conviertelas en bloque. Doble click en una instancia abre su Word si existe.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(3, 8))
        queue_list = tk.Listbox(
            queue_frame,
            height=4,
            bd=1,
            relief="solid",
            highlightthickness=0,
            selectmode="browse",
            bg="#FFFFFF",
            fg=self.palette["text"],
            selectbackground="#DBEAFE",
            selectforeground="#1D4ED8",
        )
        queue_list.grid(row=2, column=0, sticky="ew", padx=(0, 12))
        self._session_queue_listbox = queue_list
        queue_buttons = ttk.Frame(queue_frame, style="Card.TFrame")
        queue_buttons.grid(row=2, column=1, sticky="nsew")
        ttk.Button(
            queue_buttons,
            text="Agregar seleccion",
            command=self._add_selected_session_to_queue,
            style="Ghost.TButton",
        ).pack(fill="x", pady=(0, 6))
        ttk.Button(
            queue_buttons,
            text="Quitar de cola",
            command=self._remove_selected_session_from_queue,
            style="Ghost.TButton",
        ).pack(fill="x", pady=(0, 6))
        ttk.Button(
            queue_buttons,
            text="Convertir cola",
            command=self._convert_session_queue,
            style="Accent.TButton",
        ).pack(fill="x", pady=(0, 6))
        ttk.Button(
            queue_buttons,
            text="Limpiar cola",
            command=self._clear_session_conversion_queue,
            style="Ghost.TButton",
        ).pack(fill="x")
        self._refresh_session_queue_view()

        info = ttk.Frame(window)
        info.pack(fill="x", padx=18, pady=(10, 4))
        ttk.Label(info, textvariable=self._session_browser_status_var, style="Muted.TLabel").pack(side="left")

        content = ttk.Frame(window, style="Card.TFrame", padding=0)
        content.pack(fill="both", expand=True, padx=18, pady=(4, 18))
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)

        canvas = tk.Canvas(content, bg=self.palette["surface_alt"], highlightthickness=0, bd=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(content, orient="vertical", command=canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)

        cards_host = ttk.Frame(canvas, style="Panel.TFrame")
        cards_window = canvas.create_window((0, 0), window=cards_host, anchor="nw")
        self._session_browser_canvas = canvas
        self._session_browser_cards_host = cards_host
        self._session_browser_cards_window = int(cards_window)

        cards_host.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(cards_window, width=event.width))
        canvas.bind_all("<MouseWheel>", self._on_session_browser_mousewheel, add="+")
        self._refresh_session_browser_filter_options()
        self._render_session_browser_books()

    def _on_session_browser_mousewheel(self, event) -> None:
        canvas = self._session_browser_canvas
        window = self._session_browser_window
        if canvas is None or window is None or not window.winfo_exists():
            return
        try:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:
            return

    def _session_browser_normalize(self, value: object) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        text = unicodedata.normalize("NFD", text)
        text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
        text = re.sub(r"[_\\/\-.,;:()\[\]{}]+", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _session_browser_book_matches(self, book_label: str, rows: list[dict[str, object]], query: str) -> bool:
        if not query:
            return True
        haystack = self._session_browser_normalize(
            " ".join([book_label] + [str(row.get("label") or "") for row in rows])
        )
        if query in haystack:
            return True
        tokens = [token for token in query.split() if token]
        hay_tokens = [token for token in haystack.split() if token]
        return all(self._session_browser_token_matches_any(token, hay_tokens) for token in tokens)

    def _session_browser_token_matches_any(self, needle: str, hay_tokens: list[str]) -> bool:
        if not needle:
            return True
        if len(needle) <= 2:
            return any(token.startswith(needle) or needle in token for token in hay_tokens)
        threshold = 0.82 if len(needle) <= 6 else 0.74
        for token in hay_tokens:
            if needle in token:
                return True
            if len(token) >= 3 and token in needle:
                return True
            if len(needle) <= 4 and self._session_browser_is_subsequence(needle, token):
                return True
            if SequenceMatcher(None, needle, token).ratio() >= threshold:
                return True
        return False

    def _session_browser_is_subsequence(self, needle: str, haystack: str) -> bool:
        if not needle:
            return True
        pos = 0
        for ch in haystack:
            if ch == needle[pos]:
                pos += 1
                if pos >= len(needle):
                    return True
        return False

    def _session_browser_book_passes_filters(self, book_label: str, rows: list[dict[str, object]]) -> bool:
        book = self._session_book_record_map.get(book_label, {})
        state_filter = self._session_browser_normalize(self._session_browser_state_var.get())
        course_filter = self._session_browser_normalize(self._session_browser_course_var.get())
        editorial_filter = self._session_browser_normalize(self._session_browser_editorial_var.get())
        author_filter = self._session_browser_normalize(self._session_browser_author_var.get())
        word_filter = self._session_browser_normalize(self._session_browser_word_var.get())
        if state_filter != "todos" and self._session_browser_normalize(book.get("estado")) != state_filter:
            return False
        if course_filter != "todos" and self._session_browser_normalize(book.get("curso")) != course_filter:
            return False
        if editorial_filter != "todos" and self._session_browser_normalize(book.get("editorial")) != editorial_filter:
            return False
        if author_filter != "todos" and self._session_browser_normalize(book.get("autor")) != author_filter:
            return False
        if word_filter in {"listo", "pendiente"}:
            ready_count = sum(
                1
                for row in rows
                if self._session_word_exists(Path(row["path"]) if row.get("path") is not None else None)
            )
            if word_filter == "listo" and ready_count <= 0:
                return False
            if word_filter == "pendiente" and ready_count >= len(rows):
                return False
        return True

    def _refresh_session_browser_filter_options(self) -> None:
        if self._session_browser_window is None or not self._session_browser_window.winfo_exists():
            return
        self._set_session_browser_filter_values(
            getattr(self, "combo_session_browser_state", None),
            self._session_browser_state_var,
            self._collect_session_browser_distinct_values("estado"),
        )
        self._set_session_browser_filter_values(
            getattr(self, "combo_session_browser_course", None),
            self._session_browser_course_var,
            self._collect_session_browser_distinct_values("curso"),
        )
        self._set_session_browser_filter_values(
            getattr(self, "combo_session_browser_editorial", None),
            self._session_browser_editorial_var,
            self._collect_session_browser_distinct_values("editorial"),
        )
        self._set_session_browser_filter_values(
            getattr(self, "combo_session_browser_author", None),
            self._session_browser_author_var,
            self._collect_session_browser_distinct_values("autor"),
        )
        word_combo = getattr(self, "combo_session_browser_word", None)
        if word_combo is not None:
            word_combo.configure(values=["todos", "listo", "pendiente"])

    def _collect_session_browser_distinct_values(self, field_name: str) -> list[str]:
        values: dict[str, str] = {}
        for book in self._session_book_record_map.values():
            raw = str(book.get(field_name) or "").strip()
            if not raw:
                continue
            values.setdefault(self._session_browser_normalize(raw), raw)
        ordered = [values[key] for key in sorted(values.keys())]
        return ["todos", *ordered]

    def _set_session_browser_filter_values(
        self,
        combo: ttk.Combobox | None,
        var: tk.StringVar,
        values: list[str],
    ) -> None:
        if combo is None:
            return
        combo.configure(values=values)
        current = str(var.get() or "").strip()
        if current in values:
            return
        var.set("todos")

    def _render_session_browser_books(self) -> None:
        host = self._session_browser_cards_host
        if host is None or not host.winfo_exists():
            return
        for child in host.winfo_children():
            child.destroy()
        self._session_browser_thumb_refs.clear()

        query = self._session_browser_normalize(self._session_browser_search_var.get())
        books: list[tuple[str, list[dict[str, object]]]] = []
        for book_label in sorted(self._session_instances_by_book.keys(), key=str.casefold):
            rows = self._session_instances_by_book.get(book_label, [])
            if not self._session_browser_book_passes_filters(book_label, rows):
                continue
            if self._session_browser_book_matches(book_label, rows, query):
                books.append((book_label, rows))

        total_instances = sum(len(rows) for _book, rows in books)
        ready_instances = sum(
            1
            for _book, rows in books
            for row in rows
            if self._session_word_exists(Path(row["path"]) if row.get("path") is not None else None)
        )
        self._session_browser_status_var.set(
            f"Mostrando {len(books)} libro(s) | instancias={total_instances} | Word listo={ready_instances}"
        )

        if not books:
            empty = ttk.Frame(host, style="Card.TFrame", padding=24)
            empty.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
            ttk.Label(empty, text="No hay sesiones para mostrar.", style="Section.TLabel").pack(anchor="center")
            ttk.Label(empty, text="Ajusta la busqueda o refresca el catalogo.", style="Muted.TLabel").pack(anchor="center", pady=(8, 0))
            return

        columns = 3
        for col in range(columns):
            host.columnconfigure(col, weight=1)
        for idx, (book_label, rows) in enumerate(books):
            self._build_session_browser_card(host, book_label, rows, row=idx // columns, column=idx % columns)

    def _build_session_browser_card(
        self,
        parent: ttk.Frame,
        book_label: str,
        rows: list[dict[str, object]],
        *,
        row: int,
        column: int,
    ) -> None:
        outer = ttk.Frame(parent, style="Card.TFrame", padding=0)
        outer.grid(row=row, column=column, sticky="nsew", padx=10, pady=10)
        outer.columnconfigure(1, weight=1)
        ready_count = sum(
            1 for item in rows if self._session_word_exists(Path(item["path"]) if item.get("path") is not None else None)
        )
        accent_color = "#10A37F" if ready_count else self.palette["border"]
        tk.Frame(outer, bg=accent_color, width=10).grid(row=0, column=0, sticky="ns")

        card = ttk.Frame(outer, style="Card.TFrame", padding=14)
        card.grid(row=0, column=1, sticky="nsew")
        card.columnconfigure(1, weight=1)
        title = book_label.split(" | ", 1)[1] if " | " in book_label else book_label
        cover_host = ttk.Frame(card, style="Card.TFrame", width=116)
        cover_host.grid(row=0, column=0, rowspan=4, sticky="nsw", padx=(0, 14))
        cover_host.grid_propagate(False)
        self._render_session_browser_cover(cover_host, book_label, title)

        info = ttk.Frame(card, style="Card.TFrame")
        info.grid(row=0, column=1, sticky="ew")
        ttk.Label(info, text=title, style="Section.TLabel", wraplength=300).pack(anchor="w")
        ttk.Label(info, text=book_label, style="Muted.TLabel", wraplength=330).pack(anchor="w", pady=(4, 0))
        ttk.Label(
            info,
            text=f"Instancias: {len(rows)} | Word listo: {ready_count}",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(8, 0))
        ttk.Label(
            info,
            text="Instancias: verde=Word exportado, blanco=pendiente, azul=seleccionada, amarillo=en cola, gris=sesion no encontrada.",
            style="Muted.TLabel",
            wraplength=330,
        ).pack(anchor="w", pady=(6, 0))

        badges = tk.Frame(card, bg=self.palette["surface"])
        badges.grid(row=1, column=1, sticky="ew", pady=(8, 0))
        sorted_rows = sorted(rows, key=lambda item: self._session_instance_sort_key(str(item.get("label") or "")))
        for idx, item in enumerate(sorted_rows):
            instance_label = str(item.get("label") or "").strip()
            session_path = Path(item["path"]) if item.get("path") is not None else None
            key = (book_label, instance_label)
            has_word = self._session_word_exists(session_path)
            selected = key == self._session_browser_active_key
            queued = key in self._session_conversion_queue
            missing_session = session_path is None or not session_path.exists()
            bg = (
                "#DBEAFE"
                if selected
                else ("#FEF3C7" if queued else ("#E5E7EB" if missing_session else ("#10A37F" if has_word else "#FFFFFF")))
            )
            fg = (
                "#1D4ED8"
                if selected
                else ("#92400E" if queued else ("#6B7280" if missing_session else ("#FFFFFF" if has_word else self.palette["text"])))
            )
            badge = tk.Label(
                badges,
                text=instance_label,
                bg=bg,
                fg=fg,
                padx=7,
                pady=3,
                relief="solid",
                bd=1,
                font=("Segoe UI", 8, "bold"),
            )
            badge.grid(row=idx, column=0, sticky="w", padx=(0, 4), pady=(0, 4))
            badge.configure(cursor="hand2")
            payload = {
                "book_label": book_label,
                "instance_label": instance_label,
                "session_path": session_path,
                "word_path": self._session_word_path_for(session_path),
            }
            badge.bind(
                "<Button-1>",
                lambda _e, b=book_label, i=instance_label, p=session_path: self._select_session_instance_from_catalog(
                    book_label=b,
                    instance_label=i,
                    session_path=p,
                ),
                add="+",
            )
            badge.bind(
                "<Double-Button-1>",
                lambda _e, data=payload: self._open_session_word_payload(data),
                add="+",
            )

    def _render_session_browser_cover(self, parent: ttk.Frame, book_label: str, title: str) -> None:
        book = self._session_book_record_map.get(book_label, {})
        cover_path = str(book.get("cover_path") or "").strip()
        clean = str(remap_legacy_drive_path(cover_path, prefer_existing=True)) if cover_path else ""
        if clean and Image is not None and ImageTk is not None:
            try:
                preview = Image.open(clean)
                preview.thumbnail((104, 148))
                tk_img = ImageTk.PhotoImage(preview)
                self._session_browser_thumb_refs.append(tk_img)
                ttk.Label(parent, image=tk_img).pack(anchor="center", pady=(2, 0))
                return
            except Exception:
                pass
        placeholder = tk.Frame(parent, bg="#F8FAFC", width=104, height=148, highlightthickness=1, highlightbackground="#CBD5E1")
        placeholder.pack(anchor="center", pady=(2, 0))
        placeholder.pack_propagate(False)
        short = title[:28] + ("..." if len(title) > 28 else "")
        tk.Label(
            placeholder,
            text=short or "Sin imagen",
            bg="#F8FAFC",
            fg=self.palette["muted"],
            wraplength=88,
            justify="center",
            font=("Segoe UI", 8, "bold"),
        ).pack(expand=True, fill="both", padx=4, pady=4)

    def _session_instance_sort_key(self, value: str) -> tuple[int, str]:
        text = str(value or "").strip().lower()
        match = re.search(r"\bs(?:emana)?[_\s-]*n?[_\s-]*(\d+)|\bs(\d+)", text)
        if match:
            number = match.group(1) or match.group(2)
            return (int(number), text)
        return (10**9, text)

    def _refresh_session_catalog(self) -> None:
        db_name = (self.db_name_var.get() or "").strip()
        if db_name:
            try:
                if self._refresh_session_catalog_from_library(db_name):
                    return
            except Exception as exc:
                self._log(f"Sesion IA: no se pudo cargar catalogo desde biblioteca ({db_name}). Se intentara por carpetas. Detalle: {exc}")

        root = remap_legacy_drive_path(Path((self.session_root_var.get() or "").strip()), prefer_existing=True)
        if not str(root):
            self._session_catalog_status_var.set("Selecciona una raiz valida")
            return
        if not root.exists() or not root.is_dir():
            self._session_catalog_status_var.set(f"Raiz invalida: {root}")
            return

        self._session_book_map = {}
        self._session_instances_by_book = {}
        self._session_instance_path_map = {}
        self._session_book_record_map = {}
        session_dirs = sorted(path for path in root.rglob("sessions") if path.is_dir())
        total_sessions = 0

        for sessions_dir in session_dirs:
            workspace_dir = sessions_dir.parent
            try:
                book_label = str(workspace_dir.relative_to(root))
            except Exception:
                book_label = workspace_dir.name
            book_label = book_label.replace("/", os.sep)
            book_label = book_label.replace("\\", os.sep)
            self._session_book_map[book_label] = workspace_dir
            instance_rows = self._session_instances_by_book.setdefault(book_label, [])
            for session_path in sorted(sessions_dir.glob("*.json")):
                total_sessions += 1
                label = self._build_session_instance_label(session_path)
                row = {"label": label, "path": session_path}
                instance_rows.append(row)
                self._session_instance_path_map[(book_label, label)] = session_path

        book_labels = sorted(self._session_instances_by_book.keys(), key=str.casefold)
        self.combo_session_book["values"] = book_labels
        if not book_labels:
            self.session_book_var.set("")
            self.combo_session_instance["values"] = []
            self.session_instance_var.set("")
            self._session_catalog_status_var.set(f"No se encontraron sesiones en {root}")
            self._refresh_session_tree()
            return

        current_book = (self.session_book_var.get() or "").strip()
        if current_book not in self._session_instances_by_book:
            current_book = book_labels[0]
        self.session_book_var.set(current_book)
        self._on_session_book_change(preserve_current=True)
        self._session_catalog_status_var.set(
            f"Libros detectados: {len(book_labels)} | sesiones: {total_sessions} | raiz: {root}"
        )
        selected = self._selected_session_path()
        if selected is not None:
            self._sync_session_browser_to_path(selected)
        self._refresh_session_browser_filter_options()
        self._refresh_session_tree()
        self._render_session_browser_books()

    def _refresh_session_catalog_from_library(self, db_name: str) -> bool:
        books = self.book_progress_controller.listar_libros(db_name)
        self._session_book_map = {}
        self._session_instances_by_book = {}
        self._session_instance_path_map = {}
        self._session_book_record_map = {}
        total_instances = 0
        total_resolved_sessions = 0

        for book in books:
            book_id = int(book.get("id") or 0)
            if book_id <= 0:
                continue
            book_label = self._build_session_book_library_label(book)
            workspace_raw = str(book.get("workspace_dir") or "").strip()
            workspace = remap_legacy_drive_path(Path(workspace_raw), prefer_existing=True) if workspace_raw else Path("")
            if str(workspace):
                self._session_book_map[book_label] = workspace
            self._session_book_record_map[book_label] = dict(book)
            instance_rows = self._session_instances_by_book.setdefault(book_label, [])
            for instance in self.book_progress_controller.listar_instancias_libro(db_name, book_id):
                total_instances += 1
                session_path = self._resolve_library_instance_session_path(book, instance)
                label = self._build_session_instance_library_label(instance)
                row: dict[str, object] = {"label": label, "path": session_path, "instance": dict(instance)}
                instance_rows.append(row)
                if session_path is not None:
                    self._session_instance_path_map[(book_label, label)] = session_path
                    total_resolved_sessions += 1

        book_labels = [label for label, rows in self._session_instances_by_book.items() if rows]
        book_labels.sort(key=str.casefold)
        self.combo_session_book["values"] = book_labels
        if not book_labels:
            return False

        current_book = (self.session_book_var.get() or "").strip()
        if current_book not in self._session_instances_by_book:
            current_book = book_labels[0]
        self.session_book_var.set(current_book)
        self._on_session_book_change(preserve_current=True)
        self._session_catalog_status_var.set(
            f"Catalogo BD: libros={len(book_labels)} | instancias={total_instances} | sesiones resolubles={total_resolved_sessions} | db={db_name}"
        )
        selected = self._selected_session_path()
        if selected is not None:
            self._sync_session_browser_to_path(selected)
        self._refresh_session_browser_filter_options()
        self._refresh_session_tree()
        self._render_session_browser_books()
        return True

    def _build_session_book_library_label(self, book: dict[str, object]) -> str:
        code = str(book.get("codigo") or "").strip()
        title = str(book.get("titulo") or "").strip()
        if code and title:
            return f"{code} | {title}"
        return title or code or f"Libro {int(book.get('id') or 0)}"

    def _build_session_instance_library_label(self, instance: dict[str, object]) -> str:
        tipo = str(instance.get("tipo") or "").strip()
        total = int(instance.get("total_esperado") or 0)
        if total > 0:
            return f"{tipo} | esperados={total}"
        return tipo or f"instancia-{int(instance.get('id') or 0)}"

    def _resolve_library_instance_session_path(self, book: dict[str, object], instance: dict[str, object]) -> Path | None:
        raw_session = str(instance.get("session_path") or "").strip()
        session_lower = raw_session.lower()
        looks_cache_path = "\\.cache\\transcriptor_runs\\sessions" in session_lower
        workspace_raw = str(book.get("workspace_dir") or "").strip()
        tipo = normalize_instance_name(str(instance.get("tipo") or "").strip(), "sesion")
        inferred: Path | None = None
        if workspace_raw and tipo:
            try:
                inferred = project_dirs(Path(workspace_raw), tipo).get("session_path")
            except Exception:
                inferred = None
        if raw_session and not looks_cache_path:
            try:
                normalized = remap_legacy_drive_path(Path(raw_session), prefer_existing=True)
                if normalized.exists():
                    return normalized
                if inferred is not None:
                    normalized_inferred = remap_legacy_drive_path(inferred, prefer_existing=True)
                    if normalized_inferred.exists():
                        return normalized_inferred
                return None
            except Exception:
                pass
        if inferred is not None:
            try:
                normalized_inferred = remap_legacy_drive_path(inferred, prefer_existing=True)
                if normalized_inferred.exists():
                    return normalized_inferred
                return None
            except Exception:
                return inferred if inferred.exists() else None
        return None

    def _build_session_instance_label(self, session_path: Path) -> str:
        label = session_path.stem
        try:
            payload = self._load_session_payload(session_path)
        except Exception:
            payload = {}
        ui = payload.get("ui", {}) if isinstance(payload, dict) else {}
        project_name = str(ui.get("project_name", "") or "").strip()
        instance_type = str(
            ui.get("instance_type", payload.get("instance_type", "") if isinstance(payload, dict) else "") or ""
        ).strip()
        parts = [label]
        if project_name and project_name.casefold() != label.casefold():
            parts.append(project_name)
        if instance_type:
            parts.append(instance_type)
        return " | ".join(parts)

    def _on_session_book_change(self, *, preserve_current: bool = False) -> None:
        book_label = (self.session_book_var.get() or "").strip()
        rows = self._session_instances_by_book.get(book_label, [])
        labels = [str(row.get("label") or "").strip() for row in rows if str(row.get("label") or "").strip()]
        self.combo_session_instance["values"] = labels
        current_instance = (self.session_instance_var.get() or "").strip() if preserve_current else ""
        if current_instance not in labels:
            current_instance = labels[0] if labels else ""
        self.session_instance_var.set(current_instance)
        if current_instance:
            self._on_session_instance_change()

    def _on_session_instance_change(self) -> None:
        book_label = (self.session_book_var.get() or "").strip()
        instance_label = (self.session_instance_var.get() or "").strip()
        if not book_label or not instance_label:
            return
        session_path = self._session_instance_path_map.get((book_label, instance_label))
        if session_path is None:
            self._log(f"Sesion IA: la instancia seleccionada no tiene session_path resoluble todavia -> {book_label} / {instance_label}")
            return
        self._set_session_selection(session_path, sync_browser=False)
        self._session_browser_active_key = (book_label, instance_label)
        self._render_session_browser_books()
        tree = getattr(self, "session_tree", None)
        if tree is not None:
            for iid, payload in self._session_tree_items.items():
                if payload.get("book_label") == book_label and payload.get("instance_label") == instance_label:
                    self._syncing_session_tree_selection = True
                    try:
                        tree.selection_set(iid)
                        tree.focus(iid)
                        tree.see(iid)
                    finally:
                        self._syncing_session_tree_selection = False
                    break

    def _sync_session_browser_to_path(self, session_path: Path) -> None:
        normalized = remap_legacy_drive_path(session_path, prefer_existing=True)
        for (book_label, instance_label), candidate in self._session_instance_path_map.items():
            try:
                if remap_legacy_drive_path(candidate, prefer_existing=True) == normalized:
                    self.session_book_var.set(book_label)
                    self._on_session_book_change(preserve_current=False)
                    self.session_instance_var.set(instance_label)
                    tree = getattr(self, "session_tree", None)
                    if tree is not None:
                        for iid, payload in self._session_tree_items.items():
                            if payload.get("book_label") == book_label and payload.get("instance_label") == instance_label:
                                self._syncing_session_tree_selection = True
                                try:
                                    tree.selection_set(iid)
                                    tree.focus(iid)
                                    tree.see(iid)
                                finally:
                                    self._syncing_session_tree_selection = False
                                break
                    return
            except Exception:
                continue

    def _apply_session_metadata(self, session_path: Path) -> None:
        try:
            if not session_path.exists():
                self._log(f"Sesion omitida: no existe el archivo -> {session_path}")
                return
            payload = self._load_session_payload(session_path)
            ui = payload.get("ui", {}) if isinstance(payload, dict) else {}
            project_name = str(ui.get("project_name", "") or "").strip()
            curso = str(ui.get("curso", "") or "").strip()
            tema = str(ui.get("tema", "") or "").strip()
            subtema = str(ui.get("subtema", "") or "").strip()
            instance_type = str(
                ui.get("instance_type", payload.get("instance_type", "") if isinstance(payload, dict) else "") or ""
            ).strip().lower()
            if project_name and self.titulo_var.get().strip() == "Practica":
                self.titulo_var.set(project_name)
            if curso:
                self.curso_var.set(curso)
            if tema:
                self.tema_var.set(tema)
            if subtema:
                self.subtema_var.set(subtema)
            images_dir = self._infer_images_dir_from_session(session_path, payload=payload, instance_type=instance_type)
            if images_dir:
                self.images_dir_var.set(str(images_dir))
                self._log(f"Sesion: carpeta de imagenes ajustada a marcadores -> {images_dir}")
        except Exception as exc:
            self._log(f"Aviso leyendo sesion: {exc}")

    def _init_drag_and_drop(self) -> None:
        if not DND_AVAILABLE:
            msg = "Drag & Drop no disponible para sesiones."
            if DND_IMPORT_ERROR:
                msg = f"{msg} Detalle: {DND_IMPORT_ERROR}"
            self._log(msg)
            return

        def register() -> None:
            registered = False
            for widget in (self, getattr(self, "entry_session_path", None), self.session_frame):
                if widget is None or not hasattr(widget, "drop_target_register"):
                    continue
                try:
                    widget.drop_target_register(DND_FILES)
                    widget.dnd_bind("<<Drop>>", self._on_drop_session_file)
                    registered = True
                except Exception as exc:
                    self._log(f"Drag & Drop sesiones: error registrando: {exc}")
            if registered:
                self._log("Arrastra un archivo .json de sesion al campo de sesion para cargarlo.")
            else:
                self._log("Drag & Drop sesiones no se pudo activar.")

        try:
            self.after(200, register)
        except Exception:
            register()

    def _on_drop_session_file(self, event) -> None:
        raw_paths = self._parse_drop_files(getattr(event, "data", "") or "")
        if not raw_paths:
            self._log("Drop de sesion recibido, pero sin rutas detectadas.")
            return
        selected: Path | None = None
        for raw in raw_paths:
            path = Path(raw).expanduser()
            if path.is_file() and path.suffix.lower() == ".json":
                selected = path
                break
        if selected is None:
            self._log("Drop ignorado: no se detecto ningun archivo .json de sesion.")
            return
        if len(raw_paths) > 1:
            self._log(f"Drop de sesiones: se tomo solo el primer .json valido -> {selected.name}")
        self._set_session_selection(selected)

    def _parse_drop_files(self, data: str) -> list[str]:
        if not data:
            return []
        try:
            parts = self.tk.splitlist(data)
        except Exception:
            parts = data.split()
        return [str(p).strip() for p in parts if str(p).strip()]

    def _pick_output_docx(self) -> None:
        initial_name = self._default_output_name()
        selected = filedialog.asksaveasfilename(
            title="Guardar como .docx",
            defaultextension=".docx",
            initialfile=initial_name,
            filetypes=[("Word", "*.docx")],
        )
        if selected:
            self.output_docx_var.set(selected)

    def _pick_template(self) -> None:
        selected = filedialog.askopenfilename(title="Selecciona plantilla .docx", filetypes=[("Word", "*.docx")])
        if selected:
            self.template_var.set(selected)

    def _pick_images_dir(self) -> None:
        selected = filedialog.askdirectory(title="Selecciona carpeta de imagenes")
        if selected:
            self.images_dir_var.set(selected)

    def _combo_values(self, combo: ttk.Combobox | None) -> list[str]:
        if combo is None:
            return []
        try:
            return [str(v) for v in combo.cget("values")]
        except Exception:
            try:
                return [str(v) for v in combo["values"]]
            except Exception:
                return []

    def _open_db_source_window(self) -> None:
        self.source_mode_var.set("db")
        self.status_var.set("Fuente activa: seleccionador de problemas")
        try:
            self._on_db_change()
        except Exception as exc:
            self._log(f"Aviso preparando seleccionador de problemas: {exc}")
        if self._db_source_window is not None and self._db_source_window.winfo_exists():
            self._db_source_window.lift()
            self._refresh_db_source_window_values()
            return

        win = tk.Toplevel(self)
        win.title("Seleccionador de problemas -> Word")
        win.geometry("1120x620")
        win.minsize(980, 560)
        self._db_source_window = win

        def on_close() -> None:
            self._sync_db_source_structure_to_main()
            self._db_source_window = None
            self._db_source_structure_text = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)

        root = ttk.Frame(win, padding=16)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="Seleccionador de problemas", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            root,
            text="Filtra problemas, abre el visualizador para acumularlos y convierte la seleccion a Word.",
            style="SubHeader.TLabel",
        ).pack(anchor="w", pady=(4, 12))

        filters = ttk.Frame(root, style="Card.TFrame", padding=14)
        filters.pack(fill="x")
        filters.columnconfigure(1, weight=1)
        filters.columnconfigure(3, weight=1)

        ttk.Label(filters, text="Base de datos").grid(row=0, column=0, sticky="w", pady=6)
        self.combo_db_source = ttk.Combobox(filters, state="readonly", textvariable=self.db_name_var, values=self._combo_values(self.combo_db))
        self.combo_db_source.grid(row=0, column=1, sticky="ew", padx=(8, 16), pady=6)
        self.combo_db_source.bind("<<ComboboxSelected>>", lambda _e: self._on_db_source_db_change())
        ttk.Button(filters, text="Refrescar", command=self._on_db_source_refresh, style="Ghost.TButton").grid(
            row=0, column=2, sticky="ew", padx=(0, 8), pady=6
        )
        ttk.Label(filters, textvariable=self._db_source_count_var).grid(row=0, column=3, sticky="e", pady=6)

        ttk.Label(filters, text="Curso").grid(row=1, column=0, sticky="w", pady=6)
        self.combo_curso_source = ttk.Combobox(filters, textvariable=self.curso_var, values=self._combo_values(self.combo_curso))
        self.combo_curso_source.grid(row=1, column=1, sticky="ew", padx=(8, 16), pady=6)
        self.combo_curso_source.bind("<<ComboboxSelected>>", lambda _e: self._on_db_source_curso_change())
        ttk.Label(filters, text="Tema").grid(row=1, column=2, sticky="w", pady=6)
        self.combo_tema_source = ttk.Combobox(filters, textvariable=self.tema_var, values=self._combo_values(self.combo_tema))
        self.combo_tema_source.grid(row=1, column=3, sticky="ew", pady=6)
        self.combo_tema_source.bind("<<ComboboxSelected>>", lambda _e: self._on_db_source_tema_change())

        ttk.Label(filters, text="Subtema").grid(row=2, column=0, sticky="w", pady=6)
        self.combo_subtema_source = ttk.Combobox(filters, textvariable=self.subtema_var, values=self._combo_values(self.combo_subtema))
        self.combo_subtema_source.grid(row=2, column=1, sticky="ew", padx=(8, 16), pady=6)
        self.combo_subtema_source.bind("<<ComboboxSelected>>", lambda _e: self._on_db_source_subtema_change())
        ttk.Label(filters, text="Estado").grid(row=2, column=2, sticky="w", pady=6)
        ttk.Combobox(
            filters,
            textvariable=self.estado_var,
            state="readonly",
            values=["Todos", "Sin revisar", "Consistente", "Inconsistente"],
        ).grid(row=2, column=3, sticky="ew", pady=6)

        ttk.Label(filters, text="Autor").grid(row=3, column=0, sticky="w", pady=6)
        self.combo_autor_source = ttk.Combobox(filters, textvariable=self.autor_var, values=self._combo_values(self.combo_autor))
        self.combo_autor_source.grid(row=3, column=1, sticky="ew", padx=(8, 16), pady=6)
        self.combo_autor_source.bind("<<ComboboxSelected>>", lambda _e: self._on_db_source_autor_change())
        ttk.Label(filters, text="Editorial").grid(row=3, column=2, sticky="w", pady=6)
        self.combo_editorial_source = ttk.Combobox(filters, textvariable=self.editorial_var, values=self._combo_values(self.combo_editorial))
        self.combo_editorial_source.grid(row=3, column=3, sticky="ew", pady=6)
        self.combo_editorial_source.bind("<<ComboboxSelected>>", lambda _e: self._on_db_source_editorial_change())

        ttk.Checkbutton(filters, text="Seleccion aleatoria (desmarcar = orden por numero)", variable=self.aleatorio_var).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=6
        )
        ttk.Label(filters, text="Clave").grid(row=4, column=2, sticky="w", pady=6)
        ttk.Combobox(
            filters,
            textvariable=self.clave_var,
            state="readonly",
            values=["Todos", "Con clave", "Sin clave", "Abiertos"],
        ).grid(row=4, column=3, sticky="ew", pady=6)

        ttk.Label(filters, text="Titulo del lote").grid(row=5, column=0, sticky="w", pady=6)
        ttk.Entry(filters, textvariable=self.titulo_var).grid(row=5, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=6)

        structure = ttk.LabelFrame(root, text="Titulos y subtitulos", style="Card.TLabelframe")
        structure.pack(fill="both", expand=True, pady=(12, 0))
        self._db_source_structure_text = tk.Text(
            structure,
            height=6,
            bg=self.palette["surface"],
            fg=self.palette["text"],
            insertbackground=self.palette["text"],
            relief="solid",
            bd=1,
            wrap="word",
            highlightthickness=1,
            highlightbackground=self.palette["border"],
        )
        self._db_source_structure_text.pack(fill="both", expand=True, padx=8, pady=8)
        if self.txt_practice_structure is not None:
            try:
                self._db_source_structure_text.insert("1.0", self.txt_practice_structure.get("1.0", "end").strip())
            except Exception:
                pass
        ttk.Label(
            structure,
            text="Formato: # Factorizacion | ## Factor comun: 1-8 | ## Agrupacion: 9-15",
            style="Muted.TLabel",
        ).pack(anchor="w", padx=8, pady=(0, 8))

        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=(12, 0))
        ttk.Checkbutton(actions, text="Agregar hoja final de clave", variable=self.incluir_clave_final_var).pack(side="left")
        ttk.Button(
            actions,
            text="Cargar JSON de seleccion",
            command=self._load_db_selection_json_dialog,
            style="Ghost.TButton",
        ).pack(side="left", padx=(12, 0))
        ttk.Button(actions, text="Abrir visualizador", command=self._open_db_preview_from_source_window, style="Ghost.TButton").pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(
            actions,
            text="Editar JSON (Modulo 6)",
            command=self._open_practice_editor_from_db_source_window,
            style="Secondary.TButton",
        ).pack(side="right", padx=(8, 0))
        ttk.Button(actions, text="Convertir", command=self._convert_db_from_source_window, style="Accent.TButton").pack(side="right")
        ttk.Label(actions, textvariable=self._db_preview_count_var).pack(side="right", padx=(0, 16))
        self._refresh_db_source_window_values()

    def _open_session_source_window(self) -> None:
        self.source_mode_var.set("session")
        self.status_var.set("Fuente activa: sesion Transcriptor IA")
        if not self._session_book_map:
            try:
                self._refresh_session_catalog()
            except Exception as exc:
                self._log(f"Aviso cargando catalogo de sesiones: {exc}")
                messagebox.showerror("Modulo 7", f"No se pudo cargar la biblioteca de sesiones.\n{exc}")
                return
        self._open_session_browser_window()

    def _sync_db_source_structure_to_main(self) -> None:
        if self._db_source_structure_text is None or self.txt_practice_structure is None:
            return
        try:
            value = self._db_source_structure_text.get("1.0", "end").strip()
            self.txt_practice_structure.delete("1.0", "end")
            self.txt_practice_structure.insert("1.0", value)
        except Exception:
            return

    def _refresh_db_source_window_values(self) -> None:
        mapping = (
            ("combo_db_source", self.combo_db),
            ("combo_curso_source", self.combo_curso),
            ("combo_tema_source", self.combo_tema),
            ("combo_subtema_source", self.combo_subtema),
            ("combo_autor_source", self.combo_autor),
            ("combo_editorial_source", self.combo_editorial),
        )
        for attr, source_combo in mapping:
            combo = getattr(self, attr, None)
            if combo is None:
                continue
            try:
                combo.configure(values=self._combo_values(source_combo))
            except Exception:
                pass

    def _on_db_source_refresh(self) -> None:
        self._listar_dbs()
        self._refresh_db_source_window_values()

    def _on_db_source_db_change(self) -> None:
        self._on_db_change()
        self._refresh_db_source_window_values()

    def _on_db_source_curso_change(self) -> None:
        self._on_curso_change()
        self._refresh_db_source_window_values()

    def _on_db_source_tema_change(self) -> None:
        self._on_tema_change()
        self._refresh_db_source_window_values()

    def _on_db_source_subtema_change(self) -> None:
        self._on_subtema_change()
        self._refresh_db_source_window_values()

    def _on_db_source_autor_change(self) -> None:
        self._on_autor_change()
        self._refresh_db_source_window_values()

    def _on_db_source_editorial_change(self) -> None:
        self._on_editorial_change()
        self._refresh_db_source_window_values()

    def _open_db_preview_from_source_window(self) -> None:
        self._sync_db_source_structure_to_main()
        self.source_mode_var.set("db")
        self._open_db_preview_window()

    def _load_db_selection_json_dialog(self) -> None:
        raw = filedialog.askopenfilename(
            title="Cargar JSON de seleccion de problemas",
            filetypes=[("JSON de seleccion", "*.json"), ("Todos los archivos", "*.*")],
        )
        if not raw:
            return
        self._load_db_selection_json(Path(raw))

    def _load_db_selection_json(self, json_path: Path) -> None:
        if not json_path.exists():
            messagebox.showerror("Modulo 7", f"No existe el JSON seleccionado:\n{json_path}")
            return
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as exc:
            messagebox.showerror("Modulo 7", f"No se pudo leer el JSON.\n{exc}")
            return
        if not isinstance(payload, dict):
            messagebox.showerror("Modulo 7", "El JSON debe contener un objeto.")
            return
        payload_db = str(payload.get("database") or "").strip()
        if payload_db:
            self.db_name_var.set(payload_db)
            try:
                self._on_db_change()
            except Exception as exc:
                self._log(f"Aviso cargando base del JSON ({payload_db}): {exc}")
        db_name = (self.db_name_var.get() or payload_db or "").strip()
        if not db_name:
            messagebox.showwarning("Modulo 7", "El JSON no indica base de datos y no hay una base seleccionada.")
            return
        items = self._load_saved_db_selection_items(json_path, db_name=db_name)
        if not items:
            messagebox.showwarning("Modulo 7", "El JSON no contiene una seleccion reconstruible.")
            return
        self._db_selection_json_path = json_path
        self._db_selected_problem_ids.clear()
        self._db_selected_problem_order.clear()
        self._db_selected_problem_map.clear()
        for item in sorted(items, key=lambda row: int(row.get("order") or len(self._db_selected_problem_order) + 1)):
            pid = int(item.get("id") or 0)
            if pid <= 0:
                continue
            self._db_selected_problem_ids.add(pid)
            self._db_selected_problem_order.append(pid)
            self._db_selected_problem_map[pid] = dict(item)
        self.source_mode_var.set("db")
        self._refresh_db_preview_tree_marks()
        self._update_db_preview_counter()
        self.status_var.set(f"JSON de seleccion cargado: {json_path.name}")
        self._log(f"JSON de seleccion cargado: {json_path} | problemas={len(self._db_selected_problem_order)}")
        messagebox.showinfo(
            "Modulo 7",
            "Seleccion cargada correctamente.\n\n"
            "Ahora puedes usar 'Editar JSON (Modulo 6)' para corregirla o 'Convertir' para generar Word.",
        )

    def _open_practice_editor_from_db_source_window(self) -> None:
        self._sync_db_source_structure_to_main()
        self.source_mode_var.set("db")
        json_path = self._db_selection_json_path or self._db_selection_output_path(output_docx=None)
        if not self._selection_json_has_editable_items(json_path):
            try:
                json_path = self._save_db_selected_problem_ids()
            except Exception as exc:
                messagebox.showwarning(
                    "Modulo 7",
                    "Primero acumula problemas en el visualizador para crear el JSON de trabajo.\n\n"
                    f"{exc}",
                )
                return
        self._db_selection_json_path = json_path
        try:
            if self._db_practice_editor_window is not None and self._db_practice_editor_window.winfo_exists():
                self._db_practice_editor_window.lift()
                if hasattr(self._db_practice_editor_window, "_load_json_path"):
                    self._db_practice_editor_window._load_json_path(json_path)  # type: ignore[attr-defined]
                return
        except Exception:
            self._db_practice_editor_window = None
        try:
            from modulos.modulo6_practicas.gui_practicas import PracticeBuilderWindow

            win = PracticeBuilderWindow(
                self,
                initial_json_path=json_path,
                overwrite_source_json_on_update=True,
            )
            self._db_practice_editor_window = win
            self._log(f"Modulo 6 abierto para corregir JSON de seleccion: {json_path}")
            self.status_var.set("Editor de practica abierto. Corrige y usa 'Actualizar TEX' en Modulo 6.")
        except Exception as exc:
            messagebox.showerror("Modulo 7", f"No se pudo abrir el Modulo 6 con el JSON seleccionado.\n{exc}")

    def _convert_db_from_source_window(self) -> None:
        self._sync_db_source_structure_to_main()
        self.source_mode_var.set("db")
        self._convert_async()

    def _on_source_mode_change(self) -> None:
        mode = self.source_mode_var.get()
        for frame in (getattr(self, "tex_frame", None), getattr(self, "db_frame", None), getattr(self, "session_frame", None)):
            try:
                if frame is not None:
                    frame.grid_remove()
            except Exception:
                pass
        if mode == "db":
            self.status_var.set("Fuente activa: seleccionador de problemas")
            self._on_db_change()
        elif mode == "session":
            self.status_var.set("Fuente activa: sesion Transcriptor IA")
            if not self._session_book_map:
                try:
                    self._refresh_session_catalog()
                except Exception as exc:
                    self._log(f"Aviso cargando catalogo de sesiones: {exc}")
        else:
            self.source_mode_var.set("db")
            self.status_var.set("Fuente activa: seleccionador de problemas")
            self._on_db_change()

    def _listar_dbs(self) -> None:
        try:
            dbs = self.practice_controller.listar_bases_datos()
        except Exception as exc:
            self._log(f"Error listando bases de datos: {exc}")
            return
        self.combo_db["values"] = dbs
        if dbs:
            preferred = str(os.getenv("DB_NAME", "") or "").strip()
            if preferred and preferred in dbs:
                self.db_name_var.set(preferred)
            elif self.db_name_var.get() not in dbs:
                self.db_name_var.set(dbs[0])
        else:
            self.db_name_var.set("")
        if self.source_mode_var.get() == "db":
            self._on_db_change()

    def _on_db_change(self) -> None:
        db = (self.db_name_var.get() or "").strip()
        self._clear_db_preview(silent=True, clear_selection=True)
        if not db:
            self.combo_curso["values"] = ["Todos"]
            self.combo_tema["values"] = ["Todos"]
            self.combo_subtema["values"] = ["Todos"]
            self.combo_autor["values"] = ["Todos"]
            self.combo_editorial["values"] = ["Todos"]
            self._db_source_count_var.set("Problemas disponibles: 0")
            return
        try:
            schema = self.practice_controller.describir_fuente(db)
            cursos = self.practice_controller.listar_cursos(db)
            self.combo_curso["values"] = ["Todos"] + cursos
            current_course = self.practice_controller.normalizar_curso(self.curso_var.get())
            if current_course and current_course in self.combo_curso["values"]:
                self.curso_var.set(current_course)
            elif self.curso_var.get() not in self.combo_curso["values"]:
                self.curso_var.set("Todos")
            self._cargar_temas()
            self._cargar_autores()
            self._cargar_editoriales()
            self._refresh_db_count()
            self._log_db_schema_notice(db, schema)
        except Exception as exc:
            self._apply_db_schema_fallback(db, exc)

    def _on_curso_change(self) -> None:
        self._clear_db_preview(silent=True)
        self._cargar_temas()
        self._cargar_autores()
        self._cargar_editoriales()
        self._refresh_db_count()

    def _on_tema_change(self) -> None:
        self._clear_db_preview(silent=True)
        self._cargar_subtemas()
        self._cargar_autores()
        self._cargar_editoriales()
        self._refresh_db_count()

    def _on_subtema_change(self) -> None:
        self._clear_db_preview(silent=True)
        self._cargar_autores()
        self._cargar_editoriales()
        self._refresh_db_count()

    def _on_autor_change(self) -> None:
        self._clear_db_preview(silent=True)
        self._cargar_editoriales()
        self._refresh_db_count()

    def _on_editorial_change(self) -> None:
        self._clear_db_preview(silent=True)
        self._cargar_autores()
        self._refresh_db_count()

    def _on_db_preview_config_change(self, *, refresh_count: bool) -> None:
        if self.source_mode_var.get() != "db":
            return
        self._clear_db_preview(silent=True)
        if refresh_count:
            self._refresh_db_count()

    def _cargar_temas(self) -> None:
        db = (self.db_name_var.get() or "").strip()
        if not db:
            return
        curso = (self.curso_var.get() or "").strip()
        if curso == "Todos":
            curso = ""
        temas = self.practice_controller.listar_temas(db, curso=curso)
        self._tema_label_to_id = {}
        values = ["Todos"]
        for tema in temas:
            nombre = str(tema.get("nombre") or "").strip()
            area = str(tema.get("curso") or "").strip()
            if not nombre:
                continue
            label = f"{area} / {nombre}" if area else nombre
            values.append(label)
            self._tema_label_to_id[label] = tema.get("id")
        self.combo_tema["values"] = values
        current_topic = (self.tema_var.get() or "").strip()
        normalized_current = self.practice_controller.clave_texto_normalizada(current_topic)
        replacement = next(
            (value for value in values if self.practice_controller.clave_texto_normalizada(value) == normalized_current),
            "",
        )
        if replacement:
            self.tema_var.set(replacement)
        elif self.tema_var.get() not in values:
            self.tema_var.set("Todos")
        self._cargar_subtemas()

    def _cargar_subtemas(self) -> None:
        db = (self.db_name_var.get() or "").strip()
        if not db:
            return
        tema_id = self._tema_label_to_id.get((self.tema_var.get() or "").strip())
        subtemas = self.practice_controller.listar_subtemas(db, tema_id=tema_id)
        self._subtema_label_to_id = {}
        values = ["Todos"]
        for subtema in subtemas:
            nombre = str(subtema.get("nombre") or "").strip()
            if not nombre:
                continue
            values.append(nombre)
            self._subtema_label_to_id[nombre] = subtema.get("id")
        self.combo_subtema["values"] = values
        current_subtopic = (self.subtema_var.get() or "").strip()
        normalized_current = self.practice_controller.clave_texto_normalizada(current_subtopic)
        replacement = next(
            (value for value in values if self.practice_controller.clave_texto_normalizada(value) == normalized_current),
            "",
        )
        if replacement:
            self.subtema_var.set(replacement)
        elif self.subtema_var.get() not in values:
            self.subtema_var.set("Todos")

    def _cargar_autores(self) -> None:
        db = (self.db_name_var.get() or "").strip()
        if not db:
            return
        filters = self._current_db_filters(include_author=False)
        autores = self.practice_controller.listar_autores(
            db,
            curso=str(filters.get("curso") or ""),
            tema_id=filters.get("tema_id"),
            subtema_id=filters.get("subtema_id"),
            editorial=str(filters.get("editorial") or ""),
        )
        values = ["Todos"] + autores
        self.combo_autor["values"] = values
        if self.autor_var.get() not in values:
            self.autor_var.set("Todos")
        self.combo_autor.configure(state="readonly" if autores else "disabled")
        if not autores:
            self.autor_var.set("Todos")

    def _cargar_editoriales(self) -> None:
        db = (self.db_name_var.get() or "").strip()
        if not db:
            return
        filters = self._current_db_filters(include_editorial=False)
        editoriales = self.practice_controller.listar_editoriales(
            db,
            curso=str(filters.get("curso") or ""),
            tema_id=filters.get("tema_id"),
            subtema_id=filters.get("subtema_id"),
            autor=str(filters.get("autor") or ""),
        )
        values = ["Todos"] + editoriales
        self.combo_editorial["values"] = values
        if self.editorial_var.get() not in values:
            self.editorial_var.set("Todos")
        self.combo_editorial.configure(state="readonly" if editoriales else "disabled")
        if not editoriales:
            self.editorial_var.set("Todos")

    def _current_db_filters(
        self,
        *,
        include_author: bool = True,
        include_editorial: bool = True,
    ) -> dict[str, object]:
        curso = (self.curso_var.get() or "").strip()
        if curso == "Todos":
            curso = ""
        else:
            curso = self.practice_controller.normalizar_curso(curso)
        autor = (self.autor_var.get() or "").strip() if include_author else ""
        editorial = (self.editorial_var.get() or "").strip() if include_editorial else ""
        if autor == "Todos":
            autor = ""
        if editorial == "Todos":
            editorial = ""
        return {
            "curso": curso,
            "tema_id": self._tema_label_to_id.get((self.tema_var.get() or "").strip()),
            "subtema_id": self._subtema_label_to_id.get((self.subtema_var.get() or "").strip()),
            "autor": autor,
            "editorial": editorial,
            "estado": (self.estado_var.get() or "Todos").strip() or "Todos",
            "clave": (self.clave_var.get() or "Todos").strip() or "Todos",
        }

    def _refresh_db_count(self) -> None:
        db = (self.db_name_var.get() or "").strip()
        if not db:
            self._db_source_count_var.set("Problemas disponibles: 0")
            return
        filters = self._current_db_filters()
        try:
            total = self.practice_controller.contar_problemas(
                db,
                curso=str(filters.get("curso") or ""),
                tema_id=filters.get("tema_id"),
                subtema_id=filters.get("subtema_id"),
                autor=str(filters.get("autor") or ""),
                editorial=str(filters.get("editorial") or ""),
                estado=str(filters.get("estado") or "Todos"),
                clave=str(filters.get("clave") or "Todos"),
            )
        except Exception as exc:
            self._db_source_count_var.set("Problemas disponibles: error")
            self._log(f"Error contando problemas para Modulo 7: {exc}")
            return
        self._db_source_count_var.set(f"Problemas disponibles: {total}")

    def _current_db_preview_signature(self) -> tuple[object, ...]:
        filters = self._current_db_filters()
        return (
            (self.db_name_var.get() or "").strip(),
            str(filters.get("curso") or ""),
            filters.get("tema_id"),
            filters.get("subtema_id"),
            str(filters.get("autor") or ""),
            str(filters.get("editorial") or ""),
            str(filters.get("estado") or "Todos"),
            str(filters.get("clave") or "Todos"),
            bool(self.aleatorio_var.get()),
        )

    def _ensure_db_preview_window(self) -> tk.Toplevel:
        if self._db_preview_window is not None and self._db_preview_window.winfo_exists():
            return self._db_preview_window

        win = tk.Toplevel(self)
        win.title("Visualizador de problemas - Seleccionador")
        win.geometry("1280x760")
        win.minsize(1000, 620)
        win.transient(self)
        win.protocol("WM_DELETE_WINDOW", self._close_db_preview_window)

        root = ttk.Frame(win, padding=12)
        root.pack(fill="both", expand=True)

        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=(0, 8))
        ttk.Button(actions, text="Recargar", command=self._load_db_preview, style="Ghost.TButton").pack(side="left")
        ttk.Button(actions, text="Reabrir MathJax", command=self._open_db_render_preview, style="Ghost.TButton").pack(
            side="left", padx=(8, 0)
        )
        self._db_open_pdf_btn = ttk.Button(
            actions,
            text="Ver PDF asociado",
            command=self._open_selected_problem_pdf,
            style="Ghost.TButton",
        )
        self._db_open_pdf_btn.pack(side="left", padx=(8, 0))
        self._db_open_pdf_btn.configure(state="disabled")
        ttk.Button(actions, text="Seleccionar todos", command=self._select_all_db_preview, style="Ghost.TButton").pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(actions, text="Deseleccionar todos", command=self._clear_db_selection, style="Ghost.TButton").pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(actions, text="Finalizar seleccion", command=self._finalize_db_preview_selection, style="Accent.TButton").pack(
            side="left", padx=(8, 0)
        )
        ttk.Label(actions, textvariable=self._db_preview_count_var).pack(side="right")

        pane = ttk.Panedwindow(root, orient="horizontal")
        pane.pack(fill="both", expand=True)

        list_wrap = ttk.Frame(pane)
        tree = ttk.Treeview(
            list_wrap,
            columns=("sel", "numero", "curso", "tema", "subtema", "clave"),
            show="headings",
            height=20,
        )
        tree.heading("sel", text="Sel")
        tree.heading("numero", text="Nro")
        tree.heading("curso", text="Curso")
        tree.heading("tema", text="Tema")
        tree.heading("subtema", text="Subtema")
        tree.heading("clave", text="Clave")
        tree.column("sel", width=56, anchor="center", stretch=False)
        tree.column("numero", width=70, anchor="center", stretch=False)
        tree.column("curso", width=110, stretch=False)
        tree.column("tema", width=220)
        tree.column("subtema", width=180)
        tree.column("clave", width=60, anchor="center", stretch=False)
        tree_scroll = ttk.Scrollbar(list_wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=tree_scroll.set)
        tree.tag_configure("selected_problem", background="#dcfce7", foreground="#14532d")
        tree.tag_configure("course_geometria", background="#eefdf3", foreground="#14532d")
        tree.tag_configure("course_algebra", background="#eff6ff", foreground="#1e3a8a")
        tree.tag_configure("course_aritmetica", background="#fffbeb", foreground="#92400e")
        tree.tag_configure("course_trigonometria", background="#fdf2f8", foreground="#9d174d")
        tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        tree.bind("<<TreeviewSelect>>", self._on_db_preview_select)
        tree.bind("<ButtonRelease-1>", self._on_db_preview_click)
        tree.bind("<Double-1>", self._on_db_preview_double_click)
        tree.bind("<Return>", self._on_db_preview_toggle)
        tree.bind("<KeyPress-s>", self._focus_next_db_preview)
        tree.bind("<KeyPress-S>", self._focus_next_db_preview)
        tree.bind("<KeyPress-a>", self._focus_prev_db_preview)
        tree.bind("<KeyPress-A>", self._focus_prev_db_preview)

        preview_wrap = ttk.Frame(pane)
        ttk.Label(preview_wrap, text="Vista previa del enunciado", style="Section.TLabel").pack(
            anchor="w", padx=4, pady=(0, 6)
        )
        ttk.Label(
            preview_wrap,
            text="Teclas: A anterior, S siguiente, Enter seleccionar. Clic en 'Sel' o doble clic para incluir/quitar. La seleccion es acumulativa entre filtros y recargas. MathJax se sincroniza en la ventana vinculada.",
        ).pack(anchor="w", padx=4, pady=(0, 6))

        ttk.Label(preview_wrap, textvariable=self._db_preview_render_status_var).pack(anchor="w", padx=4, pady=(0, 6))
        ttk.Label(preview_wrap, text="Texto base").pack(anchor="w", padx=4, pady=(8, 6))
        text = tk.Text(
            preview_wrap,
            height=18,
            bg=self.palette["surface"],
            fg=self.palette["text"],
            insertbackground=self.palette["text"],
            relief="solid",
            bd=1,
            wrap="word",
            highlightthickness=1,
            highlightbackground=self.palette["border"],
        )
        text.pack(fill="both", expand=True, padx=4)
        text.configure(state="disabled")
        ttk.Label(preview_wrap, text="Imagen(es) asociada(s)").pack(anchor="w", padx=4, pady=(10, 6))
        render_frame = ttk.Frame(preview_wrap)
        render_frame.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        render_canvas = tk.Canvas(
            render_frame,
            bg="white",
            highlightthickness=1,
            highlightbackground=self.palette["border"],
            relief="solid",
            bd=1,
        )
        render_scroll_y = ttk.Scrollbar(render_frame, orient="vertical", command=render_canvas.yview)
        render_scroll_x = ttk.Scrollbar(render_frame, orient="horizontal", command=render_canvas.xview)
        render_canvas.configure(yscrollcommand=render_scroll_y.set, xscrollcommand=render_scroll_x.set)
        render_canvas.grid(row=0, column=0, sticky="nsew")
        render_scroll_y.grid(row=0, column=1, sticky="ns")
        render_scroll_x.grid(row=1, column=0, sticky="ew")
        render_frame.grid_rowconfigure(0, weight=1)
        render_frame.grid_columnconfigure(0, weight=1)

        pane.add(list_wrap, weight=3)
        pane.add(preview_wrap, weight=2)

        self._db_preview_window = win
        self.db_preview_tree = tree
        self.txt_db_preview = text
        self._db_preview_render_canvas = render_canvas
        self._render_db_preview_tree()
        return win

    def _open_db_preview_window(self) -> None:
        self._load_db_preview()
        if self._db_preview_window is None or not self._db_preview_window.winfo_exists():
            return
        self._db_preview_window.deiconify()
        self._db_preview_window.lift()
        self._db_preview_window.focus_force()

    def _close_db_preview_window(self) -> None:
        if self._db_preview_window is not None and self._db_preview_window.winfo_exists():
            self._db_preview_window.destroy()
        self._db_preview_window = None
        self.db_preview_tree = None
        self.txt_db_preview = None
        self._db_preview_render_canvas = None
        self._db_preview_render_image = []
        self._db_open_pdf_btn = None
        self._cancel_db_render_poll()
        try:
            self._db_render_preview.close()
        except Exception:
            pass

    def _render_db_preview_tree(self) -> None:
        if self.db_preview_tree is None or not self.db_preview_tree.winfo_exists():
            return
        for item_id in self.db_preview_tree.get_children():
            self.db_preview_tree.delete(item_id)
        for problem in self._db_preview_items:
            pid = int(problem.get("id") or 0)
            self.db_preview_tree.insert(
                "",
                "end",
                iid=str(pid),
                values=(
                    "X" if pid in self._db_selected_problem_ids else "",
                    int(problem.get("numero_original") or 0) or pid,
                    str(problem.get("curso") or ""),
                    str(problem.get("tema") or ""),
                    str(problem.get("subtema") or ""),
                    str(problem.get("respuesta_correcta") or ""),
                ),
                tags=self._db_preview_row_tags(problem),
            )
        if not self._db_preview_items:
            self._set_db_preview_text("")

    @staticmethod
    def _normalize_db_preview_course_tag(value: object) -> str:
        raw = str(value or "").strip().lower()
        replacements = {
            "á": "a",
            "é": "e",
            "í": "i",
            "ó": "o",
            "ú": "u",
            "ü": "u",
        }
        for src, dst in replacements.items():
            raw = raw.replace(src, dst)
        raw = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
        return raw

    def _db_preview_row_tags(self, problem: dict[str, object]) -> tuple[str, ...]:
        pid = int(problem.get("id") or 0)
        if pid in self._db_selected_problem_ids:
            return ("selected_problem",)
        course = self._normalize_db_preview_course_tag(problem.get("curso"))
        if course in {"geometria", "geometria_analitica"}:
            return ("course_geometria",)
        if course == "algebra":
            return ("course_algebra",)
        if course == "aritmetica":
            return ("course_aritmetica",)
        if course == "trigonometria":
            return ("course_trigonometria",)
        return ()

    def _build_db_preview_render_text(self, problem: dict[str, object]) -> str:
        raw = str(problem.get("enunciado_latex") or "").strip()
        if not raw:
            return ""
        text = self._normalize_db_preview_text_separators(raw).replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(
            r"\\item\s*\[\s*\\textbf\{\s*(\d+)\.?\s*\}\s*\]\s*",
            lambda m: f"{m.group(1)}. ",
            text,
            flags=re.IGNORECASE,
        )
        text = text.replace("£", "\n").replace("æ", "\n")
        text = re.sub(r"\[\[\s*Imagen\s*=\s*([^\]\r\n]+?)\s*\]\]", r"[Imagen: \1]", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"(?<!\n)([A-E]\))", r"\n\1", text)
        return text.strip()

    def _collect_problem_tags(self, problem: dict[str, object]) -> list[str]:
        raw = self._normalize_db_preview_text_separators(str(problem.get("enunciado_latex") or ""))
        tags: list[str] = []
        seen: set[str] = set()
        for match in GENERIC_TAG_RE.finditer(raw):
            value = str(match.group(1) or "").strip()
            if not value:
                continue
            tag = f"[[{value}]]"
            if tag in seen:
                continue
            seen.add(tag)
            tags.append(tag)
        return tags

    def _collect_db_preview_display_tags(self, problem: dict[str, object]) -> list[str]:
        tags: list[str] = []
        seen: set[str] = set()

        def _append(tag_value: str) -> None:
            clean = str(tag_value or "").strip()
            if not clean:
                return
            key = clean.lower()
            if key in seen:
                return
            seen.add(key)
            tags.append(clean)

        course = str(problem.get("curso") or "").strip()
        topic = str(problem.get("tema") or "").strip()
        subtopic = str(problem.get("subtema") or "").strip()
        exam = str(problem.get("examen") or "").strip()
        if course:
            _append(f"[[curso={course}]]")
        if topic:
            _append(f"[[tema={topic}]]")
        if subtopic:
            _append(f"[[subtema={subtopic}]]")
        if exam:
            _append(f"[[examen={exam}]]")
        for tag in self._collect_problem_tags(problem):
            if IMAGE_MARKER_RE.fullmatch(str(tag or "").strip()):
                continue
            _append(tag)
        return tags

    def _collect_db_preview_visualizer_tags(self, problem: dict[str, object]) -> list[str]:
        tags: list[str] = []
        seen: set[str] = set()

        def _append(tag_value: str) -> None:
            clean = str(tag_value or "").strip()
            if not clean:
                return
            key = clean.lower()
            if key in seen:
                return
            seen.add(key)
            tags.append(clean)

        for tag in self._collect_db_preview_display_tags(problem):
            _append(tag)
        for marker_name in self._resolve_db_preview_problem_markers(problem):
            _append(f"[[Imagen={marker_name}]]")
        return tags

    def _structured_db_preview_image_paths(self, problem: dict[str, object]) -> list[str]:
        raw_images = problem.get("imagenes", [])
        if not isinstance(raw_images, (list, tuple)):
            return []
        ordered: list[str] = []
        seen: set[str] = set()
        for raw_value in raw_images:
            clean = str(raw_value or "").strip()
            if not clean:
                continue
            key = clean.lower()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(clean)
        return ordered

    def _resolve_db_preview_structured_images(self, problem: dict[str, object]) -> list[tuple[str, Path]]:
        entries: list[tuple[str, Path]] = []
        marker_counts: dict[str, int] = {}
        ruta_carpeta = str(problem.get("ruta_carpeta") or "").strip()
        base_dir = self._normalize_path_lexically(Path(ruta_carpeta)) if ruta_carpeta else None
        candidate_dirs = self._iter_db_preview_image_dirs(problem)

        for raw_path in self._structured_db_preview_image_paths(problem):
            raw_candidate = self._normalize_path_lexically(Path(raw_path))
            resolved: Path | None = None

            try:
                if raw_candidate.is_absolute() and raw_candidate.exists() and raw_candidate.is_file():
                    resolved = raw_candidate
            except Exception:
                resolved = None

            if resolved is None and base_dir is not None:
                candidate = self._normalize_path_lexically(base_dir / raw_path)
                try:
                    if candidate.exists() and candidate.is_file():
                        resolved = candidate
                except Exception:
                    resolved = None

            if resolved is None:
                raw_name = Path(raw_path).name.strip()
                for folder in candidate_dirs:
                    if not raw_name:
                        break
                    candidate = self._normalize_path_lexically(folder / raw_name)
                    try:
                        if candidate.exists() and candidate.is_file():
                            resolved = candidate
                            break
                    except Exception:
                        continue

            raw_marker_name = Path(raw_path).stem.strip()
            marker_name = raw_marker_name
            if resolved is None or not marker_name:
                continue
            marker_key = raw_marker_name.lower()
            marker_counts[marker_key] = marker_counts.get(marker_key, 0) + 1
            if marker_counts[marker_key] > 1:
                marker_name = f"{raw_marker_name}_{marker_counts[marker_key]}"
            entries.append((marker_name, resolved))
        return entries

    def _resolve_db_preview_problem_markers(self, problem: dict[str, object]) -> list[str]:
        structured_entries = self._resolve_db_preview_structured_images(problem)
        if structured_entries:
            return [marker_name for marker_name, _path in structured_entries]

        raw = self._normalize_db_preview_text_separators(str(problem.get("enunciado_latex") or "").strip())
        markers: list[str] = []
        seen: set[str] = set()

        def _append(marker_name: str) -> None:
            clean = str(marker_name or "").strip()
            if not clean or clean.lower() in seen:
                return
            seen.add(clean.lower())
            markers.append(clean)

        for marker_name in self._extract_session_item_markers(raw):
            _append(marker_name)

        return markers

    def _strip_db_preview_generic_tags(self, text: str) -> str:
        normalized = self._normalize_db_preview_text_separators(str(text or ""))
        return GENERIC_TAG_RE.sub(" ", normalized)

    def _normalize_db_preview_text_separators(self, text: str) -> str:
        normalized = str(text or "")
        replacements = {
            "Â£": "£",
            "Ã¦": "æ",
            "\u00a0": " ",
        }
        for source, target in replacements.items():
            normalized = normalized.replace(source, target)
        return normalized

    def _inject_db_preview_markers_before_options(self, text: str, marker_names: list[str]) -> str:
        clean_text = self._normalize_db_preview_text_separators(str(text or ""))
        clean_text = IMAGE_MARKER_RE.sub(" ", clean_text)
        clean_text = re.sub(r"[ \t]{2,}", " ", clean_text).strip()
        if not marker_names:
            return clean_text
        marker_block = " ".join(f"[[Imagen={marker_name}]]" for marker_name in marker_names if str(marker_name or "").strip())
        if not marker_block:
            return clean_text
        option_match = re.search(r"(?=(?:£|æ|\r?\n|^)\s*[A-E]\))", clean_text)
        if option_match:
            left = clean_text[: option_match.start()].rstrip()
            right = clean_text[option_match.start() :]
            if left:
                if right.startswith(("£", "æ", "\n", "\r")):
                    return f"{left} {marker_block}{right}".strip()
                return f"{left} {marker_block} {right.lstrip()}".strip()
            return f"{marker_block} {right.lstrip()}".strip()
        return f"{clean_text} {marker_block}".strip()

    def _resolve_db_preview_book_workspace(self, problem: dict[str, object]) -> Path | None:
        db_name = str(self.db_name_var.get() or "").strip()
        book_code = str(problem.get("libro_codigo") or "").strip()
        if not db_name or not book_code:
            return None
        cache_key = (db_name, book_code)
        cached = str(self._db_book_workspace_cache.get(cache_key) or "").strip()
        if cached:
            path = self._normalize_path_lexically(Path(cached))
            if path.exists():
                return path
        conn = None
        try:
            conn = self.db_manager.get_connection(db_name)
            cur = conn.cursor()
            cur.execute("SELECT COALESCE(workspace_dir,'') FROM libros_escaneo WHERE codigo = %s LIMIT 1;", (book_code,))
            row = cur.fetchone()
            workspace = str((row[0] if row else "") or "").strip()
            if workspace:
                self._db_book_workspace_cache[cache_key] = workspace
                path = self._normalize_path_lexically(Path(workspace))
                if path.exists():
                    return path
        except Exception:
            return None
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
        return None

    def _iter_db_preview_strict_image_dirs(self, problem: dict[str, object]) -> list[Path]:
        dirs: list[Path] = []
        seen: set[str] = set()

        def _append(path: Path | None) -> None:
            if path is None:
                return
            normalized = self._normalize_path_lexically(path)
            key = str(normalized).lower()
            if key in seen:
                return
            seen.add(key)
            dirs.append(normalized)

        workspace = self._resolve_db_preview_book_workspace(problem)
        instance_type = str(problem.get("codigo_instancia") or "").strip()
        if workspace is not None and instance_type:
            try:
                instance_dirs = project_dirs(workspace, instance_type)
                _append(instance_dirs.get("crops_dir"))
                _append(instance_dirs.get("segments_dir"))
                _append(instance_dirs.get("sources_dir"))
            except Exception:
                pass
        return dirs

    def _iter_db_preview_image_dirs(self, problem: dict[str, object]) -> list[Path]:
        dirs = list(self._iter_db_preview_strict_image_dirs(problem))
        seen = {str(path).lower() for path in dirs}

        def _append(path: Path | None) -> None:
            if path is None:
                return
            normalized = self._normalize_path_lexically(path)
            key = str(normalized).lower()
            if key in seen:
                return
            seen.add(key)
            dirs.append(normalized)

        base_dir_raw = str(self.images_dir_var.get() or "").strip()
        if base_dir_raw:
            _append(Path(base_dir_raw))

        source_raw = str(problem.get("archivo_origen") or "").strip()
        if source_raw:
            try:
                source_path = self._normalize_path_lexically(Path(source_raw))
                if source_path.exists():
                    _append(source_path.parent)
                    if source_path.parent.name.strip().lower() == "sources":
                        _append(source_path.parent.parent / "crops")
                        _append(source_path.parent.parent / "segments")
            except Exception:
                pass
        return dirs

    def _describe_db_problem_image_sources(self, problem: dict[str, object]) -> str:
        dirs = self._iter_db_preview_image_dirs(problem)
        if not dirs:
            return "sin directorios candidatos"
        return " | ".join(str(path) for path in dirs)

    def _build_db_preview_mathjax_text(self, problem: dict[str, object]) -> str:
        body = self._normalize_db_preview_text_separators(str(problem.get("enunciado_latex") or "").strip())
        if not body:
            return ""
        metadata_tags = [
            tag
            for tag in self._collect_db_preview_display_tags(problem)
            if not IMAGE_MARKER_RE.fullmatch(str(tag or "").strip())
        ]
        marker_names = self._resolve_db_preview_problem_markers(problem)
        prefix_match = re.match(
            r"""^\s*(\\item\s*\[\s*\\textbf\{\s*\d+\.?\s*\}\s*\]\s*)""",
            body,
            flags=re.IGNORECASE,
        )
        remainder = body
        prefix = ""
        if prefix_match:
            prefix = prefix_match.group(1)
            remainder = body[prefix_match.end() :].lstrip()
        remainder = self._strip_db_preview_generic_tags(remainder)
        remainder = re.sub(r"\s+(£|æ)", r"\1", remainder)
        remainder = re.sub(r"(£|æ)\s+", r"\1", remainder)
        remainder = re.sub(r"[ \t]{2,}", " ", remainder).strip()
        remainder = self._inject_db_preview_markers_before_options(remainder, marker_names)
        if not metadata_tags:
            return f"{prefix}{remainder}".strip()
        if not prefix_match:
            return f"{' '.join(metadata_tags)} {remainder}".strip()
        return f"{prefix}{' '.join(metadata_tags)} {remainder}".strip()

    def _build_db_preview_mathjax_images(self, problem: dict[str, object]) -> dict[str, str]:
        images: dict[str, str] = {}
        for marker_name, image_path in self._resolve_db_preview_structured_images(problem):
            if marker_name not in images:
                images[marker_name] = str(image_path)
        if images:
            return images
        if not str(problem.get("enunciado_latex") or "").strip():
            return images
        for marker_name in self._resolve_db_preview_problem_markers(problem):
            if marker_name in images:
                continue
            resolved_paths = self._resolve_db_preview_marker_paths(marker_name, problem)
            if not resolved_paths:
                continue
            images[marker_name] = str(resolved_paths[0])
        return images

    def _build_db_preview_mathjax_text_bundle(self, problems: list[dict[str, object]]) -> str:
        blocks: list[str] = []
        for problem in problems:
            block = self._build_db_preview_mathjax_text(problem).strip()
            if block:
                blocks.append(block)
        return "\n\n".join(blocks).strip()

    def _build_db_preview_mathjax_images_bundle(self, problems: list[dict[str, object]]) -> dict[str, str]:
        images: dict[str, str] = {}
        for problem in problems:
            for marker_name, image_path in self._build_db_preview_mathjax_images(problem).items():
                if marker_name not in images:
                    images[marker_name] = image_path
        return images

    def _build_db_preview_image_statuses_bundle(self, problems: list[dict[str, object]]) -> dict[int, str]:
        statuses: dict[int, str] = {}
        for problem in problems:
            try:
                item_num = int(problem.get("numero_original") or 0)
            except Exception:
                item_num = 0
            if item_num <= 0:
                continue
            statuses[item_num] = (
                "imagen_confirmada" if self._resolve_db_preview_problem_markers(problem) else "sin_imagen"
            )
        return statuses

    def _build_db_preview_reconstructed_text(self, problem: dict[str, object]) -> str:
        return self._build_db_preview_mathjax_text(problem)

    def _resolve_db_preview_marker_paths(
        self,
        marker_name: str,
        problem: dict[str, object] | None = None,
        *,
        include_fallback_dirs: bool = True,
    ) -> list[Path]:
        marker = str(marker_name or "").strip()
        if not marker:
            return []
        suffixes = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
        matches: list[Path] = []
        seen: set[str] = set()

        def _append(path: Path) -> None:
            key = str(path).lower()
            if key in seen:
                return
            seen.add(key)
            matches.append(self._normalize_path_lexically(path))

        if isinstance(problem, dict):
            for structured_marker, structured_path in self._resolve_db_preview_structured_images(problem):
                if str(structured_marker or "").strip().lower() == marker.lower():
                    _append(structured_path)
            if matches:
                return matches

        candidate_dirs = (
            self._iter_db_preview_image_dirs(problem or {})
            if include_fallback_dirs
            else self._iter_db_preview_strict_image_dirs(problem or {})
        )
        for base_dir in candidate_dirs:
            try:
                if not base_dir.exists() or not base_dir.is_dir():
                    continue
            except Exception:
                continue
            for suffix in suffixes:
                direct = base_dir / f"{marker}{suffix}"
                try:
                    if direct.exists() and direct.is_file():
                        _append(direct)
                except Exception:
                    continue
            if matches:
                return matches
            try:
                for candidate in base_dir.rglob("*"):
                    if not candidate.is_file():
                        continue
                    if candidate.suffix.lower() not in suffixes:
                        continue
                    if candidate.stem.strip().lower() == marker.lower():
                        _append(candidate)
            except Exception:
                continue
        return matches

    def _render_db_preview_image(self, problem: dict[str, object]) -> None:
        if self._db_preview_render_canvas is None or not self._db_preview_render_canvas.winfo_exists():
            return
        canvas = self._db_preview_render_canvas
        canvas.delete("all")
        self._db_preview_render_image = []
        raw = str(problem.get("enunciado_latex") or "").strip()
        if not raw:
            self._db_preview_render_status_var.set("Sin contenido para vista previa")
            return
        if Image is None or ImageTk is None:
            self._db_preview_render_status_var.set("Vista de imagen no disponible: falta Pillow")
            return
        marker_names = self._resolve_db_preview_problem_markers(problem)
        if not marker_names:
            canvas.create_text(
                16,
                16,
                anchor="nw",
                text="Este problema no tiene imagenes asociadas en BD ni etiquetas [[Imagen=...]] en el enunciado.",
                fill="#475569",
                font=("Segoe UI", 10),
                width=max(canvas.winfo_width() - 32, 320),
            )
            canvas.configure(scrollregion=(0, 0, max(canvas.winfo_width(), 420), 80))
            self._db_preview_render_status_var.set("Sin imagen reconstruible para el problema seleccionado.")
            return

        y_offset = 16
        max_width = 0
        rendered = 0
        missing_markers: list[str] = []
        for marker_name in marker_names:
            resolved_paths = self._resolve_db_preview_marker_paths(marker_name, problem)
            if not resolved_paths:
                missing_markers.append(marker_name)
                continue
            for image_path in resolved_paths:
                try:
                    with Image.open(image_path) as opened:
                        image = opened.convert("RGB")
                    max_preview_width = 520
                    if image.width > max_preview_width:
                        ratio = max_preview_width / float(image.width)
                        image = image.resize((max_preview_width, max(1, int(image.height * ratio))), Image.LANCZOS)
                    tk_image = ImageTk.PhotoImage(image)
                    self._db_preview_render_image.append(tk_image)
                    canvas.create_text(
                        16,
                        y_offset,
                        anchor="nw",
                        text=f"{marker_name}  ->  {image_path.name}",
                        fill="#0f172a",
                        font=("Segoe UI Semibold", 10),
                    )
                    y_offset += 22
                    canvas.create_image(16, y_offset, anchor="nw", image=tk_image)
                    y_offset += image.height + 20
                    max_width = max(max_width, image.width + 32)
                    rendered += 1
                except Exception:
                    missing_markers.append(marker_name)
                    continue

        if rendered <= 0:
            message = "No se pudieron resolver imagenes para las etiquetas del problema."
            if missing_markers:
                message += f"\nFaltantes: {', '.join(sorted(set(missing_markers)))}"
            canvas.create_text(
                16,
                16,
                anchor="nw",
                text=message,
                fill="#7f1d1d",
                font=("Segoe UI", 10),
                width=max(canvas.winfo_width() - 32, 320),
            )
            canvas.configure(scrollregion=(0, 0, max(canvas.winfo_width(), 420), 120))
            self._db_preview_render_status_var.set("No se encontraron archivos de imagen para este problema.")
            return

        canvas.configure(scrollregion=(0, 0, max(max_width, canvas.winfo_width()), y_offset + 8))
        missing_note = ""
        if missing_markers:
            missing_note = f" | faltantes: {', '.join(sorted(set(missing_markers)))}"
        self._db_preview_render_status_var.set(
            f"Imagenes asociadas: {rendered} | etiquetas detectadas: {len(marker_names)}{missing_note}"
        )

    def _clear_db_preview(self, *, silent: bool = False, clear_selection: bool = False) -> None:
        self._db_preview_signature = None
        self._db_preview_items = []
        if clear_selection:
            self._db_selected_problem_ids.clear()
            self._db_selected_problem_order.clear()
            self._db_selected_problem_map.clear()
        if self.db_preview_tree is not None and self.db_preview_tree.winfo_exists():
            for item_id in self.db_preview_tree.get_children():
                self.db_preview_tree.delete(item_id)
        if self._db_preview_render_canvas is not None and self._db_preview_render_canvas.winfo_exists():
            self._db_preview_render_canvas.delete("all")
            self._db_preview_render_canvas.configure(scrollregion=(0, 0, 0, 0))
        self._db_preview_render_image = []
        self._db_preview_render_status_var.set("Sin vista previa")
        self._set_db_preview_text("")
        try:
            self._db_render_preview.set_text("")
            self._db_render_preview.set_images({})
            self._db_render_preview.set_item_image_statuses({})
            self._db_render_preview.set_active_item(None)
        except Exception:
            pass
        if self._db_open_pdf_btn is not None:
            self._db_open_pdf_btn.configure(state="disabled")
        self._update_db_preview_counter()
        if not silent:
            self._log("Visualizador BD limpiado.")

    def _update_db_preview_counter(self) -> None:
        selected_in_preview = sum(
            1 for problem in self._db_preview_items if int(problem.get("id") or 0) in self._db_selected_problem_ids
        )
        self._db_preview_count_var.set(
            f"Vista previa: {len(self._db_preview_items)} | "
            f"Seleccion actual: {selected_in_preview} | "
            f"Acumulados: {len(self._db_selected_problem_ids)}"
        )

    def _sync_db_render_preview(self, *, active_problem: dict[str, object] | None = None) -> None:
        problems = list(self._db_preview_items)
        try:
            self._db_render_preview.set_text(self._build_db_preview_mathjax_text_bundle(problems))
            self._db_render_preview.set_images(self._build_db_preview_mathjax_images_bundle(problems))
            self._db_render_preview.set_item_image_statuses(self._build_db_preview_image_statuses_bundle(problems))
            active_num = None
            if active_problem is not None:
                try:
                    current = int(active_problem.get("numero_original") or 0)
                except Exception:
                    current = 0
                if current > 0:
                    active_num = current
            self._db_render_preview.set_active_item(active_num)
        except Exception as exc:
            current = str(self._db_preview_render_status_var.get() or "").strip()
            suffix = f"No se pudo sincronizar el render MathJax: {exc}"
            self._db_preview_render_status_var.set(f"{current} | {suffix}" if current else suffix)

    def _set_db_preview_text(self, text: str) -> None:
        if self.txt_db_preview is None or not self.txt_db_preview.winfo_exists():
            return
        self.txt_db_preview.configure(state="normal")
        self.txt_db_preview.delete("1.0", "end")
        if text.strip():
            self.txt_db_preview.insert("1.0", text.strip())
        self.txt_db_preview.configure(state="disabled")

    def _load_db_preview(self) -> None:
        db = (self.db_name_var.get() or "").strip()
        if not db:
            messagebox.showwarning("Modulo 7", "Selecciona una fuente del seleccionador de problemas.")
            return
        filters = self._current_db_filters()
        total = self.practice_controller.contar_problemas(
            db,
            curso=str(filters.get("curso") or ""),
            tema_id=filters.get("tema_id"),
            subtema_id=filters.get("subtema_id"),
            autor=str(filters.get("autor") or ""),
            editorial=str(filters.get("editorial") or ""),
            estado=str(filters.get("estado") or "Todos"),
            clave=str(filters.get("clave") or "Todos"),
        )
        if total <= 0:
            self._clear_db_preview(silent=True)
            messagebox.showinfo("Modulo 7", "No se encontraron problemas para el filtro seleccionado.")
            return
        problemas = self.practice_controller.obtener_problemas(
            db,
            cantidad=total,
            curso=str(filters.get("curso") or ""),
            tema_id=filters.get("tema_id"),
            subtema_id=filters.get("subtema_id"),
            autor=str(filters.get("autor") or ""),
            editorial=str(filters.get("editorial") or ""),
            estado=str(filters.get("estado") or "Todos"),
            clave=str(filters.get("clave") or "Todos"),
            aleatorio=bool(self.aleatorio_var.get()),
        )
        next_signature = self._current_db_preview_signature()
        self._db_preview_signature = next_signature
        self._db_preview_items = problemas
        for problem in problemas:
            pid = int(problem.get("id") or 0)
            if pid > 0 and pid in self._db_selected_problem_ids:
                self._db_selected_problem_map[pid] = dict(problem)
        self._ensure_db_preview_window()
        self._render_db_preview_tree()
        self._update_db_preview_counter()
        self._open_db_render_preview()
        self._log(
            f"Visualizador BD cargado: {len(problemas)} problema(s) | seleccionados={len(self._db_selected_problem_ids)}"
        )

    def _open_db_render_preview(self) -> None:
        if self.db_preview_tree is None or not self.db_preview_tree.winfo_exists():
            self._ensure_db_preview_window()
        active_problem = self._current_db_preview_problem()
        self._sync_db_render_preview(active_problem=active_problem)
        try:
            host = self._db_preview_window if self._db_preview_window is not None else self
            host.update_idletasks()
            x = int(host.winfo_rootx() + host.winfo_width() + 12)
            y = int(host.winfo_rooty() + 60)
        except Exception:
            x = None
            y = None
        self._db_render_preview.ensure_open_at(x=x, y=y, on_top=True)
        self._schedule_db_render_poll()

    def _schedule_db_render_poll(self) -> None:
        if not self.winfo_exists():
            return
        self._cancel_db_render_poll()
        self._db_render_poll_after = self.after(350, self._poll_db_render_preview)

    def _cancel_db_render_poll(self) -> None:
        if not self._db_render_poll_after:
            return
        try:
            self.after_cancel(self._db_render_poll_after)
        except Exception:
            pass
        self._db_render_poll_after = None

    def _poll_db_render_preview(self) -> None:
        self._db_render_poll_after = None
        if not self.winfo_exists():
            return
        try:
            goto_item = self._db_render_preview.pop_goto_item()
        except Exception:
            goto_item = None
        if goto_item:
            matches = [
                problem
                for problem in self._db_preview_items
                if int(problem.get("numero_original") or 0) == int(goto_item)
            ]
            if len(matches) == 1 and self.db_preview_tree is not None and self.db_preview_tree.winfo_exists():
                problem = matches[0]
                pid = int(problem.get("id") or 0)
                if pid > 0 and self.db_preview_tree.exists(str(pid)):
                    self._remember_selected_db_problem(problem)
                    self._refresh_db_preview_tree_marks()
                    self.db_preview_tree.selection_set(str(pid))
                    self.db_preview_tree.focus(str(pid))
                    self.db_preview_tree.see(str(pid))
                    self._show_db_preview_problem(str(pid))
        try:
            edit_requests = self._db_render_preview.pop_edit_requests()
        except Exception:
            edit_requests = []

        changed = False
        for req in edit_requests:
            target_problem = self._resolve_db_preview_problem_for_edit_request(req)
            if target_problem is None:
                continue
            new_text = self._normalize_db_preview_edit_text(
                current_text=str(target_problem.get("enunciado_latex") or ""),
                new_text=str((req or {}).get("text") or ""),
                expected_number=int(target_problem.get("numero_original") or 0),
            )
            if not new_text:
                continue
            if new_text == str(target_problem.get("enunciado_latex") or "").strip():
                continue
            db_name = (self.db_name_var.get() or "").strip()
            if not db_name:
                continue
            try:
                updated = self.practice_controller.actualizar_enunciado_problema(
                    db_name,
                    problem_id=int(target_problem.get("id") or 0),
                    enunciado_latex=new_text,
                )
            except Exception as exc:
                self._log(f"No se pudo guardar la edicion del problema {target_problem.get('id')}: {exc}")
                continue
            self._replace_db_preview_problem(updated)
            self._log(
                f"Problema {updated.get('id')} guardado en BD desde MathJax "
                f"(nro original {updated.get('numero_original')})."
            )
            changed = True

        if changed:
            self._refresh_db_preview_tree_marks()
            current_problem = self._current_db_preview_problem()
            if current_problem is not None:
                self._show_db_preview_problem(str(int(current_problem.get("id") or 0)))
            else:
                self._sync_db_render_preview(active_problem=None)

        self._db_render_poll_after = self.after(350, self._poll_db_render_preview)

    def _resolve_problem_pdf_path(self, problem: dict[str, object] | None) -> Path | None:
        if not isinstance(problem, dict):
            return None
        candidates = [
            str(problem.get("pdf_path") or "").strip(),
            str(problem.get("archivo_origen") or "").strip(),
        ]
        for raw in candidates:
            if not raw:
                continue
            path = self._normalize_path_lexically(Path(raw))
            if path.suffix.lower() != ".pdf":
                continue
            if path.exists():
                return path
        return None

    def _open_selected_problem_pdf(self) -> None:
        problem = self._current_db_preview_problem()
        if problem is None:
            messagebox.showwarning("Modulo 7", "Selecciona un problema en el visualizador.")
            return
        pdf_path = self._resolve_problem_pdf_path(problem)
        if pdf_path is None:
            messagebox.showwarning(
                "Modulo 7",
                "El problema seleccionado no tiene un PDF asociado accesible en esta maquina.",
            )
            return
        try:
            os.startfile(str(pdf_path))  # type: ignore[attr-defined]
        except Exception:
            try:
                subprocess.Popen(["cmd", "/c", "start", "", str(pdf_path)])
            except Exception as exc:
                messagebox.showerror("Modulo 7", f"No se pudo abrir el PDF asociado.\n\n{exc}")

    def _extract_preview_item_number(self, text: str) -> int:
        match = re.search(
            r"""\\item\s*\[\s*\\textbf\{\s*(\d+)\.?\s*\}\s*\]""",
            str(text or ""),
            flags=re.IGNORECASE,
        )
        if not match:
            return 0
        try:
            return int(match.group(1))
        except Exception:
            return 0

    def _normalize_db_preview_edit_text(self, *, current_text: str, new_text: str, expected_number: int) -> str:
        old_value = str(current_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        candidate = str(new_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not candidate:
            return ""
        old_prefix_match = re.match(
            r"""^\s*(\\item\s*\[\s*\\textbf\{\s*\d+\.?\s*\}\s*\]\s*)""",
            old_value,
            flags=re.IGNORECASE,
        )
        old_prefix = old_prefix_match.group(1) if old_prefix_match else ""
        if "\\item[" not in candidate and old_prefix:
            candidate = f"{old_prefix}{candidate}".strip()
        if expected_number > 0 and old_prefix:
            candidate_number = self._extract_preview_item_number(candidate)
            if candidate_number and candidate_number != expected_number:
                body = re.sub(
                    r"""^\s*\\item\s*\[\s*\\textbf\{\s*\d+\.?\s*\}\s*\]\s*""",
                    "",
                    candidate,
                    flags=re.IGNORECASE,
                ).strip()
                candidate = f"{old_prefix}{body}".strip() if body else old_value
        return candidate

    def _current_db_preview_problem(self) -> dict[str, object] | None:
        if self.db_preview_tree is not None and self.db_preview_tree.winfo_exists():
            selection = self.db_preview_tree.selection()
            if selection:
                return self._find_db_preview_problem(selection[0])
        return None

    def _resolve_db_preview_problem_for_edit_request(self, req: dict[str, object]) -> dict[str, object] | None:
        current = self._current_db_preview_problem()
        try:
            requested_item = int((req or {}).get("item") or 0)
        except Exception:
            requested_item = 0
        if requested_item <= 0:
            return current
        if current is not None and int(current.get("numero_original") or 0) == requested_item:
            return current
        matches = [
            problem
            for problem in self._db_preview_items
            if int(problem.get("numero_original") or 0) == requested_item
        ]
        if len(matches) == 1:
            return matches[0]
        return current

    def _replace_db_preview_problem(self, updated_problem: dict[str, object]) -> None:
        updated_id = int(updated_problem.get("id") or 0)
        if updated_id <= 0:
            return
        for idx, problem in enumerate(self._db_preview_items):
            if int(problem.get("id") or 0) == updated_id:
                self._db_preview_items[idx] = updated_problem
                break
        if updated_id in self._db_selected_problem_ids:
            self._db_selected_problem_map[updated_id] = dict(updated_problem)

    def _remember_selected_db_problem(self, problem: dict[str, object]) -> None:
        pid = int(problem.get("id") or 0)
        if pid <= 0:
            return
        self._db_selected_problem_ids.add(pid)
        if pid not in self._db_selected_problem_order:
            self._db_selected_problem_order.append(pid)
        self._db_selected_problem_map[pid] = dict(problem)

    def _forget_selected_db_problem(self, problem_id: int) -> None:
        pid = int(problem_id or 0)
        if pid <= 0:
            return
        self._db_selected_problem_ids.discard(pid)
        self._db_selected_problem_map.pop(pid, None)
        self._db_selected_problem_order = [current_id for current_id in self._db_selected_problem_order if current_id != pid]

    def _select_all_db_preview(self) -> None:
        if not self._db_preview_items:
            return
        for problem in self._db_preview_items:
            self._remember_selected_db_problem(problem)
        self._refresh_db_preview_tree_marks()

    def _clear_db_selection(self) -> None:
        self._db_selected_problem_ids.clear()
        self._db_selected_problem_order.clear()
        self._db_selected_problem_map.clear()
        self._refresh_db_preview_tree_marks()

    def _refresh_db_preview_tree_marks(self) -> None:
        if self.db_preview_tree is None or not self.db_preview_tree.winfo_exists():
            self._update_db_preview_counter()
            return
        for problem in self._db_preview_items:
            pid = int(problem.get("id") or 0)
            if pid <= 0 or not self.db_preview_tree.exists(str(pid)):
                continue
            values = list(self.db_preview_tree.item(str(pid), "values"))
            if values:
                selected = pid in self._db_selected_problem_ids
                values[0] = "X" if selected else ""
                self.db_preview_tree.item(
                    str(pid),
                    values=values,
                    tags=self._db_preview_row_tags(problem),
                )
        self._update_db_preview_counter()

    def _find_db_preview_problem(self, item_id: str) -> dict[str, object] | None:
        try:
            pid = int(item_id)
        except Exception:
            return None
        for problem in self._db_preview_items:
            if int(problem.get("id") or 0) == pid:
                return problem
        return None

    def _show_db_preview_problem(self, item_id: str) -> None:
        problem = self._find_db_preview_problem(item_id)
        if problem is None:
            self._set_db_preview_text("")
            if self._db_open_pdf_btn is not None:
                self._db_open_pdf_btn.configure(state="disabled")
            try:
                self._sync_db_render_preview(active_problem=None)
            except Exception:
                pass
            return
        header = (
            f"ID: {problem.get('id')}\n"
            f"Nro original: {problem.get('numero_original')}\n"
            f"Curso: {problem.get('curso') or '-'}\n"
            f"Tema: {problem.get('tema') or '-'}\n"
            f"Subtema: {problem.get('subtema') or '-'}\n"
            f"Autor: {problem.get('autor') or '-'}\n"
            f"Editorial: {problem.get('editorial') or '-'}\n"
            f"Clave: {problem.get('respuesta_correcta') or '-'}\n"
        )
        tags = self._collect_db_preview_visualizer_tags(problem)
        if tags:
            header += "Etiquetas detectadas:\n" + "\n".join(f"- {tag}" for tag in tags) + "\n"
        pdf_path = self._resolve_problem_pdf_path(problem)
        if pdf_path is not None:
            header += f"PDF asociado: {pdf_path.name}\n"
        reconstructed = self._build_db_preview_reconstructed_text(problem)
        if reconstructed:
            header += "Item reconstruido:\n" + reconstructed + "\n"
        self._set_db_preview_text(header.strip())
        self._render_db_preview_image(problem)
        if self._db_open_pdf_btn is not None:
            self._db_open_pdf_btn.configure(state="normal" if pdf_path is not None else "disabled")
        self._sync_db_render_preview(active_problem=problem)

    def _on_db_preview_select(self, _event=None) -> None:
        if self.db_preview_tree is None or not self.db_preview_tree.winfo_exists():
            return
        selection = self.db_preview_tree.selection()
        if not selection:
            self._set_db_preview_text("")
            if self._db_open_pdf_btn is not None:
                self._db_open_pdf_btn.configure(state="disabled")
            self._sync_db_render_preview(active_problem=None)
            return
        self._show_db_preview_problem(selection[0])

    def _on_db_preview_click(self, event=None) -> str | None:
        if self.db_preview_tree is None or not self.db_preview_tree.winfo_exists() or event is None:
            return None
        row_id = self.db_preview_tree.identify_row(event.y)
        if not row_id:
            return None
        column_id = self.db_preview_tree.identify_column(event.x)
        if column_id == "#1":
            self.db_preview_tree.selection_set(row_id)
            self.db_preview_tree.focus(row_id)
            return self._on_db_preview_toggle()
        self.db_preview_tree.selection_set(row_id)
        self.db_preview_tree.focus(row_id)
        self._show_db_preview_problem(row_id)
        return None

    def _on_db_preview_double_click(self, event=None) -> str:
        if self.db_preview_tree is None or not self.db_preview_tree.winfo_exists() or event is None:
            return "break"
        row_id = self.db_preview_tree.identify_row(event.y)
        if not row_id:
            return "break"
        self.db_preview_tree.selection_set(row_id)
        self.db_preview_tree.focus(row_id)
        return self._on_db_preview_toggle()

    def _on_db_preview_toggle(self, _event=None) -> str:
        if self.db_preview_tree is None or not self.db_preview_tree.winfo_exists():
            return "break"
        selection = self.db_preview_tree.selection()
        if not selection:
            return "break"
        item_id = selection[0]
        try:
            pid = int(item_id)
        except Exception:
            return "break"
        if pid in self._db_selected_problem_ids:
            self._forget_selected_db_problem(pid)
        else:
            problem = self._find_db_preview_problem(item_id)
            if problem is not None:
                self._remember_selected_db_problem(problem)
        self._refresh_db_preview_tree_marks()
        self._show_db_preview_problem(item_id)
        return "break"

    def _focus_next_db_preview(self, _event=None) -> str:
        if self.db_preview_tree is None or not self.db_preview_tree.winfo_exists():
            return "break"
        children = list(self.db_preview_tree.get_children())
        if not children:
            return "break"
        selection = self.db_preview_tree.selection()
        if not selection:
            target = children[0]
        else:
            idx = children.index(selection[0])
            target = children[min(idx + 1, len(children) - 1)]
        self.db_preview_tree.selection_set(target)
        self.db_preview_tree.focus(target)
        self.db_preview_tree.see(target)
        self._show_db_preview_problem(target)
        return "break"

    def _focus_prev_db_preview(self, _event=None) -> str:
        if self.db_preview_tree is None or not self.db_preview_tree.winfo_exists():
            return "break"
        children = list(self.db_preview_tree.get_children())
        if not children:
            return "break"
        selection = self.db_preview_tree.selection()
        if not selection:
            target = children[0]
        else:
            idx = children.index(selection[0])
            target = children[max(idx - 1, 0)]
        self.db_preview_tree.selection_set(target)
        self.db_preview_tree.focus(target)
        self.db_preview_tree.see(target)
        self._show_db_preview_problem(target)
        return "break"

    def _selected_db_preview_items(self) -> list[dict[str, object]]:
        ordered_ids = [pid for pid in self._db_selected_problem_order if pid in self._db_selected_problem_ids]
        if not ordered_ids:
            return []
        missing_ids = [pid for pid in ordered_ids if pid not in self._db_selected_problem_map]
        if missing_ids:
            db_name = (self.db_name_var.get() or "").strip()
            if db_name:
                try:
                    fetched = self.practice_controller.obtener_problemas_por_ids(db_name, problem_ids=missing_ids)
                except Exception as exc:
                    self._log(f"No se pudieron recargar problemas seleccionados desde BD: {exc}")
                else:
                    for problem in fetched:
                        pid = int(problem.get("id") or 0)
                        if pid > 0:
                            self._db_selected_problem_map[pid] = dict(problem)
        return [self._db_selected_problem_map[pid] for pid in ordered_ids if pid in self._db_selected_problem_map]

    def _load_saved_db_selection_items(self, selection_path: Path, *, db_name: str) -> list[dict[str, object]]:
        if not selection_path.exists():
            return []
        try:
            payload = json.loads(selection_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self._log(f"No se pudo leer la seleccion guardada {selection_path}: {exc}")
            return []
        if not isinstance(payload, dict):
            return []
        payload_db = str(payload.get("database") or "").strip()
        if payload_db and payload_db != db_name:
            return []
        raw_items = payload.get("items")
        if isinstance(raw_items, list):
            items: list[dict[str, object]] = []
            for idx, row in enumerate(raw_items, start=1):
                if not isinstance(row, dict):
                    continue
                try:
                    pid = int(row.get("id") or 0)
                except Exception:
                    pid = 0
                if pid <= 0:
                    continue
                item = dict(row)
                item["order"] = int(item.get("order") or idx)
                items.append(item)
            if items:
                return sorted(items, key=lambda item: int(item.get("order") or 0))
        raw_ids = payload.get("selected_ids") or []
        if not isinstance(raw_ids, list):
            return []
        ids: list[int] = []
        seen: set[int] = set()
        for raw_id in raw_ids:
            try:
                pid = int(raw_id)
            except Exception:
                continue
            if pid <= 0 or pid in seen:
                continue
            seen.add(pid)
            ids.append(pid)
        if not ids:
            return []
        try:
            return self.practice_controller.obtener_problemas_por_ids(db_name, problem_ids=ids)
        except Exception as exc:
            self._log(f"No se pudo reconstruir la seleccion guardada desde BD ({selection_path}): {exc}")
            return []

    def _collect_db_selected_items(self, *, output_docx: Path | None = None) -> list[dict[str, object]]:
        db_name = (self.db_name_var.get() or "").strip()
        if not db_name:
            return []

        candidate_paths: list[Path] = []
        primary_path = self._db_selection_output_path(output_docx=output_docx)
        candidate_paths.append(primary_path)
        if self._db_selection_json_path is not None and self._db_selection_json_path not in candidate_paths:
            candidate_paths.append(self._db_selection_json_path)
        fallback_path = self._db_selection_output_path(output_docx=None)
        if fallback_path not in candidate_paths:
            candidate_paths.append(fallback_path)

        ordered_ids: list[int] = []
        by_id: dict[int, dict[str, object]] = {}

        def merge_items(items: list[dict[str, object]], *, overwrite: bool = True) -> None:
            for item in items:
                pid = int(item.get("id") or 0)
                if pid <= 0:
                    continue
                if pid not in by_id:
                    ordered_ids.append(pid)
                if overwrite or pid not in by_id:
                    by_id[pid] = dict(item)

        merge_items(self._selected_db_preview_items(), overwrite=True)
        for path in candidate_paths:
            merge_items(self._load_saved_db_selection_items(path, db_name=db_name), overwrite=True)

        return [by_id[pid] for pid in ordered_ids if pid in by_id]

    def _selection_json_has_editable_items(self, path: Path) -> bool:
        if not path.exists():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False
        return isinstance(payload, dict) and isinstance(payload.get("items"), list) and bool(payload.get("items"))

    def _db_selection_output_path(self, *, output_docx: Path | None = None) -> Path:
        base_output = output_docx
        if base_output is None:
            raw_output = (self.output_docx_var.get() or "").strip()
            if raw_output:
                base_output = Path(raw_output)
            else:
                base_output = Path.cwd() / self._default_output_name()
        return base_output.with_name(f"{base_output.stem}__ids_problemas.json")

    def _save_db_selected_problem_ids(self, *, output_docx: Path | None = None) -> Path:
        problemas = self._collect_db_selected_items(output_docx=output_docx)
        if not problemas:
            raise RuntimeError("No hay problemas seleccionados en el visualizador.")
        target = self._db_selection_output_path(output_docx=output_docx)
        target.parent.mkdir(parents=True, exist_ok=True)
        filters = self._current_db_filters()
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "database": (self.db_name_var.get() or "").strip(),
            "filters": {
                "curso": str(filters.get("curso") or ""),
                "tema_id": filters.get("tema_id"),
                "subtema_id": filters.get("subtema_id"),
                "autor": str(filters.get("autor") or ""),
                "editorial": str(filters.get("editorial") or ""),
                "estado": str(filters.get("estado") or "Todos"),
                "clave": str(filters.get("clave") or "Todos"),
                "aleatorio": bool(self.aleatorio_var.get()),
            },
            "selected_count": len(problemas),
            "selected_ids": [int(problem.get("id") or 0) for problem in problemas if int(problem.get("id") or 0) > 0],
            "selected_problem_refs": [
                {
                    "id": int(problem.get("id") or 0),
                    "numero_original": int(problem.get("numero_original") or 0),
                    "curso": str(problem.get("curso") or ""),
                    "tema": str(problem.get("tema") or ""),
                    "subtema": str(problem.get("subtema") or ""),
                    "respuesta_correcta": str(problem.get("respuesta_correcta") or ""),
                }
                for problem in problemas
            ],
        }
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._db_selection_json_path = target
        self._log(f"IDs de seleccion BD guardados: {target} | total={len(problemas)}")
        return target

    def _finalize_db_preview_selection(self) -> None:
        try:
            target = self._save_db_selected_problem_ids()
        except Exception as exc:
            messagebox.showwarning("Modulo 7", str(exc))
            return
        self.status_var.set(f"Seleccion BD lista: {target.name}")
        messagebox.showinfo(
            "Modulo 7",
            f"Seleccion guardada correctamente.\n\nArchivo:\n{target}",
        )
        self._close_db_preview_window()

    def _on_close(self) -> None:
        self._cancel_db_render_poll()
        try:
            self._db_render_preview.close()
        except Exception:
            pass
        self._close_db_preview_window()
        self.destroy()

    def _apply_db_schema_fallback(self, db_name: str, exc: Exception) -> None:
        self._clear_db_preview(silent=True, clear_selection=True)
        self.combo_curso["values"] = ["Todos"]
        self.combo_tema["values"] = ["Todos"]
        self.combo_subtema["values"] = ["Todos"]
        self.combo_autor["values"] = ["Todos"]
        self.combo_editorial["values"] = ["Todos"]
        self.curso_var.set("Todos")
        self.tema_var.set("Todos")
        self.subtema_var.set("Todos")
        self.autor_var.set("Todos")
        self.editorial_var.set("Todos")
        self.clave_var.set("Todos")
        self._tema_label_to_id.clear()
        self._subtema_label_to_id.clear()
        total = 0
        try:
            conn = self.db_manager.get_connection(db_name)
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*)::int FROM problemas;")
                row = cur.fetchone()
                total = int(row[0] or 0) if row else 0
            finally:
                conn.close()
        except Exception as fallback_exc:
            self._db_source_count_var.set("Problemas disponibles: error")
            self._log(f"Error cargando filtros desde BD: {exc}")
            self._log(f"Fallback de conteo tambien fallo: {fallback_exc}")
            return
        self._db_source_count_var.set(f"Problemas disponibles: {total}")
        key = (db_name, f"fallback:{type(exc).__name__}:{str(exc)}")
        if self._db_schema_notice_key != key:
            self._db_schema_notice_key = key
            self._log(
                "BD disponible, pero no se pudieron cargar filtros. "
                f"Se usara solo el total de problemas. Detalle: {exc}"
            )

    def _log_db_schema_notice(self, db_name: str, schema: dict[str, object]) -> None:
        if schema.get("uses_catalog"):
            key = (db_name, "catalog")
            if self._db_schema_notice_key != key:
                self._db_schema_notice_key = key
                self._log("BD conectada: Modulo 7 usara catalogo temas/subtemas.")
            return
        has_book_meta = bool(schema.get("has_books_table")) and bool(
            schema.get("book_id_column") or schema.get("book_code_column")
        )
        has_direct = any(
            schema.get(name)
            for name in ("course_column", "topic_column", "subtopic_column", "author_column", "editorial_column")
        )
        if has_direct or has_book_meta:
            key = (db_name, "direct_plus_books" if has_book_meta else "direct")
            if self._db_schema_notice_key != key:
                self._db_schema_notice_key = key
                if has_book_meta:
                    self._log("BD conectada: Modulo 7 usara filtros desde 'problemas' y metadata de 'libros_escaneo'.")
                else:
                    self._log("BD conectada: Modulo 7 usara filtros directos desde la tabla 'problemas'.")
            return
        key = (db_name, "count_only")
        if self._db_schema_notice_key != key:
            self._db_schema_notice_key = key
            self._log("BD conectada: no hay etiquetas filtrables; Modulo 7 usara solo el total de 'problemas'.")

    def _default_output_name(self) -> str:
        if self.source_mode_var.get() == "db":
            base = (self.titulo_var.get() or "").strip() or "practica"
            safe = "".join(ch if ch.isalnum() else "_" for ch in base).strip("_") or "practica"
            return f"{safe}.docx"
        if self.source_mode_var.get() == "session":
            session_path = (self.session_path_var.get() or "").strip()
            if session_path:
                return str(Path(session_path).with_suffix(".docx").name)
            return "sesion.docx"
        return "salida.docx"

    def _normalize_output_docx_path(self, raw_value: str) -> Path:
        value = str(raw_value or "").strip()
        path = Path(value).expanduser() if value else (Path.cwd() / self._default_output_name())
        if not path.is_absolute():
            path = Path.cwd() / path
        path = self._normalize_path_lexically(path)
        name = path.name.rstrip(" .")
        if not name:
            name = "salida.docx"
        if path.suffix:
            path = path.with_suffix(".docx")
        elif not name.lower().endswith(".docx"):
            path = path.with_name(f"{name}.docx")
        else:
            path = path.with_name(name)
        return self._normalize_path_lexically(path)

    def _normalize_input_tex_path(self, raw_value: str) -> Path:
        value = str(raw_value or "").strip()
        if not value:
            return Path("")
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return self._normalize_path_lexically(path)

    def _read_text_any_encoding(self, path: Path) -> str:
        last_error: Exception | None = None
        for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
            try:
                return path.read_text(encoding=enc)
            except UnicodeDecodeError as exc:
                last_error = exc
                continue
        if last_error is not None:
            return path.read_text(encoding="latin-1", errors="replace")
        return path.read_text(encoding="utf-8")

    def _extract_answer_key_map_from_tex(self, tex_text: str) -> dict[int, str]:
        text = str(tex_text or "")
        match = ANSWER_KEY_SECTION_RE.search(text)
        if not match:
            return {}
        tail = text[match.end():]
        key_map: dict[int, str] = {}
        for number_text, key_text in ANSWER_KEY_ENTRY_RE.findall(tail):
            try:
                item_number = int(number_text)
            except Exception:
                continue
            if item_number <= 0:
                continue
            key = str(key_text or "").strip().upper()
            if not key:
                continue
            key_map[item_number] = key
        return key_map

    def _strip_answer_key_section(self, tex_text: str) -> tuple[str, bool]:
        text = str(tex_text or "")
        match = ANSWER_KEY_SECTION_RE.search(text)
        if not match:
            return text, False
        stripped = text[:match.start()].rstrip()
        if "\\end{document}" in text[match.end():]:
            end_document_idx = text.rfind("\\end{document}")
            if end_document_idx >= 0:
                suffix = text[end_document_idx:]
                return (stripped + "\n\n" + suffix.lstrip()).strip() + "\n", True
        return stripped + "\n", True

    def _inject_answer_keys_into_tex_items(self, tex_text: str, answer_key_map: dict[int, str]) -> tuple[str, int]:
        if not answer_key_map:
            return str(tex_text or ""), 0

        out_parts: list[str] = []
        last_end = 0
        injected = 0
        for match in TEX_ITEM_BLOCK_RE.finditer(str(tex_text or "")):
            out_parts.append(str(tex_text or "")[last_end:match.start()])
            block = match.group(1) or ""
            try:
                item_number = int(match.group(2) or 0)
            except Exception:
                item_number = 0
            key = answer_key_map.get(item_number)
            if key and not CLAVE_TAG_RE.search(block):
                block = block.rstrip() + f" [[clave={key}]]"
                injected += 1
            out_parts.append(block)
            last_end = match.end()
        out_parts.append(str(tex_text or "")[last_end:])
        return "".join(out_parts), injected

    def _prepare_tex_mode_input(self, *, input_tex: Path, output_docx: Path) -> Path:
        raw_tex = self._read_text_any_encoding(input_tex)
        answer_key_map = self._extract_answer_key_map_from_tex(raw_tex)
        has_inline_keys = bool(CLAVE_TAG_RE.search(raw_tex))
        stripped_tex, stripped = self._strip_answer_key_section(raw_tex) if answer_key_map else (raw_tex, False)
        prepared_tex, injected = self._inject_answer_keys_into_tex_items(stripped_tex, answer_key_map)

        if not stripped and injected == 0:
            if not has_inline_keys:
                self._log("Archivo .tex: no se detecto bloque final de claves ni etiquetas [[clave=...]].")
            return input_tex

        target = output_docx.with_suffix(".tex")
        target = target.with_name(f"{target.stem}__tex_source.tex")
        target.write_text(prepared_tex if prepared_tex.endswith("\n") else prepared_tex + "\n", encoding="utf-8")
        self._generated_tex_path = target
        self._log(
            f"Archivo .tex preparado para Word: {target} | claves_detectadas={len(answer_key_map)} | "
            f"claves_inyectadas={injected} | bloque_final_removido={'si' if stripped else 'no'}"
        )
        return target

    def _load_session_payload(self, session_path: Path) -> dict:
        try:
            return json.loads(session_path.read_text(encoding="utf-8"))
        except UnicodeError:
            return json.loads(session_path.read_text(encoding="utf-8-sig"))

    def _normalize_path_lexically(self, path: Path) -> Path:
        try:
            normalized = Path(os.path.normpath(str(path)))
        except Exception:
            normalized = path
        try:
            return remap_legacy_drive_path(normalized, prefer_existing=True)
        except Exception:
            return normalized

    def _infer_images_dir_from_session(
        self,
        session_path: Path,
        *,
        payload: dict,
        instance_type: str = "",
    ) -> Path | None:
        def _resolve_candidate(raw_value: str) -> Path | None:
            value = str(raw_value or "").strip()
            if not value:
                return None
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = session_path.parent / candidate
            return self._normalize_path_lexically(candidate)

        session_bundle = payload.get("session_bundle", {}) if isinstance(payload, dict) else {}
        if isinstance(session_bundle, dict):
            bundle_crops = _resolve_candidate(str(session_bundle.get("crops_dir", "") or "").strip())
            if bundle_crops is not None:
                return bundle_crops

        segmentation = payload.get("segmentation", {}) if isinstance(payload, dict) else {}
        if isinstance(segmentation, dict):
            raw_crops_dir = _resolve_candidate(str(segmentation.get("crops_dir", "") or "").strip())
            if raw_crops_dir is not None:
                return raw_crops_dir

        project_root = infer_workspace_from_session_path(session_path)
        if project_root is None:
            return None
        normalized_instance = normalize_instance_name(instance_type or session_path.stem, "sesion")
        return project_dirs(project_root, normalized_instance)["crops_dir"]

    def _resolve_session_resource_path(self, session_path: Path, raw_path: str) -> Path:
        value = str(raw_path or "").strip()
        if not value:
            return Path(value)
        candidate = Path(value)
        if candidate.is_absolute():
            return self._normalize_path_lexically(candidate)
        candidates: list[Path] = []
        candidates.append(self._normalize_path_lexically(session_path.parent / candidate))

        project_root = infer_workspace_from_session_path(session_path)
        if project_root is not None:
            normalized = value.replace("\\", "/")
            if normalized.startswith("./"):
                normalized = normalized[2:]
            candidates.append(self._normalize_path_lexically(project_root / normalized))
            candidates.append(self._normalize_path_lexically(project_root.parent / normalized))

            instance_hint = normalize_instance_name(session_path.stem, "sesion")
            if "resuelt" in session_path.stem.lower():
                instance_hint = "resueltos"
            elif "propuest" in session_path.stem.lower():
                instance_hint = "propuestos"
            dirs = project_dirs(project_root, instance_hint)
            file_name = candidate.name.strip()
            if file_name:
                candidates.extend(
                    [
                        self._normalize_path_lexically(dirs["crops_dir"] / file_name),
                        self._normalize_path_lexically(dirs["segments_dir"] / file_name),
                        self._normalize_path_lexically(dirs["sources_dir"] / file_name),
                    ]
                )
                try:
                    found_crop = next(dirs["crops_dir"].rglob(file_name), None)
                except Exception:
                    found_crop = None
                if found_crop is not None:
                    candidates.append(found_crop)
                try:
                    found_segment = next(dirs["segments_dir"].rglob(file_name), None)
                except Exception:
                    found_segment = None
                if found_segment is not None:
                    candidates.append(found_segment)

        for resolved in candidates:
            try:
                if resolved.exists():
                    return self._normalize_path_lexically(resolved)
            except Exception:
                continue
        if candidates:
            return self._normalize_path_lexically(candidates[0])
        return self._normalize_path_lexically(session_path.parent / candidate)

    def _extract_session_item_markers(self, item_text: str) -> list[str]:
        return [str(m.group(1) or "").strip() for m in IMAGE_MARKER_RE.finditer(str(item_text or "")) if str(m.group(1) or "").strip()]

    def _collect_session_marker_names(self, payload: dict) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        preview_map = payload.get("preview_images", {}) if isinstance(payload, dict) else {}
        if isinstance(preview_map, dict):
            for marker_name in preview_map.keys():
                mk = str(marker_name or "").strip()
                if mk and mk not in seen:
                    seen.add(mk)
                    names.append(mk)
        items = payload.get("items", []) if isinstance(payload, dict) else []
        if isinstance(items, list):
            for row in items:
                if not isinstance(row, dict):
                    continue
                for mk in self._extract_session_item_markers(str(row.get("item", "") or "")):
                    if mk and mk not in seen:
                        seen.add(mk)
                        names.append(mk)
        return names

    def _marker_output_exists(self, images_dir: Path, marker_name: str) -> bool:
        marker = str(marker_name or "").strip()
        if not marker:
            return False
        for suffix in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            candidate = images_dir / f"{marker}{suffix}"
            try:
                if candidate.exists() and candidate.is_file():
                    return True
            except Exception:
                continue
        return False

    def _session_images_dir_complete(self, *, payload: dict, images_dir: Path) -> bool:
        marker_names = self._collect_session_marker_names(payload)
        if not marker_names:
            return True
        if not images_dir.exists() or not images_dir.is_dir():
            return False
        for marker_name in marker_names:
            if not self._marker_output_exists(images_dir, marker_name):
                return False
        return True

    def _infer_session_segments_dir(self, session_path: Path, *, payload: dict, instance_type: str) -> Path | None:
        def _resolve_candidate(raw_value: str) -> Path | None:
            value = str(raw_value or "").strip()
            if not value:
                return None
            candidate = self._resolve_session_resource_path(session_path, value)
            return candidate

        session_bundle = payload.get("session_bundle", {}) if isinstance(payload, dict) else {}
        if isinstance(session_bundle, dict):
            candidate = _resolve_candidate(str(session_bundle.get("segments_dir", "") or "").strip())
            if candidate is not None and candidate.exists():
                return candidate

        segmentation = payload.get("segmentation", {}) if isinstance(payload, dict) else {}
        if isinstance(segmentation, dict):
            candidate = _resolve_candidate(str(segmentation.get("segments_dir", "") or "").strip())
            if candidate is not None and candidate.exists():
                return candidate

        project_root = infer_workspace_from_session_path(session_path)
        if project_root is None:
            return None
        normalized_instance = normalize_instance_name(instance_type or session_path.stem, "sesion")
        candidate = project_dirs(project_root, normalized_instance)["segments_dir"]
        if candidate.exists():
            return candidate
        return candidate

    def _resolve_binding_source_path(self, session_path: Path, *, payload: dict, source_key: str) -> Path | None:
        raw = str(source_key or "").strip()
        if not raw:
            return None
        resolved = self._resolve_session_resource_path(session_path, raw)
        if resolved.exists():
            return resolved

        wanted_stem = Path(raw).stem.strip().lower()
        if not wanted_stem:
            return None
        files_data = payload.get("files", []) if isinstance(payload, dict) else []
        if not isinstance(files_data, list):
            return None
        for row in files_data:
            if not isinstance(row, dict):
                continue
            file_raw = str(row.get("path", "") or "").strip()
            if not file_raw:
                continue
            candidate = self._resolve_session_resource_path(session_path, file_raw)
            current_stem = candidate.stem.strip().lower()
            if not current_stem:
                continue
            if current_stem == wanted_stem or current_stem.startswith(wanted_stem) or wanted_stem.startswith(current_stem):
                if candidate.exists():
                    return candidate
        return None

    def _find_segment_image_for_binding(
        self,
        *,
        segments_dir: Path,
        source_path: Path,
        segment_idx: int,
    ) -> Path | None:
        source_stem = source_path.stem.strip().lower()
        candidates: list[Path] = []
        try:
            direct = segments_dir / source_path.stem
            if direct.exists() and direct.is_dir():
                candidates.append(direct)
            for folder in segments_dir.iterdir():
                if not folder.is_dir():
                    continue
                folder_stem = folder.name.strip().lower()
                if folder_stem == source_stem or folder_stem.startswith(source_stem) or source_stem.startswith(folder_stem):
                    candidates.append(folder)
        except Exception:
            return None

        seen: set[str] = set()
        for folder in candidates:
            key = str(folder).lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                images = sorted(
                    p for p in folder.iterdir()
                    if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
                )
            except Exception:
                continue
            if 0 <= int(segment_idx) < len(images):
                return images[int(segment_idx)]
        return None

    def _materialize_session_marker_images(
        self,
        *,
        session_path: Path,
        payload: dict,
        images_dir: Path,
        instance_type: str,
    ) -> int:
        images_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        resolved_cache: dict[str, Path] = {}
        missing_logged: set[tuple[str, str]] = set()

        def _copy_marker(marker_name: str, raw_path: str) -> None:
            nonlocal copied
            marker = str(marker_name or "").strip()
            if not marker:
                return
            if self._marker_output_exists(images_dir, marker):
                return
            raw = str(raw_path or "").strip()
            if not raw:
                return
            source = resolved_cache.get(raw)
            if source is None:
                source = self._resolve_session_resource_path(session_path, raw)
                resolved_cache[raw] = source
            if not source.exists() or not source.is_file():
                miss_key = (marker, raw)
                if miss_key not in missing_logged:
                    missing_logged.add(miss_key)
                    self._log(f"Sesion: no se encontro imagen fuente para {marker} -> {raw}")
                return
            suffix = source.suffix or ".png"
            target = images_dir / f"{marker}{suffix}"
            try:
                if source.resolve() == target.resolve():
                    return
            except Exception:
                pass
            try:
                shutil.copy2(str(source), str(target))
                copied += 1
            except Exception:
                return

        preview_map = payload.get("preview_images", {}) if isinstance(payload, dict) else {}
        if isinstance(preview_map, dict):
            for marker_name, raw_path in preview_map.items():
                _copy_marker(str(marker_name or "").strip(), str(raw_path or "").strip())

        items = payload.get("items", []) if isinstance(payload, dict) else []
        if isinstance(items, list):
            for row in items:
                if not isinstance(row, dict):
                    continue
                markers = self._extract_session_item_markers(str(row.get("item", "") or ""))
                if not markers:
                    continue
                imgs = row.get("imagenes", [])
                if not isinstance(imgs, list):
                    continue
                for marker_name, img_path in zip(markers, imgs):
                    _copy_marker(marker_name, str(img_path or "").strip())

        segments_dir = self._infer_session_segments_dir(session_path, payload=payload, instance_type=instance_type)
        bindings = payload.get("segment_item_bindings_by_source", {}) if isinstance(payload, dict) else {}
        if isinstance(bindings, dict) and isinstance(segments_dir, Path) and segments_dir.exists():
            for source_key, bucket in bindings.items():
                if not isinstance(bucket, dict):
                    continue
                source_path = self._resolve_binding_source_path(session_path, payload=payload, source_key=str(source_key or ""))
                if source_path is None:
                    continue
                for seg_key, raw_payload in bucket.items():
                    if not isinstance(raw_payload, dict):
                        continue
                    marker_name = str(raw_payload.get("marker_name", "") or "").strip()
                    if not marker_name:
                        continue
                    try:
                        seg_idx = int(seg_key)
                    except Exception:
                        continue
                    preferred = images_dir / f"{marker_name}.png"
                    if preferred.exists():
                        continue
                    seg_image = self._find_segment_image_for_binding(
                        segments_dir=segments_dir,
                        source_path=source_path,
                        segment_idx=seg_idx,
                    )
                    if seg_image is None:
                        continue
                    _copy_marker(marker_name, str(seg_image))
        if copied > 0:
            self._log(f"Sesion: se materializaron {copied} imagen(es) canonicas de marcador en {images_dir}")
        return copied

    def _write_scan_source_tex(self, *, output_docx: Path, suffix: str, source_text: str) -> Path:
        generated = output_docx.with_suffix(".tex")
        generated = generated.with_name(f"{generated.stem}{suffix}.tex")
        content = self._ensure_enumerate_wrapper(str(source_text or "").strip())
        generated.write_text(content + "\n", encoding="utf-8")
        self._generated_tex_path = generated
        return generated

    def _ensure_enumerate_wrapper(self, source_text: str) -> str:
        text = str(source_text or "").strip()
        if not text:
            return "\\begin{enumerate}\n\\end{enumerate}"
        low = text.lower()
        if "\\begin{enumerate}" in low and "\\end{enumerate}" in low:
            return text
        return "\\begin{enumerate}\n" + text + "\n\\end{enumerate}"

    def _build_scan_source_text_from_session(self, payload: dict) -> str:
        output_text = str(payload.get("output_text", "") or "").strip() if isinstance(payload, dict) else ""
        if output_text:
            return output_text

        blocks: list[str] = []
        items_data = payload.get("items", []) if isinstance(payload, dict) else []
        if isinstance(items_data, list):
            for row in items_data:
                if not isinstance(row, dict):
                    continue
                item_text = ""
                for key in ("item", "item_text", "text", "latex", "enunciado_latex"):
                    candidate = str(row.get(key, "") or "").strip()
                    if candidate:
                        item_text = candidate
                        break
                if item_text:
                    blocks.append(item_text)
        if blocks:
            return "\n".join(blocks).strip()
        return ""

    def _practice_structure_text(self) -> str:
        text_widget = self.txt_practice_structure
        if text_widget is None:
            return ""
        try:
            return text_widget.get("1.0", "end").strip()
        except Exception:
            return ""

    def _latex_escape_heading(self, value: str) -> str:
        text = str(value or "").strip()
        replacements = {
            "\\": r"\textbackslash{}",
            "&": r"\&",
            "%": r"\%",
            "$": r"\$",
            "#": r"\#",
            "_": r"\_",
            "{": r"\{",
            "}": r"\}",
        }
        return "".join(replacements.get(ch, ch) for ch in text)

    def _parse_practice_ranges(self, value: str) -> set[int]:
        numbers: set[int] = set()
        for part in re.split(r"[,;]", str(value or "")):
            chunk = part.strip()
            if not chunk:
                continue
            match_range = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", chunk)
            if match_range:
                start = int(match_range.group(1))
                end = int(match_range.group(2))
                if start > end:
                    start, end = end, start
                numbers.update(range(start, end + 1))
                continue
            match_single = re.match(r"^(\d+)$", chunk)
            if match_single:
                numbers.add(int(match_single.group(1)))
        return numbers

    def _parse_practice_structure(self) -> tuple[str, dict[int, list[str]]]:
        title = ""
        subtitles_by_number: dict[int, list[str]] = {}
        for raw_line in self._practice_structure_text().splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("##"):
                content = line[2:].strip()
                subtitle, sep, ranges = content.partition(":")
                subtitle = subtitle.strip()
                if not sep or not subtitle:
                    continue
                numbers = self._parse_practice_ranges(ranges)
                if not numbers:
                    continue
                for number in numbers:
                    subtitles_by_number.setdefault(number, []).append(subtitle)
            elif line.startswith("#"):
                candidate = line[1:].strip()
                if candidate:
                    title = candidate
        return title, subtitles_by_number

    def _extract_tex_item_number(self, item_text: str, fallback: int) -> int:
        match = re.search(r"\\item\s*\[\s*\\textbf\{\s*(\d+)\.?\s*\}", str(item_text or ""))
        if match:
            try:
                return int(match.group(1))
            except Exception:
                pass
        return int(fallback)

    def _apply_practice_structure_to_blocks(self, blocks: list[tuple[int, str]]) -> list[str]:
        title, subtitles_by_number = self._parse_practice_structure()
        structured: list[str] = []
        if title:
            structured.append(rf"\section*{{{self._latex_escape_heading(title)}}}")
        emitted_subtitles: set[tuple[int, str]] = set()
        for number, block in blocks:
            for subtitle in subtitles_by_number.get(number, []):
                key = (number, subtitle)
                if key in emitted_subtitles:
                    continue
                structured.append(rf"\subsection*{{{self._latex_escape_heading(subtitle)}}}")
                emitted_subtitles.add(key)
            structured.append(block)
        return structured

    def _build_scan_source_text_from_db(self, problemas: list[dict[str, object]]) -> str:
        blocks: list[tuple[int, str]] = []
        for idx, problema in enumerate(problemas, start=1):
            raw = self._build_db_preview_reconstructed_text(problema).strip()
            if not raw:
                continue
            clave = str(problema.get("respuesta_correcta") or "").strip().upper()
            if clave and not re.search(r"\[\[\s*clave\s*=", raw, flags=re.IGNORECASE):
                raw = f"{raw} [[clave={clave}]]"
            estado_bruto = str(problema.get("consistencia_matematica") or "").strip()
            estado_tag = self._normalize_state_tag(estado_bruto)
            if estado_tag and not ESTADO_TAG_RE.search(raw):
                raw = f"{raw} [[Estado={estado_tag}]]"
            blocks.append((self._extract_tex_item_number(raw, idx), raw))
        return "\n".join(self._apply_practice_structure_to_blocks(blocks)).strip()

    def _prepare_images_dir_for_db(self, problemas: list[dict[str, object]], *, output_docx: Path) -> Path | None:
        if not problemas:
            return None
        images_dir = output_docx.with_name(f"{output_docx.stem}__db_images")
        images_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        missing_markers: set[str] = set()
        for problema in problemas:
            marker_names = self._resolve_db_preview_problem_markers(problema)
            problem_number = int(problema.get("numero_original") or 0)
            if marker_names:
                self._log(
                    f"BD: problema {problem_number or problema.get('id')} -> origenes de imagen: "
                    f"{self._describe_db_problem_image_sources(problema)}"
                )
            for marker_name in marker_names:
                marker = str(marker_name or "").strip()
                if not marker or self._marker_output_exists(images_dir, marker):
                    continue
                resolved_paths = self._resolve_db_preview_marker_paths(marker, problema)
                if not resolved_paths:
                    missing_markers.add(marker)
                    self._log(
                        f"BD: no se resolvio {marker} para problema {problem_number or problema.get('id')} "
                        f"usando {self._describe_db_problem_image_sources(problema)}"
                    )
                    continue
                source = resolved_paths[0]
                suffix = source.suffix or ".png"
                target = images_dir / f"{marker}{suffix}"
                try:
                    if source.resolve() == target.resolve():
                        copied += 1
                        continue
                except Exception:
                    pass
                try:
                    shutil.copy2(str(source), str(target))
                    copied += 1
                except Exception:
                    missing_markers.add(marker)
        if copied > 0:
            self._log(f"BD: se materializaron {copied} imagen(es) canonicas en {images_dir}")
            if missing_markers:
                self._log(f"BD: algunas etiquetas no pudieron resolverse: {', '.join(sorted(missing_markers))}")
            self.images_dir_var.set(str(images_dir))
            return images_dir
        if any(
            self._marker_output_exists(images_dir, str(marker or "").strip())
            for problema in problemas
            for marker in self._resolve_db_preview_problem_markers(problema)
        ):
            self.images_dir_var.set(str(images_dir))
            return images_dir
        return None

    def _normalize_state_tag(self, value: str) -> str:
        state = str(value or "").strip().lower()
        if not state:
            return "sin_revisar"
        if state in {"consistente", "bien planteado", "bien_planteado"}:
            return "consistente"
        if state in {"inconsistente", "mal planteado", "mal_planteado", "ambiguo", "ambigua"}:
            return "inconsistente"
        if state in {"sin revisar", "sin_revisar", "pendiente revision", "pendiente revisión"}:
            return "sin_revisar"
        return state.replace(" ", "_")

    def _prepare_session_images_dir_for_conversion(self) -> Path | None:
        session_path = self._selected_session_path()
        if session_path is None:
            return None
        return self._prepare_images_dir_for_session(session_path)

    def _session_conversion_images_dir(self, *, session_path: Path, output_docx: Path | None) -> Path | None:
        if output_docx is not None:
            return output_docx.with_name(f"{output_docx.stem}__session_images")
        return None

    def _prepare_images_dir_for_session(
        self,
        session_path: Path,
        *,
        update_ui: bool = True,
        output_docx: Path | None = None,
    ) -> Path | None:
        if not session_path.exists():
            return None
        payload = self._load_session_payload(session_path)
        ui = payload.get("ui", {}) if isinstance(payload, dict) else {}
        instance_type = str(
            ui.get("instance_type", payload.get("instance_type", "") if isinstance(payload, dict) else "") or ""
        ).strip().lower()
        images_dir = self._session_conversion_images_dir(session_path=session_path, output_docx=output_docx)
        if images_dir is None:
            images_dir = self._infer_images_dir_from_session(session_path, payload=payload, instance_type=instance_type)
        if images_dir is None:
            return None
        marker_count = len(self._collect_session_marker_names(payload))
        cache_key = (str(session_path), str(images_dir), int(marker_count))
        if self._prepared_session_images_key == cache_key and self._session_images_dir_complete(payload=payload, images_dir=images_dir):
            if update_ui:
                self.images_dir_var.set(str(images_dir))
            return images_dir
        if self._session_images_dir_complete(payload=payload, images_dir=images_dir):
            self._prepared_session_images_key = cache_key
            if update_ui:
                self.images_dir_var.set(str(images_dir))
            return images_dir
        self._materialize_session_marker_images(
            session_path=session_path,
            payload=payload,
            images_dir=images_dir,
            instance_type=instance_type,
        )
        self._prepared_session_images_key = cache_key
        if update_ui:
            self.images_dir_var.set(str(images_dir))
        return images_dir

    def _resolve_session_input_tex(self, *, session_path: Path, output_docx: Path, emit_log: bool = True) -> Path:
        if not session_path.exists():
            raise RuntimeError(f"El archivo de sesion no existe:\n{session_path}")
        payload = self._load_session_payload(session_path)
        source_text = self._build_scan_source_text_from_session(payload)
        if not source_text:
            raise RuntimeError(f"La sesion no contiene items exportables:\n{session_path}")
        generated = self._write_scan_source_tex(
            output_docx=output_docx,
            suffix="__session_source",
            source_text=source_text,
        )
        if emit_log:
            self._log(
                f"Fuente sesion -> .tex generado sin alterar estructura: {generated} | sesion={session_path}"
            )
        return generated

    def _resolve_input_tex(self, *, output_docx: Path) -> Path:
        if self.source_mode_var.get() == "session":
            session_path = self._selected_session_path()
            if session_path is None:
                raise RuntimeError("Selecciona un archivo de sesion.")
            return self._resolve_session_input_tex(session_path=session_path, output_docx=output_docx)

        db = (self.db_name_var.get() or "").strip()
        if not db:
            raise RuntimeError("Selecciona una fuente del seleccionador de problemas.")
        filters = self._current_db_filters()
        manual_selection_active = bool(self._db_selected_problem_ids)
        selected_preview = self._collect_db_selected_items(output_docx=output_docx) if manual_selection_active else []
        using_preview_selection = manual_selection_active
        if manual_selection_active:
            if not selected_preview:
                raise RuntimeError("La seleccion acumulada no pudo reconstruirse desde el seleccionador.")
            problemas = selected_preview
        else:
            total = self.practice_controller.contar_problemas(
                db,
                curso=str(filters.get("curso") or ""),
                tema_id=filters.get("tema_id"),
                subtema_id=filters.get("subtema_id"),
                autor=str(filters.get("autor") or ""),
                editorial=str(filters.get("editorial") or ""),
                estado=str(filters.get("estado") or "Todos"),
                clave=str(filters.get("clave") or "Todos"),
            )
            if total <= 0:
                raise RuntimeError("No se encontraron problemas para el filtro seleccionado.")
            problemas = self.practice_controller.obtener_problemas(
                db,
                cantidad=total,
                curso=str(filters.get("curso") or ""),
                tema_id=filters.get("tema_id"),
                subtema_id=filters.get("subtema_id"),
                autor=str(filters.get("autor") or ""),
                editorial=str(filters.get("editorial") or ""),
                estado=str(filters.get("estado") or "Todos"),
                clave=str(filters.get("clave") or "Todos"),
                aleatorio=bool(self.aleatorio_var.get()),
            )
        if not problemas:
            raise RuntimeError("No se encontraron problemas para el filtro seleccionado.")
        if using_preview_selection and not self._selection_json_has_editable_items(self._db_selection_output_path(output_docx=output_docx)):
            self._save_db_selected_problem_ids(output_docx=output_docx)
        self._prepared_db_images_dir = self._prepare_images_dir_for_db(problemas, output_docx=output_docx)
        source_text = self._build_scan_source_text_from_db(problemas)
        if not source_text:
            raise RuntimeError("Los problemas seleccionados no tienen enunciado_latex utilizable.")
        generated = self._write_scan_source_tex(
            output_docx=output_docx,
            suffix="__db_source",
            source_text=source_text,
        )
        curso = str(filters.get("curso") or "")
        tema_label = (self.tema_var.get() or "").strip()
        subtema_label = (self.subtema_var.get() or "").strip()
        self._log(
            f"Fuente BD -> .tex generado sin alterar estructura: {generated} | db={db} | problemas={len(problemas)} | "
            f"curso={curso or 'Todos'} | tema={tema_label or 'Todos'} | subtema={subtema_label or 'Todos'} | "
            f"autor={str(filters.get('autor') or 'Todos') or 'Todos'} | editorial={str(filters.get('editorial') or 'Todos') or 'Todos'} | "
            f"clave={str(filters.get('clave') or 'Todos') or 'Todos'} | "
            f"seleccion_manual={'si' if using_preview_selection else 'no'}"
        )
        return generated

    def _extract_generated_docx_from_stdout(self, stdout_text: str) -> Path | None:
        text = str(stdout_text or "")
        if not text.strip():
            return None
        for raw_line in reversed(text.splitlines()):
            line = str(raw_line or "").strip()
            if not line:
                continue
            if "Word generado en:" not in line:
                continue
            _, _, candidate = line.partition("Word generado en:")
            candidate_path = candidate.strip().strip('"')
            if not candidate_path:
                continue
            path = self._normalize_path_lexically(Path(candidate_path))
            if path.exists():
                return path
        return None

    def _open_external_gui(self) -> None:
        repo = Path(self.repo_var.get().strip())
        py, errors = self._resolve_python(str(repo), self.python_var.get().strip())
        self.python_var.set(py)
        script = repo / "latex_to_word_gui.py"
        if not script.exists():
            messagebox.showerror("Modulo 7", f"No existe script:\n{script}")
            return
        if errors:
            self._log("Aviso: algunos python candidatos fallaron. Se usa fallback valido.")
            for err in errors[:4]:
                self._log(f" - {err}")
        self._log(f"Abriendo GUI externa: {script}")
        try:
            subprocess.Popen([str(py), str(script)], cwd=str(repo))
            self.status_var.set("GUI externa iniciada")
        except Exception as exc:
            messagebox.showerror("Modulo 7", str(exc))

    def _prepare_source_tex_for_conversion(self, *, output_docx: Path) -> tuple[Path, Path | None, str]:
        mode = self.source_mode_var.get()
        if mode == "session":
            session_path = self._selected_session_path()
            if session_path is None:
                raise RuntimeError("Selecciona un archivo de sesion.")
            self.after(
                0,
                lambda name=session_path.name: self.status_var.set(f"Convirtiendo sesion: {name}"),
            )
            self.after(
                0,
                lambda path=session_path, out=output_docx: self._log(f"Sesion seleccionada: {path} -> {out}"),
            )
            input_tex = self._resolve_session_input_tex(
                session_path=session_path,
                output_docx=output_docx,
                emit_log=False,
            )
            self.after(
                0,
                lambda generated=input_tex, sess=session_path: self._log(
                    f"Fuente sesion -> .tex generado sin alterar estructura: {generated} | sesion={sess}"
                ),
            )
            images_dir: Path | None = None
            try:
                prepared_images_dir = self._prepare_images_dir_for_session(
                    session_path,
                    update_ui=False,
                    output_docx=output_docx,
                )
                if prepared_images_dir is not None:
                    images_dir = prepared_images_dir
                    self.after(
                        0,
                        lambda d=images_dir: self._log(
                            f"Conversion sesion: carpeta canonica de marcadores preparada -> {d}"
                        ),
                    )
            except Exception as exc:
                self.after(0, lambda e=exc: self._log(f"Aviso preparando imagenes de sesion para conversion: {e}"))
            return input_tex, images_dir, "sesion"

        if mode != "db":
            self.source_mode_var.set("db")
            mode = "db"

        self._prepared_db_images_dir = None
        input_tex = self._resolve_input_tex(output_docx=output_docx)
        self.after(
            0,
            lambda inp=input_tex, out=output_docx: self._log(
                f"Seleccionador de problemas -> .tex generado: {inp} -> {out}"
            ),
        )
        images_dir = self._prepared_db_images_dir if isinstance(self._prepared_db_images_dir, Path) else None
        if images_dir is None:
            images_dir = Path(self.images_dir_var.get().strip()) if self.images_dir_var.get().strip() else None
        return input_tex, images_dir, mode

    def _run_tex_to_word_conversion(
        self,
        *,
        repo: Path,
        py: str,
        script: Path,
        input_tex: Path,
        output_docx: Path,
        template: Path | None,
        images_dir: Path | None,
        style: str,
    ) -> tuple[bool, list[Path]]:
        cmd = [str(py), str(script), str(input_tex), str(output_docx), "--style", style]
        if template and template.exists():
            cmd.extend(["--template", str(template)])
        if images_dir and images_dir.exists():
            cmd.extend(["--images-dir", str(images_dir)])
            self.after(
                0,
                lambda d=images_dir: self._log(f"Conversion: reemplazo de imagenes activado con carpeta -> {d}"),
            )
        else:
            self.after(
                0,
                lambda: self._log(
                    "Conversion: sin carpeta de imagenes valida; no se reemplazaran marcadores [[Imagen=...]]."
                ),
            )
        self.after(0, lambda c=subprocess.list2cmdline(cmd): self._log("Comando:\n" + c))
        proc = subprocess.run(
            cmd,
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.stdout.strip():
            self.after(0, lambda text=proc.stdout: self._log(text))
        if proc.stderr.strip():
            self.after(0, lambda text=proc.stderr: self._log(text))
        generated_docx = self._extract_generated_docx_from_stdout(proc.stdout)
        ok = proc.returncode == 0 and ((generated_docx is not None) or output_docx.exists())
        return ok, [generated_docx or output_docx] if ok else []

    def _convert_async(self) -> None:
        if self._running:
            messagebox.showwarning("Modulo 7", "Ya hay una conversion en curso.")
            return
        repo = Path(self.repo_var.get().strip())
        py, errors = self._resolve_python(str(repo), self.python_var.get().strip())
        self.python_var.set(py)
        script = repo / "latex_to_word.py"
        output_docx_raw = self.output_docx_var.get().strip()
        if not output_docx_raw:
            self._pick_output_docx()
            output_docx_raw = self.output_docx_var.get().strip()
        if not output_docx_raw:
            return
        template = Path(self.template_var.get().strip()) if self.template_var.get().strip() else None
        style = (self.style_var.get() or "").strip() or "Estilo_plantilla"

        if not script.exists():
            messagebox.showerror("Modulo 7", f"No existe script:\n{script}")
            return

        self._running = True
        self.status_var.set("Convirtiendo...")
        if errors:
            self._log("Aviso: algunos python candidatos fallaron. Se usa fallback valido.")
            for err in errors[:4]:
                self._log(f" - {err}")

        def worker() -> None:
            ok = False
            produced: list[Path] = []
            try:
                output_docx = self._normalize_output_docx_path(output_docx_raw)
                self.after(0, lambda path=str(output_docx): self.output_docx_var.set(path))
                if not output_docx.parent.exists():
                    output_docx.parent.mkdir(parents=True, exist_ok=True)
                input_tex, images_dir, _source_label = self._prepare_source_tex_for_conversion(output_docx=output_docx)
                ok, produced = self._run_tex_to_word_conversion(
                    repo=repo,
                    py=py,
                    script=script,
                    input_tex=input_tex,
                    output_docx=output_docx,
                    template=template,
                    images_dir=images_dir,
                    style=style,
                )
                if not ok and self.source_mode_var.get() == "session":
                    self.after(0, lambda: self._log("Conversion sesion: reintentando tras enfriar Word COM..."))
                    time.sleep(5.0)
                    ok, produced = self._run_tex_to_word_conversion(
                        repo=repo,
                        py=py,
                        script=script,
                        input_tex=input_tex,
                        output_docx=output_docx,
                        template=template,
                        images_dir=images_dir,
                        style=style,
                    )
            except Exception as exc:
                self.after(0, lambda: self._log(f"Error ejecutando conversion: {exc}"))

            def done() -> None:
                self._running = False
                if ok:
                    self.status_var.set("Conversion completada")
                    if self.source_mode_var.get() == "session":
                        self._refresh_session_tree_word_states()
                    if produced:
                        messagebox.showinfo("Modulo 7", f"Word generado:\n{produced[0]}")
                    else:
                        messagebox.showinfo("Modulo 7", "Conversion completada.")
                else:
                    self.status_var.set("Conversion con error")
                    messagebox.showerror("Modulo 7", "No se pudo generar el Word. Revisa el log.")

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()
