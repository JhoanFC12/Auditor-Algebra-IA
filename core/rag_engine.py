"""
MOTOR RAG (Generación Aumentada por Recuperación)
Genera prompts focalizados en 2 fases, asegurando estabilidad.
"""
from typing import List, Dict, Any

# =============================================================================
# PROMPTS MAESTROS
# =============================================================================

# FASE A: Prompt para la Clasificación (Busca IDs)
def get_prompt_fase_a_clasificacion(enunciado: str, catalogo_nombres: str) -> str:
    """Genera el prompt simple para obtener solo los IDs de reglas sugeridas."""
    return f"""Eres un motor de clasificación de álgebra. Tu única tarea es analizar el problema y sugerir los IDs de 5 a 8 reglas más relevantes que serían NECESARIAS para resolverlo.

--- CATÁLOGO DISPONIBLE (SOLO NOMBRES) ---
{catalogo_nombres}
---

PROBLEMA:
{enunciado}

REGLAS DE SALIDA:
- SOLO responde con un objeto JSON.
- Los IDs DEBEN ser números enteros (1 a 49).
- Formato de Salida: {{"reglas_sugeridas_ids": [1, 5, 47, ...]}}
"""

# FASE B: Prompt para la Generación de Soluciones (Recibe las fórmulas completas)
def get_prompt_fase_b_generacion(problema: ProblemaModel, reglas_completas: List[Dict]) -> str:
    """Genera el prompt complejo para la generación de la solución con contexto focalizado."""
    
    # 1. Construir el Catálogo Enfocado
    reglas_texto = ""
    for r in reglas_completas:
        reglas_texto += f"[ID: {r['id']}] {r['nombre']}:\n"
        reglas_texto += f"  Condición: {r['condiciones_dominio'] or 'N/A'}\n"
        reglas_texto += f"  Fórmula: {r['enunciado_formal_latex']}\n\n"

    # 2. Definir el nuevo formato de respuesta (Bloques de Texto)
    return f"""Eres un Profesor Matemático y Auditor de Soluciones.
Tu tarea es resolver el problema (ID: {problema.id}) utilizando ESTRICTAMENTE las reglas proporcionadas en el 'CATÁLOGO ENFOCADO'.

--- CATÁLOGO ENFOCADO (REGLAS ACEPTADAS) ---
{reglas_texto}
---

PROBLEMA A RESOLVER (ID: {problema.id}):
{problema.enunciado_latex}

INSTRUCCIONES CLAVE:
1. **MÉTODOS:** Genera al menos UNA solución, y hasta DOS soluciones alternativas si es lógico.
2. **CITA OBLIGATORIA:** Cada paso debe citar una regla del CATÁLOGO ENFOCADO: [Regla ID: 5] ($a^m a^n = a^{{m+n}}$).
3. **LATEX:** Escribe LaTeX puro (sin escapar barras) usando $...$ o $$.

ESTRUCTURA DE RESPUESTA REQUERIDA (BLOQUES DE TEXTO):
---------------------------------------------------
[[PROBLEMA_ID: {problema.id}]]
[[TEMA: Nombre del Tema Principal]]
[[RESPUESTA_CLAVE: Letra de la respuesta (A, B, C...)]]

[[SOLUCION_1_METODO: Factorización]]
[[SOLUCION_1_NIVEL: 2]]
[[SOLUCION_1_REGLAS: 1, 4, 15]]  <-- IDs usados en este método
[[SOLUCION_1_INICIO]]
Aquí va el LaTeX detallado del primer método.
[[SOLUCION_1_FIN]]

[[SOLUCION_2_METODO: Sustitución]]
[[SOLUCION_2_NIVEL: 3]]
[[SOLUCION_2_REGLAS: 5, 47]]
[[SOLUCION_2_INICIO]]
Aquí va el LaTeX detallado del segundo método.
[[SOLUCION_2_FIN]]
---------------------------------------------------
"""