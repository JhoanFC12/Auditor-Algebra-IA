from __future__ import annotations


def parse_page_selection(raw: str, total_pages: int) -> list[int]:
    pages: set[int] = set()
    for token in str(raw or "").split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            left, right = token.split("-", 1)
            start, end = int(left), int(right)
            if end < start:
                start, end = end, start
            pages.update(range(start, end + 1))
        else:
            pages.add(int(token))
    invalid = [page for page in sorted(pages) if page < 1 or page > int(total_pages)]
    if invalid:
        raise ValueError(f"Paginas fuera del PDF: {invalid}")
    return sorted(pages)
