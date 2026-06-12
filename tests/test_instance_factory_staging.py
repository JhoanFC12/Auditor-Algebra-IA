from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modulos.instance_factory.model_inventory import build_model_inventory_manifest, resolve_model_defaults
from modulos.instance_factory.models import InstancePipelineContext, PipelineStep, StageStatus, StagingProblemRecord
from modulos.instance_factory.page_selection import parse_page_selection
from modulos.instance_factory.pipeline import InstancePdfPipelineService
from modulos.instance_factory.staging import InstanceStagingStore


def structured_report(number: int, statement: str) -> dict:
    return {
        "items_total": 1,
        "items": [
            {
                "item": {
                    "n": str(number),
                    "curso": "GEO",
                    "tema": "ANGULOS",
                    "statement": statement,
                    "options": {"A": "10", "B": "20", "C": "30", "D": "40", "E": "50"},
                    "answer_key": "A",
                    "has_figure": False,
                },
                "rendered": statement,
            }
        ],
    }


class InstanceFactoryStagingTests(unittest.TestCase):
    def test_staging_record_archives_recovered_historical_errors_on_load(self) -> None:
        raw = {
            "record_id": "crop_recovered",
            "crop_id": "crop_recovered",
            "crop_path": "E:/tmp/crop.png",
            "status": "error",
            "raw_ocr": "1. Halle x",
            "structured_ocr": structured_report(1, "Halle x"),
            "normalized": {"numero": "1"},
            "errors": ["Error code: 403 - error historico"],
            "steps": {
                PipelineStep.OCR: {"status": StageStatus.READY, "detail": "OCR estructurado con items"},
                PipelineStep.NORMALIZATION: {"status": StageStatus.NEEDS_REVIEW, "detail": "normalizado pendiente"},
                PipelineStep.REVIEW: {"status": StageStatus.NEEDS_REVIEW, "detail": "pendiente"},
            },
        }

        record = StagingProblemRecord.from_dict(raw)

        self.assertEqual(record.errors, [])
        self.assertNotEqual(record.status, StageStatus.ERROR)
        self.assertEqual(record.audit["recovered_errors"][0]["errors"], ["Error code: 403 - error historico"])

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

    def test_ready_review_is_added_to_normalizer_training_bank(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bank = root / "normalizer_bank"
            context = InstancePipelineContext(book_code="GEO", instance_type="s02")
            store = InstanceStagingStore(context, root=root / "staging")
            crop = root / "crop.png"
            crop.write_bytes(b"png")
            store.upsert_record(
                StagingProblemRecord(
                    record_id="crop_002",
                    crop_id="crop_002",
                    crop_path=str(crop),
                    raw_ocr="12. Halle x. A) 1 B) 2 C) 3 D) 4 E) 5",
                )
            )

            with patch.dict(os.environ, {"NORMALIZER_TRAINING_BANK_ROOT": str(bank)}):
                updated = store.update_review(
                    "crop_002",
                    {
                        "numero": "12",
                        "latex_rendered_item": r"\item[\textbf{12.}] Halle $x$. £A)$1$æB)$2$æC)$3$£D)$4$ææE)$5$£",
                    },
                    notes="listo",
                    mark_ready=True,
                )

            manifest = json.loads((bank / "manifest.json").read_text(encoding="utf-8"))
            rows = [json.loads(line) for line in (bank / "samples.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(manifest["samples_total"], 1)
            self.assertEqual(rows[0]["final_latex"], updated.normalized["latex_rendered_item"])
            self.assertTrue(Path(rows[0]["images"][0]["bank_path"]).exists())
            self.assertEqual(updated.artifacts["normalizer_training_samples_total"], 1)

    def test_normalize_existing_ocr_can_target_single_record(self) -> None:
        try:
            from modulos.instance_factory.pipeline import InstancePdfPipelineService
        except Exception as exc:  # pragma: no cover - optional detector/OCR dependencies.
            self.skipTest(f"pipeline deps unavailable: {exc}")

        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="GEO", instance_type="s02")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            store.upsert_record(
                StagingProblemRecord(
                    record_id="crop_001",
                    crop_id="crop_001",
                    crop_path=str(Path(tmp) / "crop_001.png"),
                    errors=["Error code: 403 - error viejo"],
                    structured_ocr=structured_report(11, "Halle x"),
                )
            )
            store.upsert_record(
                StagingProblemRecord(
                    record_id="crop_002",
                    crop_id="crop_002",
                    crop_path=str(Path(tmp) / "crop_002.png"),
                    structured_ocr=structured_report(12, "Halle y"),
                    normalized={"numero": "99", "enunciado_latex": "preservar"},
                )
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            out = service.normalize_existing_ocr(record_id="crop_001")

            self.assertEqual([record.record_id for record in out], ["crop_001"])
            self.assertEqual(store.get_record("crop_001").normalized["numero"], "11")
            self.assertEqual(store.get_record("crop_001").errors, [])
            self.assertNotEqual(store.get_record("crop_001").status, StageStatus.ERROR)
            self.assertEqual(store.get_record("crop_002").normalized["numero"], "99")
            self.assertEqual(store.get_record("crop_002").normalized["enunciado_latex"], "preservar")

    def test_update_raw_ocr_keeps_raw_as_source_without_structured_requirement(self) -> None:
        try:
            from modulos.instance_factory.pipeline import InstancePdfPipelineService
        except Exception as exc:  # pragma: no cover - optional detector/OCR dependencies.
            self.skipTest(f"pipeline deps unavailable: {exc}")

        with tempfile.TemporaryDirectory() as tmp:
            crop = Path(tmp) / "crop_001.png"
            crop.write_bytes(b"png")
            context = InstancePipelineContext(book_code="GEO", instance_type="s03")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            store.upsert_record(
                StagingProblemRecord(
                    record_id="crop_001",
                    crop_id="crop_001",
                    crop_path=str(crop),
                    source={"problem_number": 1},
                    normalized={"numero": "99", "enunciado_latex": "viejo"},
                )
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            updated = service.update_raw_ocr(
                "crop_001",
                "<01.> Determinar x. A) $10^\\circ$ B) $20^\\circ$ C) $30^\\circ$ D) $40^\\circ$ E) $50^\\circ$",
            )

            self.assertEqual(updated.raw_ocr[:5], "<01.>")
            self.assertEqual(updated.structured_ocr, {})
            self.assertEqual(updated.normalized, {})
            self.assertEqual(updated.step_status(PipelineStep.OCR), StageStatus.READY)
            self.assertEqual(updated.step_status(PipelineStep.NORMALIZATION), StageStatus.PENDING)
            loaded = store.get_record("crop_001")
            self.assertEqual(loaded.structured_ocr, {})
            self.assertEqual(loaded.normalized, {})

    def test_normalize_existing_ocr_prepares_review_from_raw_ocr_only(self) -> None:
        try:
            from modulos.instance_factory.pipeline import InstancePdfPipelineService
        except Exception as exc:  # pragma: no cover - optional detector/OCR dependencies.
            self.skipTest(f"pipeline deps unavailable: {exc}")

        with tempfile.TemporaryDirectory() as tmp:
            crop = Path(tmp) / "crop_001.png"
            crop.write_bytes(b"png")
            context = InstancePipelineContext(book_code="GEO", instance_type="s03")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            store.upsert_record(
                StagingProblemRecord(
                    record_id="crop_001",
                    crop_id="crop_001",
                    crop_path=str(crop),
                    raw_ocr="<01.> Determinar x. A) $10^\\circ$ B) $20^\\circ$",
                    source={"problem_number": 1},
                    errors=["ocr_estructura:error antiguo"],
                )
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            out = service.normalize_existing_ocr(record_id="crop_001")
            loaded = store.get_record("crop_001")

            self.assertEqual([record.record_id for record in out], ["crop_001"])
            self.assertEqual(loaded.errors, [])
            self.assertEqual(loaded.structured_ocr, {})
            self.assertEqual(loaded.step_status(PipelineStep.OCR), StageStatus.READY)
            self.assertEqual(loaded.step_status(PipelineStep.NORMALIZATION), StageStatus.NEEDS_REVIEW)
            self.assertEqual(loaded.normalized["normalizer"], "manual_raw_ocr_review")
            self.assertEqual(loaded.normalized["enunciado_latex"], "<01.> Determinar x. A) $10^\\circ$ B) $20^\\circ$")

    def test_trained_ocr_rejects_hf_router_as_dedicated_endpoint(self) -> None:
        try:
            from modulos.instance_factory.pipeline import InstancePdfPipelineService
            from modulos.modulo0_transcriptor.scan_pipeline.extractor import TRAINED_OCR_VISION_MODEL
        except Exception as exc:  # pragma: no cover - optional detector/OCR dependencies.
            self.skipTest(f"pipeline deps unavailable: {exc}")

        env_snapshot = {
            key: os.environ.get(key)
            for key in ("HF_TOKEN", "HUGGINGFACEHUB_API_TOKEN", "HF_TRAINED_OCR_BASE_URL")
        }
        try:
            os.environ["HF_TOKEN"] = "hf_test_token"
            os.environ.pop("HUGGINGFACEHUB_API_TOKEN", None)
            os.environ["HF_TRAINED_OCR_BASE_URL"] = "https://router.huggingface.co/v1"
            service = InstancePdfPipelineService(InstancePipelineContext(book_code="ALG01", instance_type="s01"))

            with patch("importlib.util.find_spec", return_value=object()):
                with self.assertRaisesRegex(RuntimeError, "router de Hugging Face Inference Providers"):
                    service._validate_ocr_runtime(
                        provider="hf",
                        model=TRAINED_OCR_VISION_MODEL,
                        trained_model=TRAINED_OCR_VISION_MODEL,
                    )
        finally:
            for key, value in env_snapshot.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

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

    def test_promotion_candidate_blocks_continuation_record_as_independent_problem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(
                book_code="GEO",
                instance_type="s03",
                pdf_path="E:/Banco/geometria.pdf",
            )
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            crop = Path(tmp) / "crop_cont.png"
            crop.write_bytes(b"png")
            record = StagingProblemRecord(
                record_id="crop_cont",
                crop_id="crop_cont",
                crop_path=str(crop),
                status=StageStatus.READY,
                source={"page_number": 2, "bbox_px": [10, 20, 210, 320]},
                raw_ocr="[CONT.] A) 1 B) 2 C) 3 D) 4 E) 5",
                normalized={
                    "status": "listo",
                    "enunciado_latex": "A) 1 B) 2 C) 3 D) 4 E) 5",
                    "continuacion": {
                        "es_continuacion": True,
                        "fusionar_con_anterior": True,
                        "parent_record_id": "crop_prev",
                    },
                },
                models={"ocr": "test", "normalizer": "human"},
                confidence={"ocr": 0.95},
                review={"review_status": StageStatus.READY},
            )
            record.set_step(PipelineStep.REVIEW, StageStatus.READY, "continuacion fusionada")
            store.upsert_record(record)

            candidate = store.build_promotion_candidate("crop_cont")

            self.assertFalse(candidate["promotion_enabled"])
            self.assertFalse(candidate["ready_for_future_promotion"])
            self.assertIn("continuacion:fusionada_con_anterior", candidate["blocking_issues"])
            self.assertEqual(candidate["write_operations"], [])

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

    def test_staging_records_are_loaded_in_page_and_box_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="s04", pdf_path="E:/Banco/libro.pdf")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="z_page_2",
                        crop_id="z_page_2",
                        crop_path=str(Path(tmp) / "z.png"),
                        source={"page_number": 2, "source_order": 3, "box_index": 1, "bbox_px": [10, 10, 40, 40]},
                    ),
                    StagingProblemRecord(
                        record_id="a_page_1_second",
                        crop_id="a_page_1_second",
                        crop_path=str(Path(tmp) / "a.png"),
                        source={"page_number": 1, "source_order": 2, "box_index": 2, "bbox_px": [10, 60, 40, 90]},
                    ),
                    StagingProblemRecord(
                        record_id="m_page_1_first",
                        crop_id="m_page_1_first",
                        crop_path=str(Path(tmp) / "m.png"),
                        source={"page_number": 1, "source_order": 1, "box_index": 1, "bbox_px": [10, 10, 40, 40]},
                    ),
                ]
            )

            self.assertEqual(
                [record.record_id for record in store.load_records()],
                ["m_page_1_first", "a_page_1_second", "z_page_2"],
            )

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

    def test_page_box_change_invalidates_downstream_staging_records(self) -> None:
        try:
            from modulos.instance_factory.pipeline import InstancePdfPipelineService
        except Exception as exc:  # pragma: no cover - optional detector/OCR dependencies.
            self.skipTest(f"pipeline deps unavailable: {exc}")

        class FakeGolden:
            def __init__(self, page_image: Path) -> None:
                self.rows = [
                    SimpleNamespace(
                        record_id="page_001",
                        page_number=1,
                        boxes=[(1, 2, 30, 40)],
                        reviewed=True,
                        layout_mode="una_columna",
                        detector_source="pdf_factory:test",
                        image_path=page_image,
                    )
                ]

            def load_instance(self, _name: str):
                return self.rows

            def upsert_instance_rows(self, _name: str, rows):
                self.rows = list(rows)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page = root / "page.png"
            page.write_bytes(b"png")
            crop = root / "crop.png"
            crop.write_bytes(b"png")
            context = InstancePipelineContext(book_code="ALG01", instance_type="s08", pdf_path="E:/Banco/libro.pdf")
            store = InstanceStagingStore(context, root=root / "staging")
            record = StagingProblemRecord(
                record_id="crop_001",
                crop_id="crop_001",
                crop_path=str(crop),
                status=StageStatus.READY,
                source={
                    "book_code": "ALG01",
                    "instance_type": "s08",
                    "pdf_path": "E:/Banco/libro.pdf",
                    "page_number": 1,
                    "source_record_id": "page_001",
                    "bbox_px": [1, 2, 30, 40],
                },
                raw_ocr="OCR viejo",
                structured_ocr={"items_total": 1},
                figure_segmentation={"segments_total": 1},
                normalized={"numero": "1"},
                review={"notes": "validado"},
                artifacts={"raw": "old.json"},
                golden_sync={"status": "contract_prepared"},
                errors=["error anterior"],
            )
            for step in (
                PipelineStep.PAGES,
                PipelineStep.BOXES,
                PipelineStep.CROPS,
                PipelineStep.OCR,
                PipelineStep.SEGMENTATION,
                PipelineStep.NORMALIZATION,
                PipelineStep.REVIEW,
            ):
                record.set_step(step, StageStatus.READY, "listo")
            store.upsert_record(record)
            service = InstancePdfPipelineService(context, golden_controller=FakeGolden(page), staging_store=store)

            service.update_page_boxes("page_001", [[5, 6, 45, 55]], layout_mode="una_columna")

            loaded = store.get_record("crop_001")
            assert loaded is not None
            self.assertEqual(loaded.crop_path, "")
            self.assertEqual(loaded.raw_ocr, "")
            self.assertEqual(loaded.structured_ocr, {})
            self.assertEqual(loaded.figure_segmentation, {})
            self.assertEqual(loaded.normalized, {})
            self.assertEqual(loaded.review, {})
            self.assertEqual(loaded.artifacts, {})
            self.assertEqual(loaded.golden_sync, {})
            self.assertEqual(loaded.errors, [])
            self.assertEqual(loaded.step_status(PipelineStep.CROPS), StageStatus.PENDING)
            self.assertEqual(loaded.step_status(PipelineStep.OCR), StageStatus.PENDING)
            self.assertEqual(loaded.step_status(PipelineStep.SEGMENTATION), StageStatus.PENDING)
            self.assertEqual(loaded.step_status(PipelineStep.NORMALIZATION), StageStatus.PENDING)
            self.assertEqual(loaded.step_status(PipelineStep.REVIEW), StageStatus.PENDING)
            self.assertEqual(loaded.audit["downstream_state"]["status"], "invalidated")
            self.assertEqual(loaded.audit["downstream_state"]["reason"], "page_boxes_changed")
            self.assertEqual(loaded.trace["downstream_invalidations"][-1]["reason"], "page_boxes_changed")

    def test_page_box_save_without_coordinate_change_preserves_downstream_records(self) -> None:
        try:
            from modulos.instance_factory.pipeline import InstancePdfPipelineService
        except Exception as exc:  # pragma: no cover - optional detector/OCR dependencies.
            self.skipTest(f"pipeline deps unavailable: {exc}")

        class FakeGolden:
            def __init__(self, page_image: Path) -> None:
                self.rows = [
                    SimpleNamespace(
                        record_id="page_001",
                        page_number=1,
                        boxes=[(1, 2, 30, 40)],
                        reviewed=True,
                        layout_mode="una_columna",
                        detector_source="pdf_factory:test",
                        image_path=page_image,
                    )
                ]

            def load_instance(self, _name: str):
                return self.rows

            def upsert_instance_rows(self, _name: str, rows):
                self.rows = list(rows)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page = root / "page.png"
            page.write_bytes(b"png")
            crop = root / "crop.png"
            crop.write_bytes(b"png")
            context = InstancePipelineContext(book_code="ALG01", instance_type="s08", pdf_path="E:/Banco/libro.pdf")
            store = InstanceStagingStore(context, root=root / "staging")
            record = StagingProblemRecord(
                record_id="crop_001",
                crop_id="crop_001",
                crop_path=str(crop),
                status=StageStatus.READY,
                source={"page_number": 1, "source_record_id": "page_001", "bbox_px": [1, 2, 30, 40]},
                raw_ocr="OCR vigente",
                structured_ocr={"items_total": 1},
                figure_segmentation={"segments_total": 1},
                normalized={"numero": "1"},
                review={"notes": "validado"},
            )
            record.set_step(PipelineStep.CROPS, StageStatus.READY, "crop disponible")
            record.set_step(PipelineStep.OCR, StageStatus.READY, "OCR listo")
            store.upsert_record(record)
            service = InstancePdfPipelineService(context, golden_controller=FakeGolden(page), staging_store=store)

            service.update_page_boxes("page_001", [[1, 2, 30, 40]], layout_mode="una_columna")

            loaded = store.get_record("crop_001")
            assert loaded is not None
            self.assertEqual(loaded.crop_path, str(crop))
            self.assertEqual(loaded.raw_ocr, "OCR vigente")
            self.assertEqual(loaded.structured_ocr["items_total"], 1)
            self.assertEqual(loaded.normalized["numero"], "1")
            self.assertNotIn("downstream_state", loaded.audit)

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

    def test_materialization_same_crop_id_with_new_bbox_clears_downstream_outputs(self) -> None:
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
                (images / f"{crop_id}.png").write_bytes(b"new-png")
                payload = {
                    "schema_version": "problem_crop_live_v1",
                    "crop_id": crop_id,
                    "source_pdf_path": "E:/Banco/libro.pdf",
                    "source_page_number": 4,
                    "source_page_image": "page.png",
                    "bbox_px": [9, 10, 90, 100],
                    "crop_image_rel": f"images/{crop_id}.png",
                    "source_record_id": "page_0004",
                    "layout_mode": "una_columna",
                }
                (records / f"{crop_id}.json").write_text(json.dumps(payload), encoding="utf-8")
                return target, [crop_id]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_crop = root / "old_crop.png"
            old_crop.write_bytes(b"old-png")
            context = InstancePipelineContext(book_code="ALG01", instance_type="s05", pdf_path="E:/Banco/libro.pdf")
            store = InstanceStagingStore(context, root=root / "staging")
            record = StagingProblemRecord(
                record_id="crop_pipeline_001",
                crop_id="crop_pipeline_001",
                crop_path=str(old_crop),
                status=StageStatus.READY,
                source={
                    "book_code": "ALG01",
                    "instance_type": "s05",
                    "pdf_path": "E:/Banco/libro.pdf",
                    "page_number": 4,
                    "source_record_id": "page_0004",
                    "bbox_px": [1, 2, 30, 40],
                },
                raw_ocr="OCR viejo",
                structured_ocr={"items_total": 1},
                figure_segmentation={"segments_total": 1},
                normalized={"numero": "4"},
                review={"notes": "validado"},
            )
            for step in (
                PipelineStep.CROPS,
                PipelineStep.OCR,
                PipelineStep.SEGMENTATION,
                PipelineStep.NORMALIZATION,
                PipelineStep.REVIEW,
            ):
                record.set_step(step, StageStatus.READY, "listo")
            store.upsert_record(record)
            service = InstancePdfPipelineService(context, golden_controller=FakeGolden(root), staging_store=store)

            out = service.materialize_crops_to_staging(rows=[])

            self.assertEqual(len(out), 1)
            loaded = store.get_record("crop_pipeline_001")
            assert loaded is not None
            self.assertEqual(loaded.source["bbox_px"], [9, 10, 90, 100])
            self.assertTrue(Path(loaded.crop_path).exists())
            self.assertEqual(loaded.raw_ocr, "")
            self.assertEqual(loaded.structured_ocr, {})
            self.assertEqual(loaded.figure_segmentation, {})
            self.assertEqual(loaded.normalized, {})
            self.assertEqual(loaded.review, {})
            self.assertEqual(loaded.step_status(PipelineStep.CROPS), StageStatus.READY)
            self.assertEqual(loaded.step_status(PipelineStep.OCR), StageStatus.PENDING)
            self.assertEqual(loaded.step_status(PipelineStep.SEGMENTATION), StageStatus.PENDING)
            self.assertEqual(loaded.audit["downstream_state"]["status"], "invalidated")
            self.assertEqual(loaded.trace["downstream_invalidations"][-1]["reason"], "crop_source_changed")

    def test_materialization_uses_crop_payload_order_not_crop_id_order(self) -> None:
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
                payloads = [
                    ("z_crop", 2, 3, 1, [10, 10, 40, 40]),
                    ("a_crop", 1, 2, 2, [10, 60, 40, 90]),
                    ("m_crop", 1, 1, 1, [10, 10, 40, 40]),
                ]
                for crop_id, page, source_order, box_index, bbox in payloads:
                    (images / f"{crop_id}.png").write_bytes(b"png")
                    payload = {
                        "schema_version": "problem_crop_live_v1",
                        "crop_id": crop_id,
                        "source_pdf_path": "E:/Banco/libro.pdf",
                        "source_page_number": page,
                        "source_order": source_order,
                        "box_index": box_index,
                        "bbox_px": bbox,
                        "crop_image_rel": f"images/{crop_id}.png",
                        "source_record_id": f"page_{page:04d}",
                    }
                    (records / f"{crop_id}.json").write_text(json.dumps(payload), encoding="utf-8")
                return target, ["z_crop", "a_crop", "m_crop"]

        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="s05", pdf_path="E:/Banco/libro.pdf")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            service = InstancePdfPipelineService(context, golden_controller=FakeGolden(Path(tmp)), staging_store=store)

            records = service.materialize_crops_to_staging(rows=[])

            self.assertEqual([record.record_id for record in records], ["m_crop", "a_crop", "z_crop"])
            self.assertEqual([record.record_id for record in store.load_records()], ["m_crop", "a_crop", "z_crop"])
            self.assertEqual(store.get_record("m_crop").source["source_order"], 1)

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
            overview = service.build_stage_overview()

            self.assertEqual(summary["pages_total"], 1)
            self.assertEqual(summary["boxes_total"], 2)
            self.assertEqual(summary["crops_found"], 1)
            self.assertEqual(page_rows[0]["status"], StageStatus.READY)
            self.assertEqual(stage_rows[0]["ocr_items"], 1)
            self.assertEqual(stage_rows[0]["segments_total"], 2)
            self.assertEqual(stage_rows[0]["steps"][PipelineStep.OCR], StageStatus.READY)
            ocr_overview = next(row for row in overview if str(row["stage"]).startswith("OCR"))
            self.assertEqual(ocr_overview["status"], StageStatus.READY)

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

    def test_artifact_dirs_compact_long_record_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(Path(tmp) / "book.pdf"))
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            long_record_id = "aseuni-semianual-geometria__semana_2_dc9b1f016c____ASEUNI_SEM_" + ("x" * 120)

            raw_dir = store.artifact_dir("raw_outputs", long_record_id, probe_file="figure_segmentation.json")
            review_dir = store.artifact_dir("review_outputs", long_record_id, probe_file="training_examples.json")

            self.assertNotEqual(raw_dir.name, long_record_id)
            self.assertNotEqual(review_dir.name, long_record_id)
            self.assertLessEqual(len(raw_dir.name), 48)
            self.assertEqual(raw_dir.name, review_dir.name)
            self.assertEqual(raw_dir.parent.name, "raw_outputs")
            self.assertEqual(review_dir.parent.name, "review_outputs")

    def test_cold_start_503_retry_keeps_ocr_request_alive(self) -> None:
        class Extractor:
            def __init__(self) -> None:
                self.calls = 0

            def extract_from_image(self, **_kwargs):
                self.calls += 1
                if self.calls < 3:
                    raise RuntimeError("503 Service Unavailable: endpoint is initializing")
                return [], "<01.> Halle x. A) $1$ B) $2$"

        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(Path(tmp) / "book.pdf"))
            service = InstancePdfPipelineService(context, staging_store=InstanceStagingStore(context, root=Path(tmp) / "staging"))
            extractor = Extractor()
            pipeline = SimpleNamespace(extractor=extractor)
            events: list[dict] = []

            with patch.dict(os.environ, {"HF_ENDPOINT_COLD_START_RETRIES": "2"}, clear=False):
                with patch("modulos.instance_factory.pipeline.time.sleep", lambda _seconds: None):
                    _items, raw = service._extract_with_cold_start_retry(
                        pipeline,
                        image_path=Path(tmp) / "crop.png",
                        curso="SIN_CURSO",
                        tema="SIN_TEMA",
                        start_n=1,
                        progress_callback=events.append,
                    )

            self.assertEqual(raw, "<01.> Halle x. A) $1$ B) $2$")
            self.assertEqual(extractor.calls, 3)
            self.assertEqual(len(events), 2)
            self.assertIn("despertando", events[0]["message"])


if __name__ == "__main__":
    unittest.main()
