from __future__ import annotations

from collections.abc import Callable
import re
import unicodedata

from .contracts import AssistantPlan, Operation, PlanIssue, SvgInventory, plan_from_json
from .inventory import inventory_to_prompt_context


def _plain_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    plain = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return (
        plain.replace("?ngulo", "angulo")
        .replace("?ngulos", "angulos")
        .replace("tama?o", "tamano")
        .replace("proyecci?n", "proyeccion")
    )


class RuleBasedPlanner:
    """Tiny local planner for lab tests before wiring a real model."""

    def plan(self, instruction: str, inventory: SvgInventory) -> AssistantPlan:
        text = instruction.strip()
        plain = _plain_text(text)
        lower = plain.lower()
        operations: list[Operation] = []
        notes: list[str] = []
        issues: list[PlanIssue] = []

        projection = self._projection_from_text(plain)
        if projection:
            source, target = projection
            operations.append(
                Operation(
                    "orthogonal_projection",
                    {"source": source, "target": target, "helper": True, "create_foot": True},
                )
            )
            notes.append(f"Detecte una proyeccion ortogonal: {source} sobre {target}.")

        angle_label = self._angle_label_from_text(plain)
        if angle_label:
            angle, label, label_only = angle_label
            operations.append(
                Operation(
                    "angle_label",
                    {
                        "angle": angle,
                        "label": label,
                        "label_only": label_only,
                        "show_arc": not label_only,
                    },
                )
            )
            notes.append(f"Detecte etiqueta de angulo: {angle} = {label}.")

        label_size = self._number_after(lower, ("tamano de letras", "tamaño de letras", "letras"))
        if label_size:
            operations.append(Operation("set_label_size", {"font_size": label_size}))

        stroke_width = self._number_after(lower, ("grosor", "linea", "lineas", "trazo"))
        if stroke_width:
            operations.append(Operation("set_global_style", {"stroke_width": stroke_width}))

        if "marca" in lower or "iguales" in lower or "congruentes" in lower:
            segment_names = self._known_segment_mentions(plain, inventory)
            if segment_names:
                for index, segment_name in enumerate(segment_names, start=1):
                    operations.append(
                        Operation(
                            "mark_segment",
                            {
                                "segment": segment_name,
                                "style": "tick",
                                "count": index,
                            },
                        )
                    )

        if not operations:
            issues.append(
                PlanIssue(
                    "warning",
                    "No pude convertir la instruccion en operaciones seguras dentro del laboratorio.",
                )
            )

        return AssistantPlan(operations=operations, notes=notes, issues=issues)

    def _projection_from_text(self, text: str) -> tuple[str, str] | None:
        patterns = [
            r"(?:proyecta|proyeccion|altura)\s+(?:desde\s+|del\s+|de\s+)?(?P<src>[A-Za-z][A-Za-z0-9_]*)\s+(?:sobre|a|hacia)\s+(?:la\s+recta\s+|el\s+segmento\s+)?(?P<target>[A-Za-z][A-Za-z0-9_]*)",
            r"(?:desde\s+)?(?P<src>[A-Za-z][A-Za-z0-9_]*)\s+(?:proyectado|proyectada)\s+(?:sobre|a)\s+(?P<target>[A-Za-z][A-Za-z0-9_]*)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group("src").upper(), match.group("target").upper()
        return None

    def _angle_label_from_text(self, text: str) -> tuple[str, str, bool] | None:
        angle_matches: list[str] = []
        for pattern in (
            r"angulo\s+(?P<angle>[A-Za-z]{3})",
            r"angulo\s+(?:en|de|del)\s+(?P<angle>[A-Za-z]{3})",
            r"\ben\s+(?P<angle>[A-Za-z]{3})\b",
        ):
            angle_matches.extend(re.findall(pattern, text, flags=re.IGNORECASE))
        valid_angles = [item.upper() for item in angle_matches if item.upper() not in {"DEL", "LOS", "LAS", "QUE"}]
        if not valid_angles:
            return None
        degree_match = re.search(
            r"(?P<value>\d+(?:[.,]\d+)?)\s*(?:grados|grado|°|\\circ)",
            text,
            flags=re.IGNORECASE,
        )
        value = degree_match.group("value").replace(",", ".") if degree_match else ""
        label = f"{value}^\\circ" if value else "x"
        label_only = any(
            phrase in text.lower()
            for phrase in (
                "solamente etiqueta",
                "solo etiqueta",
                "sin estilo",
                "sin arco",
                "nada mas etiqueta",
            )
        )
        return valid_angles[-1], label, label_only

    def _known_segment_mentions(self, text: str, inventory: SvgInventory) -> list[str]:
        found: list[str] = []
        upper_text = text.upper()
        for segment in inventory.segments:
            if segment.name.upper() in upper_text and segment.name not in found:
                found.append(segment.name)
        if found:
            return found
        return re.findall(r"\b[A-Z]{2,3}\b", upper_text)

    def _number_after(self, text: str, keywords: tuple[str, ...]) -> float | None:
        for keyword in keywords:
            pattern = rf"{re.escape(keyword)}\D*(?P<num>\d+(?:[.,]\d+)?)"
            match = re.search(pattern, text)
            if match:
                return float(match.group("num").replace(",", "."))
        return None


class LLMPlanner:
    """Provider-neutral planner. The caller injects the model call later."""

    def __init__(self, completion_fn: Callable[[str], str]) -> None:
        self._completion_fn = completion_fn

    def plan(self, instruction: str, inventory: SvgInventory) -> AssistantPlan:
        prompt = self.build_prompt(instruction, inventory)
        return plan_from_json(self._completion_fn(prompt))

    def build_prompt(self, instruction: str, inventory: SvgInventory) -> str:
        return f"""Eres un planificador para un editor SVG geometrico.
No devuelvas SVG. Devuelve solo JSON valido.

Operaciones permitidas:
- set_global_style: {{"stroke_width": numero opcional}}
- set_label_size: {{"font_size": numero}}
- mark_segment: {{"segment": "AB", "style": "tick|points|rect", "count": 1}}
- orthogonal_projection: {{"source": "A o AB", "target": "BC", "helper": true, "create_foot": true}}
- create_segment: {{"from": "A", "to": "B"}}
- move_label: {{"point": "A", "dir": "N|S|E|O|NE|NO|SE|SO", "offset": numero}}
- shade_contour: {{"segments": ["AB", "BC", "CA"], "opacity": 0.15}}
- angle_label: {{"angle": "ABC", "label": "60^\\circ", "label_only": true, "show_arc": false}}

Formato obligatorio:
{{
  "operations": [{{"op": "...", "args": {{...}}}}],
  "notes": [],
  "needs_confirmation": false
}}

Inventario actual:
{inventory_to_prompt_context(inventory)}

Instruccion del usuario:
{instruction}
"""
