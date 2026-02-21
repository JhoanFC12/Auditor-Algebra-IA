from __future__ import annotations

import unicodedata
from typing import Dict, List, Optional, Tuple

from openai import OpenAI

from database.connection import DatabaseManager


def _vec_to_sql(emb: List[float]) -> str:
    return "[" + ",".join(f"{float(x):.8f}" for x in emb) + "]"


def _where_estado(estado_filtro: str) -> tuple[str, List[object]]:
    estado = (estado_filtro or "").strip()
    if not estado or estado == "Todos":
        return "", []
    if estado == "Pendiente Revision":
        return "(p.estado_consistencia IS NULL OR p.estado_consistencia = 'Pendiente Revision')", []
    if estado in {"Bien Planteado", "Mal Planteado"}:
        return "p.estado_consistencia = %s", [estado]
    return "", []


class ConsultaController:
    def __init__(self):
        self.db = DatabaseManager()
        self.sim_area_threshold = 0.8

    def _norm_text(self, value: str) -> str:
        raw = (value or "").strip().lower()
        if not raw:
            return ""
        raw = unicodedata.normalize("NFKD", raw)
        raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
        raw = " ".join(raw.split())
        return raw.strip()

    def listar_bases_datos(self) -> List[str]:
        return self.db.listar_bases_datos()

    def inicializar_vector(self, db_name: str, *, dim: int = 1536) -> None:
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

    def _ensure_fuzzy_extensions(self, cur) -> None:
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS unaccent;")
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
        except Exception:
            return
        try:
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_temas_area_norm_trgm
                    ON temas USING GIN (COALESCE(area_norm, '') gin_trgm_ops);
                """
            )
        except Exception:
            return

    def _resolver_area_fuzzy(self, cur, area: str) -> str:
        area = (area or "").strip()
        if not area:
            return ""
        try:
            self._ensure_fuzzy_extensions(cur)
            area_norm = self._norm_text(area)
            cur.execute(
                """
                SELECT COALESCE(area,''), similarity(COALESCE(area_norm,''), %s) AS sim
                FROM temas
                ORDER BY sim DESC
                LIMIT 1;
                """,
                (area_norm,),
            )
            row = cur.fetchone()
            if row:
                best_area = (row[0] or "").strip()
                sim = float(row[1] or 0)
                if best_area and sim >= self.sim_area_threshold:
                    return best_area
        except Exception:
            return area
        return area

    def listar_archivos_origen(self, db_name: str, *, limit: int = 500) -> List[Tuple[str, int]]:
        self.inicializar_vector(db_name)
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT COALESCE(archivo_origen,''), COUNT(*)::int
                FROM problemas
                GROUP BY COALESCE(archivo_origen,'')
                ORDER BY COUNT(*) DESC, COALESCE(archivo_origen,'') ASC
                LIMIT %s;
                """,
                (int(limit),),
            )
            rows = cur.fetchall()
            return [(r[0], int(r[1])) for r in rows if (r[0] or "").strip()]
        finally:
            conn.close()

    def listar_areas(self, db_name: str, *, limit: int = 200) -> List[str]:
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT COALESCE(area,'') FROM temas ORDER BY COALESCE(area,'') ASC LIMIT %s;", (int(limit),))
            vals = [r[0] for r in cur.fetchall()]
            return [v for v in vals if (v or "").strip()]
        finally:
            conn.close()

    def buscar_similares(
        self,
        db_name: str,
        *,
        query_text: str,
        top_k: int = 10,
        archivo_origen: str = "",
        estado_filtro: str = "Todos",
        area_filtro: str = "",
        model: str = "text-embedding-3-small",
    ) -> List[Dict[str, object]]:
        query_text = (query_text or "").strip()
        if not query_text:
            return []

        self.inicializar_vector(db_name)
        client = OpenAI()
        emb = client.embeddings.create(model=model, input=query_text).data[0].embedding
        vec = _vec_to_sql(emb)

        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            clauses: List[str] = ["p.embedding IS NOT NULL"]
            params: List[object] = []

            where_expr, where_params = _where_estado(estado_filtro)
            if where_expr:
                clauses.append(where_expr)
                params.extend(where_params)

            if archivo_origen:
                clauses.append("p.archivo_origen = %s")
                params.append(archivo_origen)

            area = (area_filtro or "").strip()
            if area and area != "Todos":
                area_resolved = self._resolver_area_fuzzy(cur, area)
                clauses.append("COALESCE(t.area,'') = %s")
                params.append(area_resolved)

            where_sql = "WHERE " + " AND ".join([f"({c})" for c in clauses])
            cur.execute(
                f"""
                SELECT
                    p.id,
                    COALESCE(p.archivo_origen,''),
                    COALESCE(p.estado_consistencia,''),
                    p.tema_id,
                    COALESCE(t.nombre,''),
                    COALESCE(t.area,''),
                    LEFT(p.enunciado_latex, 240),
                    (p.embedding <-> %s::vector) AS distance
                FROM problemas p
                LEFT JOIN temas t ON t.id = p.tema_id
                {where_sql}
                ORDER BY p.embedding <-> %s::vector
                LIMIT %s;
                """,
                (vec, *params, vec, int(top_k)),
            )
            rows = cur.fetchall()
            out: List[Dict[str, object]] = []
            for pid, archivo, estado, tema_id, tema_nombre, area, snippet, dist in rows:
                out.append(
                    {
                        "id": int(pid),
                        "archivo_origen": archivo,
                        "estado_consistencia": estado,
                        "tema_id": tema_id,
                        "tema": tema_nombre,
                        "area": area,
                        "snippet": snippet or "",
                        "distance": float(dist) if dist is not None else None,
                    }
                )
            return out
        finally:
            conn.close()

    def obtener_detalle_problema(self, db_name: str, *, problema_id: int) -> Dict[str, object]:
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    p.id, p.enunciado_latex, COALESCE(p.archivo_origen,''), COALESCE(p.estado_consistencia,''),
                    p.tema_id, COALESCE(t.nombre,''), COALESCE(t.area,''),
                    COALESCE(p.respuesta_correcta,''), p.nivel_dificultad, COALESCE(p.razon_inconsistencia,'')
                FROM problemas p
                LEFT JOIN temas t ON t.id = p.tema_id
                WHERE p.id=%s;
                """,
                (int(problema_id),),
            )
            row = cur.fetchone()
            if not row:
                return {}

            data = {
                "id": int(row[0]),
                "enunciado_latex": row[1] or "",
                "archivo_origen": row[2] or "",
                "estado_consistencia": row[3] or "",
                "tema_id": row[4],
                "tema": row[5] or "",
                "area": row[6] or "",
                "respuesta_correcta": row[7] or "",
                "nivel_dificultad": row[8],
                "razon_inconsistencia": row[9] or "",
                "soluciones": [],
            }

            # Soluciones normalizadas
            cur.execute(
                """
                SELECT id, orden, metodo_nombre, solucion_latex, COALESCE(autor_ia,'')
                FROM soluciones
                WHERE problema_id=%s
                ORDER BY orden ASC, id ASC;
                """,
                (int(problema_id),),
            )
            sols = cur.fetchall()
            for sid, orden, metodo, latex, autor in sols:
                cur.execute(
                    """
                    SELECT sp.proposicion_id, COALESCE(p.nombre,'')
                    FROM solucion_proposiciones sp
                    LEFT JOIN proposiciones_matematicas p ON p.id = sp.proposicion_id
                    WHERE sp.solucion_id=%s
                    ORDER BY sp.proposicion_id ASC;
                    """,
                    (int(sid),),
                )
                props = [(int(r[0]), r[1] or "") for r in cur.fetchall()]
                data["soluciones"].append(
                    {
                        "id": int(sid),
                        "orden": int(orden),
                        "metodo_nombre": metodo or "",
                        "solucion_latex": latex or "",
                        "autor_ia": autor or "",
                        "propiedades": props,
                    }
                )
            return data
        finally:
            conn.close()
