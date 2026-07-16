from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from modulos.problem_detector_lab.server import (
    ProblemDetectorLabServer,
    labels_semantically_equal,
    read_review_selection,
)


def test_labels_semantically_equal_ignores_line_endings_and_spacing(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.txt"
    current = tmp_path / "current.txt"
    baseline.write_bytes(b"0 0.5 0.5 0.2 0.2\r\n2 0.4 0.7 0.3 0.1\r\n")
    current.write_bytes(b"0  0.5 0.5 0.2 0.2\n2 0.4 0.7 0.3 0.1\n")

    assert labels_semantically_equal(baseline, current)

    current.write_text("0 0.5 0.5 0.2 0.2\n2 0.4 0.7 0.4 0.1\n", encoding="utf-8")
    assert not labels_semantically_equal(baseline, current)


def test_active_review_manifest_and_human_gate(tmp_path: Path) -> None:
    dataset = tmp_path / "problem_detector_multiclass_ingrid_review_test"
    reviews = dataset / "reviews"
    batches = reviews / "batches_50"
    batches.mkdir(parents=True)
    manifest = batches / "human_review_13.json"
    manifest.write_text(
        json.dumps(
            {
                "queue_id": "human-review-13",
                "rows": [
                    {"split": "train", "sample_id": "sample_a", "order": 1},
                    {"split": "val", "sample_id": "sample_b", "order": 2},
                ],
            }
        ),
        encoding="utf-8",
    )
    (batches / "active_batch.json").write_text(
        json.dumps({"manifest": str(manifest)}),
        encoding="utf-8",
    )
    (reviews / "train").mkdir()
    (reviews / "val").mkdir()
    (reviews / "train" / "sample_a.json").write_text(
        json.dumps({"status": "human_approved", "human_review": "approved"}),
        encoding="utf-8",
    )
    (reviews / "val" / "sample_b.json").write_text(
        json.dumps({"status": "agent_corrected_pending_human", "human_review": "pending"}),
        encoding="utf-8",
    )

    selection = read_review_selection(reviews)
    gate = ProblemDetectorLabServer(dataset_root=dataset)._human_approval_gate()

    assert [row["sample_id"] for row in selection["rows"]] == ["sample_a", "sample_b"]
    assert gate["approved_total"] == 1
    assert gate["pending_total"] == 1
    assert gate["status"] == "pending_human_review"


def test_approve_sample_persists_review_and_marks_queue_ready(tmp_path: Path) -> None:
    dataset = tmp_path / "problem_detector_multiclass_ingrid_review_test"
    for relative in (
        "images/train",
        "labels/train",
        "baseline_labels/train",
        "metadata/train",
        "reviews/train",
        "reviews/batches_50",
    ):
        (dataset / relative).mkdir(parents=True, exist_ok=True)
    sample_id = "sample_ready"
    Image.new("RGB", (100, 100), color="white").save(dataset / "images" / "train" / f"{sample_id}.png")
    (dataset / "baseline_labels" / "train" / f"{sample_id}.txt").write_text(
        "0 0.500000 0.500000 0.500000 0.500000\n",
        encoding="utf-8",
    )
    (dataset / "labels" / "train" / f"{sample_id}.txt").write_text(
        "0 0.500000 0.500000 0.400000 0.400000\n",
        encoding="utf-8",
    )
    (dataset / "metadata" / "train" / f"{sample_id}.json").write_text("{}", encoding="utf-8")
    (dataset / "reviews" / "train" / f"{sample_id}.json").write_text(
        json.dumps({"status": "agent_corrected_pending_human", "human_review": "pending"}),
        encoding="utf-8",
    )
    manifest = dataset / "reviews" / "batches_50" / "human_review_13.json"
    manifest.write_text(
        json.dumps(
            {
                "queue_id": "human-review-ready",
                "rows": [{"split": "train", "sample_id": sample_id, "order": 1}],
            }
        ),
        encoding="utf-8",
    )
    (manifest.parent / "active_batch.json").write_text(
        json.dumps({"manifest": str(manifest)}),
        encoding="utf-8",
    )

    result = ProblemDetectorLabServer(dataset_root=dataset)._approve_sample(
        {"sample_id": sample_id, "split": "train"}
    )
    persisted = json.loads((dataset / "reviews" / "train" / f"{sample_id}.json").read_text(encoding="utf-8"))
    gate = json.loads((dataset / "reviews" / "human_approval_gate.json").read_text(encoding="utf-8"))

    assert persisted["status"] == "human_approved"
    assert persisted["human_review"] == "approved"
    assert persisted["training_candidate"] is True
    assert result["approval_gate"]["status"] == "ready_for_database"
    assert gate["approved_total"] == 1
    assert gate["pending_total"] == 0
