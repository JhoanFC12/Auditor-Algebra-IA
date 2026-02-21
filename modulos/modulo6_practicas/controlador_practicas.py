from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from database.connection import DatabaseManager

try:
    from docx import Document
    from docx.shared import RGBColor
except Exception:  # pragma: no cover - optional dependency handled at runtime
    Document = None  # type: ignore[assignment]
    RGBColor = None  # type: ignore[assignment]


TAG_META_RE = re.compile(r"\[\[\s*(?:curso|tema|subtema|clave)\s*=\s*.*?\s*\]\]", re.IGNORECASE)


class PracticeBuilderController:
    def __init__(self):
        self.db = DatabaseManager()

    def listar_bases_datos(self) -> List[str]:
        return self.db.listar_bases_datos()

    def listar_cursos(self, db_name: str) -> List[str]:
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT DISTINCT COALESCE(area,'')
                FROM temas
                WHERE COALESCE(area,'') <> ''
                ORDER BY COALESCE(area,'') ASC;
                """
            )
            return [str(r[0]) for r in cur.fetchall() if str(r[0] or "").strip()]
        finally:
            conn.close()

    def listar_temas(self, db_name: str, *, curso: str = "") -> List[Dict[str, object]]:
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            if (curso or "").strip():
                cur.execute(
                    """
                    SELECT id, COALESCE(nombre,''), COALESCE(area,'')
                    FROM temas
                    WHERE COALESCE(area,'') = %s
                    ORDER BY COALESCE(nombre,'') ASC;
                    """,
                    ((curso or "").strip(),),
                )
            else:
                cur.execute(
                    """
                    SELECT id, COALESCE(nombre,''), COALESCE(area,'')
                    FROM temas
                    ORDER BY COALESCE(area,''), COALESCE(nombre,'') ASC;
                    """
                )
            rows = cur.fetchall()
            return [{"id": int(r[0]), "nombre": r[1], "curso": r[2]} for r in rows]
        finally:
            conn.close()

    def listar_subtemas(self, db_name: str, *, tema_id: Optional[int]) -> List[Dict[str, object]]:
        if not tema_id:
            return []
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, COALESCE(nombre,'')
                FROM subtemas
                WHERE tema_id = %s
                ORDER BY COALESCE(nombre,'') ASC;
                """,
                (int(tema_id),),
            )
            rows = cur.fetchall()
            return [{"id": int(r[0]), "nombre": r[1]} for r in rows]
        finally:
            conn.close()

    def contar_problemas(
        self,
        db_name: str,
        *,
        curso: str = "",
        tema_id: Optional[int] = None,
        subtema_id: Optional[int] = None,
        estado: str = "Todos",
    ) -> int:
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            where, params = self._build_filters(curso=curso, tema_id=tema_id, subtema_id=subtema_id, estado=estado)
            cur.execute(f"SELECT COUNT(*)::int FROM problemas p LEFT JOIN temas t ON t.id = p.tema_id {where};", params)
            row = cur.fetchone()
            return int(row[0] or 0) if row else 0
        finally:
            conn.close()

    def obtener_problemas(
        self,
        db_name: str,
        *,
        cantidad: int,
        curso: str = "",
        tema_id: Optional[int] = None,
        subtema_id: Optional[int] = None,
        estado: str = "Todos",
        aleatorio: bool = True,
    ) -> List[Dict[str, object]]:
        cantidad = max(int(cantidad or 0), 1)
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            where, params = self._build_filters(curso=curso, tema_id=tema_id, subtema_id=subtema_id, estado=estado)
            order_sql = "ORDER BY random()" if aleatorio else "ORDER BY p.id ASC"
            cur.execute(
                f"""
                SELECT
                    p.id,
                    p.numero_original,
                    COALESCE(p.enunciado_latex,''),
                    COALESCE(p.respuesta_correcta,''),
                    COALESCE(t.area,''),
                    COALESCE(t.nombre,''),
                    COALESCE(s.nombre,'')
                FROM problemas p
                LEFT JOIN temas t ON t.id = p.tema_id
                LEFT JOIN subtemas s ON s.id = p.subtema_id
                {where}
                {order_sql}
                LIMIT %s;
                """,
                (*params, cantidad),
            )
            rows = cur.fetchall()
            return [
                {
                    "id": int(r[0]),
                    "numero_original": int(r[1] or 0),
                    "enunciado_latex": str(r[2] or ""),
                    "respuesta_correcta": str(r[3] or ""),
                    "curso": str(r[4] or ""),
                    "tema": str(r[5] or ""),
                    "subtema": str(r[6] or ""),
                }
                for r in rows
            ]
        finally:
            conn.close()

    def exportar_practica_word(
        self,
        *,
        output_path: Path,
        titulo: str,
        problemas: List[Dict[str, object]],
        incluir_clave_inline: bool = False,
        incluir_clave_final: bool = True,
    ) -> Path:
        if Document is None or RGBColor is None:
            raise RuntimeError("Falta dependencia python-docx. Instala requirements.txt en tu entorno activo.")

        doc = Document()
        doc.add_heading(titulo.strip() or "Practica", level=1)
        doc.add_paragraph(f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        doc.add_paragraph(f"Total de problemas: {len(problemas)}")
        doc.add_paragraph("")

        for idx, item in enumerate(problemas, start=1):
            enunciado = self._limpiar_enunciado_para_word(str(item.get("enunciado_latex") or ""))
            p = doc.add_paragraph()
            p.add_run(f"{idx}. ")
            p.add_run(enunciado)
            clave = (item.get("respuesta_correcta") or "").strip().upper()
            if incluir_clave_inline and clave:
                run = p.add_run(f"   [Clave: {clave}]")
                run.font.color.rgb = RGBColor(220, 38, 38)

        if incluir_clave_final:
            doc.add_page_break()
            doc.add_heading("Clave de respuestas", level=2)
            for idx, item in enumerate(problemas, start=1):
                clave = (item.get("respuesta_correcta") or "").strip().upper()
                if not clave:
                    continue
                line = doc.add_paragraph()
                line.add_run(f"{idx}) ")
                run = line.add_run(clave)
                run.font.color.rgb = RGBColor(220, 38, 38)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path))
        return output_path

    def _build_filters(
        self,
        *,
        curso: str,
        tema_id: Optional[int],
        subtema_id: Optional[int],
        estado: str,
    ) -> tuple[str, List[object]]:
        clauses: List[str] = []
        params: List[object] = []
        if (curso or "").strip():
            clauses.append("COALESCE(t.area,'') = %s")
            params.append((curso or "").strip())
        if tema_id:
            clauses.append("p.tema_id = %s")
            params.append(int(tema_id))
        if subtema_id:
            clauses.append("p.subtema_id = %s")
            params.append(int(subtema_id))
        estado = (estado or "").strip()
        if estado == "Pendiente Revision":
            clauses.append("(p.estado_consistencia IS NULL OR p.estado_consistencia = 'Pendiente Revision')")
        elif estado in {"Bien Planteado", "Mal Planteado"}:
            clauses.append("p.estado_consistencia = %s")
            params.append(estado)

        if not clauses:
            return "", params
        return "WHERE " + " AND ".join(f"({c})" for c in clauses), params

    def _limpiar_enunciado_para_word(self, enunciado: str) -> str:
        text = TAG_META_RE.sub(" ", enunciado or "")
        text = text.replace("£", "\n")
        text = text.replace("æ", "\n")
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
