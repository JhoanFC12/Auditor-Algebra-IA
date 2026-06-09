from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import time
from typing import Callable, Dict, Iterable, List, Optional, Set, TypeVar

from database.connection import DatabaseManager
from utils.project_layout import (
    ensure_project_dirs,
    normalize_path,
    project_dirs,
    remap_legacy_drive_path,
    resolve_workspace_root,
    slugify_name,
)
from utils.runtime_log import get_logger


BOOK_STATES = ("pendiente", "en_progreso", "completo")
TAG_CLAVE_RE = re.compile(r"\[\[\s*clave\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)
TAG_SOLUCION_RE = re.compile(r"\[\[\s*solucion(?:ario)?\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)
_TRANSIENT_DB_CODES = {
    "08000",
    "08001",
    "08003",
    "08004",
    "08006",
    "08007",
    "08P01",
    "57P01",
    "57P02",
    "57P03",
}
T = TypeVar("T")
LOGGER = get_logger("mod9.controller")


@dataclass(slots=True)
class BookCreateInput:
    codigo: str
    titulo: str
    autor: str = ""
    editorial: str = ""
    edicion: str = ""
    curso: str = ""
    workspace_dir: str = ""
    pdf_path: str = ""
    cover_path: str = ""
    estado: str = "pendiente"
    notas: str = ""
    activo: bool = True


@dataclass(slots=True)
class BookUpdateInput(BookCreateInput):
    pass


@dataclass(slots=True)
class BookInstanceInput:
    libro_id: int
    tipo: str
    total_esperado: int = 0
    session_path: str = ""
    soluciones_dir: str = ""
    notas: str = ""
    activo: bool = True


@dataclass(slots=True)
class BookInstanceUpdateInput(BookInstanceInput):
    pass


@dataclass(slots=True)
class BookInstanceSessionStats:
    instancia_id: int
    tipo: str
    total_esperado: int
    escaneados_sesion: int
    con_clave_sesion: int
    con_solucion_sesion: int
    sin_clave_sesion: int
    sin_solucion_sesion: int
    pdf_path: str
    session_path: str
    soluciones_dir: str
    pdf_status: str
    session_status: str
    soluciones_status: str
    subidos_bd: int
    subidos_bd_con_solucion: int
    subidos_bd_sin_solucion: int
    subidos_bd_consistentes: int
    subidos_bd_inconsistentes: int
    subidos_bd_sin_revisar: int
    faltantes: int
    porcentaje: float


@dataclass(slots=True)
class BookProgressSummary:
    libro_id: int
    codigo: str
    titulo: str
    estado: str
    workspace_dir: str
    pdf_path: str
    pdf_status: str
    instancias: List[BookInstanceSessionStats]
    total_instancias: int
    total_esperado: int
    escaneados_sesion_total: int
    con_clave_sesion_total: int
    con_solucion_sesion_total: int
    subidos_bd_total: int
    subidos_bd_con_solucion_total: int
    subidos_bd_sin_solucion_total: int
    subidos_bd_consistentes_total: int
    subidos_bd_inconsistentes_total: int
    subidos_bd_sin_revisar_total: int
    faltantes_total: int
    porcentaje_total: float


class BookProgressController:
    def __init__(self) -> None:
        self.db = DatabaseManager()
        self._ensured_dbs: Set[str] = set()

    def listar_bases_datos(self) -> List[str]:
        return self.db.listar_bases_datos()

    def listar_libros(self, db_name: str) -> List[dict]:
        t0 = time.time()
        LOGGER.info("listar_libros_start db=%s", db_name)
        self._ensure_schema(db_name)
        conn = self.db.get_connection(db_name)
        try:
            instance_col = self._instance_column_name(conn)
            cur = conn.cursor()
            if self._pg_table_exists(conn, "libro_artifacts_locales"):
                cur.execute(
                    f"""
                    SELECT
                        b.id,
                        b.codigo,
                        b.titulo,
                        b.autor,
                        b.editorial,
                        b.edicion,
                        b.curso,
                        COALESCE(b.workspace_dir, '') AS workspace_dir_server,
                        COALESCE(b.pdf_path, '') AS pdf_path_server,
                        COALESCE(b.cover_path, '') AS cover_path_server,
                        COALESCE(art.workspace_dir_local, '') AS workspace_dir_local,
                        COALESCE(art.pdf_path_local, '') AS pdf_path_local,
                        COALESCE(art.cover_path_local, '') AS cover_path_local,
                        COALESCE(inst.instances_total, 0) AS instances_total,
                        COALESCE(inst.instances_expected_total, 0) AS instances_expected_total,
                        COALESCE(inst.instances_names, '-') AS instances_names,
                        COALESCE(inst.instances_session_count, 0) AS instances_session_count,
                        COALESCE(inst.instances_solutions_count, 0) AS instances_solutions_count,
                        b.estado,
                        b.notas,
                        b.activo
                    FROM libros_escaneo b
                    LEFT JOIN LATERAL (
                        SELECT
                            COALESCE(la.workspace_dir_local, '') AS workspace_dir_local,
                            COALESCE(la.pdf_path_local, '') AS pdf_path_local,
                            COALESCE(la.cover_path_local, '') AS cover_path_local
                        FROM libro_artifacts_locales la
                        WHERE la.libro_id = b.id
                        ORDER BY CASE
                            WHEN LOWER(COALESCE(la.host_name, '')) = %s THEN 0
                            WHEN la.activo THEN 1
                            ELSE 2
                        END,
                        COALESCE(la.updated_at, la.created_at) DESC,
                        la.id DESC
                        LIMIT 1
                    ) art ON TRUE
                    LEFT JOIN (
                        SELECT
                            i.libro_id,
                            COUNT(*)::int AS instances_total,
                            COALESCE(SUM(GREATEST(COALESCE(i.total_esperado, 0), 0)), 0)::int AS instances_expected_total,
                            COALESCE(
                                string_agg(
                                    DISTINCT NULLIF(LOWER(COALESCE(i.{instance_col}, '')), ''),
                                    ', '
                                    ORDER BY NULLIF(LOWER(COALESCE(i.{instance_col}, '')), '')
                                ),
                                '-'
                            ) AS instances_names,
                            COUNT(*) FILTER (WHERE COALESCE(i.session_path, '') <> '')::int AS instances_session_count,
                            COUNT(*) FILTER (WHERE COALESCE(i.soluciones_dir, '') <> '')::int AS instances_solutions_count
                        FROM libro_instancias_escaneo i
                        GROUP BY i.libro_id
                    ) inst ON inst.libro_id = b.id
                    ORDER BY CASE b.estado
                        WHEN 'pendiente' THEN 1
                        WHEN 'en_progreso' THEN 2
                        WHEN 'completo' THEN 3
                        ELSE 9
                    END,
                    LOWER(b.titulo) ASC,
                    b.id ASC
                    """,
                    (self._current_host_name(),),
                )
            else:
                cur.execute(
                    f"""
                    SELECT
                        id,
                        codigo,
                        titulo,
                        autor,
                        editorial,
                        edicion,
                        curso,
                        COALESCE(workspace_dir, '') AS workspace_dir_server,
                        COALESCE(pdf_path, '') AS pdf_path_server,
                        COALESCE(cover_path, '') AS cover_path_server,
                        '' AS workspace_dir_local,
                        '' AS pdf_path_local,
                        '' AS cover_path_local,
                        COALESCE(inst.instances_total, 0) AS instances_total,
                        COALESCE(inst.instances_expected_total, 0) AS instances_expected_total,
                        COALESCE(inst.instances_names, '-') AS instances_names,
                        COALESCE(inst.instances_session_count, 0) AS instances_session_count,
                        COALESCE(inst.instances_solutions_count, 0) AS instances_solutions_count,
                        estado,
                        notas,
                        activo
                    FROM libros_escaneo b
                    LEFT JOIN (
                        SELECT
                            i.libro_id,
                            COUNT(*)::int AS instances_total,
                            COALESCE(SUM(GREATEST(COALESCE(i.total_esperado, 0), 0)), 0)::int AS instances_expected_total,
                            COALESCE(
                                string_agg(
                                    DISTINCT NULLIF(LOWER(COALESCE(i.{instance_col}, '')), ''),
                                    ', '
                                    ORDER BY NULLIF(LOWER(COALESCE(i.{instance_col}, '')), '')
                                ),
                                '-'
                            ) AS instances_names,
                            COUNT(*) FILTER (WHERE COALESCE(i.session_path, '') <> '')::int AS instances_session_count,
                            COUNT(*) FILTER (WHERE COALESCE(i.soluciones_dir, '') <> '')::int AS instances_solutions_count
                        FROM libro_instancias_escaneo i
                        GROUP BY i.libro_id
                    ) inst ON inst.libro_id = b.id
                    ORDER BY CASE estado
                        WHEN 'pendiente' THEN 1
                        WHEN 'en_progreso' THEN 2
                        WHEN 'completo' THEN 3
                        ELSE 9
                    END,
                    LOWER(titulo) ASC,
                    id ASC
                    """
                )
            rows = self._fetchall_dicts(cur)
            health_by_book = self._query_books_instance_health(conn)
            for row in rows:
                self._hydrate_book_resource_paths(row)
                book_id = int(row.get("id") or 0)
                health = health_by_book.get(
                    book_id,
                    {
                        "items": [],
                        "consistentes_total": 0,
                        "inconsistentes_total": 0,
                        "sin_revisar_total": 0,
                    },
                )
                row["instances_health"] = list(health.get("items") or [])
                row["instances_health_json"] = json.dumps(row["instances_health"], ensure_ascii=False)
                row["consistency_consistentes_total"] = int(health.get("consistentes_total") or 0)
                row["consistency_inconsistentes_total"] = int(health.get("inconsistentes_total") or 0)
                row["consistency_sin_revisar_total"] = int(health.get("sin_revisar_total") or 0)
            LOGGER.info("listar_libros_ok db=%s count=%s elapsed=%.3fs", db_name, len(rows), time.time() - t0)
            return rows
        finally:
            conn.close()

    def obtener_libro(self, db_name: str, libro_id: int) -> Optional[dict]:
        t0 = time.time()
        LOGGER.info("obtener_libro_start db=%s libro_id=%s", db_name, libro_id)
        self._ensure_schema(db_name)
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            if self._pg_table_exists(conn, "libro_artifacts_locales"):
                cur.execute(
                    """
                    SELECT
                        b.id,
                        b.codigo,
                        b.titulo,
                        b.autor,
                        b.editorial,
                        b.edicion,
                        b.curso,
                        COALESCE(b.workspace_dir, '') AS workspace_dir_server,
                        COALESCE(b.pdf_path, '') AS pdf_path_server,
                        COALESCE(b.cover_path, '') AS cover_path_server,
                        COALESCE(art.workspace_dir_local, '') AS workspace_dir_local,
                        COALESCE(art.pdf_path_local, '') AS pdf_path_local,
                        COALESCE(art.cover_path_local, '') AS cover_path_local,
                        b.estado,
                        b.notas,
                        b.activo
                    FROM libros_escaneo b
                    LEFT JOIN LATERAL (
                        SELECT
                            COALESCE(la.workspace_dir_local, '') AS workspace_dir_local,
                            COALESCE(la.pdf_path_local, '') AS pdf_path_local,
                            COALESCE(la.cover_path_local, '') AS cover_path_local
                        FROM libro_artifacts_locales la
                        WHERE la.libro_id = b.id
                        ORDER BY CASE
                            WHEN LOWER(COALESCE(la.host_name, '')) = %s THEN 0
                            WHEN la.activo THEN 1
                            ELSE 2
                        END,
                        COALESCE(la.updated_at, la.created_at) DESC,
                        la.id DESC
                        LIMIT 1
                    ) art ON TRUE
                    WHERE b.id = %s
                    """,
                    (self._current_host_name(), int(libro_id)),
                )
            else:
                cur.execute(
                    """
                    SELECT
                        id,
                        codigo,
                        titulo,
                        autor,
                        editorial,
                        edicion,
                        curso,
                        COALESCE(workspace_dir, '') AS workspace_dir_server,
                        COALESCE(pdf_path, '') AS pdf_path_server,
                        COALESCE(cover_path, '') AS cover_path_server,
                        '' AS workspace_dir_local,
                        '' AS pdf_path_local,
                        '' AS cover_path_local,
                        estado,
                        notas,
                        activo
                    FROM libros_escaneo
                    WHERE id = %s
                    """,
                    (int(libro_id),),
                )
            row = self._fetchone_dict(cur)
            if row:
                self._hydrate_book_resource_paths(row)
            LOGGER.info("obtener_libro_ok db=%s libro_id=%s found=%s elapsed=%.3fs", db_name, libro_id, bool(row), time.time() - t0)
            return row
        finally:
            conn.close()

    def crear_libro(self, db_name: str, payload: BookCreateInput) -> int:
        t0 = time.time()
        LOGGER.info("crear_libro_start db=%s codigo=%s titulo=%s", db_name, payload.codigo, payload.titulo)
        self._ensure_schema(db_name)
        data = self._validate_book_payload(payload)

        def _op() -> int:
            conn = self.db.get_connection(db_name)
            try:
                cur = conn.cursor()
                titulo = self._resolve_book_title(data.codigo, data.titulo)
                codigo = self._resolve_book_code(cur, data.codigo, titulo)
                workspace_dir, effective_pdf_path = self._prepare_book_workspace(
                    codigo=codigo,
                    titulo=titulo,
                    pdf_path=data.pdf_path,
                    workspace_dir=data.workspace_dir,
                )
                cur.execute(
                    """
                    INSERT INTO libros_escaneo
                        (codigo, titulo, autor, editorial, edicion, curso, workspace_dir, pdf_path, cover_path, estado, notas, activo)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        codigo,
                        titulo,
                        data.autor,
                        data.editorial,
                        data.edicion,
                        data.curso,
                        workspace_dir,
                        effective_pdf_path,
                        data.cover_path,
                        data.estado,
                        data.notas,
                        bool(data.activo),
                    ),
                )
                libro_id = int(cur.fetchone()[0])
                conn.commit()
                return libro_id
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

        try:
            book_id = self._run_db_operation_with_retry(_op, retries=1)
            LOGGER.info("crear_libro_ok db=%s book_id=%s elapsed=%.3fs", db_name, book_id, time.time() - t0)
            return book_id
        except Exception as exc:
            LOGGER.exception("crear_libro_error db=%s elapsed=%.3fs err=%s", db_name, time.time() - t0, exc)
            raise ValueError(f"No se pudo guardar el libro: {exc}") from exc

    def actualizar_libro(self, db_name: str, libro_id: int, payload: BookUpdateInput) -> None:
        t0 = time.time()
        LOGGER.info("actualizar_libro_start db=%s libro_id=%s codigo=%s titulo=%s", db_name, libro_id, payload.codigo, payload.titulo)
        self._ensure_schema(db_name)
        data = self._validate_book_payload(payload)

        def _op() -> None:
            conn = self.db.get_connection(db_name)
            try:
                cur = conn.cursor()
                titulo = self._resolve_book_title(data.codigo, data.titulo)
                codigo = self._resolve_book_code(cur, data.codigo, titulo, exclude_id=int(libro_id))
                cur.execute("SELECT COALESCE(workspace_dir, '') FROM libros_escaneo WHERE id = %s", (int(libro_id),))
                row = cur.fetchone()
                current_workspace = str((row[0] if row else "") or "").strip()
                workspace_dir, effective_pdf_path = self._prepare_book_workspace(
                    codigo=codigo,
                    titulo=titulo,
                    pdf_path=data.pdf_path,
                    workspace_dir=str(data.workspace_dir or current_workspace or "").strip(),
                )
                cur.execute(
                    """
                    UPDATE libros_escaneo
                    SET codigo = %s,
                        titulo = %s,
                        autor = %s,
                        editorial = %s,
                        edicion = %s,
                        curso = %s,
                        workspace_dir = %s,
                        pdf_path = %s,
                        cover_path = %s,
                        estado = %s,
                        notas = %s,
                        activo = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        codigo,
                        titulo,
                        data.autor,
                        data.editorial,
                        data.edicion,
                        data.curso,
                        workspace_dir,
                        effective_pdf_path,
                        data.cover_path,
                        data.estado,
                        data.notas,
                        bool(data.activo),
                        int(libro_id),
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

        try:
            self._run_db_operation_with_retry(_op, retries=1)
            LOGGER.info("actualizar_libro_ok db=%s libro_id=%s elapsed=%.3fs", db_name, libro_id, time.time() - t0)
        except Exception as exc:
            LOGGER.exception("actualizar_libro_error db=%s libro_id=%s elapsed=%.3fs err=%s", db_name, libro_id, time.time() - t0, exc)
            raise ValueError(f"No se pudo actualizar el libro: {exc}") from exc

    def eliminar_libro(self, db_name: str, libro_id: int) -> None:
        self._ensure_schema(db_name)
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM libros_escaneo WHERE id = %s", (int(libro_id),))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def listar_instancias_libro(self, db_name: str, libro_id: int) -> List[dict]:
        self._ensure_schema(db_name)
        conn = self.db.get_connection(db_name)
        try:
            instance_col = self._instance_column_name(conn)
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT id, libro_id, {instance_col} AS tipo, total_esperado, session_path,
                       soluciones_dir, activo, notas
                FROM libro_instancias_escaneo
                WHERE libro_id = %s
                ORDER BY LOWER({instance_col}) ASC, id ASC
                """,
                (int(libro_id),),
            )
            return [self._hydrate_instance_row_paths(row) for row in self._fetchall_dicts(cur)]
        finally:
            conn.close()

    def obtener_instancia(self, db_name: str, libro_id: int, tipo: str) -> Optional[dict]:
        clean_type = self._normalize_instance_type(tipo)
        self._ensure_schema(db_name)
        conn = self.db.get_connection(db_name)
        try:
            instance_col = self._instance_column_name(conn)
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT id, libro_id, {instance_col} AS tipo, total_esperado, session_path,
                       soluciones_dir, activo, notas
                FROM libro_instancias_escaneo
                WHERE libro_id = %s AND {instance_col} = %s
                """,
                (int(libro_id), clean_type),
            )
            return self._hydrate_instance_row_paths(self._fetchone_dict(cur))
        finally:
            conn.close()

    def crear_instancia(self, db_name: str, payload: BookInstanceInput) -> int:
        self._ensure_schema(db_name)
        data = self._validate_instance_payload(payload)
        conn = self.db.get_connection(db_name)
        try:
            instance_col = self._instance_column_name(conn)
            cur = conn.cursor()
            cur.execute(
                "SELECT COALESCE(workspace_dir, '') FROM libros_escaneo WHERE id = %s",
                (int(data.libro_id),),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("Libro no encontrado.")
            workspace_dir = self._normalize_resource_path_text(str((row[0] if row else "") or "").strip(), prefer_existing=False)
            self._prepare_instance_workspace(workspace_dir, data.tipo)
            session_path = data.session_path or str(self._default_session_path_for_instance(workspace_dir, data.tipo))
            soluciones_dir = data.soluciones_dir or str(self._default_solutions_dir_for_instance(workspace_dir, data.tipo))
            session_path = self._normalize_resource_path_text(session_path, prefer_existing=False)
            soluciones_dir = self._normalize_resource_path_text(soluciones_dir, prefer_existing=False)
            cur.execute(
                f"""
                INSERT INTO libro_instancias_escaneo
                    (libro_id, {instance_col}, total_esperado, session_path, soluciones_dir, activo, notas)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    int(data.libro_id),
                    data.tipo,
                    data.total_esperado,
                    session_path,
                    soluciones_dir,
                    bool(data.activo),
                    data.notas,
                ),
            )
            instancia_id = int(cur.fetchone()[0])
            self._touch_book(cur, int(data.libro_id))
            conn.commit()
            return instancia_id
        except Exception as exc:
            conn.rollback()
            if str(getattr(exc, "pgcode", "") or "") == "23505":
                raise ValueError(f"La instancia '{data.tipo}' ya existe para este libro.") from exc
            raise
        finally:
            conn.close()

    def actualizar_instancia(self, db_name: str, instancia_id: int, payload: BookInstanceUpdateInput) -> None:
        self._ensure_schema(db_name)
        data = self._validate_instance_payload(payload)
        conn = self.db.get_connection(db_name)
        try:
            instance_col = self._instance_column_name(conn)
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT i.libro_id, i.{instance_col} AS tipo, COALESCE(i.session_path, ''), COALESCE(i.soluciones_dir, ''), COALESCE(l.workspace_dir, '')
                FROM libro_instancias_escaneo i
                JOIN libros_escaneo l ON l.id = i.libro_id
                WHERE i.id = %s
                """,
                (int(instancia_id),),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("Instancia no encontrada.")
            libro_id = int(row[0])
            old_tipo = self._normalize_instance_type(str(row[1] or ""))
            old_session_path = self._normalize_resource_path_text(str(row[2] or "").strip(), prefer_existing=True)
            old_soluciones_dir = self._normalize_resource_path_text(str(row[3] or "").strip(), prefer_existing=True)
            workspace_dir = self._normalize_resource_path_text(str(row[4] or "").strip(), prefer_existing=False)
            self._prepare_instance_workspace(workspace_dir, data.tipo)
            old_default_session = str(self._default_session_path_for_instance(workspace_dir, old_tipo))
            old_default_soluciones = str(self._default_solutions_dir_for_instance(workspace_dir, old_tipo))
            new_default_session = str(self._default_session_path_for_instance(workspace_dir, data.tipo))
            new_default_soluciones = str(self._default_solutions_dir_for_instance(workspace_dir, data.tipo))
            next_session_path = str(data.session_path or "").strip()
            next_soluciones_dir = str(data.soluciones_dir or "").strip()
            if not next_session_path:
                next_session_path = new_default_session if (not old_session_path or old_session_path == old_default_session) else old_session_path
            if not next_soluciones_dir:
                next_soluciones_dir = new_default_soluciones if (not old_soluciones_dir or old_soluciones_dir == old_default_soluciones) else old_soluciones_dir
            next_session_path = self._normalize_resource_path_text(next_session_path, prefer_existing=False)
            next_soluciones_dir = self._normalize_resource_path_text(next_soluciones_dir, prefer_existing=False)
            cur.execute(
                f"""
                UPDATE libro_instancias_escaneo
                SET {instance_col} = %s,
                    total_esperado = %s,
                    session_path = %s,
                    soluciones_dir = %s,
                    activo = %s,
                    notas = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    data.tipo,
                    data.total_esperado,
                    next_session_path,
                    next_soluciones_dir,
                    bool(data.activo),
                    data.notas,
                    int(instancia_id),
                ),
            )
            self._touch_book(cur, libro_id)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            if str(getattr(exc, "pgcode", "") or "") == "23505":
                raise ValueError(f"Ya existe otra instancia con el nombre '{data.tipo}'.") from exc
            raise
        finally:
            conn.close()

    def eliminar_instancia(self, db_name: str, instancia_id: int) -> Dict[str, int]:
        self._ensure_schema(db_name)
        conn = self.db.get_connection(db_name)
        try:
            instance_col = self._instance_column_name(conn)
            problem_table_exists = self._pg_table_exists(conn, "problemas")
            queue_table_exists = self._pg_table_exists(conn, "problema_pending_changes")
            problem_instance_col = self._problem_instance_column_name(conn) if problem_table_exists else None
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT i.id, i.libro_id, i.{instance_col} AS tipo, COALESCE(l.codigo, '')
                FROM libro_instancias_escaneo i
                JOIN libros_escaneo l ON l.id = i.libro_id
                WHERE i.id = %s
                """,
                (int(instancia_id),),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("Instancia no encontrada.")

            libro_id = int(row[1])
            tipo = self._normalize_instance_type(str(row[2] or ""))
            libro_codigo = str(row[3] or "").strip()
            pending_deleted = 0
            problems_deleted = 0

            if queue_table_exists:
                if self._pg_column_exists(conn, "problema_pending_changes", "libro_codigo") and self._pg_column_exists(
                    conn,
                    "problema_pending_changes",
                    "codigo_instancia",
                ):
                    cur.execute(
                        """
                        DELETE FROM problema_pending_changes
                        WHERE libro_codigo = %s
                          AND codigo_instancia = %s
                        """,
                        (libro_codigo, tipo),
                    )
                elif problem_table_exists and problem_instance_col:
                    cur.execute(
                        f"""
                        DELETE FROM problema_pending_changes
                        WHERE problema_id IN (
                            SELECT id
                            FROM problemas
                            WHERE libro_codigo = %s
                              AND {problem_instance_col} = %s
                        )
                        """,
                        (libro_codigo, tipo),
                    )
                pending_deleted = max(int(cur.rowcount or 0), 0)

            if problem_table_exists and problem_instance_col:
                cur.execute(
                    f"""
                    DELETE FROM problemas
                    WHERE libro_codigo = %s
                      AND {problem_instance_col} = %s
                    """,
                    (libro_codigo, tipo),
                )
                problems_deleted = max(int(cur.rowcount or 0), 0)

            cur.execute("DELETE FROM libro_instancias_escaneo WHERE id = %s", (int(instancia_id),))
            if int(cur.rowcount or 0) <= 0:
                raise ValueError("Instancia no encontrada.")

            self._touch_book(cur, libro_id)
            conn.commit()
            return {
                "instancia_id": int(instancia_id),
                "libro_id": libro_id,
                "problems_deleted": problems_deleted,
                "pending_deleted": pending_deleted,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def obtener_dashboard_libro(self, db_name: str, libro_id: int) -> BookProgressSummary:
        libro = self.obtener_libro(db_name, libro_id)
        if not libro:
            raise ValueError("Libro no encontrado.")
        instance_rows = self.listar_instancias_libro(db_name, libro_id)
        instance_stats = [
            self._build_instance_stats(
                db_name,
                libro_codigo=str(libro.get("codigo") or "").strip(),
                instancia=row,
            )
            for row in instance_rows
        ]
        total_esperado = int(sum(stats.total_esperado for stats in instance_stats))
        escaneados_total = int(sum(stats.escaneados_sesion for stats in instance_stats))
        con_clave_total = int(sum(stats.con_clave_sesion for stats in instance_stats))
        con_solucion_total = int(sum(stats.con_solucion_sesion for stats in instance_stats))
        subidos_total = int(sum(stats.subidos_bd for stats in instance_stats))
        subidos_con_sol_total = int(sum(stats.subidos_bd_con_solucion for stats in instance_stats))
        subidos_sin_sol_total = int(sum(stats.subidos_bd_sin_solucion for stats in instance_stats))
        subidos_consistentes_total = int(sum(stats.subidos_bd_consistentes for stats in instance_stats))
        subidos_inconsistentes_total = int(sum(stats.subidos_bd_inconsistentes for stats in instance_stats))
        subidos_sin_revisar_total = int(sum(stats.subidos_bd_sin_revisar for stats in instance_stats))
        faltantes = max(total_esperado - escaneados_total, 0)
        porcentaje_total = 0.0 if total_esperado <= 0 else (escaneados_total / total_esperado)
        pdf_path = str(libro.get("pdf_path") or "").strip()
        workspace_dir = str(libro.get("workspace_dir") or "").strip()
        return BookProgressSummary(
            libro_id=int(libro["id"]),
            codigo=str(libro.get("codigo") or ""),
            titulo=str(libro["titulo"] or ""),
            estado=str(libro.get("estado") or "pendiente"),
            workspace_dir=workspace_dir,
            pdf_path=pdf_path,
            pdf_status=self._resource_status_label(pdf_path, self._path_exists(pdf_path)),
            instancias=instance_stats,
            total_instancias=len(instance_stats),
            total_esperado=total_esperado,
            escaneados_sesion_total=escaneados_total,
            con_clave_sesion_total=con_clave_total,
            con_solucion_sesion_total=con_solucion_total,
            subidos_bd_total=subidos_total,
            subidos_bd_con_solucion_total=subidos_con_sol_total,
            subidos_bd_sin_solucion_total=subidos_sin_sol_total,
            subidos_bd_consistentes_total=subidos_consistentes_total,
            subidos_bd_inconsistentes_total=subidos_inconsistentes_total,
            subidos_bd_sin_revisar_total=subidos_sin_revisar_total,
            faltantes_total=faltantes,
            porcentaje_total=porcentaje_total,
        )

    def _validate_book_payload(self, payload: BookCreateInput) -> BookCreateInput:
        codigo = str(payload.codigo or "").strip()
        titulo = str(payload.titulo or "").strip()
        estado = str(payload.estado or "pendiente").strip().lower()
        if estado not in BOOK_STATES:
            raise ValueError("Estado invalido. Usa pendiente, en_progreso o completo.")
        return payload.__class__(
            codigo=codigo,
            titulo=titulo,
            autor=str(payload.autor or "").strip(),
            editorial=str(payload.editorial or "").strip(),
            edicion=str(payload.edicion or "").strip(),
            curso=str(payload.curso or "").strip(),
            workspace_dir=str(payload.workspace_dir or "").strip(),
            pdf_path=str(payload.pdf_path or "").strip(),
            cover_path=str(payload.cover_path or "").strip(),
            estado=estado,
            notas=str(payload.notas or "").strip(),
            activo=bool(payload.activo),
        )

    def _resolve_book_title(self, codigo: str, titulo: str) -> str:
        clean_title = str(titulo or "").strip()
        if clean_title:
            return clean_title
        clean_code = str(codigo or "").strip()
        if clean_code:
            return clean_code
        return "Libro sin titulo"

    def _resolve_book_code(self, cur, codigo: str, titulo: str, exclude_id: int | None = None) -> str:
        base = str(codigo or "").strip() or self._slugify_book_code(titulo)
        candidate = base
        suffix = 2
        while True:
            if exclude_id is None:
                cur.execute("SELECT id FROM libros_escaneo WHERE codigo = %s LIMIT 1", (candidate,))
            else:
                cur.execute(
                    "SELECT id FROM libros_escaneo WHERE codigo = %s AND id <> %s LIMIT 1",
                    (candidate, int(exclude_id)),
                )
            if cur.fetchone() is None:
                return candidate
            candidate = f"{base}-{suffix}"
            suffix += 1

    def _slugify_book_code(self, text: str) -> str:
        return slugify_name(text, "libro")

    def _validate_instance_payload(self, payload: BookInstanceInput) -> BookInstanceInput:
        return payload.__class__(
            libro_id=int(payload.libro_id),
            tipo=self._normalize_instance_type(payload.tipo),
            total_esperado=max(int(payload.total_esperado or 0), 0),
            session_path=self._normalize_resource_path_text(str(payload.session_path or "").strip(), prefer_existing=False),
            soluciones_dir=self._normalize_resource_path_text(str(payload.soluciones_dir or "").strip(), prefer_existing=False),
            activo=bool(payload.activo),
            notas=str(payload.notas or "").strip(),
        )

    def _is_transient_db_error(self, exc: Exception) -> bool:
        pgcode = str(getattr(exc, "pgcode", "") or "")
        if pgcode in _TRANSIENT_DB_CODES:
            return True
        message = str(exc or "").lower()
        return (
            "server closed the connection unexpectedly" in message
            or "connection not open" in message
            or "terminating connection due to administrator command" in message
            or "could not receive data from server" in message
            or "connection reset by peer" in message
        )

    def _run_db_operation_with_retry(self, operation: Callable[[], T], *, retries: int = 1) -> T:
        attempt = 0
        while True:
            try:
                return operation()
            except Exception as exc:
                if attempt >= retries or not self._is_transient_db_error(exc):
                    raise
                attempt += 1
                time.sleep(0.25 * attempt)

    def _ensure_schema(self, db_name: str) -> None:
        target_db = str(db_name or "").strip() or self.db.db_name
        if target_db in self._ensured_dbs:
            return
        t0 = time.time()
        LOGGER.info("ensure_schema_start db=%s", target_db)
        conn = self.db.get_connection(target_db)
        lock_contention = False
        try:
            conn.autocommit = True
            cur = conn.cursor()
            try:
                cur.execute("SET lock_timeout TO '3s';")
            except Exception:
                pass
            try:
                cur.execute("SET statement_timeout TO '8s';")
            except Exception:
                pass
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS libros_escaneo (
                    id SERIAL PRIMARY KEY,
                    codigo VARCHAR(80) NOT NULL UNIQUE,
                    titulo VARCHAR(255) NOT NULL,
                    autor VARCHAR(255) NOT NULL DEFAULT '',
                    editorial VARCHAR(255) NOT NULL DEFAULT '',
                    edicion VARCHAR(120) NOT NULL DEFAULT '',
                    curso VARCHAR(120) NOT NULL DEFAULT '',
                    notas TEXT NOT NULL DEFAULT '',
                    activo BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
                """
            )
            cur.execute("ALTER TABLE libros_escaneo ADD COLUMN IF NOT EXISTS tema_base VARCHAR(120) NOT NULL DEFAULT '';")
            cur.execute("ALTER TABLE libros_escaneo ADD COLUMN IF NOT EXISTS total_problemas_esperado INT NOT NULL DEFAULT 0;")
            cur.execute("ALTER TABLE libros_escaneo ADD COLUMN IF NOT EXISTS total_resueltos_esperado INT NOT NULL DEFAULT 0;")
            cur.execute("ALTER TABLE libros_escaneo ADD COLUMN IF NOT EXISTS total_propuestos_esperado INT NOT NULL DEFAULT 0;")
            cur.execute("ALTER TABLE libros_escaneo ADD COLUMN IF NOT EXISTS workspace_dir TEXT NOT NULL DEFAULT '';")
            cur.execute("ALTER TABLE libros_escaneo ADD COLUMN IF NOT EXISTS pdf_path TEXT NOT NULL DEFAULT '';")
            cur.execute("ALTER TABLE libros_escaneo ADD COLUMN IF NOT EXISTS cover_path TEXT NOT NULL DEFAULT '';")
            cur.execute("ALTER TABLE libros_escaneo ADD COLUMN IF NOT EXISTS session_path TEXT NOT NULL DEFAULT '';")
            cur.execute("ALTER TABLE libros_escaneo ADD COLUMN IF NOT EXISTS segmentos_dir TEXT NOT NULL DEFAULT '';")
            cur.execute("ALTER TABLE libros_escaneo ADD COLUMN IF NOT EXISTS soluciones_dir TEXT NOT NULL DEFAULT '';")
            cur.execute("ALTER TABLE libros_escaneo ADD COLUMN IF NOT EXISTS estado VARCHAR(20) NOT NULL DEFAULT 'pendiente';")
            cur.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'libros_escaneo_estado_check'
                    ) THEN
                        ALTER TABLE libros_escaneo
                        ADD CONSTRAINT libros_escaneo_estado_check
                        CHECK (estado IN ('pendiente', 'en_progreso', 'completo'));
                    END IF;
                END $$;
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS libro_instancias_escaneo (
                    id SERIAL PRIMARY KEY,
                    libro_id INT NOT NULL REFERENCES libros_escaneo(id) ON DELETE CASCADE,
                    codigo_instancia VARCHAR(80) NOT NULL,
                    total_esperado INT NOT NULL DEFAULT 0,
                    pdf_path TEXT NOT NULL DEFAULT '',
                    session_path TEXT NOT NULL DEFAULT '',
                    soluciones_dir TEXT NOT NULL DEFAULT '',
                    activo BOOLEAN NOT NULL DEFAULT TRUE,
                    notas TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE (libro_id, codigo_instancia)
                );
                """
            )
            cur.execute("ALTER TABLE libro_instancias_escaneo ADD COLUMN IF NOT EXISTS total_esperado INT NOT NULL DEFAULT 0;")
            if self._pg_column_exists(conn, "libro_instancias_escaneo", "tipo") and not self._pg_column_exists(conn, "libro_instancias_escaneo", "codigo_instancia"):
                cur.execute("ALTER TABLE libro_instancias_escaneo RENAME COLUMN tipo TO codigo_instancia;")
            if self._pg_column_exists(conn, "libro_instancias_escaneo", "codigo_instancia"):
                cur.execute("ALTER TABLE libro_instancias_escaneo ALTER COLUMN codigo_instancia TYPE VARCHAR(80);")
            cur.execute("ALTER TABLE libro_instancias_escaneo ADD COLUMN IF NOT EXISTS pdf_path TEXT NOT NULL DEFAULT '';")
            cur.execute("ALTER TABLE libro_instancias_escaneo ADD COLUMN IF NOT EXISTS session_path TEXT NOT NULL DEFAULT '';")
            cur.execute("ALTER TABLE libro_instancias_escaneo ADD COLUMN IF NOT EXISTS soluciones_dir TEXT NOT NULL DEFAULT '';")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS libro_archivos_avance (
                    id SERIAL PRIMARY KEY,
                    libro_id INT NOT NULL REFERENCES libros_escaneo(id) ON DELETE CASCADE,
                    archivo_origen VARCHAR(255) NOT NULL,
                    alias_visible VARCHAR(255) NOT NULL DEFAULT '',
                    total_esperado_archivo INT DEFAULT NULL,
                    pdf_path TEXT NOT NULL DEFAULT '',
                    session_path TEXT NOT NULL DEFAULT '',
                    activo BOOLEAN NOT NULL DEFAULT TRUE,
                    notas TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE (libro_id, archivo_origen)
                );
                """
            )
            cur.execute("ALTER TABLE libro_archivos_avance ADD COLUMN IF NOT EXISTS pdf_path TEXT NOT NULL DEFAULT '';")
            cur.execute("ALTER TABLE libro_archivos_avance ADD COLUMN IF NOT EXISTS session_path TEXT NOT NULL DEFAULT '';")
            if self._pg_table_exists(conn, "problemas"):
                cur.execute("ALTER TABLE problemas ADD COLUMN IF NOT EXISTS libro_codigo VARCHAR(80) NOT NULL DEFAULT '';")
                if self._pg_column_exists(conn, "problemas", "instancia_tipo") and not self._pg_column_exists(conn, "problemas", "codigo_instancia"):
                    cur.execute("ALTER TABLE problemas RENAME COLUMN instancia_tipo TO codigo_instancia;")
                if self._pg_column_exists(conn, "problemas", "codigo_instancia"):
                    cur.execute("ALTER TABLE problemas ALTER COLUMN codigo_instancia TYPE VARCHAR(80);")
            self._ensured_dbs.add(target_db)
            LOGGER.info("ensure_schema_ok db=%s elapsed=%.3fs", target_db, time.time() - t0)
        except Exception as exc:
            # Si hay contencion de locks de DDL (55P03), no bloqueamos la UI.
            # El esquema suele estar ya creado en esta fase y podemos continuar.
            if str(getattr(exc, "pgcode", "") or "") == "55P03":
                lock_contention = True
                self._ensured_dbs.add(target_db)
                LOGGER.warning("ensure_schema_lock_contention db=%s elapsed=%.3fs", target_db, time.time() - t0)
            elif self._is_schema_permission_error(exc) and self._schema_ready_for_runtime(conn):
                self._ensured_dbs.add(target_db)
                LOGGER.info(
                    "ensure_schema_runtime_only db=%s elapsed=%.3fs reason=permission_denied",
                    target_db,
                    time.time() - t0,
                )
            else:
                LOGGER.exception("ensure_schema_error db=%s elapsed=%.3fs err=%s", target_db, time.time() - t0, exc)
                raise
        finally:
            conn.close()
        if lock_contention:
            return

    def _ensure_book_instances(self, db_name: str, libro_id: int) -> None:
        return

    def _seed_default_instances(self, cur, libro_id: int) -> None:
        instance_col = self._instance_column_name(cur.connection)
        cur.execute(
            """
            SELECT COALESCE(total_resueltos_esperado, 0), COALESCE(total_propuestos_esperado, 0),
                   COALESCE(pdf_path, ''), COALESCE(session_path, ''), COALESCE(soluciones_dir, ''),
                   COALESCE(workspace_dir, '')
            FROM libros_escaneo
            WHERE id = %s
            """,
            (int(libro_id),),
        )
        row = cur.fetchone() or (0, 0, "", "", "", "")
        legacy_resueltos = max(int(row[0] or 0), 0)
        legacy_propuestos = max(int(row[1] or 0), 0)
        legacy_session = str(row[3] or "").strip()
        legacy_soluciones = str(row[4] or "").strip()
        workspace_dir = str(row[5] or "").strip()
        for tipo, total, seed_resources in (
            ("resueltos", legacy_resueltos, True),
            ("propuestos", legacy_propuestos, False),
        ):
            default_session = str(self._default_session_path_for_instance(workspace_dir, tipo))
            default_soluciones = str(self._default_solutions_dir_for_instance(workspace_dir, tipo))
            cur.execute(
                f"""
                INSERT INTO libro_instancias_escaneo
                    (libro_id, {instance_col}, total_esperado, session_path, soluciones_dir, activo, notas)
                VALUES (%s, %s, %s, %s, %s, TRUE, '')
                ON CONFLICT (libro_id, {instance_col}) DO NOTHING
                """,
                (
                    int(libro_id),
                    tipo,
                    int(total),
                    legacy_session if seed_resources and legacy_session else default_session,
                    legacy_soluciones if seed_resources and legacy_soluciones else default_soluciones,
                ),
            )
            cur.execute(
                f"""
                UPDATE libro_instancias_escaneo
                SET session_path = %s,
                    soluciones_dir = %s,
                    updated_at = NOW()
                WHERE libro_id = %s
                  AND {instance_col} = %s
                  AND (
                        COALESCE(session_path, '') = ''
                     OR COALESCE(soluciones_dir, '') = ''
                  )
                """,
                (default_session, default_soluciones, int(libro_id), tipo),
            )

    def _normalize_instance_type(self, raw_type: str) -> str:
        value = re.sub(r"[^A-Za-z0-9_-]+", "_", str(raw_type or "").strip().lower()).strip("_")
        if not value:
            raise ValueError("Nombre de instancia invalido.")
        return value

    def _current_host_name(self) -> str:
        return str(os.getenv("COMPUTERNAME", "") or os.getenv("HOSTNAME", "") or "default").strip().lower() or "default"

    def _preferred_resource_path(self, *candidates: object) -> str:
        cleaned: List[str] = []
        for candidate in candidates:
            path = str(candidate or "").strip()
            if not path:
                continue
            try:
                path = str(remap_legacy_drive_path(path, prefer_existing=True))
            except Exception:
                pass
            if path not in cleaned:
                cleaned.append(path)
        for path in cleaned:
            if self._path_exists(path):
                return path
        return cleaned[0] if cleaned else ""

    def _local_library_root_candidates(self) -> List[Path]:
        repo_root = Path(__file__).resolve().parents[2]
        candidates = [
            repo_root / "storage" / "math_bank_local_mirror" / "library",
            repo_root.parent / "MathContentStudio" / "scan-math-db" / "storage" / "math_bank_local_mirror" / "library",
        ]
        deduped: List[Path] = []
        for candidate in candidates:
            normalized = self._normalize_path(candidate)
            if normalized not in deduped:
                deduped.append(normalized)
        return deduped

    def _resolve_local_mirror_book_dir(self, codigo: str) -> str:
        clean_code = str(codigo or "").strip()
        if not clean_code:
            return ""
        for root in self._local_library_root_candidates():
            candidate = root / clean_code
            if candidate.exists():
                return str(self._normalize_path(candidate))
        return ""

    def _resolve_local_mirror_asset(self, codigo: str, server_path: str, *fallback_names: str) -> str:
        book_dir = self._resolve_local_mirror_book_dir(codigo)
        if not book_dir:
            return ""
        base_dir = Path(book_dir)
        candidate_names: List[str] = []
        server_name = Path(str(server_path or "").strip()).name
        if server_name:
            candidate_names.append(server_name)
        for raw_name in fallback_names:
            clean_name = str(raw_name or "").strip()
            if clean_name and clean_name not in candidate_names:
                candidate_names.append(clean_name)
        for name in candidate_names:
            candidate = base_dir / name
            if candidate.exists():
                return str(self._normalize_path(candidate))
        return ""

    def _hydrate_book_resource_paths(self, row: dict) -> None:
        codigo = str(row.get("codigo") or "").strip()
        workspace_server = str(row.get("workspace_dir_server") or row.get("workspace_dir") or "").strip()
        pdf_server = str(row.get("pdf_path_server") or row.get("pdf_path") or "").strip()
        cover_server = str(row.get("cover_path_server") or row.get("cover_path") or "").strip()
        workspace_local = str(row.get("workspace_dir_local") or "").strip()
        pdf_local = str(row.get("pdf_path_local") or "").strip()
        cover_local = str(row.get("cover_path_local") or "").strip()
        workspace_mirror = self._resolve_local_mirror_book_dir(codigo)
        pdf_mirror = self._resolve_local_mirror_asset(codigo, pdf_server, "source.pdf")
        cover_mirror = self._resolve_local_mirror_asset(codigo, cover_server, "cover.png", "cover.jpg", "cover.jpeg", "cover.webp")
        row["workspace_dir_server"] = workspace_server
        row["pdf_path_server"] = pdf_server
        row["cover_path_server"] = cover_server
        row["workspace_dir_local"] = workspace_local
        row["pdf_path_local"] = pdf_local
        row["cover_path_local"] = cover_local
        row["workspace_dir_mirror"] = workspace_mirror
        row["pdf_path_mirror"] = pdf_mirror
        row["cover_path_mirror"] = cover_mirror
        row["workspace_dir"] = self._preferred_resource_path(workspace_local, workspace_mirror, workspace_server)
        row["pdf_path"] = self._preferred_resource_path(pdf_local, pdf_mirror, pdf_server)
        row["cover_path"] = self._preferred_resource_path(cover_local, cover_mirror, cover_server)

    def _path_exists(self, raw_path: str) -> bool:
        if not raw_path:
            return False
        try:
            return remap_legacy_drive_path(raw_path, prefer_existing=True).exists()
        except Exception:
            return False

    def _resource_status_label(self, raw_path: str, exists: bool) -> str:
        if not raw_path:
            return "-"
        return "OK" if exists else "Falta"

    def _normalize_path(self, path: Path) -> Path:
        return normalize_path(path)

    def _default_workspace_dir(self, *, codigo: str, titulo: str, pdf_path: str) -> Path:
        return resolve_workspace_root(codigo=codigo, titulo=titulo, pdf_path=pdf_path, workspace_dir="", cwd=Path.cwd())

    def _resolve_workspace_root(self, *, codigo: str, titulo: str, pdf_path: str, workspace_dir: str) -> Path:
        return resolve_workspace_root(
            codigo=codigo,
            titulo=titulo,
            pdf_path=pdf_path,
            workspace_dir=workspace_dir,
            cwd=Path.cwd(),
        )

    def _copy_pdf_into_workspace(self, pdf_path: str, workspace_dir: Path) -> str:
        raw_pdf = str(pdf_path or "").strip()
        if not raw_pdf:
            return ""
        try:
            source = Path(raw_pdf).expanduser()
        except Exception:
            return raw_pdf
        try:
            return str(self._normalize_path(source))
        except Exception:
            return str(source)

    def _normalize_resource_path_text(self, raw_path: str, *, prefer_existing: bool = True) -> str:
        clean = str(raw_path or "").strip()
        if not clean:
            return ""
        try:
            return str(remap_legacy_drive_path(clean, prefer_existing=prefer_existing))
        except Exception:
            return clean

    def _hydrate_instance_row_paths(self, row: Optional[dict]) -> Optional[dict]:
        if not isinstance(row, dict):
            return row
        hydrated = dict(row)
        for key in ("pdf_path", "session_path", "soluciones_dir"):
            if key in hydrated:
                hydrated[key] = self._normalize_resource_path_text(str(hydrated.get(key) or "").strip(), prefer_existing=True)
        return hydrated

    def _default_session_path_for_instance(self, workspace_dir: str, tipo: str) -> Path:
        root = Path(str(workspace_dir or "").strip()) if str(workspace_dir or "").strip() else self._default_workspace_dir(codigo="", titulo="", pdf_path="")
        return self._normalize_path(project_dirs(root, self._normalize_instance_type(tipo))["session_path"])

    def _default_solutions_dir_for_instance(self, workspace_dir: str, tipo: str) -> Path:
        root = Path(str(workspace_dir or "").strip()) if str(workspace_dir or "").strip() else self._default_workspace_dir(codigo="", titulo="", pdf_path="")
        return self._normalize_path(project_dirs(root, self._normalize_instance_type(tipo))["solutions_dir"])

    def _prepare_book_workspace(
        self,
        *,
        codigo: str,
        titulo: str,
        pdf_path: str,
        workspace_dir: str,
    ) -> tuple[str, str]:
        t0 = time.time()
        root = self._resolve_workspace_root(
            codigo=codigo,
            titulo=titulo,
            pdf_path=pdf_path,
            workspace_dir=workspace_dir,
        )
        LOGGER.info("prepare_workspace_start codigo=%s titulo=%s root=%s", codigo, titulo, str(root))
        try:
            ensure_project_dirs(root, instance_types=())
        except Exception as exc:
            LOGGER.exception("prepare_workspace_error root=%s err=%s", str(root), exc)
            raise ValueError(f"No se pudo preparar la carpeta del proyecto: {exc}") from exc
        normalized_pdf = self._copy_pdf_into_workspace(pdf_path, root)
        LOGGER.info("prepare_workspace_ok root=%s pdf=%s elapsed=%.3fs", str(root), normalized_pdf, time.time() - t0)
        return str(self._normalize_path(root)), normalized_pdf

    def _prepare_instance_workspace(self, workspace_dir: str, tipo: str) -> None:
        clean_type = self._normalize_instance_type(tipo)
        root = Path(str(workspace_dir or "").strip()) if str(workspace_dir or "").strip() else self._default_workspace_dir(codigo="", titulo="", pdf_path="")
        try:
            ensure_project_dirs(root, instance_types=(clean_type,))
        except Exception as exc:
            raise ValueError(f"No se pudo preparar la carpeta de la instancia '{clean_type}': {exc}") from exc

    def _read_transcriptor_session_payload(self, session_path: str) -> dict:
        path = self._normalize_resource_path_text(session_path, prefer_existing=True)
        if not path:
            return {"exists": False, "status": "No definida", "payload": {}, "items": []}
        session_file = Path(path)
        if not session_file.exists():
            return {"exists": False, "status": "Falta", "payload": {}, "items": []}
        try:
            try:
                payload = json.loads(session_file.read_text(encoding="utf-8"))
            except Exception:
                payload = json.loads(session_file.read_text(encoding="utf-8-sig"))
        except Exception:
            return {"exists": True, "status": "Invalida", "payload": {}, "items": []}
        items = payload.get("items", []) if isinstance(payload, dict) else []
        if (not isinstance(items, list) or not items) and isinstance(payload, dict):
            state_v3 = payload.get("state_v3", {})
            if isinstance(state_v3, dict):
                state_items = state_v3.get("items", [])
                if isinstance(state_items, list):
                    items = state_items
        if not isinstance(items, list):
            items = []
        return {"exists": True, "status": "OK", "payload": payload if isinstance(payload, dict) else {}, "items": items}

    def _extract_session_instance_stats(self, session_path: str) -> dict:
        info = self._read_transcriptor_session_payload(session_path)
        items = list(info.get("items", []) or [])
        payload = info.get("payload", {}) if isinstance(info.get("payload", {}), dict) else {}
        solution_paths_raw = payload.get("solution_paths_by_item", {}) if isinstance(payload, dict) else {}
        solution_paths_by_num: Dict[int, List[List[str]]] = {}
        if isinstance(solution_paths_raw, dict):
            for raw_key, raw_value in solution_paths_raw.items():
                try:
                    key = int(raw_key)
                except Exception:
                    continue
                if key <= 0:
                    continue
                if isinstance(raw_value, dict):
                    raw_groups = [raw_value]
                elif isinstance(raw_value, (list, tuple, set)):
                    raw_groups = list(raw_value)
                else:
                    raw_groups = [raw_value]
                contains_nested = any(isinstance(v, (list, tuple, set, dict)) for v in raw_groups)
                if not contains_nested:
                    raw_groups = [raw_groups]
                normalized_groups: List[List[str]] = []
                for raw_group in raw_groups:
                    if isinstance(raw_group, dict):
                        raw_group = raw_group.get("images") if "images" in raw_group else raw_group.get("paths")
                    if isinstance(raw_group, (list, tuple, set)):
                        iterable = list(raw_group)
                    else:
                        iterable = [raw_group]
                    deduped: List[str] = []
                    for raw_path in iterable:
                        clean_path = str(raw_path or "").strip()
                        if clean_path and clean_path not in deduped:
                            deduped.append(clean_path)
                    if deduped:
                        normalized_groups.append(deduped)
                if normalized_groups:
                    solution_paths_by_num[key] = normalized_groups
        numeros: Set[int] = set()
        con_clave = 0
        con_solucion = 0
        for raw in items:
            item_text = self._extract_session_item_text(raw)
            numero = self._extract_session_item_number(raw, item_text)
            if numero > 0:
                numeros.add(numero)
            if TAG_CLAVE_RE.search(item_text):
                con_clave += 1
            if (numero > 0 and solution_paths_by_num.get(numero)) or TAG_SOLUCION_RE.search(item_text):
                con_solucion += 1
        total = len(items)
        return {
            "exists": bool(info.get("exists", False)),
            "status": str(info.get("status") or "Invalida"),
            "items_count": total,
            "con_clave": con_clave,
            "con_solucion": con_solucion,
            "sin_clave": max(total - con_clave, 0),
            "sin_solucion": max(total - con_solucion, 0),
            "numeros": numeros,
        }

    def _extract_session_item_text(self, raw: object) -> str:
        if isinstance(raw, dict):
            for key in ("item_text", "item", "text", "latex", "enunciado_latex"):
                value = raw.get(key)
                if value is not None:
                    return str(value)
        return str(raw or "")

    def _extract_session_item_number(self, raw: object, item_text: str) -> int:
        if isinstance(raw, dict):
            for key in ("numero_original", "n", "numero"):
                value = raw.get(key)
                try:
                    number = int(value or 0)
                except Exception:
                    number = 0
                if number > 0:
                    return number
        return self._extract_item_number_from_latex(item_text)

    def _extract_item_number_from_latex(self, item_text: str) -> int:
        match = re.search(r"\\item\s*\[\s*\\textbf\s*\{\s*(\d+)\s*\.?\s*\}\s*\]", str(item_text or ""), re.IGNORECASE)
        if not match:
            return 0
        try:
            return int(match.group(1))
        except Exception:
            return 0

    def _query_uploaded_problem_stats(self, db_name: str, libro_codigo: str, instancia_tipo: str, numeros: Set[int]) -> dict:
        clean_code = str(libro_codigo or "").strip()
        clean_tipo = self._normalize_instance_type(instancia_tipo)
        if not clean_code or not clean_tipo:
            return {
                "subidos_bd": 0,
                "subidos_bd_con_solucion": 0,
                "subidos_bd_sin_solucion": 0,
                "subidos_bd_consistentes": 0,
                "subidos_bd_inconsistentes": 0,
                "subidos_bd_sin_revisar": 0,
            }
        conn = self.db.get_connection(db_name)
        try:
            if not self._pg_table_exists(conn, "problemas"):
                return {
                    "subidos_bd": 0,
                    "subidos_bd_con_solucion": 0,
                    "subidos_bd_sin_solucion": 0,
                    "subidos_bd_consistentes": 0,
                    "subidos_bd_inconsistentes": 0,
                    "subidos_bd_sin_revisar": 0,
                }
            problem_instance_col = self._problem_instance_column_name(conn)
            consistency_col = self._problem_consistency_column_name(conn)
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT
                    COUNT(*)::int AS total,
                    COUNT(*) FILTER (
                        WHERE jsonb_array_length(
                            CASE
                                WHEN jsonb_typeof(COALESCE(soluciones, '[]'::jsonb)) = 'array'
                                THEN COALESCE(soluciones, '[]'::jsonb)
                                ELSE '[]'::jsonb
                            END
                        ) > 0
                    )::int AS con_solucion,
                    COUNT(*) FILTER (
                        WHERE jsonb_array_length(
                            CASE
                                WHEN jsonb_typeof(COALESCE(soluciones, '[]'::jsonb)) = 'array'
                                THEN COALESCE(soluciones, '[]'::jsonb)
                                ELSE '[]'::jsonb
                            END
                        ) = 0
                    )::int AS sin_solucion,
                    COUNT(*) FILTER (
                        WHERE LOWER(COALESCE({consistency_col}, '')) IN ('consistente', 'bien planteado')
                    )::int AS consistentes,
                    COUNT(*) FILTER (
                        WHERE LOWER(COALESCE({consistency_col}, '')) IN ('inconsistente', 'mal planteado')
                    )::int AS inconsistentes,
                    COUNT(*) FILTER (
                        WHERE LOWER(COALESCE({consistency_col}, 'sin revisar')) IN ('sin revisar', 'pendiente revision', 'pendiente revisión')
                    )::int AS sin_revisar
                FROM problemas
                WHERE libro_codigo = %s
                  AND {problem_instance_col} = %s
                """,
                (clean_code, clean_tipo),
            )
            row = cur.fetchone() or (0, 0, 0, 0, 0, 0)
            return {
                "subidos_bd": int(row[0] or 0),
                "subidos_bd_con_solucion": int(row[1] or 0),
                "subidos_bd_sin_solucion": int(row[2] or 0),
                "subidos_bd_consistentes": int(row[3] or 0),
                "subidos_bd_inconsistentes": int(row[4] or 0),
                "subidos_bd_sin_revisar": int(row[5] or 0),
            }
        finally:
            conn.close()

    def _query_books_instance_health(self, conn) -> Dict[int, dict]:
        if not self._pg_table_exists(conn, "libro_instancias_escaneo") or not self._pg_table_exists(conn, "libros_escaneo"):
            return {}
        instance_col = self._instance_column_name(conn)
        if not self._pg_table_exists(conn, "problemas"):
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT
                    i.libro_id,
                    LOWER(COALESCE(i.{instance_col}, '')) AS tipo
                FROM libro_instancias_escaneo i
                ORDER BY i.libro_id ASC, LOWER(COALESCE(i.{instance_col}, '')) ASC
                """
            )
            by_book: Dict[int, dict] = {}
            for row in cur.fetchall() or []:
                libro_id = int(row[0] or 0)
                tipo = str(row[1] or "").strip()
                if not tipo:
                    continue
                payload = by_book.setdefault(
                    libro_id,
                    {"items": [], "consistentes_total": 0, "inconsistentes_total": 0, "sin_revisar_total": 0},
                )
                payload["items"].append(
                    {
                        "tipo": tipo,
                        "total": 0,
                        "sin_revisar": 0,
                        "inconsistentes": 0,
                        "consistentes": 0,
                        "status": "empty",
                    }
                )
            return by_book

        problem_instance_col = self._problem_instance_column_name(conn)
        consistency_col = self._problem_consistency_column_name(conn)
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT
                i.libro_id,
                LOWER(COALESCE(i.{instance_col}, '')) AS tipo,
                COUNT(p.id)::int AS total,
                COUNT(*) FILTER (
                    WHERE LOWER(COALESCE(p.{consistency_col}, 'sin revisar')) IN ('sin revisar', 'pendiente revision', 'pendiente revisión')
                )::int AS sin_revisar,
                COUNT(*) FILTER (
                    WHERE LOWER(COALESCE(p.{consistency_col}, '')) IN ('inconsistente', 'mal planteado')
                )::int AS inconsistentes,
                COUNT(*) FILTER (
                    WHERE LOWER(COALESCE(p.{consistency_col}, '')) IN ('consistente', 'bien planteado')
                )::int AS consistentes
            FROM libro_instancias_escaneo i
            JOIN libros_escaneo b ON b.id = i.libro_id
            LEFT JOIN problemas p
              ON p.libro_codigo = b.codigo
             AND LOWER(COALESCE(p.{problem_instance_col}, '')) = LOWER(COALESCE(i.{instance_col}, ''))
            GROUP BY i.libro_id, LOWER(COALESCE(i.{instance_col}, ''))
            ORDER BY i.libro_id ASC, LOWER(COALESCE(i.{instance_col}, '')) ASC
            """
        )
        by_book: Dict[int, dict] = {}
        for row in cur.fetchall() or []:
            libro_id = int(row[0] or 0)
            tipo = str(row[1] or "").strip()
            total = int(row[2] or 0)
            sin_revisar = int(row[3] or 0)
            inconsistentes = int(row[4] or 0)
            consistentes = int(row[5] or 0)
            if not tipo:
                continue
            if total <= 0:
                status = "empty"
            elif sin_revisar > 0:
                status = "in_progress"
            elif inconsistentes > 0:
                status = "complete_with_inconsistencies"
            else:
                status = "complete"
            payload = by_book.setdefault(
                libro_id,
                {"items": [], "consistentes_total": 0, "inconsistentes_total": 0, "sin_revisar_total": 0},
            )
            payload["items"].append(
                {
                    "tipo": tipo,
                    "total": total,
                    "sin_revisar": sin_revisar,
                    "inconsistentes": inconsistentes,
                    "consistentes": consistentes,
                    "status": status,
                }
            )
            payload["consistentes_total"] += consistentes
            payload["inconsistentes_total"] += inconsistentes
            payload["sin_revisar_total"] += sin_revisar
        return by_book

    def _build_instance_stats(self, db_name: str, *, libro_codigo: str, instancia: dict) -> BookInstanceSessionStats:
        tipo = self._normalize_instance_type(str(instancia.get("tipo") or ""))
        total_esperado = max(int(instancia.get("total_esperado") or 0), 0)
        session_path = self._normalize_resource_path_text(str(instancia.get("session_path") or "").strip(), prefer_existing=True)
        soluciones_dir = self._normalize_resource_path_text(str(instancia.get("soluciones_dir") or "").strip(), prefer_existing=True)
        session_info = self._extract_session_instance_stats(session_path)
        uploaded = self._query_uploaded_problem_stats(
            db_name,
            libro_codigo=libro_codigo,
            instancia_tipo=tipo,
            numeros=set(session_info.get("numeros", set()) or set()),
        )
        escaneados = max(int(session_info["items_count"]), 0)
        faltantes = max(total_esperado - escaneados, 0)
        porcentaje = 0.0 if total_esperado <= 0 else (escaneados / total_esperado)
        return BookInstanceSessionStats(
            instancia_id=int(instancia["id"]),
            tipo=tipo,
            total_esperado=total_esperado,
            escaneados_sesion=escaneados,
            con_clave_sesion=max(int(session_info["con_clave"]), 0),
            con_solucion_sesion=max(int(session_info["con_solucion"]), 0),
            sin_clave_sesion=max(int(session_info["sin_clave"]), 0),
            sin_solucion_sesion=max(int(session_info["sin_solucion"]), 0),
            pdf_path="",
            session_path=session_path,
            soluciones_dir=soluciones_dir,
            pdf_status="-",
            session_status=str(session_info["status"]),
            soluciones_status=self._resource_status_label(soluciones_dir, self._path_exists(soluciones_dir)),
            subidos_bd=max(int(uploaded["subidos_bd"]), 0),
            subidos_bd_con_solucion=max(int(uploaded["subidos_bd_con_solucion"]), 0),
            subidos_bd_sin_solucion=max(int(uploaded["subidos_bd_sin_solucion"]), 0),
            subidos_bd_consistentes=max(int(uploaded["subidos_bd_consistentes"]), 0),
            subidos_bd_inconsistentes=max(int(uploaded["subidos_bd_inconsistentes"]), 0),
            subidos_bd_sin_revisar=max(int(uploaded["subidos_bd_sin_revisar"]), 0),
            faltantes=faltantes,
            porcentaje=porcentaje,
        )


    def _touch_book(self, cur, libro_id: int) -> None:
        cur.execute("UPDATE libros_escaneo SET updated_at = NOW() WHERE id = %s", (int(libro_id),))

    def _pg_table_exists(self, conn, table_name: str) -> bool:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass(%s);", (f"public.{table_name}",))
        row = cur.fetchone()
        return bool(row and row[0])

    def _pg_column_exists(self, conn, table_name: str, column_name: str) -> bool:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s AND column_name = %s
            LIMIT 1
            """,
            (table_name, column_name),
        )
        return cur.fetchone() is not None

    def _is_schema_permission_error(self, exc: Exception) -> bool:
        pgcode = str(getattr(exc, "pgcode", "") or "")
        message = str(exc or "").lower()
        return pgcode == "42501" or "permission denied for schema public" in message

    def _schema_ready_for_runtime(self, conn) -> bool:
        required_tables = ("libros_escaneo", "libro_instancias_escaneo")
        for table in required_tables:
            if not self._pg_table_exists(conn, table):
                return False

        required_book_columns = (
            "id",
            "codigo",
            "titulo",
            "workspace_dir",
            "pdf_path",
            "cover_path",
            "estado",
        )
        for column in required_book_columns:
            if not self._pg_column_exists(conn, "libros_escaneo", column):
                return False

        required_instance_columns = (
            "id",
            "libro_id",
            "total_esperado",
            "session_path",
            "soluciones_dir",
            "activo",
            "notas",
        )
        for column in required_instance_columns:
            if not self._pg_column_exists(conn, "libro_instancias_escaneo", column):
                return False

        if not (
            self._pg_column_exists(conn, "libro_instancias_escaneo", "codigo_instancia")
            or self._pg_column_exists(conn, "libro_instancias_escaneo", "tipo")
        ):
            return False

        if self._pg_table_exists(conn, "problemas"):
            if not self._pg_column_exists(conn, "problemas", "libro_codigo"):
                return False
            if not (
                self._pg_column_exists(conn, "problemas", "codigo_instancia")
                or self._pg_column_exists(conn, "problemas", "instancia_tipo")
            ):
                return False

        return True

    def _instance_column_name(self, conn) -> str:
        if self._pg_column_exists(conn, "libro_instancias_escaneo", "codigo_instancia"):
            return "codigo_instancia"
        return "tipo"

    def _problem_instance_column_name(self, conn) -> str:
        if self._pg_column_exists(conn, "problemas", "codigo_instancia"):
            return "codigo_instancia"
        return "instancia_tipo"

    def _problem_consistency_column_name(self, conn) -> str:
        if self._pg_column_exists(conn, "problemas", "consistencia_matematica"):
            return "consistencia_matematica"
        if self._pg_column_exists(conn, "problemas", "estado_consistencia"):
            return "estado_consistencia"
        return "consistencia_matematica"

    def _fetchall_dicts(self, cur) -> List[dict]:
        columns = [desc[0] for desc in cur.description or []]
        return [dict(zip(columns, row)) for row in cur.fetchall()]

    def _fetchone_dict(self, cur) -> Optional[dict]:
        row = cur.fetchone()
        if row is None:
            return None
        columns = [desc[0] for desc in cur.description or []]
        return dict(zip(columns, row))
