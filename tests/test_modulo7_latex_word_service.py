from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from modulos.modulo7_latex_word.service import LatexWordService


class LatexWordServiceTests(unittest.TestCase):
    def test_lists_sessions_with_word_status_from_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / "Libro Geo" / "sessions"
            sessions.mkdir(parents=True)
            session = sessions / "semana_1.json"
            session.write_text(
                json.dumps({"output_text": r"\item[\textbf{1.}] Calcule $x$."}, ensure_ascii=False),
                encoding="utf-8",
            )
            session.with_suffix(".docx").write_bytes(b"word")

            service = LatexWordService(controller=None, file_url_resolver=lambda path: f"/file/{Path(path).name}")
            payload = service.list_sessions(root=str(root))

            self.assertEqual(payload["schema_version"], "latex_word_sessions_v1")
            self.assertEqual(payload["summary"]["books_total"], 1)
            self.assertEqual(payload["summary"]["instances_total"], 1)
            self.assertEqual(payload["summary"]["word_ready"], 1)
            row = payload["books"][0]["instances"][0]
            self.assertTrue(row["session_exists"])
            self.assertTrue(row["word_exists"])
            self.assertEqual(row["word_url"], "/file/semana_1.docx")

    def test_converts_session_with_fake_latex_to_word_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "Editor_de_practicas"
            repo.mkdir()
            script = repo / "latex_to_word.py"
            script.write_text(
                "\n".join(
                    [
                        "from pathlib import Path",
                        "import sys",
                        "out = Path(sys.argv[2])",
                        "out.write_bytes(b'docx')",
                        "print(f'Word generado en: {out}')",
                    ]
                ),
                encoding="utf-8",
            )
            session = root / "sessions" / "s1.json"
            session.parent.mkdir()
            source_image = root / "crop.png"
            source_image.write_bytes(b"png")
            session.write_text(
                json.dumps(
                    {
                        "output_text": r"\item[\textbf{1.}] Calcule $x$. [[Imagen=img-1]]",
                        "preview_images": {"img-1": str(source_image)},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output = root / "salida.docx"
            service = LatexWordService(controller=None, file_url_resolver=lambda path: f"/file/{Path(path).name}")

            result = service.convert_session(
                session_path=str(session),
                output_docx=str(output),
                repo=str(repo),
                python=sys.executable,
            )

            self.assertTrue(output.exists())
            self.assertTrue((root / "salida__session_source.tex").exists())
            self.assertTrue((root / "salida__session_images" / "img-1.png").exists())
            self.assertEqual(result["word_url"], "/file/salida.docx")


if __name__ == "__main__":
    unittest.main()
