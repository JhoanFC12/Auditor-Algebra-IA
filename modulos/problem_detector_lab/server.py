from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import random
import re
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

from modulos.instance_factory.annotation_quality import evaluate_precision_annotation

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

AUDIT_ROLE_ORDER = ("theory", "problem", "solution")
AUDIT_ROLE_MAPPING: dict[str, tuple[str, ...]] = {
    "theory": ("theory",),
    "definition_property_theorem": ("theory",),
    "worked_example": ("theory",),
    "proposed_problem": ("problem",),
    "solved_problem": ("problem", "solution"),
    "answer_key": ("solution",),
    "solution": ("solution",),
}
AUDIT_MEDIA_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
VISUAL_AUDIT_SESSION_SCHEMA = "problem_detector_visual_audit_session_v1"
VISUAL_AUDIT_SAFE_PERMISSIONS = {
    "read_only": True,
    "canonical_writes": False,
    "boxes_or_crops": False,
    "map_mutation": False,
    "pdf_mutation": False,
}


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


def default_library_audit_root() -> Path:
    explicit = os.getenv("PROBLEM_DETECTOR_LAB_LIBRARY_AUDIT_ROOT", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (repo_root() / ".cache" / "book_catalog" / "problem_solution_staging").resolve()


def audit_roles_for_content_roles(content_roles: list[str] | tuple[str, ...]) -> dict[str, Any]:
    """Apply the shared page_role_mapping_v1 without inventing local semantics."""
    derived = {
        audit_role
        for content_role in content_roles
        for audit_role in AUDIT_ROLE_MAPPING.get(str(content_role), ())
    }
    return {
        "schema_version": "library_page_audit_roles_v1",
        "mapping_version": "page_role_mapping_v1",
        "roles": [role for role in AUDIT_ROLE_ORDER if role in derived],
        "source": "derived",
    }


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
    def __init__(
        self,
        dataset_root: Path | None = None,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        *,
        library_audit_root: Path | None = None,
        library_media_root: Path | None = None,
    ) -> None:
        self.dataset_root = (dataset_root or default_dataset_root()).resolve()
        self.host = host
        self.port = port
        self.static_root = Path(__file__).with_name("web")
        self.library_audit_root = (library_audit_root or default_library_audit_root()).resolve()
        self.library_media_root = (library_media_root or self.library_audit_root.parent).resolve()
        self._audit_media_tokens: dict[str, Path] = {}
        self._audit_media_lock = threading.Lock()
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

    @staticmethod
    def _path_is_within(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except (OSError, ValueError):
            return False

    def _safe_library_path(self, value: str | Path) -> Path:
        path = Path(value).expanduser().resolve()
        if not self._path_is_within(path, self.library_media_root):
            raise ValueError("Artefacto de Biblioteca fuera de la raiz permitida.")
        return path

    def _register_audit_media(self, value: str | Path) -> str:
        path = self._safe_library_path(value)
        if path.suffix.lower() not in AUDIT_MEDIA_EXTENSIONS:
            raise ValueError("Tipo de media no permitido para la auditoria de Biblioteca.")
        if not path.is_file():
            raise FileNotFoundError("No existe la evidencia visual solicitada.")
        stat = path.stat()
        token_source = f"{path}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")
        token = hashlib.sha256(token_source).hexdigest()
        with self._audit_media_lock:
            self._audit_media_tokens[token] = path
        return token

    def _audit_media_path(self, token: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", token):
            raise FileNotFoundError("Token de evidencia visual no valido.")
        with self._audit_media_lock:
            path = self._audit_media_tokens.get(token)
        if path is None or not path.is_file() or not self._path_is_within(path, self.library_media_root):
            raise FileNotFoundError("Evidencia visual no registrada o expirada.")
        return path

    @staticmethod
    def _scope_key(scope: dict[str, Any]) -> tuple[int | None, int | None, str]:
        book_id = scope.get("book_id")
        instance_id = scope.get("instance_id")
        return (
            int(book_id) if book_id is not None else None,
            int(instance_id) if instance_id is not None else None,
            str(scope.get("exercise_set_id") or ""),
        )

    @staticmethod
    def _integer_pages(value: Any) -> list[int]:
        if not isinstance(value, list):
            return []
        pages: set[int] = set()
        for item in value:
            try:
                page = int(item)
            except (TypeError, ValueError):
                continue
            if page > 0:
                pages.add(page)
        return sorted(pages)

    @staticmethod
    def _public_evidence(value: Any) -> Any:
        """Drop private filesystem references from otherwise public audit evidence."""
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if key_text.endswith("_path") or key_text in {"pdf_path", "source_page_ref", "image_asset_key"}:
                    continue
                result[key_text] = ProblemDetectorLabServer._public_evidence(item)
            return result
        if isinstance(value, list):
            return [ProblemDetectorLabServer._public_evidence(item) for item in value]
        if isinstance(value, str) and (re.search(r"[A-Za-z]:[\\/]", value) or value.startswith(("/", "\\\\"))):
            return "private_reference_omitted"
        return value

    @staticmethod
    def _visual_session_fingerprint(payload: dict[str, Any]) -> str:
        canonical = {key: value for key, value in payload.items() if key != "session_fingerprint"}
        encoded = json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _visual_selection_pages(selection: Any) -> list[int]:
        if not isinstance(selection, dict):
            return []
        value = selection.get("pages")
        if not isinstance(value, list):
            value = selection.get("selected_pages")
        return ProblemDetectorLabServer._integer_pages(value)

    def _visual_session_paths(self) -> list[Path]:
        root = self.library_audit_root
        if not root.is_dir():
            return []
        paths: list[Path] = []
        for path in root.rglob("session.json"):
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if path.parent.parent.name != "visual_audit_sessions":
                continue
            if self._path_is_within(resolved, root):
                paths.append(resolved)
        return sorted(paths, key=lambda item: (str(item.parent.parent.parent), str(item.parent)))

    @staticmethod
    def _visual_map_units(map_data: dict[str, Any], kind: str) -> list[dict[str, Any]]:
        return [
            unit
            for unit in map_data.get("provisional_units", [])
            if isinstance(unit, dict) and str(unit.get("unit_kind") or "") == kind
        ] if isinstance(map_data.get("provisional_units"), list) else []

    def _visual_structural_rows(
        self,
        map_data: dict[str, Any],
        blockers: list[str],
    ) -> tuple[dict[int, dict[str, Any]], Path | None]:
        manifest_ref = map_data.get("page_role_manifest_ref")
        if not isinstance(manifest_ref, dict):
            blockers.append("page_role_manifest_ref_missing")
            return {}, None
        ledger_value = manifest_ref.get("artifact_path")
        if not ledger_value:
            blockers.append("structural_ledger_reference_missing")
            return {}, None
        try:
            ledger_path = self._safe_library_path(str(ledger_value))
        except (OSError, ValueError):
            blockers.append("structural_ledger_outside_allowed_root")
            return {}, None
        if not ledger_path.is_file() or ledger_path.suffix.lower() not in {".jsonl", ".ndjson"}:
            blockers.append("structural_ledger_missing")
            return {}, ledger_path
        rows: dict[int, dict[str, Any]] = {}
        try:
            for raw_line in ledger_path.read_text(encoding="utf-8").splitlines():
                if not raw_line.strip():
                    continue
                row = json.loads(raw_line)
                if not isinstance(row, dict) or row.get("page_number") is None:
                    continue
                page_number = int(row["page_number"])
                if page_number in rows:
                    blockers.append(f"structural_page_duplicate:{page_number}")
                rows[page_number] = row
        except (OSError, ValueError, json.JSONDecodeError):
            blockers.append("structural_ledger_invalid")
            return {}, ledger_path
        return rows, ledger_path

    def _visual_session_validation(self, session_path: Path) -> dict[str, Any]:
        blockers: list[str] = []

        def block(code: str) -> None:
            if code not in blockers:
                blockers.append(code)

        manifest = read_json(session_path, {})
        if not isinstance(manifest, dict):
            manifest = {}
        mapping_root = session_path.parent.parent.parent.resolve()
        if not self._path_is_within(mapping_root, self.library_audit_root):
            block("session_outside_allowed_root")
        session_id = str(manifest.get("session_id") or "")
        if not session_id or session_id != session_path.parent.name:
            block("session_id_path_mismatch")
        if manifest.get("schema_version") != VISUAL_AUDIT_SESSION_SCHEMA:
            block("session_schema_mismatch")
        if manifest.get("stage") != "pre_h_ps1":
            block("session_stage_mismatch")
        if manifest.get("status") != "ready_for_visual_audit":
            block("session_status_not_ready")
        expected_session_fingerprint = str(manifest.get("session_fingerprint") or "")
        live_session_fingerprint = self._visual_session_fingerprint(manifest)
        if expected_session_fingerprint != live_session_fingerprint:
            block("session_fingerprint_mismatch")

        bundle_path = mapping_root / "bundle_manifest.json"
        bundle = read_json(bundle_path, {})
        if not isinstance(bundle, dict) or bundle.get("schema_version") != "gottfried_mapping_v2_bundle_manifest_v1":
            bundle = {}
            block("mapping_bundle_manifest_missing_or_invalid")
        if str(bundle.get("batch_id") or "") != str(manifest.get("batch_id") or ""):
            block("batch_id_mismatch")
        if str(bundle.get("status") or "") != "mapping_requires_human":
            block("mapping_bundle_status_not_pre_hps1")

        map_ref = manifest.get("map_ref") if isinstance(manifest.get("map_ref"), dict) else {}
        map_id = str(map_ref.get("map_id") or "")
        map_path: Path | None = None
        if not re.fullmatch(r"[A-Za-z0-9._-]+", map_id):
            block("map_id_invalid")
        else:
            candidate = (mapping_root / "maps" / f"{map_id}.json").resolve()
            if not self._path_is_within(candidate, mapping_root):
                block("map_path_outside_mapping_root")
            else:
                map_path = candidate
        map_data: dict[str, Any] = {}
        map_sha256_live = ""
        if map_path is None or not map_path.is_file():
            block("map_artifact_missing")
        else:
            loaded = read_json(map_path, {})
            if isinstance(loaded, dict):
                map_data = loaded
            else:
                block("map_artifact_invalid")
            map_sha256_live = self._sha256(map_path)
        if map_sha256_live != str(map_ref.get("map_sha256") or ""):
            block("map_sha256_mismatch")

        artifact_hashes_path = mapping_root / "artifact_hashes.json"
        artifact_hashes = read_json(artifact_hashes_path, {})
        ledger_map_sha256 = ""
        if isinstance(artifact_hashes, dict):
            expected_relative = f"maps/{map_id}.json"
            for row in artifact_hashes.get("artifacts", []) if isinstance(artifact_hashes.get("artifacts"), list) else []:
                if not isinstance(row, dict):
                    continue
                relative = str(row.get("path") or "").replace("\\", "/")
                if relative == expected_relative:
                    ledger_map_sha256 = str(row.get("sha256") or "")
                    break
        if not ledger_map_sha256:
            block("map_hash_ledger_entry_missing")
        elif ledger_map_sha256 != map_sha256_live:
            block("map_hash_ledger_mismatch")

        if map_data.get("schema_version") != "gottfried_problem_solution_map_v2":
            block("map_schema_mismatch")
        if str(map_data.get("map_id") or "") != map_id:
            block("map_id_mismatch")
        if map_data.get("map_revision") != map_ref.get("map_revision"):
            block("map_revision_mismatch")
        if str(map_data.get("status") or "") != "mapping_requires_human":
            block("map_status_not_pre_hps1")
        if map_data.get("scope") != manifest.get("scope"):
            block("scope_mismatch")
        if str(map_data.get("scope_fingerprint") or "") != str(map_ref.get("scope_fingerprint") or ""):
            block("scope_fingerprint_mismatch")
        if str(map_data.get("context_fingerprint") or "") != str(map_ref.get("context_fingerprint") or ""):
            block("context_fingerprint_mismatch")

        source_ref = manifest.get("source_ref") if isinstance(manifest.get("source_ref"), dict) else {}
        map_source = map_data.get("source") if isinstance(map_data.get("source"), dict) else {}
        if str(source_ref.get("pdf_sha256") or "") != str(map_source.get("pdf_sha256") or ""):
            block("source_pdf_sha256_mismatch")
        if source_ref.get("page_count") != map_source.get("page_count"):
            block("source_page_count_mismatch")

        problem_pages = self._visual_selection_pages(map_data.get("problem_page_selection"))
        solution_pages = self._visual_selection_pages(map_data.get("solution_page_selection"))
        map_pages = sorted(set(problem_pages) | set(solution_pages))
        session_pages = self._integer_pages(manifest.get("page_numbers"))
        if session_pages != map_pages:
            block("page_numbers_mismatch")

        problem_units = self._visual_map_units(map_data, "problem")
        solution_units = self._visual_map_units(map_data, "solution")
        problem_refs = [str(unit.get("provisional_unit_ref") or "") for unit in problem_units]
        solution_refs = [str(unit.get("provisional_unit_ref") or "") for unit in solution_units]
        relations = map_data.get("problem_solution_relations") if isinstance(map_data.get("problem_solution_relations"), list) else []
        relation_ids = [str(row.get("relation_id") or "") for row in relations if isinstance(row, dict)]
        if manifest.get("problem_provisional_unit_refs") != problem_refs:
            block("problem_provisional_unit_refs_mismatch")
        if manifest.get("solution_provisional_unit_refs") != solution_refs:
            block("solution_provisional_unit_refs_mismatch")
        if manifest.get("relation_ids") != relation_ids:
            block("relation_ids_mismatch")
        expected_counts = {
            "pages": len(map_pages),
            "problems": len(problem_refs),
            "solutions": len(solution_refs),
            "relations": len(relation_ids),
        }
        if manifest.get("counts") != expected_counts:
            block("counts_mismatch")

        gates = manifest.get("gates") if isinstance(manifest.get("gates"), dict) else {}
        if gates != {"h_ps1": "pending", "activate_ingrid": False, "handoff_ready": False}:
            block("session_gate_authority_mismatch")
        permissions = manifest.get("permissions") if isinstance(manifest.get("permissions"), dict) else {}
        if permissions != VISUAL_AUDIT_SAFE_PERMISSIONS:
            block("session_permissions_not_read_only")
        review = manifest.get("review") if isinstance(manifest.get("review"), dict) else {}
        if str(review.get("status") or "") != "pending":
            block("session_review_not_pending")
        map_gates = map_data.get("gates") if isinstance(map_data.get("gates"), dict) else {}
        if str(map_gates.get("h_ps1") or "") != "pending" or bool(map_gates.get("activate_ingrid")) or bool(map_gates.get("handoff_ready")):
            block("map_gate_authority_mismatch")
        mutations = map_data.get("mutations") if isinstance(map_data.get("mutations"), dict) else {}
        for key in (
            "app_writes", "api_writes", "db_writes", "dataset_writes", "pdf_mutations",
            "ingrid_activations", "boxes_created", "crops_created",
        ):
            if int(mutations.get(key) or 0) != 0:
                block(f"forbidden_map_mutation:{key}")

        bundle_map = next(
            (
                row
                for row in bundle.get("maps", [])
                if isinstance(row, dict) and str(row.get("map_id") or "") == map_id
            ),
            None,
        ) if isinstance(bundle.get("maps"), list) else None
        if bundle_map is None:
            block("map_missing_from_bundle_manifest")
        else:
            if bundle_map.get("map_revision") != map_data.get("map_revision"):
                block("bundle_map_revision_mismatch")
            if self._integer_pages(bundle_map.get("approved_pages")) != map_pages:
                block("bundle_approved_pages_mismatch")

        structural_rows, structural_ledger_path = self._visual_structural_rows(map_data, blockers)
        snapshots = map_data.get("page_role_snapshot") if isinstance(map_data.get("page_role_snapshot"), list) else []
        snapshot_by_page = {
            int(row["page_number"]): row
            for row in snapshots
            if isinstance(row, dict) and row.get("page_number") is not None
        }
        if sorted(snapshot_by_page) != map_pages:
            block("page_role_snapshot_pages_mismatch")
        for page_number in map_pages:
            structural = structural_rows.get(page_number)
            snapshot = snapshot_by_page.get(page_number)
            if structural is None:
                block(f"structural_page_missing:{page_number}")
                continue
            if snapshot is None:
                continue
            if snapshot.get("content_roles") != structural.get("content_roles"):
                block(f"content_roles_mismatch:{page_number}")
            raw_structural_roles = structural.get("audit_roles")
            structural_roles = raw_structural_roles.get("roles", []) if isinstance(raw_structural_roles, dict) else raw_structural_roles
            if snapshot.get("audit_roles") != structural_roles:
                block(f"audit_roles_mismatch:{page_number}")
            section_ref = snapshot.get("page_sections_ref") if isinstance(snapshot.get("page_sections_ref"), dict) else {}
            expected_section_ids = [
                str(section.get("section_id") or "")
                for section in structural.get("page_sections", [])
                if isinstance(section, dict)
            ] if isinstance(structural.get("page_sections"), list) else []
            if section_ref.get("section_ids") != expected_section_ids:
                block(f"page_sections_mismatch:{page_number}")
            evidence = structural.get("evidence") if isinstance(structural.get("evidence"), dict) else {}
            image_value = evidence.get("image_asset_key")
            if not image_value:
                block(f"page_image_reference_missing:{page_number}")
                continue
            try:
                image_path = self._safe_library_path(str(image_value))
                if not image_path.is_file() or image_path.suffix.lower() not in AUDIT_MEDIA_EXTENSIONS:
                    block(f"page_image_missing:{page_number}")
            except (OSError, ValueError):
                block(f"page_image_outside_allowed_root:{page_number}")

        return {
            "session_path": session_path,
            "mapping_root": mapping_root,
            "manifest": manifest,
            "bundle": bundle,
            "map_path": map_path,
            "map": map_data,
            "map_sha256_live": map_sha256_live,
            "artifact_hashes_path": artifact_hashes_path if artifact_hashes_path.is_file() else None,
            "bundle_path": bundle_path if bundle_path.is_file() else None,
            "structural_ledger_path": structural_ledger_path,
            "structural_rows": structural_rows,
            "problem_units": problem_units,
            "solution_units": solution_units,
            "relations": [row for row in relations if isinstance(row, dict)],
            "problem_pages": problem_pages,
            "solution_pages": solution_pages,
            "page_numbers": map_pages,
            "blockers": blockers,
            "status": "visual_audit_blocked" if blockers else "ready_for_visual_audit",
            "session_fingerprint_live": live_session_fingerprint,
        }

    def _visual_session_index(self) -> list[dict[str, Any]]:
        records = [self._visual_session_validation(path) for path in self._visual_session_paths()]
        counts = Counter(str(record["manifest"].get("session_id") or "") for record in records)
        for record in records:
            session_id = str(record["manifest"].get("session_id") or "")
            if session_id and counts[session_id] > 1:
                if "duplicate_session_id" not in record["blockers"]:
                    record["blockers"].append("duplicate_session_id")
                record["status"] = "visual_audit_blocked"
        return records

    def _library_visual_audit_catalog_payload(self) -> dict[str, Any]:
        sessions: list[dict[str, Any]] = []
        for record in self._visual_session_index():
            manifest = record["manifest"]
            scope = manifest.get("scope") if isinstance(manifest.get("scope"), dict) else {}
            map_ref = manifest.get("map_ref") if isinstance(manifest.get("map_ref"), dict) else {}
            source_ref = manifest.get("source_ref") if isinstance(manifest.get("source_ref"), dict) else {}
            counts = manifest.get("counts") if isinstance(manifest.get("counts"), dict) else {}
            review = manifest.get("review") if isinstance(manifest.get("review"), dict) else {}
            sessions.append(
                {
                    "session_id": str(manifest.get("session_id") or record["session_path"].parent.name),
                    "batch_id": str(manifest.get("batch_id") or ""),
                    "status": record["status"],
                    "review_status": str(review.get("status") or "pending"),
                    "scope": self._public_evidence(scope),
                    "map_id": str(map_ref.get("map_id") or ""),
                    "map_revision": map_ref.get("map_revision"),
                    "map_sha256": str(map_ref.get("map_sha256") or ""),
                    "pdf_sha256": str(source_ref.get("pdf_sha256") or ""),
                    "page_count": int(counts.get("pages") or 0),
                    "problem_unit_count": int(counts.get("problems") or 0),
                    "solution_unit_count": int(counts.get("solutions") or 0),
                    "relation_count": int(counts.get("relations") or 0),
                    "blockers": list(record["blockers"]),
                }
            )
        sessions.sort(key=lambda row: (str(row["batch_id"]), str(row["scope"].get("book_code") or ""), str(row["session_id"])))
        ready = [row for row in sessions if row["status"] == "ready_for_visual_audit"]
        return {
            "schema_version": "problem_detector_visual_audit_catalog_v1",
            "read_only": True,
            "canonical_writes": "disabled",
            "stage": "pre_h_ps1",
            "summary": {
                "session_count": len(sessions),
                "ready_count": len(ready),
                "blocked_count": len(sessions) - len(ready),
                "page_count": sum(int(row["page_count"]) for row in sessions),
                "relation_count": sum(int(row["relation_count"]) for row in sessions),
            },
            "contract": VISUAL_AUDIT_SESSION_SCHEMA,
            "sessions": sessions,
        }

    def _library_visual_audit_session_payload(self, session_id: str) -> dict[str, Any]:
        records = [
            record
            for record in self._visual_session_index()
            if str(record["manifest"].get("session_id") or record["session_path"].parent.name) == session_id
        ]
        if not records:
            raise FileNotFoundError("Sesion visual pre-H-PS1 no encontrada.")
        record = records[0]
        manifest = record["manifest"]
        map_data = record["map"]
        map_ref = manifest.get("map_ref") if isinstance(manifest.get("map_ref"), dict) else {}
        source_ref = manifest.get("source_ref") if isinstance(manifest.get("source_ref"), dict) else {}
        review = manifest.get("review") if isinstance(manifest.get("review"), dict) else {}
        integrity = {
            "status": "passed" if record["status"] == "ready_for_visual_audit" else "failed",
            "blockers": list(record["blockers"]),
            "map_sha256_live": record["map_sha256_live"],
            "session_fingerprint_live": record["session_fingerprint_live"],
            "artifact_hashes_sha256": self._sha256(record["artifact_hashes_path"]) if record["artifact_hashes_path"] else "",
            "bundle_manifest_sha256": self._sha256(record["bundle_path"]) if record["bundle_path"] else "",
            "structural_ledger_sha256": self._sha256(record["structural_ledger_path"]) if record["structural_ledger_path"] else "",
        }
        base = {
            "schema_version": "problem_detector_visual_audit_session_payload_v1",
            "read_only": True,
            "canonical_writes": "disabled",
            "stage": "pre_h_ps1",
            "session": {
                "session_id": str(manifest.get("session_id") or session_id),
                "batch_id": str(manifest.get("batch_id") or ""),
                "status": record["status"],
                "created_by": self._public_evidence(manifest.get("created_by") or {}),
                "review_status": str(review.get("status") or "pending"),
                "predecessor_session_id": review.get("predecessor_session_id"),
                "session_fingerprint": str(manifest.get("session_fingerprint") or ""),
            },
            "scope": self._public_evidence(manifest.get("scope") or {}),
            "source": {
                "pdf_sha256": str(source_ref.get("pdf_sha256") or ""),
                "page_count": int(source_ref.get("page_count") or 0),
            },
            "map": {
                "schema_version": str(map_data.get("schema_version") or ""),
                "map_id": str(map_ref.get("map_id") or ""),
                "map_revision": map_ref.get("map_revision"),
                "map_sha256": str(map_ref.get("map_sha256") or ""),
                "status": str(map_data.get("status") or ""),
                "review_status": str(map_data.get("review_status") or "pending"),
                "scope_fingerprint": str(map_ref.get("scope_fingerprint") or ""),
                "context_fingerprint": str(map_ref.get("context_fingerprint") or ""),
            },
            "gates": {"h_ps1": "pending", "activate_ingrid": False, "handoff_ready": False},
            "permissions": dict(VISUAL_AUDIT_SAFE_PERMISSIONS),
            "integrity": integrity,
            "uncertainties": self._public_evidence(map_data.get("uncertainties") or []),
            "evidence": self._public_evidence(map_data.get("evidence") or []),
            "human_decisions_required": self._public_evidence(map_data.get("human_decisions_required") or []),
            "pages": [],
            "provisional_units": {"problems": [], "solutions": []},
            "relations": [],
        }
        if record["status"] != "ready_for_visual_audit":
            return base

        problem_refs = {
            str(unit.get("provisional_unit_ref") or ""): unit for unit in record["problem_units"]
        }
        solution_refs = {
            str(unit.get("provisional_unit_ref") or ""): unit for unit in record["solution_units"]
        }
        units_by_page: dict[int, list[str]] = {}
        for unit in [*record["problem_units"], *record["solution_units"]]:
            unit_ref = str(unit.get("provisional_unit_ref") or "")
            for page_number in self._integer_pages(unit.get("source_pages")):
                units_by_page.setdefault(page_number, []).append(unit_ref)

        for page_number in record["page_numbers"]:
            structural = record["structural_rows"][page_number]
            content_roles = [str(role) for role in structural.get("content_roles", []) if isinstance(role, str)]
            audit_roles = self._explicit_or_derived_audit_roles(structural, content_roles)
            evidence = structural.get("evidence") if isinstance(structural.get("evidence"), dict) else {}
            image_path = self._safe_library_path(str(evidence["image_asset_key"]))
            token = self._register_audit_media(image_path)
            image_width, image_height = image_size(image_path)
            raw_statistics = structural.get("page_statistics")
            page_statistics = self._public_evidence(raw_statistics) if isinstance(raw_statistics, dict) else None
            if isinstance(page_statistics, dict):
                page_statistics["canonical"] = False
                page_statistics["source"] = "gottfried"
            base["pages"].append(
                {
                    "page_number": page_number,
                    "structural_schema_version": str(structural.get("schema_version") or ""),
                    "content_roles": content_roles,
                    "audit_roles": audit_roles,
                    "page_sections": self._normalise_page_sections(structural),
                    "page_statistics": page_statistics,
                    "confidence": structural.get("confidence"),
                    "evidence": self._public_evidence(evidence),
                    "uncertainty_reasons": self._public_evidence(structural.get("uncertainty_reasons") or []),
                    "image_url": f"/api/library-audit/media?token={token}",
                    "image_width": image_width,
                    "image_height": image_height,
                    "precise_boxes": [],
                    "regions_are_final": False,
                    "authorization": {
                        "problem": page_number in record["problem_pages"],
                        "solution": page_number in record["solution_pages"],
                    },
                    "provisional_unit_refs": units_by_page.get(page_number, []),
                }
            )

        public_problems = [self._public_evidence(unit) for unit in record["problem_units"]]
        public_solutions = [self._public_evidence(unit) for unit in record["solution_units"]]
        base["provisional_units"] = {"problems": public_problems, "solutions": public_solutions}
        global_uncertainties = map_data.get("uncertainties") if isinstance(map_data.get("uncertainties"), list) else []
        for relation in record["relations"]:
            problem = problem_refs.get(str(relation.get("problem_provisional_unit_ref") or ""), {})
            solution = solution_refs.get(str(relation.get("solution_provisional_unit_ref") or ""), {})
            related_pages = set(self._integer_pages(problem.get("source_pages"))) | set(self._integer_pages(solution.get("source_pages")))
            relation_uncertainties = [
                item
                for item in global_uncertainties
                if not isinstance(item, dict)
                or not self._integer_pages(item.get("pages"))
                or related_pages.intersection(self._integer_pages(item.get("pages")))
            ]
            public_relation = self._public_evidence(relation)
            base["relations"].append(
                {
                    **public_relation,
                    "problem": self._public_evidence(problem),
                    "solution": self._public_evidence(solution),
                    "problem_page_numbers": self._integer_pages(problem.get("source_pages")),
                    "solution_page_numbers": self._integer_pages(solution.get("source_pages")),
                    "problem_section_ids": [str(value) for value in problem.get("source_section_ids", [])],
                    "solution_section_ids": [str(value) for value in solution.get("source_section_ids", [])],
                    "uncertainties": self._public_evidence(relation_uncertainties),
                    "visual_review_state": "pending",
                }
            )
        return base

    def _audit_campaign_root(self) -> Path:
        root = self.library_audit_root
        if (root / "solution_eligibility_manifest.json").is_file():
            return root
        if not root.is_dir():
            raise FileNotFoundError("No existe el staging de auditoria de Biblioteca.")
        candidates = [
            path
            for path in root.iterdir()
            if path.is_dir() and (path / "solution_eligibility_manifest.json").is_file()
        ]
        if not candidates:
            raise FileNotFoundError("No se encontro una campana de problemas y soluciones para auditar.")
        return max(candidates, key=lambda path: path.stat().st_mtime).resolve()

    @staticmethod
    def _audit_activation_root(campaign: Path) -> Path:
        candidates = [
            path
            for path in campaign.glob("h_ps1_ingrid_activation*")
            if path.is_dir() and (path / "assignments").is_dir()
        ]
        if not candidates:
            raise FileNotFoundError("La campana no contiene una activacion H-PS1 para Ingrid.")
        return max(candidates, key=lambda path: path.stat().st_mtime).resolve()

    def _audit_artifact_index(self) -> dict[str, Any]:
        campaign = self._audit_campaign_root()
        activation = self._audit_activation_root(campaign)
        eligibility_manifest = read_json(campaign / "solution_eligibility_manifest.json", {})
        eligibility_rows = eligibility_manifest.get("rows", []) if isinstance(eligibility_manifest, dict) else []
        eligibility_by_book = {
            int(row["app_book_id"]): row
            for row in eligibility_rows
            if isinstance(row, dict) and row.get("app_book_id") is not None
        }

        assignments: dict[str, dict[str, Any]] = {}
        for path in sorted((activation / "assignments").glob("*.json")):
            data = read_json(path, {})
            if not isinstance(data, dict) or not data.get("assignment_id"):
                continue
            assignment_id = str(data["assignment_id"])
            assignments[assignment_id] = {"path": path.resolve(), "data": data}

        maps_by_scope: dict[tuple[int | None, int | None, str], dict[str, Any]] = {}
        maps_dir = activation / "maps"
        if maps_dir.is_dir():
            for path in sorted(maps_dir.glob("*.json")):
                data = read_json(path, {})
                if not isinstance(data, dict):
                    continue
                maps_by_scope[self._scope_key(data.get("scope") or {})] = {"path": path.resolve(), "data": data}

        segmentations: dict[str, dict[str, Any]] = {}
        outputs_root = activation / "ingrid_outputs"
        if outputs_root.is_dir():
            for path in outputs_root.rglob("segmentation.json"):
                data = read_json(path, {})
                if not isinstance(data, dict) or not data.get("assignment_id"):
                    continue
                assignment_id = str(data["assignment_id"])
                previous = segmentations.get(assignment_id)
                if previous is None or path.stat().st_mtime > previous["path"].stat().st_mtime:
                    segmentations[assignment_id] = {"path": path.resolve(), "data": data}

        return {
            "campaign": campaign,
            "activation": activation,
            "manifest": eligibility_manifest if isinstance(eligibility_manifest, dict) else {},
            "eligibility_by_book": eligibility_by_book,
            "assignments": assignments,
            "maps_by_scope": maps_by_scope,
            "segmentations": segmentations,
        }

    @staticmethod
    def _legacy_eligibility_status(row: dict[str, Any]) -> str:
        explicit = str(row.get("status") or "")
        if explicit in {"eligible_full", "eligible_partial", "pending_review", "not_eligible"}:
            return explicit
        legacy = str(row.get("eligibility") or "")
        evidence = row.get("solution_evidence") if isinstance(row.get("solution_evidence"), dict) else {}
        if legacy == "eligible" and int(evidence.get("worked_solution") or 0) > 0:
            return "eligible_full"
        if legacy == "eligible" and any(int(evidence.get(key) or 0) > 0 for key in ("short_answer", "hint", "answer_key")):
            return "eligible_partial"
        if legacy in {"not_eligible", "ineligible"}:
            return "not_eligible"
        return "pending_review"

    def _eligibility_payload(
        self,
        row: dict[str, Any],
        map_data: dict[str, Any],
        assignment: dict[str, Any],
    ) -> dict[str, Any]:
        status = self._legacy_eligibility_status(row)
        explicit_v2 = str(row.get("schema_version") or "").startswith("gottfried_map_eligibility_v1") or str(row.get("status") or "") in {
            "eligible_full",
            "eligible_partial",
            "pending_review",
            "not_eligible",
        }
        h_ps1 = assignment.get("h_ps1_gate_ref") if isinstance(assignment.get("h_ps1_gate_ref"), dict) else {}
        map_exists = bool(map_data)
        can_generate = row.get("can_generate_map")
        if can_generate not in (True, False, "unknown"):
            can_generate = True if status in {"eligible_full", "eligible_partial"} else False if status == "not_eligible" else "unknown"
        return {
            "schema_version": str(row.get("schema_version") or "legacy_solution_eligibility_adapter_v1"),
            "contract_source": "explicit" if explicit_v2 else "legacy_adapter",
            "status": status,
            "confidence": row.get("confidence"),
            "reason": str(row.get("reason") or row.get("eligibility_basis") or ""),
            "reason_code": str(row.get("reason_code") or ("worked_solutions_detected" if status == "eligible_full" else "")),
            "evidence": self._public_evidence(row.get("evidence") or row.get("solution_evidence") or {}),
            "priority": str(row.get("priority") or "normal"),
            "can_generate_map": can_generate,
            "should_generate_now": bool(row.get("should_generate_now", map_exists)),
            "generate_map": bool(assignment.get("generate_map", map_exists)),
            "activate_ingrid": bool(h_ps1.get("status") == "approved" and assignment.get("assignment_id")),
        }

    def _library_audit_catalog_payload(self) -> dict[str, Any]:
        index = self._audit_artifact_index()
        instances: list[dict[str, Any]] = []
        for assignment_id, item in index["assignments"].items():
            assignment = item["data"]
            scope = assignment.get("scope") if isinstance(assignment.get("scope"), dict) else {}
            map_item = index["maps_by_scope"].get(self._scope_key(scope), {})
            map_data = map_item.get("data", {}) if isinstance(map_item, dict) else {}
            segmentation_item = index["segmentations"].get(assignment_id, {})
            segmentation = segmentation_item.get("data", {}) if isinstance(segmentation_item, dict) else {}
            book_id = int(scope.get("book_id")) if scope.get("book_id") is not None else None
            eligibility = index["eligibility_by_book"].get(book_id, {}) if book_id is not None else {}
            h_ps1 = assignment.get("h_ps1_gate_ref") if isinstance(assignment.get("h_ps1_gate_ref"), dict) else {}
            issues = segmentation.get("issues_found") if isinstance(segmentation.get("issues_found"), list) else []
            human_review = str(segmentation.get("human_review") or "")
            instances.append(
                {
                    "assignment_id": assignment_id,
                    "title": str(eligibility.get("title") or scope.get("book_code") or "Libro sin titulo"),
                    "book_code": str(scope.get("book_code") or ""),
                    "book_id": book_id,
                    "instance_type": str(scope.get("instance_type") or ""),
                    "instance_id": scope.get("instance_id"),
                    "exercise_set_id": str(scope.get("exercise_set_id") or ""),
                    "page_count": int((assignment.get("source_document") or {}).get("page_count") or 0),
                    "approved_page_count": len(self._integer_pages(assignment.get("approved_pages"))),
                    "problem_page_count": len(self._integer_pages(assignment.get("problem_pages"))),
                    "solution_page_count": len(self._integer_pages(assignment.get("solution_pages"))),
                    "map_id": str(map_data.get("map_id") or (assignment.get("structure_snapshot") or {}).get("map_id") or ""),
                    "map_revision": map_data.get("map_revision", (assignment.get("structure_snapshot") or {}).get("map_revision")),
                    "map_status": str(map_data.get("status") or (assignment.get("structure_snapshot") or {}).get("map_status") or "not_available"),
                    "h_ps1_status": str(h_ps1.get("status") or "not_available"),
                    "ingrid_status": str(segmentation.get("status") or "not_started"),
                    "h_ps2_status": human_review if human_review else "not_started",
                    "issue_count": len(issues),
                    "eligibility_status": self._legacy_eligibility_status(eligibility),
                    "schema_versions": {
                        "assignment": str(assignment.get("schema_version") or ""),
                        "map": str(map_data.get("schema_version") or ""),
                        "segmentation": str(segmentation.get("schema_version") or ""),
                    },
                }
            )
        instances.sort(key=lambda row: (str(row["title"]).lower(), str(row["instance_type"]), int(row["instance_id"] or 0)))
        return {
            "schema_version": "problem_detector_library_audit_catalog_v1",
            "read_only": True,
            "canonical_writes": "disabled",
            "campaign_id": str(index["manifest"].get("campaign_id") or index["campaign"].name),
            "summary": {
                "assignment_count": len(instances),
                "ingrid_ready_count": sum(1 for row in instances if row["ingrid_status"] != "not_started"),
                "h_ps2_pending_count": sum(1 for row in instances if row["h_ps2_status"] == "pending"),
                "issue_count": sum(int(row["issue_count"]) for row in instances),
            },
            "contract": {
                "structural_page": "book_page_structural_analysis_v2",
                "audit_role_mapping": "page_role_mapping_v1",
                "map": "gottfried_problem_solution_map_v2",
                "traceability": "ingrid_provisional_refinement_v1",
            },
            "instances": instances,
        }

    def _structural_rows_for_instance(
        self,
        eligibility: dict[str, Any],
        map_data: dict[str, Any],
    ) -> dict[int, dict[str, Any]]:
        rows: dict[int, dict[str, Any]] = {}
        reused = eligibility.get("reused_source") if isinstance(eligibility.get("reused_source"), dict) else {}
        ledger_value = reused.get("ledger_path") or eligibility.get("structural_ledger_path")
        if ledger_value:
            try:
                ledger_path = self._safe_library_path(str(ledger_value))
                if ledger_path.is_file() and ledger_path.suffix.lower() in {".jsonl", ".ndjson"}:
                    for raw_line in ledger_path.read_text(encoding="utf-8").splitlines():
                        if not raw_line.strip():
                            continue
                        row = json.loads(raw_line)
                        if isinstance(row, dict) and row.get("page_number") is not None:
                            rows[int(row["page_number"])] = row
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        snapshots = map_data.get("page_role_snapshot") if isinstance(map_data.get("page_role_snapshot"), list) else []
        for snapshot in snapshots:
            if not isinstance(snapshot, dict) or snapshot.get("page_number") is None:
                continue
            page_number = int(snapshot["page_number"])
            rows[page_number] = {**rows.get(page_number, {}), **snapshot}
        return rows

    @staticmethod
    def _explicit_or_derived_audit_roles(row: dict[str, Any], content_roles: list[str]) -> dict[str, Any]:
        explicit = row.get("audit_roles")
        if isinstance(explicit, dict):
            roles = [role for role in AUDIT_ROLE_ORDER if role in explicit.get("roles", [])]
            return {
                "schema_version": str(explicit.get("schema_version") or "library_page_audit_roles_v1"),
                "mapping_version": str(explicit.get("mapping_version") or "page_role_mapping_v1"),
                "roles": roles,
                "source": "explicit",
            }
        return audit_roles_for_content_roles(content_roles)

    def _normalise_page_sections(self, row: dict[str, Any]) -> list[dict[str, Any]]:
        sections: list[dict[str, Any]] = []
        for raw in row.get("page_sections", []) if isinstance(row.get("page_sections"), list) else []:
            if not isinstance(raw, dict):
                continue
            bbox = raw.get("bbox_norm_xyxy")
            if not isinstance(bbox, list) or len(bbox) != 4:
                continue
            try:
                x1, y1, x2, y2 = [float(value) for value in bbox]
            except (TypeError, ValueError):
                continue
            if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
                continue
            raw_audit_roles = raw.get("audit_roles")
            if isinstance(raw_audit_roles, dict):
                section_audit_roles = raw_audit_roles.get("roles", [])
            else:
                section_audit_roles = raw_audit_roles if isinstance(raw_audit_roles, list) else []
            sections.append(
                {
                    "section_id": str(raw.get("section_id") or f"section-{len(sections) + 1}"),
                    "geometry_kind": "coarse_rect",
                    "coordinate_space": "normalized_0_1",
                    "bbox_norm_xyxy": [x1, y1, x2, y2],
                    "precision": "coarse",
                    "content_roles": [str(role) for role in raw.get("content_roles", []) if isinstance(role, str)],
                    "audit_roles": [role for role in AUDIT_ROLE_ORDER if role in section_audit_roles],
                    "reading_order": raw.get("reading_order"),
                    "confidence": raw.get("confidence"),
                    "evidence": self._public_evidence(raw.get("evidence") or []),
                    "uncertainty_reasons": self._public_evidence(raw.get("uncertainty_reasons") or []),
                    "usable_as_final_box": False,
                }
            )
        return sections

    @staticmethod
    def _valid_precise_box(raw: dict[str, Any], *, default_role: str, default_id: str) -> dict[str, Any] | None:
        bbox = raw.get("bbox_xyxy")
        if not isinstance(bbox, list) or len(bbox) != 4:
            return None
        try:
            x1, y1, x2, y2 = [float(value) for value in bbox]
        except (TypeError, ValueError):
            return None
        if not (x1 < x2 and y1 < y2):
            return None
        return {
            "box_id": str(raw.get("box_id") or raw.get("fragment_id") or raw.get("region_id") or default_id),
            "role": str(raw.get("role") or raw.get("region_class") or default_role),
            "bbox_xyxy": [x1, y1, x2, y2],
            "precision": "precise",
            "source": "ingrid",
            "parent_box_id": raw.get("parent_box_id"),
            "annotation_unit_id": raw.get("annotation_unit_id"),
            "content_members": ProblemDetectorLabServer._public_evidence(raw.get("content_members") or {}),
            "geometry_quality": ProblemDetectorLabServer._public_evidence(raw.get("geometry_quality") or {}),
        }

    def _library_precision_validation(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate V2 precision evidence without persisting or promoting it."""

        annotation = payload.get("annotation") if isinstance(payload.get("annotation"), dict) else payload
        result = evaluate_precision_annotation(annotation if isinstance(annotation, dict) else {})
        public = self._public_evidence(result)
        return {
            **public,
            "read_only": True,
            "persisted": False,
            "canonical_writes": "disabled",
            "training_writes": "disabled",
        }

    def _resolve_audit_media_reference(self, reference: str, segmentation_path: Path) -> str:
        if not reference:
            return ""
        raw = Path(reference)
        candidates = [raw] if raw.is_absolute() else [segmentation_path.parent / raw, segmentation_path.parent.parent / raw]
        for candidate in candidates:
            try:
                path = self._safe_library_path(candidate)
                if path.is_file():
                    token = self._register_audit_media(path)
                    return f"/api/library-audit/media?token={token}"
            except (OSError, ValueError):
                continue
        return ""

    def _library_audit_instance_payload(self, assignment_id: str) -> dict[str, Any]:
        index = self._audit_artifact_index()
        assignment_item = index["assignments"].get(assignment_id)
        if assignment_item is None:
            raise FileNotFoundError("Asignacion de Ingrid no encontrada.")
        assignment = assignment_item["data"]
        scope = assignment.get("scope") if isinstance(assignment.get("scope"), dict) else {}
        map_item = index["maps_by_scope"].get(self._scope_key(scope), {})
        map_data = map_item.get("data", {}) if isinstance(map_item, dict) else {}
        segmentation_item = index["segmentations"].get(assignment_id, {})
        segmentation = segmentation_item.get("data", {}) if isinstance(segmentation_item, dict) else {}
        segmentation_path = segmentation_item.get("path") if isinstance(segmentation_item, dict) else None
        precision_annotation = segmentation.get("precision_annotation") if isinstance(segmentation.get("precision_annotation"), dict) else None
        if precision_annotation is not None:
            precision_validation = self._library_precision_validation({"annotation": precision_annotation})
        else:
            precision_validation = {
                "schema_version": "precision_annotation_validation_v1",
                "applicable": False,
                "valid": False,
                "h_ps2_ready": False,
                "issues": ["precision_annotation:missing"],
                "warnings": [],
                "unit_results": [],
                "summary": {
                    "unit_count": 0,
                    "region_count": 0,
                    "relation_count": 0,
                    "answer_block_count": 0,
                    "covered_alternative_count": 0,
                    "blocking_issue_count": 1,
                    "warning_count": 0,
                },
                "read_only": True,
                "persisted": False,
                "canonical_writes": "disabled",
                "training_writes": "disabled",
            }
        precision_regions_by_page: dict[int, list[dict[str, Any]]] = {}
        if precision_annotation is not None:
            for raw_region in precision_annotation.get("regions", []) if isinstance(precision_annotation.get("regions"), list) else []:
                if not isinstance(raw_region, dict):
                    continue
                try:
                    precision_page = int(raw_region.get("page_number") or 0)
                except (TypeError, ValueError):
                    continue
                if precision_page > 0:
                    precision_regions_by_page.setdefault(precision_page, []).append(raw_region)
        book_id = int(scope.get("book_id")) if scope.get("book_id") is not None else None
        eligibility = index["eligibility_by_book"].get(book_id, {}) if book_id is not None else {}
        structural_rows = self._structural_rows_for_instance(eligibility, map_data)

        problem_reviews_by_page: dict[int, list[dict[str, Any]]] = {}
        for review in segmentation.get("problem_box_reviews", []) if isinstance(segmentation.get("problem_box_reviews"), list) else []:
            if isinstance(review, dict) and review.get("page_number") is not None:
                problem_reviews_by_page.setdefault(int(review["page_number"]), []).append(review)

        solution_units_by_page: dict[int, list[dict[str, Any]]] = {}
        for unit in segmentation.get("solution_units", []) if isinstance(segmentation.get("solution_units"), list) else []:
            if not isinstance(unit, dict):
                continue
            for fragment in unit.get("fragments", []) if isinstance(unit.get("fragments"), list) else []:
                if isinstance(fragment, dict) and fragment.get("page_number") is not None:
                    solution_units_by_page.setdefault(int(fragment["page_number"]), []).append({"unit": unit, "fragment": fragment})

        inspection_by_page = {
            int(row["page_number"]): row
            for row in segmentation.get("inspection_log", []) if isinstance(segmentation.get("inspection_log"), list)
            if isinstance(row, dict) and row.get("page_number") is not None
        }
        overlays_by_page: dict[int, dict[str, str]] = {}
        if segmentation_path is not None:
            for reference in segmentation.get("evidence_overlays", []) if isinstance(segmentation.get("evidence_overlays"), list) else []:
                match = re.search(r"page_(\d+)_(before|after)\.(?:png|jpe?g|webp)$", str(reference), re.IGNORECASE)
                if match:
                    overlays_by_page.setdefault(int(match.group(1)), {})[match.group(2).lower()] = str(reference)

        approved_pages = self._integer_pages(assignment.get("approved_pages"))
        if not approved_pages:
            approved_pages = sorted(
                set(self._integer_pages(assignment.get("problem_pages")))
                | set(self._integer_pages(assignment.get("solution_pages")))
            )
        problem_pages = set(self._integer_pages(assignment.get("problem_pages")))
        solution_pages = set(self._integer_pages(assignment.get("solution_pages")))
        pages: list[dict[str, Any]] = []
        traceability_totals = Counter()

        for page_number in approved_pages:
            structural = structural_rows.get(page_number, {})
            content_roles = [str(role) for role in structural.get("content_roles", []) if isinstance(role, str)]
            audit_roles = self._explicit_or_derived_audit_roles(structural, content_roles)
            evidence = structural.get("evidence") if isinstance(structural.get("evidence"), dict) else {}
            image_url = ""
            image_width = 0
            image_height = 0
            image_value = evidence.get("image_asset_key")
            if image_value:
                try:
                    image_path = self._safe_library_path(str(image_value))
                    if image_path.is_file():
                        token = self._register_audit_media(image_path)
                        image_url = f"/api/library-audit/media?token={token}"
                        image_width, image_height = image_size(image_path)
                except (OSError, ValueError, RuntimeError):
                    pass

            precise_boxes: list[dict[str, Any]] = []
            source_provisional_ids: set[str] = set()
            relation_types: set[str] = set()
            overlay_after_url = ""
            overlay_before_url = ""
            page_status = "not_started"
            human_review = "not_started"
            for review in problem_reviews_by_page.get(page_number, []):
                image_width = int(review.get("image_width") or image_width)
                image_height = int(review.get("image_height") or image_height)
                page_status = str(review.get("status") or page_status)
                human_review = str(review.get("human_review") or human_review)
                for raw_box in review.get("proposed_boxes", []) if isinstance(review.get("proposed_boxes"), list) else []:
                    if not isinstance(raw_box, dict):
                        continue
                    box = self._valid_precise_box(raw_box, default_role="problem", default_id=f"p{page_number}-box-{len(precise_boxes) + 1}")
                    if box is not None:
                        precise_boxes.append(box)
                ids = review.get("source_provisional_unit_ids")
                if isinstance(ids, list):
                    source_provisional_ids.update(str(value) for value in ids if value)
                refinement = review.get("provisional_refinement") if isinstance(review.get("provisional_refinement"), dict) else {}
                if refinement.get("relation_type"):
                    relation_types.add(str(refinement["relation_type"]))
                if segmentation_path is not None:
                    overlay_before_url = self._resolve_audit_media_reference(str(review.get("overlay_before") or ""), segmentation_path) or overlay_before_url
                    overlay_after_url = self._resolve_audit_media_reference(str(review.get("overlay_after") or ""), segmentation_path) or overlay_after_url

            solution_unit_ids: set[str] = set()
            for entry in solution_units_by_page.get(page_number, []):
                unit = entry["unit"]
                fragment = entry["fragment"]
                unit_id = str(unit.get("unit_id") or "")
                if unit_id:
                    solution_unit_ids.add(unit_id)
                box = self._valid_precise_box(fragment, default_role="solution", default_id=f"p{page_number}-solution-{len(precise_boxes) + 1}")
                if box is not None:
                    box["unit_id"] = unit_id
                    box["continuation_complete"] = bool(unit.get("continuation_complete", True))
                    precise_boxes.append(box)
                ids = unit.get("source_provisional_unit_ids")
                if isinstance(ids, list):
                    source_provisional_ids.update(str(value) for value in ids if value)
                refinement = unit.get("provisional_refinement") if isinstance(unit.get("provisional_refinement"), dict) else {}
                if refinement.get("relation_type"):
                    relation_types.add(str(refinement["relation_type"]))

            for raw_region in precision_regions_by_page.get(page_number, []):
                precision_box = self._valid_precise_box(
                    raw_region,
                    default_role=str(raw_region.get("region_class") or "problem"),
                    default_id=f"p{page_number}-precision-{len(precise_boxes) + 1}",
                )
                if precision_box is not None:
                    precise_boxes.append(precision_box)

            if segmentation_path is not None:
                overlay_refs = overlays_by_page.get(page_number, {})
                overlay_before_url = self._resolve_audit_media_reference(overlay_refs.get("before", ""), segmentation_path) or overlay_before_url
                overlay_after_url = self._resolve_audit_media_reference(overlay_refs.get("after", ""), segmentation_path) or overlay_after_url
            if precise_boxes and page_status == "not_started":
                page_status = str(segmentation.get("status") or "agent_segmented_pending_human")
                human_review = str(segmentation.get("human_review") or "pending")

            problem_box_count = sum(1 for box in precise_boxes if box["role"] == "problem")
            number_box_count = sum(1 for box in precise_boxes if box["role"] == "problem_number")
            solution_fragment_count = sum(1 for box in precise_boxes if box["role"] == "solution")
            if source_provisional_ids:
                traceability_status = "linked_to_provisional_units"
            elif precise_boxes:
                traceability_status = "legacy_missing_provisional_links"
            else:
                traceability_status = "not_applicable"
            traceability_totals[traceability_status] += 1

            raw_statistics = structural.get("page_statistics")
            page_statistics = self._public_evidence(raw_statistics) if isinstance(raw_statistics, dict) and raw_statistics else None
            if isinstance(page_statistics, dict):
                page_statistics["canonical"] = False
                page_statistics["source"] = "gottfried"
            inspection = inspection_by_page.get(page_number, {})
            page_unit_results = [
                row
                for row in precision_validation.get("unit_results", [])
                if isinstance(row, dict) and page_number in self._integer_pages(row.get("source_pages"))
            ]
            page_answer_blocks = sum(
                1
                for region in precision_regions_by_page.get(page_number, [])
                if str(region.get("region_class") or "") == "answer_block"
            )
            page_precision_issues = sorted(
                {
                    str(issue)
                    for row in page_unit_results
                    for issue in row.get("issues", []) if isinstance(row.get("issues"), list)
                }
            )
            if precision_annotation is None:
                page_precision_issues = ["precision_annotation:missing"]
            page_precision = {
                "schema_version": str(precision_validation.get("schema_version") or "precision_annotation_validation_v1"),
                "applicable": bool(precision_validation.get("applicable")),
                "h_ps2_ready": bool(precision_validation.get("h_ps2_ready")) and bool(page_unit_results),
                "issues": page_precision_issues,
                "warnings": sorted(
                    {
                        str(warning)
                        for row in page_unit_results
                        for warning in row.get("warnings", []) if isinstance(row.get("warnings"), list)
                    }
                ),
                "unit_results": self._public_evidence(page_unit_results),
                "summary": {
                    "unit_count": len(page_unit_results),
                    "answer_block_count": page_answer_blocks,
                    "blocking_issue_count": len(page_precision_issues),
                },
                "read_only": True,
                "persisted": False,
            }
            pages.append(
                {
                    "page_number": page_number,
                    "structural_schema_version": str(structural.get("schema_version") or "not_available"),
                    "structural_status": "available" if structural else "not_available",
                    "content_roles": content_roles,
                    "audit_roles": audit_roles,
                    "page_sections": self._normalise_page_sections(structural),
                    "page_statistics": page_statistics,
                    "observed_counts": {
                        "source": "ingrid_precise_observation",
                        "canonical": False,
                        "problem_boxes": problem_box_count,
                        "problem_number_boxes": number_box_count,
                        "solution_units": len(solution_unit_ids),
                        "solution_fragments": solution_fragment_count,
                    },
                    "confidence": structural.get("confidence"),
                    "evidence": self._public_evidence(evidence),
                    "uncertainty_reasons": self._public_evidence(structural.get("uncertainty_reasons") or []),
                    "image_url": image_url,
                    "image_width": image_width,
                    "image_height": image_height,
                    "overlay_before_url": overlay_before_url,
                    "overlay_after_url": overlay_after_url,
                    "precise_boxes": precise_boxes,
                    "authorization": {
                        "problem": page_number in problem_pages,
                        "solution": page_number in solution_pages,
                    },
                    "inspection": self._public_evidence(inspection),
                    "ingrid_status": page_status,
                    "human_review": human_review,
                    "traceability": {
                        "status": traceability_status,
                        "source_provisional_unit_ids": sorted(source_provisional_ids),
                        "relation_types": sorted(relation_types),
                    },
                    "precision_validation": page_precision,
                }
            )

        h_ps1 = assignment.get("h_ps1_gate_ref") if isinstance(assignment.get("h_ps1_gate_ref"), dict) else {}
        issues = segmentation.get("issues_found") if isinstance(segmentation.get("issues_found"), list) else []
        provisional_units = map_data.get("provisional_units") if isinstance(map_data.get("provisional_units"), list) else []
        return {
            "schema_version": "problem_detector_library_audit_instance_v1",
            "read_only": True,
            "canonical_writes": "disabled",
            "assignment_id": assignment_id,
            "title": str(eligibility.get("title") or scope.get("book_code") or "Libro sin titulo"),
            "scope": {
                "book_code": str(scope.get("book_code") or ""),
                "book_id": book_id,
                "instance_type": str(scope.get("instance_type") or ""),
                "instance_id": scope.get("instance_id"),
                "exercise_set_id": str(scope.get("exercise_set_id") or ""),
            },
            "source": {
                "pdf_sha256": str((assignment.get("source_document") or {}).get("pdf_sha256") or ""),
                "page_count": int((assignment.get("source_document") or {}).get("page_count") or 0),
            },
            "map": {
                "schema_version": str(map_data.get("schema_version") or "not_available"),
                "map_id": str(map_data.get("map_id") or (assignment.get("structure_snapshot") or {}).get("map_id") or ""),
                "map_revision": map_data.get("map_revision", (assignment.get("structure_snapshot") or {}).get("map_revision")),
                "status": str(map_data.get("status") or (assignment.get("structure_snapshot") or {}).get("map_status") or "not_available"),
                "context_fingerprint": str(map_data.get("context_fingerprint") or assignment.get("context_fingerprint") or ""),
                "provisional_unit_count": len(provisional_units),
                "provisional_units": self._public_evidence(provisional_units),
            },
            "eligibility": self._eligibility_payload(eligibility, map_data, assignment),
            "gates": {
                "h_ps1": str(h_ps1.get("status") or "not_available"),
                "h_ps2": str(segmentation.get("human_review") or "not_started"),
                "next_gate": str(segmentation.get("next_gate") or "H-PS2"),
            },
            "ingrid": {
                "schema_version": str(segmentation.get("schema_version") or "not_available"),
                "status": str(segmentation.get("status") or "not_started"),
                "pages_inspected": len(self._integer_pages(segmentation.get("pages_inspected"))),
                "problem_review_count": len(problem_reviews_by_page),
                "solution_unit_count": len(segmentation.get("solution_units", [])) if isinstance(segmentation.get("solution_units"), list) else 0,
                "issue_count": len(issues),
                "issues": self._public_evidence(issues),
            },
            "traceability_summary": {
                "contract": "ingrid_provisional_refinement_v1",
                "linked_page_count": traceability_totals["linked_to_provisional_units"],
                "legacy_missing_page_count": traceability_totals["legacy_missing_provisional_links"],
                "not_applicable_page_count": traceability_totals["not_applicable"],
            },
            "precision_validation": precision_validation,
            "pages": pages,
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
            if path == "/api/library-audit":
                self._send_json(handler, self._library_audit_catalog_payload())
                return
            if path == "/api/library-audit/sessions":
                self._send_json(handler, self._library_visual_audit_catalog_payload())
                return
            if path == "/api/library-audit/session":
                session_id = str(query.get("id", [""])[0])
                self._send_json(handler, self._library_visual_audit_session_payload(session_id))
                return
            if path == "/api/library-audit/instance":
                assignment_id = str(query.get("id", [""])[0])
                self._send_json(handler, self._library_audit_instance_payload(assignment_id))
                return
            if path == "/api/library-audit/media":
                token = str(query.get("token", [""])[0])
                self._send_file(handler, self._audit_media_path(token))
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
            if parsed.path == "/api/library-audit/precision/validate":
                self._send_json(handler, self._library_precision_validation(payload))
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
