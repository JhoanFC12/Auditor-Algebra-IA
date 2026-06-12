from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .models import InstancePipelineContext, StagingProblemRecord


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BANK_ROOT = REPO_ROOT / ".cache" / "transcriptor_runs" / "datasets" / "normalizer_training_bank"
BANK_SCHEMA_VERSION = "normalizer_training_bank_v1"
SAMPLE_SCHEMA_VERSION = "normalizer_training_sample_v1"
DEFAULT_THRESHOLD = 200


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _bank_root(root: Path | None = None) -> Path:
    if root is not None:
        return Path(root).expanduser().resolve()
    configured = str(os.environ.get("NORMALIZER_TRAINING_BANK_ROOT") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_BANK_ROOT.expanduser().resolve()


def _sample_id(context: InstancePipelineContext, record: StagingProblemRecord) -> str:
    raw = "|".join(
        [
            str(context.book_code or ""),
            str(context.instance_type or ""),
            str(context.pdf_path or ""),
            str(record.record_id or ""),
            str(record.crop_id or ""),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _is_continuation(record: StagingProblemRecord) -> bool:
    normalized = dict(record.normalized or {})
    continuation = normalized.get("continuacion") if isinstance(normalized.get("continuacion"), dict) else {}
    raw = str(record.raw_ocr or "").strip()
    return bool(
        continuation.get("es_continuacion")
        or continuation.get("fusionar_con_anterior")
        or raw.lower().startswith("[cont.")
    )


def _final_latex(record: StagingProblemRecord) -> str:
    normalized = dict(record.normalized or {})
    return str(normalized.get("latex_rendered_item") or "").strip()


def _copy_image(src: str, target: Path) -> str:
    source = Path(str(src or "")).expanduser()
    if not source.exists() or not source.is_file():
        return ""
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    return str(target)


def _continuation_records(parent: StagingProblemRecord, all_records: Iterable[StagingProblemRecord]) -> list[StagingProblemRecord]:
    normalized = dict(parent.normalized or {})
    fused = normalized.get("continuaciones_fusionadas") if isinstance(normalized.get("continuaciones_fusionadas"), list) else []
    wanted_ids = {
        str(item.get("record_id") or "").strip()
        for item in fused
        if isinstance(item, dict) and str(item.get("record_id") or "").strip()
    }
    rows = list(all_records or [])
    by_id = {str(row.record_id or ""): row for row in rows}
    out: list[StagingProblemRecord] = []
    seen: set[str] = set()

    def add(row: StagingProblemRecord | None) -> None:
        if row is None or not _is_continuation(row):
            return
        key = str(row.record_id or "")
        if not key or key in seen:
            return
        out.append(row)
        seen.add(key)

    for record_id in sorted(wanted_ids):
        add(by_id.get(record_id))

    parent_id = str(parent.record_id or "")
    for row in rows:
        continuation = row.normalized.get("continuacion") if isinstance(row.normalized.get("continuacion"), dict) else {}
        if str(continuation.get("parent_record_id") or "").strip() == parent_id:
            add(row)
    return out


def _image_entries(
    *,
    bank_root: Path,
    sample_id: str,
    record: StagingProblemRecord,
    continuations: list[StagingProblemRecord],
) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    all_images = [("main", record), *[(f"continuation_{index:02d}", row) for index, row in enumerate(continuations, start=1)]]
    for label, row in all_images:
        suffix = Path(str(row.crop_path or "")).suffix or ".png"
        target = bank_root / "images" / f"{sample_id}__{label}{suffix}"
        copied = _copy_image(row.crop_path, target)
        images.append(
            {
                "role": label,
                "record_id": row.record_id,
                "crop_id": row.crop_id,
                "source_path": row.crop_path,
                "bank_path": copied,
            }
        )
    return images


def load_manifest(root: Path | None = None) -> dict[str, Any]:
    bank_root = _bank_root(root)
    path = bank_root / "manifest.json"
    if not path.exists():
        return {
            "schema_version": BANK_SCHEMA_VERSION,
            "root": str(bank_root),
            "manifest_path": str(path),
            "threshold": DEFAULT_THRESHOLD,
            "samples_total": 0,
            "ready_to_train": False,
            "samples_jsonl": str(bank_root / "samples.jsonl"),
            "samples_dir": str(bank_root / "samples"),
            "images_dir": str(bank_root / "images"),
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _rewrite_index(bank_root: Path, *, threshold: int = DEFAULT_THRESHOLD) -> dict[str, Any]:
    samples_dir = bank_root / "samples"
    rows: list[dict[str, Any]] = []
    for path in sorted(samples_dir.glob("*.json"), key=lambda item: item.name.lower()):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    rows.sort(key=lambda row: (str(row.get("created_at") or ""), str(row.get("sample_id") or "")))
    (bank_root / "samples.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    previous = load_manifest(bank_root)
    reached_before = str(previous.get("threshold_reached_at") or "").strip()
    ready = len(rows) >= int(threshold)
    manifest = {
        "schema_version": BANK_SCHEMA_VERSION,
        "updated_at": _now(),
        "root": str(bank_root),
        "manifest_path": str(bank_root / "manifest.json"),
        "threshold": int(threshold),
        "samples_total": len(rows),
        "ready_to_train": ready,
        "threshold_reached_at": reached_before or (_now() if ready else ""),
        "samples_jsonl": str(bank_root / "samples.jsonl"),
        "samples_dir": str(samples_dir),
        "images_dir": str(bank_root / "images"),
        "next_action": "train_normalizer_v1" if ready else "collect_more_samples",
        "remaining_to_threshold": max(0, int(threshold) - len(rows)),
    }
    (bank_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def remove_sample(
    context: InstancePipelineContext,
    record: StagingProblemRecord,
    *,
    root: Path | None = None,
    threshold: int = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    bank_root = _bank_root(root)
    sample_id = _sample_id(context, record)
    sample_path = bank_root / "samples" / f"{sample_id}.json"
    if sample_path.exists():
        sample_path.unlink()
    return _rewrite_index(bank_root, threshold=threshold)


def upsert_sample(
    context: InstancePipelineContext,
    record: StagingProblemRecord,
    *,
    staging_root: Path,
    all_records: Iterable[StagingProblemRecord] = (),
    root: Path | None = None,
    threshold: int = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    bank_root = _bank_root(root)
    bank_root.mkdir(parents=True, exist_ok=True)
    (bank_root / "samples").mkdir(parents=True, exist_ok=True)
    if _is_continuation(record) or not _final_latex(record):
        return remove_sample(context, record, root=bank_root, threshold=threshold)

    sample_id = _sample_id(context, record)
    sample_path = bank_root / "samples" / f"{sample_id}.json"
    existing: dict[str, Any] = {}
    if sample_path.exists():
        try:
            payload = json.loads(sample_path.read_text(encoding="utf-8"))
            existing = payload if isinstance(payload, dict) else {}
        except Exception:
            existing = {}
    continuations = _continuation_records(record, all_records)
    row = {
        "schema_version": SAMPLE_SCHEMA_VERSION,
        "sample_id": sample_id,
        "created_at": existing.get("created_at") or _now(),
        "updated_at": _now(),
        "context": context.to_dict(),
        "staging_root": str(staging_root),
        "record_id": record.record_id,
        "crop_id": record.crop_id,
        "crop_path": record.crop_path,
        "raw_ocr": record.raw_ocr,
        "structured_ocr": dict(record.structured_ocr or {}),
        "figure_segmentation": dict(record.figure_segmentation or {}),
        "source": dict(record.source or {}),
        "models": dict(record.models or {}),
        "confidence": dict(record.confidence or {}),
        "normalized_human": dict(record.normalized or {}),
        "final_latex": _final_latex(record),
        "continuations": [
            {
                "record_id": row.record_id,
                "crop_id": row.crop_id,
                "crop_path": row.crop_path,
                "raw_ocr": row.raw_ocr,
            }
            for row in continuations
        ],
        "images": _image_entries(bank_root=bank_root, sample_id=sample_id, record=record, continuations=continuations),
        "intended_use": "normalizer_final_latex_training",
    }
    sample_path.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    return _rewrite_index(bank_root, threshold=threshold)
