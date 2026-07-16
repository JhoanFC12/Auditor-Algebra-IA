from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from modulos.instance_factory.problem_detector_corrections import (  # noqa: E402
    summarize_labeled_box_changes,
)


CLASS_NAMES = {0: "problem", 1: "problem_number", 2: "answer_block"}
DEFAULT_SOURCE = (
    REPO_ROOT
    / ".cache"
    / "transcriptor_runs"
    / "datasets"
    / "problem_detector_multiclass_reviewed_20260711_163351"
)
DEFAULT_WORKSPACE = (
    REPO_ROOT
    / ".cache"
    / "transcriptor_runs"
    / "datasets"
    / "problem_detector_multiclass_ingrid_review_20260714_v1"
)
DEFAULT_DATASETS_ROOT = REPO_ROOT / ".cache" / "transcriptor_runs" / "datasets"
DEFAULT_BANK_PARENT = DEFAULT_DATASETS_ROOT / "problem_detector_corrections_live"
DEFAULT_BANK_NAME = "ingrid_v7_401_human_approved_20260714_v1"
DEFAULT_DATASET_NAME = "problem_detector_multiclass_reviewed_20260714_231528_ingrid_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Promueve correcciones humanas aprobadas de Ingrid al banco del detector "
            "y crea un dataset YOLO versionado sin duplicar imagenes."
        )
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--bank-parent", type=Path, default=DEFAULT_BANK_PARENT)
    parser.add_argument("--bank-name", default=DEFAULT_BANK_NAME)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_DATASETS_ROOT)
    parser.add_argument("--dataset-name", default=DEFAULT_DATASET_NAME)
    parser.add_argument("--expected-source-samples", type=int, default=401)
    parser.add_argument("--expected-approved", type=int, default=19)
    parser.add_argument("--expected-final-batch", type=int, default=13)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Materializa el banco y el dataset. Sin esta bandera solo valida y muestra el plan.",
    )
    return parser.parse_args()


