from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SCAN_MATH_DB_ROOT = Path(os.environ.get("NEXUMATH_SCAN_MATH_DB_ROOT", r"E:\Github\MathContentStudio\scan-math-db"))


SCAN_MATH_REQUIRED_FILES = [
    "app/main.py",
    "app/api/studio.py",
    "app/api/studio_factory.py",
    "app/api/health.py",
    "app/math_bank.py",
    "app/core/config.py",
    "app/factory_errors.py",
    "app/factory_bootstrap.py",
    "app/factory_storage.py",
    "app/web/studio-login.html",
    "app/web/studio-factory.html",
    "app/web/studio-dashboard.html",
    "app/web/studio-instances.html",
    "app/web/studio-problems.html",
    "app/web/studio-pdf-open.html",
    "app/web/studio-pdf-viewer.html",
]

AUDITOR_REQUIRED_FILES = [
    "modulos/instance_factory/library_web_server.py",
    "modulos/instance_factory/web_server.py",
    "modulos/instance_factory/pipeline.py",
    "modulos/instance_factory/staging.py",
    "modulos/instance_factory/server_jobs.py",
    "modulos/instance_factory/server_storage.py",
    "modulos/instance_factory/model_inventory.py",
    "modulos/instance_factory/hf_endpoint_manager.py",
    "modulos/instance_factory/web/app.js",
    "modulos/instance_factory/web/styles.css",
]

EXPECTED_FACTORY_ROUTES = [
    ("GET", "/studio/factory/bootstrap", "US1 bootstrap"),
    ("GET", "/studio/factory/books", "US2 library"),
    ("GET", "/studio/factory/books/{book_id}/instances", "US2 instances"),
    ("GET", "/studio/factory/instances/{instance_id}/snapshot", "US2 instance snapshot"),
    ("POST", "/studio/factory/instances/{instance_id}/jobs", "US3 start job"),
    ("GET", "/studio/factory/jobs/{job_id}", "US3 job status"),
    ("POST", "/studio/factory/records/{record_id}/review", "US4 review save"),
    ("POST", "/studio/factory/word/selection", "US4 word selection"),
    ("POST", "/studio/factory/word/generate", "US4 word generation"),
    ("GET", "/studio/factory/word/jobs/{job_id}/download", "US4 word artifact download"),
]

ROUTE_DECORATOR_RE = re.compile(
    r"@(?P<owner>app|router)\.(?P<method>get|post|put|patch|delete)\(\s*[\"'](?P<path>[^\"']+)[\"']",
    re.IGNORECASE,
)
ROUTER_PREFIX_RE = re.compile(r"APIRouter\([^)]*prefix\s*=\s*[\"'](?P<prefix>[^\"']+)[\"']", re.IGNORECASE | re.DOTALL)
HTML_REF_RE = re.compile(r"\b(?:href|src)=[\"'](?P<ref>[^\"']+)[\"']", re.IGNORECASE)
WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s\"'<>]+")
SECRET_KEY_RE = re.compile(r"(token|password|secret|key|credential)", re.IGNORECASE)


@dataclass(frozen=True)
class FileCheck:
    path: str
    exists: bool


@dataclass(frozen=True)
class RouteInfo:
    method: str
    path: str
    file: str
    line: int


@dataclass(frozen=True)
class ExpectedRouteCheck:
    method: str
    path: str
    purpose: str
    present: bool


@dataclass(frozen=True)
class WebAssetInfo:
    file: str
    references: list[str]
    local_path_refs: list[str]


