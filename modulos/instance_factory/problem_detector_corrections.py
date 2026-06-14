from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from PIL import Image

from modulos.modulo13_laboratorio_pdf_segmentacion.controlador_laboratorio_pdf import compact_id, safe_name

from .models import InstancePipelineContext, utc_now_text


SCHEMA_VERSION = "problem_detector_correction_v1"
MANIFEST_SCHEMA_VERSION = "problem_detector_corrections_manifest_v1"
CLASS_ID_PROBLEM = 0
CLASS_NAME_PROBLEM = "problem"
DEFAULT_SIGNIFICANT_DELTA_PX = 4


def default_corrections_root(context: InstancePipelineContext) -> Path:
    configured = str(os.getenv("PDF_PROBLEM_DETECTOR_CORRECTIONS_ROOT") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve() / safe_name(context.instance_name, "instancia", max_len=72)
    return context.staging_root().parent / "problem_detector_corrections"


def maybe_write_problem_detector_correction(
    *,
    context: InstancePipelineContext,
    page_record_id: str,
    page_number: int,
    page_image: Path,
    pdf_path: str,
    detector_source: str,
    layout_mode: str,
    previous_boxes: list[Any],
    human_boxes: list[Any],
    baseline_reviewed: bool = False,
    root: Path | None = None,
    significant_delta_px: int = DEFAULT_SIGNIFICANT_DELTA_PX,
) -> dict[str, Any]:
    previous = _coerce_boxes(previous_boxes)
    human = _coerce_boxes(human_boxes)
    change_summary = summarize_box_changes(previous, human, significant_delta_px=significant_delta_px)
    if not _should_save(change_summary):
        return {
            "schema_version": SCHEMA_VERSION,
            "saved": False,
            "reason": "no_significant_change",
            "change_summary": change_summary,
        }

    dataset_root = Path(root or default_corrections_root(context)).expanduser().resolve()
    images_dir = dataset_root / "images"
    labels_dir = dataset_root / "labels"
    metadata_dir = dataset_root / "metadata"
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    correction_id = compact_id(
        context.book_code,
        context.instance_type,
        f"p{int(page_number):04d}",
        page_record_id,
        prefix="page",
        max_len=72,
    )
    source_image = Path(page_image).expanduser()
    image_target = images_dir / f"{correction_id}.png"
    width, height = _copy_page_image_as_png(source_image, image_target)
    yolo_lines = [_to_yolo_line(box, width, height) for box in human]
    yolo_lines = [line for line in yolo_lines if line]
    label_target = labels_dir / f"{correction_id}.txt"
    label_target.write_text(("\n".join(yolo_lines) + "\n") if yolo_lines else "", encoding="utf-8")

    metadata_target = metadata_dir / f"{correction_id}.json"
    existing_created_at = ""
    if metadata_target.exists():
        try:
            existing = json.loads(metadata_target.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                existing_created_at = str(existing.get("created_at") or "")
        except Exception:
            existing_created_at = ""
    now = utc_now_text()
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "correction_id": correction_id,
        "created_at": existing_created_at or now,
        "updated_at": now,
        "book_code": context.book_code,
        "instance_type": context.instance_type,
        "project_name": context.project_name,
        "page_number": int(page_number),
        "page_record_id": str(page_record_id or ""),
        "source_pdf": str(pdf_path or context.pdf_path or ""),
        "source_page_image": str(source_image),
        "dataset_root": str(dataset_root),
        "image_path": str(image_target),
        "label_path": str(label_target),
        "metadata_path": str(metadata_target),
        "image_rel": f"images/{image_target.name}",
        "label_rel": f"labels/{label_target.name}",
        "metadata_rel": f"metadata/{metadata_target.name}",
        "image_size": {"width": int(width), "height": int(height)},
        "class_map": {str(CLASS_ID_PROBLEM): CLASS_NAME_PROBLEM},
        "model_name": _model_name_from_detector_source(detector_source),
        "detector_source": str(detector_source or ""),
        "baseline_reviewed_before": bool(baseline_reviewed),
        "layout_mode": str(layout_mode or "auto"),
        "model_boxes": _box_rows(previous),
        "human_boxes": _box_rows(human, include_order=True),
        "change_summary": change_summary,
        "training_target": "pdf_problem_detector_yolov8_problem_boxes",
        "excluded_future_scope": ["problem_vs_solution_classification"],
    }
    metadata_target.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = rewrite_manifest(dataset_root)
    return {
        "schema_version": SCHEMA_VERSION,
        "saved": True,
        "correction_id": correction_id,
        "dataset_root": str(dataset_root),
        "image_path": str(image_target),
        "label_path": str(label_target),
        "metadata_path": str(metadata_target),
        "manifest_path": str(manifest.get("manifest_path") or ""),
        "change_summary": change_summary,
    }


def summarize_box_changes(
    previous_boxes: list[Any],
    human_boxes: list[Any],
    *,
    significant_delta_px: int = DEFAULT_SIGNIFICANT_DELTA_PX,
) -> dict[str, Any]:
    previous = _coerce_boxes(previous_boxes)
    human = _coerce_boxes(human_boxes)
    threshold = max(0, int(significant_delta_px))
    matches = _match_boxes(previous, human)
    matched_previous = {left for left, _right in matches}
    matched_human = {right for _left, right in matches}
    moved_or_resized = 0
    for left, right in matches:
        if _max_coord_delta(previous[left], human[right]) > threshold:
            moved_or_resized += 1
    if len(previous) == len(human) and not matches and previous != human:
        moved_or_resized = len(human)
    reordered = 0
    if len(previous) == len(human) and previous != human:
        if sorted(previous) == sorted(human):
            reordered = 1
        elif any(left != right for left, right in matches):
            reordered = 1
    return {
        "added": max(0, len(human) - len(matched_human)),
        "removed": max(0, len(previous) - len(matched_previous)),
        "moved_or_resized": int(moved_or_resized),
        "reordered": int(reordered),
        "previous_total": len(previous),
        "human_total": len(human),
        "significant_delta_px": threshold,
    }


def rewrite_manifest(root: Path) -> dict[str, Any]:
    dataset_root = Path(root).expanduser().resolve()
    metadata_dir = dataset_root / "metadata"
    rows: list[dict[str, Any]] = []
    for path in sorted(metadata_dir.glob("*.json"), key=lambda item: item.name.lower()) if metadata_dir.exists() else []:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    by_change = {"added": 0, "removed": 0, "moved_or_resized": 0, "reordered": 0}
    for row in rows:
        summary = row.get("change_summary") if isinstance(row.get("change_summary"), dict) else {}
        for key in by_change:
            if int(summary.get(key) or 0) > 0:
                by_change[key] += 1
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "updated_at": utc_now_text(),
        "root": str(dataset_root),
        "manifest_path": str(dataset_root / "manifest.json"),
        "samples_total": len(rows),
        "images_dir": str(dataset_root / "images"),
        "labels_dir": str(dataset_root / "labels"),
        "metadata_dir": str(metadata_dir),
        "class_map": {str(CLASS_ID_PROBLEM): CLASS_NAME_PROBLEM},
        "counts_by_change": by_change,
        "policy": {
            "save_only_human_modified_model_boxes": True,
            "problem_vs_solution_classification": "excluded_for_now",
        },
    }
    dataset_root.mkdir(parents=True, exist_ok=True)
    (dataset_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _copy_page_image_as_png(source: Path, target: Path) -> tuple[int, int]:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source) as image:
            width, height = image.size
            if source.suffix.lower() == ".png":
                try:
                    if source.resolve() != target.resolve():
                        shutil.copy2(source, target)
                    return int(width), int(height)
                except FileNotFoundError:
                    pass
            image.convert("RGB").save(target, format="PNG")
            return int(width), int(height)
    except Exception:
        raise


