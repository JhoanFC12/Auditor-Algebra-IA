from __future__ import annotations

import copy
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from database.connection import DatabaseManager
from database.problem_change_queue import ProblemChangeQueueController
from utils.project_layout import remap_legacy_drive_path
from utils.styles import apply_openai_theme

from .controlador_practicas import PracticeBuilderController


MATH_CONSISTENCY_VALUES = ["Sin revisar", "Consistente", "Inconsistente"]


class PracticeBuilderWindow(tk.Toplevel):
    def __init__(self, parent, *, initial_json_path: str | Path = "", overwrite_source_json_on_update: bool = False):
        super().__init__(parent)
        self.title("Modulo 6 - Editor de practica desde JSON")
        self.geometry("1360x820")
        self.minsize(1120, 700)
        self._maximize_window()

        self.local_db_manager = DatabaseManager.from_profile("local_mirror")
        self.controller = PracticeBuilderController(db_manager=self.local_db_manager)
        self.queue_controller = ProblemChangeQueueController()

        self.db_name_var = tk.StringVar(value=str(self.local_db_manager.db_name))
        self.json_path_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Carga un JSON de seleccion para empezar.")
        self.summary_var = tk.StringVar(value="Sin practica cargada.")
        self.pending_var = tk.StringVar(value="Cola local pendiente: 0")

        self.problem_id_var = tk.StringVar(value="-")
        self.numero_original_var = tk.StringVar(value="")
        self.curso_var = tk.StringVar(value="")
        self.tema_var = tk.StringVar(value="")
        self.subtema_var = tk.StringVar(value="")
        self.clave_var = tk.StringVar(value="")
        self.consistencia_matematica_var = tk.StringVar(value=MATH_CONSISTENCY_VALUES[0])
        self.autor_var = tk.StringVar(value="-")
        self.editorial_var = tk.StringVar(value="-")
        self.libro_var = tk.StringVar(value="-")
        self.instancia_var = tk.StringVar(value="-")
        self.pdf_var = tk.StringVar(value="-")

        self._source_json_path: Path | None = None
        self._source_payload: dict[str, object] = {}
        self._items: list[dict[str, object]] = []
        self._saved_state_by_id: dict[int, dict[str, object]] = {}
        self._dirty_ids: set[int] = set()
        self._current_problem_id: int | None = None
        self._loading_form = False
        self._overwrite_source_json_on_update = bool(overwrite_source_json_on_update)

        self._apply_light_theme()
        self._build_ui()
        self._listar_dbs()
        if str(initial_json_path or "").strip():
            self.after(100, lambda p=Path(initial_json_path): self._load_json_path(p))
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _apply_light_theme(self) -> None:
        self.palette = apply_openai_theme(self)

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
        header_wrap = ttk.Frame(self)
        header_wrap.pack(fill="x", padx=16, pady=(14, 4))
        ttk.Label(header_wrap, text="Modulo 6 - Editor de practica desde JSON", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            header_wrap,
            text="Abre un JSON de seleccion, edita problemas en local y deja la cola lista para el modulo Publicar Cambios.",
            style="SubHeader.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        top_card = ttk.Frame(self, style="Card.TFrame", padding=14)
        top_card.pack(fill="x", padx=16, pady=(8, 0))

        top = ttk.Frame(top_card)
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Base local").grid(row=0, column=0, sticky="w")
        self.combo_db = ttk.Combobox(top, state="readonly", textvariable=self.db_name_var, values=[])
        self.combo_db.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(top, text="Refrescar", command=self._listar_dbs, style="Ghost.TButton").grid(row=0, column=2, sticky="ew")

        ttk.Label(top, text="JSON de trabajo").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(top, textvariable=self.json_path_var).grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=(10, 0))

        path_actions = ttk.Frame(top)
        path_actions.grid(row=1, column=2, sticky="e", pady=(10, 0))
        ttk.Button(path_actions, text="Abrir JSON", command=self._pick_json, style="Ghost.TButton").pack(side="left")
        ttk.Button(path_actions, text="Recargar", command=self._reload_current_json, style="Ghost.TButton").pack(side="left", padx=(8, 0))

        actions = ttk.Frame(top_card)
        actions.pack(fill="x", pady=(12, 0))
        self.reload_from_db_btn = ttk.Button(actions, text="Recargar desde BD", command=self._reload_items_from_db, style="Ghost.TButton")
        self.reload_from_db_btn.pack(side="left")
        self.snapshot_btn = ttk.Button(actions, text="Guardar snapshot JSON", command=self._save_snapshot, style="Secondary.TButton")
        self.snapshot_btn.pack(side="left", padx=(8, 0))
        self.tex_btn = ttk.Button(actions, text="Actualizar TEX", command=self._update_tex_file, style="Secondary.TButton")
        self.tex_btn.pack(side="left", padx=(8, 0))
        self.save_btn = ttk.Button(actions, text="Guardar cambios en BD local", command=self._save_to_db, style="Accent.TButton")
        self.save_btn.pack(side="right")

        ttk.Label(self, textvariable=self.status_var, style="Section.TLabel").pack(anchor="w", padx=16, pady=(12, 0))
        ttk.Label(self, textvariable=self.summary_var, style="Muted.TLabel").pack(anchor="w", padx=16, pady=(2, 0))
        ttk.Label(self, textvariable=self.pending_var, style="Muted.TLabel").pack(anchor="w", padx=16, pady=(2, 8))

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=16, pady=(0, 0))

        left = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, weight=3)
        body.add(right, weight=5)

        ttk.Label(left, text="Problemas de la practica", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        self.tree = ttk.Treeview(
            left,
            columns=("orden", "id", "numero", "tema", "clave", "consistencia", "dirty"),
            show="headings",
            height=24,
        )
        headings = {
            "orden": "#",
            "id": "ID",
            "numero": "Nro",
            "tema": "Tema",
            "clave": "Clave",
            "consistencia": "Consistencia",
            "dirty": "Editado",
        }
        widths = {
            "orden": 46,
            "id": 70,
            "numero": 62,
            "tema": 210,
            "clave": 58,
            "consistencia": 120,
            "dirty": 72,
        }
        for key in ("orden", "id", "numero", "tema", "clave", "consistencia", "dirty"):
            self.tree.heading(key, text=headings[key])
            self.tree.column(key, width=widths[key], stretch=key == "tema", anchor="center" if key in {"orden", "id", "numero", "clave", "dirty"} else "w")
        self.tree.tag_configure("dirty_row", background="#fff7ed", foreground="#9a3412")
        self.tree.tag_configure("clean_row", background="#ffffff")
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        tree_scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        ttk.Label(right, text="Editor del problema", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        meta = ttk.Frame(right)
        meta.pack(fill="x")
        for col in (1, 3):
            meta.columnconfigure(col, weight=1)

        ttk.Label(meta, text="ID").grid(row=0, column=0, sticky="w")
        ttk.Label(meta, textvariable=self.problem_id_var).grid(row=0, column=1, sticky="w", padx=(8, 12))
        ttk.Label(meta, text="Numero original").grid(row=0, column=2, sticky="w")
        ttk.Entry(meta, textvariable=self.numero_original_var, width=12).grid(row=0, column=3, sticky="ew", padx=(8, 0))

        ttk.Label(meta, text="Curso").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(meta, textvariable=self.curso_var).grid(row=1, column=1, sticky="ew", padx=(8, 12), pady=(8, 0))
        ttk.Label(meta, text="Tema").grid(row=1, column=2, sticky="w", pady=(8, 0))
        ttk.Entry(meta, textvariable=self.tema_var).grid(row=1, column=3, sticky="ew", padx=(8, 0), pady=(8, 0))

        ttk.Label(meta, text="Subtema").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(meta, textvariable=self.subtema_var).grid(row=2, column=1, sticky="ew", padx=(8, 12), pady=(8, 0))
        ttk.Label(meta, text="Clave").grid(row=2, column=2, sticky="w", pady=(8, 0))
        ttk.Entry(meta, textvariable=self.clave_var, width=10).grid(row=2, column=3, sticky="w", padx=(8, 0), pady=(8, 0))

        ttk.Label(meta, text="Consistencia").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(
            meta,
            textvariable=self.consistencia_matematica_var,
            state="readonly",
            values=MATH_CONSISTENCY_VALUES,
        ).grid(row=3, column=1, sticky="ew", padx=(8, 12), pady=(8, 0))

        ttk.Label(meta, text="Libro / Instancia").grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Label(meta, textvariable=self.libro_var).grid(row=4, column=1, sticky="w", padx=(8, 12), pady=(8, 0))
        ttk.Label(meta, text="Instancia").grid(row=4, column=2, sticky="w", pady=(8, 0))
        ttk.Label(meta, textvariable=self.instancia_var).grid(row=4, column=3, sticky="w", padx=(8, 0), pady=(8, 0))

        extra = ttk.Frame(right, style="Card.TFrame", padding=12)
        extra.pack(fill="x", pady=(12, 0))
        extra.columnconfigure(1, weight=1)

        ttk.Label(extra, text="Autor").grid(row=0, column=0, sticky="w")
        ttk.Label(extra, textvariable=self.autor_var).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Label(extra, text="Editorial").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(extra, textvariable=self.editorial_var).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        ttk.Label(extra, text="PDF").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Label(extra, textvariable=self.pdf_var).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        self.open_pdf_btn = ttk.Button(extra, text="Abrir PDF asociado", command=self._open_current_pdf, style="Ghost.TButton")
        self.open_pdf_btn.grid(row=0, column=2, rowspan=3, sticky="e", padx=(12, 0))
        self.open_pdf_btn.configure(state="disabled")

        ttk.Label(right, text="Enunciado LaTeX", style="Section.TLabel").pack(anchor="w", pady=(14, 6))
        self.txt_enunciado = tk.Text(
            right,
            height=22,
            bg=self.palette["surface"],
            fg=self.palette["text"],
            insertbackground=self.palette["text"],
            relief="solid",
            bd=1,
            wrap="word",
            highlightthickness=1,
            highlightbackground=self.palette["border"],
        )
        self.txt_enunciado.pack(fill="both", expand=True)

        log_wrap = ttk.Frame(self)
        log_wrap.pack(fill="both", expand=False, padx=16, pady=(12, 16))
        ttk.Label(log_wrap, text="Log", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        self.txt_log = tk.Text(
            log_wrap,
            height=8,
            bg=self.palette["surface"],
            fg=self.palette["text"],
            insertbackground=self.palette["text"],
            relief="solid",
            bd=1,
            wrap="word",
            highlightthickness=1,
            highlightbackground=self.palette["border"],
        )
        self.txt_log.pack(fill="both", expand=True)

    def _log(self, msg: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.txt_log.insert("end", f"[{stamp}] {msg.rstrip()}\n")
        self.txt_log.see("end")

    def _listar_dbs(self) -> None:
        try:
            dbs = self.local_db_manager.listar_bases_datos()
        except Exception as exc:
            messagebox.showerror("BD local", str(exc))
            return
        if not dbs:
            dbs = [str(self.local_db_manager.db_name)]
        self.combo_db["values"] = dbs
        if self.db_name_var.get() not in dbs:
            self.db_name_var.set(dbs[0])
        self._refresh_pending_count()

    def _pick_json(self) -> None:
        path = filedialog.askopenfilename(
            title="Seleccionar JSON de practica",
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        self._load_json_path(Path(path))

    def _reload_current_json(self) -> None:
        if self._source_json_path is None or not self._source_json_path.exists():
            messagebox.showwarning("Modulo 6", "No hay un JSON cargado actualmente.")
            return
        self._load_json_path(self._source_json_path)

    def _load_json_path(self, path: Path) -> None:
        self._commit_form_to_current_item()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            messagebox.showerror("JSON", f"No se pudo leer el archivo.\n{exc}")
            return
        if not isinstance(payload, dict):
            messagebox.showerror("JSON", "El archivo debe contener un objeto JSON.")
            return

        source_db = str(payload.get("database") or "").strip()
        if source_db:
            self.db_name_var.set(source_db)

        items = self._build_items_from_payload(payload)
        if not items:
            messagebox.showwarning("Modulo 6", "El JSON no contiene problemas editables.")
            return

        self._source_json_path = path
        self._source_payload = payload
        self.json_path_var.set(str(path))
        self._items = items
        self._saved_state_by_id = {
            int(item["id"]): self._capture_editable_state(item)
            for item in self._items
            if int(item.get("id") or 0) > 0
        }
        self._dirty_ids.clear()
        self._current_problem_id = None
        self._render_tree()
        self._refresh_summary()
        self._refresh_pending_count()
        self.status_var.set(f"JSON cargado correctamente: {path.name}")
        self._log(f"JSON cargado: {path} | problemas={len(self._items)}")

    def _build_items_from_payload(self, payload: dict[str, object]) -> list[dict[str, object]]:
        if isinstance(payload.get("items"), list):
            items = [self._normalize_item_dict(row, index=idx + 1) for idx, row in enumerate(payload.get("items") or [])]
            return [item for item in items if item is not None]

        selected_ids = payload.get("selected_ids") or []
        if not isinstance(selected_ids, list):
            return []
        clean_ids: list[int] = []
        seen: set[int] = set()
        for raw_id in selected_ids:
            try:
                pid = int(raw_id)
            except Exception:
                continue
            if pid <= 0 or pid in seen:
                continue
            seen.add(pid)
            clean_ids.append(pid)
        if not clean_ids:
            return []

        db_name = (self.db_name_var.get() or "").strip()
        if not db_name:
            db_name = str(self.local_db_manager.db_name)
            self.db_name_var.set(db_name)

        loaded = self.controller.obtener_problemas_por_ids(db_name, problem_ids=clean_ids)
        refs_raw = payload.get("selected_problem_refs") or []
        refs_by_id: dict[int, dict[str, object]] = {}
        if isinstance(refs_raw, list):
            for row in refs_raw:
                if not isinstance(row, dict):
                    continue
                try:
                    pid = int(row.get("id"))
                except Exception:
                    continue
                refs_by_id[pid] = row

        items: list[dict[str, object]] = []
        loaded_ids = {int(item.get("id") or 0) for item in loaded}
        missing_ids = [pid for pid in clean_ids if pid not in loaded_ids]
        if missing_ids:
            self._log(f"IDs no encontrados en BD local: {missing_ids}")

        for idx, item in enumerate(loaded, start=1):
            pid = int(item.get("id") or 0)
            ref = refs_by_id.get(pid, {})
            merged = dict(item)
            if not str(merged.get("tema") or "").strip():
                merged["tema"] = str(ref.get("tema") or "")
            if not str(merged.get("subtema") or "").strip():
                merged["subtema"] = str(ref.get("subtema") or "")
            if not str(merged.get("curso") or "").strip():
                merged["curso"] = str(ref.get("curso") or "")
            if not str(merged.get("respuesta_correcta") or "").strip():
                merged["respuesta_correcta"] = str(ref.get("respuesta_correcta") or "")
            merged["order"] = idx
            items.append(self._normalize_item_dict(merged, index=idx))
        return [item for item in items if item is not None]

    def _normalize_item_dict(self, row: object, *, index: int) -> dict[str, object] | None:
        if not isinstance(row, dict):
            return None
        try:
            pid = int(row.get("id") or 0)
        except Exception:
            return None
        if pid <= 0:
            return None
        try:
            numero_original = int(row.get("numero_original") or 0)
        except Exception:
            numero_original = 0
        consistencia_matematica = str(row.get("consistencia_matematica") or "").strip() or MATH_CONSISTENCY_VALUES[0]
        if consistencia_matematica not in MATH_CONSISTENCY_VALUES:
            consistencia_matematica = MATH_CONSISTENCY_VALUES[0]
        clave = str(row.get("respuesta_correcta") or "").strip().upper()
        item = {
            "order": int(row.get("order") or index),
            "id": pid,
            "numero_original": max(numero_original, 1),
            "curso": str(row.get("curso") or "").strip(),
            "tema": str(row.get("tema") or "").strip(),
            "subtema": str(row.get("subtema") or "").strip(),
            "respuesta_correcta": clave,
            "consistencia_matematica": consistencia_matematica,
            "enunciado_latex": str(row.get("enunciado_latex") or "").replace("\r\n", "\n").replace("\r", "\n").strip(),
            "autor": str(row.get("autor") or "").strip(),
            "editorial": str(row.get("editorial") or "").strip(),
            "pdf_path": str(row.get("pdf_path") or "").strip(),
            "archivo_origen": str(row.get("archivo_origen") or "").strip(),
            "codigo_instancia": str(row.get("codigo_instancia") or "").strip(),
            "libro_codigo": str(row.get("libro_codigo") or "").strip(),
            "tiene_clave": bool(row.get("tiene_clave")) or bool(clave),
            "tiene_solucion": bool(row.get("tiene_solucion")),
        }
        return item

    def _render_tree(self) -> None:
        current_id = self._current_problem_id
        self.tree.delete(*self.tree.get_children())
        for item in self._items:
            pid = int(item.get("id") or 0)
            values = (
                int(item.get("order") or 0),
                pid,
                int(item.get("numero_original") or 0),
                str(item.get("tema") or "-"),
                str(item.get("respuesta_correcta") or "-"),
                str(item.get("consistencia_matematica") or "-"),
                "Si" if pid in self._dirty_ids else "",
            )
            tag = "dirty_row" if pid in self._dirty_ids else "clean_row"
            self.tree.insert("", "end", iid=str(pid), values=values, tags=(tag,))
        if self._items:
            target_id = None
            if current_id is not None and self.tree.exists(str(current_id)):
                target_id = str(current_id)
            else:
                target_id = str(int(self._items[0]["id"]))
            self.tree.selection_set(target_id)
            self.tree.focus(target_id)
            target_item = self._find_item(int(target_id))
            if target_item is not None:
                self._load_item_to_form(target_item)
        else:
            self._clear_form()

    def _refresh_tree_row(self, problem_id: int) -> None:
        item = self._find_item(problem_id)
        if item is None or not self.tree.exists(str(problem_id)):
            return
        values = (
            int(item.get("order") or 0),
            int(item.get("id") or 0),
            int(item.get("numero_original") or 0),
            str(item.get("tema") or "-"),
            str(item.get("respuesta_correcta") or "-"),
            str(item.get("consistencia_matematica") or "-"),
            "Si" if int(item.get("id") or 0) in self._dirty_ids else "",
        )
        tag = "dirty_row" if int(item.get("id") or 0) in self._dirty_ids else "clean_row"
        self.tree.item(str(problem_id), values=values, tags=(tag,))

    def _on_tree_select(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        next_id = int(selection[0])
        if self._current_problem_id == next_id:
            return
        if not self._commit_form_to_current_item():
            if self._current_problem_id is not None and self.tree.exists(str(self._current_problem_id)):
                self.tree.selection_set(str(self._current_problem_id))
            return
        item = self._find_item(next_id)
        if item is None:
            return
        self._load_item_to_form(item)

    def _load_item_to_form(self, item: dict[str, object]) -> None:
        self._loading_form = True
        self._current_problem_id = int(item.get("id") or 0)
        self.problem_id_var.set(str(item.get("id") or "-"))
        self.numero_original_var.set(str(item.get("numero_original") or ""))
        self.curso_var.set(str(item.get("curso") or ""))
        self.tema_var.set(str(item.get("tema") or ""))
        self.subtema_var.set(str(item.get("subtema") or ""))
        self.clave_var.set(str(item.get("respuesta_correcta") or ""))
        self.consistencia_matematica_var.set(str(item.get("consistencia_matematica") or MATH_CONSISTENCY_VALUES[0]))
        self.autor_var.set(str(item.get("autor") or "-"))
        self.editorial_var.set(str(item.get("editorial") or "-"))
        self.libro_var.set(str(item.get("libro_codigo") or "-"))
        self.instancia_var.set(str(item.get("codigo_instancia") or "-"))
        pdf_path = self._resolve_item_pdf_path(item)
        self.pdf_var.set(str(pdf_path.name if pdf_path is not None else "-"))
        self.open_pdf_btn.configure(state="normal" if pdf_path is not None else "disabled")
        self.txt_enunciado.delete("1.0", "end")
        text = str(item.get("enunciado_latex") or "")
        if text:
            self.txt_enunciado.insert("1.0", text)
        self._loading_form = False
        self.status_var.set(f"Editando problema ID {item.get('id')} (orden {item.get('order')}).")

    def _clear_form(self) -> None:
        self._loading_form = True
        self._current_problem_id = None
        self.problem_id_var.set("-")
        self.numero_original_var.set("")
        self.curso_var.set("")
        self.tema_var.set("")
        self.subtema_var.set("")
        self.clave_var.set("")
        self.consistencia_matematica_var.set(MATH_CONSISTENCY_VALUES[0])
        self.autor_var.set("-")
        self.editorial_var.set("-")
        self.libro_var.set("-")
        self.instancia_var.set("-")
        self.pdf_var.set("-")
        self.open_pdf_btn.configure(state="disabled")
        self.txt_enunciado.delete("1.0", "end")
        self._loading_form = False

    def _capture_form_state(self) -> dict[str, object]:
        raw_numero = (self.numero_original_var.get() or "").strip()
        try:
            numero_original = max(int(raw_numero), 1)
        except Exception:
            raise ValueError("Numero original invalido. Debe ser un entero mayor o igual a 1.")
        consistencia_matematica = (self.consistencia_matematica_var.get() or "").strip() or MATH_CONSISTENCY_VALUES[0]
        if consistencia_matematica not in MATH_CONSISTENCY_VALUES:
            raise ValueError("Consistencia matematica invalida.")
        return {
            "numero_original": numero_original,
            "curso": (self.curso_var.get() or "").strip(),
            "tema": (self.tema_var.get() or "").strip(),
            "subtema": (self.subtema_var.get() or "").strip(),
            "respuesta_correcta": (self.clave_var.get() or "").strip().upper(),
            "consistencia_matematica": consistencia_matematica,
            "enunciado_latex": self.txt_enunciado.get("1.0", "end-1c").replace("\r\n", "\n").replace("\r", "\n").strip(),
        }

    def _capture_editable_state(self, item: dict[str, object]) -> dict[str, object]:
        return {
            "numero_original": int(item.get("numero_original") or 0),
            "curso": str(item.get("curso") or "").strip(),
            "tema": str(item.get("tema") or "").strip(),
            "subtema": str(item.get("subtema") or "").strip(),
            "respuesta_correcta": str(item.get("respuesta_correcta") or "").strip().upper(),
            "consistencia_matematica": str(item.get("consistencia_matematica") or "").strip() or MATH_CONSISTENCY_VALUES[0],
            "enunciado_latex": str(item.get("enunciado_latex") or "").replace("\r\n", "\n").replace("\r", "\n").strip(),
        }

    def _commit_form_to_current_item(self) -> bool:
        if self._loading_form or self._current_problem_id is None:
            return True
        item = self._find_item(self._current_problem_id)
        if item is None:
            return True
        try:
            new_state = self._capture_form_state()
        except ValueError as exc:
            messagebox.showwarning("Modulo 6", str(exc))
            return False
        for key, value in new_state.items():
            item[key] = value
        item["tiene_clave"] = bool(str(item.get("respuesta_correcta") or "").strip())
        saved_state = self._saved_state_by_id.get(self._current_problem_id, {})
        if new_state != saved_state:
            self._dirty_ids.add(self._current_problem_id)
        else:
            self._dirty_ids.discard(self._current_problem_id)
        self._refresh_tree_row(self._current_problem_id)
        self._refresh_summary()
        return True

    def _find_item(self, problem_id: int) -> dict[str, object] | None:
        for item in self._items:
            if int(item.get("id") or 0) == int(problem_id):
                return item
        return None

    def _resolve_item_pdf_path(self, item: dict[str, object] | None) -> Path | None:
        if not isinstance(item, dict):
            return None
        for raw in (str(item.get("pdf_path") or "").strip(), str(item.get("archivo_origen") or "").strip()):
            if not raw:
                continue
            path = remap_legacy_drive_path(Path(raw), prefer_existing=True)
            if path.suffix.lower() != ".pdf":
                continue
            if path.exists():
                return path
        return None

    def _open_current_pdf(self) -> None:
        item = self._find_item(self._current_problem_id or 0)
        pdf_path = self._resolve_item_pdf_path(item)
        if pdf_path is None:
            messagebox.showwarning("Modulo 6", "El problema actual no tiene un PDF accesible.")
            return
        try:
            os.startfile(str(pdf_path))  # type: ignore[attr-defined]
        except Exception:
            try:
                subprocess.Popen(["cmd", "/c", "start", "", str(pdf_path)])
            except Exception as exc:
                messagebox.showerror("Modulo 6", f"No se pudo abrir el PDF.\n{exc}")

    def _refresh_summary(self) -> None:
        total = len(self._items)
        dirty = len(self._dirty_ids)
        self.summary_var.set(f"Problemas cargados: {total} | Cambios locales sin guardar en BD: {dirty}")

    def _refresh_pending_count(self) -> None:
        db_name = (self.db_name_var.get() or "").strip()
        if not db_name:
            self.pending_var.set("Cola local pendiente: -")
            return
        try:
            pending = self.queue_controller.pending_count(db_name)
        except Exception:
            self.pending_var.set("Cola local pendiente: error")
            return
        self.pending_var.set(f"Cola local pendiente: {pending}")

    def _snapshot_payload(self) -> dict[str, object]:
        self._commit_form_to_current_item()
        db_name = (self.db_name_var.get() or "").strip()
        metadata_keys = (
            "generated_at",
            "filters",
            "merged_sources",
            "merge_deduplicated",
            "merge_note",
            "selected_problem_refs",
        )
        source_metadata = {key: copy.deepcopy(self._source_payload.get(key)) for key in metadata_keys if key in self._source_payload}
        return {
            "editor_schema": "practice_editor_snapshot_v1",
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "database": db_name,
            "source_json_path": str(self._source_json_path) if self._source_json_path is not None else "",
            "source_metadata": source_metadata,
            "selected_count": len(self._items),
            "selected_ids": [int(item.get("id") or 0) for item in self._items if int(item.get("id") or 0) > 0],
            "items": [
                {
                    "order": int(item.get("order") or 0),
                    "id": int(item.get("id") or 0),
                    "numero_original": int(item.get("numero_original") or 0),
                    "curso": str(item.get("curso") or ""),
                    "tema": str(item.get("tema") or ""),
                    "subtema": str(item.get("subtema") or ""),
                    "respuesta_correcta": str(item.get("respuesta_correcta") or ""),
                    "consistencia_matematica": str(item.get("consistencia_matematica") or ""),
                    "enunciado_latex": str(item.get("enunciado_latex") or ""),
                    "autor": str(item.get("autor") or ""),
                    "editorial": str(item.get("editorial") or ""),
                    "pdf_path": str(item.get("pdf_path") or ""),
                    "archivo_origen": str(item.get("archivo_origen") or ""),
                    "codigo_instancia": str(item.get("codigo_instancia") or ""),
                    "libro_codigo": str(item.get("libro_codigo") or ""),
                    "tiene_clave": bool(item.get("tiene_clave")),
                    "tiene_solucion": bool(item.get("tiene_solucion")),
                }
                for item in self._items
            ],
        }

    def _default_snapshot_path(self) -> Path:
        if self._source_json_path is not None:
            return self._source_json_path.with_name(f"{self._source_json_path.stem}__editado.json")
        return Path.cwd() / "practica__editado.json"

    def _reference_json_path_for_tex(self) -> Path:
        source_hint = str(self._source_payload.get("source_json_path") or "").strip()
        if source_hint:
            hinted_path = Path(source_hint)
            if hinted_path.suffix.lower() == ".json":
                return hinted_path
        if self._source_json_path is not None:
            return self._source_json_path
        raw = (self.json_path_var.get() or "").strip()
        if raw:
            candidate = Path(raw)
            if candidate.suffix.lower() == ".json":
                return candidate
        return self._default_snapshot_path()

    def _default_tex_path(self) -> Path:
        base_json = self._reference_json_path_for_tex()
        stem = base_json.stem
        if stem.endswith("__ids_problemas__editado"):
            stem = stem[: -len("__ids_problemas__editado")] + "__db_source"
        elif stem.endswith("__ids_problemas"):
            stem = stem[: -len("__ids_problemas")] + "__db_source"
        elif stem.endswith("__editado"):
            stem = stem[: -len("__editado")] + "__db_source"
        elif stem.endswith("__db_source"):
            stem = stem
        else:
            stem = f"{stem}__db_source"
        return base_json.with_name(f"{stem}.tex")

    def _save_snapshot(self, *, target_path: Path | None = None, silent: bool = False) -> Path | None:
        if not self._items:
            messagebox.showwarning("Modulo 6", "No hay una practica cargada para guardar.")
            return None
        payload = self._snapshot_payload()
        target = target_path
        if target is None:
            raw = filedialog.asksaveasfilename(
                title="Guardar snapshot JSON",
                defaultextension=".json",
                initialfile=self._default_snapshot_path().name,
                filetypes=[("JSON", "*.json")],
            )
            if not raw:
                return None
            target = Path(raw)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if not silent:
            self._log(f"Snapshot JSON guardado: {target}")
            self.status_var.set(f"Snapshot guardado: {target.name}")
        return target

    def _update_tex_file(self) -> None:
        if not self._items:
            messagebox.showwarning("Modulo 6", "No hay una practica cargada para exportar a TEX.")
            return
        if not self._commit_form_to_current_item():
            return

        target = self._default_tex_path()
        try:
            self.controller.exportar_fuente_latex_para_word(output_path=target, problemas=self._items)
        except Exception as exc:
            messagebox.showerror("Modulo 6", f"No se pudo actualizar el archivo TEX.\n{exc}")
            return

        snapshot_target = self._source_json_path if self._overwrite_source_json_on_update and self._source_json_path is not None else self._default_snapshot_path()
        self._save_snapshot(target_path=snapshot_target, silent=True)
        self.status_var.set(f"TEX actualizado: {target.name}")
        self._log(
            f"TEX fuente actualizado: {target} | problemas={len(self._items)} | "
            f"cambios_locales={len(self._dirty_ids)} | snapshot={snapshot_target}"
        )
        messagebox.showinfo(
            "Modulo 6",
            f"Archivo TEX actualizado correctamente.\n\n{target}\n\n"
            "Ya puedes usarlo en el flujo de conversion a Word.",
        )

    def _reload_items_from_db(self) -> None:
        if not self._items:
            messagebox.showwarning("Modulo 6", "Primero carga un JSON de seleccion.")
            return
        if self._dirty_ids:
            proceed = messagebox.askyesno(
                "Modulo 6",
                "Hay cambios locales sin guardar en BD. Si recargas desde la BD se perderan.\n\nContinuar?",
            )
            if not proceed:
                return
        db_name = (self.db_name_var.get() or "").strip()
        ids = [int(item.get("id") or 0) for item in self._items if int(item.get("id") or 0) > 0]
        fresh = self.controller.obtener_problemas_por_ids(db_name, problem_ids=ids)
        if not fresh:
            messagebox.showwarning("Modulo 6", "No se pudieron recargar problemas desde la BD.")
            return
        for idx, item in enumerate(fresh, start=1):
            item["order"] = idx
        self._items = [self._normalize_item_dict(item, index=idx + 1) for idx, item in enumerate(fresh)]
        self._items = [item for item in self._items if item is not None]
        self._saved_state_by_id = {
            int(item["id"]): self._capture_editable_state(item)
            for item in self._items
            if int(item.get("id") or 0) > 0
        }
        self._dirty_ids.clear()
        self._current_problem_id = None
        self._render_tree()
        self._refresh_summary()
        self._refresh_pending_count()
        self._log("Problemas recargados desde la BD local.")

    def _save_to_db(self) -> None:
        if not self._items:
            messagebox.showwarning("Modulo 6", "No hay practica cargada.")
            return
        if not self._commit_form_to_current_item():
            return
        if not self._dirty_ids:
            messagebox.showinfo("Modulo 6", "No hay cambios nuevos para guardar en la BD.")
            return
        db_name = (self.db_name_var.get() or "").strip()
        if not db_name:
            messagebox.showwarning("Modulo 6", "Selecciona la base local.")
            return

        try:
            self.queue_controller.ensure_local_queue(db_name)
        except Exception as exc:
            messagebox.showerror("Modulo 6", f"No se pudo preparar la cola local.\n{exc}")
            return

        dirty_ids = [int(item.get("id") or 0) for item in self._items if int(item.get("id") or 0) in self._dirty_ids]
        updated_count = 0
        for pid in dirty_ids:
            item = self._find_item(pid)
            if item is None:
                continue
            updated = self.controller.actualizar_problema_desde_editor(
                db_name,
                problem_id=pid,
                numero_original=int(item.get("numero_original") or 0),
                enunciado_latex=str(item.get("enunciado_latex") or ""),
                respuesta_correcta=str(item.get("respuesta_correcta") or ""),
                curso=str(item.get("curso") or ""),
                tema=str(item.get("tema") or ""),
                subtema=str(item.get("subtema") or ""),
                consistencia_matematica=str(item.get("consistencia_matematica") or MATH_CONSISTENCY_VALUES[0]),
            )
            updated["order"] = int(item.get("order") or 0)
            idx = self._items.index(item)
            self._items[idx] = updated
            self._saved_state_by_id[pid] = self._capture_editable_state(updated)
            self._dirty_ids.discard(pid)
            updated_count += 1

        self._render_tree()
        self._refresh_summary()
        self._refresh_pending_count()
        snapshot_target = self._source_json_path if self._overwrite_source_json_on_update and self._source_json_path is not None else self._default_snapshot_path()
        self._save_snapshot(target_path=snapshot_target, silent=True)
        self.status_var.set(f"Cambios guardados en BD local: {updated_count} problema(s).")
        self._log(
            f"Guardado en BD local completado: actualizados={updated_count} | snapshot={snapshot_target}"
        )
        messagebox.showinfo(
            "Modulo 6",
            f"Se guardaron {updated_count} problema(s) en la BD local.\n\n"
            "Los cambios ya quedaron listos para el modulo Publicar Cambios.",
        )

    def _on_close(self) -> None:
        self._commit_form_to_current_item()
        if self._dirty_ids:
            proceed = messagebox.askyesno(
                "Modulo 6",
                "Hay cambios locales sin guardar en la BD.\n\nCerrar de todas formas?",
            )
            if not proceed:
                return
        self.destroy()
