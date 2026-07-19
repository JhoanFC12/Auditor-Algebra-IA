from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from modulos.problem_detector_lab.server import (
    ProblemDetectorLabServer,
    audit_roles_for_content_roles,
    labels_semantically_equal,
    read_review_selection,
)
from tests.test_precision_annotation_contract import approved_precision_package


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _library_audit_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    catalog_root = tmp_path / "book_catalog"
    staging_root = catalog_root / "problem_solution_staging"
    campaign = staging_root / "euler-app-library-problem-solutions-test-r1"
    activation = campaign / "h_ps1_ingrid_activation_test_r1"
    assignment_id = "ingrid-ps-b190-i6235-r1"
    page_image = catalog_root / "books" / "book-test" / "pages" / "page-0007.png"
    page_image.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (800, 1000), color="white").save(page_image)
    ledger = catalog_root / "analysis_staging" / "run-test" / "book-test.book_page_structural_analysis.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        json.dumps(
            {
                "schema_version": "book_page_structural_analysis_v1",
                "analysis_run_id": "run-test",
                "page_number": 7,
                "content_roles": ["theory", "solved_problem"],
                "confidence": 0.87,
                "evidence": {
                    "image_asset_key": str(page_image),
                    "visible_headings": ["Problema resuelto"],
                    "notes": "Fixture legado",
                },
                "uncertainty_reasons": [],
                "review_status": "pending",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_json(
        campaign / "solution_eligibility_manifest.json",
        {
            "schema_version": "solution_eligibility_manifest_v1",
            "campaign_id": "campaign-test",
            "rows": [
                {
                    "app_book_id": 190,
                    "book_code": "book-test",
                    "title": "Álgebra de prueba",
                    "eligibility": "eligible",
                    "eligibility_basis": "Soluciones desarrolladas visibles.",
                    "solution_evidence": {"worked_solution": 1, "short_answer": 0, "hint": 0, "answer_key": 0},
                    "page_summary": {"page_count": 7, "problem_pages": 1, "developed_solution_pages": 1},
                    "reused_source": {"ledger_path": str(ledger)},
                }
            ],
        },
    )
    scope = {
        "book_code": "book-test",
        "book_id": 190,
        "instance_type": "unidad_prueba",
        "instance_id": 6235,
        "exercise_set_id": "exercise-test-r1",
    }
    _write_json(
        activation / "assignments" / "ingrid-b190-i6235.json",
        {
            "schema_version": "ingrid_instance_segmentation_assignment_v1",
            "assignment_id": assignment_id,
            "scope": scope,
            "expected_revision": 1,
            "context_fingerprint": "sha256:context-test",
            "source_document": {"pdf_sha256": "abc123", "page_count": 7},
            "approved_pages": [7],
            "problem_pages": [7],
            "solution_pages": [7],
            "structure_snapshot": {
                "map_id": "map-test",
                "map_revision": 0,
                "map_status": "handoff_ready",
                "structure_mode": "interleaved",
            },
            "h_ps1_gate_ref": {"status": "approved"},
        },
    )
    _write_json(
        activation / "maps" / "map-b190-i6235.json",
        {
            "schema_version": "gottfried_problem_solution_map_v1",
            "map_id": "map-test",
            "assignment_id": "map-test",
            "status": "handoff_ready",
            "map_revision": 0,
            "scope": scope,
            "problem_page_selection": {"selected_pages": [7]},
            "solution_page_selection": {"selected_pages": [7]},
            "context_fingerprint": "sha256:context-test",
        },
    )
    output_dir = activation / "ingrid_outputs" / "batch-01" / assignment_id
    overlay = output_dir / "overlays" / "page_0007_after.jpg"
    overlay.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (800, 1000), color="#eeeeee").save(overlay)
    _write_json(
        output_dir / "segmentation.json",
        {
            "schema_version": "ingrid_instance_segmentation_v1",
            "assignment_id": assignment_id,
            "scope": scope,
            "pages_inspected": [7],
            "problem_box_reviews": [
                {
                    "page_number": 7,
                    "image_width": 800,
                    "image_height": 1000,
                    "proposed_boxes": [
                        {"box_id": "problem-1", "role": "problem", "bbox_xyxy": [40, 100, 760, 480]},
                        {
                            "box_id": "number-1",
                            "role": "problem_number",
                            "bbox_xyxy": [50, 110, 120, 145],
                            "parent_box_id": "problem-1",
                        },
                    ],
                    "overlay_after": f"{assignment_id}/overlays/page_0007_after.jpg",
                    "status": "agent_corrected_pending_human",
                    "human_review": "pending",
                }
            ],
            "solution_units": [
                {
                    "unit_id": "solution-1",
                    "page_span": [7],
                    "continuation_complete": True,
                    "fragments": [
                        {"fragment_id": "fragment-1", "page_number": 7, "bbox_xyxy": [40, 510, 760, 930]}
                    ],
                }
            ],
            "issues_found": [],
            "inspection_log": [
                {
                    "page_number": 7,
                    "problem_box_count": 1,
                    "solution_fragment_count": 1,
                    "disposition": "segmented",
                }
            ],
            "status": "agent_segmented_pending_human",
            "human_review": "pending",
            "next_gate": "H-PS2",
        },
    )
    return staging_root, catalog_root, campaign


