from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT
    / ".cache"
    / "book_catalog"
    / "repository_inventory"
    / "curated_math_books"
    / "06_prioridad_opcion_multiple.csv"
)
OUTPUT = ROOT / "output" / "pdf" / "catalogo_revision_libros_preuniversitarios.pdf"

COURSE_ORDER = [
    "Aritmetica",
    "Algebra",
    "Geometria",
    "Trigonometria",
    "Razonamiento matematico",
    "Geometria analitica",
    "Geometria del espacio",
    "Fisica",
    "Quimica",
]

DISPLAY_COURSE = {
    "Aritmetica": "Aritmetica",
    "Algebra": "Algebra",
    "Geometria": "Geometria",
    "Trigonometria": "Trigonometria",
    "Razonamiento matematico": "Razonamiento Matematico (R.M.)",
    "Geometria analitica": "Geometria Analitica",
    "Geometria del espacio": "Geometria del Espacio",
    "Fisica": "Fisica",
    "Quimica": "Quimica",
}

INK = colors.HexColor("#102235")
MUTED = colors.HexColor("#5F7184")
TEAL = colors.HexColor("#0E9488")
TEAL_DARK = colors.HexColor("#087267")
PALE = colors.HexColor("#EAF7F5")
LINE = colors.HexColor("#CEDAE5")
SURFACE = colors.HexColor("#F7FAFC")


def register_fonts() -> tuple[str, str]:
    regular = Path("C:/Windows/Fonts/arial.ttf")
    bold = Path("C:/Windows/Fonts/arialbd.ttf")
    if regular.exists() and bold.exists():
        pdfmetrics.registerFont(TTFont("CatalogRegular", str(regular)))
        pdfmetrics.registerFont(TTFont("CatalogBold", str(bold)))
        return "CatalogRegular", "CatalogBold"
    return "Helvetica", "Helvetica-Bold"


REGULAR, BOLD = register_fonts()


def load_books() -> list[dict[str, str]]:
    with SOURCE.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def page_decor(canvas, doc) -> None:
    canvas.saveState()
    width, height = A4
    canvas.setFillColor(INK)
    canvas.rect(0, height - 18 * mm, width, 18 * mm, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont(BOLD, 9)
    canvas.drawString(17 * mm, height - 11.5 * mm, "CATALOGO DE REVISION - LIBROS PREUNIVERSITARIOS")
    canvas.setStrokeColor(LINE)
    canvas.line(17 * mm, 15 * mm, width - 17 * mm, 15 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont(REGULAR, 8)
    canvas.drawString(17 * mm, 9.5 * mm, "Fuente prioritaria: canal de WhatsApp - enlaces de Google Drive")
    canvas.drawRightString(width - 17 * mm, 9.5 * mm, f"Pagina {doc.page}")
    canvas.restoreState()


def styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "Title",
            parent=base["Title"],
            fontName=BOLD,
            fontSize=24,
            leading=28,
            textColor=INK,
            alignment=TA_LEFT,
            spaceAfter=8,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle",
            parent=base["BodyText"],
            fontName=REGULAR,
            fontSize=11,
            leading=16,
            textColor=MUTED,
            spaceAfter=10,
        ),
        "course": ParagraphStyle(
            "Course",
            parent=base["Heading1"],
            fontName=BOLD,
            fontSize=16,
            leading=20,
            textColor=INK,
            spaceBefore=3,
            spaceAfter=6,
        ),
        "book": ParagraphStyle(
            "Book",
            parent=base["BodyText"],
            fontName=BOLD,
            fontSize=10.2,
            leading=13,
            textColor=INK,
        ),
        "meta": ParagraphStyle(
            "Meta",
            parent=base["BodyText"],
            fontName=REGULAR,
            fontSize=8.2,
            leading=11,
            textColor=MUTED,
        ),
        "button": ParagraphStyle(
            "Button",
            parent=base["BodyText"],
            fontName=BOLD,
            fontSize=9,
            textColor=colors.white,
            alignment=TA_CENTER,
        ),
        "small": ParagraphStyle(
            "Small",
            parent=base["BodyText"],
            fontName=REGULAR,
            fontSize=8.5,
            leading=12,
            textColor=INK,
        ),
    }


