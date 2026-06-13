from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import modulos.instance_factory.db_promotion as db_promotion
from modulos.instance_factory.db_promotion import build_problem_payload, promote_staging_records_to_db
from modulos.instance_factory.models import InstancePipelineContext, StageStatus, StagingProblemRecord
from modulos.instance_factory.staging import InstanceStagingStore


FINAL_ITEM = (
    r"\item[\textbf{7.}] [[curso=Geometria]] [[tema=Triangulos]] "
    r"[[Estado=sin_revisar]] [[Clave=C]] Calcule $x$. £A)$1$æB)$2$æC)$3$£D)$4$ææE)$5$£"
)


FINAL_ITEM_WITH_IMAGE = (
    r"\item[\textbf{15.}] [[curso=Geometria]] [[tema=Triangulos]] "
    r"[[Estado=sin_revisar]] [[Clave=E]] Calcule $x$. [[Imagen=img-15]] "
    r"A)$10$ B)$20$ C)$45$ D)$30$ E)$60$"
)


class InstanceFactoryDbPromotionTests(unittest.TestCase):
    def test_build_problem_payload_uses_final_latex_and_context(self) -> None:
        context = InstancePipelineContext(
            book_code="aseuni-geometria",
            instance_type="semana_1",
            project_name="ASEUNI",
            pdf_path="E:/Banco/ASEUNI.pdf",
        )
        record = StagingProblemRecord(
            record_id="crop_001",
            crop_id="crop_001",
            crop_path="E:/Banco/crop_001.png",
            status=StageStatus.READY,
            normalized={"latex_rendered_item": FINAL_ITEM},
        )

        payload = build_problem_payload(record, context)

        self.assertEqual(payload["numero_original"], 7)
        self.assertEqual(payload["archivo_origen"], "ASEUNI.pdf")
        self.assertEqual(payload["curso"], "Geometria")
        self.assertEqual(payload["tema"], "Triangulos")
        self.assertEqual(payload["respuesta_correcta"], "C")
        self.assertEqual(payload["libro_codigo"], "aseuni-geometria")
        self.assertEqual(payload["instancia_tipo"], "semana_1")
        self.assertNotIn("[[curso=", payload["enunciado_latex"])

    def test_build_problem_payload_materializes_image_with_final_marker_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            segment_dir = root / "segments" / "record_15"
            segment_dir.mkdir(parents=True)
            segment = segment_dir / "seg_01.png"
            segment.write_bytes(b"image-15")
            crop = root / "crops" / "crop_15.png"
            crop.parent.mkdir(parents=True)
            crop.write_bytes(b"crop-15")
            context = InstancePipelineContext(
                book_code="aseuni-geometria",
                instance_type="semana_2",
                project_name="ASEUNI",
                pdf_path="E:/Banco/ASEUNI.pdf",
                workspace_dir=str(root),
            )
            record = StagingProblemRecord(
                record_id="record_15",
                crop_id="crop_15",
                crop_path=str(crop),
                status=StageStatus.READY,
                normalized={"latex_rendered_item": FINAL_ITEM_WITH_IMAGE},
                figure_segmentation={
                    "segments_total": 1,
                    "segments": [{"image_path": str(segment)}],
                },
            )

            payload = build_problem_payload(record, context)

            self.assertEqual(payload["numero_original"], 15)
            self.assertEqual(len(payload["imagenes"]), 1)
            stored = Path(payload["imagenes"][0])
            self.assertEqual(stored.name, "img-15.png")
            self.assertEqual(stored.read_bytes(), b"image-15")
            self.assertEqual(Path(payload["ruta_carpeta"]).name, "db_images")

    def test_dry_run_reports_ready_and_skips_incomplete_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ready_crop = Path(tmp) / "ready.png"
            missing_crop = Path(tmp) / "missing.png"
            ready_crop.write_bytes(b"fake-png")
            missing_crop.write_bytes(b"fake-png")
            context = InstancePipelineContext(
                book_code="aseuni-geometria",
                instance_type="semana_1",
                pdf_path="E:/Banco/ASEUNI.pdf",
                db_name="mathcontentstudio_local_mirror",
            )
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            store.upsert_record(
                StagingProblemRecord(
                    record_id="ready",
                    crop_id="ready",
                    crop_path=str(ready_crop),
                    status=StageStatus.READY,
                    normalized={"latex_rendered_item": FINAL_ITEM},
                    models={"ocr": "test-ocr", "figure_segmentation": "test-figure"},
                    source={"page_number": 1, "bbox_px": [5, 6, 7, 8]},
                )
            )
            store.upsert_record(
                StagingProblemRecord(
                    record_id="missing_final",
                    crop_id="missing_final",
                    crop_path=str(missing_crop),
                    status=StageStatus.READY,
                    normalized={"numero": "8"},
                    models={"ocr": "test-ocr", "figure_segmentation": "test-figure"},
                    source={"page_number": 1, "bbox_px": [1, 2, 3, 4]},
                )
            )

            report = promote_staging_records_to_db(store, context, dry_run=True)

            self.assertTrue(report["dry_run"])
            statuses = {row["record_id"]: row["status"] for row in report["rows"]}
            self.assertEqual(statuses["ready"], "ready")
            self.assertEqual(statuses["missing_final"], "skipped")
            self.assertEqual(report["skipped"], 1)

    def test_upload_commits_schema_before_rows_and_serializes_origin(self) -> None:
        class FakeCursor:
            def __init__(self, conn: "FakeConnection") -> None:
                self.conn = conn
                self._next = None

            def execute(self, query, params=None):
                sql = " ".join(str(query).split())
                self.conn.events.append((sql, params))
                if "INSERT INTO problemas" in sql:
                    self._next = (101,)
                elif "INSERT INTO origenes" in sql:
                    self._next = (201,)

            def fetchone(self):
                row = self._next or (None,)
                self._next = None
                return row

            def close(self):
                self.conn.events.append(("CURSOR_CLOSE", None))

        class FakeConnection:
            def __init__(self) -> None:
                self.events = []
                self.commits = 0
                self.rollbacks = 0

            def cursor(self):
                return FakeCursor(self)

            def commit(self):
                self.commits += 1
                self.events.append(("COMMIT", None))

            def rollback(self):
                self.rollbacks += 1
                self.events.append(("ROLLBACK", None))

            def close(self):
                self.events.append(("CONN_CLOSE", None))

        class FakeDb:
            def __init__(self, conn: FakeConnection) -> None:
                self.conn = conn

            def get_connection(self, _db_name):
                return self.conn

        class FakeController:
            def __init__(self) -> None:
                self.db = None

            def _asegurar_tabla_problemas(self, conn):
                conn.events.append(("ENSURE_PROBLEMAS", None))

            def _obtener_columnas_problemas(self, _conn):
                return {
                    "numero_original",
                    "archivo_origen",
                    "enunciado_latex",
                    "imagenes",
                    "ruta_carpeta",
                    "consistencia_matematica",
                    "curso",
                    "tema",
                    "respuesta_correcta",
                    "tipo_problema",
                    "soluciones",
                    "libro_codigo",
                    "codigo_instancia",
                }

            def _extract_item_storage_fields(self, item_latex):
                return db_promotion._extract_item_storage_fields(item_latex)

            def normalizar_item_una_linea(self, item_latex):
                return db_promotion._normalizar_item_una_linea(item_latex)

            def parsear_numero_original(self, item_latex):
                return db_promotion._parsear_numero_original(item_latex)

            def _find_existing_problem_id(self, *_args, **_kwargs):
                return None

        with tempfile.TemporaryDirectory() as tmp:
            crop = Path(tmp) / "ready.png"
            crop.write_bytes(b"fake-png")
            context = InstancePipelineContext(
                book_code="aseuni-geometria",
                instance_type="semana_1",
                pdf_path="E:/Banco/ASEUNI.pdf",
                db_name="mathcontentstudio_local_mirror",
            )
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            store.upsert_record(
                StagingProblemRecord(
                    record_id="ready",
                    crop_id="ready",
                    crop_path=str(crop),
                    status=StageStatus.READY,
                    normalized={"latex_rendered_item": FINAL_ITEM},
                    models={"ocr": "test-ocr", "figure_segmentation": "test-figure"},
                    source={"page_number": 1, "bbox_px": [5, 6, 7, 8]},
                )
            )
            conn = FakeConnection()

            with patch("database.connection.DatabaseManager.from_profile", return_value=FakeDb(conn)), patch(
                "modulos.modulo0_transcriptor.controlador_transcriptor.TranscriptorController",
                return_value=FakeController(),
            ):
                report = promote_staging_records_to_db(store, context, dry_run=False)

        self.assertEqual(report["inserted"], 1)
        self.assertEqual(report["errors"], 0)
        sql_events = [event[0] for event in conn.events]
        first_problem_insert = next(i for i, sql in enumerate(sql_events) if "INSERT INTO problemas" in sql)
        self.assertGreaterEqual(sql_events[:first_problem_insert].count("COMMIT"), 2)
        advisory_index = next(i for i, sql in enumerate(sql_events) if "pg_advisory_xact_lock" in sql)
        self.assertLess(advisory_index, first_problem_insert)
        origin_schema_creates = [sql for sql in sql_events if "CREATE TABLE IF NOT EXISTS origenes" in sql]
        self.assertEqual(len(origin_schema_creates), 1)
        self.assertGreaterEqual(conn.commits, 3)
        self.assertEqual(conn.rollbacks, 0)

    def test_deadlock_sqlstate_is_transient_for_retry(self) -> None:
        class FakeDeadlock(Exception):
            pgcode = "40P01"

        self.assertTrue(db_promotion._is_transient_promotion_error(FakeDeadlock("deadlock")))


if __name__ == "__main__":
    unittest.main()
