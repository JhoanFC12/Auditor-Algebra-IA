from __future__ import annotations

import json
import re
import unittest

from modulos.semantic_similarity_review import (
    build_similarity_feedback_manifest,
    fetch_problem_similarity_review,
    fetch_similarity_feedback_examples,
)


PROBLEM_COLUMNS = (
    "id",
    "numero_original",
    "enunciado_latex",
    "curso",
    "tema",
    "subtema",
    "respuesta_correcta",
    "respuesta",
    "archivo_origen",
    "nivel_dificultad",
    "consistencia_matematica",
)


class FakeCursor:
    def __init__(self, conn: "FakeConnection") -> None:
        self.conn = conn
        self.rows = []
        self.description = []
        self._one = None

    def execute(self, query, params=None) -> None:
        sql = " ".join(str(query).split())
        self.rows = []
        self.description = []
        self._one = None
        if "FROM information_schema.columns" in sql:
            table = str((params or ("",))[0])
            self.rows = [(column,) for column in self.conn.columns_by_table.get(table, set())]
            self.description = [("column_name",)]
            return
        if "SELECT to_regclass" in sql:
            table = str((params or ("",))[0]).split(".")[-1]
            self._one = (f"public.{table}" if table in self.conn.existing_tables else None,)
            return
        if sql.startswith("SELECT") and "FROM problemas p WHERE p.id = %s" in sql:
            selected = [part.strip().split(" AS ")[-1].split(".")[-1] for part in re.search(r"SELECT (.*?) FROM problemas p", sql).group(1).split(",")]
            problem = self.conn.problems.get(int((params or (0,))[0]))
            if problem:
                self._one = tuple(problem.get(column) for column in selected)
            self.description = [(column,) for column in selected]
            return
        if sql.startswith("SELECT") and "FROM problem_similarity_edges e JOIN problemas p" in sql:
            selected_after_verified = [
                part.strip().split(" AS ")[-1].split(".")[-1]
                for part in re.search(r"e.human_verified, (.*?) FROM problem_similarity_edges", sql).group(1).split(",")
            ]
            selected_problem_cols = selected_after_verified[2:]
            problem_id = int((params or (0, "", 10))[0])
            model_id = str((params or (0, "", 10))[1])
            limit = int((params or (0, "", 10))[2])
            reverse = "e.similar_problema_id = %s" in sql
            out = []
            for edge in self.conn.edges:
                if edge["model_id"] != model_id:
                    continue
                if reverse:
                    if int(edge["similar_problema_id"]) != problem_id:
                        continue
                    problem = self.conn.problems[int(edge["problema_id"])]
                    source, target = edge["similar_problema_id"], edge["problema_id"]
                else:
                    if int(edge["problema_id"]) != problem_id:
                        continue
                    problem = self.conn.problems[int(edge["similar_problema_id"])]
                    source, target = edge["problema_id"], edge["similar_problema_id"]
                out.append(
                    (
                        source,
                        target,
                        edge["problema_id"],
                        edge["similar_problema_id"],
                        edge["score"],
                        json.dumps(edge["score_components"]),
                        edge["reason"],
                        edge["model_id"],
                        edge["status"],
                        edge["human_verified"],
                        edge.get("review_note", ""),
                        edge.get("reviewed_at", ""),
                        *[problem.get(column) for column in selected_problem_cols],
                    )
                )
            out.sort(key=lambda row: (-float(row[4]), int(row[1])))
            self.rows = out[:limit]
            self.description = [
                ("source_problem_id",),
                ("target_problem_id",),
                ("edge_problem_id",),
                ("edge_similar_problem_id",),
                ("score",),
                ("score_components",),
                ("reason",),
                ("model_id",),
                ("status",),
                ("human_verified",),
                ("review_note",),
                ("reviewed_at",),
                *[(column,) for column in selected_problem_cols],
            ]
            return
        if sql.startswith("SELECT") and "FROM problem_similarity_edges e JOIN problemas src" in sql:
            model_id = str((params or ("", []))[0])
            statuses = set(str(item) for item in (params or ("", []))[1])
            limit = int((params or ("", [], 0))[2]) if len(params or ()) > 2 else 0
            out = []
            for edge in self.conn.edges:
                if edge["model_id"] != model_id:
                    continue
                if str(edge.get("status") or "") not in statuses:
                    continue
                if not edge.get("human_verified"):
                    continue
                source = self.conn.problems[int(edge["problema_id"])]
                target = self.conn.problems[int(edge["similar_problema_id"])]
                out.append(
                    (
                        edge["problema_id"],
                        edge["similar_problema_id"],
                        edge["score"],
                        json.dumps(edge["score_components"]),
                        edge["reason"],
                        edge["model_id"],
                        edge["status"],
                        edge["human_verified"],
                        edge.get("review_note", ""),
                        edge.get("reviewed_at", ""),
                        *[source.get(column) for column in PROBLEM_COLUMNS],
                        *[target.get(column) for column in PROBLEM_COLUMNS],
                    )
                )
            if limit > 0:
                out = out[:limit]
            self.rows = out
            self.description = [
                ("edge_problem_id",),
                ("edge_similar_problem_id",),
                ("score",),
                ("score_components",),
                ("reason",),
                ("model_id",),
                ("status",),
                ("human_verified",),
                ("review_note",),
                ("reviewed_at",),
                *[(f"source_{column}",) for column in PROBLEM_COLUMNS],
                *[(f"target_{column}",) for column in PROBLEM_COLUMNS],
            ]
            return
        raise AssertionError(f"SQL no esperado: {sql}")

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self.rows)

    def close(self) -> None:
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.existing_tables = {"problemas", "problem_similarity_edges"}
        self.columns_by_table = {
            "problemas": {
                "id",
                "numero_original",
                "enunciado_latex",
                "curso",
                "tema",
                "respuesta",
                "archivo_origen",
                "nivel_dificultad",
                "consistencia_matematica",
            },
            "problem_similarity_edges": {
                "problema_id",
                "similar_problema_id",
                "score",
                "score_components",
                "reason",
                "model_id",
                "status",
                "human_verified",
                "review_note",
                "reviewed_at",
            },
        }
        self.problems = {
            1: {
                "id": 1,
                "numero_original": 1,
                "enunciado_latex": "Calcular x.",
                "curso": "Geometria",
                "tema": "Triangulos",
                "respuesta": "B",
                "archivo_origen": "demo.pdf",
                "nivel_dificultad": "2",
                "consistencia_matematica": "Consistente",
            },
            2: {
                "id": 2,
                "numero_original": 2,
                "enunciado_latex": "Hallar x.",
                "curso": "Geometria",
                "tema": "Triangulos",
                "respuesta": "C",
                "archivo_origen": "demo.pdf",
                "nivel_dificultad": "2",
                "consistencia_matematica": "Consistente",
            },
            3: {
                "id": 3,
                "numero_original": 3,
                "enunciado_latex": "Resolver un problema de circunferencia.",
                "curso": "Geometria",
                "tema": "Circunferencia",
                "respuesta": "A",
                "archivo_origen": "demo.pdf",
                "nivel_dificultad": "3",
                "consistencia_matematica": "Consistente",
            },
        }
        self.edges = [
            {
                "problema_id": 1,
                "similar_problema_id": 2,
                "score": 0.82,
                "score_components": {"components": {"concepts": 0.7}},
                "reason": "mismo tema: Triangulos",
                "model_id": "semantic_similarity_seed_v1",
                "status": "sin_revisar",
                "human_verified": False,
                "review_note": "",
                "reviewed_at": "",
            }
        ]

    def cursor(self):
        return FakeCursor(self)


