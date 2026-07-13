from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.audit_nexumath_studio_factory import (
    build_expected_route_checks,
    build_report,
    discover_auditor_api_routes,
    discover_routes,
    render_markdown_report,
)


class NexumathStudioFactoryAuditTests(unittest.TestCase):
    def test_discovers_fastapi_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            api_dir = root / "app" / "api"
            api_dir.mkdir(parents=True)
            route_file = api_dir / "studio.py"
            route_file.write_text(
                "\n".join(
                    [
                        "from fastapi import APIRouter",
                        "router = APIRouter(prefix='/studio')",
                        '@router.get("/books")',
                        "def books():",
                        "    pass",
                        '@router.post("/factory/word/generate")',
                        "def word():",
                        "    pass",
                    ]
                ),
                encoding="utf-8",
            )

            routes = discover_routes(root, ["app/api/studio.py"])

        self.assertEqual(len(routes), 2)
        self.assertEqual(routes[0].method, "GET")
        self.assertEqual(routes[0].path, "/studio/books")
        self.assertEqual(routes[1].method, "POST")

    def test_expected_route_matching_handles_path_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            api_dir = root / "app" / "api"
            api_dir.mkdir(parents=True)
            route_file = api_dir / "studio.py"
            route_file.write_text(
                '@router.get("/studio/factory/books/{book_id}/instances")\n',
                encoding="utf-8",
            )
            routes = discover_routes(root, ["app/api/studio.py"])
            checks = build_expected_route_checks(routes)

        instances = [item for item in checks if item.path == "/studio/factory/books/{book_id}/instances"][0]
        self.assertTrue(instances.present)

    def test_discovers_auditor_api_routes_from_allowed_methods_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            server_file = root / "modulos" / "instance_factory" / "web_server.py"
            server_file.parent.mkdir(parents=True)
            server_file.write_text(
                "\n".join(
                    [
                        "class Runtime:",
                        "    @staticmethod",
                        "    def _allowed_api_methods(path):",
                        "        exact = {",
                        '            "/api/bootstrap": {"GET"},',
                        '            "/api/ocr/raw": {"POST"},',
                        "        }",
                        "        return exact.get(path, set())",
                    ]
                ),
                encoding="utf-8",
            )

            routes = discover_auditor_api_routes(root, ["modulos/instance_factory/web_server.py"])

        self.assertEqual([route.path for route in routes], ["/api/bootstrap", "/api/ocr/raw"])
        self.assertEqual([route.method for route in routes], ["GET", "POST"])

    def test_build_report_marks_missing_factory_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            scan_root = Path(tmp_dir)
            (scan_root / "app" / "api").mkdir(parents=True)
            (scan_root / "app" / "web").mkdir(parents=True)
            (scan_root / "app" / "main.py").write_text('@app.get("/")\ndef root(): pass\n', encoding="utf-8")
            (scan_root / "app" / "api" / "studio.py").write_text('@router.get("/books")\ndef books(): pass\n', encoding="utf-8")
            (scan_root / "app" / "api" / "health.py").write_text('@router.get("/health")\ndef health(): pass\n', encoding="utf-8")
            (scan_root / "app" / "web" / "studio-dashboard.html").write_text(
                '<a href="/web/studio-instances.html">Instances</a>',
                encoding="utf-8",
            )

            report = build_report(scan_root)

        self.assertTrue(report.scan_math_db_exists)
        self.assertGreater(report.summary["expected_factory_routes_missing"], 0)
        self.assertIn("auditor_factory_route_count", report.summary)
        rendered = render_markdown_report(report)
        self.assertIn("Expected /studio/factory Contract Routes", rendered)
        self.assertIn("Current Biblioteca/Fabrica API Inventory", rendered)
        self.assertIn("missing", rendered)


if __name__ == "__main__":
    unittest.main()
