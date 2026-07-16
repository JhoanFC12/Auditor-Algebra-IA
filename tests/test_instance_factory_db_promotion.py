from __future__ import annotations

import hashlib
import json
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


def _confirmed_bundle(asset: Path, *, record_id: str = "ready", bundle_id: str = "psb_ready_7") -> dict:
    digest = hashlib.sha256(asset.read_bytes()).hexdigest() if asset.exists() else hashlib.sha256(b"missing").hexdigest()
    suffix = "" if record_id == "ready" else f"_{record_id}"
    return {
        "schema_version": "problem_solution_promotion_bundle_v1",
        "bundle_id": bundle_id,
        "revision": 1,
        "bundle_fingerprint": "bundle-fingerprint-7",
        "status": "human_confirmed",
        "problem_ref": {
            "record_id": record_id,
            "crop_id": record_id,
            "exercise_set_id": "practice_04",
            "number_normalized": "7",
            "source_fingerprint": "problem-source-7",
        },
        "solutions": [
            {
                "solution_id": f"solution_7{suffix}",
                "solution_unit_id": f"solution_unit_7{suffix}",
                "relation_kind": "one_to_one",
                "variant_index": 1,
                "fragments": [
                    {
                        "fragment_id": f"fragment_7_1{suffix}",
                        "order": 1,
                        "page_number": 150,
                        "bbox_px": [10, 20, 200, 300],
                        "crop_path": str(asset),
                        "sha256": digest,
                    }
                ],
                "candidate_link_id": f"candidate_7{suffix}",
                "human_review_event_id": f"review_7{suffix}",
            }
        ],
        "document_relation": {"status": "same_document"},
        "human_review": {"status": "confirmed", "reviewer": "human", "confirmed_at": "2026-07-15T00:00:00Z"},
        "provenance": {"book_code": "aseuni-geometria", "instance_type": "semana_1"},
    }


class _FakePromotionCursor:
    def __init__(self, conn: "_FakePromotionConnection") -> None:
        self.conn = conn
        self._next = None

    def execute(self, query, params=None):
        sql = " ".join(str(query).split())
        self.conn.events.append((sql, params))
        for value in tuple(params or ()):
            if isinstance(value, str) and "solution_group_id" in value:
                self.conn.solution_payload_writes.append(json.loads(value))
                break
        if "INSERT INTO problemas" in sql:
            self.conn.pending_problem_write = True
            self._next = (101,)
        elif "INSERT INTO origenes" in sql:
            if self.conn.fail_origin:
                raise RuntimeError("simulated origin failure")
            self._next = (201,)

    def fetchone(self):
        row = self._next or (None,)
        self._next = None
        return row

    def close(self):
        self.conn.events.append(("CURSOR_CLOSE", None))


class _FakePromotionConnection:
    def __init__(self, *, problem_exists: bool = False, fail_origin: bool = False) -> None:
        self.events: list[tuple[str, object]] = []
        self.commits = 0
        self.rollbacks = 0
        self.problem_exists = problem_exists
        self.pending_problem_write = False
        self.fail_origin = fail_origin
        self.solution_payload_writes: list[list[dict]] = []

    def cursor(self):
        return _FakePromotionCursor(self)

    def commit(self):
        self.commits += 1
        if self.pending_problem_write:
            self.problem_exists = True
        self.pending_problem_write = False
        self.events.append(("COMMIT", None))

    def rollback(self):
        self.rollbacks += 1
        self.pending_problem_write = False
        self.events.append(("ROLLBACK", None))

    def close(self):
        self.events.append(("CONN_CLOSE", None))


class _FakePromotionDb:
    def __init__(self, conn: _FakePromotionConnection) -> None:
        self.conn = conn

    def get_connection(self, _db_name):
        return self.conn


class _FakePromotionController:
    def __init__(self, conn: _FakePromotionConnection) -> None:
        self.db = None
        self.conn = conn

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
        return 101 if self.conn.problem_exists else None


