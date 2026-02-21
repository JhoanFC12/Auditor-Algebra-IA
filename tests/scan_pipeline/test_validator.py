from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modulos.modulo0_transcriptor.scan_pipeline.renderer import render_item
from modulos.modulo0_transcriptor.scan_pipeline.schema import ScanItem
from modulos.modulo0_transcriptor.scan_pipeline.tokens import SEP_LINE, SEP_OPT
from modulos.modulo0_transcriptor.scan_pipeline.validator import validate_item_json, validate_rendered_item


class ValidatorTests(unittest.TestCase):
    def _base_item(self, *, has_figure: bool) -> ScanItem:
        return ScanItem.from_dict(
            {
                "schema": "ScanItemJSON-v1",
                "n": 5,
                "curso": "Geometria",
                "tema": "Rectas",
                "has_figure": has_figure,
                "figure_tag": "img-5" if has_figure else "",
                "statement": "Determina m\\angle ABC",
                "options": {"A": "10", "B": "20", "C": "30", "D": "40", "E": "50"},
                "needs_review": False,
            },
            default_n=5,
            curso="Geometria",
            tema="Rectas",
        )

    def test_json_validation(self) -> None:
        item = self._base_item(has_figure=False)
        self.assertEqual(validate_item_json(item), [])

    def test_render_validation_without_figure(self) -> None:
        item = self._base_item(has_figure=False)
        rendered = render_item(item)
        self.assertEqual(validate_rendered_item(rendered, item=item), [])
        self.assertNotIn("[[Imagen=", rendered)

    def test_render_validation_with_figure(self) -> None:
        item = self._base_item(has_figure=True)
        rendered = render_item(item)
        self.assertEqual(validate_rendered_item(rendered, item=item), [])
        self.assertIn(f" [[Imagen=img-5]]{SEP_LINE}A)", rendered)

    def test_invalid_options_pattern(self) -> None:
        item = self._base_item(has_figure=False)
        rendered = rf"\item[\textbf{{5.}}] [[curso=Geometria]] [[tema=Rectas]] Enunciado {SEP_LINE}A)$1${SEP_OPT}B)$2$"
        errors = validate_rendered_item(rendered, item=item)
        self.assertIn("patron_opciones_invalido", errors)

    def test_invalid_header_missing_dot(self) -> None:
        item = self._base_item(has_figure=False)
        rendered = render_item(item).replace(r"\item[\textbf{5.}]", r"\item[\textbf{5}]")
        errors = validate_rendered_item(rendered, item=item)
        self.assertIn("header_item_invalido", errors)

    def test_unicode_math_symbol_is_rejected(self) -> None:
        item = self._base_item(has_figure=False)
        rendered = render_item(item).replace("$40$", "$40\u00b0$")
        errors = validate_rendered_item(rendered, item=item)
        self.assertIn("unicode_math_sin_normalizar", errors)

    def test_unbalanced_math_delimiters_is_rejected(self) -> None:
        item = self._base_item(has_figure=False)
        rendered = render_item(item).replace("$20$", "$20")
        errors = validate_rendered_item(rendered, item=item)
        self.assertIn("math_delimiters_desbalanceados", errors)


if __name__ == "__main__":
    unittest.main()
