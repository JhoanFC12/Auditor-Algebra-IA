from __future__ import annotations

from collections.abc import Iterable
from xml.etree import ElementTree as ET
import math

from .contracts import PointRef, SegmentRef, SvgInventory


SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _float_attr(element: ET.Element, name: str) -> float | None:
    value = element.attrib.get(name)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _iter_elements(root: ET.Element) -> Iterable[ET.Element]:
    yield root
    yield from root.iter()


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _nearest_point_name(
    points: list[PointRef],
    x: float,
    y: float,
    *,
    tolerance: float = 2.5,
) -> str | None:
    nearest: PointRef | None = None
    best = tolerance
    for point in points:
        dist = _distance((x, y), (point.x, point.y))
        if dist <= best:
            nearest = point
            best = dist
    return nearest.name if nearest else None


def build_inventory(svg_text: str) -> SvgInventory:
    root = ET.fromstring(svg_text)
    points: list[PointRef] = []
    labels_by_point: dict[str, str] = {}

    for element in _iter_elements(root):
        tag = _local_name(element.tag)
        if tag in {"text", "path"}:
            point_id = element.attrib.get("data-point-id")
            text = element.attrib.get("data-text") or (element.text or "").strip()
            if point_id and text:
                labels_by_point[point_id] = text

    for element in _iter_elements(root):
        if _local_name(element.tag) != "circle":
            continue
        point_id = element.attrib.get("data-point-id")
        data_kind = element.attrib.get("data-kind", "")
        radius = _float_attr(element, "r") or 0.0
        if not point_id and data_kind != "point" and radius > 10:
            continue
        cx = _float_attr(element, "cx")
        cy = _float_attr(element, "cy")
        if cx is None or cy is None:
            continue
        name = point_id or element.attrib.get("id") or f"point_{len(points) + 1}"
        points.append(
            PointRef(
                name=name,
                element_id=element.attrib.get("id"),
                x=cx,
                y=cy,
                label=labels_by_point.get(name),
            )
        )

    segments: list[SegmentRef] = []
    for element in _iter_elements(root):
        if _local_name(element.tag) != "line":
            continue
        x1 = _float_attr(element, "x1")
        y1 = _float_attr(element, "y1")
        x2 = _float_attr(element, "x2")
        y2 = _float_attr(element, "y2")
        if None in {x1, y1, x2, y2}:
            continue
        start = _nearest_point_name(points, x1, y1)
        end = _nearest_point_name(points, x2, y2)
        segment_id = element.attrib.get("data-segment-id") or element.attrib.get("id")
        name = segment_id or (f"{start}{end}" if start and end else f"line_{len(segments) + 1}")
        segments.append(
            SegmentRef(
                name=name,
                element_id=element.attrib.get("id"),
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
                start_point=start,
                end_point=end,
            )
        )

    return SvgInventory(points=points, segments=segments)


def inventory_to_prompt_context(inventory: SvgInventory) -> str:
    point_lines = [
        f"- {point.name}: ({point.x:g}, {point.y:g})"
        + (f", label={point.label}" if point.label else "")
        for point in inventory.points
    ]
    segment_lines = [
        f"- {segment.name}: ({segment.x1:g}, {segment.y1:g}) -> ({segment.x2:g}, {segment.y2:g})"
        + (
            f", endpoints={segment.start_point}-{segment.end_point}"
            if segment.start_point or segment.end_point
            else ""
        )
        for segment in inventory.segments
    ]
    return "\n".join(
        [
            "Puntos detectados:",
            *(point_lines or ["- ninguno"]),
            "",
            "Segmentos detectados:",
            *(segment_lines or ["- ninguno"]),
        ]
    )
