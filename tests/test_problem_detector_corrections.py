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
            self.assertEqual(manifest["revision_events_total"], 1)

    def test_recorrection_updates_current_target_and_keeps_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page = root / "page.png"
            Image.new("RGB", (200, 100), "white").save(page)
            context = InstancePipelineContext(book_code="GEO01", instance_type="semana_01")
            dataset_root = root / "problem_detector_corrections"

            first = maybe_write_problem_detector_correction(
                context=context,
                page_record_id="page_001",
                page_number=1,
                page_image=page,
                pdf_path="",
                detector_source="pdf_factory:test",
                layout_mode="una_columna",
                previous_boxes=[(10, 20, 100, 80)],
                human_boxes=[(12, 22, 110, 82)],
                root=dataset_root,
            )
            second = maybe_write_problem_detector_correction(
                context=context,
                page_record_id="page_001",
                page_number=1,
                page_image=page,
                pdf_path="",
                detector_source="pdf_factory:test",
                layout_mode="una_columna",
                previous_boxes=[(12, 22, 110, 82)],
                human_boxes=[(15, 25, 120, 88)],
                root=dataset_root,
            )

            self.assertTrue(first["saved"])
            self.assertTrue(second["saved"])
            metadata = json.loads(Path(second["metadata_path"]).read_text(encoding="utf-8"))
            self.assertEqual(metadata["revision_count"], 2)
            self.assertEqual(metadata["human_boxes"][0]["xyxy"], [15, 25, 120, 88])
            self.assertEqual(metadata["correction_history"][0]["human_boxes"][0]["xyxy"], [12, 22, 110, 82])
            manifest = json.loads((dataset_root / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["samples_total"], 1)
            self.assertEqual(manifest["revision_events_total"], 2)

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

    def test_pipeline_update_page_boxes_persists_multiclass_auxiliary_boxes(self) -> None:
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
                        box_details=[
                            {"bbox_px": [10, 20, 100, 80], "class_name": "problem", "class_key": "problem"},
                        ],
                        detector_detections=[
                            {"bbox_px": [10, 20, 100, 80], "class_name": "problem", "class_key": "problem"},
                            {"bbox_px": [12, 22, 32, 34], "class_name": "problem_number", "class_key": "problem_number"},
                            {"bbox_px": [20, 65, 95, 78], "class_name": "answer_block", "class_key": "answer_block"},
                        ],
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
            golden = FakeGolden(page)
            service = InstancePdfPipelineService(context, golden_controller=golden, staging_store=InstanceStagingStore(context, root=root / "staging"))

            service.update_page_boxes(
                "page_001",
                [[10, 20, 100, 80]],
                detector_detections=[
                    {"bbox_px": [20, 28, 44, 42], "class_name": "problem_number"},
                    {"bbox_px": [30, 70, 120, 90], "class_name": "answer_block"},
                ],
                layout_mode="una_columna",
                reviewed=True,
            )

            row = golden.rows[0]
            self.assertEqual(row.boxes, [(10, 20, 100, 80)])
            self.assertEqual([item["class_key"] for item in row.detector_detections], ["problem", "problem_number", "answer_block"])
            self.assertEqual(row.detector_detections[1]["bbox_px"], [20, 28, 44, 42])
            self.assertEqual(row.detector_detections[2]["bbox_px"], [30, 70, 120, 90])
            corrections = workspace / "temporales" / "semana_01" / "datasets" / "problem_detector_corrections"
            label = next((corrections / "labels").glob("*.txt")).read_text(encoding="utf-8")
            self.assertIn("\n1 ", "\n" + label)
            self.assertIn("\n2 ", "\n" + label)

    def test_pipeline_can_capture_reviewed_pages_for_training_without_box_delta(self) -> None:
        class FakeGolden:
            def __init__(self, page_image: Path) -> None:
                self.rows = [
                    SimpleNamespace(
                        record_id="page_001",
                        page_number=1,
                        boxes=[(10, 20, 100, 80)],
                        reviewed=True,
                        layout_mode="una_columna",
                        detector_source="pdf_factory:test-model",
                        image_path=page_image,
                        pdf_path="E:/Banco/libro.pdf",
                        box_details=[
                            {"bbox_px": [10, 20, 100, 80], "class_name": "problem", "class_key": "problem"},
                        ],
                        detector_detections=[
                            {"bbox_px": [10, 20, 100, 80], "class_name": "problem", "class_key": "problem"},
                            {"bbox_px": [12, 22, 32, 34], "class_name": "problem_number", "class_key": "problem_number"},
                            {"bbox_px": [20, 65, 95, 78], "class_name": "answer_block", "class_key": "answer_block"},
                        ],
                    ),
                    SimpleNamespace(
                        record_id="page_002",
                        page_number=2,
                        boxes=[(5, 10, 60, 50)],
                        reviewed=False,
                        layout_mode="una_columna",
                        detector_source="pdf_factory:test-model",
                        image_path=page_image,
                        pdf_path="E:/Banco/libro.pdf",
                    ),
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
            service = InstancePdfPipelineService(
                context,
                golden_controller=FakeGolden(page),
                staging_store=InstanceStagingStore(context, root=root / "staging"),
            )

            result = service.capture_problem_detector_training_pages(reviewed_only=True)

            self.assertEqual(result["saved"], 1)
            self.assertEqual(result["skipped"], 1)
            corrections = workspace / "temporales" / "semana_01" / "datasets" / "problem_detector_corrections"
            metadata_files = list((corrections / "metadata").glob("*.json"))
            self.assertEqual(len(metadata_files), 1)
            metadata = json.loads(metadata_files[0].read_text(encoding="utf-8"))
            self.assertEqual(metadata["forced_training_capture"], True)
            self.assertEqual(metadata["capture_reason"], "manual_reviewed_pages_batch")
            self.assertEqual(metadata["training_target"], "pdf_problem_detector_yolov8_multiclass_boxes")
            label = next((corrections / "labels").glob("*.txt")).read_text(encoding="utf-8")
            self.assertIn("\n0 ", "\n" + label)
            self.assertIn("\n1 ", "\n" + label)
            self.assertIn("\n2 ", "\n" + label)


if __name__ == "__main__":
    unittest.main()
