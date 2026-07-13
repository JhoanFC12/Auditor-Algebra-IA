from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from modulos.book_visual_catalog import (
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_SOURCE_ROOT,
    DEFAULT_OCR_LANG,
    classify_book_pages_with_ocr,
    export_book_themes,
    first_book_ids_from_index,
    organize_vault,
    process_pdf,
    records_by_book_id,
    scan_pdf_inventory,
    write_pdf_listing,
    write_inventory,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inventario y catalogacion visual bootstrap para libros/PDFs escaneados."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory_parser = subparsers.add_parser("inventory", help="Escanea PDFs y genera inventory.jsonl")
    inventory_parser.add_argument(
        "--source-root",
        default=str(DEFAULT_SOURCE_ROOT),
        help="Raiz donde buscar PDFs.",
    )
    inventory_parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Raiz de salida para inventory.jsonl y artefactos por libro.",
    )
    inventory_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limita cuantos PDFs inventariar en esta corrida.",
    )

    list_parser = subparsers.add_parser("list-pdfs", help="Lista todos los PDFs por curso sin calcular hash ni paginas.")
    list_parser.add_argument(
        "--source-root",
        default=str(DEFAULT_SOURCE_ROOT),
        help="Raiz donde buscar PDFs.",
    )
    list_parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Raiz del vault/catalogo visual.",
    )

    vault_parser = subparsers.add_parser("vault", help="Crea/actualiza estructura Obsidian por cursos.")
    vault_parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Raiz del vault/catalogo visual.",
    )

    process_parser = subparsers.add_parser("process", help="Procesa un PDF de prueba y genera evidencia visual.")
    process_parser.add_argument("pdf_path", help="Ruta del PDF a procesar.")
    process_parser.add_argument(
        "--source-root",
        default=str(DEFAULT_SOURCE_ROOT),
        help="Raiz de libros para calcular rutas relativas e inventario.",
    )
    process_parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Raiz de salida para el catalogo visual.",
    )
    process_parser.add_argument(
        "--pages",
        default=None,
        help="Paginas a procesar. Ejemplo: 1-12,18,20-24",
    )
    process_parser.add_argument(
        "--page-limit",
        type=int,
        default=None,
        help="Corta la seleccion despues de N paginas.",
    )
    process_parser.add_argument(
        "--dpi",
        type=int,
        default=120,
        help="Resolucion de rasterizado para pdftoppm.",
    )
    process_parser.add_argument(
        "--thumbnail-width",
        type=int,
        default=320,
        help="Ancho maximo de miniatura por pagina.",
    )
    process_parser.add_argument(
        "--contact-sheet-columns",
        type=int,
        default=4,
        help="Columnas por contact sheet.",
    )
    process_parser.add_argument(
        "--contact-sheet-rows",
        type=int,
        default=3,
        help="Filas por contact sheet.",
    )
    process_parser.add_argument(
        "--force",
        action="store_true",
        help="Regenera paginas, thumbnails y contact sheets.",
    )
    process_parser.add_argument(
        "--allow-duplicate",
        action="store_true",
        help="Permite procesar aunque coincida con un libro ya trabajado.",
    )
    process_parser.add_argument(
        "--allow-part",
        action="store_true",
        help="Permite procesar PDFs detectados como partes de un PDF general.",
    )

    ocr_parser = subparsers.add_parser(
        "ocr-classify",
        help="Aplica OCR local Tesseract y propone etiquetas de pagina.",
    )
    ocr_parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Raiz del vault/catalogo visual.",
    )
    ocr_parser.add_argument(
        "--book-id",
        action="append",
        default=[],
        help="book_id a clasificar. Puede repetirse.",
    )
    ocr_parser.add_argument(
        "--first-index",
        type=int,
        default=None,
        help="Toma los primeros N book_id del Indice General.md.",
    )
    ocr_parser.add_argument(
        "--pages",
        default=None,
        help="Paginas a procesar. Ejemplo: 1-12,18,20-24. Si se omite, procesa todo el libro.",
    )
    ocr_parser.add_argument(
        "--page-limit",
        type=int,
        default=None,
        help="Corta la seleccion despues de N paginas por libro.",
    )
    ocr_parser.add_argument(
        "--dpi",
        type=int,
        default=150,
        help="Resolucion de rasterizado para OCR.",
    )
    ocr_parser.add_argument(
        "--lang",
        default=DEFAULT_OCR_LANG,
        help="Idioma Tesseract, por defecto spa.",
    )
    ocr_parser.add_argument(
        "--tesseract-cmd",
        default=None,
        help="Ruta a tesseract.exe si no esta en PATH.",
    )
    ocr_parser.add_argument(
        "--tessdata-dir",
        default=None,
        help="Directorio tessdata con spa.traineddata.",
    )
    ocr_parser.add_argument(
        "--force-render",
        action="store_true",
        help="Vuelve a renderizar paginas aunque ya existan.",
    )
    ocr_parser.add_argument(
        "--force-ocr",
        action="store_true",
        help="Vuelve a ejecutar OCR aunque ya exista texto cacheado.",
    )
    ocr_parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Paginas OCR en paralelo por libro.",
    )

    themes_parser = subparsers.add_parser(
        "themes",
        help="Genera themes.json por libro usando evidencia visual/OCR ya catalogada.",
    )
    themes_parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Raiz del vault/catalogo visual.",
    )
    themes_parser.add_argument(
        "--book-id",
        action="append",
        default=[],
        help="book_id a exportar. Puede repetirse. Si se omite, procesa todos los libros catalogados.",
    )
    themes_parser.add_argument(
        "--no-obsidian",
        action="store_true",
        help="No actualiza el bloque de temas en obsidian.md.",
    )
    return parser


