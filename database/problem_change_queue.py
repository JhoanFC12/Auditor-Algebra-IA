from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from database.connection import DatabaseManager
from utils.runtime_log import get_logger


LOGGER = get_logger("problem_change_queue")
PUBLISHER_TAG = str(os.getenv("PROBLEM_CHANGE_PUBLISHER_TAG", "") or os.getenv("COMPUTERNAME", "") or "auditor-ia-local").strip()
DEFAULT_REMOTE_LIBRARY_ROOT = str(os.getenv("MATH_BANK_REMOTE_LIBRARY_ROOT", "") or "/srv/mathcontentstudio/library").strip() or "/srv/mathcontentstudio/library"

EDITABLE_FIELDS: tuple[str, ...] = (
    "numero_original",
    "archivo_origen",
    "enunciado_latex",
    "consistencia_matematica",
    "imagenes",
    "libro_codigo",
    "codigo_instancia",
    "tiene_clave",
    "tiene_solucion",
    "curso",
    "tema",
    "subtema",
    "respuesta_correcta",
    "ruta_imagen_solucion",
    "tema_id",
    "subtema_id",
    "respuesta",
    "nivel_dificultad",
    "auditoria_razon",
    "soluciones",
    "reglas_sugeridas_ia",
    "conceptos_ia",
    "libro_id",
    "instancia_id",
)

BOOK_SYNC_FIELDS: tuple[str, ...] = (
    "codigo",
    "titulo",
    "autor",
    "editorial",
    "edicion",
    "curso",
    "tema_base",
    "total_problemas_esperado",
    "total_resueltos_esperado",
    "total_propuestos_esperado",
    "workspace_dir",
    "pdf_path",
    "cover_path",
    "session_path",
    "segmentos_dir",
    "soluciones_dir",
    "estado",
    "notas",
    "activo",
)

INSTANCE_SYNC_FIELDS: tuple[str, ...] = (
    "codigo_instancia",
    "total_esperado",
    "pdf_path",
    "session_path",
    "soluciones_dir",
    "activo",
    "notas",
    "nombre_instancia",
    "estado",
    "config_snapshot",
    "session_schema_version",
)


@dataclass(slots=True)
class PublishSummary:
    pending_before: int = 0
    published: int = 0
    conflicts: int = 0
    failed: int = 0
    skipped: int = 0
    books_inserted: int = 0
    books_updated: int = 0
    books_skipped: int = 0
    instances_inserted: int = 0
    instances_updated: int = 0
    instances_skipped: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            "pending_before": int(self.pending_before),
            "published": int(self.published),
            "conflicts": int(self.conflicts),
            "failed": int(self.failed),
            "skipped": int(self.skipped),
            "books_inserted": int(self.books_inserted),
            "books_updated": int(self.books_updated),
            "books_skipped": int(self.books_skipped),
            "instances_inserted": int(self.instances_inserted),
            "instances_updated": int(self.instances_updated),
            "instances_skipped": int(self.instances_skipped),
        }


