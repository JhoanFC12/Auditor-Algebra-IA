# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import mimetypes
import secrets
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import parse_qs, urlparse


def _html_page() -> str:
    return """<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Vista previa LaTeX</title>
  <style>
    :root { color-scheme: light; }
    body { font-family: system-ui, Segoe UI, Arial, sans-serif; margin: 16px; background: #f8fafc; color: #0f172a; }
    .hint { margin: 0 0 12px 0; color: #475569; }
    .item { background: #fff; border: 1px solid #cbd5e1; border-radius: 8px; padding: 10px 12px; margin: 10px 0; }
    .item.clickable { cursor: pointer; border-color: #94a3b8; }
    .item.clickable:hover { border-color: #64748b; box-shadow: 0 0 0 2px rgba(100,116,139,.15); }
    .item.corrected { border-color: #16a34a; background: #f0fdf4; }
    .item.active { border-color: #0f766e; box-shadow: 0 0 0 3px rgba(15,118,110,.15); background: #ecfeff; }
    .item.corrected .corr-badge { color: #166534; border: 1px solid #86efac; background: #dcfce7; }
    .item .img-state-badge { float: right; margin-right: 8px; font-size: 11px; padding: 3px 8px; border-radius: 999px; border: 1px solid #cbd5e1; background: #f8fafc; color: #334155; }
    .item .img-state-badge.status-imagen_confirmada { color: #166534; border-color: #86efac; background: #dcfce7; }
    .item .img-state-badge.status-revision { color: #92400e; border-color: #fcd34d; background: #fef3c7; }
    .item .img-state-badge.status-sin_imagen { color: #475569; border-color: #cbd5e1; background: #f8fafc; }
    .jump-hint { margin: 0 0 8px 0; font-size: 12px; color: #0f766e; }
    .jump-btn { float: right; font-size: 12px; padding: 3px 8px; border: 1px solid #94a3b8; border-radius: 6px; background: #f8fafc; color: #0f172a; }
    .jump-btn:hover { background: #e2e8f0; }
    .corr-badge { float: right; margin-right: 8px; font-size: 11px; padding: 3px 8px; border-radius: 999px; background: #f8fafc; border: 1px solid #cbd5e1; color: #334155; }
    hr { border: 0; border-top: 1px solid #e2e8f0; margin: 12px 0; }
    .sep { opacity: .55; padding: 0 6px; }
    .sep2 { font-weight: 600; opacity: .65; }
    .empty { color: #64748b; padding: 12px; }
    .sticky { position: sticky; top: 0; background: #f8fafc; padding: 8px 0; z-index: 2; }
    code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }
    .img-wrap { margin: 8px 0 4px; padding: 8px; border: 1px dashed #cbd5e1; border-radius: 6px; background: #f8fafc; }
    .img-wrap img { max-width: 100%; max-height: 360px; border-radius: 4px; display: block; margin: 0 auto; }
    .img-cap { margin-top: 6px; color: #475569; font-size: 12px; text-align: center; }
    .modal-backdrop { position: fixed; inset: 0; background: rgba(15,23,42,.28); display: none; z-index: 30; }
    .modal-backdrop.show { display: block; }
    .modal { position: fixed; left: 50%; top: 50%; transform: translate(-50%, -50%); width: min(920px, 92vw); max-height: 86vh; background: #ffffff; border: 1px solid #cbd5e1; border-radius: 10px; box-shadow: 0 18px 50px rgba(2,6,23,.25); padding: 12px; display: none; z-index: 40; }
    .modal.show { display: block; }
    .modal-title { margin: 0 0 8px 0; font-weight: 700; }
    .modal-help { margin: 0 0 8px 0; color: #475569; font-size: 12px; }
    .modal textarea { width: 100%; min-height: 260px; max-height: 58vh; resize: vertical; border: 1px solid #cbd5e1; border-radius: 8px; padding: 8px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 13px; color: #0f172a; }
    .modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 10px; }
    .btn { border: 1px solid #94a3b8; border-radius: 8px; background: #f8fafc; color: #0f172a; padding: 7px 10px; cursor: pointer; }
    .btn:hover { background: #e2e8f0; }
    .btn-primary { border-color: #0f766e; background: #0f766e; color: #ffffff; }
    .btn-primary:hover { background: #115e59; }
    .status { min-height: 18px; font-size: 12px; margin-top: 6px; color: #334155; }
  </style>
  <script>
    window.MathJax = {
      tex: {
        inlineMath: [['$', '$']],
        displayMath: [['$$', '$$']],
        processEscapes: false
      }
    };
  </script>
  <script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"></script>
</head>
<body>
  <div class="sticky">
    <p class="hint">Vista previa en tiempo real (MathJax). Edita en la app y esto se actualiza.</p>
    <p class="hint"><code>\u00a3</code> salto visual, <code>\u00e6</code>/<code>\u00e6\u00e6</code> separadores.</p>
    <p class="jump-hint">Clic en un item para saltar al editor.</p>
  </div>
  <div id="preview"></div>
  <div id="editBackdrop" class="modal-backdrop" onclick="closeEditor()"></div>
  <div id="editModal" class="modal" role="dialog" aria-modal="true" aria-label="Editar item">
    <p id="editTitle" class="modal-title">Editar item</p>
    <p class="modal-help">Corrige texto/formulas. Se aplicara en la salida principal al guardar.</p>
    <textarea id="editText" spellcheck="false"></textarea>
    <div class="modal-actions">
      <button class="btn" onclick="closeEditor()">Cancelar</button>
      <button class="btn btn-primary" onclick="saveEditor()">Guardar cambios</button>
    </div>
    <div id="editStatus" class="status"></div>
  </div>
  <script>
    const token = new URLSearchParams(location.search).get('token') || '';
    let lastRev = -1;
    let itemByNum = {};
    let currentEditItem = null;
    let correctedSet = new Set();
    let itemImageStatuses = {};
    let activeItem = null;

    function escapeHtml(s) {
      return s.replaceAll('&', '&amp;')
              .replaceAll('<', '&lt;')
              .replaceAll('>', '&gt;')
              .replaceAll('\"', '&quot;')
              .replaceAll(\"'\", '&#39;');
    }

    function splitItems(text) {
      const t = (text || '').replaceAll('\\r\\n', '\\n').replaceAll('\\r', '\\n');
      if (!t.trim()) return [];
      // Split by LaTeX \\item start if present; otherwise by non-empty lines.
      if (t.includes('\\\\item[')) {
        return t.split(/(?=\\\\item\\s*\\[)/g).map(s => s.trim()).filter(Boolean);
      }
      return t.split(/\\n+/g).map(s => s.trim()).filter(Boolean);
    }

    function parseItemNumber(text) {
      const t = String(text || '');
      const m = t.match(/\\\\item\\s*\\[\\s*\\\\textbf\\{\\s*(\\d+)\\.?\\s*\\}\\s*\\]/i);
      if (!m) return null;
      const n = parseInt(m[1], 10);
      return Number.isFinite(n) && n > 0 ? n : null;
    }

    function decorate(text) {
      let html = escapeHtml(text);
      html = html.replace(/\[\[\s*Imagen\s*=\s*([^\]]+?)\s*\]\]/gi, (_m, name) => {
        const clean = (name || '').trim();
        const src = '/img?token=' + encodeURIComponent(token) + '&name=' + encodeURIComponent(clean);
        return `<div class=\"img-wrap\"><img src=\"${src}\" alt=\"${escapeHtml(clean)}\" onerror=\"this.style.display='none';\"/><div class=\"img-cap\">[[Imagen=${escapeHtml(clean)}]]</div></div>`;
      });
      html = html.replaceAll('\u00e6\u00e6', '<span class=\"sep sep2\">\u00e6\u00e6</span>');
      html = html.replaceAll('\u00e6', '<span class=\"sep\">\u00e6</span>');
      html = html.replaceAll('\u00a3', '<br>');
      html = html.replaceAll('\\n', '<br>');
      return html;
    }

    async function gotoItem(itemNum) {
      if (!itemNum || itemNum <= 0) return;
      try {
        await fetch('/goto', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token, item: itemNum })
        });
      } catch (_e) {}
    }

    function openEditor(itemNum) {
      const n = Number(itemNum || 0);
      if (!Number.isFinite(n) || n <= 0) return;
      const raw = itemByNum[n] || '';
      currentEditItem = n;
      document.getElementById('editTitle').textContent = 'Editar item #' + n;
      document.getElementById('editText').value = raw;
      document.getElementById('editStatus').textContent = '';
      document.getElementById('editBackdrop').classList.add('show');
      document.getElementById('editModal').classList.add('show');
      document.getElementById('editText').focus();
      document.getElementById('editText').setSelectionRange(raw.length, raw.length);
    }

    function closeEditor() {
      currentEditItem = null;
      document.getElementById('editBackdrop').classList.remove('show');
      document.getElementById('editModal').classList.remove('show');
      document.getElementById('editStatus').textContent = '';
    }

    async function saveEditor() {
      const n = Number(currentEditItem || 0);
      if (!Number.isFinite(n) || n <= 0) return;
      const text = String(document.getElementById('editText').value || '').trim();
      if (!text) {
        document.getElementById('editStatus').textContent = 'El item no puede quedar vacio.';
        return;
      }
      try {
        const resp = await fetch('/edit-item', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token, item: n, text })
        });
        if (!resp.ok) {
          document.getElementById('editStatus').textContent = 'No se pudo guardar (HTTP ' + resp.status + ').';
          return;
        }
        document.getElementById('editStatus').textContent = 'Guardado. Aplicando en editor...';
        setTimeout(closeEditor, 180);
      } catch (_e) {
        document.getElementById('editStatus').textContent = 'Error de conexion al guardar.';
      }
    }

    function onItemClick(ev) {
      const el = ev.currentTarget;
      if (!el) return;
      const v = parseInt(el.getAttribute('data-item') || '', 10);
      if (!Number.isFinite(v) || v <= 0) return;
      gotoItem(v);
    }

    function render(text) {
      const items = splitItems(text);
      itemByNum = {};
      if (!items.length) {
        document.getElementById('preview').innerHTML = '<div class=\"empty\">Sin contenido.</div>';
        return;
      }
      const blocks = items.map(item => {
        const n = parseItemNumber(item);
        if (n) {
          itemByNum[n] = item;
          const corr = correctedSet.has(n);
          const isActive = Number(activeItem) === Number(n);
          const cls = [
            'item',
            'clickable',
            corr ? 'corrected' : '',
            isActive ? 'active' : '',
          ].filter(Boolean).join(' ');
          const badge = corr ? '<span class=\"corr-badge\">Corregido</span>' : '';
          const rawStatus = String(itemImageStatuses[n] || 'sin_imagen').trim() || 'sin_imagen';
          const statusLabel = rawStatus === 'imagen_confirmada'
            ? 'Imagen confirmada'
            : (rawStatus === 'revision' ? 'Revision' : 'Sin imagen');
          const statusBadge = `<span class=\"img-state-badge status-${escapeHtml(rawStatus)}\">${escapeHtml(statusLabel)}</span>`;
          return `<div class=\"${cls}\" data-item=\"${n}\" onclick=\"onItemClick(event)\"><button class=\"jump-btn\" onclick=\"event.stopPropagation(); openEditor(${n});\">Corregir #${n}</button>${badge}${statusBadge}${decorate(item)}</div>`;
        }
        return `<div class=\"item\">${decorate(item)}</div>`;
      });
      document.getElementById('preview').innerHTML = blocks.join('<hr/>');
      if (window.MathJax && MathJax.typesetPromise) {
        MathJax.typesetClear();
        MathJax.typesetPromise();
      }
    }

    async function poll() {
      try {
        const resp = await fetch('/content?token=' + encodeURIComponent(token), { cache: 'no-store' });
        if (!resp.ok) return;
        const data = await resp.json();
        if (data.rev === lastRev) return;
        lastRev = data.rev;
        const corr = Array.isArray(data.corrected) ? data.corrected : [];
        correctedSet = new Set(corr.map(v => parseInt(v, 10)).filter(v => Number.isFinite(v) && v > 0));
        itemImageStatuses = (data && typeof data.item_image_statuses === 'object' && data.item_image_statuses !== null)
          ? data.item_image_statuses
          : {};
        activeItem = Number(data && data.active_item ? data.active_item : 0) || null;
        render(data.text || '');
      } catch (e) {}
    }

    setInterval(poll, 400);
    poll();
    window.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape') closeEditor();
    });
  </script>
</body>
</html>"""


