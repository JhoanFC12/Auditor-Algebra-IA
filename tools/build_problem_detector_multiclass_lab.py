from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


CLASS_MAP = {
    0: "problem",
    1: "problem_number",
    2: "answer_block",
}


@dataclass(frozen=True)
class Box:
    cls: int
    x1: int
    y1: int
    x2: int
    y2: int
    source: str

    def valid(self) -> bool:
        return self.x2 > self.x1 and self.y2 > self.y1


def _read_yolo_boxes(label_path: Path, width: int, height: int) -> list[Box]:
    boxes: list[Box] = []
    if not label_path.exists():
        return boxes
    for line in label_path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            cls = int(float(parts[0]))
            cx, cy, bw, bh = [float(value) for value in parts[1:5]]
        except Exception:
            continue
        x1 = int(round((cx - bw / 2.0) * width))
        y1 = int(round((cy - bh / 2.0) * height))
        x2 = int(round((cx + bw / 2.0) * width))
        y2 = int(round((cy + bh / 2.0) * height))
        box = Box(
            cls=cls,
            x1=max(0, min(width, x1)),
            y1=max(0, min(height, y1)),
            x2=max(0, min(width, x2)),
            y2=max(0, min(height, y2)),
            source="original_label",
        )
        if box.valid():
            boxes.append(box)
    return boxes


def _to_yolo(box: Box, width: int, height: int) -> str:
    cx = ((box.x1 + box.x2) / 2.0) / max(1, width)
    cy = ((box.y1 + box.y2) / 2.0) / max(1, height)
    bw = (box.x2 - box.x1) / max(1, width)
    bh = (box.y2 - box.y1) / max(1, height)
    return f"{box.cls} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"


def _dark_bbox(image: Image.Image, *, min_pixels: int = 12) -> tuple[int, int, int, int] | None:
    try:
        import numpy as np

        gray = image.convert("L")
        arr = np.asarray(gray)
        mask = arr < 178
        ys, xs = np.where(mask)
        if len(xs) < min_pixels:
            return None
        return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    except Exception:
        return None


def _infer_problem_number_box(image: Image.Image, problem: Box) -> Box | None:
    try:
        import numpy as np
    except Exception:
        return None
    width = problem.x2 - problem.x1
    height = problem.y2 - problem.y1
    if width <= 0 or height <= 0:
        return None
    inner = max(2, int(min(width, height) * 0.006))
    zone = image.crop(
        (
            problem.x1 + inner,
            problem.y1 + inner,
            problem.x1 + max(inner + 1, int(width * 0.72)),
            problem.y1 + max(inner + 1, int(height * 0.24)),
        )
    ).convert("L")
    arr = np.asarray(zone)
    mask = arr < 185
    active_rows = [int(y) for y in np.flatnonzero(mask.sum(axis=1) >= max(4, int(zone.width * 0.008)))]
    bands: list[tuple[int, int]] = []
    for y in active_rows:
        if not bands or y - bands[-1][1] > 3:
            bands.append((y, y))
        else:
            bands[-1] = (bands[-1][0], y)
    for y1, y2 in bands:
        if y1 > zone.height * 0.68:
            break
        band_height = y2 - y1 + 1
        if band_height > max(28, int(height * 0.10)):
            continue
        band = mask[y1 : y2 + 1, :]
        cols = np.flatnonzero(band.sum(axis=0) > 0)
        if len(cols) < 8:
            continue
        x1 = int(cols.min())
        x2 = int(cols.max()) + 1
        span = x2 - x1
        if span > zone.width * 0.92:
            continue
        if x1 > zone.width * 0.32:
            continue
        if span < max(36, int(width * 0.12)):
            continue

        # Header-like labels such as "PROBLEMA N° 275" are broad but not a
        # full sentence. Keep them as the number marker.
        if span <= zone.width * 0.58:
            pad_x = max(3, int(width * 0.015))
            pad_y = max(2, int(height * 0.012))
            return Box(
                1,
                problem.x1 + inner + max(0, x1 - pad_x),
                problem.y1 + inner + max(0, y1 - pad_y),
                problem.x1 + inner + min(zone.width, x2 + pad_x),
                problem.y1 + inner + min(zone.height, y2 + 1 + pad_y),
                "heuristic_problem_number_header",
            )

    fixed_w = min(width - inner, max(48, min(int(width * 0.20), 150)))
    fixed_h = min(height - inner, max(20, min(int(height * 0.11), 54)))
    if fixed_w <= 0 or fixed_h <= 0:
        return None
    return Box(
        1,
        problem.x1 + inner,
        problem.y1 + inner,
        problem.x1 + inner + fixed_w,
        problem.y1 + inner + fixed_h,
        "heuristic_problem_number_top_left",
    )


