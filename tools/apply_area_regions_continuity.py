from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from modulos.instance_factory.models import InstancePipelineContext
import modulos.instance_factory.pipeline as pipeline_module
from modulos.instance_factory.pipeline import InstancePdfPipelineService
from modulos.instance_factory.staging import InstanceStagingStore


BOOK_PARENT = Path(r"E:\Banco de Preguntas\2. GEOMETRIA\1. Cuzcano\Julio Orihuela")
BOOK_ROOT = next(iter(sorted(BOOK_PARENT.glob("S11-*reas_de_regiones_planas"))), BOOK_PARENT / "S11-Areas_de_regiones_planas")
PDF_PATH = next(iter(sorted(BOOK_ROOT.glob("S11-*reas_de_regiones_planas.pdf"))), BOOK_ROOT / "S11-Areas_de_regiones_planas.pdf")
BACKUP_ROOT = REPO_ROOT / ".cache" / "transcriptor_runs" / "backups"
REPORT_ROOT = REPO_ROOT / ".cache" / "transcriptor_runs" / "reports"


INSTANCES = [
    {
        "book_code": "areas-de-regiones-planas",
        "instance_type": "propuestos",
        "project_name": "Areas de regiones planas",
        "staging_root": BOOK_ROOT / "temporales" / "propuestos" / "datasets" / "pdf_factory_staging",
        "session_path": BOOK_ROOT / "sessions" / "propuestos.session.json",
    },
    {
        "book_code": "areas-de-regiones-planas",
        "instance_type": "resueltos",
        "project_name": "Areas de regiones planas",
        "staging_root": BOOK_ROOT / "temporales" / "resueltos" / "datasets" / "pdf_factory_staging",
        "session_path": BOOK_ROOT / "sessions" / "resueltos.session.json",
    },
]


def _context(row: dict[str, Any]) -> InstancePipelineContext:
    return InstancePipelineContext(
        book_code=str(row["book_code"]),
        instance_type=str(row["instance_type"]),
        project_name=str(row["project_name"]),
        pdf_path=str(PDF_PATH),
        session_path=str(row["session_path"]),
        staging_root_override=str(row["staging_root"]),
    )


def _backup_staging(root: Path, backup_dir: Path, instance_type: str) -> dict[str, str]:
    copied: dict[str, str] = {}
    for name in ("records", "manifest.json"):
        src = root / name
        if not src.exists():
            continue
        dst = backup_dir / instance_type / name
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        copied[str(src)] = str(dst)
    return copied


def _install_aux_ocr_progress(instance_type: str, total: int):
    original = pipeline_module._auxiliary_continuity_ocr_features
    counter = {"value": 0}

    def wrapped(record, cache=None):
        counter["value"] += 1
        value = counter["value"]
        if value == 1 or value % 25 == 0 or value == total:
            print(f"[{instance_type}] OCR local continuidad {value}/{total}", flush=True)
        return original(record, cache)

    pipeline_module._auxiliary_continuity_ocr_features = wrapped
    return original


def _restore_aux_ocr(original) -> None:
    if original is not None:
        pipeline_module._auxiliary_continuity_ocr_features = original


