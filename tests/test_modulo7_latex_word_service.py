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

            def listar_libros_problemas(self, _db_name, **_filters):
                self.book_option_filters = _filters
                return [{"id": "catalog:book:4:Libro Geo", "label": "LIB | Libro Geo"}]

        controller = PracticeController()
        service = LatexWordService(practice_controller=controller)

        payload = service.list_db_problems(
            db_name="demo",
            curso="Geometria",
            tema_id=10,
            subtema_id=20,
            autor="Meza",
            editorial="IMPECUS",
            libro="catalog:book:4:Libro Geo",
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
        self.assertEqual(controller.filters["libro"], "catalog:book:4:Libro Geo")
        self.assertEqual(payload["problems"][0]["id"], 7)
        self.assertEqual(payload["problems"][0]["respuesta_correcta"], "B")
        self.assertEqual(payload["options"]["cursos"], ["Geometria"])
        self.assertEqual(payload["options"]["libros"][0]["label"], "LIB | Libro Geo")

    def test_problem_payload_includes_image_urls_for_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "img-5.png"
            image.write_bytes(b"png")
            service = LatexWordService(
                controller=None,
                file_url_resolver=lambda path: f"/file/{Path(path).name}",
            )

            payload = service._problem_payload(
                {
                    "id": 5,
                    "numero_original": 5,
                    "curso": "Geometria",
                    "tema": "Circunferencias",
                    "respuesta_correcta": "A",
                    "imagenes": [str(image)],
                    "ruta_carpeta": str(root),
                    "enunciado_latex": r"\item[\textbf{5.}] Calcule $x$. [[Imagen=img-5]]",
                }
            )

            self.assertEqual(payload["imagenes_count"], 1)
            self.assertEqual(payload["imagenes"][0]["name"], "img-5.png")
            self.assertEqual(payload["imagenes"][0]["url"], "/file/img-5.png")

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

    def test_converts_db_instance_with_fake_latex_to_word_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "Editor_de_practicas"
            repo.mkdir()
            (repo / "latex_to_word.py").write_text(
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

            class PracticeController:
                def obtener_problemas_por_instancia(self, _db_name, *, libro_codigo, codigo_instancia):
                    self.libro_codigo = libro_codigo
                    self.codigo_instancia = codigo_instancia
                    return [
                        {
                            "id": 21,
                            "numero_original": 1,
                            "curso": "Geometria",
                            "tema": "Triangulos",
                            "respuesta_correcta": "A",
                            "consistencia_matematica": "Consistente",
                            "enunciado_latex": r"\item[\textbf{1.}] Calcule $x$. Â£A)1Ã¦B)2Â£",
                        }
                    ]

            controller = PracticeController()
            output = root / "instancia.docx"
            service = LatexWordService(practice_controller=controller)

            result = service.convert_db_instance(
                db_name="demo",
                libro_codigo="LIBRO",
                codigo_instancia="semana_1",
                output_docx=str(output),
                repo=str(repo),
                python=sys.executable,
            )

            self.assertTrue(output.exists())
            self.assertEqual(controller.libro_codigo, "LIBRO")
            self.assertEqual(controller.codigo_instancia, "semana_1")
            self.assertEqual(result["schema_version"], "latex_word_db_instance_conversion_v1")
            self.assertEqual(result["count"], 1)
            self.assertTrue((root / "instancia__instance_source.tex").exists())

    def test_db_instance_source_repairs_mojibake_before_word(self) -> None:
        service = LatexWordService()
        problems = [
            {
                "id": 91,
                "numero_original": 1,
                "curso": "Geometria",
                "tema": "Circunferencia",
                "enunciado_latex": (
                    r"\item[\textbf{1.}] En un triÃ¡ngulo, calcule el Ã¡ngulo. "
                    r"Â£A)$10^\circ$Ã¦B)$20^\circ$Â£"
                ),
            }
        ]

        source = service._build_scan_source_text_from_db(problems)

        self.assertIn("triangulo", service._normalize_search(source))
        self.assertIn("angulo", service._normalize_search(source))
        self.assertIn("\u00a3A)", source)
        self.assertIn("\u00e6B)", source)
        self.assertNotIn("Ã", source)
        self.assertNotIn("Â£", source)

    def test_converts_db_instance_to_word_folder_next_to_pdf_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "Editor_de_practicas"
            repo.mkdir()
            (repo / "latex_to_word.py").write_text(
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
            pdf = root / "Libro Origen.pdf"
            pdf.write_bytes(b"%PDF")

            class PracticeController:
                def obtener_problemas_por_instancia(self, _db_name, *, libro_codigo, codigo_instancia):
                    return [
                        {
                            "id": 31,
                            "numero_original": 1,
                            "curso": "Geometria",
                            "tema": "Triangulos",
                            "respuesta_correcta": "A",
                            "enunciado_latex": r"\item[\textbf{1.}] Calcule $x$.",
                            "pdf_path": str(pdf),
                        }
                    ]

            service = LatexWordService(practice_controller=PracticeController())

            result = service.convert_db_instance(
                db_name="demo",
                libro_codigo="LIBRO GEO",
                codigo_instancia="semana 1",
                repo=str(repo),
                python=sys.executable,
            )

            produced = Path(result["word_path"])
            self.assertEqual(produced.parent.name, "Word")
            self.assertEqual(produced.name, "LIBRO_GEO__semana_1.docx")
            self.assertTrue(produced.exists())
            self.assertTrue((produced.parent / "LIBRO_GEO__semana_1__instance_source.tex").exists())

    def test_converts_multiple_db_instances_into_combined_word(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "Editor_de_practicas"
            repo.mkdir()
            (repo / "latex_to_word.py").write_text(
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

            class PracticeController:
                def obtener_problemas_por_instancia(self, _db_name, *, libro_codigo, codigo_instancia):
                    number = 31 if codigo_instancia == "S2" else 1
                    return [
                        {
                            "id": 100 + len(codigo_instancia),
                            "numero_original": number,
                            "curso": "Geometria",
                            "tema": "Triangulos",
                            "respuesta_correcta": "A",
                            "enunciado_latex": rf"\item[\textbf{{{number}.}}] Problema de {codigo_instancia}.",
                        }
                    ]

            output = root / "completo.docx"
            service = LatexWordService(practice_controller=PracticeController())

            result = service.convert_db_instances_combined(
                db_name="demo",
                instances=[
                    {"libro_codigo": "LIB", "codigo_instancia": "S1", "title": "NOMBRE_DE_S1"},
                    {"libro_codigo": "LIB", "codigo_instancia": "S2", "title": "NOMBRE_DE_S2"},
                ],
                output_docx=str(output),
                repo=str(repo),
                python=sys.executable,
            )

            source = (root / "completo__combined_instances_source.tex").read_text(encoding="utf-8")
            self.assertTrue(output.exists())
            self.assertEqual(result["schema_version"], "latex_word_db_instances_combined_conversion_v1")
            self.assertIn(r"\subsection*{NOMBRE\_DE\_S1}", source)
            self.assertIn(r"\subsection*{NOMBRE\_DE\_S2}", source)
            self.assertNotIn("TITULO:", source)
            self.assertIn("Problema de S1", source)
            self.assertIn("Problema de S2", source)
            self.assertIn(r"\item[\textbf{1.}]", source)
            self.assertIn(r"\item[\textbf{31.}]", source)

    def test_recovers_pandoc_intermediate_when_word_postprocess_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "Editor_de_practicas"
            repo.mkdir()
            (repo / "latex_to_word.py").write_text(
                "\n".join(
                    [
                        "from pathlib import Path",
                        "import sys",
                        "out = Path(sys.argv[2])",
                        "out.with_name(out.stem + '__pandoc_intermedio.docx').write_bytes(b'intermediate')",
                        "raise SystemExit(1)",
                    ]
                ),
                encoding="utf-8",
            )
            input_tex = root / "source.tex"
            input_tex.write_text(r"\\item[\\textbf{1.}] Calcule $x$.", encoding="utf-8")
            output = root / "salida.docx"
            service = LatexWordService()
            job = service._build_word_job(
                output_docx=str(output),
                repo=str(repo),
                python=sys.executable,
                template="",
                style="Estilo_plantilla",
            )

            produced = service.run_tex_to_word(job=job, input_tex=input_tex, images_dir=None)

            self.assertEqual(produced, output)
            self.assertEqual(output.read_bytes(), b"intermediate")

    def test_prepare_db_images_creates_placeholder_for_missing_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "practice.docx"
            service = LatexWordService()
            problems = [
                {
                    "id": 9144,
                    "enunciado_latex": r"\item[\textbf{2.}] En la figura. [[Imagen=img-faltante]] A)$1$ B)$2$",
                }
            ]

            images_dir = service.prepare_images_dir_for_db(problems, output_docx=output)

            self.assertEqual(images_dir, root / "practice__db_images")
            placeholder = images_dir / "img-faltante.png"
            self.assertTrue(placeholder.exists())
            self.assertGreater(placeholder.stat().st_size, 0)

    def test_prepare_db_images_skips_when_source_has_no_image_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "practice.docx"
            service = LatexWordService()
            problems = [
                {
                    "id": 9144,
                    "enunciado_latex": r"\item[\textbf{2.}] En la figura. [[Imagen=img-faltante]] A)$1$ B)$2$",
                }
            ]

            images_dir = service.prepare_images_dir_for_db(
                problems,
                output_docx=output,
                source_text=r"\item[\textbf{2.}] Enunciado sin marcador de imagen. A)$1$ B)$2$",
            )

            self.assertIsNone(images_dir)
            self.assertFalse((root / "practice__db_images").exists())

    def test_db_image_markers_are_shortened_consistently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "practice.docx"
            service = LatexWordService()
            long_marker = "img-academia-nostradamus-semestral-2022-i_c106752eb1____ACADEMIA_c41f074aa4"
            problems = [
                {
                    "id": 9144,
                    "enunciado_latex": rf"\item[\textbf{{2.}}] En la figura. [[Imagen={long_marker}]] A)$1$ B)$2$",
                }
            ]

            source_text = service._build_scan_source_text_from_db(problems)
            images_dir = service.prepare_images_dir_for_db(problems, output_docx=output)
            marker = service._db_problem_markers(problems[0])[0]

            self.assertNotEqual(marker, long_marker)
            self.assertIn(f"[[Imagen={marker}]]", source_text)
            self.assertTrue((images_dir / f"{marker}.png").exists())
            self.assertLess(len(f"{marker}.png"), 110)

    def test_structured_db_image_markers_are_shortened(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "img-academia-nostradamus-semestral-2022-i_c106752eb1____ACADEMIA_c41f074aa4.png"
            image.write_bytes(b"image")
            output = root / "practice.docx"
            service = LatexWordService()
            problem = {
                "id": 9144,
                "numero_original": 2,
                "imagenes": [str(image)],
                "enunciado_latex": r"\item[\textbf{2.}] En la figura. [[Imagen=img-old]] A)$1$ B)$2$",
            }

            marker = service._db_problem_markers(problem)[0]
            source_text = service._build_scan_source_text_from_db([problem])
            images_dir = service.prepare_images_dir_for_db([problem], output_docx=output)

            self.assertIn(f"[[Imagen={marker}]]", source_text)
            self.assertTrue((images_dir / f"{marker}.png").exists())
            self.assertLess(len(f"{marker}.png"), 110)

    def test_library_catalog_prefers_word_folder_next_to_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "Libro Origen.pdf"
            pdf.write_bytes(b"%PDF")
            session = root / "sessions" / "semana_1.json"
            session.parent.mkdir()
            session.write_text(json.dumps({"output_text": r"\item[\textbf{1.}] Calcule $x$."}), encoding="utf-8")
            expected = root / "Word" / "LIB__semana_1.docx"
            expected.parent.mkdir()
            expected.write_bytes(b"docx")

            class Controller:
                def listar_libros(self, _db_name):
                    return [{"id": 1, "codigo": "LIB", "titulo": "Libro", "pdf_path": str(pdf), "workspace_dir": str(root)}]

                def listar_instancias_libro(self, _db_name, _book_id):
                    return [{"id": 2, "tipo": "semana_1", "titulo_practica": "Segmentos y angulos", "session_path": str(session)}]

            service = LatexWordService(controller=Controller(), file_url_resolver=lambda path: f"/file/{Path(path).name}")
            payload = service.list_sessions(db_name="demo")
            row = payload["books"][0]["instances"][0]

            self.assertEqual(Path(row["word_path"]).parent.name, "Word")
            self.assertEqual(Path(row["word_path"]).name, expected.name)
            self.assertTrue(row["word_exists"])
            self.assertEqual(row["word_url"], "/file/LIB__semana_1.docx")
            self.assertEqual(row["practice_title"], "Segmentos y angulos")


if __name__ == "__main__":
    unittest.main()
