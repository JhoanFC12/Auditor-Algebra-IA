from __future__ import annotations

import argparse
from pathlib import Path

from .executor import ExperimentalSvgExecutor
from .inventory import build_inventory
from .planner import RuleBasedPlanner


def main() -> int:
    parser = argparse.ArgumentParser(description="Laboratorio IA para operaciones SVG.")
    parser.add_argument("--svg", required=True, help="Ruta del SVG de entrada.")
    parser.add_argument("--instruction", required=True, help="Instruccion en lenguaje natural.")
    parser.add_argument("--out", required=True, help="Ruta del SVG de salida.")
    parser.add_argument("--plan-out", help="Ruta opcional para guardar el plan JSON.")
    args = parser.parse_args()

    svg_path = Path(args.svg)
    svg_text = svg_path.read_text(encoding="utf-8")
    inventory = build_inventory(svg_text)
    plan = RuleBasedPlanner().plan(args.instruction, inventory)

    if args.plan_out:
        Path(args.plan_out).write_text(plan.to_json(), encoding="utf-8")

    result = ExperimentalSvgExecutor().execute(svg_text, plan)
    Path(args.out).write_text(result.svg_text, encoding="utf-8")

    print(f"Operaciones aplicadas: {len(result.applied)}")
    for issue in result.issues:
        print(f"{issue.level}: {issue.message}")
    return 0 if not any(issue.level == "error" for issue in result.issues) else 1


if __name__ == "__main__":
    raise SystemExit(main())
