from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# OpenAI-inspired light palette
PALETTE = {
    "bg": "#f7f7f8",
    "surface": "#ffffff",
    "surface_alt": "#fbfcfd",
    "surface_soft": "#f8fafc",
    "text": "#0f172a",
    "muted": "#64748b",
    "border": "#e5e7eb",
    "border_strong": "#cbd5e1",
    "accent": "#10a37f",
    "accent_hover": "#0f8f70",
    "accent_soft": "#d1fae5",
    "secondary": "#1f2937",
    "secondary_hover": "#374151",
    "select": "#d1fae5",
    "warning": "#b45309",
    "success": "#15803d",
    "shadow_hint": "#e2e8f0",
}


def apply_openai_theme(widget: tk.Misc) -> dict[str, str]:
    widget.configure(bg=PALETTE["bg"])
    style = ttk.Style(widget)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=PALETTE["bg"], foreground=PALETTE["text"])
    style.configure("TFrame", background=PALETTE["bg"])
    style.configure("TLabel", background=PALETTE["bg"], foreground=PALETTE["text"])
    style.configure("Header.TLabel", background=PALETTE["bg"], foreground=PALETTE["text"], font=("Segoe UI", 18, "bold"))
    style.configure("SubHeader.TLabel", background=PALETTE["bg"], foreground=PALETTE["muted"], font=("Segoe UI", 10))
    style.configure("Section.TLabel", background=PALETTE["bg"], foreground=PALETTE["text"], font=("Segoe UI", 11, "bold"))
    style.configure("LabelTitle.TLabel", background=PALETTE["surface"], foreground=PALETTE["text"], font=("Segoe UI", 11, "bold"))
    style.configure("OutputTitle.TLabel", background=PALETTE["surface_alt"], foreground=PALETTE["text"], font=("Segoe UI", 11, "bold"))
    style.configure("FieldLabel.TLabel", background=PALETTE["surface"], foreground=PALETTE["muted"], font=("Segoe UI", 9, "bold"))
    style.configure("PrimaryText.TLabel", background=PALETTE["surface"], foreground=PALETTE["text"], font=("Segoe UI", 10))
    style.configure("Hint.TLabel", background=PALETTE["surface"], foreground=PALETTE["muted"], font=("Segoe UI", 9))
    style.configure("Muted.TLabel", background=PALETTE["bg"], foreground=PALETTE["muted"])
    style.configure("StatusOk.TLabel", background=PALETTE["bg"], foreground=PALETTE["success"], font=("Segoe UI", 9, "bold"))
    style.configure("StatusWarn.TLabel", background=PALETTE["bg"], foreground=PALETTE["warning"], font=("Segoe UI", 9, "bold"))
    style.configure("StatusNeutral.TLabel", background=PALETTE["bg"], foreground=PALETTE["muted"], font=("Segoe UI", 9))

    style.configure("Card.TFrame", background=PALETTE["surface"])
    style.configure("Toolbar.TFrame", background=PALETTE["bg"])
    style.configure("ToolbarCard.TFrame", background=PALETTE["surface_alt"])
    style.configure("Panel.TFrame", background=PALETTE["bg"])
    style.configure("PrimarySurface.TFrame", background=PALETTE["surface"])
    style.configure("OutputCard.TFrame", background=PALETTE["surface_alt"])
    style.configure("LogCard.TFrame", background=PALETTE["surface"])
    style.configure("Card.TLabelframe", background=PALETTE["surface"], bordercolor=PALETTE["border"])
    style.configure("Card.TLabelframe.Label", background=PALETTE["surface"], foreground=PALETTE["text"], font=("Segoe UI", 10, "bold"))

    style.configure(
        "Accent.TButton",
        background=PALETTE["accent"],
        foreground="#ffffff",
        borderwidth=0,
        focusthickness=0,
        padding=(12, 7),
    )
    style.map("Accent.TButton", background=[("active", PALETTE["accent_hover"])], foreground=[("active", "#ffffff")])

    style.configure(
        "Secondary.TButton",
        background=PALETTE["secondary"],
        foreground="#ffffff",
        borderwidth=0,
        focusthickness=0,
        padding=(11, 7),
    )
    style.map("Secondary.TButton", background=[("active", PALETTE["secondary_hover"])], foreground=[("active", "#ffffff")])

    style.configure(
        "Ghost.TButton",
        background=PALETTE["surface"],
        foreground=PALETTE["text"],
        bordercolor=PALETTE["border_strong"],
        padding=(10, 7),
    )
    style.map("Ghost.TButton", background=[("active", PALETTE["surface_soft"])])

    style.configure("TEntry", fieldbackground=PALETTE["surface"], foreground=PALETTE["text"])
    style.configure("TCombobox", fieldbackground=PALETTE["surface"], foreground=PALETTE["text"])
    try:
        style.configure("TSpinbox", fieldbackground=PALETTE["surface"], foreground=PALETTE["text"])
    except tk.TclError:
        pass
    style.configure("Accent.Horizontal.TProgressbar", troughcolor=PALETTE["border"], background=PALETTE["accent"])

    return PALETTE
