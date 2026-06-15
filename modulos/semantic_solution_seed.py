from __future__ import annotations

import json
import re
from typing import Any


IMAGE_EXT_RE = re.compile(r"\.(?:png|jpe?g|webp|gif|bmp|tiff?)$", re.IGNORECASE)
LATEX_SIGNAL_RE = re.compile(r"(\\[a-zA-Z]+|\$|=|\\frac|\\sqrt|\bpor\s+tanto\b|\bhallamos\b|\bdespej)", re.IGNORECASE)
WHITESPACE_RE = re.compile(r"\s+")


def _collapse(value: str) -> str:
    return WHITESPACE_RE.sub(" ", str(value or "")).strip()


def _is_likely_image_reference(value: str) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    if IMAGE_EXT_RE.search(raw):
        return True
    return ("\\" in raw or "/" in raw) and not LATEX_SIGNAL_RE.search(raw)


def _source_kind(author: str = "", source: str = "") -> str:
    text = f"{author} {source}".lower()
    if "human" in text or "humano" in text or "docente" in text:
        return "human_solution"
    if "gpt" in text or "model" in text or "ia" in text:
        return "model_draft"
    if "book" in text or "libro" in text or "solucionario" in text:
        return "book_solution"
    return "unknown"


def _method_from_text(method: str, solution_text: str) -> str:
    raw = _collapse(method)
    if raw:
        return raw
    text = solution_text.lower()
    if "despej" in text:
        return "despeje"
    if "\\frac" in text or "fraccion" in text:
        return "manipulacion de fracciones"
    if "angulo" in text or "triangulo" in text:
        return "relaciones geometricas"
    return "metodo por revisar"


def _canonical_solution_type(method: str) -> str:
    raw = re.sub(r"[^a-zA-Z0-9]+", "_", method.lower()).strip("_")
    return raw or "solucion_por_revisar"


def _keywords(*values: str) -> list[str]:
    out: list[str] = []
    for value in values:
        for token in re.findall(r"[^\W_]{3,}", str(value or "").lower(), flags=re.UNICODE):
            if token not in out:
                out.append(token)
    return out[:24]


def _entry_from_dict(entry: dict[str, Any], *, index: int, source: str) -> dict[str, Any] | None:
    method = _collapse(
        entry.get("metodo_nombre")
        or entry.get("metodo")
        or entry.get("method")
        or entry.get("name")
        or ""
    )
    solution_text = _collapse(
        entry.get("solucion_latex")
        or entry.get("desarrollo_latex")
        or entry.get("solution_latex")
        or entry.get("latex")
        or entry.get("text")
        or ""
    )
    if not solution_text or _is_likely_image_reference(solution_text):
        return None
    order = int(entry.get("orden") or entry.get("order") or index)
    return {
        "solution_path_id": f"sol_{max(order, 1):02d}",
        "method": method,
        "solution_text_latex": solution_text,
        "author": _collapse(entry.get("autor_ia") or entry.get("author") or entry.get("autor") or ""),
        "source": source,
        "properties": list(entry.get("propiedades") or entry.get("properties") or []),
    }


def solution_entries_from_payload(raw: Any, *, source: str = "problemas.soluciones") -> list[dict[str, Any]]:
    if raw is None:
        return []
    payload = raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            payload = json.loads(text)
        except Exception:
            if _is_likely_image_reference(text):
                return []
            payload = [{"solucion_latex": text}]
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, (list, tuple)):
        return []
    entries: list[dict[str, Any]] = []
    for index, item in enumerate(payload, start=1):
        if isinstance(item, dict):
            entry = _entry_from_dict(item, index=index, source=source)
        elif isinstance(item, str):
            entry = None if _is_likely_image_reference(item) else _entry_from_dict({"solucion_latex": item}, index=index, source=source)
        else:
            entry = None
        if entry is None:
            continue
        if entry["solution_path_id"] in {row["solution_path_id"] for row in entries}:
            entry["solution_path_id"] = f"sol_{len(entries) + 1:02d}"
        entries.append(entry)
    return entries


def solution_entry_from_table_row(row: dict[str, Any]) -> dict[str, Any] | None:
    return _entry_from_dict(
        {
            "orden": row.get("orden"),
            "metodo_nombre": row.get("metodo_nombre"),
            "solucion_latex": row.get("solucion_latex"),
            "autor_ia": row.get("autor_ia"),
            "propiedades": row.get("propiedades") or [],
        },
        index=int(row.get("orden") or 1),
        source="soluciones",
    )


def build_solution_semantic_seed(
    *,
    problem_id: str | int,
    entry: dict[str, Any],
    problem_source: str = "",
    figure_tags: list[str] | None = None,
) -> dict[str, Any]:
    text = _collapse(entry.get("solution_text_latex") or "")
    if not text:
        raise ValueError("solution_text_latex requerido.")
    method = _method_from_text(str(entry.get("method") or ""), text)
    tags = [str(item).strip() for item in list(figure_tags or []) if str(item).strip()]
    source_label = str(entry.get("source") or "")
    author = str(entry.get("author") or "")
    return {
        "schema_version": "solution_semantic_profile_v1",
        "problem_id": str(problem_id),
        "solution_path_id": str(entry.get("solution_path_id") or "sol_01"),
        "source": {
            "kind": _source_kind(author, source_label),
            "problem_source": problem_source,
            "solution_source": source_label,
            "author": author,
        },
        "method": method,
        "concepts_used": [],
        "skills_used": [],
        "steps_summary": [text[:500]],
        "properties_used": [
            {
                "name": str(item[1] if isinstance(item, (list, tuple)) and len(item) > 1 else item).strip(),
                "role": "supporting",
                "notes": "Propiedad heredada del registro de solucion; requiere revision.",
            }
            for item in list(entry.get("properties") or [])
            if str(item[1] if isinstance(item, (list, tuple)) and len(item) > 1 else item).strip()
        ],
        "representation": {
            "embedding_text": _collapse(f"{method}. {text}"),
            "canonical_solution_type": _canonical_solution_type(method),
            "search_keywords": _keywords(method, text),
        },
        "evidence": {
            "solution_text_latex": text,
            "uses_figure": bool(tags),
            "figure_tags": sorted(set(tags)),
        },
        "review": {
            "status": "sin_revisar",
            "human_verified": False,
            "notes": "Perfil semilla creado desde solucion existente; requiere revision semantica.",
        },
    }
