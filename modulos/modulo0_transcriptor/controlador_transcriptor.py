from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from database.connection import DatabaseManager
from database.problem_origins import TAG_EXAMEN_RE, ensure_problem_origin_schema, upsert_exam_origin_for_problem


ITEM_NUM_RE = re.compile(r"\\item\s*\[\s*\\textbf\s*\{\s*(\d+)\s*\.?\s*\}\s*\]")
BRACKET_TAG_RE = re.compile(r"\[\[\s*([^\]]+?)\s*\]\]")
TAG_CURSO_RE = re.compile(r"\[\[\s*curso\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)
TAG_TEMA_RE = re.compile(r"\[\[\s*tema\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)
TAG_SUBTEMA_RE = re.compile(r"\[\[\s*subtema\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)
TAG_CLAVE_RE = re.compile(r"\[\[\s*clave\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)
TAG_ESTADO_RE = re.compile(r"\[\[\s*estado\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)
TAG_SOLUCION_RE = re.compile(r"\[\[\s*solucion(?:ario)?\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)
OPTION_LABEL_RE = re.compile(r"(?<![A-Za-z0-9])([A-F])\)", re.IGNORECASE)


@dataclass
class PersistableItem:
    archivo_origen: str
    item_latex: str
    imagenes: List[str] | None = None
    libro_codigo: str = ""
    instancia_tipo: str = ""
    tiene_clave: bool = False
    tiene_solucion: bool = False
    ruta_imagen_solucion: str = ""
    soluciones: List[List[str]] | None = None


@dataclass
class InsertResult:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    invalid: int = 0
    deleted: int = 0
    pending_deleted: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            "inserted": int(self.inserted),
            "updated": int(self.updated),
            "skipped": int(self.skipped),
            "invalid": int(self.invalid),
            "deleted": int(self.deleted),
            "pending_deleted": int(self.pending_deleted),
        }


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

    def fetch_taxonomy_catalog(self, db_name: str) -> Dict[str, List[Dict[str, str]]]:
        return self.obtener_catalogo_temas_subtemas(db_name)

    def _asegurar_tabla_problemas(self, conn) -> None:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS problemas (
                    id SERIAL PRIMARY KEY,
                    numero_original INT NOT NULL,
                    archivo_origen VARCHAR(255) NOT NULL,
                    enunciado_latex TEXT NOT NULL,
                    consistencia_matematica VARCHAR(30) NOT NULL DEFAULT 'Sin revisar'
                );
                """
            )
            cur.execute("ALTER TABLE problemas DROP COLUMN IF EXISTS ruta_carpeta;")
            cur.execute(
                "ALTER TABLE problemas ADD COLUMN IF NOT EXISTS fecha_creacion TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP;"
            )
            cur.execute("ALTER TABLE problemas ADD COLUMN IF NOT EXISTS imagenes TEXT[];")
            cur.execute("ALTER TABLE problemas ADD COLUMN IF NOT EXISTS curso VARCHAR(150) NOT NULL DEFAULT '';")
            cur.execute("ALTER TABLE problemas ADD COLUMN IF NOT EXISTS tema VARCHAR(150) NOT NULL DEFAULT '';")
            cur.execute("ALTER TABLE problemas ADD COLUMN IF NOT EXISTS subtema VARCHAR(150) NOT NULL DEFAULT '';")
            cur.execute(
                "ALTER TABLE problemas ADD COLUMN IF NOT EXISTS consistencia_matematica VARCHAR(30) NOT NULL DEFAULT 'Sin revisar';"
            )
            cur.execute("ALTER TABLE problemas ADD COLUMN IF NOT EXISTS respuesta_correcta VARCHAR(32) NOT NULL DEFAULT '';")
            cur.execute("ALTER TABLE problemas ADD COLUMN IF NOT EXISTS tipo_problema VARCHAR(20) NOT NULL DEFAULT 'opcion_multiple';")
            cur.execute("ALTER TABLE problemas ADD COLUMN IF NOT EXISTS nivel_dificultad VARCHAR(50);")
            cur.execute("ALTER TABLE problemas ADD COLUMN IF NOT EXISTS auditoria_razon TEXT;")
            cur.execute("ALTER TABLE problemas ADD COLUMN IF NOT EXISTS soluciones JSONB DEFAULT '[]'::jsonb;")
            cur.execute("ALTER TABLE problemas ADD COLUMN IF NOT EXISTS conceptos_ia JSONB DEFAULT '[]'::jsonb;")
            cur.execute("ALTER TABLE problemas ADD COLUMN IF NOT EXISTS libro_codigo VARCHAR(80) NOT NULL DEFAULT '';")
            cur.execute("ALTER TABLE problemas ADD COLUMN IF NOT EXISTS instancia_tipo VARCHAR(80) NOT NULL DEFAULT '';")
            ensure_problem_origin_schema(conn)
            if self._pg_column_exists(conn, "problemas", "instancia_tipo"):
                cur.execute("ALTER TABLE problemas ALTER COLUMN instancia_tipo TYPE VARCHAR(80);")
            if self._pg_column_exists(conn, "problemas", "codigo_instancia"):
                cur.execute("ALTER TABLE problemas ALTER COLUMN codigo_instancia TYPE VARCHAR(80);")
            cur.execute(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM pg_constraint
                        WHERE conname = 'unique_problema_origen'
                          AND conrelid = 'public.problemas'::regclass
                    ) THEN
                        ALTER TABLE public.problemas DROP CONSTRAINT unique_problema_origen;
                    END IF;
                END $$;
                """
            )
            instance_col = self._problem_instance_column_name(conn)
            if instance_col:
                cur.execute(
                    f"""
                    CREATE UNIQUE INDEX IF NOT EXISTS ux_problemas_contextual
                    ON problemas (libro_codigo, {instance_col}, numero_original)
                    WHERE libro_codigo <> '' AND {instance_col} <> '';
                    """
                )
                cur.execute(
                    f"""
                    CREATE UNIQUE INDEX IF NOT EXISTS ux_problemas_legacy
                    ON problemas (numero_original, archivo_origen)
                    WHERE libro_codigo = '' OR {instance_col} = '';
                    """
                )
            cur.execute("SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'vector');")
            if bool((cur.fetchone() or [False])[0]):
                cur.execute("ALTER TABLE problemas ADD COLUMN IF NOT EXISTS embedding VECTOR(1536);")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            if self._is_schema_permission_error(exc) and self._problemas_schema_ready(conn):
                return
            raise
        finally:
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

    def _pg_column_exists(self, conn, table_name: str, column_name: str) -> bool:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s AND column_name = %s
            LIMIT 1;
            """,
            (table_name, column_name),
        )
        row = cur.fetchone()
        cur.close()
        return row is not None

    def _is_schema_permission_error(self, exc: Exception) -> bool:
        pgcode = str(getattr(exc, "pgcode", "") or "")
        message = str(exc or "").lower()
        return pgcode == "42501" or "permission denied for schema public" in message

    def _problemas_schema_ready(self, conn) -> bool:
        if not self._pg_table_exists(conn, "problemas"):
            return False
        required_columns = (
            "numero_original",
            "archivo_origen",
            "enunciado_latex",
            "consistencia_matematica",
            "libro_codigo",
        )
        for column in required_columns:
            if not self._pg_column_exists(conn, "problemas", column):
                return False
        if not (
            self._pg_column_exists(conn, "problemas", "codigo_instancia")
            or self._pg_column_exists(conn, "problemas", "instancia_tipo")
        ):
            return False
        return True

    def _pg_table_exists(self, conn, table_name: str) -> bool:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass(%s);", (f"public.{table_name}",))
        row = cur.fetchone()
        cur.close()
        return bool(row and row[0])

    def _problem_instance_column_name(self, conn_or_cols) -> str | None:
        if isinstance(conn_or_cols, set):
            cols = conn_or_cols
            if "codigo_instancia" in cols:
                return "codigo_instancia"
            if "instancia_tipo" in cols:
                return "instancia_tipo"
            return None
        conn = conn_or_cols
        if self._pg_column_exists(conn, "problemas", "codigo_instancia"):
            return "codigo_instancia"
        if self._pg_column_exists(conn, "problemas", "instancia_tipo"):
            return "instancia_tipo"
        return None

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

    def _extract_item_storage_fields(self, item_latex: str) -> Dict[str, str]:
        raw = str(item_latex or "")

        def _first(pattern: re.Pattern[str]) -> str:
            match = pattern.search(raw)
            return str(match.group(1) if match else "").strip()

        estado_raw = _first(TAG_ESTADO_RE)
        estado_norm = "Sin revisar"
        normalized_state = str(estado_raw or "").strip().lower().replace(" ", "_")
        if normalized_state in {"consistente", "bien_planteado"}:
            estado_norm = "Consistente"
        elif normalized_state in {"inconsistente", "mal_planteado", "ambiguo", "ambigua"}:
            estado_norm = "Inconsistente"
        elif normalized_state in {"sin_revisar", "pendiente", "pendiente_revision"}:
            estado_norm = "Sin revisar"
        respuesta = _first(TAG_CLAVE_RE)
        option_labels = {m.group(1).upper() for m in OPTION_LABEL_RE.finditer(raw)}
        is_multiple = bool(respuesta) or ({"A", "B"}.issubset(option_labels))
        tipo = "opcion_multiple" if is_multiple else "abierto"

        clean = BRACKET_TAG_RE.sub(" ", raw)
        clean = re.sub(r"\s+", " ", clean).strip()
        return {
            "clean_item_latex": clean,
            "curso": _first(TAG_CURSO_RE),
            "tema": _first(TAG_TEMA_RE),
            "subtema": _first(TAG_SUBTEMA_RE),
            "respuesta_correcta": respuesta,
            "tipo_problema": tipo,
            "consistencia_matematica": estado_norm,
            "ruta_imagen_solucion": _first(TAG_SOLUCION_RE),
            "examen": _first(TAG_EXAMEN_RE),
        }

    def _normalize_soluciones_payload(self, raw_value: object) -> List[List[str]]:
        def _normalize_group(group_value: object) -> List[str]:
            clean_group: List[str] = []
            if isinstance(group_value, dict):
                group_value = group_value.get("images") if "images" in group_value else group_value.get("paths")
            if isinstance(group_value, (list, tuple, set)):
                iterable = list(group_value)
            else:
                iterable = [group_value]
            for raw in iterable:
                clean = str(raw or "").strip()
                if clean and clean not in clean_group:
                    clean_group.append(clean)
            return clean_group

        if raw_value is None:
            return []
        if isinstance(raw_value, dict):
            group = _normalize_group(raw_value)
            return [group] if group else []
        if not isinstance(raw_value, (list, tuple, set)):
            group = _normalize_group(raw_value)
            return [group] if group else []

        raw_list = list(raw_value)
        if not raw_list:
            return []
        contains_nested = any(isinstance(v, (list, tuple, set, dict)) for v in raw_list)
        if not contains_nested:
            group = _normalize_group(raw_list)
            return [group] if group else []

        normalized: List[List[str]] = []
        for raw_group in raw_list:
            clean_group = _normalize_group(raw_group)
            if clean_group:
                normalized.append(clean_group)
        return normalized

    def _has_contextual_identity(self, libro_codigo: str, instancia_tipo: str, cols: set[str]) -> bool:
        instance_col = self._problem_instance_column_name(cols)
        return (
            "libro_codigo" in cols
            and bool(instance_col)
            and bool(str(libro_codigo or "").strip())
            and bool(str(instancia_tipo or "").strip())
        )

    def _find_existing_problem_id(
        self,
        cur,
        *,
        numero: int,
        archivo_origen: str,
        libro_codigo: str,
        instancia_tipo: str,
        cols: set[str],
    ) -> Optional[int]:
        instance_col = self._problem_instance_column_name(cols)
        if self._has_contextual_identity(libro_codigo, instancia_tipo, cols):
            cur.execute(
                f"""
                SELECT id
                FROM problemas
                WHERE libro_codigo = %s
                  AND {instance_col} = %s
                  AND numero_original = %s
                LIMIT 1;
                """,
                (str(libro_codigo).strip(), str(instancia_tipo).strip(), int(numero)),
            )
        else:
            cur.execute(
                """
                SELECT id
                FROM problemas
                WHERE numero_original = %s
                  AND archivo_origen = %s
                LIMIT 1;
                """,
                (int(numero), str(archivo_origen)),
            )
        row = cur.fetchone()
        if not row:
            return None
        try:
            return int(row[0])
        except Exception:
            return None

    def _build_problem_update_locator(
        self,
        *,
        numero: int,
        archivo_origen: str,
        libro_codigo: str,
        instancia_tipo: str,
        cols: set[str],
    ) -> Tuple[str, List[object]]:
        instance_col = self._problem_instance_column_name(cols)
        if self._has_contextual_identity(libro_codigo, instancia_tipo, cols):
            return (
                f"libro_codigo = %s AND {instance_col} = %s AND numero_original = %s",
                [str(libro_codigo).strip(), str(instancia_tipo).strip(), int(numero)],
            )
        return (
            "numero_original = %s AND archivo_origen = %s",
            [int(numero), str(archivo_origen)],
        )

    def _delete_obsolete_contextual_rows(
        self,
        conn,
        cur,
        *,
        libro_codigo: str,
        instancia_tipo: str,
        keep_numbers: List[int],
        cols: set[str],
    ) -> Tuple[int, int]:
        if not self._has_contextual_identity(libro_codigo, instancia_tipo, cols):
            return (0, 0)

        normalized_keep = sorted({int(v) for v in keep_numbers if int(v) > 0})
        if not normalized_keep:
            return (0, 0)

        instance_col = self._problem_instance_column_name(cols)
        if not instance_col:
            return (0, 0)

        placeholders = ", ".join(["%s"] * len(normalized_keep))
        delete_params: List[object] = [str(libro_codigo).strip(), str(instancia_tipo).strip(), *normalized_keep]
        pending_deleted = 0

        if self._pg_table_exists(conn, "problema_pending_changes"):
            if (
                self._pg_column_exists(conn, "problema_pending_changes", "libro_codigo")
                and self._pg_column_exists(conn, "problema_pending_changes", "codigo_instancia")
                and self._pg_column_exists(conn, "problema_pending_changes", "numero_original")
            ):
                cur.execute(
                    f"""
                    DELETE FROM problema_pending_changes
                    WHERE libro_codigo = %s
                      AND codigo_instancia = %s
                      AND numero_original NOT IN ({placeholders})
                    """,
                    tuple(delete_params),
                )
            else:
                cur.execute(
                    f"""
                    DELETE FROM problema_pending_changes
                    WHERE problema_id IN (
                        SELECT id
                        FROM problemas
                        WHERE libro_codigo = %s
                          AND {instance_col} = %s
                          AND numero_original NOT IN ({placeholders})
                    )
                    """,
                    tuple(delete_params),
                )
            pending_deleted = max(int(cur.rowcount or 0), 0)

        cur.execute(
            f"""
            DELETE FROM problemas
            WHERE libro_codigo = %s
              AND {instance_col} = %s
              AND numero_original NOT IN ({placeholders})
            """,
            tuple(delete_params),
        )
        deleted = max(int(cur.rowcount or 0), 0)
        return (deleted, pending_deleted)

    def insertar_items(
        self,
        db_name: str,
        *,
        items: List[Tuple] | List[PersistableItem],
    ) -> Dict[str, int]:
        """
        items: lista de (archivo_origen, item_latex[, imagenes]).
        """

        conn = self.db.get_connection(db_name)
        try:
            self._asegurar_tabla_problemas(conn)
            cols = self._obtener_columnas_problemas(conn)

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
            if "consistencia_matematica" in cols:
                fields.append("consistencia_matematica")
                values.append("%s")
            include_libro_codigo = "libro_codigo" in cols
            instance_col = self._problem_instance_column_name(cols)
            include_instancia_tipo = bool(instance_col)
            include_curso = "curso" in cols
            include_tema = "tema" in cols
            include_subtema = "subtema" in cols
            include_respuesta_correcta = "respuesta_correcta" in cols
            include_tipo_problema = "tipo_problema" in cols
            include_soluciones = "soluciones" in cols
            if include_curso:
                fields.append("curso")
                values.append("%s")
            if include_tema:
                fields.append("tema")
                values.append("%s")
            if include_subtema:
                fields.append("subtema")
                values.append("%s")
            if include_respuesta_correcta:
                fields.append("respuesta_correcta")
                values.append("%s")
            if include_tipo_problema:
                fields.append("tipo_problema")
                values.append("%s")
            if include_soluciones:
                fields.append("soluciones")
                values.append("%s::jsonb")
            if include_libro_codigo:
                fields.append("libro_codigo")
                values.append("%s")
            if include_instancia_tipo:
                fields.append(instance_col)
                values.append("%s")

            sql_insert = f"INSERT INTO problemas ({', '.join(fields)}) VALUES ({', '.join(values)}) RETURNING id;"
            cur = conn.cursor()
            normalized_items: List[Tuple[str, str, List[str], str, str, List[List[str]]]] = []
            for entry in items:
                if isinstance(entry, PersistableItem):
                    normalized_items.append(
                        (
                            str(entry.archivo_origen),
                            str(entry.item_latex),
                            [str(v) for v in (entry.imagenes or [])],
                            str(entry.libro_codigo or "").strip(),
                            str(entry.instancia_tipo or "").strip(),
                            self._normalize_soluciones_payload(entry.soluciones or []),
                        )
                    )
                    continue
                archivo_origen = str(entry[0])
                item = str(entry[1])
                imagenes = entry[2] if len(entry) > 2 else []
                normalized_items.append((archivo_origen, item, list(imagenes or []), "", "", []))

            result = InsertResult()
            valid_numbers: List[int] = []
            for entry in normalized_items:
                archivo_origen = str(entry[0])
                item = str(entry[1])
                imagenes = entry[2] if len(entry) > 2 else []
                libro_codigo = str(entry[3] if len(entry) > 3 else "").strip()
                instancia_tipo = str(entry[4] if len(entry) > 4 else "").strip()
                soluciones = self._normalize_soluciones_payload(entry[5] if len(entry) > 5 else [])
                metadata = self._extract_item_storage_fields(item)
                item_norm = self.normalizar_item_una_linea(metadata["clean_item_latex"])
                numero = self.parsear_numero_original(item_norm)
                if not numero or not item_norm.startswith(r"\item"):
                    result.invalid += 1
                    continue
                valid_numbers.append(int(numero))
                if not soluciones:
                    legacy_solution = str(metadata["ruta_imagen_solucion"] or "").strip()
                    if legacy_solution:
                        soluciones = [[legacy_solution]]

                params: List[object] = [int(numero), str(archivo_origen), item_norm]
                if include_imagenes:
                    safe_imgs = [str(p).strip() for p in (imagenes or []) if str(p).strip()]
                    params.append(safe_imgs if safe_imgs else None)
                if "consistencia_matematica" in cols:
                    params.append(str(metadata["consistencia_matematica"] or "Sin revisar"))
                if include_curso:
                    params.append(str(metadata["curso"] or "").strip())
                if include_tema:
                    params.append(str(metadata["tema"] or "").strip())
                if include_subtema:
                    params.append(str(metadata["subtema"] or "").strip())
                if include_respuesta_correcta:
                    params.append(str(metadata["respuesta_correcta"] or "").strip())
                if include_tipo_problema:
                    params.append(str(metadata["tipo_problema"] or "opcion_multiple").strip() or "opcion_multiple")
                if include_soluciones:
                    params.append(json.dumps(soluciones if soluciones else []))
                if include_libro_codigo:
                    params.append(libro_codigo)
                if include_instancia_tipo:
                    params.append(instancia_tipo)
                existing_id = self._find_existing_problem_id(
                    cur,
                    numero=int(numero),
                    archivo_origen=str(archivo_origen),
                    libro_codigo=libro_codigo,
                    instancia_tipo=instancia_tipo,
                    cols=cols,
                )
                if existing_id is not None:
                    update_parts: List[str] = ["enunciado_latex = %s"]
                    update_params: List[object] = [item_norm]
                    if include_imagenes:
                        safe_imgs = [str(p).strip() for p in (imagenes or []) if str(p).strip()]
                        update_parts.append("imagenes = %s")
                        update_params.append(safe_imgs if safe_imgs else None)
                    if "consistencia_matematica" in cols:
                        update_parts.append("consistencia_matematica = %s")
                        update_params.append(str(metadata["consistencia_matematica"] or "Sin revisar"))
                    if include_curso:
                        update_parts.append("curso = %s")
                        update_params.append(str(metadata["curso"] or "").strip())
                    if include_tema:
                        update_parts.append("tema = %s")
                        update_params.append(str(metadata["tema"] or "").strip())
                    if include_subtema:
                        update_parts.append("subtema = %s")
                        update_params.append(str(metadata["subtema"] or "").strip())
                    if include_respuesta_correcta:
                        update_parts.append("respuesta_correcta = %s")
                        update_params.append(str(metadata["respuesta_correcta"] or "").strip())
                    if include_tipo_problema:
                        update_parts.append("tipo_problema = %s")
                        update_params.append(str(metadata["tipo_problema"] or "opcion_multiple").strip() or "opcion_multiple")
                    if include_soluciones:
                        update_parts.append("soluciones = %s::jsonb")
                        update_params.append(json.dumps(soluciones if soluciones else []))
                    if include_libro_codigo:
                        update_parts.append("libro_codigo = %s")
                        update_params.append(libro_codigo)
                    if include_instancia_tipo:
                        update_parts.append(f"{instance_col} = %s")
                        update_params.append(instancia_tipo)
                    where_sql, where_params = self._build_problem_update_locator(
                        numero=int(numero),
                        archivo_origen=str(archivo_origen),
                        libro_codigo=libro_codigo,
                        instancia_tipo=instancia_tipo,
                        cols=cols,
                    )
                    update_params.extend(where_params)
                    cur.execute(
                        f"UPDATE problemas SET {', '.join(update_parts)} WHERE {where_sql};",
                        tuple(update_params),
                    )
                    upsert_exam_origin_for_problem(
                        conn,
                        problem_id=int(existing_id),
                        exam_label=str(metadata.get("examen") or ""),
                        numero_original=int(numero),
                    )
                    result.updated += 1
                    continue

                cur.execute(sql_insert, tuple(params))
                row = cur.fetchone()
                if row:
                    upsert_exam_origin_for_problem(
                        conn,
                        problem_id=int(row[0]),
                        exam_label=str(metadata.get("examen") or ""),
                        numero_original=int(numero),
                    )
                    result.inserted += 1
                else:
                    result.skipped += 1

            if result.invalid == 0 and valid_numbers:
                scope_libro = str(normalized_items[0][3] if normalized_items and len(normalized_items[0]) > 3 else "").strip()
                scope_instancia = str(normalized_items[0][4] if normalized_items and len(normalized_items[0]) > 4 else "").strip()
                deleted, pending_deleted = self._delete_obsolete_contextual_rows(
                    conn,
                    cur,
                    libro_codigo=scope_libro,
                    instancia_tipo=scope_instancia,
                    keep_numbers=valid_numbers,
                    cols=cols,
                )
                result.deleted += deleted
                result.pending_deleted += pending_deleted

            conn.commit()
            cur.close()
            return result.to_dict()
        finally:
            conn.close()

    def insert_items(
        self,
        db_name: str,
        *,
        items: List[Tuple] | List[PersistableItem],
    ) -> Dict[str, int]:
        return self.insertar_items(db_name, items=items)

    def exportar_a_tex(self, *, items: List[str], out_path: Path) -> None:
        body = "\n".join(items).strip()
        if body:
            contenido = "\\begin{enumerate}\n" + body + "\n\\end{enumerate}\n"
        else:
            contenido = "\\begin{enumerate}\n\\end{enumerate}\n"
        out_path.write_text(contenido, encoding="utf-8")

    def obtener_reporte_subida(
        self,
        db_name: str,
        *,
        libro_codigo: str,
        instancia_tipo: str,
        numeros: List[int],
    ) -> Dict[str, int]:
        normalized = sorted({int(v) for v in numeros if int(v) > 0})
        if not libro_codigo.strip() or not instancia_tipo.strip() or not normalized:
            return {"subidos": 0, "con_solucion": 0, "sin_solucion": 0}
        conn = self.db.get_connection(db_name)
        try:
            self._asegurar_tabla_problemas(conn)
            instance_col = self._problem_instance_column_name(conn)
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
                    )::int AS sin_solucion
                FROM problemas
                WHERE libro_codigo = %s
                  AND {instance_col} = %s
                  AND numero_original = ANY(%s)
                """,
                (str(libro_codigo).strip(), str(instancia_tipo).strip(), normalized),
            )
            row = cur.fetchone() or (0, 0, 0)
            cur.close()
            return {
                "subidos": int(row[0] or 0),
                "con_solucion": int(row[1] or 0),
                "sin_solucion": int(row[2] or 0),
            }
        finally:
            conn.close()

    def obtener_estado_consistencia_por_numeros(
        self,
        db_name: str,
        *,
        libro_codigo: str,
        instancia_tipo: str,
        numeros: List[int],
    ) -> Dict[int, str]:
        normalized = sorted({int(v) for v in numeros if int(v) > 0})
        if not libro_codigo.strip() or not instancia_tipo.strip() or not normalized:
            return {}
        conn = self.db.get_connection(db_name)
        try:
            self._asegurar_tabla_problemas(conn)
            cols = self._problem_columns(conn)
            instance_col = self._problem_instance_column_name(cols)
            if "consistencia_matematica" in cols:
                consistency_expr = "COALESCE(consistencia_matematica, 'Sin revisar')"
            elif "estado_consistencia" in cols:
                consistency_expr = "COALESCE(estado_consistencia, 'Sin revisar')"
            else:
                consistency_expr = "'Sin revisar'"
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT numero_original, {consistency_expr} AS estado
                FROM problemas
                WHERE libro_codigo = %s
                  AND {instance_col} = %s
                  AND numero_original = ANY(%s)
                """,
                (str(libro_codigo).strip(), str(instancia_tipo).strip(), normalized),
            )
            out: Dict[int, str] = {}
            for numero, estado in cur.fetchall() or []:
                try:
                    n = int(numero or 0)
                except Exception:
                    continue
                if n <= 0:
                    continue
                raw = str(estado or "").strip().lower().replace(" ", "_")
                if raw in {"consistente", "bien_planteado"}:
                    out[n] = "consistente"
                elif raw in {"inconsistente", "mal_planteado", "ambiguo", "ambigua"}:
                    out[n] = "inconsistente"
                else:
                    out[n] = "sin_revisar"
            cur.close()
            return out
        finally:
            conn.close()
