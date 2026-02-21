from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .controlador_consulta import ConsultaController


class ConsultaWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Modulo 5 - Consulta (Vector Search)")
        self.geometry("1180x720")
        self.minsize(1040, 660)
        self._maximize_window()

        self.controller = ConsultaController()

        self.db_name_var = tk.StringVar(value="")
        self.estado_filter_var = tk.StringVar(value="Todos")
        self.area_filter_var = tk.StringVar(value="Todos")
        self.archivo_filter_var = tk.StringVar(value="Todos")
        self.model_var = tk.StringVar(value="text-embedding-3-small")
        self.topk_var = tk.IntVar(value=10)

        self._apply_light_theme()
        self._build_ui()
        self._listar_dbs()

    def _apply_light_theme(self) -> None:
        self.configure(bg="#f8fafc")
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background="#f8fafc", foreground="#0f172a")
        style.configure("TFrame", background="#f8fafc")
        style.configure("TLabel", background="#f8fafc", foreground="#0f172a")
        style.configure("TButton", background="#2563eb", foreground="#ffffff")
        style.map("TButton", background=[("active", "#1d4ed8")])
        style.configure("TEntry", fieldbackground="#ffffff", foreground="#0f172a")
        style.configure("TCombobox", fieldbackground="#ffffff", foreground="#0f172a")

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
        header = ttk.Label(self, text="Modulo 5 - Consulta por similitud (embeddings)", font=("Segoe UI", 14, "bold"))
        header.pack(anchor="w", padx=16, pady=(14, 6))

        cfg = ttk.Frame(self)
        cfg.pack(fill="x", padx=16)

        ttk.Label(cfg, text="Base de datos").grid(row=0, column=0, sticky="w")
        self.combo_db = ttk.Combobox(cfg, state="readonly", textvariable=self.db_name_var, values=[])
        self.combo_db.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(cfg, text="Refrescar", command=self._listar_dbs).grid(row=0, column=2, sticky="ew")
        self.combo_db.bind("<<ComboboxSelected>>", lambda _e: self._refrescar_filtros())

        ttk.Label(cfg, text="Estado").grid(row=0, column=3, sticky="w", padx=(12, 0))
        ttk.Combobox(
            cfg,
            state="readonly",
            textvariable=self.estado_filter_var,
            values=["Todos", "Pendiente Revision", "Bien Planteado", "Mal Planteado"],
            width=18,
        ).grid(row=0, column=4, sticky="w")

        ttk.Label(cfg, text="Area").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.combo_area = ttk.Combobox(cfg, textvariable=self.area_filter_var, values=[])
        self.combo_area.grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=(10, 0))

        ttk.Label(cfg, text="Archivo").grid(row=1, column=2, sticky="w", pady=(10, 0))
        self.combo_archivo = ttk.Combobox(cfg, textvariable=self.archivo_filter_var, values=[])
        self.combo_archivo.grid(row=1, column=3, sticky="ew", padx=(8, 8), pady=(10, 0))

        ttk.Label(cfg, text="Top K").grid(row=1, column=4, sticky="w", pady=(10, 0))
        ttk.Spinbox(cfg, from_=1, to=100, textvariable=self.topk_var, width=8).grid(
            row=1, column=5, sticky="w", pady=(10, 0)
        )

        ttk.Label(cfg, text="Modelo").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Combobox(
            cfg,
            state="readonly",
            textvariable=self.model_var,
            values=["text-embedding-3-small", "text-embedding-3-large"],
            width=24,
        ).grid(row=2, column=1, sticky="w", padx=(8, 8), pady=(10, 0))
        ttk.Button(cfg, text="Buscar", command=self._buscar_async).grid(row=2, column=2, sticky="ew", pady=(10, 0))

        cfg.columnconfigure(1, weight=1)
        cfg.columnconfigure(3, weight=1)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=16, pady=(14, 16))

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=False)
        ttk.Label(left, text="Consulta").pack(anchor="w")
        self.txt_query = tk.Text(
            left,
            height=10,
            width=44,
            bg="#ffffff",
            fg="#0f172a",
            insertbackground="#0f172a",
            relief="solid",
            bd=1,
            wrap="word",
        )
        self.txt_query.pack(fill="x", pady=(6, 10))
        ttk.Label(left, text="Resultados").pack(anchor="w")
        self.list_results = tk.Listbox(
            left,
            width=44,
            bg="#ffffff",
            fg="#0f172a",
            selectbackground="#bfdbfe",
            selectforeground="#0f172a",
            highlightthickness=1,
            highlightbackground="#cbd5e1",
        )
        self.list_results.pack(fill="both", expand=True, pady=(6, 0))
        self.list_results.bind("<<ListboxSelect>>", lambda _e: self._on_select_result())

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(16, 0))
        ttk.Label(right, text="Detalle").pack(anchor="w")
        self.txt_detail = tk.Text(
            right,
            bg="#ffffff",
            fg="#0f172a",
            insertbackground="#0f172a",
            relief="solid",
            bd=1,
            wrap="word",
        )
        self.txt_detail.pack(fill="both", expand=True, pady=(6, 0))

        self._results = []

    def _listar_dbs(self) -> None:
        dbs = self.controller.listar_bases_datos()
        self.combo_db["values"] = dbs
        if self.db_name_var.get() in dbs:
            self._refrescar_filtros()
            return
        if dbs:
            self.db_name_var.set(dbs[0])
            self._refrescar_filtros()

    def _refrescar_filtros(self) -> None:
        db = (self.db_name_var.get() or "").strip()
        if not db:
            return
        try:
            archivos = self.controller.listar_archivos_origen(db)
        except Exception:
            archivos = []
        self._archivo_label_to_value = {"Todos": ""}
        values = ["Todos"] + [f"{a} ({c})" for a, c in archivos]
        for a, c in archivos:
            self._archivo_label_to_value[f"{a} ({c})"] = a
        self.combo_archivo["values"] = values
        if (self.archivo_filter_var.get() or "").strip() not in values:
            self.archivo_filter_var.set("Todos")

        try:
            areas = self.controller.listar_areas(db)
        except Exception:
            areas = []
        area_values = ["Todos"] + areas
        self.combo_area["values"] = area_values
        if (self.area_filter_var.get() or "").strip() not in area_values:
            self.area_filter_var.set("Todos")

    def _buscar_async(self) -> None:
        db = (self.db_name_var.get() or "").strip()
        if not db:
            messagebox.showwarning("BD", "Selecciona una base de datos.")
            return
        query = (self.txt_query.get("1.0", "end") or "").strip()
        if not query:
            messagebox.showwarning("Consulta", "Escribe una consulta.")
            return
        top_k = int(self.topk_var.get() or 10)
        model = (self.model_var.get() or "text-embedding-3-small").strip()
        estado = self.estado_filter_var.get()
        area = self.area_filter_var.get()
        archivo_label = (self.archivo_filter_var.get() or "").strip()
        archivo = getattr(self, "_archivo_label_to_value", {}).get(archivo_label, archivo_label)

        def worker():
            try:
                rows = self.controller.buscar_similares(
                    db,
                    query_text=query,
                    top_k=top_k,
                    archivo_origen=archivo,
                    estado_filtro=estado,
                    area_filtro=area,
                    model=model,
                )
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Buscar", str(exc)))
                return

            def done():
                self._results = rows
                self.list_results.delete(0, "end")
                for r in rows:
                    dist = r.get("distance")
                    d = f"{dist:.4f}" if isinstance(dist, float) else "?"
                    label = f"{r['id']} | d={d} | {r.get('archivo_origen','')} | {r.get('area','')} / {r.get('tema','')}"
                    self.list_results.insert("end", label)
                if rows:
                    self.list_results.selection_clear(0, "end")
                    self.list_results.selection_set(0)
                    self._on_select_result()
                else:
                    self.txt_detail.delete("1.0", "end")
                    self.txt_detail.insert("end", "Sin resultados. Asegura tener embeddings generados (Modulo 4).")

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_select_result(self) -> None:
        sel = self.list_results.curselection()
        if not sel:
            return
        idx = int(sel[0])
        if idx < 0 or idx >= len(self._results):
            return
        r = self._results[idx]
        db = (self.db_name_var.get() or "").strip()
        if not db:
            return
        pid = int(r["id"])
        try:
            detail = self.controller.obtener_detalle_problema(db, problema_id=pid)
        except Exception as exc:
            messagebox.showerror("Detalle", str(exc))
            return
        self.txt_detail.delete("1.0", "end")
        if not detail:
            self.txt_detail.insert("end", "No encontrado.")
            return
        lines = [
            f"ID: {detail.get('id')}",
            f"Archivo: {detail.get('archivo_origen')}",
            f"Estado: {detail.get('estado_consistencia')}",
            f"Area/Tema: {detail.get('area')} / {detail.get('tema')} (tema_id={detail.get('tema_id')})",
            f"Dificultad: {detail.get('nivel_dificultad')}",
            f"Respuesta: {detail.get('respuesta_correcta')}",
            "",
            "ENUNCIADO:",
            detail.get("enunciado_latex") or "",
            "",
        ]
        if detail.get("razon_inconsistencia"):
            lines += ["RAZON_INCONSISTENCIA:", detail.get("razon_inconsistencia") or "", ""]
        sols = detail.get("soluciones") or []
        if sols:
            lines.append("SOLUCIONES:")
            for s in sols:
                lines.append(f"- Solucion {s.get('orden')}: {s.get('metodo_nombre')} ({s.get('autor_ia')})")
                props = s.get("propiedades") or []
                if props:
                    lines.append("  Propiedades: " + ", ".join([f"{pid}:{name}" for pid, name in props]))
                lines.append(s.get("solucion_latex") or "")
                lines.append("")
        else:
            lines.append("SOLUCIONES: (ninguna)")

        self.txt_detail.insert("end", "\n".join(lines).strip() + "\n")
