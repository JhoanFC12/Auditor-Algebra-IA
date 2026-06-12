from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .models import (
    PIPELINE_CONTRACT_VERSION,
    InstancePipelineContext,
    PipelineStep,
    StageStatus,
    StagingProblemRecord,
    build_pipeline_contract,
    utc_now_text,
)
from .normalizer_training_bank import remove_sample as remove_normalizer_training_sample
from .normalizer_training_bank import upsert_sample as upsert_normalizer_training_sample


def _build_retraining_evaluation_matrix() -> dict[str, Any]:
    try:
        from .model_inventory import build_retraining_evaluation_matrix

        return build_retraining_evaluation_matrix()
    except Exception:
        return {}


def _build_model_inventory_manifest() -> dict[str, Any]:
    try:
        from .model_inventory import build_model_inventory_manifest

        return build_model_inventory_manifest()
    except Exception:
        return {}


MAX_ARTIFACT_RECORD_DIR_LEN = 48
MAX_ARTIFACT_PATH_LEN_SOFT_LIMIT = 240


def compact_artifact_dir_name(record_id: str, *, max_len: int = MAX_ARTIFACT_RECORD_DIR_LEN) -> str:
    raw = str(record_id or "").strip()
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._-") or "record"
    if len(clean) <= int(max_len):
        return clean
    digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]
    keep = max(1, int(max_len) - len(digest) - 1)
    return f"{clean[:keep].rstrip('._-')}_{digest}"


