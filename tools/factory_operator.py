from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:8765"
DEFAULT_TIMEOUT_S = 300


def _json_request(
    base_url: str,
    method: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    base = str(base_url or DEFAULT_BASE_URL).rstrip("/")
    url = f"{base}/{path.lstrip('/')}"
    clean_query = {key: str(value) for key, value in dict(query or {}).items() if value not in (None, "")}
    if clean_query:
        url = f"{url}?{urllib.parse.urlencode(clean_query)}"
    data = None
    headers = {"Accept": "application/json"}
    if method.upper() == "POST":
        data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=float(timeout_s or DEFAULT_TIMEOUT_S)) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"error": raw}
        raise SystemExit(f"HTTP {exc.code}: {json.dumps(payload, ensure_ascii=False)}") from exc
    return json.loads(raw or "{}")


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _context_query(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "instance_id": getattr(args, "instance_id", ""),
        "book_id": getattr(args, "book_id", ""),
        "db_name": getattr(args, "db_name", ""),
    }


def _context_body(args: argparse.Namespace) -> dict[str, Any]:
    return {
        key: value
        for key, value in _context_query(args).items()
        if value not in (None, "")
    }


def _record_ids(args: argparse.Namespace) -> list[str]:
    out: list[str] = []
    for raw in getattr(args, "record_ids", []) or []:
        for part in str(raw or "").split(","):
            item = part.strip()
            if item and item not in out:
                out.append(item)
    return out


def _read_json_file(path: str) -> Any:
    if not str(path or "").strip():
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_text_value(value: str = "", *, path: str = "") -> str:
    if str(path or "").strip():
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    return str(value or "")


def _compact_body(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "compact": not bool(getattr(args, "full_snapshot", False)),
        "include_summary": not bool(getattr(args, "no_summary", False)),
    }


def command_status(args: argparse.Namespace) -> None:
    query = {**_context_query(args), "job_id": args.job_id}
    _print_json(_json_request(args.base_url, "GET", "/api/automation/status", query=query))


def command_schema(args: argparse.Namespace) -> None:
    _print_json(_json_request(args.base_url, "GET", "/api/automation/schema", query=_context_query(args)))


def command_instances(args: argparse.Namespace) -> None:
    _print_json(_json_request(args.base_url, "GET", "/api/automation/instances", query=_context_query(args)))


def command_bootstrap(args: argparse.Namespace) -> None:
    _print_json(_json_request(args.base_url, "GET", "/api/bootstrap", query=_context_query(args)))


def command_summary(args: argparse.Namespace) -> None:
    _print_json(_json_request(args.base_url, "GET", "/api/summary", query=_context_query(args)))


def command_record(args: argparse.Namespace) -> None:
    query = {**_context_query(args), "record_id": args.record_id}
    _print_json(_json_request(args.base_url, "GET", "/api/record", query=query))


def command_pages_detect(args: argparse.Namespace) -> None:
    body = {
        **_context_body(args),
        **_compact_body(args),
        "pages": args.pages,
        "dpi": args.dpi,
        "confidence": args.confidence,
        "detector_model": args.detector_model,
        "replace_existing": args.replace_existing,
    }
    _print_json(_json_request(args.base_url, "POST", "/api/pages/detect", body=body))


def command_pages_save_boxes(args: argparse.Namespace) -> None:
    boxes = _read_json_file(args.boxes_file)
    if boxes is None:
        boxes = json.loads(args.boxes)
    body = {
        **_context_body(args),
        **_compact_body(args),
        "record_id": args.record_id,
        "boxes": boxes,
        "layout_mode": args.layout_mode,
        "reviewed": not args.not_reviewed,
        "reorder": args.reorder,
    }
    _print_json(_json_request(args.base_url, "POST", "/api/pages/boxes", body=body))


def command_pages_delete(args: argparse.Namespace) -> None:
    body = {
        **_context_body(args),
        **_compact_body(args),
        "record_id": args.record_id,
    }
    _print_json(_json_request(args.base_url, "POST", "/api/pages/delete", body=body))