@dataclass(frozen=True)
class CompatibilityReport:
    generated_at: str
    auditor_root: str
    scan_math_db_root: str
    scan_math_db_exists: bool
    scan_math_required_files: list[FileCheck]
    auditor_required_files: list[FileCheck]
    scan_math_routes: list[RouteInfo]
    auditor_factory_routes: list[RouteInfo]
    expected_factory_routes: list[ExpectedRouteCheck]
    web_assets: list[WebAssetInfo]
    summary: dict[str, int | bool]
    next_actions: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit compatibility between NexumathJF Studio (scan-math-db) and Auditor-IA Biblioteca/Fabrica."
    )
    parser.add_argument(
        "--scan-math-db-root",
        default=str(DEFAULT_SCAN_MATH_DB_ROOT),
        help="Path to E:/Github/MathContentStudio/scan-math-db.",
    )
    parser.add_argument(
        "--json-output",
        default=str(ROOT_DIR / "tmp" / "nexumath_studio_factory_audit" / "audit.json"),
        help="Machine-readable JSON output path.",
    )
    parser.add_argument(
        "--markdown-output",
        default=str(ROOT_DIR / "docs" / "reporte_nexumath_studio_factory_compatibilidad.md"),
        help="Markdown report output path.",
    )
    return parser.parse_args()


def rel_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except Exception:
        return str(path)


def check_files(root: Path, relative_paths: Iterable[str]) -> list[FileCheck]:
    checks: list[FileCheck] = []
    for item in relative_paths:
        checks.append(FileCheck(path=item.replace("\\", "/"), exists=(root / item).is_file()))
    return checks


def discover_routes(root: Path, files: Iterable[str]) -> list[RouteInfo]:
    routes: list[RouteInfo] = []
    for relative in files:
        path = root / relative
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        prefix_match = ROUTER_PREFIX_RE.search(text)
        router_prefix = str(prefix_match.group("prefix") if prefix_match else "").rstrip("/")
        for index, line in enumerate(lines, start=1):
            match = ROUTE_DECORATOR_RE.search(line)
            if not match:
                continue
            route_path = match.group("path")
            if match.group("owner").lower() == "router" and router_prefix and not route_path.startswith(router_prefix + "/"):
                route_path = router_prefix + (route_path if route_path.startswith("/") else f"/{route_path}")
            routes.append(
                RouteInfo(
                    method=match.group("method").upper(),
                    path=route_path,
                    file=relative.replace("\\", "/"),
                    line=index,
                )
            )
    return routes


def discover_auditor_api_routes(root: Path, files: Iterable[str]) -> list[RouteInfo]:
    routes: list[RouteInfo] = []
    for relative in files:
        path = root / relative
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != "_allowed_api_methods":
                continue
            for child in ast.walk(node):
                if not isinstance(child, ast.Assign):
                    continue
                if not any(isinstance(target, ast.Name) and target.id == "exact" for target in child.targets):
                    continue
                if not isinstance(child.value, ast.Dict):
                    continue
                for key, value in zip(child.value.keys, child.value.values):
                    if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                        continue
                    methods: set[str] = set()
                    if isinstance(value, ast.Set):
                        for element in value.elts:
                            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                                methods.add(element.value.upper())
                    elif isinstance(value, ast.Constant) and isinstance(value.value, str):
                        methods.add(value.value.upper())
                    for method in sorted(methods):
                        routes.append(
                            RouteInfo(
                                method=method,
                                path=key.value,
                                file=relative.replace("\\", "/"),
                                line=getattr(key, "lineno", getattr(child, "lineno", 0)),
                            )
                        )
    return sorted(routes, key=lambda item: (item.file, item.path, item.method))


def normalize_contract_path(path: str) -> str:
    normalized = str(path or "").strip().rstrip("/")
    normalized = re.sub(r"\{[^/{}]+\}", "{}", normalized)
    return normalized or "/"


def route_present(routes: Iterable[RouteInfo], method: str, expected_path: str) -> bool:
    expected = normalize_contract_path(expected_path)
    for route in routes:
        if route.method != method.upper():
            continue
        if normalize_contract_path(route.path) == expected:
            return True
    return False


def build_expected_route_checks(routes: Iterable[RouteInfo]) -> list[ExpectedRouteCheck]:
    route_list = list(routes)
    return [
        ExpectedRouteCheck(method=method, path=path, purpose=purpose, present=route_present(route_list, method, path))
        for method, path, purpose in EXPECTED_FACTORY_ROUTES
    ]


