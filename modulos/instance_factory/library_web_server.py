from __future__ import annotations

import dataclasses
import hashlib
import json
import mimetypes
import tempfile
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    from modulos.modulo9_organizador_libros.controlador_organizador_libros import (
        BOOK_STATES,
        BookCreateInput,
        BookInstanceInput,
        BookProgressController,
    )
    _BOOK_PROGRESS_IMPORT_ERROR: Exception | None = None
except ModuleNotFoundError as exc:
    if exc.name != "psycopg2":
        raise
    _BOOK_PROGRESS_IMPORT_ERROR = exc
    BOOK_STATES = ("pendiente", "en_progreso", "completo")

    @dataclasses.dataclass(slots=True)
    class BookCreateInput:
        codigo: str
        titulo: str
        autor: str = ""
        editorial: str = ""
        edicion: str = ""
        curso: str = ""
        workspace_dir: str = ""
        pdf_path: str = ""
        cover_path: str = ""
        estado: str = "pendiente"
        notas: str = ""
        activo: bool = True

    @dataclasses.dataclass(slots=True)
    class BookInstanceInput:
        libro_id: int
        tipo: str
        total_esperado: int = 0
        session_path: str = ""
        soluciones_dir: str = ""
        notas: str = ""
        activo: bool = True

    BookProgressController = None  # type: ignore[assignment]
from utils.project_layout import remap_legacy_drive_path

from .models import InstancePipelineContext
from .library_api import LibraryApiError, LibraryWebApi
from .web_server import FactoryWebRuntime, WebApiError


