from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


sys.path.insert(0, str(_repo_root()))

from modulos.modulo0_transcriptor.session_schema import enrich_session_payload_with_structure  # noqa: E402


def _looks_like_session(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    if isinstance(payload.get("state_v3"), dict):
        return True
    if isinstance(payload.get("files"), list):
        return True
    if isinstance(payload.get("items"), list):
        return True
    return False


def migrate_sessions(root: Path) -> tuple[int, int, int]:
    total = 0
    updated = 0
    skipped = 0
    for path in sorted(root.rglob("*.json")):
        if path.parent.name.strip().lower() != "sessions":
            continue
        total += 1
        try:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            skipped += 1
            print(f"SKIP unreadable: {path}")
            continue
        if not _looks_like_session(payload):
            skipped += 1
            print(f"SKIP non-session: {path}")
            continue

        original = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        enriched = enrich_session_payload_with_structure(payload, session_path=path)
        rewritten = json.dumps(enriched, ensure_ascii=False, indent=2)
        if json.dumps(enriched, ensure_ascii=False, sort_keys=True) != original:
            path.write_text(rewritten, encoding="utf-8")
            updated += 1
            print(f"UPDATED: {path}")
        else:
            print(f"OK: {path}")
    return total, updated, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description="Normaliza sesiones del Transcriptor al formato estructurado.")
    parser.add_argument("root", type=Path, help="Ruta raíz donde buscar carpetas sessions/*.json")
    args = parser.parse_args()
    total, updated, skipped = migrate_sessions(args.root.expanduser())
    print(f"TOTAL={total} UPDATED={updated} SKIPPED={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
