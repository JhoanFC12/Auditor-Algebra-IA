from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import random
import socket
import threading
import time
import traceback
import urllib.parse
import webbrowser
from collections import Counter
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    from PIL import Image
except Exception:  # pragma: no cover - reported by API at runtime
    Image = None  # type: ignore[assignment]


CLASS_MAP: dict[int, str] = {
    0: "problem",
    1: "problem_number",
    2: "answer_block",
}

DEFAULT_PORT = 8776
DATASET_GLOBS = (
    "problem_detector_multiclass_ingrid_review_*",
    "problem_detector_multiclass_100_lab_*",
)
ALLOWED_DATASET_PREFIXES = (
    "problem_detector_multiclass_ingrid_review_",
    "problem_detector_multiclass_100_lab_",
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def datasets_root() -> Path:
    return repo_root() / ".cache" / "transcriptor_runs" / "datasets"


def default_dataset_root() -> Path:
    explicit = os.getenv("PROBLEM_DETECTOR_LAB_DATASET", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    datasets = datasets_root()
    ingrid_workspace = datasets / "problem_detector_multiclass_ingrid_review_20260714_v1"
    if ingrid_workspace.is_dir():
        return ingrid_workspace.resolve()
    preferred = datasets / "problem_detector_multiclass_100_lab_20260624"
    candidates = [
        path
        for pattern in DATASET_GLOBS
        for path in datasets.glob(pattern)
        if path.is_dir()
    ]
    if candidates:
        return max(candidates, key=lambda p: p.stat().st_mtime).resolve()
    return preferred.resolve()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalized_yolo_rows(path: Path) -> tuple[tuple[str, ...], ...]:
    """Compare YOLO labels by rows, ignoring line-ending and spacing noise."""
    if not path.is_file():
        return ()
    return tuple(
        tuple(line.split())
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def labels_semantically_equal(left: Path, right: Path) -> bool:
    if not left.is_file() or not right.is_file():
        return False
    return normalized_yolo_rows(left) == normalized_yolo_rows(right)


def read_review_selection(reviews_dir: Path) -> dict[str, Any]:
    """Return the active review batch, falling back to the original pilot."""
    active_path = reviews_dir / "batches_50" / "active_batch.json"
    active = read_json(active_path, {})
    manifest_value = active.get("manifest") if isinstance(active, dict) else None
    if manifest_value:
        manifest_path = Path(str(manifest_value)).expanduser()
        if not manifest_path.is_absolute():
            manifest_path = active_path.parent / manifest_path
        try:
            resolved = manifest_path.resolve()
            resolved.relative_to(reviews_dir.resolve())
        except (OSError, ValueError):
            resolved = None
        if resolved is not None:
            selection = read_json(resolved, {})
            if isinstance(selection, dict) and isinstance(selection.get("rows"), list):
                return selection
    selection = read_json(reviews_dir / "pilot_selection.json", {})
    return selection if isinstance(selection, dict) else {}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def image_size(path: Path) -> tuple[int, int]:
    if Image is None:
        raise RuntimeError("Pillow no esta disponible; instala pillow para leer imagenes.")
    with Image.open(path) as img:
        return int(img.width), int(img.height)


def read_yolo_boxes(label_path: Path, width: int, height: int) -> list[dict[str, Any]]:
    boxes: list[dict[str, Any]] = []
    if not label_path.exists():
        return boxes
    for line_no, raw_line in enumerate(label_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 5:
            continue
        try:
            cls = int(float(parts[0]))
            cx, cy, bw, bh = [float(item) for item in parts[1:]]
        except Exception:
            continue
        x1 = (cx - bw / 2.0) * width
        y1 = (cy - bh / 2.0) * height
        x2 = (cx + bw / 2.0) * width
        y2 = (cy + bh / 2.0) * height
        boxes.append(
            {
                "id": f"box-{line_no}-{cls}",
                "cls": cls,
                "class_name": CLASS_MAP.get(cls, str(cls)),
                "x1": round(clamp(x1, 0, width), 2),
                "y1": round(clamp(y1, 0, height), 2),
                "x2": round(clamp(x2, 0, width), 2),
                "y2": round(clamp(y2, 0, height), 2),
            }
        )
    return boxes


def boxes_to_yolo(boxes: list[dict[str, Any]], width: int, height: int) -> str:
    rows: list[str] = []
    for box in boxes:
        try:
            cls = int(box.get("cls", 0))
            x1 = clamp(float(box["x1"]), 0, width)
            y1 = clamp(float(box["y1"]), 0, height)
            x2 = clamp(float(box["x2"]), 0, width)
            y2 = clamp(float(box["y2"]), 0, height)
        except Exception:
            continue
        if cls not in CLASS_MAP:
            continue
        if x2 < x1:
            x1, x2 = x2, x1
        if y2 < y1:
            y1, y2 = y2, y1
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        cx = ((x1 + x2) / 2.0) / width
        cy = ((y1 + y2) / 2.0) / height
        bw = (x2 - x1) / width
        bh = (y2 - y1) / height
        rows.append(f"{cls} {cx:.8f} {cy:.8f} {bw:.8f} {bh:.8f}")
    return ("\n".join(rows) + "\n") if rows else ""


class ProblemDetectorLabServer:
    def __init__(self, dataset_root: Path | None = None, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
        self.dataset_root = (dataset_root or default_dataset_root()).resolve()
        self.host = host
        self.port = port
        self.static_root = Path(__file__).with_name("web")
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def images_dir(self) -> Path:
        return self.dataset_root / "images"

    @property
    def labels_dir(self) -> Path:
        return self.dataset_root / "labels"

    @property
    def metadata_dir(self) -> Path:
        return self.dataset_root / "metadata"

    @property
    def baseline_labels_dir(self) -> Path:
        return self.dataset_root / "baseline_labels"

    @property
    def reviews_dir(self) -> Path:
        return self.dataset_root / "reviews"

    @property
    def comparison_mode(self) -> bool:
        return self.baseline_labels_dir.is_dir()

    def start(self, *, open_browser: bool = True) -> str:
        self._ensure_dataset()
        handler_server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                handler_server._handle_get(self)

            def do_POST(self) -> None:  # noqa: N802
                handler_server._handle_post(self)

            def log_message(self, fmt: str, *args: Any) -> None:
                return

        port = self._find_available_port(self.port)
        self.port = port
        self._server = ThreadingHTTPServer((self.host, port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        url = f"http://127.0.0.1:{port}/"
        if open_browser:
            webbrowser.open(url)
        return url

    def serve_forever(self) -> None:
        self._ensure_dataset()
        handler_server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                handler_server._handle_get(self)

            def do_POST(self) -> None:  # noqa: N802
                handler_server._handle_post(self)

            def log_message(self, fmt: str, *args: Any) -> None:
                return

        self.port = self._find_available_port(self.port)
        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._server.serve_forever()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    def _ensure_dataset(self) -> None:
        if not self.dataset_root.exists():
            raise FileNotFoundError(f"No existe el dataset: {self.dataset_root}")
        if not self.images_dir.exists():
            raise FileNotFoundError(f"No existe la carpeta de imagenes: {self.images_dir}")
        self.labels_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _find_available_port(preferred: int) -> int:
        for port in [preferred, *range(preferred + 1, preferred + 50)]:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind(("127.0.0.1", port))
                    return port
                except OSError:
                    continue
        raise RuntimeError("No se encontro un puerto local disponible para Problem Detector Lab.")

    def _sample_entries(self) -> list[dict[str, Any]]:
        selection = read_review_selection(self.reviews_dir)
        selected_rows = selection.get("rows", []) if isinstance(selection, dict) else []
        entries: list[dict[str, Any]] = []
        if selected_rows:
            for order, row in enumerate(selected_rows, start=1):
                if not isinstance(row, dict) or not row.get("sample_id"):
                    continue
                sample_id = str(row["sample_id"])
                split = str(row.get("split") or "")
                image_path = self.images_dir / split / f"{sample_id}.png" if split else self.images_dir / f"{sample_id}.png"
                if image_path.is_file():
                    entries.append(
                        {
                            "sample_id": sample_id,
                            "split": split,
                            "image_path": image_path,
                            "selection": row,
                            "order": int(row.get("order") or order),
                        }
                    )
            if entries:
                return sorted(entries, key=lambda item: item["order"])

        for image_path in sorted(self.images_dir.glob("*.png")):
            entries.append(
                {
                    "sample_id": image_path.stem,
                    "split": "",
                    "image_path": image_path,
                    "selection": {},
                    "order": len(entries) + 1,
                }
            )
        for split_dir in sorted(path for path in self.images_dir.iterdir() if path.is_dir()):
            for image_path in sorted(split_dir.glob("*.png")):
                entries.append(
                    {
                        "sample_id": image_path.stem,
                        "split": split_dir.name,
                        "image_path": image_path,
                        "selection": {},
                        "order": len(entries) + 1,
                    }
                )
        return entries

    def _resolve_sample(self, sample_id: str, split: str = "") -> dict[str, Any]:
        matches = [
            entry
            for entry in self._sample_entries()
            if entry["sample_id"] == sample_id and (not split or entry["split"] == split)
        ]
        if not matches:
            raise FileNotFoundError(f"Muestra no encontrada: {split}/{sample_id}" if split else f"Muestra no encontrada: {sample_id}")
        if len(matches) > 1:
            raise ValueError(f"La muestra {sample_id} existe en mas de un split; especifica split.")
        return matches[0]

    def _dataset_candidates(self) -> list[Path]:
        root = datasets_root()
        if not root.exists():
            return []
        candidates = {
            path.resolve()
            for pattern in DATASET_GLOBS
            for path in root.glob(pattern)
            if path.is_dir()
        }
        return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)

    def _dataset_card(self, path: Path) -> dict[str, Any]:
        manifest = read_json(path / "manifest.json", {})
        images_dir = path / "images"
        labels_dir = path / "labels"
        baseline_dir = path / "baseline_labels"
        reviews_dir = path / "reviews"
        selection = read_review_selection(reviews_dir)
        selected_rows = selection.get("rows", []) if isinstance(selection, dict) else []
        if selected_rows:
            samples = [
                (str(row.get("split") or ""), str(row.get("sample_id") or ""))
                for row in selected_rows
                if isinstance(row, dict) and row.get("sample_id")
            ]
        else:
            samples = [
                ("" if image.parent == images_dir else image.parent.name, image.stem)
                for image in sorted(images_dir.rglob("*.png"))
            ] if images_dir.exists() else []
        changed_total = 0
        approved_total = 0
        for split, sample_id in samples:
            label_path = labels_dir / split / f"{sample_id}.txt" if split else labels_dir / f"{sample_id}.txt"
            baseline_path = baseline_dir / split / f"{sample_id}.txt" if split else baseline_dir / f"{sample_id}.txt"
            if baseline_path.is_file() and label_path.is_file() and not labels_semantically_equal(baseline_path, label_path):
                changed_total += 1
            review_path = reviews_dir / split / f"{sample_id}.json" if split else reviews_dir / f"{sample_id}.json"
            review = read_json(review_path, {})
            if baseline_dir.is_dir():
                if isinstance(review, dict) and (review.get("human_review") == "approved" or review.get("status") == "human_approved"):
                    approved_total += 1
            else:
                metadata_path = path / "metadata" / split / f"{sample_id}.json" if split else path / "metadata" / f"{sample_id}.json"
                metadata = read_json(metadata_path, {})
                label_review = metadata.get("label_review") if isinstance(metadata, dict) else None
                if isinstance(label_review, dict) and label_review.get("reviewed_at"):
                    approved_total += 1
        samples_total = len(samples)
        return {
            "name": path.name,
            "path": str(path),
            "samples_total": samples_total,
            "reviewed_total": approved_total,
            "pending_total": max(0, samples_total - approved_total),
            "changed_total": changed_total,
            "comparison_mode": baseline_dir.is_dir(),
            "updated_at": path.stat().st_mtime,
            "current": path.resolve() == self.dataset_root.resolve(),
            "schema_version": manifest.get("schema_version", ""),
            "selected_by_group": manifest.get("selected_by_group", {}),
        }

    def _datasets_payload(self) -> dict[str, Any]:
        candidates = self._dataset_candidates()
        return {
            "current_dataset_root": str(self.dataset_root),
            "datasets_root": str(datasets_root()),
            "datasets": [self._dataset_card(path) for path in candidates],
        }

    def _select_dataset(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        dataset_root = str(payload.get("dataset_root") or "").strip()
        base = datasets_root().resolve()
        if name:
            candidate = (base / name).resolve()
        elif dataset_root:
            candidate = Path(dataset_root).expanduser().resolve()
        else:
            raise ValueError("Falta name o dataset_root.")
        if base not in [candidate, *candidate.parents]:
            raise ValueError("Dataset fuera de la carpeta permitida.")
        if not candidate.name.startswith(ALLOWED_DATASET_PREFIXES):
            raise ValueError("Dataset no permitido para Problem Detector Lab.")
        old_root = self.dataset_root
        self.dataset_root = candidate
        try:
            self._ensure_dataset()
        except Exception:
            self.dataset_root = old_root
            raise
        return self._dataset_summary()

    @staticmethod
    def _split_path(root: Path, sample_id: str, split: str, suffix: str) -> Path:
        return root / split / f"{sample_id}{suffix}" if split else root / f"{sample_id}{suffix}"

    def _image_path(self, sample_id: str, split: str = "") -> Path:
        return Path(self._resolve_sample(sample_id, split)["image_path"])

    def _label_path(self, sample_id: str, split: str = "") -> Path:
        return self._split_path(self.labels_dir, sample_id, split, ".txt")

    def _baseline_label_path(self, sample_id: str, split: str = "") -> Path:
        return self._split_path(self.baseline_labels_dir, sample_id, split, ".txt")

    def _metadata_path(self, sample_id: str, split: str = "") -> Path:
        return self._split_path(self.metadata_dir, sample_id, split, ".json")

    def _review_path(self, sample_id: str, split: str = "") -> Path:
        return self._split_path(self.reviews_dir, sample_id, split, ".json")

    @staticmethod
    def _label_class_counts(path: Path) -> dict[str, int]:
        counts = {name: 0 for name in CLASS_MAP.values()}
        if not path.is_file():
            return counts
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) != 5:
                continue
            try:
                cls = int(float(parts[0]))
            except Exception:
                continue
            if cls in CLASS_MAP:
                counts[CLASS_MAP[cls]] += 1
        return counts

    @staticmethod
    def _box_signature(box: dict[str, Any]) -> tuple[Any, ...]:
        return (
            int(box.get("cls", -1)),
            round(float(box.get("x1", 0)), 2),
            round(float(box.get("y1", 0)), 2),
            round(float(box.get("x2", 0)), 2),
            round(float(box.get("y2", 0)), 2),
        )

    def _comparison_payload(
        self,
        baseline_boxes: list[dict[str, Any]],
        current_boxes: list[dict[str, Any]],
        *,
        has_baseline: bool,
    ) -> dict[str, Any]:
        before = Counter(self._box_signature(box) for box in baseline_boxes)
        after = Counter(self._box_signature(box) for box in current_boxes)
        added = after - before
        removed = before - after
        added_by_class = {name: 0 for name in CLASS_MAP.values()}
        removed_by_class = {name: 0 for name in CLASS_MAP.values()}
        for signature, count in added.items():
            if signature[0] in CLASS_MAP:
                added_by_class[CLASS_MAP[signature[0]]] += count
        for signature, count in removed.items():
            if signature[0] in CLASS_MAP:
                removed_by_class[CLASS_MAP[signature[0]]] += count
        return {
            "has_baseline": has_baseline,
            "has_changes": bool(added or removed) if has_baseline else False,
            "baseline_box_count": len(baseline_boxes),
            "current_box_count": len(current_boxes),
            "added_box_count": sum(added.values()),
            "removed_box_count": sum(removed.values()),
            "delta_box_count": len(current_boxes) - len(baseline_boxes),
            "added_by_class": added_by_class,
            "removed_by_class": removed_by_class,
        }

    def _dataset_summary(self) -> dict[str, Any]:
        manifest = read_json(self.dataset_root / "manifest.json", {})
        workspace_manifest = read_json(self.dataset_root / "workspace_manifest.json", {})
        rows_by_id = {
            str(row.get("sample_id")): row
            for row in manifest.get("rows", [])
            if isinstance(row, dict) and row.get("sample_id")
        }
        samples: list[dict[str, Any]] = []
        approved_total = 0
        changed_total = 0
        for entry in self._sample_entries():
            sample_id = str(entry["sample_id"])
            split = str(entry.get("split") or "")
            selection = entry.get("selection") if isinstance(entry.get("selection"), dict) else {}
            row = selection or rows_by_id.get(sample_id, {})
            metadata = read_json(self._metadata_path(sample_id, split), {})
            if not isinstance(metadata, dict):
                metadata = {}
            review = read_json(self._review_path(sample_id, split), {})
            if not isinstance(review, dict):
                review = {}
            label_review = metadata.get("label_review") if isinstance(metadata, dict) else None
            human_approved = review.get("human_review") == "approved" or review.get("status") == "human_approved"
            legacy_reviewed = bool(isinstance(label_review, dict) and label_review.get("reviewed_at"))
            is_reviewed = human_approved if self.comparison_mode else legacy_reviewed
            if is_reviewed:
                approved_total += 1
            label_path = self._label_path(sample_id, split)
            baseline_path = self._baseline_label_path(sample_id, split)
            has_baseline = baseline_path.is_file()
            has_changes = has_baseline and label_path.is_file() and not labels_semantically_equal(baseline_path, label_path)
            if has_changes:
                changed_total += 1
            class_counts = self._label_class_counts(label_path)
            baseline_counts = self._label_class_counts(baseline_path) if has_baseline else class_counts
            samples.append(
                {
                    "sample_id": sample_id,
                    "sample_key": f"{split}:{sample_id}" if split else sample_id,
                    "split": split,
                    "order": int(entry.get("order") or len(samples) + 1),
                    "group": str(row.get("book_code") or row.get("project_name") or row.get("group") or metadata.get("group") or "sin_grupo"),
                    "instance": str(row.get("instance_type") or row.get("instance") or metadata.get("instance_type") or metadata.get("instance") or ""),
                    "page_number": int(row.get("page_number") or metadata.get("page_number") or 0),
                    "problem_boxes": class_counts["problem"],
                    "number_boxes": class_counts["problem_number"],
                    "answer_blocks": class_counts["answer_block"],
                    "baseline_problem_boxes": baseline_counts["problem"],
                    "baseline_number_boxes": baseline_counts["problem_number"],
                    "baseline_answer_blocks": baseline_counts["answer_block"],
                    "reviewed": is_reviewed,
                    "human_review": str(review.get("human_review") or "pending" if self.comparison_mode else ""),
                    "review_status": str(review.get("status") or ""),
                    "has_baseline": has_baseline,
                    "has_changes": has_changes,
                    "delta_box_count": sum(class_counts.values()) - sum(baseline_counts.values()),
                }
            )
        return {
            "dataset_root": str(self.dataset_root),
            "samples_total": len(samples),
            "reviewed_total": approved_total,
            "approved_total": approved_total,
            "pending_total": len(samples) - approved_total,
            "pending_human_total": len(samples) - approved_total if self.comparison_mode else len(samples) - approved_total,
            "changed_total": changed_total,
            "unchanged_total": len(samples) - changed_total,
            "comparison_mode": self.comparison_mode,
            "supports_export": not self.comparison_mode,
            "class_map": CLASS_MAP,
            "manifest": {
                "schema_version": workspace_manifest.get("schema_version") or manifest.get("schema_version", ""),
                "selected_by_group": manifest.get("selected_by_group", {}),
                "problem_boxes_total": manifest.get("problem_boxes_total", 0),
                "number_boxes_total": manifest.get("number_boxes_total", 0),
                "answer_blocks_total": manifest.get("answer_blocks_total", 0),
            },
            "samples": samples,
        }

    def _sample_payload(self, sample_id: str, split: str = "") -> dict[str, Any]:
        entry = self._resolve_sample(sample_id, split)
        split = str(entry.get("split") or "")
        image_path = Path(entry["image_path"])
        width, height = image_size(image_path)
        metadata = read_json(self._metadata_path(sample_id, split), {})
        review = read_json(self._review_path(sample_id, split), {})
        boxes = read_yolo_boxes(self._label_path(sample_id, split), width, height)
        baseline_path = self._baseline_label_path(sample_id, split)
        has_baseline = baseline_path.is_file()
        baseline_boxes = read_yolo_boxes(baseline_path, width, height) if has_baseline else [dict(box) for box in boxes]
        comparison = self._comparison_payload(baseline_boxes, boxes, has_baseline=has_baseline)
        split_query = f"&split={urllib.parse.quote(split)}" if split else ""
        return {
            "sample_id": sample_id,
            "sample_key": f"{split}:{sample_id}" if split else sample_id,
            "split": split,
            "width": width,
            "height": height,
            "image_url": f"/api/image?id={urllib.parse.quote(sample_id)}{split_query}&v={int(image_path.stat().st_mtime)}",
            "baseline_boxes": baseline_boxes,
            "boxes": boxes,
            "metadata": metadata,
            "review": review,
            "comparison": comparison,
        }

    def _save_sample(self, payload: dict[str, Any]) -> dict[str, Any]:
        sample_id = str(payload.get("sample_id") or "").strip()
        split = str(payload.get("split") or "").strip()
        boxes = payload.get("boxes")
        if not sample_id:
            raise ValueError("Falta sample_id.")
        if not isinstance(boxes, list):
            raise ValueError("boxes debe ser una lista.")
        entry = self._resolve_sample(sample_id, split)
        split = str(entry.get("split") or "")
        image_path = Path(entry["image_path"])
        width, height = image_size(image_path)
        label_path = self._label_path(sample_id, split)
        label_path.parent.mkdir(parents=True, exist_ok=True)
        label_path.write_text(boxes_to_yolo(boxes, width, height), encoding="utf-8")
        metadata_path = self._metadata_path(sample_id, split)
        metadata = read_json(metadata_path, {})
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["label_review"] = {
            "reviewed_at": now_iso(),
            "tool": "problem_detector_lab_web",
            "box_count": len(boxes),
            "class_counts": {
                CLASS_MAP[key]: sum(1 for box in boxes if int(box.get("cls", -1)) == key)
                for key in CLASS_MAP
            },
        }
        write_json(metadata_path, metadata)
        if self.comparison_mode:
            review_path = self._review_path(sample_id, split)
            review = read_json(review_path, {})
            if not isinstance(review, dict):
                review = {}
            review.update(
                {
                    "schema_version": review.get("schema_version") or "ingrid_training_box_review_v1",
                    "agent_id": review.get("agent_id") or "ingrid_daubechies_v1",
                    "sample_id": sample_id,
                    "split": split,
                    "status": "human_edited_pending_approval",
                    "human_review": "pending",
                    "human_last_edited_at": now_iso(),
                    "human_review_tool": "problem_detector_lab_comparison",
                    "training_candidate": False,
                }
            )
            write_json(review_path, review)
        return self._sample_payload(sample_id, split)

    @staticmethod
    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _approve_sample(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.comparison_mode:
            raise ValueError("El dataset activo no tiene baseline_labels para aprobar una comparacion.")
        sample_id = str(payload.get("sample_id") or "").strip()
        split = str(payload.get("split") or "").strip()
        if not sample_id:
            raise ValueError("Falta sample_id.")
        entry = self._resolve_sample(sample_id, split)
        split = str(entry.get("split") or "")
        baseline_path = self._baseline_label_path(sample_id, split)
        label_path = self._label_path(sample_id, split)
        if not baseline_path.is_file() or not label_path.is_file():
            raise FileNotFoundError("Falta baseline o label actual para aprobar la comparacion.")
        current = self._sample_payload(sample_id, split)
        review_path = self._review_path(sample_id, split)
        review = read_json(review_path, {})
        if not isinstance(review, dict):
            review = {}
        review.update(
            {
                "schema_version": review.get("schema_version") or "ingrid_training_box_review_v1",
                "agent_id": review.get("agent_id") or "ingrid_daubechies_v1",
                "sample_id": sample_id,
                "split": split,
                "status": "human_approved",
                "human_review": "approved",
                "human_reviewed_at": now_iso(),
                "human_review_tool": "problem_detector_lab_comparison",
                "approved_baseline_sha256": self._sha256(baseline_path),
                "approved_label_sha256": self._sha256(label_path),
                "training_candidate": bool(current["comparison"]["has_changes"]),
            }
        )
        write_json(review_path, review)
        current = self._sample_payload(sample_id, split)
        current["approval_gate"] = self._write_human_approval_gate()
        return current

    def _human_approval_gate(self) -> dict[str, Any]:
        selection = read_review_selection(self.reviews_dir)
        rows = selection.get("rows", []) if isinstance(selection, dict) else []
        approved_samples: list[str] = []
        pending_samples: list[str] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("sample_id"):
                continue
            sample_id = str(row["sample_id"])
            split = str(row.get("split") or "")
            sample_key = f"{split}/{sample_id}" if split else sample_id
            review = read_json(self._review_path(sample_id, split), {})
            approved = isinstance(review, dict) and (
                review.get("human_review") == "approved" or review.get("status") == "human_approved"
            )
            (approved_samples if approved else pending_samples).append(sample_key)
        return {
            "schema_version": "problem_detector_human_approval_gate_v1",
            "updated_at": now_iso(),
            "dataset_root": str(self.dataset_root),
            "queue_id": str(selection.get("queue_id") or selection.get("batch_id") or "active_review"),
            "samples_total": len(approved_samples) + len(pending_samples),
            "approved_total": len(approved_samples),
            "pending_total": len(pending_samples),
            "status": "ready_for_database" if not pending_samples and approved_samples else "pending_human_review",
            "approved_samples": approved_samples,
            "pending_samples": pending_samples,
        }

    def _write_human_approval_gate(self) -> dict[str, Any]:
        gate = self._human_approval_gate()
        write_json(self.reviews_dir / "human_approval_gate.json", gate)
        return gate

    def _export_dataset_yaml(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.comparison_mode:
            raise ValueError("La exportacion esta deshabilitada durante la auditoria comparativa de Ingrid.")
        val_ratio = float(payload.get("val_ratio") or 0.2)
        val_ratio = clamp(val_ratio, 0.05, 0.5)
        seed = int(payload.get("seed") or 20260624)
        entries = self._sample_entries()
        if any(entry.get("split") for entry in entries):
            raise ValueError("La exportacion automatica solo esta disponible para datasets planos.")
        sample_ids = [str(entry["sample_id"]) for entry in entries]
        rng = random.Random(seed)
        shuffled = list(sample_ids)
        rng.shuffle(shuffled)
        val_count = max(1, int(round(len(shuffled) * val_ratio))) if shuffled else 0
        val_ids = set(shuffled[:val_count])
        train_paths = [str((self.images_dir / f"{sid}.png").resolve()) for sid in sample_ids if sid not in val_ids]
        val_paths = [str((self.images_dir / f"{sid}.png").resolve()) for sid in sample_ids if sid in val_ids]
        (self.dataset_root / "train.txt").write_text("\n".join(train_paths) + "\n", encoding="utf-8")
        (self.dataset_root / "val.txt").write_text("\n".join(val_paths) + "\n", encoding="utf-8")
        yaml_text = "\n".join(
            [
                f"path: {self.dataset_root.as_posix()}",
                "train: train.txt",
                "val: val.txt",
                "names:",
                "  0: problem",
                "  1: problem_number",
                "  2: answer_block",
                "",
            ]
        )
        yaml_path = self.dataset_root / "dataset.yaml"
        yaml_path.write_text(yaml_text, encoding="utf-8")
        return {
            "dataset_yaml": str(yaml_path),
            "train_count": len(train_paths),
            "val_count": len(val_paths),
            "seed": seed,
        }

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        try:
            parsed = urllib.parse.urlparse(handler.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            if path == "/api/health":
                self._send_json(handler, {"ok": True, "app": "Problem Detector Lab", "dataset_root": str(self.dataset_root)})
                return
            if path == "/api/dataset":
                self._send_json(handler, self._dataset_summary())
                return
            if path == "/api/datasets":
                self._send_json(handler, self._datasets_payload())
                return
            if path == "/api/sample":
                sample_id = str(query.get("id", [""])[0])
                split = str(query.get("split", [""])[0])
                self._send_json(handler, self._sample_payload(sample_id, split))
                return
            if path == "/api/image":
                sample_id = str(query.get("id", [""])[0])
                split = str(query.get("split", [""])[0])
                self._send_file(handler, self._image_path(sample_id, split))
                return
            self._send_static(handler, path)
        except Exception as exc:
            self._send_error_json(handler, exc)

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        try:
            parsed = urllib.parse.urlparse(handler.path)
            payload = self._read_request_json(handler)
            if parsed.path == "/api/save":
                self._send_json(handler, self._save_sample(payload))
                return
            if parsed.path == "/api/review/approve":
                self._send_json(handler, self._approve_sample(payload))
                return
            if parsed.path == "/api/export-yaml":
                self._send_json(handler, self._export_dataset_yaml(payload))
                return
            if parsed.path == "/api/dataset/select":
                self._send_json(handler, self._select_dataset(payload))
                return
            raise FileNotFoundError(f"Ruta API no encontrada: POST {parsed.path}")
        except Exception as exc:
            self._send_error_json(handler, exc)

    def _read_request_json(self, handler: BaseHTTPRequestHandler) -> dict[str, Any]:
        length = int(handler.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = handler.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _send_static(self, handler: BaseHTTPRequestHandler, path: str) -> None:
        name = "index.html" if path in ("", "/") else path.lstrip("/")
        file_path = (self.static_root / name).resolve()
        if self.static_root.resolve() not in [file_path, *file_path.parents]:
            raise FileNotFoundError("Ruta estatica no permitida.")
        if not file_path.exists() or not file_path.is_file():
            file_path = self.static_root / "index.html"
        self._send_file(handler, file_path)

    def _send_file(self, handler: BaseHTTPRequestHandler, path: Path) -> None:
        data = path.read_bytes()
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        handler.send_response(200)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Last-Modified", formatdate(path.stat().st_mtime, usegmt=True))
        handler.end_headers()
        handler.wfile.write(data)

    @staticmethod
    def _send_json(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(data)

    def _send_error_json(self, handler: BaseHTTPRequestHandler, exc: Exception) -> None:
        self._send_json(
            handler,
            {
                "ok": False,
                "error": str(exc),
                "traceback": traceback.format_exc(limit=8),
            },
            status=500,
        )


def main() -> None:
    host = os.getenv("PROBLEM_DETECTOR_LAB_HOST", "127.0.0.1")
    port = int(os.getenv("PROBLEM_DETECTOR_LAB_PORT", str(DEFAULT_PORT)))
    dataset_root = default_dataset_root()
    server = ProblemDetectorLabServer(dataset_root=dataset_root, host=host, port=port)
    url = f"http://127.0.0.1:{server._find_available_port(port)}/"
    print(f"Problem Detector Lab")
    print(f"Dataset: {dataset_root}")
    print(f"URL: {url}")
    webbrowser.open(url)
    server.serve_forever()


if __name__ == "__main__":
    main()
