from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from modulos.instance_factory.models import InstancePipelineContext, PipelineStep, StageStatus, StagingProblemRecord
from modulos.instance_factory.pipeline import InstancePdfPipelineService
from modulos.instance_factory.staging import InstanceStagingStore


class InstanceFactoryStaleArtifactTests(unittest.TestCase):
    def test_box_change_invalidates_downstream_artifacts_and_blocks_ocr_edit(self):
        class FakeGolden:
            def __init__(self, page_image: Path) -> None:
                self.row = SimpleNamespace(
                    record_id="page_001",
                    page_number=1,
                    boxes=[(1, 2, 30, 40)],
                    reviewed=True,
                    layout_mode="una_columna",
                    detector_source="pdf_factory:test",
                    image_path=page_image,
                    pdf_path="book.pdf",
                    box_details=[],
                    detector_detections=[],
                )

            def load_instance(self, _name: str):
                return [self.row]

            def upsert_instance_rows(self, _name: str, rows):
                self.row = rows[0]

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            page = root / "page.png"
            crop = root / "crop.png"
            page.write_bytes(b"fake")
            crop.write_bytes(b"fake")
            context = InstancePipelineContext(book_code="LIB", instance_type="s01", pdf_path="book.pdf")
            store = InstanceStagingStore(context, root=root / "staging")
            record = StagingProblemRecord(
                record_id="crop_001",
                crop_id="crop_001",
                crop_path=str(crop),
                status=StageStatus.READY,
                source={
                    "page_number": 1,
                    "source_record_id": "page_001",
                    "bbox_px": [1, 2, 30, 40],
                },
                raw_ocr="<01.> OCR",
                structured_ocr={"items_total": 1},
                figure_segmentation={"segments_total": 1},
                normalized={"numero": "1"},
                review={"final_latex": "item"},
                artifacts={"raw": "old"},
                golden_sync={"status": "prepared"},
                errors=["old error"],
            )
            for step in PipelineStep.ORDER:
                record.set_step(step, StageStatus.READY, "listo")
            store.upsert_record(record)
            service = InstancePdfPipelineService(context, golden_controller=FakeGolden(page), staging_store=store)

            service.update_page_boxes("page_001", [[5, 6, 45, 55]], layout_mode="una_columna")
            loaded = store.get_record("crop_001")
            assert loaded is not None

            with self.assertRaises(ValueError):
                service.update_raw_ocr("crop_001", "<01.> OCR corregido")

        self.assertEqual(loaded.crop_path, "")
        self.assertEqual(loaded.raw_ocr, "")
        self.assertEqual(loaded.figure_segmentation, {})
        self.assertEqual(loaded.normalized, {})
        self.assertEqual(loaded.review, {})
        self.assertEqual(loaded.audit["downstream_state"]["status"], "invalidated")
        self.assertEqual(loaded.step_status(PipelineStep.OCR), StageStatus.PENDING)


if __name__ == "__main__":
    unittest.main()
