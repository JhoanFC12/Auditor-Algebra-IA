from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from database.connection import DatabaseManager
from utils.project_layout import infer_workspace_from_session_path, project_dirs, remap_legacy_drive_path


WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
PATH_KEY_HINTS = (
    "path",
    "ruta",
    "archivo",
    "file",
    "image",
    "imagen",
    "pdf",
    "crop",
    "segment",
    "output",
    "staging",
)


@dataclass(frozen=True)
class PathValueSample:
    instance: str
    file: str
    key_path: str
    value: str
    category: str


@dataclass(frozen=True)
class StagingInstanceSummary:
    instance: str
    root: str
    manifest_exists: bool
    records_dir_exists: bool
    record_files_total: int
    json_files_scanned: int
    path_values_total: int
    windows_or_unc_values: int
    server_values: int
    relative_values: int
    url_values: int


@dataclass(frozen=True)
class StagingPathAudit:
    generated_at: str
    roots: list[str]
    instances: list[StagingInstanceSummary]
    samples: list[PathValueSample]
    warnings: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only staging path inventory for PDF Factory server migration.")
    parser.add_argument(
        "--staging-root",
        action="append",
        default=None,
        help="Staging root or parent folder containing instance staging folders. Can be repeated.",
    )
    parser.add_argument(
        "--db-profile",
        default="",
        help="Optional database profile used to discover instance staging roots from library rows.",
    )
    parser.add_argument("--db-name", default="", help="Optional database name override for --db-profile.")
    parser.add_argument(
        "--max-db-instances",
        type=int,
        default=0,
        help="Maximum DB instances to inspect when --db-profile is used. 0 means no limit.",
    )
    parser.add_argument(
        "--json-output",
        default=str(ROOT_DIR / "tmp" / "server_factory_inventory" / "staging_paths.json"),
        help="Where to write the machine-readable staging path audit.",
    )
    parser.add_argument(
        "--markdown-output",
        default=str(ROOT_DIR / "docs" / "server_factory_staging_paths.md"),
        help="Where to write the human-readable staging path audit.",
    )
    parser.add_argument("--sample-limit", type=int, default=100, help="Maximum path samples to keep in the report.")
    parser.add_argument(
        "--max-record-files-per-instance",
        type=int,
        default=200,
        help="Maximum record JSON files to scan per instance. Use 0 for no limit.",
    )
    return parser.parse_args()


def classify_path_value(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "empty"
    if URL_RE.match(text):
        return "url"
    if WINDOWS_DRIVE_RE.match(text) or text.startswith("\\\\") or text.startswith("//"):
        return "windows_or_unc"
    normalized = text.replace("\\", "/")
    if normalized.startswith("/srv/mathcontentstudio/"):
        return "server_storage"
    if normalized.startswith("/"):
        return "posix_absolute"
    return "relative_or_identifier"


def looks_like_path_key(key: str) -> bool:
    lowered = str(key or "").lower()
    return any(hint in lowered for hint in PATH_KEY_HINTS)


def extract_path_values(payload: Any, *, prefix: str = "") -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, str):
                category = classify_path_value(value)
                if looks_like_path_key(str(key)) or category in {"windows_or_unc", "server_storage", "posix_absolute", "url"}:
                    found.append((key_path, value, category))
            elif isinstance(value, (dict, list)):
                found.extend(extract_path_values(value, prefix=key_path))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            key_path = f"{prefix}[{index}]"
            if isinstance(value, str):
                category = classify_path_value(value)
                if category in {"windows_or_unc", "server_storage", "posix_absolute", "url"}:
                    found.append((key_path, value, category))
            elif isinstance(value, (dict, list)):
                found.extend(extract_path_values(value, prefix=key_path))
    return found


def _unique_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        key = str(path.resolve()).lower() if path.exists() else str(path.absolute()).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def staging_roots_from_library_rows(rows: Iterable[dict[str, Any]]) -> list[Path]:
    roots: list[Path] = []
    for row in rows:
        instance_type = str(row.get("codigo_instancia") or row.get("instance_type") or row.get("tipo") or "").strip()
        if not instance_type:
            continue
        workspace_raw = str(row.get("workspace_dir") or row.get("book_workspace_dir") or "").strip()
        session_raw = str(row.get("session_path") or "").strip()
        workspace: Path | None = None
        if session_raw:
            workspace = infer_workspace_from_session_path(Path(session_raw))
        if workspace is None and workspace_raw:
            workspace = remap_legacy_drive_path(Path(workspace_raw).expanduser(), prefer_existing=True)
        if workspace is None:
            continue
        try:
            roots.append(project_dirs(workspace, instance_type)["datasets_dir"] / "pdf_factory_staging")
        except Exception:
            continue
    return _unique_paths(roots)


