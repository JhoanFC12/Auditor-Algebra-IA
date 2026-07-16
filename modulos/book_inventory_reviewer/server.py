from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import re
import threading
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = (
    ROOT
    / ".cache"
    / "book_catalog"
    / "repository_inventory"
    / "whatsapp_books_master_complete.csv"
)
DEFAULT_PRIORITY_CATALOG = (
    ROOT
    / ".cache"
    / "book_catalog"
    / "repository_inventory"
    / "curated_math_books"
    / "06_prioridad_opcion_multiple.csv"
)
DEFAULT_STATE = (
    ROOT
    / ".cache"
    / "book_catalog"
    / "repository_inventory"
    / "curated_math_books"
    / "review_decisions.json"
)
DEFAULT_EXPORT = ROOT / "output" / "book_inventory_by_course"
WEB_ROOT = Path(__file__).resolve().parent / "web"

COURSES = [
    "Aritmetica",
    "Algebra",
    "Geometria",
    "Trigonometria",
    "Razonamiento matematico",
    "Geometria analitica",
    "Geometria del espacio",
    "Fisica",
    "Quimica",
]
MATERIAL_TYPES = ["libro_problemas", "libro_mixto", "consulta", "practica", "solucionario", "otro"]
REVIEW_STATES = ["pendiente", "confirmado", "reasignado", "excluido"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def drive_id(url: str) -> str:
    match = re.search(r"/d/([^/?]+)", url or "")
    return match.group(1) if match else ""


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*]+", "_", value or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:160] or "sin_titulo"


