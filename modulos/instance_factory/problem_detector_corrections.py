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
CLASS_ID_PROBLEM_NUMBER = 1
CLASS_NAME_PROBLEM_NUMBER = "problem_number"
CLASS_ID_ANSWER_BLOCK = 2
CLASS_NAME_ANSWER_BLOCK = "answer_block"
CLASS_MAP = {
    CLASS_ID_PROBLEM: CLASS_NAME_PROBLEM,
    CLASS_ID_PROBLEM_NUMBER: CLASS_NAME_PROBLEM_NUMBER,
    CLASS_ID_ANSWER_BLOCK: CLASS_NAME_ANSWER_BLOCK,
}
CLASS_NAME_TO_ID = {name: class_id for class_id, name in CLASS_MAP.items()}
DEFAULT_SIGNIFICANT_DELTA_PX = 4


def default_corrections_root(context: InstancePipelineContext) -> Path:
    configured = str(os.getenv("PDF_PROBLEM_DETECTOR_CORRECTIONS_ROOT") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve() / safe_name(context.instance_name, "instancia", max_len=72)
    if str(context.workspace_dir or "").strip():
        from utils.project_layout import project_dirs

        layout = project_dirs(Path(context.workspace_dir), context.normalized_instance_type)
        return layout["datasets_dir"] / "problem_detector_corrections"
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
    previous_detections: list[Any] | None = None,
    human_detections: list[Any] | None = None,
    baseline_reviewed: bool = False,
    root: Path | None = None,
    significant_delta_px: int = DEFAULT_SIGNIFICANT_DELTA_PX,
    force: bool = False,
    capture_reason: str = "",
) -> dict[str, Any]:
    previous = _coerce_boxes(previous_boxes)
    human = _coerce_boxes(human_boxes)
    previous_labeled = _coerce_labeled_boxes(previous_detections) if previous_detections is not None else _labeled_problem_boxes(previous)
    human_labeled = _coerce_labeled_boxes(human_detections) if human_detections is not None else _labeled_problem_boxes(human)
    change_summary = summarize_labeled_box_changes(previous_labeled, human_labeled, significant_delta_px=significant_delta_px)
    if not force and not _should_save(change_summary):
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
    _mkdir(images_dir)
    _mkdir(labels_dir)
    _mkdir(metadata_dir)

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
    yolo_lines = [_to_yolo_line(row["xyxy"], width, height, class_id=int(row["class_id"])) for row in human_labeled]
    yolo_lines = [line for line in yolo_lines if line]
    label_target = labels_dir / f"{correction_id}.txt"
    _write_text(label_target, ("\n".join(yolo_lines) + "\n") if yolo_lines else "")

    metadata_target = metadata_dir / f"{correction_id}.json"
    existing: dict[str, Any] = {}
    if _path_exists(metadata_target):
        try:
            existing = json.loads(_read_text(metadata_target))
            existing = existing if isinstance(existing, dict) else {}
        except Exception:
            existing = {}
    now = utc_now_text()
    history = _history_from_existing(existing)
    revision_count = max(0, int(existing.get("revision_count") or 0)) + 1 if existing else 1
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "correction_id": correction_id,
        "created_at": str(existing.get("created_at") or now),
        "updated_at": now,
        "revision_count": revision_count,
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
        "class_map": _class_map_for_rows(human_labeled),
        "model_name": _model_name_from_detector_source(detector_source),
        "detector_source": str(detector_source or ""),
        "baseline_reviewed_before": bool(baseline_reviewed),
        "forced_training_capture": bool(force),
        "capture_reason": str(capture_reason or ("manual_training_capture" if force else "human_correction")),
        "layout_mode": str(layout_mode or "auto"),
        "model_boxes": _labeled_box_rows(previous_labeled),
        "human_boxes": _labeled_box_rows(human_labeled, include_order=True),
        "change_summary": change_summary,
        "correction_history": history,
        "training_target": "pdf_problem_detector_yolov8_multiclass_boxes" if _has_multiclass_rows(human_labeled) else "pdf_problem_detector_yolov8_problem_boxes",
        "excluded_future_scope": ["problem_vs_solution_classification"],
    }
    _write_text(metadata_target, json.dumps(metadata, ensure_ascii=False, indent=2))
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


