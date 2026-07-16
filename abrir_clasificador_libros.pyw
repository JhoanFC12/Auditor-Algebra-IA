from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> None:
    subprocess.Popen(
        [sys.executable, "-m", "modulos.book_inventory_reviewer.server"],
        cwd=str(ROOT),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


if __name__ == "__main__":
    main()

