from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any, Callable

from utils.project_layout import infer_workspace_from_session_path, normalize_instance_name, project_dirs, remap_legacy_drive_path


IMAGE_MARKER_RE = re.compile(r"\[\[\s*Imagen\s*=\s*([^\]\r\n]+?)\s*\]\]", re.IGNORECASE)
ESTADO_TAG_RE = re.compile(r"\[\[\s*Estado\s*=\s*([^\]\r\n]+?)\s*\]\]", re.IGNORECASE)
ANSWER_KEY_SECTION_RE = re.compile(
    r"(?is)(?:^|\n)\s*(?:"
    r"\\(?:section|subsection|subsubsection)\*?\{\s*claves?\s+de\s+respuestas?\s*\}"
    r"|\\textbf\{\s*claves?\s+de\s+respuestas?\s*\}"
    r"|claves?\s+de\s+respuestas?\s*:?"
    r")"
)
ANSWER_KEY_ENTRY_RE = re.compile(r"(?<!\d)(\d{1,4})\)\s*([A-Za-z])\b")
CLAVE_TAG_RE = re.compile(r"\[\[\s*clave\s*=\s*([^\]\r\n]+?)\s*\]\]", re.IGNORECASE)
TEX_ITEM_BLOCK_RE = re.compile(
    r"(?is)(\\item\s*\[\s*\\textbf\{\s*(\d+)\.?\s*\}\s*\].*?)"
    r"(?=(?:\n\s*\\item\s*\[\s*\\textbf\{)|(?:\n\s*\\end\{enumerate\})|\Z)"
)
TRANSPARENT_PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c636000000200015d0b2a0000000049454e44ae426082"
)


@dataclass(slots=True)
class SessionWordJob:
    session_path: Path
    output_docx: Path
    repo: Path
    python: str
    template: Path | None
    style: str


