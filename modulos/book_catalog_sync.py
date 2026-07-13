from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from modulos.book_visual_catalog import infer_course_folder
from modulos.instance_factory.library_covers import copy_cover_to_library_store
from modulos.modulo9_organizador_libros.controlador_organizador_libros import (
    BookCreateInput,
    BookInstanceInput,
    BookInstanceUpdateInput,
    BookProgressController,
    BookUpdateInput,
)
from utils.project_layout import remap_legacy_drive_path


SYNC_SCHEMA_VERSION = "book_catalog_sync_v1"
MANAGED_NOTES_BLOCK_RE = re.compile(
    r"\[book_catalog_sync\]\s*(\{.*?\})\s*\[/book_catalog_sync\]",
    re.DOTALL,
)
BOOK_ID_HASH_SUFFIX_RE = re.compile(r"-(?:[0-9a-f]{8,64})$", re.IGNORECASE)
INSTANCE_LABELS = (
    "teoria",
    "ejemplos",
    "problemas_propuestos",
    "problemas_resueltos",
    "solucionario",
)
EXCLUDED_COURSES = {"Examenes y Concursos"}
EXCLUDED_MATERIAL_TYPES = {"consulta", "teoria", "reference"}
VOLATILE_MANAGED_NOTE_FIELDS = {"last_catalog_sync_at"}


@dataclass(slots=True)
class SyncOptions:
    output_root: Path
    sync_root: Path
    db_name: str = ""
    apply: bool = False
    allow_exams: bool = False
    book_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class CatalogRecord:
    book_id: str
    pdf_path: str
    pdf_relpath: str
    pdf_hash_sha256: str
    page_count: int
    material_type: str
    title: str
    author: str
    editorial: str
    collection: str
    bibliographic_status: str
    source_root: str
    raw: dict[str, Any]


@dataclass(slots=True)
class CatalogInstance:
    code: str
    label: str
    title: str
    page_start: int
    page_end: int
    pages_total: int
    page_range_display: str
    config_snapshot: dict[str, Any]


@dataclass(slots=True)
class CatalogBook:
    record: CatalogRecord
    title: str
    author: str
    editorial: str
    course: str
    workspace_dir_hint: str
    source_note_path: str
    obsidian_note_path: str
    cover_source_path: str
    pdf_exists: bool
    excluded_reason: str
    review_pending_reason: str
    instances: list[CatalogInstance]


@dataclass(slots=True)
class SyncConflict:
    scope: str
    key: str
    reason: str
    severity: str
    details: dict[str, Any]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_catalog_sync(options: SyncOptions) -> dict[str, Any]:
    output_root = options.output_root.resolve()
    sync_root = options.sync_root.resolve()
    sync_root.mkdir(parents=True, exist_ok=True)

    controller = BookProgressController()
    backend = _load_backend_snapshot(controller, options.db_name)
    catalog_books = _load_catalog_books(output_root, allow_exams=options.allow_exams, selected_book_ids=options.book_ids)

    report = {
        "schema_version": SYNC_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "output_root": str(output_root),
        "sync_root": str(sync_root),
        "db_name": backend["db_name"],
        "apply_requested": bool(options.apply),
        "apply_executed": False,
        "backend": {
            "status": backend["status"],
            "db_name": backend["db_name"],
            "requested_db_name": backend["requested_db_name"],
            "available_databases": list(backend["available_databases"]),
            "error": backend["error"],
            "books_indexed": len(backend["books_by_id"]),
            "instances_indexed": sum(len(items) for items in backend["instances_by_book_id"].values()),
        },
        "rules": {
            "allow_exams": bool(options.allow_exams),
            "instance_labels": list(INSTANCE_LABELS),
            "instance_semantics": "one_instance_per_declared_theme_or_section",
            "excluded_courses": sorted(EXCLUDED_COURSES),
            "notes_traceability": "book_catalog_sync managed block",
        },
    }

    planned_books: list[dict[str, Any]] = []
    imported_rows: list[dict[str, Any]] = []
    conflicts: list[SyncConflict] = []
    summary = {
        "books_detected": 0,
        "books_excluded": 0,
        "books_missing_pdf": 0,
        "books_without_actionable_ranges": 0,
        "books_without_actionable_topics": 0,
        "books_new": 0,
        "books_existing": 0,
        "books_conflict": 0,
        "instances_to_create": 0,
        "instances_to_update": 0,
        "instances_unchanged": 0,
        "instances_conflict": 0,
        "pdfs_missing": 0,
    }

    for book in catalog_books:
        summary["books_detected"] += 1
        plan = _plan_book_sync(book, backend)
        planned_books.append(plan)
        imported_rows.append(_import_row_from_plan(plan, applied=False))

        if plan["status"] == "excluded":
            summary["books_excluded"] += 1
            conflicts.append(
                SyncConflict(
                    scope="book",
                    key=book.record.book_id,
                    reason=plan["skip_reason"],
                    severity="info",
                    details={"course": book.course, "pdf_path": book.record.pdf_path},
                )
            )
            continue
        if plan["status"] == "missing_pdf":
            summary["books_missing_pdf"] += 1
            summary["pdfs_missing"] += 1
            conflicts.append(
                SyncConflict(
                    scope="book",
                    key=book.record.book_id,
                    reason=plan["skip_reason"],
                    severity="error",
                    details={"pdf_path": book.record.pdf_path},
                )
            )
            continue
        if plan["status"] == "review_pending":
            summary["books_without_actionable_ranges"] += 1
            summary["books_without_actionable_topics"] += 1
            conflicts.append(
                SyncConflict(
                    scope="book",
                    key=book.record.book_id,
                    reason=plan["skip_reason"],
                    severity="warning",
                    details={"pdf_path": book.record.pdf_path, "themes_total": len(book.instances)},
                )
            )
            continue

        if plan["book_action"] == "create":
            summary["books_new"] += 1
        elif plan["book_action"] in {"update", "unchanged"}:
            summary["books_existing"] += 1
        else:
            summary["books_conflict"] += 1
            conflicts.append(
                SyncConflict(
                    scope="book",
                    key=book.record.book_id,
                    reason=plan["book_conflict"]["reason"],
                    severity="error",
                    details=plan["book_conflict"],
                )
            )

        for instance_plan in plan["instances"]:
            action = str(instance_plan["action"])
            if action == "create":
                summary["instances_to_create"] += 1
            elif action == "update":
                summary["instances_to_update"] += 1
            elif action == "unchanged":
                summary["instances_unchanged"] += 1
            else:
                summary["instances_conflict"] += 1
                conflicts.append(
                    SyncConflict(
                        scope="instance",
                        key=f"{book.record.book_id}:{instance_plan['code']}",
                        reason=instance_plan["conflict"]["reason"],
                        severity="error",
                        details=instance_plan["conflict"],
                    )
                )

    report["summary"] = summary
    report["books"] = planned_books

    applied_rows: list[dict[str, Any]] = []
    if options.apply:
        apply_result = _apply_sync(plan=report, controller=controller, sync_root=sync_root)
        report["apply_executed"] = True
        report["apply_result"] = apply_result
        applied_rows = list(apply_result["imported_rows"])

    _write_sync_outputs(
        sync_root=sync_root,
        report=report,
        conflicts=conflicts,
        imported_rows=applied_rows or imported_rows,
    )
    return report


