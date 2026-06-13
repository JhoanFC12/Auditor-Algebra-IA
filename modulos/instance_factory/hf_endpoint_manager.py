from __future__ import annotations

import os
import json
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .runtime_env import load_factory_runtime_env


MANAGE_ENDPOINT_PERMISSION_HINT = (
    "El HF_TOKEN no tiene permisos para administrar endpoints. "
    "Activa el permiso 'Manage your Inference Endpoints' en Hugging Face."
)

_ACTIVE_ENDPOINT_JOBS: dict[str, dict[str, Any]] = {}
_ACTIVE_ENDPOINT_JOBS_LOCK = threading.RLock()
_PROCESS_ID = f"{os.getpid()}:{uuid.uuid4().hex}"
_DEFAULT_ENDPOINT_LEASE_FILE = Path(__file__).resolve().parents[2] / ".cache" / "instance_factory" / "hf_ocr_endpoint_leases.json"


@dataclass(frozen=True)
class HfEndpointConfig:
    name: str
    base_url: str
    token: str

    @property
    def configured(self) -> bool:
        return bool(self.name or self.base_url)


def _normalize_endpoint_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    if raw.endswith("/v1"):
        raw = raw[:-3].rstrip("/")
    return raw.lower()


def _safe_status(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "unknown"
    return raw.split(".")[-1]


def _looks_permission_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return any(token in text for token in ("403", "forbidden", "permission", "insufficient"))


def begin_ocr_endpoint_job(*, kind: str, job_id: str, label: str = "") -> str:
    lease_id = f"{str(kind or 'ocr').strip() or 'ocr'}:{str(job_id or uuid.uuid4().hex).strip()}:{uuid.uuid4().hex}"
    now = time.time()
    with _ACTIVE_ENDPOINT_JOBS_LOCK:
        _ACTIVE_ENDPOINT_JOBS[lease_id] = {
            "lease_id": lease_id,
            "kind": str(kind or "ocr"),
            "job_id": str(job_id or ""),
            "label": str(label or ""),
            "started_at": now,
            "updated_at": now,
            "pid": os.getpid(),
            "process_id": _PROCESS_ID,
        }
        _sync_process_leases_locked()
    return lease_id


def end_ocr_endpoint_job(lease_id: str) -> None:
    key = str(lease_id or "").strip()
    if not key:
        return
    with _ACTIVE_ENDPOINT_JOBS_LOCK:
        _ACTIVE_ENDPOINT_JOBS.pop(key, None)
        _sync_process_leases_locked()


def active_ocr_endpoint_jobs() -> list[dict[str, Any]]:
    now = time.time()
    with _ACTIVE_ENDPOINT_JOBS_LOCK:
        _sync_process_leases_locked()
        rows_by_id: dict[str, dict[str, Any]] = {}
        for item in _read_lease_rows_locked():
            lease_id = str(item.get("lease_id") or "").strip()
            if lease_id:
                rows_by_id[lease_id] = dict(item)
        for item in _ACTIVE_ENDPOINT_JOBS.values():
            lease_id = str(item.get("lease_id") or "").strip()
            if lease_id:
                rows_by_id[lease_id] = dict(item)
        rows = list(rows_by_id.values())
    for row in rows:
        try:
            row["age_s"] = max(0.0, round(now - float(row.get("started_at") or now), 1))
        except Exception:
            row["age_s"] = 0.0
    rows.sort(key=lambda item: (str(item.get("kind") or ""), str(item.get("job_id") or ""), str(item.get("lease_id") or "")))
    return rows


def _lease_file_path() -> Path:
    override = str(os.getenv("HF_ENDPOINT_LEASE_FILE", "") or "").strip()
    return Path(override) if override else _DEFAULT_ENDPOINT_LEASE_FILE


def _lease_ttl_s() -> float:
    raw = os.getenv("HF_ENDPOINT_JOB_LEASE_TTL_SECONDS", "") or "43200"
    try:
        return max(60.0, min(86400.0, float(str(raw).strip())))
    except Exception:
        return 43200.0


def _row_is_fresh(row: dict[str, Any], *, now: float | None = None) -> bool:
    current = time.time() if now is None else float(now)
    try:
        stamp = float(row.get("updated_at") or row.get("started_at") or 0.0)
    except Exception:
        stamp = 0.0
    return stamp > 0 and (current - stamp) <= _lease_ttl_s()


def _read_lease_rows_locked() -> list[dict[str, Any]]:
    path = _lease_file_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = payload.get("leases") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    now = time.time()
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict) and _row_is_fresh(row, now=now):
            out.append(dict(row))
    return out


def _sync_process_leases_locked() -> None:
    path = _lease_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return
    now = time.time()
    active = []
    for item in _ACTIVE_ENDPOINT_JOBS.values():
        row = dict(item)
        row["updated_at"] = now
        row["pid"] = os.getpid()
        row["process_id"] = _PROCESS_ID
        active.append(row)
    try:
        rows = [
            row
            for row in _read_lease_rows_locked()
            if str(row.get("process_id") or "") != _PROCESS_ID
        ]
        rows.extend(active)
        payload = {
            "schema_version": "hf_ocr_endpoint_leases_v1",
            "updated_at": now,
            "leases": rows,
        }
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        try:
            if "tmp" in locals() and tmp.exists():
                tmp.unlink()
        except Exception:
            pass


