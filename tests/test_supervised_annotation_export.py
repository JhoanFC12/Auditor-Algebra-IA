from __future__ import annotations

import copy
import unittest

from modulos.instance_factory.supervised_annotations import (
    build_annotation_release,
    build_precision_pilot_manifest,
    transition_annotation_release,
    validate_annotation_release,
    validate_precision_pilot_manifest,
)
from tests.test_precision_annotation_contract import approved_precision_package


def _package(annotation_id: str, review_status: str) -> dict:
    payload = copy.deepcopy(approved_precision_package())
    payload["annotation_id"] = annotation_id
    payload["review"]["status"] = review_status
    return payload


class SupervisedAnnotationExportTests(unittest.TestCase):
    def test_only_human_approved_valid_annotations_are_training_eligible(self) -> None:
        approved = _package("approved-1", "approved")
        pending = _package("pending-1", "pending")
        rejected = _package("rejected-1", "rejected")

        release = build_annotation_release(
            [pending, approved, rejected],
            dataset_id="precision-pilot",
            dataset_version="2026.07.17-r1",
        )

        self.assertEqual(release["status"], "draft")
        self.assertEqual([row["annotation_id"] for row in release["annotations"]], ["approved-1"])
        self.assertEqual(release["quality_summary"]["training_eligible_count"], 1)
        self.assertEqual(release["quality_summary"]["excluded_count"], 2)
        self.assertEqual(
            {row["review_status"] for row in release["audit_exclusions"]},
            {"pending", "rejected"},
        )
        self.assertEqual(release["relation_counts"]["has_answer_block"], 2)
        self.assertEqual(release["class_counts"]["answer_block"], 2)
        self.assertEqual(validate_annotation_release(release), [])

    def test_invalid_approved_annotation_is_retained_for_audit_but_not_exported(self) -> None:
        invalid = _package("invalid-approved", "approved")
        invalid["regions"][0]["bbox_norm_xyxy"] = [0.8, 0.8, 0.2, 0.9]

        release = build_annotation_release(
            [invalid],
            dataset_id="precision-pilot",
            dataset_version="2026.07.17-r1",
        )

        self.assertEqual(release["annotations"], [])
        self.assertEqual(release["audit_exclusions"][0]["reason"], "contract_validation_failed")
        self.assertIn("region:problem-region-1:invalid_bbox_norm", release["audit_exclusions"][0]["issues"])
        self.assertIn("release:missing_training_annotations", validate_annotation_release(release))

    def test_release_fingerprint_is_deterministic_for_equivalent_input_order(self) -> None:
        first = _package("approved-a", "approved")
        second = _package("approved-b", "approved")
        second["document"]["document_id"] = "doc-2"
        second["document"]["source_digest"] = "sha256:" + "b" * 64
        for region in second["regions"]:
            region["document_id"] = "doc-2"
        for unit in second["units"]:
            unit["document_id"] = "doc-2"
        for relation in second["relations"]:
            relation["document_id"] = "doc-2"

        left = build_annotation_release(
            [first, second], dataset_id="dataset", dataset_version="v1"
        )
        right = build_annotation_release(
            [second, first], dataset_id="dataset", dataset_version="v1"
        )

        self.assertEqual(left["release_fingerprint"], right["release_fingerprint"])
        self.assertEqual(left["annotation_manifest"], right["annotation_manifest"])

    def test_release_lifecycle_requires_validation_and_human_approval_before_freeze(self) -> None:
        release = build_annotation_release(
            [_package("approved-1", "approved")],
            dataset_id="precision-pilot",
            dataset_version="2026.07.17-r1",
        )

        validated = transition_annotation_release(
            release,
            "validated",
            actor="validator",
            occurred_at="2026-07-17T12:00:00-05:00",
        )
        approved = transition_annotation_release(
            validated,
            "human_approved",
            actor="human",
            occurred_at="2026-07-17T12:05:00-05:00",
        )
        frozen = transition_annotation_release(
            approved,
            "frozen",
            actor="human",
            occurred_at="2026-07-17T12:06:00-05:00",
        )

        self.assertEqual(frozen["status"], "frozen")
        self.assertEqual(frozen["approved_by"], "human")
        self.assertTrue(frozen["frozen_fingerprint"].startswith("sha256:"))
        with self.assertRaisesRegex(ValueError, "Transicion"):
            transition_annotation_release(
                release,
                "frozen",
                actor="human",
                occurred_at="2026-07-17T12:06:00-05:00",
            )

    def test_synthetic_twenty_page_pilot_shape_is_safe_and_complete(self) -> None:
        required_tags = [
            "one_column",
            "two_columns",
            "single_answer_block",
            "multiple_answer_blocks",
            "graphical_alternatives",
            "open_question",
            "full_page_solution",
            "partial_page_solution",
            "true_multipage_continuation",
            "repeated_header_negative",
            "mixed_problem_solution",
        ]
        pages = [
            {
                "document_id": "synthetic-doc",
                "source_digest": "sha256:" + "f" * 64,
                "page_number": page_number,
                "case_tags": [required_tags[(page_number - 1) % len(required_tags)]],
                "authorized_roles": ["problem", "solution"],
            }
            for page_number in range(1, 21)
        ]

        manifest = build_precision_pilot_manifest(pages, pilot_id="precision-pilot-20")

        self.assertEqual(validate_precision_pilot_manifest(manifest), [])
        self.assertFalse(manifest["controls"]["agents_dispatched"])
        self.assertEqual(manifest["controls"]["canonical_writes"], "disabled")
        self.assertEqual(manifest["page_count"], 20)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
