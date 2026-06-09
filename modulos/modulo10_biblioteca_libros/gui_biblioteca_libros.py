from __future__ import annotations

import json
import os
import re
import unicodedata
import tkinter as tk
from difflib import SequenceMatcher
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from modulos.modulo9_organizador_libros.controlador_organizador_libros import (
    BOOK_STATES,
    BookCreateInput,
    BookProgressController,
)
from utils.project_layout import normalize_instance_name, project_dirs, remap_legacy_drive_path
from utils.styles import apply_openai_theme

try:
    from PIL import Image, ImageGrab, ImageTk  # type: ignore
except Exception:  # pragma: no cover - fallback when Pillow is unavailable
    Image = None
    ImageGrab = None
    ImageTk = None


class BookLibraryWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Modulo 10 - Biblioteca de Libros")
        self.geometry("1360x860")
        self.minsize(1180, 720)
        self._maximize_window()

        self.controller = BookProgressController()
        self.palette = apply_openai_theme(self)

        self.db_name_var = tk.StringVar(value="")
        self.search_var = tk.StringVar(value="")
        self.state_var = tk.StringVar(value="todos")
        self.course_filter_var = tk.StringVar(value="todos")
        self.editorial_filter_var = tk.StringVar(value="todos")
        self.author_filter_var = tk.StringVar(value="todos")
        self.status_var = tk.StringVar(value="Sin libros cargados.")

        self._books: list[dict] = []
        self._card_frames: list[ttk.Frame] = []
        self._thumb_refs: list[object] = []
        self._active_book_id: int | None = None
        self._visible_books: list[dict] = []

        self._build_ui()
        self._load_databases()

    def _maximize_window(self) -> None:
        try:
            self.state("zoomed")
            return
        except Exception:
            pass
        try:
            self.attributes("-zoomed", True)
        except Exception:
            pass

    def _build_ui(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x", padx=18, pady=(16, 4))
        ttk.Label(header, text="Modulo 10 - Biblioteca de Libros", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Explora el catalogo como biblioteca visual y abre rapido el organizador, PDF y carpeta de trabajo.",
            style="SubHeader.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        top = ttk.Frame(self, style="Card.TFrame", padding=14)
        top.pack(fill="x", padx=18, pady=(10, 0))
        ttk.Label(top, text="Base de datos").grid(row=0, column=0, sticky="w")
        self.combo_db = ttk.Combobox(top, state="readonly", textvariable=self.db_name_var, values=[])
        self.combo_db.grid(row=0, column=1, sticky="ew", padx=(8, 12))
        self.combo_db.bind("<<ComboboxSelected>>", lambda _e: self._load_books())

        ttk.Label(top, text="Buscar libro o instancia").grid(row=0, column=2, sticky="w")
        search = ttk.Entry(top, textvariable=self.search_var)
        search.grid(row=0, column=3, sticky="ew", padx=(8, 12))
        search.bind("<KeyRelease>", lambda _e: self._render_books())
        self.search_var.trace_add("write", lambda *_args: self._render_books())

        ttk.Label(top, text="Estado").grid(row=0, column=4, sticky="w")
        self.combo_state = ttk.Combobox(
            top,
            state="readonly",
            textvariable=self.state_var,
            values=["todos", "pendiente", "en_progreso", "completo"],
            width=16,
        )
        self.combo_state.grid(row=0, column=5, sticky="w", padx=(8, 12))
        self.combo_state.bind("<<ComboboxSelected>>", lambda _e: self._render_books())

        top_actions = ttk.Frame(top)
        top_actions.grid(row=0, column=6, sticky="e")
        ttk.Button(top_actions, text="Nuevo libro", command=self._create_book, style="Accent.TButton").pack(side="left")
        ttk.Button(top_actions, text="Refrescar", command=self._load_books, style="Ghost.TButton").pack(side="left", padx=(8, 0))

        ttk.Label(top, text="Curso").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.combo_course = ttk.Combobox(top, state="readonly", textvariable=self.course_filter_var, values=["todos"])
        self.combo_course.grid(row=1, column=1, sticky="ew", padx=(8, 12), pady=(10, 0))
        self.combo_course.bind("<<ComboboxSelected>>", lambda _e: self._render_books())

        ttk.Label(top, text="Editorial").grid(row=1, column=2, sticky="w", pady=(10, 0))
        self.combo_editorial = ttk.Combobox(top, state="readonly", textvariable=self.editorial_filter_var, values=["todos"])
        self.combo_editorial.grid(row=1, column=3, sticky="ew", padx=(8, 12), pady=(10, 0))
        self.combo_editorial.bind("<<ComboboxSelected>>", lambda _e: self._render_books())

        ttk.Label(top, text="Autor").grid(row=1, column=4, sticky="w", pady=(10, 0))
        self.combo_author = ttk.Combobox(top, state="readonly", textvariable=self.author_filter_var, values=["todos"], width=20)
        self.combo_author.grid(row=1, column=5, sticky="ew", padx=(8, 12), pady=(10, 0))
        self.combo_author.bind("<<ComboboxSelected>>", lambda _e: self._render_books())
        top.columnconfigure(1, weight=1)
        top.columnconfigure(3, weight=2)

        info = ttk.Frame(self)
        info.pack(fill="x", padx=18, pady=(10, 4))
        ttk.Label(info, textvariable=self.status_var, style="Muted.TLabel").pack(side="left")

        content = ttk.Frame(self, style="Card.TFrame", padding=0)
        content.pack(fill="both", expand=True, padx=18, pady=(4, 18))
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            content,
            bg=self.palette["surface_alt"],
            highlightthickness=0,
            bd=0,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(content, orient="vertical", command=self.canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.cards_host = ttk.Frame(self.canvas, style="Panel.TFrame")
        self.cards_window = self.canvas.create_window((0, 0), window=self.cards_host, anchor="nw")

        self.cards_host.bind("<Configure>", self._on_cards_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self.bind("<Control-v>", self._on_paste_cover_shortcut, add="+")

    def _on_cards_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfigure(self.cards_window, width=event.width)

    def _on_mousewheel(self, event) -> None:
        if not self.winfo_exists():
            return
        try:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:
            return

    def _load_databases(self) -> None:
        try:
            dbs = self.controller.listar_bases_datos()
        except Exception as exc:
            messagebox.showerror("Biblioteca", f"No se pudieron listar las bases de datos.\n{exc}")
            self.combo_db.configure(values=[])
            return
        self.combo_db.configure(values=dbs)
        if not dbs:
            self.db_name_var.set("")
            self._books = []
            self._render_books()
            return
        configured = str(os.getenv("DB_NAME", "") or "").strip()
        preferred = next((value for value in dbs if value == configured), dbs[0])
        self.db_name_var.set(preferred)
        self._load_books()

    def _load_books(self) -> None:
        db_name = str(self.db_name_var.get() or "").strip()
        if not db_name:
            self._books = []
            self._render_books()
            return
        try:
            books = self.controller.listar_libros(db_name)
        except Exception as exc:
            messagebox.showerror("Biblioteca", f"No se pudieron cargar los libros.\n{exc}")
            self._books = []
            self._render_books()
            return

        self._books = [dict(book) for book in books]
        self._refresh_filter_options()
        self._render_books()

    def _filter_books(self) -> list[dict]:
        query = self._normalize_search_text(self.search_var.get())
        state_filter = str(self.state_var.get() or "todos").strip().lower()
        course_filter = str(self.course_filter_var.get() or "todos").strip().lower()
        editorial_filter = str(self.editorial_filter_var.get() or "todos").strip().lower()
        author_filter = str(self.author_filter_var.get() or "todos").strip().lower()
        out: list[dict] = []
        for book in self._books:
            state_value = str(book.get("estado") or "").strip().lower()
            course_value = str(book.get("curso") or "").strip().lower()
            editorial_value = str(book.get("editorial") or "").strip().lower()
            author_value = str(book.get("autor") or "").strip().lower()
            if state_filter != "todos" and state_value != state_filter:
                continue
            if course_filter != "todos" and course_value != course_filter:
                continue
            if editorial_filter != "todos" and editorial_value != editorial_filter:
                continue
            if author_filter != "todos" and author_value != author_filter:
                continue
            if query and not self._book_matches_query(book, query):
                continue
            out.append(book)
        return out

    def _normalize_search_text(self, value: object) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        text = unicodedata.normalize("NFD", text)
        text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
        text = re.sub(r"[_\\/\-.,;:()\[\]{}]+", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _book_matches_query(self, book: dict, query: str) -> bool:
        fields = [
            book.get("codigo"),
            book.get("titulo"),
            book.get("autor"),
            book.get("curso"),
            book.get("editorial"),
            book.get("workspace_dir"),
            book.get("pdf_path"),
        ]
        for item in self._parse_instances_health(book):
            fields.append(item.get("tipo"))
        haystack = self._normalize_search_text(" ".join(str(v or "") for v in fields))
        if not haystack:
            return False
        if query in haystack:
            return True

        query_tokens = [tok for tok in query.split() if tok]
        hay_tokens = [tok for tok in haystack.split() if tok]
        if not query_tokens:
            return True
        return all(self._token_matches_any(token, hay_tokens) for token in query_tokens)

    def _token_matches_any(self, needle: str, hay_tokens: list[str]) -> bool:
        if not needle:
            return True
        if len(needle) <= 2:
            return any(token.startswith(needle) or needle in token for token in hay_tokens)
        threshold = 0.82 if len(needle) <= 6 else 0.74
        for token in hay_tokens:
            if needle in token:
                return True
            if len(token) >= 3 and token in needle:
                return True
            if len(needle) <= 4 and self._is_subsequence(needle, token):
                return True
            if SequenceMatcher(None, needle, token).ratio() >= threshold:
                return True
        return False

    def _is_subsequence(self, needle: str, haystack: str) -> bool:
        if not needle:
            return True
        pos = 0
        for ch in haystack:
            if ch == needle[pos]:
                pos += 1
                if pos >= len(needle):
                    return True
        return False

    def _refresh_filter_options(self) -> None:
        self._set_filter_values(
            self.combo_course,
            self.course_filter_var,
            self._collect_distinct_values("curso"),
        )
        self._set_filter_values(
            self.combo_editorial,
            self.editorial_filter_var,
            self._collect_distinct_values("editorial"),
        )
        self._set_filter_values(
            self.combo_author,
            self.author_filter_var,
            self._collect_distinct_values("autor"),
        )

    def _collect_distinct_values(self, field_name: str) -> list[str]:
        values: dict[str, str] = {}
        for book in self._books:
            raw = str(book.get(field_name) or "").strip()
            if not raw:
                continue
            values.setdefault(raw.lower(), raw)
        ordered = [values[key] for key in sorted(values.keys())]
        return ["todos", *ordered]

    def _set_filter_values(self, combo: ttk.Combobox, var: tk.StringVar, values: list[str]) -> None:
        combo.configure(values=values)
        current = str(var.get() or "").strip()
        if current in values:
            return
        var.set("todos")

    def _render_books(self) -> None:
        for child in self.cards_host.winfo_children():
            child.destroy()
        self._thumb_refs.clear()

        books = self._filter_books()
        self._visible_books = books
        self.status_var.set(f"Mostrando {len(books)} libro(s).")
        if not books:
            self._active_book_id = None
            empty = ttk.Frame(self.cards_host, style="Card.TFrame", padding=24)
            empty.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
            ttk.Label(empty, text="No hay libros para mostrar.", style="Section.TLabel").pack(anchor="center")
            ttk.Label(
                empty,
                text="Ajusta la busqueda, el filtro o crea libros desde la Biblioteca.",
                style="Muted.TLabel",
            ).pack(anchor="center", pady=(8, 0))
            return

        columns = 3
        for col in range(columns):
            self.cards_host.columnconfigure(col, weight=1)

        for idx, book in enumerate(books):
            row = idx // columns
            col = idx % columns
            self._build_book_card(self.cards_host, book, row=row, column=col)

    def _build_book_card(self, parent: ttk.Frame, book: dict, *, row: int, column: int) -> None:
        outer = ttk.Frame(parent, style="Card.TFrame", padding=0)
        outer.grid(row=row, column=column, sticky="nsew", padx=10, pady=10)
        outer.columnconfigure(1, weight=1)

        accent = tk.Frame(outer, bg=self._state_color(str(book.get("estado") or "")), width=10)
        accent.grid(row=0, column=0, sticky="ns")

        card = ttk.Frame(outer, style="Card.TFrame", padding=14)
        card.grid(row=0, column=1, sticky="nsew")
        card.columnconfigure(1, weight=1)

        title = str(book.get("titulo") or "(sin titulo)")
        code = str(book.get("codigo") or "-")
        author = str(book.get("autor") or "-")
        editorial = str(book.get("editorial") or "-")
        course = str(book.get("curso") or "-")
        state = str(book.get("estado") or "-")
        workspace = str(book.get("workspace_dir") or "").strip()
        pdf_path = str(book.get("pdf_path") or "").strip()
        cover_path = str(book.get("cover_path") or "").strip()
        image_status = self._path_state(cover_path)

        cover_host = ttk.Frame(card, style="Card.TFrame", width=116)
        cover_host.grid(row=0, column=0, rowspan=4, sticky="nsw", padx=(0, 14))
        cover_host.grid_propagate(False)
        self._render_cover_preview(cover_host, cover_path, title)

        ttk.Label(card, text=title, style="Section.TLabel", wraplength=220).grid(row=0, column=1, sticky="w")
        ttk.Label(card, text=f"{code} | {state}", style="Muted.TLabel").grid(row=1, column=1, sticky="w", pady=(4, 0))

        meta = ttk.Frame(card, style="Card.TFrame")
        meta.grid(row=2, column=1, sticky="ew", pady=(10, 0))
        ttk.Label(meta, text=f"Autor: {author}", style="Muted.TLabel").pack(anchor="w")
        ttk.Label(meta, text=f"Editorial: {editorial}", style="Muted.TLabel").pack(anchor="w", pady=(4, 0))
        ttk.Label(meta, text=f"Curso: {course}", style="Muted.TLabel").pack(anchor="w", pady=(4, 0))
        ttk.Label(
            meta,
            text=(
                f"Instancias: {int(book.get('instances_total') or 0)}"
                f" | Meta total: {int(book.get('instances_expected_total') or 0)}"
            ),
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(4, 0))
        ttk.Label(
            meta,
            text=(
                f"Sesiones: {int(book.get('instances_session_count') or 0)}"
                f" | Soluciones: {int(book.get('instances_solutions_count') or 0)}"
            ),
            style="Muted.TLabel",
            wraplength=320,
        ).pack(anchor="w", pady=(4, 0))
        ttk.Label(
            meta,
            text=(
                "Consistencia problemas: "
                f"C={int(book.get('consistency_consistentes_total') or 0)}"
                f" | I={int(book.get('consistency_inconsistentes_total') or 0)}"
                f" | SR={int(book.get('consistency_sin_revisar_total') or 0)}"
            ),
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(4, 0))
        self._render_instance_health_badges(meta, book)
        ttk.Label(
            meta,
            text=f"PDF: {self._path_state(pdf_path)} | Carpeta: {self._path_state(workspace)}",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(4, 0))
        ttk.Label(
            meta,
            text=f"Imagen: {image_status}",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        actions = ttk.Frame(card, style="Card.TFrame")
        actions.grid(row=3, column=1, sticky="ew", pady=(14, 0))
        for col in range(2):
            actions.grid_columnconfigure(col, weight=1)
        btn_open = ttk.Button(
            actions,
            text="Abrir organizador",
            command=lambda book_id=int(book["id"]): self._open_book_in_organizer(book_id),
            style="Accent.TButton",
        )
        btn_image = ttk.Button(
            actions,
            text="Imagen...",
            command=lambda current=book: (self._activate_book(current), self._select_cover_image(current)),
            style="Ghost.TButton",
        )
        btn_pdf = ttk.Button(
            actions,
            text="PDF",
            command=lambda current=book, path=pdf_path: (self._activate_book(current), self._open_path(path, expect_file=True)),
            style="Ghost.TButton",
        )
        btn_folder = ttk.Button(
            actions,
            text="Carpeta",
            command=lambda current=book, path=workspace: (self._activate_book(current), self._open_path(path, expect_file=False)),
            style="Ghost.TButton",
        )
        btn_open.grid(row=0, column=0, sticky="ew", padx=(0, 4), pady=(0, 6))
        btn_image.grid(row=0, column=1, sticky="ew", padx=(4, 0), pady=(0, 6))
        btn_pdf.grid(row=1, column=0, sticky="ew", padx=(0, 4))
        btn_folder.grid(row=1, column=1, sticky="ew", padx=(4, 0))

        def _open(_event=None) -> None:
            self._activate_book(book)
            self._open_book_in_organizer(int(book["id"]))

        def _mark_active(_event=None) -> None:
            self._activate_book(book)

        for widget in (outer, card, cover_host, meta, actions):
            widget.bind("<Button-1>", _mark_active, add="+")
            widget.bind("<Double-Button-1>", _open, add="+")

    def _render_cover_preview(self, parent: ttk.Frame, cover_path: str, title: str) -> None:
        clean = str(remap_legacy_drive_path(str(cover_path or "").strip(), prefer_existing=True))
        if clean and Image is not None and ImageTk is not None:
            try:
                preview = Image.open(clean)
                preview.thumbnail((104, 148))
                tk_img = ImageTk.PhotoImage(preview)
                self._thumb_refs.append(tk_img)
                label = ttk.Label(parent, image=tk_img)
                label.place(relx=0.5, rely=0.5, anchor="center")
                return
            except Exception:
                pass
        placeholder = tk.Frame(parent, bg=self.palette["surface_alt"], highlightthickness=1, highlightbackground=self.palette["border"])
        placeholder.place(relx=0.5, rely=0.5, anchor="center", width=104, height=148)
        initials = self._book_initials(title)
        tk.Label(
            placeholder,
            text=initials,
            bg=self.palette["surface_alt"],
            fg=self.palette["text"],
            font=("Segoe UI", 18, "bold"),
        ).place(relx=0.5, rely=0.45, anchor="center")
        tk.Label(
            placeholder,
            text="Sin imagen",
            bg=self.palette["surface_alt"],
            fg=self.palette["muted"],
            font=("Segoe UI", 9),
        ).place(relx=0.5, rely=0.73, anchor="center")

    def _book_initials(self, title: str) -> str:
        parts = [chunk for chunk in str(title or "").strip().split() if chunk]
        if not parts:
            return "LB"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][:1] + parts[1][:1]).upper()

    def _state_color(self, state: str) -> str:
        clean = str(state or "").strip().lower()
        if clean == "completo":
            return "#15803d"
        if clean == "en_progreso":
            return "#d97706"
        return "#334155"

    def _ok_flag(self, value: bool) -> str:
        return "OK" if value else "-"

    def _path_state(self, path: str) -> str:
        clean = str(path or "").strip()
        if not clean:
            return "-"
        try:
            return "OK" if remap_legacy_drive_path(clean, prefer_existing=True).exists() else "Falta"
        except Exception:
            return "Falta"

    def _parse_instances_health(self, book: dict) -> list[dict]:
        raw = book.get("instances_health")
        if isinstance(raw, list):
            return [dict(item) for item in raw if isinstance(item, dict)]
        raw_json = str(book.get("instances_health_json") or "").strip()
        if not raw_json:
            return []
        try:
            parsed = json.loads(raw_json)
        except Exception:
            return []
        if not isinstance(parsed, list):
            return []
        return [dict(item) for item in parsed if isinstance(item, dict)]

    def _instance_health_colors(self, status: str) -> tuple[str, str]:
        key = str(status or "").strip().lower()
        if key == "complete":
            return ("#15803d", "#ffffff")
        if key == "complete_with_inconsistencies":
            return ("#dc2626", "#ffffff")
        if key == "in_progress":
            return ("#d97706", "#111827")
        if key == "empty":
            return ("#ffffff", "#334155")
        return ("#64748b", "#ffffff")

    def _instance_health_tooltip(self, item: dict) -> str:
        return (
            f"{item.get('tipo')}: "
            f"T={int(item.get('total') or 0)} | "
            f"C={int(item.get('consistentes') or 0)} | "
            f"I={int(item.get('inconsistentes') or 0)} | "
            f"SR={int(item.get('sin_revisar') or 0)}"
        )

    def _instance_natural_sort_key(self, value: str) -> tuple[int, str]:
        text = str(value or "").strip().lower()
        match = re.match(r"^s(\d+)(.*)$", text)
        if match:
            return (int(match.group(1)), match.group(2))
        return (10**9, text)

    def _render_instance_health_badges(self, parent: ttk.Frame, book: dict) -> None:
        items = self._parse_instances_health(book)
        if not items:
            ttk.Label(
                parent,
                text="Instancias (estado): (sin datos)",
                style="Muted.TLabel",
            ).pack(anchor="w", pady=(4, 0))
            return
        ttk.Label(
            parent,
            text="Instancias: PDF IA conserva el flujo legacy; Fabrica staging abre el flujo revisable por instancia.",
            style="Muted.TLabel",
            wraplength=330,
        ).pack(anchor="w", pady=(4, 0))
        badges = tk.Frame(parent, bg=self.palette["surface"])
        badges.pack(anchor="w", pady=(4, 0), fill="x")
        items = sorted(items, key=lambda row: self._instance_natural_sort_key(str(row.get("tipo") or "")))
        max_cols = 1
        for idx, item in enumerate(items):
            row = idx // max_cols
            col = idx % max_cols
            bg, fg = self._instance_health_colors(str(item.get("status") or ""))
            label_text = str(item.get("tipo") or "-")
            row_frame = tk.Frame(badges, bg=self.palette["surface"])
            row_frame.grid(row=row, column=col, sticky="w", padx=(0, 4), pady=(0, 4))
            label = tk.Label(
                row_frame,
                text=self._short_instance_label(label_text),
                bg=bg,
                fg=fg,
                padx=6,
                pady=2,
                relief="solid",
                bd=1,
                font=("Segoe UI", 8, "bold"),
            )
            label.pack(side="left")
            label.configure(cursor="hand2")
            scan = tk.Label(
                row_frame,
                text="PDF IA",
                bg="#0f766e",
                fg="#ffffff",
                padx=5,
                pady=2,
                relief="solid",
                bd=1,
                font=("Segoe UI", 8, "bold"),
            )
            scan.pack(side="left", padx=(4, 0))
            scan.configure(cursor="hand2")
            factory = tk.Label(
                row_frame,
                text="Fabrica staging",
                bg="#1d4ed8",
                fg="#ffffff",
                padx=5,
                pady=2,
                relief="solid",
                bd=1,
                font=("Segoe UI", 8, "bold"),
            )
            factory.pack(side="left", padx=(4, 0))
            factory.configure(cursor="hand2")
            tooltip = self._instance_health_tooltip(item)
            label.bind(
                "<Button-1>",
                lambda _e, b=book, it=dict(item), msg=tooltip: self._open_instance_badge(b, it, tooltip=msg),
                add="+",
            )
            label.bind(
                "<Double-Button-1>",
                lambda _e, b=book, it=dict(item), msg=tooltip: self._open_instance_badge(b, it, tooltip=msg),
                add="+",
            )
            scan.bind(
                "<Button-1>",
                lambda _e, b=book, it=dict(item): self._open_pdf_ai_for_instance(b, it),
                add="+",
            )
            factory.bind(
                "<Button-1>",
                lambda _e, b=book, it=dict(item): self._open_pdf_factory_for_instance(b, it),
                add="+",
            )

    def _short_instance_label(self, value: str, *, max_chars: int = 38) -> str:
        text = str(value or "-").strip() or "-"
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3].rstrip("_- .") + "..."

    def _open_instance_badge(self, book: dict, item: dict, *, tooltip: str = "") -> None:
        db_name = str(self.db_name_var.get() or "").strip()
        if not db_name:
            messagebox.showwarning("Biblioteca", "Selecciona una base de datos.")
            return
        book_code = str(book.get("codigo") or "").strip()
        instance_type = str(item.get("tipo") or "").strip()
        if not book_code or not instance_type:
            messagebox.showwarning("Biblioteca", "La instancia no tiene codigo/tipo suficiente para abrirse.")
            return
        try:
            from modulos.modulo0_transcriptor.gui_transcriptor import TranscriptorWindow

            session_path = ""
            try:
                instancia = self.controller.obtener_instancia(db_name, int(book.get("id") or 0), instance_type) or {}
                session_path = str(instancia.get("session_path") or "").strip()
            except Exception:
                session_path = ""
            workspace_dir = str(book.get("workspace_dir") or "").strip()
            inferred_session_path = ""
            if workspace_dir and instance_type:
                try:
                    instance_name = normalize_instance_name(instance_type, "sesion")
                    inferred = project_dirs(Path(workspace_dir), instance_name).get("session_path")
                    if inferred is not None:
                        inferred_session_path = str(inferred)
                except Exception:
                    inferred_session_path = ""
            session_path_lower = session_path.lower().replace("/", "\\")
            looks_cache_path = "\\.cache\\transcriptor_runs\\sessions" in session_path_lower
            if (not session_path) or looks_cache_path:
                session_path = inferred_session_path or session_path

            TranscriptorWindow(
                self,
                initial_db_name=db_name,
                initial_book_code=book_code,
                initial_instance_type=instance_type,
                initial_pdf_path=str(book.get("pdf_path") or "").strip(),
                linked_session_path=session_path,
                initial_project_name=str(book.get("titulo") or "").strip(),
            )
            self.status_var.set(tooltip or f"Abrir instancia: {book_code} / {instance_type}")
        except Exception as exc:
            messagebox.showerror("Biblioteca", f"No se pudo abrir la instancia en Transcriptor.\n{exc}")

    def _open_pdf_ai_for_instance(self, book: dict, item: dict) -> None:
        book_code = str(book.get("codigo") or "").strip()
        instance_type = str(item.get("tipo") or "").strip()
        pdf_path = str(book.get("pdf_path") or "").strip()
        if not book_code or not instance_type:
            messagebox.showwarning("Biblioteca", "La instancia no tiene codigo/tipo suficiente para escanear.")
            return
        if not pdf_path:
            messagebox.showwarning("Biblioteca", "Este libro no tiene PDF registrado.")
            return
        try:
            resolved_pdf = remap_legacy_drive_path(pdf_path, prefer_existing=True)
        except Exception:
            resolved_pdf = Path(pdf_path)
        if not resolved_pdf.exists():
            messagebox.showwarning("Biblioteca", f"No se encontro el PDF del libro:\n{resolved_pdf}")
            return
        try:
            from modulos.modulo13_laboratorio_pdf_segmentacion.gui_laboratorio_pdf import PdfSegmentationLabWindow

            instance_name = f"{book_code}__{instance_type}"
            title = f"Recorte PDF IA - {book_code} / {instance_type}"
            session_path = self._resolve_instance_session_path(book, instance_type)
            PdfSegmentationLabWindow(
                self,
                initial_instance_name=instance_name,
                initial_pdf_path=str(resolved_pdf),
                initial_title=title,
                linked_session_path=str(session_path or ""),
                book_code=book_code,
                instance_type=instance_type,
                project_name=str(book.get("titulo") or "").strip(),
            )
            self.status_var.set(f"Flujo PDF IA abierto: {book_code} / {instance_type}")
        except Exception as exc:
            messagebox.showerror("Biblioteca", f"No se pudo abrir el flujo PDF IA.\n{exc}")

    def _open_pdf_factory_for_instance(self, book: dict, item: dict) -> None:
        book_code = str(book.get("codigo") or "").strip()
        instance_type = str(item.get("tipo") or "").strip()
        pdf_path = str(book.get("pdf_path") or "").strip()
        if not book_code or not instance_type:
            messagebox.showwarning("Biblioteca", "La instancia no tiene codigo/tipo suficiente para iniciar la fabrica.")
            return
        if not pdf_path:
            messagebox.showwarning("Biblioteca", "Este libro no tiene PDF registrado.")
            return
        try:
            resolved_pdf = remap_legacy_drive_path(pdf_path, prefer_existing=True)
        except Exception:
            resolved_pdf = Path(pdf_path)
        if not resolved_pdf.exists():
            messagebox.showwarning("Biblioteca", f"No se encontro el PDF del libro:\n{resolved_pdf}")
            return
        try:
            from modulos.instance_factory.models import InstancePipelineContext
            from modulos.instance_factory.web_launcher import open_factory_web_app

            session_path = self._resolve_instance_session_path(book, instance_type)
            context = InstancePipelineContext.from_library_instance(
                book,
                item,
                db_name=str(self.db_name_var.get() or "").strip(),
                session_path=session_path,
            )
            open_factory_web_app(
                self,
                context=context,
            )
            self.status_var.set(f"Fabrica PDF web abierta: {book_code} / {instance_type}")
        except Exception as exc:
            messagebox.showerror("Biblioteca", f"No se pudo abrir la fabrica PDF.\n{exc}")

    def _resolve_instance_session_path(self, book: dict, instance_type: str) -> Path | None:
        db_name = str(self.db_name_var.get() or "").strip()
        try:
            if db_name:
                instancia = self.controller.obtener_instancia(db_name, int(book.get("id") or 0), instance_type) or {}
                session_path = str(instancia.get("session_path") or "").strip()
                if session_path:
                    return remap_legacy_drive_path(Path(session_path).expanduser(), prefer_existing=False)
        except Exception:
            pass
        workspace_dir = str(book.get("workspace_dir") or "").strip()
        if not workspace_dir:
            return None
        try:
            instance_name = normalize_instance_name(instance_type, "sesion")
            inferred = project_dirs(Path(workspace_dir), instance_name).get("session_path")
            return Path(inferred) if inferred is not None else None
        except Exception:
            return None

    def _open_book_in_organizer(self, book_id: int) -> None:
        db_name = str(self.db_name_var.get() or "").strip()
        if not db_name:
            messagebox.showwarning("Biblioteca", "Selecciona una base de datos.")
            return
        try:
            from modulos.modulo9_organizador_libros.gui_organizador_libros import BookProgressWindow

            BookProgressWindow(self, initial_db_name=db_name, initial_book_id=int(book_id))
        except Exception as exc:
            messagebox.showerror("Biblioteca", f"No se pudo abrir el organizador.\n{exc}")

    def _create_book(self) -> None:
        db_name = str(self.db_name_var.get() or "").strip()
        if not db_name:
            messagebox.showwarning("Biblioteca", "Selecciona una base de datos.")
            return
        data = self._book_dialog("Nuevo libro")
        if not data:
            return
        try:
            book_id = self.controller.crear_libro(db_name, BookCreateInput(**data))
            created = self.controller.obtener_libro(db_name, int(book_id)) or {}
        except Exception as exc:
            messagebox.showerror("Biblioteca", f"No se pudo crear el libro.\n{exc}")
            return
        self._load_books()
        if created:
            self._activate_book(created)
            title = str(created.get("titulo") or "(sin titulo)")
            code = str(created.get("codigo") or "-")
            self.status_var.set(f"Libro creado: [{created.get('id')}] {code} | {title}")
        else:
            self.status_var.set(f"Libro creado: ID {book_id}")
        if messagebox.askyesno("Biblioteca", "Libro creado correctamente.\n\n¿Deseas abrirlo en el Organizador?"):
            self._open_book_in_organizer(int(book_id))

    def _book_dialog(self, title: str, initial: dict | None = None) -> dict | None:
        fields = [
            {"name": "workspace_dir", "label": "Carpeta base del proyecto (opcional)", "kind": "dir"},
            {"name": "codigo", "label": "Codigo (opcional)"},
            {"name": "titulo", "label": "Titulo (opcional)"},
            {"name": "autor", "label": "Autor"},
            {"name": "editorial", "label": "Editorial"},
            {"name": "edicion", "label": "Edicion"},
            {"name": "curso", "label": "Curso"},
            {"name": "pdf_path", "label": "Ruta del archivo PDF (opcional)", "kind": "file"},
            {"name": "cover_path", "label": "Imagen del libro (opcional)", "kind": "file"},
            {"name": "estado", "label": "Estado", "kind": "choice", "values": list(BOOK_STATES), "default": "pendiente"},
            {"name": "notas", "label": "Notas"},
            {"name": "activo", "label": "Activo", "kind": "bool", "default": True},
        ]
        dialog = _BookFormDialog(self, title, fields, initial=initial)
        return dialog.result

    def _open_path(self, raw_path: str, *, expect_file: bool) -> None:
        path = str(raw_path or "").strip()
        if not path:
            messagebox.showwarning("Biblioteca", "No hay ruta registrada.")
            return
        target = remap_legacy_drive_path(Path(path), prefer_existing=True)
        if not target.exists():
            messagebox.showwarning("Biblioteca", f"La ruta no existe:\n{path}")
            return
        try:
            if expect_file:
                os.startfile(str(target))
            else:
                os.startfile(str(target if target.is_dir() else target.parent))
        except Exception as exc:
            messagebox.showerror("Biblioteca", f"No se pudo abrir la ruta.\n{exc}")

    def _select_cover_image(self, book: dict) -> None:
        db_name = str(self.db_name_var.get() or "").strip()
        if not db_name:
            messagebox.showwarning("Biblioteca", "Selecciona una base de datos.")
            return
        current = str(book.get("cover_path") or "").strip()
        initial_dir = ""
        if current:
            try:
                initial_dir = str(Path(current).expanduser().parent)
            except Exception:
                initial_dir = ""
        selected = filedialog.askopenfilename(
            parent=self,
            title="Seleccionar imagen del libro",
            initialdir=initial_dir or None,
            filetypes=[
                ("Imagenes", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"),
                ("Todos los archivos", "*.*"),
            ],
        )
        if not selected:
            return
        try:
            self.controller.actualizar_libro(
                db_name,
                int(book["id"]),
                payload=self._build_update_payload(book, cover_path=selected),
            )
        except Exception as exc:
            messagebox.showerror("Biblioteca", f"No se pudo guardar la imagen del libro.\n{exc}")
            return
        self._load_books()

    def _activate_book(self, book: dict) -> None:
        try:
            self._active_book_id = int(book["id"])
        except Exception:
            self._active_book_id = None
            return
        title = str(book.get("titulo") or "(sin titulo)")
        self.status_var.set(f"Mostrando {len(self._visible_books)} libro(s). Activo para imagen: {title}.")

    def _on_paste_cover_shortcut(self, _event=None):
        book = self._resolve_target_book_for_paste()
        if not book:
            messagebox.showwarning("Biblioteca", "Haz clic en una tarjeta y luego usa Ctrl+V para pegar su imagen.")
            return "break"
        self._paste_cover_image(book)
        return "break"

    def _resolve_target_book_for_paste(self) -> dict | None:
        if self._active_book_id is not None:
            for book in self._visible_books:
                if int(book.get("id") or 0) == self._active_book_id:
                    return book
        if len(self._visible_books) == 1:
            book = self._visible_books[0]
            self._activate_book(book)
            return book
        return None

    def _paste_cover_image(self, book: dict) -> None:
        if ImageGrab is None:
            messagebox.showerror("Biblioteca", "Pegar imagen requiere Pillow con soporte de portapapeles.")
            return
        try:
            clipboard = ImageGrab.grabclipboard()
        except Exception as exc:
            messagebox.showerror("Biblioteca", f"No se pudo leer el portapapeles.\n{exc}")
            return
        if clipboard is None:
            messagebox.showwarning("Biblioteca", "No hay una imagen en el portapapeles.")
            return
        if isinstance(clipboard, list):
            messagebox.showwarning("Biblioteca", "El portapapeles no contiene una imagen directa. Copia una imagen y vuelve a intentar.")
            return
        workspace = self._ensure_workspace_for_cover(book)
        if not workspace:
            return
        assets_dir = Path(workspace) / "assets"
        try:
            assets_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            messagebox.showerror("Biblioteca", f"No se pudo preparar la carpeta de imagenes del libro.\n{exc}")
            return
        cover_file = assets_dir / "cover.png"
        try:
            clipboard.save(cover_file, format="PNG")
        except Exception as exc:
            messagebox.showerror("Biblioteca", f"No se pudo guardar la imagen pegada.\n{exc}")
            return
        db_name = str(self.db_name_var.get() or "").strip()
        if not db_name:
            messagebox.showwarning("Biblioteca", "Selecciona una base de datos.")
            return
        try:
            self.controller.actualizar_libro(
                db_name,
                int(book["id"]),
                payload=self._build_update_payload(book, cover_path=str(cover_file)),
            )
        except Exception as exc:
            messagebox.showerror("Biblioteca", f"No se pudo guardar la imagen del libro.\n{exc}")
            return
        self._load_books()

    def _ensure_workspace_for_cover(self, book: dict) -> str:
        workspace = str(book.get("workspace_dir") or "").strip()
        if workspace:
            return workspace
        db_name = str(self.db_name_var.get() or "").strip()
        if not db_name:
            messagebox.showwarning("Biblioteca", "Selecciona una base de datos.")
            return ""
        try:
            self.controller.actualizar_libro(
                db_name,
                int(book["id"]),
                payload=self._build_update_payload(book, cover_path=str(book.get("cover_path") or "").strip()),
            )
            refreshed = self.controller.obtener_libro(db_name, int(book["id"])) or {}
        except Exception as exc:
            messagebox.showerror("Biblioteca", f"No se pudo preparar la carpeta del libro.\n{exc}")
            return ""
        workspace = str(refreshed.get("workspace_dir") or "").strip()
        if not workspace:
            messagebox.showerror("Biblioteca", "El libro no tiene carpeta de trabajo disponible.")
            return ""
        book.update(refreshed)
        return workspace

    def _build_update_payload(self, book: dict, *, cover_path: str):
        from modulos.modulo9_organizador_libros.controlador_organizador_libros import BookUpdateInput

        return BookUpdateInput(
            codigo=str(book.get("codigo") or "").strip(),
            titulo=str(book.get("titulo") or "").strip(),
            autor=str(book.get("autor") or "").strip(),
            editorial=str(book.get("editorial") or "").strip(),
            edicion=str(book.get("edicion") or "").strip(),
            curso=str(book.get("curso") or "").strip(),
            workspace_dir=str(book.get("workspace_dir") or "").strip(),
            pdf_path=str(book.get("pdf_path") or "").strip(),
            cover_path=str(cover_path or "").strip(),
            estado=str(book.get("estado") or "pendiente").strip(),
            notas=str(book.get("notas") or "").strip(),
            activo=bool(book.get("activo", True)),
        )


class _BookFormDialog(tk.Toplevel):
    def __init__(self, parent, title: str, fields: list[dict], initial: dict | None = None):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.result: dict | None = None
        self._vars: dict[str, tk.Variable] = {}
        self._fields = fields
        self._initial = initial or {}
        apply_openai_theme(self)
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.wait_visibility()
        self.focus_force()
        self.wait_window(self)

    def _build_ui(self) -> None:
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        for idx, field in enumerate(self._fields):
            name = str(field["name"])
            kind = str(field.get("kind", "str"))
            ttk.Label(body, text=str(field["label"])).grid(row=idx, column=0, sticky="w", pady=(0, 8))
            initial_value = self._initial.get(name, field.get("default"))
            if kind == "bool":
                var = tk.BooleanVar(value=bool(initial_value))
                widget = ttk.Checkbutton(body, variable=var)
            elif kind in {"choice", "combo"}:
                var = tk.StringVar(value=str(initial_value or ""))
                state = "readonly" if kind == "choice" else "normal"
                widget = ttk.Combobox(body, textvariable=var, values=field.get("values", []), state=state)
            elif kind == "int":
                var = tk.StringVar(value="" if initial_value is None else str(initial_value))
                widget = ttk.Entry(body, textvariable=var)
            elif kind in {"dir", "file"}:
                var = tk.StringVar(value=str(initial_value or ""))
                wrap = ttk.Frame(body)
                wrap.columnconfigure(0, weight=1)
                entry = ttk.Entry(wrap, textvariable=var)
                entry.grid(row=0, column=0, sticky="ew")
                ttk.Button(
                    wrap,
                    text="Carpeta..." if kind == "dir" else "Archivo...",
                    style="Ghost.TButton",
                    command=lambda current_var=var, current_kind=kind: self._browse_path(current_var, current_kind),
                ).grid(row=0, column=1, padx=(8, 0))
                widget = wrap
            else:
                var = tk.StringVar(value=str(initial_value or ""))
                widget = ttk.Entry(body, textvariable=var)
            self._vars[name] = var
            widget.grid(row=idx, column=1, sticky="ew", padx=(10, 0), pady=(0, 8))
        body.columnconfigure(1, weight=1)

        actions = ttk.Frame(body)
        actions.grid(row=len(self._fields), column=0, columnspan=2, sticky="e", pady=(4, 0))
        ttk.Button(actions, text="Cancelar", command=self._cancel, style="Ghost.TButton").pack(side="left")
        ttk.Button(actions, text="Guardar", command=self._submit, style="Accent.TButton").pack(side="left", padx=(8, 0))

    def _browse_path(self, var: tk.StringVar, kind: str) -> None:
        current = str(var.get() or "").strip()
        initial_dir = ""
        if current:
            try:
                current_path = Path(current).expanduser()
                initial_dir = str(current_path if current_path.is_dir() else current_path.parent)
            except Exception:
                initial_dir = ""
        try:
            if kind == "dir":
                selected = filedialog.askdirectory(parent=self, initialdir=initial_dir or None)
            else:
                selected = filedialog.askopenfilename(parent=self, initialdir=initial_dir or None)
        except Exception:
            selected = ""
        if selected:
            var.set(str(selected))

    def _submit(self) -> None:
        data: dict[str, object] = {}
        try:
            for field in self._fields:
                name = str(field["name"])
                kind = str(field.get("kind", "str"))
                value = self._vars[name].get()
                if kind == "int":
                    raw = str(value).strip()
                    data[name] = None if raw == "" else int(raw)
                elif kind == "bool":
                    data[name] = bool(value)
                else:
                    data[name] = str(value)
        except ValueError:
            messagebox.showerror("Formulario", "Revisa los campos numericos.")
            return
        self.result = data
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()