def book_card(index: int, row: dict[str, str], style: dict) -> KeepTogether:
    title = row.get("title", "Sin titulo")
    url = row.get("url", "")
    scope = "Libro mixto: revisar cursos" if row.get("course_scope") == "mixto" else "Curso unico propuesto"
    number = Paragraph(f"<b>{index:02d}</b>", style["book"])
    description = Paragraph(
        f"<b>{title}</b><br/><font color='#5F7184'>{scope}</font>",
        style["book"],
    )
    button = Paragraph(f"<link href='{url}' color='white'><b>ABRIR PDF</b></link>", style["button"])
    table = Table(
        [[number, description, button]],
        colWidths=[13 * mm, 127 * mm, 30 * mm],
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SURFACE),
                ("BACKGROUND", (2, 0), (2, 0), TEAL),
                ("BOX", (0, 0), (-1, -1), 0.7, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (0, 0), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    review = Table(
        [[Paragraph("Revision:  [ ] Confirmar curso    [ ] Reasignar: ____________________    [ ] Excluir", style["meta"]) ]],
        colWidths=[170 * mm],
    )
    review.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PALE),
                ("BOX", (0, 0), (-1, -1), 0.7, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return KeepTogether([table, review, Spacer(1, 4 * mm)])


def build() -> None:
    rows = load_books()
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row.get("course", "Pendiente")].append(row)
    for course_rows in grouped.values():
        course_rows.sort(key=lambda row: row.get("title", "").casefold())

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        leftMargin=17 * mm,
        rightMargin=17 * mm,
        topMargin=24 * mm,
        bottomMargin=20 * mm,
        title="Catalogo de revision de libros preuniversitarios",
        author="Auditor-IA",
        subject="Enlaces para confirmar cursos y contenido de opcion multiple",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="catalog", frames=[frame], onPage=page_decor)])
    style = styles()

    story = [
        Spacer(1, 6 * mm),
        Paragraph("Catalogo de libros para revision", style["title"]),
        Paragraph(
            "Abre cada PDF desde el boton correspondiente y confirma si pertenece al curso propuesto. "
            "La seleccion contiene materiales con senales de libro preuniversitario y opcion multiple; "
            "los libros mixtos estan identificados para revisarlos con especial cuidado.",
            style["subtitle"],
        ),
    ]

    summary_data = [["Curso", "Cantidad"]]
    for course in COURSE_ORDER:
        summary_data.append([DISPLAY_COURSE[course], str(len(grouped.get(course, [])))])
    summary = Table(summary_data, colWidths=[125 * mm, 35 * mm], repeatRows=1)
    summary.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), INK),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), BOLD),
                ("FONTNAME", (0, 1), (-1, -1), REGULAR),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.6, LINE),
                ("BACKGROUND", (0, 1), (-1, -1), SURFACE),
                ("ALIGN", (1, 0), (1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story += [summary, Spacer(1, 6 * mm), PageBreak()]

    global_index = 1
    for course_index, course in enumerate(COURSE_ORDER):
        course_rows = grouped.get(course, [])
        if not course_rows:
            continue
        story.append(Paragraph(f"{DISPLAY_COURSE[course]} - {len(course_rows)} libro(s)", style["course"]))
        story.append(Spacer(1, 2 * mm))
        for row in course_rows:
            story.append(book_card(global_index, row, style))
            global_index += 1
        if course_index != len(COURSE_ORDER) - 1:
            story.append(PageBreak())

    doc.build(story)
    print(OUTPUT)
    print(f"Libros incluidos: {len(rows)}")


if __name__ == "__main__":
    build()
