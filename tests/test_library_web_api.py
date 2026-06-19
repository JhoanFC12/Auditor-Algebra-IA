from __future__ import annotations

import base64
import gzip
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

from modulos.instance_factory.library_api import LibraryApiError, LibraryWebApi, _timeline_stage_from_counts
from modulos.instance_factory.library_web_server import LibraryWebRuntime
from modulos.instance_factory.models import InstancePipelineContext, StagingProblemRecord
from modulos.instance_factory.staging import InstanceStagingStore
from modulos.instance_factory.web_server import FactoryWebRuntime, _FilePayload


def _post_json(base: str, path: str, body: dict) -> dict:
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


class _FakeRuntime:
    def __init__(self, context: InstancePipelineContext) -> None:
        self.context = context
        self.started = False

    def start(self) -> str:
        self.started = True
        return f"http://127.0.0.1:9999/{self.context.book_code}/{self.context.instance_type}/"


class _FakeController:
    def __init__(self) -> None:
        self.books = {
            1: {
                "id": 1,
                "codigo": "ALG01",
                "titulo": "Algebra",
                "autor": "",
                "editorial": "",
                "edicion": "",
                "curso": "ALG",
                "workspace_dir": "E:/tmp/ALG01",
                "pdf_path": "E:/tmp/ALG01/book.pdf",
                "cover_path": "",
                "estado": "pendiente",
                "notas": "",
                "activo": True,
                "instances_total": 1,
                "instances_expected_total": 10,
                "consistency_consistentes_total": 0,
                "consistency_inconsistentes_total": 0,
                "consistency_sin_revisar_total": 0,
            }
        }
        self.instances = {
            1: [
                {
                    "id": 11,
                    "libro_id": 1,
                    "tipo": "S01",
                    "total_esperado": 10,
                    "session_path": "E:/tmp/ALG01/s01/session.json",
                    "soluciones_dir": "E:/tmp/ALG01/s01/soluciones",
                    "activo": True,
                    "notas": "",
                }
            ]
        }
        self.created_books = []
        self.created_instances = []
        self.updated_books = []
        self.updated_instances = []
        self.book_list_calls = []
        self.dashboard_calls = []
        self.instance_list_calls = []

    def listar_bases_datos(self):
        return ["demo_db"]

    def listar_libros(self, _db_name, *, include_instance_health=True):
        self.book_list_calls.append((str(_db_name), bool(include_instance_health)))
        return [dict(row) for row in self.books.values()]

    def obtener_libro(self, _db_name, libro_id):
        row = self.books.get(int(libro_id))
        return dict(row) if row else None

    def listar_instancias_libro(self, _db_name, libro_id):
        self.instance_list_calls.append(int(libro_id))
        return [dict(row) for row in self.instances.get(int(libro_id), [])]

    def obtener_dashboard_libro(self, _db_name, libro_id):
        self.dashboard_calls.append(int(libro_id))
        return {
            "libro_id": int(libro_id),
            "codigo": "ALG01",
            "titulo": "Algebra",
            "estado": self.books[int(libro_id)]["estado"],
            "workspace_dir": "E:/tmp/ALG01",
            "pdf_path": "E:/tmp/ALG01/book.pdf",
            "pdf_status": "Falta",
            "instancias": [
                {
                    "instancia_id": 11,
                    "tipo": "S01",
                    "total_esperado": 10,
                    "escaneados_sesion": 4,
                    "con_clave_sesion": 3,
                    "con_solucion_sesion": 2,
                    "sin_clave_sesion": 1,
                    "sin_solucion_sesion": 2,
                    "pdf_path": "",
                    "session_path": "E:/tmp/ALG01/s01/session.json",
                    "soluciones_dir": "E:/tmp/ALG01/s01/soluciones",
                    "pdf_status": "-",
                    "session_status": "OK",
                    "soluciones_status": "OK",
                    "subidos_bd": 0,
                    "subidos_bd_con_solucion": 0,
                    "subidos_bd_sin_solucion": 0,
                    "subidos_bd_consistentes": 0,
                    "subidos_bd_inconsistentes": 0,
                    "subidos_bd_sin_revisar": 0,
                    "faltantes": 6,
                    "porcentaje": 0.4,
                }
            ],
            "total_instancias": 1,
            "total_esperado": 10,
            "escaneados_sesion_total": 4,
            "con_clave_sesion_total": 3,
            "con_solucion_sesion_total": 2,
            "subidos_bd_total": 0,
            "subidos_bd_con_solucion_total": 0,
            "subidos_bd_sin_solucion_total": 0,
            "subidos_bd_consistentes_total": 0,
            "subidos_bd_inconsistentes_total": 0,
            "subidos_bd_sin_revisar_total": 0,
            "faltantes_total": 6,
            "porcentaje_total": 0.4,
        }

    def crear_libro(self, _db_name, payload):
        self.created_books.append(payload)
        book_id = 2
        self.books[book_id] = {
            **asdict(payload),
            "id": book_id,
            "instances_total": 0,
            "instances_expected_total": 0,
        }
        self.instances[book_id] = []
        return book_id

    def crear_instancia(self, _db_name, payload):
        self.created_instances.append(payload)
        instance_id = 12
        self.instances.setdefault(int(payload.libro_id), []).append({**asdict(payload), "id": instance_id})
        return instance_id

    def actualizar_libro(self, _db_name, libro_id, payload):
        self.updated_books.append((int(libro_id), payload))
        self.books[int(libro_id)].update(asdict(payload))

    def actualizar_instancia(self, _db_name, instancia_id, payload):
        self.updated_instances.append((int(instancia_id), payload))
        for row in self.instances[int(payload.libro_id)]:
            if int(row["id"]) == int(instancia_id):
                row.update(asdict(payload))


