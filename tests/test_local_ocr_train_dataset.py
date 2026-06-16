from __future__ import annotations

import unittest

from tools.train_local_ocr_lora import _oversample_rows


class LocalOcrTrainDatasetTests(unittest.TestCase):
    def test_oversamples_selected_error_type_only(self) -> None:
        rows = [
            {"id": "a", "error_types": ["angle_symbol_confusion"]},
            {"id": "b", "error_types": ["option_missing"]},
        ]

        out = _oversample_rows(rows, error_type="angle_symbol_confusion", factor=3)

        self.assertEqual([row["id"] for row in out], ["a", "a", "a", "b"])


if __name__ == "__main__":
    unittest.main()
