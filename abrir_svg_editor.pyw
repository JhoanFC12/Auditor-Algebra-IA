from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from modulos.modulo8_svg_editor.svg_editor_v2_copy import main  # noqa: E402


if __name__ == "__main__":
    svg_path = sys.argv[1] if len(sys.argv) > 1 else None
    main(svg_path)
