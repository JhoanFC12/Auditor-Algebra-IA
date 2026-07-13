from __future__ import annotations

import argparse
import json
import random
import shutil
import time
from pathlib import Path
from typing import Any

from build_problem_detector_multiclass_100_lab import (
    DEFAULT_GOLDEN_ROOT,
    DEFAULT_OUT_PARENT,
    _copy_and_label_sample,
    _load_record,
    _safe_id,
)


def _match_any(value: str, patterns: list[str]) -> bool:
    haystack = value.lower()
    return any(pattern.lower() in haystack for pattern in patterns if pattern)


def _iter_author_records(golden_root: Path, *, include: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for instance_dir in sorted(item for item in golden_root.iterdir() if item.is_dir()):
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
            if not bool(payload.get("reviewed", False)):
                continue
            source_text = " ".join(
                [
                    instance_dir.name,
                    str(payload.get("pdf_path") or ""),
                    str(payload.get("record_id") or ""),
                ]
            )
            if not _match_any(source_text, include):
                continue
            if not boxes:
                continue
            try:
                image_exists = image_path.exists()
            except OSError:
                image_exists = False
            if not image_exists:
                continue
            rows.append(
                {
                    "group": "julio_orihuela",
                    "instance": instance_dir.name,
                    "record_path": str(record_path),
                    "payload": payload,
                    "image_path": image_path,
                }
            )
    return rows


def _unique_by_record(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        key = str(row["record_path"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def build_author_dataset(
    *,
    golden_root: Path,
    out_root: Path,
    include: list[str],
    total: int,
    seed: int,
    clean: bool,
) -> dict[str, Any]:
    golden_root = golden_root.expanduser().resolve()
    out_root = out_root.expanduser().resolve()
    if clean and out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    candidates = _unique_by_record(_iter_author_records(golden_root, include=include))
    rng = random.Random(seed)
    selected = list(candidates)
    rng.shuffle(selected)
    if total > 0:
        selected = selected[:total]

    rows = [_copy_and_label_sample(row, out_root, index) for index, row in enumerate(selected, start=1)]
    selected_by_instance: dict[str, int] = {}
    available_by_instance: dict[str, int] = {}
    for row in candidates:
        instance = str(row["instance"])
        available_by_instance[instance] = available_by_instance.get(instance, 0) + 1
    for row in rows:
        instance = str(row["instance"])
        selected_by_instance[instance] = selected_by_instance.get(instance, 0) + 1

    manifest = {
        "schema_version": "problem_detector_multiclass_author_lab_manifest_v1",
        "root": str(out_root),
        "golden_root": str(golden_root),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "author_focus": "julio_orihuela",
        "include_patterns": include,
        "seed": seed,
        "requested_total": total if total > 0 else "all",
        "available_total": len(candidates),
        "samples_total": len(rows),
        "selected_by_group": {"julio_orihuela": len(rows)},
        "available_by_instance": dict(sorted(available_by_instance.items())),
        "selected_by_instance": dict(sorted(selected_by_instance.items())),
        "problem_boxes_total": sum(row["problem_boxes"] for row in rows),
        "number_boxes_total": sum(row["number_boxes"] for row in rows),
        "answer_blocks_total": sum(row["answer_blocks"] for row in rows),
        "rows": rows,
        "policy": {
            "copy_only": True,
            "source_images_are_full_practice_pages": True,
            "focused_author_dataset": True,
            "subboxes_need_visual_review_before_model_training": True,
            "original_pdf_problem_boxes_live_unchanged": True,
        },
    }
    (out_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Construye un dataset multiclass enfocado por autor/libro.")
    parser.add_argument("--golden-root", default=str(DEFAULT_GOLDEN_ROOT))
    parser.add_argument("--out-root", default="")
    parser.add_argument("--include", action="append", default=["Julio Orihuela"], help="Texto que debe aparecer en instancia/pdf/record.")
    parser.add_argument("--total", type=int, default=0, help="0 usa todas las paginas disponibles.")
    parser.add_argument("--seed", type=int, default=20260625)
    parser.add_argument("--no-clean", action="store_true", help="No borra el out-root antes de construir.")
    args = parser.parse_args()
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    out_root = (
        Path(args.out_root)
        if args.out_root
        else DEFAULT_OUT_PARENT / f"problem_detector_multiclass_100_lab_julio_orihuela_{timestamp}"
    )
    manifest = build_author_dataset(
        golden_root=Path(args.golden_root),
        out_root=out_root,
        include=[str(item) for item in args.include if str(item).strip()],
        total=max(0, int(args.total or 0)),
        seed=int(args.seed),
        clean=not bool(args.no_clean),
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