def _session_fingerprint(payload: dict) -> str:
    canonical = {key: value for key, value in payload.items() if key != "session_fingerprint"}
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _pre_hps1_visual_session_fixture(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    catalog_root = tmp_path / "book_catalog"
    staging_root = catalog_root / "problem_solution_staging"
    pilot_root = staging_root / "euler-precision-pilot-test-r1"
    mapping_root = pilot_root / "gottfried_mapping_v2"
    structural_root = pilot_root / "gottfried_structural_v2"
    assignment_root = structural_root / "assignments" / "gottfried-struct-test"
    session_id = "pdl-hps1-map-test-r0"

    images: dict[int, Path] = {}
    for page_number, color in ((7, "#f5f1e8"), (8, "#edf4f8")):
        image_path = structural_root / "evidence" / "book-test" / f"p{page_number:04d}.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (800, 1000), color=color).save(image_path)
        images[page_number] = image_path

    metric_one = {
        "estimate": 1,
        "minimum_estimate": 1,
        "maximum_estimate": 1,
        "confidence": 0.96,
        "evidence": ["unidad visible"],
    }
    metric_zero = {**metric_one, "estimate": 0, "minimum_estimate": 0, "maximum_estimate": 0}
    structural_rows = [
        {
            "schema_version": "book_page_structural_analysis_v2",
            "analysis_run_id": "structural-test-r1",
            "page_number": 7,
            "content_roles": ["proposed_problem"],
            "audit_roles": {
                "schema_version": "library_page_audit_roles_v1",
                "mapping_version": "page_role_mapping_v1",
                "roles": ["problem"],
            },
            "page_sections": [
                {
                    "section_id": "sec-p0007-001",
                    "geometry_kind": "coarse_rect",
                    "coordinate_space": "normalized_0_1",
                    "bbox_norm_xyxy": [0.08, 0.12, 0.92, 0.82],
                    "precision": "coarse",
                    "content_roles": ["proposed_problem"],
                    "audit_roles": ["problem"],
                    "reading_order": 1,
                    "confidence": 0.96,
                    "evidence": ["problema 01 visible"],
                    "uncertainty_reasons": [],
                    "usable_as_final_box": False,
                }
            ],
            "page_statistics": {
                "schema_version": "library_page_statistics_v1",
                "problem_units": metric_one,
                "proposed_problems": metric_one,
                "solved_problems": metric_zero,
                "solution_units": metric_zero,
                "worked_examples": metric_zero,
                "other_elements": [],
                "validations": {
                    "problem_partition_ok": "pass",
                    "solution_count_valid": "pass",
                    "statistics_consistent": "pass",
                },
            },
            "confidence": 0.96,
            "evidence": {"image_asset_key": str(images[7]), "notes": "problema editorial 01"},
            "uncertainty_reasons": [],
            "review_status": "pending",
        },
        {
            "schema_version": "book_page_structural_analysis_v2",
            "analysis_run_id": "structural-test-r1",
            "page_number": 8,
            "content_roles": ["solution"],
            "audit_roles": {
                "schema_version": "library_page_audit_roles_v1",
                "mapping_version": "page_role_mapping_v1",
                "roles": ["solution"],
            },
            "page_sections": [
                {
                    "section_id": "sec-p0008-001",
                    "geometry_kind": "coarse_rect",
                    "coordinate_space": "normalized_0_1",
                    "bbox_norm_xyxy": [0.1, 0.1, 0.9, 0.9],
                    "precision": "coarse",
                    "content_roles": ["solution"],
                    "audit_roles": ["solution"],
                    "reading_order": 1,
                    "confidence": 0.94,
                    "evidence": ["solucion 01 visible"],
                    "uncertainty_reasons": ["limite inferior aproximado"],
                    "usable_as_final_box": False,
                }
            ],
            "page_statistics": {
                "schema_version": "library_page_statistics_v1",
                "problem_units": metric_zero,
                "proposed_problems": metric_zero,
                "solved_problems": metric_zero,
                "solution_units": metric_one,
                "worked_examples": metric_zero,
                "other_elements": [],
                "validations": {
                    "problem_partition_ok": "pass",
                    "solution_count_valid": "pass",
                    "statistics_consistent": "pass",
                },
            },
            "confidence": 0.94,
            "evidence": {"image_asset_key": str(images[8]), "notes": "solucion editorial 01"},
            "uncertainty_reasons": ["limite inferior aproximado"],
            "review_status": "pending",
        },
    ]
    pages_path = assignment_root / "pages.jsonl"
    pages_path.parent.mkdir(parents=True, exist_ok=True)
    pages_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in structural_rows) + "\n",
        encoding="utf-8",
    )

    scope = {
        "book_code": "book-test",
        "book_id": 190,
        "instance_type": "unidad_prueba",
        "instance_id": 6235,
        "exercise_set_id": "exercise-test-r1",
    }
    map_id = "psmap-v2-book-test-r1"
    problem_ref = f"{map_id}:r0:exercise-test-r1:P001"
    solution_ref = f"{map_id}:r0:exercise-test-r1:S001"
    map_payload = {
        "schema_version": "gottfried_problem_solution_map_v2",
        "map_id": map_id,
        "assignment_id": "gottfried-map-test-r1",
        "status": "mapping_requires_human",
        "map_revision": 0,
        "scope": scope,
        "source": {
            "pdf_path": str(catalog_root / "private" / "book-test.pdf"),
            "pdf_sha256": "a" * 64,
            "page_count": 8,
        },
        "eligibility_ref": {
            "eligibility_id": "elig-test-r1",
            "status": "eligible_partial",
            "context_fingerprint": "b" * 64,
            "can_generate_map": True,
            "should_generate_now": True,
        },
        "page_role_manifest_ref": {
            "schema_version": "book_page_structural_analysis_v2",
            "analysis_run_id": "structural-test-r1",
            "mapping_version": "page_role_mapping_v1",
            "artifact_path": str(pages_path),
            "pdf_sha256": "a" * 64,
            "page_count": 8,
            "context_fingerprint": "b" * 64,
        },
        "page_role_snapshot": [
            {
                "page_number": row["page_number"],
                "content_roles": row["content_roles"],
                "audit_roles": row["audit_roles"]["roles"],
                "page_sections_ref": {
                    "artifact_path": str(pages_path),
                    "record_selector": {"page_number": row["page_number"]},
                    "section_ids": [row["page_sections"][0]["section_id"]],
                    "precision": "coarse",
                    "usable_as_final_box": False,
                },
                "page_statistics_ref": {
                    "artifact_path": str(pages_path),
                    "record_selector": {"page_number": row["page_number"]},
                    "schema_version": "library_page_statistics_v1",
                },
                "confidence": row["confidence"],
                "evidence": [f"PDF p.{row['page_number']} inspeccionada"],
                "uncertainty_reasons": row["uncertainty_reasons"],
            }
            for row in structural_rows
        ],
        "problem_page_selection": {"pages": [7], "review_status": "pending"},
        "solution_page_selection": {"pages": [8], "review_status": "pending"},
        "problem_solution_structure": {
            "structure_type": "separate_sections",
            "exercise_set_id": "exercise-test-r1",
            "problem_units": 1,
            "solution_units": 1,
            "exact_pairs": 1,
            "source_mapping_confirmed": False,
        },
        "provisional_units": [
            {
                "provisional_unit_id": "P001",
                "provisional_unit_ref": problem_ref,
                "unit_kind": "problem",
                "editorial_number_raw": "01",
                "editorial_number_normalized": "1",
                "source_pages": [7],
                "source_section_ids": ["sec-p0007-001"],
                "reading_order": 1,
                "confidence": 0.96,
                "evidence": ["problema 01 visible"],
                "unit_fingerprint": "c" * 64,
                "compatibility_status": "new",
            },
            {
                "provisional_unit_id": "S001",
                "provisional_unit_ref": solution_ref,
                "unit_kind": "solution",
                "editorial_number_raw": "01",
                "editorial_number_normalized": "1",
                "source_pages": [8],
                "source_section_ids": ["sec-p0008-001"],
                "reading_order": 1,
                "confidence": 0.94,
                "evidence": ["solucion 01 visible"],
                "unit_fingerprint": "d" * 64,
                "compatibility_status": "new",
            },
        ],
        "problem_solution_relations": [
            {
                "relation_id": "R001",
                "relation_type": "one_to_one",
                "problem_provisional_unit_ref": problem_ref,
                "solution_provisional_unit_ref": solution_ref,
                "editorial_number_raw": "01",
                "editorial_number_normalized": "1",
                "match_basis": "numero editorial identico",
                "confidence": 0.95,
                "evidence": ["P001 y S001 muestran 01"],
                "review_status": "pending",
                "relation_fingerprint": "e" * 64,
            }
        ],
        "document_relation": None,
        "evidence": ["mapa de prueba"],
        "uncertainties": [{"code": "coarse_only", "pages": [8]}],
        "human_decisions_required": ["H-PS1 pendiente"],
        "review_status": "pending",
        "gates": {
            "h_ps1": "pending",
            "activate_ingrid": False,
            "handoff_ready": False,
            "canonical_persistence": "not_written",
        },
        "mutations": {
            "app_writes": 0,
            "api_writes": 0,
            "db_writes": 0,
            "pdf_mutations": 0,
            "ingrid_activations": 0,
            "boxes_created": 0,
            "crops_created": 0,
        },
        "scope_fingerprint": "f" * 64,
        "context_fingerprint": "1" * 64,
    }
    map_path = mapping_root / "maps" / f"{map_id}.json"
    _write_json(map_path, map_payload)
    map_sha256 = hashlib.sha256(map_path.read_bytes()).hexdigest()
    _write_json(
        mapping_root / "artifact_hashes.json",
        {
            "schema_version": "artifact_hash_manifest_v1",
            "batch_id": "visual-test-batch-r1",
            "algorithm": "sha256",
            "artifacts": [
                {"path": f"maps/{map_id}.json", "bytes": map_path.stat().st_size, "sha256": map_sha256}
            ],
        },
    )
    _write_json(
        mapping_root / "bundle_manifest.json",
        {
            "schema_version": "gottfried_mapping_v2_bundle_manifest_v1",
            "batch_id": "visual-test-batch-r1",
            "agent_id": "gottfried_leibniz_v1",
            "status": "mapping_requires_human",
            "structural_source_root": str(structural_root),
            "maps": [
                {
                    "map_id": map_id,
                    "status": "mapping_requires_human",
                    "map_revision": 0,
                    "map_path": str(map_path),
                    "approved_pages": [7, 8],
                    "problem_units": 1,
                    "solution_units": 1,
                    "exact_pairs": 1,
                }
            ],
            "mutations": {
                "app_writes": 0,
                "api_writes": 0,
                "db_writes": 0,
                "pdf_mutations": 0,
                "ingrid_activations": 0,
            },
        },
    )
    session_payload = {
        "schema_version": "problem_detector_visual_audit_session_v1",
        "session_id": session_id,
        "batch_id": "visual-test-batch-r1",
        "stage": "pre_h_ps1",
        "status": "ready_for_visual_audit",
        "created_by": {
            "agent_id": "gottfried_leibniz_v1",
            "capability_id": "book_problem_solution_mapper_v1",
        },
        "scope": scope,
        "map_ref": {
            "map_id": map_id,
            "map_revision": 0,
            "map_sha256": map_sha256,
            "scope_fingerprint": "f" * 64,
            "context_fingerprint": "1" * 64,
        },
        "source_ref": {"pdf_sha256": "a" * 64, "page_count": 8},
        "page_numbers": [7, 8],
        "problem_provisional_unit_refs": [problem_ref],
        "solution_provisional_unit_refs": [solution_ref],
        "relation_ids": ["R001"],
        "counts": {"pages": 2, "problems": 1, "solutions": 1, "relations": 1},
        "gates": {"h_ps1": "pending", "activate_ingrid": False, "handoff_ready": False},
        "permissions": {
            "read_only": True,
            "canonical_writes": False,
            "boxes_or_crops": False,
            "map_mutation": False,
            "pdf_mutation": False,
        },
        "review": {"status": "pending", "predecessor_session_id": None},
    }
    session_payload["session_fingerprint"] = _session_fingerprint(session_payload)
    _write_json(mapping_root / "visual_audit_sessions" / session_id / "session.json", session_payload)
    return staging_root, catalog_root, map_path, session_id


