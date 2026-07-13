from __future__ import annotations

import argparse
import json
import random
import shutil
from pathlib import Path
from typing import Any

from PIL import Image

from build_problem_detector_multiclass_lab import (
    CLASS_MAP,
    Box,
    _draw_preview,
    _infer_answer_block_box,
    _infer_problem_number_box,
    _to_yolo,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLDEN_ROOT = REPO_ROOT / ".cache" / "transcriptor_runs" / "datasets" / "pdf_problem_boxes_live"
DEFAULT_OUT_PARENT = REPO_ROOT / ".cache" / "transcriptor_runs" / "datasets"


GROUPS = {
    "aseuni": ("aseuni-semianual-geometria",),
    "nostradamus": ("academia-nostradamus-semestral-2022-i",),
    "julio_orihuela": ("areas-de-regiones-planas",),
}


def _safe_id(value: str, *, max_len: int = 120) -> str:
    import hashlib
    import re

    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._") or "sample"
    if len(clean) <= max_len:
        return clean
    digest = hashlib.sha1(clean.encode("utf-8")).hexdigest()[:10]
    return f"{clean[: max(8, max_len - 11)].rstrip('._-')}_{digest}"


def _load_record(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _iter_group_records(golden_root: Path, group: str) -> list[dict[str, Any]]:
    prefixes = GROUPS[group]
    rows: list[dict[str, Any]] = []
    for instance_dir in sorted(item for item in golden_root.iterdir() if item.is_dir()):
        name = instance_dir.name.lower()
        if not any(prefix in name for prefix in prefixes):
            continue
        records_dir = instance_dir / "records"
        if not records_dir.exists():
            continue
        for record_path in sorted(records_dir.glob("*.json"), key=lambda item: item.name.lower()):
            payload = _load_record(record_path)
            if not payload:
                continue
            image_rel = str(payload.get("image_rel") or "").strip()
            image_path = instance_dir / image_rel
            boxes = payload.get("boxes_px") if isinstance(payload.get("boxes_px"), list) else []
            if not image_path.exists() or not boxes:
                continue
            if not bool(payload.get("reviewed", False)):
                continue
            rows.append(
                {
                    "group": group,
                    "instance": instance_dir.name,
                    "record_path": str(record_path),
                    "payload": payload,
                    "image_path": image_path,
                }
            )
    return rows


def _problem_boxes(payload: dict[str, Any], width: int, height: int) -> list[Box]:
    boxes: list[Box] = []
    for raw in payload.get("boxes_px") or []:
        if not isinstance(raw, list) or len(raw) < 4:
            continue
        try:
            x1, y1, x2, y2 = [int(round(float(value))) for value in raw[:4]]
        except Exception:
            continue
        x1, x2 = sorted((max(0, min(width, x1)), max(0, min(width, x2))))
        y1, y2 = sorted((max(0, min(height, y1)), max(0, min(height, y2))))
        box = Box(0, x1, y1, x2, y2, "pdf_problem_boxes_live")
        if box.valid():
            boxes.append(box)
    return boxes


def _copy_and_label_sample(row: dict[str, Any], out_root: Path, sample_index: int) -> dict[str, Any]:
    payload = row["payload"]
    image_path = Path(row["image_path"])
    instance = str(row["instance"])
    record_id = str(payload.get("record_id") or image_path.stem)
    sample_id = _safe_id(f"{sample_index:04d}_{row['group']}__{instance}__{record_id}", max_len=130)

    images_dir = out_root / "images"
    labels_dir = out_root / "labels"
    previews_dir = out_root / "previews"
    metadata_dir = out_root / "metadata"
    for directory in (images_dir, labels_dir, previews_dir, metadata_dir):
        directory.mkdir(parents=True, exist_ok=True)

    target_image = images_dir / f"{sample_id}.png"
    with Image.open(image_path) as source:
        page = source.convert("RGB")
        width, height = page.size
        page.save(target_image)

    output_boxes: list[Box] = []
    inferred_rows: list[dict[str, Any]] = []
    for problem_index, problem in enumerate(_problem_boxes(payload, width, height), start=1):
        output_boxes.append(problem)
        number_box = _infer_problem_number_box(page, problem)
        answer_box = _infer_answer_block_box(page, problem)
        for inferred in (number_box, answer_box):
            if not inferred or not inferred.valid():
                continue
            output_boxes.append(inferred)
            inferred_rows.append(
                {
                    "problem_index": problem_index,
                    "class_id": inferred.cls,
                    "class_name": CLASS_MAP[inferred.cls],
                    "xyxy": [inferred.x1, inferred.y1, inferred.x2, inferred.y2],
                    "source": inferred.source,
                }
            )

    label_path = labels_dir / f"{sample_id}.txt"
    label_path.write_text(
        ("\n".join(_to_yolo(box, width, height) for box in output_boxes) + "\n") if output_boxes else "",
        encoding="utf-8",
    )
    preview_path = previews_dir / f"{sample_id}.png"
    _draw_preview(target_image, preview_path, output_boxes)
    metadata_path = metadata_dir / f"{sample_id}.json"
    metadata = {
        "schema_version": "problem_detector_multiclass_100_lab_sample_v1",
        "sample_id": sample_id,
        "group": row["group"],
        "instance": instance,
        "source_record_path": row["record_path"],
        "source_image_path": str(image_path),
        "source_pdf": str(payload.get("pdf_path") or ""),
        "page_number": int(payload.get("page_number") or 0),
        "record_id": record_id,
        "image_path": str(target_image),
        "label_path": str(label_path),
        "preview_path": str(preview_path),
        "image_size": {"width": width, "height": height},
        "class_map": {str(key): value for key, value in CLASS_MAP.items()},
        "problem_boxes": sum(1 for box in output_boxes if box.cls == 0),
        "number_boxes": sum(1 for box in output_boxes if box.cls == 1),
        "answer_blocks": sum(1 for box in output_boxes if box.cls == 2),
        "inferred_subboxes": inferred_rows,
        "review_status": "auto_labeled_by_codex_needs_visual_review",
        "policy": {
            "source_page_level": True,
            "original_pdf_problem_boxes_preserved": True,
            "subboxes_created_by_codex_visual_heuristics": True,
            "do_not_mix_with_original_golden_until_reviewed": True,
        },
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "sample_id": sample_id,
        "group": row["group"],
        "instance": instance,
        "page_number": metadata["page_number"],
        "problem_boxes": metadata["problem_boxes"],
        "number_boxes": metadata["number_boxes"],
        "answer_blocks": metadata["answer_blocks"],
        "image": str(target_image),
        "label": str(label_path),
        "preview": str(preview_path),
        "metadata": str(metadata_path),
    }


def _sample_balanced(rows_by_group: dict[str, list[dict[str, Any]]], total: int, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    groups = list(GROUPS)
    base = total // len(groups)
    remainder = total % len(groups)
    targets = {group: base + (1 if index < remainder else 0) for index, group in enumerate(groups)}
    selected: list[dict[str, Any]] = []
    shortage = 0
    for group in groups:
        rows = list(rows_by_group.get(group) or [])
        rng.shuffle(rows)
        take = min(targets[group], len(rows))
        selected.extend(rows[:take])
        shortage += max(0, targets[group] - take)
    if shortage:
        pool: list[dict[str, Any]] = []
        selected_keys = {str(item["record_path"]) for item in selected}
        for rows in rows_by_group.values():
            for row in rows:
                if str(row["record_path"]) not in selected_keys:
                    pool.append(row)
        rng.shuffle(pool)
        selected.extend(pool[:shortage])
    rng.shuffle(selected)
    return selected[:total]


def build_dataset(*, golden_root: Path, out_root: Path, total: int, seed: int) -> dict[str, Any]:
    golden_root = golden_root.expanduser().resolve()
    out_root = out_root.expanduser().resolve()
    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    rows_by_group = {group: _iter_group_records(golden_root, group) for group in GROUPS}
    selected = _sample_balanced(rows_by_group, total, seed)
    rows = [_copy_and_label_sample(row, out_root, index) for index, row in enumerate(selected, start=1)]
    manifest = {
        "schema_version": "problem_detector_multiclass_100_lab_manifest_v1",
        "root": str(out_root),
        "golden_root": str(golden_root),
        "seed": seed,
        "requested_total": total,
        "samples_total": len(rows),
        "class_map": {str(key): value for key, value in CLASS_MAP.items()},
        "available_by_group": {group: len(items) for group, items in rows_by_group.items()},
        "selected_by_group": {group: sum(1 for row in rows if row["group"] == group) for group in GROUPS},
        "problem_boxes_total": sum(row["problem_boxes"] for row in rows),
        "number_boxes_total": sum(row["number_boxes"] for row in rows),
        "answer_blocks_total": sum(row["answer_blocks"] for row in rows),
        "rows": rows,
        "policy": {
            "copy_only": True,
            "source_images_are_full_practice_pages": True,
            "subboxes_need_visual_review_before_model_training": True,
            "original_pdf_problem_boxes_live_unchanged": True,
        },
    }
    (out_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Construye 100 muestras multiclass desde paginas completas revisadas.")
    parser.add_argument("--golden-root", default=str(DEFAULT_GOLDEN_ROOT))
    parser.add_argument("--out-root", default="")
    parser.add_argument("--total", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260624)
    args = parser.parse_args()
    out_root = Path(args.out_root) if args.out_root else DEFAULT_OUT_PARENT / "problem_detector_multiclass_100_lab"
    manifest = build_dataset(
        golden_root=Path(args.golden_root),
        out_root=out_root,
        total=max(1, int(args.total)),
        seed=int(args.seed),
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
