from __future__ import annotations

from io import BytesIO
from xml.etree import ElementTree as ET
import re

from PIL import Image


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[1] if "}" in tag else tag


def _float_attr(element: ET.Element, name: str, default: float = 0.0) -> float:
    value = element.attrib.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _color(value: str | None, default: str = "black") -> str:
    if not value or value == "none":
        return "none"
    return value


def _stroke_width(element: ET.Element) -> float:
    width = element.attrib.get("stroke-width")
    if width:
        try:
            return float(width)
        except ValueError:
            pass
    style = element.attrib.get("style", "")
    match = re.search(r"stroke-width\s*:\s*([0-9.]+)", style)
    if match:
        return float(match.group(1))
    return 1.2


def _viewbox(root: ET.Element) -> tuple[float, float, float, float]:
    raw = root.attrib.get("viewBox")
    if raw:
        parts = [float(item) for item in re.split(r"[\s,]+", raw.strip()) if item]
        if len(parts) == 4:
            return parts[0], parts[1], parts[2], parts[3]
    width = _float_attr(root, "width", 800.0)
    height = _float_attr(root, "height", 600.0)
    return 0.0, 0.0, width, height


def _points(raw: str) -> list[tuple[float, float]]:
    values = [float(item) for item in re.split(r"[\s,]+", raw.strip()) if item]
    return list(zip(values[0::2], values[1::2]))


def render_svg_preview(svg_text: str, *, max_width: int = 760, max_height: int = 560) -> Image.Image:
    """Render a practical SVG preview without touching the production editor.

    This is intentionally lightweight. It covers the primitives most used by the
    geometry editor and draws path labels through their stored `data-text`.
    """
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from matplotlib.patches import Circle, Polygon, Rectangle

    root = ET.fromstring(svg_text)
    min_x, min_y, width, height = _viewbox(root)
    scale = min(max_width / max(width, 1.0), max_height / max(height, 1.0))
    out_w = max(240, int(width * scale))
    out_h = max(180, int(height * scale))

    fig = plt.figure(figsize=(out_w / 100, out_h / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(min_x, min_x + width)
    ax.set_ylim(min_y + height, min_y)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")

    for element in root.iter():
        tag = _local_name(element.tag)
        if tag == "svg":
            continue

        stroke = _color(element.attrib.get("stroke"), "black")
        fill = _color(element.attrib.get("fill"), "none")
        line_width = _stroke_width(element)
        dash = element.attrib.get("stroke-dasharray") or ""

        if tag == "rect":
            x = _float_attr(element, "x")
            y = _float_attr(element, "y")
            w = _float_attr(element, "width")
            h = _float_attr(element, "height")
            patch = Rectangle(
                (x, y),
                w,
                h,
                facecolor=fill if fill != "none" else "none",
                edgecolor=stroke if stroke != "none" else "none",
                linewidth=line_width,
            )
            ax.add_patch(patch)
        elif tag == "line":
            x1 = _float_attr(element, "x1")
            y1 = _float_attr(element, "y1")
            x2 = _float_attr(element, "x2")
            y2 = _float_attr(element, "y2")
            (line,) = ax.plot(
                [x1, x2],
                [y1, y2],
                color=stroke if stroke != "none" else "black",
                linewidth=line_width,
                solid_capstyle="round",
            )
            if dash:
                line.set_dashes([4, 3])
        elif tag == "circle":
            cx = _float_attr(element, "cx")
            cy = _float_attr(element, "cy")
            radius = _float_attr(element, "r", 3.0)
            patch = Circle(
                (cx, cy),
                radius,
                facecolor=fill if fill != "none" else "none",
                edgecolor=stroke if stroke != "none" else "none",
                linewidth=line_width,
            )
            ax.add_patch(patch)
        elif tag in {"polygon", "polyline"}:
            pts = _points(element.attrib.get("points", ""))
            if not pts:
                continue
            if tag == "polygon":
                patch = Polygon(
                    pts,
                    closed=True,
                    facecolor=fill if fill != "none" else "none",
                    edgecolor=stroke if stroke != "none" else "black",
                    linewidth=line_width,
                )
                ax.add_patch(patch)
            else:
                xs, ys = zip(*pts)
                ax.plot(xs, ys, color=stroke if stroke != "none" else "black", linewidth=line_width)
        elif tag == "text":
            text = element.text or ""
            if not text.strip():
                continue
            ax.text(
                _float_attr(element, "x"),
                _float_attr(element, "y"),
                text,
                color=fill if fill != "none" else "black",
                fontsize=_float_attr(element, "font-size", 18.0),
                fontfamily="serif",
            )
        elif tag == "path" and element.attrib.get("data-text"):
            ax.text(
                _float_attr(element, "data-x"),
                _float_attr(element, "data-y"),
                element.attrib["data-text"],
                color=fill if fill != "none" else "black",
                fontsize=_float_attr(element, "data-font-size", 18.0),
                fontfamily="serif",
            )

    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=100, transparent=False, facecolor="white")
    plt.close(fig)
    buffer.seek(0)
    return Image.open(buffer).convert("RGBA")
