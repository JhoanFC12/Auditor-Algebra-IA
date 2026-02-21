# -*- coding: utf-8 -*-
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from importlib.util import find_spec
from typing import Optional

from .latex_preview_server import PreviewServer


@dataclass
class PreviewWindow:
    title: str = "Vista previa LaTeX"
    width: int = 860
    height: int = 900

    def __post_init__(self) -> None:
        self._server = PreviewServer()
        self._proc: Optional[subprocess.Popen] = None

    def ensure_open(self) -> str:
        self._server.start()
        url = self._server.url
        if not url:
            raise RuntimeError("No se pudo iniciar el servidor de preview.")

        if self._proc is not None and self._proc.poll() is None:
            return url

        if find_spec("webview") is None:
            raise RuntimeError(
                "No se pudo abrir la vista previa porque falta `pywebview`.\n"
                "Instala con: python -m pip install pywebview\n"
                f"URL (alternativa manual): {url}"
            )

        args = [
            sys.executable,
            "-m",
            "utils.webview_preview",
            url,
            self.title,
            str(int(self.width)),
            str(int(self.height)),
            "None",
            "None",
            "0",
        ]
        try:
            self._proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                self._proc.wait(timeout=0.35)
            except subprocess.TimeoutExpired:
                return url
            # Terminó demasiado rápido: algo falló (WebView2, pywebview, etc.)
            raise RuntimeError(
                "No se pudo abrir la ventana de vista previa (pywebview cerró inmediatamente).\n"
                f"URL (alternativa manual): {url}"
            )
        except Exception as exc:
            raise RuntimeError(f"No se pudo lanzar la vista previa: {exc}\nURL: {url}") from exc
        return url

    def ensure_open_at(self, *, x: int | None, y: int | None, on_top: bool = False) -> str:
        self._server.start()
        url = self._server.url
        if not url:
            raise RuntimeError("No se pudo iniciar el servidor de preview.")

        if self._proc is not None and self._proc.poll() is None:
            return url

        if find_spec("webview") is None:
            raise RuntimeError(
                "No se pudo abrir la vista previa porque falta `pywebview`.\n"
                "Instala con: python -m pip install pywebview\n"
                f"URL (alternativa manual): {url}"
            )

        args = [
            sys.executable,
            "-m",
            "utils.webview_preview",
            url,
            self.title,
            str(int(self.width)),
            str(int(self.height)),
            str(x) if x is not None else "None",
            str(y) if y is not None else "None",
            "1" if on_top else "0",
        ]
        try:
            self._proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                self._proc.wait(timeout=0.35)
            except subprocess.TimeoutExpired:
                return url
            raise RuntimeError(
                "No se pudo abrir la ventana de vista previa (pywebview cerró inmediatamente).\n"
                f"URL (alternativa manual): {url}"
            )
        except Exception as exc:
            raise RuntimeError(f"No se pudo lanzar la vista previa: {exc}\nURL: {url}") from exc


    def set_text(self, text: str) -> None:
        self._server.set_text(text)

    def set_images(self, images: dict[str, str]) -> None:
        self._server.set_images(images)

    def set_corrected_items(self, items: list[int]) -> None:
        self._server.set_corrected_items(items)

    def pop_goto_item(self) -> Optional[int]:
        return self._server.pop_goto_item()

    def pop_edit_requests(self) -> list[dict[str, object]]:
        return self._server.pop_edit_requests()

    @property
    def url(self) -> str:
        return self._server.url

    def close(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass
        self._proc = None
        self._server.stop()
