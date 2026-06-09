from __future__ import annotations

import os
import re
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

from database.connection import read_db_profile_config
from database.problem_change_queue import ProblemChangeQueueController
from utils.project_layout import project_dirs
from utils.runtime_log import get_logger
from utils.styles import apply_openai_theme

from .controlador_organizador_libros import (
    BOOK_STATES,
    BookCreateInput,
    BookInstanceInput,
    BookInstanceUpdateInput,
    BookProgressController,
    BookUpdateInput,
)


class _FormDialog(tk.Toplevel):
    def __init__(self, parent, title: str, fields: list[dict], initial: dict | None = None):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.result: dict | None = None
        self._vars: dict[str, tk.Variable] = {}
        self._fields = fields
        self._initial = initial or {}
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.wait_visibility()
        self.focus_force()
        self.wait_window(self)

    def _build_ui(self) -> None:
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        for idx, field in enumerate(self._fields):
            name = field["name"]
            kind = field.get("kind", "str")
            ttk.Label(body, text=field["label"]).grid(row=idx, column=0, sticky="w", pady=(0, 8))
            initial_value = self._initial.get(name, field.get("default"))
            if kind == "bool":
                var = tk.BooleanVar(value=bool(initial_value))
                widget = ttk.Checkbutton(body, variable=var)
            elif kind in {"choice", "combo"}:
                var = tk.StringVar(value=str(initial_value or ""))
                state = "readonly" if kind == "choice" else "normal"
                widget = ttk.Combobox(body, textvariable=var, values=field.get("values", []), state=state)
            elif kind == "int":
                var = tk.StringVar(value="" if initial_value is None else str(initial_value))
                widget = ttk.Entry(body, textvariable=var)
            elif kind in {"dir", "file"}:
                var = tk.StringVar(value=str(initial_value or ""))
                wrap = ttk.Frame(body)
                wrap.columnconfigure(0, weight=1)
                entry = ttk.Entry(wrap, textvariable=var)
                entry.grid(row=0, column=0, sticky="ew")
                button_text = "Carpeta..." if kind == "dir" else "Archivo..."
                ttk.Button(
                    wrap,
                    text=button_text,
                    style="Ghost.TButton",
                    command=lambda v=var, k=kind: self._browse_path(v, k),
                ).grid(row=0, column=1, padx=(8, 0))
                widget = wrap
            else:
                var = tk.StringVar(value=str(initial_value or ""))
                widget = ttk.Entry(body, textvariable=var)
            self._vars[name] = var
            widget.grid(row=idx, column=1, sticky="ew", padx=(10, 0), pady=(0, 8))
        body.columnconfigure(1, weight=1)

        actions = ttk.Frame(body)
        actions.grid(row=len(self._fields), column=0, columnspan=2, sticky="e", pady=(4, 0))
        ttk.Button(actions, text="Cancelar", command=self._cancel, style="Ghost.TButton").pack(side="left")
        ttk.Button(actions, text="Guardar", command=self._submit, style="Accent.TButton").pack(side="left", padx=(8, 0))

    def _browse_path(self, var: tk.StringVar, kind: str) -> None:
        current = str(var.get() or "").strip()
        initial_dir = ""
        if current:
            try:
                current_path = Path(current).expanduser()
                initial_dir = str(current_path if current_path.is_dir() else current_path.parent)
            except Exception:
                initial_dir = ""
        try:
            if kind == "dir":
                selected = filedialog.askdirectory(parent=self, initialdir=initial_dir or None)
            else:
                selected = filedialog.askopenfilename(parent=self, initialdir=initial_dir or None)
        except Exception:
            selected = ""
        if selected:
            var.set(str(selected))

    def _submit(self) -> None:
        data: dict[str, object] = {}
        try:
            for field in self._fields:
                name = field["name"]
                kind = field.get("kind", "str")
                value = self._vars[name].get()
                if kind == "int":
                    txt = str(value).strip()
                    data[name] = None if txt == "" else int(txt)
                elif kind == "bool":
                    data[name] = bool(value)
                else:
                    data[name] = str(value)
        except ValueError:
            messagebox.showerror("Formulario", "Revisa los campos numericos.")
            return
        self.result = data
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


