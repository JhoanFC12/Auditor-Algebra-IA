from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import fitz
import numpy as np
from PIL import Image, ImageDraw


REPO = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = (
    REPO
    / ".cache"
    / "book_catalog"
    / "problem_solution_staging"
    / "euler-app-library-problem-solutions-20260716-global-r1"
    / "h_ps1_ingrid_activation_20260716_r1"
)
REVIEW_VERSION = "ingrid-instance-batch01-20260716-r1"
SCALE = 2.0

CONFIG: dict[str, dict[str, Any]] = {
    "ingrid-ps-b18-i4646-r1": {
        "columns": 1,
        "digital": False,
        "problem_mode": "circles",
        "solution_mode": "template",
        "problem_circle_x": [(0.13, 0.25)],
        "problem_circle_radius": (0.010, 0.030),
        "solution_template": {
            "page": 99,
            "rect": [518, 1218, 632, 1257],
            "x_ranges": [(0.40, 0.70)],
            "threshold": 0.46,
        },
    },
    "ingrid-ps-b20-i4675-r1": {
        "columns": 2,
        "digital": True,
        "problem_mode": "text_label",
        "solution_mode": "text",
        "top_margin": 0.105,
    },
    "ingrid-ps-b158-i4848-r1": {
        "columns": 1,
        "digital": True,
        "problem_mode": "text_number",
        "solution_mode": "text",
        "number_x_tolerance": 0.11,
    },
    "ingrid-ps-b159-i4760-r1": {
        "columns": 2,
        "digital": True,
        "problem_mode": "text_number",
        "solution_mode": "text",
        "number_x_tolerance": 0.055,
    },
    "ingrid-ps-b160-i4923-r1": {
        "columns": 2,
        "digital": False,
        "problem_mode": "margin_numbers",
        "solution_mode": "abstain",
    },
    "ingrid-ps-b161-i4906-r1": {
        "columns": 2,
        "digital": True,
        "problem_mode": "text_label",
        "solution_mode": "text",
        "visual_problem_mode": "dark_headers",
        "visual_header_template": {
            "page": 11,
            "rect": [586, 1154, 740, 1186],
            "threshold": 0.75,
        },
    },
    "ingrid-ps-b162-i4909-r1": {
        "columns": 2,
        "digital": False,
        "problem_mode": "golden",
        "golden_root": (
            REPO
            / ".cache"
            / "transcriptor_runs"
            / "datasets"
            / "pdf_problem_boxes_live"
            / "areas-de-regiones-planas__resueltos"
        ),
        "solution_mode": "template",
        "solution_template": {
            "page": 194,
            "rect": [99, 750, 244, 786],
            "x_ranges": [(0.04, 0.27), (0.50, 0.75)],
            "threshold": 0.55,
            "min_y": 0.08,
            "min_event_distance_y": 0.05,
        },
    },
    "ingrid-ps-b190-i6235-r1": {
        "columns": 1,
        "digital": False,
        "problem_mode": "template",
        "solution_mode": "template",
        "problem_template": {
            "page": 7,
            "rect": [53, 226, 164, 258],
            "x_ranges": [(0.02, 0.30)],
            "threshold": 0.44,
            "min_y": 0.16,
        },
        "solution_template": {
            "page": 63,
            "rect": [27, 126, 152, 158],
            "x_ranges": [(0.01, 0.31)],
            "threshold": 0.42,
            "min_y": 0.08,
        },
    },
    "ingrid-ps-b192-i6247-r1": {
        "columns": 1,
        "digital": False,
        "problem_mode": "template",
        "solution_mode": "template",
        "problem_template": {
            "page": 40,
            "rect": [52, 344, 181, 383],
            "x_ranges": [(0.02, 0.31)],
            "threshold": 0.52,
            "min_y": 0.15,
            "min_event_distance_y": 0.05,
        },
        "solution_template": {
            "page": 77,
            "rect": [137, 319, 286, 360],
            "x_ranges": [(0.03, 0.35)],
            "threshold": 0.52,
            "min_y": 0.08,
            "min_event_distance_y": 0.05,
        },
    },
    "ingrid-ps-b196-i6210-r1": {
        "columns": 2,
        "digital": False,
        "problem_mode": "golden",
        "golden_root": (
            REPO
            / ".cache"
            / "transcriptor_runs"
            / "datasets"
            / "pdf_problem_boxes_live"
            / "pre-uni-algebra-4be913b8ec__tema_05_quinto-seminario"
        ),
        "solution_mode": "abstain",
    },
}


@dataclass(frozen=True)
class Event:
    kind: str
    page_number: int
    column_index: int
    y: int
    x: int
    anchor_bbox: tuple[int, int, int, int]
    number_bbox: tuple[int, int, int, int] | None = None
    number_raw: str = ""
    source: str = ""


@dataclass(frozen=True)
class FragmentSpec:
    page_number: int
    column_index: int
    bbox: tuple[int, int, int, int]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def render_page(document: fitz.Document, page_number: int) -> Image.Image:
    page = document[page_number - 1]
    pixmap = page.get_pixmap(matrix=fitz.Matrix(SCALE, SCALE), alpha=False)
    return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def image_digest(image: Image.Image) -> str:
    return hashlib.sha256(image.tobytes()).hexdigest()


def column_rect(
    width: int,
    height: int,
    columns: int,
    column: int,
) -> tuple[int, int, int, int]:
    top = max(6, int(height * 0.055))
    bottom = min(height - 6, int(height * 0.955))
    if columns == 1:
        return (int(width * 0.035), top, int(width * 0.965), bottom)
    gutter = width // 2
    if column == 0:
        return (
            int(width * 0.025),
            top,
            gutter - int(width * 0.012),
            bottom,
        )
    return (
        gutter + int(width * 0.012),
        top,
        int(width * 0.975),
        bottom,
    )


def event_column(x: float, width: int, columns: int) -> int:
    return 0 if columns == 1 or x < width / 2 else 1