def load_db_staging_roots(*, profile: str, db_name: str = "", limit: int = 0) -> list[Path]:
    manager = DatabaseManager.from_profile(profile, db_name=db_name or None)
    sql = """
        SELECT
            i.id,
            i.codigo_instancia,
            i.session_path,
            i.pdf_path,
            l.codigo AS book_code,
            l.titulo AS book_title,
            l.workspace_dir AS book_workspace_dir
        FROM libro_instancias_escaneo i
        JOIN libros_escaneo l ON l.id = i.libro_id
        WHERE COALESCE(i.activo, TRUE) = TRUE
        ORDER BY COALESCE(i.updated_at, i.created_at) DESC NULLS LAST, i.id;
    """
    if limit and limit > 0:
        sql = sql.rstrip().rstrip(";") + "\n        LIMIT %s;"
    conn = manager.get_connection(manager.db_name)
    try:
        with conn.cursor() as cursor:
            if limit and limit > 0:
                cursor.execute(sql, (int(limit),))
            else:
                cursor.execute(sql)
            columns = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        conn.close()
    return staging_roots_from_library_rows(rows)


def discover_instance_dirs(roots: Iterable[Path]) -> list[Path]:
    candidates: list[Path] = []
    for root in roots:
        base = Path(root).expanduser()
        if not base.exists():
            continue
        if (base / "manifest.json").exists() or (base / "records").exists():
            candidates.append(base)
            continue
        for records_dir in base.rglob("records"):
            if records_dir.is_dir():
                candidates.append(records_dir.parent)
    return sorted(_unique_paths(candidates), key=lambda item: str(item).lower())


def _record_json_files(records_dir: Path, limit: int) -> list[Path]:
    files = sorted(records_dir.glob("*.json"), key=lambda item: item.name.lower()) if records_dir.exists() else []
    if limit and limit > 0:
        return files[:limit]
    return files


def audit_instance_dir(instance_dir: Path, *, sample_limit: int, max_record_files: int) -> tuple[StagingInstanceSummary, list[PathValueSample]]:
    manifest_path = instance_dir / "manifest.json"
    records_dir = instance_dir / "records"
    files: list[Path] = []
    if manifest_path.exists():
        files.append(manifest_path)
    files.extend(_record_json_files(records_dir, max_record_files))

    counts = {
        "path_values_total": 0,
        "windows_or_unc_values": 0,
        "server_values": 0,
        "relative_values": 0,
        "url_values": 0,
    }
    samples: list[PathValueSample] = []
    instance_name = instance_dir.name
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for key_path, value, category in extract_path_values(payload):
            counts["path_values_total"] += 1
            if category == "windows_or_unc":
                counts["windows_or_unc_values"] += 1
            elif category == "server_storage":
                counts["server_values"] += 1
            elif category == "url":
                counts["url_values"] += 1
            elif category == "relative_or_identifier":
                counts["relative_values"] += 1
            if len(samples) < sample_limit:
                samples.append(
                    PathValueSample(
                        instance=instance_name,
                        file=str(path.relative_to(instance_dir)),
                        key_path=key_path,
                        value=str(value),
                        category=category,
                    )
                )

    summary = StagingInstanceSummary(
        instance=instance_name,
        root=str(instance_dir),
        manifest_exists=manifest_path.exists(),
        records_dir_exists=records_dir.exists(),
        record_files_total=len(list(records_dir.glob("*.json"))) if records_dir.exists() else 0,
        json_files_scanned=len(files),
        path_values_total=counts["path_values_total"],
        windows_or_unc_values=counts["windows_or_unc_values"],
        server_values=counts["server_values"],
        relative_values=counts["relative_values"],
        url_values=counts["url_values"],
    )
    return summary, samples


