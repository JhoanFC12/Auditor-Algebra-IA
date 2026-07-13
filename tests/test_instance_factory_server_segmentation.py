from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from modulos.instance_factory.models import InstancePipelineContext
from modulos.instance_factory.server_jobs import ServerJobStatus, ServerJobStore, run_problem_segmentation_job
from modulos.instance_factory.server_storage import ServerStorageResolver
from modulos.instance_factory.staging import InstanceStagingStore


class ServerFactorySegmentationJobTests(unittest.TestCase):
    def test_segmentation_runner_persists_page_box_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            storage = ServerStorageResolver(root=root)
            store = ServerJobStore(root=root / "jobs", storage=storage)
            job = store.create(kind="problem_segmentation", total=2, instance_key="book__s01")

            def detector(page_item):
                return {
                    "boxes": [
                        {
                            "class": "problem",
                            "bbox": [10, 20, 30, 40],
                            "confidence": 0.91,
                            "page": page_item["page_number"],
                        }
                    ]
                }

            finished = run_problem_segmentation_job(
                store=store,
                job_id=job.job_id,
                page_items=[{"page_number": 3}, {"page_number": 4}],
                detector=detector,
                storage=storage,
                book_code="Libro Demo",
                instance_code="Semana 1",
            )
            restored = ServerJobStore(root=root / "jobs", storage=storage).get(job.job_id)

        self.assertEqual(finished.status, ServerJobStatus.DONE)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.output["ok"], 2)
        self.assertEqual(restored.output["failed"], 0)
        self.assertEqual(len(restored.output["artifacts"]), 2)
        for artifact in restored.output["artifacts"]:
            self.assertIn("asset_key", artifact)
            self.assertNotIn("server_path", artifact)

    def test_segmentation_runner_keeps_partial_errors_observable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            storage = ServerStorageResolver(root=root)
            store = ServerJobStore(root=root / "jobs", storage=storage)
            job = store.create(kind="problem_segmentation", total=2)

            def detector(page_item):
                if page_item["page_number"] == 2:
                    raise RuntimeError("model failed")
                return {"boxes": [{"class": "problem", "bbox": [1, 2, 3, 4]}]}

            finished = run_problem_segmentation_job(
                store=store,
                job_id=job.job_id,
                page_items=[{"page_number": 1}, {"page_number": 2}],
                detector=detector,
                storage=storage,
                book_code="Book",
                instance_code="Inst",
            )

        self.assertEqual(finished.status, ServerJobStatus.ERROR)
        self.assertEqual(finished.output["ok"], 1)
        self.assertEqual(finished.output["failed"], 1)
        self.assertEqual(finished.errors[-1]["code"], "problem_segmentation_error")

    def test_segmentation_runner_indexes_page_boxes_in_staging_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            storage = ServerStorageResolver(root=root / "server")
            context = InstancePipelineContext(book_code="Book", instance_type="S01", pdf_path=str(root / "book.pdf"))
            staging = InstanceStagingStore(context, root=root / "staging")
            store = ServerJobStore(root=root / "jobs", storage=storage)
            job = store.create(kind="problem_segmentation", total=1)

            finished = run_problem_segmentation_job(
                store=store,
                job_id=job.job_id,
                page_items=[{"page_number": 7, "image_path": r"C:\legacy\page_007.png"}],
                detector=lambda _page: {"boxes": [{"class": "problem", "bbox": [1, 2, 3, 4]}]},
                storage=storage,
                book_code=context.book_code,
                instance_code=context.instance_type,
                staging=staging,
            )
            index = staging.load_server_artifacts()
            manifest = json.loads(staging.manifest_path.read_text(encoding="utf-8"))
            artifact_path = storage.resolve_asset_key(index["page_boxes"]["7"]["artifact"]["asset_key"])
            artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))

        self.assertEqual(finished.status, ServerJobStatus.DONE)
        self.assertIn("7", index["page_boxes"])
        self.assertEqual(index["page_boxes"]["7"]["boxes_count"], 1)
        self.assertIn("asset_key", index["page_boxes"]["7"]["artifact"])
        self.assertEqual(manifest["server_storage"]["page_boxes"]["7"]["job_id"], job.job_id)
        self.assertEqual(artifact_payload["source"]["image_path"], "page_007.png")


if __name__ == "__main__":
    unittest.main()
