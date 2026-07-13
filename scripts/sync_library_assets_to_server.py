from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.connection import DatabaseManager
from database.problem_change_queue import BOOK_SYNC_FIELDS, INSTANCE_SYNC_FIELDS
from modulos.instance_factory.models import InstancePipelineContext
from utils.project_layout import normalize_instance_name, project_dirs, remap_legacy_drive_path

try:
    from psycopg2.extras import execute_batch
except Exception:  # pragma: no cover - depends on local runtime.
    execute_batch = None  # type: ignore[assignment]


DEFAULT_REMOTE_LIBRARY_ROOT = "/srv/mathcontentstudio/library"
DEFAULT_HOST = "3.225.19.0"
DEFAULT_USER = "ubuntu"
STAGING_EXTERNAL_ASSETS_DIR = "external_crops"
STAGING_EXTERNAL_ASSETS_MAP = "_external_assets_map.json"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _resolve_local_path(value: Any, *, prefer_existing: bool = True) -> Path | None:
    raw = _clean(value)
    if not raw:
        return None
    try:
        return remap_legacy_drive_path(Path(raw).expanduser(), prefer_existing=prefer_existing)
    except Exception:
        return Path(raw)


def _resolve_local_file(value: Any) -> Path | None:
    path = _resolve_local_path(value)
    if path is None:
        return None
    return path if path.exists() and path.is_file() else None


def _resolve_local_dir(value: Any) -> Path | None:
    path = _resolve_local_path(value)
    if path is None:
        return None
    return path if path.exists() and path.is_dir() else None


