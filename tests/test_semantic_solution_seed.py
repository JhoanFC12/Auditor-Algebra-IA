from __future__ import annotations

import json
import unittest

from modulos.semantic_solution_seed import (
    build_solution_semantic_seed,
    solution_entries_from_payload,
    solution_entry_from_table_row,
)
from tools.validate_semantic_descriptor_contracts import load_schemas, validate_payload


class SemanticSolutionSeedTests(unittest.TestCase):
    def test_solution_entries_from_payload_ignores_image_only_references(self) -> None:
        entries = solution_entries_from_payload(json.dumps([["C:/tmp/sol_01.png"]]), source="test")
        self.assertEqual(entries, [])

    def test_solution_entries_from_payload_extracts_dict_solution(self) -> None:
        entries = solution_entries_from_payload(
            [
                {
                    "metodo_nombre": "Suma de angulos",
                    "solucion_latex": r"Aplicamos $\alpha+\beta=180^\circ$ y despejamos $x$.",
                    "autor_ia": "GPT",
                }
            ],
            source="problemas.soluciones",
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["solution_path_id"], "sol_01")
        self.assertEqual(entries[0]["method"], "Suma de angulos")
        self.assertIn("despejamos", entries[0]["solution_text_latex"])

    def test_solution_entry_from_table_row_uses_normalized_columns(self) -> None:
        entry = solution_entry_from_table_row(
            {
                "orden": 2,
                "metodo_nombre": "Despeje",
                "solucion_latex": r"De $2x=10$, $x=5$.",
                "autor_ia": "docente",
            }
        )

        self.assertIsNotNone(entry)
        self.assertEqual(entry["solution_path_id"], "sol_02")
        self.assertEqual(entry["method"], "Despeje")

    def test_build_solution_seed_matches_contract(self) -> None:
        entry = {
            "solution_path_id": "sol_01",
            "method": "Despeje",
            "solution_text_latex": r"De $2x=10$, se obtiene $x=5$.",
            "author": "docente",
            "source": "soluciones",
        }
        profile = build_solution_semantic_seed(
            problem_id=99,
            entry=entry,
            problem_source="libro_demo s01",
            figure_tags=["img-99"],
        )

        self.assertEqual(profile["schema_version"], "solution_semantic_profile_v1")
        self.assertEqual(profile["problem_id"], "99")
        self.assertEqual(profile["source"]["kind"], "human_solution")
        self.assertTrue(profile["evidence"]["uses_figure"])
        self.assertIn("despeje", profile["representation"]["canonical_solution_type"])
        schemas, errors = load_schemas()
        self.assertEqual(errors, [])
        issue = validate_payload(__file__, profile, schemas)
        self.assertTrue(issue.valid, issue.errors)


if __name__ == "__main__":
    unittest.main()