class LibraryWebRuntime:
    """Local web app for the book library and per-instance factory entrypoints."""

    def __init__(self, *, controller: Any | None = None, default_db_name: str = "") -> None:
        if controller is None and BookProgressController is None:
            raise RuntimeError(
                "No se puede iniciar Biblioteca sin el controlador real: falta instalar psycopg2."
            ) from _BOOK_PROGRESS_IMPORT_ERROR
        self.controller = controller or BookProgressController()
        self.library_api = LibraryWebApi(controller=self.controller, file_url_resolver=self._register_library_file)
        self.default_db_name = str(default_db_name or "").strip()
        self.static_root = Path(__file__).with_name("web")
        self.cache_root = Path(tempfile.mkdtemp(prefix="pdf_library_web_"))
        self._file_tokens: dict[str, Path] = {}
        self._factory_runtimes: list[FactoryWebRuntime] = []
        self._lock = threading.RLock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    def start(self) -> str:
        if self._server is not None and self.url:
            return self.url

        runtime = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                runtime._handle_request(self, method="GET")

            def do_POST(self) -> None:  # noqa: N802
                runtime._handle_request(self, method="POST")

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        host, port = self._server.server_address
        self.url = f"http://{host}:{port}/"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        for runtime in list(self._factory_runtimes):
            try:
                runtime.stop()
            except Exception:
                pass
        self._factory_runtimes.clear()
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        self.url = ""

    def _handle_request(self, handler: BaseHTTPRequestHandler, *, method: str) -> None:
        parsed = urllib.parse.urlparse(handler.path)
        path = parsed.path or "/"
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if method == "GET" and path.startswith("/api/library/file/"):
                self._send_registered_file(handler, path)
                return
            if path.startswith("/api/library/"):
                payload = self._read_json(handler) if method == "POST" else {}
                result = self._dispatch_api(method, path, query, payload)
                self._send_json(handler, result)
                return
            if method != "GET":
                self._send_json(handler, {"error": "method_not_allowed"}, status=405)
                return
            self._send_static(handler, path)
        except (WebApiError, LibraryApiError) as exc:
            self._send_error(handler, exc, status=exc.status, code=exc.code)
        except FileNotFoundError as exc:
            self._send_error(handler, exc, status=404, code="not_found")
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_error(handler, exc, status=400, code="bad_request")
        except Exception as exc:
            self._send_error(handler, exc, status=500, code="internal_error", include_traceback=True)

    def _dispatch_api(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
        payload: dict[str, Any],
    ) -> Any:
        with self._lock:
            if path.startswith("/api/library/"):
                return self.library_api.dispatch(method, path, query, payload)
            self._ensure_allowed_method(method, path)
            if method == "GET" and path == "/api/library/bootstrap":
                db_name = self._first(query, "db_name", self.default_db_name)
                return self._snapshot(db_name)
            if method == "GET" and path == "/api/library/book":
                db_name = self._required_query(query, "db_name")
                book_id = self._bounded_int(self._required_query(query, "book_id"), "book_id", minimum=1, maximum=10**9)
                return self._book_detail(db_name, book_id)
            if method == "POST" and path == "/api/library/book/create":
                db_name = self._required_str(payload, "db_name")
                data = dict(payload.get("book") or {})
                if not isinstance(data, dict):
                    raise ValueError("book debe ser un objeto.")
                book_id = self.controller.crear_libro(db_name, BookCreateInput(**self._book_payload(data)))
                return {"schema_version": "library_book_created_v1", "book_id": int(book_id), "snapshot": self._snapshot(db_name)}
            if method == "POST" and path == "/api/library/instance/create":
                db_name = self._required_str(payload, "db_name")
                data = dict(payload.get("instance") or {})
                if not isinstance(data, dict):
                    raise ValueError("instance debe ser un objeto.")
                instance_id = self.controller.crear_instancia(db_name, BookInstanceInput(**self._instance_payload(data)))
                book_id = int(data.get("libro_id") or 0)
                return {
                    "schema_version": "library_instance_created_v1",
                    "instance_id": int(instance_id),
                    "book": self._book_detail(db_name, book_id) if book_id > 0 else None,
                    "snapshot": self._snapshot(db_name),
                }
            if method == "POST" and path == "/api/library/instance/factory":
                db_name = self._required_str(payload, "db_name")
                book_id = self._bounded_int(payload.get("book_id"), "book_id", minimum=1, maximum=10**9)
                instance_type = self._required_str(payload, "instance_type")
                return self._open_factory_for_instance(db_name, book_id, instance_type)
            raise FileNotFoundError(f"Ruta API no encontrada: {method} {path}")

    def _snapshot(self, db_name: str = "") -> dict[str, Any]:
        dbs = list(self.controller.listar_bases_datos())
        selected = str(db_name or "").strip()
        if selected not in dbs:
            selected = self.default_db_name if self.default_db_name in dbs else (dbs[0] if dbs else "")
        books = self.controller.listar_libros(selected) if selected else []
        return {
            "schema_version": "library_web_snapshot_v1",
            "selected_db": selected,
            "databases": dbs,
            "books": [self._book_to_web(row) for row in books],
            "summary": self._library_summary(books),
            "policy": {
                "target": "staging_first",
                "factory_target": "staging_only",
                "never_insert_directly_into_problemas": True,
                "promotion_enabled": False,
            },
        }

    def _book_detail(self, db_name: str, book_id: int) -> dict[str, Any]:
        book = self.controller.obtener_libro(db_name, int(book_id))
        if not book:
            raise FileNotFoundError("Libro no encontrado.")
        instances = [self._instance_to_web(row) for row in self.controller.listar_instancias_libro(db_name, int(book_id))]
        dashboard = self.controller.obtener_dashboard_libro(db_name, int(book_id))
        return {
            "schema_version": "library_book_detail_v1",
            "book": self._book_to_web(book),
            "instances": instances,
            "dashboard": self._dashboard_to_web(dashboard),
        }

    def _open_factory_for_instance(self, db_name: str, book_id: int, instance_type: str) -> dict[str, Any]:
        book = self.controller.obtener_libro(db_name, int(book_id))
        if not book:
            raise FileNotFoundError("Libro no encontrado.")
        instance = self.controller.obtener_instancia(db_name, int(book_id), instance_type)
        if not instance:
            raise FileNotFoundError("Instancia no encontrada.")
        pdf_path = str(book.get("pdf_path") or "").strip()
        if not pdf_path:
            raise ValueError("Este libro no tiene PDF registrado.")
        resolved_pdf = remap_legacy_drive_path(Path(pdf_path).expanduser(), prefer_existing=True)
        if not resolved_pdf.exists():
            raise FileNotFoundError(f"No se encontro el PDF del libro: {resolved_pdf}")
        context = InstancePipelineContext.from_library_instance(book, instance, db_name=db_name)
        runtime = FactoryWebRuntime(context)
        url = runtime.start()
        self._factory_runtimes.append(runtime)
        return {
            "schema_version": "library_factory_opened_v1",
            "url": url,
            "context": context.to_dict(),
            "policy": {
                "target": "staging_only",
                "never_insert_directly_into_problemas": True,
                "promotion_enabled": False,
            },
        }

    @staticmethod
    def _library_summary(books: list[dict[str, Any]]) -> dict[str, int]:
        states = {state: 0 for state in BOOK_STATES}
        instances_total = 0
        expected_total = 0
        for book in books:
            state = str(book.get("estado") or "").strip().lower()
            if state in states:
                states[state] += 1
            instances_total += int(book.get("instances_total") or 0)
            expected_total += int(book.get("instances_expected_total") or 0)
        return {
            "books_total": len(books),
            "instances_total": instances_total,
            "expected_total": expected_total,
            **{f"books_{key}": value for key, value in states.items()},
        }

    @staticmethod
    def _book_to_web(book: dict[str, Any]) -> dict[str, Any]:
        payload = dict(book or {})
        payload["id"] = int(payload.get("id") or 0)
        payload["instances_total"] = int(payload.get("instances_total") or 0)
        payload["instances_expected_total"] = int(payload.get("instances_expected_total") or 0)
        payload["instances_session_count"] = int(payload.get("instances_session_count") or 0)
        payload["instances_solutions_count"] = int(payload.get("instances_solutions_count") or 0)
        payload["instances_health"] = LibraryWebRuntime._parse_instances_health(payload)
        return payload

    @staticmethod
    def _instance_to_web(instance: dict[str, Any]) -> dict[str, Any]:
        payload = dict(instance or {})
        payload["id"] = int(payload.get("id") or 0)
        payload["libro_id"] = int(payload.get("libro_id") or 0)
        payload["total_esperado"] = int(payload.get("total_esperado") or 0)
        payload["activo"] = bool(payload.get("activo", True))
        return payload

    @staticmethod
    def _dashboard_to_web(dashboard: Any) -> dict[str, Any]:
        payload = dataclasses.asdict(dashboard) if dataclasses.is_dataclass(dashboard) else dict(dashboard or {})
        payload["instancias"] = [dict(row) for row in list(payload.get("instancias") or [])]
        return payload

    @staticmethod
    def _parse_instances_health(book: dict[str, Any]) -> list[dict[str, Any]]:
        raw = book.get("instances_health")
        if isinstance(raw, list):
            return [dict(item) for item in raw if isinstance(item, dict)]
        raw_json = str(book.get("instances_health_json") or "").strip()
        if not raw_json:
            return []
        try:
            parsed = json.loads(raw_json)
        except Exception:
            return []
        if not isinstance(parsed, list):
            return []
        return [dict(item) for item in parsed if isinstance(item, dict)]

    @staticmethod
    def _book_payload(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "workspace_dir": str(data.get("workspace_dir") or ""),
            "codigo": str(data.get("codigo") or ""),
            "titulo": str(data.get("titulo") or ""),
            "autor": str(data.get("autor") or ""),
            "editorial": str(data.get("editorial") or ""),
            "edicion": str(data.get("edicion") or ""),
            "curso": str(data.get("curso") or ""),
            "pdf_path": str(data.get("pdf_path") or ""),
            "cover_path": str(data.get("cover_path") or ""),
            "estado": str(data.get("estado") or "pendiente"),
            "notas": str(data.get("notas") or ""),
            "activo": bool(data.get("activo", True)),
        }

    @staticmethod
    def _instance_payload(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "libro_id": int(data.get("libro_id") or 0),
            "tipo": str(data.get("tipo") or ""),
            "total_esperado": int(data.get("total_esperado") or 0),
            "session_path": str(data.get("session_path") or ""),
            "soluciones_dir": str(data.get("soluciones_dir") or ""),
            "notas": str(data.get("notas") or ""),
            "activo": bool(data.get("activo", True)),
        }

    def _send_static(self, handler: BaseHTTPRequestHandler, path: str) -> None:
        name = "index.html" if path in {"", "/", "/library"} else path.lstrip("/")
        if "/" in name:
            name = name.split("/")[-1]
        file_path = self.static_root / name
        if not file_path.exists():
            self._send_json(handler, {"error": "not_found"}, status=404)
            return
        if file_path.name == "index.html":
            self._send_library_index(handler, file_path)
            return
        self._send_file(handler, file_path, mimetypes.guess_type(str(file_path))[0] or "text/plain")

    def _send_library_index(self, handler: BaseHTTPRequestHandler, path: Path) -> None:
        text = Path(path).read_text(encoding="utf-8")
        text = text.replace(
            "<h1 id=\"title\">Cargando interfaz...</h1>",
            "<h1 id=\"title\">Biblioteca</h1>",
        ).replace(
            "<p id=\"subtitle\" class=\"context-line\">Preparando contexto de trabajo.</p>",
            "<p id=\"subtitle\" class=\"context-line\">Cargando libros, portadas e instancias.</p>",
        ).replace(
            "<script src=\"/app.js\"></script>",
            "<script>window.__PDF_APP_MODE__ = \"library\";</script>\n  <script src=\"/app.js\"></script>",
        )
        raw = text.encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(raw)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(raw)

    def _register_library_file(self, path_text: str) -> str:
        raw = str(path_text or "").strip()
        if not raw:
            return ""
        try:
            file_path = remap_legacy_drive_path(Path(raw).expanduser(), prefer_existing=True).resolve()
        except Exception:
            return ""
        if not file_path.exists() or not file_path.is_file():
            return ""
        if file_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
            return ""
        token = hashlib.sha1(str(file_path).encode("utf-8", errors="ignore")).hexdigest()[:24]
        with self._lock:
            self._file_tokens[token] = file_path
        return f"/api/library/file/{token}"

    def _send_registered_file(self, handler: BaseHTTPRequestHandler, path: str) -> None:
        token = urllib.parse.unquote(path.rsplit("/", 1)[-1])
        with self._lock:
            file_path = self._file_tokens.get(token)
        if file_path is None:
            self._send_json(handler, {"error": "file_not_found"}, status=404)
            return
        self._send_file(handler, file_path, mimetypes.guess_type(str(file_path))[0] or "application/octet-stream")

    @staticmethod
    def _send_json(handler: BaseHTTPRequestHandler, payload: Any, *, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(raw)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(raw)

    @staticmethod
    def _send_file(handler: BaseHTTPRequestHandler, path: Path, content_type: str) -> None:
        file_path = Path(path)
        if not file_path.exists():
            LibraryWebRuntime._send_json(handler, {"error": "file_not_found"}, status=404)
            return
        raw = file_path.read_bytes()
        handler.send_response(200)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(raw)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(raw)

    @staticmethod
    def _send_error(
        handler: BaseHTTPRequestHandler,
        exc: Exception,
        *,
        status: int,
        code: str,
        include_traceback: bool = False,
    ) -> None:
        payload: dict[str, Any] = {
            "schema_version": "library_web_error_v1",
            "error": str(exc).strip("'") or code,
            "code": code,
            "status": int(status),
        }
        if include_traceback:
            payload["traceback"] = traceback.format_exc(limit=8)
        LibraryWebRuntime._send_json(handler, payload, status=status)

    @staticmethod
    def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
        try:
            length = int(handler.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise ValueError("Content-Length invalido.") from exc
        if length <= 0:
            return {}
        if length > 1_000_000:
            raise WebApiError("Payload JSON demasiado grande.", status=413, code="payload_too_large")
        payload = json.loads(handler.rfile.read(length).decode("utf-8") or "{}")
        if not isinstance(payload, dict):
            raise ValueError("El payload JSON debe ser un objeto.")
        return dict(payload)

    @staticmethod
    def _first(query: dict[str, list[str]], key: str, default: str = "") -> str:
        values = query.get(key) or []
        return str(values[0]) if values else default

    @staticmethod
    def _required_query(query: dict[str, list[str]], key: str) -> str:
        value = LibraryWebRuntime._first(query, key, "").strip()
        if not value:
            raise ValueError(f"{key} es requerido.")
        return value

    @staticmethod
    def _required_str(payload: dict[str, Any], key: str) -> str:
        value = str(payload.get(key) or "").strip()
        if not value:
            raise ValueError(f"{key} es requerido.")
        return value

    @staticmethod
    def _bounded_int(value: Any, key: str, *, minimum: int, maximum: int) -> int:
        try:
            number = int(value)
        except Exception as exc:
            raise ValueError(f"{key} debe ser entero.") from exc
        if number < minimum or number > maximum:
            raise ValueError(f"{key} fuera de rango: {minimum}-{maximum}.")
        return number

    @staticmethod
    def _allowed_api_methods(path: str) -> set[str]:
        exact = {
            "/api/library/bootstrap": {"GET"},
            "/api/library/book": {"GET"},
            "/api/library/book/create": {"POST"},
            "/api/library/instance/create": {"POST"},
            "/api/library/instance/factory": {"POST"},
        }
        if path.startswith("/api/library/"):
            return LibraryWebApi.allowed_methods(path)
        return exact.get(path, set())

    def _ensure_allowed_method(self, method: str, path: str) -> None:
        allowed = self._allowed_api_methods(path)
        if not allowed:
            raise FileNotFoundError(f"Ruta API no encontrada: {method} {path}")
        if method not in allowed:
            raise WebApiError(
                f"Metodo no permitido para {path}: {method}. Permitidos: {', '.join(sorted(allowed))}",
                status=405,
                code="method_not_allowed",
            )
