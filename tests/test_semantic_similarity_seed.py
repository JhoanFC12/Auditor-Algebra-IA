from __future__ import annotations

import unittest

from modulos.semantic_similarity_seed import (
    extract_problem_similarity_features,
    rank_similar_problems,
    score_problem_similarity,
)


def geometry_profile(problem_id: int, *, topic: str = "Triangulos", statement: str = "Calcular x en un triangulo con angulos marcados.") -> dict:
    return {
        "schema_version": "problem_semantic_profile_v1",
        "problem_id": str(problem_id),
        "modality": "text_image",
        "course": "Geometria",
        "topic": topic,
        "subtopic": "Angulos",
        "statement_summary": statement,
        "concepts": ["triangulos", "angulos", "relaciones angulares"],
        "solution_methods": ["relaciones angulares"],
        "solution_concepts": ["suma de angulos internos"],
        "alternative_solution_paths": [],
        "skills": ["leer grafico", "plantear relacion geometrica"],
        "objects": {"geometry": ["triangulo", "angulo"], "algebra": ["ecuacion simple"], "arithmetic": []},
        "given_conditions": [statement],
        "unknowns": ["x"],
        "representation": {
            "embedding_text": f"Geometria. {topic}. {statement}",
            "statement_embedding_text": statement,
            "figure_embedding_text": "Grafico de triangulo con angulos marcados.",
            "solution_embedding_text": "Usa suma de angulos internos y despeje.",
            "search_keywords": ["geometria", "triangulos", "angulos", "grafico"],
            "canonical_problem_type": "calculo_de_angulo_con_grafico",
        },
        "difficulty": {
            "estimated_level": 2,
            "scale": "1-5",
            "signals": {
                "steps_estimated": 2,
                "requires_graph_reading": True,
                "requires_formula_memory": False,
                "requires_multi_case_reasoning": False,
                "requires_algebraic_manipulation": "low",
            },
            "reason": "Lectura grafica directa.",
        },
        "evidence": {"uses_text": True, "uses_figure": True, "figure_tags": ["img-1"], "source_fields": ["test"]},
        "review": {"status": "sin_revisar", "human_verified": False, "notes": ""},
    }


def algebra_profile(problem_id: int) -> dict:
    profile = geometry_profile(problem_id, topic="Ecuaciones", statement="Resolver la ecuacion lineal 2x + 5 = 17.")
    profile["course"] = "Algebra"
    profile["modality"] = "text_only"
    profile["concepts"] = ["ecuaciones lineales", "despeje"]
    profile["solution_concepts"] = ["despeje algebraico"]
    profile["skills"] = ["manipular ecuaciones"]
    profile["objects"] = {"geometry": [], "algebra": ["ecuacion"], "arithmetic": ["suma", "resta"]}
    profile["representation"]["embedding_text"] = "Algebra. Ecuaciones lineales. Resolver 2x + 5 = 17."
    profile["representation"]["statement_embedding_text"] = "Resolver la ecuacion lineal 2x + 5 = 17."
    profile["representation"]["figure_embedding_text"] = ""
    profile["representation"]["solution_embedding_text"] = "Despejar x mediante resta y division."
    profile["representation"]["canonical_problem_type"] = "ecuacion_lineal"
    profile["evidence"] = {"uses_text": True, "uses_figure": False, "figure_tags": [], "source_fields": ["test"]}
    return profile


class SemanticSimilaritySeedTests(unittest.TestCase):
    def test_scores_close_geometry_pair_above_unrelated_algebra(self) -> None:
        base = extract_problem_similarity_features(geometry_profile(1))
        close = extract_problem_similarity_features(
            geometry_profile(2, statement="Halle x usando los angulos marcados en un triangulo.")
        )
        far = extract_problem_similarity_features(algebra_profile(3))

        close_score = score_problem_similarity(base, close)
        far_score = score_problem_similarity(base, far)

        self.assertGreater(close_score["score"], far_score["score"])
        self.assertGreater(close_score["score"], 0.35)
        self.assertIn("mismo curso", close_score["reason"])
        self.assertIn("angulos", close_score["shared_concepts"])

    def test_rank_similar_problems_returns_top_k_per_source(self) -> None:
        features = [
            extract_problem_similarity_features(geometry_profile(1)),
            extract_problem_similarity_features(geometry_profile(2)),
            extract_problem_similarity_features(algebra_profile(3)),
        ]

        edges = rank_similar_problems(features, top_k=1, threshold=0.10)

        self.assertEqual(len([edge for edge in edges if edge["source_problem_id"] == "1"]), 1)
        first = next(edge for edge in edges if edge["source_problem_id"] == "1")
        self.assertEqual(first["target_problem_id"], "2")


if __name__ == "__main__":
    unittest.main()
