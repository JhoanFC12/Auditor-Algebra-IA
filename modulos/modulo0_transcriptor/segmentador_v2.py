from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import List, Optional, Tuple

TRAINED_YOLO_FIGURE_SEGMENT_MODEL_LOCAL = (
    Path(__file__).resolve().parents[2]
    / "models"
    / "problem_segmentation_yolov8n_golden_v1"
    / "weights"
    / "best.pt"
)
TRAINED_YOLO_FIGURE_SEGMENT_MODEL_REPO = "Jhoan12/problem-segmentation-yolov8n-golden-v1"
LEGACY_YOLO_FIGURE_SEGMENT_MODEL_LOCAL = (
    Path(__file__).resolve().parents[2]
    / "models"
    / "graph_detector_yolov8n_geom_positive_v2"
    / "weights"
    / "best.pt"
)
LEGACY_YOLO_FIGURE_SEGMENT_MODEL_REPO = "Jhoan12/graph-detector-yolov8n-geom-positive-v2"
DEFAULT_YOLO_FIGURE_SEGMENT_MODEL_LOCAL = TRAINED_YOLO_FIGURE_SEGMENT_MODEL_LOCAL
DEFAULT_YOLO_FIGURE_SEGMENT_MODEL_REPO = TRAINED_YOLO_FIGURE_SEGMENT_MODEL_REPO
DEFAULT_LIVE_GOLDEN_BASE_DIR = (
    Path(__file__).resolve().parents[2]
    / ".cache"
    / "transcriptor_runs"
    / "datasets"
    / "segment_training_live"
)


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

    def __init__(self, out_root: Path, *, model_path: str = "", force_model_default: bool = False) -> None:
        self.out_root = Path(out_root)
        self.out_root.mkdir(parents=True, exist_ok=True)
        self._yolo_detector = None
        self._yolo_model_path = ""
        self._configured_model_path = str(model_path or "").strip()
        self._force_model_default = bool(force_model_default)
        self._hf_model_cache_dir = Path(tempfile.gettempdir()) / "auditor_ia_hf_models"
        self._hf_model_cache_dir.mkdir(parents=True, exist_ok=True)
        self.last_detector_source = "none"
        self.last_detector_payload: dict = {}
        self.manifest_name = "segments_manifest.json"

    @staticmethod
    def _box_reading_key(box: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
        """Orden natural de lectura para segmentos: arriba-abajo, izquierda-derecha."""
        x1, y1, x2, y2 = [int(v) for v in box]
        return (y1, x1, y2, x2)

    def _sort_boxes_reading_order(
        self,
        boxes: List[Tuple[int, int, int, int]],
    ) -> List[Tuple[int, int, int, int]]:
        clean: List[Tuple[int, int, int, int]] = []
        for raw in list(boxes or []):
            try:
                x1, y1, x2, y2 = [int(v) for v in raw]
            except Exception:
                continue
            if x2 <= x1 or y2 <= y1:
                continue
            clean.append((x1, y1, x2, y2))
        return sorted(clean, key=self._box_reading_key)

    def _sort_detection_rows_reading_order(self, rows: List[dict]) -> List[dict]:
        def _key(row: dict) -> Tuple[int, int, int, int]:
            bbox = row.get("bbox_px") if isinstance(row, dict) else None
            if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
                return (10**12, 10**12, 10**12, 10**12)
            try:
                return self._box_reading_key(tuple(int(v) for v in bbox[:4]))
            except Exception:
                return (10**12, 10**12, 10**12, 10**12)

        return sorted([dict(row) for row in list(rows or []) if isinstance(row, dict)], key=_key)

    def _iter_external_python_commands(self) -> List[List[str]]:
        commands: List[List[str]] = []
        seen: set[tuple[str, ...]] = set()

        def _add(cmd: List[str]) -> None:
            key = tuple(str(part) for part in cmd if str(part).strip())
            if not key or key in seen:
                return
            seen.add(key)
            commands.append(list(key))

        custom = (os.getenv("YOLO_EXTERNAL_PYTHON", "") or "").strip()
        if custom:
            _add([custom])
        try:
            _add([sys.executable])
        except Exception:
            pass
        try:
            home_py = Path.home() / "AppData" / "Local" / "Programs" / "Python" / "Python311" / "python.exe"
            if home_py.exists():
                _add([str(home_py)])
        except Exception:
            pass
        _add(["py", "-3.11"])
        _add(["python"])
        return commands

    def _predict_yolo_detections_external(self, src: Path, model_path: str, conf: float) -> List[dict]:
        script = r"""
import json, sys
from ultralytics import YOLO

model_path = sys.argv[1]
image_path = sys.argv[2]
conf = float(sys.argv[3])

model = YOLO(model_path)
results = model.predict(source=image_path, verbose=False, conf=conf)
out = []
for res in results or []:
    boxes = getattr(res, "boxes", None)
    if boxes is None:
        continue
    xyxy = getattr(boxes, "xyxy", None)
    scores = getattr(boxes, "conf", None)
    if xyxy is None or scores is None:
        continue
    try:
        rows = xyxy.cpu().tolist()
        conf_rows = scores.cpu().tolist()
    except Exception:
        continue
    for row, score in zip(rows, conf_rows):
        if not isinstance(row, list) or len(row) < 4:
            continue
        try:
            x1, y1, x2, y2 = [int(round(float(v))) for v in row[:4]]
            score_val = float(score or 0.0)
        except Exception:
            continue
        out.append({"bbox_px": [x1, y1, x2, y2], "conf": score_val})
print(json.dumps(out, ensure_ascii=False))
"""
        for cmd in self._iter_external_python_commands():
            try:
                proc = subprocess.run(
                    cmd + ["-c", script, model_path, str(src), str(conf)],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
            except Exception:
                continue
            if proc.returncode != 0:
                continue
            raw = (proc.stdout or "").strip()
            if not raw:
                continue
            lines = [line.strip() for line in raw.splitlines() if line.strip()]
            for candidate in reversed(lines):
                try:
                    payload = json.loads(candidate)
                except Exception:
                    continue
                if isinstance(payload, list):
                    out: List[dict] = []
                    for entry in payload:
                        if not isinstance(entry, dict):
                            continue
                        bbox_raw = entry.get("bbox_px")
                        if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) < 4:
                            continue
                        try:
                            bbox = [int(v) for v in bbox_raw[:4]]
                            score = float(entry.get("conf", 0.0) or 0.0)
                        except Exception:
                            continue
                        out.append({"bbox_px": bbox, "conf": max(0.0, min(1.0, score))})
                    return out
        return []

    def _segment_manifest_path(self, out_dir: Path) -> Path:
        return Path(out_dir) / self.manifest_name

    def _normalize_detector_payload(self, raw: Optional[dict]) -> Optional[dict]:
        if not isinstance(raw, dict):
            return None
        detector_source = str(raw.get("detector_source", "none") or "none").strip().lower()
        if not detector_source:
            detector_source = "none"
        detector_model = str(raw.get("detector_model", "") or "").strip()
        review_status = str(raw.get("review_status", "predicted") or "predicted").strip().lower()
        if not review_status:
            review_status = "predicted"
        try:
            max_conf = float(raw.get("max_conf", 0.0) or 0.0)
        except Exception:
            max_conf = 0.0
        try:
            avg_conf = float(raw.get("avg_conf", 0.0) or 0.0)
        except Exception:
            avg_conf = 0.0

        def _norm_box_list(values) -> List[dict]:
            out: List[dict] = []
            if not isinstance(values, list):
                return out
            for entry in values:
                if not isinstance(entry, dict):
                    continue
                bbox_raw = entry.get("bbox_px")
                if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) < 4:
                    continue
                try:
                    x1, y1, x2, y2 = [int(v) for v in bbox_raw[:4]]
                except Exception:
                    continue
                if x2 <= x1 or y2 <= y1:
                    continue
                row = {"bbox_px": [x1, y1, x2, y2]}
                try:
                    row["conf"] = max(0.0, min(1.0, float(entry.get("conf", 0.0) or 0.0)))
                except Exception:
                    row["conf"] = 0.0
                out.append(row)
            return out

        predicted_boxes = self._sort_detection_rows_reading_order(_norm_box_list(raw.get("predicted_boxes", [])))
        final_boxes = self._sort_detection_rows_reading_order(_norm_box_list(raw.get("final_boxes", [])))
        diagram_presence_label = str(raw.get("diagram_presence_label", "") or "").strip().lower()
        if diagram_presence_label not in {"yes", "no", "pending"}:
            diagram_presence_label = "yes" if final_boxes else "no"
        diagram_presence_source = str(raw.get("diagram_presence_source", "") or "").strip().lower()
        if not diagram_presence_source:
            diagram_presence_source = "final_segments"
        updated_at = str(raw.get("updated_at", "") or "").strip()
        if not updated_at:
            updated_at = str(raw.get("predicted_at", "") or "").strip()
        if not updated_at:
            updated_at = str(raw.get("reviewed_at", "") or "").strip()
        if not updated_at:
            updated_at = ""
        return {
            "detector_source": detector_source,
            "detector_model": detector_model,
            "review_status": review_status,
            "max_conf": max(0.0, min(1.0, max_conf)),
            "avg_conf": max(0.0, min(1.0, avg_conf)),
            "predicted_boxes": predicted_boxes,
            "final_boxes": final_boxes,
            "diagram_presence_label": diagram_presence_label,
            "diagram_presence_source": diagram_presence_source,
            "predicted_at": str(raw.get("predicted_at", "") or "").strip(),
            "reviewed_at": str(raw.get("reviewed_at", "") or "").strip(),
            "updated_at": updated_at,
        }

    def _write_segment_manifest(
        self,
        *,
        src: Path,
        out_dir: Path,
        segments: List[SegmentoProblemaV2],
        detector_payload: Optional[dict] = None,
    ) -> None:
        payload = {
            "schema_version": 2,
            "source_path": str(src),
            "source_stem": str(src.stem),
            "segments": [
                {
                    "idx": int(seg.idx),
                    "file_name": str(seg.image_path.name),
                    "bbox_px": [int(v) for v in seg.bbox],
                }
                for seg in segments
            ],
        }
        normalized_detector = self._normalize_detector_payload(detector_payload)
        if normalized_detector:
            payload["detector_review"] = normalized_detector
        try:
            self._segment_manifest_path(out_dir).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            return

    def persist_segments_manifest(
        self,
        *,
        src: Path,
        segments: List[SegmentoProblemaV2],
        detector_payload: Optional[dict] = None,
    ) -> None:
        out_dir = self.out_root / Path(src).stem
        out_dir.mkdir(parents=True, exist_ok=True)
        self._write_segment_manifest(
            src=Path(src),
            out_dir=out_dir,
            segments=segments,
            detector_payload=detector_payload,
        )
        self._mirror_to_live_golden(src=Path(src), segments=segments, detector_payload=detector_payload)

    def _load_segment_manifest(self, out_dir: Path) -> dict:
        manifest_path = self._segment_manifest_path(out_dir)
        if not manifest_path.exists():
            return {}
        try:
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _live_golden_base_dir(self) -> Path:
        raw = (os.getenv("SEGMENT_LIVE_GOLDEN_BASE", "") or "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
        return DEFAULT_LIVE_GOLDEN_BASE_DIR

    def _live_record_id(self, src: Path) -> str:
        try:
            key = str(Path(src).expanduser().resolve()).lower()
        except Exception:
            key = str(src).strip().lower()
        return hashlib.sha1(key.encode("utf-8", errors="ignore")).hexdigest()[:16]

    def _copy_file_safe(self, src: Path, dst: Path) -> bool:
        try:
            if not src.exists() or not src.is_file():
                return False
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            return True
        except Exception:
            return False

    def _rewrite_live_golden_indexes(self, live_dir: Path) -> None:
        records_dir = live_dir / "records"
        rows: List[dict] = []
        if records_dir.exists():
            for path in sorted(records_dir.glob("*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)

        live_dir.mkdir(parents=True, exist_ok=True)
        (live_dir / "source_records_all.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""),
            encoding="utf-8",
        )
        positives = [row for row in rows if int(row.get("boxes_total", 0) or 0) > 0]
        (live_dir / "source_records_positive.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in positives) + ("\n" if positives else ""),
            encoding="utf-8",
        )
        segment_rows: List[dict] = []
        for row in rows:
            for segment in list(row.get("segments", []) or []):
                if not isinstance(segment, dict):
                    continue
                segment_rows.append(
                    {
                        "record_id": f"{row.get('record_id')}_{int(segment.get('idx', 0) or 0):02d}",
                        "split": "train",
                        "book_code": "",
                        "instance_type": "",
                        "session_json": "",
                        "session_images_json": "",
                        "source_path": row.get("source_path", ""),
                        "source_stem": row.get("source_stem", ""),
                        "segment_idx": segment.get("idx"),
                        "segment_bbox_px": segment.get("bbox_px", []),
                        "segment_image_path": segment.get("session_segment_path", ""),
                        "copied_image_rel": segment.get("segment_image_rel", ""),
                        "split_image_rel": segment.get("segment_image_rel", ""),
                        "item_num": None,
                        "slot": "",
                        "marker_name": "",
                        "binding_confirmed": False,
                        "binding_status": "live",
                        "curso": "",
                        "tema": "",
                        "tags": {},
                        "item_text": "",
                    }
                )
        (live_dir / "records_all.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in segment_rows) + ("\n" if segment_rows else ""),
            encoding="utf-8",
        )
        manifest = {
            "schema_version": "segment_training_live_index_v1",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "records_total": len(rows),
            "positive_images": len(positives),
            "negative_images": max(0, len(rows) - len(positives)),
            "boxes_total": sum(int(row.get("boxes_total", 0) or 0) for row in rows),
            "files": {
                "source_records_all": "source_records_all.jsonl",
                "source_records_positive": "source_records_positive.jsonl",
                "records_all_jsonl": "records_all.jsonl",
                "records_dir": "records",
                "source_images_dir": "source_images",
                "segments_dir": "segments",
            },
            "notes": [
                "Base incremental creada automaticamente por SegmentadorProblemasV2.",
                "Incluye imagenes fuente con boxes y tambien imagenes sin detecciones.",
            ],
        }
        (live_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def _mirror_to_live_golden(
        self,
        *,
        src: Path,
        segments: List[SegmentoProblemaV2],
        detector_payload: Optional[dict] = None,
    ) -> None:
        if (os.getenv("SEGMENT_LIVE_GOLDEN_DISABLE", "") or "").strip().lower() in {"1", "true", "yes", "si"}:
            return
        source = Path(src)
        if not source.exists() or not source.is_file():
            return

        live_dir = self._live_golden_base_dir()
        record_id = self._live_record_id(source)
        source_ext = source.suffix.lower() or ".png"
        source_name = f"{record_id}_{source.stem}{source_ext}"
        copied_source = live_dir / "source_images" / source_name
        self._copy_file_safe(source, copied_source)

        live_segments_dir = live_dir / "segments" / record_id
        if live_segments_dir.exists():
            try:
                shutil.rmtree(live_segments_dir)
            except Exception:
                pass

        segment_rows: List[dict] = []
        for seg in self._sort_segments_for_live(segments):
            seg_src = Path(seg.image_path)
            seg_ext = seg_src.suffix.lower() or ".png"
            seg_name = f"{record_id}_seg_{int(seg.idx):02d}{seg_ext}"
            seg_dst = live_segments_dir / seg_name
            copied = self._copy_file_safe(seg_src, seg_dst)
            segment_rows.append(
                {
                    "idx": int(seg.idx),
                    "bbox_px": [int(v) for v in seg.bbox],
                    "segment_image_rel": str(seg_dst.relative_to(live_dir)).replace("\\", "/") if copied else "",
                    "session_segment_path": str(seg_src),
                }
            )

        normalized_detector = self._normalize_detector_payload(detector_payload) or {}
        record = {
            "schema_version": "segment_training_live_source_v1",
            "record_id": record_id,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "source_path": str(source),
            "source_stem": source.stem,
            "source_image_rel": str(copied_source.relative_to(live_dir)).replace("\\", "/") if copied_source.exists() else "",
            "session_segments_root": str(self.out_root),
            "boxes_total": len(segment_rows),
            "boxes_px": [list(row["bbox_px"]) for row in segment_rows],
            "segments": segment_rows,
            "detector_review": normalized_detector,
        }
        records_dir = live_dir / "records"
        records_dir.mkdir(parents=True, exist_ok=True)
        (records_dir / f"{record_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        if (os.getenv("SEGMENT_LIVE_GOLDEN_DEFER_INDEX", "") or "").strip().lower() not in {"1", "true", "yes", "si"}:
            self._rewrite_live_golden_indexes(live_dir)

    def rebuild_live_golden_indexes(self) -> Path:
        live_dir = self._live_golden_base_dir()
        self._rewrite_live_golden_indexes(live_dir)
        return live_dir

    def register_source_pending(self, src: Path) -> None:
        self._mirror_to_live_golden(
            src=Path(src),
            segments=[],
            detector_payload={
                "detector_source": "pdf_problem_crop_pending_review",
                "review_status": "pending",
                "diagram_presence_label": "pending",
                "diagram_presence_source": "pending_review",
                "predicted_boxes": [],
                "final_boxes": [],
            },
        )

    def _sort_segments_for_live(self, segments: List[SegmentoProblemaV2]) -> List[SegmentoProblemaV2]:
        clean = [seg for seg in list(segments or []) if isinstance(seg, SegmentoProblemaV2)]
        return sorted(clean, key=lambda seg: self._box_reading_key(seg.bbox))

    def _resolve_problem_model_path(self) -> str:
        candidates = (
            self._configured_model_path,
            (os.getenv("YOLO_FIGURE_SEGMENT_MODEL", "") or "").strip(),
            (os.getenv("YOLO_FIGURE_MODEL", "") or "").strip(),
            (os.getenv("FIGURE_DETECTOR_MODEL", "") or "").strip(),
            (os.getenv("YOLO_SEGMENT_MODEL", "") or "").strip(),
            (os.getenv("YOLO_DETECT_MODEL", "") or "").strip(),
            str(DEFAULT_YOLO_FIGURE_SEGMENT_MODEL_LOCAL),
            str(LEGACY_YOLO_FIGURE_SEGMENT_MODEL_LOCAL),
            DEFAULT_YOLO_FIGURE_SEGMENT_MODEL_REPO,
            LEGACY_YOLO_FIGURE_SEGMENT_MODEL_REPO,
        )
        for raw in candidates:
            if not raw:
                continue
            try:
                path = Path(raw).expanduser().resolve()
                if path.exists():
                    return str(path)
            except Exception:
                pass
            if "/" in raw and "\\" not in raw and raw.count("/") == 1:
                resolved = self._download_hf_problem_model(raw)
                if resolved:
                    return resolved
        return ""

    def _download_hf_problem_model(self, repo_id: str) -> str:
        try:
            from huggingface_hub import hf_hub_download  # type: ignore
        except Exception:
            return ""
        token = (os.getenv("HF_TOKEN", "") or "").strip() or None
        preferred = (os.getenv("YOLO_PROBLEM_MODEL_FILE", "") or "").strip()
        candidates = []
        if preferred:
            candidates.append(preferred)
        candidates.extend(["weights/best.pt", "best.pt", "weights/last.pt", "last.pt"])
        cache_root = self._hf_model_cache_dir / repo_id.replace("/", "__")
        for filename in candidates:
            cached_path = cache_root / Path(filename)
            try:
                if cached_path.exists():
                    return str(cached_path.resolve())
            except Exception:
                pass
            try:
                local_path = hf_hub_download(
                    repo_id=repo_id,
                    filename=filename,
                    repo_type="model",
                    token=token,
                    local_dir=str(cache_root),
                    local_dir_use_symlinks=False,
                )
            except Exception:
                continue
            if local_path:
                try:
                    resolved = Path(local_path).expanduser().resolve()
                except Exception:
                    resolved = Path(local_path)
                if resolved.exists():
                    return str(resolved)
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

    def _dedupe_detections(self, detections: List[dict]) -> List[dict]:
        out: List[dict] = []
        for det in sorted(
            detections,
            key=lambda row: (
                -float(row.get("conf", 0.0) or 0.0),
                int((row.get("bbox_px") or [0, 0, 0, 0])[1]),
                int((row.get("bbox_px") or [0, 0, 0, 0])[0]),
            ),
        ):
            bbox_raw = det.get("bbox_px")
            if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) < 4:
                continue
            try:
                box = tuple(int(v) for v in bbox_raw[:4])
            except Exception:
                continue
            keep = True
            for existing in out:
                bbox_existing = existing.get("bbox_px")
                if not isinstance(bbox_existing, (list, tuple)) or len(bbox_existing) < 4:
                    continue
                try:
                    existing_box = tuple(int(v) for v in bbox_existing[:4])
                except Exception:
                    continue
                if self._iou(existing_box, box) >= 0.9:
                    keep = False
                    break
            if keep:
                out.append(
                    {
                        "bbox_px": [int(v) for v in box],
                        "conf": max(0.0, min(1.0, float(det.get("conf", 0.0) or 0.0))),
                    }
                )
        return self._sort_detection_rows_reading_order(out)

    def _segmentar_yolo_detections(self, src: Path) -> List[dict]:
        model_path = self._resolve_problem_model_path()
        if not model_path:
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

        detections: List[dict] = []
        model = self._get_problem_yolo()
        if model is not None:
            try:
                results = model.predict(source=str(src), verbose=False, conf=min_conf)
            except Exception:
                results = []
            for res in results or []:
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
                    detections.append(
                        {
                            "bbox_px": [int(v) for v in normalized],
                            "conf": max(0.0, min(1.0, score)),
                        }
                    )
        if not detections:
            external = self._predict_yolo_detections_external(src=src, model_path=model_path, conf=min_conf)
            for entry in external:
                bbox_raw = entry.get("bbox_px")
                if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) < 4:
                    continue
                try:
                    x1, y1, x2, y2 = [int(v) for v in bbox_raw[:4]]
                    score = float(entry.get("conf", 0.0) or 0.0)
                except Exception:
                    continue
                normalized = self._normalize_box((x1, y1, x2, y2), width=w, height=h, min_w=min_w, min_h=min_h)
                if normalized is None:
                    continue
                detections.append(
                    {
                        "bbox_px": [int(v) for v in normalized],
                        "conf": max(0.0, min(1.0, score)),
                    }
                )
        return self._dedupe_detections(detections)

    def _segmentar_yolo_boxes(self, src: Path) -> List[Tuple[int, int, int, int]]:
        detections = self._segmentar_yolo_detections(src)
        out: List[Tuple[int, int, int, int]] = []
        for entry in detections:
            bbox_raw = entry.get("bbox_px")
            if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) < 4:
                continue
            try:
                out.append(tuple(int(v) for v in bbox_raw[:4]))
            except Exception:
                continue
        return self._sort_boxes_reading_order(out)

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

    def _save_segments_from_boxes(
        self,
        src: Path,
        boxes: List[Tuple[int, int, int, int]],
        *,
        detector_payload: Optional[dict] = None,
    ) -> List[SegmentoProblemaV2]:
        out_dir = self.out_root / src.stem
        out_dir.mkdir(parents=True, exist_ok=True)
        for old_path in out_dir.iterdir():
            try:
                if old_path.is_file() and old_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                    old_path.unlink()
            except Exception:
                pass
        ordered_boxes = self._sort_boxes_reading_order(list(boxes or []))
        if not ordered_boxes:
            self._write_segment_manifest(
                src=src,
                out_dir=out_dir,
                segments=[],
                detector_payload=detector_payload,
            )
            self._mirror_to_live_golden(src=src, segments=[], detector_payload=detector_payload)
            return []
        try:
            from PIL import Image  # type: ignore
        except Exception:
            return []
        result: List[SegmentoProblemaV2] = []
        try:
            base_img = Image.open(src)
        except Exception:
            return []

        try:
            for i, box in enumerate(ordered_boxes, start=1):
                try:
                    crop = base_img.crop(box)
                except Exception:
                    continue
                # Keep crop filenames short: Windows paths can exceed MAX_PATH when
                # source stems come from long book/session names.
                seg_path = out_dir / f"seg_{i:02d}.png"
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
        self._write_segment_manifest(
            src=src,
            out_dir=out_dir,
            segments=result,
            detector_payload=detector_payload,
        )
        self._mirror_to_live_golden(src=src, segments=result, detector_payload=detector_payload)
        return result

    def save_reviewed_segments(
        self,
        image_path: Path,
        boxes: List[Tuple[int, int, int, int]],
        *,
        detector_payload: Optional[dict] = None,
    ) -> List[SegmentoProblemaV2]:
        """Persist human-reviewed figure boxes and regenerate segment crops."""
        src = Path(image_path)
        payload = dict(detector_payload or {})
        payload.update(
            {
                "detector_source": "human_reviewed_segments",
                "detector_model": self._yolo_model_path or self._resolve_problem_model_path(),
                "review_status": "reviewed",
                "max_conf": 1.0 if boxes else 0.0,
                "avg_conf": 1.0 if boxes else 0.0,
                "diagram_presence_source": "human_review",
                "diagram_presence_label": "yes" if boxes else "no",
                "final_boxes": [
                    {"bbox_px": [int(v) for v in box[:4]], "conf": 1.0, "source": "human_review"}
                    for box in list(boxes or [])
                ],
            }
        )
        self.last_detector_source = "human_reviewed_segments"
        self.last_detector_payload = self._normalize_detector_payload(payload) or payload
        return self._save_segments_from_boxes(src, list(boxes or []), detector_payload=self.last_detector_payload)

    def _load_existing_segments(self, src: Path) -> List[SegmentoProblemaV2]:
        out_dir = self.out_root / src.stem
        if not out_dir.exists() or (not out_dir.is_dir()):
            return []
        manifest = self._load_segment_manifest(out_dir)

        def _repair_from_detector_final_boxes() -> List[SegmentoProblemaV2]:
            detector_review = manifest.get("detector_review") if isinstance(manifest, dict) else {}
            normalized = self._normalize_detector_payload(detector_review)
            final_boxes = normalized.get("final_boxes", []) if isinstance(normalized, dict) else []
            boxes: List[Tuple[int, int, int, int]] = []
            for raw in final_boxes if isinstance(final_boxes, list) else []:
                if not isinstance(raw, dict):
                    continue
                bbox_raw = raw.get("bbox_px")
                if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) < 4:
                    continue
                try:
                    box = tuple(int(v) for v in bbox_raw[:4])
                except Exception:
                    continue
                if box[2] <= box[0] or box[3] <= box[1]:
                    continue
                boxes.append(box)
            if not boxes:
                return []
            return self._save_segments_from_boxes(src, boxes, detector_payload=normalized or detector_review)

        image_files = sorted(
            [p for p in out_dir.iterdir() if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}],
            key=lambda p: p.name.lower(),
        )
        if not image_files:
            return _repair_from_detector_final_boxes()
        manifest_segments = manifest.get("segments", []) if isinstance(manifest, dict) else []
        if not isinstance(manifest_segments, list) or not manifest_segments:
            repaired = _repair_from_detector_final_boxes()
            return repaired if repaired else []
        meta_by_name = {}
        meta_by_idx = {}
        for raw in manifest_segments:
            if not isinstance(raw, dict):
                continue
            try:
                idx = int(raw.get("idx", 0) or 0)
            except Exception:
                idx = 0
            file_name = str(raw.get("file_name", "") or "").strip().lower()
            bbox_raw = raw.get("bbox_px", [])
            if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) != 4:
                continue
            try:
                bbox = tuple(int(v) for v in bbox_raw)
            except Exception:
                continue
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                continue
            if file_name:
                meta_by_name[file_name] = (idx, bbox)
            if idx > 0:
                meta_by_idx[idx] = bbox

        result: List[SegmentoProblemaV2] = []
        for pos, seg_path in enumerate(image_files, start=1):
            idx = pos
            try:
                match = re.search(r"(?:^|_)seg_(\d+)$", str(seg_path.stem or ""), flags=re.IGNORECASE)
                if match:
                    idx = max(1, int(match.group(1)))
            except Exception:
                idx = pos
            bbox = None
            meta = meta_by_name.get(seg_path.name.lower())
            if meta is not None:
                idx = max(1, int(meta[0] or idx))
                bbox = meta[1]
            elif idx in meta_by_idx:
                bbox = meta_by_idx[idx]
            if bbox is None:
                continue
            result.append(
                SegmentoProblemaV2(
                    idx=idx,
                    bbox=bbox,
                    image_path=seg_path,
                    source_path=src,
                )
            )
        result.sort(key=lambda seg: self._box_reading_key(seg.bbox))
        result = [
            SegmentoProblemaV2(
                idx=i,
                bbox=seg.bbox,
                image_path=seg.image_path,
                source_path=seg.source_path,
            )
            for i, seg in enumerate(result, start=1)
        ]
        return result

    def _has_cached_segment_decision(self, src: Path) -> bool:
        out_dir = self.out_root / src.stem
        manifest = self._load_segment_manifest(out_dir)
        if not isinstance(manifest, dict) or not manifest:
            return False
        if "segments" not in manifest:
            return False
        if not isinstance(manifest.get("segments"), list):
            return False
        detector_review = manifest.get("detector_review")
        if isinstance(detector_review, dict):
            return True
        return True

    def segmentar(self, image_path: Path, *, force_model: bool = False) -> List[SegmentoProblemaV2]:
        src = Path(image_path)
        if not src.exists():
            self.last_detector_source = "none"
            return []

        manifest = self._load_segment_manifest(self.out_root / src.stem)
        force_model = bool(force_model or self._force_model_default)
        if not force_model:
            cached = self._load_existing_segments(src)
            if cached:
                self.last_detector_source = "cache"
                self.last_detector_payload = self._normalize_detector_payload(manifest.get("detector_review")) or {}
                self._mirror_to_live_golden(src=src, segments=cached, detector_payload=self.last_detector_payload)
                return cached
            if self._has_cached_segment_decision(src):
                self.last_detector_source = "cache_empty"
                self.last_detector_payload = self._normalize_detector_payload(manifest.get("detector_review")) or {}
                self._mirror_to_live_golden(src=src, segments=[], detector_payload=self.last_detector_payload)
                return []

        model_path = self._resolve_problem_model_path()
        if not model_path:
            self.last_detector_source = "yolo_no_model"
            self.last_detector_payload = self._normalize_detector_payload(
                {
                    "detector_source": "yolo_no_model",
                    "detector_model": "",
                    "review_status": "predicted",
                    "max_conf": 0.0,
                    "avg_conf": 0.0,
                    "predicted_boxes": [],
                    "final_boxes": [],
                    "diagram_presence_label": "pending",
                    "diagram_presence_source": "yolo_no_model",
                }
            ) or {}
            try:
                self.persist_segments_manifest(src=src, segments=[], detector_payload=self.last_detector_payload)
            except Exception:
                self._mirror_to_live_golden(src=src, segments=[], detector_payload=self.last_detector_payload)
            return []

        detections = self._segmentar_yolo_detections(src)
        yolo_boxes: List[Tuple[int, int, int, int]] = []
        for entry in detections:
            bbox_raw = entry.get("bbox_px")
            if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) < 4:
                continue
            try:
                yolo_boxes.append(tuple(int(v) for v in bbox_raw[:4]))
            except Exception:
                continue
        yolo_boxes = self._sort_boxes_reading_order(yolo_boxes)
        if yolo_boxes:
            self.last_detector_source = "yolo"
            max_conf = max((float(row.get("conf", 0.0) or 0.0) for row in detections), default=0.0)
            avg_conf = (
                sum(float(row.get("conf", 0.0) or 0.0) for row in detections) / float(len(detections))
                if detections
                else 0.0
            )
            detector_payload = {
                "detector_source": "yolo",
                "detector_model": self._yolo_model_path or model_path,
                "review_status": "predicted",
                "max_conf": max_conf,
                "avg_conf": avg_conf,
                "predicted_boxes": detections,
                "final_boxes": [{"bbox_px": [int(v) for v in box], "conf": 1.0} for box in yolo_boxes],
                "diagram_presence_label": "yes",
                "diagram_presence_source": "final_segments",
                "predicted_at": "",
                "reviewed_at": "",
                "updated_at": "",
            }
            self.last_detector_payload = self._normalize_detector_payload(detector_payload) or {}
            return self._save_segments_from_boxes(src, yolo_boxes, detector_payload=self.last_detector_payload)

        self.last_detector_source = "yolo_sin_detecciones"
        self.last_detector_payload = self._normalize_detector_payload(
            {
                "detector_source": "yolo_sin_detecciones",
                "detector_model": self._yolo_model_path or model_path,
                "review_status": "predicted",
                "max_conf": 0.0,
                "avg_conf": 0.0,
                "predicted_boxes": [],
                "final_boxes": [],
                "diagram_presence_label": "no",
                "diagram_presence_source": "final_segments",
                "predicted_at": "",
                "reviewed_at": "",
                "updated_at": "",
            }
        ) or {}
        try:
            self.persist_segments_manifest(src=src, segments=[], detector_payload=self.last_detector_payload)
        except Exception:
            pass
        return []

    def segmentar_con_modelo(self, image_path: Path) -> List[SegmentoProblemaV2]:
        """Ejecuta el modelo ignorando cache/decisiones manuales previas."""
        return self.segmentar(image_path, force_model=True)
