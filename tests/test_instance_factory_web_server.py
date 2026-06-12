from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from modulos.modulo13_laboratorio_pdf_segmentacion.controlador_laboratorio_pdf import ProblemPageRecord
from modulos.instance_factory.models import InstancePipelineContext, StageStatus, StagingProblemRecord
from modulos.instance_factory.staging import InstanceStagingStore
from modulos.instance_factory.web_server import FactoryWebRuntime


class _FakeModels:
    def to_dict(self):
        return {"schema_version": "fake_models_v1"}


class _FakeService:
    def __init__(self, context: InstancePipelineContext, store: InstanceStagingStore) -> None:
        self.context = context
        self.staging = store
        self.models = _FakeModels()
        self.calls = []
        self.phase_calls = []
        self.pages = []
        self.delay_s = 0.0

    def build_instance_summary(self):
        return self.staging.summarize_records()

    def build_stage_overview(self):
        return [{"stage": "Staging", "status": StageStatus.NEEDS_REVIEW, "detail": "1 registro"}]

    def load_pages(self):
        return list(self.pages)

    def resolve_page_selection(self, raw):
        self.calls.append(("resolve_page_selection", raw))
        return [int(part) for part in str(raw).split(",") if part.strip()]

    def detect_pdf_pages(self, pages, *, dpi=300, confidence=0.25, detector_model=""):
        self.calls.append(("detect_pdf_pages", list(pages), dpi, confidence, detector_model))
        return list(self.pages)

    def update_page_boxes(self, record_id, boxes, *, layout_mode="auto", reviewed=True, reorder=False):
        self.calls.append(("update_page_boxes", record_id, boxes, layout_mode, reviewed, reorder))
        for page in self.pages:
            if page.record_id == record_id:
                page.boxes = [tuple(box[:4]) for box in boxes]
                page.layout_mode = layout_mode
                page.reviewed = reviewed
                return page
        raise KeyError(record_id)

    def materialize_crops_to_staging(self):
        self.calls.append(("materialize_crops_to_staging",))
        return self.staging.load_records()

    def run_ocr_and_segmentation(
        self,
        *,
        provider="hf",
        curso="SIN_CURSO",
        tema="SIN_TEMA",
        start_n=1,
        limit=None,
        ocr_model="",
        figure_model="",
        force_figure_model=True,
        record_id="",
        record_ids=None,
        progress_callback=None,
        run_segmentation=True,
        run_ocr=True,
    ):
        if self.delay_s:
            time.sleep(float(self.delay_s))
        self.phase_calls.append((record_id, list(record_ids or []), bool(run_segmentation), bool(run_ocr)))
        self.calls.append(("run_ocr_and_segmentation", provider, curso, tema, start_n, limit, ocr_model, figure_model, force_figure_model, record_id, list(record_ids or [])))
        return self.staging.load_records()

    def normalize_existing_ocr(self, *, record_id="", record_ids=None):
        self.calls.append(("normalize_existing_ocr", record_id, list(record_ids or [])))
        return self.staging.load_records()

    def update_figure_segments(self, record_id, boxes):
        self.calls.append(("update_figure_segments", record_id, boxes))
        record = self.staging.get_record(record_id)
        if record is None:
            raise KeyError(record_id)
        record.figure_segmentation = {
            "segments_total": len(boxes),
            "segments": [{"idx": index + 1, "bbox_px": list(box[:4]), "image_path": ""} for index, box in enumerate(boxes)],
        }
        self.staging.upsert_record(record)
        return record

    def update_raw_ocr(self, record_id, raw_ocr):
        self.calls.append(("update_raw_ocr", record_id, raw_ocr))
        record = self.staging.get_record(record_id)
        if record is None:
            raise KeyError(record_id)
        record.raw_ocr = str(raw_ocr or "")
        record.structured_ocr = {}
        record.normalized = {}
        self.staging.upsert_record(record)
        return record


class _FakeEndpointManager:
    def __init__(self) -> None:
        self.calls = []

    def status(self):
        self.calls.append(("status",))
        return {"schema_version": "hf_ocr_endpoint_status_v1", "status": "scaledToZero", "configured": True}

    def resume(self, *, wait=True, timeout_s=420, poll_s=8):
        self.calls.append(("resume", wait, timeout_s, poll_s))
        return {"schema_version": "hf_ocr_endpoint_status_v1", "status": "running", "configured": True}

    def scale_to_zero(self):
        self.calls.append(("scale_to_zero",))
        return {"schema_version": "hf_ocr_endpoint_status_v1", "status": "scaledToZero", "configured": True}