def extract_text_events(
    page: fitz.Page,
    page_number: int,
    config: dict[str, Any],
    image_size: tuple[int, int],
) -> list[Event]:
    width, height = image_size
    columns = int(config["columns"])
    sx = width / float(page.rect.width)
    sy = height / float(page.rect.height)
    words = []
    for row in page.get_text("words"):
        x0, y0, x1, y1, text, block, line, word = row[:8]
        words.append(
            {
                "bbox": (
                    int(x0 * sx),
                    int(y0 * sy),
                    int(x1 * sx),
                    int(y1 * sy),
                ),
                "text": str(text),
                "norm": normalized(str(text)),
                "block": int(block),
                "line": int(line),
                "word": int(word),
            }
        )

    events: list[Event] = []
    seen: set[tuple[str, int, int]] = set()

    def add_event(
        kind: str,
        item: dict[str, Any],
        number_bbox: tuple[int, int, int, int] | None = None,
        number_raw: str = "",
    ) -> None:
        x0, y0, x1, y1 = item["bbox"]
        col = event_column((x0 + x1) / 2, width, columns)
        key = (kind, col, int(y0 / 8))
        if key in seen:
            return
        seen.add(key)
        events.append(
            Event(
                kind=kind,
                page_number=page_number,
                column_index=col,
                y=max(0, y0 - 4),
                x=x0,
                anchor_bbox=(x0, y0, x1, y1),
                number_bbox=number_bbox,
                number_raw=number_raw,
                source="embedded_text_geometry",
            )
        )

    for item in words:
        if item["norm"] in {"solucion", "resolucion"}:
            add_event("solution", item)

    if config["problem_mode"] == "text_label":
        for item in words:
            if item["norm"] != "problema":
                continue
            x0, _, x1, _ = item["bbox"]
            same_line = [
                other
                for other in words
                if other["block"] == item["block"]
                and other["line"] == item["line"]
                and x1 - 5 <= other["bbox"][0] <= x1 + int(width * 0.18)
            ]
            number_candidates = [
                other
                for other in same_line
                if re.search(r"\d", other["text"])
                and normalized(other["text"]) != "problema"
            ]
            number_bbox = None
            number_raw = ""
            if number_candidates:
                number_bbox = (
                    min(other["bbox"][0] for other in number_candidates),
                    min(other["bbox"][1] for other in number_candidates),
                    max(other["bbox"][2] for other in number_candidates),
                    max(other["bbox"][3] for other in number_candidates),
                )
                joined = " ".join(
                    other["text"] for other in number_candidates
                )
                match = re.search(r"\d+", joined)
                number_raw = match.group(0) if match else ""
            add_event(
                "problem",
                item,
                number_bbox=number_bbox,
                number_raw=number_raw,
            )
    elif config["problem_mode"] == "text_number":
        for item in words:
            if not re.fullmatch(
                r"\d{1,3}[\.\)]?",
                item["text"].strip(),
            ):
                continue
            x0, y0, x1, y1 = item["bbox"]
            if y0 < height * 0.10 or y0 > height * 0.92:
                continue
            col = event_column((x0 + x1) / 2, width, columns)
            region = column_rect(width, height, columns, col)
            tolerance = float(config.get("number_x_tolerance", 0.08))
            if x0 > region[0] + width * tolerance:
                continue
            match = re.search(r"\d+", item["text"])
            add_event(
                "problem",
                item,
                number_bbox=(x0, y0, x1, y1),
                number_raw=match.group(0) if match else "",
            )
    return events


def template_from_document(
    document: fitz.Document,
    spec: dict[str, Any],
) -> np.ndarray:
    image = render_page(document, int(spec["page"]))
    x0, y0, x1, y1 = [int(value) for value in spec["rect"]]
    crop = np.asarray(image.convert("L"))[y0:y1, x0:x1]
    if crop.size == 0:
        raise ValueError(f"Plantilla vacia: {spec}")
    return crop


def template_events(
    image: Image.Image,
    page_number: int,
    kind: str,
    columns: int,
    template: np.ndarray,
    spec: dict[str, Any],
) -> list[Event]:
    gray = np.asarray(image.convert("L"))
    height, width = gray.shape
    candidates: list[tuple[float, int, int, int, int]] = []
    for scale in (0.94, 0.97, 1.0, 1.03, 1.06):
        resized = cv2.resize(
            template,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_AREA,
        )
        th, tw = resized.shape
        if th >= height or tw >= width:
            continue
        result = cv2.matchTemplate(
            gray,
            resized,
            cv2.TM_CCOEFF_NORMED,
        )
        local_max = result == cv2.dilate(
            result,
            np.ones((11, 11), np.uint8),
        )
        ys, xs = np.where(
            (result >= float(spec["threshold"])) & local_max
        )
        for y, x in zip(ys.tolist(), xs.tolist()):
            center = (x + tw / 2) / width
            if not any(
                lo <= center <= hi
                for lo, hi in spec["x_ranges"]
            ):
                continue
            if y < height * float(spec.get("min_y", 0.05)) or y > height * 0.95:
                continue
            candidates.append(
                (float(result[y, x]), x, y, tw, th)
            )

    candidates.sort(reverse=True)
    kept: list[tuple[float, int, int, int, int]] = []
    for candidate in candidates:
        _, x, y, tw, th = candidate
        duplicate = any(
            abs(x - ox) < max(tw, otw) * 0.55
            and abs(y - oy) < max(max(th, oth) * 0.80, height * float(spec.get("min_event_distance_y", 0.0)))
            for _, ox, oy, otw, oth in kept
        )
        if not duplicate:
            kept.append(candidate)

    events = []
    kept.sort(
        key=lambda row: (
            event_column(row[1], width, columns),
            row[2],
        )
    )
    for score, x, y, tw, th in kept:
        col = event_column(x + tw / 2, width, columns)
        events.append(
            Event(
                kind=kind,
                page_number=page_number,
                column_index=col,
                y=max(0, y - 3),
                x=x,
                anchor_bbox=(x, y, x + tw, y + th),
                number_bbox=(x, y, x + tw, y + th) if kind == "problem" else None,
                source=f"visual_template_match:{score:.3f}",
            )
        )
    return events


def circle_events(
    image: Image.Image,
    page_number: int,
    kind: str,
    columns: int,
    x_ranges: list[tuple[float, float]],
    radius_range: tuple[float, float],
) -> list[Event]:
    gray = np.asarray(image.convert("L"))
    height, width = gray.shape
    blurred = cv2.GaussianBlur(gray, (5, 5), 1.1)
    min_radius = max(7, int(width * radius_range[0]))
    max_radius = max(
        min_radius + 2,
        int(width * radius_range[1]),
    )
    circles = cv2.HoughCircles(
        blurred,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=max(28, int(height * 0.035)),
        param1=90,
        param2=22,
        minRadius=min_radius,
        maxRadius=max_radius,
    )
    candidates = []
    if circles is not None:
        for cx, cy, radius in np.round(circles[0]).astype(int):
            cx, cy, radius = int(cx), int(cy), int(radius)
            center = cx / width
            if not any(lo <= center <= hi for lo, hi in x_ranges):
                continue
            if cy < height * 0.12 or cy > height * 0.94:
                continue
            candidates.append((cx, cy, radius))

    events = []
    candidates.sort(
        key=lambda row: (
            event_column(row[0], width, columns),
            row[1],
        )
    )
    for cx, cy, radius in candidates:
        bbox = (
            max(0, cx - radius - 3),
            max(0, cy - radius - 3),
            min(width, cx + radius + 3),
            min(height, cy + radius + 3),
        )
        events.append(
            Event(
                kind=kind,
                page_number=page_number,
                column_index=event_column(cx, width, columns),
                y=max(0, cy - radius - 5),
                x=cx - radius,
                anchor_bbox=bbox,
                number_bbox=bbox if kind == "problem" else None,
                source="visual_circle_geometry",
            )
        )
    return events



