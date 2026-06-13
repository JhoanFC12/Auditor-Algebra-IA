import unittest
import importlib.util
import sys
import types
from pathlib import Path


if "database.connection" not in sys.modules:
    fake_database = types.ModuleType("database")
    fake_database.__path__ = []  # type: ignore[attr-defined]
    fake_connection = types.ModuleType("database.connection")

    class DatabaseManager:
        pass

    fake_connection.DatabaseManager = DatabaseManager
    sys.modules.setdefault("database", fake_database)
    sys.modules["database.connection"] = fake_connection
    _USING_FAKE_DATABASE_MODULE = True
else:
    _USING_FAKE_DATABASE_MODULE = False


MODULE_PATH = Path(__file__).resolve().parents[1] / "modulos" / "modulo6_practicas" / "controlador_practicas.py"
spec = importlib.util.spec_from_file_location("practice_controller_under_test", MODULE_PATH)
controller_module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(controller_module)
PracticeBuilderController = controller_module.PracticeBuilderController
if _USING_FAKE_DATABASE_MODULE:
    sys.modules.pop("database.connection", None)
    sys.modules.pop("database", None)


HYBRID_SCHEMA = {
    "uses_catalog": True,
    "has_temas_table": True,
    "has_subtemas_table": True,
    "has_books_table": False,
    "has_origins_table": False,
    "course_column": "curso",
    "topic_column": "tema",
    "subtopic_column": "subtema",
    "author_column": None,
    "editorial_column": None,
    "response_column": "respuesta_correcta",
    "key_flag_column": None,
    "problem_type_column": None,
    "math_consistency_column": None,
    "tema_id_column": "tema_id",
    "subtema_id_column": "subtema_id",
    "book_id_column": None,
    "book_code_column": None,
    "source_file_column": None,
    "images_column": None,
    "folder_column": None,
    "problem_columns": ["curso", "tema", "subtema", "tema_id", "subtema_id"],
}


class FakePracticeController(PracticeBuilderController):
    def __init__(self):
        self._schema_cache = {}
        self.queries = []
        self.schema = dict(HYBRID_SCHEMA)

    def _schema_info(self, db_name):
        return dict(self.schema)

    def _query_distinct(self, db_name, sql, params=()):
        self.queries.append((sql, params))
        if "FROM temas" in sql:
            return ["Geometria"]
        if "FROM problemas" in sql:
            return ["Geometría", "Algebra"]
        return []

    def _query_rows(self, db_name, sql, params=()):
        self.queries.append((sql, params))
        if "FROM temas t" in sql:
            return [(7, "Triángulos", "Geometría")]
        if "SELECT DISTINCT TRIM(CAST(p.tema" in sql:
            return [("Triangulos",), ("Poligonos",)]
        if "SELECT DISTINCT TRIM(CAST(p.subtema" in sql:
            return [("Lineas notables",)]
        return []


class PracticeControllerHybridMetadataTests(unittest.TestCase):
    def test_parse_meta_ref_accepts_catalog_direct_and_legacy_values(self):
        ctrl = FakePracticeController()

        catalog = ctrl._parse_meta_ref("catalog:topic:12:Triángulos: extra")
        self.assertEqual(catalog["source"], "catalog")
        self.assertEqual(catalog["kind"], "topic")
        self.assertEqual(catalog["id"], 12)
        self.assertEqual(catalog["text"], "Triángulos: extra")

        direct = ctrl._parse_meta_ref("direct:topic:Polígonos")
        self.assertEqual(direct["source"], "direct")
        self.assertIsNone(direct["id"])
        self.assertEqual(direct["text"], "Polígonos")

        legacy_id = ctrl._parse_meta_ref("9")
        self.assertEqual(legacy_id["source"], "catalog")
        self.assertEqual(legacy_id["id"], 9)

        legacy_text = ctrl._parse_meta_ref("Geometría plana")
        self.assertEqual(legacy_text["source"], "direct")
        self.assertEqual(legacy_text["text"], "Geometría plana")

    def test_build_filters_matches_catalog_and_direct_metadata(self):
        ctrl = FakePracticeController()
        topic_ref = ctrl._catalog_ref("topic", 7, "Triángulos")
        subtopic_ref = ctrl._direct_ref("subtopic", "Lineas notables")

        join_sql, where_sql, params = ctrl._build_filters(
            HYBRID_SCHEMA,
            curso="Geometría",
            tema_id=topic_ref,
            subtema_id=subtopic_ref,
            autor="",
            editorial="",
            estado="Todos",
            clave="Todos",
        )

        self.assertIn("LEFT JOIN temas", join_sql)
        self.assertIn("p.tema_id = %s", where_sql)
        self.assertIn("p.tema", where_sql)
        self.assertIn("t.nombre", where_sql)
        self.assertIn("p.subtema", where_sql)
        self.assertIn("s.nombre", where_sql)
        self.assertIn("p.curso", where_sql)
        self.assertIn("t.area", where_sql)
        self.assertEqual(params.count("geometria"), 2)
        self.assertIn(7, params)
        self.assertEqual(params.count("triangulos"), 2)
        self.assertEqual(params.count("lineas notables"), 2)

    def test_meta_select_prefers_direct_text_but_falls_back_to_catalog(self):
        ctrl = FakePracticeController()

        expr = ctrl._meta_select_expr(
            HYBRID_SCHEMA,
            "topic_column",
            "COALESCE(t.nombre,'')",
            use_catalog=True,
        )

        self.assertIn("NULLIF(TRIM(CAST(p.tema AS text)), '')", expr)
        self.assertIn("COALESCE(t.nombre,'')", expr)

    def test_listar_cursos_merges_catalog_and_direct_values(self):
        ctrl = FakePracticeController()

        cursos = ctrl.listar_cursos("db")

        self.assertEqual(cursos, ["Algebra", "Geometría"])

    def test_listar_temas_returns_catalog_and_direct_references(self):
        ctrl = FakePracticeController()

        temas = ctrl.listar_temas("db", curso="Geometría")
        ids = [tema["id"] for tema in temas]
        names = [tema["nombre"] for tema in temas]

        self.assertIn("Triángulos", names)
        self.assertIn("Poligonos", names)
        self.assertTrue(any(str(value).startswith("catalog:topic:7:") for value in ids))
        self.assertTrue(any(str(value).startswith("direct:topic:") for value in ids))


if __name__ == "__main__":
    unittest.main()