def command_staging_materialize(args: argparse.Namespace) -> None:
    body = {**_context_body(args), **_compact_body(args)}
    _print_json(_json_request(args.base_url, "POST", "/api/staging/materialize", body=body))


def command_ocr_start(args: argparse.Namespace) -> None:
    body = {
        **_context_body(args),
        "record_id": args.record_id,
        "record_ids": _record_ids(args),
        "provider": args.provider,
        "curso": args.curso,
        "tema": args.tema,
        "start_n": args.start_n,
        "ocr_model": args.ocr_model,
        "figure_model": args.figure_model,
        "force_figure_model": not args.no_force_figure_model,
    }
    _print_json(_json_request(args.base_url, "POST", "/api/ocr/jobs/start", body=body))


def command_ocr_status(args: argparse.Namespace) -> None:
    query = {**_context_query(args), "job_id": args.job_id, "since_update": args.since_update}
    _print_json(_json_request(args.base_url, "GET", "/api/ocr/jobs/status", query=query))


def command_ocr_save_raw(args: argparse.Namespace) -> None:
    body = {
        **_context_body(args),
        **_compact_body(args),
        "record_id": args.record_id,
        "raw_ocr": _read_text_value(args.text, path=args.file),
        "force_review": args.force_review,
    }
    _print_json(_json_request(args.base_url, "POST", "/api/ocr/raw", body=body))


def command_segments_save(args: argparse.Namespace) -> None:
    boxes = _read_json_file(args.boxes_file)
    if boxes is None:
        boxes = json.loads(args.boxes)
    body = {
        **_context_body(args),
        **_compact_body(args),
        "record_id": args.record_id,
        "boxes": boxes,
    }
    _print_json(_json_request(args.base_url, "POST", "/api/ocr/segments/boxes", body=body))


def command_normalize_prepare(args: argparse.Namespace) -> None:
    body = {
        **_context_body(args),
        **_compact_body(args),
        "record_id": args.record_id,
        "record_ids": _record_ids(args),
    }
    _print_json(_json_request(args.base_url, "POST", "/api/normalize", body=body))


def command_normalize_ai_start(args: argparse.Namespace) -> None:
    body = {**_context_body(args), "record_id": args.record_id, "record_ids": _record_ids(args)}
    if args.record_ids:
        path = "/api/normalize/ai/jobs/start"
    else:
        path = "/api/normalize/ai"
    _print_json(_json_request(args.base_url, "POST", path, body=body))


def command_normalize_ai_status(args: argparse.Namespace) -> None:
    query = {**_context_query(args), "job_id": args.job_id, "since_update": args.since_update}
    _print_json(_json_request(args.base_url, "GET", "/api/normalize/ai/jobs/status", query=query))


def command_review_save(args: argparse.Namespace) -> None:
    normalized = _read_json_file(args.normalized_file)
    if normalized is None:
        normalized = {}
    if args.final_latex or args.final_latex_file:
        normalized["latex_rendered_item"] = _read_text_value(args.final_latex, path=args.final_latex_file)
    body = {
        **_context_body(args),
        **_compact_body(args),
        "record_id": args.record_id,
        "normalized": normalized,
        "notes": _read_text_value(args.notes, path=args.notes_file),
        "mark_ready": args.mark_ready,
        "defer_golden_sync": args.defer_golden_sync,
    }
    _print_json(_json_request(args.base_url, "POST", "/api/review/save", body=body))


def command_promotion_preview(args: argparse.Namespace) -> None:
    query = {**_context_query(args), "record_id": args.record_id}
    _print_json(_json_request(args.base_url, "GET", "/api/promotion", query=query))


def command_promotion_upload(args: argparse.Namespace) -> None:
    body = {
        **_context_body(args),
        "record_id": args.record_id,
        "record_ids": _record_ids(args),
        "db_name": args.db_name,
        "db_profile": args.db_profile,
        "dry_run": args.dry_run,
        "confirm": args.confirm,
    }
    _print_json(_json_request(args.base_url, "POST", "/api/promotion/upload", body=body))