def _register_problem_solution_bundle(
    store: InstanceStagingStore,
    context: InstancePipelineContext,
    *,
    record_id: str,
    bundle: dict,
) -> dict:
    from modulos.instance_factory.problem_solution_linking import (
        bundle_fingerprint,
        generate_candidate_links,
        project_problem_units,
        review_candidate_link,
        unit_source_fingerprint,
    )

    record = store.get_record(record_id)
    if record is None:
        raise AssertionError(f"missing record: {record_id}")
    payload = json.loads(json.dumps(bundle))
    solution = dict(payload["solutions"][0])
    fragments = [dict(item) for item in solution.get("fragments") or []]
    if len(fragments) == 1:
        fragments[0].setdefault("fragment_role", "single")
    elif len(fragments) > 1:
        for index, fragment in enumerate(fragments):
            if index == 0:
                fragment.setdefault("fragment_role", "begin")
            elif index == len(fragments) - 1:
                fragment.setdefault("fragment_role", "end")
            else:
                fragment.setdefault("fragment_role", "middle")
    solution["fragments"] = fragments
    solution["continuation_complete"] = True
    payload["solutions"][0]["fragments"] = [dict(item) for item in fragments]
    payload["solutions"][0]["continuation_complete"] = True
    exercise_set_id = str(payload.get("problem_ref", {}).get("exercise_set_id") or "practice_04")
    solution_unit = {
        "solution_id": solution["solution_id"],
        "solution_unit_id": solution["solution_unit_id"],
        "book_code": context.book_code,
        "instance_type": context.instance_type,
        "exercise_set_id": exercise_set_id,
        "number_normalized": "7",
        "page_span": [150, 150],
        "continuation_complete": True,
        "variant_index": int(solution.get("variant_index") or 1),
        "provenance": {
            "source_version": "ingrid_solution_boxes_v1",
            "review_version": "human_box_review_v1",
        },
        "fragments": [dict(item) for item in fragments],
    }
    snapshot = store.problem_solution_snapshot()
    snapshot = store.upsert_solution_units([solution_unit], expected_revision=snapshot["revision"])
    saved_unit = next(
        item for item in snapshot["solution_units"] if item["solution_unit_id"] == solution_unit["solution_unit_id"]
    )
    problem_unit = project_problem_units([record], context)[0]
    generated = generate_candidate_links(
        [problem_unit],
        [saved_unit],
        pattern="separate_sections",
        structure={"source_mapping_confirmed": True, "section_pair_confirmed": True},
    )[0]
    reviewed = review_candidate_link(
        generated,
        action="confirm",
        problem_unit_id=record_id,
        reviewer="human",
        reviewed_at="2026-07-15T00:00:00Z",
    )
    existing_candidates = [
        dict(item)
        for item in snapshot.get("candidate_links") or []
        if item.get("candidate_link_id") != reviewed["candidate_link_id"]
    ]
    snapshot = store.write_candidate_links(
        [*existing_candidates, reviewed],
        expected_revision=snapshot["revision"],
    )
    snapshot = store.append_problem_solution_review(
        dict(reviewed["human_review"]),
        expected_revision=snapshot["revision"],
    )

    payload["problem_ref"] = {
        "record_id": record_id,
        "crop_id": record.crop_id,
        "book_code": context.book_code,
        "instance_type": context.instance_type,
        "exercise_set_id": exercise_set_id,
        "number_normalized": "7",
        "source_fingerprint": problem_unit["source_fingerprint"],
    }
    payload["solutions"][0].update(
        {
            "solution_unit_id": saved_unit["solution_unit_id"],
            "candidate_link_id": reviewed["candidate_link_id"],
            "human_review_event_id": reviewed["human_review"]["review_event_id"],
            "source_fingerprint": unit_source_fingerprint(saved_unit),
            "scope": {
                "book_code": context.book_code,
                "instance_type": context.instance_type,
                "exercise_set_id": exercise_set_id,
            },
            "provenance": dict(saved_unit.get("provenance") or {}),
        }
    )
    payload["human_review"] = {
        "status": "confirmed",
        "reviewer": "human",
        "reviewed_at": "2026-07-15T00:00:00Z",
    }
    payload["scope"] = {
        "book_code": context.book_code,
        "instance_type": context.instance_type,
        "exercise_set_id": exercise_set_id,
    }
    payload["provenance"] = {
        "book_code": context.book_code,
        "instance_type": context.instance_type,
        "exercise_set_id": exercise_set_id,
        "structure_map_version": "structure_map_v1",
        "box_review_version": "human_box_review_v1",
        "linker_version": "problem_solution_linker_v1",
    }
    payload["bundle_fingerprint"] = bundle_fingerprint(payload)
    return store.write_problem_solution_bundle(payload, expected_revision=snapshot["revision"])


