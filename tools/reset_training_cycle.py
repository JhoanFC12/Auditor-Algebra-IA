from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from modulos.instance_factory.training_registry import start_new_training_cycle


def main() -> int:
    parser = argparse.ArgumentParser(description="Inicia un nuevo ciclo de entrenamiento sin borrar historico.")
    parser.add_argument("--datasets-root", default="", help="Raiz opcional de datasets. Por defecto usa TRAINING_DATASETS_ROOT o .cache.")
    parser.add_argument("--reason", default="Nuevo ciclo de entrenamiento iniciado por CLI.")
    parser.add_argument("--metadata", default="{}", help="JSON opcional con metadata del ciclo.")
    args = parser.parse_args()
    metadata = json.loads(args.metadata or "{}")
    root = Path(args.datasets_root) if args.datasets_root else None
    status = start_new_training_cycle(root=root, reason=args.reason, metadata=metadata)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
