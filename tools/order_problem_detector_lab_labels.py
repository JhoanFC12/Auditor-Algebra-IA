from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image


CLASS_ORDER = {0: 0, 1: 1, 2: 2}


@dataclass
class YoloBox:
    cls: int
    cx: float
    cy: float
    w: float
    h: float
    line: str
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


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_dataset_root() -> Path:
    return repo_root() / ".cache" / "transcriptor_runs" / "datasets" / "problem_detector_multiclass_100_lab_20260624"


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def is_reviewed(metadata_path: Path) -> bool:
    metadata = read_json(metadata_path)
    label_review = metadata.get("label_review")
    return isinstance(label_review, dict) and bool(label_review.get("reviewed_at"))


def auto_labeled_pending_ids(root: Path) -> list[str]:
    report = read_json(root / "auto_label_report.json")
    items = report.get("items")
    if not isinstance(items, list):
        return []
    ids: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        sample_id = str(item.get("sample_id") or "").strip()
        status = str(item.get("status") or "").strip()
        if sample_id and status == "auto_labeled_pending":
            ids.append(sample_id)
    return sorted(dict.fromkeys(ids))


def image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as img:
        return int(img.width), int(img.height)


def parse_label(label_path: Path, image_w: int, image_h: int) -> list[YoloBox]:
    boxes: list[YoloBox] = []
    if not label_path.exists():
        return boxes
    for index, raw in enumerate(label_path.read_text(encoding="utf-8").splitlines()):
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 5:
            continue
        try:
            boxes.append(
                YoloBox(
                    cls=int(float(parts[0])),
                    cx=float(parts[1]),
                    cy=float(parts[2]),
                    w=float(parts[3]),
                    h=float(parts[4]),
                    line=line,
                    index=index,
                    image_w=image_w,
                    image_h=image_h,
                )
            )
        except Exception:
            continue
    return boxes


def has_two_columns(problems: list[YoloBox], image_w: int) -> bool:
    if len(problems) < 3:
        return False
    narrow = [box for box in problems if (box.x2 - box.x1) < image_w * 0.82]
    if len(narrow) < 2:
        return False
    return any(box.center_x < image_w * 0.45 for box in narrow) and any(box.center_x > image_w * 0.55 for box in narrow)


def reading_column(box: YoloBox, image_w: int, two_columns: bool) -> int:
    if not two_columns:
        return 0
    return 0 if box.center_x < image_w * 0.5 else 1


def problem_sort_key(box: YoloBox, image_w: int, two_columns: bool) -> tuple[float, float, float, int]:
    return (reading_column(box, image_w, two_columns), round(box.y1 / 12.0), box.x1, box.index)


def overlap_area(a: YoloBox, b: YoloBox) -> float:
    x1 = max(a.x1, b.x1)
    y1 = max(a.y1, b.y1)
    x2 = min(a.x2, b.x2)
    y2 = min(a.y2, b.y2)
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def center_inside(parent: YoloBox, child: YoloBox) -> bool:
    return parent.x1 <= child.center_x <= parent.x2 and parent.y1 <= child.center_y <= parent.y2


def owner_problem(subbox: YoloBox, problems: list[YoloBox]) -> YoloBox | None:
    best: YoloBox | None = None
    best_score = 0.0
    for problem in problems:
        overlap_ratio = overlap_area(problem, subbox) / max(1.0, subbox.area)
        score = overlap_ratio + (1.0 if center_inside(problem, subbox) else 0.0)
        if score > best_score:
            best = problem
            best_score = score
    return best if best_score >= 0.25 else None


def subbox_sort_key(box: YoloBox) -> tuple[int, float, float, int]:
    return (CLASS_ORDER.get(box.cls, 9), round(box.y1 / 12.0), box.x1, box.index)


def orphan_sort_key(box: YoloBox, image_w: int, two_columns: bool) -> tuple[float, float, float, int, int]:
    return (reading_column(box, image_w, two_columns), round(box.y1 / 12.0), box.x1, CLASS_ORDER.get(box.cls, 9), box.index)


def order_boxes(boxes: list[YoloBox], image_w: int) -> list[YoloBox]:
    problems = [box for box in boxes if box.cls == 0]
    if not problems:
        return sorted(boxes, key=lambda box: orphan_sort_key(box, image_w, False))
    two_columns = has_two_columns(problems, image_w)
    ordered_problems = sorted(problems, key=lambda box: problem_sort_key(box, image_w, two_columns))
    children: dict[int, list[YoloBox]] = {id(problem): [] for problem in ordered_problems}
    orphans: list[YoloBox] = []
    for subbox in [box for box in boxes if box.cls != 0]:
        owner = owner_problem(subbox, ordered_problems)
        if owner is None:
            orphans.append(subbox)
        else:
            children[id(owner)].append(subbox)

    ordered: list[YoloBox] = []
    for problem in ordered_problems:
        ordered.append(problem)
        ordered.extend(sorted(children[id(problem)], key=subbox_sort_key))
    ordered.extend(sorted(orphans, key=lambda box: orphan_sort_key(box, image_w, two_columns)))
    return ordered


