from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from PIL import Image

from modulos.modulo13_laboratorio_pdf_segmentacion.controlador_laboratorio_pdf import safe_name

from .models import InstancePipelineContext, StagingProblemRecord, utc_now_text
from .ocr_training_bank import upsert_raw_ocr_correction
from .problem_detector_corrections import maybe_write_problem_detector_correction


REPO_ROOT = Path(__file__).resolve().parents[2]
FIGURE_BANK_SCHEMA_VERSION = "segment_training_live_manifest_v1"
FIGURE_SAMPLE_SCHEMA_VERSION = "figure_segmenter_correction_v1"
DEFAULT_FIGURE_BANK_ROOT = REPO_ROOT / ".cache" / "transcriptor_runs" / "datasets" / "segment_training_live"


def persist_problem_detector_correction(**kwargs: Any) -> dict[str, Any]:
    return maybe_write_problem_detector_correction(**kwargs)


def persist_raw_ocr_correction(
    context: InstancePipelineContext,
    record: StagingProblemRecord,
    *,
    corrected_text: str,
    previous_text: str = "",
    root: Path | None = None,
) -> dict[str, Any]:
    return upsert_raw_ocr_correction(
        context,
        record,
        corrected_text=corrected_text,
        previous_text=previous_text,
        root=root,
    )


def default_figure_segmenter_bank_root() -> Path:
    raw = str(os.getenv("SEGMENT_LIVE_GOLDEN_BASE") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return DEFAULT_FIGURE_BANK_ROOT.expanduser().resolve()


def persist_figure_segment_correction(
    context: InstancePipelineContext,
    record: StagingProblemRecord,
    *,
    boxes: list[Any],
    detector_payload: dict[str, Any] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    clean_boxes = [_coerce_box(box) for box in list(boxes or [])]
    clean_boxes = [box for box in clean_boxes if box]
    bank_root = Path(root or default_figure_segmenter_bank_root()).expanduser().resolve()
    images_dir = bank_root / "images"
    labels_dir = bank_root / "labels"
    metadata_dir = bank_root / "metadata"
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    sample_id = _sample_id(context, record)
    source_path = Path(str(record.crop_path or "")).expanduser()
    image_target = images_dir / f"{sample_id}_{safe_name(source_path.stem, 'crop', max_len=60)}.png"
    width, height = _copy_image_as_png(source_path, image_target)
    label_path = labels_dir / f"{sample_id}.txt"
    yolo_lines = [_to_yolo_line(box, width, height) for box in clean_boxes]
    yolo_lines = [line for line in yolo_lines if line]
    label_path.write_text(("\n".join(yolo_lines) + "\n") if yolo_lines else "", encoding="utf-8")

    metadata_path = metadata_dir / f"{sample_id}.json"
    existing = _read_json(metadata_path)
    now = utc_now_text()
    revision_count = max(0, int(existing.get("revision_count") or 0)) + 1 if existing else 1
    metadata = {
        "schema_version": FIGURE_SAMPLE_SCHEMA_VERSION,
        "sample_id": sample_id,
        "status": "corrected" if clean_boxes else "empty_reviewed",
        "created_at": str(existing.get("created_at") or now),
        "updated_at": now,
        "revision_count": revision_count,
        "book_code": context.book_code,
        "instance_type": context.instance_type,
        "project_name": context.project_name,
        "record_id": record.record_id,
        "crop_id": record.crop_id,
        "source_crop_path": str(source_path),
        "image_path": str(image_target),
        "label_path": str(label_path),
        "metadata_path": str(metadata_path),
        "image_rel": f"images/{image_target.name}",
        "label_rel": f"labels/{label_path.name}",
        "metadata_rel": f"metadata/{metadata_path.name}",
        "image_size": {"width": int(width), "height": int(height)},
        "class_map": {"0": "figure"},
        "human_boxes": [{"class_id": 0, "class_name": "figure", "bbox_px": list(box)} for box in clean_boxes],
        "detector_payload": dict(detector_payload or {}),
        "origin": {
            "type": "pdf_factory_figure_segment_review",
            "source": dict(record.source or {}),
            "models": dict(record.models or {}),
        },
        "correction_history": _history_from_existing(existing),
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = rewrite_figure_segmenter_manifest(bank_root)
    return {
        "schema_version": FIGURE_SAMPLE_SCHEMA_VERSION,
        "saved": True,
        "sample_id": sample_id,
        "record_path": str(metadata_path),
        "label_path": str(label_path),
        "image_path": str(image_target),
        "manifest_path": str(manifest.get("manifest_path") or ""),
        "samples_total": int(manifest.get("samples_total") or 0),
        "corrected_images": int(manifest.get("corrected_images") or 0),
        "revision_count": revision_count,
    }


def rewrite_figure_segmenter_manifest(root: Path) -> dict[str, Any]:
    bank_root = Path(root).expanduser().resolve()
    metadata_dir = bank_root / "metadata"
    rows: list[dict[str, Any]] = []
    for path in sorted(metadata_dir.glob("*.json"), key=lambda item: item.name.lower()) if metadata_dir.exists() else []:
        payload = _read_json(path)
        if payload:
            rows.append(payload)
    corrected = [row for row in rows if str(row.get("status") or "") == "corrected"]
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
        "schema_version": FIGURE_BANK_SCHEMA_VERSION,
        "updated_at": utc_now_text(),
        "root": str(bank_root),
        "manifest_path": str(bank_root / "manifest.json"),
        "samples_total": len(rows),
        "corrected_images": len(corrected),
        "records_total": len(rows),
        "records_confirmed": len(corrected),
        "revision_events_total": sum(max(1, int(row.get("revision_count") or 1)) for row in rows),
        "files": {
            "records_all": "records_all.jsonl",
            "records_corrected": "records_corrected.jsonl",
            "images_dir": "images",
            "labels_dir": "labels",
            "metadata_dir": "metadata",
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


def _coerce_box(raw: Any) -> tuple[int, int, int, int] | None:
    if isinstance(raw, dict):
        raw = raw.get("bbox_px") or raw.get("bbox") or raw.get("xyxy")
    if not isinstance(raw, (list, tuple)) or len(raw) < 4:
        return None
    try:
        x1, y1, x2, y2 = [int(round(float(value))) for value in raw[:4]]
    except Exception:
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def _copy_image_as_png(source: Path, target: Path) -> tuple[int, int]:
    if not source.exists() or not source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1, 1), "white").save(target)
        return 1, 1
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source) as image:
            rgb = image.convert("RGB")
            rgb.save(target)
            return int(rgb.width), int(rgb.height)
    except Exception:
        shutil.copy2(source, target)
        with Image.open(target) as image:
            return int(image.width), int(image.height)


def _to_yolo_line(box: tuple[int, int, int, int], width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return ""
    x1, y1, x2, y2 = box
    x1 = max(0, min(int(width), x1))
    x2 = max(0, min(int(width), x2))
    y1 = max(0, min(int(height), y1))
    y2 = max(0, min(int(height), y2))
    if x2 <= x1 or y2 <= y1:
        return ""
    cx = ((x1 + x2) / 2.0) / float(width)
    cy = ((y1 + y2) / 2.0) / float(height)
    bw = (x2 - x1) / float(width)
    bh = (y2 - y1) / float(height)
    return f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"


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
        "human_boxes": list(existing.get("human_boxes") or []),
    }
    return [*history, event][-50:]
