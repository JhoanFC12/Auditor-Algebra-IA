from __future__ import annotations

import dataclasses
import base64
import binascii
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
        BookUpdateInput,
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

    @dataclasses.dataclass(slots=True)
    class BookUpdateInput(BookCreateInput):
        pass

    BookProgressController = None  # type: ignore[assignment]
from utils.project_layout import remap_legacy_drive_path

from .hf_endpoint_manager import HfEndpointManager
from .library_covers import library_cover_dir, save_cover_bytes
from .models import InstancePipelineContext
from .library_api import LibraryApiError, LibraryWebApi
from .runtime_env import load_factory_runtime_env
from .web_server import FactoryWebRuntime, WebApiError, _FilePayload, build_web_app_version, signal_web_app_reload


class LibraryWebRuntime:
    """Local web app for the book library and per-instance factory entrypoints."""

    def __init__(
        self,
        *,
        controller: Any | None = None,
        default_db_name: str = "",
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        load_factory_runtime_env()
        if controller is None and BookProgressController is None:
            raise RuntimeError(
                "No se puede iniciar Biblioteca sin el controlador real: falta instalar psycopg2."
            ) from _BOOK_PROGRESS_IMPORT_ERROR
        self.controller = controller or BookProgressController()
        self.library_api = LibraryWebApi(
            controller=self.controller,
            runtime_factory=self._create_factory_runtime,
            file_url_resolver=self._register_library_file,
        )
        self.default_db_name = str(default_db_name or "").strip()
        self.static_root = Path(__file__).with_name("web")
        self.cache_root = Path(tempfile.mkdtemp(prefix="pdf_library_web_"))
        self._file_tokens: dict[str, Path] = {}
        self._factory_runtimes: list[FactoryWebRuntime] = []
        self.endpoint_manager = HfEndpointManager()
        self._lock = threading.RLock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.host = str(host or "127.0.0.1").strip() or "127.0.0.1"
        self.port = max(0, int(port or 0))
        self.url = ""

    def _create_factory_runtime(self, context: InstancePipelineContext) -> FactoryWebRuntime:
        runtime = FactoryWebRuntime(context, library_api=self.library_api, endpoint_manager=self.endpoint_manager)
        self._factory_runtimes.append(runtime)
        return runtime

    def start(self) -> str:
        if self._server is not None and self.url:
            return self.url

        runtime = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                runtime._handle_request(self, method="GET")

            def do_POST(self) -> None:  # noqa: N802
                runtime._handle_request(self, method="POST")

            def do_OPTIONS(self) -> None:  # noqa: N802
                runtime._send_options(self)

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        host, port = self._server.server_address
        self.url = f"http://{host}:{port}/"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        stopped: set[int] = set()
        for runtime in [*list(self._factory_runtimes), *list(getattr(self.library_api, "_factory_runtimes", []))]:
            key = id(runtime)
            if key in stopped:
                continue
            stopped.add(key)
            try:
                runtime.stop()
            except Exception:
                pass
        self._factory_runtimes.clear()
        api_runtimes = getattr(self.library_api, "_factory_runtimes", None)
        if isinstance(api_runtimes, list):
            api_runtimes.clear()
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
            if path == "/api/app/version":
                if method != "GET":
                    self._send_json(handler, {"error": "method_not_allowed"}, status=405)
                    return
                self._send_json(handler, build_web_app_version(self.static_root))
                return
            if path == "/api/app/reload-signal":
                if method != "POST":
                    self._send_json(handler, {"error": "method_not_allowed"}, status=405)
                    return
                self._send_json(handler, signal_web_app_reload(self.static_root))
                return
            if method == "GET" and path.startswith("/api/library/file/"):
                self._send_registered_file(handler, path)
                return
            if method == "GET" and path.startswith("/api/file/"):
                result = self._dispatch_factory_file(path, query)
                self._send_file(handler, result.path, result.content_type)
                return
            if method == "POST" and path == "/api/library/cover/paste":
                result = self._save_pasted_cover(self._read_json(handler, max_bytes=24_000_000))
                self._send_json(handler, result)
                return
            if path.startswith("/api/library/"):
                payload = self._read_json(handler) if method == "POST" else {}
                result = self._dispatch_api(method, path, query, payload)
                self._send_json(handler, result)
                return
            if path.startswith("/api/") and self._factory_runtime_for_request(handler, query, {}) is not None:
                payload = self._read_json(handler) if method == "POST" else {}
                result = self._dispatch_factory_api(handler, method, path, query, payload)
                if isinstance(result, _FilePayload):
                    self._send_file(handler, result.path, result.content_type)
                else:
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
            self._send_error(handler, exc, status=500, code="internal_error")

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

    def _dispatch_factory_api(
        self,
        handler: BaseHTTPRequestHandler,
        method: str,
        path: str,
        query: dict[str, list[str]],
        payload: dict[str, Any],
    ) -> Any:
        runtime = self._factory_runtime_for_request(handler, query, payload)
        if runtime is None:
            raise FileNotFoundError(f"Ruta API no encontrada: {method} {path}")
        return runtime._dispatch_api(method, path, query, payload)

    def _dispatch_factory_file(self, path: str, query: dict[str, list[str]]) -> _FilePayload:
        token = urllib.parse.unquote(path.rsplit("/", 1)[-1])
        runtimes = self._all_factory_runtimes()
        preferred: list[Any] = []
        raw_instance_id = self._first(query, "instance_id", "")
        if raw_instance_id:
            try:
                instance_id = int(raw_instance_id)
            except Exception:
                instance_id = 0
            if instance_id > 0:
                preferred = [
                    runtime
                    for runtime in runtimes
                    if int(getattr(runtime, "_library_instance_id", 0) or 0) == instance_id
                ]
        token_owners = [
            runtime
            for runtime in runtimes
            if token in dict(getattr(runtime, "_file_tokens", {}) or {})
        ]
        candidates: list[Any] = []
        for runtime in [*preferred, *token_owners, *reversed(runtimes)]:
            if runtime not in candidates:
                candidates.append(runtime)
        for runtime in candidates:
            try:
                result = runtime._dispatch_api("GET", path, query, {})
            except FileNotFoundError:
                continue
            if isinstance(result, _FilePayload):
                return result
        raise FileNotFoundError("Archivo no registrado en ninguna instancia abierta.")

    def _all_factory_runtimes(self) -> list[Any]:
        result: list[Any] = []
        seen: set[int] = set()
        for runtime in [*list(getattr(self.library_api, "_factory_runtimes", [])), *list(self._factory_runtimes)]:
            key = id(runtime)
            if key in seen:
                continue
            seen.add(key)
            result.append(runtime)
        return result

    def _factory_runtime_for_request(
        self,
        handler: BaseHTTPRequestHandler,
        query: dict[str, list[str]],
        payload: dict[str, Any],
    ) -> Any | None:
        raw_instance_id = (
            handler.headers.get("X-Pdf-Factory-Instance-Id")
            or self._first(query, "instance_id", "")
            or str(payload.get("instance_id") or "")
        ).strip()
        instance_id = 0
        if raw_instance_id:
            try:
                instance_id = int(raw_instance_id)
            except Exception:
                instance_id = 0
        if instance_id > 0:
            for runtime in self._all_factory_runtimes():
                try:
                    if int(getattr(runtime, "_library_instance_id", 0) or 0) == instance_id:
                        return runtime
                except Exception:
                    continue
            runtime = self._create_factory_runtime_from_request(instance_id, query, payload)
            if runtime is not None:
                return runtime
            return None
        return self._active_factory_runtime()

    def _create_factory_runtime_from_request(
        self,
        instance_id: int,
        query: dict[str, list[str]],
        payload: dict[str, Any],
    ) -> Any | None:
        db_name = str(
            self._first(query, "db_name", "")
            or payload.get("db_name")
            or self.default_db_name
            or ""
        ).strip()
        raw_book_id = self._first(query, "book_id", "") or str(payload.get("book_id") or "")
        if not db_name or not raw_book_id:
            return None
        try:
            book_id = int(raw_book_id)
        except Exception:
            return None
        book = self.controller.obtener_libro(db_name, book_id)
        if not book:
            return None
        instance = self._instance_by_id(db_name, book_id, instance_id)
        if instance is None:
            return None
        context = InstancePipelineContext.from_library_instance(book, instance, db_name=db_name)
        runtime = self._create_factory_runtime(context)
        setattr(runtime, "_library_db_name", db_name)
        setattr(runtime, "_library_book_id", int(book_id))
        setattr(runtime, "_library_instance_id", int(instance_id))
        api_runtimes = getattr(self.library_api, "_factory_runtimes", None)
        if isinstance(api_runtimes, list) and runtime not in api_runtimes:
            api_runtimes.append(runtime)
        return runtime

    def _active_factory_runtime(self) -> Any | None:
        runtimes = self._all_factory_runtimes()
        if runtimes:
            return runtimes[-1]
        return None

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
                "explicit_manual_upload_enabled": True,
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
        runtime = self._create_factory_runtime(context)
        url = runtime.start()
        return {
            "schema_version": "library_factory_opened_v1",
            "url": url,
            "context": context.to_dict(),
            "policy": {
                "target": "staging_only",
                "never_insert_directly_into_problemas": True,
                "promotion_enabled": False,
                "explicit_manual_upload_enabled": True,
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

    def _save_pasted_cover(self, payload: dict[str, Any]) -> dict[str, Any]:
        book = self._cover_book_payload(payload)
        upload_payload = {**book, **dict(payload or {})} if book else dict(payload or {})
        data_url = str(payload.get("data_url") or payload.get("dataUrl") or "").strip()
        mime = str(payload.get("mime") or "").strip().lower()
        raw_b64 = str(payload.get("base64") or "").strip()
        if data_url:
            if not data_url.startswith("data:") or "," not in data_url:
                raise ValueError("La imagen pegada no tiene formato data URL valido.")
            header, raw_b64 = data_url.split(",", 1)
            if ";base64" not in header.lower():
                raise ValueError("La imagen pegada debe venir codificada en base64.")
            mime = header[5:].split(";", 1)[0].strip().lower()
        allowed = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/webp": ".webp",
            "image/gif": ".gif",
            "image/bmp": ".bmp",
        }
        suffix = allowed.get(mime)
        if not suffix:
            raise ValueError("Solo se aceptan portadas PNG, JPG, WEBP, GIF o BMP.")
        try:
            raw = base64.b64decode(raw_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("No se pudo leer la imagen pegada.") from exc
        max_bytes = 12 * 1024 * 1024
        if not raw:
            raise ValueError("La imagen pegada esta vacia.")
        if len(raw) > max_bytes:
            raise ValueError("La portada pegada supera 12 MB.")

        file_path = save_cover_bytes(raw, suffix, upload_payload, db_name=str(payload.get("db_name") or ""))
        cover_path = str(file_path)
        attached = False
        if self._bool(payload.get("attach"), default=False):
            self._attach_cover_to_book(payload, cover_path, book=book)
            attached = True
        return {
            "schema_version": "library_cover_pasted_v1",
            "cover_path": cover_path,
            "cover_url": self._register_library_file(cover_path),
            "bytes": len(raw),
            "attached": attached,
        }

    def _cover_book_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            book_id = int(payload.get("book_id") or 0)
        except Exception:
            book_id = 0
        db_name = str(payload.get("db_name") or "").strip()
        if not book_id or not db_name:
            return {}
        book = self.controller.obtener_libro(db_name, book_id)
        return dict(book or {})

    def _attach_cover_to_book(self, payload: dict[str, Any], cover_path: str, *, book: dict[str, Any] | None = None) -> None:
        db_name = str(payload.get("db_name") or "").strip()
        try:
            book_id = int(payload.get("book_id") or 0)
        except Exception:
            book_id = 0
        if not db_name or not book_id:
            raise ValueError("db_name y book_id son requeridos para asociar portada.")
        current = dict(book or self.controller.obtener_libro(db_name, book_id) or {})
        if not current:
            raise FileNotFoundError("Libro no encontrado.")
        merged = {**current, "cover_path": cover_path}
        self.controller.actualizar_libro(db_name, book_id, BookUpdateInput(**self._book_payload(merged)))

    def _cover_upload_dir(self, payload: dict[str, Any]) -> Path:
        return library_cover_dir(payload, db_name=str(payload.get("db_name") or ""))

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

    def _instance_by_id(self, db_name: str, book_id: int, instance_id: int) -> dict[str, Any] | None:
        for row in self.controller.listar_instancias_libro(db_name, int(book_id)):
            if int(dict(row).get("id") or 0) == int(instance_id):
                return dict(row)
        return None

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
        parsed = urllib.parse.urlparse(handler.path)
        params = urllib.parse.parse_qs(parsed.query)
        mode = "factory" if self._first(params, "factory", "") or self._first(params, "instance_id", "") else "library"
        text = text.replace(
            "<h1 id=\"title\">Cargando interfaz...</h1>",
            "<h1 id=\"title\">Biblioteca</h1>" if mode == "library" else "<h1 id=\"title\">Cargando instancia...</h1>",
        ).replace(
            "<p id=\"subtitle\" class=\"context-line\">Preparando contexto de trabajo.</p>",
            "<p id=\"subtitle\" class=\"context-line\">Cargando libros, portadas e instancias.</p>"
            if mode == "library"
            else "<p id=\"subtitle\" class=\"context-line\">Preparando contexto de libro, instancia y PDF.</p>",
        ).replace(
            "<script src=\"/app.js\"></script>",
            f"<script>window.__PDF_APP_MODE__ = \"{mode}\";</script>\n  <script src=\"/app.js\"></script>",
        )
        raw = text.encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(raw)))
        handler.send_header("Cache-Control", "no-store")
        self._send_cors_headers(handler)
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
    def _send_cors_headers(handler: BaseHTTPRequestHandler) -> None:
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        handler.send_header("Access-Control-Allow-Headers", "Content-Type")

    @staticmethod
    def _send_options(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(204)
        LibraryWebRuntime._send_cors_headers(handler)
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()

    @staticmethod
    def _send_json(handler: BaseHTTPRequestHandler, payload: Any, *, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(raw)))
        handler.send_header("Cache-Control", "no-store")
        LibraryWebRuntime._send_cors_headers(handler)
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
        LibraryWebRuntime._send_cors_headers(handler)
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
        message = str(exc).strip("'") or code
        if code == "internal_error" and not include_traceback:
            message = "Error interno de la Biblioteca. Revisa el log local para mas detalle."
        payload: dict[str, Any] = {
            "schema_version": "library_web_error_v1",
            "error": message,
            "code": code,
            "status": int(status),
        }
        if include_traceback:
            payload["traceback"] = traceback.format_exc(limit=8)
        LibraryWebRuntime._send_json(handler, payload, status=status)

    @staticmethod
    def _read_json(handler: BaseHTTPRequestHandler, *, max_bytes: int = 1_000_000) -> dict[str, Any]:
        try:
            length = int(handler.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise ValueError("Content-Length invalido.") from exc
        if length <= 0:
            return {}
        if length > int(max_bytes):
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
    def _bool(value: Any, *, default: bool = False) -> bool:
        if value is None or value == "":
            return bool(default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "si", "sí", "yes", "on"}

    @staticmethod
    def _allowed_api_methods(path: str) -> set[str]:
        exact = {
            "/api/library/bootstrap": {"GET"},
            "/api/library/book": {"GET"},
            "/api/library/book/create": {"POST"},
            "/api/library/cover/paste": {"POST"},
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
