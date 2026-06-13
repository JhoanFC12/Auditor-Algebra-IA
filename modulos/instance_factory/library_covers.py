from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from utils.project_layout import remap_legacy_drive_path, slugify_name


ALLOWED_COVER_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def library_cover_root() -> Path:
    override = str(os.getenv("PDF_LIBRARY_COVER_ROOT") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[2] / ".cache" / "instance_factory" / "library_covers"


def library_cover_dir(payload: dict[str, Any], *, db_name: str = "") -> Path:
    database = slugify_name(str(db_name or payload.get("db_name") or "biblioteca").strip() or "biblioteca")
    book_key = _book_cover_key(payload)
    return library_cover_root() / database / book_key


def save_cover_bytes(raw: bytes, suffix: str, payload: dict[str, Any], *, db_name: str = "") -> Path:
    clean_suffix = str(suffix or "").lower()
    if clean_suffix == ".jpeg":
        clean_suffix = ".jpg"
    if clean_suffix not in ALLOWED_COVER_SUFFIXES:
        raise ValueError("Extension de portada no permitida.")
    target_dir = library_cover_dir(payload, db_name=db_name)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"cover{clean_suffix}"
    target.write_bytes(raw)
    return target


def copy_cover_to_library_store(path_text: str, payload: dict[str, Any], *, db_name: str = "") -> str:
    raw = str(path_text or "").strip()
    if not raw:
        return ""
    try:
        source = remap_legacy_drive_path(Path(raw).expanduser(), prefer_existing=True).resolve()
    except Exception:
        return raw
    if not source.exists() or not source.is_file():
        return raw
    if source.suffix.lower() not in ALLOWED_COVER_SUFFIXES:
        return raw
    if _is_relative_to(source, library_cover_root().resolve()):
        return str(source)
    target_dir = library_cover_dir(payload, db_name=db_name)
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".jpg" if source.suffix.lower() == ".jpeg" else source.suffix.lower()
    target = target_dir / f"cover{suffix}"
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    return str(target)


def _book_cover_key(payload: dict[str, Any]) -> str:
    book_id = str(payload.get("id") or payload.get("book_id") or payload.get("libro_id") or "").strip()
    code = str(payload.get("codigo") or payload.get("code") or "").strip()
    title = str(payload.get("titulo") or payload.get("title") or "").strip()
    if book_id:
        return slugify_name(f"book-{book_id}-{code or title or 'libro'}")
    return slugify_name(code or title or "libro")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
