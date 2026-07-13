from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image
from ultralytics import YOLO


CLASS_NAMES = {0: "problem", 1: "problem_number", 2: "answer_block"}
CLASS_ORDER = {0: 0, 1: 1, 2: 2}
MODEL_REPO_ID = "Jhoan12/pdf-problem-detector-multiclass-pilot-v1"


@dataclass
class AutoBox:
    cls: int
    cx: float
    cy: float
    w: float
    h: float
    conf: float
    index: int
    image_w: int
    image_h: int

    @property
    def x1(self) -> float:
        return (self.cx - self.w / 2.0) * self.image_w

    @property
    def y1(self) -> float:
        return (self.cy - self.h / 2.0) * self.image_h

    @property
    def x2(self) -> float:
        return (self.cx + self.w / 2.0) * self.image_w

    @property
    def y2(self) -> float:
        return (self.cy + self.h / 2.0) * self.image_h

    @property
    def center_x(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def center_y(self) -> float:
        return (self.y1 + self.y2) / 2.0

    @property
    def area(self) -> float:
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1)

    @property
    def line(self) -> str:
        return f"{self.cls} {self.cx:.6f} {self.cy:.6f} {self.w:.6f} {self.h:.6f}"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_dataset_root() -> Path:
    return repo_root() / ".cache" / "transcriptor_runs" / "datasets" / "problem_detector_multiclass_100_lab_20260624"


def default_model_path() -> Path:
    return repo_root() / "models" / "pdf_problem_detector_multiclass_pilot_v1" / "weights" / "best.pt"


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def is_reviewed(metadata: dict[str, Any]) -> bool:
    label_review = metadata.get("label_review")
    return isinstance(label_review, dict) and bool(label_review.get("reviewed_at"))


def image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return int(image.width), int(image.height)


def clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def box_from_xyxy(
    xyxy: list[float],
    *,
    cls: int,
    conf: float,
    index: int,
    image_w: int,
    image_h: int,
) -> AutoBox | None:
    x1, y1, x2, y2 = xyxy
    x1 = clip(float(x1), 0.0, float(image_w))
    y1 = clip(float(y1), 0.0, float(image_h))
    x2 = clip(float(x2), 0.0, float(image_w))
    y2 = clip(float(y2), 0.0, float(image_h))
    if x2 <= x1 or y2 <= y1:
        return None
    if (x2 - x1) < 2.0 or (y2 - y1) < 2.0:
        return None
    cx = ((x1 + x2) / 2.0) / image_w
    cy = ((y1 + y2) / 2.0) / image_h
    width = (x2 - x1) / image_w
    height = (y2 - y1) / image_h
    return AutoBox(
        cls=cls,
        cx=clip(cx, 0.0, 1.0),
        cy=clip(cy, 0.0, 1.0),
        w=clip(width, 0.0, 1.0),
        h=clip(height, 0.0, 1.0),
        conf=float(conf),
        index=index,
        image_w=image_w,
        image_h=image_h,
    )


def has_two_columns(problems: list[AutoBox], image_w: int) -> bool:
    if len(problems) < 3:
        return False
    narrow = [box for box in problems if (box.x2 - box.x1) < image_w * 0.82]
    if len(narrow) < 2:
        return False
    return any(box.center_x < image_w * 0.45 for box in narrow) and any(box.center_x > image_w * 0.55 for box in narrow)


def reading_column(box: AutoBox, image_w: int, two_columns: bool) -> int:
    if not two_columns:
        return 0
    return 0 if box.center_x < image_w * 0.5 else 1


def problem_sort_key(box: AutoBox, image_w: int, two_columns: bool) -> tuple[int, float, float, int]:
    return (reading_column(box, image_w, two_columns), round(box.y1 / 12.0), box.x1, box.index)


def subbox_sort_key(box: AutoBox) -> tuple[int, float, float, int]:
    return (CLASS_ORDER.get(box.cls, 9), round(box.y1 / 12.0), box.x1, box.index)


def orphan_sort_key(box: AutoBox, image_w: int, two_columns: bool) -> tuple[int, float, float, int, int]:
    return (
        reading_column(box, image_w, two_columns),
        round(box.y1 / 12.0),
        box.x1,
        CLASS_ORDER.get(box.cls, 9),
        box.index,
    )


def overlap_area(a: AutoBox, b: AutoBox) -> float:
    x1 = max(a.x1, b.x1)
    y1 = max(a.y1, b.y1)
    x2 = min(a.x2, b.x2)
    y2 = min(a.y2, b.y2)
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def center_inside(parent: AutoBox, child: AutoBox) -> bool:
    return parent.x1 <= child.center_x <= parent.x2 and parent.y1 <= child.center_y <= parent.y2


def owner_problem(subbox: AutoBox, problems: list[AutoBox]) -> AutoBox | None:
    best: AutoBox | None = None
    best_score = 0.0
    for problem in problems:
        overlap_ratio = overlap_area(problem, subbox) / max(1.0, subbox.area)
        score = overlap_ratio + (1.0 if center_inside(problem, subbox) else 0.0)
        if score > best_score:
            best = problem
            best_score = score
    return best if best_score >= 0.25 else None