def summarize_labeled_box_changes(
    previous_boxes: list[dict[str, Any]],
    human_boxes: list[dict[str, Any]],
    *,
    significant_delta_px: int = DEFAULT_SIGNIFICANT_DELTA_PX,
) -> dict[str, Any]:
    previous_by_class: dict[int, list[tuple[int, int, int, int]]] = {}
    human_by_class: dict[int, list[tuple[int, int, int, int]]] = {}
    for row in previous_boxes:
        previous_by_class.setdefault(int(row["class_id"]), []).append(tuple(int(v) for v in row["xyxy"]))
    for row in human_boxes:
        human_by_class.setdefault(int(row["class_id"]), []).append(tuple(int(v) for v in row["xyxy"]))
    totals = {
        "added": 0,
        "removed": 0,
        "moved_or_resized": 0,
        "reordered": 0,
        "previous_total": len(previous_boxes),
        "human_total": len(human_boxes),
        "significant_delta_px": max(0, int(significant_delta_px)),
        "by_class": {},
    }
    for class_id in sorted(set(previous_by_class) | set(human_by_class)):
        summary = summarize_box_changes(
            previous_by_class.get(class_id, []),
            human_by_class.get(class_id, []),
            significant_delta_px=significant_delta_px,
        )
        class_name = CLASS_MAP.get(class_id, str(class_id))
        totals["by_class"][class_name] = summary
        for key in ("added", "removed", "moved_or_resized", "reordered"):
            totals[key] += int(summary.get(key) or 0)
    return totals