def _fetch_all(conn, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cur = conn.cursor()
    try:
        cur.execute(query, params)
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        cur.close()


def _fetch_one(conn, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    rows = _fetch_all(conn, query, params)
    return rows[0] if rows else None


def _run(cmd: list[str], *, quiet: bool = False) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Comando fallo ({result.returncode}): {' '.join(cmd)}\n{detail}")
    if not quiet and result.stdout.strip():
        print(result.stdout.strip())


def _ssh_base(args: argparse.Namespace) -> list[str]:
    return [
        "ssh",
        "-i",
        str(args.identity_file),
        "-o",
        "StrictHostKeyChecking=no",
        f"{args.server_user}@{args.server_host}",
    ]


def _scp_base(args: argparse.Namespace) -> list[str]:
    return [
        "scp",
        "-i",
        str(args.identity_file),
        "-o",
        "StrictHostKeyChecking=no",
    ]


def _remote_book_dir(root: str, code: str) -> str:
    root = root.rstrip("/")
    return f"{root}/{code}" if code else root


def _remote_project_dirs(root: str, instance_code: str | None = None) -> dict[str, str]:
    root = root.rstrip("/")
    data = {
        "project_root": root,
        "sessions_dir": f"{root}/sessions",
        "solutions_root": f"{root}/solutions",
        "temporales_root": f"{root}/temporales",
    }
    if instance_code is not None:
        instance_name = normalize_instance_name(instance_code, "sesion")
        instance_root = f"{data['temporales_root']}/{instance_name}"
        data.update(
            {
                "instance_name": instance_name,
                "instance_root": instance_root,
                "sources_dir": f"{instance_root}/sources",
                "datasets_dir": f"{instance_root}/datasets",
                "staging_root": f"{instance_root}/datasets/pdf_factory_staging",
                "solutions_dir": f"{data['solutions_root']}/{instance_name}",
                "session_path": f"{data['sessions_dir']}/{instance_name}.session.json",
            }
        )
    return data


def _remote_quote(value: str) -> str:
    return shlex.quote(str(value))


def _assert_safe_remote_path(args: argparse.Namespace, remote_path: str) -> None:
    root = str(args.remote_library_root or "").rstrip("/")
    path = str(remote_path or "").rstrip("/")
    if not root or path == root or not path.startswith(root + "/"):
        raise RuntimeError(f"Ruta remota fuera del almacen permitido: {remote_path}")


def _upload_asset(args: argparse.Namespace, local_path: Path, remote_path: str) -> None:
    _assert_safe_remote_path(args, remote_path)
    remote_dir = remote_path.rsplit("/", 1)[0]
    _run(_ssh_base(args) + [f"mkdir -p {_remote_quote(remote_dir)}"], quiet=True)
    _run(_scp_base(args) + [str(local_path), f"{args.server_user}@{args.server_host}:{remote_path}"], quiet=True)


def _upload_directory_archive(args: argparse.Namespace, local_dir: Path, remote_dir: str) -> tuple[int, int]:
    """Upload a directory as a tarball and replace the remote directory atomically enough for this workflow."""
    _assert_safe_remote_path(args, remote_dir)
    local_dir = local_dir.resolve()
    parent_remote = remote_dir.rstrip("/").rsplit("/", 1)[0]
    arc_root = remote_dir.rstrip("/").rsplit("/", 1)[-1]
    remote_tmp = f"/tmp/mathcontentstudio-sync-{uuid.uuid4().hex}.tar.gz"
    file_count = 0
    byte_count = 0
    files: list[Path] = []
    for root, _dirs, names in os.walk(local_dir, onerror=lambda _exc: None):
        for name in names:
            path = Path(root) / name
            if not path.is_file():
                continue
            files.append(path)
            file_count += 1
            try:
                byte_count += int(path.stat().st_size)
            except OSError:
                pass
    with tempfile.TemporaryDirectory(prefix="mathcontentstudio-sync-") as tmp:
        archive_path = Path(tmp) / f"{local_dir.name}.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(local_dir, arcname=arc_root, recursive=False)
            for path in files:
                try:
                    rel = path.relative_to(local_dir).as_posix()
                    tar.add(path, arcname=f"{arc_root}/{rel}", recursive=False)
                except OSError:
                    continue
        _run(_scp_base(args) + [str(archive_path), f"{args.server_user}@{args.server_host}:{remote_tmp}"], quiet=True)
    remote_cmd = (
        "set -e; "
        f"mkdir -p {_remote_quote(parent_remote)}; "
        f"rm -rf {_remote_quote(remote_dir)}; "
        f"tar -xzf {_remote_quote(remote_tmp)} -C {_remote_quote(parent_remote)}; "
        f"rm -f {_remote_quote(remote_tmp)}"
    )
    _run(_ssh_base(args) + [remote_cmd], quiet=True)
    return file_count, byte_count


def _normalize_asset_key(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip()


def _iter_image_path_values(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            lower_key = str(key or "").casefold()
            if isinstance(child, str) and "path" in lower_key:
                raw = _normalize_asset_key(child)
                if Path(raw).suffix.casefold() in IMAGE_SUFFIXES:
                    found.append(raw)
            elif isinstance(child, list) and "path" in lower_key:
                for item in child:
                    raw = _normalize_asset_key(item)
                    if raw and Path(raw).suffix.casefold() in IMAGE_SUFFIXES:
                        found.append(raw)
            else:
                found.extend(_iter_image_path_values(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_iter_image_path_values(child))
    return found


def _collect_external_staging_assets(staging_root: Path) -> dict[str, Path]:
    records_dir = staging_root / "records"
    assets: dict[str, Path] = {}
    if not records_dir.exists():
        return assets
    staging_root_resolved = staging_root.resolve()
    for record_path in records_dir.glob("*.json"):
        try:
            data = json.loads(record_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for raw in _iter_image_path_values(data):
            local_file = _resolve_local_file(raw)
            if local_file is None:
                continue
            try:
                local_file.resolve().relative_to(staging_root_resolved)
                continue
            except ValueError:
                pass
            assets.setdefault(_normalize_asset_key(raw), local_file)
    return assets


def _copy_augmented_staging(source_dir: Path, target_dir: Path) -> None:
    """Copy staging and include image assets referenced from outside that staging tree."""
    shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)
    external_assets = _collect_external_staging_assets(source_dir)
    if not external_assets:
        return

    external_dir = target_dir / STAGING_EXTERNAL_ASSETS_DIR
    external_dir.mkdir(parents=True, exist_ok=True)
    used_names: dict[str, Path] = {}
    asset_map: dict[str, str] = {}

    for raw_key, local_file in sorted(external_assets.items()):
        name = local_file.name
        previous = used_names.get(name)
        if previous is not None and previous.resolve() != local_file.resolve():
            digest = hashlib.sha1(raw_key.encode("utf-8", errors="ignore")).hexdigest()[:10]
            name = f"{local_file.stem}_{digest}{local_file.suffix}"
        used_names[name] = local_file
        target = external_dir / name
        if not target.exists():
            try:
                shutil.copy2(local_file, target)
            except OSError:
                continue
        asset_map[raw_key] = f"{STAGING_EXTERNAL_ASSETS_DIR}/{name}"

    if asset_map:
        (target_dir / STAGING_EXTERNAL_ASSETS_MAP).write_text(
            json.dumps({"assets": asset_map}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _upload_augmented_staging_archive(args: argparse.Namespace, local_dir: Path, remote_dir: str) -> tuple[int, int]:
    with tempfile.TemporaryDirectory(prefix="mathcontentstudio-staging-") as tmp:
        augmented = Path(tmp) / local_dir.name
        _copy_augmented_staging(local_dir, augmented)
        return _upload_directory_archive(args, augmented, remote_dir)


def _iter_directory_files(local_dir: Path) -> tuple[list[Path], int, int]:
    files: list[Path] = []
    file_count = 0
    byte_count = 0
    for root, _dirs, names in os.walk(local_dir, onerror=lambda _exc: None):
        for name in names:
            path = Path(root) / name
            if not path.is_file():
                continue
            files.append(path)
            file_count += 1
            try:
                byte_count += int(path.stat().st_size)
            except OSError:
                pass
    return files, file_count, byte_count


def _upload_directory_batch_archive(args: argparse.Namespace, items: list[tuple[Path, str]], *, label: str) -> tuple[int, int, int]:
    if not items:
        return 0, 0, 0
    root = str(args.remote_library_root or "").rstrip("/")
    for _local_dir, remote_dir in items:
        _assert_safe_remote_path(args, remote_dir)

    remote_tmp = f"/tmp/mathcontentstudio-sync-{uuid.uuid4().hex}.tar.gz"
    total_dirs = 0
    total_files = 0
    total_bytes = 0
    with tempfile.TemporaryDirectory(prefix="mathcontentstudio-sync-bulk-") as tmp:
        archive_path = Path(tmp) / f"{label}-{uuid.uuid4().hex}.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tar:
            for local_dir, remote_dir in items:
                local_dir = local_dir.resolve()
                rel_root = remote_dir.rstrip("/")[len(root) + 1 :]
                files, file_count, byte_count = _iter_directory_files(local_dir)
                total_dirs += 1
                total_files += file_count
                total_bytes += byte_count
                try:
                    tar.add(local_dir, arcname=rel_root, recursive=False)
                except OSError:
                    pass
                for path in files:
                    try:
                        rel = path.relative_to(local_dir).as_posix()
                        tar.add(path, arcname=f"{rel_root}/{rel}", recursive=False)
                    except OSError:
                        continue
        _run(_scp_base(args) + [str(archive_path), f"{args.server_user}@{args.server_host}:{remote_tmp}"], quiet=True)

    remove_cmd = " ".join(f"rm -rf {_remote_quote(remote_dir)};" for _local_dir, remote_dir in items)
    remote_cmd = (
        "set -e; "
        f"mkdir -p {_remote_quote(root)}; "
        f"{remove_cmd} "
        f"tar -xzf {_remote_quote(remote_tmp)} -C {_remote_quote(root)}; "
        f"rm -f {_remote_quote(remote_tmp)}"
    )
    _run(_ssh_base(args) + [remote_cmd], quiet=True)
    return total_dirs, total_files, total_bytes


def _upload_file_batch_archive(args: argparse.Namespace, items: list[tuple[Path, str]], *, label: str) -> tuple[int, int]:
    if not items:
        return 0, 0
    root = str(args.remote_library_root or "").rstrip("/")
    deduped: dict[str, Path] = {}
    for local_file, remote_path in items:
        _assert_safe_remote_path(args, remote_path)
        if local_file.exists() and local_file.is_file():
            deduped[remote_path] = local_file
    if not deduped:
        return 0, 0

    remote_tmp = f"/tmp/mathcontentstudio-sync-{uuid.uuid4().hex}.tar.gz"
    total_files = 0
    total_bytes = 0
    with tempfile.TemporaryDirectory(prefix="mathcontentstudio-sync-files-") as tmp:
        archive_path = Path(tmp) / f"{label}-{uuid.uuid4().hex}.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tar:
            for remote_path, local_file in deduped.items():
                rel = remote_path.rstrip("/")[len(root) + 1 :]
                total_files += 1
                try:
                    total_bytes += int(local_file.stat().st_size)
                except OSError:
                    pass
                try:
                    tar.add(local_file, arcname=rel, recursive=False)
                except OSError:
                    continue
        _run(_scp_base(args) + [str(archive_path), f"{args.server_user}@{args.server_host}:{remote_tmp}"], quiet=True)

    remote_cmd = (
        "set -e; "
        f"mkdir -p {_remote_quote(root)}; "
        f"tar -xzf {_remote_quote(remote_tmp)} -C {_remote_quote(root)}; "
        f"rm -f {_remote_quote(remote_tmp)}"
    )
    _run(_ssh_base(args) + [remote_cmd], quiet=True)
    return total_files, total_bytes


def _local_context(local_book: dict[str, Any], local_instance: dict[str, Any]) -> InstancePipelineContext:
    return InstancePipelineContext.from_library_instance(local_book, local_instance)


def _prepare_book_payload(
    args: argparse.Namespace,
    local_book: dict[str, Any],
    server_book: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[tuple[Path, str]]]:
    payload = dict(local_book)
    code = _clean(local_book.get("codigo"))
    remote_dir = _remote_book_dir(args.remote_library_root, code)
    uploads: list[tuple[Path, str]] = []

    if code:
        payload["workspace_dir"] = remote_dir

    cover = _resolve_local_file(local_book.get("cover_path"))
    if cover is not None and code:
        ext = cover.suffix.lower() or ".png"
        payload["cover_path"] = f"{remote_dir}/cover{ext}"
        uploads.append((cover, payload["cover_path"]))
    else:
        payload["cover_path"] = _clean((server_book or {}).get("cover_path"))

    if args.include_pdfs:
        pdf = _resolve_local_file(local_book.get("pdf_path"))
        if pdf is not None and code:
            payload["pdf_path"] = f"{remote_dir}/source.pdf"
            uploads.append((pdf, payload["pdf_path"]))
        else:
            payload["pdf_path"] = _clean((server_book or {}).get("pdf_path"))
    else:
        payload["pdf_path"] = _clean((server_book or {}).get("pdf_path"))

    local_session = _resolve_local_file(local_book.get("session_path"))
    if local_session is not None and code:
        payload["session_path"] = f"{remote_dir}/sessions/{local_session.name}"
        uploads.append((local_session, payload["session_path"]))
    else:
        payload["session_path"] = _clean((server_book or {}).get("session_path"))

    for field, folder_name in (("segmentos_dir", "segments"), ("soluciones_dir", "solutions")):
        local_dir = _resolve_local_dir(local_book.get(field))
        if local_dir is not None and code:
            payload[field] = f"{remote_dir}/{folder_name}"
        else:
            payload[field] = _clean((server_book or {}).get(field))

    return payload, uploads


def _prepare_instance_payload(
    args: argparse.Namespace,
    local_book: dict[str, Any],
    local_instance: dict[str, Any],
    server_book: dict[str, Any] | None,
    server_instance: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[tuple[Path, str]], list[tuple[Path, str, str]]]:
    payload = dict(local_instance)
    book_code = _clean(local_book.get("codigo"))
    instance_code = _clean(local_instance.get("codigo_instancia"))
    remote_book = _remote_book_dir(args.remote_library_root, book_code)
    remote_layout = _remote_project_dirs(remote_book, instance_code)
    uploads: list[tuple[Path, str]] = []
    dir_uploads: list[tuple[Path, str, str]] = []

    local_instance_pdf = _resolve_local_file(local_instance.get("pdf_path"))
    local_book_pdf = _resolve_local_file(local_book.get("pdf_path"))
    if args.include_instance_pdfs and local_instance_pdf is not None:
        payload["pdf_path"] = f"{remote_layout['sources_dir']}/{local_instance_pdf.name}"
        uploads.append((local_instance_pdf, payload["pdf_path"]))
    elif args.include_pdfs and local_book_pdf is not None:
        payload["pdf_path"] = f"{remote_book}/source.pdf"
    else:
        payload["pdf_path"] = _clean((server_instance or {}).get("pdf_path")) or _clean((server_book or {}).get("pdf_path"))

    local_session = _resolve_local_file(local_instance.get("session_path"))
    if local_session is not None:
        payload["session_path"] = remote_layout["session_path"]
        uploads.append((local_session, payload["session_path"]))
    else:
        payload["session_path"] = _clean((server_instance or {}).get("session_path"))

    local_solutions = _resolve_local_dir(local_instance.get("soluciones_dir"))
    if args.include_solutions and local_solutions is not None:
        payload["soluciones_dir"] = remote_layout["solutions_dir"]
        dir_uploads.append((local_solutions, payload["soluciones_dir"], "solutions"))
    else:
        payload["soluciones_dir"] = _clean((server_instance or {}).get("soluciones_dir"))

    if args.include_staging:
        staging_root = _local_context(local_book, local_instance).staging_root()
        if staging_root.exists() and staging_root.is_dir():
            dir_uploads.append((staging_root, remote_layout["staging_root"], "staging"))

    return payload, uploads, dir_uploads


def _insert_book(conn, row: dict[str, Any]) -> int:
    cur = conn.cursor()
    try:
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
            RETURNING id
            """,
            tuple(row.get(field) for field in BOOK_SYNC_FIELDS),
        )
        new_id = int(cur.fetchone()[0])
        conn.commit()
        return new_id
    finally:
        cur.close()


def _update_book(conn, server_id: int, row: dict[str, Any]) -> None:
    cur = conn.cursor()
    try:
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
            WHERE id = %s
            """,
            tuple(row.get(field) for field in BOOK_SYNC_FIELDS[1:]) + (int(server_id),),
        )
        conn.commit()
    finally:
        cur.close()


def _insert_instance(conn, server_book_id: int, row: dict[str, Any]) -> int:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO libro_instancias_escaneo (
                libro_id, codigo_instancia, total_esperado, pdf_path, session_path,
                soluciones_dir, activo, notas, nombre_instancia, estado, config_snapshot, session_schema_version
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            RETURNING id
            """,
            (
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
            ),
        )
        new_id = int(cur.fetchone()[0])
        conn.commit()
        return new_id
    finally:
        cur.close()


def _update_instance(conn, server_id: int, row: dict[str, Any]) -> None:
    cur = conn.cursor()
    try:
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
            WHERE id = %s
            """,
            (
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
            ),
        )
        conn.commit()
    finally:
        cur.close()


def _insert_instances_batch(conn, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    cur = conn.cursor()
    try:
        sql = """
            INSERT INTO libro_instancias_escaneo (
                libro_id, codigo_instancia, total_esperado, pdf_path, session_path,
                soluciones_dir, activo, notas, nombre_instancia, estado, config_snapshot, session_schema_version
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
        """
        if execute_batch is None:
            cur.executemany(sql, rows)
        else:
            execute_batch(cur, sql, rows, page_size=100)
        conn.commit()
    finally:
        cur.close()


def _update_instances_batch(conn, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    cur = conn.cursor()
    try:
        sql = """
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
            WHERE id = %s
        """
        if execute_batch is None:
            cur.executemany(sql, rows)
        else:
            execute_batch(cur, sql, rows, page_size=100)
        conn.commit()
    finally:
        cur.close()


def sync(args: argparse.Namespace) -> dict[str, int]:
    local = DatabaseManager.from_profile("local_mirror").get_connection(args.local_db)
    cloud = DatabaseManager.from_profile("cloud").get_connection(args.cloud_db)
    summary = {
        "books_inserted": 0,
        "books_updated": 0,
        "books_skipped": 0,
        "covers_uploaded": 0,
        "pdfs_uploaded": 0,
        "sessions_uploaded": 0,
        "staging_dirs_uploaded": 0,
        "staging_files_uploaded": 0,
        "staging_mb_uploaded": 0,
        "solutions_dirs_uploaded": 0,
        "solutions_files_uploaded": 0,
        "solutions_mb_uploaded": 0,
        "instance_pdfs_uploaded": 0,
        "instances_inserted": 0,
        "instances_updated": 0,
        "instances_skipped": 0,
        "instances_deactivated": 0,
    }
    try:
        local_books = _fetch_all(local, "SELECT * FROM libros_escaneo ORDER BY id ASC")
        server_books = _fetch_all(cloud, "SELECT * FROM libros_escaneo ORDER BY id ASC")
        server_by_code = {_clean(row.get("codigo")): row for row in server_books if _clean(row.get("codigo"))}
        local_code_by_id = {int(row["id"]): _clean(row.get("codigo")) for row in local_books}
        local_book_by_id = {int(row["id"]): row for row in local_books}
        server_id_by_code: dict[str, int] = {}

        if not args.instances_only:
            for local_book in local_books:
                code = _clean(local_book.get("codigo"))
                if not code:
                    summary["books_skipped"] += 1
                    continue
                server_book = server_by_code.get(code)
                payload, uploads = _prepare_book_payload(args, local_book, server_book)
                if not args.dry_run and not args.metadata_only:
                    for local_path, remote_path in uploads:
                        _upload_asset(args, local_path, remote_path)
                        if Path(remote_path).suffix.lower() == ".pdf":
                            summary["pdfs_uploaded"] += 1
                        elif remote_path.endswith(".session.json") or "/sessions/" in remote_path:
                            summary["sessions_uploaded"] += 1
                        else:
                            summary["covers_uploaded"] += 1
                if server_book is None:
                    if not args.dry_run:
                        server_id = _insert_book(cloud, payload)
                    else:
                        server_id = -1
                    payload["id"] = int(server_id)
                    server_by_code[code] = dict(payload)
                    server_id_by_code[code] = int(server_id)
                    summary["books_inserted"] += 1
                else:
                    server_id_by_code[code] = int(server_book["id"])
                    if not args.dry_run:
                        _update_book(cloud, int(server_book["id"]), payload)
                    merged = dict(server_book)
                    merged.update(payload)
                    merged["id"] = int(server_book["id"])
                    server_by_code[code] = merged
                    summary["books_updated"] += 1

        server_books = _fetch_all(cloud, "SELECT * FROM libros_escaneo ORDER BY id ASC")
        server_id_by_code = {_clean(row.get("codigo")): int(row["id"]) for row in server_books if _clean(row.get("codigo"))}
        server_by_code = {_clean(row.get("codigo")): row for row in server_books if _clean(row.get("codigo"))}

        local_instances = _fetch_all(local, "SELECT * FROM libro_instancias_escaneo ORDER BY libro_id ASC, id ASC")
        if int(args.max_instances or 0) > 0:
            local_instances = local_instances[: int(args.max_instances)]
        server_instances = _fetch_all(cloud, "SELECT * FROM libro_instancias_escaneo ORDER BY libro_id ASC, id ASC")
        server_instance_by_key: dict[tuple[int, str], dict[str, Any]] = {}
        for row in server_instances:
            code = _clean(row.get("codigo_instancia"))
            if code:
                server_instance_by_key[(int(row["libro_id"]), code)] = row

        insert_rows: list[tuple[Any, ...]] = []
        update_rows: list[tuple[Any, ...]] = []
        local_instance_keys: set[tuple[int, str]] = set()
        for local_instance in local_instances:
            local_book_id = local_instance.get("libro_id")
            code = local_code_by_id.get(int(local_book_id or 0), "")
            server_book_id = server_id_by_code.get(code)
            instance_code = _clean(local_instance.get("codigo_instancia"))
            if not server_book_id or not instance_code:
                summary["instances_skipped"] += 1
                continue
            local_instance_keys.add((int(server_book_id), instance_code))
            local_book = local_book_by_id.get(int(local_book_id or 0))
            if local_book is None:
                summary["instances_skipped"] += 1
                continue
            existing = server_instance_by_key.get((int(server_book_id), instance_code))
            payload, uploads, dir_uploads = _prepare_instance_payload(
                args,
                local_book,
                local_instance,
                server_by_code.get(code),
                existing,
            )
            if not args.dry_run and not args.metadata_only:
                for local_path, remote_path in uploads:
                    _upload_asset(args, local_path, remote_path)
                    if Path(remote_path).suffix.lower() == ".pdf":
                        summary["instance_pdfs_uploaded"] += 1
                    else:
                        summary["sessions_uploaded"] += 1
                for local_dir, remote_dir, kind in dir_uploads:
                    if kind == "staging":
                        file_count, byte_count = _upload_augmented_staging_archive(args, local_dir, remote_dir)
                    else:
                        file_count, byte_count = _upload_directory_archive(args, local_dir, remote_dir)
                    if kind == "staging":
                        summary["staging_dirs_uploaded"] += 1
                        summary["staging_files_uploaded"] += int(file_count)
                        summary["staging_mb_uploaded"] += int(round(byte_count / 1024 / 1024))
                    elif kind == "solutions":
                        summary["solutions_dirs_uploaded"] += 1
                        summary["solutions_files_uploaded"] += int(file_count)
                        summary["solutions_mb_uploaded"] += int(round(byte_count / 1024 / 1024))
            if existing is None:
                insert_rows.append(
                    (
                        int(server_book_id),
                        payload.get("codigo_instancia"),
                        payload.get("total_esperado"),
                        payload.get("pdf_path"),
                        payload.get("session_path"),
                        payload.get("soluciones_dir"),
                        payload.get("activo"),
                        payload.get("notas"),
                        payload.get("nombre_instancia"),
                        payload.get("estado"),
                        json.dumps(payload.get("config_snapshot") or {}, ensure_ascii=False),
                        payload.get("session_schema_version"),
                    )
                )
                summary["instances_inserted"] += 1
            else:
                update_rows.append(
                    (
                        payload.get("total_esperado"),
                        payload.get("pdf_path"),
                        payload.get("session_path"),
                        payload.get("soluciones_dir"),
                        payload.get("activo"),
                        payload.get("notas"),
                        payload.get("nombre_instancia"),
                        payload.get("estado"),
                        json.dumps(payload.get("config_snapshot") or {}, ensure_ascii=False),
                        payload.get("session_schema_version"),
                        int(existing["id"]),
                    )
                )
                summary["instances_updated"] += 1
        if not args.dry_run:
            _insert_instances_batch(cloud, insert_rows)
            _update_instances_batch(cloud, update_rows)
            if args.deactivate_missing_instances:
                local_server_book_ids = {
                    int(server_id_by_code[code])
                    for code in set(local_code_by_id.values())
                    if code and code in server_id_by_code
                }
                missing_ids = [
                    int(row["id"])
                    for row in server_instances
                    if int(row.get("libro_id") or 0) in local_server_book_ids
                    and _clean(row.get("codigo_instancia"))
                    and (int(row.get("libro_id") or 0), _clean(row.get("codigo_instancia"))) not in local_instance_keys
                    and bool(row.get("activo"))
                ]
                if missing_ids:
                    cur = cloud.cursor()
                    try:
                        cur.execute(
                            "UPDATE libro_instancias_escaneo SET activo = false, updated_at = NOW() WHERE id = ANY(%s)",
                            (missing_ids,),
                        )
                        cloud.commit()
                    finally:
                        cur.close()
                    summary["instances_deactivated"] = len(missing_ids)
        elif args.deactivate_missing_instances:
            local_server_book_ids = {
                int(server_id_by_code[code])
                for code in set(local_code_by_id.values())
                if code and code in server_id_by_code
            }
            summary["instances_deactivated"] = sum(
                1
                for row in server_instances
                if int(row.get("libro_id") or 0) in local_server_book_ids
                and _clean(row.get("codigo_instancia"))
                and (int(row.get("libro_id") or 0), _clean(row.get("codigo_instancia"))) not in local_instance_keys
                and bool(row.get("activo"))
            )
    finally:
        local.close()
        cloud.close()
    return summary


def _collect_staging_items(args: argparse.Namespace) -> list[tuple[Path, str]]:
    local = DatabaseManager.from_profile("local_mirror").get_connection(args.local_db)
    try:
        local_books = _fetch_all(local, "SELECT * FROM libros_escaneo ORDER BY id ASC")
        local_instances = _fetch_all(local, "SELECT * FROM libro_instancias_escaneo ORDER BY libro_id ASC, id ASC")
        local_book_by_id = {int(row["id"]): row for row in local_books}
        items: list[tuple[Path, str]] = []
        for local_instance in local_instances:
            local_book = local_book_by_id.get(int(local_instance.get("libro_id") or 0))
            if local_book is None:
                continue
            book_code = _clean(local_book.get("codigo"))
            instance_code = _clean(local_instance.get("codigo_instancia"))
            if not book_code or not instance_code:
                continue
            local_staging = _local_context(local_book, local_instance).staging_root()
            if not local_staging.exists() or not local_staging.is_dir():
                continue
            remote_book = _remote_book_dir(args.remote_library_root, book_code)
            remote_layout = _remote_project_dirs(remote_book, instance_code)
            items.append((local_staging, remote_layout["staging_root"]))
        if int(args.max_instances or 0) > 0:
            return items[: int(args.max_instances)]
        return items
    finally:
        local.close()


def sync_staging_only(args: argparse.Namespace) -> dict[str, int]:
    items = _collect_staging_items(args)
    summary = {
        "staging_dirs_planned": len(items),
        "staging_dirs_uploaded": 0,
        "staging_files_uploaded": 0,
        "staging_mb_uploaded": 0,
    }
    if args.dry_run:
        return summary

    batch_size = max(int(args.staging_batch_size or 0), 1)
    total = len(items)
    for offset in range(0, total, batch_size):
        batch = items[offset : offset + batch_size]
        print(
            f"Subiendo staging {offset + 1}-{offset + len(batch)} de {total}...",
            flush=True,
        )
        with tempfile.TemporaryDirectory(prefix="mathcontentstudio-staging-batch-") as tmp:
            augmented_batch: list[tuple[Path, str]] = []
            for index, (local_dir, remote_dir) in enumerate(batch, start=1):
                augmented = Path(tmp) / f"{index:03d}-{local_dir.name}"
                _copy_augmented_staging(local_dir, augmented)
                augmented_batch.append((augmented, remote_dir))
            dirs, files, bytes_ = _upload_directory_batch_archive(
                args,
                augmented_batch,
                label=f"staging-{offset + 1}-{offset + len(batch)}",
            )
        summary["staging_dirs_uploaded"] += int(dirs)
        summary["staging_files_uploaded"] += int(files)
        summary["staging_mb_uploaded"] += int(round(bytes_ / 1024 / 1024))
    return summary


def _collect_instance_file_items(args: argparse.Namespace) -> list[tuple[Path, str, str]]:
    local = DatabaseManager.from_profile("local_mirror").get_connection(args.local_db)
    try:
        local_books = _fetch_all(local, "SELECT * FROM libros_escaneo ORDER BY id ASC")
        local_instances = _fetch_all(local, "SELECT * FROM libro_instancias_escaneo ORDER BY libro_id ASC, id ASC")
        local_book_by_id = {int(row["id"]): row for row in local_books}
        items: list[tuple[Path, str, str]] = []
        for local_instance in local_instances:
            local_book = local_book_by_id.get(int(local_instance.get("libro_id") or 0))
            if local_book is None:
                continue
            book_code = _clean(local_book.get("codigo"))
            instance_code = _clean(local_instance.get("codigo_instancia"))
            if not book_code or not instance_code:
                continue
            remote_book = _remote_book_dir(args.remote_library_root, book_code)
            remote_layout = _remote_project_dirs(remote_book, instance_code)
            local_session = _resolve_local_file(local_instance.get("session_path"))
            if local_session is not None:
                items.append((local_session, remote_layout["session_path"], "session"))
            if args.include_instance_pdfs:
                local_instance_pdf = _resolve_local_file(local_instance.get("pdf_path"))
                if local_instance_pdf is not None:
                    items.append((local_instance_pdf, f"{remote_layout['sources_dir']}/{local_instance_pdf.name}", "instance_pdf"))
        if int(args.max_instances or 0) > 0:
            return items[: int(args.max_instances)]
        return items
    finally:
        local.close()


def sync_instance_files_only(args: argparse.Namespace) -> dict[str, int]:
    items_with_kind = _collect_instance_file_items(args)
    summary = {
        "files_planned": len(items_with_kind),
        "files_uploaded": 0,
        "mb_uploaded": 0,
        "sessions_uploaded": 0,
        "instance_pdfs_uploaded": 0,
    }
    if args.dry_run:
        return summary

    batch_size = max(int(args.file_batch_size or 0), 1)
    total = len(items_with_kind)
    for offset in range(0, total, batch_size):
        batch_with_kind = items_with_kind[offset : offset + batch_size]
        batch = [(local_path, remote_path) for local_path, remote_path, _kind in batch_with_kind]
        print(
            f"Subiendo archivos de instancia {offset + 1}-{offset + len(batch)} de {total}...",
            flush=True,
        )
        files, bytes_ = _upload_file_batch_archive(
            args,
            batch,
            label=f"instance-files-{offset + 1}-{offset + len(batch)}",
        )
        summary["files_uploaded"] += int(files)
        summary["mb_uploaded"] += int(round(bytes_ / 1024 / 1024))
        summary["sessions_uploaded"] += sum(1 for _local, _remote, kind in batch_with_kind if kind == "session")
        summary["instance_pdfs_uploaded"] += sum(1 for _local, _remote, kind in batch_with_kind if kind == "instance_pdf")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sincroniza biblioteca local hacia servidor NexumathJF.")
    parser.add_argument("--local-db", default="mathcontentstudio_local_mirror")
    parser.add_argument("--cloud-db", default="mathcontentstudio")
    parser.add_argument("--server-host", default=os.getenv("MATH_BANK_SERVER_HOST", DEFAULT_HOST))
    parser.add_argument("--server-user", default=os.getenv("MATH_BANK_SERVER_USER", DEFAULT_USER))
    parser.add_argument(
        "--identity-file",
        type=Path,
        default=Path(os.getenv("MATH_BANK_IDENTITY_FILE", r"C:\Users\DANNYF~1\.ssh\LightsailDefaultKey-us-east-1.pem")),
    )
    parser.add_argument("--remote-library-root", default=os.getenv("MATH_BANK_REMOTE_LIBRARY_ROOT", DEFAULT_REMOTE_LIBRARY_ROOT))
    parser.add_argument("--include-pdfs", action="store_true", help="Tambien sube PDFs fuente. Puede ser pesado.")
    parser.add_argument("--include-instance-pdfs", action="store_true", help="Sube PDFs especificos de instancia cuando existan.")
    parser.add_argument("--include-staging", action="store_true", help="Sube staging completo: crops, records, OCR, segmentos y normalizacion.")
    parser.add_argument("--include-solutions", action="store_true", help="Sube carpetas de soluciones generadas por instancia.")
    parser.add_argument("--instances-only", action="store_true", help="Solo sincroniza instancias; no toca libros ni assets.")
    parser.add_argument("--staging-only", action="store_true", help="Solo sube staging completo en lotes; no toca la BD remota.")
    parser.add_argument("--instance-files-only", action="store_true", help="Solo sube session.json y PDFs de instancia en lotes; no toca la BD remota.")
    parser.add_argument("--metadata-only", action="store_true", help="Actualiza BD remota sin subir archivos.")
    parser.add_argument(
        "--deactivate-missing-instances",
        action="store_true",
        help="Marca inactivas las instancias remotas de libros locales que ya no existen en la base local.",
    )
    parser.add_argument("--staging-batch-size", type=int, default=10, help="Cantidad de staging dirs por tarball en --staging-only.")
    parser.add_argument("--file-batch-size", type=int, default=200, help="Cantidad de archivos por tarball en --instance-files-only.")
    parser.add_argument("--max-instances", type=int, default=0, help="Limita instancias procesadas para pruebas.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.staging_only:
        summary = sync_staging_only(args)
    elif args.instance_files_only:
        summary = sync_instance_files_only(args)
    else:
        summary = sync(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