class HfEndpointManager:
    """Lifecycle helper for the dedicated trained OCR Hugging Face endpoint."""

    def __init__(self, *, api_factory: Callable[[str], Any] | None = None, env_root: str | os.PathLike[str] | None = None) -> None:
        self.api_factory = api_factory
        self.env_root = env_root

    def config(self) -> HfEndpointConfig:
        load_factory_runtime_env(self.env_root)
        token = str(os.getenv("HF_TOKEN", "") or os.getenv("HUGGINGFACEHUB_API_TOKEN", "") or "").strip()
        return HfEndpointConfig(
            name=str(os.getenv("HF_TRAINED_OCR_ENDPOINT_NAME", "") or "").strip(),
            base_url=str(os.getenv("HF_TRAINED_OCR_BASE_URL", "") or "").strip(),
            token=token,
        )

    def status(self) -> dict[str, Any]:
        cfg = self.config()
        base = self._base_payload(cfg)
        if not cfg.configured:
            return {
                **base,
                "status": "no_configurado",
                "manageable": False,
                "message": "Configura HF_TRAINED_OCR_ENDPOINT_NAME o HF_TRAINED_OCR_BASE_URL.",
            }
        if not cfg.token:
            return {
                **base,
                "status": "error",
                "manageable": False,
                "message": "Falta HF_TOKEN para consultar el endpoint OCR.",
            }
        try:
            endpoint = self._find_endpoint(cfg)
            return self._endpoint_payload(endpoint, cfg, message="Endpoint OCR localizado.")
        except Exception as exc:
            return self._error_payload(exc, cfg)

    def resume(self, *, wait: bool = True, timeout_s: int = 420, poll_s: int = 8) -> dict[str, Any]:
        cfg = self.config()
        self._ensure_can_manage(cfg)
        try:
            endpoint = self._find_endpoint(cfg)
            status = _safe_status(getattr(endpoint, "status", ""))
            if status.lower() == "paused":
                endpoint = endpoint.resume(running_ok=True)
            if wait and status.lower() != "scaledtozero":
                endpoint = endpoint.wait(timeout=int(timeout_s), refresh_every=max(1, int(poll_s)))
            payload = self._endpoint_payload(endpoint, cfg, message="Endpoint OCR encendido.")
            if status.lower() == "scaledtozero":
                payload["cold_start"] = True
                payload["message"] = "Endpoint OCR en scale-to-zero; la proxima llamada lo despertara."
            return payload
        except Exception as exc:
            if _looks_permission_error(exc):
                raise PermissionError(MANAGE_ENDPOINT_PERMISSION_HINT) from exc
            raise

    def ensure_ready(self, *, timeout_s: int = 420, poll_s: int = 8) -> dict[str, Any]:
        cfg = self.config()
        self._ensure_can_manage(cfg)
        try:
            endpoint = self._find_endpoint(cfg)
            status = _safe_status(getattr(endpoint, "status", "")).lower()
            if status == "paused":
                return self.resume(wait=True, timeout_s=timeout_s, poll_s=poll_s)
            payload = self._endpoint_payload(endpoint, cfg, message="Endpoint OCR disponible.")
            if status == "scaledtozero":
                payload["cold_start"] = True
                payload["message"] = "Endpoint OCR en scale-to-zero; se despertara con la llamada OCR."
            return payload
        except Exception as exc:
            if _looks_permission_error(exc):
                raise PermissionError(MANAGE_ENDPOINT_PERMISSION_HINT) from exc
            raise

    def scale_to_zero(self) -> dict[str, Any]:
        cfg = self.config()
        self._ensure_can_manage(cfg)
        try:
            endpoint = self._find_endpoint(cfg)
            endpoint = endpoint.scale_to_zero()
            return self._endpoint_payload(endpoint, cfg, message="Endpoint OCR apagado para ahorro.")
        except Exception as exc:
            if _looks_permission_error(exc):
                raise PermissionError(MANAGE_ENDPOINT_PERMISSION_HINT) from exc
            raise

    def begin_job(self, *, kind: str, job_id: str, label: str = "") -> str:
        return begin_ocr_endpoint_job(kind=kind, job_id=job_id, label=label)

    def end_job(self, lease_id: str) -> None:
        end_ocr_endpoint_job(lease_id)

    def active_jobs(self) -> list[dict[str, Any]]:
        return active_ocr_endpoint_jobs()

    def scale_to_zero_if_idle(self, *, force: bool = False, delay_s: float | None = None) -> dict[str, Any]:
        active = active_ocr_endpoint_jobs()
        if active and not force:
            return self._scale_skipped_payload(active, message="Endpoint OCR se mantiene activo porque hay jobs OCR en curso.")
        wait_s = self._idle_shutdown_delay_s() if delay_s is None else max(0.0, min(300.0, float(delay_s)))
        if wait_s > 0:
            time.sleep(wait_s)
            active = active_ocr_endpoint_jobs()
            if active and not force:
                return self._scale_skipped_payload(active, message="Endpoint OCR se mantiene activo porque entro otro job OCR.")
        payload = self.scale_to_zero()
        payload["active_jobs"] = []
        payload["idle_delay_s"] = wait_s
        return payload

    def _ensure_can_manage(self, cfg: HfEndpointConfig) -> None:
        if not cfg.configured:
            raise RuntimeError("Configura HF_TRAINED_OCR_ENDPOINT_NAME o HF_TRAINED_OCR_BASE_URL.")
        if not cfg.token:
            raise RuntimeError("Falta HF_TOKEN para administrar el endpoint OCR.")

    def _idle_shutdown_delay_s(self) -> float:
        load_factory_runtime_env(self.env_root)
        raw = (
            os.getenv("HF_ENDPOINT_IDLE_SHUTDOWN_DELAY_SECONDS", "")
            or os.getenv("HF_TRAINED_OCR_IDLE_SHUTDOWN_DELAY_SECONDS", "")
            or "180"
        )
        try:
            return max(0.0, min(300.0, float(str(raw).strip())))
        except Exception:
            return 180.0

    def _scale_skipped_payload(self, active: list[dict[str, Any]], *, message: str) -> dict[str, Any]:
        cfg = self.config()
        return {
            **self._base_payload(cfg),
            "status": "skipped",
            "reason": "active_ocr_jobs",
            "message": message,
            "active_jobs": active,
            "active_count": len(active),
        }

    def _api(self, token: str) -> Any:
        if self.api_factory is not None:
            return self.api_factory(token)
        try:
            from huggingface_hub import HfApi
        except Exception as exc:
            raise RuntimeError("Falta huggingface_hub para administrar el endpoint OCR.") from exc
        return HfApi(token=token)

    def _find_endpoint(self, cfg: HfEndpointConfig) -> Any:
        api = self._api(cfg.token)
        if cfg.name:
            try:
                return api.get_inference_endpoint(cfg.name)
            except Exception:
                if not cfg.base_url:
                    raise
        expected_url = _normalize_endpoint_url(cfg.base_url)
        if expected_url:
            for endpoint in api.list_inference_endpoints():
                if _normalize_endpoint_url(str(getattr(endpoint, "url", "") or "")) == expected_url:
                    return endpoint
        if cfg.name:
            raise RuntimeError(f"No se encontro el endpoint OCR dedicado: {cfg.name}")
        raise RuntimeError("No se encontro un endpoint OCR que coincida con HF_TRAINED_OCR_BASE_URL.")

    def _base_payload(self, cfg: HfEndpointConfig) -> dict[str, Any]:
        return {
            "schema_version": "hf_ocr_endpoint_status_v1",
            "configured": cfg.configured,
            "name": cfg.name,
            "url": cfg.base_url,
            "manageable": bool(cfg.token and cfg.configured),
            "cold_start": False,
        }

    def _endpoint_payload(self, endpoint: Any, cfg: HfEndpointConfig, *, message: str = "") -> dict[str, Any]:
        status = _safe_status(getattr(endpoint, "status", ""))
        return {
            **self._base_payload(cfg),
            "name": str(getattr(endpoint, "name", "") or cfg.name),
            "url": str(getattr(endpoint, "url", "") or cfg.base_url),
            "status": status,
            "repository": str(getattr(endpoint, "repository", "") or ""),
            "namespace": str(getattr(endpoint, "namespace", "") or ""),
            "message": message,
        }

    def _error_payload(self, exc: Exception, cfg: HfEndpointConfig) -> dict[str, Any]:
        message = MANAGE_ENDPOINT_PERMISSION_HINT if _looks_permission_error(exc) else str(exc or "")
        return {
            **self._base_payload(cfg),
            "status": "error",
            "manageable": False if _looks_permission_error(exc) else bool(cfg.token and cfg.configured),
            "message": message,
        }


def is_cold_start_runtime_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return any(
        token in text
        for token in (
            "502",
            "503",
            "504",
            "bad gateway",
            "service unavailable",
            "gateway timeout",
            "temporarily unavailable",
            "server unavailable",
            "upstream",
            "no healthy upstream",
            "timed out",
            "timeout",
            "read timeout",
            "connection error",
            "connection reset",
            "server disconnected",
            "endpoint is starting",
            "initializing",
            "initialising",
            "not ready",
            "retry later",
            "currently loading",
            "loading",
        )
    )


def cold_start_sleep_seconds(attempt: int) -> float:
    schedule = (8.0, 15.0, 30.0, 45.0, 60.0, 60.0, 90.0, 120.0)
    idx = max(0, min(len(schedule) - 1, int(attempt)))
    return schedule[idx]
