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

from modulos.modulo13_laboratorio_pdf_segmentacion.controlador_laboratorio_pdf import PdfProblemGoldenController, ProblemPageRecord
from modulos.instance_factory.models import InstancePipelineContext, PipelineStep, StageStatus, StagingProblemRecord
from modulos.instance_factory.pipeline import InstancePdfPipelineService
from modulos.instance_factory.staging import InstanceStagingStore
import modulos.instance_factory.web_server as web_server_module
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
        self.invalidated_records_on_update: list[StagingProblemRecord] = []
        self.invalidated_records_on_delete: list[StagingProblemRecord] = []

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
                self._last_page_boxes_invalidated_count = len(self.invalidated_records_on_update)
                self._last_page_boxes_invalidated_records = list(self.invalidated_records_on_update)
                return page
        raise KeyError(record_id)

    def delete_page_record(self, record_id):
        self.calls.append(("delete_page_record", record_id))
        self.pages = [page for page in self.pages if str(page.record_id) != str(record_id)]
        self._last_page_removed_invalidated_count = len(self.invalidated_records_on_delete)
        self._last_page_removed_invalidated_records = list(self.invalidated_records_on_delete)
        return list(self.pages)

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


class _CountingGolden:
    def __init__(self, rows: list[ProblemPageRecord]) -> None:
        self.rows = list(rows)
        self.load_count = 0

    def load_instance(self, _name: str) -> list[ProblemPageRecord]:
        self.load_count += 1
        return list(self.rows)


class _CountingRealGolden(PdfProblemGoldenController):
    def __init__(self, golden_root: Path) -> None:
        super().__init__(golden_root=golden_root)
        self.load_count = 0

    def load_instance(self, name: str) -> list[ProblemPageRecord]:
        self.load_count += 1
        return super().load_instance(name)


