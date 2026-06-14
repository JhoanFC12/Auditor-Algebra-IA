from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


REPORT_SCHEMA_VERSION = "semantic_descriptor_validation_report_v1"
SCHEMA_FILES = {
    "problem_semantic_profile_v1": Path("docs/schemas/problem_semantic_profile_v1.schema.json"),
    "geometry_figure_description_v1": Path("docs/schemas/geometry_figure_description_v1.schema.json"),
}

try:
    import jsonschema  # type: ignore
except Exception:  # pragma: no cover - depends on local environment
    jsonschema = None  # type: ignore


@dataclass
class ValidationIssue:
    path: str
    valid: bool
    schema_version: str
    errors: list[str]


@dataclass
class ValidationReport:
    manifest: dict[str, Any]
    issues: list[ValidationIssue]


def _read_json(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, [f"invalid_json:{exc}"]
    if not isinstance(payload, dict):
        return None, ["invalid_payload:not_object"]
    return payload, []


def _load_schema(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    payload, errors = _read_json(path)
    if errors:
        return None, errors
    return payload, []


def load_schemas(root: Path = ROOT) -> tuple[dict[str, dict[str, Any]], list[str]]:
    schemas: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for schema_version, relative_path in SCHEMA_FILES.items():
        schema_path = root / relative_path
        payload, schema_errors = _load_schema(schema_path)
        if schema_errors:
            errors.extend(f"{schema_path}:{error}" for error in schema_errors)
            continue
        assert payload is not None
        schemas[schema_version] = payload
    return schemas, errors


def iter_json_paths(paths: Iterable[Path]) -> list[Path]:
    selected: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        if path.is_dir():
            selected.extend(sorted(path.rglob("*.json"), key=lambda item: str(item).lower()))
        elif path.exists():
            selected.append(path)
        else:
            selected.append(path)
    return selected


def validate_payload(path: Path, payload: dict[str, Any], schemas: dict[str, dict[str, Any]]) -> ValidationIssue:
    schema_version = str(payload.get("schema_version") or "").strip()
    errors: list[str] = []
    if not schema_version:
        errors.append("missing:schema_version")
    elif schema_version not in schemas:
        errors.append(f"unknown:schema_version:{schema_version}")
    elif jsonschema is None:
        errors.append("jsonschema_unavailable")
    else:
        try:
            jsonschema.validate(payload, schemas[schema_version])
        except Exception as exc:
            errors.append(f"schema_validation:{exc}")
    return ValidationIssue(
        path=str(path),
        valid=not errors,
        schema_version=schema_version,
        errors=errors,
    )


def validate_paths(paths: Iterable[Path], *, root: Path = ROOT) -> ValidationReport:
    schemas, schema_errors = load_schemas(root)
    issues: list[ValidationIssue] = []
    for error in schema_errors:
        issues.append(ValidationIssue(path="<schemas>", valid=False, schema_version="", errors=[error]))
    for path in iter_json_paths(paths):
        if not path.exists():
            issues.append(ValidationIssue(path=str(path), valid=False, schema_version="", errors=["missing_file"]))
            continue
        payload, errors = _read_json(path)
        if errors:
            issues.append(ValidationIssue(path=str(path), valid=False, schema_version="", errors=errors))
            continue
        assert payload is not None
        issues.append(validate_payload(path, payload, schemas))
    valid_total = sum(1 for item in issues if item.valid)
    manifest = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "jsonschema_available": jsonschema is not None,
        "schemas_loaded": sorted(schemas.keys()),
        "total": len(issues),
        "valid_total": valid_total,
        "invalid_total": len(issues) - valid_total,
        "results": [
            {
                "path": item.path,
                "valid": item.valid,
                "schema_version": item.schema_version,
                "errors": item.errors,
            }
            for item in issues
        ],
    }
    return ValidationReport(manifest=manifest, issues=issues)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Valida contratos JSON del descriptor semantico y descriptor de graficos."
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="Archivo JSON o carpeta con JSON. Repetible. Si se omite, usa docs/examples/semantic_descriptor.",
    )
    args = parser.parse_args()
    selected = [Path(item) for item in args.path] or [ROOT / "docs/examples/semantic_descriptor"]
    report = validate_paths(selected)
    print(json.dumps(report.manifest, ensure_ascii=False, indent=2))
    return 0 if int(report.manifest["invalid_total"]) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
