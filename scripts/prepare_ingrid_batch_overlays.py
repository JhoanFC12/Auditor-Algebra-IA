#!/usr/bin/env python3
"""Render frozen-baseline overlays for one Ingrid review batch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finalize_ingrid_box_batch import render_overlay


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--batch", type=int, required=True)
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    batch_name = f"batch_{args.batch:02d}"
    manifest_path = workspace / "reviews" / "batches_50" / f"{batch_name}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rendered = 0
    for row in manifest["rows"]:
        split = row["split"]
        sample_id = row["sample_id"]
        order = int(row["order"])
        image_path = workspace / "images" / split / f"{sample_id}.png"
        baseline_path = (
            workspace / "remaining_review_baseline_labels" / split / f"{sample_id}.txt"
        )
        destination = (
            workspace
            / "remaining_overlays_before"
            / f"{batch_name}_jpg"
            / split
            / f"{order:02d}__{sample_id}.jpg"
        )
        render_overlay(image_path, baseline_path, destination)
        rendered += 1
    print(json.dumps({"batch": args.batch, "rendered": rendered}))


if __name__ == "__main__":
    main()
