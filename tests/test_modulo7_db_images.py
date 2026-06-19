from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from modulos.modulo7_latex_word.gui_latex_word import LatexWordBridgeWindow


class _Var:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


def _window() -> LatexWordBridgeWindow:
    win = object.__new__(LatexWordBridgeWindow)
    win.images_dir_var = _Var("")
    win.db_name_var = _Var("")
    win._db_book_workspace_cache = {}
    win.db_manager = None
    win.txt_practice_structure = None
    win._log = lambda _text: None
    return win


class Modulo7DbImageTests(unittest.TestCase):
    def test_structured_db_images_are_namespaced_by_problem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_dir = root / "first"
            second_dir = root / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            first_image = first_dir / "img-15.png"
            second_image = second_dir / "img-15.png"
            first_image.write_bytes(b"first")
            second_image.write_bytes(b"second")
            win = _window()

            first = {"id": 101, "numero_original": 15, "imagenes": [str(first_image)]}
            second = {"id": 102, "numero_original": 16, "imagenes": [str(second_image)]}

            first_entries = win._resolve_db_preview_structured_images(first)
            second_entries = win._resolve_db_preview_structured_images(second)

            self.assertEqual(first_entries, [("p101_img-15", first_image)])
            self.assertEqual(second_entries, [("p102_img-15", second_image)])
            self.assertEqual(
                set(win._build_db_preview_mathjax_images_bundle([first, second])),
                {"p101_img-15", "p102_img-15"},
            )

    def test_prepare_db_images_copies_duplicate_source_names_without_collision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_dir = root / "first"
            second_dir = root / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            first_image = first_dir / "img-15.png"
            second_image = second_dir / "img-15.png"
            first_image.write_bytes(b"first")
            second_image.write_bytes(b"second")
            output_docx = root / "practice.docx"
            win = _window()
            problems = [
                {
                    "id": 101,
                    "numero_original": 15,
                    "imagenes": [str(first_image)],
                    "enunciado_latex": r"\item[\textbf{15.}] Calcule $x$. [[Imagen=img-15]] A)$1$ B)$2$",
                },
                {
                    "id": 102,
                    "numero_original": 16,
                    "imagenes": [str(second_image)],
                    "enunciado_latex": r"\item[\textbf{16.}] Calcule $y$. [[Imagen=img-15]] A)$3$ B)$4$",
                },
            ]

            images_dir = win._prepare_images_dir_for_db(problems, output_docx=output_docx)

            self.assertEqual(images_dir, root / "practice__db_images")
            self.assertEqual((images_dir / "p101_img-15.png").read_bytes(), b"first")
            self.assertEqual((images_dir / "p102_img-15.png").read_bytes(), b"second")
            source_text = win._build_scan_source_text_from_db(problems)
            self.assertIn("[[Imagen=p101_img-15]]", source_text)
            self.assertIn("[[Imagen=p102_img-15]]", source_text)


if __name__ == "__main__":
    unittest.main()
