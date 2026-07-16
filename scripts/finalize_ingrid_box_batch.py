#!/usr/bin/env python3
"""Finalize one visually audited Ingrid box-review batch.

This script is deliberately limited to the editable review workspace. It renders
after-overlays, writes per-page review records, updates the batch manifests, and
verifies that the immutable source still matches the frozen 381-page baseline.
It does not run OCR, training, model promotion, or database operations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


CLASS_NAMES = {0: "problem", 1: "problem_number", 2: "answer_block"}
CLASS_COLORS = {0: (255, 45, 55), 1: (20, 145, 255), 2: (24, 194, 93)}
MODEL_SHA256 = "b62e280a993c092cbec194a72cc7512c3f52a8bed6846ea82e4274a20362043c"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--batch", type=int, required=True)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def read_label_lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def parse_yolo(line: str) -> list[float | int]:
    fields = line.split()
    return [int(fields[0]), *(float(value) for value in fields[1:])]


def format_yolo(values: list[float | int]) -> str:
    return f"{int(values[0])} " + " ".join(f"{float(value):.6f}" for value in values[1:])


def canonical_yolo(line: str) -> str:
    """Normalize labels before comparison while preserving source precision on disk."""
    return format_yolo(parse_yolo(line))


def xyxy(values: list[float | int], width: int, height: int) -> list[int]:
    _, cx, cy, box_width, box_height = values
    return [
        round((float(cx) - float(box_width) / 2) * width),
        round((float(cy) - float(box_height) / 2) * height),
        round((float(cx) + float(box_width) / 2) * width),
        round((float(cy) + float(box_height) / 2) * height),
    ]


def apply_declared_operations(
    baseline_lines: list[str], operations: list[dict[str, Any]]
) -> list[str]:
    result = list(baseline_lines)
    for operation in operations:
        index = int(operation["line_index"]) - 1
        kind = operation["operation"]
        before = operation.get("before_yolo")
        after = operation.get("after_yolo")
        if kind == "resize":
            if index >= len(result) or canonical_yolo(result[index]) != format_yolo(before):
                raise ValueError(f"Resize baseline mismatch at line {index + 1}")
            result[index] = format_yolo(after)
        elif kind == "add":
            result.insert(index, format_yolo(after))
        elif kind == "remove":
            if index >= len(result) or canonical_yolo(result[index]) != format_yolo(before):
                raise ValueError(f"Removal baseline mismatch at line {index + 1}")
            result.pop(index)
        else:
            raise ValueError(f"Unsupported operation: {kind}")
    return result


def render_overlay(image_path: Path, label_path: Path, destination: Path) -> tuple[int, int]:
    image = Image.open(image_path).convert("RGB")
    width, height = image.size
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    stroke = max(3, round(min(width, height) / 600))
    for line_index, line in enumerate(read_label_lines(label_path), start=1):
        values = parse_yolo(line)
        class_id = int(values[0])
        color = CLASS_COLORS[class_id]
        x1, y1, x2, y2 = xyxy(values, width, height)
        draw.rectangle((x1, y1, x2, y2), outline=color, width=stroke)
        caption = f"{line_index}:{CLASS_NAMES[class_id]}"
        caption_box = draw.textbbox((x1, y1), caption, font=font)
        draw.rectangle(caption_box, fill=color)
        draw.text((x1, y1), caption, fill=(255, 255, 255), font=font)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="JPEG", quality=92, optimize=True)
    return width, height


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def operation_record(
    operation: dict[str, Any], width: int, height: int
) -> dict[str, Any]:
    class_id = int(operation["class_id"])
    before = operation.get("before_yolo")
    after = operation.get("after_yolo")
    return {
        "operation": operation["operation"],
        "class_id": class_id,
        "class": CLASS_NAMES[class_id],
        "line_index": int(operation["line_index"]),
        "before": None
        if before is None
        else {"xyxy_px": xyxy(before, width, height), "yolo": before},
        "after": None
        if after is None
        else {"xyxy_px": xyxy(after, width, height), "yolo": after},
        "reason": operation["reason"],
    }


def main() -> None:
    args = parse_args()
    workspace = args.workspace.resolve()
    source = args.source.resolve()
    batch_number = args.batch
    batch_name = f"batch_{batch_number:02d}"
    batch_dir = workspace / "reviews" / "batches_50"
    batch_path = batch_dir / f"{batch_name}.json"
    jsonl_path = batch_dir / f"{batch_name}.jsonl"
    operations_path = batch_dir / f"{batch_name}_operations.json"
    batch = read_json(batch_path)
    declared = read_json(operations_path) if operations_path.exists() else {}
    operations_by_sample = declared.get("operations_by_sample", {})
    notes_by_sample = declared.get("sample_notes", {})
    reviewed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    status_counts: Counter[str] = Counter()
    changed_samples: list[dict[str, Any]] = []
    operation_counts: Counter[str] = Counter()

    for row in batch["rows"]:
        split = row["split"]
        sample_id = row["sample_id"]
        order = int(row["order"])
        image_path = workspace / "images" / split / f"{sample_id}.png"
        label_path = workspace / "labels" / split / f"{sample_id}.txt"
        baseline_path = (
            workspace / "remaining_review_baseline_labels" / split / f"{sample_id}.txt"
        )
        before_overlay = (
            workspace
            / "remaining_overlays_before"
            / f"{batch_name}_jpg"
            / split
            / f"{order:02d}__{sample_id}.jpg"
        )
        after_overlay = (
            workspace
            / "remaining_overlays_after"
            / f"{batch_name}_jpg"
            / split
            / f"{order:02d}__{sample_id}.jpg"
        )
        review_path = workspace / "reviews" / split / f"{sample_id}.json"

        baseline_lines = read_label_lines(baseline_path)
        corrected_lines = read_label_lines(label_path)
        baseline_class_counts = Counter(
            CLASS_NAMES[int(parse_yolo(line)[0])] for line in baseline_lines
        )
        corrected_class_counts = Counter(
            CLASS_NAMES[int(parse_yolo(line)[0])] for line in corrected_lines
        )
        operations = operations_by_sample.get(sample_id, [])
        reconstructed = apply_declared_operations(baseline_lines, operations)
        if [canonical_yolo(line) for line in reconstructed] != [
            canonical_yolo(line) for line in corrected_lines
        ]:
            raise ValueError(
                f"Undeclared or inconsistent label difference for {split}/{sample_id}"
            )
        changed = corrected_lines != baseline_lines
        if changed != bool(operations):
            raise ValueError(f"Change/operation mismatch for {split}/{sample_id}")

        width, height = render_overlay(image_path, label_path, after_overlay)
        status = "agent_corrected_pending_human" if changed else "accepted_unchanged"
        status_counts[status] += 1
        notes = notes_by_sample.get(sample_id, {})
        recorded_operations = [operation_record(op, width, height) for op in operations]
        for operation in operations:
            operation_counts[operation["operation"]] += 1
        if changed:
            changed_samples.append(
                {
                    "order": order,
                    "split": split,
                    "sample_id": sample_id,
                    "operations": len(operations),
                }
            )

        if changed:
            reasoning = notes.get(
                "reasoning_summary",
                "Visual dimension audit corrected only the declared target boundaries.",
            )
        else:
            reasoning = (
                "Visual dimension audit found complete target coverage for every existing "
                "problem, numbering, and alternatives box. Split column/page fragments were "
                "retained; no dimensional change was justified."
            )

        review = {
            "schema_version": "ingrid_training_box_review_v1",
            "agent_id": "ingrid_daubechies_v1",
            "detector_model_id": "pdf_problem_detector_multiclass_v7_401",
            "detector_model_sha256": MODEL_SHA256,
            "review_batch_id": batch["batch_id"],
            "review_order": order,
            "split": split,
            "sample_id": sample_id,
            "page_number": row.get("page_number"),
            "book_code": row.get("book_code"),
            "instance_type": row.get("instance_type"),
            "stratum": row.get("stratum", {}),
            "image_path": str(image_path),
            "label_path": str(label_path),
            "baseline_label_path": str(baseline_path),
            "page_sha256": sha256(image_path),
            "image_width": width,
            "image_height": height,
            "baseline_box_count": len(baseline_lines),
            "corrected_box_count": len(corrected_lines),
            "status": status,
            "human_review": "pending",
            "training_candidate": False,
            "continuation_policy_applied": True,
            "issues_found": notes.get("issues_found", []),
            "reasoning_summary": reasoning,
            "continuations": notes.get("continuations", []),
            "operations": recorded_operations,
            "fine_dimension_review": {
                "scope": f"remaining_pages_batch_{batch_number:02d}",
                "existing_boxes_reviewed": len(baseline_lines),
                "dimension_adjustments": sum(
                    op["operation"] == "resize" for op in operations
                ),
                "boxes_added": sum(op["operation"] == "add" for op in operations),
                "boxes_removed": sum(op["operation"] == "remove" for op in operations),
                "status": status,
            },
            "confidence": notes.get("confidence", 0.94 if not changed else 0.97),
            "downstream_invalidations": notes.get("downstream_invalidations", []),
            "evidence": {
                "before_overlay": str(before_overlay),
                "after_overlay": str(after_overlay),
            },
            "source_provenance_flag": "immutable reviewed source; assignment model v7_401",
            "reviewed_at_utc": reviewed_at,
        }
        write_json(review_path, review)
        row.update(
            {
                "baseline_boxes_total": row.get("baseline_boxes_total", len(baseline_lines)),
                "baseline_class_counts": row.get(
                    "baseline_class_counts",
                    {name: baseline_class_counts[name] for name in CLASS_NAMES.values()},
                ),
                "boxes_total": len(corrected_lines),
                "class_counts": {
                    name: corrected_class_counts[name] for name in CLASS_NAMES.values()
                },
                "status": status,
                "review_record": str(review_path),
                "before_overlay": str(before_overlay),
                "after_overlay": str(after_overlay),
            }
        )

    batch.update(
        {
            "status": "audited_pending_human_review",
            "audited_at_utc": reviewed_at,
            "audit_summary": {
                "pages_audited": len(batch["rows"]),
                "accepted_unchanged": status_counts["accepted_unchanged"],
                "agent_corrected_pending_human": status_counts[
                    "agent_corrected_pending_human"
                ],
                "boxes_adjusted": operation_counts["resize"],
                "boxes_added": operation_counts["add"],
                "boxes_removed": operation_counts["remove"],
                "changed_samples": changed_samples,
                "human_gate_required": True,
            },
        }
    )
    write_json(batch_path, batch)
    jsonl_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in batch["rows"]),
        encoding="utf-8",
    )

    batch_index_path = batch_dir / "batch_index.json"
    batch_index = read_json(batch_index_path)
    audited_batches = 0
    for batch_entry in batch_index["batches"]:
        manifest = read_json(Path(batch_entry["manifest"]))
        batch_entry["status"] = manifest["status"]
        if manifest["status"] == "audited_pending_human_review":
            audited_batches += 1
            batch_entry["audit_summary"] = manifest.get("audit_summary", {})
    batch_index["audited_batches"] = audited_batches
    batch_index["audited_pages"] = sum(
        entry["sample_count"]
        for entry in batch_index["batches"]
        if entry["status"] == "audited_pending_human_review"
    )
    batch_index["status"] = (
        "all_batches_audited_pending_human_review"
        if audited_batches == batch_index["batch_count"]
        else "auditing_in_progress"
    )
    batch_index["last_audited_batch"] = batch["batch_id"]
    batch_index["updated_at_utc"] = reviewed_at
    write_json(batch_index_path, batch_index)

    source_mismatches: list[str] = []
    frozen_count = 0
    frozen_root = workspace / "remaining_review_baseline_labels"
    for frozen_label in sorted(frozen_root.glob("*/*.txt")):
        frozen_count += 1
        split = frozen_label.parent.name
        source_label = source / "labels" / split / frozen_label.name
        if not source_label.exists() or source_label.read_bytes() != frozen_label.read_bytes():
            source_mismatches.append(f"{split}/{frozen_label.name}")

    after_count = sum(
        1
        for row in batch["rows"]
        if Path(row["after_overlay"]).exists()
    )
    before_count = sum(
        1
        for row in batch["rows"]
        if Path(row["before_overlay"]).exists()
    )
    review_count = sum(
        1
        for row in batch["rows"]
        if Path(row["review_record"]).exists()
    )
    validation = {
        "schema_version": "ingrid_batch_audit_validation_v1",
        "validated_at_utc": reviewed_at,
        "batch_id": batch["batch_id"],
        "pages_expected": len(batch["rows"]),
        "pages_reviewed": sum(status_counts.values()),
        "status_counts": dict(status_counts),
        "boxes_adjusted": operation_counts["resize"],
        "boxes_added": operation_counts["add"],
        "boxes_removed": operation_counts["remove"],
        "changed_samples": changed_samples,
        "before_overlays": before_count,
        "after_overlays": after_count,
        "review_records_present": review_count,
        "frozen_remaining_labels_checked_against_source": frozen_count,
        "source_label_mismatches": source_mismatches,
        "source_immutable_check_passed": not source_mismatches and frozen_count == 381,
        "no_ocr_executed": True,
        "no_training_executed": True,
        "no_database_write": True,
    }
    validation_path = batch_dir / f"{batch_name}_audit_validation.json"
    write_json(validation_path, validation)

    if not validation["source_immutable_check_passed"]:
        raise SystemExit("Immutable source validation failed")
    if min(before_count, after_count, review_count) != len(batch["rows"]):
        raise SystemExit("Batch evidence or review records are incomplete")
    print(json.dumps(validation, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
