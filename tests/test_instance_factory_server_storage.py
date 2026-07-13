from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from modulos.instance_factory.server_storage import ServerStorageResolver, is_windows_or_unc_path


class ServerFactoryStorageTests(unittest.TestCase):
    def test_windows_paths_are_not_server_safe(self) -> None:
        resolver = ServerStorageResolver(root="/srv/mathcontentstudio")
        self.assertTrue(is_windows_or_unc_path(r"E:\Banco\file.pdf"))
        self.assertEqual(resolver.classify_reference(r"E:\Banco\file.pdf"), "windows_or_unc")
        self.assertFalse(resolver.is_server_safe_reference(r"E:\Banco\file.pdf"))

    def test_artifact_key_round_trip_stays_under_root(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            resolver = ServerStorageResolver(root=root)
            path = resolver.artifact_path(
                book_code="Libro Demo",
                instance_code="Semana 1",
                kind="crops",
                parts=("crop 01.png",),
            )
            resolver.ensure_parent(path)
            path.write_text("ok", encoding="utf-8")

            key = resolver.asset_key_for_path(path)
            restored = resolver.resolve_asset_key(key)

        self.assertEqual(restored.name, "crop-01.png")
        self.assertIn("factory/instances/libro-demo/semana_1/staging/crops/crop-01.png", key)

    def test_rejects_parent_traversal_asset_key(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            resolver = ServerStorageResolver(root=raw)
            with self.assertRaises(ValueError):
                resolver.resolve_asset_key("../secret.env")

    def test_artifact_record_uses_asset_key_without_absolute_path_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            resolver = ServerStorageResolver(root=raw)
            path = resolver.artifact_path(book_code="B", instance_code="I", kind="records", parts=("r1.json",))
            resolver.ensure_parent(path)
            path.write_text("{}", encoding="utf-8")
            record = resolver.artifact_record(path, kind="record")

        self.assertEqual(record["schema_version"], "server_storage_artifact_v1")
        self.assertEqual(record["kind"], "record")
        self.assertIn("asset_key", record)
        self.assertNotIn("server_path", record)


if __name__ == "__main__":
    unittest.main()
