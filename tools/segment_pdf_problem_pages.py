from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import fitz

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from modulos.modulo0_transcriptor.segmentador_v2 import SegmentadorProblemasV2


def safe_name(value: str, fallback: str = "documento") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._")
    return cleaned or fallback


def render_pdf_pages(pdf_path: Path, pages_dir: Path, *, dpi: int) -> list[Path]:
    pages_dir.mkdir(parents=True, exist_ok=True)
    zoom = float(dpi) / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    rendered: list[Path] = []
    with fitz.open(pdf_path) as document:
        digits = max(4, len(str(max(1, document.page_count))))
        for index, page in enumerate(document, start=1):
            image_path = pages_dir / f"pagina_{index:0{digits}d}.png"
            if not image_path.exists():
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                pixmap.save(str(image_path))
            rendered.append(image_path)
    return rendered


def build_manifest(
    *,
    pdf_path: Path,
    out_dir: Path,
    dpi: int,
    page_paths: list[Path],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    crops_total = sum(int(row.get("segments_total", 0) or 0) for row in rows)
    return {
        "schema_version": "pdf_problem_segmentation_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "pdf_path": str(pdf_path),
        "output_dir": str(out_dir),
        "dpi": int(dpi),
        "pages_total": len(page_paths),
        "pages_with_segments": sum(1 for row in rows if int(row.get("segments_total", 0) or 0) > 0),
        "problem_crops_total": crops_total,
        "pages_dir": "pages_png",
        "segments_dir": "problem_crops",
        "pages": rows,
        "notes": [
            "Cada pagina del PDF se rasteriza como PNG de alta resolucion.",
            "Cada box corresponde a un problema completo detectado por YOLO.",
            "Los recortes quedan ordenados de arriba hacia abajo y de izquierda a derecha.",
            "Los PNG y boxes tambien se reflejan en la golden base incremental del segmentador.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convierte un PDF a PNG de alta resolucion y recorta problemas completos con Segmentador V2."
    )
    parser.add_argument("pdf_path", help="Ruta del PDF de entrada.")
    parser.add_argument("--out-root", default=".cache/transcriptor_runs/pdf_problem_ingestion")
    parser.add_argument("--dpi", type=int, default=300, help="Resolucion para rasterizar paginas. Default: 300.")
    parser.add_argument("--force", action="store_true", help="Regenera paginas y decisiones de segmentacion existentes.")
    args = parser.parse_args()

    pdf_path = Path(args.pdf_path).expanduser().resolve()
    if not pdf_path.exists() or pdf_path.suffix.lower() != ".pdf":
        raise FileNotFoundError(f"PDF no encontrado: {pdf_path}")
    dpi = max(144, min(600, int(args.dpi)))
    out_root = Path(args.out_root).expanduser().resolve()
    out_dir = out_root / safe_name(pdf_path.stem)
    pages_dir = out_dir / "pages_png"
    crops_dir = out_dir / "problem_crops"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.force:
        for manifest_path in crops_dir.glob("*/segments_manifest.json"):
            manifest_path.unlink(missing_ok=True)

    page_paths = render_pdf_pages(pdf_path, pages_dir, dpi=dpi)
    segmentador = SegmentadorProblemasV2(crops_dir)
    rows: list[dict[str, Any]] = []
    for page_number, page_path in enumerate(page_paths, start=1):
        segments = segmentador.segmentar(page_path)
        rows.append(
            {
                "page_number": int(page_number),
                "page_image": str(page_path.relative_to(out_dir)).replace("\\", "/"),
                "detector_source": segmentador.last_detector_source,
                "detector": dict(segmentador.last_detector_payload or {}),
                "segments_total": len(segments),
                "segments": [
                    {
                        "idx": int(segment.idx),
                        "bbox_px": [int(value) for value in segment.bbox],
                        "crop_path": str(segment.image_path.relative_to(out_dir)).replace("\\", "/"),
                    }
                    for segment in segments
                ],
            }
        )
        print(f"[PAGE] {page_number}/{len(page_paths)} boxes={len(segments)} source={segmentador.last_detector_source}")

    manifest = build_manifest(
        pdf_path=pdf_path,
        out_dir=out_dir,
        dpi=dpi,
        page_paths=page_paths,
        rows=rows,
    )
    manifest_path = out_dir / "pdf_problem_segments_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] Paginas PNG: {len(page_paths)}")
    print(f"[OK] Recortes de problemas: {manifest['problem_crops_total']}")
    print(f"[OK] Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