def _apply_instance(
    row: dict[str, Any],
    *,
    scan_confidence: float,
    merge_confidence: float,
    write: bool,
    backup_dir: Path,
) -> dict[str, Any]:
    context = _context(row)
    staging = InstanceStagingStore(context)
    service = InstancePdfPipelineService(context, staging_store=staging)
    records_before = staging.load_records()
    print(
        f"[{row['instance_type']}] Analizando {len(records_before)} crop(s) "
        f"(scan>={scan_confidence}, merge>={merge_confidence})",
        flush=True,
    )
    original_aux = _install_aux_ocr_progress(str(row["instance_type"]), len(records_before))
    try:
        scan = service.scan_continuation_candidates(
            min_confidence=scan_confidence,
            max_candidates=max(1000, len(records_before) * 2),
        )
    finally:
        _restore_aux_ocr(original_aux)
    candidates = list(scan.get("candidates") or [])
    scan_summary = dict(scan.get("summary") or {})
    merge_candidates = [
        candidate
        for candidate in candidates
        if str(candidate.get("recommendation") or "") == "merge"
        and float(candidate.get("confidence") or 0.0) >= merge_confidence
    ]
    merge_candidates.sort(key=lambda item: int(item.get("index") or 0))
    print(
        f"[{row['instance_type']}] scan: {scan_summary.get('total_crops', len(records_before))} crop(s), "
        f"{scan_summary.get('complete_discarded', 0)} completos, "
        f"{scan_summary.get('possible_parents', 0)} padres, "
        f"{scan_summary.get('possible_continuations', 0)} continuaciones, "
        f"{len(merge_candidates)} fusion(es) fuertes.",
        flush=True,
    )

    merged: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    backups: dict[str, str] = {}
    if write and merge_candidates:
        backups = _backup_staging(Path(row["staging_root"]), backup_dir, str(row["instance_type"]))

    for candidate in merge_candidates:
        parent_id = str(candidate.get("parent_record_id") or "")
        child_id = str(candidate.get("continuation_record_id") or "")
        if not parent_id or not child_id:
            skipped.append({**candidate, "skip_reason": "ids vacios"})
            continue
        if parent_id in used_ids or child_id in used_ids:
            skipped.append({**candidate, "skip_reason": "candidato solapado con fusion previa"})
            continue
        if write:
            service.merge_records_for_ocr(parent_id, [child_id])
        used_ids.add(parent_id)
        used_ids.add(child_id)
        merged.append(candidate)

    records_after = staging.load_records() if write else records_before
    merged_children_after = 0
    for record in records_after:
        source = dict(record.source or {})
        if str(source.get("merged_into_record_id") or "").strip():
            merged_children_after += 1

    return {
        "instance_type": row["instance_type"],
        "staging_root": str(row["staging_root"]),
        "records_total": len(records_before),
        "scan_summary": scan_summary,
        "candidates_total": len(candidates),
        "merge_candidates_total": len(merge_candidates),
        "merged_total": len(merged),
        "skipped_total": len(skipped),
        "merged_children_after": merged_children_after,
        "backups": backups,
        "merged": merged,
        "skipped": skipped,
        "dry_run": not write,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Aplica fusion de continuidades al libro Areas de regiones planas.")
    parser.add_argument("--write", action="store_true", help="Ejecuta fusiones reales. Sin esto solo reporta.")
    parser.add_argument("--scan-confidence", type=float, default=0.1)
    parser.add_argument("--merge-confidence", type=float, default=0.78)
    parser.add_argument("--tesseract-timeout", type=float, default=2.0)
    args = parser.parse_args()
    import os

    os.environ["PDF_FACTORY_CONTINUITY_TESSERACT_TIMEOUT"] = str(max(0.5, float(args.tesseract_timeout)))

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUP_ROOT / f"area_regions_continuity_{stamp}"
    report_dir = REPORT_ROOT / f"area_regions_continuity_{stamp}"
    report_dir.mkdir(parents=True, exist_ok=True)

    results = [
        _apply_instance(
            row,
            scan_confidence=float(args.scan_confidence),
            merge_confidence=float(args.merge_confidence),
            write=bool(args.write),
            backup_dir=backup_dir,
        )
        for row in INSTANCES
    ]
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "book_root": str(BOOK_ROOT),
        "write": bool(args.write),
        "scan_confidence": float(args.scan_confidence),
        "merge_confidence": float(args.merge_confidence),
        "tesseract_timeout": float(args.tesseract_timeout),
        "backup_dir": str(backup_dir) if args.write else "",
        "results": results,
    }
    report_path = report_dir / ("applied.json" if args.write else "dry_run.json")
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report_path": str(report_path), **payload}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
