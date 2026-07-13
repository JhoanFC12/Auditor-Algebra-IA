import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _handoff_to_venv() -> int | None:
    venv_python = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        return None
    if str(os.environ.get("AUDITOR_SEGMENTATION_VENV_HANDOFF") or "").strip() == "1":
        return None
    try:
        if Path(sys.executable).resolve() == venv_python.resolve():
            return None
    except Exception:
        pass
    env = os.environ.copy()
    env["AUDITOR_SEGMENTATION_VENV_HANDOFF"] = "1"
    result = subprocess.run([str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]], cwd=str(REPO_ROOT), env=env)
    return int(result.returncode)


def _timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _coerce_positive_int(value: Any) -> int:
    try:
        parsed = int(value)
    except Exception:
        return 0
    return parsed if parsed > 0 else 0


def _normalize_ranges(items: list[dict[str, Any]]) -> list[dict[str, int]]:
    ranges: list[dict[str, int]] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        start_page = _coerce_positive_int(raw.get("page_start") or raw.get("start_page"))
        end_page = _coerce_positive_int(raw.get("page_end") or raw.get("end_page"))
        if start_page <= 0 or end_page <= 0:
            continue
        if end_page < start_page:
            start_page, end_page = end_page, start_page
        ranges.append({"page_start": start_page, "page_end": end_page})
    ranges.sort(key=lambda item: (item["page_start"], item["page_end"]))
    merged: list[dict[str, int]] = []
    for item in ranges:
        if not merged:
            merged.append(dict(item))
            continue
        current = merged[-1]
        if item["page_start"] <= current["page_end"] + 1:
            current["page_end"] = max(current["page_end"], item["page_end"])
            continue
        merged.append(dict(item))
    return merged


def _ranges_from_segments(config_snapshot: dict[str, Any], *, label: str) -> list[dict[str, int]]:
    rows = []
    for raw in list(config_snapshot.get("segments") or []):
        if not isinstance(raw, dict):
            continue
        if str(raw.get("label") or "").strip() != label:
            continue
        rows.append(raw)
    return _normalize_ranges(rows)


def _derive_operational_selection(instance: dict[str, Any]) -> dict[str, Any]:
    config_snapshot = dict(instance.get("config_snapshot") or {})
    selected_label = str(config_snapshot.get("selected_content_label") or "").strip()

    selected_ranges_raw = config_snapshot.get("selected_page_ranges")
    if isinstance(selected_ranges_raw, list):
        selected_ranges = _normalize_ranges([dict(item) for item in selected_ranges_raw if isinstance(item, dict)])
        if selected_ranges:
            return {"label": selected_label or "selected_page_ranges", "ranges": selected_ranges, "source": "selected_page_ranges"}

    if selected_label:
        selected_ranges = _ranges_from_segments(config_snapshot, label=selected_label)
        if selected_ranges:
            return {"label": selected_label, "ranges": selected_ranges, "source": "selected_content_label"}

    proposed_ranges = _ranges_from_segments(config_snapshot, label="problemas_propuestos")
    if proposed_ranges:
        return {"label": "problemas_propuestos", "ranges": proposed_ranges, "source": "segments.problemas_propuestos"}

    start_page = _coerce_positive_int(config_snapshot.get("page_start"))
    end_page = _coerce_positive_int(config_snapshot.get("page_end"))
    if start_page > 0 and end_page > 0:
        if end_page < start_page:
            start_page, end_page = end_page, start_page
        return {
            "label": selected_label or "page_range",
            "ranges": [{"page_start": start_page, "page_end": end_page}],
            "source": "config_snapshot.page_range",
        }

    raise ValueError(f"No se pudo derivar rango operativo para la instancia {instance.get('tipo') or ''}.")


def _expand_pages(ranges: list[dict[str, int]]) -> list[int]:
    pages: list[int] = []
    for item in ranges:
        start_page = int(item["page_start"])
        end_page = int(item["page_end"])
        pages.extend(range(start_page, end_page + 1))
    return sorted(dict.fromkeys(pages))


def _display_ranges(ranges: list[dict[str, int]]) -> str:
    parts: list[str] = []
    for item in ranges:
        start_page = int(item["page_start"])
        end_page = int(item["page_end"])
        parts.append(str(start_page) if start_page == end_page else f"{start_page}-{end_page}")
    return ",".join(parts)


def _prepare_tasks(db_name: str, book_codes: list[str]) -> list[dict[str, Any]]:
    import main

    main._apply_db_profile("local_mirror")
    from modulos.modulo9_organizador_libros.controlador_organizador_libros import BookProgressController

    controller = BookProgressController()
    wanted = {str(code or "").strip() for code in book_codes if str(code or "").strip()}
    books = [dict(row) for row in controller.listar_libros(db_name) if str(row.get("codigo") or "").strip() in wanted]
    tasks: list[dict[str, Any]] = []
    for book in books:
        book_id = _coerce_positive_int(book.get("id"))
        if book_id <= 0:
            continue
        instances = [dict(row) for row in controller.listar_instancias_libro(db_name, book_id)]
        for instance in instances:
            selection = _derive_operational_selection(instance)
            tasks.append(
                {
                    "db_name": db_name,
                    "book": book,
                    "instance": instance,
                    "selection": selection,
                }
            )
    return tasks


