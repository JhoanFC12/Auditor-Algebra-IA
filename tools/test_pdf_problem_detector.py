from __future__ import annotations

import argparse
import html
import json
import re
import webbrowser
from datetime import datetime
from pathlib import Path

import fitz
from huggingface_hub import hf_hub_download
from PIL import Image
from ultralytics import YOLO


MODEL_REPO_ID = "Jhoan12/pdf-problem-detector-yolov8n-v4"


def safe_name(value: str, fallback: str = "documento") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._")
    return cleaned or fallback


def parse_pages(value: str, total: int) -> list[int]:
    if not value.strip():
        return list(range(1, total + 1))
    selected: set[int] = set()
    for part in value.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start, end = int(start_text), int(end_text)
            selected.update(range(min(start, end), max(start, end) + 1))
        else:
            selected.add(int(token))
    pages = sorted(page for page in selected if 1 <= page <= total)
    if not pages:
        raise ValueError("El rango no contiene paginas validas.")
    return pages


def render_pages(pdf_path: Path, pages_dir: Path, pages: list[int], dpi: int) -> list[tuple[int, Path]]:
    pages_dir.mkdir(parents=True, exist_ok=True)
    matrix = fitz.Matrix(float(dpi) / 72.0, float(dpi) / 72.0)
    rendered: list[tuple[int, Path]] = []
    with fitz.open(pdf_path) as document:
        digits = max(4, len(str(document.page_count)))
        for page_number in pages:
            image_path = pages_dir / f"pagina_{page_number:0{digits}d}.png"
            page = document.load_page(page_number - 1)
            page.get_pixmap(matrix=matrix, alpha=False).save(str(image_path))
            rendered.append((page_number, image_path))
    return rendered


def sort_boxes(boxes: list[dict[str, object]]) -> list[dict[str, object]]:
    if len(boxes) < 4:
        return sorted(boxes, key=lambda row: (int(row["bbox_px"][1]), int(row["bbox_px"][0])))
    centers = sorted(((int(row["bbox_px"][0]) + int(row["bbox_px"][2])) / 2.0, row) for row in boxes)
    gaps = [(centers[index + 1][0] - centers[index][0], index) for index in range(len(centers) - 1)]
    largest_gap, split_index = max(gaps, default=(0.0, 0))
    page_span = max(int(row["bbox_px"][2]) for row in boxes) - min(int(row["bbox_px"][0]) for row in boxes)
    left = [row for _, row in centers[: split_index + 1]]
    right = [row for _, row in centers[split_index + 1 :]]
    if largest_gap >= max(80.0, page_span * 0.12) and len(left) >= 2 and len(right) >= 2:
        return sorted(left, key=lambda row: (int(row["bbox_px"][1]), int(row["bbox_px"][0]))) + sorted(
            right, key=lambda row: (int(row["bbox_px"][1]), int(row["bbox_px"][0]))
        )
    return sorted(boxes, key=lambda row: (int(row["bbox_px"][1]), int(row["bbox_px"][0])))


