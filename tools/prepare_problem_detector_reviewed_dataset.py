from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
from collections import Counter
from datetime import datetime
import os
from pathlib import Path
from typing import Any


CLASS_NAMES = {
    0: "problem",
    1: "problem_number",
    2: "answer_block",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_source_root() -> Path:
    return repo_root() / ".cache" / "transcriptor_runs" / "datasets" / "problem_detector_multiclass_100_lab_20260624"


def default_out_root() -> Path:
    return repo_root() / ".cache" / "transcriptor_runs" / "datasets"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(read_text(path))


def fs_path(path: Path) -> str:
    resolved = str(Path(path).expanduser().resolve())
    if os.name != "nt":
        return resolved
    if resolved.startswith("\\\\?\\"):
        return resolved
    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved.lstrip("\\")
    return "\\\\?\\" + resolved


def path_exists(path: Path) -> bool:
    return os.path.exists(fs_path(path))


def read_text(path: Path) -> str:
    with open(fs_path(path), "r", encoding="utf-8") as handle:
        return handle.read()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(fs_path(path), "w", encoding="utf-8") as handle:
        handle.write(text)


def copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    source_path = fs_path(source)
    target_path = fs_path(target)
    try:
        os.link(source_path, target_path)
    except OSError:
        shutil.copy2(source_path, target_path)


def is_reviewed(metadata: dict[str, Any]) -> bool:
    review = metadata.get("label_review")
    if isinstance(review, dict) and bool(str(review.get("reviewed_at") or "").strip()):
        return True
    if metadata.get("schema_version") == "problem_detector_correction_v1":
        return bool(metadata.get("forced_training_capture") or metadata.get("human_boxes"))
    return False


def count_label_classes(label_path: Path) -> Counter[int]:
    counts: Counter[int] = Counter()
    if not path_exists(label_path):
        return counts
    for raw_line in read_text(label_path).splitlines():
        parts = raw_line.strip().split()
        if not parts:
            continue
        try:
            counts[int(float(parts[0]))] += 1
        except Exception:
            counts[-1] += 1
    return counts


def split_for_sample(sample_id: str, *, val_ratio: float, seed: int) -> str:
    digest = hashlib.sha1(f"{seed}:{sample_id}".encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return "val" if bucket < val_ratio else "train"


def safe_sample_id(source_root: Path, sample_id: str) -> str:
    source_key = hashlib.sha1(str(source_root.resolve()).encode("utf-8")).hexdigest()[:8]
    sample_key = hashlib.sha1(sample_id.encode("utf-8")).hexdigest()[:16]
    return f"{source_key}_{sample_key}"


def iter_dataset_roots(source_root: Path) -> list[Path]:
    """Return direct or per-instance correction datasets below a source root."""
    source_root = source_root.resolve()
    if (source_root / "metadata").is_dir():
        return [source_root]
    roots = {
        metadata_dir.parent.resolve()
        for metadata_dir in source_root.rglob("metadata")
        if metadata_dir.is_dir()
        and (metadata_dir.parent / "images").is_dir()
        and (metadata_dir.parent / "labels").is_dir()
    }
    return sorted(roots, key=lambda path: str(path).lower())


def iter_reviewed_rows(source_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if (source_root / "metadata" / "train").is_dir():
        for split in ("train", "val"):
            metadata_dir = source_root / "metadata" / split
            if not metadata_dir.is_dir():
                continue
            rows.extend(
                iter_reviewed_rows_from_dataset(
                    source_root,
                    images_dir=source_root / "images" / split,
                    labels_dir=source_root / "labels" / split,
                    metadata_dir=metadata_dir,
                    prefer_metadata_stem=True,
                )
            )
        return rows
    for dataset_root in iter_dataset_roots(source_root):
        rows.extend(iter_reviewed_rows_from_dataset(dataset_root))
    return rows


def iter_reviewed_rows_from_dataset(
    source_root: Path,
    *,
    images_dir: Path | None = None,
    labels_dir: Path | None = None,
    metadata_dir: Path | None = None,
    prefer_metadata_stem: bool = False,
) -> list[dict[str, Any]]:
    source_root = source_root.resolve()
    source_images = images_dir or source_root / "images"
    source_labels = labels_dir or source_root / "labels"
    source_metadata = metadata_dir or source_root / "metadata"

    rows: list[dict[str, Any]] = []
    for metadata_path in sorted(source_metadata.glob("*.json")):
        metadata = load_json(metadata_path)
        if not is_reviewed(metadata):
            continue
        sample_id = metadata_path.stem if prefer_metadata_stem else str(metadata.get("sample_id") or metadata_path.stem)
        image_path = source_images / f"{sample_id}.png"
        label_path = source_labels / f"{sample_id}.txt"
        if not path_exists(image_path) or not path_exists(label_path):
            continue
        label_counts = count_label_classes(label_path)
        if sum(label_counts.values()) <= 0:
            continue
        rows.append(
            {
                "sample_id": safe_sample_id(source_root, sample_id),
                "original_sample_id": sample_id,
                "source_root": str(source_root),
                "image_path": image_path,
                "label_path": label_path,
                "metadata_path": metadata_path,
                "label_counts": dict(label_counts),
                "group": metadata.get("group", ""),
                "instance": metadata.get("instance", ""),
            }
        )
    return rows


def build_dataset(*, source_roots: list[Path], out_root: Path, val_ratio: float, seed: int) -> Path:
    out_dir = out_root.resolve() / f"problem_detector_multiclass_reviewed_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    rows: list[dict[str, Any]] = []
    for source_root in source_roots:
        rows.extend(iter_reviewed_rows(source_root))

    rng = random.Random(seed)
    rng.shuffle(rows)
    for row in rows:
        row["split"] = split_for_sample(str(row["sample_id"]), val_ratio=val_ratio, seed=seed)

    if rows and not any(row["split"] == "val" for row in rows):
        rows[0]["split"] = "val"
    if len(rows) > 1 and not any(row["split"] == "train" for row in rows):
        rows[-1]["split"] = "train"

    for split in ("train", "val"):
        (out_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (out_dir / "labels" / split).mkdir(parents=True, exist_ok=True)
        (out_dir / "metadata" / split).mkdir(parents=True, exist_ok=True)

    split_counts: dict[str, Counter[int]] = {"train": Counter(), "val": Counter()}
    output_rows: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: (str(item["split"]), str(item["sample_id"]))):
        split = str(row["split"])
        sample_id = str(row["sample_id"])
        dst_image = out_dir / "images" / split / f"{sample_id}.png"
        dst_label = out_dir / "labels" / split / f"{sample_id}.txt"
        dst_metadata = out_dir / "metadata" / split / f"{sample_id}.json"
        copy_file(row["image_path"], dst_image)
        copy_file(row["label_path"], dst_label)
        copy_file(row["metadata_path"], dst_metadata)
        label_counts = Counter({int(k): int(v) for k, v in row["label_counts"].items()})
        split_counts[split].update(label_counts)
        output_rows.append(
            {
                "sample_id": sample_id,
                "split": split,
                "image": str(dst_image.relative_to(out_dir)).replace("\\", "/"),
                "label": str(dst_label.relative_to(out_dir)).replace("\\", "/"),
                "metadata": str(dst_metadata.relative_to(out_dir)).replace("\\", "/"),
                "original_sample_id": row.get("original_sample_id", ""),
                "source_root": row.get("source_root", ""),
                "group": row.get("group", ""),
                "instance": row.get("instance", ""),
                "boxes": sum(label_counts.values()),
                "classes": {CLASS_NAMES.get(cls, str(cls)): count for cls, count in sorted(label_counts.items())},
            }
        )

    dataset_yaml = out_dir / "dataset.yaml"
    write_text(
        dataset_yaml,
        "\n".join(
            [
                f"path: {out_dir.as_posix()}",
                "train: images/train",
                "val: images/val",
                "names:",
                "  0: problem",
                "  1: problem_number",
                "  2: answer_block",
                "",
            ]
        ),
    )
    write_text(
        out_dir / "samples.jsonl",
        "\n".join(json.dumps(row, ensure_ascii=False) for row in output_rows) + ("\n" if output_rows else ""),
    )
    manifest = {
        "schema_version": "problem_detector_multiclass_reviewed_dataset_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_roots": [str(path.resolve()) for path in source_roots],
        "dataset_yaml": str(dataset_yaml),
        "seed": seed,
        "val_ratio": val_ratio,
        "samples_total": len(output_rows),
        "splits": {
            split: {
                "images": sum(1 for row in output_rows if row["split"] == split),
                "boxes": sum(split_counts[split].values()),
                "classes": {CLASS_NAMES.get(cls, str(cls)): count for cls, count in sorted(split_counts[split].items())},
            }
            for split in ("train", "val")
        },
    }
    write_text(out_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return out_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepara dataset YOLO solo con labels revisados del laboratorio.")
    parser.add_argument(
        "--source-root",
        type=Path,
        action="append",
        default=[],
        help="Dataset Lab fuente. Puede repetirse para combinar varios.",
    )
    parser.add_argument("--out-root", type=Path, default=default_out_root())
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    source_roots = args.source_root or [default_source_root()]
    out_dir = build_dataset(
        source_roots=source_roots,
        out_root=args.out_root,
        val_ratio=max(0.05, min(0.5, float(args.val_ratio))),
        seed=int(args.seed),
    )
    manifest = load_json(out_dir / "manifest.json")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
