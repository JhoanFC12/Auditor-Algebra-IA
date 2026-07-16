from __future__ import annotations

import json
import sys
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont


root = Path(sys.argv[1])
out_root = Path(sys.argv[2])
batch = json.loads((root / "batches" / "batch-01.json").read_text(encoding="utf-8"))
font = ImageFont.load_default(size=22)

for entry in batch["assignments"]:
    assignment = json.loads(Path(entry["assignment_path"]).read_text(encoding="utf-8"))
    assignment_id = assignment["assignment_id"]
    target = out_root / assignment_id
    target.mkdir(parents=True, exist_ok=True)
    document = fitz.open(assignment["source_document"]["pdf_path"])
    pages = list(assignment["approved_pages"])
    rendered: list[tuple[int, Image.Image]] = []
    for page_number in pages:
        page = document[page_number - 1]
        pixmap = page.get_pixmap(matrix=fitz.Matrix(0.8, 0.8), alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        image.thumbnail((480, 680), Image.Resampling.LANCZOS)
        rendered.append((page_number, image.copy()))

    for sheet_number, start in enumerate(range(0, len(rendered), 16), start=1):
        subset = rendered[start : start + 16]
        sheet = Image.new("RGB", (4 * 500, 4 * 720), "#d5d9df")
        draw = ImageDraw.Draw(sheet)
        for index, (page_number, image) in enumerate(subset):
            row, column = divmod(index, 4)
            x = column * 500 + 10
            y = row * 720 + 34
            sheet.paste(image, (x, y))
            roles = []
            if page_number in assignment["problem_pages"]:
                roles.append("P")
            if page_number in assignment["solution_pages"]:
                roles.append("S")
            draw.text(
                (x, row * 720 + 5),
                f"PDF {page_number} [{'|'.join(roles)}]",
                fill="black",
                font=font,
            )
        first_page = subset[0][0]
        last_page = subset[-1][0]
        sheet.save(
            target / f"contact_{sheet_number:02d}_p{first_page:04d}-p{last_page:04d}.jpg",
            quality=90,
            optimize=True,
        )

    sample_indexes = sorted({0, len(pages) // 2, len(pages) - 1})
    for index in sample_indexes:
        page_number = pages[index]
        page = document[page_number - 1]
        pixmap = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
        pixmap.save(target / f"sample_p{page_number:04d}.png")
    document.close()

print(out_root)
