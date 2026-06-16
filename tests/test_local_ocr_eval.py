from __future__ import annotations

import unittest

from tools.evaluate_local_ocr_dataset import evaluate_rows


class LocalOcrEvalTests(unittest.TestCase):
    def test_evaluates_raw_candidate_metrics(self) -> None:
        rows = [
            {
                "id": "a",
                "image": "x.png",
                "text": "<01.> Halle $x$. A) 1 B) 2 C) 3 D) 4 E) 5",
                "raw_candidate": "<01.> Halle $x$. A) 1 B) 2 C) 3 D) 4 E) 5",
            },
            {
                "id": "b",
                "image": "y.png",
                "text": "<02.> Calcule. A) 10 B) 20 C) 30 D) 40 E) 50",
                "raw_candidate": "<92.> Calcule. A) 10 B) 20",
            },
        ]

        summary = evaluate_rows(rows, dataset_dir=".", candidate_field="raw_candidate")

        self.assertEqual(summary["samples"], 2)
        self.assertAlmostEqual(summary["exact_match_rate"], 0.5)
        self.assertAlmostEqual(summary["prefix_ok_rate"], 0.5)
        self.assertLess(summary["avg_options_recall"], 1.0)

    def test_continuation_prefix_is_checked(self) -> None:
        rows = [
            {
                "id": "cont",
                "image": "x.png",
                "text": "[CONT.] A) 1 B) 2",
                "raw_candidate": "<03.> A) 1 B) 2",
            }
        ]

        summary = evaluate_rows(rows, dataset_dir=".", candidate_field="raw_candidate")

        self.assertEqual(summary["prefix_ok_rate"], 0.0)

    def test_angle_symbol_accuracy_catches_inequality_confusion(self) -> None:
        rows = [
            {
                "id": "angle-ok",
                "image": "x.png",
                "text": "<01.> Si $m\\sphericalangle ABC=40^\\circ$, calcule $x$.",
                "raw_candidate": "<01.> Si $m\\sphericalangle ABC=40^\\circ$, calcule $x$.",
            },
            {
                "id": "angle-bad",
                "image": "y.png",
                "text": "<02.> Si $m\\sphericalangle BCD=50^\\circ$, calcule $x$.",
                "raw_candidate": "<02.> Si $m\\leq BCD=50^\\circ$, calcule $x$.",
            },
        ]

        summary = evaluate_rows(rows, dataset_dir=".", candidate_field="raw_candidate")

        self.assertEqual(summary["angle_symbol_samples"], 2)
        self.assertAlmostEqual(summary["angle_symbol_accuracy"], 0.5)
        self.assertEqual(summary["angle_confusion_total"], 1)


if __name__ == "__main__":
    unittest.main()
