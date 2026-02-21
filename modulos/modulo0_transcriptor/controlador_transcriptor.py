from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from database.connection import DatabaseManager


ITEM_NUM_RE = re.compile(r"\\item\s*\[\s*\\textbf\s*\{\s*(\d+)\s*\.?\s*\}\s*\]")


class TranscriptorController:
    def __init__(self):
        self.db = DatabaseManager()

    def listar_bases_datos(self) -> List[str]:
        return self.db.listar_bases_datos()

    def obtener_catalogo_temas_subtemas(self, db_name: str) -> Dict[str, List[Dict[str, str]]]:
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            cur.execute("SELECT to_regclass('public.temas') IS NOT NULL, to_regclass('public.subtemas') IS NOT NULL;")
            row = cur.fetchone() or (False, False)
            has_temas = bool(row[0])
            has_subtemas = bool(row[1])
            if not has_temas:
                cur.close()
                return {"areas": [], "temas": [], "subtemas": []}

            if has_subtemas:
                cur.execute(
                    """
                    SELECT
                        COALESCE(t.area, '') AS area,
                        COALESCE(t.nombre, '') AS tema,
                        COALESCE(s.nombre, '') AS subtema
                    FROM temas t
                    LEFT JOIN subtemas s ON s.tema_id = t.id
                    ORDER BY COALESCE(t.area, ''), COALESCE(t.nombre, ''), COALESCE(s.nombre, '');
                    """
                )
            else:
                cur.execute(
                    """
                    SELECT
                        COALESCE(t.area, '') AS area,
                        COALESCE(t.nombre, '') AS tema,
                        '' AS subtema
                    FROM temas t
                    ORDER BY COALESCE(t.area, ''), COALESCE(t.nombre, '');
                    """
                )
            rows = cur.fetchall()
            cur.close()

            areas_seen: set[str] = set()
            temas_seen: set[Tuple[str, str]] = set()
            subtemas_seen: set[Tuple[str, str, str]] = set()
            areas: List[Dict[str, str]] = []
            temas: List[Dict[str, str]] = []
            subtemas: List[Dict[str, str]] = []

            for area, tema, subtema in rows:
                area_val = (area or "").strip()
                tema_val = (tema or "").strip()
                subtema_val = (subtema or "").strip()

                if area_val and area_val not in areas_seen:
                    areas_seen.add(area_val)
                    areas.append({"curso": area_val})

                if tema_val:
                    tema_key = (area_val, tema_val)
                    if tema_key not in temas_seen:
                        temas_seen.add(tema_key)
                        temas.append({"curso": area_val, "tema": tema_val})

                if tema_val and subtema_val:
                    sub_key = (area_val, tema_val, subtema_val)
                    if sub_key not in subtemas_seen:
                        subtemas_seen.add(sub_key)
                        subtemas.append({"curso": area_val, "tema": tema_val, "subtema": subtema_val})

            return {"areas": areas, "temas": temas, "subtemas": subtemas}
        finally:
            conn.close()

    def _asegurar_tabla_problemas(self, conn) -> None:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS problemas (
                id SERIAL PRIMARY KEY,
                numero_original INT NOT NULL,
                archivo_origen VARCHAR(255) NOT NULL,
                ruta_carpeta TEXT,
                enunciado_latex TEXT NOT NULL,
                estado_consistencia VARCHAR(50) DEFAULT 'Pendiente Revision',
                CONSTRAINT unique_problema_origen UNIQUE (numero_original, archivo_origen)
            );
            """
        )
        cur.execute("ALTER TABLE problemas ADD COLUMN IF NOT EXISTS imagenes TEXT[];")
        conn.commit()
        cur.close()

    def _obtener_columnas_problemas(self, conn) -> set[str]:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'problemas';
            """
        )
        cols = {r[0] for r in cur.fetchall()}
        cur.close()
        return cols

    def parsear_numero_original(self, item_latex: str) -> Optional[int]:
        m = ITEM_NUM_RE.search(item_latex or "")
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None

    def normalizar_item_una_linea(self, item_latex: str) -> str:
        txt = (item_latex or "").replace("\r\n", "\n").replace("\r", "\n")
        txt = " ".join([x.strip() for x in txt.split("\n") if x.strip()])
        txt = re.sub(r"\s+", " ", txt).strip()
        return txt

    def insertar_items(
        self,
        db_name: str,
        *,
        items: List[Tuple],
    ) -> Dict[str, int]:
        """
        items: lista de (archivo_origen, item_latex[, imagenes]).
        """

        conn = self.db.get_connection(db_name)
        try:
            self._asegurar_tabla_problemas(conn)
            cols = self._obtener_columnas_problemas(conn)
            inserted = 0
            skipped = 0
            invalid = 0

            has_num = "numero_original" in cols
            has_arch = "archivo_origen" in cols
            if not (has_num and has_arch):
                raise Exception("La tabla problemas no tiene numero_original/archivo_origen.")

            fields = ["numero_original", "archivo_origen", "enunciado_latex"]
            values = ["%s", "%s", "%s"]
            include_imagenes = "imagenes" in cols
            if include_imagenes:
                fields.append("imagenes")
                values.append("%s")
            if "ruta_carpeta" in cols:
                fields.append("ruta_carpeta")
                values.append("%s")
            if "estado_consistencia" in cols:
                fields.append("estado_consistencia")
                values.append("%s")

            sql = f"INSERT INTO problemas ({', '.join(fields)}) VALUES ({', '.join(values)}) ON CONFLICT DO NOTHING RETURNING id;"
            cur = conn.cursor()
            for entry in items:
                archivo_origen = str(entry[0])
                item = str(entry[1])
                imagenes = entry[2] if len(entry) > 2 else []
                item_norm = self.normalizar_item_una_linea(item)
                numero = self.parsear_numero_original(item_norm)
                if not numero or not item_norm.startswith(r"\item"):
                    invalid += 1
                    continue

                params: List[object] = [int(numero), str(archivo_origen), item_norm]
                if include_imagenes:
                    safe_imgs = [str(p).strip() for p in (imagenes or []) if str(p).strip()]
                    params.append(safe_imgs if safe_imgs else None)
                if "ruta_carpeta" in cols:
                    params.append(None)
                if "estado_consistencia" in cols:
                    params.append("Pendiente Revision")
                cur.execute(sql, tuple(params))
                row = cur.fetchone()
                if row:
                    inserted += 1
                else:
                    skipped += 1

            conn.commit()
            cur.close()
            return {"inserted": inserted, "skipped": skipped, "invalid": invalid}
        finally:
            conn.close()

    def exportar_a_tex(self, *, items: List[str], out_path: Path) -> None:
        body = "\n".join(items).strip()
        if body:
            contenido = "\\begin{enumerate}\n" + body + "\n\\end{enumerate}\n"
        else:
            contenido = "\\begin{enumerate}\n\\end{enumerate}\n"
        out_path.write_text(contenido, encoding="utf-8")
