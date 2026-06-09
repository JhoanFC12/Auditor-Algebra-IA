from __future__ import annotations

import base64
import io
import importlib.util
import math
import re
import sys
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from xml.etree import ElementTree as ET


_NUM_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")
_MATH_TEXT_READY = False
_LABEL_BG_PAD = 4.0
_LABEL_BOUNDS_PAD = 2.0
_LABEL_BG_MODE_NONE = "none"
_LABEL_BG_MODE_WHITE = "white"
_LABEL_BG_MODE_CUT = "cut"
_LABEL_BG_MODE_ATTR = "data-label-bg-mode"
_LABEL_BG_MODE_UI_NONE = "sin fondo"
_LABEL_BG_MODE_UI_WHITE = "blanco"
_LABEL_BG_MODE_UI_CUT = "transparente-recorte"
_LABEL_CUT_MASK_ID = "lg-label-cut-mask"
_LABEL_CUT_MASK_STROKE = 2.0
_LABEL_CUT_SHAPE_ATTR = "data-label-cut-shape"
_LABEL_CUT_SHAPE_RECT = "rect"
_LABEL_CUT_SHAPE_CONTOUR = "contour"
_LABEL_CUT_RECT_PAD = 4.0
_ARROW_RETREAT_FRAC = 0.0
_ARROW_MARKER_VIEWBOX = 10.0
_DEFAULT_SNAP_TOL_PX = 8.0
_DEFAULT_DRAG_THRESHOLD_PX = 3.0
_DEFAULT_INTERSECTION_NEAR_TOL_PX = 2.0
_CURVE_RADIUS_DATA_KIND = "curve-radius"
_PROJECTION_HELPER_DATA_KIND = "projection-helper"
_PROJECTION_SEGMENT_DATA_KIND = "projection-segment"
_PROJECTION_SOURCE_ATTR = "data-constraint-projection-source"
_PROJECTION_TARGET_ATTR = "data-constraint-projection-to"
_LINE_START_REF_ATTR = "data-constraint-start-ref"
_LINE_END_REF_ATTR = "data-constraint-end-ref"
_SEG_DIM_LINE_DATA_KIND = "seg-dim-line"
_SEG_DIM_EXT_DATA_KIND = "seg-dim-ext"
_SEG_DIM_LABEL_DATA_KIND = "seg-dim-label"
_SEG_DIM_TICK_DATA_KIND = "seg-dim-tick"
_SEG_DIM_KEY_ATTR = "data-dim-key"
_SEG_DIM_SHOW_ATTR = "data-dim-show"
_SEG_DIM_OFFSET_ATTR = "data-dim-offset"
_SEG_DIM_SIDE_ATTR = "data-dim-side"
_SEG_DIM_SIDE_POS = "normal+"
_SEG_DIM_SIDE_NEG = "normal-"
_SEG_DIM_DEFAULT_OFFSET = 15.0
_SEG_DIM_TICK_LEN = 18.0
_SHADE_DATA_ENABLED = "data-shade-enabled"
_SHADE_DATA_HOLE_IDS = "data-shade-hole-ids"
_SHADE_DATA_MASK_ID = "data-shade-mask-id"
_SHADE_DATA_OVERLAP = "data-shade-overlap"
_SHADE_MASK_PREFIX = "shade-mask-"
_SHADE_CONTOUR_DATA_KIND = "shade-contour"
_SHADE_CONTOUR_DATA_FLAG = "data-shade-contour"
_SHADE_CONTOUR_DATA_SRC = "data-shade-contour-src"
_SHADE_CONTOUR_DATA_TOL = "data-shade-contour-tol"
_SHADE_CONTOUR_ID_PREFIX = "shade-contour"
_DEFAULT_SHADE_CONTOUR_TOL_PX = 6.0
_SVG_NS_URI = "http://www.w3.org/2000/svg"
_XLINK_NS_URI = "http://www.w3.org/1999/xlink"
_XLINK_HREF = f"{{{_XLINK_NS_URI}}}href"
_HIGHLIGHT_COLOR = "#66ccff"
_SHADE_BASE_HIGHLIGHT_COLOR = "#66ccff"
_SHADE_HOLE_HIGHLIGHT_COLOR = "#ff9800"
_GROUP_DATA_KIND = "user-group"
_GROUP_ID_PREFIX = "grp-"
_AUX_DATA_KINDS = {
    "seg-mark",
    "seg-endpoint",
    "seg-endpoint-label",
    "seg-mid-label",
    _SEG_DIM_LINE_DATA_KIND,
    _SEG_DIM_TICK_DATA_KIND,
    _SEG_DIM_EXT_DATA_KIND,
    _SEG_DIM_LABEL_DATA_KIND,
    "label-bg",
    "circle-radius",
    _CURVE_RADIUS_DATA_KIND,
    "subsegment",
    "background",
}

ET.register_namespace("", _SVG_NS_URI)
ET.register_namespace("xlink", _XLINK_NS_URI)


def _is_aux_data_kind(kind: str | None) -> bool:
    return (kind or "").strip() in _AUX_DATA_KINDS


def _label_bg_mode_from_ui(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in (_LABEL_BG_MODE_UI_WHITE, _LABEL_BG_MODE_WHITE):
        return _LABEL_BG_MODE_WHITE
    if raw in (_LABEL_BG_MODE_UI_CUT, _LABEL_BG_MODE_CUT):
        return _LABEL_BG_MODE_CUT
    return _LABEL_BG_MODE_NONE


def _label_bg_mode_to_ui(mode: str | None) -> str:
    raw = (mode or "").strip().lower()
    if raw == _LABEL_BG_MODE_WHITE:
        return _LABEL_BG_MODE_UI_WHITE
    if raw == _LABEL_BG_MODE_CUT:
        return _LABEL_BG_MODE_UI_CUT
    return _LABEL_BG_MODE_UI_NONE


def _locked_point_reason_from_attrs(attrs: dict[str, str | None]) -> str | None:
    if (attrs.get("data-constraint-intersection-of") or "").strip():
        return "Punto dependiente (interseccion)."
    if (attrs.get(_PROJECTION_TARGET_ATTR) or "").strip():
        return "Punto dependiente (proyeccion ortogonal)."
    if (attrs.get("data-constraint-on") or "").strip():
        return "Punto dependiente (sobre objeto)."
    return None


def _class_style_value(
    el: ET.Element,
    name: str,
    class_styles: dict[str, dict[str, str]],
) -> str | None:
    val = _get_attr(el, name)
    if val is not None:
        return val
    class_attr = (el.get("class") or "").strip()
    if not class_attr:
        return None
    merged: dict[str, str] = {}
    for cls in class_attr.split():
        style = class_styles.get(cls)
        if style:
            merged.update(style)
    return merged.get(name)


def _circle_is_point_like(
    el: ET.Element,
    class_styles: dict[str, dict[str, str]],
) -> bool:
    if _strip_ns(el.tag) != "circle":
        return False
    kind = (el.get("data-kind") or "").strip()
    if kind == "point":
        return True
    if (el.get("data-point-id") or "").strip():
        return True
    if (el.get("data-point-kind") or "").strip():
        return True
    if _is_aux_data_kind(kind):
        return False
    class_attr = (el.get("class") or "").strip()
    if class_attr:
        classes = {c.strip() for c in class_attr.split() if c.strip()}
        if "pt" in classes or "point" in classes:
            return True
    r = _parse_float(_get_attr(el, "r"), 0.0)
    if r <= 0:
        return False
    fill = str(_class_style_value(el, "fill", class_styles) or "").strip().lower()
    if fill in ("", "none", "transparent"):
        return False
    return r <= 8.0


def _snap_anchor_point(
    target_x: float,
    target_y: float,
    anchors: list[tuple[float, float]],
    tol_units: float,
    *,
    exclude_anchor: tuple[float, float] | None = None,
) -> tuple[float, float, bool]:
    if tol_units <= 0:
        return (target_x, target_y, False)
    best: tuple[float, float] | None = None
    best_d = float("inf")
    ex = ey = None
    if exclude_anchor is not None:
        ex, ey = exclude_anchor
    for ax, ay in anchors:
        if ex is not None and ey is not None:
            if abs(ax - ex) <= 1e-9 and abs(ay - ey) <= 1e-9:
                continue
        d = math.hypot(ax - target_x, ay - target_y)
        if d < best_d:
            best_d = d
            best = (ax, ay)
    if best is None or best_d > tol_units:
        return (target_x, target_y, False)
    return (best[0], best[1], True)


def _same_point(x1: float, y1: float, x2: float, y2: float, tol: float) -> bool:
    return math.hypot(x1 - x2, y1 - y2) <= tol


def _propagate_point_move_model(
    root: ET.Element,
    point_el: ET.Element,
    old_xy: tuple[float, float],
    new_xy: tuple[float, float],
    tol: float,
) -> tuple[list[ET.Element], list[ET.Element]]:
    old_x, old_y = old_xy
    new_x, new_y = new_xy
    affected_lines: list[ET.Element] = []
    affected_circles: list[ET.Element] = []
    seen_lines: set[int] = set()
    seen_circles: set[int] = set()
    for el in root.iter():
        tag = _strip_ns(el.tag)
        kind = (el.get("data-kind") or "").strip()
        if tag == "line":
            if kind in ("seg-mark", "circle-radius", _CURVE_RADIUS_DATA_KIND):
                continue
            x1 = _parse_float(_get_attr(el, "x1"))
            y1 = _parse_float(_get_attr(el, "y1"))
            x2 = _parse_float(_get_attr(el, "x2"))
            y2 = _parse_float(_get_attr(el, "y2"))
            changed = False
            if _same_point(x1, y1, old_x, old_y, tol):
                _set_attr(el, "x1", _format_num(new_x))
                _set_attr(el, "y1", _format_num(new_y))
                changed = True
            if _same_point(x2, y2, old_x, old_y, tol):
                _set_attr(el, "x2", _format_num(new_x))
                _set_attr(el, "y2", _format_num(new_y))
                changed = True
            if changed and kind != "subsegment" and id(el) not in seen_lines:
                seen_lines.add(id(el))
                affected_lines.append(el)
            continue
        if tag != "circle":
            continue
        if el is point_el:
            continue
        if _is_aux_data_kind(kind):
            continue
        cx = _parse_float(_get_attr(el, "cx"))
        cy = _parse_float(_get_attr(el, "cy"))
        if not _same_point(cx, cy, old_x, old_y, tol):
            continue
        _set_attr(el, "cx", _format_num(new_x))
        _set_attr(el, "cy", _format_num(new_y))
        if id(el) not in seen_circles:
            seen_circles.add(id(el))
            affected_circles.append(el)
    return (affected_lines, affected_circles)


def _ensure_mathtext_fonts() -> None:
    global _MATH_TEXT_READY
    if _MATH_TEXT_READY:
        return
    try:
        import matplotlib as mpl

        mpl.rcParams["mathtext.fontset"] = "cm"
        mpl.rcParams["mathtext.rm"] = "serif"
        mpl.rcParams["font.family"] = "serif"
    except Exception:
        pass
    _MATH_TEXT_READY = True


def _resolve_latex_support():
    try:
        from .latex import configure_mathtext, require_matplotlib

        return configure_mathtext, require_matplotlib
    except Exception as exc:
        last_exc = exc
    try:
        from libreria_geometria.latex import configure_mathtext, require_matplotlib

        return configure_mathtext, require_matplotlib
    except Exception as exc:
        last_exc = exc
    try:
        module_name = "_libreria_geometria_latex_local"
        mod = sys.modules.get(module_name)
        if mod is None:
            latex_path = Path(__file__).with_name("latex.py")
            spec = importlib.util.spec_from_file_location(module_name, str(latex_path))
            if spec is None or spec.loader is None:
                raise ImportError(f"No se pudo cargar {latex_path}")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            sys.modules[module_name] = mod
        configure_mathtext = getattr(mod, "configure_mathtext")
        require_matplotlib = getattr(mod, "require_matplotlib")
        return configure_mathtext, require_matplotlib
    except Exception as exc:
        raise ImportError(f"No se pudo resolver soporte LaTeX: {exc}") from last_exc


def _strip_ns(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _parse_float(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    s = str(value).strip()
    if not s:
        return default
    m = _NUM_RE.search(s)
    if not m:
        return default
    try:
        return float(m.group(0))
    except Exception:
        return default


def _parse_points(points: str | None) -> list[float]:
    if not points:
        return []
    nums = _NUM_RE.findall(points.replace(",", " "))
    out: list[float] = []
    for n in nums:
        try:
            out.append(float(n))
        except Exception:
            continue
    return out


def _parse_style(style: str | None) -> dict[str, str]:
    if not style:
        return {}
    out: dict[str, str] = {}
    for chunk in style.split(";"):
        if ":" not in chunk:
            continue
        k, v = chunk.split(":", 1)
        k = k.strip()
        v = v.strip()
        if k:
            out[k] = v
    return out


def _hex_to_rgb(color: str | None) -> tuple[int, int, int] | None:
    if color is None:
        return None
    s = str(color).strip().lower()
    if not s:
        return None
    if s in ("white", "#fff", "#ffffff"):
        return (255, 255, 255)
    if s in ("black", "#000", "#000000"):
        return (0, 0, 0)
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            try:
                return (int(h[0] * 2, 16), int(h[1] * 2, 16), int(h[2] * 2, 16))
            except Exception:
                return None
        if len(h) == 6:
            try:
                return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
            except Exception:
                return None
    return None


def _contrast_text_color(bg: str | None, *, light: str = "#f0f0f0", dark: str = "#111111") -> str:
    rgb = _hex_to_rgb(bg)
    if rgb is None:
        return light
    r, g, b = rgb
    luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    return dark if luminance >= 0.6 else light


def _parse_css_classes(css: str | None) -> dict[str, dict[str, str]]:
    if not css:
        return {}
    cleaned = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    out: dict[str, dict[str, str]] = {}
    for block in re.finditer(r"([^{}]+)\{([^}]*)\}", cleaned):
        selectors = [s.strip() for s in block.group(1).split(",") if s.strip()]
        styles = _parse_style(block.group(2))
        if not styles:
            continue
        for selector in selectors:
            if not selector.startswith("."):
                continue
            name = selector[1:].split()[0]
            if not name:
                continue
            if name not in out:
                out[name] = {}
            out[name].update(styles)
    return out


def _get_attr(el: ET.Element, name: str) -> str | None:
    if name in el.attrib:
        return el.attrib.get(name)
    style = _parse_style(el.attrib.get("style"))
    return style.get(name)


def _set_attr(el: ET.Element, name: str, value: str) -> None:
    el.set(name, value)
    style = _parse_style(el.attrib.get("style"))
    if name in style:
        style[name] = value
        el.set("style", "; ".join(f"{k}:{v}" for k, v in style.items()))


def _force_style_attr(el: ET.Element, name: str, value: str) -> None:
    el.set(name, value)
    style = _parse_style(el.attrib.get("style"))
    style[name] = value
    el.set("style", "; ".join(f"{k}:{v}" for k, v in style.items()))


def _remove_style_attr(el: ET.Element, name: str) -> None:
    if name in el.attrib:
        del el.attrib[name]
    style = _parse_style(el.attrib.get("style"))
    if name in style:
        del style[name]
        if style:
            el.set("style", "; ".join(f"{k}:{v}" for k, v in style.items()))
        elif "style" in el.attrib:
            del el.attrib["style"]


def _parse_dash(dash: str | None) -> tuple[int, ...] | None:
    if not dash:
        return None
    nums = _NUM_RE.findall(dash)
    if not nums:
        return None
    out: list[int] = []
    for n in nums:
        try:
            out.append(int(float(n)))
        except Exception:
            continue
    return tuple(out) if out else None


def _scaled_dash(dash: tuple[int, ...] | None, scale: float) -> tuple[int, ...] | None:
    if not dash or scale == 1.0:
        return dash
    out: list[int] = []
    for v in dash:
        out.append(max(1, int(round(float(v) * scale))))
    return tuple(out) if out else None


def _mpl_color(value: str | None):
    if value is None:
        return "none"
    v = str(value).strip().lower()
    if v in ("", "none", "transparent"):
        return "none"
    if v.startswith("url("):
        return "none"
    if v.startswith("rgba(") and v.endswith(")"):
        inner = v[5:-1]
        parts = [p.strip() for p in inner.split(",")]
        if len(parts) == 4:
            try:
                r = float(parts[0]) / 255.0
                g = float(parts[1]) / 255.0
                b = float(parts[2]) / 255.0
                a = float(parts[3])
                return (r, g, b, a)
            except Exception:
                return value
    return value


def _path_numbers(d: str | None) -> list[float]:
    if not d:
        return []
    nums = _NUM_RE.findall(d)
    out: list[float] = []
    for n in nums:
        try:
            out.append(float(n))
        except Exception:
            continue
    return out


def _format_num(x: float) -> str:
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _tokenize_path(d: str) -> list[str]:
    return re.findall(r"[MmZzLlHhVvCcSsQqTtAa]|[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", d)


def _scale_path_d(d: str, factor: float) -> str:
    tokens = _tokenize_path(d)
    if not tokens:
        return d or ""

    def is_cmd(tok: str) -> bool:
        return len(tok) == 1 and tok.isalpha()

    def scale_num(tok: str) -> str:
        try:
            return _format_num(float(tok) * factor)
        except Exception:
            return tok

    def keep_num(tok: str) -> str:
        try:
            return _format_num(float(tok))
        except Exception:
            return tok

    def norm_flag(tok: str) -> str:
        try:
            return "1" if float(tok) >= 0.5 else "0"
        except Exception:
            return "0"

    out: list[str] = []
    cmd = ""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if is_cmd(tok):
            cmd = tok
            out.append(tok)
            i += 1
            continue
        if not cmd:
            out.append(scale_num(tok))
            i += 1
            continue

        if cmd in "MmLlTt":
            while i < len(tokens) and not is_cmd(tokens[i]):
                if i + 1 >= len(tokens) or is_cmd(tokens[i + 1]):
                    out.append(scale_num(tokens[i]))
                    i += 1
                    break
                out.append(scale_num(tokens[i]))
                out.append(scale_num(tokens[i + 1]))
                i += 2
            continue
        if cmd in "Hh":
            while i < len(tokens) and not is_cmd(tokens[i]):
                out.append(scale_num(tokens[i]))
                i += 1
            continue
        if cmd in "Vv":
            while i < len(tokens) and not is_cmd(tokens[i]):
                out.append(scale_num(tokens[i]))
                i += 1
            continue
        if cmd in "Cc":
            while i < len(tokens) and not is_cmd(tokens[i]):
                if i + 5 >= len(tokens):
                    out.append(scale_num(tokens[i]))
                    i += 1
                    continue
                for j in range(6):
                    out.append(scale_num(tokens[i + j]))
                i += 6
            continue
        if cmd in "SsQq":
            while i < len(tokens) and not is_cmd(tokens[i]):
                if i + 3 >= len(tokens):
                    out.append(scale_num(tokens[i]))
                    i += 1
                    continue
                for j in range(4):
                    out.append(scale_num(tokens[i + j]))
                i += 4
            continue
        if cmd in "Aa":
            while i < len(tokens) and not is_cmd(tokens[i]):
                if i + 6 >= len(tokens):
                    out.append(scale_num(tokens[i]))
                    i += 1
                    continue
                rx, ry = tokens[i], tokens[i + 1]
                rot = tokens[i + 2]
                large = tokens[i + 3]
                sweep = tokens[i + 4]
                x = tokens[i + 5]
                y = tokens[i + 6]
                out.append(scale_num(rx))
                out.append(scale_num(ry))
                out.append(keep_num(rot))
                out.append(norm_flag(large))
                out.append(norm_flag(sweep))
                out.append(scale_num(x))
                out.append(scale_num(y))
                i += 7
            continue

        out.append(scale_num(tok))
        i += 1

    return " ".join(out)


def _parse_svg_path(d: str, *, curve_steps: int = 48, arc_steps: int = 128) -> list[tuple[list[tuple[float, float]], bool]]:
    tokens = _tokenize_path(d)
    if not tokens:
        return []
    i = 0
    cmd = ""
    cur_x = 0.0
    cur_y = 0.0
    start_x = 0.0
    start_y = 0.0
    last_ctrl: tuple[float, float] | None = None
    last_q_ctrl: tuple[float, float] | None = None
    subpaths: list[tuple[list[tuple[float, float]], bool]] = []
    points: list[tuple[float, float]] = []
    closed = False

    def flush_subpath() -> None:
        nonlocal points, closed
        if points:
            subpaths.append((points, closed))
        points = []
        closed = False

    def next_float() -> float:
        nonlocal i
        val = float(tokens[i])
        i += 1
        return val

    def add_point(x: float, y: float) -> None:
        points.append((x, y))

    def reflect(ctrl: tuple[float, float]) -> tuple[float, float]:
        return (2 * cur_x - ctrl[0], 2 * cur_y - ctrl[1])

    while i < len(tokens):
        t = tokens[i]
        if re.match(r"[A-Za-z]", t):
            cmd = t
            i += 1
        if cmd == "":
            break
        is_rel = cmd.islower()
        op = cmd.upper()

        if op == "M":
            if i + 1 >= len(tokens):
                break
            x = next_float()
            y = next_float()
            if is_rel:
                x += cur_x
                y += cur_y
            if points:
                flush_subpath()
            cur_x, cur_y = x, y
            start_x, start_y = x, y
            add_point(x, y)
            last_ctrl = None
            last_q_ctrl = None
            # Subsequent pairs are implicit LineTo
            cmd = "l" if is_rel else "L"
            continue

        if op == "Z":
            if points and (points[-1][0] != start_x or points[-1][1] != start_y):
                add_point(start_x, start_y)
            closed = True
            cur_x, cur_y = start_x, start_y
            last_ctrl = None
            last_q_ctrl = None
            i += 1
            continue

        if op == "L":
            while i + 1 < len(tokens) and not re.match(r"[A-Za-z]", tokens[i]):
                x = next_float()
                y = next_float()
                if is_rel:
                    x += cur_x
                    y += cur_y
                add_point(x, y)
                cur_x, cur_y = x, y
            last_ctrl = None
            last_q_ctrl = None
            continue

        if op == "H":
            while i < len(tokens) and not re.match(r"[A-Za-z]", tokens[i]):
                x = next_float()
                if is_rel:
                    x += cur_x
                cur_x = x
                add_point(cur_x, cur_y)
            last_ctrl = None
            last_q_ctrl = None
            continue

        if op == "V":
            while i < len(tokens) and not re.match(r"[A-Za-z]", tokens[i]):
                y = next_float()
                if is_rel:
                    y += cur_y
                cur_y = y
                add_point(cur_x, cur_y)
            last_ctrl = None
            last_q_ctrl = None
            continue

        if op == "C":
            while i + 5 < len(tokens) and not re.match(r"[A-Za-z]", tokens[i]):
                x1 = next_float()
                y1 = next_float()
                x2 = next_float()
                y2 = next_float()
                x = next_float()
                y = next_float()
                if is_rel:
                    x1 += cur_x
                    y1 += cur_y
                    x2 += cur_x
                    y2 += cur_y
                    x += cur_x
                    y += cur_y
                for j in range(1, max(2, curve_steps) + 1):
                    t = j / float(max(2, curve_steps))
                    mt = 1 - t
                    px = (
                        mt * mt * mt * cur_x
                        + 3 * mt * mt * t * x1
                        + 3 * mt * t * t * x2
                        + t * t * t * x
                    )
                    py = (
                        mt * mt * mt * cur_y
                        + 3 * mt * mt * t * y1
                        + 3 * mt * t * t * y2
                        + t * t * t * y
                    )
                    add_point(px, py)
                cur_x, cur_y = x, y
                last_ctrl = (x2, y2)
                last_q_ctrl = None
            continue

        if op == "S":
            while i + 3 < len(tokens) and not re.match(r"[A-Za-z]", tokens[i]):
                x2 = next_float()
                y2 = next_float()
                x = next_float()
                y = next_float()
                if is_rel:
                    x2 += cur_x
                    y2 += cur_y
                    x += cur_x
                    y += cur_y
                if last_ctrl is not None:
                    x1, y1 = reflect(last_ctrl)
                else:
                    x1, y1 = cur_x, cur_y
                for j in range(1, max(2, curve_steps) + 1):
                    t = j / float(max(2, curve_steps))
                    mt = 1 - t
                    px = (
                        mt * mt * mt * cur_x
                        + 3 * mt * mt * t * x1
                        + 3 * mt * t * t * x2
                        + t * t * t * x
                    )
                    py = (
                        mt * mt * mt * cur_y
                        + 3 * mt * mt * t * y1
                        + 3 * mt * t * t * y2
                        + t * t * t * y
                    )
                    add_point(px, py)
                cur_x, cur_y = x, y
                last_ctrl = (x2, y2)
                last_q_ctrl = None
            continue

        if op == "Q":
            while i + 3 < len(tokens) and not re.match(r"[A-Za-z]", tokens[i]):
                x1 = next_float()
                y1 = next_float()
                x = next_float()
                y = next_float()
                if is_rel:
                    x1 += cur_x
                    y1 += cur_y
                    x += cur_x
                    y += cur_y
                for j in range(1, max(2, curve_steps) + 1):
                    t = j / float(max(2, curve_steps))
                    mt = 1 - t
                    px = mt * mt * cur_x + 2 * mt * t * x1 + t * t * x
                    py = mt * mt * cur_y + 2 * mt * t * y1 + t * t * y
                    add_point(px, py)
                cur_x, cur_y = x, y
                last_q_ctrl = (x1, y1)
                last_ctrl = None
            continue

        if op == "T":
            while i + 1 < len(tokens) and not re.match(r"[A-Za-z]", tokens[i]):
                x = next_float()
                y = next_float()
                if is_rel:
                    x += cur_x
                    y += cur_y
                if last_q_ctrl is not None:
                    x1, y1 = reflect(last_q_ctrl)
                else:
                    x1, y1 = cur_x, cur_y
                for j in range(1, max(2, curve_steps) + 1):
                    t = j / float(max(2, curve_steps))
                    mt = 1 - t
                    px = mt * mt * cur_x + 2 * mt * t * x1 + t * t * x
                    py = mt * mt * cur_y + 2 * mt * t * y1 + t * t * y
                    add_point(px, py)
                cur_x, cur_y = x, y
                last_q_ctrl = (x1, y1)
                last_ctrl = None
            continue

        if op == "A":
            while i + 6 < len(tokens) and not re.match(r"[A-Za-z]", tokens[i]):
                rx = next_float()
                ry = next_float()
                phi = next_float()
                large = int(next_float())
                sweep = int(next_float())
                x = next_float()
                y = next_float()
                if is_rel:
                    x += cur_x
                    y += cur_y
                if rx == 0 or ry == 0:
                    add_point(x, y)
                    cur_x, cur_y = x, y
                    last_ctrl = None
                    last_q_ctrl = None
                    continue

                phi_rad = math.radians(phi % 360.0)
                cos_phi = math.cos(phi_rad)
                sin_phi = math.sin(phi_rad)
                dx = (cur_x - x) / 2.0
                dy = (cur_y - y) / 2.0
                x1p = cos_phi * dx + sin_phi * dy
                y1p = -sin_phi * dx + cos_phi * dy
                rx_abs = abs(rx)
                ry_abs = abs(ry)
                lam = (x1p * x1p) / (rx_abs * rx_abs) + (y1p * y1p) / (ry_abs * ry_abs)
                if lam > 1:
                    scale = math.sqrt(lam)
                    rx_abs *= scale
                    ry_abs *= scale

                sign = -1.0 if large == sweep else 1.0
                num = rx_abs * rx_abs * ry_abs * ry_abs - rx_abs * rx_abs * y1p * y1p - ry_abs * ry_abs * x1p * x1p
                den = rx_abs * rx_abs * y1p * y1p + ry_abs * ry_abs * x1p * x1p
                coef = 0.0
                if den != 0:
                    coef = sign * math.sqrt(max(0.0, num / den))
                cxp = coef * (rx_abs * y1p / ry_abs)
                cyp = coef * (-ry_abs * x1p / rx_abs)
                cx = cos_phi * cxp - sin_phi * cyp + (cur_x + x) / 2.0
                cy = sin_phi * cxp + cos_phi * cyp + (cur_y + y) / 2.0

                def angle(u: tuple[float, float], v: tuple[float, float]) -> float:
                    dot = u[0] * v[0] + u[1] * v[1]
                    det = u[0] * v[1] - u[1] * v[0]
                    return math.atan2(det, dot)

                ux = (x1p - cxp) / rx_abs
                uy = (y1p - cyp) / ry_abs
                vx = (-x1p - cxp) / rx_abs
                vy = (-y1p - cyp) / ry_abs
                theta1 = angle((1, 0), (ux, uy))
                delta = angle((ux, uy), (vx, vy))
                if sweep == 0 and delta > 0:
                    delta -= 2 * math.pi
                if sweep == 1 and delta < 0:
                    delta += 2 * math.pi

                steps = max(4, int(abs(delta) / (2 * math.pi) * max(arc_steps, 8)))
                for j in range(1, steps + 1):
                    t = j / float(steps)
                    ang = theta1 + delta * t
                    cos_a = math.cos(ang)
                    sin_a = math.sin(ang)
                    px = cx + rx_abs * cos_phi * cos_a - ry_abs * sin_phi * sin_a
                    py = cy + rx_abs * sin_phi * cos_a + ry_abs * cos_phi * sin_a
                    add_point(px, py)

                cur_x, cur_y = x, y
                last_ctrl = None
                last_q_ctrl = None
            continue

        i += 1

    if points:
        subpaths.append((points, closed))
    return subpaths


def _extract_arc_command(d: str) -> tuple[float, float, float, int, int, float, float] | None:
    tokens = _tokenize_path(d)
    if not tokens:
        return None
    i = 0
    sx = sy = tx = ty = 0.0
    ra = None
    sweep = 0
    cmd = ""
    while i < len(tokens):
        t = tokens[i]
        if re.match(r"[A-Za-z]", t):
            cmd = t
            i += 1
        if cmd == "M":
            try:
                sx = float(tokens[i])
                sy = float(tokens[i + 1])
                i += 2
            except Exception:
                return None
        elif cmd == "A":
            try:
                rx = float(tokens[i])
                ry = float(tokens[i + 1])
                _xrot = float(tokens[i + 2])
                large = int(float(tokens[i + 3]))
                sweep = int(float(tokens[i + 4]))
                tx = float(tokens[i + 5])
                ty = float(tokens[i + 6])
                ra = rx if abs(rx - ry) <= 1e-6 else max(rx, ry)
                return (sx, sy, ra, large, sweep, tx, ty)
            except Exception:
                return None
        else:
            i += 1
    return None


def _arc_center_from_endpoints(
    sx: float, sy: float, tx: float, ty: float, r: float, sweep: int, large_arc: int = 0
) -> tuple[float, float] | None:
    dx = tx - sx
    dy = ty - sy
    dist = math.hypot(dx, dy)
    if dist <= 1e-6:
        return None
    if dist > 2.0 * r + 1e-6:
        return None
    mx = 0.5 * (sx + tx)
    my = 0.5 * (sy + ty)
    h = math.sqrt(max(0.0, r * r - (dist * 0.5) ** 2))
    ux = -dy / dist
    uy = dx / dist
    cx1 = mx + h * ux
    cy1 = my + h * uy
    cx2 = mx - h * ux
    cy2 = my - h * uy
    def oriented_delta(cx: float, cy: float) -> float:
        a1 = math.atan2(sy - cy, sx - cx)
        a2 = math.atan2(ty - cy, tx - cx)
        delta = a2 - a1
        if sweep == 1:
            if delta < 0:
                delta += math.tau
        else:
            if delta > 0:
                delta -= math.tau
        return delta

    d1 = oriented_delta(cx1, cy1)
    d2 = oriented_delta(cx2, cy2)
    is_large_1 = abs(d1) > math.pi + 1e-6
    is_large_2 = abs(d2) > math.pi + 1e-6
    want_large = int(large_arc) == 1
    if is_large_1 == want_large and is_large_2 != want_large:
        return (cx1, cy1)
    if is_large_2 == want_large and is_large_1 != want_large:
        return (cx2, cy2)
    return (cx1, cy1)


def _normalize_mathtext(text: str) -> str:
    sub_map = {
        "₀": "0",
        "₁": "1",
        "₂": "2",
        "₃": "3",
        "₄": "4",
        "₅": "5",
        "₆": "6",
        "₇": "7",
        "₈": "8",
        "₉": "9",
        "₊": "+",
        "₋": "-",
        "₌": "=",
        "₍": "(",
        "₎": ")",
    }
    sup_map = {
        "⁰": "0",
        "¹": "1",
        "²": "2",
        "³": "3",
        "⁴": "4",
        "⁵": "5",
        "⁶": "6",
        "⁷": "7",
        "⁸": "8",
        "⁹": "9",
        "⁺": "+",
        "⁻": "-",
        "⁼": "=",
        "⁽": "(",
        "⁾": ")",
    }
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in sub_map:
            j = i
            acc: list[str] = []
            while j < len(text) and text[j] in sub_map:
                acc.append(sub_map[text[j]])
                j += 1
            out.append("_{%s}" % "".join(acc))
            i = j
            continue
        if ch in sup_map:
            j = i
            acc = []
            while j < len(text) and text[j] in sup_map:
                acc.append(sup_map[text[j]])
                j += 1
            out.append("^{%s}" % "".join(acc))
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _strip_mathtext_delims(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    if (s.startswith("\\(") and s.endswith("\\)")) or (
        s.startswith("\\[") and s.endswith("\\]")
    ):
        s = s[2:-2].strip()
    while s.startswith("$") and s.endswith("$") and len(s) >= 2:
        s = s[1:-1].strip()
        if not s:
            break
    return s


_COMPASS_32 = (
    "E",
    "EBS",
    "ESE",
    "SEBE",
    "SE",
    "SEBS",
    "SSE",
    "SBE",
    "S",
    "SBO",
    "SSO",
    "SOBS",
    "SO",
    "SOBO",
    "OSO",
    "OBS",
    "O",
    "OBN",
    "ONO",
    "NOBO",
    "NO",
    "NOBN",
    "NNO",
    "NBO",
    "N",
    "NBE",
    "NNE",
    "NEBN",
    "NE",
    "NEBE",
    "ENE",
    "EBN",
)
_COMPASS_SET = set(_COMPASS_32)
_CENTER_DIR = "C"


def _is_valid_dir(dir_s: str) -> bool:
    d = (dir_s or "").strip().upper()
    return d == _CENTER_DIR or d in _COMPASS_SET


def _dir_to_vec(dir_s: str) -> tuple[float, float]:
    d = (dir_s or "").strip().upper()
    if d == _CENTER_DIR:
        return (0.0, 0.0)
    if d not in _COMPASS_SET:
        return (0.0, -1.0)
    idx = _COMPASS_32.index(d)
    angle_deg = idx * (360.0 / len(_COMPASS_32))
    angle_rad = math.radians(angle_deg)
    return (math.cos(angle_rad), math.sin(angle_rad))


def _label_anchor_for_dir(dir_s: str) -> tuple[float, float] | None:
    if not _is_valid_dir(dir_s):
        return None
    ux, uy = _dir_to_vec(dir_s)
    eps = 1e-9
    if ux > eps:
        ax = 0.0
    elif ux < -eps:
        ax = 1.0
    else:
        ax = 0.5
    if uy < -eps:
        ay = 0.0
    elif uy > eps:
        ay = 1.0
    else:
        ay = 0.5
    return (ax, ay)


def _text_bounds(text: str, font_size: float, latex: bool) -> tuple[float, float, float, float]:
    try:
        _ensure_mathtext_fonts()
        from matplotlib.font_manager import FontProperties
        from matplotlib.textpath import TextPath

        fixed = _normalize_mathtext(_strip_mathtext_delims(text))
        s = fixed if not latex else f"${fixed}$"
        prop = FontProperties()
        path = TextPath((0, 0), s, size=font_size, prop=prop, usetex=False)
        bbox = path.get_extents()
        return (float(bbox.x0), float(bbox.x1), float(bbox.y0), float(bbox.y1))
    except Exception:
        w = max(6.0, 0.6 * font_size * max(1, len(text)))
        h = max(6.0, font_size)
        x0 = 0.0
        x1 = w
        y0 = -0.2 * h
        y1 = 0.8 * h
        return (x0, x1, y0, y1)


def _label_position_from_anchor(
    ax: float,
    ay: float,
    text: str,
    dir_s: str,
    offset_px: float,
    font_size: float,
    latex: bool,
) -> tuple[float, float]:
    x0, x1, y0, y1 = _text_bounds(text, font_size, latex)
    d = (dir_s or "").strip().upper()
    if d == _CENTER_DIR:
        # Same-position mode: center the text on its anchor.
        return (ax - (x0 + x1) / 2.0, ay + (y0 + y1) / 2.0)
    ux, uy = _dir_to_vec(dir_s)
    norm = math.hypot(ux, uy)
    if norm <= 1e-9:
        ux, uy = (0.0, -1.0)
        norm = 1.0
    ux /= norm
    uy /= norm
    eps = 1e-9
    if ux > eps:
        x = ax + offset_px * ux - x0
    elif ux < -eps:
        x = ax + offset_px * ux - x1
    else:
        x = ax - (x0 + x1) / 2.0
    if uy < -eps:
        y = ay + offset_px * uy + y0
    elif uy > eps:
        y = ay + offset_px * uy + y1
    else:
        y = ay + (y0 + y1) / 2.0
    return (x, y)


def _normalize_dir_input(raw: str) -> str:
    s = (raw or "").strip().upper().replace(" ", "")
    if not s:
        return ""
    for ch in "-_/.":
        s = s.replace(ch, "")
    if s in {"C", "CENTRO", "CENTER", "CENTRE", "MISMA", "MISMAPOSICION", "SAMEPOSITION"}:
        return _CENTER_DIR
    s = s.replace("W", "O")
    if s in _COMPASS_SET:
        return s
    letters = [ch for ch in s if ch in "NSEO"]
    if not letters:
        return ""
    if len(letters) == 1:
        return letters[0]
    if len(letters) == 2:
        a, b = letters[0], letters[1]
        if a == b:
            return a
        if a in "NS" and b in "EO":
            return a + b
        if b in "NS" and a in "EO":
            return b + a
        return ""
    letters = letters[:3]
    s = set(letters)
    if s <= {"N", "E"}:
        return "NNE" if letters.count("N") >= 2 else "ENE"
    if s <= {"S", "E"}:
        return "SSE" if letters.count("S") >= 2 else "ESE"
    if s <= {"S", "O"}:
        return "SSO" if letters.count("S") >= 2 else "OSO"
    if s <= {"N", "O"}:
        return "NNO" if letters.count("N") >= 2 else "ONO"
    return ""


def _safe_mathtext(text: str) -> str | None:
    fixed = _normalize_mathtext(_strip_mathtext_delims(text))
    if not fixed:
        return None
    return f"${fixed}$"


def _pretty_xml(root: ET.Element) -> str:
    raw = ET.tostring(root, encoding="unicode")
    try:
        clone = ET.fromstring(raw)
    except Exception:
        return raw
    try:
        ET.indent(clone, space="  ")
        out = ET.tostring(clone, encoding="unicode")
    except Exception:
        try:
            from xml.dom import minidom

            out = minidom.parseString(raw).toprettyxml(indent="  ")
        except Exception:
            return raw
    lines = []
    for line in out.splitlines():
        if line.strip():
            lines.append(line.rstrip())
    if lines and lines[0].startswith("<?xml"):
        lines = lines[1:]
    return "\n".join(lines)


def _textpath_anchor_point(path, anchor: tuple[float, float]) -> tuple[float, float]:
    bbox = path.get_extents()
    width = bbox.xmax - bbox.xmin
    height = bbox.ymax - bbox.ymin
    ax = bbox.xmin + anchor[0] * width
    ay = bbox.ymin + anchor[1] * height
    return (ax, ay)


def _mpl_path_to_svg_d(vertices, codes) -> str:
    from matplotlib.path import Path as MplPath

    if codes is None:
        codes = [MplPath.LINETO] * len(vertices)
        codes[0] = MplPath.MOVETO
    out: list[str] = []
    i = 0
    n = len(vertices)
    while i < n:
        code = codes[i]
        x, y = vertices[i]
        if code == MplPath.MOVETO:
            out.append(f"M {_format_num(x)} {_format_num(y)}")
            i += 1
            continue
        if code == MplPath.LINETO:
            out.append(f"L {_format_num(x)} {_format_num(y)}")
            i += 1
            continue
        if code == MplPath.CURVE3:
            if i + 1 >= n:
                raise ValueError("CURVE3 incompleto")
            cx, cy = vertices[i]
            ex, ey = vertices[i + 1]
            out.append(
                f"Q {_format_num(cx)} {_format_num(cy)} {_format_num(ex)} {_format_num(ey)}"
            )
            i += 2
            continue
        if code == MplPath.CURVE4:
            if i + 2 >= n:
                raise ValueError("CURVE4 incompleto")
            c1x, c1y = vertices[i]
            c2x, c2y = vertices[i + 1]
            ex, ey = vertices[i + 2]
            out.append(
                f"C {_format_num(c1x)} {_format_num(c1y)} {_format_num(c2x)} {_format_num(c2y)} {_format_num(ex)} {_format_num(ey)}"
            )
            i += 3
            continue
        if code == MplPath.CLOSEPOLY:
            out.append("Z")
            i += 1
            continue
        raise ValueError(f"Codigo de Path desconocido: {code}")
    return " ".join(out)


def _latex_path_d(
    text: str,
    x: float,
    y: float,
    font_size: float,
    *,
    anchor: tuple[float, float] | None = None,
) -> str:
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextPath

    fixed = _normalize_mathtext(_strip_mathtext_delims(text))
    if not fixed:
        raise ValueError("texto LaTeX vacio")
    s = f"${fixed}$"
    prop = FontProperties()
    path = TextPath((0, 0), s, size=font_size, prop=prop, usetex=False)
    ax = ay = 0.0
    if anchor is not None:
        ax, ay = _textpath_anchor_point(path, anchor)
    vertices = []
    for vx, vy in path.vertices:
        vertices.append((x + (vx - ax), y - (vy - ay)))
    return _mpl_path_to_svg_d(vertices, path.codes)


@dataclass
class _Record:
    el: ET.Element
    tag: str
    item_ids: list[int]
    kind: str
    orig_fill: str | None = None
    orig_outline: str | None = None


@dataclass
class _Drawable:
    kind: str
    coords: list[float]
    style: dict[str, str]
    layer: int
    text: str | None = None
    record: _Record | None = None


class SvgEditorApp(tk.Tk):
    def __init__(self, initial_path: str | None = None) -> None:
        super().__init__()
        self._apply_dark_theme()
        self.title("Editor SVG - Graficador")
        self.geometry("1400x850")

        self._svg_tree: ET.ElementTree | None = None
        self._svg_root: ET.Element | None = None
        self._current_path: str | None = None
        self._records: list[_Record] = []
        self._drawables: list[_Drawable] = []
        self._selected: _Record | None = None
        self._svg_parent_by_id: dict[int, ET.Element] = {}
        self._anchor_points: list[tuple[float, float]] = []
        self._preview_image: tk.PhotoImage | None = None
        self._embedded_canvas: tk.Canvas | None = None
        self._floating_canvas: tk.Canvas | None = None
        self._floating_graph_window: tk.Toplevel | None = None
        self._floating_graph_btn: ttk.Button | None = None
        self._save_btn: ttk.Button | None = None
        self._view_scale = tk.DoubleVar(self, value=1.0)
        self._view_min_x = 0.0
        self._view_min_y = 0.0
        self._view_width = 0.0
        self._view_height = 0.0
        self._shift_x = 0.0
        self._shift_y = 0.0
        self._history: list[str] = []
        self._history_index = -1
        self._class_styles: dict[str, dict[str, str]] = {}
        self.sel_label: ttk.Label | None = None
        self._line_numbers: tk.Text | None = None
        self._text_scrollbar: ttk.Scrollbar | None = None
        self._code_highlight_tag = "code_select"
        self._suspend_text_sync = False
        self._last_svg_text_raw = ""
        self._code_panel_visible = tk.BooleanVar(self, value=True)
        self._code_sash_pos: int | None = None
        # Full mode keeps the original workflow visible: paste/open SVG on the
        # left, render it, then use the geometric editing modules on the right.
        self._minimal_v1_globales_only = False
        self._global_stroke_var = tk.StringVar(self, value="3")
        self._global_font_size_var = tk.StringVar(self, value="40")
        self._global_point_radius_var = tk.StringVar(self, value="6")
        self._global_label_offset_var = tk.StringVar(self, value="15")
        self._global_arrow_size_var = tk.StringVar(self, value="18")
        self._global_dash_enabled_var = tk.BooleanVar(self, value=False)
        self._global_dash_var = tk.StringVar(self, value="4,3")
        self.stroke_width_var = tk.StringVar(self, value="2")
        self._stroke_dash_var = tk.StringVar(self, value="4,3")
        self._stroke_dash_enabled_var = tk.BooleanVar(self, value=False)
        self.label_text_var = tk.StringVar(self, value="")
        self.label_offset_var = tk.StringVar(self, value="10")
        self._point_label_enabled_var = tk.BooleanVar(self, value=False)
        self._point_label_text_var = tk.StringVar(self, value="")
        self._point_label_dir_var = tk.StringVar(self, value="")
        self._point_label_offset_var = tk.StringVar(self, value="10")
        self._point_label_bg_var = tk.BooleanVar(self, value=False)
        self._point_label_bg_mode_var = tk.StringVar(self, value=_LABEL_BG_MODE_UI_NONE)
        self._point_visible_var = tk.BooleanVar(self, value=True)
        self._polygon_shade_var = tk.BooleanVar(self, value=False)
        self._polygon_shade_opacity_var = tk.StringVar(self, value="0.15")
        self._circle_dashed_var = tk.BooleanVar(self, value=False)
        self._circle_show_radius_var = tk.BooleanVar(self, value=False)
        self._curve_stroke_width_var = tk.StringVar(self, value="3")
        self._curve_stroke_color_var = tk.StringVar(self, value="#000000")
        self._curve_dashed_var = tk.BooleanVar(self, value=False)
        self._curve_dash_var = tk.StringVar(self, value="4,3")
        self._curve_arrow_start_var = tk.BooleanVar(self, value=False)
        self._curve_arrow_end_var = tk.BooleanVar(self, value=False)
        self._curve_show_radius_var = tk.BooleanVar(self, value=False)
        self._segment_dashed_var = tk.BooleanVar(self, value=False)
        self._segment_arrow_start_var = tk.BooleanVar(self, value=False)
        self._segment_arrow_end_var = tk.BooleanVar(self, value=False)
        self._segment_mark_count_var = tk.StringVar(self, value="0")
        self._segment_mark_style_var = tk.StringVar(self, value="none")
        self._segment_mark_radius_var = tk.StringVar(self, value="3")
        self._segment_mark_rect_w_var = tk.StringVar(self, value="8")
        self._segment_mark_rect_h_var = tk.StringVar(self, value="4")
        self._segment_mark_rect_fill_var = tk.BooleanVar(self, value=False)
        self._segment_mark_amp_var = tk.StringVar(self, value="6")
        self._segment_mark_length_var = tk.StringVar(self, value="40")
        self._segment_mark_cycles_var = tk.StringVar(self, value="2")
        self._segment_mark_gap_var = tk.StringVar(self, value="6")
        self._segment_resize_mode_var = tk.StringVar(self, value="ambos")
        self._segment_resize_delta_var = tk.StringVar(self, value="0")
        self._segment_endpoint_target_var = tk.StringVar(self, value="inicio")
        self._segment_endpoint_label_var = tk.StringVar(self, value="")
        self._segment_endpoint_dir_var = tk.StringVar(self, value="")
        self._segment_endpoint_offset_var = tk.StringVar(self, value="")
        self._segment_endpoint_bg_var = tk.BooleanVar(self, value=False)
        self._segment_endpoint_bg_mode_var = tk.StringVar(self, value=_LABEL_BG_MODE_UI_NONE)
        self._segment_mid_label_var = tk.StringVar(self, value="")
        self._segment_mid_dir_var = tk.StringVar(self, value="")
        self._segment_mid_offset_var = tk.StringVar(self, value="")
        self._segment_mid_bg_var = tk.BooleanVar(self, value=False)
        self._segment_mid_bg_mode_var = tk.StringVar(self, value=_LABEL_BG_MODE_UI_NONE)
        self._segment_dim_show_var = tk.BooleanVar(self, value=False)
        self._segment_dim_offset_var = tk.StringVar(self, value=_format_num(_SEG_DIM_DEFAULT_OFFSET))
        self._segment_dim_side_var = tk.StringVar(self, value=_SEG_DIM_SIDE_POS)
        self._segment_editor_enabled = False
        self._segment_mark_updating = False
        self._bg_mode_var = tk.StringVar(self, value="blanco")
        self._segment_create_var = tk.BooleanVar(self, value=False)
        self._segment_create_active = False
        self._segment_create_points: list[tuple[float, float]] = []
        self._intersection_create_var = tk.BooleanVar(self, value=False)
        self._intersection_create_active = False
        self._intersection_create_first: ET.Element | None = None
        self._curve_radius_create_var = tk.BooleanVar(self, value=False)
        self._curve_radius_create_active = False
        self._curve_radius_create_center_el: ET.Element | None = None
        self._projection_create_var = tk.BooleanVar(self, value=False)
        self._projection_create_active = False
        self._projection_create_source: tuple[str, ET.Element] | None = None
        self._shade_diff_var = tk.BooleanVar(self, value=False)
        self._shade_diff_active = False
        self._shade_diff_base: ET.Element | None = None
        self._shade_diff_holes: list[ET.Element] = []
        self._shade_diff_opacity_var = tk.StringVar(self, value="0.15")
        self._shade_selected_opacity_var = tk.StringVar(self, value="0.15")
        self._shade_selected_editor_enabled = False
        self._selected_shade_el: ET.Element | None = None
        self._shade_contour_active = False
        self._shade_contour_edges: list[tuple[ET.Element, int]] = []
        self._shade_contour_open_start: tuple[float, float] | None = None
        self._shade_contour_open_end: tuple[float, float] | None = None
        self._group_select_var = tk.BooleanVar(self, value=False)
        self._group_select_active = False
        self._group_select_elements: list[ET.Element] = []
        self._child_cycle_idx_by_parent: dict[int, int] = {}
        self._last_parent_for_cycle: int | None = None
        self._angle_create_var = tk.BooleanVar(self, value=False)
        self._angle_create_obtuse_var = tk.BooleanVar(self, value=False)
        self._angle_create_active = False
        self._angle_create_points: list[tuple[float, float]] = []
        self._angle_create_segments: list[ET.Element] = []
        self._angle_create_mode: str | None = None
        self._angle_show_arc_var = tk.BooleanVar(self, value=True)
        self._angle_arrow_start_var = tk.BooleanVar(self, value=False)
        self._angle_arrow_end_var = tk.BooleanVar(self, value=False)
        self._angle_reflex_var = tk.BooleanVar(self, value=False)
        self._angle_show_double_var = tk.BooleanVar(self, value=False)
        self._angle_show_sector_var = tk.BooleanVar(self, value=False)
        self._angle_show_point_var = tk.BooleanVar(self, value=False)
        self._angle_show_s_var = tk.BooleanVar(self, value=False)
        self._angle_show_rect_var = tk.BooleanVar(self, value=False)
        self._angle_rect_fill_var = tk.BooleanVar(self, value=False)
        self._angle_label_show_var = tk.BooleanVar(self, value=False)
        self._angle_obtuse_var = tk.BooleanVar(self, value=False)
        self._angle_sector_alpha_var = tk.StringVar(self, value="0.15")
        self._angle_label_text_var = tk.StringVar(self, value="")
        self._angle_label_offset_var = tk.StringVar(self, value="15")
        self._angle_label_angle_var = tk.StringVar(self, value="0")
        self._angle_label_bg_var = tk.BooleanVar(self, value=False)
        self._angle_label_bg_mode_var = tk.StringVar(self, value=_LABEL_BG_MODE_UI_NONE)
        self._angle_vertical_var = tk.BooleanVar(self, value=False)
        self._angle_radius_var = tk.StringVar(self, value="30")
        self._angle_arc_count_var = tk.StringVar(self, value="2")
        self._angle_double_delta_var = tk.StringVar(self, value="5")
        self._angle_point_lambda_var = tk.StringVar(self, value="0.60")
        self._angle_point_r_var = tk.StringVar(self, value="2")
        self._angle_s_len_var = tk.StringVar(self, value="15")
        self._angle_s_amp_var = tk.StringVar(self, value="5")
        self._angle_s_count_var = tk.StringVar(self, value="1")
        self._angle_s_gap_var = tk.StringVar(self, value="6")
        self._angle_rect_len_var = tk.StringVar(self, value="40")
        self._angle_rect_h_var = tk.StringVar(self, value="8")
        self._angle_editor_enabled = False
        self._selected_angle_root: ET.Element | None = None
        self._point_editor_enabled = False
        self._suspend_point_updates = False
        self._selected_point_el: ET.Element | None = None
        self._selected_label_el: ET.Element | None = None
        self._selected_anchor: tuple[float, float] | None = None
        self._polygon_editor_enabled = False
        self._selected_polygon_el: ET.Element | None = None
        self._circle_editor_enabled = False
        self._selected_circle_el: ET.Element | None = None
        self._curve_editor_enabled = False
        self._selected_curve_el: ET.Element | None = None
        self._stroke_editor_enabled = False
        self._selected_stroke_el: ET.Element | None = None
        self._suspend_stroke_updates = False
        self._suspend_segment_updates = False
        self._suspend_circle_updates = False
        self._scale_current = tk.StringVar(self, value="50.0")
        self._scale_new = tk.StringVar(self, value="50.0")
        self._snap_enabled_var = tk.BooleanVar(self, value=True)
        self._snap_tol_var = tk.StringVar(self, value=_format_num(_DEFAULT_SNAP_TOL_PX))
        self._transform_status_var = tk.StringVar(self, value="Listo")
        self._drag_point_el: ET.Element | None = None
        self._drag_radius_el: ET.Element | None = None
        self._drag_point_start: tuple[float, float] | None = None
        self._drag_mouse_start: tuple[float, float] | None = None
        self._drag_active = False
        self._drag_moved = False
        self._drag_threshold_px = _DEFAULT_DRAG_THRESHOLD_PX
        self._drag_history_before: str | None = None
        self._suppress_release_click_once = False
        self._shade_pending_click_after_id: str | None = None
        self._shade_pending_click_event: tuple[int, int] | None = None
        self._shade_single_click_delay_ms = 220
        self._shade_pending_click_tokens: list[tuple[str, tuple[int, int]]] = []

        self._build_ui()
        self._update_save_button_state()
        self._on_transform_field_commit()
        self._show_editor_mode(None)
        self._point_label_enabled_var.trace_add("write", self._on_point_field_change)
        self._point_label_bg_var.trace_add("write", self._on_point_field_change)
        self._point_label_bg_mode_var.trace_add("write", self._on_point_field_change)
        self._point_visible_var.trace_add("write", self._on_point_visibility_change)
        self._polygon_shade_var.trace_add("write", self._on_polygon_field_change)
        self._circle_dashed_var.trace_add("write", self._on_circle_field_change)
        self._circle_show_radius_var.trace_add("write", self._on_circle_field_change)
        self._stroke_dash_enabled_var.trace_add("write", self._on_stroke_dash_change)
        self._segment_dashed_var.trace_add("write", self._on_segment_field_change)
        self._segment_arrow_start_var.trace_add("write", self._on_segment_field_change)
        self._segment_arrow_end_var.trace_add("write", self._on_segment_field_change)
        self._segment_mark_rect_fill_var.trace_add("write", self._on_segment_field_change)
        self._segment_endpoint_bg_var.trace_add("write", self._on_segment_field_change)
        self._segment_mid_bg_var.trace_add("write", self._on_segment_field_change)
        self._segment_endpoint_bg_mode_var.trace_add("write", self._on_segment_field_change)
        self._segment_mid_bg_mode_var.trace_add("write", self._on_segment_field_change)
        self._segment_dim_show_var.trace_add("write", self._on_segment_field_change)
        self._segment_dim_side_var.trace_add("write", self._on_segment_field_change)
        self._angle_show_arc_var.trace_add("write", self._on_angle_toggle_change)
        self._angle_arrow_start_var.trace_add("write", self._on_angle_toggle_change)
        self._angle_arrow_end_var.trace_add("write", self._on_angle_toggle_change)
        self._angle_reflex_var.trace_add("write", self._on_angle_toggle_change)
        self._angle_show_double_var.trace_add("write", self._on_angle_toggle_change)
        self._angle_show_sector_var.trace_add("write", self._on_angle_toggle_change)
        self._angle_show_point_var.trace_add("write", self._on_angle_toggle_change)
        self._angle_show_s_var.trace_add("write", self._on_angle_toggle_change)
        self._angle_show_rect_var.trace_add("write", self._on_angle_toggle_change)
        self._angle_rect_fill_var.trace_add("write", self._on_angle_toggle_change)
        self._angle_label_show_var.trace_add("write", self._on_angle_toggle_change)
        self._angle_label_bg_var.trace_add("write", self._on_angle_toggle_change)
        self._angle_label_bg_mode_var.trace_add("write", self._on_angle_toggle_change)
        self._angle_obtuse_var.trace_add("write", self._on_angle_obtuse_change)
        self._angle_vertical_var.trace_add("write", self._on_angle_vertical_change)
        if initial_path:
            self.after(100, lambda: self.open_svg_path(initial_path))

    def _apply_dark_theme(self) -> None:
        bg = "#1b1b1b"
        panel = "#252526"
        fg = "#f0f0f0"
        entry_bg = "#111111"
        accent = "#2d2d30"

        self.configure(bg=bg)
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(".", background=bg, foreground=fg, fieldbackground=entry_bg)
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("TLabelframe", background=bg, foreground=fg)
        style.configure("TLabelframe.Label", background=bg, foreground=fg)
        style.configure("TButton", background=accent, foreground=fg)
        style.map("TButton", background=[("active", panel)], foreground=[("active", fg)])
        style.configure("TEntry", fieldbackground=entry_bg, foreground=fg)
        style.configure("TPanedwindow", background=bg)
        style.configure("TScrollbar", background=accent, troughcolor=bg, arrowcolor=fg)
        self._style = style
        self._ui_bg = bg
        self._ui_panel = panel
        self._ui_fg = fg
        self._ui_entry_bg = entry_bg
        self._combo_style_cache: dict[str, str] = {}

    def _combobox_style_for_bg(self, bg: str | None) -> str:
        if not hasattr(self, "_style"):
            self._style = ttk.Style(self)
        key = (bg or "").strip().lower()
        if not key:
            key = (self._ui_entry_bg if hasattr(self, "_ui_entry_bg") else "#111111")
        if key in self._combo_style_cache:
            return self._combo_style_cache[key]
        field_bg = bg if bg else (self._ui_entry_bg if hasattr(self, "_ui_entry_bg") else "#111111")
        fg = _contrast_text_color(field_bg)
        style_name = f"Combo{len(self._combo_style_cache)}.TCombobox"
        self._style.configure(style_name, fieldbackground=field_bg, foreground=fg, background=field_bg)
        self._style.map(
            style_name,
            fieldbackground=[("readonly", field_bg), ("!readonly", field_bg)],
            foreground=[("readonly", fg), ("!readonly", fg)],
            background=[("readonly", field_bg), ("!readonly", field_bg)],
        )
        self._combo_style_cache[key] = style_name
        return style_name

    def _apply_combobox_style(self, combo: ttk.Combobox, bg: str | None) -> None:
        if combo is None:
            return
        style_name = self._combobox_style_for_bg(bg)
        combo.configure(style=style_name)

    def _bg_mode_combo_color(self) -> str:
        mode = (self._bg_mode_var.get() or "").strip().lower()
        if mode == "blanco":
            return "#f2f2f2"
        if mode == "negro":
            return "#111111"
        return self._ui_panel if hasattr(self, "_ui_panel") else "#252526"

    def _update_bg_mode_combo_style(self) -> None:
        if hasattr(self, "_bg_mode_combo"):
            self._apply_combobox_style(self._bg_mode_combo, self._bg_mode_combo_color())

    def _build_ui(self) -> None:
        top_bar = ttk.Frame(self)
        top_bar.pack(fill="x", padx=8, pady=(8, 0))
        self._code_toggle_btn = ttk.Button(
            top_bar,
            text="Ocultar codigo",
            command=self._toggle_code_panel,
        )
        self._code_toggle_btn.pack(side="left", padx=(0, 8))
        self._code_buttons = ttk.Frame(top_bar)
        self._code_buttons.pack(side="left")
        ttk.Button(self._code_buttons, text="Renderizar", command=self._render_from_text).pack(side="left")
        ttk.Button(self._code_buttons, text="Abrir", command=self._load_svg).pack(side="left", padx=(8, 0))
        self._save_btn = ttk.Button(self._code_buttons, text="Guardar", command=self._save_current)
        self._save_btn.pack(side="left", padx=(8, 0))
        ttk.Button(self._code_buttons, text="Guardar como", command=self._save_as).pack(side="left", padx=(8, 0))
        ttk.Button(self._code_buttons, text="Exportar PNG", command=self._export_png_as).pack(side="left", padx=(8, 0))
        ttk.Button(self._code_buttons, text="Exportar PDF", command=self._export_pdf_as).pack(side="left", padx=(8, 0))
        self._floating_graph_btn = ttk.Button(
            self._code_buttons,
            text="Grafico flotante",
            command=self._open_floating_graph,
        )
        self._floating_graph_btn.pack(side="left", padx=(8, 0))

        self._main_pane = ttk.PanedWindow(self, orient="horizontal")
        self._main_pane.pack(fill="both", expand=True, padx=8, pady=8)

        self._left_panel = ttk.Frame(self._main_pane, padding=8)
        self._right_panel = ttk.Frame(self._main_pane, padding=8)
        self._main_pane.add(self._left_panel, weight=1)
        self._main_pane.add(self._right_panel, weight=3)

        ttk.Label(self._left_panel, text="SVG input:").pack(anchor="w")
        text_container = ttk.Frame(self._left_panel)
        text_container.pack(fill="both", expand=True, pady=(4, 8))
        self._line_numbers = tk.Text(text_container, width=4, padx=4, takefocus=0, borderwidth=0, wrap="none")
        self._line_numbers.configure(bg="#1b1b1b", fg="#9e9e9e")
        self._line_numbers.pack(side="left", fill="y")

        self.text_input = tk.Text(text_container, width=50, height=40, wrap="none")
        self.text_input.configure(bg="#111111", fg="#f0f0f0", insertbackground="#f0f0f0")
        self.text_input.pack(side="left", fill="both", expand=True)
        self.text_input.tag_configure(self._code_highlight_tag, background="#2b4a6f")

        self._text_scrollbar = ttk.Scrollbar(text_container, orient="vertical", command=self._on_text_scroll)
        self._text_scrollbar.pack(side="right", fill="y")
        self.text_input.configure(yscrollcommand=self._on_text_yscroll)
        self.text_input.bind("<<Modified>>", self._on_text_modified)
        self.text_input.bind("<KeyRelease>", self._on_text_modified)
        self.text_input.bind("<MouseWheel>", self._on_text_modified)
        self.text_input.bind("<Button-1>", self._on_text_modified)
        self._update_line_numbers()

        self._bg_frame = ttk.Frame(self._right_panel)
        self._bg_frame.pack(fill="x", pady=(0, 6))
        ttk.Label(self._bg_frame, text="Fondo:").pack(side="left")
        self._bg_mode_combo = ttk.Combobox(
            self._bg_frame,
            textvariable=self._bg_mode_var,
            values=["blanco", "sin fondo", "negro"],
            width=12,
            state="readonly",
        )
        self._bg_mode_combo.pack(side="left", padx=(6, 0))
        self._bg_mode_combo.bind("<<ComboboxSelected>>", self._on_bg_mode_change)
        self._update_bg_mode_combo_style()

        canvas_frame = ttk.Frame(self._right_panel)
        canvas_frame.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(canvas_frame, bg="#ffffff", highlightthickness=0)
        self._embedded_canvas = self.canvas
        self.canvas.pack(side="left", fill="both", expand=True)
        self._bind_graph_canvas(self.canvas)
        self.bind_all("<Delete>", self._on_delete_key_global)
        self.bind_all("<BackSpace>", self._on_delete_key_global)
        self.bind_all("<Return>", self._on_global_return)
        self.bind_all("<Escape>", self._on_global_escape)

        sb_y = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.canvas.yview)
        sb_y.pack(side="right", fill="y")
        sb_x = ttk.Scrollbar(self._right_panel, orient="horizontal", command=self.canvas.xview)
        sb_x.pack(fill="x")
        self.canvas.configure(xscrollcommand=sb_x.set, yscrollcommand=sb_y.set)

        self._tools_frame = ttk.LabelFrame(self._right_panel, text="Herramientas")
        self._tools_frame.pack(fill="x", pady=(8, 0))
        self._angle_create_chk = ttk.Checkbutton(
            self._tools_frame,
            text="Crear angulo (3 pts / 2 seg)",
            variable=self._angle_create_var,
            command=self._toggle_angle_create,
        )
        self._angle_create_chk.pack(side="left", padx=(4, 8), pady=(4, 4))
        self._angle_create_obtuse_chk = ttk.Checkbutton(
            self._tools_frame,
            text="Preferir obtuso",
            variable=self._angle_create_obtuse_var,
        )
        self._angle_create_obtuse_chk.pack(side="left", padx=(4, 8), pady=(4, 4))
        self._segment_create_chk = ttk.Checkbutton(
            self._tools_frame,
            text="Crear segmento (2 pts)",
            variable=self._segment_create_var,
            command=self._toggle_segment_create,
        )
        self._segment_create_chk.pack(side="left", padx=(4, 8), pady=(4, 4))
        self._intersection_create_chk = ttk.Checkbutton(
            self._tools_frame,
            text="Interseccion (2 obj)",
            variable=self._intersection_create_var,
            command=self._toggle_intersection_create,
        )
        self._intersection_create_chk.pack(side="left", padx=(4, 8), pady=(4, 4))
        self._curve_radius_create_chk = ttk.Checkbutton(
            self._tools_frame,
            text="Radio (centro+curva)",
            variable=self._curve_radius_create_var,
            command=self._toggle_curve_radius_create,
        )
        self._curve_radius_create_chk.pack(side="left", padx=(4, 8), pady=(4, 4))
        self._projection_create_chk = ttk.Checkbutton(
            self._tools_frame,
            text="Proyeccion ort.",
            variable=self._projection_create_var,
            command=self._toggle_projection_create,
        )
        self._projection_create_chk.pack(side="left", padx=(4, 8), pady=(4, 4))
        self._shade_diff_chk = ttk.Checkbutton(
            self._tools_frame,
            text="Sombreado (contorno)",
            variable=self._shade_diff_var,
            command=self._toggle_shade_diff,
        )
        self._shade_diff_chk.pack(side="left", padx=(4, 8), pady=(4, 4))
        ttk.Label(self._tools_frame, text="Opac.").pack(side="left", padx=(0, 2))
        self._shade_diff_opacity_entry = ttk.Entry(self._tools_frame, textvariable=self._shade_diff_opacity_var, width=6)
        self._shade_diff_opacity_entry.pack(side="left", padx=(0, 4), pady=(4, 4))
        self._bind_entry_commit(self._shade_diff_opacity_entry, self._on_shade_diff_opacity_commit)
        self._shade_diff_apply_btn = ttk.Button(self._tools_frame, text="Aplicar", command=self._apply_shade_diff_selection)
        self._shade_diff_apply_btn.pack(side="left", padx=(0, 4), pady=(4, 4))
        self._shade_diff_clear_btn = ttk.Button(
            self._tools_frame, text="Limpiar seleccion", command=self._clear_shade_diff_selection
        )
        self._shade_diff_clear_btn.pack(side="left", padx=(0, 8), pady=(4, 4))
        self._angle_create_status = ttk.Label(self._tools_frame, text="")
        self._angle_create_status.pack(side="left", padx=(4, 0))

        self._transform_frame = ttk.LabelFrame(self._right_panel, text="Transformacion")
        self._transform_frame.pack(fill="x", pady=(8, 0))
        self._snap_enabled_chk = ttk.Checkbutton(
            self._transform_frame,
            text="Snap a anclas",
            variable=self._snap_enabled_var,
            command=self._on_transform_field_commit,
        )
        self._snap_enabled_chk.grid(row=0, column=0, sticky="w", padx=(4, 8), pady=(4, 4))
        ttk.Label(self._transform_frame, text="Tol snap (px):").grid(row=0, column=1, sticky="w", padx=(4, 4), pady=(4, 4))
        self._snap_tol_entry = ttk.Entry(self._transform_frame, textvariable=self._snap_tol_var, width=8)
        self._snap_tol_entry.grid(row=0, column=2, sticky="w", padx=(0, 8), pady=(4, 4))
        self._bind_entry_commit(self._snap_tol_entry, self._on_transform_field_commit)
        self._transform_status_lbl = ttk.Label(
            self._transform_frame,
            textvariable=self._transform_status_var,
        )
        self._transform_status_lbl.grid(row=1, column=0, columnspan=3, sticky="w", padx=(4, 8), pady=(0, 4))
        self._transform_frame.columnconfigure(3, weight=1)

        self._point_editor_frame = ttk.LabelFrame(self._right_panel, text="Editor de punto")
        self._point_editor_frame.pack(fill="x", pady=(8, 0))
        self._point_label_chk = ttk.Checkbutton(
            self._point_editor_frame,
            text="Mostrar etiqueta",
            variable=self._point_label_enabled_var,
        )
        self._point_label_chk.grid(row=0, column=0, sticky="w", padx=(4, 8), pady=(4, 4))
        self._point_visible_chk = ttk.Checkbutton(
            self._point_editor_frame,
            text="Mostrar punto",
            variable=self._point_visible_var,
        )
        self._point_visible_chk.grid(row=0, column=1, sticky="w", padx=(4, 8), pady=(4, 4))
        ttk.Label(self._point_editor_frame, text="Fondo:").grid(row=0, column=2, sticky="w", padx=(4, 4), pady=(4, 4))
        self._point_label_bg_mode_combo = ttk.Combobox(
            self._point_editor_frame,
            textvariable=self._point_label_bg_mode_var,
            values=(
                _LABEL_BG_MODE_UI_NONE,
                _LABEL_BG_MODE_UI_WHITE,
                _LABEL_BG_MODE_UI_CUT,
            ),
            width=20,
            state="readonly",
        )
        self._point_label_bg_mode_combo.grid(row=0, column=3, sticky="w", padx=(0, 8), pady=(4, 4))
        self._point_label_bg_mode_combo.bind("<<ComboboxSelected>>", self._on_point_field_change)
        self._apply_combobox_style(self._point_label_bg_mode_combo, self._ui_entry_bg)

        ttk.Label(self._point_editor_frame, text="Texto:").grid(row=1, column=0, sticky="w", padx=(4, 4))
        self._point_label_text_entry = ttk.Entry(self._point_editor_frame, textvariable=self._point_label_text_var, width=24)
        self._point_label_text_entry.grid(row=1, column=1, sticky="w", padx=(0, 8))
        self._bind_entry_commit(self._point_label_text_entry, self._on_point_field_commit)

        ttk.Label(self._point_editor_frame, text="Direccion (N/S/E/O/C):").grid(row=1, column=2, sticky="w", padx=(4, 4))
        self._point_label_dir_entry = ttk.Entry(self._point_editor_frame, textvariable=self._point_label_dir_var, width=6)
        self._point_label_dir_entry.grid(row=1, column=3, sticky="w", padx=(0, 8))
        self._bind_entry_commit(self._point_label_dir_entry, self._on_point_field_commit)

        ttk.Label(self._point_editor_frame, text="Separacion (px):").grid(row=2, column=0, sticky="w", padx=(4, 4))
        self._point_label_offset_entry = ttk.Entry(self._point_editor_frame, textvariable=self._point_label_offset_var, width=8)
        self._point_label_offset_entry.grid(row=2, column=1, sticky="w", padx=(0, 8), pady=(4, 4))
        self._bind_entry_commit(self._point_label_offset_entry, self._on_point_field_commit)
        self._point_editor_frame.columnconfigure(1, weight=1)

        self._segment_editor_frame = ttk.LabelFrame(self._right_panel, text="Editor de segmento")
        self._segment_editor_frame.pack(fill="x", pady=(8, 0))
        self._segment_dashed_chk = ttk.Checkbutton(
            self._segment_editor_frame,
            text="Discontinuo",
            variable=self._segment_dashed_var,
        )
        self._segment_dashed_chk.grid(row=0, column=0, sticky="w", padx=(4, 8), pady=(4, 4))
        self._segment_arrow_start_chk = ttk.Checkbutton(
            self._segment_editor_frame,
            text="Flecha inicio",
            variable=self._segment_arrow_start_var,
        )
        self._segment_arrow_start_chk.grid(row=0, column=1, sticky="w", padx=(4, 8), pady=(4, 4))
        self._segment_arrow_end_chk = ttk.Checkbutton(
            self._segment_editor_frame,
            text="Flecha fin",
            variable=self._segment_arrow_end_var,
        )
        self._segment_arrow_end_chk.grid(row=0, column=2, sticky="w", padx=(4, 8), pady=(4, 4))
        ttk.Label(self._segment_editor_frame, text="Estilo:").grid(row=1, column=0, sticky="w", padx=(4, 4))
        self._segment_mark_style_combo = ttk.Combobox(
            self._segment_editor_frame,
            textvariable=self._segment_mark_style_var,
            values=("none", "puntos", "rectangulo", "sinusoidal", "s"),
            width=12,
            state="readonly",
        )
        self._segment_mark_style_combo.grid(row=1, column=1, sticky="w", padx=(0, 8), pady=(2, 4))
        self._segment_mark_style_combo.bind("<<ComboboxSelected>>", self._on_segment_style_selected)
        self._apply_combobox_style(self._segment_mark_style_combo, self._ui_entry_bg)
        ttk.Label(self._segment_editor_frame, text="n:").grid(row=1, column=2, sticky="w", padx=(4, 4))
        self._segment_mark_entry = ttk.Entry(
            self._segment_editor_frame, textvariable=self._segment_mark_count_var, width=6
        )
        self._segment_mark_entry.grid(row=1, column=3, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._segment_mark_entry, self._on_segment_field_commit)

        self._segment_mark_points_frame = ttk.Frame(self._segment_editor_frame)
        ttk.Label(self._segment_mark_points_frame, text="r:").grid(row=0, column=0, sticky="w", padx=(4, 4))
        self._segment_mark_radius_entry = ttk.Entry(
            self._segment_mark_points_frame, textvariable=self._segment_mark_radius_var, width=6
        )
        self._segment_mark_radius_entry.grid(row=0, column=1, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._segment_mark_radius_entry, self._on_segment_field_commit)

        self._segment_mark_rect_frame = ttk.Frame(self._segment_editor_frame)
        ttk.Label(self._segment_mark_rect_frame, text="Rect W:").grid(row=0, column=0, sticky="w", padx=(4, 4))
        self._segment_mark_rect_w_entry = ttk.Entry(
            self._segment_mark_rect_frame, textvariable=self._segment_mark_rect_w_var, width=6
        )
        self._segment_mark_rect_w_entry.grid(row=0, column=1, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._segment_mark_rect_w_entry, self._on_segment_field_commit)
        ttk.Label(self._segment_mark_rect_frame, text="Rect H:").grid(row=0, column=2, sticky="w", padx=(4, 4))
        self._segment_mark_rect_h_entry = ttk.Entry(
            self._segment_mark_rect_frame, textvariable=self._segment_mark_rect_h_var, width=6
        )
        self._segment_mark_rect_h_entry.grid(row=0, column=3, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._segment_mark_rect_h_entry, self._on_segment_field_commit)
        self._segment_mark_rect_fill_chk = ttk.Checkbutton(
            self._segment_mark_rect_frame,
            text="Relleno",
            variable=self._segment_mark_rect_fill_var,
        )
        self._segment_mark_rect_fill_chk.grid(row=0, column=4, sticky="w", padx=(4, 8), pady=(2, 4))

        self._segment_mark_wave_frame = ttk.Frame(self._segment_editor_frame)
        ttk.Label(self._segment_mark_wave_frame, text="Amp:").grid(row=0, column=0, sticky="w", padx=(4, 4))
        self._segment_mark_amp_entry = ttk.Entry(
            self._segment_mark_wave_frame, textvariable=self._segment_mark_amp_var, width=6
        )
        self._segment_mark_amp_entry.grid(row=0, column=1, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._segment_mark_amp_entry, self._on_segment_field_commit)
        ttk.Label(self._segment_mark_wave_frame, text="Long:").grid(row=0, column=2, sticky="w", padx=(4, 4))
        self._segment_mark_length_entry = ttk.Entry(
            self._segment_mark_wave_frame, textvariable=self._segment_mark_length_var, width=6
        )
        self._segment_mark_length_entry.grid(row=0, column=3, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._segment_mark_length_entry, self._on_segment_field_commit)
        ttk.Label(self._segment_mark_wave_frame, text="Ciclos:").grid(row=0, column=4, sticky="w", padx=(4, 4))
        self._segment_mark_cycles_entry = ttk.Entry(
            self._segment_mark_wave_frame, textvariable=self._segment_mark_cycles_var, width=6
        )
        self._segment_mark_cycles_entry.grid(row=0, column=5, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._segment_mark_cycles_entry, self._on_segment_field_commit)
        ttk.Label(self._segment_mark_wave_frame, text="Gap:").grid(row=0, column=6, sticky="w", padx=(4, 4))
        self._segment_mark_gap_entry = ttk.Entry(
            self._segment_mark_wave_frame, textvariable=self._segment_mark_gap_var, width=6
        )
        self._segment_mark_gap_entry.grid(row=0, column=7, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._segment_mark_gap_entry, self._on_segment_field_commit)

        self._segment_mark_s_frame = ttk.Frame(self._segment_editor_frame)
        ttk.Label(self._segment_mark_s_frame, text="Amp:").grid(row=0, column=0, sticky="w", padx=(4, 4))
        self._segment_mark_s_amp_entry = ttk.Entry(
            self._segment_mark_s_frame, textvariable=self._segment_mark_amp_var, width=6
        )
        self._segment_mark_s_amp_entry.grid(row=0, column=1, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._segment_mark_s_amp_entry, self._on_segment_field_commit)
        ttk.Label(self._segment_mark_s_frame, text="Long:").grid(row=0, column=2, sticky="w", padx=(4, 4))
        self._segment_mark_s_length_entry = ttk.Entry(
            self._segment_mark_s_frame, textvariable=self._segment_mark_length_var, width=6
        )
        self._segment_mark_s_length_entry.grid(row=0, column=3, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._segment_mark_s_length_entry, self._on_segment_field_commit)
        ttk.Label(self._segment_mark_s_frame, text="Gap:").grid(row=0, column=4, sticky="w", padx=(4, 4))
        self._segment_mark_s_gap_entry = ttk.Entry(
            self._segment_mark_s_frame, textvariable=self._segment_mark_gap_var, width=6
        )
        self._segment_mark_s_gap_entry.grid(row=0, column=5, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._segment_mark_s_gap_entry, self._on_segment_field_commit)
        self._segment_resize_frame = ttk.Frame(self._segment_editor_frame)
        ttk.Label(self._segment_resize_frame, text="Ajuste (px):").grid(row=0, column=0, sticky="w", padx=(4, 4))
        self._segment_resize_entry = ttk.Entry(
            self._segment_resize_frame, textvariable=self._segment_resize_delta_var, width=8
        )
        self._segment_resize_entry.grid(row=0, column=1, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._segment_resize_entry, self._on_segment_field_commit)
        ttk.Label(self._segment_resize_frame, text="Modo:").grid(row=0, column=2, sticky="w", padx=(4, 4))
        self._segment_resize_mode_combo = ttk.Combobox(
            self._segment_resize_frame,
            textvariable=self._segment_resize_mode_var,
            values=["ambos", "izquierda", "derecha"],
            width=12,
            state="readonly",
        )
        self._segment_resize_mode_combo.grid(row=0, column=3, sticky="w", padx=(0, 8), pady=(2, 4))
        self._apply_combobox_style(self._segment_resize_mode_combo, self._ui_entry_bg)
        self._segment_resize_frame.grid(row=3, column=0, columnspan=8, sticky="w")
        self._segment_endpoint_frame = ttk.Frame(self._segment_editor_frame)
        ttk.Label(self._segment_endpoint_frame, text="Etiqueta extremo:").grid(row=0, column=0, sticky="w", padx=(4, 4), pady=(2, 4))
        self._segment_endpoint_target_combo = ttk.Combobox(
            self._segment_endpoint_frame,
            textvariable=self._segment_endpoint_target_var,
            values=["inicio", "fin"],
            width=10,
            state="readonly",
        )
        self._segment_endpoint_target_combo.grid(row=0, column=1, sticky="w", padx=(0, 8), pady=(2, 4))
        self._segment_endpoint_target_combo.bind("<<ComboboxSelected>>", self._on_segment_field_change)
        self._apply_combobox_style(self._segment_endpoint_target_combo, self._ui_entry_bg)
        ttk.Label(self._segment_endpoint_frame, text="Texto:").grid(row=0, column=2, sticky="w", padx=(4, 4), pady=(2, 4))
        self._segment_endpoint_label_entry = ttk.Entry(
            self._segment_endpoint_frame, textvariable=self._segment_endpoint_label_var, width=10
        )
        self._segment_endpoint_label_entry.grid(row=0, column=3, sticky="w", padx=(0, 8), pady=(2, 4))
        ttk.Label(self._segment_endpoint_frame, text="Dir (C=centro):").grid(row=0, column=4, sticky="w", padx=(4, 4), pady=(2, 4))
        self._segment_endpoint_dir_entry = ttk.Entry(
            self._segment_endpoint_frame, textvariable=self._segment_endpoint_dir_var, width=6
        )
        self._segment_endpoint_dir_entry.grid(row=0, column=5, sticky="w", padx=(0, 8), pady=(2, 4))
        ttk.Label(self._segment_endpoint_frame, text="Offset:").grid(row=0, column=6, sticky="w", padx=(4, 4), pady=(2, 4))
        self._segment_endpoint_offset_entry = ttk.Entry(
            self._segment_endpoint_frame, textvariable=self._segment_endpoint_offset_var, width=6
        )
        self._segment_endpoint_offset_entry.grid(row=0, column=7, sticky="w", padx=(0, 8), pady=(2, 4))
        ttk.Label(self._segment_endpoint_frame, text="Fondo:").grid(row=0, column=8, sticky="w", padx=(4, 4), pady=(2, 4))
        self._segment_endpoint_bg_mode_combo = ttk.Combobox(
            self._segment_endpoint_frame,
            textvariable=self._segment_endpoint_bg_mode_var,
            values=(
                _LABEL_BG_MODE_UI_NONE,
                _LABEL_BG_MODE_UI_WHITE,
                _LABEL_BG_MODE_UI_CUT,
            ),
            width=20,
            state="readonly",
        )
        self._segment_endpoint_bg_mode_combo.grid(row=0, column=9, sticky="w", padx=(0, 8), pady=(2, 4))
        self._segment_endpoint_bg_mode_combo.bind("<<ComboboxSelected>>", self._on_segment_field_change)
        self._apply_combobox_style(self._segment_endpoint_bg_mode_combo, self._ui_entry_bg)
        self._bind_entry_commit(self._segment_endpoint_label_entry, self._on_segment_field_commit)
        self._bind_entry_commit(self._segment_endpoint_dir_entry, self._on_segment_field_commit)
        self._bind_entry_commit(self._segment_endpoint_offset_entry, self._on_segment_field_commit)
        self._segment_endpoint_frame.grid(row=4, column=0, columnspan=8, sticky="w")
        self._segment_mid_frame = ttk.Frame(self._segment_editor_frame)
        ttk.Label(self._segment_mid_frame, text="Etiqueta centro:").grid(row=0, column=0, sticky="w", padx=(4, 4), pady=(2, 4))
        ttk.Label(self._segment_mid_frame, text="Texto:").grid(row=0, column=1, sticky="w", padx=(4, 4), pady=(2, 4))
        self._segment_mid_label_entry = ttk.Entry(
            self._segment_mid_frame, textvariable=self._segment_mid_label_var, width=10
        )
        self._segment_mid_label_entry.grid(row=0, column=2, sticky="w", padx=(0, 8), pady=(2, 4))
        ttk.Label(self._segment_mid_frame, text="Dir (C=centro):").grid(row=0, column=3, sticky="w", padx=(4, 4), pady=(2, 4))
        self._segment_mid_dir_entry = ttk.Entry(
            self._segment_mid_frame, textvariable=self._segment_mid_dir_var, width=6
        )
        self._segment_mid_dir_entry.grid(row=0, column=4, sticky="w", padx=(0, 8), pady=(2, 4))
        ttk.Label(self._segment_mid_frame, text="Offset:").grid(row=0, column=5, sticky="w", padx=(4, 4), pady=(2, 4))
        self._segment_mid_offset_entry = ttk.Entry(
            self._segment_mid_frame, textvariable=self._segment_mid_offset_var, width=6
        )
        self._segment_mid_offset_entry.grid(row=0, column=6, sticky="w", padx=(0, 8), pady=(2, 4))
        ttk.Label(self._segment_mid_frame, text="Fondo:").grid(row=0, column=7, sticky="w", padx=(4, 4), pady=(2, 4))
        self._segment_mid_bg_mode_combo = ttk.Combobox(
            self._segment_mid_frame,
            textvariable=self._segment_mid_bg_mode_var,
            values=(
                _LABEL_BG_MODE_UI_NONE,
                _LABEL_BG_MODE_UI_WHITE,
                _LABEL_BG_MODE_UI_CUT,
            ),
            width=20,
            state="readonly",
        )
        self._segment_mid_bg_mode_combo.grid(row=0, column=8, sticky="w", padx=(0, 8), pady=(2, 4))
        self._segment_mid_bg_mode_combo.bind("<<ComboboxSelected>>", self._on_segment_field_change)
        self._apply_combobox_style(self._segment_mid_bg_mode_combo, self._ui_entry_bg)
        self._bind_entry_commit(self._segment_mid_label_entry, self._on_segment_field_commit)
        self._bind_entry_commit(self._segment_mid_dir_entry, self._on_segment_field_commit)
        self._bind_entry_commit(self._segment_mid_offset_entry, self._on_segment_field_commit)
        self._segment_mid_frame.grid(row=5, column=0, columnspan=8, sticky="w")
        self._segment_dim_frame = ttk.Frame(self._segment_editor_frame)
        self._segment_dim_show_chk = ttk.Checkbutton(
            self._segment_dim_frame,
            text="Longitud (paralela)",
            variable=self._segment_dim_show_var,
        )
        self._segment_dim_show_chk.grid(row=0, column=0, sticky="w", padx=(4, 8), pady=(2, 4))
        ttk.Label(self._segment_dim_frame, text="Offset:").grid(row=0, column=1, sticky="w", padx=(4, 4), pady=(2, 4))
        self._segment_dim_offset_entry = ttk.Entry(
            self._segment_dim_frame, textvariable=self._segment_dim_offset_var, width=8
        )
        self._segment_dim_offset_entry.grid(row=0, column=2, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._segment_dim_offset_entry, self._on_segment_field_commit)
        ttk.Label(self._segment_dim_frame, text="Lado:").grid(row=0, column=3, sticky="w", padx=(4, 4), pady=(2, 4))
        self._segment_dim_side_combo = ttk.Combobox(
            self._segment_dim_frame,
            textvariable=self._segment_dim_side_var,
            values=[_SEG_DIM_SIDE_POS, _SEG_DIM_SIDE_NEG],
            width=10,
            state="readonly",
        )
        self._segment_dim_side_combo.grid(row=0, column=4, sticky="w", padx=(0, 8), pady=(2, 4))
        self._segment_dim_side_combo.bind("<<ComboboxSelected>>", self._on_segment_field_change)
        self._apply_combobox_style(self._segment_dim_side_combo, self._ui_entry_bg)
        self._segment_dim_frame.grid(row=6, column=0, columnspan=8, sticky="w")
        self._segment_apply_btn = ttk.Button(
            self._segment_editor_frame,
            text="Aplicar",
            command=self._apply_segment_editor_changes,
        )
        self._segment_apply_btn.grid(row=7, column=0, columnspan=8, sticky="w", padx=(4, 4), pady=(2, 6))
        self._update_segment_mark_fields()

        self._curve_editor_frame = ttk.LabelFrame(self._right_panel, text="Editor de curva")
        self._curve_editor_frame.pack(fill="x", pady=(8, 0))
        ttk.Label(self._curve_editor_frame, text="Grosor:").grid(row=0, column=0, sticky="w", padx=(4, 4), pady=(4, 2))
        self._curve_stroke_width_entry = ttk.Entry(
            self._curve_editor_frame, textvariable=self._curve_stroke_width_var, width=8
        )
        self._curve_stroke_width_entry.grid(row=0, column=1, sticky="w", padx=(0, 8), pady=(4, 2))
        self._bind_entry_commit(self._curve_stroke_width_entry, self._on_curve_field_commit)
        ttk.Label(self._curve_editor_frame, text="Color:").grid(row=0, column=2, sticky="w", padx=(4, 4), pady=(4, 2))
        self._curve_stroke_color_entry = ttk.Entry(
            self._curve_editor_frame, textvariable=self._curve_stroke_color_var, width=10
        )
        self._curve_stroke_color_entry.grid(row=0, column=3, sticky="w", padx=(0, 8), pady=(4, 2))
        self._bind_entry_commit(self._curve_stroke_color_entry, self._on_curve_field_commit)
        self._curve_dashed_chk = ttk.Checkbutton(
            self._curve_editor_frame,
            text="Discontinuo",
            variable=self._curve_dashed_var,
            command=self._on_curve_field_change,
        )
        self._curve_dashed_chk.grid(row=1, column=0, sticky="w", padx=(4, 8), pady=(2, 2))
        ttk.Label(self._curve_editor_frame, text="Patron:").grid(row=1, column=1, sticky="w", padx=(4, 4), pady=(2, 2))
        self._curve_dash_entry = ttk.Entry(self._curve_editor_frame, textvariable=self._curve_dash_var, width=10)
        self._curve_dash_entry.grid(row=1, column=2, sticky="w", padx=(0, 8), pady=(2, 2))
        self._bind_entry_commit(self._curve_dash_entry, self._on_curve_field_commit)
        self._curve_arrow_start_chk = ttk.Checkbutton(
            self._curve_editor_frame,
            text="Flecha inicio",
            variable=self._curve_arrow_start_var,
            command=self._on_curve_field_change,
        )
        self._curve_arrow_start_chk.grid(row=2, column=0, sticky="w", padx=(4, 8), pady=(2, 2))
        self._curve_arrow_end_chk = ttk.Checkbutton(
            self._curve_editor_frame,
            text="Flecha fin",
            variable=self._curve_arrow_end_var,
            command=self._on_curve_field_change,
        )
        self._curve_arrow_end_chk.grid(row=2, column=1, sticky="w", padx=(4, 8), pady=(2, 2))
        self._curve_radius_chk = ttk.Checkbutton(
            self._curve_editor_frame,
            text="Radio",
            variable=self._curve_show_radius_var,
            command=self._on_curve_field_change,
        )
        self._curve_radius_chk.grid(row=2, column=2, sticky="w", padx=(4, 8), pady=(2, 2))
        self._curve_apply_btn = ttk.Button(
            self._curve_editor_frame,
            text="Aplicar",
            command=self._apply_curve_editor_changes,
        )
        self._curve_apply_btn.grid(row=3, column=0, columnspan=4, sticky="w", padx=(4, 4), pady=(2, 6))

        self._angle_editor_frame = ttk.LabelFrame(self._right_panel, text="Editor de angulo")
        self._angle_editor_frame.pack(fill="x", pady=(8, 0))
        self._angle_arc_chk = ttk.Checkbutton(
            self._angle_editor_frame,
            text="Arco",
            variable=self._angle_show_arc_var,
        )
        self._angle_arc_chk.grid(row=0, column=0, sticky="w", padx=(4, 8), pady=(4, 4))
        self._angle_arrow_start_chk = ttk.Checkbutton(
            self._angle_editor_frame,
            text="Flecha inicio",
            variable=self._angle_arrow_start_var,
        )
        self._angle_arrow_start_chk.grid(row=0, column=1, sticky="w", padx=(4, 8), pady=(4, 4))
        self._angle_arrow_end_chk = ttk.Checkbutton(
            self._angle_editor_frame,
            text="Flecha fin",
            variable=self._angle_arrow_end_var,
        )
        self._angle_arrow_end_chk.grid(row=0, column=2, sticky="w", padx=(4, 8), pady=(4, 4))
        self._angle_double_chk = ttk.Checkbutton(
            self._angle_editor_frame,
            text="Doble",
            variable=self._angle_show_double_var,
        )
        self._angle_double_chk.grid(row=0, column=3, sticky="w", padx=(4, 8), pady=(4, 4))
        self._angle_sector_chk = ttk.Checkbutton(
            self._angle_editor_frame,
            text="Sector",
            variable=self._angle_show_sector_var,
        )
        self._angle_sector_chk.grid(row=0, column=4, sticky="w", padx=(4, 8), pady=(4, 4))
        self._angle_point_chk = ttk.Checkbutton(
            self._angle_editor_frame,
            text="Punto",
            variable=self._angle_show_point_var,
        )
        self._angle_point_chk.grid(row=0, column=5, sticky="w", padx=(4, 8), pady=(4, 4))
        self._angle_s_chk = ttk.Checkbutton(
            self._angle_editor_frame,
            text="S",
            variable=self._angle_show_s_var,
        )
        self._angle_s_chk.grid(row=0, column=6, sticky="w", padx=(4, 8), pady=(4, 4))
        self._angle_rect_chk = ttk.Checkbutton(
            self._angle_editor_frame,
            text="Rect 90",
            variable=self._angle_show_rect_var,
        )
        self._angle_rect_chk.grid(row=0, column=7, sticky="w", padx=(4, 8), pady=(4, 4))
        self._angle_rect_fill_chk = ttk.Checkbutton(
            self._angle_editor_frame,
            text="Relleno",
            variable=self._angle_rect_fill_var,
        )
        self._angle_rect_fill_chk.grid(row=0, column=8, sticky="w", padx=(4, 8), pady=(4, 4))
        self._angle_label_chk = ttk.Checkbutton(
            self._angle_editor_frame,
            text="Etiqueta",
            variable=self._angle_label_show_var,
        )
        self._angle_label_chk.grid(row=0, column=9, sticky="w", padx=(4, 8), pady=(4, 4))
        self._angle_obtuse_chk = ttk.Checkbutton(
            self._angle_editor_frame,
            text="Obtuso",
            variable=self._angle_obtuse_var,
        )
        self._angle_obtuse_chk.grid(row=0, column=10, sticky="w", padx=(4, 8), pady=(4, 4))
        self._angle_reflex_chk = ttk.Checkbutton(
            self._angle_editor_frame,
            text="Replemento",
            variable=self._angle_reflex_var,
        )
        self._angle_reflex_chk.grid(row=0, column=11, sticky="w", padx=(4, 8), pady=(4, 4))
        self._angle_vertical_chk = ttk.Checkbutton(
            self._angle_editor_frame,
            text="Opuesto",
            variable=self._angle_vertical_var,
        )
        self._angle_vertical_chk.grid(row=0, column=12, sticky="w", padx=(4, 8), pady=(4, 4))

        self._angle_label_frame = ttk.Frame(self._angle_editor_frame)
        ttk.Label(self._angle_label_frame, text="Texto:").grid(row=0, column=0, sticky="w", padx=(4, 4))
        self._angle_label_text_entry = ttk.Entry(
            self._angle_label_frame, textvariable=self._angle_label_text_var, width=14
        )
        self._angle_label_text_entry.grid(row=0, column=1, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._angle_label_text_entry, self._on_angle_field_commit)
        ttk.Label(self._angle_label_frame, text="Offset:").grid(row=0, column=2, sticky="w", padx=(4, 4))
        self._angle_label_offset_entry = ttk.Entry(
            self._angle_label_frame, textvariable=self._angle_label_offset_var, width=6
        )
        self._angle_label_offset_entry.grid(row=0, column=3, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._angle_label_offset_entry, self._on_angle_field_commit)
        ttk.Label(self._angle_label_frame, text="Ang:").grid(row=0, column=4, sticky="w", padx=(4, 4))
        self._angle_label_angle_entry = ttk.Entry(
            self._angle_label_frame, textvariable=self._angle_label_angle_var, width=6
        )
        self._angle_label_angle_entry.grid(row=0, column=5, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._angle_label_angle_entry, self._on_angle_field_commit)
        ttk.Label(self._angle_label_frame, text="Fondo:").grid(row=0, column=6, sticky="w", padx=(4, 4), pady=(2, 4))
        self._angle_label_bg_mode_combo = ttk.Combobox(
            self._angle_label_frame,
            textvariable=self._angle_label_bg_mode_var,
            values=(
                _LABEL_BG_MODE_UI_NONE,
                _LABEL_BG_MODE_UI_WHITE,
                _LABEL_BG_MODE_UI_CUT,
            ),
            width=20,
            state="readonly",
        )
        self._angle_label_bg_mode_combo.grid(row=0, column=7, sticky="w", padx=(0, 8), pady=(2, 4))
        self._angle_label_bg_mode_combo.bind("<<ComboboxSelected>>", self._on_angle_toggle_change)
        self._apply_combobox_style(self._angle_label_bg_mode_combo, self._ui_entry_bg)

        self._angle_base_frame = ttk.Frame(self._angle_editor_frame)
        ttk.Label(self._angle_base_frame, text="Radio:").grid(row=0, column=0, sticky="w", padx=(4, 4))
        self._angle_radius_entry = ttk.Entry(self._angle_base_frame, textvariable=self._angle_radius_var, width=6)
        self._angle_radius_entry.grid(row=0, column=1, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._angle_radius_entry, self._on_angle_field_commit)
        ttk.Label(self._angle_base_frame, text="Opac:").grid(row=0, column=2, sticky="w", padx=(4, 4))
        self._angle_sector_alpha_entry = ttk.Entry(self._angle_base_frame, textvariable=self._angle_sector_alpha_var, width=6)
        self._angle_sector_alpha_entry.grid(row=0, column=3, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._angle_sector_alpha_entry, self._on_angle_field_commit)

        self._angle_double_frame = ttk.Frame(self._angle_editor_frame)
        ttk.Label(self._angle_double_frame, text="Delta r:").grid(row=0, column=0, sticky="w", padx=(4, 4))
        self._angle_double_entry = ttk.Entry(
            self._angle_double_frame, textvariable=self._angle_double_delta_var, width=6
        )
        self._angle_double_entry.grid(row=0, column=1, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._angle_double_entry, self._on_angle_field_commit)
        ttk.Label(self._angle_double_frame, text="Arcos:").grid(row=0, column=2, sticky="w", padx=(4, 4))
        self._angle_arc_count_entry = ttk.Entry(
            self._angle_double_frame, textvariable=self._angle_arc_count_var, width=4
        )
        self._angle_arc_count_entry.grid(row=0, column=3, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._angle_arc_count_entry, self._on_angle_field_commit)

        self._angle_point_frame = ttk.Frame(self._angle_editor_frame)
        ttk.Label(self._angle_point_frame, text="Lambda:").grid(row=0, column=0, sticky="w", padx=(4, 4))
        self._angle_point_lambda_entry = ttk.Entry(
            self._angle_point_frame, textvariable=self._angle_point_lambda_var, width=6
        )
        self._angle_point_lambda_entry.grid(row=0, column=1, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._angle_point_lambda_entry, self._on_angle_field_commit)
        ttk.Label(self._angle_point_frame, text="r:").grid(row=0, column=2, sticky="w", padx=(4, 4))
        self._angle_point_r_entry = ttk.Entry(self._angle_point_frame, textvariable=self._angle_point_r_var, width=6)
        self._angle_point_r_entry.grid(row=0, column=3, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._angle_point_r_entry, self._on_angle_field_commit)

        self._angle_s_frame = ttk.Frame(self._angle_editor_frame)
        ttk.Label(self._angle_s_frame, text="L:").grid(row=0, column=0, sticky="w", padx=(4, 4))
        self._angle_s_len_entry = ttk.Entry(self._angle_s_frame, textvariable=self._angle_s_len_var, width=6)
        self._angle_s_len_entry.grid(row=0, column=1, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._angle_s_len_entry, self._on_angle_field_commit)
        ttk.Label(self._angle_s_frame, text="A:").grid(row=0, column=2, sticky="w", padx=(4, 4))
        self._angle_s_amp_entry = ttk.Entry(self._angle_s_frame, textvariable=self._angle_s_amp_var, width=6)
        self._angle_s_amp_entry.grid(row=0, column=3, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._angle_s_amp_entry, self._on_angle_field_commit)
        ttk.Label(self._angle_s_frame, text="n:").grid(row=0, column=4, sticky="w", padx=(4, 4))
        self._angle_s_count_entry = ttk.Entry(self._angle_s_frame, textvariable=self._angle_s_count_var, width=4)
        self._angle_s_count_entry.grid(row=0, column=5, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._angle_s_count_entry, self._on_angle_field_commit)
        ttk.Label(self._angle_s_frame, text="d:").grid(row=0, column=6, sticky="w", padx=(4, 4))
        self._angle_s_gap_entry = ttk.Entry(self._angle_s_frame, textvariable=self._angle_s_gap_var, width=6)
        self._angle_s_gap_entry.grid(row=0, column=7, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._angle_s_gap_entry, self._on_angle_field_commit)

        self._angle_rect_frame = ttk.Frame(self._angle_editor_frame)
        ttk.Label(self._angle_rect_frame, text="L:").grid(row=0, column=0, sticky="w", padx=(4, 4))
        self._angle_rect_len_entry = ttk.Entry(self._angle_rect_frame, textvariable=self._angle_rect_len_var, width=6)
        self._angle_rect_len_entry.grid(row=0, column=1, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._angle_rect_len_entry, self._on_angle_field_commit)
        ttk.Label(self._angle_rect_frame, text="H:").grid(row=0, column=2, sticky="w", padx=(4, 4))
        self._angle_rect_h_entry = ttk.Entry(self._angle_rect_frame, textvariable=self._angle_rect_h_var, width=6)
        self._angle_rect_h_entry.grid(row=0, column=3, sticky="w", padx=(0, 8), pady=(2, 4))
        self._bind_entry_commit(self._angle_rect_h_entry, self._on_angle_field_commit)
        self._update_angle_fields()

        self._polygon_editor_frame = ttk.LabelFrame(self._right_panel, text="Editor de poligono")
        self._polygon_editor_frame.pack(fill="x", pady=(8, 0))
        self._polygon_shade_chk = ttk.Checkbutton(
            self._polygon_editor_frame,
            text="Sombreado",
            variable=self._polygon_shade_var,
        )
        self._polygon_shade_chk.grid(row=0, column=0, sticky="w", padx=(4, 8), pady=(4, 4))
        ttk.Label(self._polygon_editor_frame, text="Opacidad (0-1):").grid(
            row=0, column=1, sticky="w", padx=(4, 4), pady=(4, 4)
        )
        self._polygon_shade_opacity_entry = ttk.Entry(
            self._polygon_editor_frame, textvariable=self._polygon_shade_opacity_var, width=6
        )
        self._polygon_shade_opacity_entry.grid(row=0, column=2, sticky="w", padx=(0, 8), pady=(4, 4))
        self._bind_entry_commit(self._polygon_shade_opacity_entry, self._on_polygon_field_commit)
        self._polygon_split_btn = ttk.Button(
            self._polygon_editor_frame, text="Separar lados", command=self._split_polygon_to_segments
        )
        self._polygon_split_btn.grid(row=1, column=0, sticky="w", padx=(4, 8), pady=(2, 4))

        self._circle_editor_frame = ttk.LabelFrame(self._right_panel, text="Editor de circunferencia")
        self._circle_editor_frame.pack(fill="x", pady=(8, 0))
        self._circle_dashed_chk = ttk.Checkbutton(
            self._circle_editor_frame,
            text="Discontinuo",
            variable=self._circle_dashed_var,
        )
        self._circle_dashed_chk.grid(row=0, column=0, sticky="w", padx=(4, 8), pady=(4, 4))
        self._circle_radius_chk = ttk.Checkbutton(
            self._circle_editor_frame,
            text="Radio",
            variable=self._circle_show_radius_var,
        )
        self._circle_radius_chk.grid(row=0, column=1, sticky="w", padx=(4, 8), pady=(4, 4))

        self._stroke_editor_frame = ttk.LabelFrame(self._right_panel, text="Editor de trazo")
        self._stroke_editor_frame.pack(fill="x", pady=(8, 0))
        self._stroke_dash_chk = ttk.Checkbutton(
            self._stroke_editor_frame,
            text="Discontinuo",
            variable=self._stroke_dash_enabled_var,
        )
        self._stroke_dash_chk.grid(row=0, column=0, sticky="w", padx=(4, 8), pady=(4, 4))
        ttk.Label(self._stroke_editor_frame, text="Patron:").grid(
            row=0, column=1, sticky="w", padx=(4, 4), pady=(4, 4)
        )
        self._stroke_dash_entry = ttk.Entry(
            self._stroke_editor_frame, textvariable=self._stroke_dash_var, width=10
        )
        self._stroke_dash_entry.grid(row=0, column=2, sticky="w", padx=(0, 8), pady=(4, 4))
        self._bind_entry_commit(self._stroke_dash_entry, self._on_stroke_dash_commit)

        self._global_frame = ttk.LabelFrame(self._right_panel, text="Globales")
        self._global_frame.pack(fill="x", pady=(8, 0))
        global_top = ttk.Frame(self._global_frame)
        global_top.pack(fill="x", padx=6, pady=(6, 2))
        global_top.columnconfigure(0, weight=1)
        global_top.columnconfigure(1, weight=1)
        global_top.columnconfigure(2, weight=1)

        style_box = ttk.LabelFrame(global_top, text="Estilo")
        style_box.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        ttk.Label(style_box, text="Grosor linea").grid(row=0, column=0, sticky="w", padx=(6, 4), pady=(4, 2))
        ttk.Entry(style_box, textvariable=self._global_stroke_var, width=7).grid(row=0, column=1, sticky="w", padx=(0, 10), pady=(4, 2))
        ttk.Label(style_box, text="Letras").grid(row=0, column=2, sticky="w", padx=(4, 4), pady=(4, 2))
        ttk.Entry(style_box, textvariable=self._global_font_size_var, width=7).grid(row=0, column=3, sticky="w", padx=(0, 6), pady=(4, 2))
        ttk.Label(style_box, text="Puntos").grid(row=1, column=0, sticky="w", padx=(6, 4), pady=(2, 2))
        ttk.Entry(style_box, textvariable=self._global_point_radius_var, width=7).grid(row=1, column=1, sticky="w", padx=(0, 10), pady=(2, 2))
        self._global_dash_chk = ttk.Checkbutton(
            style_box,
            text="Lineas dashed",
            variable=self._global_dash_enabled_var,
        )
        self._global_dash_chk.grid(row=1, column=2, sticky="w", padx=(4, 4), pady=(2, 2))
        self._global_dash_entry = ttk.Entry(style_box, textvariable=self._global_dash_var, width=7)
        self._global_dash_entry.grid(row=1, column=3, sticky="w", padx=(0, 6), pady=(2, 2))
        ttk.Label(style_box, text="Sep. etiquetas").grid(row=2, column=0, sticky="w", padx=(6, 4), pady=(2, 4))
        ttk.Entry(style_box, textvariable=self._global_label_offset_var, width=7).grid(row=2, column=1, sticky="w", padx=(0, 10), pady=(2, 4))
        ttk.Label(style_box, text="Flecha").grid(row=2, column=2, sticky="w", padx=(4, 4), pady=(2, 4))
        ttk.Entry(style_box, textvariable=self._global_arrow_size_var, width=7).grid(row=2, column=3, sticky="w", padx=(0, 6), pady=(2, 4))
        ttk.Button(style_box, text="Aplicar globales", command=self._apply_global_styles).grid(
            row=0, column=4, rowspan=3, sticky="ns", padx=(10, 6), pady=(4, 4)
        )

        scale_box = ttk.LabelFrame(global_top, text="Escala")
        scale_box.grid(row=0, column=1, sticky="nsew", padx=(0, 6))
        ttk.Label(scale_box, text="px/u actual").grid(row=0, column=0, sticky="w", padx=(6, 4), pady=(6, 2))
        ttk.Entry(scale_box, textvariable=self._scale_current, width=7).grid(row=0, column=1, sticky="w", padx=(0, 6), pady=(6, 2))
        ttk.Label(scale_box, text="nuevo").grid(row=1, column=0, sticky="w", padx=(6, 4), pady=(2, 4))
        ttk.Entry(scale_box, textvariable=self._scale_new, width=7).grid(row=1, column=1, sticky="w", padx=(0, 6), pady=(2, 4))
        ttk.Button(scale_box, text="Reescalar", command=self._apply_scale).grid(
            row=0, column=2, rowspan=2, sticky="ns", padx=(8, 6), pady=(6, 4)
        )

        quick_box = ttk.LabelFrame(global_top, text="Creacion rapida")
        quick_box.grid(row=0, column=2, sticky="nsew")
        self._global_join_points_chk = ttk.Checkbutton(
            quick_box,
            text="Unir 2 puntos",
            variable=self._segment_create_var,
            command=self._toggle_segment_create,
        )
        self._global_join_points_chk.grid(row=0, column=0, sticky="w", padx=(6, 8), pady=(4, 2))
        self._global_angle_create_chk = ttk.Checkbutton(
            quick_box,
            text="Crear angulo (3 pts / 2 seg)",
            variable=self._angle_create_var,
            command=self._toggle_angle_create,
        )
        self._global_angle_create_chk.grid(row=0, column=1, sticky="w", padx=(6, 8), pady=(4, 2))
        self._global_angle_obtuse_chk = ttk.Checkbutton(
            quick_box,
            text="Preferir obtuso",
            variable=self._angle_create_obtuse_var,
        )
        self._global_angle_obtuse_chk.grid(row=1, column=0, sticky="w", padx=(6, 8), pady=(0, 2))
        self._global_intersection_create_chk = ttk.Checkbutton(
            quick_box,
            text="Interseccion (2 obj)",
            variable=self._intersection_create_var,
            command=self._toggle_intersection_create,
        )
        self._global_intersection_create_chk.grid(row=1, column=1, sticky="w", padx=(6, 8), pady=(0, 2))
        self._global_curve_radius_create_chk = ttk.Checkbutton(
            quick_box,
            text="Radio (centro+curva)",
            variable=self._curve_radius_create_var,
            command=self._toggle_curve_radius_create,
        )
        self._global_curve_radius_create_chk.grid(row=2, column=0, sticky="w", padx=(6, 8), pady=(0, 4))
        self._global_projection_create_chk = ttk.Checkbutton(
            quick_box,
            text="Proyeccion ort.",
            variable=self._projection_create_var,
            command=self._toggle_projection_create,
        )
        self._global_projection_create_chk.grid(row=2, column=1, sticky="w", padx=(6, 8), pady=(0, 4))

        shade_box = ttk.LabelFrame(self._global_frame, text="Sombreado")
        shade_box.pack(fill="x", padx=6, pady=(2, 6))
        self._global_shade_row = ttk.Frame(shade_box)
        self._global_shade_row.pack(side="left", fill="x", padx=(6, 10), pady=4)
        self._global_shade_diff_chk = ttk.Checkbutton(
            self._global_shade_row,
            text="Sombreado (contorno)",
            variable=self._shade_diff_var,
            command=self._toggle_shade_diff,
        )
        self._global_shade_diff_chk.pack(side="left", padx=(0, 8))
        ttk.Label(self._global_shade_row, text="Opac.").pack(side="left", padx=(0, 2))
        self._global_shade_diff_opacity_entry = ttk.Entry(
            self._global_shade_row, textvariable=self._shade_diff_opacity_var, width=6
        )
        self._global_shade_diff_opacity_entry.pack(side="left", padx=(0, 4))
        self._bind_entry_commit(self._global_shade_diff_opacity_entry, self._on_shade_diff_opacity_commit)
        self._global_shade_diff_apply_btn = ttk.Button(
            self._global_shade_row, text="Aplicar", command=self._apply_shade_diff_selection
        )
        self._global_shade_diff_apply_btn.pack(side="left", padx=(0, 4))
        self._global_shade_diff_clear_btn = ttk.Button(
            self._global_shade_row, text="Limpiar seleccion", command=self._clear_shade_diff_selection
        )
        self._global_shade_diff_clear_btn.pack(side="left")
        self._global_shade_selected_row = ttk.Frame(shade_box)
        self._global_shade_selected_row.pack(side="left", fill="x", padx=(10, 6), pady=4)
        ttk.Label(self._global_shade_selected_row, text="Intensidad sombreado sel.").pack(side="left", padx=(0, 4))
        self._global_shade_selected_opacity_entry = ttk.Entry(
            self._global_shade_selected_row, textvariable=self._shade_selected_opacity_var, width=6
        )
        self._global_shade_selected_opacity_entry.pack(side="left", padx=(0, 4))
        self._bind_entry_commit(self._global_shade_selected_opacity_entry, self._on_selected_shade_opacity_commit)
        self._global_shade_selected_apply_btn = ttk.Button(
            self._global_shade_selected_row, text="Aplicar intensidad", command=self._apply_selected_shade_opacity
        )
        self._global_shade_selected_apply_btn.pack(side="left")
        self._global_shade_selected_opacity_entry.configure(state="disabled")
        self._global_shade_selected_apply_btn.configure(state="disabled")
        self._global_status_lbl = ttk.Label(
            self._global_frame,
            textvariable=self._transform_status_var,
        )
        self._global_status_lbl.pack(fill="x", padx=8, pady=(0, 6))
        self._apply_minimal_v1_ui()

    def _bind_graph_canvas(self, canvas: tk.Canvas) -> None:
        canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        canvas.bind("<B1-Motion>", self._on_canvas_drag)
        canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        canvas.bind("<Double-Button-1>", self._on_canvas_double_click)
        canvas.bind("<Control-z>", self._on_undo_key)
        canvas.bind("<Control-y>", self._on_redo_key)
        canvas.bind("<Control-MouseWheel>", self._on_zoom_wheel)
        canvas.bind("<Alt-MouseWheel>", self._on_hscroll_wheel)
        canvas.bind("<Shift-MouseWheel>", self._on_vscroll_wheel)

    def _open_floating_graph(self) -> None:
        if self._floating_graph_window is not None:
            try:
                self._floating_graph_window.lift()
                self._floating_graph_window.focus_force()
            except Exception:
                pass
            return

        win = tk.Toplevel(self)
        win.title("Grafico SVG - ventana flotante")
        win.geometry("1100x760")
        win.configure(bg=self._ui_bg)
        win.protocol("WM_DELETE_WINDOW", self._close_floating_graph)

        top = ttk.Frame(win)
        top.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(
            top,
            text="Vista flotante activa: selecciona y arrastra aqui; las herramientas quedan en la ventana principal.",
        ).pack(side="left")
        ttk.Button(top, text="Volver al panel", command=self._close_floating_graph).pack(side="right")

        frame = ttk.Frame(win, padding=(8, 4, 8, 8))
        frame.pack(fill="both", expand=True)
        float_canvas = tk.Canvas(frame, bg="#ffffff", highlightthickness=0)
        float_canvas.pack(side="left", fill="both", expand=True)
        sb_y = ttk.Scrollbar(frame, orient="vertical", command=float_canvas.yview)
        sb_y.pack(side="right", fill="y")
        sb_x = ttk.Scrollbar(win, orient="horizontal", command=float_canvas.xview)
        sb_x.pack(fill="x", padx=8, pady=(0, 8))
        float_canvas.configure(xscrollcommand=sb_x.set, yscrollcommand=sb_y.set)
        self._bind_graph_canvas(float_canvas)

        self._floating_graph_window = win
        self._floating_canvas = float_canvas
        self.canvas = float_canvas
        if self._floating_graph_btn is not None:
            try:
                self._floating_graph_btn.configure(text="Grafico flotante activo")
            except Exception:
                pass
        self._render_svg()
        try:
            float_canvas.focus_set()
        except Exception:
            pass

    def _close_floating_graph(self) -> None:
        win = self._floating_graph_window
        self._floating_graph_window = None
        self._floating_canvas = None
        if self._embedded_canvas is not None:
            self.canvas = self._embedded_canvas
        if self._floating_graph_btn is not None:
            try:
                self._floating_graph_btn.configure(text="Grafico flotante")
            except Exception:
                pass
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
        try:
            self._render_svg()
        except Exception:
            pass

    def _apply_minimal_v1_ui(self) -> None:
        if not bool(getattr(self, "_minimal_v1_globales_only", False)):
            return
        for attr in (
            "_bg_frame",
            "_tools_frame",
            "_transform_frame",
            "_segment_editor_frame",
            "_curve_editor_frame",
            "_angle_editor_frame",
            "_polygon_editor_frame",
            "_circle_editor_frame",
            "_stroke_editor_frame",
        ):
            widget = getattr(self, attr, None)
            if widget is None:
                continue
            try:
                widget.pack_forget()
            except Exception:
                continue
        point_frame = getattr(self, "_point_editor_frame", None)
        if point_frame is not None:
            try:
                point_frame.pack_forget()
            except Exception:
                pass

    def _update_line_numbers(self) -> None:
        if self._line_numbers is None:
            return
        line_count = int(self.text_input.index("end-1c").split(".")[0])
        lines = "\n".join(str(i) for i in range(1, line_count + 1))
        self._line_numbers.configure(state="normal")
        self._line_numbers.delete("1.0", "end")
        self._line_numbers.insert("1.0", lines)
        self._line_numbers.configure(state="disabled")
        try:
            self._line_numbers.yview_moveto(self.text_input.yview()[0])
        except Exception:
            pass

    def _on_text_scroll(self, *args) -> None:
        self.text_input.yview(*args)
        if self._line_numbers is not None:
            self._line_numbers.yview(*args)

    def _on_text_yscroll(self, first: str, last: str) -> None:
        if self._text_scrollbar is not None:
            self._text_scrollbar.set(first, last)
        if self._line_numbers is not None:
            try:
                self._line_numbers.yview_moveto(first)
            except Exception:
                pass

    def _on_text_modified(self, _event=None) -> None:
        if self._suspend_text_sync:
            return
        if self.text_input.edit_modified():
            self.text_input.edit_modified(False)
        self._clear_code_highlight()
        self._update_line_numbers()

    def _toggle_code_panel(self) -> None:
        visible = bool(self._code_panel_visible.get())
        if visible:
            try:
                self._code_sash_pos = int(self._main_pane.sashpos(0))
            except Exception:
                self._code_sash_pos = None
            try:
                self._main_pane.paneconfigure(self._left_panel, hide=True)
            except Exception:
                try:
                    self._main_pane.forget(self._left_panel)
                except Exception:
                    pass
            self._code_panel_visible.set(False)
            if hasattr(self, "_code_toggle_btn"):
                self._code_toggle_btn.configure(text="Mostrar codigo")
            return

        panes = []
        try:
            panes = list(self._main_pane.panes())
        except Exception:
            panes = []
        if str(self._left_panel) not in panes:
            try:
                self._main_pane.insert(0, self._left_panel)
            except Exception:
                try:
                    self._main_pane.add(self._left_panel)
                except Exception:
                    pass
        try:
            self._main_pane.paneconfigure(self._left_panel, hide=False)
        except Exception:
            pass
        try:
            self._main_pane.paneconfigure(self._left_panel, weight=1, minsize=240)
        except Exception:
            pass
        if self._code_sash_pos is None:
            self._code_sash_pos = 340
        try:
            target = int(self._code_sash_pos)
        except Exception:
            target = 340
        try:
            self.after(1, lambda: self._main_pane.sashpos(0, target))
        except Exception:
            pass
        self._code_panel_visible.set(True)
        if hasattr(self, "_code_toggle_btn"):
            self._code_toggle_btn.configure(text="Ocultar codigo")

    def _is_point_circle(self, el: ET.Element) -> bool:
        return _circle_is_point_like(el, self._class_styles)

    def _is_split_point_circle(self, el: ET.Element) -> bool:
        if _strip_ns(el.tag) != "circle":
            return False
        kind = (el.get("data-kind") or "").strip()
        if kind and _is_aux_data_kind(kind):
            return False
        # Prefer explicit geometric metadata.
        if kind == "point":
            return True
        if (el.get("data-point-kind") or "").strip().lower() == "intersection":
            return True
        if (el.get("data-constraint-intersection-of") or "").strip():
            return True
        # Legacy/imported SVGs may omit metadata for geometric points.
        # Accept point-like circles as split candidates so double click can
        # still split/select subelements reliably.
        class_styles = getattr(self, "_class_styles", {}) or {}
        return _circle_is_point_like(el, class_styles)

    def _normalize_geometric_metadata(self, root: ET.Element) -> bool:
        class_styles = self._collect_css_class_styles(root)
        changed = False
        for el in root.iter():
            if _strip_ns(el.tag) != "circle":
                continue
            kind = (el.get("data-kind") or "").strip()
            if kind == "point":
                continue
            if _is_aux_data_kind(kind):
                continue
            if not _circle_is_point_like(el, class_styles):
                continue
            el.set("data-kind", "point")
            changed = True
        return changed

    def _normalize_imported_labels_for_editor(self) -> bool:
        if self._svg_root is None:
            return False
        changed = False
        points_by_id: dict[str, ET.Element] = {}
        for el in self._svg_root.iter():
            if _strip_ns(el.tag) != "circle":
                continue
            if not self._is_point_circle(el):
                continue
            pid = (el.get("data-point-id") or "").strip()
            if pid:
                points_by_id[pid] = el

        def _point_anchor(point_el: ET.Element) -> tuple[float, float]:
            return (_parse_float(_get_attr(point_el, "cx")), _parse_float(_get_attr(point_el, "cy")))

        for el in list(self._svg_root.iter()):
            tag = _strip_ns(el.tag)
            if tag not in ("text", "path"):
                continue
            if tag == "path" and el.get("data-text") is None:
                continue
            if el.get("data-angle-id") is not None:
                continue
            if (el.get("data-angle-kind") or "").strip() == "label":
                continue
            if (el.get("data-kind") or "").strip() in ("seg-endpoint-label", "seg-mid-label"):
                continue

            text = (el.text or "").strip() if tag == "text" else (el.get("data-text") or "").strip()
            if not text:
                continue

            point_el: ET.Element | None = None
            point_id = (el.get("data-point-id") or "").strip()
            if point_id:
                point_el = points_by_id.get(point_id)
            if point_el is None:
                point_el = points_by_id.get(text)
                if point_el is not None and not point_id:
                    el.set("data-point-id", text)
                    changed = True
            if point_el is None:
                raw_ax = el.get("data-anchor-x")
                raw_ay = el.get("data-anchor-y")
                if raw_ax is not None and raw_ay is not None:
                    candidate = self._find_point_by_anchor(_parse_float(raw_ax), _parse_float(raw_ay))
                    if candidate is not None:
                        candidate_id = (candidate.get("data-point-id") or "").strip()
                        # Imported SVGs may place angle labels and point labels as
                        # identical path elements. Only bind by anchor when the
                        # visible label agrees with the point identity; otherwise a
                        # point near an angle can accidentally "steal" the angle label.
                        if not candidate_id or candidate_id == text:
                            point_el = candidate
            if point_el is None:
                continue

            ax, ay = _point_anchor(point_el)
            x, y = self._label_position(el)
            latex = tag == "path"
            font_size = _parse_float(
                el.get("data-font-size"),
                _parse_float(self._effective_attr(el, "font-size"), 12.0),
            )
            raw_dir = (el.get("data-dir") or "").strip()
            dir_s = _normalize_dir_input(raw_dir)
            if not _is_valid_dir(dir_s):
                dir_s, inferred_offset = self._infer_label_dir_offset(ax, ay, x, y, text, font_size, latex)
            else:
                _dir_for_offset, inferred_offset = self._infer_label_dir_offset(ax, ay, x, y, text, font_size, latex)
            raw_offset = el.get("data-offset")
            offset = _parse_float(raw_offset, float("nan"))
            if not math.isfinite(offset) or offset < 0:
                offset = inferred_offset
            if not math.isfinite(offset) or offset < 0:
                offset = _parse_float(self._global_label_offset_var.get().strip(), 10.0)

            before = dict(el.attrib)
            el.set("data-anchor-x", _format_num(ax))
            el.set("data-anchor-y", _format_num(ay))
            el.set("data-dir", dir_s)
            el.set("data-offset", _format_num(offset))
            if tag == "path":
                el.set("data-font-size", _format_num(font_size))
                _set_attr(el, "font-size", _format_num(font_size))
            nx, ny = _label_position_from_anchor(ax, ay, text, dir_s, offset, font_size, latex)
            old_x, old_y = self._label_position(el)
            if abs(old_x - nx) > 1e-6 or abs(old_y - ny) > 1e-6:
                self._set_label_position(el, nx, ny)
                changed = True
            if dict(el.attrib) != before:
                changed = True
        return changed

    def _normalize_imported_svg_for_editor(self) -> bool:
        """Convert raw/imported SVG geometry into the editor's working model."""
        if self._svg_root is None:
            return False
        changed = self._normalize_geometric_metadata(self._svg_root)
        self._class_styles = self._collect_css_class_styles(self._svg_root)
        self._rebuild_svg_parent_map()
        if self._normalize_imported_labels_for_editor():
            changed = True

        split_targets: list[tuple[ET.Element, list[tuple[float, float, float]]]] = []
        for el in list(self._svg_root.iter()):
            if _strip_ns(el.tag) != "line":
                continue
            kind = (el.get("data-kind") or "").strip()
            if kind in ("subsegment", "seg-mark", "circle-radius", _CURVE_RADIUS_DATA_KIND):
                continue
            if _is_aux_data_kind(kind):
                continue
            if (el.get("data-hidden-parent") or "").strip() == "1":
                continue
            stroke = str(self._effective_attr(el, "stroke") or "").strip().lower()
            if stroke in ("none", "transparent"):
                continue
            self._ensure_element_id(el, prefix="shape")
            if not str(_get_attr(el, "stroke") or "").strip():
                inherited_stroke = self._effective_attr(el, "stroke")
                if inherited_stroke:
                    _set_attr(el, "stroke", inherited_stroke)
            if not str(_get_attr(el, "stroke-width") or "").strip():
                inherited_sw = self._effective_attr(el, "stroke-width")
                if inherited_sw:
                    _set_attr(el, "stroke-width", inherited_sw)
            if not str(_get_attr(el, "fill") or "").strip():
                inherited_fill = self._effective_attr(el, "fill")
                if inherited_fill:
                    _set_attr(el, "fill", inherited_fill)
            if not (el.get("data-mark-key") or "").strip():
                mark_key = self._segment_mark_key(el, create=False)
                if mark_key:
                    el.set("data-mark-key", mark_key)
            changed = True
            try:
                points = self._points_on_line(el, tol_px=5.0, zoom=1.0)
            except Exception:
                points = []
            if len(points) >= 3:
                split_targets.append((el, points))

        for line_el, points in split_targets:
            self._split_line_on_points(line_el, points)
            changed = True
        if changed:
            self._rebuild_svg_parent_map()

        return changed

    def _set_transform_status(self, msg: str) -> None:
        if hasattr(self, "_transform_status_var"):
            self._transform_status_var.set(msg)

    def _snap_tolerance_px(self) -> float:
        raw = ""
        if hasattr(self, "_snap_tol_var"):
            raw = self._snap_tol_var.get().strip()
        try:
            tol = float(raw)
        except Exception:
            tol = _DEFAULT_SNAP_TOL_PX
        if tol <= 0:
            tol = _DEFAULT_SNAP_TOL_PX
        tol = max(1.0, min(200.0, tol))
        if hasattr(self, "_snap_tol_var"):
            self._snap_tol_var.set(_format_num(tol))
        return tol

    def _on_transform_field_commit(self, _event=None) -> None:
        self._snap_tolerance_px()
        if not bool(self._snap_enabled_var.get()):
            self._set_transform_status("Snap desactivado.")
            return
        self._set_transform_status(f"Snap activo ({self._snap_tol_var.get()} px).")

    def _is_locked_point(self, el: ET.Element) -> tuple[bool, str | None]:
        reason = _locked_point_reason_from_attrs(el.attrib)
        return (reason is not None, reason)

    def _pick_draggable_point(
        self,
        x: float,
        y: float,
        zoom: float,
    ) -> tuple[_Record, ET.Element, float, float, str | None] | None:
        candidates = self._collect_hit_candidates(x, y, zoom, kinds=("circle",))
        for dist, d in candidates:
            if d.record is None:
                continue
            el = d.record.el
            if _strip_ns(el.tag) != "circle":
                continue
            if not self._is_point_circle(el):
                continue
            if dist > 6.0:
                continue
            if el.get("data-angle-id") is not None or el.get("data-angle-kind") == "point":
                continue
            kind = (el.get("data-kind") or "").strip()
            if kind in ("seg-mark", "seg-endpoint"):
                continue
            cx = _parse_float(_get_attr(el, "cx"))
            cy = _parse_float(_get_attr(el, "cy"))
            locked, reason = self._is_locked_point(el)
            return (d.record, el, cx, cy, reason if locked else None)
        return None

    def _pick_draggable_radius_endpoint(
        self,
        x: float,
        y: float,
        zoom: float,
    ) -> tuple[_Record, ET.Element] | None:
        candidates = self._collect_hit_candidates(x, y, zoom, kinds=("line",))
        best: tuple[float, _Record, ET.Element] | None = None
        for _dist, d in candidates:
            if d.record is None:
                continue
            el = d.record.el
            if _strip_ns(el.tag) != "line":
                continue
            kind = (el.get("data-kind") or "").strip()
            if kind not in ("circle-radius", _CURVE_RADIUS_DATA_KIND):
                continue
            ex = (_parse_float(_get_attr(el, "x2")) + self._shift_x) * zoom
            ey = (_parse_float(_get_attr(el, "y2")) + self._shift_y) * zoom
            end_dist = math.hypot(x - ex, y - ey)
            if end_dist > 8.0:
                continue
            if best is None or end_dist < best[0]:
                best = (end_dist, d.record, el)
        if best is None:
            return None
        return (best[1], best[2])

    def _element_by_id(self, el_id: str | None) -> ET.Element | None:
        if self._svg_root is None:
            return None
        target_id = (el_id or "").strip()
        if not target_id:
            return None
        for el in self._svg_root.iter():
            if (el.get("id") or "").strip() == target_id:
                return el
        return None

    def _update_radius_endpoint_by_drag(
        self,
        radius_el: ET.Element,
        *,
        target_x: float,
        target_y: float,
        zoom: float,
    ) -> bool:
        kind = (radius_el.get("data-kind") or "").strip()
        if kind == "circle-radius":
            circle_el = self._circle_for_radius_line(radius_el)
            if circle_el is None:
                return False
            cx = _parse_float(_get_attr(circle_el, "cx"))
            cy = _parse_float(_get_attr(circle_el, "cy"))
            r = _parse_float(_get_attr(circle_el, "r"), 0.0)
            if r <= 0:
                return False
            dx = target_x - cx
            dy = target_y - cy
            if math.hypot(dx, dy) <= 1e-9:
                dx = 1.0
                dy = 0.0
            ang = math.atan2(dy, dx)
            radius_el.set("data-radius-angle", _format_num(ang))
            qx = cx + r * math.cos(ang)
            qy = cy + r * math.sin(ang)
            radius_el.set("x1", _format_num(cx))
            radius_el.set("y1", _format_num(cy))
            radius_el.set("x2", _format_num(qx))
            radius_el.set("y2", _format_num(qy))
            return True
        if kind != _CURVE_RADIUS_DATA_KIND:
            return False
        center_id = (radius_el.get("data-radius-center-id") or "").strip()
        curve_id = (radius_el.get("data-radius-curve-id") or "").strip()
        center_el = self._element_by_id(center_id)
        curve_el = self._element_by_id(curve_id)
        if center_el is None or curve_el is None:
            return False
        if _strip_ns(center_el.tag) != "circle" or not self._is_point_circle(center_el):
            return False
        if not self._is_curve_radius_curve_candidate(curve_el):
            return False
        projected = self._project_radius_target_on_curve(curve_el, target_x, target_y)
        if projected is None:
            return False
        s_use, qx, qy, _closed, _total = projected
        cx = _parse_float(_get_attr(center_el, "cx"))
        cy = _parse_float(_get_attr(center_el, "cy"))
        radius_el.set("x1", _format_num(cx))
        radius_el.set("y1", _format_num(cy))
        radius_el.set("x2", _format_num(qx))
        radius_el.set("y2", _format_num(qy))
        radius_el.set("data-radius-s", _format_num(s_use))
        return True

    def _clear_drag_state(self) -> None:
        self._drag_point_el = None
        self._drag_radius_el = None
        self._drag_point_start = None
        self._drag_mouse_start = None
        self._drag_active = False
        self._drag_moved = False
        self._drag_history_before = None

    def _begin_drag_history(self) -> None:
        if self._svg_root is None:
            self._drag_history_before = None
            return
        self._drag_history_before = ET.tostring(self._svg_root, encoding="unicode")

    def _commit_drag_history(self) -> None:
        if self._svg_root is None:
            self._drag_history_before = None
            return
        before = self._drag_history_before
        self._drag_history_before = None
        if not before:
            return
        after = ET.tostring(self._svg_root, encoding="unicode")
        if after == before:
            return
        if self._history_index < 0:
            self._history = [before]
            self._history_index = 0
        elif self._history_index >= len(self._history):
            self._history_index = len(self._history) - 1
        if self._history_index < len(self._history) - 1:
            self._history = self._history[: self._history_index + 1]
        if not self._history or self._history[self._history_index] != before:
            self._history.append(before)
            self._history_index = len(self._history) - 1
        if self._history[self._history_index] != after:
            self._history.append(after)
            self._history_index = len(self._history) - 1

    def _snap_to_anchor(
        self,
        target_x: float,
        target_y: float,
        zoom: float,
        *,
        exclude_anchor: tuple[float, float] | None = None,
    ) -> tuple[float, float, bool]:
        if not bool(self._snap_enabled_var.get()):
            return (target_x, target_y, False)
        tol_units = self._snap_tolerance_px() / max(zoom, 1e-6)
        return _snap_anchor_point(
            target_x,
            target_y,
            self._anchor_points,
            tol_units,
            exclude_anchor=exclude_anchor,
        )

    def _propagate_point_move(
        self,
        point_el: ET.Element,
        old_xy: tuple[float, float],
        new_xy: tuple[float, float],
        zoom: float,
    ) -> None:
        if self._svg_root is None:
            return
        tol = max(1e-9, 2.0 / max(zoom, 1e-6))
        lines, circles = _propagate_point_move_model(self._svg_root, point_el, old_xy, new_xy, tol)
        self._update_labels_for_anchor(old_xy[0], old_xy[1], new_xy[0], new_xy[1])
        for line_el in lines:
            self._sync_subsegments_from_parent(line_el)
            self._rebuild_segment_marks_from_line(line_el)
            self._sync_segment_endpoints_from_line(line_el)
            self._sync_segment_mid_labels_from_line(line_el)
            self._rebuild_segment_dimension_from_line(line_el)
        for circle_el in circles:
            self._sync_circle_radius_from_circle(circle_el)
        self._apply_constraints()
        self._anchor_points = self._collect_anchor_points(self._svg_root)

    def _is_intersection_candidate(self, el: ET.Element) -> bool:
        tag = _strip_ns(el.tag)
        if tag not in ("line", "polyline", "polygon", "path", "circle", "ellipse", "rect"):
            return False
        if tag == "path" and el.get("data-text") is not None:
            return False
        kind = (el.get("data-kind") or "").strip()
        if _is_aux_data_kind(kind) and kind not in ("subsegment", "circle-radius"):
            return False
        if self._is_shade_contour_helper(el):
            return False
        if tag == "circle" and self._is_point_circle(el):
            return False
        if self._intersection_candidate_is_hidden(el, tag):
            return False
        return True

    def _intersection_candidate_is_hidden(self, el: ET.Element, tag: str) -> bool:
        display = (self._effective_attr(el, "display") or "").strip().lower()
        visibility = (self._effective_attr(el, "visibility") or "").strip().lower()
        if display == "none" or visibility == "hidden":
            return True
        stroke = (self._effective_attr(el, "stroke") or "").strip().lower()
        fill = (self._effective_attr(el, "fill") or "").strip().lower()
        stroke_hidden = stroke in ("", "none", "transparent")
        fill_hidden = fill in ("", "none", "transparent")
        if tag in ("line", "polyline"):
            return stroke_hidden
        if tag in ("polygon", "rect", "circle", "ellipse", "path"):
            return stroke_hidden and fill_hidden
        return False

    def _element_in_svg(self, target: ET.Element | None) -> bool:
        if target is None or self._svg_root is None:
            return False
        for el in self._svg_root.iter():
            if el is target:
                return True
        return False

    def _svg_ns(self) -> str:
        if self._svg_root is None:
            return ""
        tag = self._svg_root.tag
        if tag.startswith("{") and "}" in tag:
            return tag.split("}", 1)[0][1:]
        return ""

    def _svg_ns_tag(self, name: str) -> str:
        ns = self._svg_ns()
        return f"{{{ns}}}{name}" if ns else name

    def _ensure_defs_node(self) -> ET.Element | None:
        if self._svg_root is None:
            return None
        for child in list(self._svg_root):
            if _strip_ns(child.tag) == "defs":
                return child
        defs = ET.Element(self._svg_ns_tag("defs"))
        self._svg_root.insert(0, defs)
        return defs

    def _next_generic_id(self, prefix: str = "obj") -> str:
        if self._svg_root is None:
            return f"{prefix}-1"
        used: set[str] = set()
        for el in self._svg_root.iter():
            el_id = (el.get("id") or "").strip()
            if el_id:
                used.add(el_id)
        idx = 1
        while True:
            cand = f"{prefix}-{idx}"
            if cand not in used:
                return cand
            idx += 1

    def _ensure_element_id(self, el: ET.Element, *, prefix: str = "obj") -> str:
        cur = (el.get("id") or "").strip()
        if cur:
            return cur
        new_id = self._next_generic_id(prefix=prefix)
        el.set("id", new_id)
        return new_id

    def _parse_id_list(self, raw: str | None) -> list[str]:
        if not raw:
            return []
        return [part.strip() for part in str(raw).split(",") if part.strip()]

    def _path_has_closed_subpath(self, el: ET.Element) -> bool:
        if _strip_ns(el.tag) != "path":
            return False
        if el.get("data-text") is not None:
            return False
        d = _get_attr(el, "d") or ""
        for pts, closed in _parse_svg_path(d):
            if closed and len(pts) >= 3:
                return True
        return False

    def _is_shade_diff_candidate(self, el: ET.Element) -> bool:
        tag = _strip_ns(el.tag)
        kind = (el.get("data-kind") or "").strip()
        if _is_aux_data_kind(kind):
            return False
        if tag == "polygon":
            coords = _parse_points(_get_attr(el, "points"))
            return len(coords) >= 6
        if tag == "circle":
            return self._is_editable_circle(el)
        if tag == "ellipse":
            rx = _parse_float(_get_attr(el, "rx"), 0.0)
            ry = _parse_float(_get_attr(el, "ry"), 0.0)
            return rx > 0 and ry > 0
        if tag == "rect":
            if kind in ("background", "label-bg"):
                return False
            w = _parse_float(_get_attr(el, "width"), 0.0)
            h = _parse_float(_get_attr(el, "height"), 0.0)
            return w > 0 and h > 0
        if tag == "path":
            return self._path_has_closed_subpath(el)
        return False

    def _is_shade_contour_helper(self, el: ET.Element | None) -> bool:
        if el is None:
            return False
        if _strip_ns(el.tag) != "path":
            return False
        if (el.get(_SHADE_CONTOUR_DATA_FLAG) or "").strip() == "1":
            return True
        return (el.get("data-kind") or "").strip() == _SHADE_CONTOUR_DATA_KIND

    def _is_shade_contour_source_candidate(self, el: ET.Element) -> bool:
        if self._is_shade_contour_helper(el):
            return False
        # Contour shading must be built from real geometric borders. Imported
        # SVGs often contain angle arcs/sectors, labels converted to paths, and
        # segment marks near the same clicks; accepting those makes ChatGPT-made
        # line segments feel unselectable.
        if el.get("data-angle-id") is not None or el.get("data-angle-kind") is not None:
            return False
        if el.get("data-text") is not None:
            return False
        tag = _strip_ns(el.tag)
        kind = (el.get("data-kind") or "").strip()
        if kind == "subsegment":
            if tag == "line":
                x1 = _parse_float(_get_attr(el, "x1"))
                y1 = _parse_float(_get_attr(el, "y1"))
                x2 = _parse_float(_get_attr(el, "x2"))
                y2 = _parse_float(_get_attr(el, "y2"))
                return math.hypot(x2 - x1, y2 - y1) > 1e-9
            if tag == "path":
                d = _get_attr(el, "d") or ""
                for pts, _closed in _parse_svg_path(d):
                    if len(pts) >= 2:
                        return True
                return False
            return False
        if _is_aux_data_kind(kind):
            return False
        if kind in ("label-overlay", "label", "text", "point"):
            return False
        if tag == "line":
            x1 = _parse_float(_get_attr(el, "x1"))
            y1 = _parse_float(_get_attr(el, "y1"))
            x2 = _parse_float(_get_attr(el, "x2"))
            y2 = _parse_float(_get_attr(el, "y2"))
            return math.hypot(x2 - x1, y2 - y1) > 1e-9
        if tag == "polyline":
            coords = _parse_points(_get_attr(el, "points"))
            return len(coords) >= 4
        if tag == "polygon":
            coords = _parse_points(_get_attr(el, "points"))
            return len(coords) >= 6
        if tag == "rect":
            if kind in ("background", "label-bg"):
                return False
            w = _parse_float(_get_attr(el, "width"), 0.0)
            h = _parse_float(_get_attr(el, "height"), 0.0)
            return w > 0 and h > 0
        if tag == "path":
            d = _get_attr(el, "d") or ""
            for pts, _closed in _parse_svg_path(d):
                if len(pts) >= 2:
                    return True
            return False
        if tag == "circle":
            return self._is_editable_circle(el)
        if tag == "ellipse":
            rx = _parse_float(_get_attr(el, "rx"), 0.0)
            ry = _parse_float(_get_attr(el, "ry"), 0.0)
            return rx > 0 and ry > 0
        return False

    def _shade_contour_pick_subpath(self, el: ET.Element) -> tuple[list[tuple[float, float]], bool]:
        if _strip_ns(el.tag) != "path":
            return ([], False)
        d = _get_attr(el, "d") or ""
        subpaths = [(pts, closed) for pts, closed in _parse_svg_path(d) if len(pts) >= 2]
        if not subpaths:
            return ([], False)
        for pts, closed in subpaths:
            if closed and len(pts) >= 3:
                out = list(pts)
                if len(out) >= 2 and math.hypot(out[0][0] - out[-1][0], out[0][1] - out[-1][1]) <= 1e-9:
                    out.pop()
                return (out, True)
        pts, closed = subpaths[0]
        return (list(pts), bool(closed))

    def _shade_contour_source_points(
        self,
        el: ET.Element,
        *,
        direction: int = 0,
    ) -> tuple[list[tuple[float, float]], bool]:
        tag = _strip_ns(el.tag)
        points: list[tuple[float, float]] = []
        closed = False
        if tag == "line":
            x1 = _parse_float(_get_attr(el, "x1"))
            y1 = _parse_float(_get_attr(el, "y1"))
            x2 = _parse_float(_get_attr(el, "x2"))
            y2 = _parse_float(_get_attr(el, "y2"))
            if math.hypot(x2 - x1, y2 - y1) <= 1e-9:
                return ([], False)
            points = [(x1, y1), (x2, y2)]
        elif tag == "polyline":
            coords = _parse_points(_get_attr(el, "points"))
            points = [(coords[i], coords[i + 1]) for i in range(0, len(coords) - 1, 2)]
            if len(points) < 2:
                return ([], False)
        elif tag == "polygon":
            coords = _parse_points(_get_attr(el, "points"))
            points = [(coords[i], coords[i + 1]) for i in range(0, len(coords) - 1, 2)]
            if len(points) < 3:
                return ([], False)
            closed = True
        elif tag == "rect":
            rx = _parse_float(_get_attr(el, "x"))
            ry = _parse_float(_get_attr(el, "y"))
            w = _parse_float(_get_attr(el, "width"), 0.0)
            h = _parse_float(_get_attr(el, "height"), 0.0)
            if w <= 0 or h <= 0:
                return ([], False)
            points = [(rx, ry), (rx + w, ry), (rx + w, ry + h), (rx, ry + h)]
            closed = True
        elif tag == "path":
            points, closed = self._shade_contour_pick_subpath(el)
            if len(points) < (3 if closed else 2):
                return ([], False)
        elif tag == "circle":
            cx = _parse_float(_get_attr(el, "cx"))
            cy = _parse_float(_get_attr(el, "cy"))
            r = _parse_float(_get_attr(el, "r"), 0.0)
            if r <= 0:
                return ([], False)
            points = self._ellipse_points(cx, cy, r, r)
            closed = True
        elif tag == "ellipse":
            cx = _parse_float(_get_attr(el, "cx"))
            cy = _parse_float(_get_attr(el, "cy"))
            rx = _parse_float(_get_attr(el, "rx"), 0.0)
            ry = _parse_float(_get_attr(el, "ry"), 0.0)
            if rx <= 0 or ry <= 0:
                return ([], False)
            points = self._ellipse_points(cx, cy, rx, ry)
            closed = True
        else:
            return ([], False)
        if direction == 1:
            points = list(reversed(points))
        if closed and len(points) >= 2 and math.hypot(points[0][0] - points[-1][0], points[0][1] - points[-1][1]) <= 1e-9:
            points = points[:-1]
        return (points, closed)

    def _shade_contour_source_endpoints(
        self,
        el: ET.Element,
        *,
        direction: int = 0,
    ) -> tuple[tuple[float, float], tuple[float, float]] | None:
        points, closed = self._shade_contour_source_points(el, direction=direction)
        if closed or len(points) < 2:
            return None
        return (points[0], points[-1])

    def _shade_contour_gap(self, a: tuple[float, float], b: tuple[float, float]) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    def _shade_contour_tol_svg(self, zoom: float) -> float:
        return max(1e-6, _DEFAULT_SHADE_CONTOUR_TOL_PX / max(zoom, 1e-6))

    def _shade_contour_points_from_edges(
        self,
        edges: list[tuple[ET.Element, int]],
        *,
        tol: float,
    ) -> tuple[list[tuple[float, float]], bool]:
        if not edges:
            return ([], False)
        all_pts: list[tuple[float, float]] = []
        for idx, (el, direction) in enumerate(edges):
            pts, closed = self._shade_contour_source_points(el, direction=direction)
            if not pts:
                return ([], False)
            if closed:
                if len(edges) != 1 or idx != 0:
                    return ([], False)
                return (pts, True)
            if idx == 0:
                all_pts = list(pts)
                continue
            if self._shade_contour_gap(all_pts[-1], pts[0]) > tol:
                return ([], False)
            tail = list(pts)
            tail[0] = all_pts[-1]
            all_pts.extend(tail[1:])
        if len(all_pts) < 2:
            return ([], False)
        closed = len(all_pts) >= 3 and self._shade_contour_gap(all_pts[0], all_pts[-1]) <= tol
        if closed:
            all_pts[-1] = all_pts[0]
        return (all_pts, closed)

    def _shade_contour_path_d(self, points: list[tuple[float, float]], *, closed: bool) -> str:
        if len(points) < 2:
            return ""
        pts = list(points)
        if closed and len(pts) >= 2 and self._shade_contour_gap(pts[0], pts[-1]) <= 1e-9:
            pts = pts[:-1]
        if not pts:
            return ""
        out = [f"M {_format_num(pts[0][0])} {_format_num(pts[0][1])}"]
        for px, py in pts[1:]:
            out.append(f"L {_format_num(px)} {_format_num(py)}")
        if closed:
            out.append("Z")
        return " ".join(out)

    def _shade_contour_self_intersects(self, points: list[tuple[float, float]], *, closed: bool) -> bool:
        segs = self._segments_from_points(points, closed=closed)
        if len(segs) < 3:
            return False
        for i, a in enumerate(segs):
            for j in range(i + 1, len(segs)):
                if abs(i - j) <= 1:
                    continue
                if closed and i == 0 and j == len(segs) - 1:
                    continue
                hit = self._segment_intersection(a[0], a[1], a[2], a[3], segs[j][0], segs[j][1], segs[j][2], segs[j][3])
                if hit is None:
                    continue
                return True
        return False

    def _shade_contour_src_from_edges(self, edges: list[tuple[ET.Element, int]]) -> str:
        parts: list[str] = []
        for source_el, direction in edges:
            if self._svg_root is None or not self._element_in_svg(source_el):
                continue
            src_id = self._ensure_element_id(source_el, prefix="shape")
            dir_val = "1" if int(direction) == 1 else "0"
            parts.append(f"{src_id}:{dir_val}")
        return ";".join(parts)

    def _shade_contour_parse_src(self, raw: str | None) -> list[tuple[str, int]]:
        if not raw:
            return []
        out: list[tuple[str, int]] = []
        for part in str(raw).split(";"):
            token = part.strip()
            if not token:
                continue
            if ":" not in token:
                continue
            src_id, dir_raw = token.rsplit(":", 1)
            src_id = src_id.strip()
            if not src_id:
                continue
            dir_val = 1 if dir_raw.strip() == "1" else 0
            out.append((src_id, dir_val))
        return out

    def _shade_contour_resolve_sources(self, raw: str | None) -> list[tuple[ET.Element, int]]:
        if self._svg_root is None:
            return []
        src_parts = self._shade_contour_parse_src(raw)
        if not src_parts:
            return []
        by_id: dict[str, ET.Element] = {}
        for el in self._svg_root.iter():
            el_id = (el.get("id") or "").strip()
            if el_id:
                by_id[el_id] = el
        out: list[tuple[ET.Element, int]] = []
        for src_id, direction in src_parts:
            source = by_id.get(src_id)
            if source is None or not self._is_shade_contour_source_candidate(source):
                return []
            out.append((source, direction))
        return out

    def _clear_shade_contour_runtime(self) -> None:
        self._shade_contour_active = False
        self._shade_contour_edges.clear()
        self._shade_contour_open_start = None
        self._shade_contour_open_end = None

    def _is_shade_diff_holder(self, el: ET.Element | None) -> bool:
        if el is None:
            return False
        if self._is_shade_contour_helper(el):
            return True
        if self._is_shade_diff_candidate(el):
            return True
        return self._is_user_group(el)

    def _shade_effective_elements(self, target: ET.Element | None) -> list[ET.Element]:
        if target is None:
            return []
        if not self._element_in_svg(target):
            return []
        if self._is_shade_contour_helper(target):
            return [target]
        if self._is_shade_diff_candidate(target):
            return [target]
        if not self._is_user_group(target):
            return []
        out: list[ET.Element] = []
        for child in target.iter():
            if child is target:
                continue
            if self._is_shade_contour_helper(child):
                continue
            if not self._is_shade_diff_candidate(child):
                continue
            if any(prev is child for prev in out):
                continue
            out.append(child)
        return out

    def _shade_diff_pick_target(self, record: _Record) -> ET.Element | None:
        group = self._group_ancestor(record.el)
        if group is not None:
            return group
        if self._is_shade_diff_candidate(record.el):
            return record.el
        return None

    def _shade_holder_for_element(self, el: ET.Element | None) -> ET.Element | None:
        cur = el
        while cur is not None:
            if (cur.get(_SHADE_DATA_ENABLED) or "").strip() == "1" and self._is_shade_diff_holder(cur):
                return cur
            cur = self._parent_of(cur)
        return None

    def _pick_shade_diff_candidate(self, x: float, y: float, zoom: float) -> _Record | None:
        candidates = self._collect_hit_candidates(x, y, zoom)
        filtered: list[tuple[float, _Drawable]] = []
        for dist, d in candidates:
            if d.record is None:
                continue
            if not self._is_shade_diff_candidate(d.record.el):
                if d.record.kind != "shape":
                    continue
                if self._group_ancestor(d.record.el) is None:
                    continue
            filtered.append((dist, d))
        if not filtered:
            return None
        primary = [(dist, d) for dist, d in filtered if not self._is_subsegment_record(d.record)]
        if primary:
            filtered = primary
        return filtered[0][1].record

    def _pick_shade_contour_source_candidate(
        self,
        x: float,
        y: float,
        zoom: float,
        *,
        prefer_subsegments: bool = False,
    ) -> _Record | None:
        candidates = self._collect_hit_candidates(x, y, zoom)
        filtered: list[tuple[float, _Drawable]] = []
        for dist, d in candidates:
            if d.record is None:
                continue
            if d.record.kind != "shape":
                continue
            if not self._is_shade_contour_source_candidate(d.record.el):
                continue
            if self._intersection_candidate_is_hidden(d.record.el, _strip_ns(d.record.el.tag)):
                continue
            filtered.append((dist, d))
        if not filtered:
            direct = self._pick_shade_contour_source_candidate_direct(x, y, zoom, prefer_subsegments=prefer_subsegments)
            if direct is not None:
                return direct
            return None
        near = [(dist, d) for dist, d in filtered if dist <= 6.0]
        if not near:
            direct = self._pick_shade_contour_source_candidate_direct(x, y, zoom, prefer_subsegments=prefer_subsegments)
            if direct is not None:
                return direct
            return None
        if self._shade_contour_edges:
            tol = self._shade_contour_tol_svg(zoom)
            connecting = [
                (dist, d)
                for dist, d in near
                if d.record is not None and self._shade_contour_record_connects_to_chain(d.record, tol=tol)
            ]
            if connecting:
                near = connecting
        if prefer_subsegments:
            prefer_split_parent = False
            primary = [(dist, d) for dist, d in near if self._is_subsegment_record(d.record)]
            if primary:
                near = primary
            else:
                splitable = [(dist, d) for dist, d in near if self._is_split_target_record(d.record)]
                if splitable:
                    near = splitable
                    prefer_split_parent = True
        else:
            prefer_split_parent = False
            primary = [(dist, d) for dist, d in near if not self._is_subsegment_record(d.record)]
            if primary:
                near = primary
        click_svg = self._canvas_to_svg(x, y, zoom)
        if prefer_split_parent:
            near.sort(key=lambda item: self._shade_contour_split_candidate_sort_key(item, zoom=zoom))
        else:
            near.sort(key=lambda item: self._shade_contour_candidate_sort_key(item, click_svg=click_svg))
        return near[0][1].record

    def _pick_shade_contour_source_candidate_direct(
        self,
        x: float,
        y: float,
        zoom: float,
        *,
        prefer_subsegments: bool = False,
    ) -> _Record | None:
        if self._svg_root is None:
            return None
        sx, sy = self._canvas_to_svg(x, y, zoom)
        max_dist_svg = 10.0 / max(zoom, 1e-6)
        candidates: list[tuple[float, _Record]] = []
        for el in self._svg_root.iter():
            if not self._is_shade_contour_source_candidate(el):
                continue
            if self._intersection_candidate_is_hidden(el, _strip_ns(el.tag)):
                continue
            points, closed = self._shade_contour_source_points(el, direction=0)
            if len(points) < 2:
                continue
            proj = self._project_point_on_polyline(points, closed=closed, px=sx, py=sy)
            if proj is None:
                continue
            dist_svg = proj[0]
            if dist_svg > max_dist_svg:
                continue
            candidates.append((dist_svg * zoom, self._record_for_element(el)))
        if not candidates:
            return None
        if self._shade_contour_edges:
            tol = self._shade_contour_tol_svg(zoom)
            connecting = [
                (dist, rec)
                for dist, rec in candidates
                if self._shade_contour_record_connects_to_chain(rec, tol=tol)
            ]
            if connecting:
                candidates = connecting
        if prefer_subsegments:
            splitable = [(dist, rec) for dist, rec in candidates if self._is_split_target_record(rec)]
            if splitable:
                candidates = splitable
        else:
            nonsub = [(dist, rec) for dist, rec in candidates if not self._is_subsegment_record(rec)]
            if nonsub:
                candidates = nonsub
        if prefer_subsegments:
            candidates.sort(
                key=lambda item: self._shade_contour_record_split_sort_key(item[0], item[1], zoom=zoom)
            )
        else:
            candidates.sort(
                key=lambda item: self._shade_contour_record_sort_key(item[0], item[1], click_svg=(sx, sy))
            )
        return candidates[0][1]

    def _shade_contour_split_candidate_sort_key(
        self,
        item: tuple[float, _Drawable],
        *,
        zoom: float,
    ) -> tuple[float, int, float]:
        dist, drawable = item
        record = drawable.record
        if record is None:
            return (dist, 0, float("inf"))
        split_count = self._shade_contour_internal_split_count(record.el, zoom=zoom)
        length = self._shade_contour_source_length(record.el)
        # Double click means "split if possible". When imported SVGs contain a
        # long parent segment plus already drawn short pieces on top, prefer the
        # parent that actually has internal points to split on.
        return self._shade_contour_record_split_sort_key(dist, record, zoom=zoom)

    def _shade_contour_record_split_sort_key(
        self,
        dist: float,
        record: _Record,
        *,
        zoom: float,
    ) -> tuple[float, int, float]:
        split_count = self._shade_contour_internal_split_count(record.el, zoom=zoom)
        length = self._shade_contour_source_length(record.el)
        return (round(dist, 3), -split_count, -length)

    def _shade_contour_internal_split_count(self, el: ET.Element, *, zoom: float) -> int:
        tag = _strip_ns(el.tag)
        if tag == "line":
            try:
                points = self._points_on_line(el, tol_px=5.0, zoom=zoom)
            except Exception:
                return 0
            return max(0, len(points) - 2)
        if self._is_curve_subsegment_parent(el):
            try:
                sx = sy = 0.0
                points, closed = self._curve_subsegment_points(el, click_svg=(sx, sy))
                if closed or len(points) < 2:
                    return 0
                total_len = self._polyline_total_length(points, closed=False)
                if total_len <= 1e-9:
                    return 0
                tol = 5.0 / max(zoom, 1e-6)
                split_points = self._curve_split_points(points, closed=False, tol=tol, total_len=total_len)
                return max(0, len(split_points) - 2)
            except Exception:
                return 0
        return 0

    def _shade_contour_source_length(self, el: ET.Element) -> float:
        points, closed = self._shade_contour_source_points(el, direction=0)
        if len(points) < 2:
            return float("inf")
        total = 0.0
        for idx in range(len(points) - 1):
            total += self._shade_contour_gap(points[idx], points[idx + 1])
        if closed and len(points) >= 3:
            total += self._shade_contour_gap(points[-1], points[0])
        return total

    def _shade_contour_candidate_sort_key(
        self,
        item: tuple[float, _Drawable],
        *,
        click_svg: tuple[float, float],
    ) -> tuple[float, float, int, float]:
        dist, drawable = item
        record = drawable.record
        if record is None:
            return (dist, float("inf"), 1, float("inf"))
        # Distance remains the main criterion. Length is the important tie-break
        # for imported SVGs where a long parent line and shorter real edges are
        # drawn on the exact same pixels, e.g. AE over AP1/P1E.
        return self._shade_contour_record_sort_key(dist, record, click_svg=click_svg)

    def _shade_contour_record_sort_key(
        self,
        dist: float,
        record: _Record,
        *,
        click_svg: tuple[float, float],
    ) -> tuple[float, float, int, float]:
        length = self._shade_contour_source_length(record.el)
        has_segment_id = 0 if (record.el.get("data-segment-id") or "").strip() else 1
        endpoints = self._shade_contour_source_endpoints(record.el, direction=0)
        if endpoints is None:
            endpoint_gap = float("inf")
        else:
            a, b = endpoints
            endpoint_gap = min(self._shade_contour_gap(click_svg, a), self._shade_contour_gap(click_svg, b))
        return (round(dist, 3), length, has_segment_id, -endpoint_gap)

    def _shade_contour_record_connects_to_chain(self, record: _Record, *, tol: float) -> bool:
        if not self._shade_contour_edges:
            return True
        source = record.el
        if any(prev is source for prev, _dir in self._shade_contour_edges):
            return True
        chain_points, chain_closed = self._shade_contour_points_from_edges(self._shade_contour_edges, tol=tol)
        if chain_closed or len(chain_points) < 2:
            return False
        open_start = chain_points[0]
        open_end = chain_points[-1]
        end0 = self._shade_contour_source_endpoints(source, direction=0)
        end1 = self._shade_contour_source_endpoints(source, direction=1)
        if end0 is None or end1 is None:
            return False
        s0, e0 = end0
        s1, e1 = end1
        return (
            self._shade_contour_gap(open_end, s0) <= tol
            or self._shade_contour_gap(open_end, s1) <= tol
            or self._shade_contour_gap(open_start, e0) <= tol
            or self._shade_contour_gap(open_start, e1) <= tol
        )

    def _create_shade_contour_helper(
        self,
        edges: list[tuple[ET.Element, int]],
        *,
        tol: float,
    ) -> ET.Element | None:
        if self._svg_root is None:
            return None
        points, closed = self._shade_contour_points_from_edges(edges, tol=tol)
        if not closed or len(points) < 3:
            return None
        if self._shade_contour_self_intersects(points, closed=True):
            return None
        d = self._shade_contour_path_d(points, closed=True)
        if not d:
            return None
        src = self._shade_contour_src_from_edges(edges)
        helper: ET.Element | None = None
        duplicates: list[ET.Element] = []
        for el in list(self._svg_root.iter()):
            if not self._is_shade_contour_helper(el):
                continue
            if (el.get(_SHADE_CONTOUR_DATA_SRC) or "").strip() != src:
                continue
            if helper is None:
                helper = el
            else:
                duplicates.append(el)
        for extra in duplicates:
            parent = self._parent_of(extra)
            if parent is None:
                continue
            try:
                parent.remove(extra)
            except Exception:
                pass
        if helper is None:
            helper = ET.Element(self._svg_ns_tag("path"))
            helper.set("id", self._next_generic_id(prefix=_SHADE_CONTOUR_ID_PREFIX))
            self._svg_root.append(helper)
        helper.set("data-kind", _SHADE_CONTOUR_DATA_KIND)
        helper.set(_SHADE_CONTOUR_DATA_FLAG, "1")
        helper.set(_SHADE_CONTOUR_DATA_SRC, src)
        helper.set(_SHADE_CONTOUR_DATA_TOL, _format_num(tol))
        helper.set("d", d)
        _set_attr(helper, "fill", "#000000")
        _force_style_attr(helper, "fill-opacity", _format_num(self._normalize_shade_diff_opacity()))
        _set_attr(helper, "stroke", "none")
        return helper

    def _shade_diff_assign_target(self, target: ET.Element) -> None:
        if self._shade_diff_base is None:
            self._shade_diff_base = target
            self._shade_diff_holes.clear()
            return
        if target is self._shade_diff_base:
            return
        idx = -1
        for i, hole in enumerate(self._shade_diff_holes):
            if hole is target:
                idx = i
                break
        if idx >= 0:
            self._shade_diff_holes.pop(idx)
        else:
            self._shade_diff_holes.append(target)

    def _apply_closed_shade_contour(self, edges: list[tuple[ET.Element, int]], *, tol: float) -> ET.Element | None:
        if self._svg_root is None:
            return None
        self._push_history()
        helper = self._create_shade_contour_helper(edges, tol=tol)
        if helper is None:
            self._set_shade_status("Contorno invalido: no se pudo crear region sombreada.")
            return None
        self._clear_shade_contour_runtime()
        self._set_shade_status("Sombreado aplicado por contorno.")
        self._render_svg()
        # Keep the newly created shaded contour selected so auto-apply does not
        # feel like a full deselection after the chain closes.
        if hasattr(self, "_records"):
            for record in self._records:
                if record.el is helper:
                    self._select_record(record)
                    break
        return helper

    def _handle_shade_contour_source_click(self, record: _Record, x: float, y: float, zoom: float) -> None:
        if self._svg_root is None:
            return
        source = record.el
        if not self._is_shade_contour_source_candidate(source):
            self._shade_diff_status()
            return
        tol = self._shade_contour_tol_svg(zoom)
        if any(prev is source for prev, _dir in self._shade_contour_edges):
            idx = -1
            for i, (prev, _dir) in enumerate(self._shade_contour_edges):
                if prev is source:
                    idx = i
                    break
            if idx < 0:
                self._set_shade_status("Contorno: el borde ya esta seleccionado.")
                return
            n_edges = len(self._shade_contour_edges)
            if n_edges > 2 and idx not in (0, n_edges - 1):
                self._set_shade_status("Contorno: para deseleccionar, usa un borde extremo de la cadena.")
                return
            self._shade_contour_edges.pop(idx)
            if not self._shade_contour_edges:
                self._clear_shade_contour_runtime()
                self._shade_diff_status()
                self._render_preview()
                return
            points_after, closed_after = self._shade_contour_points_from_edges(self._shade_contour_edges, tol=tol)
            if not points_after:
                self._clear_shade_contour_runtime()
                self._set_shade_status("Contorno: la cadena quedo invalida; se reinicio la seleccion.")
                self._render_preview()
                return
            if closed_after:
                self._shade_contour_open_start = None
                self._shade_contour_open_end = None
            else:
                self._shade_contour_open_start = points_after[0]
                self._shade_contour_open_end = points_after[-1]
            self._shade_diff_status()
            self._render_preview()
            return
        prev_edges = list(self._shade_contour_edges)
        if not self._shade_contour_edges:
            points, closed = self._shade_contour_source_points(source, direction=0)
            if not points:
                self._shade_diff_status()
                return
            self._shade_contour_active = True
            if closed:
                self._shade_contour_edges = [(source, 0)]
                self._shade_contour_open_start = None
                self._shade_contour_open_end = None
                self._set_shade_status("Contorno cerrado listo. Usa Aplicar para confirmar o Esc para limpiar.")
                self._render_preview()
                return
            sx, sy = self._canvas_to_svg(x, y, zoom)
            a = points[0]
            b = points[-1]
            direction = 0 if self._shade_contour_gap((sx, sy), a) <= self._shade_contour_gap((sx, sy), b) else 1
            self._shade_contour_edges = [(source, direction)]
            endpoints = self._shade_contour_source_endpoints(source, direction=direction)
            if endpoints is not None:
                self._shade_contour_open_start, self._shade_contour_open_end = endpoints
            self._shade_diff_status()
            self._render_preview()
            return

        chain_points, chain_closed = self._shade_contour_points_from_edges(self._shade_contour_edges, tol=tol)
        if chain_closed:
            self._set_shade_status("Contorno cerrado listo. Usa Aplicar para confirmar o Esc para limpiar.")
            self._render_preview()
            return
        if len(chain_points) < 2:
            self._clear_shade_contour_runtime()
            self._shade_diff_status()
            self._render_preview()
            return
        open_start = chain_points[0]
        open_end = chain_points[-1]

        cand: list[tuple[float, str, int]] = []
        end0 = self._shade_contour_source_endpoints(source, direction=0)
        end1 = self._shade_contour_source_endpoints(source, direction=1)
        if end0 is None or end1 is None:
            self._set_shade_status("Contorno: el borde seleccionado es cerrado y no conecta.")
            return
        s0, e0 = end0
        s1, e1 = end1
        gap_append_0 = self._shade_contour_gap(open_end, s0)
        gap_append_1 = self._shade_contour_gap(open_end, s1)
        gap_prepend_0 = self._shade_contour_gap(open_start, e0)
        gap_prepend_1 = self._shade_contour_gap(open_start, e1)
        if gap_append_0 <= tol:
            cand.append((gap_append_0, "append", 0))
        if gap_append_1 <= tol:
            cand.append((gap_append_1, "append", 1))
        if gap_prepend_0 <= tol:
            cand.append((gap_prepend_0, "prepend", 0))
        if gap_prepend_1 <= tol:
            cand.append((gap_prepend_1, "prepend", 1))
        if not cand:
            self._set_shade_status("Contorno: el borde no conecta; se conserva la cadena actual.")
            return
        cand.sort(key=lambda item: item[0])
        _gap, place, direction = cand[0]
        if place == "append":
            self._shade_contour_edges.append((source, direction))
        else:
            self._shade_contour_edges.insert(0, (source, direction))

        points, closed = self._shade_contour_points_from_edges(self._shade_contour_edges, tol=tol)
        if not points:
            self._shade_contour_edges = prev_edges
            self._set_shade_status("Contorno invalido: cadena no conectada.")
            return
        if closed:
            if self._shade_contour_self_intersects(points, closed=True):
                self._shade_contour_edges = prev_edges
                self._set_shade_status("Contorno invalido: no se aceptan autointersecciones.")
                return
            self._shade_contour_open_start = None
            self._shade_contour_open_end = None
            self._set_shade_status("Contorno cerrado listo. Usa Aplicar para confirmar o Esc para limpiar.")
            self._render_preview()
            return

        self._shade_contour_open_start = points[0]
        self._shade_contour_open_end = points[-1]
        self._shade_diff_status()
        self._render_preview()

    def _handle_shade_diff_click(self, event: tk.Event) -> None:
        if self.canvas is None or self._svg_root is None:
            return
        x = float(self.canvas.canvasx(event.x))
        y = float(self.canvas.canvasy(event.y))
        try:
            zoom = float(self._view_scale.get())
        except Exception:
            zoom = 1.0
        if zoom <= 0:
            zoom = 1.0
        contour_record = self._pick_shade_contour_source_candidate(x, y, zoom)
        if contour_record is None:
            self._shade_diff_status()
            return
        target_record = self._resolve_shade_contour_record(contour_record, x, y, zoom, split=False)
        self._reset_child_cycle_for_parent(target_record.el)
        self._select_record(target_record)
        self._handle_shade_contour_source_click(target_record, x, y, zoom)

    def _has_any_hit_candidate_near(self, x: float, y: float, zoom: float, *, max_dist: float = 6.0) -> bool:
        hits = self._collect_hit_candidates(x, y, zoom)
        if not hits:
            return False
        return bool(hits[0][0] <= max_dist)

    def _resolve_shade_contour_record(
        self,
        record: _Record,
        x: float,
        y: float,
        zoom: float,
        *,
        split: bool,
    ) -> _Record:
        if record is None:
            return record
        if not split:
            # For contour shading the visible edge is the source of truth. Some
            # SVGs keep the original segment hidden and draw usable subsegments
            # on top; resolving those clicks back to the hidden parent breaks
            # chains after the first couple of borders.
            return record
        return self._resolve_child_record(record, x, y, zoom, create_if_missing=True)

    def _handle_shade_diff_double_click(self, event: tk.Event) -> None:
        if self.canvas is None or self._svg_root is None:
            return
        x = float(self.canvas.canvasx(event.x))
        y = float(self.canvas.canvasy(event.y))
        try:
            zoom = float(self._view_scale.get())
        except Exception:
            zoom = 1.0
        if zoom <= 0:
            zoom = 1.0
        record = self._pick_shade_contour_source_candidate(x, y, zoom, prefer_subsegments=True)
        if record is None:
            if self._has_any_hit_candidate_near(x, y, zoom):
                self._shade_diff_status()
            else:
                self._clear_selection_on_double_click_outside()
            return
        target_record = self._resolve_shade_contour_record(record, x, y, zoom, split=True)
        self._select_record(target_record)
        self._handle_shade_contour_source_click(target_record, x, y, zoom)

    def _clear_shade_diff_attrs(self, base_el: ET.Element) -> None:
        base_el.attrib.pop(_SHADE_DATA_ENABLED, None)
        base_el.attrib.pop(_SHADE_DATA_HOLE_IDS, None)
        base_el.attrib.pop(_SHADE_DATA_MASK_ID, None)
        base_el.attrib.pop(_SHADE_DATA_OVERLAP, None)
        base_el.attrib.pop("mask", None)

    def _shade_hole_elements(self, base_el: ET.Element) -> list[ET.Element]:
        if self._svg_root is None:
            return []
        base_effective_ids = {id(el) for el in self._shade_effective_elements(base_el)}
        ids: dict[str, ET.Element] = {}
        for el in self._svg_root.iter():
            el_id = (el.get("id") or "").strip()
            if el_id:
                ids[el_id] = el
        out: list[ET.Element] = []
        seen: set[str] = set()
        for hid in self._parse_id_list(base_el.get(_SHADE_DATA_HOLE_IDS)):
            if hid in seen:
                continue
            seen.add(hid)
            hole = ids.get(hid)
            if hole is None or hole is base_el:
                continue
            if id(hole) in base_effective_ids:
                continue
            if not self._is_shade_diff_candidate(hole):
                continue
            out.append(hole)
        return out

    def _shape_contains_point(self, el: ET.Element, x: float, y: float) -> bool:
        tag = _strip_ns(el.tag)
        eps = 1e-9
        if tag == "polygon":
            coords = _parse_points(_get_attr(el, "points"))
            pts = [(coords[i], coords[i + 1]) for i in range(0, len(coords) - 1, 2)]
            if len(pts) < 3:
                return False
            return self._point_in_polygon(x, y, pts)
        if tag == "circle":
            cx = _parse_float(_get_attr(el, "cx"))
            cy = _parse_float(_get_attr(el, "cy"))
            r = _parse_float(_get_attr(el, "r"), 0.0)
            if r <= 0:
                return False
            return math.hypot(x - cx, y - cy) <= r + eps
        if tag == "ellipse":
            cx = _parse_float(_get_attr(el, "cx"))
            cy = _parse_float(_get_attr(el, "cy"))
            rx = _parse_float(_get_attr(el, "rx"), 0.0)
            ry = _parse_float(_get_attr(el, "ry"), 0.0)
            if rx <= 0 or ry <= 0:
                return False
            dx = (x - cx) / rx
            dy = (y - cy) / ry
            return dx * dx + dy * dy <= 1.0 + eps
        if tag == "rect":
            rx = _parse_float(_get_attr(el, "x"))
            ry = _parse_float(_get_attr(el, "y"))
            w = _parse_float(_get_attr(el, "width"), 0.0)
            h = _parse_float(_get_attr(el, "height"), 0.0)
            if w <= 0 or h <= 0:
                return False
            return (rx - eps) <= x <= (rx + w + eps) and (ry - eps) <= y <= (ry + h + eps)
        if tag == "path":
            if el.get("data-text") is not None:
                return False
            d = _get_attr(el, "d") or ""
            inside = False
            for pts, closed in _parse_svg_path(d):
                if not closed or len(pts) < 3:
                    continue
                if self._point_in_polygon(x, y, pts):
                    inside = not inside
            return inside
        return False

    def _sync_shade_contour_helpers(self) -> None:
        if self._svg_root is None:
            return
        root = self._svg_root
        helpers = [el for el in list(root.iter()) if self._is_shade_contour_helper(el)]
        seen_src: dict[str, ET.Element] = {}
        for helper in list(helpers):
            src = (helper.get(_SHADE_CONTOUR_DATA_SRC) or "").strip()
            if not src:
                continue
            if src not in seen_src:
                seen_src[src] = helper
                continue
            parent = self._parent_of(helper)
            if parent is not None:
                try:
                    parent.remove(helper)
                except Exception:
                    pass
        helpers = [el for el in list(root.iter()) if self._is_shade_contour_helper(el)]
        for helper in helpers:
            raw_src = helper.get(_SHADE_CONTOUR_DATA_SRC)
            resolved = self._shade_contour_resolve_sources(raw_src)
            tol = _parse_float(helper.get(_SHADE_CONTOUR_DATA_TOL), 0.0)
            if tol <= 0:
                try:
                    zoom = float(self._view_scale.get())
                except Exception:
                    zoom = 1.0
                tol = self._shade_contour_tol_svg(zoom)
            valid = bool(resolved)
            points: list[tuple[float, float]] = []
            closed = False
            if valid:
                points, closed = self._shade_contour_points_from_edges(resolved, tol=tol)
                if not closed or len(points) < 3:
                    valid = False
                elif self._shade_contour_self_intersects(points, closed=True):
                    valid = False
            if not valid:
                parent = self._parent_of(helper)
                if parent is not None:
                    try:
                        parent.remove(helper)
                    except Exception:
                        pass
                continue
            d = self._shade_contour_path_d(points, closed=True)
            if not d:
                parent = self._parent_of(helper)
                if parent is not None:
                    try:
                        parent.remove(helper)
                    except Exception:
                        pass
                continue
            helper.set("d", d)
            helper.set("data-kind", _SHADE_CONTOUR_DATA_KIND)
            helper.set(_SHADE_CONTOUR_DATA_FLAG, "1")
            helper.set(_SHADE_CONTOUR_DATA_SRC, self._shade_contour_src_from_edges(resolved))
            helper.set(_SHADE_CONTOUR_DATA_TOL, _format_num(tol))
            fill = str(self._effective_attr(helper, "fill") or "").strip().lower()
            if fill in ("", "none", "transparent"):
                _set_attr(helper, "fill", "#000000")
            op = str(self._effective_attr(helper, "fill-opacity") or "").strip()
            if not op:
                _force_style_attr(helper, "fill-opacity", _format_num(self._normalize_shade_diff_opacity()))
            _set_attr(helper, "stroke", "none")

    def _point_in_shade_hole_canvas(self, base_el: ET.Element, x: float, y: float, zoom: float) -> bool:
        holder = self._shade_holder_for_element(base_el)
        if holder is None:
            return False
        sx, sy = self._canvas_to_svg(x, y, zoom)
        holes = self._shade_hole_elements(holder)
        if not holes:
            return False
        for hole in holes:
            if self._shape_contains_point(hole, sx, sy):
                return True
        return False

    def _apply_shade_diff_selection(self) -> None:
        if self._svg_root is None or not self._shade_diff_active:
            return
        self._flush_pending_shade_clicks()
        if not self._shade_contour_edges:
            self._set_shade_status("Contorno: no hay cadena activa para aplicar.")
            return
        try:
            zoom = float(self._view_scale.get())
        except Exception:
            zoom = 1.0
        if zoom <= 0:
            zoom = 1.0
        tol = self._shade_contour_tol_svg(zoom)
        points, closed = self._shade_contour_points_from_edges(self._shade_contour_edges, tol=tol)
        if not points:
            self._set_shade_status("Contorno invalido: cadena no conectada.")
            return
        if not closed:
            self._set_shade_status("Contorno abierto: agrega mas bordes o cierra la cadena.")
            return
        if self._shade_contour_self_intersects(points, closed=True):
            self._set_shade_status("Contorno invalido: no se aceptan autointersecciones.")
            return
        self._apply_closed_shade_contour(self._shade_contour_edges, tol=tol)

    def _sync_shade_diff_runtime_selection(self) -> None:
        if not self._shade_diff_active:
            self._shade_diff_base = None
            self._shade_diff_holes.clear()
            self._clear_shade_contour_runtime()
            return
        self._shade_diff_base = None
        self._shade_diff_holes.clear()
        kept_edges: list[tuple[ET.Element, int]] = []
        for source, direction in self._shade_contour_edges:
            if not self._element_in_svg(source):
                continue
            if not self._is_shade_contour_source_candidate(source):
                continue
            kept_edges.append((source, direction))
        self._shade_contour_edges = kept_edges

    def _shade_target_highlight_elements(self, target: ET.Element | None) -> list[ET.Element]:
        if target is None or self._svg_root is None:
            return []
        if not self._element_in_svg(target):
            return []
        if self._is_shade_contour_helper(target):
            out: list[ET.Element] = []
            for source, _dir in self._shade_contour_resolve_sources(target.get(_SHADE_CONTOUR_DATA_SRC)):
                if not self._element_in_svg(source):
                    continue
                if any(prev is source for prev in out):
                    continue
                out.append(source)
            return out
        return self._shade_effective_elements(target)

    def _shade_runtime_highlight_roles(self) -> tuple[set[int], set[int]]:
        contour_ids: set[int] = set()
        if not self._shade_diff_active or self._svg_root is None:
            return (contour_ids, set())
        for source, _direction in self._shade_contour_edges:
            if not self._element_in_svg(source):
                continue
            contour_ids.add(id(source))
        return (contour_ids, set())

    def _sync_shade_diff_masks(self) -> None:
        if self._svg_root is None:
            return
        root = self._svg_root
        self._class_styles = self._collect_css_class_styles(root)
        ids: dict[str, ET.Element] = {}
        for el in root.iter():
            el_id = (el.get("id") or "").strip()
            if el_id:
                ids[el_id] = el
        defs = self._ensure_defs_node()
        expected_mask_ids: set[str] = set()
        for el in list(root.iter()):
            if (el.get(_SHADE_DATA_ENABLED) or "").strip() != "1":
                continue
            if not self._is_shade_diff_holder(el):
                self._clear_shade_diff_attrs(el)
                continue
            base_effective = self._shade_effective_elements(el)
            if not base_effective:
                self._clear_shade_diff_attrs(el)
                continue
            base_effective_ids: set[str] = set()
            for base_comp in base_effective:
                bid = self._ensure_element_id(base_comp, prefix="shape")
                ids[bid] = base_comp
                base_effective_ids.add(bid)
            base_id = self._ensure_element_id(el, prefix="shape")
            ids[base_id] = el
            raw_holes = self._parse_id_list(el.get(_SHADE_DATA_HOLE_IDS))
            valid_holes: list[str] = []
            seen: set[str] = set()
            for hid in raw_holes:
                if hid in seen:
                    continue
                seen.add(hid)
                hole_el = ids.get(hid)
                if hole_el is None or hole_el is el:
                    continue
                if hid in base_effective_ids:
                    continue
                if not self._is_shade_diff_candidate(hole_el):
                    continue
                valid_holes.append(hid)
            if not valid_holes:
                self._clear_shade_diff_attrs(el)
                continue
            hole_raw = ",".join(valid_holes)
            el.set(_SHADE_DATA_ENABLED, "1")
            el.set(_SHADE_DATA_HOLE_IDS, hole_raw)
            el.set(_SHADE_DATA_OVERLAP, "union")
            mask_id = f"{_SHADE_MASK_PREFIX}{base_id}"
            el.set(_SHADE_DATA_MASK_ID, mask_id)
            el.set("mask", f"url(#{mask_id})")
            if self._is_shade_diff_candidate(el) and not self._is_shade_contour_helper(el):
                fill = str(self._effective_attr(el, "fill") or "").strip().lower()
                if fill in ("", "none", "transparent"):
                    _set_attr(el, "fill", "#000000")
            if defs is None:
                defs = self._ensure_defs_node()
                if defs is None:
                    continue
            mask_el = None
            for child in list(defs):
                if _strip_ns(child.tag) == "mask" and (child.get("id") or "").strip() == mask_id:
                    mask_el = child
                    break
            if mask_el is None:
                mask_el = ET.Element(self._svg_ns_tag("mask"))
                mask_el.set("id", mask_id)
                defs.append(mask_el)
            for child in list(mask_el):
                mask_el.remove(child)
            min_x, min_y, vb_w, vb_h = self._resolve_viewbox(root)
            mask_el.set("maskUnits", "userSpaceOnUse")
            mask_el.set("maskContentUnits", "userSpaceOnUse")
            mask_el.set("x", _format_num(min_x))
            mask_el.set("y", _format_num(min_y))
            mask_el.set("width", _format_num(max(vb_w, 1.0)))
            mask_el.set("height", _format_num(max(vb_h, 1.0)))
            for comp_id in base_effective_ids:
                use_base = ET.Element(self._svg_ns_tag("use"))
                href = f"#{comp_id}"
                use_base.set("href", href)
                use_base.set(_XLINK_HREF, href)
                use_base.set("fill", "#ffffff")
                use_base.set("stroke", "none")
                mask_el.append(use_base)
            for hid in valid_holes:
                use_hole = ET.Element(self._svg_ns_tag("use"))
                hh = f"#{hid}"
                use_hole.set("href", hh)
                use_hole.set(_XLINK_HREF, hh)
                use_hole.set("fill", "#000000")
                use_hole.set("stroke", "none")
                mask_el.append(use_hole)
            expected_mask_ids.add(mask_id)
        if defs is not None:
            for child in list(defs):
                if _strip_ns(child.tag) != "mask":
                    continue
                mask_id = (child.get("id") or "").strip()
                if not mask_id.startswith(_SHADE_MASK_PREFIX):
                    continue
                if mask_id in expected_mask_ids:
                    continue
                defs.remove(child)

    def _label_font_size(self, el: ET.Element, default: float = 12.0) -> float:
        if _strip_ns(el.tag) == "path" and el.get("data-text") is not None:
            return _parse_float(el.get("data-font-size"), _parse_float(self._effective_attr(el, "font-size"), default))
        return _parse_float(self._effective_attr(el, "font-size"), default)

    def _apply_label_font_size(self, el: ET.Element, font_size: float) -> None:
        tag = _strip_ns(el.tag)
        fmt = _format_num(font_size)
        if tag == "text":
            _force_style_attr(el, "font-size", fmt)
        elif tag == "path" and el.get("data-text") is not None:
            el.set("data-font-size", fmt)
            _force_style_attr(el, "font-size", fmt)
        else:
            return

        text = (el.text or "").strip() if tag == "text" else (el.get("data-text") or "").strip()
        if not text:
            return
        latex = tag == "path"
        raw_ax = el.get("data-anchor-x")
        raw_ay = el.get("data-anchor-y")
        if raw_ax is not None and raw_ay is not None and el.get("data-anchor-frac") is None:
            ax = _parse_float(raw_ax, None)
            ay = _parse_float(raw_ay, None)
            dir_s = (el.get("data-dir") or "").strip().upper()
            if ax is not None and ay is not None and _is_valid_dir(dir_s):
                offset = _parse_float(
                    el.get("data-offset"),
                    _parse_float(self._global_label_offset_var.get().strip(), 10.0),
                )
                x, y = _label_position_from_anchor(ax, ay, text, dir_s, offset, font_size, latex)
            else:
                x, y = self._label_position(el)
        else:
            x, y = self._label_position(el)

        if tag == "text":
            _set_attr(el, "x", _format_num(x))
            _set_attr(el, "y", _format_num(y))
        else:
            el.set("data-x", _format_num(x))
            el.set("data-y", _format_num(y))
            self._update_latex_path(el, text, x, y, font_size, silent=True)

    def _apply_global_styles(self) -> None:
        if self._svg_root is None:
            return
        self._rebuild_svg_parent_map()
        self._class_styles = self._collect_css_class_styles(self._svg_root)
        stroke = self._global_stroke_var.get().strip()
        font_size = self._global_font_size_var.get().strip()
        point_r = self._global_point_radius_var.get().strip()
        label_off = self._global_label_offset_var.get().strip()
        arrow_size = self._global_arrow_size_var.get().strip()
        apply_dash = bool(getattr(self, "_global_dash_enabled_var", None) and self._global_dash_enabled_var.get())
        dash_raw = self._global_dash_var.get().strip() if hasattr(self, "_global_dash_var") else ""
        stroke_val = None
        font_val = None
        point_val = None
        label_off_val = None
        arrow_val = None
        dash_pattern: str | None = None
        if stroke:
            stroke_val = _parse_float(stroke, None)
        if font_size:
            font_val = _parse_float(font_size, None)
        if point_r:
            point_val = _parse_float(point_r, None)
        if label_off:
            label_off_val = _parse_float(label_off, None)
        if arrow_size:
            arrow_val = _parse_float(arrow_size, None)
        if apply_dash:
            dash_pattern = self._normalize_dash_pattern(dash_raw)
        if stroke and (stroke_val is None or stroke_val <= 0):
            messagebox.showerror("Globales", "Grosor de linea invalido.")
            return
        if font_size and (font_val is None or font_val <= 0):
            messagebox.showerror("Globales", "Tamano de letras invalido.")
            return
        if point_r and (point_val is None or point_val <= 0):
            messagebox.showerror("Globales", "Tamano de puntos invalido.")
            return
        if label_off and (label_off_val is None or label_off_val < 0):
            messagebox.showerror("Globales", "Separacion de etiquetas invalida.")
            return
        if arrow_size and (arrow_val is None or arrow_val <= 0):
            messagebox.showerror("Globales", "Tamano de flecha invalido.")
            return
        if apply_dash and not dash_pattern:
            messagebox.showerror("Globales", "Patron dashed invalido. Usa formato como 4,3.")
            return
        if apply_dash and dash_pattern and hasattr(self, "_global_dash_var"):
            self._global_dash_var.set(dash_pattern)

        def _is_global_editable_shape(el: ET.Element, tag: str) -> bool:
            if tag not in ("line", "polyline", "polygon", "path", "circle", "rect", "ellipse"):
                return False
            if tag == "path" and el.get("data-text") is not None:
                return False
            kind = (el.get("data-kind") or "").strip()
            if _is_aux_data_kind(kind):
                return False
            if kind == _SHADE_CONTOUR_DATA_KIND:
                return False
            return True

        dash_targets_present = False
        if apply_dash:
            for el in self._svg_root.iter():
                tag = _strip_ns(el.tag)
                if not _is_global_editable_shape(el, tag):
                    continue
                existing_dash = str(self._effective_attr(el, "stroke-dasharray") or "").strip()
                if existing_dash:
                    dash_targets_present = True
                    break

        has_change = any(v is not None for v in (stroke_val, font_val, point_val, label_off_val, arrow_val))
        if apply_dash and dash_targets_present:
            has_change = True
        if not has_change:
            self._set_transform_status("Sin cambios para aplicar.")
            return

        self._push_history()
        defs_ids = self._defs_descendant_ids()

        for el in self._svg_root.iter():
            if id(el) in defs_ids:
                continue
            tag = _strip_ns(el.tag)
            if stroke_val is not None and _is_global_editable_shape(el, tag):
                if not (tag == "path" and el.get("data-text") is not None):
                    _force_style_attr(el, "stroke-width", _format_num(stroke_val))
            if apply_dash and dash_pattern and _is_global_editable_shape(el, tag):
                existing_dash = str(self._effective_attr(el, "stroke-dasharray") or "").strip()
                if existing_dash:
                    _force_style_attr(el, "stroke-dasharray", dash_pattern)
            if font_val is not None:
                if tag == "text":
                    self._apply_label_font_size(el, font_val)
                if tag == "path" and el.get("data-text") is not None:
                    self._apply_label_font_size(el, font_val)
            if point_val is not None:
                kind = (el.get("data-kind") or "").strip()
                if self._is_point_circle(el):
                    if el.get("data-angle-id") is not None or el.get("data-angle-kind") == "point":
                        continue
                    if kind and _is_aux_data_kind(kind):
                        continue
                    _set_attr(el, "r", _format_num(point_val))
            if label_off_val is not None:
                tag = _strip_ns(el.tag)
                if tag == "text":
                    text = (el.text or "").strip()
                    latex = False
                elif tag == "path" and el.get("data-text") is not None:
                    text = (el.get("data-text") or "").strip()
                    latex = True
                else:
                    continue
                if not text:
                    continue
                if (el.get("data-angle-kind") or "").strip() == "label":
                    continue
                if not (el.get("data-point-id") or "").strip():
                    continue
                raw_ax = el.get("data-anchor-x")
                raw_ay = el.get("data-anchor-y")
                if raw_ax is None or raw_ay is None:
                    continue
                ax = _parse_float(raw_ax, None)
                ay = _parse_float(raw_ay, None)
                if ax is None or ay is None:
                    continue
                dir_s = (el.get("data-dir") or "").strip().upper()
                font_size_val = self._label_font_size(el, 12.0)
                if not dir_s:
                    cur_x, cur_y = self._label_position(el)
                    dir_s, _cur_off = self._infer_label_dir_offset(ax, ay, cur_x, cur_y, text, font_size_val, latex)
                if not dir_s:
                    continue
                el.set("data-dir", dir_s)
                el.set("data-offset", _format_num(label_off_val))
                nx, ny = _label_position_from_anchor(
                    ax, ay, text, dir_s, label_off_val, font_size_val, latex
                )
                self._set_label_position(el, nx, ny)

        if arrow_val is not None:
            has_arrow_usage = False
            for el in self._svg_root.iter():
                if (el.get("marker-start") or "").strip() or (el.get("marker-end") or "").strip():
                    has_arrow_usage = True
                    break
            if has_arrow_usage:
                self._ensure_arrow_marker()

        self._set_transform_status("Globales aplicados.")
        self._render_svg()

    def _render_from_text(self) -> None:
        raw = self.text_input.get("1.0", "end").strip()
        if not raw:
            messagebox.showerror("SVG", "No hay SVG en el cuadro de texto.")
            return
        try:
            root = ET.fromstring(raw)
        except Exception as exc:
            messagebox.showerror("SVG", f"SVG invalido: {exc}")
            return
        self._svg_tree = ET.ElementTree(root)
        self._svg_root = root
        self._current_path = None
        self._update_save_button_state()
        self._class_styles = self._collect_css_class_styles(root)
        normalized = self._normalize_imported_svg_for_editor()
        self._sync_bg_mode_from_svg()
        pretty = _pretty_xml(root)
        self.text_input.delete("1.0", "end")
        self.text_input.insert("1.0", pretty)
        self._update_line_numbers()
        self._last_svg_text_raw = ET.tostring(root, encoding="unicode")
        self._reset_history(ET.tostring(root, encoding="unicode"))
        self._set_transform_status("SVG cargado y normalizado." if normalized else "SVG cargado.")
        self._render_svg()

    def open_svg_path(self, path: str) -> None:
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
        except Exception as exc:
            messagebox.showerror("SVG", f"No se pudo abrir: {exc}")
            return
        try:
            root = ET.fromstring(raw)
        except Exception as exc:
            messagebox.showerror("SVG", f"SVG invalido: {exc}")
            return
        self._current_path = path
        self._update_save_button_state()
        self._svg_tree = ET.ElementTree(root)
        self._svg_root = root
        self._class_styles = self._collect_css_class_styles(root)
        normalized = self._normalize_imported_svg_for_editor()
        self._sync_bg_mode_from_svg()
        self.text_input.delete("1.0", "end")
        self.text_input.insert("1.0", _pretty_xml(root))
        self._update_line_numbers()
        self._last_svg_text_raw = ET.tostring(root, encoding="unicode")
        self._reset_history(ET.tostring(root, encoding="unicode"))
        self._set_transform_status("SVG abierto y normalizado." if normalized else "SVG abierto.")
        self._render_svg()

    def _load_svg(self) -> None:
        path = filedialog.askopenfilename(
            title="Abrir SVG",
            filetypes=[("SVG", "*.svg"), ("All files", "*.*")],
        )
        if not path:
            return
        self.open_svg_path(path)

    def _render_svg(self) -> None:
        if self._svg_root is None:
            return
        self._class_styles = self._collect_css_class_styles(self._svg_root)
        self._rebuild_svg_parent_map()
        self._repair_hidden_subsegment_orphans()
        if not bool(getattr(self, "_minimal_v1_globales_only", False)):
            self._sync_all_subsegments()
            self._sync_shade_contour_helpers()
            self._sync_shade_diff_runtime_selection()
            self._sync_shade_diff_masks()
            self._class_styles = self._collect_css_class_styles(self._svg_root)
            self._rebuild_svg_parent_map()
        self._sync_segment_dimensions()
        self._sync_label_backgrounds()
        self._sync_circle_radii()
        self._sync_curve_radii()
        self._sync_arrow_marker_if_used()
        self._auto_expand_viewbox()
        self._sync_label_cut_masks()
        selected_el = self._selected.el if self._selected is not None else None
        self.canvas.delete("all")
        self._records.clear()
        self._drawables.clear()
        self._selected = None
        self._anchor_points = self._collect_anchor_points(self._svg_root)
        defs_ids: set[int] = set()
        for el in self._svg_root.iter():
            if _strip_ns(el.tag) == "defs":
                for child in el.iter():
                    defs_ids.add(id(child))
        min_x, min_y, width, height = self._resolve_viewbox(self._svg_root)
        if width <= 0 or height <= 0:
            min_x, min_y, width, height = (0.0, 0.0, 800.0, 600.0)
        self._view_min_x = min_x
        self._view_min_y = min_y
        self._view_width = width
        self._view_height = height
        self._shift_x = -min_x
        self._shift_y = -min_y
        self.canvas.config(scrollregion=(0, 0, width, height))

        for el in self._svg_root.iter():
            if id(el) in defs_ids:
                continue
            tag = _strip_ns(el.tag)
            if tag == "svg":
                continue
            if tag in ("g", "defs", "title", "desc"):
                continue
            record = self._render_element(el, tag)
            if record is not None:
                self._records.append(record)

        if selected_el is not None:
            for record in self._records:
                if record.el is selected_el:
                    self._selected = record
                    break
            if self._selected is None and self._is_user_group(selected_el) and self._element_in_svg(selected_el):
                self._selected = _Record(el=selected_el, tag="g", item_ids=[], kind="shape")

        self._sync_selected_ui()
        self._render_preview()
        self._sync_text_from_svg()
        if self._shade_diff_active:
            self._shade_diff_status()

    def _collect_css_class_styles(self, root: ET.Element) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for el in root.iter():
            if _strip_ns(el.tag) != "style":
                continue
            styles = _parse_css_classes(el.text or "")
            for name, props in styles.items():
                if name not in out:
                    out[name] = {}
                out[name].update(props)
        return out

    def _class_style_for(self, el: ET.Element) -> dict[str, str]:
        class_attr = (el.get("class") or "").strip()
        if not class_attr:
            return {}
        merged: dict[str, str] = {}
        for cls in class_attr.split():
            class_style = self._class_styles.get(cls)
            if class_style:
                merged.update(class_style)
        return merged

    def _rebuild_svg_parent_map(self) -> None:
        self._svg_parent_by_id = {}
        if self._svg_root is None:
            return
        for parent in self._svg_root.iter():
            for child in list(parent):
                self._svg_parent_by_id[id(child)] = parent

    def _effective_attr(self, el: ET.Element, name: str, *, inherit: bool = True) -> str | None:
        val = _get_attr(el, name)
        if val is not None:
            return val
        if not inherit:
            return None
        parent = self._svg_parent_by_id.get(id(el))
        while parent is not None:
            parent_class_style = self._class_style_for(parent)
            if name in parent_class_style:
                return parent_class_style.get(name)
            val = _get_attr(parent, name)
            if val is not None:
                return val
            parent = self._svg_parent_by_id.get(id(parent))
        return None

    def _defs_descendant_ids(self) -> set[int]:
        if self._svg_root is None:
            return set()
        ids: set[int] = set()
        for el in self._svg_root.iter():
            if _strip_ns(el.tag) != "defs":
                continue
            for child in el.iter():
                ids.add(id(child))
        return ids

    def _find_marker_by_id(self, marker_id: str) -> ET.Element | None:
        if self._svg_root is None:
            return None
        for el in self._svg_root.iter():
            if _strip_ns(el.tag) != "marker":
                continue
            if (el.get("id") or "").strip() == marker_id:
                return el
        return None

    def _sanitize_lg_arrow_marker(self) -> None:
        marker = self._find_marker_by_id("lg-arrow")
        if marker is None:
            return
        marker.set("orient", "auto-start-reverse")
        marker.set("viewBox", f"0 0 {_format_num(_ARROW_MARKER_VIEWBOX)} {_format_num(_ARROW_MARKER_VIEWBOX)}")
        marker.set("markerUnits", "strokeWidth")
        if marker.get("refY") is None:
            marker.set("refY", "5")

        path = None
        for child in list(marker):
            if _strip_ns(child.tag) != "path":
                continue
            if path is None:
                path = child
            else:
                marker.remove(child)
        if path is None:
            ns = ""
            if marker.tag.startswith("{") and "}" in marker.tag:
                ns = marker.tag.split("}", 1)[0][1:]
            path = ET.Element(f"{{{ns}}}path") if ns else ET.Element("path")
            marker.append(path)

        for stale in (
            "style",
            "stroke-width",
            "stroke-dasharray",
            "stroke-linecap",
            "stroke-linejoin",
            "stroke-miterlimit",
            "transform",
            "class",
        ):
            path.attrib.pop(stale, None)
        path.set("d", "M 0 0 L 10 5 L 0 10 L 2 5 z")
        path.set("fill", "#000000")
        path.set("stroke", "none")

    def _effective_attr(self, el: ET.Element, name: str, *, inherit: bool = True) -> str | None:
        val = _get_attr(el, name)
        if val is not None:
            return val
        class_style = self._class_style_for(el)
        if name in class_style:
            return class_style.get(name)
        if not inherit:
            return None
        parent = self._svg_parent_by_id.get(id(el))
        while parent is not None:
            parent_class_style = self._class_style_for(parent)
            if name in parent_class_style:
                return parent_class_style.get(name)
            val = _get_attr(parent, name)
            if val is not None:
                return val
            parent = self._svg_parent_by_id.get(id(parent))
        return None

    def _sync_text_from_svg(self) -> None:
        if self._svg_root is None:
            return
        raw = ET.tostring(self._svg_root, encoding="unicode")
        if raw == self._last_svg_text_raw:
            return
        self._last_svg_text_raw = raw
        pretty = _pretty_xml(self._svg_root)
        try:
            self._suspend_text_sync = True
            insert_pos = self.text_input.index("insert")
            yview = self.text_input.yview()
            self.text_input.delete("1.0", "end")
            self.text_input.insert("1.0", pretty)
            try:
                self.text_input.mark_set("insert", insert_pos)
            except Exception:
                pass
            try:
                self.text_input.yview_moveto(yview[0])
            except Exception:
                pass
            self.text_input.edit_modified(False)
        finally:
            self._suspend_text_sync = False
        self._update_line_numbers()

    def _sync_bg_mode_from_svg(self) -> None:
        if self._svg_root is None:
            return
        rect = self._find_background_rect()
        if rect is None:
            self._bg_mode_var.set("sin fondo")
            self._update_bg_mode_combo_style()
            return
        fill = (_get_attr(rect, "fill") or "").strip().lower()
        if fill in ("", "none", "transparent"):
            self._bg_mode_var.set("sin fondo")
            self._update_bg_mode_combo_style()
            return
        if fill in ("#fff", "#ffffff", "white"):
            self._bg_mode_var.set("blanco")
            self._update_bg_mode_combo_style()
            return
        if fill in ("#000", "#000000", "black"):
            self._bg_mode_var.set("negro")
            self._update_bg_mode_combo_style()
            return
        self._bg_mode_var.set("sin fondo")
        self._update_bg_mode_combo_style()

    def _find_background_rect(self) -> ET.Element | None:
        if self._svg_root is None:
            return None
        for el in self._svg_root.iter():
            if _strip_ns(el.tag) != "rect":
                continue
            if self._is_background_rect_like(el):
                if el.get("data-kind") != "background":
                    el.set("data-kind", "background")
                return el
        return None

    def _is_background_rect_like(self, el: ET.Element) -> bool:
        if self._svg_root is None:
            return False
        if _strip_ns(el.tag) != "rect":
            return False
        if (el.get("data-kind") or "").strip() == "background":
            return True
        w_raw = (_get_attr(el, "width") or "").strip()
        h_raw = (_get_attr(el, "height") or "").strip()
        if w_raw == "100%" and h_raw == "100%":
            return True
        w = _parse_float(w_raw, 0.0)
        h = _parse_float(h_raw, 0.0)
        if w <= 0 or h <= 0:
            return False
        x = _parse_float(_get_attr(el, "x"), 0.0)
        y = _parse_float(_get_attr(el, "y"), 0.0)
        min_x, min_y, vb_w, vb_h = self._resolve_viewbox(self._svg_root)
        if abs(w - vb_w) <= 1e-6 and abs(h - vb_h) <= 1e-6:
            if abs(x - min_x) <= 1e-6 and abs(y - min_y) <= 1e-6:
                return True
        root_w = _parse_float(self._svg_root.get("width"), 0.0)
        root_h = _parse_float(self._svg_root.get("height"), 0.0)
        if root_w > 0 and root_h > 0:
            if abs(w - root_w) <= 1e-6 and abs(h - root_h) <= 1e-6:
                if abs(x) <= 1e-6 and abs(y) <= 1e-6:
                    return True
        return False

    def _ensure_background_rect(self) -> ET.Element | None:
        if self._svg_root is None:
            return None
        rect = self._find_background_rect()
        if rect is not None:
            return rect
        ns = ""
        tag = self._svg_root.tag
        if tag.startswith("{") and "}" in tag:
            ns = tag.split("}", 1)[0][1:]
        rect = ET.Element(f"{{{ns}}}rect") if ns else ET.Element("rect")
        rect.set("x", "0")
        rect.set("y", "0")
        rect.set("width", "100%")
        rect.set("height", "100%")
        rect.set("fill", "#ffffff")
        rect.set("stroke", "none")
        rect.set("data-kind", "background")
        self._svg_root.insert(0, rect)
        return rect

    def _on_bg_mode_change(self, _event=None) -> None:
        if self._svg_root is None:
            return
        mode = (self._bg_mode_var.get() or "").strip().lower()
        self._update_bg_mode_combo_style()
        self._push_history()
        rect = self._find_background_rect()
        if mode == "sin fondo":
            if rect is not None:
                _set_attr(rect, "fill", "none")
                _set_attr(rect, "stroke", "none")
            self._render_svg()
            return
        if rect is None:
            rect = self._ensure_background_rect()
        if rect is None:
            return
        if mode == "negro":
            _set_attr(rect, "fill", "#000000")
        else:
            _set_attr(rect, "fill", "#ffffff")
        _set_attr(rect, "stroke", "none")
        self._render_svg()

    def _segment_resize_geometry(
        self, line_el: ET.Element
    ) -> tuple[float, float, float, float] | None:
        raw = self._segment_resize_delta_var.get().strip()
        if not raw:
            return None
        try:
            delta = float(raw)
        except Exception as exc:
            raise ValueError("Ajuste invalido.") from exc
        if abs(delta) <= 1e-9:
            return None
        x1 = _parse_float(_get_attr(line_el, "x1"))
        y1 = _parse_float(_get_attr(line_el, "y1"))
        x2 = _parse_float(_get_attr(line_el, "x2"))
        y2 = _parse_float(_get_attr(line_el, "y2"))
        dx = x2 - x1
        dy = y2 - y1
        length = math.hypot(dx, dy)
        if length <= 1e-6:
            raise ValueError("Segmento degenerado.")
        ux = dx / length
        uy = dy / length
        mode = (self._segment_resize_mode_var.get() or "ambos").strip().lower()
        if mode == "derecha":
            nx1, ny1 = x1, y1
            nx2, ny2 = x2 + ux * delta, y2 + uy * delta
        elif mode == "izquierda":
            nx1, ny1 = x1 - ux * delta, y1 - uy * delta
            nx2, ny2 = x2, y2
        else:
            half = delta * 0.5
            nx1, ny1 = x1 - ux * half, y1 - uy * half
            nx2, ny2 = x2 + ux * half, y2 + uy * half
        new_len = math.hypot(nx2 - nx1, ny2 - ny1)
        if new_len <= 1e-6:
            raise ValueError("Ajuste deja el segmento sin longitud.")
        return (nx1, ny1, nx2, ny2)

    def _apply_segment_resize(
        self,
        _event=None,
        *,
        line_el: ET.Element | None = None,
        push_history: bool = True,
        render: bool = True,
        sync_helpers: bool = True,
        apply_constraints: bool = True,
        reset_delta: bool = True,
    ) -> bool:
        if self._svg_root is None:
            return False
        target = line_el
        if target is None:
            if not self._segment_editor_enabled:
                return False
            target = self._segment_selection_info(self._selected)
        if target is None:
            return False
        try:
            resize = self._segment_resize_geometry(target)
        except ValueError as exc:
            messagebox.showerror("Segmento", str(exc))
            return False
        if resize is None:
            return True
        nx1, ny1, nx2, ny2 = resize
        if push_history:
            self._push_history()
        _set_attr(target, "x1", _format_num(nx1))
        _set_attr(target, "y1", _format_num(ny1))
        _set_attr(target, "x2", _format_num(nx2))
        _set_attr(target, "y2", _format_num(ny2))
        if sync_helpers:
            if (target.get("data-kind") or "").strip() != "subsegment":
                self._sync_subsegments_from_parent(target)
            self._sync_segment_marks_from_editor(target)
            self._sync_segment_endpoints_from_editor(target)
            self._sync_segment_mid_labels_from_editor(target)
            self._rebuild_segment_dimension_from_line(target)
        if apply_constraints:
            self._apply_constraints(driver_el=target)
        if reset_delta:
            self._segment_resize_delta_var.set("0")
        if render:
            self._render_svg()
        return True

    def _segment_dimension_settings_from_editor(self) -> tuple[bool, float, str]:
        show = bool(self._segment_dim_show_var.get())
        side = (self._segment_dim_side_var.get() or "").strip()
        if side not in (_SEG_DIM_SIDE_POS, _SEG_DIM_SIDE_NEG):
            side = _SEG_DIM_SIDE_POS
        if not show:
            return (False, _SEG_DIM_DEFAULT_OFFSET, side)
        raw_off = (self._segment_dim_offset_var.get() or "").strip()
        if not raw_off:
            raise ValueError("Offset de cota invalido.")
        try:
            off = float(raw_off)
        except Exception:
            raise ValueError("Offset de cota invalido.")
        if off <= 0:
            raise ValueError("Offset de cota debe ser > 0.")
        return (True, off, side)

    def _segment_dimension_key(self, line_el: ET.Element, *, create: bool = True) -> str | None:
        key = (line_el.get(_SEG_DIM_KEY_ATTR) or "").strip()
        if key:
            return key
        if not create or self._svg_root is None:
            return None
        used: set[str] = set()
        for el in self._svg_root.iter():
            el_key = (el.get(_SEG_DIM_KEY_ATTR) or "").strip()
            if el_key:
                used.add(el_key)
            kind = (el.get("data-kind") or "").strip()
            if kind in (
                _SEG_DIM_LINE_DATA_KIND,
                _SEG_DIM_TICK_DATA_KIND,
                _SEG_DIM_EXT_DATA_KIND,
                _SEG_DIM_LABEL_DATA_KIND,
            ):
                pkey = (el.get("data-parent-key") or "").strip()
                if pkey:
                    used.add(pkey)
        idx = 1
        while True:
            cand = f"seg-dim-{idx}"
            if cand not in used:
                line_el.set(_SEG_DIM_KEY_ATTR, cand)
                return cand
            idx += 1

    def _segment_dimension_owner_line(self, line_el: ET.Element) -> ET.Element | None:
        if self._svg_root is None or _strip_ns(line_el.tag) != "line":
            return None
        kind = (line_el.get("data-kind") or "").strip()
        if kind != _SEG_DIM_LINE_DATA_KIND:
            return line_el
        key = (line_el.get("data-parent-key") or "").strip()
        if not key:
            return None
        for cand in self._svg_root.iter():
            if _strip_ns(cand.tag) != "line":
                continue
            if cand is line_el:
                continue
            cand_kind = (cand.get("data-kind") or "").strip()
            if cand_kind in (_SEG_DIM_LINE_DATA_KIND, _SEG_DIM_TICK_DATA_KIND, _SEG_DIM_EXT_DATA_KIND):
                continue
            if (cand.get(_SEG_DIM_KEY_ATTR) or "").strip() == key:
                return cand
        return None

    def _remove_segment_dimensions(self, line_el: ET.Element, *, clear_attrs: bool = False) -> None:
        if self._svg_root is None:
            return
        key = self._segment_dimension_key(line_el, create=False)
        if key:
            to_remove: list[ET.Element] = []
            for el in self._svg_root.iter():
                kind = (el.get("data-kind") or "").strip()
                if kind not in (
                    _SEG_DIM_LINE_DATA_KIND,
                    _SEG_DIM_TICK_DATA_KIND,
                    _SEG_DIM_EXT_DATA_KIND,
                    _SEG_DIM_LABEL_DATA_KIND,
                ):
                    continue
                if (el.get("data-parent-key") or "").strip() != key:
                    continue
                to_remove.append(el)
            for el in to_remove:
                parent = self._parent_of(el)
                if parent is not None:
                    try:
                        parent.remove(el)
                    except Exception:
                        pass
        if clear_attrs:
            line_el.attrib.pop(_SEG_DIM_SHOW_ATTR, None)
            line_el.attrib.pop(_SEG_DIM_OFFSET_ATTR, None)
            line_el.attrib.pop(_SEG_DIM_SIDE_ATTR, None)
            line_el.attrib.pop(_SEG_DIM_KEY_ATTR, None)

    def _rebuild_segment_dimension_from_line(
        self,
        line_el: ET.Element,
        *,
        force_style_from_global: bool = False,
    ) -> bool:
        if self._svg_root is None or _strip_ns(line_el.tag) != "line":
            return False
        owner_line = self._segment_dimension_owner_line(line_el)
        if owner_line is None:
            return False
        line_el = owner_line
        show = (line_el.get(_SEG_DIM_SHOW_ATTR) or "").strip() == "1"
        if not show:
            self._remove_segment_dimensions(line_el)
            return False
        try:
            x1 = _parse_float(_get_attr(line_el, "x1"))
            y1 = _parse_float(_get_attr(line_el, "y1"))
            x2 = _parse_float(_get_attr(line_el, "x2"))
            y2 = _parse_float(_get_attr(line_el, "y2"))
        except Exception:
            self._remove_segment_dimensions(line_el, clear_attrs=True)
            return False
        dx = x2 - x1
        dy = y2 - y1
        seg_len = math.hypot(dx, dy)
        if seg_len <= 1e-6:
            self._remove_segment_dimensions(line_el, clear_attrs=True)
            return False
        off = _parse_float((line_el.get(_SEG_DIM_OFFSET_ATTR) or "").strip(), _SEG_DIM_DEFAULT_OFFSET)
        if off <= 0:
            self._remove_segment_dimensions(line_el, clear_attrs=True)
            return False
        side = (line_el.get(_SEG_DIM_SIDE_ATTR) or "").strip()
        if side not in (_SEG_DIM_SIDE_POS, _SEG_DIM_SIDE_NEG):
            side = _SEG_DIM_SIDE_POS
        sign = 1.0 if side == _SEG_DIM_SIDE_POS else -1.0
        ux = dx / seg_len
        uy = dy / seg_len
        nx = -uy
        ny = ux
        ax = x1 + sign * off * nx
        ay = y1 + sign * off * ny
        bx = x2 + sign * off * nx
        by = y2 + sign * off * ny
        key = self._segment_dimension_key(line_el, create=True)
        if not key:
            return False
        dim_lines: list[ET.Element] = []
        ticks: list[ET.Element] = []
        legacy: list[ET.Element] = []
        for el in self._svg_root.iter():
            if (el.get("data-parent-key") or "").strip() != key:
                continue
            kind = (el.get("data-kind") or "").strip()
            if kind == _SEG_DIM_LINE_DATA_KIND:
                dim_lines.append(el)
            elif kind == _SEG_DIM_TICK_DATA_KIND:
                ticks.append(el)
            elif kind in (_SEG_DIM_EXT_DATA_KIND, _SEG_DIM_LABEL_DATA_KIND):
                legacy.append(el)

        global_stroke_raw = self._global_stroke_var.get().strip() if hasattr(self, "_global_stroke_var") else "3"
        stroke_w = _parse_float(global_stroke_raw, 3.0)
        if stroke_w <= 0:
            stroke_w = 3.0
        if not force_style_from_global and dim_lines:
            cand_w = _parse_float(_get_attr(dim_lines[0], "stroke-width"), stroke_w)
            if cand_w > 0:
                stroke_w = cand_w

        dim_line: ET.Element
        if dim_lines:
            dim_line = dim_lines[0]
        else:
            dim_line = ET.Element(self._svg_ns_tag("line"))
            self._svg_root.append(dim_line)

        for stale in dim_lines[1:] + ticks + legacy:
            parent = self._parent_of(stale)
            if parent is not None:
                try:
                    parent.remove(stale)
                except Exception:
                    pass

        stroke_w_s = _format_num(stroke_w)
        marker_id = self._ensure_dim_arrow_marker()
        dim_line.set("x1", _format_num(ax))
        dim_line.set("y1", _format_num(ay))
        dim_line.set("x2", _format_num(bx))
        dim_line.set("y2", _format_num(by))
        dim_line.set("data-kind", _SEG_DIM_LINE_DATA_KIND)
        dim_line.set("data-parent-key", key)
        _set_attr(dim_line, "stroke", "#000000")
        _set_attr(dim_line, "stroke-width", stroke_w_s)
        _set_attr(dim_line, "fill", "none")
        if marker_id:
            dim_line.set("marker-start", f"url(#{marker_id})")
            dim_line.set("marker-end", f"url(#{marker_id})")
        else:
            dim_line.attrib.pop("marker-start", None)
            dim_line.attrib.pop("marker-end", None)

        half_tick = 0.5 * _SEG_DIM_TICK_LEN
        tax1 = ax - half_tick * nx
        tay1 = ay - half_tick * ny
        tax2 = ax + half_tick * nx
        tay2 = ay + half_tick * ny
        tbx1 = bx - half_tick * nx
        tby1 = by - half_tick * ny
        tbx2 = bx + half_tick * nx
        tby2 = by + half_tick * ny

        tick_a = ET.Element(self._svg_ns_tag("line"))
        tick_a.set("x1", _format_num(tax1))
        tick_a.set("y1", _format_num(tay1))
        tick_a.set("x2", _format_num(tax2))
        tick_a.set("y2", _format_num(tay2))
        tick_a.set("data-kind", _SEG_DIM_TICK_DATA_KIND)
        tick_a.set("data-parent-key", key)
        _set_attr(tick_a, "stroke", "#000000")
        _set_attr(tick_a, "stroke-width", stroke_w_s)
        _set_attr(tick_a, "fill", "none")
        self._svg_root.append(tick_a)

        tick_b = ET.Element(self._svg_ns_tag("line"))
        tick_b.set("x1", _format_num(tbx1))
        tick_b.set("y1", _format_num(tby1))
        tick_b.set("x2", _format_num(tbx2))
        tick_b.set("y2", _format_num(tby2))
        tick_b.set("data-kind", _SEG_DIM_TICK_DATA_KIND)
        tick_b.set("data-parent-key", key)
        _set_attr(tick_b, "stroke", "#000000")
        _set_attr(tick_b, "stroke-width", stroke_w_s)
        _set_attr(tick_b, "fill", "none")
        self._svg_root.append(tick_b)

        line_el.set(_SEG_DIM_SHOW_ATTR, "1")
        line_el.set(_SEG_DIM_OFFSET_ATTR, _format_num(off))
        line_el.set(_SEG_DIM_SIDE_ATTR, side)
        return True

    def _sync_segment_dimensions(self) -> None:
        if self._svg_root is None:
            return
        keep_keys: set[str] = set()
        lines = [el for el in self._svg_root.iter() if _strip_ns(el.tag) == "line"]
        for line_el in lines:
            kind = (line_el.get("data-kind") or "").strip()
            if kind in (_SEG_DIM_LINE_DATA_KIND, _SEG_DIM_TICK_DATA_KIND, _SEG_DIM_EXT_DATA_KIND):
                continue
            if kind and _is_aux_data_kind(kind) and kind != "subsegment":
                continue
            show = (line_el.get(_SEG_DIM_SHOW_ATTR) or "").strip() == "1"
            if not show:
                self._remove_segment_dimensions(line_el)
                continue
            if self._rebuild_segment_dimension_from_line(line_el):
                key = self._segment_dimension_key(line_el, create=False)
                if key:
                    keep_keys.add(key)
            else:
                self._remove_segment_dimensions(line_el, clear_attrs=True)
        to_remove: list[ET.Element] = []
        for el in self._svg_root.iter():
            kind = (el.get("data-kind") or "").strip()
            if kind not in (
                _SEG_DIM_LINE_DATA_KIND,
                _SEG_DIM_TICK_DATA_KIND,
                _SEG_DIM_EXT_DATA_KIND,
                _SEG_DIM_LABEL_DATA_KIND,
            ):
                continue
            key = (el.get("data-parent-key") or "").strip()
            if key not in keep_keys:
                to_remove.append(el)
        for el in to_remove:
            parent = self._parent_of(el)
            if parent is not None:
                try:
                    parent.remove(el)
                except Exception:
                    pass

    def _apply_segment_editor_changes(self) -> None:
        if not self._segment_editor_enabled or self._svg_root is None:
            return
        target = self._segment_selection_info(self._selected)
        if target is None:
            messagebox.showerror("Segmento", "Selecciona una linea o subsegmento lineal.")
            return
        target_kind = (target.get("data-kind") or "").strip()
        is_dim_line = target_kind == _SEG_DIM_LINE_DATA_KIND
        dim_owner = self._segment_dimension_owner_line(target)
        if dim_owner is None:
            messagebox.showerror("Segmento", "No se encontro el segmento base de la cota.")
            return
        dim_owner_kind = (dim_owner.get("data-kind") or "").strip()
        dim_owner_is_subsegment = dim_owner_kind == "subsegment"

        resize = None
        if not is_dim_line:
            try:
                resize = self._segment_resize_geometry(target)
            except ValueError as exc:
                messagebox.showerror("Segmento", str(exc))
                return
        try:
            dim_show, dim_offset, dim_side = self._segment_dimension_settings_from_editor()
        except ValueError as exc:
            messagebox.showerror("Segmento", str(exc))
            return

        self._push_history()
        is_subsegment = target_kind == "subsegment"
        if is_subsegment:
            target.set("data-subsegment-override", "1")
        if dim_owner_is_subsegment:
            dim_owner.set("data-subsegment-override", "1")

        if not is_dim_line:
            if self._segment_dashed_var.get():
                pattern = self._dash_pattern_for_apply()
                _force_style_attr(target, "stroke-dasharray", pattern)
            else:
                _remove_style_attr(target, "stroke-dasharray")

            marker_id = None
            if self._segment_arrow_start_var.get() or self._segment_arrow_end_var.get():
                marker_id = self._ensure_arrow_marker()
            if self._segment_arrow_start_var.get() and marker_id:
                target.set("marker-start", f"url(#{marker_id})")
            else:
                target.attrib.pop("marker-start", None)
            if self._segment_arrow_end_var.get() and marker_id:
                target.set("marker-end", f"url(#{marker_id})")
            else:
                target.attrib.pop("marker-end", None)

            if resize is not None:
                nx1, ny1, nx2, ny2 = resize
                _set_attr(target, "x1", _format_num(nx1))
                _set_attr(target, "y1", _format_num(ny1))
                _set_attr(target, "x2", _format_num(nx2))
                _set_attr(target, "y2", _format_num(ny2))

            if not is_subsegment:
                self._sync_subsegments_from_parent(target)
        self._sync_segment_marks_from_editor(target)
        self._sync_segment_endpoints_from_editor(target)
        self._sync_segment_mid_labels_from_editor(target)
        if dim_show:
            dim_owner.set(_SEG_DIM_SHOW_ATTR, "1")
            dim_owner.set(_SEG_DIM_OFFSET_ATTR, _format_num(dim_offset))
            dim_owner.set(_SEG_DIM_SIDE_ATTR, dim_side)
            self._segment_dimension_key(dim_owner, create=True)
            self._rebuild_segment_dimension_from_line(dim_owner, force_style_from_global=True)
        else:
            self._remove_segment_dimensions(dim_owner, clear_attrs=True)
        if not is_dim_line:
            self._apply_constraints(driver_el=target)
        else:
            self._apply_constraints(driver_el=dim_owner)

        if hasattr(self, "_stroke_dash_enabled_var"):
            self._suspend_stroke_updates = True
            self._stroke_dash_enabled_var.set(bool(self._segment_dashed_var.get()))
            if self._segment_dashed_var.get() and hasattr(self, "_stroke_dash_var"):
                self._stroke_dash_var.set(
                    _get_attr(target, "stroke-dasharray") or self._stroke_dash_var.get()
                )
            self._suspend_stroke_updates = False
        self._segment_resize_delta_var.set("0")
        self._render_svg()

    def _clear_shade_diff_selection(self) -> None:
        self._shade_diff_base = None
        self._shade_diff_holes.clear()
        self._clear_shade_contour_runtime()
        if self._shade_diff_active:
            self._set_shade_status("Contorno: 0 borde(s). Click para iniciar. Esc para limpiar.")

    def _clear_group_selection(self) -> None:
        self._group_select_elements.clear()
        if self._group_select_active and hasattr(self, "_angle_create_status"):
            self._angle_create_status.config(text="Selecciona elementos para agrupar")

    def _group_status(self) -> None:
        if not self._group_select_active or not hasattr(self, "_angle_create_status"):
            return
        n = len(self._group_select_elements)
        self._angle_create_status.config(
            text=f"Grupo: {n} elemento(s). Shift=acumular, clic=grupo, doble clic=elemento. Enter/Agrupar para confirmar"
        )

    def _is_user_group(self, el: ET.Element | None) -> bool:
        if el is None:
            return False
        if _strip_ns(el.tag) != "g":
            return False
        kind = (el.get("data-kind") or "").strip()
        if kind == _GROUP_DATA_KIND:
            return True
        gid = (el.get("id") or "").strip()
        return gid.startswith(_GROUP_ID_PREFIX)

    def _group_ancestor(self, el: ET.Element | None) -> ET.Element | None:
        cur = el
        while cur is not None:
            parent = self._parent_of(cur)
            if parent is None:
                return None
            if self._is_user_group(parent):
                return parent
            cur = parent
        return None

    def _group_candidate_descendants(self, group_el: ET.Element) -> list[ET.Element]:
        out: list[ET.Element] = []
        for child in group_el.iter():
            if child is group_el:
                continue
            if self._is_shade_diff_candidate(child):
                out.append(child)
        return out

    def _next_group_id(self) -> str:
        if self._svg_root is None:
            return f"{_GROUP_ID_PREFIX}1"
        max_idx = 0
        for el in self._svg_root.iter():
            gid = (el.get("id") or "").strip()
            if not gid.startswith(_GROUP_ID_PREFIX):
                continue
            try:
                idx = int(gid[len(_GROUP_ID_PREFIX) :])
            except Exception:
                continue
            if idx > max_idx:
                max_idx = idx
        return f"{_GROUP_ID_PREFIX}{max_idx + 1}"

    def _is_group_candidate(self, el: ET.Element) -> bool:
        if self._is_user_group(el):
            return False
        if self._is_shade_contour_helper(el):
            return False
        kind = (el.get("data-kind") or "").strip()
        if _is_aux_data_kind(kind):
            return False
        tag = _strip_ns(el.tag)
        if tag in ("svg", "defs", "title", "desc", "marker"):
            return False
        if tag == "rect" and kind in ("background", "label-bg"):
            return False
        return tag in ("line", "polyline", "polygon", "path", "circle", "ellipse", "rect", "text")

    def _pick_group_candidate(self, x: float, y: float, zoom: float) -> _Record | None:
        candidates = self._collect_hit_candidates(x, y, zoom)
        filtered: list[tuple[float, _Drawable]] = []
        for dist, d in candidates:
            if d.record is None:
                continue
            if not self._is_group_candidate(d.record.el):
                continue
            filtered.append((dist, d))
        if not filtered:
            return None
        primary = [(dist, d) for dist, d in filtered if not self._is_subsegment_record(d.record)]
        if primary:
            filtered = primary
        return filtered[0][1].record

    def _event_has_shift(self, event: tk.Event) -> bool:
        try:
            state = int(getattr(event, "state", 0))
        except Exception:
            state = 0
        return bool(state & 0x0001)

    def _apply_group_selection_target(self, target: ET.Element, event: tk.Event) -> None:
        if not self._element_in_svg(target):
            return
        if self._event_has_shift(event):
            idx = -1
            for i, el in enumerate(self._group_select_elements):
                if el is target:
                    idx = i
                    break
            if idx >= 0:
                self._group_select_elements.pop(idx)
            else:
                self._group_select_elements.append(target)
        else:
            self._group_select_elements = [target]

    def _group_single_click_target(self, record: _Record) -> ET.Element:
        target = record.el
        group = self._group_ancestor(target)
        if group is not None:
            return group
        return target

    def _group_double_click_target(self, record: _Record) -> ET.Element:
        return record.el

    def _group_highlight_element_ids(self) -> set[int]:
        return set()

    def _group_descendant_highlight_ids(self, group_el: ET.Element) -> set[int]:
        out: set[int] = set()
        for child in group_el.iter():
            if child is group_el:
                continue
            if self._is_group_candidate(child):
                out.add(id(child))
        return out

    def _group_record_for_click(self, record: _Record) -> _Record:
        group = self._group_ancestor(record.el)
        if group is None:
            return record
        return _Record(el=group, tag="g", item_ids=[], kind="shape")

    def _handle_group_click(self, event: tk.Event) -> None:
        if self.canvas is None or self._svg_root is None:
            return
        x = float(self.canvas.canvasx(event.x))
        y = float(self.canvas.canvasy(event.y))
        try:
            zoom = float(self._view_scale.get())
        except Exception:
            zoom = 1.0
        if zoom <= 0:
            zoom = 1.0
        record = self._pick_group_candidate(x, y, zoom)
        if record is None:
            self._group_status()
            return
        self._select_record(record)
        target = self._group_single_click_target(record)
        self._apply_group_selection_target(target, event)
        self._group_status()

    def _handle_group_double_click(self, event: tk.Event) -> None:
        if self.canvas is None or self._svg_root is None:
            return
        x = float(self.canvas.canvasx(event.x))
        y = float(self.canvas.canvasy(event.y))
        try:
            zoom = float(self._view_scale.get())
        except Exception:
            zoom = 1.0
        if zoom <= 0:
            zoom = 1.0
        record = self._pick_group_candidate(x, y, zoom)
        if record is None:
            self._group_status()
            return
        self._select_record(record)
        target = self._group_double_click_target(record)
        self._apply_group_selection_target(target, event)
        self._group_status()

    def _toggle_group_select(self) -> None:
        self._group_select_active = False
        self._group_select_var.set(False)
        self._group_select_elements.clear()

    def _apply_group_selection(self) -> None:
        return

    def _ungroup_selected_parent(self) -> None:
        return

    def _set_shade_status(self, msg: str) -> None:
        if hasattr(self, "_angle_create_status"):
            self._angle_create_status.config(text=msg)
        self._set_transform_status(msg)

    def _set_intersection_status(self, msg: str) -> None:
        if hasattr(self, "_angle_create_status"):
            self._angle_create_status.config(text=msg)
        self._set_transform_status(msg)

    def _set_curve_radius_status(self, msg: str) -> None:
        if hasattr(self, "_angle_create_status"):
            self._angle_create_status.config(text=msg)
        self._set_transform_status(msg)

    def _shade_pending_click_count(self) -> int:
        pending = list(self.__dict__.get("_shade_pending_click_tokens", []))
        after_id = self.__dict__.get("_shade_pending_click_after_id")
        if after_id and not any(token == after_id for token, _pos in pending):
            ev = self.__dict__.get("_shade_pending_click_event") or (0, 0)
            pending.append((after_id, ev))
        return len(pending)

    def _shade_diff_status(self) -> None:
        if not self._shade_diff_active:
            return
        n = len(self._shade_contour_edges)
        pending = self._shade_pending_click_count()
        pending_txt = f" (+{pending} pendiente(s))" if pending > 0 else ""
        if n <= 0:
            self._set_shade_status(f"Contorno: 0 borde(s){pending_txt}. Click para iniciar. Esc para limpiar.")
            return
        try:
            zoom = float(self._view_scale.get())
        except Exception:
            zoom = 1.0
        if zoom <= 0:
            zoom = 1.0
        tol = self._shade_contour_tol_svg(zoom)
        _pts, closed = self._shade_contour_points_from_edges(self._shade_contour_edges, tol=tol)
        if closed:
            self._set_shade_status(
                f"Contorno: {n} borde(s){pending_txt} (cerrado). Aplicar para confirmar. Esc para limpiar."
            )
            return
        self._set_shade_status(
            f"Contorno: {n} borde(s){pending_txt}. Click para continuar. Doble click para sub-borde. Aplicar para confirmar. Esc para limpiar."
        )

    def _toggle_shade_diff(self) -> None:
        self._cancel_pending_shade_click()
        self._shade_diff_active = bool(self._shade_diff_var.get())
        if self._shade_diff_active:
            if self._segment_create_var.get():
                self._segment_create_var.set(False)
                self._toggle_segment_create()
            if self._angle_create_var.get():
                self._angle_create_var.set(False)
                self._toggle_angle_create()
            if self._intersection_create_var.get():
                self._intersection_create_var.set(False)
                self._toggle_intersection_create()
            if self._curve_radius_create_var.get():
                self._curve_radius_create_var.set(False)
                self._toggle_curve_radius_create()
            if self._projection_create_var.get():
                self._projection_create_var.set(False)
                self._toggle_projection_create()
            self._clear_shade_diff_selection()
            if self._normalize_imported_svg_for_editor():
                self._render_svg()
            self._normalize_shade_diff_opacity()
            self._shade_diff_status()
        else:
            self._clear_shade_diff_selection()
            if hasattr(self, "_angle_create_status"):
                self._angle_create_status.config(text="")
            self._set_transform_status("Listo.")

    def _toggle_angle_create(self) -> None:
        self._angle_create_active = bool(self._angle_create_var.get())
        self._angle_create_points.clear()
        self._angle_create_segments.clear()
        self._angle_create_mode = None
        if self._angle_create_active:
            if self._segment_create_var.get():
                self._segment_create_var.set(False)
                self._toggle_segment_create()
            if self._intersection_create_var.get():
                self._intersection_create_var.set(False)
                self._toggle_intersection_create()
            if self._curve_radius_create_var.get():
                self._curve_radius_create_var.set(False)
                self._toggle_curve_radius_create()
            if self._projection_create_var.get():
                self._projection_create_var.set(False)
                self._toggle_projection_create()
            if self._shade_diff_var.get():
                self._shade_diff_var.set(False)
                self._toggle_shade_diff()
            if hasattr(self, "_angle_create_status"):
                self._angle_create_status.config(text="Selecciona punto o segmento")
            self._set_transform_status("Selecciona punto o segmento")
        else:
            if hasattr(self, "_angle_create_status"):
                self._angle_create_status.config(text="")
            self._set_transform_status("Listo.")

    def _toggle_segment_create(self) -> None:
        self._segment_create_active = bool(self._segment_create_var.get())
        self._segment_create_points.clear()
        if self._segment_create_active:
            if self._angle_create_var.get():
                self._angle_create_var.set(False)
                self._toggle_angle_create()
            if self._intersection_create_var.get():
                self._intersection_create_var.set(False)
                self._toggle_intersection_create()
            if self._curve_radius_create_var.get():
                self._curve_radius_create_var.set(False)
                self._toggle_curve_radius_create()
            if self._projection_create_var.get():
                self._projection_create_var.set(False)
                self._toggle_projection_create()
            if self._shade_diff_var.get():
                self._shade_diff_var.set(False)
                self._toggle_shade_diff()
            if hasattr(self, "_angle_create_status"):
                self._angle_create_status.config(text="Selecciona punto 1")
            self._set_transform_status("Selecciona punto 1")
        else:
            if hasattr(self, "_angle_create_status"):
                self._angle_create_status.config(text="")
            self._set_transform_status("Listo.")

    def _toggle_intersection_create(self) -> None:
        self._intersection_create_active = bool(self._intersection_create_var.get())
        self._intersection_create_first = None
        if self._intersection_create_active:
            if self._segment_create_var.get():
                self._segment_create_var.set(False)
                self._toggle_segment_create()
            if self._angle_create_var.get():
                self._angle_create_var.set(False)
                self._toggle_angle_create()
            if self._curve_radius_create_var.get():
                self._curve_radius_create_var.set(False)
                self._toggle_curve_radius_create()
            if self._projection_create_var.get():
                self._projection_create_var.set(False)
                self._toggle_projection_create()
            if self._shade_diff_var.get():
                self._shade_diff_var.set(False)
                self._toggle_shade_diff()
            self._set_intersection_status("Interseccion: selecciona objeto 1.")
        else:
            if hasattr(self, "_angle_create_status"):
                self._angle_create_status.config(text="")
            self._set_transform_status("Listo.")

    def _toggle_curve_radius_create(self) -> None:
        self._curve_radius_create_active = bool(self._curve_radius_create_var.get())
        self._curve_radius_create_center_el = None
        if self._curve_radius_create_active:
            if self._segment_create_var.get():
                self._segment_create_var.set(False)
                self._toggle_segment_create()
            if self._angle_create_var.get():
                self._angle_create_var.set(False)
                self._toggle_angle_create()
            if self._intersection_create_var.get():
                self._intersection_create_var.set(False)
                self._toggle_intersection_create()
            if self._projection_create_var.get():
                self._projection_create_var.set(False)
                self._toggle_projection_create()
            if self._shade_diff_var.get():
                self._shade_diff_var.set(False)
                self._toggle_shade_diff()
            self._set_curve_radius_status("Radio: selecciona centro (punto geometrico).")
        else:
            if hasattr(self, "_angle_create_status"):
                self._angle_create_status.config(text="")
            self._set_transform_status("Listo.")

    def _toggle_projection_create(self) -> None:
        self._projection_create_active = bool(self._projection_create_var.get())
        self._projection_create_source = None
        if self._projection_create_active:
            if self._segment_create_var.get():
                self._segment_create_var.set(False)
                self._toggle_segment_create()
            if self._angle_create_var.get():
                self._angle_create_var.set(False)
                self._toggle_angle_create()
            if self._intersection_create_var.get():
                self._intersection_create_var.set(False)
                self._toggle_intersection_create()
            if self._curve_radius_create_var.get():
                self._curve_radius_create_var.set(False)
                self._toggle_curve_radius_create()
            if self._shade_diff_var.get():
                self._shade_diff_var.set(False)
                self._toggle_shade_diff()
            self._set_projection_status("Proyeccion: selecciona punto o segmento origen.")
        else:
            if hasattr(self, "_angle_create_status"):
                self._angle_create_status.config(text="")
            self._set_transform_status("Listo.")

    def _set_projection_status(self, msg: str) -> None:
        if hasattr(self, "_angle_create_status"):
            self._angle_create_status.config(text=msg)
        self._set_transform_status(msg)

    def _handle_segment_create_click(self, event: tk.Event) -> None:
        if self.canvas is None:
            return
        x = float(self.canvas.canvasx(event.x))
        y = float(self.canvas.canvasy(event.y))
        try:
            zoom = float(self._view_scale.get())
        except Exception:
            zoom = 1.0
        if zoom <= 0:
            zoom = 1.0
        hit = self._pick_point_element(x, y, zoom)
        if hit is None:
            if hasattr(self, "_angle_create_status"):
                self._angle_create_status.config(text="Selecciona un punto")
            self._set_transform_status("Selecciona un punto")
            return
        self._segment_create_points.append(hit)
        if len(self._segment_create_points) == 1:
            if hasattr(self, "_angle_create_status"):
                self._angle_create_status.config(text="Selecciona punto 2")
            self._set_transform_status("Selecciona punto 2")
            return
        if len(self._segment_create_points) >= 2:
            p1, p2 = self._segment_create_points[:2]
            self._segment_create_points.clear()
            self._create_segment_from_points(p1, p2)
            if self._segment_create_active and hasattr(self, "_angle_create_status"):
                self._angle_create_status.config(text="Selecciona punto 1")
            if self._segment_create_active:
                self._set_transform_status("Selecciona punto 1")

    def _handle_intersection_create_click(self, event: tk.Event) -> None:
        if self.canvas is None or self._svg_root is None:
            return
        x = float(self.canvas.canvasx(event.x))
        y = float(self.canvas.canvasy(event.y))
        try:
            zoom = float(self._view_scale.get())
        except Exception:
            zoom = 1.0
        if zoom <= 0:
            zoom = 1.0
        record = self._pick_intersection_candidate(x, y, zoom)
        if record is None:
            msg = (
                "Interseccion: selecciona objeto 2."
                if self._intersection_create_first is not None
                else "Interseccion: selecciona un objeto."
            )
            self._set_intersection_status(msg)
            return
        self._select_record(record)
        target = record.el
        if self._intersection_create_first is None:
            self._intersection_create_first = target
            self._set_intersection_status("Interseccion: selecciona objeto 2.")
            return
        if target is self._intersection_create_first:
            self._set_intersection_status("Interseccion: selecciona otro objeto.")
            return
        ref_pt = self._canvas_to_svg(x, y, zoom)
        near_tol = max(1e-9, _DEFAULT_INTERSECTION_NEAR_TOL_PX / max(zoom, 1e-6))
        points = self._intersections_for_elements(self._intersection_create_first, target, near_tol=near_tol)
        if not points:
            messagebox.showerror("Interseccion", "No hay interseccion entre los objetos.")
            self._set_intersection_status("Interseccion: no hay corte. Selecciona otro objeto 2.")
            return
        picked = self._pick_intersection_point(points, ref_pt)
        if picked is None:
            messagebox.showerror("Interseccion", "No se pudo determinar la interseccion.")
            self._set_intersection_status("Interseccion: no se pudo determinar el punto. Selecciona objeto 2.")
            return
        self._create_intersection_point(
            picked[0],
            picked[1],
            self._intersection_create_first,
            target,
            zoom=zoom,
        )
        self._intersection_create_first = None
        if self._intersection_create_active:
            self._set_intersection_status("Interseccion creada. Selecciona objeto 1.")

    def _pick_geometric_point_record(self, x: float, y: float, zoom: float) -> _Record | None:
        candidates = self._collect_hit_candidates(x, y, zoom, kinds=("circle",))
        for dist, d in candidates:
            if d.record is None:
                continue
            el = d.record.el
            if _strip_ns(el.tag) != "circle":
                continue
            if not self._is_point_circle(el):
                continue
            if dist > 6.0:
                continue
            if el.get("data-angle-id") is not None or el.get("data-angle-kind") == "point":
                continue
            kind = (el.get("data-kind") or "").strip()
            if kind in ("seg-mark", "seg-endpoint"):
                continue
            return d.record
        return None

    def _is_curve_radius_curve_candidate(self, el: ET.Element) -> bool:
        tag = _strip_ns(el.tag)
        kind = (el.get("data-kind") or "").strip()
        if kind and _is_aux_data_kind(kind) and kind != "subsegment":
            return False
        if tag == "circle":
            if self._is_point_circle(el):
                return False
            r = _parse_float(_get_attr(el, "r"), 0.0)
            if r <= 0:
                return False
            return not self._intersection_candidate_is_hidden(el, tag)
        if tag == "ellipse":
            rx = _parse_float(_get_attr(el, "rx"), 0.0)
            ry = _parse_float(_get_attr(el, "ry"), 0.0)
            if rx <= 0 or ry <= 0:
                return False
            return not self._intersection_candidate_is_hidden(el, tag)
        if tag == "path":
            if el.get("data-text") is not None:
                return False
            d = _get_attr(el, "d") or ""
            if not any(len(pts) >= 2 for pts, _closed in _parse_svg_path(d)):
                return False
            return not self._intersection_candidate_is_hidden(el, tag)
        return False

    def _pick_curve_radius_curve_record(self, x: float, y: float, zoom: float) -> _Record | None:
        candidates = self._collect_hit_candidates(
            x,
            y,
            zoom,
            kinds=("circle", "ellipse", "polyline", "polygon"),
        )
        for dist, d in candidates:
            if d.record is None:
                continue
            if dist > 6.0:
                continue
            if not self._is_curve_radius_curve_candidate(d.record.el):
                continue
            return d.record
        return None

    def _project_radius_target_on_curve(
        self,
        curve_el: ET.Element,
        px: float,
        py: float,
    ) -> tuple[float, float, float, bool, float] | None:
        points, closed = self._curve_subsegment_points(curve_el, click_svg=(px, py))
        if len(points) < 2:
            return None
        proj = self._project_point_on_polyline(points, closed=closed, px=px, py=py)
        if proj is None:
            return None
        _dist, s_raw, _qx, _qy, total_len = proj
        if total_len <= 1e-9:
            return None
        if closed:
            s_use = s_raw % total_len
        else:
            s_use = max(0.0, min(total_len, s_raw))
        qx, qy = self._polyline_point_at_s(points, closed=closed, s=s_use)
        return (s_use, qx, qy, closed, total_len)

    def _create_curve_radius_line(
        self,
        center_point_el: ET.Element,
        curve_el: ET.Element,
        *,
        s_on_curve: float,
        end_x: float,
        end_y: float,
    ) -> ET.Element | None:
        if self._svg_root is None:
            return None
        center_id = self._ensure_element_id(center_point_el, prefix="pt")
        curve_id = self._ensure_element_id(curve_el, prefix="shape")
        cx = _parse_float(_get_attr(center_point_el, "cx"))
        cy = _parse_float(_get_attr(center_point_el, "cy"))
        ns = ""
        tag = self._svg_root.tag
        if tag.startswith("{") and "}" in tag:
            ns = tag.split("}", 1)[0][1:]
        line_el = ET.Element(f"{{{ns}}}line") if ns else ET.Element("line")
        line_el.set("x1", _format_num(cx))
        line_el.set("y1", _format_num(cy))
        line_el.set("x2", _format_num(end_x))
        line_el.set("y2", _format_num(end_y))
        stroke = _get_attr(curve_el, "stroke") or "#000000"
        if str(stroke).strip().lower() in ("", "none", "transparent"):
            stroke = "#000000"
        stroke_w = _get_attr(curve_el, "stroke-width") or (self._global_stroke_var.get().strip() or "2")
        _set_attr(line_el, "stroke", stroke)
        _set_attr(line_el, "stroke-width", stroke_w)
        _set_attr(line_el, "fill", "none")
        line_el.set("data-kind", _CURVE_RADIUS_DATA_KIND)
        line_el.set("data-radius-center-id", center_id)
        line_el.set("data-radius-curve-id", curve_id)
        line_el.set("data-radius-s", _format_num(s_on_curve))
        return line_el

    def _handle_curve_radius_create_click(self, event: tk.Event) -> None:
        if self.canvas is None or self._svg_root is None:
            return
        x = float(self.canvas.canvasx(event.x))
        y = float(self.canvas.canvasy(event.y))
        try:
            zoom = float(self._view_scale.get())
        except Exception:
            zoom = 1.0
        if zoom <= 0:
            zoom = 1.0
        if self._curve_radius_create_center_el is None:
            center_record = self._pick_geometric_point_record(x, y, zoom)
            if center_record is None:
                self._set_curve_radius_status("Radio: selecciona centro (punto geometrico).")
                return
            self._curve_radius_create_center_el = center_record.el
            self._select_record(center_record)
            self._set_curve_radius_status("Radio: selecciona curva.")
            return
        if not self._element_in_svg(self._curve_radius_create_center_el):
            self._curve_radius_create_center_el = None
            self._set_curve_radius_status("Radio: centro invalido. Selecciona centro (punto geometrico).")
            return
        curve_record = self._pick_curve_radius_curve_record(x, y, zoom)
        if curve_record is None:
            self._set_curve_radius_status("Radio: selecciona una curva valida.")
            return
        center_el = self._curve_radius_create_center_el
        curve_el = curve_record.el
        sx, sy = self._canvas_to_svg(x, y, zoom)
        projected = self._project_radius_target_on_curve(curve_el, sx, sy)
        if projected is None:
            self._set_curve_radius_status("Radio: no se pudo proyectar sobre la curva.")
            return
        s_use, qx, qy, _closed, _total = projected
        line_el = self._create_curve_radius_line(
            center_el,
            curve_el,
            s_on_curve=s_use,
            end_x=qx,
            end_y=qy,
        )
        if line_el is None:
            self._set_curve_radius_status("Radio: no se pudo crear.")
            return
        self._push_history()
        self._svg_root.append(line_el)
        self._curve_radius_create_center_el = None
        self._render_svg()
        for record in self._records:
            if record.el is line_el:
                self._select_record(record)
                break
        self._set_curve_radius_status("Radio creado. Selecciona centro (punto geometrico).")

    def _line_endpoints(self, line_el: ET.Element) -> tuple[float, float, float, float] | None:
        if _strip_ns(line_el.tag) != "line":
            return None
        try:
            return (
                _parse_float(_get_attr(line_el, "x1")),
                _parse_float(_get_attr(line_el, "y1")),
                _parse_float(_get_attr(line_el, "x2")),
                _parse_float(_get_attr(line_el, "y2")),
            )
        except Exception:
            return None

    def _project_point_to_line_coords(
        self,
        px: float,
        py: float,
        line_el: ET.Element,
    ) -> tuple[float, float] | None:
        pts = self._line_endpoints(line_el)
        if pts is None:
            return None
        x1, y1, x2, y2 = pts
        dx = x2 - x1
        dy = y2 - y1
        denom = dx * dx + dy * dy
        if denom <= 1e-9:
            return None
        t = ((px - x1) * dx + (py - y1) * dy) / denom
        return (x1 + dx * t, y1 + dy * t)

    def _projection_ref_for_point(self, point_el: ET.Element) -> str:
        point_id = self._ensure_element_id(point_el, prefix="pt")
        return f"point:{point_id}"

    def _projection_ref_for_line_endpoint(self, line_el: ET.Element, role: str) -> str:
        line_id = self._ensure_element_id(line_el, prefix="line")
        role = "end" if role == "end" else "start"
        return f"line-{role}:{line_id}"

    def _projection_source_point_coords(self, source: tuple[str, ET.Element], role: str = "point") -> tuple[float, float] | None:
        source_kind, source_el = source
        if source_kind == "point":
            return (_parse_float(_get_attr(source_el, "cx")), _parse_float(_get_attr(source_el, "cy")))
        pts = self._line_endpoints(source_el)
        if pts is None:
            return None
        x1, y1, x2, y2 = pts
        if role == "end":
            return (x2, y2)
        return (x1, y1)

    def _pick_projection_line_record(self, x: float, y: float, zoom: float) -> _Record | None:
        candidates = self._collect_hit_candidates(x, y, zoom, kinds=("line",))
        for dist, d in candidates:
            if dist > 6.0 or d.record is None:
                continue
            el = d.record.el
            if _strip_ns(el.tag) != "line":
                continue
            kind = (el.get("data-kind") or "").strip()
            if kind in ("seg-mark", "seg-endpoint"):
                continue
            pts = self._line_endpoints(el)
            if pts is None:
                continue
            x1, y1, x2, y2 = pts
            if math.hypot(x2 - x1, y2 - y1) <= 1e-9:
                continue
            return d.record
        return None

    def _pick_projection_source_record(self, x: float, y: float, zoom: float) -> tuple[str, ET.Element, _Record] | None:
        point_record = self._pick_geometric_point_record(x, y, zoom)
        line_record = self._pick_projection_line_record(x, y, zoom)
        if point_record is not None:
            return ("point", point_record.el, point_record)
        if line_record is not None:
            return ("segment", line_record.el, line_record)
        return None

    def _assign_new_element_id(
        self,
        el: ET.Element,
        *,
        prefix: str,
        reserved_ids: set[str],
    ) -> str:
        cur = (el.get("id") or "").strip()
        if cur:
            reserved_ids.add(cur)
            return cur
        used: set[str] = set(reserved_ids)
        if self._svg_root is not None:
            for existing in self._svg_root.iter():
                el_id = (existing.get("id") or "").strip()
                if el_id:
                    used.add(el_id)
        idx = 1
        while True:
            cand = f"{prefix}-{idx}"
            if cand not in used:
                el.set("id", cand)
                reserved_ids.add(cand)
                return cand
            idx += 1

    def _new_projection_point(
        self,
        x: float,
        y: float,
        *,
        source_ref: str,
        target_line: ET.Element,
        role: str,
        reserved_ids: set[str],
    ) -> ET.Element:
        circle = ET.Element(self._svg_ns_tag("circle"))
        circle.set("cx", _format_num(x))
        circle.set("cy", _format_num(y))
        r = _parse_float(self._global_point_radius_var.get().strip(), 6.0)
        if r <= 0:
            r = 6.0
        circle.set("r", _format_num(r))
        _set_attr(circle, "fill", "#000000")
        _set_attr(circle, "stroke", "none")
        circle.set("data-kind", "point")
        circle.set("data-point-kind", "projection")
        circle.set(_PROJECTION_SOURCE_ATTR, source_ref)
        circle.set(_PROJECTION_TARGET_ATTR, self._ensure_element_id(target_line, prefix="line"))
        circle.set("data-projection-role", role)
        self._assign_new_element_id(circle, prefix="proj", reserved_ids=reserved_ids)
        return circle

    def _new_projection_line(
        self,
        p1: tuple[float, float],
        p2: tuple[float, float],
        *,
        start_ref: str,
        end_ref: str,
        kind: str,
        dashed: bool,
        target_style_el: ET.Element | None = None,
        reserved_ids: set[str],
    ) -> ET.Element | None:
        x1, y1 = p1
        x2, y2 = p2
        if math.hypot(x2 - x1, y2 - y1) <= 1e-6:
            return None
        line = ET.Element(self._svg_ns_tag("line"))
        _set_attr(line, "x1", _format_num(x1))
        _set_attr(line, "y1", _format_num(y1))
        _set_attr(line, "x2", _format_num(x2))
        _set_attr(line, "y2", _format_num(y2))
        stroke = "#000000"
        stroke_w = self._global_stroke_var.get().strip() or "2"
        if target_style_el is not None:
            raw_stroke = self._effective_attr(target_style_el, "stroke")
            if raw_stroke and str(raw_stroke).strip().lower() not in ("none", "transparent"):
                stroke = str(raw_stroke)
            raw_w = self._effective_attr(target_style_el, "stroke-width")
            if raw_w:
                stroke_w = str(raw_w)
        _set_attr(line, "stroke", stroke)
        _set_attr(line, "stroke-width", stroke_w)
        _set_attr(line, "fill", "none")
        if dashed:
            pattern = self._normalize_dash_pattern(self._global_dash_var.get().strip() if hasattr(self, "_global_dash_var") else "")
            _force_style_attr(line, "stroke-dasharray", pattern or "4,3")
        line.set("data-kind", kind)
        line.set(_LINE_START_REF_ATTR, start_ref)
        line.set(_LINE_END_REF_ATTR, end_ref)
        self._assign_new_element_id(line, prefix="proj-line", reserved_ids=reserved_ids)
        return line

    def _create_projection_from_point(
        self,
        source_point: ET.Element,
        target_line: ET.Element,
        *,
        zoom: float,
    ) -> list[ET.Element]:
        sx = _parse_float(_get_attr(source_point, "cx"))
        sy = _parse_float(_get_attr(source_point, "cy"))
        foot = self._project_point_to_line_coords(sx, sy, target_line)
        if foot is None:
            return []
        reserved_ids: set[str] = set()
        source_ref = self._projection_ref_for_point(source_point)
        foot_el = self._new_projection_point(
            foot[0],
            foot[1],
            source_ref=source_ref,
            target_line=target_line,
            role="point",
            reserved_ids=reserved_ids,
        )
        foot_ref = self._projection_ref_for_point(foot_el)
        helper = self._new_projection_line(
            (sx, sy),
            foot,
            start_ref=source_ref,
            end_ref=foot_ref,
            kind=_PROJECTION_HELPER_DATA_KIND,
            dashed=True,
            target_style_el=target_line,
            reserved_ids=reserved_ids,
        )
        out = [foot_el]
        if helper is not None:
            out.append(helper)
        return out

    def _create_projection_from_segment(
        self,
        source_line: ET.Element,
        target_line: ET.Element,
        *,
        zoom: float,
    ) -> list[ET.Element]:
        pts = self._line_endpoints(source_line)
        if pts is None:
            return []
        x1, y1, x2, y2 = pts
        start_ref = self._projection_ref_for_line_endpoint(source_line, "start")
        end_ref = self._projection_ref_for_line_endpoint(source_line, "end")
        foot_start = self._project_point_to_line_coords(x1, y1, target_line)
        foot_end = self._project_point_to_line_coords(x2, y2, target_line)
        if foot_start is None or foot_end is None:
            return []
        reserved_ids: set[str] = set()
        foot_a = self._new_projection_point(
            foot_start[0],
            foot_start[1],
            source_ref=start_ref,
            target_line=target_line,
            role="start",
            reserved_ids=reserved_ids,
        )
        foot_b = self._new_projection_point(
            foot_end[0],
            foot_end[1],
            source_ref=end_ref,
            target_line=target_line,
            role="end",
            reserved_ids=reserved_ids,
        )
        foot_a_ref = self._projection_ref_for_point(foot_a)
        foot_b_ref = self._projection_ref_for_point(foot_b)
        out: list[ET.Element] = [foot_a, foot_b]
        helper_a = self._new_projection_line(
            (x1, y1),
            foot_start,
            start_ref=start_ref,
            end_ref=foot_a_ref,
            kind=_PROJECTION_HELPER_DATA_KIND,
            dashed=True,
            target_style_el=target_line,
            reserved_ids=reserved_ids,
        )
        helper_b = self._new_projection_line(
            (x2, y2),
            foot_end,
            start_ref=end_ref,
            end_ref=foot_b_ref,
            kind=_PROJECTION_HELPER_DATA_KIND,
            dashed=True,
            target_style_el=target_line,
            reserved_ids=reserved_ids,
        )
        projection_line = self._new_projection_line(
            foot_start,
            foot_end,
            start_ref=foot_a_ref,
            end_ref=foot_b_ref,
            kind=_PROJECTION_SEGMENT_DATA_KIND,
            dashed=False,
            target_style_el=target_line,
            reserved_ids=reserved_ids,
        )
        if helper_a is not None:
            out.append(helper_a)
        if helper_b is not None:
            out.append(helper_b)
        if projection_line is not None:
            out.append(projection_line)
        return out

    def _handle_projection_create_click(self, event: tk.Event) -> None:
        if self.canvas is None or self._svg_root is None:
            return
        x = float(self.canvas.canvasx(event.x))
        y = float(self.canvas.canvasy(event.y))
        try:
            zoom = float(self._view_scale.get())
        except Exception:
            zoom = 1.0
        if zoom <= 0:
            zoom = 1.0
        if self._projection_create_source is None:
            source = self._pick_projection_source_record(x, y, zoom)
            if source is None:
                self._set_projection_status("Proyeccion: selecciona punto o segmento origen.")
                return
            kind, el, record = source
            self._projection_create_source = (kind, el)
            self._select_record(record)
            self._set_projection_status("Proyeccion: selecciona recta destino.")
            return
        source_kind, source_el = self._projection_create_source
        if not self._element_in_svg(source_el):
            self._projection_create_source = None
            self._set_projection_status("Proyeccion: origen invalido. Selecciona punto o segmento origen.")
            return
        target_record = self._pick_projection_line_record(x, y, zoom)
        if target_record is None:
            self._set_projection_status("Proyeccion: selecciona una recta destino.")
            return
        target_line = self._intersection_split_parent_target(target_record.el) or target_record.el
        if source_kind == "segment" and target_line is source_el:
            self._set_projection_status("Proyeccion: selecciona otra recta destino.")
            return
        self._select_record(target_record)
        if source_kind == "point":
            new_elements = self._create_projection_from_point(source_el, target_line, zoom=zoom)
        else:
            new_elements = self._create_projection_from_segment(source_el, target_line, zoom=zoom)
        if not new_elements:
            self._set_projection_status("Proyeccion: no se pudo construir.")
            return
        self._push_history()
        for el in new_elements:
            self._svg_root.append(el)
        self._apply_constraints()
        self._split_intersection_target_on_points(target_line, zoom=zoom)
        self._projection_create_source = None
        self._render_svg()
        preferred = None
        for el in reversed(new_elements):
            if _strip_ns(el.tag) == "line" and (el.get("data-kind") or "").strip() == _PROJECTION_SEGMENT_DATA_KIND:
                preferred = el
                break
        if preferred is None:
            preferred = new_elements[0]
        for record in self._records:
            if record.el is preferred:
                self._select_record(record)
                break
        self._set_projection_status("Proyeccion creada. Selecciona punto o segmento origen.")

    def _default_line_class(self) -> str | None:
        if self._selected is not None and _strip_ns(self._selected.el.tag) == "line":
            class_attr = (self._selected.el.get("class") or "").strip()
            if class_attr:
                return class_attr
        if self._svg_root is None:
            return None
        if not self._class_styles:
            self._class_styles = self._collect_css_class_styles(self._svg_root)
        for name in ("seg", "line", "aux"):
            if name in self._class_styles:
                return name
        for el in self._svg_root.iter():
            if _strip_ns(el.tag) != "line":
                continue
            class_attr = (el.get("class") or "").strip()
            if class_attr:
                return class_attr
        return None

    def _segment_exists_between_points(
        self,
        p1: tuple[float, float],
        p2: tuple[float, float],
        tol: float = 1e-6,
    ) -> bool:
        if self._svg_root is None:
            return False
        ax, ay = p1
        bx, by = p2
        for el in self._svg_root.iter():
            if _strip_ns(el.tag) != "line":
                continue
            kind = (el.get("data-kind") or "").strip()
            if kind in ("seg-mark", "circle-radius", _CURVE_RADIUS_DATA_KIND, "seg-endpoint", "subsegment"):
                continue
            if _is_aux_data_kind(kind):
                continue
            x1 = _parse_float(_get_attr(el, "x1"))
            y1 = _parse_float(_get_attr(el, "y1"))
            x2 = _parse_float(_get_attr(el, "x2"))
            y2 = _parse_float(_get_attr(el, "y2"))
            direct = _same_point(x1, y1, ax, ay, tol) and _same_point(x2, y2, bx, by, tol)
            reverse = _same_point(x1, y1, bx, by, tol) and _same_point(x2, y2, ax, ay, tol)
            if direct or reverse:
                return True
        return False

    def _create_segment_from_points(
        self, p1: tuple[float, float], p2: tuple[float, float]
    ) -> None:
        if self._svg_root is None:
            return
        x1, y1 = p1
        x2, y2 = p2
        if math.hypot(x2 - x1, y2 - y1) <= 1e-6:
            messagebox.showerror("Segmento", "Puntos coinciden.")
            return
        if self._segment_exists_between_points(p1, p2):
            msg = "Ya existe un segmento entre esos puntos."
            if hasattr(self, "_angle_create_status"):
                self._angle_create_status.config(text=msg)
            self._set_transform_status(msg)
            return
        ns = ""
        tag = self._svg_root.tag
        if tag.startswith("{") and "}" in tag:
            ns = tag.split("}", 1)[0][1:]
        line_el = ET.Element(f"{{{ns}}}line") if ns else ET.Element("line")
        _set_attr(line_el, "x1", _format_num(x1))
        _set_attr(line_el, "y1", _format_num(y1))
        _set_attr(line_el, "x2", _format_num(x2))
        _set_attr(line_el, "y2", _format_num(y2))
        class_attr = self._default_line_class()
        if class_attr:
            line_el.set("class", class_attr)
        stroke_w = self._global_stroke_var.get().strip() or "2"
        _set_attr(line_el, "stroke", "#000000")
        _set_attr(line_el, "stroke-width", stroke_w)
        _set_attr(line_el, "fill", "none")
        self._push_history()
        self._svg_root.append(line_el)
        self._render_svg()
        self._set_transform_status("Segmento creado.")
        for record in self._records:
            if record.el is line_el:
                self._select_record(record)
                break

    def _intersection_split_parent_target(self, el: ET.Element | None) -> ET.Element | None:
        if el is None:
            return None
        if (el.get("data-kind") or "").strip() != "subsegment":
            return el
        parent = self._subsegment_parent_element(el)
        if parent is None:
            return el
        return parent

    def _split_intersection_target_on_points(self, el: ET.Element | None, *, zoom: float) -> None:
        if self._svg_root is None:
            return
        target = self._intersection_split_parent_target(el)
        if target is None:
            return
        tag = _strip_ns(target.tag)
        z = max(zoom, 1e-6)
        if tag == "line":
            points = self._points_on_line(target, tol_px=5.0, zoom=z)
            if len(points) >= 3:
                self._split_line_on_points(target, points)
            return
        if not self._is_curve_subsegment_parent(target):
            return
        points, closed = self._curve_subsegment_points(target)
        if len(points) < (3 if closed else 2):
            return
        total_len = self._polyline_total_length(points, closed=closed)
        if total_len <= 1e-9:
            return
        tol = 5.0 / z
        split_points = self._curve_split_points(points, closed=closed, tol=tol, total_len=total_len)
        if closed:
            if len(split_points) < 2:
                return
        else:
            if len(split_points) < 3:
                return
        self._split_curve_on_points(
            target,
            points,
            closed=closed,
            split_points=split_points,
            total_len=total_len,
        )

    def _create_intersection_point(
        self,
        x: float,
        y: float,
        el1: ET.Element,
        el2: ET.Element,
        *,
        zoom: float = 1.0,
    ) -> None:
        if self._svg_root is None:
            return
        ns = ""
        tag = self._svg_root.tag
        if tag.startswith("{") and "}" in tag:
            ns = tag.split("}", 1)[0][1:]
        circle = ET.Element(f"{{{ns}}}circle") if ns else ET.Element("circle")
        circle.set("cx", _format_num(x))
        circle.set("cy", _format_num(y))
        r = _parse_float(self._global_point_radius_var.get().strip(), 6.0)
        if r <= 0:
            r = 6.0
        circle.set("r", _format_num(r))
        _set_attr(circle, "fill", "#000000")
        _set_attr(circle, "stroke", "none")
        circle.set("data-kind", "point")
        circle.set("data-point-kind", "intersection")
        if _strip_ns(el1.tag) == "line" and _strip_ns(el2.tag) == "line":
            id1 = el1.get("id")
            id2 = el2.get("id")
            if id1 and id2:
                circle.set("data-constraint-intersection-of", f"{id1},{id2}")
                circle.set("data-constraint-intersection-mode", "segment")
        self._push_history()
        self._svg_root.append(circle)
        self._split_intersection_target_on_points(el1, zoom=zoom)
        if el2 is not el1:
            self._split_intersection_target_on_points(el2, zoom=zoom)
        self._render_svg()
        for record in self._records:
            if record.el is circle:
                self._select_record(record)
                break

    def _apply_constraints(self, driver_el: ET.Element | None = None) -> None:
        if self._svg_root is None:
            return
        line_by_id: dict[str, ET.Element] = {}
        line_ref_by_id: dict[str, ET.Element] = {}
        for el in self._svg_root.iter():
            if _strip_ns(el.tag) != "line":
                continue
            kind = (el.get("data-kind") or "").strip()
            el_id = el.get("id")
            if el_id:
                line_ref_by_id[el_id] = el
            if kind in ("subsegment", "circle-radius", _CURVE_RADIUS_DATA_KIND):
                continue
            if el_id:
                line_by_id[el_id] = el

        point_by_id: dict[str, ET.Element] = {}
        for el in self._svg_root.iter():
            if _strip_ns(el.tag) != "circle":
                continue
            el_id = (el.get("id") or "").strip()
            if el_id:
                point_by_id[el_id] = el

        changed_lines: set[ET.Element] = set()
        moved_points: list[tuple[float, float, float, float]] = []

        def line_dir(el: ET.Element) -> tuple[float, float] | None:
            try:
                x1 = _parse_float(_get_attr(el, "x1"))
                y1 = _parse_float(_get_attr(el, "y1"))
                x2 = _parse_float(_get_attr(el, "x2"))
                y2 = _parse_float(_get_attr(el, "y2"))
            except Exception:
                return None
            dx = x2 - x1
            dy = y2 - y1
            ln = math.hypot(dx, dy)
            if ln <= 1e-9:
                return None
            return (dx / ln, dy / ln)

        def line_len(el: ET.Element) -> float:
            x1 = _parse_float(_get_attr(el, "x1"))
            y1 = _parse_float(_get_attr(el, "y1"))
            x2 = _parse_float(_get_attr(el, "x2"))
            y2 = _parse_float(_get_attr(el, "y2"))
            return math.hypot(x2 - x1, y2 - y1)

        def line_anchor(el: ET.Element, anchor: str) -> tuple[float, float]:
            x1 = _parse_float(_get_attr(el, "x1"))
            y1 = _parse_float(_get_attr(el, "y1"))
            x2 = _parse_float(_get_attr(el, "x2"))
            y2 = _parse_float(_get_attr(el, "y2"))
            if anchor == "end":
                return (x2, y2)
            if anchor == "mid":
                return ((x1 + x2) * 0.5, (y1 + y2) * 0.5)
            return (x1, y1)

        def apply_line_from_anchor(
            el: ET.Element, anchor: str, ux: float, uy: float, length: float
        ) -> bool:
            if length <= 1e-9:
                return False
            ax, ay = line_anchor(el, anchor)
            if anchor == "end":
                x2, y2 = ax, ay
                x1, y1 = x2 - ux * length, y2 - uy * length
            elif anchor == "mid":
                half = 0.5 * length
                x1, y1 = ax - ux * half, ay - uy * half
                x2, y2 = ax + ux * half, ay + uy * half
            else:
                x1, y1 = ax, ay
                x2, y2 = x1 + ux * length, y1 + uy * length
            _set_attr(el, "x1", _format_num(x1))
            _set_attr(el, "y1", _format_num(y1))
            _set_attr(el, "x2", _format_num(x2))
            _set_attr(el, "y2", _format_num(y2))
            return True

        def resolve_ref_id(raw: str | None) -> str | None:
            if not raw:
                return None
            parts = [p.strip() for p in raw.split(",") if p.strip()]
            return parts[0] if parts else None

        def resolve_projection_ref(raw: str | None) -> tuple[float, float] | None:
            if not raw or ":" not in raw:
                return None
            kind, ref_id = raw.split(":", 1)
            kind = kind.strip().lower()
            ref_id = ref_id.strip()
            if kind == "point":
                point = point_by_id.get(ref_id)
                if point is None:
                    return None
                return (_parse_float(_get_attr(point, "cx")), _parse_float(_get_attr(point, "cy")))
            line = line_ref_by_id.get(ref_id)
            if line is None:
                return None
            pts = self._line_endpoints(line)
            if pts is None:
                return None
            x1, y1, x2, y2 = pts
            if kind == "line-end":
                return (x2, y2)
            if kind == "line-start":
                return (x1, y1)
            return None

        def rotate_dir(dx: float, dy: float, deg: float, side: str) -> tuple[float, float]:
            rad = math.radians(deg)
            if side == "cw":
                rad = -rad
            cos_a = math.cos(rad)
            sin_a = math.sin(rad)
            return (dx * cos_a - dy * sin_a, dx * sin_a + dy * cos_a)

        for _ in range(2):
            changed_any = False
            for el in self._svg_root.iter():
                if _strip_ns(el.tag) != "line":
                    continue
                kind = (el.get("data-kind") or "").strip()
                if kind in ("subsegment", "circle-radius", _CURVE_RADIUS_DATA_KIND):
                    continue
                if driver_el is not None and el is driver_el:
                    continue
                if (
                    el.get("data-constraint-eq-to") is None
                    and el.get("data-constraint-parallel-to") is None
                    and el.get("data-constraint-perp-to") is None
                    and el.get("data-constraint-angle-to") is None
                ):
                    continue
                anchor = (el.get("data-constraint-anchor") or "start").strip().lower()
                if anchor not in ("start", "end", "mid"):
                    anchor = "start"
                cur_dir = line_dir(el)
                if cur_dir is None:
                    continue
                ux, uy = cur_dir
                length = line_len(el)

                eq_id = resolve_ref_id(el.get("data-constraint-eq-to"))
                if eq_id and eq_id in line_by_id:
                    length = line_len(line_by_id[eq_id])

                par_id = resolve_ref_id(el.get("data-constraint-parallel-to"))
                perp_id = resolve_ref_id(el.get("data-constraint-perp-to"))
                ang_id = resolve_ref_id(el.get("data-constraint-angle-to"))

                ref = None
                mode = None
                if par_id and par_id in line_by_id:
                    ref = line_by_id[par_id]
                    mode = "parallel"
                elif perp_id and perp_id in line_by_id:
                    ref = line_by_id[perp_id]
                    mode = "perp"
                elif ang_id and ang_id in line_by_id:
                    ref = line_by_id[ang_id]
                    mode = "angle"

                if ref is not None:
                    ref_dir = line_dir(ref)
                    if ref_dir is None:
                        continue
                    rx, ry = ref_dir
                    if mode == "parallel":
                        ux, uy = rx, ry
                        if ux * cur_dir[0] + uy * cur_dir[1] < 0:
                            ux, uy = -ux, -uy
                    elif mode == "perp":
                        ux, uy = -ry, rx
                        if ux * cur_dir[0] + uy * cur_dir[1] < 0:
                            ux, uy = -ux, -uy
                    elif mode == "angle":
                        raw_deg = el.get("data-constraint-angle-deg") or "0"
                        try:
                            deg = float(raw_deg)
                        except Exception:
                            deg = 0.0
                        side = (el.get("data-constraint-angle-side") or "ccw").strip().lower()
                        ux, uy = rotate_dir(rx, ry, deg, side)

                if apply_line_from_anchor(el, anchor, ux, uy, length):
                    changed_any = True
                    changed_lines.add(el)
            if not changed_any:
                break

        for el in self._svg_root.iter():
            if _strip_ns(el.tag) != "circle":
                continue
            if (
                el.get("data-constraint-on") is None
                and el.get("data-constraint-intersection-of") is None
                and el.get(_PROJECTION_SOURCE_ATTR) is None
                and el.get(_PROJECTION_TARGET_ATTR) is None
            ):
                continue
            old_x = _parse_float(_get_attr(el, "cx"))
            old_y = _parse_float(_get_attr(el, "cy"))
            moved = False
            projection_source = el.get(_PROJECTION_SOURCE_ATTR)
            projection_target_id = resolve_ref_id(el.get(_PROJECTION_TARGET_ATTR))
            if projection_source and projection_target_id and projection_target_id in line_ref_by_id:
                source_xy = resolve_projection_ref(projection_source)
                if source_xy is not None:
                    foot = self._project_point_to_line_coords(source_xy[0], source_xy[1], line_ref_by_id[projection_target_id])
                    if foot is not None:
                        _set_attr(el, "cx", _format_num(foot[0]))
                        _set_attr(el, "cy", _format_num(foot[1]))
                        moved = True
            ref_id = resolve_ref_id(el.get("data-constraint-on"))
            if ref_id and ref_id in line_by_id:
                ref = line_by_id[ref_id]
                x1 = _parse_float(_get_attr(ref, "x1"))
                y1 = _parse_float(_get_attr(ref, "y1"))
                x2 = _parse_float(_get_attr(ref, "x2"))
                y2 = _parse_float(_get_attr(ref, "y2"))
                dx = x2 - x1
                dy = y2 - y1
                denom = dx * dx + dy * dy
                if denom > 1e-9:
                    raw_t = el.get("data-constraint-t")
                    if raw_t is None:
                        t = ((old_x - x1) * dx + (old_y - y1) * dy) / denom
                        el.set("data-constraint-t", _format_num(t))
                    else:
                        try:
                            t = float(raw_t)
                        except Exception:
                            t = 0.0
                    nx = x1 + dx * t
                    ny = y1 + dy * t
                    _set_attr(el, "cx", _format_num(nx))
                    _set_attr(el, "cy", _format_num(ny))
                    moved = True
            inter_raw = el.get("data-constraint-intersection-of")
            if inter_raw:
                parts = [p.strip() for p in inter_raw.split(",") if p.strip()]
                if len(parts) >= 2:
                    id1, id2 = parts[0], parts[1]
                    if id1 in line_by_id and id2 in line_by_id:
                        l1 = line_by_id[id1]
                        l2 = line_by_id[id2]
                        x1 = _parse_float(_get_attr(l1, "x1"))
                        y1 = _parse_float(_get_attr(l1, "y1"))
                        x2 = _parse_float(_get_attr(l1, "x2"))
                        y2 = _parse_float(_get_attr(l1, "y2"))
                        x3 = _parse_float(_get_attr(l2, "x1"))
                        y3 = _parse_float(_get_attr(l2, "y1"))
                        x4 = _parse_float(_get_attr(l2, "x2"))
                        y4 = _parse_float(_get_attr(l2, "y2"))
                        denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
                        if abs(denom) > 1e-9:
                            px = (
                                (x1 * y2 - y1 * x2) * (x3 - x4)
                                - (x1 - x2) * (x3 * y4 - y3 * x4)
                            ) / denom
                            py = (
                                (x1 * y2 - y1 * x2) * (y3 - y4)
                                - (y1 - y2) * (x3 * y4 - y3 * x4)
                            ) / denom
                            mode = (el.get("data-constraint-intersection-mode") or "line").strip().lower()
                            if mode == "segment":
                                def in_seg(xa: float, ya: float, xb: float, yb: float, px: float, py: float) -> bool:
                                    minx = min(xa, xb) - 1e-6
                                    maxx = max(xa, xb) + 1e-6
                                    miny = min(ya, yb) - 1e-6
                                    maxy = max(ya, yb) + 1e-6
                                    return minx <= px <= maxx and miny <= py <= maxy
                                if not (in_seg(x1, y1, x2, y2, px, py) and in_seg(x3, y3, x4, y4, px, py)):
                                    continue
                            _set_attr(el, "cx", _format_num(px))
                            _set_attr(el, "cy", _format_num(py))
                            moved = True
            if moved:
                moved_points.append((old_x, old_y, _parse_float(_get_attr(el, "cx")), _parse_float(_get_attr(el, "cy"))))

        for el in self._svg_root.iter():
            if _strip_ns(el.tag) != "line":
                continue
            start_ref = el.get(_LINE_START_REF_ATTR)
            end_ref = el.get(_LINE_END_REF_ATTR)
            if not start_ref and not end_ref:
                continue
            old_pts = self._line_endpoints(el)
            if old_pts is None:
                continue
            x1, y1, x2, y2 = old_pts
            start_xy = resolve_projection_ref(start_ref) if start_ref else None
            end_xy = resolve_projection_ref(end_ref) if end_ref else None
            if start_xy is not None:
                x1, y1 = start_xy
            if end_xy is not None:
                x2, y2 = end_xy
            if _same_point(old_pts[0], old_pts[1], x1, y1, 1e-9) and _same_point(old_pts[2], old_pts[3], x2, y2, 1e-9):
                continue
            _set_attr(el, "x1", _format_num(x1))
            _set_attr(el, "y1", _format_num(y1))
            _set_attr(el, "x2", _format_num(x2))
            _set_attr(el, "y2", _format_num(y2))
            changed_lines.add(el)

        if changed_lines:
            for line_el in changed_lines:
                self._sync_subsegments_from_parent(line_el)
                self._rebuild_segment_marks_from_line(line_el)
                self._sync_segment_endpoints_from_line(line_el)
                self._sync_segment_mid_labels_from_line(line_el)
                self._rebuild_segment_dimension_from_line(line_el)

        for ox, oy, nx, ny in moved_points:
            self._update_labels_for_anchor(ox, oy, nx, ny)

    def _rebuild_segment_marks_from_line(self, line_el: ET.Element) -> None:
        if self._svg_root is None:
            return
        style = (line_el.get("data-mark-style") or "").strip().lower()
        raw = line_el.get("data-mark-count")
        if not style or style in ("none", "ninguno", "nonce"):
            self._remove_segment_marks(line_el)
            return
        try:
            count = int(raw) if raw is not None else 0
        except Exception:
            count = 0
        if count <= 0:
            self._remove_segment_marks(line_el)
            return
        self._remove_segment_marks(line_el)
        self._create_segment_marks(line_el, count)

    def _sync_segment_endpoints_from_line(self, line_el: ET.Element) -> None:
        if self._svg_root is None:
            return
        key = self._segment_endpoint_key(line_el, create=True)
        if not key:
            return
        self._remove_segment_endpoints(line_el)
        try:
            x1 = _parse_float(_get_attr(line_el, "x1"))
            y1 = _parse_float(_get_attr(line_el, "y1"))
            x2 = _parse_float(_get_attr(line_el, "x2"))
            y2 = _parse_float(_get_attr(line_el, "y2"))
        except Exception:
            return
        font_size = _parse_float(self._global_font_size_var.get().strip(), 15.0)
        global_off = _parse_float(self._global_label_offset_var.get().strip(), 10.0)
        for role, ax, ay, text_key, dir_key, off_key in (
            ("start", x1, y1, "data-endpoint-label-a", "data-endpoint-dir-a", "data-endpoint-offset-a"),
            ("end", x2, y2, "data-endpoint-label-b", "data-endpoint-dir-b", "data-endpoint-offset-b"),
        ):
            text = (line_el.get(text_key) or "").strip()
            dir_s = (line_el.get(dir_key) or "").strip().upper()
            if not text or not _is_valid_dir(dir_s):
                continue
            off_raw = line_el.get(off_key)
            try:
                offset = float(off_raw) if off_raw is not None else global_off
            except Exception:
                offset = global_off
            lx, ly = _label_position_from_anchor(ax, ay, text, dir_s, offset, font_size, True)
            lbl = self._create_segment_endpoint_label(text, lx, ly, dir_s, offset, font_size, key, role)
            if lbl is not None:
                if role == "start":
                    mode_raw = (line_el.get("data-endpoint-bg-mode-a") or "").strip().lower()
                    legacy = (line_el.get("data-endpoint-bg-a") or "").strip()
                else:
                    mode_raw = (line_el.get("data-endpoint-bg-mode-b") or "").strip().lower()
                    legacy = (line_el.get("data-endpoint-bg-b") or "").strip()
                mode = mode_raw if mode_raw in (_LABEL_BG_MODE_NONE, _LABEL_BG_MODE_WHITE, _LABEL_BG_MODE_CUT) else (_LABEL_BG_MODE_WHITE if legacy == "1" else _LABEL_BG_MODE_NONE)
                self._set_label_bg_mode(lbl, mode)
                self._svg_root.append(lbl)

    def _sync_segment_mid_labels_from_line(self, line_el: ET.Element) -> None:
        if self._svg_root is None:
            return
        key = self._segment_mid_key(line_el, create=True)
        if not key:
            return
        self._remove_segment_mid_labels(line_el)
        text = (line_el.get("data-mid-label") or "").strip()
        dir_s = (line_el.get("data-mid-dir") or "").strip().upper()
        if not text or not _is_valid_dir(dir_s):
            return
        try:
            x1 = _parse_float(_get_attr(line_el, "x1"))
            y1 = _parse_float(_get_attr(line_el, "y1"))
            x2 = _parse_float(_get_attr(line_el, "x2"))
            y2 = _parse_float(_get_attr(line_el, "y2"))
        except Exception:
            return
        mx = (x1 + x2) * 0.5
        my = (y1 + y2) * 0.5
        font_size = _parse_float(self._global_font_size_var.get().strip(), 15.0)
        global_off = _parse_float(self._global_label_offset_var.get().strip(), 10.0)
        off_raw = line_el.get("data-mid-offset")
        try:
            offset = float(off_raw) if off_raw is not None else global_off
        except Exception:
            offset = global_off
        lx, ly = _label_position_from_anchor(mx, my, text, dir_s, offset, font_size, True)
        lbl = self._create_segment_mid_label(text, lx, ly, dir_s, offset, font_size, key)
        if lbl is not None:
            mode_raw = (line_el.get("data-mid-bg-mode") or "").strip().lower()
            legacy = (line_el.get("data-mid-bg") or "").strip()
            mode = mode_raw if mode_raw in (_LABEL_BG_MODE_NONE, _LABEL_BG_MODE_WHITE, _LABEL_BG_MODE_CUT) else (_LABEL_BG_MODE_WHITE if legacy == "1" else _LABEL_BG_MODE_NONE)
            self._set_label_bg_mode(lbl, mode)
            self._svg_root.append(lbl)

    def _update_labels_for_anchor(self, ox: float, oy: float, nx: float, ny: float) -> None:
        if self._svg_root is None:
            return
        tol = 1.5
        for el in self._svg_root.iter():
            tag = _strip_ns(el.tag)
            if tag not in ("text", "path"):
                continue
            if el.get("data-anchor-frac") is not None:
                continue
            if (el.get("data-kind") or "").strip() in ("seg-endpoint-label", "seg-mid-label"):
                continue
            dax = el.get("data-anchor-x")
            day = el.get("data-anchor-y")
            if dax is None or day is None:
                continue
            if abs(_parse_float(dax) - ox) > tol or abs(_parse_float(day) - oy) > tol:
                continue
            text = (el.text or "").strip() if tag == "text" else (el.get("data-text") or "").strip()
            if not text:
                continue
            dir_s = (el.get("data-dir") or "").strip().upper()
            if not _is_valid_dir(dir_s):
                continue
            offset = _parse_float(el.get("data-offset"), _parse_float(self._global_label_offset_var.get().strip(), 10.0))
            font_size = self._label_font_size(el, 12.0)
            el.set("data-anchor-x", _format_num(nx))
            el.set("data-anchor-y", _format_num(ny))
            lx, ly = _label_position_from_anchor(nx, ny, text, dir_s, offset, font_size, True)
            self._set_label_position(el, lx, ly)
            if tag == "path":
                self._update_latex_path(el, text, lx, ly, font_size, silent=True)

    def _handle_angle_create_click(self, event: tk.Event) -> None:
        if self.canvas is None:
            return

        def _set_angle_create_status(msg: str) -> None:
            if hasattr(self, "_angle_create_status"):
                self._angle_create_status.config(text=msg)
            self._set_transform_status(msg)

        x = float(self.canvas.canvasx(event.x))
        y = float(self.canvas.canvasy(event.y))
        try:
            zoom = float(self._view_scale.get())
        except Exception:
            zoom = 1.0
        if zoom <= 0:
            zoom = 1.0
        if self._angle_create_mode is None:
            cand = self._pick_angle_create_candidate(x, y, zoom)
            if cand is None:
                _set_angle_create_status("Selecciona punto o segmento")
                return
            kind, payload = cand
            if kind == "point":
                self._angle_create_mode = "points"
                self._angle_create_points.append(payload)
                _set_angle_create_status("Selecciona vertice (punto 2)")
            else:
                self._angle_create_mode = "segments"
                self._angle_create_segments.append(payload)
                _set_angle_create_status("Selecciona segmento 2")
            return

        if self._angle_create_mode == "points":
            hit = self._pick_point_element(x, y, zoom)
            if hit is None:
                _set_angle_create_status("Selecciona un punto")
                return
            self._angle_create_points.append(hit)
            if len(self._angle_create_points) == 1:
                _set_angle_create_status("Selecciona vertice (punto 2)")
                return
            if len(self._angle_create_points) == 2:
                _set_angle_create_status("Selecciona punto 3")
                return
            if len(self._angle_create_points) >= 3:
                p1, v, p2 = self._angle_create_points[:3]
                self._angle_create_points.clear()
                self._angle_create_mode = None
                self._create_angle_from_points(p1, v, p2)
                if self._angle_create_active:
                    _set_angle_create_status("Selecciona punto o segmento")
            return

        if self._angle_create_mode == "segments":
            seg = self._pick_segment_element(x, y, zoom)
            if seg is None:
                _set_angle_create_status("Selecciona un segmento")
                return
            if self._angle_create_segments and seg is self._angle_create_segments[0]:
                _set_angle_create_status("Selecciona otro segmento")
                return
            self._angle_create_segments.append(seg)
            if len(self._angle_create_segments) >= 2:
                s1, s2 = self._angle_create_segments[:2]
                self._angle_create_segments.clear()
                self._angle_create_mode = None
                self._create_angle_from_segments(s1, s2)
                if self._angle_create_active:
                    _set_angle_create_status("Selecciona punto o segmento")
            return

    def _pick_point_element(
        self, x: float, y: float, zoom: float
    ) -> tuple[float, float] | None:
        candidates = self._collect_hit_candidates(x, y, zoom)
        for _dist, d in candidates:
            if d.record is None:
                continue
            el = d.record.el
            if _strip_ns(el.tag) != "circle":
                continue
            if not self._is_point_circle(el):
                continue
            if el.get("data-angle-id") is not None or el.get("data-angle-kind") == "point":
                continue
            if (el.get("data-kind") or "").strip() == "seg-mark":
                continue
            cx = _parse_float(_get_attr(el, "cx"))
            cy = _parse_float(_get_attr(el, "cy"))
            if d.record is not None:
                self._select_record(d.record)
            return (cx, cy)
        return None

    def _pick_segment_element(self, x: float, y: float, zoom: float) -> ET.Element | None:
        candidates = self._collect_hit_candidates(x, y, zoom, kinds=("line",))
        for _dist, d in candidates:
            if d.record is None:
                continue
            el = d.record.el
            if _strip_ns(el.tag) != "line":
                continue
            if (el.get("data-kind") or "").strip() == "seg-mark":
                continue
            if d.record is not None:
                self._select_record(d.record)
            return el
        return None

    def _pick_intersection_candidate(self, x: float, y: float, zoom: float) -> _Record | None:
        candidates = self._collect_hit_candidates(x, y, zoom)
        filtered: list[tuple[float, _Drawable]] = []
        for dist, d in candidates:
            if d.record is None:
                continue
            if not self._is_intersection_candidate(d.record.el):
                continue
            filtered.append((dist, d))
        if not filtered:
            return None
        best_dist = filtered[0][0]
        tol = 1e-6
        near_best = [item for item in filtered if abs(item[0] - best_dist) <= tol]
        sub_hits = [item for item in near_best if self._is_subsegment_record(item[1].record)]
        if sub_hits:
            filtered = sub_hits
        return filtered[0][1].record

    def _pick_angle_create_candidate(
        self, x: float, y: float, zoom: float
    ) -> tuple[str, tuple[float, float] | ET.Element] | None:
        candidates = self._collect_hit_candidates(x, y, zoom)
        best_point = None
        best_seg = None
        for dist, d in candidates:
            if d.record is None:
                continue
            el = d.record.el
            tag = _strip_ns(el.tag)
            if tag == "circle":
                if not self._is_point_circle(el):
                    continue
                if el.get("data-angle-id") is not None or el.get("data-angle-kind") == "point":
                    continue
                if (el.get("data-kind") or "").strip() == "seg-mark":
                    continue
                if best_point is None:
                    best_point = (dist, el, d.record)
            elif tag == "line":
                if (el.get("data-kind") or "").strip() == "seg-mark":
                    continue
                if best_seg is None:
                    best_seg = (dist, el, d.record)
            if best_point is not None and best_seg is not None:
                break
        if best_point is None and best_seg is None:
            return None
        if best_point is not None and (
            best_seg is None or best_point[0] <= best_seg[0] + 1e-6
        ):
            _dist, el, rec = best_point
            if rec is not None:
                self._select_record(rec)
            cx = _parse_float(_get_attr(el, "cx"))
            cy = _parse_float(_get_attr(el, "cy"))
            return ("point", (cx, cy))
        if best_seg is not None:
            _dist, el, rec = best_seg
            if rec is not None:
                self._select_record(rec)
            return ("segment", el)
        return None

    def _create_angle_from_points(
        self, p1: tuple[float, float], v: tuple[float, float], p2: tuple[float, float]
    ) -> None:
        if self._svg_root is None:
            return
        x1, y1 = p1
        vx, vy = v
        x2, y2 = p2
        v1x = x1 - vx
        v1y = y1 - vy
        v2x = x2 - vx
        v2y = y2 - vy
        n1 = math.hypot(v1x, v1y)
        n2 = math.hypot(v2x, v2y)
        if n1 <= 1e-6 or n2 <= 1e-6:
            messagebox.showerror("Angulo", "Puntos degenerados.")
            return
        v1x /= n1
        v1y /= n1
        v2x /= n2
        v2y /= n2
        cross = v1x * v2y - v1y * v2x
        if abs(cross) <= 1e-6:
            messagebox.showerror("Angulo", "Puntos colineales.")
            return
        want_obtuse = False
        if hasattr(self, "_angle_create_obtuse_var"):
            want_obtuse = bool(self._angle_create_obtuse_var.get())
        v1x, v1y, v2x, v2y, sweep = self._adjust_angle_vectors_for_obtuse(
            v1x, v1y, v2x, v2y, want_obtuse
        )
        ra = _parse_float(self._angle_radius_var.get().strip(), 30.0)
        ns = ""
        tag = self._svg_root.tag
        if tag.startswith("{") and "}" in tag:
            ns = tag.split("}", 1)[0][1:]
        root = ET.Element(f"{{{ns}}}path") if ns else ET.Element("path")
        stroke_w = self._global_stroke_var.get().strip() or "2"
        _set_attr(root, "stroke", "#000000")
        _set_attr(root, "stroke-width", stroke_w)
        _set_attr(root, "fill", "none")
        angle_id = self._next_angle_id()
        root.set("data-angle-id", angle_id)
        root.set("data-angle-root", "1")
        root.set("data-angle-vx", _format_num(vx))
        root.set("data-angle-vy", _format_num(vy))
        root.set("data-angle-v1x", _format_num(v1x))
        root.set("data-angle-v1y", _format_num(v1y))
        root.set("data-angle-v2x", _format_num(v2x))
        root.set("data-angle-v2y", _format_num(v2y))
        root.set("data-angle-ra", _format_num(ra))
        root.set("data-angle-sweep", str(sweep))
        is_reflex = bool(self._angle_reflex_var.get()) if hasattr(self, "_angle_reflex_var") else False
        root.set("data-angle-replement", "1" if is_reflex else "0")
        root.set("data-angle-source", "segments")
        root.set("data-angle-vertical", "0")
        root.set("data-angle-source", "points")
        root.set("data-angle-vertical", "0")
        self._push_history()
        self._svg_root.append(root)
        new_root = self._rebuild_angle_group(root)
        self._render_svg()
        if new_root is None:
            return
        for record in self._records:
            if record.el is new_root:
                self._select_record(record)
                break

    def _create_angle_from_segments(self, seg1: ET.Element, seg2: ET.Element) -> None:
        if self._svg_root is None:
            return
        try:
            a1 = _parse_float(_get_attr(seg1, "x1"))
            b1 = _parse_float(_get_attr(seg1, "y1"))
            a2 = _parse_float(_get_attr(seg1, "x2"))
            b2 = _parse_float(_get_attr(seg1, "y2"))
            c1 = _parse_float(_get_attr(seg2, "x1"))
            d1 = _parse_float(_get_attr(seg2, "y1"))
            c2 = _parse_float(_get_attr(seg2, "x2"))
            d2 = _parse_float(_get_attr(seg2, "y2"))
        except Exception:
            messagebox.showerror("Angulo", "Segmentos invalidos.")
            return

        def dist(p: tuple[float, float], q: tuple[float, float]) -> float:
            return math.hypot(p[0] - q[0], p[1] - q[1])

        eps = 1e-6
        p1 = (a1, b1)
        p2 = (a2, b2)
        p3 = (c1, d1)
        p4 = (c2, d2)
        vertex = None
        for p in (p1, p2):
            for q in (p3, p4):
                if dist(p, q) <= eps:
                    vertex = p
                    break
            if vertex is not None:
                break

        if vertex is None:
            def cross(ax: float, ay: float, bx: float, by: float) -> float:
                return ax * by - ay * bx

            r = (p2[0] - p1[0], p2[1] - p1[1])
            s = (p4[0] - p3[0], p4[1] - p3[1])
            denom = cross(r[0], r[1], s[0], s[1])
            if abs(denom) <= eps:
                messagebox.showerror("Angulo", "Segmentos paralelos.")
                return
            t = cross(p3[0] - p1[0], p3[1] - p1[1], s[0], s[1]) / denom
            u = cross(p3[0] - p1[0], p3[1] - p1[1], r[0], r[1]) / denom
            if t < -eps or t > 1.0 + eps or u < -eps or u > 1.0 + eps:
                messagebox.showerror("Angulo", "Segmentos no se intersectan.")
                return
            vertex = (p1[0] + t * r[0], p1[1] + t * r[1])

        vx, vy = vertex

        def unit_dirs(pa: tuple[float, float], pb: tuple[float, float]) -> list[tuple[float, float]]:
            dirs: list[tuple[float, float]] = []
            for px, py in (pa, pb):
                dx = px - vx
                dy = py - vy
                n = math.hypot(dx, dy)
                if n > eps:
                    dirs.append((dx / n, dy / n))
            return dirs

        dirs1 = unit_dirs(p1, p2)
        dirs2 = unit_dirs(p3, p4)
        if not dirs1 or not dirs2:
            messagebox.showerror("Angulo", "Segmentos degenerados.")
            return

        best = None
        for u1x, u1y in dirs1:
            for u2x, u2y in dirs2:
                dot = max(-1.0, min(1.0, u1x * u2x + u1y * u2y))
                ang = math.acos(dot)
                if best is None or ang < best[0]:
                    best = (ang, u1x, u1y, u2x, u2y)
        if best is None:
            messagebox.showerror("Angulo", "No se pudo construir el angulo.")
            return
        ang, v1x, v1y, v2x, v2y = best
        if ang <= 1e-6:
            messagebox.showerror("Angulo", "Segmentos colineales.")
            return
        cross = v1x * v2y - v1y * v2x
        if abs(cross) <= 1e-6:
            messagebox.showerror("Angulo", "Segmentos colineales.")
            return
        want_obtuse = False
        if hasattr(self, "_angle_create_obtuse_var"):
            want_obtuse = bool(self._angle_create_obtuse_var.get())
        v1x, v1y, v2x, v2y, sweep = self._adjust_angle_vectors_for_obtuse(
            v1x, v1y, v2x, v2y, want_obtuse
        )
        ra = _parse_float(self._angle_radius_var.get().strip(), 30.0)
        ns = ""
        tag = self._svg_root.tag
        if tag.startswith("{") and "}" in tag:
            ns = tag.split("}", 1)[0][1:]
        root = ET.Element(f"{{{ns}}}path") if ns else ET.Element("path")
        stroke_w = self._global_stroke_var.get().strip() or "2"
        _set_attr(root, "stroke", "#000000")
        _set_attr(root, "stroke-width", stroke_w)
        _set_attr(root, "fill", "none")
        angle_id = self._next_angle_id()
        root.set("data-angle-id", angle_id)
        root.set("data-angle-root", "1")
        root.set("data-angle-vx", _format_num(vx))
        root.set("data-angle-vy", _format_num(vy))
        root.set("data-angle-v1x", _format_num(v1x))
        root.set("data-angle-v1y", _format_num(v1y))
        root.set("data-angle-v2x", _format_num(v2x))
        root.set("data-angle-v2y", _format_num(v2y))
        root.set("data-angle-ra", _format_num(ra))
        root.set("data-angle-sweep", str(sweep))
        is_reflex = bool(self._angle_reflex_var.get()) if hasattr(self, "_angle_reflex_var") else False
        root.set("data-angle-replement", "1" if is_reflex else "0")
        self._push_history()
        self._svg_root.append(root)
        new_root = self._rebuild_angle_group(root)
        self._render_svg()
        if new_root is None:
            return
        for record in self._records:
            if record.el is new_root:
                self._select_record(record)
                break
        if self._selected is not None:
            self._highlight_code_for_element(self._selected.el)

    def _resolve_viewbox(self, root: ET.Element) -> tuple[float, float, float, float]:
        view_box = root.get("viewBox")
        if view_box:
            parts = _parse_points(view_box)
            if len(parts) >= 4:
                return (parts[0], parts[1], parts[2], parts[3])
        width = _parse_float(root.get("width"), 0.0)
        height = _parse_float(root.get("height"), 0.0)
        if width > 0 and height > 0:
            return (0.0, 0.0, width, height)
        min_x, min_y, max_x, max_y = self._collect_svg_bounds(root)
        if min_x == float("inf"):
            return (0.0, 0.0, 800.0, 600.0)
        return (min_x, min_y, max_x - min_x, max_y - min_y)

    def _render_element(self, el: ET.Element, tag: str) -> _Record | None:
        display = (_get_attr(el, "display") or "").strip().lower()
        visibility = (_get_attr(el, "visibility") or "").strip().lower()
        if display == "none" or visibility == "hidden":
            return None
        sx = self._shift_x
        sy = self._shift_y
        is_mark = el.get("data-kind") in (
            "seg-mark",
            "seg-endpoint",
            "seg-endpoint-label",
            "seg-mid-label",
            _SEG_DIM_TICK_DATA_KIND,
            _SEG_DIM_EXT_DATA_KIND,
            _SEG_DIM_LABEL_DATA_KIND,
            "label-bg",
        )

        def style_for(defaults: dict[str, str]) -> dict[str, str]:
            style: dict[str, str] = {}
            class_attr = (el.get("class") or "").strip()
            if class_attr:
                for cls in class_attr.split():
                    class_style = self._class_styles.get(cls)
                    if class_style:
                        style.update(class_style)
            for key in (
                "stroke",
                "stroke-width",
                "stroke-dasharray",
                "fill",
                "fill-opacity",
                "font-size",
                "font-family",
                "font-weight",
            ):
                val = self._effective_attr(el, key)
                if val is not None:
                    style[key] = val
            for key, val in defaults.items():
                if key not in style:
                    style[key] = val
            return style

        if tag == "line":
            if el.get("data-kind") == "seg-mark":
                x1 = _parse_float(_get_attr(el, "x1")) + sx
                y1 = _parse_float(_get_attr(el, "y1")) + sy
                x2 = _parse_float(_get_attr(el, "x2")) + sx
                y2 = _parse_float(_get_attr(el, "y2")) + sy
                style = style_for({"stroke": "#000000", "stroke-width": "1", "fill": "none"})
                self._drawables.append(_Drawable(kind="line", coords=[x1, y1, x2, y2], style=style, layer=1))
                return None
            x1 = _parse_float(_get_attr(el, "x1")) + sx
            y1 = _parse_float(_get_attr(el, "y1")) + sy
            x2 = _parse_float(_get_attr(el, "x2")) + sx
            y2 = _parse_float(_get_attr(el, "y2")) + sy
            style = style_for({"stroke": "#000000", "stroke-width": "1", "fill": "none"})
            record = None if is_mark else _Record(el=el, tag=tag, item_ids=[], kind="shape", orig_fill=style.get("stroke"))
            self._drawables.append(_Drawable(kind="line", coords=[x1, y1, x2, y2], style=style, layer=1, record=record))
            return None if is_mark else record

        if tag in ("polyline", "polygon"):
            pts = _parse_points(_get_attr(el, "points"))
            if not pts:
                return None
            coords: list[float] = []
            for i, val in enumerate(pts):
                coords.append(val + (sx if i % 2 == 0 else sy))
            defaults = {"stroke": "#000000", "stroke-width": "1", "fill": "none"}
            style = style_for(defaults if tag == "polyline" else {**defaults})
            kind = "polyline" if tag == "polyline" else "polygon"
            record = None if is_mark else _Record(el=el, tag=tag, item_ids=[], kind="shape", orig_fill=style.get("stroke"))
            self._drawables.append(_Drawable(kind=kind, coords=coords, style=style, layer=1, record=record))
            return None if is_mark else record

        if tag == "circle":
            cx = _parse_float(_get_attr(el, "cx")) + sx
            cy = _parse_float(_get_attr(el, "cy")) + sy
            r = _parse_float(_get_attr(el, "r"))
            is_point_circle = self._is_point_circle(el)
            if is_point_circle:
                style = style_for({"stroke": "#000000", "stroke-width": "1", "fill": "#000000"})
            else:
                style = style_for({"stroke": "", "stroke-width": "1", "fill": ""})
            record = None if is_mark else _Record(el=el, tag=tag, item_ids=[], kind="shape", orig_outline=style.get("stroke"), orig_fill=style.get("fill"))
            self._drawables.append(
                _Drawable(
                    kind="circle",
                    coords=[cx - r, cy - r, cx + r, cy + r],
                    style=style,
                    layer=2 if is_point_circle else 1,
                    record=record,
                )
            )
            return None if is_mark else record

        if tag == "ellipse":
            cx = _parse_float(_get_attr(el, "cx")) + sx
            cy = _parse_float(_get_attr(el, "cy")) + sy
            rx = _parse_float(_get_attr(el, "rx"))
            ry = _parse_float(_get_attr(el, "ry"))
            style = style_for({"stroke": "", "stroke-width": "1", "fill": ""})
            record = None if is_mark else _Record(el=el, tag=tag, item_ids=[], kind="shape", orig_outline=style.get("stroke"), orig_fill=style.get("fill"))
            self._drawables.append(
                _Drawable(kind="ellipse", coords=[cx - rx, cy - ry, cx + rx, cy + ry], style=style, layer=1, record=record)
            )
            return None if is_mark else record

        if tag == "rect":
            x = _parse_float(_get_attr(el, "x")) + sx
            y = _parse_float(_get_attr(el, "y")) + sy
            w = _parse_float(_get_attr(el, "width"))
            h = _parse_float(_get_attr(el, "height"))
            style = style_for({"stroke": "", "stroke-width": "1", "fill": ""})
            is_bg = (el.get("data-kind") or "").strip() == "background"
            is_label_bg = (el.get("data-kind") or "").strip() == "label-bg"
            record = None if (is_mark or is_bg or is_label_bg) else _Record(
                el=el, tag=tag, item_ids=[], kind="shape", orig_outline=style.get("stroke"), orig_fill=style.get("fill")
            )
            coords = [x, y, x + w, y, x + w, y + h, x, y + h]
            layer = 0 if is_bg else (2 if is_label_bg else 1)
            self._drawables.append(_Drawable(kind="polygon", coords=coords, style=style, layer=layer, record=record))
            return None if (is_mark or is_bg or is_label_bg) else record

        if tag == "path":
            if el.get("data-text") is not None:
                text = (el.get("data-text") or "").strip()
                x = _parse_float(el.get("data-x")) + sx
                y = _parse_float(el.get("data-y")) + sy
                size = self._label_font_size(el, 12.0)
                style = style_for({"fill": "#000000"})
                # Las etiquetas convertidas a path usan data-font-size como fuente de verdad.
                # Si el grupo padre tiene font-size, no debe pisar el tamano global aplicado.
                style["font-size"] = _format_num(size)
                record = None if is_mark else _Record(el=el, tag=tag, item_ids=[], kind="label", orig_fill=style.get("fill"))
                self._drawables.append(
                    _Drawable(kind="latex", coords=[x, y], style=style, layer=3, text=text, record=record)
                )
                return None if is_mark else record
            d = _get_attr(el, "d") or ""
            subpaths = _parse_svg_path(d)
            if subpaths:
                style = style_for({"stroke": "#000000", "stroke-width": "1", "fill": "none"})
                fill = str(style.get("fill", "")).strip().lower()
                is_filled = fill not in ("", "none", "transparent")
                record = None if is_mark else _Record(el=el, tag=tag, item_ids=[], kind="shape", orig_fill=style.get("stroke"))
                for pts, closed in subpaths:
                    coords = []
                    for x, y in pts:
                        coords.extend([x + sx, y + sy])
                    kind = "polygon" if (is_filled and closed) else "polyline"
                    self._drawables.append(_Drawable(kind=kind, coords=coords, style=style, layer=1, record=record))
                return None if is_mark else record
            return None

        if tag == "text":
            x = _parse_float(_get_attr(el, "x")) + sx
            y = _parse_float(_get_attr(el, "y")) + sy
            size = _parse_float(_get_attr(el, "font-size"), 12.0)
            style = style_for({"fill": "#000000", "font-size": _format_num(size), "font-family": "Arial"})
            text = (el.text or "").strip()
            self._ensure_label_anchor(el, x - sx, y - sy)
            record = None if is_mark else _Record(el=el, tag=tag, item_ids=[], kind="label", orig_fill=style.get("fill"))
            self._drawables.append(_Drawable(kind="latex", coords=[x, y], style=style, layer=3, text=text, record=record))
            return None if is_mark else record

        return None

    def _collect_svg_bounds(self, root: ET.Element) -> tuple[float, float, float, float]:
        min_x = float("inf")
        min_y = float("inf")
        max_x = float("-inf")
        max_y = float("-inf")
        defs_ids: set[int] = set()
        for el in root.iter():
            if _strip_ns(el.tag) == "defs":
                for child in el.iter():
                    defs_ids.add(id(child))

        def include(x: float, y: float) -> None:
            nonlocal min_x, min_y, max_x, max_y
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)

        def should_skip(el: ET.Element, tag: str) -> bool:
            display = (_get_attr(el, "display") or "").strip().lower()
            visibility = (_get_attr(el, "visibility") or "").strip().lower()
            if display == "none" or visibility == "hidden":
                return True
            if self._is_shade_contour_helper(el):
                return True
            if tag in ("svg", "g", "defs", "title", "desc"):
                return True
            if tag == "text":
                return not bool((el.text or "").strip())
            if tag == "path" and el.get("data-text") is not None:
                return False
            stroke = (self._effective_attr(el, "stroke") or "").strip().lower()
            fill = (self._effective_attr(el, "fill") or "").strip().lower()
            if tag in ("line", "polyline"):
                return stroke in ("", "none", "transparent")
            if tag in ("polygon", "rect", "circle", "ellipse", "path"):
                stroke_hidden = stroke in ("", "none", "transparent")
                fill_hidden = fill in ("", "none", "transparent")
                return stroke_hidden and fill_hidden
            return False

        for el in root.iter():
            if id(el) in defs_ids:
                continue
            tag = _strip_ns(el.tag)
            if should_skip(el, tag):
                continue
            if tag == "line":
                include(_parse_float(_get_attr(el, "x1")), _parse_float(_get_attr(el, "y1")))
                include(_parse_float(_get_attr(el, "x2")), _parse_float(_get_attr(el, "y2")))
            elif tag in ("polyline", "polygon"):
                pts = _parse_points(_get_attr(el, "points"))
                for i in range(0, len(pts) - 1, 2):
                    include(pts[i], pts[i + 1])
            elif tag == "circle":
                cx = _parse_float(_get_attr(el, "cx"))
                cy = _parse_float(_get_attr(el, "cy"))
                r = _parse_float(_get_attr(el, "r"))
                include(cx - r, cy - r)
                include(cx + r, cy + r)
            elif tag == "ellipse":
                cx = _parse_float(_get_attr(el, "cx"))
                cy = _parse_float(_get_attr(el, "cy"))
                rx = _parse_float(_get_attr(el, "rx"))
                ry = _parse_float(_get_attr(el, "ry"))
                include(cx - rx, cy - ry)
                include(cx + rx, cy + ry)
            elif tag == "rect":
                if (el.get("data-kind") or "").strip() in ("background", "label-bg"):
                    continue
                x = _parse_float(_get_attr(el, "x"))
                y = _parse_float(_get_attr(el, "y"))
                w = _parse_float(_get_attr(el, "width"))
                h = _parse_float(_get_attr(el, "height"))
                include(x, y)
                include(x + w, y + h)
            elif tag == "path":
                if el.get("data-text") is not None:
                    bounds = self._label_bounds(el)
                    if bounds is not None:
                        include(bounds[0], bounds[1])
                        include(bounds[2], bounds[3])
                    else:
                        x = _parse_float(el.get("data-x"))
                        y = _parse_float(el.get("data-y"))
                        include(x, y)
                else:
                    d = _get_attr(el, "d") or ""
                    for pts, _closed in _parse_svg_path(d):
                        for x, y in pts:
                            include(x, y)
            elif tag == "text":
                bounds = self._label_bounds(el)
                if bounds is not None:
                    include(bounds[0], bounds[1])
                    include(bounds[2], bounds[3])
                else:
                    x = _parse_float(_get_attr(el, "x"))
                    y = _parse_float(_get_attr(el, "y"))
                    include(x, y)

        return (min_x, min_y, max_x, max_y)

    def _collect_svg_bounds_content(self, root: ET.Element) -> tuple[float, float, float, float]:
        bg = self._find_background_rect()
        min_x = float("inf")
        min_y = float("inf")
        max_x = float("-inf")
        max_y = float("-inf")
        defs_ids: set[int] = set()
        for el in root.iter():
            if _strip_ns(el.tag) == "defs":
                for child in el.iter():
                    defs_ids.add(id(child))

        def include(x: float, y: float) -> None:
            nonlocal min_x, min_y, max_x, max_y
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)

        def should_skip(el: ET.Element, tag: str) -> bool:
            display = (_get_attr(el, "display") or "").strip().lower()
            visibility = (_get_attr(el, "visibility") or "").strip().lower()
            if display == "none" or visibility == "hidden":
                return True
            if self._is_shade_contour_helper(el):
                return True
            if tag in ("svg", "g", "defs", "title", "desc"):
                return True
            if tag == "text":
                return not bool((el.text or "").strip())
            if tag == "path" and el.get("data-text") is not None:
                return False
            stroke = (self._effective_attr(el, "stroke") or "").strip().lower()
            fill = (self._effective_attr(el, "fill") or "").strip().lower()
            if tag in ("line", "polyline"):
                return stroke in ("", "none", "transparent")
            if tag in ("polygon", "rect", "circle", "ellipse", "path"):
                stroke_hidden = stroke in ("", "none", "transparent")
                fill_hidden = fill in ("", "none", "transparent")
                return stroke_hidden and fill_hidden
            return False

        for el in root.iter():
            if id(el) in defs_ids:
                continue
            if el is bg:
                continue
            tag = _strip_ns(el.tag)
            if should_skip(el, tag):
                continue
            if tag == "line":
                include(_parse_float(_get_attr(el, "x1")), _parse_float(_get_attr(el, "y1")))
                include(_parse_float(_get_attr(el, "x2")), _parse_float(_get_attr(el, "y2")))
            elif tag in ("polyline", "polygon"):
                pts = _parse_points(_get_attr(el, "points"))
                for i in range(0, len(pts) - 1, 2):
                    include(pts[i], pts[i + 1])
            elif tag == "circle":
                cx = _parse_float(_get_attr(el, "cx"))
                cy = _parse_float(_get_attr(el, "cy"))
                r = _parse_float(_get_attr(el, "r"))
                include(cx - r, cy - r)
                include(cx + r, cy + r)
            elif tag == "ellipse":
                cx = _parse_float(_get_attr(el, "cx"))
                cy = _parse_float(_get_attr(el, "cy"))
                rx = _parse_float(_get_attr(el, "rx"))
                ry = _parse_float(_get_attr(el, "ry"))
                include(cx - rx, cy - ry)
                include(cx + rx, cy + ry)
            elif tag == "rect":
                if self._is_background_rect_like(el):
                    continue
                x = _parse_float(_get_attr(el, "x"))
                y = _parse_float(_get_attr(el, "y"))
                w = _parse_float(_get_attr(el, "width"))
                h = _parse_float(_get_attr(el, "height"))
                include(x, y)
                include(x + w, y + h)
            elif tag == "path":
                if el.get("data-text") is not None:
                    bounds = self._label_bounds(el)
                    if bounds is not None:
                        include(bounds[0], bounds[1])
                        include(bounds[2], bounds[3])
                    else:
                        x = _parse_float(el.get("data-x"))
                        y = _parse_float(el.get("data-y"))
                        include(x, y)
                else:
                    d = _get_attr(el, "d") or ""
                    for pts, _closed in _parse_svg_path(d):
                        for x, y in pts:
                            include(x, y)
            elif tag == "text":
                bounds = self._label_bounds(el)
                if bounds is not None:
                    include(bounds[0], bounds[1])
                    include(bounds[2], bounds[3])
                else:
                    x = _parse_float(_get_attr(el, "x"))
                    y = _parse_float(_get_attr(el, "y"))
                    include(x, y)

        return (min_x, min_y, max_x, max_y)

    def _auto_expand_viewbox(self) -> None:
        if self._svg_root is None:
            return
        min_x, min_y, max_x, max_y = self._collect_svg_bounds_content(self._svg_root)
        if min_x == float("inf"):
            return
        margin = 0.0
        bmin_x = min_x - margin
        bmin_y = min_y - margin
        bmax_x = max_x + margin
        bmax_y = max_y + margin
        if bmax_x - bmin_x < 1.0:
            bmax_x = bmin_x + 1.0
        if bmax_y - bmin_y < 1.0:
            bmax_y = bmin_y + 1.0

        new_min_x = bmin_x
        new_min_y = bmin_y
        new_max_x = bmax_x
        new_max_y = bmax_y
        new_w = new_max_x - new_min_x
        new_h = new_max_y - new_min_y

        cur_min_x, cur_min_y, cur_w, cur_h = self._resolve_viewbox(self._svg_root)
        view_changed = (
            abs(new_min_x - cur_min_x) > 1e-6
            or abs(new_min_y - cur_min_y) > 1e-6
            or abs(new_w - cur_w) > 1e-6
            or abs(new_h - cur_h) > 1e-6
        )

        if view_changed:
            self._svg_root.set(
                "viewBox",
                f"{_format_num(new_min_x)} {_format_num(new_min_y)} {_format_num(new_w)} {_format_num(new_h)}",
            )
        self._svg_root.attrib.pop("width", None)
        self._svg_root.attrib.pop("height", None)

        bg = self._find_background_rect()
        if bg is not None:
            _set_attr(bg, "x", _format_num(new_min_x))
            _set_attr(bg, "y", _format_num(new_min_y))
            _set_attr(bg, "width", _format_num(new_w))
            _set_attr(bg, "height", _format_num(new_h))

    def _render_preview(self) -> None:
        self._draw_preview_png()

    def _reset_history(self, raw: str) -> None:
        self._history = [raw]
        self._history_index = 0

    def _push_history(self) -> None:
        if self._svg_root is None:
            return
        raw = ET.tostring(self._svg_root, encoding="unicode")
        if self._history_index >= 0 and self._history_index < len(self._history):
            if self._history[self._history_index] == raw:
                return
        if self._history_index < len(self._history) - 1:
            self._history = self._history[: self._history_index + 1]
        self._history.append(raw)
        self._history_index = len(self._history) - 1

    def _apply_history_state(self, raw: str) -> None:
        try:
            root = ET.fromstring(raw)
        except Exception:
            return
        self._clear_drag_state()
        self._svg_tree = ET.ElementTree(root)
        self._svg_root = root
        self._class_styles = self._collect_css_class_styles(root)
        self._render_svg()

    def _undo(self) -> None:
        if self._history_index <= 0:
            return
        self._history_index -= 1
        raw = self._history[self._history_index]
        self._apply_history_state(raw)

    def _redo(self) -> None:
        if self._history_index < 0 or self._history_index >= len(self._history) - 1:
            return
        self._history_index += 1
        raw = self._history[self._history_index]
        self._apply_history_state(raw)

    def _on_undo_key(self, _event=None):
        self._undo()
        return "break"

    def _on_redo_key(self, _event=None):
        self._redo()
        return "break"

    def _on_zoom_wheel(self, event: tk.Event):
        try:
            current = float(self._view_scale.get())
        except Exception:
            current = 1.0
        delta = getattr(event, "delta", 0)
        step = 0.1
        if delta > 0:
            current += step
        elif delta < 0:
            current -= step
        current = max(0.2, min(5.0, current))
        self._view_scale.set(current)
        self._render_preview()
        return "break"

    def _on_hscroll_wheel(self, event: tk.Event):
        if self.canvas is None:
            return "break"
        delta = int(getattr(event, "delta", 0))
        steps = int(-delta / 120) if delta else 0
        if steps == 0:
            steps = -1 if delta > 0 else 1
        self.canvas.xview_scroll(steps, "units")
        return "break"

    def _on_vscroll_wheel(self, event: tk.Event):
        if self.canvas is None:
            return "break"
        delta = int(getattr(event, "delta", 0))
        steps = int(-delta / 120) if delta else 0
        if steps == 0:
            steps = -1 if delta > 0 else 1
        self.canvas.yview_scroll(steps, "units")
        return "break"

    def _draw_preview_png(
        self,
        *,
        export_png_path: str | None = None,
        export_pdf_path: str | None = None,
        update_canvas: bool = True,
        force_transparent_bg: bool = False,
        export_scale: float = 1.0,
    ) -> bool:
        if self.canvas is None:
            return False
        if not self._drawables:
            if update_canvas:
                self.canvas.delete("all")
            return False
        try:
            _ensure_mathtext_fonts()
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            from matplotlib.figure import Figure
            from matplotlib.patches import Circle as MplCircle, Ellipse as MplEllipse, Polygon as MplPolygon
            from matplotlib.colors import to_rgba
            from matplotlib import patheffects as MplPathEffects
        except Exception as exc:
            messagebox.showerror("Render", f"Matplotlib no disponible: {exc}")
            return False

        shade_base_ids, shade_hole_ids = self._shade_runtime_highlight_roles()

        def layer_key(d: _Drawable) -> float:
            layer = float(d.layer)
            if d.record is not None and id(d.record.el) in shade_hole_ids:
                layer += 0.095
            elif d.record is not None and id(d.record.el) in shade_base_ids:
                layer += 0.09
            if self._selected is not None and d.record is self._selected:
                layer += 0.1
            return layer

        items = sorted(self._drawables, key=layer_key)
        try:
            zoom = float(self._view_scale.get())
        except Exception:
            zoom = 1.0
        if zoom <= 0:
            zoom = 1.0
        if export_png_path and not update_canvas:
            try:
                export_scale = float(export_scale)
            except Exception:
                export_scale = 1.0
            if export_scale <= 0:
                export_scale = 1.0
            zoom *= export_scale

        width = max(1.0, float(self._view_width) * zoom)
        height = max(1.0, float(self._view_height) * zoom)
        max_dim = max(width, height)
        max_px = 8000.0
        if max_dim > max_px:
            scale = max_px / max_dim
            zoom = max(1e-6, zoom * scale)
            width = max(1.0, float(self._view_width) * zoom)
            height = max(1.0, float(self._view_height) * zoom)
            try:
                if abs(float(self._view_scale.get()) - zoom) > 1e-6:
                    self._view_scale.set(zoom)
            except Exception:
                self._view_scale.set(zoom)
        dpi = 144.0
        bg_mode = (self._bg_mode_var.get() or "").strip().lower()
        bg_color = "#000000" if bg_mode == "negro" else "#ffffff"
        export_transparent_bg = force_transparent_bg or bg_mode == "sin fondo"

        fig_facecolor = "none" if export_transparent_bg and not update_canvas else bg_color
        fig = Figure(figsize=(width / dpi, height / dpi), dpi=dpi, facecolor=fig_facecolor)
        ax = fig.add_axes([0, 0, 1, 1])
        if export_transparent_bg and not update_canvas:
            fig.patch.set_alpha(0.0)
            ax.set_facecolor("none")
            ax.patch.set_alpha(0.0)
        ax.set_xlim(0, width)
        ax.set_ylim(height, 0)
        ax.axis("off")

        def px_to_pt(px: float) -> float:
            return px * 72.0 / dpi

        def apply_fill_alpha(color, alpha: float | None):
            if alpha is None or color == "none":
                return color
            try:
                if isinstance(color, tuple) and len(color) == 4:
                    a = max(0.0, min(1.0, color[3] * alpha))
                    return (color[0], color[1], color[2], a)
            except Exception:
                pass
            try:
                return to_rgba(color, alpha)
            except Exception:
                return color

        def add_arrow(
            x1: float,
            y1: float,
            x2: float,
            y2: float,
            color: str,
            size_px: float,
            zorder: float,
        ) -> None:
            dx = x2 - x1
            dy = y2 - y1
            dist = math.hypot(dx, dy)
            if dist <= 1e-6:
                return
            ux = dx / dist
            uy = dy / dist
            retreat = size_px * _ARROW_RETREAT_FRAC
            tip_x = x2 - ux * retreat
            tip_y = y2 - uy * retreat
            base_x = tip_x - ux * size_px
            base_y = tip_y - uy * size_px
            perp_x = -uy
            perp_y = ux
            half_w = size_px * 0.45
            p_left = (base_x + perp_x * half_w, base_y + perp_y * half_w)
            p_right = (base_x - perp_x * half_w, base_y - perp_y * half_w)
            # Stealth-style notch at the tail.
            notch_x = base_x + ux * (size_px * 0.2)
            notch_y = base_y + uy * (size_px * 0.2)
            arrow = MplPolygon(
                [p_left, (tip_x, tip_y), p_right, (notch_x, notch_y)],
                closed=True,
                edgecolor=color,
                facecolor=color,
                linewidth=0,
            )
            arrow.set_zorder(zorder)
            ax.add_patch(arrow)

        def to_canvas_x(v: float) -> float:
            return (v + self._shift_x) * zoom

        def to_canvas_y(v: float) -> float:
            return (v + self._shift_y) * zoom

        def shade_hole_patches(el: ET.Element):
            out = []
            tag = _strip_ns(el.tag)
            if tag == "polygon":
                coords = _parse_points(_get_attr(el, "points"))
                if len(coords) >= 6:
                    pts = [(to_canvas_x(coords[i]), to_canvas_y(coords[i + 1])) for i in range(0, len(coords) - 1, 2)]
                    out.append(MplPolygon(pts, closed=True, edgecolor="none", facecolor=bg_color, linewidth=0))
                return out
            if tag == "circle":
                cx = _parse_float(_get_attr(el, "cx"))
                cy = _parse_float(_get_attr(el, "cy"))
                r = _parse_float(_get_attr(el, "r"), 0.0)
                if r > 0:
                    out.append(MplCircle((to_canvas_x(cx), to_canvas_y(cy)), r * zoom, edgecolor="none", facecolor=bg_color, linewidth=0))
                return out
            if tag == "ellipse":
                cx = _parse_float(_get_attr(el, "cx"))
                cy = _parse_float(_get_attr(el, "cy"))
                rx = _parse_float(_get_attr(el, "rx"), 0.0)
                ry = _parse_float(_get_attr(el, "ry"), 0.0)
                if rx > 0 and ry > 0:
                    out.append(
                        MplEllipse(
                            (to_canvas_x(cx), to_canvas_y(cy)),
                            2.0 * rx * zoom,
                            2.0 * ry * zoom,
                            edgecolor="none",
                            facecolor=bg_color,
                            linewidth=0,
                        )
                    )
                return out
            if tag == "rect":
                x0 = _parse_float(_get_attr(el, "x"))
                y0 = _parse_float(_get_attr(el, "y"))
                w = _parse_float(_get_attr(el, "width"), 0.0)
                h = _parse_float(_get_attr(el, "height"), 0.0)
                if w > 0 and h > 0:
                    pts = [
                        (to_canvas_x(x0), to_canvas_y(y0)),
                        (to_canvas_x(x0 + w), to_canvas_y(y0)),
                        (to_canvas_x(x0 + w), to_canvas_y(y0 + h)),
                        (to_canvas_x(x0), to_canvas_y(y0 + h)),
                    ]
                    out.append(MplPolygon(pts, closed=True, edgecolor="none", facecolor=bg_color, linewidth=0))
                return out
            if tag == "path":
                if el.get("data-text") is not None:
                    return out
                d_raw = _get_attr(el, "d") or ""
                for pts, closed in _parse_svg_path(d_raw):
                    if not closed or len(pts) < 3:
                        continue
                    ppts = [(to_canvas_x(px), to_canvas_y(py)) for px, py in pts]
                    out.append(MplPolygon(ppts, closed=True, edgecolor="none", facecolor=bg_color, linewidth=0))
                return out
            return out

        def add_shade_hole_punches(base_el: ET.Element, clip_patch, zorder: float) -> None:
            holder = self._shade_holder_for_element(base_el)
            if holder is None:
                return
            holes = self._shade_hole_elements(holder)
            if not holes:
                return
            for hole_el in holes:
                for patch in shade_hole_patches(hole_el):
                    patch.set_zorder(zorder)
                    patch.set_clip_path(clip_patch)
                    ax.add_patch(patch)
                stroke_raw = self._effective_attr(hole_el, "stroke")
                stroke = _mpl_color(stroke_raw)
                if stroke in ("", "none"):
                    continue
                hole_w_px = _parse_float(self._effective_attr(hole_el, "stroke-width"), 1.0) * zoom
                hole_lw = px_to_pt(hole_w_px)
                hole_dash = _scaled_dash(_parse_dash(self._effective_attr(hole_el, "stroke-dasharray")), zoom)
                hole_dash_pts = [px_to_pt(float(v)) for v in hole_dash] if hole_dash else None
                for outline in shade_hole_patches(hole_el):
                    outline.set_facecolor("none")
                    outline.set_edgecolor(stroke)
                    outline.set_linewidth(hole_lw)
                    if hole_dash_pts:
                        outline.set_linestyle((0, hole_dash_pts))
                    outline.set_zorder(zorder + 0.03)
                    outline.set_clip_path(clip_patch)
                    ax.add_patch(outline)

        highlight = _HIGHLIGHT_COLOR
        selected_angle_id = None
        if self._selected is not None:
            selected_angle_id = self._selected.el.get("data-angle-id")
        for d in items:
            if export_transparent_bg and d.kind == "polygon":
                if d.layer == 0 and d.record is None:
                    continue
                if d.record is not None and self._is_background_rect_like(d.record.el):
                    continue
            stroke_raw = d.style.get("stroke")
            fill_raw = d.style.get("fill")
            if fill_raw == "context-stroke":
                fill_raw = stroke_raw
            if stroke_raw == "context-stroke":
                stroke_raw = fill_raw
            stroke = _mpl_color(stroke_raw)
            fill = _mpl_color(fill_raw)
            stroke_w_px = _parse_float(d.style.get("stroke-width"), 1.0) * zoom
            z_base = float(d.layer)
            is_selected = self._selected is not None and d.record is self._selected
            if not is_selected and selected_angle_id and d.record is not None:
                if d.record.el.get("data-angle-id") == selected_angle_id:
                    is_selected = True
            is_shade_hole = d.record is not None and id(d.record.el) in shade_hole_ids
            is_shade_base = d.record is not None and id(d.record.el) in shade_base_ids
            role_color = None
            if is_shade_hole:
                role_color = _SHADE_HOLE_HIGHLIGHT_COLOR
            elif is_shade_base:
                role_color = _SHADE_BASE_HIGHLIGHT_COLOR
            elif is_selected and not self._shade_diff_active:
                role_color = highlight
            if role_color is not None:
                stroke = role_color
                if d.kind in ("text", "latex"):
                    fill = role_color
                elif (
                    d.kind == "circle"
                    and d.record is not None
                    and self._is_point_circle(d.record.el)
                ):
                    pass
                elif d.kind in ("circle", "polygon", "ellipse") and fill not in ("none", ""):
                    fill = role_color
                stroke_w_px += 1.0
            lw = px_to_pt(stroke_w_px)
            dash = _scaled_dash(_parse_dash(d.style.get("stroke-dasharray")), zoom)
            dash_pts = None
            if dash:
                dash_pts = [px_to_pt(float(v)) for v in dash]

            if d.kind == "line":
                if stroke == "none":
                    continue
                coords = [c * zoom for c in d.coords]
                xs = [coords[0], coords[2]]
                ys = [coords[1], coords[3]]
                line = ax.plot(xs, ys, color=stroke, linewidth=lw)[0]
                line.set_solid_joinstyle("round")
                line.set_solid_capstyle("round")
                line.set_zorder(z_base)
                if dash_pts:
                    line.set_dashes(dash_pts)
                if d.record is not None:
                    el = d.record.el
                    arrow_base = _parse_float(self._global_arrow_size_var.get().strip(), 18.0)
                    if arrow_base <= 0:
                        arrow_base = 4.0
                    arrow_size = max(1.0, arrow_base * zoom)
                    if el.get("marker-start"):
                        add_arrow(coords[2], coords[3], coords[0], coords[1], stroke, arrow_size, z_base + 0.05)
                    if el.get("marker-end"):
                        add_arrow(coords[0], coords[1], coords[2], coords[3], stroke, arrow_size, z_base + 0.05)
            elif d.kind == "polyline":
                if stroke == "none":
                    continue
                coords = [c * zoom for c in d.coords]
                xs = coords[0::2]
                ys = coords[1::2]
                line = ax.plot(xs, ys, color=stroke, linewidth=lw)[0]
                line.set_zorder(z_base)
                if dash_pts:
                    line.set_dashes(dash_pts)
                if d.record is not None and len(coords) >= 4:
                    el = d.record.el
                    arrow_base = _parse_float(self._global_arrow_size_var.get().strip(), 18.0)
                    if arrow_base <= 0:
                        arrow_base = 4.0
                    arrow_size = max(1.0, arrow_base * zoom)
                    if el.get("marker-start"):
                        add_arrow(coords[2], coords[3], coords[0], coords[1], stroke, arrow_size, z_base + 0.05)
                    if el.get("marker-end"):
                        add_arrow(coords[-4], coords[-3], coords[-2], coords[-1], stroke, arrow_size, z_base + 0.05)
            elif d.kind == "polygon":
                coords = [c * zoom for c in d.coords]
                pts = list(zip(coords[0::2], coords[1::2]))
                edge = "none" if stroke == "none" else stroke
                face = "none" if fill == "none" else fill
                fill_alpha = _parse_float(d.style.get("fill-opacity"), None)
                if fill_alpha is not None:
                    fill_alpha = max(0.0, min(1.0, float(fill_alpha)))
                face = apply_fill_alpha(face, fill_alpha)
                poly = MplPolygon(pts, closed=True, edgecolor=edge, facecolor=face, linewidth=lw)
                poly.set_zorder(z_base)
                if edge != "none":
                    poly.set_joinstyle("round")
                if dash_pts and edge != "none":
                    poly.set_linestyle((0, dash_pts))
                ax.add_patch(poly)
                if d.record is not None:
                    add_shade_hole_punches(d.record.el, poly, z_base + 0.06)
            elif d.kind == "circle":
                coords = [c * zoom for c in d.coords]
                x1, y1, x2, y2 = coords
                cx = 0.5 * (x1 + x2)
                cy = 0.5 * (y1 + y2)
                r = abs(x2 - x1) * 0.5
                edge = "none" if stroke == "none" else stroke
                face = "none" if fill == "none" else fill
                fill_alpha = _parse_float(d.style.get("fill-opacity"), None)
                if fill_alpha is not None:
                    fill_alpha = max(0.0, min(1.0, float(fill_alpha)))
                face = apply_fill_alpha(face, fill_alpha)
                circ = MplCircle((cx, cy), r, edgecolor=edge, facecolor=face, linewidth=lw)
                circ.set_zorder(z_base)
                if dash_pts and edge != "none":
                    circ.set_linestyle((0, dash_pts))
                ax.add_patch(circ)
                if d.record is not None:
                    add_shade_hole_punches(d.record.el, circ, z_base + 0.06)
            elif d.kind == "ellipse":
                coords = [c * zoom for c in d.coords]
                x1, y1, x2, y2 = coords
                cx = 0.5 * (x1 + x2)
                cy = 0.5 * (y1 + y2)
                rx = abs(x2 - x1) * 0.5
                ry = abs(y2 - y1) * 0.5
                edge = "none" if stroke == "none" else stroke
                face = "none" if fill == "none" else fill
                fill_alpha = _parse_float(d.style.get("fill-opacity"), None)
                if fill_alpha is not None:
                    fill_alpha = max(0.0, min(1.0, float(fill_alpha)))
                face = apply_fill_alpha(face, fill_alpha)
                ell = MplEllipse((cx, cy), 2.0 * rx, 2.0 * ry, edgecolor=edge, facecolor=face, linewidth=lw)
                ell.set_zorder(z_base)
                if dash_pts and edge != "none":
                    ell.set_linestyle((0, dash_pts))
                ax.add_patch(ell)
                if d.record is not None:
                    add_shade_hole_punches(d.record.el, ell, z_base + 0.06)
            elif d.kind in ("text", "latex"):
                coords = [d.coords[0] * zoom, d.coords[1] * zoom]
                color = _mpl_color(fill if fill is not None else d.style.get("fill", "#000"))
                if color == "none":
                    continue
                size_px = _parse_float(d.style.get("font-size"), 12.0) * zoom
                size_pt = px_to_pt(size_px)
                text = d.text or ""
                if d.kind == "latex":
                    math_text = _safe_mathtext(text)
                    if math_text is None:
                        continue
                    text = math_text
                family = d.style.get("font-family")
                font_families = None
                if family:
                    font_families = [p.strip() for p in family.split(",") if p.strip()]
                weight = d.style.get("font-weight")
                ha = "left"
                va = "baseline"
                is_cut_label = False
                cut_shape = _LABEL_CUT_SHAPE_CONTOUR
                if d.record is not None:
                    frac = d.record.el.get("data-anchor-frac")
                    if frac:
                        parts = _parse_points(frac)
                        if len(parts) >= 2:
                            ax_f, ay_f = parts[0], parts[1]
                            if abs(ax_f - 0.5) <= 1e-3:
                                ha = "center"
                            if abs(ay_f - 0.5) <= 1e-3:
                                va = "center"
                    is_cut_label = self._label_bg_mode_for(d.record.el) == _LABEL_BG_MODE_CUT
                    if is_cut_label:
                        cut_shape = self._label_cut_shape_for(d.record.el)

                def add_text_cutout(draw_text: str) -> None:
                    if not is_cut_label:
                        return
                    if cut_shape == _LABEL_CUT_SHAPE_RECT and d.record is not None:
                        bounds = self._label_bounds(d.record.el)
                        if bounds is not None:
                            min_x2, min_y2, max_x2, max_y2 = bounds
                            pad2 = _LABEL_CUT_RECT_PAD
                            x0 = to_canvas_x(min_x2 - pad2)
                            y0 = to_canvas_y(min_y2 - pad2)
                            x1 = to_canvas_x(max_x2 + pad2)
                            y1 = to_canvas_y(max_y2 + pad2)
                            rect_pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
                            rect = MplPolygon(
                                rect_pts,
                                closed=True,
                                edgecolor="none",
                                facecolor=bg_color,
                                linewidth=0.0,
                            )
                            rect.set_zorder(z_base + 0.15)
                            ax.add_patch(rect)
                            return
                    # Para etiquetas LaTeX persistidas como path[data-text], usar su
                    # geometria real para que el recorte coincida con el contorno.
                    if d.record is not None:
                        label_el = d.record.el
                        if _strip_ns(label_el.tag) == "path" and label_el.get("data-text") is not None:
                            d_raw = _get_attr(label_el, "d") or ""
                            if d_raw.strip():
                                path_stroke = _parse_float(
                                    self._effective_attr(label_el, "stroke-width"),
                                    _LABEL_CUT_MASK_STROKE,
                                )
                                cut_lw = px_to_pt(max(path_stroke * zoom, _LABEL_CUT_MASK_STROKE))
                                added = False
                                for pts, closed in _parse_svg_path(d_raw):
                                    if len(pts) < 2:
                                        continue
                                    canvas_pts = [(to_canvas_x(px), to_canvas_y(py)) for px, py in pts]
                                    if closed and len(canvas_pts) >= 3:
                                        poly = MplPolygon(
                                            canvas_pts,
                                            closed=True,
                                            edgecolor=bg_color,
                                            facecolor=bg_color,
                                            linewidth=cut_lw,
                                        )
                                        poly.set_zorder(z_base + 0.15)
                                        poly.set_joinstyle("round")
                                        ax.add_patch(poly)
                                        added = True
                                    else:
                                        xs = [p[0] for p in canvas_pts]
                                        ys = [p[1] for p in canvas_pts]
                                        line = ax.plot(xs, ys, color=bg_color, linewidth=cut_lw)[0]
                                        line.set_solid_joinstyle("round")
                                        line.set_solid_capstyle("round")
                                        line.set_zorder(z_base + 0.15)
                                        added = True
                                if added:
                                    return
                    cut = ax.text(
                        coords[0],
                        coords[1],
                        draw_text,
                        color=bg_color,
                        fontsize=size_pt,
                        fontfamily=font_families,
                        fontweight=weight,
                        ha=ha,
                        va=va,
                    )
                    # Simula el recorte del trazo alrededor del contorno de la etiqueta.
                    cut.set_path_effects(
                        [
                            MplPathEffects.withStroke(
                                linewidth=px_to_pt(max(_LABEL_CUT_MASK_STROKE, 1.0)),
                                foreground=bg_color,
                            )
                        ]
                    )
                    cut.set_zorder(z_base + 0.15)
                try:
                    add_text_cutout(text)
                    text_obj = ax.text(
                        coords[0],
                        coords[1],
                        text,
                        color=color,
                        fontsize=size_pt,
                        fontfamily=font_families,
                        fontweight=weight,
                        ha=ha,
                        va=va,
                    )
                    text_obj.set_zorder(z_base + 0.2)
                except Exception:
                    if d.kind != "latex":
                        continue
                    plain = _strip_mathtext_delims(d.text or "")
                    if not plain:
                        continue
                    add_text_cutout(plain)
                    text_obj = ax.text(
                        coords[0],
                        coords[1],
                        plain,
                        color=color,
                        fontsize=size_pt,
                        fontfamily=font_families,
                        fontweight=weight,
                        ha=ha,
                        va=va,
                    )
                    text_obj.set_zorder(z_base + 0.2)

        if export_pdf_path:
            try:
                if export_transparent_bg:
                    fig.savefig(
                        export_pdf_path,
                        format="pdf",
                        dpi=dpi,
                        facecolor="none",
                        edgecolor="none",
                        transparent=True,
                    )
                else:
                    fig.savefig(
                        export_pdf_path,
                        format="pdf",
                        dpi=dpi,
                        facecolor=bg_color,
                        edgecolor=bg_color,
                    )
            except Exception as exc:
                messagebox.showerror("Exportar PDF", f"No se pudo exportar PDF: {exc}")
                return False

        if export_png_path:
            try:
                if export_transparent_bg:
                    fig.savefig(
                        export_png_path,
                        format="png",
                        dpi=dpi,
                        facecolor="none",
                        edgecolor="none",
                        transparent=True,
                    )
                else:
                    fig.savefig(
                        export_png_path,
                        format="png",
                        dpi=dpi,
                        facecolor=bg_color,
                        edgecolor=bg_color,
                    )
            except Exception as exc:
                messagebox.showerror("Exportar PNG", f"No se pudo exportar PNG: {exc}")
                return False

        if update_canvas:
            canvas = FigureCanvasAgg(fig)
            canvas.draw()
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=dpi, facecolor="white")
            data = base64.b64encode(buf.getvalue()).decode("ascii")
            self._preview_image = tk.PhotoImage(data=data)
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, image=self._preview_image, anchor="nw")
            self.canvas.config(scrollregion=(0, 0, int(width), int(height)))
        return True

    def _text_bounds(self, text: str, font_size: float) -> tuple[float, float, float, float]:
        w = max(6.0, 0.6 * font_size * max(1, len(text)))
        h = max(6.0, font_size)
        return (0.0, w, -0.2 * h, 0.8 * h)

    def _drawable_bounds(self, d: _Drawable, zoom: float) -> tuple[float, float, float, float]:
        if not d.coords:
            return (0.0, 0.0, 0.0, 0.0)
        if d.kind in ("line", "polyline", "polygon"):
            xs = [c * zoom for c in d.coords[::2]]
            ys = [c * zoom for c in d.coords[1::2]]
            return (min(xs), min(ys), max(xs), max(ys))
        if d.kind == "circle":
            coords = [c * zoom for c in d.coords]
            x1, y1, x2, y2 = coords
            return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        if d.kind == "ellipse":
            coords = [c * zoom for c in d.coords]
            x1, y1, x2, y2 = coords
            return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        if d.kind in ("text", "latex"):
            font_size = _parse_float(d.style.get("font-size"), 12.0) * zoom
            x0, x1, y0, y1 = self._text_bounds(d.text or "", font_size)
            x = d.coords[0] * zoom
            y = d.coords[1] * zoom
            return (x + x0, y + y0, x + x1, y + y1)
        return (0.0, 0.0, 0.0, 0.0)

    def _hit_test_drawable(self, d: _Drawable, x: float, y: float, zoom: float) -> float | None:
        tol = 6.0
        if not d.coords:
            return None
        if d.kind in ("line", "polyline"):
            coords = [c * zoom for c in d.coords]
            min_d = 1e9
            for i in range(0, len(coords) - 2, 2):
                dseg = self._dist_point_to_segment(x, y, coords[i], coords[i + 1], coords[i + 2], coords[i + 3])
                min_d = min(min_d, dseg)
            return min_d
        if d.kind == "polygon":
            coords = [c * zoom for c in d.coords]
            pts = list(zip(coords[0::2], coords[1::2]))
            fill = d.style.get("fill", "")
            if fill and fill not in ("none", "transparent") and self._point_in_polygon(x, y, pts):
                if d.record is not None and self._point_in_shade_hole_canvas(d.record.el, x, y, zoom):
                    pass
                else:
                    return 0.0
            min_d = 1e9
            for i in range(len(pts)):
                x1, y1 = pts[i]
                x2, y2 = pts[(i + 1) % len(pts)]
                min_d = min(min_d, self._dist_point_to_segment(x, y, x1, y1, x2, y2))
            return min_d
        if d.kind == "circle":
            coords = [c * zoom for c in d.coords]
            x1, y1, x2, y2 = coords
            cx = 0.5 * (x1 + x2)
            cy = 0.5 * (y1 + y2)
            r = abs(x2 - x1) * 0.5
            dist = math.hypot(x - cx, y - cy)
            fill = d.style.get("fill", "")
            if fill and fill not in ("none", "transparent") and dist <= r + tol:
                if d.record is not None and self._point_in_shade_hole_canvas(d.record.el, x, y, zoom):
                    pass
                else:
                    return 0.0
            return abs(dist - r)
        if d.kind == "ellipse":
            coords = [c * zoom for c in d.coords]
            x1, y1, x2, y2 = coords
            cx = 0.5 * (x1 + x2)
            cy = 0.5 * (y1 + y2)
            rx = abs(x2 - x1) * 0.5
            ry = abs(y2 - y1) * 0.5
            if rx <= 1e-9 or ry <= 1e-9:
                return math.hypot(x - cx, y - cy)
            dx = (x - cx) / rx
            dy = (y - cy) / ry
            norm = dx * dx + dy * dy
            fill = d.style.get("fill", "")
            if fill and fill not in ("none", "transparent") and norm <= 1.0 + 1e-6:
                if d.record is not None and self._point_in_shade_hole_canvas(d.record.el, x, y, zoom):
                    pass
                else:
                    return 0.0
            dist = abs(math.sqrt(norm) - 1.0) * max(rx, ry)
            return dist
        if d.kind in ("text", "latex"):
            font_size = _parse_float(d.style.get("font-size"), 12.0) * zoom
            text = d.text or ""
            width = max(6.0, 0.6 * font_size * max(1, len(text)))
            height = max(6.0, font_size)
            x0 = d.coords[0] * zoom
            y0 = d.coords[1] * zoom
            left = x0
            right = x0 + width
            top = y0 - height
            bottom = y0
            if left - tol <= x <= right + tol and top - tol <= y <= bottom + tol:
                if left <= x <= right and top <= y <= bottom:
                    return 0.0
                dx = max(left - x, 0.0, x - right)
                dy = max(top - y, 0.0, y - bottom)
                return math.hypot(dx, dy)
            return None
        return None

    def _dist_point_to_segment(self, px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
        vx = x2 - x1
        vy = y2 - y1
        denom = vx * vx + vy * vy
        if denom <= 1e-12:
            return math.hypot(px - x1, py - y1)
        t = ((px - x1) * vx + (py - y1) * vy) / denom
        t = max(0.0, min(1.0, t))
        cx = x1 + t * vx
        cy = y1 + t * vy
        return math.hypot(px - cx, py - cy)

    def _point_in_polygon(self, x: float, y: float, pts: list[tuple[float, float]]) -> bool:
        inside = False
        j = len(pts) - 1
        for i in range(len(pts)):
            xi, yi = pts[i]
            xj, yj = pts[j]
            intersects = ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi)
            if intersects:
                inside = not inside
            j = i
        return inside

    def _intersections_for_elements(
        self, el1: ET.Element, el2: ET.Element, *, near_tol: float = 0.0
    ) -> list[tuple[float, float]]:
        circ1 = self._circle_from_element(el1)
        circ2 = self._circle_from_element(el2)
        points: list[tuple[float, float]]
        if circ1 is not None and circ2 is not None:
            points = self._circle_circle_intersections(circ1, circ2)
        elif circ1 is not None:
            segs2 = self._segments_from_element(el2)
            points = self._circle_segments_intersections(circ1, segs2)
        elif circ2 is not None:
            segs1 = self._segments_from_element(el1)
            points = self._circle_segments_intersections(circ2, segs1)
        else:
            segs1 = self._segments_from_element(el1)
            segs2 = self._segments_from_element(el2)
            points = self._segments_intersections(segs1, segs2)
        if points or near_tol <= 0:
            return points
        segs1 = self._segments_for_intersection_fallback(el1, circ1)
        segs2 = self._segments_for_intersection_fallback(el2, circ2)
        if not segs1 or not segs2:
            return []
        return self._segments_near_intersections(segs1, segs2, tol=max(1e-9, near_tol))

    def _circle_from_element(self, el: ET.Element) -> tuple[float, float, float] | None:
        tag = _strip_ns(el.tag)
        if tag == "circle":
            if self._is_point_circle(el):
                return None
            r = _parse_float(_get_attr(el, "r"), 0.0)
            if r <= 0:
                return None
            cx = _parse_float(_get_attr(el, "cx"))
            cy = _parse_float(_get_attr(el, "cy"))
            return (cx, cy, r)
        if tag == "ellipse":
            rx = _parse_float(_get_attr(el, "rx"), 0.0)
            ry = _parse_float(_get_attr(el, "ry"), 0.0)
            if rx <= 0 or ry <= 0:
                return None
            if abs(rx - ry) <= 1e-6:
                cx = _parse_float(_get_attr(el, "cx"))
                cy = _parse_float(_get_attr(el, "cy"))
                return (cx, cy, 0.5 * (rx + ry))
        return None

    def _segments_from_element(self, el: ET.Element) -> list[tuple[float, float, float, float]]:
        tag = _strip_ns(el.tag)
        if tag == "line":
            x1 = _parse_float(_get_attr(el, "x1"))
            y1 = _parse_float(_get_attr(el, "y1"))
            x2 = _parse_float(_get_attr(el, "x2"))
            y2 = _parse_float(_get_attr(el, "y2"))
            if math.hypot(x2 - x1, y2 - y1) <= 1e-9:
                return []
            return [(x1, y1, x2, y2)]
        if tag in ("polyline", "polygon"):
            coords = _parse_points(_get_attr(el, "points"))
            pts = [(coords[i], coords[i + 1]) for i in range(0, len(coords) - 1, 2)]
            return self._segments_from_points(pts, closed=(tag == "polygon"))
        if tag == "rect":
            x = _parse_float(_get_attr(el, "x"))
            y = _parse_float(_get_attr(el, "y"))
            w = _parse_float(_get_attr(el, "width"))
            h = _parse_float(_get_attr(el, "height"))
            if w <= 0 or h <= 0:
                return []
            pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
            return self._segments_from_points(pts, closed=True)
        if tag == "ellipse":
            rx = _parse_float(_get_attr(el, "rx"), 0.0)
            ry = _parse_float(_get_attr(el, "ry"), 0.0)
            if rx <= 0 or ry <= 0:
                return []
            cx = _parse_float(_get_attr(el, "cx"))
            cy = _parse_float(_get_attr(el, "cy"))
            pts = self._ellipse_points(cx, cy, rx, ry)
            return self._segments_from_points(pts, closed=True)
        if tag == "path":
            if el.get("data-text") is not None:
                return []
            d = _get_attr(el, "d") or ""
            segs: list[tuple[float, float, float, float]] = []
            for pts, closed in _parse_svg_path(d):
                segs.extend(self._segments_from_points(pts, closed=closed))
            return segs
        return []

    def _ellipse_points(
        self, cx: float, cy: float, rx: float, ry: float, *, steps: int = 96
    ) -> list[tuple[float, float]]:
        pts: list[tuple[float, float]] = []
        steps = max(12, int(steps))
        for i in range(steps):
            ang = (2.0 * math.pi) * (i / float(steps))
            pts.append((cx + rx * math.cos(ang), cy + ry * math.sin(ang)))
        return pts

    def _segments_from_points(
        self, pts: list[tuple[float, float]], *, closed: bool
    ) -> list[tuple[float, float, float, float]]:
        if len(pts) < 2:
            return []
        segs: list[tuple[float, float, float, float]] = []
        for i in range(len(pts) - 1):
            ax, ay = pts[i]
            bx, by = pts[i + 1]
            if math.hypot(bx - ax, by - ay) <= 1e-9:
                continue
            segs.append((ax, ay, bx, by))
        if closed:
            ax, ay = pts[0]
            bx, by = pts[-1]
            if abs(ax - bx) > 1e-9 or abs(ay - by) > 1e-9:
                segs.append((bx, by, ax, ay))
        return segs

    def _segments_intersections(
        self,
        segs1: list[tuple[float, float, float, float]],
        segs2: list[tuple[float, float, float, float]],
    ) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for ax, ay, bx, by in segs1:
            for cx, cy, dx, dy in segs2:
                hit = self._segment_intersection(ax, ay, bx, by, cx, cy, dx, dy)
                if hit is not None:
                    out.append(hit)
        return self._dedup_points(out)

    def _segments_for_intersection_fallback(
        self,
        el: ET.Element,
        circle: tuple[float, float, float] | None,
    ) -> list[tuple[float, float, float, float]]:
        if circle is not None:
            cx, cy, r = circle
            pts = self._ellipse_points(cx, cy, r, r, steps=192)
            return self._segments_from_points(pts, closed=True)
        return self._segments_from_element(el)

    def _project_point_to_segment(
        self,
        px: float,
        py: float,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
    ) -> tuple[float, float, float]:
        vx = x2 - x1
        vy = y2 - y1
        denom = vx * vx + vy * vy
        if denom <= 1e-12:
            return (x1, y1, 0.0)
        t = ((px - x1) * vx + (py - y1) * vy) / denom
        t = max(0.0, min(1.0, t))
        return (x1 + t * vx, y1 + t * vy, t)

    def _segments_near_intersections(
        self,
        segs1: list[tuple[float, float, float, float]],
        segs2: list[tuple[float, float, float, float]],
        *,
        tol: float,
    ) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        tol = max(1e-9, float(tol))
        for ax, ay, bx, by in segs1:
            for cx, cy, dx, dy in segs2:
                for px, py in ((ax, ay), (bx, by)):
                    qx, qy, _ = self._project_point_to_segment(px, py, cx, cy, dx, dy)
                    if math.hypot(px - qx, py - qy) <= tol:
                        out.append(((px + qx) * 0.5, (py + qy) * 0.5))
                for px, py in ((cx, cy), (dx, dy)):
                    qx, qy, _ = self._project_point_to_segment(px, py, ax, ay, bx, by)
                    if math.hypot(px - qx, py - qy) <= tol:
                        out.append(((px + qx) * 0.5, (py + qy) * 0.5))
        return self._dedup_points(out, tol=max(1e-6, tol))

    def _segment_intersection(
        self,
        ax: float,
        ay: float,
        bx: float,
        by: float,
        cx: float,
        cy: float,
        dx: float,
        dy: float,
        *,
        eps: float = 1e-9,
    ) -> tuple[float, float] | None:
        rx = bx - ax
        ry = by - ay
        sx = dx - cx
        sy = dy - cy
        denom = rx * sy - ry * sx
        if abs(denom) <= eps:
            for px, py in ((ax, ay), (bx, by)):
                if math.hypot(px - cx, py - cy) <= eps or math.hypot(px - dx, py - dy) <= eps:
                    return (px, py)
            for px, py in ((cx, cy), (dx, dy)):
                if math.hypot(px - ax, py - ay) <= eps or math.hypot(px - bx, py - by) <= eps:
                    return (px, py)
            return None
        t = ((cx - ax) * sy - (cy - ay) * sx) / denom
        u = ((cx - ax) * ry - (cy - ay) * rx) / denom
        if t < -eps or t > 1.0 + eps or u < -eps or u > 1.0 + eps:
            return None
        return (ax + t * rx, ay + t * ry)

    def _circle_segments_intersections(
        self,
        circle: tuple[float, float, float],
        segs: list[tuple[float, float, float, float]],
    ) -> list[tuple[float, float]]:
        cx, cy, r = circle
        out: list[tuple[float, float]] = []
        for ax, ay, bx, by in segs:
            out.extend(self._circle_segment_intersections(cx, cy, r, ax, ay, bx, by))
        return self._dedup_points(out)

    def _circle_segment_intersections(
        self, cx: float, cy: float, r: float, ax: float, ay: float, bx: float, by: float
    ) -> list[tuple[float, float]]:
        dx = bx - ax
        dy = by - ay
        A = dx * dx + dy * dy
        if A <= 1e-12:
            return []
        B = 2.0 * (dx * (ax - cx) + dy * (ay - cy))
        C = (ax - cx) ** 2 + (ay - cy) ** 2 - r * r
        disc = B * B - 4.0 * A * C
        eps = 1e-9
        if disc < -eps:
            return []
        pts: list[tuple[float, float]] = []
        if abs(disc) <= eps:
            t = -B / (2.0 * A)
            if -eps <= t <= 1.0 + eps:
                pts.append((ax + t * dx, ay + t * dy))
        else:
            sq = math.sqrt(max(0.0, disc))
            t1 = (-B + sq) / (2.0 * A)
            t2 = (-B - sq) / (2.0 * A)
            if -eps <= t1 <= 1.0 + eps:
                pts.append((ax + t1 * dx, ay + t1 * dy))
            if -eps <= t2 <= 1.0 + eps:
                pts.append((ax + t2 * dx, ay + t2 * dy))
        return pts

    def _circle_circle_intersections(
        self,
        c1: tuple[float, float, float],
        c2: tuple[float, float, float],
    ) -> list[tuple[float, float]]:
        x0, y0, r0 = c1
        x1, y1, r1 = c2
        dx = x1 - x0
        dy = y1 - y0
        d = math.hypot(dx, dy)
        if d <= 1e-12:
            return []
        if d > r0 + r1 + 1e-9:
            return []
        if d < abs(r0 - r1) - 1e-9:
            return []
        a = (r0 * r0 - r1 * r1 + d * d) / (2.0 * d)
        h2 = r0 * r0 - a * a
        if h2 < 0:
            h2 = 0.0
        h = math.sqrt(h2)
        xm = x0 + a * dx / d
        ym = y0 + a * dy / d
        rx = -dy * (h / d)
        ry = dx * (h / d)
        p1 = (xm + rx, ym + ry)
        p2 = (xm - rx, ym - ry)
        return self._dedup_points([p1, p2])

    def _dedup_points(self, pts: list[tuple[float, float]], *, tol: float = 1e-6) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for px, py in pts:
            if not any(abs(px - qx) <= tol and abs(py - qy) <= tol for qx, qy in out):
                out.append((px, py))
        return out

    def _pick_intersection_point(
        self, pts: list[tuple[float, float]], ref: tuple[float, float] | None
    ) -> tuple[float, float] | None:
        if not pts:
            return None
        if ref is None:
            return pts[0]
        rx, ry = ref
        return min(pts, key=lambda p: ((p[0] - rx) ** 2 + (p[1] - ry) ** 2, p[0], p[1]))

    def _select_all_entry(self, event=None):
        widget = getattr(event, "widget", None)
        if widget is None:
            return None
        try:
            widget.after(1, lambda: widget.selection_range(0, "end"))
        except Exception:
            return None
        return None

    def _bind_entry_commit(self, entry: tk.Widget, callback) -> None:
        entry.bind("<Return>", callback)
        entry.bind("<FocusOut>", callback)
        entry.bind("<FocusIn>", self._select_all_entry)
        entry.bind("<Button-1>", self._select_all_entry)

    def _on_point_field_commit(self, _event=None) -> None:
        self._on_point_field_change()

    def _on_polygon_field_commit(self, _event=None) -> None:
        self._on_polygon_field_change()

    def _on_circle_field_commit(self, _event=None) -> None:
        self._on_circle_field_change()

    def _on_stroke_dash_commit(self, _event=None) -> None:
        self._on_stroke_dash_change()

    def _on_curve_field_commit(self, _event=None) -> None:
        self._on_curve_field_change()

    def _on_segment_field_commit(self, _event=None) -> None:
        self._update_segment_mark_fields()

    def _on_segment_style_selected(self, _event=None) -> None:
        self._update_segment_mark_fields()
        return None

    def _global_text_focus_active(self) -> bool:
        focus = self.focus_get()
        return isinstance(focus, (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox))

    def _on_global_return(self, _event=None):
        if not self._shade_diff_active:
            return None
        if self._global_text_focus_active():
            return None
        if self._shade_contour_edges:
            self._set_shade_status("Usa Aplicar para confirmar el sombreado del contorno.")
        else:
            self._set_shade_status("Contorno: no hay cadena activa para aplicar.")
        return "break"

    def _on_global_escape(self, _event=None):
        if self._global_text_focus_active():
            return None
        if self._shade_diff_active:
            self._clear_shade_diff_selection()
            return "break"
        if self._curve_radius_create_active:
            self._curve_radius_create_center_el = None
            self._set_curve_radius_status("Radio: selecciona centro (punto geometrico).")
            return "break"
        if self._projection_create_active:
            self._projection_create_source = None
            self._set_projection_status("Proyeccion: selecciona punto o segmento origen.")
            return "break"
        return None

    def _on_group_shortcut_group(self, _event=None):
        return None

    def _on_group_shortcut_ungroup(self, _event=None):
        return None

    def _on_shade_diff_opacity_commit(self, _event=None) -> None:
        self._normalize_shade_diff_opacity()

    def _normalize_shade_diff_opacity(self) -> float:
        raw = self._shade_diff_opacity_var.get().strip()
        val = _parse_float(raw, 0.15)
        val = max(0.0, min(1.0, float(val)))
        self._shade_diff_opacity_var.set(_format_num(val))
        return val

    def _on_selected_shade_opacity_commit(self, _event=None) -> None:
        self._normalize_selected_shade_opacity()

    def _normalize_selected_shade_opacity(self) -> float:
        raw = self._shade_selected_opacity_var.get().strip()
        val = _parse_float(raw, 0.15)
        val = max(0.0, min(1.0, float(val)))
        self._shade_selected_opacity_var.set(_format_num(val))
        return val

    def _sync_shade_selected_editor(self, record: _Record | None) -> None:
        if not hasattr(self, "_global_shade_selected_opacity_entry"):
            return
        self._shade_selected_editor_enabled = False
        self._selected_shade_el = None
        if record is not None and self._is_shade_contour_helper(record.el):
            self._shade_selected_editor_enabled = True
            self._selected_shade_el = record.el
            cur = _parse_float(self._effective_attr(record.el, "fill-opacity"), 0.15)
            cur = max(0.0, min(1.0, float(cur)))
            self._shade_selected_opacity_var.set(_format_num(cur))
            self._global_shade_selected_opacity_entry.configure(state="normal")
            self._global_shade_selected_apply_btn.configure(state="normal")
            return
        self._global_shade_selected_opacity_entry.configure(state="disabled")
        self._global_shade_selected_apply_btn.configure(state="disabled")

    def _apply_selected_shade_opacity(self) -> None:
        if self._svg_root is None:
            return
        target = self._selected_shade_el
        if target is None and self._selected is not None and self._is_shade_contour_helper(self._selected.el):
            target = self._selected.el
        if target is None or not self._element_in_svg(target):
            messagebox.showerror("Sombreado", "Selecciona una region sombreada para cambiar su intensidad.")
            return
        val = self._normalize_selected_shade_opacity()
        self._push_history()
        _set_attr(target, "fill-opacity", _format_num(val))
        _force_style_attr(target, "fill-opacity", _format_num(val))
        self._set_transform_status("Intensidad de sombreado aplicada.")
        self._render_svg()

    def _point_selection_info(
        self, record: _Record | None
    ) -> tuple[ET.Element | None, ET.Element | None, tuple[float, float] | None]:
        if record is None or self._svg_root is None:
            return (None, None, None)
        tag = _strip_ns(record.el.tag)
        if tag == "circle":
            if not self._is_point_circle(record.el):
                return (None, None, None)
            ax = _parse_float(_get_attr(record.el, "cx"))
            ay = _parse_float(_get_attr(record.el, "cy"))
            label = self._find_label_for_anchor(ax, ay)
            if label is None:
                label = self._find_label_near_point(ax, ay)
                if label is not None:
                    self._attach_label_to_anchor(label, ax, ay)
            return (record.el, label, (ax, ay))
        if record.kind == "label":
            ax = record.el.get("data-anchor-x")
            ay = record.el.get("data-anchor-y")
            if ax is not None and ay is not None:
                anchor = (_parse_float(ax), _parse_float(ay))
                point_el = self._find_point_by_anchor(anchor[0], anchor[1])
                return (point_el, record.el, anchor) if point_el is not None else (None, record.el, anchor)
            return (None, None, None)
        return (None, None, None)

    def _segment_selection_info(self, record: _Record | None) -> ET.Element | None:
        if record is None:
            return None
        el = record.el
        if _strip_ns(el.tag) != "line":
            return None
        kind = (el.get("data-kind") or "").strip()
        if kind and _is_aux_data_kind(kind) and kind not in (
            "subsegment",
            "circle-radius",
            _CURVE_RADIUS_DATA_KIND,
            _SEG_DIM_LINE_DATA_KIND,
        ):
            return None
        return el

    def _curve_selection_info(self, record: _Record | None) -> ET.Element | None:
        if record is None:
            return None
        if record.kind == "label":
            return None
        el = record.el
        tag = _strip_ns(el.tag)
        kind = (el.get("data-kind") or "").strip()
        if tag == "circle":
            if not self._is_editable_circle(el):
                return None
            return el
        if tag == "ellipse":
            if _is_aux_data_kind(kind):
                return None
            rx = _parse_float(_get_attr(el, "rx"), 0.0)
            ry = _parse_float(_get_attr(el, "ry"), 0.0)
            if rx <= 0 or ry <= 0:
                return None
            return el
        if tag == "path":
            if el.get("data-text") is not None:
                return None
            if kind and _is_aux_data_kind(kind) and kind != "subsegment":
                return None
            d = _get_attr(el, "d") or ""
            for pts, _closed in _parse_svg_path(d):
                if len(pts) >= 2:
                    return el
            return None
        return None

    def _find_point_by_anchor(self, ax: float, ay: float) -> ET.Element | None:
        if self._svg_root is None:
            return None
        tol = 1.0
        for el in self._svg_root.iter():
            if _strip_ns(el.tag) != "circle":
                continue
            if (el.get("data-kind") or "").strip() == "seg-endpoint":
                continue
            if not self._is_point_circle(el):
                continue
            cx = _parse_float(_get_attr(el, "cx"))
            cy = _parse_float(_get_attr(el, "cy"))
            if abs(cx - ax) <= tol and abs(cy - ay) <= tol:
                return el
        return None

    def _is_point_label_candidate(self, el: ET.Element) -> bool:
        tag = _strip_ns(el.tag)
        if tag not in ("text", "path"):
            return False
        if tag == "path" and el.get("data-text") is None:
            return False
        if el.get("data-angle-id") is not None:
            return False
        if (el.get("data-angle-kind") or "").strip():
            return False
        if (el.get("data-kind") or "").strip() in ("seg-endpoint-label", "seg-mid-label"):
            return False
        return True

    def _label_visible_text(self, el: ET.Element) -> str:
        if _strip_ns(el.tag) == "text":
            return (el.text or "").strip()
        return (el.get("data-text") or "").strip()

    def _point_id_exists_elsewhere(self, point_id: str, point_el: ET.Element | None) -> bool:
        if self._svg_root is None or not point_id:
            return False
        for el in self._svg_root.iter():
            if el is point_el:
                continue
            if _strip_ns(el.tag) != "circle":
                continue
            if not self._is_point_circle(el):
                continue
            if (el.get("data-point-id") or "").strip() == point_id:
                return True
        return False

    def _sync_point_identity_from_label(self, point_el: ET.Element, label_el: ET.Element, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        current_id = (point_el.get("data-point-id") or "").strip()
        if current_id != text and not self._point_id_exists_elsewhere(text, point_el):
            point_el.set("data-point-id", text)
            current_id = text
        if current_id:
            label_el.set("data-point-id", current_id)

    def _find_label_for_anchor(self, ax: float, ay: float) -> ET.Element | None:
        if self._svg_root is None:
            return None
        tol = 1.5
        candidates: list[tuple[int, ET.Element]] = []
        for el in self._svg_root.iter():
            if not self._is_point_label_candidate(el):
                continue
            dax = el.get("data-anchor-x")
            day = el.get("data-anchor-y")
            if dax is None or day is None:
                continue
            if abs(_parse_float(dax) - ax) <= tol and abs(_parse_float(day) - ay) <= tol:
                explicit = 0 if (el.get("data-point-id") or "").strip() else 1
                candidates.append((explicit, el))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _find_label_near_point(self, ax: float, ay: float) -> ET.Element | None:
        if self._svg_root is None:
            return None
        best = None
        best_d = None
        for el in self._svg_root.iter():
            if not self._is_point_label_candidate(el):
                continue
            if el.get("data-anchor-x") is not None and el.get("data-anchor-y") is not None:
                continue
            x, y = self._label_position(el)
            dx = x - ax
            dy = y - ay
            dist = math.hypot(dx, dy)
            font_size = self._label_font_size(el, 12.0)
            limit = max(20.0, font_size * 2.0)
            if dist > limit:
                continue
            if best_d is None or dist < best_d:
                best = el
                best_d = dist
        return best

    def _attach_label_to_anchor(self, el: ET.Element, ax: float, ay: float) -> None:
        text = ""
        tag = _strip_ns(el.tag)
        if tag == "text":
            text = (el.text or "").strip()
        else:
            text = (el.get("data-text") or "").strip()
        if not text:
            return
        x, y = self._label_position(el)
        font_size = self._label_font_size(el, 12.0)
        latex = tag == "path" and el.get("data-text") is not None
        dir_s, offset = self._infer_label_dir_offset(ax, ay, x, y, text, font_size, latex)
        el.set("data-anchor-x", _format_num(ax))
        el.set("data-anchor-y", _format_num(ay))
        el.set("data-dir", dir_s)
        el.set("data-offset", _format_num(offset))
        nx, ny = _label_position_from_anchor(ax, ay, text, dir_s, offset, font_size, latex)
        self._set_label_position(el, nx, ny)

    def _on_point_field_change(self, *_args) -> None:
        if self._suspend_point_updates or not self._point_editor_enabled:
            return
        if self._selected_point_el is None or self._svg_root is None:
            return
        raw_offset = self._point_label_offset_var.get().strip()
        if not raw_offset:
            raw_offset = self._global_label_offset_var.get().strip()
        try:
            offset = float(raw_offset)
        except Exception:
            return
        if offset < 0:
            return
        raw_dir = self._point_label_dir_var.get().strip()
        dir_input = _normalize_dir_input(raw_dir)
        dir_s = dir_input
        if not dir_s:
            if self._selected_label_el is not None:
                dir_s = (self._selected_label_el.get("data-dir") or "").strip().upper()
            if not dir_s:
                return
        if not _is_valid_dir(dir_s):
            return
        if dir_input and dir_input != raw_dir:
            self._suspend_point_updates = True
            self._point_label_dir_var.set(dir_input)
            self._suspend_point_updates = False
        text = self._point_label_text_var.get().strip()
        enabled = bool(self._point_label_enabled_var.get())
        label_bg_mode = self._point_label_bg_mode_selected()

        ax = _parse_float(_get_attr(self._selected_point_el, "cx"))
        ay = _parse_float(_get_attr(self._selected_point_el, "cy"))
        self._selected_anchor = (ax, ay)

        if not enabled:
            if self._selected_label_el is not None:
                self._push_history()
                self._remove_label_background(self._selected_label_el)
                parent = self._parent_of(self._selected_label_el)
                if parent is not None:
                    try:
                        parent.remove(self._selected_label_el)
                    except Exception:
                        pass
                self._selected_label_el = None
                self._render_svg()
            return

        if not text and self._selected_label_el is None:
            return
        if not text and self._selected_label_el is not None:
            self._push_history()
            self._remove_label_background(self._selected_label_el)
            parent = self._parent_of(self._selected_label_el)
            if parent is not None:
                try:
                    parent.remove(self._selected_label_el)
                except Exception:
                    pass
            self._selected_label_el = None
            self._render_svg()
            return

        font_size = _parse_float(self._global_font_size_var.get().strip(), 15.0)
        if self._selected_label_el is not None:
            font_size = _parse_float(self._selected_label_el.get("data-font-size"), font_size)
        x, y = _label_position_from_anchor(
            ax, ay, text, dir_s, offset, font_size, True
        )

        if self._selected_label_el is None:
            self._push_history()
            new_el = self._create_latex_label(
                text,
                x,
                y,
                font_size,
                "#000000",
                dir_s=dir_s,
                anchor=(ax, ay),
                offset=offset,
            )
            if new_el is None:
                return
            self._sync_point_identity_from_label(self._selected_point_el, new_el, text)
            self._set_label_bg_mode(new_el, label_bg_mode)
            self._svg_root.append(new_el)
            self._selected_label_el = new_el
            self._render_svg()
            return

        self._push_history()
        el = self._selected_label_el
        if _strip_ns(el.tag) == "text":
            if not self._convert_text_element(el, silent=False):
                return
            el = self._find_label_for_anchor(ax, ay)
            if el is None:
                return
            self._selected_label_el = el
        el.set("data-anchor-x", _format_num(ax))
        el.set("data-anchor-y", _format_num(ay))
        el.set("data-dir", dir_s)
        el.set("data-offset", _format_num(offset))
        self._sync_point_identity_from_label(self._selected_point_el, el, text)
        self._set_label_position(el, x, y)
        if _strip_ns(el.tag) == "text":
            el.text = text
        else:
            el.set("data-text", text)
            font_size = _parse_float(el.get("data-font-size"), 12.0)
            self._update_latex_path(el, text, x, y, font_size)
        self._set_label_bg_mode(el, label_bg_mode)
        self._ensure_label_background(el)
        self._render_svg()

    def _is_circle_visible(self, el: ET.Element) -> bool:
        display = (_get_attr(el, "display") or "").strip().lower()
        visibility = (_get_attr(el, "visibility") or "").strip().lower()
        if display == "none" or visibility == "hidden":
            return False
        return True

    def _set_circle_visibility(self, el: ET.Element, visible: bool) -> None:
        if visible:
            _remove_style_attr(el, "display")
            _remove_style_attr(el, "visibility")
        else:
            _force_style_attr(el, "display", "none")

    def _on_point_visibility_change(self, *_args) -> None:
        if self._suspend_point_updates or not self._point_editor_enabled:
            return
        if self._selected_point_el is None:
            return
        visible = bool(self._point_visible_var.get())
        self._push_history()
        self._set_circle_visibility(self._selected_point_el, visible)
        self._render_svg()

    def _on_polygon_field_change(self, *_args) -> None:
        if not self._polygon_editor_enabled:
            return
        if self._selected_polygon_el is None or self._svg_root is None:
            return
        if _strip_ns(self._selected_polygon_el.tag) != "polygon":
            return
        if (self._selected_polygon_el.get(_SHADE_DATA_ENABLED) or "").strip() == "1":
            return
        raw_opacity = self._polygon_shade_opacity_var.get().strip()
        opacity = _parse_float(raw_opacity, 0.15)
        opacity = max(0.0, min(1.0, float(opacity)))
        self._polygon_shade_opacity_var.set(_format_num(opacity))
        self._push_history()
        if self._polygon_shade_var.get():
            _set_attr(self._selected_polygon_el, "fill", "#000000")
            _force_style_attr(self._selected_polygon_el, "fill-opacity", _format_num(opacity))
        else:
            _set_attr(self._selected_polygon_el, "fill", "none")
            _remove_style_attr(self._selected_polygon_el, "fill-opacity")
        self._render_svg()

    def _split_polygon_to_segments(self) -> None:
        if self._selected_polygon_el is None or self._svg_root is None:
            return
        pts_raw = _get_attr(self._selected_polygon_el, "points")
        coords_raw = _parse_points(pts_raw)
        if len(coords_raw) < 6:
            messagebox.showerror("Poligono", "No hay suficientes puntos para separar.")
            return
        coords: list[tuple[float, float]] = []
        for i in range(0, len(coords_raw) - 1, 2):
            coords.append((coords_raw[i], coords_raw[i + 1]))
        if len(coords) >= 2:
            if abs(coords[0][0] - coords[-1][0]) <= 1e-6 and abs(coords[0][1] - coords[-1][1]) <= 1e-6:
                coords.pop()
        if len(coords) < 2:
            messagebox.showerror("Poligono", "No hay suficientes puntos para separar.")
            return
        stroke = self._effective_attr(self._selected_polygon_el, "stroke") or "#000000"
        stroke_w = _parse_float(self._effective_attr(self._selected_polygon_el, "stroke-width"), 1.0)
        dash = self._effective_attr(self._selected_polygon_el, "stroke-dasharray")
        linecap = self._effective_attr(self._selected_polygon_el, "stroke-linecap")
        linejoin = self._effective_attr(self._selected_polygon_el, "stroke-linejoin")
        class_attr = (self._selected_polygon_el.get("class") or "").strip()
        fill = str(self._effective_attr(self._selected_polygon_el, "fill") or "").strip().lower()
        parent = self._parent_of(self._selected_polygon_el) or self._svg_root
        siblings = list(parent)
        insert_at = siblings.index(self._selected_polygon_el) if self._selected_polygon_el in siblings else len(siblings)
        ns = ""
        tag = self._svg_root.tag
        if tag.startswith("{") and "}" in tag:
            ns = tag.split("}", 1)[0][1:]
        self._push_history()
        for i in range(len(coords)):
            x1, y1 = coords[i]
            x2, y2 = coords[(i + 1) % len(coords)]
            if math.hypot(x2 - x1, y2 - y1) <= 1e-9:
                continue
            line = ET.Element(f"{{{ns}}}line") if ns else ET.Element("line")
            line.set("x1", _format_num(x1))
            line.set("y1", _format_num(y1))
            line.set("x2", _format_num(x2))
            line.set("y2", _format_num(y2))
            if class_attr:
                line.set("class", class_attr)
            _set_attr(line, "stroke", stroke)
            _set_attr(line, "stroke-width", _format_num(stroke_w))
            if dash:
                _set_attr(line, "stroke-dasharray", dash)
            if linecap:
                _set_attr(line, "stroke-linecap", linecap)
            if linejoin:
                _set_attr(line, "stroke-linejoin", linejoin)
            parent.insert(insert_at, line)
            insert_at += 1
        if fill and fill not in ("none", "transparent"):
            _set_attr(self._selected_polygon_el, "stroke", "none")
            _remove_style_attr(self._selected_polygon_el, "stroke-width")
            _remove_style_attr(self._selected_polygon_el, "stroke-dasharray")
        else:
            try:
                parent.remove(self._selected_polygon_el)
            except Exception:
                pass
        self._selected_polygon_el = None
        self._selected = None
        self._sync_selected_ui()
        self._render_svg()

    def _on_stroke_dash_change(self, *_args) -> None:
        if self._suspend_stroke_updates or not self._stroke_editor_enabled:
            return
        if self._selected_stroke_el is None or self._svg_root is None:
            return
        if not self._is_stroke_eligible(self._selected_stroke_el):
            return
        enabled = bool(self._stroke_dash_enabled_var.get())
        pattern = self._dash_pattern_for_apply() if enabled else ""
        self._push_history()
        if enabled:
            _force_style_attr(self._selected_stroke_el, "stroke-dasharray", pattern)
        else:
            _remove_style_attr(self._selected_stroke_el, "stroke-dasharray")
        tag = _strip_ns(self._selected_stroke_el.tag)
        if tag == "line":
            self._sync_subsegments_from_parent(self._selected_stroke_el)
        if tag == "circle":
            self._sync_circle_radius_from_circle(self._selected_stroke_el)
        if tag == "line" and self._segment_editor_enabled:
            self._suspend_segment_updates = True
            self._segment_dashed_var.set(enabled)
            self._suspend_segment_updates = False
        if tag == "circle" and self._circle_editor_enabled:
            self._suspend_circle_updates = True
            self._circle_dashed_var.set(enabled)
            self._suspend_circle_updates = False
        self._render_svg()

    def _on_circle_field_change(self, *_args) -> None:
        if self._suspend_circle_updates or not self._circle_editor_enabled:
            return
        if self._selected_circle_el is None or self._svg_root is None:
            return
        if not self._is_editable_circle(self._selected_circle_el):
            return
        self._push_history()
        if self._circle_dashed_var.get():
            pattern = self._dash_pattern_for_apply()
            _force_style_attr(self._selected_circle_el, "stroke-dasharray", pattern)
        else:
            _remove_style_attr(self._selected_circle_el, "stroke-dasharray")
        if self._circle_show_radius_var.get():
            self._selected_circle_el.set("data-radius-show", "1")
        else:
            self._selected_circle_el.attrib.pop("data-radius-show", None)
        self._sync_circle_radius_from_circle(self._selected_circle_el)
        self._suspend_stroke_updates = True
        self._stroke_dash_enabled_var.set(bool(self._circle_dashed_var.get()))
        if self._circle_dashed_var.get():
            self._stroke_dash_var.set(_get_attr(self._selected_circle_el, "stroke-dasharray") or self._stroke_dash_var.get())
        self._suspend_stroke_updates = False
        self._render_svg()

    def _ensure_arrow_marker(self) -> str | None:
        if self._svg_root is None:
            return None
        tag = self._svg_root.tag
        ns = ""
        if tag.startswith("{") and "}" in tag:
            ns = tag.split("}", 1)[0][1:]
        def _ns_tag(name: str) -> str:
            return f"{{{ns}}}{name}" if ns else name

        defs = None
        for child in list(self._svg_root):
            if _strip_ns(child.tag) == "defs":
                defs = child
                break
        if defs is None:
            defs = ET.Element(_ns_tag("defs"))
            self._svg_root.insert(0, defs)

        marker_id = "lg-arrow"
        marker = None
        for el in defs.iter():
            if _strip_ns(el.tag) == "marker" and el.get("id") == marker_id:
                marker = el
                break

        if marker is None:
            marker = ET.Element(_ns_tag("marker"))
            marker.set("id", marker_id)
            defs.append(marker)

        arrow_size = _parse_float(self._global_arrow_size_var.get().strip(), 18.0)
        if arrow_size <= 0:
            arrow_size = 4.0
        stroke_w = _parse_float(self._global_stroke_var.get().strip(), 2.0)
        if stroke_w <= 0:
            stroke_w = 2.0
        marker_size = arrow_size / stroke_w
        if marker_size <= 0:
            marker_size = 1.0
        marker.set("markerWidth", _format_num(marker_size))
        marker.set("markerHeight", _format_num(marker_size))
        ref_x = _ARROW_MARKER_VIEWBOX * (1.0 + _ARROW_RETREAT_FRAC)
        marker.set("refX", _format_num(ref_x))
        marker.set("refY", "5")
        marker.set("orient", "auto-start-reverse")
        marker.set("viewBox", f"0 0 {_format_num(_ARROW_MARKER_VIEWBOX)} {_format_num(_ARROW_MARKER_VIEWBOX)}")
        marker.set("markerUnits", "strokeWidth")

        path = None
        for child in list(marker):
            if _strip_ns(child.tag) == "path":
                path = child
                break
        if path is None:
            path = ET.Element(_ns_tag("path"))
            marker.append(path)
        # TikZ-like stealth arrow head.
        path.set("d", "M 0 0 L 10 5 L 0 10 L 2 5 z")
        path.set("fill", "#000000")
        path.set("stroke", "none")
        self._sanitize_lg_arrow_marker()
        return marker_id

    def _ensure_dim_arrow_marker(self) -> str | None:
        if self._svg_root is None:
            return None
        tag = self._svg_root.tag
        ns = ""
        if tag.startswith("{") and "}" in tag:
            ns = tag.split("}", 1)[0][1:]

        def _ns_tag(name: str) -> str:
            return f"{{{ns}}}{name}" if ns else name

        defs = None
        for child in list(self._svg_root):
            if _strip_ns(child.tag) == "defs":
                defs = child
                break
        if defs is None:
            defs = ET.Element(_ns_tag("defs"))
            self._svg_root.insert(0, defs)

        marker_id = "lg-dim-arrow"
        marker = None
        for el in defs.iter():
            if _strip_ns(el.tag) == "marker" and el.get("id") == marker_id:
                marker = el
                break
        if marker is None:
            marker = ET.Element(_ns_tag("marker"))
            marker.set("id", marker_id)
            defs.append(marker)

        arrow_size_var = getattr(self, "_global_arrow_size_var", None)
        arrow_size_raw = arrow_size_var.get().strip() if arrow_size_var is not None else "4"
        arrow_size = _parse_float(arrow_size_raw, 18.0)
        if arrow_size <= 0:
            arrow_size = 4.0
        stroke_var = getattr(self, "_global_stroke_var", None)
        stroke_raw = stroke_var.get().strip() if stroke_var is not None else "3"
        stroke_w = _parse_float(stroke_raw, 3.0)
        if stroke_w <= 0:
            stroke_w = 3.0
        marker_size = arrow_size / stroke_w
        if marker_size <= 0:
            marker_size = 1.0

        marker.set("markerWidth", _format_num(marker_size))
        marker.set("markerHeight", _format_num(marker_size))
        ref_x = _ARROW_MARKER_VIEWBOX * (1.0 + _ARROW_RETREAT_FRAC)
        marker.set("refX", _format_num(ref_x))
        marker.set("refY", "5")
        marker.set("orient", "auto-start-reverse")
        marker.set("viewBox", f"0 0 {_format_num(_ARROW_MARKER_VIEWBOX)} {_format_num(_ARROW_MARKER_VIEWBOX)}")
        marker.set("markerUnits", "strokeWidth")

        path = None
        for child in list(marker):
            if _strip_ns(child.tag) == "path":
                path = child
                break
        if path is None:
            path = ET.Element(_ns_tag("path"))
            marker.append(path)
        # Dedicated stealth head for segment dimensions.
        path.set("d", "M 0 0 L 10 5 L 0 10 L 3 5 z")
        path.set("fill", "#000000")
        path.set("stroke", "none")
        return marker_id

    def _has_arrow_usage(self) -> bool:
        if self._svg_root is None:
            return False
        for el in self._svg_root.iter():
            if _strip_ns(el.tag) in ("svg", "defs"):
                continue
            marker_start = str(self._effective_attr(el, "marker-start") or "").strip().lower()
            marker_end = str(self._effective_attr(el, "marker-end") or "").strip().lower()
            if marker_start and marker_start != "none":
                return True
            if marker_end and marker_end != "none":
                return True
        return False

    def _sync_arrow_marker_if_used(self) -> None:
        if self._svg_root is None:
            return
        if self._has_arrow_usage():
            self._ensure_arrow_marker()

    def _on_segment_field_change(self, *_args) -> None:
        if self._suspend_segment_updates or not self._segment_editor_enabled:
            return
        self._update_segment_mark_fields()

    def _create_latex_label(
        self,
        text: str,
        x: float,
        y: float,
        font_size: float,
        fill: str,
        *,
        dir_s: str | None = None,
        anchor: tuple[float, float] | None = None,
        offset: float | None = None,
        anchor_frac: tuple[float, float] | None = None,
    ) -> ET.Element | None:
        try:
            configure_mathtext, require_matplotlib = _resolve_latex_support()
            require_matplotlib()
            configure_mathtext()
        except Exception as exc:
            messagebox.showerror("LaTeX", f"No se pudo cargar matplotlib: {exc}")
            return None
        ns = ""
        tag = self._svg_root.tag if self._svg_root is not None else ""
        if tag.startswith("{") and "}" in tag:
            ns = tag.split("}", 1)[0][1:]
        el = ET.Element(f"{{{ns}}}path") if ns else ET.Element("path")
        el.set("fill", fill)
        el.set("stroke", "none")
        el.set("data-text", text)
        el.set("data-x", _format_num(x))
        el.set("data-y", _format_num(y))
        el.set("data-font-size", _format_num(font_size))
        if dir_s:
            el.set("data-dir", dir_s)
        if anchor is not None:
            el.set("data-anchor-x", _format_num(anchor[0]))
            el.set("data-anchor-y", _format_num(anchor[1]))
        if offset is not None:
            el.set("data-offset", _format_num(offset))
        if anchor_frac is not None:
            ax = max(0.0, min(1.0, float(anchor_frac[0])))
            ay = max(0.0, min(1.0, float(anchor_frac[1])))
            el.set("data-anchor-frac", f"{_format_num(ax)},{_format_num(ay)}")
        ok = self._update_latex_path(el, text, x, y, font_size, silent=True)
        return el if ok else None

    def _sync_selected_ui(self) -> None:
        if self._selected is None:
            if self.sel_label is not None:
                self.sel_label.config(text="Seleccion: (ninguna)")
            self.label_text_var.set("")
            self._sync_point_editor(None)
            self._sync_shade_selected_editor(None)
            return
        record = self._selected
        if self.sel_label is not None:
            self.sel_label.config(text=f"Seleccion: {record.tag}")
        if record.kind == "label":
            if record.tag == "text":
                text = (record.el.text or "").strip()
            else:
                text = (record.el.get("data-text") or "").strip()
            self.label_text_var.set(text)
            pos = self._label_position(record.el)
            self._ensure_label_anchor(record.el, pos[0], pos[1])
            offset = _parse_float(record.el.get("data-offset"), 10.0)
            self.label_offset_var.set(_format_num(offset))
        else:
            self.label_text_var.set("")
        self._sync_point_editor(record)
        self._sync_polygon_editor(record)
        if not bool(getattr(self, "_minimal_v1_globales_only", False)):
            self._sync_circle_editor(record)
            self._sync_stroke_editor(record)
        self._sync_segment_editor(record)
        self._sync_curve_editor(record)
        self._sync_angle_editor(record)
        self._sync_shade_selected_editor(record)

    def _sync_point_editor(self, record: _Record | None) -> None:
        if not hasattr(self, "_point_label_chk"):
            return
        label_bg_widget = self.__dict__.get("_point_label_bg_mode_combo")
        if label_bg_widget is None:
            label_bg_widget = self.__dict__.get("_point_label_bg_chk")
        self._suspend_point_updates = True
        self._selected_point_el = None
        self._selected_label_el = None
        self._selected_anchor = None

        point_el, label_el, anchor = self._point_selection_info(record)
        if point_el is None:
            self._point_editor_enabled = False
            self._show_editor_mode(None)
            self._point_label_chk.configure(state="disabled")
            self._point_visible_chk.configure(state="disabled")
            if label_bg_widget is not None:
                label_bg_widget.configure(state="disabled")
            self._point_label_text_entry.configure(state="disabled")
            self._point_label_dir_entry.configure(state="disabled")
            self._point_label_offset_entry.configure(state="disabled")
            self._point_label_enabled_var.set(False)
            self._point_visible_var.set(True)
            self._point_label_text_var.set("")
            self._point_label_dir_var.set("")
            self._point_label_offset_var.set(self._global_label_offset_var.get().strip() or "10")
            self._point_label_bg_var.set(False)
            self._point_label_bg_mode_var.set(_LABEL_BG_MODE_UI_NONE)
            self._suspend_point_updates = False
            return

        self._point_editor_enabled = True
        self._show_editor_mode("point")
        self._selected_point_el = point_el
        self._selected_label_el = label_el
        self._selected_anchor = anchor
        self._point_label_chk.configure(state="normal")
        self._point_visible_chk.configure(state="normal")
        if label_bg_widget is not None:
            label_bg_widget.configure(state="readonly")
        self._point_label_text_entry.configure(state="normal")
        self._point_label_dir_entry.configure(state="normal")
        self._point_label_offset_entry.configure(state="normal")

        self._point_visible_var.set(self._is_circle_visible(point_el))

        if label_el is None:
            self._point_label_enabled_var.set(False)
            self._point_label_text_var.set("")
            self._point_label_dir_var.set("")
            self._point_label_offset_var.set(self._global_label_offset_var.get().strip() or "10")
            self._point_label_bg_var.set(False)
            self._point_label_bg_mode_var.set(_LABEL_BG_MODE_UI_NONE)
            self._suspend_point_updates = False
            return

        text = ""
        if _strip_ns(label_el.tag) == "text":
            text = (label_el.text or "").strip()
        else:
            text = (label_el.get("data-text") or "").strip()
        self._point_label_enabled_var.set(True)
        self._point_label_text_var.set(text)

        offset = _parse_float(label_el.get("data-offset"), 10.0)
        self._point_label_offset_var.set(_format_num(offset))
        dir_s = (label_el.get("data-dir") or "").strip().upper()
        self._point_label_dir_var.set(dir_s or "")
        mode = self._label_bg_mode_for(label_el)
        self._point_label_bg_var.set(mode == _LABEL_BG_MODE_WHITE)
        self._point_label_bg_mode_var.set(_label_bg_mode_to_ui(mode))
        self._suspend_point_updates = False

    def _sync_segment_editor(self, record: _Record | None) -> None:
        if not hasattr(self, "_segment_dashed_chk"):
            return
        self._segment_editor_enabled = False
        segment_el = self._segment_selection_info(record)
        if segment_el is None:
            self._segment_dashed_chk.configure(state="disabled")
            self._segment_arrow_start_chk.configure(state="disabled")
            self._segment_arrow_end_chk.configure(state="disabled")
            if hasattr(self, "_segment_mark_entry"):
                self._segment_mark_entry.configure(state="disabled")
            if hasattr(self, "_segment_mark_style_combo"):
                self._segment_mark_style_combo.configure(state="disabled")
            if hasattr(self, "_segment_mark_rect_fill_chk"):
                self._segment_mark_rect_fill_chk.configure(state="disabled")
            if hasattr(self, "_segment_resize_entry"):
                self._segment_resize_entry.configure(state="disabled")
            if hasattr(self, "_segment_resize_mode_combo"):
                self._segment_resize_mode_combo.configure(state="disabled")
            if hasattr(self, "_segment_endpoint_target_combo"):
                self._segment_endpoint_target_combo.configure(state="disabled")
            if hasattr(self, "_segment_endpoint_label_entry"):
                self._segment_endpoint_label_entry.configure(state="disabled")
            if hasattr(self, "_segment_endpoint_dir_entry"):
                self._segment_endpoint_dir_entry.configure(state="disabled")
            if hasattr(self, "_segment_endpoint_offset_entry"):
                self._segment_endpoint_offset_entry.configure(state="disabled")
            if "_segment_endpoint_bg_mode_combo" in self.__dict__:
                self._segment_endpoint_bg_mode_combo.configure(state="disabled")
            if hasattr(self, "_segment_mid_label_entry"):
                self._segment_mid_label_entry.configure(state="disabled")
            if hasattr(self, "_segment_mid_dir_entry"):
                self._segment_mid_dir_entry.configure(state="disabled")
            if hasattr(self, "_segment_mid_offset_entry"):
                self._segment_mid_offset_entry.configure(state="disabled")
            if "_segment_mid_bg_mode_combo" in self.__dict__:
                self._segment_mid_bg_mode_combo.configure(state="disabled")
            if hasattr(self, "_segment_dim_show_chk"):
                self._segment_dim_show_chk.configure(state="disabled")
            if hasattr(self, "_segment_dim_offset_entry"):
                self._segment_dim_offset_entry.configure(state="disabled")
            if hasattr(self, "_segment_dim_side_combo"):
                self._segment_dim_side_combo.configure(state="disabled")
            if hasattr(self, "_segment_apply_btn"):
                self._segment_apply_btn.configure(state="disabled")
            self._segment_dashed_var.set(False)
            self._segment_arrow_start_var.set(False)
            self._segment_arrow_end_var.set(False)
            self._segment_mark_count_var.set("0")
            self._segment_mark_style_var.set("none")
            self._segment_mark_radius_var.set("3")
            self._segment_mark_rect_w_var.set("8")
            self._segment_mark_rect_h_var.set("4")
            self._segment_mark_rect_fill_var.set(False)
            self._segment_mark_amp_var.set("6")
            self._segment_mark_length_var.set("40")
            self._segment_mark_cycles_var.set("2")
            self._segment_mark_gap_var.set("6")
            self._segment_resize_mode_var.set("ambos")
            self._segment_resize_delta_var.set("0")
            self._segment_endpoint_target_var.set("inicio")
            self._segment_endpoint_label_var.set("")
            self._segment_endpoint_dir_var.set("")
            self._segment_endpoint_offset_var.set("")
            self._segment_endpoint_bg_var.set(False)
            self._segment_endpoint_bg_mode_var.set(_LABEL_BG_MODE_UI_NONE)
            self._segment_mid_label_var.set("")
            self._segment_mid_dir_var.set("")
            self._segment_mid_offset_var.set("")
            self._segment_mid_bg_var.set(False)
            self._segment_mid_bg_mode_var.set(_LABEL_BG_MODE_UI_NONE)
            self._segment_dim_show_var.set(False)
            self._segment_dim_offset_var.set(_format_num(_SEG_DIM_DEFAULT_OFFSET))
            self._segment_dim_side_var.set(_SEG_DIM_SIDE_POS)
            self._update_segment_mark_fields()
            if (
                not self._point_editor_enabled
                and not self._polygon_editor_enabled
                and not self._circle_editor_enabled
                and not self._curve_editor_enabled
            ):
                self._show_editor_mode(None)
            return
        self._show_editor_mode("segment")
        self._segment_dashed_chk.configure(state="normal")
        self._segment_arrow_start_chk.configure(state="normal")
        self._segment_arrow_end_chk.configure(state="normal")
        if hasattr(self, "_segment_mark_entry"):
            self._segment_mark_entry.configure(state="normal")
        if hasattr(self, "_segment_mark_style_combo"):
            self._segment_mark_style_combo.configure(state="readonly")
        if hasattr(self, "_segment_mark_rect_fill_chk"):
            self._segment_mark_rect_fill_chk.configure(state="normal")
        if hasattr(self, "_segment_resize_entry"):
            self._segment_resize_entry.configure(state="normal")
        if hasattr(self, "_segment_resize_mode_combo"):
            self._segment_resize_mode_combo.configure(state="readonly")
        if hasattr(self, "_segment_endpoint_target_combo"):
            self._segment_endpoint_target_combo.configure(state="readonly")
        if hasattr(self, "_segment_endpoint_label_entry"):
            self._segment_endpoint_label_entry.configure(state="normal")
        if hasattr(self, "_segment_endpoint_dir_entry"):
            self._segment_endpoint_dir_entry.configure(state="normal")
        if hasattr(self, "_segment_endpoint_offset_entry"):
            self._segment_endpoint_offset_entry.configure(state="normal")
        if "_segment_endpoint_bg_mode_combo" in self.__dict__:
            self._segment_endpoint_bg_mode_combo.configure(state="readonly")
        if hasattr(self, "_segment_mid_label_entry"):
            self._segment_mid_label_entry.configure(state="normal")
        if hasattr(self, "_segment_mid_dir_entry"):
            self._segment_mid_dir_entry.configure(state="normal")
        if hasattr(self, "_segment_mid_offset_entry"):
            self._segment_mid_offset_entry.configure(state="normal")
        if "_segment_mid_bg_mode_combo" in self.__dict__:
            self._segment_mid_bg_mode_combo.configure(state="readonly")
        if hasattr(self, "_segment_dim_show_chk"):
            self._segment_dim_show_chk.configure(state="normal")
        if hasattr(self, "_segment_dim_offset_entry"):
            self._segment_dim_offset_entry.configure(state="normal")
        if hasattr(self, "_segment_dim_side_combo"):
            self._segment_dim_side_combo.configure(state="readonly")
        if hasattr(self, "_segment_apply_btn"):
            self._segment_apply_btn.configure(state="normal")
        dash = _get_attr(segment_el, "stroke-dasharray")
        self._segment_dashed_var.set(bool(dash))
        self._segment_arrow_start_var.set(bool(_get_attr(segment_el, "marker-start")))
        self._segment_arrow_end_var.set(bool(_get_attr(segment_el, "marker-end")))
        mark_count = segment_el.get("data-mark-count")
        mark_style = segment_el.get("data-mark-style")
        mark_radius = segment_el.get("data-mark-radius") or "3"
        mark_rect_w = segment_el.get("data-mark-rect-w") or "8"
        mark_rect_h = segment_el.get("data-mark-rect-h") or "4"
        mark_rect_fill = segment_el.get("data-mark-rect-fill") == "1"
        mark_amp = segment_el.get("data-mark-amp") or "6"
        mark_length = segment_el.get("data-mark-length") or "40"
        mark_cycles = segment_el.get("data-mark-cycles") or "2"
        mark_gap = segment_el.get("data-mark-gap") or "6"
        if mark_count is None:
            mark_count = str(self._count_segment_marks(segment_el))
        if not mark_style:
            mark_style = "none" if str(mark_count) == "0" else "puntos"
        self._segment_mark_style_var.set(mark_style)
        self._segment_mark_count_var.set(mark_count)
        self._segment_mark_radius_var.set(mark_radius)
        self._segment_mark_rect_w_var.set(mark_rect_w)
        self._segment_mark_rect_h_var.set(mark_rect_h)
        self._segment_mark_rect_fill_var.set(mark_rect_fill)
        self._segment_mark_amp_var.set(mark_amp)
        self._segment_mark_length_var.set(mark_length)
        self._segment_mark_cycles_var.set(mark_cycles)
        self._segment_mark_gap_var.set(mark_gap)
        self._segment_resize_mode_var.set("ambos")
        self._segment_resize_delta_var.set("0")
        label_a = segment_el.get("data-endpoint-label-a") or ""
        label_b = segment_el.get("data-endpoint-label-b") or ""
        dir_a = segment_el.get("data-endpoint-dir-a") or ""
        dir_b = segment_el.get("data-endpoint-dir-b") or ""
        off_a = segment_el.get("data-endpoint-offset-a") or ""
        off_b = segment_el.get("data-endpoint-offset-b") or ""
        bg_a = segment_el.get("data-endpoint-bg-a") or ""
        bg_b = segment_el.get("data-endpoint-bg-b") or ""
        bg_mode_a = (segment_el.get("data-endpoint-bg-mode-a") or "").strip().lower()
        bg_mode_b = (segment_el.get("data-endpoint-bg-mode-b") or "").strip().lower()
        mid_label = segment_el.get("data-mid-label") or ""
        mid_dir = segment_el.get("data-mid-dir") or ""
        mid_off = segment_el.get("data-mid-offset") or ""
        mid_bg = segment_el.get("data-mid-bg") or ""
        mid_bg_mode = (segment_el.get("data-mid-bg-mode") or "").strip().lower()
        if label_a:
            self._segment_endpoint_target_var.set("inicio")
            self._segment_endpoint_label_var.set(label_a)
            self._segment_endpoint_dir_var.set(dir_a)
            self._segment_endpoint_offset_var.set(off_a)
            mode = bg_mode_a if bg_mode_a in (_LABEL_BG_MODE_NONE, _LABEL_BG_MODE_WHITE, _LABEL_BG_MODE_CUT) else (_LABEL_BG_MODE_WHITE if bg_a == "1" else _LABEL_BG_MODE_NONE)
            self._segment_endpoint_bg_var.set(mode == _LABEL_BG_MODE_WHITE)
            self._segment_endpoint_bg_mode_var.set(_label_bg_mode_to_ui(mode))
        elif label_b:
            self._segment_endpoint_target_var.set("fin")
            self._segment_endpoint_label_var.set(label_b)
            self._segment_endpoint_dir_var.set(dir_b)
            self._segment_endpoint_offset_var.set(off_b)
            mode = bg_mode_b if bg_mode_b in (_LABEL_BG_MODE_NONE, _LABEL_BG_MODE_WHITE, _LABEL_BG_MODE_CUT) else (_LABEL_BG_MODE_WHITE if bg_b == "1" else _LABEL_BG_MODE_NONE)
            self._segment_endpoint_bg_var.set(mode == _LABEL_BG_MODE_WHITE)
            self._segment_endpoint_bg_mode_var.set(_label_bg_mode_to_ui(mode))
        else:
            self._segment_endpoint_target_var.set("inicio")
            self._segment_endpoint_label_var.set("")
            self._segment_endpoint_dir_var.set("")
            self._segment_endpoint_offset_var.set("")
            self._segment_endpoint_bg_var.set(False)
            self._segment_endpoint_bg_mode_var.set(_LABEL_BG_MODE_UI_NONE)
        self._segment_mid_label_var.set(mid_label)
        self._segment_mid_dir_var.set(mid_dir)
        self._segment_mid_offset_var.set(mid_off)
        mid_mode = mid_bg_mode if mid_bg_mode in (_LABEL_BG_MODE_NONE, _LABEL_BG_MODE_WHITE, _LABEL_BG_MODE_CUT) else (_LABEL_BG_MODE_WHITE if mid_bg == "1" else _LABEL_BG_MODE_NONE)
        self._segment_mid_bg_var.set(mid_mode == _LABEL_BG_MODE_WHITE)
        self._segment_mid_bg_mode_var.set(_label_bg_mode_to_ui(mid_mode))
        dim_source = self._segment_dimension_owner_line(segment_el)
        if dim_source is None:
            dim_source = segment_el
        show_dim = (dim_source.get(_SEG_DIM_SHOW_ATTR) or "").strip() == "1"
        off_dim_raw = (dim_source.get(_SEG_DIM_OFFSET_ATTR) or "").strip()
        try:
            off_dim_val = float(off_dim_raw) if off_dim_raw else _SEG_DIM_DEFAULT_OFFSET
        except Exception:
            off_dim_val = _SEG_DIM_DEFAULT_OFFSET
        if off_dim_val <= 0:
            off_dim_val = _SEG_DIM_DEFAULT_OFFSET
        side_dim = (dim_source.get(_SEG_DIM_SIDE_ATTR) or "").strip()
        if side_dim not in (_SEG_DIM_SIDE_POS, _SEG_DIM_SIDE_NEG):
            side_dim = _SEG_DIM_SIDE_POS
        self._segment_dim_show_var.set(show_dim)
        self._segment_dim_offset_var.set(_format_num(off_dim_val))
        self._segment_dim_side_var.set(side_dim)
        self._update_segment_mark_fields()
        self._segment_editor_enabled = True

    def _sync_angle_editor(self, record: _Record | None) -> None:
        if not hasattr(self, "_angle_editor_frame"):
            return
        self._angle_editor_enabled = False
        self._selected_angle_root = None
        angle_root = self._angle_root_for_record(record)
        if angle_root is None:
            self._angle_arc_chk.configure(state="disabled")
            if hasattr(self, "_angle_arrow_start_chk"):
                self._angle_arrow_start_chk.configure(state="disabled")
            if hasattr(self, "_angle_arrow_end_chk"):
                self._angle_arrow_end_chk.configure(state="disabled")
            self._angle_double_chk.configure(state="disabled")
            self._angle_sector_chk.configure(state="disabled")
            self._angle_point_chk.configure(state="disabled")
            self._angle_s_chk.configure(state="disabled")
            self._angle_rect_chk.configure(state="disabled")
            self._angle_rect_fill_chk.configure(state="disabled")
            self._angle_label_chk.configure(state="disabled")
            if "_angle_label_bg_mode_combo" in self.__dict__:
                self._angle_label_bg_mode_combo.configure(state="disabled")
            if hasattr(self, "_angle_obtuse_chk"):
                self._angle_obtuse_chk.configure(state="disabled")
            if hasattr(self, "_angle_reflex_chk"):
                self._angle_reflex_chk.configure(state="disabled")
            if hasattr(self, "_angle_vertical_chk"):
                self._angle_vertical_chk.configure(state="disabled")
            self._angle_show_arc_var.set(True)
            self._angle_arrow_start_var.set(False)
            self._angle_arrow_end_var.set(False)
            self._angle_show_double_var.set(False)
            self._angle_show_sector_var.set(False)
            self._angle_show_point_var.set(False)
            self._angle_show_s_var.set(False)
            self._angle_show_rect_var.set(False)
            self._angle_rect_fill_var.set(False)
            self._angle_label_show_var.set(False)
            self._angle_obtuse_var.set(False)
            if hasattr(self, "_angle_reflex_var"):
                self._angle_reflex_var.set(False)
            self._angle_sector_alpha_var.set("0.15")
            self._angle_label_text_var.set("")
            self._angle_label_offset_var.set("15")
            self._angle_label_angle_var.set("0")
            self._angle_label_bg_var.set(False)
            self._angle_label_bg_mode_var.set(_LABEL_BG_MODE_UI_NONE)
            self._angle_vertical_var.set(False)
            self._angle_radius_var.set("30")
            self._angle_arc_count_var.set("2")
            self._angle_double_delta_var.set("5")
            self._angle_point_lambda_var.set("0.60")
            self._angle_point_r_var.set("2")
            self._angle_s_len_var.set("15")
            self._angle_s_amp_var.set("5")
            self._angle_s_count_var.set("1")
            self._angle_s_gap_var.set("6")
            self._angle_rect_len_var.set("40")
            self._angle_rect_h_var.set("8")
            self._update_angle_fields()
            if (
                not self._point_editor_enabled
                and not self._segment_editor_enabled
                and not self._polygon_editor_enabled
                and not self._circle_editor_enabled
                and not self._curve_editor_enabled
            ):
                self._show_editor_mode(None)
            return
        self._show_editor_mode("angle")
        self._selected_angle_root = angle_root
        self._angle_arc_chk.configure(state="normal")
        if hasattr(self, "_angle_arrow_start_chk"):
            self._angle_arrow_start_chk.configure(state="normal")
        if hasattr(self, "_angle_arrow_end_chk"):
            self._angle_arrow_end_chk.configure(state="normal")
        self._angle_double_chk.configure(state="normal")
        self._angle_sector_chk.configure(state="normal")
        self._angle_point_chk.configure(state="normal")
        self._angle_s_chk.configure(state="normal")
        self._angle_rect_chk.configure(state="normal")
        self._angle_rect_fill_chk.configure(state="normal")
        self._angle_label_chk.configure(state="normal")
        if "_angle_label_bg_mode_combo" in self.__dict__:
            self._angle_label_bg_mode_combo.configure(state="readonly")
        if hasattr(self, "_angle_obtuse_chk"):
            self._angle_obtuse_chk.configure(state="normal")
        if hasattr(self, "_angle_reflex_chk"):
            self._angle_reflex_chk.configure(state="normal")
        if hasattr(self, "_angle_vertical_chk"):
            self._angle_vertical_chk.configure(state="normal")
        show_arc = angle_root.get("data-angle-show-arc")
        show_double = angle_root.get("data-angle-show-double")
        show_reflex = angle_root.get("data-angle-replement")
        if show_reflex is None:
            show_reflex = angle_root.get("data-angle-reflex")
        arrow_start = angle_root.get("data-angle-arrow-start")
        arrow_end = angle_root.get("data-angle-arrow-end")
        show_sector = angle_root.get("data-angle-show-sector")
        show_point = angle_root.get("data-angle-show-point")
        show_s = angle_root.get("data-angle-show-s")
        show_rect = angle_root.get("data-angle-show-rect")
        rect_fill = angle_root.get("data-angle-rect-fill")
        show_label = angle_root.get("data-angle-label-show")
        label_bg = angle_root.get("data-angle-label-bg")
        label_bg_mode = (angle_root.get("data-angle-label-bg-mode") or "").strip().lower()
        if show_arc is None:
            show_arc = "1"
        if arrow_start is None:
            arrow_start = "0"
        if arrow_end is None:
            arrow_end = "0"
        if show_reflex is None:
            show_reflex = "0"
        self._angle_show_arc_var.set(show_arc == "1")
        self._angle_arrow_start_var.set(arrow_start == "1")
        self._angle_arrow_end_var.set(arrow_end == "1")
        if hasattr(self, "_angle_reflex_var"):
            self._angle_reflex_var.set(show_reflex == "1")
        self._angle_show_double_var.set(show_double == "1")
        self._angle_show_sector_var.set(show_sector == "1")
        self._angle_show_point_var.set(show_point == "1")
        self._angle_show_s_var.set(show_s == "1")
        self._angle_show_rect_var.set(show_rect == "1")
        self._angle_rect_fill_var.set(rect_fill == "1")
        self._angle_label_show_var.set(show_label == "1")
        self._angle_label_text_var.set(angle_root.get("data-angle-label") or "")
        self._angle_label_offset_var.set(angle_root.get("data-angle-label-offset") or "15")
        self._angle_label_angle_var.set(angle_root.get("data-angle-label-angle") or "0")
        mode = label_bg_mode if label_bg_mode in (_LABEL_BG_MODE_NONE, _LABEL_BG_MODE_WHITE, _LABEL_BG_MODE_CUT) else (_LABEL_BG_MODE_WHITE if label_bg == "1" else _LABEL_BG_MODE_NONE)
        self._angle_label_bg_var.set(mode == _LABEL_BG_MODE_WHITE)
        self._angle_label_bg_mode_var.set(_label_bg_mode_to_ui(mode))
        self._angle_sector_alpha_var.set(angle_root.get("data-angle-sector-alpha") or "0.15")
        source = (angle_root.get("data-angle-source") or "").strip().lower()
        self._angle_vertical_var.set((angle_root.get("data-angle-vertical") or "") == "1")
        if hasattr(self, "_angle_vertical_chk"):
            if source == "points":
                self._angle_vertical_chk.configure(state="disabled")
                self._angle_vertical_var.set(False)
        self._angle_radius_var.set(angle_root.get("data-angle-ra") or "30")
        self._angle_arc_count_var.set(angle_root.get("data-angle-arc-count") or "2")
        self._angle_double_delta_var.set(angle_root.get("data-angle-double-delta") or "5")
        self._angle_point_lambda_var.set(angle_root.get("data-angle-point-lambda") or "0.60")
        self._angle_point_r_var.set(angle_root.get("data-angle-point-r") or "2")
        self._angle_s_len_var.set(angle_root.get("data-angle-s-len") or "15")
        self._angle_s_amp_var.set(angle_root.get("data-angle-s-amp") or "5")
        self._angle_s_count_var.set(angle_root.get("data-angle-s-count") or "1")
        self._angle_s_gap_var.set(angle_root.get("data-angle-s-gap") or "6")
        self._angle_rect_len_var.set(angle_root.get("data-angle-rect-len") or "40")
        self._angle_rect_h_var.set(angle_root.get("data-angle-rect-h") or "8")
        try:
            v1x = _parse_float(angle_root.get("data-angle-v1x"))
            v1y = _parse_float(angle_root.get("data-angle-v1y"))
            v2x = _parse_float(angle_root.get("data-angle-v2x"))
            v2y = _parse_float(angle_root.get("data-angle-v2y"))
            dot = max(-1.0, min(1.0, v1x * v2x + v1y * v2y))
            ang = math.acos(dot)
            right_eps = 1e-3
            if abs(ang - 0.5 * math.pi) <= right_eps:
                sweep = int(float(angle_root.get("data-angle-sweep") or "0"))
                self._angle_obtuse_var.set(sweep == 1)
            else:
                self._angle_obtuse_var.set(ang > 0.5 * math.pi + 1e-6)
        except Exception:
            self._angle_obtuse_var.set(False)
        self._update_angle_fields()
        self._angle_editor_enabled = True

    def _sync_polygon_editor(self, record: _Record | None) -> None:
        if not hasattr(self, "_polygon_editor_frame"):
            return
        self._polygon_editor_enabled = False
        self._selected_polygon_el = None
        if record is None or _strip_ns(record.el.tag) != "polygon":
            self._polygon_shade_chk.configure(state="disabled")
            self._polygon_shade_opacity_entry.configure(state="disabled")
            if hasattr(self, "_polygon_split_btn"):
                self._polygon_split_btn.configure(state="disabled")
            self._polygon_shade_var.set(False)
            if not (self._polygon_shade_opacity_var.get() or "").strip():
                self._polygon_shade_opacity_var.set("0.15")
            return
        self._show_editor_mode("polygon")
        self._selected_polygon_el = record.el
        shade_diff_enabled = (record.el.get(_SHADE_DATA_ENABLED) or "").strip() == "1"
        if shade_diff_enabled:
            self._polygon_shade_chk.configure(state="disabled")
            self._polygon_shade_opacity_entry.configure(state="disabled")
            if hasattr(self, "_polygon_split_btn"):
                self._polygon_split_btn.configure(state="disabled")
            self._set_transform_status("Poligono con sombreado base-huecos: editar desde Herramientas.")
        else:
            self._polygon_shade_chk.configure(state="normal")
            self._polygon_shade_opacity_entry.configure(state="normal")
            if hasattr(self, "_polygon_split_btn"):
                self._polygon_split_btn.configure(state="normal")
        fill = str(self._effective_attr(record.el, "fill") or "").strip().lower()
        shaded = fill not in ("", "none", "transparent")
        self._polygon_shade_var.set(shaded)
        if shaded:
            opacity = _parse_float(self._effective_attr(record.el, "fill-opacity"), 1.0)
            opacity = max(0.0, min(1.0, float(opacity)))
            self._polygon_shade_opacity_var.set(_format_num(opacity))
        else:
            if not (self._polygon_shade_opacity_var.get() or "").strip():
                self._polygon_shade_opacity_var.set("0.15")
        self._polygon_editor_enabled = not shade_diff_enabled

    def _is_editable_circle(self, el: ET.Element) -> bool:
        if _strip_ns(el.tag) != "circle":
            return False
        if (el.get("data-kind") or "").strip() in ("point", "seg-endpoint"):
            return False
        if (el.get("data-angle-kind") or "").strip() == "point":
            return False
        if el.get("data-angle-id"):
            return False
        if self._is_point_circle(el):
            return False
        return True

    def _sync_circle_editor(self, record: _Record | None) -> None:
        if not hasattr(self, "_circle_editor_frame"):
            return
        self._circle_editor_enabled = False
        self._selected_circle_el = None
        if record is None or not self._is_editable_circle(record.el):
            self._circle_dashed_chk.configure(state="disabled")
            self._circle_radius_chk.configure(state="disabled")
            self._circle_dashed_var.set(False)
            self._circle_show_radius_var.set(False)
            return
        self._show_editor_mode("circle")
        self._selected_circle_el = record.el
        self._circle_dashed_chk.configure(state="normal")
        self._circle_radius_chk.configure(state="normal")
        dash = _get_attr(record.el, "stroke-dasharray")
        self._circle_dashed_var.set(bool(dash))
        show = (record.el.get("data-radius-show") or "").strip() == "1"
        if not show:
            key = self._circle_key(record.el, create=False)
            if key and self._circle_radius_present(key):
                show = True
        self._circle_show_radius_var.set(show)
        self._circle_editor_enabled = True

    def _normalize_dash_pattern(self, raw: str) -> str | None:
        nums = _NUM_RE.findall((raw or "").replace(",", " "))
        parts: list[str] = []
        for n in nums:
            try:
                val = float(n)
            except Exception:
                continue
            if val <= 0:
                continue
            parts.append(_format_num(val))
        if not parts:
            return None
        return ",".join(parts)

    def _dash_pattern_for_apply(self) -> str:
        raw = self._stroke_dash_var.get().strip()
        pattern = self._normalize_dash_pattern(raw)
        if not pattern:
            pattern = "4,3"
            if self._stroke_dash_var.get().strip() != pattern:
                self._stroke_dash_var.set(pattern)
        return pattern

    def _curve_dash_pattern_for_apply(self) -> str:
        raw = self._curve_dash_var.get().strip()
        pattern = self._normalize_dash_pattern(raw)
        if not pattern:
            pattern = "4,3"
            if self._curve_dash_var.get().strip() != pattern:
                self._curve_dash_var.set(pattern)
        return pattern

    def _curve_is_closed(self, el: ET.Element) -> bool:
        tag = _strip_ns(el.tag)
        if tag in ("circle", "ellipse"):
            return True
        if tag != "path":
            return False
        d = _get_attr(el, "d") or ""
        for pts, closed in _parse_svg_path(d):
            if len(pts) >= 2 and closed:
                return True
        return False

    def _update_curve_field_states(self) -> None:
        if not hasattr(self, "_curve_apply_btn"):
            return
        enabled = bool(getattr(self, "_curve_editor_enabled", False))
        curve_el = getattr(self, "_selected_curve_el", None)
        is_closed = bool(curve_el is not None and self._curve_is_closed(curve_el))
        is_circle = bool(curve_el is not None and _strip_ns(curve_el.tag) == "circle")
        entry_state = "normal" if enabled else "disabled"
        self._curve_stroke_width_entry.configure(state=entry_state)
        self._curve_stroke_color_entry.configure(state=entry_state)
        self._curve_dashed_chk.configure(state=entry_state)
        if enabled and bool(self._curve_dashed_var.get()):
            self._curve_dash_entry.configure(state="normal")
        else:
            self._curve_dash_entry.configure(state="disabled")
        if enabled and not is_closed:
            self._curve_arrow_start_chk.configure(state="normal")
            self._curve_arrow_end_chk.configure(state="normal")
        else:
            self._curve_arrow_start_chk.configure(state="disabled")
            self._curve_arrow_end_chk.configure(state="disabled")
            self._curve_arrow_start_var.set(False)
            self._curve_arrow_end_var.set(False)
        if enabled and is_circle:
            self._curve_radius_chk.configure(state="normal")
        else:
            self._curve_radius_chk.configure(state="disabled")
            if not is_circle:
                self._curve_show_radius_var.set(False)
        self._curve_apply_btn.configure(state=entry_state)

    def _on_curve_field_change(self, *_args) -> None:
        self._update_curve_field_states()

    def _apply_curve_style_values(
        self,
        el: ET.Element,
        *,
        stroke_width: float,
        stroke_color: str,
        dashed: bool,
        dash_pattern: str | None,
        arrow_start: bool,
        arrow_end: bool,
        is_closed: bool,
        marker_id: str | None,
    ) -> None:
        _force_style_attr(el, "stroke-width", _format_num(stroke_width))
        _force_style_attr(el, "stroke", stroke_color)
        if dashed and dash_pattern:
            _force_style_attr(el, "stroke-dasharray", dash_pattern)
        else:
            _remove_style_attr(el, "stroke-dasharray")
        if is_closed:
            _remove_style_attr(el, "marker-start")
            _remove_style_attr(el, "marker-end")
            return
        if arrow_start and marker_id:
            _force_style_attr(el, "marker-start", f"url(#{marker_id})")
        else:
            _remove_style_attr(el, "marker-start")
        if arrow_end and marker_id:
            _force_style_attr(el, "marker-end", f"url(#{marker_id})")
        else:
            _remove_style_attr(el, "marker-end")

    def _sync_curve_subsegments_from_parent(
        self,
        parent_el: ET.Element,
        *,
        stroke_width: float,
        stroke_color: str,
        dashed: bool,
        dash_pattern: str | None,
        arrow_start: bool,
        arrow_end: bool,
    ) -> None:
        if self._svg_root is None:
            return
        parent_id = (parent_el.get("id") or "").strip()
        if not parent_id:
            parent_id = self._ensure_element_id(parent_el, prefix="shape")
        is_parent_closed = self._curve_is_closed(parent_el)
        marker_id = None
        if not is_parent_closed and (arrow_start or arrow_end):
            marker_id = self._ensure_arrow_marker()
        for el in self._svg_root.iter():
            if _strip_ns(el.tag) != "path":
                continue
            if (el.get("data-kind") or "").strip() != "subsegment":
                continue
            if (el.get("data-parent-id") or "").strip() != parent_id:
                continue
            if el.get("data-subsegment-override") == "1":
                continue
            child_closed = self._curve_is_closed(el)
            self._apply_curve_style_values(
                el,
                stroke_width=stroke_width,
                stroke_color=stroke_color,
                dashed=dashed,
                dash_pattern=dash_pattern,
                arrow_start=arrow_start,
                arrow_end=arrow_end,
                is_closed=child_closed,
                marker_id=marker_id,
            )

    def _apply_curve_editor_changes(self) -> None:
        if not self._curve_editor_enabled or self._svg_root is None:
            return
        target = self._curve_selection_info(self._selected)
        if target is None:
            messagebox.showerror("Curva", "Selecciona una curva o subcurva valida.")
            return
        try:
            stroke_width = float((self._curve_stroke_width_var.get() or "").strip())
        except Exception as exc:
            messagebox.showerror("Curva", "Grosor invalido.")
            return
        if stroke_width <= 0:
            messagebox.showerror("Curva", "El grosor debe ser mayor a 0.")
            return
        stroke_color = (self._curve_stroke_color_var.get() or "").strip()
        if not stroke_color:
            messagebox.showerror("Curva", "Color de trazo invalido.")
            return
        dashed = bool(self._curve_dashed_var.get())
        dash_pattern: str | None = None
        if dashed:
            dash_pattern = self._normalize_dash_pattern(self._curve_dash_var.get().strip())
            if not dash_pattern:
                messagebox.showerror("Curva", "Patron discontinuo invalido.")
                return
            self._curve_dash_var.set(dash_pattern)
        is_closed = self._curve_is_closed(target)
        arrow_start = bool(self._curve_arrow_start_var.get()) and not is_closed
        arrow_end = bool(self._curve_arrow_end_var.get()) and not is_closed
        self._curve_stroke_width_var.set(_format_num(stroke_width))
        self._curve_stroke_color_var.set(stroke_color)

        self._push_history()
        marker_id = None
        if not is_closed and (arrow_start or arrow_end):
            marker_id = self._ensure_arrow_marker()
        self._apply_curve_style_values(
            target,
            stroke_width=stroke_width,
            stroke_color=stroke_color,
            dashed=dashed,
            dash_pattern=dash_pattern,
            arrow_start=arrow_start,
            arrow_end=arrow_end,
            is_closed=is_closed,
            marker_id=marker_id,
        )

        is_subsegment = (target.get("data-kind") or "").strip() == "subsegment"
        if is_subsegment:
            target.set("data-subsegment-override", "1")
        else:
            self._sync_curve_subsegments_from_parent(
                target,
                stroke_width=stroke_width,
                stroke_color=stroke_color,
                dashed=dashed,
                dash_pattern=dash_pattern,
                arrow_start=arrow_start,
                arrow_end=arrow_end,
            )

        if _strip_ns(target.tag) == "circle":
            if self._curve_show_radius_var.get():
                target.set("data-radius-show", "1")
            else:
                target.attrib.pop("data-radius-show", None)
            self._sync_circle_radius_from_circle(target)

        self._set_transform_status("Cambios de curva aplicados.")
        self._render_svg()

    def _sync_curve_editor(self, record: _Record | None) -> None:
        if "_curve_editor_frame" not in self.__dict__ and "_show_editor_mode" not in self.__dict__:
            return
        self._curve_editor_enabled = False
        self._selected_curve_el = None
        curve_el = self._curve_selection_info(record)
        if curve_el is None:
            self._curve_stroke_width_var.set(self._global_stroke_var.get().strip() or "3")
            self._curve_stroke_color_var.set("#000000")
            self._curve_dashed_var.set(False)
            if not self._curve_dash_var.get().strip():
                self._curve_dash_var.set("4,3")
            self._curve_arrow_start_var.set(False)
            self._curve_arrow_end_var.set(False)
            self._curve_show_radius_var.set(False)
            self._update_curve_field_states()
            if (
                not self._point_editor_enabled
                and not self._segment_editor_enabled
                and not self._polygon_editor_enabled
                and not self._circle_editor_enabled
                and not self._angle_editor_enabled
            ):
                self._show_editor_mode(None)
            return
        self._curve_editor_enabled = True
        self._selected_curve_el = curve_el
        self._show_editor_mode("curve")
        stroke_w = _parse_float(self._effective_attr(curve_el, "stroke-width"), _parse_float(self._global_stroke_var.get(), 3.0))
        self._curve_stroke_width_var.set(_format_num(stroke_w))
        stroke = (self._effective_attr(curve_el, "stroke") or "").strip()
        self._curve_stroke_color_var.set(stroke or "#000000")
        dash = _get_attr(curve_el, "stroke-dasharray")
        if dash:
            self._curve_dashed_var.set(True)
            self._curve_dash_var.set(self._normalize_dash_pattern(dash) or dash)
        else:
            self._curve_dashed_var.set(False)
            if not self._curve_dash_var.get().strip():
                self._curve_dash_var.set("4,3")
        is_closed = self._curve_is_closed(curve_el)
        self._curve_arrow_start_var.set(False if is_closed else bool(_get_attr(curve_el, "marker-start")))
        self._curve_arrow_end_var.set(False if is_closed else bool(_get_attr(curve_el, "marker-end")))
        if _strip_ns(curve_el.tag) == "circle":
            show = (curve_el.get("data-radius-show") or "").strip() == "1"
            if not show:
                key = self._circle_key(curve_el, create=False)
                if key and self._circle_radius_present(key):
                    show = True
            self._curve_show_radius_var.set(show)
        else:
            self._curve_show_radius_var.set(False)
        self._update_curve_field_states()

    def _is_stroke_eligible(self, el: ET.Element) -> bool:
        tag = _strip_ns(el.tag)
        if tag not in ("line", "polyline", "polygon", "path", "circle", "ellipse", "rect"):
            return False
        if tag == "path" and el.get("data-text") is not None:
            return False
        if tag == "circle" and not self._is_editable_circle(el):
            return False
        kind = (el.get("data-kind") or "").strip()
        if kind in (
            "seg-mark",
            "seg-endpoint",
            "seg-endpoint-label",
            "seg-mid-label",
            _SEG_DIM_LINE_DATA_KIND,
            _SEG_DIM_TICK_DATA_KIND,
            _SEG_DIM_EXT_DATA_KIND,
            _SEG_DIM_LABEL_DATA_KIND,
            "label-bg",
            "circle-radius",
            _CURVE_RADIUS_DATA_KIND,
        ):
            return False
        if tag == "rect" and kind in ("background", "label-bg"):
            return False
        return True

    def _sync_stroke_editor(self, record: _Record | None) -> None:
        if not hasattr(self, "_stroke_editor_frame"):
            return
        self._stroke_editor_enabled = False
        self._selected_stroke_el = None
        if record is None or not self._is_stroke_eligible(record.el):
            self._stroke_dash_chk.configure(state="disabled")
            self._stroke_dash_entry.configure(state="disabled")
            self._suspend_stroke_updates = True
            self._stroke_dash_enabled_var.set(False)
            if not self._stroke_dash_var.get().strip():
                self._stroke_dash_var.set("4,3")
            self._suspend_stroke_updates = False
            return
        self._selected_stroke_el = record.el
        self._stroke_dash_chk.configure(state="normal")
        self._stroke_dash_entry.configure(state="normal")
        dash = _get_attr(record.el, "stroke-dasharray")
        self._suspend_stroke_updates = True
        if dash:
            pattern = self._normalize_dash_pattern(dash) or dash
            self._stroke_dash_enabled_var.set(True)
            self._stroke_dash_var.set(pattern)
        else:
            self._stroke_dash_enabled_var.set(False)
            if not self._stroke_dash_var.get().strip():
                self._stroke_dash_var.set("4,3")
        self._suspend_stroke_updates = False
        self._stroke_editor_enabled = True

    def _angle_root_for_record(self, record: _Record | None) -> ET.Element | None:
        if record is None or self._svg_root is None:
            return None
        el = record.el
        angle_id = el.get("data-angle-id")
        if angle_id:
            root = self._angle_root_for_id(angle_id)
            return root or el
        if _strip_ns(el.tag) == "path":
            if self._ensure_angle_metadata(el):
                return el
        return None

    def _angle_root_for_id(self, angle_id: str) -> ET.Element | None:
        if self._svg_root is None:
            return None
        for el in self._svg_root.iter():
            if el.get("data-angle-id") != angle_id:
                continue
            if el.get("data-angle-root") == "1":
                return el
        return None

    def _ensure_angle_metadata(self, el: ET.Element) -> bool:
        if self._svg_root is None:
            return False
        if el.get("data-angle-id"):
            return True
        if _strip_ns(el.tag) != "path":
            return False
        d = _get_attr(el, "d") or ""
        arc = _extract_arc_command(d)
        if arc is None:
            return False
        sx, sy, ra, large_arc, sweep, tx, ty = arc
        center = _arc_center_from_endpoints(sx, sy, tx, ty, ra, sweep, large_arc)
        if center is None:
            return False
        vx, vy = center
        v1x, v1y = (sx - vx) / ra, (sy - vy) / ra
        v2x, v2y = (tx - vx) / ra, (ty - vy) / ra
        angle_id = self._next_angle_id()
        el.set("data-angle-id", angle_id)
        el.set("data-angle-root", "1")
        el.set("data-angle-vx", _format_num(vx))
        el.set("data-angle-vy", _format_num(vy))
        el.set("data-angle-v1x", _format_num(v1x))
        el.set("data-angle-v1y", _format_num(v1y))
        el.set("data-angle-v2x", _format_num(v2x))
        el.set("data-angle-v2y", _format_num(v2y))
        el.set("data-angle-ra", _format_num(ra))
        el.set("data-angle-arc-count", "1")
        el.set("data-angle-sweep", str(sweep))
        el.set("data-angle-replement", "1" if int(large_arc) == 1 else "0")
        el.set("data-angle-source", "unknown")
        el.set("data-angle-vertical", "0")
        el.set("data-angle-show-arc", "1")
        el.set("data-angle-arrow-start", "0")
        el.set("data-angle-arrow-end", "0")
        el.set("data-angle-show-double", "0")
        el.set("data-angle-show-sector", "0")
        el.set("data-angle-show-point", "0")
        el.set("data-angle-show-s", "0")
        el.set("data-angle-show-rect", "0")
        el.set("data-angle-rect-fill", "0")
        el.set("data-angle-label-show", "0")
        el.set("data-angle-label", "")
        el.set("data-angle-label-offset", "15")
        el.set("data-angle-label-angle", "0")
        el.set("data-angle-label-bg", "0")
        el.set("data-angle-label-bg-mode", _LABEL_BG_MODE_NONE)
        el.set("data-angle-double-delta", "5")
        el.set("data-angle-point-lambda", "0.60")
        el.set("data-angle-point-r", "2")
        el.set("data-angle-s-len", "15")
        el.set("data-angle-s-amp", "5")
        el.set("data-angle-s-count", "1")
        el.set("data-angle-s-gap", "6")
        el.set("data-angle-rect-len", "40")
        el.set("data-angle-rect-h", "8")
        el.set("data-angle-sector-alpha", "0.15")
        return True

    def _next_angle_id(self) -> str:
        if self._svg_root is None:
            return "ang-1"
        used: set[str] = set()
        for el in self._svg_root.iter():
            aid = el.get("data-angle-id")
            if aid:
                used.add(aid)
        idx = 1
        while f"ang-{idx}" in used:
            idx += 1
        return f"ang-{idx}"

    def _update_angle_fields(self) -> None:
        if not hasattr(self, "_angle_base_frame"):
            return
        show_rect = bool(self._angle_show_rect_var.get())
        if show_rect:
            self._angle_arc_chk.configure(state="disabled")
            if hasattr(self, "_angle_arrow_start_chk"):
                self._angle_arrow_start_chk.configure(state="disabled")
            if hasattr(self, "_angle_arrow_end_chk"):
                self._angle_arrow_end_chk.configure(state="disabled")
            self._angle_double_chk.configure(state="disabled")
            self._angle_sector_chk.configure(state="disabled")
            self._angle_point_chk.configure(state="disabled")
            self._angle_s_chk.configure(state="disabled")
        else:
            self._angle_arc_chk.configure(state="normal")
            if hasattr(self, "_angle_arrow_start_chk"):
                self._angle_arrow_start_chk.configure(state="normal")
            if hasattr(self, "_angle_arrow_end_chk"):
                self._angle_arrow_end_chk.configure(state="normal")
            self._angle_double_chk.configure(state="normal")
            self._angle_sector_chk.configure(state="normal")
            self._angle_point_chk.configure(state="normal")
            self._angle_s_chk.configure(state="normal")
        self._angle_label_frame.grid_remove()
        if self._angle_label_show_var.get():
            self._angle_label_frame.grid(row=1, column=0, columnspan=8, sticky="w")
        self._angle_base_frame.grid_remove()
        self._angle_double_frame.grid_remove()
        self._angle_point_frame.grid_remove()
        self._angle_s_frame.grid_remove()
        self._angle_rect_frame.grid_remove()
        if show_rect:
            self._angle_rect_frame.grid(row=2, column=0, columnspan=8, sticky="w")
            return
        self._angle_base_frame.grid(row=2, column=0, columnspan=8, sticky="w")
        if self._angle_show_double_var.get():
            self._angle_double_frame.grid(row=3, column=0, columnspan=8, sticky="w")
        if self._angle_show_point_var.get():
            self._angle_point_frame.grid(row=4, column=0, columnspan=8, sticky="w")
        if self._angle_show_s_var.get():
            self._angle_s_frame.grid(row=5, column=0, columnspan=8, sticky="w")

    def _on_angle_toggle_change(self, *_args) -> None:
        if not self._angle_editor_enabled:
            return
        self._update_angle_fields()
        self._on_angle_field_commit()

    def _on_angle_field_commit(self, _event=None) -> None:
        if not self._angle_editor_enabled or self._svg_root is None:
            return
        if self._selected_angle_root is None:
            return
        self._apply_angle_obtuse_to_root(self._selected_angle_root)
        self._push_history()
        root = self._selected_angle_root
        new_root = self._rebuild_angle_group(root)
        self._render_svg()
        if new_root is not None:
            for record in self._records:
                if record.el is new_root:
                    self._select_record(record)
                    break

    def _angle_geometry_from_root(
        self, root_el: ET.Element
    ) -> tuple[float, float, float, float, float, float, int] | None:
        try:
            vx = _parse_float(root_el.get("data-angle-vx"))
            vy = _parse_float(root_el.get("data-angle-vy"))
            v1x = _parse_float(root_el.get("data-angle-v1x"))
            v1y = _parse_float(root_el.get("data-angle-v1y"))
            v2x = _parse_float(root_el.get("data-angle-v2x"))
            v2y = _parse_float(root_el.get("data-angle-v2y"))
            sweep = int(float(root_el.get("data-angle-sweep", "0")))
        except Exception:
            return None
        return (vx, vy, v1x, v1y, v2x, v2y, sweep)

    def _angle_settings_from_root(self, root_el: ET.Element) -> dict[str, object]:
        def bool_attr(name: str, default: bool) -> bool:
            raw = root_el.get(name)
            if raw is None:
                return default
            val = str(raw).strip().lower()
            return val in ("1", "true", "si", "yes", "on")

        def num_attr(name: str, default: float) -> float:
            return _parse_float(root_el.get(name), default)

        label_bg_mode_raw = (root_el.get("data-angle-label-bg-mode") or "").strip().lower()
        label_bg_mode = (
            label_bg_mode_raw
            if label_bg_mode_raw in (_LABEL_BG_MODE_NONE, _LABEL_BG_MODE_WHITE, _LABEL_BG_MODE_CUT)
            else (_LABEL_BG_MODE_WHITE if bool_attr("data-angle-label-bg", False) else _LABEL_BG_MODE_NONE)
        )

        return {
            "ra": num_attr("data-angle-ra", 30.0),
            "arc_count": int(num_attr("data-angle-arc-count", 2.0)),
            "show_arc": bool_attr("data-angle-show-arc", True),
            "reflex": bool_attr(
                "data-angle-replement",
                bool_attr("data-angle-reflex", False),
            ),
            "arrow_start": bool_attr("data-angle-arrow-start", False),
            "arrow_end": bool_attr("data-angle-arrow-end", False),
            "show_double": bool_attr("data-angle-show-double", False),
            "show_sector": bool_attr("data-angle-show-sector", False),
            "show_point": bool_attr("data-angle-show-point", False),
            "show_s": bool_attr("data-angle-show-s", False),
            "show_rect": bool_attr("data-angle-show-rect", False),
            "rect_fill": bool_attr("data-angle-rect-fill", False),
            "show_label": bool_attr("data-angle-label-show", False),
            "sector_alpha": num_attr("data-angle-sector-alpha", 0.15),
            "label_text": (root_el.get("data-angle-label") or ""),
            "label_offset": num_attr("data-angle-label-offset", 15.0),
            "label_angle": num_attr("data-angle-label-angle", 0.0),
            "label_bg": bool_attr("data-angle-label-bg", False),
            "label_bg_mode": label_bg_mode,
            "vertical": bool_attr("data-angle-vertical", False),
            "source": (root_el.get("data-angle-source") or ""),
            "delta": num_attr("data-angle-double-delta", 5.0),
            "lam": num_attr("data-angle-point-lambda", 0.60),
            "point_r": num_attr("data-angle-point-r", 2.0),
            "s_len": num_attr("data-angle-s-len", 15.0),
            "s_amp": num_attr("data-angle-s-amp", 5.0),
            "s_count": int(num_attr("data-angle-s-count", 1.0)),
            "s_gap": num_attr("data-angle-s-gap", 6.0),
            "rect_len": num_attr("data-angle-rect-len", 40.0),
            "rect_h": num_attr("data-angle-rect-h", 8.0),
        }

    def _adjust_angle_vectors_for_obtuse(
        self, v1x: float, v1y: float, v2x: float, v2y: float, want_obtuse: bool
    ) -> tuple[float, float, float, float, int]:
        dot = max(-1.0, min(1.0, v1x * v2x + v1y * v2y))
        ang = math.acos(dot)
        eps = 1e-6
        right_eps = 1e-3
        if abs(ang - 0.5 * math.pi) <= right_eps:
            cross = v1x * v2y - v1y * v2x
            # En angulos rectos, "obtuso" alterna el lado del arco (izq/der).
            if want_obtuse and cross < 0:
                v2x, v2y = -v2x, -v2y
            elif (not want_obtuse) and cross > 0:
                v2x, v2y = -v2x, -v2y
            cross = v1x * v2y - v1y * v2x
            sweep = 1 if cross > 0 else 0
            return v1x, v1y, v2x, v2y, sweep
        if want_obtuse and ang < (0.5 * math.pi - eps):
            v2x, v2y = -v2x, -v2y
        elif not want_obtuse and ang > (0.5 * math.pi + eps):
            v2x, v2y = -v2x, -v2y
        cross = v1x * v2y - v1y * v2x
        sweep = 1 if cross > 0 else 0
        return v1x, v1y, v2x, v2y, sweep

    def _apply_angle_obtuse_to_root(self, root_el: ET.Element) -> None:
        if not hasattr(self, "_angle_obtuse_var"):
            return
        try:
            v1x = _parse_float(root_el.get("data-angle-v1x"))
            v1y = _parse_float(root_el.get("data-angle-v1y"))
            v2x = _parse_float(root_el.get("data-angle-v2x"))
            v2y = _parse_float(root_el.get("data-angle-v2y"))
        except Exception:
            return
        want = bool(self._angle_obtuse_var.get())
        v1x, v1y, v2x, v2y, sweep = self._adjust_angle_vectors_for_obtuse(
            v1x, v1y, v2x, v2y, want
        )
        root_el.set("data-angle-v1x", _format_num(v1x))
        root_el.set("data-angle-v1y", _format_num(v1y))
        root_el.set("data-angle-v2x", _format_num(v2x))
        root_el.set("data-angle-v2y", _format_num(v2y))
        root_el.set("data-angle-sweep", str(sweep))

    def _on_angle_obtuse_change(self, *_args) -> None:
        if not self._angle_editor_enabled:
            return
        self._on_angle_field_commit()

    def _on_angle_vertical_change(self, *_args) -> None:
        if not self._angle_editor_enabled:
            return
        self._on_angle_field_commit()

    def _rebuild_angle_group(
        self, root_el: ET.Element, *, settings: dict[str, object] | None = None
    ) -> ET.Element | None:
        if self._svg_root is None:
            return None
        geom = self._angle_geometry_from_root(root_el)
        if geom is None:
            return None
        vx, vy, v1x, v1y, v2x, v2y, sweep = geom
        orig_v1x, orig_v1y = v1x, v1y
        orig_v2x, orig_v2y = v2x, v2y
        angle_id = root_el.get("data-angle-id") or self._next_angle_id()
        root_id = root_el.get("id")
        parent = self._parent_of(root_el) or self._svg_root
        siblings = list(parent)
        insert_at = siblings.index(root_el) if root_el in siblings else len(siblings)
        to_remove = [el for el in self._svg_root.iter() if el.get("data-angle-id") == angle_id]
        for el in to_remove:
            parent_el = self._parent_of(el)
            if parent_el is None:
                continue
            try:
                parent_el.remove(el)
            except Exception:
                pass

        stroke = _get_attr(root_el, "stroke") or "#000000"
        stroke_w = _get_attr(root_el, "stroke-width") or "2"
        if str(stroke).strip().lower() in ("", "none", "transparent"):
            stroke = "#000000"
        if settings is None:
            ra = _parse_float(self._angle_radius_var.get().strip(), 30.0)
            arc_count_raw = self._angle_arc_count_var.get().strip()
            show_arc = bool(self._angle_show_arc_var.get())
            reflex = bool(self._angle_reflex_var.get()) if hasattr(self, "_angle_reflex_var") else False
            arrow_start = bool(self._angle_arrow_start_var.get())
            arrow_end = bool(self._angle_arrow_end_var.get())
            show_double = bool(self._angle_show_double_var.get())
            show_sector = bool(self._angle_show_sector_var.get())
            show_point = bool(self._angle_show_point_var.get())
            show_s = bool(self._angle_show_s_var.get())
            show_rect = bool(self._angle_show_rect_var.get())
            rect_fill = bool(self._angle_rect_fill_var.get())
            sector_alpha = _parse_float(self._angle_sector_alpha_var.get().strip(), 0.15)
            show_label = bool(self._angle_label_show_var.get())
            label_bg_mode = self._angle_label_bg_mode_selected()
            label_text = self._angle_label_text_var.get().strip()
            label_offset = _parse_float(self._angle_label_offset_var.get().strip(), 15.0)
            label_angle = _parse_float(self._angle_label_angle_var.get().strip(), 0.0)
            vertical = bool(self._angle_vertical_var.get())
            source = (root_el.get("data-angle-source") or "")
            delta = _parse_float(self._angle_double_delta_var.get().strip(), 5.0)
            lam = _parse_float(self._angle_point_lambda_var.get().strip(), 0.60)
            point_r = _parse_float(self._angle_point_r_var.get().strip(), 2.0)
            s_len = _parse_float(self._angle_s_len_var.get().strip(), 15.0)
            s_amp = _parse_float(self._angle_s_amp_var.get().strip(), 5.0)
            s_count_raw = self._angle_s_count_var.get().strip()
            s_gap = _parse_float(self._angle_s_gap_var.get().strip(), 6.0)
        else:
            ra = float(settings.get("ra", 30.0))
            arc_count_raw = str(settings.get("arc_count", 2))
            show_arc = bool(settings.get("show_arc", True))
            reflex = bool(settings.get("reflex", False))
            arrow_start = bool(settings.get("arrow_start", False))
            arrow_end = bool(settings.get("arrow_end", False))
            show_double = bool(settings.get("show_double", False))
            show_sector = bool(settings.get("show_sector", False))
            show_point = bool(settings.get("show_point", False))
            show_s = bool(settings.get("show_s", False))
            show_rect = bool(settings.get("show_rect", False))
            rect_fill = bool(settings.get("rect_fill", False))
            sector_alpha = float(settings.get("sector_alpha", 0.15))
            show_label = bool(settings.get("show_label", False))
            label_bg_mode = (str(settings.get("label_bg_mode", "")).strip().lower() or (_LABEL_BG_MODE_WHITE if bool(settings.get("label_bg", False)) else _LABEL_BG_MODE_NONE))
            if label_bg_mode not in (_LABEL_BG_MODE_NONE, _LABEL_BG_MODE_WHITE, _LABEL_BG_MODE_CUT):
                label_bg_mode = _LABEL_BG_MODE_NONE
            label_text = str(settings.get("label_text", "")).strip()
            label_offset = float(settings.get("label_offset", 15.0))
            label_angle = float(settings.get("label_angle", 0.0))
            vertical = bool(settings.get("vertical", False))
            source = str(settings.get("source", root_el.get("data-angle-source") or ""))
            delta = float(settings.get("delta", 5.0))
            lam = float(settings.get("lam", 0.60))
            point_r = float(settings.get("point_r", 2.0))
            s_len = float(settings.get("s_len", 15.0))
            s_amp = float(settings.get("s_amp", 5.0))
            s_count_raw = str(settings.get("s_count", 1))
            s_gap = float(settings.get("s_gap", 6.0))
            rect_len = float(settings.get("rect_len", 40.0))
            rect_h = float(settings.get("rect_h", 8.0))
        if settings is None:
            rect_len = _parse_float(self._angle_rect_len_var.get().strip(), 40.0)
            rect_h = _parse_float(self._angle_rect_h_var.get().strip(), 8.0)

        try:
            arc_count = int(float(arc_count_raw))
        except Exception:
            arc_count = 2
        if not show_double:
            arc_count = 1
        else:
            arc_count = max(2, min(3, arc_count))
        try:
            s_count = int(float(s_count_raw))
        except Exception:
            s_count = 1
        if s_count < 1:
            s_count = 1
        if s_gap < 0:
            s_gap = 0.0
        if vertical:
            v1x, v1y = -v1x, -v1y
            v2x, v2y = -v2x, -v2y

        def mk_el(tag: str) -> ET.Element:
            ns = ""
            root_tag = self._svg_root.tag
            if root_tag.startswith("{") and "}" in root_tag:
                ns = root_tag.split("}", 1)[0][1:]
            return ET.Element(f"{{{ns}}}{tag}") if ns else ET.Element(tag)

        def mid_angle_dir() -> tuple[float, float]:
            bx = v1x + v2x
            by = v1y + v2y
            bl = math.hypot(bx, by)
            if bl <= 1e-6:
                bx, by = v1x, v1y
                bl = math.hypot(bx, by)
            if bl <= 1e-6:
                return (1.0, 0.0)
            return (bx / bl, by / bl)

        def base_signed_span_from_vectors() -> float:
            a1 = math.atan2(v1y, v1x)
            a2 = math.atan2(v2y, v2x)
            delta_ang = a2 - a1
            if sweep == 1:
                if delta_ang < 0:
                    delta_ang += math.tau
            else:
                if delta_ang > 0:
                    delta_ang -= math.tau
            return delta_ang

        def effective_signed_span_from_vectors() -> float:
            delta_ang = base_signed_span_from_vectors()
            eps = 1e-6
            if not reflex:
                return delta_ang
            mag = abs(delta_ang)
            if mag <= eps:
                return delta_ang
            # Replemento: dibuja solo la parte faltante para completar 360.
            if abs(mag - math.pi) <= eps:
                # Caso limite 180: invertimos sweep de forma determinista.
                return -delta_ang
            sign = 1.0 if delta_ang >= 0 else -1.0
            return -sign * (math.tau - mag)

        def oriented_delta_from_vectors() -> float:
            delta_ang = effective_signed_span_from_vectors()
            return delta_ang

        def arc_flags_from_span(delta_ang: float) -> tuple[int, int]:
            eps = 1e-6
            large_arc_flag = 1 if abs(delta_ang) > (math.pi + eps) else 0
            if abs(delta_ang) <= eps:
                sweep_flag = sweep
            else:
                sweep_flag = 1 if delta_ang > 0 else 0
            if reflex:
                # En modo replemento solo se dibuja la rama faltante.
                large_arc_flag = 1 if abs(delta_ang) > (math.pi + eps) else 0
            return large_arc_flag, sweep_flag

        def mid_angle_dir_sweep() -> tuple[float, float]:
            a1 = math.atan2(v1y, v1x)
            delta_ang = oriented_delta_from_vectors()
            if abs(delta_ang) <= 1e-6:
                return mid_angle_dir()
            mid_ang = a1 + 0.5 * delta_ang
            return (math.cos(mid_ang), math.sin(mid_ang))

        def set_common(el: ET.Element, kind: str) -> None:
            el.set("data-angle-id", angle_id)
            el.set("data-angle-kind", kind)

        def set_root_meta(el: ET.Element) -> None:
            el.set("data-angle-id", angle_id)
            el.set("data-angle-root", "1")
            el.set("data-angle-vx", _format_num(vx))
            el.set("data-angle-vy", _format_num(vy))
            el.set("data-angle-v1x", _format_num(orig_v1x))
            el.set("data-angle-v1y", _format_num(orig_v1y))
            el.set("data-angle-v2x", _format_num(orig_v2x))
            el.set("data-angle-v2y", _format_num(orig_v2y))
            el.set("data-angle-ra", _format_num(ra))
            el.set("data-angle-arc-count", str(arc_count))
            el.set("data-angle-sweep", str(sweep))
            el.set("data-angle-show-arc", "1" if show_arc else "0")
            el.set("data-angle-replement", "1" if reflex else "0")
            el.set("data-angle-arrow-start", "1" if arrow_start else "0")
            el.set("data-angle-arrow-end", "1" if arrow_end else "0")
            el.set("data-angle-show-double", "1" if show_double else "0")
            el.set("data-angle-show-sector", "1" if show_sector else "0")
            el.set("data-angle-show-point", "1" if show_point else "0")
            el.set("data-angle-show-s", "1" if show_s else "0")
            el.set("data-angle-show-rect", "1" if show_rect else "0")
            el.set("data-angle-rect-fill", "1" if rect_fill else "0")
            el.set("data-angle-label-show", "1" if show_label else "0")
            el.set("data-angle-label", label_text)
            el.set("data-angle-label-offset", _format_num(label_offset))
            el.set("data-angle-label-angle", _format_num(label_angle))
            el.set("data-angle-label-bg-mode", label_bg_mode)
            el.set("data-angle-label-bg", "1" if label_bg_mode == _LABEL_BG_MODE_WHITE else "0")
            el.set("data-angle-vertical", "1" if vertical else "0")
            el.set("data-angle-sector-alpha", _format_num(sector_alpha))
            if source:
                el.set("data-angle-source", source)
            el.set("data-angle-double-delta", _format_num(delta))
            el.set("data-angle-point-lambda", _format_num(lam))
            el.set("data-angle-point-r", _format_num(point_r))
            el.set("data-angle-s-len", _format_num(s_len))
            el.set("data-angle-s-amp", _format_num(s_amp))
            el.set("data-angle-s-count", str(s_count))
            el.set("data-angle-s-gap", _format_num(s_gap))
            el.set("data-angle-rect-len", _format_num(rect_len))
            el.set("data-angle-rect-h", _format_num(rect_h))

        elements: list[ET.Element] = []
        if show_rect:
            e1x, e1y = v1x, v1y
            e2x, e2y = v2x, v2y
            r0x, r0y = vx, vy
            r1x, r1y = vx + rect_len * e1x, vy + rect_len * e1y
            r2x, r2y = r1x + rect_h * e2x, r1y + rect_h * e2y
            r3x, r3y = vx + rect_h * e2x, vy + rect_h * e2y
            rect = mk_el("path")
            d = (
                f"M {_format_num(r0x)} {_format_num(r0y)} "
                f"L {_format_num(r1x)} {_format_num(r1y)} "
                f"L {_format_num(r2x)} {_format_num(r2y)} "
                f"L {_format_num(r3x)} {_format_num(r3y)} Z"
            )
            rect.set("d", d)
            if rect_fill:
                _set_attr(rect, "fill", stroke)
                _set_attr(rect, "stroke", "none")
            else:
                _set_attr(rect, "fill", "none")
                _set_attr(rect, "stroke", stroke)
                _set_attr(rect, "stroke-width", stroke_w)
            set_root_meta(rect)
            set_common(rect, "rect90")
            elements.append(rect)
            if show_label and label_text:
                dx, dy = mid_angle_dir_sweep()
                if abs(label_angle) > 1e-9:
                    ang = math.atan2(dy, dx) + math.radians(label_angle)
                    dx = math.cos(ang)
                    dy = math.sin(ang)
                base = max(ra, math.hypot(rect_len, rect_h), 1.0)
                lx = vx + (base + label_offset) * dx
                ly = vy + (base + label_offset) * dy
                font_size = _parse_float(self._global_font_size_var.get().strip(), 15.0)
                label_el = self._create_latex_label(
                    label_text, lx, ly, font_size, stroke, anchor_frac=(0.5, 0.5)
                )
                if label_el is not None:
                    self._set_label_bg_mode(label_el, label_bg_mode)
                    set_common(label_el, "label")
                    elements.append(label_el)
        else:
            sx = vx + ra * v1x
            sy = vy + ra * v1y
            tx = vx + ra * v2x
            ty = vy + ra * v2y
            delta_eff = oriented_delta_from_vectors()
            large_arc_flag, sweep_eff = arc_flags_from_span(delta_eff)
            arc_marker_ref = None
            if (show_arc or arc_count >= 2) and (arrow_start or arrow_end):
                arc_marker_id = self._ensure_arrow_marker()
                if arc_marker_id:
                    arc_marker_ref = f"url(#{arc_marker_id})"
            if show_sector:
                sector = mk_el("path")
                d = (
                    f"M {_format_num(vx)} {_format_num(vy)} "
                    f"L {_format_num(sx)} {_format_num(sy)} "
                    f"A {_format_num(ra)} {_format_num(ra)} 0 {large_arc_flag} {sweep_eff} {_format_num(tx)} {_format_num(ty)} Z"
                )
                sector.set("d", d)
                _set_attr(sector, "fill", "#000000")
                sector_alpha = max(0.0, min(1.0, float(sector_alpha)))
                _set_attr(sector, "fill-opacity", _format_num(sector_alpha))
                _set_attr(sector, "stroke", "none")
                set_common(sector, "sector")
                elements.append(sector)
            arc_root = None
            if show_arc:
                arc = mk_el("path")
                d = f"M {_format_num(sx)} {_format_num(sy)} A {_format_num(ra)} {_format_num(ra)} 0 {large_arc_flag} {sweep_eff} {_format_num(tx)} {_format_num(ty)}"
                arc.set("d", d)
                _set_attr(arc, "stroke", stroke)
                _set_attr(arc, "stroke-width", stroke_w)
                _set_attr(arc, "fill", "none")
                _set_attr(arc, "stroke-linecap", "round")
                if arc_marker_ref:
                    if arrow_start:
                        _set_attr(arc, "marker-start", arc_marker_ref)
                    if arrow_end:
                        _set_attr(arc, "marker-end", arc_marker_ref)
                set_common(arc, "arc")
                elements.append(arc)
                arc_root = arc
            if arc_count >= 2:
                r2 = ra + delta
                sx2 = vx + r2 * v1x
                sy2 = vy + r2 * v1y
                tx2 = vx + r2 * v2x
                ty2 = vy + r2 * v2y
                arc2 = mk_el("path")
                d2 = f"M {_format_num(sx2)} {_format_num(sy2)} A {_format_num(r2)} {_format_num(r2)} 0 {large_arc_flag} {sweep_eff} {_format_num(tx2)} {_format_num(ty2)}"
                arc2.set("d", d2)
                _set_attr(arc2, "stroke", stroke)
                _set_attr(arc2, "stroke-width", stroke_w)
                _set_attr(arc2, "fill", "none")
                _set_attr(arc2, "stroke-linecap", "round")
                if arc_marker_ref:
                    if arrow_start:
                        _set_attr(arc2, "marker-start", arc_marker_ref)
                    if arrow_end:
                        _set_attr(arc2, "marker-end", arc_marker_ref)
                set_common(arc2, "arc2")
                elements.append(arc2)
                if arc_root is None:
                    arc_root = arc2
            if arc_count >= 3:
                r3 = ra + 2.0 * delta
                sx3 = vx + r3 * v1x
                sy3 = vy + r3 * v1y
                tx3 = vx + r3 * v2x
                ty3 = vy + r3 * v2y
                arc3 = mk_el("path")
                d3 = f"M {_format_num(sx3)} {_format_num(sy3)} A {_format_num(r3)} {_format_num(r3)} 0 {large_arc_flag} {sweep_eff} {_format_num(tx3)} {_format_num(ty3)}"
                arc3.set("d", d3)
                _set_attr(arc3, "stroke", stroke)
                _set_attr(arc3, "stroke-width", stroke_w)
                _set_attr(arc3, "fill", "none")
                _set_attr(arc3, "stroke-linecap", "round")
                if arc_marker_ref:
                    if arrow_start:
                        _set_attr(arc3, "marker-start", arc_marker_ref)
                    if arrow_end:
                        _set_attr(arc3, "marker-end", arc_marker_ref)
                set_common(arc3, "arc3")
                elements.append(arc3)
                if arc_root is None:
                    arc_root = arc3
            bx = v1x + v2x
            by = v1y + v2y
            bl = math.hypot(bx, by)
            if bl <= 1e-6:
                bx, by = v1x, v1y
                bl = math.hypot(bx, by)
            if bl <= 1e-6:
                bx, by = 1.0, 0.0
                bl = 1.0
            bx /= bl
            by /= bl
            txu = -by
            tyu = bx
            if show_s:
                if ra > 1e-6:
                    a1 = math.atan2(v1y, v1x)
                    delta_ang = oriented_delta_from_vectors()
                    if abs(delta_ang) > 1e-6:
                        s_gap = max(0.0, s_gap)
                        if s_gap <= 1e-6:
                            s_gap = max(4.0, delta)
                        d_ang = s_gap / ra
                        s_offsets = self._centered_half_offsets(s_count, d_ang)
                        mid_ang = a1 + 0.5 * delta_ang
                        sweep_sign = 1.0 if delta_ang >= 0 else -1.0
                        for off in s_offsets:
                            t = 0.5 + (off / delta_ang)
                            if t < -1e-6 or t > 1.0 + 1e-6:
                                continue
                            ang = mid_ang + off
                            mx = vx + ra * math.cos(ang)
                            my = vy + ra * math.sin(ang)
                            tdx = math.cos(ang)
                            tdy = math.sin(ang)
                            ndx = -sweep_sign * math.sin(ang)
                            ndy = sweep_sign * math.cos(ang)
                            k0x = mx - 0.5 * s_len * tdx
                            k0y = my - 0.5 * s_len * tdy
                            k3x = mx + 0.5 * s_len * tdx
                            k3y = my + 0.5 * s_len * tdy
                            k1x = mx - (s_len / 6.0) * tdx + s_amp * ndx
                            k1y = my - (s_len / 6.0) * tdy + s_amp * ndy
                            k2x = mx + (s_len / 6.0) * tdx - s_amp * ndx
                            k2y = my + (s_len / 6.0) * tdy - s_amp * ndy
                            spath = mk_el("path")
                            d = (
                                f"M {_format_num(k0x)} {_format_num(k0y)} "
                                f"C {_format_num(k1x)} {_format_num(k1y)} "
                                f"{_format_num(k2x)} {_format_num(k2y)} "
                                f"{_format_num(k3x)} {_format_num(k3y)}"
                            )
                            spath.set("d", d)
                            _set_attr(spath, "stroke", stroke)
                            _set_attr(spath, "stroke-width", stroke_w)
                            _set_attr(spath, "fill", "none")
                            _set_attr(spath, "stroke-linecap", "round")
                            set_common(spath, "smark")
                            elements.append(spath)
                    spath = mk_el("path")
                    d = (
                        f"M {_format_num(k0x)} {_format_num(k0y)} "
                        f"C {_format_num(k1x)} {_format_num(k1y)} "
                        f"{_format_num(k2x)} {_format_num(k2y)} "
                        f"{_format_num(k3x)} {_format_num(k3y)}"
                    )
                    spath.set("d", d)
                    _set_attr(spath, "stroke", stroke)
                    _set_attr(spath, "stroke-width", stroke_w)
                    _set_attr(spath, "fill", "none")
                    _set_attr(spath, "stroke-linecap", "round")
                    set_common(spath, "smark")
                    elements.append(spath)
            if show_point:
                mp_x = vx + lam * ra * bx
                mp_y = vy + lam * ra * by
                c = mk_el("circle")
                c.set("cx", _format_num(mp_x))
                c.set("cy", _format_num(mp_y))
                c.set("r", _format_num(point_r))
                _set_attr(c, "fill", stroke)
                _set_attr(c, "stroke", "none")
                set_common(c, "point")
                elements.append(c)
            if show_label and label_text:
                dx, dy = mid_angle_dir_sweep()
                if abs(label_angle) > 1e-9:
                    ang = math.atan2(dy, dx) + math.radians(label_angle)
                    dx = math.cos(ang)
                    dy = math.sin(ang)
                base = ra + max(0.0, arc_count - 1) * max(delta, 0.0)
                base = max(base, 1.0)
                lx = vx + (base + label_offset) * dx
                ly = vy + (base + label_offset) * dy
                font_size = _parse_float(self._global_font_size_var.get().strip(), 15.0)
                label_el = self._create_latex_label(
                    label_text, lx, ly, font_size, stroke, anchor_frac=(0.5, 0.5)
                )
                if label_el is not None:
                    self._set_label_bg_mode(label_el, label_bg_mode)
                    set_common(label_el, "label")
                    elements.append(label_el)
            if arc_root is not None:
                set_root_meta(arc_root)
            elif elements:
                set_root_meta(elements[0])
        if not elements:
            return None
        for i, el in enumerate(elements):
            parent.insert(insert_at + i, el)
        root = next((el for el in elements if el.get("data-angle-root") == "1"), elements[0])
        if root_id and not root.get("id"):
            root.set("id", root_id)
        return root

    def _update_segment_mark_fields(self) -> None:
        if not hasattr(self, "_segment_mark_entry"):
            return
        style = self._segment_mark_style_var.get().strip().lower()
        if style in ("", "none", "ninguno", "nonce"):
            self._segment_mark_entry.configure(state="disabled")
            for frame in (
                getattr(self, "_segment_mark_points_frame", None),
                getattr(self, "_segment_mark_rect_frame", None),
                getattr(self, "_segment_mark_wave_frame", None),
                getattr(self, "_segment_mark_s_frame", None),
            ):
                if frame is not None:
                    frame.grid_remove()
            return
        self._segment_mark_entry.configure(state="normal")
        for frame in (
            self._segment_mark_points_frame,
            self._segment_mark_rect_frame,
            self._segment_mark_wave_frame,
            self._segment_mark_s_frame,
        ):
            frame.grid_remove()
        if style == "puntos":
            self._segment_mark_points_frame.grid(row=2, column=0, columnspan=8, sticky="w")
        elif style == "rectangulo":
            self._segment_mark_rect_frame.grid(row=2, column=0, columnspan=8, sticky="w")
        elif style == "sinusoidal":
            self._segment_mark_wave_frame.grid(row=2, column=0, columnspan=8, sticky="w")
        elif style == "s":
            self._segment_mark_s_frame.grid(row=2, column=0, columnspan=8, sticky="w")

    def _show_editor_mode(self, mode: str | None) -> None:
        required_frames = (
            "_point_editor_frame",
            "_segment_editor_frame",
            "_curve_editor_frame",
            "_angle_editor_frame",
            "_polygon_editor_frame",
            "_circle_editor_frame",
        )
        if any(name not in self.__dict__ for name in required_frames):
            return
        if bool(getattr(self, "_minimal_v1_globales_only", False)):
            self._segment_editor_frame.pack_forget()
            self._curve_editor_frame.pack_forget()
            self._angle_editor_frame.pack_forget()
            self._polygon_editor_frame.pack_forget()
            self._circle_editor_frame.pack_forget()
            if hasattr(self, "_stroke_editor_frame"):
                self._stroke_editor_frame.pack_forget()
            before = self._global_frame if hasattr(self, "_global_frame") else None
            if mode == "point":
                self._segment_editor_frame.pack_forget()
                self._curve_editor_frame.pack_forget()
                if not self._point_editor_frame.winfo_ismapped():
                    if before is not None:
                        self._point_editor_frame.pack(fill="x", pady=(8, 0), before=before)
                    else:
                        self._point_editor_frame.pack(fill="x", pady=(8, 0))
            elif mode == "segment":
                self._point_editor_frame.pack_forget()
                self._curve_editor_frame.pack_forget()
                if not self._segment_editor_frame.winfo_ismapped():
                    if before is not None:
                        self._segment_editor_frame.pack(fill="x", pady=(8, 0), before=before)
                    else:
                        self._segment_editor_frame.pack(fill="x", pady=(8, 0))
            elif mode == "curve":
                self._point_editor_frame.pack_forget()
                self._segment_editor_frame.pack_forget()
                self._angle_editor_frame.pack_forget()
                if not self._curve_editor_frame.winfo_ismapped():
                    if before is not None:
                        self._curve_editor_frame.pack(fill="x", pady=(8, 0), before=before)
                    else:
                        self._curve_editor_frame.pack(fill="x", pady=(8, 0))
            elif mode == "angle":
                self._point_editor_frame.pack_forget()
                self._segment_editor_frame.pack_forget()
                self._curve_editor_frame.pack_forget()
                if not self._angle_editor_frame.winfo_ismapped():
                    if before is not None:
                        self._angle_editor_frame.pack(fill="x", pady=(8, 0), before=before)
                    else:
                        self._angle_editor_frame.pack(fill="x", pady=(8, 0))
            else:
                self._point_editor_frame.pack_forget()
                self._segment_editor_frame.pack_forget()
                self._curve_editor_frame.pack_forget()
                self._angle_editor_frame.pack_forget()
            return
        before = self._global_frame if hasattr(self, "_global_frame") else None
        if mode == "point":
            self._segment_editor_frame.pack_forget()
            self._curve_editor_frame.pack_forget()
            self._angle_editor_frame.pack_forget()
            self._polygon_editor_frame.pack_forget()
            self._circle_editor_frame.pack_forget()
            if not self._point_editor_frame.winfo_ismapped():
                if before is not None:
                    self._point_editor_frame.pack(fill="x", pady=(8, 0), before=before)
                else:
                    self._point_editor_frame.pack(fill="x", pady=(8, 0))
            return
        if mode == "segment":
            self._point_editor_frame.pack_forget()
            self._curve_editor_frame.pack_forget()
            self._angle_editor_frame.pack_forget()
            self._polygon_editor_frame.pack_forget()
            self._circle_editor_frame.pack_forget()
            if not self._segment_editor_frame.winfo_ismapped():
                if before is not None:
                    self._segment_editor_frame.pack(fill="x", pady=(8, 0), before=before)
                else:
                    self._segment_editor_frame.pack(fill="x", pady=(8, 0))
            return
        if mode == "curve":
            self._point_editor_frame.pack_forget()
            self._segment_editor_frame.pack_forget()
            self._angle_editor_frame.pack_forget()
            self._polygon_editor_frame.pack_forget()
            self._circle_editor_frame.pack_forget()
            if not self._curve_editor_frame.winfo_ismapped():
                if before is not None:
                    self._curve_editor_frame.pack(fill="x", pady=(8, 0), before=before)
                else:
                    self._curve_editor_frame.pack(fill="x", pady=(8, 0))
            return
        if mode == "angle":
            self._point_editor_frame.pack_forget()
            self._segment_editor_frame.pack_forget()
            self._curve_editor_frame.pack_forget()
            self._polygon_editor_frame.pack_forget()
            self._circle_editor_frame.pack_forget()
            if not self._angle_editor_frame.winfo_ismapped():
                if before is not None:
                    self._angle_editor_frame.pack(fill="x", pady=(8, 0), before=before)
                else:
                    self._angle_editor_frame.pack(fill="x", pady=(8, 0))
            return
        if mode == "polygon":
            self._point_editor_frame.pack_forget()
            self._segment_editor_frame.pack_forget()
            self._curve_editor_frame.pack_forget()
            self._angle_editor_frame.pack_forget()
            self._circle_editor_frame.pack_forget()
            if not self._polygon_editor_frame.winfo_ismapped():
                if before is not None:
                    self._polygon_editor_frame.pack(fill="x", pady=(8, 0), before=before)
                else:
                    self._polygon_editor_frame.pack(fill="x", pady=(8, 0))
            return
        if mode == "circle":
            self._point_editor_frame.pack_forget()
            self._segment_editor_frame.pack_forget()
            self._curve_editor_frame.pack_forget()
            self._angle_editor_frame.pack_forget()
            self._polygon_editor_frame.pack_forget()
            if not self._circle_editor_frame.winfo_ismapped():
                if before is not None:
                    self._circle_editor_frame.pack(fill="x", pady=(8, 0), before=before)
                else:
                    self._circle_editor_frame.pack(fill="x", pady=(8, 0))
            return
        self._point_editor_frame.pack_forget()
        self._segment_editor_frame.pack_forget()
        self._curve_editor_frame.pack_forget()
        self._angle_editor_frame.pack_forget()
        self._polygon_editor_frame.pack_forget()
        self._circle_editor_frame.pack_forget()

    def _render_label_from_path(self, el: ET.Element) -> _Record | None:
        text = (el.get("data-text") or "").strip()
        x = _parse_float(el.get("data-x"))
        y = _parse_float(el.get("data-y"))
        size = _parse_float(el.get("data-font-size"), 12.0)
        fill = _get_attr(el, "fill") or "#000000"
        self._ensure_label_anchor(el, x, y)
        dir_s = (el.get("data-dir") or "").strip().upper()
        use_anchor = el.get("data-anchor-x") is None or el.get("data-anchor-y") is None
        item_ids = self._render_latex_items(text, x, y, size, fill, dir_s, use_anchor)
        if not item_ids:
            item = self.canvas.create_text(x, y, text=text, fill=fill, anchor="sw", font=("Arial", int(size)))
            item_ids = [item]
        return _Record(el=el, tag="path", item_ids=item_ids, kind="label", orig_fill=fill)

    def _render_latex_items(
        self,
        text: str,
        x: float,
        y: float,
        font_size: float,
        fill: str,
        dir_s: str | None = None,
        use_anchor: bool = True,
    ) -> list[int] | None:
        try:
            configure_mathtext, require_matplotlib = _resolve_latex_support()
            require_matplotlib()
            configure_mathtext()
            from matplotlib.font_manager import FontProperties
            from matplotlib.textpath import TextPath
        except Exception:
            return None

        fixed = _normalize_mathtext(_strip_mathtext_delims(text))
        if not fixed:
            return None
        s = f"${fixed}$"
        prop = FontProperties()
        path = TextPath((0, 0), s, size=font_size, prop=prop, usetex=False)
        ax = ay = 0.0
        if use_anchor:
            anchor = _label_anchor_for_dir(dir_s) if dir_s else None
            if anchor is not None:
                ax, ay = _textpath_anchor_point(path, anchor)
        polys = path.to_polygons()
        if not polys:
            return None
        item_ids: list[int] = []
        for poly in polys:
            if len(poly) < 3:
                continue
            coords: list[float] = []
            for vx, vy in poly:
                coords.extend([x + (vx - ax), y - (vy - ay)])
            item_ids.append(self.canvas.create_polygon(*coords, fill=fill, outline=""))
        return item_ids if item_ids else None

    def _on_canvas_press(self, event: tk.Event) -> None:
        if self.canvas is None:
            return
        try:
            self.canvas.focus_set()
        except Exception:
            pass
        self._clear_drag_state()
        if (
            self._segment_create_active
            or self._angle_create_active
            or self._intersection_create_active
            or self._curve_radius_create_active
            or self._projection_create_active
            or self._shade_diff_active
        ):
            return
        if not self._drawables or self._svg_root is None:
            return
        x = float(self.canvas.canvasx(event.x))
        y = float(self.canvas.canvasy(event.y))
        try:
            zoom = float(self._view_scale.get())
        except Exception:
            zoom = 1.0
        if zoom <= 0:
            zoom = 1.0
        radius_pick = self._pick_draggable_radius_endpoint(x, y, zoom)
        if radius_pick is not None:
            record, radius_el = radius_pick
            already_selected = self._selected is not None and self._selected.el is radius_el
            self._select_record(record)
            if not already_selected:
                self._set_transform_status("Radio seleccionado. Manten clic y arrastra para ajustar.")
                return
            self._drag_radius_el = radius_el
            self._drag_mouse_start = (x, y)
            self._drag_active = False
            self._drag_moved = False
            self._set_transform_status("Presiona y arrastra para ajustar radio.")
            return
        picked = self._pick_draggable_point(x, y, zoom)
        if picked is None:
            return
        record, point_el, cx, cy, reason = picked
        already_selected = self._selected is not None and self._selected.el is point_el
        self._select_record(record)
        if reason:
            self._set_transform_status(reason)
            return
        if not already_selected:
            self._set_transform_status("Punto seleccionado. Mantén clic y arrastra para mover.")
            return
        self._drag_point_el = point_el
        self._drag_point_start = (cx, cy)
        self._drag_mouse_start = (x, y)
        self._drag_active = False
        self._drag_moved = False
        self._set_transform_status("Presiona y arrastra para mover punto.")

    def _on_canvas_drag(self, event: tk.Event) -> None:
        if self.canvas is None or self._svg_root is None:
            return
        if (self._drag_point_el is None and self._drag_radius_el is None) or self._drag_mouse_start is None:
            return
        x = float(self.canvas.canvasx(event.x))
        y = float(self.canvas.canvasy(event.y))
        start_x, start_y = self._drag_mouse_start
        if not self._drag_active:
            if math.hypot(x - start_x, y - start_y) < self._drag_threshold_px:
                return
            self._drag_active = True
            self._begin_drag_history()
            self._set_transform_status("Arrastrando punto...")
        try:
            zoom = float(self._view_scale.get())
        except Exception:
            zoom = 1.0
        if zoom <= 0:
            zoom = 1.0
        if self._drag_radius_el is not None:
            target_x, target_y = self._canvas_to_svg(x, y, zoom)
            changed = self._update_radius_endpoint_by_drag(
                self._drag_radius_el,
                target_x=target_x,
                target_y=target_y,
                zoom=zoom,
            )
            if not changed:
                return
            self._drag_moved = True
            self._set_transform_status("Arrastrando radio...")
            self._render_svg()
            return
        old_x = _parse_float(_get_attr(self._drag_point_el, "cx"))
        old_y = _parse_float(_get_attr(self._drag_point_el, "cy"))
        target_x, target_y = self._canvas_to_svg(x, y, zoom)
        new_x, new_y, snapped = self._snap_to_anchor(
            target_x,
            target_y,
            zoom,
            exclude_anchor=(old_x, old_y),
        )
        if _same_point(old_x, old_y, new_x, new_y, 1e-9):
            return
        _set_attr(self._drag_point_el, "cx", _format_num(new_x))
        _set_attr(self._drag_point_el, "cy", _format_num(new_y))
        self._propagate_point_move(
            self._drag_point_el,
            (old_x, old_y),
            (new_x, new_y),
            zoom,
        )
        self._drag_moved = True
        if snapped:
            self._set_transform_status("Snap a ancla aplicado.")
        else:
            self._set_transform_status("Arrastrando punto...")
        self._render_svg()

    def _on_canvas_release(self, event: tk.Event):
        if (self._drag_point_el is not None or self._drag_radius_el is not None) and self._drag_active:
            if self._drag_moved:
                self._commit_drag_history()
                self._set_transform_status("Movimiento aplicado.")
            else:
                self._set_transform_status("Listo.")
            self._clear_drag_state()
            return "break"
        self._clear_drag_state()
        if bool(getattr(self, "_suppress_release_click_once", False)):
            self._suppress_release_click_once = False
            return "break"
        if self._shade_diff_active:
            self._schedule_pending_shade_click(event)
            return "break"
        self._on_canvas_click(event)
        return "break"

    def _cancel_pending_shade_click(self) -> None:
        pending = list(self.__dict__.get("_shade_pending_click_tokens", []))
        after_id = self.__dict__.get("_shade_pending_click_after_id")
        if after_id:
            pending.append((after_id, self.__dict__.get("_shade_pending_click_event") or (0, 0)))
        try:
            after_cancel = object.__getattribute__(self, "after_cancel")
        except Exception:
            after_cancel = None
        if callable(after_cancel):
            for token, _pos in pending:
                if not token:
                    continue
                try:
                    after_cancel(token)
                except Exception:
                    pass
        self._shade_pending_click_after_id = None
        self._shade_pending_click_event = None
        self._shade_pending_click_tokens = []

    def _cancel_pending_shade_click_near(self, x: int, y: int, *, tol_px: float = 8.0) -> None:
        pending = list(self.__dict__.get("_shade_pending_click_tokens", []))
        last_token = self.__dict__.get("_shade_pending_click_after_id")
        last_pos = self.__dict__.get("_shade_pending_click_event")
        if last_token and isinstance(last_pos, tuple):
            if not any(token == last_token for token, _ in pending):
                pending.append((last_token, (int(last_pos[0]), int(last_pos[1]))))
        if not pending:
            return
        try:
            after_cancel = object.__getattribute__(self, "after_cancel")
        except Exception:
            after_cancel = None
        kept: list[tuple[str, tuple[int, int]]] = []
        for token, pos in pending:
            px, py = pos
            if math.hypot(float(px - x), float(py - y)) <= tol_px:
                if callable(after_cancel) and token:
                    try:
                        after_cancel(token)
                    except Exception:
                        pass
                continue
            kept.append((token, pos))
        self._shade_pending_click_tokens = kept
        if self._shade_pending_click_after_id:
            keep_last = False
            for token, _pos in kept:
                if token == self._shade_pending_click_after_id:
                    keep_last = True
                    break
            if not keep_last:
                self._shade_pending_click_after_id = None
                self._shade_pending_click_event = None

    def _flush_pending_shade_clicks(self) -> None:
        pending = list(self.__dict__.get("_shade_pending_click_tokens", []))
        after_id = self.__dict__.get("_shade_pending_click_after_id")
        after_ev = self.__dict__.get("_shade_pending_click_event")
        if after_id and isinstance(after_ev, tuple):
            if not any(token == after_id for token, _pos in pending):
                pending.append((after_id, (int(after_ev[0]), int(after_ev[1]))))
        if not pending:
            return
        try:
            after_cancel = object.__getattribute__(self, "after_cancel")
        except Exception:
            after_cancel = None
        if callable(after_cancel):
            for token, _pos in pending:
                if not token:
                    continue
                try:
                    after_cancel(token)
                except Exception:
                    pass
        self._shade_pending_click_after_id = None
        self._shade_pending_click_event = None
        self._shade_pending_click_tokens = []
        for _token, pos in pending:
            if not self._shade_diff_active:
                break
            ev = type("_ShadeFlushClickEvent", (), {"x": int(pos[0]), "y": int(pos[1])})()
            self._on_canvas_click(ev)

    def _schedule_pending_shade_click(self, event: tk.Event) -> None:
        if not self._shade_diff_active:
            self._on_canvas_click(event)
            return
        try:
            after_fn = object.__getattribute__(self, "after")
        except Exception:
            after_fn = None
        if not callable(after_fn):
            self._on_canvas_click(event)
            return
        try:
            ex = int(event.x)
            ey = int(event.y)
        except Exception:
            self._on_canvas_click(event)
            return
        self._shade_pending_click_event = (ex, ey)
        delay = int(self.__dict__.get("_shade_single_click_delay_ms", 220))
        if delay < 0:
            delay = 0

        holder: dict[str, str | None] = {"token": None}

        def _fire_pending_shade_click() -> None:
            token = holder.get("token")
            pending = list(self.__dict__.get("_shade_pending_click_tokens", []))
            kept: list[tuple[str, tuple[int, int]]] = []
            pos: tuple[int, int] | None = None
            for tkid, p in pending:
                if token and tkid == token:
                    pos = p
                    continue
                kept.append((tkid, p))
            self._shade_pending_click_tokens = kept
            if token and self._shade_pending_click_after_id == token:
                self._shade_pending_click_after_id = None
                self._shade_pending_click_event = None
            if not self._shade_diff_active or pos is None:
                return
            ev = type("_ShadePendingClickEvent", (), {"x": pos[0], "y": pos[1]})()
            self._on_canvas_click(ev)

        try:
            token = after_fn(delay, _fire_pending_shade_click)
            holder["token"] = token
            self._shade_pending_click_after_id = token
            self._shade_pending_click_event = (ex, ey)
            self._shade_pending_click_tokens = list(self.__dict__.get("_shade_pending_click_tokens", []))
            self._shade_pending_click_tokens.append((token, (ex, ey)))
        except Exception:
            self._shade_pending_click_after_id = None
            self._shade_pending_click_event = None
            self._on_canvas_click(event)

    def _clear_selection_on_double_click_outside(self) -> None:
        had_change = False
        if self._selected is not None:
            self._selected = None
            if hasattr(self, "_sync_selected_ui"):
                try:
                    self._sync_selected_ui()
                except Exception:
                    pass
            if hasattr(self, "_clear_code_highlight"):
                try:
                    self._clear_code_highlight()
                except Exception:
                    pass
            had_change = True
        if self._shade_diff_active and self._shade_contour_edges:
            self._clear_shade_diff_selection()
            had_change = True
        if had_change:
            self._render_preview()
        elif self._shade_diff_active:
            self._shade_diff_status()

    def _on_canvas_click(self, event: tk.Event) -> None:
        if self.canvas is not None:
            try:
                self.canvas.focus_set()
            except Exception:
                pass
        if self._segment_create_active:
            self._handle_segment_create_click(event)
            return
        if self._angle_create_active:
            self._handle_angle_create_click(event)
            return
        if self._intersection_create_active:
            self._handle_intersection_create_click(event)
            return
        if self._curve_radius_create_active:
            self._handle_curve_radius_create_click(event)
            return
        if self._projection_create_active:
            self._handle_projection_create_click(event)
            return
        if self._shade_diff_active:
            self._handle_shade_diff_click(event)
            return
        if not self._drawables:
            return
        x = float(self.canvas.canvasx(event.x))
        y = float(self.canvas.canvasy(event.y))
        try:
            zoom = float(self._view_scale.get())
        except Exception:
            zoom = 1.0
        if zoom <= 0:
            zoom = 1.0
        candidates = self._collect_hit_candidates(x, y, zoom)
        if candidates:
            primary = [(dist, d) for dist, d in candidates if not self._is_subsegment_record(d.record)]
            if primary:
                candidates = primary
        best = candidates[0][1] if candidates else None
        best_dist = candidates[0][0] if candidates else 1e9
        if best is None or best_dist > 6.0:
            if self._selected is not None:
                self._selected = None
                self._sync_selected_ui()
                self._clear_code_highlight()
                self._render_preview()
            return
        if best.record is not None:
            target = self._resolve_parent_record(best.record)
            self._reset_child_cycle_for_parent(target.el)
            self._select_record(target)

    def _on_canvas_double_click(self, event: tk.Event) -> None:
        if self.canvas is None:
            return
        try:
            self.canvas.focus_set()
        except Exception:
            pass
        if self._shade_diff_active:
            try:
                dx = int(event.x)
                dy = int(event.y)
            except Exception:
                dx = dy = 0
            self._cancel_pending_shade_click_near(dx, dy)
            self._suppress_release_click_once = True
            self._handle_shade_diff_double_click(event)
            return
        if not self._drawables:
            self._clear_selection_on_double_click_outside()
            return
        x = float(self.canvas.canvasx(event.x))
        y = float(self.canvas.canvasy(event.y))
        try:
            zoom = float(self._view_scale.get())
        except Exception:
            zoom = 1.0
        if zoom <= 0:
            zoom = 1.0

        candidates = self._collect_hit_candidates(
            x,
            y,
            zoom,
            kinds=("line", "polyline", "polygon", "circle", "ellipse", "path"),
        )
        if not candidates:
            candidates = self._collect_hit_candidates(x, y, zoom)
        if not candidates:
            self._clear_selection_on_double_click_outside()
            return
        valid_hits = [item for item in candidates if item[0] <= 6.0]
        if not valid_hits:
            self._clear_selection_on_double_click_outside()
            return
        non_point_hits = [item for item in valid_hits if not self._is_geometric_point_record(item[1].record)]
        if non_point_hits:
            valid_hits = non_point_hits
        split_hits = [item for item in valid_hits if self._is_split_target_record(item[1].record)]
        if split_hits:
            valid_hits = split_hits

        sub_hits = [item for item in valid_hits if self._is_subsegment_record(item[1].record)]
        if sub_hits:
            pick = sub_hits[0][1]
        else:
            pick = valid_hits[0][1]
        if pick.record is None:
            self._clear_selection_on_double_click_outside()
            return
        self._suppress_release_click_once = True
        target = self._resolve_child_record(pick.record, x, y, zoom, create_if_missing=True)
        self._select_record(target)

    def _collect_hit_candidates(
        self,
        x: float,
        y: float,
        zoom: float,
        *,
        kinds: tuple[str, ...] | None = None,
    ) -> list[tuple[float, _Drawable]]:
        hits: list[tuple[float, _Drawable]] = []
        for d in self._drawables:
            if d.record is None:
                continue
            if kinds and d.kind not in kinds:
                continue
            dist = self._hit_test_drawable(d, x, y, zoom)
            if dist is None:
                continue
            hits.append((dist, d))
        hits.sort(key=lambda item: item[0])
        return hits

    def _is_subsegment_record(self, record: _Record | None) -> bool:
        if record is None:
            return False
        return record.el.get("data-kind") == "subsegment"

    def _is_geometric_point_record(self, record: _Record | None) -> bool:
        if record is None:
            return False
        if record.kind != "shape":
            return False
        if _strip_ns(record.el.tag) != "circle":
            return False
        return self._is_point_circle(record.el)

    def _is_split_target_record(self, record: _Record | None) -> bool:
        if record is None or record.kind != "shape":
            return False
        if self._is_subsegment_record(record):
            return True
        el = record.el
        tag = _strip_ns(el.tag)
        if tag == "line":
            kind = (el.get("data-kind") or "").strip()
            if kind and _is_aux_data_kind(kind):
                return False
            x1 = _parse_float(_get_attr(el, "x1"))
            y1 = _parse_float(_get_attr(el, "y1"))
            x2 = _parse_float(_get_attr(el, "x2"))
            y2 = _parse_float(_get_attr(el, "y2"))
            return math.hypot(x2 - x1, y2 - y1) > 1e-9
        return self._is_curve_subsegment_parent(el)

    def _subsegment_parent_element(self, sub_el: ET.Element | None) -> ET.Element | None:
        if sub_el is None or self._svg_root is None:
            return None
        if (sub_el.get("data-kind") or "").strip() != "subsegment":
            return None
        parent_id = (sub_el.get("data-parent-id") or "").strip()
        if not parent_id:
            return None
        for el in self._svg_root.iter():
            if el is sub_el:
                continue
            if (el.get("id") or "").strip() != parent_id:
                continue
            return el
        return None

    def _record_for_element(self, el: ET.Element) -> _Record:
        for rec in self._records:
            if rec.el is el:
                return rec
        return _Record(el=el, tag=_strip_ns(el.tag), item_ids=[], kind="shape")

    def _reset_child_cycle_for_parent(self, parent_el: ET.Element | None) -> None:
        if parent_el is None:
            self._last_parent_for_cycle = None
            return
        parent_key = id(parent_el)
        self._child_cycle_idx_by_parent[parent_key] = 0
        self._last_parent_for_cycle = parent_key

    def _resolve_parent_record(self, record: _Record) -> _Record:
        if not self._is_subsegment_record(record):
            return record
        parent = self._subsegment_parent_element(record.el)
        if parent is None:
            return record
        return self._record_for_element(parent)

    def _is_degenerate_subsegment_element(self, el: ET.Element) -> bool:
        if (el.get("data-kind") or "").strip() != "subsegment":
            return False
        pts, _closed = self._shade_contour_source_points(el, direction=0)
        if len(pts) < 2:
            return True
        length = 0.0
        for i in range(len(pts) - 1):
            ax, ay = pts[i]
            bx, by = pts[i + 1]
            length += math.hypot(bx - ax, by - ay)
        return length <= 1e-6

    def _open_curve_expected_split_count(
        self,
        parent_el: ET.Element,
        x: float,
        y: float,
        zoom: float,
    ) -> int | None:
        if not self._is_curve_subsegment_parent(parent_el):
            return None
        sx, sy = self._canvas_to_svg(x, y, zoom)
        points, closed = self._curve_subsegment_points(parent_el, click_svg=(sx, sy))
        if closed or len(points) < 2:
            return None
        total_len = self._polyline_total_length(points, closed=False)
        if total_len <= 1e-9:
            return 0
        tol = 5.0 / max(zoom, 1e-6)
        split_points = self._curve_split_points(points, closed=False, tol=tol, total_len=total_len)
        if len(split_points) < 3:
            return 0
        return len(split_points) - 1

    def _open_curve_existing_children(self, parent_id: str) -> list[ET.Element]:
        if self._svg_root is None or not parent_id:
            return []
        out: list[ET.Element] = []
        for el in self._svg_root.iter():
            if _strip_ns(el.tag) != "path":
                continue
            if (el.get("data-kind") or "").strip() != "subsegment":
                continue
            if (el.get("data-parent-id") or "").strip() != parent_id:
                continue
            if not self._element_in_svg(el):
                continue
            if self._is_degenerate_subsegment_element(el):
                continue
            out.append(el)
        return out

    def _remove_subsegments_for_parent(self, parent_id: str) -> bool:
        if self._svg_root is None or not parent_id:
            return False
        removed = False
        for el in list(self._svg_root.iter()):
            if (el.get("data-kind") or "").strip() != "subsegment":
                continue
            if (el.get("data-parent-id") or "").strip() != parent_id:
                continue
            parent = self._parent_of(el)
            if parent is None:
                continue
            try:
                parent.remove(el)
                removed = True
            except Exception:
                continue
        return removed

    def _needs_open_curve_rebuild(
        self,
        parent_el: ET.Element,
        existing_children: list[ET.Element],
        expected_count: int | None,
    ) -> bool:
        if expected_count is None:
            return False
        if expected_count < 2:
            return False
        if len(existing_children) != expected_count:
            return True
        points, closed = self._curve_subsegment_points(parent_el)
        if closed or len(points) < 2:
            return False
        total_len = self._polyline_total_length(points, closed=False)
        if total_len <= 1e-9:
            return True
        seen_keys: set[str] = set()
        intervals: list[tuple[float, float]] = []
        for child in existing_children:
            key = (child.get("data-subsegment-key") or "").strip()
            if key:
                if key in seen_keys:
                    return True
                seen_keys.add(key)
            interval = self._curve_child_interval_on_parent(
                parent_el,
                points,
                False,
                total_len=total_len,
                child_el=child,
            )
            if interval is None:
                return True
            start_s, span = interval
            if not math.isfinite(start_s) or not math.isfinite(span):
                return True
            if span <= 1e-9:
                return True
            start = max(0.0, min(total_len, start_s))
            end = max(start, min(total_len, start_s + span))
            if end - start <= 1e-9:
                return True
            intervals.append((start, end))
        if len(intervals) != expected_count:
            return True
        intervals.sort(key=lambda item: item[0])
        s_tol = max(1e-6, total_len * 1e-3)
        if intervals[0][0] < -s_tol:
            return True
        if intervals[0][0] > s_tol:
            return True
        if abs(intervals[-1][1] - total_len) > s_tol:
            return True
        for i in range(len(intervals) - 1):
            if abs(intervals[i][1] - intervals[i + 1][0]) > s_tol:
                return True
        return False

    def _child_candidates_for_parent(
        self,
        parent_el: ET.Element,
        x: float,
        y: float,
        zoom: float,
        *,
        create_if_missing: bool = True,
    ) -> list[_Record]:
        if self._svg_root is None:
            return []
        parent_id = (parent_el.get("id") or "").strip()
        if not parent_id and create_if_missing:
            parent_id = self._ensure_element_id(parent_el, prefix="shape")
        out: list[_Record] = []

        def _collect_existing() -> None:
            if not parent_id:
                return
            for el in self._svg_root.iter():
                if (el.get("data-kind") or "").strip() != "subsegment":
                    continue
                if (el.get("data-parent-id") or "").strip() != parent_id:
                    continue
                if not self._element_in_svg(el):
                    continue
                if self._is_degenerate_subsegment_element(el):
                    continue
                if not self._is_shade_contour_source_candidate(el):
                    continue
                out.append(self._record_for_element(el))

        _collect_existing()
        if create_if_missing and self._is_curve_subsegment_parent(parent_el):
            expected_count = self._open_curve_expected_split_count(parent_el, x, y, zoom)
            existing_children = self._open_curve_existing_children(parent_id)
            if self._needs_open_curve_rebuild(parent_el, existing_children, expected_count):
                if self._remove_subsegments_for_parent(parent_id):
                    out.clear()
                    _collect_existing()
        if out or not create_if_missing:
            pass
        else:
            created: ET.Element | None = None
            if _strip_ns(parent_el.tag) == "line":
                created = self._select_subsegment_for_line(parent_el, x, y, zoom, split=True)
            elif self._is_curve_subsegment_parent(parent_el):
                created = self._select_subsegment_for_curve(parent_el, x, y, zoom, split=True)
            if created is not None:
                self._render_svg()
                if not parent_id:
                    parent_id = (parent_el.get("id") or "").strip()
                _collect_existing()
                if not out and not self._is_degenerate_subsegment_element(created):
                    out.append(self._record_for_element(created))

        uniq: list[_Record] = []
        seen: set[int] = set()
        for rec in out:
            if id(rec.el) in seen:
                continue
            seen.add(id(rec.el))
            uniq.append(rec)
        uniq.sort(
            key=lambda rec: (
                (rec.el.get("data-subsegment-key") or ""),
                (rec.el.get("id") or ""),
                id(rec.el),
            )
        )
        return uniq

    def _resolve_line_child_by_click(
        self,
        parent_el: ET.Element,
        children: list[_Record],
        x: float,
        y: float,
        zoom: float,
    ) -> _Record | None:
        try:
            x1 = _parse_float(_get_attr(parent_el, "x1"))
            y1 = _parse_float(_get_attr(parent_el, "y1"))
            x2 = _parse_float(_get_attr(parent_el, "x2"))
            y2 = _parse_float(_get_attr(parent_el, "y2"))
        except Exception:
            return None
        vx = x2 - x1
        vy = y2 - y1
        denom = vx * vx + vy * vy
        if denom <= 1e-12:
            return None
        sx, sy = self._canvas_to_svg(x, y, zoom)
        t_click = ((sx - x1) * vx + (sy - y1) * vy) / denom
        t_click = max(0.0, min(1.0, t_click))
        line_len = math.sqrt(denom)
        t_tol = max(1e-6, (2.0 / max(zoom, 1e-6)) / max(line_len, 1e-9))
        chosen: _Record | None = None
        best_key = (float("inf"), float("inf"))
        for child in children:
            child_el = child.el
            if _strip_ns(child_el.tag) != "line":
                continue
            try:
                cx1 = _parse_float(_get_attr(child_el, "x1"))
                cy1 = _parse_float(_get_attr(child_el, "y1"))
                cx2 = _parse_float(_get_attr(child_el, "x2"))
                cy2 = _parse_float(_get_attr(child_el, "y2"))
            except Exception:
                continue
            t1 = ((cx1 - x1) * vx + (cy1 - y1) * vy) / denom
            t2 = ((cx2 - x1) * vx + (cy2 - y1) * vy) / denom
            t1 = max(0.0, min(1.0, t1))
            t2 = max(0.0, min(1.0, t2))
            t_min = min(t1, t2) - t_tol
            t_max = max(t1, t2) + t_tol
            if not (t_min <= t_click <= t_max):
                continue
            dist = self._dist_point_to_segment(sx, sy, cx1, cy1, cx2, cy2)
            mid = 0.5 * (t1 + t2)
            key = (dist, abs(mid - t_click))
            if key < best_key:
                best_key = key
                chosen = child
        return chosen

    def _curve_child_interval_on_parent(
        self,
        parent_el: ET.Element,
        parent_points: list[tuple[float, float]],
        parent_closed: bool,
        *,
        total_len: float,
        child_el: ET.Element,
    ) -> tuple[float, float] | None:
        key = (child_el.get("data-subsegment-key") or "").strip()
        parent_token = self._curve_parent_token(parent_el)
        marker = ":curve:"
        if key and marker in key:
            prefix, rest = key.split(marker, 1)
            if prefix == parent_token:
                parts = rest.split(":")
                if len(parts) >= 2:
                    try:
                        start_s = _parse_float(parts[0])
                        span = _parse_float(parts[1])
                    except Exception:
                        start_s = float("nan")
                        span = float("nan")
                    if math.isfinite(start_s) and math.isfinite(span) and span > 1e-9:
                        return (start_s, span)
        child_points, _child_closed = self._shade_contour_source_points(child_el, direction=0)
        if len(child_points) < 2:
            return None
        start_proj = self._project_point_on_polyline(
            parent_points,
            closed=parent_closed,
            px=child_points[0][0],
            py=child_points[0][1],
        )
        end_proj = self._project_point_on_polyline(
            parent_points,
            closed=parent_closed,
            px=child_points[-1][0],
            py=child_points[-1][1],
        )
        if start_proj is None or end_proj is None:
            return None
        s0 = start_proj[1]
        s1 = end_proj[1]
        if parent_closed:
            fwd = (s1 - s0) % total_len
            rev = (s0 - s1) % total_len
            child_len = self._polyline_total_length(child_points, closed=False)
            diff_fwd = abs(fwd - child_len)
            diff_rev = abs(rev - child_len)
            if abs(diff_fwd - diff_rev) > 1e-6:
                if diff_fwd <= diff_rev:
                    return (s0, fwd)
                return (s1, rev)
            # Ambiguous endpoints on a closed parent (common in half-arcs):
            # disambiguate with the child's midpoint projected on parent.
            mid_idx = len(child_points) // 2
            mid_px, mid_py = child_points[mid_idx]
            mid_proj = self._project_point_on_polyline(
                parent_points,
                closed=parent_closed,
                px=mid_px,
                py=mid_py,
            )
            if mid_proj is not None:
                s_mid = mid_proj[1] % total_len
                s_tol = max(1e-6, total_len * 1e-6)
                if self._curve_interval_contains_s(
                    s_click=s_mid,
                    start_s=s0,
                    span=fwd,
                    total_len=total_len,
                    closed=True,
                    s_tol=s_tol,
                ):
                    return (s0, fwd)
                return (s1, rev)
            return (s0, fwd)
        if s1 < s0:
            s0, s1 = s1, s0
        span = s1 - s0
        if span <= 1e-9:
            return None
        return (s0, span)

    def _curve_interval_contains_s(
        self,
        *,
        s_click: float,
        start_s: float,
        span: float,
        total_len: float,
        closed: bool,
        s_tol: float,
    ) -> bool:
        if span <= 1e-9:
            return False
        if not closed:
            return (start_s - s_tol) <= s_click <= (start_s + span + s_tol)
        s = s_click % total_len
        start = start_s % total_len
        end = start + span
        if end <= total_len:
            return (start - s_tol) <= s <= (end + s_tol)
        wrap_end = end - total_len
        return s >= (start - s_tol) or s <= (wrap_end + s_tol)

    def _resolve_curve_child_by_click(
        self,
        parent_el: ET.Element,
        children: list[_Record],
        x: float,
        y: float,
        zoom: float,
    ) -> _Record | None:
        sx, sy = self._canvas_to_svg(x, y, zoom)
        parent_points, parent_closed = self._curve_subsegment_points(parent_el, click_svg=(sx, sy))
        if len(parent_points) < (3 if parent_closed else 2):
            return None
        click_proj = self._project_point_on_polyline(parent_points, closed=parent_closed, px=sx, py=sy)
        if click_proj is None:
            return None
        _dist_click, s_click_raw, _qx, _qy, total_len = click_proj
        if total_len <= 1e-9:
            return None
        # A child curve is considered "under cursor" only inside the same hit
        # tolerance used by the generic pick logic.
        hit_tol_svg = 6.0 / max(zoom, 1e-6)
        s_click = s_click_raw % total_len if parent_closed else s_click_raw
        s_tol = max(1e-6, 2.0 / max(zoom, 1e-6))
        chosen: _Record | None = None
        chosen_key = (float("inf"), float("inf"))
        nearest: _Record | None = None
        nearest_dist = float("inf")
        for child in children:
            child_el = child.el
            child_points, _child_closed = self._shade_contour_source_points(child_el, direction=0)
            if len(child_points) < 2:
                continue
            child_proj = self._project_point_on_polyline(child_points, closed=False, px=sx, py=sy)
            if child_proj is None:
                continue
            dist_child = child_proj[0]
            if dist_child > hit_tol_svg:
                continue
            if dist_child < nearest_dist:
                nearest_dist = dist_child
                nearest = child
            interval = self._curve_child_interval_on_parent(
                parent_el,
                parent_points,
                parent_closed,
                total_len=total_len,
                child_el=child_el,
            )
            if interval is None:
                continue
            start_s, span = interval
            if not self._curve_interval_contains_s(
                s_click=s_click,
                start_s=start_s,
                span=span,
                total_len=total_len,
                closed=parent_closed,
                s_tol=s_tol,
            ):
                continue
            mid = start_s + 0.5 * span
            if parent_closed:
                delta = abs(((s_click - mid + 0.5 * total_len) % total_len) - 0.5 * total_len)
            else:
                delta = abs(s_click - mid)
            key = (dist_child, delta)
            if key < chosen_key:
                chosen_key = key
                chosen = child
        if chosen is not None:
            return chosen
        # Fallback for arc-path parents with weak interval reconstruction:
        # pick the nearest child under cursor when interval checks are inconclusive.
        return nearest

    def _resolve_child_record(
        self,
        record: _Record,
        x: float,
        y: float,
        zoom: float,
        *,
        create_if_missing: bool = True,
    ) -> _Record:
        if self._is_subsegment_record(record):
            tag = _strip_ns(record.el.tag)
            if tag == "line":
                nested = self._child_candidates_for_parent(
                    record.el,
                    x,
                    y,
                    zoom,
                    create_if_missing=create_if_missing,
                )
                chosen_nested = self._resolve_line_child_by_click(record.el, nested, x, y, zoom)
                if chosen_nested is not None:
                    self._last_parent_for_cycle = id(record.el)
                    return chosen_nested
            elif tag == "path" and self._is_curve_subsegment_parent(record.el):
                nested = self._child_candidates_for_parent(
                    record.el,
                    x,
                    y,
                    zoom,
                    create_if_missing=create_if_missing,
                )
                chosen_nested = self._resolve_curve_child_by_click(record.el, nested, x, y, zoom)
                if chosen_nested is not None:
                    self._last_parent_for_cycle = id(record.el)
                    return chosen_nested
            parent_el = self._subsegment_parent_element(record.el)
            if parent_el is None:
                self._last_parent_for_cycle = id(record.el)
                return record
            if _strip_ns(record.el.tag) == "line" and _strip_ns(parent_el.tag) == "line":
                siblings = self._child_candidates_for_parent(
                    parent_el,
                    x,
                    y,
                    zoom,
                    create_if_missing=create_if_missing,
                )
                self._last_parent_for_cycle = id(parent_el)
                chosen = self._resolve_line_child_by_click(parent_el, siblings, x, y, zoom)
                if chosen is not None:
                    return chosen
                return record
            if _strip_ns(record.el.tag) == "path" and self._is_curve_subsegment_parent(parent_el):
                siblings = self._child_candidates_for_parent(
                    parent_el,
                    x,
                    y,
                    zoom,
                    create_if_missing=create_if_missing,
                )
                self._last_parent_for_cycle = id(parent_el)
                chosen = self._resolve_curve_child_by_click(parent_el, siblings, x, y, zoom)
                if chosen is not None:
                    return chosen
                return record
            parent_key = id(parent_el)
            self._last_parent_for_cycle = parent_key
            siblings = self._child_candidates_for_parent(
                parent_el,
                x,
                y,
                zoom,
                create_if_missing=False,
            )
            if siblings:
                for idx, sibling in enumerate(siblings):
                    if sibling.el is record.el:
                        self._child_cycle_idx_by_parent[parent_key] = (idx + 1) % len(siblings)
                        break
            return record
        parent_record = self._resolve_parent_record(record)
        parent_el = parent_record.el
        children = self._child_candidates_for_parent(parent_el, x, y, zoom, create_if_missing=create_if_missing)
        if not children:
            self._last_parent_for_cycle = id(parent_el)
            return parent_record
        if _strip_ns(parent_el.tag) == "line":
            chosen = self._resolve_line_child_by_click(parent_el, children, x, y, zoom)
            self._last_parent_for_cycle = id(parent_el)
            if chosen is not None:
                return chosen
            return parent_record
        if self._is_curve_subsegment_parent(parent_el):
            chosen = self._resolve_curve_child_by_click(parent_el, children, x, y, zoom)
            self._last_parent_for_cycle = id(parent_el)
            if chosen is not None:
                return chosen
            return parent_record
        parent_key = id(parent_el)
        if self._last_parent_for_cycle != parent_key:
            idx = 0
        else:
            idx = self._child_cycle_idx_by_parent.get(parent_key, 0)
        if idx < 0 or idx >= len(children):
            idx = 0
        chosen = children[idx]
        self._child_cycle_idx_by_parent[parent_key] = (idx + 1) % len(children)
        self._last_parent_for_cycle = parent_key
        return chosen

    def _canvas_to_svg(self, x: float, y: float, zoom: float) -> tuple[float, float]:
        return (x / zoom - self._shift_x, y / zoom - self._shift_y)

    def _select_subsegment_for_line(
        self, line_el: ET.Element, x: float, y: float, zoom: float, *, split: bool = False
    ) -> ET.Element | None:
        if self._svg_root is None:
            return None
        try:
            x1 = _parse_float(_get_attr(line_el, "x1"))
            y1 = _parse_float(_get_attr(line_el, "y1"))
            x2 = _parse_float(_get_attr(line_el, "x2"))
            y2 = _parse_float(_get_attr(line_el, "y2"))
        except Exception:
            return None
        points = self._points_on_line(line_el, tol_px=5.0, zoom=zoom)
        if len(points) < 3:
            return None
        if split:
            self._split_line_on_points(line_el, points)
        sx, sy = self._canvas_to_svg(x, y, zoom)
        vx = x2 - x1
        vy = y2 - y1
        denom = vx * vx + vy * vy
        if denom <= 1e-12:
            return None
        t_click = ((sx - x1) * vx + (sy - y1) * vy) / denom
        t_click = max(0.0, min(1.0, t_click))
        idx = 0
        for i in range(len(points) - 1):
            if points[i][0] <= t_click <= points[i + 1][0]:
                idx = i
                break
        ax, ay = points[idx][1], points[idx][2]
        bx, by = points[idx + 1][1], points[idx + 1][2]
        return self._ensure_subsegment(line_el, ax, ay, bx, by)

    def _is_curve_subsegment_parent(self, el: ET.Element | None) -> bool:
        if el is None:
            return False
        tag = _strip_ns(el.tag)
        kind = (el.get("data-kind") or "").strip()
        # Allow recursive split on path subsegments (child becomes parent),
        # while still excluding all other auxiliary elements.
        if _is_aux_data_kind(kind) and not (kind == "subsegment" and tag == "path"):
            return False
        if tag == "circle":
            return self._is_editable_circle(el)
        if tag == "ellipse":
            rx = _parse_float(_get_attr(el, "rx"), 0.0)
            ry = _parse_float(_get_attr(el, "ry"), 0.0)
            return rx > 0 and ry > 0
        if tag == "path":
            if el.get("data-text") is not None:
                return False
            d = _get_attr(el, "d") or ""
            return any(len(pts) >= 2 for pts, _closed in _parse_svg_path(d))
        return False

    def _curve_subsegment_points(
        self, el: ET.Element, *, click_svg: tuple[float, float] | None = None
    ) -> tuple[list[tuple[float, float]], bool]:
        tag = _strip_ns(el.tag)
        if tag == "circle":
            cx = _parse_float(_get_attr(el, "cx"))
            cy = _parse_float(_get_attr(el, "cy"))
            r = _parse_float(_get_attr(el, "r"), 0.0)
            if r <= 0:
                return ([], False)
            return (self._ellipse_points(cx, cy, r, r, steps=160), True)
        if tag == "ellipse":
            cx = _parse_float(_get_attr(el, "cx"))
            cy = _parse_float(_get_attr(el, "cy"))
            rx = _parse_float(_get_attr(el, "rx"), 0.0)
            ry = _parse_float(_get_attr(el, "ry"), 0.0)
            if rx <= 0 or ry <= 0:
                return ([], False)
            return (self._ellipse_points(cx, cy, rx, ry, steps=160), True)
        if tag != "path":
            return ([], False)
        d = _get_attr(el, "d") or ""
        raw_subpaths = [(list(pts), bool(closed)) for pts, closed in _parse_svg_path(d) if len(pts) >= 2]
        if not raw_subpaths:
            return ([], False)
        subpaths: list[tuple[list[tuple[float, float]], bool]] = []
        for pts, closed in raw_subpaths:
            norm = list(pts)
            if closed and len(norm) >= 2 and self._shade_contour_gap(norm[0], norm[-1]) <= 1e-9:
                norm = norm[:-1]
            if len(norm) < (3 if closed else 2):
                continue
            subpaths.append((norm, closed))
        if not subpaths:
            return ([], False)
        if click_svg is None or len(subpaths) == 1:
            return subpaths[0]
        sx, sy = click_svg
        best_idx = 0
        best_dist = float("inf")
        for i, (pts, closed) in enumerate(subpaths):
            proj = self._project_point_on_polyline(pts, closed=closed, px=sx, py=sy)
            if proj is None:
                continue
            dist, _s, _px, _py, _total = proj
            if dist < best_dist:
                best_dist = dist
                best_idx = i
        return subpaths[best_idx]

    def _polyline_total_length(self, points: list[tuple[float, float]], *, closed: bool) -> float:
        if len(points) < 2:
            return 0.0
        n = len(points)
        seg_count = n if closed else n - 1
        total = 0.0
        for i in range(seg_count):
            ax, ay = points[i]
            bx, by = points[(i + 1) % n] if closed else points[i + 1]
            total += math.hypot(bx - ax, by - ay)
        return total

    def _project_point_on_polyline(
        self,
        points: list[tuple[float, float]],
        *,
        closed: bool,
        px: float,
        py: float,
    ) -> tuple[float, float, float, float, float] | None:
        if len(points) < 2:
            return None
        n = len(points)
        seg_count = n if closed else n - 1
        best: tuple[float, float, float, float] | None = None
        acc = 0.0
        for i in range(seg_count):
            ax, ay = points[i]
            bx, by = points[(i + 1) % n] if closed else points[i + 1]
            vx = bx - ax
            vy = by - ay
            seg_len2 = vx * vx + vy * vy
            seg_len = math.sqrt(seg_len2) if seg_len2 > 0 else 0.0
            if seg_len <= 1e-9:
                continue
            t = ((px - ax) * vx + (py - ay) * vy) / seg_len2
            t = max(0.0, min(1.0, t))
            qx = ax + t * vx
            qy = ay + t * vy
            dist = math.hypot(px - qx, py - qy)
            s = acc + t * seg_len
            if best is None or dist < best[0]:
                best = (dist, s, qx, qy)
            acc += seg_len
        if best is None:
            return None
        total_len = self._polyline_total_length(points, closed=closed)
        if total_len <= 1e-9:
            return None
        return (best[0], best[1], best[2], best[3], total_len)

    def _polyline_point_at_s(
        self,
        points: list[tuple[float, float]],
        *,
        closed: bool,
        s: float,
    ) -> tuple[float, float]:
        if not points:
            return (0.0, 0.0)
        if len(points) == 1:
            return points[0]
        n = len(points)
        total_len = self._polyline_total_length(points, closed=closed)
        if total_len <= 1e-9:
            return points[0]
        if closed:
            s_norm = s % total_len
        else:
            s_norm = max(0.0, min(total_len, s))
        acc = 0.0
        seg_count = n if closed else n - 1
        for i in range(seg_count):
            ax, ay = points[i]
            bx, by = points[(i + 1) % n] if closed else points[i + 1]
            seg_len = math.hypot(bx - ax, by - ay)
            if seg_len <= 1e-9:
                continue
            nxt = acc + seg_len
            if s_norm <= nxt + 1e-9:
                t = (s_norm - acc) / seg_len
                t = max(0.0, min(1.0, t))
                return (ax + t * (bx - ax), ay + t * (by - ay))
            acc = nxt
        return points[0] if closed else points[-1]

    def _polyline_points_between_s(
        self,
        points: list[tuple[float, float]],
        *,
        closed: bool,
        start_s: float,
        end_s: float,
    ) -> list[tuple[float, float]]:
        if len(points) < 2:
            return []
        total_len = self._polyline_total_length(points, closed=closed)
        if total_len <= 1e-9:
            return []
        if not closed:
            a = max(0.0, min(total_len, start_s))
            b = max(0.0, min(total_len, end_s))
            if b <= a + 1e-9:
                return []
            out = [self._polyline_point_at_s(points, closed=False, s=a)]
            acc = 0.0
            for i in range(len(points) - 1):
                seg_len = math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1])
                acc += seg_len
                if a + 1e-9 < acc < b - 1e-9:
                    if self._shade_contour_gap(out[-1], points[i + 1]) > 1e-9:
                        out.append(points[i + 1])
            end_pt = self._polyline_point_at_s(points, closed=False, s=b)
            if self._shade_contour_gap(out[-1], end_pt) > 1e-9:
                out.append(end_pt)
            return out

        a = start_s % total_len
        b = end_s % total_len
        if b <= a + 1e-9:
            b += total_len
        out = [self._polyline_point_at_s(points, closed=True, s=a)]
        n = len(points)
        verts: list[tuple[float, tuple[float, float]]] = []
        acc = 0.0
        for i in range(n):
            ax, ay = points[i]
            bx, by = points[(i + 1) % n]
            seg_len = math.hypot(bx - ax, by - ay)
            acc += seg_len
            verts.append((acc, (bx, by)))
        ext: list[tuple[float, tuple[float, float]]] = []
        for c, pt in verts:
            ext.append((c, pt))
            ext.append((c + total_len, pt))
        ext.sort(key=lambda item: item[0])
        for c, pt in ext:
            if a + 1e-9 < c < b - 1e-9:
                if self._shade_contour_gap(out[-1], pt) > 1e-9:
                    out.append(pt)
        end_pt = self._polyline_point_at_s(points, closed=True, s=b)
        if self._shade_contour_gap(out[-1], end_pt) > 1e-9:
            out.append(end_pt)
        return out

    def _curve_split_points(
        self,
        points: list[tuple[float, float]],
        *,
        closed: bool,
        tol: float,
        total_len: float,
    ) -> list[tuple[float, float, float]]:
        if self._svg_root is None or total_len <= 1e-9:
            return []
        raw: list[tuple[float, float, float]] = []
        for el in self._svg_root.iter():
            if _strip_ns(el.tag) != "circle":
                continue
            if not self._is_split_point_circle(el):
                continue
            cx = _parse_float(_get_attr(el, "cx"))
            cy = _parse_float(_get_attr(el, "cy"))
            pr = max(0.0, _parse_float(_get_attr(el, "r"), 0.0))
            proj = self._project_point_on_polyline(points, closed=closed, px=cx, py=cy)
            if proj is None:
                continue
            dist, s, qx, qy, _total = proj
            hit_tol = max(tol, pr)
            if dist > hit_tol:
                continue
            s_use = (s % total_len) if closed else s
            raw.append((s_use, qx, qy))
        if not closed:
            raw.append((0.0, points[0][0], points[0][1]))
            raw.append((total_len, points[-1][0], points[-1][1]))
        raw.sort(key=lambda item: item[0])
        out: list[tuple[float, float, float]] = []
        s_tol = max(1e-6, tol * 0.25)
        # For open curves, keep interior split points even when they are near
        # endpoints in geometric distance; otherwise a valid interior point can
        # be collapsed with 0/total and prevent the 2-subcurve split.
        if not closed:
            s_tol = 1e-6
        for s, px, py in raw:
            if not out:
                out.append((s, px, py))
                continue
            if abs(s - out[-1][0]) <= s_tol:
                continue
            out.append((s, px, py))
        if closed and len(out) >= 2:
            wrap_gap = (out[0][0] + total_len) - out[-1][0]
            if wrap_gap <= s_tol:
                out.pop()
        # Closed curves require at least 2 geometric split points to create
        # subcurves. Do not synthesize an opposite point from a single hit.
        return out

    def _curve_parent_token(self, curve_el: ET.Element) -> str:
        parent_id = (curve_el.get("id") or "").strip()
        if parent_id:
            return f"id:{parent_id}"
        tag = _strip_ns(curve_el.tag)
        if tag == "circle":
            cx = _format_num(_parse_float(_get_attr(curve_el, "cx")))
            cy = _format_num(_parse_float(_get_attr(curve_el, "cy")))
            r = _format_num(_parse_float(_get_attr(curve_el, "r")))
            return f"circle:{cx},{cy},{r}"
        if tag == "ellipse":
            cx = _format_num(_parse_float(_get_attr(curve_el, "cx")))
            cy = _format_num(_parse_float(_get_attr(curve_el, "cy")))
            rx = _format_num(_parse_float(_get_attr(curve_el, "rx")))
            ry = _format_num(_parse_float(_get_attr(curve_el, "ry")))
            return f"ellipse:{cx},{cy},{rx},{ry}"
        d = (_get_attr(curve_el, "d") or "").strip()
        return f"path:{d}"

    def _curve_subsegment_key(self, curve_el: ET.Element, start_s: float, end_s: float, total_len: float) -> str | None:
        if total_len <= 1e-9:
            return None
        span = end_s - start_s
        if span <= 1e-9:
            return None
        start_norm = start_s % total_len
        if span > total_len:
            span = total_len
        parent_token = self._curve_parent_token(curve_el)
        return f"{parent_token}:curve:{_format_num(start_norm)}:{_format_num(span)}"

    def _ensure_curve_subsegment(
        self,
        curve_el: ET.Element,
        points: list[tuple[float, float]],
        *,
        start_s: float,
        end_s: float,
        total_len: float,
    ) -> ET.Element | None:
        if self._svg_root is None:
            return None
        if len(points) < 2:
            return None
        key = self._curve_subsegment_key(curve_el, start_s, end_s, total_len)
        if not key:
            return None
        for el in self._svg_root.iter():
            if _strip_ns(el.tag) != "path":
                continue
            if (el.get("data-kind") or "").strip() != "subsegment":
                continue
            if (el.get("data-subsegment-key") or "") == key:
                return el
        d = self._shade_contour_path_d(points, closed=False)
        if not d:
            return None
        ns = ""
        tag = self._svg_root.tag
        if tag.startswith("{") and "}" in tag:
            ns = tag.split("}", 1)[0][1:]
        new_el = ET.Element(f"{{{ns}}}path") if ns else ET.Element("path")
        new_el.set("d", d)
        new_el.set("data-kind", "subsegment")
        new_el.set("data-subsegment-key", key)
        parent_id = self._ensure_element_id(curve_el, prefix="shape")
        new_el.set("data-parent-id", parent_id)
        if curve_el.get("class") is not None:
            new_el.set("class", curve_el.get("class") or "")
        src_hidden = (curve_el.get("data-hidden-parent") or "").strip() == "1"
        src_stroke = _get_attr(curve_el, "stroke")
        src_stroke_hidden = str(src_stroke or "").strip().lower() in ("", "none", "transparent")
        fallback_sw = _get_attr(curve_el, "stroke-width")
        if not str(fallback_sw or "").strip():
            fallback_sw = "1"
            if "_global_stroke_var" in self.__dict__:
                try:
                    raw_sw = (self._global_stroke_var.get() or "").strip()
                    if raw_sw:
                        float(raw_sw)
                        fallback_sw = raw_sw
                except Exception:
                    pass
        for name in ("stroke", "stroke-width", "stroke-dasharray", "marker-start", "marker-end"):
            val = _get_attr(curve_el, name)
            if name == "stroke" and src_hidden and src_stroke_hidden:
                val = "#000000"
            elif name == "stroke-width" and not str(val or "").strip():
                val = fallback_sw
            if val is not None:
                _set_attr(new_el, name, val)
        _set_attr(new_el, "fill", "none")
        self._svg_root.append(new_el)
        return new_el

    def _split_curve_on_points(
        self,
        curve_el: ET.Element,
        points: list[tuple[float, float]],
        *,
        closed: bool,
        split_points: list[tuple[float, float, float]],
        total_len: float,
    ) -> None:
        if self._svg_root is None:
            return
        if closed:
            if len(split_points) < 2:
                return
            count = len(split_points)
            for i in range(count):
                s0 = split_points[i][0]
                s1 = split_points[(i + 1) % count][0]
                if s1 <= s0:
                    s1 += total_len
                sub_pts = self._polyline_points_between_s(points, closed=True, start_s=s0, end_s=s1)
                self._ensure_curve_subsegment(curve_el, sub_pts, start_s=s0, end_s=s1, total_len=total_len)
        else:
            if len(split_points) < 3:
                return
            for i in range(len(split_points) - 1):
                s0 = split_points[i][0]
                s1 = split_points[i + 1][0]
                if s1 <= s0 + 1e-9:
                    continue
                sub_pts = self._polyline_points_between_s(points, closed=False, start_s=s0, end_s=s1)
                self._ensure_curve_subsegment(curve_el, sub_pts, start_s=s0, end_s=s1, total_len=total_len)
        curve_el.set("data-hidden-parent", "1")
        _force_style_attr(curve_el, "stroke", "none")

    def _select_subsegment_for_curve(
        self, curve_el: ET.Element, x: float, y: float, zoom: float, *, split: bool = False
    ) -> ET.Element | None:
        if self._svg_root is None or not self._is_curve_subsegment_parent(curve_el):
            return None
        sx, sy = self._canvas_to_svg(x, y, zoom)
        points, closed = self._curve_subsegment_points(curve_el, click_svg=(sx, sy))
        if len(points) < (3 if closed else 2):
            return None
        click_proj = self._project_point_on_polyline(points, closed=closed, px=sx, py=sy)
        if click_proj is None:
            return None
        _dist_click, s_click, _qx, _qy, total_len = click_proj
        if total_len <= 1e-9:
            return None
        tol = 5.0 / max(zoom, 1e-6)
        split_points = self._curve_split_points(points, closed=closed, tol=tol, total_len=total_len)
        if closed:
            if len(split_points) < 2:
                return None
        else:
            if len(split_points) < 3:
                return None
        if split:
            self._split_curve_on_points(
                curve_el,
                points,
                closed=closed,
                split_points=split_points,
                total_len=total_len,
            )
        if closed:
            n = len(split_points)
            s_click_mod = s_click % total_len
            idx = 0
            for i in range(n):
                s0 = split_points[i][0]
                s1 = split_points[(i + 1) % n][0]
                if s1 <= s0:
                    s1 += total_len
                sc = s_click_mod
                if sc < s0:
                    sc += total_len
                if s0 <= sc <= s1:
                    idx = i
                    break
            start_s = split_points[idx][0]
            end_s = split_points[(idx + 1) % n][0]
            if end_s <= start_s:
                end_s += total_len
            sub_pts = self._polyline_points_between_s(points, closed=True, start_s=start_s, end_s=end_s)
            return self._ensure_curve_subsegment(curve_el, sub_pts, start_s=start_s, end_s=end_s, total_len=total_len)
        idx = 0
        for i in range(len(split_points) - 1):
            s0 = split_points[i][0]
            s1 = split_points[i + 1][0]
            if s0 <= s_click <= s1:
                idx = i
                break
        start_s = split_points[idx][0]
        end_s = split_points[idx + 1][0]
        sub_pts = self._polyline_points_between_s(points, closed=False, start_s=start_s, end_s=end_s)
        return self._ensure_curve_subsegment(curve_el, sub_pts, start_s=start_s, end_s=end_s, total_len=total_len)

    def _split_line_on_points(
        self, line_el: ET.Element, points: list[tuple[float, float, float]]
    ) -> None:
        if self._svg_root is None:
            return
        if len(points) < 3:
            return
        for i in range(len(points) - 1):
            ax, ay = points[i][1], points[i][2]
            bx, by = points[i + 1][1], points[i + 1][2]
            self._ensure_subsegment(line_el, ax, ay, bx, by)
        line_el.set("data-hidden-parent", "1")
        _force_style_attr(line_el, "stroke", "none")

    def _points_on_line(
        self, line_el: ET.Element, *, tol_px: float, zoom: float
    ) -> list[tuple[float, float, float]]:
        x1 = _parse_float(_get_attr(line_el, "x1"))
        y1 = _parse_float(_get_attr(line_el, "y1"))
        x2 = _parse_float(_get_attr(line_el, "x2"))
        y2 = _parse_float(_get_attr(line_el, "y2"))
        vx = x2 - x1
        vy = y2 - y1
        denom = vx * vx + vy * vy
        if denom <= 1e-12:
            return [(0.0, x1, y1), (1.0, x2, y2)]
        tol = tol_px / max(zoom, 1e-6)
        pts: list[tuple[float, float, float]] = []
        pts.append((0.0, x1, y1))
        pts.append((1.0, x2, y2))
        for el in self._svg_root.iter():
            if _strip_ns(el.tag) != "circle":
                continue
            if not self._is_split_point_circle(el):
                continue
            cx = _parse_float(_get_attr(el, "cx"))
            cy = _parse_float(_get_attr(el, "cy"))
            pr = max(0.0, _parse_float(_get_attr(el, "r"), 0.0))
            t = ((cx - x1) * vx + (cy - y1) * vy) / denom
            if t < 0.0 or t > 1.0:
                continue
            dist = self._dist_point_to_segment(cx, cy, x1, y1, x2, y2)
            hit_tol = max(tol, pr)
            if dist <= hit_tol:
                pts.append((t, cx, cy))
        pts.sort(key=lambda item: item[0])
        dedup: list[tuple[float, float, float]] = []
        for t, px, py in pts:
            if not dedup:
                dedup.append((t, px, py))
                continue
            if abs(t - dedup[-1][0]) <= 1e-6:
                continue
            dedup.append((t, px, py))
        return dedup

    def _subsegment_key(
        self, line_el: ET.Element, ax: float, ay: float, bx: float, by: float
    ) -> str:
        pid = line_el.get("id")
        if not pid:
            pid = ",".join(
                [
                    _get_attr(line_el, "x1") or "",
                    _get_attr(line_el, "y1") or "",
                    _get_attr(line_el, "x2") or "",
                    _get_attr(line_el, "y2") or "",
                ]
            )
        return f"{pid}:{_format_num(ax)},{_format_num(ay)}:{_format_num(bx)},{_format_num(by)}"

    def _ensure_subsegment(
        self, line_el: ET.Element, ax: float, ay: float, bx: float, by: float
    ) -> ET.Element | None:
        if self._svg_root is None:
            return None
        parent_id = self._ensure_element_id(line_el, prefix="shape")
        key = self._subsegment_key(line_el, ax, ay, bx, by)
        for el in self._svg_root.iter():
            if _strip_ns(el.tag) != "line":
                continue
            if el.get("data-kind") != "subsegment":
                continue
            if el.get("data-subsegment-key") == key:
                return el
        ns = ""
        tag = self._svg_root.tag
        if tag.startswith("{") and "}" in tag:
            ns = tag.split("}", 1)[0][1:]
        new_el = ET.Element(f"{{{ns}}}line") if ns else ET.Element("line")
        new_el.set("x1", _format_num(ax))
        new_el.set("y1", _format_num(ay))
        new_el.set("x2", _format_num(bx))
        new_el.set("y2", _format_num(by))
        new_el.set("data-kind", "subsegment")
        new_el.set("data-subsegment-key", key)
        new_el.set("data-parent-id", parent_id)
        if line_el.get("class") is not None:
            new_el.set("class", line_el.get("class") or "")
        src_hidden = (line_el.get("data-hidden-parent") or "").strip() == "1"
        src_stroke = _get_attr(line_el, "stroke")
        src_stroke_hidden = str(src_stroke or "").strip().lower() in ("", "none", "transparent")
        fallback_sw = _get_attr(line_el, "stroke-width")
        if not str(fallback_sw or "").strip():
            fallback_sw = "1"
            if "_global_stroke_var" in self.__dict__:
                try:
                    raw_sw = (self._global_stroke_var.get() or "").strip()
                    if raw_sw:
                        float(raw_sw)
                        fallback_sw = raw_sw
                except Exception:
                    pass
        for name in ("stroke", "stroke-width", "stroke-dasharray", "marker-start", "marker-end"):
            val = _get_attr(line_el, name)
            if name == "stroke" and src_hidden and src_stroke_hidden:
                val = "#000000"
            elif name == "stroke-width" and not str(val or "").strip():
                val = fallback_sw
            if val is not None:
                _set_attr(new_el, name, val)
        self._svg_root.append(new_el)
        return new_el

    def _subsegment_parent_prefix(self, line_el: ET.Element) -> str:
        pid = line_el.get("id")
        if not pid:
            pid = ",".join(
                [
                    _get_attr(line_el, "x1") or "",
                    _get_attr(line_el, "y1") or "",
                    _get_attr(line_el, "x2") or "",
                    _get_attr(line_el, "y2") or "",
                ]
            )
        return f"{pid}:"

    def _iter_subsegments_for_parent(self, line_el: ET.Element) -> list[ET.Element]:
        if self._svg_root is None:
            return []
        prefix = self._subsegment_parent_prefix(line_el)
        matches: list[ET.Element] = []
        for el in self._svg_root.iter():
            if _strip_ns(el.tag) != "line":
                continue
            if el.get("data-kind") != "subsegment":
                continue
            key = el.get("data-subsegment-key", "")
            if key.startswith(prefix):
                matches.append(el)
        return matches

    def _apply_line_style_to(self, src: ET.Element, dst: ET.Element) -> None:
        if (dst.get("data-kind") or "").strip() == "subsegment" and dst.get("data-subsegment-override") == "1":
            return
        src_hidden = src.get("data-hidden-parent") == "1"
        if src.get("class") is not None:
            dst.set("class", src.get("class") or "")
        elif dst.get("class") is not None:
            del dst.attrib["class"]
        for name in ("stroke", "stroke-width", "stroke-dasharray", "marker-start", "marker-end"):
            val = _get_attr(src, name)
            if name == "stroke" and src_hidden:
                if val is None:
                    continue
                if str(val).strip().lower() in ("none", "transparent", ""):
                    continue
            if val is None:
                if name in dst.attrib:
                    del dst.attrib[name]
                _remove_style_attr(dst, name)
                continue
            _set_attr(dst, name, val)

    def _sync_subsegments_from_parent(self, line_el: ET.Element) -> None:
        for sub_el in self._iter_subsegments_for_parent(line_el):
            self._apply_line_style_to(line_el, sub_el)

    def _repair_hidden_subsegment_orphans(self) -> None:
        if self._svg_root is None:
            return
        parent_ids: set[str] = set()
        for el in self._svg_root.iter():
            if (el.get("data-kind") or "").strip() != "subsegment":
                continue
            pid = (el.get("data-parent-id") or "").strip()
            if pid:
                parent_ids.add(pid)
        for el in self._svg_root.iter():
            if (el.get("data-kind") or "").strip() != "subsegment":
                continue
            if (el.get("data-hidden-parent") or "").strip() != "1":
                continue
            el_id = (el.get("id") or "").strip()
            if not el_id:
                continue
            if el_id in parent_ids:
                continue
            el.attrib.pop("data-hidden-parent", None)
            stroke = (_get_attr(el, "stroke") or "").strip().lower()
            if stroke in ("", "none", "transparent"):
                _set_attr(el, "stroke", "#000000")
            raw_sw = (_get_attr(el, "stroke-width") or "").strip()
            try:
                sw = float(raw_sw) if raw_sw else 0.0
            except Exception:
                sw = 0.0
            if sw <= 0.0:
                sw_txt = "1"
                if "_global_stroke_var" in self.__dict__:
                    try:
                        raw = (self._global_stroke_var.get() or "").strip()
                        if raw:
                            float(raw)
                            sw_txt = raw
                    except Exception:
                        pass
                _set_attr(el, "stroke-width", sw_txt)
            if _strip_ns(el.tag) == "path":
                fill = (_get_attr(el, "fill") or "").strip().lower()
                if fill in ("", "transparent"):
                    _set_attr(el, "fill", "none")

    def _sync_all_subsegments(self) -> None:
        if self._svg_root is None:
            return
        parents_by_id: dict[str, ET.Element] = {}
        for el in self._svg_root.iter():
            tag = _strip_ns(el.tag)
            if tag not in ("line", "path", "circle", "ellipse"):
                continue
            kind = (el.get("data-kind") or "").strip()
            if kind in ("subsegment", "circle-radius", _CURVE_RADIUS_DATA_KIND):
                continue
            if tag == "path" and el.get("data-text") is not None:
                continue
            el_id = el.get("id")
            if el_id:
                parents_by_id[el_id] = el
        for el in self._svg_root.iter():
            if (el.get("data-kind") or "").strip() != "subsegment":
                continue
            parent_id = (el.get("data-parent-id") or "").strip()
            if not parent_id:
                continue
            parent = parents_by_id.get(parent_id)
            if parent is not None:
                self._apply_line_style_to(parent, el)
        for el in self._svg_root.iter():
            if _strip_ns(el.tag) != "line":
                continue
            kind = (el.get("data-kind") or "").strip()
            if kind in ("subsegment", "circle-radius", _CURVE_RADIUS_DATA_KIND):
                continue
            self._sync_subsegments_from_parent(el)

    def _segment_mark_key(self, line_el: ET.Element, *, create: bool = True) -> str | None:
        key = line_el.get("data-mark-key")
        if key:
            return key
        pid = line_el.get("id")
        if pid:
            key = f"id:{pid}"
        else:
            try:
                x1 = _get_attr(line_el, "x1") or ""
                y1 = _get_attr(line_el, "y1") or ""
                x2 = _get_attr(line_el, "x2") or ""
                y2 = _get_attr(line_el, "y2") or ""
                key = f"coords:{x1},{y1},{x2},{y2}"
            except Exception:
                key = None
        if key and create:
            line_el.set("data-mark-key", key)
        return key

    def _segment_endpoint_key(self, line_el: ET.Element, *, create: bool = True) -> str | None:
        key = line_el.get("data-endpoint-key")
        if key:
            return key
        key = line_el.get("data-mark-key") or self._segment_mark_key(line_el, create=True)
        if key and create:
            line_el.set("data-endpoint-key", key)
        return key

    def _segment_mid_key(self, line_el: ET.Element, *, create: bool = True) -> str | None:
        key = line_el.get("data-mid-key")
        if key:
            return key
        key = line_el.get("data-mark-key") or self._segment_mark_key(line_el, create=True)
        if key and create:
            line_el.set("data-mid-key", key)
        return key

    def _next_circle_id(self) -> str:
        if self._svg_root is None:
            return "circ-1"
        max_idx = 0
        for el in self._svg_root.iter():
            raw = (el.get("data-circle-id") or "").strip()
            if not raw.startswith("circ-"):
                continue
            try:
                idx = int(raw[5:])
            except Exception:
                continue
            if idx > max_idx:
                max_idx = idx
        return f"circ-{max_idx + 1}"

    def _circle_key(self, circle_el: ET.Element, *, create: bool = True) -> str | None:
        key = circle_el.get("data-circle-key")
        if key:
            return key
        cid = circle_el.get("id")
        if cid:
            key = f"id:{cid}"
        else:
            cid = circle_el.get("data-circle-id")
            if not cid and create:
                cid = self._next_circle_id()
                circle_el.set("data-circle-id", cid)
            if cid:
                key = f"cid:{cid}"
        if key and create:
            circle_el.set("data-circle-key", key)
        return key

    def _circle_radius_present(self, key: str) -> bool:
        if self._svg_root is None:
            return False
        for el in self._svg_root.iter():
            if (el.get("data-kind") or "").strip() != "circle-radius":
                continue
            if el.get("data-parent-key") == key:
                return True
        return False

    def _symmetric_offsets(self, count: int, step: float) -> list[float]:
        if count <= 0:
            return []
        offsets: list[float] = []
        if count % 2 == 1:
            offsets.append(0.0)
            k = 1
            while len(offsets) < count:
                offsets.append(step * k)
                if len(offsets) >= count:
                    break
                offsets.append(-step * k)
                k += 1
        else:
            k = 0
            while len(offsets) < count:
                offsets.append(step * (2 * k + 1) * 0.5)
                if len(offsets) >= count:
                    break
                offsets.append(-step * (2 * k + 1) * 0.5)
                k += 1
        return offsets

    def _centered_half_offsets(self, count: int, step: float) -> list[float]:
        if count <= 0:
            return []
        step = abs(step)
        if step <= 1e-9:
            return [0.0 for _ in range(count)]
        offsets: list[float] = []
        if count % 2 == 1:
            offsets.append(0.0)
        k = 0
        half = 0.5 * step
        while len(offsets) < count:
            val = (2 * k + 1) * half
            offsets.append(val)
            if len(offsets) >= count:
                break
            offsets.append(-val)
            k += 1
        return offsets

    def _count_segment_marks(self, line_el: ET.Element) -> int:
        if self._svg_root is None:
            return 0
        key = self._segment_mark_key(line_el, create=False)
        if not key:
            return 0
        count = 0
        for el in self._svg_root.iter():
            if el.get("data-kind") != "seg-mark":
                continue
            if el.get("data-parent-key") == key:
                count += 1
        return count

    def _remove_segment_marks(self, line_el: ET.Element) -> None:
        if self._svg_root is None:
            return
        key = self._segment_mark_key(line_el, create=False)
        if not key:
            return
        to_remove: list[ET.Element] = []
        for el in self._svg_root.iter():
            if el.get("data-kind") != "seg-mark":
                continue
            if el.get("data-parent-key") == key:
                to_remove.append(el)
        for el in to_remove:
            parent = self._parent_of(el)
            if parent is not None:
                try:
                    parent.remove(el)
                except Exception:
                    pass

    def _remove_segment_endpoints(self, line_el: ET.Element) -> None:
        if self._svg_root is None:
            return
        key = self._segment_endpoint_key(line_el, create=False)
        if not key:
            return
        to_remove: list[ET.Element] = []
        for el in self._svg_root.iter():
            if el.get("data-kind") not in ("seg-endpoint", "seg-endpoint-label"):
                continue
            if el.get("data-parent-key") == key:
                to_remove.append(el)
        for el in to_remove:
            parent = self._parent_of(el)
            if parent is not None:
                try:
                    parent.remove(el)
                except Exception:
                    pass

    def _remove_segment_mid_labels(self, line_el: ET.Element) -> None:
        if self._svg_root is None:
            return
        key = self._segment_mid_key(line_el, create=False)
        if not key:
            return
        to_remove: list[ET.Element] = []
        for el in self._svg_root.iter():
            if el.get("data-kind") != "seg-mid-label":
                continue
            if el.get("data-parent-key") == key:
                to_remove.append(el)
        for el in to_remove:
            parent = self._parent_of(el)
            if parent is not None:
                try:
                    parent.remove(el)
                except Exception:
                    pass

    def _remove_circle_radius(self, circle_el: ET.Element) -> None:
        if self._svg_root is None:
            return
        key = self._circle_key(circle_el, create=False)
        if not key:
            return
        to_remove: list[ET.Element] = []
        for el in self._svg_root.iter():
            if (el.get("data-kind") or "").strip() != "circle-radius":
                continue
            if el.get("data-parent-key") == key:
                to_remove.append(el)
        for el in to_remove:
            parent = self._parent_of(el)
            if parent is not None:
                try:
                    parent.remove(el)
                except Exception:
                    pass

    def _circle_for_radius_line(self, radius_el: ET.Element) -> ET.Element | None:
        if self._svg_root is None:
            return None
        if _strip_ns(radius_el.tag) != "line":
            return None
        if (radius_el.get("data-kind") or "").strip() != "circle-radius":
            return None
        key = (radius_el.get("data-parent-key") or "").strip()
        if not key:
            return None
        for el in self._svg_root.iter():
            if _strip_ns(el.tag) != "circle":
                continue
            if not self._is_editable_circle(el):
                continue
            if self._circle_key(el, create=False) == key:
                return el
        return None

    def _create_circle_radius(self, circle_el: ET.Element) -> ET.Element | None:
        if self._svg_root is None:
            return None
        key = self._circle_key(circle_el, create=True)
        if not key:
            return None
        try:
            cx = _parse_float(_get_attr(circle_el, "cx"))
            cy = _parse_float(_get_attr(circle_el, "cy"))
            r = _parse_float(_get_attr(circle_el, "r"))
        except Exception:
            return None
        if r <= 0:
            return None
        ns = ""
        tag = self._svg_root.tag
        if tag.startswith("{") and "}" in tag:
            ns = tag.split("}", 1)[0][1:]
        line_el = ET.Element(f"{{{ns}}}line") if ns else ET.Element("line")
        line_el.set("x1", _format_num(cx))
        line_el.set("y1", _format_num(cy))
        line_el.set("x2", _format_num(cx + r))
        line_el.set("y2", _format_num(cy))
        stroke = _get_attr(circle_el, "stroke") or "#000000"
        if str(stroke).strip().lower() in ("", "none", "transparent"):
            stroke = "#000000"
        stroke_w = _get_attr(circle_el, "stroke-width") or (self._global_stroke_var.get().strip() or "2")
        _set_attr(line_el, "stroke", stroke)
        _set_attr(line_el, "stroke-width", stroke_w)
        _set_attr(line_el, "fill", "none")
        line_el.set("data-kind", "circle-radius")
        line_el.set("data-parent-key", key)
        line_el.set("data-radius-angle", _format_num(0.0))
        return line_el

    def _sync_circle_radius_from_circle(self, circle_el: ET.Element) -> None:
        if self._svg_root is None:
            return
        show = (circle_el.get("data-radius-show") or "").strip() == "1"
        if not show:
            self._remove_circle_radius(circle_el)
            return
        key = self._circle_key(circle_el, create=True)
        if not key:
            return
        existing: list[ET.Element] = []
        for el in self._svg_root.iter():
            if (el.get("data-kind") or "").strip() != "circle-radius":
                continue
            if (el.get("data-parent-key") or "").strip() != key:
                continue
            existing.append(el)
        radius_el: ET.Element | None = existing[0] if existing else None
        for dup in existing[1:]:
            parent = self._parent_of(dup)
            if parent is not None:
                try:
                    parent.remove(dup)
                except Exception:
                    pass
        if radius_el is None:
            radius_el = self._create_circle_radius(circle_el)
            if radius_el is None:
                return
            self._svg_root.append(radius_el)
        cx = _parse_float(_get_attr(circle_el, "cx"))
        cy = _parse_float(_get_attr(circle_el, "cy"))
        r = _parse_float(_get_attr(circle_el, "r"), 0.0)
        if r <= 0:
            return
        ang = _parse_float((radius_el.get("data-radius-angle") or "").strip(), float("nan"))
        if not math.isfinite(ang):
            # Legacy radii without explicit angle keep deterministic default
            # orientation (East). Drag updates always persist data-radius-angle.
            ang = 0.0
        qx = cx + r * math.cos(ang)
        qy = cy + r * math.sin(ang)
        radius_el.set("x1", _format_num(cx))
        radius_el.set("y1", _format_num(cy))
        radius_el.set("x2", _format_num(qx))
        radius_el.set("y2", _format_num(qy))
        radius_el.set("data-kind", "circle-radius")
        radius_el.set("data-parent-key", key)
        radius_el.set("data-radius-angle", _format_num(ang))

    def _sync_circle_radii(self) -> None:
        if self._svg_root is None:
            return
        show_keys: set[str] = set()
        circles = [el for el in self._svg_root.iter() if _strip_ns(el.tag) == "circle"]
        for el in circles:
            if not self._is_editable_circle(el):
                continue
            key = self._circle_key(el, create=False)
            show = (el.get("data-radius-show") or "").strip() == "1"
            if show:
                if key is None:
                    key = self._circle_key(el, create=True)
                if key:
                    show_keys.add(key)
                self._sync_circle_radius_from_circle(el)
        to_remove: list[ET.Element] = []
        for el in self._svg_root.iter():
            if (el.get("data-kind") or "").strip() != "circle-radius":
                continue
            key = el.get("data-parent-key") or ""
            if key not in show_keys:
                to_remove.append(el)
        for el in to_remove:
            parent = self._parent_of(el)
            if parent is not None:
                try:
                    parent.remove(el)
                except Exception:
                    pass

    def _sync_curve_radii(self) -> None:
        if self._svg_root is None:
            return
        to_remove: list[ET.Element] = []
        for el in self._svg_root.iter():
            if _strip_ns(el.tag) != "line":
                continue
            if (el.get("data-kind") or "").strip() != _CURVE_RADIUS_DATA_KIND:
                continue
            center_id = (el.get("data-radius-center-id") or "").strip()
            curve_id = (el.get("data-radius-curve-id") or "").strip()
            center_el = self._element_by_id(center_id)
            curve_el = self._element_by_id(curve_id)
            if center_el is None or curve_el is None:
                to_remove.append(el)
                continue
            if _strip_ns(center_el.tag) != "circle" or not self._is_point_circle(center_el):
                to_remove.append(el)
                continue
            if not self._is_curve_radius_curve_candidate(curve_el):
                to_remove.append(el)
                continue
            cx = _parse_float(_get_attr(center_el, "cx"))
            cy = _parse_float(_get_attr(center_el, "cy"))
            ref_x = _parse_float(_get_attr(el, "x2"), cx)
            ref_y = _parse_float(_get_attr(el, "y2"), cy)
            points, closed = self._curve_subsegment_points(curve_el, click_svg=(ref_x, ref_y))
            if len(points) < 2:
                to_remove.append(el)
                continue
            proj = self._project_point_on_polyline(points, closed=closed, px=ref_x, py=ref_y)
            if proj is None:
                to_remove.append(el)
                continue
            total_len = proj[4]
            if total_len <= 1e-9:
                to_remove.append(el)
                continue
            s_raw = _parse_float((el.get("data-radius-s") or "").strip(), proj[1])
            if closed:
                s_use = s_raw % total_len
            else:
                s_use = max(0.0, min(total_len, s_raw))
            qx, qy = self._polyline_point_at_s(points, closed=closed, s=s_use)
            el.set("x1", _format_num(cx))
            el.set("y1", _format_num(cy))
            el.set("x2", _format_num(qx))
            el.set("y2", _format_num(qy))
            el.set("data-kind", _CURVE_RADIUS_DATA_KIND)
            el.set("data-radius-center-id", center_id)
            el.set("data-radius-curve-id", curve_id)
            el.set("data-radius-s", _format_num(s_use))
        for el in to_remove:
            parent = self._parent_of(el)
            if parent is not None:
                try:
                    parent.remove(el)
                except Exception:
                    pass

    def _create_segment_endpoint_circle(
        self, x: float, y: float, r: float, key: str, role: str
    ) -> ET.Element | None:
        if self._svg_root is None:
            return None
        ns = ""
        tag = self._svg_root.tag
        if tag.startswith("{") and "}" in tag:
            ns = tag.split("}", 1)[0][1:]
        circle = ET.Element(f"{{{ns}}}circle") if ns else ET.Element("circle")
        circle.set("cx", _format_num(x))
        circle.set("cy", _format_num(y))
        circle.set("r", _format_num(r))
        _set_attr(circle, "fill", "#000000")
        _set_attr(circle, "stroke", "none")
        circle.set("data-kind", "seg-endpoint")
        circle.set("data-parent-key", key)
        circle.set("data-endpoint-role", role)
        return circle

    def _create_segment_endpoint_label(
        self,
        text: str,
        ax: float,
        ay: float,
        dir_s: str,
        offset: float,
        font_size: float,
        key: str,
        role: str,
    ) -> ET.Element | None:
        label_el = self._create_latex_label(
            text,
            ax,
            ay,
            font_size,
            "#000000",
            dir_s=dir_s,
            anchor=(ax, ay),
            offset=offset,
        )
        if label_el is None:
            return None
        label_el.set("data-kind", "seg-endpoint-label")
        label_el.set("data-parent-key", key)
        label_el.set("data-endpoint-role", role)
        return label_el

    def _create_segment_mid_label(
        self,
        text: str,
        ax: float,
        ay: float,
        dir_s: str,
        offset: float,
        font_size: float,
        key: str,
    ) -> ET.Element | None:
        label_el = self._create_latex_label(
            text,
            ax,
            ay,
            font_size,
            "#000000",
            dir_s=dir_s,
            anchor=(ax, ay),
            offset=offset,
        )
        if label_el is None:
            return None
        label_el.set("data-kind", "seg-mid-label")
        label_el.set("data-parent-key", key)
        return label_el

    def _sync_segment_marks_from_editor(self, line_el: ET.Element) -> None:
        if self._segment_mark_updating:
            return
        style_raw = self._segment_mark_style_var.get().strip().lower()
        style = "puntos"
        if style_raw in ("none", "ninguno", "nonce"):
            style = "none"
        elif style_raw in ("puntos", "punto", "pts", "circulos", "circles"):
            style = "puntos"
        elif style_raw in ("rectangulo", "rect", "rectang"):
            style = "rectangulo"
        elif style_raw in ("sinusoidal", "seno", "sine", "onda"):
            style = "sinusoidal"
        elif style_raw in ("s", "forma s", "s-shape", "s_shape"):
            style = "s"
        raw = self._segment_mark_count_var.get().strip()
        try:
            count = int(raw)
        except Exception:
            count = 0
        if count < 0:
            count = 0
        if count > 25:
            count = 25
        if raw != str(count):
            self._segment_mark_updating = True
            try:
                self._segment_mark_count_var.set(str(count))
            finally:
                self._segment_mark_updating = False
        if style == "none":
            self._segment_mark_updating = True
            try:
                self._segment_mark_count_var.set("0")
            finally:
                self._segment_mark_updating = False
            if "data-mark-count" in line_el.attrib:
                del line_el.attrib["data-mark-count"]
            if "data-mark-style" in line_el.attrib:
                del line_el.attrib["data-mark-style"]
            self._remove_segment_marks(line_el)
            return
        line_el.set("data-mark-style", style)
        line_el.set("data-mark-count", str(count))
        line_el.set("data-mark-radius", self._segment_mark_radius_var.get().strip() or "3")
        line_el.set("data-mark-rect-w", self._segment_mark_rect_w_var.get().strip() or "8")
        line_el.set("data-mark-rect-h", self._segment_mark_rect_h_var.get().strip() or "4")
        line_el.set("data-mark-rect-fill", "1" if self._segment_mark_rect_fill_var.get() else "0")
        line_el.set("data-mark-amp", self._segment_mark_amp_var.get().strip() or "6")
        line_el.set("data-mark-length", self._segment_mark_length_var.get().strip() or "40")
        line_el.set("data-mark-cycles", self._segment_mark_cycles_var.get().strip() or "2")
        line_el.set("data-mark-gap", self._segment_mark_gap_var.get().strip() or "6")
        self._remove_segment_marks(line_el)
        if count <= 0:
            return
        self._create_segment_marks(line_el, count)

    def _sync_segment_endpoints_from_editor(self, line_el: ET.Element) -> None:
        if self._svg_root is None:
            return
        key = self._segment_endpoint_key(line_el, create=True)
        if not key:
            return
        target = (self._segment_endpoint_target_var.get() or "inicio").strip().lower()
        text = self._segment_endpoint_label_var.get().strip()
        raw_dir = self._segment_endpoint_dir_var.get().strip()
        raw_offset = self._segment_endpoint_offset_var.get().strip()
        label_bg_mode = self._segment_endpoint_bg_mode_selected()
        dir_s = _normalize_dir_input(raw_dir)
        if dir_s and dir_s != raw_dir:
            self._segment_endpoint_dir_var.set(dir_s)
        if not _is_valid_dir(dir_s):
            dir_s = ""
        if target not in ("inicio", "fin"):
            target = "inicio"
        offset = None
        if raw_offset:
            try:
                offset = float(raw_offset)
            except Exception:
                return
        if offset is not None and offset < 0:
            return

        if target == "inicio":
            if text:
                line_el.set("data-endpoint-label-a", text)
            else:
                line_el.attrib.pop("data-endpoint-label-a", None)
            if dir_s:
                line_el.set("data-endpoint-dir-a", dir_s)
            else:
                line_el.attrib.pop("data-endpoint-dir-a", None)
            if offset is not None:
                line_el.set("data-endpoint-offset-a", _format_num(offset))
            else:
                line_el.attrib.pop("data-endpoint-offset-a", None)
            if text and label_bg_mode == _LABEL_BG_MODE_WHITE:
                line_el.set("data-endpoint-bg-a", "1")
            else:
                line_el.attrib.pop("data-endpoint-bg-a", None)
            line_el.set("data-endpoint-bg-mode-a", label_bg_mode if text else _LABEL_BG_MODE_NONE)
            line_el.attrib.pop("data-endpoint-label-b", None)
            line_el.attrib.pop("data-endpoint-dir-b", None)
            line_el.attrib.pop("data-endpoint-offset-b", None)
            line_el.attrib.pop("data-endpoint-bg-b", None)
            line_el.attrib.pop("data-endpoint-bg-mode-b", None)
        else:
            if text:
                line_el.set("data-endpoint-label-b", text)
            else:
                line_el.attrib.pop("data-endpoint-label-b", None)
            if dir_s:
                line_el.set("data-endpoint-dir-b", dir_s)
            else:
                line_el.attrib.pop("data-endpoint-dir-b", None)
            if offset is not None:
                line_el.set("data-endpoint-offset-b", _format_num(offset))
            else:
                line_el.attrib.pop("data-endpoint-offset-b", None)
            if text and label_bg_mode == _LABEL_BG_MODE_WHITE:
                line_el.set("data-endpoint-bg-b", "1")
            else:
                line_el.attrib.pop("data-endpoint-bg-b", None)
            line_el.set("data-endpoint-bg-mode-b", label_bg_mode if text else _LABEL_BG_MODE_NONE)
            line_el.attrib.pop("data-endpoint-label-a", None)
            line_el.attrib.pop("data-endpoint-dir-a", None)
            line_el.attrib.pop("data-endpoint-offset-a", None)
            line_el.attrib.pop("data-endpoint-bg-a", None)
            line_el.attrib.pop("data-endpoint-bg-mode-a", None)

        self._remove_segment_endpoints(line_el)
        if not text or not dir_s:
            return
        try:
            x1 = _parse_float(_get_attr(line_el, "x1"))
            y1 = _parse_float(_get_attr(line_el, "y1"))
            x2 = _parse_float(_get_attr(line_el, "x2"))
            y2 = _parse_float(_get_attr(line_el, "y2"))
        except Exception:
            return
        ax, ay = (x1, y1) if target == "inicio" else (x2, y2)
        font_size = _parse_float(self._global_font_size_var.get().strip(), 15.0)
        if offset is None:
            offset = _parse_float(self._global_label_offset_var.get().strip(), 10.0)
        lx, ly = _label_position_from_anchor(ax, ay, text, dir_s, offset, font_size, True)
        role = "start" if target == "inicio" else "end"
        lbl = self._create_segment_endpoint_label(text, lx, ly, dir_s, offset, font_size, key, role)
        if lbl is not None:
            self._set_label_bg_mode(lbl, label_bg_mode)
            self._svg_root.append(lbl)

    def _sync_segment_mid_labels_from_editor(self, line_el: ET.Element) -> None:
        if self._svg_root is None:
            return
        key = self._segment_mid_key(line_el, create=True)
        if not key:
            return
        text = self._segment_mid_label_var.get().strip()
        raw_dir = self._segment_mid_dir_var.get().strip()
        raw_offset = self._segment_mid_offset_var.get().strip()
        label_bg_mode = self._segment_mid_bg_mode_selected()
        dir_s = _normalize_dir_input(raw_dir)
        if dir_s and dir_s != raw_dir:
            self._segment_mid_dir_var.set(dir_s)
        if not _is_valid_dir(dir_s):
            dir_s = ""
        offset = None
        if raw_offset:
            try:
                offset = float(raw_offset)
            except Exception:
                return
        if offset is not None and offset < 0:
            return
        if text:
            line_el.set("data-mid-label", text)
        else:
            line_el.attrib.pop("data-mid-label", None)
        if dir_s:
            line_el.set("data-mid-dir", dir_s)
        else:
            line_el.attrib.pop("data-mid-dir", None)
        if offset is not None:
            line_el.set("data-mid-offset", _format_num(offset))
        else:
            line_el.attrib.pop("data-mid-offset", None)
        if text and label_bg_mode == _LABEL_BG_MODE_WHITE:
            line_el.set("data-mid-bg", "1")
        else:
            line_el.attrib.pop("data-mid-bg", None)
        line_el.set("data-mid-bg-mode", label_bg_mode if text else _LABEL_BG_MODE_NONE)

        self._remove_segment_mid_labels(line_el)
        if not text or not dir_s:
            return
        try:
            x1 = _parse_float(_get_attr(line_el, "x1"))
            y1 = _parse_float(_get_attr(line_el, "y1"))
            x2 = _parse_float(_get_attr(line_el, "x2"))
            y2 = _parse_float(_get_attr(line_el, "y2"))
        except Exception:
            return
        mx = (x1 + x2) * 0.5
        my = (y1 + y2) * 0.5
        font_size = _parse_float(self._global_font_size_var.get().strip(), 15.0)
        if offset is None:
            offset = _parse_float(self._global_label_offset_var.get().strip(), 10.0)
        lx, ly = _label_position_from_anchor(mx, my, text, dir_s, offset, font_size, True)
        lbl = self._create_segment_mid_label(text, lx, ly, dir_s, offset, font_size, key)
        if lbl is not None:
            self._set_label_bg_mode(lbl, label_bg_mode)
            self._svg_root.append(lbl)

    def _create_segment_marks(self, line_el: ET.Element, count: int) -> None:
        if self._svg_root is None or count <= 0:
            return
        key = self._segment_mark_key(line_el, create=True)
        if not key:
            return
        try:
            x1 = _parse_float(_get_attr(line_el, "x1"))
            y1 = _parse_float(_get_attr(line_el, "y1"))
            x2 = _parse_float(_get_attr(line_el, "x2"))
            y2 = _parse_float(_get_attr(line_el, "y2"))
        except Exception:
            return
        dx = x2 - x1
        dy = y2 - y1
        length = math.hypot(dx, dy)
        if length <= 1e-6:
            return
        ux = dx / length
        uy = dy / length
        nx = -uy
        ny = ux
        stroke_w = _parse_float(_get_attr(line_el, "stroke-width"), 1.0)
        stroke = _get_attr(line_el, "stroke") or "#000000"
        if str(stroke).strip().lower() in ("", "none", "transparent"):
            stroke = "#000000"
        style = (line_el.get("data-mark-style") or "puntos").strip().lower()
        radius = _parse_float(line_el.get("data-mark-radius"), 3.0)
        rect_w = _parse_float(line_el.get("data-mark-rect-w"), 8.0)
        rect_h = _parse_float(line_el.get("data-mark-rect-h"), 4.0)
        rect_fill = line_el.get("data-mark-rect-fill") == "1"
        amp = _parse_float(line_el.get("data-mark-amp"), 6.0)
        mark_len = _parse_float(line_el.get("data-mark-length"), 40.0)
        cycles = int(_parse_float(line_el.get("data-mark-cycles"), 2.0))
        gap = _parse_float(line_el.get("data-mark-gap"), 6.0)
        cycles = max(1, cycles)
        if style == "puntos":
            step = 2.0 * max(radius, 0.1)
        elif style == "rectangulo":
            step = max(rect_h, 1.0) + max(gap, 0.0)
        else:
            step = max(mark_len, 1.0) + max(gap, 0.0)
        if style == "s":
            offsets = self._centered_half_offsets(count, step)
        else:
            offsets = self._symmetric_offsets(count, step)
        ns = ""
        tag = self._svg_root.tag
        if tag.startswith("{") and "}" in tag:
            ns = tag.split("}", 1)[0][1:]
        mx = 0.5 * (x1 + x2)
        my = 0.5 * (y1 + y2)
        for i, offset in enumerate(offsets, start=1):
            cx = mx + offset * ux
            cy = my + offset * uy
            if style == "puntos":
                new_el = ET.Element(f"{{{ns}}}circle") if ns else ET.Element("circle")
                new_el.set("cx", _format_num(cx))
                new_el.set("cy", _format_num(cy))
                new_el.set("r", _format_num(radius))
                new_el.set("data-kind", "seg-mark")
                new_el.set("data-parent-key", key)
                new_el.set("data-mark-index", str(i))
                _set_attr(new_el, "fill", stroke)
                _set_attr(new_el, "stroke", "none")
                self._svg_root.append(new_el)
                continue
            if style == "rectangulo":
                half_w = 0.5 * rect_w
                half_h = 0.5 * rect_h
                p1 = (cx - ux * half_h - nx * half_w, cy - uy * half_h - ny * half_w)
                p2 = (cx + ux * half_h - nx * half_w, cy + uy * half_h - ny * half_w)
                p3 = (cx + ux * half_h + nx * half_w, cy + uy * half_h + ny * half_w)
                p4 = (cx - ux * half_h + nx * half_w, cy - uy * half_h + ny * half_w)
                new_el = ET.Element(f"{{{ns}}}polygon") if ns else ET.Element("polygon")
                pts = " ".join(f"{_format_num(x)},{_format_num(y)}" for x, y in (p1, p2, p3, p4))
                new_el.set("points", pts)
                new_el.set("data-kind", "seg-mark")
                new_el.set("data-parent-key", key)
                new_el.set("data-mark-index", str(i))
                _set_attr(new_el, "stroke", stroke)
                _set_attr(new_el, "stroke-width", _format_num(stroke_w))
                _set_attr(new_el, "fill", stroke if rect_fill else "none")
                self._svg_root.append(new_el)
                continue
            if style == "sinusoidal":
                start_x = cx - ux * (mark_len * 0.5)
                start_y = cy - uy * (mark_len * 0.5)
                steps = max(20, cycles * 20)
                pts: list[str] = []
                for s in range(steps + 1):
                    t = s / steps
                    px = start_x + ux * (mark_len * t)
                    py = start_y + uy * (mark_len * t)
                    w = math.sin(2.0 * math.pi * cycles * t) * amp
                    px += nx * w
                    py += ny * w
                    pts.append(f"{_format_num(px)},{_format_num(py)}")
                new_el = ET.Element(f"{{{ns}}}polyline") if ns else ET.Element("polyline")
                new_el.set("points", " ".join(pts))
                new_el.set("data-kind", "seg-mark")
                new_el.set("data-parent-key", key)
                new_el.set("data-mark-index", str(i))
                _set_attr(new_el, "stroke", stroke)
                _set_attr(new_el, "stroke-width", _format_num(stroke_w))
                _set_attr(new_el, "fill", "none")
                self._svg_root.append(new_el)
                continue
            if style == "s":
                start_x = cx - ux * (mark_len * 0.5)
                start_y = cy - uy * (mark_len * 0.5)
                steps = 40
                pts = []
                for s in range(steps + 1):
                    t = s / steps
                    px = start_x + ux * (mark_len * t)
                    py = start_y + uy * (mark_len * t)
                    w = math.cos(math.pi * t) * amp
                    px += nx * w
                    py += ny * w
                    pts.append(f"{_format_num(px)},{_format_num(py)}")
                new_el = ET.Element(f"{{{ns}}}polyline") if ns else ET.Element("polyline")
                new_el.set("points", " ".join(pts))
                new_el.set("data-kind", "seg-mark")
                new_el.set("data-parent-key", key)
                new_el.set("data-mark-index", str(i))
                _set_attr(new_el, "stroke", stroke)
                _set_attr(new_el, "stroke-width", _format_num(stroke_w))
                _set_attr(new_el, "fill", "none")
                self._svg_root.append(new_el)

    def _select_record(self, record: _Record) -> None:
        if record.el.get("data-angle-id"):
            root = self._angle_root_for_id(record.el.get("data-angle-id") or "")
            if root is not None and root is not record.el:
                for r in self._records:
                    if r.el is root:
                        record = r
                        break
        if self._selected is record:
            return
        self._selected = record
        self._sync_selected_ui()
        self._highlight_code_for_element(record.el)
        self._render_preview()

    def _clear_code_highlight(self) -> None:
        try:
            self.text_input.tag_remove(self._code_highlight_tag, "1.0", "end")
        except Exception:
            pass

    def _highlight_code_for_element(self, el: ET.Element | None) -> None:
        if el is None:
            self._clear_code_highlight()
            return
        raw = self.text_input.get("1.0", "end")
        line = self._find_code_line_for_element(el, raw)
        self._clear_code_highlight()
        if line is None:
            return
        start = f"{line}.0"
        end = f"{line}.end"
        try:
            self.text_input.tag_add(self._code_highlight_tag, start, end)
            self.text_input.mark_set("insert", start)
            self.text_input.see(start)
        except Exception:
            pass

    def _find_code_line_for_element(self, el: ET.Element, raw: str) -> int | None:
        tag = _strip_ns(el.tag)
        attrs = el.attrib
        candidates: list[str] = []
        if "id" in attrs:
            candidates.append(f'id="{attrs["id"]}"')
            candidates.append(f"id='{attrs['id']}'")
        if tag == "circle":
            cx = _get_attr(el, "cx")
            cy = _get_attr(el, "cy")
            if cx and cy:
                candidates.append(f'cx="{cx}"')
                candidates.append(f'cy="{cy}"')
        if tag in ("text", "path"):
            if "data-text" in attrs:
                candidates.append(f'data-text="{attrs["data-text"]}"')
                candidates.append(f"data-text='{attrs['data-text']}'")
        if "d" in attrs:
            frag = attrs["d"][:24].strip()
            if frag:
                candidates.append(frag)
        if not candidates:
            candidates.append(f"<{tag}")

        idx = -1
        for c in candidates:
            idx = raw.find(c)
            if idx != -1:
                break
        if idx == -1:
            idx = raw.find(f"<{tag}")
        if idx == -1:
            return None
        line = raw.count("\n", 0, idx) + 1
        return line

    def _highlight(self, record: _Record) -> None:
        color = "#ff3333"
        if record.kind == "label":
            for item_id in record.item_ids:
                self.canvas.itemconfig(item_id, fill=color)
            return
        if record.tag in ("line", "polyline", "path"):
            for item_id in record.item_ids:
                self.canvas.itemconfig(item_id, fill=color)
        else:
            for item_id in record.item_ids:
                self.canvas.itemconfig(item_id, outline=color)

    def _restore_color(self, record: _Record) -> None:
        if record.kind == "label":
            if record.orig_fill is not None:
                for item_id in record.item_ids:
                    self.canvas.itemconfig(item_id, fill=record.orig_fill)
            return
        if record.tag in ("line", "polyline", "path"):
            if record.orig_fill is not None:
                for item_id in record.item_ids:
                    self.canvas.itemconfig(item_id, fill=record.orig_fill)
        else:
            if record.orig_outline is not None:
                for item_id in record.item_ids:
                    self.canvas.itemconfig(item_id, outline=record.orig_outline)

    def _delete_selected(self, _event=None) -> None:
        if self._selected is None or self._svg_root is None:
            return
        record = self._selected
        if _strip_ns(record.el.tag) == "line" and (record.el.get("data-kind") or "").strip() == _SEG_DIM_LINE_DATA_KIND:
            owner = self._segment_dimension_owner_line(record.el)
            if owner is not None:
                self._push_history()
                self._remove_segment_dimensions(owner, clear_attrs=True)
                self._selected = None
                self._anchor_points = self._collect_anchor_points(self._svg_root)
                self._sync_selected_ui()
                self._render_svg()
            return
        parent = self._parent_of(record.el)
        if parent is None:
            return
        radius_parent_circle: ET.Element | None = None
        if _strip_ns(record.el.tag) == "line" and record.el.get("data-kind") != "seg-mark":
            if (record.el.get("data-kind") or "").strip() == "circle-radius":
                radius_parent_circle = self._circle_for_radius_line(record.el)
            self._remove_segment_marks(record.el)
            self._remove_segment_endpoints(record.el)
            self._remove_segment_mid_labels(record.el)
            self._remove_segment_dimensions(record.el, clear_attrs=True)
        if _strip_ns(record.el.tag) == "circle":
            self._remove_circle_radius(record.el)
        if record.kind == "label":
            self._remove_label_background(record.el)
        self._push_history()
        if radius_parent_circle is not None:
            radius_parent_circle.attrib.pop("data-radius-show", None)
        try:
            parent.remove(record.el)
        except Exception:
            return
        self._selected = None
        self._anchor_points = self._collect_anchor_points(self._svg_root)
        self._sync_selected_ui()
        self._render_svg()

    def _on_delete_key_global(self, _event=None):
        focus = self.focus_get()
        if isinstance(focus, (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox)):
            return None
        if focus is None:
            return None
        self._delete_selected()
        return "break"

    def _apply_stroke_selected(self) -> None:
        if self._selected is None:
            messagebox.showerror("Stroke", "No hay elemento seleccionado.")
            return
        if self._selected.kind == "label":
            return
        try:
            w = float(self.stroke_width_var.get().strip())
        except Exception:
            messagebox.showerror("Stroke", "stroke-width invalido.")
            return
        if w <= 0:
            messagebox.showerror("Stroke", "stroke-width debe ser > 0.")
            return
        self._push_history()
        _set_attr(self._selected.el, "stroke-width", str(w))
        self._render_svg()

    def _apply_stroke_all(self) -> None:
        try:
            w = float(self.stroke_width_var.get().strip())
        except Exception:
            messagebox.showerror("Stroke", "stroke-width invalido.")
            return
        if w <= 0:
            messagebox.showerror("Stroke", "stroke-width debe ser > 0.")
            return
        self._push_history()
        for record in self._records:
            if record.kind == "label":
                continue
            _set_attr(record.el, "stroke-width", str(w))
        self._render_svg()

    def _collect_anchor_points(self, root: ET.Element) -> list[tuple[float, float]]:
        points: list[tuple[float, float]] = []
        for el in root.iter():
            tag = _strip_ns(el.tag)
            kind = (el.get("data-kind") or "").strip()
            if _is_aux_data_kind(kind):
                continue
            if tag == "circle":
                cx = _parse_float(_get_attr(el, "cx"))
                cy = _parse_float(_get_attr(el, "cy"))
                points.append((cx, cy))
            elif tag == "ellipse":
                cx = _parse_float(_get_attr(el, "cx"))
                cy = _parse_float(_get_attr(el, "cy"))
                points.append((cx, cy))
            elif tag == "line":
                x1 = _parse_float(_get_attr(el, "x1"))
                y1 = _parse_float(_get_attr(el, "y1"))
                x2 = _parse_float(_get_attr(el, "x2"))
                y2 = _parse_float(_get_attr(el, "y2"))
                points.append((x1, y1))
                points.append((x2, y2))
            elif tag in ("polyline", "polygon"):
                pts = _parse_points(_get_attr(el, "points"))
                for i in range(0, len(pts) - 1, 2):
                    points.append((pts[i], pts[i + 1]))
            elif tag == "rect":
                x = _parse_float(_get_attr(el, "x"))
                y = _parse_float(_get_attr(el, "y"))
                w = _parse_float(_get_attr(el, "width"))
                h = _parse_float(_get_attr(el, "height"))
                points.append((x, y))
                points.append((x + w, y))
                points.append((x, y + h))
                points.append((x + w, y + h))
            elif tag == "path":
                if el.get("data-text") is not None:
                    continue
                nums = _path_numbers(_get_attr(el, "d"))
                for i in range(0, len(nums) - 1, 2):
                    points.append((nums[i], nums[i + 1]))
        return points

    def _nearest_anchor(self, x: float, y: float) -> tuple[float, float] | None:
        if not self._anchor_points:
            return None
        best = None
        best_d = None
        for ax, ay in self._anchor_points:
            dx = ax - x
            dy = ay - y
            d = dx * dx + dy * dy
            if best_d is None or d < best_d:
                best = (ax, ay)
                best_d = d
        return best

    def _move_label_dir(self, dx: int, dy: int) -> None:
        if self._selected is None or self._selected.kind != "label":
            return
        try:
            offset = float(self.label_offset_var.get().strip())
        except Exception:
            messagebox.showerror("Etiqueta", "offset invalido.")
            return
        if offset < 0:
            messagebox.showerror("Etiqueta", "offset debe ser >= 0.")
            return
        x, y = self._label_position(self._selected.el)
        self._ensure_label_anchor(self._selected.el, x, y)
        ax = _parse_float(self._selected.el.get("data-anchor-x"), 0.0)
        ay = _parse_float(self._selected.el.get("data-anchor-y"), 0.0)
        anchor = (ax, ay) if (self._selected.el.get("data-anchor-x") is not None) else self._nearest_anchor(x, y)
        if anchor is None:
            return
        dir_s = self._dir_from_delta(dx, dy)[0]
        if _strip_ns(self._selected.el.tag) == "text":
            text = (self._selected.el.text or "").strip()
        else:
            text = (self._selected.el.get("data-text") or "").strip()
        if not text:
            return
        font_size = self._label_font_size(self._selected.el, 12.0)
        self._push_history()
        self._selected.el.set("data-dir", dir_s)
        self._selected.el.set("data-offset", _format_num(offset))
        nx, ny = _label_position_from_anchor(
            anchor[0], anchor[1], text, dir_s, offset, font_size, True
        )
        self._set_label_position(self._selected.el, nx, ny)
        self._render_svg()

    def _label_position(self, el: ET.Element) -> tuple[float, float]:
        tag = _strip_ns(el.tag)
        if tag == "text":
            return (_parse_float(_get_attr(el, "x")), _parse_float(_get_attr(el, "y")))
        return (_parse_float(el.get("data-x")), _parse_float(el.get("data-y")))

    def _ensure_label_anchor(self, el: ET.Element, x: float, y: float) -> None:
        if el.get("data-anchor-frac") is not None:
            return
        if el.get("data-anchor-x") is not None and el.get("data-anchor-y") is not None:
            return
        anchor = self._nearest_anchor(x, y)
        if anchor is None:
            return
        ax, ay = anchor
        el.set("data-anchor-x", _format_num(ax))
        el.set("data-anchor-y", _format_num(ay))
        if el.get("data-dir") is not None:
            existing_dir = _normalize_dir_input(el.get("data-dir") or "")
            if existing_dir == _CENTER_DIR:
                el.set("data-dir", _CENTER_DIR)
                el.set("data-offset", "0")
            else:
                dx = x - ax
                dy = y - ay
                dir_s, offset = self._dir_from_delta(dx, dy)
                el.set("data-dir", dir_s)
                el.set("data-offset", _format_num(offset))

    def _dir_from_delta(self, dx: float, dy: float) -> tuple[str, float]:
        dist = math.hypot(dx, dy)
        if dist <= 1e-9:
            return (_CENTER_DIR, 0.0)
        ang = math.degrees(math.atan2(dy, dx))
        if ang < 0:
            ang += 360.0
        step = 360.0 / len(_COMPASS_32)
        idx = int(round(ang / step)) % len(_COMPASS_32)
        dir_s = _COMPASS_32[idx]
        ux, uy = _dir_to_vec(dir_s)
        proj = abs(dx * ux + dy * uy)
        offset = proj if proj > 1e-9 else dist
        return (dir_s, float(offset))

    def _infer_label_dir_offset(
        self,
        ax: float,
        ay: float,
        x: float,
        y: float,
        text: str,
        font_size: float,
        latex: bool,
    ) -> tuple[str, float]:
        dx = x - ax
        dy = y - ay
        dir_s, _ = self._dir_from_delta(dx, dy)
        x0, x1, y0, y1 = _text_bounds(text, font_size, latex)
        off_x = None
        off_y = None
        ux, uy = _dir_to_vec(dir_s)
        eps = 1e-9
        if uy < -eps:
            off_y = ay + y0 - y
        elif uy > eps:
            off_y = y - ay - y1
        if ux > eps:
            off_x = x - ax + x0
        elif ux < -eps:
            off_x = ax - x - x1
        if off_x is None and off_y is None:
            offset = max(abs(dx), abs(dy))
        elif off_x is None:
            offset = off_y
        elif off_y is None:
            offset = off_x
        else:
            offset = max(off_x, off_y)
        if offset is None or not math.isfinite(offset):
            offset = max(abs(dx), abs(dy))
        if offset < 0:
            offset = abs(offset)
        return (dir_s, float(offset))

    def _dir_to_vec(self, dir_s: str) -> tuple[float, float]:
        return _dir_to_vec(dir_s)

    def _label_bg_mode_for(self, el: ET.Element | None) -> str:
        if el is None:
            return _LABEL_BG_MODE_NONE
        raw_mode = (el.get(_LABEL_BG_MODE_ATTR) or "").strip().lower()
        if raw_mode in (_LABEL_BG_MODE_NONE, _LABEL_BG_MODE_WHITE, _LABEL_BG_MODE_CUT):
            return raw_mode
        if (el.get("data-label-bg") or "").strip() == "1":
            return _LABEL_BG_MODE_WHITE
        return _LABEL_BG_MODE_NONE

    def _label_cut_shape_for(self, el: ET.Element | None) -> str:
        if el is None:
            return _LABEL_CUT_SHAPE_CONTOUR
        raw_shape = (el.get(_LABEL_CUT_SHAPE_ATTR) or "").strip().lower()
        if raw_shape in (_LABEL_CUT_SHAPE_RECT, _LABEL_CUT_SHAPE_CONTOUR):
            return raw_shape
        return _LABEL_CUT_SHAPE_CONTOUR

    def _set_label_bg_mode(self, el: ET.Element, mode: str | None) -> None:
        normalized = (mode or "").strip().lower()
        if normalized not in (_LABEL_BG_MODE_NONE, _LABEL_BG_MODE_WHITE, _LABEL_BG_MODE_CUT):
            normalized = _LABEL_BG_MODE_NONE
        el.set(_LABEL_BG_MODE_ATTR, normalized)
        if normalized == _LABEL_BG_MODE_WHITE:
            el.set("data-label-bg", "1")
        else:
            el.attrib.pop("data-label-bg", None)
        if normalized == _LABEL_BG_MODE_CUT:
            el.set(_LABEL_CUT_SHAPE_ATTR, _LABEL_CUT_SHAPE_RECT)
        else:
            el.attrib.pop(_LABEL_CUT_SHAPE_ATTR, None)

    def _point_label_bg_mode_selected(self) -> str:
        if hasattr(self, "_point_label_bg_mode_var"):
            return _label_bg_mode_from_ui(self._point_label_bg_mode_var.get())
        return _LABEL_BG_MODE_WHITE if bool(self._point_label_bg_var.get()) else _LABEL_BG_MODE_NONE

    def _segment_endpoint_bg_mode_selected(self) -> str:
        if hasattr(self, "_segment_endpoint_bg_mode_var"):
            return _label_bg_mode_from_ui(self._segment_endpoint_bg_mode_var.get())
        return _LABEL_BG_MODE_WHITE if bool(self._segment_endpoint_bg_var.get()) else _LABEL_BG_MODE_NONE

    def _segment_mid_bg_mode_selected(self) -> str:
        if hasattr(self, "_segment_mid_bg_mode_var"):
            return _label_bg_mode_from_ui(self._segment_mid_bg_mode_var.get())
        return _LABEL_BG_MODE_WHITE if bool(self._segment_mid_bg_var.get()) else _LABEL_BG_MODE_NONE

    def _angle_label_bg_mode_selected(self) -> str:
        if hasattr(self, "_angle_label_bg_mode_var"):
            return _label_bg_mode_from_ui(self._angle_label_bg_mode_var.get())
        return _LABEL_BG_MODE_WHITE if bool(self._angle_label_bg_var.get()) else _LABEL_BG_MODE_NONE

    def _set_label_position(self, el: ET.Element, x: float, y: float) -> None:
        tag = _strip_ns(el.tag)
        if tag == "text":
            _set_attr(el, "x", _format_num(x))
            _set_attr(el, "y", _format_num(y))
        else:
            el.set("data-x", _format_num(x))
            el.set("data-y", _format_num(y))
            text = el.get("data-text") or ""
            font_size = _parse_float(el.get("data-font-size"), 12.0)
            self._update_latex_path(el, text, x, y, font_size)
        self._ensure_label_background(el)

    def _is_path_label(self, el: ET.Element) -> bool:
        return _strip_ns(el.tag) == "path" and el.get("data-text") is not None

    def _next_label_id(self) -> str:
        if self._svg_root is None:
            return "lbl-1"
        max_idx = 0
        for el in self._svg_root.iter():
            raw = (el.get("data-label-id") or "").strip()
            if not raw.startswith("lbl-"):
                continue
            try:
                idx = int(raw[4:])
            except Exception:
                continue
            if idx > max_idx:
                max_idx = idx
        return f"lbl-{max_idx + 1}"

    def _label_id_for(self, el: ET.Element) -> str:
        label_id = (el.get("data-label-id") or "").strip()
        if label_id:
            return label_id
        label_id = self._next_label_id()
        el.set("data-label-id", label_id)
        return label_id

    def _find_label_bg(self, label_id: str) -> ET.Element | None:
        if self._svg_root is None or not label_id:
            return None
        for el in self._svg_root.iter():
            if (el.get("data-kind") or "").strip() != "label-bg":
                continue
            if (el.get("data-label-id") or "").strip() == label_id:
                return el
        return None

    def _label_overlay_group(self) -> ET.Element | None:
        if self._svg_root is None:
            return None
        for child in list(self._svg_root):
            if _strip_ns(child.tag) == "g" and (child.get("data-kind") or "").strip() == "label-overlay":
                return child
        ns = ""
        tag = self._svg_root.tag
        if tag.startswith("{") and "}" in tag:
            ns = tag.split("}", 1)[0][1:]
        overlay = ET.Element(f"{{{ns}}}g") if ns else ET.Element("g")
        overlay.set("data-kind", "label-overlay")
        self._svg_root.append(overlay)
        return overlay

    def _label_bounds(self, el: ET.Element) -> tuple[float, float, float, float] | None:
        tag = _strip_ns(el.tag)
        path_bounds: tuple[float, float, float, float] | None = None
        if tag == "path" and el.get("data-text") is not None:
            d = _get_attr(el, "d") or ""
            subpaths = _parse_svg_path(d)
            if subpaths:
                min_x = float("inf")
                min_y = float("inf")
                max_x = float("-inf")
                max_y = float("-inf")
                for pts, _closed in subpaths:
                    for px, py in pts:
                        min_x = min(min_x, px)
                        min_y = min(min_y, py)
                        max_x = max(max_x, px)
                        max_y = max(max_y, py)
                if min_x != float("inf"):
                    path_bounds = (min_x, min_y, max_x, max_y)
        bounds: tuple[float, float, float, float] | None = None
        if tag == "text":
            text = (el.text or "").strip()
            font_size = self._label_font_size(el, 12.0)
            latex = False
        elif tag == "path" and el.get("data-text") is not None:
            text = (el.get("data-text") or "").strip()
            font_size = _parse_float(el.get("data-font-size"), 12.0)
            latex = True
        else:
            text = ""
            font_size = 12.0
            latex = False
        if text:
            x, y = self._label_position(el)
            x0, x1, y0, y1 = _text_bounds(text, font_size, latex)
            ax = ay = 0.0
            frac = el.get("data-anchor-frac")
            if frac:
                parts = _parse_points(frac)
                if len(parts) >= 2:
                    fx = max(0.0, min(1.0, parts[0]))
                    fy = max(0.0, min(1.0, parts[1]))
                    ax = x0 + fx * (x1 - x0)
                    ay = y0 + fy * (y1 - y0)
            min_x = x + x0 - ax
            max_x = x + x1 - ax
            min_y = y - y1 + ay
            max_y = y - y0 + ay
            if max_x < min_x:
                min_x, max_x = max_x, min_x
            if max_y < min_y:
                min_y, max_y = max_y, min_y
            bounds = (min_x, min_y, max_x, max_y)
        if path_bounds is not None:
            if bounds is None:
                bounds = path_bounds
            else:
                bounds = (
                    min(path_bounds[0], bounds[0]),
                    min(path_bounds[1], bounds[1]),
                    max(path_bounds[2], bounds[2]),
                    max(path_bounds[3], bounds[3]),
                )
        if bounds is None:
            return None
        pad = _LABEL_BOUNDS_PAD
        return (bounds[0] - pad, bounds[1] - pad, bounds[2] + pad, bounds[3] + pad)

    def _remove_label_background(self, el: ET.Element) -> None:
        if self._svg_root is None:
            return
        label_id = (el.get("data-label-id") or "").strip()
        if not label_id:
            return
        bg = self._find_label_bg(label_id)
        if bg is None:
            return
        parent = self._parent_of(bg)
        if parent is not None:
            try:
                parent.remove(bg)
            except Exception:
                pass

    def _ensure_label_background(self, el: ET.Element) -> None:
        if self._svg_root is None:
            return
        mode = self._label_bg_mode_for(el)
        if mode != _LABEL_BG_MODE_WHITE:
            self._remove_label_background(el)
            return
        overlay = self._label_overlay_group()
        if overlay is None:
            return
        bounds = self._label_bounds(el)
        if bounds is None:
            self._remove_label_background(el)
            return
        label_id = self._label_id_for(el)
        bg = self._find_label_bg(label_id)
        parent = self._parent_of(el)
        if overlay is not None and parent is not overlay:
            try:
                parent.remove(el)
            except Exception:
                parent = None
            overlay.append(el)
        if bg is None:
            ns = ""
            tag = self._svg_root.tag
            if tag.startswith("{") and "}" in tag:
                ns = tag.split("}", 1)[0][1:]
            bg = ET.Element(f"{{{ns}}}rect") if ns else ET.Element("rect")
            bg.set("data-kind", "label-bg")
            bg.set("data-label-id", label_id)
            _set_attr(bg, "fill", "#ffffff")
            _set_attr(bg, "stroke", "none")
            if overlay is None:
                return
            try:
                idx = list(overlay).index(el)
            except ValueError:
                return
            overlay.insert(idx, bg)
        else:
            bg_parent = self._parent_of(bg)
            if overlay is not None and bg_parent is not overlay:
                if bg_parent is not None:
                    try:
                        bg_parent.remove(bg)
                    except Exception:
                        pass
                try:
                    idx = list(overlay).index(el)
                except ValueError:
                    idx = len(list(overlay))
                overlay.insert(idx, bg)
        min_x, min_y, max_x, max_y = bounds
        pad = _LABEL_BG_PAD
        x = min_x - pad
        y = min_y - pad
        w = max(0.1, (max_x - min_x) + 2.0 * pad)
        h = max(0.1, (max_y - min_y) + 2.0 * pad)
        _set_attr(bg, "x", _format_num(x))
        _set_attr(bg, "y", _format_num(y))
        _set_attr(bg, "width", _format_num(w))
        _set_attr(bg, "height", _format_num(h))

    def _sync_label_backgrounds(self) -> None:
        if self._svg_root is None:
            return
        labels = []
        for el in self._svg_root.iter():
            tag = _strip_ns(el.tag)
            if tag == "text" or (tag == "path" and el.get("data-text") is not None):
                labels.append(el)
        active_ids: set[str] = set()
        for el in labels:
            if self._label_bg_mode_for(el) == _LABEL_BG_MODE_WHITE:
                label_id = self._label_id_for(el)
                active_ids.add(label_id)
                self._ensure_label_background(el)
            else:
                self._remove_label_background(el)
        to_remove: list[ET.Element] = []
        for bg in self._svg_root.iter():
            if (bg.get("data-kind") or "").strip() != "label-bg":
                continue
            label_id = (bg.get("data-label-id") or "").strip()
            if not label_id or label_id not in active_ids:
                to_remove.append(bg)
        for bg in to_remove:
            parent = self._parent_of(bg)
            if parent is not None:
                try:
                    parent.remove(bg)
                except Exception:
                    pass

    def _mask_id_from_attr(self, raw: str | None) -> str:
        token = (raw or "").strip()
        if not token:
            return ""
        match = re.match(r"url\(\s*#([^)]+)\s*\)", token)
        if match is not None:
            return (match.group(1) or "").strip()
        return token

    def _is_label_cut_mask_eligible(self, el: ET.Element) -> bool:
        if self._svg_root is None:
            return False
        tag = _strip_ns(el.tag)
        if tag not in ("line", "polyline", "polygon", "path", "circle", "ellipse", "rect"):
            return False
        if tag == "path" and el.get("data-text") is not None:
            return False
        kind = (el.get("data-kind") or "").strip()
        if (_is_aux_data_kind(kind) and kind != "subsegment") or kind in ("background", "label-bg"):
            return False
        cur: ET.Element | None = el
        while cur is not None:
            if _strip_ns(cur.tag) == "defs":
                return False
            cur = self._parent_of(cur)
        display = (self._effective_attr(el, "display") or "").strip().lower()
        visibility = (self._effective_attr(el, "visibility") or "").strip().lower()
        if display == "none" or visibility == "hidden":
            return False
        stroke = (self._effective_attr(el, "stroke") or "").strip().lower()
        stroke_opacity = _parse_float(self._effective_attr(el, "stroke-opacity"), 1.0)
        stroke_w = _parse_float(self._effective_attr(el, "stroke-width"), 1.0)
        stroke_visible = (
            stroke not in ("", "none", "transparent")
            and stroke_opacity > 0
            and stroke_w > 0
        )
        fill = (self._effective_attr(el, "fill") or "").strip().lower()
        fill_opacity = _parse_float(self._effective_attr(el, "fill-opacity"), 1.0)
        fill_visible = fill not in ("", "none", "transparent") and fill_opacity > 0
        return stroke_visible or fill_visible

    def _sync_label_cut_masks(self) -> None:
        if self._svg_root is None:
            return
        root = self._svg_root
        cut_labels: list[ET.Element] = []
        for el in root.iter():
            tag = _strip_ns(el.tag)
            if tag == "text" or (tag == "path" and el.get("data-text") is not None):
                if self._label_bg_mode_for(el) == _LABEL_BG_MODE_CUT:
                    cut_labels.append(el)

        defs = None
        for child in list(root):
            if _strip_ns(child.tag) == "defs":
                defs = child
                break
        mask_el = None
        if defs is not None:
            for child in list(defs):
                if _strip_ns(child.tag) == "mask" and (child.get("id") or "").strip() == _LABEL_CUT_MASK_ID:
                    mask_el = child
                    break

        all_elements = [el for el in root.iter()]
        if not cut_labels:
            for el in all_elements:
                if self._mask_id_from_attr(el.get("mask")) == _LABEL_CUT_MASK_ID:
                    el.attrib.pop("mask", None)
            if defs is not None and mask_el is not None:
                try:
                    defs.remove(mask_el)
                except Exception:
                    pass
            return

        if defs is None:
            defs = self._ensure_defs_node()
        if defs is None:
            return
        if mask_el is None:
            mask_el = ET.Element(self._svg_ns_tag("mask"))
            mask_el.set("id", _LABEL_CUT_MASK_ID)
            defs.append(mask_el)
        for child in list(mask_el):
            mask_el.remove(child)

        min_x, min_y, vb_w, vb_h = self._resolve_viewbox(root)
        mask_el.set("maskUnits", "userSpaceOnUse")
        mask_el.set("maskContentUnits", "userSpaceOnUse")
        mask_el.set("x", _format_num(min_x))
        mask_el.set("y", _format_num(min_y))
        mask_el.set("width", _format_num(max(vb_w, 1.0)))
        mask_el.set("height", _format_num(max(vb_h, 1.0)))

        base_rect = ET.Element(self._svg_ns_tag("rect"))
        base_rect.set("x", _format_num(min_x))
        base_rect.set("y", _format_num(min_y))
        base_rect.set("width", _format_num(max(vb_w, 1.0)))
        base_rect.set("height", _format_num(max(vb_h, 1.0)))
        base_rect.set("fill", "#ffffff")
        base_rect.set("stroke", "none")
        mask_el.append(base_rect)

        stroke_pad = _format_num(_LABEL_CUT_MASK_STROKE)
        for label_el in cut_labels:
            cut_shape = self._label_cut_shape_for(label_el)
            if cut_shape == _LABEL_CUT_SHAPE_RECT:
                bounds = self._label_bounds(label_el)
                if bounds is None:
                    continue
                min_x2, min_y2, max_x2, max_y2 = bounds
                pad2 = _LABEL_CUT_RECT_PAD
                rect = ET.Element(self._svg_ns_tag("rect"))
                rect.set("x", _format_num(min_x2 - pad2))
                rect.set("y", _format_num(min_y2 - pad2))
                rect.set("width", _format_num(max(0.1, (max_x2 - min_x2) + 2.0 * pad2)))
                rect.set("height", _format_num(max(0.1, (max_y2 - min_y2) + 2.0 * pad2)))
                rect.set("fill", "#000000")
                rect.set("stroke", "none")
                mask_el.append(rect)
                continue
            tag = _strip_ns(label_el.tag)
            if tag == "path":
                d = _get_attr(label_el, "d") or ""
                if not d.strip():
                    continue
                cut_path = ET.Element(self._svg_ns_tag("path"))
                cut_path.set("d", d)
                transform = _get_attr(label_el, "transform")
                if transform:
                    cut_path.set("transform", transform)
                cut_path.set("fill", "#000000")
                cut_path.set("stroke", "#000000")
                cut_path.set("stroke-width", stroke_pad)
                cut_path.set("stroke-linejoin", "round")
                cut_path.set("stroke-linecap", "round")
                mask_el.append(cut_path)
                continue
            text = (label_el.text or "").strip()
            if not text:
                continue
            cut_text = ET.Element(self._svg_ns_tag("text"))
            cut_text.text = text
            for attr in (
                "x",
                "y",
                "font-family",
                "font-size",
                "font-style",
                "font-weight",
                "text-anchor",
                "dominant-baseline",
                "transform",
            ):
                value = _get_attr(label_el, attr)
                if value is None:
                    value = self._effective_attr(label_el, attr)
                if value is not None and str(value).strip():
                    cut_text.set(attr, str(value))
            cut_text.set("fill", "#000000")
            cut_text.set("stroke", "#000000")
            cut_text.set("stroke-width", stroke_pad)
            cut_text.set("stroke-linejoin", "round")
            cut_text.set("paint-order", "stroke fill")
            mask_el.append(cut_text)

        mask_ref = f"url(#{_LABEL_CUT_MASK_ID})"
        for el in all_elements:
            current_mask_id = self._mask_id_from_attr(el.get("mask"))
            if current_mask_id and current_mask_id != _LABEL_CUT_MASK_ID:
                continue
            if self._is_label_cut_mask_eligible(el):
                el.set("mask", mask_ref)
            elif current_mask_id == _LABEL_CUT_MASK_ID:
                el.attrib.pop("mask", None)

    def _refresh_and_reselect(self, el: ET.Element) -> None:
        self._render_svg()
        for record in self._records:
            if record.el is el:
                self._select_record(record)
                break

    def _update_label_text(self) -> None:
        if self._selected is None or self._selected.kind != "label":
            return
        text = self.label_text_var.get()
        el = self._selected.el
        x, y = self._label_position(el)
        self._ensure_label_anchor(el, x, y)
        self._push_history()
        if _strip_ns(el.tag) == "text":
            el.text = text
        else:
            el.set("data-text", text)
            font_size = _parse_float(el.get("data-font-size"), 12.0)
            self._update_latex_path(el, text, x, y, font_size)
        self._ensure_label_background(el)
        self._render_svg()

    def _add_label_to_point(self) -> None:
        if self._svg_root is None:
            return
        if self._selected is None or _strip_ns(self._selected.el.tag) != "circle":
            messagebox.showerror("Etiqueta", "Selecciona un punto (circle) para agregar etiqueta.")
            return
        text = self.label_text_var.get().strip()
        if not text:
            messagebox.showerror("Etiqueta", "Texto de etiqueta vacio.")
            return
        try:
            offset = float(self.label_offset_var.get().strip())
        except Exception:
            messagebox.showerror("Etiqueta", "offset invalido.")
            return
        if offset < 0:
            messagebox.showerror("Etiqueta", "offset debe ser >= 0.")
            return
        self._push_history()
        cx = _parse_float(_get_attr(self._selected.el, "cx"))
        cy = _parse_float(_get_attr(self._selected.el, "cy"))
        dx, dy = self._dir_to_vec("N")
        x = cx + dx * offset
        y = cy + dy * offset
        ns = ""
        tag = self._svg_root.tag
        if tag.startswith("{") and "}" in tag:
            ns = tag.split("}", 1)[0][1:]
        if ns:
            new_el = ET.Element(f"{{{ns}}}text")
        else:
            new_el = ET.Element("text")
        new_el.text = text
        new_el.set("x", _format_num(x))
        new_el.set("y", _format_num(y))
        new_el.set("font-size", "15")
        new_el.set("fill", "#000000")
        new_el.set("data-anchor-x", _format_num(cx))
        new_el.set("data-anchor-y", _format_num(cy))
        new_el.set("data-offset", _format_num(offset))
        self._sync_point_identity_from_label(self._selected.el, new_el, text)
        self._svg_root.append(new_el)
        self._render_svg()
        for record in self._records:
            if record.el is new_el:
                self._select_record(record)
                break

    def _update_latex_path(
        self,
        el: ET.Element,
        text: str,
        x: float,
        y: float,
        font_size: float,
        *,
        silent: bool = False,
    ) -> bool:
        try:
            configure_mathtext, require_matplotlib = _resolve_latex_support()
            require_matplotlib()
            configure_mathtext()
        except Exception as exc:
            if not silent:
                messagebox.showerror("LaTeX", f"No se pudo cargar matplotlib: {exc}")
            return False
        dir_s = (el.get("data-dir") or "").strip().upper()
        anchor = None
        frac = el.get("data-anchor-frac")
        if frac:
            parts = _parse_points(frac)
            if len(parts) >= 2:
                anchor = (max(0.0, min(1.0, parts[0])), max(0.0, min(1.0, parts[1])))
        if anchor is None:
            use_anchor = el.get("data-anchor-x") is None or el.get("data-anchor-y") is None
            anchor = _label_anchor_for_dir(dir_s) if (dir_s and use_anchor) else None
        try:
            d = _latex_path_d(text, x, y, font_size, anchor=anchor)
        except Exception as exc:
            if not silent:
                messagebox.showerror(
                    "LaTeX", f"No se pudo actualizar la etiqueta: {exc}"
                )
            return False
        el.set("d", d)
        el.set("data-font-size", _format_num(font_size))
        return True

    def _latex_selected(self) -> None:
        if self._selected is None or self._selected.kind != "label":
            return
        el = self._selected.el
        if _strip_ns(el.tag) != "text":
            return
        self._push_history()
        self._convert_text_element(el, silent=False)
        self._render_svg()

    def _latex_all(self) -> None:
        if self._svg_root is None:
            return
        to_convert: list[ET.Element] = []
        for el in self._svg_root.iter():
            if _strip_ns(el.tag) == "text":
                to_convert.append(el)
        if not to_convert:
            return
        self._push_history()
        converted = 0
        failed = 0
        for el in to_convert:
            if self._convert_text_element(el, silent=False):
                converted += 1
            else:
                failed += 1
        self._render_svg()
        if converted == 0 and failed > 0:
            messagebox.showerror("LaTeX", "No se pudo convertir ninguna etiqueta.")

    def _convert_text_element(self, el: ET.Element, *, silent: bool = False) -> bool:
        try:
            configure_mathtext, require_matplotlib = _resolve_latex_support()
            require_matplotlib()
            configure_mathtext()
        except Exception as exc:
            if not silent:
                messagebox.showerror("LaTeX", f"No se pudo cargar matplotlib: {exc}")
            return False
        text = (el.text or "").strip()
        if not text:
            return False
        x = _parse_float(_get_attr(el, "x"))
        y = _parse_float(_get_attr(el, "y"))
        font_size = self._label_font_size(el, 12.0)
        fill = _get_attr(el, "fill") or "#000000"
        try:
            dir_s = (el.get("data-dir") or "").strip().upper()
            use_anchor = el.get("data-anchor-x") is None or el.get("data-anchor-y") is None
            anchor = _label_anchor_for_dir(dir_s) if (dir_s and use_anchor) else None
            d = _latex_path_d(text, x, y, font_size, anchor=anchor)
        except Exception as exc:
            if not silent:
                messagebox.showerror("LaTeX", f"No se pudo convertir: {text!r}. Error: {exc}")
            return False
        tag = el.tag
        if tag.startswith("{") and "}" in tag:
            ns = tag.split("}", 1)[0][1:]
            new_tag = f"{{{ns}}}path"
        else:
            new_tag = "path"
        new_el = ET.Element(new_tag, {"d": d, "fill": fill})
        new_el.set("stroke", "none")
        if "id" in el.attrib:
            new_el.set("id", el.attrib["id"])
        if "transform" in el.attrib:
            new_el.set("transform", el.attrib["transform"])
        for key in (
            "data-anchor-x",
            "data-anchor-y",
            "data-dir",
            "data-offset",
            "data-anchor-frac",
            "data-label-bg",
            "data-label-bg-mode",
            "data-label-cut-shape",
            "data-label-id",
            "data-point-id",
        ):
            if key in el.attrib:
                new_el.set(key, el.attrib[key])
        new_el.set("data-text", text)
        new_el.set("data-x", _format_num(x))
        new_el.set("data-y", _format_num(y))
        new_el.set("data-font-size", _format_num(font_size))
        parent = self._parent_of(el)
        if parent is None:
            return False
        idx = list(parent).index(el)
        parent.remove(el)
        parent.insert(idx, new_el)
        return True

    def _ensure_latex_labels(self, *, silent: bool = True) -> bool:
        if self._svg_root is None:
            return False
        to_convert: list[ET.Element] = []
        for el in self._svg_root.iter():
            if _strip_ns(el.tag) == "text":
                to_convert.append(el)
        if not to_convert:
            return False
        changed = False
        for el in to_convert:
            if self._convert_text_element(el, silent=silent):
                changed = True
        return changed

    def _normalize_angle_label_anchor(self) -> bool:
        if self._svg_root is None:
            return False
        changed = False
        for el in self._svg_root.iter():
            if _strip_ns(el.tag) != "path":
                continue
            if el.get("data-angle-kind") != "label":
                continue
            text = (el.get("data-text") or "").strip()
            if not text:
                continue
            if el.get("data-x") is None or el.get("data-y") is None:
                continue
            frac = el.get("data-anchor-frac")
            if frac:
                parts = _parse_points(frac)
                if len(parts) >= 2:
                    if abs(parts[0] - 0.5) <= 1e-6 and abs(parts[1] - 0.5) <= 1e-6:
                        continue
            x = _parse_float(el.get("data-x"), 0.0)
            y = _parse_float(el.get("data-y"), 0.0)
            font_size = _parse_float(el.get("data-font-size"), 12.0)
            el.set("data-anchor-frac", "0.5,0.5")
            if self._update_latex_path(el, text, x, y, font_size, silent=True):
                changed = True
        return changed

    def _parent_of(self, target: ET.Element) -> ET.Element | None:
        if self._svg_root is None:
            return None
        for parent in self._svg_root.iter():
            for child in list(parent):
                if child is target:
                    return parent
        return None

    def _apply_scale(self) -> None:
        if self._svg_root is None:
            return
        try:
            current = float(self._scale_current.get().strip())
            new = float(self._scale_new.get().strip())
        except Exception:
            messagebox.showerror("Escala", "Escala invalida.")
            return
        if current <= 0 or new <= 0:
            messagebox.showerror("Escala", "Escala debe ser > 0.")
            return
        factor = new / current
        if abs(factor - 1.0) <= 1e-12:
            return
        self._push_history()
        self._scale_svg(factor)
        self._scale_current.set(str(new))
        self._scale_new.set(str(new))
        self._render_svg()

    def _scale_svg(self, factor: float) -> None:
        root = self._svg_root
        if root is None:
            return
        self._class_styles = self._collect_css_class_styles(root)
        angle_roots: list[ET.Element] = []
        angle_ids: set[str] = set()
        if abs(factor - 1.0) > 1e-12:
            for el in root.iter():
                if (el.get("data-angle-root") or "").strip() == "1":
                    angle_roots.append(el)
                    angle_id = (el.get("data-angle-id") or "").strip()
                    if angle_id:
                        angle_ids.add(angle_id)
        w = _parse_float(root.get("width"), 0.0)
        h = _parse_float(root.get("height"), 0.0)
        if w > 0:
            root.set("width", _format_num(w * factor))
        if h > 0:
            root.set("height", _format_num(h * factor))
        vb = root.get("viewBox")
        if vb:
            parts = _parse_points(vb)
            if len(parts) >= 4:
                min_x, min_y, vb_w, vb_h = parts[0], parts[1], parts[2], parts[3]
                root.set(
                    "viewBox",
                    f"{_format_num(min_x * factor)} {_format_num(min_y * factor)} {_format_num(vb_w * factor)} {_format_num(vb_h * factor)}",
                )
        defs_ids = self._defs_descendant_ids()
        for el in root.iter():
            if id(el) in defs_ids:
                continue
            angle_id = (el.get("data-angle-id") or "").strip()
            if angle_id and angle_id in angle_ids:
                continue
            tag = _strip_ns(el.tag)
            if tag == "line":
                for k in ("x1", "y1", "x2", "y2"):
                    v = _parse_float(_get_attr(el, k))
                    _set_attr(el, k, _format_num(v * factor))
            elif tag in ("polyline", "polygon"):
                pts = _parse_points(_get_attr(el, "points"))
                if not pts:
                    continue
                scaled = []
                for i, v in enumerate(pts):
                    scaled.append(_format_num(v * factor))
                _set_attr(el, "points", " ".join(scaled))
            elif tag == "circle":
                cx = _parse_float(_get_attr(el, "cx"))
                cy = _parse_float(_get_attr(el, "cy"))
                _set_attr(el, "cx", _format_num(cx * factor))
                _set_attr(el, "cy", _format_num(cy * factor))
                r = _parse_float(_get_attr(el, "r"))
                if not self._is_point_circle(el):
                    _set_attr(el, "r", _format_num(r * factor))
            elif tag == "ellipse":
                for k in ("cx", "cy", "rx", "ry"):
                    v = _parse_float(_get_attr(el, k))
                    _set_attr(el, k, _format_num(v * factor))
            elif tag == "rect":
                for k in ("x", "y", "width", "height"):
                    v = _parse_float(_get_attr(el, k))
                    _set_attr(el, k, _format_num(v * factor))
            elif tag == "text":
                old_x = _parse_float(_get_attr(el, "x"))
                old_y = _parse_float(_get_attr(el, "y"))
                nx = old_x * factor
                ny = old_y * factor
                _set_attr(el, "x", _format_num(nx))
                _set_attr(el, "y", _format_num(ny))
                if el.get("data-anchor-x") is not None:
                    ax = _parse_float(el.get("data-anchor-x"))
                    el.set("data-anchor-x", _format_num(ax * factor))
                if el.get("data-anchor-y") is not None:
                    ay = _parse_float(el.get("data-anchor-y"))
                    el.set("data-anchor-y", _format_num(ay * factor))
                text = (el.text or "").strip()
                if text and el.get("data-anchor-x") is not None and el.get("data-anchor-y") is not None:
                    dir_s = (el.get("data-dir") or "").strip().upper()
                    offset = _parse_float(el.get("data-offset"), None)
                    if not dir_s:
                        font_size = _parse_float(_get_attr(el, "font-size"), 12.0)
                        dir_s, inferred = self._infer_label_dir_offset(
                            _parse_float(el.get("data-anchor-x")),
                            _parse_float(el.get("data-anchor-y")),
                            nx,
                            ny,
                            text,
                            font_size,
                            False,
                        )
                        if offset is None:
                            offset = inferred
                    if offset is not None and dir_s:
                        font_size = _parse_float(_get_attr(el, "font-size"), 12.0)
                        lx, ly = _label_position_from_anchor(
                            _parse_float(el.get("data-anchor-x")),
                            _parse_float(el.get("data-anchor-y")),
                            text,
                            dir_s,
                            offset,
                            font_size,
                            False,
                        )
                        _set_attr(el, "x", _format_num(lx))
                        _set_attr(el, "y", _format_num(ly))
                        el.set("data-dir", dir_s)
                        el.set("data-offset", _format_num(offset))
            elif tag == "path":
                if el.get("data-text") is not None:
                    x = _parse_float(el.get("data-x"))
                    y = _parse_float(el.get("data-y"))
                    nx = x * factor
                    ny = y * factor
                    el.set("data-x", _format_num(nx))
                    el.set("data-y", _format_num(ny))
                    if el.get("data-anchor-x") is not None:
                        ax = _parse_float(el.get("data-anchor-x"))
                        el.set("data-anchor-x", _format_num(ax * factor))
                    if el.get("data-anchor-y") is not None:
                        ay = _parse_float(el.get("data-anchor-y"))
                        el.set("data-anchor-y", _format_num(ay * factor))
                    text = el.get("data-text") or ""
                    font_size = _parse_float(el.get("data-font-size"), 12.0)
                    dir_s = (el.get("data-dir") or "").strip().upper()
                    offset = _parse_float(el.get("data-offset"), None)
                    if text and el.get("data-anchor-x") is not None and el.get("data-anchor-y") is not None:
                        if not dir_s:
                            dir_s, inferred = self._infer_label_dir_offset(
                                _parse_float(el.get("data-anchor-x")),
                                _parse_float(el.get("data-anchor-y")),
                                nx,
                                ny,
                                text,
                                font_size,
                                True,
                            )
                            if offset is None:
                                offset = inferred
                        if offset is not None and dir_s:
                            lx, ly = _label_position_from_anchor(
                                _parse_float(el.get("data-anchor-x")),
                                _parse_float(el.get("data-anchor-y")),
                                text,
                                dir_s,
                                offset,
                                font_size,
                                True,
                            )
                            el.set("data-x", _format_num(lx))
                            el.set("data-y", _format_num(ly))
                            nx, ny = lx, ly
                            el.set("data-dir", dir_s)
                            el.set("data-offset", _format_num(offset))
                    ok = self._update_latex_path(el, text, nx, ny, font_size, silent=True)
                    if ok:
                        continue
                d = _get_attr(el, "d")
                if d:
                    _set_attr(el, "d", _scale_path_d(d, factor))
        if not angle_roots:
            return
        for root_el in angle_roots:
            if (root_el.get("data-angle-root") or "").strip() != "1":
                continue
            angle_id = (root_el.get("data-angle-id") or "").strip()
            if not angle_id:
                continue
            if root_el.get("data-angle-vx") is None or root_el.get("data-angle-vy") is None:
                continue
            vx = _parse_float(root_el.get("data-angle-vx"))
            vy = _parse_float(root_el.get("data-angle-vy"))
            root_el.set("data-angle-vx", _format_num(vx * factor))
            root_el.set("data-angle-vy", _format_num(vy * factor))
            settings = self._angle_settings_from_root(root_el)
            for key in ("ra", "delta", "s_len", "s_amp", "s_gap", "rect_len", "rect_h"):
                if key in settings:
                    settings[key] = float(settings[key]) * factor
            root_el.set("data-angle-ra", _format_num(settings.get("ra", 30.0)))
            root_el.set("data-angle-label-offset", _format_num(settings.get("label_offset", 15.0)))
            root_el.set("data-angle-double-delta", _format_num(settings.get("delta", 5.0)))
            root_el.set("data-angle-point-r", _format_num(settings.get("point_r", 2.0)))
            root_el.set("data-angle-s-len", _format_num(settings.get("s_len", 15.0)))
            root_el.set("data-angle-s-amp", _format_num(settings.get("s_amp", 5.0)))
            root_el.set("data-angle-s-gap", _format_num(settings.get("s_gap", 6.0)))
            root_el.set("data-angle-rect-len", _format_num(settings.get("rect_len", 40.0)))
            root_el.set("data-angle-rect-h", _format_num(settings.get("rect_h", 8.0)))
            self._rebuild_angle_group(root_el, settings=settings)

    def _save(self) -> None:
        self._save_as()

    def _sync_svg_for_export(self) -> str | None:
        if self._svg_root is None:
            return None
        # Garantiza que etiquetas de texto usen geometria estable para export
        # (incluye casos con contenido LaTeX/mathtext).
        try:
            self._ensure_latex_labels(silent=True)
        except Exception:
            pass
        self._class_styles = self._collect_css_class_styles(self._svg_root)
        self._sync_segment_dimensions()
        self._sync_label_backgrounds()
        self._sync_arrow_marker_if_used()
        self._sanitize_lg_arrow_marker()
        self._auto_expand_viewbox()
        self._sync_label_cut_masks()
        return ET.tostring(self._svg_root, encoding="unicode")

    def _cairosvg_marker_compat(self, raw_svg: str) -> str:
        """Build a CairoSVG-safe marker setup so marker-start keeps direction."""
        try:
            root = ET.fromstring(raw_svg)
        except Exception:
            return raw_svg

        tag = root.tag
        ns = ""
        if tag.startswith("{") and "}" in tag:
            ns = tag.split("}", 1)[0][1:]

        def _ns_tag(name: str) -> str:
            return f"{{{ns}}}{name}" if ns else name

        defs = None
        for child in list(root):
            if _strip_ns(child.tag) == "defs":
                defs = child
                break
        if defs is None:
            return raw_svg

        base = None
        for el in defs.iter():
            if _strip_ns(el.tag) == "marker" and (el.get("id") or "").strip() == "lg-arrow":
                base = el
                break
        if base is None:
            return raw_svg

        view_box = base.get("viewBox") or f"0 0 {_format_num(_ARROW_MARKER_VIEWBOX)} {_format_num(_ARROW_MARKER_VIEWBOX)}"
        marker_units = base.get("markerUnits") or "strokeWidth"
        marker_w = base.get("markerWidth") or "6"
        marker_h = base.get("markerHeight") or "6"
        ref_y = base.get("refY") or "5"
        ref_x_end = base.get("refX") or _format_num(_ARROW_MARKER_VIEWBOX)
        ref_x_start = _format_num(_ARROW_MARKER_VIEWBOX * _ARROW_RETREAT_FRAC)

        end_d = "M 0 0 L 10 5 L 0 10 L 2 5 z"
        start_d = "M 10 0 L 0 5 L 10 10 L 8 5 z"

        def _ensure_marker(mid: str, *, ref_x: str, d: str) -> None:
            marker = None
            for el in defs.iter():
                if _strip_ns(el.tag) == "marker" and (el.get("id") or "").strip() == mid:
                    marker = el
                    break
            if marker is None:
                marker = ET.Element(_ns_tag("marker"))
                marker.set("id", mid)
                defs.append(marker)
            marker.set("markerWidth", marker_w)
            marker.set("markerHeight", marker_h)
            marker.set("refX", ref_x)
            marker.set("refY", ref_y)
            marker.set("orient", "auto")
            marker.set("viewBox", view_box)
            marker.set("markerUnits", marker_units)

            path = None
            for child in list(marker):
                if _strip_ns(child.tag) != "path":
                    continue
                if path is None:
                    path = child
                else:
                    marker.remove(child)
            if path is None:
                path = ET.Element(_ns_tag("path"))
                marker.append(path)
            for stale in (
                "style",
                "stroke-width",
                "stroke-dasharray",
                "stroke-linecap",
                "stroke-linejoin",
                "stroke-miterlimit",
                "transform",
                "class",
            ):
                path.attrib.pop(stale, None)
            path.set("d", d)
            path.set("fill", "#000000")
            path.set("stroke", "none")

        end_id = "lg-arrow-end"
        start_id = "lg-arrow-start"
        _ensure_marker(end_id, ref_x=ref_x_end, d=end_d)
        _ensure_marker(start_id, ref_x=ref_x_start, d=start_d)

        defs_ids: set[int] = set()
        for el in root.iter():
            if _strip_ns(el.tag) != "defs":
                continue
            for child in el.iter():
                defs_ids.add(id(child))

        marker_ref_re = re.compile(r"^\s*url\(\s*['\"]?#([^)'\"]+)['\"]?\s*\)\s*$", flags=re.IGNORECASE)

        def _marker_id(raw: str) -> str | None:
            m = marker_ref_re.match(raw or "")
            if not m:
                return None
            return (m.group(1) or "").strip()

        for el in root.iter():
            if id(el) in defs_ids:
                continue
            raw_start = (el.get("marker-start") or "").strip()
            raw_end = (el.get("marker-end") or "").strip()
            start_mid = _marker_id(raw_start)
            end_mid = _marker_id(raw_end)
            if start_mid in ("lg-arrow", "lg-arrow-end"):
                el.set("marker-start", f"url(#{start_id})")
            if end_mid in ("lg-arrow", "lg-arrow-start"):
                el.set("marker-end", f"url(#{end_id})")

        return ET.tostring(root, encoding="unicode")

    def _export_png_as(self) -> None:
        if self._svg_root is None:
            return
        path = filedialog.asksaveasfilename(
            title="Exportar PNG",
            defaultextension=".png",
            filetypes=[("PNG", "*.png")],
        )
        if not path:
            return
        raw = self._sync_svg_for_export()
        if not raw:
            messagebox.showerror("Exportar PNG", "No hay contenido para exportar.")
            return
        # WYSIWYG export: render with the same matplotlib pipeline used by the visualizer.
        self._render_svg()
        if not self._draw_preview_png(
            export_png_path=path,
            update_canvas=False,
            force_transparent_bg=True,
            export_scale=4.0,
        ):
            return
        self._set_transform_status("PNG exportado 4x con fondo transparente.")

    def _export_pdf_as(self) -> None:
        if self._svg_root is None:
            return
        path = filedialog.asksaveasfilename(
            title="Exportar PDF",
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
        )
        if not path:
            return
        raw = self._sync_svg_for_export()
        if not raw:
            messagebox.showerror("Exportar PDF", "No hay contenido para exportar.")
            return
        # WYSIWYG export: render with the same matplotlib pipeline used by the visualizer.
        self._render_svg()
        if not self._draw_preview_png(export_pdf_path=path, update_canvas=False, force_transparent_bg=True):
            return
        self._set_transform_status("PDF exportado con fondo transparente.")

    def _save_as(self) -> None:
        if self._svg_root is None:
            return
        path = filedialog.asksaveasfilename(
            title="Guardar SVG",
            defaultextension=".svg",
            filetypes=[("SVG", "*.svg")],
        )
        if not path:
            return
        self._current_path = path
        self._update_save_button_state()
        self._write_svg(path)

    def _save_current(self) -> None:
        if self._svg_root is None:
            return
        if not self._current_path:
            self._update_save_button_state()
            return
        self._write_svg(self._current_path)
        self._set_transform_status(f"SVG guardado: {self._current_path}")

    def _update_save_button_state(self) -> None:
        if self._save_btn is None:
            return
        state = "normal" if self._current_path else "disabled"
        self._save_btn.configure(state=state)

    def _write_svg(self, path: str) -> None:
        if self._svg_root is None:
            return
        raw = self._sync_svg_for_export()
        if raw is None:
            return
        pretty = _pretty_xml(self._svg_root)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(pretty)
        except Exception as exc:
            messagebox.showerror("Guardar", f"No se pudo guardar: {exc}")
            return
        self.text_input.delete("1.0", "end")
        self.text_input.insert("1.0", pretty)
        self._update_line_numbers()
        self._last_svg_text_raw = raw


def main(initial_path: str | None = None) -> None:
    if initial_path is None and len(sys.argv) > 1:
        initial_path = sys.argv[1]
    app = SvgEditorApp(initial_path=initial_path)
    app.mainloop()


if __name__ == "__main__":
    main()
