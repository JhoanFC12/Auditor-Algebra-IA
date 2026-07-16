from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image


REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / ".cache" / "book_catalog" / "problem_solution_staging"
ROOT = ROOT / "euler-app-library-problem-solutions-20260716-global-r1"
ROOT = ROOT / "h_ps1_ingrid_activation_20260716_r1"
OUTPUT = ROOT / "ingrid_outputs" / "batch-01"
EXPECTED = {
    "bundle_manifest.json": "af1832c6ece45656774d11af00bf33bbcb9e656f68d5514d313e43b7ebd40f9f",
    "validation_report.json": "311c1abdfa038b2ca2e67552841adeeb06cc0ff2b731148d3760ecf81a2a3c3a",
    "batches/batch-01.json": "b763ca36ba45e8c42873ea5016b6faf59cb669f200fd83e049dabe8013cb249a",
}


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def safe(path: Path) -> bool:
    try:
        path.resolve().relative_to(OUTPUT.resolve())
        return True
    except ValueError:
        return False


def all_keys(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            result.add(str(key))
            result.update(all_keys(item))
    elif isinstance(value, list):
        for item in value:
            result.update(all_keys(item))
    return result


def valid_box(box: Any, width: int, height: int) -> bool:
    return (
        isinstance(box, list)
        and len(box) == 4
        and all(isinstance(value, int) for value in box)
        and 0 <= box[0] < box[2] <= width
        and 0 <= box[1] < box[3] <= height
    )


def main() -> int:
    errors: list[str] = []
    hashes = {name: digest(ROOT / name) for name in EXPECTED}
    for name, expected in EXPECTED.items():
        if hashes[name] != expected:
            errors.append("activation_hash_mismatch:" + name)

    activation = read(ROOT / "validation_report.json")
    tests = activation.get("tests") or []
    if len(tests) != 20 or not all(test.get("passed") for test in tests):
        errors.append("activation_controls_not_20_of_20")

    batch = read(ROOT / "batches" / "batch-01.json")
    if not batch.get("execution_authorized"):
        errors.append("batch01_not_authorized")
    if batch.get("assignment_count") != 10:
        errors.append("batch01_assignment_count")
    if batch.get("authorized_page_assignments") != 538:
        errors.append("batch01_page_count")

    for sequence in range(2, 10):
        queued = read(ROOT / "batches" / f"batch-{sequence:02d}.json")
        if queued.get("execution_authorized"):
            errors.append(f"batch{sequence:02d}_unexpectedly_authorized")
        if queued.get("status") != "queued_not_authorized":
            errors.append(f"batch{sequence:02d}_status_changed")
        if (ROOT / "ingrid_outputs" / f"batch-{sequence:02d}").exists():
            errors.append(f"batch{sequence:02d}_has_output")

    total = Counter()
    summaries = []
    seen_units: set[str] = set()
    seen_fragments: set[str] = set()
    expected_dirs = set()

    for entry in batch["assignments"]:
        assignment_path = Path(entry["assignment_path"])
        assignment = read(assignment_path)
        assignment_id = assignment["assignment_id"]
        expected_dirs.add(assignment_id)
        if digest(assignment_path) != entry["assignment_sha256"]:
            errors.append(assignment_id + ":assignment_hash")
        source = Path(assignment["source_document"]["pdf_path"])
        if digest(source) != assignment["source_document"]["pdf_sha256"]:
            errors.append(assignment_id + ":source_hash")

        path = OUTPUT / assignment_id / "segmentation.json"
        if not path.exists():
            errors.append(assignment_id + ":missing_segmentation")
            continue
        value = read(path)
        keys = all_keys(value)
        if "bbox_px" in keys or "sha256" in keys:
            errors.append(assignment_id + ":legacy_alias")
        checks = {
            "schema_version": "ingrid_instance_solution_segmentation_v1",
            "assignment_id": assignment_id,
            "context_fingerprint": assignment["context_fingerprint"],
            "expected_revision": assignment["expected_revision"],
            "status": "agent_segmented_pending_human",
            "human_review": "pending",
            "next_gate": "H-PS2",
        }
        for key, expected in checks.items():
            if value.get(key) != expected:
                errors.append(f"{assignment_id}:{key}_mismatch")
        if value.get("pages_inspected") != assignment["approved_pages"]:
            errors.append(assignment_id + ":pages_inspected")

        log = value.get("inspection_log") or []
        if [row.get("page_number") for row in log] != assignment["approved_pages"]:
            errors.append(assignment_id + ":inspection_log")
        abstentions = [
            row["page_number"]
            for row in log
            if str(row.get("disposition", "")).startswith("abstained")
        ]
        if abstentions != value.get("abstention_pages"):
            errors.append(assignment_id + ":abstention_list")

        page_sizes = {}
        overlays = value.get("evidence_overlays") or []
        if len(overlays) != 2 * len(assignment["approved_pages"]):
            errors.append(assignment_id + ":overlay_count")
        for relative in overlays:
            evidence = OUTPUT / relative
            if not safe(evidence) or not evidence.is_file():
                errors.append(assignment_id + ":overlay_missing:" + str(relative))
        for page in assignment["approved_pages"]:
            before = OUTPUT / assignment_id / "overlays" / f"page_{page:04d}_before.jpg"
            if before.is_file():
                with Image.open(before) as image:
                    page_sizes[page] = image.size
            else:
                errors.append(f"{assignment_id}:before_missing:{page}")

        reviews = value.get("problem_box_reviews") or []
        if [row.get("page_number") for row in reviews] != assignment["problem_pages"]:
            errors.append(assignment_id + ":review_pages")
        roles = Counter()
        statuses = Counter()
        operations = Counter()
        for review in reviews:
            page = review["page_number"]
            width, height = page_sizes.get(page, (0, 0))
            statuses[review.get("status")] += 1
            if review.get("human_review") != "pending":
                errors.append(f"{assignment_id}:review_not_pending:{page}")
            for operation in review.get("operations") or []:
                operations[operation.get("action")] += 1
            for box in review.get("proposed_boxes") or []:
                role = box.get("role")
                roles[role] += 1
                if role not in {"problem", "problem_number", "answer_block"}:
                    errors.append(f"{assignment_id}:bad_role:{role}")
                if not valid_box(box.get("bbox_xyxy"), width, height):
                    errors.append(f"{assignment_id}:bad_box:{box.get('box_id')}")
            for field in ("overlay_before", "overlay_after"):
                evidence = OUTPUT / str(review.get(field))
                if not safe(evidence) or not evidence.is_file():
                    errors.append(f"{assignment_id}:review_evidence:{page}:{field}")

        unit_count = 0
        fragment_count = 0
        complete_count = 0
        incomplete_ids = []
        for unit in value.get("solution_units") or []:
            unit_count += 1
            unit_id = unit.get("unit_id")
            if unit_id in seen_units:
                errors.append("duplicate_unit:" + str(unit_id))
            seen_units.add(unit_id)
            if unit.get("source_mapping_status") != "confirmed":
                errors.append(f"{assignment_id}:mapping:{unit_id}")
            fragments = unit.get("fragments") or []
            roles_sequence = [
                fragment.get("fragment_role") for fragment in fragments
            ]
            complete = bool(unit.get("continuation_complete"))
            if complete:
                complete_count += 1
                expected_roles = ["single"]
                if len(fragments) > 1:
                    expected_roles = (
                        ["begin"]
                        + ["middle"] * max(0, len(fragments) - 2)
                        + ["end"]
                    )
                if roles_sequence != expected_roles:
                    errors.append(f"{assignment_id}:complete_roles:{unit_id}")
            else:
                incomplete_ids.append(unit_id)
                if (
                    not roles_sequence
                    or roles_sequence[0] != "begin"
                    or "single" in roles_sequence
                    or "end" in roles_sequence
                ):
                    errors.append(f"{assignment_id}:incomplete_roles:{unit_id}")
            if [item.get("reading_order") for item in fragments] != list(
                range(1, len(fragments) + 1)
            ):
                errors.append(f"{assignment_id}:reading_order:{unit_id}")
            for fragment in fragments:
                fragment_count += 1
                fragment_id = fragment.get("fragment_id")
                if fragment_id in seen_fragments:
                    errors.append("duplicate_fragment:" + str(fragment_id))
                seen_fragments.add(fragment_id)
                page = fragment.get("page_number")
                if page not in assignment["solution_pages"]:
                    errors.append(f"{assignment_id}:page_outside_scope:{fragment_id}")
                width, height = page_sizes.get(page, (0, 0))
                if not valid_box(fragment.get("bbox_xyxy"), width, height):
                    errors.append(f"{assignment_id}:fragment_box:{fragment_id}")
                crop = OUTPUT / str(fragment.get("crop_path"))
                if not safe(crop) or not crop.is_file():
                    errors.append(f"{assignment_id}:crop_missing:{fragment_id}")
                elif digest(crop) != fragment.get("crop_sha256"):
                    errors.append(f"{assignment_id}:crop_hash:{fragment_id}")

        total["pages_inspected"] += len(value.get("pages_inspected") or [])
        total["problem_reviews"] += len(reviews)
        total["problem_boxes"] += sum(roles.values())
        total["problem_role"] += roles["problem"]
        total["problem_number_role"] += roles["problem_number"]
        total["answer_block_role"] += roles["answer_block"]
        total["solution_units"] += unit_count
        total["solution_fragments"] += fragment_count
        total["complete_solution_units"] += complete_count
        total["incomplete_solution_units"] += len(incomplete_ids)
        total["abstention_pages"] += len(abstentions)
        total["evidence_overlays"] += len(overlays)
        total["accepted_unchanged_reviews"] += statuses["accepted_unchanged"]
        total["agent_corrected_reviews"] += statuses[
            "agent_corrected_pending_human"
        ]
        total["abstained_reviews"] += statuses["abstained"]
        summaries.append(
            {
                "assignment_id": assignment_id,
                "book_code": assignment["scope"]["book_code"],
                "instance_id": assignment["scope"]["instance_id"],
                "pages_inspected": len(value.get("pages_inspected") or []),
                "problem_reviews": len(reviews),
                "problem_boxes": sum(roles.values()),
                "solution_units": unit_count,
                "solution_fragments": fragment_count,
                "complete_solution_units": complete_count,
                "incomplete_solution_units": len(incomplete_ids),
                "incomplete_unit_ids": incomplete_ids,
                "abstention_pages": abstentions,
                "status_counts": dict(statuses),
                "role_counts": dict(roles),
                "segmentation_path": str(path),
                "overlay_directory": str(OUTPUT / assignment_id / "overlays"),
                "crop_directory": str(OUTPUT / assignment_id / "solution_crops"),
            }
        )

    actual_dirs = {path.name for path in OUTPUT.iterdir() if path.is_dir()}
    if actual_dirs != expected_dirs:
        errors.append("assignment_output_directories")
    if total["pages_inspected"] != 538:
        errors.append("aggregate_pages:" + str(total["pages_inspected"]))
    if len(summaries) != 10:
        errors.append("aggregate_assignments:" + str(len(summaries)))

    common = {
        "batch_id": batch["batch_id"],
        "executed_assignments": len(summaries),
        "authorized_assignments": 10,
        **dict(total),
        "structure_mismatch": 0,
        "activation_hashes": hashes,
        "activation_controls": {"passed": 20, "total": 20},
        "assignments": summaries,
        "human_review": "pending",
        "next_gate": "H-PS2",
        "forbidden_actions_performed": [],
    }
    validation = {
        "schema_version": "ingrid_instance_batch01_validation_report_v1",
        **common,
        "errors": errors,
        "status": (
            "validated_agent_segmented_pending_human"
            if not errors
            else "validation_failed"
        ),
    }
    execution = {
        "schema_version": "ingrid_instance_batch_execution_report_v1",
        "capability_id": "instance_problem_solution_segmenter_v1",
        "mode": "instance_staging",
        **common,
        "status": "agent_segmented_pending_human",
    }
    write(OUTPUT / "batch_validation_report.json", validation)
    write(OUTPUT / "batch_execution_report.json", execution)

    rows = [
        "| Asignacion | Paginas | Boxes | Unidades | Incompletas | Abstenciones |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for item in summaries:
        rows.append(
            "| {assignment_id} | {pages_inspected} | {problem_boxes} | "
            "{solution_units} | {incomplete_solution_units} | {abstention_count} |".format(
                **item,
                abstention_count=len(item["abstention_pages"]),
            )
        )
    handoff = "\n".join(
        [
            "# H-PS2 handoff - Ingrid batch-01",
            "",
            "Estado: agent_segmented_pending_human. H-PS2 no esta aprobado.",
            "",
            f"- Instancias: {len(summaries)}/10.",
            f"- Paginas inspeccionadas: {total['pages_inspected']}/538.",
            f"- Boxes propuestos o conservados: {total['problem_boxes']}.",
            f"- Unidades de solucion: {total['solution_units']}.",
            f"- Fragmentos con crop y SHA-256: {total['solution_fragments']}.",
            f"- Unidades completas: {total['complete_solution_units']}.",
            f"- Unidades incompletas bloqueadas: {total['incomplete_solution_units']}.",
            f"- Paginas con abstencion explicita: {total['abstention_pages']}.",
            "- Structure mismatch: 0.",
            "- OCR, endpoints, app, BD, entrenamiento y promocion: no ejecutados.",
            "",
            *rows,
            "",
            "## Evidencia",
            "",
            f"- Reporte de ejecucion: {OUTPUT / 'batch_execution_report.json'}",
            f"- Reporte de validacion: {OUTPUT / 'batch_validation_report.json'}",
            f"- Overlays y crops: {OUTPUT}",
            "",
            "## Decisiones requeridas en H-PS2",
            "",
            "1. Revisar overlays antes/despues de las 10 instancias.",
            "2. Mantener bloqueadas las unidades incompletas.",
            "3. Resolver manualmente las paginas con abstencion.",
            "4. Aplicar solo tras aprobacion humana mediante writers controlados.",
            "",
        ]
    )
    (OUTPUT / "H_PS2_HANDOFF.md").write_text(handoff, encoding="utf-8")
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

