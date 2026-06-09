from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping


IMAGE_BINDING_STATUS_NONE = "none"
IMAGE_BINDING_STATUS_NEEDS_REVIEW = "needs_review"
IMAGE_BINDING_STATUS_CONFIRMED = "confirmed"
IMAGE_BINDING_STATUS_MANUAL_CONFIRMED = "manual_confirmed"
IMAGE_BINDING_STATUSES = {
    IMAGE_BINDING_STATUS_NONE,
    IMAGE_BINDING_STATUS_NEEDS_REVIEW,
    IMAGE_BINDING_STATUS_CONFIRMED,
    IMAGE_BINDING_STATUS_MANUAL_CONFIRMED,
}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _clean_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _clean_text(value).lower() in {"1", "true", "si", "sí", "yes", "y"}


def _clean_int_list(raw: Any) -> List[int]:
    out: List[int] = []
    seen: set[int] = set()
    if not isinstance(raw, (list, tuple, set)):
        return out
    for value in raw:
        try:
            current = int(value)
        except Exception:
            continue
        if current in seen:
            continue
        seen.add(current)
        out.append(current)
    return out


def _clean_str_list(raw: Any) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    if isinstance(raw, str):
        raw_values = [part.strip() for part in raw.split(",")]
    elif isinstance(raw, (list, tuple, set)):
        raw_values = [str(value or "").strip() for value in raw]
    else:
        raw_values = []
    for value in raw_values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def normalize_image_binding_status(raw_status: Any) -> str:
    status = _clean_text(raw_status).lower()
    if status not in IMAGE_BINDING_STATUSES:
        return IMAGE_BINDING_STATUS_NONE
    return status


def image_binding_is_confirmed(binding: Mapping[str, Any] | None) -> bool:
    if not isinstance(binding, Mapping):
        return False
    status = normalize_image_binding_status(binding.get("status", ""))
    return status in {IMAGE_BINDING_STATUS_CONFIRMED, IMAGE_BINDING_STATUS_MANUAL_CONFIRMED}


def image_binding_preview_status(binding: Mapping[str, Any] | None) -> str:
    if not isinstance(binding, Mapping):
        return "sin_imagen"
    if image_binding_is_confirmed(binding):
        return "imagen_confirmada"
    if _clean_bool(binding.get("needs_review", False)):
        return "revision"
    status = normalize_image_binding_status(binding.get("status", ""))
    if status == IMAGE_BINDING_STATUS_NEEDS_REVIEW:
        return "revision"
    return "sin_imagen"


@dataclass
class ImageBinding:
    marker_name: str = ""
    segment_ids: List[int] = field(default_factory=list)
    crop_paths: List[str] = field(default_factory=list)
    status: str = IMAGE_BINDING_STATUS_NONE
    origin: str = ""
    marker_names: List[str] = field(default_factory=list)
    marker_paths: Dict[str, str] = field(default_factory=dict)
    slots: List[str] = field(default_factory=list)
    figure_hint_score: float = 0.0
    needs_review: bool = False

    @property
    def is_confirmed(self) -> bool:
        return self.status in {
            IMAGE_BINDING_STATUS_CONFIRMED,
            IMAGE_BINDING_STATUS_MANUAL_CONFIRMED,
        }

    @property
    def preview_status(self) -> str:
        return image_binding_preview_status(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "ImageBinding":
        data = raw if isinstance(raw, Mapping) else {}
        marker_names = _clean_str_list(data.get("marker_names", []))
        marker_name = _clean_text(data.get("marker_name", ""))
        if marker_name and marker_name not in marker_names:
            marker_names.insert(0, marker_name)
        marker_name = marker_names[0] if marker_names else marker_name

        crop_paths = _clean_str_list(data.get("crop_paths", []))
        marker_paths_raw = data.get("marker_paths", {})
        marker_paths: Dict[str, str] = {}
        if isinstance(marker_paths_raw, Mapping):
            for key, value in marker_paths_raw.items():
                clean_key = _clean_text(key)
                clean_value = _clean_text(value)
                if clean_key and clean_value:
                    marker_paths[clean_key] = clean_value
        for idx, marker in enumerate(marker_names):
            if marker in marker_paths:
                continue
            if idx < len(crop_paths):
                marker_paths[marker] = crop_paths[idx]
            elif len(crop_paths) == 1:
                marker_paths[marker] = crop_paths[0]

        try:
            hint_score = float(data.get("figure_hint_score", 0.0) or 0.0)
        except Exception:
            hint_score = 0.0

        status = normalize_image_binding_status(data.get("status", ""))
        needs_review = _clean_bool(data.get("needs_review", False))
        if status == IMAGE_BINDING_STATUS_NONE and needs_review:
            status = IMAGE_BINDING_STATUS_NEEDS_REVIEW

        return cls(
            marker_name=marker_name,
            segment_ids=_clean_int_list(data.get("segment_ids", [])),
            crop_paths=crop_paths,
            status=status,
            origin=_clean_text(data.get("origin", "")),
            marker_names=marker_names,
            marker_paths=marker_paths,
            slots=_clean_str_list(data.get("slots", [])),
            figure_hint_score=max(0.0, hint_score),
            needs_review=needs_review or status == IMAGE_BINDING_STATUS_NEEDS_REVIEW,
        )

    def to_dict(self) -> Dict[str, Any]:
        marker_names = _clean_str_list(self.marker_names or [])
        if self.marker_name and self.marker_name not in marker_names:
            marker_names.insert(0, self.marker_name)
        marker_name = marker_names[0] if marker_names else _clean_text(self.marker_name)
        crop_paths = _clean_str_list(self.crop_paths or [])
        marker_paths: Dict[str, str] = {}
        for key, value in dict(self.marker_paths or {}).items():
            clean_key = _clean_text(key)
            clean_value = _clean_text(value)
            if clean_key and clean_value:
                marker_paths[clean_key] = clean_value
        for idx, marker in enumerate(marker_names):
            if marker in marker_paths:
                continue
            if idx < len(crop_paths):
                marker_paths[marker] = crop_paths[idx]
            elif len(crop_paths) == 1:
                marker_paths[marker] = crop_paths[0]
        status = normalize_image_binding_status(self.status)
        needs_review = bool(self.needs_review or status == IMAGE_BINDING_STATUS_NEEDS_REVIEW)
        if status == IMAGE_BINDING_STATUS_NONE and needs_review:
            status = IMAGE_BINDING_STATUS_NEEDS_REVIEW
        return {
            "marker_name": marker_name,
            "segment_ids": _clean_int_list(self.segment_ids or []),
            "crop_paths": crop_paths,
            "status": status,
            "origin": _clean_text(self.origin),
            "marker_names": marker_names,
            "marker_paths": marker_paths,
            "slots": _clean_str_list(self.slots or []),
            "figure_hint_score": max(0.0, float(self.figure_hint_score or 0.0)),
            "needs_review": needs_review,
        }