def order_boxes(boxes: list[AutoBox], image_w: int) -> list[AutoBox]:
    problems = [box for box in boxes if box.cls == 0]
    if not problems:
        return sorted(boxes, key=lambda box: orphan_sort_key(box, image_w, False))

    two_columns = has_two_columns(problems, image_w)
    ordered_problems = sorted(problems, key=lambda box: problem_sort_key(box, image_w, two_columns))
    children: dict[int, list[AutoBox]] = {id(problem): [] for problem in ordered_problems}
    orphans: list[AutoBox] = []

    for subbox in [box for box in boxes if box.cls != 0]:
        owner = owner_problem(subbox, ordered_problems)
        if owner is None:
            orphans.append(subbox)
        else:
            children[id(owner)].append(subbox)

    ordered: list[AutoBox] = []
    for problem in ordered_problems:
        ordered.append(problem)
        ordered.extend(sorted(children[id(problem)], key=subbox_sort_key))
    ordered.extend(sorted(orphans, key=lambda box: orphan_sort_key(box, image_w, two_columns)))
    return ordered


def predict_boxes(model: YOLO, image_path: Path, *, imgsz: int, conf: float, iou: float, max_det: int) -> list[AutoBox]:
    image_w, image_h = image_size(image_path)
    result = model.predict(
        str(image_path),
        imgsz=imgsz,
        conf=conf,
        iou=iou,
        max_det=max_det,
        verbose=False,
    )[0]

    boxes: list[AutoBox] = []
    result_boxes = result.boxes
    if result_boxes is None:
        return boxes

    xyxys = result_boxes.xyxy.cpu().tolist()
    classes = result_boxes.cls.cpu().tolist()
    confs = result_boxes.conf.cpu().tolist()
    for index, (xyxy, cls_value, conf_value) in enumerate(zip(xyxys, classes, confs)):
        cls = int(cls_value)
        if cls not in CLASS_NAMES:
            continue
        box = box_from_xyxy(
            xyxy,
            cls=cls,
            conf=float(conf_value),
            index=index,
            image_w=image_w,
            image_h=image_h,
        )
        if box is not None:
            boxes.append(box)
    return order_boxes(boxes, image_w)


def count_classes(boxes: list[AutoBox]) -> dict[str, int]:
    counts = {name: 0 for name in CLASS_NAMES.values()}
    for box in boxes:
        counts[CLASS_NAMES.get(box.cls, str(box.cls))] = counts.get(CLASS_NAMES.get(box.cls, str(box.cls)), 0) + 1
    return counts


def make_backup(
    label_path: Path,
    backup_dir: Path,
    *,
    sample_id: str,
    sequence: int,
    manifest: list[dict[str, str]],
) -> Path | None:
    if not label_path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(sample_id.encode("utf-8")).hexdigest()[:12]
    backup_path = backup_dir / f"{sequence:04d}_{digest}.txt"
    shutil.copy2(label_path, backup_path)
    manifest.append(
        {
            "sample_id": sample_id,
            "original_label": str(label_path),
            "backup_label": str(backup_path),
        }
    )
    return backup_path


def update_metadata(
    metadata_path: Path,
    metadata: dict[str, Any],
    *,
    boxes: list[AutoBox],
    backup_path: Path | None,
    model_path: Path,
    model_repo_id: str,
    imgsz: int,
    conf: float,
    iou: float,
    status: str,
) -> None:
    metadata["auto_label"] = {
        "schema_version": "problem_detector_multiclass_auto_label_v1",
        "applied_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "status": status,
        "model_path": str(model_path),
        "model_repo_id": model_repo_id,
        "imgsz": imgsz,
        "conf": conf,
        "iou": iou,
        "detections_total": len(boxes),
        "classes": count_classes(boxes),
        "backup_label_path": str(backup_path) if backup_path is not None else "",
        "note": "Auto-label only. This sample is not marked reviewed.",
    }
    write_json(metadata_path, metadata)


