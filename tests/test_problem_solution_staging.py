from __future__ import annotations

import hashlib
import tempfile
import threading
import time
import unittest
from pathlib import Path

from modulos.instance_factory.models import InstancePipelineContext, StageStatus, StagingProblemRecord
from modulos.instance_factory.problem_solution_linking import (
    PROMOTION_BUNDLE_SCHEMA_VERSION,
    problem_source_fingerprint,
    unit_source_fingerprint,
)
from modulos.instance_factory.staging import InstanceStagingStore


FINAL_ITEM = r"\item[\textbf{7.}] [[curso=Algebra]] Calcule $x$. A)1 B)2 C)3 D)4 E)5"


class ProblemSolutionStagingTests(unittest.TestCase):
    def _store_with_record(self, root: Path) -> tuple[InstanceStagingStore, StagingProblemRecord]:
        crop = root / "problem.png"
        crop.write_bytes(b"problem")
        context = InstancePipelineContext(
            book_code="book",
            instance_type="practice",
            pdf_path=str(root / "book.pdf"),
            workspace_dir=str(root),
            problem_solution_structure={
                "schema_version": "problem_solution_structure_v1",
                "structure_mode": "separate_sections",
                "solution_status": "identified",
                "exercise_set_id": "set-1",
            },
        )
        store = InstanceStagingStore(context, root=root / "staging")
        record = StagingProblemRecord(
            record_id="problem-7",
            crop_id="crop-7",
            crop_path=str(crop),
            status=StageStatus.READY,
            source={
                "book_code": "book",
                "instance_type": "practice",
                "pdf_path": str(root / "book.pdf"),
                "page_number": 2,
                "bbox_px": [10, 20, 300, 500],
                "exercise_set_id": "set-1",
            },
            models={"ocr": "test-model"},
            confidence={"pdf_box": 1.0},
            normalized={"latex_rendered_item": FINAL_ITEM},
        )
        store.upsert_record(record)
        saved = store.get_record(record.record_id)
        assert saved is not None
        return store, saved

    @staticmethod
    def _add_second_record(
        store: InstanceStagingStore,
        root: Path,
    ) -> StagingProblemRecord:
        crop = root / "problem-8.png"
        crop.write_bytes(b"problem-8")
        record = StagingProblemRecord(
            record_id="problem-8",
            crop_id="crop-8",
            crop_path=str(crop),
            status=StageStatus.READY,
            source={
                "book_code": "book",
                "instance_type": "practice",
                "pdf_path": str(root / "book.pdf"),
                "page_number": 3,
                "bbox_px": [20, 30, 310, 510],
                "exercise_set_id": "set-1",
            },
            models={"ocr": "test-model"},
            confidence={"pdf_box": 1.0},
            normalized={
                "latex_rendered_item": FINAL_ITEM.replace("7.", "8.", 1),
            },
        )
        store.upsert_record(record)
        saved = store.get_record(record.record_id)
        assert saved is not None
        return saved

    @staticmethod
    def _bundle(record: StagingProblemRecord, asset: Path, *, status: str = "human_confirmed") -> dict:
        asset_hash = hashlib.sha256(asset.read_bytes()).hexdigest()
        return {
            "schema_version": PROMOTION_BUNDLE_SCHEMA_VERSION,
            "bundle_id": "bundle-7",
            "revision": 1,
            "status": status,
            "scope": {
                "book_code": "book",
                "instance_type": "practice",
                "exercise_set_id": "set-1",
            },
            "problem_ref": {
                "record_id": record.record_id,
                "crop_id": record.crop_id,
                "book_code": "book",
                "instance_type": "practice",
                "exercise_set_id": "set-1",
                "number_normalized": "7",
                "source_fingerprint": problem_source_fingerprint(record),
            },
            "solutions": [
                {
                    "solution_id": "solution-7",
                    "solution_unit_id": "unit-7",
                    "relation_kind": "one_to_one",
                    "variant_index": 1,
                    "candidate_link_id": "link-7",
                    "human_review_event_id": "review-7",
                    "fragments": [
                        {
                            "fragment_id": "fragment-7",
                            "order": 1,
                            "page_number": 20,
                            "bbox_px": [20, 30, 600, 800],
                            "crop_path": str(asset),
                            "sha256": asset_hash,
                        }
                    ],
                }
            ],
            "document_relation": {"status": "confirmed", "external": False},
            "human_review": {
                "status": "confirmed" if status == "human_confirmed" else "pending",
                "reviewer": "human",
                "reviewed_at": "2026-07-15T12:00:00-05:00",
            },
            "provenance": {
                "structure_map_version": "problem_solution_structure_v1",
                "box_review_version": "ingrid_review_v1",
                "linker_version": "rules_v1",
            },
        }

    @staticmethod
    def _solution_unit(asset: Path) -> dict:
        return {
            "solution_unit_id": "unit-7",
            "book_code": "book",
            "instance_type": "practice",
            "exercise_set_id": "set-1",
            "number_normalized": "7",
            "provenance": {
                "source_version": "solution_detector_v1",
                "review_version": "ingrid_review_v1",
            },
            "fragments": [
                {
                    "fragment_id": "fragment-7",
                    "page_number": 20,
                    "bbox_px": [20, 30, 600, 800],
                    "crop_path": str(asset),
                    "sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
                }
            ],
        }

    @staticmethod
    def _reviewed_candidate(record: StagingProblemRecord, unit: dict) -> dict:
        return {
            "schema_version": "problem_solution_candidate_link_v1",
            "candidate_link_id": "link-7",
            "pattern": "separate_sections",
            "relation_kind": "one_to_one",
            "problem_ref": {
                "unit_id": record.record_id,
                "record_id": record.record_id,
                "source_fingerprint": problem_source_fingerprint(record),
            },
            "solution_ref": {
                "unit_id": "unit-7",
                "source_fingerprint": unit_source_fingerprint(unit),
            },
            "signals": [{"name": "exact_number", "weight": 50}],
            "score": 90,
            "runner_up_score": 0,
            "score_margin": 90,
            "status": "high_confidence",
            "gates": {"scope_compatible": True, "source_mapping_confirmed": True},
            "ambiguity_reasons": [],
            "human_review": {
                "schema_version": "problem_solution_review_event_v1",
                "review_version": "problem_solution_review_event_v1",
                "review_event_id": "review-7",
                "status": "confirmed",
                "problem_unit_id": record.record_id,
                "reviewer": "human",
                "reviewed_at": "2026-07-15T12:00:00-05:00",
            },
            "review_status": "confirmed",
            "selected_problem_unit_id": record.record_id,
            "provenance": {"linker_version": "rules_v1"},
        }

    def _prepare_review_state(
        self,
        store: InstanceStagingStore,
        record: StagingProblemRecord,
        asset: Path,
        *,
        expected_revision: int = 0,
    ) -> tuple[int, dict]:
        unit = self._solution_unit(asset)
        snapshot = store.upsert_solution_units([unit], expected_revision=expected_revision)
        stored_unit = snapshot["solution_units"][0]
        candidate = self._reviewed_candidate(record, stored_unit)
        snapshot = store.write_candidate_links([candidate], expected_revision=snapshot["revision"])
        event = {
            **dict(candidate["human_review"]),
            "target_type": "candidate_link",
            "target_id": "link-7",
            "candidate_link_id": "link-7",
        }
        snapshot = store.append_problem_solution_review(event, expected_revision=snapshot["revision"])
        return int(snapshot["revision"]), stored_unit

    def test_snapshot_state_operations_are_idempotent_and_revision_guarded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, _record = self._store_with_record(root)
            asset = root / "solution.png"
            asset.write_bytes(b"solution")
            self.assertEqual(store.problem_solution_snapshot()["revision"], 0)

            snapshot = store.upsert_solution_units(
                [self._solution_unit(asset)],
                expected_revision=0,
            )
            self.assertEqual(snapshot["revision"], 1)
            same = store.upsert_solution_units(
                [self._solution_unit(asset)],
                expected_revision=1,
            )
            self.assertEqual(same["revision"], 1)

            links = store.write_candidate_links(
                [{"candidate_link_id": "link-7", "status": "high_confidence"}],
                expected_revision=1,
            )
            self.assertEqual(links["revision"], 2)
            reviewed = store.append_problem_solution_review(
                {"review_event_id": "review-7", "status": "confirmed"},
                expected_revision=2,
            )
            self.assertEqual(reviewed["revision"], 3)
            duplicate = store.append_problem_solution_review(
                {"review_event_id": "review-7", "status": "confirmed"},
                expected_revision=3,
            )
            self.assertEqual(duplicate["revision"], 3)

            with self.assertRaisesRegex(RuntimeError, "problem_solution_revision_conflict"):
                store.write_candidate_links([], expected_revision=2)

    def test_record_solution_status_is_versioned_and_append_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, record = self._store_with_record(root)

            pending = store.set_problem_solution_record_status(
                record.record_id,
                "pending_review",
                "human",
                "Todavia no se ha revisado.",
                expected_revision=0,
            )
            self.assertEqual(pending["revision"], 1)
            self.assertEqual(
                pending["problem_statuses"][record.record_id]["status"],
                "pending_review",
            )
            self.assertEqual(len(pending["review_events"]), 1)

            same = store.set_problem_solution_record_status(
                record.record_id,
                "pending_review",
                "human",
                "Todavia no se ha revisado.",
                expected_revision=1,
            )
            self.assertEqual(same["revision"], 1)
            self.assertEqual(len(same["review_events"]), 1)

            absent = store.set_problem_solution_record_status(
                record.record_id,
                "solutions_absent_confirmed",
                "human",
                "El enunciado no tiene solucion en la fuente revisada.",
                expected_revision=1,
            )
            self.assertEqual(absent["revision"], 2)
            self.assertEqual(
                absent["problem_statuses"][record.record_id]["status"],
                "solutions_absent_confirmed",
            )
            self.assertEqual(len(absent["review_events"]), 2)
            self.assertEqual(
                [event["after"]["status"] for event in absent["review_events"]],
                ["pending_review", "solutions_absent_confirmed"],
            )

            with self.assertRaisesRegex(ValueError, "problem_solution_record_status invalido"):
                store.set_problem_solution_record_status(
                    record.record_id,
                    "ignored",
                    "human",
                    "",
                    expected_revision=2,
                )
            self.assertEqual(store.problem_solution_snapshot(), absent)

    def test_absent_status_cannot_skip_a_pending_candidate_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, record = self._store_with_record(root)
            pending_candidate = {
                "candidate_link_id": "pending-link-7",
                "problem_ref": {"record_id": record.record_id, "unit_id": record.record_id},
                "review_status": "generated",
            }
            snapshot = store.write_candidate_links([pending_candidate], expected_revision=0)

            with self.assertRaisesRegex(ValueError, "pending_candidate_review:pending-link-7"):
                store.set_problem_solution_record_status(
                    record.record_id,
                    "solutions_absent_confirmed",
                    "human",
                    "No hay solucion.",
                    expected_revision=snapshot["revision"],
                )
            unchanged = store.problem_solution_snapshot()
            self.assertEqual(unchanged["revision"], snapshot["revision"])
            self.assertEqual(unchanged["problem_statuses"], {})
            self.assertEqual(unchanged["review_events"], [])

            rejected = {
                **pending_candidate,
                "review_status": "rejected",
                "human_review": {"status": "rejected"},
            }
            snapshot = store.write_candidate_links([rejected], expected_revision=snapshot["revision"])
            reviewed = store.set_problem_solution_record_status(
                record.record_id,
                "solutions_absent_confirmed",
                "human",
                "La propuesta fue descartada.",
                expected_revision=snapshot["revision"],
            )
            self.assertEqual(
                reviewed["problem_statuses"][record.record_id]["status"],
                "solutions_absent_confirmed",
            )

    def test_bundle_sidecar_roundtrip_attaches_to_record_and_allows_promotion_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, record = self._store_with_record(root)
            asset = root / "solution.png"
            asset.write_bytes(b"solution")
            revision, _unit = self._prepare_review_state(store, record, asset)

            saved = store.write_problem_solution_bundle(
                self._bundle(record, asset),
                expected_revision=revision,
            )

            self.assertEqual(saved["revision"], 1)
            self.assertEqual(store.read_problem_solution_bundle("bundle-7"), saved)
            self.assertEqual(store.bundle_for_record(record.record_id), saved)
            self.assertEqual(len(store.problem_solution_snapshot()["bundles"]), 1)
            candidate = store.build_promotion_candidate(record.record_id)
            self.assertTrue(candidate["explicit_upload_enabled"])
            self.assertFalse(
                any(str(issue).startswith("solution_bundle:") for issue in candidate["blocking_issues"])
            )
            self.assertEqual(candidate["payload"]["problem_solution_bundle"]["solutions_total"], 1)

    def test_bundle_write_is_idempotent_and_uses_optimistic_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, record = self._store_with_record(root)
            asset = root / "solution.png"
            asset.write_bytes(b"solution")
            bundle = self._bundle(record, asset)
            revision, _unit = self._prepare_review_state(store, record, asset)
            first = store.write_problem_solution_bundle(bundle, expected_revision=revision)
            retry = store.write_problem_solution_bundle(first, expected_revision=revision + 1)

            self.assertEqual(retry, first)
            self.assertEqual(store.problem_solution_snapshot()["revision"], revision + 1)

            changed = dict(first)
            changed["human_review"] = {**dict(first["human_review"]), "comment": "changed"}
            with self.assertRaisesRegex(RuntimeError, "problem_solution_revision_conflict"):
                store.write_problem_solution_bundle(changed, expected_revision=0)
            second = store.write_problem_solution_bundle(changed, expected_revision=revision + 1)
            self.assertEqual(second["revision"], 2)
            self.assertEqual(store.problem_solution_snapshot()["revision"], revision + 2)

    def test_atomic_review_transaction_replaces_bundle_with_one_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, record = self._store_with_record(root)
            asset = root / "solution.png"
            asset.write_bytes(b"solution")
            revision, _unit = self._prepare_review_state(store, record, asset)
            store.write_problem_solution_bundle(
                self._bundle(record, asset),
                expected_revision=revision,
            )
            before = store.problem_solution_snapshot()
            replacement = self._bundle(record, asset)
            replacement["bundle_id"] = "bundle-8"
            event = {
                "review_event_id": "review-atomic-8",
                "target_type": "candidate_link",
                "target_id": "link-7",
                "candidate_link_id": "link-7",
                "status": "confirmed",
                "reviewer": "human",
            }

            after = store.apply_problem_solution_review(
                candidates=before["candidate_links"],
                review_event=event,
                bundle_removals=["bundle-7"],
                bundle_writes=[replacement],
                expected_revision=before["revision"],
            )

            self.assertEqual(after["revision"], before["revision"] + 1)
            self.assertIsNone(store.read_problem_solution_bundle("bundle-7"))
            self.assertIsNotNone(store.read_problem_solution_bundle("bundle-8"))
            attached = store.get_record(record.record_id)
            assert attached is not None
            self.assertEqual(
                attached.artifacts.get("problem_solution_bundle_id"),
                "bundle-8",
            )
            self.assertEqual(
                [row.get("review_event_id") for row in after["review_events"]],
                ["review-7", "review-atomic-8"],
            )

    def test_atomic_review_transaction_rolls_back_every_failure_phase(self) -> None:
        phases = (
            "journal_prepared",
            "bundle_removals_applied",
            "bundle_writes_applied",
            "record_attachments_applied",
            "state_persisted",
            "manifest_persisted",
        )
        for phase in phases:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                store, record = self._store_with_record(root)
                asset = root / "solution.png"
                asset.write_bytes(b"solution")
                revision, _unit = self._prepare_review_state(store, record, asset)
                store.write_problem_solution_bundle(
                    self._bundle(record, asset),
                    expected_revision=revision,
                )
                before_snapshot = store.problem_solution_snapshot()
                before_record = store.get_record(record.record_id)
                assert before_record is not None
                before_record_payload = before_record.to_dict()
                before_state_bytes = store.problem_solution_state_path.read_bytes()
                before_manifest_bytes = store.manifest_path.read_bytes()
                before_bundle_bytes = store._problem_solution_bundle_path("bundle-7").read_bytes()
                replacement = self._bundle(record, asset)
                replacement["bundle_id"] = "bundle-8"

                def fail_at(current_phase: str) -> None:
                    if current_phase == phase:
                        raise RuntimeError(f"injected:{phase}")

                with self.assertRaisesRegex(RuntimeError, f"injected:{phase}"):
                    store.apply_problem_solution_review(
                        candidates=before_snapshot["candidate_links"],
                        review_event={
                            "review_event_id": "review-atomic-8",
                            "status": "confirmed",
                            "reviewer": "human",
                        },
                        bundle_removals=["bundle-7"],
                        bundle_writes=[replacement],
                        expected_revision=before_snapshot["revision"],
                        failure_injector=fail_at,
                    )

                self.assertEqual(store.problem_solution_snapshot(), before_snapshot)
                restored_record = store.get_record(record.record_id)
                assert restored_record is not None
                self.assertEqual(restored_record.to_dict(), before_record_payload)
                self.assertEqual(store.problem_solution_state_path.read_bytes(), before_state_bytes)
                self.assertEqual(store.manifest_path.read_bytes(), before_manifest_bytes)
                self.assertEqual(
                    store._problem_solution_bundle_path("bundle-7").read_bytes(),
                    before_bundle_bytes,
                )
                self.assertFalse(store._problem_solution_bundle_path("bundle-8").exists())
                self.assertFalse(store.problem_solution_transaction_journal_path.exists())

    def test_next_locked_operation_recovers_an_unfinished_transaction_journal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, record = self._store_with_record(root)
            asset = root / "solution.png"
            asset.write_bytes(b"solution")
            revision, _unit = self._prepare_review_state(store, record, asset)
            before = store.problem_solution_snapshot()
            snapshots = store._problem_solution_transaction_snapshots(
                [store.problem_solution_state_path]
            )
            store._write_problem_solution_transaction_journal(snapshots, phase="prepared")
            corrupted = store._load_problem_solution_state()
            corrupted["candidate_links"] = []
            corrupted["revision"] = revision + 50
            store._write_problem_solution_state(corrupted)

            recovered = store.write_candidate_links(
                before["candidate_links"],
                expected_revision=before["revision"],
            )

            self.assertEqual(recovered, before)
            self.assertFalse(store.problem_solution_transaction_journal_path.exists())

    def test_changed_solution_asset_blocks_candidate_as_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, record = self._store_with_record(root)
            asset = root / "solution.png"
            asset.write_bytes(b"solution-before")
            revision, _unit = self._prepare_review_state(store, record, asset)
            store.write_problem_solution_bundle(self._bundle(record, asset), expected_revision=revision)

            asset.write_bytes(b"solution-after")
            candidate = store.build_promotion_candidate(record.record_id)

            self.assertFalse(candidate["explicit_upload_enabled"])
            self.assertIn(
                "solution_bundle:stale_asset:solution-7:fragment-7",
                candidate["blocking_issues"],
            )

    def test_changed_problem_box_invalidates_attached_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, record = self._store_with_record(root)
            asset = root / "solution.png"
            asset.write_bytes(b"solution")
            revision, _unit = self._prepare_review_state(store, record, asset)
            store.write_problem_solution_bundle(self._bundle(record, asset), expected_revision=revision)

            changed = store.get_record(record.record_id)
            assert changed is not None
            changed.source = {**dict(changed.source or {}), "bbox_px": [11, 20, 300, 500]}
            store.upsert_record(changed)
            candidate = store.build_promotion_candidate(record.record_id)

            self.assertFalse(candidate["explicit_upload_enabled"])
            self.assertIn("solution_bundle:stale_problem_source", candidate["blocking_issues"])

    def test_changed_solution_unit_invalidates_attached_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, record = self._store_with_record(root)
            asset = root / "solution.png"
            asset.write_bytes(b"solution")
            revision, solution_unit = self._prepare_review_state(store, record, asset)
            bundle = self._bundle(record, asset)
            bundle["solutions"][0]["source_fingerprint"] = unit_source_fingerprint(solution_unit)
            store.write_problem_solution_bundle(bundle, expected_revision=revision)

            changed_unit = {
                **solution_unit,
                "fragments": [
                    {
                        **solution_unit["fragments"][0],
                        "bbox_px": [25, 30, 600, 800],
                    }
                ],
            }
            store.upsert_solution_units([changed_unit], expected_revision=revision + 1)
            candidate = store.build_promotion_candidate(record.record_id)

            self.assertFalse(candidate["explicit_upload_enabled"])
            self.assertIn(
                "solution_bundle:stale_solution_unit:unit-7",
                candidate["blocking_issues"],
            )

    def test_pending_bundle_blocks_only_its_attached_problem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, record = self._store_with_record(root)
            clean_candidate = store.build_promotion_candidate(record.record_id)
            self.assertFalse(
                any(str(issue).startswith("solution_bundle:") for issue in clean_candidate["blocking_issues"])
            )
            asset = root / "solution.png"
            asset.write_bytes(b"solution")
            revision, _unit = self._prepare_review_state(store, record, asset)
            store.write_problem_solution_bundle(
                self._bundle(record, asset, status="pending"),
                expected_revision=revision,
            )

            blocked = store.build_promotion_candidate(record.record_id)
            self.assertIn("solution_bundle:not_human_confirmed", blocked["blocking_issues"])
            self.assertFalse(blocked["explicit_upload_enabled"])

    def test_opted_solution_flow_requires_bundle_or_per_record_absence_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, record = self._store_with_record(root)

            blocked = store.build_promotion_candidate(record.record_id)
            self.assertFalse(blocked["explicit_upload_enabled"])
            self.assertIn(
                "problem_solution:bundle_or_absence_review_required",
                blocked["blocking_issues"],
            )

            snapshot = store.set_problem_solution_record_status(
                record.record_id,
                "solutions_absent_confirmed",
                "human",
                "La fuente revisada no contiene solucion.",
                expected_revision=0,
            )
            allowed = store.build_promotion_candidate(record.record_id)
            self.assertTrue(allowed["explicit_upload_enabled"])
            self.assertEqual(
                allowed["payload"]["problem_solution_review"]["status"],
                "solutions_absent_confirmed",
            )
            self.assertEqual(snapshot["revision"], 1)

    def test_pending_candidate_blocks_only_the_problem_it_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, record = self._store_with_record(root)
            independent = self._add_second_record(store, root)
            snapshot = store.write_candidate_links(
                [
                    {
                        "candidate_link_id": "pending-link-7",
                        "problem_ref": {
                            "record_id": record.record_id,
                            "unit_id": record.record_id,
                        },
                        "review_status": "review_required",
                    }
                ],
                expected_revision=0,
            )
            store.set_problem_solution_record_status(
                independent.record_id,
                "solutions_absent_confirmed",
                "human",
                "El segundo problema no tiene solucion.",
                expected_revision=snapshot["revision"],
            )

            blocked = store.build_promotion_candidate(record.record_id)
            allowed = store.build_promotion_candidate(independent.record_id)

            self.assertIn(
                "problem_solution:pending_candidate_review:pending-link-7",
                blocked["blocking_issues"],
            )
            self.assertFalse(blocked["explicit_upload_enabled"])
            self.assertTrue(allowed["explicit_upload_enabled"])
            self.assertFalse(
                any("pending-link-7" in str(issue) for issue in allowed["blocking_issues"])
            )

    def test_global_confirmed_absent_and_legacy_context_preserve_problem_only_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, record = self._store_with_record(root)

            store.context.problem_solution_structure = {
                "schema_version": "problem_solution_structure_v1",
                "structure_mode": "no_solutions",
                "solution_status": "confirmed_absent",
                "exercise_set_id": "set-1",
            }
            globally_absent = store.build_promotion_candidate(record.record_id)
            self.assertTrue(globally_absent["explicit_upload_enabled"])
            self.assertNotIn(
                "problem_solution:bundle_or_absence_review_required",
                globally_absent["blocking_issues"],
            )

            store.context.problem_solution_structure = {}
            legacy = store.build_promotion_candidate(record.record_id)
            self.assertTrue(legacy["explicit_upload_enabled"])
            self.assertNotIn(
                "problem_solution:bundle_or_absence_review_required",
                legacy["blocking_issues"],
            )

    def test_invalid_unit_scope_or_versions_never_replaces_valid_unit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, _record = self._store_with_record(root)
            asset = root / "solution.png"
            asset.write_bytes(b"solution")
            valid = store.upsert_solution_units([self._solution_unit(asset)], expected_revision=0)
            original = valid["solution_units"][0]

            invalid = self._solution_unit(asset)
            invalid["book_code"] = "another-book"
            invalid.pop("provenance")
            with self.assertRaisesRegex(ValueError, "scope_mismatch:book_code"):
                store.upsert_solution_units([invalid], expected_revision=1)

            snapshot = store.problem_solution_snapshot()
            self.assertEqual(snapshot["revision"], 1)
            self.assertEqual(snapshot["solution_units"], [original])

    def test_changed_page_map_invalidates_reviewed_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, record = self._store_with_record(root)
            asset = root / "solution.png"
            asset.write_bytes(b"solution")
            revision, _unit = self._prepare_review_state(store, record, asset)
            store.write_problem_solution_bundle(self._bundle(record, asset), expected_revision=revision)

            store.context.solution_selected_pages = [20, 21]
            store.context.solution_selected_page_ranges = [{"start": 20, "end": 21}]
            store.context.solution_page_selection_configured = True
            candidate = store.build_promotion_candidate(record.record_id)

            self.assertFalse(candidate["explicit_upload_enabled"])
            self.assertIn("solution_bundle:stale_page_map", candidate["blocking_issues"])

    def test_changed_candidate_evidence_invalidates_reviewed_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, record = self._store_with_record(root)
            asset = root / "solution.png"
            asset.write_bytes(b"solution")
            revision, _unit = self._prepare_review_state(store, record, asset)
            store.write_problem_solution_bundle(self._bundle(record, asset), expected_revision=revision)

            snapshot = store.problem_solution_snapshot()
            changed = dict(snapshot["candidate_links"][0])
            changed["score"] = int(changed["score"]) - 10
            store.write_candidate_links([changed], expected_revision=snapshot["revision"])
            candidate = store.build_promotion_candidate(record.record_id)

            self.assertFalse(candidate["explicit_upload_enabled"])
            self.assertIn(
                "solution_bundle:stale_candidate_evidence:link-7",
                candidate["blocking_issues"],
            )

    def test_changed_candidate_review_invalidates_reviewed_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, record = self._store_with_record(root)
            asset = root / "solution.png"
            asset.write_bytes(b"solution")
            revision, _unit = self._prepare_review_state(store, record, asset)
            store.write_problem_solution_bundle(self._bundle(record, asset), expected_revision=revision)

            snapshot = store.problem_solution_snapshot()
            changed = dict(snapshot["candidate_links"][0])
            changed["human_review"] = {
                **dict(changed["human_review"]),
                "reviewer": "second-human",
            }
            store.write_candidate_links([changed], expected_revision=snapshot["revision"])
            candidate = store.build_promotion_candidate(record.record_id)

            self.assertFalse(candidate["explicit_upload_enabled"])
            self.assertIn(
                "solution_bundle:stale_candidate_review:link-7",
                candidate["blocking_issues"],
            )

    def test_sync_context_invalidates_candidates_once_and_preserves_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, record = self._store_with_record(root)
            asset = root / "solution.png"
            asset.write_bytes(b"solution")
            revision, _unit = self._prepare_review_state(store, record, asset)

            store.context.solution_page_selection_configured = True
            store.context.solution_selected_pages = [20]
            store.context.solution_selected_page_ranges = [{"start": 20, "end": 20}]
            synced = store.sync_problem_solution_context(expected_revision=revision)

            self.assertEqual(synced["revision"], revision + 1)
            self.assertEqual(synced["candidate_links"], [])
            self.assertEqual(len(synced["invalidated_candidate_links"]), 1)
            self.assertEqual(len(synced["review_events"]), 1)

            store.context.solution_page_selection = {
                "source": "another-ui",
                "updated_at": "2099-01-01T00:00:00Z",
            }
            no_op = store.sync_problem_solution_context(expected_revision=revision + 1)
            self.assertEqual(no_op["revision"], revision + 1)
            self.assertEqual(len(no_op["invalidated_candidate_links"]), 1)

    def test_context_change_invalidates_prior_per_problem_absence_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, record = self._store_with_record(root)
            reviewed = store.set_problem_solution_record_status(
                record.record_id,
                "solutions_absent_confirmed",
                "human",
                "No habia solucion en el rango revisado.",
                expected_revision=0,
            )

            store.context.solution_page_selection_configured = True
            store.context.solution_selected_pages = [20]
            store.context.solution_selected_page_ranges = [{"start": 20, "end": 20}]
            synced = store.sync_problem_solution_context(expected_revision=reviewed["revision"])

            self.assertEqual(
                synced["problem_statuses"][record.record_id]["status"],
                "pending_review",
            )
            self.assertEqual(
                synced["review_events"][-1]["action"],
                "invalidate_absence_context_changed",
            )
            candidate = store.build_promotion_candidate(record.record_id)
            self.assertFalse(candidate["explicit_upload_enabled"])
            self.assertIn(
                "problem_solution:bundle_or_absence_review_required",
                candidate["blocking_issues"],
            )

    def test_solution_unit_change_invalidates_prior_per_problem_absence_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, record = self._store_with_record(root)
            reviewed = store.set_problem_solution_record_status(
                record.record_id,
                "solutions_absent_confirmed",
                "human",
                "No habia solucion con los boxes disponibles.",
                expected_revision=0,
            )
            asset = root / "solution.png"
            asset.write_bytes(b"new-solution")

            changed = store.upsert_solution_units(
                [self._solution_unit(asset)],
                expected_revision=reviewed["revision"],
            )

            self.assertEqual(
                changed["problem_statuses"][record.record_id]["status"],
                "pending_review",
            )
            self.assertEqual(
                changed["review_events"][-1]["action"],
                "invalidate_absence_solution_units_changed",
            )
            candidate = store.build_promotion_candidate(record.record_id)
            self.assertFalse(candidate["explicit_upload_enabled"])
            self.assertIn(
                "problem_solution:bundle_or_absence_review_required",
                candidate["blocking_issues"],
            )

    def test_external_source_context_cannot_be_disguised_as_same_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, record = self._store_with_record(root)
            store.context.problem_solution_structure["solution_status"] = "external_source"
            asset = root / "solution.png"
            asset.write_bytes(b"solution")
            revision, _unit = self._prepare_review_state(store, record, asset)
            bundle = self._bundle(record, asset)
            bundle["document_relation"] = {"external": False, "status": "same_document"}

            with self.assertRaisesRegex(ValueError, "external_document_required"):
                store.write_problem_solution_bundle(
                    bundle,
                    expected_revision=revision,
                )

    def test_atomic_review_serializes_normal_record_writer_and_preserves_both_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, record = self._store_with_record(root)
            asset = root / "solution.png"
            asset.write_bytes(b"solution")
            revision, _unit = self._prepare_review_state(store, record, asset)
            snapshot = store.problem_solution_snapshot()
            stale_normal_edit = store.get_record(record.record_id)
            assert stale_normal_edit is not None
            stale_normal_edit.review = {
                **dict(stale_normal_edit.review or {}),
                "concurrent_note": "edicion humana concurrente",
            }

            transaction_paused = threading.Event()
            allow_transaction = threading.Event()
            writer_done = threading.Event()
            failures: list[BaseException] = []

            def checkpoint(phase: str) -> None:
                if phase == "record_attachments_applied":
                    transaction_paused.set()
                    if not allow_transaction.wait(timeout=3.0):
                        raise RuntimeError("test_transaction_release_timeout")

            def run_transaction() -> None:
                try:
                    store.apply_problem_solution_review(
                        candidates=snapshot["candidate_links"],
                        review_event={
                            "review_event_id": "review-concurrent",
                            "status": "confirmed",
                            "reviewer": "human",
                        },
                        bundle_writes=[self._bundle(record, asset)],
                        expected_revision=revision,
                        failure_injector=checkpoint,
                    )
                except BaseException as exc:  # pragma: no cover - asserted below
                    failures.append(exc)

            def run_writer() -> None:
                try:
                    store.upsert_record(stale_normal_edit)
                except BaseException as exc:  # pragma: no cover - asserted below
                    failures.append(exc)
                finally:
                    writer_done.set()

            transaction_thread = threading.Thread(target=run_transaction)
            transaction_thread.start()
            self.assertTrue(transaction_paused.wait(timeout=3.0))
            writer_thread = threading.Thread(target=run_writer)
            writer_thread.start()
            time.sleep(0.1)
            self.assertFalse(writer_done.is_set())
            allow_transaction.set()
            transaction_thread.join(timeout=5.0)
            writer_thread.join(timeout=5.0)

            self.assertFalse(transaction_thread.is_alive())
            self.assertFalse(writer_thread.is_alive())
            self.assertEqual(failures, [])
            saved = store.get_record(record.record_id)
            assert saved is not None
            self.assertEqual(
                saved.review.get("concurrent_note"),
                "edicion humana concurrente",
            )
            self.assertEqual(
                saved.artifacts.get("problem_solution_bundle_id"),
                "bundle-7",
            )

    def test_unit_outside_configured_solution_pages_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, _record = self._store_with_record(root)
            asset = root / "solution.png"
            asset.write_bytes(b"solution")
            store.context.solution_page_selection_configured = True
            store.context.solution_selected_pages = [21]

            with self.assertRaisesRegex(ValueError, "outside_solution_page_selection:20"):
                store.upsert_solution_units([self._solution_unit(asset)], expected_revision=0)

            self.assertEqual(store.problem_solution_snapshot()["revision"], 0)


if __name__ == "__main__":
    unittest.main()