class SemanticSimilarityReviewTests(unittest.TestCase):
    def test_fetch_problem_similarity_review_returns_edges_with_problem_detail(self) -> None:
        payload = fetch_problem_similarity_review(FakeConnection(), problem_id=1, top_k=5)

        self.assertEqual(payload["schema_version"], "problem_similarity_review_v1")
        self.assertEqual(payload["problem"]["id"], 1)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["similar"][0]["problem"]["id"], 2)
        self.assertEqual(payload["similar"][0]["score"], 0.82)
        self.assertEqual(payload["similar"][0]["review_note"], "")
        self.assertIn("Triangulos", payload["similar"][0]["reason"])

    def test_fetch_problem_similarity_review_reports_missing_edges_table(self) -> None:
        conn = FakeConnection()
        conn.existing_tables.remove("problem_similarity_edges")

        payload = fetch_problem_similarity_review(conn, problem_id=1)

        self.assertEqual(payload["similar"], [])
        self.assertIn("problem_similarity_edges", payload["message"])

    def test_fetch_similarity_feedback_examples_exports_reviewed_pairs(self) -> None:
        conn = FakeConnection()
        conn.edges = [
            {
                "problema_id": 1,
                "similar_problema_id": 2,
                "score": 0.82,
                "score_components": {"components": {"concepts": 0.7}},
                "reason": "mismo tema: Triangulos",
                "model_id": "semantic_similarity_seed_v1",
                "status": "aceptado",
                "human_verified": True,
                "review_note": "Misma propiedad.",
                "reviewed_at": "2026-06-15 10:00:00",
            },
            {
                "problema_id": 1,
                "similar_problema_id": 3,
                "score": 0.41,
                "score_components": {"components": {"statement": 0.2}},
                "reason": "curso parecido",
                "model_id": "semantic_similarity_seed_v1",
                "status": "rechazado",
                "human_verified": True,
                "review_note": "No usa el mismo concepto.",
                "reviewed_at": "2026-06-15 10:05:00",
            },
        ]

        rows = fetch_similarity_feedback_examples(conn)
        manifest = build_similarity_feedback_manifest(
            rows,
            db_profile="local_mirror",
            db_name="demo_db",
            model_id="semantic_similarity_seed_v1",
            statuses=["aceptado", "rechazado"],
            output_jsonl="feedback.jsonl",
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["schema_version"], "semantic_similarity_feedback_example_v1")
        self.assertEqual(rows[0]["label"], "positive")
        self.assertEqual(rows[1]["label"], "negative")
        self.assertEqual(rows[0]["source_problem"]["id"], 1)
        self.assertEqual(rows[0]["target_problem"]["id"], 2)
        self.assertIn("SOURCE", rows[0]["pair_text"])
        self.assertEqual(manifest["schema_version"], "semantic_similarity_feedback_export_v1")
        self.assertEqual(manifest["labels"]["positive"], 1)
        self.assertEqual(manifest["labels"]["negative"], 1)


if __name__ == "__main__":
    unittest.main()
