from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modulos.modulo0_transcriptor.scan_pipeline.key_classifier import classify_key_text


class KeyClassifierTests(unittest.TestCase):
    def test_detect_key_sheet(self) -> None:
        text = "CLAVE\n1)B 2)D 3)A 4)C 5)E"
        cls = classify_key_text(text, path=Path("claves_unidad_1.png"))
        self.assertTrue(cls.is_key_image)

    def test_not_key_problem_statement(self) -> None:
        text = "Problema 12. Determine el valor de x en la ecuacion x+3=10."
        cls = classify_key_text(text, path=Path("problemas_12.png"))
        self.assertFalse(cls.is_key_image)

    def test_ambiguous_low_confidence(self) -> None:
        text = "1) Enunciado parcial 2) texto borroso"
        cls = classify_key_text(text, path=Path("scan_001.png"))
        self.assertLess(cls.confidence, 0.75)


if __name__ == "__main__":
    unittest.main()