def now_text() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Se esperaba un objeto JSON: {path}")
    return payload


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_samples(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError(f"Fila invalida en {path}:{line_number}")
            rows.append(payload)
    return rows


def write_samples(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    path.write_text(text + ("\n" if rows else ""), encoding="utf-8")


def parse_yolo(path: Path) -> list[tuple[int, float, float, float, float]]:
    rows: list[tuple[int, float, float, float, float]] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 5:
            raise ValueError(f"Label YOLO invalido en {path}:{line_number}")
        raw_class = float(parts[0])
        class_id = int(raw_class)
        if raw_class != class_id or class_id not in CLASS_NAMES:
            raise ValueError(f"Clase no autorizada en {path}:{line_number}: {parts[0]}")
        values = tuple(float(value) for value in parts[1:])
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"Coordenada no finita en {path}:{line_number}")
        cx, cy, width, height = values
        if width <= 0 or height <= 0:
            raise ValueError(f"Box sin area en {path}:{line_number}")
        tolerance = 1e-4
        if (
            cx - width / 2 < -tolerance
            or cy - height / 2 < -tolerance
            or cx + width / 2 > 1 + tolerance
            or cy + height / 2 > 1 + tolerance
        ):
            raise ValueError(f"Box fuera de [0,1] en {path}:{line_number}")
        rows.append((class_id, cx, cy, width, height))
    if not rows:
        raise ValueError(f"Label sin boxes: {path}")
    return rows


def yolo_semantically_equal(left: Path, right: Path) -> bool:
    return parse_yolo(left) == parse_yolo(right)


def yolo_box_rows(
    path: Path,
    image_width: int,
    image_height: int,
    *,
    include_order: bool = False,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for order, (class_id, cx, cy, width, height) in enumerate(parse_yolo(path), start=1):
        row: dict[str, Any] = {
            "class": CLASS_NAMES[class_id],
            "class_id": class_id,
            "xyxy": [
                round((cx - width / 2) * image_width),
                round((cy - height / 2) * image_height),
                round((cx + width / 2) * image_width),
                round((cy + height / 2) * image_height),
            ],
        }
        if include_order:
            row["order"] = order
        result.append(row)
    return result


def label_counts(path: Path) -> Counter[int]:
    return Counter(row[0] for row in parse_yolo(path))


def sample_key(split: str, sample_id: str) -> str:
    return f"{split}/{sample_id}"


def aggregate_hash(rows: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for key, value in sorted(rows.items()):
        digest.update(key.encode("utf-8"))
        digest.update(b"\0")
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def source_paths(source: Path, row: dict[str, Any]) -> tuple[Path, Path, Path]:
    split = str(row.get("split") or "")
    sample_id = str(row.get("sample_id") or "")
    image = source / str(row.get("image") or f"images/{split}/{sample_id}.png")
    label = source / str(row.get("label") or f"labels/{split}/{sample_id}.txt")
    metadata = source / str(row.get("metadata") or f"metadata/{split}/{sample_id}.json")
    return image, label, metadata


def audit_inputs(
    source: Path,
    workspace: Path,
    *,
    expected_source_samples: int,
    expected_approved: int,
    expected_final_batch: int,
) -> dict[str, Any]:
    gate = read_json(workspace / "reviews" / "human_approval_gate.json")
    if gate.get("status") != "ready_for_database":
        raise ValueError("El gate humano no esta en ready_for_database.")
    final_manifest = read_json(workspace / "reviews" / "human_approved_13.json")
    final_keys = {
        sample_key(str(row["split"]), str(row["sample_id"]))
        for row in final_manifest.get("rows", [])
    }
    if len(final_keys) != expected_final_batch:
        raise ValueError(
            f"El lote final tiene {len(final_keys)} muestras; se esperaban {expected_final_batch}."
        )

    source_rows = read_samples(source / "samples.jsonl")
    if len(source_rows) != expected_source_samples:
        raise ValueError(
            f"La fuente tiene {len(source_rows)} muestras; se esperaban {expected_source_samples}."
        )
    rows_by_key: dict[str, dict[str, Any]] = {}
    source_label_hashes: dict[str, str] = {}
    for row in source_rows:
        split = str(row.get("split") or "")
        sample_id = str(row.get("sample_id") or "")
        key = sample_key(split, sample_id)
        if not split or not sample_id or key in rows_by_key:
            raise ValueError(f"Muestra fuente invalida o duplicada: {key}")
        image_path, label_path, metadata_path = source_paths(source, row)
        for required in (image_path, label_path, metadata_path):
            if not required.is_file():
                raise FileNotFoundError(required)
        parse_yolo(label_path)
        baseline_path = workspace / "baseline_labels" / split / f"{sample_id}.txt"
        if not baseline_path.is_file() or sha256(label_path) != sha256(baseline_path):
            raise ValueError(f"La fuente no coincide con baseline_labels: {key}")
        rows_by_key[key] = row
        source_label_hashes[key] = sha256(label_path)

    changed: dict[str, dict[str, Any]] = {}
    for key, row in rows_by_key.items():
        split, sample_id = key.split("/", 1)
        _image_path, source_label, _metadata_path = source_paths(source, row)
        working_label = workspace / "labels" / split / f"{sample_id}.txt"
        if not working_label.is_file():
            raise FileNotFoundError(working_label)
        parse_yolo(working_label)
        if yolo_semantically_equal(source_label, working_label):
            continue
        review_path = workspace / "reviews" / split / f"{sample_id}.json"
        review = read_json(review_path)
        if (
            review.get("status") != "human_approved"
            or review.get("human_review") != "approved"
            or review.get("training_candidate") is not True
        ):
            raise ValueError(f"Cambio sin aprobacion humana completa: {key}")
        approved_label_hash = str(review.get("approved_label_sha256") or "")
        approved_baseline_hash = str(review.get("approved_baseline_sha256") or "")
        if approved_label_hash != sha256(working_label):
            raise ValueError(f"Hash aprobado del label no coincide: {key}")
        if approved_baseline_hash != source_label_hashes[key]:
            raise ValueError(f"Hash aprobado del baseline no coincide con la fuente: {key}")
        changed[key] = {
            "key": key,
            "split": split,
            "sample_id": sample_id,
            "source_row": row,
            "source_label": source_label,
            "working_label": working_label,
            "review_path": review_path,
            "review": review,
            "final_batch": key in final_keys,
        }

    approved_candidate_keys: set[str] = set()
    for split in ("train", "val"):
        review_dir = workspace / "reviews" / split
        for review_path in sorted(review_dir.glob("*.json")):
            review = read_json(review_path)
            if (
                review.get("status") == "human_approved"
                and review.get("human_review") == "approved"
                and review.get("training_candidate") is True
            ):
                approved_candidate_keys.add(sample_key(split, review_path.stem))
    if approved_candidate_keys != set(changed):
        missing = sorted(approved_candidate_keys - set(changed))
        extra = sorted(set(changed) - approved_candidate_keys)
        raise ValueError(
            f"Desalineacion entre cambios y candidatos aprobados; sin cambio={missing}, sin registro={extra}"
        )
    if len(changed) != expected_approved:
        raise ValueError(
            f"Se encontraron {len(changed)} cambios aprobados; se esperaban {expected_approved}."
        )
    if not final_keys.issubset(changed):
        raise ValueError("El lote final aprobado no es subconjunto de los cambios promovibles.")

    return {
        "gate": gate,
        "final_manifest": final_manifest,
        "final_keys": final_keys,
        "source_rows": source_rows,
        "rows_by_key": rows_by_key,
        "source_label_hashes": source_label_hashes,
        "source_labels_sha256": aggregate_hash(source_label_hashes),
        "source_manifest_sha256": sha256(source / "manifest.json"),
        "changed": changed,
    }


def hardlink(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    os.link(source, target)
    if not os.path.samefile(source, target):
        raise OSError(f"No se creo un hardlink real: {target}")


def correction_id(split: str, sample_id: str) -> str:
    return f"ingrid_v7_401__{split}__{sample_id}"


def build_bank(
    staging: Path,
    final_root: Path,
    source: Path,
    workspace: Path,
    audit: dict[str, Any],
    transaction_id: str,
    created_at: str,
) -> dict[str, Any]:
    counts_by_change = {"added": 0, "removed": 0, "moved_or_resized": 0, "reordered": 0}
    operation_totals = {key: 0 for key in counts_by_change}
    rows: list[dict[str, Any]] = []
    for key in sorted(audit["changed"]):
        item = audit["changed"][key]
        split = item["split"]
        sample_id = item["sample_id"]
        source_row = item["source_row"]
        review = item["review"]
        source_image, source_label, source_metadata = source_paths(source, source_row)
        with Image.open(source_image) as image:
            image_width, image_height = int(image.width), int(image.height)
        model_boxes = yolo_box_rows(source_label, image_width, image_height)
        human_boxes = yolo_box_rows(
            item["working_label"], image_width, image_height, include_order=True
        )
        summary = summarize_labeled_box_changes(model_boxes, human_boxes)
        for change_name in counts_by_change:
            amount = int(summary.get(change_name) or 0)
            operation_totals[change_name] += amount
            if amount > 0:
                counts_by_change[change_name] += 1

        bank_id = correction_id(split, sample_id)
        image_target = staging / "images" / f"{bank_id}.png"
        label_target = staging / "labels" / f"{bank_id}.txt"
        metadata_target = staging / "metadata" / f"{bank_id}.json"
        hardlink(source_image, image_target)
        label_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item["working_label"], label_target)
        source_meta = read_json(source_metadata)
        metadata = {
            "schema_version": "problem_detector_correction_v1",
            "correction_id": bank_id,
            "sample_id": bank_id,
            "created_at": created_at,
            "updated_at": created_at,
            "revision_count": 1,
            "book_code": str(review.get("book_code") or source_meta.get("book_code") or ""),
            "instance_type": str(
                review.get("instance_type") or source_meta.get("instance_type") or ""
            ),
            "project_name": str(source_meta.get("project_name") or ""),
            "page_number": int(review.get("page_number") or source_meta.get("page_number") or 0),
            "page_record_id": str(source_meta.get("page_record_id") or sample_id),
            "source_pdf": str(source_meta.get("source_pdf") or ""),
            "source_page_image": str(source_image),
            "dataset_root": str(final_root),
            "image_path": str(final_root / "images" / image_target.name),
            "label_path": str(final_root / "labels" / label_target.name),
            "metadata_path": str(final_root / "metadata" / metadata_target.name),
            "image_rel": f"images/{image_target.name}",
            "label_rel": f"labels/{label_target.name}",
            "metadata_rel": f"metadata/{metadata_target.name}",
            "image_size": {"width": image_width, "height": image_height},
            "class_map": {str(key): value for key, value in CLASS_NAMES.items()},
            "model_name": "pdf_problem_detector_multiclass_v7_401",
            "detector_source": "ingrid_review:pdf_problem_detector_multiclass_v7_401",
            "baseline_reviewed_before": True,
            "forced_training_capture": False,
            "capture_reason": "ingrid_human_approved_correction",
            "layout_mode": str((review.get("stratum") or {}).get("layout") or "auto"),
            "model_boxes": model_boxes,
            "human_boxes": human_boxes,
            "change_summary": summary,
            "correction_history": [],
            "training_target": "pdf_problem_detector_yolov8_multiclass_boxes",
            "excluded_future_scope": ["problem_vs_solution_classification"],
            "training_candidate": True,
            "human_review": {
                "status": "approved",
                "reviewed_at": str(review.get("human_reviewed_at") or ""),
                "tool": str(review.get("human_review_tool") or ""),
                "approved_baseline_sha256": str(review.get("approved_baseline_sha256") or ""),
                "approved_label_sha256": str(review.get("approved_label_sha256") or ""),
            },
            "ingrid_provenance": {
                "agent_id": str(review.get("agent_id") or "ingrid_daubechies_v1"),
                "review_record": str(item["review_path"]),
                "review_batch_id": str(review.get("review_batch_id") or "pilot_pre_batch"),
                "final_13_batch": bool(item["final_batch"]),
                "source_dataset_root": str(source),
                "source_split": split,
                "source_sample_id": sample_id,
                "source_label_sha256": sha256(source_label),
                "transaction_id": transaction_id,
                "evidence": review.get("evidence") or {},
            },
        }
        write_json(metadata_target, metadata)
        rows.append(
            {
                "sample_id": bank_id,
                "source_sample_id": sample_id,
                "source_split": split,
                "page_number": metadata["page_number"],
                "book_code": metadata["book_code"],
                "instance_type": metadata["instance_type"],
                "image": metadata["image_rel"],
                "label": metadata["label_rel"],
                "metadata": metadata["metadata_rel"],
                "boxes": len(human_boxes),
                "classes": {
                    CLASS_NAMES[class_id]: count
                    for class_id, count in sorted(label_counts(item["working_label"]).items())
                },
                "final_13_batch": bool(item["final_batch"]),
            }
        )

    write_samples(staging / "samples.jsonl", rows)
    manifest = {
        "schema_version": "problem_detector_corrections_manifest_v1",
        "created_at": created_at,
        "updated_at": created_at,
        "root": str(final_root),
        "manifest_path": str(final_root / "manifest.json"),
        "batch_id": "ingrid-v7_401-human-approved-19",
        "transaction_id": transaction_id,
        "status": "human_approved_materialized",
        "samples_total": len(rows),
        "final_batch_samples": sum(bool(row["final_13_batch"]) for row in rows),
        "prior_pilot_samples": sum(not bool(row["final_13_batch"]) for row in rows),
        "images_dir": str(final_root / "images"),
        "labels_dir": str(final_root / "labels"),
        "metadata_dir": str(final_root / "metadata"),
        "class_map": {str(key): value for key, value in CLASS_NAMES.items()},
        "counts_by_change": counts_by_change,
        "operation_totals": operation_totals,
        "revision_events_total": len(rows),
        "source_dataset_root": str(source),
        "source_manifest_sha256": audit["source_manifest_sha256"],
        "source_labels_sha256": audit["source_labels_sha256"],
        "source_workspace_root": str(workspace),
        "image_link_mode": "hardlink",
        "images_are_not_copied": True,
        "policy": {
            "save_only_human_modified_model_boxes": True,
            "allows_reviewed_page_training_capture": False,
            "problem_vs_solution_classification": "excluded_for_now",
        },
    }
    write_json(staging / "manifest.json", manifest)
    return manifest


def build_dataset(
    staging: Path,
    final_root: Path,
    source: Path,
    workspace: Path,
    bank_root: Path,
    audit: dict[str, Any],
    transaction_id: str,
    created_at: str,
) -> dict[str, Any]:
    output_rows: list[dict[str, Any]] = []
    split_images = Counter()
    split_boxes: dict[str, Counter[int]] = {"train": Counter(), "val": Counter()}
    promoted_rows: list[dict[str, Any]] = []
    for source_row in audit["source_rows"]:
        split = str(source_row["split"])
        sample_id = str(source_row["sample_id"])
        key = sample_key(split, sample_id)
        source_image, source_label, source_metadata = source_paths(source, source_row)
        promoted = audit["changed"].get(key)
        selected_label = promoted["working_label"] if promoted else source_label
        image_target = staging / "images" / split / f"{sample_id}.png"
        label_target = staging / "labels" / split / f"{sample_id}.txt"
        metadata_target = staging / "metadata" / split / f"{sample_id}.json"
        hardlink(source_image, image_target)
        label_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(selected_label, label_target)
        metadata_target.parent.mkdir(parents=True, exist_ok=True)
        if promoted:
            metadata = read_json(source_metadata)
            review = promoted["review"]
            metadata["ingrid_label_promotion"] = {
                "schema_version": "ingrid_label_promotion_v1",
                "status": "human_approved",
                "promoted_at": created_at,
                "transaction_id": transaction_id,
                "agent_id": str(review.get("agent_id") or "ingrid_daubechies_v1"),
                "detector_model_id": str(
                    review.get("detector_model_id") or "pdf_problem_detector_multiclass_v7_401"
                ),
                "review_record": str(promoted["review_path"]),
                "review_batch_id": str(review.get("review_batch_id") or "pilot_pre_batch"),
                "human_reviewed_at": str(review.get("human_reviewed_at") or ""),
                "approved_baseline_sha256": str(review.get("approved_baseline_sha256") or ""),
                "approved_label_sha256": str(review.get("approved_label_sha256") or ""),
                "final_13_batch": bool(promoted["final_batch"]),
                "correction_bank_root": str(bank_root),
            }
            write_json(metadata_target, metadata)
        else:
            shutil.copy2(source_metadata, metadata_target)

        counts = label_counts(selected_label)
        split_images[split] += 1
        split_boxes.setdefault(split, Counter()).update(counts)
        output_row = dict(source_row)
        output_row.update(
            {
                "image": f"images/{split}/{sample_id}.png",
                "label": f"labels/{split}/{sample_id}.txt",
                "metadata": f"metadata/{split}/{sample_id}.json",
                "boxes": sum(counts.values()),
                "classes": {
                    CLASS_NAMES[class_id]: count
                    for class_id, count in sorted(counts.items())
                },
            }
        )
        if promoted:
            review = promoted["review"]
            output_row["label_revision"] = {
                "status": "human_approved",
                "agent_id": "ingrid_daubechies_v1",
                "approved_at": str(review.get("human_reviewed_at") or ""),
                "baseline_sha256": str(review.get("approved_baseline_sha256") or ""),
                "label_sha256": str(review.get("approved_label_sha256") or ""),
                "transaction_id": transaction_id,
            }
            promoted_rows.append(
                {
                    "split": split,
                    "sample_id": sample_id,
                    "page_number": review.get("page_number"),
                    "book_code": review.get("book_code"),
                    "instance_type": review.get("instance_type"),
                    "human_reviewed_at": review.get("human_reviewed_at"),
                    "approved_baseline_sha256": review.get("approved_baseline_sha256"),
                    "approved_label_sha256": review.get("approved_label_sha256"),
                    "final_13_batch": bool(promoted["final_batch"]),
                }
            )
        output_rows.append(output_row)

    write_samples(staging / "samples.jsonl", output_rows)
    dataset_yaml = staging / "dataset.yaml"
    dataset_yaml.write_text(
        "\n".join(
            [
                f"path: {final_root.as_posix()}",
                "train: images/train",
                "val: images/val",
                "names:",
                "  0: problem",
                "  1: problem_number",
                "  2: answer_block",
                "",
            ]
        ),
        encoding="utf-8",
    )
    promotion_manifest = {
        "schema_version": "ingrid_dataset_promotion_manifest_v1",
        "transaction_id": transaction_id,
        "created_at": created_at,
        "status": "human_approved_materialized",
        "source_dataset_root": str(source),
        "source_workspace_root": str(workspace),
        "correction_bank_root": str(bank_root),
        "samples_total": len(promoted_rows),
        "final_batch_samples": sum(bool(row["final_13_batch"]) for row in promoted_rows),
        "prior_pilot_samples": sum(not bool(row["final_13_batch"]) for row in promoted_rows),
        "rows": promoted_rows,
    }
    write_json(staging / "promotion_manifest.json", promotion_manifest)
    manifest = {
        "schema_version": "problem_detector_multiclass_reviewed_dataset_v2",
        "created_at": created_at,
        "status": "prepared_no_training",
        "transaction_id": transaction_id,
        "parent_dataset_root": str(source),
        "parent_manifest_sha256": audit["source_manifest_sha256"],
        "parent_labels_sha256": audit["source_labels_sha256"],
        "source_roots": [str(source), str(bank_root)],
        "source_workspace_root": str(workspace),
        "dataset_yaml": str(final_root / "dataset.yaml"),
        "promotion_manifest": str(final_root / "promotion_manifest.json"),
        "samples_total": len(output_rows),
        "promoted_samples": len(promoted_rows),
        "final_batch_samples": promotion_manifest["final_batch_samples"],
        "prior_pilot_samples": promotion_manifest["prior_pilot_samples"],
        "image_link_mode": "hardlink",
        "images_are_not_copied": True,
        "splits_preserved": True,
        "splits": {
            split: {
                "images": split_images[split],
                "boxes": sum(split_boxes[split].values()),
                "classes": {
                    CLASS_NAMES[class_id]: split_boxes[split][class_id]
                    for class_id in sorted(CLASS_NAMES)
                },
            }
            for split in ("train", "val")
        },
        "prohibitions_respected": {
            "source_modified": False,
            "ocr_executed": False,
            "training_executed": False,
            "model_promoted": False,
            "env_changed": False,
        },
    }
    write_json(staging / "manifest.json", manifest)
    return manifest


def validate_staging(
    bank_staging: Path,
    dataset_staging: Path,
    source: Path,
    audit: dict[str, Any],
) -> dict[str, Any]:
    changed_keys = set(audit["changed"])
    output_changed: set[str] = set()
    hardlinked_images = 0
    for source_row in audit["source_rows"]:
        split = str(source_row["split"])
        sample_id = str(source_row["sample_id"])
        key = sample_key(split, sample_id)
        source_image, source_label, _source_metadata = source_paths(source, source_row)
        output_image = dataset_staging / "images" / split / f"{sample_id}.png"
        output_label = dataset_staging / "labels" / split / f"{sample_id}.txt"
        output_metadata = dataset_staging / "metadata" / split / f"{sample_id}.json"
        if not output_image.is_file() or not output_label.is_file() or not output_metadata.is_file():
            raise FileNotFoundError(f"Salida incompleta: {key}")
        if not os.path.samefile(source_image, output_image):
            raise ValueError(f"La imagen no es hardlink: {key}")
        hardlinked_images += 1
        parse_yolo(output_label)
        if not yolo_semantically_equal(source_label, output_label):
            output_changed.add(key)
            expected_hash = str(audit["changed"][key]["review"]["approved_label_sha256"])
            if sha256(output_label) != expected_hash:
                raise ValueError(f"Label promovido no coincide con la aprobacion: {key}")
    if output_changed != changed_keys:
        raise ValueError(
            f"El dataset no reemplaza exactamente los aprobados: {sorted(output_changed ^ changed_keys)}"
        )

    current_source_hashes: dict[str, str] = {}
    for key, source_row in audit["rows_by_key"].items():
        _source_image, source_label, _source_metadata = source_paths(source, source_row)
        current_source_hashes[key] = sha256(source_label)
    if current_source_hashes != audit["source_label_hashes"]:
        raise ValueError("La fuente cambio durante la materializacion.")
    if aggregate_hash(current_source_hashes) != audit["source_labels_sha256"]:
        raise ValueError("El digest agregado de la fuente cambio durante la materializacion.")

    bank_manifest = read_json(bank_staging / "manifest.json")
    dataset_manifest = read_json(dataset_staging / "manifest.json")
    bank_metadata = list((bank_staging / "metadata").glob("*.json"))
    if len(bank_metadata) != len(changed_keys):
        raise ValueError("El banco de correcciones no contiene exactamente las muestras aprobadas.")
    for key, item in audit["changed"].items():
        bank_id = correction_id(item["split"], item["sample_id"])
        bank_image = bank_staging / "images" / f"{bank_id}.png"
        bank_label = bank_staging / "labels" / f"{bank_id}.txt"
        source_image, _source_label, _source_metadata = source_paths(source, item["source_row"])
        if not os.path.samefile(source_image, bank_image):
            raise ValueError(f"Imagen del banco no es hardlink: {key}")
        if sha256(bank_label) != str(item["review"]["approved_label_sha256"]):
            raise ValueError(f"Label del banco no coincide con la aprobacion: {key}")

    return {
        "source_samples": len(audit["source_rows"]),
        "approved_samples": len(changed_keys),
        "final_batch_samples": len(audit["final_keys"]),
        "prior_pilot_samples": len(changed_keys - set(audit["final_keys"])),
        "dataset_images_hardlinked": hardlinked_images,
        "bank_images_hardlinked": len(changed_keys),
        "bank_samples": int(bank_manifest.get("samples_total") or 0),
        "dataset_samples": int(dataset_manifest.get("samples_total") or 0),
        "train_images": int(dataset_manifest["splits"]["train"]["images"]),
        "val_images": int(dataset_manifest["splits"]["val"]["images"]),
        "train_boxes": int(dataset_manifest["splits"]["train"]["boxes"]),
        "val_boxes": int(dataset_manifest["splits"]["val"]["boxes"]),
        "source_labels_unchanged": len(current_source_hashes),
        "source_labels_sha256": audit["source_labels_sha256"],
    }


def remove_staging(path: Path, parent: Path) -> None:
    try:
        resolved = path.resolve()
        resolved.relative_to(parent.resolve())
    except (OSError, ValueError):
        return
    if resolved.name.startswith(".") and ".staging-" in resolved.name and resolved.exists():
        shutil.rmtree(resolved)


def materialize(args: argparse.Namespace, audit: dict[str, Any]) -> dict[str, Any]:
    source = args.source.expanduser().resolve()
    workspace = args.workspace.expanduser().resolve()
    bank_parent = args.bank_parent.expanduser().resolve()
    out_root = args.out_root.expanduser().resolve()
    bank_final = bank_parent / str(args.bank_name)
    dataset_final = out_root / str(args.dataset_name)
    if bank_final.exists() or dataset_final.exists():
        if bank_final.is_dir() and dataset_final.is_dir():
            bank_manifest = read_json(bank_final / "manifest.json")
            dataset_manifest = read_json(dataset_final / "manifest.json")
            if (
                int(bank_manifest.get("samples_total") or 0) == len(audit["changed"])
                and int(dataset_manifest.get("promoted_samples") or 0) == len(audit["changed"])
                and str(dataset_manifest.get("parent_dataset_root") or "") == str(source)
            ):
                return {
                    "status": "already_materialized",
                    "transaction_id": str(dataset_manifest.get("transaction_id") or ""),
                    "bank_root": str(bank_final),
                    "dataset_root": str(dataset_final),
                    "approved_samples": len(audit["changed"]),
                }
        raise FileExistsError(
            f"Destino existente no idempotente; no se sobrescribira: {bank_final} | {dataset_final}"
        )

    transaction_id = f"ingrid-db-{uuid.uuid4().hex[:12]}"
    created_at = now_text()
    bank_staging = bank_parent / f".{args.bank_name}.staging-{transaction_id}"
    dataset_staging = out_root / f".{args.dataset_name}.staging-{transaction_id}"
    bank_parent.mkdir(parents=True, exist_ok=True)
    out_root.mkdir(parents=True, exist_ok=True)
    if bank_staging.exists() or dataset_staging.exists():
        raise FileExistsError("Ya existe un staging con el mismo id de transaccion.")

    bank_committed = False
    try:
        build_bank(
            bank_staging,
            bank_final,
            source,
            workspace,
            audit,
            transaction_id,
            created_at,
        )
        build_dataset(
            dataset_staging,
            dataset_final,
            source,
            workspace,
            bank_final,
            audit,
            transaction_id,
            created_at,
        )
        validation = validate_staging(bank_staging, dataset_staging, source, audit)
        os.replace(bank_staging, bank_final)
        bank_committed = True
        try:
            os.replace(dataset_staging, dataset_final)
        except Exception:
            os.replace(bank_final, bank_staging)
            bank_committed = False
            raise

        workspace_manifest_path = workspace / "workspace_manifest.json"
        workspace_manifest = read_json(workspace_manifest_path)
        backup_path = (
            workspace
            / "reviews"
            / "promotion_backups"
            / f"workspace_manifest_before_{transaction_id}.json"
        )
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(workspace_manifest_path, backup_path)
        workspace_manifest["status"] = "human_approved_materialized_to_training_bank"
        workspace_manifest["database_promotion"] = {
            "schema_version": "ingrid_database_promotion_receipt_v1",
            "status": "completed",
            "transaction_id": transaction_id,
            "promoted_at": created_at,
            "approved_samples": len(audit["changed"]),
            "final_batch_samples": len(audit["final_keys"]),
            "prior_pilot_samples": len(audit["changed"]) - len(audit["final_keys"]),
            "bank_root": str(bank_final),
            "dataset_root": str(dataset_final),
            "source_root": str(source),
            "source_manifest_sha256": audit["source_manifest_sha256"],
            "source_labels_sha256": audit["source_labels_sha256"],
            "source_immutable_verified": True,
            "training_executed": False,
            "model_promoted": False,
        }
        write_json(workspace_manifest_path, workspace_manifest)
        receipt = {
            **workspace_manifest["database_promotion"],
            "validation": validation,
            "workspace_manifest_backup": str(backup_path),
        }
        write_json(workspace / "reviews" / "database_promotion_receipt.json", receipt)
        return {
            "status": "materialized",
            "transaction_id": transaction_id,
            "bank_root": str(bank_final),
            "dataset_root": str(dataset_final),
            "receipt": str(workspace / "reviews" / "database_promotion_receipt.json"),
            "validation": validation,
        }
    finally:
        if not bank_committed:
            remove_staging(bank_staging, bank_parent)
        remove_staging(dataset_staging, out_root)


def main() -> int:
    args = parse_args()
    source = args.source.expanduser().resolve()
    workspace = args.workspace.expanduser().resolve()
    audit = audit_inputs(
        source,
        workspace,
        expected_source_samples=max(1, int(args.expected_source_samples)),
        expected_approved=max(1, int(args.expected_approved)),
        expected_final_batch=max(1, int(args.expected_final_batch)),
    )
    plan = {
        "status": "validated_dry_run" if not args.apply else "validated_for_apply",
        "source_root": str(source),
        "workspace_root": str(workspace),
        "source_samples": len(audit["source_rows"]),
        "source_manifest_sha256": audit["source_manifest_sha256"],
        "source_labels_sha256": audit["source_labels_sha256"],
        "approved_samples": len(audit["changed"]),
        "final_batch_samples": len(audit["final_keys"]),
        "prior_pilot_samples": len(audit["changed"]) - len(audit["final_keys"]),
        "bank_root": str(args.bank_parent.expanduser().resolve() / str(args.bank_name)),
        "dataset_root": str(args.out_root.expanduser().resolve() / str(args.dataset_name)),
        "images_policy": "hardlink_only",
        "source_will_be_modified": False,
        "training_will_run": False,
        "model_will_be_promoted": False,
    }
    if not args.apply:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    result = materialize(args, audit)
    print(json.dumps({**plan, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