def update_metadata(metadata_path: Path, *, changed: bool, backup_path: Path | None) -> None:
    metadata = read_json(metadata_path)
    label_review = metadata.get("label_review")
    if not isinstance(label_review, dict):
        label_review = {}
        metadata["label_review"] = label_review
    label_review["reading_order_checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    label_review["reading_order_changed"] = bool(changed)
    if backup_path is not None:
        label_review["reading_order_backup"] = str(backup_path)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ordena labels YOLO revisados en orden de lectura.")
    parser.add_argument("--dataset-root", type=Path, default=default_dataset_root())
    parser.add_argument(
        "--scope",
        choices=("reviewed", "auto-labeled", "all"),
        default="reviewed",
        help="Muestras a ordenar: revisadas humanas, auto-etiquetadas pendientes o todas.",
    )
    parser.add_argument("--apply", action="store_true", help="Escribe labels ordenados. Sin esto solo reporta.")
    args = parser.parse_args()

    root = args.dataset_root.resolve()
    images_dir = root / "images"
    labels_dir = root / "labels"
    metadata_dir = root / "metadata"
    backup_dir = root / "label_backups" / f"reading_order_{time.strftime('%Y%m%d_%H%M%S')}"
    report: list[dict[str, Any]] = []
    backup_manifest: list[dict[str, str]] = []

    if args.scope == "reviewed":
        selected_ids = sorted(path.stem for path in metadata_dir.glob("*.json") if is_reviewed(path))
    elif args.scope == "auto-labeled":
        selected_ids = auto_labeled_pending_ids(root)
    else:
        selected_ids = sorted(path.stem for path in metadata_dir.glob("*.json"))

    for review_index, sample_id in enumerate(selected_ids, start=1):
        image_path = images_dir / f"{sample_id}.png"
        label_path = labels_dir / f"{sample_id}.txt"
        metadata_path = metadata_dir / f"{sample_id}.json"
        if not image_path.exists() or not label_path.exists():
            report.append({"sample_id": sample_id, "status": "missing_image_or_label"})
            continue

        width, height = image_size(image_path)
        boxes = parse_label(label_path, width, height)
        ordered = order_boxes(boxes, width)
        before = [box.line for box in boxes]
        after = [box.line for box in ordered]
        changed = before != after
        backup_path: Path | None = None
        if args.apply:
            if changed:
                backup_dir.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha1(sample_id.encode("utf-8")).hexdigest()[:12]
                backup_path = backup_dir / f"{review_index:04d}_{digest}.txt"
                shutil.copy2(label_path, backup_path)
                backup_manifest.append(
                    {
                        "sample_id": sample_id,
                        "original_label": str(label_path),
                        "backup_label": str(backup_path),
                    }
                )
                label_path.write_text(("\n".join(after) + "\n") if after else "", encoding="utf-8")
            update_metadata(metadata_path, changed=changed, backup_path=backup_path)
        report.append(
            {
                "sample_id": sample_id,
                "status": "changed" if changed else "already_ordered",
                "boxes": len(boxes),
                "problem": sum(1 for box in boxes if box.cls == 0),
                "problem_number": sum(1 for box in boxes if box.cls == 1),
                "answer_block": sum(1 for box in boxes if box.cls == 2),
            }
        )

    report_path = root / "reading_order_report.json"
    report_payload = {
        "dataset_root": str(root),
        "apply": bool(args.apply),
        "scope": args.scope,
        "selected_total": len(selected_ids),
        "reviewed_total": len(selected_ids) if args.scope == "reviewed" else sum(
            1 for sample_id in selected_ids if is_reviewed(metadata_dir / f"{sample_id}.json")
        ),
        "changed_total": sum(1 for row in report if row.get("status") == "changed"),
        "already_ordered_total": sum(1 for row in report if row.get("status") == "already_ordered"),
        "backup_dir": str(backup_dir) if args.apply else "",
        "rows": report,
    }
    report_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.apply and backup_manifest:
        (backup_dir / "backup_manifest.json").write_text(
            json.dumps(backup_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps(report_payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
