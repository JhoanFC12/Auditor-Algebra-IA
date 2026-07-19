from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


DOCUMENT_SPLIT_SCHEMA_VERSION = "document_split_manifest_v1"
SPLIT_NAMES = ("train", "validation", "test", "difficult_ood")
_DIGEST_RE = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{64}$")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _fingerprint(payload: Mapping[str, Any]) -> str:
    clean = copy.deepcopy(_mapping(payload))
    clean.pop("manifest_fingerprint", None)
    encoded = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _split_memberships(manifest: Mapping[str, Any]) -> dict[str, set[str]]:
    memberships: dict[str, set[str]] = defaultdict(set)
    splits = _mapping(manifest.get("splits"))
    for split_name in SPLIT_NAMES:
        for document_id in _sequence(splits.get(split_name)):
            if _text(document_id):
                memberships[_text(document_id)].add(split_name)
    return memberships


def audit_document_split_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    raw = _mapping(manifest)
    documents = [_mapping(item) for item in _sequence(raw.get("documents"))]
    memberships = _split_memberships(raw)
    source_splits: dict[str, set[str]] = defaultdict(set)
    derivative_splits: dict[str, set[str]] = defaultdict(set)
    equivalence_splits: dict[str, set[str]] = defaultdict(set)
    details: list[dict[str, Any]] = []
    for document in documents:
        document_id = _text(document.get("document_id"))
        split_name = _text(document.get("split"))
        source_digest = _text(document.get("source_digest"))
        equivalence_group = _text(document.get("equivalence_group"))
        if source_digest and split_name:
            source_splits[source_digest].add(split_name)
        if equivalence_group and split_name:
            equivalence_splits[equivalence_group].add(split_name)
        for derivative in _sequence(document.get("derivatives")):
            row = _mapping(derivative)
            digest = _text(row.get("digest"))
            derivative_split = _text(row.get("split") or split_name)
            if digest and derivative_split:
                derivative_splits[digest].add(derivative_split)

    duplicate_documents = {key: sorted(value) for key, value in memberships.items() if len(value) > 1}
    source_leaks = {key: sorted(value) for key, value in source_splits.items() if len(value) > 1}
    derivative_leaks = {key: sorted(value) for key, value in derivative_splits.items() if len(value) > 1}
    equivalence_leaks = {key: sorted(value) for key, value in equivalence_splits.items() if len(value) > 1}
    for kind, rows in (
        ("document", duplicate_documents),
        ("source_digest", source_leaks),
        ("derivative", derivative_leaks),
        ("equivalence_group", equivalence_leaks),
    ):
        details.extend({"kind": kind, "identity": key, "splits": splits} for key, splits in sorted(rows.items()))
    failed = any((duplicate_documents, source_leaks, derivative_leaks, equivalence_leaks))
    return {
        "schema_version": "document_split_leakage_audit_v1",
        "documents_in_multiple_splits": len(duplicate_documents),
        "source_digests_in_multiple_splits": len(source_leaks),
        "derivative_leaks": len(derivative_leaks),
        "equivalence_group_leaks": len(equivalence_leaks),
        "details": details,
        "status": "failed" if failed else "passed",
    }


