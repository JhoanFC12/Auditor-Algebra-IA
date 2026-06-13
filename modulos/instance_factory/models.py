from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.project_layout import normalize_instance_name, project_dirs, remap_legacy_drive_path


PIPELINE_CONTRACT_VERSION = "pdf_factory_instance_pipeline_v2"


class StageStatus:
    PENDING = "pendiente"
    PROCESSING = "procesando"
    READY = "listo"
    NEEDS_REVIEW = "requiere_revision"
    HUMAN_REVIEWED = READY
    ERROR = "error"

    @classmethod
    def values(cls) -> set[str]:
        return {cls.PENDING, cls.PROCESSING, cls.READY, cls.NEEDS_REVIEW, cls.ERROR}

    @classmethod
    def normalize(cls, value: str, default: str = PENDING) -> str:
        text = str(value or "").strip().lower().replace(" ", "_")
        aliases = {
            "pendiente": cls.PENDING,
            "pending": cls.PENDING,
            "procesando": cls.PROCESSING,
            "processing": cls.PROCESSING,
            "listo": cls.READY,
            "ready": cls.READY,
            "done": cls.READY,
            "complete": cls.READY,
            "revision_humana": cls.READY,
            "revisado": cls.READY,
            "requiere_revision": cls.NEEDS_REVIEW,
            "needs_review": cls.NEEDS_REVIEW,
            "review": cls.NEEDS_REVIEW,
            "pending_review": cls.NEEDS_REVIEW,
            "error": cls.ERROR,
            "failed": cls.ERROR,
        }
        text = aliases.get(text, text)
        return text if text in cls.values() else default


class PipelineStep:
    PAGES = "paginas"
    BOXES = "boxes"
    CROPS = "crops"
    SEGMENTATION = "segmentacion"
    OCR = "ocr"
    NORMALIZATION = "normalizacion"
    REVIEW = "revision"

    ORDER = [PAGES, BOXES, CROPS, SEGMENTATION, OCR, NORMALIZATION, REVIEW]

    @classmethod
    def normalize(cls, value: str) -> str:
        text = str(value or "").strip().lower()
        aliases = {
            "pages": cls.PAGES,
            "page_selection": cls.PAGES,
            "pdf_pages": cls.PAGES,
            "pdf_box_detection": cls.BOXES,
            "box_detection": cls.BOXES,
            "crop": cls.CROPS,
            "crops_staging": cls.CROPS,
            "figure_segmentation": cls.SEGMENTATION,
            "segmentation": cls.SEGMENTATION,
            "normalization": cls.NORMALIZATION,
            "review": cls.REVIEW,
        }
        return aliases.get(text, text)


def build_pipeline_contract() -> dict[str, Any]:
    return {
        "schema_version": "pdf_factory_pipeline_contract_v1",
        "contract_version": PIPELINE_CONTRACT_VERSION,
        "ordered_steps": list(PipelineStep.ORDER),
        "allowed_statuses": sorted(StageStatus.values()),
        "storage_policy": {
            "automatic_results_target": "staging",
            "forbidden_automatic_targets": ["problemas"],
            "promotion_enabled": False,
            "explicit_manual_upload_enabled": True,
        },
        "human_review_policy": {
            "corrections_are_training_data": True,
            "review_step": PipelineStep.REVIEW,
            "ready_status": StageStatus.READY,
        },
    }


def utc_now_text() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


