from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from modulos.modulo13_laboratorio_pdf_segmentacion.controlador_laboratorio_pdf import (
    DEFAULT_PROBLEM_CROPS_LIVE_ROOT,
    PdfProblemGoldenController,
    ProblemPageRecord,
    sort_boxes_reading_order,
)

from .model_inventory import build_model_inventory_manifest, resolve_model_defaults
from .models import (
    PIPELINE_CONTRACT_VERSION,
    InstancePipelineContext,
    PipelineStep,
    StageStatus,
    StagingProblemRecord,
    build_pipeline_contract,
    utc_now_text,
)
from .page_selection import parse_page_selection
from .staging import InstanceStagingStore


class InstancePdfPipelineService:
    def __init__(
        self,
        context: InstancePipelineContext,
        *,
        golden_controller: PdfProblemGoldenController | None = None,
        staging_store: InstanceStagingStore | None = None,
    ) -> None:
        self.context = context
        self.golden = golden_controller or PdfProblemGoldenController()
        self.staging = staging_store or InstanceStagingStore(context)
        self.models = resolve_model_defaults()

    @classmethod
    def from_library_instance(
        cls,
        book: dict[str, Any],
        instance: dict[str, Any],
        *,
        db_name: str = "",
        session_path: str | Path | None = None,
        golden_controller: PdfProblemGoldenController | None = None,
        staging_store: InstanceStagingStore | None = None,
    ) -> "InstancePdfPipelineService":
        context = InstancePipelineContext.from_library_instance(
            book,
            instance,
            db_name=db_name,
            session_path=session_path,
        )
        return cls(context, golden_controller=golden_controller, staging_store=staging_store)

    @classmethod
    def run_from_library_instance(
        cls,
        book: dict[str, Any],
        instance: dict[str, Any],
        *,
        db_name: str = "",
        session_path: str | Path | None = None,
        golden_controller: PdfProblemGoldenController | None = None,
        staging_store: InstanceStagingStore | None = None,
        **run_options: Any,
    ) -> dict[str, Any]:
        service = cls.from_library_instance(
            book,
            instance,
            db_name=db_name,
            session_path=session_path,
            golden_controller=golden_controller,
            staging_store=staging_store,
        )
        return service.run_instance_pipeline(**run_options)

    def load_pages(self) -> list[ProblemPageRecord]:
        return self._dedupe_page_rows(self.golden.load_instance(self.context.instance_name))

    @staticmethod
    def _dedupe_page_rows(rows: list[ProblemPageRecord]) -> list[ProblemPageRecord]:
        by_page: dict[int, ProblemPageRecord] = {}
        for index, row in enumerate(rows or []):
            page_number = int(row.page_number or 0)
            if page_number <= 0:
                continue
            current = by_page.get(page_number)
            if current is None or InstancePdfPipelineService._page_row_score(row, index) >= InstancePdfPipelineService._page_row_score(current, -1):
                by_page[page_number] = row
        return [by_page[key] for key in sorted(by_page)]

    @staticmethod
    def _page_row_score(row: ProblemPageRecord, index: int) -> tuple[int, int, int, int, str]:
        image_exists = 1 if Path(row.image_path).exists() else 0
        detector = str(row.detector_source or "").lower()
        return (
            1 if detector.startswith("pdf_factory") else 0,
            1 if bool(row.reviewed) else 0,
            len(row.boxes or []),
            image_exists,
            int(index),
            str(row.record_id or ""),
        )

    def resolve_page_selection(self, raw_pages: str) -> list[int]:
        import fitz

        pdf_path = self.context.resolved_pdf_path()
        if not pdf_path.exists():
            raise FileNotFoundError(f"No se encontro el PDF: {pdf_path}")
        with fitz.open(pdf_path) as document:
            return parse_page_selection(raw_pages, document.page_count)

    def _model_snapshot(
        self,
        *,
        provider: str = "",
        confidence_overrides: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        provider_overrides = {"ocr": provider} if provider else None
        return self.models.to_dict(
            provider_overrides=provider_overrides,
            confidence_overrides=confidence_overrides,
        )

    def build_stage_overview(self) -> list[dict[str, str]]:
        pages = self.load_pages()
        records = self.staging.load_records()
        summary = self.staging.summarize_records(records)
        boxes_total = sum(len(row.boxes) for row in pages)
        reviewed_pages = sum(1 for row in pages if row.reviewed)
        pages_status = self._status_from_counts(
            total=len(pages),
            ready=reviewed_pages,
            needs_review=len(pages) - reviewed_pages if pages else 0,
        )
        boxes_status = self._aggregate_step_status(records, PipelineStep.BOXES)
        if not records:
            boxes_status = StageStatus.READY if boxes_total else StageStatus.PENDING
        crops_status = self._aggregate_step_status(records, PipelineStep.CROPS)
        ocr_status = self._aggregate_step_status(records, PipelineStep.OCR)
        normalization_status = self._aggregate_step_status(records, PipelineStep.NORMALIZATION)
        staging_status = self._aggregate_record_status(records)
        review_detail = (
            f"{summary['ready']}/{summary['records_total']} listos"
            if summary["records_total"]
            else "sin registros"
        )
        return [
            {
                "stage": "Paginas",
                "status": pages_status,
                "detail": f"{len(pages)} pagina(s), {reviewed_pages}/{len(pages)} revisada(s)",
            },
            {
                "stage": "Boxes",
                "status": boxes_status,
                "detail": f"{boxes_total} box(es) detectados para revisar",
            },
            {
                "stage": "Crops",
                "status": crops_status,
                "detail": f"{summary['crops_found']}/{summary['records_total']} crop(s) disponibles",
            },
            {
                "stage": "OCR / Segmentacion",
                "status": ocr_status,
                "detail": f"{summary['ocr_done']}/{summary['records_total']} con OCR, {summary['segments_done']} con segmentacion",
            },
            {
                "stage": "Revision / Normalizacion pendiente",
                "status": normalization_status,
                "detail": f"{summary['normalized_done']}/{summary['records_total']} borrador(es) de revision",
            },
            {
                "stage": "Staging",
                "status": staging_status,
                "detail": f"{review_detail}; {summary['errors']} con error; no inserta directo en problemas",
            },
        ]

    def build_contract_report(self) -> dict[str, Any]:
        records = self.staging.load_records()
        return {
            "schema_version": "instance_pdf_pipeline_contract_report_v1",
            "contract_version": PIPELINE_CONTRACT_VERSION,
            "context": self.context.to_dict(),
            "contract": build_pipeline_contract(),
            "validation": self.staging.validate_contract(records),
            "stage_overview": self.build_stage_overview(),
            "summary": self.staging.summarize_records(records),
        }

    def run_instance_pipeline(
        self,
        *,
        pages: str | list[int] | None = None,
        dpi: int = 300,
        confidence: float = 0.25,
        detect_pages: bool = False,
        materialize: bool = True,
        run_ocr: bool = False,
        normalize_existing: bool = False,
        provider: str = "hf",
        curso: str = "SIN_CURSO",
        tema: str = "SIN_TEMA",
        start_n: int = 1,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Run the instance PDF -> staging contract without GUI orchestration."""
        executed: list[dict[str, Any]] = []
        selected_pages: list[int] = []
        should_detect = bool(detect_pages or pages)
        if should_detect:
            if pages is None:
                raise ValueError("pages es requerido cuando detect_pages=True")
            selected_pages = self.resolve_page_selection(pages) if isinstance(pages, str) else [int(page) for page in pages]
            detected = self.detect_pdf_pages(selected_pages, dpi=dpi, confidence=confidence)
            executed.append(
                {
                    "step": PipelineStep.PAGES,
                    "status": StageStatus.READY if detected else StageStatus.PENDING,
                    "pages": selected_pages,
                    "records": len(detected),
                }
            )
        if materialize:
            materialized = self.materialize_crops_to_staging()
            executed.append(
                {
                    "step": PipelineStep.CROPS,
                    "status": self._aggregate_record_status(materialized),
                    "records": len(materialized),
                }
            )
        if run_ocr:
            processed = self.run_ocr_and_segmentation(
                provider=provider,
                curso=curso,
                tema=tema,
                start_n=start_n,
                limit=limit,
            )
            executed.append(
                {
                    "step": "ocr_segmentacion_normalizacion",
                    "status": self._aggregate_record_status(processed),
                    "records": len(processed),
                }
            )
        elif normalize_existing:
            normalized = self.normalize_existing_ocr()
            executed.append(
                {
                    "step": PipelineStep.NORMALIZATION,
                    "status": self._aggregate_record_status(normalized),
                    "records": len(normalized),
                }
            )
        self.staging.rewrite_manifest()
        records = self.staging.load_records()
        return {
            "schema_version": "instance_pdf_pipeline_run_v1",
            "contract_version": PIPELINE_CONTRACT_VERSION,
            "context": self.context.to_dict(),
            "selected_pages": selected_pages,
            "executed": executed,
            "status": self._aggregate_record_status(records),
            "stage_overview": self.build_stage_overview(),
            "staging_root": str(self.staging.root),
            "contract_report": self.build_contract_report(),
            "model_inventory": build_model_inventory_manifest(self.models),
            "policy": {
                "target": "staging_only",
                "never_insert_directly_into_problemas": True,
                "human_corrections_are_training_data": True,
            },
        }

    run_from_instance = run_instance_pipeline

    def build_instance_summary(self) -> dict[str, int]:
        pages = self.load_pages()
        records = self.staging.load_records()
        summary = self.staging.summarize_records(records)
        return {
            **summary,
            "pages_total": len(pages),
            "pages_reviewed": sum(1 for row in pages if row.reviewed),
            "boxes_total": sum(len(row.boxes) for row in pages),
            "pages_with_boxes": sum(1 for row in pages if row.boxes),
        }

    def build_page_box_overview(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in self.load_pages():
            boxes_total = len(row.boxes)
            status = StageStatus.PENDING
            if row.reviewed and boxes_total:
                status = StageStatus.READY
            elif row.reviewed:
                status = StageStatus.NEEDS_REVIEW
            elif boxes_total:
                status = StageStatus.NEEDS_REVIEW
            rows.append(
                {
                    "record_id": row.record_id,
                    "page_number": int(row.page_number),
                    "status": status,
                    "boxes_total": boxes_total,
                    "reviewed": bool(row.reviewed),
                    "layout_mode": row.layout_mode,
                    "detector_source": row.detector_source,
                    "image_path": str(row.image_path),
                }
            )
        return sorted(rows, key=lambda item: (int(item.get("page_number") or 0), str(item.get("record_id") or "")))

    def update_page_boxes(
        self,
        record_id: str,
        boxes: list[Any],
        *,
        layout_mode: str = "auto",
        reviewed: bool = True,
        reorder: bool = False,
    ) -> ProblemPageRecord:
        """Persist reviewed page boxes from a UI without opening the legacy editor."""
        rows = self._dedupe_page_rows(self.load_pages())
        target: ProblemPageRecord | None = None
        for row in rows:
            if str(row.record_id) == str(record_id):
                target = row
                break
        if target is None:
            raise KeyError(f"Pagina no encontrada en la instancia: {record_id}")
        clean_boxes = self._coerce_boxes(boxes)
        previous_boxes = list(target.boxes or [])
        previous_signature = self._boxes_signature(previous_boxes)
        target.layout_mode = str(layout_mode or target.layout_mode or "auto")
        target.boxes = sort_boxes_reading_order(clean_boxes, target.layout_mode) if reorder else clean_boxes
        target.reviewed = bool(reviewed)
        self.golden.upsert_instance_rows(self.context.instance_name, self._dedupe_page_rows(rows))
        if previous_signature != self._boxes_signature(target.boxes):
            self._invalidate_downstream_for_page_boxes_change(target, previous_boxes=previous_boxes)
        for row in self.load_pages():
            if str(row.record_id) == str(record_id):
                return row
        return target

    @staticmethod
    def _coerce_boxes(raw_boxes: list[Any]) -> list[tuple[int, int, int, int]]:
        clean: list[tuple[int, int, int, int]] = []
        for raw in raw_boxes or []:
            if not isinstance(raw, (list, tuple)) or len(raw) < 4:
                continue
            try:
                x1, y1, x2, y2 = [int(round(float(value))) for value in list(raw)[:4]]
            except Exception:
                continue
            left, right = sorted((x1, x2))
            top, bottom = sorted((y1, y2))
            if right - left < 8 or bottom - top < 8:
                continue
            clean.append((left, top, right, bottom))
        return clean

    @classmethod
    def _boxes_signature(cls, boxes: list[Any] | tuple[Any, ...]) -> str:
        return json.dumps([list(box) for box in cls._coerce_boxes(list(boxes or []))], separators=(",", ":"))

    @classmethod
    def _source_dependency_signature(cls, source: dict[str, Any]) -> str:
        raw_bbox = source.get("bbox_px") or []
        clean_bbox = cls._coerce_boxes([raw_bbox])
        bbox = list(clean_bbox[0]) if clean_bbox else list(raw_bbox or [])[:4]
        try:
            bbox_key = json.dumps([int(v) for v in bbox[:4]], separators=(",", ":"))
        except Exception:
            bbox_key = json.dumps(bbox, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "|".join(
            [
                str(source.get("book_code") or "").strip(),
                str(source.get("instance_type") or "").strip(),
                str(source.get("pdf_path") or "").strip(),
                str(source.get("page_number") or "").strip(),
                str(source.get("source_record_id") or "").strip(),
                bbox_key,
            ]
        )

    @staticmethod
    def _int_sort_value(value: Any, default: int = 10**9) -> int:
        try:
            number = int(value)
        except Exception:
            return default
        return number if number >= 0 else default

    @classmethod
    def _crop_payload_sort_key(cls, crop_id: str, payload: dict[str, Any]) -> tuple[int, int, int, int, int, str]:
        bbox = payload.get("bbox_px") or []
        y1 = cls._int_sort_value(bbox[1] if isinstance(bbox, (list, tuple)) and len(bbox) > 1 else None)
        x1 = cls._int_sort_value(bbox[0] if isinstance(bbox, (list, tuple)) and len(bbox) > 0 else None)
        return (
            cls._int_sort_value(payload.get("source_page_number")),
            cls._int_sort_value(payload.get("source_order")),
            cls._int_sort_value(payload.get("box_index") or payload.get("page_problem_index") or payload.get("problem_index")),
            y1,
            x1,
            str(crop_id or ""),
        )

    def _invalidate_downstream_for_page_boxes_change(
        self,
        page: ProblemPageRecord,
        *,
        previous_boxes: list[Any] | tuple[Any, ...],
    ) -> list[StagingProblemRecord]:
        changed: list[StagingProblemRecord] = []
        previous_box_signature = self._boxes_signature(list(previous_boxes or []))
        current_box_signature = self._boxes_signature(list(page.boxes or []))
        for record in self.staging.load_records():
            source = dict(record.source or {})
            same_source_record = str(source.get("source_record_id") or "") == str(page.record_id or "")
            same_page = str(source.get("page_number") or "") == str(page.page_number or "")
            if not (same_source_record or same_page):
                continue
            self._invalidate_record_downstream(
                record,
                reason="page_boxes_changed",
                clear_crop=True,
                previous_source=dict(source),
                updated_source={
                    **dict(source),
                    "page_number": page.page_number,
                    "source_record_id": page.record_id,
                    "page_boxes_signature": current_box_signature,
                },
                metadata={
                    "previous_page_boxes_signature": previous_box_signature,
                    "current_page_boxes_signature": current_box_signature,
                    "page_record_id": page.record_id,
                },
            )
            changed.append(record)
        if changed:
            self.staging.upsert_many(changed)
        return changed

    def _invalidate_record_downstream(
        self,
        record: StagingProblemRecord,
        *,
        reason: str,
        clear_crop: bool,
        previous_source: dict[str, Any] | None = None,
        updated_source: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        previous = dict(previous_source or record.source or {})
        if updated_source is not None:
            record.source = dict(updated_source)
        if clear_crop:
            record.crop_path = ""
            record.set_step(PipelineStep.CROPS, StageStatus.PENDING, "crop pendiente de regenerar por cambio de box fuente")
        record.raw_ocr = ""
        record.structured_ocr = {}
        record.figure_segmentation = {}
        record.normalized = {}
        record.review = {}
        record.artifacts = {}
        record.golden_sync = {}
        record.errors = []
        record.set_step(PipelineStep.OCR, StageStatus.PENDING, "OCR invalidado por cambio de box fuente")
        record.set_step(PipelineStep.SEGMENTATION, StageStatus.PENDING, "segmentacion grafica invalidada por cambio de box fuente")
        record.set_step(PipelineStep.NORMALIZATION, StageStatus.PENDING, "normalizacion invalidada por cambio de box fuente")
        record.set_step(PipelineStep.REVIEW, StageStatus.PENDING, "revision invalidada por cambio de box fuente")
        invalidations = list(dict(record.trace or {}).get("downstream_invalidations") or [])
        invalidations.append(
            {
                "updated_at": utc_now_text(),
                "reason": str(reason or "source_changed"),
                "previous_source": previous,
                "updated_source": dict(record.source or {}),
                **dict(metadata or {}),
            }
        )
        record.trace = {**dict(record.trace or {}), "downstream_invalidations": invalidations[-20:]}
        record.audit = {
            **dict(record.audit or {}),
            "downstream_state": {
                "status": "invalidated",
                "reason": str(reason or "source_changed"),
                "updated_at": utc_now_text(),
            },
        }
        record.sync_status_from_steps()

    def _mark_record_downstream_active(self, record: StagingProblemRecord, *, reason: str) -> None:
        downstream = dict(dict(record.audit or {}).get("downstream_state") or {})
        if downstream.get("status") != "invalidated":
            return
        record.audit = {
            **dict(record.audit or {}),
            "downstream_state": {
                "status": "active",
                "reason": str(reason or "source_regenerated"),
                "updated_at": utc_now_text(),
            },
        }

    def build_record_stage_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for record in self.staging.load_records():
            source = dict(record.source or {})
            normalized = dict(record.normalized or {})
            segmentation = dict(record.figure_segmentation or {})
            structured = dict(record.structured_ocr or {})
            records_steps = {
                step: record.step_status(step)
                for step in (
                    PipelineStep.PAGES,
                    PipelineStep.BOXES,
                    PipelineStep.CROPS,
                    PipelineStep.OCR,
                    PipelineStep.SEGMENTATION,
                    PipelineStep.NORMALIZATION,
                    PipelineStep.REVIEW,
                )
            }
            rows.append(
                {
                    "record_id": record.record_id,
                    "status": record.status,
                    "page_number": source.get("page_number") or "",
                    "bbox_px": source.get("bbox_px") or [],
                    "crop_exists": bool(record.crop_path and Path(record.crop_path).exists()),
                    "crop_name": Path(record.crop_path).name,
                    "ocr_items": int(structured.get("items_total") or 0),
                    "segments_total": int(segmentation.get("segments_total") or 0),
                    "normalized_number": normalized.get("numero") or "",
                    "errors_total": len(record.errors),
                    "steps": records_steps,
                }
            )
        return rows

    def detect_pdf_pages(
        self,
        pages: list[int],
        *,
        dpi: int = 300,
        confidence: float = 0.25,
        detector_model: str = "",
    ) -> list[ProblemPageRecord]:
        import fitz

        pdf_path = self.context.resolved_pdf_path()
        if not pdf_path.exists():
            raise FileNotFoundError(f"No se encontro el PDF: {pdf_path}")
        rows = self._dedupe_page_rows(self.load_pages())
        temp = Path(tempfile.mkdtemp(prefix="pdf_factory_pages_"))
        with fitz.open(pdf_path) as document:
            matrix = fitz.Matrix(int(dpi) / 72.0, int(dpi) / 72.0)
            for page_number in pages:
                if page_number < 1 or page_number > document.page_count:
                    raise ValueError(f"Pagina fuera del PDF: {page_number}")
                rendered = temp / f"page_{page_number:04d}.png"
                document[page_number - 1].get_pixmap(matrix=matrix, alpha=False).save(str(rendered))
                row = self.golden.add_rendered_page(
                    self.context.instance_name,
                    pdf_path=pdf_path,
                    page_number=page_number,
                    rendered_path=rendered,
                )
                active_detector = str(detector_model or self.models.pdf_detector or "")
                row.boxes = self.golden.predict_boxes(
                    row.image_path,
                    confidence=confidence,
                    layout_mode=row.layout_mode,
                    model=active_detector,
                )
                row.detector_source = f"pdf_factory:{active_detector or self.models.pdf_detector}"
                row.reviewed = False
                rows = [existing for existing in rows if int(existing.page_number or 0) != int(row.page_number or 0)]
                rows.append(row)
        self.golden.upsert_instance_rows(self.context.instance_name, self._dedupe_page_rows(rows))
        return self.load_pages()

    def materialize_crops_to_staging(self, rows: list[ProblemPageRecord] | None = None) -> list[StagingProblemRecord]:
        page_rows = rows if rows is not None else self.load_pages()
        session_path = self.context.resolved_session_path()
        target, crop_ids = self.golden.materialize_problem_crops_for_downstream(
            self.context.instance_name,
            page_rows,
            return_crop_ids=True,
            session_path=session_path,
            book_code=self.context.book_code,
            instance_type=self.context.instance_type,
            project_name=self.context.project_name,
            pdf_path=self.context.pdf_path,
        )
        crop_payloads: list[tuple[tuple[int, int, int, int, int, str], str, Path, dict[str, Any]]] = []
        for crop_id in crop_ids:
            record_path = Path(target) / "records" / f"{crop_id}.json"
            if not record_path.exists():
                continue
            try:
                crop_payload = json.loads(record_path.read_text(encoding="utf-8"))
            except Exception:
                crop_payload = {}
            crop_payloads.append((self._crop_payload_sort_key(crop_id, crop_payload), crop_id, record_path, crop_payload))

        out: list[StagingProblemRecord] = []
        for _sort_key, crop_id, record_path, crop_payload in sorted(crop_payloads, key=lambda item: item[0]):
            crop_rel = str(crop_payload.get("crop_image_rel") or "").strip()
            crop_path = Path(target) / crop_rel if crop_rel else Path("")
            existing = self.staging.get_record(crop_id)
            record = existing or StagingProblemRecord(record_id=crop_id, crop_id=crop_id, crop_path=str(crop_path))
            previous_source = dict(record.source or {})
            new_source = {
                "book_code": self.context.book_code,
                "instance_type": self.context.instance_type,
                "pdf_path": crop_payload.get("source_pdf_path") or self.context.pdf_path,
                "page_number": crop_payload.get("source_page_number"),
                "page_image": crop_payload.get("source_page_image"),
                "source_order": crop_payload.get("source_order"),
                "box_index": crop_payload.get("box_index"),
                "page_problem_index": crop_payload.get("page_problem_index"),
                "problem_index": crop_payload.get("problem_index"),
                "bbox_px": crop_payload.get("bbox_px") or [],
                "crop_id": crop_id,
                "crop_path": str(crop_path),
                "crop_image_rel": crop_rel,
                "source_record_id": crop_payload.get("source_record_id") or "",
                "source_instance": crop_payload.get("source_instance_full") or crop_payload.get("source_instance") or "",
                "layout_mode": crop_payload.get("layout_mode") or "",
                "session_json": crop_payload.get("session_json") or "",
                "problem_crops_live_record": str(record_path),
            }
            if existing and self._source_dependency_signature(previous_source) != self._source_dependency_signature(new_source):
                self._invalidate_record_downstream(
                    record,
                    reason="crop_source_changed",
                    clear_crop=False,
                    previous_source=previous_source,
                    updated_source=new_source,
                    metadata={
                        "crop_id": crop_id,
                        "previous_bbox_px": previous_source.get("bbox_px") or [],
                        "current_bbox_px": new_source.get("bbox_px") or [],
                    },
                )
            record.crop_path = str(crop_path)
            record.status = StageStatus.normalize(record.status)
            if record.status == StageStatus.ERROR and record.step_status(PipelineStep.CROPS) == StageStatus.ERROR:
                record.status = StageStatus.PENDING
            record.source = new_source
            record.set_step(
                PipelineStep.PAGES,
                StageStatus.READY,
                "pagina fuente vinculada a instancia",
                page_number=crop_payload.get("source_page_number"),
                page_image=crop_payload.get("source_page_image"),
            )
            record.set_step(
                PipelineStep.BOXES,
                StageStatus.READY,
                "box de problema materializado desde Modulo 13",
                bbox_px=crop_payload.get("bbox_px") or [],
                source_record_id=crop_payload.get("source_record_id") or "",
            )
            record.set_step(
                PipelineStep.CROPS,
                StageStatus.READY if crop_path.exists() else StageStatus.ERROR,
                "crop disponible en staging/live" if crop_path.exists() else "crop no encontrado",
                crop_path=str(crop_path),
            )
            if not record.raw_ocr and not record.structured_ocr:
                record.set_step(PipelineStep.OCR, StageStatus.PENDING, "pendiente de OCR")
            if not record.figure_segmentation:
                record.set_step(PipelineStep.SEGMENTATION, StageStatus.PENDING, "pendiente de segmentacion")
            if not record.normalized:
                record.set_step(PipelineStep.NORMALIZATION, StageStatus.PENDING, "pendiente de normalizacion")
            if not record.review:
                record.set_step(PipelineStep.REVIEW, StageStatus.PENDING, "pendiente de revision humana")
            record.models = {**record.models, **self._model_snapshot()}
            record.confidence.setdefault(
                "pdf_box",
                float(record.models.get("stages", {}).get("pdf_detector", {}).get("confidence", 0.0) or 0.0),
            )
            record.trace = {
                **dict(record.trace or {}),
                "materialized_at": utc_now_text(),
                "raw_sources": {
                    "problem_crops_live_record": str(record_path),
                    "crop_payload_schema": str(crop_payload.get("schema_version") or ""),
                },
            }
            record.sync_status_from_steps()
            record.touch()
            out.append(record)
        self.staging.upsert_many(out)
        return out

    def run_ocr_and_segmentation(
        self,
        *,
        provider: str = "hf",
        curso: str = "SIN_CURSO",
        tema: str = "SIN_TEMA",
        start_n: int = 1,
        limit: int | None = None,
        ocr_model: str = "",
        figure_model: str = "",
        force_figure_model: bool = True,
        record_id: str = "",
        record_ids: list[str] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        run_segmentation: bool = True,
        run_ocr: bool = True,
    ) -> list[StagingProblemRecord]:
        records = self.staging.load_records()
        selected_record_ids = [str(item or "").strip() for item in list(record_ids or []) if str(item or "").strip()]
        if selected_record_ids:
            by_id = {str(record.record_id or ""): record for record in records}
            missing = [item for item in selected_record_ids if item not in by_id]
            if missing:
                raise KeyError(missing[0])
            records = [by_id[item] for item in selected_record_ids]
        selected_record_id = str(record_id or "").strip()
        if selected_record_id and not selected_record_ids:
            records = [record for record in records if str(record.record_id or "") == selected_record_id]
            if not records:
                raise KeyError(selected_record_id)
        if limit is not None:
            records = records[: max(0, int(limit))]
        if not records:
            return []
        if not run_segmentation and not run_ocr:
            return records
        if run_segmentation and run_ocr:
            selected_ids = [str(record.record_id or "") for record in records if str(record.record_id or "")]
            if progress_callback is not None:
                try:
                    progress_callback(
                        {
                            "event": "phase_start",
                            "phase": "segmentation",
                            "message": f"Segmentando graficos localmente 0 de {len(selected_ids)}",
                            "total": len(selected_ids),
                        }
                    )
                except Exception:
                    pass
            self.run_ocr_and_segmentation(
                provider=provider,
                curso=curso,
                tema=tema,
                start_n=start_n,
                limit=None,
                ocr_model=ocr_model,
                figure_model=figure_model,
                force_figure_model=force_figure_model,
                record_ids=selected_ids,
                progress_callback=progress_callback,
                run_segmentation=True,
                run_ocr=False,
            )
            if progress_callback is not None:
                try:
                    progress_callback(
                        {
                            "event": "phase_start",
                            "phase": "ocr",
                            "message": f"Ejecutando OCR remoto 0 de {len(selected_ids)}",
                            "total": len(selected_ids),
                        }
                    )
                except Exception:
                    pass
            return self.run_ocr_and_segmentation(
                provider=provider,
                curso=curso,
                tema=tema,
                start_n=start_n,
                limit=None,
                ocr_model=ocr_model,
                figure_model=figure_model,
                force_figure_model=force_figure_model,
                record_ids=selected_ids,
                progress_callback=progress_callback,
                run_segmentation=False,
                run_ocr=True,
            )

        if run_ocr:
            from modulos.modulo0_transcriptor.scan_pipeline.pipeline import ScanPipeline
            from modulos.modulo0_transcriptor.scan_pipeline.extractor import TRAINED_OCR_VISION_MODEL
        else:
            ScanPipeline = None  # type: ignore[assignment]
            TRAINED_OCR_VISION_MODEL = ""
        if run_segmentation:
            from modulos.modulo0_transcriptor.segmentador_v2 import SegmentadorProblemasV2
        else:
            SegmentadorProblemasV2 = None  # type: ignore[assignment]

        active_ocr_model = str(ocr_model or self.models.ocr or "").strip()
        active_figure_model = str(figure_model or self.models.figure_segmenter or "").strip()
        endpoint_state: dict[str, Any] = {}
        pipeline = None
        if run_ocr:
            resolved_ocr_model = active_ocr_model or str(os.getenv("HF_MODEL", TRAINED_OCR_VISION_MODEL) or TRAINED_OCR_VISION_MODEL).strip()
            self._validate_ocr_runtime(provider=provider, model=resolved_ocr_model, trained_model=TRAINED_OCR_VISION_MODEL)
            endpoint_state = self._prepare_trained_ocr_endpoint(
                provider=provider,
                model=resolved_ocr_model,
                trained_model=TRAINED_OCR_VISION_MODEL,
            )
            pipeline = ScanPipeline(
                provider=provider,
                model=active_ocr_model,
                debug_dir=str(self.staging.root / "ocr_debug"),
                strict_json=True,
            )
        segmenter = None
        if run_segmentation:
            segmenter = SegmentadorProblemasV2(
                self.staging.root / "segments",
                model_path=active_figure_model,
                force_model_default=bool(force_figure_model),
            )
        processed: list[StagingProblemRecord] = []
        next_n = max(1, int(start_n))
        phase_error_prefixes: list[str] = []
        if run_segmentation:
            phase_error_prefixes.append("segmentacion_grafica:")
        if run_ocr:
            phase_error_prefixes.extend(["ocr_crudo:", "ocr_estructura:"])
        phase_name = "OCR" if run_ocr else "segmentacion"
        for index, record in enumerate(records):
            crop_path = Path(record.crop_path)
            if phase_error_prefixes:
                record.errors = [
                    str(item)
                    for item in list(record.errors or [])
                    if not any(str(item).startswith(prefix) for prefix in phase_error_prefixes)
                ]
            if progress_callback is not None:
                try:
                    progress_callback(
                        {
                            "event": "record_start",
                            "phase": "ocr" if run_ocr else "segmentation",
                            "record_id": record.record_id,
                            "index": index + 1,
                            "total": len(records),
                            "message": f"{phase_name} {index + 1} de {len(records)}",
                        }
                    )
                except Exception:
                    pass
            if not crop_path.exists():
                record.status = StageStatus.ERROR
                record.errors.append(f"crop_missing:{crop_path}")
                record.set_step(PipelineStep.CROPS, StageStatus.ERROR, "crop no encontrado", crop_path=str(crop_path))
                if run_ocr:
                    record.set_step(PipelineStep.OCR, StageStatus.PENDING, "pendiente hasta recuperar crop")
                if run_segmentation:
                    record.set_step(PipelineStep.SEGMENTATION, StageStatus.PENDING, "pendiente hasta recuperar crop")
                record.set_step(PipelineStep.NORMALIZATION, StageStatus.PENDING, "pendiente hasta recuperar crop")
                record.sync_status_from_steps()
                self.staging.upsert_record(record)
                processed.append(record)
                continue
            record.status = StageStatus.PROCESSING
            record.set_step(PipelineStep.CROPS, StageStatus.READY, "crop disponible", crop_path=str(crop_path))
            if run_segmentation:
                record.set_step(PipelineStep.SEGMENTATION, StageStatus.PROCESSING, "segmentando graficos internos")
                try:
                    if segmenter is None:
                        raise RuntimeError("Segmentador no inicializado.")
                    segments = segmenter.segmentar(crop_path, force_model=bool(force_figure_model))
                    detector_payload = dict(segmenter.last_detector_payload or {})
                    try:
                        figure_max_conf = float(detector_payload.get("max_conf", 0.0) or 0.0)
                    except Exception:
                        figure_max_conf = 0.0
                    try:
                        figure_avg_conf = float(detector_payload.get("avg_conf", 0.0) or 0.0)
                    except Exception:
                        figure_avg_conf = 0.0
                    record.confidence["figure_segmenter_max"] = max(0.0, min(1.0, figure_max_conf))
                    record.confidence["figure_segmenter_avg"] = max(0.0, min(1.0, figure_avg_conf))
                    record.models = {
                        **record.models,
                        **self._model_snapshot(
                            provider=provider,
                            confidence_overrides={"figure_segmenter": record.confidence["figure_segmenter_max"]},
                        ),
                    }
                    record.figure_segmentation = {
                        "status": StageStatus.NEEDS_REVIEW if segments else StageStatus.READY,
                        "segments_total": len(segments),
                        "segments": [
                            {
                                "idx": int(seg.idx),
                                "bbox_px": [int(v) for v in seg.bbox],
                                "image_path": str(seg.image_path),
                            }
                            for seg in segments
                        ],
                        "detector": detector_payload,
                    }
                    record.set_step(
                        PipelineStep.SEGMENTATION,
                        StageStatus.NEEDS_REVIEW if segments else StageStatus.READY,
                        "segmentos detectados para revisar" if segments else "sin graficos internos detectados",
                        segments_total=len(segments),
                    )
                except Exception as exc:
                    message = str(exc or "")
                    record.errors.append(f"segmentacion_grafica:{message}")
                    record.set_step(PipelineStep.SEGMENTATION, StageStatus.ERROR, f"segmentacion grafica: {message}")
            if run_ocr:
                record.set_step(PipelineStep.OCR, StageStatus.PROCESSING, "ejecutando OCR crudo remoto")
                try:
                    if endpoint_state:
                        record.trace = {
                            **dict(record.trace or {}),
                            "hf_ocr_endpoint_before_run": dict(endpoint_state),
                        }
                    if pipeline is None:
                        raise RuntimeError("Pipeline OCR no inicializado.")
                    _initial_items, raw_output = self._extract_with_cold_start_retry(
                        pipeline,
                        image_path=crop_path,
                        curso=curso,
                        tema=tema,
                        start_n=next_n,
                        progress_callback=progress_callback,
                    )
                    record.raw_ocr = raw_output
                    record.structured_ocr = {}
                    raw_has_text = bool(str(raw_output or "").strip())
                    record.set_step(
                        PipelineStep.OCR,
                        StageStatus.READY if raw_has_text else StageStatus.NEEDS_REVIEW,
                        "OCR crudo guardado" if raw_has_text else "OCR crudo vacio; requiere revision",
                        characters=len(str(raw_output or "")),
                    )
                    if raw_has_text:
                        record.confidence["ocr_raw_available"] = 1.0
                    record.models = {
                        **record.models,
                        **self._model_snapshot(provider=provider, confidence_overrides={"ocr": float(record.confidence.get("ocr_raw_available") or 0.0)} if raw_has_text else None),
                    }
                    record.normalized = {}
                    record.set_step(
                        PipelineStep.NORMALIZATION,
                        StageStatus.PENDING,
                        "pendiente de normalizacion desde OCR crudo revisado",
                        source="normalizer_pending_training",
                    )
                    record.set_step(
                        PipelineStep.REVIEW,
                        StageStatus.NEEDS_REVIEW,
                        "OCR crudo listo para revision humana; normalizacion IA pendiente",
                    )
                    self._mark_record_downstream_active(record, reason="ocr_segmentation_reran_after_source_change")
                    self._write_raw_artifacts(record)
                    next_n += 1
                except Exception as exc:
                    message = str(exc or "")
                    if str(record.raw_ocr or "").strip():
                        record.set_step(
                            PipelineStep.OCR,
                            StageStatus.READY,
                            "OCR crudo guardado; fallo posterior no bloquea revision",
                            characters=len(str(record.raw_ocr or "")),
                        )
                        record.set_step(
                            PipelineStep.NORMALIZATION,
                            StageStatus.PENDING,
                            "pendiente de normalizacion desde OCR crudo revisado",
                        )
                        try:
                            self._write_raw_artifacts(record)
                        except Exception:
                            pass
                    else:
                        record.errors.append(f"ocr_crudo:{message}")
                        record.set_step(PipelineStep.OCR, StageStatus.ERROR, f"OCR crudo remoto: {message}")
                        record.set_step(PipelineStep.NORMALIZATION, StageStatus.ERROR, "normalizacion no ejecutada por error de OCR crudo")
            confidence_overrides = {}
            if "figure_segmenter_max" in record.confidence:
                confidence_overrides["figure_segmenter"] = float(record.confidence.get("figure_segmenter_max") or 0.0)
            if "ocr_raw_available" in record.confidence:
                confidence_overrides["ocr"] = float(record.confidence.get("ocr_raw_available") or 0.0)
            record.models = {
                **record.models,
                **self._model_snapshot(provider=provider, confidence_overrides=confidence_overrides or None),
            }
            record.sync_status_from_steps()
            record.touch()
            self.staging.upsert_record(record)
            processed.append(record)
        self.staging.upsert_many(processed)
        return processed

    def _prepare_trained_ocr_endpoint(self, *, provider: str, model: str, trained_model: str) -> dict[str, Any]:
        if str(provider or "").strip().lower() != "hf":
            return {}
        if str(model or "").strip() != str(trained_model or "").strip():
            return {}
        try:
            from .hf_endpoint_manager import HfEndpointManager
        except Exception:
            return {}
        try:
            timeout_s = int(str(os.getenv("HF_ENDPOINT_START_TIMEOUT", "420") or "420").strip())
        except Exception:
            timeout_s = 420
        try:
            poll_s = int(str(os.getenv("HF_ENDPOINT_POLL_SECONDS", "8") or "8").strip())
        except Exception:
            poll_s = 8
        manager = HfEndpointManager()
        return manager.ensure_ready(timeout_s=max(1, min(1800, timeout_s)), poll_s=max(1, min(120, poll_s)))

    def _extract_with_cold_start_retry(
        self,
        pipeline: Any,
        *,
        image_path: Path,
        curso: str,
        tema: str,
        start_n: int,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> Any:
        from .hf_endpoint_manager import cold_start_sleep_seconds, is_cold_start_runtime_error

        try:
            retries = int(str(os.getenv("HF_ENDPOINT_COLD_START_RETRIES", "8") or "8").strip())
        except Exception:
            retries = 8
        retries = max(0, min(12, retries))
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return pipeline.extractor.extract_from_image(
                    image_path=image_path,
                    curso=curso,
                    tema=tema,
                    start_n=start_n,
                )
            except Exception as exc:
                last_exc = exc
                if attempt >= retries or not is_cold_start_runtime_error(exc):
                    raise
                delay_s = cold_start_sleep_seconds(attempt)
                if progress_callback is not None:
                    try:
                        progress_callback(
                            {
                                "event": "ocr_cold_start_retry",
                                "attempt": attempt + 1,
                                "retries": retries,
                                "delay_s": delay_s,
                                "message": (
                                    "Endpoint OCR despertando o temporalmente no disponible "
                                    f"(reintento {attempt + 1}/{retries}, espera {int(delay_s)}s)."
                                ),
                                "error": str(exc or ""),
                            }
                        )
                    except Exception:
                        pass
                time.sleep(delay_s)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("No se pudo ejecutar OCR.")

    def _validate_ocr_runtime(self, *, provider: str, model: str, trained_model: str) -> None:
        runtime = str(provider or "hf").strip().lower()
        if runtime == "ocr":
            return
        try:
            from importlib.util import find_spec

            has_openai = find_spec("openai") is not None
        except Exception:
            has_openai = False
        if not has_openai:
            raise RuntimeError("Falta instalar la libreria openai para ejecutar OCR remoto compatible.")
        if runtime == "openai":
            if not str(os.getenv("OPENAI_API_KEY", "") or "").strip():
                raise RuntimeError("Falta OPENAI_API_KEY para ejecutar OCR con OpenAI.")
            return
        token = str(os.getenv("HF_TOKEN", "") or os.getenv("HUGGINGFACEHUB_API_TOKEN", "") or "").strip()
        if not token:
            raise RuntimeError("Falta HF_TOKEN para ejecutar el OCR entrenado.")
        if str(model or "").strip() == str(trained_model or "").strip():
            endpoint = str(os.getenv("HF_TRAINED_OCR_BASE_URL", "") or "").strip()
            if not endpoint:
                raise RuntimeError(
                    "Falta HF_TRAINED_OCR_BASE_URL. Configura la URL /v1 del endpoint dedicado "
                    "del modelo OCR entrenado o usa temporalmente HF_BASE_URL con esa misma URL."
                )
            if "router.huggingface.co" in endpoint.lower():
                raise RuntimeError(
                    "HF_TRAINED_OCR_BASE_URL esta apuntando al router de Hugging Face Inference Providers. "
                    "Para el modelo OCR entrenado usa la URL /v1 del endpoint dedicado "
                    "(por ejemplo https://...endpoints.huggingface.cloud/v1) o genera un HF_TOKEN "
                    "fine-grained con permiso 'Make calls to Inference Providers' si decides usar el router."
                )

    def normalize_existing_ocr(
        self,
        *,
        record_id: str = "",
        record_ids: list[str] | None = None,
    ) -> list[StagingProblemRecord]:
        records = self.staging.load_records()
        selected_record_ids = [str(item or "").strip() for item in list(record_ids or []) if str(item or "").strip()]
        if selected_record_ids:
            by_id = {str(record.record_id or ""): record for record in records}
            missing = [item for item in selected_record_ids if item not in by_id]
            if missing:
                raise KeyError(missing[0])
            records = [by_id[item] for item in selected_record_ids]
        selected_record_id = str(record_id or "").strip()
        if selected_record_id and not selected_record_ids:
            records = [record for record in records if str(record.record_id or "") == selected_record_id]
            if not records:
                raise KeyError(selected_record_id)
        out: list[StagingProblemRecord] = []
        for record in records:
            record.errors = []
            try:
                record.models = {**record.models, **self._model_snapshot()}
                if str(record.raw_ocr or "").strip():
                    record.set_step(PipelineStep.OCR, StageStatus.READY, "OCR crudo disponible para preparar revision")
                    record.normalized = self._draft_normalized_from_raw_ocr(record)
                elif record.structured_ocr:
                    record.set_step(PipelineStep.OCR, StageStatus.READY, "OCR historico disponible para preparar revision")
                    record.normalized = self._normalize_from_pipeline_record(record, record.structured_ocr)
                else:
                    record.normalized = {}
                if record.normalized:
                    record.set_step(PipelineStep.NORMALIZATION, StageStatus.NEEDS_REVIEW, "borrador desde OCR crudo pendiente de formato final")
                    record.set_step(PipelineStep.REVIEW, StageStatus.NEEDS_REVIEW, "pendiente de revision humana")
                else:
                    record.set_step(PipelineStep.NORMALIZATION, StageStatus.PENDING, "sin OCR crudo para preparar revision")
                self._write_raw_artifacts(record)
                record.sync_status_from_steps()
            except Exception as exc:
                message = str(exc or "")
                record.errors.append(f"normalizacion:{message}")
                record.set_step(PipelineStep.NORMALIZATION, StageStatus.ERROR, f"normalizacion: {message}")
                record.sync_status_from_steps()
            record.touch()
            self.staging.upsert_record(record)
            out.append(record)
        self.staging.upsert_many(out)
        return out

    def update_raw_ocr(self, record_id: str, raw_ocr: str) -> StagingProblemRecord:
        record = self.staging.get_record(record_id)
        if record is None:
            raise KeyError(record_id)
        record.raw_ocr = str(raw_ocr or "")
        record.errors = []
        if not record.raw_ocr.strip():
            record.structured_ocr = {}
        record.normalized = {}
        if record.raw_ocr.strip():
            ocr_status = StageStatus.READY
            ocr_detail = "OCR crudo revisado"
        else:
            ocr_status = StageStatus.PENDING
            ocr_detail = "OCR crudo vacio; pendiente"
        record.set_step(
            PipelineStep.OCR,
            ocr_status,
            ocr_detail,
            source="human_raw_ocr_editor",
            characters=len(record.raw_ocr),
        )
        record.set_step(PipelineStep.NORMALIZATION, StageStatus.PENDING, "pendiente de preparar revision desde OCR crudo revisado")
        record.set_step(PipelineStep.REVIEW, StageStatus.PENDING, "pendiente de revision final")
        record.trace = {
            **dict(record.trace or {}),
            "last_raw_ocr_review": {
                "updated_at": utc_now_text(),
                "source": "human_raw_ocr_editor",
                "characters": len(record.raw_ocr),
                "structured_items_total": int((record.structured_ocr or {}).get("items_total") or 0),
            },
        }
        self._mark_record_downstream_active(record, reason="raw_ocr_reviewed_after_source_change")
        self._write_raw_artifacts(record)
        record.sync_status_from_steps()
        record.touch()
        self.staging.upsert_record(record)
        return record

    def update_figure_segments(self, record_id: str, boxes: list[Any]) -> StagingProblemRecord:
        from modulos.modulo0_transcriptor.segmentador_v2 import SegmentadorProblemasV2

        record = self.staging.get_record(record_id)
        if record is None:
            raise KeyError(record_id)
        crop_path = Path(record.crop_path)
        if not crop_path.exists():
            raise FileNotFoundError(f"No se encontro el crop: {crop_path}")
        clean_boxes = self._coerce_boxes(boxes)
        detector_payload = dict((record.figure_segmentation or {}).get("detector") or {})
        detector_payload.setdefault("predicted_boxes", detector_payload.get("predicted_boxes") or [])
        segmenter = SegmentadorProblemasV2(
            self.staging.root / "segments",
            model_path=str(self.models.figure_segmenter or ""),
            force_model_default=False,
        )
        segments = segmenter.save_reviewed_segments(crop_path, clean_boxes, detector_payload=detector_payload)
        detector_payload = dict(segmenter.last_detector_payload or detector_payload or {})
        if not detector_payload:
            detector_payload = {
                "detector_source": "human_reviewed_segments",
                "review_status": "reviewed",
                "final_boxes": [{"bbox_px": [int(v) for v in box[:4]], "conf": 1.0} for box in clean_boxes],
            }
        record.figure_segmentation = {
            "status": StageStatus.READY,
            "segments_total": len(segments),
            "segments": [
                {
                    "idx": int(seg.idx),
                    "bbox_px": [int(v) for v in seg.bbox],
                    "image_path": str(seg.image_path),
                    "reviewed": True,
                }
                for seg in segments
            ],
            "detector": detector_payload,
            "review": {
                "review_status": "reviewed",
                "updated_at": utc_now_text(),
                "source": "human_canvas_editor",
                "boxes_total": len(segments),
            },
        }
        record.models = {
            **record.models,
            **self._model_snapshot(confidence_overrides={"figure_segmenter": 1.0}),
        }
        record.confidence["figure_segmenter_reviewed"] = 1.0
        record.set_step(
            PipelineStep.SEGMENTATION,
            StageStatus.READY,
            "segmentos graficos revisados por humano",
            segments_total=len(segments),
        )
        record.trace = {
            **dict(record.trace or {}),
            "last_figure_segment_review": {
                "updated_at": utc_now_text(),
                "source": "human_canvas_editor",
                "boxes": [[int(v) for v in box[:4]] for box in clean_boxes],
            },
        }
        self._write_raw_artifacts(record)
        record.sync_status_from_steps()
        record.touch()
        self.staging.upsert_record(record)
        return record

    def _structure_raw_ocr_for_normalization(self, record: StagingProblemRecord):
        from modulos.modulo0_transcriptor.scan_pipeline.pipeline import ScanPipeline

        crop_path = Path(record.crop_path)
        source = dict(record.source or {})
        try:
            start_n = max(1, int(record.normalized.get("numero") or source.get("problem_number") or source.get("n") or 1))
        except Exception:
            start_n = 1
        pipeline = ScanPipeline(
            provider="ocr",
            debug_dir=str(self.staging.root / "ocr_debug"),
            strict_json=False,
            max_retries=0,
            parse_max_retries=0,
        )
        return pipeline.process_raw_output(
            raw_output=str(record.raw_ocr or ""),
            image_path=crop_path,
            start_n=start_n,
            curso=str(record.normalized.get("curso") or "SIN_CURSO"),
            tema=str(record.normalized.get("tema") or "SIN_TEMA"),
            has_figure_hint=bool(record.figure_segmentation.get("segments_total")),
            initial_items=None,
        )

    def _draft_normalized_from_raw_ocr(self, record: StagingProblemRecord) -> dict[str, Any]:
        raw = str(record.raw_ocr or "").strip()
        if not raw:
            return {}
        source = dict(record.source or {})
        try:
            number = str(int(record.normalized.get("numero") or source.get("problem_number") or source.get("n") or "")).strip()
        except Exception:
            number = str(record.normalized.get("numero") or source.get("problem_number") or source.get("n") or "").strip()
        has_figure = bool((record.figure_segmentation or {}).get("segments_total"))
        return {
            "schema_version": "normalized_problem_staging_v1",
            "normalizer": "manual_raw_ocr_review",
            "status": StageStatus.NEEDS_REVIEW,
            "updated_at": utc_now_text(),
            "source_record_id": record.record_id,
            "numero": number,
            "curso": str((record.normalized or {}).get("curso") or "SIN_CURSO"),
            "tema": str((record.normalized or {}).get("tema") or "SIN_TEMA"),
            "enunciado_latex": raw,
            "alternativas": {"A": "", "B": "", "C": "", "D": "", "E": ""},
            "respuesta_correcta": "",
            "respuesta_final": "",
            "tiene_grafico": has_figure,
            "figure_tag": f"img-{number or record.record_id}" if has_figure else "",
            "latex_rendered_item": "",
            "metadata_tecnica": {
                "crop_path": record.crop_path,
                "source": source,
                "models": dict(record.models),
                "confidence": dict(record.confidence),
                "raw_ocr_source": "raw_ocr_only",
            },
        }

    def _normalize_from_pipeline_record(self, record: StagingProblemRecord, report: dict[str, Any]) -> dict[str, Any]:
        items = list(report.get("items") or []) if isinstance(report, dict) else []
        item_payload: dict[str, Any] = {}
        rendered = ""
        if items and isinstance(items[0], dict):
            item_payload = dict(items[0].get("item") or {})
            rendered = str(items[0].get("rendered") or "")
        if not item_payload:
            return {}
        options = dict(item_payload.get("options") or {})
        return {
            "schema_version": "normalized_problem_staging_v1",
            "normalizer": self.models.normalizer,
            "status": StageStatus.NEEDS_REVIEW,
            "updated_at": utc_now_text(),
            "source_record_id": record.record_id,
            "numero": item_payload.get("n") or "",
            "curso": item_payload.get("curso") or "",
            "tema": item_payload.get("tema") or "",
            "enunciado_latex": item_payload.get("statement") or "",
            "alternativas": {label: str(options.get(label, "") or "") for label in ("A", "B", "C", "D", "E")},
            "respuesta_correcta": item_payload.get("answer_key") or "",
            "tiene_grafico": bool(item_payload.get("has_figure")) or bool(record.figure_segmentation.get("segments_total")),
            "figure_tag": item_payload.get("figure_tag") or "",
            "latex_rendered_item": rendered,
            "metadata_tecnica": {
                "crop_path": record.crop_path,
                "source": dict(record.source),
                "models": dict(record.models),
                "confidence": dict(record.confidence),
            },
        }

    def _write_raw_artifacts(self, record: StagingProblemRecord) -> None:
        artifacts_dir = self.staging.artifact_dir("raw_outputs", record.record_id, probe_file="figure_segmentation.json")
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        raw_path = artifacts_dir / "raw_ocr.txt"
        structured_path = artifacts_dir / "structured_ocr.json"
        normalized_path = artifacts_dir / "normalized.json"
        figure_path = artifacts_dir / "figure_segmentation.json"
        trace_path = artifacts_dir / "traceability.json"
        raw_path.write_text(str(record.raw_ocr or ""), encoding="utf-8")
        structured_path.write_text(json.dumps(record.structured_ocr, ensure_ascii=False, indent=2), encoding="utf-8")
        normalized_path.write_text(json.dumps(record.normalized, ensure_ascii=False, indent=2), encoding="utf-8")
        figure_path.write_text(json.dumps(record.figure_segmentation, ensure_ascii=False, indent=2), encoding="utf-8")
        trace_path.write_text(
            json.dumps(
                {
                    "schema_version": "pdf_factory_raw_traceability_v1",
                    "updated_at": utc_now_text(),
                    "record_id": record.record_id,
                    "crop_id": record.crop_id,
                    "source": dict(record.source or {}),
                    "models": dict(record.models or {}),
                    "confidence": dict(record.confidence or {}),
                    "trace": dict(record.trace or {}),
                    "steps": dict(record.steps or {}),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        record.artifacts = {
            **dict(record.artifacts or {}),
            "raw_outputs_schema": "pdf_factory_raw_outputs_v1",
            "updated_at": utc_now_text(),
            "raw_ocr": str(raw_path),
            "structured_ocr": str(structured_path),
            "normalized": str(normalized_path),
            "figure_segmentation": str(figure_path),
            "traceability": str(trace_path),
        }

    @staticmethod
    def _status_from_counts(*, total: int, ready: int = 0, needs_review: int = 0, errors: int = 0, processing: int = 0) -> str:
        if total <= 0:
            return StageStatus.PENDING
        if errors:
            return StageStatus.ERROR
        if processing:
            return StageStatus.PROCESSING
        if needs_review:
            return StageStatus.NEEDS_REVIEW
        if ready >= total:
            return StageStatus.READY
        return StageStatus.PENDING

    @classmethod
    def _aggregate_record_status(cls, records: list[StagingProblemRecord]) -> str:
        if not records:
            return StageStatus.PENDING
        statuses = [StageStatus.normalize(record.status) for record in records]
        return cls._status_from_counts(
            total=len(statuses),
            ready=sum(1 for status in statuses if status == StageStatus.READY),
            needs_review=sum(1 for status in statuses if status == StageStatus.NEEDS_REVIEW),
            errors=sum(1 for status in statuses if status == StageStatus.ERROR),
            processing=sum(1 for status in statuses if status == StageStatus.PROCESSING),
        )

    @classmethod
    def _aggregate_step_status(cls, records: list[StagingProblemRecord], step: str) -> str:
        if not records:
            return StageStatus.PENDING
        statuses = [record.step_status(step) for record in records]
        return cls._status_from_counts(
            total=len(statuses),
            ready=sum(1 for status in statuses if status == StageStatus.READY),
            needs_review=sum(1 for status in statuses if status == StageStatus.NEEDS_REVIEW),
            errors=sum(1 for status in statuses if status == StageStatus.ERROR),
            processing=sum(1 for status in statuses if status == StageStatus.PROCESSING),
        )

    @classmethod
    def _aggregate_group_status(cls, records: list[StagingProblemRecord], steps: list[str]) -> str:
        if not records:
            return StageStatus.PENDING
        statuses = [record.step_status(step) for record in records for step in steps]
        return cls._status_from_counts(
            total=len(statuses),
            ready=sum(1 for status in statuses if status == StageStatus.READY),
            needs_review=sum(1 for status in statuses if status == StageStatus.NEEDS_REVIEW),
            errors=sum(1 for status in statuses if status == StageStatus.ERROR),
            processing=sum(1 for status in statuses if status == StageStatus.PROCESSING),
        )
