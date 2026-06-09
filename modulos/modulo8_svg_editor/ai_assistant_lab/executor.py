from __future__ import annotations

from xml.etree import ElementTree as ET
import math

from .contracts import AssistantPlan, ExecutionResult, Operation, PlanIssue, PointRef, SegmentRef
from .inventory import build_inventory


SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[1] if "}" in tag else tag


def _fmt(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _same_name(left: str | None, right: str) -> bool:
    return bool(left) and left.upper() == right.upper()


def _find_point(points: list[PointRef], name: str) -> PointRef | None:
    for point in points:
        if _same_name(point.name, name) or _same_name(point.label, name):
            return point
    return None


def _find_segment(segments: list[SegmentRef], name: str) -> SegmentRef | None:
    for segment in segments:
        if _same_name(segment.name, name) or _same_name(segment.element_id, name):
            return segment
    return None


def _project_point_to_segment(point: PointRef, segment: SegmentRef) -> tuple[float, float]:
    ax, ay = segment.x1, segment.y1
    bx, by = segment.x2, segment.y2
    vx, vy = bx - ax, by - ay
    denom = vx * vx + vy * vy
    if denom <= 1e-9:
        return ax, ay
    t = ((point.x - ax) * vx + (point.y - ay) * vy) / denom
    return ax + t * vx, ay + t * vy


def _next_id(root: ET.Element, prefix: str) -> str:
    existing = {element.attrib.get("id") for element in root.iter()}
    index = 1
    while f"{prefix}-{index}" in existing:
        index += 1
    return f"{prefix}-{index}"


def _append_line(
    root: ET.Element,
    *,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    data_kind: str,
    stroke_width: float = 2.0,
    dashed: bool = False,
) -> ET.Element:
    attrib = {
        "id": _next_id(root, "ai-line"),
        "x1": _fmt(x1),
        "y1": _fmt(y1),
        "x2": _fmt(x2),
        "y2": _fmt(y2),
        "stroke": "#000000",
        "stroke-width": _fmt(stroke_width),
        "fill": "none",
        "data-kind": data_kind,
    }
    if dashed:
        attrib["stroke-dasharray"] = "4,3"
        attrib["style"] = "stroke-dasharray:4,3"
    return ET.SubElement(root, f"{{{SVG_NS}}}line", attrib)


def _append_point(root: ET.Element, *, x: float, y: float, name: str) -> ET.Element:
    return ET.SubElement(
        root,
        f"{{{SVG_NS}}}circle",
        {
            "id": _next_id(root, "ai-point"),
            "data-kind": "point",
            "data-point-id": name,
            "cx": _fmt(x),
            "cy": _fmt(y),
            "r": "5",
            "fill": "#000000",
            "stroke": "none",
        },
    )


class ExperimentalSvgExecutor:
    """Applies safe lab operations to a copy of SVG text."""

    def execute(self, svg_text: str, plan: AssistantPlan) -> ExecutionResult:
        root = ET.fromstring(svg_text)
        applied: list[Operation] = []
        issues: list[PlanIssue] = list(plan.issues)

        for operation in plan.operations:
            if operation.op == "set_global_style":
                self._set_global_style(root, operation)
                applied.append(operation)
            elif operation.op == "set_label_size":
                self._set_label_size(root, operation)
                applied.append(operation)
            elif operation.op == "mark_segment":
                ok, issue = self._mark_segment(root, operation)
                if ok:
                    applied.append(operation)
                elif issue:
                    issues.append(issue)
            elif operation.op == "orthogonal_projection":
                ok, new_issues = self._orthogonal_projection(root, operation)
                issues.extend(new_issues)
                if ok:
                    applied.append(operation)
            elif operation.op == "angle_label":
                ok, issue = self._angle_label(root, operation)
                if ok:
                    applied.append(operation)
                elif issue:
                    issues.append(issue)
            else:
                issues.append(PlanIssue("warning", f"Operacion aun no implementada en laboratorio: {operation.op}"))

        return ExecutionResult(
            svg_text=ET.tostring(root, encoding="unicode"),
            applied=applied,
            issues=issues,
        )

    def _set_global_style(self, root: ET.Element, operation: Operation) -> None:
        stroke_width = operation.args.get("stroke_width")
        if stroke_width is None:
            return
        for element in root.iter():
            if _local_name(element.tag) in {"line", "path", "circle", "rect", "polygon", "polyline"}:
                element.set("stroke-width", _fmt(float(stroke_width)))
                style = element.attrib.get("style", "")
                if "stroke-width" in style:
                    parts = [part for part in style.split(";") if not part.strip().startswith("stroke-width")]
                    parts.append(f"stroke-width:{_fmt(float(stroke_width))}")
                    element.set("style", "; ".join(part for part in parts if part.strip()))

    def _set_label_size(self, root: ET.Element, operation: Operation) -> None:
        font_size = operation.args.get("font_size")
        if font_size is None:
            return
        for element in root.iter():
            if _local_name(element.tag) in {"text", "path"} and (
                element.attrib.get("data-point-id") or element.attrib.get("data-text")
            ):
                element.set("font-size", _fmt(float(font_size)))
                element.set("data-font-size", _fmt(float(font_size)))

    def _mark_segment(self, root: ET.Element, operation: Operation) -> tuple[bool, PlanIssue | None]:
        segment_name = str(operation.args.get("segment", "")).strip()
        inventory = build_inventory(ET.tostring(root, encoding="unicode"))
        segment = _find_segment(inventory.segments, segment_name)
        if not segment:
            return False, PlanIssue("warning", f"No encontre el segmento para marcar: {segment_name}")
        for element in root.iter():
            if _local_name(element.tag) != "line":
                continue
            if _same_name(element.attrib.get("data-segment-id"), segment.name) or _same_name(
                element.attrib.get("id"), segment.element_id or segment.name
            ):
                element.set("data-mark-style", str(operation.args.get("style", "tick")))
                element.set("data-mark-count", str(operation.args.get("count", 1)))
                return True, None
        return False, PlanIssue("warning", f"El segmento existe pero no pude ubicar su elemento: {segment_name}")

    def _angle_label(self, root: ET.Element, operation: Operation) -> tuple[bool, PlanIssue | None]:
        angle = str(operation.args.get("angle", "")).strip().upper()
        label = str(operation.args.get("label", "")).strip()
        if len(angle) != 3:
            return False, PlanIssue("warning", f"Angulo invalido: {angle}")
        if not label:
            return False, PlanIssue("warning", f"No encontre etiqueta para el angulo {angle}.")

        inventory = build_inventory(ET.tostring(root, encoding="unicode"))
        p1 = _find_point(inventory.points, angle[0])
        vertex = _find_point(inventory.points, angle[1])
        p2 = _find_point(inventory.points, angle[2])
        if not p1 or not vertex or not p2:
            return False, PlanIssue("warning", f"No encontre los puntos necesarios para el angulo {angle}.")

        x, y = self._angle_label_position(p1, vertex, p2, offset=float(operation.args.get("offset", 42)))
        ET.SubElement(
            root,
            f"{{{SVG_NS}}}text",
            {
                "id": _next_id(root, "ai-angle-label"),
                "data-kind": "angle-label",
                "data-angle-id": angle,
                "x": _fmt(x),
                "y": _fmt(y),
                "fill": "#000000",
                "font-size": str(operation.args.get("font_size", 35)),
                "font-family": "Cambria Math, Times New Roman, serif",
                "text-anchor": "middle",
            },
        ).text = label
        return True, None

    def _angle_label_position(
        self,
        p1: PointRef,
        vertex: PointRef,
        p2: PointRef,
        *,
        offset: float,
    ) -> tuple[float, float]:
        v1x, v1y = p1.x - vertex.x, p1.y - vertex.y
        v2x, v2y = p2.x - vertex.x, p2.y - vertex.y
        n1 = math.hypot(v1x, v1y) or 1.0
        n2 = math.hypot(v2x, v2y) or 1.0
        u1x, u1y = v1x / n1, v1y / n1
        u2x, u2y = v2x / n2, v2y / n2
        bx, by = u1x + u2x, u1y + u2y
        bn = math.hypot(bx, by)
        if bn <= 1e-6:
            bx, by = -u1y, u1x
            bn = math.hypot(bx, by) or 1.0
        return vertex.x + (bx / bn) * offset, vertex.y + (by / bn) * offset

    def _orthogonal_projection(self, root: ET.Element, operation: Operation) -> tuple[bool, list[PlanIssue]]:
        inventory = build_inventory(ET.tostring(root, encoding="unicode"))
        source_name = str(operation.args.get("source", "")).strip()
        target_name = str(operation.args.get("target", "")).strip()
        target = _find_segment(inventory.segments, target_name)
        if not target:
            return False, [PlanIssue("warning", f"No encontre la recta/segmento destino: {target_name}")]

        point_source = _find_point(inventory.points, source_name)
        if point_source:
            foot_x, foot_y = _project_point_to_segment(point_source, target)
            foot_name = f"{point_source.name}'"
            _append_point(root, x=foot_x, y=foot_y, name=foot_name)
            if operation.args.get("helper", True):
                _append_line(
                    root,
                    x1=point_source.x,
                    y1=point_source.y,
                    x2=foot_x,
                    y2=foot_y,
                    data_kind="projection-helper",
                    dashed=True,
                )
            return True, []

        source_segment = _find_segment(inventory.segments, source_name)
        if not source_segment:
            return False, [PlanIssue("warning", f"No encontre el punto o segmento origen: {source_name}")]

        start = PointRef("tmp_start", None, source_segment.x1, source_segment.y1)
        end = PointRef("tmp_end", None, source_segment.x2, source_segment.y2)
        f1x, f1y = _project_point_to_segment(start, target)
        f2x, f2y = _project_point_to_segment(end, target)
        if math.hypot(f2x - f1x, f2y - f1y) <= 1e-6:
            return False, [PlanIssue("warning", "La proyeccion del segmento se redujo a un punto.")]
        _append_line(root, x1=f1x, y1=f1y, x2=f2x, y2=f2y, data_kind="projection-segment", stroke_width=2.5)
        if operation.args.get("helper", True):
            _append_line(root, x1=start.x, y1=start.y, x2=f1x, y2=f1y, data_kind="projection-helper", dashed=True)
            _append_line(root, x1=end.x, y1=end.y, x2=f2x, y2=f2y, data_kind="projection-helper", dashed=True)
        return True, []
