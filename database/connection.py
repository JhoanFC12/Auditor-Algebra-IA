from __future__ import annotations

import os
import time
from pathlib import Path

import psycopg2
from utils.runtime_log import get_logger

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency
    load_dotenv = None  # type: ignore[assignment]


_PLACEHOLDER_VALUES = {"", "replace_me", "changeme", "xxx", "your_password_here"}


def _load_dotenv_if_present() -> None:
    if load_dotenv is None:
        return
    root = Path(__file__).resolve().parents[1]
    preferred = root / ".env.local"
    fallback = root / ".env"
    if preferred.exists():
        load_dotenv(preferred, override=False)
        return
    if fallback.exists():
        load_dotenv(fallback, override=False)


def _read_env(name: str, default: str) -> str:
    value = str(os.getenv(name, "") or "").strip()
    if value.lower() in _PLACEHOLDER_VALUES:
        return default
    return value or default


def _read_int_env(name: str, default: int) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except Exception:
        return default
    return value if value > 0 else default


def _read_nonneg_int_env(name: str, default: int) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except Exception:
        return default
    return value if value >= 0 else default


def _is_transient_db_connection_error(exc: Exception) -> bool:
    text = str(exc or "").strip().lower()
    if not text:
        return False
    patterns = (
        "server closed the connection unexpectedly",
        "connection unexpectedly",
        "connection refused",
        "connection reset by peer",
        "could not connect to server",
        "could not receive data from server",
        "ssl syscall error: eof detected",
        "terminating connection",
        "connection timed out",
        "timeout expired",
        "network is unreachable",
    )
    return any(token in text for token in patterns)


_load_dotenv_if_present()
LOGGER = get_logger("db")

DEFAULT_SHARED_DB_NAME = "mathcontentstudio"
DEFAULT_LOCAL_MIRROR_DB_NAME = "mathcontentstudio_local_mirror"


def read_db_profile_config(profile_name: str) -> dict[str, str | int]:
    profile_key = str(profile_name or "").strip().lower()
    if profile_key in {"", "default", "active", "current"}:
        return {
            "profile": str(os.getenv("DB_PROFILE", "") or "").strip().lower() or "active",
            "host": _read_env("DB_HOST", "localhost"),
            "port": _read_env("DB_PORT", "5432"),
            "db_name": _read_env("DB_NAME", DEFAULT_SHARED_DB_NAME),
            "user": _read_env("DB_USER", "postgres"),
            "password": _read_env("DB_PASSWORD", ""),
            "sslmode": _read_env("DB_SSLMODE", "require") or "require",
            "sslrootcert": str(os.getenv("DB_SSLROOTCERT", "")).strip(),
            "connect_timeout": _read_int_env("DB_CONNECT_TIMEOUT", 5),
            "idle_in_tx_timeout_ms": _read_nonneg_int_env("DB_IDLE_IN_TX_TIMEOUT_MS", 0),
            "connect_retries": _read_nonneg_int_env("DB_CONNECT_RETRIES", 2),
            "connect_retry_delay_ms": _read_nonneg_int_env("DB_CONNECT_RETRY_DELAY_MS", 350),
            "keepalives_idle": _read_nonneg_int_env("DB_KEEPALIVES_IDLE", 30),
            "keepalives_interval": _read_nonneg_int_env("DB_KEEPALIVES_INTERVAL", 10),
            "keepalives_count": _read_nonneg_int_env("DB_KEEPALIVES_COUNT", 5),
        }

    if profile_key == "cloud":
        prefix = "DB_CLOUD_"
        default_name = _read_env("DB_SHARED_NAME", _read_env("DB_NAME", DEFAULT_SHARED_DB_NAME)) or DEFAULT_SHARED_DB_NAME
    elif profile_key == "local_mirror":
        prefix = "DB_LOCAL_MIRROR_"
        default_name = _read_env(f"{prefix}NAME", DEFAULT_LOCAL_MIRROR_DB_NAME) or DEFAULT_LOCAL_MIRROR_DB_NAME
    else:
        raise ValueError(f"Perfil de BD no soportado: {profile_name}")

    return {
        "profile": profile_key,
        "host": _read_env(f"{prefix}HOST", _read_env("DB_HOST", "localhost")),
        "port": _read_env(f"{prefix}PORT", _read_env("DB_PORT", "5432")),
        "db_name": _read_env(f"{prefix}NAME", default_name) or default_name,
        "user": _read_env(f"{prefix}USER", _read_env("DB_USER", "postgres")),
        "password": _read_env(f"{prefix}PASSWORD", _read_env("DB_PASSWORD", "")),
        "sslmode": _read_env(f"{prefix}SSLMODE", _read_env("DB_SSLMODE", "disable")) or "disable",
        "sslrootcert": str(os.getenv(f"{prefix}SSLROOTCERT", "") or os.getenv("DB_SSLROOTCERT", "")).strip(),
        "connect_timeout": _read_int_env("DB_CONNECT_TIMEOUT", 5),
        "idle_in_tx_timeout_ms": _read_nonneg_int_env(
            f"{prefix}IDLE_IN_TX_TIMEOUT_MS", _read_nonneg_int_env("DB_IDLE_IN_TX_TIMEOUT_MS", 0)
        ),
        "connect_retries": _read_nonneg_int_env(f"{prefix}CONNECT_RETRIES", _read_nonneg_int_env("DB_CONNECT_RETRIES", 2)),
        "connect_retry_delay_ms": _read_nonneg_int_env(
            f"{prefix}CONNECT_RETRY_DELAY_MS", _read_nonneg_int_env("DB_CONNECT_RETRY_DELAY_MS", 350)
        ),
        "keepalives_idle": _read_nonneg_int_env(
            f"{prefix}KEEPALIVES_IDLE", _read_nonneg_int_env("DB_KEEPALIVES_IDLE", 30)
        ),
        "keepalives_interval": _read_nonneg_int_env(
            f"{prefix}KEEPALIVES_INTERVAL", _read_nonneg_int_env("DB_KEEPALIVES_INTERVAL", 10)
        ),
        "keepalives_count": _read_nonneg_int_env(
            f"{prefix}KEEPALIVES_COUNT", _read_nonneg_int_env("DB_KEEPALIVES_COUNT", 5)
        ),
    }


