from __future__ import annotations

import unittest

from modulos.semantic_practice_recommendation import build_semantic_practice_draft


class SemanticPracticeRecommendationTests(unittest.TestCase):
    def test_build_practice_draft_prioritizes_teacher_accepted_edges(self) -> None:
        payload = {
            "problem_id": 10,
            "model_id": "semantic_similarity_seed_v1",
            "problem": {
                "id": 10,
                "curso": "Geometria",
                "tema": "Triangulos",
                "enunciado_latex": "Calcular x.",
            },
            "similar": [
                {
                    "target_problem_id": 11,
                    "score": 0.95,
                    "status": "rechazado",
                    "human_verified": True,
                    "reason": "Falso positivo",
                    "problem": {"id": 11, "curso": "Geometria", "tema": "Circunferencia"},
                },
                {
                    "target_problem_id": 12,
                    "score": 0.72,
                    "status": "sin_revisar",
                    "human_verified": False,
                    "reason": "Mismo tema",
                    "problem": {
                        "id": 12,
                        "curso": "Geometria",
                        "tema": "Triangulos",
                        "enunciado_latex": r"\item[\textbf{99.}] Halle $x$.",
                    },
                },
                {
                    "target_problem_id": 13,
                    "score": 0.68,
                    "status": "aceptado",
                    "human_verified": True,
                    "reason": "Misma propiedad",
                    "problem": {
                        "id": 13,
                        "curso": "Geometria",
                        "tema": "Triangulos",
                        "enunciado_latex": "Calcule el angulo pedido.",
                    },
                },
            ],
        }

        draft = build_semantic_practice_draft(payload, target_count=5)

        self.assertEqual(draft["schema_version"], "semantic_practice_draft_v1")
        self.assertEqual(draft["title"], "Practica de refuerzo: Geometria / Triangulos")
        self.assertEqual(draft["count"], 2)
        self.assertEqual(draft["recommendations"][0]["problem_id"], 13)
        self.assertEqual(draft["recommendations"][0]["role"], "refuerzo_validado")
        self.assertEqual(draft["recommendations"][1]["problem_id"], 12)
        self.assertEqual(draft["excluded"]["rejected"], 1)
        self.assertIn(r"\begin{enumerate}", draft["practice_latex"])
        self.assertIn(r"\item[\textbf{1.}] Calcule el angulo pedido.", draft["practice_latex"])
        self.assertIn(r"\item[\textbf{2.}] Halle $x$.", draft["practice_latex"])


if __name__ == "__main__":
    unittest.main()