def _infer_answer_block_box(image: Image.Image, problem: Box) -> Box | None:
    try:
        import numpy as np
    except Exception:
        return None
    width = problem.x2 - problem.x1
    height = problem.y2 - problem.y1
    if width <= 0 or height <= 0:
        return None
    zone_top = int(height * 0.42)
    zone = image.crop((problem.x1, problem.y1 + zone_top, problem.x2, problem.y2)).convert("L")
    arr = np.asarray(zone)
    mask = arr < 178
    row_threshold = max(6, int(width * 0.012))
    active_rows = [int(y) for y in np.flatnonzero(mask.sum(axis=1) >= row_threshold)]
    if not active_rows:
        return None

    bands: list[tuple[int, int]] = []
    for y in active_rows:
        if not bands or y - bands[-1][1] > 4:
            bands.append((y, y))
        else:
            bands[-1] = (bands[-1][0], y)
    candidate_bands: list[tuple[int, int]] = []
    for y1, y2 in bands:
        band_height = y2 - y1 + 1
        if band_height > max(30, int(height * 0.16)):
            continue
        band = mask[y1 : y2 + 1, :]
        col_threshold = max(1, int(band_height * 0.08))
        cols = np.flatnonzero(band.sum(axis=0) >= col_threshold)
        if len(cols) < 8:
            continue
        segments: list[tuple[int, int]] = []
        max_gap = max(8, int(width * 0.035))
        for x in [int(item) for item in cols]:
            if not segments or x - segments[-1][1] > max_gap:
                segments.append((x, x))
            else:
                segments[-1] = (segments[-1][0], x)
        compact_segments = [
            (x1, x2)
            for x1, x2 in segments
            if x2 - x1 + 1 >= max(8, int(width * 0.018)) and x2 - x1 + 1 <= width * 0.44
        ]
        span = int(cols.max() - cols.min() + 1)
        row_center = (y1 + y2) / 2.0
        starts_left = bool(compact_segments and compact_segments[0][0] <= width * 0.26)
        horizontal_options = len(compact_segments) >= 2 and span >= width * 0.18
        vertical_options = starts_left and span <= width * 0.84 and len(compact_segments) <= 5
        if row_center >= zone.height * 0.18 and (horizontal_options or vertical_options):
            candidate_bands.append((y1, y2))
    if not candidate_bands:
        return None
    y1 = min(item[0] for item in candidate_bands)
    y2 = max(item[1] for item in candidate_bands)
    selected = mask[y1 : y2 + 1, :]
    ys, xs = np.where(selected)
    if len(xs) < 20:
        return None
    pad_x = max(4, int(width * 0.018))
    pad_y = max(3, int(height * 0.015))
    x1 = problem.x1 + max(0, int(xs.min()) - pad_x)
    x2 = problem.x1 + min(width, int(xs.max()) + 1 + pad_x)
    abs_y1 = problem.y1 + zone_top + max(0, y1 + int(ys.min()) - pad_y)
    abs_y2 = problem.y1 + zone_top + min(zone.height, y1 + int(ys.max()) + 1 + pad_y)
    if x2 - x1 < max(20, int(width * 0.12)) or abs_y2 - abs_y1 < max(10, int(height * 0.04)):
        return None
    return Box(2, x1, abs_y1, x2, abs_y2, "heuristic_lower_answer_region")


def _draw_preview(image_path: Path, preview_path: Path, boxes: list[Box]) -> None:
    colors = {
        0: (235, 55, 55),
        1: (255, 174, 0),
        2: (35, 132, 255),
    }
    with Image.open(image_path) as img:
        preview = img.convert("RGB")
    draw = ImageDraw.Draw(preview)
    for index, box in enumerate(boxes, start=1):
        color = colors.get(box.cls, (90, 90, 90))
        for offset in range(3):
            draw.rectangle((box.x1 - offset, box.y1 - offset, box.x2 + offset, box.y2 + offset), outline=color)
        draw.text((box.x1 + 4, max(0, box.y1 + 4)), f"{index}:{CLASS_MAP.get(box.cls, box.cls)}", fill=color)
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(preview_path)


