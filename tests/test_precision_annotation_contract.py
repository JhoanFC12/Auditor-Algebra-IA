from __future__ import annotations

import copy
import unittest

from modulos.instance_factory.annotation_contracts import validate_annotation_package
from modulos.instance_factory.annotation_quality import evaluate_precision_annotation


def _quality(*, alternatives: str = "not_applicable", continuation: str = "not_applicable") -> dict:
    return {
        "schema_version": "ingrid_geometry_quality_v1",
        "checks": {
            "content_complete": "pass",
            "foreign_content_excluded": "pass",
            "unit_boundary_valid": "pass",
            "alternatives_complete": alternatives,
            "visible_identifier_captured": "pass",
            "continuation_supported": continuation,
            "geometry_precise": "pass",
        },
        "warnings": [],
        "inclusion_exceptions": [],
        "evidence": ["human visual inspection"],
        "confidence": 0.98,
        "status": "reviewed",
        "human_review": "approved",
    }


def _region(
    region_id: str,
    unit_id: str,
    region_class: str,
    bbox: list[float],
    *,
    page_number: int = 1,
    labels: list[str] | None = None,
    quality: dict | None = None,
) -> dict:
    return {
        "region_id": region_id,
        "annotation_unit_id": unit_id,
        "document_id": "doc-1",
        "page_number": page_number,
        "region_class": region_class,
        "bbox_norm_xyxy": bbox,
        "bbox_xyxy": [value * 1000 for value in bbox],
        "reading_order": 1,
        "column_index": 0,
        "content_members": {"alternative_labels": list(labels or [])},
        "geometry_quality": quality or _quality(),
        "confidence": 0.98,
        "contract_version": "precision-annotation-v1",
        "annotation_schema_version": "supervised_relational_annotation_v1",
        "annotator": {"agent_id": "ingrid_daubechies_v1", "capability_id": "instance_problem_solution_segmenter_v1"},
        "human_review": "approved",
    }


def approved_precision_package() -> dict:
    regions = [
        _region(
            "problem-region-1",
            "P001",
            "problem",
            [0.08, 0.15, 0.92, 0.58],
            labels=["A", "B", "C", "D", "E"],
            quality=_quality(alternatives="pass"),
        ),
        _region("number-region-1", "P001", "problem_number", [0.08, 0.15, 0.15, 0.20]),
        _region(
            "answer-region-1",
            "P001",
            "answer_block",
            [0.12, 0.42, 0.48, 0.56],
            labels=["A", "B", "C"],
            quality=_quality(alternatives="pass"),
        ),
        _region(
            "answer-region-2",
            "P001",
            "answer_block",
            [0.55, 0.42, 0.90, 0.56],
            labels=["D", "E"],
            quality=_quality(alternatives="pass"),
        ),
        _region("solution-region-1", "S001", "solution", [0.08, 0.62, 0.92, 0.90]),
    ]
    return {
        "schema_version": "supervised_relational_annotation_v1",
        "annotation_id": "annotation-1",
        "document": {
            "document_id": "doc-1",
            "source_digest": "sha256:" + "a" * 64,
            "page_count": 2,
        },
        "page": {"page_number": 1, "printed_page_number": "1"},
        "regions": regions,
        "units": [
            {
                "annotation_unit_id": "P001",
                "unit_kind": "problem",
                "document_id": "doc-1",
                "exercise_set_id": "set-1",
                "source_pages": [1],
                "visible_identifier_raw": "1.",
                "visible_identifier_normalized": "1",
                "visible_identifier_status": "captured",
                "region_ids": [row["region_id"] for row in regions if row["annotation_unit_id"] == "P001"],
                "relation_ids": ["rel-ab-1", "rel-ab-2", "rel-solves"],
                "reading_order": 1,
                "source_provisional_unit_ids": ["P001"],
                "answer_block_status": "complete",
                "expected_alternative_count": 5,
                "human_review": "approved",
            },
            {
                "annotation_unit_id": "S001",
                "unit_kind": "solution",
                "document_id": "doc-1",
                "exercise_set_id": "set-1",
                "source_pages": [1],
                "visible_identifier_raw": "Solucion 1",
                "visible_identifier_normalized": "1",
                "visible_identifier_status": "captured",
                "region_ids": ["solution-region-1"],
                "relation_ids": ["rel-solves"],
                "reading_order": 2,
                "source_provisional_unit_ids": ["S001"],
                "human_review": "approved",
            },
        ],
        "relations": [
            {
                "relation_id": "rel-ab-1",
                "relation_type": "has_answer_block",
                "source_ids": ["P001"],
                "target_ids": ["answer-region-1"],
                "document_id": "doc-1",
                "source_pages": [1],
                "target_pages": [1],
                "evidence": ["alternatives A-C belong to problem 1"],
                "confidence": 0.99,
                "contract_version": "precision-annotation-v1",
                "human_review": "approved",
            },
            {
                "relation_id": "rel-ab-2",
                "relation_type": "has_answer_block",
                "source_ids": ["P001"],
                "target_ids": ["answer-region-2"],
                "document_id": "doc-1",
                "source_pages": [1],
                "target_pages": [1],
                "evidence": ["alternatives D-E belong to problem 1"],
                "confidence": 0.99,
                "contract_version": "precision-annotation-v1",
                "human_review": "approved",
            },
            {
                "relation_id": "rel-solves",
                "relation_type": "solves",
                "source_ids": ["S001"],
                "target_ids": ["P001"],
                "document_id": "doc-1",
                "source_pages": [1],
                "target_pages": [1],
                "evidence": ["same visible identifier"],
                "confidence": 0.99,
                "contract_version": "precision-annotation-v1",
                "human_review": "approved",
            },
        ],
        "confidence": 0.98,
        "uncertainty_reasons": [],
        "contract_version": "precision-annotation-v1",
        "annotation_schema_version": "supervised_relational_annotation_v1",
        "annotator": {"agent_id": "ingrid_daubechies_v1", "capability_id": "instance_problem_solution_segmenter_v1"},
        "review": {"status": "approved", "reviewer": "human", "reviewed_at": "2026-07-17T10:00:00-05:00"},
    }