def redact_reference(value: str) -> str:
    text = str(value or "")
    if SECRET_KEY_RE.search(text):
        return "[redacted-sensitive-reference]"
    return text


def discover_web_assets(root: Path) -> list[WebAssetInfo]:
    web_root = root / "app" / "web"
    if not web_root.is_dir():
        return []
    assets: list[WebAssetInfo] = []
    for path in sorted(web_root.glob("studio*.html")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        refs = [redact_reference(match.group("ref")) for match in HTML_REF_RE.finditer(text)]
        local_refs = sorted(set(WINDOWS_PATH_RE.findall(text)))
        assets.append(
            WebAssetInfo(
                file=rel_path(path, root),
                references=refs,
                local_path_refs=local_refs,
            )
        )
    return assets


def build_next_actions(report: CompatibilityReport) -> list[str]:
    actions: list[str] = []
    if not report.scan_math_db_exists:
        actions.append("Confirm the scan-math-db repository path before implementation.")
        return actions
    missing_scan = [item.path for item in report.scan_math_required_files if not item.exists]
    if missing_scan:
        actions.append("Restore or locate required scan-math-db files before replacing Studio routes.")
    missing_auditor = [item.path for item in report.auditor_required_files if not item.exists]
    if missing_auditor:
        actions.append("Complete or locate required Auditor-IA factory modules before porting workflow slices.")
    missing_routes = [item.path for item in report.expected_factory_routes if not item.present]
    if missing_routes:
        actions.append("Implement the missing /studio/factory API contract routes in scan-math-db.")
    if any(asset.local_path_refs for asset in report.web_assets):
        actions.append("Remove local Windows path references from Studio web files before public deployment.")
    if not actions:
        actions.append("Proceed to Studio shell replacement and contract tests.")
    return actions


def build_report(scan_math_db_root: Path, auditor_root: Path = ROOT_DIR) -> CompatibilityReport:
    scan_root = scan_math_db_root.resolve()
    auditor = auditor_root.resolve()
    scan_exists = scan_root.is_dir()
    scan_files = check_files(scan_root, SCAN_MATH_REQUIRED_FILES) if scan_exists else [
        FileCheck(path=item, exists=False) for item in SCAN_MATH_REQUIRED_FILES
    ]
    auditor_files = check_files(auditor, AUDITOR_REQUIRED_FILES)
    route_files = ["app/main.py", "app/api/studio.py", "app/api/studio_factory.py", "app/api/health.py"]
    routes = discover_routes(scan_root, route_files) if scan_exists else []
    auditor_route_files = [
        "modulos/instance_factory/library_web_server.py",
        "modulos/instance_factory/web_server.py",
    ]
    auditor_routes = discover_auditor_api_routes(auditor, auditor_route_files)
    expected_routes = build_expected_route_checks(routes)
    web_assets = discover_web_assets(scan_root) if scan_exists else []

    report = CompatibilityReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        auditor_root=str(auditor),
        scan_math_db_root=str(scan_root),
        scan_math_db_exists=scan_exists,
        scan_math_required_files=scan_files,
        auditor_required_files=auditor_files,
        scan_math_routes=routes,
        auditor_factory_routes=auditor_routes,
        expected_factory_routes=expected_routes,
        web_assets=web_assets,
        summary={
            "scan_math_required_missing": sum(1 for item in scan_files if not item.exists),
            "auditor_required_missing": sum(1 for item in auditor_files if not item.exists),
            "scan_math_route_count": len(routes),
            "auditor_factory_route_count": len(auditor_routes),
            "expected_factory_routes_present": sum(1 for item in expected_routes if item.present),
            "expected_factory_routes_missing": sum(1 for item in expected_routes if not item.present),
            "studio_web_files": len(web_assets),
            "studio_web_files_with_local_paths": sum(1 for item in web_assets if item.local_path_refs),
            "scan_math_db_exists": scan_exists,
        },
        next_actions=[],
    )
    return replace(report, next_actions=build_next_actions(report))


