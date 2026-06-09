import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modulos.modulo0_transcriptor.controlador_transcriptor import PersistableItem, TranscriptorController


class _FakeCursor:
    def __init__(self, fetches=None):
        self._fetches = list(fetches or [])
        self.executed = []
        self.rowcount = 0

    def execute(self, query, params=None):
        sql = " ".join(str(query).split())
        self.executed.append((sql, params))
        if "DELETE FROM problema_pending_changes" in sql:
            self.rowcount = 1
        elif sql.startswith("DELETE FROM problemas"):
            self.rowcount = 2
        else:
            self.rowcount = 0

    def fetchone(self):
        if self._fetches:
            return self._fetches.pop(0)
        return None

    def close(self):
        pass


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


class _FakeDb:
    def __init__(self, conn):
        self._conn = conn

    def get_connection(self, _db_name):
        return self._conn


def _controller_with_conn(conn):
    controller = TranscriptorController.__new__(TranscriptorController)
    controller.db = _FakeDb(conn)
    controller._asegurar_tabla_problemas = lambda _conn: None
    controller._obtener_columnas_problemas = lambda _conn: {
        "numero_original",
        "archivo_origen",
        "enunciado_latex",
        "libro_codigo",
        "codigo_instancia",
    }
    controller._pg_table_exists = lambda _conn, table: table == "problema_pending_changes"
    controller._pg_column_exists = lambda _conn, table, column: (
        (table == "problema_pending_changes" and column in {"libro_codigo", "codigo_instancia", "numero_original"})
        or (table == "problemas" and column == "codigo_instancia")
    )
    return controller


def test_insertar_items_prunes_obsolete_rows_for_same_group():
    cursor = _FakeCursor(fetches=[(10,), None, (11,)])
    conn = _FakeConnection(cursor)
    controller = _controller_with_conn(conn)

    items = [
        PersistableItem(
            archivo_origen="demo.pdf",
            item_latex=r"\item[\textbf{1.}] Enunciado uno",
            libro_codigo="vesalius-algebra-temas",
            instancia_tipo="s05_polinomios_especiales",
        ),
        PersistableItem(
            archivo_origen="demo.pdf",
            item_latex=r"\item[\textbf{2.}] Enunciado dos",
            libro_codigo="vesalius-algebra-temas",
            instancia_tipo="s05_polinomios_especiales",
        ),
    ]

    result = controller.insertar_items("demo_db", items=items)

    assert result == {
        "inserted": 1,
        "updated": 1,
        "skipped": 0,
        "invalid": 0,
        "deleted": 2,
        "pending_deleted": 1,
    }
    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert any(
        "DELETE FROM problema_pending_changes" in sql and "numero_original NOT IN" in sql
        for sql, _params in cursor.executed
    )
    assert any(
        sql.startswith("DELETE FROM problemas") and "numero_original NOT IN" in sql
        for sql, _params in cursor.executed
    )