def _load_backend_snapshot(controller: BookProgressController, requested_db_name: str) -> dict[str, Any]:
    configured = str(requested_db_name or "").strip()
    available = [str(name) for name in controller.listar_bases_datos()]
    selected = configured or (available[0] if available else "")
    status = "ready" if selected and selected in available else "unavailable"
    if configured and available and configured not in available:
        status = "unavailable"

    books_by_id: dict[int, dict[str, Any]] = {}
    instances_by_book_id: dict[int, list[dict[str, Any]]] = {}
    book_index = {
        "by_code": {},
        "by_source_book_id": {},
        "by_pdf_hash": {},
        "by_pdf_path": {},
        "by_workspace_dir": {},
    }
    instance_index = {}
    error = ""

    if status == "ready":
        try:
            rows = [dict(row) for row in controller.listar_libros(selected, include_instance_health=False)]
            for row in rows:
                book_id = int(row.get("id") or 0)
                books_by_id[book_id] = row
                code = str(row.get("codigo") or "").strip()
                if code:
                    book_index["by_code"][code] = row
                metadata = _extract_managed_metadata(str(row.get("notas") or ""))
                source_book_id = str(metadata.get("source_book_id") or metadata.get("book_id") or "").strip()
                pdf_hash = str(metadata.get("pdf_hash_sha256") or "").strip()
                if source_book_id:
                    book_index["by_source_book_id"][source_book_id] = row
                if pdf_hash:
                    book_index["by_pdf_hash"][pdf_hash] = row
                normalized_pdf = _normalize_existing_path(str(row.get("pdf_path") or ""))
                if normalized_pdf:
                    book_index["by_pdf_path"][normalized_pdf] = row
                workspace_candidates = {
                    _normalize_path_key(str(row.get("workspace_dir") or "")),
                    _normalize_path_key(str(row.get("workspace_dir_server") or "")),
                    _normalize_path_key(str(row.get("workspace_dir_local") or "")),
                    _normalize_path_key(str(row.get("workspace_dir_mirror") or "")),
                }
                for workspace_key in workspace_candidates:
                    if workspace_key:
                        book_index["by_workspace_dir"].setdefault(workspace_key, []).append(row)
            instances_by_book_id = {
                int(book_id): [dict(item) for item in items]
                for book_id, items in controller.listar_instancias_todos(selected).items()
            }
            for book_id, items in instances_by_book_id.items():
                for item in items:
                    metadata = _extract_managed_metadata(str(item.get("notas") or ""))
                    source_instance_key = str(metadata.get("source_instance_key") or "").strip()
                    code = str(item.get("tipo") or item.get("codigo_instancia") or "").strip()
                    if source_instance_key:
                        instance_index[(book_id, source_instance_key)] = item
                    if code:
                        instance_index[(book_id, code)] = item
        except Exception as exc:  # pragma: no cover - depends on local runtime state.
            status = "unavailable"
            error = str(exc)

    return {
        "status": status,
        "db_name": selected or configured,
        "requested_db_name": configured,
        "available_databases": available,
        "error": error,
        "books_by_id": books_by_id,
        "instances_by_book_id": instances_by_book_id,
        "book_index": book_index,
        "instance_index": instance_index,
    }


