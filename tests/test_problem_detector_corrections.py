from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from modulos.instance_factory.models import InstancePipelineContext
from modulos.instance_factory.pipeline import InstancePdfPipelineService
from modulos.instance_factory.problem_detector_corrections import maybe_write_problem_detector_correction
from modulos.instance_factory.staging import InstanceStagingStore


class ProblemDetectorCorrectionTests(unittest.TestCase):
    def test_writes_yolo_dataset_when_boxes_change_significantly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page = root / "page.png"
            Image.new("RGB", (200, 100), "white").save(page)
            context = InstancePipelineContext(
                book_code="GEO01",
                instance_type="semana_01",
                project_name="Geometria",
                pdf_path="E:/Banco/libro.pdf",
            )

            result = maybe_write_problem_detector_correction(
                context=context,
                page_record_id="page_001",
                page_number=1,
                page_image=page,
                pdf_path="E:/Banco/libro.pdf",
                detector_source="pdf_factory:Jhoan12/pdf-problem-detector-yolov8n-v4",
                layout_mode="una_columna",
                previous_boxes=[(10, 20, 100, 80)],
                human_boxes=[(12, 22, 110, 82)],
                root=root / "problem_detector_corrections",
            )

            self.assertTrue(result["saved"])
            metadata = json.loads(Path(result["metadata_path"]).read_text(encoding="utf-8"))
            self.assertEqual(metadata["schema_version"], "problem_detector_correction_v1")
            self.assertEqual(metadata["book_code"], "GEO01")
            self.assertEqual(metadata["instance_type"], "semana_01")
            self.assertEqual(metadata["model_boxes"][0]["xyxy"], [10, 20, 100, 80])
            self.assertEqual(metadata["human_boxes"][0]["xyxy"], [12, 22, 110, 82])
            self.assertEqual(metadata["change_summary"]["moved_or_resized"], 1)
            self.assertEqual(metadata["excluded_future_scope"], ["problem_vs_solution_classification"])
            label = Path(result["label_path"]).read_text(encoding="utf-8").strip()
            parts = label.split()
            self.assertEqual(parts[0], "0")
            self.assertEqual(len(parts), 5)
            manifest = json.loads((root / "problem_detector_corrections" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["samples_total"], 1)

    def test_skips_small_coordinate_nudge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page = root / "page.png"
            Image.new("RGB", (200, 100), "white").save(page)
            context = InstancePipelineContext(book_code="GEO01", instance_type="semana_01")

            result = maybe_write_problem_detector_correction(
                context=context,
                page_record_id="page_001",
                page_number=1,
                page_image=page,
                pdf_path="",
                detector_source="pdf_factory:test",
                layout_mode="una_columna",
                previous_boxes=[(10, 20, 100, 80)],
                human_boxes=[(11, 21, 101, 81)],
                root=root / "problem_detector_corrections",
            )

            self.assertFalse(result["saved"])
            self.assertFalse((root / "problem_detector_corrections").exists())

    def test_pipeline_update_page_boxes_captures_correction_dataset(self) -> None:
        class FakeGolden:
            def __init__(self, page_image: Path) -> None:
                self.rows = [
                    SimpleNamespace(
                        record_id="page_001",
                        page_number=1,
                        boxes=[(10, 20, 100, 80)],
                        reviewed=False,
                        layout_mode="una_columna",
                        detector_source="pdf_factory:test-model",
                        image_path=page_image,
                        pdf_path="E:/Banco/libro.pdf",
                    )
                ]

            def load_instance(self, _name: str):
                return self.rows

            def upsert_instance_rows(self, _name: str, rows):
                self.rows = list(rows)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            page = root / "page.png"
            Image.new("RGB", (200, 100), "white").save(page)
            context = InstancePipelineContext(
                book_code="GEO01",
                instance_type="semana_01",
                pdf_path="E:/Banco/libro.pdf",
                workspace_dir=str(workspace),
            )
            store = InstanceStagingStore(context, root=root / "staging")
            service = InstancePdfPipelineService(context, golden_controller=FakeGolden(page), staging_store=store)

            service.update_page_boxes("page_001", [[12, 22, 110, 82]], layout_mode="una_columna", reviewed=True)

            corrections = workspace / "temporales" / "semana_01" / "datasets" / "problem_detector_corrections"
            metadata_files = list((corrections / "metadata").glob("*.json"))
            label_files = list((corrections / "labels").glob("*.txt"))
            image_files = list((corrections / "images").glob("*.png"))
            self.assertEqual(len(metadata_files), 1)
            self.assertEqual(len(label_files), 1)
            self.assertEqual(len(image_files), 1)
            metadata = json.loads(metadata_files[0].read_text(encoding="utf-8"))
            self.assertEqual(metadata["model_name"], "test-model")
            self.assertEqual(metadata["baseline_reviewed_before"], False)
            self.assertEqual(metadata["human_boxes"][0]["order"], 1)


if __name__ == "__main__":
    unittest.main()
