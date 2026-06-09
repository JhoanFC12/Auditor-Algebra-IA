from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

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
        self.pages = []

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
    ):
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


def _post_json(base: str, path: str, body: dict) -> dict:
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _read_http_error(exc: urllib.error.HTTPError) -> dict:
    return json.loads(exc.read().decode("utf-8"))


class InstanceFactoryWebServerTests(unittest.TestCase):
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
                self.assertIn(("update_figure_segments", "crop_001", [[2, 3, 44, 55]]), service.calls)
                self.assertEqual(segment_snapshot["records"][0]["figure_segmentation"]["segments_total"], 1)
                self.assertIn(("normalize_existing_ocr", "", []), service.calls)
                _post_json(base, "api/normalize", {"record_id": "crop_001"})
                self.assertIn(("normalize_existing_ocr", "crop_001", []), service.calls)
                _post_json(base, "api/normalize", {"record_ids": ["crop_001", "crop_001", ""]})
                self.assertIn(("normalize_existing_ocr", "", ["crop_001"]), service.calls)

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
