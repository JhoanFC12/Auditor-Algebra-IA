"""Compatibilidad minima para el soporte MathText del editor SVG."""

from __future__ import annotations

_MPL_CONFIGURED = False


def configure_mathtext(*, fontset: str = "cm") -> None:
    global _MPL_CONFIGURED
    if _MPL_CONFIGURED:
        return
    import matplotlib as mpl

    mpl.rcParams["mathtext.fontset"] = fontset
    mpl.rcParams["font.family"] = "serif"
    _MPL_CONFIGURED = True


def require_matplotlib() -> None:
    try:
        import matplotlib  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("Soporte LaTeX requiere 'matplotlib' (MathText).") from exc
