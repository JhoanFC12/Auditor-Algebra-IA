import re
import unicodedata
from typing import Dict, List, Optional, Tuple

from database.connection import DatabaseManager


class TheoryController:
    def __init__(self):
        self.db = DatabaseManager()
        self.sim_tema_threshold = 0.8
        self.sim_area_threshold = 0.5

    def _norm_text(self, value: str) -> str:
        raw = (value or "").strip().lower()
        if not raw:
            return ""
        raw = unicodedata.normalize("NFKD", raw)
        raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
        raw = re.sub(r"\s+", " ", raw)
        return raw.strip()

    def listar_bases_datos(self) -> List[str]:
        return self.db.listar_bases_datos()

    def _table_exists(self, cur, table_name: str) -> bool:
        cur.execute("SELECT to_regclass(%s);", (f"public.{table_name}",))
        return cur.fetchone()[0] is not None

    def _ensure_column(self, cur, table: str, column: str, ddl_type: str) -> None:
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name=%s AND column_name=%s;",
            (table, column),
        )
        if not cur.fetchone():
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type};")

    def inicializar_tablas_si_no_existen(self, db_name: str) -> bool:
        try:
            conn = self.db.get_connection(db_name)
            cur = conn.cursor()

            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS unaccent;")
                cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
            except Exception:
                pass

            cur.execute(
                "CREATE TABLE IF NOT EXISTS temas (id SERIAL PRIMARY KEY, nombre TEXT NOT NULL, area TEXT, nombre_norm TEXT, area_norm TEXT);"
            )
            cur.execute("ALTER TABLE temas ADD COLUMN IF NOT EXISTS nombre_norm TEXT;")
            cur.execute("ALTER TABLE temas ADD COLUMN IF NOT EXISTS area_norm TEXT;")
            cur.execute("ALTER TABLE temas ALTER COLUMN nombre TYPE TEXT;")
            self._ensure_column(cur, "temas", "area", "TEXT")
            cur.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'uq_temas_nombre_norm_area_norm'
                    ) THEN
                        ALTER TABLE temas
                            ADD CONSTRAINT uq_temas_nombre_norm_area_norm UNIQUE (nombre_norm, area_norm);
                    END IF;
                END $$;
                """
            )
            try:
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_temas_nombre_norm_trgm
                        ON temas USING GIN (COALESCE(nombre_norm, '') gin_trgm_ops);
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_temas_area_norm_trgm
                        ON temas USING GIN (COALESCE(area_norm, '') gin_trgm_ops);
                    """
                )
            except Exception:
                pass

            # Migraciones de nombres:
            # - reglas_matematicas -> proposiciones_matematicas (si existe en BD vieja)
            # - teoremas_matematicas -> proposiciones_matematicas (si existe en una BD intermedia)
            if self._table_exists(cur, "reglas_matematicas") and not self._table_exists(
                cur, "proposiciones_matematicas"
            ):
                cur.execute("ALTER TABLE reglas_matematicas RENAME TO proposiciones_matematicas;")

            if self._table_exists(cur, "teoremas_matematicas") and not self._table_exists(
                cur, "proposiciones_matematicas"
            ):
                cur.execute("ALTER TABLE teoremas_matematicas RENAME TO proposiciones_matematicas;")

            # Proposiciones (teoremas/corolarios/lemas/axiomas)
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS proposiciones_matematicas (
                    id SERIAL PRIMARY KEY,
                    nombre TEXT UNIQUE NOT NULL,
                    tipo VARCHAR(50),
                    hipotesis TEXT,
                    tesis TEXT,
                    descripcion TEXT,
                    fecha_carga TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    tema_id INT REFERENCES temas(id)
                );
                """
            )
            # Si venia como "conclusion", renombrar a "tesis" (sin perder datos)
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='proposiciones_matematicas' AND column_name='conclusion';"
            )
            if cur.fetchone():
                cur.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name='proposiciones_matematicas' AND column_name='tesis';"
                )
                if not cur.fetchone():
                    cur.execute("ALTER TABLE proposiciones_matematicas RENAME COLUMN conclusion TO tesis;")

            self._ensure_column(cur, "proposiciones_matematicas", "tema_id", "INT REFERENCES temas(id)")
            self._ensure_column(cur, "proposiciones_matematicas", "hipotesis", "TEXT")
            self._ensure_column(cur, "proposiciones_matematicas", "tesis", "TEXT")
            self._ensure_column(cur, "proposiciones_matematicas", "descripcion", "TEXT")

            # Definiciones (enunciado)
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS definiciones_matematicas (
                    id SERIAL PRIMARY KEY,
                    nombre TEXT UNIQUE NOT NULL,
                    enunciado TEXT,
                    fecha_carga TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    tema_id INT REFERENCES temas(id)
                );
                """
            )
            self._ensure_column(cur, "definiciones_matematicas", "tema_id", "INT REFERENCES temas(id)")
            self._ensure_column(cur, "definiciones_matematicas", "enunciado", "TEXT")

            # Ajustes de columnas por compatibilidad (evita "value too long" en BDs creadas con VARCHAR)
            cur.execute("ALTER TABLE proposiciones_matematicas ALTER COLUMN nombre TYPE TEXT;")
            cur.execute("ALTER TABLE definiciones_matematicas ALTER COLUMN nombre TYPE TEXT;")

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"Error tabla: {e}")
            return False

    def parsear_texto_teoria(self, contenido_raw: str) -> List[Dict[str, str]]:
        contenido = contenido_raw.replace("\r\n", "\n")
        bloques = contenido.split("--- INICIO UNIDAD ---")
        reglas: List[Dict[str, str]] = []
        for bloque in bloques:
            if "--- FIN UNIDAD ---" not in bloque:
                continue
            b = bloque.split("--- FIN UNIDAD ---")[0].strip()

            def field_single(label: str) -> str:
                m = re.search(rf"(?m)^{label}:\s*(.+?)\s*$", b)
                return m.group(1).strip() if m else ""

            def field_block(label: str) -> str:
                m = re.search(rf"(?ms)^{label}:\s*(.*?)(?=^[A-Z_]+:\s*|\Z)", b)
                return m.group(1).strip() if m else ""

            clase_norm = field_single("CLASE").upper()
            nombre_val = field_single("NOMBRE")
            if not nombre_val:
                continue

            area_val = field_single("AREA") or field_single("CURSO") or "General"
            tema_val = field_single("TEMA") or "Gral"
            tema_id_val = field_single("TEMA_ID")

            if clase_norm in {"DEFINICION", "DEF"}:
                reglas.append(
                    {
                        "clase": "DEFINICION",
                        "nombre": nombre_val,
                        "area": area_val,
                        "tema": tema_val,
                        "tema_id": tema_id_val,
                        "enunciado": field_block("ENUNCIADO"),
                    }
                )
            else:
                reglas.append(
                    {
                        "clase": "PROPOSICION",
                        "nombre": nombre_val,
                        "tipo": (field_single("TIPO") or "TEOREMA"),
                        "area": area_val,
                        "tema": tema_val,
                        "tema_id": tema_id_val,
                        "hipotesis": field_block("HIPOTESIS"),
                        "tesis": field_block("TESIS"),
                        "descripcion": field_block("DESCRIPCION"),
                    }
                )
        return reglas

    def _get_tema_id(self, cur, tema_nombre: str, *, area: str = "General") -> int:
        tema_nombre = (tema_nombre or "").strip() or "Gral"
        area = (area or "").strip() or "General"
        tema_norm = self._norm_text(tema_nombre)
        area_norm = self._norm_text(area)

        cur.execute(
            "SELECT id, COALESCE(area,'') FROM temas WHERE nombre_norm=%s AND area_norm=%s;",
            (tema_norm, area_norm),
        )
        row = cur.fetchone()
        if row:
            tid = int(row[0])
            existing_area = (row[1] or "").strip()
            if not existing_area and area:
                cur.execute(
                    "UPDATE temas SET area = COALESCE(NULLIF(area,''), %s), area_norm=%s WHERE id=%s;",
                    (area, area_norm, tid),
                )
            return int(tid)

        try:
            cur.execute(
                """
                SELECT id,
                       similarity(COALESCE(nombre_norm,''), %s) AS sim_tema,
                       similarity(COALESCE(area_norm,''), %s) AS sim_area
                FROM temas
                ORDER BY sim_tema DESC, sim_area DESC
                LIMIT 1;
                """,
                (tema_norm, area_norm),
            )
            row = cur.fetchone()
            if row:
                tid = int(row[0])
                sim_tema = float(row[1] or 0)
                sim_area = float(row[2] or 0)
                if sim_tema >= self.sim_tema_threshold and (not area or sim_area >= self.sim_area_threshold):
                    cur.execute(
                        "UPDATE temas SET nombre_norm=%s, area_norm=%s WHERE id=%s;",
                        (tema_norm, area_norm, tid),
                    )
                    return int(tid)
        except Exception:
            pass

        cur.execute("SELECT id FROM temas WHERE nombre=%s AND COALESCE(area,'')=%s;", (tema_nombre, area))
        tid = cur.fetchone()
        if not tid:
            cur.execute(
                "INSERT INTO temas (nombre, area, nombre_norm, area_norm) VALUES (%s, %s, %s, %s) RETURNING id",
                (tema_nombre, area, tema_norm, area_norm),
            )
            return int(cur.fetchone()[0])
        if area:
            cur.execute(
                "UPDATE temas SET area = COALESCE(NULLIF(area,''), %s), nombre_norm=%s, area_norm=%s WHERE id=%s;",
                (area, tema_norm, area_norm, int(tid[0])),
            )
        return int(tid[0])

    def _tema_id_existe(self, cur, tema_id: int) -> bool:
        cur.execute("SELECT 1 FROM temas WHERE id=%s;", (int(tema_id),))
        return cur.fetchone() is not None

    def _resolver_tema_id(self, cur, *, tema_id_raw: str, tema_nombre: str, area: str) -> int:
        """Resuelve un tema_id valido. Si el TEMA_ID no existe, cae a TEMA (creandolo si hace falta)."""

        tema_id_raw = (tema_id_raw or "").strip()
        tema_nombre = (tema_nombre or "").strip() or "Gral"
        area = (area or "").strip() or "General"

        tema_id = None
        if tema_id_raw:
            m = re.fullmatch(r"\d+", tema_id_raw) or re.search(r"\d+", tema_id_raw)
            if m:
                tema_id = int(m.group(0))

        if tema_id and tema_id > 0:
            if self._tema_id_existe(cur, tema_id):
                # Si hay area, intentar setearla si esta vacia.
                if area:
                    cur.execute(
                        "UPDATE temas SET area = COALESCE(NULLIF(area,''), %s) WHERE id=%s;",
                        (area, int(tema_id)),
                    )
                return tema_id
            # Si el usuario/IA puso un id inexistente, no bloqueamos la carga.
            return self._get_tema_id(cur, tema_nombre, area=area)

        return self._get_tema_id(cur, tema_nombre, area=area)

    def _tabla_por_tipo(self, tipo: str) -> str:
        tipo_norm = (tipo or "").strip().upper()
        return "definiciones_matematicas" if tipo_norm.startswith("DEF") else "proposiciones_matematicas"

    def guardar_teoria(self, lista: List[Dict[str, str]], db_name: str) -> Tuple[int, str]:
        if not lista:
            return 0, "Vacio"
        self.inicializar_tablas_si_no_existen(db_name)
        conn = self.db.get_connection(db_name)
        cur = conn.cursor()
        c = 0
        errores: List[str] = []
        try:
            for r in lista:
                tid = self._resolver_tema_id(
                    cur,
                    tema_id_raw=(r.get("tema_id") or ""),
                    tema_nombre=(r.get("tema") or "Gral"),
                    area=(r.get("area") or "General"),
                )

                clase_norm = (r.get("clase") or "").strip().upper()

                nombre = (r.get("nombre") or "").strip()
                if not nombre:
                    errores.append("Registro sin NOMBRE (omitido).")
                    continue

                # Savepoint por item: permite continuar aunque una fila falle.
                cur.execute("SAVEPOINT sp_item;")
                try:
                    if clase_norm in {"DEFINICION", "DEF"}:
                        sql = """INSERT INTO definiciones_matematicas (nombre, tema_id, enunciado)
                                 VALUES (%s,%s,%s) ON CONFLICT (nombre) DO UPDATE SET
                                 tema_id=EXCLUDED.tema_id, enunciado=EXCLUDED.enunciado;"""
                        cur.execute(sql, (nombre, tid, r.get("enunciado") or ""))
                    else:
                        tipo = (r.get("tipo") or "TEOREMA").strip().upper()
                        if tipo not in {"TEOREMA", "COROLARIO", "LEMA", "AXIOMA"}:
                            tipo = "TEOREMA"
                        sql = """INSERT INTO proposiciones_matematicas (nombre, tipo, tema_id, hipotesis, tesis, descripcion)
                                 VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (nombre) DO UPDATE SET
                                 tipo=EXCLUDED.tipo, tema_id=EXCLUDED.tema_id, hipotesis=EXCLUDED.hipotesis,
                                 tesis=EXCLUDED.tesis, descripcion=EXCLUDED.descripcion;"""
                        cur.execute(
                            sql,
                            (
                                nombre,
                                tipo,
                                tid,
                                r.get("hipotesis") or "",
                                r.get("tesis") or "",
                                r.get("descripcion") or "",
                            ),
                        )
                    c += 1
                except Exception as e:
                    cur.execute("ROLLBACK TO SAVEPOINT sp_item;")
                    errores.append(f"{nombre}: {e}")

            conn.commit()
            conn.close()
            if errores:
                return c, "Guardado con errores:\n" + "\n".join(errores[:20]) + (
                    "\n...(mas errores)" if len(errores) > 20 else ""
                )
            return c, "Guardado"
        except Exception as e:
            conn.close()
            return 0, str(e)

    def guardar_teoria_en_lotes(
        self, lista: List[Dict[str, str]], db_name: str, *, batch_size: int = 25
    ) -> Tuple[int, str]:
        if batch_size <= 0:
            batch_size = 25
        total_guardados = 0
        mensajes: List[str] = []

        for i in range(0, len(lista), batch_size):
            chunk = lista[i : i + batch_size]
            c, msg = self.guardar_teoria(chunk, db_name)
            total_guardados += c
            if msg and msg != "Guardado":
                mensajes.append(f"Lote {i//batch_size + 1}: {msg}")

        if mensajes:
            return total_guardados, "\n\n".join(mensajes)
        return total_guardados, "Guardado"

    def obtener_temas(self, db_name: str) -> List[Tuple[int, str, str]]:
        self.inicializar_tablas_si_no_existen(db_name)
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, nombre, COALESCE(area,'') FROM temas ORDER BY COALESCE(area,''), nombre ASC;")
            return [(int(r[0]), r[1], r[2] or "") for r in cur.fetchall()]
        finally:
            conn.close()

    def obtener_reglas_resumen(
        self,
        db_name: str,
        *,
        tabla: str,
        tema_id: Optional[int] = None,
        limit: int = 500,
    ) -> List[Tuple[int, str, str, str]]:
        self.inicializar_tablas_si_no_existen(db_name)
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            if tabla == "definiciones_matematicas":
                select_sql = """
                    SELECT r.id, r.nombre, ''::text as tipo, t.nombre
                    FROM definiciones_matematicas r
                    LEFT JOIN temas t ON t.id = r.tema_id
                """
            else:
                select_sql = """
                    SELECT r.id, r.nombre, r.tipo, t.nombre
                    FROM proposiciones_matematicas r
                    LEFT JOIN temas t ON t.id = r.tema_id
                """
            if tema_id:
                cur.execute(
                    select_sql
                    + """
                    WHERE r.tema_id = %s
                    ORDER BY r.id DESC
                    LIMIT %s;
                    """,
                    (int(tema_id), int(limit)),
                )
            else:
                cur.execute(
                    select_sql
                    + """
                    ORDER BY r.id DESC
                    LIMIT %s;
                    """,
                    (int(limit),),
                )
            return [(int(r[0]), r[1] or "", r[2] or "", r[3] or "") for r in cur.fetchall()]
        finally:
            conn.close()

    def obtener_regla_detalle(
        self, db_name: str, *, tabla: str, regla_id: int
    ) -> Optional[Dict[str, str]]:
        self.inicializar_tablas_si_no_existen(db_name)
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            if tabla == "definiciones_matematicas":
                cur.execute(
                    """
                    SELECT r.id, r.nombre, t.nombre, COALESCE(t.area,''), r.enunciado
                    FROM definiciones_matematicas r
                    LEFT JOIN temas t ON t.id = r.tema_id
                    WHERE r.id = %s;
                    """,
                    (int(regla_id),),
                )
            else:
                cur.execute(
                    """
                    SELECT r.id, r.nombre, r.tipo, t.nombre, COALESCE(t.area,''), r.hipotesis, r.tesis, r.descripcion
                    FROM proposiciones_matematicas r
                    LEFT JOIN temas t ON t.id = r.tema_id
                    WHERE r.id = %s;
                    """,
                    (int(regla_id),),
                )
            row = cur.fetchone()
            if not row:
                return None
            if tabla == "definiciones_matematicas":
                return {
                    "id": str(int(row[0])),
                    "nombre": row[1] or "",
                    "tipo": "",
                    "tema": row[2] or "",
                    "area": row[3] or "",
                    "hipotesis": "",
                    "tesis": "",
                    "descripcion": "",
                    "enunciado": row[4] or "",
                }
            return {
                "id": str(int(row[0])),
                "nombre": row[1] or "",
                "tipo": row[2] or "",
                "tema": row[3] or "",
                "area": row[4] or "",
                "hipotesis": row[5] or "",
                "tesis": row[6] or "",
                "descripcion": row[7] or "",
                "enunciado": "",
            }
        finally:
            conn.close()

    def guardar_regla_detalle(
        self,
        db_name: str,
        *,
        tabla: str,
        regla_id: Optional[int],
        data: Dict[str, str],
    ) -> Tuple[bool, str]:
        self.inicializar_tablas_si_no_existen(db_name)
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            nombre = (data.get("nombre") or "").strip()
            if not nombre:
                return False, "El campo NOMBRE es obligatorio."

            tema_nombre = (data.get("tema") or "Gral").strip() or "Gral"
            area = (data.get("area") or "General").strip() or "General"
            tid = self._get_tema_id(cur, tema_nombre, area=area)

            if tabla == "definiciones_matematicas":
                enunciado = data.get("enunciado") or ""
            else:
                tipo = (data.get("tipo") or "TEOREMA").strip()
                hip = data.get("hipotesis") or ""
                tesis = data.get("tesis") or ""
                desc = data.get("descripcion") or ""

            if regla_id:
                if tabla == "definiciones_matematicas":
                    cur.execute(
                        """
                        UPDATE definiciones_matematicas SET
                            nombre=%s, tema_id=%s, enunciado=%s
                        WHERE id=%s;
                        """,
                        (nombre, int(tid), enunciado, int(regla_id)),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE proposiciones_matematicas SET
                            nombre=%s, tipo=%s, tema_id=%s,
                            hipotesis=%s, tesis=%s, descripcion=%s
                        WHERE id=%s;
                        """,
                        (nombre, tipo, int(tid), hip, tesis, desc, int(regla_id)),
                    )
            else:
                if tabla == "definiciones_matematicas":
                    sql = """INSERT INTO definiciones_matematicas (nombre, tema_id, enunciado)
                             VALUES (%s,%s,%s) ON CONFLICT (nombre) DO UPDATE SET
                             tema_id=EXCLUDED.tema_id, enunciado=EXCLUDED.enunciado;"""
                    cur.execute(sql, (nombre, int(tid), enunciado))
                else:
                    sql = """INSERT INTO proposiciones_matematicas (nombre, tipo, tema_id, hipotesis, tesis, descripcion)
                             VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (nombre) DO UPDATE SET
                             tipo=EXCLUDED.tipo, tema_id=EXCLUDED.tema_id, hipotesis=EXCLUDED.hipotesis,
                             tesis=EXCLUDED.tesis, descripcion=EXCLUDED.descripcion;"""
                    cur.execute(sql, (nombre, tipo, int(tid), hip, tesis, desc))

            conn.commit()
            return True, "Guardado"
        except Exception as e:
            return False, str(e)
        finally:
            conn.close()

    def exportar_teoria_formato(self, db_name: str, *, tabla: str) -> str:
        """Exporta toda la teoria en el formato de bloques para copiar/pegar."""

        self.inicializar_tablas_si_no_existen(db_name)
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            if tabla == "definiciones_matematicas":
                cur.execute(
                    """
                    SELECT d.id, d.nombre, d.tema_id, t.nombre, COALESCE(t.area,''), d.enunciado, d.fecha_carga
                    FROM definiciones_matematicas d
                    LEFT JOIN temas t ON t.id = d.tema_id
                    ORDER BY COALESCE(t.nombre,''), d.nombre;
                    """
                )
                rows = cur.fetchall()
                blocks: List[str] = []
                for _id, nombre, tema_id, tema, area, enunciado, _fecha in rows:
                    blocks.append(
                        "\n".join(
                            [
                                "--- INICIO UNIDAD ---",
                                "CLASE: DEFINICION",
                                f"NOMBRE: {nombre or ''}",
                                f"AREA: {area or ''}",
                                f"TEMA: {tema or ''}",
                                f"TEMA_ID: {tema_id or ''}",
                                "ENUNCIADO:",
                                (enunciado or "").rstrip(),
                                "--- FIN UNIDAD ---",
                            ]
                        )
                    )
                return "\n\n".join(blocks).strip() + ("\n" if blocks else "")

            cur.execute(
                """
                SELECT p.id, p.nombre, p.tipo, p.tema_id, t.nombre, COALESCE(t.area,''), p.hipotesis, p.tesis, p.descripcion, p.fecha_carga
                FROM proposiciones_matematicas p
                LEFT JOIN temas t ON t.id = p.tema_id
                ORDER BY COALESCE(t.nombre,''), p.nombre;
                """
            )
            rows = cur.fetchall()
            blocks = []
            for _id, nombre, tipo, tema_id, tema, area, hipotesis, tesis, descripcion, _fecha in rows:
                blocks.append(
                    "\n".join(
                        [
                            "--- INICIO UNIDAD ---",
                            "CLASE: PROPOSICION",
                            f"TIPO: {(tipo or '').upper()}",
                            f"NOMBRE: {nombre or ''}",
                            f"AREA: {area or ''}",
                            f"TEMA: {tema or ''}",
                            f"TEMA_ID: {tema_id or ''}",
                            "HIPOTESIS:",
                            (hipotesis or "").rstrip(),
                            "TESIS:",
                            (tesis or "").rstrip(),
                            "DESCRIPCION:",
                            (descripcion or "").rstrip(),
                            "--- FIN UNIDAD ---",
                        ]
                    )
                )
            return "\n\n".join(blocks).strip() + ("\n" if blocks else "")
        finally:
            conn.close()
