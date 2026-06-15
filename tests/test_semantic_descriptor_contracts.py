from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.validate_semantic_descriptor_contracts import validate_paths


ROOT = Path(__file__).resolve().parents[1]


class SemanticDescriptorContractTests(unittest.TestCase):
    def test_validates_versioned_examples(self) -> None:
        report = validate_paths([ROOT / "docs/examples/semantic_descriptor"])

        self.assertEqual(report.manifest["invalid_total"], 0)
        self.assertEqual(report.manifest["valid_total"], 4)
        self.assertEqual(
            sorted(item.schema_version for item in report.issues),
            [
                "geometry_figure_description_v1",
                "problem_semantic_profile_v1",
                "problem_semantic_profile_v1",
                "solution_semantic_profile_v1",
            ],
        )

    def test_reports_missing_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad_profile.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "problem_semantic_profile_v1",
                        "problem_id": "bad",
                    }
                ),
                encoding="utf-8",
            )

            report = validate_paths([path])

            self.assertEqual(report.manifest["valid_total"], 0)
            self.assertEqual(report.manifest["invalid_total"], 1)
            self.assertFalse(report.issues[0].valid)
            self.assertIn("schema_validation:", report.issues[0].errors[0])

    def test_reports_unknown_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unknown.json"
            path.write_text(json.dumps({"schema_version": "future_v9"}), encoding="utf-8")

            report = validate_paths([path])

            self.assertEqual(report.manifest["invalid_total"], 1)
            self.assertEqual(report.issues[0].errors, ["unknown:schema_version:future_v9"])

    def test_database_schema_declares_semantic_layer_tables(self) -> None:
        schema_sql = (ROOT / "database/schema.sql").read_text(encoding="utf-8")

        for table_name in (
            "problema_assets",
            "conceptos_matematicos",
            "problema_concepto",
            "problem_semantic_profiles",
            "problem_figure_profiles",
            "solution_semantic_profiles",
            "problem_embeddings",
            "problem_similarity_edges",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table_name}", schema_sql)


if __name__ == "__main__":
    unittest.main()
