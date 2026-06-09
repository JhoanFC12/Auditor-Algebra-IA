from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

DEFAULT_INSTANCE_TYPES = ("resueltos", "propuestos")
WINDOWS_ABSOLUTE_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def preferred_drive_letters() -> tuple[str, ...]:
    configured = str(os.getenv("AUDITOR_PREFERRED_DRIVES", "") or "").strip()
    ordered: list[str] = []
    for raw in re.split(r"[^A-Za-z]+", configured):
        letter = (raw or "").strip().upper()[:1]
        if letter and letter not in ordered:
            ordered.append(letter)
    for letter in ("E", "D", "C", "K"):
        if letter not in ordered:
            ordered.append(letter)
    return tuple(ordered)


def remap_legacy_drive_path(path: Path | str, *, prefer_existing: bool = True) -> Path:
    raw_value = str(path or "").strip()
    if not raw_value:
        return Path("")
    try:
        candidate = Path(os.path.normpath(os.path.expanduser(raw_value)))
    except Exception:
        candidate = Path(raw_value)
    raw_candidate = str(candidate)
    if not WINDOWS_ABSOLUTE_DRIVE_RE.match(raw_candidate):
        return candidate
    try:
        if candidate.exists():
            return candidate
    except Exception:
        pass

    source_drive = str(candidate.drive or "").rstrip(":").upper()
    suffix = raw_candidate[2:].lstrip("\\/")
    remapped_candidates: list[Path] = []
    for drive in preferred_drive_letters():
        if drive == source_drive:
            continue
        remapped = Path(f"{drive}:\\{suffix}") if suffix else Path(f"{drive}:\\")
        remapped_candidates.append(remapped)
        try:
            if remapped.exists():
                return remapped
        except Exception:
            continue
    if prefer_existing:
        return candidate
    for remapped in remapped_candidates:
        try:
            if Path(f"{remapped.drive}\\").exists():
                return remapped
        except Exception:
            continue
    return candidate


def normalize_path(path: Path | str) -> Path:
    normalized = Path(os.path.normpath(os.path.abspath(str(path))))
    return remap_legacy_drive_path(normalized, prefer_existing=True)


def slugify_name(text: str, fallback: str = "libro") -> str:
    source = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    source = re.sub(r"[^A-Za-z0-9]+", "-", source).strip("-").lower()
    return source or fallback


def normalize_instance_name(instance_type: str, fallback: str = "sesion") -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]+", "_", str(instance_type or "").strip().lower()).strip("_")
    return clean or fallback


def default_workspace_dir(*, codigo: str, titulo: str, pdf_path: str, cwd: Path | None = None) -> Path:
    pdf_raw = str(pdf_path or "").strip()
    if pdf_raw:
        pdf_candidate = remap_legacy_drive_path(Path(pdf_raw).expanduser(), prefer_existing=False)
        parent = pdf_candidate.parent if pdf_candidate.parent else (cwd or Path.cwd())
        stem = pdf_candidate.stem.strip()
        safe_name = slugify_name(stem or codigo or titulo or "libro")
        return normalize_path(parent / safe_name)
    safe_name = slugify_name(codigo or titulo or "libro")
    return normalize_path((cwd or Path.cwd()) / ".cache" / "book_workspaces" / safe_name)


def resolve_workspace_root(*, codigo: str, titulo: str, pdf_path: str, workspace_dir: str, cwd: Path | None = None) -> Path:
    raw_workspace = str(workspace_dir or "").strip()
    if not raw_workspace:
        return default_workspace_dir(codigo=codigo, titulo=titulo, pdf_path=pdf_path, cwd=cwd)
    candidate = remap_legacy_drive_path(Path(raw_workspace).expanduser(), prefer_existing=False)
    safe_name = slugify_name(Path(str(pdf_path or "").strip()).stem or codigo or titulo or "libro")
    try:
        if candidate.name.strip().lower() == safe_name.lower():
            return normalize_path(candidate)
    except Exception:
        pass
    if (candidate / "sessions").exists() or (candidate / "solutions").exists() or (candidate / "temporales").exists():
        return normalize_path(candidate)
    return normalize_path(candidate / safe_name)


def project_dirs(workspace_dir: str | Path, instance_type: str | None = None) -> dict[str, Path]:
    root = normalize_path(Path(str(workspace_dir or "").strip()) if str(workspace_dir or "").strip() else Path.cwd())
    data = {
        "project_root": root,
        "sessions_dir": root / "sessions",
        "solutions_root": root / "solutions",
        "temporales_root": root / "temporales",
    }
    if instance_type is None:
        return data
    instance_name = normalize_instance_name(instance_type)
    instance_root = data["temporales_root"] / instance_name
    data.update(
        {
            "instance_type": Path(instance_name),
            "instance_root": instance_root,
            "sources_dir": instance_root / "sources",
            "crops_dir": instance_root / "crops",
            "segments_dir": instance_root / "segments",
            "datasets_dir": instance_root / "datasets",
            "solutions_dir": data["solutions_root"] / instance_name,
            "session_path": data["sessions_dir"] / f"{instance_name}.session.json",
        }
    )
    return data


def ensure_project_dirs(workspace_dir: str | Path, *, instance_types: tuple[str, ...] = DEFAULT_INSTANCE_TYPES) -> dict[str, Path]:
    dirs = project_dirs(workspace_dir)
    dirs["project_root"].mkdir(parents=True, exist_ok=True)
    dirs["sessions_dir"].mkdir(parents=True, exist_ok=True)
    dirs["solutions_root"].mkdir(parents=True, exist_ok=True)
    dirs["temporales_root"].mkdir(parents=True, exist_ok=True)
    for instance_type in instance_types:
        inst_dirs = project_dirs(dirs["project_root"], instance_type)
        inst_dirs["solutions_dir"].mkdir(parents=True, exist_ok=True)
        inst_dirs["sources_dir"].mkdir(parents=True, exist_ok=True)
        inst_dirs["crops_dir"].mkdir(parents=True, exist_ok=True)
        inst_dirs["segments_dir"].mkdir(parents=True, exist_ok=True)
        inst_dirs["datasets_dir"].mkdir(parents=True, exist_ok=True)
    return dirs


def infer_workspace_from_session_path(session_path: Path) -> Path | None:
    try:
        session_path = remap_legacy_drive_path(session_path, prefer_existing=True)
        if session_path.parent.name.strip().lower() != "sessions":
            return None
        return normalize_path(session_path.parent.parent)
    except Exception:
        return None
