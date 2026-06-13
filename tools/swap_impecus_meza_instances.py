from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.connection import DatabaseManager
from utils.project_layout import project_dirs, remap_legacy_drive_path


OLD_INSTANCE = "problemas_propuestos"
NEW_INSTANCE = "problemas_resueltos"
TEXT_SUFFIXES = {".json", ".jsonl", ".txt", ".md", ".csv", ".log"}


def _utc_stamp() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def _table_exists(cur, table: str) -> bool:
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = %s
        )
        """,
        (table,),
    )
    return bool(cur.fetchone()[0])


def _column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
              AND column_name = %s
        )
        """,
        (table, column),
    )
    return bool(cur.fetchone()[0])


def _instance_column(cur) -> str:
    return "codigo_instancia" if _column_exists(cur, "libro_instancias_escaneo", "codigo_instancia") else "tipo"


def _problem_instance_column(cur) -> str:
    return "codigo_instancia" if _column_exists(cur, "problemas", "codigo_instancia") else "instancia_tipo"


def _fetch_target_books(cur, book_ids: list[int] | None = None) -> list[dict[str, Any]]:
    where = """
        LOWER(COALESCE(editorial, '')) LIKE '%%impecus%%'
        AND LOWER(COALESCE(autor, '')) LIKE '%%meza%%'
        AND LOWER(COALESCE(autor, '')) LIKE '%%barcena%%'
    """
    params: list[Any] = []
    if book_ids:
        placeholders = ", ".join(["%s"] * len(book_ids))
        where = f"id IN ({placeholders})"
        params.extend(book_ids)
    cur.execute(
        f"""
        SELECT id, codigo, titulo, autor, editorial, COALESCE(workspace_dir, '') AS workspace_dir
        FROM libros_escaneo
        WHERE {where}
        ORDER BY id
        """,
        tuple(params),
    )
    cols = [desc[0] for desc in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _fetch_instances(cur, book_id: int, instance_col: str) -> list[dict[str, Any]]:
    cur.execute(
        f"""
        SELECT id, libro_id, {instance_col} AS codigo_instancia, total_esperado,
               COALESCE(session_path, '') AS session_path,
               COALESCE(soluciones_dir, '') AS soluciones_dir,
               activo, COALESCE(notas, '') AS notas
        FROM libro_instancias_escaneo
        WHERE libro_id = %s
        ORDER BY id
        """,
        (book_id,),
    )
    cols = [desc[0] for desc in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _problem_counts(cur, book_code: str, problem_instance_col: str) -> dict[str, int]:
    if not _table_exists(cur, "problemas"):
        return {}
    cur.execute(
        f"""
        SELECT COALESCE({problem_instance_col}, '') AS codigo_instancia, COUNT(*)::int
        FROM problemas
        WHERE libro_codigo = %s
        GROUP BY COALESCE({problem_instance_col}, '')
        ORDER BY COALESCE({problem_instance_col}, '')
        """,
        (book_code,),
    )
    return {str(row[0] or ""): int(row[1] or 0) for row in cur.fetchall()}


def _pending_counts(cur, book_code: str) -> dict[str, int]:
    if not _table_exists(cur, "problema_pending_changes"):
        return {}
    if not (_column_exists(cur, "problema_pending_changes", "libro_codigo") and _column_exists(cur, "problema_pending_changes", "codigo_instancia")):
        return {}
    cur.execute(
        """
        SELECT COALESCE(codigo_instancia, '') AS codigo_instancia, COUNT(*)::int
        FROM problema_pending_changes
        WHERE libro_codigo = %s
        GROUP BY COALESCE(codigo_instancia, '')
        ORDER BY COALESCE(codigo_instancia, '')
        """,
        (book_code,),
    )
    return {str(row[0] or ""): int(row[1] or 0) for row in cur.fetchall()}


def _path_info(path: Path) -> dict[str, Any]:
    path = remap_legacy_drive_path(path, prefer_existing=True)
    info: dict[str, Any] = {"path": str(path), "exists": path.exists(), "kind": "missing", "items": 0}
    if path.is_file():
        info.update({"kind": "file", "items": 1, "size": path.stat().st_size})
    elif path.is_dir():
        count = 0
        for _ in path.rglob("*"):
            count += 1
        info.update({"kind": "dir", "items": count})
    return info


def _paths_for_workspace(workspace_dir: str, instance_name: str) -> dict[str, Path]:
    root = remap_legacy_drive_path(Path(workspace_dir).expanduser(), prefer_existing=True)
    layout = project_dirs(root, instance_name)
    return {
        "instance_root": remap_legacy_drive_path(layout["instance_root"], prefer_existing=True),
        "session_path": remap_legacy_drive_path(layout["session_path"], prefer_existing=True),
        "solutions_dir": remap_legacy_drive_path(layout["solutions_dir"], prefer_existing=True),
    }


def _swap_path(left: Path, right: Path, *, apply: bool, stamp: str) -> dict[str, Any]:
    left = remap_legacy_drive_path(left, prefer_existing=True)
    right = remap_legacy_drive_path(right, prefer_existing=True)
    result = {
        "left": str(left),
        "right": str(right),
        "left_exists_before": left.exists(),
        "right_exists_before": right.exists(),
        "action": "dry_run" if not apply else "none",
    }
    if not apply:
        return result
    if not left.exists() and not right.exists():
        result["action"] = "both_missing"
        return result
    left.parent.mkdir(parents=True, exist_ok=True)
    right.parent.mkdir(parents=True, exist_ok=True)
    tmp = left.with_name(f"{left.name}.__swap_tmp_{stamp}")
    if tmp.exists():
        raise FileExistsError(f"Ruta temporal ya existe: {tmp}")
    if left.exists() and right.exists():
        left.rename(tmp)
        right.rename(left)
        tmp.rename(right)
        result["action"] = "swapped"
    elif left.exists():
        left.rename(right)
        result["action"] = "left_to_right"
    else:
        right.rename(left)
        result["action"] = "right_to_left"
    result["left_exists_after"] = left.exists()
    result["right_exists_after"] = right.exists()
    return result


def _replacement_variants(old_path: Path, new_path: Path) -> list[tuple[str, str]]:
    old_text = str(old_path)
    new_text = str(new_path)
    pairs = [
        (old_text, new_text),
        (old_text.replace("\\", "/"), new_text.replace("\\", "/")),
    ]
    if len(old_text) > 2 and old_text[1:3] == ":\\":
        suffix = old_text[2:]
        for drive in ("D", "E", "C", "K"):
            pairs.append((f"{drive}:{suffix}", new_text))
            pairs.append((f"{drive}:{suffix}".replace("\\", "/"), new_text.replace("\\", "/")))
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for pair in pairs:
        if pair[0] and pair not in seen:
            seen.add(pair)
            unique.append(pair)
    return unique


def _rewrite_text_tree(root: Path, replacements: list[tuple[str, str]], *, apply: bool) -> dict[str, int]:
    root = remap_legacy_drive_path(root, prefer_existing=True)
    summary = {"files_seen": 0, "files_changed": 0}
    if not root.exists() or not root.is_dir():
        return summary
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        summary["files_seen"] += 1
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8-sig")
        except Exception:
            continue
        new_text = text
        for old, new in replacements:
            new_text = new_text.replace(old, new)
        if new_text != text:
            summary["files_changed"] += 1
            if apply:
                path.write_text(new_text, encoding="utf-8")
    return summary


def _backup_snapshot(cur, books: list[dict[str, Any]], instance_col: str, problem_instance_col: str, backup_path: Path) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "schema_version": "impecus_meza_instance_swap_backup_v1",
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "old_instance": OLD_INSTANCE,
        "new_instance": NEW_INSTANCE,
        "books": [],
    }
    for book in books:
        paths_old = _paths_for_workspace(str(book.get("workspace_dir") or ""), OLD_INSTANCE)
        paths_new = _paths_for_workspace(str(book.get("workspace_dir") or ""), NEW_INSTANCE)
        snapshot["books"].append(
            {
                "book": book,
                "instances": _fetch_instances(cur, int(book["id"]), instance_col),
                "problem_counts": _problem_counts(cur, str(book.get("codigo") or ""), problem_instance_col),
                "pending_counts": _pending_counts(cur, str(book.get("codigo") or "")),
                "paths": {
                    OLD_INSTANCE: {key: _path_info(path) for key, path in paths_old.items()},
                    NEW_INSTANCE: {key: _path_info(path) for key, path in paths_new.items()},
                },
            }
        )
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return snapshot


def _swap_db_rows(cur, book: dict[str, Any], instance_col: str, problem_instance_col: str, stamp: str) -> dict[str, int]:
    book_id = int(book["id"])
    book_code = str(book.get("codigo") or "").strip()
    workspace_dir = str(book.get("workspace_dir") or "").strip()
    temp = f"__swap_tmp_{stamp}_{book_id}__"
    paths_old = _paths_for_workspace(workspace_dir, OLD_INSTANCE)
    paths_new = _paths_for_workspace(workspace_dir, NEW_INSTANCE)

    cur.execute(
        f"UPDATE libro_instancias_escaneo SET {instance_col} = %s WHERE libro_id = %s AND {instance_col} = %s",
        (temp, book_id, OLD_INSTANCE),
    )
    inst_old_to_temp = max(int(cur.rowcount or 0), 0)
    cur.execute(
        f"""
        UPDATE libro_instancias_escaneo
        SET {instance_col} = %s,
            session_path = %s,
            soluciones_dir = %s,
            updated_at = NOW()
        WHERE libro_id = %s AND {instance_col} = %s
        """,
        (OLD_INSTANCE, str(paths_old["session_path"]), str(paths_old["solutions_dir"]), book_id, NEW_INSTANCE),
    )
    inst_new_to_old = max(int(cur.rowcount or 0), 0)
    cur.execute(
        f"""
        UPDATE libro_instancias_escaneo
        SET {instance_col} = %s,
            session_path = %s,
            soluciones_dir = %s,
            updated_at = NOW()
        WHERE libro_id = %s AND {instance_col} = %s
        """,
        (NEW_INSTANCE, str(paths_new["session_path"]), str(paths_new["solutions_dir"]), book_id, temp),
    )
    inst_temp_to_new = max(int(cur.rowcount or 0), 0)

    problems_old_to_temp = problems_new_to_old = problems_temp_to_new = 0
    if _table_exists(cur, "problemas"):
        cur.execute(
            f"UPDATE problemas SET {problem_instance_col} = %s WHERE libro_codigo = %s AND {problem_instance_col} = %s",
            (temp, book_code, OLD_INSTANCE),
        )
        problems_old_to_temp = max(int(cur.rowcount or 0), 0)
        cur.execute(
            f"UPDATE problemas SET {problem_instance_col} = %s WHERE libro_codigo = %s AND {problem_instance_col} = %s",
            (OLD_INSTANCE, book_code, NEW_INSTANCE),
        )
        problems_new_to_old = max(int(cur.rowcount or 0), 0)
        cur.execute(
            f"UPDATE problemas SET {problem_instance_col} = %s WHERE libro_codigo = %s AND {problem_instance_col} = %s",
            (NEW_INSTANCE, book_code, temp),
        )
        problems_temp_to_new = max(int(cur.rowcount or 0), 0)

    pending_old_to_temp = pending_new_to_old = pending_temp_to_new = 0
    if _table_exists(cur, "problema_pending_changes") and _column_exists(cur, "problema_pending_changes", "libro_codigo") and _column_exists(
        cur,
        "problema_pending_changes",
        "codigo_instancia",
    ):
        cur.execute(
            "UPDATE problema_pending_changes SET codigo_instancia = %s WHERE libro_codigo = %s AND codigo_instancia = %s",
            (temp, book_code, OLD_INSTANCE),
        )
        pending_old_to_temp = max(int(cur.rowcount or 0), 0)
        cur.execute(
            "UPDATE problema_pending_changes SET codigo_instancia = %s WHERE libro_codigo = %s AND codigo_instancia = %s",
            (OLD_INSTANCE, book_code, NEW_INSTANCE),
        )
        pending_new_to_old = max(int(cur.rowcount or 0), 0)
        cur.execute(
            "UPDATE problema_pending_changes SET codigo_instancia = %s WHERE libro_codigo = %s AND codigo_instancia = %s",
            (NEW_INSTANCE, book_code, temp),
        )
        pending_temp_to_new = max(int(cur.rowcount or 0), 0)

    cur.execute("UPDATE libros_escaneo SET updated_at = NOW() WHERE id = %s", (book_id,))
    return {
        "instances_old_to_temp": inst_old_to_temp,
        "instances_new_to_old": inst_new_to_old,
        "instances_temp_to_new": inst_temp_to_new,
        "problems_old_to_temp": problems_old_to_temp,
        "problems_new_to_old": problems_new_to_old,
        "problems_temp_to_new": problems_temp_to_new,
        "pending_old_to_temp": pending_old_to_temp,
        "pending_new_to_old": pending_new_to_old,
        "pending_temp_to_new": pending_temp_to_new,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Intercambia problemas_propuestos/problemas_resueltos para libros IMPECUS Meza Barcena.")
    parser.add_argument("--profile", default="local_mirror")
    parser.add_argument("--db", default="")
    parser.add_argument("--book-id", action="append", type=int, default=[])
    parser.add_argument("--apply", action="store_true", help="Ejecuta la migracion. Sin esto solo genera respaldo y plan.")
    args = parser.parse_args()

    stamp = _utc_stamp()
    dbm = DatabaseManager.from_profile(args.profile, db_name=args.db or None)
    db_name = args.db or dbm.db_name
    conn = dbm.get_connection(db_name)
    try:
        cur = conn.cursor()
        instance_col = _instance_column(cur)
        problem_instance_col = _problem_instance_column(cur) if _table_exists(cur, "problemas") else "codigo_instancia"
        books = _fetch_target_books(cur, args.book_id or None)
        backup_path = Path(".cache") / "instance_swap_backups" / f"{stamp}_impecus_meza_swap.json"
        snapshot = _backup_snapshot(cur, books, instance_col, problem_instance_col, backup_path)
        print(f"Backup: {backup_path.resolve()}")
        print(f"Libros objetivo: {len(books)}")
        for item in snapshot["books"]:
            book = item["book"]
            print(f"- {book['id']} {book['titulo']} | problemas={item['problem_counts']} | pending={item['pending_counts']}")

        if not args.apply:
            print("Modo dry-run. Vuelve a ejecutar con --apply para aplicar el intercambio.")
            return 0

        fs_results: list[dict[str, Any]] = []
        for book in books:
            paths_old = _paths_for_workspace(str(book.get("workspace_dir") or ""), OLD_INSTANCE)
            paths_new = _paths_for_workspace(str(book.get("workspace_dir") or ""), NEW_INSTANCE)
            book_fs = {"book_id": int(book["id"]), "title": book.get("titulo"), "swaps": {}, "rewrites": {}}
            for key in ("instance_root", "session_path", "solutions_dir"):
                book_fs["swaps"][key] = _swap_path(paths_old[key], paths_new[key], apply=True, stamp=stamp)
            # After the swap, the old proposed data lives under NEW_INSTANCE and vice versa.
            replacements_new = []
            replacements_old = []
            for key in ("instance_root", "session_path", "solutions_dir"):
                replacements_new.extend(_replacement_variants(paths_old[key], paths_new[key]))
                replacements_old.extend(_replacement_variants(paths_new[key], paths_old[key]))
            replacements_new.append((OLD_INSTANCE, NEW_INSTANCE))
            replacements_old.append((NEW_INSTANCE, OLD_INSTANCE))
            book_fs["rewrites"][NEW_INSTANCE] = _rewrite_text_tree(paths_new["instance_root"], replacements_new, apply=True)
            book_fs["rewrites"][OLD_INSTANCE] = _rewrite_text_tree(paths_old["instance_root"], replacements_old, apply=True)
            fs_results.append(book_fs)

        db_results: list[dict[str, Any]] = []
        try:
            for book in books:
                db_results.append({"book_id": int(book["id"]), **_swap_db_rows(cur, book, instance_col, problem_instance_col, stamp)})
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        result_path = Path(".cache") / "instance_swap_backups" / f"{stamp}_impecus_meza_swap_result.json"
        result_path.write_text(
            json.dumps(
                {
                    "schema_version": "impecus_meza_instance_swap_result_v1",
                    "backup_path": str(backup_path.resolve()),
                    "filesystem": fs_results,
                    "database": db_results,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Resultado: {result_path.resolve()}")
        for result in db_results:
            print("DB", result)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
