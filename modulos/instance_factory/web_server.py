from __future__ import annotations

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

from modulos.modulo13_laboratorio_pdf_segmentacion.controlador_laboratorio_pdf import (
    DEFAULT_PROBLEM_CROPS_LIVE_ROOT,
)

from .models import InstancePipelineContext, StageStatus, StagingProblemRecord
from .pipeline import InstancePdfPipelineService
from .library_api import LibraryApiError, LibraryWebApi


class WebApiError(Exception):
    def __init__(self, message: str, *, status: int = 400, code: str = "bad_request") -> None:
        super().__init__(message)
        self.status = int(status)
        self.code = code


class FactoryWebRuntime:
    """Small local web app for the PDF factory.

    The Python service remains the source of truth. The browser only sends UI
    intents such as selected pages, edited boxes, and review form fields.
    """

    def __init__(
        self,
        context: InstancePipelineContext,
        *,
        service: Any | None = None,
        library_api: LibraryWebApi | None = None,
    ) -> None:
        self.context = context
        self.service = service or InstancePdfPipelineService(context)
        self.library_api = library_api or LibraryWebApi()
        self.static_root = Path(__file__).with_name("web")
        self.cache_root = Path(tempfile.mkdtemp(prefix="pdf_factory_web_"))
        self._file_tokens: dict[str, Path] = {}
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
            if path.startswith("/api/"):
                payload = self._read_json(handler) if method == "POST" else {}
                result = self._dispatch_api(method, path, query, payload)
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
        except KeyError as exc:
            self._send_error(handler, exc, status=404, code="not_found")
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_error(handler, exc, status=400, code="bad_request")
        except PermissionError as exc:
            self._send_error(handler, exc, status=403, code="forbidden")
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
            if method == "GET" and path == "/api/bootstrap":
                return self._snapshot()
            if method == "GET" and path == "/api/pdf/page":
                page = self._bounded_int(self._first(query, "page", "1"), "page", minimum=1, maximum=10000)
                dpi = self._bounded_int(self._first(query, "dpi", "140"), "dpi", minimum=72, maximum=320)
                return _FilePayload(self._render_pdf_page(page, dpi=dpi), "image/png")
            if method == "GET" and path.startswith("/api/file/"):
                token = path.rsplit("/", 1)[-1]
                file_path = self._file_tokens.get(token)
                if file_path is None:
                    raise FileNotFoundError("Archivo no registrado en la sesion web.")
                if not self._is_trusted_file(file_path):
                    raise PermissionError("Archivo fuera de las rutas confiables de la fabrica.")
                return _FilePayload(file_path, mimetypes.guess_type(str(file_path))[0] or "application/octet-stream")
            if method == "GET" and path == "/api/record":
                record_id = self._first(query, "record_id", "")
                return self._record_detail(record_id)
            if method == "GET" and path == "/api/promotion":
                record_id = self._first(query, "record_id", "")
                return self.service.staging.build_promotion_candidate(record_id)
            if method == "POST" and path == "/api/pages/detect":
                pages = self._required_str(payload, "pages")
                dpi = self._bounded_int(payload.get("dpi") or 300, "dpi", minimum=72, maximum=600)
                confidence = self._bounded_float(payload.get("confidence") or 0.25, "confidence", minimum=0.0, maximum=1.0)
                selected = self.service.resolve_page_selection(str(pages))
                self.service.detect_pdf_pages(
                    selected,
                    dpi=dpi,
                    confidence=confidence,
                    detector_model=str(payload.get("detector_model") or ""),
                )
                return self._snapshot()
            if method == "POST" and path == "/api/pages/boxes":
                boxes = payload.get("boxes") or []
                if not isinstance(boxes, list):
                    raise ValueError("boxes debe ser una lista.")
                self.service.update_page_boxes(
                    self._required_str(payload, "record_id"),
                    boxes,
                    layout_mode=str(payload.get("layout_mode") or "auto"),
                    reviewed=bool(payload.get("reviewed", True)),
                    reorder=bool(payload.get("reorder", False)),
                )
                return self._snapshot()
            if method == "POST" and path == "/api/staging/materialize":
                self.service.materialize_crops_to_staging()
                return self._snapshot()
            if method == "POST" and path == "/api/ocr/run":
                record_ids = self._record_ids_from_payload(payload)
                self.service.run_ocr_and_segmentation(
                    provider=str(payload.get("provider") or "hf"),
                    curso=str(payload.get("curso") or "SIN_CURSO"),
                    tema=str(payload.get("tema") or "SIN_TEMA"),
                    start_n=self._bounded_int(payload.get("start_n") or 1, "start_n", minimum=1, maximum=100000),
                    limit=self._optional_int(payload.get("limit")),
                    ocr_model=str(payload.get("ocr_model") or ""),
                    figure_model=str(payload.get("figure_model") or ""),
                    force_figure_model=self._bool(payload.get("force_figure_model"), default=True),
                    record_id=str(payload.get("record_id") or ""),
                    record_ids=record_ids,
                )
                return self._snapshot()
            if method == "POST" and path in {"/api/segments/boxes", "/api/ocr/segments/boxes"}:
                boxes = payload.get("boxes") or []
                if not isinstance(boxes, list):
                    raise ValueError("boxes debe ser una lista.")
                self.service.update_figure_segments(self._required_str(payload, "record_id"), boxes)
                return self._snapshot()
            if method == "POST" and path == "/api/ocr/raw":
                self.service.update_raw_ocr(
                    self._required_str(payload, "record_id"),
                    str(payload.get("raw_ocr") or ""),
                )
                return self._snapshot()
            if method == "POST" and path == "/api/normalize":
                record_ids = self._record_ids_from_payload(payload)
                self.service.normalize_existing_ocr(
                    record_id=str(payload.get("record_id") or ""),
                    record_ids=record_ids,
                )
                return self._snapshot()
            if method == "POST" and path == "/api/review/save":
                record_id = self._required_str(payload, "record_id")
                normalized = payload.get("normalized") or {}
                if not isinstance(normalized, dict):
                    raise ValueError("normalized debe ser un objeto JSON.")
                notes = str(payload.get("notes") or "")
                mark_ready = bool(payload.get("mark_ready", False))
                updated = self.service.staging.update_review(record_id, dict(normalized), notes, mark_ready=mark_ready)
                return {
                    "schema_version": "pdf_factory_web_review_saved_v1",
                    "record": self._record_to_web(updated),
                    "snapshot": self._snapshot(),
                }
            raise FileNotFoundError(f"Ruta API no encontrada: {method} {path}")

    def _snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": "pdf_factory_web_snapshot_v1",
            "context": self.context.to_dict(),
            "pdf": self._pdf_info(),
            "summary": self.service.build_instance_summary(),
            "timeline": self.service.build_stage_overview(),
            "pages": [self._page_to_web(row) for row in self.service.load_pages()],
            "records": [self._record_to_web(record) for record in self.service.staging.load_records()],
            "models": self.service.models.to_dict(),
            "policy": {
                "target": "staging_only",
                "never_insert_directly_into_problemas": True,
                "promotion_enabled": False,
            },
        }

    def _pdf_info(self) -> dict[str, Any]:
        pdf_path = self.context.resolved_pdf_path()
        info: dict[str, Any] = {
            "path": str(pdf_path),
            "name": pdf_path.name,
            "exists": pdf_path.exists(),
            "page_count": 0,
        }
        if not pdf_path.exists():
            return info
        try:
            import fitz

            with fitz.open(pdf_path) as document:
                info["page_count"] = int(document.page_count)
        except Exception as exc:
            info["error"] = str(exc)
        return info

    def _render_pdf_page(self, page_number: int, *, dpi: int = 140) -> Path:
        pdf_path = self.context.resolved_pdf_path()
        if not pdf_path.exists():
            raise FileNotFoundError(f"No se encontro el PDF: {pdf_path}")
        dpi = max(72, min(320, int(dpi)))
        target = self.cache_root / f"pdf_page_{int(page_number):04d}_{dpi}.png"
        if target.exists():
            return target
        import fitz

        with fitz.open(pdf_path) as document:
            if page_number < 1 or page_number > document.page_count:
                raise ValueError(f"Pagina fuera del PDF: {page_number}")
            matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
            document[page_number - 1].get_pixmap(matrix=matrix, alpha=False).save(str(target))
        return target

    def _page_to_web(self, row: Any) -> dict[str, Any]:
        image_path = Path(row.image_path)
        return {
            "record_id": str(row.record_id),
            "pdf_path": str(row.pdf_path),
            "page_number": int(row.page_number),
            "image_path": str(image_path),
            "image_url": self._register_file(image_path),
            "boxes": [[int(value) for value in box[:4]] for box in list(row.boxes or [])],
            "boxes_total": len(row.boxes or []),
            "detector_source": str(row.detector_source or ""),
            "reviewed": bool(row.reviewed),
            "layout_mode": str(row.layout_mode or "auto"),
        }

    def _record_to_web(self, record: StagingProblemRecord) -> dict[str, Any]:
        payload = record.to_dict()
        crop_path = Path(record.crop_path)
        downstream = dict(dict(record.audit or {}).get("downstream_state") or {})
        crop_exists = bool(record.crop_path and crop_path.exists())
        payload["crop_name"] = crop_path.name
        payload["crop_url"] = self._register_file(crop_path) if crop_exists else ""
        payload["status_label"] = StageStatus.normalize(record.status)
        payload["downstream_state"] = downstream
        payload["downstream_invalidated"] = downstream.get("status") == "invalidated"
        payload["source_state"] = "stale" if payload["downstream_invalidated"] and not crop_exists else "active"
        payload["source_stale"] = payload["source_state"] == "stale"
        figure = dict(record.figure_segmentation or {})
        segments = []
        for segment in list(figure.get("segments") or []):
            if not isinstance(segment, dict):
                continue
            row = dict(segment)
            image_path = Path(str(row.get("image_path") or ""))
            row["image_url"] = self._register_file(image_path) if image_path.exists() else ""
            row["image_name"] = image_path.name
            segments.append(row)
        payload["figure_segments_web"] = segments
        structured = dict(record.structured_ocr or {})
        payload["structured_items_web"] = [
            dict(item)
            for item in list(structured.get("items") or [])
            if isinstance(item, dict)
        ]
        return payload

    def _record_detail(self, record_id: str) -> dict[str, Any]:
        record = self.service.staging.get_record(record_id)
        if record is None:
            raise KeyError(record_id)
        return {
            "schema_version": "pdf_factory_web_record_detail_v1",
            "record": self._record_to_web(record),
            "promotion_candidate": self.service.staging.build_promotion_candidate(record_id),
        }

    def _register_file(self, path: Path) -> str:
        if not Path(path).exists():
            return ""
        resolved = Path(path).expanduser().resolve()
        if not self._is_trusted_file(resolved):
            return ""
        token = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()
        self._file_tokens[token] = resolved
        return f"/api/file/{token}"

    def _trusted_file_roots(self) -> list[Path]:
        roots = [
            self.cache_root,
            self.static_root,
            Path(getattr(self.service.staging, "root", "")),
            Path(DEFAULT_PROBLEM_CROPS_LIVE_ROOT),
        ]
        pdf_path = self.context.resolved_pdf_path()
        if str(pdf_path):
            roots.append(pdf_path.parent)
        session_path = self.context.resolved_session_path()
        if session_path is not None:
            roots.append(Path(session_path))
            roots.append(Path(session_path).parent)
        workspace_dir = str(self.context.workspace_dir or "").strip()
        if workspace_dir:
            roots.append(Path(workspace_dir))
        resolved_roots: list[Path] = []
        for root in roots:
            try:
                if str(root):
                    resolved_roots.append(Path(root).expanduser().resolve())
            except Exception:
                continue
        return resolved_roots

    def _is_trusted_file(self, path: Path) -> bool:
        try:
            resolved = Path(path).expanduser().resolve()
        except Exception:
            return False
        if resolved.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".pdf"}:
            return False
        return any(self._is_relative_to(resolved, root) for root in self._trusted_file_roots())

    @staticmethod
    def _is_relative_to(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _send_static(self, handler: BaseHTTPRequestHandler, path: str) -> None:
        name = "index.html" if path in {"", "/"} else path.lstrip("/")
        if "/" in name:
            name = name.split("/")[-1]
        file_path = self.static_root / name
        if not file_path.exists():
            self._send_json(handler, {"error": "not_found"}, status=404)
            return
        self._send_file(handler, file_path, mimetypes.guess_type(str(file_path))[0] or "text/plain")

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
            FactoryWebRuntime._send_json(handler, {"error": "file_not_found"}, status=404)
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
            "schema_version": "pdf_factory_web_error_v1",
            "error": str(exc).strip("'") or code,
            "code": code,
            "status": int(status),
        }
        if include_traceback:
            payload["traceback"] = traceback.format_exc(limit=8)
        FactoryWebRuntime._send_json(handler, payload, status=status)

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
        raw = handler.rfile.read(length).decode("utf-8")
        payload = json.loads(raw or "{}")
        if not isinstance(payload, dict):
            raise ValueError("El payload JSON debe ser un objeto.")
        return dict(payload)

    @staticmethod
    def _first(query: dict[str, list[str]], key: str, default: str = "") -> str:
        values = query.get(key) or []
        return str(values[0]) if values else default

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        return int(value)

    @staticmethod
    def _record_ids_from_payload(payload: dict[str, Any]) -> list[str]:
        raw = payload.get("record_ids")
        if raw in (None, ""):
            return []
        if isinstance(raw, str):
            values = [part.strip() for part in raw.split(",")]
        elif isinstance(raw, list):
            values = raw
        else:
            raise ValueError("record_ids debe ser una lista o texto separado por comas.")
        out: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = str(value or "").strip()
            if item and item not in seen:
                seen.add(item)
                out.append(item)
        return out

    @staticmethod
    def _bool(value: Any, *, default: bool = False) -> bool:
        if value is None:
            return bool(default)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        raw = str(value or "").strip().lower()
        if raw in {"1", "true", "yes", "si", "sí", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
        return bool(default)

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
    def _bounded_float(value: Any, key: str, *, minimum: float, maximum: float) -> float:
        try:
            number = float(value)
        except Exception as exc:
            raise ValueError(f"{key} debe ser numerico.") from exc
        if number < minimum or number > maximum:
            raise ValueError(f"{key} fuera de rango: {minimum}-{maximum}.")
        return number

    @staticmethod
    def _allowed_api_methods(path: str) -> set[str]:
        exact = {
            "/api/bootstrap": {"GET"},
            "/api/pdf/page": {"GET"},
            "/api/record": {"GET"},
            "/api/promotion": {"GET"},
            "/api/pages/detect": {"POST"},
            "/api/pages/boxes": {"POST"},
            "/api/staging/materialize": {"POST"},
            "/api/ocr/run": {"POST"},
            "/api/ocr/raw": {"POST"},
            "/api/ocr/segments/boxes": {"POST"},
            "/api/segments/boxes": {"POST"},
            "/api/normalize": {"POST"},
            "/api/review/save": {"POST"},
        }
        if path.startswith("/api/library/"):
            return LibraryWebApi.allowed_methods(path)
        if path.startswith("/api/file/"):
            return {"GET"}
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


class _FilePayload:
    def __init__(self, path: Path, content_type: str) -> None:
        self.path = Path(path)
        self.content_type = content_type
