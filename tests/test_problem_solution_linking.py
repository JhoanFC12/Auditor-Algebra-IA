from __future__ import annotations

import copy
import unittest
import time

from modulos.instance_factory.problem_solution_linking import (
    PROMOTION_BUNDLE_SCHEMA_VERSION,
    build_problem_solution_bundle,
    bundle_fingerprint,
    candidate_evidence_fingerprint,
    canonical_payload_fingerprint,
    generate_candidate_links,
    group_solution_fragments,
    problem_source_fingerprint,
    project_problem_units,
    retarget_candidate_problem,
    review_candidate_link,
    validate_confirmed_bundle,
    validate_solution_unit,
    visual_solution_payloads,
)


class ProblemSolutionLinkingTests(unittest.TestCase):
    def test_linker_handles_five_hundred_numbered_pairs_under_one_second(self) -> None:
        total = 500
        problems = [
            {
                "unit_id": f"p{number}",
                "number": number,
                "book_code": "book",
                "instance_type": "practice",
                "exercise_set_id": "set",
                "reading_order": number,
            }
            for number in range(total)
        ]
        solutions = [
            {
                "unit_id": f"s{number}",
                "number": number,
                "book_code": "book",
                "instance_type": "practice",
                "exercise_set_id": "set",
                "reading_order": number,
            }
            for number in range(total)
        ]

        started = time.perf_counter()
        links = generate_candidate_links(
            problems,
            solutions,
            pattern="separate_sections",
            source_mapping_confirmed=True,
            structure={"section_pair_confirmed": True},
        )
        elapsed = time.perf_counter() - started

        self.assertEqual(len(links), total)
        self.assertLess(elapsed, 1.0)

    def test_separate_sections_exact_numbers_are_high_confidence(self) -> None:
        problems = [
            {
                "unit_id": f"p{number}",
                "number": number,
                "exercise_set_id": "set-1",
                "book_code": "book",
                "instance_type": "practice",
                "page_number": 1,
                "reading_order": number,
            }
            for number in (1, 2)
        ]
        solutions = [
            {
                "unit_id": f"s{number}",
                "number": number,
                "exercise_set_id": "set-1",
                "book_code": "book",
                "instance_type": "practice",
                "page_number": 10,
                "reading_order": number,
            }
            for number in (1, 2)
        ]

        links = generate_candidate_links(
            problems,
            solutions,
            pattern="separate_sections",
            source_mapping_confirmed=True,
            structure={"section_pair_confirmed": True},
        )

        self.assertEqual([row["status"] for row in links], ["high_confidence", "high_confidence"])
        self.assertEqual([row["problem_ref"]["unit_id"] for row in links], ["p1", "p2"])
        self.assertEqual([row["score"] for row in links], [100, 100])

    def test_interleaved_without_number_requires_review(self) -> None:
        problems = [
            {
                "unit_id": "p1",
                "book_code": "book",
                "instance_type": "practice",
                "exercise_set_id": "set-1",
                "page_number": 3,
                "reading_order": 1,
                "column": 1,
            }
        ]
        solutions = [
            {
                "unit_id": "s1",
                "book_code": "book",
                "instance_type": "practice",
                "exercise_set_id": "set-1",
                "page_number": 3,
                "reading_order": 2,
                "column": 1,
                "solution_heading": True,
            }
        ]

        link = generate_candidate_links(
            problems,
            solutions,
            pattern="interleaved",
            source_mapping_confirmed=True,
        )[0]

        self.assertEqual(link["status"], "review_required")
        self.assertEqual(link["score"], 65)
        self.assertEqual(link["problem_ref"]["unit_id"], "p1")

    def test_deterministic_states_cover_weak_orphan_and_conflict(self) -> None:
        weak = generate_candidate_links(
            [{"unit_id": "p", "book_code": "book", "instance_type": "practice", "exercise_set_id": "set", "reading_order": 1}],
            [{"unit_id": "s", "book_code": "book", "instance_type": "practice", "exercise_set_id": "set", "reading_order": 1}],
            pattern="separate_sections",
            source_mapping_confirmed=True,
            structure={"section_pair_confirmed": True},
        )[0]
        orphan = generate_candidate_links(
            [{"unit_id": "p", "book_code": "one", "instance_type": "practice", "exercise_set_id": "set"}],
            [{"unit_id": "s", "book_code": "two", "instance_type": "practice", "exercise_set_id": "set"}],
            pattern="separate_sections",
        )[0]
        conflict = generate_candidate_links(
            [
                {"unit_id": "p1", "number": 7, "book_code": "book", "instance_type": "practice", "exercise_set_id": "set", "reading_order": 1},
                {"unit_id": "p2", "number": 7, "book_code": "book", "instance_type": "practice", "exercise_set_id": "set", "reading_order": 2},
            ],
            [{"unit_id": "s", "number": 7, "book_code": "book", "instance_type": "practice", "exercise_set_id": "set", "reading_order": 1}],
            pattern="separate_sections",
            source_mapping_confirmed=True,
            structure={"section_pair_confirmed": True},
        )[0]

        self.assertEqual(weak["status"], "weak")
        self.assertEqual(orphan["status"], "orphan")
        self.assertEqual(conflict["status"], "conflict")
        self.assertIn("top_score_tie", conflict["ambiguity_reasons"])

    def test_source_mapping_gate_prevents_high_confidence(self) -> None:
        link = generate_candidate_links(
            [{"unit_id": "p1", "number": 1, "book_code": "book", "instance_type": "practice", "exercise_set_id": "set", "reading_order": 1}],
            [{"unit_id": "s1", "number": 1, "book_code": "book", "instance_type": "practice", "exercise_set_id": "set", "reading_order": 1}],
            pattern="separate_sections",
            source_mapping_confirmed=False,
            structure={"section_pair_confirmed": True},
        )[0]

        self.assertEqual(link["status"], "review_required")
        self.assertFalse(link["gates"]["source_mapping_confirmed"])
        self.assertIn("source_mapping_unconfirmed", link["ambiguity_reasons"])

    def test_fingerprints_are_canonical_and_ignore_volatile_fields(self) -> None:
        left = {"b": 2, "a": 1, "updated_at": "first"}
        right = {"updated_at": "second", "a": 1, "b": 2}

        self.assertEqual(canonical_payload_fingerprint(left), canonical_payload_fingerprint(right))
        bundle = {"bundle_id": "b", "revision": 1, "bundle_fingerprint": "old"}
        self.assertEqual(bundle_fingerprint(bundle), bundle_fingerprint({"revision": 1, "bundle_id": "b"}))

    def test_project_review_bundle_and_visual_payload_contract(self) -> None:
        record = {
            "record_id": "problem-7",
            "crop_id": "crop-7",
            "crop_path": "problem.png",
            "source": {
                "book_code": "book",
                "instance_type": "practice",
                "exercise_set_id": "set-1",
                "page_number": 2,
                "bbox_px": [10, 20, 300, 500],
                "source_order": 1,
            },
            "normalized": {"latex_rendered_item": r"\item[\textbf{7.}] Calcule x."},
        }
        problem = project_problem_units([record])[0]
        self.assertEqual(problem["number_normalized"], "7")
        self.assertEqual(problem["source_fingerprint"], problem_source_fingerprint(record))
        solution = {
            "unit_id": "solution-7",
            "number": 7,
            "book_code": "book",
            "instance_type": "practice",
            "exercise_set_id": "set-1",
            "page_number": 9,
            "reading_order": 1,
            "continuation_complete": True,
            "provenance": {
                "source_version": "solution_detector_v1",
                "review_version": "ingrid_review_v1",
            },
            "fragments": [
                {
                    "fragment_id": "fragment-7-a",
                    "order": 1,
                    "page_number": 9,
                    "bbox_px": [30, 40, 500, 700],
                    "crop_path": "solution-7.png",
                    "sha256": "abc123",
                }
            ],
        }
        candidate = generate_candidate_links(
            [problem],
            [solution],
            pattern="separate_sections",
            source_mapping_confirmed=True,
            structure={"section_pair_confirmed": True},
        )[0]
        reviewed = review_candidate_link(
            candidate,
            action="confirm",
            problem_unit_id="problem-7",
            reviewer="human",
            reviewed_at="2026-07-15T12:00:00-05:00",
        )
        bundle = build_problem_solution_bundle(
            problem_unit=problem,
            solution_units=[solution],
            reviewed_links=[reviewed],
            provenance={
                "structure_map_version": "problem_solution_structure_v1",
                "box_review_version": "ingrid_review_v1",
                "linker_version": "rules_v1",
            },
        )

        self.assertEqual(bundle["schema_version"], PROMOTION_BUNDLE_SCHEMA_VERSION)
        self.assertEqual(validate_confirmed_bundle(bundle), [])
        visual = visual_solution_payloads(bundle)
        self.assertEqual(len(visual), 1)
        self.assertEqual(visual[0]["images"], ["solution-7.png"])
        self.assertEqual(visual[0]["bundle_fingerprint"], bundle["bundle_fingerprint"])

    def test_validation_rejects_pending_or_incomplete_bundle(self) -> None:
        issues = validate_confirmed_bundle(
            {
                "schema_version": PROMOTION_BUNDLE_SCHEMA_VERSION,
                "bundle_id": "pending",
                "revision": 1,
                "status": "pending",
                "problem_ref": {"record_id": "p"},
                "solutions": [],
                "human_review": {"status": "pending"},
            }
        )

        self.assertIn("solution_bundle:not_human_confirmed", issues)
        self.assertIn("solution_bundle:missing_problem_source_fingerprint", issues)
        self.assertIn("solution_bundle:missing_solutions", issues)

    def test_fragment_grouping_marks_complete_multipage_unit(self) -> None:
        units = group_solution_fragments(
            [
                {
                    "fragment_id": "f2",
                    "continuation_group_id": "s1",
                    "fragment_role": "end",
                    "page_number": 11,
                },
                {
                    "fragment_id": "f1",
                    "continuation_group_id": "s1",
                    "fragment_role": "begin",
                    "page_number": 10,
                },
            ]
        )

        self.assertEqual(len(units), 1)
        self.assertTrue(units[0]["continuation_complete"])
        self.assertEqual([row["fragment_id"] for row in units[0]["fragments"]], ["f1", "f2"])

    def test_solution_unit_rejects_incomplete_or_malformed_continuation(self) -> None:
        unit = {
            "solution_unit_id": "s1",
            "book_code": "book",
            "instance_type": "practice",
            "exercise_set_id": "set",
            "continuation_complete": False,
            "provenance": {
                "source_version": "boxes-v1",
                "review_version": "review-v1",
            },
            "fragments": [
                {
                    "fragment_id": "f1",
                    "fragment_role": "middle",
                    "page_number": 10,
                    "bbox_px": [1, 2, 30, 40],
                    "crop_path": "f1.png",
                    "sha256": "abc",
                },
                {
                    "fragment_id": "f2",
                    "fragment_role": "end",
                    "page_number": 11,
                    "bbox_px": [1, 2, 30, 40],
                    "crop_path": "f2.png",
                    "sha256": "def",
                },
            ],
        }

        issues = validate_solution_unit(unit)

        self.assertIn("solution_unit:s1:incomplete_continuation", issues)
        self.assertIn("solution_unit:s1:invalid_continuation_start", issues)

        without_roles = copy.deepcopy(unit)
        without_roles["continuation_complete"] = True
        for fragment in without_roles["fragments"]:
            fragment.pop("fragment_role", None)
        self.assertIn(
            "solution_unit:s1:missing_continuation_roles",
            validate_solution_unit(without_roles),
        )

    def test_external_document_confirmation_requires_stable_reference(self) -> None:
        bundle = {
            "schema_version": PROMOTION_BUNDLE_SCHEMA_VERSION,
            "bundle_id": "external-1",
            "revision": 1,
            "status": "human_confirmed",
            "problem_ref": {"record_id": "p1", "source_fingerprint": "sha256:p"},
            "solutions": [
                {
                    "solution_id": "s1",
                    "solution_unit_id": "u1",
                    "continuation_complete": True,
                    "fragments": [
                        {
                            "fragment_id": "f1",
                            "page_number": 1,
                            "bbox_px": [0, 0, 10, 10],
                            "crop_path": "s.png",
                        }
                    ],
                }
            ],
            "human_review": {"status": "confirmed"},
            "document_relation": {"external": True, "status": "confirmed"},
        }

        self.assertIn(
            "solution_bundle:external_document_reference_missing",
            validate_confirmed_bundle(bundle),
        )
        bundle["document_relation"]["document_reference"] = "solucionario-book-1.pdf"
        self.assertNotIn(
            "solution_bundle:external_document_reference_missing",
            validate_confirmed_bundle(bundle),
        )

        disguised_internal = copy.deepcopy(bundle)
        disguised_internal["provenance"] = {"solution_status": "external_source"}
        disguised_internal["document_relation"] = {
            "external": False,
            "status": "same_document",
        }
        self.assertIn(
            "solution_bundle:external_document_required",
            validate_confirmed_bundle(disguised_internal),
        )

    def test_retarget_refreshes_problem_reference_before_human_review(self) -> None:
        problems = [
            {
                "unit_id": "p1",
                "record_id": "p1",
                "number": 1,
                "book_code": "book",
                "instance_type": "practice",
                "exercise_set_id": "set",
                "source_fingerprint": "sha256:problem-1",
            },
            {
                "unit_id": "p2",
                "record_id": "p2",
                "number": 2,
                "book_code": "book",
                "instance_type": "practice",
                "exercise_set_id": "set",
                "source_fingerprint": "sha256:problem-2",
            },
        ]
        solution = {
            "unit_id": "s1",
            "number": 1,
            "book_code": "book",
            "instance_type": "practice",
            "exercise_set_id": "set",
        }
        candidate = generate_candidate_links(
            problems[:1],
            [solution],
            pattern="separate_sections",
            source_mapping_confirmed=True,
        )[0]

        retargeted = retarget_candidate_problem(candidate, problems[1])
        reviewed = review_candidate_link(
            retargeted,
            action="change",
            problem_unit_id="p2",
            reviewer="human",
            reviewed_at="2026-07-15T12:00:00-05:00",
        )

        self.assertEqual(retargeted["problem_ref"]["unit_id"], "p2")
        self.assertEqual(retargeted["problem_ref"]["source_fingerprint"], "sha256:problem-2")
        self.assertEqual(retargeted["original_problem_ref"]["unit_id"], "p1")
        self.assertEqual(reviewed["selected_problem_unit_id"], "p2")
        self.assertEqual(
            reviewed["human_review"]["candidate_evidence_fingerprint"],
            candidate_evidence_fingerprint(retargeted),
        )

    def test_problem_source_fingerprint_changes_when_box_source_changes(self) -> None:
        record = {
            "record_id": "problem-7",
            "crop_id": "crop-7",
            "crop_path": "problem.png",
            "source": {"page_number": 2, "bbox_px": [10, 20, 300, 500]},
            "normalized": {"latex_rendered_item": r"\item[\textbf{7.}] Calcule."},
        }
        changed = {
            **record,
            "source": {"page_number": 2, "bbox_px": [11, 20, 300, 500]},
        }

        self.assertNotEqual(problem_source_fingerprint(record), problem_source_fingerprint(changed))

    def test_link_review_keeps_original_evidence_and_creates_distinct_events(self) -> None:
        candidate = generate_candidate_links(
            [{"unit_id": "p1", "number": 1, "book_code": "book", "instance_type": "practice", "exercise_set_id": "set"}],
            [{"unit_id": "s1", "number": 1, "book_code": "book", "instance_type": "practice", "exercise_set_id": "set"}],
            pattern="separate_sections",
            source_mapping_confirmed=True,
            structure={"section_pair_confirmed": True},
        )[0]
        confirmed = review_candidate_link(
            candidate,
            action="confirm",
            problem_unit_id="p1",
            reviewer="human",
            reviewed_at="2026-07-15T12:00:00Z",
        )
        rejected = review_candidate_link(
            candidate,
            action="reject",
            reviewer="human",
            reviewed_at="2026-07-15T12:01:00Z",
        )

        self.assertNotIn("human_review", candidate)
        self.assertEqual(confirmed["signals"], candidate["signals"])
        self.assertEqual(rejected["signals"], candidate["signals"])
        self.assertNotEqual(
            confirmed["human_review"]["review_event_id"],
            rejected["human_review"]["review_event_id"],
        )

    def test_empty_scope_never_behaves_as_automatic_wildcard(self) -> None:
        candidate = generate_candidate_links(
            [{"unit_id": "p1", "number": 1, "exercise_set_id": "set"}],
            [{"unit_id": "s1", "number": 1, "exercise_set_id": "set"}],
            pattern="separate_sections",
            source_mapping_confirmed=True,
            structure={"section_pair_confirmed": True},
        )[0]

        self.assertEqual(candidate["status"], "review_required")
        self.assertIsNone(candidate["problem_ref"])
        self.assertFalse(candidate["gates"]["scope_compatible"])
        self.assertIn("missing_solution_scope:book_code", candidate["ambiguity_reasons"])

    def test_candidate_evidence_fingerprint_ignores_review_but_not_scoring(self) -> None:
        candidate = {
            "candidate_link_id": "link-1",
            "score": 90,
            "signals": [{"name": "exact_number", "weight": 50}],
            "human_review": {"status": "confirmed"},
        }
        changed_review = {**candidate, "human_review": {"status": "rejected"}}
        changed_score = {**candidate, "score": 80}

        self.assertEqual(
            candidate_evidence_fingerprint(candidate),
            candidate_evidence_fingerprint(changed_review),
        )
        self.assertNotEqual(
            candidate_evidence_fingerprint(candidate),
            candidate_evidence_fingerprint(changed_score),
        )

    def test_projection_and_generation_respect_configured_page_roles(self) -> None:
        records = [
            {
                "record_id": f"p{page}",
                "source": {"page_number": page, "bbox_px": [0, 0, 10, 10]},
                "normalized": {"latex_rendered_item": rf"\item[\textbf{{{page}.}}] P."},
            }
            for page in (1, 2)
        ]
        context = {
            "book_code": "book",
            "instance_type": "practice",
            "page_selection_configured": True,
            "selected_pages": [1],
            "problem_solution_structure": {"exercise_set_id": "set"},
        }
        problems = project_problem_units(records, context)
        self.assertEqual([row["unit_id"] for row in problems], ["p1"])

        solutions = [
            {
                "unit_id": f"s{page}",
                "book_code": "book",
                "instance_type": "practice",
                "exercise_set_id": "set",
                "number": 1,
                "page_number": page,
            }
            for page in (10, 11)
        ]
        candidates = generate_candidate_links(
            problems,
            solutions,
            pattern="separate_sections",
            source_mapping_confirmed=True,
            structure={
                "section_pair_confirmed": True,
                "solution_page_selection_configured": True,
                "solution_selected_pages": [10],
            },
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["solution_ref"]["unit_id"], "s10")


if __name__ == "__main__":
    unittest.main()
