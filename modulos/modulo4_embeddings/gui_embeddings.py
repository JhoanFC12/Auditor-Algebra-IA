from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import List, Tuple

from openai import OpenAI

from .controlador_embeddings import EmbeddingController


class EmbeddingWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Modulo 4 - Embeddings")
        self.geometry("980x640")
        self.minsize(920, 600)
        self._maximize_window()

        self.controller = EmbeddingController()

        self.db_name_var = tk.StringVar(value="")
        self.estado_filter_var = tk.StringVar(value="Todos")
        self.archivo_filter_var = tk.StringVar(value="Todos")
        self.solo_sin_var = tk.BooleanVar(value=True)
        self.batch_var = tk.IntVar(value=50)
        self.model_var = tk.StringVar(value="text-embedding-3-small")

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
        style.configure("Horizontal.TProgressbar", troughcolor="#e2e8f0", background="#2563eb")

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
        header = ttk.Label(self, text="Modulo 4 - Embeddings (pgvector)", font=("Segoe UI", 14, "bold"))
        header.pack(anchor="w", padx=16, pady=(14, 6))

        cfg = ttk.Frame(self)
        cfg.pack(fill="x", padx=16)

        ttk.Label(cfg, text="Base de datos").grid(row=0, column=0, sticky="w")
        self.combo_db = ttk.Combobox(cfg, state="readonly", textvariable=self.db_name_var, values=[])
        self.combo_db.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(cfg, text="Refrescar", command=self._listar_dbs).grid(row=0, column=2, sticky="ew")
        self.combo_db.bind("<<ComboboxSelected>>", lambda _e: self._refrescar_archivos())

        ttk.Label(cfg, text="Estado").grid(row=0, column=3, sticky="w", padx=(12, 0))
        self.combo_estado = ttk.Combobox(
            cfg,
            state="readonly",
            textvariable=self.estado_filter_var,
            values=["Todos", "Pendiente Revision", "Bien Planteado", "Mal Planteado"],
            width=18,
        )
        self.combo_estado.grid(row=0, column=4, sticky="w")
        self.combo_estado.bind("<<ComboboxSelected>>", lambda _e: self._refrescar_archivos())

        ttk.Label(cfg, text="Archivo").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.combo_archivo = ttk.Combobox(cfg, textvariable=self.archivo_filter_var, values=[])
        self.combo_archivo.grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=(10, 0))
        ttk.Button(cfg, text="Archivos", command=self._refrescar_archivos).grid(
            row=1, column=2, sticky="ew", pady=(10, 0)
        )

        ttk.Checkbutton(cfg, text="Solo sin embedding", variable=self.solo_sin_var, command=self._refrescar_archivos).grid(
            row=1, column=3, sticky="w", padx=(12, 0), pady=(10, 0)
        )

        ttk.Label(cfg, text="Modelo").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Combobox(
            cfg,
            state="readonly",
            textvariable=self.model_var,
            values=["text-embedding-3-small", "text-embedding-3-large"],
            width=24,
        ).grid(row=2, column=1, sticky="w", padx=(8, 8), pady=(10, 0))

        ttk.Label(cfg, text="Batch").grid(row=2, column=2, sticky="w", pady=(10, 0))
        ttk.Spinbox(cfg, from_=1, to=200, textvariable=self.batch_var, width=8).grid(
            row=2, column=3, sticky="w", pady=(10, 0)
        )
        ttk.Button(cfg, text="Procesar embeddings", command=self._procesar_async).grid(
            row=2, column=4, sticky="ew", padx=(8, 0), pady=(10, 0)
        )

        cfg.columnconfigure(1, weight=1)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=16, pady=(14, 16))

        self.txt_log = tk.Text(
            body,
            bg="#ffffff",
            fg="#0f172a",
            insertbackground="#0f172a",
            relief="solid",
            bd=1,
            wrap="word",
        )
        self.txt_log.pack(fill="both", expand=True)
        self.progress = ttk.Progressbar(body, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(10, 0))

    def _log(self, msg: str) -> None:
        self.txt_log.insert("end", msg + "\n")
        self.txt_log.see("end")

    def _listar_dbs(self) -> None:
        dbs = self.controller.listar_bases_datos()
        self.combo_db["values"] = dbs
        if self.db_name_var.get() in dbs:
            self._refrescar_archivos()
            return
        if dbs:
            self.db_name_var.set(dbs[0])
            self._refrescar_archivos()

    def _refrescar_archivos(self) -> None:
        db = (self.db_name_var.get() or "").strip()
        if not db:
            return
        try:
            items = self.controller.listar_archivos_origen(
                db,
                estado_filtro=self.estado_filter_var.get(),
                solo_sin_embedding=bool(self.solo_sin_var.get()),
            )
        except Exception as exc:
            self._log(f"Error listando archivos: {exc}")
            items = []

        self._archivo_label_to_value = {"Todos": ""}
        values = ["Todos"]
        for nombre, count in items:
            label = f"{nombre} ({count})"
            values.append(label)
            self._archivo_label_to_value[label] = nombre
        self.combo_archivo["values"] = values
        if (self.archivo_filter_var.get() or "").strip() not in values:
            self.archivo_filter_var.set("Todos")

    def _procesar_async(self) -> None:
        db = (self.db_name_var.get() or "").strip()
        if not db:
            messagebox.showwarning("BD", "Selecciona una base de datos.")
            return

        try:
            batch = int(self.batch_var.get())
        except Exception:
            batch = 50
        batch = max(1, min(batch, 200))

        estado = self.estado_filter_var.get()
        archivo_label = (self.archivo_filter_var.get() or "").strip()
        archivo = getattr(self, "_archivo_label_to_value", {}).get(archivo_label, archivo_label)
        solo_sin = bool(self.solo_sin_var.get())
        model = (self.model_var.get() or "text-embedding-3-small").strip()

        def worker():
            try:
                client = OpenAI()
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("OpenAI", str(exc)))
                return

            processed = 0
            while True:
                try:
                    problemas = self.controller.obtener_problemas_para_embedding(
                        db,
                        limit=batch,
                        archivo_origen=archivo,
                        estado_filtro=estado,
                        solo_sin_embedding=solo_sin,
                    )
                except Exception as exc:
                    self.after(0, lambda: self._log(f"Error obteniendo problemas: {exc}"))
                    return

                if not problemas:
                    break

                ids = [pid for pid, _ in problemas]
                texts = [latex for _, latex in problemas]
                try:
                    resp = client.embeddings.create(model=model, input=texts)
                    embs = [d.embedding for d in resp.data]
                except Exception as exc:
                    self.after(0, lambda: self._log(f"Error embeddings: {exc}"))
                    return

                try:
                    self.controller.guardar_embeddings(db, items=list(zip(ids, embs)))
                except Exception as exc:
                    self.after(0, lambda: self._log(f"Error guardando embeddings: {exc}"))
                    return

                processed += len(ids)
                self.after(0, lambda n=processed: self._log(f"Embeddings guardados: {n}"))
                self.after(0, lambda: self.progress.configure(value=min(100, (processed % 1000) / 10)))

            def done():
                self.progress.configure(value=100)
                self._log("Completado.")
                messagebox.showinfo("Embeddings", f"Completado. Total procesados: {processed}")
                self._refrescar_archivos()

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()
