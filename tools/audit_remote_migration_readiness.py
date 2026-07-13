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

from database.connection import DatabaseManager, read_db_profile_config


CORE_TABLES = [
    "problemas",
    "libros_escaneo",
    "libro_instancias_escaneo",
    "libro_archivos_escaneo",
    "libro_archivos_avance",
    "libro_secciones_escaneo",
    "origenes",
    "problema_origen",
]

PATH_COLUMN_HINTS = {
    "path",
    "ruta",
    "archivo",
    "pdf_path",
    "cover_path",
    "docx_path",
    "word_path",
    "imagen",
    "image",
}

WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


@dataclass(frozen=True)
class PathClassification:
    category: str
    needs_rewrite: bool
    can_check_locally: bool


@dataclass(frozen=True)
class PathSample:
    table: str
    column: str
    row_ref: str
    value: str
    category: str
    exists_locally: bool | None


@dataclass(frozen=True)
class PathColumnSummary:
    table: str
    column: str
    total_non_empty: int
    windows_or_unc: int
    server_absolute: int
    url: int
    sampled: int


@dataclass(frozen=True)
class AuditReport:
    generated_at: str
    profile: str
    database: dict[str, Any]
    file_checks_enabled: bool
    table_counts: dict[str, int | None]
    missing_core_tables: list[str]
    path_columns: list[PathColumnSummary]
    path_samples: list[PathSample]
    missing_local_files: list[PathSample]
    warnings: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only audit for remote migration readiness: counts core tables, detects local paths, and checks referenced files."
    )
    parser.add_argument("--profile", default="local_mirror", help="Database profile to audit. Default: local_mirror.")
    parser.add_argument("--db-name", default="", help="Override database name for the selected profile.")
    parser.add_argument(
        "--json-output",
        default=str(ROOT_DIR / "tmp" / "remote_migration_audit" / "audit.json"),
        help="Where to write the machine-readable audit JSON.",
    )
    parser.add_argument(
        "--markdown-output",
        default=str(ROOT_DIR / "docs" / "reporte_pre_migracion_servidor.md"),
        help="Where to write the human-readable Markdown report.",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=50,
        help="Maximum path samples per path column. Default: 50.",
    )
    parser.add_argument(
        "--skip-file-checks",
        action="store_true",
        help="Do not call Path.exists() for local absolute paths.",
    )
    return parser.parse_args()


def quote_ident(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def classify_path(value: str) -> PathClassification:
    text = str(value or "").strip()
    if not text:
        return PathClassification("empty", False, False)
    if URL_RE.match(text):
        return PathClassification("url", False, False)
    if WINDOWS_DRIVE_RE.match(text):
        return PathClassification("windows_drive", True, True)
    if text.startswith("\\\\") or text.startswith("//"):
        return PathClassification("unc_path", True, True)
    if text.startswith("/srv/"):
        return PathClassification("server_absolute", False, False)
    if text.startswith("/"):
        return PathClassification("posix_absolute", False, False)
    return PathClassification("relative_or_identifier", False, False)


def is_path_column(column_name: str) -> bool:
    lowered = str(column_name or "").lower()
    return any(hint in lowered for hint in PATH_COLUMN_HINTS)


def table_exists(cursor: Any, table: str) -> bool:
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = %s
              AND table_type = 'BASE TABLE'
        );
        """,
        (table,),
    )
    return bool(cursor.fetchone()[0])


def get_table_columns(cursor: Any, table: str) -> list[tuple[str, str]]:
    cursor.execute(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
        ORDER BY ordinal_position;
        """,
        (table,),
    )
    return [(str(row[0]), str(row[1])) for row in cursor.fetchall()]


