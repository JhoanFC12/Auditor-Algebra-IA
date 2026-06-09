from __future__ import annotations

from typing import List, Optional, Tuple

from database.connection import DatabaseManager


def _where_estado(estado_filtro: str) -> tuple[str, List[object]]:
    estado = (estado_filtro or "").strip()
    if not estado or estado == "Todos":
        return "", []
    if estado in {"Pendiente Revision", "Sin revisar"}:
        return "(consistencia_matematica IS NULL OR consistencia_matematica = 'Sin revisar')", []
    if estado in {"Bien Planteado", "Consistente"}:
        return "consistencia_matematica = %s", ["Consistente"]
    if estado in {"Mal Planteado", "Inconsistente"}:
        return "consistencia_matematica = %s", ["Inconsistente"]
    return "", []


class EmbeddingController:
    def __init__(self):
        self.db = DatabaseManager()

    def listar_bases_datos(self) -> List[str]:
        return self.db.listar_bases_datos()

    def inicializar_embedding(self, db_name: str, *, dim: int = 1536) -> None:
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            except Exception:
                conn.rollback()
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name='problemas' AND column_name='embedding';
                """
            )
            if not cur.fetchone():
                cur.execute(f"ALTER TABLE problemas ADD COLUMN embedding VECTOR({int(dim)});")
            conn.commit()
        finally:
            conn.close()

    def listar_archivos_origen(
        self, db_name: str, *, estado_filtro: str = "Todos", solo_sin_embedding: bool = False, limit: int = 500
    ) -> List[Tuple[str, int]]:
        self.inicializar_embedding(db_name)
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            clauses: List[str] = []
            params: List[object] = []
            where_expr, where_params = _where_estado(estado_filtro)
            if where_expr:
                clauses.append(where_expr)
                params.extend(where_params)
            if solo_sin_embedding:
                clauses.append("embedding IS NULL")
            where_sql = f"WHERE {' AND '.join([f'({c})' for c in clauses])}" if clauses else ""
            cur.execute(
                f"""
                SELECT COALESCE(archivo_origen,''), COUNT(*)::int
                FROM problemas
                {where_sql}
                GROUP BY COALESCE(archivo_origen,'')
                ORDER BY COUNT(*) DESC, COALESCE(archivo_origen,'') ASC
                LIMIT %s;
                """,
                (*params, int(limit)),
            )
            rows = cur.fetchall()
            return [(r[0], int(r[1])) for r in rows if (r[0] or "").strip()]
        finally:
            conn.close()

    def obtener_problemas_para_embedding(
        self,
        db_name: str,
        *,
        limit: int,
        archivo_origen: str = "",
        estado_filtro: str = "Todos",
        solo_sin_embedding: bool = True,
    ) -> List[Tuple[int, str]]:
        self.inicializar_embedding(db_name)
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            clauses: List[str] = []
            params: List[object] = []
            where_expr, where_params = _where_estado(estado_filtro)
            if where_expr:
                clauses.append(where_expr)
                params.extend(where_params)
            if solo_sin_embedding:
                clauses.append("embedding IS NULL")
            if archivo_origen:
                clauses.append("archivo_origen = %s")
                params.append(archivo_origen)
            where_sql = f"WHERE {' AND '.join([f'({c})' for c in clauses])}" if clauses else ""
            cur.execute(
                f"""
                SELECT id, enunciado_latex
                FROM problemas
                {where_sql}
                ORDER BY id ASC
                LIMIT %s;
                """,
                (*params, int(limit)),
            )
            return [(int(r[0]), r[1] or "") for r in cur.fetchall()]
        finally:
            conn.close()

    def guardar_embeddings(self, db_name: str, *, items: List[Tuple[int, List[float]]]) -> None:
        if not items:
            return
        self.inicializar_embedding(db_name)
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            for pid, emb in items:
                vec = "[" + ",".join(f"{float(x):.8f}" for x in emb) + "]"
                cur.execute("UPDATE problemas SET embedding=%s::vector WHERE id=%s;", (vec, int(pid)))
            conn.commit()
        finally:
            conn.close()

