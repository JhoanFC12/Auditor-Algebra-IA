import re
import shutil
import threading
import tkinter as tk
import unicodedata
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from database.connection import DatabaseManager
from database.problem_origins import TAG_EXAMEN_RE, ensure_problem_origin_schema, upsert_exam_origin_for_problem
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

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".svg")
IMG_MARK_LEGACY_RE = re.compile(r"\[\[([^\]\r\n]+?)\]\]")
IMG_MARK_TAG_RE = re.compile(r"\[\[\s*Imagen\s*=\s*([^\]\r\n]+?)\s*\]\]", re.IGNORECASE)
IMG_MARK_VALUE_RE = re.compile(r"^(.*)([-_])\s*(\d+)$")
IMG_MARK_VALUE_OPT_RE = re.compile(r"^(.*)([-_])\s*(\d+)\s*([-_])\s*([A-Ea-e])$")
IMG_MARK_FILE_RE = re.compile(
    r"\[\[([A-Za-z0-9][A-Za-z0-9._-]*\.(?:png|jpg|jpeg|webp|bmp|svg))\]\]",
    re.IGNORECASE,
)
ITEM_HEADER_RE = re.compile(r"(\\item\s*\[\s*\\textbf\s*\{\s*\d+\s*\.\s*\}\s*\])", re.IGNORECASE)
TAG_CURSO_RE = re.compile(r"\[\[\s*curso\s*=\s*(.*?)\s*\]\]", re.IGNORECASE)
TAG_TEMA_RE = re.compile(r"\[\[\s*tema\s*=\s*(.*?)\s*\]\]", re.IGNORECASE)
TAG_SUBTEMA_RE = re.compile(r"\[\[\s*subtema\s*=\s*(.*?)\s*\]\]", re.IGNORECASE)
TAG_CLAVE_RE = re.compile(r"\[\[\s*clave\s*=\s*(.*?)\s*\]\]", re.IGNORECASE)
SIM_TEMA_THRESHOLD = 0.8
SIM_SUBTEMA_THRESHOLD = 0.8
SIM_AREA_THRESHOLD = 0.5


