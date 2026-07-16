from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from database.problem_origins import TAG_EXAMEN_RE, ensure_problem_origin_schema, normalize_origin_code
from utils.project_layout import project_dirs

from .continuations import continuation_flags_enabled, has_continuation_marker
from .models import InstancePipelineContext, StageStatus, StagingProblemRecord, utc_now_text
from .staging import InstanceStagingStore


REPORT_SCHEMA_VERSION = "pdf_factory_db_promotion_report_v1"
VISUAL_SOLUTION_GROUP_SCHEMA_VERSION = "canonical_visual_solution_group_v1"
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
CONFIRMED_REVIEW_STATES = {"confirm", "confirmed", "human_confirmed", "approved", "accepted", "ready_for_db"}
PROMOTABLE_BUNDLE_STATES = {"human_confirmed", "ready_for_db"}


class BundlePreflightError(ValueError):
    """A reviewed problem-solution bundle cannot be promoted safely."""

    def __init__(self, issues: list[str] | tuple[str, ...] | str) -> None:
        raw_issues = [issues] if isinstance(issues, str) else list(issues)
        self.issues = [str(item).strip() for item in raw_issues if str(item).strip()]
        super().__init__("; ".join(self.issues) or "invalid:problem_solution_bundle")


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


def _canonical_solution_dir(context: InstancePipelineContext) -> Path:
    workspace = _clean_text(context.workspace_dir)
    if not workspace:
        raise BundlePreflightError("bundle:managed_solution_workspace_required")
    return project_dirs(Path(workspace), context.normalized_instance_type)["solutions_dir"] / "db_solutions"


def _managed_solution_group_id_values(book_code: Any, instance_type: Any, record_id: Any) -> str:
    seed = "|".join((_clean_text(book_code), _clean_text(instance_type), _clean_text(record_id)))
    return f"psg_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:20]}"


def _managed_solution_group_id(context: InstancePipelineContext, record: StagingProblemRecord) -> str:
    return _managed_solution_group_id_values(context.book_code, context.instance_type, record.record_id)


def _file_sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_existing_file(left: Path, right: Path) -> bool:
    try:
        if left.resolve() == right.resolve():
            return True
    except Exception:
        pass
    try:
        if not left.exists() or not right.exists() or not left.is_file() or not right.is_file():
            return False
        if left.stat().st_size != right.stat().st_size:
            return False
        return _file_sha1(left) == _file_sha1(right)
    except Exception:
        return False


def _unique_image_target(target_dir: Path, marker: str, suffix: str, source: Path) -> Path:
    target = target_dir / f"{marker}{suffix}"
    try:
        if not target.exists() or _same_existing_file(source, target):
            return target
    except Exception:
        return target

    try:
        digest = _file_sha1(source)[:12]
    except Exception:
        digest = hashlib.sha1(str(source).encode("utf-8", errors="ignore")).hexdigest()[:12]
    base = f"{marker}_{digest}".strip("._-")
    candidate = target_dir / f"{base}{suffix}"
    if not candidate.exists() or _same_existing_file(source, candidate):
        return candidate
    for index in range(2, 1000):
        candidate = target_dir / f"{base}_{index}{suffix}"
        if not candidate.exists() or _same_existing_file(source, candidate):
            return candidate
    return target_dir / f"{base}_{int(time.time())}{suffix}"


def _copy_materialized_asset(
    source: Path,
    target: Path,
    created_assets: list[tuple[Path, str]] | None = None,
) -> bool:
    """Copy exclusively and remember only files created by this promotion."""

    try:
        if source.resolve() == target.resolve():
            return False
    except OSError:
        pass
    try:
        with source.open("rb") as source_stream, target.open("xb") as target_stream:
            shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
    except FileExistsError:
        if _same_existing_file(source, target):
            return False
        raise
    except Exception:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    try:
        shutil.copystat(source, target)
    except OSError:
        pass
    digest = _file_sha256(target)
    if created_assets is not None:
        created_assets.append((target.resolve(), digest))
    return True


def _cleanup_created_assets(created_assets: list[tuple[Path, str]]) -> None:
    """Compensate a rollback without deleting reused or subsequently changed files."""

    seen: set[Path] = set()
    for raw_path, created_digest in reversed(created_assets):
        path = Path(raw_path)
        if path in seen:
            continue
        seen.add(path)
        try:
            if path.is_file() and _file_sha256(path) == created_digest:
                path.unlink()
        except OSError:
            continue


def _is_continuation(record: StagingProblemRecord) -> bool:
    normalized = dict(record.normalized or {})
    continuation = normalized.get("continuacion") if isinstance(normalized.get("continuacion"), dict) else {}
    return bool(
        continuation_flags_enabled(continuation)
        or has_continuation_marker(record.raw_ocr)
        or has_continuation_marker(normalized.get("latex_rendered_item"))
        or has_continuation_marker(normalized.get("enunciado_latex"))
    )