class _CountingStagingStore(InstanceStagingStore):
    def __init__(self, context: InstancePipelineContext, root: Path) -> None:
        super().__init__(context, root=root)
        self.load_count = 0

    def load_record_entries(self):
        self.load_count += 1
        return super().load_record_entries()

    def load_records(self) -> list[StagingProblemRecord]:
        self.load_count += 1
        return super().load_records()


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
    def test_snapshot_reuses_loaded_pages_and_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_image = root / "page.png"
            page_image.write_bytes(b"png")
            crop = root / "crop.png"
            crop.write_bytes(b"png")
            context = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(root / "book.pdf"))
            golden = _CountingGolden([
                ProblemPageRecord(
                    record_id="page_001",
                    pdf_path=str(root / "book.pdf"),
                    page_number=1,
                    image_path=page_image,
                    boxes=[(1, 2, 30, 40)],
                    reviewed=True,
                )
            ])
            store = _CountingStagingStore(context, root=root / "staging")
            store.upsert_record(
                StagingProblemRecord(
                    record_id="crop_001",
                    crop_id="crop_001",
                    crop_path=str(crop),
                    status=StageStatus.NEEDS_REVIEW,
                    raw_ocr="Halle x",
                )
            )
            store.load_count = 0
            service = InstancePdfPipelineService(context, golden_controller=golden, staging_store=store)
            runtime = FactoryWebRuntime(context, service=service)

            snapshot = runtime._snapshot()

            self.assertEqual(snapshot["summary"]["records_total"], 1)
            self.assertEqual(snapshot["summary"]["pages_total"], 1)
            self.assertEqual(len(snapshot["timeline"]), 6)
            self.assertEqual(golden.load_count, 1)
            self.assertEqual(store.load_count, 1)

    def test_service_load_pages_uses_file_signature_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(root / "book.pdf"))
            golden = _CountingRealGolden(root / "golden")
            instance = golden.instance_dir(context.instance_name)
            records_dir = instance / "records"
            pages_dir = instance / "pages_png"
            records_dir.mkdir(parents=True, exist_ok=True)
            pages_dir.mkdir(parents=True, exist_ok=True)
            (pages_dir / "page_001.png").write_bytes(b"png")
            record_path = records_dir / "page_001.json"
            record_path.write_text(
                json.dumps(
                    {
                        "record_id": "page_001",
                        "pdf_path": str(root / "book.pdf"),
                        "page_number": 1,
                        "image_rel": "pages_png/page_001.png",
                        "boxes_px": [[1, 2, 30, 40]],
                        "detector_source": "pdf_factory:test",
                        "reviewed": False,
                        "layout_mode": "auto",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            service = InstancePdfPipelineService(
                context,
                golden_controller=golden,
                staging_store=InstanceStagingStore(context, root=root / "staging"),
            )

            first = service.load_pages()
            first[0].boxes = [(99, 99, 100, 100)]
            second = service.load_pages()

            self.assertEqual(golden.load_count, 1)
            self.assertEqual(second[0].boxes, [(1, 2, 30, 40)])

            payload = json.loads(record_path.read_text(encoding="utf-8"))
            payload["boxes_px"] = [[5, 6, 70, 80], [9, 10, 90, 100]]
            record_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            third = service.load_pages()

            self.assertEqual(golden.load_count, 2)
            self.assertEqual(third[0].boxes, [(5, 6, 70, 80), (9, 10, 90, 100)])

    def test_snapshot_reuses_record_web_payload_cache_until_record_changes(self) -> None:
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
                    raw_ocr="OCR inicial",
                )
            )
            runtime = FactoryWebRuntime(context, service=_FakeService(context, store))
            register_count = {"calls": 0}
            original_register_file = runtime._register_file

            def counting_register_file(path: Path) -> str:
                register_count["calls"] += 1
                return original_register_file(path)

            runtime._register_file = counting_register_file  # type: ignore[method-assign]

            first = runtime._snapshot()
            second = runtime._snapshot()

            self.assertEqual(first["records"][0]["raw_ocr"], "OCR inicial")
            self.assertEqual(second["records"][0]["raw_ocr"], "OCR inicial")
            self.assertEqual(register_count["calls"], 1)

            record = store.get_record("crop_001")
            assert record is not None
            record.raw_ocr = "OCR actualizado"
            store.upsert_record(record)

            third = runtime._snapshot()

            self.assertEqual(third["records"][0]["raw_ocr"], "OCR actualizado")
            self.assertEqual(register_count["calls"], 2)

    def test_snapshot_reuses_page_web_payload_cache_until_boxes_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_image = root / "page.png"
            page_image.write_bytes(b"png")
            context = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(root / "book.pdf"))
            service = _FakeService(context, InstanceStagingStore(context, root=root / "staging"))
            service.pages = [
                ProblemPageRecord(
                    record_id="page_001",
                    pdf_path=str(root / "book.pdf"),
                    page_number=1,
                    image_path=page_image,
                    boxes=[(1, 2, 30, 40)],
                    reviewed=False,
                )
            ]
            runtime = FactoryWebRuntime(context, service=service)
            register_count = {"calls": 0}
            original_register_file = runtime._register_file

            def counting_register_file(path: Path) -> str:
                register_count["calls"] += 1
                return original_register_file(path)

            runtime._register_file = counting_register_file  # type: ignore[method-assign]

            first = runtime._snapshot()
            second = runtime._snapshot()

            self.assertEqual(first["pages"][0]["boxes"], [[1, 2, 30, 40]])
            self.assertEqual(second["pages"][0]["boxes"], [[1, 2, 30, 40]])
            self.assertEqual(register_count["calls"], 1)

            service.pages[0].boxes = [(5, 6, 70, 80)]
            third = runtime._snapshot()

            self.assertEqual(third["pages"][0]["boxes"], [[5, 6, 70, 80]])
            self.assertEqual(register_count["calls"], 2)

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

    def test_web_runtime_serves_static_assets_with_etag_revalidation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(Path(tmp) / "book.pdf"))
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            runtime = FactoryWebRuntime(context, service=_FakeService(context, store))
            try:
                base = runtime.start()
                with urllib.request.urlopen(base + "app.js", timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    etag = response.headers.get("ETag")
                    self.assertTrue(etag)
                    self.assertIn("must-revalidate", response.headers.get("Cache-Control", ""))

                request = urllib.request.Request(base + "app.js", headers={"If-None-Match": etag or ""})
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(raised.exception.code, 304)
                self.assertEqual(raised.exception.headers.get("ETag"), etag)
            finally:
                runtime.stop()

    def test_web_runtime_serves_pdf_pages_with_long_lived_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_image = root / "page.png"
            page_image.write_bytes(b"png")
            context = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            runtime = FactoryWebRuntime(context, service=_FakeService(context, store))
            runtime._render_pdf_page = lambda _page, dpi=140: page_image
            try:
                base = runtime.start()
                with urllib.request.urlopen(base + "api/pdf/page?page=1&dpi=150", timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    etag = response.headers.get("ETag")
                    self.assertTrue(etag)
                    cache_control = response.headers.get("Cache-Control", "")
                    self.assertIn("max-age=86400", cache_control)
                    self.assertIn("immutable", cache_control)

                request = urllib.request.Request(base + "api/pdf/page?page=1&dpi=150", headers={"If-None-Match": etag or ""})
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(raised.exception.code, 304)
                self.assertEqual(raised.exception.headers.get("ETag"), etag)
                self.assertIn("immutable", raised.exception.headers.get("Cache-Control", ""))
            finally:
                runtime.stop()

    def test_web_runtime_lazy_loads_mathjax_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(Path(tmp) / "book.pdf"))
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            runtime = FactoryWebRuntime(context, service=_FakeService(context, store))
            try:
                base = runtime.start()
                with urllib.request.urlopen(base, timeout=5) as response:
                    index_html = response.read().decode("utf-8")
                with urllib.request.urlopen(base + "app.js", timeout=5) as response:
                    app_js = response.read().decode("utf-8")

                self.assertIn("window.MathJax", index_html)
                self.assertNotIn("tex-chtml.js", index_html)
                self.assertIn("ensureMathJaxLoaded", app_js)
                self.assertIn("tex-chtml.js", app_js)
            finally:
                runtime.stop()

    def test_web_app_debounces_library_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(Path(tmp) / "book.pdf"))
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            runtime = FactoryWebRuntime(context, service=_FakeService(context, store))
            try:
                base = runtime.start()
                with urllib.request.urlopen(base + "app.js", timeout=5) as response:
                    app_js = response.read().decode("utf-8")

                self.assertIn("LIBRARY_SEARCH_DEBOUNCE_MS", app_js)
                self.assertIn("scheduleLibrarySearchRender", app_js)
                self.assertIn("buildLibrarySearchKey", app_js)
                self.assertIn("row._searchKey", app_js)
                self.assertNotIn("state.library.query = event.target.value;\n    state.library.screen = \"books\";\n    state.library.showInstanceForm = false;\n    ensureLibrarySelection();\n    renderLibraryContent();", app_js)
            finally:
                runtime.stop()

    def test_web_app_windows_large_library_lists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(Path(tmp) / "book.pdf"))
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            runtime = FactoryWebRuntime(context, service=_FakeService(context, store))
            try:
                base = runtime.start()
                with urllib.request.urlopen(base + "app.js", timeout=5) as response:
                    app_js = response.read().decode("utf-8")

                self.assertIn("LIBRARY_BOOKS_INITIAL_LIMIT", app_js)
                self.assertIn("LIBRARY_INSTANCES_INITIAL_LIMIT", app_js)
                self.assertIn("windowLibraryRows", app_js)
                self.assertIn("loadMoreBooks", app_js)
                self.assertIn("loadMoreInstances", app_js)
            finally:
                runtime.stop()

    def test_web_app_prefetches_adjacent_pdf_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(Path(tmp) / "book.pdf"))
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            runtime = FactoryWebRuntime(context, service=_FakeService(context, store))
            try:
                base = runtime.start()
                with urllib.request.urlopen(base + "app.js", timeout=5) as response:
                    app_js = response.read().decode("utf-8")

                self.assertIn("PDF_PAGE_PREFETCH_RADIUS", app_js)
                self.assertIn("PDF_IMAGE_CACHE_LIMIT", app_js)
                self.assertIn("const pdfImageCache = new Map()", app_js)
                self.assertIn("function prefetchPdfPages", app_js)
                self.assertIn("getCachedPdfImage(pdfPageImageUrl(page, dpi))", app_js)
                self.assertIn("prefetchPdfPages(state.pdfPage, pageCount, 150)", app_js)
                self.assertIn("drawLoadedImageOnCanvas", app_js)
            finally:
                runtime.stop()

    def test_web_app_virtualizes_crop_gallery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(Path(tmp) / "book.pdf"))
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            runtime = FactoryWebRuntime(context, service=_FakeService(context, store))
            try:
                base = runtime.start()
                with urllib.request.urlopen(base + "app.js", timeout=5) as response:
                    app_js = response.read().decode("utf-8")

                self.assertIn("CROP_GALLERY_FULL_LIMIT", app_js)
                self.assertIn("cropGalleryRenderState", app_js)
                self.assertIn("renderCropGallery(records)", app_js)
                self.assertIn("data-crop-window-offset", app_js)
                self.assertIn("recordJumpBtn", app_js)
                self.assertIn("data-delete-page", app_js)
                self.assertIn("Eliminar pagina detectada", app_js)
                self.assertIn("captureBoxesScrollState", app_js)
                self.assertIn("restoreBoxesScrollState", app_js)
                self.assertIn("setBoxMode(\"select\")", app_js)
                self.assertIn("function syncBoxEditorUi", app_js)
                self.assertIn("data-box-coords", app_js)
                self.assertIn("function scheduleBoxEditorFrame", app_js)
                self.assertIn("scheduleBoxEditorFrame({ updateRows: true })", app_js)
                self.assertIn("cancelBoxEditorFrame()", app_js)
                self.assertIn("redrawBoxesWithActiveDragPreview", app_js)
                self.assertIn("function syncFigureSegmentEditorUi", app_js)
                self.assertIn("setFigureSegmentMode(\"select\")", app_js)
                self.assertIn("function scheduleFigureEditorFrame", app_js)
                self.assertIn("cancelFigureEditorFrame()", app_js)
                self.assertIn("redrawFigureSegmentsWithActiveDragPreview", app_js)
                self.assertIn("data-lazy-technical-detail", app_js)
                self.assertIn("function hydrateLazyTechnicalDetail", app_js)
                self.assertIn("lazyTechnicalDetails.set", app_js)
                self.assertIn("Abre para cargar el detalle tecnico.", app_js)
                self.assertIn("document.addEventListener(\"toggle\", handleLazyTechnicalDetailToggle, true)", app_js)
                self.assertIn("function mathPreviewSignature", app_js)
                self.assertIn("function latexPreviewSourceKey", app_js)
                self.assertIn("data-latex-source", app_js)
                self.assertIn("preview.dataset.latexSource !== sourceKey", app_js)
                self.assertIn("preview.dataset.mathjaxSignature !== signature", app_js)
                self.assertNotIn("state.boxMode = \"select\"; renderBoxesStage();", app_js)
                self.assertNotIn("redrawFigureSegments();\n      renderOcrStage();", app_js)
                self.assertNotIn("const body = mode === \"text\" ? String(payload ?? \"\") : JSON.stringify(payload || {}, null, 2);", app_js)
                self.assertIn("compact: true", app_js)
                self.assertIn("deleteSelectedDetectedPage(btn.dataset.deletePage)", app_js)
                self.assertIn("APP_VERSION_POLL_MS", app_js)
                self.assertIn("APP_VERSION_HIDDEN_POLL_MS", app_js)
                self.assertIn("window.setTimeout(checkAppVersionChanged, delay)", app_js)
                self.assertNotIn("records.map(recordCardHtml).join(\"\")", app_js)
                self.assertNotIn("<select id=\"recordJump\">", app_js)
                self.assertNotIn("window.setTimeout(checkAppVersionChanged, 5000)", app_js)
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
                self.assertIn("backend_version", before)
                self.assertIn("backend_boot_version", before)
                self.assertFalse(before["backend_restart_required"])
                self.assertNotEqual(before.get("reload_token"), after.get("reload_token"))
            finally:
                runtime.stop()

    def test_web_app_version_uses_short_server_cache(self) -> None:
        web_server_module._WEB_APP_VERSION_CACHE.clear()
        with tempfile.TemporaryDirectory() as tmp:
            context = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(Path(tmp) / "book.pdf"))
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            runtime = FactoryWebRuntime(context, service=_FakeService(context, store))
            try:
                with patch.object(web_server_module, "_version_rows", wraps=web_server_module._version_rows) as rows:
                    first = web_server_module.build_web_app_version(runtime.static_root)
                    first["assets"][0]["size"] = 999999
                    second = web_server_module.build_web_app_version(runtime.static_root)

                self.assertEqual(rows.call_count, 2)
                self.assertEqual(second["schema_version"], "pdf_factory_web_app_version_v1")
                self.assertNotEqual(second["assets"][0]["size"], 999999)
            finally:
                web_server_module._WEB_APP_VERSION_CACHE.clear()

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
                self.assertEqual(status["record_update_seq"], 1)
                self.assertEqual(len(status["record_updates"]), 1)
                self.assertEqual(status["record_updates"][0]["record_id"], "crop_001")
                self.assertEqual(status["record_updates"][0]["record"]["record_id"], "crop_001")
                synced_status = _get_json(base, f"api/ocr/jobs/status?job_id={job_id}&since_update=1")
                self.assertEqual(synced_status["record_update_seq"], 1)
                self.assertEqual(synced_status["record_updates"], [])
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

    def test_web_runtime_skips_graph_segmentation_when_already_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = root / "crop.png"
            crop.write_bytes(b"png")
            context = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(root / "book.pdf"))
            store = InstanceStagingStore(context, root=root / "staging")
            record = StagingProblemRecord(
                record_id="crop_001",
                crop_id="crop_001",
                crop_path=str(crop),
                status=StageStatus.NEEDS_REVIEW,
                figure_segmentation={
                    "status": StageStatus.READY,
                    "segments_total": 0,
                    "segments": [],
                },
            )
            record.set_step(PipelineStep.SEGMENTATION, StageStatus.READY, "segmentacion ya disponible", segments_total=0)
            store.upsert_record(record)
            service = _FakeService(context, store)
            endpoint = _FakeEndpointManager()
            runtime = FactoryWebRuntime(context, service=service, endpoint_manager=endpoint)
            try:
                base = runtime.start()
                started = _post_json(base, "api/ocr/jobs/start", {"provider": "local", "record_ids": ["crop_001"]})
                job_id = started["job_id"]

                status = {}
                for _ in range(40):
                    status = _get_json(base, f"api/ocr/jobs/status?job_id={job_id}")
                    if not status["running"]:
                        break
                    time.sleep(0.05)

                self.assertEqual(status["status"], "done")
                self.assertEqual(status["ok"], 1)
                self.assertEqual(
                    service.phase_calls,
                    [
                        ("crop_001", [], False, True),
                    ],
                )
            finally:
                runtime.stop()

    def test_web_runtime_reports_active_segmentation_progress(self) -> None:
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
            runtime = FactoryWebRuntime(context, service=service, endpoint_manager=_FakeEndpointManager())
            try:
                base = runtime.start()
                started = _post_json(base, "api/ocr/jobs/start", {"provider": "local", "record_ids": ["crop_001", "crop_002"]})
                job_id = started["job_id"]

                progress = {}
                for _ in range(30):
                    progress = _get_json(base, f"api/ocr/jobs/status?job_id={job_id}")
                    if progress.get("phase") == "segmentation" and progress.get("active_position"):
                        break
                    time.sleep(0.05)

                self.assertEqual(progress["phase_label"], "Segmentacion grafica")
                self.assertEqual(progress["active_position"], 1)
                self.assertEqual(progress["progress_label"], "1/2")
                self.assertIn("1/2", progress["message"])

                status = progress
                for _ in range(80):
                    status = _get_json(base, f"api/ocr/jobs/status?job_id={job_id}")
                    if not status["running"]:
                        break
                    time.sleep(0.05)
                self.assertEqual(status["status"], "done")
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
                    structured_ocr={"status": StageStatus.READY, "items": [{"numero": "1", "enunciado": "Halle x."}]},
                    figure_segmentation={
                        "status": StageStatus.READY,
                        "segments_total": 1,
                        "segments": [{"idx": 1, "bbox_px": [1, 2, 30, 40], "image_path": str(Path(tmp) / "seg_01.png")}],
                    },
                    normalized={"numero": "1"},
                    training_examples=[{"notes": "correccion extensa", "payload": "x" * 250}],
                    trace={"debug": "interno"},
                    artifacts={"raw_model_dump": "interno"},
                    golden_sync={"last": "interno"},
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
                record_payload = snapshot["records"][0]
                self.assertEqual(record_payload["training_examples_total"], 1)
                self.assertNotIn("training_examples", record_payload)
                self.assertNotIn("trace", record_payload)
                self.assertNotIn("artifacts", record_payload)
                self.assertEqual(record_payload["golden_sync"], {"last": "interno"})
                self.assertEqual(record_payload["structured_ocr"]["items_total"], 1)
                self.assertNotIn("items", record_payload["structured_ocr"])
                self.assertEqual(record_payload["figure_segmentation"]["segments_total"], 1)
                self.assertNotIn("segments", record_payload["figure_segmentation"])
                self.assertEqual(record_payload["figure_segments_web"][0]["bbox_px"], [1, 2, 30, 40])
                self.assertEqual(record_payload["structured_items_web"][0]["numero"], "1")

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

    def test_promotion_upload_uses_library_local_db_and_blocks_external_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = root / "crop.png"
            crop.write_bytes(b"png")
            context = InstancePipelineContext(
                book_code="ALG01",
                instance_type="S01",
                pdf_path=str(root / "book.pdf"),
                db_name="context_db_should_not_win",
            )
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_record(
                StagingProblemRecord(
                    record_id="crop_001",
                    crop_id="crop_001",
                    crop_path=str(crop),
                    status=StageStatus.READY,
                    normalized={
                        "latex_rendered_item": r"\item[\textbf{1.}] [[curso=Algebra]] [[tema=Ecuaciones]] [[Estado=sin_revisar]] [[Clave=B]] Halle $x$. A)$1$ B)$2$"
                    },
                    models={"ocr": "test-ocr", "figure_segmentation": "test-figure"},
                    source={"page_number": 1, "bbox_px": [1, 2, 3, 4]},
                )
            )
            runtime = FactoryWebRuntime(context, service=_FakeService(context, store))
            setattr(runtime, "_library_db_name", "library_local_db")
            try:
                base = runtime.start()
                body = json.dumps(
                    {
                        "db_name": "library_local_db",
                        "db_profile": "biblioteca",
                        "record_ids": ["crop_001"],
                        "dry_run": True,
                    }
                ).encode("utf-8")
                request = urllib.request.Request(
                    base + "api/promotion/upload",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    report = json.loads(response.read().decode("utf-8"))
                self.assertEqual(report["db_name"], "library_local_db")
                self.assertEqual(report["db_profile"], "local_mirror")
                self.assertEqual(report["rows"][0]["status"], "ready")

                body = json.dumps(
                    {
                        "db_name": "library_local_db",
                        "db_profile": "cloud",
                        "record_ids": ["crop_001"],
                        "dry_run": True,
                    }
                ).encode("utf-8")
                request = urllib.request.Request(
                    base + "api/promotion/upload",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(ctx.exception.code, 400)
                payload = json.loads(ctx.exception.read().decode("utf-8"))
                self.assertEqual(payload["code"], "non_local_db_profile_blocked")
            finally:
                runtime.stop()

    def test_promotion_upload_compact_returns_touched_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = root / "crop.png"
            crop.write_bytes(b"png")
            context = InstancePipelineContext(book_code="ALG01", instance_type="S01", pdf_path=str(root / "book.pdf"), db_name="library_db")
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_record(
                StagingProblemRecord(
                    record_id="crop_001",
                    crop_id="crop_001",
                    crop_path=str(crop),
                    status=StageStatus.READY,
                    normalized={
                        "latex_rendered_item": r"\item[\textbf{1.}] [[curso=Algebra]] [[tema=Ecuaciones]] [[Estado=sin_revisar]] [[Clave=B]] Halle $x$. A)$1$ B)$2$"
                    },
                )
            )

            def _fake_promote(staging, _context, **_kwargs):
                record = staging.get_record("crop_001")
                self.assertIsNotNone(record)
                record.artifacts = {**dict(record.artifacts or {}), "db_problem_id": 123}
                staging.upsert_record(record)
                return {
                    "schema_version": "pdf_factory_db_promotion_report_v1",
                    "db_name": "library_db",
                    "db_profile": "local_mirror",
                    "dry_run": False,
                    "inserted": 1,
                    "updated": 0,
                    "skipped": 0,
                    "errors": 0,
                    "rows": [{"record_id": "crop_001", "status": "inserted", "problem_id": 123}],
                }

            runtime = FactoryWebRuntime(context, service=_FakeService(context, store))
            try:
                base = runtime.start()
                with patch("modulos.instance_factory.web_server.promote_staging_records_to_db", _fake_promote):
                    report = _post_json(
                        base,
                        "api/promotion/upload",
                        {
                            "db_name": "library_db",
                            "db_profile": "biblioteca",
                            "record_ids": ["crop_001"],
                            "dry_run": False,
                            "confirm": True,
                            "compact": True,
                        },
                    )
                self.assertEqual(report["schema_version"], "pdf_factory_db_promotion_report_v1")
                self.assertEqual(report["records"][0]["record_id"], "crop_001")
                self.assertNotIn("artifacts", report["records"][0])
                self.assertNotIn("audit", report["records"][0])
                self.assertIn("summary", report)
                self.assertIn("timeline", report)
                self.assertNotIn("snapshot", report)
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

                detected_compact = _post_json(
                    base,
                    "api/pages/detect",
                    {"pages": "1", "dpi": 150, "confidence": 0.4, "compact": True, "include_summary": False},
                )
                self.assertEqual(detected_compact["schema_version"], "pdf_factory_web_pages_detected_v1")
                self.assertEqual(detected_compact["selected_pages"], [1])
                self.assertEqual(detected_compact["pages"][0]["record_id"], "page_001")
                self.assertNotIn("records", detected_compact)
                self.assertNotIn("summary", detected_compact)
                self.assertNotIn("timeline", detected_compact)

                snapshot = _post_json(
                    base,
                    "api/pages/boxes",
                    {"record_id": "page_001", "boxes": [[10, 20, 60, 80]], "layout_mode": "una_columna", "reviewed": True},
                )
                self.assertEqual(snapshot["pages"][0]["boxes"], [[10, 20, 60, 80]])
                self.assertEqual(service.calls[-1][0], "update_page_boxes")

                compact_page = _post_json(
                    base,
                    "api/pages/boxes",
                    {
                        "record_id": "page_001",
                        "boxes": [[11, 22, 66, 88]],
                        "layout_mode": "una_columna",
                        "reviewed": True,
                        "compact": True,
                    },
                )
                self.assertEqual(compact_page["schema_version"], "pdf_factory_web_page_saved_v1")
                self.assertEqual(compact_page["page"]["record_id"], "page_001")
                self.assertEqual(compact_page["page"]["boxes"], [[11, 22, 66, 88]])
                self.assertIn("summary", compact_page)
                self.assertIn("timeline", compact_page)

                compact_page_without_summary = _post_json(
                    base,
                    "api/pages/boxes",
                    {
                        "record_id": "page_001",
                        "boxes": [[12, 24, 68, 90]],
                        "layout_mode": "una_columna",
                        "reviewed": True,
                        "compact": True,
                        "include_summary": False,
                    },
                )
                self.assertEqual(compact_page_without_summary["schema_version"], "pdf_factory_web_page_saved_v1")
                self.assertEqual(compact_page_without_summary["page"]["boxes"], [[12, 24, 68, 90]])
                self.assertNotIn("summary", compact_page_without_summary)
                self.assertNotIn("timeline", compact_page_without_summary)

                invalidated_record = store.get_record("crop_001")
                assert invalidated_record is not None
                invalidated_record.raw_ocr = ""
                invalidated_record.status = StageStatus.PENDING
                service.invalidated_records_on_update = [invalidated_record]
                compact_invalidated = _post_json(
                    base,
                    "api/pages/boxes",
                    {
                        "record_id": "page_001",
                        "boxes": [[13, 26, 70, 92]],
                        "layout_mode": "una_columna",
                        "reviewed": True,
                        "compact": True,
                        "include_summary": False,
                    },
                )
                self.assertEqual(compact_invalidated["schema_version"], "pdf_factory_web_page_saved_v1")
                self.assertEqual(compact_invalidated["invalidated_records"], 1)
                self.assertEqual(compact_invalidated["updated_records"][0]["record_id"], "crop_001")
                self.assertNotIn("records", compact_invalidated)
                self.assertNotIn("summary", compact_invalidated)
                self.assertNotIn("timeline", compact_invalidated)

                deleted_page = _post_json(base, "api/pages/delete", {"record_id": "page_001"})
                self.assertEqual(deleted_page["schema_version"], "pdf_factory_web_page_deleted_v1")
                self.assertEqual(deleted_page["record_id"], "page_001")
                self.assertEqual(deleted_page["pages"], [])
                self.assertIn(("delete_page_record", "page_001"), service.calls)

                service.pages = [
                    ProblemPageRecord(
                        record_id="page_002",
                        pdf_path=str(pdf),
                        page_number=2,
                        image_path=page_image,
                        boxes=[(3, 4, 50, 60)],
                        reviewed=False,
                    )
                ]
                service.invalidated_records_on_delete = [invalidated_record]
                deleted_compact = _post_json(
                    base,
                    "api/pages/delete",
                    {"record_id": "page_002", "compact": True, "include_summary": False},
                )
                self.assertEqual(deleted_compact["schema_version"], "pdf_factory_web_page_deleted_v1")
                self.assertEqual(deleted_compact["record_id"], "page_002")
                self.assertEqual(deleted_compact["pages"], [])
                self.assertEqual(deleted_compact["invalidated_records"], 1)
                self.assertEqual(deleted_compact["updated_records"][0]["record_id"], "crop_001")
                self.assertNotIn("summary", deleted_compact)
                self.assertNotIn("timeline", deleted_compact)
                self.assertNotIn("records", deleted_compact)

                _post_json(base, "api/staging/materialize", {})
                staging_compact = _post_json(base, "api/staging/materialize", {"compact": True})
                self.assertEqual(staging_compact["schema_version"], "pdf_factory_web_staging_materialized_v1")
                self.assertEqual(staging_compact["records"][0]["record_id"], "crop_001")
                self.assertIn("summary", staging_compact)
                self.assertIn("timeline", staging_compact)
                self.assertNotIn("pages", staging_compact)
                self.assertNotIn("models", staging_compact)
                _post_json(base, "api/ocr/run", {"provider": "local", "curso": "ALG", "tema": "EC", "start_n": 3, "limit": 1, "record_id": "crop_001"})
                ocr_compact = _post_json(
                    base,
                    "api/ocr/run",
                    {
                        "provider": "local",
                        "record_id": "crop_001",
                        "compact": True,
                        "include_summary": False,
                    },
                )
                self.assertEqual(ocr_compact["schema_version"], "pdf_factory_web_ocr_run_v1")
                self.assertEqual(ocr_compact["record"]["record_id"], "crop_001")
                self.assertEqual(ocr_compact["records"][0]["record_id"], "crop_001")
                self.assertNotIn("summary", ocr_compact)
                self.assertNotIn("timeline", ocr_compact)
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
                self.assertIn("summary", raw_compact)
                self.assertIn("timeline", raw_compact)
                raw_minimal = _post_json(
                    base,
                    "api/ocr/raw",
                    {"record_id": "crop_001", "raw_ocr": "texto lote minimo", "compact": True, "include_summary": False},
                )
                self.assertEqual(raw_minimal["schema_version"], "pdf_factory_web_record_saved_v1")
                self.assertEqual(raw_minimal["record"]["raw_ocr"], "texto lote minimo")
                self.assertNotIn("summary", raw_minimal)
                self.assertNotIn("timeline", raw_minimal)
                summary_payload = _get_json(base, "api/summary")
                self.assertEqual(summary_payload["schema_version"], "pdf_factory_web_summary_v1")
                self.assertIn("summary", summary_payload)
                self.assertIn("timeline", summary_payload)
                segment_compact = _post_json(base, "api/ocr/segments/boxes", {"record_id": "crop_001", "boxes": [[5, 6, 77, 88]], "compact": True})
                self.assertEqual(segment_compact["schema_version"], "pdf_factory_web_record_saved_v1")
                self.assertEqual(segment_compact["record"]["figure_segmentation"]["segments_total"], 1)
                self.assertIn("summary", segment_compact)
                self.assertIn(("update_figure_segments", "crop_001", [[2, 3, 44, 55]]), service.calls)
                self.assertEqual(segment_snapshot["records"][0]["figure_segmentation"]["segments_total"], 1)
                self.assertIn(("normalize_existing_ocr", "", []), service.calls)
                normalize_compact = _post_json(base, "api/normalize", {"record_id": "crop_001", "compact": True})
                self.assertEqual(normalize_compact["schema_version"], "pdf_factory_web_record_saved_v1")
                self.assertEqual(normalize_compact["record"]["record_id"], "crop_001")
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