def cmd_inventory(args: argparse.Namespace) -> int:
    source_root = Path(args.source_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    records = scan_pdf_inventory(source_root, limit=args.limit)
    inventory_path = write_inventory(records, output_root / "inventory.jsonl")
    print(f"[ok] inventario: {inventory_path}")
    print(f"[ok] pdfs: {len(records)}")
    return 0


def cmd_process(args: argparse.Namespace) -> int:
    output = process_pdf(
        pdf_path=Path(args.pdf_path).expanduser().resolve(),
        source_root=Path(args.source_root).expanduser().resolve(),
        output_root=Path(args.output_root).expanduser().resolve(),
        page_spec=args.pages,
        page_limit=args.page_limit,
        dpi=args.dpi,
        thumbnail_width=args.thumbnail_width,
        contact_sheet_columns=args.contact_sheet_columns,
        contact_sheet_rows=args.contact_sheet_rows,
        force=bool(args.force),
        allow_duplicates=bool(args.allow_duplicate),
        allow_parts=bool(args.allow_part),
    )
    record = output["record"]
    if output.get("status") == "part_skipped":
        part = output["part"]
        print(f"[skip] parte: {record.book_id}")
        print(f"[skip] general: {part.general_pdf_path}")
        print(f"[skip] motivo: {part.reason}")
        print(f"[ok] partes: {output['parts_markdown']}")
        return 0
    if output.get("status") == "duplicate_skipped":
        duplicate = output["duplicate"]
        print(f"[skip] duplicado: {record.book_id}")
        print(f"[skip] tipo: {duplicate.match_type}")
        print(f"[skip] canonico: {duplicate.canonical_book_id}")
        print(f"[skip] motivo: {duplicate.reason}")
        print(f"[ok] duplicates: {output['duplicates_markdown']}")
        return 0
    print(f"[ok] book_id: {record.book_id}")
    print(f"[ok] paginas procesadas: {output['processed_pages_total']}")
    print(f"[ok] inventory: {output['inventory_jsonl']}")
    print(f"[ok] pages: {output['pages_jsonl']}")
    print(f"[ok] ranges: {output['ranges_json']}")
    print(f"[ok] obsidian: {output['obsidian_md']}")
    print(f"[ok] manifest: {output['book_manifest']}")
    print(f"[ok] vault index: {output['vault_index']}")
    for sheet in output["contact_sheets"]:
        print(
            f"[sheet] {sheet['sheet_index']:03d} "
            f"pages={sheet['start_page']}-{sheet['end_page']} path={sheet['image_path']}"
        )
    return 0


def cmd_vault(args: argparse.Namespace) -> int:
    output = organize_vault(Path(args.output_root).expanduser().resolve())
    print(f"[ok] vault: {output['vault_root']}")
    print(f"[ok] indice: {output['index_path']}")
    print(f"[ok] notas: {output['notes_total']}")
    for course, total in output["notes_by_course"].items():
        print(f"[curso] {course}: {total}")
    return 0


def cmd_list_pdfs(args: argparse.Namespace) -> int:
    output = write_pdf_listing(
        source_root=Path(args.source_root).expanduser().resolve(),
        output_root=Path(args.output_root).expanduser().resolve(),
    )
    organize_vault(Path(args.output_root).expanduser().resolve())
    print(f"[ok] listado markdown: {output['markdown_path']}")
    print(f"[ok] listado jsonl: {output['jsonl_path']}")
    print(f"[ok] pdfs: {output['total']}")
    for course, total in output["by_course"].items():
        print(f"[curso] {course}: {total}")
    return 0


def cmd_ocr_classify(args: argparse.Namespace) -> int:
    output_root = Path(args.output_root).expanduser().resolve()
    book_ids = list(args.book_id or [])
    if args.first_index:
        book_ids.extend(first_book_ids_from_index(output_root, limit=int(args.first_index)))
    deduped_book_ids: list[str] = []
    for book_id in book_ids:
        if book_id and book_id not in deduped_book_ids:
            deduped_book_ids.append(book_id)
    if not deduped_book_ids:
        raise SystemExit("No hay book_id para OCR. Usa --book-id o --first-index.")
    records = records_by_book_id(output_root)
    missing = [book_id for book_id in deduped_book_ids if book_id not in records]
    if missing:
        raise SystemExit(f"book_id no encontrados en inventory.jsonl: {', '.join(missing)}")
    for index, book_id in enumerate(deduped_book_ids, start=1):
        record = records[book_id]
        print(f"[ocr] {index}/{len(deduped_book_ids)} {book_id} paginas={record.page_count}", flush=True)
        output = classify_book_pages_with_ocr(
            record=record,
            output_root=output_root,
            page_spec=args.pages,
            page_limit=args.page_limit,
            dpi=args.dpi,
            force_render=bool(args.force_render),
            force_ocr=bool(args.force_ocr),
            tesseract_cmd=args.tesseract_cmd,
            tessdata_dir=args.tessdata_dir,
            lang=args.lang,
            workers=args.workers,
        )
        counts = output["label_counts"]
        counts_text = ", ".join(f"{key}={int(counts.get(key, 0) or 0)}" for key in sorted(counts))
        print(f"[ok] {book_id} paginas={output['processed_pages_total']} {counts_text}", flush=True)
        print(f"[ok] ocr: {output['ocr_jsonl']}", flush=True)
    organize_vault(output_root)
    print(f"[ok] vault: {output_root}", flush=True)
    return 0


def cmd_themes(args: argparse.Namespace) -> int:
    output = export_book_themes(
        Path(args.output_root).expanduser().resolve(),
        book_ids=list(args.book_id or []),
        update_obsidian=not bool(args.no_obsidian),
    )
    summary = output["summary"]
    print(
        "[ok] themes "
        f"multi_tema={int(summary.get('multi_tema', 0) or 0)} "
        f"tema_unico={int(summary.get('tema_unico', 0) or 0)} "
        f"pendiente={int(summary.get('pendiente', 0) or 0)} "
        f"missing={int(summary.get('missing', 0) or 0)}",
        flush=True,
    )
    for row in output["books"]:
        print(
            f"[theme] {row['book_id']} status={row['status']} "
            f"themes={row['themes_total']} path={row.get('themes_json', '-')}",
            flush=True,
        )
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "inventory":
        return cmd_inventory(args)
    if args.command == "process":
        return cmd_process(args)
    if args.command == "vault":
        return cmd_vault(args)
    if args.command == "list-pdfs":
        return cmd_list_pdfs(args)
    if args.command == "ocr-classify":
        return cmd_ocr_classify(args)
    if args.command == "themes":
        return cmd_themes(args)
    parser.error(f"Comando no soportado: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
