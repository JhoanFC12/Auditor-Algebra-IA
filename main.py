import json
import os
import socket
import subprocess
import sys
import time
import tkinter as tk
from tkinter import messagebox
from pathlib import Path
from utils.runtime_log import get_logger, get_log_file_path

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = None  # type: ignore[assignment]

try:
    from tkinterdnd2 import TkinterDnD  # type: ignore

    BaseTk = TkinterDnD.Tk
except Exception:
    BaseTk = tk.Tk


BG = "#edf2f7"
CONTENT_BG = "#f4f7fb"
SIDEBAR_BG = "#0b1220"
SIDEBAR_PANEL = "#111b2e"
SIDEBAR_TEXT = "#e2e8f0"
SIDEBAR_MUTED = "#94a3b8"
CARD_BG = "#ffffff"
CARD_BORDER = "#dbe2ea"
CARD_BORDER_HOVER = "#93c5fd"
TEXT = "#0f172a"
MUTED = "#475569"
PRIMARY = "#0f766e"
PRIMARY_HOVER = "#0b5f5a"
SECONDARY = "#1e293b"
SECONDARY_HOVER = "#334155"
BADGE_BG = "#e0f2fe"
BADGE_FG = "#0c4a6e"
SELECTOR_BG = "#f8fafc"

DB_PROFILE_LABELS = {
    "local_mirror": "Local mirror",
    "cloud": "Cloud",
}
DEFAULT_SHARED_DB_NAME = "mathcontentstudio"
DEFAULT_LOCAL_MIRROR_DB_NAME = "mathcontentstudio_local_mirror"
LOGGER = get_logger("main")


def _is_frozen_app() -> bool:
    return bool(getattr(sys, "frozen", False))


def _launcher_cwd() -> str:
    if _is_frozen_app():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _db_profile_state_path() -> Path:
    return Path(_launcher_cwd()) / ".db_profile_state.json"


def _machine_key() -> str:
    return str(os.getenv("COMPUTERNAME", "") or socket.gethostname() or "default").strip().lower() or "default"


def _read_saved_db_profile() -> str:
    path = _db_profile_state_path()
    if not path.exists():
        return ""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(raw, dict):
        return ""
    value = str(raw.get(_machine_key(), "") or "").strip().lower()
    return value if value in DB_PROFILE_LABELS else ""


def _save_db_profile(profile_name: str) -> None:
    profile_key = str(profile_name or "").strip().lower()
    if profile_key not in DB_PROFILE_LABELS:
        return
    path = _db_profile_state_path()
    payload: dict[str, str] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                payload = {str(k): str(v) for k, v in existing.items()}
        except Exception:
            payload = {}
    payload[_machine_key()] = profile_key
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _load_dotenv_if_present() -> None:
    if load_dotenv is None or _is_frozen_app():
        return
    root = Path(__file__).resolve().parent
    preferred = root / ".env.local"
    fallback = root / ".env"
    if preferred.exists():
        load_dotenv(preferred, override=False)
        return
    if fallback.exists():
        load_dotenv(fallback, override=False)


_load_dotenv_if_present()


def _read_env_value(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or default).strip()


def _is_truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "si"}


def _is_local_host(host: str) -> bool:
    return str(host or "").strip().lower() in {"", "localhost", "127.0.0.1", "::1"}


def _port_is_open(host: str, port: str, timeout: float = 1.0) -> bool:
    try:
        port_num = int(str(port).strip())
    except Exception:
        return False
    try:
        with socket.create_connection((str(host).strip(), port_num), timeout=timeout):
            return True
    except Exception:
        return False


def _build_db_connection_hint(config: dict[str, str]) -> str:
    hints: list[str] = []
    if _is_local_host(config["host"]) and not _port_is_open(config["host"], config["port"]):
        hints.append("No hay un servidor PostgreSQL escuchando en ese host/puerto.")
        profile = str(config.get("profile", "") or "").strip().lower()
        if profile == "local_mirror":
            hints.append("El perfil Local mirror espera una base local en 127.0.0.1:5432.")
            hints.append("Inicia PostgreSQL local o sincroniza primero el espejo mathcontentstudio_local_mirror.")
            hints.append("Si prefieres trabajar contra el servidor remoto, cambia al perfil Cloud y abre el tunel SSH local en 15432.")
        elif profile == "cloud" and _is_local_host(config["host"]) and str(config.get("port", "")).strip() == "15432":
            hints.append("El perfil Cloud esta apuntando a un tunel SSH local en 127.0.0.1:15432.")
            hints.append("Abre start_math_bank_db_tunnel.ps1 del repo scan-math-db con tu ServerHost e IdentityFile.")
        else:
            hints.append("Si trabajas en esta misma maquina, inicia el servicio PostgreSQL.")
    if config["profile"] == "cloud":
        if not str(config["host"]).strip():
            hints.append("Configura DB_CLOUD_HOST, DB_CLOUD_PORT, DB_CLOUD_USER y DB_CLOUD_PASSWORD.")
        elif _is_local_host(config["host"]):
            hints.append("El perfil Cloud esta usando una conexion local como respaldo temporal.")
            hints.append("Cuando migres a Neon, reemplaza DB_CLOUD_HOST por el endpoint remoto.")
        if not _is_local_host(config["host"]) and str(config.get("sslmode", "")).strip().lower() != "require":
            hints.append("Neon normalmente requiere SSL/TLS.")
            hints.append("Usa DB_CLOUD_SSLMODE=require.")
    if not hints:
        hints.append("Revisa host, puerto, usuario y contrasena del perfil.")
    return "\n".join(hints)


