from __future__ import annotations

import argparse
import html
import re
from urllib.parse import quote
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MapRow:
    index: int
    problem_id: int
    original: int
    archivo_origen: str
    raw: str

    @property
    def is_resuelto(self) -> bool:
        return False


LINE_RE = re.compile(
    r"^\s*(?P<index>\d+)\.\s*id=(?P<id>\d+)\s*\|\s*original=(?P<original>\d+)\s*\|\s*archivo_origen=(?P<src>.+?)\s*$"
)


def parse_map(path: Path) -> list[MapRow]:
    rows: list[MapRow] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        match = LINE_RE.match(line)
        if not match:
            continue
        rows.append(
            MapRow(
                index=int(match.group("index")),
                problem_id=int(match.group("id")),
                original=int(match.group("original")),
                archivo_origen=match.group("src").strip(),
                raw=line,
            )
        )
    return rows


def _stem_without_estado(filename: str) -> str:
    """
    Ejemplos:
      S10-Prisma_y_Piramide_Resueltos.tex -> S10-Prisma_y_Piramide
      S11-Cilindro_y_Cono_Propuestos.tex -> S11-Cilindro_y_Cono
    """
    name = filename.strip()
    name = re.sub(r"_(Resueltos|Propuestos)\.tex$", "", name, flags=re.IGNORECASE)
    return re.sub(r"\.tex$", "", name, flags=re.IGNORECASE)


def _href_for_existing(path: Path | None, fallback_relative: str) -> str:
    if path is not None and path.exists():
        return path.resolve().as_uri()
    return quote(fallback_relative)


def _find_file(docs_dir: Path, relative_name: str, recursive: bool) -> Path | None:
    candidate = (docs_dir / relative_name).resolve()
    if candidate.exists():
        return candidate
    if not recursive:
        return None
    # Búsqueda recursiva: primero por nombre exacto.
    for found in docs_dir.rglob(Path(relative_name).name):
        if found.is_file() and found.name.lower() == Path(relative_name).name.lower():
            return found
    return None


def build_html(
    rows: list[MapRow],
    resueltos: set[str],
    title: str,
    tex_href_by_row_index: dict[int, str],
    pdf_href_by_row_index: dict[int, str],
) -> str:
    total = len(rows)
    resueltos_count = sum(1 for r in rows if r.archivo_origen in resueltos)
    css = """
    :root { color-scheme: light dark; }
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }
    h1 { margin: 0 0 8px 0; font-size: 20px; }
    .meta { margin: 0 0 16px 0; opacity: 0.8; }
    table { border-collapse: collapse; width: 100%; }
    th, td { padding: 8px 10px; border-bottom: 1px solid rgba(127,127,127,0.35); text-align: left; }
    th { position: sticky; top: 0; background: Canvas; }
    tr.resuelto td { background: rgba(72, 187, 120, 0.18); }
    .pill { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; border: 1px solid rgba(127,127,127,0.35); }
    .pill.ok { background: rgba(72, 187, 120, 0.18); }
    .pill.no { background: rgba(237, 137, 54, 0.18); }
    .src { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }
    """
    head = f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>{css}</style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <p class="meta">Total: <b>{total}</b> · Resueltos: <b>{resueltos_count}</b> · No resueltos: <b>{total - resueltos_count}</b></p>
  <table>
    <thead>
      <tr>
        <th>#</th>
        <th>id</th>
        <th>original</th>
        <th>estado</th>
        <th>archivo_origen</th>
      </tr>
    </thead>
    <tbody>
"""
    body_lines: list[str] = []
    for r in rows:
        ok = r.archivo_origen in resueltos
        estado = '<span class="pill ok">RESUELTO</span>' if ok else '<span class="pill no">NO</span>'
        tr_class = "resuelto" if ok else ""
        archivo_name = r.archivo_origen
        tex_href = tex_href_by_row_index.get(r.index) or quote(archivo_name)
        pdf_href = pdf_href_by_row_index.get(r.index) or quote(_stem_without_estado(archivo_name) + ".pdf")
        label = html.escape(archivo_name)
        body_lines.append(
            "      <tr class=\"%s\"><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class=\"src\"><a href=\"%s\" target=\"_blank\" rel=\"noopener\">%s</a> · <a href=\"%s\" target=\"_blank\" rel=\"noopener\">PDF</a></td></tr>"
            % (
                tr_class,
                r.index,
                r.problem_id,
                r.original,
                estado,
                tex_href,
                label,
                pdf_href,
            )
        )
    tail = """
    </tbody>
  </table>
</body>
</html>
"""
    return head + "\n".join(body_lines) + tail


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Genera un HTML desde un *_map.txt resaltando los problemas cuyo archivo_origen es uno de los 'resueltos'."
    )
    parser.add_argument("map_txt", type=Path, help="Ruta al archivo *_map.txt")
    parser.add_argument(
        "--resueltos",
        nargs="+",
        default=["S10-Prisma_y_Piramide_Resueltos.tex", "S11-Cilindro_y_Cono_Resueltos.tex"],
        help="Lista de nombres de archivo_origen a considerar como resueltos.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Ruta de salida .html (por defecto: mismo nombre que el .txt).",
    )
    parser.add_argument(
        "--docs-dir",
        type=Path,
        default=None,
        help="Directorio donde buscar los archivos 'archivo_origen' para linkearlos (por defecto: carpeta del map).",
    )
    parser.add_argument(
        "--pdf-dir",
        type=Path,
        default=None,
        help="Directorio donde buscar los PDF (por defecto: --docs-dir o carpeta del map).",
    )
    parser.add_argument(
        "--search-recursive",
        action="store_true",
        help="Si está presente, busca recursivamente dentro de --docs-dir/--pdf-dir cuando no encuentra el archivo directo.",
    )
    args = parser.parse_args()

    map_path: Path = args.map_txt
    out_path: Path = args.out or map_path.with_suffix(".html")

    rows = parse_map(map_path)
    resueltos_set = set(args.resueltos)
    title = f"Mapa de problemas: {map_path.name}"

    docs_dir: Path = (args.docs_dir or map_path.parent).resolve()
    pdf_dir: Path = (args.pdf_dir or docs_dir).resolve()
    recursive: bool = bool(args.search_recursive)

    tex_href_by_row_index: dict[int, str] = {}
    pdf_href_by_row_index: dict[int, str] = {}
    for r in rows:
        tex_path = _find_file(docs_dir, r.archivo_origen, recursive)
        tex_href_by_row_index[r.index] = _href_for_existing(tex_path, r.archivo_origen)

        pdf_name = _stem_without_estado(r.archivo_origen) + ".pdf"
        pdf_path = _find_file(pdf_dir, pdf_name, recursive)
        pdf_href_by_row_index[r.index] = _href_for_existing(pdf_path, pdf_name)

    out_path.write_text(
        build_html(rows, resueltos_set, title, tex_href_by_row_index, pdf_href_by_row_index),
        encoding="utf-8",
    )
    print(f"OK: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