class DatabaseManager:
    def __init__(
        self,
        user: str | None = None,
        password: str | None = None,
        host: str | None = None,
        port: str | int | None = None,
        db_name: str | None = None,
        sslmode: str | None = None,
        connect_timeout: int | None = None,
        sslrootcert: str | None = None,
        idle_in_tx_timeout_ms: int | None = None,
        connect_retries: int | None = None,
        connect_retry_delay_ms: int | None = None,
        keepalives_idle: int | None = None,
        keepalives_interval: int | None = None,
        keepalives_count: int | None = None,
    ):
        self.user = str(user or _read_env("DB_USER", "postgres")).strip()
        self.password = str(password if password is not None else _read_env("DB_PASSWORD", ""))
        self.host = str(host or _read_env("DB_HOST", "localhost")).strip()
        self.port = str(port or _read_env("DB_PORT", "5432")).strip()
        self.db_name = str(db_name or _read_env("DB_NAME", "mathcontentstudio")).strip()
        self.sslmode = str(sslmode or _read_env("DB_SSLMODE", "require")).strip() or "require"
        self.connect_timeout = int(connect_timeout or _read_int_env("DB_CONNECT_TIMEOUT", 5))
        self.sslrootcert = str(sslrootcert or os.getenv("DB_SSLROOTCERT", "")).strip()
        self.idle_in_tx_timeout_ms = int(
            _read_nonneg_int_env("DB_IDLE_IN_TX_TIMEOUT_MS", 0)
            if idle_in_tx_timeout_ms is None
            else max(int(idle_in_tx_timeout_ms), 0)
        )
        self.connect_retries = int(
            _read_nonneg_int_env("DB_CONNECT_RETRIES", 2)
            if connect_retries is None
            else max(int(connect_retries), 1)
        )
        self.connect_retry_delay_ms = int(
            _read_nonneg_int_env("DB_CONNECT_RETRY_DELAY_MS", 350)
            if connect_retry_delay_ms is None
            else max(int(connect_retry_delay_ms), 0)
        )
        self.keepalives_idle = int(
            _read_nonneg_int_env("DB_KEEPALIVES_IDLE", 30)
            if keepalives_idle is None
            else max(int(keepalives_idle), 0)
        )
        self.keepalives_interval = int(
            _read_nonneg_int_env("DB_KEEPALIVES_INTERVAL", 10)
            if keepalives_interval is None
            else max(int(keepalives_interval), 0)
        )
        self.keepalives_count = int(
            _read_nonneg_int_env("DB_KEEPALIVES_COUNT", 5)
            if keepalives_count is None
            else max(int(keepalives_count), 0)
        )
        self.connection = None

    @classmethod
    def from_profile(cls, profile_name: str, *, db_name: str | None = None):
        config = read_db_profile_config(profile_name)
        return cls(
            user=str(config["user"]),
            password=str(config["password"]),
            host=str(config["host"]),
            port=str(config["port"]),
            db_name=str(db_name or config["db_name"]),
            sslmode=str(config["sslmode"]),
            connect_timeout=int(config["connect_timeout"]),
            sslrootcert=str(config["sslrootcert"]),
            idle_in_tx_timeout_ms=int(config["idle_in_tx_timeout_ms"]),
            connect_retries=int(config["connect_retries"]),
            connect_retry_delay_ms=int(config["connect_retry_delay_ms"]),
            keepalives_idle=int(config["keepalives_idle"]),
            keepalives_interval=int(config["keepalives_interval"]),
            keepalives_count=int(config["keepalives_count"]),
        )

    def _connect(self, db_name: str | None = None):
        target_db = str(db_name or self.db_name).strip() or self.db_name
        LOGGER.info("db_connect_start host=%s port=%s db=%s sslmode=%s", self.host, self.port, target_db, self.sslmode)
        options_parts = ["-c client_encoding=utf8"]
        if int(self.idle_in_tx_timeout_ms or 0) > 0:
            options_parts.append(f"-c idle_in_transaction_session_timeout={int(self.idle_in_tx_timeout_ms)}")
        kwargs = {
            "dbname": target_db,
            "user": self.user,
            "password": self.password,
            "host": self.host,
            "port": self.port,
            "options": " ".join(options_parts),
            "sslmode": self.sslmode,
            "connect_timeout": self.connect_timeout,
            "keepalives": 1,
        }
        if int(self.keepalives_idle or 0) > 0:
            kwargs["keepalives_idle"] = int(self.keepalives_idle)
        if int(self.keepalives_interval or 0) > 0:
            kwargs["keepalives_interval"] = int(self.keepalives_interval)
        if int(self.keepalives_count or 0) > 0:
            kwargs["keepalives_count"] = int(self.keepalives_count)
        if self.sslrootcert:
            kwargs["sslrootcert"] = self.sslrootcert
        conn = psycopg2.connect(**kwargs)
        LOGGER.info("db_connect_ok host=%s port=%s db=%s", self.host, self.port, target_db)
        return conn

    def listar_bases_datos(self):
        """
        Valida la conexion contra la base configurada y expone solo esa base.
        """
        try:
            conn = self.get_connection(self.db_name)
            conn.close()
            LOGGER.info("db_listar_bases_ok db=%s", self.db_name)
            return [self.db_name]
        except Exception as e:
            LOGGER.exception("db_listar_bases_error db=%s err=%s", self.db_name, e)
            print(f"Error listando BDs: {repr(e)}")
            return []

    def get_connection(self, db_name):
        """
        Devuelve una conexion activa a una base de datos especifica.
        """
        target_db = str(db_name or self.db_name).strip() or self.db_name
        attempts = max(int(self.connect_retries or 1), 1)
        delay_s = max(float(self.connect_retry_delay_ms or 0) / 1000.0, 0.0)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return self._connect(target_db)
            except Exception as e:
                last_error = e
                transient = _is_transient_db_connection_error(e)
                should_retry = transient and attempt < attempts
                if should_retry:
                    LOGGER.warning(
                        "db_get_connection_retry host=%s port=%s db=%s attempt=%s/%s err=%s",
                        self.host,
                        self.port,
                        target_db,
                        attempt,
                        attempts,
                        e,
                    )
                    if delay_s > 0:
                        time.sleep(delay_s)
                    continue
                LOGGER.exception("db_get_connection_error db=%s err=%s", target_db, e)
                raise ConnectionError(f"Error conectando a '{target_db}': {repr(e)}") from e
        raise ConnectionError(f"Error conectando a '{target_db}': {repr(last_error)}")