class LoaderWindow(tk.Toplevel):
    """Modulo 1: Cargar archivos LaTeX y guardarlos como problemas en Postgres."""

    def __init__(self, parent, db_name_inicial: str | None = None):
        super().__init__(parent)
        self.title("Modulo 1 - Carga de LaTeX")
        self.geometry("920x600")
        self.minsize(820, 560)
        self._maximize_window()

        self.db = DatabaseManager()
        self.db_sel = db_name_inicial
        self.db_name_var = tk.StringVar(value=db_name_inicial or "")
        self.folder_var = tk.StringVar(value=str(Path.cwd()))
        self.curso_var = tk.StringVar(value="General")
        self.tema_var = tk.StringVar(value="Gral")
        self.subtema_var = tk.StringVar(value="")
        self.auto_tema_var = tk.BooleanVar(value=False)

        self.file_map: Dict[str, Path] = {}

        self._apply_light_theme()
        self._build_ui()
        self._init_drag_and_drop()
        self._listar_dbs_async()

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
        header = ttk.Label(self, text="Modulo 1 - Cargar archivos LaTeX", style="Header.TLabel")
        header.pack(anchor="w", padx=16, pady=(14, 6))

        top = ttk.Frame(self)
        top.pack(fill="x", padx=16)

        ttk.Label(top, text="Base de datos").grid(row=0, column=0, sticky="w")
        self.combo_db = ttk.Combobox(top, state="readonly", textvariable=self.db_name_var, values=[])
        self.combo_db.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(top, text="Refrescar", command=self._listar_dbs_async, style="Ghost.TButton").grid(row=0, column=2, sticky="ew")

        ttk.Label(top, text="Carpeta").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(top, textvariable=self.folder_var).grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=(10, 0))
        ttk.Button(top, text="Examinar", command=self._browse_folder, style="Ghost.TButton").grid(row=1, column=2, sticky="ew", pady=(10, 0))
        ttk.Button(top, text="Buscar .tex", command=self._scan_folder, style="Secondary.TButton").grid(row=1, column=3, sticky="ew", padx=(8, 0), pady=(10, 0))

        top.columnconfigure(1, weight=1)

        meta = ttk.Frame(self)
        meta.pack(fill="x", padx=16, pady=(8, 0))
        ttk.Label(meta, text="Curso").grid(row=0, column=0, sticky="w")
        ttk.Entry(meta, textvariable=self.curso_var).grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Label(meta, text="Tema").grid(row=0, column=2, sticky="w", padx=(8, 0))
        ttk.Entry(meta, textvariable=self.tema_var).grid(row=0, column=3, sticky="ew", padx=(8, 8))
        ttk.Label(meta, text="Subtema").grid(row=0, column=4, sticky="w", padx=(8, 0))
        ttk.Entry(meta, textvariable=self.subtema_var).grid(row=0, column=5, sticky="ew", padx=(8, 8))
        ttk.Checkbutton(meta, text="Autocompletar Curso/Tema/Subtema", variable=self.auto_tema_var).grid(
            row=0, column=6, sticky="w"
        )
        meta.columnconfigure(1, weight=1)
        meta.columnconfigure(3, weight=1)
        meta.columnconfigure(5, weight=1)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=16, pady=(14, 0))

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        ttk.Label(left, text="Archivos .tex").pack(anchor="w")
        self.files_list = tk.Listbox(
            left,
            selectmode=tk.EXTENDED,
            bg="#ffffff",
            fg="#0f172a",
            selectbackground="#bfdbfe",
            selectforeground="#0f172a",
            highlightthickness=1,
            highlightbackground="#cbd5e1",
        )
        self.files_list.pack(fill="both", expand=True, pady=(6, 0))

        actions = ttk.Frame(left)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="Seleccionar todo", command=lambda: self.files_list.select_set(0, "end"), style="Ghost.TButton").pack(side="left")
        ttk.Button(actions, text="Revisar + vista previa", command=self._abrir_revisor, style="Secondary.TButton").pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Cargar a BD", command=self._cargar_bd_async, style="Accent.TButton").pack(side="left", padx=(8, 0))

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(16, 0))
        ttk.Label(right, text="Log").pack(anchor="w")
        self.txt_log = tk.Text(
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
        self.txt_log.pack(fill="both", expand=True, pady=(6, 0))
        self.progress = ttk.Progressbar(right, mode="determinate", maximum=100, style="Accent.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(10, 0))

    def _init_drag_and_drop(self) -> None:
        if not DND_AVAILABLE:
            msg = "Drag & Drop no disponible (tkinterdnd2 no cargado)."
            if DND_IMPORT_ERROR:
                msg = f"{msg} Detalle: {DND_IMPORT_ERROR}"
            self._log(msg)
            return
        def register() -> None:
            registered = False
            for widget in (self, self.files_list):
                if not hasattr(widget, "drop_target_register"):
                    self._log("Drag & Drop: widget sin soporte drop_target_register.")
                    continue
                try:
                    widget.drop_target_register(DND_FILES)
                    widget.dnd_bind("<<Drop>>", self._on_drop_files)
                    registered = True
                except Exception as exc:
                    self._log(f"Drag & Drop: error registrando: {exc}")
            if registered:
                self._log("Arrastra archivos .tex a la lista para agregar.")
            else:
                self._log("Drag & Drop no se pudo activar.")

        # Registrar después de crear widgets (algunas builds requieren esto)
        try:
            self.after(200, register)
        except Exception:
            register()

    def _on_drop_files(self, event) -> None:
        raw_paths = self._parse_drop_files(getattr(event, "data", "") or "")
        if not raw_paths:
            self._log("Drop recibido, pero sin rutas detectadas.")
        tex_paths = self._collect_tex_paths(raw_paths)
        self._add_tex_files(tex_paths)

    def _parse_drop_files(self, data: str) -> List[str]:
        if not data:
            return []
        try:
            parts = self.tk.splitlist(data)
        except Exception:
            parts = data.split()
        return [str(p).strip() for p in parts if str(p).strip()]

    def _collect_tex_paths(self, raw_paths: List[str]) -> List[Path]:
        tex_paths: List[Path] = []
        for raw in raw_paths:
            path = Path(raw).expanduser()
            if path.is_dir():
                tex_paths.extend(sorted(path.glob("*.tex")))
                continue
            if path.is_file() and path.suffix.lower() == ".tex":
                tex_paths.append(path)
        seen: set[str] = set()
        unique: List[Path] = []
        for path in tex_paths:
            try:
                key = str(path.resolve())
            except Exception:
                key = str(path)
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)
        return unique

    def _make_unique_label(self, path: Path) -> str:
        size_kb = path.stat().st_size / 1024
        base = f"{path.name} - {size_kb:,.1f} KB"
        label = base
        if label in self.file_map and self.file_map[label] == path:
            return label
        if label in self.file_map:
            label = f"{base} ({path.parent})"
        counter = 2
        while label in self.file_map and self.file_map[label] != path:
            label = f"{base} ({path.parent}) #{counter}"
            counter += 1
        return label

    def _add_tex_files(self, tex_paths: List[Path]) -> None:
        if not tex_paths:
            self._log("No se detectaron archivos .tex para agregar.")
            return
        added = 0
        skipped = 0
        existing: set[str] = set()
        for p in self.file_map.values():
            try:
                existing.add(str(p.resolve()))
            except Exception:
                existing.add(str(p))
        for path in tex_paths:
            if not path.exists():
                continue
            try:
                key = str(path.resolve())
            except Exception:
                key = str(path)
            if key in existing:
                skipped += 1
                continue
            label = self._make_unique_label(path)
            self.file_map[label] = path
            self.files_list.insert("end", label)
            existing.add(key)
            added += 1
        if added:
            self._log(f"Agregados {added} archivo(s) .tex.")
        if skipped:
            self._log(f"Omitidos {skipped} archivo(s) duplicados.")

    def _log(self, text: str) -> None:
        self.txt_log.insert("end", text + "\n")
        self.txt_log.see("end")

    def _listar_dbs_async(self) -> None:
        def worker():
            dbs = self.db.listar_bases_datos()

            def done():
                self.combo_db["values"] = dbs
                if self.db_sel and self.db_sel in dbs:
                    self.db_name_var.set(self.db_sel)
                elif self.db_name_var.get() in dbs:
                    pass
                elif dbs:
                    self.db_name_var.set(dbs[0])
                self._log(f"Bases disponibles: {', '.join(dbs)}")

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _browse_folder(self) -> None:
        folder = filedialog.askdirectory(title="Selecciona carpeta", initialdir=self.folder_var.get())
        if folder:
            self.folder_var.set(folder)
            self._scan_folder()

    def _scan_folder(self) -> None:
        folder = Path(self.folder_var.get()).expanduser()
        self.files_list.delete(0, "end")
        self.file_map = {}
        self.txt_log.delete("1.0", "end")
        self.progress.configure(value=0)

        if not folder.is_dir():
            self._log(f"La ruta {folder} no es valida.")
            return

        tex_files = sorted(folder.glob("*.tex"))
        if not tex_files:
            self._log("No se encontraron archivos .tex en la carpeta.")
            return

        for path in tex_files:
            label = self._make_unique_label(path)
            self.file_map[label] = path
            self.files_list.insert("end", label)

        self._log(f"Encontrados {len(tex_files)} archivo(s) .tex.")

    def _abrir_revisor(self) -> None:
        selected = [self.files_list.get(i) for i in self.files_list.curselection()]
        if not selected:
            messagebox.showwarning("Archivo", "Selecciona 1 archivo .tex para revisar.")
            return
        if len(selected) != 1:
            messagebox.showwarning("Archivo", "Para revisar con vista previa selecciona solo 1 archivo.")
            return
        path = self.file_map.get(selected[0])
        if not path:
            messagebox.showwarning("Archivo", "No se pudo resolver la ruta del archivo seleccionado.")
            return
        LatexReviewWindow(self, file_path=path)

    def _leer_archivo_seguro(self, ruta: Path) -> str:
        raw = ruta.read_bytes()
        for enc in ("utf-8", "cp1252", "latin-1"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("latin-1", errors="replace")

    def _extraer_tags_curso_tema(self, texto: str) -> tuple[str, str, str, str, str]:
        curso = ""
        tema = ""
        subtema = ""
        examen = ""
        for match in TAG_CURSO_RE.finditer(texto or ""):
            value = (match.group(1) or "").strip()
            if value:
                curso = value
        for match in TAG_TEMA_RE.finditer(texto or ""):
            value = (match.group(1) or "").strip()
            if value:
                tema = value
        for match in TAG_SUBTEMA_RE.finditer(texto or ""):
            value = (match.group(1) or "").strip()
            if value:
                subtema = value
        for match in TAG_EXAMEN_RE.finditer(texto or ""):
            value = (match.group(1) or "").strip()
            if value:
                examen = value
        cleaned = TAG_CURSO_RE.sub(" ", texto or "")
        cleaned = TAG_TEMA_RE.sub(" ", cleaned)
        cleaned = TAG_SUBTEMA_RE.sub(" ", cleaned)
        cleaned = TAG_EXAMEN_RE.sub(" ", cleaned)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
        return curso, tema, subtema, examen, cleaned

    def _split_marker_value(self, value: str) -> Optional[tuple[str, str, str, Optional[str], Optional[str]]]:
        value = (value or "").strip()
        if not value:
            return None
        match = IMG_MARK_VALUE_OPT_RE.match(value)
        if match:
            base = (match.group(1) or "").strip()
            sep_num = match.group(2) or "-"
            num = (match.group(3) or "").strip()
            sep_opt = match.group(4) or "-"
            opt = (match.group(5) or "").strip().upper()
            if not base or not num or not opt:
                return None
            return base, sep_num, num, sep_opt, opt
        match = IMG_MARK_VALUE_RE.match(value)
        if not match:
            return None
        base = (match.group(1) or "").strip()
        sep_num = match.group(2) or "-"
        num = (match.group(3) or "").strip()
        if not base or not num:
            return None
        return base, sep_num, num, None, None

    def _is_base_placeholder(self, base: str) -> bool:
        value = (base or "").strip().lower()
        if value.startswith("<") and value.endswith(">"):
            value = value[1:-1].strip()
        value = value.replace(" ", "").replace("-", "_")
        return value in {"nombre_de_archivo", "archivo_origen", "archivo"}

    def _normalize_name_for_compare(self, value: str) -> str:
        return re.sub(r"[^0-9a-z]+", "", (value or "").lower())

    def _base_matches_stem(self, base: str, stem: str) -> bool:
        base_norm = self._normalize_name_for_compare(base)
        stem_norm = self._normalize_name_for_compare(stem)
        if not base_norm or not stem_norm:
            return False
        if base_norm == stem_norm:
            return True
        if stem_norm.startswith("s") and len(stem_norm) > 1 and stem_norm[1].isdigit():
            if base_norm == stem_norm[1:]:
                return True
        if base_norm.startswith("s") and len(base_norm) > 1 and base_norm[1].isdigit():
            if base_norm[1:] == stem_norm:
                return True
        return False

    def _normalizar_marcadores_img(self, texto: str, *, archivo_origen: str) -> str:
        stem = Path(archivo_origen).stem
        if not stem:
            return texto or ""

        def repl_tag(match: re.Match) -> str:
            value = match.group(1) or ""
            parsed = self._split_marker_value(value)
            if not parsed:
                return match.group(0)
            base, sep_num, num, sep_opt, opt = parsed
            needs_standard = (
                base.lower() == "img"
                or self._is_base_placeholder(base)
                or self._base_matches_stem(base, stem)
            )
            if needs_standard:
                base = stem
                sep_num = "-"
                if opt:
                    sep_opt = "-"
            opt_text = f"{sep_opt or '-'}{opt}" if opt else ""
            return f"[[Imagen={base}{sep_num}{num}{opt_text}]]"

        def repl_legacy(match: re.Match) -> str:
            value = match.group(1) or ""
            if "=" in value:
                return match.group(0)
            low = value.strip().lower()
            if any(low.endswith(ext) for ext in IMAGE_EXTS):
                return match.group(0)
            parsed = self._split_marker_value(value)
            if not parsed:
                return match.group(0)
            base, sep_num, num, sep_opt, opt = parsed
            needs_standard = (
                base.lower() == "img"
                or self._is_base_placeholder(base)
                or self._base_matches_stem(base, stem)
            )
            if needs_standard:
                base = stem
                sep_num = "-"
                if opt:
                    sep_opt = "-"
            opt_text = f"{sep_opt or '-'}{opt}" if opt else ""
            return f"[[Imagen={base}{sep_num}{num}{opt_text}]]"

        txt = IMG_MARK_TAG_RE.sub(repl_tag, texto or "")
        return IMG_MARK_LEGACY_RE.sub(repl_legacy, txt)

    def _norm_text(self, value: str) -> str:
        raw = (value or "").strip().lower()
        if not raw:
            return ""
        raw = unicodedata.normalize("NFKD", raw)
        raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
        raw = re.sub(r"\s+", " ", raw)
        return raw.strip()

    def _normalizar_clave(self, value: str) -> Optional[str]:
        raw = (value or "").strip().upper()
        if not raw:
            return None
        match = re.search(r"[A-E]", raw)
        if not match:
            return None
        return match.group(0)

    def _extraer_tag_clave(self, texto: str) -> tuple[Optional[str], str]:
        clave: Optional[str] = None
        for match in TAG_CLAVE_RE.finditer(texto or ""):
            value = self._normalizar_clave(match.group(1) or "")
            if value:
                clave = value
        cleaned = TAG_CLAVE_RE.sub(" ", texto or "")
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
        return clave, cleaned

    def _insertar_tags_curso_tema(self, texto: str, *, curso: str, tema: str, subtema: str, clave: str = "") -> str:
        cleaned = TAG_CURSO_RE.sub(" ", texto or "")
        cleaned = TAG_TEMA_RE.sub(" ", cleaned)
        cleaned = TAG_SUBTEMA_RE.sub(" ", cleaned)
        cleaned = TAG_CLAVE_RE.sub(" ", cleaned)
        cleaned = TAG_EXAMEN_RE.sub(" ", cleaned)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
        tags: List[str] = []
        if curso:
            tags.append(f"[[curso={curso}]]")
        if tema:
            tags.append(f"[[tema={tema}]]")
        if subtema:
            tags.append(f"[[subtema={subtema}]]")
        clave_norm = self._normalizar_clave(clave)
        if clave_norm:
            tags.append(f"[[clave={clave_norm}]]")
        if not tags:
            return cleaned
        tag_text = " ".join(tags)
        match = ITEM_HEADER_RE.search(cleaned)
        if not match:
            return f"{tag_text} {cleaned}".strip()
        header = match.group(1)
        rest = cleaned[match.end():].lstrip()
        if rest:
            return f"{header} {tag_text} {rest}".strip()
        return f"{header} {tag_text}".strip()

    def _extraer_marcadores_img_base(self, texto: str, *, archivo_origen: str) -> List[tuple[str, Optional[str]]]:
        stem = Path(archivo_origen).stem
        markers: List[tuple[str, Optional[str]]] = []
        for match in IMG_MARK_TAG_RE.finditer(texto or ""):
            value = match.group(1) or ""
            parsed = self._split_marker_value(value)
            if not parsed:
                continue
            base, _sep_num, numero, _sep_opt, opt = parsed
            if base.lower() == "img" or self._is_base_placeholder(base) or self._base_matches_stem(base, stem):
                markers.append((numero, opt))
        for match in IMG_MARK_LEGACY_RE.finditer(texto or ""):
            value = match.group(1) or ""
            if "=" in value:
                continue
            low = value.strip().lower()
            if any(low.endswith(ext) for ext in IMAGE_EXTS):
                continue
            parsed = self._split_marker_value(value)
            if not parsed:
                continue
            base, _sep_num, numero, _sep_opt, opt = parsed
            if base.lower() == "img" or self._is_base_placeholder(base) or self._base_matches_stem(base, stem):
                markers.append((numero, opt))
        seen: set[tuple[str, Optional[str]]] = set()
        ordered: List[tuple[str, Optional[str]]] = []
        for marker in markers:
            if marker in seen:
                continue
            seen.add(marker)
            ordered.append(marker)
        return ordered

    def _contar_marcadores_img(self, texto: str, *, archivo_origen: str | None = None) -> int:
        stem = Path(archivo_origen).stem if archivo_origen else ""
        total = 0
        for match in IMG_MARK_TAG_RE.finditer(texto or ""):
            value = match.group(1) or ""
            parsed = self._split_marker_value(value)
            if not parsed:
                continue
            base = parsed[0]
            if base.lower() == "img" or self._is_base_placeholder(base) or (stem and self._base_matches_stem(base, stem)):
                total += 1
        for match in IMG_MARK_LEGACY_RE.finditer(texto or ""):
            value = match.group(1) or ""
            if "=" in value:
                continue
            low = value.strip().lower()
            if any(low.endswith(ext) for ext in IMAGE_EXTS):
                continue
            parsed = self._split_marker_value(value)
            if not parsed:
                continue
            base = parsed[0]
            if base.lower() == "img" or self._is_base_placeholder(base) or (stem and self._base_matches_stem(base, stem)):
                total += 1
        return total

    def _escribir_con_respaldo(self, ruta: Path, contenido: str) -> None:
        if ruta.exists():
            respaldo = ruta.with_suffix(ruta.suffix + ".bak")
            try:
                respaldo.write_bytes(ruta.read_bytes())
            except Exception:
                pass
        ruta.write_text(contenido, encoding="utf-8")

    def _renombrar_imagenes_img(
        self,
        *,
        base_dir: Path,
        archivo_origen: str,
        markers: List[tuple[str, Optional[str]]],
    ) -> Dict[str, int]:
        stem = Path(archivo_origen).stem
        if not stem:
            return {"renamed": 0, "copied": 0, "skipped": 0, "missing": 0}
        dest_dir = base_dir / f"{stem}_IMG"
        try:
            if markers:
                dest_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            dest_dir = Path("")
        dir_variants = [
            base_dir,
            base_dir / "imagenes",
            base_dir / "img",
            base_dir / "imgs",
            base_dir / "imagenes" / stem,
            base_dir / "img" / stem,
            base_dir / "imgs" / stem,
            base_dir / f"{stem}_IMG",
        ]
        renamed = 0
        copied = 0
        skipped = 0
        missing = 0
        seen_src: set[str] = set()
        for numero, opt in markers:
            opt = (opt or "").strip().upper() if opt else ""
            opt_part = f"-{opt}" if opt else ""
            found_any = False
            for dir_path in dir_variants:
                if not dir_path.exists():
                    continue
                for ext in IMAGE_EXTS:
                    for base_name in ("img", stem):
                        for sep_num in ("-", "_"):
                            base_label = f"{base_name}{sep_num}{numero}"
                            if opt:
                                for sep_opt in ("-", "_"):
                                    src = dir_path / f"{base_label}{sep_opt}{opt}{ext}"
                                    if not src.exists():
                                        continue
                                    found_any = True
                                    key = str(src)
                                    if key in seen_src:
                                        continue
                                    seen_src.add(key)
                                    target_name = f"{stem}-{numero}{opt_part}{ext}"
                                    dst = dir_path / target_name
                                    if src != dst:
                                        if dst.exists():
                                            skipped += 1
                                        else:
                                            try:
                                                src.rename(dst)
                                                renamed += 1
                                                src = dst
                                            except Exception:
                                                skipped += 1
                                    if dest_dir and dest_dir.exists():
                                        dest_path = dest_dir / target_name
                                        if str(dest_path) != str(src) and not dest_path.exists():
                                            try:
                                                shutil.copy2(src, dest_path)
                                                copied += 1
                                            except Exception:
                                                pass
                            else:
                                src = dir_path / f"{base_label}{ext}"
                                if not src.exists():
                                    continue
                                found_any = True
                                key = str(src)
                                if key in seen_src:
                                    continue
                                seen_src.add(key)
                                target_name = f"{stem}-{numero}{ext}"
                                dst = dir_path / target_name
                                if src != dst:
                                    if dst.exists():
                                        skipped += 1
                                    else:
                                        try:
                                            src.rename(dst)
                                            renamed += 1
                                            src = dst
                                        except Exception:
                                            skipped += 1
                                if dest_dir and dest_dir.exists():
                                    dest_path = dest_dir / target_name
                                    if str(dest_path) != str(src) and not dest_path.exists():
                                        try:
                                            shutil.copy2(src, dest_path)
                                            copied += 1
                                        except Exception:
                                            pass
            if not found_any:
                missing += 1
        return {"renamed": renamed, "copied": copied, "skipped": skipped, "missing": missing}

    def _normalizar_archivo_y_imagenes(self, *, ruta: Path, contenido: str) -> tuple[str, Dict[str, int]]:
        markers = self._extraer_marcadores_img_base(contenido, archivo_origen=ruta.name)
        total_markers = self._contar_marcadores_img(contenido, archivo_origen=ruta.name)
        if not markers:
            return contenido, {
                "markers": total_markers,
                "rewritten": 0,
                "renamed": 0,
                "copied": 0,
                "skipped": 0,
                "missing": 0,
            }
        normalizado = self._normalizar_marcadores_img(contenido, archivo_origen=ruta.name)
        rewritten = 0
        if normalizado != contenido:
            try:
                self._escribir_con_respaldo(ruta, normalizado)
                rewritten = 1
            except Exception:
                rewritten = 0
        stats = self._renombrar_imagenes_img(
            base_dir=ruta.parent if ruta.parent else Path.cwd(),
            archivo_origen=ruta.name,
            markers=markers,
        )
        stats.update({"markers": total_markers, "rewritten": rewritten})
        return normalizado, stats

    def _extraer_problemas(self, contenido: str, *, archivo_origen: str | None = None) -> List[Dict[str, object]]:
        contenido = contenido.replace("\r\n", "\n")
        patron = re.compile(
            r"(\\item\s*\[\s*\\textbf\s*\{\s*(\d+)\s*\.\s*\}\s*\].*?)(?=\s*\\item\s*\[\s*\\textbf|\Z)",
            re.DOTALL | re.IGNORECASE,
        )
        matches = patron.findall(contenido)
        if not matches:
            return []
        curso_default = (self.curso_var.get() or "").strip() if self.auto_tema_var.get() else ""
        tema_default = (self.tema_var.get() or "").strip() if self.auto_tema_var.get() else ""
        subtema_default = (self.subtema_var.get() or "").strip() if self.auto_tema_var.get() else ""
        problemas: List[Dict[str, object]] = []
        for bloque, numero_str in matches:
            numero = int(numero_str) if str(numero_str).isdigit() else 0
            curso_tag, tema_tag, subtema_tag, examen_tag, bloque_clean = self._extraer_tags_curso_tema(str(bloque))
            clave_tag, bloque_clean = self._extraer_tag_clave(bloque_clean)
            if archivo_origen:
                bloque_clean = self._normalizar_marcadores_img(bloque_clean, archivo_origen=archivo_origen)
            curso = curso_tag or curso_default
            tema = tema_tag or tema_default
            subtema = subtema_tag or subtema_default
            problemas.append(
                {
                    "numero_original": numero,
                    "enunciado_latex": bloque_clean.strip(),
                    "curso": curso,
                    "tema": tema,
                    "subtema": subtema,
                    "respuesta_correcta": clave_tag,
                    "examen": examen_tag,
                }
            )
        return problemas

    def _resumen_numeros(self, problemas: List[Dict[str, object]]) -> tuple[int, int, List[int]]:
        nums: List[int] = []
        for p in problemas:
            try:
                n = int(p.get("numero_original") or 0)
            except Exception:
                n = 0
            if n > 0:
                nums.append(n)
        if not nums:
            return 0, 0, []
        nums_sorted = sorted(nums)
        seen: set[int] = set()
        dupes: set[int] = set()
        for n in nums_sorted:
            if n in seen:
                dupes.add(n)
            else:
                seen.add(n)
        return nums_sorted[0], nums_sorted[-1], sorted(dupes)

    def _obtener_columnas_problemas(self, conn) -> set[str]:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'problemas'
            """
        )
        cols = {r[0] for r in cur.fetchall()}
        cur.close()
        return cols

    def _borrar_problemas_por_archivo(self, conn, archivo_origen: str) -> Dict[str, int]:
        cur = conn.cursor()
        cur.execute("SELECT id FROM public.problemas WHERE archivo_origen=%s;", (archivo_origen,))
        ids = [row[0] for row in cur.fetchall()]
        if not ids:
            cur.close()
            return {"problemas": 0, "relaciones": 0}

        cur.execute(
            """
            SELECT table_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND column_name = 'problema_id'
            """
        )
        tables = [row[0] for row in cur.fetchall() if row[0] != "problemas"]
        deleted_rel = 0
        try:
            from psycopg2 import sql
        except Exception:
            sql = None
        for table in tables:
            if sql:
                stmt = sql.SQL("DELETE FROM {} WHERE problema_id = ANY(%s)").format(
                    sql.Identifier("public", table)
                )
                cur.execute(stmt, (ids,))
            else:
                cur.execute(f"DELETE FROM public.{table} WHERE problema_id = ANY(%s)", (ids,))
            deleted_rel += cur.rowcount

        if sql:
            stmt = sql.SQL("DELETE FROM {} WHERE id = ANY(%s)").format(
                sql.Identifier("public", "problemas")
            )
            cur.execute(stmt, (ids,))
        else:
            cur.execute("DELETE FROM public.problemas WHERE id = ANY(%s)", (ids,))
        deleted_prob = cur.rowcount
        cur.close()
        return {"problemas": deleted_prob, "relaciones": deleted_rel}

    def _asegurar_tabla_temas(self, conn) -> None:
        cur = conn.cursor()
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS unaccent;")
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
        except Exception:
            pass
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS temas (
                id SERIAL PRIMARY KEY,
                nombre TEXT NOT NULL,
                area TEXT,
                nombre_norm TEXT,
                area_norm TEXT
            );
            """
        )
        cur.execute("ALTER TABLE temas ADD COLUMN IF NOT EXISTS nombre_norm TEXT;")
        cur.execute("ALTER TABLE temas ADD COLUMN IF NOT EXISTS area_norm TEXT;")
        cur.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'uq_temas_nombre_norm_area_norm'
                ) THEN
                    ALTER TABLE temas
                        ADD CONSTRAINT uq_temas_nombre_norm_area_norm UNIQUE (nombre_norm, area_norm);
                END IF;
            END $$;
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS subtemas (
                id SERIAL PRIMARY KEY,
                tema_id INT REFERENCES temas(id) ON DELETE CASCADE,
                nombre TEXT NOT NULL,
                nombre_norm TEXT NOT NULL,
                CONSTRAINT uq_subtemas_tema_nombre_norm UNIQUE (tema_id, nombre_norm)
            );
            """
        )
        cur.execute("ALTER TABLE subtemas ADD COLUMN IF NOT EXISTS nombre_norm TEXT;")
        try:
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_temas_nombre_norm_trgm
                    ON temas USING GIN (COALESCE(nombre_norm, '') gin_trgm_ops);
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_temas_area_norm_trgm
                    ON temas USING GIN (COALESCE(area_norm, '') gin_trgm_ops);
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_subtemas_nombre_norm_trgm
                    ON subtemas USING GIN (COALESCE(nombre_norm, '') gin_trgm_ops);
                """
            )
        except Exception:
            pass
        conn.commit()
        cur.close()

    def _asegurar_tabla_problemas(self, conn) -> None:
        cur = conn.cursor()
        self._asegurar_tabla_temas(conn)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS problemas (
                id SERIAL PRIMARY KEY,
                numero_original INT NOT NULL,
                archivo_origen VARCHAR(255) NOT NULL,
                ruta_carpeta TEXT,
                enunciado_latex TEXT NOT NULL,
                consistencia_matematica VARCHAR(30) NOT NULL DEFAULT 'Sin revisar',
                CONSTRAINT unique_problema_origen UNIQUE (numero_original, archivo_origen)
            );
            """
        )
        cur.execute(
            "ALTER TABLE problemas ADD COLUMN IF NOT EXISTS consistencia_matematica VARCHAR(30) NOT NULL DEFAULT 'Sin revisar';"
        )
        cur.execute("ALTER TABLE problemas ADD COLUMN IF NOT EXISTS tema_id INT REFERENCES temas(id);")
        cur.execute("ALTER TABLE problemas ADD COLUMN IF NOT EXISTS subtema_id INT REFERENCES subtemas(id);")
        cur.execute("ALTER TABLE problemas ADD COLUMN IF NOT EXISTS imagenes TEXT[];")
        cur.execute("ALTER TABLE problemas ADD COLUMN IF NOT EXISTS respuesta_correcta VARCHAR(10);")
        ensure_problem_origin_schema(conn)
        conn.commit()
        cur.close()

    def _get_or_create_tema_id(self, conn, *, tema: str, curso: str) -> int:
        tema = (tema or "").strip() or "Gral"
        curso = (curso or "").strip() or "General"
        tema_norm = self._norm_text(tema)
        curso_norm = self._norm_text(curso)
        cur = conn.cursor()
        cur.execute(
            "SELECT id, COALESCE(area,'') FROM temas WHERE nombre_norm=%s AND area_norm=%s;",
            (tema_norm, curso_norm),
        )
        row = cur.fetchone()
        if row:
            tid = int(row[0])
            existing_area = (row[1] or "").strip()
            if not existing_area and curso:
                cur.execute(
                    "UPDATE temas SET area = COALESCE(NULLIF(area,''), %s), area_norm=%s WHERE id=%s;",
                    (curso, curso_norm, tid),
                )
                conn.commit()
            cur.close()
            return tid

        try:
            cur.execute(
                """
                SELECT id,
                       similarity(COALESCE(nombre_norm,''), %s) AS sim_tema,
                       similarity(COALESCE(area_norm,''), %s) AS sim_area
                FROM temas
                ORDER BY sim_tema DESC, sim_area DESC
                LIMIT 1;
                """,
                (tema_norm, curso_norm),
            )
            row = cur.fetchone()
            if row:
                tid = int(row[0])
                sim_tema = float(row[1] or 0)
                sim_area = float(row[2] or 0)
                if sim_tema >= SIM_TEMA_THRESHOLD and (not curso or sim_area >= SIM_AREA_THRESHOLD):
                    cur.execute(
                        "UPDATE temas SET nombre_norm=%s, area_norm=%s WHERE id=%s;",
                        (tema_norm, curso_norm, tid),
                    )
                    conn.commit()
                    cur.close()
                    return tid
        except Exception:
            pass

        cur.execute(
            "SELECT id, COALESCE(area,'') FROM temas WHERE nombre=%s AND COALESCE(area,'')=%s;",
            (tema, curso),
        )
        row = cur.fetchone()
        if row:
            tid = int(row[0])
            cur.execute(
                "UPDATE temas SET nombre_norm=%s, area_norm=%s WHERE id=%s;",
                (tema_norm, curso_norm, tid),
            )
            conn.commit()
            cur.close()
            return tid

        cur.execute(
            "INSERT INTO temas (nombre, area, nombre_norm, area_norm) VALUES (%s, %s, %s, %s) RETURNING id;",
            (tema, curso, tema_norm, curso_norm),
        )
        tid = int(cur.fetchone()[0])
        conn.commit()
        cur.close()
        return tid

    def _get_or_create_subtema_id(self, conn, *, tema_id: int, subtema: str) -> Optional[int]:
        subtema = (subtema or "").strip()
        if not subtema:
            return None
        subtema_norm = self._norm_text(subtema)
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM subtemas WHERE tema_id=%s AND nombre_norm=%s;",
            (int(tema_id), subtema_norm),
        )
        row = cur.fetchone()
        if row:
            cur.close()
            return int(row[0])
        try:
            cur.execute(
                """
                SELECT id,
                       similarity(COALESCE(nombre_norm,''), %s) AS sim_subtema
                FROM subtemas
                WHERE tema_id=%s
                ORDER BY sim_subtema DESC
                LIMIT 1;
                """,
                (subtema_norm, int(tema_id)),
            )
            row = cur.fetchone()
            if row:
                sid = int(row[0])
                sim_subtema = float(row[1] or 0)
                if sim_subtema >= SIM_SUBTEMA_THRESHOLD:
                    cur.execute(
                        "UPDATE subtemas SET nombre_norm=%s WHERE id=%s;",
                        (subtema_norm, sid),
                    )
                    conn.commit()
                    cur.close()
                    return sid
        except Exception:
            pass
        cur.execute(
            "SELECT id FROM subtemas WHERE tema_id=%s AND nombre=%s;",
            (int(tema_id), subtema),
        )
        row = cur.fetchone()
        if row:
            sid = int(row[0])
            cur.execute(
                "UPDATE subtemas SET nombre_norm=%s WHERE id=%s;",
                (subtema_norm, sid),
            )
            conn.commit()
            cur.close()
            return sid
        cur.execute(
            "INSERT INTO subtemas (tema_id, nombre, nombre_norm) VALUES (%s, %s, %s) RETURNING id;",
            (int(tema_id), subtema, subtema_norm),
        )
        sid = int(cur.fetchone()[0])
        conn.commit()
        cur.close()
        return sid

    def _resolver_tema_id(self, conn) -> Optional[int]:
        if not self.auto_tema_var.get():
            return None
        curso = (self.curso_var.get() or "").strip()
        tema = (self.tema_var.get() or "").strip()
        if not tema:
            return None
        curso = curso or "General"
        try:
            return self._get_or_create_tema_id(conn, tema=tema, curso=curso)
        except Exception:
            return None

    def _extraer_marcadores_imagen(self, texto: str) -> List[tuple[str, str, Optional[str]]]:
        markers: List[tuple[str, str, Optional[str]]] = []
        for match in IMG_MARK_TAG_RE.finditer(texto or ""):
            value = match.group(1) or ""
            parsed = self._split_marker_value(value)
            if not parsed:
                continue
            base, _sep_num, num, _sep_opt, opt = parsed
            markers.append((base, num, opt))
        for match in IMG_MARK_LEGACY_RE.finditer(texto or ""):
            value = match.group(1) or ""
            if "=" in value:
                continue
            low = value.strip().lower()
            if any(low.endswith(ext) for ext in IMAGE_EXTS):
                continue
            parsed = self._split_marker_value(value)
            if not parsed:
                continue
            base, _sep_num, num, _sep_opt, opt = parsed
            markers.append((base, num, opt))
        seen: set[tuple[str, str, str]] = set()
        ordered: List[tuple[str, str, Optional[str]]] = []
        for base, num, opt in markers:
            key = (base.lower(), str(num), (opt or "").lower())
            if key in seen:
                continue
            seen.add(key)
            ordered.append((base, str(num), opt))
        return ordered

    def _extraer_marcadores_imagen_archivo(self, texto: str) -> List[str]:
        markers = [m.group(1) for m in IMG_MARK_FILE_RE.finditer(texto or "")]
        seen: set[str] = set()
        ordered: List[str] = []
        for name in markers:
            key = str(name).lower()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(str(name))
        return ordered

    def _buscar_imagenes_para_etiqueta(
        self,
        *,
        base_dir: Path,
        archivo_origen: str,
        base: str,
        numero: str,
        opcion: Optional[str] = None,
    ) -> List[Path]:
        stem = Path(archivo_origen).stem
        base = (base or "").strip()
        numero = (numero or "").strip()
        opcion = (opcion or "").strip().upper()
        if not base or not numero:
            return []
        name_variants: List[str] = []
        seen_names: set[str] = set()
        base_names: List[str] = [base]
        if base.lower() != "img":
            base_names.append("img")
        elif stem:
            base_names.append(stem)

        def add_name(name: str) -> None:
            if not name:
                return
            if name in seen_names:
                return
            seen_names.add(name)
            name_variants.append(name)

        for base_name in base_names:
            for sep in ("-", "_"):
                root = f"{base_name}{sep}{numero}"
                if opcion:
                    for sep_opt in ("-", "_"):
                        add_name(f"{root}{sep_opt}{opcion}")
                else:
                    add_name(root)
        if stem:
            for name in list(name_variants):
                lname = name.lower()
                if lname.startswith(stem.lower() + "_") or lname.startswith(stem.lower() + "-"):
                    continue
                add_name(f"{stem}_{name}")
        dir_variants = [
            base_dir,
            base_dir / "imagenes",
            base_dir / "img",
            base_dir / "imgs",
            base_dir / "imagenes" / stem,
            base_dir / "img" / stem,
            base_dir / "imgs" / stem,
            base_dir / f"{stem}_IMG",
        ]
        results: List[Path] = []
        seen: set[str] = set()
        for dir_path in dir_variants:
            if not dir_path.exists():
                continue
            for name in name_variants:
                for ext in IMAGE_EXTS:
                    path = dir_path / f"{name}{ext}"
                    if not path.exists():
                        continue
                    key = str(path)
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(path)
        return results

    def _buscar_imagenes_por_archivo(
        self,
        *,
        base_dir: Path,
        archivo_origen: str,
        nombre: str,
    ) -> List[Path]:
        stem = Path(archivo_origen).stem
        nombre = Path(nombre).name.strip()
        if not nombre:
            return []
        dir_variants = [
            base_dir,
            base_dir / "imagenes",
            base_dir / "img",
            base_dir / "imgs",
            base_dir / "imagenes" / stem,
            base_dir / "img" / stem,
            base_dir / "imgs" / stem,
            base_dir / f"{stem}_IMG",
        ]
        results: List[Path] = []
        seen: set[str] = set()
        for dir_path in dir_variants:
            if not dir_path.exists():
                continue
            path = dir_path / nombre
            if not path.exists():
                continue
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            results.append(path)
        return results

    def _ruta_relativa(self, base_dir: Path, path: Path) -> str:
        try:
            return str(path.relative_to(base_dir))
        except Exception:
            return str(path)

    def _insertar_problemas(
        self,
        conn,
        *,
        archivo_origen: str,
        ruta_carpeta: str,
        problemas: List[Dict[str, object]],
        tema_id_default: Optional[int] = None,
    ) -> Dict[str, int]:
        cols = self._obtener_columnas_problemas(conn)
        has_images_col = "imagenes" in cols
        include_tema = "tema_id" in cols
        include_subtema = "subtema_id" in cols
        include_respuesta = "respuesta_correcta" in cols
        tema_cache: Dict[tuple[str, str], int] = {}
        subtema_cache: Dict[tuple[int, str], int] = {}

        def resolve_tema_id(item: Dict[str, object]) -> Optional[int]:
            tema = str(item.get("tema") or "").strip()
            curso = str(item.get("curso") or "").strip()
            if tema:
                curso_norm = curso or "General"
                key = (tema, curso_norm)
                if key not in tema_cache:
                    tema_cache[key] = self._get_or_create_tema_id(conn, tema=tema, curso=curso_norm)
                return tema_cache[key]
            return tema_id_default

        def resolve_subtema_id(item: Dict[str, object], tema_id_value: Optional[int]) -> Optional[int]:
            if not include_subtema:
                return None
            if not tema_id_value:
                return None
            subtema = str(item.get("subtema") or "").strip()
            if not subtema:
                return None
            key = (int(tema_id_value), subtema)
            if key not in subtema_cache:
                sid = self._get_or_create_subtema_id(conn, tema_id=int(tema_id_value), subtema=subtema)
                if sid is not None:
                    subtema_cache[key] = sid
            return subtema_cache.get(key)

        inserted = 0
        skipped = 0
        invalid = 0
        images_inserted = 0
        missing_markers: List[str] = []
        base_dir = Path(ruta_carpeta) if ruta_carpeta else Path.cwd()
        if not base_dir.is_dir():
            base_dir = Path.cwd()
        deleted_info = {"problemas": 0, "relaciones": 0}
        if "archivo_origen" in cols:
            deleted_info = self._borrar_problemas_por_archivo(conn, archivo_origen)

        cur = conn.cursor()

        if {"numero_original", "archivo_origen", "enunciado_latex"}.issubset(cols):
            fields = ["numero_original", "archivo_origen", "enunciado_latex"]
            values = ["%s", "%s", "%s"]
            if "ruta_carpeta" in cols:
                fields.append("ruta_carpeta")
                values.append("%s")
            if "consistencia_matematica" in cols:
                fields.append("consistencia_matematica")
                values.append("%s")
            if include_tema:
                fields.append("tema_id")
                values.append("%s")
            if include_subtema:
                fields.append("subtema_id")
                values.append("%s")
            if include_respuesta:
                fields.append("respuesta_correcta")
                values.append("%s")

            sql = f"INSERT INTO problemas ({', '.join(fields)}) VALUES ({', '.join(values)}) ON CONFLICT DO NOTHING RETURNING id;"
            for p in problemas:
                numero = int(p.get("numero_original") or 0)
                enunciado = str(p.get("enunciado_latex") or "").strip()
                clave_item = self._normalizar_clave(str(p.get("respuesta_correcta") or ""))
                if numero <= 0 or not enunciado:
                    invalid += 1
                    continue

                params: List[object] = [numero, archivo_origen, enunciado]
                if "ruta_carpeta" in cols:
                    params.append(ruta_carpeta)
                if "consistencia_matematica" in cols:
                    params.append("Sin revisar")
                tema_id_item: Optional[int] = None
                if include_tema:
                    tema_id_item = resolve_tema_id(p)
                    params.append(int(tema_id_item) if tema_id_item is not None else None)
                if include_subtema:
                    subtema_id_item = resolve_subtema_id(p, tema_id_item)
                    params.append(int(subtema_id_item) if subtema_id_item is not None else None)
                if include_respuesta:
                    params.append(clave_item)

                cur.execute(sql, tuple(params))
                row = cur.fetchone()
                if row:
                    inserted += 1
                    problema_id = int(row[0])
                else:
                    skipped += 1
                    problema_id = None
                    cur.execute(
                        "SELECT id FROM problemas WHERE numero_original=%s AND archivo_origen=%s;",
                        (numero, archivo_origen),
                    )
                    row = cur.fetchone()
                    if row:
                        problema_id = int(row[0])

                if problema_id and include_tema:
                    tema_id_item = resolve_tema_id(p)
                    if tema_id_item is not None:
                        cur.execute(
                            "UPDATE problemas SET tema_id = COALESCE(tema_id, %s) WHERE id=%s;",
                            (tema_id_item, problema_id),
                        )
                if problema_id and include_subtema:
                    tema_id_item = resolve_tema_id(p)
                    subtema_id_item = resolve_subtema_id(p, tema_id_item)
                    if subtema_id_item is not None:
                        cur.execute(
                            "UPDATE problemas SET subtema_id = COALESCE(subtema_id, %s) WHERE id=%s;",
                            (subtema_id_item, problema_id),
                        )
                if problema_id and include_respuesta and clave_item:
                    cur.execute(
                        "UPDATE problemas SET respuesta_correcta = COALESCE(respuesta_correcta, %s) WHERE id=%s;",
                        (clave_item, problema_id),
                    )
                if problema_id:
                    upsert_exam_origin_for_problem(
                        conn,
                        problem_id=int(problema_id),
                        exam_label=str(p.get("examen") or ""),
                        numero_original=int(numero),
                    )

                markers = self._extraer_marcadores_imagen(enunciado) if has_images_col else []
                file_markers = self._extraer_marcadores_imagen_archivo(enunciado) if has_images_col else []
                if problema_id and (markers or file_markers) and has_images_col:
                    image_paths: List[str] = []
                    for base, numero, opt in markers:
                        paths = self._buscar_imagenes_para_etiqueta(
                            base_dir=base_dir,
                            archivo_origen=archivo_origen,
                            base=base,
                            numero=numero,
                            opcion=opt,
                        )
                        if not paths:
                            opt_label = f"-{opt}" if opt else ""
                            marker_label = f"{base}_{numero}{opt_label}"
                            if marker_label not in missing_markers:
                                missing_markers.append(marker_label)
                            continue
                        for img_path in paths:
                            ruta_rel = self._ruta_relativa(base_dir, img_path)
                            if ruta_rel not in image_paths:
                                image_paths.append(ruta_rel)
                    for name in file_markers:
                        paths = self._buscar_imagenes_por_archivo(
                            base_dir=base_dir,
                            archivo_origen=archivo_origen,
                            nombre=name,
                        )
                        if not paths:
                            if name not in missing_markers:
                                missing_markers.append(name)
                            continue
                        for img_path in paths:
                            ruta_rel = self._ruta_relativa(base_dir, img_path)
                            if ruta_rel not in image_paths:
                                image_paths.append(ruta_rel)
                    if image_paths:
                        cur.execute("SELECT imagenes FROM problemas WHERE id=%s;", (int(problema_id),))
                        row = cur.fetchone()
                        existing = list(row[0]) if row and row[0] else []
                        existing_unique: List[str] = []
                        seen_existing: set[str] = set()
                        for val in existing:
                            if val in seen_existing:
                                continue
                            seen_existing.add(val)
                            existing_unique.append(val)
                        merged: List[str] = []
                        seen_merged: set[str] = set()
                        for val in existing_unique + image_paths:
                            if val in seen_merged:
                                continue
                            seen_merged.add(val)
                            merged.append(val)
                        cur.execute("UPDATE problemas SET imagenes=%s WHERE id=%s;", (merged, int(problema_id)))
                        images_inserted += max(len(merged) - len(existing_unique), 0)
        else:
            sql = (
                "INSERT INTO problemas (enunciado_latex, consistencia_matematica) "
                "VALUES (%s, 'Sin revisar') RETURNING id;"
            )
            for p in problemas:
                enunciado = str(p.get("enunciado_latex") or "").strip()
                if not enunciado:
                    invalid += 1
                    continue
                cur.execute(sql, (enunciado,))
                if cur.fetchone():
                    inserted += 1

        conn.commit()
        cur.close()
        return {
            "inserted": inserted,
            "skipped": skipped,
            "invalid": invalid,
            "images": images_inserted,
            "missing_images": missing_markers,
            "deleted": deleted_info.get("problemas", 0),
            "deleted_related": deleted_info.get("relaciones", 0),
        }

    def _cargar_bd_async(self) -> None:
        db_name = (self.db_name_var.get() or "").strip()
        if not db_name:
            messagebox.showwarning("BD", "Selecciona una base de datos.")
            return

        selected = [self.files_list.get(i) for i in self.files_list.curselection()]
        if not selected:
            messagebox.showwarning("Archivos", "Selecciona al menos un archivo.")
            return

        def worker():
            try:
                conn = self.db.get_connection(db_name)
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("BD", str(exc)))
                return

            try:
                self._asegurar_tabla_problemas(conn)
            except Exception as exc:
                conn.close()
                self.after(0, lambda: messagebox.showerror("BD", f"No se pudo preparar tabla.\n\n{exc}"))
                return

            tema_id_default = self._resolver_tema_id(conn)
            if self.auto_tema_var.get() and tema_id_default is None:
                self.after(0, lambda: self._log("Aviso: no se pudo resolver Curso/Tema; se cargara sin tema."))

            total_files = len(selected)
            total_inserted = 0
            total_parsed = 0
            total_skipped = 0
            total_invalid = 0
            total_images = 0
            total_missing_images = 0
            total_deleted = 0
            for idx, label in enumerate(selected, start=1):
                path = self.file_map.get(label)
                if not path:
                    continue
                self.after(0, lambda p=path: self._log(f"Leyendo {p.name} ..."))
                try:
                    contenido = self._leer_archivo_seguro(path)
                    contenido, stats_norm = self._normalizar_archivo_y_imagenes(ruta=path, contenido=contenido)
                    if stats_norm.get("rewritten"):
                        msg = (
                            f"{path.name}: marcadores img={stats_norm.get('markers', 0)} "
                            f"imagenes renombradas={stats_norm.get('renamed', 0)} "
                            f"copiadas={stats_norm.get('copied', 0)} "
                            f"omitidas={stats_norm.get('skipped', 0)} "
                            f"faltantes={stats_norm.get('missing', 0)}"
                        )
                        self.after(0, lambda m=msg: self._log(m))
                    problemas = self._extraer_problemas(contenido, archivo_origen=path.name)
                except Exception as exc:
                    self.after(0, lambda p=path, e=exc: self._log(f"Error procesando {p.name}: {e}"))
                    continue

                total_parsed += len(problemas)
                if problemas:
                    mn, mx, dupes = self._resumen_numeros(problemas)
                    if mn and mx:
                        self.after(0, lambda p=path, a=mn, b=mx: self._log(f"{p.name}: rango numero_original={a}-{b}"))
                    if dupes:
                        show = ", ".join(str(x) for x in dupes[:40])
                        more = f" (+{len(dupes)-40} mas)" if len(dupes) > 40 else ""
                        self.after(
                            0,
                            lambda p=path, s=show, more=more, c=len(dupes): self._log(
                                f"{p.name}: advertencia: numeros repetidos dentro del archivo ({c}): {s}{more}"
                            ),
                        )
                if problemas:
                    try:
                        stats = self._insertar_problemas(
                            conn,
                            archivo_origen=path.name,
                            ruta_carpeta=str(path.parent),
                            problemas=problemas,
                            tema_id_default=tema_id_default,
                        )
                    except Exception as exc:
                        self.after(0, lambda p=path, e=exc: self._log(f"Error insertando {p.name}: {e}"))
                        continue

                    total_inserted += stats["inserted"]
                    total_skipped += stats["skipped"]
                    total_invalid += stats["invalid"]
                    images = int(stats.get("images", 0) or 0)
                    missing = stats.get("missing_images") or []
                    total_images += images
                    total_missing_images += len(missing)
                    deleted = int(stats.get("deleted", 0) or 0)
                    total_deleted += deleted
                    msg = (
                        f"{path.name}: parseados={len(problemas)} "
                        f"insertados={stats['inserted']} duplicados={stats['skipped']} "
                        f"invalidos={stats['invalid']} imagenes={images}"
                    )
                    if deleted:
                        msg = f"{msg} sobrescritos={deleted}"
                    if missing:
                        show = ", ".join(missing[:10])
                        more = f" (+{len(missing)-10} mas)" if len(missing) > 10 else ""
                        msg = f"{msg} faltantes={show}{more}"
                    self.after(
                        0,
                        lambda m=msg: self._log(m),
                    )
                else:
                    self.after(0, lambda p=path: self._log(f"{p.name}: no se detectaron problemas con el patron."))

                percent = int(idx / total_files * 100)
                self.after(0, lambda v=percent: self.progress.configure(value=v))

            conn.close()

            def done():
                self.progress.configure(value=100)
                messagebox.showinfo(
                    "Resultado",
                    f"BD: {db_name}\nArchivos: {total_files}\n"
                    f"Problemas parseados: {total_parsed}\n"
                    f"Insertados: {total_inserted}\n"
                    f"Duplicados: {total_skipped}\n"
                    f"Sobrescritos: {total_deleted}\n"
                    f"Invalidos: {total_invalid}\n"
                    f"Imagenes vinculadas: {total_images}\n"
                    f"Marcadores sin archivo: {total_missing_images}",
                )

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()


class LatexReviewWindow(tk.Toplevel):
    def __init__(self, parent: LoaderWindow, *, file_path: Path):
        super().__init__(parent)
        self.parent = parent
        self.file_path = file_path
        self.title(f"Revisor LaTeX - {file_path.name}")
        self.geometry("1100x720")
        self.minsize(980, 620)

        self.preview = PreviewWindow(title=f"Vista previa - {file_path.name}", width=540, height=620)
        self.preview_all_var = tk.BooleanVar(value=False)
        self.preview_panel_var = tk.BooleanVar(value=True)
        self._debounce_after: str | None = None
        self._current_index: int | None = None
        self.var_curso = tk.StringVar(value="")
        self.var_tema = tk.StringVar(value="")
        self.var_subtema = tk.StringVar(value="")

        self._apply_light_theme()
        self._build_ui()
        self._load_file()

    def _apply_light_theme(self) -> None:
        self.palette = apply_openai_theme(self)

    def _build_ui(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x", padx=16, pady=(14, 10))

        ttk.Label(top, text="Revision de archivo", style="Section.TLabel").pack(side="left")
        ttk.Label(top, text=f"BD seleccionada: {self.parent.db_name_var.get() or '(no seleccionada)'}", style="Muted.TLabel").pack(
            side="left", padx=(12, 0)
        )

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=16, pady=(0, 10))

        left = ttk.Frame(body)
        left.pack(side="left", fill="y")
        ttk.Label(left, text="Problemas").pack(anchor="w")
        self.list_problemas = tk.Listbox(
            left,
            selectmode=tk.SINGLE,
            width=26,
            bg=self.palette["surface"],
            fg=self.palette["text"],
            selectbackground=self.palette["select"],
            selectforeground=self.palette["text"],
            highlightthickness=1,
            highlightbackground=self.palette["border"],
        )
        self.list_problemas.pack(fill="y", expand=True, pady=(6, 0))
        self.list_problemas.bind("<<ListboxSelect>>", self._on_select)

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(16, 0))

        toolbar = ttk.Frame(right)
        toolbar.pack(fill="x")
        ttk.Checkbutton(toolbar, text="Panel vista previa", variable=self.preview_panel_var).pack(side="left")
        ttk.Checkbutton(
            toolbar,
            text="Vista previa del archivo completo",
            variable=self.preview_all_var,
            command=self._push_preview,
        ).pack(side="left", padx=(10, 0))
        ttk.Button(toolbar, text="Vista previa", command=self._open_preview, style="Ghost.TButton").pack(side="left", padx=(10, 0))
        ttk.Button(toolbar, text="Guardar .tex (editado)", command=self._guardar_archivo_editado, style="Secondary.TButton").pack(side="left", padx=(10, 0))
        ttk.Button(toolbar, text="Cargar a BD (editado)", command=self._cargar_a_bd_editado, style="Accent.TButton").pack(side="right")

        meta = ttk.Frame(right)
        meta.pack(fill="x", pady=(10, 0))
        ttk.Label(meta, text="Curso").pack(side="left")
        ttk.Entry(meta, textvariable=self.var_curso, width=20).pack(side="left", padx=(8, 14))
        ttk.Label(meta, text="Tema").pack(side="left")
        ttk.Entry(meta, textvariable=self.var_tema).pack(side="left", fill="x", expand=True, padx=(8, 14))
        ttk.Label(meta, text="Subtema").pack(side="left")
        ttk.Entry(meta, textvariable=self.var_subtema).pack(side="left", fill="x", expand=True, padx=(8, 0))

        ttk.Label(right, text="Editor (edita y la vista previa se actualiza)", style="Muted.TLabel").pack(anchor="w", pady=(10, 0))
        self.txt_editor = tk.Text(
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
        self.txt_editor.pack(fill="both", expand=True, pady=(6, 0))
        self.txt_editor.bind("<<Modified>>", self._on_modified)

        self.lbl_status = ttk.Label(self, text="")
        self.lbl_status.pack(anchor="w", padx=16, pady=(0, 12))

    def _load_file(self) -> None:
        try:
            contenido = self.parent._leer_archivo_seguro(self.file_path)
        except Exception as exc:
            messagebox.showerror("Archivo", str(exc))
            self.destroy()
            return

        self._contenido_original = contenido
        self.problemas = self.parent._extraer_problemas(contenido, archivo_origen=self.file_path.name)
        self.list_problemas.delete(0, "end")
        if not self.problemas:
            self.lbl_status.configure(text="No se detectaron problemas con el patrón \\item[\\textbf{n.}].")
            return

        for i, p in enumerate(self.problemas, start=1):
            n = int(p.get("numero_original") or 0)
            self.list_problemas.insert("end", f"{i:02d}. n={n}")
        self.lbl_status.configure(text=f"Problemas detectados: {len(self.problemas)}")

        self.list_problemas.select_set(0)
        self._load_index(0)

    def _load_index(self, idx: int) -> None:
        if idx < 0 or idx >= len(self.problemas):
            return
        self._current_index = idx
        txt = str(self.problemas[idx].get("enunciado_latex") or "")
        self.var_curso.set(str(self.problemas[idx].get("curso") or ""))
        self.var_tema.set(str(self.problemas[idx].get("tema") or ""))
        self.var_subtema.set(str(self.problemas[idx].get("subtema") or ""))
        self.txt_editor.delete("1.0", "end")
        self.txt_editor.insert("1.0", txt)
        try:
            self.txt_editor.edit_modified(False)
        except Exception:
            pass
        self._push_preview()

    def _save_current_edit(self) -> None:
        if self._current_index is None:
            return
        txt = (self.txt_editor.get("1.0", "end") or "").strip()
        self.problemas[self._current_index]["enunciado_latex"] = txt
        self.problemas[self._current_index]["curso"] = (self.var_curso.get() or "").strip()
        self.problemas[self._current_index]["tema"] = (self.var_tema.get() or "").strip()
        self.problemas[self._current_index]["subtema"] = (self.var_subtema.get() or "").strip()

    def _on_select(self, _event=None) -> None:
        sel = self.list_problemas.curselection()
        if not sel:
            return
        self._save_current_edit()
        self._load_index(int(sel[0]))

    def _on_modified(self, _event=None) -> None:
        try:
            self.txt_editor.edit_modified(False)
        except Exception:
            pass
        if self._debounce_after:
            try:
                self.after_cancel(self._debounce_after)
            except Exception:
                pass
        self._debounce_after = self.after(200, self._push_preview)

    def _text_to_preview(self) -> str:
        self._save_current_edit()
        if self.preview_all_var.get():
            return "\n\n".join(str(p.get("enunciado_latex") or "").strip() for p in self.problemas if str(p.get("enunciado_latex") or "").strip())
        return (self.txt_editor.get("1.0", "end") or "").strip()

    def _push_preview(self) -> None:
        try:
            self.preview.set_text(self._text_to_preview())
            if bool(self.preview_panel_var.get()):
                self._open_preview_panel_dock()
        except Exception:
            return

    def _open_preview(self) -> None:
        self._push_preview()
        try:
            self._open_preview_panel_dock()
        except Exception as exc:
            messagebox.showerror("Vista previa", str(exc))

    def _open_preview_panel_dock(self) -> None:
        self.update_idletasks()
        try:
            x = int(self.txt_editor.winfo_rootx() + self.txt_editor.winfo_width() + 12)
            y = int(self.txt_editor.winfo_rooty())
        except Exception:
            x = None
            y = None
        self.preview.ensure_open_at(x=x, y=y, on_top=False)

    def _guardar_archivo_editado(self) -> None:
        self._save_current_edit()
        rendered: List[str] = []
        for p in self.problemas:
            enunciado = str(p.get("enunciado_latex") or "").strip()
            if not enunciado:
                continue
            curso = str(p.get("curso") or "").strip()
            tema = str(p.get("tema") or "").strip()
            subtema = str(p.get("subtema") or "").strip()
            normalizado = self.parent._normalizar_marcadores_img(enunciado, archivo_origen=self.file_path.name)
            clave = str(p.get("respuesta_correcta") or "").strip()
            rendered.append(
                self.parent._insertar_tags_curso_tema(
                    normalizado,
                    curso=curso,
                    tema=tema,
                    subtema=subtema,
                    clave=clave,
                )
            )
        contenido = "\n\n".join(rendered)
        if not contenido.strip():
            messagebox.showwarning("Guardar", "No hay contenido para guardar.")
            return
        out = filedialog.asksaveasfilename(
            title="Guardar .tex",
            defaultextension=".tex",
            filetypes=[("LaTeX", "*.tex"), ("Texto", "*.txt"), ("Todos", "*.*")],
            initialfile=f"EDITADO_{self.file_path.name}",
        )
        if not out:
            return
        try:
            Path(out).write_text(contenido, encoding="utf-8")
        except Exception as exc:
            messagebox.showerror("Guardar", str(exc))
            return
        messagebox.showinfo("Guardar", f"Guardado en:\n{out}")

    def _cargar_a_bd_editado(self) -> None:
        db_name = (self.parent.db_name_var.get() or "").strip()
        if not db_name:
            messagebox.showwarning("BD", "Selecciona una base de datos en la ventana principal del Modulo 1.")
            return
        self._save_current_edit()
        if not self.problemas:
            messagebox.showwarning("BD", "No hay problemas parseados para cargar.")
            return

        try:
            conn = self.parent.db.get_connection(db_name)
        except Exception as exc:
            messagebox.showerror("BD", str(exc))
            return

        try:
            self.parent._asegurar_tabla_problemas(conn)
            tema_id_default = self.parent._resolver_tema_id(conn)
            for item in self.problemas:
                texto = str(item.get("enunciado_latex") or "").strip()
                if not texto:
                    continue
                item["enunciado_latex"] = self.parent._normalizar_marcadores_img(
                    texto,
                    archivo_origen=self.file_path.name,
                )
            stats = self.parent._insertar_problemas(
                conn,
                archivo_origen=self.file_path.name,
                ruta_carpeta=str(self.file_path.parent),
                problemas=self.problemas,
                tema_id_default=tema_id_default,
            )
        except Exception as exc:
            conn.close()
            messagebox.showerror("BD", str(exc))
            return
        conn.close()
        images = int(stats.get("images", 0) or 0)
        deleted = int(stats.get("deleted", 0) or 0)
        missing = stats.get("missing_images") or []
        missing_line = ""
        if missing:
            show = ", ".join(missing[:10])
            more = f" (+{len(missing)-10} mas)" if len(missing) > 10 else ""
            missing_line = f"\nMarcadores sin archivo: {show}{more}"
        messagebox.showinfo(
            "BD",
            f"BD: {db_name}\nArchivo: {self.file_path.name}\n"
            f"Parseados: {len(self.problemas)}\nInsertados: {stats['inserted']}\n"
            f"Duplicados: {stats['skipped']}\nSobrescritos: {deleted}\nInvalidos: {stats['invalid']}\n"
            f"Imagenes vinculadas: {images}{missing_line}",
        )


def _run_gui() -> None:
    try:
        from tkinterdnd2 import TkinterDnD  # type: ignore
        root = TkinterDnD.Tk()
    except Exception:
        root = tk.Tk()
    root.withdraw()
    win = LoaderWindow(root)
    root.mainloop()


if __name__ == "__main__":
    _run_gui()