def _default_db_profile() -> str:
    saved = _read_saved_db_profile()
    if saved in DB_PROFILE_LABELS:
        return saved
    candidate = _read_env_value("DB_PROFILE_DEFAULT", _read_env_value("DB_PROFILE", "cloud")).lower()
    if candidate in DB_PROFILE_LABELS:
        return candidate
    return "cloud"


def _shared_db_name() -> str:
    return _read_env_value("DB_SHARED_NAME", _read_env_value("DB_NAME", DEFAULT_SHARED_DB_NAME)) or DEFAULT_SHARED_DB_NAME


def _resolve_db_profile(profile_name: str) -> dict[str, str]:
    profile_key = profile_name.strip().lower()
    if profile_key not in DB_PROFILE_LABELS:
        raise ValueError(f"Perfil de BD no soportado: {profile_name}")
    prefix = f"DB_{profile_key.upper()}_"
    if profile_key == "cloud":
        host = _read_env_value(f"{prefix}HOST", _read_env_value("DB_HOST", ""))
        port = _read_env_value(f"{prefix}PORT", _read_env_value("DB_PORT", "5432"))
        user = _read_env_value(f"{prefix}USER", _read_env_value("DB_USER", "postgres"))
        password = _read_env_value(f"{prefix}PASSWORD", _read_env_value("DB_PASSWORD", ""))
        sslmode = _read_env_value(f"{prefix}SSLMODE", _read_env_value("DB_SSLMODE", "disable")) or "disable"
        sslrootcert = _read_env_value(f"{prefix}SSLROOTCERT", _read_env_value("DB_SSLROOTCERT", ""))
        db_name = _read_env_value(f"{prefix}NAME", _read_env_value("DB_SHARED_NAME", _read_env_value("DB_NAME", DEFAULT_SHARED_DB_NAME)) or DEFAULT_SHARED_DB_NAME)
    elif profile_key == "local_mirror":
        host = _read_env_value(f"{prefix}HOST", _read_env_value("DB_HOST", "127.0.0.1"))
        port = _read_env_value(f"{prefix}PORT", "5432")
        user = _read_env_value(f"{prefix}USER", "postgres")
        password = _read_env_value(f"{prefix}PASSWORD", "postgres")
        sslmode = _read_env_value(f"{prefix}SSLMODE", "disable") or "disable"
        sslrootcert = _read_env_value(f"{prefix}SSLROOTCERT", "")
        db_name = _read_env_value(f"{prefix}NAME", DEFAULT_LOCAL_MIRROR_DB_NAME) or DEFAULT_LOCAL_MIRROR_DB_NAME
    else:
        raise ValueError(f"Perfil de BD no soportado: {profile_name}")
    return {
        "profile": profile_key,
        "label": DB_PROFILE_LABELS[profile_key],
        "host": host,
        "port": port,
        "name": db_name,
        "user": user,
        "password": password,
        "sslmode": sslmode,
        "sslrootcert": sslrootcert,
    }


def _apply_db_profile(profile_name: str) -> dict[str, str]:
    config = _resolve_db_profile(profile_name)
    LOGGER.info(
        "db_profile_apply profile=%s host=%s port=%s db=%s sslmode=%s",
        config["profile"],
        config["host"],
        config["port"],
        config["name"],
        config["sslmode"],
    )
    os.environ["DB_PROFILE"] = config["profile"]
    os.environ["DB_PROFILE_LABEL"] = config["label"]
    os.environ["DB_HOST"] = config["host"]
    os.environ["DB_PORT"] = config["port"]
    os.environ["DB_NAME"] = config["name"]
    os.environ["DB_USER"] = config["user"]
    os.environ["DB_PASSWORD"] = config["password"]
    os.environ["DB_SSLMODE"] = config["sslmode"]
    if str(config.get("sslrootcert", "")).strip():
        os.environ["DB_SSLROOTCERT"] = str(config["sslrootcert"]).strip()
    else:
        os.environ.pop("DB_SSLROOTCERT", None)
    return config


def _current_db_config() -> dict[str, str]:
    return {
        "profile": _read_env_value("DB_PROFILE", _default_db_profile()) or _default_db_profile(),
        "label": _read_env_value("DB_PROFILE_LABEL", _read_env_value("DB_PROFILE", _default_db_profile())) or _default_db_profile(),
        "host": _read_env_value("DB_HOST", "localhost"),
        "port": _read_env_value("DB_PORT", "5432"),
        "name": _read_env_value("DB_NAME", DEFAULT_SHARED_DB_NAME) or DEFAULT_SHARED_DB_NAME,
        "sslmode": _read_env_value("DB_SSLMODE", "require") or "require",
    }