class BookProgressWindow(tk.Toplevel):
    def __init__(self, parent, *, initial_db_name: str | None = None, initial_book_id: int | None = None):
        super().__init__(parent)
        self.title("Modulo 9 - Organizador de Libros")
        self.geometry("1280x780")
        self.minsize(1120, 700)
        self._maximize_window()

        self.controller = BookProgressController()
        self.palette = apply_openai_theme(self)

        self.db_name_var = tk.StringVar(value="")
        self.book_var = tk.StringVar(value="")

        self.book_code_var = tk.StringVar(value="-")
        self.book_title_var = tk.StringVar(value="-")
        self.book_author_var = tk.StringVar(value="-")
        self.book_course_var = tk.StringVar(value="-")
        self.book_state_var = tk.StringVar(value="-")
        self.book_instances_var = tk.StringVar(value="0")
        self.book_total_var = tk.StringVar(value="0")
        self.book_workspace_var = tk.StringVar(value="-")
        self.book_pdf_var = tk.StringVar(value="-")
        self.book_cover_var = tk.StringVar(value="-")

        self.summary_state_var = tk.StringVar(value="Estado: -")
        self.summary_instances_var = tk.StringVar(value="Instancias creadas: 0")
        self.summary_total_var = tk.StringVar(value="Total esperado: N/D")
        self.summary_scan_var = tk.StringVar(value="Escaneados (sesiones): 0")
        self.summary_key_var = tk.StringVar(value="Con clave: 0")
        self.summary_solution_var = tk.StringVar(value="Con solucion: 0")
        self.summary_db_var = tk.StringVar(value="Subidos BD: 0")
        self.summary_db_solution_var = tk.StringVar(value="BD con solucion: 0")
        self.summary_db_pending_var = tk.StringVar(value="BD sin solucion: 0")
        self.summary_db_consistency_var = tk.StringVar(value="BD Consistencia C/I/SR: 0/0/0")
        self.summary_missing_var = tk.StringVar(value="Faltantes: 0")
        self.progress_percent_var = tk.StringVar(value="Avance: N/D")

        self._book_label_to_id: dict[str, int] = {}
        self._current_dashboard = None
        self._current_book = None
        self._current_instances: dict[str, dict] = {}
        self._logger = get_logger("mod9.gui")
        self._preferred_db_name = str(initial_db_name or "").strip()
        self._preferred_book_id = int(initial_book_id) if initial_book_id else None
        self._publisher = ProblemChangeQueueController()
        self._sync_running = False
        self._publish_running = False
        self.pending_status_var = tk.StringVar(value="Cambios nuevos pendientes: 0")

        self._build_ui()
        self._load_databases()

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
        header = ttk.Frame(self)
        header.pack(fill="x", padx=16, pady=(14, 4))
        ttk.Label(header, text="Modulo 9 - Organizador de Libros", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Cataloga libros y crea instancias de trabajo bajo demanda para cada libro.",
            style="SubHeader.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        top = ttk.Frame(self, style="Card.TFrame", padding=14)
        top.pack(fill="x", padx=16, pady=(8, 0))
        ttk.Label(top, text="Base de datos").grid(row=0, column=0, sticky="w")
        self.combo_db = ttk.Combobox(top, state="readonly", textvariable=self.db_name_var, values=[])
        self.combo_db.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        self.combo_db.bind("<<ComboboxSelected>>", lambda _e: self._on_db_change())
        sync_actions = ttk.Frame(top)
        sync_actions.grid(row=0, column=2, sticky="e")
        self.btn_refresh = ttk.Button(sync_actions, text="Refrescar", command=self._refresh_everything, style="Ghost.TButton")
        self.btn_refresh.pack(side="left")
        self.btn_sync = ttk.Button(sync_actions, text="Bajar servidor -> mirror", command=self._sync_from_server, style="Ghost.TButton")
        self.btn_sync.pack(side="left", padx=(8, 0))
        self.btn_publish = ttk.Button(sync_actions, text="Subir cambios nuevos", command=self._publish_pending_changes, style="Accent.TButton")
        self.btn_publish.pack(side="left", padx=(8, 0))
        ttk.Label(top, textvariable=self.pending_status_var, style="Muted.TLabel").grid(row=0, column=3, sticky="w", padx=(12, 0))

        ttk.Label(top, text="Libro").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.combo_book = ttk.Combobox(top, state="readonly", textvariable=self.book_var, values=[])
        self.combo_book.grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=(10, 0))
        self.combo_book.bind("<<ComboboxSelected>>", lambda _e: self._on_book_change())

        actions = ttk.Frame(top)
        actions.grid(row=1, column=2, sticky="e", pady=(10, 0))
        ttk.Button(actions, text="Nuevo libro", command=self._create_book, style="Accent.TButton").pack(side="left")
        ttk.Button(actions, text="Editar", command=self._edit_book, style="Ghost.TButton").pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Eliminar", command=self._delete_book, style="Ghost.TButton").pack(side="left", padx=(8, 0))
        top.columnconfigure(1, weight=1)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=16, pady=(12, 10))
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=5)
        body.columnconfigure(2, weight=4)
        body.rowconfigure(0, weight=1)

        self._build_book_panel(body)
        self._build_instances_panel(body)
        self._build_dashboard_panel(body)

        bottom = ttk.Frame(self, style="Card.TFrame", padding=14)
        bottom.pack(fill="both", expand=False, padx=16, pady=(0, 16))
        bottom.columnconfigure(0, weight=1)
        title = ttk.Frame(bottom)
        title.grid(row=0, column=0, sticky="ew")
        ttk.Label(title, text="Resumen", style="Section.TLabel").pack(side="left")
        ttk.Button(title, text="Copiar resumen", command=self._copy_summary, style="Ghost.TButton").pack(side="right")
        self.txt_log = tk.Text(
            bottom,
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
        self.txt_log.grid(row=1, column=0, sticky="nsew", pady=(10, 0))

    def _build_book_panel(self, parent: ttk.Frame) -> None:
        card = ttk.Frame(parent, style="Card.TFrame", padding=12)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        card.columnconfigure(1, weight=1)
        ttk.Label(card, text="Ficha del libro", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        rows = (
            ("Codigo", self.book_code_var),
            ("Titulo", self.book_title_var),
            ("Autor", self.book_author_var),
            ("Curso", self.book_course_var),
            ("Estado", self.book_state_var),
            ("Instancias creadas", self.book_instances_var),
            ("Total esperado", self.book_total_var),
            ("Carpeta de trabajo", self.book_workspace_var),
            ("PDF principal", self.book_pdf_var),
            ("Imagen de referencia", self.book_cover_var),
        )
        for idx, (label, var) in enumerate(rows, start=1):
            ttk.Label(card, text=label).grid(row=idx, column=0, sticky="nw", pady=(8, 0))
            ttk.Label(card, textvariable=var, wraplength=260).grid(row=idx, column=1, sticky="nw", padx=(10, 0), pady=(8, 0))

    def _build_instances_panel(self, parent: ttk.Frame) -> None:
        card = ttk.Frame(parent, style="Card.TFrame", padding=12)
        card.grid(row=0, column=1, sticky="nsew", padx=8)
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)
        ttk.Label(card, text="Instancias del libro", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.tree_instances = ttk.Treeview(
            card,
            columns=("tipo", "meta", "sesion", "clave", "solucion", "subidos", "bd_sol", "bd_cons", "bd_inc", "bd_sr", "pct", "recursos"),
            show="headings",
            height=14,
        )
        for key, label, width in (
            ("tipo", "Tipo", 90),
            ("meta", "Meta", 70),
            ("sesion", "Sesion", 70),
            ("clave", "Clave", 70),
            ("solucion", "Solucion", 80),
            ("subidos", "Subidos BD", 90),
            ("bd_sol", "BD c/sol", 80),
            ("bd_cons", "BD C", 60),
            ("bd_inc", "BD I", 60),
            ("bd_sr", "BD SR", 60),
            ("pct", "%", 55),
            ("recursos", "Recursos", 170),
        ):
            self.tree_instances.heading(key, text=label)
            self.tree_instances.column(key, width=width, anchor="center")
        self.tree_instances.grid(row=1, column=0, sticky="nsew", pady=(8, 8))
        actions = ttk.Frame(card)
        actions.grid(row=2, column=0, sticky="ew")
        ttk.Button(actions, text="Crear instancia", command=self._create_instance, style="Accent.TButton").pack(side="left")
        ttk.Button(actions, text="Editar seleccionada", command=self._edit_selected_instance, style="Ghost.TButton").pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Eliminar seleccionada", command=self._delete_selected_instance, style="Ghost.TButton").pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Abrir Transcriptor IA", command=self._open_transcriptor, style="Ghost.TButton").pack(side="left", padx=(8, 0))

    def _build_dashboard_panel(self, parent: ttk.Frame) -> None:
        card = ttk.Frame(parent, style="Card.TFrame", padding=12)
        card.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
        card.columnconfigure(0, weight=1)
        card.rowconfigure(4, weight=1)
        ttk.Label(card, text="Dashboard", style="Section.TLabel").grid(row=0, column=0, sticky="w")

        stats_card = ttk.Frame(card, style="ToolbarCard.TFrame", padding=10)
        stats_card.grid(row=1, column=0, sticky="ew", pady=(8, 10))
        for idx, var in enumerate(
            (
                self.summary_state_var,
                self.summary_instances_var,
                self.summary_total_var,
                self.summary_scan_var,
                self.summary_key_var,
                self.summary_solution_var,
                self.summary_db_var,
                self.summary_db_solution_var,
                self.summary_db_pending_var,
                self.summary_db_consistency_var,
                self.summary_missing_var,
            )
        ):
            ttk.Label(stats_card, textvariable=var).grid(row=idx, column=0, sticky="w", pady=(0, 4))

        ttk.Label(card, textvariable=self.progress_percent_var, style="Section.TLabel").grid(row=2, column=0, sticky="w")
        self.progress = ttk.Progressbar(card, orient="horizontal", mode="determinate", style="Accent.Horizontal.TProgressbar")
        self.progress.grid(row=3, column=0, sticky="ew", pady=(8, 12))

        ttk.Label(card, text="Progreso por instancia", style="Muted.TLabel").grid(row=4, column=0, sticky="w")
        self.tree_progress = ttk.Treeview(card, columns=("tipo", "avance"), show="headings", height=6)
        self.tree_progress.heading("tipo", text="Instancia")
        self.tree_progress.heading("avance", text="Avance")
        self.tree_progress.column("tipo", width=120, anchor="center")
        self.tree_progress.column("avance", width=140, anchor="center")
        self.tree_progress.grid(row=5, column=0, sticky="nsew", pady=(8, 0))

    def _load_databases(self) -> None:
        t0 = time.time()
        try:
            dbs = self.controller.listar_bases_datos()
            self._logger.info("ui_load_databases_ok count=%s elapsed=%.3fs", len(dbs), time.time() - t0)
        except Exception as exc:
            self._logger.exception("ui_load_databases_error elapsed=%.3fs err=%s", time.time() - t0, exc)
            self._write_summary(f"ERROR al listar bases de datos: {exc}")
            self.combo_db.configure(values=[])
            return
        self.combo_db.configure(values=dbs)
        if not dbs:
            self.db_name_var.set("")
            self._load_books()
            self._refresh_pending_status()
            return
        if self.db_name_var.get() not in dbs:
            configured_db = self._preferred_db_name or str(os.getenv("DB_NAME", "") or "").strip()
            preferred = next((x for x in dbs if x == configured_db), dbs[0])
            self.db_name_var.set(preferred)
        self._load_books()
        self._refresh_pending_status()

    def _load_books(self) -> None:
        db = self._db_name()
        t0 = time.time()
        self._book_label_to_id.clear()
        if not db:
            self.combo_book.configure(values=[])
            self.book_var.set("")
            self._current_book = None
            self._current_dashboard = None
            self._clear_views()
            return
        try:
            books = self.controller.listar_libros(db)
            self._logger.info("ui_load_books_ok db=%s count=%s elapsed=%.3fs", db, len(books), time.time() - t0)
        except Exception as exc:
            self._logger.exception("ui_load_books_error db=%s elapsed=%.3fs err=%s", db, time.time() - t0, exc)
            self.combo_book.configure(values=[])
            self.book_var.set("")
            self._current_book = None
            self._current_dashboard = None
            self._clear_views()
            self._write_summary(f"ERROR al cargar libros: {exc}")
            return
        labels: list[str] = []
        current_id = self._current_book_id()
        selected_label = ""
        for row in books:
            label = f"{row.get('estado', '-')} | {row.get('codigo', '')} | {row.get('titulo', '')}"
            labels.append(label)
            book_id = int(row["id"])
            self._book_label_to_id[label] = book_id
            if current_id and book_id == current_id:
                selected_label = label
        self.combo_book.configure(values=labels)
        if self._preferred_book_id:
            for label, value in self._book_label_to_id.items():
                if int(value) == int(self._preferred_book_id):
                    selected_label = label
                    break
        if selected_label:
            self.book_var.set(selected_label)
        elif labels:
            self.book_var.set(labels[0])
        else:
            self.book_var.set("")
        self._preferred_book_id = None
        self._on_book_change()

    def _refresh_everything(self) -> None:
        self._load_databases()

    def _refresh_pending_status(self) -> None:
        try:
            pending = int(self._publisher.pending_count())
        except Exception as exc:
            self.pending_status_var.set(f"Cambios nuevos pendientes: error ({exc})")
            return
        self.pending_status_var.set(f"Cambios nuevos pendientes: {pending}")

    def _resolve_sync_script(self) -> Path:
        repo_root = Path(__file__).resolve().parents[2]
        candidates = (
            repo_root.parent / "MathContentStudio" / "scan-math-db" / "scripts" / "sync_math_bank_from_server.ps1",
            repo_root / "scan-math-db" / "scripts" / "sync_math_bank_from_server.ps1",
        )
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise FileNotFoundError("No se encontro sync_math_bank_from_server.ps1.")

    def _resolve_sync_credentials(self) -> tuple[str, str, Path]:
        host = str(os.getenv("MATH_BANK_SERVER_HOST") or "3.225.19.0").strip()
        user = str(os.getenv("MATH_BANK_SERVER_USER") or "ubuntu").strip()
        identity_candidates = []
        env_identity = str(os.getenv("MATH_BANK_IDENTITY_FILE") or "").strip()
        if env_identity:
            identity_candidates.append(Path(env_identity).expanduser())
        identity_candidates.append(Path.home() / "Keys" / "LightsailDefaultKey-us-east-1.pem")
        for candidate in identity_candidates:
            try:
                if candidate.exists():
                    return host, user, candidate.resolve()
            except Exception:
                continue
        raise FileNotFoundError("No se encontro la llave PEM para sincronizar con el servidor.")

    def _set_sync_buttons_state(self, disabled: bool) -> None:
        state = ["disabled"] if disabled else ["!disabled"]
        self.btn_refresh.state(state)
        self.btn_sync.state(state)
        self.btn_publish.state(state)

    def _friendly_publish_error(self, error: Exception) -> str:
        message = str(error or "").strip()
        normalized = message.lower()
        if "server closed the connection unexpectedly" in normalized:
            return (
                "La conexion con PostgreSQL del servidor se cerro de forma inesperada.\n\n"
                "Suele pasar cuando el backend reinicia o corta una sesion activa.\n"
                "La cola local se conserva, asi que puedes reintentar sin perder cambios."
            )
        if "127.0.0.1" in normalized and "15432" in normalized and "connection refused" in normalized:
            try:
                cloud = read_db_profile_config("cloud")
                host = str(cloud.get("host") or "").strip() or "127.0.0.1"
                port = str(cloud.get("port") or "").strip() or "15432"
            except Exception:
                host = "127.0.0.1"
                port = "15432"
            return (
                "No se pudo conectar al servidor.\n\n"
                f"El perfil Cloud apunta a {host}:{port}.\n"
                "Si usas tunel SSH, abre el tunel y vuelve a intentar."
            )
        return message or "Error desconocido al publicar."

    def _sync_from_server(self) -> None:
        if self._sync_running or self._publish_running:
            return
        try:
            pending = int(self._publisher.pending_count())
        except Exception:
            pending = 0
        if pending > 0:
            messagebox.showwarning(
                "Sincronizar mirror",
                "Hay cambios locales pendientes de publicar.\n\n"
                "Publicalos primero para no bloquear la sincronizacion del mirror ni sobrescribir sesiones locales.",
            )
            return
        try:
            script_path = self._resolve_sync_script()
            host, user, identity_file = self._resolve_sync_credentials()
        except Exception as exc:
            self._show_action_feedback("error", f"ERROR al preparar sincronizacion: {exc}")
            return
        if not messagebox.askyesno(
            "Sincronizar mirror",
            "Se bajaran solo los cambios oficiales del servidor al mirror local y luego se reescribiran las sesiones enlazadas.\n\n"
            "Este proceso puede tardar varios minutos.\n\n"
            "Deseas continuar?",
        ):
            return

        self._sync_running = True
        self._set_sync_buttons_state(True)
        self._write_summary("Sincronizando servidor -> mirror -> sesiones locales...")

        def worker() -> None:
            try:
                command = [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script_path),
                    "-ServerHost",
                    host,
                    "-ServerUser",
                    user,
                    "-IdentityFile",
                    str(identity_file),
                ]
                result = subprocess.run(
                    command,
                    cwd=str(script_path.parent.parent),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.after(0, lambda: self._on_sync_done(result))
            except Exception as exc:
                self.after(0, lambda: self._on_sync_done(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_sync_done(self, result) -> None:
        self._sync_running = False
        self._set_sync_buttons_state(False)
        if isinstance(result, Exception):
            self._show_action_feedback("error", f"ERROR al sincronizar mirror: {result}")
            return
        stdout = str(result.stdout or "").strip()
        stderr = str(result.stderr or "").strip()
        if int(result.returncode or 0) != 0:
            detail = stderr or stdout or f"codigo de salida {result.returncode}"
            self._show_action_feedback("error", f"ERROR al sincronizar mirror: {detail}")
            return
        self._load_databases()
        message = "Mirror y sesiones locales sincronizados desde el servidor."
        if stdout:
            message = f"{message}\n\n{stdout}"
        self._write_summary(message)
        messagebox.showinfo("Sincronizar mirror", message)

    def _publish_pending_changes(self) -> None:
        if self._publish_running or self._sync_running:
            return
        try:
            pending = int(self._publisher.pending_count())
        except Exception as exc:
            self._show_action_feedback("error", f"ERROR al leer la cola de cambios: {exc}")
            return
        if not messagebox.askyesno(
            "Publicar cambios",
            f"Problemas pendientes en cola: {pending}\n\n"
            "Tambien se sincronizaran libros, instancias y assets nuevos o modificados.\n"
            "Solo se enviaran cambios nuevos; lo ya consolidado no se vuelve a publicar.\n\n"
            "Deseas continuar?",
        ):
            return

        self._publish_running = True
        self._set_sync_buttons_state(True)
        self._write_summary("Subiendo cambios nuevos del mirror local al servidor...")

        def worker() -> None:
            try:
                summary = self._publisher.publish_pending()
                self.after(0, lambda: self._on_publish_done(summary, None))
            except Exception as exc:
                self.after(0, lambda: self._on_publish_done(None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_publish_done(self, summary: dict | None, error: Exception | None) -> None:
        self._publish_running = False
        self._set_sync_buttons_state(False)
        if error is not None:
            self._show_action_feedback("error", f"ERROR al publicar cambios: {self._friendly_publish_error(error)}")
            self._refresh_pending_status()
            return
        summary = summary or {}
        message = (
            "Subida incremental terminada.\n\n"
            f"Libros insertados: {int(summary.get('books_inserted') or 0)}\n"
            f"Libros actualizados: {int(summary.get('books_updated') or 0)}\n"
            f"Instancias insertadas: {int(summary.get('instances_inserted') or 0)}\n"
            f"Instancias actualizadas: {int(summary.get('instances_updated') or 0)}\n"
            f"Pendientes: {int(summary.get('pending_before') or 0)}\n"
            f"Publicados: {int(summary.get('published') or 0)}\n"
            f"Conflictos: {int(summary.get('conflicts') or 0)}\n"
            f"Fallidos: {int(summary.get('failed') or 0)}"
        )
        self._write_summary(message)
        self._refresh_pending_status()
        messagebox.showinfo("Publicar cambios", message)

    def _on_db_change(self) -> None:
        self._current_book = None
        self._current_dashboard = None
        self._load_books()

    def _on_book_change(self) -> None:
        db = self._db_name()
        book_id = self._current_book_id()
        if not db or not book_id:
            self._current_book = None
            self._current_dashboard = None
            self._current_instances = {}
            self._clear_views()
            return
        try:
            self._current_book = self.controller.obtener_libro(db, book_id)
            self._current_dashboard = self.controller.obtener_dashboard_libro(db, book_id)
            rows = self.controller.listar_instancias_libro(db, book_id)
            self._current_instances = {str(row.get("tipo") or "").strip().lower(): row for row in rows}
        except Exception as exc:
            self._current_book = None
            self._current_dashboard = None
            self._current_instances = {}
            self._clear_views()
            self._write_summary(f"ERROR al cargar dashboard del libro: {exc}")
            return
        self._refresh_book_card()
        self._refresh_instances()
        self._refresh_dashboard()

    def _db_name(self) -> str:
        return self.db_name_var.get().strip()

    def _current_book_id(self) -> int | None:
        label = self.book_var.get().strip()
        if not label:
            return None
        book_id = self._book_label_to_id.get(label)
        return int(book_id) if book_id else None

    def _selected_instance_type(self) -> str | None:
        selection = self.tree_instances.selection()
        if not selection:
            return None
        value = str(selection[0])
        return value or None

    def _natural_instance_sort_key(self, value: object) -> tuple:
        text = str(value or "").strip().lower()
        parts = re.split(r"(\d+)", text)
        key: list[object] = []
        for part in parts:
            if not part:
                continue
            if part.isdigit():
                key.append(int(part))
            else:
                key.append(part)
        return tuple(key)

    def _clear_views(self) -> None:
        for var in (
            self.book_code_var,
            self.book_title_var,
            self.book_author_var,
            self.book_course_var,
            self.book_state_var,
            self.book_workspace_var,
            self.book_pdf_var,
        ):
            var.set("-")
        for var in (self.book_instances_var, self.book_total_var):
            var.set("0")
        self._current_instances = {}
        self.tree_instances.delete(*self.tree_instances.get_children())
        self.tree_progress.delete(*self.tree_progress.get_children())
        self.summary_state_var.set("Estado: -")
        self.summary_instances_var.set("Instancias creadas: 0")
        self.summary_total_var.set("Total esperado: N/D")
        self.summary_scan_var.set("Escaneados (sesiones): 0")
        self.summary_key_var.set("Con clave: 0")
        self.summary_solution_var.set("Con solucion: 0")
        self.summary_db_var.set("Subidos BD: 0")
        self.summary_db_solution_var.set("BD con solucion: 0")
        self.summary_db_pending_var.set("BD sin solucion: 0")
        self.summary_db_consistency_var.set("BD Consistencia C/I/SR: 0/0/0")
        self.summary_missing_var.set("Faltantes: 0")
        self.progress_percent_var.set("Avance: N/D")
        self.progress.configure(value=0.0)
        self._write_summary("Selecciona una base de datos y un libro para ver su progreso.")

    def _refresh_book_card(self) -> None:
        book = self._current_book or {}
        summary = self._current_dashboard
        self.book_code_var.set(str(book.get("codigo") or "-"))
        self.book_title_var.set(str(book.get("titulo") or "-"))
        self.book_author_var.set(str(book.get("autor") or "-"))
        self.book_course_var.set(str(book.get("curso") or "-"))
        self.book_state_var.set(str(book.get("estado") or "-"))
        self.book_workspace_var.set(str(getattr(summary, "workspace_dir", "") or book.get("workspace_dir") or "-"))
        self.book_pdf_var.set(str(book.get("pdf_path") or "-"))
        self.book_cover_var.set(str(book.get("cover_path") or "-"))
        if summary is None:
            self.book_instances_var.set("0")
            self.book_total_var.set("0")
            return
        self.book_instances_var.set(str(summary.total_instancias))
        self.book_total_var.set(str(summary.total_esperado))

    def _refresh_instances(self) -> None:
        self.tree_instances.delete(*self.tree_instances.get_children())
        summary = self._current_dashboard
        if summary is None:
            return
        for stats in sorted(summary.instancias, key=lambda row: self._natural_instance_sort_key(row.tipo)):
            pct_text = "N/D" if stats.total_esperado <= 0 else f"{stats.porcentaje * 100:.1f}%"
            resource_state = f"{stats.session_status}/{stats.soluciones_status}"
            self.tree_instances.insert(
                "",
                "end",
                iid=stats.tipo,
                values=(
                    stats.tipo.capitalize(),
                    stats.total_esperado,
                    stats.escaneados_sesion,
                    stats.con_clave_sesion,
                    stats.con_solucion_sesion,
                    stats.subidos_bd,
                    stats.subidos_bd_con_solucion,
                    stats.subidos_bd_consistentes,
                    stats.subidos_bd_inconsistentes,
                    stats.subidos_bd_sin_revisar,
                    pct_text,
                    resource_state,
                ),
            )
        if not self.tree_instances.selection():
            children = self.tree_instances.get_children()
            if children:
                self.tree_instances.selection_set(children[0])

    def _refresh_dashboard(self) -> None:
        summary = self._current_dashboard
        self.tree_progress.delete(*self.tree_progress.get_children())
        if summary is None:
            return
        self.summary_state_var.set(f"Estado: {summary.estado}")
        self.summary_instances_var.set(f"Instancias creadas: {summary.total_instancias}")
        self.summary_total_var.set("Total esperado: N/D" if summary.total_esperado <= 0 else f"Total esperado: {summary.total_esperado}")
        self.summary_scan_var.set(f"Escaneados (sesiones): {summary.escaneados_sesion_total}")
        self.summary_key_var.set(f"Con clave: {summary.con_clave_sesion_total}")
        self.summary_solution_var.set(f"Con solucion: {summary.con_solucion_sesion_total}")
        self.summary_db_var.set(f"Subidos BD: {summary.subidos_bd_total}")
        self.summary_db_solution_var.set(f"BD con solucion: {summary.subidos_bd_con_solucion_total}")
        self.summary_db_pending_var.set(f"BD sin solucion: {summary.subidos_bd_sin_solucion_total}")
        self.summary_db_consistency_var.set(
            "BD Consistencia C/I/SR: "
            f"{summary.subidos_bd_consistentes_total}/"
            f"{summary.subidos_bd_inconsistentes_total}/"
            f"{summary.subidos_bd_sin_revisar_total}"
        )
        self.summary_missing_var.set(f"Faltantes: {summary.faltantes_total}")
        self.progress_percent_var.set("Avance: N/D" if summary.total_esperado <= 0 else f"Avance: {summary.porcentaje_total * 100:.1f}%")
        self.progress.configure(value=max(min(summary.porcentaje_total * 100.0, 100.0), 0.0))
        for stats in sorted(summary.instancias, key=lambda row: self._natural_instance_sort_key(row.tipo)):
            pct_text = "N/D" if stats.total_esperado <= 0 else f"{stats.porcentaje * 100:.1f}%"
            self.tree_progress.insert("", "end", iid=f"prog-{stats.tipo}", values=(stats.tipo.capitalize(), pct_text))
        self._write_summary(self._build_summary_text(summary))

    def _build_summary_text(self, summary) -> str:
        lines = [
            f"Libro: {summary.titulo}",
            f"Codigo: {summary.codigo}",
            f"Estado: {summary.estado}",
            f"Carpeta de trabajo: {summary.workspace_dir or '-'}",
            f"PDF principal: {summary.pdf_path or '-'} ({summary.pdf_status})",
            "",
            f"Instancias creadas: {summary.total_instancias}",
            f"Total esperado: {summary.total_esperado}",
            f"Escaneados (sesiones): {summary.escaneados_sesion_total}",
            f"Con clave: {summary.con_clave_sesion_total}",
            f"Con solucion: {summary.con_solucion_sesion_total}",
            f"Subidos BD: {summary.subidos_bd_total}",
            f"Subidos BD con solucion: {summary.subidos_bd_con_solucion_total}",
            f"Subidos BD sin solucion: {summary.subidos_bd_sin_solucion_total}",
            (
                "BD Consistencia C/I/SR: "
                f"{summary.subidos_bd_consistentes_total}/"
                f"{summary.subidos_bd_inconsistentes_total}/"
                f"{summary.subidos_bd_sin_revisar_total}"
            ),
            f"Faltantes: {summary.faltantes_total}",
            "Avance: N/D" if summary.total_esperado <= 0 else f"Avance: {summary.porcentaje_total * 100:.1f}%",
            "",
        ]
        for stats in summary.instancias:
            lines.extend(
                [
                    f"[{stats.tipo.upper()}]",
                    f"Meta: {stats.total_esperado}",
                    f"Sesion: {stats.session_status}",
                    f"Escaneados: {stats.escaneados_sesion}",
                    f"Con clave: {stats.con_clave_sesion}",
                    f"Con solucion: {stats.con_solucion_sesion}",
                    f"Subidos BD: {stats.subidos_bd}",
                    f"Subidos BD con solucion: {stats.subidos_bd_con_solucion}",
                    f"Subidos BD sin solucion: {stats.subidos_bd_sin_solucion}",
                    (
                        "BD Consistencia C/I/SR: "
                        f"{stats.subidos_bd_consistentes}/"
                        f"{stats.subidos_bd_inconsistentes}/"
                        f"{stats.subidos_bd_sin_revisar}"
                    ),
                    f"Sesion path: {stats.session_path or '-'} ({stats.session_status})",
                    f"Carpeta de soluciones: {stats.soluciones_dir or '-'} ({stats.soluciones_status})",
                    "",
                ]
            )
        return "\n".join(lines).strip()

    def _write_summary(self, text: str) -> None:
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.insert("1.0", text)
        self.txt_log.configure(state="normal")

    def _copy_summary(self) -> None:
        text = self.txt_log.get("1.0", "end").strip()
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._show_action_feedback("success", "Resumen copiado al portapapeles.")

    def _show_action_feedback(self, kind: str, message: str) -> None:
        self._write_summary(message)
        if kind == "success":
            messagebox.showinfo("Organizador", message)
        else:
            messagebox.showerror("Organizador", message)

    def _select_book_by_id(self, book_id: int) -> None:
        target = None
        for label, value in self._book_label_to_id.items():
            if int(value) == int(book_id):
                target = label
                break
        if not target:
            return
        self.book_var.set(target)
        self._on_book_change()

    def _default_session_path_for_instance(self, book: dict, instance_type: str) -> Path:
        workspace_raw = str((book or {}).get("workspace_dir") or "").strip()
        if workspace_raw:
            try:
                return project_dirs(Path(workspace_raw), str(instance_type or "").strip().lower())["session_path"]
            except Exception:
                pass
        code = str((book or {}).get("codigo") or "").strip() or "libro"
        safe_code = re.sub(r"[^A-Za-z0-9_-]+", "_", code).strip("_").lower() or "libro"
        safe_type = re.sub(r"[^A-Za-z0-9_-]+", "_", str(instance_type or "").strip().lower()).strip("_") or "instancia"
        sessions_dir = Path.cwd() / ".cache" / "transcriptor_runs" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        return sessions_dir / f"sesion_{safe_code}_{safe_type}.json"

    def _ensure_instance_session_path(self, db: str, book: dict, instance: dict) -> str:
        current = str((instance or {}).get("session_path") or "").strip()
        if current:
            return current
        generated = self._default_session_path_for_instance(book, str((instance or {}).get("tipo") or ""))
        payload = BookInstanceUpdateInput(
            libro_id=int(instance["libro_id"]),
            tipo=str(instance["tipo"]),
            total_esperado=int(instance.get("total_esperado") or 0),
            session_path=str(generated),
            soluciones_dir=str(instance.get("soluciones_dir") or ""),
            notas=str(instance.get("notas") or ""),
            activo=bool(instance.get("activo", True)),
        )
        self.controller.actualizar_instancia(db, int(instance["id"]), payload)
        instance["session_path"] = str(generated)
        return str(generated)

    def _create_book(self) -> None:
        db = self._db_name()
        t0 = time.time()
        if not db:
            messagebox.showwarning("Libro", "Selecciona una base de datos primero.")
            return
        data = self._book_dialog("Nuevo libro")
        if not data:
            return
        try:
            book_id = self.controller.crear_libro(db, BookCreateInput(**data))
            created = self.controller.obtener_libro(db, book_id)
            if not created:
                raise ValueError("El libro fue insertado pero no se pudo reconsultar en la base.")
            self._load_books()
            self._select_book_by_id(book_id)
            self._show_action_feedback("success", f"Guardado OK: [{created['id']}] {created['codigo']} | {created['titulo']}")
            self._logger.info("ui_create_book_ok db=%s book_id=%s elapsed=%.3fs", db, book_id, time.time() - t0)
        except Exception as exc:
            self._logger.exception("ui_create_book_error db=%s elapsed=%.3fs err=%s", db, time.time() - t0, exc)
            self._show_action_feedback("error", f"ERROR al guardar libro: {exc}")

    def _edit_book(self) -> None:
        db = self._db_name()
        t0 = time.time()
        book_id = self._current_book_id()
        if not db or not book_id:
            return
        book = self.controller.obtener_libro(db, book_id)
        if not book:
            return
        data = self._book_dialog("Editar libro", initial=book)
        if not data:
            return
        try:
            self.controller.actualizar_libro(db, book_id, BookUpdateInput(**data))
            updated = self.controller.obtener_libro(db, book_id)
            if not updated:
                raise ValueError("El libro fue actualizado pero no se pudo reconsultar en la base.")
            self._load_books()
            self._select_book_by_id(book_id)
            self._show_action_feedback("success", f"Actualizado OK: [{updated['id']}] {updated['codigo']} | {updated['titulo']}")
            self._logger.info("ui_edit_book_ok db=%s book_id=%s elapsed=%.3fs", db, book_id, time.time() - t0)
        except Exception as exc:
            self._logger.exception("ui_edit_book_error db=%s book_id=%s elapsed=%.3fs err=%s", db, book_id, time.time() - t0, exc)
            self._show_action_feedback("error", f"ERROR al actualizar libro: {exc}")

    def _delete_book(self) -> None:
        db = self._db_name()
        book_id = self._current_book_id()
        if not db or not book_id:
            return
        if not messagebox.askyesno("Eliminar", "Se eliminara el libro y todas sus instancias de trabajo."):
            return
        try:
            self.controller.eliminar_libro(db, book_id)
            self._load_books()
        except Exception as exc:
            self._show_action_feedback("error", f"ERROR al eliminar libro: {exc}")

    def _create_instance(self) -> None:
        db = self._db_name()
        book_id = self._current_book_id()
        if not db or not book_id:
            return
        data = self._instance_dialog("Crear instancia", "instancia")
        if not data:
            return
        try:
            payload = BookInstanceInput(
                libro_id=int(book_id),
                tipo=str(data.get("tipo") or "").strip(),
                total_esperado=int(data.get("total_esperado") or 0),
                session_path=str(data.get("session_path") or ""),
                soluciones_dir=str(data.get("soluciones_dir") or ""),
                notas=str(data.get("notas") or ""),
                activo=bool(data.get("activo", True)),
            )
            self.controller.crear_instancia(db, payload)
            self._on_book_change()
            self._show_action_feedback("success", f"Instancia creada: {payload.tipo}")
        except Exception as exc:
            self._show_action_feedback("error", f"ERROR al crear instancia: {exc}")

    def _edit_selected_instance(self) -> None:
        tipo = self._selected_instance_type()
        if not tipo:
            messagebox.showwarning("Instancias", "Selecciona una instancia para editar.")
            return
        self._edit_instance(tipo)

    def _delete_selected_instance(self) -> None:
        tipo = self._selected_instance_type()
        if not tipo:
            messagebox.showwarning("Instancias", "Selecciona una instancia para eliminar.")
            return
        db = self._db_name()
        book_id = self._current_book_id()
        if not db or not book_id:
            return
        instance = self.controller.obtener_instancia(db, book_id, tipo)
        if not instance:
            self._show_action_feedback("error", f"ERROR: no se encontro la instancia {tipo}.")
            return
        if not messagebox.askyesno(
            "Eliminar instancia",
            (
                f"Se eliminara la instancia '{tipo}' de la base seleccionada.\n\n"
                "Tambien se borraran los problemas locales y los cambios pendientes vinculados a esa instancia.\n"
                "Los archivos de sesion y soluciones en disco no se eliminaran.\n\n"
                "Nota: este modulo no publica borrados automaticamente al servidor."
            ),
        ):
            return
        try:
            summary = self.controller.eliminar_instancia(db, int(instance["id"]))
            self._on_book_change()
            self._show_action_feedback(
                "success",
                "Instancia eliminada: "
                f"{tipo} | problemas={int(summary.get('problems_deleted') or 0)} "
                f"| cola={int(summary.get('pending_deleted') or 0)}",
            )
        except Exception as exc:
            self._show_action_feedback("error", f"ERROR al eliminar instancia {tipo}: {exc}")

    def _edit_instance(self, tipo: str) -> None:
        db = self._db_name()
        book_id = self._current_book_id()
        if not db or not book_id:
            return
        instance = self.controller.obtener_instancia(db, book_id, tipo)
        if not instance:
            self._show_action_feedback("error", f"ERROR: no se encontro la instancia {tipo}.")
            return
        data = self._instance_dialog(f"Editar {tipo}", tipo, initial=instance)
        if not data:
            return
        try:
            payload = BookInstanceUpdateInput(
                libro_id=int(book_id),
                tipo=str(data.get("tipo") or "").strip(),
                total_esperado=int(data.get("total_esperado") or 0),
                session_path=str(data.get("session_path") or ""),
                soluciones_dir=str(data.get("soluciones_dir") or ""),
                notas=str(data.get("notas") or ""),
                activo=bool(data.get("activo", True)),
            )
            self.controller.actualizar_instancia(db, int(instance["id"]), payload)
            self._on_book_change()
            self._show_action_feedback("success", f"Instancia actualizada: {tipo}")
        except Exception as exc:
            self._show_action_feedback("error", f"ERROR al actualizar instancia {tipo}: {exc}")

    def _open_transcriptor(self) -> None:
        db = self._db_name()
        instance_type = self._selected_instance_type()
        if not instance_type:
            messagebox.showwarning("Transcriptor IA", "Selecciona una instancia primero.")
            return
        if not db:
            messagebox.showwarning("Transcriptor IA", "Selecciona una base de datos primero.")
            return
        summary = self._current_dashboard
        book = self._current_book
        book_id = self._current_book_id()
        if summary is None or not book or not book_id:
            return
        instance = self.controller.obtener_instancia(db, book_id, instance_type)
        if not instance:
            messagebox.showerror("Transcriptor IA", f"No se encontro la instancia {instance_type}.")
            return
        try:
            session_path = self._ensure_instance_session_path(db, book, instance)
        except Exception as exc:
            messagebox.showerror("Transcriptor IA", f"No se pudo preparar la sesion enlazada.\n{exc}")
            return
        stats = next((row for row in summary.instancias if row.tipo == instance_type), None)
        if stats is None:
            messagebox.showerror("Transcriptor IA", f"No se pudo resolver el resumen de la instancia '{instance_type}'.")
            return
        try:
            from modulos.modulo0_transcriptor.gui_transcriptor import TranscriptorWindow

            window = TranscriptorWindow(
                self,
                initial_db_name=db,
                initial_book_code=str(book.get("codigo") or "").strip(),
                initial_instance_type=instance_type,
                initial_pdf_path=str(getattr(summary, "pdf_path", "") or "").strip(),
                linked_session_path=session_path,
                initial_solutions_dir=str(stats.soluciones_dir or "").strip(),
                initial_project_name=str(book.get("titulo") or "").strip(),
            )
        except Exception as exc:
            messagebox.showerror("Transcriptor IA", f"No se pudo abrir Modulo 0.\n{exc}")
            return
        bootstrap_status = str(getattr(window, "_linked_session_bootstrap_status", "none") or "none").strip().lower()
        if bootstrap_status == "created":
            session_hint = "Sesion enlazada creada e inicializada."
        elif bootstrap_status == "loaded":
            session_hint = "Sesion enlazada cargada."
        elif bootstrap_status == "error":
            session_hint = "No se pudo cargar la sesion enlazada."
        else:
            session_hint = "Sesion enlazada preparada."
        messagebox.showinfo(
            "Transcriptor IA",
            "Transcriptor IA abierto.\n\n"
            f"{session_hint}\n\n"
            f"Instancia: {stats.tipo}\n"
            f"PDF principal: {summary.pdf_path or '(no definido)'}\n"
            f"Sesion: {session_path or '(no definida)'}\n"
            f"Carpeta de soluciones: {stats.soluciones_dir or '(no definida)'}\n\n"
            "La sesion ya quedo enlazada a esta instancia.",
        )
        if bootstrap_status == "created":
            self._on_book_change()

    def _book_dialog(self, title: str, initial: dict | None = None) -> dict | None:
        fields = [
            {"name": "workspace_dir", "label": "Carpeta base del proyecto (opcional)", "kind": "dir"},
            {"name": "codigo", "label": "Codigo (opcional)"},
            {"name": "titulo", "label": "Titulo (opcional)"},
            {"name": "autor", "label": "Autor"},
            {"name": "editorial", "label": "Editorial"},
            {"name": "edicion", "label": "Edicion"},
            {"name": "curso", "label": "Curso"},
            {"name": "pdf_path", "label": "Ruta del archivo PDF (opcional)", "kind": "file"},
            {"name": "cover_path", "label": "Imagen del libro (opcional)", "kind": "file"},
            {"name": "estado", "label": "Estado", "kind": "choice", "values": list(BOOK_STATES), "default": "pendiente"},
            {"name": "notas", "label": "Notas"},
            {"name": "activo", "label": "Activo", "kind": "bool", "default": True},
        ]
        dlg = _FormDialog(self, title, fields, initial=initial)
        return dlg.result

    def _instance_dialog(
        self,
        title: str,
        tipo: str,
        initial: dict | None = None,
    ) -> dict | None:
        seed = dict(initial or {})
        fields = [
            {"name": "tipo", "label": "Nombre de instancia"},
            {"name": "total_esperado", "label": "Total esperado", "kind": "int", "default": 0},
            {"name": "session_path", "label": "Ruta sesion (opcional)"},
            {"name": "soluciones_dir", "label": "Carpeta de soluciones (opcional)"},
            {"name": "notas", "label": "Notas"},
            {"name": "activo", "label": "Activo", "kind": "bool", "default": True},
        ]
        seed["tipo"] = seed.get("tipo", tipo)
        dlg = _FormDialog(self, title, fields, initial=seed)
        return dlg.result
