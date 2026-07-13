from __future__ import annotations

import argparse
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from html import escape
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _lname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _math_text(elem: ET.Element | None) -> str:
    if elem is None:
        return ""
    name = _lname(elem.tag)
    if name == "t":
        return elem.text or ""
    if name == "f":
        numerator = _child_text(elem, "num")
        denominator = _child_text(elem, "den")
        return f"{numerator}/{denominator}" if denominator else numerator
    if name == "sSup":
        return f"{_child_text(elem, 'e')}^{_child_text(elem, 'sup')}"
    if name == "sSub":
        return f"{_child_text(elem, 'e')}_{_child_text(elem, 'sub')}"
    if name == "sSubSup":
        return f"{_child_text(elem, 'e')}_{_child_text(elem, 'sub')}^{_child_text(elem, 'sup')}"
    if name == "rad":
        return f"sqrt({_child_text(elem, 'e')})"
    if name == "bar":
        return _child_text(elem, "e")
    return "".join(_math_text(child) for child in list(elem))


def _child_text(elem: ET.Element, child_name: str) -> str:
    child = elem.find(f"{{{MATH_NS}}}{child_name}")
    return _math_text(child)


def _omml_to_run(match: re.Match[str]) -> str:
    fragment = match.group(0)
    try:
        root = ET.fromstring(f'<root xmlns:m="{MATH_NS}" xmlns:w="{WORD_NS}">{fragment}</root>')
        text = "".join(_math_text(child) for child in list(root))
    except Exception:
        text = "".join(re.findall(r"<m:t(?:\s[^>]*)?>(.*?)</m:t>", fragment, flags=re.S))
    text = re.sub(r"\s+", " ", text).strip()
    return f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def convert_docx(input_docx: Path, output_docx: Path) -> dict[str, int]:
    with ZipFile(input_docx, "r") as zin:
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as handle:
            tmp_path = Path(handle.name)
        replaced = 0
        with ZipFile(tmp_path, "w", ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename == "word/document.xml":
                    xml = data.decode("utf-8", errors="replace")
                    xml, replaced = re.subn(r"<m:oMath[\s\S]*?</m:oMath>", _omml_to_run, xml)
                    data = xml.encode("utf-8")
                zout.writestr(info, data)
    output_docx.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(tmp_path), str(output_docx))
    return {"replaced": int(replaced), "bytes": int(output_docx.stat().st_size)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert Word OMML math to visible plain text runs.")
    parser.add_argument("input_docx")
    parser.add_argument("output_docx")
    args = parser.parse_args()
    result = convert_docx(Path(args.input_docx), Path(args.output_docx))
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