class LibraryWebApiTests(unittest.TestCase):
    def test_library_databases_reports_connection_error(self) -> None:
        class _FailingController:
            def __init__(self) -> None:
                self.db = SimpleNamespace(last_connection_error="connection refused")

            def listar_bases_datos(self):
                return []

        api = LibraryWebApi(controller=_FailingController())

        payload = api.dispatch("GET", "/api/library/databases", {}, {})

        self.assertEqual(payload["schema_version"], "library_databases_v1")
        self.assertEqual(payload["databases"], [])
        self.assertEqual(payload["status"], "error")
        self.assertIn("connection refused", payload["message"])

    def test_library_api_lists_detail_mutates_and_prepares_factory(self) -> None:
        with tempfile.TemporaryDirectory() as covers_tmp:
            previous_cover_root = os.environ.get("PDF_LIBRARY_COVER_ROOT")
            os.environ["PDF_LIBRARY_COVER_ROOT"] = covers_tmp
            source_cover = Path(covers_tmp).parent / "external-cover.png"
            source_cover.write_bytes(b"\x89PNG\r\n\x1a\nmanual")
            self.addCleanup(lambda: source_cover.unlink(missing_ok=True))
            try:
                controller = _FakeController()
                runtimes = []
                opened = []

                def runtime_factory(context):
                    runtime = _FakeRuntime(context)
                    runtimes.append(runtime)
                    return runtime

                api = LibraryWebApi(
                    controller=controller,
                    runtime_factory=runtime_factory,
                    open_url=lambda url, title: opened.append((url, title)),
                )

                databases = api.dispatch("GET", "/api/library/databases", {}, {})
                self.assertEqual(databases["databases"], ["demo_db"])

                books = api.dispatch("GET", "/api/library/books", {"db_name": ["demo_db"]}, {})
                self.assertEqual(books["schema_version"], "library_books_v1")
                self.assertTrue(books["policy"]["never_insert_directly_into_problemas"])
                self.assertEqual(books["books"][0]["indicators"]["total_instancias"], 1)
                self.assertEqual(controller.dashboard_calls, [])
                self.assertNotIn("instances", books["books"][0])
                self.assertEqual(controller.instance_list_calls, [])

                books_with_instances = api.dispatch("GET", "/api/library/books", {"db_name": ["demo_db"], "include_instances": ["1"]}, {})
                self.assertEqual(books_with_instances["books"][0]["instances"][0]["tipo"], "S01")
                self.assertEqual(controller.instance_list_calls, [1])

                detail = api.dispatch("GET", "/api/library/books/1", {"db_name": ["demo_db"]}, {})
                self.assertEqual(controller.dashboard_calls, [1])
                self.assertEqual(detail["instances"][0]["indicators"]["escaneados_sesion"], 4)
                self.assertEqual(detail["instances"][0]["timeline_stage"]["id"], "crops")
                self.assertEqual(detail["instances"][0]["timeline_stage"]["index"], 3)
                self.assertEqual(detail["instances"][0]["factory_prepare_endpoint"], "/api/library/instances/11/factory")

                created = api.dispatch("POST", "/api/library/books", {}, {"db_name": "demo_db", "codigo": "GEO01", "titulo": "Geometria"})
                self.assertEqual(created["book_id"], 2)
                self.assertEqual(controller.created_books[0].codigo, "GEO01")

                edited = api.dispatch(
                    "POST",
                    "/api/library/books/1",
                    {},
                    {
                        "db_name": "demo_db",
                        "codigo": "ALG01-EDIT",
                        "titulo": "Algebra editada",
                        "autor": "Nuevo autor",
                        "editorial": "Nueva editorial",
                        "edicion": "2026",
                        "curso": "Algebra",
                        "cover_path": str(source_cover),
                        "notas": "metadata revisada",
                        "estado": "en_progreso",
                    },
                )
                stored_cover = Path(controller.books[1]["cover_path"])
                self.assertEqual(edited["schema_version"], "library_book_updated_v1")
                self.assertEqual(edited["book"]["code"], "ALG01-EDIT")
                self.assertEqual(controller.books[1]["titulo"], "Algebra editada")
                self.assertEqual(controller.books[1]["notas"], "metadata revisada")
                self.assertTrue(stored_cover.exists())
                self.assertTrue(stored_cover.is_relative_to(Path(covers_tmp)))
                self.assertEqual(stored_cover.read_bytes(), source_cover.read_bytes())

                instance = api.dispatch("POST", "/api/library/books/1/instances", {}, {"db_name": "demo_db", "tipo": "S02", "total_esperado": 20})
                self.assertEqual(instance["instance_id"], 12)
                self.assertEqual(controller.created_instances[0].tipo, "S02")

                state = api.dispatch("POST", "/api/library/books/1/state", {}, {"db_name": "demo_db", "estado": "en_progreso"})
                self.assertEqual(state["estado"], "en_progreso")
                self.assertEqual(controller.books[1]["estado"], "en_progreso")

                updated = api.dispatch(
                    "POST",
                    "/api/library/instances/11/state",
                    {},
                    {
                        "db_name": "demo_db",
                        "book_id": 1,
                        "tipo": "S01 editada",
                        "total_esperado": 12,
                        "activo": False,
                        "notas": "pausada",
                    },
                )
                self.assertFalse(updated["instance"]["activo"])
                self.assertEqual(updated["instance"]["tipo"], "S01 editada")
                self.assertEqual(controller.updated_instances[0][0], 11)
                self.assertEqual(controller.updated_instances[0][1].tipo, "S01 editada")
                self.assertEqual(controller.updated_instances[0][1].total_esperado, 12)

                factory = api.dispatch("POST", "/api/library/instances/11/factory", {}, {"db_name": "demo_db", "book_id": 1, "open": True})
                self.assertEqual(factory["context"]["book_code"], "ALG01-EDIT")
                self.assertEqual(factory["context"]["instance_type"], "S01 editada")
                self.assertEqual(factory["url"], "http://127.0.0.1:9999/ALG01-EDIT/S01 editada/")
                self.assertEqual(len(runtimes), 1)
                self.assertTrue(runtimes[0].started)
                self.assertEqual(opened[0][0], factory["url"])
            finally:
                if previous_cover_root is None:
                    os.environ.pop("PDF_LIBRARY_COVER_ROOT", None)
                else:
                    os.environ["PDF_LIBRARY_COVER_ROOT"] = previous_cover_root

    def test_library_api_caches_gets_and_invalidates_after_mutation(self) -> None:
        controller = _FakeController()
        api = LibraryWebApi(controller=controller)

        first = api.dispatch("GET", "/api/library/books", {"db_name": ["demo_db"]}, {})
        first["books"][0]["title"] = "mutado en cliente"
        second = api.dispatch("GET", "/api/library/books", {"db_name": ["demo_db"]}, {})

        self.assertEqual(controller.book_list_calls, [("demo_db", False)])
        self.assertEqual(second["books"][0]["title"], "Algebra")

        with_instances = api.dispatch("GET", "/api/library/books", {"db_name": ["demo_db"], "include_instances": ["1"]}, {})
        again_with_instances = api.dispatch("GET", "/api/library/books", {"db_name": ["demo_db"], "include_instances": ["1"]}, {})
        self.assertEqual(with_instances["books"][0]["instances"][0]["tipo"], "S01")
        self.assertEqual(again_with_instances["books"][0]["instances"][0]["tipo"], "S01")
        self.assertEqual(controller.instance_list_calls, [1])

        forced = api.dispatch("GET", "/api/library/books", {"db_name": ["demo_db"], "no_cache": ["1"]}, {})
        self.assertEqual(forced["books"][0]["title"], "Algebra")
        self.assertEqual(controller.book_list_calls, [("demo_db", False), ("demo_db", True), ("demo_db", False)])

        api.dispatch("POST", "/api/library/books/1/state", {}, {"db_name": "demo_db", "estado": "en_progreso"})
        refreshed = api.dispatch("GET", "/api/library/books", {"db_name": ["demo_db"]}, {})

        self.assertEqual(controller.book_list_calls, [("demo_db", False), ("demo_db", True), ("demo_db", False), ("demo_db", False)])
        self.assertEqual(refreshed["books"][0]["status"], "en_progreso")

    def test_library_api_exposes_problem_similarity_review(self) -> None:
        calls = []

        def fake_fetcher(**kwargs):
            calls.append(kwargs)
            return {
                "schema_version": "problem_similarity_review_v1",
                "problem_id": kwargs["problem_id"],
                "similar": [{"target_problem_id": 23, "score": 0.75}],
                "count": 1,
            }

        api = LibraryWebApi(controller=_FakeController(), semantic_similarity_fetcher=fake_fetcher)

        payload = api.dispatch(
            "GET",
            "/api/library/problems/22/similar",
            {
                "db_name": ["demo_db"],
                "db_profile": ["local_mirror"],
                "top_k": ["7"],
                "include_reverse": ["1"],
            },
            {},
        )

        self.assertEqual(payload["schema_version"], "problem_similarity_review_v1")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(calls[0]["db_name"], "demo_db")
        self.assertEqual(calls[0]["problem_id"], 22)
        self.assertEqual(calls[0]["top_k"], 7)
        self.assertTrue(calls[0]["include_reverse"])

    def test_library_api_lists_problem_concepts(self) -> None:
        calls = []

        def fake_problem_concept_fetcher(**kwargs):
            calls.append(kwargs)
            return {
                "schema_version": "semantic_problem_concept_links_v1",
                "problem_id": kwargs["problem_id"],
                "count": 1,
                "concepts": [{"concept": {"id": 1, "nombre": "Triangulos"}, "link": {"status": "aceptado"}}],
            }

        api = LibraryWebApi(
            controller=_FakeController(),
            semantic_problem_concept_fetcher=fake_problem_concept_fetcher,
        )

        payload = api.dispatch(
            "GET",
            "/api/library/problems/22/concepts",
            {"db_name": ["demo_db"], "db_profile": ["local_mirror"], "role": ["concept"], "status": ["aceptado"], "limit": ["20"]},
            {},
        )

        self.assertEqual(payload["schema_version"], "semantic_problem_concept_links_v1")
        self.assertEqual(payload["problem_id"], 22)
        self.assertEqual(payload["concepts"][0]["concept"]["nombre"], "Triangulos")
        self.assertEqual(calls[0]["db_name"], "demo_db")
        self.assertEqual(calls[0]["db_profile"], "local_mirror")
        self.assertEqual(calls[0]["problem_id"], 22)
        self.assertEqual(calls[0]["role"], "concept")
        self.assertEqual(calls[0]["status"], "aceptado")
        self.assertEqual(calls[0]["limit"], 20)

    def test_library_api_exposes_problem_practice_draft(self) -> None:
        calls = []

        def fake_practice_fetcher(**kwargs):
            calls.append(kwargs)
            return {
                "schema_version": "semantic_practice_draft_v1",
                "seed_problem_id": kwargs["problem_id"],
                "recommendations": [{"problem_id": 23, "role": "refuerzo_directo"}],
                "count": 1,
            }

        api = LibraryWebApi(controller=_FakeController(), semantic_practice_fetcher=fake_practice_fetcher)

        payload = api.dispatch(
            "GET",
            "/api/library/problems/22/practice-draft",
            {
                "db_name": ["demo_db"],
                "top_k": ["12"],
                "target_count": ["6"],
                "include_reverse": ["1"],
            },
            {},
        )

        self.assertEqual(payload["schema_version"], "semantic_practice_draft_v1")
        self.assertEqual(payload["seed_problem_id"], 22)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(calls[0]["db_name"], "demo_db")
        self.assertEqual(calls[0]["top_k"], 12)
        self.assertEqual(calls[0]["target_count"], 6)
        self.assertTrue(calls[0]["include_reverse"])

    def test_library_api_saves_problem_practice_draft(self) -> None:
        calls = []

        def fake_practice_saver(**kwargs):
            calls.append(kwargs)
            return {
                "schema_version": "semantic_practice_draft_saved_v1",
                "seed_problem_id": kwargs["problem_id"],
                "status": kwargs["status"],
                "recommendation_count": len(kwargs["draft"].get("recommendations") or []),
                "policy": {"does_not_modify_problemas": True},
            }

        api = LibraryWebApi(controller=_FakeController(), semantic_practice_saver=fake_practice_saver)
        draft = {
            "schema_version": "semantic_practice_draft_v1",
            "seed_problem_id": 22,
            "model_id": "semantic_similarity_seed_v1",
            "recommendations": [{"problem_id": 23}],
            "practice_latex": r"\begin{enumerate}\item[\textbf{1.}] Halle $x$.\end{enumerate}",
        }

        payload = api.dispatch(
            "POST",
            "/api/library/problems/22/practice-draft",
            {"db_name": ["demo_db"]},
            {"draft": draft, "status": "borrador"},
        )

        self.assertEqual(payload["schema_version"], "semantic_practice_draft_saved_v1")
        self.assertEqual(payload["seed_problem_id"], 22)
        self.assertEqual(payload["recommendation_count"], 1)
        self.assertTrue(payload["policy"]["does_not_modify_problemas"])
        self.assertEqual(calls[0]["db_name"], "demo_db")
        self.assertEqual(calls[0]["problem_id"], 22)
        self.assertEqual(calls[0]["status"], "borrador")

    def test_library_api_saves_reviewed_problem_practice_draft_status(self) -> None:
        calls = []

        def fake_practice_saver(**kwargs):
            calls.append(kwargs)
            return {
                "schema_version": "semantic_practice_draft_saved_v1",
                "seed_problem_id": kwargs["problem_id"],
                "status": kwargs["status"],
                "human_verified": kwargs["status"] == "revisado",
            }

        api = LibraryWebApi(controller=_FakeController(), semantic_practice_saver=fake_practice_saver)
        draft = {
            "schema_version": "semantic_practice_draft_v1",
            "seed_problem_id": 22,
            "model_id": "semantic_similarity_seed_v1",
            "recommendations": [{"problem_id": 23}],
        }

        payload = api.dispatch(
            "POST",
            "/api/library/problems/22/practice-draft",
            {"db_name": ["demo_db"]},
            {"draft": draft, "status": "revisado", "review_note": "Lista para alumnos."},
        )

        self.assertEqual(payload["status"], "revisado")
        self.assertTrue(payload["human_verified"])
        self.assertEqual(calls[0]["status"], "revisado")
        self.assertEqual(calls[0]["review_note"], "Lista para alumnos.")

    def test_library_api_lists_saved_problem_practice_drafts(self) -> None:
        calls = []

        def fake_practice_lister(**kwargs):
            calls.append(kwargs)
            return {
                "schema_version": "semantic_practice_draft_list_v1",
                "seed_problem_id": kwargs["problem_id"],
                "count": 1,
                "drafts": [{"id": 7, "title": "Practica guardada"}],
            }

        api = LibraryWebApi(controller=_FakeController(), semantic_practice_lister=fake_practice_lister)

        payload = api.dispatch(
            "GET",
            "/api/library/problems/22/practice-drafts",
            {"db_name": ["demo_db"], "limit": ["5"], "status": ["revisado"]},
            {},
        )

        self.assertEqual(payload["schema_version"], "semantic_practice_draft_list_v1")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["drafts"][0]["title"], "Practica guardada")
        self.assertEqual(calls[0]["db_name"], "demo_db")
        self.assertEqual(calls[0]["problem_id"], 22)
        self.assertEqual(calls[0]["limit"], 5)
        self.assertEqual(calls[0]["status"], "revisado")

    def test_library_api_lists_reviewed_practice_draft_catalog(self) -> None:
        calls = []

        def fake_practice_lister(**kwargs):
            calls.append(kwargs)
            return {
                "schema_version": "semantic_practice_draft_catalog_v1",
                "status_filter": kwargs["status"],
                "count": 1,
                "drafts": [{"id": 9, "seed_problem_id": 30, "title": "Practica revisada"}],
            }

        api = LibraryWebApi(controller=_FakeController(), semantic_practice_lister=fake_practice_lister)

        payload = api.dispatch(
            "GET",
            "/api/library/practice-drafts",
            {"db_name": ["demo_db"], "status": ["revisado"], "limit": ["15"]},
            {},
        )

        self.assertEqual(payload["schema_version"], "semantic_practice_draft_catalog_v1")
        self.assertEqual(payload["status_filter"], "revisado")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["drafts"][0]["seed_problem_id"], 30)
        self.assertEqual(calls[0]["db_name"], "demo_db")
        self.assertEqual(calls[0]["problem_id"], 0)
        self.assertEqual(calls[0]["limit"], 15)
        self.assertEqual(calls[0]["status"], "revisado")

    def test_library_api_exposes_semantic_status(self) -> None:
        calls = []

        def fake_status_fetcher(**kwargs):
            calls.append(kwargs)
            return {
                "schema_version": "semantic_coverage_status_v1",
                "model_id": kwargs["model_id"],
                "counts": {"problems": 12, "similarity_edges": 40},
                "coverage": {},
                "readiness": "review_ready",
                "next_step": "Revisar similitud.",
            }

        api = LibraryWebApi(controller=_FakeController(), semantic_status_fetcher=fake_status_fetcher)

        payload = api.dispatch(
            "GET",
            "/api/library/semantic/status",
            {
                "db_name": ["demo_db"],
                "db_profile": ["local_mirror"],
                "model_id": ["semantic_similarity_seed_v1"],
            },
            {},
        )

        self.assertEqual(payload["schema_version"], "semantic_coverage_status_v1")
        self.assertEqual(payload["counts"]["problems"], 12)
        self.assertEqual(calls[0]["db_name"], "demo_db")
        self.assertEqual(calls[0]["db_profile"], "local_mirror")
        self.assertEqual(calls[0]["model_id"], "semantic_similarity_seed_v1")

    def test_library_api_lists_semantic_concepts(self) -> None:
        calls = []

        def fake_concept_fetcher(**kwargs):
            calls.append(kwargs)
            return {
                "schema_version": "semantic_concept_catalog_v1",
                "filters": {"query": kwargs["query"], "course": kwargs["course"], "status": kwargs["status"]},
                "count": 1,
                "concepts": [{"id": 1, "nombre": "Triangulos", "problem_count": 12}],
            }

        api = LibraryWebApi(controller=_FakeController(), semantic_concept_fetcher=fake_concept_fetcher)

        payload = api.dispatch(
            "GET",
            "/api/library/concepts",
            {"db_name": ["demo_db"], "q": ["tri"], "course": ["geo"], "status": ["pendiente"], "limit": ["25"]},
            {},
        )

        self.assertEqual(payload["schema_version"], "semantic_concept_catalog_v1")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["concepts"][0]["nombre"], "Triangulos")
        self.assertEqual(calls[0]["db_name"], "demo_db")
        self.assertEqual(calls[0]["query"], "tri")
        self.assertEqual(calls[0]["course"], "geo")
        self.assertEqual(calls[0]["status"], "pendiente")
        self.assertEqual(calls[0]["limit"], 25)

    def test_library_api_lists_semantic_concept_problems(self) -> None:
        calls = []

        def fake_concept_problem_fetcher(**kwargs):
            calls.append(kwargs)
            return {
                "schema_version": "semantic_concept_linked_problems_v1",
                "concept_id": kwargs["concept_id"],
                "concept": {"id": kwargs["concept_id"], "nombre": "Triangulos"},
                "role_filter": kwargs["role"] or "all",
                "count": 1,
                "problems": [{"id": 22, "enunciado_latex": "Calcular $x$.", "link": {"role": "concept"}}],
            }

        api = LibraryWebApi(
            controller=_FakeController(),
            semantic_concept_problem_fetcher=fake_concept_problem_fetcher,
        )

        payload = api.dispatch(
            "GET",
            "/api/library/concepts/5/problems",
            {"db_name": ["demo_db"], "db_profile": ["local_mirror"], "role": ["concept"], "limit": ["30"]},
            {},
        )

        self.assertEqual(payload["schema_version"], "semantic_concept_linked_problems_v1")
        self.assertEqual(payload["concept_id"], 5)
        self.assertEqual(payload["problems"][0]["id"], 22)
        self.assertEqual(calls[0]["db_name"], "demo_db")
        self.assertEqual(calls[0]["db_profile"], "local_mirror")
        self.assertEqual(calls[0]["concept_id"], 5)
        self.assertEqual(calls[0]["role"], "concept")
        self.assertEqual(calls[0]["limit"], 30)

    def test_library_api_reviews_semantic_concept_link(self) -> None:
        calls = []

        def fake_concept_link_reviewer(**kwargs):
            calls.append(kwargs)
            return {
                "schema_version": "semantic_concept_link_review_v1",
                "concept_id": kwargs["concept_id"],
                "problem_id": kwargs["problem_id"],
                "role": kwargs["role"],
                "status": kwargs["status"],
                "reviewed": True,
                "review_note": kwargs["review_note"],
            }

        api = LibraryWebApi(
            controller=_FakeController(),
            semantic_concept_link_reviewer=fake_concept_link_reviewer,
        )

        payload = api.dispatch(
            "POST",
            "/api/library/concepts/5/problems/22/review",
            {"db_name": ["demo_db"], "db_profile": ["local_mirror"]},
            {"role": "concept", "status": "rechazado", "review_note": "Solo comparte tema."},
        )

        self.assertEqual(payload["schema_version"], "semantic_concept_link_review_v1")
        self.assertEqual(payload["concept_id"], 5)
        self.assertEqual(payload["problem_id"], 22)
        self.assertEqual(payload["status"], "rechazado")
        self.assertEqual(calls[0]["db_name"], "demo_db")
        self.assertEqual(calls[0]["db_profile"], "local_mirror")
        self.assertEqual(calls[0]["role"], "concept")
        self.assertEqual(calls[0]["review_note"], "Solo comparte tema.")

    def test_library_api_reviews_problem_similarity_edge(self) -> None:
        calls = []

        def fake_reviewer(**kwargs):
            calls.append(kwargs)
            return {
                "schema_version": "problem_similarity_edge_review_v1",
                "problem_id": kwargs["problem_id"],
                "similar_problem_id": kwargs["similar_problem_id"],
                "model_id": kwargs["model_id"],
                "status": kwargs["status"],
                "human_verified": True,
                "review_note": kwargs["review_note"],
            }

        api = LibraryWebApi(controller=_FakeController(), semantic_similarity_reviewer=fake_reviewer)

        payload = api.dispatch(
            "POST",
            "/api/library/problems/22/similar/23/review",
            {"db_name": ["demo_db"]},
            {"status": "rechazado", "review_note": "Comparte tema, pero no propiedad."},
        )

        self.assertEqual(payload["schema_version"], "problem_similarity_edge_review_v1")
        self.assertEqual(payload["status"], "rechazado")
        self.assertEqual(calls[0]["db_name"], "demo_db")
        self.assertEqual(calls[0]["problem_id"], 22)
        self.assertEqual(calls[0]["similar_problem_id"], 23)
        self.assertEqual(calls[0]["review_note"], "Comparte tema, pero no propiedad.")

    def test_library_books_response_compacts_instance_health_payload(self) -> None:
        controller = _FakeController()
        controller.books[1]["instances_total"] = 2
        controller.books[1]["instances_health"] = [
            {"tipo": "S01", "total": 4, "sin_revisar": 0, "inconsistentes": 0, "consistentes": 4, "status": "complete"},
            {"tipo": "S02", "total": 3, "sin_revisar": 0, "inconsistentes": 1, "consistentes": 2, "status": "complete_with_inconsistencies"},
        ]
        controller.books[1]["instances_health_json"] = json.dumps(controller.books[1]["instances_health"], ensure_ascii=False)
        controller.books[1]["instances_names"] = "S01, S02"
        controller.instances[1].append(
            {
                "id": 12,
                "libro_id": 1,
                "tipo": "S02",
                "total_esperado": 10,
                "session_path": "",
                "soluciones_dir": "",
                "activo": True,
                "notas": "",
            }
        )
        api = LibraryWebApi(controller=controller)

        books = api.dispatch("GET", "/api/library/books", {"db_name": ["demo_db"]}, {})
        book = books["books"][0]

        self.assertEqual(controller.book_list_calls[-1], ("demo_db", False))
        self.assertNotIn("instances_health", book)
        self.assertNotIn("instances_health_json", book)
        self.assertNotIn("instances_names", book)
        self.assertEqual(book["indicators"]["instancias_en_bd"], 2)
        self.assertEqual(book["indicators"]["errores_total"], 1)

        books_with_instances = api.dispatch("GET", "/api/library/books", {"db_name": ["demo_db"], "include_instances": ["1"]}, {})
        self.assertEqual(controller.book_list_calls[-1], ("demo_db", True))
        instances = {row["tipo"]: row for row in books_with_instances["books"][0]["instances"]}
        self.assertEqual(instances["S02"]["indicators"]["inconsistentes"], 1)

    def test_library_runtime_snapshot_compacts_instance_health_payload(self) -> None:
        controller = _FakeController()
        controller.books[1]["instances_health"] = [
            {"tipo": "S01", "total": 4, "sin_revisar": 0, "inconsistentes": 0, "consistentes": 4, "status": "complete"},
            {"tipo": "S02", "total": 2, "sin_revisar": 0, "inconsistentes": 1, "consistentes": 1, "status": "complete_with_inconsistencies"},
        ]
        controller.books[1]["instances_health_json"] = json.dumps(controller.books[1]["instances_health"], ensure_ascii=False)
        controller.books[1]["instances_names"] = "S01, S02"
        runtime = LibraryWebRuntime(controller=controller)

        snapshot = runtime._snapshot("demo_db")
        book = snapshot["books"][0]

        self.assertEqual(controller.book_list_calls[-1], ("demo_db", False))
        self.assertNotIn("instances_health", book)
        self.assertNotIn("instances_health_json", book)
        self.assertNotIn("instances_names", book)
        self.assertEqual(book["indicators"]["instancias_en_bd"], 2)
        self.assertEqual(book["indicators"]["errores_total"], 1)

    def test_library_mutations_return_lightweight_payloads_without_dashboard_scan(self) -> None:
        controller = _FakeController()
        api = LibraryWebApi(controller=controller)

        created_book = api.dispatch("POST", "/api/library/books", {}, {"db_name": "demo_db", "codigo": "GEO01", "titulo": "Geometria"})
        updated_book = api.dispatch("POST", "/api/library/books/1", {}, {"db_name": "demo_db", "titulo": "Algebra rapida"})
        state = api.dispatch("POST", "/api/library/books/1/state", {}, {"db_name": "demo_db", "estado": "en_progreso"})
        created_instance = api.dispatch("POST", "/api/library/books/1/instances", {}, {"db_name": "demo_db", "tipo": "S02", "total_esperado": 20})
        updated_instance = api.dispatch(
            "POST",
            "/api/library/instances/11/state",
            {},
            {"db_name": "demo_db", "book_id": 1, "tipo": "S01 editada", "total_esperado": 12},
        )

        self.assertEqual(controller.dashboard_calls, [])
        self.assertEqual(created_book["book"]["code"], "GEO01")
        self.assertEqual(updated_book["book"]["title"], "Algebra rapida")
        self.assertEqual(state["book"]["status"], "en_progreso")
        self.assertEqual(created_instance["instance"]["id"], 12)
        self.assertNotIn("dashboard", created_instance)
        self.assertEqual(updated_instance["instance"]["tipo"], "S01 editada")
        self.assertNotIn("instances", updated_instance)
        self.assertNotIn("dashboard", updated_instance)

    def test_timeline_stage_from_counts_prioritizes_bd_and_ocr(self) -> None:
        uploaded = _timeline_stage_from_counts({"subidos_bd": 12, "ocr_done": 12})
        self.assertEqual(uploaded["id"], "candidate")
        self.assertEqual(uploaded["title"], "BD final")

        ocr = _timeline_stage_from_counts({"records_total": 20, "crops_found": 20, "ocr_done": 8, "segments_done": 7})
        self.assertEqual(ocr["id"], "ocr")
        self.assertEqual(ocr["index"], 4)

        review = _timeline_stage_from_counts({"records_total": 2, "problems_total": 1, "normalized_done": 1})
        self.assertEqual(review["id"], "review")
        self.assertIn("1/1 borrador", review["detail"])

    def test_library_api_resolves_book_cover_url(self) -> None:
        controller = _FakeController()
        controller.books[1]["cover_path"] = "E:/tmp/ALG01/cover.png"
        api = LibraryWebApi(controller=controller, file_url_resolver=lambda path: f"/covers/{Path(path).name}")

        books = api.dispatch("GET", "/api/library/books", {"db_name": ["demo_db"]}, {})
        detail = api.dispatch("GET", "/api/library/books/1", {"db_name": ["demo_db"]}, {})

        self.assertEqual(books["books"][0]["cover_url"], "/covers/cover.png")
        self.assertEqual(detail["book"]["cover_url"], "/covers/cover.png")

    def test_library_api_pastes_cover_into_central_store_and_attaches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cover_root = Path(tmp) / "central_covers"
            previous_cover_root = os.environ.get("PDF_LIBRARY_COVER_ROOT")
            os.environ["PDF_LIBRARY_COVER_ROOT"] = str(cover_root)
            try:
                controller = _FakeController()
                controller.books[1]["workspace_dir"] = str(Path(tmp) / "ALG01")
                controller.books[1]["pdf_path"] = str(Path(tmp) / "ALG01" / "book.pdf")
                api = LibraryWebApi(controller=controller, file_url_resolver=lambda path: f"/covers/{Path(path).name}")
                raw = b"\x89PNG\r\n\x1a\ncover"

                self.assertEqual(api.allowed_methods("/api/library/cover/paste"), {"POST"})
                payload = api.dispatch(
                    "POST",
                    "/api/library/cover/paste",
                    {},
                    {
                        "db_name": "demo_db",
                        "book_id": 1,
                        "attach": True,
                        "data_url": "data:image/png;base64," + base64.b64encode(raw).decode("ascii"),
                    },
                )

                cover_path = Path(payload["cover_path"])
                self.assertEqual(payload["schema_version"], "library_cover_pasted_v1")
                self.assertEqual(payload["db_name"], "demo_db")
                self.assertEqual(payload["bytes"], len(raw))
                self.assertTrue(payload["attached"])
                self.assertEqual(payload["cover_url"], "/covers/cover.png")
                self.assertTrue(cover_path.is_relative_to(cover_root))
                self.assertEqual(cover_path.read_bytes(), raw)
                self.assertEqual(controller.books[1]["cover_path"], str(cover_path))
                self.assertEqual(controller.updated_books[-1][1].cover_path, str(cover_path))
            finally:
                if previous_cover_root is None:
                    os.environ.pop("PDF_LIBRARY_COVER_ROOT", None)
                else:
                    os.environ["PDF_LIBRARY_COVER_ROOT"] = previous_cover_root

    def test_library_runtime_serves_registered_book_cover(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cover_path = Path(tmp) / "cover.png"
            cover_path.write_bytes(b"\x89PNG\r\n\x1a\n")
            controller = _FakeController()
            controller.books[1]["cover_path"] = str(cover_path)
            runtime = LibraryWebRuntime(controller=controller)
            try:
                base = runtime.start()
                with urllib.request.urlopen(base + "api/library/books?db_name=demo_db", timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                cover_url = payload["books"][0]["cover_url"]
                self.assertTrue(cover_url.startswith("/api/library/file/"))
                with urllib.request.urlopen(base + cover_url.lstrip("/"), timeout=5) as response:
                    etag = response.headers.get("ETag")
                    self.assertEqual(response.read(), b"\x89PNG\r\n\x1a\n")
                    self.assertEqual(response.headers.get_content_type(), "image/png")
                    self.assertTrue(etag)
                    self.assertIn("immutable", response.headers.get("Cache-Control", ""))
                request = urllib.request.Request(base + cover_url.lstrip("/"), headers={"If-None-Match": etag or ""})
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(raised.exception.code, 304)
            finally:
                runtime.stop()

    def test_library_runtime_gzips_large_json_when_requested(self) -> None:
        controller = _FakeController()
        for index in range(2, 45):
            controller.books[index] = {
                **controller.books[1],
                "id": index,
                "codigo": f"ALG{index:02d}",
                "titulo": f"Algebra volumen {index:02d} " + ("con metadata extensa " * 4),
            }
            controller.instances[index] = []
        runtime = LibraryWebRuntime(controller=controller)
        try:
            base = runtime.start()
            request = urllib.request.Request(
                base + "api/library/books?db_name=demo_db",
                headers={"Accept-Encoding": "gzip"},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(response.headers.get("Content-Encoding"), "gzip")
                payload = json.loads(gzip.decompress(response.read()).decode("utf-8"))
        finally:
            runtime.stop()

        self.assertEqual(payload["schema_version"], "library_books_v1")
        self.assertGreaterEqual(payload["count"], 40)

    def test_library_runtime_pastes_cover_into_central_store_and_attaches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cover_root = root / "central_covers"
            previous_cover_root = os.environ.get("PDF_LIBRARY_COVER_ROOT")
            os.environ["PDF_LIBRARY_COVER_ROOT"] = str(cover_root)
            runtime = None
            try:
                controller = _FakeController()
                controller.books[1]["workspace_dir"] = str(root)
                controller.books[1]["pdf_path"] = str(root / "book.pdf")
                runtime = LibraryWebRuntime(controller=controller)
                base = runtime.start()
                raw = b"\x89PNG\r\n\x1a\ncover"
                payload = _post_json(
                    base,
                    "api/library/cover/paste",
                    {
                        "db_name": "demo_db",
                        "book_id": 1,
                        "attach": True,
                        "data_url": "data:image/png;base64," + base64.b64encode(raw).decode("ascii"),
                    },
                )
                cover_path = Path(payload["cover_path"])
                self.assertEqual(payload["schema_version"], "library_cover_pasted_v1")
                self.assertTrue(payload["attached"])
                self.assertEqual(cover_path.name, "cover.png")
                self.assertTrue(cover_path.is_relative_to(cover_root))
                self.assertIn("demo-db", cover_path.parts)
                self.assertEqual(cover_path.read_bytes(), raw)
                self.assertEqual(controller.books[1]["cover_path"], str(cover_path))
                self.assertEqual(controller.updated_books[-1][1].cover_path, str(cover_path))
            finally:
                if runtime is not None:
                    runtime.stop()
                if previous_cover_root is None:
                    os.environ.pop("PDF_LIBRARY_COVER_ROOT", None)
                else:
                    os.environ["PDF_LIBRARY_COVER_ROOT"] = previous_cover_root

    def test_library_runtime_accepts_large_cover_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cover_root = root / "central_covers"
            previous_cover_root = os.environ.get("PDF_LIBRARY_COVER_ROOT")
            os.environ["PDF_LIBRARY_COVER_ROOT"] = str(cover_root)
            runtime = None
            try:
                controller = _FakeController()
                controller.books[1]["workspace_dir"] = str(root)
                controller.books[1]["pdf_path"] = str(root / "book.pdf")
                runtime = LibraryWebRuntime(controller=controller)
                base = runtime.start()
                raw = b"\x89PNG\r\n\x1a\n" + (b"x" * 1_200_000)
                payload = _post_json(
                    base,
                    "api/library/cover/paste",
                    {
                        "db_name": "demo_db",
                        "book_id": 1,
                        "attach": False,
                        "data_url": "data:image/png;base64," + base64.b64encode(raw).decode("ascii"),
                    },
                )
                cover_path = Path(payload["cover_path"])
                self.assertEqual(payload["schema_version"], "library_cover_pasted_v1")
                self.assertFalse(payload["attached"])
                self.assertEqual(payload["bytes"], len(raw))
                self.assertTrue(cover_path.is_relative_to(cover_root))
                self.assertEqual(cover_path.read_bytes(), raw)
            finally:
                if runtime is not None:
                    runtime.stop()
                if previous_cover_root is None:
                    os.environ.pop("PDF_LIBRARY_COVER_ROOT", None)
                else:
                    os.environ["PDF_LIBRARY_COVER_ROOT"] = previous_cover_root

    def test_local_timeline_counts_prefers_staging_manifest_when_work_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            book = {
                "id": 1,
                "codigo": "ALG01",
                "titulo": "Algebra",
                "workspace_dir": str(root),
                "pdf_path": str(root / "book.pdf"),
            }
            instance = {"id": 11, "libro_id": 1, "tipo": "S01", "total_esperado": 10}
            context = InstancePipelineContext.from_library_instance(book, instance, db_name="demo_db")
            staging_root = context.staging_root()
            staging_root.mkdir(parents=True, exist_ok=True)
            (staging_root / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": "pdf_factory_staging_v1",
                        "records_total": 5,
                        "summary": {
                            "records_total": 5,
                            "crops_found": 5,
                            "ocr_done": 4,
                            "segments_done": 3,
                            "normalized_done": 2,
                            "needs_review": 1,
                            "human_reviewed": 2,
                            "ready": 2,
                            "errors": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )

            class _GoldenFromManifest:
                def load_instance_summary(self, _name):
                    raise AssertionError("No debe leer paginas si staging ya tiene crops/OCR.")

                def load_instance(self, _name):
                    raise AssertionError("No debe leer cada pagina si staging ya tiene crops/OCR.")

            with patch(
                "modulos.modulo13_laboratorio_pdf_segmentacion.controlador_laboratorio_pdf.PdfProblemGoldenController",
                return_value=_GoldenFromManifest(),
            ), patch.object(InstanceStagingStore, "load_records", side_effect=AssertionError("No debe leer cada record si existe manifest.")):
                counts = LibraryWebApi._local_timeline_counts("demo_db", book, instance)

            self.assertEqual(counts["records_total"], 5)
            self.assertEqual(counts["ocr_done"], 4)
            self.assertEqual(counts["segments_done"], 3)
            self.assertEqual(counts["errors"], 1)

    def test_local_timeline_counts_reads_golden_when_staging_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            book = {
                "id": 1,
                "codigo": "ALG01",
                "titulo": "Algebra",
                "workspace_dir": str(root),
                "pdf_path": str(root / "book.pdf"),
            }
            instance = {"id": 11, "libro_id": 1, "tipo": "S01", "total_esperado": 10}
            context = InstancePipelineContext.from_library_instance(book, instance, db_name="demo_db")
            staging_root = context.staging_root()
            staging_root.mkdir(parents=True, exist_ok=True)
            (staging_root / "manifest.json").write_text(
                json.dumps({"schema_version": "pdf_factory_staging_v1", "summary": {"records_total": 0}}),
                encoding="utf-8",
            )

            class _GoldenFromManifest:
                def load_instance_summary(self, _name):
                    return {"pages_total": 2, "reviewed_pages": 1, "boxes_total": 5}

                def load_instance(self, _name):
                    raise AssertionError("No debe leer cada pagina si existe resumen golden.")

            with patch(
                "modulos.modulo13_laboratorio_pdf_segmentacion.controlador_laboratorio_pdf.PdfProblemGoldenController",
                return_value=_GoldenFromManifest(),
            ):
                counts = LibraryWebApi._local_timeline_counts("demo_db", book, instance)

            self.assertEqual(counts["pages_total"], 2)
            self.assertEqual(counts["pages_reviewed"], 1)
            self.assertEqual(counts["boxes_total"], 5)
            self.assertEqual(counts["records_total"], 0)

    def test_timeline_stage_skips_local_scan_when_instance_is_already_in_db(self) -> None:
        controller = _FakeController()
        api = LibraryWebApi(controller=controller)
        book = controller.books[1]
        instance = controller.instances[1][0]

        with patch(
            "modulos.modulo13_laboratorio_pdf_segmentacion.controlador_laboratorio_pdf.PdfProblemGoldenController",
            side_effect=AssertionError("No debe leer staging local si ya esta en BD."),
        ):
            stage = api._instance_timeline_stage(
                "demo_db",
                book,
                instance,
                {"subidos_bd": 3, "total_esperado": 10},
            )

        self.assertEqual(stage["id"], "candidate")
        self.assertEqual(stage["title"], "BD final")
        self.assertEqual(stage["counts"]["subidos_bd"], 3)

    def test_local_timeline_counts_are_cached_per_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            book = {
                "id": 1,
                "codigo": "ALG01",
                "titulo": "Algebra",
                "workspace_dir": str(root),
                "pdf_path": str(root / "book.pdf"),
            }
            instance = {"id": 11, "libro_id": 1, "tipo": "S01", "total_esperado": 10}
            context = InstancePipelineContext.from_library_instance(book, instance, db_name="demo_db")
            context.staging_root().mkdir(parents=True, exist_ok=True)
            (context.staging_root() / "manifest.json").write_text(
                json.dumps({"schema_version": "pdf_factory_staging_v1", "summary": {"records_total": 0}}),
                encoding="utf-8",
            )

            class _GoldenFromManifest:
                def __init__(self):
                    self.calls = 0

                def load_instance_summary(self, _name):
                    self.calls += 1
                    return {"pages_total": 2, "reviewed_pages": 1, "boxes_total": 5}

                def load_instance(self, _name):
                    raise AssertionError("No debe leer paginas si existe resumen.")

            golden = _GoldenFromManifest()
            api = LibraryWebApi(controller=_FakeController())
            with patch(
                "modulos.modulo13_laboratorio_pdf_segmentacion.controlador_laboratorio_pdf.PdfProblemGoldenController",
                return_value=golden,
            ):
                first = api._cached_local_timeline_counts("demo_db", book, instance)
                first["pages_total"] = 999
                second = api._cached_local_timeline_counts("demo_db", book, instance)

        self.assertEqual(golden.calls, 1)
        self.assertEqual(second["pages_total"], 2)
        self.assertEqual(second["records_total"], 0)

    def test_local_timeline_counts_reuses_golden_controller_across_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            book = {
                "id": 1,
                "codigo": "ALG01",
                "titulo": "Algebra",
                "workspace_dir": str(root),
                "pdf_path": str(root / "book.pdf"),
            }
            first_instance = {"id": 11, "libro_id": 1, "tipo": "S01", "total_esperado": 10}
            second_instance = {"id": 12, "libro_id": 1, "tipo": "S02", "total_esperado": 10}

            class _GoldenCounter:
                def __init__(self):
                    self.calls = 0

                def load_instance_summary(self, _name):
                    self.calls += 1
                    return {"pages_total": 2, "reviewed_pages": 1, "boxes_total": 5}

                def load_instance(self, _name):
                    raise AssertionError("No debe leer paginas si existe resumen.")

            golden = _GoldenCounter()
            created = []

            def _factory():
                created.append(golden)
                return golden

            api = LibraryWebApi(controller=_FakeController())
            with patch(
                "modulos.modulo13_laboratorio_pdf_segmentacion.controlador_laboratorio_pdf.PdfProblemGoldenController",
                side_effect=_factory,
            ):
                first = api._cached_local_timeline_counts("demo_db", book, first_instance)
                second = api._cached_local_timeline_counts("demo_db", book, second_instance)

        self.assertEqual(len(created), 1)
        self.assertEqual(golden.calls, 2)
        self.assertEqual(first["pages_total"], 2)
        self.assertEqual(second["boxes_total"], 5)

    def test_library_runtime_serves_factory_file_token_from_non_active_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controller = _FakeController()
            library = LibraryWebRuntime(controller=controller)

            context_one = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(root / "book1.pdf"))
            store_one = InstanceStagingStore(context_one, root=root / "staging_one")
            crop_one = store_one.root / "crops" / "crop_one.png"
            crop_one.parent.mkdir(parents=True, exist_ok=True)
            crop_one.write_bytes(b"\x89PNG\r\n\x1a\none")
            service_one = type(
                "FakeService",
                (),
                {
                    "staging": store_one,
                    "models": type("FakeModels", (), {"to_dict": lambda _self: {}})(),
                    "build_instance_summary": lambda _self: {},
                    "build_stage_overview": lambda _self: [],
                    "load_pages": lambda _self: [],
                },
            )()
            runtime_one = FactoryWebRuntime(context_one, service=service_one)
            setattr(runtime_one, "_library_instance_id", 11)
            crop_url = runtime_one._register_file(crop_one)

            context_two = InstancePipelineContext(book_code="ALG02", instance_type="S02", pdf_path=str(root / "book2.pdf"))
            store_two = InstanceStagingStore(context_two, root=root / "staging_two")
            service_two = type(
                "FakeService",
                (),
                {
                    "staging": store_two,
                    "models": type("FakeModels", (), {"to_dict": lambda _self: {}})(),
                    "build_instance_summary": lambda _self: {},
                    "build_stage_overview": lambda _self: [],
                    "load_pages": lambda _self: [],
                },
            )()
            runtime_two = FactoryWebRuntime(context_two, service=service_two)
            setattr(runtime_two, "_library_instance_id", 12)
            library._factory_runtimes.extend([runtime_one, runtime_two])

            try:
                base = library.start()
                self.assertIn("instance_id=11", crop_url)
                legacy_url_without_instance = crop_url.split("?", 1)[0]
                with urllib.request.urlopen(base + legacy_url_without_instance.lstrip("/"), timeout=5) as response:
                    self.assertEqual(response.read(), b"\x89PNG\r\n\x1a\none")
                    self.assertEqual(response.headers.get_content_type(), "image/png")
            finally:
                library.stop()

    def test_library_runtime_serves_factory_file_from_preferred_instance_first(self) -> None:
        class _PreferredRuntime:
            _library_instance_id = 11

            def __init__(self):
                self.calls = 0

            def _dispatch_api(self, method, path, query, payload):
                self.calls += 1
                self.seen = (method, path, query, payload)
                return _FilePayload(Path("preferred.png"), "image/png")

        class _BadRuntime:
            _library_instance_id = 12

            @property
            def _file_tokens(self):
                raise AssertionError("No debe escanear runtimes no preferidos cuando instance_id resuelve.")

            def _dispatch_api(self, method, path, query, payload):
                raise AssertionError("No debe despachar archivos desde otro runtime.")

        library = LibraryWebRuntime(controller=_FakeController())
        preferred = _PreferredRuntime()
        library._factory_runtime_by_instance_id[11] = preferred
        library._factory_runtimes.extend([_BadRuntime()])

        payload = library._dispatch_factory_file("/api/file/token123", {"instance_id": ["11"]})

        self.assertEqual(payload.path, Path("preferred.png"))
        self.assertEqual(payload.content_type, "image/png")
        self.assertEqual(preferred.calls, 1)
        self.assertEqual(preferred.seen[0], "GET")

    def test_library_runtime_indexes_factory_runtime_by_instance_id(self) -> None:
        class _Runtime:
            def __init__(self, instance_id):
                self._library_instance_id = instance_id

            def stop(self):
                return None

        library = LibraryWebRuntime(controller=_FakeController())
        runtime_one = _Runtime(11)
        runtime_two = _Runtime(12)
        library._factory_runtimes.extend([runtime_one, runtime_two])

        self.assertIs(library._runtime_by_instance_id(12), runtime_two)
        self.assertIs(library._factory_runtime_by_instance_id[12], runtime_two)
        self.assertIs(library._runtime_by_instance_id(12), runtime_two)

    def test_library_runtime_falls_back_to_runtime_that_owns_record_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            class RawOcrService:
                def __init__(self, context: InstancePipelineContext, store: InstanceStagingStore) -> None:
                    self.context = context
                    self.staging = store
                    self.models = type("FakeModels", (), {"to_dict": lambda _self: {}})()

                def build_instance_summary(self):
                    return self.staging.summarize_records()

                def build_stage_overview(self):
                    return []

                def load_pages(self):
                    return []

                def update_raw_ocr(self, record_id, raw_ocr):
                    record = self.staging.get_record(record_id)
                    if record is None:
                        raise KeyError(record_id)
                    record.raw_ocr = str(raw_ocr or "")
                    self.staging.upsert_record(record)
                    return record

            context_one = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(root / "book1.pdf"))
            store_one = InstanceStagingStore(context_one, root=root / "staging_one")
            runtime_one = FactoryWebRuntime(context_one, service=RawOcrService(context_one, store_one))
            setattr(runtime_one, "_library_instance_id", 11)

            context_two = InstancePipelineContext(book_code="ALG02", instance_type="S02", pdf_path=str(root / "book2.pdf"))
            store_two = InstanceStagingStore(context_two, root=root / "staging_two")
            store_two.upsert_record(
                StagingProblemRecord(
                    record_id="crop_002",
                    crop_id="crop_002",
                    crop_path=str(root / "crop_002.png"),
                    source={"page_number": 1},
                )
            )
            runtime_two = FactoryWebRuntime(context_two, service=RawOcrService(context_two, store_two))
            setattr(runtime_two, "_library_instance_id", 12)

            library = LibraryWebRuntime(controller=_FakeController())
            library._factory_runtimes.extend([runtime_one, runtime_two])

            response = library._dispatch_factory_api(
                type("Handler", (), {"headers": {}})(),
                "POST",
                "/api/ocr/raw",
                {},
                {"record_id": "crop_002", "raw_ocr": "texto corregido", "compact": True, "include_summary": False},
                runtime=runtime_one,
            )

            self.assertEqual(response["record"]["record_id"], "crop_002")
            self.assertEqual(store_two.get_record("crop_002").raw_ocr, "texto corregido")

    def test_library_runtime_serves_library_boot_shell(self) -> None:
        runtime = LibraryWebRuntime(controller=_FakeController())
        try:
            base = runtime.start()
            with urllib.request.urlopen(base, timeout=5) as response:
                html = response.read().decode("utf-8")
            self.assertIn('window.__PDF_APP_MODE__ = "library"', html)
            self.assertIn("<h1 id=\"title\">Biblioteca</h1>", html)
            self.assertIn("id=\"themeToggle\"", html)
            self.assertNotIn("Cargando instancia", html)
        finally:
            runtime.stop()

    def test_library_runtime_exposes_shared_app_reload_signal(self) -> None:
        runtime = LibraryWebRuntime(controller=_FakeController())
        try:
            base = runtime.start()
            before = _get_json(base, "api/app/version")
            after = _post_json(base, "api/app/reload-signal", {})
            self.assertEqual(before["schema_version"], "pdf_factory_web_app_version_v1")
            self.assertEqual(after["schema_version"], "pdf_factory_web_app_version_v1")
            self.assertTrue(after["reload_requested"])
            self.assertEqual(before["asset_version"], after["asset_version"])
            self.assertIn("backend_version", before)
            self.assertIn("backend_boot_version", before)
            self.assertFalse(before["backend_restart_required"])
            self.assertNotEqual(before.get("reload_token"), after.get("reload_token"))
        finally:
            runtime.stop()

    def test_library_runtime_exposes_training_cycle_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "normalizer_training_bank").mkdir(parents=True)
            (root / "normalizer_training_bank" / "manifest.json").write_text(
                json.dumps({"schema_version": "test_manifest_v1", "samples_total": 300}),
                encoding="utf-8",
            )
            (root / "segment_training_live").mkdir(parents=True)
            (root / "segment_training_live" / "manifest.json").write_text(
                json.dumps({"schema_version": "test_manifest_v1", "corrected_images": 27}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"TRAINING_DATASETS_ROOT": str(root), "TRAINING_SAMPLE_TARGET": "500"}):
                runtime = LibraryWebRuntime(controller=_FakeController())
                try:
                    base = runtime.start()
                    payload = _get_json(base, "api/training/status")
                finally:
                    runtime.stop()

        self.assertEqual(payload["schema_version"], "pdf_factory_training_cycle_status_v1")
        tasks = {row["key"]: row for row in payload["tasks"]}
        self.assertEqual(set(tasks), {"problem_detector", "ocr_raw", "figure_segmenter", "normalizer"})
        self.assertEqual(tasks["normalizer"]["samples_total"], 300)
        self.assertEqual(tasks["figure_segmenter"]["samples_total"], 27)

    def test_library_runtime_retires_global_ocr_cart_route(self) -> None:
        runtime = LibraryWebRuntime(controller=_FakeController())
        try:
            base = runtime.start()
            with self.assertRaises(urllib.error.HTTPError) as raised:
                _post_json(
                    base,
                    "api/library/ocr-cart/start",
                    {
                        "db_name": "demo_db",
                        "items": [{"book_id": 1, "instance_id": 11}],
                    },
                )
            self.assertEqual(raised.exception.code, 404)
        finally:
            runtime.stop()

    def test_factory_runtime_mounts_library_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(Path(tmp) / "book.pdf"))
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            service = type(
                "FakeService",
                (),
                {
                    "staging": store,
                    "models": type("FakeModels", (), {"to_dict": lambda _self: {}})(),
                    "build_instance_summary": lambda _self: {},
                    "build_stage_overview": lambda _self: [],
                    "load_pages": lambda _self: [],
                },
            )()
            runtime = FactoryWebRuntime(
                context,
                service=service,
                library_api=LibraryWebApi(controller=_FakeController(), runtime_factory=_FakeRuntime, open_url=lambda _u, _t: None),
            )
            try:
                base = runtime.start()
                with urllib.request.urlopen(base + "api/library/books?db_name=demo_db", timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payload["schema_version"], "library_books_v1")
                self.assertEqual(payload["books"][0]["codigo"], "ALG01")
            finally:
                runtime.stop()

    def test_library_runtime_serves_legacy_bootstrap_route(self) -> None:
        runtime = LibraryWebRuntime(controller=_FakeController())
        try:
            base = runtime.start()
            payload = _get_json(base, "api/library/bootstrap?db_name=demo_db")
            self.assertEqual(payload["schema_version"], "library_web_snapshot_v1")
            self.assertEqual(payload["selected_db"], "demo_db")
        finally:
            runtime.stop()

    def test_library_runtime_proxies_factory_api_routes_to_open_factory(self) -> None:
        class ProxyFactory:
            def __init__(self) -> None:
                self.calls = []

            def _dispatch_api(self, method, path, query, payload):
                self.calls.append((method, path, dict(query), dict(payload)))
                return {"schema_version": "proxied_factory_v1", "path": path, "raw_ocr": payload.get("raw_ocr", "")}

            def stop(self) -> None:
                return None

        proxy = ProxyFactory()
        runtime = LibraryWebRuntime(controller=_FakeController())
        runtime._factory_runtimes.append(proxy)
        try:
            base = runtime.start()
            payload = _post_json(base, "api/ocr/raw", {"record_id": "crop_001", "raw_ocr": "texto"})
            self.assertEqual(payload["schema_version"], "proxied_factory_v1")
            self.assertEqual(payload["path"], "/api/ocr/raw")
            self.assertEqual(proxy.calls[0][0:2], ("POST", "/api/ocr/raw"))
            self.assertEqual(proxy.calls[0][3]["record_id"], "crop_001")
            normalizer_job = _post_json(base, "api/normalize/ai/jobs/start", {"record_ids": ["crop_001"]})
            self.assertEqual(normalizer_job["schema_version"], "proxied_factory_v1")
            self.assertEqual(normalizer_job["path"], "/api/normalize/ai/jobs/start")
            self.assertEqual(proxy.calls[1][0:2], ("POST", "/api/normalize/ai/jobs/start"))
            normalizer_status = _get_json(base, "api/normalize/ai/jobs/status?job_id=test-job")
            self.assertEqual(normalizer_status["schema_version"], "proxied_factory_v1")
            self.assertEqual(normalizer_status["path"], "/api/normalize/ai/jobs/status")
            self.assertEqual(proxy.calls[2][0:2], ("GET", "/api/normalize/ai/jobs/status"))
            self.assertEqual(proxy.calls[2][2]["job_id"], ["test-job"])
        finally:
            runtime.stop()

    def test_library_runtime_proxies_factory_api_created_by_library_api(self) -> None:
        class ProxyFactory:
            def __init__(self, context: InstancePipelineContext) -> None:
                self.context = context
                self.calls = []

            def start(self) -> str:
                return "http://127.0.0.1:9999/factory/"

            def _dispatch_api(self, method, path, query, payload):
                self.calls.append((method, path, dict(query), dict(payload)))
                return {"schema_version": "proxied_factory_v1", "path": path, "raw_ocr": payload.get("raw_ocr", "")}

            def stop(self) -> None:
                return None

        proxies = []

        def runtime_factory(context: InstancePipelineContext):
            proxy = ProxyFactory(context)
            proxies.append(proxy)
            return proxy

        runtime = LibraryWebRuntime(controller=_FakeController())
        runtime.library_api = LibraryWebApi(controller=runtime.controller, runtime_factory=runtime_factory, open_url=lambda _u, _t: None)
        try:
            base = runtime.start()
            opened = _post_json(
                base,
                "api/library/instances/11/factory",
                {"db_name": "demo_db", "book_id": 1, "open": False},
            )
            opened_again = _post_json(
                base,
                "api/library/instances/11/factory",
                {"db_name": "demo_db", "book_id": 1, "open": False},
            )
            self.assertEqual(opened["schema_version"], "library_instance_factory_prepared_v1")
            self.assertEqual(opened_again["schema_version"], "library_instance_factory_prepared_v1")
            self.assertEqual(len(proxies), 1)
            payload = _post_json(base, "api/ocr/raw", {"record_id": "crop_001", "raw_ocr": "texto"})

            self.assertEqual(payload["schema_version"], "proxied_factory_v1")
            self.assertEqual(payload["path"], "/api/ocr/raw")
            self.assertEqual(proxies[0].calls[0][0:2], ("POST", "/api/ocr/raw"))
            self.assertEqual(proxies[0].calls[0][3]["raw_ocr"], "texto")
        finally:
            runtime.stop()

    def test_library_runtime_serves_shell_when_controller_is_unavailable(self) -> None:
        dependency_error = LibraryApiError(
            "Biblioteca no puede conectar con la base local: falta instalar psycopg2 en este entorno de Python.",
            status=503,
            code="library_dependency_missing",
        )
        with patch.object(LibraryWebApi, "controller", new_callable=PropertyMock) as controller_property:
            controller_property.side_effect = dependency_error
            runtime = LibraryWebRuntime(controller=None)
            try:
                base = runtime.start()
                with urllib.request.urlopen(base, timeout=5) as response:
                    html = response.read().decode("utf-8")

                self.assertIn("Biblioteca", html)
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(base + "api/library/bootstrap", timeout=5)

                self.assertEqual(raised.exception.code, 503)
                payload = json.loads(raised.exception.read().decode("utf-8"))
                self.assertEqual(payload["code"], "library_dependency_missing")
            finally:
                runtime.stop()

    def test_library_runtime_exposes_latex_word_session_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = root / "sessions" / "s01.json"
            session.parent.mkdir(parents=True)
            session.write_text(
                json.dumps({"output_text": r"\item[\textbf{1.}] Calcule $x$."}, ensure_ascii=False),
                encoding="utf-8",
            )
            session.with_suffix(".docx").write_bytes(b"docx")
            controller = _FakeController()
            controller.books[1]["workspace_dir"] = str(root)
            controller.instances[1][0]["session_path"] = str(session)
            runtime = LibraryWebRuntime(controller=controller)
            try:
                base = runtime.start()
                payload = _get_json(base, "api/word/sessions?db_name=demo_db")

                self.assertEqual(payload["schema_version"], "latex_word_sessions_v1")
                self.assertEqual(payload["summary"]["instances_total"], 1)
                self.assertEqual(payload["summary"]["word_ready"], 1)
                row = payload["books"][0]["instances"][0]
                self.assertTrue(row["word_exists"])
                self.assertIn("/api/library/file/", row["word_url"])
            finally:
                runtime.stop()

    def test_library_runtime_converts_latex_word_session_via_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "Editor_de_practicas"
            repo.mkdir()
            (repo / "latex_to_word.py").write_text(
                "\n".join(
                    [
                        "from pathlib import Path",
                        "import sys",
                        "out = Path(sys.argv[2])",
                        "out.write_bytes(b'docx')",
                        "print(f'Word generado en: {out}')",
                    ]
                ),
                encoding="utf-8",
            )
            session = root / "sessions" / "s01.json"
            session.parent.mkdir(parents=True)
            session.write_text(
                json.dumps({"output_text": r"\item[\textbf{1.}] Calcule $x$."}, ensure_ascii=False),
                encoding="utf-8",
            )
            output = root / "s01.docx"
            runtime = LibraryWebRuntime(controller=_FakeController())
            try:
                base = runtime.start()
                payload = _post_json(
                    base,
                    "api/word/convert",
                    {
                        "session_path": str(session),
                        "output_docx": str(output),
                        "repo": str(repo),
                        "python": sys.executable,
                    },
                )

                self.assertEqual(payload["schema_version"], "latex_word_conversion_v1")
                self.assertTrue(payload["word_exists"])
                self.assertTrue(output.exists())
                self.assertIn("/api/library/file/", payload["word_url"])
            finally:
                runtime.stop()

    def test_library_runtime_lists_latex_word_db_problems_via_api(self) -> None:
        class _PracticeController:
            def contar_problemas(self, db_name, **filters):
                self.db_name = db_name
                self.filters = filters
                return 1

            def obtener_problemas(self, _db_name, *, cantidad, **_filters):
                self.cantidad = cantidad
                return [
                    {
                        "id": 91,
                        "numero_original": 12,
                        "curso": "Geometria",
                        "tema": "Triangulos",
                        "subtema": "Angulos",
                        "autor": "Autor",
                        "editorial": "Editorial",
                        "respuesta_correcta": "D",
                        "enunciado_latex": r"\item[\textbf{12.}] Calcule $x$.",
                    }
                ]

            def listar_cursos(self, _db_name):
                return ["Geometria"]

            def listar_temas(self, _db_name, *, curso=""):
                return [{"id": 7, "nombre": "Triangulos", "curso": curso}]

            def listar_subtemas(self, _db_name, *, tema_id=None):
                return [{"id": 8, "nombre": "Angulos", "tema_id": tema_id}]

            def listar_autores(self, _db_name, **_filters):
                return ["Autor"]

            def listar_editoriales(self, _db_name, **_filters):
                return ["Editorial"]

        runtime = LibraryWebRuntime(controller=_FakeController())
        practice = _PracticeController()
        runtime.word_service.practice_controller = practice
        try:
            base = runtime.start()
            payload = _get_json(
                base,
                "api/word/problems?db_name=demo_db&curso=Geometria&tema_id=7&subtema_id=8&autor=Autor&editorial=Editorial&estado=Todos&clave=D&limit=25",
            )

            self.assertEqual(payload["schema_version"], "latex_word_problem_selection_v1")
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["problems"][0]["id"], 91)
            self.assertEqual(practice.filters["tema_id"], 7)
            self.assertEqual(practice.filters["subtema_id"], 8)
            self.assertEqual(practice.filters["autor"], "Autor")
            self.assertEqual(practice.filters["editorial"], "Editorial")
            self.assertEqual(practice.filters["clave"], "D")
            self.assertEqual(payload["options"]["temas"][0]["id"], 7)
        finally:
            runtime.stop()

    def test_library_runtime_converts_latex_word_db_problems_via_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "Editor_de_practicas"
            repo.mkdir()
            (repo / "latex_to_word.py").write_text(
                "\n".join(
                    [
                        "from pathlib import Path",
                        "import sys",
                        "out = Path(sys.argv[2])",
                        "out.write_bytes(b'docx')",
                        "print(f'Word generado en: {out}')",
                    ]
                ),
                encoding="utf-8",
            )
            image = root / "figura.png"
            image.write_bytes(b"png")

            class _PracticeController:
                def obtener_problemas_por_ids(self, _db_name, *, problem_ids):
                    self.problem_ids = list(problem_ids)
                    return [
                        {
                            "id": 44,
                            "numero_original": 9,
                            "curso": "Geometria",
                            "tema": "Triangulos",
                            "respuesta_correcta": "A",
                            "enunciado_latex": r"\item[\textbf{9.}] Calcule $x$.",
                            "imagenes": [str(image)],
                            "ruta_carpeta": str(root),
                        }
                    ]

            output = root / "bd_practica.docx"
            runtime = LibraryWebRuntime(controller=_FakeController())
            runtime.word_service.practice_controller = _PracticeController()
            try:
                base = runtime.start()
                payload = _post_json(
                    base,
                    "api/word/convert-problems",
                    {
                        "db_name": "demo_db",
                        "problem_ids": [44],
                        "output_docx": str(output),
                        "repo": str(repo),
                        "python": sys.executable,
                        "title": "Practica BD",
                    },
                )

                self.assertEqual(payload["schema_version"], "latex_word_db_conversion_v1")
                self.assertTrue(payload["word_exists"])
                self.assertTrue(output.exists())
                self.assertTrue((root / "bd_practica__db_images" / "p44_figura.png").exists())
                self.assertIn("/api/library/file/", payload["word_url"])
            finally:
                runtime.stop()

    def test_library_runtime_hides_internal_tracebacks_from_client_with_request_id(self) -> None:
        class _BrokenController(_FakeController):
            def listar_libros(self, *_args, **_kwargs):
                raise RuntimeError("secret internal path E:/private/token")

        runtime = LibraryWebRuntime(controller=_BrokenController())
        try:
            base = runtime.start()
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(base + "api/library/bootstrap", timeout=5)

            self.assertEqual(raised.exception.code, 500)
            payload = json.loads(raised.exception.read().decode("utf-8"))
            self.assertEqual(payload["schema_version"], "library_web_error_v1")
            self.assertEqual(payload["code"], "internal_error")
            self.assertRegex(payload.get("request_id", ""), r"^[a-f0-9]{12}$")
            self.assertIn(payload["request_id"], payload["error"])
            self.assertNotIn("traceback", payload)
            self.assertNotIn("secret internal path", payload["error"])
        finally:
            runtime.stop()


if __name__ == "__main__":
    unittest.main()
