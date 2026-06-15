from __future__ import annotations

import json
import re
import unittest

from modulos.semantic_profile_db import (
    build_figure_seed_profiles_from_problem_row,
    build_seed_profile_from_problem_row,
    build_solution_seed_profiles_from_problem_row,
    fetch_problem_concept_links,
    fetch_semantic_concept_catalog,
    fetch_semantic_concept_linked_problems,
    fetch_semantic_practice_draft_catalog,
    fetch_semantic_practice_drafts,
    fetch_semantic_coverage_status,
    populate_problem_concept_graph,
    populate_problem_figure_seed_profiles,
    populate_problem_similarity_edges,
    populate_problem_semantic_seed_profiles,
    populate_solution_semantic_seed_profiles,
    save_semantic_practice_draft,
    update_problem_concept_link_review,
    update_problem_similarity_edge_review,
)


class FakeCursor:
    def __init__(self, conn: "FakeConnection") -> None:
        self.conn = conn
        self.rows = []
        self.description = []
        self._one = None
        self.rowcount = 0

    def execute(self, query, params=None) -> None:
        sql = " ".join(str(query).split())
        self.conn.events.append((sql, params))
        self.rows = []
        self.description = []
        self._one = None
        self.rowcount = 0
        if "FROM information_schema.columns" in sql:
            table = str((params or ("",))[0])
            self.rows = [(column,) for column in self.conn.columns_by_table.get(table, set())]
            self.description = [("column_name",)]
            return
        if "SELECT to_regclass" in sql:
            table = str((params or ("",))[0]).split(".")[-1]
            self._one = (f"public.{table}" if table in self.conn.existing_tables else None,)
            return
        if sql.startswith("SELECT COUNT("):
            match = re.search(r"FROM ([a-z_]+)", sql)
            if not match:
                raise AssertionError(f"COUNT sin tabla: {sql}")
            table = match.group(1)
            rows = list(self.conn.rows_for_table(table))
            if "schema_version = %s" in sql and params:
                rows = [row for row in rows if str(row.get("schema_version") or "") == str(params[0])]
            if "model_id = %s" in sql and params:
                rows = [row for row in rows if str(row.get("model_id") or "") == str(params[0])]
            distinct = re.search(r"COUNT\(DISTINCT ([a-z_]+)\)", sql)
            if distinct:
                column = distinct.group(1)
                self._one = (len({row.get(column) for row in rows if row.get(column) is not None}),)
            else:
                self._one = (len(rows),)
            return
        if sql.startswith("CREATE TABLE IF NOT EXISTS problem_semantic_profiles"):
            self.conn.existing_tables.add("problem_semantic_profiles")
            self.conn.columns_by_table.setdefault(
                "problem_semantic_profiles",
                {"problema_id", "schema_version", "profile_json", "embedding_text", "model_id", "status", "human_verified"},
            )
            return
        if sql.startswith("CREATE TABLE IF NOT EXISTS problema_assets"):
            self.conn.existing_tables.add("problema_assets")
            self.conn.columns_by_table.setdefault(
                "problema_assets",
                {"problema_id", "asset_type", "asset_tag", "file_path", "content_hash", "metadata_json"},
            )
            return
        if sql.startswith("CREATE TABLE IF NOT EXISTS problem_figure_profiles"):
            self.conn.existing_tables.add("problem_figure_profiles")
            self.conn.columns_by_table.setdefault(
                "problem_figure_profiles",
                {
                    "problema_id",
                    "asset_id",
                    "figure_tag",
                    "schema_version",
                    "profile_json",
                    "embedding_text",
                    "model_id",
                    "status",
                    "human_verified",
                },
            )
            return
        if sql.startswith("CREATE TABLE IF NOT EXISTS solution_semantic_profiles"):
            self.conn.existing_tables.add("solution_semantic_profiles")
            self.conn.columns_by_table.setdefault(
                "solution_semantic_profiles",
                {
                    "problema_id",
                    "solution_path_id",
                    "schema_version",
                    "solution_latex",
                    "profile_json",
                    "embedding_text",
                    "model_id",
                    "status",
                    "human_verified",
                },
            )
            return
        if sql.startswith("CREATE TABLE IF NOT EXISTS problem_similarity_edges"):
            self.conn.existing_tables.add("problem_similarity_edges")
            self.conn.columns_by_table.setdefault(
                "problem_similarity_edges",
                {
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
            )
            return
        if sql.startswith("CREATE TABLE IF NOT EXISTS semantic_practice_drafts"):
            self.conn.existing_tables.add("semantic_practice_drafts")
            self.conn.columns_by_table.setdefault(
                "semantic_practice_drafts",
                {
                    "seed_problema_id",
                    "schema_version",
                    "title",
                    "objective",
                    "draft_json",
                    "practice_latex",
                    "model_id",
                    "status",
                    "human_verified",
                    "review_note",
                },
            )
            return
        if sql.startswith("CREATE TABLE IF NOT EXISTS conceptos_matematicos"):
            self.conn.existing_tables.add("conceptos_matematicos")
            self.conn.columns_by_table.setdefault(
                "conceptos_matematicos",
                {"id", "codigo", "nombre", "curso", "tema", "tipo", "descripcion", "metadata_json", "estado"},
            )
            return
        if sql.startswith("CREATE TABLE IF NOT EXISTS problema_concepto"):
            self.conn.existing_tables.add("problema_concepto")
            self.conn.columns_by_table.setdefault(
                "problema_concepto",
                {
                    "problema_id",
                    "concepto_id",
                    "source",
                    "role",
                    "confidence",
                    "reviewed",
                    "status",
                    "review_note",
                    "reviewed_at",
                    "metadata_json",
                },
            )
            return
        if sql.startswith("ALTER TABLE problema_concepto ADD COLUMN IF NOT EXISTS status"):
            self.conn.columns_by_table.setdefault("problema_concepto", set()).add("status")
            return
        if sql.startswith("ALTER TABLE problema_concepto ADD COLUMN IF NOT EXISTS review_note"):
            self.conn.columns_by_table.setdefault("problema_concepto", set()).add("review_note")
            return
        if sql.startswith("ALTER TABLE problema_concepto ADD COLUMN IF NOT EXISTS reviewed_at"):
            self.conn.columns_by_table.setdefault("problema_concepto", set()).add("reviewed_at")
            return
        if sql.startswith("ALTER TABLE problem_similarity_edges ADD COLUMN IF NOT EXISTS review_note"):
            self.conn.columns_by_table.setdefault("problem_similarity_edges", set()).add("review_note")
            return
        if sql.startswith("ALTER TABLE problem_similarity_edges ADD COLUMN IF NOT EXISTS reviewed_at"):
            self.conn.columns_by_table.setdefault("problem_similarity_edges", set()).add("reviewed_at")
            return
        if sql.startswith("CREATE INDEX IF NOT EXISTS"):
            return
        if sql.startswith("SELECT ") and " FROM problem_semantic_profiles" in sql and " FROM problemas p" not in sql:
            selected = [
                part.strip()
                for part in re.search(r"SELECT (.*?) FROM problem_semantic_profiles", sql).group(1).split(",")
            ]
            problem_ids = None
            if "problema_id = ANY" in sql and params and len(params) > 1:
                problem_ids = set(int(item) for item in params[1])
            rows = []
            for row in self.conn.problem_semantic_rows:
                if problem_ids is not None and int(row["problema_id"]) not in problem_ids:
                    continue
                rows.append(tuple(row.get(column) for column in selected))
            self.rows = rows
            self.description = [(column,) for column in selected]
            return
        if sql.startswith("SELECT ") and " FROM problem_figure_profiles" in sql and " FROM problemas p" not in sql:
            selected = [
                part.strip()
                for part in re.search(r"SELECT (.*?) FROM problem_figure_profiles", sql).group(1).split(",")
            ]
            problem_ids = set(int(item) for item in (params or (None, []))[1])
            rows = []
            for row in self.conn.problem_figure_rows:
                if int(row["problema_id"]) not in problem_ids:
                    continue
                rows.append(tuple(row.get(column) for column in selected))
            self.rows = rows
            self.description = [(column,) for column in selected]
            return
        if sql.startswith("SELECT ") and " FROM solution_semantic_profiles" in sql and " FROM problemas p" not in sql:
            selected = [
                part.strip()
                for part in re.search(r"SELECT (.*?) FROM solution_semantic_profiles", sql).group(1).split(",")
            ]
            problem_ids = set(int(item) for item in (params or (None, []))[1])
            rows = []
            for row in self.conn.solution_semantic_rows:
                if int(row["problema_id"]) not in problem_ids:
                    continue
                rows.append(tuple(row.get(column) for column in selected))
            self.rows = rows
            self.description = [(column,) for column in selected]
            return
        if sql.startswith("SELECT id, seed_problema_id") and " FROM semantic_practice_drafts" in sql:
            by_problem = "WHERE seed_problema_id = %s" in sql
            problem_id = int((params or (0,))[0]) if by_problem else None
            status_filter = None
            if "status = %s" in sql:
                if by_problem:
                    status_filter = str((params or ("", ""))[1])
                    limit = int((params or (0, "", 20))[2])
                else:
                    status_filter = str((params or ("",))[0])
                    limit = int((params or ("", 20))[1])
            else:
                limit = int((params or (0, 20))[1]) if by_problem else int((params or (20,))[0])
            rows = []
            for row in self.conn.practice_draft_rows:
                if by_problem and int(row.get("seed_problema_id") or 0) != problem_id:
                    continue
                if status_filter and str(row.get("status") or "") != status_filter:
                    continue
                rows.append(
                    (
                        row.get("id"),
                        row.get("seed_problema_id"),
                        row.get("schema_version"),
                        row.get("title"),
                        row.get("objective"),
                        row.get("draft_json"),
                        row.get("practice_latex"),
                        row.get("model_id"),
                        row.get("status"),
                        row.get("human_verified"),
                        row.get("review_note"),
                        row.get("created_at"),
                        row.get("updated_at"),
                    )
                )
            def rank(item):
                status_rank = {"revisado": 0, "borrador": 1, "descartado": 2}
                return (status_rank.get(str(item[8] or ""), 3), str(item[12] or ""))

            self.rows = sorted(rows, key=rank)[:limit]
            return
        if sql.startswith("SELECT c.id, c.codigo") and " FROM conceptos_matematicos c" in sql:
            params_list = list(params or [])
            limit = int(params_list[-1]) if params_list else 100
            filter_values = params_list[:-1]
            query_filter = ""
            course_filter = ""
            status_filter = ""
            if "c.nombre ILIKE %s" in sql and len(filter_values) >= 3:
                query_filter = str(filter_values[0]).strip("%").lower()
                filter_values = filter_values[3:]
            if "c.curso ILIKE %s" in sql and filter_values:
                course_filter = str(filter_values[0]).strip("%").lower()
                filter_values = filter_values[1:]
            if "c.estado = %s" in sql and filter_values:
                status_filter = str(filter_values[0])
            rows = []
            for concept in self.conn.concept_rows:
                haystack = " ".join(
                    str(concept.get(key) or "")
                    for key in ("nombre", "codigo", "tema")
                ).lower()
                if query_filter and query_filter not in haystack:
                    continue
                if course_filter and course_filter not in str(concept.get("curso") or "").lower():
                    continue
                if status_filter and status_filter != str(concept.get("estado") or ""):
                    continue
                links = [
                    row
                    for row in self.conn.problem_concept_rows
                    if int(row.get("concepto_id") or 0) == int(concept.get("id") or 0)
                ]
                rows.append(
                    (
                        concept.get("id"),
                        concept.get("codigo"),
                        concept.get("nombre"),
                        concept.get("curso"),
                        concept.get("tema"),
                        concept.get("tipo"),
                        concept.get("estado"),
                        concept.get("descripcion"),
                        len(links),
                        len([row for row in links if bool(row.get("reviewed"))]),
                    )
                )
            self.rows = sorted(rows, key=lambda item: (-int(item[8] or 0), str(item[3] or ""), str(item[4] or ""), str(item[2] or "")))[:limit]
            return
        if sql.startswith("SELECT id, codigo, nombre, curso, tema, tipo, estado, descripcion FROM conceptos_matematicos WHERE id = %s"):
            concept_id = int((params or (0,))[0])
            concept = next((row for row in self.conn.concept_rows if int(row.get("id") or 0) == concept_id), None)
            if concept:
                self._one = (
                    concept.get("id"),
                    concept.get("codigo"),
                    concept.get("nombre"),
                    concept.get("curso"),
                    concept.get("tema"),
                    concept.get("tipo"),
                    concept.get("estado"),
                    concept.get("descripcion"),
                )
            return
        if sql.startswith("SELECT pc.role, pc.source") and " FROM problema_concepto pc JOIN problemas p" in sql:
            selected = [
                part.strip().split(".")[-1]
                for part in re.search(r"reviewed_at, (.*?) FROM problema_concepto", sql).group(1).split(",")
            ]
            concept_id = int((params or (0,))[0])
            role_filter = None
            if "pc.role = %s" in sql:
                role_filter = str((params or ("", ""))[1])
            limit = int((params or (50,))[-1])
            rows = []
            for link in self.conn.problem_concept_rows:
                if int(link.get("concepto_id") or 0) != concept_id:
                    continue
                if role_filter and str(link.get("role") or "") != role_filter:
                    continue
                problem = next((row for row in self.conn.problem_rows if int(row.get("id") or 0) == int(link.get("problema_id") or 0)), None)
                if not problem:
                    continue
                rows.append(
                    (
                        link.get("role"),
                        link.get("source"),
                        link.get("confidence"),
                        link.get("reviewed"),
                        link.get("metadata_json"),
                        link.get("status"),
                        link.get("review_note"),
                        link.get("reviewed_at"),
                        *[problem.get(column) for column in selected],
                    )
                )
            self.rows = sorted(rows, key=lambda item: (bool(item[3]), -float(item[2] or 0), int(item[8] or 0)))[:limit]
            return
        if sql.startswith("SELECT ") and " FROM problemas p WHERE p.id = %s" in sql:
            selected = [
                part.strip().split(".")[-1]
                for part in re.search(r"SELECT (.*?) FROM problemas p", sql).group(1).split(",")
            ]
            problem_id = int((params or (0,))[0])
            row = next((item for item in self.conn.problem_rows if int(item.get("id") or 0) == problem_id), None)
            if row:
                self._one = tuple(row.get(column) for column in selected)
                self.description = [(column,) for column in selected]
            return
        if sql.startswith("SELECT c.id, c.codigo") and " FROM problema_concepto pc JOIN conceptos_matematicos c" in sql:
            problem_id = int((params or (0,))[0])
            role_filter = None
            status_filter = None
            offset = 1
            if "pc.role = %s" in sql:
                role_filter = str((params or ("", ""))[offset])
                offset += 1
            if "pc.status = %s" in sql:
                status_filter = str((params or ("", ""))[offset])
            limit = int((params or (50,))[-1])
            rows = []
            for link in self.conn.problem_concept_rows:
                if int(link.get("problema_id") or 0) != problem_id:
                    continue
                if role_filter and str(link.get("role") or "") != role_filter:
                    continue
                if status_filter and str(link.get("status") or "") != status_filter:
                    continue
                concept = next((row for row in self.conn.concept_rows if int(row.get("id") or 0) == int(link.get("concepto_id") or 0)), None)
                if not concept:
                    continue
                rows.append(
                    (
                        concept.get("id"),
                        concept.get("codigo"),
                        concept.get("nombre"),
                        concept.get("curso"),
                        concept.get("tema"),
                        concept.get("tipo"),
                        concept.get("estado"),
                        concept.get("descripcion"),
                        link.get("role"),
                        link.get("source"),
                        link.get("confidence"),
                        link.get("reviewed"),
                        link.get("metadata_json"),
                        link.get("status"),
                        link.get("review_note"),
                        link.get("reviewed_at"),
                    )
                )
            self.rows = sorted(rows, key=lambda item: (not bool(item[11]), -float(item[10] or 0), str(item[3] or ""), str(item[4] or ""), str(item[2] or "")))[:limit]
            return
        if sql.startswith("SELECT ") and " FROM problemas p" in sql:
            selected = [part.strip().split(".")[-1] for part in re.search(r"SELECT (.*?) FROM problemas p", sql).group(1).split(",")]
            problem_ids = None
            if "p.id = ANY" in sql and params:
                problem_ids = set(int(item) for item in params[0])
            rows = []
            for row in self.conn.problem_rows:
                if problem_ids is not None and int(row["id"]) not in problem_ids:
                    continue
                rows.append(tuple(row.get(column) for column in selected))
            self.rows = rows
            self.description = [(column,) for column in selected]
            return
        if sql.startswith("SELECT ") and " FROM soluciones" in sql:
            selected = [part.strip() for part in re.search(r"SELECT (.*?) FROM soluciones", sql).group(1).split(",")]
            problem_ids = None
            if "problema_id = ANY" in sql and params:
                problem_ids = set(int(item) for item in params[0])
            rows = []
            for row in self.conn.solution_rows:
                if problem_ids is not None and int(row["problema_id"]) not in problem_ids:
                    continue
                rows.append(tuple(row.get(column) for column in selected))
            self.rows = rows
            self.description = [(column,) for column in selected]
            return
        if sql.startswith("INSERT INTO problem_semantic_profiles"):
            self.conn.upserts.append(params)
            self.rowcount = 1
            return
        if sql.startswith("INSERT INTO problem_figure_profiles"):
            self.conn.figure_upserts.append(params)
            self.rowcount = 1
            return
        if sql.startswith("INSERT INTO solution_semantic_profiles"):
            self.conn.solution_upserts.append(params)
            self.rowcount = 1
            return
        if sql.startswith("INSERT INTO problem_similarity_edges"):
            self.conn.similarity_upserts.append(params)
            self.rowcount = 1
            return
        if sql.startswith("INSERT INTO semantic_practice_drafts"):
            self.conn.practice_draft_upserts.append(params)
            self.rowcount = 1
            return
        if sql.startswith("INSERT INTO conceptos_matematicos"):
            codigo, nombre, curso, tema, tipo, descripcion, metadata_json, estado = params
            existing = next((row for row in self.conn.concept_rows if row["codigo"] == codigo), None)
            if existing is None:
                concept_id = len(self.conn.concept_rows) + 1
                existing = {
                    "id": concept_id,
                    "codigo": codigo,
                    "nombre": nombre,
                    "curso": curso,
                    "tema": tema,
                    "tipo": tipo,
                    "descripcion": descripcion,
                    "metadata_json": metadata_json,
                    "estado": estado,
                }
                self.conn.concept_rows.append(existing)
            else:
                existing.update({"nombre": nombre, "curso": curso, "tema": tema, "tipo": tipo})
            self.conn.concept_upserts.append(params)
            self._one = (existing["id"],)
            self.rowcount = 1
            return
        if sql.startswith("INSERT INTO problema_concepto"):
            problema_id, concepto_id, source, role, confidence, metadata_json = params
            existing = next(
                (
                    row
                    for row in self.conn.problem_concept_rows
                    if int(row["problema_id"]) == int(problema_id)
                    and int(row["concepto_id"]) == int(concepto_id)
                    and str(row["role"]) == str(role)
                ),
                None,
            )
            if existing is None:
                self.conn.problem_concept_rows.append(
                    {
                        "problema_id": problema_id,
                        "concepto_id": concepto_id,
                        "source": source,
                        "role": role,
                        "confidence": confidence,
                        "reviewed": False,
                        "status": "sin_revisar",
                        "review_note": "",
                        "reviewed_at": None,
                        "metadata_json": metadata_json,
                    }
                )
            else:
                existing.update({"source": source, "confidence": max(float(existing["confidence"]), float(confidence)), "metadata_json": metadata_json})
            self.conn.problem_concept_upserts.append(params)
            self.rowcount = 1
            return
        if sql.startswith("UPDATE problem_similarity_edges SET status = %s"):
            status, human_verified, review_note, reviewed, problem_id, similar_problem_id, model_id = params
            for row in self.conn.similarity_rows:
                if (
                    int(row.get("problema_id") or 0) == int(problem_id)
                    and int(row.get("similar_problema_id") or 0) == int(similar_problem_id)
                    and str(row.get("model_id") or "") == str(model_id)
                ):
                    row["status"] = status
                    row["human_verified"] = bool(human_verified)
                    row["review_note"] = review_note
                    row["reviewed_at"] = "now" if reviewed else None
                    self.rowcount = 1
                    return
            self.rowcount = 0
            return
        if sql.startswith("UPDATE problema_concepto SET status = %s"):
            status, reviewed, review_note, reviewed_at_enabled, concepto_id, problema_id, role = params
            for row in self.conn.problem_concept_rows:
                if (
                    int(row.get("concepto_id") or 0) == int(concepto_id)
                    and int(row.get("problema_id") or 0) == int(problema_id)
                    and str(row.get("role") or "") == str(role)
                ):
                    row["status"] = status
                    row["reviewed"] = bool(reviewed)
                    row["review_note"] = review_note
                    row["reviewed_at"] = "now" if reviewed_at_enabled else None
                    self._one = (
                        row.get("problema_id"),
                        row.get("concepto_id"),
                        row.get("role"),
                        row.get("source"),
                        row.get("confidence"),
                        row.get("reviewed"),
                        row.get("status"),
                        row.get("review_note"),
                        row.get("reviewed_at"),
                    )
                    self.rowcount = 1
                    return
            self.rowcount = 0
            return
        raise AssertionError(f"SQL no esperado: {sql}")

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self._one

    def close(self) -> None:
        self.conn.events.append(("CURSOR_CLOSE", None))


class FakeConnection:
    def __init__(self) -> None:
        self.columns_by_table = {
            "problemas": {
                "id",
                "numero_original",
                "enunciado_latex",
                "curso",
                "tema",
                "respuesta_correcta",
                "imagenes",
                "libro_codigo",
                "codigo_instancia",
                "soluciones",
            },
            "soluciones": {
                "id",
                "problema_id",
                "orden",
                "metodo_nombre",
                "solucion_latex",
                "autor_ia",
            },
            "problem_semantic_profiles": {
                "problema_id",
                "schema_version",
                "profile_json",
                "embedding_text",
                "model_id",
                "status",
                "human_verified",
            },
            "problem_figure_profiles": {
                "problema_id",
                "schema_version",
                "figure_tag",
                "profile_json",
                "embedding_text",
            },
            "solution_semantic_profiles": {
                "problema_id",
                "schema_version",
                "solution_path_id",
                "profile_json",
                "embedding_text",
            },
            "problem_embeddings": {
                "problema_id",
                "source_kind",
                "model_id",
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
            "semantic_practice_drafts": {
                "seed_problema_id",
                "schema_version",
                "title",
                "objective",
                "draft_json",
                "practice_latex",
                "model_id",
                "status",
                "human_verified",
                "review_note",
            },
            "conceptos_matematicos": {
                "id",
                "codigo",
                "nombre",
                "curso",
                "tema",
                "tipo",
                "descripcion",
                "metadata_json",
                "estado",
            },
            "problema_concepto": {
                "problema_id",
                "concepto_id",
                "source",
                "role",
                "confidence",
                "reviewed",
                "status",
                "review_note",
                "reviewed_at",
                "metadata_json",
            },
        }
        self.existing_tables = {"problemas", "soluciones"}
        self.problem_rows = [
            {
                "id": 22,
                "numero_original": 22,
                "enunciado_latex": "Calcular $x$. £A)$10$æB)$20$æC)$30$£D)$40$ææE)$50$£",
                "curso": "Geometria",
                "tema": "Triangulos",
                "respuesta_correcta": "B",
                "imagenes": json.dumps(["img-22.png"]),
                "libro_codigo": "aseuni-geometria",
                "codigo_instancia": "semana_1",
                "soluciones": json.dumps(
                    [
                        {
                            "metodo_nombre": "Alternativa",
                            "solucion_latex": r"Solucion heredada en JSON.",
                            "autor_ia": "GPT",
                        }
                    ]
                ),
            }
        ]
        self.solution_rows = [
            {
                "id": 501,
                "problema_id": 22,
                "orden": 1,
                "metodo_nombre": "Despeje",
                "solucion_latex": r"De $2x=10$, se obtiene $x=5$.",
                "autor_ia": "docente",
            }
        ]
        self.problem_semantic_rows = []
        self.problem_figure_rows = []
        self.solution_semantic_rows = []
        self.embedding_rows = []
        self.similarity_rows = []
        self.practice_draft_rows = []
        self.concept_rows = []
        self.problem_concept_rows = []
        self.events = []
        self.upserts = []
        self.figure_upserts = []
        self.solution_upserts = []
        self.similarity_upserts = []
        self.practice_draft_upserts = []
        self.concept_upserts = []
        self.problem_concept_upserts = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def rows_for_table(self, table: str):
        return {
            "problemas": self.problem_rows,
            "problem_semantic_profiles": self.problem_semantic_rows,
            "problem_figure_profiles": self.problem_figure_rows,
            "solution_semantic_profiles": self.solution_semantic_rows,
            "problem_embeddings": self.embedding_rows,
            "problem_similarity_edges": self.similarity_rows,
            "semantic_practice_drafts": self.practice_draft_rows,
            "conceptos_matematicos": self.concept_rows,
            "problema_concepto": self.problem_concept_rows,
        }.get(table, [])


class SemanticProfileDbTests(unittest.TestCase):
    def test_build_seed_profile_from_problem_row_uses_db_metadata_hints(self) -> None:
        conn = FakeConnection()
        profile = build_seed_profile_from_problem_row(conn.problem_rows[0])

        self.assertEqual(profile["problem_id"], "22")
        self.assertEqual(profile["course"], "Geometria")
        self.assertEqual(profile["topic"], "Triangulos")
        self.assertEqual(profile["evidence"]["parsed_answer"], "B")
        self.assertEqual(profile["evidence"]["figure_tags"], ["img-22"])
        self.assertEqual(profile["evidence"]["db_context"]["libro_codigo"], "aseuni-geometria")

    def test_populate_seed_profiles_dry_run_does_not_write(self) -> None:
        conn = FakeConnection()
        report = populate_problem_semantic_seed_profiles(conn, apply=False)

        self.assertTrue(report["dry_run"])
        self.assertEqual(report["generated"], 1)
        self.assertEqual(report["rows"][0]["status"], "would_upsert")
        self.assertEqual(conn.upserts, [])
        self.assertEqual(conn.commits, 0)

    def test_populate_seed_profiles_apply_creates_schema_and_upserts(self) -> None:
        conn = FakeConnection()
        report = populate_problem_semantic_seed_profiles(conn, apply=True)

        self.assertFalse(report["dry_run"])
        self.assertEqual(report["upserted"], 1)
        self.assertEqual(conn.commits, 1)
        self.assertEqual(len(conn.upserts), 1)
        params = conn.upserts[0]
        self.assertEqual(params[0], 22)
        self.assertEqual(params[1], "problem_semantic_profile_v1")
        profile_json = json.loads(params[2])
        self.assertEqual(profile_json["course"], "Geometria")

    def test_build_figure_seed_profiles_from_problem_row_uses_image_column(self) -> None:
        conn = FakeConnection()
        profiles = build_figure_seed_profiles_from_problem_row(conn.problem_rows[0])

        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["source_record_id"], "22")
        self.assertEqual(profiles[0]["figure_tag"], "img-22")
        self.assertEqual(profiles[0]["figure_type"], "geometria_plana")

    def test_populate_figure_seed_profiles_apply_creates_schema_and_upserts(self) -> None:
        conn = FakeConnection()
        report = populate_problem_figure_seed_profiles(conn, apply=True)

        self.assertFalse(report["dry_run"])
        self.assertEqual(report["generated"], 1)
        self.assertEqual(report["upserted"], 1)
        self.assertEqual(conn.commits, 1)
        self.assertEqual(len(conn.figure_upserts), 1)
        params = conn.figure_upserts[0]
        self.assertEqual(params[0], 22)
        self.assertEqual(params[1], "img-22")
        self.assertEqual(params[2], "geometry_figure_description_v1")
        profile_json = json.loads(params[3])
        self.assertEqual(profile_json["figure_type"], "geometria_plana")

    def test_build_solution_seed_profiles_prefers_normalized_table_rows(self) -> None:
        conn = FakeConnection()
        profiles = build_solution_seed_profiles_from_problem_row(
            conn.problem_rows[0],
            normalized_solution_rows=conn.solution_rows,
        )

        self.assertEqual(len(profiles), 1)
        profile = profiles[0]
        self.assertEqual(profile["problem_id"], "22")
        self.assertEqual(profile["solution_path_id"], "sol_01")
        self.assertEqual(profile["method"], "Despeje")
        self.assertEqual(profile["source"]["kind"], "human_solution")
        self.assertIn("img-22", profile["evidence"]["figure_tags"])

    def test_populate_solution_seed_profiles_apply_creates_schema_and_upserts(self) -> None:
        conn = FakeConnection()
        report = populate_solution_semantic_seed_profiles(conn, apply=True)

        self.assertFalse(report["dry_run"])
        self.assertEqual(report["generated"], 1)
        self.assertEqual(report["upserted"], 1)
        self.assertEqual(conn.commits, 1)
        self.assertEqual(len(conn.solution_upserts), 1)
        params = conn.solution_upserts[0]
        self.assertEqual(params[0], 22)
        self.assertEqual(params[1], "sol_01")
        self.assertEqual(params[2], "solution_semantic_profile_v1")
        self.assertIn("2x=10", params[3])
        profile_json = json.loads(params[4])
        self.assertEqual(profile_json["method"], "Despeje")

    def test_populate_problem_concept_graph_dry_run_uses_profiles_without_writes(self) -> None:
        conn = FakeConnection()
        conn.existing_tables.add("problem_semantic_profiles")
        profile = build_seed_profile_from_problem_row(conn.problem_rows[0])
        profile["concepts"] = ["Triangulos", "Angulos"]
        profile["solution_concepts"] = ["Suma de angulos"]
        conn.problem_semantic_rows = [
            {
                "problema_id": 22,
                "schema_version": "problem_semantic_profile_v1",
                "profile_json": json.dumps(profile),
                "embedding_text": "",
                "model_id": "semantic_seed_v1",
                "status": "sin_revisar",
                "human_verified": False,
            }
        ]

        report = populate_problem_concept_graph(conn, apply=False)

        self.assertTrue(report["dry_run"])
        self.assertEqual(report["generated"], 1)
        self.assertGreaterEqual(report["concept_candidates"], 3)
        self.assertEqual(conn.concept_upserts, [])
        self.assertEqual(conn.problem_concept_upserts, [])
        self.assertEqual(report["rows"][0]["status"], "would_link")

    def test_populate_problem_concept_graph_apply_creates_pending_links(self) -> None:
        conn = FakeConnection()
        conn.existing_tables.update({"problem_semantic_profiles", "solution_semantic_profiles"})
        profile = build_seed_profile_from_problem_row(conn.problem_rows[0])
        profile["concepts"] = ["Triangulos"]
        conn.problem_semantic_rows = [
            {
                "problema_id": 22,
                "schema_version": "problem_semantic_profile_v1",
                "profile_json": json.dumps(profile),
                "embedding_text": "",
                "model_id": "semantic_seed_v1",
                "status": "sin_revisar",
                "human_verified": False,
            }
        ]
        solution_profile = build_solution_seed_profiles_from_problem_row(
            conn.problem_rows[0],
            normalized_solution_rows=conn.solution_rows,
        )[0]
        solution_profile["properties_used"] = [{"name": "Propiedad angular", "role": "central"}]
        conn.solution_semantic_rows = [
            {
                "problema_id": 22,
                "solution_path_id": "sol_01",
                "schema_version": "solution_semantic_profile_v1",
                "profile_json": json.dumps(solution_profile),
                "embedding_text": "",
            }
        ]

        report = populate_problem_concept_graph(conn, apply=True)

        self.assertFalse(report["dry_run"])
        self.assertEqual(report["generated"], 1)
        self.assertIn("conceptos_matematicos", conn.existing_tables)
        self.assertIn("problema_concepto", conn.existing_tables)
        self.assertGreaterEqual(len(conn.concept_rows), 2)
        self.assertEqual(len(conn.problem_concept_rows), report["links"])
        self.assertEqual(conn.commits, 1)
        self.assertTrue(all(row["reviewed"] is False for row in conn.problem_concept_rows))
        self.assertIn("Propiedad angular", {row["nombre"] for row in conn.concept_rows})

    def test_fetch_semantic_concept_catalog_returns_problem_counts(self) -> None:
        conn = FakeConnection()
        conn.existing_tables.update({"conceptos_matematicos", "problema_concepto"})
        conn.concept_rows = [
            {
                "id": 1,
                "codigo": "geometria__triangulos__concepto__triangulos",
                "nombre": "Triangulos",
                "curso": "Geometria",
                "tema": "Triangulos",
                "tipo": "concepto",
                "estado": "pendiente",
                "descripcion": "",
            },
            {
                "id": 2,
                "codigo": "algebra__ecuaciones__concepto__ecuacion",
                "nombre": "Ecuacion",
                "curso": "Algebra",
                "tema": "Ecuaciones",
                "tipo": "concepto",
                "estado": "pendiente",
                "descripcion": "",
            },
        ]
        conn.problem_concept_rows = [
            {"problema_id": 22, "concepto_id": 1, "role": "concept", "reviewed": False},
            {"problema_id": 23, "concepto_id": 1, "role": "concept", "reviewed": True},
        ]

        payload = fetch_semantic_concept_catalog(conn, query="triang", course="geo")

        self.assertEqual(payload["schema_version"], "semantic_concept_catalog_v1")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["concepts"][0]["nombre"], "Triangulos")
        self.assertEqual(payload["concepts"][0]["problem_count"], 2)
        self.assertEqual(payload["concepts"][0]["reviewed_links"], 1)

    def test_fetch_semantic_concept_linked_problems_returns_problem_rows(self) -> None:
        conn = FakeConnection()
        conn.existing_tables.update({"conceptos_matematicos", "problema_concepto"})
        conn.concept_rows = [
            {
                "id": 1,
                "codigo": "geometria__triangulos__concepto__triangulos",
                "nombre": "Triangulos",
                "curso": "Geometria",
                "tema": "Triangulos",
                "tipo": "concepto",
                "estado": "pendiente",
                "descripcion": "",
            }
        ]
        conn.problem_concept_rows = [
            {
                "problema_id": 22,
                "concepto_id": 1,
                "role": "concept",
                "source": "problem_semantic_profile",
                "confidence": 0.55,
                "reviewed": False,
                "metadata_json": json.dumps({"evidence": "perfil"}),
            }
        ]

        payload = fetch_semantic_concept_linked_problems(conn, concept_id=1)

        self.assertEqual(payload["schema_version"], "semantic_concept_linked_problems_v1")
        self.assertEqual(payload["concept"]["nombre"], "Triangulos")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["problems"][0]["id"], 22)
        self.assertEqual(payload["problems"][0]["respuesta"], "B")
        self.assertEqual(payload["problems"][0]["link"]["role"], "concept")
        self.assertAlmostEqual(payload["problems"][0]["link"]["confidence"], 0.55)
        self.assertEqual(payload["problems"][0]["link"]["status"], "sin_revisar")
        self.assertEqual(payload["problems"][0]["link"]["metadata"]["evidence"], "perfil")

    def test_update_problem_concept_link_review_marks_link(self) -> None:
        conn = FakeConnection()
        conn.existing_tables.update({"conceptos_matematicos", "problema_concepto"})
        conn.problem_concept_rows = [
            {
                "problema_id": 22,
                "concepto_id": 1,
                "role": "concept",
                "source": "problem_semantic_profile",
                "confidence": 0.55,
                "reviewed": False,
                "status": "sin_revisar",
                "review_note": "",
                "reviewed_at": None,
                "metadata_json": "{}",
            }
        ]

        payload = update_problem_concept_link_review(
            conn,
            concept_id=1,
            problem_id=22,
            role="concept",
            status="aceptado",
            review_note="Entrena triangulos.",
        )

        self.assertEqual(payload["schema_version"], "semantic_concept_link_review_v1")
        self.assertEqual(payload["status"], "aceptado")
        self.assertTrue(payload["reviewed"])
        self.assertEqual(payload["review_note"], "Entrena triangulos.")
        self.assertEqual(conn.problem_concept_rows[0]["status"], "aceptado")

    def test_fetch_problem_concept_links_returns_concepts_for_problem(self) -> None:
        conn = FakeConnection()
        conn.existing_tables.update({"conceptos_matematicos", "problema_concepto"})
        conn.concept_rows = [
            {
                "id": 1,
                "codigo": "geometria__triangulos__concepto__triangulos",
                "nombre": "Triangulos",
                "curso": "Geometria",
                "tema": "Triangulos",
                "tipo": "concepto",
                "estado": "pendiente",
                "descripcion": "",
            }
        ]
        conn.problem_concept_rows = [
            {
                "problema_id": 22,
                "concepto_id": 1,
                "role": "concept",
                "source": "problem_semantic_profile",
                "confidence": 0.55,
                "reviewed": True,
                "status": "aceptado",
                "review_note": "Relacion correcta.",
                "reviewed_at": "now",
                "metadata_json": json.dumps({"evidence": "perfil"}),
            }
        ]

        payload = fetch_problem_concept_links(conn, problem_id=22)

        self.assertEqual(payload["schema_version"], "semantic_problem_concept_links_v1")
        self.assertEqual(payload["problem_id"], 22)
        self.assertEqual(payload["problem"]["respuesta"], "B")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["concepts"][0]["concept"]["nombre"], "Triangulos")
        self.assertEqual(payload["concepts"][0]["link"]["status"], "aceptado")
        self.assertEqual(payload["concepts"][0]["link"]["review_note"], "Relacion correcta.")

    def test_populate_similarity_edges_apply_uses_semantic_profiles(self) -> None:
        conn = FakeConnection()
        conn.existing_tables.update(
            {
                "problem_semantic_profiles",
                "problem_figure_profiles",
                "solution_semantic_profiles",
            }
        )
        first = build_seed_profile_from_problem_row(conn.problem_rows[0])
        second = build_seed_profile_from_problem_row(
            {
                **conn.problem_rows[0],
                "id": 23,
                "numero_original": 23,
                "enunciado_latex": "Halle $x$ en un triangulo con angulos marcados. A) 10 B) 20 C) 30 D) 40 E) 50",
                "imagenes": json.dumps(["img-23.png"]),
            }
        )
        first["representation"]["canonical_problem_type"] = "calculo_de_angulo_con_grafico"
        second["representation"]["canonical_problem_type"] = "calculo_de_angulo_con_grafico"
        conn.problem_semantic_rows = [
            {
                "problema_id": 22,
                "profile_json": json.dumps(first),
                "embedding_text": first["representation"]["embedding_text"],
                "model_id": "test",
                "status": "sin_revisar",
                "human_verified": False,
            },
            {
                "problema_id": 23,
                "profile_json": json.dumps(second),
                "embedding_text": second["representation"]["embedding_text"],
                "model_id": "test",
                "status": "sin_revisar",
                "human_verified": False,
            },
        ]

        report = populate_problem_similarity_edges(conn, apply=True, top_k=1, threshold=0.05)

        self.assertEqual(report["selected_profiles"], 2)
        self.assertEqual(report["generated"], 2)
        self.assertEqual(report["upserted"], 2)
        self.assertEqual(conn.commits, 1)
        self.assertEqual(len(conn.similarity_upserts), 2)
        params = conn.similarity_upserts[0]
        self.assertEqual(params[0], 22)
        self.assertEqual(params[1], 23)
        self.assertGreater(float(params[2]), 0.05)
        self.assertEqual(params[5], "semantic_similarity_seed_v1")

    def test_fetch_semantic_coverage_status_reports_counts_and_next_step(self) -> None:
        conn = FakeConnection()
        conn.existing_tables.update(
            {
                "problem_semantic_profiles",
                "problem_figure_profiles",
                "solution_semantic_profiles",
                "conceptos_matematicos",
                "problema_concepto",
                "problem_embeddings",
                "problem_similarity_edges",
            }
        )
        conn.problem_rows.append({**conn.problem_rows[0], "id": 23, "numero_original": 23})
        conn.problem_semantic_rows = [
            {"problema_id": 22, "schema_version": "problem_semantic_profile_v1", "profile_json": "{}"},
            {"problema_id": 23, "schema_version": "problem_semantic_profile_v1", "profile_json": "{}"},
        ]
        conn.problem_figure_rows = [
            {"problema_id": 22, "schema_version": "geometry_figure_description_v1", "figure_tag": "img-22"},
        ]
        conn.solution_semantic_rows = [
            {"problema_id": 22, "schema_version": "solution_semantic_profile_v1", "solution_path_id": "sol_01"},
        ]
        conn.concept_rows = [{"id": 1, "codigo": "geometria__triangulos__concepto__triangulos"}]
        conn.problem_concept_rows = [
            {"problema_id": 22, "concepto_id": 1, "source": "semantic_profile", "role": "concept"},
            {"problema_id": 23, "concepto_id": 1, "source": "semantic_profile", "role": "concept"},
        ]
        conn.embedding_rows = [{"problema_id": 22, "source_kind": "problem"}]
        conn.similarity_rows = [
            {"problema_id": 22, "similar_problema_id": 23, "model_id": "semantic_similarity_seed_v1"},
        ]

        status = fetch_semantic_coverage_status(conn)

        self.assertEqual(status["schema_version"], "semantic_coverage_status_v1")
        self.assertEqual(status["counts"]["problems"], 2)
        self.assertEqual(status["counts"]["problem_profile_problems"], 2)
        self.assertEqual(status["counts"]["figure_profile_problems"], 1)
        self.assertEqual(status["counts"]["concept_link_problems"], 2)
        self.assertEqual(status["counts"]["similarity_edges"], 1)
        self.assertEqual(status["coverage"]["problem_profiles"]["percent"], 100.0)
        self.assertEqual(status["coverage"]["concept_link_problems"]["percent"], 100.0)
        self.assertEqual(status["readiness"], "review_ready")

    def test_update_problem_similarity_edge_review_marks_human_feedback(self) -> None:
        conn = FakeConnection()
        conn.existing_tables.add("problem_similarity_edges")
        conn.similarity_rows = [
            {
                "problema_id": 22,
                "similar_problema_id": 23,
                "model_id": "semantic_similarity_seed_v1",
                "status": "sin_revisar",
                "human_verified": False,
            }
        ]

        payload = update_problem_similarity_edge_review(
            conn,
            problem_id=22,
            similar_problem_id=23,
            status="aceptado",
            review_note="Misma propiedad principal.",
        )

        self.assertEqual(payload["schema_version"], "problem_similarity_edge_review_v1")
        self.assertEqual(payload["status"], "aceptado")
        self.assertTrue(payload["human_verified"])
        self.assertEqual(conn.similarity_rows[0]["status"], "aceptado")
        self.assertEqual(conn.similarity_rows[0]["review_note"], "Misma propiedad principal.")

    def test_save_semantic_practice_draft_upserts_teacher_draft_without_touching_problems(self) -> None:
        conn = FakeConnection()
        draft = {
            "schema_version": "semantic_practice_draft_v1",
            "seed_problem_id": 22,
            "model_id": "semantic_similarity_seed_v1",
            "title": "Practica de refuerzo: Geometria / Triangulos",
            "objective": "Reforzar triangulos.",
            "recommendations": [{"problem_id": 23, "role": "refuerzo_directo"}],
            "practice_latex": r"\begin{enumerate}\item[\textbf{1.}] Halle $x$.\end{enumerate}",
        }

        payload = save_semantic_practice_draft(conn, draft, problem_id=22, status="borrador")

        self.assertEqual(payload["schema_version"], "semantic_practice_draft_saved_v1")
        self.assertEqual(payload["seed_problem_id"], 22)
        self.assertEqual(payload["status"], "borrador")
        self.assertEqual(payload["recommendation_count"], 1)
        self.assertTrue(payload["policy"]["does_not_modify_problemas"])
        self.assertEqual(len(conn.practice_draft_upserts), 1)
        params = conn.practice_draft_upserts[0]
        self.assertEqual(params[0], 22)
        self.assertEqual(params[1], "semantic_practice_draft_v1")
        self.assertEqual(params[6], "semantic_similarity_seed_v1")
        self.assertEqual(params[7], "borrador")

    def test_save_semantic_practice_draft_reviewed_marks_human_verified(self) -> None:
        conn = FakeConnection()
        draft = {
            "schema_version": "semantic_practice_draft_v1",
            "seed_problem_id": 22,
            "model_id": "semantic_similarity_seed_v1",
            "recommendations": [{"problem_id": 23, "role": "refuerzo_validado"}],
            "practice_latex": r"\begin{enumerate}\item[\textbf{1.}] Halle $x$.\end{enumerate}",
        }

        payload = save_semantic_practice_draft(
            conn,
            draft,
            problem_id=22,
            status="revisado",
            review_note="Apta para practica guiada.",
        )

        self.assertEqual(payload["status"], "revisado")
        self.assertTrue(payload["human_verified"])
        params = conn.practice_draft_upserts[0]
        self.assertEqual(params[7], "revisado")
        self.assertTrue(params[8])
        self.assertEqual(params[9], "Apta para practica guiada.")

    def test_fetch_semantic_practice_drafts_returns_saved_teacher_drafts(self) -> None:
        conn = FakeConnection()
        conn.existing_tables.add("semantic_practice_drafts")
        conn.practice_draft_rows = [
            {
                "id": 7,
                "seed_problema_id": 22,
                "schema_version": "semantic_practice_draft_v1",
                "title": "Practica guardada",
                "objective": "Reforzar triangulos.",
                "draft_json": json.dumps(
                    {
                        "schema_version": "semantic_practice_draft_v1",
                        "seed_problem_id": 22,
                        "recommendations": [{"problem_id": 23}],
                    }
                ),
                "practice_latex": r"\begin{enumerate}\item[\textbf{1.}] Halle $x$.\end{enumerate}",
                "model_id": "semantic_similarity_seed_v1",
                "status": "borrador",
                "human_verified": False,
                "review_note": "",
                "created_at": "2026-06-15 10:00:00",
                "updated_at": "2026-06-15 10:05:00",
            },
            {
                "id": 8,
                "seed_problema_id": 22,
                "schema_version": "semantic_practice_draft_v1",
                "title": "Practica revisada",
                "objective": "Reforzar triangulos.",
                "draft_json": json.dumps(
                    {
                        "schema_version": "semantic_practice_draft_v1",
                        "seed_problem_id": 22,
                        "recommendations": [{"problem_id": 24}],
                    }
                ),
                "practice_latex": r"\begin{enumerate}\item[\textbf{1.}] Calcule $x$.\end{enumerate}",
                "model_id": "semantic_similarity_seed_v1",
                "status": "revisado",
                "human_verified": True,
                "review_note": "",
                "created_at": "2026-06-15 10:01:00",
                "updated_at": "2026-06-15 10:06:00",
            }
        ]

        payload = fetch_semantic_practice_drafts(conn, problem_id=22)

        self.assertEqual(payload["schema_version"], "semantic_practice_draft_list_v1")
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["drafts"][0]["id"], 8)
        self.assertEqual(payload["drafts"][0]["recommendation_count"], 1)
        self.assertTrue(payload["policy"]["read_only"])

    def test_fetch_semantic_practice_drafts_can_filter_reviewed_only(self) -> None:
        conn = FakeConnection()
        conn.existing_tables.add("semantic_practice_drafts")
        conn.practice_draft_rows = [
            {
                "id": 7,
                "seed_problema_id": 22,
                "schema_version": "semantic_practice_draft_v1",
                "title": "Practica borrador",
                "draft_json": json.dumps({"schema_version": "semantic_practice_draft_v1", "seed_problem_id": 22}),
                "practice_latex": "",
                "model_id": "semantic_similarity_seed_v1",
                "status": "borrador",
                "human_verified": False,
            },
            {
                "id": 8,
                "seed_problema_id": 22,
                "schema_version": "semantic_practice_draft_v1",
                "title": "Practica revisada",
                "draft_json": json.dumps({"schema_version": "semantic_practice_draft_v1", "seed_problem_id": 22}),
                "practice_latex": "",
                "model_id": "semantic_similarity_seed_v1",
                "status": "revisado",
                "human_verified": True,
            },
        ]

        payload = fetch_semantic_practice_drafts(conn, problem_id=22, status="revisado")

        self.assertEqual(payload["status_filter"], "revisado")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["drafts"][0]["id"], 8)

    def test_fetch_semantic_practice_draft_catalog_returns_reviewed_across_seeds(self) -> None:
        conn = FakeConnection()
        conn.existing_tables.add("semantic_practice_drafts")
        conn.practice_draft_rows = [
            {
                "id": 7,
                "seed_problema_id": 22,
                "schema_version": "semantic_practice_draft_v1",
                "title": "Borrador",
                "draft_json": json.dumps({"schema_version": "semantic_practice_draft_v1", "seed_problem_id": 22}),
                "practice_latex": "",
                "model_id": "semantic_similarity_seed_v1",
                "status": "borrador",
                "human_verified": False,
            },
            {
                "id": 8,
                "seed_problema_id": 30,
                "schema_version": "semantic_practice_draft_v1",
                "title": "Revisada",
                "draft_json": json.dumps({"schema_version": "semantic_practice_draft_v1", "seed_problem_id": 30}),
                "practice_latex": "",
                "model_id": "semantic_similarity_seed_v1",
                "status": "revisado",
                "human_verified": True,
            },
        ]

        payload = fetch_semantic_practice_draft_catalog(conn, status="revisado")

        self.assertEqual(payload["schema_version"], "semantic_practice_draft_catalog_v1")
        self.assertEqual(payload["status_filter"], "revisado")
        self.assertTrue(payload["policy"]["student_safe_only"])
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["drafts"][0]["seed_problem_id"], 30)


if __name__ == "__main__":
    unittest.main()