def build_multiclass_lab(root: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    rows: list[dict[str, Any]] = []
    for dataset in sorted(item for item in root.iterdir() if item.is_dir()):
        images_dir = dataset / "images"
        labels_dir = dataset / "labels"
        if not images_dir.exists() or not labels_dir.exists():
            continue
        labels_out = dataset / "labels_multiclass"
        metadata_out = dataset / "metadata_multiclass"
        previews_out = dataset / "previews_multiclass"
        labels_out.mkdir(parents=True, exist_ok=True)
        metadata_out.mkdir(parents=True, exist_ok=True)
        previews_out.mkdir(parents=True, exist_ok=True)
        for image_path in sorted(images_dir.glob("*.png"), key=lambda path: path.name.lower()):
            with Image.open(image_path) as img:
                width, height = img.size
                page = img.convert("RGB")
            problem_boxes = _read_yolo_boxes(labels_dir / f"{image_path.stem}.txt", width, height)
            output_boxes: list[Box] = []
            metadata_boxes: list[dict[str, Any]] = []
            for problem in problem_boxes:
                if problem.cls != 0:
                    continue
                output_boxes.append(problem)
                for inferred in (_infer_problem_number_box(page, problem), _infer_answer_block_box(page, problem)):
                    if inferred and inferred.valid():
                        output_boxes.append(inferred)
                        metadata_boxes.append(
                            {
                                "class_id": inferred.cls,
                                "class_name": CLASS_MAP[inferred.cls],
                                "xyxy": [inferred.x1, inferred.y1, inferred.x2, inferred.y2],
                                "source": inferred.source,
                                "review_status": "needs_review",
                            }
                        )
            label_path = labels_out / f"{image_path.stem}.txt"
            label_path.write_text(
                ("\n".join(_to_yolo(box, width, height) for box in output_boxes) + "\n") if output_boxes else "",
                encoding="utf-8",
            )
            preview_path = previews_out / f"{image_path.stem}.png"
            _draw_preview(image_path, preview_path, output_boxes)
            metadata_path = metadata_out / f"{image_path.stem}.json"
            metadata = {
                "schema_version": "problem_detector_multiclass_lab_v1",
                "source_dataset": dataset.name,
                "image_path": str(image_path),
                "label_path": str(label_path),
                "preview_path": str(preview_path),
                "image_size": {"width": width, "height": height},
                "class_map": {str(key): value for key, value in CLASS_MAP.items()},
                "problem_boxes": len(problem_boxes),
                "inferred_boxes": metadata_boxes,
                "policy": {
                    "source": "copy_of_problem_detector_corrections",
                    "original_labels_preserved": True,
                    "subboxes_are_draft": True,
                    "requires_human_review_before_training": True,
                },
            }
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            rows.append(
                {
                    "dataset": dataset.name,
                    "image": image_path.name,
                    "problems": len(problem_boxes),
                    "number_boxes": sum(1 for box in output_boxes if box.cls == 1),
                    "answer_blocks": sum(1 for box in output_boxes if box.cls == 2),
                    "label": str(label_path),
                    "preview": str(preview_path),
                }
            )
    manifest = {
        "schema_version": "problem_detector_multiclass_lab_manifest_v1",
        "root": str(root),
        "class_map": {str(key): value for key, value in CLASS_MAP.items()},
        "samples_total": len(rows),
        "problem_boxes_total": sum(row["problems"] for row in rows),
        "number_boxes_total": sum(row["number_boxes"] for row in rows),
        "answer_blocks_total": sum(row["answer_blocks"] for row in rows),
        "rows": rows,
        "policy": {
            "source": "copied_problem_detector_corrections_only",
            "do_not_train_without_human_review": True,
        },
    }
    (root / "manifest_multiclass.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera labels multiclass de laboratorio sobre copia de problem_detector_corrections.")
    parser.add_argument("--root", required=True, help="Raiz de la copia problem_detector_corrections_lab_*.")
    args = parser.parse_args()
    manifest = build_multiclass_lab(Path(args.root))
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