def rewrite_manifest(root: Path) -> dict[str, Any]:
    dataset_root = Path(root).expanduser().resolve()
    metadata_dir = dataset_root / "metadata"
    rows: list[dict[str, Any]] = []
    for path in _iter_json_files(metadata_dir):
        try:
            payload = json.loads(_read_text(path))
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
        "class_map": {str(key): value for key, value in CLASS_MAP.items()},
        "counts_by_change": by_change,
        "revision_events_total": sum(max(1, int(row.get("revision_count") or 1)) for row in rows),
        "policy": {
            "save_only_human_modified_model_boxes": False,
            "allows_reviewed_page_training_capture": True,
            "problem_vs_solution_classification": "excluded_for_now",
        },
    }
    _mkdir(dataset_root)
    _write_text(dataset_root / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def _copy_page_image_as_png(source: Path, target: Path) -> tuple[int, int]:
    _mkdir(target.parent)
    try:
        with Image.open(_fs_path(source)) as image:
            width, height = image.size
            if source.suffix.lower() == ".png":
                try:
                    if source.resolve() != target.resolve():
                        shutil.copy2(_fs_path(source), _fs_path(target))
                    return int(width), int(height)
                except FileNotFoundError:
                    pass
            image.convert("RGB").save(_fs_path(target), format="PNG")
            return int(width), int(height)
    except Exception:
        raise


def _fs_path(path: Path) -> str:
    """Return a filesystem path usable with long Windows paths."""
    resolved = Path(path).expanduser().resolve()
    text = str(resolved)
    if os.name != "nt" or text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


def _mkdir(path: Path) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def _path_exists(path: Path) -> bool:
    return os.path.exists(_fs_path(path))


def _read_text(path: Path) -> str:
    with open(_fs_path(path), "r", encoding="utf-8") as handle:
        return handle.read()


def _write_text(path: Path, text: str) -> None:
    _mkdir(Path(path).parent)
    with open(_fs_path(path), "w", encoding="utf-8") as handle:
        handle.write(text)


def _iter_json_files(directory: Path) -> list[Path]:
    if not _path_exists(directory):
        return []
    rows: list[Path] = []
    try:
        with os.scandir(_fs_path(directory)) as entries:
            for entry in entries:
                if entry.is_file() and entry.name.lower().endswith(".json"):
                    rows.append(Path(directory) / entry.name)
    except FileNotFoundError:
        return []
    return sorted(rows, key=lambda item: item.name.lower())


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


def _class_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    key = "".join(ch if ch.isalnum() else "_" for ch in text).strip("_")
    while "__" in key:
        key = key.replace("__", "_")
    if key in {"problem_number", "numero", "number"}:
        return CLASS_NAME_PROBLEM_NUMBER
    if key in {"answer_block", "alternatives", "alternativas", "options"}:
        return CLASS_NAME_ANSWER_BLOCK
    return CLASS_NAME_PROBLEM


def _labeled_problem_boxes(boxes: list[tuple[int, int, int, int]]) -> list[dict[str, Any]]:
    return [
        {
            "class": CLASS_NAME_PROBLEM,
            "class_id": CLASS_ID_PROBLEM,
            "xyxy": [int(value) for value in box],
        }
        for box in boxes
    ]


def _coerce_labeled_boxes(raw_rows: list[Any] | tuple[Any, ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in list(raw_rows or []):
        if isinstance(raw, dict):
            bbox_raw = raw.get("bbox_px") or raw.get("xyxy") or []
            class_name = _class_key(raw.get("class_key") or raw.get("class_name") or raw.get("role") or raw.get("class"))
            class_id = CLASS_NAME_TO_ID.get(class_name, CLASS_ID_PROBLEM)
        else:
            bbox_raw = raw
            class_name = CLASS_NAME_PROBLEM
            class_id = CLASS_ID_PROBLEM
        boxes = _coerce_boxes([bbox_raw])
        if not boxes:
            continue
        rows.append(
            {
                "class": class_name,
                "class_id": int(class_id),
                "xyxy": [int(value) for value in boxes[0]],
            }
        )
    return rows


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


def _labeled_box_rows(rows: list[dict[str, Any]], *, include_order: bool = False) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        item = {
            "class": str(row.get("class") or CLASS_MAP.get(int(row.get("class_id") or 0), CLASS_NAME_PROBLEM)),
            "class_id": int(row.get("class_id") or 0),
            "xyxy": [int(value) for value in list(row.get("xyxy") or [])[:4]],
        }
        if include_order:
            item["order"] = index
        result.append(item)
    return result


def _class_map_for_rows(rows: list[dict[str, Any]]) -> dict[str, str]:
    class_ids = {int(row.get("class_id") or 0) for row in rows} or {CLASS_ID_PROBLEM}
    return {str(class_id): CLASS_MAP.get(class_id, str(class_id)) for class_id in sorted(class_ids)}


def _has_multiclass_rows(rows: list[dict[str, Any]]) -> bool:
    return any(int(row.get("class_id") or 0) != CLASS_ID_PROBLEM for row in rows)


def _to_yolo_line(box: tuple[int, int, int, int] | list[int], width: int, height: int, *, class_id: int = CLASS_ID_PROBLEM) -> str:
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
    return f"{int(class_id)} {x_center:.6f} {y_center:.6f} {box_width:.6f} {box_height:.6f}"


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


def _history_from_existing(existing: dict[str, Any]) -> list[dict[str, Any]]:
    if not existing:
        return []
    history = existing.get("correction_history") if isinstance(existing.get("correction_history"), list) else []
    event = {
        "updated_at": str(existing.get("updated_at") or ""),
        "layout_mode": str(existing.get("layout_mode") or ""),
        "model_boxes": existing.get("model_boxes") if isinstance(existing.get("model_boxes"), list) else [],
        "human_boxes": existing.get("human_boxes") if isinstance(existing.get("human_boxes"), list) else [],
        "change_summary": existing.get("change_summary") if isinstance(existing.get("change_summary"), dict) else {},
    }
    return [*history, event][-50:]


def _model_name_from_detector_source(detector_source: str) -> str:
    source = str(detector_source or "").strip()
    if ":" in source:
        return source.split(":", 1)[1].strip() or source
    return source or "unknown_pdf_problem_detector"