def main() -> None:
    parser = argparse.ArgumentParser(description="Aplica el detector multiclass a muestras pendientes del lab.")
    parser.add_argument("--dataset-root", type=Path, default=default_dataset_root())
    parser.add_argument("--model", type=Path, default=default_model_path())
    parser.add_argument("--model-repo-id", default=MODEL_REPO_ID)
    parser.add_argument("--imgsz", type=int, default=768)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.45)
    parser.add_argument("--max-det", type=int, default=300)
    parser.add_argument("--apply", action="store_true", help="Escribe labels y metadata. Sin esto solo reporta.")
    parser.add_argument("--replace-empty", action="store_true", help="Permite reemplazar labels por archivo vacio.")
    args = parser.parse_args()

    root = args.dataset_root.resolve()
    model_path = args.model.resolve()
    images_dir = root / "images"
    labels_dir = root / "labels"
    metadata_dir = root / "metadata"

    if not root.exists():
        raise SystemExit(f"Dataset no encontrado: {root}")
    if not model_path.exists():
        raise SystemExit(f"Modelo no encontrado: {model_path}")

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    model_tag = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(args.model_repo_id)).strip("_")[-80:]
    backup_dir = root / "label_backups" / f"autolabel_multiclass_{model_tag}_{timestamp}"
    if args.apply:
        backup_dir.mkdir(parents=True, exist_ok=True)
    backup_manifest: list[dict[str, str]] = []
    report_items: list[dict[str, Any]] = []

    metadata_paths = sorted(metadata_dir.glob("*.json"))
    pending: list[tuple[str, Path, dict[str, Any]]] = []
    reviewed_count = 0
    for metadata_path in metadata_paths:
        metadata = read_json(metadata_path)
        if is_reviewed(metadata):
            reviewed_count += 1
            continue
        pending.append((metadata_path.stem, metadata_path, metadata))

    model = YOLO(str(model_path))
    totals = {name: 0 for name in CLASS_NAMES.values()}
    updated = 0
    no_detection = 0
    missing = 0
    errors = 0

    for sequence, (sample_id, metadata_path, metadata) in enumerate(pending, start=1):
        image_path = images_dir / f"{sample_id}.png"
        label_path = labels_dir / f"{sample_id}.txt"
        if not image_path.exists():
            missing += 1
            report_items.append({"sample_id": sample_id, "status": "missing_image", "image_path": str(image_path)})
            continue

        try:
            boxes = predict_boxes(
                model,
                image_path,
                imgsz=args.imgsz,
                conf=args.conf,
                iou=args.iou,
                max_det=args.max_det,
            )
        except Exception as exc:
            errors += 1
            report_items.append({"sample_id": sample_id, "status": "prediction_error", "error": str(exc)})
            continue

        class_counts = count_classes(boxes)
        for class_name, value in class_counts.items():
            totals[class_name] = totals.get(class_name, 0) + value

        if not boxes and not args.replace_empty:
            no_detection += 1
            if args.apply:
                update_metadata(
                    metadata_path,
                    metadata,
                    boxes=boxes,
                    backup_path=None,
                    model_path=model_path,
                    model_repo_id=args.model_repo_id,
                    imgsz=args.imgsz,
                    conf=args.conf,
                    iou=args.iou,
                    status="no_detections_preserved_existing_label",
                )
            report_items.append(
                {
                    "sample_id": sample_id,
                    "status": "no_detections_preserved_existing_label",
                    "detections_total": 0,
                    "classes": class_counts,
                }
            )
            continue

        backup_path: Path | None = None
        if args.apply:
            backup_path = make_backup(label_path, backup_dir, sample_id=sample_id, sequence=sequence, manifest=backup_manifest)
            labels_dir.mkdir(parents=True, exist_ok=True)
            label_text = "\n".join(box.line for box in boxes)
            label_path.write_text((label_text + "\n") if label_text else "", encoding="utf-8")
            update_metadata(
                metadata_path,
                metadata,
                boxes=boxes,
                backup_path=backup_path,
                model_path=model_path,
                model_repo_id=args.model_repo_id,
                imgsz=args.imgsz,
                conf=args.conf,
                iou=args.iou,
                status="auto_labeled_pending",
            )
        updated += 1
        report_items.append(
            {
                "sample_id": sample_id,
                "status": "auto_labeled_pending" if args.apply else "would_auto_label_pending",
                "detections_total": len(boxes),
                "classes": class_counts,
                "label_path": str(label_path),
                "backup_label_path": str(backup_path) if backup_path is not None else "",
            }
        )

    if args.apply and backup_manifest:
        backup_manifest_path = backup_dir / "backup_manifest.json"
        write_json(backup_manifest_path, {"items": backup_manifest})

    report = {
        "schema_version": "problem_detector_multiclass_auto_label_report_v1",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "apply": bool(args.apply),
        "dataset_root": str(root),
        "model_path": str(model_path),
        "model_repo_id": args.model_repo_id,
        "imgsz": args.imgsz,
        "conf": args.conf,
        "iou": args.iou,
        "metadata_total": len(metadata_paths),
        "reviewed_total": reviewed_count,
        "pending_total": len(pending),
        "updated_total": updated,
        "no_detection_total": no_detection,
        "missing_total": missing,
        "error_total": errors,
        "predicted_class_totals": totals,
        "backup_dir": str(backup_dir) if args.apply else "",
        "items": report_items,
    }
    report_path = root / ("auto_label_report.json" if args.apply else "auto_label_report_dry_run.json")
    write_json(report_path, report)

    print(
        json.dumps(
            {
                "apply": bool(args.apply),
                "pending_total": len(pending),
                "updated_total": updated,
                "no_detection_total": no_detection,
                "missing_total": missing,
                "error_total": errors,
                "predicted_class_totals": totals,
                "report_path": str(report_path),
                "backup_dir": str(backup_dir) if args.apply else "",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
