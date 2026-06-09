from __future__ import annotations

from typing import Any


__all__ = [
    "merge_options",
    "IMAGE_BINDING_STATUS_CONFIRMED",
    "IMAGE_BINDING_STATUS_MANUAL_CONFIRMED",
    "IMAGE_BINDING_STATUS_NEEDS_REVIEW",
    "IMAGE_BINDING_STATUS_NONE",
    "IMAGE_BINDING_STATUSES",
    "ImageBinding",
    "image_binding_is_confirmed",
    "image_binding_preview_status",
    "normalize_item_text",
    "normalize_image_binding_status",
    "parse_structured_items",
    "extract_chat_text",
    "extract_formatted_item",
    "extract_reasoning_payload",
    "extract_reasoning_payload_loose",
    "sanitize_reasoning_payload",
]


def __getattr__(name: str) -> Any:
    if name == "merge_options":
        from .item_merger import merge_options

        return merge_options
    if name in {
        "IMAGE_BINDING_STATUS_CONFIRMED",
        "IMAGE_BINDING_STATUS_MANUAL_CONFIRMED",
        "IMAGE_BINDING_STATUS_NEEDS_REVIEW",
        "IMAGE_BINDING_STATUS_NONE",
        "IMAGE_BINDING_STATUSES",
        "ImageBinding",
        "image_binding_is_confirmed",
        "image_binding_preview_status",
        "normalize_image_binding_status",
    }:
        from .image_binding import (
            IMAGE_BINDING_STATUS_CONFIRMED,
            IMAGE_BINDING_STATUS_MANUAL_CONFIRMED,
            IMAGE_BINDING_STATUS_NEEDS_REVIEW,
            IMAGE_BINDING_STATUS_NONE,
            IMAGE_BINDING_STATUSES,
            ImageBinding,
            image_binding_is_confirmed,
            image_binding_preview_status,
            normalize_image_binding_status,
        )

        return {
            "IMAGE_BINDING_STATUS_CONFIRMED": IMAGE_BINDING_STATUS_CONFIRMED,
            "IMAGE_BINDING_STATUS_MANUAL_CONFIRMED": IMAGE_BINDING_STATUS_MANUAL_CONFIRMED,
            "IMAGE_BINDING_STATUS_NEEDS_REVIEW": IMAGE_BINDING_STATUS_NEEDS_REVIEW,
            "IMAGE_BINDING_STATUS_NONE": IMAGE_BINDING_STATUS_NONE,
            "IMAGE_BINDING_STATUSES": IMAGE_BINDING_STATUSES,
            "ImageBinding": ImageBinding,
            "image_binding_is_confirmed": image_binding_is_confirmed,
            "image_binding_preview_status": image_binding_preview_status,
            "normalize_image_binding_status": normalize_image_binding_status,
        }[name]
    if name == "normalize_item_text":
        from .item_normalizer import normalize_item_text

        return normalize_item_text
    if name == "parse_structured_items":
        from .item_parser import parse_structured_items

        return parse_structured_items
    if name in {
        "extract_chat_text",
        "extract_formatted_item",
        "extract_reasoning_payload",
        "extract_reasoning_payload_loose",
        "sanitize_reasoning_payload",
    }:
        from .model_output_parser import (
            extract_chat_text,
            extract_formatted_item,
            extract_reasoning_payload,
            extract_reasoning_payload_loose,
            sanitize_reasoning_payload,
        )

        return {
            "extract_chat_text": extract_chat_text,
            "extract_formatted_item": extract_formatted_item,
            "extract_reasoning_payload": extract_reasoning_payload,
            "extract_reasoning_payload_loose": extract_reasoning_payload_loose,
            "sanitize_reasoning_payload": sanitize_reasoning_payload,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
