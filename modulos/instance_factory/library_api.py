from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
import json
import os
import urllib.parse

from .library_covers import copy_cover_to_library_store
from .models import InstancePipelineContext

if TYPE_CHECKING:
    from modulos.modulo9_organizador_libros.controlador_organizador_libros import BookProgressController


BOOK_STATES = ("pendiente", "en_progreso", "completo")
OpenUrlCallback = Callable[[str, str], None]
FileUrlResolver = Callable[[str], str]


@dataclass(slots=True)
class LibraryBookInput:
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


@dataclass(slots=True)
class LibraryInstanceInput:
    libro_id: int
    tipo: str
    total_esperado: int = 0
    session_path: str = ""
    soluciones_dir: str = ""
    notas: str = ""
    activo: bool = True


class LibraryApiError(Exception):
    def __init__(self, message: str, *, status: int = 400, code: str = "bad_request") -> None:
        super().__init__(message)
        self.status = int(status)
        self.code = code


class LibraryWebApi:
    """HTTP-facing boundary for the web library.

    BookProgressController remains the source of truth for catalog data. This
    adapter only validates web payloads, serializes controller responses, and
    starts instance-scoped factory runtimes.
    """

    def __init__(
        self,
        *,
        controller: "BookProgressController | None" = None,
        runtime_factory: Callable[[InstancePipelineContext], Any] | None = None,
        open_url: OpenUrlCallback | None = None,
        file_url_resolver: FileUrlResolver | None = None,
    ) -> None:
        self._controller = controller
        self.runtime_factory = runtime_factory or _default_runtime_factory
        self.open_url = open_url or _default_open_url
        self.file_url_resolver = file_url_resolver
        self._factory_runtimes: list[Any] = []

    @property
    def controller(self) -> Any:
        if self._controller is None:
            from modulos.modulo9_organizador_libros.controlador_organizador_libros import BookProgressController

            self._controller = BookProgressController()
        return self._controller

    @staticmethod
    def allowed_methods(path: str) -> set[str]:
        parts = _path_parts(path)
        if parts == ["api", "library", "databases"]:
            return {"GET"}
        if parts == ["api", "library", "books"]:
            return {"GET", "POST"}
        if len(parts) == 4 and parts[:3] == ["api", "library", "books"]:
            return {"GET", "POST"}
        if len(parts) == 5 and parts[:3] == ["api", "library", "books"] and parts[4] == "instances":
            return {"POST"}
        if len(parts) == 5 and parts[:3] == ["api", "library", "books"] and parts[4] == "state":
            return {"POST"}
        if len(parts) == 5 and parts[:3] == ["api", "library", "instances"] and parts[4] == "state":
            return {"POST"}
        if len(parts) == 5 and parts[:3] == ["api", "library", "instances"] and parts[4] == "factory":
            return {"POST"}
        return set()

    def dispatch(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        allowed = self.allowed_methods(path)
        if not allowed:
            raise FileNotFoundError(f"Ruta API no encontrada: {method} {path}")
        if method not in allowed:
            raise LibraryApiError(
                f"Metodo no permitido para {path}: {method}. Permitidos: {', '.join(sorted(allowed))}",
                status=405,
                code="method_not_allowed",
            )

        parts = _path_parts(path)
        if parts == ["api", "library", "databases"]:
            return self._databases()
        if parts == ["api", "library", "books"] and method == "GET":
            return self._books(query)
        if parts == ["api", "library", "books"] and method == "POST":
            return self._create_book(payload)
        if len(parts) == 4 and parts[:3] == ["api", "library", "books"]:
            book_id = _int_id(parts[3], "book_id")
            if method == "GET":
                return self._book_detail(query, book_id)
            return self._update_book(query, payload, book_id)
        if len(parts) == 5 and parts[:3] == ["api", "library", "books"] and parts[4] == "instances":
            return self._create_instance(query, payload, _int_id(parts[3], "book_id"))
        if len(parts) == 5 and parts[:3] == ["api", "library", "books"] and parts[4] == "state":
            return self._update_book_state(query, payload, _int_id(parts[3], "book_id"))
        if len(parts) == 5 and parts[:3] == ["api", "library", "instances"] and parts[4] == "state":
            return self._update_instance_state(payload, _int_id(parts[3], "instance_id"))
        if len(parts) == 5 and parts[:3] == ["api", "library", "instances"] and parts[4] == "factory":
            return self._prepare_factory(payload, _int_id(parts[3], "instance_id"))
        raise FileNotFoundError(f"Ruta API no encontrada: {method} {path}")

    def _databases(self) -> dict[str, Any]:
        dbs = [str(name) for name in self.controller.listar_bases_datos()]
        configured = str(os.getenv("DB_NAME", "") or "").strip()
        selected = configured if configured in dbs else (dbs[0] if dbs else "")
        return {
            "schema_version": "library_databases_v1",
            "databases": dbs,
            "selected_db": selected,
            "count": len(dbs),
        }

    def _books(self, query: dict[str, list[str]]) -> dict[str, Any]:
        db_name = _required_db(query=query)
        books = []
        for row in self.controller.listar_libros(db_name):
            book = self._book_summary(db_name, dict(row))
            book_id = int(book.get("id") or 0)
            if book_id > 0:
                try:
                    book["instances"] = self._lightweight_instances(db_name, book_id, book)
                except Exception:
                    book["instances"] = []
            books.append(book)
        return {
            "schema_version": "library_books_v1",
            "db_name": db_name,
            "books": books,
            "count": len(books),
            "policy": _policy(),
        }

    def _lightweight_instances(self, db_name: str, book_id: int, book: dict[str, Any]) -> list[dict[str, Any]]:
        health_by_type = {
            str(item.get("tipo") or "").strip().lower(): dict(item)
            for item in _parse_instances_health(book)
            if str(item.get("tipo") or "").strip()
        }
        instances = []
        for row in self.controller.listar_instancias_libro(db_name, book_id):
            item = dict(row)
            tipo = str(item.get("tipo") or "").strip().lower()
            health = dict(health_by_type.get(tipo) or {})
            if health:
                item["indicators"] = health
                item["status"] = _health_status_to_web(str(health.get("status") or ""))
            item["factory_available"] = bool(str(book.get("pdf_path") or "").strip())
            item["factory_prepare_endpoint"] = f"/api/library/instances/{int(item.get('id') or 0)}/factory"
            instances.append(item)
        return instances

    def _book_detail(self, query: dict[str, list[str]], book_id: int) -> dict[str, Any]:
        db_name = _required_db(query=query)
        return self._book_detail_payload(db_name, book_id)

    def _create_book(self, payload: dict[str, Any]) -> dict[str, Any]:
        db_name = _required_db(payload=payload)
        data = dict(payload)
        data["cover_path"] = copy_cover_to_library_store(str(data.get("cover_path") or ""), data, db_name=db_name)
        book_id = self.controller.crear_libro(db_name, _book_input(data))
        return {
            "schema_version": "library_book_created_v1",
            "db_name": db_name,
            "book_id": book_id,
            "book": self._book_detail_payload(db_name, book_id)["book"],
            "policy": _policy(),
        }

    def _update_book(self, query: dict[str, list[str]], payload: dict[str, Any], book_id: int) -> dict[str, Any]:
        db_name = _required_db(query=query, payload=payload)
        current = self.controller.obtener_libro(db_name, book_id)
        if not current:
            raise FileNotFoundError("Libro no encontrado.")
        merged = {**dict(current), **payload, "id": book_id}
        merged["cover_path"] = copy_cover_to_library_store(str(merged.get("cover_path") or ""), merged, db_name=db_name)
        data = _book_input(merged)
        self.controller.actualizar_libro(db_name, book_id, _book_update_input(asdict(data)))
        return {
            "schema_version": "library_book_updated_v1",
            "db_name": db_name,
            "book_id": book_id,
            "book": self._book_detail_payload(db_name, book_id)["book"],
            "policy": _policy(),
        }

    def _create_instance(self, query: dict[str, list[str]], payload: dict[str, Any], book_id: int) -> dict[str, Any]:
        db_name = _required_db(query=query, payload=payload)
        instance_id = self.controller.crear_instancia(
            db_name,
            _instance_input(payload, book_id=book_id),
        )
        detail = self._book_detail_payload(db_name, book_id)
        instance = next((row for row in detail["instances"] if int(row.get("id") or 0) == instance_id), None)
        return {
            "schema_version": "library_instance_created_v1",
            "db_name": db_name,
            "book_id": book_id,
            "instance_id": instance_id,
            "instance": instance,
            "book": detail["book"],
            "dashboard": detail["dashboard"],
            "policy": _policy(),
        }

    def _update_book_state(self, query: dict[str, list[str]], payload: dict[str, Any], book_id: int) -> dict[str, Any]:
        db_name = _required_db(query=query, payload=payload)
        state = str(payload.get("estado") or payload.get("state") or "").strip().lower()
        if state not in BOOK_STATES:
            raise ValueError("Estado invalido. Usa pendiente, en_progreso o completo.")
        book = self.controller.obtener_libro(db_name, book_id)
        if not book:
            raise FileNotFoundError("Libro no encontrado.")
        data = _book_input({**book, "estado": state})
        self.controller.actualizar_libro(db_name, book_id, _book_update_input(asdict(data)))
        return {
            "schema_version": "library_book_state_updated_v1",
            "db_name": db_name,
            "book_id": book_id,
            "estado": state,
            "book": self._book_detail_payload(db_name, book_id)["book"],
            "policy": _policy(),
        }

    def _update_instance_state(self, payload: dict[str, Any], instance_id: int) -> dict[str, Any]:
        db_name = _required_db(payload=payload)
        book_id = _required_int(payload, "book_id")
        current = self._instance_by_id(db_name, book_id, instance_id)
        if current is None:
            raise FileNotFoundError("Instancia no encontrada.")
        merged = {**current, **payload, "libro_id": book_id}
        incoming_name = str(
            payload.get("tipo")
            or payload.get("name")
            or payload.get("title")
            or payload.get("instance_type")
            or payload.get("codigo_instancia")
            or ""
        ).strip()
        if incoming_name:
            merged["tipo"] = incoming_name
        self.controller.actualizar_instancia(db_name, instance_id, _instance_update_input(asdict(_instance_input(merged, book_id=book_id))))
        detail = self._book_detail_payload(db_name, book_id)
        updated = next((row for row in detail["instances"] if int(row.get("id") or 0) == int(instance_id)), None)
        return {
            "schema_version": "library_instance_state_updated_v1",
            "db_name": db_name,
            "book_id": book_id,
            "instance_id": instance_id,
            "instance": updated,
            "instances": detail["instances"],
            "book": detail["book"],
            "dashboard": detail["dashboard"],
            "policy": _policy(),
        }

    def _prepare_factory(self, payload: dict[str, Any], instance_id: int) -> dict[str, Any]:
        db_name = _required_db(payload=payload)
        book_id = _required_int(payload, "book_id")
        book = self.controller.obtener_libro(db_name, book_id)
        if not book:
            raise FileNotFoundError("Libro no encontrado.")
        instance = self._instance_by_id(db_name, book_id, instance_id)
        if instance is None:
            raise FileNotFoundError("Instancia no encontrada.")
        context = InstancePipelineContext.from_library_instance(book, instance, db_name=db_name)
        runtime = self.runtime_factory(context)
        setattr(runtime, "_library_db_name", db_name)
        setattr(runtime, "_library_book_id", int(book_id))
        setattr(runtime, "_library_instance_id", int(instance_id))
        embedded = bool(payload.get("embedded") or payload.get("stable") or payload.get("use_library_server"))
        url = "" if embedded else runtime.start()
        self._factory_runtimes.append(runtime)
        opened = bool(payload.get("open") or payload.get("abrir"))
        if opened and self.open_url is not None:
            self.open_url(url, f"Fabrica PDF - {context.book_code} / {context.instance_type}")
        return {
            "schema_version": "library_instance_factory_prepared_v1",
            "db_name": db_name,
            "book_id": book_id,
            "instance_id": instance_id,
            "url": url,
            "opened": opened,
            "context": context.to_dict(),
            "policy": _policy(),
        }

    def _book_detail_payload(self, db_name: str, book_id: int) -> dict[str, Any]:
        book = self.controller.obtener_libro(db_name, book_id)
        if not book:
            raise FileNotFoundError("Libro no encontrado.")
        instances = [dict(row) for row in self.controller.listar_instancias_libro(db_name, book_id)]
        dashboard = _serialize(self.controller.obtener_dashboard_libro(db_name, book_id))
        instance_stats = {int(row.get("instancia_id") or 0): row for row in list(dashboard.get("instancias") or []) if isinstance(row, dict)}
        enriched_instances = []
        for instance in instances:
            row = dict(instance)
            row["factory_available"] = bool(str(book.get("pdf_path") or "").strip())
            row["factory_prepare_endpoint"] = f"/api/library/instances/{int(row.get('id') or 0)}/factory"
            stats = instance_stats.get(int(row.get("id") or 0))
            if stats:
                row["indicators"] = stats
            row["timeline_stage"] = self._instance_timeline_stage(db_name, book, row, stats or {})
            enriched_instances.append(row)
        return {
            "schema_version": "library_book_detail_v1",
            "db_name": db_name,
            "book": self._book_summary(db_name, dict(book), dashboard=dashboard),
            "instances": enriched_instances,
            "dashboard": dashboard,
            "policy": _policy(),
        }

    def _book_summary(self, db_name: str, book: dict[str, Any], *, dashboard: dict[str, Any] | None = None) -> dict[str, Any]:
        row = dict(book)
        row["db_name"] = db_name
        row["code"] = str(row.get("code") or row.get("codigo") or "").strip()
        row["title"] = str(row.get("title") or row.get("titulo") or "").strip()
        row["author"] = str(row.get("author") or row.get("autor") or "").strip()
        row["subject"] = str(row.get("subject") or row.get("curso") or "").strip()
        row["edition"] = str(row.get("edition") or row.get("edicion") or "").strip()
        row["notes"] = str(row.get("notes") or row.get("notas") or "").strip()
        row["status"] = str(row.get("status") or row.get("estado") or "").strip()
        row["active"] = bool(row.get("active", row.get("activo", True)))
        row["workspaceDir"] = str(row.get("workspaceDir") or row.get("workspace_dir") or "").strip()
        row["pdfPath"] = str(row.get("pdfPath") or row.get("pdf_path") or "").strip()
        row["coverPath"] = str(row.get("coverPath") or row.get("cover_path") or "").strip()
        row["detail_endpoint"] = f"/api/library/books/{int(row.get('id') or 0)}"
        cover_path = str(row.get("cover_path") or "").strip()
        row["cover_url"] = self.file_url_resolver(cover_path) if cover_path and self.file_url_resolver else ""
        if dashboard is not None:
            row["indicators"] = {
                "total_instancias": int(dashboard.get("total_instancias") or 0),
                "total_esperado": int(dashboard.get("total_esperado") or 0),
                "escaneados_sesion_total": int(dashboard.get("escaneados_sesion_total") or 0),
                "subidos_bd_total": int(dashboard.get("subidos_bd_total") or 0),
                "faltantes_total": int(dashboard.get("faltantes_total") or 0),
                "porcentaje_total": float(dashboard.get("porcentaje_total") or 0.0),
            }
        else:
            row["indicators"] = {
                "total_instancias": int(row.get("instances_total") or 0),
                "total_esperado": int(row.get("instances_expected_total") or 0),
                "consistentes_total": int(row.get("consistency_consistentes_total") or 0),
                "inconsistentes_total": int(row.get("consistency_inconsistentes_total") or 0),
                "sin_revisar_total": int(row.get("consistency_sin_revisar_total") or 0),
            }
        return row

    def _instance_by_id(self, db_name: str, book_id: int, instance_id: int) -> dict[str, Any] | None:
        for row in self.controller.listar_instancias_libro(db_name, book_id):
            if int(row.get("id") or 0) == int(instance_id):
                return dict(row)
        return None

    def _instance_timeline_stage(
        self,
        db_name: str,
        book: dict[str, Any],
        instance: dict[str, Any],
        indicators: dict[str, Any],
    ) -> dict[str, Any]:
        counts = _empty_timeline_counts(indicators)
        for key, value in self._local_timeline_counts(db_name, book, instance).items():
            if isinstance(value, int):
                counts[key] = max(int(counts.get(key) or 0), int(value))
            elif value:
                counts[key] = value
        return _timeline_stage_from_counts(counts)

    @staticmethod
    def _local_timeline_counts(db_name: str, book: dict[str, Any], instance: dict[str, Any]) -> dict[str, Any]:
        counts: dict[str, Any] = {}
        try:
            from modulos.modulo13_laboratorio_pdf_segmentacion.controlador_laboratorio_pdf import PdfProblemGoldenController

            from .staging import InstanceStagingStore

            context = InstancePipelineContext.from_library_instance(book, instance, db_name=db_name)
            pages = PdfProblemGoldenController().load_instance(context.instance_name)
            by_page: dict[int, Any] = {}
            for index, page in enumerate(pages or []):
                try:
                    page_number = int(page.page_number or 0)
                except Exception:
                    page_number = 0
                if page_number <= 0:
                    continue
                current = by_page.get(page_number)
                current_score = _page_timeline_score(current, -1) if current is not None else None
                next_score = _page_timeline_score(page, index)
                if current is None or (current_score is not None and next_score >= current_score):
                    by_page[page_number] = page
            page_rows = [by_page[key] for key in sorted(by_page)]
            counts["pages_total"] = len(page_rows)
            counts["pages_reviewed"] = sum(1 for row in page_rows if bool(getattr(row, "reviewed", False)))
            counts["boxes_total"] = sum(len(getattr(row, "boxes", None) or []) for row in page_rows)

            store = InstanceStagingStore(context)
            records = store.load_records()
            counts.update(store.summarize_records(records))
        except Exception as exc:
            counts["timeline_error"] = str(exc)
        return counts


def _path_parts(path: str) -> list[str]:
    return [urllib.parse.unquote(part) for part in str(path or "").strip("/").split("/") if part]


def _int_id(raw: str, name: str) -> int:
    try:
        number = int(raw)
    except Exception as exc:
        raise ValueError(f"{name} debe ser entero.") from exc
    if number <= 0:
        raise ValueError(f"{name} debe ser mayor que cero.")
    return number


def _first(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key) or []
    return str(values[0] or "").strip() if values else ""


def _required_db(*, query: dict[str, list[str]] | None = None, payload: dict[str, Any] | None = None) -> str:
    db_name = ""
    if payload is not None:
        db_name = str(payload.get("db_name") or payload.get("db") or "").strip()
    if not db_name and query is not None:
        db_name = _first(query, "db_name") or _first(query, "db")
    if not db_name:
        raise ValueError("db_name es requerido.")
    return db_name


def _required_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    try:
        number = int(value)
    except Exception as exc:
        raise ValueError(f"{key} debe ser entero.") from exc
    if number <= 0:
        raise ValueError(f"{key} debe ser mayor que cero.")
    return number


def _book_input(payload: dict[str, Any]) -> LibraryBookInput:
    return LibraryBookInput(
        codigo=str(payload.get("codigo") or payload.get("code") or payload.get("book_code") or "").strip(),
        titulo=str(payload.get("titulo") or payload.get("title") or payload.get("project_name") or "").strip(),
        autor=str(payload.get("autor") or payload.get("author") or "").strip(),
        editorial=str(payload.get("editorial") or "").strip(),
        edicion=str(payload.get("edicion") or payload.get("edition") or "").strip(),
        curso=str(payload.get("curso") or payload.get("subject") or "").strip(),
        workspace_dir=str(payload.get("workspace_dir") or "").strip(),
        pdf_path=str(payload.get("pdf_path") or payload.get("pdf") or "").strip(),
        cover_path=str(payload.get("cover_path") or "").strip(),
        estado=str(payload.get("estado") or payload.get("state") or "pendiente").strip(),
        notas=str(payload.get("notas") or payload.get("notes") or "").strip(),
        activo=bool(payload.get("activo", True)),
    )


def _book_update_input(payload: dict[str, Any]) -> Any:
    return LibraryBookInput(**payload)


def _instance_input(payload: dict[str, Any], *, book_id: int) -> LibraryInstanceInput:
    return LibraryInstanceInput(
        libro_id=int(book_id),
        tipo=str(
            payload.get("tipo")
            or payload.get("name")
            or payload.get("title")
            or payload.get("instance_type")
            or payload.get("codigo_instancia")
            or ""
        ).strip(),
        total_esperado=max(int(payload.get("total_esperado") or payload.get("expected_total") or 0), 0),
        session_path=str(payload.get("session_path") or "").strip(),
        soluciones_dir=str(payload.get("soluciones_dir") or payload.get("solutions_dir") or "").strip(),
        notas=str(payload.get("notas") or payload.get("notes") or "").strip(),
        activo=bool(payload.get("activo", True)),
    )


def _instance_update_input(payload: dict[str, Any]) -> Any:
    return LibraryInstanceInput(**payload)


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _serialize(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    return value


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


def _health_status_to_web(value: str) -> str:
    key = str(value or "").strip().lower()
    if key == "complete":
        return "listo"
    if key == "complete_with_inconsistencies":
        return "error"
    if key == "in_progress":
        return "requiere_revision"
    return "pendiente"


TIMELINE_STAGE_ROWS: tuple[dict[str, Any], ...] = (
    {"id": "pages", "index": 1, "title": "Paginas"},
    {"id": "boxes", "index": 2, "title": "Boxes"},
    {"id": "crops", "index": 3, "title": "Staging"},
    {"id": "ocr", "index": 4, "title": "OCR"},
    {"id": "review", "index": 5, "title": "Revision"},
    {"id": "candidate", "index": 6, "title": "BD final"},
)


def _empty_timeline_counts(indicators: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = dict(indicators or {})
    return {
        "pages_total": int(raw.get("pages_total") or raw.get("pages") or 0),
        "pages_reviewed": int(raw.get("pages_reviewed") or 0),
        "boxes_total": int(raw.get("boxes_total") or raw.get("boxes") or 0),
        "records_total": int(raw.get("records_total") or raw.get("records") or raw.get("escaneados_sesion") or 0),
        "crops_found": int(raw.get("crops_found") or raw.get("crops_total") or raw.get("crops") or 0),
        "ocr_done": int(raw.get("ocr_done") or raw.get("ocr") or 0),
        "segments_done": int(raw.get("segments_done") or raw.get("segments") or 0),
        "normalized_done": int(raw.get("normalized_done") or raw.get("normalized") or 0),
        "ready": int(raw.get("ready") or 0),
        "errors": int(raw.get("errors") or 0),
        "subidos_bd": int(raw.get("subidos_bd") or 0),
        "total_esperado": int(raw.get("total_esperado") or raw.get("expected_total") or 0),
    }


def _timeline_stage_from_counts(counts: dict[str, Any]) -> dict[str, Any]:
    rows = {str(row["id"]): dict(row) for row in TIMELINE_STAGE_ROWS}
    subidos_bd = int(counts.get("subidos_bd") or 0)
    records_total = int(counts.get("records_total") or 0)
    crops_found = int(counts.get("crops_found") or 0)
    ocr_done = int(counts.get("ocr_done") or 0)
    segments_done = int(counts.get("segments_done") or 0)
    normalized_done = int(counts.get("normalized_done") or 0)
    ready = int(counts.get("ready") or 0)
    boxes_total = int(counts.get("boxes_total") or 0)
    pages_total = int(counts.get("pages_total") or 0)
    pages_reviewed = int(counts.get("pages_reviewed") or 0)
    errors = int(counts.get("errors") or 0)

    if subidos_bd > 0:
        stage_id = "candidate"
        detail = f"{subidos_bd} problema(s) enviados a BD."
        status = "listo"
    elif normalized_done > 0 or ready > 0:
        stage_id = "review"
        detail = f"{normalized_done}/{records_total} borrador(es); {ready} listo(s)."
        status = "requiere_revision" if errors else "procesando"
    elif ocr_done > 0 or segments_done > 0:
        stage_id = "ocr"
        detail = f"{ocr_done}/{records_total} con OCR; {segments_done} con graficos."
        status = "error" if errors else "procesando"
    elif records_total > 0 or crops_found > 0:
        stage_id = "crops"
        detail = f"{crops_found}/{records_total} crop(s) disponibles."
        status = "procesando"
    elif boxes_total > 0:
        stage_id = "boxes"
        detail = f"{boxes_total} box(es) detectados."
        status = "procesando"
    elif pages_total > 0:
        stage_id = "pages"
        detail = f"{pages_total} pagina(s), {pages_reviewed}/{pages_total} revisada(s)."
        status = "procesando" if pages_reviewed or boxes_total else "pendiente"
    else:
        stage_id = "pages"
        detail = "Sin paginas elegidas todavia."
        status = "pendiente"

    row = rows[stage_id]
    return {
        "schema_version": "library_instance_timeline_stage_v1",
        "id": stage_id,
        "index": int(row["index"]),
        "title": str(row["title"]),
        "status": status,
        "detail": detail,
        "counts": {key: int(value) for key, value in counts.items() if isinstance(value, int)},
        "error": str(counts.get("timeline_error") or ""),
    }


def _page_timeline_score(page: Any, index: int) -> tuple[int, int, int, int, str]:
    if page is None:
        return (0, 0, 0, int(index), "")
    try:
        image_path = Path(getattr(page, "image_path", ""))
        image_exists = 1 if image_path.exists() else 0
    except Exception:
        image_exists = 0
    detector = str(getattr(page, "detector_source", "") or "").lower()
    return (
        1 if detector.startswith("pdf_factory") else 0,
        1 if bool(getattr(page, "reviewed", False)) else 0,
        len(getattr(page, "boxes", None) or []),
        image_exists,
        int(index),
        str(getattr(page, "record_id", "") or ""),
    )


def _policy() -> dict[str, Any]:
    return {
        "target": "staging_only",
        "never_insert_directly_into_problemas": True,
        "promotion_enabled": False,
        "explicit_manual_upload_enabled": True,
    }


def _default_runtime_factory(context: InstancePipelineContext) -> Any:
    from .web_server import FactoryWebRuntime

    return FactoryWebRuntime(context)


def _default_open_url(url: str, title: str) -> None:
    from .web_launcher import _open_url

    _open_url(url, title)
