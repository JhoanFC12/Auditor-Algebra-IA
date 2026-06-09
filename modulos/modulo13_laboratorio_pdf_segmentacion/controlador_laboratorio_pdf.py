from __future__ import annotations

import json
import os
import re
import shutil
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PIL import Image
from huggingface_hub import hf_hub_download
from ultralytics import YOLO

from utils.project_layout import infer_workspace_from_session_path, normalize_instance_name, project_dirs


DEFAULT_GOLDEN_ROOT = Path(".cache/transcriptor_runs/datasets/pdf_problem_boxes_live").resolve()
DEFAULT_MODEL_REPO_ID = "Jhoan12/pdf-problem-detector-yolov8n-v4"
DEFAULT_PREDICT_ROOT = Path(".cache/transcriptor_runs/pdf_problem_detector_runtime").resolve()
DEFAULT_PROBLEM_CROPS_LIVE_ROOT = Path(".cache/transcriptor_runs/datasets/problem_crops_live").resolve()
DEFAULT_LOCAL_MODEL_PATH = Path("models/pdf_problem_detector_yolov8n_v4/weights/best.pt").resolve()


def safe_name(value: str, fallback: str = "instancia", max_len: int = 72) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._")
    cleaned = cleaned or fallback
    if len(cleaned) <= max_len:
        return cleaned
    digest = hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:10]
    return f"{cleaned[: max(8, max_len - 11)].rstrip('._-')}_{digest}"


def compact_id(*parts: object, prefix: str = "id", max_len: int = 72) -> str:
    raw = "__".join(str(part or "").strip() for part in parts if str(part or "").strip())
    base = safe_name(raw or prefix, prefix, max_len=max_len)
    digest = hashlib.sha1((raw or base).encode("utf-8")).hexdigest()[:10]
    clean_prefix = safe_name(prefix, "id", max_len=18)
    if len(base) <= max_len:
        return base
    return f"{clean_prefix}_{digest}"


def detect_layout_mode(boxes: list[tuple[int, int, int, int]]) -> str:
    if len(boxes) < 4:
        return "una_columna"
    centers = sorted((box[0] + box[2]) / 2.0 for box in boxes)
    largest_gap = max((centers[index + 1] - centers[index] for index in range(len(centers) - 1)), default=0.0)
    page_span = max(box[2] for box in boxes) - min(box[0] for box in boxes)
    return "dos_columnas" if largest_gap >= max(80.0, page_span * 0.12) else "una_columna"


def sort_boxes_reading_order(boxes: list[tuple[int, int, int, int]], layout_mode: str = "auto") -> list[tuple[int, int, int, int]]:
    effective_mode = detect_layout_mode(boxes) if layout_mode == "auto" else layout_mode
    if effective_mode != "dos_columnas" or len(boxes) < 4:
        return sorted(boxes, key=lambda box: (box[1], box[0]))
    centers = sorted(((box[0] + box[2]) / 2.0, box) for box in boxes)
    gaps = [(centers[index + 1][0] - centers[index][0], index) for index in range(len(centers) - 1)]
    largest_gap, split_index = max(gaps, default=(0.0, 0))
    page_span = max(box[2] for box in boxes) - min(box[0] for box in boxes)
    left = [box for _, box in centers[: split_index + 1]]
    right = [box for _, box in centers[split_index + 1 :]]
    if len(left) >= 2 and len(right) >= 2:
        return sorted(left, key=lambda box: (box[1], box[0])) + sorted(right, key=lambda box: (box[1], box[0]))
    return sorted(boxes, key=lambda box: (box[1], box[0]))


@dataclass
class ProblemPageRecord:
    record_id: str
    pdf_path: str
    page_number: int
    image_path: Path
    boxes: list[tuple[int, int, int, int]] = field(default_factory=list)
    detector_source: str = "manual"
    reviewed: bool = False
    layout_mode: str = "auto"