class PrecisionAnnotationContractTests(unittest.TestCase):
    def test_complete_discontinuous_answer_blocks_are_hps2_ready(self) -> None:
        payload = approved_precision_package()

        self.assertEqual(validate_annotation_package(payload), [])
        result = evaluate_precision_annotation(payload)

        self.assertTrue(result["valid"])
        self.assertTrue(result["h_ps2_ready"])
        self.assertEqual(result["summary"]["answer_block_count"], 2)
        self.assertEqual(result["summary"]["covered_alternative_count"], 5)

    def test_omitted_or_duplicated_alternative_blocks_hps2(self) -> None:
        payload = approved_precision_package()
        payload["regions"][3]["content_members"]["alternative_labels"] = ["D", "A"]

        result = evaluate_precision_annotation(payload)

        self.assertFalse(result["h_ps2_ready"])
        self.assertIn("unit:P001:alternative_coverage_missing:E", result["issues"])
        self.assertIn("unit:P001:alternative_coverage_duplicate:A", result["issues"])

    def test_failed_or_unresolved_quality_check_blocks_hps2(self) -> None:
        payload = approved_precision_package()
        checks = payload["regions"][-1]["geometry_quality"]["checks"]
        checks["foreign_content_excluded"] = "fail"
        checks["geometry_precise"] = "uncertain"

        result = evaluate_precision_annotation(payload)

        self.assertFalse(result["h_ps2_ready"])
        self.assertIn("region:solution-region-1:quality:foreign_content_excluded:fail", result["issues"])
        self.assertIn("region:solution-region-1:quality:geometry_precise:uncertain", result["issues"])

    def test_multipage_continuity_requires_reciprocal_positive_evidence(self) -> None:
        payload = approved_precision_package()
        solution = payload["units"][1]
        solution["source_pages"] = [1, 2]
        solution["region_ids"].append("solution-region-2")
        solution["relation_ids"].append("rel-continues-on")
        payload["regions"].append(
            _region(
                "solution-region-2",
                "S001",
                "solution",
                [0.08, 0.15, 0.92, 0.45],
                page_number=2,
                quality=_quality(continuation="pass"),
            )
        )
        payload["regions"][-2]["geometry_quality"] = _quality(continuation="pass")
        payload["relations"].append(
            {
                "relation_id": "rel-continues-on",
                "relation_type": "continues_on",
                "source_ids": ["solution-region-1"],
                "target_ids": ["solution-region-2"],
                "document_id": "doc-1",
                "source_pages": [1],
                "target_pages": [2],
                "evidence": ["unfinished expression at source", "same expression continues at target"],
                "confidence": 0.96,
                "contract_version": "precision-annotation-v1",
                "human_review": "approved",
            }
        )

        result = evaluate_precision_annotation(payload)

        self.assertFalse(result["h_ps2_ready"])
        self.assertIn("relation:rel-continues-on:missing_reciprocal_continues_from", result["issues"])

    def test_geometry_bands_generate_warnings_without_automatic_failure(self) -> None:
        payload = approved_precision_package()
        solution_region = payload["regions"][-1]
        solution_region["bbox_norm_xyxy"] = [0.01, 0.01, 0.99, 0.99]
        solution_region["bbox_xyxy"] = [10, 10, 990, 990]

        result = evaluate_precision_annotation(payload)

        self.assertTrue(result["h_ps2_ready"])
        self.assertIn("region:solution-region-1:warning:large_page_area", result["warnings"])
        self.assertIn("region:solution-region-1:warning:top_page_band", result["warnings"])
        self.assertIn("region:solution-region-1:warning:bottom_page_band", result["warnings"])

    def test_open_problem_uses_not_applicable_without_answer_block(self) -> None:
        payload = approved_precision_package()
        problem = payload["units"][0]
        problem["answer_block_status"] = "not_applicable"
        problem["expected_alternative_count"] = 0
        problem["region_ids"] = ["problem-region-1", "number-region-1"]
        problem["relation_ids"] = ["rel-solves"]
        payload["regions"] = [row for row in payload["regions"] if row["region_class"] != "answer_block"]
        payload["regions"][0]["content_members"]["alternative_labels"] = []
        payload["regions"][0]["geometry_quality"]["checks"]["alternatives_complete"] = "not_applicable"
        payload["relations"] = [row for row in payload["relations"] if row["relation_type"] != "has_answer_block"]

        result = evaluate_precision_annotation(payload)

        self.assertTrue(result["h_ps2_ready"])
        self.assertEqual(result["summary"]["answer_block_count"], 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
