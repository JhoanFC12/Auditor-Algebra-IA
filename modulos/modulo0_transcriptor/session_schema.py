from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from utils.project_layout import infer_workspace_from_session_path, normalize_instance_name, project_dirs, remap_legacy_drive_path

ITEM_NUM_RE = re.compile(r"\\item\s*\[\s*\\textbf\s*\{\s*(\d+)\s*\.?\s*\}\s*\]", re.IGNORECASE)
BRACKET_TAG_RE = re.compile(r"\[\[\s*([^\]]+?)\s*\]\]")
MARKER_VALUE_RE = re.compile(r"^(?P<base>.+?)[-_](?P<num>\d+)(?:[-_](?P<opt>[A-Za-z0-9]+))?$", re.IGNORECASE)
TAG_CURSO_RE = re.compile(r"\[\[\s*curso\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)
TAG_TEMA_RE = re.compile(r"\[\[\s*tema\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)
TAG_SUBTEMA_RE = re.compile(r"\[\[\s*subtema\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)
TAG_CLAVE_RE = re.compile(r"\[\[\s*clave\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)
SEGMENT_FILE_RE = re.compile(r"_seg_(\d+)$", re.IGNORECASE)


def enrich_session_payload_with_structure(payload: Dict[str, Any], *, session_path: Path) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return payload

    source_entries = _get_source_entries(payload)
    instance_type = _infer_instance_type(payload, session_path)
    source_records, source_index = _build_source_records(
        source_entries=source_entries,
        payload=payload,
        session_path=session_path,
        instance_type=instance_type,
    )
    segment_records, source_segments, source_preview_markers = _build_segment_records(
        payload=payload,
        session_path=session_path,
        instance_type=instance_type,
        source_records=source_records,
        source_index=source_index,
    )
    problem_records = _build_problem_records(
        payload=payload,
        session_path=session_path,
        source_records=source_records,
        segment_records=segment_records,
    )

    for record in source_records:
        source_key = str(record.get("source_key", "") or "")
        segs = source_segments.get(source_key, [])
        record["segment_count"] = len(segs)
        record["segments_dir"] = str(record.get("segments_dir", "") or "")
        record["used_segment_indexes"] = [int(seg["segment_index"]) for seg in segs if bool(seg.get("used"))]
        record["bound_segment_indexes"] = [
            int(seg["segment_index"]) for seg in segs if isinstance(seg.get("binding"), dict)
        ]
        record["preview_markers"] = dict(source_preview_markers.get(source_key, {}))

    payload["structured_session_version"] = 1
    payload["sources"] = source_records
    payload["segments"] = segment_records
    payload["problems"] = problem_records

    state_v3 = payload.get("state_v3", {})
    if isinstance(state_v3, dict):
        metadata = state_v3.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["structured_session_version"] = 1
        metadata["structured_counts"] = {
            "sources": len(source_records),
            "segments": len(segment_records),
            "problems": len(problem_records),
        }
        state_v3["metadata"] = metadata
        state_v3["source_images"] = [_build_state_source_image(record, source_segments) for record in source_records]
        payload["state_v3"] = state_v3
    return payload


def _build_state_source_image(
    record: Dict[str, Any],
    source_segments: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    source_key = str(record.get("source_key", "") or "")
    return {
        "label": str(record.get("label", "") or ""),
        "path": str(record.get("source_path", "") or ""),
        "source_key": source_key,
        "reviewed": bool(record.get("reviewed")),
        "preview_markers": dict(record.get("preview_markers", {}) or {}),
        "figure_boxes": list(record.get("figure_boxes", []) or []),
        "segment_detector_audit": dict(record.get("segment_detector_audit", {}) or {}),
        "ocr_exclusion_box": list(record.get("ocr_exclusion_box", []) or []),
        "segments": [
            {
                "idx": int(seg.get("segment_index", 0) or 0),
                "bbox_px": [int(v) for v in (seg.get("bbox_px", []) or [])],
                "image_path": str(seg.get("segment_path", "") or ""),
                "source_path": str(record.get("source_path", "") or ""),
            }
            for seg in source_segments.get(source_key, [])
        ],
    }


def _build_problem_records(
    *,
    payload: Dict[str, Any],
    session_path: Path,
    source_records: List[Dict[str, Any]],
    segment_records: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    items_data = payload.get("items", [])
    if not isinstance(items_data, list) and isinstance(payload.get("state_v3"), dict):
        items_data = payload["state_v3"].get("items", [])
    if not isinstance(items_data, list):
        items_data = []

    preview_images_raw = payload.get("preview_images", {})
    preview_images = preview_images_raw if isinstance(preview_images_raw, dict) else {}
    preview_map: Dict[str, str] = {}
    for marker_name, raw_path in preview_images.items():
        clean_marker = str(marker_name or "").strip()
        if not clean_marker:
            continue
        preview_map[clean_marker] = _portable_resource_path(
            _resolve_resource_path(str(raw_path or "").strip(), session_path=session_path, bucket="crops"),
            session_path=session_path,
            fallback=str(raw_path or "").strip(),
        )

    binding_by_marker: Dict[str, Dict[str, Any]] = {}
    for segment in segment_records:
        binding = segment.get("binding")
        if not isinstance(binding, dict):
            continue
        marker_name = str(binding.get("marker_name", "") or "").strip()
        if marker_name:
            binding_by_marker[marker_name] = {
                "source_key": str(segment.get("source_key", "") or ""),
                "segment_index": int(segment.get("segment_index", 0) or 0),
                "slot": str(binding.get("slot", "ENUNCIADO") or "ENUNCIADO"),
                "crop_path": str(binding.get("crop_path", "") or ""),
                "confirmed": bool(binding.get("confirmed", False)),
            }

    source_by_key = {str(row.get("source_key", "") or ""): row for row in source_records}
    solution_paths_raw = payload.get("solution_paths_by_item", {})
    if not isinstance(solution_paths_raw, dict):
        solution_paths_raw = {}

    problems: List[Dict[str, Any]] = []
    for idx, raw_item in enumerate(items_data, start=1):
        if not isinstance(raw_item, dict):
            continue
        item_text = str(raw_item.get("item", raw_item.get("item_text", "")) or "").strip()
        archivo_origen = str(raw_item.get("archivo_origen", "") or "").strip()
        image_paths_raw = raw_item.get("imagenes", raw_item.get("image_paths", []))
        image_paths: List[str] = []
        if isinstance(image_paths_raw, (list, tuple)):
            for raw_path in image_paths_raw:
                clean = str(raw_path or "").strip()
                if not clean:
                    continue
                image_paths.append(
                    _portable_resource_path(
                        _resolve_resource_path(clean, session_path=session_path, bucket="crops"),
                        session_path=session_path,
                        fallback=clean,
                    )
                )

        problem_number = _parse_problem_number(item_text)
        markers = _extract_image_marker_names(item_text)
        figures: List[Dict[str, Any]] = []
        used_image_paths: Set[str] = set()
        for marker_name in markers:
            binding = dict(binding_by_marker.get(marker_name, {}))
            crop_path = str(preview_map.get(marker_name) or binding.get("crop_path", "") or "").strip()
            if not crop_path:
                for candidate in image_paths:
                    stem = Path(str(candidate)).stem.strip().lower()
                    if stem == marker_name.strip().lower():
                        crop_path = candidate
                        break
            if crop_path:
                used_image_paths.add(crop_path)
            source_key = str(binding.get("source_key", "") or "").strip()
            source_record = source_by_key.get(source_key, {})
            figures.append(
                {
                    "marker_name": marker_name,
                    "crop_path": crop_path,
                    "source_key": source_key,
                    "source_stem": str(source_record.get("source_stem", "") or ""),
                    "segment_index": binding.get("segment_index", None),
                    "slot": str(binding.get("slot", "ENUNCIADO") or "ENUNCIADO"),
                    "confirmed": bool(binding.get("confirmed", False)),
                }
            )

        dangling_paths = [p for p in image_paths if p and p not in used_image_paths]
        solutions = _normalize_solution_groups(solution_paths_raw.get(str(problem_number or 0), []))
        problems.append(
            {
                "problem_index": idx,
                "problem_number": problem_number,
                "archivo_origen": archivo_origen,
                "item_text": item_text,
                "curso": _first_tag(TAG_CURSO_RE, item_text),
                "tema": _first_tag(TAG_TEMA_RE, item_text),
                "subtema": _first_tag(TAG_SUBTEMA_RE, item_text),
                "key": _first_tag(TAG_CLAVE_RE, item_text),
                "image_markers": markers,
                "image_paths": image_paths,
                "figures": figures,
                "unlinked_image_paths": dangling_paths,
                "solutions": [
                    {
                        "solution_index": pos,
                        "images": [
                            _portable_resource_path(
                                _resolve_resource_path(path, session_path=session_path, bucket="solutions"),
                                session_path=session_path,
                                fallback=path,
                            )
                            for path in group
                        ],
                    }
                    for pos, group in enumerate(solutions, start=1)
                ],
                "has_figures": bool(figures or image_paths),
                "has_key": bool(_first_tag(TAG_CLAVE_RE, item_text)),
                "has_solutions": bool(solutions),
            }
        )
    return problems


def _build_segment_records(
    *,
    payload: Dict[str, Any],
    session_path: Path,
    instance_type: str,
    source_records: List[Dict[str, Any]],
    source_index: Dict[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]], Dict[str, Dict[str, str]]]:
    segmentation = payload.get("segmentation", {})
    if not isinstance(segmentation, dict):
        segmentation = {}
    overrides_raw = segmentation.get("overrides", {})
    used_raw = segmentation.get("used_segments", {})
    bindings_raw = payload.get("segment_item_bindings_by_source", {})
    figure_boxes_raw = payload.get("figure_boxes_by_source", {})
    segment_detector_audit_raw = payload.get("segment_detection_audit_by_source", {})
    ocr_boxes_raw = payload.get("ocr_exclusion_boxes", {})

    source_overrides: Dict[str, List[List[int]]] = {}
    if isinstance(overrides_raw, dict):
        for raw_key, raw_boxes in overrides_raw.items():
            source_key = _resolve_source_key(str(raw_key or ""), source_index)
            if not source_key:
                continue
            source_overrides[source_key] = _normalize_box_list(raw_boxes)

    source_used: Dict[str, Set[int]] = {}
    if isinstance(used_raw, dict):
        for raw_key, raw_indexes in used_raw.items():
            source_key = _resolve_source_key(str(raw_key or ""), source_index)
            if not source_key:
                continue
            bucket: Set[int] = set()
            if isinstance(raw_indexes, (list, tuple, set)):
                for raw_idx in raw_indexes:
                    try:
                        iv = int(raw_idx)
                    except Exception:
                        continue
                    if iv >= 0:
                        bucket.add(iv)
            source_used[source_key] = bucket

    source_bindings: Dict[str, Dict[int, Dict[str, Any]]] = {}
    if isinstance(bindings_raw, dict):
        for raw_key, raw_bucket in bindings_raw.items():
            source_key = _resolve_source_key(str(raw_key or ""), source_index)
            if not source_key or not isinstance(raw_bucket, dict):
                continue
            bucket: Dict[int, Dict[str, Any]] = {}
            for raw_seg_idx, raw_payload in raw_bucket.items():
                if not isinstance(raw_payload, dict):
                    continue
                try:
                    seg_idx = int(raw_seg_idx)
                except Exception:
                    continue
                crop_path = str(raw_payload.get("crop_path", "") or "").strip()
                bucket[seg_idx] = {
                    "item_num": int(raw_payload.get("item_num", 0) or 0),
                    "slot": str(raw_payload.get("slot", "ENUNCIADO") or "ENUNCIADO"),
                    "marker_name": str(raw_payload.get("marker_name", "") or "").strip(),
                    "crop_path": _portable_resource_path(
                        _resolve_resource_path(crop_path, session_path=session_path, bucket="crops"),
                        session_path=session_path,
                        fallback=crop_path,
                    ),
                    "confirmed": bool(raw_payload.get("confirmed", False)),
                    "updated_at": str(raw_payload.get("updated_at", "") or "").strip(),
                }
            if bucket:
                source_bindings[source_key] = bucket

    figure_boxes: Dict[str, List[Dict[str, Any]]] = {}
    if isinstance(figure_boxes_raw, dict):
        for raw_key, raw_entries in figure_boxes_raw.items():
            source_key = _resolve_source_key(str(raw_key or ""), source_index)
            if not source_key or not isinstance(raw_entries, list):
                continue
            figure_boxes[source_key] = [dict(entry) for entry in raw_entries if isinstance(entry, dict)]

    detector_audit_by_source: Dict[str, Dict[str, Any]] = {}
    if isinstance(segment_detector_audit_raw, dict):
        for raw_key, raw_entry in segment_detector_audit_raw.items():
            source_key = _resolve_source_key(str(raw_key or ""), source_index)
            if not source_key or not isinstance(raw_entry, dict):
                continue
            detector_audit_by_source[source_key] = dict(raw_entry)

    ocr_boxes: Dict[str, List[int]] = {}
    if isinstance(ocr_boxes_raw, dict):
        for raw_key, raw_box in ocr_boxes_raw.items():
            source_key = _resolve_source_key(str(raw_key or ""), source_index)
            if not source_key:
                continue
            normalized = _normalize_box(raw_box)
            if normalized:
                ocr_boxes[source_key] = normalized

    records: List[Dict[str, Any]] = []
    by_source: Dict[str, List[Dict[str, Any]]] = {str(row.get("source_key", "") or ""): [] for row in source_records}
    preview_markers_by_source: Dict[str, Dict[str, str]] = {
        str(row.get("source_key", "") or ""): {} for row in source_records
    }

    for record in source_records:
        source_key = str(record.get("source_key", "") or "")
        source_path = str(record.get("source_path", "") or "")
        actual_source_path = _resolve_resource_path(source_path, session_path=session_path, bucket="sources")
        record["reviewed"] = bool(record.get("reviewed")) or any(
            alias in set(record.get("_reviewed_keys", []))
            for alias in _source_aliases(
                label=str(record.get("label", "") or ""),
                resolved_path=actual_source_path,
            )
        )
        record.pop("_reviewed_keys", None)
        if source_key in figure_boxes:
            record["figure_boxes"] = figure_boxes[source_key]
        if source_key in detector_audit_by_source:
            record["segment_detector_audit"] = dict(detector_audit_by_source[source_key])
        if source_key in ocr_boxes:
            record["ocr_exclusion_box"] = ocr_boxes[source_key]
        segment_dir_path = _resolve_source_segments_dir(
            actual_source_path=actual_source_path,
            label=str(record.get("label", "") or ""),
            session_path=session_path,
            instance_type=instance_type,
        )
        record["segments_dir"] = _portable_resource_path(
            segment_dir_path,
            session_path=session_path,
            fallback=str(record.get("segments_dir", "") or ""),
        )
        disk_segments = _collect_segment_files(segment_dir_path)
        state_segments = _collect_state_segments(payload, source_key, session_path=session_path)
        index_pool: Set[int] = set(disk_segments.keys())
        index_pool.update(state_segments.keys())
        index_pool.update(range(len(source_overrides.get(source_key, []))))
        index_pool.update(source_used.get(source_key, set()))
        index_pool.update(source_bindings.get(source_key, {}).keys())
        for seg_idx in sorted(index_pool):
            disk_entry = disk_segments.get(seg_idx, {})
            state_entry = state_segments.get(seg_idx, {})
            binding = dict(source_bindings.get(source_key, {}).get(seg_idx, {}))
            segment_path = str(disk_entry.get("segment_path") or state_entry.get("segment_path") or "").strip()
            if segment_path:
                segment_path = _portable_resource_path(
                    _resolve_resource_path(segment_path, session_path=session_path, bucket="segments"),
                    session_path=session_path,
                    fallback=segment_path,
                )
            bbox_px = list(state_entry.get("bbox_px", []) or [])
            if not bbox_px:
                overrides = source_overrides.get(source_key, [])
                if 0 <= seg_idx < len(overrides):
                    bbox_px = list(overrides[seg_idx])
            used = bool(seg_idx in source_used.get(source_key, set()) or binding)
            segment_record = {
                "source_key": source_key,
                "source_stem": str(record.get("source_stem", "") or ""),
                "source_path": source_path,
                "segment_index": int(seg_idx),
                "segment_label": f"seg-{seg_idx + 1}",
                "segment_path": segment_path,
                "bbox_px": [int(v) for v in bbox_px] if bbox_px else [],
                "used": used,
                "binding": binding or None,
            }
            records.append(segment_record)
            by_source.setdefault(source_key, []).append(segment_record)
            if binding and binding.get("marker_name") and binding.get("crop_path"):
                preview_markers_by_source.setdefault(source_key, {})[str(binding["marker_name"])] = str(
                    binding["crop_path"]
                )

    return records, by_source, preview_markers_by_source


def _build_source_records(
    *,
    source_entries: List[Dict[str, str]],
    payload: Dict[str, Any],
    session_path: Path,
    instance_type: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    reviewed_keys = set()
    segmentation = payload.get("segmentation", {})
    if isinstance(segmentation, dict):
        raw_reviewed = segmentation.get("reviewed_sources", [])
        if isinstance(raw_reviewed, list):
            reviewed_keys = {str(v or "").strip().lower() for v in raw_reviewed if str(v or "").strip()}

    records: List[Dict[str, Any]] = []
    source_index: Dict[str, Dict[str, Any]] = {}
    for entry in source_entries:
        label = str(entry.get("label", "") or "").strip()
        raw_path = str(entry.get("path", "") or "").strip()
        if not label or not raw_path:
            continue
        resolved = _resolve_resource_path(raw_path, session_path=session_path, bucket="sources")
        portable_path = _portable_resource_path(resolved, session_path=session_path, fallback=raw_path)
        resolved_stem = resolved.stem if resolved.name else Path(label).stem
        source_key = label
        source_record = {
            "source_key": source_key,
            "label": label,
            "source_path": portable_path,
            "source_stem": resolved_stem or Path(label).stem,
            "reviewed": False,
            "_reviewed_keys": list(reviewed_keys),
            "segments_dir": _portable_resource_path(
                _resolve_source_segments_dir(
                    actual_source_path=resolved,
                    label=label,
                    session_path=session_path,
                    instance_type=instance_type,
                ),
                session_path=session_path,
                fallback="",
            ),
            "segment_count": 0,
            "used_segment_indexes": [],
            "bound_segment_indexes": [],
            "preview_markers": {},
            "figure_boxes": [],
            "segment_detector_audit": {},
            "ocr_exclusion_box": [],
        }
        records.append(source_record)
        for alias in _source_aliases(label=label, resolved_path=resolved):
            source_index[alias] = source_record
    return records, source_index


def _collect_state_segments(
    payload: Dict[str, Any],
    source_key: str,
    *,
    session_path: Path,
) -> Dict[int, Dict[str, Any]]:
    state_v3 = payload.get("state_v3", {})
    if not isinstance(state_v3, dict):
        return {}
    source_images = state_v3.get("source_images", [])
    if not isinstance(source_images, list):
        return {}
    for raw_image in source_images:
        if not isinstance(raw_image, dict):
            continue
        candidate_key = str(raw_image.get("source_key", raw_image.get("label", "")) or "").strip()
        if candidate_key != source_key:
            continue
        segments = raw_image.get("segments", [])
        if not isinstance(segments, list):
            return {}
        out: Dict[int, Dict[str, Any]] = {}
        for raw_segment in segments:
            if not isinstance(raw_segment, dict):
                continue
            try:
                idx = int(raw_segment.get("idx", 0) or 0)
            except Exception:
                continue
            image_path = str(raw_segment.get("image_path", "") or "").strip()
            out[idx] = {
                "bbox_px": [int(v) for v in (raw_segment.get("bbox_px", []) or []) if str(v).strip()],
                "segment_path": _portable_resource_path(
                    _resolve_resource_path(image_path, session_path=session_path, bucket="segments"),
                    session_path=session_path,
                    fallback=image_path,
                ),
            }
        return out
    return {}


def _get_source_entries(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    files = payload.get("files", [])
    if isinstance(files, list) and files:
        return [dict(row) for row in files if isinstance(row, dict)]
    state_v3 = payload.get("state_v3", {})
    if not isinstance(state_v3, dict):
        return []
    source_images = state_v3.get("source_images", [])
    if not isinstance(source_images, list):
        return []
    out: List[Dict[str, str]] = []
    for row in source_images:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label", "") or "").strip()
        path = str(row.get("path", "") or "").strip()
        if label and path:
            out.append({"label": label, "path": path})
    return out


def _infer_instance_type(payload: Dict[str, Any], session_path: Path) -> str:
    candidates: List[str] = []
    for raw in (
        payload.get("instance_type"),
        (payload.get("ui") or {}).get("instance_type") if isinstance(payload.get("ui"), dict) else "",
        (payload.get("state_v3") or {}).get("ui_settings", {}).get("instance_type")
        if isinstance((payload.get("state_v3") or {}).get("ui_settings"), dict)
        else "",
    ):
        clean = str(raw or "").strip().lower()
        if clean:
            candidates.append(clean)
    lowered_name = session_path.stem.strip().lower()
    if "resuelt" in lowered_name:
        candidates.append("resueltos")
    if "propuest" in lowered_name:
        candidates.append("propuestos")
    for candidate in candidates:
        clean_candidate = normalize_instance_name(candidate, "sesion")
        if clean_candidate:
            return clean_candidate
    return "sesion"


def _normalize_solution_groups(raw_value: Any) -> List[List[str]]:
    def _normalize_group(group_value: Any) -> List[str]:
        clean_group: List[str] = []
        if isinstance(group_value, dict):
            group_value = group_value.get("images") if "images" in group_value else group_value.get("paths")
        if isinstance(group_value, (list, tuple, set)):
            iterable = list(group_value)
        else:
            iterable = [group_value]
        for raw in iterable:
            clean = str(raw or "").strip()
            if clean and clean not in clean_group:
                clean_group.append(clean)
        return clean_group

    if raw_value is None:
        return []
    if isinstance(raw_value, dict):
        group = _normalize_group(raw_value)
        return [group] if group else []
    if not isinstance(raw_value, (list, tuple, set)):
        group = _normalize_group(raw_value)
        return [group] if group else []
    raw_list = list(raw_value)
    if not raw_list:
        return []
    contains_nested = any(isinstance(v, (list, tuple, set, dict)) for v in raw_list)
    if not contains_nested:
        group = _normalize_group(raw_list)
        return [group] if group else []
    normalized: List[List[str]] = []
    for raw_group in raw_list:
        clean_group = _normalize_group(raw_group)
        if clean_group:
            normalized.append(clean_group)
    return normalized


def _parse_problem_number(item_text: str) -> Optional[int]:
    match = ITEM_NUM_RE.search(item_text or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _first_tag(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text or "")
    return str(match.group(1) if match else "").strip()


def _extract_image_marker_names(item_text: str) -> List[str]:
    names: List[str] = []
    for match in BRACKET_TAG_RE.finditer(item_text or ""):
        token = (match.group(1) or "").strip()
        if not token:
            continue
        name = ""
        if "=" in token:
            key, value = token.split("=", 1)
            if key.strip().lower() == "imagen":
                name = (value or "").strip()
        else:
            parsed = MARKER_VALUE_RE.match(token)
            if parsed:
                base = (parsed.group("base") or "").strip()
                num = (parsed.group("num") or "").strip()
                opt = (parsed.group("opt") or "").strip()
                if base and num:
                    name = f"{base}-{num}{('-' + opt) if opt else ''}"
        if name and name not in names:
            names.append(name)
    return names


def _collect_segment_files(segment_dir: Path) -> Dict[int, Dict[str, str]]:
    if not segment_dir.exists() or not segment_dir.is_dir():
        return {}
    candidates = sorted(
        [
            path
            for path in segment_dir.iterdir()
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
        ],
        key=lambda path: path.name.lower(),
    )
    out: Dict[int, Dict[str, str]] = {}
    next_idx = 0
    for path in candidates:
        idx = _segment_index_from_name(path.stem)
        if idx is None:
            while next_idx in out:
                next_idx += 1
            idx = next_idx
            next_idx += 1
        out[idx] = {"segment_path": str(path)}
    return out


def _segment_index_from_name(stem: str) -> Optional[int]:
    match = SEGMENT_FILE_RE.search(stem or "")
    if not match:
        return None
    try:
        return max(0, int(match.group(1)) - 1)
    except Exception:
        return None


def _normalize_box_list(raw_boxes: Any) -> List[List[int]]:
    if not isinstance(raw_boxes, list):
        return []
    out: List[List[int]] = []
    for row in raw_boxes:
        normalized = _normalize_box(row)
        if normalized:
            out.append(normalized)
    return out


def _normalize_box(raw_box: Any) -> List[int]:
    if not isinstance(raw_box, (list, tuple)) or len(raw_box) != 4:
        return []
    try:
        return [int(v) for v in raw_box]
    except Exception:
        return []


def _resolve_source_segments_dir(
    *,
    actual_source_path: Path,
    label: str,
    session_path: Path,
    instance_type: str,
) -> Path:
    project_root = infer_workspace_from_session_path(session_path)
    if project_root is None:
        return Path("")
    layout = project_dirs(project_root, normalize_instance_name(instance_type))
    segments_root = layout["segments_dir"]
    stem_candidates = [
        actual_source_path.stem,
        Path(label).stem,
        _strip_numeric_suffix(actual_source_path.stem),
        _strip_numeric_suffix(Path(label).stem),
    ]
    seen: Set[str] = set()
    for stem in stem_candidates:
        clean = str(stem or "").strip()
        if not clean:
            continue
        low = clean.lower()
        if low in seen:
            continue
        seen.add(low)
        candidate = segments_root / clean
        if candidate.exists():
            return candidate
    return segments_root / (actual_source_path.stem or Path(label).stem)


def _resolve_source_key(raw_key: str, source_index: Dict[str, Dict[str, Any]]) -> str:
    key = str(raw_key or "").strip()
    if not key:
        return ""
    aliases = _aliases_for_lookup(key)
    for alias in aliases:
        record = source_index.get(alias)
        if record is not None:
            return str(record.get("source_key", "") or "")
    return ""


def _aliases_for_lookup(raw_value: str) -> Set[str]:
    value = str(raw_value or "").strip()
    if not value:
        return set()
    candidates: Set[str] = set()
    candidates.add(value.lower())
    path = Path(value)
    candidates.add(path.name.lower())
    candidates.add(path.stem.lower())
    stripped = _strip_numeric_suffix(path.stem)
    if stripped:
        candidates.add(stripped.lower())
        candidates.add(f"{stripped}{path.suffix.lower()}")
    return {candidate for candidate in candidates if candidate}


def _source_aliases(*, label: str, resolved_path: Path) -> Set[str]:
    aliases = _aliases_for_lookup(label)
    aliases.update(_aliases_for_lookup(str(resolved_path)))
    return aliases


def _strip_numeric_suffix(stem: str) -> str:
    text = str(stem or "").strip()
    if not text:
        return ""
    return re.sub(r"_(\d+)$", "", text)


def _portable_resource_path(path: Path, *, session_path: Path, fallback: str) -> str:
    raw_fallback = str(fallback or "").strip()
    if not str(path or "").strip():
        return raw_fallback
    try:
        candidate = path.resolve()
    except Exception:
        candidate = Path(path)
    try:
        relative = os.path.relpath(str(candidate), start=str(session_path.parent))
        return os.path.normpath(relative)
    except Exception:
        return os.path.normpath(str(candidate)) if str(candidate).strip() else raw_fallback


def _resolve_resource_path(raw_path: str, *, session_path: Path, bucket: str) -> Path:
    value = str(raw_path or "").strip()
    if not value:
        return Path("")
    session_path = remap_legacy_drive_path(session_path, prefer_existing=True)
    p = remap_legacy_drive_path(Path(os.path.normpath(value)), prefer_existing=True)
    candidates: List[Path] = []
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.append(Path(os.path.normpath(str(session_path.parent / p))))
        candidates.append(p)
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate.resolve()
        except Exception:
            continue

    project_root = infer_workspace_from_session_path(session_path)
    instance_type = normalize_instance_name(_infer_instance_type({}, session_path))
    if project_root is not None:
        layout = project_dirs(project_root, instance_type)
        target_name = p.name.strip() if p.name else ""
        if target_name:
            if bucket == "sources":
                direct = layout["sources_dir"] / target_name
                if direct.exists():
                    return direct.resolve()
            elif bucket == "crops":
                direct = layout["crops_dir"] / target_name
                if direct.exists():
                    return direct.resolve()
            elif bucket == "segments":
                direct = layout["segments_dir"] / target_name
                if direct.exists():
                    return direct.resolve()
                try:
                    found = next(layout["segments_dir"].rglob(target_name), None)
                except Exception:
                    found = None
                if found is not None and found.exists():
                    return found.resolve()
            elif bucket == "solutions":
                for candidate_dir in (layout["solutions_dir"], project_root / "solutions", project_root / "soluciones"):
                    direct = candidate_dir / target_name
                    if direct.exists():
                        return direct.resolve()
                    try:
                        found = next(candidate_dir.rglob(target_name), None)
                    except Exception:
                        found = None
                    if found is not None and found.exists():
                        return found.resolve()

    if not p.is_absolute() and len(p.parts) >= 2:
        old_root_name = str(p.parts[0] or "").strip().lower()
        old_bucket = str(p.parts[1] or "").strip().lower()
        if old_root_name.endswith("_tmp") and old_bucket in {"sources", "crops", "segments"} and project_root is not None:
            old_inst = "resueltos" if "resuelt" in old_root_name else "propuestos"
            migrated = project_dirs(project_root, old_inst)[f"{old_bucket}_dir"] / Path(*p.parts[2:])
            if migrated.exists():
                return migrated.resolve()

    return candidates[0] if candidates else p
