from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .hf_endpoint_manager import call_with_hf_ocr_retry
from .server_storage import ServerStorageResolver


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ServerJobStatus:
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"

    ACTIVE = {QUEUED, RUNNING}


@dataclass
class ServerJob:
    job_id: str
    kind: str
    status: str = ServerJobStatus.QUEUED
    created_at: str = field(default_factory=utc_now_text)
    updated_at: str = field(default_factory=utc_now_text)
    instance_key: str = ""
    total: int = 0
    current: int = 0
    ok: int = 0
    failed: int = 0
    message: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    logs: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def running(self) -> bool:
        return self.status in ServerJobStatus.ACTIVE

    @property
    def progress_label(self) -> str:
        total = max(0, int(self.total or 0))
        current = max(0, int(self.current or 0))
        return f"{current}/{total}" if total else "0/0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "server_factory_job_v1",
            "job_id": self.job_id,
            "kind": self.kind,
            "status": self.status,
            "running": self.running,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "instance_key": self.instance_key,
            "total": int(self.total or 0),
            "current": int(self.current or 0),
            "ok": int(self.ok or 0),
            "failed": int(self.failed or 0),
            "progress_label": self.progress_label,
            "message": self.message,
            "input": dict(self.input or {}),
            "output": dict(self.output or {}),
            "logs": list(self.logs or []),
            "errors": list(self.errors or []),
        }

    def public_dict(self, *, include_input: bool = False) -> dict[str, Any]:
        payload = self.to_dict()
        if not include_input:
            payload.pop("input", None)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ServerJob":
        return cls(
            job_id=str(payload.get("job_id") or ""),
            kind=str(payload.get("kind") or ""),
            status=str(payload.get("status") or ServerJobStatus.QUEUED),
            created_at=str(payload.get("created_at") or utc_now_text()),
            updated_at=str(payload.get("updated_at") or utc_now_text()),
            instance_key=str(payload.get("instance_key") or ""),
            total=int(payload.get("total") or 0),
            current=int(payload.get("current") or 0),
            ok=int(payload.get("ok") or 0),
            failed=int(payload.get("failed") or 0),
            message=str(payload.get("message") or ""),
            input=dict(payload.get("input") or {}),
            output=dict(payload.get("output") or {}),
            logs=list(payload.get("logs") or []),
            errors=list(payload.get("errors") or []),
        )


