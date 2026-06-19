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
from pathlib import Path
from typing import Any, Callable

from utils.project_layout import infer_workspace_from_session_path, normalize_instance_name, project_dirs, remap_legacy_drive_path


IMAGE_MARKER_RE = re.compile(r"\[\[\s*Imagen\s*=\s*([^\]\r\n]+?)\s*\]\]", re.IGNORECASE)
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
        file_url_resolver: Callable[[str], str] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.controller = controller
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
        for book in rows:
            book_id = int(book.get("id") or 0)
            if book_id <= 0:
                continue
            instances: list[dict[str, Any]] = []
            for instance in self.controller.listar_instancias_libro(db_name, book_id):
                session_path = self._resolve_library_instance_session_path(book, instance)
                instances.append(self._session_instance_payload(book, instance, session_path=session_path))
            if instances:
                books.append(self._book_payload(book, instances))
        return self._catalog_payload(books, source="library", db_name=db_name, root="")

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
        return {
            "book_key": self._stable_key(code, title, str(book.get("id") or "")),
            "id": int(book.get("id") or 0),
            "code": code,
            "title": title,
            "author": str(book.get("autor") or book.get("author") or "").strip(),
            "editorial": str(book.get("editorial") or "").strip(),
            "course": str(book.get("curso") or book.get("subject") or "").strip(),
            "status": str(book.get("estado") or book.get("status") or "").strip(),
            "workspace_dir": self._normalize_existing_text_path(str(book.get("workspace_dir") or "")),
            "cover_path": cover_path,
            "cover_url": cover_url,
            "instances": sorted(instances, key=lambda row: self._session_instance_sort_key(str(row.get("title") or ""))),
            "counts": {
                "instances": len(instances),
                "word_ready": ready,
                "word_pending": max(0, len(instances) - ready),
            },
        }

    def _session_instance_payload(
        self,
        book: dict[str, Any],
        instance: dict[str, Any],
        *,
        session_path: Path | None,
    ) -> dict[str, Any]:
        label = str(instance.get("tipo") or instance.get("name") or instance.get("label") or "").strip()
        if not label and session_path is not None:
            label = self._build_session_instance_label(session_path)
        session_exists = bool(session_path and session_path.exists())
        word_path = self.session_word_path_for(session_path)
        word_exists = bool(word_path and word_path.exists())
        session_url = self.file_url_resolver(str(session_path)) if session_exists and self.file_url_resolver else ""
        word_url = self.file_url_resolver(str(word_path)) if word_exists and self.file_url_resolver else ""
        key = self._stable_key(str(book.get("codigo") or ""), label, str(session_path or ""))
        return {
            "instance_key": key,
            "id": int(instance.get("id") or 0),
            "title": label or f"instancia-{int(instance.get('id') or 0)}",
            "session_path": str(session_path or ""),
            "session_exists": session_exists,
            "session_url": session_url,
            "word_path": str(word_path or ""),
            "word_exists": word_exists,
            "word_url": word_url,
            "expected_total": int(instance.get("total_esperado") or 0),
            "notes": str(instance.get("notas") or instance.get("notes") or "").strip(),
        }

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
            raise RuntimeError(f"La sesion no contiene items exportables: {session_path}")
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
        proc = subprocess.run(
            cmd,
            cwd=str(job.repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or f"exit={proc.returncode}").strip()
            raise RuntimeError(f"No se pudo generar Word: {detail}")
        produced = self._extract_generated_docx_from_stdout(proc.stdout) or job.output_docx
        if not produced.exists():
            raise RuntimeError(f"La conversion termino sin crear el Word: {produced}")
        return produced

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
        items_data = payload.get("items", []) if isinstance(payload, dict) else []
        if isinstance(items_data, list):
            for row in items_data:
                if not isinstance(row, dict):
                    continue
                for key in ("item", "item_text", "text", "latex", "enunciado_latex"):
                    candidate = str(row.get(key, "") or "").strip()
                    if candidate:
                        blocks.append(candidate)
                        break
        return "\n".join(blocks).strip()

    def _ensure_enumerate_wrapper(self, source_text: str) -> str:
        text = str(source_text or "").strip()
        if not text:
            return "\\begin{enumerate}\n\\end{enumerate}"
        low = text.lower()
        if "\\begin{enumerate}" in low and "\\end{enumerate}" in low:
            return text
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
            shutil.copy2(str(source), str(target))
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
