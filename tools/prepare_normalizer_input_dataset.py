from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modulos.instance_factory.models import PipelineStep, StageStatus


SCHEMA_VERSION = "normalizer_input_staging_v1"
MANIFEST_SCHEMA_VERSION = "normalizer_input_export_manifest_v1"


@dataclass
class ExportResult:
    rows: list[dict[str, Any]]
    skipped: list[dict[str, Any]]
    manifest: dict[str, Any]


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def iter_staging_record_paths(staging_roots: Iterable[Path]) -> list[Path]:
    paths: list[Path] = []
    for raw_root in staging_roots:
        root = Path(raw_root).expanduser().resolve()
        if (root / "records").exists():
            paths.extend(sorted((root / "records").glob("*.json"), key=lambda item: item.name.lower()))
            continue
        for records_dir in sorted(root.glob("*/records"), key=lambda item: str(item).lower()):
            paths.extend(sorted(records_dir.glob("*.json"), key=lambda item: item.name.lower()))
    return paths


def _int_sort_value(value: Any, default: int = 10**9) -> int:
    try:
        number = int(value)
    except Exception:
        return default
    return number if number >= 0 else default


def _record_sort_key(row: dict[str, Any]) -> tuple[int, int, int, int, int, str]:
    source = row.get("source") if isinstance(row.get("source"), dict) else {}
    bbox = source.get("bbox_px") or []
    y1 = _int_sort_value(bbox[1] if isinstance(bbox, (list, tuple)) and len(bbox) > 1 else None)
    x1 = _int_sort_value(bbox[0] if isinstance(bbox, (list, tuple)) and len(bbox) > 0 else None)
    return (
        _int_sort_value(source.get("page_number") or source.get("source_page_number")),
        _int_sort_value(source.get("source_order")),
        _int_sort_value(source.get("box_index") or source.get("page_problem_index") or source.get("problem_index")),
        y1,
        x1,
        str(row.get("record_id") or row.get("crop_id") or ""),
    )


def _is_stale_or_invalidated(row: dict[str, Any]) -> bool:
    audit = row.get("audit") if isinstance(row.get("audit"), dict) else {}
    downstream = audit.get("downstream_state") if isinstance(audit.get("downstream_state"), dict) else {}
    if str(downstream.get("status") or "").strip().lower() == "invalidated":
        return True
    for container_name in ("source", "trace", "audit"):
        container = row.get(container_name) if isinstance(row.get(container_name), dict) else {}
        raw = container.get("source_stale")
        if raw is True or str(raw).strip().lower() in {"1", "true", "yes", "si", "sí"}:
            return True
    return False


def _metadata_issues(row: dict[str, Any]) -> list[str]:
    source = row.get("source") if isinstance(row.get("source"), dict) else {}
    issues: list[str] = []
    if source.get("page_number") in (None, "") and source.get("source_page_number") in (None, ""):
        issues.append("missing:source.page_number")
    bbox = source.get("bbox_px")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        issues.append("invalid:source.bbox_px")
    if not str(row.get("crop_id") or source.get("crop_id") or row.get("record_id") or "").strip():
        issues.append("missing:crop_id")
    if not str(row.get("crop_path") or source.get("crop_path") or "").strip():
        issues.append("missing:crop_path")
    return issues


def _source_dict(row: dict[str, Any]) -> dict[str, Any]:
    return dict(row.get("source") or {}) if isinstance(row.get("source"), dict) else {}


def _skip(source: Path, reason: str, **extra: Any) -> dict[str, Any]:
    return {"source": str(source), "reason": reason, **extra}


def _build_input_row(row: dict[str, Any], record_path: Path) -> dict[str, Any]:
    source = _source_dict(row)
    return {
        "schema_version": SCHEMA_VERSION,
        "record_id": str(row.get("record_id") or row.get("crop_id") or record_path.stem),
        "crop_id": str(row.get("crop_id") or row.get("record_id") or record_path.stem),
        "crop_path": str(row.get("crop_path") or source.get("crop_path") or ""),
        "raw_ocr": str(row.get("raw_ocr") or ""),
        "structured_ocr": {},
        "figure_segmentation": dict(row.get("figure_segmentation") or {}),
        "source": source,
        "models": dict(row.get("models") or {}),
        "confidence": dict(row.get("confidence") or {}),
        "steps": dict(row.get("steps") or {}),
        "status": StageStatus.normalize(str(row.get("status") or StageStatus.PENDING)),
        "errors": [str(item) for item in list(row.get("errors") or [])],
        "traceability": {
            "source_record_path": str(record_path),
            "exported_at": datetime.now().isoformat(timespec="seconds"),
        },
    }


