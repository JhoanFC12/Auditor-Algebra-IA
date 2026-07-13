from __future__ import annotations

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
DATASET_GLOB = "problem_detector_multiclass_100_lab_*"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def datasets_root() -> Path:
    return repo_root() / ".cache" / "transcriptor_runs" / "datasets"


def default_dataset_root() -> Path:
    explicit = os.getenv("PROBLEM_DETECTOR_LAB_DATASET", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    datasets = datasets_root()
    preferred = datasets / "problem_detector_multiclass_100_lab_20260624"
    candidates = [p for p in datasets.glob(DATASET_GLOB) if p.is_dir()]
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

    def _sample_ids(self) -> list[str]:
        return sorted(path.stem for path in self.images_dir.glob("*.png"))

    def _dataset_candidates(self) -> list[Path]:
        root = datasets_root()
        if not root.exists():
            return []
        candidates = [path.resolve() for path in root.glob(DATASET_GLOB) if path.is_dir()]
        return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)

    def _dataset_card(self, path: Path) -> dict[str, Any]:
        manifest = read_json(path / "manifest.json", {})
        images_dir = path / "images"
        metadata_dir = path / "metadata"
        samples_total = len(list(images_dir.glob("*.png"))) if images_dir.exists() else 0
        reviewed_total = 0
        if metadata_dir.exists():
            for metadata_path in metadata_dir.glob("*.json"):
                metadata = read_json(metadata_path, {})
                label_review = metadata.get("label_review") if isinstance(metadata, dict) else None
                if isinstance(label_review, dict) and label_review.get("reviewed_at"):
                    reviewed_total += 1
        return {
            "name": path.name,
            "path": str(path),
            "samples_total": samples_total,
            "reviewed_total": reviewed_total,
            "pending_total": max(0, samples_total - reviewed_total),
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
        if not candidate.name.startswith("problem_detector_multiclass_100_lab_"):
            raise ValueError("Dataset no permitido para Problem Detector Lab.")
        old_root = self.dataset_root
        self.dataset_root = candidate
        try:
            self._ensure_dataset()
        except Exception:
            self.dataset_root = old_root
            raise
        return self._dataset_summary()

    def _image_path(self, sample_id: str) -> Path:
        if sample_id not in set(self._sample_ids()):
            raise FileNotFoundError(f"Muestra no encontrada: {sample_id}")
        return self.images_dir / f"{sample_id}.png"

    def _label_path(self, sample_id: str) -> Path:
        return self.labels_dir / f"{sample_id}.txt"

    def _metadata_path(self, sample_id: str) -> Path:
        return self.metadata_dir / f"{sample_id}.json"

    def _dataset_summary(self) -> dict[str, Any]:
        manifest = read_json(self.dataset_root / "manifest.json", {})
        rows_by_id = {
            str(row.get("sample_id")): row
            for row in manifest.get("rows", [])
            if isinstance(row, dict) and row.get("sample_id")
        }
        samples: list[dict[str, Any]] = []
        reviewed = 0
        for sample_id in self._sample_ids():
            row = rows_by_id.get(sample_id, {})
            metadata = read_json(self._metadata_path(sample_id), {})
            label_review = metadata.get("label_review") if isinstance(metadata, dict) else None
            is_reviewed = bool(isinstance(label_review, dict) and label_review.get("reviewed_at"))
            if is_reviewed:
                reviewed += 1
            samples.append(
                {
                    "sample_id": sample_id,
                    "group": str(row.get("group") or metadata.get("group") or "sin_grupo"),
                    "instance": str(row.get("instance") or metadata.get("instance") or ""),
                    "page_number": int(row.get("page_number") or metadata.get("page_number") or 0),
                    "problem_boxes": int(row.get("problem_boxes") or 0),
                    "number_boxes": int(row.get("number_boxes") or 0),
                    "answer_blocks": int(row.get("answer_blocks") or 0),
                    "reviewed": is_reviewed,
                }
            )
        return {
            "dataset_root": str(self.dataset_root),
            "samples_total": len(samples),
            "reviewed_total": reviewed,
            "pending_total": len(samples) - reviewed,
            "class_map": CLASS_MAP,
            "manifest": {
                "schema_version": manifest.get("schema_version", ""),
                "selected_by_group": manifest.get("selected_by_group", {}),
                "problem_boxes_total": manifest.get("problem_boxes_total", 0),
                "number_boxes_total": manifest.get("number_boxes_total", 0),
                "answer_blocks_total": manifest.get("answer_blocks_total", 0),
            },
            "samples": samples,
        }

    def _sample_payload(self, sample_id: str) -> dict[str, Any]:
        image_path = self._image_path(sample_id)
        width, height = image_size(image_path)
        metadata = read_json(self._metadata_path(sample_id), {})
        boxes = read_yolo_boxes(self._label_path(sample_id), width, height)
        return {
            "sample_id": sample_id,
            "width": width,
            "height": height,
            "image_url": f"/api/image?id={urllib.parse.quote(sample_id)}&v={int(image_path.stat().st_mtime)}",
            "boxes": boxes,
            "metadata": metadata,
        }

    def _save_sample(self, payload: dict[str, Any]) -> dict[str, Any]:
        sample_id = str(payload.get("sample_id") or "").strip()
        boxes = payload.get("boxes")
        if not sample_id:
            raise ValueError("Falta sample_id.")
        if not isinstance(boxes, list):
            raise ValueError("boxes debe ser una lista.")
        image_path = self._image_path(sample_id)
        width, height = image_size(image_path)
        self._label_path(sample_id).write_text(boxes_to_yolo(boxes, width, height), encoding="utf-8")
        metadata_path = self._metadata_path(sample_id)
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
        return self._sample_payload(sample_id)

    def _export_dataset_yaml(self, payload: dict[str, Any]) -> dict[str, Any]:
        val_ratio = float(payload.get("val_ratio") or 0.2)
        val_ratio = clamp(val_ratio, 0.05, 0.5)
        seed = int(payload.get("seed") or 20260624)
        sample_ids = self._sample_ids()
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
                self._send_json(handler, self._sample_payload(sample_id))
                return
            if path == "/api/image":
                sample_id = str(query.get("id", [""])[0])
                self._send_file(handler, self._image_path(sample_id))
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
