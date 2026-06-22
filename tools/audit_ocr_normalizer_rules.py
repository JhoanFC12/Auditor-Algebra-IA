from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modulos.instance_factory.rule_auditor import write_audit_report


DEFAULT_OUT_ROOT = ROOT / ".cache" / "transcriptor_runs" / "audits"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"Linea JSONL no es objeto en {path}:{line_no}")
        rows.append(payload)
    return rows


def _iter_record_paths(staging_roots: Iterable[Path]) -> list[Path]:
    paths: list[Path] = []
    for raw_root in staging_roots:
        root = Path(raw_root).expanduser().resolve()
        if (root / "records").exists():
            paths.extend(sorted((root / "records").glob("*.json"), key=lambda item: item.name.lower()))
            continue
        for records_dir in sorted(root.glob("*/records"), key=lambda item: str(item).lower()):
            paths.extend(sorted(records_dir.glob("*.json"), key=lambda item: item.name.lower()))
    return paths


def collect_rows(*, input_paths: Iterable[Path], staging_roots: Iterable[Path], max_records: int = 0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_path in input_paths:
        path = Path(raw_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"No existe input: {path}")
        if path.suffix.lower() == ".jsonl":
            rows.extend(_read_jsonl(path))
            continue
        payload = _read_json(path)
        if payload is not None:
            rows.append(payload)
    for record_path in _iter_record_paths(staging_roots):
        payload = _read_json(record_path)
        if payload is not None:
            payload.setdefault("traceability", {})
            if isinstance(payload["traceability"], dict):
                payload["traceability"].setdefault("source_record_path", str(record_path))
            rows.append(payload)
    if max_records > 0:
        return rows[: int(max_records)]
    return rows


def _default_out_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DEFAULT_OUT_ROOT / f"ocr_normalizer_rules_{stamp}"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(
        description="Audita reglas de OCR crudo y normalizador sin escribir en staging ni problemas."
    )
    parser.add_argument("--input", action="append", default=[], help="Archivo .json o .jsonl. Repetible.")
    parser.add_argument("--staging-root", action="append", default=[], help="Staging root directo o padre con */records.")
    parser.add_argument("--out-dir", default="", help="Carpeta de salida. Por defecto usa .cache/transcriptor_runs/audits.")
    parser.add_argument("--mode", default="auto", choices=["auto", "ocr", "normalizer", "both"])
    parser.add_argument("--max-records", type=int, default=0)
    args = parser.parse_args()

    rows = collect_rows(
        input_paths=[Path(item) for item in args.input],
        staging_roots=[Path(item) for item in args.staging_root],
        max_records=max(0, int(args.max_records or 0)),
    )
    if not rows:
        raise SystemExit("No se encontraron registros para auditar.")
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else _default_out_dir()
    summary = write_audit_report(rows, out_dir=out_dir, mode=args.mode)
    summary["out_dir"] = str(out_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
