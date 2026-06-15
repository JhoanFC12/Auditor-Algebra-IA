from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modulos.instance_factory.training_registry import load_training_cycle_status, start_new_training_cycle, task_by_key


class TrainingRegistryTests(unittest.TestCase):
    def test_training_cycle_counts_existing_banks_against_500_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_manifest(root / "normalizer_training_bank", {"samples_total": 300, "threshold": 200})
            self._write_manifest(root / "segment_training_live", {"corrected_images": 43, "target_corrected_images": 200})
            self._write_manifest(root / "ocr_golden_live", {"records_corrected": 148, "records_total": 3849})
            self._write_manifest(root / "ocr_geometry_golden_live", {"records_corrected": 333, "records_total": 333})
            self._write_manifest(root / "problem_detector_corrections_live", {"samples_total": 4})
            self._write_manifest(root / "pdf_problem_boxes_live" / "piloto", {"pages_total": 715, "boxes_total": 4970})

            with patch.dict(
                os.environ,
                {
                    "TRAINING_DATASETS_ROOT": str(root),
                    "TRAINING_SAMPLE_TARGET": "500",
                    "NORMALIZER_TRAINING_BANK_ROOT": "",
                    "SEGMENT_LIVE_GOLDEN_BASE": "",
                    "OCR_TRAINING_BANK_ROOTS": "",
                    "PDF_PROBLEM_DETECTOR_CORRECTIONS_ROOT": "",
                },
            ):
                status = load_training_cycle_status()

            self.assertEqual(status["schema_version"], "pdf_factory_training_cycle_status_v1")
            self.assertEqual(status["target_per_model"], 500)

            normalizer = task_by_key(status, "normalizer")
            self.assertEqual(normalizer["samples_total"], 300)
            self.assertEqual(normalizer["target_samples"], 500)
            self.assertEqual(normalizer["remaining_samples"], 200)
            self.assertFalse(normalizer["ready_to_train"])

            ocr = task_by_key(status, "ocr_raw")
            self.assertEqual(ocr["samples_total"], 481)
            self.assertEqual(ocr["remaining_samples"], 19)
            self.assertFalse(ocr["ready_to_train"])

            figure = task_by_key(status, "figure_segmenter")
            self.assertEqual(figure["samples_total"], 43)
            self.assertEqual(figure["remaining_samples"], 457)

            detector = task_by_key(status, "problem_detector")
            self.assertEqual(detector["samples_total"], 719)
            self.assertTrue(detector["ready_to_train"])

    def test_start_new_cycle_resets_visible_counts_but_keeps_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_manifest(root / "normalizer_training_bank", {"samples_total": 300})
            self._write_manifest(root / "segment_training_live", {"corrected_images": 43})
            self._write_manifest(root / "ocr_golden_live", {"records_corrected": 148})
            self._write_manifest(root / "ocr_geometry_golden_live", {"records_corrected": 333})
            self._write_manifest(root / "pdf_problem_boxes_live" / "piloto", {"pages_total": 715})

            with patch.dict(
                os.environ,
                {
                    "TRAINING_DATASETS_ROOT": str(root),
                    "TRAINING_SAMPLE_TARGET": "500",
                    "NORMALIZER_TRAINING_BANK_ROOT": "",
                    "SEGMENT_LIVE_GOLDEN_BASE": "",
                    "OCR_TRAINING_BANK_ROOTS": "",
                    "PDF_PROBLEM_DETECTOR_CORRECTIONS_ROOT": "",
                },
            ):
                status = start_new_training_cycle(reason="normalizer v1 submitted")

            self.assertTrue((root / "training_cycle_state.json").exists())
            self.assertTrue(status["cycle"]["cycle_id"])

            normalizer = task_by_key(status, "normalizer")
            self.assertEqual(normalizer["samples_total"], 0)
            self.assertEqual(normalizer["historical_samples_total"], 300)
            self.assertEqual(normalizer["cycle_baseline_samples"], 300)
            self.assertEqual(normalizer["remaining_samples"], 500)

            ocr = task_by_key(status, "ocr_raw")
            self.assertEqual(ocr["samples_total"], 0)
            self.assertEqual(ocr["historical_samples_total"], 481)

            detector = task_by_key(status, "problem_detector")
            self.assertEqual(detector["samples_total"], 0)
            self.assertEqual(detector["historical_samples_total"], 715)

    @staticmethod
    def _write_manifest(root: Path, payload: dict[str, object]) -> None:
        root.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": "test_manifest_v1", **payload}
        (root / "manifest.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