def render_markdown_report(report: CompatibilityReport) -> str:
    lines: list[str] = []
    lines.append("# Nexumath Studio Factory Compatibility Report")
    lines.append("")
    lines.append(f"Generated: `{report.generated_at}`")
    lines.append("")
    lines.append("## Scope")
    lines.append("")
    lines.append(f"- Auditor-IA root: `{report.auditor_root}`")
    lines.append(f"- scan-math-db root: `{report.scan_math_db_root}`")
    lines.append(f"- scan-math-db exists: `{str(report.scan_math_db_exists).lower()}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    for key, value in report.summary.items():
        lines.append(f"- {key}: `{value}`")
    lines.append("")
    lines.append("## Required scan-math-db Files")
    lines.append("")
    lines.append("| File | Status |")
    lines.append("|------|--------|")
    for item in report.scan_math_required_files:
        lines.append(f"| `{item.path}` | {'ok' if item.exists else 'missing'} |")
    lines.append("")
    lines.append("## Required Auditor-IA Factory Files")
    lines.append("")
    lines.append("| File | Status |")
    lines.append("|------|--------|")
    for item in report.auditor_required_files:
        lines.append(f"| `{item.path}` | {'ok' if item.exists else 'missing'} |")
    lines.append("")
    lines.append("## Current scan-math-db Routes")
    lines.append("")
    lines.append("| Method | Path | File | Line |")
    lines.append("|--------|------|------|------|")
    for route in report.scan_math_routes:
        lines.append(f"| `{route.method}` | `{route.path}` | `{route.file}` | `{route.line}` |")
    if not report.scan_math_routes:
        lines.append("| - | - | - | - |")
    lines.append("")
    lines.append("## Current Biblioteca/Fabrica API Inventory")
    lines.append("")
    lines.append("| Method | Path | File | Line |")
    lines.append("|--------|------|------|------|")
    for route in report.auditor_factory_routes:
        lines.append(f"| `{route.method}` | `{route.path}` | `{route.file}` | `{route.line}` |")
    if not report.auditor_factory_routes:
        lines.append("| - | - | - | - |")
    lines.append("")
    lines.append("## Expected /studio/factory Contract Routes")
    lines.append("")
    lines.append("| Method | Path | Purpose | Status |")
    lines.append("|--------|------|---------|--------|")
    for route in report.expected_factory_routes:
        lines.append(f"| `{route.method}` | `{route.path}` | {route.purpose} | {'present' if route.present else 'missing'} |")
    lines.append("")
    lines.append("## Studio Web Files")
    lines.append("")
    lines.append("| File | References | Local Path Refs |")
    lines.append("|------|------------|-----------------|")
    for asset in report.web_assets:
        lines.append(f"| `{asset.file}` | `{len(asset.references)}` | `{len(asset.local_path_refs)}` |")
    if not report.web_assets:
        lines.append("| - | - | - |")
    lines.append("")
    lines.append("## Next Actions")
    lines.append("")
    for action in report.next_actions:
        lines.append(f"- {action}")
    lines.append("")
    return "\n".join(lines)


def write_report(report: CompatibilityReport, *, json_output: Path, markdown_output: Path) -> None:
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8")
    markdown_output.write_text(render_markdown_report(report), encoding="utf-8")


def main() -> int:
    args = parse_args()
    report = build_report(Path(args.scan_math_db_root))
    write_report(
        report,
        json_output=Path(args.json_output),
        markdown_output=Path(args.markdown_output),
    )
    print(f"Wrote JSON: {args.json_output}")
    print(f"Wrote Markdown: {args.markdown_output}")
    print(f"Missing expected factory routes: {report.summary['expected_factory_routes_missing']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
