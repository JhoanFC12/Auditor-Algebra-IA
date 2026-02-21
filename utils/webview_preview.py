from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Uso: python -m utils.webview_preview <url> [title] [width] [height] [x] [y] [on_top(0/1)]")
        return 2

    url = argv[1]
    title = argv[2] if len(argv) >= 3 else "Vista previa LaTeX"
    width = int(argv[3]) if len(argv) >= 4 else 860
    height = int(argv[4]) if len(argv) >= 5 else 900
    x = int(argv[5]) if len(argv) >= 6 and argv[5] not in {"", "None", "none"} else None
    y = int(argv[6]) if len(argv) >= 7 and argv[6] not in {"", "None", "none"} else None
    on_top = bool(int(argv[7])) if len(argv) >= 8 else False

    try:
        import webview  # type: ignore
    except Exception as exc:
        print(f"pywebview no disponible: {exc}")
        return 3

    webview.create_window(
        title,
        url,
        width=width,
        height=height,
        x=x,
        y=y,
        resizable=True,
        on_top=on_top,
    )
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