def command_run(args: argparse.Namespace) -> None:
    body = {
        **_context_body(args),
        "steps": args.steps,
        "scope": args.scope,
        "record_ids": _record_ids(args),
        "concurrency": args.concurrency,
        "provider": args.provider,
        "curso": args.curso,
        "tema": args.tema,
        "ocr_model": args.ocr_model,
        "figure_model": args.figure_model,
        "limit": args.limit,
    }
    _print_json(_json_request(args.base_url, "POST", "/api/automation/queue", body=body))


def command_retry_errors(args: argparse.Namespace) -> None:
    body = {
        **_context_body(args),
        "steps": args.steps,
        "concurrency": args.concurrency,
        "provider": args.provider,
        "curso": args.curso,
        "tema": args.tema,
        "ocr_model": args.ocr_model,
        "figure_model": args.figure_model,
        "limit": args.limit,
    }
    _print_json(_json_request(args.base_url, "POST", "/api/automation/retry-errors", body=body))


def command_cancel(args: argparse.Namespace) -> None:
    body = {**_context_body(args), "job_id": args.job_id}
    _print_json(_json_request(args.base_url, "POST", "/api/automation/cancel", body=body))


def command_wait(args: argparse.Namespace) -> None:
    last: dict[str, Any] = {}
    started = time.monotonic()
    while True:
        payload = _json_request(
            args.base_url,
            "GET",
            "/api/automation/status",
            query={**_context_query(args), "job_id": args.job_id},
        )
        last = payload
        current = dict(payload.get("current_job") or {})
        if not current.get("running"):
            break
        if args.timeout and time.monotonic() - started > float(args.timeout):
            break
        message = str(current.get("message") or "")
        progress = str(current.get("current") or "0")
        total = str(current.get("total") or "0")
        print(f"{progress}/{total} {message}", file=sys.stderr)
        time.sleep(max(0.5, float(args.poll)))
    _print_json(last)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Control local de Biblioteca/Fabrica PDF por API.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="URL local de la app web.")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_context(command: argparse.ArgumentParser) -> None:
        command.add_argument("--instance-id", default="", help="ID de instancia de Biblioteca.")
        command.add_argument("--book-id", default="", help="ID del libro si la instancia aun no esta preparada.")
        command.add_argument("--db-name", default="", help="Nombre de la base local de Biblioteca.")

    status = sub.add_parser("status", help="Ver estado de automatizacion.")
    add_context(status)
    status.add_argument("--job-id", default="")
    status.set_defaults(func=command_status)

    schema = sub.add_parser("schema", help="Ver contrato de automatizacion.")
    add_context(schema)
    schema.set_defaults(func=command_schema)

    instances = sub.add_parser("instances", help="Listar libros/instancias operables.")
    add_context(instances)
    instances.set_defaults(func=command_instances)

    bootstrap = sub.add_parser("bootstrap", help="Snapshot completo de la instancia.")
    add_context(bootstrap)
    bootstrap.set_defaults(func=command_bootstrap)

    summary = sub.add_parser("summary", help="Resumen/timeline de la instancia.")
    add_context(summary)
    summary.set_defaults(func=command_summary)

    record = sub.add_parser("record", help="Detalle de un registro de staging.")
    add_context(record)
    record.add_argument("--record-id", required=True)
    record.set_defaults(func=command_record)

    run = sub.add_parser("run", help="Crear una cola de automatizacion.")
    add_context(run)
    run.add_argument("--steps", default="ocr", help="Pasos: ocr,prepare_review,normalize_ai.")
    run.add_argument("--scope", default="all", help="all, errors, missing_ocr, missing_final, needs_review o selected.")
    run.add_argument("--record-ids", nargs="*", default=[], help="IDs para scope selected.")
    run.add_argument("--concurrency", type=int, default=1)
    run.add_argument("--provider", default="hf")
    run.add_argument("--curso", default="SIN_CURSO")
    run.add_argument("--tema", default="SIN_TEMA")
    run.add_argument("--ocr-model", default="")
    run.add_argument("--figure-model", default="")
    run.add_argument("--limit", type=int, default=None)
    run.set_defaults(func=command_run)

    retry = sub.add_parser("retry-errors", help="Reprocesar solo registros con error.")
    add_context(retry)
    retry.add_argument("--steps", default="ocr")
    retry.add_argument("--concurrency", type=int, default=1)
    retry.add_argument("--provider", default="hf")
    retry.add_argument("--curso", default="SIN_CURSO")
    retry.add_argument("--tema", default="SIN_TEMA")
    retry.add_argument("--ocr-model", default="")
    retry.add_argument("--figure-model", default="")
    retry.add_argument("--limit", type=int, default=None)
    retry.set_defaults(func=command_retry_errors)

    cancel = sub.add_parser("cancel", help="Cancelar job de automatizacion.")
    add_context(cancel)
    cancel.add_argument("--job-id", default="")
    cancel.set_defaults(func=command_cancel)

    wait = sub.add_parser("wait", help="Esperar a que termine un job.")
    add_context(wait)
    wait.add_argument("--job-id", default="")
    wait.add_argument("--poll", type=float, default=1.0)
    wait.add_argument("--timeout", type=float, default=0.0)
    wait.set_defaults(func=command_wait)

    def add_compact(command: argparse.ArgumentParser) -> None:
        command.add_argument("--full-snapshot", action="store_true", help="Pedir snapshot completo en vez de respuesta compacta.")
        command.add_argument("--no-summary", action="store_true", help="No pedir resumen/timeline en respuestas compactas.")

    pages_detect = sub.add_parser("pages-detect", help="Detectar boxes en paginas PDF. Acepta rangos como 22-50.")
    add_context(pages_detect)
    add_compact(pages_detect)
    pages_detect.add_argument("--pages", required=True, help="Ejemplo: 1,3,22-50.")
    pages_detect.add_argument("--dpi", type=int, default=300)
    pages_detect.add_argument("--confidence", type=float, default=0.25)
    pages_detect.add_argument("--detector-model", default="")
    pages_detect.add_argument("--replace-existing", action="store_true")
    pages_detect.set_defaults(func=command_pages_detect)

    pages_boxes = sub.add_parser("pages-save-boxes", help="Guardar boxes de una pagina detectada.")
    add_context(pages_boxes)
    add_compact(pages_boxes)
    pages_boxes.add_argument("--record-id", required=True)
    pages_boxes.add_argument("--boxes", default="", help="JSON de boxes, ejemplo: [[1,2,30,40]].")
    pages_boxes.add_argument("--boxes-file", default="", help="Archivo JSON con boxes.")
    pages_boxes.add_argument("--layout-mode", default="auto")
    pages_boxes.add_argument("--not-reviewed", action="store_true")
    pages_boxes.add_argument("--reorder", action="store_true")
    pages_boxes.set_defaults(func=command_pages_save_boxes)

    pages_delete = sub.add_parser("pages-delete", help="Eliminar una pagina detectada.")
    add_context(pages_delete)
    add_compact(pages_delete)
    pages_delete.add_argument("--record-id", required=True)
    pages_delete.set_defaults(func=command_pages_delete)

    staging = sub.add_parser("staging-materialize", help="Materializar crops a staging.")
    add_context(staging)
    add_compact(staging)
    staging.set_defaults(func=command_staging_materialize)

    ocr_start = sub.add_parser("ocr-start", help="Arrancar cola OCR por record_ids.")
    add_context(ocr_start)
    ocr_start.add_argument("--record-id", default="")
    ocr_start.add_argument("--record-ids", nargs="*", default=[])
    ocr_start.add_argument("--provider", default="hf")
    ocr_start.add_argument("--curso", default="SIN_CURSO")
    ocr_start.add_argument("--tema", default="SIN_TEMA")
    ocr_start.add_argument("--start-n", type=int, default=1)
    ocr_start.add_argument("--ocr-model", default="")
    ocr_start.add_argument("--figure-model", default="")
    ocr_start.add_argument("--no-force-figure-model", action="store_true")
    ocr_start.set_defaults(func=command_ocr_start)

    ocr_status = sub.add_parser("ocr-status", help="Estado de cola OCR.")
    add_context(ocr_status)
    ocr_status.add_argument("--job-id", default="")
    ocr_status.add_argument("--since-update", type=int, default=0)
    ocr_status.set_defaults(func=command_ocr_status)

    ocr_raw = sub.add_parser("ocr-save-raw", help="Guardar OCR crudo editable.")
    add_context(ocr_raw)
    add_compact(ocr_raw)
    ocr_raw.add_argument("--record-id", required=True)
    ocr_raw.add_argument("--text", default="")
    ocr_raw.add_argument("--file", default="")
    ocr_raw.add_argument("--force-review", action="store_true")
    ocr_raw.set_defaults(func=command_ocr_save_raw)

    segments = sub.add_parser("segments-save", help="Guardar boxes de segmentacion grafica.")
    add_context(segments)
    add_compact(segments)
    segments.add_argument("--record-id", required=True)
    segments.add_argument("--boxes", default="", help="JSON de boxes.")
    segments.add_argument("--boxes-file", default="", help="Archivo JSON con boxes.")
    segments.set_defaults(func=command_segments_save)

    normalize_prepare = sub.add_parser("normalize-prepare", help="Preparar revision desde OCR crudo.")
    add_context(normalize_prepare)
    add_compact(normalize_prepare)
    normalize_prepare.add_argument("--record-id", default="")
    normalize_prepare.add_argument("--record-ids", nargs="*", default=[])
    normalize_prepare.set_defaults(func=command_normalize_prepare)

    normalize_ai = sub.add_parser("normalize-ai", help="Normalizar con IA un registro o lote.")
    add_context(normalize_ai)
    normalize_ai.add_argument("--record-id", default="")
    normalize_ai.add_argument("--record-ids", nargs="*", default=[])
    normalize_ai.set_defaults(func=command_normalize_ai_start)

    normalize_ai_status = sub.add_parser("normalize-ai-status", help="Estado de cola normalizador IA.")
    add_context(normalize_ai_status)
    normalize_ai_status.add_argument("--job-id", default="")
    normalize_ai_status.add_argument("--since-update", type=int, default=0)
    normalize_ai_status.set_defaults(func=command_normalize_ai_status)

    review = sub.add_parser("review-save", help="Guardar formato final revisado.")
    add_context(review)
    add_compact(review)
    review.add_argument("--record-id", required=True)
    review.add_argument("--normalized-file", default="", help="JSON normalizado completo.")
    review.add_argument("--final-latex", default="", help="Formato final LaTeX.")
    review.add_argument("--final-latex-file", default="", help="Archivo con formato final LaTeX.")
    review.add_argument("--notes", default="")
    review.add_argument("--notes-file", default="")
    review.add_argument("--mark-ready", action="store_true")
    review.add_argument("--defer-golden-sync", action="store_true")
    review.set_defaults(func=command_review_save)

    promotion_preview = sub.add_parser("promotion-preview", help="Ver candidato a BD para un registro.")
    add_context(promotion_preview)
    promotion_preview.add_argument("--record-id", required=True)
    promotion_preview.set_defaults(func=command_promotion_preview)

    promotion_upload = sub.add_parser("promotion-upload", help="Subir registros revisados a la BD local/interna.")
    add_context(promotion_upload)
    promotion_upload.add_argument("--record-id", default="")
    promotion_upload.add_argument("--record-ids", nargs="*", default=[])
    promotion_upload.add_argument("--db-profile", default="local_mirror")
    promotion_upload.add_argument("--dry-run", action="store_true")
    promotion_upload.add_argument("--confirm", action="store_true")
    promotion_upload.set_defaults(func=command_promotion_upload)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
