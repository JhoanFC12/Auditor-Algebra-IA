from __future__ import annotations

import json
from typing import Any, Iterable

from .tokens import SEP_LINE


SYSTEM_PROMPT_EXTRACT = (
    "Eres un extractor estricto de problemas matematicos escaneados. "
    "Devuelves SOLO JSON valido."
)


def build_extract_prompt(*, curso: str, tema: str, start_n: int) -> str:
    return (
        "Extrae EXACTAMENTE los problemas matematicos de la imagen y devuelve SOLO JSON valido.\\n"
        "Prohibido responder markdown o texto fuera del JSON.\\n"
        "No uses Unicode matematico crudo (ej: \u00b0, \u2220, \u2264, \u03b8): usa comandos LaTeX compilables.\\n"
        "Esquema obligatorio:\\n"
        "{\\n"
        '  "items": [\\n'
        "    {\\n"
        '      "schema": "ScanItemJSON-v1",\\n'
        '      "n": <int>,\\n'
        '      "curso": "<texto>",\\n'
        '      "tema": "<texto>",\\n'
        '      "has_figure": <true|false>,\\n'
        '      "figure_tag": "img-n" o "",\\n'
        f'      "statement": "una sola linea; usa {SEP_LINE} para saltos internos; ecuaciones SIEMPRE en $...$ (nunca {SEP_LINE}...{SEP_LINE}); simbolos matematicos en LaTeX",\\n'
        '      "options": {"A":"<texto>","B":"<texto>","C":"<texto>","D":"<texto>","E":"<texto>"},\\n'
        '      "needs_review": <true|false>\\n'
        "    }\\n"
        "  ]\\n"
        "}\\n"
        "Reglas:\\n"
        "1) NO inventes texto.\\n"
        "2) options siempre tiene A-E; si falta una opcion usa '...'.\\n"
        "3) has_figure=true solo si hay figura/diagrama asociado al enunciado.\\n"
        "4) Si NO hay figura, has_figure=false y figure_tag=''.\\n"
        "5) Si hay figura, figure_tag='img-n'.\\n"
        "6) Si hay duda o ilegible, usa '' y needs_review=true.\\n"
        "6.1) Para geometria usa \\angle y ^\\circ (no caracteres Unicode).\\n"
        f"7) Usa curso='{curso}' y tema='{tema}' por defecto si no aparecen en imagen.\\n"
        f"8) Si n no es detectable, usa secuencia desde {max(1, int(start_n))}.\\n"
        "Salida final: SOLO JSON valido."
    )


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


def build_correction_prompt(
    *,
    bad_item: Any,
    errors: Iterable[str],
    curso: str,
    tema: str,
) -> str:
    joined = "\n".join(f"- {e}" for e in errors) or "- formato_invalido"
    return (
        "Corrige el siguiente item para que cumpla ScanItemJSON-v1.\\n"
        "Devuelve SOLO JSON del item (no arreglo, no texto extra).\\n"
        "Usa sintaxis LaTeX compilable para simbolos matematicos y ecuaciones en $...$.\\n"
        f"Curso por defecto: {curso}\\n"
        f"Tema por defecto: {tema}\\n"
        "Errores detectados:\\n"
        f"{joined}\\n"
        "Item actual:\\n"
        f"{_safe_json(bad_item)}"
    )


def build_parse_retry_prompt(
    *,
    raw_output: str,
    errors: Iterable[str],
    curso: str,
    tema: str,
    start_n: int,
) -> str:
    joined = "\n".join(f"- {e}" for e in errors) or "- salida_no_json"
    return (
        "Tu salida previa no se pudo parsear como JSON valido.\\n"
        "Reconstruye SOLO JSON valido con el esquema ScanItemJSON-v1, usando la imagen como fuente principal.\\n"
        "No uses Unicode matematico crudo: usa comandos LaTeX y $...$ para ecuaciones.\\n"
        "Devuelve un objeto con clave 'items'.\\n"
        f"Curso por defecto: {curso}\\n"
        f"Tema por defecto: {tema}\\n"
        f"Numeracion fallback desde: {max(1, int(start_n))}\\n"
        "Errores detectados:\\n"
        f"{joined}\\n"
        "Salida previa (referencia):\\n"
        f"{raw_output.strip()[:4000]}"
    )