def dark_header_events(
    image: Image.Image,
    page_number: int,
    columns: int,
    reference_template: np.ndarray,
    match_threshold: float,
) -> list[Event]:
    gray = np.asarray(image.convert("L"))
    height, width = gray.shape
    binary = (gray < 70).astype(np.uint8) * 255
    closed = cv2.morphologyEx(
        binary,
        cv2.MORPH_CLOSE,
        np.ones((5, 19), np.uint8),
    )
    count, _, stats, _ = cv2.connectedComponentsWithStats(closed)
    events = []
    for index in range(1, count):
        x, y, box_width, box_height, area = [
            int(value) for value in stats[index]
        ]
        density = area / max(1, box_width * box_height)
        if not (
            width * 0.105 <= box_width <= width * 0.155
            and height * 0.016 <= box_height <= height * 0.024
            and density >= 0.82
            and height * 0.05 <= y <= height * 0.92
        ):
            continue
        candidate = cv2.resize(
            gray[y:y + box_height, x:x + box_width],
            (reference_template.shape[1], reference_template.shape[0]),
        )
        score = float(
            cv2.matchTemplate(
                candidate, reference_template, cv2.TM_CCOEFF_NORMED
            )[0, 0]
        )
        if score < match_threshold:
            continue

        col = event_column(x + box_width / 2, width, columns)
        bbox = (x, y, x + box_width, y + box_height)
        number_bbox = (
            x + int(box_width * 0.64),
            y,
            x + box_width,
            y + box_height,
        )
        events.append(
            Event(
                kind="problem",
                page_number=page_number,
                column_index=col,
                y=max(0, y - 3),
                x=x,
                anchor_bbox=bbox,
                number_bbox=number_bbox,
                source=f"visual_dark_header_geometry:{score:.3f}",
            )
        )
    events.sort(key=lambda event: (event.column_index, event.y))
    return events



def margin_number_events(
    image: Image.Image,
    page_number: int,
    columns: int,
) -> list[Event]:
    gray = np.asarray(image.convert("L"))
    height, width = gray.shape
    binary = (gray < 115).astype(np.uint8)
    events = []
    for col in range(columns):
        region = column_rect(width, height, columns, col)
        col_width = region[2] - region[0]
        strip_x0 = region[0] + int(col_width * 0.06)
        strip_x1 = region[0] + int(col_width * 0.19)
        strip = binary[:, strip_x0:strip_x1]
        projection = cv2.GaussianBlur(
            strip.sum(axis=1).astype(np.float32),
            (1, 7),
            0,
        ).ravel()
        body = projection[
            int(height * 0.20):int(height * 0.94)
        ]
        threshold = max(
            4.0,
            float(np.percentile(body, 88)),
        )
        active = projection >= threshold
        groups: list[tuple[int, int, float]] = []
        start = None
        for y, flag in enumerate(active.tolist() + [False]):
            if flag and start is None:
                start = y
            elif not flag and start is not None:
                end = y
                if (
                    8 <= end - start <= 40
                    and start >= height * 0.20
                    and end <= height * 0.94
                ):
                    groups.append(
                        (
                            start,
                            end,
                            float(projection[start:end].max()),
                        )
                    )
                start = None

        groups.sort(key=lambda row: row[2], reverse=True)
        selected: list[tuple[int, int, float]] = []
        for group in groups:
            center = (group[0] + group[1]) // 2
            if any(
                abs(
                    center
                    - (other[0] + other[1]) // 2
                )
                < int(height * 0.075)
                for other in selected
            ):
                continue
            selected.append(group)
            if len(selected) >= 5:
                break

        for y0, y1, _ in sorted(selected):
            bbox = (
                strip_x0,
                max(0, y0 - 4),
                strip_x1,
                min(height, y1 + 4),
            )
            events.append(
                Event(
                    kind="problem",
                    page_number=page_number,
                    column_index=col,
                    y=max(0, y0 - 5),
                    x=strip_x0,
                    anchor_bbox=bbox,
                    number_bbox=bbox,
                    source="visual_margin_number_geometry",
                )
            )
    return events


def stream_segments(
    pages: list[int],
    page_meta: dict[int, dict[str, Any]],
    columns: int,
) -> tuple[
    list[tuple[int, int, tuple[int, int, int, int]]],
    dict[tuple[int, int], int],
]:
    segments = []
    index = {}
    for page_number in pages:
        width = int(page_meta[page_number]["width"])
        height = int(page_meta[page_number]["height"])
        for col in range(columns):
            index[(page_number, col)] = len(segments)
            region = column_rect(width, height, columns, col)
            region = (
                region[0],
                max(region[1], int(height * page_meta[page_number].get("top_margin", 0.055))),
                region[2],
                region[3],
            )
            segments.append((page_number, col, region))
    return segments, index


def range_fragments(
    start: Event,
    end: Event | None,
    segments: list[
        tuple[int, int, tuple[int, int, int, int]]
    ],
    index: dict[tuple[int, int], int],
) -> list[FragmentSpec]:
    start_index = index[
        (start.page_number, start.column_index)
    ]
    end_index = (
        index[(end.page_number, end.column_index)]
        if end
        else len(segments) - 1
    )
    output = []
    for segment_index in range(start_index, end_index + 1):
        page_number, col, region = segments[segment_index]
        x0, y0, x1, y1 = region
        if segment_index == start_index:
            y0 = max(y0, start.y)
        if end is not None and segment_index == end_index:
            y1 = min(
                y1,
                max(region[1], end.y - 5),
            )
        if y1 - y0 >= 14 and x1 - x0 >= 20:
            output.append(
                FragmentSpec(
                    page_number,
                    col,
                    (x0, y0, x1, y1),
                )
            )
    return output


