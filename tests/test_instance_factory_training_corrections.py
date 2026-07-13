from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from modulos.instance_factory.models import InstancePipelineContext, StagingProblemRecord
from modulos.instance_factory.training_bank import (
    persist_figure_segment_correction,
    persist_problem_detector_correction,
    persist_raw_ocr_correction,
)


class InstanceFactoryTrainingCorrectionTests(unittest.TestCase):
    def test_training_bank_persists_problem_detector_raw_ocr_and_figure_corrections(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image = root / "crop.png"
            page = root / "page.png"
            Image.new("RGB", (200, 120), "white").save(image)
            Image.new("RGB", (400, 600), "white").save(page)
            context = InstancePipelineContext(book_code="LIB", instance_type="s01", project_name="Proyecto")
            record = StagingProblemRecord(
                record_id="crop_001",
                crop_id="crop_001",
                crop_path=str(image),
                raw_ocr="<01.> OCR modelo",
                source={"page_number": 1, "bbox_px": [10, 20, 110, 80]},
                models={"ocr": "hf-test", "figure_segmenter": "fig-test"},
            )

            detector = persist_problem_detector_correction(
                context=context,
                page_record_id="page_001",
                page_number=1,
                page_image=page,
                pdf_path="book.pdf",
                detector_source="pdf_factory:test",
                layout_mode="auto",
                previous_boxes=[],
                human_boxes=[[10, 20, 200, 160]],
                force=True,
                root=root / "problem_detector_corrections",
            )
            ocr = persist_raw_ocr_correction(
                context,
                record,
                corrected_text="<01.> OCR corregido",
                previous_text="<01.> OCR modelo",
                root=root / "ocr_golden_live",
            )
            figure = persist_figure_segment_correction(
                context,
                record,
                boxes=[[20, 30, 140, 100]],
                detector_payload={"detector_source": "model:test"},
                root=root / "segment_training_live",
            )

            detector_manifest = json.loads(Path(detector["manifest_path"]).read_text(encoding="utf-8"))
            ocr_manifest = json.loads(Path(ocr["manifest_path"]).read_text(encoding="utf-8"))
            figure_manifest = json.loads(Path(figure["manifest_path"]).read_text(encoding="utf-8"))
            figure_label_text = Path(figure["label_path"]).read_text(encoding="utf-8")

        self.assertTrue(detector["saved"])
        self.assertEqual(detector_manifest["samples_total"], 1)
        self.assertTrue(ocr["saved"])
        self.assertEqual(ocr_manifest["records_corrected"], 1)
        self.assertTrue(figure["saved"])
        self.assertEqual(figure_manifest["corrected_images"], 1)
        self.assertTrue(figure_label_text.startswith("0 "))


if __name__ == "__main__":
    unittest.main()
