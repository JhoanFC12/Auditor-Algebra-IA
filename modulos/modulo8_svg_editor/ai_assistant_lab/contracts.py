from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import json


ALLOWED_OPERATIONS = {
    "set_global_style",
    "set_label_size",
    "mark_segment",
    "orthogonal_projection",
    "create_segment",
    "move_label",
    "shade_contour",
    "angle_label",
}


@dataclass(frozen=True)
class PointRef:
    name: str
    element_id: str | None
    x: float
    y: float
    label: str | None = None


@dataclass(frozen=True)
class SegmentRef:
    name: str
    element_id: str | None
    x1: float
    y1: float
    x2: float
    y2: float
    start_point: str | None = None
    end_point: str | None = None


@dataclass(frozen=True)
class SvgInventory:
    points: list[PointRef] = field(default_factory=list)
    segments: list[SegmentRef] = field(default_factory=list)


@dataclass(frozen=True)
class Operation:
    op: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanIssue:
    level: str
    message: str


@dataclass(frozen=True)
class AssistantPlan:
    operations: list[Operation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    issues: list[PlanIssue] = field(default_factory=list)
    needs_confirmation: bool = False

    def to_json(self) -> str:
        payload = {
            "operations": [{"op": item.op, "args": item.args} for item in self.operations],
            "notes": self.notes,
            "issues": [{"level": item.level, "message": item.message} for item in self.issues],
            "needs_confirmation": self.needs_confirmation,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)


@dataclass(frozen=True)
class ExecutionResult:
    svg_text: str
    applied: list[Operation] = field(default_factory=list)
    issues: list[PlanIssue] = field(default_factory=list)


def plan_from_json(raw_text: str) -> AssistantPlan:
    """Parse a model response into the conservative operation contract."""
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return AssistantPlan(
            issues=[PlanIssue("error", f"No se pudo leer JSON del plan: {exc}")],
            needs_confirmation=True,
        )

    operations: list[Operation] = []
    issues: list[PlanIssue] = []
    for index, item in enumerate(payload.get("operations", []), start=1):
        op_name = str(item.get("op", "")).strip()
        if op_name not in ALLOWED_OPERATIONS:
            issues.append(PlanIssue("error", f"Operacion no permitida en #{index}: {op_name}"))
            continue
        args = item.get("args", {})
        if not isinstance(args, dict):
            issues.append(PlanIssue("error", f"Argumentos invalidos en #{index}: {op_name}"))
            continue
        operations.append(Operation(op_name, args))

    notes = [str(note) for note in payload.get("notes", [])]
    needs_confirmation = bool(payload.get("needs_confirmation", bool(issues)))
    return AssistantPlan(
        operations=operations,
        notes=notes,
        issues=issues,
        needs_confirmation=needs_confirmation,
    )