def collect_normalizer_inputs(staging_roots: Iterable[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for record_path in iter_staging_record_paths(staging_roots):
        row = _read_json(record_path)
        if row is None:
            skipped.append(_skip(record_path, "invalid_json"))
            continue
        if _is_stale_or_invalidated(row):
            skipped.append(_skip(record_path, "source_stale"))
            continue
        source = _source_dict(row)
        crop_path = Path(str(row.get("crop_path") or source.get("crop_path") or "")).expanduser()
        if not crop_path.exists():
            skipped.append(_skip(record_path, "missing_crop", crop_path=str(crop_path)))
            continue
        raw_ocr = str(row.get("raw_ocr") or "").strip()
        if not raw_ocr:
            skipped.append(_skip(record_path, "missing_raw_ocr"))
            continue
        metadata_issues = _metadata_issues(row)
        if metadata_issues:
            skipped.append(_skip(record_path, "metadata_incomplete", issues=metadata_issues))
            continue
        rows.append(_build_input_row(row, record_path))
    rows.sort(key=_record_sort_key)
    return rows, skipped


def export_normalizer_inputs(
    *,
    staging_roots: Iterable[Path],
    out_dir: Path,
    max_records: int = 0,
) -> ExportResult:
    selected_roots = [Path(root).expanduser().resolve() for root in staging_roots]
    rows, skipped = collect_normalizer_inputs(selected_roots)
    if max_records > 0:
        rows = rows[: int(max_records)]
    out_dir = Path(out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "inputs.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    (out_dir / "skipped.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in skipped),
        encoding="utf-8",
    )
    counts_by_status: dict[str, int] = {}
    counts_by_step_ready: dict[str, int] = {step: 0 for step in PipelineStep.ORDER}
    for row in rows:
        status = str(row.get("status") or StageStatus.PENDING)
        counts_by_status[status] = counts_by_status.get(status, 0) + 1
        steps = row.get("steps") if isinstance(row.get("steps"), dict) else {}
        for step in PipelineStep.ORDER:
            payload = steps.get(step) if isinstance(steps.get(step), dict) else {}
            if StageStatus.normalize(str(payload.get("status") or "")) == StageStatus.READY:
                counts_by_step_ready[step] += 1
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "row_schema_version": SCHEMA_VERSION,
        "staging_roots": [str(root) for root in selected_roots],
        "files": {
            "inputs": "inputs.jsonl",
            "skipped": "skipped.jsonl",
        },
        "total": len(rows),
        "skipped_total": len(skipped),
        "counts_by_status": counts_by_status,
        "ready_steps": counts_by_step_ready,
        "filters": {
            "requires_existing_crop": True,
            "requires_raw_ocr": True,
            "requires_page_box_crop_metadata": True,
            "excludes_source_stale_or_invalidated": True,
        },
        "policy": {
            "target": "staging_only",
            "writes_to_problemas": False,
            "purpose": "normalizer_input_before_human_normalization",
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return ExportResult(rows=rows, skipped=skipped, manifest=manifest)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Exporta normalizer_input_staging_v1 desde records de staging para preparar normalizacion."
    )
    parser.add_argument(
        "--staging-root",
        action="append",
        required=True,
        help="Staging root directo o carpeta que contiene subcarpetas */records. Repetible.",
    )
    parser.add_argument("--out-dir", required=True, help="Carpeta de salida para inputs.jsonl, skipped.jsonl y manifest.json.")
    parser.add_argument("--max-records", type=int, default=0, help="Limita registros exportados para un smoke.")
    args = parser.parse_args()
    result = export_normalizer_inputs(
        staging_roots=[Path(item) for item in args.staging_root],
        out_dir=Path(args.out_dir),
        max_records=max(0, int(args.max_records or 0)),
    )
    print(json.dumps(result.manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
