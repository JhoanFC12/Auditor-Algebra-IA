from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from modulos.instance_factory.models import InstancePipelineContext, StagingProblemRecord
from modulos.instance_factory.server_jobs import ServerJobStatus, ServerJobStore, run_hf_ocr_job
from modulos.instance_factory.server_storage import ServerStorageResolver
from modulos.instance_factory.staging import InstanceStagingStore


class _FakeOcrEndpointManager:
    def __init__(self) -> None:
        self.active: set[str] = set()
        self.external_active = False
        self.calls: list[tuple[str, str]] = []

    def begin_job(self, *, kind: str, job_id: str, label: str = "") -> str:
        lease_id = f"{kind}:{job_id}:{len(self.active) + 1}"
        self.active.add(lease_id)
        self.calls.append(("begin_job", job_id))
        return lease_id

    def end_job(self, lease_id: str) -> None:
        self.active.discard(lease_id)
        self.calls.append(("end_job", lease_id))

    def ensure_ready(self) -> dict[str, str]:
        self.calls.append(("ensure_ready", "running"))
        return {"status": "running"}

    def scale_to_zero_if_idle(self) -> dict[str, str]:
        if self.active or self.external_active:
            self.calls.append(("scale_to_zero_if_idle", "skipped"))
            return {"status": "skipped", "reason": "active_ocr_jobs"}
        self.calls.append(("scale_to_zero_if_idle", "scaledToZero"))
        return {"status": "scaledToZero"}


class ServerFactoryJobStoreTests(unittest.TestCase):
    def test_job_persists_across_store_recreation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "jobs"
            first_store = ServerJobStore(root=root)
            job = first_store.create(kind="ocr", input={"record_ids": ["r1"]}, total=1, instance_key="book__inst")
            first_store.mark_running(job.job_id, "Running OCR")
            first_store.update_progress(job.job_id, current=1, ok=1)
            first_store.complete(job.job_id, output={"saved": 1})

            second_store = ServerJobStore(root=root)
            restored = second_store.get(job.job_id)

        self.assertIsNotNone(restored)
        self.assertEqual(restored.status, ServerJobStatus.DONE)
        self.assertEqual(restored.output["saved"], 1)
        self.assertEqual(restored.progress_label, "1/1")

    def test_job_errors_are_persisted_and_public_payload_hides_input(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = ServerJobStore(root=Path(raw) / "jobs")
            job = store.create(kind="segmentation", input={"internal_path": r"E:\private.pdf"}, total=2)
            store.mark_running(job.job_id)
            store.append_error(job.job_id, "model missing", code="missing_model", data={"stage": "pdf_detector"})
            failed = store.fail(job.job_id, "model missing", code="missing_model")
            public = failed.public_dict()

        self.assertEqual(failed.status, ServerJobStatus.ERROR)
        self.assertGreaterEqual(failed.failed, 1)
        self.assertEqual(failed.errors[-1]["code"], "missing_model")
        self.assertNotIn("input", public)

    def test_active_jobs_only_returns_queued_or_running(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = ServerJobStore(root=Path(raw) / "jobs")
            queued = store.create(kind="ocr")
            done = store.create(kind="ocr")
            store.complete(done.job_id)
            running = store.create(kind="segmentation")
            store.mark_running(running.job_id)
            active_ids = {job.job_id for job in store.active_jobs()}

        self.assertIn(queued.job_id, active_ids)
        self.assertIn(running.job_id, active_ids)
        self.assertNotIn(done.job_id, active_ids)

    def test_refresh_can_read_running_job_state_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "jobs"
            store = ServerJobStore(root=root)
            job = store.create(kind="problem_segmentation", total=10, instance_key="book__s01")
            store.mark_running(job.job_id, "Segmenting")
            store.update_progress(job.job_id, current=4, ok=4)

            after_refresh = ServerJobStore(root=root)
            restored = after_refresh.get(job.job_id)

        self.assertIsNotNone(restored)
        self.assertTrue(restored.running)
        self.assertEqual(restored.status, ServerJobStatus.RUNNING)
        self.assertEqual(restored.progress_label, "4/10")

    def test_hf_ocr_runner_persists_raw_ocr_to_server_staging(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            crop = root / "crop.png"
            crop.write_bytes(b"fake")
            storage = ServerStorageResolver(root=root / "server")
            context = InstancePipelineContext(book_code="Book", instance_type="S01", pdf_path=str(root / "book.pdf"))
            staging = InstanceStagingStore(context, root=root / "staging")
            staging.upsert_record(
                StagingProblemRecord(
                    record_id="crop_001",
                    crop_id="crop_001",
                    crop_path=str(crop),
                    source={"page_number": 1, "bbox_px": [1, 2, 3, 4]},
                )
            )
            store = ServerJobStore(root=root / "jobs", storage=storage)
            endpoint = _FakeOcrEndpointManager()
            job = store.create(kind="hf_ocr", total=1, instance_key="Book__S01")

            finished = run_hf_ocr_job(
                store=store,
                job_id=job.job_id,
                record_items=[{"record_id": "crop_001", "model": "hf/demo"}],
                ocr_client=lambda _item: {"raw_ocr": "<1.> texto"},
                storage=storage,
                book_code=context.book_code,
                instance_code=context.instance_type,
                staging=staging,
                endpoint_manager=endpoint,
                retry_sleep=lambda _seconds: None,
            )
            restored_record = staging.get_record("crop_001")
            server_index = staging.load_server_artifacts()
            manifest = staging.manifest_path.read_text(encoding="utf-8")
            artifact_path = storage.resolve_asset_key(server_index["raw_ocr"]["crop_001"]["artifact"]["asset_key"])
            artifact_text = artifact_path.read_text(encoding="utf-8")

        self.assertEqual(finished.status, ServerJobStatus.DONE)
        self.assertIsNotNone(restored_record)
        self.assertEqual(restored_record.raw_ocr, "<1.> texto")
        self.assertIn('"raw_ocr"', manifest)
        self.assertEqual(artifact_text, "<1.> texto")
        self.assertEqual(finished.output["endpoint_shutdown"]["status"], "scaledToZero")

    def test_hf_ocr_runner_skips_scale_down_when_other_jobs_are_active(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            storage = ServerStorageResolver(root=root / "server")
            store = ServerJobStore(root=root / "jobs", storage=storage)
            endpoint = _FakeOcrEndpointManager()
            endpoint.external_active = True
            first = store.create(kind="hf_ocr", total=1)

            first_finished = run_hf_ocr_job(
                store=store,
                job_id=first.job_id,
                record_items=[{"record_id": "crop_001"}],
                ocr_client=lambda _item: "texto 1",
                storage=storage,
                book_code="Book",
                instance_code="S01",
                endpoint_manager=endpoint,
                retry_sleep=lambda _seconds: None,
            )

            endpoint.external_active = False
            second = store.create(kind="hf_ocr", total=1)
            second_finished = run_hf_ocr_job(
                store=store,
                job_id=second.job_id,
                record_items=[{"record_id": "crop_002"}],
                ocr_client=lambda _item: "texto 2",
                storage=storage,
                book_code="Book",
                instance_code="S01",
                endpoint_manager=endpoint,
                retry_sleep=lambda _seconds: None,
            )

        self.assertEqual(first_finished.output["endpoint_shutdown"]["status"], "skipped")
        self.assertEqual(first_finished.output["endpoint_shutdown"]["reason"], "active_ocr_jobs")
        self.assertEqual(second_finished.output["endpoint_shutdown"]["status"], "scaledToZero")


if __name__ == "__main__":
    unittest.main()
