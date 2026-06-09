from __future__ import annotations

import os
import sys
import webbrowser
from importlib.util import find_spec
from pathlib import Path
from typing import Any


APP_TITLE = "Biblioteca | Fabrica PDF"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


def _bootstrap_path() -> Path:
    root = _repo_root()
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


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
    root.geometry("440x150")
    root.minsize(420, 140)

    frame = ttk.Frame(root, padding=14)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="Biblioteca/Fabrica PDF esta en ejecucion.", font=("Segoe UI", 10, "bold")).pack(anchor="w")
    ttk.Label(frame, text=url, foreground="#2563eb").pack(anchor="w", pady=(6, 12))

    actions = ttk.Frame(frame)
    actions.pack(fill="x")

    ttk.Button(actions, text="Abrir navegador", command=lambda: webbrowser.open(url, new=2)).pack(side="left")

    def close() -> None:
        try:
            runtime.stop()
        finally:
            root.destroy()

    ttk.Button(actions, text="Cerrar servidor", command=close).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", close)
    root.mainloop()


def main() -> int:
    _bootstrap_path()
    default_db_name = _configure_database_profile()
    from modulos.instance_factory.library_web_server import LibraryWebRuntime

    runtime = LibraryWebRuntime(default_db_name=default_db_name)
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