@dataclass
class ReviewerStore:
    catalog_path: Path = DEFAULT_CATALOG
    state_path: Path = DEFAULT_STATE
    export_root: Path = DEFAULT_EXPORT

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._catalog_items = self._load_catalog()
        self._catalog_by_id = {item["id"]: item for item in self._catalog_items}

    def _load_catalog(self) -> list[dict]:
        priority_ids: set[str] = set()
        if DEFAULT_PRIORITY_CATALOG.exists():
            with DEFAULT_PRIORITY_CATALOG.open("r", encoding="utf-8-sig", newline="") as stream:
                priority_ids = {
                    row.get("drive_id") or drive_id(row.get("url", ""))
                    for row in csv.DictReader(stream)
                }
        with self.catalog_path.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        items = []
        for index, row in enumerate(rows, start=1):
            item = dict(row)
            item_id = item.get("drive_id") or drive_id(item.get("url", "")) or f"row-{index}"
            had_title = bool(item.get("title", "").strip())
            item["id"] = item_id
            item["original_course"] = item.get("course", "") or "Pendiente"
            item["title"] = item.get("title", "").strip() or f"PDF sin titulo - {item_id}"
            item["is_priority"] = item_id in priority_ids
            item["has_title"] = had_title
            item["preview_url"] = (
                f"https://drive.google.com/file/d/{item_id}/preview" if not item_id.startswith("row-") else item.get("url", "")
            )
            items.append(item)
        items.sort(key=lambda item: (not item["is_priority"], not item["has_title"], item["title"].casefold()))
        return items

    def _merged_items(self) -> list[dict]:
        decisions = self._state().get("decisions", {})
        items = []
        for row in self._catalog_items:
            decision = decisions.get(row["id"], {})
            merged = dict(row)
            merged.update(
                {
                    "review_state": decision.get("review_state", "pendiente"),
                    "confirmed_course": decision.get("confirmed_course", row.get("course") or "Pendiente"),
                    "material_type_review": decision.get("material_type", "libro_problemas"),
                    "multiple_choice": decision.get("multiple_choice", "por_verificar"),
                    "notes": decision.get("notes", ""),
                    "reviewed_at": decision.get("reviewed_at"),
                }
            )
            items.append(merged)
        return items

    def _state(self) -> dict:
        if not self.state_path.exists():
            return {"schema_version": "book_inventory_review_v1", "updated_at": None, "decisions": {}}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": "book_inventory_review_v1", "updated_at": None, "decisions": {}}

    def _write_state(self, state: dict) -> None:
        state["updated_at"] = utc_now()
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)

    def snapshot(self) -> dict:
        with self._lock:
            items = self._merged_items()
            counts = {
                state: sum(item["review_state"] == state for item in items)
                for state in REVIEW_STATES
            }
            return {
                "schema_version": "book_inventory_reviewer_api_v1",
                "items": items,
                "courses": COURSES,
                "material_types": MATERIAL_TYPES,
                "review_states": REVIEW_STATES,
                "counts": counts,
                "total": len(items),
                "state_path": str(self.state_path),
                "export_root": str(self.export_root),
            }

    def query_catalog(
        self,
        *,
        page: int = 1,
        page_size: int = 100,
        search: str = "",
        course: str = "todos",
        review_state: str = "pendiente",
    ) -> dict:
        with self._lock:
            all_items = self._merged_items()
            normalized_search = search.casefold().strip()
            filtered = []
            for item in all_items:
                if course != "todos" and item["confirmed_course"] != course:
                    continue
                if review_state != "todos" and item["review_state"] != review_state:
                    continue
                haystack = f"{item.get('title', '')} {item.get('confirmed_course', '')} {item['id']}".casefold()
                if normalized_search and normalized_search not in haystack:
                    continue
                filtered.append(item)

            page_size = max(25, min(int(page_size or 100), 250))
            total_filtered = len(filtered)
            pages = max(1, (total_filtered + page_size - 1) // page_size)
            page = max(1, min(int(page or 1), pages))
            start = (page - 1) * page_size
            counts = {
                state: sum(item["review_state"] == state for item in all_items)
                for state in REVIEW_STATES
            }
            course_counts: dict[str, int] = {}
            for item in all_items:
                key = item["confirmed_course"] or "Pendiente"
                course_counts[key] = course_counts.get(key, 0) + 1
            return {
                "schema_version": "book_inventory_reviewer_api_v2",
                "items": filtered[start : start + page_size],
                "courses": COURSES,
                "filter_courses": ["Pendiente", *COURSES],
                "material_types": MATERIAL_TYPES,
                "review_states": REVIEW_STATES,
                "counts": counts,
                "course_counts": course_counts,
                "total": len(all_items),
                "total_filtered": total_filtered,
                "page": page,
                "page_size": page_size,
                "pages": pages,
                "state_path": str(self.state_path),
                "export_root": str(self.export_root),
            }

    def save_decision(self, item_id: str, payload: dict) -> dict:
        with self._lock:
            if item_id not in self._catalog_by_id:
                raise KeyError("Libro no encontrado")
            catalog_item = self._catalog_by_id[item_id]
            review_state = str(payload.get("review_state") or "pendiente")
            course = str(payload.get("confirmed_course") or catalog_item.get("course") or "")
            material_type = str(payload.get("material_type") or "libro_problemas")
            multiple_choice = str(payload.get("multiple_choice") or "por_verificar")
            if review_state not in REVIEW_STATES:
                raise ValueError("Estado de revisión inválido")
            if review_state != "excluido" and course not in COURSES:
                raise ValueError("Curso inválido")
            if material_type not in MATERIAL_TYPES:
                raise ValueError("Tipo de material inválido")
            if multiple_choice not in {"si", "no", "por_verificar"}:
                raise ValueError("Valor de opción múltiple inválido")
            state = self._state()
            state.setdefault("decisions", {})[item_id] = {
                "review_state": review_state,
                "confirmed_course": course,
                "material_type": material_type,
                "multiple_choice": multiple_choice,
                "notes": str(payload.get("notes") or "").strip(),
                "reviewed_at": utc_now(),
            }
            self._write_state(state)
            return state["decisions"][item_id]

    def export(self) -> dict:
        snapshot = self.snapshot()
        accepted = [
            item
            for item in snapshot["items"]
            if item["review_state"] in {"confirmado", "reasignado"}
            and item["multiple_choice"] == "si"
            and item["material_type_review"] in {"libro_problemas", "libro_mixto"}
        ]
        self.export_root.mkdir(parents=True, exist_ok=True)
        exported = []
        for course in COURSES:
            rows = [item for item in accepted if item["confirmed_course"] == course]
            folder = self.export_root / safe_name(course)
            folder.mkdir(parents=True, exist_ok=True)
            for old_shortcut in folder.glob("*.url"):
                old_shortcut.unlink()
            csv_path = folder / "catalogo.csv"
            with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
                fields = ["id", "title", "course", "material_type", "url", "notes", "reviewed_at"]
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                for index, item in enumerate(rows, start=1):
                    writer.writerow(
                        {
                            "id": item["id"],
                            "title": item.get("title", ""),
                            "course": course,
                            "material_type": item["material_type_review"],
                            "url": item.get("url", ""),
                            "notes": item.get("notes", ""),
                            "reviewed_at": item.get("reviewed_at", ""),
                        }
                    )
                    shortcut = folder / f"{index:03d} - {safe_name(Path(item.get('title', '')).stem)}.url"
                    shortcut.write_text(
                        "[InternetShortcut]\n" f"URL={item.get('url', '')}\n",
                        encoding="utf-8",
                    )
            exported.append({"course": course, "count": len(rows), "folder": str(folder)})
        manifest = {
            "schema_version": "book_inventory_course_export_v1",
            "exported_at": utc_now(),
            "total": len(accepted),
            "courses": exported,
        }
        (self.export_root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return manifest


class ReviewerServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self.store = ReviewerStore()
        self.httpd: ThreadingHTTPServer | None = None

    def create_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:
                return

            def send_json(self, payload: dict, status: int = 200) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def read_json(self) -> dict:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0:
                    return {}
                return json.loads(self.rfile.read(length).decode("utf-8"))

            def serve_static(self, path: str) -> None:
                relative = "index.html" if path in {"", "/"} else unquote(path.lstrip("/"))
                target = (WEB_ROOT / relative).resolve()
                if WEB_ROOT.resolve() not in target.parents and target != WEB_ROOT.resolve():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                if not target.exists() or not target.is_file():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                body = target.read_bytes()
                content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                from urllib.parse import parse_qs

                parsed = urlparse(self.path)
                path = parsed.path
                if path == "/api/catalog":
                    query = parse_qs(parsed.query)
                    self.send_json(
                        server.store.query_catalog(
                            page=int(query.get("page", ["1"])[0]),
                            page_size=int(query.get("page_size", ["100"])[0]),
                            search=query.get("search", [""])[0],
                            course=query.get("course", ["todos"])[0],
                            review_state=query.get("review_state", ["pendiente"])[0],
                        )
                    )
                    return
                if path == "/api/health":
                    self.send_json({"ok": True, "app": "Book Inventory Reviewer"})
                    return
                self.serve_static(path)

            def do_POST(self) -> None:
                path = urlparse(self.path).path
                try:
                    if path.startswith("/api/books/") and path.endswith("/review"):
                        item_id = unquote(path[len("/api/books/") : -len("/review")]).strip("/")
                        decision = server.store.save_decision(item_id, self.read_json())
                        self.send_json({"ok": True, "decision": decision})
                        return
                    if path == "/api/export":
                        self.send_json({"ok": True, "manifest": server.store.export()})
                        return
                    self.send_json({"ok": False, "error": "Ruta API no encontrada"}, 404)
                except (ValueError, KeyError, json.JSONDecodeError) as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 400)
                except Exception as exc:
                    self.send_json({"ok": False, "error": f"Error interno: {exc}"}, 500)

        return Handler

    def start(self) -> str:
        self.httpd = ThreadingHTTPServer((self.host, self.port), self.create_handler())
        self.port = int(self.httpd.server_address[1])
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        return f"http://{self.host}:{self.port}/"


def main() -> None:
    parser = argparse.ArgumentParser(description="Visor web para clasificar libros remotos")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    app = ReviewerServer(args.host, args.port)
    url = app.start()
    print(url, flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        if app.httpd:
            app.httpd.shutdown()


if __name__ == "__main__":
    main()
