import json
import re
import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from database.connection import DatabaseManager


@dataclass
class MetodoSolucion:
    metodo: str
    propiedades: List[int]
    desarrollo_latex: str


@dataclass
class ResultadoAuditoria:
    problema_id: int
    estado: str
    tema_id: Optional[int]
    conceptos_principales: List[int]
    conceptos_secundarios: List[int]
    metodos: List[MetodoSolucion]
    respuesta_correcta: str
    nivel_dificultad: int
    razon_inconsistencia: str = ""


class AuditorController:
    def __init__(self):
        self.db = DatabaseManager()

    def listar_bases_datos(self) -> List[str]:
        return self.db.listar_bases_datos()

    def _where_estado(self, *, estado_filtro: str) -> tuple[str, list[object]]:
        estado = (estado_filtro or "").strip()
        if not estado or estado == "Todos":
            return "", []
        if estado in {"Pendiente Revision", "Sin revisar"}:
            return "(consistencia_matematica IS NULL OR consistencia_matematica = 'Sin revisar')", []
        if estado in {"Bien Planteado", "Consistente"}:
            return "consistencia_matematica = %s", ["Consistente"]
        if estado in {"Mal Planteado", "Inconsistente"}:
            return "consistencia_matematica = %s", ["Inconsistente"]
        # Default seguro
        return "(consistencia_matematica IS NULL OR consistencia_matematica = 'Sin revisar')", []

    def listar_archivos_origen(
        self,
        db_name: str,
        *,
        estado_filtro: str = "Pendiente Revision",
        limit: int = 500,
    ) -> List[Tuple[str, int]]:
        self.inicializar_tablas_si_no_existen(db_name)
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            where_expr, params = self._where_estado(estado_filtro=estado_filtro)
            where_sql = f"WHERE {where_expr}" if where_expr else ""
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

    def inicializar_tablas_si_no_existen(self, db_name: str) -> bool:
        conn = None
        try:
            conn = self.db.get_connection(db_name)
            cur = conn.cursor()

            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS unaccent;")
                cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
            except Exception:
                pass
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS temas (
                    id SERIAL PRIMARY KEY,
                    nombre TEXT NOT NULL,
                    area TEXT,
                    nombre_norm TEXT,
                    area_norm TEXT
                );
                """
            )
            cur.execute("ALTER TABLE temas ADD COLUMN IF NOT EXISTS nombre_norm TEXT;")
            cur.execute("ALTER TABLE temas ADD COLUMN IF NOT EXISTS area_norm TEXT;")
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
                    CREATE INDEX IF NOT EXISTS idx_temas_nombre_trgm
                        ON temas USING GIN (unaccent(lower(nombre)) gin_trgm_ops);
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_temas_area_trgm
                        ON temas USING GIN (unaccent(lower(COALESCE(area, ''))) gin_trgm_ops);
                    """
                )
            except Exception:
                pass

            # Tabla de proposiciones (para FKs de auditoría). Si ya existe (por Módulo 2), no se toca.
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

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS problemas (
                    id SERIAL PRIMARY KEY,
                    enunciado_latex TEXT NOT NULL,
                    consistencia_matematica VARCHAR(30) NOT NULL DEFAULT 'Sin revisar'
                );
                """
            )

            # Base (compatibilidad con Modulo 1)
            self._ensure_column(cur, "problemas", "numero_original", "INT")
            self._ensure_column(cur, "problemas", "archivo_origen", "VARCHAR(255)")
            self._ensure_column(cur, "problemas", "ruta_carpeta", "TEXT")
            self._ensure_column(cur, "problemas", "imagenes", "TEXT[]")

            # Columnas objetivo de auditoria (modulo 3)
            self._ensure_column(cur, "problemas", "tema_id", "INT REFERENCES temas(id)")
            self._ensure_column(cur, "problemas", "nivel_dificultad", "INT")
            self._ensure_column(cur, "problemas", "soluciones", "JSONB DEFAULT '[]'::jsonb")

            # Renombres / migraciones de columnas antiguas
            self._rename_column_if_exists(cur, "problemas", "auditoria_razon", "razon_inconsistencia")
            self._rename_column_if_exists(cur, "problemas", "respuesta", "respuesta_correcta")

            # Eliminar columnas obsoletas (si existen)
            self._drop_column_if_exists(cur, "problemas", "tema")
            self._drop_column_if_exists(cur, "problemas", "reglas_sugeridas_ia")

            # Asegurar columnas finales
            self._ensure_column(cur, "problemas", "razon_inconsistencia", "TEXT")
            self._ensure_column(cur, "problemas", "respuesta_correcta", "VARCHAR(10)")
            self._ensure_column(
                cur,
                "problemas",
                "consistencia_matematica",
                "VARCHAR(30) NOT NULL DEFAULT 'Sin revisar'",
            )

            # Eliminar definitivamente columnas legacy, preservando datos si ya existen las nuevas
            self._copy_and_drop_legacy_column(
                cur, table="problemas", old="auditoria_razon", new="razon_inconsistencia"
            )
            self._copy_and_drop_legacy_column(
                cur, table="problemas", old="respuesta", new="respuesta_correcta"
            )

            # conceptos_ia: usar INTEGER[] (si venía como JSONB, migrar)
            self._ensure_conceptos_ia_integer_array(cur)

            # Normalizar tipos antes de agregar CHECKs (evita errores tipo "varchar >= int")
            self._ensure_column_type_int(cur, table="problemas", column="nivel_dificultad")
            self._ensure_column_type_int(cur, table="problemas", column="tema_id")
            self._ensure_column_type_varchar(cur, table="problemas", column="respuesta_correcta", size=10)
            self._ensure_column_type_varchar(cur, table="problemas", column="consistencia_matematica", size=30)

            # Limpiar valores fuera de rango/invalidos antes de constraints
            self._sanitize_problemas(cur)

            # CHECK constraints (validaciones fuertes)
            self._ensure_constraint(
                cur,
                table="problemas",
                name="ck_problemas_consistencia_matematica",
                expr="consistencia_matematica IN ('Sin revisar','Consistente','Inconsistente')",
            )
            self._ensure_constraint(
                cur,
                table="problemas",
                name="ck_problemas_nivel_dificultad",
                expr="(nivel_dificultad IS NULL) OR (nivel_dificultad BETWEEN 1 AND 5)",
            )
            self._ensure_constraint(
                cur,
                table="problemas",
                name="ck_problemas_respuesta_correcta",
                expr="(respuesta_correcta IS NULL) OR (respuesta_correcta IN ('A','B','C','D','E'))",
            )

            # Tablas normalizadas: conceptos y soluciones
            self._ensure_tablas_normalizadas(cur)

            # Ajustes para evitar "value too long"
            cur.execute("ALTER TABLE temas ALTER COLUMN nombre TYPE TEXT;")
            self._ensure_column(cur, "temas", "area", "TEXT")

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"Error tabla: {e}")
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
            return False

    def _ensure_column(self, cur, table: str, column: str, ddl_type: str) -> None:
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name=%s AND column_name=%s;",
            (table, column),
        )
        if not cur.fetchone():
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type};")

    def _drop_column_if_exists(self, cur, table: str, column: str) -> None:
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name=%s AND column_name=%s;",
            (table, column),
        )
        if cur.fetchone():
            cur.execute(f"ALTER TABLE {table} DROP COLUMN {column};")

    def _column_exists(self, cur, table: str, column: str) -> bool:
        cur.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s;",
            (table, column),
        )
        return cur.fetchone() is not None

    def _rename_column_if_exists(self, cur, table: str, old: str, new: str) -> None:
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name=%s AND column_name=%s;",
            (table, old),
        )
        if not cur.fetchone():
            return
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name=%s AND column_name=%s;",
            (table, new),
        )
        if cur.fetchone():
            return
        cur.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new};")

    def _copy_and_drop_legacy_column(self, cur, *, table: str, old: str, new: str) -> None:
        """
        Si existen ambas columnas, copia valores a la nueva (solo si la nueva está NULL)
        y elimina la columna vieja.
        """
        if not self._column_exists(cur, table, old):
            return
        if not self._column_exists(cur, table, new):
            return
        cur.execute(
            f"""
            UPDATE {table}
            SET {new} = COALESCE({new}, {old})
            WHERE {old} IS NOT NULL AND {new} IS NULL;
            """
        )
        self._drop_column_if_exists(cur, table, old)

    def _ensure_constraint(self, cur, *, table: str, name: str, expr: str) -> None:
        cur.execute("SELECT 1 FROM pg_constraint WHERE conname = %s;", (name,))
        if cur.fetchone():
            return
        cur.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expr});")

    def _get_column_info(self, cur, *, table: str, column: str) -> Optional[Tuple[str, str]]:
        cur.execute(
            """
            SELECT data_type, udt_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s AND column_name=%s;
            """,
            (table, column),
        )
        row = cur.fetchone()
        if not row:
            return None
        return str(row[0]), str(row[1])

    def _ensure_column_type_int(self, cur, *, table: str, column: str) -> None:
        info = self._get_column_info(cur, table=table, column=column)
        if not info:
            return
        data_type, _udt = info
        if data_type == "integer":
            return
        if data_type not in {"character varying", "text"}:
            # Intentar cast directo si es numerico
            cur.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE INT USING NULLIF({column}::text,'')::int;")
            return
        # Extrae digitos; si no hay digitos -> NULL
        cur.execute(
            f"""
            ALTER TABLE {table}
            ALTER COLUMN {column}
            TYPE INT
            USING NULLIF(regexp_replace({column}::text, '[^0-9]', '', 'g'), '')::int;
            """
        )

    def _ensure_column_type_varchar(self, cur, *, table: str, column: str, size: int) -> None:
        info = self._get_column_info(cur, table=table, column=column)
        if not info:
            return
        data_type, _udt = info
        if data_type == "character varying":
            return
        cur.execute(
            f"""
            ALTER TABLE {table}
            ALTER COLUMN {column}
            TYPE VARCHAR({int(size)})
            USING NULLIF({column}::text,'');
            """
        )

    def _sanitize_problemas(self, cur) -> None:
        if self._column_exists(cur, "problemas", "consistencia_matematica"):
            cur.execute(
                """
                UPDATE problemas
                SET consistencia_matematica = 'Sin revisar'
                WHERE consistencia_matematica IS NULL
                   OR consistencia_matematica NOT IN ('Sin revisar','Consistente','Inconsistente');
                """
            )
        # nivel_dificultad: fuera de rango -> NULL
        if self._column_exists(cur, "problemas", "nivel_dificultad"):
            cur.execute("UPDATE problemas SET nivel_dificultad = NULL WHERE nivel_dificultad IS NOT NULL AND (nivel_dificultad < 1 OR nivel_dificultad > 5);")
        # respuesta_correcta: fuera de A-E -> NULL
        if self._column_exists(cur, "problemas", "respuesta_correcta"):
            cur.execute(
                """
                UPDATE problemas
                SET respuesta_correcta = NULL
                WHERE respuesta_correcta IS NOT NULL
                  AND upper(left(respuesta_correcta, 1)) NOT IN ('A','B','C','D','E');
                """
            )

    def _ensure_tablas_normalizadas(self, cur) -> None:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS problema_conceptos (
                problema_id INT REFERENCES problemas(id) ON DELETE CASCADE,
                proposicion_id INT REFERENCES proposiciones_matematicas(id),
                rol VARCHAR(12) NOT NULL,
                peso INT,
                PRIMARY KEY (problema_id, proposicion_id),
                CONSTRAINT ck_problema_conceptos_rol CHECK (rol IN ('principal','secundario'))
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS soluciones (
                id SERIAL PRIMARY KEY,
                problema_id INT REFERENCES problemas(id) ON DELETE CASCADE,
                orden INT NOT NULL DEFAULT 1,
                metodo_nombre TEXT NOT NULL,
                solucion_latex TEXT NOT NULL,
                autor_ia TEXT,
                fecha_carga TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS solucion_proposiciones (
                solucion_id INT REFERENCES soluciones(id) ON DELETE CASCADE,
                proposicion_id INT REFERENCES proposiciones_matematicas(id),
                PRIMARY KEY (solucion_id, proposicion_id)
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_problema_conceptos_proposicion_id ON problema_conceptos (proposicion_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_soluciones_problema_id ON soluciones (problema_id);")

    def _ensure_conceptos_ia_integer_array(self, cur) -> None:
        cur.execute(
            """
            SELECT data_type, udt_name
            FROM information_schema.columns
            WHERE table_name='problemas' AND column_name='conceptos_ia';
            """
        )
        row = cur.fetchone()
        if not row:
            cur.execute("ALTER TABLE problemas ADD COLUMN conceptos_ia INTEGER[];")
            return
        data_type, udt_name = row[0], row[1]
        if data_type == "ARRAY" and udt_name == "_int4":
            return
        # Si existía como JSONB u otro, migramos a INTEGER[]
        cur.execute("ALTER TABLE problemas ADD COLUMN IF NOT EXISTS conceptos_ia_new INTEGER[];")
        if data_type == "jsonb":
            cur.execute(
                """
                UPDATE problemas
                SET conceptos_ia_new = COALESCE(
                    ARRAY(
                        SELECT (v)::int
                        FROM jsonb_array_elements_text(conceptos_ia) AS v
                        WHERE v ~ '^[0-9]+$'
                    ),
                    '{}'::int[]
                )
                WHERE conceptos_ia IS NOT NULL;
                """
            )
        cur.execute("ALTER TABLE problemas DROP COLUMN conceptos_ia;")
        cur.execute("ALTER TABLE problemas RENAME COLUMN conceptos_ia_new TO conceptos_ia;")

    def obtener_pendientes(
        self,
        db_name: str,
        *,
        limit: int = 10,
        archivo_origen_filtro: str = "",
        estado_filtro: str = "Pendiente Revision",
    ) -> List[Tuple[int, str]]:
        self.inicializar_tablas_si_no_existen(db_name)
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            clauses: List[str] = []
            params: List[object] = []

            where_expr, where_params = self._where_estado(estado_filtro=estado_filtro)
            if where_expr:
                clauses.append(where_expr)
                params.extend(where_params)

            filtro = (archivo_origen_filtro or "").strip()
            if filtro:
                clauses.append("archivo_origen ILIKE %s")
                params.append(f"%{filtro}%")

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

    def guardar_resultado(self, db_name: str, resultado: ResultadoAuditoria) -> None:
        self.inicializar_tablas_si_no_existen(db_name)
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            self._guardar_resultado_tx(cur, resultado)
            conn.commit()
        finally:
            conn.close()

    def guardar_resultados_lote(self, db_name: str, resultados: List[ResultadoAuditoria]) -> tuple[int, str]:
        if not resultados:
            return 0, "Vacío"

        self.inicializar_tablas_si_no_existen(db_name)
        conn = self.db.get_connection(db_name)
        cur = conn.cursor()
        ok_count = 0
        errores: List[str] = []
        try:
            for r in resultados:
                cur.execute("SAVEPOINT sp_item;")
                try:
                    self._guardar_resultado_tx(cur, r)
                    ok_count += 1
                except Exception as exc:
                    cur.execute("ROLLBACK TO SAVEPOINT sp_item;")
                    errores.append(f"ID {r.problema_id}: {exc}")

            conn.commit()
            if errores:
                return ok_count, "Guardado con errores:\n" + "\n".join(errores[:25]) + (
                    "\n...(mas errores)" if len(errores) > 25 else ""
                )
            return ok_count, "Guardado"
        finally:
            conn.close()

    def validar_resultado(self, db_name: str, resultado: ResultadoAuditoria) -> Tuple[bool, List[str], List[str]]:
        """
        Valida un resultado contra reglas de calidad y existencia de teoría (FKs lógicas).
        No escribe en BD.
        """

        self.inicializar_tablas_si_no_existen(db_name)
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            return self._validar_resultado_tx(cur, resultado)
        finally:
            conn.close()

    def validar_lote(
        self, db_name: str, resultados: List[ResultadoAuditoria]
    ) -> Tuple[int, Dict[int, List[str]], Dict[int, List[str]]]:
        """
        Valida un lote. Devuelve (ok_count, errores_por_id, warnings_por_id).
        """

        if not resultados:
            return 0, {}, {}
        self.inicializar_tablas_si_no_existen(db_name)
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            ok = 0
            errs: Dict[int, List[str]] = {}
            warns: Dict[int, List[str]] = {}
            for r in resultados:
                is_ok, e, w = self._validar_resultado_tx(cur, r)
                if is_ok:
                    ok += 1
                if e:
                    errs[int(r.problema_id)] = e
                if w:
                    warns[int(r.problema_id)] = w
            return ok, errs, warns
        finally:
            conn.close()

    def _guardar_resultado_tx(self, cur, resultado: ResultadoAuditoria) -> None:
        """
        Guarda un ResultadoAuditoria dentro de una transacción abierta, con validación estricta.
        """

        pid = int(resultado.problema_id)

        estado = (resultado.estado or "").strip() or "Pendiente Revision"
        if estado not in {"Pendiente Revision", "Bien Planteado", "Mal Planteado"}:
            raise ValueError("consistencia_matematica invalida")

        respuesta = (resultado.respuesta_correcta or "").strip().upper()
        if respuesta:
            respuesta = respuesta[0]
        if respuesta and respuesta not in {"A", "B", "C", "D", "E"}:
            raise ValueError("respuesta_correcta inválida (solo A-E)")

        dificultad = int(resultado.nivel_dificultad or 0)
        if dificultad and (dificultad < 1 or dificultad > 5):
            raise ValueError("nivel_dificultad inválido (1-5)")

        tema_id = resultado.tema_id
        if tema_id is not None:
            cur.execute("SELECT 1 FROM temas WHERE id=%s;", (int(tema_id),))
            if not cur.fetchone():
                raise ValueError(f"Falta tema_id={tema_id} en tabla temas (carga la teoría/temas).")

        if estado == "Mal Planteado" and not (resultado.razon_inconsistencia or "").strip():
            raise ValueError("Mal Planteado requiere RAZON_INCONSISTENCIA.")

        principales = sorted({int(x) for x in (resultado.conceptos_principales or []) if int(x) > 0})
        secundarios = sorted({int(x) for x in (resultado.conceptos_secundarios or []) if int(x) > 0})
        secundarios = [x for x in secundarios if x not in set(principales)]

        # IDs usados en métodos (propiedades por solución)
        props_metodos: List[int] = []
        for m in resultado.metodos:
            props_metodos.extend([int(x) for x in (m.propiedades or []) if int(x) > 0])
        props_metodos = sorted({int(x) for x in props_metodos if int(x) > 0})

        # Validación de proposiciones: no inventar IDs
        all_prop_ids = sorted({*principales, *secundarios, *props_metodos})
        if all_prop_ids:
            cur.execute(
                "SELECT id FROM proposiciones_matematicas WHERE id = ANY(%s::int[]);",
                (all_prop_ids,),
            )
            existentes = {int(r[0]) for r in cur.fetchall()}
            faltan = [x for x in all_prop_ids if x not in existentes]
            if faltan:
                raise ValueError(f"Falta teoría: proposiciones_matematicas id(s) {faltan}")

        # Reglas de calidad extra para Bien Planteado
        metodos_no_vacios = [m for m in (resultado.metodos or []) if (m.desarrollo_latex or "").strip()]
        if estado == "Bien Planteado":
            if tema_id is None:
                raise ValueError("Bien Planteado requiere TEMA_ID.")
            if not respuesta:
                raise ValueError("Bien Planteado requiere RESPUESTA_CORRECTA (A-E).")
            if dificultad < 1 or dificultad > 5:
                raise ValueError("Bien Planteado requiere NIVEL_DIFICULTAD (1-5).")
            if len(metodos_no_vacios) < 2:
                raise ValueError("Bien Planteado requiere 2 o más soluciones (metodos con DESARROLLO).")

        # Persistencia en problemas
        cur.execute(
            """
            UPDATE problemas SET
                consistencia_matematica = %s,
                razon_inconsistencia = %s,
                tema_id = %s,
                respuesta_correcta = %s,
                nivel_dificultad = %s
            WHERE id = %s;
            """,
            (
                estado,
                (resultado.razon_inconsistencia or None),
                tema_id,
                (respuesta or None),
                (dificultad or None),
                pid,
            ),
        )

        # Conceptos normalizados
        cur.execute("DELETE FROM problema_conceptos WHERE problema_id=%s;", (pid,))
        for cid in principales:
            cur.execute(
                "INSERT INTO problema_conceptos (problema_id, proposicion_id, rol) VALUES (%s,%s,'principal');",
                (pid, int(cid)),
            )
        for cid in secundarios:
            cur.execute(
                "INSERT INTO problema_conceptos (problema_id, proposicion_id, rol) VALUES (%s,%s,'secundario');",
                (pid, int(cid)),
            )

        # Soluciones normalizadas
        cur.execute("DELETE FROM soluciones WHERE problema_id=%s;", (pid,))
        for idx, m in enumerate(resultado.metodos or [], start=1):
            metodo_nombre = (m.metodo or "").strip() or "(sin metodo)"
            latex = (m.desarrollo_latex or "").strip()
            if not latex:
                continue
            cur.execute(
                """
                INSERT INTO soluciones (problema_id, orden, metodo_nombre, solucion_latex, autor_ia)
                VALUES (%s,%s,%s,%s,%s)
                RETURNING id;
                """,
                (pid, int(idx), metodo_nombre, latex, "GPT"),
            )
            sol_id = int(cur.fetchone()[0])
            for prop_id in sorted({int(x) for x in (m.propiedades or []) if int(x) > 0}):
                cur.execute(
                    "INSERT INTO solucion_proposiciones (solucion_id, proposicion_id) VALUES (%s,%s) ON CONFLICT DO NOTHING;",
                    (sol_id, int(prop_id)),
                )

        # Compatibilidad: mantener columnas si existen (no son fuente de verdad)
        cur.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name='problemas'
              AND column_name IN ('conceptos_ia','soluciones');
            """
        )
        cols = {r[0]: r[1] for r in cur.fetchall()}
        if "conceptos_ia" in cols:
            cur.execute(
                "UPDATE problemas SET conceptos_ia=%s WHERE id=%s;",
                (principales + secundarios or None, pid),
            )
        if "soluciones" in cols and cols["soluciones"] == "jsonb":
            soluciones_json = [
                {
                    "metodo_nombre": (m.metodo or "").strip(),
                    "solucion_latex": (m.desarrollo_latex or "").strip(),
                    "propiedades": sorted({int(x) for x in (m.propiedades or []) if int(x) > 0}),
                    "autor_ia": "GPT",
                }
                for m in (resultado.metodos or [])
                if (m.desarrollo_latex or "").strip()
            ]
            cur.execute(
                "UPDATE problemas SET soluciones=%s::jsonb WHERE id=%s;",
                (json.dumps(soluciones_json, ensure_ascii=False), pid),
            )

    def _validar_resultado_tx(
        self, cur, resultado: ResultadoAuditoria
    ) -> Tuple[bool, List[str], List[str]]:
        errors: List[str] = []
        warnings: List[str] = []

        estado = (resultado.estado or "").strip() or "Pendiente Revision"
        if estado not in {"Pendiente Revision", "Bien Planteado", "Mal Planteado"}:
            errors.append("consistencia_matematica invalida.")

        respuesta = (resultado.respuesta_correcta or "").strip().upper()
        if respuesta:
            respuesta = respuesta[0]
        if respuesta and respuesta not in {"A", "B", "C", "D", "E"}:
            errors.append("respuesta_correcta inválida (solo A-E).")

        dificultad = int(resultado.nivel_dificultad or 0)
        if dificultad and (dificultad < 1 or dificultad > 5):
            errors.append("nivel_dificultad inválido (1-5).")

        if estado == "Mal Planteado" and not (resultado.razon_inconsistencia or "").strip():
            errors.append("Mal Planteado requiere RAZON_INCONSISTENCIA.")

        tema_id = resultado.tema_id
        if estado == "Bien Planteado" and tema_id is None:
            errors.append("Bien Planteado requiere TEMA_ID.")
        if tema_id is not None:
            cur.execute("SELECT 1 FROM temas WHERE id=%s;", (int(tema_id),))
            if not cur.fetchone():
                errors.append(f"Falta teoría de tema_id={tema_id} (no existe en temas).")

        if estado == "Bien Planteado":
            if not respuesta:
                errors.append("Bien Planteado requiere RESPUESTA_CORRECTA (A-E).")
            if dificultad < 1 or dificultad > 5:
                errors.append("Bien Planteado requiere NIVEL_DIFICULTAD (1-5).")

        metodos_no_vacios = [m for m in (resultado.metodos or []) if (m.desarrollo_latex or "").strip()]
        if estado == "Bien Planteado" and len(metodos_no_vacios) < 2:
            errors.append("Bien Planteado requiere 2 o más soluciones (DESARROLLO no vacío).")
        if estado != "Bien Planteado" and len(metodos_no_vacios) == 0:
            warnings.append("Sin soluciones registradas.")

        principales = sorted({int(x) for x in (resultado.conceptos_principales or []) if int(x) > 0})
        secundarios = sorted({int(x) for x in (resultado.conceptos_secundarios or []) if int(x) > 0})
        secundarios = [x for x in secundarios if x not in set(principales)]

        props_metodos: List[int] = []
        for m in (resultado.metodos or []):
            props_metodos.extend([int(x) for x in (m.propiedades or []) if int(x) > 0])
            if estado == "Bien Planteado" and (m.desarrollo_latex or "").strip() and not (m.propiedades or []):
                warnings.append("Hay un METODO con DESARROLLO pero sin PROPIEDADES.")
        props_metodos = sorted({int(x) for x in props_metodos if int(x) > 0})

        all_prop_ids = sorted({*principales, *secundarios, *props_metodos})
        if estado == "Bien Planteado" and not (principales or secundarios):
            warnings.append("Sin CONCEPTOS_PRINCIPALES/SECUNDARIOS.")

        if all_prop_ids:
            cur.execute(
                "SELECT id FROM proposiciones_matematicas WHERE id = ANY(%s::int[]);",
                (all_prop_ids,),
            )
            existentes = {int(r[0]) for r in cur.fetchall()}
            faltan = [x for x in all_prop_ids if x not in existentes]
            if faltan:
                errors.append(f"Falta teoría: proposiciones_matematicas id(s) {faltan}")

        return (len(errors) == 0), errors, warnings

    def reset_campos_auditoria(self, db_name: str) -> tuple[int, str]:
        """
        Vacia columnas de auditoria y deja `consistencia_matematica` en 'Sin revisar'
        para todos los registros.
        """

        self.inicializar_tablas_si_no_existen(db_name)
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT column_name, data_type, udt_name
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name='problemas';
                """
            )
            cols = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

            sets: List[str] = []
            if "consistencia_matematica" in cols:
                sets.append("consistencia_matematica = 'Sin revisar'")
            if "nivel_dificultad" in cols:
                sets.append("nivel_dificultad = NULL")
            if "razon_inconsistencia" in cols:
                sets.append("razon_inconsistencia = NULL")
            if "embedding" in cols:
                sets.append("embedding = NULL")
            if "respuesta_correcta" in cols:
                sets.append("respuesta_correcta = NULL")
            if "tema_id" in cols:
                sets.append("tema_id = NULL")

            if "soluciones" in cols:
                dt, _udt = cols["soluciones"]
                if dt == "jsonb":
                    sets.append("soluciones = '[]'::jsonb")
                else:
                    sets.append("soluciones = NULL")

            if "conceptos_ia" in cols:
                dt, udt = cols["conceptos_ia"]
                if dt == "ARRAY" or udt == "_int4":
                    sets.append("conceptos_ia = NULL")
                elif dt == "jsonb":
                    sets.append("conceptos_ia = '[]'::jsonb")
                else:
                    sets.append("conceptos_ia = NULL")

            if not sets:
                return 0, "No hay columnas para resetear."

            cur.execute(f"UPDATE problemas SET {', '.join(sets)};")
            affected = int(cur.rowcount or 0)

            # Tablas normalizadas (si existen): limpiar
            cur.execute("SELECT to_regclass('public.solucion_proposiciones');")
            if cur.fetchone()[0] is not None:
                cur.execute("DELETE FROM solucion_proposiciones;")
            cur.execute("SELECT to_regclass('public.soluciones');")
            if cur.fetchone()[0] is not None:
                cur.execute("DELETE FROM soluciones;")
            cur.execute("SELECT to_regclass('public.problema_conceptos');")
            if cur.fetchone()[0] is not None:
                cur.execute("DELETE FROM problema_conceptos;")

            conn.commit()
            return affected, "Reset aplicado"
        except Exception as exc:
            conn.rollback()
            return 0, str(exc)
        finally:
            conn.close()

    def vaciar_tabla_problemas(self, db_name: str) -> tuple[int, str]:
        """
        Elimina TODOS los registros de `problemas` y reinicia el ID en 1.
        También trunca tablas dependientes vía CASCADE (soluciones, problema_conceptos, etc.).
        """

        self.inicializar_tablas_si_no_existen(db_name)
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            try:
                cur.execute("SELECT COUNT(*) FROM problemas;")
                total = int(cur.fetchone()[0] or 0)
            except Exception:
                total = 0
            cur.execute("TRUNCATE TABLE problemas RESTART IDENTITY CASCADE;")
            conn.commit()
            return total, "Tabla problemas vaciada (ID reiniciado)."
        except Exception as exc:
            conn.rollback()
            return 0, str(exc)
        finally:
            conn.close()

    def actualizar_enunciado(self, db_name: str, *, problema_id: int, enunciado_latex: str) -> int:
        self.inicializar_tablas_si_no_existen(db_name)
        conn = self.db.get_connection(db_name)
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE problemas SET enunciado_latex=%s WHERE id=%s;",
                ((enunciado_latex or "").strip(), int(problema_id)),
            )
            affected = int(cur.rowcount or 0)
            conn.commit()
            return affected
        finally:
            conn.close()

    def parsear_respuesta_lote(self, texto: str) -> Dict[int, ResultadoAuditoria]:
        resultados: Dict[int, ResultadoAuditoria] = {}

        # Particion por ::ID::
        bloque_pat = re.compile(r"::ID::\s*\(?(?P<id>\d+)\)?(?P<body>.*?)(?=::ID::|\Z)", re.DOTALL)
        for match in bloque_pat.finditer(texto):
            pid = int(match.group("id"))
            body = match.group("body")

            estado = self._buscar_linea(body, r"::ESTADO::\s*(.+)") or "Pendiente Revision"
            if not estado or estado == "Pendiente Revision":
                estado = self._buscar_linea(body, r"::ESTADO_CONSISTENCIA::\s*(.+)") or estado
            tema_raw = self._buscar_linea(body, r"::TEMA_ID::\s*\(?(?P<v>\d+)\)?")
            tema_id = int(tema_raw) if tema_raw and tema_raw.isdigit() else None
            clave = (
                self._buscar_linea(body, r"::RESPUESTA_CORRECTA::\s*(.+)")
                or self._buscar_linea(body, r"::CLAVE_CORRECTA::\s*(.+)")
                or ""
            ).strip()
            dif_raw = (
                self._buscar_linea(body, r"::NIVEL_DIFICULTAD::\s*(\d+)")
                or self._buscar_linea(body, r"::DIFICULTAD::\s*(\d+)")
                or "0"
            ).strip()
            dificultad = int(dif_raw) if dif_raw.isdigit() else 0

            principales_raw = (
                self._buscar_linea(body, r"::CONCEPTOS_PRINCIPALES::\s*(.+)")
                or self._buscar_linea(body, r"::CONCEPTOS_IA::\s*(.+)")
                or ""
            )
            secundarios_raw = self._buscar_linea(body, r"::CONCEPTOS_SECUNDARIOS::\s*(.+)") or ""
            conceptos_principales = [int(x) for x in re.findall(r"\d+", principales_raw)]
            conceptos_secundarios = [int(x) for x in re.findall(r"\d+", secundarios_raw)]

            metodos = self._parsear_metodos(body)
            if not metodos:
                metodos = [MetodoSolucion(metodo="(sin metodo)", propiedades=[], desarrollo_latex="")]

            razon = ""
            if "Mal Planteado" in estado:
                razon = (
                    self._buscar_linea(body, r"::RAZON_INCONSISTENCIA::\s*(.+)")
                    or self._buscar_linea(body, r"::RAZON::\s*(.+)")
                    or ""
                )

            resultados[pid] = ResultadoAuditoria(
                problema_id=pid,
                estado=estado.strip(),
                tema_id=tema_id,
                conceptos_principales=conceptos_principales,
                conceptos_secundarios=conceptos_secundarios,
                metodos=metodos,
                respuesta_correcta=clave.strip(),
                nivel_dificultad=dificultad,
                razon_inconsistencia=razon.strip(),
            )

        return resultados

    def _buscar_linea(self, texto: str, patron: str) -> Optional[str]:
        m = re.search(patron, texto)
        if not m:
            return None
        if "v" in m.groupdict():
            return m.group("v")
        return m.group(1).strip()

    def _parsear_metodos(self, texto: str) -> List[MetodoSolucion]:
        metodos: List[MetodoSolucion] = []

        # Captura por bloques ::METODO:: ... ::FIN_METODO::
        pat = re.compile(
            r"::METODO::\s*(?P<nombre>.+?)\n(?P<body>.*?)(?=::FIN_METODO::)",
            re.DOTALL,
        )
        for m in pat.finditer(texto):
            nombre = m.group("nombre").strip()
            body = m.group("body")

            props_raw = (
                self._buscar_linea(body, r"::PROPIEDADES::\s*(.+)")
                or self._buscar_linea(body, r"::REGLAS::\s*(.+)")
                or ""
            )
            props = [int(x) for x in re.findall(r"\d+", props_raw)]

            desarrollo = ""
            m_des = re.search(r"::DESARROLLO::\s*(?P<d>.*)", body, re.DOTALL)
            if m_des:
                desarrollo = m_des.group("d").strip()

            metodos.append(MetodoSolucion(metodo=nombre, propiedades=props, desarrollo_latex=desarrollo))

        return metodos
