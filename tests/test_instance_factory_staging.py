from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from modulos.instance_factory.model_inventory import build_model_inventory_manifest, resolve_model_defaults
from modulos.instance_factory.models import InstancePipelineContext, PipelineStep, StageStatus, StagingProblemRecord
from modulos.instance_factory.page_selection import parse_page_selection
from modulos.instance_factory.staging import InstanceStagingStore


class InstanceFactoryStagingTests(unittest.TestCase):
    def test_staging_upsert_is_idempotent_and_rewrites_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(
                book_code="ALG01",
                instance_type="s01",
                project_name="Algebra",
                pdf_path="E:/Banco/libro.pdf",
                workspace_dir="",
            )
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            record = StagingProblemRecord(
                record_id="crop_001",
                crop_id="crop_001",
                crop_path=str(Path(tmp) / "crop.png"),
                status=StageStatus.PENDING,
                source={"page_number": 3, "bbox_px": [1, 2, 30, 40]},
            )

            store.upsert_record(record)
            record.status = StageStatus.NEEDS_REVIEW
            store.upsert_record(record)

            rows = store.load_records()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].record_id, "crop_001")
            self.assertEqual(rows[0].status, StageStatus.NEEDS_REVIEW)

            manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], "pdf_factory_staging_v1")
            self.assertEqual(manifest["contract_version"], "pdf_factory_instance_pipeline_v2")
            self.assertEqual(manifest["records_total"], 1)
            self.assertTrue(manifest["policy"]["never_insert_directly_into_problemas"])
            self.assertFalse(manifest["policy"]["promotion_boundary"]["enabled"])
            self.assertEqual(manifest["policy"]["promotion_boundary"]["write_operations"], [])
            self.assertEqual(manifest["contract"]["contract_version"], "pdf_factory_instance_pipeline_v2")
            self.assertEqual(
                manifest["contract"]["ordered_steps"],
                [
                    PipelineStep.PAGES,
                    PipelineStep.BOXES,
                    PipelineStep.CROPS,
                    PipelineStep.SEGMENTATION,
                    PipelineStep.OCR,
                    PipelineStep.NORMALIZATION,
                    PipelineStep.REVIEW,
                ],
            )
            self.assertFalse(manifest["contract_validation"]["valid"])
            self.assertEqual(
                manifest["contract_validation"]["issues"][0]["issue"],
                "metadata_minima_incomplete",
            )
            self.assertIn("metadata", manifest)
            self.assertIn("evaluation_matrix", manifest)
            self.assertIn("ocr", manifest["evaluation_matrix"]["stages"])
            self.assertIn("model_inventory", manifest)
            self.assertEqual(
                manifest["training_contracts"]["human_review_training_example_schema"],
                "human_review_training_example_v1",
            )

    def test_record_steps_are_normalized_and_manifest_counts_by_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            crop_path = Path(tmp) / "crop.png"
            crop_path.write_bytes(b"fake")
            record = StagingProblemRecord(
                record_id="crop_001",
                crop_id="crop_001",
                crop_path=str(crop_path),
                status="revision_humana",
                source={"page_number": 1, "bbox_px": [1, 2, 30, 40]},
            )
            record.set_step("pages", "ready", "pagina resuelta")
            record.set_step("crop", StageStatus.READY, "crop disponible")

            store.upsert_record(record)

            loaded = store.get_record("crop_001")
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.status, StageStatus.READY)
            self.assertEqual(loaded.step_status(PipelineStep.PAGES), StageStatus.READY)
            self.assertEqual(loaded.step_status(PipelineStep.CROPS), StageStatus.READY)

            manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["by_status"][StageStatus.READY], 1)
            self.assertEqual(manifest["by_step_status"][PipelineStep.PAGES][StageStatus.READY], 1)
            self.assertEqual(manifest["by_step_status"][PipelineStep.CROPS][StageStatus.READY], 1)

    def test_review_update_preserves_normalized_form_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="GEO", instance_type="s02")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            store.upsert_record(
                StagingProblemRecord(
                    record_id="crop_002",
                    crop_id="crop_002",
                    crop_path="crop.png",
                    raw_ocr="12. Halle x. A) 1 B) 2 C) 3 D) 4 E) 5",
                )
            )

            updated = store.update_review(
                "crop_002",
                {
                    "numero": "12",
                    "enunciado_latex": "Halle x.",
                    "alternativas": {"A": "1", "B": "2", "C": "3", "D": "4", "E": "5"},
                },
                notes="corregido",
            )

            self.assertEqual(updated.status, StageStatus.NEEDS_REVIEW)
            self.assertEqual(updated.step_status(PipelineStep.REVIEW), StageStatus.NEEDS_REVIEW)
            self.assertEqual(updated.normalized["numero"], "12")
            self.assertEqual(updated.review["notes"], "corregido")
            self.assertEqual(updated.review["training_examples_total"], 1)
            self.assertEqual(updated.training_examples[0]["schema_version"], "human_review_training_example_v1")
            self.assertEqual(updated.training_examples[0]["human_normalized"]["numero"], "12")
            self.assertIn("latest_review", updated.artifacts)
            review_artifact = json.loads(Path(updated.artifacts["latest_review"]).read_text(encoding="utf-8"))
            self.assertEqual(review_artifact["schema_version"], "pdf_factory_review_artifact_v1")
            self.assertEqual(review_artifact["training_examples"][0]["human_normalized"]["numero"], "12")
            self.assertEqual(updated.golden_sync["status"], "contract_prepared")
            contract_path = Path(updated.golden_sync["contract_path"])
            self.assertTrue(contract_path.exists())
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            self.assertEqual(contract["schema_version"], "pdf_factory_golden_contract_v1")
            self.assertEqual(contract["raw_ocr"], "12. Halle x. A) 1 B) 2 C) 3 D) 4 E) 5")
            self.assertIn("Halle x.", contract["corrected_text"])

    def test_review_update_can_mark_record_ready_without_inserting_problems(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="GEO", instance_type="s03")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            crop = Path(tmp) / "crop.png"
            crop.write_bytes(b"png")
            store.upsert_record(
                StagingProblemRecord(
                    record_id="crop_003",
                    crop_id="crop_003",
                    crop_path=str(crop),
                    raw_ocr="texto",
                    figure_segmentation={"segments_total": 2},
                    normalized={"numero": "1"},
                )
            )

            updated = store.update_review("crop_003", {"numero": "1"}, mark_ready=True)
            summary = store.summarize_records()
            manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(updated.status, StageStatus.READY)
            self.assertEqual(updated.review["review_status"], StageStatus.READY)
            self.assertEqual(updated.step_status(PipelineStep.REVIEW), StageStatus.READY)
            self.assertEqual(summary["ready"], 1)
            self.assertEqual(summary["crops_found"], 1)
            self.assertTrue(manifest["policy"]["never_insert_directly_into_problemas"])
            candidate = store.build_promotion_candidate("crop_003")
            self.assertFalse(candidate["promotion_enabled"])
            self.assertIsNone(candidate["sql"])
            self.assertEqual(candidate["write_operations"], [])
            self.assertTrue(candidate["policy"]["never_insert_directly_into_problemas"])

    def test_upsert_many_coalesces_duplicate_source_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="s04", pdf_path="E:/Banco/libro.pdf")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            source = {
                "book_code": "ALG01",
                "instance_type": "s04",
                "pdf_path": "E:/Banco/libro.pdf",
                "page_number": 9,
                "bbox_px": [10, 20, 110, 220],
            }

            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="crop_a",
                        crop_id="crop_a",
                        crop_path=str(Path(tmp) / "a.png"),
                        source=source,
                        models={"pdf_detector": "m1"},
                        confidence={"pdf_box": 0.71},
                    ),
                    StagingProblemRecord(
                        record_id="crop_b",
                        crop_id="crop_b",
                        crop_path=str(Path(tmp) / "b.png"),
                        source=dict(source),
                        models={"pdf_detector": "m1"},
                        confidence={"pdf_box": 0.72},
                    ),
                ]
            )

            rows = store.load_records()
            manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 1)
            self.assertEqual(manifest["records_total"], 1)
            self.assertEqual(manifest["metadata"]["duplicate_identity_total"], 0)

    def test_manifest_repair_removes_legacy_duplicate_identity_records_preserving_review_training(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="s04", pdf_path="E:/Banco/libro.pdf")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            source = {
                "book_code": "ALG01",
                "instance_type": "s04",
                "pdf_path": "E:/Banco/libro.pdf",
                "page_number": 9,
                "bbox_px": [10, 20, 110, 220],
            }
            primary = StagingProblemRecord(
                record_id="crop_legacy_a",
                crop_id="crop_legacy_a",
                crop_path=str(Path(tmp) / "a.png"),
                source=dict(source),
                models={"pdf_detector": "m1"},
                confidence={"pdf_box": 0.71},
            )
            duplicate = StagingProblemRecord(
                record_id="crop_legacy_b",
                crop_id="crop_legacy_b",
                crop_path=str(Path(tmp) / "b.png"),
                status=StageStatus.READY,
                source=dict(source),
                models={"pdf_detector": "m1"},
                confidence={"pdf_box": 0.72},
                normalized={"numero": "9"},
                review={"review_status": StageStatus.READY, "notes": "validado"},
                training_examples=[
                    {
                        "schema_version": "human_review_training_example_v1",
                        "human_normalized": {"numero": "9"},
                    }
                ],
            )
            (store.records_dir / "crop_legacy_a.json").write_text(
                json.dumps(primary.to_dict(), ensure_ascii=False),
                encoding="utf-8",
            )
            duplicate_path = store.records_dir / "crop_legacy_b.json"
            duplicate_path.write_text(json.dumps(duplicate.to_dict(), ensure_ascii=False), encoding="utf-8")

            store.rewrite_manifest()

            rows = store.load_records()
            manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 1)
            self.assertFalse(duplicate_path.exists())
            self.assertEqual(rows[0].status, StageStatus.READY)
            self.assertEqual(rows[0].normalized["numero"], "9")
            self.assertEqual(rows[0].review["notes"], "validado")
            self.assertEqual(rows[0].training_examples[0]["human_normalized"]["numero"], "9")
            self.assertEqual(manifest["metadata"]["duplicate_records_repaired"], 1)
            self.assertEqual(manifest["metadata"]["duplicate_identity_total"], 0)

    def test_invalid_status_is_rejected_before_record_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="s04")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")

            with self.assertRaises(ValueError):
                store.upsert_record(
                    StagingProblemRecord(
                        record_id="crop_bad",
                        crop_id="crop_bad",
                        crop_path=str(Path(tmp) / "bad.png"),
                        status="estado_fantasma",
                    )
                )

            self.assertEqual(store.load_records(), [])

    def test_context_can_be_created_from_biblioteca_instance_payload(self) -> None:
        book = {
            "id": 42,
            "codigo": "ALG01",
            "titulo": "Algebra",
            "pdf_path": "E:/Banco/libro.pdf",
            "workspace_dir": "E:/Banco/ALG01",
        }
        item = {"tipo": "S05", "session_path": "E:/Banco/ALG01/sessions/S05.json"}

        context = InstancePipelineContext.from_library_instance(book, item, db_name="demo")

        self.assertEqual(context.book_code, "ALG01")
        self.assertEqual(context.instance_type, "S05")
        self.assertEqual(context.project_name, "Algebra")
        self.assertEqual(context.db_name, "demo")
        self.assertEqual(context.book_id, 42)
        self.assertTrue(context.pdf_path.endswith("libro.pdf"))

    def test_pipeline_materialization_writes_required_staging_metadata(self) -> None:
        try:
            from modulos.instance_factory.pipeline import InstancePdfPipelineService
        except Exception as exc:  # pragma: no cover - optional detector/OCR dependencies.
            self.skipTest(f"pipeline deps unavailable: {exc}")

        class FakeGolden:
            def __init__(self, root: Path) -> None:
                self.root = root

            def materialize_problem_crops_for_downstream(self, *_args, **_kwargs):
                target = self.root / "problem_crops_live"
                records = target / "records"
                images = target / "images"
                records.mkdir(parents=True, exist_ok=True)
                images.mkdir(parents=True, exist_ok=True)
                crop_id = "crop_pipeline_001"
                (images / f"{crop_id}.png").write_bytes(b"png")
                payload = {
                    "schema_version": "problem_crop_live_v1",
                    "crop_id": crop_id,
                    "book_code": "ALG01",
                    "instance_type": "s05",
                    "source_pdf_path": "E:/Banco/libro.pdf",
                    "source_page_number": 4,
                    "source_page_image": "page.png",
                    "bbox_px": [1, 2, 30, 40],
                    "crop_image_rel": f"images/{crop_id}.png",
                    "source_record_id": "page_0004",
                    "layout_mode": "una_columna",
                }
                (records / f"{crop_id}.json").write_text(json.dumps(payload), encoding="utf-8")
                return target, [crop_id]

        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="s05", pdf_path="E:/Banco/libro.pdf")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            service = InstancePdfPipelineService(context, golden_controller=FakeGolden(Path(tmp)), staging_store=store)

            records = service.materialize_crops_to_staging(rows=[])

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].source["book_code"], "ALG01")
            self.assertEqual(records[0].source["page_number"], 4)
            self.assertEqual(records[0].source["bbox_px"], [1, 2, 30, 40])
            self.assertEqual(records[0].source["crop_id"], "crop_pipeline_001")
            for stage in ("pdf_detector", "ocr", "figure_segmenter", "normalizer"):
                trace = records[0].models["stages"][stage]
                self.assertTrue(trace["model_id"])
                self.assertTrue(trace["provider"])
                self.assertTrue(trace["version"])
                self.assertIn("fallback", trace)
            manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["metadata"]["complete_records"], 1)
            self.assertTrue(manifest["contract_validation"]["valid"])

    def test_pipeline_dashboard_overviews_expose_stage_state(self) -> None:
        try:
            from types import SimpleNamespace

            from modulos.instance_factory.pipeline import InstancePdfPipelineService
        except Exception as exc:  # pragma: no cover - optional detector/OCR dependencies.
            self.skipTest(f"pipeline deps unavailable: {exc}")

        class FakeGolden:
            def __init__(self, page_image: Path) -> None:
                self.page_image = page_image

            def load_instance(self, _name: str):
                return [
                    SimpleNamespace(
                        record_id="page_001",
                        page_number=1,
                        boxes=[(1, 2, 30, 40), (2, 50, 30, 80)],
                        reviewed=True,
                        layout_mode="una_columna",
                        detector_source="test_detector",
                        image_path=self.page_image,
                    )
                ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = root / "crop.png"
            crop.write_bytes(b"png")
            page = root / "page.png"
            page.write_bytes(b"png")
            context = InstancePipelineContext(book_code="ALG01", instance_type="s06", pdf_path="E:/Banco/libro.pdf")
            store = InstanceStagingStore(context, root=root / "staging")
            record = StagingProblemRecord(
                record_id="crop_001",
                crop_id="crop_001",
                crop_path=str(crop),
                status=StageStatus.NEEDS_REVIEW,
                source={"page_number": 1, "bbox_px": [1, 2, 30, 40]},
                structured_ocr={"items_total": 1, "items": [{"item": {"n": 7, "statement": "Halle x"}}]},
                figure_segmentation={"segments_total": 2},
                normalized={"numero": "7"},
            )
            record.set_step(PipelineStep.CROPS, StageStatus.READY, "crop disponible")
            record.set_step(PipelineStep.OCR, StageStatus.READY, "OCR estructurado")
            record.set_step(PipelineStep.SEGMENTATION, StageStatus.NEEDS_REVIEW, "segmentos detectados")
            record.set_step(PipelineStep.NORMALIZATION, StageStatus.NEEDS_REVIEW, "normalizado")
            store.upsert_record(record)
            service = InstancePdfPipelineService(context, golden_controller=FakeGolden(page), staging_store=store)

            summary = service.build_instance_summary()
            page_rows = service.build_page_box_overview()
            stage_rows = service.build_record_stage_rows()

            self.assertEqual(summary["pages_total"], 1)
            self.assertEqual(summary["boxes_total"], 2)
            self.assertEqual(summary["crops_found"], 1)
            self.assertEqual(page_rows[0]["status"], StageStatus.READY)
            self.assertEqual(stage_rows[0]["ocr_items"], 1)
            self.assertEqual(stage_rows[0]["segments_total"], 2)
            self.assertEqual(stage_rows[0]["steps"][PipelineStep.OCR], StageStatus.READY)

    def test_run_instance_pipeline_materializes_only_to_staging(self) -> None:
        try:
            from modulos.instance_factory.pipeline import InstancePdfPipelineService
        except Exception as exc:  # pragma: no cover - optional detector/OCR dependencies.
            self.skipTest(f"pipeline deps unavailable: {exc}")

        class FakeGolden:
            def __init__(self, root: Path, *, missing_crop: bool = False) -> None:
                self.root = root
                self.missing_crop = missing_crop

            def load_instance(self, _name):
                return []

            def materialize_problem_crops_for_downstream(self, *_args, **_kwargs):
                target = self.root / "problem_crops_live"
                records = target / "records"
                images = target / "images"
                records.mkdir(parents=True, exist_ok=True)
                images.mkdir(parents=True, exist_ok=True)
                crop_id = "crop_pipeline_run_001"
                if not self.missing_crop:
                    (images / f"{crop_id}.png").write_bytes(b"png")
                payload = {
                    "schema_version": "problem_crop_live_v1",
                    "crop_id": crop_id,
                    "book_code": "ALG01",
                    "instance_type": "s06",
                    "source_pdf_path": "E:/Banco/libro.pdf",
                    "source_page_number": 8,
                    "source_page_image": "page.png",
                    "bbox_px": [10, 20, 110, 220],
                    "crop_image_rel": f"images/{crop_id}.png",
                    "source_record_id": "page_0008",
                    "layout_mode": "una_columna",
                }
                (records / f"{crop_id}.json").write_text(json.dumps(payload), encoding="utf-8")
                return target, [crop_id]

        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="s06", pdf_path="E:/Banco/libro.pdf")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            service = InstancePdfPipelineService(context, golden_controller=FakeGolden(Path(tmp)), staging_store=store)

            report = service.run_instance_pipeline(materialize=True, run_ocr=False)

            rows = store.load_records()
            manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(report["schema_version"], "instance_pdf_pipeline_run_v1")
            self.assertEqual(report["model_inventory"]["schema_version"], "pdf_factory_model_inventory_manifest_v1")
            self.assertEqual(report["policy"]["target"], "staging_only")
            self.assertTrue(report["policy"]["never_insert_directly_into_problemas"])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].status, StageStatus.PENDING)
            self.assertEqual(rows[0].step_status(PipelineStep.PAGES), StageStatus.READY)
            self.assertEqual(rows[0].step_status(PipelineStep.BOXES), StageStatus.READY)
            self.assertEqual(rows[0].step_status(PipelineStep.CROPS), StageStatus.READY)
            self.assertEqual(rows[0].step_status(PipelineStep.OCR), StageStatus.PENDING)
            self.assertEqual(manifest["policy"]["promotion_boundary"]["write_operations"], [])

        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="s06", pdf_path="E:/Banco/libro.pdf")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            service = InstancePdfPipelineService(
                context,
                golden_controller=FakeGolden(Path(tmp), missing_crop=True),
                staging_store=store,
            )

            report = service.run_from_instance(materialize=True, run_ocr=False)

            rows = store.load_records()
            self.assertEqual(report["status"], StageStatus.ERROR)
            self.assertEqual(rows[0].status, StageStatus.ERROR)
            self.assertEqual(rows[0].step_status(PipelineStep.CROPS), StageStatus.ERROR)

    def test_pipeline_can_run_directly_from_library_instance_payload(self) -> None:
        try:
            from modulos.instance_factory.pipeline import InstancePdfPipelineService
        except Exception as exc:  # pragma: no cover - optional detector/OCR dependencies.
            self.skipTest(f"pipeline deps unavailable: {exc}")

        class FakeGolden:
            def __init__(self, root: Path) -> None:
                self.root = root

            def load_instance(self, _name):
                return []

            def materialize_problem_crops_for_downstream(self, *_args, **_kwargs):
                target = self.root / "problem_crops_live"
                records = target / "records"
                images = target / "images"
                records.mkdir(parents=True, exist_ok=True)
                images.mkdir(parents=True, exist_ok=True)
                crop_id = "crop_library_run_001"
                (images / f"{crop_id}.png").write_bytes(b"png")
                payload = {
                    "schema_version": "problem_crop_live_v1",
                    "crop_id": crop_id,
                    "source_pdf_path": "E:/Banco/libro.pdf",
                    "source_page_number": 3,
                    "source_page_image": "page.png",
                    "bbox_px": [5, 6, 50, 60],
                    "crop_image_rel": f"images/{crop_id}.png",
                    "source_record_id": "page_0003",
                    "layout_mode": "una_columna",
                }
                (records / f"{crop_id}.json").write_text(json.dumps(payload), encoding="utf-8")
                return target, [crop_id]

        with tempfile.TemporaryDirectory() as tmp:
            book = {
                "id": 9,
                "codigo": "ALG01",
                "titulo": "Algebra",
                "pdf_path": "E:/Banco/libro.pdf",
                "workspace_dir": str(Path(tmp) / "workspace"),
            }
            instance = {"tipo": "S07", "session_path": str(Path(tmp) / "workspace" / "sessions" / "S07.json")}

            report = InstancePdfPipelineService.run_from_library_instance(
                book,
                instance,
                db_name="demo",
                golden_controller=FakeGolden(Path(tmp)),
                materialize=True,
                run_ocr=False,
            )

            staging_root = Path(report["staging_root"])
            rows = sorted((staging_root / "records").glob("*.json"))
            self.assertEqual(report["context"]["book_code"], "ALG01")
            self.assertEqual(report["context"]["instance_type"], "S07")
            self.assertEqual(report["policy"]["target"], "staging_only")
            self.assertTrue(report["contract_report"]["validation"]["valid"])
            self.assertEqual(len(rows), 1)
            payload = json.loads(rows[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["source"]["page_number"], 3)
            self.assertEqual(payload["source"]["bbox_px"], [5, 6, 50, 60])
            self.assertNotIn("sql", payload)

    def test_page_selection_supports_ranges_and_rejects_invalid_pages(self) -> None:
        self.assertEqual(parse_page_selection("1-3, 5, 7-6", 10), [1, 2, 3, 5, 6, 7])
        with self.assertRaises(ValueError):
            parse_page_selection("1, 12", 10)

    def test_model_inventory_records_provider_version_and_fallbacks(self) -> None:
        previous = {key: os.environ.get(key) for key in ("PDF_PROBLEM_MODEL", "PDF_PROBLEM_MODEL_REPO")}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                local_model = Path(tmp) / "pdf_detector_v9.pt"
                local_model.write_bytes(b"stub")
                os.environ["PDF_PROBLEM_MODEL"] = str(local_model)
                os.environ.pop("PDF_PROBLEM_MODEL_REPO", None)

                defaults = resolve_model_defaults().to_dict()

                self.assertEqual(defaults["pdf_detector"], str(local_model.resolve()))
                self.assertEqual(defaults["stages"]["pdf_detector"]["provider"], "local")
                self.assertEqual(defaults["stages"]["pdf_detector"]["version"], "v9")
                self.assertEqual(defaults["stages"]["ocr"]["fallback"], "local_tesseract_ocr_and_rule_parser")
                self.assertEqual(defaults["schema_version"], "model_inventory_v2")
                manifest = build_model_inventory_manifest(resolve_model_defaults())
                self.assertEqual(manifest["schema_version"], "pdf_factory_model_inventory_manifest_v1")
                self.assertTrue(manifest["candidates_from_config"])
                self.assertEqual(manifest["policy"]["problemas_write_enabled"], False)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