def _continuation_records(parent: StagingProblemRecord, all_records: list[StagingProblemRecord]) -> list[StagingProblemRecord]:
    normalized = dict(parent.normalized or {})
    fused = normalized.get("continuaciones_fusionadas") if isinstance(normalized.get("continuaciones_fusionadas"), list) else []
    wanted_ids = [
        _clean_text(item.get("record_id"))
        for item in fused
        if isinstance(item, dict) and _clean_text(item.get("record_id"))
    ]
    by_id = {_clean_text(row.record_id): row for row in all_records}
    out: list[StagingProblemRecord] = []
    seen: set[str] = set()

    def add(row: StagingProblemRecord | None, *, allow_unmarked: bool = False) -> None:
        if row is None or (not allow_unmarked and not _is_continuation(row)):
            return
        key = _clean_text(row.record_id)
        if not key or key in seen:
            return
        out.append(row)
        seen.add(key)

    for record_id in wanted_ids:
        add(by_id.get(record_id), allow_unmarked=True)

    parent_id = _clean_text(parent.record_id)
    for row in all_records:
        continuation = row.normalized.get("continuacion") if isinstance(row.normalized.get("continuacion"), dict) else {}
        if _clean_text(continuation.get("parent_record_id")) == parent_id:
            add(row, allow_unmarked=True)

    parent_index = next((index for index, row in enumerate(all_records) if _clean_text(row.record_id) == parent_id), -1)
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

    rows = [record, *_continuation_records(record, all_records)]

    def add_record_segments(row: StagingProblemRecord) -> None:
        figure = dict(row.figure_segmentation or {})
        segments = figure.get("segments") if isinstance(figure.get("segments"), list) else []
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            add(segment.get("image_path") or segment.get("file_path") or segment.get("path"))

    for row in rows:
        add_record_segments(row)
    if paths:
        return paths

    def add_record_crop_if_image_tagged(row: StagingProblemRecord) -> None:
        figure = dict(row.figure_segmentation or {})
        final_latex = _record_final_latex(row)
        normalized = dict(row.normalized or {})
        has_image_tag = "[[Imagen=" in final_latex or bool(normalized.get("tiene_grafico")) or bool(figure.get("segments_total"))
        if has_image_tag and not paths:
            add(row.crop_path)

    for row in rows:
        add_record_crop_if_image_tagged(row)
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
    created_assets: list[tuple[Path, str]] | None = None,
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
        try:
            if source.exists() and source.is_file():
                target = _unique_image_target(target_dir, marker, suffix, source)
                _copy_materialized_asset(source, target, created_assets)
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


def _database_manager_from_profile(db_profile: str, target_db: str):
    from database.connection import DatabaseManager

    return DatabaseManager.from_profile(db_profile, db_name=target_db)