class InstanceStagingStore:
    schema_version = "pdf_factory_staging_v1"
    candidate_schema_version = "pdf_factory_promotion_candidate_v1"
    safe_record_id_re = re.compile(r"^[A-Za-z0-9._-]+$")
    required_metadata = {
        "libro": "source.book_code",
        "instancia": "source.instance_type",
        "pdf": "source.pdf_path",
        "pagina": "source.page_number",
        "box": "source.bbox_px",
        "crop": "crop_path",
        "modelos": "models",
        "confianza": "confidence",
        "estado": "status",
    }

    def __init__(self, context: InstancePipelineContext, root: Path | None = None) -> None:
        self.context = context
        self.root = Path(root or context.staging_root()).expanduser().resolve()
        self.records_dir = self.root / "records"
        self.root.mkdir(parents=True, exist_ok=True)
        self.records_dir.mkdir(parents=True, exist_ok=True)

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    def _record_path(self, record_id: str) -> Path:
        clean = str(record_id or "").strip()
        if not clean or not self.safe_record_id_re.match(clean):
            raise ValueError(f"record_id invalido para staging: {record_id!r}")
        return self.records_dir / f"{clean}.json"

    def artifact_dir(self, kind: str, record_id: str, *, probe_file: str = "latest.json") -> Path:
        clean_kind = re.sub(r"[^A-Za-z0-9._-]+", "_", str(kind or "").strip()).strip("._-")
        if not clean_kind:
            raise ValueError("kind de artefacto requerido")
        clean_record_id = str(record_id or "").strip()
        if not clean_record_id or not self.safe_record_id_re.match(clean_record_id):
            raise ValueError(f"record_id invalido para artefactos: {record_id!r}")
        root = self.root / clean_kind
        legacy = root / clean_record_id
        if len(clean_record_id) <= MAX_ARTIFACT_RECORD_DIR_LEN and len(str(legacy / probe_file)) < MAX_ARTIFACT_PATH_LEN_SOFT_LIMIT:
            return legacy
        return root / compact_artifact_dir_name(clean_record_id)

    def _source_identity_key(self, record: StagingProblemRecord) -> str:
        source = dict(record.source or {})
        bbox = source.get("bbox_px") or []
        if isinstance(bbox, tuple):
            bbox = list(bbox)
        try:
            bbox_key = json.dumps([int(v) for v in list(bbox)[:4]], separators=(",", ":"))
        except Exception:
            bbox_key = json.dumps(bbox, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        parts = [
            str(source.get("book_code") or "").strip(),
            str(source.get("instance_type") or "").strip(),
            str(source.get("pdf_path") or "").strip(),
            str(source.get("page_number") or "").strip(),
            bbox_key,
        ]
        if not all(parts[:4]) or bbox_key in ("[]", "null"):
            return ""
        return "|".join(parts)

    def metadata_issues(self, record: StagingProblemRecord) -> list[str]:
        source = dict(record.source or {})
        models = dict(record.models or {})
        issues: list[str] = []
        if not str(source.get("book_code") or "").strip():
            issues.append("missing:source.book_code")
        if not str(source.get("instance_type") or "").strip():
            issues.append("missing:source.instance_type")
        if not str(source.get("pdf_path") or "").strip():
            issues.append("missing:source.pdf_path")
        if source.get("page_number") in (None, ""):
            issues.append("missing:source.page_number")
        bbox = source.get("bbox_px")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            issues.append("invalid:source.bbox_px")
        if not str(record.crop_path or "").strip():
            issues.append("missing:crop_path")
        if not models:
            issues.append("missing:models")
        else:
            stages = models.get("stages")
            if isinstance(stages, dict):
                for stage, payload in stages.items():
                    if not isinstance(payload, dict):
                        issues.append(f"invalid:models.stages.{stage}")
                        continue
                    for required in ("model_id", "provider", "version", "fallback"):
                        if not str(payload.get(required) or "").strip():
                            issues.append(f"missing:models.stages.{stage}.{required}")
        if not dict(record.confidence or {}):
            issues.append("missing:confidence")
        if StageStatus.normalize(record.status, default="") not in StageStatus.values():
            issues.append(f"invalid:status:{record.status}")
        return issues

    def _prepare_record_for_write(self, record: StagingProblemRecord) -> StagingProblemRecord:
        record.record_id = str(record.record_id or record.crop_id or "").strip()
        record.crop_id = str(record.crop_id or record.record_id or "").strip()
        self._record_path(record.record_id)
        original_status = record.status
        record.status = StageStatus.normalize(record.status, default="")
        if record.status not in StageStatus.values():
            raise ValueError(f"Estado staging invalido: {original_status!r}")
        record.ensure_pipeline_steps()
        source = dict(record.source or {})
        source.setdefault("book_code", self.context.book_code)
        source.setdefault("instance_type", self.context.instance_type)
        source.setdefault("pdf_path", self.context.pdf_path)
        source.setdefault("crop_id", record.crop_id)
        record.source = source
        record.confidence = dict(record.confidence or {})
        if not record.confidence:
            record.confidence["pdf_box"] = 0.0
        now = utc_now_text()
        issues = self.metadata_issues(record)
        record.audit = {
            **dict(record.audit or {}),
            "contract_version": PIPELINE_CONTRACT_VERSION,
            "metadata_minima": {
                "schema_version": "staging_metadata_audit_v1",
                "complete": not issues,
                "missing_or_invalid": issues,
                "required": dict(self.required_metadata),
                "updated_at": now,
            },
            "identity_key": self._source_identity_key(record),
            "storage_policy": {
                "target": "staging_only",
                "problemas_write_enabled": False,
            },
        }
        record.touch()
        return record

    def validate_contract(self, records: list[StagingProblemRecord] | None = None) -> dict[str, Any]:
        rows = records if records is not None else self.load_records()
        issues: list[dict[str, Any]] = []
        allowed_statuses = StageStatus.values()
        for row in rows:
            row_status = StageStatus.normalize(row.status, default="")
            if row_status not in allowed_statuses:
                issues.append(
                    {
                        "record_id": row.record_id,
                        "issue": f"invalid:status:{row.status}",
                    }
                )
            metadata_issues = self.metadata_issues(row)
            if metadata_issues:
                issues.append(
                    {
                        "record_id": row.record_id,
                        "issue": "metadata_minima_incomplete",
                        "details": metadata_issues,
                    }
                )
            for step in PipelineStep.ORDER:
                step_status = row.step_status(step, default="")
                if step_status not in allowed_statuses:
                    issues.append(
                        {
                            "record_id": row.record_id,
                            "issue": f"invalid:step_status:{step}",
                            "status": row.steps.get(step, {}).get("status"),
                        }
                    )
            storage_policy = dict(dict(row.audit or {}).get("storage_policy") or {})
            if storage_policy.get("problemas_write_enabled") is True:
                issues.append(
                    {
                        "record_id": row.record_id,
                        "issue": "forbidden:problemas_write_enabled",
                    }
                )
        return {
            "schema_version": "pdf_factory_contract_validation_v1",
            "contract_version": PIPELINE_CONTRACT_VERSION,
            "valid": not issues,
            "issues": issues,
            "records_total": len(rows),
            "required_steps": list(PipelineStep.ORDER),
            "allowed_statuses": sorted(allowed_statuses),
            "policy": build_pipeline_contract()["storage_policy"],
        }

    def _merge_human_review_data(self, incoming: StagingProblemRecord, existing: StagingProblemRecord) -> StagingProblemRecord:
        if existing.review and not incoming.review:
            incoming.review = dict(existing.review)
        if existing.training_examples and not incoming.training_examples:
            incoming.training_examples = [dict(item) for item in existing.training_examples]
        if existing.created_at and not incoming.created_at:
            incoming.created_at = existing.created_at
        return incoming

    def _merge_duplicate_record_data(
        self,
        primary: StagingProblemRecord,
        duplicate: StagingProblemRecord,
    ) -> StagingProblemRecord:
        if duplicate.review and not primary.review:
            primary.review = dict(duplicate.review)
        if duplicate.normalized and not primary.normalized:
            primary.normalized = dict(duplicate.normalized)
        if duplicate.raw_ocr and not primary.raw_ocr:
            primary.raw_ocr = duplicate.raw_ocr
        if duplicate.structured_ocr and not primary.structured_ocr:
            primary.structured_ocr = dict(duplicate.structured_ocr)
        if duplicate.figure_segmentation and not primary.figure_segmentation:
            primary.figure_segmentation = dict(duplicate.figure_segmentation)
        if duplicate.artifacts and not primary.artifacts:
            primary.artifacts = dict(duplicate.artifacts)
        if duplicate.golden_sync and not primary.golden_sync:
            primary.golden_sync = dict(duplicate.golden_sync)
        primary.source = {**dict(duplicate.source or {}), **dict(primary.source or {})}
        primary.models = {**dict(duplicate.models or {}), **dict(primary.models or {})}
        primary.confidence = {**dict(duplicate.confidence or {}), **dict(primary.confidence or {})}
        primary.trace = {**dict(duplicate.trace or {}), **dict(primary.trace or {})}
        primary.audit = {**dict(duplicate.audit or {}), **dict(primary.audit or {})}
        primary.steps = {**dict(duplicate.steps or {}), **dict(primary.steps or {})}
        primary_status = StageStatus.normalize(primary.status)
        duplicate_status = StageStatus.normalize(duplicate.status)
        if primary_status == StageStatus.PENDING and duplicate_status in {
            StageStatus.READY,
            StageStatus.NEEDS_REVIEW,
            StageStatus.PROCESSING,
        }:
            primary.status = duplicate_status
        elif primary_status != StageStatus.READY and duplicate_status == StageStatus.READY:
            primary.status = StageStatus.READY
        seen_examples: set[str] = set()
        merged_examples: list[dict[str, Any]] = []
        for item in [*list(primary.training_examples or []), *list(duplicate.training_examples or [])]:
            if not isinstance(item, dict):
                continue
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if key in seen_examples:
                continue
            seen_examples.add(key)
            merged_examples.append(dict(item))
        primary.training_examples = merged_examples[-50:]
        primary.errors = sorted(set([*list(primary.errors or []), *list(duplicate.errors or [])]))
        if duplicate.created_at and (not primary.created_at or duplicate.created_at < primary.created_at):
            primary.created_at = duplicate.created_at
        primary.sync_status_from_steps()
        return primary

    def _coalesce_duplicate_identity(
        self,
        record: StagingProblemRecord,
        identity_to_record: dict[str, StagingProblemRecord],
    ) -> StagingProblemRecord:
        identity = self._source_identity_key(record)
        if not identity:
            return record
        existing = identity_to_record.get(identity)
        if existing is None or existing.record_id == record.record_id:
            identity_to_record[identity] = record
            return record
        record = self._merge_human_review_data(record, existing)
        record.record_id = existing.record_id
        record.crop_id = existing.crop_id or record.crop_id
        identity_to_record[identity] = record
        return record

    def _load_record_entries(self) -> list[tuple[Path, StagingProblemRecord]]:
        rows: list[tuple[Path, StagingProblemRecord]] = []
        for path in sorted(self.records_dir.glob("*.json"), key=lambda item: item.name.lower()):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, dict):
                record = StagingProblemRecord.from_dict(payload)
                if not record.record_id:
                    record.record_id = path.stem
                if not record.crop_id:
                    record.crop_id = record.record_id
                rows.append((path, record))
        return rows

    @staticmethod
    def _int_sort_value(value: Any, default: int = 10**9) -> int:
        try:
            number = int(value)
        except Exception:
            return default
        return number if number >= 0 else default

    @classmethod
    def _record_sort_key(cls, record: StagingProblemRecord) -> tuple[int, int, int, int, int, str]:
        source = dict(record.source or {})
        bbox = source.get("bbox_px") or []
        y1 = cls._int_sort_value(bbox[1] if isinstance(bbox, (list, tuple)) and len(bbox) > 1 else None)
        x1 = cls._int_sort_value(bbox[0] if isinstance(bbox, (list, tuple)) and len(bbox) > 0 else None)
        return (
            cls._int_sort_value(source.get("page_number") or source.get("source_page_number")),
            cls._int_sort_value(source.get("source_order")),
            cls._int_sort_value(source.get("box_index") or source.get("page_problem_index") or source.get("problem_index")),
            y1,
            x1,
            str(record.record_id or record.crop_id or ""),
        )

    def load_records(self) -> list[StagingProblemRecord]:
        return sorted((record for _path, record in self._load_record_entries()), key=self._record_sort_key)

    def _deduplicate_record_entries(
        self,
        entries: list[tuple[Path, StagingProblemRecord]],
    ) -> tuple[list[StagingProblemRecord], dict[str, Any]]:
        by_identity: dict[str, tuple[Path, StagingProblemRecord]] = {}
        canonical_by_path: dict[Path, StagingProblemRecord] = {}
        duplicate_paths: set[Path] = set()
        duplicate_identities: set[str] = set()
        for path, record in entries:
            identity = self._source_identity_key(record)
            if not identity:
                canonical_by_path[path] = record
                continue
            existing = by_identity.get(identity)
            if existing is None:
                by_identity[identity] = (path, record)
                canonical_by_path[path] = record
                continue
            canonical_path, canonical_record = existing
            merged = self._merge_duplicate_record_data(canonical_record, record)
            by_identity[identity] = (canonical_path, merged)
            canonical_by_path[canonical_path] = merged
            duplicate_paths.add(path)
            duplicate_identities.add(identity)

        repaired = 0
        if duplicate_paths:
            canonical_output_paths: set[Path] = set()
            for record in canonical_by_path.values():
                prepared = self._prepare_record_for_write(record)
                output_path = self._record_path(prepared.record_id)
                output_path.write_text(
                    json.dumps(prepared.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                canonical_output_paths.add(output_path.resolve())
            for path in duplicate_paths:
                try:
                    resolved = path.resolve()
                except Exception:
                    resolved = path
                if resolved in canonical_output_paths:
                    continue
                try:
                    path.unlink()
                    repaired += 1
                except FileNotFoundError:
                    pass

        rows = self.load_records() if duplicate_paths else sorted((record for _path, record in entries), key=self._record_sort_key)
        return rows, {
            "duplicate_identity_keys_before_repair": sorted(duplicate_identities),
            "duplicate_records_repaired": repaired,
        }

    def summarize_records(self, records: list[StagingProblemRecord] | None = None) -> dict[str, int]:
        rows = records if records is not None else self.load_records()
        summary = {
            "records_total": len(rows),
            "crops_found": 0,
            "ocr_done": 0,
            "segments_done": 0,
            "normalized_done": 0,
            "needs_review": 0,
            "human_reviewed": 0,
            "ready": 0,
            "errors": 0,
        }
        for row in rows:
            if row.crop_path and Path(row.crop_path).exists():
                summary["crops_found"] += 1
            if row.raw_ocr or row.structured_ocr:
                summary["ocr_done"] += 1
            if row.figure_segmentation:
                summary["segments_done"] += 1
            if row.normalized:
                summary["normalized_done"] += 1
            status = StageStatus.normalize(row.status)
            review_status = StageStatus.normalize(str(dict(row.review or {}).get("review_status") or ""))
            if status == StageStatus.READY or review_status == StageStatus.READY:
                summary["ready"] += 1
            elif status == StageStatus.HUMAN_REVIEWED or review_status == StageStatus.HUMAN_REVIEWED:
                summary["human_reviewed"] += 1
            elif status == StageStatus.NEEDS_REVIEW or review_status == StageStatus.NEEDS_REVIEW:
                summary["needs_review"] += 1
            if status == StageStatus.ERROR or row.errors:
                summary["errors"] += 1
        return summary

    def get_record(self, record_id: str) -> StagingProblemRecord | None:
        try:
            path = self._record_path(record_id)
        except ValueError:
            return None
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return StagingProblemRecord.from_dict(payload) if isinstance(payload, dict) else None

    def upsert_record(self, record: StagingProblemRecord) -> None:
        existing_by_identity = {
            self._source_identity_key(row): row
            for row in self.load_records()
            if self._source_identity_key(row)
        }
        record = self._prepare_record_for_write(record)
        record = self._coalesce_duplicate_identity(record, existing_by_identity)
        record = self._prepare_record_for_write(record)
        path = self._record_path(record.record_id)
        path.write_text(json.dumps(record.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        self.rewrite_manifest()

    def upsert_many(self, records: list[StagingProblemRecord]) -> None:
        existing_by_identity = {
            self._source_identity_key(row): row
            for row in self.load_records()
            if self._source_identity_key(row)
        }
        prepared_by_id: dict[str, StagingProblemRecord] = {}
        for record in records:
            record = self._prepare_record_for_write(record)
            record = self._coalesce_duplicate_identity(record, existing_by_identity)
            record = self._prepare_record_for_write(record)
            prepared_by_id[record.record_id] = record
        for record in prepared_by_id.values():
            path = self._record_path(record.record_id)
            path.write_text(json.dumps(record.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        self.rewrite_manifest()

    def rewrite_manifest(self) -> None:
        rows, dedupe_audit = self._deduplicate_record_entries(self._load_record_entries())
        by_status: dict[str, int] = {}
        by_step_status: dict[str, dict[str, int]] = {step: {} for step in PipelineStep.ORDER}
        identity_counts: dict[str, int] = {}
        metadata_complete = 0
        for row in rows:
            status = StageStatus.normalize(row.status)
            by_status[status] = by_status.get(status, 0) + 1
            if not self.metadata_issues(row):
                metadata_complete += 1
            identity = self._source_identity_key(row)
            if identity:
                identity_counts[identity] = identity_counts.get(identity, 0) + 1
            for step in PipelineStep.ORDER:
                step_status = row.step_status(step)
                bucket = by_step_status.setdefault(step, {})
                bucket[step_status] = bucket.get(step_status, 0) + 1
        duplicate_identity_keys = sorted(key for key, count in identity_counts.items() if count > 1)
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "contract_version": PIPELINE_CONTRACT_VERSION,
            "updated_at": utc_now_text(),
            "context": self.context.to_dict(),
            "records_total": len(rows),
            "by_status": by_status,
            "by_step_status": by_step_status,
            "summary": self.summarize_records(rows),
            "metadata": {
                "required": dict(self.required_metadata),
                "complete_records": metadata_complete,
                "incomplete_records": len(rows) - metadata_complete,
                "duplicate_identity_keys": duplicate_identity_keys,
                "duplicate_identity_total": len(duplicate_identity_keys),
                "duplicate_identity_keys_before_repair": dedupe_audit["duplicate_identity_keys_before_repair"],
                "duplicate_records_repaired": dedupe_audit["duplicate_records_repaired"],
            },
            "records_dir": "records",
            "policy": {
                "target": "staging_only",
                "never_insert_directly_into_problemas": True,
                "default_status_after_normalization": StageStatus.NEEDS_REVIEW,
                "human_corrections_are_training_data": True,
                "promotion_boundary": {
                    "prepared": True,
                    "enabled": False,
                    "target_table": "problemas",
                    "candidate_builder": "InstanceStagingStore.build_promotion_candidate",
                    "requires_ready_review": True,
                    "write_operations": [],
                },
            },
            "model_inventory": _build_model_inventory_manifest(),
            "training_contracts": {
                "schema_version": "pdf_factory_training_contracts_v1",
                "raw_outputs_dir": "raw_outputs",
                "review_outputs_dir": "review_outputs",
                "golden_contracts_dir": "golden_contracts",
                "human_review_training_example_schema": "human_review_training_example_v1",
                "golden_contract_schema": "pdf_factory_golden_contract_v1",
                "targets": [
                    "problem_crops_live",
                    "ocr_golden_live",
                    "segment_training_live",
                    "ocr_normalization_golden_live",
                ],
            },
            "contract": build_pipeline_contract(),
            "contract_validation": self.validate_contract(rows),
            "evaluation_matrix": _build_retraining_evaluation_matrix(),
        }
        self.manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def update_review(
        self,
        record_id: str,
        normalized: dict[str, Any],
        notes: str = "",
        *,
        mark_ready: bool = False,
        sync_golden: bool = True,
    ) -> StagingProblemRecord:
        record = self.get_record(record_id)
        if record is None:
            raise KeyError(record_id)
        previous_review = dict(record.review or {})
        history = list(previous_review.get("history") or [])
        machine_normalized_before = dict(record.normalized or {})
        if record.normalized:
            history.append(
                {
                    "updated_at": str(previous_review.get("updated_at") or record.updated_at or ""),
                    "normalized": dict(record.normalized),
                    "notes": str(previous_review.get("notes") or ""),
                }
            )
        record.normalized = dict(normalized or {})
        review_status = StageStatus.READY if mark_ready else StageStatus.NEEDS_REVIEW
        correction_time = utc_now_text()
        record.training_examples = [
            *[dict(item) for item in list(record.training_examples or [])],
            {
                "schema_version": "human_review_training_example_v1",
                "created_at": correction_time,
                "source_record_id": record.record_id,
                "crop_id": record.crop_id,
                "crop_path": record.crop_path,
                "source": dict(record.source or {}),
                "models": dict(record.models or {}),
                "confidence": dict(record.confidence or {}),
                "machine_normalized_before": machine_normalized_before,
                "human_normalized": dict(record.normalized or {}),
                "notes": str(notes or ""),
                "intended_use": "ocr_normalization_training",
            },
        ][-50:]
        record.trace = {
            **dict(record.trace or {}),
            "last_human_correction": {
                "updated_at": correction_time,
                "reviewer": "human",
                "fields": sorted(str(key) for key in record.normalized.keys()),
                "source_record_id": record.record_id,
                "saved_as_training_example": True,
            },
        }
        record.review = {
            **dict(record.review or {}),
            "review_status": review_status,
            "notes": str(notes or ""),
            "history": history[-20:],
            "training_examples_total": len(record.training_examples),
            "updated_at": correction_time,
        }
        record.status = review_status
        record.set_step(
            PipelineStep.REVIEW,
            review_status,
            "revision humana guardada en staging",
            notes_present=bool(str(notes or "").strip()),
        )
        self._write_review_artifacts(record)
        if sync_golden:
            self._sync_review_to_golden_bases(record, notes=str(notes or ""))
        else:
            record.golden_sync = {
                **dict(record.golden_sync or {}),
                "updated_at": utc_now_text(),
                "status": "deferred",
                "reason": "batch_review_save",
                "targets": {},
                "errors": [],
            }
        self._sync_review_to_normalizer_training_bank(record, review_status=review_status)
        self.upsert_record(record)
        return record

    def _sync_review_to_normalizer_training_bank(self, record: StagingProblemRecord, *, review_status: str) -> None:
        try:
            if StageStatus.normalize(review_status) != StageStatus.READY:
                manifest = remove_normalizer_training_sample(self.context, record)
            else:
                rows = self.load_records()
                rows = [row for row in rows if row.record_id != record.record_id] + [record]
                manifest = upsert_normalizer_training_sample(
                    self.context,
                    record,
                    staging_root=self.root,
                    all_records=rows,
                )
            record.artifacts = {
                **dict(record.artifacts or {}),
                "normalizer_training_bank_manifest": str(manifest.get("manifest_path", "")),
                "normalizer_training_samples_total": int(manifest.get("samples_total") or 0),
                "normalizer_training_ready_to_train": bool(manifest.get("ready_to_train")),
            }
        except Exception as exc:
            record.artifacts = {
                **dict(record.artifacts or {}),
                "normalizer_training_bank_error": str(exc),
            }

    def build_promotion_candidate(self, record_id: str) -> dict[str, Any]:
        record = self.get_record(record_id)
        if record is None:
            raise KeyError(record_id)
        metadata_issues = self.metadata_issues(record)
        blocking_issues = list(metadata_issues)
        normalized = dict(record.normalized or {})
        continuation = normalized.get("continuacion") if isinstance(normalized.get("continuacion"), dict) else {}
        if not normalized:
            blocking_issues.append("missing:normalized")
        if bool(continuation.get("es_continuacion") or continuation.get("fusionar_con_anterior")):
            blocking_issues.append("continuacion:fusionada_con_anterior")
        if StageStatus.normalize(record.status) != StageStatus.READY:
            blocking_issues.append("not_ready:human_review")
        return {
            "schema_version": self.candidate_schema_version,
            "created_at": utc_now_text(),
            "promotion_enabled": False,
            "target_table": "problemas",
            "ready_for_future_promotion": not blocking_issues,
            "blocking_issues": blocking_issues,
            "write_operations": [],
            "sql": None,
            "record_id": record.record_id,
            "crop_id": record.crop_id,
            "payload": {
                "normalized": normalized,
                "source": dict(record.source or {}),
                "crop_path": record.crop_path,
                "models": dict(record.models or {}),
                "confidence": dict(record.confidence or {}),
                "review": dict(record.review or {}),
                "training_examples_total": len(record.training_examples or []),
                "audit": dict(record.audit or {}),
            },
            "policy": {
                "staging_only": True,
                "never_insert_directly_into_problemas": True,
                "requires_explicit_future_promotion_flow": True,
            },
        }

    def _sync_review_to_golden_bases(self, record: StagingProblemRecord, *, notes: str = "") -> None:
        record.golden_sync = {
            **dict(record.golden_sync or {}),
            "updated_at": utc_now_text(),
            "status": "pending",
            "targets": {},
            "errors": [],
        }
        live_record_path = self._problem_crop_record_path(record)
        if live_record_path is None:
            contract = self._write_golden_contract(record, notes=notes, reason="missing_problem_crops_live_record")
            record.golden_sync.update({"status": "contract_prepared", "contract_path": str(contract)})
            return

        corrected_text = self._normalized_to_training_text(record.normalized)
        figure_boxes = self._figure_boxes_from_record(record)
        try:
            from modulos.modulo12_auditor_entrenamiento.controlador_auditor_entrenamiento import (
                TrainingAuditController,
            )

            audit = TrainingAuditController()
            crops_root = live_record_path.parent.parent
            live_rows = audit.load_problem_crops_live(crops_root, crop_ids=[record.crop_id])
            if live_rows:
                audit.save_problem_crop_review(
                    live_rows[0],
                    ocr_text=record.raw_ocr,
                    corrected_text=corrected_text,
                    notes=notes,
                    ocr_status="corrected" if corrected_text.strip() else "pending_ocr",
                    figure_segmentation_status="reviewed" if figure_boxes else "pending_figure_segmentation",
                    figure_boxes_px=figure_boxes,
                    root=crops_root,
                )
            ocr_target, ocr_added = audit.import_problem_crops_into_ocr_golden(
                crops_root=crops_root,
                crop_ids=[record.crop_id],
                session_json=str(self.context.resolved_session_path() or ""),
                book_code=self.context.book_code,
                instance_type=self.context.instance_type,
                project_name=self.context.project_name,
            )
            normalizer_target = audit.build_ocr_normalization_golden_base(ocr_golden_dir=ocr_target)
            record.golden_sync["targets"] = {
                "problem_crops_live": str(crops_root),
                "ocr_golden": str(ocr_target),
                "ocr_golden_added": int(ocr_added),
                "ocr_normalization_golden": str(normalizer_target),
            }
            if figure_boxes or record.figure_segmentation:
                segment_target, segment_added, positives, boxes_total = audit.import_problem_crops_into_segment_golden(
                    crops_root=crops_root,
                    crop_ids=[record.crop_id],
                )
                record.golden_sync["targets"] = {
                    **dict(record.golden_sync.get("targets") or {}),
                    "segment_golden": str(segment_target),
                    "segment_golden_added": int(segment_added),
                    "segment_positive_images": int(positives),
                    "segment_boxes": int(boxes_total),
                }
            record.golden_sync["status"] = "synced"
        except Exception as exc:
            contract = self._write_golden_contract(record, notes=notes, reason="golden_api_error")
            record.golden_sync["status"] = "contract_prepared"
            record.golden_sync["contract_path"] = str(contract)
            record.golden_sync["errors"] = [*list(record.golden_sync.get("errors") or []), str(exc)]

    def _write_review_artifacts(self, record: StagingProblemRecord) -> None:
        artifacts_dir = self.artifact_dir("review_outputs", record.record_id, probe_file="training_examples.json")
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        latest_path = artifacts_dir / "latest_review.json"
        examples_path = artifacts_dir / "training_examples.json"
        history_path = artifacts_dir / "review_history.jsonl"
        payload = {
            "schema_version": "pdf_factory_review_artifact_v1",
            "updated_at": utc_now_text(),
            "context": self.context.to_dict(),
            "record_id": record.record_id,
            "crop_id": record.crop_id,
            "crop_path": record.crop_path,
            "source": dict(record.source or {}),
            "raw_ocr": record.raw_ocr,
            "structured_ocr": dict(record.structured_ocr or {}),
            "figure_segmentation": dict(record.figure_segmentation or {}),
            "machine_and_human_normalized": dict(record.normalized or {}),
            "review": dict(record.review or {}),
            "models": dict(record.models or {}),
            "confidence": dict(record.confidence or {}),
            "training_examples": [dict(item) for item in list(record.training_examples or [])],
            "intended_targets": [
                "problem_crops_live",
                "ocr_golden_live",
                "segment_training_live",
                "ocr_normalization_golden_live",
            ],
        }
        latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        examples_path.write_text(
            json.dumps(payload["training_examples"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        record.artifacts = {
            **dict(record.artifacts or {}),
            "review_outputs_schema": "pdf_factory_review_artifact_v1",
            "review_updated_at": payload["updated_at"],
            "latest_review": str(latest_path),
            "training_examples": str(examples_path),
            "review_history": str(history_path),
        }

    def _problem_crop_record_path(self, record: StagingProblemRecord) -> Path | None:
        raw = str(record.source.get("problem_crops_live_record") or "").strip()
        if not raw:
            return None
        path = Path(raw).expanduser()
        if path.exists():
            return path.resolve()
        return None

    def _write_golden_contract(self, record: StagingProblemRecord, *, notes: str, reason: str) -> Path:
        contracts_dir = self.root / "golden_contracts"
        contracts_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "pdf_factory_golden_contract_v1",
            "created_at": utc_now_text(),
            "reason": reason,
            "context": self.context.to_dict(),
            "record_id": record.record_id,
            "crop_id": record.crop_id,
            "crop_path": record.crop_path,
            "source": dict(record.source),
            "raw_ocr": record.raw_ocr,
            "structured_ocr": dict(record.structured_ocr),
            "normalized_human": dict(record.normalized),
            "corrected_text": self._normalized_to_training_text(record.normalized),
            "figure_boxes_px": self._figure_boxes_from_record(record),
            "models": dict(record.models),
            "confidence": dict(record.confidence),
            "notes": str(notes or ""),
            "intended_targets": [
                "problem_crops_live",
                "ocr_golden_live",
                "segment_training_live",
                "ocr_normalization_golden_live",
            ],
        }
        path = contracts_dir / f"{record.record_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @staticmethod
    def _normalized_to_training_text(normalized: dict[str, Any]) -> str:
        rendered = str(normalized.get("latex_rendered_item") or "").strip()
        if rendered:
            return rendered
        statement = str(normalized.get("enunciado_latex") or "").strip()
        options = normalized.get("alternativas") if isinstance(normalized.get("alternativas"), dict) else {}
        option_text = " ".join(
            f"{label}) {str(options.get(label, '') or '').strip()}"
            for label in ("A", "B", "C", "D", "E")
            if str(options.get(label, "") or "").strip()
        ).strip()
        answer = str(normalized.get("respuesta_correcta") or "").strip()
        parts = [part for part in (statement, option_text, f"[[Clave={answer}]]" if answer else "") if part]
        if parts:
            return "\n".join(parts)
        return json.dumps(normalized, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _figure_boxes_from_record(record: StagingProblemRecord) -> list[list[int]]:
        segments = record.figure_segmentation.get("segments") if isinstance(record.figure_segmentation, dict) else []
        out: list[list[int]] = []
        for segment in segments if isinstance(segments, list) else []:
            if not isinstance(segment, dict):
                continue
            raw = segment.get("bbox_px")
            if not isinstance(raw, (list, tuple)) or len(raw) < 4:
                continue
            try:
                box = [int(value) for value in raw[:4]]
            except Exception:
                continue
            if box[2] > box[0] and box[3] > box[1]:
                out.append(box)
        return out
