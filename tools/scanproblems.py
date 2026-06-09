from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modulos.modulo0_transcriptor.scan_pipeline.pipeline import ScanPipeline
from utils.env_validation import load_env_file_if_present, validate_scan_provider_env


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="scanproblems: escanea imagenes (png/jpg) y genera LaTeX de formato fijo."
    )
    parser.add_argument("--input", required=True, help="Carpeta de imagenes.")
    parser.add_argument("--start", type=int, default=1, help="Numeracion inicial fallback.")
    parser.add_argument("--curso", required=True, help="Valor para [[curso=...]].")
    parser.add_argument("--tema", required=True, help="Valor para [[tema=...]].")
    parser.add_argument("--out", required=True, help="Archivo .tex de salida.")
    parser.add_argument("--provider", choices=("hf", "openai", "ocr"), default="hf")
    parser.add_argument("--model", default="", help="Modelo vision.")
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--parse-max-retries", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--max-tokens", type=int, default=3200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--strict-json", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--debug-dir", default="", help="Carpeta para dumps de debug.")
    parser.add_argument("--fail-on-needs-review", action="store_true")
    return parser


def run_scan(args: argparse.Namespace) -> int:
    load_env_file_if_present()
    validate_scan_provider_env(args.provider)

    input_dir = Path(args.input).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    strict_json = bool(args.strict_json)
    if args.provider == "ocr":
        strict_json = False

    pipeline = ScanPipeline(
        provider=args.provider,
        model=args.model,
        max_retries=args.max_retries,
        parse_max_retries=args.parse_max_retries,
        timeout_s=args.timeout,
        debug_dir=args.debug_dir,
        temperature=args.temperature,
        top_p=args.top_p,
        max_tokens=args.max_tokens,
        seed=args.seed,
        strict_json=strict_json,
    )
    result = pipeline.run_on_folder(
        input_dir=input_dir,
        start_n=max(1, int(args.start)),
        curso=str(args.curso),
        tema=str(args.tema),
    )

    out_path.write_text(result.rendered_document, encoding="utf-8")
    report_path = out_path.with_suffix(out_path.suffix + ".report.json")
    report_path.write_text(json.dumps(result.to_report_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        "[scanproblems] items={items} needs_review={needs} skipped_keys={skipped} parse_failed={parse_failed}".format(
            items=len(result.items),
            needs=result.needs_review_count,
            skipped=len(result.skipped_images),
            parse_failed=result.json_parse_failed_count,
        )
    )
    print(f"[scanproblems] tex={out_path}")
    print(f"[scanproblems] report={report_path}")

    if args.fail_on_needs_review and result.needs_review_count > 0:
        print("[scanproblems] FAIL: hay items con needs_review=true")
        return 2
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run_scan(args)
    except Exception as exc:
        print(f"[scanproblems] ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())