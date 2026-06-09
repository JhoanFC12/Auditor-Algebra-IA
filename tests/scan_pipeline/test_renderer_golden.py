from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modulos.modulo0_transcriptor.scan_pipeline.renderer import render_document, render_item
from modulos.modulo0_transcriptor.scan_pipeline.schema import ScanItem
from modulos.modulo0_transcriptor.scan_pipeline.tokens import SEP_LINE, SEP_OPT


class RendererGoldenTests(unittest.TestCase):
    def test_render_without_figure(self) -> None:
        item = ScanItem.from_dict(
            {
                "schema": "ScanItemJSON-v1",
                "n": 3,
                "curso": "Algebra",
                "tema": "Ecuaciones",
                "has_figure": False,
                "figure_tag": "",
                "statement": "Resuelve x+2=5",
                "options": {"A": "1", "B": "2", "C": "3", "D": "4", "E": "5"},
                "needs_review": False,
            },
            default_n=3,
            curso="Algebra",
            tema="Ecuaciones",
        )
        out = render_item(item)
        self.assertIn(r"\item[\textbf{3.}]", out)
        self.assertIn("[[curso=Algebra]] [[tema=Ecuaciones]]", out)
        self.assertNotIn("[[Imagen=", out)
        self.assertIn(f"{SEP_LINE}A)$1${SEP_OPT}B)$2${SEP_OPT}C)$3${SEP_LINE}D)$4${SEP_OPT}{SEP_OPT}E)$5${SEP_LINE}", out)

    def test_render_with_figure_position(self) -> None:
        item = ScanItem.from_dict(
            {
                "schema": "ScanItemJSON-v1",
                "n": 7,
                "curso": "Geometria",
                "tema": "Triangulos",
                "has_figure": True,
                "figure_tag": "img-7",
                "statement": "Halla AB en la figura",
                "options": {"A": "4", "B": "6", "C": "8", "D": "10", "E": "12"},
                "needs_review": False,
            },
            default_n=7,
            curso="Geometria",
            tema="Triangulos",
        )
        out = render_item(item)
        self.assertIn(f" [[Imagen=img-7]]{SEP_LINE}A)", out)

    def test_render_preserves_answer_key_from_structured_json(self) -> None:
        item = ScanItem.from_dict(
            {
                "schema": "ScanItemJSON-v1",
                "n": 11,
                "curso": "Algebra",
                "tema": "Binomio de Newton",
                "has_figure": False,
                "figure_tag": "",
                "statement": "Calcule el termino independiente",
                "options": {"A": "1", "B": "2", "C": "3", "D": "4", "E": "5"},
                "clave": "C",
                "needs_review": False,
            },
            default_n=11,
            curso="Algebra",
            tema="Binomio de Newton",
        )
        out = render_item(item)
        self.assertIn("[[Clave=C]]", out)
        self.assertIn("[[tema=Binomio de Newton]] [[Clave=C]]", out)

    def test_render_preserves_answer_key_from_final_latex_candidate(self) -> None:
        item = ScanItem.from_dict(
            {
                "schema": "ScanItemJSON-v1",
                "n": 12,
                "curso": "Algebra",
                "tema": "Binomio de Newton",
                "has_figure": False,
                "figure_tag": "",
                "statement": "Calcule el coeficiente",
                "options": {"A": "1", "B": "2", "C": "3", "D": "4", "E": "5"},
                "final_latex_candidate": r"\item[\textbf{12.}] [[Clave=E]] Calcule...",
                "needs_review": False,
            },
            default_n=12,
            curso="Algebra",
            tema="Binomio de Newton",
        )
        out = render_item(item)
        self.assertIn("[[Clave=E]]", out)

    def test_render_statement_with_line_and_equation(self) -> None:
        item = ScanItem.from_dict(
            {
                "schema": "ScanItemJSON-v1",
                "n": 1,
                "curso": "Aritmetica",
                "tema": "Razones",
                "has_figure": False,
                "figure_tag": "",
                "statement": f"Calcula la razon {SEP_LINE}$\\dfrac{{a}}{{b}}=2${SEP_LINE} entre magnitudes",
                "options": {"A": "1", "B": "2", "C": "3", "D": "4", "E": "5"},
                "needs_review": False,
            },
            default_n=1,
            curso="Aritmetica",
            tema="Razones",
        )
        doc = render_document([item])
        self.assertIn(r"\begin{enumerate}", doc)
        self.assertIn(r"\end{enumerate}", doc)
        self.assertIn("$\\dfrac{a}{b}=2$", doc)
        self.assertNotIn(f"{SEP_LINE}$\\dfrac{{a}}{{b}}=2${SEP_LINE}", doc)

    def test_render_converts_equation_delimiters_from_sep_to_math(self) -> None:
        item = ScanItem.from_dict(
            {
                "schema": "ScanItemJSON-v1",
                "n": 2,
                "curso": "Algebra",
                "tema": "Ecuaciones",
                "has_figure": False,
                "figure_tag": "",
                "statement": f"Resuelve {SEP_LINE}x+2=5{SEP_LINE} ahora",
                "options": {"A": "1", "B": "2", "C": "3", "D": "4", "E": "5"},
                "needs_review": False,
            },
            default_n=2,
            curso="Algebra",
            tema="Ecuaciones",
        )
        out = render_item(item)
        self.assertIn("$x+2=5$", out)
        self.assertNotIn(f"{SEP_LINE}$x+2=5${SEP_LINE}", out)

    def test_render_normalizes_unicode_math_symbols(self) -> None:
        item = ScanItem.from_dict(
            {
                "schema": "ScanItemJSON-v1",
                "n": 4,
                "curso": "Geometria",
                "tema": "Angulos",
                "has_figure": False,
                "figure_tag": "",
                "statement": "Halla \u2220ABC = 30\u00b0",
                "options": {"A": "10\u00b0", "B": "20\u00b0", "C": "30\u00b0", "D": "40\u00b0", "E": "50\u00b0"},
                "needs_review": False,
            },
            default_n=4,
            curso="Geometria",
            tema="Angulos",
        )
        out = render_item(item)
        self.assertIn(r"\angle ABC", out)
        self.assertIn(r"30^\circ", out)
        self.assertNotIn("\u2220", out)
        self.assertNotIn("\u00b0", out)


if __name__ == "__main__":
    unittest.main()
