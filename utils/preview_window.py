# -*- coding: utf-8 -*-
from __future__ import annotations

import subprocess
import sys
import webbrowser
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
        self._browser_opened = False

    def _open_external_browser_once(self, url: str) -> None:
        if (not url) or self._browser_opened:
            return
        try:
            webbrowser.open(url, new=2)
            self._browser_opened = True
        except Exception:
            pass

    def ensure_open(self) -> str:
        self._server.start()
        url = self._server.url
        if not url:
            raise RuntimeError("No se pudo iniciar el servidor de preview.")

        if self._proc is not None and self._proc.poll() is None:
            return url

        if find_spec("webview") is None:
            self._browser_opened = False
            self._open_external_browser_once(url)
            return url

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
            # Termino demasiado rapido: fallback a navegador externo.
            self._open_external_browser_once(url)
            return url
        except Exception:
            self._open_external_browser_once(url)
            return url

    def ensure_open_at(self, *, x: int | None, y: int | None, on_top: bool = False) -> str:
        self._server.start()
        url = self._server.url
        if not url:
            raise RuntimeError("No se pudo iniciar el servidor de preview.")

        if self._proc is not None and self._proc.poll() is None:
            return url

        if find_spec("webview") is None:
            self._browser_opened = False
            self._open_external_browser_once(url)
            return url

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
            self._open_external_browser_once(url)
            return url
        except Exception:
            self._open_external_browser_once(url)
            return url

    def set_text(self, text: str) -> None:
        self._server.set_text(text)

    def set_images(self, images: dict[str, str]) -> None:
        self._server.set_images(images)

    def set_corrected_items(self, items: list[int]) -> None:
        self._server.set_corrected_items(items)

    def set_item_image_statuses(self, statuses: dict[int, str]) -> None:
        self._server.set_item_image_statuses(statuses)

    def set_active_item(self, item_num: int | None) -> None:
        self._server.set_active_item(item_num)

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
        self._browser_opened = False
        self._server.stop()
