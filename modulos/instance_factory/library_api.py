from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from typing import TYPE_CHECKING, Any, Callable
import json
import os
import urllib.parse

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
            return {"GET"}
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
            return self._book_detail(query, _int_id(parts[3], "book_id"))
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
        book_id = self.controller.crear_libro(db_name, _book_input(payload))
        return {
            "schema_version": "library_book_created_v1",
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
        self.controller.actualizar_instancia(db_name, instance_id, _instance_update_input(asdict(_instance_input(merged, book_id=book_id))))
        updated = self._instance_by_id(db_name, book_id, instance_id)
        return {
            "schema_version": "library_instance_state_updated_v1",
            "db_name": db_name,
            "book_id": book_id,
            "instance_id": instance_id,
            "instance": updated,
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
        url = runtime.start()
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


def _policy() -> dict[str, Any]:
    return {
        "target": "staging_only",
        "never_insert_directly_into_problemas": True,
        "promotion_enabled": False,
    }


def _default_runtime_factory(context: InstancePipelineContext) -> Any:
    from .web_server import FactoryWebRuntime

    return FactoryWebRuntime(context)


def _default_open_url(url: str, title: str) -> None:
    from .web_launcher import _open_url

    _open_url(url, title)
