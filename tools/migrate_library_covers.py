from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modulos.instance_factory.library_covers import (
    ALLOWED_COVER_SUFFIXES,
    copy_cover_to_library_store,
    library_cover_root,
)
from utils.project_layout import remap_legacy_drive_path


def migrate_existing_covers(
    controller: Any,
    *,
    db_names: list[str] | None = None,
    commit: bool = False,
) -> dict[str, Any]:
    selected_dbs = [str(name).strip() for name in (db_names or []) if str(name).strip()]
    if not selected_dbs:
        selected_dbs = [str(name) for name in controller.listar_bases_datos()]

    report: dict[str, Any] = {
        "schema_version": "library_cover_migration_v1",
        "commit": bool(commit),
        "cover_root": str(library_cover_root()),
        "databases": [],
        "totals": _empty_counts(),
    }
    for db_name in selected_dbs:
        db_report = _migrate_db(controller, db_name, commit=commit)
        report["databases"].append(db_report)
        _merge_counts(report["totals"], db_report["counts"])
    return report


def _migrate_db(controller: Any, db_name: str, *, commit: bool) -> dict[str, Any]:
    counts = _empty_counts()
    items: list[dict[str, Any]] = []
    books = controller.listar_libros(db_name)
    counts["books"] = len(books)

    for row in books:
        book = dict(row)
        book_id = _book_id(book)
        raw_cover = _first_text(book, "cover_path", "cover_path_local", "cover_path_mirror", "cover_path_server")
        item: dict[str, Any] = {
            "book_id": book_id,
            "codigo": str(book.get("codigo") or ""),
            "titulo": str(book.get("titulo") or ""),
            "source": raw_cover,
            "target": "",
            "status": "",
            "error": "",
        }

        if not raw_cover:
            counts["without_cover"] += 1
            item["status"] = "without_cover"
            items.append(item)
            continue

        source = _resolve_cover_path(raw_cover)
        if source is None:
            counts["missing"] += 1
            item["status"] = "missing"
            items.append(item)
            continue
        if source.suffix.lower() not in ALLOWED_COVER_SUFFIXES:
            counts["unsupported"] += 1
            item["status"] = "unsupported"
            item["target"] = str(source)
            items.append(item)
            continue

        try:
            target = copy_cover_to_library_store(str(source), {**book, "id": book_id}, db_name=db_name)
            item["target"] = target
            if _same_path(source, Path(target)):
                counts["already_central"] += 1
                item["status"] = "already_central"
            else:
                counts["copied"] += 1
                item["status"] = "copied"
            if commit and target and book_id > 0:
                _update_cover_path(controller, db_name, book_id, target)
                counts["updated"] += 1
        except Exception as exc:
            counts["errors"] += 1
            item["status"] = "error"
            item["error"] = str(exc)
        items.append(item)

    return {
        "db_name": db_name,
        "counts": counts,
        "items": items,
    }


def _update_cover_path(controller: Any, db_name: str, book_id: int, cover_path: str) -> None:
    ensure = getattr(controller, "_ensure_schema", None)
    if callable(ensure):
        ensure(db_name)
    db = getattr(controller, "db", None)
    if db is None or not hasattr(db, "get_connection"):
        raise RuntimeError("El controller no expone conexion directa para actualizar cover_path.")

    conn = db.get_connection(db_name)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE libros_escaneo
            SET cover_path = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (str(cover_path), int(book_id)),
        )
        if _pg_table_exists(cur, "libro_artifacts_locales"):
            cur.execute(
                """
                UPDATE libro_artifacts_locales
                SET cover_path_local = %s,
                    updated_at = NOW()
                WHERE libro_id = %s
                  AND COALESCE(cover_path_local, '') <> ''
                """,
                (str(cover_path), int(book_id)),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _pg_table_exists(cur: Any, table_name: str) -> bool:
    try:
        cur.execute("SELECT to_regclass(%s)", (f"public.{table_name}",))
        row = cur.fetchone()
        return bool(row and row[0])
    except Exception:
        return False


def _resolve_cover_path(raw_cover: str) -> Path | None:
    try:
        source = remap_legacy_drive_path(Path(raw_cover).expanduser(), prefer_existing=True).resolve()
    except Exception:
        return None
    try:
        if source.exists() and source.is_file():
            return source
    except Exception:
        return None
    return None


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except Exception:
        return str(left) == str(right)


def _book_id(book: dict[str, Any]) -> int:
    for key in ("id", "book_id", "libro_id"):
        try:
            value = int(book.get(key) or 0)
        except Exception:
            value = 0
        if value > 0:
            return value
    return 0


def _first_text(book: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(book.get(key) or "").strip()
        if value:
            return value
    return ""


def _empty_counts() -> dict[str, int]:
    return {
        "books": 0,
        "without_cover": 0,
        "missing": 0,
        "unsupported": 0,
        "already_central": 0,
        "copied": 0,
        "updated": 0,
        "errors": 0,
    }


def _merge_counts(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        target[key] = int(target.get(key) or 0) + int(value or 0)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Organiza portadas existentes de Biblioteca en el almacen central.")
    parser.add_argument("--db", action="append", default=[], help="Base de datos a migrar. Puede repetirse.")
    parser.add_argument("--all-dbs", action="store_true", help="Migrar todas las bases de datos detectadas.")
    parser.add_argument(
        "--profile",
        default="auto",
        help="Perfil de BD a usar: auto, local_mirror, cloud o none. Por defecto usa el mismo perfil del launcher.",
    )
    parser.add_argument("--apply", action="store_true", help="Aplicar cambios en BD. Sin esto solo hace dry-run.")
    parser.add_argument("--json", action="store_true", help="Imprimir reporte JSON completo.")
    return parser.parse_args(argv)


def _configure_db_profile(profile: str) -> dict[str, str]:
    selected = str(profile or "auto").strip().lower()
    if selected in {"", "auto"}:
        import main as auditor_main

        selected = auditor_main._default_db_profile()  # type: ignore[attr-defined]
        return auditor_main._apply_db_profile(selected)  # type: ignore[attr-defined]
    if selected in {"none", "current", "env"}:
        return {
            "profile": "env",
            "name": "",
        }
    import main as auditor_main

    return auditor_main._apply_db_profile(selected)  # type: ignore[attr-defined]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv or sys.argv[1:]))
    db_config = _configure_db_profile(str(args.profile or "auto"))
    from modulos.modulo9_organizador_libros.controlador_organizador_libros import BookProgressController

    controller = BookProgressController()
    db_names = [] if args.all_dbs else list(args.db or [])
    report = migrate_existing_covers(controller, db_names=db_names, commit=bool(args.apply))
    report["db_profile"] = str(db_config.get("profile") or "")
    report["db_name"] = str(db_config.get("name") or "")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        totals = report["totals"]
        mode = "APLICADO" if report["commit"] else "DRY-RUN"
        print(f"{mode} | perfil: {report['db_profile']} | bd: {report['db_name']} | almacen: {report['cover_root']}")
        print(
            "libros={books} copiadas={copied} actualizadas={updated} ya_centrales={already_central} "
            "sin_portada={without_cover} faltantes={missing} no_soportadas={unsupported} errores={errors}".format(**totals)
        )
        for db_report in report["databases"]:
            counts = db_report["counts"]
            print(
                "- {db_name}: libros={books} copiadas={copied} actualizadas={updated} "
                "ya_centrales={already_central} errores={errors}".format(
                    db_name=db_report["db_name"],
                    **counts,
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