def _post_json(base: str, path: str, body: dict) -> dict:
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _read_http_error(exc: urllib.error.HTTPError) -> dict:
    return json.loads(exc.read().decode("utf-8"))


class InstanceFactoryWebServerTests(unittest.TestCase):
    def test_web_runtime_hides_internal_tracebacks_from_client(self) -> None:
        class _BrokenService(_FakeService):
            def build_instance_summary(self):
                raise RuntimeError("secret internal path E:/private/token")

        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(Path(tmp) / "book.pdf"))
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            runtime = FactoryWebRuntime(context, service=_BrokenService(context, store))
            try:
                base = runtime.start()
                with self.assertRaises(urllib.error.HTTPError) as failure:
                    urllib.request.urlopen(base + "api/bootstrap", timeout=5)
                payload = _read_http_error(failure.exception)
                self.assertEqual(failure.exception.code, 500)
                self.assertEqual(payload["schema_version"], "pdf_factory_web_error_v1")
                self.assertEqual(payload["code"], "internal_error")
                self.assertNotIn("traceback", payload)
                self.assertNotIn("secret internal path", payload["error"])
            finally:
                runtime.stop()

    def test_web_runtime_exposes_ocr_endpoint_lifecycle_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(Path(tmp) / "book.pdf"))
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            endpoint = _FakeEndpointManager()
            runtime = FactoryWebRuntime(context, service=_FakeService(context, store), endpoint_manager=endpoint)
            try:
                base = runtime.start()
                with urllib.request.urlopen(base + "api/endpoint/ocr/status", timeout=5) as response:
                    status = json.loads(response.read().decode("utf-8"))
                self.assertEqual(status["status"], "scaledToZero")

                resumed = _post_json(base, "api/endpoint/ocr/resume", {"wait": False, "timeout_s": 12, "poll_s": 2})
                self.assertEqual(resumed["status"], "running")
                scaled = _post_json(base, "api/endpoint/ocr/scale-to-zero", {})
                self.assertEqual(scaled["status"], "scaledToZero")
                self.assertEqual(endpoint.calls, [
                    ("status",),
                    ("resume", False, 12, 2),
                    ("scale_to_zero",),
                ])
            finally:
                runtime.stop()

    def test_web_runtime_exposes_shared_app_reload_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(Path(tmp) / "book.pdf"))
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            runtime = FactoryWebRuntime(context, service=_FakeService(context, store))
            try:
                base = runtime.start()
                before = _get_json(base, "api/app/version")
                after = _post_json(base, "api/app/reload-signal", {})
                self.assertEqual(before["schema_version"], "pdf_factory_web_app_version_v1")
                self.assertEqual(after["schema_version"], "pdf_factory_web_app_version_v1")
                self.assertTrue(after["reload_requested"])
                self.assertEqual(before["asset_version"], after["asset_version"])
                self.assertNotEqual(before.get("reload_token"), after.get("reload_token"))
            finally:
                runtime.stop()

    def test_web_runtime_runs_ocr_queue_as_reconnectable_background_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = root / "crop.png"
            crop.write_bytes(b"png")
            context = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_record(
                StagingProblemRecord(
                    record_id="crop_001",
                    crop_id="crop_001",
                    crop_path=str(crop),
                    status=StageStatus.NEEDS_REVIEW,
                )
            )
            service = _FakeService(context, store)
            endpoint = _FakeEndpointManager()
            runtime = FactoryWebRuntime(context, service=service, endpoint_manager=endpoint)
            try:
                base = runtime.start()
                started = _post_json(
                    base,
                    "api/ocr/jobs/start",
                    {
                        "provider": "local",
                        "record_ids": ["crop_001"],
                        "ocr_model": "ocr-test",
                        "figure_model": "fig-test",
                    },
                )
                self.assertEqual(started["schema_version"], "pdf_factory_ocr_job_v1")
                self.assertTrue(started["running"])
                job_id = started["job_id"]

                status = {}
                for _ in range(40):
                    status = _get_json(base, f"api/ocr/jobs/status?job_id={job_id}")
                    if not status["running"]:
                        break
                    time.sleep(0.05)

                self.assertEqual(status["status"], "done")
                self.assertEqual(status["ok"], 1)
                self.assertEqual(status["failed"], 0)
                self.assertEqual(status["current"], 1)
                self.assertEqual(status["endpoint_shutdown"]["status"], "scaledToZero")
                self.assertIn(
                    ("run_ocr_and_segmentation", "local", "SIN_CURSO", "SIN_TEMA", 1, 1, "ocr-test", "fig-test", True, "crop_001", []),
                    service.calls,
                )
                self.assertEqual(
                    service.phase_calls,
                    [
                        ("crop_001", [], True, False),
                        ("crop_001", [], False, True),
                    ],
                )
                self.assertIn(("scale_to_zero",), endpoint.calls)
            finally:
                runtime.stop()

    def test_web_runtime_keeps_ocr_endpoint_on_until_parallel_jobs_finish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop_1 = root / "crop_1.png"
            crop_2 = root / "crop_2.png"
            crop_1.write_bytes(b"png")
            crop_2.write_bytes(b"png")
            context = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            for record_id, crop in (("crop_001", crop_1), ("crop_002", crop_2)):
                store.upsert_record(
                    StagingProblemRecord(
                        record_id=record_id,
                        crop_id=record_id,
                        crop_path=str(crop),
                        status=StageStatus.NEEDS_REVIEW,
                    )
                )
            service = _FakeService(context, store)
            service.delay_s = 0.25
            endpoint = _FakeEndpointManager()
            runtime = FactoryWebRuntime(context, service=service, endpoint_manager=endpoint)
            try:
                base = runtime.start()
                first = _post_json(base, "api/ocr/jobs/start", {"provider": "local", "record_ids": ["crop_001"]})
                second = _post_json(base, "api/ocr/jobs/start", {"provider": "local", "record_ids": ["crop_002"]})
                self.assertNotEqual(first["job_id"], second["job_id"])

                statuses = {}
                for job_id in (first["job_id"], second["job_id"]):
                    status = {}
                    for _ in range(60):
                        status = _get_json(base, f"api/ocr/jobs/status?job_id={job_id}")
                        if not status["running"]:
                            break
                        time.sleep(0.05)
                    statuses[job_id] = status

                self.assertEqual(statuses[first["job_id"]]["status"], "done")
                self.assertEqual(statuses[second["job_id"]]["status"], "done")
                self.assertEqual(endpoint.calls.count(("scale_to_zero",)), 1)
                shutdown_payloads = [status.get("endpoint_shutdown") or {} for status in statuses.values()]
                self.assertTrue(any(payload.get("status") == "scaledToZero" for payload in shutdown_payloads))
                self.assertTrue(any(payload.get("reason") == "other_ocr_jobs_running" for payload in shutdown_payloads))
            finally:
                runtime.stop()

    def test_web_runtime_exposes_normalizer_training_bank_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bank = root / "normalizer_bank"
            crop = root / "crop.png"
            crop.write_bytes(b"png")
            context = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_record(
                StagingProblemRecord(
                    record_id="crop_001",
                    crop_id="crop_001",
                    crop_path=str(crop),
                    raw_ocr="Halle x. A) 1 B) 2 C) 3 D) 4 E) 5",
                )
            )
            with patch.dict(os.environ, {"NORMALIZER_TRAINING_BANK_ROOT": str(bank)}):
                store.update_review(
                    "crop_001",
                    {"latex_rendered_item": r"\item[\textbf{1.}] Halle $x$. £A)$1$æB)$2$æC)$3$£D)$4$ææE)$5$£"},
                    mark_ready=True,
                )
                runtime = FactoryWebRuntime(context, service=_FakeService(context, store))
                try:
                    base = runtime.start()
                    with urllib.request.urlopen(base + "api/training/normalizer/status", timeout=5) as response:
                        status = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(status["schema_version"], "normalizer_training_bank_status_v1")
                    self.assertEqual(status["samples_total"], 1)
                    self.assertFalse(status["ready_to_train"])
                finally:
                    runtime.stop()

    def test_web_runtime_exposes_snapshot_review_and_disabled_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(Path(tmp) / "book.pdf"))
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            store.upsert_record(
                StagingProblemRecord(
                    record_id="crop_001",
                    crop_id="crop_001",
                    crop_path=str(Path(tmp) / "crop.png"),
                    status=StageStatus.NEEDS_REVIEW,
                    raw_ocr="Halle x. A) 1 B) 2",
                    normalized={"numero": "1"},
                )
            )
            runtime = FactoryWebRuntime(context, service=_FakeService(context, store))
            try:
                base = runtime.start()
                with urllib.request.urlopen(base + "api/bootstrap", timeout=5) as response:
                    snapshot = json.loads(response.read().decode("utf-8"))
                self.assertEqual(snapshot["schema_version"], "pdf_factory_web_snapshot_v1")
                self.assertEqual(snapshot["context"]["book_code"], "ALG01")
                self.assertTrue(snapshot["policy"]["never_insert_directly_into_problemas"])
                self.assertFalse(snapshot["policy"]["promotion_enabled"])

                body = json.dumps(
                    {
                        "record_id": "crop_001",
                        "normalized": {"numero": "1", "enunciado_latex": "Halle x."},
                        "notes": "ok",
                        "mark_ready": True,
                    }
                ).encode("utf-8")
                request = urllib.request.Request(
                    base + "api/review/save",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    saved = json.loads(response.read().decode("utf-8"))
                self.assertEqual(saved["schema_version"], "pdf_factory_web_review_saved_v1")
                self.assertEqual(saved["record"]["status"], StageStatus.READY)

                with urllib.request.urlopen(base + "api/promotion?record_id=crop_001", timeout=5) as response:
                    candidate = json.loads(response.read().decode("utf-8"))
                self.assertFalse(candidate["promotion_enabled"])
                self.assertIsNone(candidate["sql"])
                self.assertEqual(candidate["write_operations"], [])
            finally:
                runtime.stop()

    def test_web_runtime_routes_ui_intents_to_service_methods(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "book.pdf"
            pdf.write_bytes(b"%PDF-1.4 placeholder")
            page_image = root / "page.png"
            page_image.write_bytes(b"png")
            crop = root / "crop.png"
            crop.write_bytes(b"png")
            context = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(pdf))
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_record(
                StagingProblemRecord(
                    record_id="crop_001",
                    crop_id="crop_001",
                    crop_path=str(crop),
                    status=StageStatus.NEEDS_REVIEW,
                    normalized={"numero": "1"},
                )
            )
            service = _FakeService(context, store)
            service.pages = [
                ProblemPageRecord(
                    record_id="page_001",
                    pdf_path=str(pdf),
                    page_number=1,
                    image_path=page_image,
                    boxes=[(1, 2, 30, 40)],
                    reviewed=False,
                )
            ]
            runtime = FactoryWebRuntime(context, service=service)
            try:
                base = runtime.start()
                snapshot = _post_json(base, "api/pages/detect", {"pages": "1", "dpi": 150, "confidence": 0.4})
                self.assertEqual(snapshot["pages"][0]["record_id"], "page_001")
                self.assertEqual(service.calls[-2:], [("resolve_page_selection", "1"), ("detect_pdf_pages", [1], 150, 0.4, "")])

                snapshot = _post_json(
                    base,
                    "api/pages/boxes",
                    {"record_id": "page_001", "boxes": [[10, 20, 60, 80]], "layout_mode": "una_columna", "reviewed": True},
                )
                self.assertEqual(snapshot["pages"][0]["boxes"], [[10, 20, 60, 80]])
                self.assertEqual(service.calls[-1][0], "update_page_boxes")

                _post_json(base, "api/staging/materialize", {})
                _post_json(base, "api/ocr/run", {"provider": "local", "curso": "ALG", "tema": "EC", "start_n": 3, "limit": 1, "record_id": "crop_001"})
                segment_snapshot = _post_json(base, "api/ocr/segments/boxes", {"record_id": "crop_001", "boxes": [[2, 3, 44, 55]]})
                _post_json(base, "api/normalize", {})
                self.assertIn(("materialize_crops_to_staging",), service.calls)
                self.assertIn(("run_ocr_and_segmentation", "local", "ALG", "EC", 3, 1, "", "", True, "crop_001", []), service.calls)
                _post_json(base, "api/ocr/run", {"provider": "local", "record_ids": ["crop_001", "crop_001", ""]})
                self.assertIn(("run_ocr_and_segmentation", "local", "SIN_CURSO", "SIN_TEMA", 1, None, "", "", True, "", ["crop_001"]), service.calls)
                raw_snapshot = _post_json(base, "api/ocr/raw", {"record_id": "crop_001", "raw_ocr": "texto corregido"})
                self.assertIn(("update_raw_ocr", "crop_001", "texto corregido"), service.calls)
                self.assertEqual(raw_snapshot["records"][0]["raw_ocr"], "texto corregido")
                raw_compact = _post_json(base, "api/ocr/raw", {"record_id": "crop_001", "raw_ocr": "texto lote", "compact": True})
                self.assertEqual(raw_compact["schema_version"], "pdf_factory_web_record_saved_v1")
                self.assertEqual(raw_compact["record"]["record_id"], "crop_001")
                self.assertEqual(raw_compact["record"]["raw_ocr"], "texto lote")
                self.assertIn(("update_figure_segments", "crop_001", [[2, 3, 44, 55]]), service.calls)
                self.assertEqual(segment_snapshot["records"][0]["figure_segmentation"]["segments_total"], 1)
                self.assertIn(("normalize_existing_ocr", "", []), service.calls)
                _post_json(base, "api/normalize", {"record_id": "crop_001"})
                self.assertIn(("normalize_existing_ocr", "crop_001", []), service.calls)
                _post_json(base, "api/normalize", {"record_ids": ["crop_001", "crop_001", ""]})
                self.assertIn(("normalize_existing_ocr", "", ["crop_001"]), service.calls)
                review_compact = _post_json(
                    base,
                    "api/review/save",
                    {
                        "record_id": "crop_001",
                        "normalized": {"numero": "8", "enunciado_latex": "Lote"},
                        "notes": "batch",
                        "mark_ready": True,
                        "compact": True,
                        "defer_golden_sync": True,
                    },
                )
                self.assertEqual(review_compact["schema_version"], "pdf_factory_web_record_saved_v1")
                self.assertEqual(review_compact["record"]["record_id"], "crop_001")
                self.assertEqual(review_compact["record"]["normalized"]["numero"], "8")
                self.assertEqual(review_compact["record"]["golden_sync"]["status"], "deferred")
                self.assertEqual(review_compact["record"]["status"], StageStatus.READY)

                with urllib.request.urlopen(base + "api/record?record_id=crop_001", timeout=5) as response:
                    detail = json.loads(response.read().decode("utf-8"))
                self.assertEqual(detail["schema_version"], "pdf_factory_web_record_detail_v1")
                self.assertFalse(detail["promotion_candidate"]["promotion_enabled"])
                self.assertEqual(detail["promotion_candidate"]["write_operations"], [])
            finally:
                runtime.stop()

    def test_web_runtime_returns_client_errors_without_tracebacks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(Path(tmp) / "book.pdf"))
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            runtime = FactoryWebRuntime(context, service=_FakeService(context, store))
            try:
                base = runtime.start()
                with self.assertRaises(urllib.error.HTTPError) as missing:
                    urllib.request.urlopen(base + "api/record?record_id=missing", timeout=5)
                payload = _read_http_error(missing.exception)
                self.assertEqual(missing.exception.code, 404)
                self.assertEqual(payload["schema_version"], "pdf_factory_web_error_v1")
                self.assertNotIn("traceback", payload)

                request = urllib.request.Request(base + "api/bootstrap", data=b"{}", method="POST")
                with self.assertRaises(urllib.error.HTTPError) as method_error:
                    urllib.request.urlopen(request, timeout=5)
                payload = _read_http_error(method_error.exception)
                self.assertEqual(method_error.exception.code, 405)
                self.assertEqual(payload["code"], "method_not_allowed")

                request = urllib.request.Request(base + "api/review/save", data=b"[1, 2]", method="POST")
                with self.assertRaises(urllib.error.HTTPError) as json_error:
                    urllib.request.urlopen(request, timeout=5)
                payload = _read_http_error(json_error.exception)
                self.assertEqual(json_error.exception.code, 400)
                self.assertEqual(payload["code"], "bad_request")
            finally:
                runtime.stop()


if __name__ == "__main__":
    unittest.main()
