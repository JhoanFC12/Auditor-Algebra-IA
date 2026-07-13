from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from modulos.book_catalog_sync import SyncOptions, run_catalog_sync


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sincroniza el catalogo visual de libros hacia Biblioteca/Fabrica en modo incremental."
    )
    parser.add_argument(
        "--output-root",
        default=str(Path(".cache") / "book_catalog"),
        help="Raiz del catalogo visual/Obsidian.",
    )
    parser.add_argument(
        "--sync-root",
        default=str(Path(".cache") / "book_catalog_sync"),
        help="Raiz donde guardar plan, reporte y conflictos.",
    )
    parser.add_argument(
        "--db-name",
        default="",
        help="Base de datos de Biblioteca. Si se omite, usa la configurada.",
    )
    parser.add_argument(
        "--book-id",
        action="append",
        default=[],
        help="Sincroniza solo un book_id concreto. Puede repetirse.",
    )
    parser.add_argument(
        "--allow-exams",
        action="store_true",
        help="Incluye libros de examenes. Por defecto se excluyen.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplica cambios en Biblioteca. Sin este flag solo genera dry-run.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_catalog_sync(
        SyncOptions(
            output_root=Path(args.output_root).expanduser().resolve(),
            sync_root=Path(args.sync_root).expanduser().resolve(),
            db_name=str(args.db_name or "").strip(),
            apply=bool(args.apply),
            allow_exams=bool(args.allow_exams),
            book_ids=tuple(str(item).strip() for item in args.book_id if str(item).strip()),
        )
    )
    summary = report["summary"]
    print(f"[ok] plan: {Path(report['sync_root']) / 'sync_plan.json'}")
    print(f"[ok] report: {Path(report['sync_root']) / 'sync_report.md'}")
    print(f"[ok] conflicts: {Path(report['sync_root']) / 'conflicts.jsonl'}")
    print(f"[ok] imported: {Path(report['sync_root']) / 'imported_books.jsonl'}")
    print(f"[ok] backend: {report['backend']['status']} db={report['db_name'] or 'sin_base'}")
    print(f"[ok] books_detected: {summary['books_detected']}")
    print(f"[ok] books_new: {summary['books_new']}")
    print(f"[ok] books_existing: {summary['books_existing']}")
    print(f"[ok] instances_to_create: {summary['instances_to_create']}")
    print(f"[ok] instances_to_update: {summary['instances_to_update']}")
    if report.get("apply_result"):
        apply_summary = report["apply_result"]["summary"]
        print(
            "[apply] "
            f"books_created={apply_summary['books_created']} "
            f"books_updated={apply_summary['books_updated']} "
            f"instances_created={apply_summary['instances_created']} "
            f"instances_updated={apply_summary['instances_updated']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
