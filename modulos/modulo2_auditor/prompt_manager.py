from __future__ import annotations

from typing import List, Optional, Tuple

from database.connection import DatabaseManager


class PromptManager:
    # ---------------------------------------------------------------------
    # Prompt 1: Solucion didactica (no es el formato de BD).
    # ---------------------------------------------------------------------
    def generar_prompt_solucion_didactica(self, lista_problemas: List[Tuple[int, str]]) -> str:
        cant = len(lista_problemas)

        texto_problemas = ""
        for pid, latex in lista_problemas:
            texto_problemas += f"\n--- ID {pid} ---\n{latex}\n"

        return f"""Actua como Auditor Matematico Senior y Profesor Experto.
Procesaras un LOTE de {cant} problemas.

OBJETIVOS CRITICOS:
1. LIMPIEZA OCR: La entrada puede tener caracteres basura (ej: £, æ, \\item). ELIMINALOS COMPLETAMENTE.
2. NORMALIZACION LATEX: Usa ESTRICTAMENTE $...$ para formulas en linea y $$...$$ para bloques. PROHIBIDO usar \\[ o \\(.
3. NIVEL DE DETALLE: EXTREMO. No saltes pasos algebraicos. Explica cada transformacion.

FORMATO DE RESPUESTA (Responde UNICAMENTE con esta estructura por cada problema):

::ID:: (ID original del problema)
::ESTADO:: (Bien Planteado / Mal Planteado)
::RAZON:: (Solo si es Mal Planteado; si es Bien Planteado, vacio)
::METODO:: (Nombre descriptivo del metodo)
::DESARROLLO::
(1) Enunciado limpio (sin basura OCR).
(2) Solucion paso a paso, con cada operacion intermedia.
(3) Usa $$...$$ para ecuaciones principales.
::FIN_METODO::

(Si hay otro metodo valido, repite desde ::METODO:: hasta ::FIN_METODO::)

--- PROBLEMAS A PROCESAR ---
{texto_problemas}
"""

    def generar_prompt_resolucion(self, lista_problemas: List[Tuple[int, str]]) -> str:
        # Compatibilidad: nombre historico.
        return self.generar_prompt_solucion_didactica(lista_problemas)

    # ---------------------------------------------------------------------
    # Prompt 2: Formateo de teoria (Modulo 2).
    # ---------------------------------------------------------------------
    def generar_prompt_carga_teorica(self, db_name: str):
        # Reservado para una futura generacion automatica de catalogos.
        pass

    def generar_prompt_formateo_teoria(self) -> str:
        return (
            "Actua como Editor/Normalizador de Teoria Matematica.\n"
            "Tu tarea es convertir el contenido que yo te entregue en un TEXTO PLANO con un formato ESTRICTO.\n"
            "\n"
            "REGLAS GENERALES (OBLIGATORIAS):\n"
            "1) Devuelve UNICAMENTE el texto final formateado. Prohibido agregar comentarios.\n"
            "2) Debes producir una secuencia de bloques. Cada bloque debe iniciar y terminar EXACTAMENTE con:\n"
            "   --- INICIO UNIDAD ---\n"
            "   ... (campos) ...\n"
            "   --- FIN UNIDAD ---\n"
            "3) Las etiquetas de campos deben estar en MAYUSCULAS y terminar con ':' exactamente.\n"
            "4) Si falta informacion, deja el campo vacio (pero conserva la etiqueta).\n"
            "5) LaTeX: usa SOLO $...$ o $$...$$. PROHIBIDO usar \\\\[ \\\\] o \\\\( \\\\).\n"
            "6) PROHIBIDO inventar IDs (ej: TEMA_ID). Si no conoces el ID real, deja el campo vacio y usa TEMA.\n"
            "\n"
            "DEBES CLASIFICAR CADA UNIDAD CON CLASE:\n"
            "- CLASE: DEFINICION  -> definiciones_matematicas\n"
            "- CLASE: PROPOSICION -> proposiciones_matematicas\n"
            "\n"
            "FORMATO PARA DEFINICION:\n"
            "--- INICIO UNIDAD ---\n"
            "CLASE: DEFINICION\n"
            "NOMBRE:\n"
            "AREA: (curso; ejemplo: Algebra / Aritmetica / Geometria Plana)\n"
            "TEMA: (si no hay, Gral)\n"
            "TEMA_ID: (opcional; si no sabes, vacio)\n"
            "ENUNCIADO:\n"
            "(multilinea)\n"
            "--- FIN UNIDAD ---\n"
            "\n"
            "FORMATO PARA PROPOSICION:\n"
            "--- INICIO UNIDAD ---\n"
            "CLASE: PROPOSICION\n"
            "TIPO: (TEOREMA | COROLARIO | LEMA | AXIOMA)\n"
            "NOMBRE:\n"
            "AREA: (curso; ejemplo: Algebra / Aritmetica / Geometria Plana; si no hay, General)\n"
            "TEMA: (si no hay, Gral)\n"
            "TEMA_ID: (opcional; si no sabes, vacio)\n"
            "HIPOTESIS:\n"
            "(multilinea)\n"
            "TESIS:\n"
            "(multilinea)\n"
            "DESCRIPCION:\n"
            "(multilinea)\n"
            "--- FIN UNIDAD ---\n"
        )

    # ---------------------------------------------------------------------
    # Prompt 3: Clasificacion para llenar campos de BD (Modulo 3).
    # ---------------------------------------------------------------------
    def generar_prompt_clasificacion_bd(
        self,
        lista_problemas: List[Tuple[int, str]],
        *,
        db_name: Optional[str] = None,
        temas_limit: int = 200,
        proposiciones_limit: int = 500,
    ) -> str:
        cant = len(lista_problemas)
        header = (
            "Actua como Auditor Matematico y Clasificador.\n"
            f"Procesaras un LOTE de {cant} problemas.\n"
            "\n"
            "OBJETIVO: devolver SOLO texto con el formato estricto (sin explicaciones).\n"
            "LaTeX: usa SOLO $...$ o $$...$$. PROHIBIDO usar \\\\[ \\\\] o \\\\( \\\\).\n"
            "PROHIBIDO inventar IDs (tema_id o ids de proposiciones). Si no estas seguro, deja el campo vacio.\n"
            "\n"
            "FORMATO POR PROBLEMA (OBLIGATORIO, en este orden):\n"
            "::ID:: (id)\n"
            "::ESTADO_CONSISTENCIA:: (Bien Planteado / Mal Planteado / Pendiente Revision)\n"
            "::TEMA_ID:: (id de temas)\n"
            "::NIVEL_DIFICULTAD:: (1-5)\n"
            "::RESPUESTA_CORRECTA:: (UNA letra A-E; si no se puede determinar, vacio)\n"
            "::RAZON_INCONSISTENCIA:: (texto; vacio si no aplica)\n"
            "::CONCEPTOS_PRINCIPALES:: (ids de proposiciones principales; separados por coma; vacio si ninguno)\n"
            "::CONCEPTOS_SECUNDARIOS:: (ids de proposiciones secundarias; separados por coma; vacio si ninguno)\n"
            "::METODO:: Nombre\n"
            "::PROPIEDADES:: (ids de proposiciones usados en este metodo; separados por coma; puede ser vacio)\n"
            "::DESARROLLO:: (LaTeX)\n"
            "::FIN_METODO::\n"
            "(Repite al menos 2 bloques ::METODO:: por problema si el problema es 'Bien Planteado'.)\n"
            "\n"
            "VALIDACION FINAL (antes de responder):\n"
            "- Devuelve exactamente un bloque por cada ID del lote.\n"
            "- No devuelvas IDs que no esten en el lote.\n"
            "- No agregues texto fuera del formato.\n"
        )

        catalogo = ""
        if db_name:
            try:
                db = DatabaseManager()
                conn = db.get_connection(db_name)
                try:
                    cur = conn.cursor()
                    cur.execute("SELECT id, nombre FROM temas ORDER BY nombre ASC LIMIT %s;", (int(temas_limit),))
                    temas = cur.fetchall()
                    cur.execute(
                        """
                        SELECT p.id, p.nombre, COALESCE(p.tipo,''), COALESCE(t.nombre,'')
                        FROM proposiciones_matematicas p
                        LEFT JOIN temas t ON t.id = p.tema_id
                        ORDER BY p.id DESC
                        LIMIT %s;
                        """,
                        (int(proposiciones_limit),),
                    )
                    props = cur.fetchall()
                finally:
                    conn.close()

                if temas:
                    catalogo += "\nCATALOGO TEMAS (id: nombre):\n"
                    catalogo += "\n".join([f"- {int(t[0])}: {t[1]}" for t in temas]) + "\n"
                if props:
                    catalogo += "\nCATALOGO PROPOSICIONES (id - [tipo] nombre (tema)):\n"
                    catalogo += "\n".join(
                        [f"- {int(r[0])} - [{r[2]}] {r[1]} ({r[3]})".rstrip() for r in props]
                    ) + "\n"
            except Exception:
                catalogo = ""

        cuerpo = "\n\n".join([f"--- ID {pid} ---\n{latex}" for pid, latex in lista_problemas])
        return header + catalogo + "\n\nPROBLEMAS DEL LOTE:\n" + cuerpo + "\n"

    def generar_prompt_clasificacion(
        self,
        lista_problemas: List[Tuple[int, str]],
        *,
        db_name: Optional[str] = None,
        temas_limit: int = 200,
        proposiciones_limit: int = 500,
    ) -> str:
        # Compatibilidad: nombre historico.
        return self.generar_prompt_clasificacion_bd(
            lista_problemas,
            db_name=db_name,
            temas_limit=temas_limit,
            proposiciones_limit=proposiciones_limit,
        )
