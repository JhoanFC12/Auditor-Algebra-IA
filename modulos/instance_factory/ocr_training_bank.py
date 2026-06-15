from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from modulos.modulo13_laboratorio_pdf_segmentacion.controlador_laboratorio_pdf import safe_name

from .models import InstancePipelineContext, StagingProblemRecord, utc_now_text


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BANK_ROOT = REPO_ROOT / ".cache" / "transcriptor_runs" / "datasets" / "ocr_golden_live"
BANK_SCHEMA_VERSION = "ocr_golden_live_index_v1"
SAMPLE_SCHEMA_VERSION = "ocr_golden_live_v1"


def default_bank_root() -> Path:
    raw = str(os.getenv("OCR_TRAINING_BANK_ROOTS") or "").strip()
    if raw:
        first = next((chunk.strip() for chunk in raw.split(os.pathsep) if chunk.strip()), "")
        if first:
            return Path(first).expanduser().resolve()
    return DEFAULT_BANK_ROOT.expanduser().resolve()


def upsert_raw_ocr_correction(
    context: InstancePipelineContext,
    record: StagingProblemRecord,
    *,
    corrected_text: str,
    previous_text: str = "",
    root: Path | None = None,
) -> dict[str, Any]:
    bank_root = Path(root or default_bank_root()).expanduser().resolve()
    records_dir = bank_root / "records"
    images_dir = bank_root / "images"
    records_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    sample_id = _sample_id(context, record)
    record_path = records_dir / f"{sample_id}.json"
    existing = _read_json(record_path)
    now = utc_now_text()
    source_path = Path(str(record.crop_path or "")).expanduser()
    copied_rel = _copy_source_image(source_path, images_dir, sample_id)
    previous_clean = str(previous_text or "")
    corrected_clean = str(corrected_text or "")
    history = _history_from_existing(existing)
    revision_count = max(0, int(existing.get("revision_count") or 0)) + 1 if existing else 1
    session_json = ""
    try:
        resolved = context.resolved_session_path()
        session_json = str(resolved or "")
    except Exception:
        session_json = ""
    source_label = _source_label(context, record)
    row = {
        "schema_version": SAMPLE_SCHEMA_VERSION,
        "record_id": sample_id,
        "status": "corrected" if corrected_clean.strip() else "pending",
        "created_at": str(existing.get("created_at") or now),
        "updated_at": now,
        "revision_count": revision_count,
        "book_code": context.book_code,
        "instance_type": context.instance_type,
        "project_name": context.project_name,
        "session_json": session_json,
        "source_label": source_label,
        "source_path": str(source_path),
        "copied_image_rel": copied_rel,
        "ocr_text": previous_clean or corrected_clean,
        "ocr_model_text": previous_clean,
        "corrected_text": corrected_clean,
        "notes": "Capturado desde Fabrica PDF: editor OCR crudo.",
        "origin": {
            "type": "pdf_factory_staging_raw_ocr_review",
            "record_id": record.record_id,
            "crop_id": record.crop_id,
            "crop_path": record.crop_path,
            "source": dict(record.source or {}),
            "models": dict(record.models or {}),
        },
        "correction_history": history,
    }
    record_path.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = rewrite_manifest(bank_root)
    return {
        "schema_version": SAMPLE_SCHEMA_VERSION,
        "saved": bool(corrected_clean.strip()),
        "record_id": sample_id,
        "record_path": str(record_path),
        "manifest_path": str(manifest.get("manifest_path") or ""),
        "records_corrected": int(manifest.get("records_corrected") or 0),
        "revision_count": revision_count,
    }


def rewrite_manifest(root: Path) -> dict[str, Any]:
    bank_root = Path(root).expanduser().resolve()
    records_dir = bank_root / "records"
    rows: list[dict[str, Any]] = []
    for path in sorted(records_dir.glob("*.json"), key=lambda item: item.name.lower()) if records_dir.exists() else []:
        payload = _read_json(path)
        if payload:
            rows.append(payload)
    rows.sort(key=lambda row: (str(row.get("book_code") or ""), str(row.get("source_label") or "")))
    corrected = [row for row in rows if str(row.get("corrected_text") or "").strip()]
    bank_root.mkdir(parents=True, exist_ok=True)
    (bank_root / "records_all.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )
    (bank_root / "records_corrected.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in corrected) + ("\n" if corrected else ""),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": BANK_SCHEMA_VERSION,
        "updated_at": utc_now_text(),
        "root": str(bank_root),
        "manifest_path": str(bank_root / "manifest.json"),
        "records_total": len(rows),
        "records_corrected": len(corrected),
        "records_pending": max(0, len(rows) - len(corrected)),
        "revision_events_total": sum(max(1, int(row.get("revision_count") or 1)) for row in rows),
        "files": {
            "records_all": "records_all.jsonl",
            "records_corrected": "records_corrected.jsonl",
            "records_dir": "records",
            "images_dir": "images",
        },
    }
    (bank_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _sample_id(context: InstancePipelineContext, record: StagingProblemRecord) -> str:
    key = "|".join(
        [
            context.book_code,
            context.instance_type,
            str(record.record_id or ""),
            str(record.crop_id or ""),
            str(record.crop_path or ""),
        ]
    ).lower()
    return hashlib.sha1(key.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _source_label(context: InstancePipelineContext, record: StagingProblemRecord) -> str:
    raw = "__".join(
        [
            context.book_code or "libro",
            context.instance_type or "instancia",
            str(record.crop_id or record.record_id or "crop"),
        ]
    )
    return safe_name(raw, "ocr_source", max_len=120)


def _copy_source_image(source: Path, images_dir: Path, sample_id: str) -> str:
    if not source.exists() or not source.is_file():
        return ""
    suffix = source.suffix.lower() or ".png"
    stem = safe_name(source.stem, "crop", max_len=64)
    target = images_dir / f"{sample_id}_{stem}{suffix}"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or source.stat().st_mtime_ns != target.stat().st_mtime_ns:
            shutil.copy2(source, target)
    except Exception:
        return ""
    return str(target.relative_to(images_dir.parent)).replace("\\", "/")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _history_from_existing(existing: dict[str, Any]) -> list[dict[str, Any]]:
    if not existing:
        return []
    history = existing.get("correction_history") if isinstance(existing.get("correction_history"), list) else []
    event = {
        "updated_at": str(existing.get("updated_at") or ""),
        "status": str(existing.get("status") or ""),
        "ocr_text": str(existing.get("ocr_text") or ""),
        "ocr_model_text": str(existing.get("ocr_model_text") or ""),
        "corrected_text": str(existing.get("corrected_text") or ""),
    }
    return [*history, event][-50:]