class ServerJobStore:
    """Small JSON-backed job store used by server-side factory workers."""

    def __init__(self, root: str | Path | None = None, *, storage: ServerStorageResolver | None = None) -> None:
        self.storage = storage or ServerStorageResolver(root=Path(root).parent if root else None)
        self.root = Path(root).expanduser() if root is not None else self.storage.jobs_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, job_id: str) -> Path:
        safe = "".join(ch for ch in str(job_id or "") if ch.isalnum() or ch in {"-", "_"}) or "job"
        return self.root / f"{safe}.json"

    def _write(self, job: ServerJob) -> ServerJob:
        job.updated_at = utc_now_text()
        path = self._path(job.job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(job.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return job

    def create(
        self,
        *,
        kind: str,
        input: dict[str, Any] | None = None,
        total: int = 0,
        instance_key: str = "",
        job_id: str = "",
    ) -> ServerJob:
        with self._lock:
            job = ServerJob(
                job_id=job_id or uuid.uuid4().hex,
                kind=str(kind or "job"),
                input=dict(input or {}),
                total=max(0, int(total or 0)),
                instance_key=str(instance_key or ""),
            )
            return self._write(job)

    def get(self, job_id: str) -> ServerJob | None:
        path = self._path(job_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return ServerJob.from_dict(payload) if isinstance(payload, dict) else None

    def update(self, job_id: str, **updates: Any) -> ServerJob:
        with self._lock:
            job = self.get(job_id)
            if job is None:
                raise KeyError(f"Unknown server job: {job_id}")
            for key, value in updates.items():
                if hasattr(job, key):
                    setattr(job, key, value)
            return self._write(job)

    def mark_running(self, job_id: str, message: str = "") -> ServerJob:
        return self.update(job_id, status=ServerJobStatus.RUNNING, message=message)

    def update_progress(self, job_id: str, *, current: int, ok: int | None = None, failed: int | None = None, message: str = "") -> ServerJob:
        updates: dict[str, Any] = {"current": max(0, int(current or 0))}
        if ok is not None:
            updates["ok"] = max(0, int(ok or 0))
        if failed is not None:
            updates["failed"] = max(0, int(failed or 0))
        if message:
            updates["message"] = message
        return self.update(job_id, **updates)

    def append_log(self, job_id: str, message: str, *, event: str = "info", data: dict[str, Any] | None = None) -> ServerJob:
        with self._lock:
            job = self.get(job_id)
            if job is None:
                raise KeyError(f"Unknown server job: {job_id}")
            job.logs.append({"at": utc_now_text(), "event": str(event or "info"), "message": str(message or ""), "data": dict(data or {})})
            job.logs = job.logs[-300:]
            return self._write(job)

    def append_error(self, job_id: str, message: str, *, code: str = "", data: dict[str, Any] | None = None) -> ServerJob:
        with self._lock:
            job = self.get(job_id)
            if job is None:
                raise KeyError(f"Unknown server job: {job_id}")
            job.errors.append({"at": utc_now_text(), "code": str(code or ""), "message": str(message or ""), "data": dict(data or {})})
            job.errors = job.errors[-300:]
            job.failed = max(0, int(job.failed or 0)) + 1
            job.message = str(message or job.message)
            return self._write(job)

    def complete(self, job_id: str, *, output: dict[str, Any] | None = None, message: str = "") -> ServerJob:
        job = self.get(job_id)
        if job is None:
            raise KeyError(f"Unknown server job: {job_id}")
        current = int(job.total or job.current or 0)
        return self.update(
            job_id,
            status=ServerJobStatus.DONE,
            current=current,
            output=dict(output or job.output or {}),
            message=message or "Job completed.",
        )

    def fail(self, job_id: str, message: str, *, code: str = "", data: dict[str, Any] | None = None) -> ServerJob:
        self.append_error(job_id, message, code=code, data=data)
        return self.update(job_id, status=ServerJobStatus.ERROR, message=message)

    def cancel(self, job_id: str, message: str = "Job cancelled.") -> ServerJob:
        return self.update(job_id, status=ServerJobStatus.CANCELLED, message=message)

    def list_jobs(self, *, status: str = "", limit: int = 50) -> list[ServerJob]:
        rows: list[ServerJob] = []
        for path in sorted(self.root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            job = self.get(path.stem)
            if job is None:
                continue
            if status and job.status != status:
                continue
            rows.append(job)
            if len(rows) >= max(1, int(limit or 50)):
                break
        return rows

    def active_jobs(self) -> list[ServerJob]:
        return [job for job in self.list_jobs(limit=500) if job.running]


def _page_number_from_item(item: Mapping[str, Any], position: int) -> int:
    for key in ("page", "page_number", "page_index"):
        raw = item.get(key)
        try:
            value = int(raw)
        except Exception:
            continue
        return value + 1 if key == "page_index" else value
    return position


def _detector_boxes(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, Mapping):
        raw_boxes = result.get("boxes") or result.get("detections") or []
    else:
        raw_boxes = result or []
    boxes: list[dict[str, Any]] = []
    for raw in raw_boxes:
        if isinstance(raw, Mapping):
            boxes.append(dict(raw))
    return boxes


def _record_id_from_item(item: Mapping[str, Any], position: int) -> str:
    for key in ("record_id", "crop_id", "id"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return f"record_{max(1, int(position or 1)):04d}"


def _ocr_text_from_result(result: Any) -> str:
    if isinstance(result, Mapping):
        for key in ("raw_ocr", "ocr", "text", "content", "output"):
            value = result.get(key)
            if value is not None:
                return str(value)
        choices = result.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0] if isinstance(choices[0], Mapping) else {}
            message = first.get("message") if isinstance(first, Mapping) else {}
            if isinstance(message, Mapping) and message.get("content") is not None:
                return str(message.get("content") or "")
    return str(result or "")


def _server_safe_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _server_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_server_safe_value(item) for item in value]
    if isinstance(value, Path):
        return value.name
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return text
        if len(text) >= 2 and text[1] == ":":
            return Path(text).name
        if text.startswith("\\\\"):
            return Path(text.replace("\\", "/")).name
    return value


def _notify_ocr_retry(store: ServerJobStore, job_id: str, event: dict[str, Any]) -> None:
    message = str(event.get("message") or event.get("event") or "").strip()
    try:
        store.append_log(job_id, message, event=str(event.get("event") or "hf_ocr"), data=dict(event))
    except Exception:
        pass


def run_problem_segmentation_job(
    *,
    store: ServerJobStore,
    job_id: str,
    page_items: Sequence[Mapping[str, Any]],
    detector: Callable[[Mapping[str, Any]], Any],
    storage: ServerStorageResolver | None = None,
    book_code: str = "",
    instance_code: str = "",
    staging: Any | None = None,
) -> ServerJob:
    """Run a persisted page/problem segmentation job.

    The detector callable is intentionally injected so this runner can be tested
    without loading YOLO. Pipeline wiring will provide the real detector later.
    """
    storage = storage or store.storage
    total = len(page_items)
    store.update(job_id, total=total, current=0, ok=0, failed=0, output={})
    store.mark_running(job_id, "Problem segmentation started.")

    ok = 0
    failed = 0
    pages: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []

    for position, page_item in enumerate(page_items, start=1):
        page_number = _page_number_from_item(page_item, position)
        try:
            result = detector(page_item)
            boxes = _detector_boxes(result)
            page_payload = {
                "schema_version": "server_problem_segmentation_page_v1",
                "page_number": page_number,
                "position": position,
                "boxes": boxes,
                "source": _server_safe_value(dict(page_item)),
            }
            artifact: dict[str, Any] = {}
            if book_code and instance_code:
                path = storage.artifact_path(
                    book_code=book_code,
                    instance_code=instance_code,
                    kind="boxes",
                    parts=(f"page_{page_number:04d}_boxes.json",),
                )
                storage.ensure_parent(path)
                path.write_text(json.dumps(page_payload, ensure_ascii=False, indent=2), encoding="utf-8")
                artifact = storage.artifact_record(path, kind="page_boxes")
                artifacts.append(artifact)
            if staging is not None and hasattr(staging, "record_server_page_boxes"):
                staging.record_server_page_boxes(
                    page_number=page_number,
                    position=position,
                    boxes=boxes,
                    artifact=artifact,
                    job_id=job_id,
                    rewrite_manifest=False,
                )
            pages.append(
                {
                    "page_number": page_number,
                    "position": position,
                    "boxes_count": len(boxes),
                    "artifact": artifact,
                }
            )
            ok += 1
            store.update_progress(
                job_id,
                current=position,
                ok=ok,
                failed=failed,
                message=f"Problem segmentation {position}/{total}",
            )
        except Exception as exc:
            failed += 1
            store.append_error(
                job_id,
                str(exc),
                code="problem_segmentation_error",
                data={"page_number": page_number, "position": position},
            )
            store.update_progress(
                job_id,
                current=position,
                ok=ok,
                failed=failed,
                message=f"Problem segmentation error {position}/{total}",
            )

    if staging is not None and hasattr(staging, "rewrite_manifest"):
        staging.rewrite_manifest()

    output = {
        "schema_version": "server_problem_segmentation_job_output_v1",
        "pages": pages,
        "artifacts": artifacts,
        "ok": ok,
        "failed": failed,
    }
    if failed:
        return store.update(job_id, status=ServerJobStatus.ERROR, output=output, message=f"Segmentation finished with {failed} error(s).")
    return store.complete(job_id, output=output, message="Problem segmentation completed.")


def run_hf_ocr_job(
    *,
    store: ServerJobStore,
    job_id: str,
    record_items: Sequence[Mapping[str, Any]],
    ocr_client: Callable[[Mapping[str, Any]], Any],
    storage: ServerStorageResolver | None = None,
    book_code: str = "",
    instance_code: str = "",
    staging: Any | None = None,
    endpoint_manager: Any | None = None,
    retry_sleep: Callable[[float], None] | None = None,
    shutdown_when_done: bool = True,
) -> ServerJob:
    """Run OCR requests and persist raw OCR as server-safe staging artifacts."""
    storage = storage or store.storage
    total = len(record_items)
    store.update(job_id, total=total, current=0, ok=0, failed=0, output={})
    store.mark_running(job_id, "HF OCR started.")

    endpoint_lease_id = ""
    if endpoint_manager is not None and hasattr(endpoint_manager, "begin_job"):
        endpoint_lease_id = str(
            endpoint_manager.begin_job(
                kind="server_hf_ocr",
                job_id=job_id,
                label=f"{book_code} / {instance_code}".strip(" /"),
            )
            or ""
        )
    if endpoint_manager is not None and hasattr(endpoint_manager, "ensure_ready"):
        try:
            endpoint_manager.ensure_ready()
        except TypeError:
            endpoint_manager.ensure_ready(timeout_s=420, poll_s=8)

    ok = 0
    failed = 0
    records: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []

    try:
        for position, item in enumerate(record_items, start=1):
            record_id = _record_id_from_item(item, position)
            try:
                result = call_with_hf_ocr_retry(
                    lambda _item=item: ocr_client(_item),
                    sleep_func=retry_sleep,
                    status_callback=lambda event, _job_id=job_id: _notify_ocr_retry(store, _job_id, event),
                )
                raw_ocr = _ocr_text_from_result(result)
                artifact: dict[str, Any] = {}
                if book_code and instance_code:
                    path = storage.artifact_path(
                        book_code=book_code,
                        instance_code=instance_code,
                        kind="raw_ocr",
                        parts=(record_id, "raw_ocr.txt"),
                    )
                    storage.ensure_parent(path)
                    path.write_text(raw_ocr, encoding="utf-8")
                    artifact = storage.artifact_record(path, kind="raw_ocr")
                    artifacts.append(artifact)
                if staging is not None and hasattr(staging, "record_server_raw_ocr"):
                    staging.record_server_raw_ocr(
                        record_id=record_id,
                        raw_ocr=raw_ocr,
                        artifact=artifact,
                        job_id=job_id,
                        position=position,
                        model=str(item.get("model") or ""),
                        rewrite_manifest=False,
                    )
                records.append(
                    {
                        "record_id": record_id,
                        "position": position,
                        "characters": len(raw_ocr),
                        "artifact": artifact,
                    }
                )
                ok += 1
                store.update_progress(
                    job_id,
                    current=position,
                    ok=ok,
                    failed=failed,
                    message=f"HF OCR {position}/{total}",
                )
            except Exception as exc:
                failed += 1
                store.append_error(
                    job_id,
                    str(exc),
                    code="hf_ocr_error",
                    data={"record_id": record_id, "position": position},
                )
                store.update_progress(
                    job_id,
                    current=position,
                    ok=ok,
                    failed=failed,
                    message=f"HF OCR error {position}/{total}",
                )
    finally:
        if endpoint_manager is not None and endpoint_lease_id and hasattr(endpoint_manager, "end_job"):
            try:
                endpoint_manager.end_job(endpoint_lease_id)
            except Exception:
                pass
        if staging is not None and hasattr(staging, "rewrite_manifest"):
            staging.rewrite_manifest()

    endpoint_shutdown: dict[str, Any] = {}
    if shutdown_when_done and endpoint_manager is not None and hasattr(endpoint_manager, "scale_to_zero_if_idle"):
        try:
            endpoint_shutdown = dict(endpoint_manager.scale_to_zero_if_idle() or {})
        except Exception as exc:
            endpoint_shutdown = {"status": "error", "message": str(exc)}

    output = {
        "schema_version": "server_hf_ocr_job_output_v1",
        "records": records,
        "artifacts": artifacts,
        "ok": ok,
        "failed": failed,
        "endpoint_shutdown": endpoint_shutdown,
    }
    if failed:
        return store.update(job_id, status=ServerJobStatus.ERROR, output=output, message=f"HF OCR finished with {failed} error(s).")
    return store.complete(job_id, output=output, message="HF OCR completed.")