class PdfProblemGoldenController:
    def __init__(self, golden_root: Path = DEFAULT_GOLDEN_ROOT) -> None:
        self.golden_root = Path(golden_root).resolve()
        self.golden_root.mkdir(parents=True, exist_ok=True)
        self.predict_root = DEFAULT_PREDICT_ROOT
        self.predict_root.mkdir(parents=True, exist_ok=True)
        self._model = None
        self._model_path = ""

    def instance_dir(self, name: str) -> Path:
        return self.golden_root / safe_name(name)

    def load_instance(self, name: str) -> list[ProblemPageRecord]:
        records_dir = self.instance_dir(name) / "records"
        rows: list[ProblemPageRecord] = []
        for path in sorted(records_dir.glob("*.json")) if records_dir.exists() else []:
            payload = json.loads(path.read_text(encoding="utf-8"))
            image_path = self.instance_dir(name) / str(payload["image_rel"])
            rows.append(
                ProblemPageRecord(
                    record_id=str(payload["record_id"]),
                    pdf_path=str(payload["pdf_path"]),
                    page_number=int(payload["page_number"]),
                    image_path=image_path,
                    boxes=[tuple(int(value) for value in box[:4]) for box in payload.get("boxes_px", [])],
                    detector_source=str(payload.get("detector_source", "manual")),
                    reviewed=bool(payload.get("reviewed", False)),
                    layout_mode=str(payload.get("layout_mode", "auto")),
                )
            )
        return rows

    def add_rendered_page(self, name: str, *, pdf_path: Path, page_number: int, rendered_path: Path) -> ProblemPageRecord:
        instance = self.instance_dir(name)
        pages_dir = instance / "pages_png"
        pages_dir.mkdir(parents=True, exist_ok=True)
        pdf_key = safe_name(pdf_path.stem, "pdf", max_len=42)
        record_id = compact_id(pdf_key, f"p{int(page_number):04d}", prefix="p", max_len=58)
        destination = pages_dir / f"{record_id}.png"
        suffix = 2
        while destination.exists():
            existing = self._read_record(instance, record_id)
            if existing and Path(str(existing.get("pdf_path", ""))).resolve() == pdf_path.resolve():
                break
            record_id = f"{pdf_key}_p{int(page_number):04d}_{suffix}"
            destination = pages_dir / f"{record_id}.png"
            suffix += 1
        shutil.copy2(rendered_path, destination)
        return ProblemPageRecord(record_id, str(pdf_path), int(page_number), destination, [])

    def add_image(self, name: str, *, image_path: Path) -> ProblemPageRecord:
        image_path = Path(image_path).expanduser().resolve()
        return self.add_rendered_page(name, pdf_path=image_path, page_number=1, rendered_path=image_path)

    def _resolve_detector_weights(self, model: str = "") -> str:
        candidates = [
            str(model or "").strip(),
            os.getenv("PDF_PROBLEM_MODEL", "").strip(),
            os.getenv("PDF_PROBLEM_MODEL_REPO", "").strip(),
            str(DEFAULT_LOCAL_MODEL_PATH),
            DEFAULT_MODEL_REPO_ID,
        ]
        token = (os.getenv("HF_TOKEN", "") or "").strip() or None
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
                try:
                    return hf_hub_download(raw, "weights/best.pt", repo_type="model", token=token)
                except Exception:
                    try:
                        return hf_hub_download(raw, "best.pt", repo_type="model", token=token)
                    except Exception:
                        continue
        raise FileNotFoundError("No se encontro modelo entrenado para detector de problemas PDF.")

    def predict_boxes(
        self,
        image_path: Path,
        *,
        confidence: float = 0.25,
        imgsz: int = 1280,
        layout_mode: str = "auto",
        model: str = "",
    ) -> list[tuple[int, int, int, int]]:
        weights = self._resolve_detector_weights(model)
        if self._model is None or self._model_path != weights:
            self._model = YOLO(weights)
            self._model_path = weights
        result = self._model.predict(
            source=str(image_path),
            conf=float(confidence),
            imgsz=int(imgsz),
            verbose=False,
            save=False,
            project=str(self.predict_root),
            name="predict",
            exist_ok=True,
        )[0]
        boxes = [
            tuple(int(round(value)) for value in xyxy)
            for xyxy in result.boxes.xyxy.tolist()
        ]
        return sort_boxes_reading_order(boxes, layout_mode)

    @staticmethod
    def reorder_boxes(row: ProblemPageRecord) -> None:
        row.boxes = sort_boxes_reading_order(row.boxes, row.layout_mode)

    def save_instance(self, name: str, rows: list[ProblemPageRecord]) -> Path:
        instance = self.instance_dir(name)
        records_dir = instance / "records"
        crops_dir = instance / "problem_crops"
        records_dir.mkdir(parents=True, exist_ok=True)
        crops_dir.mkdir(parents=True, exist_ok=True)
        active_ids = {row.record_id for row in rows}
        active_crop_dirs = {compact_id(row.record_id, prefix="p", max_len=24) for row in rows}
        for stale_record in records_dir.glob("*.json"):
            if stale_record.stem not in active_ids:
                stale_record.unlink()
        for stale_crops in crops_dir.iterdir():
            if stale_crops.is_dir() and stale_crops.name not in active_crop_dirs:
                shutil.rmtree(stale_crops)
        pages_dir = instance / "pages_png"
        if pages_dir.exists():
            for stale_page in pages_dir.glob("*.png"):
                if stale_page.stem not in active_ids:
                    stale_page.unlink()
        for row in rows:
            row.reviewed = True
            try:
                row.image_path.relative_to(instance)
            except ValueError:
                pages_dir = instance / "pages_png"
                pages_dir.mkdir(parents=True, exist_ok=True)
                copied_page = pages_dir / f"{row.record_id}.png"
                shutil.copy2(row.image_path, copied_page)
                row.image_path = copied_page
            # Keep nested crop paths short enough for Windows path limits.
            row_crop_dir = compact_id(row.record_id, prefix="p", max_len=24)
            row_crops = crops_dir / row_crop_dir
            row_crops.mkdir(parents=True, exist_ok=True)
            for old in row_crops.glob("*.png"):
                old.unlink()
            crop_rows = []
            with Image.open(row.image_path) as image:
                for index, box in enumerate(row.boxes, start=1):
                    crop_file = compact_id(row.record_id, f"problem_{index:02d}", prefix="pr", max_len=32)
                    crop_path = row_crops / f"{crop_file}.png"
                    crop_path.parent.mkdir(parents=True, exist_ok=True)
                    image.crop(box).save(crop_path, format="PNG")
                    crop_rows.append({"idx": index, "bbox_px": list(box), "crop_rel": str(crop_path.relative_to(instance)).replace("\\", "/")})
            payload = {
                "schema_version": "pdf_problem_boxes_source_v1",
                "record_id": row.record_id,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "pdf_path": row.pdf_path,
                "page_number": row.page_number,
                "image_rel": str(row.image_path.relative_to(instance)).replace("\\", "/"),
                "boxes_px": [list(box) for box in row.boxes],
                "detector_source": row.detector_source,
                "reviewed": row.reviewed,
                "layout_mode": row.layout_mode,
                "problems": crop_rows,
            }
            (records_dir / f"{row.record_id}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._rewrite_manifest(name, rows)
        return instance

    def upsert_instance_rows(self, name: str, rows: list[ProblemPageRecord]) -> Path:
        """Agrega o reemplaza paginas puntuales sin regenerar toda la instancia."""
        instance = self.instance_dir(name)
        records_dir = instance / "records"
        crops_dir = instance / "problem_crops"
        pages_dir = instance / "pages_png"
        records_dir.mkdir(parents=True, exist_ok=True)
        crops_dir.mkdir(parents=True, exist_ok=True)
        pages_dir.mkdir(parents=True, exist_ok=True)
        active_ids = {str(row.record_id) for row in rows}
        incoming_pages = {int(row.page_number) for row in rows}
        for stale_record in records_dir.glob("*.json"):
            if stale_record.stem in active_ids:
                continue
            try:
                payload = json.loads(stale_record.read_text(encoding="utf-8"))
                page_number = int(payload.get("page_number") or 0)
            except Exception:
                continue
            if page_number not in incoming_pages:
                continue
            stale_record.unlink()
            stale_page = pages_dir / f"{stale_record.stem}.png"
            if stale_page.exists():
                stale_page.unlink()
            stale_crops = crops_dir / compact_id(stale_record.stem, prefix="p", max_len=24)
            if stale_crops.exists() and stale_crops.is_dir():
                shutil.rmtree(stale_crops)
        for row in rows:
            try:
                row.image_path.relative_to(instance)
            except ValueError:
                copied_page = pages_dir / f"{row.record_id}.png"
                shutil.copy2(row.image_path, copied_page)
                row.image_path = copied_page
            row_crop_dir = compact_id(row.record_id, prefix="p", max_len=24)
            row_crops = crops_dir / row_crop_dir
            row_crops.mkdir(parents=True, exist_ok=True)
            for old in row_crops.glob("*.png"):
                old.unlink()
            crop_rows = []
            with Image.open(row.image_path) as image:
                for index, box in enumerate(row.boxes, start=1):
                    crop_file = compact_id(row.record_id, f"problem_{index:02d}", prefix="pr", max_len=32)
                    crop_path = row_crops / f"{crop_file}.png"
                    crop_path.parent.mkdir(parents=True, exist_ok=True)
                    image.crop(box).save(crop_path, format="PNG")
                    crop_rows.append({"idx": index, "bbox_px": list(box), "crop_rel": str(crop_path.relative_to(instance)).replace("\\", "/")})
            payload = {
                "schema_version": "pdf_problem_boxes_source_v1",
                "record_id": row.record_id,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "pdf_path": row.pdf_path,
                "page_number": row.page_number,
                "image_rel": str(row.image_path.relative_to(instance)).replace("\\", "/"),
                "boxes_px": [list(box) for box in row.boxes],
                "detector_source": row.detector_source,
                "reviewed": row.reviewed,
                "layout_mode": row.layout_mode,
                "problems": crop_rows,
            }
            (records_dir / f"{row.record_id}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._rewrite_manifest(name, self.load_instance(name))
        return instance

    def materialize_problem_crops_for_downstream(
        self,
        name: str,
        rows: list[ProblemPageRecord],
        *,
        return_crop_ids: bool = False,
        session_path: Path | None = None,
        book_code: str = "",
        instance_type: str = "",
        project_name: str = "",
        pdf_path: str = "",
    ):
        instance = self.save_instance(name, rows)
        target = DEFAULT_PROBLEM_CROPS_LIVE_ROOT
        records_dir = target / "records"
        images_dir = target / "images"
        records_dir.mkdir(parents=True, exist_ok=True)
        images_dir.mkdir(parents=True, exist_ok=True)
        source_prefix = f"{safe_name(name, max_len=48)}__"
        active_ids: set[str] = set()
        for row in rows:
            with Image.open(row.image_path) as image:
                for index, box in enumerate(row.boxes, start=1):
                    crop_id = compact_id(source_prefix, row.record_id, f"problem_{index:02d}", prefix="crop", max_len=72)
                    session_source_label = compact_id(source_prefix, row.record_id, f"problem_{index:02d}", prefix="problem", max_len=62)
                    active_ids.add(crop_id)
                    crop_path = images_dir / f"{crop_id}.png"
                    crop_path.parent.mkdir(parents=True, exist_ok=True)
                    image.crop(box).save(crop_path, format="PNG")
                    record_path = records_dir / f"{crop_id}.json"
                    previous: dict = {}
                    if record_path.exists():
                        try:
                            previous = json.loads(record_path.read_text(encoding="utf-8"))
                        except Exception:
                            previous = {}
                    payload = {
                        "schema_version": "problem_crop_live_v1",
                        "crop_id": crop_id,
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                        "source_instance": safe_name(name, max_len=72),
                        "source_instance_full": str(name),
                        "session_json": str(Path(session_path).expanduser().resolve()) if session_path else "",
                        "session_source_label": session_source_label,
                        "book_code": str(book_code or ""),
                        "instance_type": str(instance_type or ""),
                        "project_name": str(project_name or ""),
                        "source_record_id": row.record_id,
                        "source_pdf_path": str(pdf_path or row.pdf_path or ""),
                        "source_page_number": row.page_number,
                        "source_page_image": str(row.image_path),
                        "bbox_px": list(box),
                        "crop_image_rel": str(crop_path.relative_to(target)).replace("\\", "/"),
                        "layout_mode": row.layout_mode,
                        "ocr_status": previous.get("ocr_status") or "pending_ocr",
                        "figure_segmentation_status": previous.get("figure_segmentation_status") or "pending_figure_segmentation",
                        "ocr_text": previous.get("ocr_text") or "",
                        "corrected_text": previous.get("corrected_text") or "",
                        "notes": previous.get("notes") or "",
                        "figure_boxes_px": previous.get("figure_boxes_px") or [],
                    }
                    record_path.write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
        for record_path in records_dir.glob(f"{source_prefix}*.json"):
            if record_path.stem not in active_ids:
                record_path.unlink()
        for image_path in images_dir.glob(f"{source_prefix}*.png"):
            if image_path.stem not in active_ids:
                image_path.unlink()
        self._rewrite_problem_crops_live_manifest(target)
        if return_crop_ids:
            return target, sorted(active_ids)
        return target

    def sync_problem_crops_to_transcriptor_session(
        self,
        name: str,
        rows: list[ProblemPageRecord],
        *,
        session_path: Path,
        book_code: str = "",
        instance_type: str = "",
        project_name: str = "",
        pdf_path: str = "",
    ) -> tuple[Path, int]:
        """Copia los problemas recortados como imagenes fuente normales del Modulo 0."""
        session_path = Path(session_path).expanduser().resolve()
        workspace = infer_workspace_from_session_path(session_path) or session_path.parent.parent
        instance_name = normalize_instance_name(instance_type or session_path.stem, "sesion")
        layout = project_dirs(workspace, instance_name)
        sources_dir = layout["sources_dir"]
        sessions_dir = layout["sessions_dir"]
        sources_dir.mkdir(parents=True, exist_ok=True)
        sessions_dir.mkdir(parents=True, exist_ok=True)

        self.save_instance(name, rows)
        source_prefix = safe_name(name, max_len=42)
        active_labels: set[str] = set()
        files: list[dict[str, str]] = []
        state_sources: list[dict] = []

        for row in rows:
            with Image.open(row.image_path) as image:
                for index, box in enumerate(row.boxes, start=1):
                    label = compact_id(source_prefix, row.record_id, f"problem_{index:02d}", prefix="problem", max_len=62)
                    active_labels.add(label)
                    destination = sources_dir / f"{label}.png"
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    image.crop(box).save(destination, format="PNG")
                    files.append({"label": label, "path": str(destination)})
                    state_sources.append(
                        {
                            "label": label,
                            "path": str(destination),
                            "source_key": label,
                            "reviewed": False,
                            "preview_markers": {},
                            "figure_boxes": [],
                            "segment_detector_audit": {},
                            "ocr_exclusion_box": [],
                            "segments": [],
                        }
                    )

        for old in sources_dir.glob(f"{source_prefix}__*.png"):
            if old.stem not in active_labels:
                old.unlink()

        now = datetime.now().isoformat(timespec="seconds")
        existing = self._load_json_file(session_path)
        ui = existing.get("ui", {}) if isinstance(existing.get("ui"), dict) else {}
        ui.update(
            {
                "book_code": str(book_code or ui.get("book_code", "") or "").strip(),
                "instance_type": instance_name,
                "project_name": str(project_name or ui.get("project_name", "") or workspace.name).strip(),
                "pdf_path": str(pdf_path or ui.get("pdf_path", "") or "").strip(),
            }
        )
        session_payload = dict(existing) if existing else {}
        session_payload.update(
            {
                "session_schema_version": int(session_payload.get("session_schema_version") or 4),
                "project_name": ui.get("project_name") or workspace.name,
                "book_code": ui.get("book_code", ""),
                "instance_type": instance_name,
                "pdf_path": ui.get("pdf_path", ""),
                "ui": ui,
                "state_v3": {
                    **(session_payload.get("state_v3", {}) if isinstance(session_payload.get("state_v3"), dict) else {}),
                    "session_schema_version": 4,
                    "project_name": ui.get("project_name") or workspace.name,
                    "ui_settings": ui,
                },
                "updated_at": now,
            }
        )
        session_payload["images_manifest_file"] = f"{session_path.stem}__images.json"
        session_path.write_text(json.dumps(session_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        manifest_path = session_path.with_name(f"{session_path.stem}__images.json")
        manifest = self._load_json_file(manifest_path)
        manifest_files = self._merge_labeled_rows(manifest.get("files", []) if isinstance(manifest, dict) else [], files)
        manifest_sources = self._merge_labeled_rows(
            manifest.get("state_v3_source_images", []) if isinstance(manifest, dict) else [],
            state_sources,
        )
        manifest_payload = {
            **(manifest if isinstance(manifest, dict) else {}),
            "schema_version": 1,
            "generated_at": now,
            "session_file": session_path.name,
            "session_path": str(session_path),
            "book_code": ui.get("book_code", ""),
            "instance_type": instance_name,
            "files": manifest_files,
            "state_v3_source_images": manifest_sources,
            "sources": [
                {
                    "source_key": row["label"],
                    "label": row["label"],
                    "source_path": row["path"],
                    "source_stem": Path(row["path"]).stem,
                }
                for row in manifest_files
            ],
            "pdf_problem_boxes_source": {
                "module": 13,
                "golden_instance": safe_name(name),
                "synced_at": now,
                "pages": len(rows),
                "problem_crops": len(files),
            },
        }
        manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return sources_dir, len(files)

    def sync_pdf_golden_from_problem_crops(
        self,
        *,
        crops_root: Path | None = None,
        instance_names: set[str] | None = None,
        aggregate_name: str = "",
    ) -> dict[str, int]:
        """Reconstruye la Golden PDF del Modulo 13 desde los recortes vivos.

        Los recortes completos guardan su pagina origen y bbox. Esto permite que
        al enlazar sesiones desde otros modulos tambien se reflejen las paginas
        con boxes dentro de la pestaña Golden PDF.
        """
        target = Path(crops_root or DEFAULT_PROBLEM_CROPS_LIVE_ROOT).expanduser().resolve()
        records_dir = target / "records"
        if not records_dir.exists():
            return {"instances": 0, "unchanged": 0, "pages": 0, "boxes": 0, "records": 0, "skipped": 0}

        allowed = {safe_name(name) for name in (instance_names or set()) if str(name or "").strip()}
        grouped: dict[str, dict[str, dict]] = {}
        aggregate_pages: dict[str, dict] = {}
        skipped = 0
        records_seen = 0
        for record_path in sorted(records_dir.glob("*.json"), key=lambda item: item.name.lower()):
            try:
                raw = json.loads(record_path.read_text(encoding="utf-8"))
            except Exception:
                skipped += 1
                continue
            if not isinstance(raw, dict):
                skipped += 1
                continue

            instance_name = str(raw.get("source_instance_full") or raw.get("source_instance") or "").strip()
            if not instance_name:
                skipped += 1
                continue
            safe_instance = safe_name(instance_name)
            if allowed and safe_instance not in allowed and instance_name not in instance_names:
                continue

            page_image = Path(str(raw.get("source_page_image") or "")).expanduser()
            if not page_image.exists():
                skipped += 1
                continue
            bbox_raw = raw.get("bbox_px") or []
            if not isinstance(bbox_raw, list) or len(bbox_raw) < 4:
                skipped += 1
                continue
            try:
                bbox = tuple(int(round(float(value))) for value in bbox_raw[:4])
            except Exception:
                skipped += 1
                continue
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                skipped += 1
                continue

            records_seen += 1
            source_record_id = str(raw.get("source_record_id") or page_image.stem).strip() or page_image.stem
            page_payload = {
                "pdf_path": str(raw.get("source_pdf_path") or ""),
                "page_number": int(raw.get("source_page_number") or 0),
                "image_path": page_image.resolve(),
                "layout_mode": str(raw.get("layout_mode") or "auto"),
                "boxes": [],
            }
            page_group = grouped.setdefault(instance_name, {}).setdefault(source_record_id, dict(page_payload))
            page_group["boxes"].append(bbox)
            if aggregate_name:
                aggregate_record_id = compact_id(instance_name, source_record_id, prefix="pdfp", max_len=72)
                aggregate_group = aggregate_pages.setdefault(aggregate_record_id, dict(page_payload))
                aggregate_group["boxes"].append(bbox)

        instances = 0
        unchanged = 0
        pages = 0
        boxes = 0
        for instance_name, page_map in grouped.items():
            rows: list[ProblemPageRecord] = []
            for record_id, page in sorted(page_map.items(), key=lambda item: item[0].lower()):
                unique_boxes = list(dict.fromkeys(page["boxes"]))
                ordered_boxes = sort_boxes_reading_order(unique_boxes, str(page.get("layout_mode") or "auto"))
                rows.append(
                    ProblemPageRecord(
                        record_id=record_id,
                        pdf_path=str(page.get("pdf_path") or ""),
                        page_number=int(page.get("page_number") or 0),
                        image_path=Path(page["image_path"]),
                        boxes=ordered_boxes,
                        detector_source="linked_problem_crops_live",
                        reviewed=True,
                        layout_mode=str(page.get("layout_mode") or "auto"),
                    )
                )
            if rows:
                if self._pdf_rows_are_equivalent(self.load_instance(instance_name), rows):
                    unchanged += 1
                else:
                    self.upsert_instance_rows(instance_name, rows)
                    instances += 1
                pages += len(rows)
                boxes += sum(len(row.boxes) for row in rows)

        aggregate_updated = 0
        aggregate_unchanged = 0
        if aggregate_name and aggregate_pages:
            existing_rows = self.load_instance(aggregate_name)
            merged_by_id = {row.record_id: row for row in existing_rows}
            incoming_rows: list[ProblemPageRecord] = []
            for record_id, page in sorted(aggregate_pages.items(), key=lambda item: item[0].lower()):
                unique_boxes = list(dict.fromkeys(page["boxes"]))
                ordered_boxes = sort_boxes_reading_order(unique_boxes, str(page.get("layout_mode") or "auto"))
                incoming_rows.append(
                    ProblemPageRecord(
                        record_id=record_id,
                        pdf_path=str(page.get("pdf_path") or ""),
                        page_number=int(page.get("page_number") or 0),
                        image_path=Path(page["image_path"]),
                        boxes=ordered_boxes,
                        detector_source="linked_problem_crops_live",
                        reviewed=True,
                        layout_mode=str(page.get("layout_mode") or "auto"),
                    )
                )
            if self._pdf_rows_are_equivalent([merged_by_id.get(row.record_id) for row in incoming_rows if merged_by_id.get(row.record_id)], incoming_rows):
                aggregate_unchanged = 1
            else:
                for row in incoming_rows:
                    merged_by_id[row.record_id] = row
                self.upsert_instance_rows(aggregate_name, incoming_rows)
                aggregate_updated = 1

        return {
            "instances": instances,
            "unchanged": unchanged,
            "aggregate_updated": aggregate_updated,
            "aggregate_unchanged": aggregate_unchanged,
            "pages": pages,
            "boxes": boxes,
            "records": records_seen,
            "skipped": skipped,
        }

    @staticmethod
    def _pdf_rows_are_equivalent(existing: list[ProblemPageRecord], incoming: list[ProblemPageRecord]) -> bool:
        if len(existing) != len(incoming):
            return False
        existing_by_id = {row.record_id: row for row in existing}
        for row in incoming:
            current = existing_by_id.get(row.record_id)
            if current is None or not current.image_path.exists():
                return False
            if int(current.page_number) != int(row.page_number):
                return False
            if [tuple(box) for box in current.boxes] != [tuple(box) for box in row.boxes]:
                return False
        return True

    @staticmethod
    def _load_json_file(path: Path) -> dict:
        if not Path(path).exists():
            return {}
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            try:
                return json.loads(Path(path).read_text(encoding="utf-8-sig"))
            except Exception:
                return {}

    @staticmethod
    def _merge_labeled_rows(existing: object, incoming: list[dict]) -> list[dict]:
        merged: dict[str, dict] = {}
        if isinstance(existing, list):
            for row in existing:
                if not isinstance(row, dict):
                    continue
                label = str(row.get("label") or row.get("source_key") or "").strip()
                if label:
                    merged[label] = dict(row)
        for row in incoming:
            label = str(row.get("label") or row.get("source_key") or "").strip()
            if label:
                merged[label] = dict(row)
        return list(merged.values())

    @staticmethod
    def _rewrite_problem_crops_live_manifest(target: Path) -> None:
        records = list((target / "records").glob("*.json"))
        payload = {
            "schema_version": "problem_crops_live_index_v1",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "problem_crops_total": len(records),
            "records_dir": "records",
            "images_dir": "images",
            "notes": [
                "Cada imagen corresponde a un problema matematico completo corregido en Modulo 13.",
                "Es materia prima pendiente para OCR y para deteccion de graficos internos.",
                "No se asume que todos los problemas contienen graficos.",
            ],
        }
        (target / "manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_record(self, instance: Path, record_id: str) -> dict:
        path = instance / "records" / f"{record_id}.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _rewrite_manifest(self, name: str, rows: list[ProblemPageRecord]) -> None:
        instance = self.instance_dir(name)
        payload = {
            "schema_version": "pdf_problem_boxes_golden_v1",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "instance": safe_name(name),
            "pages_total": len(rows),
            "boxes_total": sum(len(row.boxes) for row in rows),
            "positive_pages": sum(1 for row in rows if row.boxes),
            "negative_pages": sum(1 for row in rows if not row.boxes),
            "reviewed_pages": sum(1 for row in rows if row.reviewed),
            "layout_modes": {
                "auto": sum(1 for row in rows if row.layout_mode == "auto"),
                "una_columna": sum(1 for row in rows if row.layout_mode == "una_columna"),
                "dos_columnas": sum(1 for row in rows if row.layout_mode == "dos_columnas"),
            },
            "notes": [
                "Golden Base exclusiva para detectar problemas matematicos completos dentro de paginas PDF.",
                "No corresponde al detector de graficos internos ni al segmentador de figuras.",
                "Marcas de agua y subtitulos aislados son fondo: nunca se incluyen como boxes.",
            ],
        }
        (instance / "manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
