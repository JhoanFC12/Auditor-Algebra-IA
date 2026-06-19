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

    def test_root_override_lists_filesystem_even_when_library_exists(self) -> None:
        class Controller:
            def listar_libros(self, _db_name):
                return [{"id": 1, "codigo": "LIB", "titulo": "Libro BD"}]

            def listar_instancias_libro(self, _db_name, _book_id):
                return [{"id": 10, "tipo": "S01", "session_path": ""}]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / "Libro externo" / "sessions"
            sessions.mkdir(parents=True)
            session = sessions / "semana_2.json"
            session.write_text(
                json.dumps({"output_text": r"\item[\textbf{2.}] Calcule $y$."}, ensure_ascii=False),
                encoding="utf-8",
            )

            service = LatexWordService(controller=Controller())
            payload = service.list_sessions(db_name="demo_db", root=str(root))

            self.assertEqual(payload["source"], "filesystem")
            self.assertEqual(payload["root"], str(root))
            self.assertEqual(payload["summary"]["instances_total"], 1)
            self.assertEqual(payload["books"][0]["title"], "Libro externo")

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

    def test_lists_db_problems_from_practice_controller(self) -> None:
        class PracticeController:
            def normalizar_curso(self, value):
                return str(value or "").upper()

            def contar_problemas(self, db_name, **filters):
                self.db_name = db_name
                self.filters = filters
                return 1

            def obtener_problemas(self, _db_name, *, cantidad, **_filters):
                self.cantidad = cantidad
                return [
                    {
                        "id": 7,
                        "numero_original": 3,
                        "curso": "Geometria",
                        "tema": "Angulos",
                        "respuesta_correcta": "B",
                        "enunciado_latex": r"\item[\textbf{3.}] Calcule $x$. £A)1æB)2£",
                    }
                ]

            def listar_cursos(self, _db_name):
                return ["Geometria"]

            def listar_temas(self, _db_name, *, curso=""):
                return [{"id": 1, "nombre": "Angulos", "curso": curso}]

            def listar_autores(self, _db_name, **_filters):
                return []

            def listar_editoriales(self, _db_name, **_filters):
                return []

        controller = PracticeController()
        service = LatexWordService(practice_controller=controller)

        payload = service.list_db_problems(
            db_name="demo",
            curso="Geometria",
            tema_id=10,
            subtema_id=20,
            autor="Meza",
            editorial="IMPECUS",
            limit=20,
            aleatorio=False,
        )

        self.assertEqual(payload["schema_version"], "latex_word_problem_selection_v1")
        self.assertEqual(payload["db_name"], "demo")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(controller.filters["curso"], "GEOMETRIA")
        self.assertEqual(controller.filters["tema_id"], 10)
        self.assertEqual(controller.filters["subtema_id"], 20)
        self.assertEqual(controller.filters["autor"], "Meza")
        self.assertEqual(controller.filters["editorial"], "IMPECUS")
        self.assertEqual(payload["problems"][0]["id"], 7)
        self.assertEqual(payload["problems"][0]["respuesta_correcta"], "B")
        self.assertEqual(payload["options"]["cursos"], ["Geometria"])

    def test_converts_db_problems_with_fake_latex_to_word_script(self) -> None:
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
            image = root / "figura.png"
            image.write_bytes(b"png")

            class PracticeController:
                def obtener_problemas_por_ids(self, _db_name, *, problem_ids):
                    self.problem_ids = list(problem_ids)
                    return [
                        {
                            "id": 11,
                            "numero_original": 5,
                            "curso": "Geometria",
                            "tema": "Triangulos",
                            "respuesta_correcta": "C",
                            "consistencia_matematica": "",
                            "enunciado_latex": r"\item[\textbf{5.}] Calcule $x$. £A)1æB)2æC)3£",
                            "imagenes": [str(image)],
                            "ruta_carpeta": str(root),
                        }
                    ]

            output = root / "practica.docx"
            service = LatexWordService(
                practice_controller=PracticeController(),
                file_url_resolver=lambda path: f"/file/{Path(path).name}",
            )

            result = service.convert_db_problems(
                db_name="demo",
                problem_ids=[11],
                output_docx=str(output),
                repo=str(repo),
                python=sys.executable,
                title="Practica demo",
            )

            self.assertTrue(output.exists())
            source = root / "practica__db_source.tex"
            self.assertTrue(source.exists())
            source_text = source.read_text(encoding="utf-8")
            self.assertIn("[[curso=Geometria]]", source_text)
            self.assertIn("[[Imagen=p11_figura]]", source_text)
            self.assertIn("[[clave=C]]", source_text)
            self.assertTrue((root / "practica__db_images" / "p11_figura.png").exists())
            self.assertEqual(result["word_url"], "/file/practica.docx")


if __name__ == "__main__":
    unittest.main()