def get_public_tables(cursor: Any) -> list[str]:
    cursor.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
        ORDER BY table_name;
        """
    )
    return [str(row[0]) for row in cursor.fetchall()]


def get_table_count(cursor: Any, table: str) -> int:
    cursor.execute(f"SELECT COUNT(*) FROM {quote_ident(table)};")
    return int(cursor.fetchone()[0] or 0)


def get_path_column_summary(cursor: Any, table: str, column: str) -> PathColumnSummary:
    table_sql = quote_ident(table)
    column_sql = quote_ident(column)
    cursor.execute(
        f"""
        SELECT
            COUNT(*) FILTER (WHERE NULLIF(BTRIM({column_sql}::text), '') IS NOT NULL) AS total_non_empty,
            COUNT(*) FILTER (
                WHERE NULLIF(BTRIM({column_sql}::text), '') IS NOT NULL
                  AND ({column_sql}::text ~ '^[A-Za-z]:[\\\\/]' OR {column_sql}::text LIKE '\\\\\\\\%%')
            ) AS windows_or_unc,
            COUNT(*) FILTER (
                WHERE NULLIF(BTRIM({column_sql}::text), '') IS NOT NULL
                  AND {column_sql}::text LIKE '/srv/%%'
            ) AS server_absolute,
            COUNT(*) FILTER (
                WHERE NULLIF(BTRIM({column_sql}::text), '') IS NOT NULL
                  AND {column_sql}::text ~ '^[A-Za-z][A-Za-z0-9+.-]*://'
            ) AS url
        FROM {table_sql};
        """
    )
    row = cursor.fetchone()
    return PathColumnSummary(
        table=table,
        column=column,
        total_non_empty=int(row[0] or 0),
        windows_or_unc=int(row[1] or 0),
        server_absolute=int(row[2] or 0),
        url=int(row[3] or 0),
        sampled=0,
    )


def get_path_samples(
    cursor: Any,
    *,
    table: str,
    column: str,
    columns: Iterable[str],
    limit: int,
    check_files: bool,
) -> list[PathSample]:
    table_sql = quote_ident(table)
    column_sql = quote_ident(column)
    row_ref_sql = quote_ident("id") if "id" in set(columns) else "ctid::text"
    cursor.execute(
        f"""
        SELECT {row_ref_sql}::text AS row_ref, {column_sql}::text AS value
        FROM {table_sql}
        WHERE NULLIF(BTRIM({column_sql}::text), '') IS NOT NULL
          AND ({column_sql}::text ~ '^[A-Za-z]:[\\\\/]' OR {column_sql}::text LIKE '\\\\\\\\%%')
        ORDER BY 1
        LIMIT %s;
        """,
        (max(int(limit), 0),),
    )
    samples: list[PathSample] = []
    for row_ref, raw_value in cursor.fetchall():
        value = str(raw_value or "").strip()
        classification = classify_path(value)
        exists_locally: bool | None = None
        if check_files and classification.can_check_locally:
            try:
                exists_locally = Path(value).exists()
            except OSError:
                exists_locally = False
        samples.append(
            PathSample(
                table=table,
                column=column,
                row_ref=str(row_ref),
                value=value,
                category=classification.category,
                exists_locally=exists_locally,
            )
        )
    return samples


def build_audit_report(
    *,
    profile: str,
    db_name: str = "",
    sample_limit: int = 50,
    check_files: bool = True,
) -> AuditReport:
    config = read_db_profile_config(profile)
    manager = DatabaseManager.from_profile(profile, db_name=db_name or None)
    database_info = {
        "profile": profile,
        "host": manager.host,
        "port": manager.port,
        "db_name": manager.db_name,
        "user": manager.user,
        "sslmode": manager.sslmode,
    }
    warnings: list[str] = []
    table_counts: dict[str, int | None] = {}
    missing_core_tables: list[str] = []
    path_columns: list[PathColumnSummary] = []
    path_samples: list[PathSample] = []

    conn = manager.get_connection(manager.db_name)
    try:
        with conn.cursor() as cursor:
            public_tables = set(get_public_tables(cursor))
            for table in CORE_TABLES:
                if table in public_tables:
                    table_counts[table] = get_table_count(cursor, table)
                else:
                    table_counts[table] = None
                    missing_core_tables.append(table)

            for table in sorted(public_tables):
                columns = get_table_columns(cursor, table)
                column_names = [name for name, _data_type in columns]
                for column, data_type in columns:
                    if data_type not in {"text", "character varying", "character", "json", "jsonb"}:
                        continue
                    if not is_path_column(column):
                        continue
                    summary = get_path_column_summary(cursor, table, column)
                    if summary.total_non_empty == 0:
                        continue
                    samples = get_path_samples(
                        cursor,
                        table=table,
                        column=column,
                        columns=column_names,
                        limit=sample_limit,
                        check_files=check_files,
                    )
                    path_columns.append(
                        PathColumnSummary(
                            table=summary.table,
                            column=summary.column,
                            total_non_empty=summary.total_non_empty,
                            windows_or_unc=summary.windows_or_unc,
                            server_absolute=summary.server_absolute,
                            url=summary.url,
                            sampled=len(samples),
                        )
                    )
                    path_samples.extend(samples)
    finally:
        conn.close()

    if config.get("profile") == "cloud":
        warnings.append("Auditing the cloud profile. Confirm this is intentional before migration.")
    missing_local_files = [sample for sample in path_samples if sample.exists_locally is False]
    return AuditReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        profile=profile,
        database=database_info,
        file_checks_enabled=check_files,
        table_counts=table_counts,
        missing_core_tables=missing_core_tables,
        path_columns=path_columns,
        path_samples=path_samples,
        missing_local_files=missing_local_files,
        warnings=warnings,
    )


def render_markdown_report(report: AuditReport) -> str:
    lines: list[str] = []
    lines.append("# Pre-Migration Readiness Report")
    lines.append("")
    lines.append(f"Generated at: `{report.generated_at}`")
    lines.append(f"Profile: `{report.profile}`")
    lines.append(
        "Database: "
        f"`{report.database.get('user')}@{report.database.get('host')}:{report.database.get('port')}/{report.database.get('db_name')}`"
    )
    lines.append("")
    lines.append("## Executive Summary")
    lines.append("")
    total_windows_paths = sum(item.windows_or_unc for item in report.path_columns)
    lines.append(f"- Core tables checked: `{len(report.table_counts)}`")
    lines.append(f"- Missing core tables: `{len(report.missing_core_tables)}`")
    lines.append(f"- Path columns with values: `{len(report.path_columns)}`")
    lines.append(f"- Windows/UNC paths needing rewrite: `{total_windows_paths}`")
    if report.file_checks_enabled:
        lines.append(f"- Sampled local files missing: `{len(report.missing_local_files)}`")
    else:
        lines.append("- Sampled local files missing: `not checked`")
    lines.append("")

    if report.warnings:
        lines.append("## Warnings")
        lines.append("")
        for warning in report.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    lines.append("## Core Table Counts")
    lines.append("")
    lines.append("| Table | Rows |")
    lines.append("|---|---:|")
    for table, count in report.table_counts.items():
        value = "missing" if count is None else str(count)
        lines.append(f"| `{table}` | {value} |")
    lines.append("")

    if report.missing_core_tables:
        lines.append("## Missing Core Tables")
        lines.append("")
        for table in report.missing_core_tables:
            lines.append(f"- `{table}`")
        lines.append("")

    lines.append("## Path Column Summary")
    lines.append("")
    if report.path_columns:
        lines.append("| Table | Column | Non-empty | Windows/UNC | Server `/srv` | URL | Sampled |")
        lines.append("|---|---|---:|---:|---:|---:|---:|")
        for item in report.path_columns:
            lines.append(
                f"| `{item.table}` | `{item.column}` | {item.total_non_empty} | {item.windows_or_unc} | "
                f"{item.server_absolute} | {item.url} | {item.sampled} |"
            )
    else:
        lines.append("No path-like columns with values were found.")
    lines.append("")

    lines.append("## Missing Local File Samples")
    lines.append("")
    if not report.file_checks_enabled:
        lines.append("Local file existence checks were skipped for this run.")
    elif report.missing_local_files:
        lines.append("| Table | Column | Row | Path |")
        lines.append("|---|---|---:|---|")
        for sample in report.missing_local_files[:100]:
            safe_value = sample.value.replace("|", "\\|")
            lines.append(f"| `{sample.table}` | `{sample.column}` | `{sample.row_ref}` | `{safe_value}` |")
        if len(report.missing_local_files) > 100:
            lines.append("")
            lines.append(f"Only the first 100 missing file samples are shown out of {len(report.missing_local_files)}.")
    else:
        lines.append("No missing local files were found in sampled Windows/UNC paths.")
    lines.append("")

    lines.append("## Windows/UNC Path Samples")
    lines.append("")
    if report.path_samples:
        lines.append("| Table | Column | Row | Exists locally | Path |")
        lines.append("|---|---|---:|---|---|")
        for sample in report.path_samples[:200]:
            exists = "not checked" if sample.exists_locally is None else ("yes" if sample.exists_locally else "no")
            safe_value = sample.value.replace("|", "\\|")
            lines.append(f"| `{sample.table}` | `{sample.column}` | `{sample.row_ref}` | {exists} | `{safe_value}` |")
        if len(report.path_samples) > 200:
            lines.append("")
            lines.append(f"Only the first 200 path samples are shown out of {len(report.path_samples)}.")
    else:
        lines.append("No Windows/UNC path samples were found.")
    lines.append("")

    lines.append("## Required Next Actions")
    lines.append("")
    lines.append("1. Confirm the audited database is the intended migration source.")
    lines.append("2. Resolve missing required PDFs/covers before export.")
    lines.append("3. Define server rewrite rules for every Windows/UNC path family.")
    lines.append("4. Run bundle export only after this report is reviewed.")
    lines.append("5. Restore to a test PostgreSQL database before production cutover.")
    lines.append("")
    return "\n".join(lines)


def write_report(report: AuditReport, *, json_output: Path, markdown_output: Path) -> None:
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8")
    markdown_output.write_text(render_markdown_report(report), encoding="utf-8")


def main() -> None:
    args = parse_args()
    report = build_audit_report(
        profile=args.profile,
        db_name=args.db_name,
        sample_limit=args.sample_limit,
        check_files=not args.skip_file_checks,
    )
    write_report(report, json_output=Path(args.json_output), markdown_output=Path(args.markdown_output))
    print(f"[ok] json: {Path(args.json_output)}")
    print(f"[ok] markdown: {Path(args.markdown_output)}")
    print(f"[ok] windows_or_unc_paths: {sum(item.windows_or_unc for item in report.path_columns)}")
    print(f"[ok] missing_file_samples: {len(report.missing_local_files)}")


if __name__ == "__main__":
    main()
