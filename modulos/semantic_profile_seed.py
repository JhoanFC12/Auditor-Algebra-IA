from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any


TAG_RE = re.compile(r"\[\[([A-Za-z_]+)=([^\]]*)\]\]")
ITEM_NUMBER_RE = re.compile(r"\\item\s*\[\s*\\textbf\s*\{\s*([^}]*)\s*\}\s*\]", re.IGNORECASE)
LATEX_COMMAND_RE = re.compile(r"\\[a-zA-Z]+(?:\s*\{([^{}]*)\})?")
WHITESPACE_RE = re.compile(r"\s+")

GEOMETRY_KEYWORDS = {
    "angulo": "angulo",
    "angulos": "angulo",
    "triangulo": "triangulo",
    "triangulos": "triangulo",
    "recta": "recta",
    "rectas": "recta",
    "segmento": "segmento",
    "segmentos": "segmento",
    "circunferencia": "circunferencia",
    "circulo": "circulo",
    "poligono": "poligono",
    "poligonos": "poligono",
    "cuadrilatero": "cuadrilatero",
    "cuadrilateros": "cuadrilatero",
    "area": "area",
    "perimetro": "perimetro",
}

ALGEBRA_KEYWORDS = {
    "ecuacion": "ecuacion",
    "ecuaciones": "ecuacion",
    "funcion": "funcion",
    "funciones": "funcion",
    "polinomio": "polinomio",
    "polinomios": "polinomio",
    "logaritmo": "logaritmo",
    "logaritmos": "logaritmo",
    "matriz": "matriz",
    "matrices": "matriz",
    "determinante": "determinante",
}

ARITHMETIC_KEYWORDS = {
    "suma": "suma",
    "resta": "resta",
    "producto": "producto",
    "division": "division",
    "fraccion": "fraccion",
    "fracciones": "fraccion",
    "proporcion": "proporcion",
    "proporcionalidad": "proporcionalidad",
    "porcentaje": "porcentaje",
}


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _collapse(value: str) -> str:
    return WHITESPACE_RE.sub(" ", str(value or "")).strip()


def _clean_visible_text(value: str) -> str:
    text = str(value or "")
    text = ITEM_NUMBER_RE.sub("", text)
    text = TAG_RE.sub("", text)
    text = text.replace("£", " ").replace("æ", " ")
    text = text.replace("$", "")

    def replace_command(match: re.Match[str]) -> str:
        inner = match.group(1)
        return inner if inner is not None else " "

    text = LATEX_COMMAND_RE.sub(replace_command, text)
    text = re.sub(r"[{}]", " ", text)
    return _collapse(text)


def _split_statement_and_options(final_latex: str) -> tuple[str, dict[str, str]]:
    body = ITEM_NUMBER_RE.sub("", str(final_latex or ""))
    body = TAG_RE.sub("", body)
    option_start = re.search(r"(?:^|\s|£|æ)(A\))", body)
    statement = body[: option_start.start()].strip() if option_start else body.strip()
    options_text = body[option_start.start() :].strip() if option_start else ""
    options: dict[str, str] = {}
    if options_text:
        pieces = re.split(r"(?:^|\s|£|æ)+([A-E]\))", options_text)
        current = ""
        for piece in pieces:
            token = str(piece or "").strip()
            if not token:
                continue
            if re.fullmatch(r"[A-E]\)", token):
                current = token[0]
                options[current] = ""
                continue
            if current:
                options[current] = _collapse(f"{options[current]} {token}")
    return _clean_visible_text(statement), {key: _clean_visible_text(value) for key, value in options.items()}


def parse_final_problem_latex(final_latex: str) -> dict[str, Any]:
    """Extract stable fields from the final item format without semantic guessing."""
    raw = str(final_latex or "")
    tags = {key.lower(): _collapse(value) for key, value in TAG_RE.findall(raw)}
    number = ""
    match = ITEM_NUMBER_RE.search(raw)
    if match:
        number = _collapse(match.group(1)).rstrip(".")
    statement, options = _split_statement_and_options(raw)
    image_tags = []
    for key, value in tags.items():
        if key == "imagen" and value:
            image_tags.append(value)
    return {
        "number": number,
        "tags": tags,
        "course": tags.get("curso") or "",
        "topic": tags.get("tema") or "",
        "answer": tags.get("clave") or "",
        "image_tags": sorted(set(image_tags)),
        "statement": statement,
        "options": options,
        "is_continuation": "[CONT.]" in raw.upper(),
    }


def _keyword_hits(text: str, mapping: dict[str, str]) -> list[str]:
    normalized = _strip_accents(text).lower()
    hits: list[str] = []
    for raw, canonical in mapping.items():
        if re.search(rf"\b{re.escape(raw)}\b", normalized) and canonical not in hits:
            hits.append(canonical)
    return hits


