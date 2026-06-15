from __future__ import annotations

import re
from pathlib import Path
from typing import Any


GEOMETRY_RE = re.compile(r"\b(geo|geometr|triang|angulo|circunferencia|poligono|cuadril)", re.IGNORECASE)


def normalize_figure_tag(value: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    name = raw.rsplit("/", 1)[-1]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return name.strip()


def figure_type_from_context(*, course: str = "", topic: str = "", asset_path: str = "") -> str:
    context = " ".join(str(item or "") for item in (course, topic, asset_path))
    if GEOMETRY_RE.search(context):
        return "geometria_plana"
    return "otro"


def build_figure_semantic_seed(
    *,
    problem_id: str | int,
    figure_tag: str,
    course: str = "",
    topic: str = "",
    asset_path: str = "",
) -> dict[str, Any]:
    tag = normalize_figure_tag(figure_tag)
    if not tag:
        raise ValueError("figure_tag requerido para crear perfil de figura.")
    asset_name = Path(str(asset_path or "")).name
    evidence_source = "db_image_binding" if asset_path else "figure_tag"
    warnings = [
        "Perfil semilla: no describe puntos, segmentos ni medidas hasta que un modelo visual o humano revise la imagen.",
        "No usar este perfil para inferir propiedades geometricas todavia.",
    ]
    if asset_name:
        warnings.append(f"Imagen vinculada: {asset_name}.")
    return {
        "schema_version": "geometry_figure_description_v1",
        "source_record_id": str(problem_id),
        "figure_tag": tag,
        "figure_type": figure_type_from_context(course=course, topic=topic, asset_path=asset_path),
        "visual_text": {
            "points": [],
            "measure_labels": [],
            "other_labels": [],
        },
        "primitives": {
            "points": [],
            "segments": [],
            "circles": [],
            "arcs": [],
        },
        "formalgeo_candidate": {
            "construction_cdl": [],
            "condition_cdl": [],
            "goal_cdl": [],
        },
        "evidence": [
            {
                "predicate": f"HasFigure({tag})",
                "source": evidence_source,
                "confidence": 1.0,
            }
        ],
        "warnings": warnings,
    }


def figure_embedding_text(profile: dict[str, Any]) -> str:
    figure_type = str(profile.get("figure_type") or "")
    figure_tag = str(profile.get("figure_tag") or "")
    warnings = " ".join(str(item) for item in list(profile.get("warnings") or []) if str(item).strip())
    return " ".join(part for part in [figure_type, figure_tag, warnings] if part).strip()