def build_audit(
    *,
    roots: Iterable[Path],
    sample_limit: int = 100,
    max_record_files_per_instance: int = 200,
) -> StagingPathAudit:
    selected_roots = [Path(root).expanduser() for root in roots]
    instance_dirs = discover_instance_dirs(selected_roots)
    warnings: list[str] = []
    for root in selected_roots:
        if not root.exists():
            warnings.append(f"Staging root does not exist: {root}")
    instances: list[StagingInstanceSummary] = []
    samples: list[PathValueSample] = []
    for instance_dir in instance_dirs:
        summary, instance_samples = audit_instance_dir(
            instance_dir,
            sample_limit=max(0, sample_limit - len(samples)),
            max_record_files=max_record_files_per_instance,
        )
        instances.append(summary)
        samples.extend(instance_samples)
    return StagingPathAudit(
        generated_at=datetime.now(timezone.utc).isoformat(),
        roots=[str(root) for root in selected_roots],
        instances=instances,
        samples=samples[:sample_limit],
        warnings=warnings,
    )


def render_markdown(audit: StagingPathAudit) -> str:
    total_records = sum(row.record_files_total for row in audit.instances)
    total_windows = sum(row.windows_or_unc_values for row in audit.instances)
    total_server = sum(row.server_values for row in audit.instances)
    total_path_values = sum(row.path_values_total for row in audit.instances)
    lines: list[str] = []
    lines.append("# Server Factory Staging Path Inventory")
    lines.append("")
    lines.append(f"Generated at: `{audit.generated_at}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Roots: `{len(audit.roots)}`")
    lines.append(f"- Instance staging folders: `{len(audit.instances)}`")
    lines.append(f"- Record JSON files: `{total_records}`")
    lines.append(f"- Path-like values scanned: `{total_path_values}`")
    lines.append(f"- Windows/UNC values: `{total_windows}`")
    lines.append(f"- Server storage values: `{total_server}`")
    lines.append("")
    if audit.warnings:
        lines.append("## Warnings")
        lines.append("")
        for warning in audit.warnings:
            lines.append(f"- {warning}")
        lines.append("")
    lines.append("## Instance Summary")
    lines.append("")
    lines.append("| Instance | Records | Scanned JSON | Windows/UNC | Server | Relative | URL | Root |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---|")
    for row in audit.instances[:200]:
        root = row.root.replace("|", "\\|")
        lines.append(
            f"| `{row.instance}` | {row.record_files_total} | {row.json_files_scanned} | "
            f"{row.windows_or_unc_values} | {row.server_values} | {row.relative_values} | {row.url_values} | `{root}` |"
        )
    if len(audit.instances) > 200:
        lines.append("")
        lines.append(f"Only the first 200 instances are shown out of {len(audit.instances)}.")
    lines.append("")
    lines.append("## Path Samples")
    lines.append("")
    if audit.samples:
        lines.append("| Instance | File | Key | Category | Value |")
        lines.append("|---|---|---|---|---|")
        for sample in audit.samples:
            value = sample.value.replace("|", "\\|")
            lines.append(
                f"| `{sample.instance}` | `{sample.file}` | `{sample.key_path}` | `{sample.category}` | `{value}` |"
            )
    else:
        lines.append("No path-like values were sampled.")
    lines.append("")
    lines.append("## Required Server Actions")
    lines.append("")
    lines.append("1. Move staging artifacts to `/srv/mathcontentstudio` before remote production use.")
    lines.append("2. Replace Windows/UNC paths with portable server asset references.")
    lines.append("3. Keep local staging as development/mirror data until an explicit sync contract exists.")
    lines.append("")
    return "\n".join(lines)


def write_outputs(audit: StagingPathAudit, *, json_output: Path, markdown_output: Path) -> None:
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(asdict(audit), ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_output.write_text(render_markdown(audit), encoding="utf-8")


def main() -> None:
    args = parse_args()
    roots = [Path(item) for item in args.staging_root] if args.staging_root else [ROOT_DIR / ".cache" / "transcriptor_runs" / "staging"]
    if str(args.db_profile or "").strip():
        roots.extend(
            load_db_staging_roots(
                profile=str(args.db_profile).strip(),
                db_name=str(args.db_name or "").strip(),
                limit=max(0, int(args.max_db_instances)),
            )
        )
    audit = build_audit(
        roots=roots,
        sample_limit=max(0, int(args.sample_limit)),
        max_record_files_per_instance=max(0, int(args.max_record_files_per_instance)),
    )
    write_outputs(audit, json_output=Path(args.json_output), markdown_output=Path(args.markdown_output))
    print(f"[ok] json: {Path(args.json_output)}")
    print(f"[ok] markdown: {Path(args.markdown_output)}")
    print(f"[ok] instances: {len(audit.instances)}")
    print(f"[ok] windows_or_unc_values: {sum(row.windows_or_unc_values for row in audit.instances)}")


if __name__ == "__main__":
    main()