class ProblemChangeQueueController:
    def __init__(self) -> None:
        self.local_db = DatabaseManager.from_profile("local_mirror")
        self.cloud_db = DatabaseManager.from_profile("cloud")

    def _ensure_problem_math_consistency_column(self, conn) -> None:
        cur = conn.cursor()
        try:
            cur.execute("SELECT to_regclass('public.problemas');")
            row = cur.fetchone()
            if not row or not row[0]:
                conn.commit()
                return
            cur.execute(
                """
                ALTER TABLE problemas
                ADD COLUMN IF NOT EXISTS consistencia_matematica VARCHAR(30) NOT NULL DEFAULT 'Sin revisar';
                """
            )
            cur.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_constraint
                        WHERE conname = 'ck_problemas_consistencia_matematica'
                          AND conrelid = 'public.problemas'::regclass
                    ) THEN
                        ALTER TABLE problemas
                        ADD CONSTRAINT ck_problemas_consistencia_matematica
                        CHECK (
                            consistencia_matematica IN ('Sin revisar', 'Consistente', 'Inconsistente')
                        );
                    END IF;
                END $$;
                """
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            message = str(exc or "").lower()
            if "must be owner of table problemas" in message or "permission denied" in message:
                # En perfiles cloud con permisos restringidos de DDL, no bloquear la publicación.
                return
            raise
        finally:
            cur.close()

    def ensure_local_queue(self, db_name: str | None = None) -> None:
        local_name = str(db_name or self.local_db.db_name).strip() or self.local_db.db_name
        conn = self.local_db.get_connection(local_name)
        try:
            self._ensure_problem_math_consistency_column(conn)
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS problema_pending_changes (
                    problema_id INTEGER PRIMARY KEY,
                    numero_original INTEGER NOT NULL,
                    archivo_origen VARCHAR(255) NOT NULL DEFAULT '',
                    libro_codigo VARCHAR(80) NOT NULL DEFAULT '',
                    codigo_instancia VARCHAR(80) NOT NULL DEFAULT '',
                    base_revision_version INTEGER NOT NULL DEFAULT 1,
                    local_revision_version INTEGER NOT NULL DEFAULT 1,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    publish_status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    queued_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                    last_local_change_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                    published_at TIMESTAMP WITHOUT TIME ZONE,
                    server_revision_version INTEGER,
                    pending_count INTEGER NOT NULL DEFAULT 1,
                    error_message TEXT NOT NULL DEFAULT ''
                );
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_problema_pending_changes_status
                ON problema_pending_changes (publish_status, last_local_change_at DESC);
                """
            )
            cur.execute(
                """
                CREATE OR REPLACE FUNCTION queue_problema_local_change() RETURNS trigger AS $$
                DECLARE
                    skip_queue TEXT;
                    payload JSONB;
                    base_revision INTEGER;
                BEGIN
                    skip_queue := current_setting('mathcontentstudio.skip_local_queue', true);
                    IF COALESCE(skip_queue, 'off') = 'on' THEN
                        RETURN NEW;
                    END IF;

                    IF TG_OP = 'UPDATE'
                       AND NEW.numero_original IS NOT DISTINCT FROM OLD.numero_original
                       AND NEW.archivo_origen IS NOT DISTINCT FROM OLD.archivo_origen
                       AND NEW.enunciado_latex IS NOT DISTINCT FROM OLD.enunciado_latex
                       AND NEW.consistencia_matematica IS NOT DISTINCT FROM OLD.consistencia_matematica
                       AND NEW.imagenes IS NOT DISTINCT FROM OLD.imagenes
                       AND NEW.libro_codigo IS NOT DISTINCT FROM OLD.libro_codigo
                       AND NEW.codigo_instancia IS NOT DISTINCT FROM OLD.codigo_instancia
                       AND NEW.tiene_clave IS NOT DISTINCT FROM OLD.tiene_clave
                       AND NEW.tiene_solucion IS NOT DISTINCT FROM OLD.tiene_solucion
                       AND NEW.curso IS NOT DISTINCT FROM OLD.curso
                       AND NEW.tema IS NOT DISTINCT FROM OLD.tema
                       AND NEW.subtema IS NOT DISTINCT FROM OLD.subtema
                       AND NEW.respuesta_correcta IS NOT DISTINCT FROM OLD.respuesta_correcta
                       AND NEW.ruta_imagen_solucion IS NOT DISTINCT FROM OLD.ruta_imagen_solucion
                       AND NEW.tema_id IS NOT DISTINCT FROM OLD.tema_id
                       AND NEW.subtema_id IS NOT DISTINCT FROM OLD.subtema_id
                       AND NEW.respuesta IS NOT DISTINCT FROM OLD.respuesta
                       AND NEW.nivel_dificultad IS NOT DISTINCT FROM OLD.nivel_dificultad
                       AND NEW.auditoria_razon IS NOT DISTINCT FROM OLD.auditoria_razon
                       AND NEW.soluciones IS NOT DISTINCT FROM OLD.soluciones
                       AND NEW.reglas_sugeridas_ia IS NOT DISTINCT FROM OLD.reglas_sugeridas_ia
                       AND NEW.conceptos_ia IS NOT DISTINCT FROM OLD.conceptos_ia
                       AND NEW.libro_id IS NOT DISTINCT FROM OLD.libro_id
                       AND NEW.instancia_id IS NOT DISTINCT FROM OLD.instancia_id
                    THEN
                        RETURN NEW;
                    END IF;

                    payload := jsonb_build_object(
                        'numero_original', NEW.numero_original,
                        'archivo_origen', COALESCE(NEW.archivo_origen, ''),
                        'enunciado_latex', COALESCE(NEW.enunciado_latex, ''),
                        'consistencia_matematica', COALESCE(NEW.consistencia_matematica, 'Sin revisar'),
                        'imagenes', COALESCE(to_jsonb(NEW.imagenes), '[]'::jsonb),
                        'libro_codigo', COALESCE(NEW.libro_codigo, ''),
                        'codigo_instancia', COALESCE(NEW.codigo_instancia, ''),
                        'tiene_clave', COALESCE(NEW.tiene_clave, FALSE),
                        'tiene_solucion', COALESCE(NEW.tiene_solucion, FALSE),
                        'curso', COALESCE(NEW.curso, ''),
                        'tema', COALESCE(NEW.tema, ''),
                        'subtema', COALESCE(NEW.subtema, ''),
                        'respuesta_correcta', COALESCE(NEW.respuesta_correcta, ''),
                        'ruta_imagen_solucion', COALESCE(NEW.ruta_imagen_solucion, ''),
                        'tema_id', NEW.tema_id,
                        'subtema_id', NEW.subtema_id,
                        'respuesta', NEW.respuesta,
                        'nivel_dificultad', NEW.nivel_dificultad,
                        'auditoria_razon', NEW.auditoria_razon,
                        'soluciones', COALESCE(NEW.soluciones, '[]'::jsonb),
                        'reglas_sugeridas_ia', COALESCE(to_jsonb(NEW.reglas_sugeridas_ia), '[]'::jsonb),
                        'conceptos_ia', COALESCE(NEW.conceptos_ia, '[]'::jsonb),
                        'libro_id', NEW.libro_id,
                        'instancia_id', NEW.instancia_id
                    );

                    IF TG_OP = 'UPDATE' THEN
                        base_revision := GREATEST(COALESCE(OLD.revision_version, 1), 1);
                    ELSE
                        base_revision := GREATEST(COALESCE(NEW.revision_version, 1) - 1, 1);
                    END IF;

                    INSERT INTO problema_pending_changes (
                        problema_id,
                        numero_original,
                        archivo_origen,
                        libro_codigo,
                        codigo_instancia,
                        base_revision_version,
                        local_revision_version,
                        payload,
                        publish_status,
                        queued_at,
                        last_local_change_at,
                        published_at,
                        server_revision_version,
                        pending_count,
                        error_message
                    )
                    VALUES (
                        NEW.id,
                        NEW.numero_original,
                        COALESCE(NEW.archivo_origen, ''),
                        COALESCE(NEW.libro_codigo, ''),
                        COALESCE(NEW.codigo_instancia, ''),
                        base_revision,
                        GREATEST(COALESCE(NEW.revision_version, 1), 1),
                        payload,
                        'pending',
                        NOW(),
                        NOW(),
                        NULL,
                        NULL,
                        1,
                        ''
                    )
                    ON CONFLICT (problema_id) DO UPDATE
                    SET
                        numero_original = EXCLUDED.numero_original,
                        archivo_origen = EXCLUDED.archivo_origen,
                        libro_codigo = EXCLUDED.libro_codigo,
                        codigo_instancia = EXCLUDED.codigo_instancia,
                        payload = EXCLUDED.payload,
                        local_revision_version = EXCLUDED.local_revision_version,
                        base_revision_version = CASE
                            WHEN problema_pending_changes.publish_status = 'pending'
                                THEN problema_pending_changes.base_revision_version
                            ELSE EXCLUDED.base_revision_version
                        END,
                        publish_status = 'pending',
                        queued_at = CASE
                            WHEN problema_pending_changes.publish_status = 'pending'
                                THEN problema_pending_changes.queued_at
                            ELSE NOW()
                        END,
                        last_local_change_at = NOW(),
                        published_at = NULL,
                        server_revision_version = NULL,
                        pending_count = CASE
                            WHEN problema_pending_changes.publish_status = 'pending'
                                THEN problema_pending_changes.pending_count + 1
                            ELSE 1
                        END,
                        error_message = '';

                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
                """
            )
            cur.execute("DROP TRIGGER IF EXISTS trg_queue_problema_local_change ON problemas;")
            cur.execute(
                """
                CREATE TRIGGER trg_queue_problema_local_change
                AFTER INSERT OR UPDATE ON problemas
                FOR EACH ROW
                EXECUTE FUNCTION queue_problema_local_change();
                """
            )
            conn.commit()
        finally:
            conn.close()

    def list_pending(self, db_name: str | None = None) -> List[dict]:
        self.ensure_local_queue(db_name)
        local_name = str(db_name or self.local_db.db_name).strip() or self.local_db.db_name
        conn = self.local_db.get_connection(local_name)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    problema_id,
                    numero_original,
                    archivo_origen,
                    libro_codigo,
                    codigo_instancia,
                    base_revision_version,
                    local_revision_version,
                    publish_status,
                    queued_at,
                    last_local_change_at,
                    published_at,
                    server_revision_version,
                    pending_count,
                    error_message
                FROM problema_pending_changes
                WHERE publish_status IN ('pending', 'conflict', 'error')
                ORDER BY last_local_change_at DESC, problema_id DESC;
                """
            )
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def pending_count(self, db_name: str | None = None) -> int:
        self.ensure_local_queue(db_name)
        local_name = str(db_name or self.local_db.db_name).strip() or self.local_db.db_name
        conn = self.local_db.get_connection(local_name)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT COUNT(*)::int
                FROM problema_pending_changes
                WHERE publish_status IN ('pending', 'conflict', 'error');
                """
            )
            row = cur.fetchone()
            return int(row[0] or 0) if row else 0
        finally:
            conn.close()

    def publish_pending(self, db_name: str | None = None, *, limit: int | None = None) -> Dict[str, int]:
        self.ensure_local_queue(db_name)
        summary = PublishSummary()
        local_name = str(db_name or self.local_db.db_name).strip() or self.local_db.db_name
        local_conn = self.local_db.get_connection(local_name)
        cloud_conn = self.cloud_db.get_connection(self.cloud_db.db_name)
        try:
            self._ensure_problem_math_consistency_column(local_conn)
            self._ensure_problem_math_consistency_column(cloud_conn)
            try:
                structure_summary = self._sync_books_and_instances(local_conn, cloud_conn)
            except Exception as exc:
                if not self._is_cloud_connection_error(exc):
                    raise
                LOGGER.warning("cloud_connection_lost_during_structure_sync err=%s", exc)
                cloud_conn = self._reconnect_cloud_connection(cloud_conn)
                structure_summary = self._sync_books_and_instances(local_conn, cloud_conn)
            summary.books_inserted = int(structure_summary.get("books_inserted") or 0)
            summary.books_updated = int(structure_summary.get("books_updated") or 0)
            summary.books_skipped = int(structure_summary.get("books_skipped") or 0)
            summary.instances_inserted = int(structure_summary.get("instances_inserted") or 0)
            summary.instances_updated = int(structure_summary.get("instances_updated") or 0)
            summary.instances_skipped = int(structure_summary.get("instances_skipped") or 0)
            local_cur = local_conn.cursor()
            try:
                local_cur.execute(
                    """
                    SELECT
                        problema_id,
                        numero_original,
                        archivo_origen,
                        libro_codigo,
                        codigo_instancia,
                        base_revision_version,
                        local_revision_version,
                        payload,
                        publish_status,
                        pending_count
                    FROM problema_pending_changes
                    WHERE publish_status IN ('pending', 'conflict', 'error')
                    ORDER BY last_local_change_at ASC, problema_id ASC;
                    """
                )
                rows = local_cur.fetchall()
                if limit is not None and limit > 0:
                    rows = rows[: int(limit)]
                summary.pending_before = len(rows)
                for row in rows:
                    problema_id = int(row[0])
                    try:
                        payload = row[7]
                        if isinstance(payload, str):
                            payload = json.loads(payload)
                        if not isinstance(payload, dict):
                            raise ValueError("Payload local invalido.")

                        publish_result = None
                        last_error: Exception | None = None
                        for attempt in range(2):
                            try:
                                publish_result = self._publish_one(
                                    local_conn=local_conn,
                                    cloud_conn=cloud_conn,
                                    problema_id=problema_id,
                                    numero_original=int(row[1] or 0),
                                    archivo_origen=str(row[2] or "").strip(),
                                    libro_codigo=str(row[3] or "").strip(),
                                    codigo_instancia=str(row[4] or "").strip(),
                                    base_revision_version=max(int(row[5] or 1), 1),
                                    payload=payload,
                                )
                                last_error = None
                                break
                            except Exception as exc:
                                last_error = exc
                                if attempt == 0 and self._is_cloud_connection_error(exc):
                                    LOGGER.warning(
                                        "cloud_connection_lost_during_problem_publish problema_id=%s err=%s",
                                        problema_id,
                                        exc,
                                    )
                                    cloud_conn = self._reconnect_cloud_connection(cloud_conn)
                                    continue
                                raise
                        if last_error is not None:
                            raise last_error

                        if publish_result == "published":
                            summary.published += 1
                        elif publish_result == "conflict":
                            summary.conflicts += 1
                        else:
                            summary.skipped += 1
                    except ConflictError as exc:
                        self._mark_local_status(local_conn, problema_id, "conflict", error_message=str(exc))
                        summary.conflicts += 1
                    except Exception as exc:
                        LOGGER.exception("problem_publish_error problema_id=%s err=%s", problema_id, exc)
                        self._mark_local_status(local_conn, problema_id, "error", error_message=str(exc))
                        summary.failed += 1
            finally:
                try:
                    local_cur.close()
                except Exception:
                    pass
            return summary.to_dict()
        finally:
            cloud_conn.close()
            local_conn.close()

    def _is_cloud_connection_error(self, exc: Exception) -> bool:
        text = str(exc or "").strip().lower()
        if not text:
            return False
        patterns = (
            "server closed the connection unexpectedly",
            "connection unexpectedly",
            "connection not open",
            "terminating connection",
            "ssl syscall error: eof detected",
            "could not receive data from server",
            "connection reset by peer",
            "broken pipe",
        )
        return any(token in text for token in patterns)

    def _reconnect_cloud_connection(self, cloud_conn):
        try:
            cloud_conn.close()
        except Exception:
            pass
        new_conn = self.cloud_db.get_connection(self.cloud_db.db_name)
        self._ensure_problem_math_consistency_column(new_conn)
        return new_conn

    def _sync_books_and_instances(self, local_conn, cloud_conn) -> Dict[str, int]:
        summary = {
            "books_inserted": 0,
            "books_updated": 0,
            "books_skipped": 0,
            "instances_inserted": 0,
            "instances_updated": 0,
            "instances_skipped": 0,
        }
        local_books = self._fetch_all_dicts(local_conn, "SELECT * FROM libros_escaneo ORDER BY updated_at ASC NULLS FIRST, id ASC;")
        cloud_books = self._fetch_all_dicts(cloud_conn, "SELECT * FROM libros_escaneo ORDER BY updated_at ASC NULLS FIRST, id ASC;")
        cloud_books_by_code = {
            str(row.get("codigo") or "").strip(): row
            for row in cloud_books
            if str(row.get("codigo") or "").strip()
        }
        local_book_code_by_id = {int(row["id"]): str(row.get("codigo") or "").strip() for row in local_books if row.get("id") is not None}
        server_book_id_by_code: Dict[str, int] = {}
        cloud_cur = cloud_conn.cursor()
        try:
            for local_book in local_books:
                code = str(local_book.get("codigo") or "").strip()
                if not code:
                    summary["books_skipped"] += 1
                    continue
                server_book = cloud_books_by_code.get(code)
                prepared_book, asset_ops = self._build_server_book_payload(local_book, server_book)
                if server_book is None:
                    self._upload_book_assets(asset_ops)
                    new_id = self._insert_server_book(cloud_cur, prepared_book)
                    cloud_conn.commit()
                    server_book_id_by_code[code] = int(new_id)
                    summary["books_inserted"] += 1
                    continue
                server_book_id_by_code[code] = int(server_book["id"])
                if not self._row_has_meaningful_changes(prepared_book, server_book, BOOK_SYNC_FIELDS):
                    summary["books_skipped"] += 1
                    continue
                if not self._is_local_row_newer(local_book, server_book):
                    summary["books_skipped"] += 1
                    continue
                self._upload_book_assets(asset_ops)
                self._update_server_book(cloud_cur, int(server_book["id"]), prepared_book)
                cloud_conn.commit()
                summary["books_updated"] += 1

            local_instances = self._fetch_all_dicts(local_conn, "SELECT * FROM libro_instancias_escaneo ORDER BY updated_at ASC NULLS FIRST, id ASC;")
            cloud_instances = self._fetch_all_dicts(cloud_conn, "SELECT * FROM libro_instancias_escaneo ORDER BY updated_at ASC NULLS FIRST, id ASC;")
            cloud_instance_map: Dict[tuple[int, str], dict] = {}
            for row in cloud_instances:
                try:
                    key = (int(row["libro_id"]), str(row.get("codigo_instancia") or "").strip())
                except Exception:
                    continue
                if key[1]:
                    cloud_instance_map[key] = row

            for local_instance in local_instances:
                local_book_id = local_instance.get("libro_id")
                if local_book_id is None:
                    summary["instances_skipped"] += 1
                    continue
                code = local_book_code_by_id.get(int(local_book_id), "")
                server_book_id = server_book_id_by_code.get(code)
                instance_code = str(local_instance.get("codigo_instancia") or "").strip()
                if not server_book_id or not instance_code:
                    summary["instances_skipped"] += 1
                    continue
                server_instance = cloud_instance_map.get((int(server_book_id), instance_code))
                if server_instance is None:
                    self._insert_server_instance(cloud_cur, int(server_book_id), local_instance)
                    cloud_conn.commit()
                    summary["instances_inserted"] += 1
                    continue
                if not self._row_has_meaningful_changes(local_instance, server_instance, INSTANCE_SYNC_FIELDS):
                    summary["instances_skipped"] += 1
                    continue
                if not self._is_local_row_newer(local_instance, server_instance):
                    summary["instances_skipped"] += 1
                    continue
                self._update_server_instance(cloud_cur, int(server_instance["id"]), local_instance)
                cloud_conn.commit()
                summary["instances_updated"] += 1
        finally:
            try:
                cloud_cur.close()
            except Exception:
                pass
        return summary

    def _build_server_book_payload(self, local_row: dict, server_row: Optional[dict]) -> tuple[dict, list[tuple[Path, str]]]:
        payload = dict(local_row)
        code = str(local_row.get("codigo") or "").strip()
        remote_dir = f"{DEFAULT_REMOTE_LIBRARY_ROOT.rstrip('/')}/{code}" if code else DEFAULT_REMOTE_LIBRARY_ROOT.rstrip("/")
        asset_ops: list[tuple[Path, str]] = []

        local_pdf = self._resolve_existing_local_file(local_row.get("pdf_path"))
        if local_pdf is not None and code:
            payload["pdf_path"] = f"{remote_dir}/source.pdf"
            asset_ops.append((local_pdf, payload["pdf_path"]))
        else:
            payload["pdf_path"] = str((server_row or {}).get("pdf_path") or "").strip()

        local_cover = self._resolve_existing_local_file(local_row.get("cover_path"))
        if local_cover is not None and code:
            cover_ext = local_cover.suffix.lower() or ".png"
            payload["cover_path"] = f"{remote_dir}/cover{cover_ext}"
            asset_ops.append((local_cover, payload["cover_path"]))
        else:
            payload["cover_path"] = str((server_row or {}).get("cover_path") or "").strip()

        return payload, asset_ops

    def _resolve_existing_local_file(self, raw_path: object) -> Optional[Path]:
        path = str(raw_path or "").strip()
        if not path:
            return None
        try:
            candidate = Path(path).expanduser()
        except Exception:
            return None
        return candidate if candidate.exists() and candidate.is_file() else None

    def _resolve_server_access(self) -> tuple[str, str, Path]:
        host = str(os.getenv("MATH_BANK_SERVER_HOST") or "3.225.19.0").strip()
        user = str(os.getenv("MATH_BANK_SERVER_USER") or "ubuntu").strip()
        candidates: list[Path] = []
        raw = str(os.getenv("MATH_BANK_IDENTITY_FILE") or "").strip()
        if raw:
            candidates.append(Path(raw).expanduser())
        home = Path.home()
        candidates.extend(
            [
                home / ".ssh" / "LightsailDefaultKey-us-east-1.pem",
                home / "Keys" / "LightsailDefaultKey-us-east-1.pem",
                home / "Downloads" / "LightsailDefaultKey-us-east-1.pem",
            ]
        )
        for pattern in (
            "LightsailDefaultKey-*.pem",
            "*.pem",
        ):
            for folder in (home / ".ssh", home / "Keys", home / "Downloads"):
                try:
                    if folder.exists():
                        candidates.extend(sorted(folder.glob(pattern)))
                except Exception:
                    continue
        seen: set[str] = set()
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except Exception:
                continue
            key = str(resolved).lower()
            if key in seen:
                continue
            seen.add(key)
            if resolved.exists():
                return host, user, resolved
        raise FileNotFoundError("No se encontro la llave PEM para subir assets al servidor.")

    def _upload_book_assets(self, asset_ops: list[tuple[Path, str]]) -> None:
        if not asset_ops:
            return
        host, user, identity_file = self._resolve_server_access()
        created_dirs: set[str] = set()
        for local_path, remote_path in asset_ops:
            remote_dir = remote_path.rsplit("/", 1)[0]
            if remote_dir not in created_dirs:
                self._run_ssh_command(host, user, identity_file, f"mkdir -p '{remote_dir}'")
                created_dirs.add(remote_dir)
            self._run_scp_command(host, user, identity_file, local_path, remote_path)

    def _run_ssh_command(self, host: str, user: str, identity_file: Path, remote_command: str) -> None:
        result = subprocess.run(
            [
                "ssh",
                "-i",
                str(identity_file),
                "-o",
                "StrictHostKeyChecking=no",
                f"{user}@{host}",
                remote_command,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if int(result.returncode or 0) != 0:
            detail = str(result.stderr or result.stdout or "").strip() or f"codigo {result.returncode}"
            raise RuntimeError(f"No se pudo preparar el directorio remoto de assets: {detail}")

    def _run_scp_command(self, host: str, user: str, identity_file: Path, local_path: Path, remote_path: str) -> None:
        result = subprocess.run(
            [
                "scp",
                "-i",
                str(identity_file),
                "-o",
                "StrictHostKeyChecking=no",
                str(local_path),
                f"{user}@{host}:{remote_path}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if int(result.returncode or 0) != 0:
            detail = str(result.stderr or result.stdout or "").strip() or f"codigo {result.returncode}"
            raise RuntimeError(f"No se pudo subir asset al servidor: {local_path.name} -> {remote_path} ({detail})")

    def _fetch_all_dicts(self, conn, query: str, params: tuple | None = None) -> List[dict]:
        cur = conn.cursor()
        try:
            cur.execute(query, params or ())
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
        finally:
            try:
                cur.close()
            except Exception:
                pass

    def _row_has_meaningful_changes(self, local_row: dict, server_row: dict, fields: tuple[str, ...]) -> bool:
        for field in fields:
            left = self._normalize_compare_value(local_row.get(field))
            right = self._normalize_compare_value(server_row.get(field))
            if left != right:
                return True
        return False

    def _is_local_row_newer(self, local_row: dict, server_row: dict) -> bool:
        local_updated = local_row.get("updated_at")
        server_updated = server_row.get("updated_at")
        if local_updated is None:
            return False
        if server_updated is None:
            return True
        try:
            return local_updated >= server_updated
        except Exception:
            return True

    def _insert_server_book(self, cur, row: dict) -> int:
        payload = tuple(row.get(field) for field in BOOK_SYNC_FIELDS)
        cur.execute(
            """
            INSERT INTO libros_escaneo (
                codigo, titulo, autor, editorial, edicion, curso, tema_base,
                total_problemas_esperado, total_resueltos_esperado, total_propuestos_esperado,
                workspace_dir, pdf_path, cover_path, session_path, segmentos_dir, soluciones_dir,
                estado, notas, activo
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s
            )
            RETURNING id;
            """,
            payload,
        )
        return int(cur.fetchone()[0])

    def _update_server_book(self, cur, server_id: int, row: dict) -> None:
        payload = tuple(row.get(field) for field in BOOK_SYNC_FIELDS[1:]) + (int(server_id),)
        cur.execute(
            """
            UPDATE libros_escaneo
            SET
                titulo = %s,
                autor = %s,
                editorial = %s,
                edicion = %s,
                curso = %s,
                tema_base = %s,
                total_problemas_esperado = %s,
                total_resueltos_esperado = %s,
                total_propuestos_esperado = %s,
                workspace_dir = %s,
                pdf_path = %s,
                cover_path = %s,
                session_path = %s,
                segmentos_dir = %s,
                soluciones_dir = %s,
                estado = %s,
                notas = %s,
                activo = %s,
                updated_at = NOW()
            WHERE id = %s;
            """,
            payload,
        )

    def _insert_server_instance(self, cur, server_book_id: int, row: dict) -> int:
        payload = (
            int(server_book_id),
            row.get("codigo_instancia"),
            row.get("total_esperado"),
            row.get("pdf_path"),
            row.get("session_path"),
            row.get("soluciones_dir"),
            row.get("activo"),
            row.get("notas"),
            row.get("nombre_instancia"),
            row.get("estado"),
            json.dumps(row.get("config_snapshot") or {}, ensure_ascii=False),
            row.get("session_schema_version"),
        )
        cur.execute(
            """
            INSERT INTO libro_instancias_escaneo (
                libro_id, codigo_instancia, total_esperado, pdf_path, session_path,
                soluciones_dir, activo, notas, nombre_instancia, estado, config_snapshot, session_schema_version
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            RETURNING id;
            """,
            payload,
        )
        return int(cur.fetchone()[0])

    def _update_server_instance(self, cur, server_id: int, row: dict) -> None:
        payload = (
            row.get("total_esperado"),
            row.get("pdf_path"),
            row.get("session_path"),
            row.get("soluciones_dir"),
            row.get("activo"),
            row.get("notas"),
            row.get("nombre_instancia"),
            row.get("estado"),
            json.dumps(row.get("config_snapshot") or {}, ensure_ascii=False),
            row.get("session_schema_version"),
            int(server_id),
        )
        cur.execute(
            """
            UPDATE libro_instancias_escaneo
            SET
                total_esperado = %s,
                pdf_path = %s,
                session_path = %s,
                soluciones_dir = %s,
                activo = %s,
                notas = %s,
                nombre_instancia = %s,
                estado = %s,
                config_snapshot = %s::jsonb,
                session_schema_version = %s,
                updated_at = NOW()
            WHERE id = %s;
            """,
            payload,
        )

    def _publish_one(
        self,
        *,
        local_conn,
        cloud_conn,
        problema_id: int,
        numero_original: int,
        archivo_origen: str,
        libro_codigo: str,
        codigo_instancia: str,
        base_revision_version: int,
        payload: dict,
    ) -> str:
        cloud_cur = cloud_conn.cursor()
        try:
            server_row = self._find_server_problem(
                cloud_cur,
                numero_original=numero_original,
                archivo_origen=archivo_origen,
                libro_codigo=libro_codigo,
                codigo_instancia=codigo_instancia,
            )
            server_libro_id, server_instancia_id = self._resolve_server_context_ids(cloud_cur, libro_codigo=libro_codigo, codigo_instancia=codigo_instancia)
            normalized = self._normalize_payload(payload, libro_id=server_libro_id, instancia_id=server_instancia_id)
            if server_row is None:
                new_server_id, new_revision = self._insert_server_problem(cloud_cur, normalized)
            else:
                server_id, server_revision = int(server_row[0]), max(int(server_row[1] or 1), 1)
                if server_revision != max(int(base_revision_version or 1), 1):
                    raise ConflictError(
                        f"Conflicto de version para problema {problema_id}: servidor={server_revision} local_base={base_revision_version}"
                    )
                server_payload = self._fetch_server_payload(cloud_cur, server_id)
                if self._payload_equivalent(server_payload, normalized):
                    self._mark_local_status(
                        local_conn,
                        problema_id,
                        "published",
                        error_message="",
                        server_revision_version=server_revision,
                    )
                    return "published"
                new_server_id, new_revision = self._update_server_problem(cloud_cur, server_id=server_id, server_revision=server_revision, payload=normalized)

            cloud_conn.commit()
            self._mark_local_status(
                local_conn,
                problema_id,
                "published",
                error_message="",
                server_revision_version=new_revision,
            )
            self._sync_local_problem_revision(
                local_conn,
                problema_id=problema_id,
                server_revision_version=new_revision,
            )
            LOGGER.info(
                "problem_publish_ok local_problem_id=%s server_problem_id=%s revision=%s",
                problema_id,
                new_server_id,
                new_revision,
            )
            return "published"
        finally:
            try:
                cloud_cur.close()
            except Exception:
                pass

    def _find_server_problem(self, cur, *, numero_original: int, archivo_origen: str, libro_codigo: str, codigo_instancia: str):
        numero_original = int(numero_original or 0)
        archivo_origen = str(archivo_origen or "").strip()
        libro_codigo = str(libro_codigo or "").strip()
        codigo_instancia = str(codigo_instancia or "").strip()

        if libro_codigo and codigo_instancia:
            cur.execute(
                """
                SELECT id, revision_version
                FROM problemas
                WHERE libro_codigo = %s
                  AND codigo_instancia = %s
                  AND numero_original = %s
                LIMIT 1;
                """,
                (libro_codigo, codigo_instancia, numero_original),
            )
            row = cur.fetchone()
            if row:
                return row

            # When the instance code is known, do not fall back to the shared
            # source PDF path. Different sessions often reuse the same file.
            return None

        if libro_codigo and archivo_origen:
            cur.execute(
                """
                SELECT id, revision_version
                FROM problemas
                WHERE libro_codigo = %s
                  AND numero_original = %s
                  AND archivo_origen = %s
                LIMIT 1;
                """,
                (libro_codigo, numero_original, archivo_origen),
            )
            row = cur.fetchone()
            if row:
                return row

        if archivo_origen:
            cur.execute(
                """
                SELECT id, revision_version
                FROM problemas
                WHERE numero_original = %s
                  AND archivo_origen = %s
                LIMIT 1;
                """,
                (numero_original, archivo_origen),
            )
            return cur.fetchone()

        cur.execute(
            """
            SELECT id, revision_version
            FROM problemas
            WHERE numero_original = %s
            LIMIT 1;
            """,
            (numero_original,),
        )
        return cur.fetchone()

    def _resolve_server_context_ids(self, cur, *, libro_codigo: str, codigo_instancia: str) -> tuple[Optional[int], Optional[int]]:
        if not libro_codigo:
            return (None, None)
        cur.execute(
            """
            SELECT l.id, i.id
            FROM libros_escaneo l
            LEFT JOIN libro_instancias_escaneo i
              ON i.libro_id = l.id AND i.codigo_instancia = %s
            WHERE l.codigo = %s
            LIMIT 1;
            """,
            (codigo_instancia, libro_codigo),
        )
        row = cur.fetchone()
        if not row:
            return (None, None)
        libro_id = int(row[0]) if row[0] is not None else None
        instancia_id = int(row[1]) if row[1] is not None else None
        return (libro_id, instancia_id)

    def _normalize_payload(self, payload: dict, *, libro_id: Optional[int], instancia_id: Optional[int]) -> dict:
        normalized = {}
        for key in EDITABLE_FIELDS:
            value = payload.get(key)
            if key in {"soluciones", "conceptos_ia"}:
                if value in (None, ""):
                    normalized[key] = []
                else:
                    normalized[key] = value
            elif key in {"imagenes", "reglas_sugeridas_ia"}:
                normalized[key] = list(value or [])
            elif key in {"tiene_clave", "tiene_solucion"}:
                normalized[key] = bool(value)
            else:
                normalized[key] = value
        normalized["libro_id"] = libro_id if libro_id is not None else normalized.get("libro_id")
        normalized["instancia_id"] = instancia_id if instancia_id is not None else normalized.get("instancia_id")
        normalized["updated_by"] = PUBLISHER_TAG or "auditor-ia-local"
        return normalized

    def _insert_server_problem(self, cur, payload: dict) -> tuple[int, int]:
        cur.execute(
            """
            INSERT INTO problemas (
                numero_original,
                archivo_origen,
                enunciado_latex,
                consistencia_matematica,
                imagenes,
                libro_codigo,
                codigo_instancia,
                tiene_clave,
                tiene_solucion,
                curso,
                tema,
                subtema,
                respuesta_correcta,
                ruta_imagen_solucion,
                tema_id,
                subtema_id,
                respuesta,
                nivel_dificultad,
                auditoria_razon,
                soluciones,
                reglas_sugeridas_ia,
                conceptos_ia,
                libro_id,
                instancia_id,
                updated_by,
                revision_version
            )
            VALUES (
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s::jsonb, %s, %s::jsonb, %s, %s, %s, 1
            )
            RETURNING id, revision_version;
            """,
            (
                int(payload["numero_original"] or 0),
                str(payload.get("archivo_origen") or ""),
                str(payload.get("enunciado_latex") or ""),
                str(payload.get("consistencia_matematica") or "Sin revisar"),
                list(payload.get("imagenes") or []),
                str(payload.get("libro_codigo") or ""),
                str(payload.get("codigo_instancia") or ""),
                bool(payload.get("tiene_clave")),
                bool(payload.get("tiene_solucion")),
                str(payload.get("curso") or ""),
                str(payload.get("tema") or ""),
                str(payload.get("subtema") or ""),
                str(payload.get("respuesta_correcta") or ""),
                str(payload.get("ruta_imagen_solucion") or ""),
                payload.get("tema_id"),
                payload.get("subtema_id"),
                payload.get("respuesta"),
                payload.get("nivel_dificultad"),
                payload.get("auditoria_razon"),
                json.dumps(payload.get("soluciones") or [], ensure_ascii=False),
                list(payload.get("reglas_sugeridas_ia") or []),
                json.dumps(payload.get("conceptos_ia") or [], ensure_ascii=False),
                payload.get("libro_id"),
                payload.get("instancia_id"),
                str(payload.get("updated_by") or PUBLISHER_TAG),
            ),
        )
        row = cur.fetchone()
        return (int(row[0]), int(row[1]))

    def _update_server_problem(self, cur, *, server_id: int, server_revision: int, payload: dict) -> tuple[int, int]:
        next_revision = max(int(server_revision or 1), 1) + 1
        cur.execute(
            """
            UPDATE problemas
            SET
                numero_original = %s,
                archivo_origen = %s,
                enunciado_latex = %s,
                consistencia_matematica = %s,
                imagenes = %s,
                libro_codigo = %s,
                codigo_instancia = %s,
                tiene_clave = %s,
                tiene_solucion = %s,
                curso = %s,
                tema = %s,
                subtema = %s,
                respuesta_correcta = %s,
                ruta_imagen_solucion = %s,
                tema_id = %s,
                subtema_id = %s,
                respuesta = %s,
                nivel_dificultad = %s,
                auditoria_razon = %s,
                soluciones = %s::jsonb,
                reglas_sugeridas_ia = %s,
                conceptos_ia = %s::jsonb,
                libro_id = %s,
                instancia_id = %s,
                updated_by = %s,
                revision_version = %s,
                updated_at = NOW()
            WHERE id = %s
            RETURNING id, revision_version;
            """,
            (
                int(payload["numero_original"] or 0),
                str(payload.get("archivo_origen") or ""),
                str(payload.get("enunciado_latex") or ""),
                str(payload.get("consistencia_matematica") or "Sin revisar"),
                list(payload.get("imagenes") or []),
                str(payload.get("libro_codigo") or ""),
                str(payload.get("codigo_instancia") or ""),
                bool(payload.get("tiene_clave")),
                bool(payload.get("tiene_solucion")),
                str(payload.get("curso") or ""),
                str(payload.get("tema") or ""),
                str(payload.get("subtema") or ""),
                str(payload.get("respuesta_correcta") or ""),
                str(payload.get("ruta_imagen_solucion") or ""),
                payload.get("tema_id"),
                payload.get("subtema_id"),
                payload.get("respuesta"),
                payload.get("nivel_dificultad"),
                payload.get("auditoria_razon"),
                json.dumps(payload.get("soluciones") or [], ensure_ascii=False),
                list(payload.get("reglas_sugeridas_ia") or []),
                json.dumps(payload.get("conceptos_ia") or [], ensure_ascii=False),
                payload.get("libro_id"),
                payload.get("instancia_id"),
                str(payload.get("updated_by") or PUBLISHER_TAG),
                next_revision,
                int(server_id),
            ),
        )
        row = cur.fetchone()
        return (int(row[0]), int(row[1]))

    def _fetch_server_payload(self, cur, server_id: int) -> dict:
        cur.execute(
            """
            SELECT
                numero_original,
                archivo_origen,
                enunciado_latex,
                consistencia_matematica,
                imagenes,
                libro_codigo,
                codigo_instancia,
                tiene_clave,
                tiene_solucion,
                curso,
                tema,
                subtema,
                respuesta_correcta,
                ruta_imagen_solucion,
                tema_id,
                subtema_id,
                respuesta,
                nivel_dificultad,
                auditoria_razon,
                soluciones,
                reglas_sugeridas_ia,
                conceptos_ia,
                libro_id,
                instancia_id
            FROM problemas
            WHERE id = %s;
            """,
            (int(server_id),),
        )
        row = cur.fetchone()
        if not row:
            return {}
        return dict(zip(EDITABLE_FIELDS, row))

    def _payload_equivalent(self, server_payload: dict, local_payload: dict) -> bool:
        if not server_payload:
            return False
        for key in EDITABLE_FIELDS:
            left = self._normalize_compare_value(server_payload.get(key))
            right = self._normalize_compare_value(local_payload.get(key))
            if left != right:
                return False
        return True

    def _normalize_compare_value(self, value):
        if value is None:
            return None
        if isinstance(value, tuple):
            return [self._normalize_compare_value(item) for item in value]
        if isinstance(value, list):
            return [self._normalize_compare_value(item) for item in value]
        if isinstance(value, dict):
            return {str(key): self._normalize_compare_value(val) for key, val in value.items()}
        return value

    def _mark_local_status(
        self,
        local_conn,
        problema_id: int,
        status: str,
        *,
        error_message: str,
        server_revision_version: Optional[int] = None,
    ) -> None:
        cur = local_conn.cursor()
        try:
            cur.execute(
                """
                UPDATE problema_pending_changes
                SET
                    publish_status = %s,
                    error_message = %s,
                    published_at = CASE WHEN %s = 'published' THEN NOW() ELSE published_at END,
                    server_revision_version = %s,
                    last_local_change_at = CASE WHEN %s = 'published' THEN last_local_change_at ELSE NOW() END
                WHERE problema_id = %s;
                """,
                (
                    status,
                    str(error_message or ""),
                    status,
                    server_revision_version,
                    status,
                    int(problema_id),
                ),
            )
            local_conn.commit()
        finally:
            try:
                cur.close()
            except Exception:
                pass

    def _sync_local_problem_revision(self, local_conn, *, problema_id: int, server_revision_version: int) -> None:
        cur = local_conn.cursor()
        try:
            cur.execute("SELECT set_config('mathcontentstudio.skip_local_queue', 'on', true);")
            cur.execute(
                """
                UPDATE problemas
                SET
                    revision_version = %s,
                    updated_by = %s,
                    updated_at = NOW()
                WHERE id = %s;
                """,
                (int(server_revision_version), str(PUBLISHER_TAG or "auditor-ia-local"), int(problema_id)),
            )
            cur.execute("SELECT set_config('mathcontentstudio.skip_local_queue', 'off', true);")
            local_conn.commit()
        finally:
            try:
                cur.close()
            except Exception:
                pass


class ConflictError(RuntimeError):
    pass