@dataclass
class InstancePipelineContext:
    book_code: str
    instance_type: str
    project_name: str = ""
    pdf_path: str = ""
    workspace_dir: str = ""
    session_path: str = ""
    db_name: str = ""
    book_id: int | None = None

    @classmethod
    def from_library_instance(
        cls,
        book: dict[str, Any],
        instance: dict[str, Any],
        *,
        db_name: str = "",
        session_path: str | Path | None = None,
    ) -> "InstancePipelineContext":
        """Build the factory context from Modulo 10 book/instance rows."""
        book_code = str(book.get("codigo") or book.get("book_code") or "").strip()
        instance_type = str(
            instance.get("tipo")
            or instance.get("instance_type")
            or instance.get("codigo_instancia")
            or instance.get("instancia_tipo")
            or ""
        ).strip()
        pdf_path = str(book.get("pdf_path") or book.get("pdf") or "").strip()
        resolved_pdf = remap_legacy_drive_path(Path(pdf_path).expanduser(), prefer_existing=True) if pdf_path else Path("")
        raw_session = session_path if session_path is not None else instance.get("session_path")
        return cls(
            book_code=book_code,
            instance_type=instance_type,
            project_name=str(book.get("titulo") or book.get("project_name") or "").strip(),
            pdf_path=str(resolved_pdf) if pdf_path else "",
            workspace_dir=str(book.get("workspace_dir") or "").strip(),
            session_path=str(raw_session or "").strip(),
            db_name=str(db_name or "").strip(),
            book_id=int(book.get("id") or 0) or None,
        )

    @property
    def instance_name(self) -> str:
        left = str(self.book_code or "").strip() or "libro"
        right = str(self.instance_type or "").strip() or "instancia"
        return f"{left}__{right}"

    @property
    def normalized_instance_type(self) -> str:
        return normalize_instance_name(self.instance_type, "sesion")

    def resolved_pdf_path(self) -> Path:
        raw = str(self.pdf_path or "").strip()
        return remap_legacy_drive_path(Path(raw).expanduser(), prefer_existing=True) if raw else Path("")

    def resolved_session_path(self) -> Path | None:
        raw = str(self.session_path or "").strip()
        if raw:
            return remap_legacy_drive_path(Path(raw).expanduser(), prefer_existing=False)
        if not str(self.workspace_dir or "").strip():
            return None
        try:
            return project_dirs(Path(self.workspace_dir), self.normalized_instance_type)["session_path"]
        except Exception:
            return None

    def staging_root(self) -> Path:
        if str(self.workspace_dir or "").strip():
            layout = project_dirs(Path(self.workspace_dir), self.normalized_instance_type)
            return layout["datasets_dir"] / "pdf_factory_staging"
        safe = normalize_instance_name(self.instance_name, "instancia")
        return Path(".cache") / "transcriptor_runs" / "staging" / safe

    def to_dict(self) -> dict[str, Any]:
        return {
            "book_code": self.book_code,
            "instance_type": self.instance_type,
            "project_name": self.project_name,
            "pdf_path": self.pdf_path,
            "workspace_dir": self.workspace_dir,
            "session_path": self.session_path,
            "db_name": self.db_name,
            "book_id": self.book_id,
        }


