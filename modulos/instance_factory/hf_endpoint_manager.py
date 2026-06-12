from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from .runtime_env import load_factory_runtime_env


MANAGE_ENDPOINT_PERMISSION_HINT = (
    "El HF_TOKEN no tiene permisos para administrar endpoints. "
    "Activa el permiso 'Manage your Inference Endpoints' en Hugging Face."
)


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

    def _ensure_can_manage(self, cfg: HfEndpointConfig) -> None:
        if not cfg.configured:
            raise RuntimeError("Configura HF_TRAINED_OCR_ENDPOINT_NAME o HF_TRAINED_OCR_BASE_URL.")
        if not cfg.token:
            raise RuntimeError("Falta HF_TOKEN para administrar el endpoint OCR.")

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