def _load_catalog_books(output_root: Path, *, allow_exams: bool, selected_book_ids: tuple[str, ...]) -> list[CatalogBook]:
    records = _load_inventory_records(output_root)
    selected = set(selected_book_ids or ())
    course_notes = _course_note_map(output_root)
    books: list[CatalogBook] = []
    for book_id, record in sorted(records.items()):
        if selected and book_id not in selected:
            continue
        book_dir = output_root / "books" / book_id
        book_json = _read_json(book_dir / "book.json")
        ranges_json = _read_json(book_dir / "ranges.json")
        note_path = course_notes.get(book_id) or str((book_dir / "obsidian.md").resolve())
        obsidian_note_path = str((book_dir / "obsidian.md").resolve())
        course = _resolve_course(record=record, note_path=note_path)
        pdf_exists = _resolve_existing_file(record.pdf_path) is not None
        excluded_reason = ""
        if record.material_type in EXCLUDED_MATERIAL_TYPES:
            excluded_reason = "Material excluido de la Biblioteca operativa por ser libro de consulta/teoria sin problemas de opcion multiple."
        elif not allow_exams and (course in EXCLUDED_COURSES or record.material_type == "examen_concurso"):
            excluded_reason = "Curso/material excluido de la Biblioteca operativa por ser examen."
        instances = _derive_theme_instances(
            record=record,
            book_dir=book_dir,
            book_json=book_json,
            ranges_json=ranges_json,
            source_note_path=note_path,
            obsidian_note_path=obsidian_note_path,
        )
        review_pending_reason = ""
        if not excluded_reason and not instances:
            review_pending_reason = "No hay temas estructurados para crear instancias. El catalogo actual solo trae rangos visuales/etiquetas y eso ya no se sincroniza como instancia."
        books.append(
            CatalogBook(
                record=record,
                title=_coalesce_text(book_json.get("bibliographic", {}).get("title"), record.title, record.book_id),
                author=_coalesce_text(book_json.get("bibliographic", {}).get("author"), record.author),
                editorial=_coalesce_text(book_json.get("bibliographic", {}).get("editorial"), record.editorial),
                course=course,
                workspace_dir_hint=_catalog_workspace_dir_hint(record),
                source_note_path=note_path,
                obsidian_note_path=obsidian_note_path,
                cover_source_path=_resolve_cover_source(book_dir, ranges_json),
                pdf_exists=pdf_exists,
                excluded_reason=excluded_reason,
                review_pending_reason=review_pending_reason,
                instances=instances,
            )
        )
    return books


def _load_inventory_records(output_root: Path) -> dict[str, CatalogRecord]:
    inventory_path = output_root / "inventory.jsonl"
    records: dict[str, CatalogRecord] = {}
    for line in inventory_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        raw = json.loads(text)
        book_id = str(raw.get("book_id") or "").strip()
        if not book_id:
            continue
        records[book_id] = CatalogRecord(
            book_id=book_id,
            pdf_path=str(raw.get("pdf_path") or "").strip(),
            pdf_relpath=str(raw.get("pdf_relpath") or "").strip(),
            pdf_hash_sha256=str(raw.get("pdf_hash_sha256") or "").strip(),
            page_count=int(raw.get("page_count") or 0),
            material_type=str(raw.get("material_type") or "").strip(),
            title=_coalesce_text(raw.get("bibliographic_title"), raw.get("metadata_title"), book_id),
            author=_coalesce_text(raw.get("bibliographic_author"), raw.get("metadata_author")),
            editorial=str(raw.get("bibliographic_editorial") or "").strip(),
            collection=str(raw.get("bibliographic_collection") or "").strip(),
            bibliographic_status=str(raw.get("bibliographic_status") or "").strip(),
            source_root=str(raw.get("source_root") or "").strip(),
            raw=raw,
        )
    return records


