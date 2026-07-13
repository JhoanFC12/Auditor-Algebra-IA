from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from modulos.instance_factory.model_inventory import (
    build_server_model_inventory,
    select_server_model_path,
)
from modulos.instance_factory.models import ModelDefaults, ModelStageTrace
from modulos.instance_factory.server_storage import ServerStorageResolver


def _model_defaults(*, pdf_model: Path, figure_model: Path) -> ModelDefaults:
    return ModelDefaults(
        pdf_detector=str(pdf_model),
        ocr="Jhoan12/math-ocr-qwen2.5-vl-3b-geometry-agent",
        figure_segmenter=str(figure_model),
        normalizer="normalizer_v0_passthrough",
        stages={
            "pdf_detector": ModelStageTrace(
                stage="pdf_detector",
                model_id=str(pdf_model),
                provider="local",
                resolved_path=str(pdf_model),
                source="env:PDF_PROBLEM_MODEL",
            ),
            "ocr": ModelStageTrace(
                stage="ocr",
                model_id="Jhoan12/math-ocr-qwen2.5-vl-3b-geometry-agent",
                provider="huggingface",
                source="env:HF_MODEL",
            ),
            "figure_segmenter": ModelStageTrace(
                stage="figure_segmenter",
                model_id=str(figure_model),
                provider="local",
                resolved_path=str(figure_model),
                source="env:YOLO_FIGURE_SEGMENT_MODEL",
            ),
            "normalizer": ModelStageTrace(
                stage="normalizer",
                model_id="normalizer_v0_passthrough",
                provider="local_passthrough",
                source="pipeline_passthrough",
            ),
        },
    )


class InstanceFactoryModelInventoryTests(unittest.TestCase):
    def test_server_model_inventory_marks_required_local_models_ready(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            pdf_model = root / "models" / "problem" / "best.pt"
            figure_model = root / "models" / "figure" / "best.pt"
            pdf_model.parent.mkdir(parents=True)
            figure_model.parent.mkdir(parents=True)
            pdf_model.write_text("pdf", encoding="utf-8")
            figure_model.write_text("fig", encoding="utf-8")
            defaults = _model_defaults(pdf_model=pdf_model, figure_model=figure_model)

            payload = build_server_model_inventory(defaults, storage=ServerStorageResolver(root=root))
            rows = payload["stage_map"]

        self.assertTrue(payload["summary"]["server_ready"])
        self.assertEqual(payload["summary"]["missing_required_stages"], [])
        self.assertTrue(rows["pdf_detector"]["server_ready"])
        self.assertTrue(rows["figure_segmenter"]["server_ready"])
        self.assertTrue(rows["number_alt_detector"]["server_ready"])
        self.assertEqual(rows["number_alt_detector"]["metadata"]["derived_from"], "pdf_detector")
        self.assertEqual(rows["number_alt_detector"]["resolved_path"], rows["pdf_detector"]["resolved_path"])

    def test_missing_server_model_reports_actionable_error(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            pdf_model = root / "models" / "problem" / "missing.pt"
            figure_model = root / "models" / "figure" / "best.pt"
            figure_model.parent.mkdir(parents=True)
            figure_model.write_text("fig", encoding="utf-8")
            defaults = _model_defaults(pdf_model=pdf_model, figure_model=figure_model)
            resolver = ServerStorageResolver(root=root)

            payload = build_server_model_inventory(defaults, storage=resolver)
            rows = payload["stage_map"]

            with self.assertRaises(FileNotFoundError):
                select_server_model_path("pdf_detector", defaults, storage=resolver, allow_not_ready=False)
            with self.assertRaises(FileNotFoundError):
                select_server_model_path("number_alt_detector", defaults, storage=resolver, allow_not_ready=False)

        self.assertFalse(payload["summary"]["server_ready"])
        self.assertEqual(rows["pdf_detector"]["action"], "server_model_file_missing")
        self.assertEqual(rows["number_alt_detector"]["action"], "server_model_file_missing")
        self.assertIn("pdf_detector", payload["summary"]["missing_required_stages"])
        self.assertIn("number_alt_detector", payload["summary"]["missing_required_stages"])
        self.assertTrue(rows["ocr"]["server_ready"])


if __name__ == "__main__":
    unittest.main()
