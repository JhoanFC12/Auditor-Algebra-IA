from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path

from modulos.instance_factory.library_api import LibraryWebApi
from modulos.instance_factory.library_web_server import LibraryWebRuntime
from modulos.instance_factory.models import InstancePipelineContext
from modulos.instance_factory.staging import InstanceStagingStore
from modulos.instance_factory.web_server import FactoryWebRuntime


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
        self.dashboard_calls = []

    def listar_bases_datos(self):
        return ["demo_db"]

    def listar_libros(self, _db_name):
        return [dict(row) for row in self.books.values()]

    def obtener_libro(self, _db_name, libro_id):
        row = self.books.get(int(libro_id))
        return dict(row) if row else None

    def listar_instancias_libro(self, _db_name, libro_id):
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
                self.assertEqual(books["books"][0]["instances"][0]["tipo"], "S01")

                detail = api.dispatch("GET", "/api/library/books/1", {"db_name": ["demo_db"]}, {})
                self.assertEqual(controller.dashboard_calls, [1])
                self.assertEqual(detail["instances"][0]["indicators"]["escaneados_sesion"], 4)
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

    def test_library_api_resolves_book_cover_url(self) -> None:
        controller = _FakeController()
        controller.books[1]["cover_path"] = "E:/tmp/ALG01/cover.png"
        api = LibraryWebApi(controller=controller, file_url_resolver=lambda path: f"/covers/{Path(path).name}")

        books = api.dispatch("GET", "/api/library/books", {"db_name": ["demo_db"]}, {})
        detail = api.dispatch("GET", "/api/library/books/1", {"db_name": ["demo_db"]}, {})

        self.assertEqual(books["books"][0]["cover_url"], "/covers/cover.png")
        self.assertEqual(detail["book"]["cover_url"], "/covers/cover.png")

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
                    self.assertEqual(response.read(), b"\x89PNG\r\n\x1a\n")
                    self.assertEqual(response.headers.get_content_type(), "image/png")
            finally:
                runtime.stop()

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
            self.assertEqual(opened["schema_version"], "library_instance_factory_prepared_v1")
            payload = _post_json(base, "api/ocr/raw", {"record_id": "crop_001", "raw_ocr": "texto"})

            self.assertEqual(payload["schema_version"], "proxied_factory_v1")
            self.assertEqual(payload["path"], "/api/ocr/raw")
            self.assertEqual(proxies[0].calls[0][0:2], ("POST", "/api/ocr/raw"))
            self.assertEqual(proxies[0].calls[0][3]["raw_ocr"], "texto")
        finally:
            runtime.stop()


if __name__ == "__main__":
    unittest.main()
