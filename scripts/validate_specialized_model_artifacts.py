from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from modulos.instance_factory.document_splits import (  # noqa: E402
    audit_document_split_manifest,
    validate_document_split_manifest,
)
from modulos.instance_factory.specialized_model_evaluation import (  # noqa: E402
    evaluate_specialized_model_candidate,
)
from modulos.instance_factory.supervised_annotations import (  # noqa: E402
    validate_annotation_release,
)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"El artefacto debe ser un objeto JSON: {path}")
    return payload


def validate_artifacts(
    *,
    annotation_release: Path | None = None,
    split_manifest: Path | None = None,
    model_candidate: Path | None = None,
    operation_mode: str = "pilot",
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    if annotation_release is not None:
        issues = validate_annotation_release(_read_json(annotation_release))
        checks.append(
            {
                "artifact_type": "annotation_release",
                "artifact_name": annotation_release.name,
                "passed": not issues,
                "issues": issues,
            }
        )
    if split_manifest is not None:
        payload = _read_json(split_manifest)
        issues = validate_document_split_manifest(payload)
        checks.append(
            {
                "artifact_type": "document_split_manifest",
                "artifact_name": split_manifest.name,
                "passed": not issues,
                "issues": issues,
                "leakage_audit": audit_document_split_manifest(payload),
            }
        )
    if model_candidate is not None:
        evaluation = evaluate_specialized_model_candidate(
            _read_json(model_candidate),
            operation_mode=operation_mode,
        )
        checks.append(
            {
                "artifact_type": "specialized_model_candidate",
                "artifact_name": model_candidate.name,
                "passed": bool(evaluation.get("gate_passed")),
                "issues": list(evaluation.get("blockers") or []),
                "evaluation": evaluation,
            }
        )
    if not checks:
        raise ValueError("Debe proporcionar al menos un artefacto para validar")
    return {
        "schema_version": "specialized_model_artifact_validation_report_v1",
        "read_only": True,
        "canonical_writes": "disabled",
        "training": "not_started",
        "checks": checks,
        "passed": all(bool(check["passed"]) for check in checks),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Valida releases relacionales, splits por documento y candidatos IND-MA-01 sin promoverlos.",
    )
    parser.add_argument("--annotation-release", type=Path)
    parser.add_argument("--split-manifest", type=Path)
    parser.add_argument("--model-candidate", type=Path)
    parser.add_argument("--operation-mode", choices=("pilot", "regular"), default="pilot")
    parser.add_argument("--output", type=Path, help="Ruta opcional para guardar el reporte no canonico.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = validate_artifacts(
            annotation_release=args.annotation_release,
            split_manifest=args.split_manifest,
            model_candidate=args.model_candidate,
            operation_mode=args.operation_mode,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
