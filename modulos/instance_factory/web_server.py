from __future__ import annotations

import hashlib
import json
import mimetypes
import tempfile
import threading
import traceback
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from modulos.modulo13_laboratorio_pdf_segmentacion.controlador_laboratorio_pdf import (
    DEFAULT_PROBLEM_CROPS_LIVE_ROOT,
)

from .models import InstancePipelineContext, StageStatus, StagingProblemRecord
from .pipeline import InstancePdfPipelineService
from .library_api import LibraryApiError, LibraryWebApi
from .runtime_env import load_factory_runtime_env
from .hf_endpoint_manager import HfEndpointManager
from .normalizer_training_bank import load_manifest as load_normalizer_training_manifest
from .db_promotion import promote_staging_records_to_db


WEB_APP_ASSET_NAMES = ("index.html", "app.js", "styles.css")
WEB_BACKEND_SOURCE_NAMES = (
    "web_server.py",
    "library_web_server.py",
    "library_api.py",
    "pipeline.py",
    "staging.py",
    "db_promotion.py",
    "../modulo9_organizador_libros/controlador_organizador_libros.py",
)
WEB_RELOAD_SIGNAL_PATH = Path(__file__).resolve().parents[2] / ".cache" / "instance_factory" / "web_reload_signal.json"


def _version_rows(paths: list[Path] | tuple[Path, ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        try:
            stat = path.stat()
            row = {"name": path.name, "mtime_ns": int(stat.st_mtime_ns), "size": int(stat.st_size)}
        except Exception:
            row = {"name": path.name, "mtime_ns": 0, "size": 0}
        rows.append(row)
    return rows


def _digest_rows(rows: list[dict[str, Any]]) -> str:
    digest = hashlib.sha1()
    for row in rows:
        digest.update(json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return digest.hexdigest()


def _backend_source_version() -> dict[str, Any]:
    root = Path(__file__).resolve().parent
    rows = _version_rows(tuple((root / name).resolve() for name in WEB_BACKEND_SOURCE_NAMES))
    return {
        "backend_version": _digest_rows(rows),
        "backend_sources": rows,
    }


WEB_BACKEND_BOOT_VERSION = str(_backend_source_version().get("backend_version") or "")


def build_web_app_version(static_root: Path) -> dict[str, Any]:
    root = Path(static_root)
    rows = _version_rows(tuple(root / name for name in WEB_APP_ASSET_NAMES))
    backend = _backend_source_version()
    backend_version = str(backend.get("backend_version") or "")
    reload_token = ""
    try:
        payload = json.loads(WEB_RELOAD_SIGNAL_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            reload_token = str(payload.get("token") or "")
    except Exception:
        reload_token = ""
    return {
        "schema_version": "pdf_factory_web_app_version_v1",
        "asset_version": _digest_rows(rows),
        "reload_token": reload_token,
        "backend_version": backend_version,
        "backend_boot_version": WEB_BACKEND_BOOT_VERSION,
        "backend_restart_required": bool(backend_version and backend_version != WEB_BACKEND_BOOT_VERSION),
        "assets": rows,
        "backend_sources": backend.get("backend_sources") or [],
    }


def signal_web_app_reload(static_root: Path) -> dict[str, Any]:
    WEB_RELOAD_SIGNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    payload = {
        "schema_version": "pdf_factory_web_reload_signal_v1",
        "token": token,
        "asset_version": build_web_app_version(static_root).get("asset_version", ""),
    }
    WEB_RELOAD_SIGNAL_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        **build_web_app_version(static_root),
        "reload_requested": True,
    }


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
        endpoint_manager: HfEndpointManager | None = None,
    ) -> None:
        load_factory_runtime_env()
        self.context = context
        self.service = service or InstancePdfPipelineService(context)
        self.library_api = library_api or LibraryWebApi()
        self.endpoint_manager = endpoint_manager or HfEndpointManager()
        self.static_root = Path(__file__).with_name("web")
        self.cache_root = Path(tempfile.mkdtemp(prefix="pdf_factory_web_"))
        self._file_tokens: dict[str, Path] = {}
        self._lock = threading.RLock()
        self._job_lock = threading.RLock()
        self._endpoint_lifecycle_lock = threading.RLock()
        self._ocr_jobs: dict[str, dict[str, Any]] = {}
        self._active_ocr_job_id = ""
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

            def do_OPTIONS(self) -> None:  # noqa: N802
                runtime._send_options(self)

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
            self._send_error(handler, exc, status=500, code="internal_error")

    def _dispatch_api(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
        payload: dict[str, Any],
    ) -> Any:
        if method == "GET" and path == "/api/app/version":
            return build_web_app_version(self.static_root)
        if method == "POST" and path == "/api/app/reload-signal":
            return signal_web_app_reload(self.static_root)
        if method == "GET" and path == "/api/ocr/jobs/status":
            return self._ocr_job_status(self._first(query, "job_id", ""))
        if method == "POST" and path == "/api/ocr/jobs/start":
            return self._start_ocr_job(payload)
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
            if method == "POST" and path == "/api/promotion/upload":
                dry_run = self._bool(payload.get("dry_run"), default=False)
                if not dry_run and not self._bool(payload.get("confirm"), default=False):
                    raise WebApiError("confirm=true requerido para escribir en la base de datos.", status=400, code="missing_confirmation")
                db_name = self._promotion_db_name(payload)
                record_ids = self._record_ids_from_payload(payload)
                explicit_record_id = str(payload.get("record_id") or "").strip()
                if explicit_record_id and not record_ids:
                    record_ids = [explicit_record_id]
                return promote_staging_records_to_db(
                    self.service.staging,
                    self.context,
                    db_name=db_name,
                    db_profile=self._promotion_db_profile(payload),
                    record_ids=record_ids,
                    dry_run=dry_run,
                )
            if method == "GET" and path == "/api/endpoint/ocr/status":
                return self.endpoint_manager.status()
            if method == "GET" and path == "/api/training/normalizer/status":
                return self._normalizer_training_status()
            if method == "POST" and path == "/api/endpoint/ocr/resume":
                return self.endpoint_manager.resume(
                    wait=self._bool(payload.get("wait"), default=True),
                    timeout_s=self._bounded_int(payload.get("timeout_s") or 420, "timeout_s", minimum=1, maximum=1800),
                    poll_s=self._bounded_int(payload.get("poll_s") or 8, "poll_s", minimum=1, maximum=120),
                )
            if method == "POST" and path == "/api/endpoint/ocr/scale-to-zero":
                return self._scale_endpoint_when_idle(
                    force=self._bool(payload.get("force"), default=False),
                    delay_s=0,
                )
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
                updated = self.service.update_raw_ocr(
                    self._required_str(payload, "record_id"),
                    str(payload.get("raw_ocr") or ""),
                )
                if self._bool(payload.get("compact"), default=False):
                    return {
                        "schema_version": "pdf_factory_web_record_saved_v1",
                        "record": self._record_to_web(updated),
                    }
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
                sync_golden = not self._bool(payload.get("defer_golden_sync"), default=False)
                updated = self.service.staging.update_review(
                    record_id,
                    dict(normalized),
                    notes,
                    mark_ready=mark_ready,
                    sync_golden=sync_golden,
                )
                if self._bool(payload.get("compact"), default=False):
                    return {
                        "schema_version": "pdf_factory_web_record_saved_v1",
                        "record": self._record_to_web(updated),
                    }
                return {
                    "schema_version": "pdf_factory_web_review_saved_v1",
                    "record": self._record_to_web(updated),
                    "snapshot": self._snapshot(),
                }
            raise FileNotFoundError(f"Ruta API no encontrada: {method} {path}")

    def _start_ocr_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        record_ids = self._record_ids_from_payload(payload)
        explicit_record_id = str(payload.get("record_id") or "").strip()
        if not record_ids and explicit_record_id:
            record_ids = [explicit_record_id]
        record_ids = [item for item in dict.fromkeys(record_ids) if item]
        if not record_ids:
            raise WebApiError("record_ids requerido para iniciar cola OCR.", status=400, code="missing_record_ids")
        with self._endpoint_lifecycle_lock, self._job_lock:
            job_id = uuid.uuid4().hex
            job = {
                "schema_version": "pdf_factory_ocr_job_v1",
                "job_id": job_id,
                "status": "queued",
                "record_ids": record_ids,
                "total": len(record_ids),
                "current": 0,
                "ok": 0,
                "failed": 0,
                "active_id": record_ids[0] if record_ids else "",
                "active_name": "",
                "message": f"Cola OCR preparada 0 de {len(record_ids)}",
                "errors": [],
                "payload": {
                    "provider": str(payload.get("provider") or "hf"),
                    "curso": str(payload.get("curso") or "SIN_CURSO"),
                    "tema": str(payload.get("tema") or "SIN_TEMA"),
                    "start_n": self._bounded_int(payload.get("start_n") or 1, "start_n", minimum=1, maximum=100000),
                    "limit": self._optional_int(payload.get("limit")),
                    "ocr_model": str(payload.get("ocr_model") or ""),
                    "figure_model": str(payload.get("figure_model") or ""),
                    "force_figure_model": self._bool(payload.get("force_figure_model"), default=True),
                },
                "endpoint_shutdown": {},
            }
            self._ocr_jobs[job_id] = job
            self._active_ocr_job_id = job_id
            thread = threading.Thread(target=self._run_ocr_job, args=(job_id,), daemon=True)
            job["thread_name"] = thread.name
            response = self._public_ocr_job(job)
            thread.start()
            return response

    def _ocr_job_status(self, job_id: str = "") -> dict[str, Any]:
        with self._job_lock:
            selected = str(job_id or self._current_ocr_job_id() or "").strip()
            job = self._ocr_jobs.get(selected) if selected else None
            if not job:
                return {
                    "schema_version": "pdf_factory_ocr_job_v1",
                    "job_id": selected,
                    "status": "idle",
                    "running": False,
                    "total": 0,
                    "current": 0,
                    "ok": 0,
                    "failed": 0,
                    "errors": [],
                }
            return self._public_ocr_job(job)

    def _current_ocr_job_id(self) -> str:
        active_id = str(self._active_ocr_job_id or "").strip()
        active = self._ocr_jobs.get(active_id)
        if active and self._ocr_job_is_running(active):
            return active_id
        for job_id in reversed(list(self._ocr_jobs.keys())):
            job = self._ocr_jobs.get(job_id)
            if job and self._ocr_job_is_running(job):
                return job_id
        return active_id

    @staticmethod
    def _ocr_job_is_running(job: dict[str, Any]) -> bool:
        return str(job.get("status") or "") in {"queued", "running"}

    def _active_ocr_job_count(self) -> int:
        return sum(1 for job in self._ocr_jobs.values() if self._ocr_job_is_running(job))

    def _public_ocr_job(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = dict(job)
        payload.pop("payload", None)
        payload.pop("thread_name", None)
        payload["running"] = self._ocr_job_is_running(payload)
        payload["active_jobs"] = self._active_ocr_job_count()
        return payload

    def _update_ocr_job(self, job_id: str, **updates: Any) -> None:
        with self._job_lock:
            job = self._ocr_jobs.get(job_id)
            if not job:
                return
            job.update(updates)

    def _begin_endpoint_job(self, *, kind: str, job_id: str, label: str = "") -> str:
        begin_job = getattr(self.endpoint_manager, "begin_job", None)
        if callable(begin_job):
            try:
                return str(begin_job(kind=kind, job_id=job_id, label=label) or "")
            except Exception:
                return ""
        return ""

    def _end_endpoint_job(self, lease_id: str) -> None:
        end_job = getattr(self.endpoint_manager, "end_job", None)
        if callable(end_job):
            try:
                end_job(lease_id)
            except Exception:
                pass

    def _scale_endpoint_when_idle(self, *, force: bool = False, delay_s: float | None = None) -> dict[str, Any]:
        scale_if_idle = getattr(self.endpoint_manager, "scale_to_zero_if_idle", None)
        if callable(scale_if_idle):
            kwargs: dict[str, Any] = {"force": force}
            if delay_s is not None:
                kwargs["delay_s"] = delay_s
            return scale_if_idle(**kwargs)
        return self.endpoint_manager.scale_to_zero()

    def _promotion_db_name(self, payload: dict[str, Any]) -> str:
        library_db = str(getattr(self, "_library_db_name", "") or "").strip()
        explicit_db = str(payload.get("db_name") or payload.get("db") or "").strip()
        context_db = str(self.context.db_name or "").strip()
        if library_db:
            if explicit_db and explicit_db != library_db:
                raise WebApiError(
                    f"La instancia pertenece a la BD local de Biblioteca '{library_db}', no a '{explicit_db}'.",
                    status=400,
                    code="library_db_mismatch",
                )
            return library_db
        target = explicit_db or context_db
        if not target:
            raise WebApiError(
                "No hay BD local de Biblioteca definida para esta instancia.",
                status=400,
                code="missing_local_library_db",
            )
        return target

    def _promotion_db_profile(self, payload: dict[str, Any]) -> str:
        raw = str(payload.get("db_profile") or payload.get("profile") or "local_mirror").strip().lower()
        aliases = {"", "local", "local_mirror", "internal", "interna", "biblioteca"}
        if raw not in aliases:
            raise WebApiError(
                "La subida final solo puede escribir en la BD local/interna usada por Biblioteca.",
                status=400,
                code="non_local_db_profile_blocked",
            )
        return "local_mirror"

    def _run_ocr_job(self, job_id: str) -> None:
        with self._job_lock:
            job = self._ocr_jobs.get(job_id)
            if not job:
                return
            record_ids = list(job.get("record_ids") or [])
            options = dict(job.get("payload") or {})
        total = len(record_ids)
        provider_key = str(options.get("provider") or "hf").strip().lower()
        self._update_ocr_job(job_id, status="running", message=f"Segmentando graficos 0 de {total}", phase="segmentation")
        for index, record_id in enumerate(record_ids):
            self._update_ocr_job(
                job_id,
                current=index,
                active_id=record_id,
                active_event={"phase": "segmentation", "record_id": record_id},
                message=f"Segmentando graficos {index + 1} de {total}",
            )
            try:
                self.service.run_ocr_and_segmentation(
                    provider=str(options.get("provider") or "hf"),
                    curso=str(options.get("curso") or "SIN_CURSO"),
                    tema=str(options.get("tema") or "SIN_TEMA"),
                    start_n=int(options.get("start_n") or 1),
                    limit=1,
                    ocr_model=str(options.get("ocr_model") or ""),
                    figure_model=str(options.get("figure_model") or ""),
                    force_figure_model=bool(options.get("force_figure_model", True)),
                    record_id=str(record_id),
                    record_ids=[],
                    run_segmentation=True,
                    run_ocr=False,
                )
            except Exception as exc:
                with self._job_lock:
                    job = self._ocr_jobs.get(job_id) or {}
                    errors = list(job.get("errors") or [])
                    errors.append({"record_id": record_id, "phase": "segmentation", "message": str(exc)})
                    job["errors"] = errors[-50:]
                    job["message"] = f"Error de segmentacion en {index + 1} de {total}; se intentara OCR igual"
        endpoint_lease_id = ""
        if provider_key == "hf":
            endpoint_lease_id = self._begin_endpoint_job(
                kind="factory_ocr",
                job_id=job_id,
                label=f"{self.context.instance_type} ({total} imagenes)",
            )
        self._update_ocr_job(job_id, current=0, message=f"Ejecutando OCR remoto 0 de {total}", phase="ocr")
        try:
            for index, record_id in enumerate(record_ids):
                self._update_ocr_job(
                    job_id,
                    current=index,
                    active_id=record_id,
                    active_event={"phase": "ocr", "record_id": record_id},
                    message=f"OCR remoto {index + 1} de {total}",
                )
                def _progress(event: dict[str, Any], *, _record_id: str = str(record_id)) -> None:
                    with self._job_lock:
                        job = self._ocr_jobs.get(job_id)
                        if not job:
                            return
                        clean_event = dict(event or {})
                        job["active_id"] = _record_id
                        job["active_event"] = clean_event
                        message = str(clean_event.get("message") or "").strip()
                        if message:
                            job["message"] = message

                try:
                    self.service.run_ocr_and_segmentation(
                        provider=str(options.get("provider") or "hf"),
                        curso=str(options.get("curso") or "SIN_CURSO"),
                        tema=str(options.get("tema") or "SIN_TEMA"),
                        start_n=int(options.get("start_n") or 1),
                        limit=1,
                        ocr_model=str(options.get("ocr_model") or ""),
                        figure_model=str(options.get("figure_model") or ""),
                        force_figure_model=bool(options.get("force_figure_model", True)),
                        record_id=str(record_id),
                        record_ids=[],
                        progress_callback=_progress,
                        run_segmentation=False,
                        run_ocr=True,
                    )
                    with self._job_lock:
                        job = self._ocr_jobs.get(job_id) or {}
                        job["ok"] = int(job.get("ok") or 0) + 1
                        job["current"] = index + 1
                        job["message"] = f"Guardado {index + 1} de {total}"
                except Exception as exc:
                    with self._job_lock:
                        job = self._ocr_jobs.get(job_id) or {}
                        errors = list(job.get("errors") or [])
                        errors.append({"record_id": record_id, "message": str(exc)})
                        job["errors"] = errors[-50:]
                        job["failed"] = int(job.get("failed") or 0) + 1
                        job["current"] = index + 1
                        job["message"] = f"Error en {index + 1} de {total}"
        finally:
            self._end_endpoint_job(endpoint_lease_id)
        should_shutdown = False
        endpoint_shutdown: dict[str, Any] = {}
        with self._job_lock:
            job = self._ocr_jobs.get(job_id)
            if job:
                failed = int(job.get("failed") or 0)
                job["status"] = "error" if failed else "done"
                job["running"] = False
                job["current"] = total
                job["message"] = f"Cola terminada con {failed} error(es)." if failed else f"OCR terminado para {total} imagen(es)."
                should_shutdown = self._active_ocr_job_count() == 0
                if not should_shutdown:
                    endpoint_shutdown = {
                        "status": "skipped",
                        "reason": "other_ocr_jobs_running",
                        "message": "Endpoint OCR se mantiene activo porque hay otros jobs en curso.",
                    }
                    job["endpoint_shutdown"] = endpoint_shutdown
        if should_shutdown:
            with self._endpoint_lifecycle_lock:
                with self._job_lock:
                    should_shutdown = self._active_ocr_job_count() == 0
                if should_shutdown:
                    try:
                        endpoint_shutdown = self._scale_endpoint_when_idle()
                    except Exception as exc:
                        endpoint_shutdown = {"error": str(exc)}
                else:
                    endpoint_shutdown = {
                        "status": "skipped",
                        "reason": "other_ocr_jobs_running",
                        "message": "Endpoint OCR se mantiene activo porque hay otros jobs en curso.",
                    }
            with self._job_lock:
                job = self._ocr_jobs.get(job_id)
                if job:
                    job["endpoint_shutdown"] = endpoint_shutdown

    def _normalizer_training_status(self) -> dict[str, Any]:
        manifest = load_normalizer_training_manifest()
        samples_total = int(manifest.get("samples_total") or 0)
        threshold = int(manifest.get("threshold") or 200)
        return {
            **manifest,
            "schema_version": "normalizer_training_bank_status_v1",
            "samples_total": samples_total,
            "threshold": threshold,
            "ready_to_train": samples_total >= threshold,
            "notification": (
                f"Ya hay {samples_total} muestras listas para entrenar una primera version del normalizador."
                if samples_total >= threshold
                else ""
            ),
        }

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
                "explicit_manual_upload_enabled": True,
            },
        }

    def _pdf_info(self) -> dict[str, Any]:
        pdf_path = self.context.resolved_pdf_path()
        info: dict[str, Any] = {
            "path": str(pdf_path),
            "name": pdf_path.name,
            "exists": pdf_path.exists(),
            "page_count": 0,
            "size": 0,
            "mtime_ns": 0,
        }
        if not pdf_path.exists():
            return info
        try:
            stat = pdf_path.stat()
            info["size"] = int(stat.st_size)
            info["mtime_ns"] = int(stat.st_mtime_ns)
        except Exception:
            pass
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
        target = self.cache_root / f"pdf_page_{self._pdf_cache_key(pdf_path)}_{int(page_number):04d}_{dpi}.png"
        if target.exists():
            return target
        import fitz

        with fitz.open(pdf_path) as document:
            if page_number < 1 or page_number > document.page_count:
                raise ValueError(f"Pagina fuera del PDF: {page_number}")
            matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
            document[page_number - 1].get_pixmap(matrix=matrix, alpha=False).save(str(target))
        return target

    @staticmethod
    def _pdf_cache_key(pdf_path: Path) -> str:
        try:
            resolved = pdf_path.expanduser().resolve()
            stat = resolved.stat()
            raw = f"{resolved}|{int(stat.st_size)}|{int(stat.st_mtime_ns)}"
        except Exception:
            raw = str(pdf_path)
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:12]

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
        instance_id = ""
        try:
            raw_instance_id = int(getattr(self, "_library_instance_id", 0) or 0)
            if raw_instance_id > 0:
                instance_id = f"?instance_id={raw_instance_id}"
        except Exception:
            instance_id = ""
        return f"/api/file/{token}{instance_id}"

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
    def _send_cors_headers(handler: BaseHTTPRequestHandler) -> None:
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        handler.send_header("Access-Control-Allow-Headers", "Content-Type")

    @staticmethod
    def _send_options(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(204)
        FactoryWebRuntime._send_cors_headers(handler)
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()

    @staticmethod
    def _send_json(handler: BaseHTTPRequestHandler, payload: Any, *, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(raw)))
        handler.send_header("Cache-Control", "no-store")
        FactoryWebRuntime._send_cors_headers(handler)
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
        FactoryWebRuntime._send_cors_headers(handler)
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
            message = "Error interno de la Fabrica. Revisa el log local para mas detalle."
        payload: dict[str, Any] = {
            "schema_version": "pdf_factory_web_error_v1",
            "error": message,
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
            "/api/app/version": {"GET"},
            "/api/app/reload-signal": {"POST"},
            "/api/pdf/page": {"GET"},
            "/api/record": {"GET"},
            "/api/promotion": {"GET"},
            "/api/promotion/upload": {"POST"},
            "/api/endpoint/ocr/status": {"GET"},
            "/api/ocr/jobs/status": {"GET"},
            "/api/training/normalizer/status": {"GET"},
            "/api/endpoint/ocr/resume": {"POST"},
            "/api/endpoint/ocr/scale-to-zero": {"POST"},
            "/api/ocr/jobs/start": {"POST"},
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
