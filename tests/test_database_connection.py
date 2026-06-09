from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.connection import DatabaseManager


class DatabaseManagerTests(unittest.TestCase):
    def _env(self, **overrides: str) -> dict[str, str]:
        base = {
            "DB_HOST": "cloud.example.com",
            "DB_PORT": "5432",
            "DB_NAME": "mathcontentstudio",
            "DB_USER": "mathcontentstudio_app",
            "DB_PASSWORD": "supersecret",
            "DB_SSLMODE": "require",
            "DB_CONNECT_TIMEOUT": "9",
        }
        base.update(overrides)
        return base

    def test_get_connection_uses_tls_and_timeout_kwargs(self) -> None:
        fake_conn = object()
        with (
            patch.dict(os.environ, self._env(DB_SSLROOTCERT="C:/certs/root.pem"), clear=False),
            patch("database.connection.psycopg2.connect", return_value=fake_conn) as mocked_connect,
        ):
            db = DatabaseManager()
            conn = db.get_connection("")

        self.assertIs(conn, fake_conn)
        self.assertEqual(db.db_name, "mathcontentstudio")
        self.assertEqual(db.sslmode, "require")
        self.assertEqual(db.connect_timeout, 9)
        kwargs = mocked_connect.call_args.kwargs
        self.assertEqual(kwargs["dbname"], "mathcontentstudio")
        self.assertEqual(kwargs["host"], "cloud.example.com")
        self.assertEqual(kwargs["port"], "5432")
        self.assertEqual(kwargs["user"], "mathcontentstudio_app")
        self.assertEqual(kwargs["password"], "supersecret")
        self.assertEqual(kwargs["sslmode"], "require")
        self.assertEqual(kwargs["connect_timeout"], 9)
        self.assertEqual(kwargs["sslrootcert"], "C:/certs/root.pem")

    def test_listar_bases_datos_returns_only_configured_db(self) -> None:
        fake_conn = MagicMock()
        with (
            patch.dict(os.environ, self._env(), clear=False),
            patch("database.connection.psycopg2.connect", return_value=fake_conn) as mocked_connect,
        ):
            db = DatabaseManager()
            dbs = db.listar_bases_datos()

        self.assertEqual(dbs, ["mathcontentstudio"])
        fake_conn.close.assert_called_once()
        self.assertEqual(mocked_connect.call_args.kwargs["dbname"], "mathcontentstudio")

    def test_listar_bases_datos_returns_empty_when_connection_fails(self) -> None:
        with (
            patch.dict(os.environ, self._env(), clear=False),
            patch("database.connection.psycopg2.connect", side_effect=RuntimeError("boom")),
            patch("builtins.print"),
        ):
            db = DatabaseManager()
            dbs = db.listar_bases_datos()

        self.assertEqual(dbs, [])


if __name__ == "__main__":
    unittest.main()