@dataclass
class _State:
    token: str
    text: str = ""
    images: dict[str, str] | None = None
    corrected_items: Set[int] = field(default_factory=set)
    item_image_statuses: Dict[int, str] = field(default_factory=dict)
    active_item: Optional[int] = None
    goto_item: Optional[int] = None
    edit_requests: List[Dict[str, Any]] = field(default_factory=list)
    rev: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


class PreviewServer:
    def __init__(self):
        self._state = _State(token=secrets.token_urlsafe(16))
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def token(self) -> str:
        return self._state.token

    @property
    def url(self) -> str:
        if not self._server:
            return ""
        host, port = self._server.server_address
        return f"http://{host}:{port}/?token={self.token}"

    @property
    def port(self) -> int:
        if not self._server:
            return 0
        _host, port = self._server.server_address
        return int(port)

    @property
    def set_url(self) -> str:
        if not self._server:
            return ""
        host, port = self._server.server_address
        return f"http://{host}:{port}/set"

    def start(self) -> None:
        if self._server:
            return

        state = self._state

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code: int, body: bytes, content_type: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query or "")
                token = (qs.get("token") or [""])[0]

                if self.path.startswith("/content"):
                    if token != state.token:
                        self._send(403, b"forbidden", "text/plain; charset=utf-8")
                        return
                    with state.lock:
                        payload = {
                            "text": state.text,
                            "rev": state.rev,
                            "corrected": sorted(int(v) for v in state.corrected_items if int(v) > 0),
                            "item_image_statuses": {
                                str(int(k)): str(v)
                                for k, v in state.item_image_statuses.items()
                                if int(k) > 0 and str(v or "").strip()
                            },
                            "active_item": int(state.active_item or 0) if int(state.active_item or 0) > 0 else None,
                        }
                    self._send(200, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")
                    return

                if parsed.path == "/img":
                    if token != state.token:
                        self._send(403, b"forbidden", "text/plain; charset=utf-8")
                        return
                    name = (qs.get("name") or [""])[0].strip()
                    with state.lock:
                        images = state.images or {}
                        img_path = images.get(name, "")
                    if not img_path:
                        self._send(404, b"not found", "text/plain; charset=utf-8")
                        return
                    p = Path(img_path)
                    if not p.exists() or not p.is_file():
                        self._send(404, b"not found", "text/plain; charset=utf-8")
                        return
                    try:
                        data = p.read_bytes()
                    except Exception:
                        self._send(500, b"read error", "text/plain; charset=utf-8")
                        return
                    ctype, _ = mimetypes.guess_type(p.name)
                    self._send(200, data, (ctype or "application/octet-stream"))
                    return

                if self.path == "/" or self.path.startswith("/?"):
                    self._send(200, _html_page().encode("utf-8"), "text/html; charset=utf-8")
                    return
                self._send(404, b"not found", "text/plain; charset=utf-8")

            def do_POST(self):  # noqa: N802
                if self.path == "/goto":
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                        raw = self.rfile.read(length)
                        data = json.loads(raw.decode("utf-8"))
                    except Exception:
                        self._send(400, b"bad request", "text/plain; charset=utf-8")
                        return
                    if (data or {}).get("token") != state.token:
                        self._send(403, b"forbidden", "text/plain; charset=utf-8")
                        return
                    try:
                        item_num = int((data or {}).get("item") or 0)
                    except Exception:
                        item_num = 0
                    if item_num <= 0:
                        self._send(400, b"bad item", "text/plain; charset=utf-8")
                        return
                    with state.lock:
                        state.goto_item = item_num
                    self._send(200, b"ok", "text/plain; charset=utf-8")
                    return

                if self.path == "/edit-item":
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                        raw = self.rfile.read(length)
                        data = json.loads(raw.decode("utf-8"))
                    except Exception:
                        self._send(400, b"bad request", "text/plain; charset=utf-8")
                        return
                    if (data or {}).get("token") != state.token:
                        self._send(403, b"forbidden", "text/plain; charset=utf-8")
                        return
                    try:
                        item_num = int((data or {}).get("item") or 0)
                    except Exception:
                        item_num = 0
                    text = str((data or {}).get("text") or "").strip()
                    if item_num <= 0 or not text:
                        self._send(400, b"bad payload", "text/plain; charset=utf-8")
                        return
                    with state.lock:
                        state.edit_requests.append({"item": item_num, "text": text})
                    self._send(200, b"ok", "text/plain; charset=utf-8")
                    return

                if self.path != "/set":
                    self._send(404, b"not found", "text/plain; charset=utf-8")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    raw = self.rfile.read(length)
                    data = json.loads(raw.decode("utf-8"))
                except Exception:
                    self._send(400, b"bad request", "text/plain; charset=utf-8")
                    return
                if (data or {}).get("token") != state.token:
                    self._send(403, b"forbidden", "text/plain; charset=utf-8")
                    return
                text = str((data or {}).get("text") or "")
                with state.lock:
                    state.text = text
                    state.rev += 1
                self._send(200, b"ok", "text/plain; charset=utf-8")

            def log_message(self, format, *args):  # noqa: A003
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def set_text(self, text: str) -> None:
        with self._state.lock:
            self._state.text = text
            self._state.rev += 1

    def set_images(self, images: dict[str, str]) -> None:
        clean: dict[str, str] = {}
        for k, v in (images or {}).items():
            key = str(k or "").strip()
            val = str(v or "").strip()
            if not key or not val:
                continue
            clean[key] = val
        with self._state.lock:
            self._state.images = clean
            self._state.rev += 1

    def set_corrected_items(self, items: List[int]) -> None:
        vals: Set[int] = set()
        for v in (items or []):
            try:
                iv = int(v)
            except Exception:
                continue
            if iv > 0:
                vals.add(iv)
        with self._state.lock:
            self._state.corrected_items = vals
            self._state.rev += 1

    def set_item_image_statuses(self, statuses: Dict[int, str]) -> None:
        clean: Dict[int, str] = {}
        for raw_key, raw_value in (statuses or {}).items():
            try:
                key = int(raw_key)
            except Exception:
                continue
            value = str(raw_value or "").strip()
            if key <= 0 or not value:
                continue
            clean[key] = value
        with self._state.lock:
            self._state.item_image_statuses = clean
            self._state.rev += 1

    def set_active_item(self, item_num: int | None) -> None:
        value: Optional[int] = None
        try:
            current = int(item_num or 0)
        except Exception:
            current = 0
        if current > 0:
            value = current
        with self._state.lock:
            self._state.active_item = value
            self._state.rev += 1

    def pop_goto_item(self) -> Optional[int]:
        with self._state.lock:
            item = self._state.goto_item
            self._state.goto_item = None
        return item

    def pop_edit_requests(self) -> List[Dict[str, Any]]:
        with self._state.lock:
            out = list(self._state.edit_requests)
            self._state.edit_requests.clear()
        return out

    def stop(self) -> None:
        if not self._server:
            return
        try:
            self._server.shutdown()
        except Exception:
            pass
        try:
            self._server.server_close()
        except Exception:
            pass
        self._server = None