def _coerce_boxes(raw_boxes: list[Any] | tuple[Any, ...]) -> list[tuple[int, int, int, int]]:
    clean: list[tuple[int, int, int, int]] = []
    for raw in list(raw_boxes or []):
        if isinstance(raw, dict):
            raw = raw.get("bbox_px") or raw.get("xyxy") or []
        if not isinstance(raw, (list, tuple)) or len(raw) < 4:
            continue
        try:
            x1, y1, x2, y2 = [int(round(float(value))) for value in list(raw)[:4]]
        except Exception:
            continue
        left, right = sorted((x1, x2))
        top, bottom = sorted((y1, y2))
        if right <= left or bottom <= top:
            continue
        clean.append((left, top, right, bottom))
    return clean


def _box_rows(boxes: list[tuple[int, int, int, int]], *, include_order: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, box in enumerate(boxes, start=1):
        row: dict[str, Any] = {
            "class": CLASS_NAME_PROBLEM,
            "class_id": CLASS_ID_PROBLEM,
            "xyxy": [int(value) for value in box],
        }
        if include_order:
            row["order"] = index
        rows.append(row)
    return rows


def _to_yolo_line(box: tuple[int, int, int, int], width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return ""
    x1, y1, x2, y2 = [float(value) for value in box]
    x1 = max(0.0, min(float(width), x1))
    x2 = max(0.0, min(float(width), x2))
    y1 = max(0.0, min(float(height), y1))
    y2 = max(0.0, min(float(height), y2))
    if x2 <= x1 or y2 <= y1:
        return ""
    x_center = ((x1 + x2) / 2.0) / float(width)
    y_center = ((y1 + y2) / 2.0) / float(height)
    box_width = (x2 - x1) / float(width)
    box_height = (y2 - y1) / float(height)
    return f"{CLASS_ID_PROBLEM} {x_center:.6f} {y_center:.6f} {box_width:.6f} {box_height:.6f}"


def _match_boxes(
    previous: list[tuple[int, int, int, int]],
    human: list[tuple[int, int, int, int]],
) -> list[tuple[int, int]]:
    candidates: list[tuple[float, int, int]] = []
    for left, previous_box in enumerate(previous):
        for right, human_box in enumerate(human):
            score = _iou(previous_box, human_box)
            if score >= 0.25:
                candidates.append((score, left, right))
    candidates.sort(reverse=True)
    used_left: set[int] = set()
    used_right: set[int] = set()
    matches: list[tuple[int, int]] = []
    for _score, left, right in candidates:
        if left in used_left or right in used_right:
            continue
        used_left.add(left)
        used_right.add(right)
        matches.append((left, right))
    matches.sort()
    return matches


def _iou(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    if inter <= 0:
        return 0.0
    left_area = max(0, left[2] - left[0]) * max(0, left[3] - left[1])
    right_area = max(0, right[2] - right[0]) * max(0, right[3] - right[1])
    union = left_area + right_area - inter
    return float(inter) / float(union) if union > 0 else 0.0


def _max_coord_delta(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> int:
    return max(abs(int(a) - int(b)) for a, b in zip(left, right))


def _should_save(change_summary: dict[str, Any]) -> bool:
    return any(int(change_summary.get(key) or 0) > 0 for key in ("added", "removed", "moved_or_resized", "reordered"))


def _model_name_from_detector_source(detector_source: str) -> str:
    source = str(detector_source or "").strip()
    if ":" in source:
        return source.split(":", 1)[1].strip() or source
    return source or "unknown_pdf_problem_detector"