def _transcriptor_controller_factory():
    from modulos.modulo0_transcriptor.controlador_transcriptor import TranscriptorController

    return TranscriptorController()


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
    problem_solution_bundle: dict[str, Any] | None = None,
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
    if problem_solution_bundle is not None:
        metadata["problem_solution_bundle"] = {
            "bundle_id": _clean_text(problem_solution_bundle.get("bundle_id")),
            "bundle_fingerprint": _clean_text(
                problem_solution_bundle.get("bundle_fingerprint") or problem_solution_bundle.get("idempotency_key")
            ),
            "status": _clean_text(problem_solution_bundle.get("status")),
            "problem_ref": dict(problem_solution_bundle.get("problem_ref") or {}),
            "document_relation": dict(problem_solution_bundle.get("document_relation") or {}),
            "human_review": dict(problem_solution_bundle.get("human_review") or {}),
            "provenance": dict(problem_solution_bundle.get("provenance") or {}),
            "solution_count": len(list(problem_solution_bundle.get("solutions") or [])),
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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _coerce_bundle_payload(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    for key in ("bundle", "problem_solution_bundle", "payload"):
        nested = raw.get(key)
        if isinstance(nested, dict) and (nested.get("bundle_id") or nested.get("problem_record_id") or nested.get("problem_ref")):
            return dict(nested)
    if raw.get("bundle_id") or raw.get("problem_record_id") or raw.get("problem_ref"):
        return dict(raw)
    return None


def _record_bundle_references(record: StagingProblemRecord) -> tuple[dict[str, Any] | None, str, str]:
    artifacts = dict(record.artifacts or {})
    review = dict(record.review or {})
    inline: dict[str, Any] | None = None
    bundle_id = ""
    bundle_path = ""
    for container in (artifacts, review):
        for key in ("problem_solution_bundle", "solution_bundle"):
            value = container.get(key)
            if isinstance(value, dict):
                inline = _coerce_bundle_payload(value)
                if inline is not None:
                    break
            elif isinstance(value, str) and value.strip():
                if value.lower().endswith(".json") or "/" in value or "\\" in value:
                    bundle_path = value.strip()
                else:
                    bundle_id = value.strip()
        if inline is not None:
            break
        for key in ("problem_solution_bundle_id", "solution_bundle_id", "bundle_id"):
            value = _clean_text(container.get(key))
            if value:
                bundle_id = value
                break
        for key in ("problem_solution_bundle_path", "solution_bundle_path", "bundle_path"):
            value = _clean_text(container.get(key))
            if value:
                bundle_path = value
                break
    return inline, bundle_id, bundle_path


def _bundle_report_fields(bundle: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(bundle or {})
    return {
        "bundle_id": _clean_text(raw.get("bundle_id")),
        "solution_count": len([item for item in list(raw.get("solutions") or []) if isinstance(item, dict)]),
    }


def _load_attached_problem_solution_bundle(
    staging: InstanceStagingStore,
    record: StagingProblemRecord,
) -> dict[str, Any] | None:
    """Loads a reviewed bundle while remaining compatible with staged API rollout."""

    inline, bundle_id, bundle_path = _record_bundle_references(record)
    if inline is not None:
        return inline

    lookup_errors: list[str] = []
    lookup_specs = (
        ("bundle_for_record", _clean_text(record.record_id)),
        ("problem_solution_bundle_for_record", _clean_text(record.record_id)),
        ("read_problem_solution_bundle", bundle_id),
        ("get_problem_solution_bundle", bundle_id),
    )
    for method_name, argument in lookup_specs:
        method = getattr(staging, method_name, None)
        if not callable(method) or not argument:
            continue
        try:
            loaded = _coerce_bundle_payload(method(argument))
        except (FileNotFoundError, KeyError) as exc:
            lookup_errors.append(f"{method_name}:{exc}")
            continue
        except TypeError:
            # A concurrently deployed staging adapter may expose a different
            # signature. Inline/path fallback below keeps legacy uploads valid.
            continue
        except Exception as exc:
            raise BundlePreflightError(f"bundle:lookup_failed:{method_name}:{exc}") from exc
        if loaded is not None:
            return loaded

    if bundle_path:
        raw_path = Path(bundle_path).expanduser()
        candidates = [raw_path] if raw_path.is_absolute() else [staging.root / raw_path, raw_path]
        for candidate in candidates:
            try:
                if candidate.exists() and candidate.is_file():
                    loaded = _coerce_bundle_payload(json.loads(candidate.read_text(encoding="utf-8")))
                    if loaded is None:
                        raise BundlePreflightError("bundle:invalid_payload")
                    return loaded
            except BundlePreflightError:
                raise
            except Exception as exc:
                raise BundlePreflightError(f"bundle:read_failed:{exc}") from exc
        raise BundlePreflightError(f"bundle:asset_missing:{bundle_path}")

    if bundle_id:
        detail = lookup_errors[-1] if lookup_errors else bundle_id
        raise BundlePreflightError(f"bundle:not_found:{detail}")
    return None


def _staging_bundle_validation_issues(
    staging: InstanceStagingStore,
    bundle: dict[str, Any],
    record: StagingProblemRecord,
) -> list[str]:
    validator = getattr(staging, "problem_solution_bundle_issues", None)
    if not callable(validator):
        return []
    try:
        raw = validator(bundle, record=record)
    except TypeError:
        try:
            raw = validator(bundle)
        except Exception as exc:
            return [f"bundle:staging_validation_failed:{exc}"]
    except Exception as exc:
        return [f"bundle:staging_validation_failed:{exc}"]
    return [str(item).strip() for item in list(raw or []) if str(item).strip()]


def _bundle_snapshot_token(bundle: dict[str, Any] | None) -> str:
    if bundle is None:
        return ""
    encoded = json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _record_promotion_snapshot_token(record: StagingProblemRecord) -> str:
    payload = {
        "record_id": record.record_id,
        "crop_id": record.crop_id,
        "crop_path": record.crop_path,
        "status": StageStatus.normalize(record.status),
        "source": dict(record.source or {}),
        "normalized": dict(record.normalized or {}),
        "review": dict(record.review or {}),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _problem_solution_snapshot_lock(staging: InstanceStagingStore):
    lock_factory = getattr(staging, "_problem_solution_lock", None)
    if not callable(lock_factory):
        return nullcontext()
    try:
        return lock_factory()
    except Exception as exc:
        raise BundlePreflightError(f"bundle:snapshot_lock_failed:{exc}") from exc


def _reload_bundle_snapshot_for_promotion(
    staging: InstanceStagingStore,
    record: StagingProblemRecord,
    expected_bundle: dict[str, Any] | None,
) -> tuple[StagingProblemRecord, dict[str, Any] | None]:
    live_record = staging.get_record(record.record_id)
    if live_record is None:
        raise BundlePreflightError("record:removed_after_preflight")
    if _record_promotion_snapshot_token(live_record) != _record_promotion_snapshot_token(record):
        raise BundlePreflightError("record:changed_after_preflight")
    candidate = staging.build_promotion_candidate(live_record.record_id)
    blocking = [str(item).strip() for item in list(candidate.get("blocking_issues") or []) if str(item).strip()]
    if blocking:
        raise BundlePreflightError([f"record:changed_after_preflight:{item}" for item in blocking])

    current_bundle = _load_attached_problem_solution_bundle(staging, live_record)
    expected_token = _bundle_snapshot_token(expected_bundle)
    current_token = _bundle_snapshot_token(current_bundle)
    if expected_token != current_token:
        if expected_bundle is not None and current_bundle is None:
            raise BundlePreflightError("bundle:revoked_after_preflight")
        raise BundlePreflightError("bundle:changed_after_preflight")
    if current_bundle is not None:
        issues = _staging_bundle_validation_issues(staging, current_bundle, live_record)
        if issues:
            raise BundlePreflightError(issues)
    return live_record, current_bundle


def _review_state(raw: Any) -> str:
    if isinstance(raw, dict):
        for key in ("status", "decision", "action", "state", "outcome"):
            value = _clean_text(raw.get(key)).lower().replace("-", "_").replace(" ", "_")
            if value:
                return value
        return ""
    return _clean_text(raw).lower().replace("-", "_").replace(" ", "_")


def _solution_asset_roots(context: InstancePipelineContext, asset_root: Path | None) -> list[Path]:
    roots: list[Path] = []
    for raw in (
        asset_root,
        context.staging_root(),
        Path(context.workspace_dir) if _clean_text(context.workspace_dir) else None,
        Path(context.pdf_path).parent if _clean_text(context.pdf_path) else None,
    ):
        if raw is None:
            continue
        path = Path(raw).expanduser()
        if path not in roots:
            roots.append(path)
    return roots


def _resolve_solution_asset(raw_path: Any, roots: list[Path]) -> Path | None:
    text = _clean_text(raw_path)
    if not text:
        return None
    source = Path(text).expanduser()
    candidates = [source] if source.is_absolute() else [*(root / source for root in roots), source]
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


def _fallback_visual_solution_payloads(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    bundle_id = _clean_text(bundle.get("bundle_id"))
    problem_ref = dict(bundle.get("problem_ref") or bundle.get("problem") or {})
    bundle_scope = dict(bundle.get("scope") or {})
    bundle_review = dict(bundle.get("human_review") or {})
    provenance = dict(bundle.get("provenance") or {})
    payloads: list[dict[str, Any]] = []
    for index, raw_solution in enumerate(list(bundle.get("solutions") or []), start=1):
        solution = dict(raw_solution or {}) if isinstance(raw_solution, dict) else {}
        unit_id = _clean_text(solution.get("solution_unit_id") or solution.get("unit_id") or solution.get("solution_id"))
        group_id = _clean_text(solution.get("solution_group_id") or solution.get("solution_id") or unit_id)
        if not group_id:
            digest = hashlib.sha256(f"{bundle_id}:{index}".encode("utf-8")).hexdigest()[:16]
            group_id = f"solution_{digest}"
        try:
            variant_index = int(solution.get("variant_index") or index)
        except (TypeError, ValueError):
            variant_index = index
        fragments = [dict(item) for item in list(solution.get("fragments") or []) if isinstance(item, dict)]
        link = dict(solution.get("link") or {})
        link.update(
            {
                "candidate_link_id": _clean_text(solution.get("candidate_link_id") or link.get("candidate_link_id")),
                "human_review_event_id": _clean_text(solution.get("human_review_event_id") or link.get("human_review_event_id")),
                "status": "human_confirmed",
                "relation_kind": _clean_text(solution.get("relation_kind") or link.get("relation_kind") or "one_to_one"),
            }
        )
        payloads.append(
            {
                "schema_version": VISUAL_SOLUTION_GROUP_SCHEMA_VERSION,
                "solution_group_id": group_id,
                "solution_unit_id": unit_id or group_id,
                "variant_index": variant_index,
                "solution_kind": _clean_text(solution.get("solution_kind") or "unknown"),
                "images": [
                    _clean_text(fragment.get("crop_path") or fragment.get("image_path") or fragment.get("file_path") or fragment.get("path"))
                    for fragment in fragments
                    if _clean_text(fragment.get("crop_path") or fragment.get("image_path") or fragment.get("file_path") or fragment.get("path"))
                ],
                "fragments": fragments,
                "source": {
                    **bundle_scope,
                    "exercise_set_id": _clean_text(
                        solution.get("exercise_set_id")
                        or dict(solution.get("source") or {}).get("exercise_set_id")
                        or problem_ref.get("exercise_set_id")
                        or bundle_scope.get("exercise_set_id")
                    ),
                    "source_fingerprint": _clean_text(solution.get("source_fingerprint") or solution.get("source_digest")),
                    "provenance": dict(solution.get("provenance") or provenance),
                },
                "link": link,
                "human_review": dict(solution.get("human_review") or bundle_review),
                "bundle_id": bundle_id,
                "bundle_fingerprint": _clean_text(bundle.get("bundle_fingerprint") or bundle.get("idempotency_key")),
            }
        )
    return payloads


def _domain_bundle_validation_issues(bundle: dict[str, Any]) -> list[str]:
    try:
        from . import problem_solution_linking
    except (ImportError, AttributeError):
        return []
    validator = getattr(problem_solution_linking, "problem_solution_bundle_issues", None)
    if not callable(validator):
        validator = getattr(problem_solution_linking, "validate_confirmed_bundle", None)
    if not callable(validator):
        return []
    try:
        raw = validator(bundle)
    except Exception as exc:
        return [f"bundle:domain_validation_failed:{exc}"]
    if raw is None or raw is True:
        return []
    if raw is False:
        return ["bundle:domain_validation_failed"]
    if isinstance(raw, dict):
        raw = raw.get("issues") or raw.get("errors") or []
    return [str(item).strip() for item in list(raw or []) if str(item).strip()]


def _domain_visual_solution_payloads(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        from .problem_solution_linking import visual_solution_payloads

        raw = visual_solution_payloads(bundle)
        payloads = [dict(item) for item in list(raw or []) if isinstance(item, dict)]
        if payloads:
            return payloads
    except (ImportError, AttributeError):
        pass
    except Exception as exc:
        raise BundlePreflightError(f"bundle:visual_payload_failed:{exc}") from exc
    return _fallback_visual_solution_payloads(bundle)


def _validated_visual_solution_groups(
    bundle: dict[str, Any],
    record: StagingProblemRecord,
    context: InstancePipelineContext,
    *,
    asset_root: Path | None = None,
    materialize_assets: bool = False,
    created_assets: list[tuple[Path, str]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    issues: list[str] = []
    bundle_id = _clean_text(bundle.get("bundle_id"))
    if not bundle_id:
        issues.append("bundle:missing_bundle_id")
    status = _review_state(bundle.get("status"))
    if status not in PROMOTABLE_BUNDLE_STATES:
        issues.append(f"bundle:not_confirmed:{status or 'missing'}")
    review_state = _review_state(bundle.get("human_review"))
    if review_state not in CONFIRMED_REVIEW_STATES:
        issues.append(f"bundle:human_review_required:{review_state or 'missing'}")

    problem_ref = dict(bundle.get("problem_ref") or bundle.get("problem") or {})
    problem_record_id = _clean_text(bundle.get("problem_record_id") or problem_ref.get("record_id"))
    if problem_record_id and problem_record_id != _clean_text(record.record_id):
        issues.append(f"bundle:problem_record_mismatch:{problem_record_id}")

    bundle_scope = dict(bundle.get("scope") or {})
    scope_book = _clean_text(bundle_scope.get("book_code") or problem_ref.get("book_code"))
    scope_instance = _clean_text(bundle_scope.get("instance_type") or problem_ref.get("instance_type"))
    if scope_book and _clean_text(context.book_code) and scope_book != _clean_text(context.book_code):
        issues.append(f"bundle:book_mismatch:{scope_book}")
    if scope_instance and _clean_text(context.instance_type) and scope_instance != _clean_text(context.instance_type):
        issues.append(f"bundle:instance_mismatch:{scope_instance}")

    raw_solutions = list(bundle.get("solutions") or [])
    if not raw_solutions:
        issues.append("bundle:missing_solutions")
    if any(not isinstance(item, dict) for item in raw_solutions):
        issues.append("bundle:invalid_solution")
    issues.extend(_domain_bundle_validation_issues(bundle))
    if issues:
        raise BundlePreflightError(issues)

    groups = _domain_visual_solution_payloads(bundle)
    if len(groups) != len(raw_solutions):
        raise BundlePreflightError("bundle:visual_solution_count_mismatch")
    roots = _solution_asset_roots(context, asset_root)
    managed_group_id = _managed_solution_group_id(context, record)
    managed_solution_dir = _canonical_solution_dir(context)
    normalized_groups: list[dict[str, Any]] = []
    seen_group_ids: set[str] = set()
    for solution_index, (raw_solution, raw_group) in enumerate(zip(raw_solutions, groups), start=1):
        solution = dict(raw_solution)
        group = dict(raw_group)
        group_id = _clean_text(
            group.get("solution_group_id")
            or group.get("solution_id")
            or solution.get("solution_group_id")
            or solution.get("solution_id")
            or solution.get("solution_unit_id")
        )
        if not group_id:
            group_id = f"solution_{hashlib.sha256(f'{bundle_id}:{solution_index}'.encode('utf-8')).hexdigest()[:16]}"
        if group_id in seen_group_ids:
            issues.append(f"bundle:duplicate_solution_group_id:{group_id}")
        seen_group_ids.add(group_id)

        raw_fragments = [dict(item) for item in list(solution.get("fragments") or []) if isinstance(item, dict)]
        if not raw_fragments:
            issues.append(f"bundle:solution_without_fragments:{group_id}")
            continue
        normalized_fragments: list[dict[str, Any]] = []
        images: list[str] = []
        seen_fragment_ids: set[str] = set()
        for fragment_index, raw_fragment in enumerate(raw_fragments, start=1):
            fragment = dict(raw_fragment)
            fragment_id = _clean_text(fragment.get("fragment_id")) or f"{group_id}_fragment_{fragment_index}"
            if fragment_id in seen_fragment_ids:
                issues.append(f"bundle:duplicate_fragment_id:{group_id}:{fragment_id}")
            seen_fragment_ids.add(fragment_id)
            asset_raw = fragment.get("crop_path") or fragment.get("image_path") or fragment.get("file_path") or fragment.get("path")
            asset = _resolve_solution_asset(asset_raw, roots)
            if asset is None:
                issues.append(f"bundle:solution_asset_missing:{group_id}:{_clean_text(asset_raw) or fragment_id}")
                continue
            expected_hash = _clean_text(fragment.get("crop_sha256") or fragment.get("sha256") or fragment.get("asset_sha256"))
            actual_hash = _file_sha256(asset)
            if expected_hash:
                expected_hash = expected_hash.lower().removeprefix("sha256:")
                if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
                    issues.append(f"bundle:invalid_asset_sha256:{group_id}:{fragment_id}")
                    continue
                if actual_hash != expected_hash:
                    issues.append(f"bundle:asset_sha256_mismatch:{group_id}:{fragment_id}")
                    continue
            bbox = fragment.get("bbox_xyxy") or fragment.get("bbox_px")
            if bbox is not None:
                if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                    issues.append(f"bundle:invalid_bbox:{group_id}:{fragment_id}")
                    continue
                try:
                    x1, y1, x2, y2 = [float(value) for value in bbox]
                except (TypeError, ValueError):
                    issues.append(f"bundle:invalid_bbox:{group_id}:{fragment_id}")
                    continue
                if min(x1, y1) < 0 or x2 <= x1 or y2 <= y1:
                    issues.append(f"bundle:invalid_bbox:{group_id}:{fragment_id}")
                    continue
            stored_asset = asset
            if materialize_assets:
                managed_solution_dir.mkdir(parents=True, exist_ok=True)
                marker = _safe_image_marker(f"{group_id}_{fragment_id}")
                suffix = asset.suffix if asset.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"} else ".png"
                target = _unique_image_target(managed_solution_dir, marker, suffix, asset)
                _copy_materialized_asset(asset, target, created_assets)
                try:
                    if _file_sha256(target) != actual_hash:
                        issues.append(f"bundle:managed_asset_sha256_mismatch:{group_id}:{fragment_id}")
                        continue
                except OSError:
                    issues.append(f"bundle:managed_asset_unreadable:{group_id}:{fragment_id}")
                    continue
                stored_asset = target.resolve()
            stored_path = str(stored_asset)
            if stored_path not in images:
                images.append(stored_path)
            normalized_fragment = {
                **fragment,
                "fragment_id": fragment_id,
                "crop_path": stored_path,
                "source_crop_path": str(asset),
                "managed": bool(materialize_assets),
            }
            if materialize_assets:
                try:
                    normalized_fragment["managed_path_relative"] = str(
                        stored_asset.relative_to(Path(context.workspace_dir).expanduser().resolve())
                    ).replace("\\", "/")
                except (OSError, ValueError):
                    normalized_fragment["managed_path_relative"] = stored_asset.name
            if expected_hash:
                normalized_fragment["crop_sha256"] = expected_hash
                normalized_fragment["sha256"] = expected_hash
            normalized_fragments.append(normalized_fragment)

        group.update(
            {
                "schema_version": _clean_text(group.get("schema_version")) or VISUAL_SOLUTION_GROUP_SCHEMA_VERSION,
                "solution_group_id": group_id,
                "managed_group_id": managed_group_id,
                "images": images,
                "fragments": normalized_fragments,
                "bundle_id": bundle_id,
                "bundle_fingerprint": _clean_text(bundle.get("bundle_fingerprint") or bundle.get("idempotency_key")),
            }
        )
        raw_source = group.get("source")
        source_payload = dict(raw_source) if isinstance(raw_source, dict) else {"kind": _clean_text(raw_source)}
        source_payload.update(
            {
                "problem_ref": dict(bundle.get("problem_ref") or bundle.get("problem") or {}),
                "document_relation": dict(bundle.get("document_relation") or {}),
                "provenance": dict(bundle.get("provenance") or {}),
                "managed_solution_dir": str(managed_solution_dir) if materialize_assets else "",
            }
        )
        group["source"] = source_payload
        group["human_review"] = dict(group.get("human_review") or bundle.get("human_review") or {})
        link = dict(group.get("link") or {})
        link.update(
            {
                "status": "human_confirmed",
                "candidate_link_id": _clean_text(solution.get("candidate_link_id") or link.get("candidate_link_id")),
                "human_review_event_id": _clean_text(solution.get("human_review_event_id") or link.get("human_review_event_id")),
                "relation_kind": _clean_text(solution.get("relation_kind") or link.get("relation_kind") or "one_to_one"),
            }
        )
        group["link"] = link
        normalized_groups.append(group)

    if issues:
        raise BundlePreflightError(issues)
    return normalized_groups, {
        "bundle_id": bundle_id,
        "bundle_fingerprint": _clean_text(bundle.get("bundle_fingerprint") or bundle.get("idempotency_key")),
        "solution_count": len(normalized_groups),
    }


def build_problem_payload(
    record: StagingProblemRecord,
    context: InstancePipelineContext,
    *,
    controller: Any | None = None,
    all_records: list[StagingProblemRecord] | None = None,
    materialize_images: bool = True,
    problem_solution_bundle: dict[str, Any] | None = None,
    solution_asset_root: Path | None = None,
    created_assets: list[tuple[Path, str]] | None = None,
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
        created_assets=created_assets,
    )
    image_base_dir = _canonical_image_dir(context) if imagenes and materialize_images else None
    payload = {
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
        "libro_codigo": _clean_text(context.book_code),
        "instancia_tipo": _clean_text(context.instance_type),
        "record_id": record.record_id,
        "crop_id": record.crop_id,
    }
    if problem_solution_bundle is not None:
        visual_solutions, bundle_metadata = _validated_visual_solution_groups(
            problem_solution_bundle,
            record,
            context,
            asset_root=solution_asset_root,
            materialize_assets=materialize_images,
            created_assets=created_assets,
        )
        payload["soluciones"] = visual_solutions
        payload.update(bundle_metadata)
    return payload


def _solution_identity(raw: Any) -> str:
    if not isinstance(raw, dict):
        return ""
    return _clean_text(raw.get("solution_group_id") or raw.get("solution_id"))


def _managed_solution_group_keys(raw: Any) -> set[str]:
    if not isinstance(raw, dict):
        return set()
    keys: set[str] = set()
    managed_group_id = _clean_text(raw.get("managed_group_id"))
    if managed_group_id:
        keys.add(f"managed:{managed_group_id}")
    bundle_id = _clean_text(raw.get("bundle_id"))
    if bundle_id:
        keys.add(f"bundle:{bundle_id}")
    source = dict(raw.get("source") or {}) if isinstance(raw.get("source"), dict) else {}
    problem_ref = dict(source.get("problem_ref") or {})
    provenance = dict(source.get("provenance") or {})
    record_id = _clean_text(problem_ref.get("record_id"))
    book_code = _clean_text(provenance.get("book_code") or source.get("book_code"))
    instance_type = _clean_text(provenance.get("instance_type") or source.get("instance_type"))
    if record_id and (book_code or instance_type):
        inferred = _managed_solution_group_id_values(book_code, instance_type, record_id)
        keys.add(f"managed:{inferred}")
    return keys


def _coerce_existing_solutions(raw: Any) -> list[Any]:
    payload = raw
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return [raw] if raw.strip() else []
    if payload is None:
        return []
    if isinstance(payload, list):
        return list(payload)
    return [payload]


def _merge_solution_payloads(existing: Any, incoming: list[dict[str, Any]]) -> list[Any]:
    """Replace one managed problem group while preserving unrelated/legacy entries."""

    incoming_rows = [dict(item) for item in list(incoming or []) if isinstance(item, dict)]
    incoming_ids = {_solution_identity(item) for item in incoming_rows if _solution_identity(item)}
    incoming_group_keys = set().union(*(_managed_solution_group_keys(item) for item in incoming_rows)) if incoming_rows else set()
    preserved = [
        item
        for item in _coerce_existing_solutions(existing)
        if not (
            (_solution_identity(item) and _solution_identity(item) in incoming_ids)
            or (_managed_solution_group_keys(item) & incoming_group_keys)
        )
    ]
    return [*preserved, *incoming_rows]


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
    if "soluciones" in cols and "soluciones" in payload:
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
    merged_solutions: list[Any] | None = None
    if "soluciones" in cols and "soluciones" in payload:
        cur.execute("SELECT soluciones FROM problemas WHERE id = %s FOR UPDATE;", (int(problem_id),))
        existing_row = cur.fetchone()
        existing_solutions = existing_row[0] if existing_row else None
        merged_solutions = _merge_solution_payloads(existing_solutions, list(payload["soluciones"] or []))

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
    if "soluciones" in cols and "soluciones" in payload:
        add("soluciones", json.dumps(merged_solutions or [], ensure_ascii=False), "%s::jsonb")
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
    if not dry_run:
        staging.repair_detected_continuation_links()
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
        "bundles_promoted": 0,
        "solution_groups_promoted": 0,
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

    prepared_bundles: dict[str, dict[str, Any] | None] = {}
    for row in records:
        candidate = staging.build_promotion_candidate(row.record_id)
        blocking = list(candidate.get("blocking_issues") or [])
        if blocking:
            inline_bundle, bundle_id, _bundle_path = _record_bundle_references(row)
            bundle_fields = _bundle_report_fields(inline_bundle or ({"bundle_id": bundle_id} if bundle_id else None))
            report["skipped"] += 1
            report["rows"].append(
                {
                    "record_id": row.record_id,
                    "status": "skipped",
                    "blocking_issues": blocking,
                    **bundle_fields,
                }
            )
            continue
        bundle: dict[str, Any] | None = None
        try:
            bundle = _load_attached_problem_solution_bundle(staging, row)
            if bundle is not None:
                staging_issues = _staging_bundle_validation_issues(staging, bundle, row)
                if staging_issues:
                    raise BundlePreflightError(staging_issues)
            payload = build_problem_payload(
                row,
                context,
                all_records=all_records,
                materialize_images=False,
                problem_solution_bundle=bundle,
                solution_asset_root=staging.root,
            )
            prepared_bundles[_clean_text(row.record_id)] = bundle
        except BundlePreflightError as exc:
            report["skipped"] += 1
            report["rows"].append(
                {
                    "record_id": row.record_id,
                    "status": "skipped",
                    "blocking_issues": list(exc.issues),
                    **_bundle_report_fields(bundle),
                }
            )
            continue
        except Exception as exc:
            report["errors"] += 1
            report["rows"].append(
                {
                    "record_id": row.record_id,
                    "status": "error",
                    "message": str(exc),
                    **_bundle_report_fields(bundle),
                }
            )
            continue
        if dry_run:
            report["rows"].append(
                {
                    "record_id": row.record_id,
                    "status": "ready",
                    "numero_original": payload["numero_original"],
                    "archivo_origen": payload["archivo_origen"],
                    "imagenes": len(payload["imagenes"]),
                    "bundle_id": _clean_text(payload.get("bundle_id")),
                    "solution_count": int(payload.get("solution_count") or 0),
                }
            )
            continue

    if dry_run:
        return report

    db = _database_manager_from_profile(db_profile, target_db)
    controller = _transcriptor_controller_factory()
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
            prepared_bundle = prepared_bundles.get(_clean_text(row.record_id))
            bundle = prepared_bundle
            payload: dict[str, Any] = {}
            try:
                problem_id = 0
                origin_id = 0
                operation = ""
                attempts = 0
                while True:
                    attempts += 1
                    created_assets: list[tuple[Path, str]] = []
                    db_committed = False
                    cur = conn.cursor()
                    try:
                        with _problem_solution_snapshot_lock(staging):
                            _lock_factory_origin(cur, context)
                            live_row, bundle = _reload_bundle_snapshot_for_promotion(staging, row, prepared_bundle)
                            payload = build_problem_payload(
                                live_row,
                                context,
                                controller=controller,
                                all_records=all_records,
                                problem_solution_bundle=bundle,
                                solution_asset_root=staging.root,
                                created_assets=created_assets,
                            )
                            live_row, bundle = _reload_bundle_snapshot_for_promotion(staging, live_row, bundle)
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
                                record=live_row,
                                numero_original=int(payload["numero_original"]),
                                problem_solution_bundle=bundle,
                            )
                            conn.commit()
                            db_committed = True
                            if operation == "inserted":
                                report["inserted"] += 1
                            else:
                                report["updated"] += 1
                            if payload.get("bundle_id"):
                                report["bundles_promoted"] += 1
                                report["solution_groups_promoted"] += int(payload.get("solution_count") or 0)
                            live_row.audit = {
                                **dict(live_row.audit or {}),
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
                                    "bundle_id": _clean_text(payload.get("bundle_id")),
                                    "bundle_fingerprint": _clean_text(payload.get("bundle_fingerprint")),
                                    "solution_count": int(payload.get("solution_count") or 0),
                                },
                            }
                            live_row.artifacts = {
                                **dict(live_row.artifacts or {}),
                                "db_problem_id": int(problem_id),
                                "db_origin_id": int(origin_id),
                                "db_problem_solution_bundle_id": _clean_text(payload.get("bundle_id")),
                                "db_solution_groups_total": int(payload.get("solution_count") or 0),
                                "db_promotion_updated_at": live_row.audit["db_promotion"]["uploaded_at"],
                            }
                            live_row.touch()
                            staging.upsert_record(live_row)
                            row = live_row
                        break
                    except Exception as exc:
                        rollback_succeeded = False
                        try:
                            conn.rollback()
                            rollback_succeeded = True
                        finally:
                            if rollback_succeeded and not db_committed:
                                _cleanup_created_assets(created_assets)
                                created_assets.clear()
                        if not db_committed and attempts < PROMOTION_ROW_MAX_ATTEMPTS and _is_transient_promotion_error(exc):
                            time.sleep(_retry_delay_seconds(attempts))
                            continue
                        raise
                    finally:
                        cur.close()
                report["rows"].append(
                    {
                        "record_id": row.record_id,
                        "status": operation,
                        "problem_id": int(problem_id),
                        "origin_id": int(origin_id),
                        "numero_original": int(payload["numero_original"]),
                        "bundle_id": _clean_text(payload.get("bundle_id")),
                        "solution_count": int(payload.get("solution_count") or 0),
                    }
                )
            except BundlePreflightError as exc:
                conn.rollback()
                report["skipped"] += 1
                report["rows"].append(
                    {
                        "record_id": row.record_id,
                        "status": "skipped",
                        "blocking_issues": list(exc.issues),
                        **_bundle_report_fields(bundle),
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
                        "bundle_id": _clean_text(payload.get("bundle_id") or (bundle or {}).get("bundle_id")),
                        "solution_count": int(payload.get("solution_count") or 0),
                    }
                )
    finally:
        conn.close()
    return report
