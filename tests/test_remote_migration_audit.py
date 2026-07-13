import unittest

from tools.audit_remote_migration_readiness import (
    AuditReport,
    PathColumnSummary,
    PathSample,
    classify_path,
    is_path_column,
    render_markdown_report,
)


class RemoteMigrationAuditTests(unittest.TestCase):
    def test_classify_windows_drive_path(self):
        result = classify_path(r"D:\Banco de Preguntas\book.pdf")
        self.assertEqual(result.category, "windows_drive")
        self.assertTrue(result.needs_rewrite)
        self.assertTrue(result.can_check_locally)

    def test_classify_server_path(self):
        result = classify_path("/srv/mathcontentstudio/library/book/source.pdf")
        self.assertEqual(result.category, "server_absolute")
        self.assertFalse(result.needs_rewrite)
        self.assertFalse(result.can_check_locally)

    def test_classify_url(self):
        result = classify_path("https://nexumathjf.com/file.pdf")
        self.assertEqual(result.category, "url")
        self.assertFalse(result.needs_rewrite)

    def test_path_column_detection(self):
        self.assertTrue(is_path_column("pdf_path"))
        self.assertTrue(is_path_column("ruta_imagen_solucion"))
        self.assertTrue(is_path_column("archivo_origen"))
        self.assertFalse(is_path_column("titulo"))

    def test_markdown_report_contains_core_sections(self):
        report = AuditReport(
            generated_at="2026-07-06T00:00:00+00:00",
            profile="local_mirror",
            database={
                "user": "postgres",
                "host": "127.0.0.1",
                "port": "5432",
                "db_name": "mathcontentstudio_local_mirror",
            },
            file_checks_enabled=True,
            table_counts={"problemas": 10, "libros_escaneo": None},
            missing_core_tables=["libros_escaneo"],
            path_columns=[
                PathColumnSummary(
                    table="libros_escaneo",
                    column="pdf_path",
                    total_non_empty=2,
                    windows_or_unc=1,
                    server_absolute=1,
                    url=0,
                    sampled=1,
                )
            ],
            path_samples=[
                PathSample(
                    table="libros_escaneo",
                    column="pdf_path",
                    row_ref="1",
                    value=r"D:\Banco de Preguntas\a.pdf",
                    category="windows_drive",
                    exists_locally=False,
                )
            ],
            missing_local_files=[
                PathSample(
                    table="libros_escaneo",
                    column="pdf_path",
                    row_ref="1",
                    value=r"D:\Banco de Preguntas\a.pdf",
                    category="windows_drive",
                    exists_locally=False,
                )
            ],
            warnings=[],
        )

        rendered = render_markdown_report(report)
        self.assertIn("# Pre-Migration Readiness Report", rendered)
        self.assertIn("Windows/UNC paths needing rewrite: `1`", rendered)
        self.assertIn("`libros_escaneo`", rendered)
        self.assertIn("Required Next Actions", rendered)

    def test_markdown_report_shows_when_file_checks_are_skipped(self):
        report = AuditReport(
            generated_at="2026-07-06T00:00:00+00:00",
            profile="local_mirror",
            database={
                "user": "postgres",
                "host": "127.0.0.1",
                "port": "5432",
                "db_name": "mathcontentstudio_local_mirror",
            },
            file_checks_enabled=False,
            table_counts={"problemas": 10},
            missing_core_tables=[],
            path_columns=[],
            path_samples=[],
            missing_local_files=[],
            warnings=[],
        )

        rendered = render_markdown_report(report)
        self.assertIn("Sampled local files missing: `not checked`", rendered)
        self.assertIn("Local file existence checks were skipped for this run.", rendered)


if __name__ == "__main__":
    unittest.main()
