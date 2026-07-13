from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.audit_factory_model_inventory import classify_stage, is_windows_or_unc_path, render_markdown as render_model_markdown
from tools.audit_factory_model_inventory import ModelInventoryAudit
from tools.audit_factory_staging_paths import (
    StagingPathAudit,
    audit_instance_dir,
    build_audit,
    classify_path_value,
    discover_instance_dirs,
    extract_path_values,
    render_markdown as render_staging_markdown,
    staging_roots_from_library_rows,
)
from modulos.instance_factory.model_inventory import build_server_model_configuration
from modulos.instance_factory.models import ModelDefaults, ModelStageTrace
from modulos.instance_factory.server_storage import ServerStorageResolver


class ServerFactoryModelInventoryTests(unittest.TestCase):
    def test_windows_or_unc_detection(self):
        self.assertTrue(is_windows_or_unc_path(r"E:\Github\Auditor-IA\models\best.pt"))
        self.assertTrue(is_windows_or_unc_path(r"\\server\share\best.pt"))
        self.assertFalse(is_windows_or_unc_path("/srv/mathcontentstudio/models/best.pt"))

    def test_hf_ocr_stage_is_server_ready(self):
        row = classify_stage(
            {
                "stage": "ocr",
                "provider": "huggingface",
                "model_id": "Jhoan12/math-ocr-model",
                "source": "env:HF_MODEL",
            }
        )
        self.assertTrue(row.server_ready)
        self.assertEqual(row.server_action, "keep_hugging_face_endpoint")

    def test_windows_detector_stage_requires_copy(self):
        row = classify_stage(
            {
                "stage": "pdf_detector",
                "provider": "local",
                "model_id": r"E:\Github\Auditor-IA\models\detector\best.pt",
                "resolved_path": r"E:\Github\Auditor-IA\models\detector\best.pt",
                "source": "env:PDF_PROBLEM_MODEL",
            }
        )
        self.assertFalse(row.server_ready)
        self.assertEqual(row.server_action, "copy_model_to_server_storage_and_repoint_env")

    def test_model_markdown_contains_required_actions(self):
        row = classify_stage({"stage": "ocr", "provider": "huggingface", "model_id": "repo/model"})
        rendered = render_model_markdown(
            ModelInventoryAudit(
                generated_at="2026-07-06T00:00:00+00:00",
                env_sources_loaded={},
                stages=[row],
                candidates_total=1,
                warnings=[],
            )
        )
        self.assertIn("# Server Factory Model Inventory", rendered)
        self.assertIn("keep_hugging_face_endpoint", rendered)

    def test_server_model_configuration_marks_windows_detector_not_ready(self):
        defaults = ModelDefaults(
            pdf_detector=r"E:\Github\Auditor-IA\models\detector\best.pt",
            ocr="Jhoan12/math-ocr-model",
            figure_segmenter="/srv/mathcontentstudio/models/fig.pt",
            stages={
                "pdf_detector": ModelStageTrace(
                    stage="pdf_detector",
                    model_id=r"E:\Github\Auditor-IA\models\detector\best.pt",
                    provider="local",
                    resolved_path=r"E:\Github\Auditor-IA\models\detector\best.pt",
                ),
                "ocr": ModelStageTrace(stage="ocr", model_id="Jhoan12/math-ocr-model", provider="huggingface"),
            },
        )
        payload = build_server_model_configuration(defaults, storage=ServerStorageResolver(root="/srv/mathcontentstudio"))
        rows = {row["stage"]: row for row in payload["stages"]}
        self.assertFalse(rows["pdf_detector"]["server_ready"])
        self.assertEqual(rows["pdf_detector"]["action"], "copy_model_to_server_storage_and_repoint_env")
        self.assertTrue(rows["ocr"]["server_ready"])
        self.assertFalse(payload["summary"]["server_ready"])


class ServerFactoryStagingPathInventoryTests(unittest.TestCase):
    def test_classify_path_value(self):
        self.assertEqual(classify_path_value(r"D:\Banco\a.pdf"), "windows_or_unc")
        self.assertEqual(classify_path_value("/srv/mathcontentstudio/library/a.pdf"), "server_storage")
        self.assertEqual(classify_path_value("https://nexumathjf.com/a.pdf"), "url")
        self.assertEqual(classify_path_value("crops/a.png"), "relative_or_identifier")

    def test_extract_path_values_nested(self):
        payload = {
            "source": {"pdf_path": r"E:\Banco\source.pdf"},
            "figure": {"segments": [{"image_path": "segments/seg_01.png"}]},
            "title": "not a path",
        }
        found = extract_path_values(payload)
        keys = {key for key, _value, _category in found}
        self.assertIn("source.pdf_path", keys)
        self.assertIn("figure.segments[0].image_path", keys)
        self.assertNotIn("title", keys)

    def test_discover_and_audit_instance_dir(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            instance = root / "book__instance"
            records = instance / "records"
            records.mkdir(parents=True)
            (instance / "manifest.json").write_text(json.dumps({"staging_root": r"E:\Banco\staging"}), encoding="utf-8")
            (records / "r1.json").write_text(
                json.dumps({"crop_path": "crops/r1.png", "source": {"pdf_path": r"E:\Banco\a.pdf"}}),
                encoding="utf-8",
            )

            discovered = discover_instance_dirs([root])
            self.assertEqual(discovered, [instance])
            summary, samples = audit_instance_dir(instance, sample_limit=10, max_record_files=10)

        self.assertEqual(summary.instance, "book__instance")
        self.assertEqual(summary.record_files_total, 1)
        self.assertGreaterEqual(summary.windows_or_unc_values, 2)
        self.assertGreaterEqual(len(samples), 2)

    def test_staging_markdown_contains_summary(self):
        audit = StagingPathAudit(
            generated_at="2026-07-06T00:00:00+00:00",
            roots=[".cache/transcriptor_runs/staging"],
            instances=[],
            samples=[],
            warnings=[],
        )
        rendered = render_staging_markdown(audit)
        self.assertIn("# Server Factory Staging Path Inventory", rendered)
        self.assertIn("Required Server Actions", rendered)

    def test_build_audit_warns_missing_root(self):
        audit = build_audit(roots=[Path("Z:/definitely/missing/root")], sample_limit=5)
        self.assertEqual(len(audit.instances), 0)
        self.assertEqual(len(audit.warnings), 1)

    def test_staging_roots_from_library_rows_uses_session_path(self):
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw) / "book"
            session = workspace / "sessions" / "propuestos.session.json"
            session.parent.mkdir(parents=True)
            session.write_text("{}", encoding="utf-8")
            roots = staging_roots_from_library_rows(
                [{"codigo_instancia": "propuestos", "session_path": str(session), "book_workspace_dir": ""}]
            )
        self.assertEqual(len(roots), 1)
        self.assertTrue(str(roots[0]).endswith(str(Path("temporales") / "propuestos" / "datasets" / "pdf_factory_staging")))


if __name__ == "__main__":
    unittest.main()
