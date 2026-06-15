from __future__ import annotations

import unittest

from modulos.semantic_figure_seed import build_figure_semantic_seed, figure_embedding_text, normalize_figure_tag
from tools.validate_semantic_descriptor_contracts import load_schemas, validate_payload


class SemanticFigureSeedTests(unittest.TestCase):
    def test_normalize_figure_tag_accepts_paths_and_extensions(self) -> None:
        self.assertEqual(normalize_figure_tag(r"C:\tmp\img-22.png"), "img-22")
        self.assertEqual(normalize_figure_tag("img-7"), "img-7")

    def test_build_figure_seed_matches_contract(self) -> None:
        profile = build_figure_semantic_seed(
            problem_id=22,
            figure_tag="img-22.png",
            course="Geometria",
            topic="Triangulos",
            asset_path=r"C:\data\img-22.png",
        )

        self.assertEqual(profile["schema_version"], "geometry_figure_description_v1")
        self.assertEqual(profile["source_record_id"], "22")
        self.assertEqual(profile["figure_tag"], "img-22")
        self.assertEqual(profile["figure_type"], "geometria_plana")
        self.assertEqual(profile["primitives"]["segments"], [])
        self.assertIn("semilla", " ".join(profile["warnings"]).lower())
        self.assertIn("img-22", figure_embedding_text(profile))
        schemas, errors = load_schemas()
        self.assertEqual(errors, [])
        issue = validate_payload(__file__, profile, schemas)
        self.assertTrue(issue.valid, issue.errors)


if __name__ == "__main__":
    unittest.main()