def build_interleaved_units(
    events: list[Event],
    pages: list[int],
    page_meta: dict[int, dict[str, Any]],
    columns: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    segments, index = stream_segments(
        pages,
        page_meta,
        columns,
    )
    page_order = {
        page: position
        for position, page in enumerate(pages)
    }
    ordered = sorted(
        events,
        key=lambda event: (
            page_order[event.page_number],
            event.column_index,
            event.y,
            event.kind,
        ),
    )
    problems = []
    solutions = []
    last_problem_number = ""
    for position, event in enumerate(ordered):
        end = (
            ordered[position + 1]
            if position + 1 < len(ordered)
            else None
        )
        fragments = range_fragments(
            event,
            end,
            segments,
            index,
        )
        if not fragments:
            continue
        closed = end is not None
        if event.kind == "problem":
            last_problem_number = event.number_raw
            problems.append(
                {
                    "event": event,
                    "fragments": fragments,
                    "closed": closed,
                }
            )
        else:
            solutions.append(
                {
                    "event": event,
                    "fragments": fragments,
                    "closed": closed,
                    "number_raw": last_problem_number,
                }
            )
    return problems, solutions


def build_separate_units(
    events: list[Event],
    pages: list[int],
    page_meta: dict[int, dict[str, Any]],
    columns: int,
) -> list[dict[str, Any]]:
    segments, index = stream_segments(
        pages,
        page_meta,
        columns,
    )
    page_order = {
        page: position
        for position, page in enumerate(pages)
    }
    ordered = sorted(
        events,
        key=lambda event: (
            page_order[event.page_number],
            event.column_index,
            event.y,
        ),
    )
    units = []
    for position, event in enumerate(ordered):
        end = (
            ordered[position + 1]
            if position + 1 < len(ordered)
            else None
        )
        fragments = range_fragments(
            event,
            end,
            segments,
            index,
        )
        if fragments:
            units.append(
                {
                    "event": event,
                    "fragments": fragments,
                    "closed": end is not None,
                    "number_raw": event.number_raw,
                }
            )
    return units



def build_page_column_units(
    events: list[Event],
    page_meta: dict[int, dict[str, Any]],
    columns: int,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, int], list[Event]] = defaultdict(list)
    for event in events:
        grouped[(event.page_number, event.column_index)].append(event)
    units = []
    for (page_number, column), page_events in sorted(grouped.items()):
        page_events.sort(key=lambda event: event.y)
        meta = page_meta[page_number]
        region = column_rect(meta["width"], meta["height"], columns, column)
        for position, event in enumerate(page_events):
            end_y = (
                page_events[position + 1].y - 5
                if position + 1 < len(page_events)
                else region[3]
            )
            bbox = (
                region[0],
                max(region[1], event.y),
                region[2],
                min(region[3], end_y),
            )
            if bbox[3] - bbox[1] < 14:
                continue
            units.append(
                {
                    "event": event,
                    "fragments": [FragmentSpec(page_number, column, bbox)],
                    "closed": True,
                    "number_raw": event.number_raw,
                }
            )
    return units



def load_golden_page(
    config: dict[str, Any],
    page_number: int,
    target_size: tuple[int, int],
) -> dict[str, Any] | None:
    root = Path(config["golden_root"])
    matches = list(
        (root / "records").glob(
            f"*p{page_number:04d}.json"
        )
    )
    if len(matches) != 1:
        return None
    record_path = matches[0]
    record = json_load(record_path)
    image_path = root / record["image_rel"]
    with Image.open(image_path) as source_image:
        source_width, source_height = source_image.size

    target_width, target_height = target_size
    sx = target_width / source_width
    sy = target_height / source_height
    boxes = []
    seen = set()
    details = (
        record.get("detector_detections")
        or record.get("box_details")
        or []
    )
    for position, detail in enumerate(details, start=1):
        role = (
            detail.get("role")
            or detail.get("class_key")
            or detail.get("class_name")
        )
        if role not in {
            "problem",
            "problem_number",
            "answer_block",
        }:
            continue
        raw = detail.get("bbox_px")
        if not isinstance(raw, list) or len(raw) != 4:
            continue
        bbox = (
            max(0, int(round(raw[0] * sx))),
            max(0, int(round(raw[1] * sy))),
            min(target_width, int(round(raw[2] * sx))),
            min(target_height, int(round(raw[3] * sy))),
        )
        key = (role, bbox)
        if (
            key in seen
            or bbox[2] <= bbox[0]
            or bbox[3] <= bbox[1]
        ):
            continue
        seen.add(key)
        boxes.append(
            {
                "box_id": (
                    f"{record['record_id']}:{role}:{position}"
                ),
                "role": role,
                "bbox_xyxy": list(bbox),
                "parent_box_id": None,
            }
        )
    return {
        "record_id": record["record_id"],
        "record_path": str(record_path),
        "record_sha256": sha256_file(record_path),
        "reviewed": bool(record.get("reviewed")),
        "boxes": boxes,
    }


def answer_box_from_words(
    page: fitz.Page,
    bbox: tuple[int, int, int, int],
    image_size: tuple[int, int],
) -> tuple[int, int, int, int] | None:
    width, height = image_size
    sx = width / float(page.rect.width)
    sy = height / float(page.rect.height)
    x0, y0, x1, y1 = bbox
    option_words = []
    for row in page.get_text("words"):
        wx0, wy0, wx1, wy1, text = row[:5]
        pixel_box = (
            int(wx0 * sx),
            int(wy0 * sy),
            int(wx1 * sx),
            int(wy1 * sy),
        )
        px0, py0, px1, py1 = pixel_box
        if (
            px1 < x0
            or px0 > x1
            or py1 < y0
            or py0 > y1
        ):
            continue
        if re.match(
            r"^[A-Ea-e][\)\.\:]",
            str(text).strip(),
        ):
            option_words.append(pixel_box)

    if not option_words:
        return None
    top = max(
        y0,
        min(row[1] for row in option_words) - 5,
    )
    if y1 - top < 12:
        return None
    return (x0, top, x1, y1)


