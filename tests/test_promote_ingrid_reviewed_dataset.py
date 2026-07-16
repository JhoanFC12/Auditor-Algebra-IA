from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools.promote_ingrid_reviewed_dataset import audit_inputs, materialize


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PromoteIngridReviewedDatasetTests(unittest.TestCase):
    def test_materializes_only_approved_changes_with_hardlinked_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            workspace = root / "workspace"
            datasets = root / "datasets"
            bank_parent = datasets / "problem_detector_corrections_live"
            rows = []
            for split, sample_id, label in (
                ("train", "sample_a", "0 0.500000 0.500000 0.400000 0.400000\n"),
                ("val", "sample_b", "2 0.500000 0.500000 0.300000 0.200000\n"),
            ):
                for base in (source, workspace):
                    (base / "images" / split).mkdir(parents=True, exist_ok=True)
                    (base / "labels" / split).mkdir(parents=True, exist_ok=True)
                    (base / "metadata" / split).mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (100, 120), "white").save(
                    source / "images" / split / f"{sample_id}.png"
                )
                os.link(
                    source / "images" / split / f"{sample_id}.png",
                    workspace / "images" / split / f"{sample_id}.png",
                )
                (source / "labels" / split / f"{sample_id}.txt").write_text(
                    label, encoding="utf-8"
                )
                (workspace / "labels" / split / f"{sample_id}.txt").write_text(
                    label, encoding="utf-8"
                )
                (source / "metadata" / split / f"{sample_id}.json").write_text(
                    json.dumps(
                        {
                            "schema_version": "problem_detector_correction_v1",
                            "book_code": "book",
                            "instance_type": "instance",
                            "page_number": len(rows) + 1,
                            "human_boxes": [{"class": "problem", "xyxy": [1, 1, 10, 10]}],
                        }
                    ),
                    encoding="utf-8",
                )
                (workspace / "metadata" / split / f"{sample_id}.json").write_text(
                    (source / "metadata" / split / f"{sample_id}.json").read_text(
                        encoding="utf-8"
                    ),
                    encoding="utf-8",
                )
                baseline = workspace / "baseline_labels" / split / f"{sample_id}.txt"
                baseline.parent.mkdir(parents=True, exist_ok=True)
                baseline.write_text(label, encoding="utf-8")
                rows.append(
                    {
                        "sample_id": sample_id,
                        "split": split,
                        "image": f"images/{split}/{sample_id}.png",
                        "label": f"labels/{split}/{sample_id}.txt",
                        "metadata": f"metadata/{split}/{sample_id}.json",
                        "boxes": 1,
                        "classes": {"problem": 1},
                    }
                )

            (source / "samples.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
            )
            (source / "manifest.json").write_text(
                json.dumps({"schema_version": "source_v1", "samples_total": 2}),
                encoding="utf-8",
            )
            (workspace / "workspace_manifest.json").write_text(
                json.dumps({"schema_version": "workspace_v1", "status": "pending"}),
                encoding="utf-8",
            )
            changed_label = workspace / "labels" / "train" / "sample_a.txt"
            changed_label.write_text(
                "0 0.500000 0.500000 0.500000 0.400000\n", encoding="utf-8"
            )
            review_dir = workspace / "reviews" / "train"
            review_dir.mkdir(parents=True, exist_ok=True)
            review = {
                "schema_version": "ingrid_training_box_review_v1",
                "agent_id": "ingrid_daubechies_v1",
                "detector_model_id": "pdf_problem_detector_multiclass_v7_401",
                "sample_id": "sample_a",
                "split": "train",
                "status": "human_approved",
                "human_review": "approved",
                "training_candidate": True,
                "human_reviewed_at": "2026-07-14T23:15:28-05:00",
                "approved_baseline_sha256": file_hash(
                    source / "labels" / "train" / "sample_a.txt"
                ),
                "approved_label_sha256": file_hash(changed_label),
                "page_number": 1,
                "book_code": "book",
                "instance_type": "instance",
            }
            (review_dir / "sample_a.json").write_text(
                json.dumps(review), encoding="utf-8"
            )
            (workspace / "reviews" / "human_approval_gate.json").write_text(
                json.dumps({"status": "ready_for_database"}), encoding="utf-8"
            )
            (workspace / "reviews" / "human_approved_13.json").write_text(
                json.dumps(
                    {
                        "rows": [
                            {"split": "train", "sample_id": "sample_a"}
                        ]
                    }
                ),
                encoding="utf-8",
            )

            audit = audit_inputs(
                source,
                workspace,
                expected_source_samples=2,
                expected_approved=1,
                expected_final_batch=1,
            )
            args = argparse.Namespace(
                source=source,
                workspace=workspace,
                bank_parent=bank_parent,
                bank_name="ingrid_batch",
                out_root=datasets,
                dataset_name="reviewed_dataset",
            )
            result = materialize(args, audit)

            self.assertEqual(result["status"], "materialized")
            dataset = datasets / "reviewed_dataset"
            bank = bank_parent / "ingrid_batch"
            self.assertTrue(
                os.path.samefile(
                    source / "images" / "train" / "sample_a.png",
                    dataset / "images" / "train" / "sample_a.png",
                )
            )
            self.assertTrue(
                os.path.samefile(
                    source / "images" / "train" / "sample_a.png",
                    bank / "images" / "ingrid_v7_401__train__sample_a.png",
                )
            )
            self.assertEqual(
                file_hash(dataset / "labels" / "train" / "sample_a.txt"),
                review["approved_label_sha256"],
            )
            self.assertEqual(
                file_hash(dataset / "labels" / "val" / "sample_b.txt"),
                file_hash(source / "labels" / "val" / "sample_b.txt"),
            )
            self.assertEqual(result["validation"]["source_labels_unchanged"], 2)
            repeated = materialize(args, audit)
            self.assertEqual(repeated["status"], "already_materialized")


if __name__ == "__main__":
    unittest.main()