def test_labels_semantically_equal_ignores_line_endings_and_spacing(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.txt"
    current = tmp_path / "current.txt"
    baseline.write_bytes(b"0 0.5 0.5 0.2 0.2\r\n2 0.4 0.7 0.3 0.1\r\n")
    current.write_bytes(b"0  0.5 0.5 0.2 0.2\n2 0.4 0.7 0.3 0.1\n")

    assert labels_semantically_equal(baseline, current)

    current.write_text("0 0.5 0.5 0.2 0.2\n2 0.4 0.7 0.4 0.1\n", encoding="utf-8")
    assert not labels_semantically_equal(baseline, current)


def test_active_review_manifest_and_human_gate(tmp_path: Path) -> None:
    dataset = tmp_path / "problem_detector_multiclass_ingrid_review_test"
    reviews = dataset / "reviews"
    batches = reviews / "batches_50"
    batches.mkdir(parents=True)
    manifest = batches / "human_review_13.json"
    manifest.write_text(
        json.dumps(
            {
                "queue_id": "human-review-13",
                "rows": [
                    {"split": "train", "sample_id": "sample_a", "order": 1},
                    {"split": "val", "sample_id": "sample_b", "order": 2},
                ],
            }
        ),
        encoding="utf-8",
    )
    (batches / "active_batch.json").write_text(
        json.dumps({"manifest": str(manifest)}),
        encoding="utf-8",
    )
    (reviews / "train").mkdir()
    (reviews / "val").mkdir()
    (reviews / "train" / "sample_a.json").write_text(
        json.dumps({"status": "human_approved", "human_review": "approved"}),
        encoding="utf-8",
    )
    (reviews / "val" / "sample_b.json").write_text(
        json.dumps({"status": "agent_corrected_pending_human", "human_review": "pending"}),
        encoding="utf-8",
    )

    selection = read_review_selection(reviews)
    gate = ProblemDetectorLabServer(dataset_root=dataset)._human_approval_gate()

    assert [row["sample_id"] for row in selection["rows"]] == ["sample_a", "sample_b"]
    assert gate["approved_total"] == 1
    assert gate["pending_total"] == 1
    assert gate["status"] == "pending_human_review"


def test_approve_sample_persists_review_and_marks_queue_ready(tmp_path: Path) -> None:
    dataset = tmp_path / "problem_detector_multiclass_ingrid_review_test"
    for relative in (
        "images/train",
        "labels/train",
        "baseline_labels/train",
        "metadata/train",
        "reviews/train",
        "reviews/batches_50",
    ):
        (dataset / relative).mkdir(parents=True, exist_ok=True)
    sample_id = "sample_ready"
    Image.new("RGB", (100, 100), color="white").save(dataset / "images" / "train" / f"{sample_id}.png")
    (dataset / "baseline_labels" / "train" / f"{sample_id}.txt").write_text(
        "0 0.500000 0.500000 0.500000 0.500000\n",
        encoding="utf-8",
    )
    (dataset / "labels" / "train" / f"{sample_id}.txt").write_text(
        "0 0.500000 0.500000 0.400000 0.400000\n",
        encoding="utf-8",
    )
    (dataset / "metadata" / "train" / f"{sample_id}.json").write_text("{}", encoding="utf-8")
    (dataset / "reviews" / "train" / f"{sample_id}.json").write_text(
        json.dumps({"status": "agent_corrected_pending_human", "human_review": "pending"}),
        encoding="utf-8",
    )
    manifest = dataset / "reviews" / "batches_50" / "human_review_13.json"
    manifest.write_text(
        json.dumps(
            {
                "queue_id": "human-review-ready",
                "rows": [{"split": "train", "sample_id": sample_id, "order": 1}],
            }
        ),
        encoding="utf-8",
    )
    (manifest.parent / "active_batch.json").write_text(
        json.dumps({"manifest": str(manifest)}),
        encoding="utf-8",
    )

    result = ProblemDetectorLabServer(dataset_root=dataset)._approve_sample(
        {"sample_id": sample_id, "split": "train"}
    )
    persisted = json.loads((dataset / "reviews" / "train" / f"{sample_id}.json").read_text(encoding="utf-8"))
    gate = json.loads((dataset / "reviews" / "human_approval_gate.json").read_text(encoding="utf-8"))

    assert persisted["status"] == "human_approved"
    assert persisted["human_review"] == "approved"
    assert persisted["training_candidate"] is True
    assert result["approval_gate"]["status"] == "ready_for_database"
    assert gate["approved_total"] == 1
    assert gate["pending_total"] == 0


def test_audit_role_mapping_is_versioned_and_multilabel() -> None:
    result = audit_roles_for_content_roles(["theory", "worked_example", "solved_problem", "answer_key"])

    assert result == {
        "schema_version": "library_page_audit_roles_v1",
        "mapping_version": "page_role_mapping_v1",
        "roles": ["theory", "problem", "solution"],
        "source": "derived",
    }


def test_library_audit_normalizes_legacy_artifacts_and_uses_opaque_media_tokens(tmp_path: Path) -> None:
    staging_root, catalog_root, _campaign = _library_audit_fixture(tmp_path)
    dataset = tmp_path / "problem_detector_multiclass_ingrid_review_test"
    server = ProblemDetectorLabServer(
        dataset_root=dataset,
        library_audit_root=staging_root,
        library_media_root=catalog_root,
    )

    catalog = server._library_audit_catalog_payload()
    detail = server._library_audit_instance_payload("ingrid-ps-b190-i6235-r1")
    page = detail["pages"][0]

    assert catalog["summary"]["assignment_count"] == 1
    assert catalog["summary"]["ingrid_ready_count"] == 1
    assert catalog["instances"][0]["title"] == "Álgebra de prueba"
    assert catalog["instances"][0]["h_ps1_status"] == "approved"
    assert catalog["instances"][0]["h_ps2_status"] == "pending"
    assert detail["read_only"] is True
    assert detail["canonical_writes"] == "disabled"
    assert page["content_roles"] == ["theory", "solved_problem"]
    assert page["audit_roles"]["roles"] == ["theory", "problem", "solution"]
    assert page["audit_roles"]["mapping_version"] == "page_role_mapping_v1"
    assert page["page_sections"] == []
    assert page["page_statistics"] is None
    assert page["observed_counts"]["problem_boxes"] == 1
    assert page["observed_counts"]["solution_units"] == 1
    assert {box["role"] for box in page["precise_boxes"]} == {"problem", "problem_number", "solution"}
    assert page["traceability"]["status"] == "legacy_missing_provisional_links"
    assert page["image_url"].startswith("/api/library-audit/media?token=")
    assert page["overlay_after_url"].startswith("/api/library-audit/media?token=")
    serialized = json.dumps({"catalog": catalog, "detail": detail}, ensure_ascii=False)
    assert str(tmp_path) not in serialized
    token = page["image_url"].split("token=", 1)[1]
    assert server._audit_media_path(token).is_file()


def test_library_audit_rejects_media_outside_catalog_root(tmp_path: Path) -> None:
    staging_root, catalog_root, _campaign = _library_audit_fixture(tmp_path)
    outside = tmp_path / "outside.png"
    Image.new("RGB", (20, 20), color="white").save(outside)
    server = ProblemDetectorLabServer(
        dataset_root=tmp_path / "dataset",
        library_audit_root=staging_root,
        library_media_root=catalog_root,
    )

    try:
        server._register_audit_media(outside)
    except ValueError as exc:
        assert "fuera" in str(exc).lower()
    else:  # pragma: no cover - safety assertion
        raise AssertionError("Se acepto media fuera del catalogo permitido")


def test_library_audit_preserves_v2_coarse_statistics_and_provisional_traceability(tmp_path: Path) -> None:
    staging_root, catalog_root, campaign = _library_audit_fixture(tmp_path)
    ledger = catalog_root / "analysis_staging" / "run-test" / "book-test.book_page_structural_analysis.jsonl"
    metric = {"estimate": 1, "minimum_estimate": 1, "maximum_estimate": 1, "confidence": 0.9, "evidence": ["visible"]}
    ledger.write_text(
        json.dumps(
            {
                "schema_version": "book_page_structural_analysis_v2",
                "analysis_run_id": "run-test-v2",
                "page_number": 7,
                "content_roles": ["theory", "solved_problem"],
                "audit_roles": {
                    "schema_version": "library_page_audit_roles_v1",
                    "mapping_version": "page_role_mapping_v1",
                    "roles": ["theory", "problem", "solution"],
                },
                "page_sections": [
                    {
                        "section_id": "sec-p0007-001",
                        "bbox_norm_xyxy": [0.05, 0.1, 0.95, 0.9],
                        "content_roles": ["solved_problem"],
                        "audit_roles": ["problem", "solution"],
                        "reading_order": 1,
                        "confidence": 0.88,
                        "evidence": ["distribución visible"],
                    }
                ],
                "page_statistics": {
                    "schema_version": "library_page_statistics_v1",
                    "problem_units": metric,
                    "proposed_problems": {**metric, "estimate": 0, "minimum_estimate": 0, "maximum_estimate": 0},
                    "solved_problems": metric,
                    "solution_units": metric,
                    "worked_examples": {**metric, "estimate": 0, "minimum_estimate": 0, "maximum_estimate": 0},
                    "other_elements": [],
                    "validations": {
                        "problem_partition_ok": "pass",
                        "solution_count_valid": "pass",
                        "statistics_consistent": "pass",
                    },
                },
                "confidence": 0.91,
                "evidence": {"image_asset_key": str(catalog_root / "books" / "book-test" / "pages" / "page-0007.png")},
                "uncertainty_reasons": [],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    activation = campaign / "h_ps1_ingrid_activation_test_r1"
    map_path = activation / "maps" / "map-b190-i6235.json"
    map_payload = json.loads(map_path.read_text(encoding="utf-8"))
    map_payload["schema_version"] = "gottfried_problem_solution_map_v2"
    map_payload["provisional_units"] = [
        {"provisional_unit_id": "P001", "unit_kind": "problem"},
        {"provisional_unit_id": "S001", "unit_kind": "solution"},
    ]
    _write_json(map_path, map_payload)
    segmentation_path = activation / "ingrid_outputs" / "batch-01" / "ingrid-ps-b190-i6235-r1" / "segmentation.json"
    segmentation = json.loads(segmentation_path.read_text(encoding="utf-8"))
    segmentation["problem_box_reviews"][0]["source_provisional_unit_ids"] = ["P001"]
    segmentation["problem_box_reviews"][0]["provisional_refinement"] = {"relation_type": "boundary_adjustment"}
    segmentation["solution_units"][0]["source_provisional_unit_ids"] = ["S001"]
    segmentation["solution_units"][0]["provisional_refinement"] = {"relation_type": "exact"}
    _write_json(segmentation_path, segmentation)
    server = ProblemDetectorLabServer(
        dataset_root=tmp_path / "dataset",
        library_audit_root=staging_root,
        library_media_root=catalog_root,
    )

    detail = server._library_audit_instance_payload("ingrid-ps-b190-i6235-r1")
    page = detail["pages"][0]

    assert page["audit_roles"]["source"] == "explicit"
    assert page["page_sections"][0]["precision"] == "coarse"
    assert page["page_sections"][0]["usable_as_final_box"] is False
    assert page["page_statistics"]["source"] == "gottfried"
    assert page["page_statistics"]["canonical"] is False
    assert page["traceability"]["status"] == "linked_to_provisional_units"
    assert page["traceability"]["source_provisional_unit_ids"] == ["P001", "S001"]
    assert page["traceability"]["relation_types"] == ["boundary_adjustment", "exact"]
    assert detail["map"]["provisional_unit_count"] == 2


def _precision_package_for_page(page_number: int) -> dict:
    payload = copy.deepcopy(approved_precision_package())
    payload["document"]["page_count"] = page_number
    payload["page"]["page_number"] = page_number
    for region in payload["regions"]:
        region["page_number"] = page_number
    for unit in payload["units"]:
        unit["source_pages"] = [page_number]
    for relation in payload["relations"]:
        relation["source_pages"] = [page_number]
        relation["target_pages"] = [page_number]
    return payload


def test_library_audit_validates_v2_precision_and_exposes_answer_blocks(tmp_path: Path) -> None:
    staging_root, catalog_root, campaign = _library_audit_fixture(tmp_path)
    activation = campaign / "h_ps1_ingrid_activation_test_r1"
    segmentation_path = activation / "ingrid_outputs" / "batch-01" / "ingrid-ps-b190-i6235-r1" / "segmentation.json"
    segmentation = json.loads(segmentation_path.read_text(encoding="utf-8"))
    precision = _precision_package_for_page(7)
    segmentation["precision_annotation"] = precision
    _write_json(segmentation_path, segmentation)
    server = ProblemDetectorLabServer(
        dataset_root=tmp_path / "dataset",
        library_audit_root=staging_root,
        library_media_root=catalog_root,
    )

    validation = server._library_precision_validation({"annotation": precision})
    detail = server._library_audit_instance_payload("ingrid-ps-b190-i6235-r1")
    page = detail["pages"][0]

    assert validation["read_only"] is True
    assert validation["canonical_writes"] == "disabled"
    assert validation["h_ps2_ready"] is True
    assert detail["precision_validation"]["h_ps2_ready"] is True
    assert page["precision_validation"]["h_ps2_ready"] is True
    assert page["precision_validation"]["summary"]["answer_block_count"] == 2
    assert "answer_block" in {box["role"] for box in page["precise_boxes"]}


def test_library_precision_validation_reports_blockers_without_persisting(tmp_path: Path) -> None:
    precision = _precision_package_for_page(7)
    precision["regions"][-1]["geometry_quality"]["checks"]["foreign_content_excluded"] = "fail"
    server = ProblemDetectorLabServer(dataset_root=tmp_path / "dataset")

    result = server._library_precision_validation({"annotation": precision})

    assert result["h_ps2_ready"] is False
    assert "region:solution-region-1:quality:foreign_content_excluded:fail" in result["issues"]
    assert result["persisted"] is False


def test_library_audit_tab_is_separate_and_hps2_controls_are_guarded() -> None:
    web_root = Path(__file__).resolve().parents[1] / "modulos" / "problem_detector_lab" / "web"
    html = (web_root / "index.html").read_text(encoding="utf-8")
    javascript = (web_root / "app.js").read_text(encoding="utf-8")

    assert 'id="datasetView"' in html
    assert 'id="libraryAuditView"' in html
    assert 'id="libraryAuditTab"' in html
    assert 'id="auditPageStage"' in html
    assert 'id="auditApprovePageBtn"' in html
    assert 'id="auditPrecisionQuality"' in html
    assert "readOnly" in javascript
    assert "/api/library-audit" in javascript
    assert "/api/library-audit/precision/validate" in javascript
    assert "answer_block" in javascript
    assert "h_ps2_ready" in javascript
    assert "/api/pages/boxes" not in javascript


def test_pre_hps1_visual_session_revalidates_and_exposes_exact_read_only_map(tmp_path: Path) -> None:
    staging_root, catalog_root, _map_path, session_id = _pre_hps1_visual_session_fixture(tmp_path)
    server = ProblemDetectorLabServer(
        dataset_root=tmp_path / "dataset",
        library_audit_root=staging_root,
        library_media_root=catalog_root,
    )

    catalog = server._library_visual_audit_catalog_payload()
    detail = server._library_visual_audit_session_payload(session_id)
    relation = detail["relations"][0]

    assert catalog["stage"] == "pre_h_ps1"
    assert catalog["summary"] == {
        "session_count": 1,
        "ready_count": 1,
        "blocked_count": 0,
        "page_count": 2,
        "relation_count": 1,
    }
    assert detail["session"]["status"] == "ready_for_visual_audit"
    assert detail["integrity"]["status"] == "passed"
    assert detail["map"]["map_revision"] == 0
    assert len(detail["map"]["map_sha256"]) == 64
    assert detail["gates"] == {
        "h_ps1": "pending",
        "activate_ingrid": False,
        "handoff_ready": False,
    }
    assert relation["relation_id"] == "R001"
    assert relation["problem"]["provisional_unit_id"] == "P001"
    assert relation["solution"]["provisional_unit_id"] == "S001"
    assert relation["review_status"] == "pending"
    assert detail["pages"][0]["page_sections"][0]["precision"] == "coarse"
    assert detail["pages"][0]["page_sections"][0]["usable_as_final_box"] is False
    assert all(page["image_url"].startswith("/api/library-audit/media?token=") for page in detail["pages"])
    assert all(page["precise_boxes"] == [] for page in detail["pages"])
    serialized = json.dumps({"catalog": catalog, "detail": detail}, ensure_ascii=False)
    assert str(tmp_path) not in serialized
    assert "pdf_path" not in serialized


def test_pre_hps1_visual_session_blocks_live_map_hash_mismatch(tmp_path: Path) -> None:
    staging_root, catalog_root, map_path, session_id = _pre_hps1_visual_session_fixture(tmp_path)
    tampered = json.loads(map_path.read_text(encoding="utf-8"))
    tampered["uncertainties"].append({"code": "tampered_after_materialization"})
    _write_json(map_path, tampered)
    server = ProblemDetectorLabServer(
        dataset_root=tmp_path / "dataset",
        library_audit_root=staging_root,
        library_media_root=catalog_root,
    )

    catalog = server._library_visual_audit_catalog_payload()
    detail = server._library_visual_audit_session_payload(session_id)

    assert catalog["summary"]["ready_count"] == 0
    assert catalog["summary"]["blocked_count"] == 1
    assert catalog["sessions"][0]["status"] == "visual_audit_blocked"
    assert "map_sha256_mismatch" in catalog["sessions"][0]["blockers"]
    assert detail["session"]["status"] == "visual_audit_blocked"
    assert detail["integrity"]["status"] == "failed"
    assert detail["relations"] == []


def test_pre_hps1_visual_session_blocks_revision_mismatch_even_with_valid_session_fingerprint(tmp_path: Path) -> None:
    staging_root, catalog_root, map_path, session_id = _pre_hps1_visual_session_fixture(tmp_path)
    session_path = map_path.parent.parent / "visual_audit_sessions" / session_id / "session.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    session["map_ref"]["map_revision"] = 1
    session["session_fingerprint"] = _session_fingerprint(session)
    _write_json(session_path, session)
    server = ProblemDetectorLabServer(
        dataset_root=tmp_path / "dataset",
        library_audit_root=staging_root,
        library_media_root=catalog_root,
    )

    detail = server._library_visual_audit_session_payload(session_id)

    assert detail["session"]["status"] == "visual_audit_blocked"
    assert "map_revision_mismatch" in detail["integrity"]["blockers"]
    assert "session_fingerprint_mismatch" not in detail["integrity"]["blockers"]
    assert detail["pages"] == []
    assert detail["relations"] == []


def test_pre_hps1_visual_ui_is_side_by_side_and_has_no_mutating_gate_route() -> None:
    web_root = Path(__file__).resolve().parents[1] / "modulos" / "problem_detector_lab" / "web"
    html = (web_root / "index.html").read_text(encoding="utf-8")
    javascript = (web_root / "app.js").read_text(encoding="utf-8")

    assert 'id="auditStageSelect"' in html
    assert 'id="auditSessionSelect"' in html
    assert 'id="auditRelationList"' in html
    assert 'id="auditRelationProblemPane"' in html
    assert 'id="auditRelationSolutionPane"' in html
    assert 'id="auditSessionHashes"' in html
    assert 'id="auditWorkflowHps1Step"' in html
    assert "/api/library-audit/sessions" in javascript
    assert "/api/library-audit/session" in javascript
    assert "ready_for_visual_audit" in javascript
    assert "visual_audit_blocked" in javascript
    assert 'workflowHps1.textContent = "H-PS1 pendiente"' in javascript
    assert 'workflowHps1.className = "workflow-step pending"' in javascript
    assert "Ã" not in html
    assert "â€”" not in html
    assert "/api/library-audit/session/review" not in javascript
    assert "/api/pages/boxes" not in javascript


def test_library_audit_controls_have_responsive_nonoverlap_contract() -> None:
    web_root = Path(__file__).resolve().parents[1] / "modulos" / "problem_detector_lab" / "web"
    css = (web_root / "styles.css").read_text(encoding="utf-8")

    assert "flex: 1 1 160px;" in css
    assert "overflow-wrap: anywhere;" in css
    assert "grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));" in css
    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in css
    assert "@media (max-width: 1080px)" in css
    assert "@media (max-width: 640px)" in css


def test_library_audit_sidebar_uses_single_scroll_without_collapsing_lists() -> None:
    web_root = Path(__file__).resolve().parents[1] / "modulos" / "problem_detector_lab" / "web"
    css = (web_root / "styles.css").read_text(encoding="utf-8")

    sidebar_rule = css.split(".audit-sidebar {", 1)[1].split("}", 1)[0]
    page_list_rule = css.split(".audit-page-list {", 1)[1].split("}", 1)[0]
    relation_list_rule = css.split(".audit-relation-list {", 1)[1].split("}", 1)[0]
    relation_section_rule = css.split("#auditRelationSection {", 1)[1].split("}", 1)[0]

    assert "overflow-y: auto;" in sidebar_rule
    assert "overflow-x: hidden;" in sidebar_rule
    assert "scrollbar-gutter: stable;" in sidebar_rule
    for rule in (page_list_rule, relation_list_rule):
        assert "flex: none;" in rule
        assert "max-height: none;" in rule
        assert "overflow: visible;" in rule
    assert "flex: none;" in relation_section_rule
    assert "overflow: visible;" in relation_section_rule


class ProblemDetectorLibraryAuditTests(unittest.TestCase):
    """Unittest wrappers keep this contract executable without a pytest dependency."""

    def test_existing_label_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_labels_semantically_equal_ignores_line_endings_and_spacing(Path(temp_dir))

    def test_existing_active_review_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_active_review_manifest_and_human_gate(Path(temp_dir))

    def test_existing_approval_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_approve_sample_persists_review_and_marks_queue_ready(Path(temp_dir))

    def test_role_mapping(self) -> None:
        test_audit_role_mapping_is_versioned_and_multilabel()

    def test_legacy_adapter_and_media_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_library_audit_normalizes_legacy_artifacts_and_uses_opaque_media_tokens(Path(temp_dir))

    def test_media_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_library_audit_rejects_media_outside_catalog_root(Path(temp_dir))

    def test_v2_contract_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_library_audit_preserves_v2_coarse_statistics_and_provisional_traceability(Path(temp_dir))

    def test_precision_validation_and_answer_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_library_audit_validates_v2_precision_and_exposes_answer_blocks(Path(temp_dir))

    def test_precision_validation_is_non_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_library_precision_validation_reports_blockers_without_persisting(Path(temp_dir))

    def test_static_contract(self) -> None:
        test_library_audit_tab_is_separate_and_hps2_controls_are_guarded()

    def test_pre_hps1_visual_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_pre_hps1_visual_session_revalidates_and_exposes_exact_read_only_map(Path(temp_dir))

    def test_pre_hps1_visual_session_integrity_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_pre_hps1_visual_session_blocks_live_map_hash_mismatch(Path(temp_dir))

    def test_pre_hps1_visual_session_revision_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_pre_hps1_visual_session_blocks_revision_mismatch_even_with_valid_session_fingerprint(Path(temp_dir))

    def test_pre_hps1_visual_ui_contract(self) -> None:
        test_pre_hps1_visual_ui_is_side_by_side_and_has_no_mutating_gate_route()

    def test_responsive_controls_contract(self) -> None:
        test_library_audit_controls_have_responsive_nonoverlap_contract()

    def test_sidebar_single_scroll_contract(self) -> None:
        test_library_audit_sidebar_uses_single_scroll_without_collapsing_lists()