class LatexWordService:
    """Headless Module 7 service used by web routes and tests."""

    def __init__(
        self,
        *,
        controller: Any | None = None,
        practice_controller: Any | None = None,
        file_url_resolver: Callable[[str], str] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.controller = controller
        self.practice_controller = practice_controller
        self.file_url_resolver = file_url_resolver
        self.log = log or (lambda _text: None)

    def default_editor_repo(self) -> Path:
        candidates = [
            Path(value)
            for value in (
                os.getenv("LATEX_WORD_EDITOR_REPO", ""),
                os.getenv("MODULO7_EDITOR_REPO", ""),
            )
            if str(value or "").strip()
        ] + [
            Path(__file__).resolve().parents[3] / "Editor_de_practicas",
            Path(r"K:\Github\Editor_de_practicas"),
            Path(r"E:\Github\Editor_de_practicas"),
        ]
        for candidate in candidates:
            if str(candidate) and candidate.exists():
                return candidate
        return Path.cwd()

    def default_template(self, repo: Path | str | None = None) -> Path | None:
        repo_path = Path(repo) if repo else self.default_editor_repo()
        candidate = repo_path / "plantilla.docx"
        return candidate if candidate.exists() else None

    def default_sessions_root(self) -> Path:
        candidates = [
            Path(value)
            for value in (os.getenv("TRANSCRIPTOR_SESSIONS_ROOT", ""),)
            if str(value or "").strip()
        ] + [
            Path(r"E:\Banco de Preguntas"),
            Path(r"K:\Banco de Preguntas"),
            Path.home() / "Documents" / "Banco de Preguntas",
        ]
        for candidate in candidates:
            if str(candidate) and candidate.exists():
                return candidate
        return Path.cwd()

    def list_sessions(self, *, db_name: str = "", root: str = "") -> dict[str, Any]:
        db_name = str(db_name or "").strip()
        root = str(root or "").strip()
        if root:
            return self._list_sessions_from_root(Path(root).expanduser())
        if db_name and self.controller is not None:
            try:
                payload = self._list_sessions_from_library(db_name)
                if payload["summary"]["instances_total"] > 0:
                    return payload
            except Exception as exc:
                self.log(f"No se pudo listar sesiones desde biblioteca ({db_name}): {exc}")
        return self._list_sessions_from_root(Path(root).expanduser() if root else self.default_sessions_root())

    def _list_sessions_from_library(self, db_name: str) -> dict[str, Any]:
        books: list[dict[str, Any]] = []
        rows = self.controller.listar_libros(db_name)
        db_problem_counts = self._db_instance_problem_counts(db_name)
        instances_by_book = self._library_instances_by_book(db_name)
        for book in rows:
            book_id = int(book.get("id") or 0)
            if book_id <= 0:
                continue
            instances: list[dict[str, Any]] = []
            source_instances = (
                instances_by_book.get(book_id)
                if instances_by_book is not None
                else self.controller.listar_instancias_libro(db_name, book_id)
            )
            for instance in source_instances:
                current_instance = dict(instance or {})
                session_path = self._resolve_library_instance_session_path(book, instance)
                instances.append(
                    self._session_instance_payload(
                        book,
                        current_instance,
                        session_path=session_path,
                        db_name=db_name,
                        db_problem_counts=db_problem_counts,
                    )
                )
            if instances:
                books.append(self._book_payload(book, instances))
        return self._catalog_payload(books, source="library", db_name=db_name, root="")

    def _library_instances_by_book(self, db_name: str) -> dict[int, list[dict[str, Any]]] | None:
        getter = getattr(self.controller, "listar_instancias_todos", None)
        if not callable(getter):
            return None
        try:
            raw = getter(str(db_name or "").strip()) or {}
        except Exception as exc:
            self.log(f"No se pudo precargar instancias de biblioteca: {exc}")
            return None
        result: dict[int, list[dict[str, Any]]] = {}
        for book_id, rows in dict(raw).items():
            try:
                clean_id = int(book_id or 0)
            except Exception:
                continue
            result[clean_id] = [dict(row or {}) for row in (rows or []) if isinstance(row, dict)]
        return result

    def _library_instance_stats_by_id(self, db_name: str, book_id: int) -> dict[Any, dict[str, Any]]:
        getter = getattr(self.controller, "obtener_dashboard_libro", None)
        if not callable(getter):
            return {}
        try:
            dashboard = getter(db_name, int(book_id))
        except Exception as exc:
            self.log(f"No se pudo cargar avance BD de instancias libro={book_id}: {exc}")
            return {}
        raw_items = []
        if isinstance(dashboard, dict):
            raw_items = list(dashboard.get("instancias") or [])
        elif hasattr(dashboard, "instancias"):
            raw_items = list(getattr(dashboard, "instancias") or [])
        result: dict[int, dict[str, Any]] = {}
        for item in raw_items:
            if hasattr(item, "__dataclass_fields__"):
                from dataclasses import asdict

                item = asdict(item)
            if not isinstance(item, dict):
                continue
            instance_id = int(item.get("instancia_id") or item.get("id") or 0)
            if instance_id > 0:
                result[instance_id] = dict(item)
            label = str(item.get("tipo") or item.get("codigo_instancia") or "").strip()
            if label:
                result[self._stats_label_key(label)] = dict(item)
        return result

    def _stats_label_key(self, value: str) -> str:
        return "tipo:" + re.sub(r"\s+", " ", str(value or "").strip()).casefold()

    def _list_sessions_from_root(self, root: Path) -> dict[str, Any]:
        root = self._normalize_path(root)
        books: list[dict[str, Any]] = []
        if not root.exists() or not root.is_dir():
            return self._catalog_payload([], source="filesystem", db_name="", root=str(root))
        for sessions_dir in sorted(path for path in root.rglob("sessions") if path.is_dir()):
            workspace_dir = sessions_dir.parent
            try:
                rel = str(workspace_dir.relative_to(root))
            except Exception:
                rel = workspace_dir.name
            instances = []
            for session_path in sorted(sessions_dir.glob("*.json")):
                label = self._build_session_instance_label(session_path)
                instances.append(
                    self._session_instance_payload(
                        {"titulo": rel, "codigo": rel, "workspace_dir": str(workspace_dir)},
                        {"tipo": label, "session_path": str(session_path)},
                        session_path=session_path,
                    )
                )
            if instances:
                books.append(
                    self._book_payload(
                        {
                            "id": 0,
                            "codigo": rel,
                            "titulo": rel,
                            "workspace_dir": str(workspace_dir),
                        },
                        instances,
                    )
                )
        return self._catalog_payload(books, source="filesystem", db_name="", root=str(root))

    def _book_payload(self, book: dict[str, Any], instances: list[dict[str, Any]]) -> dict[str, Any]:
        code = str(book.get("codigo") or book.get("code") or "").strip()
        title = str(book.get("titulo") or book.get("title") or code or "Libro").strip()
        cover_path = self._normalize_existing_text_path(str(book.get("cover_path") or book.get("coverPath") or ""))
        cover_url = self.file_url_resolver(cover_path) if cover_path and self.file_url_resolver else ""
        ready = sum(1 for row in instances if row.get("word_exists"))
        complete_word_path = self._book_complete_word_path(book=book, code=code, title=title)
        complete_word_exists = bool(complete_word_path and complete_word_path.exists())
        complete_word_url = self.file_url_resolver(str(complete_word_path)) if complete_word_exists and self.file_url_resolver else ""
        return {
            "book_key": self._stable_key(code, title, str(book.get("id") or "")),
            "id": int(book.get("id") or 0),
            "code": code,
            "title": title,
            "author": str(book.get("autor") or book.get("author") or "").strip(),
            "editorial": str(book.get("editorial") or "").strip(),
            "course": str(book.get("curso") or book.get("subject") or "").strip(),
            "status": str(book.get("estado") or book.get("status") or "").strip(),
            "pdf_path": self._normalize_existing_text_path(str(book.get("pdf_path") or book.get("pdfPath") or "")),
            "workspace_dir": self._normalize_existing_text_path(str(book.get("workspace_dir") or "")),
            "cover_path": cover_path,
            "cover_url": cover_url,
            "complete_word_path": str(complete_word_path or ""),
            "complete_word_exists": complete_word_exists,
            "complete_word_url": complete_word_url,
            "instances": sorted(instances, key=lambda row: self._session_instance_sort_key(str(row.get("title") or ""))),
            "counts": {
                "instances": len(instances),
                "word_ready": ready,
                "word_pending": max(0, len(instances) - ready),
            },
        }

    def _book_complete_word_path(self, *, book: dict[str, Any], code: str, title: str) -> Path | None:
        source_pdf_path = str(book.get("pdf_path") or book.get("pdfPath") or "").strip()
        raw_title = f"{str(title or code or 'Documento').strip()} - completo"
        try:
            return self._normalize_path(Path(self._default_combined_instances_output_name(title=raw_title, libro_codigo=code, source_pdf_path=source_pdf_path)))
        except Exception:
            return None

    def _session_instance_payload(
        self,
        book: dict[str, Any],
        instance: dict[str, Any],
        *,
        session_path: Path | None,
        db_name: str = "",
        db_problem_counts: dict[tuple[str, str], int] | None = None,
    ) -> dict[str, Any]:
        label = str(instance.get("tipo") or instance.get("name") or instance.get("label") or "").strip()
        if not label and session_path is not None:
            label = self._build_session_instance_label(session_path)
        session_exists = bool(session_path and session_path.exists())
        indicators = instance.get("indicators") if isinstance(instance.get("indicators"), dict) else {}
        db_problem_count = int(
            instance.get("subidos_bd")
            or instance.get("problemas_bd")
            or indicators.get("subidos_bd")
            or indicators.get("total")
            or 0
        )
        if db_problem_count <= 0:
            book_code = str(book.get("codigo") or book.get("code") or "").strip()
            count_key = self._db_instance_count_key(book_code, label)
            if db_problem_counts is not None:
                db_problem_count = int(db_problem_counts.get(count_key, 0) or 0)
            else:
                db_problem_count = self._count_db_instance_problems(
                    db_name=db_name,
                    libro_codigo=book_code,
                    codigo_instancia=label,
                )
        exportable_info = (
            {"exportable": False, "items": 0, "reason": "se usara BD"}
            if db_problem_count > 0
            else (self._session_exportable_info(session_path) if session_exists else {"exportable": False, "items": 0, "reason": "sin sesion"})
        )
        word_path = self.library_instance_word_path_for(book=book, instance=instance, label=label, session_path=session_path)
        word_exists = bool(word_path and word_path.exists())
        session_url = self.file_url_resolver(str(session_path)) if session_exists and self.file_url_resolver else ""
        word_url = self.file_url_resolver(str(word_path)) if word_exists and self.file_url_resolver else ""
        key = self._stable_key(str(book.get("codigo") or ""), label, str(session_path or ""))
        return {
            "instance_key": key,
            "instance_ref": self._stable_key(str(db_name or ""), str(book.get("id") or ""), str(instance.get("id") or ""), str(book.get("codigo") or ""), label),
            "id": int(instance.get("id") or 0),
            "book_id": int(book.get("id") or 0),
            "book_code": str(book.get("codigo") or book.get("code") or "").strip(),
            "instance_code": label,
            "db_ref_available": bool(str(book.get("codigo") or book.get("code") or "").strip() and label),
            "title": label or f"instancia-{int(instance.get('id') or 0)}",
            "practice_title": str(instance.get("titulo_practica") or instance.get("practice_title") or "").strip(),
            "session_path": str(session_path or ""),
            "session_exists": session_exists,
            "session_exportable": bool(exportable_info.get("exportable")),
            "exportable_items": int(exportable_info.get("items") or 0),
            "exportable_reason": str(exportable_info.get("reason") or ""),
            "db_exportable": db_problem_count > 0,
            "db_problem_count": db_problem_count,
            "session_url": session_url,
            "word_path": str(word_path or ""),
            "word_exists": word_exists,
            "word_url": word_url,
            "expected_total": int(instance.get("total_esperado") or 0),
            "notes": str(instance.get("notas") or instance.get("notes") or "").strip(),
        }

    def _count_db_instance_problems(self, *, db_name: str, libro_codigo: str, codigo_instancia: str) -> int:
        if not str(db_name or "").strip() or not str(libro_codigo or "").strip() or not str(codigo_instancia or "").strip():
            return 0
        try:
            controller = self._practice_controller()
            counter = getattr(controller, "contar_problemas_por_instancia", None)
            if callable(counter):
                return int(
                    counter(
                        str(db_name or "").strip(),
                        libro_codigo=str(libro_codigo or "").strip(),
                        codigo_instancia=str(codigo_instancia or "").strip(),
                    )
                    or 0
                )
        except Exception as exc:
            self.log(f"No se pudo contar problemas BD instancia {libro_codigo}/{codigo_instancia}: {exc}")
        return 0

    def _db_instance_problem_counts(self, db_name: str) -> dict[tuple[str, str], int] | None:
        if not str(db_name or "").strip():
            return None
        try:
            controller = self._practice_controller()
            counter = getattr(controller, "contar_problemas_por_instancia_masivo", None)
            if not callable(counter):
                return None
            raw_counts = counter(str(db_name or "").strip()) or {}
            result: dict[tuple[str, str], int] = {}
            for key, value in dict(raw_counts).items():
                if not isinstance(key, tuple) or len(key) != 2:
                    continue
                result[(str(key[0] or "").strip(), str(key[1] or "").strip())] = int(value or 0)
            return result
        except Exception as exc:
            self.log(f"No se pudo precargar conteos BD de instancias: {exc}")
            return None

    def _db_instance_count_key(self, libro_codigo: str, codigo_instancia: str) -> tuple[str, str]:
        return (self._normalize_db_count_key(libro_codigo), self._normalize_db_count_key(codigo_instancia))

    def _normalize_db_count_key(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        text = re.sub(r"\s+", " ", text)
        return text.casefold()

    def _catalog_payload(self, books: list[dict[str, Any]], *, source: str, db_name: str, root: str) -> dict[str, Any]:
        instances = [row for book in books for row in book.get("instances", [])]
        return {
            "schema_version": "latex_word_sessions_v1",
            "source": source,
            "db_name": db_name,
            "root": root,
            "repo": str(self.default_editor_repo()),
            "template": str(self.default_template() or ""),
            "summary": {
                "books_total": len(books),
                "instances_total": len(instances),
                "sessions_found": sum(1 for row in instances if row.get("session_exists")),
                "word_ready": sum(1 for row in instances if row.get("word_exists")),
                "word_pending": sum(1 for row in instances if not row.get("word_exists")),
            },
            "books": books,
        }

    def convert_session(
        self,
        *,
        session_path: str,
        output_docx: str = "",
        repo: str = "",
        python: str = "",
        template: str = "",
        style: str = "Estilo_plantilla",
    ) -> dict[str, Any]:
        job = self._build_session_job(
            session_path=session_path,
            output_docx=output_docx,
            repo=repo,
            python=python,
            template=template,
            style=style,
        )
        input_tex = self.resolve_session_input_tex(session_path=job.session_path, output_docx=job.output_docx)
        images_dir = self.prepare_images_dir_for_session(job.session_path, output_docx=job.output_docx)
        produced = self.run_tex_to_word(job=job, input_tex=input_tex, images_dir=images_dir)
        return {
            "schema_version": "latex_word_conversion_v1",
            "ok": True,
            "session_path": str(job.session_path),
            "input_tex": str(input_tex),
            "images_dir": str(images_dir or ""),
            "output_docx": str(produced),
            "word_path": str(produced),
            "word_exists": produced.exists(),
            "word_url": self.file_url_resolver(str(produced)) if produced.exists() and self.file_url_resolver else "",
        }

    def list_db_problems(
        self,
        *,
        db_name: str,
        curso: str = "",
        tema_id: Any | None = None,
        subtema_id: Any | None = None,
        autor: str = "",
        editorial: str = "",
        libro: Any | None = None,
        estado: str = "Todos",
        clave: str = "Todos",
        limit: int = 100,
        aleatorio: bool = False,
    ) -> dict[str, Any]:
        controller = self._practice_controller()
        db = str(db_name or "").strip()
        if not db:
            raise ValueError("db_name es requerido.")
        bounded_limit = max(1, min(int(limit or 100), 500))
        curso_value = str(curso or "").strip()
        normalizar_curso = getattr(controller, "normalizar_curso", None)
        if curso_value and callable(normalizar_curso):
            curso_value = str(normalizar_curso(curso_value) or curso_value).strip()
        filters = {
            "curso": curso_value,
            "tema_id": tema_id,
            "subtema_id": subtema_id,
            "autor": str(autor or "").strip(),
            "editorial": str(editorial or "").strip(),
            "libro": libro,
            "estado": str(estado or "Todos").strip() or "Todos",
            "clave": str(clave or "Todos").strip() or "Todos",
        }
        total = int(controller.contar_problemas(db, **filters))
        rows = []
        if total > 0:
            rows = controller.obtener_problemas(
                db,
                cantidad=min(total, bounded_limit),
                **filters,
                aleatorio=bool(aleatorio),
            )
        return {
            "schema_version": "latex_word_problem_selection_v1",
            "db_name": db,
            "filters": filters,
            "limit": bounded_limit,
            "total": total,
            "count": len(rows),
            "options": self._db_problem_options(controller, db, filters),
            "problems": [self._problem_payload(row) for row in rows],
        }

    def convert_db_problems(
        self,
        *,
        db_name: str,
        problem_ids: list[int],
        output_docx: str = "",
        title: str = "",
        structure: str = "",
        repo: str = "",
        python: str = "",
        template: str = "",
        style: str = "Estilo_plantilla",
    ) -> dict[str, Any]:
        controller = self._practice_controller()
        db = str(db_name or "").strip()
        if not db:
            raise ValueError("db_name es requerido.")
        ids = self._normalize_problem_ids(problem_ids)
        if not ids:
            raise ValueError("Selecciona al menos un problema.")
        problems = controller.obtener_problemas_por_ids(db, problem_ids=ids)
        if not problems:
            raise RuntimeError("No se encontraron problemas para convertir.")
        output = self._normalize_output_docx_path(output_docx or self._default_db_output_name(title, db))
        job = self._build_word_job(
            output_docx=str(output),
            repo=repo,
            python=python,
            template=template,
            style=style,
        )
        source_text = self._build_scan_source_text_from_db(problems, structure=structure, title=title)
        if not source_text:
            raise RuntimeError("Los problemas seleccionados no tienen enunciado_latex utilizable.")
        input_tex = self._write_scan_source_tex(output_docx=job.output_docx, suffix="__db_source", source_text=source_text)
        images_dir = self.prepare_images_dir_for_db(problems, output_docx=job.output_docx, source_text=source_text)
        produced = self.run_tex_to_word(job=job, input_tex=input_tex, images_dir=images_dir)
        return {
            "schema_version": "latex_word_db_conversion_v1",
            "ok": True,
            "db_name": db,
            "problem_ids": ids,
            "count": len(problems),
            "input_tex": str(input_tex),
            "images_dir": str(images_dir or ""),
            "output_docx": str(produced),
            "word_path": str(produced),
            "word_exists": produced.exists(),
            "word_url": self.file_url_resolver(str(produced)) if produced.exists() and self.file_url_resolver else "",
        }

    def convert_db_instance(
        self,
        *,
        db_name: str,
        libro_codigo: str,
        codigo_instancia: str,
        session_path: str = "",
        output_docx: str = "",
        title: str = "",
        repo: str = "",
        python: str = "",
        template: str = "",
        style: str = "Estilo_plantilla",
    ) -> dict[str, Any]:
        controller = self._practice_controller()
        db = str(db_name or "").strip()
        if not db:
            raise ValueError("db_name es requerido.")
        book_code = str(libro_codigo or "").strip()
        instance_code = str(codigo_instancia or "").strip()
        if not book_code:
            raise ValueError("libro_codigo es requerido.")
        if not instance_code:
            raise ValueError("codigo_instancia es requerido.")
        fetch = getattr(controller, "obtener_problemas_por_instancia", None)
        if not callable(fetch):
            raise RuntimeError("El controlador de practicas no soporta consulta por instancia.")
        problems = fetch(db, libro_codigo=book_code, codigo_instancia=instance_code)
        if not problems:
            raise RuntimeError(
                "No se encontraron problemas en BD para esta instancia. "
                "Verifica que la Fabrica haya subido la instancia a la base local."
            )
        output = self._normalize_output_docx_path(
            output_docx
            or self._default_instance_output_name(
                title=title,
                libro_codigo=book_code,
                codigo_instancia=instance_code,
                session_path=session_path,
                source_pdf_path=self._source_pdf_path_from_problems(problems),
            )
        )
        job = self._build_word_job(
            output_docx=str(output),
            repo=repo,
            python=python,
            template=template,
            style=style,
        )
        source_title = title or f"{book_code} - {instance_code}"
        source_text = self._build_scan_source_text_from_db(problems, title=source_title)
        if not source_text:
            raise RuntimeError("Los problemas de la instancia no tienen enunciado_latex utilizable.")
        input_tex = self._write_scan_source_tex(output_docx=job.output_docx, suffix="__instance_source", source_text=source_text)
        images_dir = self.prepare_images_dir_for_db(problems, output_docx=job.output_docx, source_text=source_text)
        produced = self.run_tex_to_word(job=job, input_tex=input_tex, images_dir=images_dir)
        return {
            "schema_version": "latex_word_db_instance_conversion_v1",
            "ok": True,
            "db_name": db,
            "libro_codigo": book_code,
            "codigo_instancia": instance_code,
            "count": len(problems),
            "input_tex": str(input_tex),
            "images_dir": str(images_dir or ""),
            "output_docx": str(produced),
            "word_path": str(produced),
            "word_exists": produced.exists(),
            "word_url": self.file_url_resolver(str(produced)) if produced.exists() and self.file_url_resolver else "",
        }

    def convert_db_instances_combined(
        self,
        *,
        db_name: str,
        instances: list[dict[str, Any]],
        output_docx: str = "",
        title: str = "",
        repo: str = "",
        python: str = "",
        template: str = "",
        style: str = "Estilo_plantilla",
    ) -> dict[str, Any]:
        controller = self._practice_controller()
        db = str(db_name or "").strip()
        if not db:
            raise ValueError("db_name es requerido.")
        clean_instances = self._normalize_combined_instance_specs(instances)
        if not clean_instances:
            raise ValueError("Selecciona al menos una instancia.")
        fetch = getattr(controller, "obtener_problemas_por_instancia", None)
        if not callable(fetch):
            raise RuntimeError("El controlador de practicas no soporta consulta por instancia.")

        sections: list[dict[str, Any]] = []
        all_problems: list[dict[str, Any]] = []
        first_pdf_path = ""
        for spec in clean_instances:
            problems = fetch(db, libro_codigo=spec["libro_codigo"], codigo_instancia=spec["codigo_instancia"])
            if not problems:
                sections.append({**spec, "count": 0, "skipped": True, "reason": "sin problemas en BD"})
                continue
            section_title = spec["title"] or spec["codigo_instancia"]
            source_text = self._build_scan_source_text_from_db(problems)
            if not source_text:
                sections.append({**spec, "count": len(problems), "skipped": True, "reason": "sin enunciado_latex utilizable"})
                continue
            sections.append({**spec, "title": section_title, "count": len(problems), "source_text": source_text})
            all_problems.extend(problems)
            if not first_pdf_path:
                first_pdf_path = self._source_pdf_path_from_problems(problems)

        usable_sections = [row for row in sections if not row.get("skipped") and str(row.get("source_text") or "").strip()]
        if not usable_sections:
            raise RuntimeError("No hay instancias con problemas utilizables para crear el Word completo.")

        output = self._normalize_output_docx_path(
            output_docx
            or self._default_combined_instances_output_name(
                title=title,
                libro_codigo=clean_instances[0]["libro_codigo"],
                source_pdf_path=first_pdf_path,
            )
        )
        job = self._build_word_job(
            output_docx=str(output),
            repo=repo,
            python=python,
            template=template,
            style=style,
        )
        source_text = self._build_combined_instances_source_text(usable_sections, title=title)
        input_tex = self._write_raw_scan_source_tex(output_docx=job.output_docx, suffix="__combined_instances_source", source_text=source_text)
        images_dir = self.prepare_images_dir_for_db(all_problems, output_docx=job.output_docx, source_text=source_text)
        produced = self.run_tex_to_word(job=job, input_tex=input_tex, images_dir=images_dir)
        return {
            "schema_version": "latex_word_db_instances_combined_conversion_v1",
            "ok": True,
            "db_name": db,
            "count": sum(int(row.get("count") or 0) for row in usable_sections),
            "instances": [
                {
                    "libro_codigo": row.get("libro_codigo", ""),
                    "codigo_instancia": row.get("codigo_instancia", ""),
                    "title": row.get("title", ""),
                    "count": int(row.get("count") or 0),
                    "skipped": bool(row.get("skipped")),
                    "reason": str(row.get("reason") or ""),
                }
                for row in sections
            ],
            "input_tex": str(input_tex),
            "images_dir": str(images_dir or ""),
            "output_docx": str(produced),
            "word_path": str(produced),
            "word_exists": produced.exists(),
            "word_url": self.file_url_resolver(str(produced)) if produced.exists() and self.file_url_resolver else "",
        }

    def _practice_controller(self) -> Any:
        if self.practice_controller is None:
            from modulos.modulo6_practicas.controlador_practicas import PracticeBuilderController

            self.practice_controller = PracticeBuilderController()
        return self.practice_controller

    def _db_problem_options(self, controller: Any, db_name: str, filters: dict[str, Any]) -> dict[str, Any]:
        def safe_call(name: str, *args: Any, **kwargs: Any) -> list[Any]:
            fn = getattr(controller, name, None)
            if not callable(fn):
                return []
            try:
                value = fn(*args, **kwargs)
            except Exception as exc:
                self.log(f"No se pudo cargar opcion BD {name}: {exc}")
                return []
            return list(value or []) if isinstance(value, (list, tuple)) else []

        curso = str(filters.get("curso") or "").strip()
        tema_id = filters.get("tema_id")
        subtema_id = filters.get("subtema_id")
        autor = str(filters.get("autor") or "").strip()
        editorial = str(filters.get("editorial") or "").strip()
        libro = filters.get("libro")
        return {
            "cursos": safe_call("listar_cursos", db_name),
            "temas": safe_call("listar_temas", db_name, curso=curso),
            "subtemas": safe_call("listar_subtemas", db_name, tema_id=tema_id) if tema_id not in (None, "") else [],
            "autores": safe_call(
                "listar_autores",
                db_name,
                curso=curso,
                tema_id=tema_id,
                subtema_id=subtema_id,
                editorial=editorial,
                libro=libro,
            ),
            "editoriales": safe_call(
                "listar_editoriales",
                db_name,
                curso=curso,
                tema_id=tema_id,
                subtema_id=subtema_id,
                autor=autor,
                libro=libro,
            ),
            "libros": safe_call(
                "listar_libros_problemas",
                db_name,
                curso=curso,
                tema_id=tema_id,
                subtema_id=subtema_id,
                autor=autor,
                editorial=editorial,
            ),
            "estados": ["Todos", "sin_revisar", "consistente", "inconsistente"],
            "claves": ["Todos", "A", "B", "C", "D", "E", "Sin clave"],
        }

    def _problem_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        text = str(row.get("enunciado_latex") or "").strip()
        images = row.get("imagenes") if isinstance(row.get("imagenes"), list) else []
        return {
            "id": int(row.get("id") or 0),
            "numero_original": int(row.get("numero_original") or 0),
            "curso": str(row.get("curso") or "").strip(),
            "tema": str(row.get("tema") or "").strip(),
            "subtema": str(row.get("subtema") or "").strip(),
            "autor": str(row.get("autor") or "").strip(),
            "editorial": str(row.get("editorial") or "").strip(),
            "respuesta_correcta": str(row.get("respuesta_correcta") or "").strip(),
            "consistencia_matematica": str(row.get("consistencia_matematica") or "").strip(),
            "tipo_problema": str(row.get("tipo_problema") or "").strip(),
            "examen": str(row.get("examen") or "").strip(),
            "imagenes_count": len(images),
            "has_image_marker": bool(IMAGE_MARKER_RE.search(text)),
            "enunciado_latex": text,
            "preview": self._compact_problem_preview(text),
        }

    def _compact_problem_preview(self, text: str, limit: int = 240) -> str:
        clean = re.sub(r"\s+", " ", str(text or "")).strip()
        return clean if len(clean) <= limit else clean[: max(0, limit - 1)].rstrip() + "..."

    def _normalize_problem_ids(self, problem_ids: list[int] | tuple[int, ...] | Any) -> list[int]:
        if not isinstance(problem_ids, (list, tuple)):
            return []
        ids: list[int] = []
        seen: set[int] = set()
        for raw in problem_ids:
            try:
                pid = int(raw)
            except Exception:
                continue
            if pid <= 0 or pid in seen:
                continue
            seen.add(pid)
            ids.append(pid)
        return ids

    def _build_word_job(
        self,
        *,
        output_docx: str,
        repo: str,
        python: str,
        template: str,
        style: str,
    ) -> SessionWordJob:
        output = self._normalize_output_docx_path(output_docx)
        repo_path = self._normalize_path(Path(repo).expanduser()) if str(repo or "").strip() else self.default_editor_repo()
        script = repo_path / "latex_to_word.py"
        if not script.exists():
            raise FileNotFoundError(f"No existe script de conversion: {script}")
        py, errors = self.resolve_python(repo_path, python)
        for error in errors[:4]:
            self.log(f"Python candidato descartado: {error}")
        template_path = self._normalize_path(Path(template).expanduser()) if str(template or "").strip() else self.default_template(repo_path)
        if template_path is not None and not template_path.exists():
            template_path = None
        return SessionWordJob(
            session_path=Path(),
            output_docx=output,
            repo=repo_path,
            python=py,
            template=template_path,
            style=str(style or "Estilo_plantilla").strip() or "Estilo_plantilla",
        )

    def _default_db_output_name(self, title: str, db_name: str) -> str:
        base = str(title or "").strip() or f"practica_{db_name}"
        safe = self._safe_filename(base, fallback="practica_bd")
        return str((Path.cwd() / f"{safe}.docx").resolve())

    def _default_instance_output_name(
        self,
        *,
        title: str = "",
        libro_codigo: str,
        codigo_instancia: str,
        session_path: str = "",
        source_pdf_path: str = "",
    ) -> str:
        safe = self._safe_filename(f"{libro_codigo}__{codigo_instancia}", fallback="instancia")
        pdf_dir = self._word_output_dir_for_pdf(source_pdf_path)
        if pdf_dir is not None:
            return str((pdf_dir / f"{safe}.docx").resolve())
        if str(session_path or "").strip():
            session = self._normalize_path(Path(str(session_path or "").strip()))
            if session.parent.exists():
                return str((session.parent / f"{safe}.docx").resolve())
        return str((Path.cwd() / f"{safe}.docx").resolve())

    def _default_combined_instances_output_name(self, *, title: str = "", libro_codigo: str = "", source_pdf_path: str = "") -> str:
        base = str(title or "").strip() or f"{libro_codigo}__documento_completo"
        safe = self._safe_filename(base, fallback="documento_completo")
        pdf_dir = self._word_output_dir_for_pdf(source_pdf_path)
        if pdf_dir is not None:
            return str((pdf_dir / f"{safe}.docx").resolve())
        return str((Path.cwd() / f"{safe}.docx").resolve())

    def _word_output_dir_for_pdf(self, source_pdf_path: str = "") -> Path | None:
        raw = str(source_pdf_path or "").strip()
        if not raw:
            return None
        try:
            pdf_path = self._normalize_path(Path(raw).expanduser())
        except Exception:
            return None
        if pdf_path.suffix.lower() != ".pdf":
            return None
        parent = pdf_path.parent
        if not parent:
            return None
        return parent / "Word"

    def _source_pdf_path_from_problems(self, problems: list[dict[str, Any]]) -> str:
        for problem in problems or []:
            if not isinstance(problem, dict):
                continue
            for field in ("pdf_path", "archivo_origen"):
                raw = str(problem.get(field) or "").strip()
                if raw and raw.lower().endswith(".pdf"):
                    return raw
        return ""

    def _safe_filename(self, value: str, *, fallback: str = "archivo", limit: int = 120) -> str:
        clean = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._-")
        return (clean[:limit].strip("._-") or fallback)

    def _write_scan_source_tex(self, *, output_docx: Path, suffix: str, source_text: str) -> Path:
        generated = output_docx.with_suffix(".tex")
        generated = generated.with_name(f"{generated.stem}{suffix}.tex")
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text(self._ensure_enumerate_wrapper(str(source_text or "").strip()) + "\n", encoding="utf-8")
        return generated

    def _write_raw_scan_source_tex(self, *, output_docx: Path, suffix: str, source_text: str) -> Path:
        generated = output_docx.with_suffix(".tex")
        generated = generated.with_name(f"{generated.stem}{suffix}.tex")
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text(str(source_text or "").strip() + "\n", encoding="utf-8")
        return generated

    def _normalize_combined_instance_specs(self, instances: list[dict[str, Any]]) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        if not isinstance(instances, list):
            return result
        for row in instances:
            if not isinstance(row, dict):
                continue
            book_code = str(row.get("libro_codigo") or row.get("book_code") or "").strip()
            instance_code = str(row.get("codigo_instancia") or row.get("instance_code") or row.get("title") or "").strip()
            if not book_code or not instance_code:
                continue
            key = (book_code.casefold(), instance_code.casefold())
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "libro_codigo": book_code,
                    "codigo_instancia": instance_code,
                    "session_path": str(row.get("session_path") or "").strip(),
                    "title": str(row.get("title") or instance_code).strip(),
                }
            )
        return result

    def _build_combined_instances_source_text(self, sections: list[dict[str, Any]], *, title: str = "") -> str:
        blocks: list[str] = []
        main_title = str(title or "").strip()
        if main_title:
            blocks.append(rf"\section*{{{self._latex_escape_heading(main_title)}}}")
        for section in sections:
            section_title = str(section.get("title") or section.get("codigo_instancia") or "Instancia").strip()
            source = str(section.get("source_text") or "").strip()
            if not source:
                continue
            blocks.append(rf"\subsection*{{{self._latex_escape_heading(section_title)}}}")
            blocks.append(self._ensure_clean_enumerate_block(source))
        return "\n\n".join(blocks).strip()

    def _build_scan_source_text_from_db(
        self,
        problems: list[dict[str, Any]],
        *,
        structure: str = "",
        title: str = "",
    ) -> str:
        blocks: list[tuple[int, str]] = []
        for idx, problem in enumerate(problems, start=1):
            raw = self._build_db_problem_text(problem).strip()
            if not raw:
                continue
            clave = str(problem.get("respuesta_correcta") or "").strip().upper()
            if clave and not CLAVE_TAG_RE.search(raw):
                raw = f"{raw} [[clave={clave}]]"
            estado = self._normalize_state_tag(str(problem.get("consistencia_matematica") or "").strip())
            if estado and not ESTADO_TAG_RE.search(raw):
                raw = f"{raw} [[Estado={estado}]]"
            blocks.append((self._extract_tex_item_number(raw, idx), raw))
        if not blocks:
            return ""
        return "\n".join(self._apply_practice_structure_to_blocks(blocks, structure=structure, title=title)).strip()

    def _build_db_problem_text(self, problem: dict[str, Any]) -> str:
        body = self._normalize_db_text_separators(str(problem.get("enunciado_latex") or "").strip())
        if not body:
            return ""
        metadata_tags = self._collect_db_display_tags(problem)
        marker_names = self._db_problem_markers(problem)
        prefix_match = re.match(
            r"""^\s*(\\item\s*\[\s*\\textbf\{\s*\d+\.?\s*\}\s*\]\s*)""",
            body,
            flags=re.IGNORECASE,
        )
        prefix = ""
        remainder = body
        if prefix_match:
            prefix = prefix_match.group(1)
            remainder = body[prefix_match.end() :].lstrip()
        remainder = IMAGE_MARKER_RE.sub(" ", remainder)
        remainder = self._strip_generic_db_tags(remainder)
        remainder = re.sub(r"\s+(£|æ)", r"\1", remainder)
        remainder = re.sub(r"(£|æ)\s+", r"\1", remainder)
        remainder = re.sub(r"[ \t]{2,}", " ", remainder).strip()
        remainder = self._inject_db_markers_before_options(remainder, marker_names)
        if not metadata_tags:
            return f"{prefix}{remainder}".strip()
        if prefix:
            return f"{prefix}{' '.join(metadata_tags)} {remainder}".strip()
        return f"{' '.join(metadata_tags)} {remainder}".strip()

    def _normalize_db_text_separators(self, text: str) -> str:
        normalized = self._repair_db_text_mojibake(str(text or ""))
        replacements = {
            "Ã‚Â£": "\u00a3",
            "ÃƒÂ¦": "\u00e6",
            "Â£": "\u00a3",
            "Ã¦": "\u00e6",
            "\u00a0": " ",
        }
        for source, target in replacements.items():
            normalized = normalized.replace(source, target)
        return normalized

    def _repair_db_text_mojibake(self, text: str) -> str:
        value = str(text or "")
        markers = ("Ã", "Â")
        if not any(marker in value for marker in markers):
            return value
        try:
            repaired = value.encode("latin-1", errors="strict").decode("utf-8", errors="strict")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return value
        current_score = sum(value.count(marker) for marker in markers)
        repaired_score = sum(repaired.count(marker) for marker in markers)
        return repaired if repaired_score < current_score else value

    def _strip_generic_db_tags(self, text: str) -> str:
        cleaned = str(text or "")
        for pattern in (
            r"\[\[\s*curso\s*=[^\]\r\n]+?\]\]",
            r"\[\[\s*tema\s*=[^\]\r\n]+?\]\]",
            r"\[\[\s*subtema\s*=[^\]\r\n]+?\]\]",
            r"\[\[\s*examen\s*=[^\]\r\n]+?\]\]",
        ):
            cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
        return cleaned

    def _collect_db_display_tags(self, problem: dict[str, Any]) -> list[str]:
        tags: list[str] = []
        seen: set[str] = set()

        def add(name: str, value: Any) -> None:
            clean = str(value or "").strip()
            if not clean:
                return
            tag = f"[[{name}={clean}]]"
            key = tag.lower()
            if key not in seen:
                seen.add(key)
                tags.append(tag)

        add("curso", problem.get("curso"))
        add("tema", problem.get("tema"))
        add("subtema", problem.get("subtema"))
        add("examen", problem.get("examen"))
        return tags

    def _inject_db_markers_before_options(self, text: str, marker_names: list[str]) -> str:
        clean_text = str(text or "").strip()
        marker_block = " ".join(f"[[Imagen={marker}]]" for marker in marker_names if str(marker or "").strip())
        if not marker_block:
            return clean_text
        option_match = re.search(r"(?=(?:£|æ|\r?\n|^)\s*[A-E]\))", clean_text)
        if option_match:
            left = clean_text[: option_match.start()].rstrip()
            right = clean_text[option_match.start() :]
            return f"{left} {marker_block} {right.lstrip()}".strip() if left else f"{marker_block} {right.lstrip()}".strip()
        return f"{clean_text} {marker_block}".strip()

    def _db_problem_markers(self, problem: dict[str, Any]) -> list[str]:
        structured = self._resolve_db_structured_images(problem)
        if structured:
            return [marker for marker, _path in structured]
        return [safe for safe, _raw in self._db_problem_marker_aliases(problem)]

    def _db_problem_marker_aliases(self, problem: dict[str, Any]) -> list[tuple[str, str]]:
        raw = self._normalize_db_text_separators(str(problem.get("enunciado_latex") or "").strip())
        markers: list[tuple[str, str]] = []
        seen: set[str] = set()
        for match in IMAGE_MARKER_RE.finditer(raw):
            marker = str(match.group(1) or "").strip()
            key = marker.lower()
            if marker and key not in seen:
                seen.add(key)
                markers.append((self._safe_db_marker_name(problem, marker), marker))
        return markers

    def _safe_db_marker_name(self, problem: dict[str, Any], marker: str) -> str:
        clean = self._safe_filename(str(marker or "").strip(), fallback="img", limit=72)
        original = str(marker or "").strip()
        if clean == original and len(clean) <= 80:
            return clean
        digest = hashlib.sha1(original.encode("utf-8", errors="ignore")).hexdigest()[:10]
        return self._safe_filename(f"{self._db_marker_prefix(problem)}_img_{digest}", fallback=f"img_{digest}", limit=48)

    def _structured_db_image_paths(self, problem: dict[str, Any]) -> list[str]:
        raw_images = problem.get("imagenes", [])
        if not isinstance(raw_images, (list, tuple)):
            return []
        paths: list[str] = []
        seen: set[str] = set()
        for raw in raw_images:
            value = str(raw or "").strip()
            key = value.lower()
            if value and key not in seen:
                seen.add(key)
                paths.append(value)
        return paths

    def _db_marker_prefix(self, problem: dict[str, Any]) -> str:
        for field in ("id", "problema_id"):
            try:
                value = int(problem.get(field) or 0)
            except Exception:
                value = 0
            if value > 0:
                return f"p{value}"
        try:
            number = int(problem.get("numero_original") or 0)
        except Exception:
            number = 0
        if number > 0:
            return f"n{number}"
        seed = json.dumps(problem, ensure_ascii=False, sort_keys=True, default=str)
        return f"p{hashlib.sha1(seed.encode('utf-8', errors='ignore')).hexdigest()[:10]}"

    def _db_structured_marker_name(self, problem: dict[str, Any], raw_path: str, counts: dict[str, int]) -> str:
        raw_marker = Path(str(raw_path or "")).stem.strip() or "img"
        raw_base = f"{self._db_marker_prefix(problem)}_{raw_marker}"
        clean_base = self._safe_filename(raw_base, fallback="img", limit=92)
        if clean_base == raw_base and len(clean_base) <= 80:
            base = clean_base
        else:
            digest = hashlib.sha1(raw_base.encode("utf-8", errors="ignore")).hexdigest()[:10]
            base = self._safe_filename(f"{self._db_marker_prefix(problem)}_img_{digest}", fallback="img", limit=48)
        key = base.lower()
        counts[key] = counts.get(key, 0) + 1
        return self._safe_filename(f"{base}_{counts[key]}", fallback="img", limit=92) if counts[key] > 1 else base

    def _resolve_db_structured_images(self, problem: dict[str, Any]) -> list[tuple[str, Path]]:
        entries: list[tuple[str, Path]] = []
        counts: dict[str, int] = {}
        candidate_dirs = self._iter_db_image_dirs(problem)
        ruta = str(problem.get("ruta_carpeta") or "").strip()
        if ruta:
            candidate_dirs.insert(0, self._normalize_path(Path(ruta)))
        for raw_path in self._structured_db_image_paths(problem):
            resolved = self._resolve_db_image_path(raw_path, candidate_dirs)
            if resolved is not None:
                entries.append((self._db_structured_marker_name(problem, raw_path, counts), resolved))
        return entries

    def _iter_db_image_dirs(self, problem: dict[str, Any]) -> list[Path]:
        dirs: list[Path] = []
        seen: set[str] = set()

        def add(path: Path | None) -> None:
            if path is None:
                return
            normalized = self._normalize_path(path)
            key = str(normalized).lower()
            if key in seen:
                return
            seen.add(key)
            dirs.append(normalized)

        for field in ("ruta_carpeta", "archivo_origen", "pdf_path"):
            raw = str(problem.get(field) or "").strip()
            if not raw:
                continue
            path = self._normalize_path(Path(raw))
            add(path if not path.suffix else path.parent)
            add((path if not path.suffix else path.parent) / "crops")
            add((path if not path.suffix else path.parent) / "segments")
        return dirs

    def _resolve_db_image_path(self, raw_path: str, candidate_dirs: list[Path]) -> Path | None:
        value = str(raw_path or "").strip()
        if not value:
            return None
        candidate = self._normalize_path(Path(value))
        try:
            if candidate.is_absolute() and candidate.exists() and candidate.is_file():
                return candidate
        except Exception:
            pass
        name = Path(value).name.strip()
        for directory in candidate_dirs:
            for current in (directory / value, directory / name):
                try:
                    resolved = self._normalize_path(current)
                    if resolved.exists() and resolved.is_file():
                        return resolved
                except Exception:
                    continue
        return None

    def _resolve_db_marker_paths(self, marker_name: str, problem: dict[str, Any]) -> list[Path]:
        marker = str(marker_name or "").strip()
        if not marker:
            return []
        lookup_markers = [marker]
        for safe_marker, raw_marker in self._db_problem_marker_aliases(problem):
            if safe_marker.lower() == marker.lower() and raw_marker.lower() != marker.lower():
                lookup_markers.append(raw_marker)
        for structured_marker, structured_path in self._resolve_db_structured_images(problem):
            if structured_marker.lower() == marker.lower():
                return [structured_path]
        suffixes = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
        matches: list[Path] = []
        seen: set[str] = set()
        for directory in self._iter_db_image_dirs(problem):
            try:
                if not directory.exists() or not directory.is_dir():
                    continue
            except Exception:
                continue
            for suffix in suffixes:
                for lookup_marker in lookup_markers:
                    candidate = directory / f"{lookup_marker}{suffix}"
                    key = str(candidate).lower()
                    try:
                        if key not in seen and candidate.exists() and candidate.is_file():
                            seen.add(key)
                            matches.append(self._normalize_path(candidate))
                    except Exception:
                        continue
            if matches:
                return matches
        return matches

    def prepare_images_dir_for_db(
        self,
        problems: list[dict[str, Any]],
        *,
        output_docx: Path,
        source_text: str | None = None,
    ) -> Path | None:
        if not problems:
            return None
        if source_text is not None and not IMAGE_MARKER_RE.search(str(source_text or "")):
            return None
        images_dir = output_docx.with_name(f"{output_docx.stem}__db_images")
        images_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for problem in problems:
            for marker in self._db_problem_markers(problem):
                if self._marker_output_exists(images_dir, marker):
                    continue
                paths = self._resolve_db_marker_paths(marker, problem)
                if not paths:
                    self.log(f"BD Word: no se resolvio imagen {marker} para problema {problem.get('id')}")
                    placeholder = self._write_missing_db_image_placeholder(images_dir, marker)
                    if placeholder is not None:
                        copied += 1
                    continue
                source = paths[0]
                target = images_dir / f"{marker}{source.suffix or '.png'}"
                try:
                    if source.resolve() == target.resolve():
                        continue
                except Exception:
                    pass
                self._materialize_file_fast(source, target)
                copied += 1
        if copied > 0:
            self.log(f"BD Word: se materializaron {copied} imagen(es) en {images_dir}")
        return images_dir if any(images_dir.iterdir()) else None

    def _write_missing_db_image_placeholder(self, images_dir: Path, marker: str) -> Path | None:
        clean_marker = str(marker or "").strip()
        if not clean_marker:
            return None
        target = images_dir / f"{clean_marker}.png"
        if target.exists():
            return target
        try:
            target.write_bytes(TRANSPARENT_PNG_1X1)
            self.log(f"BD Word: placeholder creado para imagen faltante {clean_marker}: {target}")
            return target
        except OSError as exc:
            self.log(f"BD Word: no se pudo crear placeholder para {clean_marker}: {exc}")
            return None

    def _materialize_file_fast(self, source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if target.exists():
                try:
                    if target.stat().st_size == source.stat().st_size:
                        return
                except OSError:
                    return
                target.unlink()
        except OSError:
            pass
        try:
            os.link(str(source), str(target))
            return
        except OSError:
            shutil.copy2(str(source), str(target))


    def _normalize_state_tag(self, value: str) -> str:
        state = str(value or "").strip().lower()
        if not state:
            return "sin_revisar"
        replacements = {
            "sin revisar": "sin_revisar",
            "pendiente revision": "sin_revisar",
            "pendiente revision": "sin_revisar",
            "bien planteado": "consistente",
            "bien_planteado": "consistente",
            "mal planteado": "inconsistente",
            "mal_planteado": "inconsistente",
            "ambiguo": "inconsistente",
            "ambigua": "inconsistente",
        }
        return replacements.get(state, state.replace(" ", "_"))

    def _latex_escape_heading(self, value: str) -> str:
        replacements = {
            "\\": r"\textbackslash{}",
            "&": r"\&",
            "%": r"\%",
            "$": r"\$",
            "#": r"\#",
            "_": r"\_",
            "{": r"\{",
            "}": r"\}",
        }
        return "".join(replacements.get(ch, ch) for ch in str(value or "").strip())

    def _parse_practice_ranges(self, value: str) -> set[int]:
        numbers: set[int] = set()
        for part in re.split(r"[,;]", str(value or "")):
            chunk = part.strip()
            if not chunk:
                continue
            match_range = re.match(r"^(\d+)\s*[-\u2013]\s*(\d+)$", chunk)
            if match_range:
                start = int(match_range.group(1))
                end = int(match_range.group(2))
                if start > end:
                    start, end = end, start
                numbers.update(range(start, end + 1))
                continue
            if re.match(r"^\d+$", chunk):
                numbers.add(int(chunk))
        return numbers

    def _parse_practice_structure(self, structure: str = "", title: str = "") -> tuple[str, dict[int, list[str]]]:
        heading = str(title or "").strip()
        subtitles_by_number: dict[int, list[str]] = {}
        for raw_line in str(structure or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("##"):
                content = line[2:].strip()
                subtitle, sep, ranges = content.partition(":")
                if not sep or not subtitle.strip():
                    continue
                for number in self._parse_practice_ranges(ranges):
                    subtitles_by_number.setdefault(number, []).append(subtitle.strip())
            elif line.startswith("#") and not heading:
                heading = line[1:].strip()
        return heading, subtitles_by_number

    def _extract_tex_item_number(self, item_text: str, fallback: int) -> int:
        match = re.search(r"\\item\s*\[\s*\\textbf\{\s*(\d+)\.?\s*\}", str(item_text or ""))
        if not match:
            return int(fallback)
        try:
            return int(match.group(1))
        except Exception:
            return int(fallback)

    def _apply_practice_structure_to_blocks(
        self,
        blocks: list[tuple[int, str]],
        *,
        structure: str = "",
        title: str = "",
    ) -> list[str]:
        heading, subtitles_by_number = self._parse_practice_structure(structure, title)
        out: list[str] = []
        if heading:
            out.append(rf"\section*{{{self._latex_escape_heading(heading)}}}")
        emitted: set[tuple[int, str]] = set()
        for number, block in blocks:
            for subtitle in subtitles_by_number.get(number, []):
                key = (number, subtitle)
                if key in emitted:
                    continue
                emitted.add(key)
                out.append(rf"\subsection*{{{self._latex_escape_heading(subtitle)}}}")
            out.append(block)
        return out

    def _build_session_job(
        self,
        *,
        session_path: str,
        output_docx: str,
        repo: str,
        python: str,
        template: str,
        style: str,
    ) -> SessionWordJob:
        session = self._normalize_path(Path(str(session_path or "").strip()))
        if not session.exists() or not session.is_file():
            raise FileNotFoundError(f"Archivo de sesion no encontrado: {session}")
        output = self._normalize_output_docx_path(output_docx or str(self.session_word_path_for(session) or session.with_suffix(".docx")))
        repo_path = self._normalize_path(Path(repo).expanduser()) if str(repo or "").strip() else self.default_editor_repo()
        script = repo_path / "latex_to_word.py"
        if not script.exists():
            raise FileNotFoundError(f"No existe script de conversion: {script}")
        py, errors = self.resolve_python(repo_path, python)
        for error in errors[:4]:
            self.log(f"Python candidato descartado: {error}")
        template_path = self._normalize_path(Path(template).expanduser()) if str(template or "").strip() else self.default_template(repo_path)
        if template_path is not None and not template_path.exists():
            template_path = None
        return SessionWordJob(
            session_path=session,
            output_docx=output,
            repo=repo_path,
            python=py,
            template=template_path,
            style=str(style or "Estilo_plantilla").strip() or "Estilo_plantilla",
        )

    def resolve_session_input_tex(self, *, session_path: Path, output_docx: Path) -> Path:
        payload = self._load_session_payload(session_path)
        source_text = self._build_scan_source_text_from_session(payload)
        if not source_text:
            raise ValueError(
                "La instancia tiene sesion, pero todavia no tiene formato final exportable. "
                "Primero termina Revision/BD final o guarda la salida LaTeX final."
            )
        generated = output_docx.with_name(f"{output_docx.with_suffix('').name}__session_source.tex")
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text(self._ensure_enumerate_wrapper(source_text) + "\n", encoding="utf-8")
        return generated

    def prepare_images_dir_for_session(self, session_path: Path, *, output_docx: Path | None = None) -> Path | None:
        payload = self._load_session_payload(session_path)
        ui = payload.get("ui", {}) if isinstance(payload, dict) else {}
        instance_type = str(ui.get("instance_type", payload.get("instance_type", "") if isinstance(payload, dict) else "") or "").strip().lower()
        images_dir = output_docx.with_name(f"{output_docx.stem}__session_images") if output_docx is not None else None
        if images_dir is None:
            images_dir = self._infer_images_dir_from_session(session_path, payload=payload, instance_type=instance_type)
        if images_dir is None:
            return None
        if self._session_images_dir_complete(payload=payload, images_dir=images_dir):
            return images_dir
        self._materialize_session_marker_images(
            session_path=session_path,
            payload=payload,
            images_dir=images_dir,
            instance_type=instance_type,
        )
        return images_dir

    def run_tex_to_word(self, *, job: SessionWordJob, input_tex: Path, images_dir: Path | None) -> Path:
        job.output_docx.parent.mkdir(parents=True, exist_ok=True)
        script = job.repo / "latex_to_word.py"
        cmd = [str(job.python), str(script), str(input_tex), str(job.output_docx), "--style", job.style]
        if job.template and job.template.exists():
            cmd.extend(["--template", str(job.template)])
        if images_dir and images_dir.exists():
            cmd.extend(["--images-dir", str(images_dir)])
        timeout_seconds = self._tex_to_word_timeout_seconds(input_tex)
        self.log(f"Modulo 7: timeout conversion Word={timeout_seconds}s para {input_tex.name}")
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(job.repo),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            recovered = self._recover_pandoc_intermediate_docx(job.output_docx)
            if recovered is not None:
                self.log(f"Word recuperado desde Pandoc intermedio tras timeout: {recovered}")
                return recovered
            raise RuntimeError(f"No se pudo generar Word: timeout ejecutando latex_to_word.py ({exc.timeout}s)") from exc
        if proc.returncode != 0:
            recovered = self._recover_pandoc_intermediate_docx(job.output_docx)
            if recovered is not None:
                detail = (proc.stderr or proc.stdout or f"exit={proc.returncode}").strip()
                self.log(f"Word recuperado desde Pandoc intermedio tras fallo del postproceso: {recovered}. Detalle: {detail[:500]}")
                return recovered
            detail = (proc.stderr or proc.stdout or f"exit={proc.returncode}").strip()
            raise RuntimeError(f"No se pudo generar Word: {detail}")
        produced = self._extract_generated_docx_from_stdout(proc.stdout) or job.output_docx
        if not produced.exists():
            recovered = self._recover_pandoc_intermediate_docx(job.output_docx)
            if recovered is not None:
                self.log(f"Word recuperado desde Pandoc intermedio porque no aparecio salida final: {recovered}")
                return recovered
            raise RuntimeError(f"La conversion termino sin crear el Word: {produced}")
        return produced

    def _tex_to_word_timeout_seconds(self, input_tex: Path) -> int:
        try:
            text = input_tex.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return 300
        item_count = len(re.findall(r"\\item\s*\[", text))
        if item_count <= 120:
            return 300
        if item_count <= 350:
            return 900
        return 1800

    def _recover_pandoc_intermediate_docx(self, output_docx: Path) -> Path | None:
        output = self._normalize_path(output_docx)
        intermediate = output.with_name(f"{output.stem}__pandoc_intermedio.docx")
        if not intermediate.exists():
            return None
        try:
            if intermediate.stat().st_size <= 0:
                return None
        except OSError:
            return None
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(intermediate, output)
        except OSError:
            return intermediate
        return output if output.exists() else intermediate

    def resolve_python(self, repo: Path, preferred: str = "") -> tuple[str, list[str]]:
        errors: list[str] = []
        for candidate in self._python_candidates(repo, preferred):
            is_path_like = ("\\" in candidate) or ("/" in candidate) or candidate.lower().endswith(".exe")
            if is_path_like and not Path(candidate).exists():
                errors.append(f"{candidate}: no existe")
                continue
            ok, msg = self._probe_python(candidate)
            if ok:
                return candidate, errors
            errors.append(f"{candidate}: {msg}")
        return str(Path(sys.executable)), errors

    def _python_candidates(self, repo: Path, preferred: str = "") -> list[str]:
        out: list[str] = []
        seen: set[str] = set()

        def add(value: str) -> None:
            v = str(value or "").strip()
            if not v or v.lower() in seen:
                return
            seen.add(v.lower())
            out.append(v)

        add(preferred)
        add(str(repo / ".venv" / "Scripts" / "python.exe"))
        add(str(repo / "venv" / "Scripts" / "python.exe"))
        add(str(Path.cwd() / ".venv" / "Scripts" / "python.exe"))
        add(str(Path.cwd() / "venv" / "Scripts" / "python.exe"))
        add(str(Path(sys.executable)))
        add("python")
        return out

    def _probe_python(self, exe: str) -> tuple[bool, str]:
        try:
            proc = subprocess.run(
                [str(exe), "-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
            )
        except Exception as exc:
            return False, str(exc)
        if proc.returncode != 0:
            return False, (proc.stderr or proc.stdout or f"exit={proc.returncode}").strip()
        return True, (proc.stdout or "").strip() or str(exe)

    def _resolve_library_instance_session_path(self, book: dict[str, Any], instance: dict[str, Any]) -> Path | None:
        raw_session = str(instance.get("session_path") or "").strip()
        session_lower = raw_session.lower()
        looks_cache_path = "\\.cache\\transcriptor_runs\\sessions" in session_lower
        workspace_raw = str(book.get("workspace_dir") or "").strip()
        tipo = normalize_instance_name(str(instance.get("tipo") or "").strip(), "sesion")
        inferred: Path | None = None
        if workspace_raw and tipo:
            try:
                inferred = project_dirs(Path(workspace_raw), tipo).get("session_path")
            except Exception:
                inferred = None
        if raw_session and not looks_cache_path:
            try:
                normalized = self._normalize_path(Path(raw_session))
                if normalized.exists():
                    return normalized
                if inferred is not None:
                    normalized_inferred = self._normalize_path(inferred)
                    if normalized_inferred.exists():
                        return normalized_inferred
                return None
            except Exception:
                pass
        if inferred is not None:
            normalized_inferred = self._normalize_path(inferred)
            return normalized_inferred if normalized_inferred.exists() else None
        return None

    def _build_session_instance_label(self, session_path: Path) -> str:
        label = session_path.stem
        try:
            payload = self._load_session_payload(session_path)
        except Exception:
            payload = {}
        ui = payload.get("ui", {}) if isinstance(payload, dict) else {}
        project_name = str(ui.get("project_name", "") or "").strip()
        instance_type = str(ui.get("instance_type", payload.get("instance_type", "") if isinstance(payload, dict) else "") or "").strip()
        parts = [label]
        if project_name and project_name.casefold() != label.casefold():
            parts.append(project_name)
        if instance_type:
            parts.append(instance_type)
        return " | ".join(parts)

    def _load_session_payload(self, session_path: Path) -> dict[str, Any]:
        try:
            return json.loads(session_path.read_text(encoding="utf-8"))
        except UnicodeError:
            return json.loads(session_path.read_text(encoding="utf-8-sig"))

    def _build_scan_source_text_from_session(self, payload: dict[str, Any]) -> str:
        output_text = str(payload.get("output_text", "") or "").strip() if isinstance(payload, dict) else ""
        if output_text:
            return output_text
        blocks: list[str] = []
        item_sources = []
        if isinstance(payload, dict):
            item_sources.extend(
                row for row in (
                    payload.get("items", []),
                    payload.get("problems", []),
                    payload.get("final_items", []),
                    payload.get("latex_items", []),
                )
                if isinstance(row, list)
            )
        for items_data in item_sources:
            for row in items_data:
                if not isinstance(row, dict):
                    continue
                for key in ("item", "item_text", "final_text", "text", "latex", "enunciado_latex"):
                    candidate = str(row.get(key, "") or "").strip()
                    if candidate:
                        blocks.append(candidate)
                        break
        return "\n".join(blocks).strip()

    def _session_exportable_info(self, session_path: Path | None) -> dict[str, Any]:
        if session_path is None:
            return {"exportable": False, "items": 0, "reason": "sin sesion"}
        try:
            payload = self._load_session_payload(session_path)
        except Exception as exc:
            return {"exportable": False, "items": 0, "reason": f"sesion ilegible: {exc}"}
        text = self._build_scan_source_text_from_session(payload)
        if not text:
            return {"exportable": False, "items": 0, "reason": "sin formato final"}
        count = len(re.findall(r"\\item\b", text)) or max(1, len([line for line in text.splitlines() if line.strip()]))
        return {"exportable": True, "items": count, "reason": "lista para Word"}

    def _ensure_enumerate_wrapper(self, source_text: str) -> str:
        text = str(source_text or "").strip()
        if not text:
            return "\\begin{enumerate}\n\\end{enumerate}"
        low = text.lower()
        if "\\begin{enumerate}" in low and "\\end{enumerate}" in low:
            return text
        return "\\begin{enumerate}\n" + text + "\n\\end{enumerate}"

    def _ensure_clean_enumerate_block(self, source_text: str) -> str:
        text = str(source_text or "").strip()
        text = re.sub(r"\\(?:begin|end)\s*\{\s*enumerate\s*\}", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return "\\begin{enumerate}\n" + text + "\n\\end{enumerate}"

    def session_word_candidates(self, session_path: Path | None) -> list[Path]:
        if session_path is None:
            return []
        candidates: list[Path] = []

        def add(path: Path) -> None:
            normalized = self._normalize_path(path)
            if normalized not in candidates:
                candidates.append(normalized)

        add(session_path.with_suffix(".docx"))
        name = session_path.name
        if name.endswith(".session.json"):
            add(session_path.with_name(name.removesuffix(".session.json") + ".docx"))
        if session_path.stem.endswith(".session"):
            add(session_path.with_name(session_path.stem.removesuffix(".session") + ".docx"))
        return candidates

    def session_word_path_for(self, session_path: Path | None) -> Path | None:
        candidates = self.session_word_candidates(session_path)
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0] if candidates else None

    def library_instance_word_candidates(
        self,
        *,
        book: dict[str, Any],
        instance: dict[str, Any],
        label: str,
        session_path: Path | None,
    ) -> list[Path]:
        candidates: list[Path] = []

        def add(path: Path | None) -> None:
            if path is None:
                return
            normalized = self._normalize_path(path)
            if normalized not in candidates:
                candidates.append(normalized)

        book_code = str(book.get("codigo") or book.get("code") or "").strip()
        instance_code = str(label or instance.get("tipo") or instance.get("name") or instance.get("label") or "").strip()
        safe = self._safe_filename(f"{book_code}__{instance_code}", fallback="instancia")
        for raw_pdf in (
            str(book.get("pdf_path") or book.get("pdfPath") or "").strip(),
            str(instance.get("pdf_path") or instance.get("pdfPath") or "").strip(),
        ):
            output_dir = self._word_output_dir_for_pdf(raw_pdf)
            if output_dir is not None:
                add(output_dir / f"{safe}.docx")
                break

        workspace_raw = str(book.get("workspace_dir") or "").strip()
        if workspace_raw:
            try:
                workspace = self._normalize_path(Path(workspace_raw).expanduser())
                add(workspace / "Word" / f"{safe}.docx")
            except Exception:
                pass

        for candidate in self.session_word_candidates(session_path):
            add(candidate)
        return candidates

    def library_instance_word_path_for(
        self,
        *,
        book: dict[str, Any],
        instance: dict[str, Any],
        label: str,
        session_path: Path | None,
    ) -> Path | None:
        candidates = self.library_instance_word_candidates(book=book, instance=instance, label=label, session_path=session_path)
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0] if candidates else None

    def _infer_images_dir_from_session(self, session_path: Path, *, payload: dict[str, Any], instance_type: str = "") -> Path | None:
        def resolve_candidate(raw_value: str) -> Path | None:
            value = str(raw_value or "").strip()
            if not value:
                return None
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = session_path.parent / candidate
            return self._normalize_path(candidate)

        session_bundle = payload.get("session_bundle", {}) if isinstance(payload, dict) else {}
        if isinstance(session_bundle, dict):
            bundle_crops = resolve_candidate(str(session_bundle.get("crops_dir", "") or "").strip())
            if bundle_crops is not None:
                return bundle_crops
        segmentation = payload.get("segmentation", {}) if isinstance(payload, dict) else {}
        if isinstance(segmentation, dict):
            raw_crops_dir = resolve_candidate(str(segmentation.get("crops_dir", "") or "").strip())
            if raw_crops_dir is not None:
                return raw_crops_dir
        project_root = infer_workspace_from_session_path(session_path)
        if project_root is None:
            return None
        normalized_instance = normalize_instance_name(instance_type or session_path.stem, "sesion")
        return project_dirs(project_root, normalized_instance)["crops_dir"]

    def _resolve_session_resource_path(self, session_path: Path, raw_path: str) -> Path:
        value = str(raw_path or "").strip()
        if not value:
            return Path(value)
        candidate = Path(value)
        if candidate.is_absolute():
            return self._normalize_path(candidate)
        candidates = [self._normalize_path(session_path.parent / candidate)]
        project_root = infer_workspace_from_session_path(session_path)
        if project_root is not None:
            normalized = value.replace("\\", "/")
            if normalized.startswith("./"):
                normalized = normalized[2:]
            candidates.append(self._normalize_path(project_root / normalized))
            candidates.append(self._normalize_path(project_root.parent / normalized))
        for resolved in candidates:
            try:
                if resolved.exists():
                    return self._normalize_path(resolved)
            except Exception:
                continue
        return candidates[0]

    def _extract_session_item_markers(self, item_text: str) -> list[str]:
        return [str(match.group(1) or "").strip() for match in IMAGE_MARKER_RE.finditer(str(item_text or "")) if str(match.group(1) or "").strip()]

    def _collect_session_marker_names(self, payload: dict[str, Any]) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        preview_map = payload.get("preview_images", {}) if isinstance(payload, dict) else {}
        if isinstance(preview_map, dict):
            for marker_name in preview_map:
                marker = str(marker_name or "").strip()
                if marker and marker not in seen:
                    seen.add(marker)
                    names.append(marker)
        items = payload.get("items", []) if isinstance(payload, dict) else []
        if isinstance(items, list):
            for row in items:
                if not isinstance(row, dict):
                    continue
                for marker in self._extract_session_item_markers(str(row.get("item", "") or "")):
                    if marker and marker not in seen:
                        seen.add(marker)
                        names.append(marker)
        return names

    def _marker_output_exists(self, images_dir: Path, marker_name: str) -> bool:
        marker = str(marker_name or "").strip()
        if not marker:
            return False
        for suffix in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            candidate = images_dir / f"{marker}{suffix}"
            try:
                if candidate.exists() and candidate.is_file():
                    return True
            except Exception:
                continue
        return False

    def _session_images_dir_complete(self, *, payload: dict[str, Any], images_dir: Path) -> bool:
        marker_names = self._collect_session_marker_names(payload)
        if not marker_names:
            return True
        if not images_dir.exists() or not images_dir.is_dir():
            return False
        return all(self._marker_output_exists(images_dir, marker) for marker in marker_names)

    def _materialize_session_marker_images(
        self,
        *,
        session_path: Path,
        payload: dict[str, Any],
        images_dir: Path,
        instance_type: str,
    ) -> int:
        del instance_type
        images_dir.mkdir(parents=True, exist_ok=True)
        copied = 0

        def copy_marker(marker_name: str, raw_path: str) -> None:
            nonlocal copied
            marker = str(marker_name or "").strip()
            raw = str(raw_path or "").strip()
            if not marker or not raw or self._marker_output_exists(images_dir, marker):
                return
            source = self._resolve_session_resource_path(session_path, raw)
            if not source.exists() or not source.is_file():
                return
            target = images_dir / f"{marker}{source.suffix or '.png'}"
            try:
                if source.resolve() == target.resolve():
                    return
            except Exception:
                pass
            self._materialize_file_fast(source, target)
            copied += 1

        preview_map = payload.get("preview_images", {}) if isinstance(payload, dict) else {}
        if isinstance(preview_map, dict):
            for marker_name, raw_path in preview_map.items():
                copy_marker(str(marker_name), str(raw_path))
        items = payload.get("items", []) if isinstance(payload, dict) else []
        if isinstance(items, list):
            for row in items:
                if not isinstance(row, dict):
                    continue
                markers = self._extract_session_item_markers(str(row.get("item", "") or ""))
                imgs = row.get("imagenes", [])
                if markers and isinstance(imgs, list):
                    for marker_name, img_path in zip(markers, imgs):
                        copy_marker(marker_name, str(img_path or ""))
        return copied

    def _extract_generated_docx_from_stdout(self, stdout_text: str) -> Path | None:
        for raw_line in reversed(str(stdout_text or "").splitlines()):
            line = raw_line.strip()
            if "Word generado en:" not in line:
                continue
            _, _, candidate = line.partition("Word generado en:")
            path = self._normalize_path(Path(candidate.strip().strip('"')))
            if path.exists():
                return path
        return None

    def _normalize_output_docx_path(self, raw_value: str) -> Path:
        value = str(raw_value or "").strip()
        path = Path(value).expanduser() if value else Path.cwd() / "salida.docx"
        if not path.is_absolute():
            path = Path.cwd() / path
        path = self._normalize_path(path)
        if path.suffix:
            path = path.with_suffix(".docx")
        elif not path.name.lower().endswith(".docx"):
            path = path.with_name(f"{path.name}.docx")
        return self._normalize_path(path)

    def _normalize_existing_text_path(self, value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        return str(self._normalize_path(Path(raw).expanduser()))

    def _normalize_path(self, path: Path) -> Path:
        try:
            normalized = Path(os.path.normpath(str(path)))
        except Exception:
            normalized = path
        try:
            return remap_legacy_drive_path(normalized, prefer_existing=True)
        except Exception:
            return normalized

    def _stable_key(self, *parts: str) -> str:
        raw = "|".join(str(part or "") for part in parts)
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]

    def _session_instance_sort_key(self, value: str) -> tuple[int, str]:
        text = str(value or "").strip().lower()
        match = re.search(r"\bs(?:emana)?[_\s-]*n?[_\s-]*(\d+)|\bs(\d+)", text)
        if match:
            number = match.group(1) or match.group(2)
            return int(number), text
        return 10**9, text

    def search_match(self, query: str, *values: str) -> bool:
        needle = self._normalize_search(query)
        if not needle:
            return True
        haystack = self._normalize_search(" ".join(values))
        if needle in haystack:
            return True
        tokens = [token for token in needle.split() if token]
        hay_tokens = [token for token in haystack.split() if token]
        return all(self._token_matches_any(token, hay_tokens) for token in tokens)

    def _token_matches_any(self, needle: str, hay_tokens: list[str]) -> bool:
        threshold = 0.82 if len(needle) <= 6 else 0.74
        return any(needle in token or token in needle or SequenceMatcher(None, needle, token).ratio() >= threshold for token in hay_tokens)

    def _normalize_search(self, value: str) -> str:
        import unicodedata

        text = unicodedata.normalize("NFD", str(value or "").strip().lower())
        text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
        text = re.sub(r"[_\\/\-.,;:()\[\]{}]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()
