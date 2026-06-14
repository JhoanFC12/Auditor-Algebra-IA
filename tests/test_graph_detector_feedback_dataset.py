from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools.build_graph_detector_feedback_dataset import (
    DATASET_KIND,
    MANIFEST_SCHEMA_VERSION,
    build_feedback_dataset,
)


def _write_image(path: Path, size: tuple[int, int] = (100, 80)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color=(255, 255, 255)).save(path)


class GraphDetectorFeedbackDatasetTests(unittest.TestCase):
    def test_builds_yolo_dataset_from_live_golden_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "segment_training_live"
            source = live / "source_images" / "rec1_crop.png"
            _write_image(source)
            records = live / "records"
            records.mkdir(parents=True)
            (records / "rec1.json").write_text(
                json.dumps(
                    {
                        "schema_version": "segment_training_live_source_v1",
                        "record_id": "rec1",
                        "source_path": "missing/original.png",
                        "source_image_rel": "source_images/rec1_crop.png",
                        "boxes_total": 1,
                        "boxes_px": [[10, 20, 60, 70]],
                        "segments": [{"idx": 1, "bbox_px": [10, 20, 60, 70]}],
                        "detector_review": {
                            "review_status": "corrected",
                            "diagram_presence_label": "yes",
                            "detector_source": "human_reviewed_segments",
                            "predicted_boxes": [],
                            "final_boxes": [{"bbox_px": [10, 20, 60, 70]}],
                        },
                    }
                ),
                encoding="utf-8",
            )

            manifest = build_feedback_dataset(segments_root=live, out_dir=root / "out")

            self.assertEqual(manifest["schema_version"], MANIFEST_SCHEMA_VERSION)
            self.assertEqual(manifest["dataset_kind"], DATASET_KIND)
            self.assertEqual(manifest["samples"], 1)
            self.assertEqual(manifest["positive_samples"], 1)
            self.assertEqual(manifest["corrected_samples"], 1)
            self.assertEqual(manifest["remaining_to_target"], 199)
            label = root / "out" / "labels" / "rec1.txt"
            self.assertTrue(label.exists())
            self.assertEqual(len(label.read_text(encoding="utf-8").strip().split()), 5)
            records_rows = [
                json.loads(line)
                for line in (root / "out" / "records.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(records_rows[0]["source_kind"], "segment_training_live")

    def test_keeps_negative_corrected_live_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "segment_training_live"
            source = live / "source_images" / "rec2_crop.png"
            _write_image(source)
            records = live / "records"
            records.mkdir(parents=True)
            (records / "rec2.json").write_text(
                json.dumps(
                    {
                        "schema_version": "segment_training_live_source_v1",
                        "record_id": "rec2",
                        "source_image_rel": "source_images/rec2_crop.png",
                        "boxes_total": 0,
                        "boxes_px": [],
                        "segments": [],
                        "detector_review": {
                            "review_status": "corrected",
                            "diagram_presence_label": "no",
                            "detector_source": "human_reviewed_segments",
                            "predicted_boxes": [{"bbox_px": [10, 20, 60, 70]}],
                            "final_boxes": [],
                        },
                    }
                ),
                encoding="utf-8",
            )

            manifest = build_feedback_dataset(segments_root=live, out_dir=root / "out")

            self.assertEqual(manifest["samples"], 1)
            self.assertEqual(manifest["positive_samples"], 0)
            self.assertEqual(manifest["negative_samples"], 1)
            self.assertEqual((root / "out" / "labels" / "rec2.txt").read_text(encoding="utf-8"), "")

    def test_skips_reviewed_without_changes_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "segment_training_live"
            source = live / "source_images" / "rec3_crop.png"
            _write_image(source)
            records = live / "records"
            records.mkdir(parents=True)
            (records / "rec3.json").write_text(
                json.dumps(
                    {
                        "schema_version": "segment_training_live_source_v1",
                        "record_id": "rec3",
                        "source_image_rel": "source_images/rec3_crop.png",
                        "boxes_total": 1,
                        "boxes_px": [[10, 20, 60, 70]],
                        "segments": [{"idx": 1, "bbox_px": [10, 20, 60, 70]}],
                        "detector_review": {
                            "review_status": "reviewed",
                            "diagram_presence_label": "yes",
                            "detector_source": "human_reviewed_segments",
                            "predicted_boxes": [{"bbox_px": [10, 20, 60, 70]}],
                            "final_boxes": [{"bbox_px": [10, 20, 60, 70]}],
                        },
                    }
                ),
                encoding="utf-8",
            )

            manifest = build_feedback_dataset(segments_root=live, out_dir=root / "out")
            audit_manifest = build_feedback_dataset(
                segments_root=live,
                out_dir=root / "out_audit",
                corrected_only=False,
            )

            self.assertEqual(manifest["samples"], 0)
            self.assertEqual(manifest["corrected_samples"], 0)
            self.assertEqual(audit_manifest["samples"], 1)


if __name__ == "__main__":
    unittest.main()
