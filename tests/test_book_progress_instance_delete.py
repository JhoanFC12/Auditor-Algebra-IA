from pathlib import Path

from modulos.modulo9_organizador_libros.controlador_organizador_libros import (
    BookInstanceUpdateInput,
    BookProgressController,
)


class _FakeCursor:
    def __init__(self, fetches=None, rowcounts=None):
        self._fetches = list(fetches or [])
        self._rowcounts = list(rowcounts or [])
        self.executed = []
        self.rowcount = 0
        self.description = []

    def execute(self, query, params=None):
        self.executed.append((" ".join(str(query).split()), params))
        self.rowcount = self._rowcounts.pop(0) if self._rowcounts else 0

    def fetchone(self):
        if self._fetches:
            return self._fetches.pop(0)
        return None


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
    controller = BookProgressController.__new__(BookProgressController)
    controller.db = _FakeDb(conn)
    controller._ensured_dbs = set()
    controller._ensure_schema = lambda _db_name: None
    controller._instance_column_name = lambda _conn: "codigo_instancia"
    controller._problem_instance_column_name = lambda _conn: "codigo_instancia"
    controller._touch_book = lambda cur, libro_id: cur.execute(
        "UPDATE libros_escaneo SET updated_at = NOW() WHERE id = %s",
        (int(libro_id),),
    )
    controller._normalize_instance_type = lambda value: str(value or "").strip()
    controller._normalize_resource_path_text = lambda value, prefer_existing=False: str(value or "").strip()
    controller._prepare_instance_workspace = lambda _workspace_dir, _tipo: None
    controller._default_session_path_for_instance = lambda workspace_dir, tipo: Path(workspace_dir) / "sessions" / f"{tipo}.session.json"
    controller._default_solutions_dir_for_instance = lambda workspace_dir, tipo: Path(workspace_dir) / "solutions" / str(tipo)
    return controller


def test_eliminar_instancia_removes_pending_and_problems_before_instance():
    cursor = _FakeCursor(
        fetches=[(17, 8, "s01_teoria_de_exponentes", "vesalius-algebra-temas")],
        rowcounts=[0, 3, 5, 1, 1],
    )
    conn = _FakeConnection(cursor)
    controller = _controller_with_conn(conn)
    controller._pg_table_exists = lambda _conn, table: table in {"problemas", "problema_pending_changes"}
    controller._pg_column_exists = lambda _conn, table, column: (
        (table == "problema_pending_changes" and column in {"libro_codigo", "codigo_instancia"})
        or (table == "problemas" and column == "codigo_instancia")
    )

    summary = controller.eliminar_instancia("demo_db", 17)

    assert summary == {
        "instancia_id": 17,
        "libro_id": 8,
        "problems_deleted": 5,
        "pending_deleted": 3,
    }
    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert "DELETE FROM problema_pending_changes" in cursor.executed[1][0]
    assert "DELETE FROM problemas" in cursor.executed[2][0]
    assert "DELETE FROM libro_instancias_escaneo" in cursor.executed[3][0]


def test_eliminar_instancia_raises_when_not_found():
    cursor = _FakeCursor(fetches=[None], rowcounts=[0])
    conn = _FakeConnection(cursor)
    controller = _controller_with_conn(conn)
    controller._pg_table_exists = lambda _conn, _table: False
    controller._pg_column_exists = lambda _conn, _table, _column: False

    try:
        controller.eliminar_instancia("demo_db", 999)
    except ValueError as exc:
        assert "Instancia no encontrada" in str(exc)
    else:
        raise AssertionError("Se esperaba ValueError para instancia inexistente.")

    assert conn.commits == 0
    assert conn.rollbacks == 1


def test_actualizar_instancia_renames_problem_and_pending_references():
    cursor = _FakeCursor(
        fetches=[(17, "problemas_propuestos", "", "", "C:/workspace/libro", "impecus-book")],
        rowcounts=[1, 4, 2, 1],
    )
    conn = _FakeConnection(cursor)
    controller = _controller_with_conn(conn)
    controller._pg_table_exists = lambda _conn, table: table in {"problemas", "problema_pending_changes"}
    controller._pg_column_exists = lambda _conn, table, column: (
        (table == "problema_pending_changes" and column in {"libro_codigo", "codigo_instancia"})
        or (table == "problemas" and column == "codigo_instancia")
    )

    payload = BookInstanceUpdateInput(
        libro_id=8,
        tipo="problemas_resueltos",
        total_esperado=0,
        session_path="",
        soluciones_dir="",
        notas="",
        activo=True,
    )
    controller.actualizar_instancia("demo_db", 17, payload)

    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert "UPDATE libro_instancias_escaneo" in cursor.executed[1][0]
    assert "UPDATE problemas SET codigo_instancia = %s" in cursor.executed[2][0]
    assert cursor.executed[2][1] == ("problemas_resueltos", "impecus-book", "problemas_propuestos")
    assert "UPDATE problema_pending_changes SET codigo_instancia = %s" in cursor.executed[3][0]
    assert cursor.executed[3][1] == ("problemas_resueltos", "impecus-book", "problemas_propuestos")
