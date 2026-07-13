from __future__ import annotations

import unittest

from modulos.instance_factory.normalizer_inference import repair_final_latex_with_normalizer_input


class NormalizerContinuationRepairTests(unittest.TestCase):
    def test_adds_options_from_continuation(self) -> None:
        final = (
            r"\item[\textbf{14.}] [[curso=Geometría]] [[tema=Triángulos]] "
            r"[[Estado=sin_revisar]] [[Clave=-]] Enunciado incompleto."
        )
        payload = {
            "raw_ocr": (
                r"<14.> Enunciado incompleto."
                "\n\n"
                r"[CONT. 1] calcule $x$. A) $30^\circ$ B) $45^\circ$ C) $60^\circ$ D) $75^\circ$ E) $90^\circ$"
            ),
            "continuations": [
                {
                    "raw_ocr": r"calcule $x$. A) $30^\circ$ B) $45^\circ$ C) $60^\circ$ D) $75^\circ$ E) $90^\circ$",
                    "figure_segmentation": {"has_figure": False, "segments_total": 0},
                }
            ],
            "human_hints": {"has_figure": False, "figure_tag": ""},
        }

        repaired = repair_final_latex_with_normalizer_input(final, payload)

        self.assertIn(r"calcule $x$.", repaired)
        self.assertIn("£A) $30^\\circ$æB) $45^\\circ$æC) $60^\\circ$£D) $75^\\circ$ææE) $90^\\circ$£", repaired)

    def test_adds_image_when_only_continuation_has_figure(self) -> None:
        final = (
            r"\item[\textbf{11.}] [[curso=Geometría]] [[tema=Triángulos]] "
            r"[[Estado=sin_revisar]] [[Clave=-]] Calcular $x$."
        )
        payload = {
            "raw_ocr": r"<11.> Calcular $x$." "\n\n" r"[CONT. 1] A) $30^\circ$ B) $10^\circ$ C) $18^\circ$ D) $72^\circ$ E) $36^\circ$",
            "source": {"problem_number": 11},
            "continuations": [
                {
                    "raw_ocr": r"A) $30^\circ$ B) $10^\circ$ C) $18^\circ$ D) $72^\circ$ E) $36^\circ$",
                    "figure_segmentation": {"has_figure": True, "segments_total": 1},
                }
            ],
            "human_hints": {"has_figure": True, "figure_tag": "img-11"},
        }

        repaired = repair_final_latex_with_normalizer_input(final, payload)

        self.assertIn("[[Imagen=img-11]]", repaired)
        self.assertLess(repaired.index("[[Imagen=img-11]]"), repaired.index("£A)"))


if __name__ == "__main__":
    unittest.main()
