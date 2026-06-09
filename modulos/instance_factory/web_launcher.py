from __future__ import annotations

import atexit
import importlib
import os
import subprocess
import sys
import time
import webbrowser
from importlib.util import find_spec
from typing import Any, Callable

from .models import InstancePipelineContext
from .web_server import FactoryWebRuntime


_ACTIVE_RUNTIMES: list[Any] = []
_ACTIVE_PROCESSES: list[subprocess.Popen] = []


def open_factory_web_app(parent: Any = None, *, context: InstancePipelineContext) -> str:
    runtime = FactoryWebRuntime(context)
    url = runtime.start()
    _ACTIVE_RUNTIMES.append(runtime)
    title = f"Fabrica PDF - {context.book_code} / {context.instance_type}"

    _open_url(url, title)

    try:
        if parent is not None and hasattr(parent, "after"):
            parent.after(1000, _reap_finished_processes)
    except Exception:
        pass
    return url


def open_biblioteca_web_app(
    parent: Any = None,
    *,
    legacy_launcher: Callable[[], Any] | None = None,
) -> str | None:
    """Open the Biblioteca web runtime when it is available.

    The web Biblioteca is intentionally optional while the Tkinter module keeps
    running as the stable fallback. A future backend only needs to expose one of
    the supported runtime class names with a start() method that returns a URL.
    """
    runtime_cls = _resolve_biblioteca_runtime_class()
    if runtime_cls is None:
        return _open_biblioteca_legacy(legacy_launcher)

    try:
        try:
            runtime = runtime_cls(default_db_name=str(os.getenv("DB_NAME", "") or "").strip())
        except TypeError:
            runtime = runtime_cls()
        url = str(runtime.start())
    except Exception:
        return _open_biblioteca_legacy(legacy_launcher)

    _ACTIVE_RUNTIMES.append(runtime)
    _open_url(url, "Biblioteca de Libros")
    try:
        if parent is not None and hasattr(parent, "after"):
            parent.after(1000, _reap_finished_processes)
    except Exception:
        pass
    return url


def _resolve_biblioteca_runtime_class() -> type[Any] | None:
    candidates = (
        (
            "modulos.instance_factory.library_web_server",
            ("LibraryWebRuntime", "BookLibraryWebRuntime", "BibliotecaWebRuntime"),
        ),
        (
            "modulos.modulo10_biblioteca_libros.web_server",
            ("BookLibraryWebRuntime", "BibliotecaWebRuntime", "LibraryWebRuntime"),
        ),
        (
            "modulos.modulo10_biblioteca_libros.web_runtime",
            ("BookLibraryWebRuntime", "BibliotecaWebRuntime", "LibraryWebRuntime"),
        ),
    )
    for module_name, class_names in candidates:
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        for class_name in class_names:
            runtime_cls = getattr(module, class_name, None)
            if runtime_cls is not None and hasattr(runtime_cls, "start"):
                return runtime_cls
    return None


def _open_biblioteca_legacy(legacy_launcher: Callable[[], Any] | None) -> None:
    if legacy_launcher is not None:
        legacy_launcher()
        return None
    from modulos.modulo10_biblioteca_libros.gui_biblioteca_libros import BookLibraryWindow

    BookLibraryWindow(None)
    return None


def _open_url(url: str, title: str) -> None:
    if _start_webview(url, title):
        return
    webbrowser.open(url, new=2)


def _start_webview(url: str, title: str) -> bool:
    if find_spec("webview") is None:
        return False
    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "utils.webview_preview",
                url,
                title,
                "1440",
                "920",
            ],
            cwd=None,
        )
    except Exception:
        return False
    time.sleep(0.05)
    if proc.poll() is not None:
        return False
    _ACTIVE_PROCESSES.append(proc)
    return True


def _reap_finished_processes() -> None:
    _ACTIVE_PROCESSES[:] = [proc for proc in _ACTIVE_PROCESSES if proc.poll() is None]


def _shutdown() -> None:
    for runtime in list(_ACTIVE_RUNTIMES):
        try:
            runtime.stop()
        except Exception:
            pass


atexit.register(_shutdown)