def create_report(out_dir: Path, rows: list[dict[str, object]], manifest_path: Path) -> Path:
    cards: list[str] = []
    for row in rows:
        overlay = html.escape(str(row["overlay_path"]))
        crops = "".join(
            f'<img src="{html.escape(str(segment["crop_path"]))}" alt="Recorte">'
            for segment in row["segments"]
        )
        cards.append(
            f"""
            <section class="card">
              <h2>Pagina {row["page_number"]} | {row["segments_total"]} problemas</h2>
              <img class="overlay" src="{overlay}" alt="Pagina con boxes">
              <div class="crops">{crops}</div>
            </section>
            """
        )
    report = out_dir / "reporte_visual.html"
    report.write_text(
        f"""<!doctype html>
<html lang="es">
<meta charset="utf-8">
<title>Prueba detector de problemas</title>
<style>
body {{ margin: 24px; background: #f3f0e8; color: #17222f; font-family: Georgia, serif; }}
h1 {{ margin-bottom: 4px; }}
.meta {{ margin-bottom: 24px; color: #506070; }}
.card {{ margin: 0 0 34px; padding: 18px; background: white; border: 1px solid #c8c0b2; }}
.overlay {{ display: block; max-width: 100%; border: 1px solid #8d877d; }}
.crops {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; margin-top: 16px; }}
.crops img {{ width: 100%; border: 1px solid #d0c8bc; }}
</style>
<h1>Detector de problemas matematicos completos</h1>
<div class="meta">Reporte local | manifest: {html.escape(str(manifest_path.name))}</div>
{''.join(cards)}
</html>""",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Prueba el detector entrenado sobre paginas seleccionadas de un PDF nuevo.")
    parser.add_argument("pdf_path", help="Ruta del PDF nuevo.")
    parser.add_argument("--pages", default="", help="Paginas: 1-3,7. Vacio procesa todas.")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--imgsz", type=int, default=1280)
    parser.add_argument("--out-root", default=".cache/transcriptor_runs/pdf_problem_detector_tests")
    parser.add_argument("--model-repo-id", default=MODEL_REPO_ID)
    parser.add_argument("--open-report", action="store_true")
    args = parser.parse_args()

    pdf_path = Path(args.pdf_path).expanduser().resolve()
    if not pdf_path.exists() or pdf_path.suffix.lower() != ".pdf":
        raise FileNotFoundError(f"PDF no encontrado: {pdf_path}")
    out_dir = Path(args.out_root).expanduser().resolve() / f"{safe_name(pdf_path.stem)}_{datetime.now():%Y%m%d_%H%M%S}"
    pages_dir, overlays_dir, crops_dir = out_dir / "pages_png", out_dir / "overlays", out_dir / "problem_crops"
    overlays_dir.mkdir(parents=True, exist_ok=True)
    crops_dir.mkdir(parents=True, exist_ok=True)

    with fitz.open(pdf_path) as document:
        pages = parse_pages(args.pages, document.page_count)
    rendered = render_pages(pdf_path, pages_dir, pages, max(144, min(600, int(args.dpi))))
    weights = hf_hub_download(args.model_repo_id, "weights/best.pt")
    model = YOLO(weights)
    rows: list[dict[str, object]] = []
    for page_number, image_path in rendered:
        result = model.predict(source=str(image_path), imgsz=int(args.imgsz), conf=float(args.confidence), verbose=False)[0]
        raw_boxes: list[dict[str, object]] = []
        for xyxy, confidence in zip(result.boxes.xyxy.tolist(), result.boxes.conf.tolist()):
            x1, y1, x2, y2 = [int(round(value)) for value in xyxy]
            raw_boxes.append({"bbox_px": [x1, y1, x2, y2], "confidence": round(float(confidence), 6)})
        boxes = sort_boxes(raw_boxes)
        source_image = Image.open(image_path).convert("RGB")
        segments: list[dict[str, object]] = []
        for index, box in enumerate(boxes, start=1):
            crop_path = crops_dir / f"pagina_{page_number:04d}_problema_{index:02d}.png"
            source_image.crop(tuple(box["bbox_px"])).save(crop_path)
            segments.append({**box, "crop_path": crop_path.relative_to(out_dir).as_posix()})
        overlay_path = overlays_dir / f"pagina_{page_number:04d}.jpg"
        Image.fromarray(result.plot()).save(overlay_path, quality=92)
        rows.append(
            {
                "page_number": page_number,
                "page_image": image_path.relative_to(out_dir).as_posix(),
                "overlay_path": overlay_path.relative_to(out_dir).as_posix(),
                "segments_total": len(segments),
                "segments": segments,
            }
        )
        print(f"[PAGE] {page_number} boxes={len(segments)}")

    manifest_path = out_dir / "pdf_problem_detector_test_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "pdf_problem_detector_test_v1",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "pdf_path": str(pdf_path),
                "model_repo_id": args.model_repo_id,
                "pages": rows,
                "pages_total": len(rows),
                "problem_crops_total": sum(int(row["segments_total"]) for row in rows),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    report = create_report(out_dir, rows, manifest_path)
    print(f"[OK] Paginas evaluadas: {len(rows)}")
    print(f"[OK] Recortes detectados: {sum(int(row['segments_total']) for row in rows)}")
    print(f"[OK] Reporte visual: {report}")
    if args.open_report:
        webbrowser.open(report.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
