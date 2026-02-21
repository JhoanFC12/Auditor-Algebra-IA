from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class SegmentoProblemaV2:
    idx: int
    bbox: Tuple[int, int, int, int]
    image_path: Path
    source_path: Path


class SegmentadorProblemasV2:
    """
    Segmentacion por YOLO (prioridad absoluta).
    Si YOLO no detecta, devuelve sin segmentos para correccion manual.
    """

    def __init__(self, out_root: Path) -> None:
        self.out_root = Path(out_root)
        self.out_root.mkdir(parents=True, exist_ok=True)
        self._yolo_detector = None
        self._yolo_model_path = ""
        self.last_detector_source = "none"

    def _resolve_problem_model_path(self) -> str:
        candidates = (
            (os.getenv("YOLO_PROBLEM_MODEL", "") or "").strip(),
            (os.getenv("YOLO_SEGMENT_MODEL", "") or "").strip(),
            (os.getenv("YOLO_DETECT_MODEL", "") or "").strip(),
            (os.getenv("YOLO_FIGURE_MODEL", "") or "").strip(),
        )
        for raw in candidates:
            if not raw:
                continue
            try:
                path = Path(raw).expanduser().resolve()
            except Exception:
                continue
            if path.exists():
                return str(path)
        return ""

    def _resolve_problem_min_conf(self) -> float:
        raw = (os.getenv("YOLO_PROBLEM_MIN_CONF", "0.12") or "0.12").strip().replace(",", ".")
        try:
            value = float(raw)
        except Exception:
            value = 0.12
        return max(0.0, min(1.0, value))

    def _get_problem_yolo(self):
        model_path = self._resolve_problem_model_path()
        if not model_path:
            return None
        try:
            from ultralytics import YOLO  # type: ignore
        except Exception:
            return None
        try:
            if self._yolo_detector is None or self._yolo_model_path != model_path:
                self._yolo_detector = YOLO(model_path)
                self._yolo_model_path = model_path
            return self._yolo_detector
        except Exception:
            return None

    def _normalize_box(
        self,
        box: Tuple[int, int, int, int],
        *,
        width: int,
        height: int,
        min_w: int = 16,
        min_h: int = 16,
    ) -> Optional[Tuple[int, int, int, int]]:
        if width <= 1 or height <= 1:
            return None
        x1, y1, x2, y2 = [int(v) for v in box]
        left = max(0, min(width - 1, min(x1, x2)))
        top = max(0, min(height - 1, min(y1, y2)))
        right = max(left + 1, min(width, max(x1, x2)))
        bottom = max(top + 1, min(height, max(y1, y2)))
        if (right - left) < min_w or (bottom - top) < min_h:
            return None
        return (left, top, right, bottom)

    def _iou(self, a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1 = max(ax1, bx1)
        iy1 = max(ay1, by1)
        ix2 = min(ax2, bx2)
        iy2 = min(ay2, by2)
        if ix2 <= ix1 or iy2 <= iy1:
            return 0.0
        inter = float((ix2 - ix1) * (iy2 - iy1))
        area_a = float((ax2 - ax1) * (ay2 - ay1))
        area_b = float((bx2 - bx1) * (by2 - by1))
        union = max(1.0, area_a + area_b - inter)
        return inter / union

    def _dedupe_boxes(self, boxes: List[Tuple[int, int, int, int]]) -> List[Tuple[int, int, int, int]]:
        out: List[Tuple[int, int, int, int]] = []
        for box in sorted(boxes, key=lambda b: (b[1], b[0], b[2], b[3])):
            keep = True
            for existing in out:
                if self._iou(existing, box) >= 0.9:
                    keep = False
                    break
            if keep:
                out.append(box)
        return out

    def _segmentar_yolo_boxes(self, src: Path) -> List[Tuple[int, int, int, int]]:
        model = self._get_problem_yolo()
        if model is None:
            return []
        try:
            from PIL import Image  # type: ignore
        except Exception:
            return []
        try:
            with Image.open(src) as im:
                w, h = int(im.size[0]), int(im.size[1])
        except Exception:
            return []
        if w <= 1 or h <= 1:
            return []
        min_conf = self._resolve_problem_min_conf()
        min_w = max(18, int(round(w * 0.04)))
        min_h = max(18, int(round(h * 0.04)))

        try:
            results = model.predict(source=str(src), verbose=False, conf=min_conf)
        except Exception:
            return []
        if not results:
            return []

        boxes: List[Tuple[int, int, int, int]] = []
        for res in results:
            boxes_obj = getattr(res, "boxes", None)
            if boxes_obj is None:
                continue
            xyxy = getattr(boxes_obj, "xyxy", None)
            conf = getattr(boxes_obj, "conf", None)
            if xyxy is None or conf is None:
                continue
            try:
                rows = xyxy.cpu().tolist()
                conf_rows = conf.cpu().tolist()
            except Exception:
                continue
            for row, score_raw in zip(rows, conf_rows):
                if not isinstance(row, list) or len(row) < 4:
                    continue
                try:
                    score = float(score_raw or 0.0)
                except Exception:
                    score = 0.0
                if score < min_conf:
                    continue
                try:
                    x1, y1, x2, y2 = [int(round(float(v))) for v in row[:4]]
                except Exception:
                    continue
                normalized = self._normalize_box((x1, y1, x2, y2), width=w, height=h, min_w=min_w, min_h=min_h)
                if normalized is None:
                    continue
                boxes.append(normalized)
        return self._dedupe_boxes(boxes)

    def _segmentar_projection_boxes(self, src: Path) -> List[Tuple[int, int, int, int]]:
        try:
            from PIL import Image, ImageOps  # type: ignore
        except Exception:
            return []
        try:
            im = Image.open(src).convert("L")
            im = ImageOps.autocontrast(im)
        except Exception:
            return []

        w, h = im.size
        if w <= 1 or h <= 1:
            return []

        max_width = 1400
        scale = 1.0
        if w > max_width:
            scale = max_width / float(w)
            nw = max(1, int(round(w * scale)))
            nh = max(1, int(round(h * scale)))
            im = im.resize((nw, nh))
        ws, hs = im.size

        hist = im.histogram()
        total = float(sum(hist)) or 1.0
        mean = sum(i * c for i, c in enumerate(hist)) / total
        dark_thr = int(max(60, min(180, mean * 0.82)))
        min_dark_ratio = 0.012
        max_gap = max(8, hs // 160)
        min_height = max(70, hs // 20)
        pad_top = max(8, hs // 140)
        pad_bottom = max(12, hs // 120)

        text_rows: List[bool] = []
        pix = im.load()
        for y in range(hs):
            dark = 0
            for x in range(ws):
                if pix[x, y] <= dark_thr:
                    dark += 1
            ratio = dark / float(ws)
            text_rows.append(ratio >= min_dark_ratio)

        ranges: List[Tuple[int, int]] = []
        start = -1
        last_text = -1
        for y, is_text in enumerate(text_rows):
            if is_text:
                if start < 0:
                    start = y
                last_text = y
            else:
                if start >= 0 and (y - last_text) > max_gap:
                    ranges.append((start, last_text))
                    start = -1
                    last_text = -1
        if start >= 0:
            ranges.append((start, last_text if last_text >= 0 else hs - 1))

        compact: List[Tuple[int, int]] = []
        for y1, y2 in ranges:
            if y2 <= y1:
                continue
            if (y2 - y1 + 1) < min_height:
                continue
            if compact and (y1 - compact[-1][1]) <= max_gap:
                compact[-1] = (compact[-1][0], y2)
            else:
                compact.append((y1, y2))

        if not compact:
            return []

        inv = 1.0 / scale if scale > 0 else 1.0
        boxes: List[Tuple[int, int, int, int]] = []
        for (y1s, y2s) in compact:
            y1s = max(0, y1s - pad_top)
            y2s = min(hs - 1, y2s + pad_bottom)
            y1 = max(0, int(round(y1s * inv)))
            y2 = min(h, int(round((y2s + 1) * inv)))
            normalized = self._normalize_box((0, y1, w, y2), width=w, height=h, min_w=20, min_h=20)
            if normalized is not None:
                boxes.append(normalized)
        return boxes

    def _save_segments_from_boxes(self, src: Path, boxes: List[Tuple[int, int, int, int]]) -> List[SegmentoProblemaV2]:
        try:
            from PIL import Image  # type: ignore
        except Exception:
            return []
        if not boxes:
            return []
        out_dir = self.out_root / src.stem
        out_dir.mkdir(parents=True, exist_ok=True)
        result: List[SegmentoProblemaV2] = []
        try:
            base_img = Image.open(src)
        except Exception:
            return []

        try:
            for i, box in enumerate(boxes, start=1):
                try:
                    crop = base_img.crop(box)
                except Exception:
                    continue
                seg_path = out_dir / f"{src.stem}_seg_{i:02d}.png"
                try:
                    crop.save(seg_path, format="PNG")
                except Exception:
                    continue
                result.append(
                    SegmentoProblemaV2(
                        idx=i,
                        bbox=box,
                        image_path=seg_path,
                        source_path=src,
                    )
                )
        finally:
            try:
                base_img.close()
            except Exception:
                pass
        return result

    def segmentar(self, image_path: Path) -> List[SegmentoProblemaV2]:
        src = Path(image_path)
        if not src.exists():
            self.last_detector_source = "none"
            return []

        model_path = self._resolve_problem_model_path()
        if not model_path:
            self.last_detector_source = "yolo_no_model"
            return []
        try:
            from ultralytics import YOLO  # type: ignore  # noqa: F401
        except Exception:
            self.last_detector_source = "yolo_no_ultralytics"
            return []

        yolo_boxes = self._segmentar_yolo_boxes(src)
        if yolo_boxes:
            self.last_detector_source = "yolo"
            return self._save_segments_from_boxes(src, yolo_boxes)

        self.last_detector_source = "yolo_sin_detecciones"
        return []
