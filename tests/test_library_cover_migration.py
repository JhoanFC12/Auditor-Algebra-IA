from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from tools.migrate_library_covers import migrate_existing_covers


class _FakeCursor:
    def __init__(self, controller):
        self.controller = controller
        self._last = None

    def execute(self, sql, params=()):
        statement = " ".join(str(sql).split()).lower()
        self._last = statement
        if "update libros_escaneo" in statement:
            cover_path, book_id = params
            self.controller.books[int(book_id)]["cover_path"] = cover_path
            self.controller.updated_server.append((int(book_id), cover_path))
        elif "update libro_artifacts_locales" in statement:
            cover_path, book_id = params
            self.controller.updated_local.append((int(book_id), cover_path))

    def fetchone(self):
        if "to_regclass" in str(self._last or ""):
            return ("libro_artifacts_locales",)
        return None


class _FakeConnection:
    def __init__(self, controller):
        self.controller = controller
        self.committed = False
        self.closed = False

    def cursor(self):
        return _FakeCursor(self.controller)

    def commit(self):
        self.committed = True

    def rollback(self):
        raise AssertionError("rollback should not be called")

    def close(self):
        self.closed = True


class _FakeDb:
    def __init__(self, controller):
        self.controller = controller

    def get_connection(self, _db_name):
        return _FakeConnection(self.controller)


class _FakeController:
    def __init__(self, cover_path: str):
        self.db = _FakeDb(self)
        self.ensured = []
        self.updated_server = []
        self.updated_local = []
        self.books = {
            7: {
                "id": 7,
                "codigo": "GEO01",
                "titulo": "Geometria",
                "cover_path": cover_path,
                "cover_path_local": cover_path,
                "cover_path_server": cover_path,
            }
        }

    def listar_bases_datos(self):
        return ["demo_db"]

    def listar_libros(self, _db_name):
        return [dict(row) for row in self.books.values()]

    def _ensure_schema(self, db_name):
        self.ensured.append(db_name)


class LibraryCoverMigrationTests(unittest.TestCase):
    def test_migrates_existing_cover_to_central_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "external" / "cover.png"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"\x89PNG\r\n\x1a\ncover")
            central = root / "central"
            previous = os.environ.get("PDF_LIBRARY_COVER_ROOT")
            os.environ["PDF_LIBRARY_COVER_ROOT"] = str(central)
            try:
                controller = _FakeController(str(source))
                dry = migrate_existing_covers(controller, commit=False)
                self.assertEqual(dry["totals"]["copied"], 1)
                self.assertEqual(dry["totals"]["updated"], 0)
                self.assertFalse(controller.updated_server)

                applied = migrate_existing_covers(controller, commit=True)
                self.assertEqual(applied["totals"]["copied"], 1)
                self.assertEqual(applied["totals"]["updated"], 1)
                target = Path(controller.books[7]["cover_path"])
                self.assertTrue(target.exists())
                self.assertTrue(target.is_relative_to(central))
                self.assertEqual(target.read_bytes(), source.read_bytes())
                self.assertEqual(controller.updated_local, [(7, str(target))])
            finally:
                if previous is None:
                    os.environ.pop("PDF_LIBRARY_COVER_ROOT", None)
                else:
                    os.environ["PDF_LIBRARY_COVER_ROOT"] = previous


if __name__ == "__main__":
    unittest.main()
