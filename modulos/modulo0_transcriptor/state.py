from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from .domain.image_binding import ImageBinding


@dataclass
class SegmentState:
    idx: int
    bbox_px: List[int] = field(default_factory=list)
    image_path: str = ""
    source_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "idx": int(self.idx),
            "bbox_px": [int(v) for v in self.bbox_px],
            "image_path": str(self.image_path),
            "source_path": str(self.source_path),
        }


@dataclass
class SourceImageState:
    label: str
    path: str
    source_key: str = ""
    reviewed: bool = False
    preview_markers: Dict[str, str] = field(default_factory=dict)
    figure_boxes: List[Dict[str, Any]] = field(default_factory=list)
    segment_detector_audit: Dict[str, Any] = field(default_factory=dict)
    ocr_exclusion_box: List[int] = field(default_factory=list)
    segments: List[SegmentState] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": str(self.label),
            "path": str(self.path),
            "source_key": str(self.source_key),
            "reviewed": bool(self.reviewed),
            "preview_markers": {str(k): str(v) for k, v in self.preview_markers.items()},
            "figure_boxes": [dict(v) for v in self.figure_boxes],
            "segment_detector_audit": dict(self.segment_detector_audit),
            "ocr_exclusion_box": [int(v) for v in self.ocr_exclusion_box],
            "segments": [seg.to_dict() for seg in self.segments],
        }


@dataclass
class ItemState:
    archivo_origen: str
    item_text: str
    image_paths: List[str] = field(default_factory=list)
    corrected: bool = False
    image_binding: ImageBinding = field(default_factory=ImageBinding)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "archivo_origen": str(self.archivo_origen),
            "item_text": str(self.item_text),
            "image_paths": [str(v) for v in self.image_paths],
            "corrected": bool(self.corrected),
            "image_binding": self.image_binding.to_dict(),
        }


@dataclass
class UsageState:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_usd: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_tokens": int(self.input_tokens),
            "output_tokens": int(self.output_tokens),
            "total_tokens": int(self.total_tokens),
            "estimated_usd": float(self.estimated_usd),
        }


@dataclass
class ReviewState:
    reviewed_sources: List[str] = field(default_factory=list)
    review_done: bool = False
    route_active: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reviewed_sources": [str(v) for v in self.reviewed_sources],
            "review_done": bool(self.review_done),
            "route_active": bool(self.route_active),
        }


@dataclass
class TranscriptorSessionState:
    session_schema_version: int = 4
    project_name: str = "Proyecto"
    ui_settings: Dict[str, Any] = field(default_factory=dict)
    source_images: List[SourceImageState] = field(default_factory=list)
    items: List[ItemState] = field(default_factory=list)
    output_text: str = ""
    usage: UsageState = field(default_factory=UsageState)
    review: ReviewState = field(default_factory=ReviewState)
    logs: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def clear_runtime(self) -> None:
        self.source_images.clear()
        self.items.clear()
        self.output_text = ""
        self.logs.clear()
        self.metadata.clear()
        self.usage = UsageState()
        self.review = ReviewState()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_schema_version": int(self.session_schema_version),
            "project_name": str(self.project_name),
            "ui_settings": dict(self.ui_settings),
            "source_images": [img.to_dict() for img in self.source_images],
            "items": [item.to_dict() for item in self.items],
            "output_text": str(self.output_text),
            "usage": self.usage.to_dict(),
            "review": self.review.to_dict(),
            "logs": [str(v) for v in self.logs],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "TranscriptorSessionState":
        data = payload if isinstance(payload, dict) else {}
        usage_raw = data.get("usage", {}) if isinstance(data.get("usage"), dict) else {}
        review_raw = data.get("review", {}) if isinstance(data.get("review"), dict) else {}
        source_images: List[SourceImageState] = []
        for raw_img in data.get("source_images", []) or []:
            if not isinstance(raw_img, dict):
                continue
            segments: List[SegmentState] = []
            for raw_seg in raw_img.get("segments", []) or []:
                if not isinstance(raw_seg, dict):
                    continue
                segments.append(
                    SegmentState(
                        idx=int(raw_seg.get("idx", 0) or 0),
                        bbox_px=[int(v) for v in (raw_seg.get("bbox_px", []) or [])],
                        image_path=str(raw_seg.get("image_path", "") or ""),
                        source_path=str(raw_seg.get("source_path", "") or ""),
                    )
                )
            source_images.append(
                SourceImageState(
                    label=str(raw_img.get("label", "") or ""),
                    path=str(raw_img.get("path", "") or ""),
                    source_key=str(raw_img.get("source_key", "") or ""),
                    reviewed=bool(raw_img.get("reviewed", False)),
                    preview_markers={
                        str(k): str(v) for k, v in (raw_img.get("preview_markers", {}) or {}).items()
                    },
                    figure_boxes=[dict(v) for v in (raw_img.get("figure_boxes", []) or []) if isinstance(v, dict)],
                    segment_detector_audit=dict(raw_img.get("segment_detector_audit", {}) or {}),
                    ocr_exclusion_box=[int(v) for v in (raw_img.get("ocr_exclusion_box", []) or [])],
                    segments=segments,
                )
            )
        items: List[ItemState] = []
        for raw_item in data.get("items", []) or []:
            if not isinstance(raw_item, dict):
                continue
            items.append(
                ItemState(
                    archivo_origen=str(raw_item.get("archivo_origen", "") or ""),
                    item_text=str(raw_item.get("item_text", "") or ""),
                    image_paths=[str(v) for v in (raw_item.get("image_paths", []) or [])],
                    corrected=bool(raw_item.get("corrected", False)),
                    image_binding=ImageBinding.from_dict(raw_item.get("image_binding", {})),
                )
            )
        return cls(
            session_schema_version=int(data.get("session_schema_version", 3) or 3),
            project_name=str(data.get("project_name", "Proyecto") or "Proyecto"),
            ui_settings=dict(data.get("ui_settings", {}) or {}),
            source_images=source_images,
            items=items,
            output_text=str(data.get("output_text", "") or ""),
            usage=UsageState(
                input_tokens=int(usage_raw.get("input_tokens", 0) or 0),
                output_tokens=int(usage_raw.get("output_tokens", 0) or 0),
                total_tokens=int(usage_raw.get("total_tokens", 0) or 0),
                estimated_usd=float(usage_raw.get("estimated_usd", 0.0) or 0.0),
            ),
            review=ReviewState(
                reviewed_sources=[str(v) for v in (review_raw.get("reviewed_sources", []) or [])],
                review_done=bool(review_raw.get("review_done", False)),
                route_active=bool(review_raw.get("route_active", False)),
            ),
            logs=[str(v) for v in (data.get("logs", []) or [])],
            metadata=dict(data.get("metadata", {}) or {}),
        )