def _describe_db_connection_error(exc: Exception) -> tuple[str, str]:
    base_error = exc.__cause__ if exc.__cause__ is not None else exc
    raw = str(base_error).strip() or str(exc).strip() or repr(exc)
    lower = raw.lower()
    if "could not translate host name" in lower or "getaddrinfo failed" in lower or "unknown host" in lower:
        return "No se pudo resolver el host de la base de datos.", raw
    if "password authentication failed" in lower or "authentication failed" in lower:
        return "Las credenciales de acceso fueron rechazadas.", raw
    if "ssl" in lower or "tls" in lower or "certificate" in lower:
        return "La negociacion TLS/SSL fallo. Revisa DB_SSLMODE y certificados.", raw
    if "timeout expired" in lower or "timed out" in lower:
        return "La conexion expiro. Revisa internet, firewall o disponibilidad del servicio.", raw
    if "connection refused" in lower or "could not connect to server" in lower or "no route to host" in lower:
        return "No se pudo alcanzar el servidor PostgreSQL. Revisa red, firewall, host y puerto.", raw
    return "No se pudo establecer la conexion con PostgreSQL.", raw


def _run_db_profile_selector() -> str | None:
    return _default_db_profile()


def _configure_startup_db_profile_legacy() -> bool:
    selected_profile: str | None = None
    if False:
        selected_profile = _default_db_profile()
    else:
        selected_profile = _run_db_profile_selector()
        if selected_profile is None:
            return False

    config = _apply_db_profile(selected_profile)
    try:
        from database.connection import DatabaseManager

        dbs = DatabaseManager().listar_bases_datos()
    except Exception as exc:
        messagebox.showerror(
            "Conexion de base de datos",
            "No se pudo conectar con el perfil seleccionado.\n"
            f"Perfil: {config['label']}\n"
            f"Host: {config['host']}:{config['port']}\n\n{exc}",
        )
        return False

    if not dbs:
        messagebox.showerror(
            "Conexion de base de datos",
            "No se pudo conectar con el perfil seleccionado.\n"
            f"Perfil: {config['label']}\n"
            f"Host: {config['host']}:{config['port']}\n\n"
            f"{_build_db_connection_hint(config)}",
        )
        return False

    if not dbs:
        messagebox.showerror(
            "Conexion de base de datos",
            "No se pudo conectar con el perfil seleccionado.\n"
            f"Perfil: {config['label']}\n"
            f"Host: {config['host']}:{config['port']}\n\n"
            "Revisa host, puerto, usuario y contraseña del perfil.",
        )
        return False
    _save_db_profile(selected_profile)
    return True


