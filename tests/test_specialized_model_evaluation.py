from __future__ import annotations

import copy
import unittest

from modulos.instance_factory.specialized_model_evaluation import (
    evaluate_specialized_model_candidate,
    next_rollout_state,
)


def passing_candidate() -> dict:
    return {
        "schema_version": "specialized_model_candidate_v1",
        "evaluation_id": "eval-1",
        "capability_id": "mathematical_region_linker_v1",
        "model_id": "candidate-model",
        "model_version": "v1",
        "dataset": {
            "dataset_id": "precision-pilot",
            "status": "frozen",
            "split_audit_status": "passed",
        },
        "test_document_ids": ["test-book-1"],
        "ood_document_ids": ["ood-book-1"],
        "metrics": {
            "structural_page_accuracy": 0.97,
            "problem_precision": 0.97,
            "problem_recall": 0.96,
            "alternative_coverage": 0.99,
            "solution_precision": 0.96,
            "continuity_accuracy": 0.96,
            "linking_accuracy": 0.96,
            "critical_foreign_content_rate": 0.005,
            "manual_intervention_rate": 0.10,
            "independence_index": 0.92,
        },
        "family_metrics": {
            "editorial-a": {"evaluated_units": 120, "systematic_critical_errors": 0},
            "two-column": {"evaluated_units": 80, "systematic_critical_errors": 0},
        },
        "critical_errors": [],
        "abstention": {"supported": True, "low_confidence_routed": True, "rate": 0.08},
        "human_decision": {"status": "approved", "reviewer": "human"},
        "rollback_target": {"model_id": "supervised-route", "model_version": "agents-v1"},
        "current_rollout_state": "model_candidate",
    }


class SpecializedModelEvaluationTests(unittest.TestCase):
    def test_passing_candidate_meets_pilot_gate_but_only_recommends_next_state(self) -> None:
        report = evaluate_specialized_model_candidate(passing_candidate(), operation_mode="pilot")

        self.assertTrue(report["gate_passed"])
        self.assertEqual(report["blockers"], [])
        self.assertEqual(report["recommended_next_state"], "offline_evaluated")
        self.assertTrue(report["human_execution_required"])
        self.assertEqual(next_rollout_state("model_candidate", report), "offline_evaluated")

    def test_metric_failure_blocks_even_when_other_averages_pass(self) -> None:
        candidate = passing_candidate()
        candidate["metrics"]["alternative_coverage"] = 0.97
        candidate["metrics"]["critical_foreign_content_rate"] = 0.02

        report = evaluate_specialized_model_candidate(candidate, operation_mode="pilot")

        self.assertFalse(report["gate_passed"])
        self.assertIn("metric:alternative_coverage:below_minimum", report["blockers"])
        self.assertIn("metric:critical_foreign_content_rate:above_maximum", report["blockers"])
        self.assertEqual(report["recommended_next_state"], "model_candidate")

    def test_systematic_family_error_cannot_be_hidden_by_global_metrics(self) -> None:
        candidate = passing_candidate()
        candidate["family_metrics"]["editorial-a"]["systematic_critical_errors"] = 1

        report = evaluate_specialized_model_candidate(candidate)

        self.assertFalse(report["gate_passed"])
        self.assertIn("family:editorial-a:systematic_critical_errors", report["blockers"])

    def test_abstention_human_approval_rollback_and_frozen_dataset_are_required(self) -> None:
        candidate = passing_candidate()
        candidate["dataset"]["status"] = "validated"
        candidate["dataset"]["split_audit_status"] = "failed"
        candidate["abstention"]["supported"] = False
        candidate["human_decision"]["status"] = "pending"
        candidate["rollback_target"] = {}

        report = evaluate_specialized_model_candidate(candidate)

        self.assertIn("dataset:not_frozen", report["blockers"])
        self.assertIn("dataset:split_audit_not_passed", report["blockers"])
        self.assertIn("abstention:not_supported", report["blockers"])
        self.assertIn("human_decision:not_approved", report["blockers"])
        self.assertIn("rollback_target:missing", report["blockers"])

    def test_regular_operation_uses_stricter_independence_and_manual_thresholds(self) -> None:
        candidate = passing_candidate()
        candidate["metrics"]["independence_index"] = 0.89
        candidate["metrics"]["manual_intervention_rate"] = 0.11
        candidate["current_rollout_state"] = "limited_operation"

        report = evaluate_specialized_model_candidate(candidate, operation_mode="regular")

        self.assertIn("metric:independence_index:below_minimum", report["blockers"])
        self.assertIn("metric:manual_intervention_rate:above_maximum", report["blockers"])
        self.assertEqual(next_rollout_state("limited_operation", report), "limited_operation")

    def test_unresolved_critical_error_blocks_candidate(self) -> None:
        candidate = passing_candidate()
        candidate["critical_errors"] = [
            {"error_type": "neighbor_alternative_in_problem", "status": "open", "document_id": "test-book-1"}
        ]

        report = evaluate_specialized_model_candidate(candidate)

        self.assertIn("critical_error:neighbor_alternative_in_problem", report["blockers"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
