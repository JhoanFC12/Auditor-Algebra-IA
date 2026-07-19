from __future__ import annotations

import unittest

from modulos.instance_factory.document_splits import (
    audit_document_split_manifest,
    build_document_split_manifest,
    validate_document_split_manifest,
)


def _document(
    document_id: str,
    digest_char: str,
    *,
    derivatives: list[str] | None = None,
    equivalence_group: str = "",
) -> dict:
    return {
        "document_id": document_id,
        "source_digest": "sha256:" + digest_char * 64,
        "page_count": 100,
        "derivative_digests": list(derivatives or []),
        "equivalence_group": equivalence_group or document_id,
    }


class DocumentSplitLeakageTests(unittest.TestCase):
    def test_complete_documents_and_derivatives_inherit_one_split(self) -> None:
        documents = [
            _document("doc-a", "a", derivatives=["sha256:" + "1" * 64]),
            _document("doc-b", "b", derivatives=["sha256:" + "2" * 64]),
            _document("doc-c", "c", derivatives=["sha256:" + "3" * 64]),
            _document("doc-d", "d", derivatives=["sha256:" + "4" * 64]),
        ]
        assignments = {
            "doc-a": "train",
            "doc-b": "validation",
            "doc-c": "test",
            "doc-d": "difficult_ood",
        }

        manifest = build_document_split_manifest(documents, assignments, dataset_id="dataset-v1")
        audit = audit_document_split_manifest(manifest)

        self.assertEqual(audit["status"], "passed")
        self.assertEqual(manifest["splits"]["test"], ["doc-c"])
        self.assertEqual(manifest["documents"][2]["split"], "test")
        self.assertEqual(manifest["documents"][2]["derivatives"][0]["split"], "test")
        self.assertEqual(validate_document_split_manifest(manifest), [])

    def test_source_digest_cannot_cross_splits(self) -> None:
        first = _document("doc-a", "a")
        duplicate = _document("doc-a-copy", "a")
        manifest = build_document_split_manifest(
            [first, duplicate],
            {"doc-a": "train", "doc-a-copy": "test"},
            dataset_id="dataset-v1",
        )

        audit = audit_document_split_manifest(manifest)

        self.assertEqual(audit["status"], "failed")
        self.assertEqual(audit["source_digests_in_multiple_splits"], 1)
        self.assertIn("split:source_digest_leak", validate_document_split_manifest(manifest))

    def test_derivative_and_equivalent_document_leaks_are_detected(self) -> None:
        shared_derivative = "sha256:" + "9" * 64
        first = _document("doc-a", "a", derivatives=[shared_derivative], equivalence_group="same-book")
        second = _document("doc-b", "b", derivatives=[shared_derivative], equivalence_group="same-book")
        manifest = build_document_split_manifest(
            [first, second],
            {"doc-a": "train", "doc-b": "difficult_ood"},
            dataset_id="dataset-v1",
        )

        audit = audit_document_split_manifest(manifest)

        self.assertEqual(audit["derivative_leaks"], 1)
        self.assertEqual(audit["equivalence_group_leaks"], 1)
        self.assertEqual(audit["status"], "failed")

    def test_manifest_is_deterministic_and_rejects_unassigned_documents(self) -> None:
        first = _document("doc-a", "a")
        second = _document("doc-b", "b")
        assignments = {"doc-a": "train", "doc-b": "test"}

        left = build_document_split_manifest([first, second], assignments, dataset_id="dataset-v1")
        right = build_document_split_manifest([second, first], assignments, dataset_id="dataset-v1")

        self.assertEqual(left["manifest_fingerprint"], right["manifest_fingerprint"])
        with self.assertRaisesRegex(ValueError, "sin split"):
            build_document_split_manifest([first], {}, dataset_id="dataset-v1")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