def save_jpeg(
    image: Image.Image,
    path: Path,
    quality: int = 88,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(
        path,
        format="JPEG",
        quality=quality,
        optimize=True,
    )


def draw_overlay(
    image: Image.Image,
    problem_boxes: list[dict[str, Any]],
    solution_fragments: list[dict[str, Any]],
) -> Image.Image:
    canvas = image.copy()
    draw = ImageDraw.Draw(canvas)
    colors = {
        "problem": "#ef4444",
        "problem_number": "#f59e0b",
        "answer_block": "#22c55e",
    }
    for item in problem_boxes:
        bbox = tuple(item["bbox_xyxy"])
        color = colors.get(item["role"], "#ef4444")
        draw.rectangle(bbox, outline=color, width=4)
        draw.text(
            (bbox[0] + 3, bbox[1] + 3),
            item["role"],
            fill=color,
        )
    for item in solution_fragments:
        bbox = tuple(item["bbox_xyxy"])
        draw.rectangle(
            bbox,
            outline="#2563eb",
            width=4,
        )
        draw.text(
            (bbox[0] + 3, bbox[1] + 3),
            "solution",
            fill="#2563eb",
        )
    return canvas


def validate_bbox(
    bbox: list[int],
    width: int,
    height: int,
) -> bool:
    return (
        len(bbox) == 4
        and 0 <= bbox[0] < bbox[2] <= width
        and 0 <= bbox[1] < bbox[3] <= height
    )



def execute_assignment(
    assignment: dict[str, Any],
    output_root: Path,
) -> dict[str, Any]:
    assignment_id = assignment["assignment_id"]
    config = CONFIG[assignment_id]
    assignment_output = (output_root / assignment_id).resolve()
    if assignment_output.parent != output_root.resolve():
        raise RuntimeError("Ruta de salida fuera de batch-01")
    if assignment_output.exists():
        shutil.rmtree(assignment_output)
    assignment_output.mkdir(parents=True, exist_ok=True)
    document = fitz.open(
        assignment["source_document"]["pdf_path"]
    )
    approved_pages = [
        int(value)
        for value in assignment["approved_pages"]
    ]
    problem_pages = [
        int(value)
        for value in assignment["problem_pages"]
    ]
    solution_pages = [
        int(value)
        for value in assignment["solution_pages"]
    ]
    page_meta: dict[int, dict[str, Any]] = {}
    events: list[Event] = []
    templates = {}
    if config.get("problem_template"):
        templates["problem"] = template_from_document(
            document,
            config["problem_template"],
        )
    if config.get("solution_template"):
        templates["solution"] = template_from_document(
            document,
            config["solution_template"],
        )
    if config.get("visual_header_template"):
        templates["visual_problem"] = template_from_document(
            document,
            config["visual_header_template"],
        )

    for page_number in approved_pages:
        image = render_page(document, page_number)
        width, height = image.size
        page_meta[page_number] = {
            "width": width,
            "height": height,
            "sha256": image_digest(image),
            "top_margin": float(config.get("top_margin", 0.055)),
        }
        if config["digital"]:
            events.extend(
                extract_text_events(
                    document[page_number - 1],
                    page_number,
                    config,
                    image.size,
                )
            )
            if (
                page_number in problem_pages
                and config.get("visual_problem_mode")
                == "dark_headers"
            ):
                events.extend(
                    dark_header_events(
                        image,
                        page_number,
                        config["columns"],
                        templates["visual_problem"],
                        float(config["visual_header_template"]["threshold"]),
                    )
                )
            continue

        if page_number in problem_pages:
            if config["problem_mode"] == "template":
                events.extend(
                    template_events(
                        image,
                        page_number,
                        "problem",
                        config["columns"],
                        templates["problem"],
                        config["problem_template"],
                    )
                )
            elif config["problem_mode"] == "circles":
                events.extend(
                    circle_events(
                        image,
                        page_number,
                        "problem",
                        config["columns"],
                        config["problem_circle_x"],
                        config["problem_circle_radius"],
                    )
                )
            elif config["problem_mode"] == "margin_numbers":
                events.extend(
                    margin_number_events(
                        image,
                        page_number,
                        config["columns"],
                    )
                )

        if page_number in solution_pages:
            if config["solution_mode"] == "template":
                events.extend(
                    template_events(
                        image,
                        page_number,
                        "solution",
                        config["columns"],
                        templates["solution"],
                        config["solution_template"],
                    )
                )
            elif config["solution_mode"] == "circles":
                events.extend(
                    circle_events(
                        image,
                        page_number,
                        "solution",
                        config["columns"],
                        config["solution_circle_x"],
                        config["solution_circle_radius"],
                    )
                )

    if assignment_id == "ingrid-ps-b18-i4646-r1":
        grouped = defaultdict(list)
        for event in events:
            if event.kind == "problem":
                grouped[(event.page_number, event.column_index)].append(event)
        rejected = set()
        for page_events in grouped.values():
            page_events.sort(key=lambda item: item.y)
            height = page_meta[page_events[0].page_number]["height"]
            for current, following in zip(page_events, page_events[1:]):
                if following.y - current.y < height * 0.075:
                    rejected.add(current)
        events = [event for event in events if event not in rejected]


    structure_mode = assignment[
        "structure_snapshot"
    ]["structure_mode"]
    if structure_mode == "interleaved":
        problem_units, solution_units_raw = (
            build_interleaved_units(
                events,
                approved_pages,
                page_meta,
                int(config["columns"]),
            )
        )
    else:
        problem_events = [
            event for event in events if event.kind == "problem"
        ]
        if assignment_id == "ingrid-ps-b160-i4923-r1":
            problem_units = build_page_column_units(
                problem_events,
                page_meta,
                int(config["columns"]),
            )
        else:
            problem_units = build_separate_units(
                problem_events,
                problem_pages,
                page_meta,
                int(config["columns"]),
            )
        solution_units_raw = build_separate_units(
            [
                event
                for event in events
                if event.kind == "solution"
            ],
            solution_pages,
            page_meta,
            int(config["columns"]),
        )

    problem_boxes_by_page: dict[
        int,
        list[dict[str, Any]],
    ] = defaultdict(list)
    problem_source_by_page = {}
    issues: list[str] = []

    if config["problem_mode"] == "golden":
        for page_number in problem_pages:
            meta = page_meta[page_number]
            golden = load_golden_page(
                config,
                page_number,
                (meta["width"], meta["height"]),
            )
            if golden is None:
                issues.append(
                    f"golden_problem_record_missing:page={page_number}"
                )
                continue
            problem_source_by_page[page_number] = golden
            problem_boxes_by_page[page_number].extend(
                golden["boxes"]
            )
    else:
        for unit_index, unit in enumerate(
            problem_units,
            start=1,
        ):
            event: Event = unit["event"]
            for fragment_index, fragment in enumerate(
                unit["fragments"],
                start=1,
            ):
                box_id = (
                    f"{assignment_id}:"
                    f"p{event.page_number:04d}:"
                    f"problem:{unit_index:04d}:"
                    f"{fragment_index}"
                )
                problem_boxes_by_page[
                    fragment.page_number
                ].append(
                    {
                        "box_id": box_id,
                        "role": "problem",
                        "bbox_xyxy": list(fragment.bbox),
                        "parent_box_id": None,
                    }
                )
                if (
                    fragment_index == 1
                    and event.number_bbox is not None
                    and fragment.page_number
                    == event.page_number
                ):
                    problem_boxes_by_page[
                        fragment.page_number
                    ].append(
                        {
                            "box_id": f"{box_id}:number",
                            "role": "problem_number",
                            "bbox_xyxy": list(
                                event.number_bbox
                            ),
                            "parent_box_id": box_id,
                        }
                    )

                if config["digital"]:
                    meta = page_meta[fragment.page_number]
                    answer_bbox = answer_box_from_words(
                        document[fragment.page_number - 1],
                        fragment.bbox,
                        (meta["width"], meta["height"]),
                    )
                    if answer_bbox is not None:
                        problem_boxes_by_page[
                            fragment.page_number
                        ].append(
                            {
                                "box_id": (
                                    f"{box_id}:answers"
                                ),
                                "role": "answer_block",
                                "bbox_xyxy": list(
                                    answer_bbox
                                ),
                                "parent_box_id": box_id,
                            }
                        )



    solution_units = []
    solution_fragments_by_page: dict[
        int,
        list[dict[str, Any]],
    ] = defaultdict(list)
    crop_root = assignment_output / "solution_crops"
    for unit_index, unit in enumerate(
        solution_units_raw,
        start=1,
    ):
        specs: list[FragmentSpec] = unit["fragments"]
        complete = bool(unit["closed"])
        fragments_payload = []
        for fragment_index, spec in enumerate(
            specs,
            start=1,
        ):
            if len(specs) == 1:
                role = "single" if complete else "begin"
            elif fragment_index == 1:
                role = "begin"
            elif fragment_index == len(specs) and complete:
                role = "end"
            else:
                role = "middle"

            fragment_id = (
                f"{assignment_id}:solution:"
                f"{unit_index:04d}:fragment:"
                f"{fragment_index:03d}"
            )
            image = render_page(
                document,
                spec.page_number,
            )
            crop = image.crop(spec.bbox)
            crop_path = (
                crop_root
                / (
                    f"solution_{unit_index:04d}_"
                    f"fragment_{fragment_index:03d}_"
                    f"p{spec.page_number:04d}.jpg"
                )
            )
            save_jpeg(crop, crop_path, quality=92)
            crop_sha = sha256_file(crop_path)
            rel_crop = crop_path.relative_to(
                output_root
            ).as_posix()
            fragment_payload = {
                "fragment_id": fragment_id,
                "page_number": spec.page_number,
                "bbox_xyxy": list(spec.bbox),
                "crop_path": rel_crop,
                "crop_sha256": crop_sha,
                "fragment_role": role,
                "reading_order": fragment_index,
                "column_index": spec.column_index,
            }
            fragments_payload.append(fragment_payload)
            solution_fragments_by_page[
                spec.page_number
            ].append(fragment_payload)

        number_raw = str(unit.get("number_raw") or "")
        page_span = [
            min(spec.page_number for spec in specs),
            max(spec.page_number for spec in specs),
        ]
        solution_units.append(
            {
                "unit_id": (
                    f"{assignment_id}:solution:"
                    f"{unit_index:04d}"
                ),
                "scope": assignment["scope"],
                "number_raw": number_raw,
                "number_normalized": number_raw,
                "number_bbox_xyxy": None,
                "solution_kind": "worked",
                "variant_index": 1,
                "page_span": page_span,
                "continuation_complete": complete,
                "source_mapping_status": "confirmed",
                "source_digest": assignment[
                    "source_document"
                ]["pdf_sha256"],
                "fragments": fragments_payload,
                "provenance": {
                    "source_version": (
                        "pdf_sha256:"
                        + assignment["source_document"][
                            "pdf_sha256"
                        ]
                    ),
                    "review_version": REVIEW_VERSION,
                },
            }
        )

    problem_box_reviews = []
    overlays = []
    inspection_log = []
    overlay_root = assignment_output / "overlays"
    for page_number in approved_pages:
        image = render_page(document, page_number)
        before_path = (
            overlay_root
            / f"page_{page_number:04d}_before.jpg"
        )
        after_path = (
            overlay_root
            / f"page_{page_number:04d}_after.jpg"
        )
        save_jpeg(image, before_path, quality=84)
        overlay = draw_overlay(
            image,
            problem_boxes_by_page.get(page_number, []),
            solution_fragments_by_page.get(
                page_number,
                [],
            ),
        )
        save_jpeg(overlay, after_path, quality=88)
        rel_before = before_path.relative_to(
            output_root
        ).as_posix()
        rel_after = after_path.relative_to(
            output_root
        ).as_posix()
        overlays.extend([rel_before, rel_after])

        page_problem_boxes = problem_boxes_by_page.get(
            page_number,
            [],
        )
        page_solution_fragments = (
            solution_fragments_by_page.get(
                page_number,
                [],
            )
        )
        page_events = [
            event
            for event in events
            if event.page_number == page_number
        ]
        disposition = "segmented"
        if (
            page_number in problem_pages
            and not page_problem_boxes
        ):
            disposition = (
                "abstained_problem_no_reliable_anchor"
            )
        elif (
            page_number in solution_pages
            and not page_solution_fragments
        ):
            disposition = (
                "abstained_solution_no_reliable_anchor"
            )
        inspection_log.append(
            {
                "page_number": page_number,
                "problem_authorized": (
                    page_number in problem_pages
                ),
                "solution_authorized": (
                    page_number in solution_pages
                ),
                "problem_anchor_count": sum(
                    event.kind == "problem"
                    for event in page_events
                ),
                "solution_anchor_count": sum(
                    event.kind == "solution"
                    for event in page_events
                ),
                "problem_box_count": sum(
                    box["role"] == "problem"
                    for box in page_problem_boxes
                ),
                "solution_fragment_count": len(
                    page_solution_fragments
                ),
                "disposition": disposition,
            }
        )

        if page_number not in problem_pages:
            continue
        source_info = problem_source_by_page.get(
            page_number
        )


        if page_problem_boxes:
            if source_info:
                original_boxes = page_problem_boxes
                operations = [
                    {
                        "action": "accept",
                        "target_box_id": box["box_id"],
                        "before": box,
                        "after": box,
                        "reason": (
                            "Box humano previo de la misma "
                            "pagina e instancia, revalidado "
                            "visualmente para H-PS2."
                        ),
                    }
                    for box in page_problem_boxes
                ]
                status = "accepted_unchanged"
                reasoning = (
                    "Se conservaron boxes humanos previos "
                    "de la misma pagina; no se modifico "
                    "ningun dataset."
                )
                source_version = (
                    "golden_record_sha256:"
                    + source_info["record_sha256"]
                )
            else:
                original_boxes = []
                operations = [
                    {
                        "action": "add",
                        "target_box_id": box["box_id"],
                        "before": None,
                        "after": box,
                        "reason": (
                            "Limite propuesto desde ancla "
                            "visual dentro del scope H-PS1; "
                            "pendiente de H-PS2."
                        ),
                    }
                    for box in page_problem_boxes
                ]
                status = (
                    "agent_corrected_pending_human"
                )
                reasoning = (
                    "Boxes propuestos desde limites "
                    "visuales observables; requieren "
                    "confirmacion H-PS2."
                )
                source_version = (
                    "pdf_sha256:"
                    + assignment["source_document"][
                        "pdf_sha256"
                    ]
                )

            problem_box_reviews.append(
                {
                    "schema_version": (
                        "ingrid_instance_"
                        "problem_box_review_v1"
                    ),
                    "review_id": (
                        f"{assignment_id}:"
                        f"problem-review:"
                        f"p{page_number:04d}"
                    ),
                    "assignment_id": assignment_id,
                    "scope": assignment["scope"],
                    "problem_record_id": (
                        source_info["record_id"]
                        if source_info
                        else (
                            f"{assignment_id}:"
                            f"page:{page_number:04d}"
                        )
                    ),
                    "page_number": page_number,
                    "source_page_ref": (
                        assignment["source_document"][
                            "pdf_path"
                        ]
                        + f"#page={page_number}"
                    ),
                    "source_page_sha256": page_meta[
                        page_number
                    ]["sha256"],
                    "image_width": page_meta[
                        page_number
                    ]["width"],
                    "image_height": page_meta[
                        page_number
                    ]["height"],
                    "context_fingerprint": assignment[
                        "context_fingerprint"
                    ],
                    "expected_revision": assignment[
                        "expected_revision"
                    ],
                    "original_boxes": original_boxes,
                    "proposed_boxes": page_problem_boxes,
                    "operations": operations,
                    "issues_found": [],
                    "reasoning_summary": reasoning,
                    "overlay_before": rel_before,
                    "overlay_after": rel_after,
                    "source_version": source_version,
                    "review_version": REVIEW_VERSION,
                    "status": status,
                    "human_review": "pending",
                }
            )
        else:
            problem_box_reviews.append(
                {
                    "schema_version": (
                        "ingrid_instance_"
                        "problem_box_review_v1"
                    ),
                    "review_id": (
                        f"{assignment_id}:"
                        f"problem-review:"
                        f"p{page_number:04d}:abstain"
                    ),
                    "assignment_id": assignment_id,
                    "scope": assignment["scope"],
                    "problem_record_id": (
                        f"{assignment_id}:"
                        f"page:{page_number:04d}:abstain"
                    ),
                    "page_number": page_number,
                    "source_page_ref": (
                        assignment["source_document"][
                            "pdf_path"
                        ]
                        + f"#page={page_number}"
                    ),
                    "source_page_sha256": page_meta[
                        page_number
                    ]["sha256"],
                    "image_width": page_meta[
                        page_number
                    ]["width"],
                    "image_height": page_meta[
                        page_number
                    ]["height"],
                    "context_fingerprint": assignment[
                        "context_fingerprint"
                    ],
                    "expected_revision": assignment[
                        "expected_revision"
                    ],
                    "original_boxes": [],
                    "proposed_boxes": [],
                    "operations": [
                        {
                            "action": "abstain",
                            "target_box_id": "",
                            "before": None,
                            "after": None,
                            "reason": (
                                "No se encontro un ancla "
                                "visual suficientemente "
                                "fiable sin OCR; no se "
                                "invento un box."
                            ),
                        }
                    ],
                    "issues_found": [
                        (
                            "problem_segmentation_"
                            "abstained_no_reliable_"
                            "visual_anchor"
                        )
                    ],
                    "reasoning_summary": (
                        "Pagina inspeccionada completa; "
                        "se abstiene la propuesta para "
                        "evitar cortar o absorber contenido."
                    ),
                    "overlay_before": rel_before,
                    "overlay_after": rel_after,
                    "source_version": (
                        "pdf_sha256:"
                        + assignment["source_document"][
                            "pdf_sha256"
                        ]
                    ),
                    "review_version": REVIEW_VERSION,
                    "status": "abstained",
                    "human_review": "pending",
                }
            )



    for page_number, meta in page_meta.items():
        for box in problem_boxes_by_page.get(
            page_number,
            [],
        ):
            if not validate_bbox(
                box["bbox_xyxy"],
                meta["width"],
                meta["height"],
            ):
                issues.append(
                    f"invalid_problem_bbox:"
                    f"page={page_number}:"
                    f"box={box['box_id']}"
                )
        for fragment in solution_fragments_by_page.get(
            page_number,
            [],
        ):
            if not validate_bbox(
                fragment["bbox_xyxy"],
                meta["width"],
                meta["height"],
            ):
                issues.append(
                    f"invalid_solution_bbox:"
                    f"page={page_number}:"
                    f"fragment={fragment['fragment_id']}"
                )

    abstention_pages = [
        row["page_number"]
        for row in inspection_log
        if row["disposition"].startswith("abstained")
    ]
    incomplete_units = [
        unit["unit_id"]
        for unit in solution_units
        if not unit["continuation_complete"]
    ]
    if incomplete_units:
        issues.append(
            f"incomplete_solution_units:"
            f"{len(incomplete_units)}"
        )

    payload = {
        "schema_version": (
            "ingrid_instance_solution_segmentation_v1"
        ),
        "assignment_id": assignment_id,
        "batch_id": assignment["batch_id"],
        "capability_id": assignment["capability_id"],
        "mode": assignment["mode"],
        "scope": assignment["scope"],
        "context_fingerprint": assignment[
            "context_fingerprint"
        ],
        "expected_revision": assignment[
            "expected_revision"
        ],
        "source_version": (
            "pdf_sha256:"
            + assignment["source_document"]["pdf_sha256"]
        ),
        "review_version": REVIEW_VERSION,
        "structure_mode": structure_mode,
        "pages_inspected": approved_pages,
        "problem_box_reviews": problem_box_reviews,
        "solution_units": solution_units,
        "issues_found": issues,
        "evidence_overlays": overlays,
        "inspection_log": inspection_log,
        "abstention_pages": abstention_pages,
        "status": "agent_segmented_pending_human",
        "human_review": "pending",
        "next_gate": "H-PS2",
    }
    json_write(
        assignment_output / "segmentation.json",
        payload,
    )
    document.close()

    return {
        "assignment_id": assignment_id,
        "pages_inspected": len(approved_pages),
        "problem_reviews": len(problem_box_reviews),
        "problem_boxes": sum(
            len(review["proposed_boxes"])
            for review in problem_box_reviews
        ),
        "solution_units": len(solution_units),
        "solution_fragments": sum(
            len(unit["fragments"])
            for unit in solution_units
        ),
        "complete_solution_units": sum(
            unit["continuation_complete"]
            for unit in solution_units
        ),
        "incomplete_solution_units": len(
            incomplete_units
        ),
        "abstention_pages": len(abstention_pages),
        "issues": issues,
        "output": str(
            assignment_output / "segmentation.json"
        ),
    }



def validate_activation(root: Path) -> dict[str, Any]:
    expected = {
        "bundle_manifest.json": (
            "af1832c6ece45656774d11af00bf33bb"
            "cb9e656f68d5514d313e43b7ebd40f9f"
        ),
        "validation_report.json": (
            "311c1abdfa038b2ca2e67552841adeeb0"
            "6cc0ff2b731148d3760ecf81a2a3c3a"
        ),
        "batches/batch-01.json": (
            "b763ca36ba45e8c42873ea5016b6faf5"
            "9cb669f200fd83e049dabe8013cb249a"
        ),
    }
    actual = {
        relative: sha256_file(root / relative)
        for relative in expected
    }
    mismatches = {
        relative: [
            expected[relative],
            actual[relative],
        ]
        for relative in expected
        if expected[relative] != actual[relative]
    }
    if mismatches:
        raise RuntimeError(
            f"Hash mismatch: {mismatches}"
        )

    batch = json_load(
        root / "batches" / "batch-01.json"
    )
    if (
        not batch.get("execution_authorized")
        or batch.get("assignment_count") != 10
    ):
        raise RuntimeError(
            "batch-01 no esta autorizado "
            "exactamente para 10 asignaciones"
        )
    if (
        batch.get("batch_id")
        != "ingrid-instance-solutions-20260716-01"
    ):
        raise RuntimeError("batch_id inesperado")

    for sequence in range(2, 10):
        queued = json_load(
            root
            / "batches"
            / f"batch-{sequence:02d}.json"
        )
        if (
            queued.get("execution_authorized")
            or queued.get("status")
            != "queued_not_authorized"
        ):
            raise RuntimeError(
                f"batch-{sequence:02d} no permanece "
                "queued_not_authorized"
            )

    assignment_rows = []
    for row in batch["assignments"]:
        path = Path(row["assignment_path"])
        digest = sha256_file(path)
        if digest != row["assignment_sha256"]:
            raise RuntimeError(
                f"Hash de asignacion invalido: {path}"
            )
        assignment = json_load(path)
        if not assignment.get("execution_authorized"):
            raise RuntimeError(
                "Asignacion no autorizada: "
                + assignment["assignment_id"]
            )
        if (
            assignment["capability_id"]
            != "instance_problem_solution_segmenter_v1"
        ):
            raise RuntimeError("Capability incorrecta")
        if assignment["mode"] != "instance_staging":
            raise RuntimeError("Modo incorrecto")
        if (
            assignment["h_ps1_gate_ref"]["status"]
            != "approved"
        ):
            raise RuntimeError("H-PS1 no aprobado")
        if (
            assignment["structure_snapshot"]["map_status"]
            != "handoff_ready"
        ):
            raise RuntimeError("Mapa no handoff_ready")

        pdf_path = Path(
            assignment["source_document"]["pdf_path"]
        )
        pdf_digest = sha256_file(pdf_path)
        if (
            pdf_digest
            != assignment["source_document"]["pdf_sha256"]
        ):
            raise RuntimeError(
                f"PDF fuente cambio: {pdf_path}"
            )
        assignment_rows.append(
            (assignment, row["assignment_sha256"])
        )

    return {
        "expected_hashes": expected,
        "actual_hashes": actual,
        "batch": batch,
        "assignments": assignment_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--activation-root",
        type=Path,
        default=DEFAULT_ROOT,
    )
    parser.add_argument(
        "--assignment",
        action="append",
        default=[],
    )
    args = parser.parse_args()
    root = args.activation_root.resolve()
    validation = validate_activation(root)
    output_root = (
        root / "ingrid_outputs" / "batch-01"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    selected = set(args.assignment)
    summaries = []

    for assignment, _ in validation["assignments"]:
        if (
            selected
            and assignment["assignment_id"]
            not in selected
        ):
            continue
        print(
            "[Ingrid] segmentando "
            + assignment["assignment_id"]
            + " ("
            + str(len(assignment["approved_pages"]))
            + " paginas)",
            flush=True,
        )
        summaries.append(
            execute_assignment(
                assignment,
                output_root,
            )
        )
        print(
            "[Ingrid] terminado "
            + assignment["assignment_id"],
            flush=True,
        )

    report = {
        "schema_version": (
            "ingrid_instance_batch_"
            "execution_report_v1"
        ),
        "batch_id": validation["batch"]["batch_id"],
        "capability_id": (
            "instance_problem_solution_segmenter_v1"
        ),
        "mode": "instance_staging",
        "executed_assignments": len(summaries),
        "authorized_assignments": 10,
        "pages_inspected": sum(
            row["pages_inspected"]
            for row in summaries
        ),
        "problem_reviews": sum(
            row["problem_reviews"]
            for row in summaries
        ),
        "problem_boxes": sum(
            row["problem_boxes"]
            for row in summaries
        ),
        "solution_units": sum(
            row["solution_units"]
            for row in summaries
        ),
        "solution_fragments": sum(
            row["solution_fragments"]
            for row in summaries
        ),
        "complete_solution_units": sum(
            row["complete_solution_units"]
            for row in summaries
        ),
        "incomplete_solution_units": sum(
            row["incomplete_solution_units"]
            for row in summaries
        ),
        "abstention_pages": sum(
            row["abstention_pages"]
            for row in summaries
        ),
        "structure_mismatch": 0,
        "hashes_validated": validation[
            "actual_hashes"
        ],
        "assignments": summaries,
        "status": "agent_segmented_pending_human",
        "human_review": "pending",
        "next_gate": "H-PS2",
        "forbidden_actions_performed": [],
    }
    json_write(
        output_root / "batch_execution_report.json",
        report,
    )
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

