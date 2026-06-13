from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from database.problem_origins import TAG_EXAMEN_RE, ensure_problem_origin_schema, normalize_origin_code

from .models import InstancePipelineContext, StageStatus, StagingProblemRecord, utc_now_text
from .staging import InstanceStagingStore


REPORT_SCHEMA_VERSION = "pdf_factory_db_promotion_report_v1"
TRANSIENT_PROMOTION_SQLSTATES = {"40P01", "40001", "55P03"}
PROMOTION_ROW_MAX_ATTEMPTS = 3
ITEM_NUM_RE = re.compile(r"\\item\s*\[\s*\\textbf\s*\{\s*(\d+)\s*\.?\s*\}\s*\]")
BRACKET_TAG_RE = re.compile(r"\[\[\s*([^\]]+?)\s*\]\]")
TAG_CURSO_RE = re.compile(r"\[\[\s*curso\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)
TAG_TEMA_RE = re.compile(r"\[\[\s*tema\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)
TAG_SUBTEMA_RE = re.compile(r"\[\[\s*subtema\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)
TAG_CLAVE_RE = re.compile(r"\[\[\s*clave\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)
TAG_ESTADO_RE = re.compile(r"\[\[\s*estado\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)
TAG_SOLUCION_RE = re.compile(r"\[\[\s*solucion(?:ario)?\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)
TAG_IMAGEN_RE = re.compile(r"\[\[\s*imagen\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)
OPTION_LABEL_RE = re.compile(r"(?<![A-Za-z0-9])([A-F])\)", re.IGNORECASE)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _parsear_numero_original(item_latex: str) -> int | None:
    match = ITEM_NUM_RE.search(item_latex or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _normalizar_item_una_linea(item_latex: str) -> str:
    text = (item_latex or "").replace("\r\n", "\n").replace("\r", "\n")
    text = " ".join(part.strip() for part in text.split("\n") if part.strip())
    return re.sub(r"\s+", " ", text).strip()


def _first_tag(raw: str, pattern: re.Pattern[str]) -> str:
    match = pattern.search(raw or "")
    return str(match.group(1) if match else "").strip()


def _extract_item_storage_fields(item_latex: str) -> dict[str, str]:
    raw = str(item_latex or "")
    estado_raw = _first_tag(raw, TAG_ESTADO_RE)
    estado_norm = "Sin revisar"
    normalized_state = str(estado_raw or "").strip().lower().replace(" ", "_")
    if normalized_state in {"consistente", "bien_planteado"}:
        estado_norm = "Consistente"
    elif normalized_state in {"inconsistente", "mal_planteado", "ambiguo", "ambigua"}:
        estado_norm = "Inconsistente"
    elif normalized_state in {"sin_revisar", "pendiente", "pendiente_revision"}:
        estado_norm = "Sin revisar"

    respuesta = _first_tag(raw, TAG_CLAVE_RE)
    option_labels = {match.group(1).upper() for match in OPTION_LABEL_RE.finditer(raw)}
    tipo = "opcion_multiple" if respuesta or {"A", "B"}.issubset(option_labels) else "abierto"
    clean = BRACKET_TAG_RE.sub(" ", raw)
    clean = re.sub(r"\s+", " ", clean).strip()
    return {
        "clean_item_latex": clean,
        "curso": _first_tag(raw, TAG_CURSO_RE),
        "tema": _first_tag(raw, TAG_TEMA_RE),
        "subtema": _first_tag(raw, TAG_SUBTEMA_RE),
        "respuesta_correcta": respuesta,
        "tipo_problema": tipo,
        "consistencia_matematica": estado_norm,
        "ruta_imagen_solucion": _first_tag(raw, TAG_SOLUCION_RE),
        "examen": _first_tag(raw, TAG_EXAMEN_RE),
    }


def _problem_instance_column_name(cols: set[str]) -> str | None:
    if "codigo_instancia" in cols:
        return "codigo_instancia"
    if "instancia_tipo" in cols:
        return "instancia_tipo"
    return None


def _context_db_name(context: InstancePipelineContext, db_name: str = "") -> str:
    return _clean_text(db_name) or _clean_text(context.db_name)


def _archivo_origen(context: InstancePipelineContext) -> str:
    pdf = _clean_text(context.pdf_path)
    if pdf:
        return Path(pdf).name
    label = " / ".join(part for part in (_clean_text(context.book_code), _clean_text(context.instance_type)) if part)
    return label or "fabrica_pdf"


def _record_final_latex(record: StagingProblemRecord) -> str:
    normalized = dict(record.normalized or {})
    return _clean_text(normalized.get("latex_rendered_item"))


def _image_markers(item_latex: str) -> list[str]:
    markers: list[str] = []
    seen: set[str] = set()
    for match in TAG_IMAGEN_RE.finditer(str(item_latex or "")):
        marker = _clean_text(match.group(1))
        if not marker:
            continue
        key = marker.lower()
        if key in seen:
            continue
        seen.add(key)
        markers.append(marker)
    return markers


def _safe_image_marker(value: Any) -> str:
    raw = _clean_text(value)
    if not raw:
        return "img"
    candidate = Path(raw).stem if Path(raw).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"} else raw
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "_", candidate).strip("._-")
    return candidate[:100] or "img"


def _canonical_image_dir(context: InstancePipelineContext) -> Path:
    return context.staging_root() / "db_images"


def _is_continuation(record: StagingProblemRecord) -> bool:
    normalized = dict(record.normalized or {})
    continuation = normalized.get("continuacion") if isinstance(normalized.get("continuacion"), dict) else {}
    return bool(
        continuation.get("es_continuacion")
        or continuation.get("fusionar_con_anterior")
        or _clean_text(record.raw_ocr).lower().startswith("[cont.")
    )


def _continuation_records(parent: StagingProblemRecord, all_records: list[StagingProblemRecord]) -> list[StagingProblemRecord]:
    normalized = dict(parent.normalized or {})
    fused = normalized.get("continuaciones_fusionadas") if isinstance(normalized.get("continuaciones_fusionadas"), list) else []
    wanted_ids = {
        _clean_text(item.get("record_id"))
        for item in fused
        if isinstance(item, dict) and _clean_text(item.get("record_id"))
    }
    by_id = {_clean_text(row.record_id): row for row in all_records}
    out: list[StagingProblemRecord] = []
    seen: set[str] = set()

    def add(row: StagingProblemRecord | None) -> None:
        if row is None or not _is_continuation(row):
            return
        key = _clean_text(row.record_id)
        if not key or key in seen:
            return
        out.append(row)
        seen.add(key)

    for record_id in sorted(wanted_ids):
        add(by_id.get(record_id))

    parent_index = next((index for index, row in enumerate(all_records) if _clean_text(row.record_id) == _clean_text(parent.record_id)), -1)
    if parent_index >= 0:
        for row in all_records[parent_index + 1 :]:
            if not _is_continuation(row):
                break
            add(row)
    return out


def _source_image_paths(record: StagingProblemRecord, all_records: list[StagingProblemRecord]) -> list[str]:
    paths: list[str] = []

    def add(raw: Any) -> None:
        value = _clean_text(raw)
        if value and value not in paths:
            paths.append(value)

    def add_record_images(row: StagingProblemRecord) -> None:
        figure = dict(row.figure_segmentation or {})
        segments = figure.get("segments") if isinstance(figure.get("segments"), list) else []
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            add(segment.get("image_path") or segment.get("file_path") or segment.get("path"))
        final_latex = _record_final_latex(row)
        normalized = dict(row.normalized or {})
        has_image_tag = "[[Imagen=" in final_latex or bool(normalized.get("tiene_grafico")) or bool(figure.get("segments_total"))
        if has_image_tag and not paths:
            add(row.crop_path)

    add_record_images(record)
    for continuation in _continuation_records(record, all_records):
        add_record_images(continuation)
    return paths


def _canonical_image_markers(final_latex: str, image_count: int, numero_original: int) -> list[str]:
    markers = [_safe_image_marker(marker) for marker in _image_markers(final_latex)]
    markers = [marker for marker in markers if marker]
    if not markers and image_count > 0:
        markers = [f"img-{int(numero_original)}"]
    while len(markers) < image_count:
        base = markers[0] if markers else f"img-{int(numero_original)}"
        markers.append(f"{base}-{len(markers) + 1}")

    out: list[str] = []
    counts: dict[str, int] = {}
    for raw_marker in markers[:image_count]:
        marker = _safe_image_marker(raw_marker)
        key = marker.lower()
        counts[key] = counts.get(key, 0) + 1
        if counts[key] > 1:
            marker = f"{marker}-{counts[key]}"
        out.append(marker)
    return out


def _image_paths(
    record: StagingProblemRecord,
    all_records: list[StagingProblemRecord],
    *,
    context: InstancePipelineContext,
    final_latex: str,
    numero_original: int,
    materialize_images: bool,
) -> list[str]:
    source_paths = _source_image_paths(record, all_records)
    if not source_paths:
        return []
    markers = _canonical_image_markers(final_latex, len(source_paths), numero_original)
    if not materialize_images:
        return list(source_paths)

    target_dir = _canonical_image_dir(context)
    target_dir.mkdir(parents=True, exist_ok=True)
    stored: list[str] = []
    for index, source_raw in enumerate(source_paths):
        source = Path(source_raw)
        marker = markers[index] if index < len(markers) else f"img-{int(numero_original)}-{index + 1}"
        suffix = source.suffix if source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"} else ".png"
        target = target_dir / f"{marker}{suffix}"
        try:
            if source.exists() and source.is_file():
                try:
                    if source.resolve() != target.resolve():
                        shutil.copy2(str(source), str(target))
                except FileNotFoundError:
                    shutil.copy2(str(source), str(target))
                stored.append(str(target))
                continue
        except Exception:
            pass
        stored.append(str(source_raw))
    return stored


def _origin_code(context: InstancePipelineContext) -> str:
    raw = normalize_origin_code(f"{context.book_code}_{context.instance_type}")
    if len(raw) <= 150:
        return raw
    digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"{raw[:133].rstrip('_')}_{digest}"


def _db_error_code(exc: Exception) -> str:
    code = str(getattr(exc, "pgcode", "") or "").strip()
    if code:
        return code
    diag = getattr(exc, "diag", None)
    return str(getattr(diag, "sqlstate", "") or "").strip()


def _is_transient_promotion_error(exc: Exception) -> bool:
    return _db_error_code(exc) in TRANSIENT_PROMOTION_SQLSTATES


def _retry_delay_seconds(attempt: int) -> float:
    return min(0.25 * max(int(attempt), 1), 1.0)


def _lock_factory_origin(cur, context: InstancePipelineContext) -> None:
    # Serializes uploads for the same book/instance without blocking other origins.
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s)::bigint);", (f"pdf_factory_origin:{_origin_code(context)}",))


def _upsert_factory_origin(
    conn,
    *,
    context: InstancePipelineContext,
    problem_id: int,
    record: StagingProblemRecord,
    numero_original: int,
) -> int:
    source = dict(record.source or {})
    metadata = {
        "schema_version": "pdf_factory_problem_origin_metadata_v1",
        "record_id": record.record_id,
        "crop_id": record.crop_id,
        "crop_path": record.crop_path,
        "page_number": source.get("page_number") or source.get("source_page_number"),
        "bbox_px": source.get("bbox_px"),
        "staging_root": str(context.staging_root()),
    }
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO origenes (
                tipo_origen, codigo, nombre, proyecto, libro, instancia, pdf_path, session_path, metadata_json
            )
            VALUES ('libro_escaneado', %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (codigo) DO UPDATE
            SET nombre = EXCLUDED.nombre,
                proyecto = EXCLUDED.proyecto,
                libro = EXCLUDED.libro,
                instancia = EXCLUDED.instancia,
                pdf_path = EXCLUDED.pdf_path,
                session_path = EXCLUDED.session_path,
                metadata_json = origenes.metadata_json || EXCLUDED.metadata_json,
                updated_at = NOW()
            RETURNING id;
            """,
            (
                _origin_code(context),
                " / ".join(part for part in (_clean_text(context.project_name), _clean_text(context.instance_type)) if part)
                or _origin_code(context),
                _clean_text(context.project_name),
                _clean_text(context.book_code),
                _clean_text(context.instance_type),
                _clean_text(context.pdf_path),
                _clean_text(context.session_path),
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        origin_id = int(cur.fetchone()[0])
        cur.execute(
            """
            INSERT INTO problema_origen (problema_id, origen_id, numero_original, orden, pagina, bloque, metadata_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (problema_id, origen_id) DO UPDATE
            SET numero_original = EXCLUDED.numero_original,
                orden = EXCLUDED.orden,
                pagina = EXCLUDED.pagina,
                bloque = EXCLUDED.bloque,
                metadata_json = problema_origen.metadata_json || EXCLUDED.metadata_json;
            """,
            (
                int(problem_id),
                int(origin_id),
                int(numero_original),
                int(source.get("source_order") or source.get("box_index") or numero_original),
                source.get("page_number") or source.get("source_page_number"),
                _clean_text(record.record_id),
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        return origin_id
    finally:
        cur.close()


def build_problem_payload(
    record: StagingProblemRecord,
    context: InstancePipelineContext,
    *,
    controller: Any | None = None,
    all_records: list[StagingProblemRecord] | None = None,
    materialize_images: bool = True,
) -> dict[str, Any]:
    all_records = list(all_records or [record])
    final_latex = _record_final_latex(record)
    if not final_latex:
        raise ValueError("missing:final_latex")
    if controller is not None:
        metadata = controller._extract_item_storage_fields(final_latex)
        item_norm = controller.normalizar_item_una_linea(metadata["clean_item_latex"])
        numero = controller.parsear_numero_original(item_norm)
    else:
        metadata = _extract_item_storage_fields(final_latex)
        item_norm = _normalizar_item_una_linea(metadata["clean_item_latex"])
        numero = _parsear_numero_original(item_norm)
    if not numero:
        raise ValueError("invalid:numero_original")
    imagenes = _image_paths(
        record,
        all_records,
        context=context,
        final_latex=final_latex,
        numero_original=int(numero),
        materialize_images=materialize_images,
    )
    image_base_dir = _canonical_image_dir(context) if imagenes and materialize_images else None
    return {
        "numero_original": int(numero),
        "archivo_origen": _archivo_origen(context),
        "enunciado_latex": item_norm,
        "imagenes": imagenes,
        "ruta_carpeta": str(image_base_dir or (Path(record.crop_path).parent if _clean_text(record.crop_path) else _clean_text(context.workspace_dir))),
        "consistencia_matematica": _clean_text(metadata.get("consistencia_matematica")) or "Sin revisar",
        "curso": _clean_text(metadata.get("curso")),
        "tema": _clean_text(metadata.get("tema")),
        "subtema": _clean_text(metadata.get("subtema")),
        "respuesta_correcta": _clean_text(metadata.get("respuesta_correcta")).upper(),
        "tipo_problema": _clean_text(metadata.get("tipo_problema")) or "opcion_multiple",
        "soluciones": [],
        "libro_codigo": _clean_text(context.book_code),
        "instancia_tipo": _clean_text(context.instance_type),
        "record_id": record.record_id,
        "crop_id": record.crop_id,
    }


def _insert_problem(cur, payload: dict[str, Any], cols: set[str]) -> int:
    fields = ["numero_original", "archivo_origen", "enunciado_latex"]
    placeholders = ["%s", "%s", "%s"]
    params: list[Any] = [payload["numero_original"], payload["archivo_origen"], payload["enunciado_latex"]]

    def add(column: str, value: Any, placeholder: str = "%s") -> None:
        fields.append(column)
        placeholders.append(placeholder)
        params.append(value)

    if "imagenes" in cols:
        add("imagenes", payload["imagenes"] or None)
    if "ruta_carpeta" in cols:
        add("ruta_carpeta", payload["ruta_carpeta"])
    if "consistencia_matematica" in cols:
        add("consistencia_matematica", payload["consistencia_matematica"])
    if "curso" in cols:
        add("curso", payload["curso"])
    if "tema" in cols:
        add("tema", payload["tema"])
    if "subtema" in cols:
        add("subtema", payload["subtema"])
    if "respuesta_correcta" in cols:
        add("respuesta_correcta", payload["respuesta_correcta"])
    elif "respuesta" in cols:
        add("respuesta", payload["respuesta_correcta"])
    if "tipo_problema" in cols:
        add("tipo_problema", payload["tipo_problema"])
    if "soluciones" in cols:
        add("soluciones", json.dumps(payload["soluciones"], ensure_ascii=False), "%s::jsonb")
    if "libro_codigo" in cols:
        add("libro_codigo", payload["libro_codigo"])
    instance_col = _problem_instance_column_name(cols)
    if instance_col:
        add(instance_col, payload["instancia_tipo"])
    cur.execute(
        f"INSERT INTO problemas ({', '.join(fields)}) VALUES ({', '.join(placeholders)}) RETURNING id;",
        tuple(params),
    )
    return int(cur.fetchone()[0])


def _update_problem(
    cur,
    *,
    problem_id: int,
    payload: dict[str, Any],
    cols: set[str],
) -> int:
    parts = ["enunciado_latex = %s"]
    params: list[Any] = [payload["enunciado_latex"]]

    def add(column: str, value: Any, placeholder: str = "%s") -> None:
        parts.append(f"{column} = {placeholder}")
        params.append(value)

    if "archivo_origen" in cols:
        add("archivo_origen", payload["archivo_origen"])
    if "imagenes" in cols:
        add("imagenes", payload["imagenes"] or None)
    if "ruta_carpeta" in cols:
        add("ruta_carpeta", payload["ruta_carpeta"])
    if "consistencia_matematica" in cols:
        add("consistencia_matematica", payload["consistencia_matematica"])
    if "curso" in cols:
        add("curso", payload["curso"])
    if "tema" in cols:
        add("tema", payload["tema"])
    if "subtema" in cols:
        add("subtema", payload["subtema"])
    if "respuesta_correcta" in cols:
        add("respuesta_correcta", payload["respuesta_correcta"])
    elif "respuesta" in cols:
        add("respuesta", payload["respuesta_correcta"])
    if "tipo_problema" in cols:
        add("tipo_problema", payload["tipo_problema"])
    if "soluciones" in cols:
        add("soluciones", json.dumps(payload["soluciones"], ensure_ascii=False), "%s::jsonb")
    if "libro_codigo" in cols:
        add("libro_codigo", payload["libro_codigo"])
    instance_col = _problem_instance_column_name(cols)
    if instance_col:
        add(instance_col, payload["instancia_tipo"])
    params.append(int(problem_id))
    cur.execute(f"UPDATE problemas SET {', '.join(parts)} WHERE id = %s;", tuple(params))
    return int(problem_id)


def promote_staging_records_to_db(
    staging: InstanceStagingStore,
    context: InstancePipelineContext,
    *,
    db_name: str = "",
    db_profile: str = "local_mirror",
    record_ids: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    target_db = _context_db_name(context, db_name)
    if not target_db:
        raise ValueError("db_name es requerido para subir a BD.")
    selected_ids = [_clean_text(item) for item in list(record_ids or []) if _clean_text(item)]
    all_records = staging.load_records()
    by_id = {_clean_text(row.record_id): row for row in all_records}
    records = [by_id[item] for item in selected_ids if item in by_id] if selected_ids else all_records
    missing_ids = [item for item in selected_ids if item not in by_id]

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "created_at": utc_now_text(),
        "db_name": target_db,
        "db_profile": db_profile,
        "dry_run": bool(dry_run),
        "total": len(records),
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
        "missing": len(missing_ids),
        "rows": [],
        "policy": {
            "automatic_insert": False,
            "explicit_user_action_required": True,
            "deletes_obsolete_rows": False,
        },
    }
    for missing in missing_ids:
        report["rows"].append({"record_id": missing, "status": "missing", "message": "registro no encontrado"})

    for row in records:
        candidate = staging.build_promotion_candidate(row.record_id)
        blocking = list(candidate.get("blocking_issues") or [])
        if blocking:
            report["skipped"] += 1
            report["rows"].append({"record_id": row.record_id, "status": "skipped", "blocking_issues": blocking})
            continue
        try:
            payload = build_problem_payload(row, context, all_records=all_records, materialize_images=False)
        except Exception as exc:
            report["errors"] += 1
            report["rows"].append({"record_id": row.record_id, "status": "error", "message": str(exc)})
            continue
        if dry_run:
            report["rows"].append(
                {
                    "record_id": row.record_id,
                    "status": "ready",
                    "numero_original": payload["numero_original"],
                    "archivo_origen": payload["archivo_origen"],
                    "imagenes": len(payload["imagenes"]),
                }
            )
            continue

    if dry_run:
        return report

    from database.connection import DatabaseManager
    from modulos.modulo0_transcriptor.controlador_transcriptor import TranscriptorController

    db = DatabaseManager.from_profile(db_profile, db_name=target_db)
    controller = TranscriptorController()
    controller.db = db
    conn = db.get_connection(target_db)
    try:
        try:
            controller._asegurar_tabla_problemas(conn)
            ensure_problem_origin_schema(conn)
            conn.commit()
            cols = controller._obtener_columnas_problemas(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        for row in records:
            if any(existing.get("record_id") == row.record_id and existing.get("status") in {"skipped", "error"} for existing in report["rows"]):
                continue
            try:
                payload = build_problem_payload(row, context, controller=controller, all_records=all_records)
                problem_id = 0
                origin_id = 0
                operation = ""
                attempts = 0
                while True:
                    attempts += 1
                    cur = conn.cursor()
                    try:
                        _lock_factory_origin(cur, context)
                        existing_id = controller._find_existing_problem_id(
                            cur,
                            numero=int(payload["numero_original"]),
                            archivo_origen=str(payload["archivo_origen"]),
                            libro_codigo=str(payload["libro_codigo"]),
                            instancia_tipo=str(payload["instancia_tipo"]),
                            cols=cols,
                        )
                        if existing_id is None:
                            problem_id = _insert_problem(cur, payload, cols)
                            operation = "inserted"
                        else:
                            problem_id = _update_problem(cur, problem_id=int(existing_id), payload=payload, cols=cols)
                            operation = "updated"
                        origin_id = _upsert_factory_origin(
                            conn,
                            context=context,
                            problem_id=int(problem_id),
                            record=row,
                            numero_original=int(payload["numero_original"]),
                        )
                        conn.commit()
                        if operation == "inserted":
                            report["inserted"] += 1
                        else:
                            report["updated"] += 1
                        break
                    except Exception as exc:
                        conn.rollback()
                        if attempts < PROMOTION_ROW_MAX_ATTEMPTS and _is_transient_promotion_error(exc):
                            time.sleep(_retry_delay_seconds(attempts))
                            continue
                        raise
                    finally:
                        cur.close()
                row.audit = {
                    **dict(row.audit or {}),
                    "db_promotion": {
                        "schema_version": "pdf_factory_db_promotion_audit_v1",
                        "uploaded_at": utc_now_text(),
                        "db_name": target_db,
                        "db_profile": db_profile,
                        "problem_id": int(problem_id),
                        "origin_id": int(origin_id),
                        "operation": operation,
                        "attempts": int(attempts),
                        "numero_original": int(payload["numero_original"]),
                    },
                }
                row.artifacts = {
                    **dict(row.artifacts or {}),
                    "db_problem_id": int(problem_id),
                    "db_origin_id": int(origin_id),
                    "db_promotion_updated_at": row.audit["db_promotion"]["uploaded_at"],
                }
                row.touch()
                staging.upsert_record(row)
                report["rows"].append(
                    {
                        "record_id": row.record_id,
                        "status": operation,
                        "problem_id": int(problem_id),
                        "origin_id": int(origin_id),
                        "numero_original": int(payload["numero_original"]),
                    }
                )
            except Exception as exc:
                conn.rollback()
                report["errors"] += 1
                report["rows"].append(
                    {
                        "record_id": row.record_id,
                        "status": "error",
                        "message": str(exc),
                        "db_error_code": _db_error_code(exc),
                    }
                )
    finally:
        conn.close()
    return report