def _detect_unknowns(statement: str) -> list[str]:
    normalized = _strip_accents(statement).lower()
    found: list[str] = []
    for pattern in (
        r"\b(?:calcule|calcular|halle|halla|determine|determinar)\s+[\"']?([a-z])\b",
        r"\b([a-z])\s*(?:=|\?)",
    ):
        for match in re.finditer(pattern, normalized):
            value = match.group(1)
            if value in {"a", "e", "o", "u"}:
                continue
            if value not in found:
                found.append(value)
    return found


def build_problem_semantic_seed(
    *,
    problem_id: str | int,
    final_latex: str,
    raw_ocr: str = "",
    course_hint: str = "",
    topic_hint: str = "",
    subtopic_hint: str = "",
    answer_hint: str = "",
    image_tags_hint: list[str] | None = None,
) -> dict[str, Any]:
    parsed = parse_final_problem_latex(final_latex)
    statement = parsed["statement"] or _clean_visible_text(raw_ocr) or "Problema pendiente de descripcion."
    course = parsed["course"] or _collapse(course_hint) or "SIN_CURSO"
    topic = parsed["topic"] or _collapse(topic_hint) or "SIN_TEMA"
    subtopic = _collapse(subtopic_hint)
    image_tags = sorted(set([*parsed["image_tags"], *[str(item).strip() for item in list(image_tags_hint or []) if str(item).strip()]]))
    answer = parsed["answer"] or _collapse(answer_hint)
    has_figure = bool(image_tags)
    modality = "merged_continuation" if parsed["is_continuation"] else ("text_image" if has_figure else "text_only")

    geometry = _keyword_hits(f"{course} {topic} {statement}", GEOMETRY_KEYWORDS)
    algebra = _keyword_hits(f"{course} {topic} {statement}", ALGEBRA_KEYWORDS)
    arithmetic = _keyword_hits(f"{course} {topic} {statement}", ARITHMETIC_KEYWORDS)
    concepts = []
    for value in [topic, *geometry, *algebra, *arithmetic]:
        normalized = _collapse(value)
        if normalized and normalized.upper() != "SIN_TEMA" and normalized not in concepts:
            concepts.append(normalized)
    skills = ["leer grafico"] if has_figure else []
    unknowns = _detect_unknowns(statement)
    search_keywords = []
    for value in [course, topic, *geometry, *algebra, *arithmetic, *unknowns]:
        normalized = _collapse(value).lower()
        if normalized and normalized not in {"sin_curso", "sin_tema"} and normalized not in search_keywords:
            search_keywords.append(normalized)
    embedding_text = _collapse(
        ". ".join(
            part
            for part in [
                course,
                topic,
                statement,
                f"Imagenes: {', '.join(image_tags)}" if has_figure else "",
            ]
            if part
        )
    )
    return {
        "schema_version": "problem_semantic_profile_v1",
        "problem_id": str(problem_id),
        "modality": modality,
        "course": course,
        "topic": topic,
        "subtopic": subtopic,
        "statement_summary": statement[:500],
        "concepts": concepts,
        "solution_methods": [],
        "solution_concepts": [],
        "alternative_solution_paths": [],
        "skills": skills,
        "objects": {
            "geometry": geometry,
            "algebra": algebra,
            "arithmetic": arithmetic,
        },
        "given_conditions": [statement],
        "unknowns": unknowns,
        "representation": {
            "embedding_text": embedding_text,
            "statement_embedding_text": statement,
            "figure_embedding_text": f"Problema con grafico {', '.join(image_tags)}" if has_figure else "",
            "solution_embedding_text": "",
            "search_keywords": search_keywords,
            "canonical_problem_type": "semilla_por_revisar",
        },
        "difficulty": {
            "estimated_level": 1,
            "scale": "1-5",
            "signals": {
                "steps_estimated": 0,
                "requires_graph_reading": has_figure,
                "requires_formula_memory": False,
                "requires_multi_case_reasoning": False,
                "requires_algebraic_manipulation": "none",
            },
            "reason": "Semilla deterministica; requiere perfilado semantico o revision humana.",
        },
        "evidence": {
            "uses_text": bool(statement),
            "uses_figure": has_figure,
            "figure_tags": image_tags,
            "source_fields": ["latex_rendered_item", "raw_ocr"] if raw_ocr else ["latex_rendered_item"],
            "parsed_number": parsed["number"],
            "parsed_answer": answer,
            "parsed_options": parsed["options"],
        },
        "review": {
            "status": "sin_revisar",
            "human_verified": False,
            "notes": "Perfil semilla creado sin inferencia semantica profunda.",
        },
    }


def write_seed_profile(path: Path, profile: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