def _store_with_registered_bundle(root: Path, bundle: dict) -> tuple[InstancePipelineContext, InstanceStagingStore]:

    crop = root / "problem_7.png"
    crop.write_bytes(b"problem-7")
    context = InstancePipelineContext(
        book_code="aseuni-geometria",
        instance_type="semana_1",
        project_name="ASEUNI",
        pdf_path="E:/Banco/ASEUNI.pdf",
        workspace_dir=str(root),
        db_name="mathcontentstudio_local_mirror",
        problem_solution_structure={"exercise_set_id": "practice_04"},
    )
    store = InstanceStagingStore(context, root=root / "staging")
    record = StagingProblemRecord(
        record_id="ready",
        crop_id="ready",
        crop_path=str(crop),
        status=StageStatus.READY,
        normalized={"latex_rendered_item": FINAL_ITEM},
        models={"ocr": "test-ocr", "figure_segmentation": "test-figure"},
        source={"page_number": 1, "bbox_px": [5, 6, 100, 120]},
    )
    store.upsert_record(record)
    _register_problem_solution_bundle(store, context, record_id="ready", bundle=bundle)
    return context, store


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
        self.assertNotIn("soluciones", payload)

    def test_build_problem_payload_adds_only_confirmed_visual_solution_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            asset = Path(tmp) / "solution_7.png"
            asset.write_bytes(b"visual-solution-7")
            context = InstancePipelineContext(
                book_code="aseuni-geometria",
                instance_type="semana_1",
                project_name="ASEUNI",
                pdf_path="E:/Banco/ASEUNI.pdf",
                workspace_dir=tmp,
            )
            record = StagingProblemRecord(
                record_id="ready",
                crop_id="ready",
                crop_path=str(Path(tmp) / "problem.png"),
                status=StageStatus.READY,
                normalized={"latex_rendered_item": FINAL_ITEM},
            )

            payload = build_problem_payload(
                record,
                context,
                materialize_images=False,
                problem_solution_bundle=_confirmed_bundle(asset),
                solution_asset_root=Path(tmp),
            )

        self.assertEqual(payload["bundle_id"], "psb_ready_7")
        self.assertEqual(payload["solution_count"], 1)
        self.assertEqual(len(payload["soluciones"]), 1)
        visual = payload["soluciones"][0]
        self.assertEqual(visual["solution_group_id"], "solution_7")
        self.assertEqual(visual["link"]["status"], "human_confirmed")
        self.assertEqual(visual["link"]["candidate_link_id"], "candidate_7")
        self.assertEqual(visual["bundle_id"], "psb_ready_7")
        self.assertEqual(visual["fragments"][0]["crop_sha256"], hashlib.sha256(b"visual-solution-7").hexdigest())
        self.assertTrue(visual["managed_group_id"].startswith("psg_"))

    def test_build_problem_payload_materializes_solution_assets_in_managed_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_asset = root / "source" / "solution_7.png"
            source_asset.parent.mkdir(parents=True)
            source_asset.write_bytes(b"visual-solution-7")
            context = InstancePipelineContext(
                book_code="aseuni-geometria",
                instance_type="semana_1",
                project_name="ASEUNI",
                workspace_dir=str(root),
            )
            record = StagingProblemRecord(
                record_id="ready",
                crop_id="ready",
                crop_path=str(root / "problem.png"),
                status=StageStatus.READY,
                normalized={"latex_rendered_item": FINAL_ITEM},
            )

            payload = build_problem_payload(
                record,
                context,
                problem_solution_bundle=_confirmed_bundle(source_asset),
                solution_asset_root=root,
            )

            visual = payload["soluciones"][0]
            managed_asset = Path(visual["images"][0])
            fragment = visual["fragments"][0]
            self.assertNotEqual(managed_asset, source_asset.resolve())
            self.assertEqual(managed_asset.parent.name, "db_solutions")
            self.assertTrue(managed_asset.is_file())
            self.assertEqual(managed_asset.read_bytes(), source_asset.read_bytes())
            self.assertEqual(fragment["crop_path"], str(managed_asset))
            self.assertEqual(fragment["source_crop_path"], str(source_asset.resolve()))
            self.assertTrue(fragment["managed"])
            self.assertEqual(fragment["sha256"], hashlib.sha256(source_asset.read_bytes()).hexdigest())

    def test_build_problem_payload_rejects_unconfirmed_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            asset = Path(tmp) / "solution_7.png"
            asset.write_bytes(b"visual-solution-7")
            bundle = _confirmed_bundle(asset)
            bundle["status"] = "pending_review"
            context = InstancePipelineContext(
                book_code="aseuni-geometria",
                instance_type="semana_1",
                workspace_dir=tmp,
            )
            record = StagingProblemRecord(
                record_id="ready",
                crop_id="ready",
                crop_path=str(Path(tmp) / "problem.png"),
                status=StageStatus.READY,
                normalized={"latex_rendered_item": FINAL_ITEM},
            )

            with self.assertRaises(db_promotion.BundlePreflightError) as raised:
                build_problem_payload(
                    record,
                    context,
                    materialize_images=False,
                    problem_solution_bundle=bundle,
                    solution_asset_root=Path(tmp),
                )

        self.assertTrue(any("not_confirmed" in issue for issue in raised.exception.issues))

    def test_legacy_update_does_not_write_or_clear_existing_solutions(self) -> None:
        class Cursor:
            def __init__(self) -> None:
                self.sql = ""
                self.params = None

            def execute(self, query, params=None):
                self.sql = " ".join(str(query).split())
                self.params = params

        context = InstancePipelineContext(book_code="aseuni-geometria", instance_type="semana_1")
        record = StagingProblemRecord(
            record_id="legacy",
            crop_id="legacy",
            crop_path="E:/Banco/legacy.png",
            status=StageStatus.READY,
            normalized={"latex_rendered_item": FINAL_ITEM},
        )
        payload = build_problem_payload(record, context, materialize_images=False)
        cursor = Cursor()

        db_promotion._update_problem(cursor, problem_id=101, payload=payload, cols={"soluciones", "enunciado_latex"})

        self.assertNotIn("soluciones =", cursor.sql.lower())
        self.assertNotIn("soluciones", payload)

    def test_bundle_update_preserves_legacy_solution_and_replaces_same_visual_id(self) -> None:
        class Cursor:
            def __init__(self) -> None:
                self.events: list[tuple[str, object]] = []
                self._next = None

            def execute(self, query, params=None):
                sql = " ".join(str(query).split())
                self.events.append((sql, params))
                if sql.startswith("SELECT soluciones"):
                    self._next = (
                        [
                            ["E:/legacy/solution_7.png"],
                            {"solution_group_id": "solution_7", "images": ["E:/stale.png"]},
                        ],
                    )

            def fetchone(self):
                row = self._next
                self._next = None
                return row

        with tempfile.TemporaryDirectory() as tmp:
            asset = Path(tmp) / "solution_7.png"
            asset.write_bytes(b"visual-solution-7")
            context = InstancePipelineContext(
                book_code="aseuni-geometria",
                instance_type="semana_1",
                workspace_dir=tmp,
            )
            record = StagingProblemRecord(
                record_id="ready",
                crop_id="ready",
                crop_path=str(Path(tmp) / "problem.png"),
                status=StageStatus.READY,
                normalized={"latex_rendered_item": FINAL_ITEM},
            )
            payload = build_problem_payload(
                record,
                context,
                materialize_images=False,
                problem_solution_bundle=_confirmed_bundle(asset),
                solution_asset_root=Path(tmp),
            )
            cursor = Cursor()

            db_promotion._update_problem(
                cursor,
                problem_id=101,
                payload=payload,
                cols={"enunciado_latex", "soluciones"},
            )

        self.assertTrue(cursor.events[0][0].startswith("SELECT soluciones"))
        update_sql, update_params = cursor.events[-1]
        self.assertIn("soluciones = %s::jsonb", update_sql)
        stored = next(json.loads(value) for value in update_params if isinstance(value, str) and "solution_group_id" in value)
        self.assertEqual(stored[0], ["E:/legacy/solution_7.png"])
        visual_rows = [item for item in stored if isinstance(item, dict) and item.get("solution_group_id") == "solution_7"]
        self.assertEqual(len(visual_rows), 1)
        self.assertNotEqual(visual_rows[0]["images"], ["E:/stale.png"])

    def test_merge_removes_retired_solution_from_same_managed_group_only(self) -> None:
        managed_group = "psg_problem_7"
        existing = [
            ["E:/legacy/solution_7.png"],
            {"solution_group_id": "solution_a", "managed_group_id": managed_group, "images": ["old-a.png"]},
            {"solution_group_id": "solution_b", "managed_group_id": managed_group, "images": ["retired-b.png"]},
            {"solution_group_id": "foreign", "managed_group_id": "psg_other", "images": ["foreign.png"]},
            {"solucion_latex": r"x=3", "autor": "legacy"},
        ]
        incoming = [
            {"solution_group_id": "solution_a", "managed_group_id": managed_group, "images": ["new-a.png"]},
        ]

        merged = db_promotion._merge_solution_payloads(existing, incoming)

        self.assertIn(["E:/legacy/solution_7.png"], merged)
        self.assertIn({"solucion_latex": r"x=3", "autor": "legacy"}, merged)
        self.assertIn({"solution_group_id": "foreign", "managed_group_id": "psg_other", "images": ["foreign.png"]}, merged)
        managed_rows = [item for item in merged if isinstance(item, dict) and item.get("managed_group_id") == managed_group]
        self.assertEqual(managed_rows, incoming)
        self.assertFalse(any(isinstance(item, dict) and item.get("solution_group_id") == "solution_b" for item in merged))

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

    def test_build_problem_payload_does_not_overwrite_distinct_image_with_same_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = InstancePipelineContext(
                book_code="aseuni-geometria",
                instance_type="semana_2",
                project_name="ASEUNI",
                pdf_path="E:/Banco/ASEUNI.pdf",
                workspace_dir=str(root),
            )
            first_segment = root / "segments" / "first" / "seg_01.png"
            second_segment = root / "segments" / "second" / "seg_01.png"
            first_segment.parent.mkdir(parents=True)
            second_segment.parent.mkdir(parents=True)
            first_segment.write_bytes(b"first-image")
            second_segment.write_bytes(b"second-image")

            def _record(record_id: str, image_path: Path) -> StagingProblemRecord:
                return StagingProblemRecord(
                    record_id=record_id,
                    crop_id=record_id,
                    crop_path=str(image_path),
                    status=StageStatus.READY,
                    normalized={"latex_rendered_item": FINAL_ITEM_WITH_IMAGE},
                    figure_segmentation={
                        "segments_total": 1,
                        "segments": [{"image_path": str(image_path)}],
                    },
                )

            first_payload = build_problem_payload(_record("first", first_segment), context)
            second_payload = build_problem_payload(_record("second", second_segment), context)

            first_stored = Path(first_payload["imagenes"][0])
            second_stored = Path(second_payload["imagenes"][0])
            self.assertEqual(first_stored.name, "img-15.png")
            self.assertTrue(second_stored.name.startswith("img-15_"))
            self.assertNotEqual(first_stored.name, second_stored.name)
            self.assertEqual(first_stored.read_bytes(), b"first-image")
            self.assertEqual(second_stored.read_bytes(), b"second-image")

    def test_build_problem_payload_uses_parent_linked_continuation_segments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main_crop = root / "crops" / "main.png"
            cont_crop = root / "crops" / "cont.png"
            segment = root / "segments" / "cont_seg.png"
            main_crop.parent.mkdir(parents=True)
            segment.parent.mkdir(parents=True)
            main_crop.write_bytes(b"main-crop")
            cont_crop.write_bytes(b"cont-crop")
            segment.write_bytes(b"continuation-segment")
            context = InstancePipelineContext(
                book_code="aseuni-geometria",
                instance_type="semana_2",
                project_name="ASEUNI",
                pdf_path="E:/Banco/ASEUNI.pdf",
                workspace_dir=str(root),
            )
            parent = StagingProblemRecord(
                record_id="record_15",
                crop_id="crop_15",
                crop_path=str(main_crop),
                status=StageStatus.READY,
                normalized={
                    "latex_rendered_item": FINAL_ITEM_WITH_IMAGE,
                    "continuaciones_fusionadas": [{"record_id": "record_15_cont"}],
                },
            )
            continuation = StagingProblemRecord(
                record_id="record_15_cont",
                crop_id="crop_15_cont",
                crop_path=str(cont_crop),
                status=StageStatus.READY,
                raw_ocr="A) 10 B) 20 C) 45 D) 30 E) 60",
                figure_segmentation={
                    "segments_total": 1,
                    "segments": [{"image_path": str(segment)}],
                },
            )

            payload = build_problem_payload(parent, context, all_records=[parent, continuation])

            self.assertEqual(len(payload["imagenes"]), 1)
            stored = Path(payload["imagenes"][0])
            self.assertEqual(stored.name, "img-15.png")
            self.assertEqual(stored.read_bytes(), b"continuation-segment")

    def test_dry_run_skips_continuation_as_independent_problem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_crop = root / "parent.png"
            cont_crop = root / "cont.png"
            parent_crop.write_bytes(b"parent")
            cont_crop.write_bytes(b"cont")
            context = InstancePipelineContext(
                book_code="aseuni-geometria",
                instance_type="semana_1",
                pdf_path="E:/Banco/ASEUNI.pdf",
                db_name="mathcontentstudio_local_mirror",
            )
            store = InstanceStagingStore(context, root=root / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="ready",
                        crop_id="ready",
                        crop_path=str(parent_crop),
                        status=StageStatus.READY,
                        normalized={
                            "latex_rendered_item": FINAL_ITEM,
                            "continuaciones_fusionadas": [{"record_id": "ready_cont"}],
                        },
                        models={"ocr": "test-ocr", "figure_segmentation": "test-figure"},
                        source={"page_number": 1, "bbox_px": [5, 6, 7, 8]},
                    ),
                    StagingProblemRecord(
                        record_id="ready_cont",
                        crop_id="ready_cont",
                        crop_path=str(cont_crop),
                        status=StageStatus.READY,
                        raw_ocr="[CONT.] A) 1 B) 2 C) 3 D) 4 E) 5",
                        normalized={
                            "continuacion": {
                                "es_continuacion": True,
                                "fusionar_con_anterior": True,
                                "parent_record_id": "ready",
                            }
                        },
                        models={"ocr": "test-ocr", "figure_segmentation": "test-figure"},
                        source={"page_number": 1, "bbox_px": [9, 10, 11, 12]},
                    ),
                ]
            )

            report = promote_staging_records_to_db(store, context, dry_run=True)

            statuses = {row["record_id"]: row["status"] for row in report["rows"]}
            self.assertEqual(statuses["ready"], "ready")
            self.assertEqual(statuses["ready_cont"], "skipped")
            self.assertEqual(sum(1 for status in statuses.values() if status == "ready"), 1)
            self.assertEqual(report["skipped"], 1)

    def test_dry_run_skips_angle_bracket_continuation_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent_crop = Path(tmp) / "parent.png"
            cont_crop = Path(tmp) / "cont.png"
            parent_crop.write_bytes(b"parent")
            cont_crop.write_bytes(b"cont")
            context = InstancePipelineContext(
                book_code="aseuni-geometria",
                instance_type="semana_1",
                pdf_path="E:/Banco/ASEUNI.pdf",
                db_name="mathcontentstudio_local_mirror",
            )
            store = InstanceStagingStore(context, root=Path(tmp) / "staging")
            store.upsert_many(
                [
                    StagingProblemRecord(
                        record_id="ready",
                        crop_id="ready",
                        crop_path=str(parent_crop),
                        status=StageStatus.READY,
                        normalized={"latex_rendered_item": FINAL_ITEM},
                        models={"ocr": "test-ocr", "figure_segmentation": "test-figure"},
                        source={"page_number": 1, "bbox_px": [5, 6, 7, 8]},
                    ),
                    StagingProblemRecord(
                        record_id="ready_cont",
                        crop_id="ready_cont",
                        crop_path=str(cont_crop),
                        status=StageStatus.READY,
                        raw_ocr="<CONT.> A) 1 B) 2 C) 3 D) 4 E) 5",
                        models={"ocr": "test-ocr", "figure_segmentation": "test-figure"},
                        source={"page_number": 1, "bbox_px": [9, 10, 11, 12]},
                    ),
                ]
            )

            report = promote_staging_records_to_db(store, context, dry_run=True)

            statuses = {row["record_id"]: row["status"] for row in report["rows"]}
            self.assertEqual(statuses["ready"], "ready")
            self.assertEqual(statuses["ready_cont"], "skipped")
            self.assertEqual(report["skipped"], 1)

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

    def test_dry_run_blocks_confirmed_bundle_when_solution_asset_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing_asset = root / "missing_solution.png"
            missing_asset.write_bytes(b"visual-solution-7")
            context, store = _store_with_registered_bundle(root, _confirmed_bundle(missing_asset))
            missing_asset.unlink()

            report = promote_staging_records_to_db(store, context, dry_run=True)

        self.assertEqual(report["skipped"], 1)
        self.assertEqual(report["errors"], 0)
        row = next(item for item in report["rows"] if item["record_id"] == "ready")
        self.assertEqual(row["status"], "skipped")
        self.assertEqual(row["bundle_id"], "psb_ready_7")
        self.assertTrue(any("missing_asset" in issue or "solution_asset_missing" in issue for issue in row["blocking_issues"]))

    def test_dry_run_blocks_solution_asset_when_registered_sha256_changed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset = root / "solution_7.png"
            asset.write_bytes(b"visual-solution-7")
            bundle = _confirmed_bundle(asset)
            context, store = _store_with_registered_bundle(root, bundle)
            asset.write_bytes(b"changed-after-review")

            report = promote_staging_records_to_db(store, context, dry_run=True)

        self.assertEqual(report["skipped"], 1)
        self.assertEqual(report["errors"], 0)
        row = next(item for item in report["rows"] if item["record_id"] == "ready")
        self.assertTrue(any("stale_asset" in issue or "sha256_mismatch" in issue for issue in row["blocking_issues"]))

    def test_upload_blocks_bundle_revoked_after_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset = root / "solution_7.png"
            asset.write_bytes(b"visual-solution-7")
            context, store = _store_with_registered_bundle(root, _confirmed_bundle(asset))
            conn = _FakePromotionConnection()

            def revoke_bundle(_profile, _target_db):
                live_bundle = store.bundle_for_record("ready")
                self.assertIsNotNone(live_bundle)
                snapshot = store.problem_solution_snapshot()
                store.remove_problem_solution_bundle(
                    live_bundle["bundle_id"],
                    expected_revision=snapshot["revision"],
                )
                return _FakePromotionDb(conn)

            with patch(
                "modulos.instance_factory.db_promotion._database_manager_from_profile",
                side_effect=revoke_bundle,
            ), patch(
                "modulos.instance_factory.db_promotion._transcriptor_controller_factory",
                return_value=_FakePromotionController(conn),
            ):
                report = promote_staging_records_to_db(store, context, dry_run=False)

        self.assertEqual(report["inserted"], 0)
        self.assertEqual(report["updated"], 0)
        self.assertEqual(report["bundles_promoted"], 0)
        self.assertEqual(report["errors"], 0)
        self.assertEqual(report["skipped"], 1)
        row = next(item for item in report["rows"] if item["record_id"] == "ready")
        self.assertTrue(any("revoked_after_preflight" in issue for issue in row["blocking_issues"]))
        self.assertFalse(any("INSERT INTO problemas" in sql for sql, _params in conn.events))

    def test_upload_blocks_valid_bundle_changed_after_preflight(self) -> None:
        from modulos.instance_factory.problem_solution_linking import bundle_fingerprint

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset = root / "solution_7.png"
            asset.write_bytes(b"visual-solution-7")
            context, store = _store_with_registered_bundle(root, _confirmed_bundle(asset))
            conn = _FakePromotionConnection()

            def change_bundle(_profile, _target_db):
                live = store.get_record("ready")
                self.assertIsNotNone(live)
                changed = json.loads(json.dumps(store.bundle_for_record("ready")))
                changed["revision"] = int(changed.get("revision") or 1) + 1
                changed["human_review"]["reviewer"] = "second-human"
                changed["bundle_fingerprint"] = bundle_fingerprint(changed)
                store.write_problem_solution_bundle(
                    changed,
                    expected_revision=store.problem_solution_snapshot()["revision"],
                )
                return _FakePromotionDb(conn)

            with patch(
                "modulos.instance_factory.db_promotion._database_manager_from_profile",
                side_effect=change_bundle,
            ), patch(
                "modulos.instance_factory.db_promotion._transcriptor_controller_factory",
                return_value=_FakePromotionController(conn),
            ):
                report = promote_staging_records_to_db(store, context, dry_run=False)

        self.assertEqual(report["bundles_promoted"], 0)
        self.assertEqual(report["errors"], 0)
        self.assertEqual(report["skipped"], 1)
        row = next(item for item in report["rows"] if item["record_id"] == "ready")
        self.assertTrue(any("changed_after_preflight" in issue for issue in row["blocking_issues"]))
        self.assertFalse(any("INSERT INTO problemas" in sql for sql, _params in conn.events))

    def test_replaying_confirmed_bundle_replaces_same_solution_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset = root / "solution_7.png"
            asset.write_bytes(b"visual-solution-7")
            context, store = _store_with_registered_bundle(root, _confirmed_bundle(asset))
            conn = _FakePromotionConnection()

            with patch(
                "modulos.instance_factory.db_promotion._database_manager_from_profile",
                return_value=_FakePromotionDb(conn),
            ), patch(
                "modulos.instance_factory.db_promotion._transcriptor_controller_factory",
                side_effect=lambda: _FakePromotionController(conn),
            ):
                first = promote_staging_records_to_db(store, context, dry_run=False)
                second = promote_staging_records_to_db(store, context, dry_run=False)

        self.assertEqual(first["inserted"], 1)
        self.assertEqual(first["bundles_promoted"], 1)
        self.assertEqual(first["solution_groups_promoted"], 1)
        self.assertEqual(second["updated"], 1)
        self.assertEqual(second["bundles_promoted"], 1)
        self.assertEqual(len(conn.solution_payload_writes), 2)
        self.assertEqual(conn.solution_payload_writes[0], conn.solution_payload_writes[1])
        self.assertEqual([item["solution_group_id"] for item in conn.solution_payload_writes[1]], ["solution_7"])
        promoted_row = next(item for item in second["rows"] if item["record_id"] == "ready")
        self.assertEqual(promoted_row["bundle_id"], "psb_ready_7")
        self.assertEqual(promoted_row["solution_count"], 1)
        origin_metadata = [
            json.loads(params[-1])
            for sql, params in conn.events
            if "INSERT INTO origenes" in sql and params
        ]
        self.assertTrue(origin_metadata)
        self.assertEqual(origin_metadata[-1]["problem_solution_bundle"]["bundle_id"], "psb_ready_7")
        self.assertEqual(origin_metadata[-1]["problem_solution_bundle"]["solution_count"], 1)

    def test_origin_failure_rolls_back_problem_and_solution_bundle_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset = root / "solution_7.png"
            asset.write_bytes(b"visual-solution-7")
            context, store = _store_with_registered_bundle(root, _confirmed_bundle(asset))
            conn = _FakePromotionConnection(fail_origin=True)

            with patch(
                "modulos.instance_factory.db_promotion._database_manager_from_profile",
                return_value=_FakePromotionDb(conn),
            ), patch(
                "modulos.instance_factory.db_promotion._transcriptor_controller_factory",
                return_value=_FakePromotionController(conn),
            ):
                report = promote_staging_records_to_db(store, context, dry_run=False)

        self.assertEqual(report["inserted"], 0)
        self.assertEqual(report["updated"], 0)
        self.assertEqual(report["bundles_promoted"], 0)
        self.assertEqual(report["errors"], 1)
        self.assertFalse(conn.problem_exists)
        self.assertGreaterEqual(conn.rollbacks, 1)
        sql_events = [sql for sql, _params in conn.events]
        problem_index = next(index for index, sql in enumerate(sql_events) if "INSERT INTO problemas" in sql)
        failure_rollback = next(index for index, sql in enumerate(sql_events[problem_index + 1 :], start=problem_index + 1) if sql == "ROLLBACK")
        self.assertGreater(failure_rollback, problem_index)

    def test_db_rollback_removes_only_assets_created_by_that_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reused_source = root / "solution_7.png"
            created_source = root / "solution_7_part_2.png"
            reused_source.write_bytes(b"reused-visual-solution")
            created_source.write_bytes(b"new-visual-solution")
            bundle = _confirmed_bundle(reused_source)
            bundle["solutions"][0]["fragments"].append(
                {
                    "fragment_id": "fragment_7_2",
                    "order": 2,
                    "page_number": 151,
                    "bbox_px": [10, 20, 200, 300],
                    "crop_path": str(created_source),
                    "sha256": hashlib.sha256(created_source.read_bytes()).hexdigest(),
                }
            )
            context, store = _store_with_registered_bundle(root, bundle)
            managed_dir = db_promotion._canonical_solution_dir(context)
            managed_dir.mkdir(parents=True, exist_ok=True)
            reused_target = managed_dir / "solution_7_fragment_7_1.png"
            created_target = managed_dir / "solution_7_fragment_7_2.png"
            reused_target.write_bytes(reused_source.read_bytes())
            conn = _FakePromotionConnection(fail_origin=True)

            with patch(
                "modulos.instance_factory.db_promotion._database_manager_from_profile",
                return_value=_FakePromotionDb(conn),
            ), patch(
                "modulos.instance_factory.db_promotion._transcriptor_controller_factory",
                return_value=_FakePromotionController(conn),
            ):
                report = promote_staging_records_to_db(store, context, dry_run=False)

            self.assertEqual(report["errors"], 1)
            self.assertTrue(reused_target.is_file())
            self.assertEqual(reused_target.read_bytes(), reused_source.read_bytes())
            self.assertFalse(created_target.exists())

    def test_invalid_bundle_does_not_block_independent_confirmed_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good_asset = root / "good_solution.png"
            good_asset.write_bytes(b"good-solution")
            missing_asset = root / "missing_solution.png"
            missing_asset.write_bytes(b"bad-solution-before-corruption")
            context = InstancePipelineContext(
                book_code="aseuni-geometria",
                instance_type="semana_1",
                project_name="ASEUNI",
                pdf_path="E:/Banco/ASEUNI.pdf",
                workspace_dir=str(root),
                db_name="mathcontentstudio_local_mirror",
                problem_solution_structure={"exercise_set_id": "practice_04"},
            )
            store = InstanceStagingStore(context, root=root / "staging")
            for source_index, record_id in enumerate(("bad", "good"), start=1):
                crop = root / f"{record_id}.png"
                crop.write_bytes(record_id.encode("utf-8"))
                store.upsert_record(
                    StagingProblemRecord(
                        record_id=record_id,
                        crop_id=record_id,
                        crop_path=str(crop),
                        status=StageStatus.READY,
                        normalized={"latex_rendered_item": FINAL_ITEM},
                        models={"ocr": "test-ocr", "figure_segmentation": "test-figure"},
                        source={"page_number": source_index, "bbox_px": [5, 6, 100, 120]},
                    )
                )
            for record_id, asset in (("bad", missing_asset), ("good", good_asset)):
                bundle = _confirmed_bundle(asset, record_id=record_id, bundle_id=f"psb_{record_id}")
                _register_problem_solution_bundle(
                    store,
                    context,
                    record_id=record_id,
                    bundle=bundle,
                )
            missing_asset.unlink()
            conn = _FakePromotionConnection()

            with patch(
                "modulos.instance_factory.db_promotion._database_manager_from_profile",
                return_value=_FakePromotionDb(conn),
            ), patch(
                "modulos.instance_factory.db_promotion._transcriptor_controller_factory",
                return_value=_FakePromotionController(conn),
            ):
                report = promote_staging_records_to_db(store, context, dry_run=False)

        statuses = {row["record_id"]: row["status"] for row in report["rows"]}
        self.assertEqual(statuses["bad"], "skipped")
        self.assertEqual(statuses["good"], "inserted")
        self.assertEqual(report["skipped"], 1)
        self.assertEqual(report["inserted"], 1)
        self.assertEqual(report["errors"], 0)

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

            with patch.object(store, "repair_detected_continuation_links", wraps=store.repair_detected_continuation_links) as repair_mock, patch(
                "modulos.instance_factory.db_promotion._database_manager_from_profile",
                return_value=FakeDb(conn),
            ), patch("modulos.instance_factory.db_promotion._transcriptor_controller_factory", return_value=FakeController()):
                report = promote_staging_records_to_db(store, context, dry_run=False)

        self.assertEqual(report["inserted"], 1)
        self.assertEqual(report["errors"], 0)
        self.assertEqual(repair_mock.call_count, 1)
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
