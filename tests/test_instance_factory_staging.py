from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import modulos.instance_factory.staging as staging_module
from modulos.instance_factory.model_inventory import build_model_inventory_manifest, resolve_model_defaults
from modulos.instance_factory.models import InstancePipelineContext, PipelineStep, StageStatus, StagingProblemRecord
from modulos.instance_factory.page_selection import parse_page_selection
from modulos.instance_factory.pipeline import InstancePdfPipelineService
from modulos.instance_factory.staging import (
    MAX_ARTIFACT_PATH_LEN_SOFT_LIMIT,
    InstanceStagingStore,
    compact_artifact_dir_name,
)
from modulos.modulo13_laboratorio_pdf_segmentacion.controlador_laboratorio_pdf import ProblemPageRecord


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

    def test_manifest_static_payloads_are_cached_between_rewrites(self) -> None:
        staging_module._STATIC_MANIFEST_PAYLOAD_CACHE.clear()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                context = InstancePipelineContext(book_code="ALG01", instance_type="s01")
                store = InstanceStagingStore(context, root=Path(tmp) / "staging")
                record = StagingProblemRecord(
                    record_id="crop_001",
                    crop_id="crop_001",
                    crop_path=str(Path(tmp) / "crop.png"),
                    status=StageStatus.PENDING,
                )
                calls = {"inventory": 0, "matrix": 0}

                def inventory() -> dict:
                    calls["inventory"] += 1
                    return {"schema_version": "fake_inventory_v1"}

                def matrix() -> dict:
                    calls["matrix"] += 1
                    return {"schema_version": "fake_matrix_v1", "stages": {}}

                with patch("modulos.instance_factory.model_inventory.build_model_inventory_manifest", side_effect=inventory), patch(
                    "modulos.instance_factory.model_inventory.build_retraining_evaluation_matrix",
                    side_effect=matrix,
                ):
                    store.upsert_record(record)
                    store.rewrite_manifest()

                manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))
                self.assertEqual(manifest["model_inventory"]["schema_version"], "fake_inventory_v1")
                self.assertEqual(manifest["evaluation_matrix"]["schema_version"], "fake_matrix_v1")
                self.assertEqual(calls, {"inventory": 1, "matrix": 1})
        finally:
            staging_module._STATIC_MANIFEST_PAYLOAD_CACHE.clear()

    def test_load_record_entries_can_reuse_known_signature_without_hiding_external_changes(self) -> None:
        class CountingStore(InstanceStagingStore):
            def __init__(self, context: InstancePipelineContext, root: Path) -> None:
                super().__init__(context, root=root)
                self.signature_scans = 0

            def _scan_records_dir_signature(self) -> tuple[tuple[str, int, int], ...]:
                self.signature_scans += 1
                return super()._scan_records_dir_signature()

        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01")
            store = CountingStore(context, root=Path(tmp) / "staging")
            record = StagingProblemRecord(
                record_id="crop_001",
                crop_id="crop_001",
                crop_path=str(Path(tmp) / "crop.png"),
                status=StageStatus.PENDING,
            )
            store.upsert_record(record)

            store.signature_scans = 0
            known_signature = store._records_dir_signature()
            entries = store.load_record_entries(signature=known_signature)

            self.assertEqual(entries[0][1].record_id, "crop_001")
            self.assertEqual(store.signature_scans, 1)

            path = store._record_path("crop_001")
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["raw_ocr"] = "externo inmediato"
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            store.signature_scans = 0
            self.assertEqual(store.load_records()[0].raw_ocr, "externo inmediato")
            self.assertEqual(store.signature_scans, 1)

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
            self.assertEqual(manifest["revision_events_total"], 1)
            self.assertEqual(rows[0]["final_latex"], updated.normalized["latex_rendered_item"])
            self.assertEqual(rows[0]["revision_count"], 1)
            self.assertTrue(Path(rows[0]["images"][0]["bank_path"]).exists())
            self.assertEqual(updated.artifacts["normalizer_training_samples_total"], 1)

    def test_review_save_repairs_empty_final_latex_number_from_raw_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = root / "crop_024.png"
            crop.write_bytes(b"png")
            context = InstancePipelineContext(book_code="GEO", instance_type="s01")
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_record(
                StagingProblemRecord(
                    record_id="crop_024",
                    crop_id="crop_024",
                    crop_path=str(crop),
                    raw_ocr="<24.> Indique el valor de verdad. A) FFFV B) VFVF",
                )
            )

            updated = store.update_review(
                "crop_024",
                {
                    "latex_rendered_item": (
                        r"\item[\textbf{.}] [[curso=SIN_CURSO]] [[tema=SIN_TEMA]] "
                        r"[[Estado=sin_revisar]] [[Clave=]] <24.> Indique el valor de verdad."
                    ),
                },
                mark_ready=True,
            )

            self.assertEqual(updated.normalized["numero"], "24")
            self.assertIn(r"\item[\textbf{24.}]", updated.normalized["latex_rendered_item"])
            self.assertNotIn("<24.>", updated.normalized["latex_rendered_item"])

    def test_summary_counts_records_uploaded_to_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = InstancePipelineContext(book_code="GEO", instance_type="s02")
            store = InstanceStagingStore(context, root=root / "staging")
            crop = root / "crop.png"
            crop.write_bytes(b"png")
            store.upsert_record(
                StagingProblemRecord(
                    record_id="crop_002",
                    crop_id="crop_002",
                    crop_path=str(crop),
                    status=StageStatus.READY,
                    raw_ocr="<12.> Halle x.",
                    normalized={"latex_rendered_item": r"\item[\textbf{12.}] Halle $x$."},
                    audit={
                        "db_promotion": {
                            "schema_version": "pdf_factory_db_promotion_audit_v1",
                            "problem_id": 321,
                        }
                    },
                )
            )

            summary = store.summarize_records()

            self.assertEqual(summary["ready"], 1)
            self.assertEqual(summary["subidos_bd"], 1)

    def test_summary_counts_continuation_outside_effective_ocr_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = InstancePipelineContext(book_code="GEO", instance_type="s02")
            store = InstanceStagingStore(context, root=root / "staging")
            main_crop = root / "crop_main.png"
            cont_crop = root / "crop_cont.png"
            main_crop.write_bytes(b"png")
            cont_crop.write_bytes(b"png")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="crop_008",
                        crop_id="crop_008",
                        crop_path=str(main_crop),
                        status=StageStatus.READY,
                        raw_ocr="<08.> Indique el valor de verdad.",
                        normalized={
                            "latex_rendered_item": r"\item[\textbf{8.}] Indique...",
                            "continuaciones_fusionadas": [{"record_id": "crop_009"}],
                        },
                    ),
                    StagingProblemRecord(
                        record_id="crop_009",
                        crop_id="crop_009",
                        crop_path=str(cont_crop),
                        status=StageStatus.READY,
                        raw_ocr="[CONT.] A) FVVF B) FFVF C) FFFF D) FVFV E) FFVF",
                        normalized={
                            "latex_rendered_item": "[CONT.] A) FVVF B) FFVF C) FFFF D) FVFV E) FFVF",
                        },
                    ),
                ]
            )

            summary = store.summarize_records()

            self.assertEqual(summary["raw_records_total"], 2)
            self.assertEqual(summary["records_total"], 1)
            self.assertEqual(summary["crops_found"], 1)
            self.assertEqual(summary["ocr_done"], 1)
            self.assertEqual(summary["problems_total"], 1)
            self.assertEqual(summary["primary_records_total"], 1)
            self.assertEqual(summary["normalized_done"], 1)
            self.assertEqual(summary["ready"], 1)

    def test_normalizer_training_bank_keeps_parent_linked_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bank = root / "normalizer_bank"
            context = InstancePipelineContext(book_code="GEO", instance_type="s02")
            store = InstanceStagingStore(context, root=root / "staging")
            main_crop = root / "crop_main.png"
            cont_crop = root / "crop_cont.png"
            main_crop.write_bytes(b"png")
            cont_crop.write_bytes(b"png")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="crop_002",
                        crop_id="crop_002",
                        crop_path=str(main_crop),
                        raw_ocr="<08.> Indique el valor de verdad.",
                        normalized={"continuaciones_fusionadas": [{"record_id": "crop_003"}]},
                    ),
                    StagingProblemRecord(
                        record_id="crop_003",
                        crop_id="crop_003",
                        crop_path=str(cont_crop),
                        raw_ocr="A) FVVF B) FFVF C) FFFF D) FVFV E) FFVF",
                    ),
                ]
            )

            final_latex = (
                r"\item[\textbf{8.}] [[curso=Geometria]] [[tema=angulos]] [[Estado=sin_revisar]] "
                r"[[Clave=-]] Indique el valor de verdad. £A)FVVFæB)FFVFæC)FFFF£D)FVFVææE)FFVF£"
            )
            with patch.dict(os.environ, {"NORMALIZER_TRAINING_BANK_ROOT": str(bank)}):
                store.update_review(
                    "crop_002",
                    {
                        "numero": "8",
                        "continuaciones_fusionadas": [{"record_id": "crop_003"}],
                        "latex_rendered_item": final_latex,
                    },
                    notes="listo",
                    mark_ready=True,
                )

            row = json.loads((bank / "samples.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["continuations"][0]["record_id"], "crop_003")
            self.assertIn("FVVF", row["continuations"][0]["raw_ocr"])
            self.assertEqual([image["role"] for image in row["images"]], ["main", "continuation_01"])

    def test_normalizer_training_bank_skips_angle_bracket_continuation_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bank = root / "normalizer_bank"
            context = InstancePipelineContext(book_code="GEO", instance_type="s02")
            store = InstanceStagingStore(context, root=root / "staging")
            crop = root / "crop_cont.png"
            crop.write_bytes(b"png")
            store.upsert_record(
                StagingProblemRecord(
                    record_id="crop_cont",
                    crop_id="crop_cont",
                    crop_path=str(crop),
                    raw_ocr="<CONT.> A) FVVF B) FFVF C) FFFF D) FVFV E) FFVF",
                )
            )

            with patch.dict(os.environ, {"NORMALIZER_TRAINING_BANK_ROOT": str(bank)}):
                store.update_review(
                    "crop_cont",
                    {
                        "latex_rendered_item": (
                            r"\item[\textbf{8.}] [[curso=Geometria]] [[tema=angulos]] "
                            r"[[Estado=sin_revisar]] [[Clave=-]] A) FVVF B) FFVF"
                        )
                    },
                    notes="continuacion",
                    mark_ready=True,
                )

            self.assertTrue((bank / "samples.jsonl").exists())
            self.assertEqual((bank / "samples.jsonl").read_text(encoding="utf-8"), "")

    def test_update_review_auto_links_immediate_continuation_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bank = root / "normalizer_bank"
            context = InstancePipelineContext(book_code="GEO", instance_type="s02")
            store = InstanceStagingStore(context, root=root / "staging")
            main_crop = root / "crop_008.png"
            cont_crop = root / "crop_009.png"
            main_crop.write_bytes(b"png")
            cont_crop.write_bytes(b"png")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="crop_008",
                        crop_id="crop_008",
                        crop_path=str(main_crop),
                        raw_ocr="<08.> Indique el valor de verdad.",
                        source={"page_number": 1, "source_order": 8, "box_index": 8, "bbox_px": [10, 10, 40, 40]},
                    ),
                    StagingProblemRecord(
                        record_id="crop_009",
                        crop_id="crop_009",
                        crop_path=str(cont_crop),
                        raw_ocr="[CONT.] A) FVVF B) FFVF C) FFFF D) FVFV E) FFVF",
                        source={"page_number": 2, "source_order": 9, "box_index": 1, "bbox_px": [10, 10, 40, 40]},
                    ),
                ]
            )
            final_latex = (
                r"\item[\textbf{8.}] [[curso=Geometria]] [[tema=angulos]] [[Estado=sin_revisar]] "
                r"[[Clave=-]] Indique el valor de verdad. Â£A)FVVFÃ¦B)FFVFÃ¦C)FFFFÂ£D)FVFVÃ¦Ã¦E)FFVFÂ£"
            )

            with patch.dict(os.environ, {"NORMALIZER_TRAINING_BANK_ROOT": str(bank)}):
                updated = store.update_review(
                    "crop_008",
                    {"numero": "8", "latex_rendered_item": final_latex},
                    notes="listo",
                    mark_ready=True,
                )

            fused = updated.normalized.get("continuaciones_fusionadas") or []
            self.assertEqual(fused[0]["record_id"], "crop_009")
            self.assertIn("FVVF", fused[0]["texto_fusionado"])
            row = json.loads((bank / "samples.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual([image["role"] for image in row["images"]], ["main", "continuation_01"])

    def test_update_review_preserves_existing_unmarked_continuation_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bank = root / "normalizer_bank"
            context = InstancePipelineContext(book_code="GEO", instance_type="s02")
            store = InstanceStagingStore(context, root=root / "staging")
            main_crop = root / "crop_020.png"
            cont_crop = root / "crop_021.png"
            main_crop.write_bytes(b"png")
            cont_crop.write_bytes(b"png")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="crop_020",
                        crop_id="crop_020",
                        crop_path=str(main_crop),
                        raw_ocr="<20.> Dadas las proposiciones.",
                        normalized={"continuaciones_fusionadas": [{"record_id": "crop_021"}]},
                    ),
                    StagingProblemRecord(
                        record_id="crop_021",
                        crop_id="crop_021",
                        crop_path=str(cont_crop),
                        raw_ocr="A) FVF B) FFF C) FVV D) VVF E) FFV",
                    ),
                ]
            )

            with patch.dict(os.environ, {"NORMALIZER_TRAINING_BANK_ROOT": str(bank)}):
                updated = store.update_review(
                    "crop_020",
                    {
                        "numero": "20",
                        "latex_rendered_item": (
                            r"\item[\textbf{20.}] [[curso=Geometria]] [[tema=angulos]] "
                            r"[[Estado=sin_revisar]] [[Clave=-]] Dadas las proposiciones."
                        ),
                    },
                    notes="listo",
                    mark_ready=True,
                )

            fused = updated.normalized.get("continuaciones_fusionadas") or []
            self.assertEqual(fused[0]["record_id"], "crop_021")
            self.assertIn("FVF", fused[0]["texto_fusionado"])

    def test_repair_detected_continuation_links_persists_legacy_marker_only_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = InstancePipelineContext(book_code="GEO", instance_type="s02")
            store = InstanceStagingStore(context, root=root / "staging")
            main_crop = root / "crop_024.png"
            cont_crop = root / "crop_025.png"
            main_crop.write_bytes(b"png")
            cont_crop.write_bytes(b"png")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="crop_024",
                        crop_id="crop_024",
                        crop_path=str(main_crop),
                        raw_ocr="<24.> Indique el valor de verdad.",
                        normalized={"numero": "24"},
                        source={"page_number": 3, "source_order": 24, "box_index": 9, "bbox_px": [10, 10, 40, 40]},
                    ),
                    StagingProblemRecord(
                        record_id="crop_025",
                        crop_id="crop_025",
                        crop_path=str(cont_crop),
                        raw_ocr="[CONT.] A) FFFV B) VFVF C) FVFF D) VFFV E) VFFF",
                        source={"page_number": 4, "source_order": 25, "box_index": 1, "bbox_px": [10, 10, 40, 40]},
                    ),
                ]
            )

            changed = store.repair_detected_continuation_links()
            loaded = store.get_record("crop_024")

            self.assertEqual([row.record_id for row in changed], ["crop_024"])
            fused = loaded.normalized.get("continuaciones_fusionadas") or []
            self.assertEqual(fused[0]["record_id"], "crop_025")
            self.assertIn("FFFV", fused[0]["texto_fusionado"])

    def test_ready_review_recorrection_keeps_normalizer_training_history(self) -> None:
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
                store.update_review(
                    "crop_002",
                    {
                        "numero": "12",
                        "latex_rendered_item": r"\item[\textbf{12.}] Halle $x$. £A)$1$æB)$2$æC)$3$£D)$4$ææE)$5$£",
                    },
                    mark_ready=True,
                )
                updated = store.update_review(
                    "crop_002",
                    {
                        "numero": "12",
                        "latex_rendered_item": r"\item[\textbf{12.}] Halle $x+y$. £A)$1$æB)$2$æC)$3$£D)$4$ææE)$5$£",
                    },
                    mark_ready=True,
                )

            manifest = json.loads((bank / "manifest.json").read_text(encoding="utf-8"))
            rows = [json.loads(line) for line in (bank / "samples.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(manifest["samples_total"], 1)
            self.assertEqual(manifest["revision_events_total"], 2)
            self.assertEqual(rows[0]["revision_count"], 2)
            self.assertIn("Halle $x+y$", rows[0]["final_latex"])
            self.assertIn("Halle $x$.", rows[0]["correction_history"][0]["final_latex"])
            self.assertEqual(updated.artifacts["normalizer_training_samples_total"], 1)

    def test_normalize_existing_ocr_requires_raw_ocr_when_targeting_single_record(self) -> None:
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
            self.assertEqual(store.get_record("crop_001").normalized, {})
            self.assertEqual(store.get_record("crop_001").step_status(PipelineStep.NORMALIZATION), StageStatus.PENDING)
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
                    review={"final_latex": "viejo"},
                )
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            ocr_bank = Path(tmp) / "ocr_golden_live"
            with patch.dict(os.environ, {"OCR_TRAINING_BANK_ROOTS": str(ocr_bank)}):
                updated = service.update_raw_ocr(
                    "crop_001",
                    "<01.> Determinar x. A) $10^\\circ$ B) $20^\\circ$ C) $30^\\circ$ D) $40^\\circ$ E) $50^\\circ$",
                )

            self.assertEqual(updated.raw_ocr[:5], "<01.>")
            self.assertEqual(updated.structured_ocr, {})
            self.assertEqual(updated.normalized, {})
            self.assertEqual(updated.review, {})
            self.assertEqual(updated.step_status(PipelineStep.OCR), StageStatus.READY)
            self.assertEqual(updated.step_status(PipelineStep.NORMALIZATION), StageStatus.PENDING)
            loaded = store.get_record("crop_001")
            self.assertEqual(loaded.structured_ocr, {})
            self.assertEqual(loaded.normalized, {})
            self.assertEqual(loaded.review, {})
            manifest = json.loads((ocr_bank / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["records_corrected"], 1)
            self.assertEqual(manifest["revision_events_total"], 1)
            rows = list((ocr_bank / "records").glob("*.json"))
            self.assertEqual(len(rows), 1)
            training_record = json.loads(rows[0].read_text(encoding="utf-8"))
            self.assertEqual(training_record["corrected_text"], updated.raw_ocr)
            self.assertEqual(training_record["revision_count"], 1)
            self.assertTrue((ocr_bank / training_record["copied_image_rel"]).exists())

    def test_update_raw_ocr_same_text_does_not_create_training_revision(self) -> None:
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
                    raw_ocr="<01.> Texto modelo",
                    structured_ocr={"items_total": 1},
                    normalized={"numero": "1", "enunciado_latex": "preservar"},
                )
            )
            service = InstancePdfPipelineService(context, staging_store=store)
            ocr_bank = Path(tmp) / "ocr_golden_live"

            with patch.dict(os.environ, {"OCR_TRAINING_BANK_ROOTS": str(ocr_bank)}):
                updated = service.update_raw_ocr("crop_001", "<01.> Texto modelo   \n")

            self.assertEqual(updated.raw_ocr, "<01.> Texto modelo")
            self.assertEqual(updated.structured_ocr, {"items_total": 1})
            self.assertEqual(updated.normalized["enunciado_latex"], "preservar")
            self.assertFalse((ocr_bank / "manifest.json").exists())

    def test_update_raw_ocr_same_text_rebuilds_missing_raw_artifacts(self) -> None:
        try:
            from modulos.instance_factory.pipeline import InstancePdfPipelineService
        except Exception as exc:  # pragma: no cover - optional detector/OCR dependencies.
            self.skipTest(f"pipeline deps unavailable: {exc}")

        with tempfile.TemporaryDirectory() as tmp:
            crop = Path(tmp) / "crop_001.png"
            crop.write_bytes(b"png")
            context = InstancePipelineContext(book_code="GEO", instance_type="s03")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            record = StagingProblemRecord(
                record_id="crop_001",
                crop_id="crop_001",
                crop_path=str(crop),
                raw_ocr="<01.> Texto modelo",
                structured_ocr={"items_total": 1},
                trace={"last_raw_ocr_review": {"source": "human_raw_ocr_editor"}},
            )
            store.upsert_record(record)
            service = InstancePdfPipelineService(context, staging_store=store)
            raw_dir = store.artifact_dir("raw_outputs", "crop_001", probe_file="figure_segmentation.json")
            structured_path = raw_dir / "structured_ocr.json"
            self.assertFalse(structured_path.exists())

            updated = service.update_raw_ocr("crop_001", "<01.> Texto modelo")

            self.assertEqual(updated.raw_ocr, "<01.> Texto modelo")
            self.assertEqual(updated.structured_ocr, {"items_total": 1})
            self.assertTrue(structured_path.exists())
            self.assertEqual(json.loads(structured_path.read_text(encoding="utf-8")), {"items_total": 1})

    def test_update_raw_ocr_rejects_invalidated_downstream_record(self) -> None:
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
                    raw_ocr="<01.> Texto previo",
                    audit={"downstream_state": {"status": "invalidated", "reason": "page_boxes_changed"}},
                )
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            with self.assertRaisesRegex(ValueError, "Regenera staging"):
                service.update_raw_ocr("crop_001", "<01.> Texto corregido")

    def test_update_raw_ocr_force_review_accepts_same_text_once(self) -> None:
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
                    raw_ocr="<01.> Texto modelo",
                    normalized={"numero": "1", "enunciado_latex": "preservar"},
                    review={"final_latex": "preservar"},
                )
            )
            service = InstancePdfPipelineService(context, staging_store=store)
            ocr_bank = Path(tmp) / "ocr_golden_live"

            with patch.dict(os.environ, {"OCR_TRAINING_BANK_ROOTS": str(ocr_bank)}):
                accepted = service.update_raw_ocr("crop_001", "<01.> Texto modelo", force_review=True)
                repeated = service.update_raw_ocr("crop_001", "<01.> Texto modelo", force_review=True)

            self.assertEqual(accepted.raw_ocr, "<01.> Texto modelo")
            self.assertEqual(accepted.normalized["enunciado_latex"], "preservar")
            self.assertEqual(accepted.review["final_latex"], "preservar")
            self.assertEqual(accepted.trace["last_raw_ocr_review"]["source"], "human_raw_ocr_batch_acceptance")
            self.assertTrue(accepted.trace["last_raw_ocr_review"]["accepted_without_text_change"])
            self.assertEqual(repeated.trace["last_raw_ocr_review"]["source"], "human_raw_ocr_batch_acceptance")
            manifest = json.loads((ocr_bank / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["records_corrected"], 1)
            self.assertEqual(manifest["revision_events_total"], 1)

    def test_update_raw_ocr_accumulates_recorrections_without_duplicate_samples(self) -> None:
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
                    raw_ocr="modelo inicial",
                )
            )
            service = InstancePdfPipelineService(context, staging_store=store)
            ocr_bank = Path(tmp) / "ocr_golden_live"

            with patch.dict(os.environ, {"OCR_TRAINING_BANK_ROOTS": str(ocr_bank)}):
                service.update_raw_ocr("crop_001", "texto corregido v1")
                updated = service.update_raw_ocr("crop_001", "texto corregido v2")

            manifest = json.loads((ocr_bank / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["records_corrected"], 1)
            self.assertEqual(manifest["revision_events_total"], 2)
            rows = list((ocr_bank / "records").glob("*.json"))
            self.assertEqual(len(rows), 1)
            training_record = json.loads(rows[0].read_text(encoding="utf-8"))
            self.assertEqual(training_record["corrected_text"], "texto corregido v2")
            self.assertEqual(training_record["revision_count"], 2)
            self.assertEqual(training_record["correction_history"][0]["corrected_text"], "texto corregido v1")
            self.assertEqual(updated.artifacts["ocr_training_revision_count"], 2)

    def test_update_figure_segments_resets_normalization_and_review(self) -> None:
        try:
            from modulos.instance_factory.pipeline import InstancePdfPipelineService
        except Exception as exc:  # pragma: no cover - optional detector/OCR dependencies.
            self.skipTest(f"pipeline deps unavailable: {exc}")

        class FakeSegmenter:
            last_detector_payload = {
                "detector_source": "test",
                "review_status": "corrected",
            }

            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def save_reviewed_segments(self, crop_path, boxes, *, detector_payload=None):
                segment_path = Path(crop_path).parent / "seg_01.png"
                segment_path.write_bytes(b"png")
                return [
                    SimpleNamespace(
                        idx=1,
                        bbox=tuple(boxes[0]),
                        image_path=segment_path,
                    )
                ]

        with tempfile.TemporaryDirectory() as tmp:
            crop = Path(tmp) / "crop_001.png"
            crop.write_bytes(b"png")
            context = InstancePipelineContext(book_code="GEO", instance_type="s03")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            record = StagingProblemRecord(
                record_id="crop_001",
                crop_id="crop_001",
                crop_path=str(crop),
                raw_ocr="<01.> Halle x",
                figure_segmentation={"segments_total": 1},
                normalized={"numero": "1", "enunciado_latex": "viejo"},
                review={"final_latex": "viejo"},
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
            service = InstancePdfPipelineService(context, staging_store=store)

            with patch("modulos.modulo0_transcriptor.segmentador_v2.SegmentadorProblemasV2", FakeSegmenter):
                updated = service.update_figure_segments("crop_001", [[1, 2, 30, 40]])

            self.assertEqual(updated.raw_ocr, "<01.> Halle x")
            self.assertEqual(updated.figure_segmentation["segments_total"], 1)
            self.assertEqual(updated.normalized, {})
            self.assertEqual(updated.review, {})
            self.assertEqual(updated.step_status(PipelineStep.SEGMENTATION), StageStatus.READY)
            self.assertEqual(updated.step_status(PipelineStep.NORMALIZATION), StageStatus.PENDING)
            self.assertEqual(updated.step_status(PipelineStep.REVIEW), StageStatus.PENDING)
            raw_dir = store.artifact_dir("raw_outputs", "crop_001", probe_file="figure_segmentation.json")
            figure_path = raw_dir / "figure_segmentation.json"
            self.assertTrue(figure_path.exists())
            self.assertEqual(json.loads(figure_path.read_text(encoding="utf-8"))["segments_total"], 1)

    def test_update_figure_segments_rejects_invalidated_downstream_record(self) -> None:
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
                    audit={"downstream_state": {"status": "invalidated", "reason": "page_boxes_changed"}},
                )
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            with self.assertRaisesRegex(ValueError, "Regenera staging"):
                service.update_figure_segments("crop_001", [[1, 2, 30, 40]])

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

    def test_string_false_continuation_flags_keep_record_as_primary(self) -> None:
        try:
            from modulos.instance_factory.pipeline import InstancePdfPipelineService
        except Exception as exc:  # pragma: no cover - optional detector/OCR dependencies.
            self.skipTest(f"pipeline deps unavailable: {exc}")

        with tempfile.TemporaryDirectory() as tmp:
            crop = Path(tmp) / "crop_024.png"
            crop.write_bytes(b"png")
            context = InstancePipelineContext(book_code="GEO", instance_type="s01")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            record = StagingProblemRecord(
                record_id="crop_024",
                crop_id="crop_024",
                crop_path=str(crop),
                raw_ocr="<24.> Indique el valor de verdad. A) FFFV B) VFVF",
                normalized={
                    "numero": "24",
                    "latex_rendered_item": r"\item[\textbf{24.}] Indique el valor de verdad.",
                    "continuacion": {
                        "es_continuacion": "false",
                        "fusionar_con_anterior": "false",
                    },
                },
                status=StageStatus.READY,
            )
            store.upsert_record(record)
            service = InstancePdfPipelineService(context, staging_store=store)

            self.assertFalse(service._is_continuation_record(record))
            self.assertFalse(store._is_summary_continuation_record(record))
            self.assertEqual(store.summarize_records([record])["primary_records_total"], 1)
            candidate = store.build_promotion_candidate("crop_024")
            self.assertNotIn("continuacion:fusionada_con_anterior", candidate["blocking_issues"])

            record.normalized["continuacion"]["fusionar_con_anterior"] = "true"
            self.assertTrue(service._is_continuation_record(record))
            self.assertTrue(store._is_summary_continuation_record(record))

    def test_continuation_flag_accepts_spanish_yes_without_accepting_false(self) -> None:
        from modulos.instance_factory.continuations import truthy_continuation_flag

        self.assertFalse(truthy_continuation_flag("false"))
        self.assertFalse(truthy_continuation_flag("no"))
        self.assertTrue(truthy_continuation_flag("si"))
        self.assertTrue(truthy_continuation_flag("s\u00ed"))
        self.assertTrue(truthy_continuation_flag("s\u00c3\u00ad"))

    def test_normalize_existing_ocr_merges_continuation_raw_ocr_into_parent(self) -> None:
        try:
            from modulos.instance_factory.pipeline import InstancePdfPipelineService
        except Exception as exc:  # pragma: no cover - optional detector/OCR dependencies.
            self.skipTest(f"pipeline deps unavailable: {exc}")

        with tempfile.TemporaryDirectory() as tmp:
            main_crop = Path(tmp) / "crop_001.png"
            cont_crop = Path(tmp) / "crop_002.png"
            main_crop.write_bytes(b"png")
            cont_crop.write_bytes(b"png")
            context = InstancePipelineContext(book_code="GEO", instance_type="s03")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="crop_001",
                        crop_id="crop_001",
                        crop_path=str(main_crop),
                        raw_ocr="<08.> Indique el valor de verdad.",
                        source={"problem_number": 8},
                        normalized={
                            "numero": "8",
                            "curso": "Geometria",
                            "tema": "angulos",
                            "continuaciones_fusionadas": [{"record_id": "crop_002"}],
                        },
                    ),
                    StagingProblemRecord(
                        record_id="crop_002",
                        crop_id="crop_002",
                        crop_path=str(cont_crop),
                        raw_ocr="A) FVVF B) FFVF C) FFFF D) FVFV E) FFVF",
                        normalized={
                            "continuacion": {
                                "es_continuacion": True,
                                "fusionar_con_anterior": True,
                                "parent_record_id": "crop_001",
                            }
                        },
                    ),
                ]
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            out = service.normalize_existing_ocr(record_id="crop_001")
            loaded = store.get_record("crop_001")

            self.assertEqual([record.record_id for record in out], ["crop_001"])
            self.assertIn("Indique el valor", loaded.normalized["enunciado_latex"])
            self.assertIn("A) FVVF", loaded.normalized["enunciado_latex"])
            self.assertEqual(loaded.normalized["metadata_tecnica"]["raw_ocr_source"], "raw_ocr_plus_continuations")
            self.assertEqual(loaded.normalized["metadata_tecnica"]["continuation_record_ids"], ["crop_002"])
            self.assertEqual(loaded.normalized["continuaciones_fusionadas"][0]["record_id"], "crop_002")

    def test_normalize_existing_ocr_repairs_marker_only_continuation_before_draft(self) -> None:
        try:
            from modulos.instance_factory.pipeline import InstancePdfPipelineService
        except Exception as exc:  # pragma: no cover - optional detector/OCR dependencies.
            self.skipTest(f"pipeline deps unavailable: {exc}")

        with tempfile.TemporaryDirectory() as tmp:
            main_crop = Path(tmp) / "crop_024.png"
            cont_crop = Path(tmp) / "crop_025.png"
            main_crop.write_bytes(b"png")
            cont_crop.write_bytes(b"png")
            context = InstancePipelineContext(book_code="GEO", instance_type="s03")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="crop_024",
                        crop_id="crop_024",
                        crop_path=str(main_crop),
                        raw_ocr="<24.> Indique el valor de verdad.",
                        source={"problem_number": 24, "page_number": 3, "source_order": 24, "box_index": 9},
                    ),
                    StagingProblemRecord(
                        record_id="crop_025",
                        crop_id="crop_025",
                        crop_path=str(cont_crop),
                        raw_ocr="[CONT.] A) FFFV B) VFVF C) FVFF D) VFFV E) VFFF",
                        source={"page_number": 4, "source_order": 25, "box_index": 1},
                    ),
                ]
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            service.normalize_existing_ocr(record_id="crop_024")
            loaded = store.get_record("crop_024")

            self.assertIn("Indique el valor", loaded.normalized["enunciado_latex"])
            self.assertIn("FFFV", loaded.normalized["enunciado_latex"])
            self.assertEqual(loaded.normalized["metadata_tecnica"]["continuation_record_ids"], ["crop_025"])
            self.assertEqual(loaded.normalized["continuaciones_fusionadas"][0]["record_id"], "crop_025")

    def test_normalize_existing_ocr_replaces_stale_final_latex_with_parent_continuation(self) -> None:
        try:
            from modulos.instance_factory.pipeline import InstancePdfPipelineService
        except Exception as exc:  # pragma: no cover - optional detector/OCR dependencies.
            self.skipTest(f"pipeline deps unavailable: {exc}")

        with tempfile.TemporaryDirectory() as tmp:
            main_crop = Path(tmp) / "crop_001.png"
            cont_crop = Path(tmp) / "crop_002.png"
            main_crop.write_bytes(b"png")
            cont_crop.write_bytes(b"png")
            context = InstancePipelineContext(book_code="GEO", instance_type="s03")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="crop_001",
                        crop_id="crop_001",
                        crop_path=str(main_crop),
                        raw_ocr="<08.> Indique el valor de verdad.",
                        source={"problem_number": 8},
                        normalized={
                            "numero": "8",
                            "curso": "Geometria",
                            "tema": "angulos",
                            "latex_rendered_item": r"\item[\textbf{8.}] Indique el valor de verdad.",
                            "continuaciones_fusionadas": [{"record_id": "crop_002"}],
                        },
                    ),
                    StagingProblemRecord(
                        record_id="crop_002",
                        crop_id="crop_002",
                        crop_path=str(cont_crop),
                        raw_ocr="A) FVVF B) FFVF C) FFFF D) FVFV E) FFVF",
                    ),
                ]
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            service.normalize_existing_ocr(record_id="crop_001")
            loaded = store.get_record("crop_001")

            self.assertEqual(loaded.normalized["latex_rendered_item"], "")
            self.assertIn("Indique el valor", loaded.normalized["enunciado_latex"])
            self.assertIn("A) FVVF", loaded.normalized["enunciado_latex"])
            self.assertEqual(loaded.normalized["metadata_tecnica"]["raw_ocr_source"], "raw_ocr_plus_continuations")
            self.assertEqual(loaded.normalized["continuaciones_fusionadas"][0]["record_id"], "crop_002")

    def test_normalize_existing_ocr_rejects_single_invalidated_downstream_record(self) -> None:
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
                    raw_ocr="<01.> Determinar x.",
                    audit={"downstream_state": {"status": "invalidated", "reason": "page_boxes_changed"}},
                )
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            with self.assertRaisesRegex(ValueError, "Regenera staging"):
                service.normalize_existing_ocr(record_id="crop_001")

    def test_normalize_existing_ocr_marks_invalidated_record_and_continues_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stale_crop = Path(tmp) / "stale.png"
            good_crop = Path(tmp) / "good.png"
            stale_crop.write_bytes(b"png")
            good_crop.write_bytes(b"png")
            context = InstancePipelineContext(book_code="GEO", instance_type="s03")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="crop_stale",
                        crop_id="crop_stale",
                        crop_path=str(stale_crop),
                        raw_ocr="<01.> Determinar x.",
                        audit={"downstream_state": {"status": "invalidated", "reason": "page_boxes_changed"}},
                    ),
                    StagingProblemRecord(
                        record_id="crop_good",
                        crop_id="crop_good",
                        crop_path=str(good_crop),
                        raw_ocr="<02.> Halle y.",
                    ),
                ]
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            service.normalize_existing_ocr()

            stale = store.get_record("crop_stale")
            good = store.get_record("crop_good")
            self.assertIn("normalizacion:Regenera staging", stale.errors[0])
            self.assertEqual(stale.step_status(PipelineStep.NORMALIZATION), StageStatus.ERROR)
            self.assertIn("Halle y", good.normalized["enunciado_latex"])

    def test_normalize_with_ai_stores_review_draft_without_marking_ready(self) -> None:
        class FakeNormalizerClient:
            calls: list[dict] = []

            def __init__(self, *, model: str):
                self.model = model

            def generate_final_latex(self, input_payload: dict) -> dict:
                self.calls.append(input_payload)
                return {
                    "model": self.model,
                    "base_url": "fake://normalizer",
                    "final_latex": "\\item[\\textbf{1.}] [[curso=GEO]] [[tema=ANGULOS]] [[Estado=sin_revisar]] [[Clave=A]] Determinar x. \u00a3A)$10$\u00e6B)$20$\u00e6C)$30$\u00a3D)$40$\u00e6\u00e6E)$50$\u00a3",
                }

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
                    raw_ocr="<01.> Determinar x. A) $10$ B) $20$ C) $30$ D) $40$ E) $50$",
                    source={"problem_number": 1, "page_number": 3},
                    figure_segmentation={"status": StageStatus.READY, "segments_total": 0},
                )
            )
            service = InstancePdfPipelineService(context, staging_store=store)
            service.models.normalizer = "Jhoan12/fake-normalizer"

            with patch("modulos.instance_factory.pipeline.HfOcrNormalizerClient", FakeNormalizerClient):
                updated = service.normalize_with_ai("crop_001")

            self.assertEqual(updated.status, StageStatus.NEEDS_REVIEW)
            self.assertEqual(updated.step_status(PipelineStep.NORMALIZATION), StageStatus.NEEDS_REVIEW)
            self.assertEqual(updated.step_status(PipelineStep.REVIEW), StageStatus.NEEDS_REVIEW)
            self.assertEqual(updated.normalized["normalizer"], "Jhoan12/fake-normalizer")
            self.assertIn("\\item[\\textbf{1.}]", updated.normalized["latex_rendered_item"])
            self.assertTrue(updated.normalized["metadata_tecnica"]["ai_generated_requires_human_review"])
            self.assertEqual(FakeNormalizerClient.calls[0]["schema_version"], "normalizer_training_input_v1")
            self.assertEqual(FakeNormalizerClient.calls[0]["raw_ocr"], "<01.> Determinar x. A) $10$ B) $20$ C) $30$ D) $40$ E) $50$")
            self.assertEqual(store.get_record("crop_001").status, StageStatus.NEEDS_REVIEW)

    def test_normalize_with_ai_sends_parent_and_continuations(self) -> None:
        class FakeNormalizerClient:
            calls: list[dict] = []

            def __init__(self, *, model: str):
                self.model = model

            def generate_final_latex(self, input_payload: dict) -> dict:
                self.calls.append(input_payload)
                return {
                    "model": self.model,
                    "base_url": "fake://normalizer",
                    "final_latex": "\\item[\\textbf{8.}] [[curso=GEO]] [[tema=ANGULOS]] [[Estado=sin_revisar]] [[Clave=A]] Indique el valor de verdad. \u00a3A)FVVF\u00e6B)FFVF\u00e6C)FFFF\u00a3D)FVFV\u00e6\u00e6E)FFVF\u00a3",
                }

        with tempfile.TemporaryDirectory() as tmp:
            main_crop = Path(tmp) / "crop_001.png"
            cont_crop = Path(tmp) / "crop_002.png"
            main_crop.write_bytes(b"png")
            cont_crop.write_bytes(b"png")
            context = InstancePipelineContext(book_code="GEO", instance_type="s03")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="crop_001",
                        crop_id="crop_001",
                        crop_path=str(main_crop),
                        raw_ocr="<08.> Indique el valor de verdad.",
                        source={"problem_number": 8, "page_number": 1},
                        normalized={"continuaciones_fusionadas": [{"record_id": "crop_002"}]},
                    ),
                    StagingProblemRecord(
                        record_id="crop_002",
                        crop_id="crop_002",
                        crop_path=str(cont_crop),
                        raw_ocr="A) FVVF B) FFVF C) FFFF D) FVFV E) FFVF",
                        source={"page_number": 1, "box_index": 2},
                        normalized={
                            "continuacion": {
                                "es_continuacion": True,
                                "fusionar_con_anterior": True,
                                "parent_record_id": "crop_001",
                            }
                        },
                    ),
                ]
            )
            service = InstancePdfPipelineService(context, staging_store=store)
            service.models.normalizer = "Jhoan12/fake-normalizer"

            with patch("modulos.instance_factory.pipeline.HfOcrNormalizerClient", FakeNormalizerClient):
                service.normalize_with_ai("crop_001")

            sent = FakeNormalizerClient.calls[0]
            self.assertIn("Indique el valor", sent["raw_ocr"])
            self.assertIn("FVVF", sent["raw_ocr"])
            self.assertEqual(len(sent["continuations"]), 1)
            self.assertEqual(sent["continuations"][0]["record_id"], "crop_002")
            self.assertEqual(len(sent["images"]), 2)

    def test_normalize_with_ai_rejects_explicit_continuation_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            crop = Path(tmp) / "crop_cont.png"
            crop.write_bytes(b"png")
            context = InstancePipelineContext(book_code="GEO", instance_type="s03")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            store.upsert_record(
                StagingProblemRecord(
                    record_id="crop_cont",
                    crop_id="crop_cont",
                    crop_path=str(crop),
                    raw_ocr="<CONT.> A) FVVF B) FFVF C) FFFF D) FVFV E) FFVF",
                    normalized={
                        "continuacion": {
                            "es_continuacion": True,
                            "fusionar_con_anterior": True,
                            "parent_record_id": "crop_prev",
                        }
                    },
                )
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            with self.assertRaisesRegex(ValueError, "continuacion fusionada"):
                service.normalize_with_ai("crop_cont")

    def test_normalize_with_ai_rejects_parent_fused_continuation_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent_crop = Path(tmp) / "parent.png"
            cont_crop = Path(tmp) / "cont.png"
            parent_crop.write_bytes(b"png")
            cont_crop.write_bytes(b"png")
            context = InstancePipelineContext(book_code="GEO", instance_type="s03")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="crop_parent",
                        crop_id="crop_parent",
                        crop_path=str(parent_crop),
                        raw_ocr="<08.> Indique el valor de verdad.",
                        normalized={"continuaciones_fusionadas": [{"record_id": "crop_cont"}]},
                    ),
                    StagingProblemRecord(
                        record_id="crop_cont",
                        crop_id="crop_cont",
                        crop_path=str(cont_crop),
                        raw_ocr="A) FVVF B) FFVF C) FFFF D) FVFV E) FFVF",
                    ),
                ]
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            with self.assertRaisesRegex(ValueError, "continuacion fusionada"):
                service.normalize_with_ai("crop_cont")

    def test_normalize_with_ai_rejects_invalidated_downstream_record(self) -> None:
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
                    raw_ocr="<01.> Determinar x. A) $10$ B) $20$ C) $30$ D) $40$ E) $50$",
                    audit={"downstream_state": {"status": "invalidated", "reason": "page_boxes_changed"}},
                )
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            with self.assertRaisesRegex(ValueError, "Regenera staging"):
                service.normalize_with_ai("crop_001")

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

    def test_promotion_candidate_blocks_raw_cont_marker_without_structured_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(
                book_code="GEO",
                instance_type="s03",
                pdf_path="E:/Banco/geometria.pdf",
            )
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            crop = Path(tmp) / "crop_cont.png"
            crop.write_bytes(b"png")
            store.upsert_record(
                StagingProblemRecord(
                    record_id="crop_cont",
                    crop_id="crop_cont",
                    crop_path=str(crop),
                    status=StageStatus.READY,
                    source={"page_number": 2, "bbox_px": [10, 20, 210, 320]},
                    raw_ocr="[CONT.] A) FVVF B) FFVF C) FFFF D) FVFV E) FFVF",
                    normalized={
                        "status": "listo",
                        "latex_rendered_item": r"\item[\textbf{8.}] A) FVVF B) FFVF",
                    },
                    models={"ocr": "test", "normalizer": "human"},
                    confidence={"ocr": 0.95},
                    review={"review_status": StageStatus.READY},
                )
            )

            candidate = store.build_promotion_candidate("crop_cont")

            self.assertFalse(candidate["ready_for_future_promotion"])
            self.assertIn("continuacion:fusionada_con_anterior", candidate["blocking_issues"])

    def test_promotion_candidate_blocks_invalidated_downstream_even_if_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(
                book_code="GEO",
                instance_type="s03",
                pdf_path="E:/Banco/geometria.pdf",
            )
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            crop = Path(tmp) / "crop.png"
            crop.write_bytes(b"png")
            store.upsert_record(
                StagingProblemRecord(
                    record_id="crop_001",
                    crop_id="crop_001",
                    crop_path=str(crop),
                    status=StageStatus.READY,
                    source={"page_number": 2, "bbox_px": [10, 20, 210, 320]},
                    raw_ocr="<08.> Indique el valor de verdad.",
                    normalized={
                        "status": "listo",
                        "latex_rendered_item": r"\item[\textbf{8.}] [[curso=Geometria]] [[tema=angulos]] Texto.",
                    },
                    models={"ocr": "test", "normalizer": "human"},
                    confidence={"ocr": 0.95},
                    review={"review_status": StageStatus.READY},
                    audit={"downstream_state": {"status": "invalidated", "reason": "page_boxes_changed"}},
                )
            )

            candidate = store.build_promotion_candidate("crop_001")

            self.assertFalse(candidate["ready_for_future_promotion"])
            self.assertIn("source_stale:page_boxes_changed", candidate["blocking_issues"])

    def test_update_review_rejects_invalidated_downstream_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(
                book_code="GEO",
                instance_type="s03",
                pdf_path="E:/Banco/geometria.pdf",
            )
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            crop = Path(tmp) / "crop.png"
            crop.write_bytes(b"png")
            store.upsert_record(
                StagingProblemRecord(
                    record_id="crop_001",
                    crop_id="crop_001",
                    crop_path=str(crop),
                    status=StageStatus.NEEDS_REVIEW,
                    raw_ocr="<08.> Indique el valor de verdad.",
                    audit={"downstream_state": {"status": "invalidated", "reason": "page_boxes_changed"}},
                )
            )

            with self.assertRaisesRegex(ValueError, "Regenera staging"):
                store.update_review(
                    "crop_001",
                    {
                        "status": "listo",
                        "latex_rendered_item": r"\item[\textbf{8.}] [[curso=Geometria]] [[tema=angulos]] Texto.",
                    },
                    mark_ready=True,
                )

    def test_promotion_candidate_blocks_parent_fused_continuation_without_raw_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(
                book_code="GEO",
                instance_type="s03",
                pdf_path="E:/Banco/geometria.pdf",
            )
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            parent_crop = Path(tmp) / "parent.png"
            cont_crop = Path(tmp) / "cont.png"
            parent_crop.write_bytes(b"png")
            cont_crop.write_bytes(b"png")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="crop_parent",
                        crop_id="crop_parent",
                        crop_path=str(parent_crop),
                        status=StageStatus.READY,
                        source={"page_number": 2, "bbox_px": [10, 20, 210, 320]},
                        raw_ocr="<08.> Indique el valor de verdad.",
                        normalized={
                            "status": "listo",
                            "latex_rendered_item": r"\item[\textbf{8.}] Indique...",
                            "continuaciones_fusionadas": [{"record_id": "crop_cont"}],
                        },
                        models={"ocr": "test", "normalizer": "human"},
                        confidence={"ocr": 0.95},
                        review={"review_status": StageStatus.READY},
                    ),
                    StagingProblemRecord(
                        record_id="crop_cont",
                        crop_id="crop_cont",
                        crop_path=str(cont_crop),
                        status=StageStatus.READY,
                        source={"page_number": 2, "bbox_px": [220, 20, 410, 120]},
                        raw_ocr="A) FVVF B) FFVF C) FFFF D) FVFV E) FFVF",
                        normalized={
                            "status": "listo",
                            "latex_rendered_item": r"\item[\textbf{9.}] A) FVVF B) FFVF",
                        },
                        models={"ocr": "test", "normalizer": "human"},
                        confidence={"ocr": 0.95},
                        review={"review_status": StageStatus.READY},
                    ),
                ]
            )

            candidate = store.build_promotion_candidate("crop_cont")

            self.assertFalse(candidate["ready_for_future_promotion"])
            self.assertIn("continuacion:fusionada_con_anterior", candidate["blocking_issues"])

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

    def test_load_records_cache_is_isolated_and_refreshes_on_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="s04")
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            record = StagingProblemRecord(
                record_id="crop_001",
                crop_id="crop_001",
                crop_path=str(Path(tmp) / "crop.png"),
                raw_ocr="uno",
            )
            store.upsert_record(record)

            first = store.load_records()
            second = store.load_records()
            self.assertEqual(first[0].raw_ocr, "uno")
            self.assertEqual(second[0].raw_ocr, "uno")
            self.assertIsNot(first[0], second[0])

            first[0].raw_ocr = "mutado sin guardar"
            self.assertEqual(store.load_records()[0].raw_ocr, "uno")

            record.raw_ocr = "dos"
            store.upsert_record(record)
            self.assertEqual(store.load_records()[0].raw_ocr, "dos")

            path = store._record_path("crop_001")
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["raw_ocr"] = "externo con mas caracteres"
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self.assertEqual(store.load_records()[0].raw_ocr, "externo con mas caracteres")

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

    def test_page_delete_invalidates_downstream_staging_records(self) -> None:
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
                    ),
                    SimpleNamespace(
                        record_id="page_002",
                        page_number=2,
                        boxes=[(5, 6, 45, 55)],
                        reviewed=True,
                        layout_mode="una_columna",
                        detector_source="pdf_factory:test",
                        image_path=page_image,
                    ),
                ]

            def load_instance(self, _name: str):
                return self.rows

            def delete_instance_row(self, _name: str, record_id: str):
                self.rows = [row for row in self.rows if str(row.record_id) != str(record_id)]

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

            remaining_pages = service.delete_page_record("page_001")

            self.assertEqual([row.record_id for row in remaining_pages], ["page_002"])
            loaded = store.get_record("crop_001")
            assert loaded is not None
            self.assertEqual(loaded.crop_path, "")
            self.assertEqual(loaded.raw_ocr, "")
            self.assertEqual(loaded.figure_segmentation, {})
            self.assertEqual(loaded.normalized, {})
            self.assertEqual(loaded.review, {})
            self.assertEqual(loaded.artifacts, {})
            self.assertEqual(loaded.golden_sync, {})
            self.assertEqual(loaded.errors, [])
            self.assertEqual(loaded.step_status(PipelineStep.PAGES), StageStatus.PENDING)
            self.assertEqual(loaded.step_status(PipelineStep.BOXES), StageStatus.PENDING)
            self.assertEqual(loaded.step_status(PipelineStep.CROPS), StageStatus.PENDING)
            self.assertEqual(loaded.step_status(PipelineStep.OCR), StageStatus.PENDING)
            self.assertEqual(loaded.step_status(PipelineStep.SEGMENTATION), StageStatus.PENDING)
            self.assertEqual(loaded.step_status(PipelineStep.NORMALIZATION), StageStatus.PENDING)
            self.assertEqual(loaded.step_status(PipelineStep.REVIEW), StageStatus.PENDING)
            self.assertEqual(loaded.audit["downstream_state"]["status"], "invalidated")
            self.assertEqual(loaded.audit["downstream_state"]["reason"], "page_removed_from_boxes")
            self.assertTrue(loaded.source["source_removed"])

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
            self.assertEqual(loaded.audit["downstream_state"]["status"], "active")
            self.assertEqual(loaded.audit["downstream_state"]["reason"], "crop_source_regenerated_after_change")
            self.assertEqual(loaded.trace["downstream_invalidations"][-1]["reason"], "crop_source_changed")

    def test_materialization_relinks_unchanged_bbox_when_new_box_shifts_crop_ids(self) -> None:
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
                    ("crop_01", 1, 1, [10, 10, 40, 40]),
                    ("crop_02", 2, 2, [50, 10, 80, 40]),
                    ("crop_03", 3, 3, [10, 60, 40, 90]),
                ]
                for crop_id, source_order, box_index, bbox in payloads:
                    (images / f"{crop_id}.png").write_bytes(b"png")
                    payload = {
                        "schema_version": "problem_crop_live_v1",
                        "crop_id": crop_id,
                        "source_pdf_path": "E:/Banco/libro.pdf",
                        "source_page_number": 1,
                        "source_order": source_order,
                        "box_index": box_index,
                        "page_problem_index": box_index,
                        "problem_index": box_index,
                        "bbox_px": bbox,
                        "crop_image_rel": f"images/{crop_id}.png",
                        "source_record_id": "page_0001",
                    }
                    (records / f"{crop_id}.json").write_text(json.dumps(payload), encoding="utf-8")
                return target, ["crop_01", "crop_02", "crop_03"]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = InstancePipelineContext(book_code="ALG01", instance_type="s05", pdf_path="E:/Banco/libro.pdf")
            store = InstanceStagingStore(context, root=root / "staging")
            for crop_id, bbox, raw, number in [
                ("crop_01", [10, 10, 40, 40], "OCR A", "1"),
                ("crop_02", [10, 60, 40, 90], "OCR B", "2"),
            ]:
                crop = root / f"{crop_id}.png"
                crop.write_bytes(b"png")
                record = StagingProblemRecord(
                    record_id=crop_id,
                    crop_id=crop_id,
                    crop_path=str(crop),
                    status=StageStatus.READY,
                    source={
                        "book_code": "ALG01",
                        "instance_type": "s05",
                        "pdf_path": "E:/Banco/libro.pdf",
                        "page_number": 1,
                        "source_record_id": "page_0001",
                        "bbox_px": bbox,
                    },
                    raw_ocr=raw,
                    structured_ocr={"items_total": 1},
                    figure_segmentation={"segments_total": 1},
                    normalized={"numero": number},
                    review={"final_latex": f"final {number}"},
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

            self.assertEqual([record.record_id for record in out], ["crop_01", "crop_02", "crop_03"])
            self.assertEqual(store.get_record("crop_01").raw_ocr, "OCR A")
            inserted = store.get_record("crop_02")
            assert inserted is not None
            self.assertEqual(inserted.raw_ocr, "")
            self.assertEqual(inserted.normalized, {})
            self.assertEqual(inserted.step_status(PipelineStep.OCR), StageStatus.PENDING)
            relinked = store.get_record("crop_03")
            assert relinked is not None
            self.assertEqual(relinked.raw_ocr, "OCR B")
            self.assertEqual(relinked.normalized["numero"], "2")
            self.assertEqual(relinked.review["final_latex"], "final 2")
            self.assertEqual(relinked.source["bbox_px"], [10, 60, 40, 90])
            self.assertEqual(relinked.trace["source_relinks"][-1]["previous_record_id"], "crop_02")

    def test_materialization_keeps_changed_source_invalidated_when_new_crop_is_missing(self) -> None:
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
                records.mkdir(parents=True, exist_ok=True)
                crop_id = "crop_pipeline_001"
                payload = {
                    "schema_version": "problem_crop_live_v1",
                    "crop_id": crop_id,
                    "source_pdf_path": "E:/Banco/libro.pdf",
                    "source_page_number": 4,
                    "bbox_px": [9, 10, 90, 100],
                    "crop_image_rel": f"images/{crop_id}.png",
                    "source_record_id": "page_0004",
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
                source={"page_number": 4, "source_record_id": "page_0004", "bbox_px": [1, 2, 30, 40]},
                raw_ocr="OCR viejo",
                normalized={"numero": "4"},
            )
            record.set_step(PipelineStep.CROPS, StageStatus.READY, "listo")
            record.set_step(PipelineStep.OCR, StageStatus.READY, "listo")
            store.upsert_record(record)
            service = InstancePdfPipelineService(context, golden_controller=FakeGolden(root), staging_store=store)

            service.materialize_crops_to_staging(rows=[])

            loaded = store.get_record("crop_pipeline_001")
            assert loaded is not None
            self.assertEqual(loaded.step_status(PipelineStep.CROPS), StageStatus.ERROR)
            self.assertEqual(loaded.audit["downstream_state"]["status"], "invalidated")
            self.assertEqual(loaded.audit["downstream_state"]["reason"], "crop_source_changed")

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
        self.assertEqual(parse_page_selection("22-25", 50), [22, 23, 24, 25])
        self.assertEqual(parse_page_selection("22\u201350", 60), list(range(22, 51)))
        self.assertEqual(parse_page_selection("50\u201322", 60), list(range(22, 51)))
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

    def test_compact_artifact_dir_name_respects_small_path_budgets(self) -> None:
        record_id = "impecus-jose-meza-barcena-const_aef5f6c63f877da4"

        for max_len in (24, 18, 12, 10, 8, 1):
            with self.subTest(max_len=max_len):
                compact = compact_artifact_dir_name(record_id, max_len=max_len)
                self.assertLessEqual(len(compact), max_len)
                self.assertTrue(compact)

    def test_artifact_dirs_shrink_for_deep_windows_like_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target_root_len = 183
            filler_len = max(12, target_root_len - len(str(base)) - 1)
            deep_root = base / ("x" * filler_len)
            context = InstancePipelineContext(book_code="IMPECUS", instance_type="problemas_propuestos", pdf_path=str(Path(tmp) / "book.pdf"))
            store = InstanceStagingStore(context, root=deep_root)
            record_id = "impecus-jose-meza-barcena-construcciones-triangulos____CONSTRUCC_" + ("x" * 96)

            raw_dir = store.artifact_dir("raw_outputs", record_id, probe_file="figure_segmentation.json")
            structured_path = raw_dir / "structured_ocr.json"
            figure_path = raw_dir / "figure_segmentation.json"

            self.assertNotEqual(raw_dir.name, record_id)
            self.assertLess(len(raw_dir.name), 48)
            self.assertLessEqual(len(str(structured_path)), MAX_ARTIFACT_PATH_LEN_SOFT_LIMIT)
            self.assertLessEqual(len(str(figure_path)), MAX_ARTIFACT_PATH_LEN_SOFT_LIMIT)
            raw_dir.mkdir(parents=True, exist_ok=True)
            structured_path.write_text("{}", encoding="utf-8")
            figure_path.write_text("{}", encoding="utf-8")
            self.assertTrue(structured_path.exists())
            self.assertTrue(figure_path.exists())

    def test_record_files_compact_long_record_ids_without_changing_record_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(Path(tmp) / "book.pdf"))
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            long_record_id = "impecus-jose-meza-barcena-construccio_a7a09462a0____CONSTRUCC_" + ("x" * 120)
            crop = Path(tmp) / "crop.png"
            crop.write_bytes(b"png")

            store.upsert_record(
                StagingProblemRecord(
                    record_id=long_record_id,
                    crop_id=long_record_id,
                    crop_path=str(crop),
                    raw_ocr="<01.> Halle x. A) $1$ B) $2$",
                    source={"page_number": 1, "bbox_px": [1, 2, 30, 40]},
                )
            )

            files = list((Path(tmp) / "staging" / "records").glob("*.json"))
            self.assertEqual(len(files), 1)
            self.assertNotEqual(files[0].stem, long_record_id)
            self.assertLess(len(str(files[0])), 240)

            loaded = store.get_record(long_record_id)
            assert loaded is not None
            self.assertEqual(loaded.record_id, long_record_id)

            updated = store.update_review(long_record_id, {"numero": "1", "latex_rendered_item": "Problema $x$"})
            self.assertEqual(updated.record_id, long_record_id)
            self.assertEqual(store.get_record(long_record_id).normalized["numero"], "1")

    def test_ocr_batch_saves_each_record_but_rewrites_manifest_once(self) -> None:
        class CountingStore(InstanceStagingStore):
            def __init__(self, context: InstancePipelineContext, root: Path) -> None:
                super().__init__(context, root=root)
                self.rewrite_count = 0
                self.load_count = 0

            def load_records(self) -> list[StagingProblemRecord]:
                self.load_count += 1
                return super().load_records()

            def rewrite_manifest(self) -> None:
                self.rewrite_count += 1
                super().rewrite_manifest()

        class FakeExtractor:
            def extract_from_image(self, **kwargs):
                start_n = int(kwargs.get("start_n") or 1)
                return [], f"<{start_n:02d}.> Halle x. A) $1$ B) $2$"

        class FakeScanPipeline:
            def __init__(self, *_args, **_kwargs) -> None:
                self.extractor = FakeExtractor()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(root / "book.pdf"))
            store = CountingStore(context, root=root / "staging")
            crop_ids = []
            for index in range(3):
                crop = root / f"crop_{index}.png"
                crop.write_bytes(b"png")
                record_id = f"crop_{index}"
                crop_ids.append(record_id)
                store.upsert_record(
                    StagingProblemRecord(
                        record_id=record_id,
                        crop_id=record_id,
                        crop_path=str(crop),
                        status=StageStatus.PENDING,
                    )
                )
            store.rewrite_count = 0
            store.load_count = 0
            service = InstancePdfPipelineService(context, staging_store=store)

            with patch("modulos.modulo0_transcriptor.scan_pipeline.pipeline.ScanPipeline", FakeScanPipeline), patch.object(
                service,
                "_validate_ocr_runtime",
                lambda **_kwargs: None,
            ):
                processed = service.run_ocr_and_segmentation(
                    provider="local",
                    record_ids=crop_ids,
                    run_segmentation=False,
                    run_ocr=True,
                )

            self.assertEqual(len(processed), 3)
            self.assertEqual(store.rewrite_count, 1)
            self.assertEqual(store.load_count, 1)
            self.assertEqual([row.raw_ocr for row in store.load_records()], [
                "<01.> Halle x. A) $1$ B) $2$",
                "<02.> Halle x. A) $1$ B) $2$",
                "<03.> Halle x. A) $1$ B) $2$",
            ])

    def test_merge_records_for_ocr_uses_single_effective_image(self) -> None:
        from PIL import Image

        seen_paths: list[Path] = []

        class FakeExtractor:
            def extract_from_image(self, **kwargs):
                seen_paths.append(Path(kwargs["image_path"]))
                return [], "<01.> Problema fusionado. A) $1$ B) $2$"

        class FakeScanPipeline:
            def __init__(self, *_args, **_kwargs) -> None:
                self.extractor = FakeExtractor()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_crop = root / "parent.png"
            child_crop = root / "child.png"
            Image.new("RGB", (120, 40), "white").save(parent_crop)
            Image.new("RGB", (80, 30), "white").save(child_crop)
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_record(StagingProblemRecord(record_id="r1", crop_id="r1", crop_path=str(parent_crop)))
            store.upsert_record(StagingProblemRecord(record_id="r2", crop_id="r2", crop_path=str(child_crop)))
            service = InstancePdfPipelineService(context, staging_store=store)

            updated = service.merge_records_for_ocr("r1", ["r2"])
            merged = next(row for row in updated if str(row.source.get("ocr_input_mode") or "") == "merged_crops_replacement")
            parent = store.get_record("r1")
            child = store.get_record("r2")
            persisted_merged = store.get_record(merged.record_id)

            self.assertEqual([row.record_id for row in updated], [merged.record_id, "r1", "r2"])
            self.assertIsNotNone(parent)
            self.assertIsNotNone(child)
            self.assertIsNotNone(persisted_merged)
            merged_path = Path(str(persisted_merged.crop_path or ""))
            self.assertTrue(merged_path.exists())
            with Image.open(merged_path) as merged:
                self.assertEqual(merged.width, 120)
                self.assertGreater(merged.height, 70)
            self.assertEqual(parent.source.get("replaced_by_record_id"), persisted_merged.record_id)
            self.assertEqual(child.source.get("replaced_by_record_id"), persisted_merged.record_id)
            summary = store.summarize_records()
            self.assertEqual(summary["raw_records_total"], 3)
            self.assertEqual(summary["records_total"], 1)
            self.assertEqual(summary["problems_total"], 1)
            self.assertEqual(summary["primary_records_total"], 1)

            with patch("modulos.modulo0_transcriptor.scan_pipeline.pipeline.ScanPipeline", FakeScanPipeline), patch.object(
                service,
                "_validate_ocr_runtime",
                lambda **_kwargs: None,
            ):
                processed = service.run_ocr_and_segmentation(
                    provider="local",
                    run_segmentation=False,
                    run_ocr=True,
                )

            self.assertEqual(len(processed), 1)
            self.assertEqual(seen_paths, [merged_path])
            self.assertEqual(store.get_record(persisted_merged.record_id).raw_ocr, "<01.> Problema fusionado. A) $1$ B) $2$")
            self.assertFalse(store.get_record("r1").raw_ocr)
            self.assertFalse(store.get_record("r2").raw_ocr)

    def test_materialization_rebuild_removes_previous_merged_crop_records(self) -> None:
        from PIL import Image

        class FakeGolden:
            def __init__(self, root: Path) -> None:
                self.root = root

            def materialize_problem_crops_for_downstream(self, *_args, **_kwargs):
                target = self.root / "problem_crops_live"
                records = target / "records"
                images = target / "images"
                records.mkdir(parents=True, exist_ok=True)
                images.mkdir(parents=True, exist_ok=True)
                crop_ids = ["r1", "r2"]
                for index, crop_id in enumerate(crop_ids, start=1):
                    Image.new("RGB", (120, 40), "white").save(images / f"{crop_id}.png")
                    payload = {
                        "schema_version": "problem_crop_live_v1",
                        "crop_id": crop_id,
                        "source_pdf_path": str(self.root / "book.pdf"),
                        "source_page_number": 1,
                        "source_order": index,
                        "box_index": index,
                        "bbox_px": [10, 10 + index * 50, 130, 45 + index * 50],
                        "crop_image_rel": f"images/{crop_id}.png",
                        "source_record_id": "page_0001",
                    }
                    (records / f"{crop_id}.json").write_text(json.dumps(payload), encoding="utf-8")
                return target, crop_ids

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_crop = root / "parent.png"
            child_crop = root / "child.png"
            Image.new("RGB", (120, 40), "white").save(parent_crop)
            Image.new("RGB", (120, 40), "white").save(child_crop)
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_record(
                StagingProblemRecord(
                    record_id="r1",
                    crop_id="r1",
                    crop_path=str(parent_crop),
                    source={
                        "book_code": "ALG01",
                        "instance_type": "s01",
                        "pdf_path": str(root / "book.pdf"),
                        "page_number": 1,
                        "source_record_id": "page_0001",
                        "bbox_px": [10, 60, 130, 95],
                    },
                )
            )
            store.upsert_record(
                StagingProblemRecord(
                    record_id="r2",
                    crop_id="r2",
                    crop_path=str(child_crop),
                    source={
                        "book_code": "ALG01",
                        "instance_type": "s01",
                        "pdf_path": str(root / "book.pdf"),
                        "page_number": 1,
                        "source_record_id": "page_0001",
                        "bbox_px": [10, 110, 130, 145],
                    },
                )
            )
            service = InstancePdfPipelineService(context, golden_controller=FakeGolden(root), staging_store=store)
            merged_rows = service.merge_records_for_ocr("r1", ["r2"])
            merged_id = next(row.record_id for row in merged_rows if row.record_id not in {"r1", "r2"})
            before_rebuild = store.summarize_records()
            self.assertEqual(before_rebuild["raw_records_total"], 3)
            self.assertEqual(before_rebuild["records_total"], 1)

            rebuilt = service.materialize_crops_to_staging(rows=[])

            self.assertEqual([record.record_id for record in rebuilt], ["r1", "r2"])
            self.assertIsNone(store.get_record(merged_id))
            self.assertEqual([record.record_id for record in store.load_records()], ["r1", "r2"])
            self.assertNotIn("replaced_by_record_id", store.get_record("r1").source)
            self.assertNotIn("replaced_by_record_id", store.get_record("r2").source)
            self.assertNotIn("continuacion", store.get_record("r1").normalized)
            self.assertNotIn("continuacion", store.get_record("r2").normalized)
            summary = store.summarize_records()
            self.assertEqual(summary["raw_records_total"], 2)
            self.assertEqual(summary["records_total"], 2)
            self.assertEqual(summary["crops_found"], 2)

    def test_continuation_scan_ignores_merged_crop_records(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_crop = root / "parent.png"
            child_crop = root / "child.png"
            Image.new("RGB", (120, 40), "white").save(parent_crop)
            Image.new("RGB", (120, 40), "white").save(child_crop)
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_record(StagingProblemRecord(record_id="r1", crop_id="r1", crop_path=str(parent_crop)))
            store.upsert_record(StagingProblemRecord(record_id="r2", crop_id="r2", crop_path=str(child_crop)))
            service = InstancePdfPipelineService(context, staging_store=store)

            service.merge_records_for_ocr("r1", ["r2"])
            scan = service.scan_continuation_candidates(min_confidence=0.1)

            self.assertEqual(scan["summary"]["total_crops"], 0)
            self.assertEqual(scan["candidates"], [])

    def test_detect_continuation_candidates_from_visual_layout_before_ocr(self) -> None:
        from PIL import Image, ImageDraw

        def write_crop(path: Path, *, options: bool = False, paragraph: bool = False) -> None:
            image = Image.new("RGB", (460, 170), "white")
            draw = ImageDraw.Draw(image)
            if paragraph:
                draw.text((18, 18), "17.", fill="black")
                draw.text((58, 18), "En un triangulo ABC se cumple que", fill="black")
                draw.text((58, 42), "AB = AP = PQ y se pide calcular x.", fill="black")
                draw.text((58, 66), "La figura queda cortada debajo.", fill="black")
            if options:
                draw.text((22, 86), "A) 5        B) 8        C) 9", fill="black")
                draw.text((22, 116), "D) 10       E) 12", fill="black")
            image.save(path)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            r1 = root / "r1.png"
            r2 = root / "r2.png"
            r3 = root / "r3.png"
            write_crop(r1, paragraph=True)
            write_crop(r2, options=True)
            write_crop(r3, paragraph=True, options=True)
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            first = StagingProblemRecord(
                record_id="r1",
                crop_id="r1",
                crop_path=str(r1),
                source={
                    "page_number": 1,
                    "source_order": 1,
                    "bbox_px": [10, 10, 400, 600],
                    "continuity_subboxes_checked": True,
                    "continuity_subboxes": [{"class_name": "problem_number", "conf": 1.0}],
                },
            )
            continuation = StagingProblemRecord(
                record_id="r2",
                crop_id="r2",
                crop_path=str(r2),
                source={
                    "page_number": 1,
                    "source_order": 2,
                    "bbox_px": [12, 610, 398, 760],
                    "continuity_subboxes_checked": True,
                    "continuity_subboxes": [{"class_name": "answer_block", "conf": 1.0}],
                },
            )
            next_problem = StagingProblemRecord(
                record_id="r3",
                crop_id="r3",
                crop_path=str(r3),
                source={
                    "page_number": 1,
                    "source_order": 3,
                    "bbox_px": [10, 770, 400, 900],
                    "continuity_subboxes_checked": True,
                    "continuity_subboxes": [
                        {"class_name": "problem_number", "conf": 1.0},
                        {"class_name": "answer_block", "conf": 1.0},
                    ],
                },
            )
            store.upsert_many([first, continuation, next_problem])
            service = InstancePdfPipelineService(context, staging_store=store)

            def fake_aux_ocr(record, cache=None):
                raise AssertionError("continuity decisions must not call auxiliary OCR")

            with patch.dict(os.environ, {"PDF_FACTORY_CONTINUITY_DETECTOR": "0"}, clear=False):
                with patch("modulos.instance_factory.pipeline._auxiliary_continuity_ocr_features", fake_aux_ocr):
                    candidates = service.detect_continuation_candidates(min_confidence=0.35)

            self.assertEqual(candidates[0]["parent_record_id"], "r1")
            self.assertEqual(candidates[0]["continuation_record_id"], "r2")
            self.assertEqual(candidates[0]["features"]["scoring_mode"], "detector_boolean_no_visual_no_ocr")
            self.assertTrue(candidates[0]["features"]["split_multiple_choice_signal"])
            self.assertFalse(candidates[0]["features"]["auxiliary_ocr_available"])
            self.assertTrue(candidates[0]["features"]["parent_detector"]["has_problem_number"])
            self.assertFalse(candidates[0]["features"]["continuation_detector"]["has_problem_number"])
            self.assertTrue(candidates[0]["features"]["continuation_detector"]["has_answer_block"])
            self.assertTrue(candidates[0]["features"]["geometry_confirms_split"])
            self.assertIn(
                "regla fuerte: padre numerado sin alternativas + continuacion sin numero con alternativas",
                candidates[0]["reasons"],
            )
            self.assertEqual(candidates[0]["recommendation"], "merge")

            with patch.dict(os.environ, {"PDF_FACTORY_CONTINUITY_DETECTOR": "0"}, clear=False):
                with patch("modulos.instance_factory.pipeline._auxiliary_continuity_ocr_features", fake_aux_ocr):
                    scan = service.scan_continuation_candidates(min_confidence=0.35)
            self.assertEqual(scan["summary"]["total_crops"], 3)
            self.assertEqual(scan["summary"]["complete_discarded"], 1)
            self.assertEqual(scan["summary"]["possible_parents"], 1)
            self.assertEqual(scan["summary"]["possible_continuations"], 1)
            self.assertEqual(scan["summary"]["merge_recommended"], 1)

    def test_detector_subboxes_can_auto_merge_without_auxiliary_ocr(self) -> None:
        from PIL import Image, ImageDraw

        def write_crop(path: Path, *, options: bool = False, paragraph: bool = False) -> None:
            image = Image.new("RGB", (460, 170), "white")
            draw = ImageDraw.Draw(image)
            if paragraph:
                draw.text((18, 18), "17.", fill="black")
                draw.text((58, 18), "En un triangulo ABC se pide calcular x.", fill="black")
                draw.text((58, 42), "El enunciado continua en la imagen siguiente.", fill="black")
            if options:
                draw.text((22, 86), "A) 5        B) 8        C) 9", fill="black")
                draw.text((22, 116), "D) 10       E) 12", fill="black")
            image.save(path)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_path = root / "parent.png"
            child_path = root / "child.png"
            write_crop(parent_path, paragraph=True)
            write_crop(child_path, options=True)
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="r1",
                        crop_id="r1",
                        crop_path=str(parent_path),
                        source={
                            "page_number": 1,
                            "source_order": 1,
                            "bbox_px": [10, 10, 400, 600],
                            "continuity_subboxes_checked": True,
                            "continuity_subboxes": [{"class_name": "problem_number", "conf": 1.0}],
                        },
                    ),
                    StagingProblemRecord(
                        record_id="r2",
                        crop_id="r2",
                        crop_path=str(child_path),
                        source={
                            "page_number": 1,
                            "source_order": 2,
                            "bbox_px": [12, 610, 398, 760],
                            "continuity_subboxes_checked": True,
                            "continuity_subboxes": [{"class_name": "answer_block", "conf": 1.0}],
                        },
                    ),
                ]
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            with patch.dict(os.environ, {"PDF_FACTORY_CONTINUITY_TESSERACT": "0", "PDF_FACTORY_CONTINUITY_DETECTOR": "0"}, clear=False):
                candidates = service.detect_continuation_candidates(min_confidence=0.35)

            self.assertEqual(candidates[0]["parent_record_id"], "r1")
            self.assertEqual(candidates[0]["continuation_record_id"], "r2")
            self.assertEqual(candidates[0]["recommendation"], "merge")
            self.assertFalse(candidates[0]["features"]["auxiliary_ocr_available"])
            self.assertFalse(candidates[0]["features"]["visual_confirms_split"])
            self.assertTrue(candidates[0]["features"]["detector_confirms_split"])
            self.assertIn("detector v3 confirma", " ".join(candidates[0]["reasons"]))

    def test_top_answer_row_is_not_treated_as_problem_number(self) -> None:
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_path = root / "parent.png"
            child_path = root / "child.png"
            parent = Image.new("RGB", (460, 150), "white")
            parent_draw = ImageDraw.Draw(parent)
            parent_draw.text((18, 22), "06.", fill="black")
            parent_draw.text((58, 22), 'Hallar "x" en:', fill="black")
            parent_draw.text((160, 64), "(a+1)/(x+b) = (a-b)/(a-x)", fill="black")
            parent.save(parent_path)
            child = Image.new("RGB", (460, 150), "white")
            child_draw = ImageDraw.Draw(child)
            child_draw.text((22, 18), "a) (a+b)/(x+b)        b) (a-b)/(a-x)        c) (a+b)/2", fill="black")
            child_draw.text((22, 86), "d) (a-b)/2            e) (a+b)/ab", fill="black")
            child.save(child_path)
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="r1",
                        crop_id="r1",
                        crop_path=str(parent_path),
                        source={
                            "page_number": 1,
                            "source_order": 1,
                            "bbox_px": [10, 10, 400, 160],
                            "continuity_subboxes_checked": True,
                            "continuity_subboxes": [{"class_name": "problem_number", "conf": 1.0}],
                        },
                    ),
                    StagingProblemRecord(
                        record_id="r2",
                        crop_id="r2",
                        crop_path=str(child_path),
                        source={
                            "page_number": 1,
                            "source_order": 2,
                            "bbox_px": [10, 166, 400, 316],
                            "continuity_subboxes_checked": True,
                            "continuity_subboxes": [{"class_name": "answer_block", "conf": 1.0}],
                        },
                    ),
                ]
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            with patch.dict(os.environ, {"PDF_FACTORY_CONTINUITY_TESSERACT": "0", "PDF_FACTORY_CONTINUITY_DETECTOR": "0"}, clear=False):
                child_profile = service._classify_continuation_crop(store.load_records()[1])
                candidates = service.detect_continuation_candidates(min_confidence=0.35)

            self.assertFalse(child_profile["has_number"])
            self.assertTrue(child_profile["has_options"])
            self.assertEqual(candidates[0]["parent_record_id"], "r1")
            self.assertEqual(candidates[0]["continuation_record_id"], "r2")
            self.assertEqual(candidates[0]["recommendation"], "merge")

    def test_visual_number_does_not_block_merge_when_detector_only_sees_answers(self) -> None:
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_path = root / "parent.png"
            child_path = root / "child.png"
            parent = Image.new("RGB", (460, 150), "white")
            parent_draw = ImageDraw.Draw(parent)
            parent_draw.text((18, 22), "52.", fill="black")
            parent_draw.text((58, 22), 'Resolver en "x":', fill="black")
            parent_draw.text((160, 64), "a^2/(a-sqrt(x+b)) = sqrt(a(x+3a)+b)", fill="black")
            parent.save(parent_path)
            child = Image.new("RGB", (460, 210), "white")
            child_draw = ImageDraw.Draw(child)
            child_draw.text((18, 18), "53.", fill="black")
            child_draw.text((58, 18), "Si las soluciones de una ecuacion son alfa y beta.", fill="black")
            child_draw.text((24, 108), "a) -5        b) 2        c) -1", fill="black")
            child_draw.text((24, 148), "d) -3        e) 1", fill="black")
            child.save(child_path)
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="r1",
                        crop_id="r1",
                        crop_path=str(parent_path),
                        source={"page_number": 1, "source_order": 1, "bbox_px": [10, 10, 400, 160]},
                    ),
                    StagingProblemRecord(
                        record_id="r2",
                        crop_id="r2",
                        crop_path=str(child_path),
                        source={"page_number": 1, "source_order": 2, "bbox_px": [10, 166, 400, 376]},
                    ),
                ]
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            def fake_detector(record, cache=None):
                if record.record_id == "r1":
                    return {
                        "available": True,
                        "subbox_detections_total": 1,
                        "has_problem_number": True,
                        "has_answer_block": False,
                        "complete_problem": False,
                        "counts": {"problem": 0, "problem_number": 1, "answer_block": 0},
                    }
                if record.record_id == "r2":
                    return {
                        "available": True,
                        "subbox_detections_total": 1,
                        "has_problem_number": False,
                        "has_answer_block": True,
                        "complete_problem": False,
                        "counts": {"problem": 0, "problem_number": 0, "answer_block": 1},
                    }
                return {"available": False}

            with patch("modulos.instance_factory.pipeline._continuity_detector_features", fake_detector):
                child_profile = service._classify_continuation_crop(store.load_records()[1])
                candidates = service.detect_continuation_candidates(min_confidence=0.1)

            self.assertFalse(child_profile["has_number"])
            self.assertTrue(child_profile["has_options"])
            self.assertEqual(candidates[0]["parent_record_id"], "r1")
            self.assertEqual(candidates[0]["continuation_record_id"], "r2")
            self.assertEqual(candidates[0]["recommendation"], "merge")
            self.assertFalse(candidates[0]["features"]["visual_confirms_split"])

    def test_visual_continuation_scoring_does_not_auto_merge_after_complete_options(self) -> None:
        from PIL import Image, ImageDraw

        def option_crop(path: Path) -> None:
            image = Image.new("RGB", (460, 170), "white")
            draw = ImageDraw.Draw(image)
            draw.text((20, 20), "Calcule x.", fill="black")
            draw.text((22, 86), "A) 1        B) 2        C) 3", fill="black")
            draw.text((22, 116), "D) 4        E) 5", fill="black")
            image.save(path)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_path = root / "parent.png"
            child_path = root / "child.png"
            option_crop(parent_path)
            option_crop(child_path)
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="r1",
                        crop_id="r1",
                        crop_path=str(parent_path),
                        source={"page_number": 1, "source_order": 1, "bbox_px": [10, 10, 400, 180]},
                    ),
                    StagingProblemRecord(
                        record_id="r2",
                        crop_id="r2",
                        crop_path=str(child_path),
                        source={"page_number": 1, "source_order": 2, "bbox_px": [10, 200, 400, 370]},
                        raw_ocr="[CONT.] A) 1 B) 2 C) 3 D) 4 E) 5",
                    ),
                ]
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            with patch.dict(os.environ, {"PDF_FACTORY_CONTINUITY_TESSERACT": "0", "PDF_FACTORY_CONTINUITY_DETECTOR": "0"}, clear=False):
                candidates = service.detect_continuation_candidates(min_confidence=0.35)

            self.assertFalse(any(candidate.get("recommendation") == "merge" for candidate in candidates))
            if candidates:
                self.assertIn("primer crop ya parece contener alternativas completas", candidates[0]["warnings"])

    def test_numbered_crop_with_options_is_not_continuation_candidate(self) -> None:
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_path = root / "parent.png"
            child_path = root / "child.png"
            parent = Image.new("RGB", (460, 210), "white")
            parent_draw = ImageDraw.Draw(parent)
            parent_draw.text((18, 18), "07.", fill="black")
            parent_draw.text((58, 18), "Halle el valor de x.", fill="black")
            parent_draw.text((24, 92), "A) 10        B) 20        C) 30", fill="black")
            parent_draw.text((24, 124), "D) 40        E) 50", fill="black")
            parent.save(parent_path)
            child = Image.new("RGB", (460, 160), "white")
            child_draw = ImageDraw.Draw(child)
            child_draw.text((24, 64), "A) 1        B) 2        C) 3", fill="black")
            child_draw.text((24, 96), "D) 4        E) 5", fill="black")
            child.save(child_path)
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="r1",
                        crop_id="r1",
                        crop_path=str(parent_path),
                        source={"page_number": 1, "source_order": 1, "bbox_px": [10, 10, 400, 220]},
                    ),
                    StagingProblemRecord(
                        record_id="r2",
                        crop_id="r2",
                        crop_path=str(child_path),
                        source={"page_number": 1, "source_order": 2, "bbox_px": [10, 230, 400, 390]},
                    ),
                ]
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            def fake_aux_ocr(record, cache=None):
                return {
                    "available": True,
                    "starts_problem": record.record_id == "r1",
                    "has_options": True,
                    "complete_options": True,
                    "option_labels": ["A", "B", "C", "D", "E"],
                }

            with patch.dict(os.environ, {"PDF_FACTORY_CONTINUITY_DETECTOR": "0"}, clear=False):
                with patch("modulos.instance_factory.pipeline._auxiliary_continuity_ocr_features", fake_aux_ocr):
                    self.assertEqual(service.detect_continuation_candidates(min_confidence=0.1), [])

    def test_visual_continuation_scoring_ignores_raw_ocr_marker_when_geometry_fails(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_path = root / "parent.png"
            child_path = root / "child.png"
            Image.new("RGB", (300, 120), "white").save(parent_path)
            Image.new("RGB", (300, 120), "white").save(child_path)
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="r1",
                        crop_id="r1",
                        crop_path=str(parent_path),
                        source={"page_number": 1, "source_order": 1, "bbox_px": [10, 10, 250, 130]},
                        raw_ocr="<01.> Texto principal",
                    ),
                    StagingProblemRecord(
                        record_id="r2",
                        crop_id="r2",
                        crop_path=str(child_path),
                        source={"page_number": 3, "source_order": 2, "bbox_px": [10, 10, 250, 130]},
                        raw_ocr="[CONT.] A) 1 B) 2 C) 3 D) 4 E) 5",
                    ),
                ]
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            with patch.dict(os.environ, {"PDF_FACTORY_CONTINUITY_DETECTOR": "0"}, clear=False):
                self.assertEqual(service.detect_continuation_candidates(min_confidence=0.1), [])

    def test_detector_subboxes_drive_continuation_candidate_before_ocr(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_path = root / "parent.png"
            child_path = root / "child.png"
            next_path = root / "next.png"
            Image.new("RGB", (460, 170), "white").save(parent_path)
            Image.new("RGB", (460, 150), "white").save(child_path)
            Image.new("RGB", (460, 180), "white").save(next_path)
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="r1",
                        crop_id="r1",
                        crop_path=str(parent_path),
                        source={"page_number": 1, "source_order": 1, "bbox_px": [10, 10, 400, 600]},
                    ),
                    StagingProblemRecord(
                        record_id="r2",
                        crop_id="r2",
                        crop_path=str(child_path),
                        source={"page_number": 1, "source_order": 2, "bbox_px": [12, 610, 398, 760]},
                    ),
                    StagingProblemRecord(
                        record_id="r3",
                        crop_id="r3",
                        crop_path=str(next_path),
                        source={"page_number": 1, "source_order": 3, "bbox_px": [10, 780, 400, 940]},
                    ),
                ]
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            def fake_detector(record, cache=None):
                if record.record_id == "r1":
                    return {
                        "available": True,
                        "subbox_detections_total": 1,
                        "has_problem_number": True,
                        "has_answer_block": False,
                        "complete_problem": False,
                        "counts": {"problem": 1, "problem_number": 1, "answer_block": 0},
                    }
                if record.record_id == "r2":
                    return {
                        "available": True,
                        "subbox_detections_total": 1,
                        "has_problem_number": False,
                        "has_answer_block": True,
                        "complete_problem": False,
                        "counts": {"problem": 0, "problem_number": 0, "answer_block": 1},
                    }
                return {
                    "available": True,
                    "subbox_detections_total": 2,
                    "has_problem_number": True,
                    "has_answer_block": True,
                    "complete_problem": True,
                    "counts": {"problem": 1, "problem_number": 1, "answer_block": 1},
                }

            with patch("modulos.instance_factory.pipeline._continuity_detector_features", fake_detector):
                candidates = service.detect_continuation_candidates(min_confidence=0.35)

            self.assertEqual(candidates[0]["parent_record_id"], "r1")
            self.assertEqual(candidates[0]["continuation_record_id"], "r2")
            self.assertEqual(candidates[0]["recommendation"], "merge")
            self.assertTrue(candidates[0]["features"]["detector_confirms_split"])
            self.assertTrue(candidates[0]["features"]["detector_available"])

    def test_detector_subboxes_allow_lateral_continuation_without_visual_heuristic(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_path = root / "parent.png"
            child_path = root / "child.png"
            Image.new("RGB", (460, 420), "white").save(parent_path)
            Image.new("RGB", (460, 120), "white").save(child_path)
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="r1",
                        crop_id="r1",
                        crop_path=str(parent_path),
                        source={
                            "page_number": 1,
                            "source_order": 1,
                            "bbox_px": [100, 277, 600, 1445],
                            "continuity_subboxes_checked": True,
                            "continuity_subboxes": [{"class_name": "problem_number", "conf": 1.0}],
                        },
                    ),
                    StagingProblemRecord(
                        record_id="r2",
                        crop_id="r2",
                        crop_path=str(child_path),
                        source={
                            "page_number": 1,
                            "source_order": 2,
                            "bbox_px": [900, 296, 1300, 499],
                            "continuity_subboxes_checked": True,
                            "continuity_subboxes": [{"class_name": "answer_block", "conf": 1.0}],
                        },
                    ),
                ]
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            candidates = service.detect_continuation_candidates(min_confidence=0.35)

            self.assertEqual(candidates[0]["parent_record_id"], "r1")
            self.assertEqual(candidates[0]["continuation_record_id"], "r2")
            self.assertEqual(candidates[0]["recommendation"], "merge")
            self.assertTrue(candidates[0]["features"]["split_multiple_choice_signal"])
            self.assertTrue(candidates[0]["features"]["detector_confirms_split"])
            self.assertFalse(candidates[0]["features"]["visual_confirms_split"])
            self.assertIn("detector v3 confirma", " ".join(candidates[0]["reasons"]))

    def test_page_subboxes_drive_continuation_candidate_without_crop_yolo(self) -> None:
        from PIL import Image

        class FakeGolden:
            def __init__(self, page):
                self.page = page

            def load_instance(self, _name):
                return [self.page]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_path = root / "page.png"
            parent_path = root / "parent.png"
            child_path = root / "child.png"
            Image.new("RGB", (500, 500), "white").save(page_path)
            Image.new("RGB", (390, 150), "white").save(parent_path)
            Image.new("RGB", (390, 90), "white").save(child_path)
            page = ProblemPageRecord(
                "p1",
                str(root / "book.pdf"),
                1,
                page_path,
                [(10, 10, 400, 160), (10, 170, 400, 260)],
                detector_detections=[
                    {"bbox_px": [18, 18, 60, 42], "class_name": "problem_number", "conf": 0.9},
                    {"bbox_px": [28, 205, 360, 245], "class_name": "answer_block", "conf": 0.9},
                ],
            )
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="r1",
                        crop_id="r1",
                        crop_path=str(parent_path),
                        source={"page_number": 1, "source_record_id": "p1", "source_order": 1, "bbox_px": [10, 10, 400, 160]},
                    ),
                    StagingProblemRecord(
                        record_id="r2",
                        crop_id="r2",
                        crop_path=str(child_path),
                        source={"page_number": 1, "source_record_id": "p1", "source_order": 2, "bbox_px": [10, 170, 400, 260]},
                    ),
                ]
            )
            service = InstancePdfPipelineService(context, golden_controller=FakeGolden(page), staging_store=store)

            candidates = service.detect_continuation_candidates(min_confidence=0.1)

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["parent_record_id"], "r1")
            self.assertEqual(candidates[0]["continuation_record_id"], "r2")
            self.assertEqual(candidates[0]["recommendation"], "merge")
            self.assertTrue(candidates[0]["features"]["detector_confirms_split"])
            self.assertEqual(candidates[0]["features"]["parent_detector"]["source"], "page_detector_subboxes")
            self.assertEqual(candidates[0]["features"]["continuation_detector"]["source"], "page_detector_subboxes")

    def test_detector_subboxes_override_visual_false_options_for_cross_page_continuation(self) -> None:
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page1_path = root / "page1.png"
            page2_path = root / "page2.png"
            parent_path = root / "parent.png"
            child_path = root / "child.png"

            Image.new("RGB", (2480, 3509), "white").save(page1_path)
            Image.new("RGB", (2480, 3509), "white").save(page2_path)

            parent = Image.new("RGB", (460, 320), "white")
            draw = ImageDraw.Draw(parent)
            draw.text((20, 18), "296.", fill="black")
            draw.text((70, 18), "Se tiene una region sombreada.", fill="black")
            for x1, x2 in ((30, 110), (180, 260), (330, 410)):
                draw.rectangle((x1, 268, x2, 288), fill="black")
            parent.save(parent_path)

            child = Image.new("RGB", (460, 110), "white")
            draw = ImageDraw.Draw(child)
            draw.text((20, 18), "A) 10     B) 20     C) 30", fill="black")
            draw.text((20, 52), "D) 35     E) 40", fill="black")
            child.save(child_path)

            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="r1",
                        crop_id="r1",
                        crop_path=str(parent_path),
                        source={
                            "page_number": 1,
                            "source_order": 5,
                            "bbox_px": [1290, 2356, 2322, 3111],
                            "page_image": str(page1_path),
                            "continuity_subboxes_checked": True,
                            "continuity_subboxes": [
                                {"bbox_px": [1312, 2377, 1751, 2469], "class_name": "problem_number", "conf": 0.84},
                            ],
                        },
                    ),
                    StagingProblemRecord(
                        record_id="r2",
                        crop_id="r2",
                        crop_path=str(child_path),
                        source={
                            "page_number": 2,
                            "source_order": 6,
                            "bbox_px": [139, 308, 1134, 526],
                            "page_image": str(page2_path),
                            "continuity_subboxes_checked": True,
                            "continuity_subboxes": [
                                {"bbox_px": [153, 323, 1116, 508], "class_name": "answer_block", "conf": 1.0},
                            ],
                        },
                    ),
                ]
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            with patch.dict(os.environ, {"PDF_FACTORY_CONTINUITY_DETECTOR": "0"}, clear=False):
                candidates = service.scan_continuation_candidates(min_confidence=0.1)["candidates"]

            self.assertEqual(candidates[0]["parent_record_id"], "r1")
            self.assertEqual(candidates[0]["continuation_record_id"], "r2")
            self.assertEqual(candidates[0]["recommendation"], "merge")
            self.assertTrue(candidates[0]["features"]["detector_confirms_split"])

    def test_continuation_scan_uses_global_source_order_before_page_order(self) -> None:
        from PIL import Image

        class FakeGolden:
            def __init__(self, pages):
                self.pages = pages

            def load_instance(self, _name):
                return list(self.pages)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page1_path = root / "page1.png"
            page2_path = root / "page2.png"
            parent_path = root / "parent.png"
            continuation_path = root / "continuation.png"
            unrelated_path = root / "unrelated.png"
            Image.new("RGB", (1000, 1000), "white").save(page1_path)
            Image.new("RGB", (1000, 1000), "white").save(page2_path)
            Image.new("RGB", (390, 150), "white").save(parent_path)
            Image.new("RGB", (390, 110), "white").save(continuation_path)
            Image.new("RGB", (390, 200), "white").save(unrelated_path)
            page1 = ProblemPageRecord(
                "p1",
                str(root / "book.pdf"),
                1,
                page1_path,
                [(10, 800, 400, 950), (10, 100, 400, 300)],
                detector_detections=[
                    {"bbox_px": [10, 800, 400, 950], "class_name": "problem", "conf": 0.95},
                    {"bbox_px": [18, 810, 70, 842], "class_name": "problem_number", "conf": 0.92},
                    {"bbox_px": [10, 100, 400, 300], "class_name": "problem", "conf": 0.95},
                    {"bbox_px": [18, 108, 70, 140], "class_name": "problem_number", "conf": 0.92},
                    {"bbox_px": [35, 245, 370, 285], "class_name": "answer_block", "conf": 0.92},
                ],
            )
            page2 = ProblemPageRecord(
                "p2",
                str(root / "book.pdf"),
                2,
                page2_path,
                [(10, 10, 400, 120)],
                detector_detections=[
                    {"bbox_px": [10, 10, 400, 120], "class_name": "problem", "conf": 0.95},
                    {"bbox_px": [35, 60, 370, 105], "class_name": "answer_block", "conf": 0.92},
                ],
            )
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="parent",
                        crop_id="parent",
                        crop_path=str(parent_path),
                        source={
                            "page_number": 1,
                            "page_image": str(page1_path),
                            "source_record_id": "p1",
                            "source_order": 44,
                            "bbox_px": [10, 800, 400, 950],
                        },
                    ),
                    StagingProblemRecord(
                        record_id="unrelated",
                        crop_id="unrelated",
                        crop_path=str(unrelated_path),
                        source={
                            "page_number": 1,
                            "page_image": str(page1_path),
                            "source_record_id": "p1",
                            "source_order": 131,
                            "bbox_px": [10, 100, 400, 300],
                        },
                    ),
                    StagingProblemRecord(
                        record_id="continuation",
                        crop_id="continuation",
                        crop_path=str(continuation_path),
                        source={
                            "page_number": 2,
                            "page_image": str(page2_path),
                            "source_record_id": "p2",
                            "source_order": 45,
                            "bbox_px": [10, 10, 400, 120],
                        },
                    ),
                ]
            )
            service = InstancePdfPipelineService(context, golden_controller=FakeGolden([page1, page2]), staging_store=store)

            scan = service.scan_continuation_candidates(min_confidence=0.1)

            self.assertEqual(len(scan["candidates"]), 1)
            self.assertEqual(scan["candidates"][0]["parent_record_id"], "parent")
            self.assertEqual(scan["candidates"][0]["continuation_record_id"], "continuation")
            self.assertEqual(scan["candidates"][0]["features"]["order_gap"], 1)
            self.assertEqual(scan["candidates"][0]["recommendation"], "merge")

    def test_continuation_scan_uses_layout_order_when_box_order_is_wrong(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_path = root / "parent.png"
            child_path = root / "child.png"
            complete_path = root / "complete.png"
            Image.new("RGB", (390, 160), "white").save(parent_path)
            Image.new("RGB", (390, 120), "white").save(child_path)
            Image.new("RGB", (390, 260), "white").save(complete_path)
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="child_options",
                        crop_id="child_options",
                        crop_path=str(child_path),
                        source={
                            "page_number": 1,
                            "source_order": 1,
                            "bbox_px": [450, 100, 850, 220],
                            "continuity_subboxes_checked": True,
                            "continuity_subboxes": [{"class_name": "answer_block", "conf": 1.0}],
                        },
                    ),
                    StagingProblemRecord(
                        record_id="complete_problem",
                        crop_id="complete_problem",
                        crop_path=str(complete_path),
                        source={
                            "page_number": 1,
                            "source_order": 2,
                            "bbox_px": [10, 100, 400, 360],
                            "continuity_subboxes_checked": True,
                            "continuity_subboxes": [
                                {"class_name": "problem_number", "conf": 1.0},
                                {"class_name": "answer_block", "conf": 1.0},
                            ],
                        },
                    ),
                    StagingProblemRecord(
                        record_id="parent_number",
                        crop_id="parent_number",
                        crop_path=str(parent_path),
                        source={
                            "page_number": 1,
                            "source_order": 3,
                            "bbox_px": [10, 620, 400, 780],
                            "continuity_subboxes_checked": True,
                            "continuity_subboxes": [{"class_name": "problem_number", "conf": 1.0}],
                        },
                    ),
                ]
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            scan = service.scan_continuation_candidates(min_confidence=0.1)

            self.assertEqual(len(scan["candidates"]), 1)
            self.assertEqual(scan["candidates"][0]["parent_record_id"], "parent_number")
            self.assertEqual(scan["candidates"][0]["continuation_record_id"], "child_options")
            self.assertEqual(scan["candidates"][0]["features"]["order_basis"], "layout")
            self.assertEqual(scan["candidates"][0]["features"]["source_order_gap"], -2)
            self.assertTrue(scan["candidates"][0]["features"]["split_multiple_choice_signal"])
            self.assertEqual(scan["candidates"][0]["recommendation"], "merge")

    def test_continuation_scan_ignores_number_and_answer_subbox_records(self) -> None:
        from PIL import Image

        class FakeGolden:
            def __init__(self, page):
                self.page = page

            def load_instance(self, _name):
                return [self.page]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_path = root / "page.png"
            problem_path = root / "problem.png"
            number_path = root / "number.png"
            answer_path = root / "answer.png"
            Image.new("RGB", (500, 500), "white").save(page_path)
            Image.new("RGB", (390, 300), "white").save(problem_path)
            Image.new("RGB", (80, 40), "white").save(number_path)
            Image.new("RGB", (340, 60), "white").save(answer_path)
            page = ProblemPageRecord(
                "p1",
                str(root / "book.pdf"),
                1,
                page_path,
                [(10, 10, 400, 310)],
                detector_detections=[
                    {"bbox_px": [10, 10, 400, 310], "class_name": "problem", "conf": 0.95},
                    {"bbox_px": [18, 18, 60, 42], "class_name": "problem_number", "conf": 0.9},
                    {"bbox_px": [28, 245, 360, 295], "class_name": "answer_block", "conf": 0.9},
                ],
            )
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="problem",
                        crop_id="problem",
                        crop_path=str(problem_path),
                        source={"page_number": 1, "source_record_id": "p1", "source_order": 1, "bbox_px": [10, 10, 400, 310]},
                    ),
                    StagingProblemRecord(
                        record_id="number",
                        crop_id="number",
                        crop_path=str(number_path),
                        source={"page_number": 1, "source_record_id": "p1", "source_order": 2, "bbox_px": [18, 18, 60, 42]},
                    ),
                    StagingProblemRecord(
                        record_id="answer",
                        crop_id="answer",
                        crop_path=str(answer_path),
                        source={"page_number": 1, "source_record_id": "p1", "source_order": 3, "bbox_px": [28, 245, 360, 295]},
                    ),
                ]
            )
            service = InstancePdfPipelineService(context, golden_controller=FakeGolden(page), staging_store=store)

            scan = service.scan_continuation_candidates(min_confidence=0.1)

            self.assertEqual(scan["candidates"], [])
            self.assertEqual(scan["summary"]["total_crops"], 1)

    def test_continuation_scan_ignores_records_without_crop_file(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_path = root / "parent.png"
            child_path = root / "child.png"
            Image.new("RGB", (390, 150), "white").save(parent_path)
            Image.new("RGB", (390, 100), "white").save(child_path)
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="parent",
                        crop_id="parent",
                        crop_path=str(parent_path),
                        source={"page_number": 1, "source_order": 1, "bbox_px": [10, 10, 400, 160]},
                    ),
                    StagingProblemRecord(
                        record_id="phantom",
                        crop_id="phantom",
                        crop_path="",
                        source={"page_number": 1, "source_order": 2, "bbox_px": [10, 170, 400, 260]},
                    ),
                    StagingProblemRecord(
                        record_id="child",
                        crop_id="child",
                        crop_path=str(child_path),
                        source={"page_number": 1, "source_order": 3, "bbox_px": [10, 270, 400, 370]},
                    ),
                ]
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            scan = service.scan_continuation_candidates(min_confidence=0.1)

            self.assertEqual(scan["summary"]["total_crops"], 2)
            self.assertNotIn("phantom", {row["parent_record_id"] for row in scan["candidates"]})
            self.assertNotIn("phantom", {row["continuation_record_id"] for row in scan["candidates"]})

    def test_page_subboxes_reject_child_with_own_number_and_options(self) -> None:
        from PIL import Image

        class FakeGolden:
            def __init__(self, page):
                self.page = page

            def load_instance(self, _name):
                return [self.page]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_path = root / "page.png"
            parent_path = root / "parent.png"
            child_path = root / "child.png"
            Image.new("RGB", (500, 500), "white").save(page_path)
            Image.new("RGB", (390, 150), "white").save(parent_path)
            Image.new("RGB", (390, 160), "white").save(child_path)
            page = ProblemPageRecord(
                "p1",
                str(root / "book.pdf"),
                1,
                page_path,
                [(10, 10, 400, 160), (10, 170, 400, 330)],
                detector_detections=[
                    {"bbox_px": [18, 18, 60, 42], "class_name": "problem_number", "conf": 0.9},
                    {"bbox_px": [18, 178, 60, 202], "class_name": "problem_number", "conf": 0.9},
                    {"bbox_px": [28, 260, 360, 310], "class_name": "answer_block", "conf": 0.9},
                ],
            )
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="r1",
                        crop_id="r1",
                        crop_path=str(parent_path),
                        source={"page_number": 1, "source_record_id": "p1", "source_order": 1, "bbox_px": [10, 10, 400, 160]},
                    ),
                    StagingProblemRecord(
                        record_id="r2",
                        crop_id="r2",
                        crop_path=str(child_path),
                        source={"page_number": 1, "source_record_id": "p1", "source_order": 2, "bbox_px": [10, 170, 400, 330]},
                    ),
                ]
            )
            service = InstancePdfPipelineService(context, golden_controller=FakeGolden(page), staging_store=store)

            self.assertEqual(service.detect_continuation_candidates(min_confidence=0.1), [])

    def test_detector_rejects_child_with_own_problem_number(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_path = root / "parent.png"
            child_path = root / "child.png"
            Image.new("RGB", (460, 170), "white").save(parent_path)
            Image.new("RGB", (460, 150), "white").save(child_path)
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="r1",
                        crop_id="r1",
                        crop_path=str(parent_path),
                        source={"page_number": 1, "source_order": 1, "bbox_px": [10, 10, 400, 600]},
                    ),
                    StagingProblemRecord(
                        record_id="r2",
                        crop_id="r2",
                        crop_path=str(child_path),
                        source={"page_number": 1, "source_order": 2, "bbox_px": [12, 610, 398, 760]},
                    ),
                ]
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            def fake_detector(record, cache=None):
                return {
                    "available": True,
                    "subbox_detections_total": 2,
                    "has_problem_number": True,
                    "has_answer_block": record.record_id == "r2",
                    "complete_problem": record.record_id == "r2",
                    "counts": {"problem": 1, "problem_number": 1, "answer_block": int(record.record_id == "r2")},
                }

            with patch("modulos.instance_factory.pipeline._continuity_detector_features", fake_detector):
                self.assertEqual(service.detect_continuation_candidates(min_confidence=0.1), [])

    def test_detector_rejects_number_only_parent_when_child_has_number_and_options(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_path = root / "parent.png"
            child_path = root / "child.png"
            Image.new("RGB", (460, 120), "white").save(parent_path)
            Image.new("RGB", (460, 180), "white").save(child_path)
            context = InstancePipelineContext(book_code="ALG01", instance_type="s01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="r1",
                        crop_id="r1",
                        crop_path=str(parent_path),
                        source={"page_number": 1, "source_order": 1, "bbox_px": [10, 10, 400, 130]},
                    ),
                    StagingProblemRecord(
                        record_id="r2",
                        crop_id="r2",
                        crop_path=str(child_path),
                        source={"page_number": 1, "source_order": 2, "bbox_px": [12, 135, 398, 315]},
                    ),
                ]
            )
            service = InstancePdfPipelineService(context, staging_store=store)

            def fake_detector(record, cache=None):
                if record.record_id == "r1":
                    return {
                        "available": True,
                        "subbox_detections_total": 1,
                        "has_problem_number": True,
                        "has_answer_block": False,
                        "complete_problem": False,
                        "counts": {"problem": 1, "problem_number": 1, "answer_block": 0},
                    }
                return {
                    "available": True,
                    "subbox_detections_total": 2,
                    "has_problem_number": True,
                    "has_answer_block": True,
                    "complete_problem": True,
                    "counts": {"problem": 1, "problem_number": 1, "answer_block": 1},
                }

            with patch("modulos.instance_factory.pipeline._continuity_detector_features", fake_detector):
                self.assertEqual(service.detect_continuation_candidates(min_confidence=0.1), [])

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