@dataclass
class ModelStageTrace:
    stage: str
    model_id: str
    provider: str = ""
    version: str = ""
    source: str = ""
    resolved_path: str = ""
    fallback: str = ""
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "stage": self.stage,
            "model_id": self.model_id,
            "provider": self.provider,
            "version": self.version,
            "source": self.source,
            "fallback": self.fallback,
        }
        if self.resolved_path:
            payload["resolved_path"] = self.resolved_path
        if self.confidence is not None:
            payload["confidence"] = float(self.confidence)
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass
class ModelDefaults:
    pdf_detector: str = "Jhoan12/pdf-problem-detector-yolov8n-v4"
    ocr: str = "Jhoan12/math-ocr-qwen2.5-vl-3b-geometry-rules-merged-v4"
    figure_segmenter: str = "Jhoan12/problem-segmentation-yolov8n-golden-v1"
    normalizer: str = "normalizer_v0_passthrough"
    fallbacks: dict[str, str] = field(default_factory=dict)
    stages: dict[str, ModelStageTrace] = field(default_factory=dict)

    def to_dict(
        self,
        *,
        provider_overrides: dict[str, str] | None = None,
        confidence_overrides: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        stages: dict[str, dict[str, Any]] = {}
        for key, value in dict(self.stages or {}).items():
            row = value.to_dict() if isinstance(value, ModelStageTrace) else dict(value)
            if provider_overrides and key in provider_overrides:
                row["provider"] = provider_overrides[key]
            if confidence_overrides and key in confidence_overrides:
                row["confidence"] = float(confidence_overrides[key])
            stages[key] = row
        return {
            "schema_version": "model_inventory_v2",
            "pdf_detector": self.pdf_detector,
            "ocr": self.ocr,
            "figure_segmenter": self.figure_segmenter,
            "normalizer": self.normalizer,
            "fallbacks": dict(self.fallbacks),
            "stages": stages,
        }


@dataclass
class StagingProblemRecord:
    record_id: str
    crop_id: str
    crop_path: str
    status: str = StageStatus.PENDING
    source: dict[str, Any] = field(default_factory=dict)
    models: dict[str, Any] = field(default_factory=dict)
    confidence: dict[str, float] = field(default_factory=dict)
    raw_ocr: str = ""
    structured_ocr: dict[str, Any] = field(default_factory=dict)
    figure_segmentation: dict[str, Any] = field(default_factory=dict)
    normalized: dict[str, Any] = field(default_factory=dict)
    review: dict[str, Any] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    golden_sync: dict[str, Any] = field(default_factory=dict)
    training_examples: list[dict[str, Any]] = field(default_factory=list)
    audit: dict[str, Any] = field(default_factory=dict)
    steps: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_text)
    updated_at: str = field(default_factory=utc_now_text)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "StagingProblemRecord":
        record = cls(
            record_id=str(raw.get("record_id") or raw.get("crop_id") or ""),
            crop_id=str(raw.get("crop_id") or raw.get("record_id") or ""),
            crop_path=str(raw.get("crop_path") or ""),
            status=StageStatus.normalize(str(raw.get("status") or StageStatus.PENDING)),
            source=dict(raw.get("source") or {}),
            models=dict(raw.get("models") or {}),
            confidence=dict(raw.get("confidence") or {}),
            raw_ocr=str(raw.get("raw_ocr") or ""),
            structured_ocr=dict(raw.get("structured_ocr") or {}),
            figure_segmentation=dict(raw.get("figure_segmentation") or {}),
            normalized=dict(raw.get("normalized") or {}),
            review=dict(raw.get("review") or {}),
            trace=dict(raw.get("trace") or {}),
            artifacts=dict(raw.get("artifacts") or {}),
            golden_sync=dict(raw.get("golden_sync") or {}),
            training_examples=[dict(item) for item in list(raw.get("training_examples") or []) if isinstance(item, dict)],
            audit=dict(raw.get("audit") or {}),
            steps=normalize_steps(raw.get("steps") or raw.get("pipeline_steps") or {}),
            errors=[str(item) for item in list(raw.get("errors") or [])],
            created_at=str(raw.get("created_at") or utc_now_text()),
            updated_at=str(raw.get("updated_at") or utc_now_text()),
        )
        record.clear_recovered_errors()
        return record

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "crop_id": self.crop_id,
            "crop_path": self.crop_path,
            "status": StageStatus.normalize(self.status),
            "source": dict(self.source),
            "models": dict(self.models),
            "confidence": dict(self.confidence),
            "raw_ocr": self.raw_ocr,
            "structured_ocr": dict(self.structured_ocr),
            "figure_segmentation": dict(self.figure_segmentation),
            "normalized": dict(self.normalized),
            "review": dict(self.review),
            "trace": dict(self.trace),
            "artifacts": dict(self.artifacts),
            "golden_sync": dict(self.golden_sync),
            "training_examples": [dict(item) for item in self.training_examples],
            "audit": dict(self.audit),
            "steps": normalize_steps(self.steps),
            "errors": list(self.errors),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def touch(self) -> None:
        self.updated_at = utc_now_text()

    def ensure_pipeline_steps(self) -> None:
        status = StageStatus.normalize(self.status)
        for step in PipelineStep.ORDER:
            if step in self.steps:
                continue
            step_status = StageStatus.PENDING
            detail = "pendiente"
            if step == PipelineStep.REVIEW and status == StageStatus.READY:
                step_status = StageStatus.READY
                detail = "revision humana marcada lista"
            elif step == PipelineStep.REVIEW and status == StageStatus.NEEDS_REVIEW:
                step_status = StageStatus.NEEDS_REVIEW
                detail = "pendiente de revision humana"
            elif step == PipelineStep.NORMALIZATION and self.normalized:
                step_status = StageStatus.READY if status == StageStatus.READY else StageStatus.NEEDS_REVIEW
                detail = "normalizacion disponible"
            elif step == PipelineStep.OCR and (self.raw_ocr or self.structured_ocr):
                step_status = StageStatus.READY
                detail = "OCR disponible"
            elif step == PipelineStep.SEGMENTATION and self.figure_segmentation:
                step_status = StageStatus.READY
                detail = "segmentacion disponible"
            elif step == PipelineStep.CROPS and self.crop_path and Path(self.crop_path).exists():
                step_status = StageStatus.READY
                detail = "crop disponible"
            self.steps[step] = {
                "status": step_status,
                "detail": detail,
                "updated_at": utc_now_text(),
            }

    def sync_status_from_steps(self) -> None:
        self.ensure_pipeline_steps()
        step_statuses = [self.step_status(step) for step in PipelineStep.ORDER]
        review_status = self.step_status(PipelineStep.REVIEW)
        normalization_status = self.step_status(PipelineStep.NORMALIZATION)
        if self.errors or StageStatus.ERROR in step_statuses:
            self.status = StageStatus.ERROR
        elif review_status == StageStatus.READY:
            self.status = StageStatus.READY
        elif review_status == StageStatus.NEEDS_REVIEW or normalization_status == StageStatus.NEEDS_REVIEW:
            self.status = StageStatus.NEEDS_REVIEW
        elif StageStatus.PROCESSING in step_statuses:
            self.status = StageStatus.PROCESSING
        else:
            self.status = StageStatus.PENDING
        self.touch()

    def clear_recovered_errors(self) -> bool:
        """Archive stale errors when current step state shows a recovered record."""
        if not self.errors:
            return False
        previous_updated_at = self.updated_at
        self.ensure_pipeline_steps()
        step_statuses = [self.step_status(step) for step in PipelineStep.ORDER]
        if StageStatus.ERROR in step_statuses:
            self.updated_at = previous_updated_at
            return False
        recovered_steps = {
            self.step_status(PipelineStep.OCR),
            self.step_status(PipelineStep.NORMALIZATION),
            self.step_status(PipelineStep.REVIEW),
        }
        if not (recovered_steps & {StageStatus.READY, StageStatus.NEEDS_REVIEW}):
            self.updated_at = previous_updated_at
            return False
        archived = {
            "schema_version": "stale_staging_errors_archived_v1",
            "archived_at": utc_now_text(),
            "previous_status": str(self.status or ""),
            "errors": list(self.errors),
        }
        history = list((self.audit or {}).get("recovered_errors") or [])
        self.audit = {
            **dict(self.audit or {}),
            "recovered_errors": [*history, archived][-10:],
        }
        self.errors = []
        self.sync_status_from_steps()
        self.updated_at = previous_updated_at
        return True

    def set_step(self, step: str, status: str, detail: str = "", **metadata: Any) -> None:
        key = PipelineStep.normalize(step)
        current = dict(self.steps.get(key) or {})
        payload: dict[str, Any] = {
            **current,
            "status": StageStatus.normalize(status),
            "detail": str(detail or current.get("detail") or ""),
            "updated_at": utc_now_text(),
        }
        for meta_key, value in metadata.items():
            if value is not None:
                payload[meta_key] = value
        self.steps[key] = payload
        self.touch()

    def step_status(self, step: str, default: str = StageStatus.PENDING) -> str:
        payload = self.steps.get(PipelineStep.normalize(step)) or {}
        return StageStatus.normalize(str(payload.get("status") or ""), default)


def normalize_steps(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        step = PipelineStep.normalize(str(key))
        payload = dict(value)
        payload["status"] = StageStatus.normalize(str(payload.get("status") or ""))
        if "detail" in payload:
            payload["detail"] = str(payload.get("detail") or "")
        out[step] = payload
    return out