class Launcher(BaseTk):
    def __init__(self, *, open_biblioteca: bool = False):
        super().__init__()
        self.title("MathContentStudio")
        self.geometry("1120x740")
        self.minsize(980, 640)
        self._maximize_window()
        self.configure(bg=BG)

        self.db_host = (os.getenv("DB_HOST", "localhost") or "localhost").strip()
        self.db_port = (os.getenv("DB_PORT", "5432") or "5432").strip()
        self.db_name = (os.getenv("DB_NAME", DEFAULT_SHARED_DB_NAME) or DEFAULT_SHARED_DB_NAME).strip()
        self.db_sslmode = (_read_env_value("DB_SSLMODE", "require") or "require").strip()
        self.db_profile_label = (_read_env_value("DB_PROFILE_LABEL", _read_env_value("DB_PROFILE", "cloud")) or "cloud").strip()
        self.show_experimental_var = tk.BooleanVar(value=False)
        self.experimental_state_var = tk.StringVar(value="Experimental: oculto")

        self._build_ui()
        self._bind_shortcuts()
        self._toggle_experimental_ui()
        if open_biblioteca:
            self.after(350, self._open_mod10)

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
        shell = tk.Frame(self, bg=BG)
        shell.pack(fill="both", expand=True)

        sidebar = tk.Frame(shell, bg=SIDEBAR_BG, width=300)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        content = tk.Frame(shell, bg=CONTENT_BG)
        content.pack(side="left", fill="both", expand=True)

        self._build_sidebar(sidebar)
        self._build_content(content)

    def _build_sidebar(self, parent: tk.Frame) -> None:
        top = tk.Frame(parent, bg=SIDEBAR_BG)
        top.pack(fill="x", padx=18, pady=(20, 10))

        tk.Label(
            top,
            text="MathContentStudio",
            bg=SIDEBAR_BG,
            fg=SIDEBAR_TEXT,
            font=("Segoe UI Semibold", 16),
        ).pack(anchor="w")
        tk.Label(
            top,
            text="Launcher de trabajo\npara contenido matematico",
            bg=SIDEBAR_BG,
            fg=SIDEBAR_MUTED,
            justify="left",
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(6, 0))

        nav = tk.Frame(parent, bg=SIDEBAR_BG)
        nav.pack(fill="x", padx=14, pady=(14, 10))
        tk.Label(
            nav,
            text="ACCESOS",
            bg=SIDEBAR_BG,
            fg=SIDEBAR_MUTED,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", padx=4, pady=(0, 6))
        self._sidebar_button(nav, "LaTeX -> Word", self._open_mod7, primary=True)
        self._sidebar_button(nav, "Editor SVG", self._open_mod8_svg)
        self._sidebar_button(nav, "Editor JSON", self._open_mod6)
        self._sidebar_button(nav, "Organizador de libros", self._open_mod9)
        self._sidebar_button(nav, "Biblioteca", self._open_mod10)
        self._sidebar_button(nav, "Auditor entrenamiento", self._open_mod12)

        exp = tk.Frame(parent, bg=SIDEBAR_BG)
        exp.pack(fill="x", padx=18, pady=(4, 8))
        tk.Checkbutton(
            exp,
            text="Mostrar modulos experimentales",
            variable=self.show_experimental_var,
            command=self._toggle_experimental_ui,
            bg=SIDEBAR_BG,
            fg=SIDEBAR_TEXT,
            selectcolor=SIDEBAR_BG,
            activebackground=SIDEBAR_BG,
            activeforeground=SIDEBAR_TEXT,
            highlightthickness=0,
            font=("Segoe UI", 10),
        ).pack(anchor="w")

        status_panel = tk.Frame(
            parent,
            bg=SIDEBAR_PANEL,
            highlightbackground="#1f2b44",
            highlightthickness=1,
            bd=0,
        )
        status_panel.pack(fill="x", padx=18, pady=(10, 8))
        tk.Label(
            status_panel,
            text="Estado de entorno",
            bg=SIDEBAR_PANEL,
            fg=SIDEBAR_TEXT,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=12, pady=(10, 6))
        tk.Label(
            status_panel,
            text=f"Perfil: {self.db_profile_label}",
            bg=SIDEBAR_PANEL,
            fg=SIDEBAR_MUTED,
            font=("Consolas", 9),
        ).pack(anchor="w", padx=12, pady=(0, 2))
        tk.Label(
            status_panel,
            text=f"SSL: {self.db_sslmode}",
            bg=SIDEBAR_PANEL,
            fg=SIDEBAR_MUTED,
            font=("Consolas", 9),
        ).pack(anchor="w", padx=12, pady=(0, 2))
        tk.Label(
            status_panel,
            text=f"DB: {self.db_host}:{self.db_port}",
            bg=SIDEBAR_PANEL,
            fg=SIDEBAR_MUTED,
            font=("Consolas", 9),
        ).pack(anchor="w", padx=12, pady=(0, 2))
        tk.Label(
            status_panel,
            text=f"Base: {self.db_name}",
            bg=SIDEBAR_PANEL,
            fg=SIDEBAR_MUTED,
            font=("Consolas", 9),
        ).pack(anchor="w", padx=12, pady=(0, 2))
        tk.Label(
            status_panel,
            textvariable=self.experimental_state_var,
            bg=SIDEBAR_PANEL,
            fg=SIDEBAR_MUTED,
            font=("Consolas", 9),
        ).pack(anchor="w", padx=12, pady=(0, 12))

        shortcuts = tk.Frame(
            parent,
            bg=SIDEBAR_PANEL,
            highlightbackground="#1f2b44",
            highlightthickness=1,
            bd=0,
        )
        shortcuts.pack(fill="x", padx=18, pady=(4, 10))
        tk.Label(
            shortcuts,
            text="Atajos",
            bg=SIDEBAR_PANEL,
            fg=SIDEBAR_TEXT,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=12, pady=(10, 4))
        tk.Label(
            shortcuts,
            text="Ctrl+7 LaTeX   Ctrl+8 SVG\nCtrl+6 Editor JSON  Ctrl+9 Organizador\nCtrl+B Biblioteca  Ctrl+E Experimental",
            bg=SIDEBAR_PANEL,
            fg=SIDEBAR_MUTED,
            justify="left",
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=12, pady=(0, 12))

        exit_btn = tk.Button(
            parent,
            text="Salir",
            command=self.destroy,
            bg=SECONDARY,
            fg=SIDEBAR_TEXT,
            activebackground=SECONDARY_HOVER,
            activeforeground=SIDEBAR_TEXT,
            relief="flat",
            bd=0,
            font=("Segoe UI", 10, "bold"),
            cursor="hand2",
            padx=14,
            pady=8,
        )
        self._bind_button_hover(exit_btn, SECONDARY, SECONDARY_HOVER)
        exit_btn.pack(side="bottom", anchor="w", padx=18, pady=18)

    def _build_content(self, parent: tk.Frame) -> None:
        content_shell = tk.Frame(parent, bg=CONTENT_BG)
        content_shell.pack(fill="both", expand=True)

        self.content_canvas = tk.Canvas(
            content_shell,
            bg=CONTENT_BG,
            highlightthickness=0,
            bd=0,
        )
        self.content_canvas.pack(side="left", fill="both", expand=True)

        self.content_scrollbar = tk.Scrollbar(content_shell, orient="vertical", command=self.content_canvas.yview)
        self.content_scrollbar.pack(side="right", fill="y")
        self.content_canvas.configure(yscrollcommand=self.content_scrollbar.set)

        self.content_body = tk.Frame(self.content_canvas, bg=CONTENT_BG)
        self.content_window = self.content_canvas.create_window((0, 0), window=self.content_body, anchor="nw")
        self.content_body.bind("<Configure>", self._on_content_body_configure)
        self.content_canvas.bind("<Configure>", self._on_content_canvas_configure)
        self.bind_all("<MouseWheel>", self._on_content_mousewheel, add="+")

        hero = tk.Frame(
            self.content_body,
            bg=CARD_BG,
            highlightbackground=CARD_BORDER,
            highlightthickness=1,
            bd=0,
        )
        hero.pack(fill="x", padx=24, pady=(24, 12))

        hero_row = tk.Frame(hero, bg=CARD_BG)
        hero_row.pack(fill="x", padx=18, pady=(16, 10))

        hero_left = tk.Frame(hero_row, bg=CARD_BG)
        hero_left.pack(side="left", fill="x", expand=True)

        tk.Label(
            hero_left,
            text="Workspace",
            bg=CARD_BG,
            fg=TEXT,
            font=("Segoe UI Semibold", 24),
        ).pack(anchor="w")
        tk.Label(
            hero_left,
            text="Flujo recomendado: convertir, editar desde JSON en local y luego publicar cambios al servidor.",
            bg=CARD_BG,
            fg=MUTED,
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(4, 0))

        hero_status = tk.Frame(
            hero_row,
            bg="#f8fafc",
            highlightbackground=CARD_BORDER,
            highlightthickness=1,
            bd=0,
        )
        hero_status.pack(side="right", padx=(16, 0))
        tk.Label(
            hero_status,
            text="Conexion activa",
            bg="#f8fafc",
            fg=TEXT,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", padx=10, pady=(8, 2))
        tk.Label(
            hero_status,
            text=f"{self.db_profile_label}\n{self.db_host}:{self.db_port}\n{self.db_name}\nssl={self.db_sslmode}",
            bg="#f8fafc",
            fg=MUTED,
            font=("Consolas", 9),
        ).pack(anchor="w", padx=10, pady=(0, 8))

        actions = tk.Frame(hero, bg=CARD_BG)
        actions.pack(anchor="w", padx=18, pady=(2, 16))
        self._action_button(actions, "Abrir LaTeX -> Word", self._open_mod7, hotkey="Ctrl+7", primary=True).pack(
            side="left", padx=(0, 10)
        )
        self._action_button(actions, "Abrir Editor SVG", self._open_mod8_svg, hotkey="Ctrl+8", primary=True).pack(
            side="left", padx=(0, 10)
        )
        self._action_button(actions, "Organizar libros", self._open_mod9, hotkey="Ctrl+9", primary=False).pack(
            side="left"
        )
        self._action_button(actions, "Biblioteca", self._open_mod10, hotkey="Ctrl+B", primary=False).pack(
            side="left", padx=(10, 0)
        )
        self._action_button(actions, "Publicar cambios", self._open_mod11, hotkey="Ctrl+P", primary=False).pack(
            side="left", padx=(10, 0)
        )

        tk.Label(
            self.content_body,
            text="Workflows",
            bg=CONTENT_BG,
            fg=TEXT,
            font=("Segoe UI", 14, "bold"),
        ).pack(anchor="w", padx=24, pady=(4, 2))

        self.productive_grid = tk.Frame(self.content_body, bg=CONTENT_BG)
        self.productive_grid.pack(fill="both", expand=False, padx=24, pady=(4, 8))

        self._add_workflow_card(
            self.productive_grid,
            row=0,
            col=0,
            title="LaTeX -> Word",
            description="Convierte archivos .tex a .docx con plantilla y soporte de imagenes.",
            button_text="Abrir modulo",
            command=self._open_mod7,
            tag="Produccion",
            hotkey="Ctrl+7",
        )
        self._add_workflow_card(
            self.productive_grid,
            row=0,
            col=1,
            title="Editor SVG",
            description="Editor grafico SVG avanzado para crear y ajustar diagramas matematicos.",
            button_text="Abrir modulo",
            command=self._open_mod8_svg,
            tag="Produccion",
            hotkey="Ctrl+8",
        )
        self._add_workflow_card(
            self.productive_grid,
            row=1,
            col=0,
            title="Editor de practica JSON",
            description="Carga un JSON de seleccion, edita problemas en local y deja la cola lista para publicar.",
            button_text="Abrir modulo",
            command=self._open_mod6,
            tag="Produccion",
            hotkey="Ctrl+6",
        )
        self._add_workflow_card(
            self.productive_grid,
            row=1,
            col=1,
            title="Avance de libros",
            description="Registra libros y archivos fuente para medir el progreso real de escaneo por problemas.",
            button_text="Abrir modulo",
            command=self._open_mod9,
            tag="Gestion",
            hotkey="Ctrl+9",
        )
        self._add_workflow_card(
            self.productive_grid,
            row=2,
            col=0,
            title="Biblioteca de libros",
            description="Explora el catalogo como biblioteca visual y abre PDF, carpeta y organizador por libro.",
            button_text="Abrir modulo",
            command=self._open_mod10,
            tag="Gestion",
            hotkey="Ctrl+B",
        )
        self._add_workflow_card(
            self.productive_grid,
            row=2,
            col=1,
            title="Publicar cambios",
            description="Revisa cambios de problemas guardados en local y publícalos al servidor cuando estés listo.",
            button_text="Abrir modulo",
            command=self._open_mod11,
            tag="Sincronizacion",
            hotkey="Ctrl+P",
        )

        self.experimental_title = tk.Label(
            self.content_body,
            text="Experimental",
            bg=CONTENT_BG,
            fg=MUTED,
            font=("Segoe UI", 12, "bold"),
        )
        self.experimental_grid = tk.Frame(self.content_body, bg=CONTENT_BG)

        self._add_workflow_card(
            self.experimental_grid,
            row=0,
            col=0,
            title="Transcriptor IA",
            description="OCR y transcripcion asistida de contenido escaneado.",
            button_text="Abrir experimental",
            command=self._open_mod0,
            accent=False,
            tag="Experimental",
            hotkey="Ctrl+0",
        )
        self._add_workflow_card(
            self.experimental_grid,
            row=0,
            col=1,
            title="Cargar teoria",
            description="Carga de teoria y recursos para el flujo de auditoria.",
            button_text="Abrir experimental",
            command=self._open_teoria,
            accent=False,
            tag="Experimental",
        )
        self._add_workflow_card(
            self.experimental_grid,
            row=1,
            col=0,
            title="Auditoria IA + humano",
            description="Revision asistida de consistencia para los problemas cargados.",
            button_text="Abrir experimental",
            command=self._open_mod3,
            accent=False,
            tag="Experimental",
        )
        self._add_workflow_card(
            self.experimental_grid,
            row=1,
            col=1,
            title="Embeddings",
            description="Generacion de vectores para busqueda semantica.",
            button_text="Abrir experimental",
            command=self._open_mod4,
            accent=False,
            tag="Experimental",
        )
        self._add_workflow_card(
            self.experimental_grid,
            row=2,
            col=0,
            title="Cargar problemas",
            description="Importa bancos de problemas hacia PostgreSQL con curso, tema y subtema.",
            button_text="Abrir experimental",
            command=self._open_mod1,
            accent=False,
            tag="Experimental",
        )
        self._add_workflow_card(
            self.experimental_grid,
            row=2,
            col=1,
            title="Consulta por similitud",
            description="Busca problemas similares por embeddings y revisa resultados rapidamente.",
            button_text="Abrir experimental",
            command=self._open_mod5,
            accent=False,
            tag="Experimental",
        )
        self._add_workflow_card(
            self.experimental_grid,
            row=3,
            col=0,
            title="Auditor de entrenamiento",
            description="Revisa sesiones, segmentos y pares OCR antes de usarlos para entrenar modelos.",
            button_text="Abrir experimental",
            command=self._open_mod12,
            accent=False,
            tag="Dataset IA",
        )
        self._add_workflow_card(
            self.experimental_grid,
            row=3,
            col=1,
            title="Golden PDF -> problemas",
            description="Acumula paginas de distintos PDF y dibuja manualmente boxes de problemas completos para entrenar un detector nuevo.",
            button_text="Crear instancia",
            command=self._open_mod13,
            accent=False,
            tag="Prueba IA",
        )

    def _on_content_body_configure(self, _event=None) -> None:
        if hasattr(self, "content_canvas"):
            self.content_canvas.configure(scrollregion=self.content_canvas.bbox("all"))

    def _on_content_canvas_configure(self, event) -> None:
        if hasattr(self, "content_window"):
            self.content_canvas.itemconfigure(self.content_window, width=event.width)

    def _on_content_mousewheel(self, event) -> None:
        if not hasattr(self, "content_canvas"):
            return
        try:
            widget = self.winfo_containing(event.x_root, event.y_root)
        except Exception:
            widget = None
        if widget is None or not self._is_descendant_of_content(widget):
            return
        try:
            self.content_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:
            return

    def _is_descendant_of_content(self, widget) -> bool:
        current = widget
        while current is not None:
            if current == self.content_canvas or current == self.content_body:
                return True
            try:
                current = current.master
            except Exception:
                return False
        return False

    def _action_button(
        self,
        parent: tk.Frame,
        text: str,
        command,
        *,
        hotkey: str | None = None,
        primary: bool,
    ) -> tk.Button:
        label = text if not hotkey else f"{text} ({hotkey})"
        bg = PRIMARY if primary else SECONDARY
        hover = PRIMARY_HOVER if primary else SECONDARY_HOVER
        button = tk.Button(
            parent,
            text=label,
            command=command,
            bg=bg,
            fg="#ffffff",
            activebackground=hover,
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            font=("Segoe UI", 10, "bold"),
            cursor="hand2",
            padx=14,
            pady=9,
        )
        self._bind_button_hover(button, bg, hover)
        return button

    def _add_workflow_card(
        self,
        parent: tk.Frame,
        *,
        row: int,
        col: int,
        title: str,
        description: str,
        button_text: str,
        command,
        accent: bool = True,
        tag: str = "Workflow",
        hotkey: str | None = None,
    ) -> None:
        card = tk.Frame(parent, bg=CARD_BG, highlightbackground=CARD_BORDER, highlightthickness=1, bd=0)
        card.grid(row=row, column=col, sticky="nsew", padx=8, pady=8)
        parent.grid_columnconfigure(col, weight=1)

        top_row = tk.Frame(card, bg=CARD_BG)
        top_row.pack(fill="x", padx=14, pady=(12, 6))

        title_label = tk.Label(
            top_row,
            text=title,
            bg=CARD_BG,
            fg=TEXT,
            font=("Segoe UI", 12, "bold"),
            anchor="w",
        )
        title_label.pack(side="left", anchor="w")

        badge_label = tk.Label(
            top_row,
            text=tag,
            bg=BADGE_BG,
            fg=BADGE_FG,
            font=("Segoe UI", 8, "bold"),
            padx=8,
            pady=2,
        )
        badge_label.pack(side="right")

        desc_label = tk.Label(
            card,
            text=description,
            bg=CARD_BG,
            fg=MUTED,
            justify="left",
            wraplength=380,
            font=("Segoe UI", 10),
            anchor="w",
        )
        desc_label.pack(fill="x", padx=14, pady=(0, 8))

        if hotkey:
            hotkey_label = tk.Label(
                card,
                text=f"Atajo: {hotkey}",
                bg=CARD_BG,
                fg="#64748b",
                font=("Consolas", 9),
                anchor="w",
            )
            hotkey_label.pack(fill="x", padx=14, pady=(0, 8))
        else:
            hotkey_label = None

        btn_bg = PRIMARY if accent else "#334155"
        btn_hover = PRIMARY_HOVER if accent else "#1f2937"
        button = tk.Button(
            card,
            text=button_text,
            command=command,
            bg=btn_bg,
            fg="#ffffff",
            activebackground=btn_hover,
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            font=("Segoe UI", 10, "bold"),
            cursor="hand2",
            padx=12,
            pady=7,
        )
        self._bind_button_hover(button, btn_bg, btn_hover)
        button.pack(anchor="w", padx=14, pady=(0, 14))

        def on_enter(_event=None) -> None:
            card.configure(highlightbackground=CARD_BORDER_HOVER)

        def on_leave(_event=None) -> None:
            card.configure(highlightbackground=CARD_BORDER)

        hover_targets = [card, top_row, title_label, badge_label, desc_label, button]
        if hotkey_label is not None:
            hover_targets.append(hotkey_label)
        for widget in hover_targets:
            widget.bind("<Enter>", on_enter, add="+")
            widget.bind("<Leave>", on_leave, add="+")

    def _sidebar_button(self, parent: tk.Frame, text: str, command, *, primary: bool = False) -> None:
        bg = PRIMARY if primary else SECONDARY
        hover = PRIMARY_HOVER if primary else SECONDARY_HOVER
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg="#ffffff",
            activebackground=hover,
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            font=("Segoe UI", 10, "bold" if primary else "normal"),
            cursor="hand2",
            padx=12,
            pady=8,
            anchor="w",
        )
        self._bind_button_hover(btn, bg, hover)
        btn.pack(fill="x", pady=4)

    def _bind_button_hover(self, button: tk.Button, normal_bg: str, hover_bg: str) -> None:
        button.bind("<Enter>", lambda _evt: button.configure(bg=hover_bg), add="+")
        button.bind("<Leave>", lambda _evt: button.configure(bg=normal_bg), add="+")

    def _bind_shortcuts(self) -> None:
        self.bind_all("<Control-Key-6>", lambda _e: self._open_mod6())
        self.bind_all("<Control-Key-7>", lambda _e: self._open_mod7())
        self.bind_all("<Control-Key-8>", lambda _e: self._open_mod8_svg())
        self.bind_all("<Control-Key-9>", lambda _e: self._open_mod9())
        self.bind_all("<Control-b>", lambda _e: self._open_mod10())
        self.bind_all("<Control-B>", lambda _e: self._open_mod10())
        self.bind_all("<Control-Key-0>", lambda _e: self._open_mod0())
        self.bind_all("<Control-Key-2>", lambda _e: self._open_mod12())
        self.bind_all("<Control-e>", lambda _e: self._toggle_experimental_shortcut())
        self.bind_all("<Control-E>", lambda _e: self._toggle_experimental_shortcut())
        self.bind_all("<Escape>", lambda _e: self.destroy())

    def _toggle_experimental_shortcut(self) -> None:
        self.show_experimental_var.set(not self.show_experimental_var.get())
        self._toggle_experimental_ui()

    def _toggle_experimental_ui(self) -> None:
        if self.show_experimental_var.get():
            self.experimental_title.pack(anchor="w", padx=24, pady=(10, 0))
            self.experimental_grid.pack(fill="both", expand=True, padx=24, pady=(4, 18))
            self.experimental_state_var.set("Experimental: visible")
            return
        self.experimental_title.pack_forget()
        self.experimental_grid.pack_forget()
        self.experimental_state_var.set("Experimental: oculto")

    def _open_mod1(self) -> None:
        try:
            from modulos.modulo1_cargador.gui_cargador import LoaderWindow

            LoaderWindow(self)
        except Exception as exc:
            messagebox.showerror("Modulo 1", f"No se pudo abrir Modulo 1.\n{exc}")

    def _open_mod5(self) -> None:
        try:
            from modulos.modulo5_consulta.gui_consulta import ConsultaWindow

            ConsultaWindow(self)
        except Exception as exc:
            messagebox.showerror("Modulo 5", f"No se pudo abrir Modulo 5.\n{exc}")

    def _open_mod6(self) -> None:
        try:
            from modulos.modulo6_practicas.gui_practicas import PracticeBuilderWindow

            PracticeBuilderWindow(self)
        except Exception as exc:
            messagebox.showerror("Modulo 6", f"No se pudo abrir Modulo 6.\n{exc}")

    def _open_mod7(self) -> None:
        try:
            from modulos.modulo7_latex_word.gui_latex_word import LatexWordBridgeWindow

            LatexWordBridgeWindow(self)
        except Exception as exc:
            messagebox.showerror("Modulo 7", f"No se pudo abrir Modulo 7.\n{exc}")

    def _open_mod8_svg(self) -> None:
        try:
            if _is_frozen_app():
                cmd = [sys.executable, "--run-svg-editor"]
            else:
                cmd = [sys.executable, "-m", "modulos.modulo8_svg_editor"]
            subprocess.Popen(cmd, cwd=_launcher_cwd())
        except Exception as exc:
            messagebox.showerror("Modulo 8", f"No se pudo abrir Modulo 8 (Editor SVG).\n{exc}")

    def _open_mod0(self) -> None:
        try:
            from modulos.modulo0_transcriptor.gui_transcriptor import TranscriptorWindow

            TranscriptorWindow(self)
        except Exception as exc:
            messagebox.showerror("Modulo 0", f"No se pudo abrir Modulo 0.\n{exc}")

    def _open_mod9(self) -> None:
        try:
            from modulos.modulo9_organizador_libros.gui_organizador_libros import BookProgressWindow

            BookProgressWindow(self)
        except Exception as exc:
            messagebox.showerror("Modulo 9", f"No se pudo abrir Modulo 9.\n{exc}")

    def _open_mod10(self) -> None:
        try:
            from modulos.instance_factory.web_launcher import open_biblioteca_web_app

            def _open_legacy() -> None:
                from modulos.modulo10_biblioteca_libros.gui_biblioteca_libros import BookLibraryWindow

                BookLibraryWindow(self)

            open_biblioteca_web_app(self, legacy_launcher=_open_legacy)
        except Exception as exc:
            messagebox.showerror("Modulo 10", f"No se pudo abrir Modulo 10.\n{exc}")

    def _open_mod11(self) -> None:
        try:
            from modulos.modulo11_publicador.gui_publicador import PublishQueueWindow

            PublishQueueWindow(self)
        except Exception as exc:
            messagebox.showerror("Modulo 11", f"No se pudo abrir Modulo 11.\n{exc}")

    def _open_mod12(self) -> None:
        try:
            from modulos.modulo12_auditor_entrenamiento.gui_auditor_entrenamiento import TrainingAuditWindow

            TrainingAuditWindow(self)
        except Exception as exc:
            messagebox.showerror("Modulo 12", f"No se pudo abrir Modulo 12.\n{exc}")

    def _open_mod13(self) -> None:
        try:
            from modulos.modulo13_laboratorio_pdf_segmentacion.gui_laboratorio_pdf import PdfSegmentationLabWindow

            PdfSegmentationLabWindow(self)
        except Exception as exc:
            messagebox.showerror("Modulo 13", f"No se pudo abrir Modulo 13.\n{exc}")

    def _open_teoria(self) -> None:
        try:
            from modulos.modulo2_auditor.gui_teoria import TheoryUploaderWindow

            TheoryUploaderWindow(self, db_name_inicial=None)
        except Exception as exc:
            messagebox.showerror("Modulo 2", f"No se pudo abrir Modulo 2.\n{exc}")

    def _open_mod3(self) -> None:
        try:
            from modulos.modulo2_auditor.gui_auditor import AuditorWindow

            AuditorWindow(self)
        except Exception as exc:
            messagebox.showerror("Modulo 3", f"No se pudo abrir Modulo 3.\n{exc}")

    def _open_mod4(self) -> None:
        try:
            from modulos.modulo4_embeddings.gui_embeddings import EmbeddingWindow

            EmbeddingWindow(self)
        except Exception as exc:
            messagebox.showerror("Modulo 4", f"No se pudo abrir Modulo 4.\n{exc}")


def _configure_startup_db_profile() -> bool:
    t0 = time.time()
    selected_profile = _default_db_profile()
    config = _apply_db_profile(selected_profile)
    config = _current_db_config()
    LOGGER.info(
        "startup_db_check_start host=%s port=%s db=%s sslmode=%s",
        config["host"],
        config["port"],
        config["name"],
        config["sslmode"],
    )
    if config["host"] in {"", "<cloud-host>"}:
        LOGGER.error("startup_db_check_invalid_host host=%s", config["host"])
        messagebox.showerror(
            "Conexion de base de datos",
            "No se pudo conectar a la base configurada.\n"
            f"Host: {config['host'] or '<vacio>'}\n"
            f"Puerto: {config['port']}\n"
            f"Base: {config['name']}\n"
            f"SSL: {config['sslmode']}\n\n"
            "Configura DB_HOST con el endpoint de tu PostgreSQL en la nube.",
        )
        return False
    try:
        from database.connection import DatabaseManager
        from database.problem_change_queue import ProblemChangeQueueController

        db = DatabaseManager()
        conn = db.get_connection(config["name"])
        conn.close()
        if selected_profile == "local_mirror":
            ProblemChangeQueueController().ensure_local_queue(config["name"])
        _save_db_profile(selected_profile)
        LOGGER.info("startup_db_check_ok elapsed=%.3fs", time.time() - t0)
    except Exception as exc:
        reason, raw = _describe_db_connection_error(exc)
        hint = _build_db_connection_hint(config)
        LOGGER.exception("startup_db_check_error elapsed=%.3fs reason=%s raw=%s", time.time() - t0, reason, raw)
        messagebox.showerror(
            "Conexion de base de datos",
            "No se pudo conectar a la base configurada.\n"
            f"Host: {config['host']}\n"
            f"Puerto: {config['port']}\n"
            f"Base: {config['name']}\n"
            f"SSL: {config['sslmode']}\n\n"
            f"{reason}\n\n"
            f"Sugerencia:\n{hint}\n\n"
            f"Detalle tecnico:\n{raw}",
        )
        return False
    return True


def main() -> None:
    LOGGER.info("app_start argv=%s log_file=%s", sys.argv, str(get_log_file_path()))
    if "--run-svg-editor" in sys.argv[1:]:
        from modulos.modulo8_svg_editor.svg_editor_v2_copy import main as svg_editor_main

        svg_editor_main()
        return
    if not _configure_startup_db_profile():
        return

    app = Launcher(open_biblioteca="--open-biblioteca" in sys.argv[1:])
    app.mainloop()


if __name__ == "__main__":
    main()