def _course_note_map(output_root: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    courses_root = output_root / "Cursos"
    if not courses_root.exists():
        return mapping
    for note in courses_root.rglob("*.md"):
        if note.name in {"00 Listado PDF.md", "README.md"}:
            continue
        mapping[note.stem] = str(note.resolve())
    return mapping


def _resolve_course(*, record: CatalogRecord, note_path: str) -> str:
    note = Path(note_path)
    if note.exists() and note.parent.name:
        return note.parent.name
    return infer_course_folder(pdf_path=record.pdf_path, pdf_relpath=record.pdf_relpath)


def _resolve_cover_source(book_dir: Path, ranges_json: dict[str, Any]) -> str:
    pages_dir = book_dir / "pages"
    preferred_page = 1
    for item in ranges_json.get("ranges") or []:
        if str(item.get("label") or "").strip() == "portada":
            preferred_page = int(item.get("start_page") or 1)
            break
    cover = pages_dir / f"page-{preferred_page:04d}.png"
    if cover.exists():
        return str(cover.resolve())
    fallback = pages_dir / "page-0001.png"
    return str(fallback.resolve()) if fallback.exists() else ""


def _load_theme_definitions(*, book_dir: Path, book_json: dict[str, Any], ranges_json: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    themes_path = book_dir / "themes.json"
    if themes_path.exists():
        candidates.append(_read_json_value(themes_path))
    candidates.append(book_json.get("themes"))
    candidates.append(ranges_json.get("themes"))
    for candidate in candidates:
        if isinstance(candidate, dict):
            raw = candidate.get("themes")
            if isinstance(raw, list):
                return [item for item in raw if isinstance(item, dict)]
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return []


def _load_instance_semantics(book_dir: Path) -> str:
    themes_path = book_dir / "themes.json"
    if not themes_path.exists():
        return "theme"
    data = _read_json_value(themes_path)
    if not isinstance(data, dict):
        return "theme"
    value = _coalesce_text(data.get("instance_semantics"), data.get("semantica_instancia"))
    return value or "theme"


def _catalog_workspace_dir_hint(record: CatalogRecord) -> str:
    pdf_path = str(record.pdf_path or "").strip()
    if not pdf_path:
        return ""
    base_slug = _book_id_base_slug(record.book_id)
    if not base_slug:
        return ""
    parent = Path(pdf_path).expanduser().parent
    return str(parent / base_slug)


def _book_id_base_slug(book_id: str) -> str:
    return BOOK_ID_HASH_SUFFIX_RE.sub("", str(book_id or "").strip())


def _workspace_keys_for_book(book: CatalogBook) -> list[str]:
    candidates = [
        _normalize_path_key(book.workspace_dir_hint),
    ]
    return [item for item in candidates if item]


def _normalize_path_key(raw_path: str) -> str:
    text = str(raw_path or "").strip()
    if not text:
        return ""
    text = text.replace("/", "\\").rstrip("\\").lower()
    return text


def _unique_rows_by_id(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in rows:
        row_id = int(row.get("id") or 0)
        if row_id and row_id in seen:
            continue
        if row_id:
            seen.add(row_id)
        result.append(dict(row))
    return result


def _slugify(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    text = text.strip("-")
    return text[:80]


def _display_page_ranges(ranges: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in ranges:
        start_page = int(item.get("page_start") or 0)
        end_page = int(item.get("page_end") or 0)
        if start_page <= 0 or end_page <= 0:
            continue
        parts.append(f"{start_page}-{end_page}" if start_page != end_page else f"{start_page}")
    return ", ".join(parts)


def _preferred_operational_ranges(segments: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    proposed = [dict(item) for item in segments if str(item.get("label") or "").strip() == "problemas_propuestos"]
    if proposed:
        return "problemas_propuestos", proposed
    return "tema_completo", [dict(item) for item in segments]


def _derive_theme_instances(
    *,
    record: CatalogRecord,
    book_dir: Path,
    book_json: dict[str, Any],
    ranges_json: dict[str, Any],
    source_note_path: str,
    obsidian_note_path: str,
) -> list[CatalogInstance]:
    raw_themes = _load_theme_definitions(book_dir=book_dir, book_json=book_json, ranges_json=ranges_json)
    instance_semantics = _load_instance_semantics(book_dir)
    instances: list[CatalogInstance] = []
    for index, raw_theme in enumerate(raw_themes, start=1):
        theme_name = _coalesce_text(
            raw_theme.get("theme_name"),
            raw_theme.get("tema"),
            raw_theme.get("name"),
            raw_theme.get("title"),
        )
        if not theme_name:
            continue
        raw_segments = raw_theme.get("segments") or raw_theme.get("ranges") or raw_theme.get("bloques") or []
        if not isinstance(raw_segments, list):
            continue
        segments: list[dict[str, Any]] = []
        for raw_segment in raw_segments:
            if not isinstance(raw_segment, dict):
                continue
            label = str(raw_segment.get("label") or raw_segment.get("type") or raw_segment.get("kind") or "").strip()
            start_page = int(raw_segment.get("start_page") or raw_segment.get("page_start") or 0)
            end_page = int(raw_segment.get("end_page") or raw_segment.get("page_end") or 0)
            if label not in INSTANCE_LABELS:
                continue
            if start_page <= 0 or end_page <= 0 or end_page < start_page:
                continue
            if record.page_count and end_page > record.page_count:
                continue
            segments.append(
                {
                    "label": label,
                    "title": label.replace("_", " ").strip(),
                    "page_start": start_page,
                    "page_end": end_page,
                    "pages_total": int(raw_segment.get("pages_total") or (end_page - start_page + 1)),
                    "page_range": f"{start_page}-{end_page}",
                }
            )
        if not segments:
            continue
        start_page = int(raw_theme.get("start_page") or raw_theme.get("page_start") or min(int(item["page_start"]) for item in segments))
        end_page = int(raw_theme.get("end_page") or raw_theme.get("page_end") or max(int(item["page_end"]) for item in segments))
        if start_page <= 0 or end_page <= 0 or end_page < start_page:
            continue
        if record.page_count and end_page > record.page_count:
            continue
        slug = _slugify(theme_name)
        code = f"tema_{index:02d}" if not slug else f"tema_{index:02d}_{slug}"
        theme_page_range_display = f"pp. {start_page}-{end_page}"
        selected_label, selected_ranges = _preferred_operational_ranges(segments)
        selected_start_page = min(int(item["page_start"]) for item in selected_ranges)
        selected_end_page = max(int(item["page_end"]) for item in selected_ranges)
        selected_pages_total = sum(int(item.get("pages_total") or 0) for item in selected_ranges)
        selected_ranges_display = _display_page_ranges(selected_ranges)
        page_range_display = (
            f"pp. {selected_ranges_display}"
            if selected_ranges_display
            else theme_page_range_display
        )
        source_instance_key = f"{record.book_id}:{code}"
        config_snapshot = {
            "schema_version": SYNC_SCHEMA_VERSION,
            "source_book_id": record.book_id,
            "source_instance_key": source_instance_key,
            "instance_semantics": instance_semantics,
            "theme_order": index,
            "theme_name": theme_name,
            "page_start": selected_start_page,
            "page_end": selected_end_page,
            "page_total": selected_pages_total or int(raw_theme.get("pages_total") or (selected_end_page - selected_start_page + 1)),
            "page_range": f"{selected_start_page}-{selected_end_page}",
            "page_range_display": page_range_display,
            "selected_content_label": selected_label,
            "selected_page_ranges": selected_ranges,
            "selected_page_ranges_display": page_range_display,
            "theme_page_start": start_page,
            "theme_page_end": end_page,
            "theme_page_total": int(raw_theme.get("pages_total") or (end_page - start_page + 1)),
            "theme_page_range": f"{start_page}-{end_page}",
            "theme_page_range_display": theme_page_range_display,
            "segments": segments,
            "pdf_hash_sha256": record.pdf_hash_sha256,
            "source_note_path": source_note_path,
            "obsidian_note_path": obsidian_note_path,
            "pdf_source_path": record.pdf_path,
            "status": "pendiente",
        }
        instances.append(
            CatalogInstance(
                code=code,
                label="tema",
                title=theme_name,
                page_start=selected_start_page,
                page_end=selected_end_page,
                pages_total=int(config_snapshot["page_total"]),
                page_range_display=page_range_display,
                config_snapshot=config_snapshot,
            )
        )
    return instances


def _plan_book_sync(book: CatalogBook, backend: dict[str, Any]) -> dict[str, Any]:
    managed = {
        "schema_version": SYNC_SCHEMA_VERSION,
        "source_book_id": book.record.book_id,
        "book_id": book.record.book_id,
        "source_note_path": book.source_note_path,
        "obsidian_note_path": book.obsidian_note_path,
        "pdf_hash_sha256": book.record.pdf_hash_sha256,
        "pdf_source_path": book.record.pdf_path,
        "workspace_dir_hint": book.workspace_dir_hint,
        "catalog_status": book.record.bibliographic_status,
        "last_catalog_sync_at": utc_now_iso(),
    }
    book_notes = _merge_managed_notes("", managed, prefix="Sincronizado desde catalogo visual.")
    payload = {
        "codigo": book.record.book_id,
        "titulo": book.title,
        "autor": book.author,
        "editorial": book.editorial,
        "edicion": "",
        "curso": book.course,
        "workspace_dir": book.workspace_dir_hint,
        "pdf_path": book.record.pdf_path,
        "cover_path": book.cover_source_path,
        "estado": "pendiente",
        "notas": book_notes,
        "activo": True,
    }
    base = {
        "book_id": book.record.book_id,
        "source_note_path": book.source_note_path,
        "obsidian_note_path": book.obsidian_note_path,
        "pdf_path": book.record.pdf_path,
        "pdf_hash_sha256": book.record.pdf_hash_sha256,
        "pdf_exists": book.pdf_exists,
        "course": book.course,
        "title": book.title,
        "instances_total": len(book.instances),
        "status": "planned",
        "skip_reason": "",
        "book_action": "create",
        "book_payload": payload,
        "book_conflict": None,
        "matched_book": None,
        "instances": [],
    }

    if book.excluded_reason:
        base["status"] = "excluded"
        base["skip_reason"] = book.excluded_reason
        return base
    if not book.pdf_exists:
        base["status"] = "missing_pdf"
        base["skip_reason"] = "El PDF catalogado no existe en la ruta esperada."
        return base

    matched_book, book_conflict = _match_existing_book(book=book, backend=backend)
    if book_conflict:
        base["book_action"] = "conflict"
        base["book_conflict"] = book_conflict
        return base
    if not book.instances:
        base["status"] = "review_pending"
        base["skip_reason"] = book.review_pending_reason
        return base

    if matched_book:
        base["matched_book"] = _book_ref(matched_book)
        update_payload = _merged_book_update_payload(matched_book, payload)
        if _same_payload(matched_book, update_payload, ("codigo", "titulo", "autor", "editorial", "curso", "estado", "notas")):
            base["book_action"] = "unchanged"
        else:
            base["book_action"] = "update"
        base["book_payload"] = update_payload
    else:
        base["book_action"] = "create"

    matched_book_id = int((matched_book or {}).get("id") or 0)
    for instance in book.instances:
        instance_plan = _plan_instance_sync(book=book, instance=instance, backend=backend, matched_book_id=matched_book_id)
        base["instances"].append(instance_plan)
    return base


def _match_existing_book(*, book: CatalogBook, backend: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if backend["status"] != "ready":
        return None, None
    by_code = backend["book_index"]["by_code"]
    by_source_book_id = backend["book_index"]["by_source_book_id"]
    by_pdf_hash = backend["book_index"]["by_pdf_hash"]
    by_pdf_path = backend["book_index"]["by_pdf_path"]
    by_workspace_dir = backend["book_index"]["by_workspace_dir"]

    workspace_matches = _unique_rows_by_id(
        row
        for key in _workspace_keys_for_book(book)
        for row in by_workspace_dir.get(key, [])
    )

    source_match = by_source_book_id.get(book.record.book_id)
    if source_match is not None:
        related = [row for row in workspace_matches if int(row.get("id") or 0) != int(source_match.get("id") or 0)]
        if related:
            return None, {
                "reason": "Ya existe mas de un libro en Biblioteca con la misma huella de origen. No se sincroniza automaticamente para evitar duplicados o merges incorrectos.",
                "match_strategy": "source_book_id+workspace_dir",
                "existing_books": [_book_ref(source_match), *[_book_ref(row) for row in related]],
            }
        return dict(source_match), None

    code_match = by_code.get(book.record.book_id)
    if code_match is not None:
        metadata = _extract_managed_metadata(str(code_match.get("notas") or ""))
        existing_source = str(metadata.get("source_book_id") or metadata.get("book_id") or "").strip()
        if existing_source and existing_source != book.record.book_id:
            return None, {
                "reason": "El codigo del libro ya existe y apunta a otro origen sincronizado.",
                "existing_book": _book_ref(code_match),
                "expected_source_book_id": book.record.book_id,
                "found_source_book_id": existing_source,
            }
        if not existing_source:
            return None, {
                "reason": "El codigo del libro ya existe en Biblioteca como registro previo. Se requiere merge manual antes de sincronizar.",
                "match_strategy": "code",
                "existing_books": [_book_ref(code_match)],
            }
        return dict(code_match), None

    hash_match = by_pdf_hash.get(book.record.pdf_hash_sha256)
    if hash_match is not None:
        metadata = _extract_managed_metadata(str(hash_match.get("notas") or ""))
        if not str(metadata.get("source_book_id") or metadata.get("book_id") or "").strip():
            return None, {
                "reason": "Ya existe un libro previo con el mismo PDF en Biblioteca. Se requiere merge manual antes de sincronizar.",
                "match_strategy": "pdf_hash",
                "existing_books": [_book_ref(hash_match)],
            }
        return dict(hash_match), None

    normalized_pdf = _normalize_existing_path(book.record.pdf_path)
    if normalized_pdf and normalized_pdf in by_pdf_path:
        path_match = dict(by_pdf_path[normalized_pdf])
        metadata = _extract_managed_metadata(str(path_match.get("notas") or ""))
        if not str(metadata.get("source_book_id") or metadata.get("book_id") or "").strip():
            return None, {
                "reason": "Ya existe un libro previo con la misma ruta PDF en Biblioteca. Se requiere merge manual antes de sincronizar.",
                "match_strategy": "pdf_path",
                "existing_books": [_book_ref(path_match)],
            }
        return path_match, None

    if workspace_matches:
        if len(workspace_matches) > 1:
            return None, {
                "reason": "Se detectaron varios libros existentes con el mismo workspace de origen. No se puede decidir automaticamente cual reutilizar.",
                "match_strategy": "workspace_dir",
                "existing_books": [_book_ref(row) for row in workspace_matches],
            }
        workspace_match = dict(workspace_matches[0])
        metadata = _extract_managed_metadata(str(workspace_match.get("notas") or ""))
        if not str(metadata.get("source_book_id") or metadata.get("book_id") or "").strip():
            return None, {
                "reason": "Ya existe un libro historico con la misma huella de origen. Se bloquea la sincronizacion para evitar duplicar libros ya trabajados.",
                "match_strategy": "workspace_dir",
                "existing_books": [_book_ref(workspace_match)],
            }
        return workspace_match, None
    return None, None


def _plan_instance_sync(
    *,
    book: CatalogBook,
    instance: CatalogInstance,
    backend: dict[str, Any],
    matched_book_id: int,
) -> dict[str, Any]:
    managed = {
        "schema_version": SYNC_SCHEMA_VERSION,
        "source_book_id": book.record.book_id,
        "source_instance_key": instance.config_snapshot["source_instance_key"],
        "source_note_path": book.source_note_path,
        "obsidian_note_path": book.obsidian_note_path,
        "pdf_hash_sha256": book.record.pdf_hash_sha256,
        "instance_semantics": instance.config_snapshot.get("instance_semantics") or "theme",
        "theme_name": instance.config_snapshot.get("theme_name") or instance.title,
        "page_range": instance.config_snapshot["page_range"],
        "page_range_display": instance.page_range_display,
        "selected_content_label": instance.config_snapshot.get("selected_content_label") or "",
        "selected_page_ranges_display": instance.config_snapshot.get("selected_page_ranges_display") or instance.page_range_display,
        "label": instance.label,
        "label_display": instance.title,
        "last_catalog_sync_at": utc_now_iso(),
    }
    selected_label = str(instance.config_snapshot.get("selected_content_label") or "").strip()
    selected_display = str(instance.config_snapshot.get("selected_page_ranges_display") or instance.page_range_display).strip()
    note_prefix = (
        f"Paginas operativas ({selected_label}): {selected_display}."
        if selected_label
        else f"Rango catalogado: {instance.page_range_display}."
    )
    payload = {
        "libro_id": matched_book_id,
        "tipo": instance.code,
        "titulo_practica": instance.title,
        "pdf_path": book.record.pdf_path,
        "total_esperado": 0,
        "session_path": "",
        "soluciones_dir": "",
        "nombre_instancia": instance.title,
        "estado": "pendiente",
        "config_snapshot": dict(instance.config_snapshot),
        "session_schema_version": 4,
        "notas": _merge_managed_notes("", managed, prefix=f"Tema catalogado: {instance.title}. {note_prefix}"),
        "activo": True,
    }
    if backend["status"] != "ready" or matched_book_id <= 0:
        return {
            "code": instance.code,
            "title": instance.title,
            "action": "create",
            "payload": payload,
            "matched_instance": None,
            "conflict": None,
        }

    matched, conflict = _match_existing_instance(backend=backend, book_id=matched_book_id, instance=instance)
    if conflict:
        return {
            "code": instance.code,
            "title": instance.title,
            "action": "conflict",
            "payload": payload,
            "matched_instance": None,
            "conflict": conflict,
        }
    if matched is None:
        return {
            "code": instance.code,
            "title": instance.title,
            "action": "create",
            "payload": payload,
            "matched_instance": None,
            "conflict": None,
        }
    merged_payload = _merged_instance_update_payload(matched, payload)
    action = "unchanged" if _same_payload(matched, merged_payload, ("tipo", "titulo_practica", "pdf_path", "notas", "estado")) else "update"
    return {
        "code": instance.code,
        "title": instance.title,
        "action": action,
        "payload": merged_payload,
        "matched_instance": _instance_ref(matched),
        "conflict": None,
    }


def _match_existing_instance(*, backend: dict[str, Any], book_id: int, instance: CatalogInstance) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    source_key = instance.config_snapshot["source_instance_key"]
    source_match = backend["instance_index"].get((int(book_id), source_key))
    if source_match is not None:
        return dict(source_match), None
    code_match = backend["instance_index"].get((int(book_id), instance.code))
    if code_match is None:
        return None, None
    metadata = _extract_managed_metadata(str(code_match.get("notas") or ""))
    existing_source_key = str(metadata.get("source_instance_key") or "").strip()
    if existing_source_key and existing_source_key != source_key:
        return None, {
            "reason": "La instancia ya existe con el mismo codigo pero apunta a otro rango sincronizado.",
            "existing_instance": _instance_ref(code_match),
            "expected_source_instance_key": source_key,
            "found_source_instance_key": existing_source_key,
        }
    return dict(code_match), None


def _apply_sync(*, plan: dict[str, Any], controller: BookProgressController, sync_root: Path) -> dict[str, Any]:
    backend = plan["backend"]
    if backend["status"] != "ready":
        raise RuntimeError("No se puede aplicar la sincronizacion: la base de datos no esta disponible.")
    db_name = str(backend["db_name"] or "").strip()
    imported_rows: list[dict[str, Any]] = []
    applied_summary = {
        "books_created": 0,
        "books_updated": 0,
        "books_unchanged": 0,
        "instances_created": 0,
        "instances_updated": 0,
        "instances_unchanged": 0,
    }

    for book_plan in plan["books"]:
        if book_plan["status"] != "planned" or book_plan["book_action"] == "conflict":
            imported_rows.append(_import_row_from_plan(book_plan, applied=False))
            continue

        book_payload = dict(book_plan["book_payload"])
        book_payload["cover_path"] = copy_cover_to_library_store(
            str(book_payload.get("cover_path") or ""),
            book_payload,
            db_name=db_name,
        )
        book_payload = _book_input_payload(book_payload)
        matched_book_id = int((book_plan.get("matched_book") or {}).get("id") or 0)
        if book_plan["book_action"] == "create":
            created_id = controller.crear_libro(db_name, BookCreateInput(**book_payload))
            matched_book_id = int(created_id)
            applied_summary["books_created"] += 1
        elif book_plan["book_action"] == "update":
            controller.actualizar_libro(db_name, matched_book_id, BookUpdateInput(**book_payload))
            applied_summary["books_updated"] += 1
        else:
            applied_summary["books_unchanged"] += 1

        current_book = controller.obtener_libro(db_name, matched_book_id) or {"id": matched_book_id, **book_payload}
        effective_pdf_path = str(current_book.get("pdf_path") or book_payload.get("pdf_path") or "").strip()

        for instance_plan in book_plan["instances"]:
            if instance_plan["action"] == "conflict":
                continue
            instance_payload = dict(instance_plan["payload"])
            instance_payload["libro_id"] = matched_book_id
            instance_payload["pdf_path"] = effective_pdf_path or str(instance_payload.get("pdf_path") or "").strip()
            instance_payload = _instance_input_payload(instance_payload)
            if instance_plan["action"] == "create":
                controller.crear_instancia(db_name, BookInstanceInput(**instance_payload))
                applied_summary["instances_created"] += 1
            elif instance_plan["action"] == "update":
                instance_id = int((instance_plan.get("matched_instance") or {}).get("id") or 0)
                controller.actualizar_instancia(db_name, instance_id, BookInstanceUpdateInput(**instance_payload))
                applied_summary["instances_updated"] += 1
            else:
                applied_summary["instances_unchanged"] += 1

        applied_row = _import_row_from_plan(book_plan, applied=True)
        applied_row["library_book_id"] = matched_book_id
        imported_rows.append(applied_row)

    return {
        "schema_version": SYNC_SCHEMA_VERSION,
        "db_name": db_name,
        "summary": applied_summary,
        "imported_rows": imported_rows,
        "artifact_root": str(sync_root),
    }


def _write_sync_outputs(
    *,
    sync_root: Path,
    report: dict[str, Any],
    conflicts: list[SyncConflict],
    imported_rows: list[dict[str, Any]],
) -> None:
    (sync_root / "sync_plan.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (sync_root / "conflicts.jsonl").write_text(
        "".join(json.dumps(asdict(item), ensure_ascii=False) + "\n" for item in conflicts),
        encoding="utf-8",
    )
    (sync_root / "imported_books.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in imported_rows),
        encoding="utf-8",
    )
    (sync_root / "sync_report.md").write_text(_render_report(report, conflicts), encoding="utf-8")


def _render_report(report: dict[str, Any], conflicts: list[SyncConflict]) -> str:
    lines = [
        "# Reporte de sincronizacion book_catalog -> Biblioteca/Fabrica",
        "",
        f"- `generado_en`: `{report['generated_at']}`",
        f"- `db_name`: `{report['db_name'] or 'sin_base'}`",
        f"- `backend_status`: `{report['backend']['status']}`",
        f"- `apply_requested`: `{report['apply_requested']}`",
        f"- `apply_executed`: `{report['apply_executed']}`",
        "",
        "## Resumen",
        "",
    ]
    for key, value in report["summary"].items():
        lines.append(f"- `{key}`: `{value}`")

    lines.extend(
        [
            "",
            "## Libros detectados",
            "",
            "| book_id | curso | estado | accion_libro | instancias | pdf |",
            "|---|---|---|---|---:|---|",
        ]
    )
    for row in report["books"]:
        lines.append(
            f"| `{row['book_id']}` | `{row['course'] or '-'}` | `{row['status']}` | "
            f"`{row['book_action']}` | {int(row['instances_total'])} | `{_short_path(row['pdf_path'])}` |"
        )

    lines.extend(
        [
            "",
            "## Conflictos y exclusiones",
            "",
            "| scope | key | severity | reason |",
            "|---|---|---|---|",
        ]
    )
    if conflicts:
        for item in conflicts:
            lines.append(f"| `{item.scope}` | `{item.key}` | `{item.severity}` | {item.reason} |")
    else:
        lines.append("| `-` | `-` | `-` | Sin conflictos detectados. |")

    if report["backend"].get("error"):
        lines.extend(
            [
                "",
                "## Backend",
                "",
                f"- `error`: `{report['backend']['error']}`",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _merge_managed_notes(notes: str, payload: dict[str, Any], *, prefix: str = "") -> str:
    clean_notes = _strip_managed_block(notes).strip()
    managed_block = "[book_catalog_sync]\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n[/book_catalog_sync]"
    if prefix and clean_notes:
        return f"{clean_notes}\n\n{managed_block}"
    if prefix:
        return f"{prefix}\n\n{managed_block}"
    if clean_notes:
        return f"{clean_notes}\n\n{managed_block}"
    return managed_block


def _extract_managed_metadata(notes: str) -> dict[str, Any]:
    match = MANAGED_NOTES_BLOCK_RE.search(str(notes or ""))
    if not match:
        return {}
    try:
        payload = json.loads(match.group(1))
    except Exception:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _strip_managed_block(notes: str) -> str:
    return MANAGED_NOTES_BLOCK_RE.sub("", str(notes or "")).strip()


def _book_ref(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row.get("id") or 0),
        "codigo": str(row.get("codigo") or "").strip(),
        "titulo": str(row.get("titulo") or "").strip(),
    }


def _instance_ref(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row.get("id") or 0),
        "tipo": str(row.get("tipo") or row.get("codigo_instancia") or "").strip(),
        "titulo_practica": str(row.get("titulo_practica") or "").strip(),
    }


def _merged_book_update_payload(current: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    merged = dict(payload)
    merged["notas"] = _merge_managed_notes(
        str(current.get("notas") or ""),
        _extract_managed_metadata(payload.get("notas") or ""),
    )
    return merged


def _merged_instance_update_payload(current: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    merged = dict(payload)
    merged["notas"] = _merge_managed_notes(
        str(current.get("notas") or ""),
        _extract_managed_metadata(payload.get("notas") or ""),
    )
    return merged


def _same_payload(current: dict[str, Any], payload: dict[str, Any], fields: Iterable[str]) -> bool:
    for field in fields:
        if field == "notas":
            current_meta = {
                key: value
                for key, value in _extract_managed_metadata(str(current.get(field) or "")).items()
                if key not in VOLATILE_MANAGED_NOTE_FIELDS
            }
            next_meta = {
                key: value
                for key, value in _extract_managed_metadata(str(payload.get(field) or "")).items()
                if key not in VOLATILE_MANAGED_NOTE_FIELDS
            }
            if current_meta != next_meta:
                return False
            continue
        if str(current.get(field) or "").strip() != str(payload.get(field) or "").strip():
            return False
    return True


def _import_row_from_plan(plan: dict[str, Any], *, applied: bool) -> dict[str, Any]:
    return {
        "schema_version": SYNC_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "applied": bool(applied),
        "book_id": plan["book_id"],
        "status": plan["status"],
        "book_action": plan["book_action"],
        "instances_total": int(plan["instances_total"]),
        "instances_to_create": sum(1 for item in plan["instances"] if item["action"] == "create"),
        "instances_to_update": sum(1 for item in plan["instances"] if item["action"] == "update"),
        "source_note_path": plan["source_note_path"],
        "pdf_hash_sha256": plan["pdf_hash_sha256"],
    }


def _normalize_existing_path(raw_path: str) -> str:
    candidate = _resolve_existing_file(raw_path)
    return str(candidate) if candidate is not None else ""


def _resolve_existing_file(raw_path: str) -> Path | None:
    clean = str(raw_path or "").strip()
    if not clean:
        return None
    try:
        candidate = remap_legacy_drive_path(Path(clean).expanduser(), prefer_existing=True)
    except Exception:
        candidate = Path(clean)
    try:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    except Exception:
        return None
    return None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _read_json_value(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _coalesce_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _short_path(value: str) -> str:
    text = str(value or "").strip()
    if len(text) <= 72:
        return text
    return "..." + text[-69:]


def _book_input_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "codigo": str(payload.get("codigo") or "").strip(),
        "titulo": str(payload.get("titulo") or "").strip(),
        "autor": str(payload.get("autor") or "").strip(),
        "editorial": str(payload.get("editorial") or "").strip(),
        "edicion": str(payload.get("edicion") or "").strip(),
        "curso": str(payload.get("curso") or "").strip(),
        "workspace_dir": str(payload.get("workspace_dir") or "").strip(),
        "pdf_path": str(payload.get("pdf_path") or "").strip(),
        "cover_path": str(payload.get("cover_path") or "").strip(),
        "estado": str(payload.get("estado") or "pendiente").strip(),
        "notas": str(payload.get("notas") or "").strip(),
        "activo": bool(payload.get("activo", True)),
    }


def _instance_input_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "libro_id": int(payload.get("libro_id") or 0),
        "tipo": str(payload.get("tipo") or "").strip(),
        "total_esperado": int(payload.get("total_esperado") or 0),
        "titulo_practica": str(payload.get("titulo_practica") or "").strip(),
        "pdf_path": str(payload.get("pdf_path") or "").strip(),
        "session_path": str(payload.get("session_path") or "").strip(),
        "soluciones_dir": str(payload.get("soluciones_dir") or "").strip(),
        "nombre_instancia": str(payload.get("nombre_instancia") or "").strip(),
        "estado": str(payload.get("estado") or "pendiente").strip(),
        "config_snapshot": dict(payload.get("config_snapshot") or {}),
        "session_schema_version": int(payload.get("session_schema_version") or 0),
        "notas": str(payload.get("notas") or "").strip(),
        "activo": bool(payload.get("activo", True)),
    }