def build_document_split_manifest(
    documents: Sequence[Mapping[str, Any]],
    split_assignments: Mapping[str, str],
    *,
    dataset_id: str,
) -> dict[str, Any]:
    clean_dataset_id = _text(dataset_id)
    if not clean_dataset_id:
        raise ValueError("dataset_id es requerido")
    assignments = {str(key): _text(value) for key, value in split_assignments.items()}
    normalized_documents: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in sorted((_mapping(item) for item in documents), key=lambda row: _text(row.get("document_id"))):
        document_id = _text(raw.get("document_id"))
        if not document_id:
            raise ValueError("Documento sin document_id")
        if document_id in seen_ids:
            raise ValueError(f"document_id duplicado: {document_id}")
        seen_ids.add(document_id)
        split_name = assignments.get(document_id, "")
        if not split_name:
            raise ValueError(f"Documento sin split: {document_id}")
        if split_name not in SPLIT_NAMES:
            raise ValueError(f"Split no valido para {document_id}: {split_name}")
        source_digest = _text(raw.get("source_digest"))
        if not _DIGEST_RE.fullmatch(source_digest):
            raise ValueError(f"source_digest no valido para {document_id}")
        try:
            page_count = int(raw.get("page_count") or 0)
        except (TypeError, ValueError):
            page_count = 0
        if page_count < 1:
            raise ValueError(f"page_count no valido para {document_id}")
        derivative_digests = sorted({_text(item) for item in _sequence(raw.get("derivative_digests")) if _text(item)})
        for digest in derivative_digests:
            if not _DIGEST_RE.fullmatch(digest):
                raise ValueError(f"derivative_digest no valido para {document_id}")
        normalized_documents.append(
            {
                "document_id": document_id,
                "source_digest": source_digest,
                "page_count": page_count,
                "equivalence_group": _text(raw.get("equivalence_group")) or source_digest,
                "split": split_name,
                "pages_inherit_split": True,
                "derivatives": [{"digest": digest, "split": split_name} for digest in derivative_digests],
            }
        )

    splits = {
        split_name: sorted(row["document_id"] for row in normalized_documents if row["split"] == split_name)
        for split_name in SPLIT_NAMES
    }
    source_groups: dict[str, list[str]] = defaultdict(list)
    equivalence_groups: dict[str, list[str]] = defaultdict(list)
    for document in normalized_documents:
        source_groups[document["source_digest"]].append(document["document_id"])
        equivalence_groups[document["equivalence_group"]].append(document["document_id"])
    manifest: dict[str, Any] = {
        "schema_version": DOCUMENT_SPLIT_SCHEMA_VERSION,
        "dataset_id": clean_dataset_id,
        "splits": splits,
        "documents": normalized_documents,
        "deduplication_report": {
            "exact_duplicate_groups": [sorted(ids) for ids in source_groups.values() if len(ids) > 1],
            "equivalence_groups": [sorted(ids) for ids in equivalence_groups.values() if len(ids) > 1],
        },
    }
    manifest["leakage_audit"] = audit_document_split_manifest(manifest)
    manifest["manifest_fingerprint"] = _fingerprint(manifest)
    return manifest


def validate_document_split_manifest(manifest: Mapping[str, Any]) -> list[str]:
    raw = _mapping(manifest)
    issues: list[str] = []
    if _text(raw.get("schema_version")) != DOCUMENT_SPLIT_SCHEMA_VERSION:
        issues.append("split:invalid_schema_version")
    if not _text(raw.get("dataset_id")):
        issues.append("split:missing_dataset_id")
    splits = _mapping(raw.get("splits"))
    if set(splits) != set(SPLIT_NAMES):
        issues.append("split:invalid_split_keys")
    documents = [_mapping(item) for item in _sequence(raw.get("documents"))]
    if not documents:
        issues.append("split:missing_documents")
    document_ids = [_text(item.get("document_id")) for item in documents]
    if len(document_ids) != len(set(document_ids)):
        issues.append("split:duplicate_document_ids")
    memberships = _split_memberships(raw)
    for document in documents:
        document_id = _text(document.get("document_id"))
        split_name = _text(document.get("split"))
        if split_name not in SPLIT_NAMES:
            issues.append(f"split:invalid_document_split:{document_id}")
        if memberships.get(document_id) != {split_name}:
            issues.append(f"split:membership_mismatch:{document_id}")
        if document.get("pages_inherit_split") is not True:
            issues.append(f"split:pages_do_not_inherit:{document_id}")
        for derivative in _sequence(document.get("derivatives")):
            if _text(_mapping(derivative).get("split")) != split_name:
                issues.append(f"split:derivative_does_not_inherit:{document_id}")
    audit = audit_document_split_manifest(raw)
    if audit["documents_in_multiple_splits"]:
        issues.append("split:document_leak")
    if audit["source_digests_in_multiple_splits"]:
        issues.append("split:source_digest_leak")
    if audit["derivative_leaks"]:
        issues.append("split:derivative_leak")
    if audit["equivalence_group_leaks"]:
        issues.append("split:equivalence_group_leak")
    if _text(raw.get("manifest_fingerprint")) != _fingerprint(raw):
        issues.append("split:fingerprint_mismatch")
    return list(dict.fromkeys(issues))


__all__ = [
    "DOCUMENT_SPLIT_SCHEMA_VERSION",
    "SPLIT_NAMES",
    "audit_document_split_manifest",
    "build_document_split_manifest",
    "validate_document_split_manifest",
]
