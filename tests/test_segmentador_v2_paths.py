from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from modulos.modulo0_transcriptor.segmentador_v2 import SegmentadorProblemasV2


class SegmentadorV2PathTests(unittest.TestCase):
    def test_preserves_short_source_stem_for_legacy_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "segments"
            src = Path(tmp) / "crop_001.png"
            segmenter = SegmentadorProblemasV2(root)

            segmenter.persist_segments_manifest(src=src, segments=[])

            manifest = root / "crop_001" / "segments_manifest.json"
            self.assertTrue(manifest.exists())
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(payload["source_stem"], "crop_001")
            self.assertEqual(payload["source_dir"], "crop_001")

    def test_compacts_long_source_stem_for_windows_safe_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "segments"
            long_stem = "aseuni-semianual-geometria__semana_2_dc9b1f016c____ASEUNI_SEM_" + ("x" * 140)
            src = Path(tmp) / f"{long_stem}.png"
            segmenter = SegmentadorProblemasV2(root)

            segmenter.persist_segments_manifest(src=src, segments=[])

            dirs = [path for path in root.iterdir() if path.is_dir()]
            self.assertEqual(len(dirs), 1)
            self.assertNotEqual(dirs[0].name, long_stem)
            self.assertLessEqual(len(dirs[0].name), 32)
            manifest = dirs[0] / "segments_manifest.json"
            self.assertTrue(manifest.exists())
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(payload["source_stem"], long_stem)
            self.assertEqual(payload["source_dir"], dirs[0].name)

    def test_reviewed_segments_feed_live_golden_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            segments_root = root / "segments"
            live_root = root / "segment_training_live"
            src = root / "crop_001.png"
            Image.new("RGB", (120, 90), color=(255, 255, 255)).save(src)
            previous_live = os.environ.get("SEGMENT_LIVE_GOLDEN_BASE")
            try:
                os.environ["SEGMENT_LIVE_GOLDEN_BASE"] = str(live_root)
                segmenter = SegmentadorProblemasV2(segments_root)

                segmenter.save_reviewed_segments(src, [(10, 15, 80, 70)])

                manifest = json.loads((live_root / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest["schema_version"], "segment_training_live_index_v1")
                self.assertEqual(manifest["records_total"], 1)
                self.assertEqual(manifest["boxes_total"], 1)
                self.assertEqual(manifest["corrected_images"], 1)
                self.assertEqual(manifest["remaining_corrected_images"], 199)
                records = list((live_root / "records").glob("*.json"))
                self.assertEqual(len(records), 1)
                record = json.loads(records[0].read_text(encoding="utf-8"))
                self.assertEqual(record["schema_version"], "segment_training_live_source_v1")
                self.assertEqual(record["boxes_px"], [[10, 15, 80, 70]])
                self.assertEqual(record["detector_review"]["review_status"], "corrected")
                self.assertEqual(record["detector_review"]["diagram_presence_label"], "yes")
            finally:
                if previous_live is None:
                    os.environ.pop("SEGMENT_LIVE_GOLDEN_BASE", None)
                else:
                    os.environ["SEGMENT_LIVE_GOLDEN_BASE"] = previous_live

    def test_reviewed_segments_without_changes_stay_as_reviewed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            segments_root = root / "segments"
            live_root = root / "segment_training_live"
            src = root / "crop_002.png"
            Image.new("RGB", (120, 90), color=(255, 255, 255)).save(src)
            previous_live = os.environ.get("SEGMENT_LIVE_GOLDEN_BASE")
            try:
                os.environ["SEGMENT_LIVE_GOLDEN_BASE"] = str(live_root)
                segmenter = SegmentadorProblemasV2(segments_root)

                segmenter.save_reviewed_segments(
                    src,
                    [(10, 15, 80, 70)],
                    detector_payload={
                        "review_status": "predicted",
                        "diagram_presence_label": "yes",
                        "predicted_boxes": [{"bbox_px": [10, 15, 80, 70], "conf": 0.9}],
                    },
                )

                records = list((live_root / "records").glob("*.json"))
                self.assertEqual(len(records), 1)
                record = json.loads(records[0].read_text(encoding="utf-8"))
                self.assertEqual(record["detector_review"]["review_status"], "reviewed")
            finally:
                if previous_live is None:
                    os.environ.pop("SEGMENT_LIVE_GOLDEN_BASE", None)
                else:
                    os.environ["SEGMENT_LIVE_GOLDEN_BASE"] = previous_live


if __name__ == "__main__":
    unittest.main()