def _run_single_task(task: dict[str, Any], *, dpi: int, confidence: float) -> dict[str, Any]:
    from modulos.instance_factory.pipeline import InstancePdfPipelineService

    book = dict(task["book"])
    instance = dict(task["instance"])
    selection = dict(task["selection"])
    ranges = [dict(item) for item in list(selection.get("ranges") or []) if isinstance(item, dict)]
    pages = _expand_pages(ranges)
    if not pages:
        raise ValueError(f"La instancia {instance.get('tipo') or ''} no tiene paginas operativas.")

    started_at = time.time()
    service = InstancePdfPipelineService.from_library_instance(book, instance, db_name=str(task.get("db_name") or ""))
    detected_pages = service.detect_pdf_pages(
        pages,
        dpi=int(dpi),
        confidence=float(confidence),
        replace_existing=True,
    )
    staging_records = service.materialize_crops_to_staging(detected_pages)
    summary = service.build_instance_summary(pages=detected_pages, records=staging_records)
    return {
        "status": "ok",
        "book_id": int(book.get("id") or 0),
        "book_code": str(book.get("codigo") or ""),
        "book_title": str(book.get("titulo") or ""),
        "instance_id": int(instance.get("id") or 0),
        "instance_type": str(instance.get("tipo") or ""),
        "instance_title": str(instance.get("titulo_practica") or instance.get("nombre_instancia") or ""),
        "selected_label": str(selection.get("label") or ""),
        "selection_source": str(selection.get("source") or ""),
        "page_ranges": ranges,
        "page_ranges_display": _display_ranges(ranges),
        "pages_total_selected": len(pages),
        "pages_detected_total": len(detected_pages),
        "staging_root": str(service.staging.root),
        "summary": summary,
        "elapsed_seconds": round(time.time() - started_at, 3),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ejecuta segmentacion de problemas por lotes para instancias de Biblioteca.")
    parser.add_argument("--db-name", default="mathcontentstudio_local_mirror")
    parser.add_argument("--book-code", action="append", default=[], help="Codigo exacto del libro. Repite la bandera para varios.")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--dpi", type=int, default=220)
    parser.add_argument("--confidence", type=float, default=0.12)
    parser.add_argument(
        "--report-path",
        default="",
        help="Ruta JSON de salida. Por defecto usa .cache/book_catalog_sync/problem_segmentation_<timestamp>.json",
    )
    return parser.parse_args()


def main() -> int:
    handoff_code = _handoff_to_venv()
    if handoff_code is not None:
        return int(handoff_code)

    args = parse_args()
    book_codes = [str(code or "").strip() for code in list(args.book_code or []) if str(code or "").strip()]
    if not book_codes:
        raise SystemExit("Debes indicar al menos un --book-code.")

    tasks = _prepare_tasks(str(args.db_name or "").strip(), book_codes)
    found_codes = {str(task["book"].get("codigo") or "").strip() for task in tasks}
    missing_codes = sorted(code for code in book_codes if code not in found_codes)
    if not tasks:
        raise SystemExit("No se encontraron instancias operables para los libros solicitados.")

    concurrency = max(1, min(int(args.concurrency or 1), len(tasks)))
    results: list[dict[str, Any]] = []
    started_at = time.time()
    with ProcessPoolExecutor(max_workers=concurrency) as executor:
        future_map = {
            executor.submit(_run_single_task, task, dpi=int(args.dpi), confidence=float(args.confidence)): task
            for task in tasks
        }
        for future in as_completed(future_map):
            task = future_map[future]
            instance = dict(task["instance"])
            try:
                results.append(future.result())
            except Exception as exc:
                results.append(
                    {
                        "status": "error",
                        "book_id": int(task["book"].get("id") or 0),
                        "book_code": str(task["book"].get("codigo") or ""),
                        "book_title": str(task["book"].get("titulo") or ""),
                        "instance_id": int(instance.get("id") or 0),
                        "instance_type": str(instance.get("tipo") or ""),
                        "instance_title": str(instance.get("titulo_practica") or instance.get("nombre_instancia") or ""),
                        "error": str(exc),
                    }
                )

    results.sort(key=lambda item: (str(item.get("book_code") or ""), str(item.get("instance_type") or "")))
    ok_total = sum(1 for item in results if item.get("status") == "ok")
    error_total = sum(1 for item in results if item.get("status") != "ok")
    payload = {
        "schema_version": "library_problem_segmentation_batch_v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "db_name": str(args.db_name or "").strip(),
        "book_codes_requested": book_codes,
        "book_codes_missing": missing_codes,
        "concurrency": concurrency,
        "dpi": int(args.dpi),
        "confidence": float(args.confidence),
        "elapsed_seconds": round(time.time() - started_at, 3),
        "summary": {
            "tasks_total": len(tasks),
            "ok_total": ok_total,
            "error_total": error_total,
        },
        "results": results,
    }

    default_report = Path(".cache") / "book_catalog_sync" / f"problem_segmentation_{_timestamp_slug()}.json"
    report_path = Path(str(args.report_path or default_report)).expanduser()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report_path": str(report_path), **payload["summary"]}, ensure_ascii=False, indent=2))
    return 0 if error_total == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
