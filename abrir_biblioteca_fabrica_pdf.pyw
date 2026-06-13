from __future__ import annotations

import os
import subprocess
import sys
import urllib.request
import webbrowser
from importlib.util import find_spec
from pathlib import Path
from typing import Any


APP_TITLE = "Biblioteca | Fabrica PDF"
DEFAULT_LIBRARY_PORT = 8765


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


def _bootstrap_path() -> Path:
    root = _repo_root()
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def _handoff_to_workspace_python(root: Path) -> bool:
    if os.getenv("AUDITOR_LIBRARY_VENV_HANDOFF") == "1":
        return False
    venv_exe = root / ".venv" / "Scripts" / "pythonw.exe"
    if not venv_exe.exists():
        return False
    current = Path(sys.executable).resolve()
    try:
        if current == venv_exe.resolve() or root in current.parents:
            return False
    except Exception:
        pass
    env = os.environ.copy()
    env["AUDITOR_LIBRARY_VENV_HANDOFF"] = "1"
    subprocess.Popen([str(venv_exe), str(Path(__file__).resolve())], cwd=str(root), env=env)
    return True


def _configure_database_profile() -> str:
    """Apply the same DB profile used by the main Auditor launcher."""
    try:
        import main as auditor_main

        profile = auditor_main._default_db_profile()  # type: ignore[attr-defined]
        config = auditor_main._apply_db_profile(profile)  # type: ignore[attr-defined]
        return str(config.get("name") or os.getenv("DB_NAME") or "").strip()
    except Exception:
        pass

    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        load_dotenv = None  # type: ignore[assignment]
    if load_dotenv is not None:
        root = _repo_root()
        for candidate in (root / ".env.local", root / ".env"):
            if candidate.exists():
                load_dotenv(candidate, override=False)
                break
    return str(os.getenv("DB_NAME", "") or "").strip()


def _preferred_library_port() -> int:
    raw = str(os.getenv("PDF_FACTORY_LIBRARY_PORT", "") or os.getenv("AUDITOR_LIBRARY_PORT", "") or DEFAULT_LIBRARY_PORT).strip()
    try:
        return max(1, min(65535, int(raw)))
    except Exception:
        return DEFAULT_LIBRARY_PORT


def _server_is_alive(url: str) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/app/version", timeout=1.5) as response:
            if int(response.status) != 200:
                return False
            return b"pdf_factory_web_app_version_v1" in response.read(4096)
    except Exception:
        return False


def _open_webview(url: str) -> bool:
    if find_spec("webview") is None:
        return False
    try:
        import webview  # type: ignore
    except Exception:
        return False
    webview.create_window(APP_TITLE, url, width=1440, height=920, resizable=True)
    webview.start()
    return True


def _control_window(runtime: Any, url: str) -> None:
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry("500x170")
    root.minsize(480, 160)

    frame = ttk.Frame(root, padding=14)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="Biblioteca/Fabrica PDF esta en ejecucion.", font=("Segoe UI", 10, "bold")).pack(anchor="w")
    ttk.Label(frame, text=url, foreground="#2563eb").pack(anchor="w", pady=(6, 12))
    ttk.Label(
        frame,
        text="Puedes ocultar esta ventana; el servidor seguira activo. Usa Cerrar servidor solo al terminar.",
        wraplength=460,
    ).pack(anchor="w", pady=(0, 10))

    actions = ttk.Frame(frame)
    actions.pack(fill="x")

    ttk.Button(actions, text="Abrir navegador", command=lambda: webbrowser.open(url, new=2)).pack(side="left")
    ttk.Button(actions, text="Ocultar ventana", command=root.withdraw).pack(side="left", padx=(8, 0))

    def close() -> None:
        try:
            runtime.stop()
        finally:
            root.destroy()

    ttk.Button(actions, text="Cerrar servidor", command=close).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", root.withdraw)
    root.mainloop()


def main() -> int:
    root = _bootstrap_path()
    if _handoff_to_workspace_python(root):
        return 0
    from modulos.instance_factory.runtime_env import load_factory_runtime_env

    load_factory_runtime_env(root)
    default_db_name = _configure_database_profile()
    from modulos.instance_factory.library_web_server import LibraryWebRuntime

    preferred_url = f"http://127.0.0.1:{_preferred_library_port()}/"
    if _server_is_alive(preferred_url):
        webbrowser.open(preferred_url, new=2)
        return 0

    runtime = LibraryWebRuntime(default_db_name=default_db_name, port=_preferred_library_port())
    try:
        url = runtime.start()
    except OSError:
        runtime = LibraryWebRuntime(default_db_name=default_db_name, port=0)
        url = runtime.start()
    try:
        if _open_webview(url):
            return 0
        webbrowser.open(url, new=2)
        _control_window(runtime, url)
        return 0
    finally:
        runtime.stop()


if __name__ == "__main__":
    raise SystemExit(main())
