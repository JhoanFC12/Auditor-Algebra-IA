from __future__ import annotations

import base64
import binascii
import copy
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
import inspect
import json
import os
import time
import urllib.parse

from .library_covers import copy_cover_to_library_store, save_cover_bytes
from .models import (
    PROBLEM_SOLUTION_STATUSES,
    PROBLEM_SOLUTION_STRUCTURE_MODES,
    PROBLEM_SOLUTION_STRUCTURE_SCHEMA_VERSION,
    SOLUTION_PAGE_SELECTION_SCHEMA_VERSION,
    InstancePipelineContext,
)

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
    titulo_practica: str = ""
    pdf_path: str = ""
    session_path: str = ""
    soluciones_dir: str = ""
    nombre_instancia: str = ""
    estado: str = "pendiente"
    config_snapshot: dict[str, Any] | None = None
    session_schema_version: str = ""
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
        semantic_similarity_fetcher: Callable[..., dict[str, Any]] | None = None,
        semantic_status_fetcher: Callable[..., dict[str, Any]] | None = None,
        semantic_similarity_reviewer: Callable[..., dict[str, Any]] | None = None,
        semantic_practice_fetcher: Callable[..., dict[str, Any]] | None = None,
        semantic_practice_saver: Callable[..., dict[str, Any]] | None = None,
        semantic_practice_lister: Callable[..., dict[str, Any]] | None = None,
        semantic_concept_fetcher: Callable[..., dict[str, Any]] | None = None,
        semantic_concept_problem_fetcher: Callable[..., dict[str, Any]] | None = None,
        semantic_concept_link_reviewer: Callable[..., dict[str, Any]] | None = None,
        semantic_problem_concept_fetcher: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self._controller = controller
        self.runtime_factory = runtime_factory or _default_runtime_factory
        self.open_url = open_url or _default_open_url
        self.file_url_resolver = file_url_resolver
        self.semantic_similarity_fetcher = semantic_similarity_fetcher
        self.semantic_status_fetcher = semantic_status_fetcher
        self.semantic_similarity_reviewer = semantic_similarity_reviewer
        self.semantic_practice_fetcher = semantic_practice_fetcher
        self.semantic_practice_saver = semantic_practice_saver
        self.semantic_practice_lister = semantic_practice_lister
        self.semantic_concept_fetcher = semantic_concept_fetcher
        self.semantic_concept_problem_fetcher = semantic_concept_problem_fetcher
        self.semantic_concept_link_reviewer = semantic_concept_link_reviewer
        self.semantic_problem_concept_fetcher = semantic_problem_concept_fetcher
        self._factory_runtimes: list[Any] = []
        self._factory_runtime_by_instance: dict[tuple[str, int, int], Any] = {}
        self._response_cache: dict[tuple[str, str, tuple[tuple[str, tuple[str, ...]], ...]], tuple[float, dict[str, Any]]] = {}
        self._response_cache_ttl_s = 2.0
        self._local_timeline_cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
        self._local_timeline_cache_ttl_s = 8.0
        self._timeline_golden_controller: Any | None = None

    @property
    def controller(self) -> Any:
        if self._controller is None:
            try:
                from modulos.modulo9_organizador_libros.controlador_organizador_libros import BookProgressController
            except ModuleNotFoundError as exc:
                if exc.name == "psycopg2":
                    raise LibraryApiError(
                        "Biblioteca no puede conectar con la base local: falta instalar psycopg2 en este entorno de Python.",
                        status=503,
                        code="library_dependency_missing",
                    ) from exc
                raise

            self._controller = BookProgressController()
        return self._controller

    @staticmethod
    def allowed_methods(path: str) -> set[str]:
        parts = _path_parts(path)
        if parts == ["api", "library", "databases"]:
            return {"GET"}
        if parts == ["api", "library", "books"]:
            return {"GET", "POST"}
        if parts == ["api", "library", "practice-drafts"]:
            return {"GET"}
        if parts == ["api", "library", "concepts"]:
            return {"GET"}
        if parts == ["api", "library", "cover", "paste"]:
            return {"POST"}
        if len(parts) == 5 and parts[:3] == ["api", "library", "concepts"] and parts[4] == "problems":
            return {"GET"}
        if len(parts) == 7 and parts[:3] == ["api", "library", "concepts"] and parts[4] == "problems" and parts[6] == "review":
            return {"POST"}
        if len(parts) == 4 and parts[:3] == ["api", "library", "books"]:
            return {"GET", "POST", "DELETE"}
        if len(parts) == 5 and parts[:3] == ["api", "library", "books"] and parts[4] == "instances":
            return {"POST"}
        if len(parts) == 5 and parts[:3] == ["api", "library", "books"] and parts[4] == "state":
            return {"POST"}
        if len(parts) == 5 and parts[:3] == ["api", "library", "instances"] and parts[4] == "state":
            return {"POST"}
        if len(parts) == 5 and parts[:3] == ["api", "library", "instances"] and parts[4] == "page-selection":
            return {"POST"}
        if len(parts) == 5 and parts[:3] == ["api", "library", "instances"] and parts[4] == "factory":
            return {"POST"}
        if len(parts) == 5 and parts[:3] == ["api", "library", "problems"] and parts[4] == "similar":
            return {"GET"}
        if len(parts) == 5 and parts[:3] == ["api", "library", "problems"] and parts[4] == "concepts":
            return {"GET"}
        if len(parts) == 5 and parts[:3] == ["api", "library", "problems"] and parts[4] == "practice-draft":
            return {"GET", "POST"}
        if len(parts) == 5 and parts[:3] == ["api", "library", "problems"] and parts[4] == "practice-drafts":
            return {"GET"}
        if len(parts) == 7 and parts[:3] == ["api", "library", "problems"] and parts[4] == "similar" and parts[6] == "review":
            return {"POST"}
        if parts == ["api", "library", "semantic", "status"]:
            return {"GET"}
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

        cache_key = self._cache_key(method, path, query) if method == "GET" else None
        if cache_key is not None:
            cached = self._get_cached_response(cache_key)
            if cached is not None:
                return cached
        elif method != "GET" or _query_bool(query, "no_cache", default=False):
            self._invalidate_response_cache()

        result = self._dispatch_uncached(method, path, query, payload)
        if cache_key is not None:
            self._set_cached_response(cache_key, result)
        return result

    def _dispatch_uncached(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        parts = _path_parts(path)
        if parts == ["api", "library", "databases"]:
            return self._databases()
        if parts == ["api", "library", "books"] and method == "GET":
            return self._books(query)
        if parts == ["api", "library", "books"] and method == "POST":
            return self._create_book(payload)
        if parts == ["api", "library", "cover", "paste"]:
            return self._save_pasted_cover(payload)
        if parts == ["api", "library", "practice-drafts"]:
            return self._practice_draft_catalog(query)
        if parts == ["api", "library", "concepts"]:
            return self._semantic_concepts(query)
        if len(parts) == 5 and parts[:3] == ["api", "library", "concepts"] and parts[4] == "problems":
            return self._semantic_concept_problems(query, _int_id(parts[3], "concept_id"))
        if len(parts) == 7 and parts[:3] == ["api", "library", "concepts"] and parts[4] == "problems" and parts[6] == "review":
            return self._review_semantic_concept_problem_link(
                query,
                payload,
                _int_id(parts[3], "concept_id"),
                _int_id(parts[5], "problem_id"),
            )
        if len(parts) == 4 and parts[:3] == ["api", "library", "books"]:
            book_id = _int_id(parts[3], "book_id")
            if method == "GET":
                return self._book_detail(query, book_id)
            if method == "DELETE":
                return self._delete_book(query, payload, book_id)
            return self._update_book(query, payload, book_id)
        if len(parts) == 5 and parts[:3] == ["api", "library", "books"] and parts[4] == "instances":
            return self._create_instance(query, payload, _int_id(parts[3], "book_id"))
        if len(parts) == 5 and parts[:3] == ["api", "library", "books"] and parts[4] == "state":
            return self._update_book_state(query, payload, _int_id(parts[3], "book_id"))
        if len(parts) == 5 and parts[:3] == ["api", "library", "instances"] and parts[4] == "state":
            return self._update_instance_state(payload, _int_id(parts[3], "instance_id"))
        if len(parts) == 5 and parts[:3] == ["api", "library", "instances"] and parts[4] == "page-selection":
            return self._update_instance_page_selection(payload, _int_id(parts[3], "instance_id"))
        if len(parts) == 5 and parts[:3] == ["api", "library", "instances"] and parts[4] == "factory":
            return self._prepare_factory(payload, _int_id(parts[3], "instance_id"))
        if len(parts) == 5 and parts[:3] == ["api", "library", "problems"] and parts[4] == "similar":
            return self._problem_similarity(query, _int_id(parts[3], "problem_id"))
        if len(parts) == 5 and parts[:3] == ["api", "library", "problems"] and parts[4] == "concepts":
            return self._problem_concepts(query, _int_id(parts[3], "problem_id"))
        if len(parts) == 5 and parts[:3] == ["api", "library", "problems"] and parts[4] == "practice-draft":
            problem_id = _int_id(parts[3], "problem_id")
            if method == "GET":
                return self._problem_practice_draft(query, problem_id)
            return self._save_problem_practice_draft(query, payload, problem_id)
        if len(parts) == 5 and parts[:3] == ["api", "library", "problems"] and parts[4] == "practice-drafts":
            return self._problem_practice_drafts(query, _int_id(parts[3], "problem_id"))
        if len(parts) == 7 and parts[:3] == ["api", "library", "problems"] and parts[4] == "similar" and parts[6] == "review":
            return self._review_problem_similarity(
                query,
                payload,
                _int_id(parts[3], "problem_id"),
                _int_id(parts[5], "similar_problem_id"),
            )
        if parts == ["api", "library", "semantic", "status"]:
            return self._semantic_status(query)
        raise FileNotFoundError(f"Ruta API no encontrada: {method} {path}")

    @staticmethod
    def _cache_key(
        method: str,
        path: str,
        query: dict[str, list[str]],
    ) -> tuple[str, str, tuple[tuple[str, tuple[str, ...]], ...]] | None:
        if _query_bool(query, "no_cache", default=False):
            return None
        normalized_query = tuple(
            sorted(
                (str(key), tuple(str(value) for value in values))
                for key, values in (query or {}).items()
                if str(key) not in {"_", "ts", "cache_bust"}
            )
        )
        return (str(method).upper(), str(path), normalized_query)

    def _get_cached_response(
        self,
        key: tuple[str, str, tuple[tuple[str, tuple[str, ...]], ...]],
    ) -> dict[str, Any] | None:
        cached = self._response_cache.get(key)
        if cached is None:
            return None
        created_at, payload = cached
        if time.monotonic() - created_at > self._response_cache_ttl_s:
            self._response_cache.pop(key, None)
            return None
        return copy.deepcopy(payload)

    def _set_cached_response(
        self,
        key: tuple[str, str, tuple[tuple[str, tuple[str, ...]], ...]],
        payload: dict[str, Any],
    ) -> None:
        self._response_cache[key] = (time.monotonic(), copy.deepcopy(payload))

    def _invalidate_response_cache(self) -> None:
        self._response_cache.clear()
        self._local_timeline_cache.clear()

    def _databases(self) -> dict[str, Any]:
        dbs = [str(name) for name in self.controller.listar_bases_datos()]
        configured = str(getattr(getattr(self.controller, "db", None), "db_name", "") or os.getenv("DB_NAME", "") or "").strip()
        selected = configured if configured in dbs else (dbs[0] if dbs else "")
        db_error = str(getattr(getattr(self.controller, "db", None), "last_connection_error", "") or "").strip()
        status = "ready" if dbs else ("error" if db_error else "empty")
        return {
            "schema_version": "library_databases_v1",
            "databases": dbs,
            "selected_db": selected,
            "count": len(dbs),
            "status": status,
            "error": db_error,
            "message": (
                f"No se pudo conectar a la base local configurada ({configured or 'sin DB_NAME'}): {db_error}"
                if db_error
                else ("" if dbs else "No hay bases de datos disponibles.")
            ),
        }

    def _books(self, query: dict[str, list[str]]) -> dict[str, Any]:
        db_name = _required_db(query=query)
        include_instances = _query_bool(query, "include_instances", default=False)
        books = []
        for row in self._list_books(db_name, include_instance_health=include_instances):
            raw_book = dict(row)
            book = self._book_summary(db_name, raw_book)
            book_id = int(book.get("id") or 0)
            if include_instances and book_id > 0:
                try:
                    book["instances"] = self._lightweight_instances(db_name, book_id, raw_book)
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

    def _list_books(self, db_name: str, *, include_instance_health: bool = False) -> list[dict[str, Any]]:
        try:
            return [dict(row) for row in self.controller.listar_libros(db_name, include_instance_health=include_instance_health)]
        except TypeError as exc:
            if "include_instance_health" not in str(exc):
                raise
            return [dict(row) for row in self.controller.listar_libros(db_name)]

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
        book = self.controller.obtener_libro(db_name, book_id) or {"id": book_id, **data}
        return {
            "schema_version": "library_book_created_v1",
            "db_name": db_name,
            "book_id": book_id,
            "book": self._book_summary(db_name, dict(book)),
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
        updated = self.controller.obtener_libro(db_name, book_id) or asdict(data)
        return {
            "schema_version": "library_book_updated_v1",
            "db_name": db_name,
            "book_id": book_id,
            "book": self._book_summary(db_name, dict(updated)),
            "policy": _policy(),
        }

    def _delete_book(self, query: dict[str, list[str]], payload: dict[str, Any], book_id: int) -> dict[str, Any]:
        db_name = _required_db(query=query, payload=payload)
        current = self.controller.obtener_libro(db_name, book_id)
        if not current:
            raise FileNotFoundError("Libro no encontrado.")
        title = str(current.get("titulo") or current.get("title") or "").strip()
        confirmation = str(payload.get("confirmation") or "").strip()
        if not confirmation or confirmation != title:
            raise LibraryApiError(
                "La confirmacion no coincide con el titulo del libro.",
                status=409,
                code="book_delete_confirmation_mismatch",
            )
        instances = self.controller.listar_instancias_libro(db_name, book_id)
        self.controller.eliminar_libro(db_name, book_id)
        return {
            "schema_version": "library_book_deleted_v1",
            "db_name": db_name,
            "book_id": int(book_id),
            "title": title,
            "deleted_instances": len(instances),
            "files_deleted": False,
            "policy": _policy(),
        }

    def _save_pasted_cover(self, payload: dict[str, Any]) -> dict[str, Any]:
        db_name = _required_db(payload=payload)
        book = self._cover_book_payload(payload, db_name=db_name)
        upload_payload = {**book, **dict(payload or {})} if book else dict(payload or {})
        raw, suffix = _decode_cover_payload(payload)
        file_path = save_cover_bytes(raw, suffix, upload_payload, db_name=db_name)
        cover_path = str(file_path)
        attached = False
        if _truthy(payload.get("attach")):
            self._attach_cover_to_book(payload, cover_path, db_name=db_name, book=book)
            attached = True
        return {
            "schema_version": "library_cover_pasted_v1",
            "db_name": db_name,
            "cover_path": cover_path,
            "cover_url": self.file_url_resolver(cover_path) if self.file_url_resolver else "",
            "bytes": len(raw),
            "attached": attached,
        }

    def _cover_book_payload(self, payload: dict[str, Any], *, db_name: str) -> dict[str, Any]:
        try:
            book_id = int(payload.get("book_id") or 0)
        except Exception:
            book_id = 0
        if not book_id:
            return {}
        book = self.controller.obtener_libro(db_name, book_id)
        return dict(book or {})

    def _attach_cover_to_book(
        self,
        payload: dict[str, Any],
        cover_path: str,
        *,
        db_name: str,
        book: dict[str, Any] | None = None,
    ) -> None:
        try:
            book_id = int(payload.get("book_id") or 0)
        except Exception:
            book_id = 0
        if not book_id:
            raise ValueError("book_id es requerido para asociar portada.")
        current = dict(book or self.controller.obtener_libro(db_name, book_id) or {})
        if not current:
            raise FileNotFoundError("Libro no encontrado.")
        merged = {**current, "cover_path": cover_path, "id": book_id}
        data = _book_input(merged)
        self.controller.actualizar_libro(db_name, book_id, _book_update_input(asdict(data)))

    def _create_instance(self, query: dict[str, list[str]], payload: dict[str, Any], book_id: int) -> dict[str, Any]:
        db_name = _required_db(query=query, payload=payload)
        instance_id = self.controller.crear_instancia(
            db_name,
            _instance_input(payload, book_id=book_id),
        )
        book = self.controller.obtener_libro(db_name, book_id) or {"id": book_id}
        instance = self._instance_by_id(db_name, book_id, instance_id)
        if instance is not None:
            instance = self._lightweight_instance(db_name, dict(book), dict(instance))
        return {
            "schema_version": "library_instance_created_v1",
            "db_name": db_name,
            "book_id": book_id,
            "instance_id": instance_id,
            "instance": instance,
            "book": self._book_summary(db_name, dict(book)),
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
        updated = self.controller.obtener_libro(db_name, book_id) or {**book, "estado": state}
        return {
            "schema_version": "library_book_state_updated_v1",
            "db_name": db_name,
            "book_id": book_id,
            "estado": state,
            "book": self._book_summary(db_name, dict(updated)),
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
        self._factory_runtime_by_instance.pop((db_name, int(book_id), int(instance_id)), None)
        book = self.controller.obtener_libro(db_name, book_id) or {"id": book_id}
        updated = self._instance_by_id(db_name, book_id, instance_id) or merged
        updated = self._lightweight_instance(db_name, dict(book), dict(updated))
        return {
            "schema_version": "library_instance_state_updated_v1",
            "db_name": db_name,
            "book_id": book_id,
            "instance_id": instance_id,
            "instance": updated,
            "book": self._book_summary(db_name, dict(book)),
            "policy": _policy(),
        }

    def _update_instance_page_selection(self, payload: dict[str, Any], instance_id: int) -> dict[str, Any]:
        db_name = _required_db(payload=payload)
        book_id = _required_int(payload, "book_id")
        current = self._instance_by_id(db_name, book_id, instance_id)
        if current is None:
            raise FileNotFoundError("Instancia no encontrada.")
        try:
            page_count = max(0, int(payload.get("page_count") or 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("page_count debe ser entero.") from exc
        ordered_pages = _normalize_selected_pages(
            payload.get("selected_pages"),
            page_count=page_count,
            field_name="selected_pages",
        )
        page_ranges = _page_ranges_from_pages(ordered_pages)
        range_display = _page_range_display(page_ranges)
        config_snapshot = _dict_payload(current.get("config_snapshot")) or {}
        previous_pages = _configured_page_selection(config_snapshot)
        source = str(payload.get("source") or "web_ui").strip() or "web_ui"
        updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        selection_payload = {
            "schema_version": "library_instance_page_selection_v1",
            "selected_pages": ordered_pages,
            "page_ranges": page_ranges,
            "page_range_display": range_display,
            "source": source,
            "status": "proposed",
            "review_status": "pending",
            "updated_at": updated_at,
        }
        next_snapshot = {
            **config_snapshot,
            "selected_pages": ordered_pages,
            "page_ranges": page_ranges,
            "page_range_display": range_display,
            "page_selection": selection_payload,
        }
        previous_selection = _dict_payload(config_snapshot.get("page_selection")) or {}
        problem_selection_changed = (
            previous_pages != ordered_pages
            or previous_selection.get("page_ranges") != page_ranges
            or str(previous_selection.get("source") or "") != selection_payload["source"]
            or str(previous_selection.get("status") or "") != "proposed"
            or str(previous_selection.get("review_status") or "") != "pending"
        )

        previous_solution_selection = _dict_payload(config_snapshot.get("solution_page_selection")) or {}
        current_solution_pages = _configured_page_selection(
            config_snapshot,
            selection_key="solution_page_selection",
            selected_pages_key="solution_selected_pages",
            page_ranges_key="solution_page_ranges",
        )
        solution_selection_provided = "solution_selected_pages" in payload
        if solution_selection_provided:
            solution_selected_pages = _normalize_selected_pages(
                payload.get("solution_selected_pages"),
                page_count=page_count,
                field_name="solution_selected_pages",
            )
            solution_page_ranges = _page_ranges_from_pages(solution_selected_pages)
            solution_page_range_display = _page_range_display(solution_page_ranges)
            solution_selection_payload = {
                "schema_version": SOLUTION_PAGE_SELECTION_SCHEMA_VERSION,
                "selected_pages": solution_selected_pages,
                "page_ranges": solution_page_ranges,
                "page_range_display": solution_page_range_display,
                "source": source,
                "status": "proposed",
                "review_status": "pending",
                "updated_at": updated_at,
            }
            next_snapshot.update(
                {
                    "solution_selected_pages": solution_selected_pages,
                    "solution_page_ranges": solution_page_ranges,
                    "solution_page_range_display": solution_page_range_display,
                    "solution_page_selection": solution_selection_payload,
                }
            )
            solution_selection_changed = (
                current_solution_pages != solution_selected_pages
                or previous_solution_selection.get("page_ranges") != solution_page_ranges
                or str(previous_solution_selection.get("source") or "") != source
                or str(previous_solution_selection.get("status") or "") != "proposed"
                or str(previous_solution_selection.get("review_status") or "") != "pending"
            )
        else:
            solution_selected_pages = current_solution_pages
            solution_page_ranges = _page_ranges_from_pages(solution_selected_pages)
            solution_selection_payload = previous_solution_selection
            solution_selection_changed = False

        previous_structure = _dict_payload(config_snapshot.get("problem_solution_structure")) or {}
        raw_structure = _dict_payload(payload.get("problem_solution_structure"))
        structure_provided = raw_structure is not None or any(
            key in payload for key in ("structure_mode", "solution_status", "exercise_set_id")
        )
        if structure_provided:
            structure_input = {**previous_structure, **dict(raw_structure or {})}
            for key in ("structure_mode", "solution_status", "exercise_set_id"):
                if key in payload:
                    structure_input[key] = payload.get(key)
            problem_solution_structure = _normalize_problem_solution_structure(
                structure_input,
                source=source,
                updated_at=updated_at,
                strict=True,
            )
            next_snapshot["problem_solution_structure"] = problem_solution_structure
            structure_changed = any(
                previous_structure.get(key) != problem_solution_structure.get(key)
                for key in (
                    "schema_version",
                    "structure_mode",
                    "solution_status",
                    "exercise_set_id",
                    "source",
                    "review_status",
                )
            )
        else:
            problem_solution_structure = _normalize_problem_solution_structure(previous_structure, strict=False)
            structure_changed = False

        changed = problem_selection_changed or solution_selection_changed or structure_changed
        if changed:
            merged = {**current, "libro_id": book_id, "config_snapshot": next_snapshot}
            self.controller.actualizar_instancia(
                db_name,
                instance_id,
                _instance_update_input(asdict(_instance_input(merged, book_id=book_id))),
            )
            self._factory_runtime_by_instance.pop((db_name, int(book_id), int(instance_id)), None)
        book = self.controller.obtener_libro(db_name, book_id) or {"id": book_id}
        updated = self._instance_by_id(db_name, book_id, instance_id) or {
            **current,
            "config_snapshot": next_snapshot,
        }
        updated = self._lightweight_instance(db_name, dict(book), dict(updated))
        return {
            "schema_version": "library_instance_page_selection_updated_v1",
            "db_name": db_name,
            "book_id": book_id,
            "instance_id": instance_id,
            "changed": changed,
            "selected_pages": ordered_pages,
            "selected_pages_count": len(ordered_pages),
            "page_ranges": page_ranges,
            "solution_selected_pages": solution_selected_pages,
            "solution_selected_pages_count": len(solution_selected_pages),
            "solution_page_ranges": solution_page_ranges,
            "solution_page_selection": solution_selection_payload,
            "problem_solution_structure": problem_solution_structure,
            "instance": updated,
            "book": self._book_summary(db_name, dict(book)),
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
        runtime_key = (db_name, int(book_id), int(instance_id))
        runtime = self._factory_runtime_by_instance.get(runtime_key)
        if runtime is None:
            runtime = self.runtime_factory(context)
            setattr(runtime, "_library_db_name", db_name)
            setattr(runtime, "_library_book_id", int(book_id))
            setattr(runtime, "_library_instance_id", int(instance_id))
            self._factory_runtime_by_instance[runtime_key] = runtime
            if runtime not in self._factory_runtimes:
                self._factory_runtimes.append(runtime)
        embedded = bool(payload.get("embedded") or payload.get("stable") or payload.get("use_library_server"))
        url = "" if embedded else runtime.start()
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

    def _problem_similarity(self, query: dict[str, list[str]], problem_id: int) -> dict[str, Any]:
        db_name = _required_db(query=query)
        db_profile = _first(query, "db_profile") or "local_mirror"
        model_id = _first(query, "model_id") or "semantic_similarity_seed_v1"
        top_k = _query_int(query, "top_k", default=10, minimum=1, maximum=100)
        include_reverse = _query_bool(query, "include_reverse", default=False)
        fetcher = self.semantic_similarity_fetcher or _default_semantic_similarity_fetcher
        return fetcher(
            db_name=db_name,
            db_profile=db_profile,
            problem_id=int(problem_id),
            top_k=top_k,
            model_id=model_id,
            include_reverse=include_reverse,
        )

    def _problem_concepts(self, query: dict[str, list[str]], problem_id: int) -> dict[str, Any]:
        db_name = _required_db(query=query)
        db_profile = _first(query, "db_profile") or "local_mirror"
        limit = _query_int(query, "limit", default=50, minimum=1, maximum=200)
        role = _first(query, "role") or ""
        status = _first(query, "status") or _first(query, "estado") or ""
        fetcher = self.semantic_problem_concept_fetcher or _default_semantic_problem_concept_fetcher
        return fetcher(
            db_name=db_name,
            db_profile=db_profile,
            problem_id=int(problem_id),
            limit=limit,
            role=role,
            status=status,
        )

    def _problem_practice_draft(self, query: dict[str, list[str]], problem_id: int) -> dict[str, Any]:
        db_name = _required_db(query=query)
        db_profile = _first(query, "db_profile") or "local_mirror"
        model_id = _first(query, "model_id") or "semantic_similarity_seed_v1"
        top_k = _query_int(query, "top_k", default=20, minimum=1, maximum=100)
        target_count = _query_int(query, "target_count", default=10, minimum=1, maximum=50)
        include_reverse = _query_bool(query, "include_reverse", default=True)
        include_rejected = _query_bool(query, "include_rejected", default=False)
        fetcher = self.semantic_practice_fetcher or _default_semantic_practice_fetcher
        return fetcher(
            db_name=db_name,
            db_profile=db_profile,
            problem_id=int(problem_id),
            top_k=top_k,
            target_count=target_count,
            model_id=model_id,
            include_reverse=include_reverse,
            include_rejected=include_rejected,
        )

    def _save_problem_practice_draft(
        self,
        query: dict[str, list[str]],
        payload: dict[str, Any],
        problem_id: int,
    ) -> dict[str, Any]:
        db_name = _required_db(query=query, payload=payload)
        db_profile = _first(query, "db_profile") or str(payload.get("db_profile") or "local_mirror")
        status = str(payload.get("status") or payload.get("estado") or "borrador")
        review_note = str(payload.get("review_note") or payload.get("note") or payload.get("nota") or "")
        draft = payload.get("draft") if isinstance(payload.get("draft"), dict) else payload
        saver = self.semantic_practice_saver or _default_semantic_practice_saver
        return saver(
            db_name=db_name,
            db_profile=db_profile,
            problem_id=int(problem_id),
            draft=draft,
            status=status,
            review_note=review_note,
        )

    def _problem_practice_drafts(self, query: dict[str, list[str]], problem_id: int) -> dict[str, Any]:
        db_name = _required_db(query=query)
        db_profile = _first(query, "db_profile") or "local_mirror"
        limit = _query_int(query, "limit", default=20, minimum=1, maximum=100)
        status = _first(query, "status") or ""
        lister = self.semantic_practice_lister or _default_semantic_practice_lister
        return lister(
            db_name=db_name,
            db_profile=db_profile,
            problem_id=int(problem_id),
            limit=limit,
            status=status,
        )

    def _practice_draft_catalog(self, query: dict[str, list[str]]) -> dict[str, Any]:
        db_name = _required_db(query=query)
        db_profile = _first(query, "db_profile") or "local_mirror"
        limit = _query_int(query, "limit", default=50, minimum=1, maximum=200)
        status = _first(query, "status") or "revisado"
        lister = self.semantic_practice_lister or _default_semantic_practice_lister
        return lister(
            db_name=db_name,
            db_profile=db_profile,
            problem_id=0,
            limit=limit,
            status=status,
        )

    def _semantic_status(self, query: dict[str, list[str]]) -> dict[str, Any]:
        db_name = _required_db(query=query)
        db_profile = _first(query, "db_profile") or "local_mirror"
        model_id = _first(query, "model_id") or "semantic_similarity_seed_v1"
        fetcher = self.semantic_status_fetcher or _default_semantic_status_fetcher
        return fetcher(
            db_name=db_name,
            db_profile=db_profile,
            model_id=model_id,
        )

    def _semantic_concepts(self, query: dict[str, list[str]]) -> dict[str, Any]:
        db_name = _required_db(query=query)
        db_profile = _first(query, "db_profile") or "local_mirror"
        limit = _query_int(query, "limit", default=100, minimum=1, maximum=300)
        search = _first(query, "q") or _first(query, "query") or ""
        course = _first(query, "course") or _first(query, "curso") or ""
        status = _first(query, "status") or _first(query, "estado") or ""
        fetcher = self.semantic_concept_fetcher or _default_semantic_concept_fetcher
        return fetcher(
            db_name=db_name,
            db_profile=db_profile,
            limit=limit,
            query=search,
            course=course,
            status=status,
        )

    def _semantic_concept_problems(self, query: dict[str, list[str]], concept_id: int) -> dict[str, Any]:
        db_name = _required_db(query=query)
        db_profile = _first(query, "db_profile") or "local_mirror"
        limit = _query_int(query, "limit", default=50, minimum=1, maximum=200)
        role = _first(query, "role") or ""
        fetcher = self.semantic_concept_problem_fetcher or _default_semantic_concept_problem_fetcher
        return fetcher(
            db_name=db_name,
            db_profile=db_profile,
            concept_id=int(concept_id),
            limit=limit,
            role=role,
        )

    def _review_semantic_concept_problem_link(
        self,
        query: dict[str, list[str]],
        payload: dict[str, Any],
        concept_id: int,
        problem_id: int,
    ) -> dict[str, Any]:
        db_name = _required_db(query=query, payload=payload)
        db_profile = _first(query, "db_profile") or str(payload.get("db_profile") or "local_mirror")
        role = str(payload.get("role") or _first(query, "role") or "concept").strip() or "concept"
        status = str(payload.get("status") or payload.get("estado") or "").strip()
        review_note = str(payload.get("review_note") or payload.get("note") or payload.get("nota") or "").strip()
        reviewer = self.semantic_concept_link_reviewer or _default_semantic_concept_link_reviewer
        return reviewer(
            db_name=db_name,
            db_profile=db_profile,
            concept_id=int(concept_id),
            problem_id=int(problem_id),
            role=role,
            status=status,
            review_note=review_note,
        )

    def _review_problem_similarity(
        self,
        query: dict[str, list[str]],
        payload: dict[str, Any],
        problem_id: int,
        similar_problem_id: int,
    ) -> dict[str, Any]:
        db_name = _required_db(query=query, payload=payload)
        db_profile = _first(query, "db_profile") or str(payload.get("db_profile") or "local_mirror")
        model_id = _first(query, "model_id") or str(payload.get("model_id") or "semantic_similarity_seed_v1")
        status = str(payload.get("status") or payload.get("estado") or "").strip()
        review_note = str(payload.get("review_note") or payload.get("note") or payload.get("nota") or "").strip()
        reviewer = self.semantic_similarity_reviewer or _default_semantic_similarity_reviewer
        return reviewer(
            db_name=db_name,
            db_profile=db_profile,
            problem_id=int(problem_id),
            similar_problem_id=int(similar_problem_id),
            model_id=model_id,
            status=status,
            review_note=review_note,
        )

    def _book_detail_payload(self, db_name: str, book_id: int) -> dict[str, Any]:
        book = self.controller.obtener_libro(db_name, book_id)
        if not book:
            raise FileNotFoundError("Libro no encontrado.")
        instances = [dict(row) for row in self.controller.listar_instancias_libro(db_name, book_id)]
        dashboard = _serialize(self._dashboard_for_book(db_name, book_id, dict(book), instances))
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

    def _lightweight_instance(self, db_name: str, book: dict[str, Any], instance: dict[str, Any]) -> dict[str, Any]:
        row = dict(instance)
        _apply_instance_catalog_metadata(row)
        row["factory_available"] = bool(str(book.get("pdf_path") or "").strip())
        row["factory_prepare_endpoint"] = f"/api/library/instances/{int(row.get('id') or 0)}/factory"
        row["timeline_stage"] = self._instance_timeline_stage(db_name, book, row, {})
        return row

    def _dashboard_for_book(self, db_name: str, book_id: int, book: dict[str, Any], instances: list[dict[str, Any]]) -> Any:
        try:
            signature = inspect.signature(self.controller.obtener_dashboard_libro)
        except (TypeError, ValueError):
            signature = None
        if signature and {"book", "instance_rows"}.issubset(signature.parameters):
            return self.controller.obtener_dashboard_libro(db_name, book_id, book=book, instance_rows=instances)
        return self.controller.obtener_dashboard_libro(db_name, book_id)

    def _book_summary(self, db_name: str, book: dict[str, Any], *, dashboard: dict[str, Any] | None = None) -> dict[str, Any]:
        row = dict(book)
        health = _parse_instances_health(row)
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
            health_in_db = (
                sum(1 for item in health if int(item.get("total") or item.get("subidos_bd") or 0) > 0)
                if health
                else int(row.get("instances_in_db_total") or row.get("instances_en_bd_total") or 0)
            )
            health_errors = (
                sum(
                    1
                    for item in health
                    if int(item.get("inconsistentes") or 0) > 0
                    or str(item.get("status") or "") == "complete_with_inconsistencies"
                )
                if health
                else int(row.get("instances_with_errors_total") or row.get("instances_error_total") or 0)
            )
            row["indicators"] = {
                "total_instancias": int(row.get("instances_total") or 0),
                "total_esperado": int(row.get("instances_expected_total") or 0),
                "instancias_en_bd": health_in_db,
                "consistentes_total": int(row.get("consistency_consistentes_total") or 0),
                "inconsistentes_total": int(row.get("consistency_inconsistentes_total") or 0),
                "sin_revisar_total": int(row.get("consistency_sin_revisar_total") or 0),
                "errores_total": health_errors,
            }
        for heavy_key in ("instances_health", "instances_health_json", "instances_names"):
            row.pop(heavy_key, None)
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
        if int(counts.get("subidos_bd") or 0) > 0:
            return _timeline_stage_from_counts(counts)
        for key, value in self._cached_local_timeline_counts(db_name, book, instance).items():
            if isinstance(value, int):
                counts[key] = max(int(counts.get(key) or 0), int(value))
            elif value:
                counts[key] = value
        return _timeline_stage_from_counts(counts)

    def _cached_local_timeline_counts(self, db_name: str, book: dict[str, Any], instance: dict[str, Any]) -> dict[str, Any]:
        key = self._local_timeline_cache_key(db_name, book, instance)
        cached = self._local_timeline_cache.get(key)
        if cached is not None:
            created_at, payload = cached
            if time.monotonic() - created_at <= self._local_timeline_cache_ttl_s:
                return copy.deepcopy(payload)
            self._local_timeline_cache.pop(key, None)
        counts = self._local_timeline_counts(db_name, book, instance, golden=self._timeline_golden())
        self._local_timeline_cache[key] = (time.monotonic(), copy.deepcopy(counts))
        return counts

    def _timeline_golden(self) -> Any:
        if self._timeline_golden_controller is None:
            from modulos.modulo13_laboratorio_pdf_segmentacion.controlador_laboratorio_pdf import PdfProblemGoldenController

            self._timeline_golden_controller = PdfProblemGoldenController()
        return self._timeline_golden_controller

    @staticmethod
    def _local_timeline_cache_key(db_name: str, book: dict[str, Any], instance: dict[str, Any]) -> tuple[str, str, str]:
        book_key = "|".join(
            [
                str(book.get("id") or ""),
                str(book.get("codigo") or book.get("code") or ""),
                str(book.get("pdf_path") or book.get("pdfPath") or ""),
                str(book.get("workspace_dir") or book.get("workspaceDir") or ""),
            ]
        )
        instance_key = "|".join(
            [
                str(instance.get("id") or ""),
                str(instance.get("tipo") or ""),
                str(instance.get("session_path") or ""),
                str(instance.get("soluciones_dir") or ""),
            ]
        )
        return (str(db_name or ""), book_key, instance_key)

    @staticmethod
    def _local_timeline_counts(
        db_name: str,
        book: dict[str, Any],
        instance: dict[str, Any],
        *,
        golden: Any | None = None,
    ) -> dict[str, Any]:
        counts: dict[str, Any] = {}
        try:
            from .staging import InstanceStagingStore

            context = InstancePipelineContext.from_library_instance(book, instance, db_name=db_name)
            staging_summary = InstanceStagingStore.load_manifest_summary_from_root(context.staging_root())
            if staging_summary is None and (context.staging_root() / "records").exists():
                store = InstanceStagingStore(context)
                records = store.load_records()
                staging_summary = store.summarize_records(records)
            if staging_summary is not None:
                counts.update(staging_summary)
                if _staging_summary_has_work(staging_summary):
                    return counts

            if golden is None:
                from modulos.modulo13_laboratorio_pdf_segmentacion.controlador_laboratorio_pdf import PdfProblemGoldenController

                golden = PdfProblemGoldenController()
            page_summary = golden.load_instance_summary(context.instance_name)
            if page_summary is not None:
                counts["pages_total"] = int(page_summary.get("pages_total") or 0)
                counts["pages_reviewed"] = int(page_summary.get("reviewed_pages") or 0)
                counts["boxes_total"] = int(page_summary.get("boxes_total") or 0)
            else:
                pages = golden.load_instance(context.instance_name)
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
            if staging_summary is not None:
                counts.update(staging_summary)
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


def _query_bool(query: dict[str, list[str]], key: str, *, default: bool = False) -> bool:
    values = query.get(key) or []
    if not values:
        return default
    value = str(values[-1] or "").strip().lower()
    if value in {"1", "true", "si", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _truthy(value: Any, *, default: bool = False) -> bool:
    if value is None or value == "":
        return bool(default)
    if isinstance(value, bool):
        return value
    raw = str(value).strip().lower()
    if raw in {"1", "true", "si", "sí", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _decode_cover_payload(payload: dict[str, Any]) -> tuple[bytes, str]:
    data_url = str(payload.get("data_url") or payload.get("dataUrl") or "").strip()
    mime = str(payload.get("mime") or "").strip().lower()
    raw_b64 = str(payload.get("base64") or "").strip()
    if data_url:
        if not data_url.startswith("data:") or "," not in data_url:
            raise ValueError("La imagen de portada no tiene formato data URL valido.")
        header, raw_b64 = data_url.split(",", 1)
        if ";base64" not in header.lower():
            raise ValueError("La imagen de portada debe venir codificada en base64.")
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
        raise ValueError("No se pudo leer la imagen de portada.") from exc
    max_bytes = 12 * 1024 * 1024
    if not raw:
        raise ValueError("La imagen de portada esta vacia.")
    if len(raw) > max_bytes:
        raise ValueError("La portada supera 12 MB.")
    return raw, suffix


def _query_int(
    query: dict[str, list[str]],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    values = query.get(key) or []
    raw = str(values[-1] if values else default).strip()
    try:
        value = int(raw)
    except Exception:
        value = int(default)
    return max(int(minimum), min(int(maximum), value))


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
        titulo_practica=str(payload.get("titulo_practica") or payload.get("practice_title") or "").strip(),
        total_esperado=max(int(payload.get("total_esperado") or payload.get("expected_total") or 0), 0),
        pdf_path=str(payload.get("pdf_path") or payload.get("pdf") or "").strip(),
        session_path=str(payload.get("session_path") or "").strip(),
        soluciones_dir=str(payload.get("soluciones_dir") or payload.get("solutions_dir") or "").strip(),
        nombre_instancia=str(payload.get("nombre_instancia") or payload.get("name") or "").strip(),
        estado=str(payload.get("estado") or payload.get("state") or "pendiente").strip(),
        config_snapshot=_dict_payload(payload.get("config_snapshot")),
        session_schema_version=str(payload.get("session_schema_version") or "").strip(),
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


def _dict_payload(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    return None


def _configured_page_selection(
    config_snapshot: dict[str, Any],
    *,
    selection_key: str = "page_selection",
    selected_pages_key: str = "selected_pages",
    page_ranges_key: str = "page_ranges",
) -> list[int]:
    page_selection = _dict_payload(config_snapshot.get(selection_key)) or {}
    raw_pages = page_selection.get("selected_pages")
    if not isinstance(raw_pages, list):
        raw_pages = config_snapshot.get(selected_pages_key)
    pages: set[int] = set()
    if isinstance(raw_pages, list):
        for raw_page in raw_pages:
            try:
                page = int(raw_page)
            except (TypeError, ValueError):
                continue
            if page > 0:
                pages.add(page)
    if pages:
        return sorted(pages)
    raw_ranges = page_selection.get("page_ranges")
    if not isinstance(raw_ranges, list):
        raw_ranges = config_snapshot.get(page_ranges_key)
    if not isinstance(raw_ranges, list):
        return []
    for item in raw_ranges:
        if not isinstance(item, dict):
            continue
        try:
            start = int(item.get("start_page") or item.get("start") or 0)
            end = int(item.get("end_page") or item.get("end") or start)
        except (TypeError, ValueError):
            continue
        if start > 0 and end >= start:
            pages.update(range(start, end + 1))
    return sorted(pages)


def _normalize_selected_pages(raw_pages: Any, *, page_count: int, field_name: str) -> list[int]:
    if not isinstance(raw_pages, list):
        raise ValueError(f"{field_name} debe ser una lista.")
    selected_pages: set[int] = set()
    for raw_page in raw_pages:
        try:
            page = int(raw_page)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} solo admite enteros.") from exc
        if page <= 0 or (page_count and page > page_count):
            limit = f" entre 1 y {page_count}" if page_count else " mayor que cero"
            raise ValueError(f"Pagina fuera de rango en {field_name}: {page}; debe ser{limit}.")
        selected_pages.add(page)
    return sorted(selected_pages)


def _normalize_problem_solution_structure(
    raw: dict[str, Any],
    *,
    source: str = "",
    updated_at: str = "",
    strict: bool,
) -> dict[str, Any]:
    structure_mode = str(raw.get("structure_mode") or "unknown").strip().lower().replace("-", "_")
    solution_status = str(raw.get("solution_status") or "pending_review").strip().lower().replace("-", "_")
    if strict and structure_mode not in PROBLEM_SOLUTION_STRUCTURE_MODES:
        allowed = ", ".join(sorted(PROBLEM_SOLUTION_STRUCTURE_MODES))
        raise ValueError(f"structure_mode invalido. Usa uno de: {allowed}.")
    if strict and solution_status not in PROBLEM_SOLUTION_STATUSES:
        allowed = ", ".join(sorted(PROBLEM_SOLUTION_STATUSES))
        raise ValueError(f"solution_status invalido. Usa uno de: {allowed}.")
    if structure_mode not in PROBLEM_SOLUTION_STRUCTURE_MODES:
        structure_mode = "unknown"
    if solution_status not in PROBLEM_SOLUTION_STATUSES:
        solution_status = "pending_review"
    normalized = {
        **dict(raw or {}),
        "schema_version": PROBLEM_SOLUTION_STRUCTURE_SCHEMA_VERSION,
        "structure_mode": structure_mode,
        "solution_status": solution_status,
        "exercise_set_id": str(raw.get("exercise_set_id") or "").strip(),
    }
    if source:
        normalized["source"] = source
        normalized["review_status"] = "pending"
    else:
        normalized.setdefault("source", "")
        normalized.setdefault("review_status", "")
    if updated_at:
        normalized["updated_at"] = updated_at
    return normalized


def _page_ranges_from_pages(pages: list[int]) -> list[dict[str, int]]:
    if not pages:
        return []
    ranges: list[dict[str, int]] = []
    start = previous = int(pages[0])
    for raw_page in pages[1:]:
        page = int(raw_page)
        if page == previous + 1:
            previous = page
            continue
        ranges.append({"start_page": start, "end_page": previous})
        start = previous = page
    ranges.append({"start_page": start, "end_page": previous})
    return ranges


def _page_range_display(page_ranges: list[dict[str, int]]) -> str:
    parts = []
    for item in page_ranges:
        start = int(item.get("start_page") or 0)
        end = int(item.get("end_page") or start)
        parts.append(str(start) if start == end else f"{start}-{end}")
    return ",".join(parts)


def _apply_instance_catalog_metadata(row: dict[str, Any]) -> None:
    snapshot = row.get("config_snapshot")
    if not isinstance(snapshot, dict):
        snapshot = {}
    page_range = str(
        snapshot.get("page_range_display")
        or snapshot.get("page_range")
        or snapshot.get("range")
        or ""
    ).strip()
    if page_range:
        row.setdefault("pages", page_range)
        row.setdefault("page_range", page_range)
        row.setdefault("range", page_range)
    practice_title = str(
        row.get("practice_title")
        or row.get("titulo_practica")
        or row.get("nombre_instancia")
        or snapshot.get("instance_name")
        or snapshot.get("label_display")
        or ""
    ).strip()
    if practice_title:
        row.setdefault("practice_title", practice_title)
        row.setdefault("titulo_practica", practice_title)
    status = str(row.get("status") or row.get("estado") or snapshot.get("status") or "").strip()
    if status:
        row["status"] = status


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
        "problems_total": int(raw.get("problems_total") or raw.get("primary_records_total") or raw.get("records_total") or raw.get("records") or 0),
        "primary_records_total": int(raw.get("primary_records_total") or raw.get("problems_total") or raw.get("records_total") or raw.get("records") or 0),
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
    problems_total = int(counts.get("problems_total") or counts.get("primary_records_total") or records_total or 0)
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
        detail = f"{normalized_done}/{problems_total} borrador(es); {ready} listo(s)."
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


def _staging_summary_has_work(summary: dict[str, Any]) -> bool:
    for key in (
        "records_total",
        "crops_found",
        "ocr_done",
        "segments_done",
        "normalized_done",
        "ready",
    ):
        try:
            if int(summary.get(key) or 0) > 0:
                return True
        except Exception:
            continue
    return False


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


def _default_semantic_similarity_fetcher(
    *,
    db_name: str,
    db_profile: str,
    problem_id: int,
    top_k: int,
    model_id: str,
    include_reverse: bool,
) -> dict[str, Any]:
    from database.connection import DatabaseManager
    from modulos.semantic_similarity_review import fetch_problem_similarity_review

    db = DatabaseManager.from_profile(db_profile or "local_mirror", db_name=db_name)
    conn = db.get_connection(db.db_name)
    try:
        return fetch_problem_similarity_review(
            conn,
            problem_id=int(problem_id),
            top_k=int(top_k),
            model_id=str(model_id or "semantic_similarity_seed_v1"),
            include_reverse=bool(include_reverse),
        )
    finally:
        conn.close()


def _default_semantic_status_fetcher(
    *,
    db_name: str,
    db_profile: str,
    model_id: str,
) -> dict[str, Any]:
    from database.connection import DatabaseManager
    from modulos.semantic_profile_db import fetch_semantic_coverage_status

    db = DatabaseManager.from_profile(db_profile or "local_mirror", db_name=db_name)
    conn = db.get_connection(db.db_name)
    try:
        return fetch_semantic_coverage_status(
            conn,
            model_id=str(model_id or "semantic_similarity_seed_v1"),
        )
    finally:
        conn.close()


def _default_semantic_concept_fetcher(
    *,
    db_name: str,
    db_profile: str,
    limit: int,
    query: str = "",
    course: str = "",
    status: str = "",
) -> dict[str, Any]:
    from database.connection import DatabaseManager
    from modulos.semantic_profile_db import fetch_semantic_concept_catalog

    db = DatabaseManager.from_profile(db_profile or "local_mirror", db_name=db_name)
    conn = db.get_connection(db.db_name)
    try:
        return fetch_semantic_concept_catalog(
            conn,
            limit=int(limit or 100),
            query=str(query or ""),
            course=str(course or ""),
            status=str(status or ""),
        )
    finally:
        conn.close()


def _default_semantic_concept_problem_fetcher(
    *,
    db_name: str,
    db_profile: str,
    concept_id: int,
    limit: int,
    role: str = "",
) -> dict[str, Any]:
    from database.connection import DatabaseManager
    from modulos.semantic_profile_db import fetch_semantic_concept_linked_problems

    db = DatabaseManager.from_profile(db_profile or "local_mirror", db_name=db_name)
    conn = db.get_connection(db.db_name)
    try:
        return fetch_semantic_concept_linked_problems(
            conn,
            concept_id=int(concept_id),
            limit=int(limit or 50),
            role=str(role or ""),
        )
    finally:
        conn.close()


def _default_semantic_concept_link_reviewer(
    *,
    db_name: str,
    db_profile: str,
    concept_id: int,
    problem_id: int,
    role: str,
    status: str,
    review_note: str = "",
) -> dict[str, Any]:
    from database.connection import DatabaseManager
    from modulos.semantic_profile_db import update_problem_concept_link_review

    db = DatabaseManager.from_profile(db_profile or "local_mirror", db_name=db_name)
    conn = db.get_connection(db.db_name)
    try:
        payload = update_problem_concept_link_review(
            conn,
            concept_id=int(concept_id),
            problem_id=int(problem_id),
            role=str(role or "concept"),
            status=str(status or ""),
            review_note=str(review_note or ""),
        )
        conn.commit()
        return payload
    finally:
        conn.close()


def _default_semantic_problem_concept_fetcher(
    *,
    db_name: str,
    db_profile: str,
    problem_id: int,
    limit: int,
    role: str = "",
    status: str = "",
) -> dict[str, Any]:
    from database.connection import DatabaseManager
    from modulos.semantic_profile_db import fetch_problem_concept_links

    db = DatabaseManager.from_profile(db_profile or "local_mirror", db_name=db_name)
    conn = db.get_connection(db.db_name)
    try:
        return fetch_problem_concept_links(
            conn,
            problem_id=int(problem_id),
            limit=int(limit or 50),
            role=str(role or ""),
            status=str(status or ""),
        )
    finally:
        conn.close()


def _default_semantic_similarity_reviewer(
    *,
    db_name: str,
    db_profile: str,
    problem_id: int,
    similar_problem_id: int,
    model_id: str,
    status: str,
    review_note: str,
) -> dict[str, Any]:
    from database.connection import DatabaseManager
    from modulos.semantic_profile_db import update_problem_similarity_edge_review

    db = DatabaseManager.from_profile(db_profile or "local_mirror", db_name=db_name)
    conn = db.get_connection(db.db_name)
    try:
        payload = update_problem_similarity_edge_review(
            conn,
            problem_id=int(problem_id),
            similar_problem_id=int(similar_problem_id),
            model_id=str(model_id or "semantic_similarity_seed_v1"),
            status=str(status or ""),
            review_note=str(review_note or ""),
        )
        conn.commit()
        return payload
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def _default_semantic_practice_fetcher(
    *,
    db_name: str,
    db_profile: str,
    problem_id: int,
    top_k: int,
    target_count: int,
    model_id: str,
    include_reverse: bool,
    include_rejected: bool,
) -> dict[str, Any]:
    from database.connection import DatabaseManager
    from modulos.semantic_practice_recommendation import fetch_semantic_practice_draft

    db = DatabaseManager.from_profile(db_profile or "local_mirror", db_name=db_name)
    conn = db.get_connection(db.db_name)
    try:
        return fetch_semantic_practice_draft(
            conn,
            problem_id=int(problem_id),
            top_k=int(top_k),
            target_count=int(target_count),
            model_id=str(model_id or "semantic_similarity_seed_v1"),
            include_reverse=bool(include_reverse),
            include_rejected=bool(include_rejected),
        )
    finally:
        conn.close()


def _default_semantic_practice_saver(
    *,
    db_name: str,
    db_profile: str,
    problem_id: int,
    draft: dict[str, Any],
    status: str,
    review_note: str,
) -> dict[str, Any]:
    from database.connection import DatabaseManager
    from modulos.semantic_profile_db import save_semantic_practice_draft

    db = DatabaseManager.from_profile(db_profile or "local_mirror", db_name=db_name)
    conn = db.get_connection(db.db_name)
    try:
        payload = save_semantic_practice_draft(
            conn,
            draft,
            problem_id=int(problem_id),
            status=str(status or "borrador"),
            review_note=str(review_note or ""),
        )
        conn.commit()
        return payload
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def _default_semantic_practice_lister(
    *,
    db_name: str,
    db_profile: str,
    problem_id: int,
    limit: int,
    status: str = "",
) -> dict[str, Any]:
    from database.connection import DatabaseManager
    from modulos.semantic_profile_db import fetch_semantic_practice_draft_catalog, fetch_semantic_practice_drafts

    db = DatabaseManager.from_profile(db_profile or "local_mirror", db_name=db_name)
    conn = db.get_connection(db.db_name)
    try:
        if int(problem_id or 0) <= 0:
            return fetch_semantic_practice_draft_catalog(
                conn,
                limit=int(limit or 50),
                status=str(status or "revisado"),
            )
        return fetch_semantic_practice_drafts(
            conn,
            problem_id=int(problem_id),
            limit=int(limit or 20),
            status=str(status or ""),
        )
    finally:
        conn.close()
