from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .controlador_practicas import PracticeBuilderController
from utils.styles import apply_openai_theme


class PracticeBuilderWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Modulo 6 - Generador de Practicas")
        self.geometry("980x640")
        self.minsize(900, 600)
        self._maximize_window()

        self.controller = PracticeBuilderController()

        self.db_name_var = tk.StringVar(value="")
        self.curso_var = tk.StringVar(value="Todos")
        self.tema_var = tk.StringVar(value="Todos")
        self.subtema_var = tk.StringVar(value="Todos")
        self.estado_var = tk.StringVar(value="Todos")
        self.cantidad_var = tk.IntVar(value=20)
        self.titulo_var = tk.StringVar(value="Practica")
        self.incluir_clave_inline_var = tk.BooleanVar(value=False)
        self.incluir_clave_final_var = tk.BooleanVar(value=True)
        self.aleatorio_var = tk.BooleanVar(value=True)

        self._tema_label_to_id: dict[str, int] = {}
        self._subtema_label_to_id: dict[str, int] = {}

        self._apply_light_theme()
        self._build_ui()
        self._listar_dbs()

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
        ttk.Label(header_wrap, text="Modulo 6 - Generador de Practicas", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            header_wrap,
            text="Filtra por curso/tema/subtema y exporta practica en Word con clave.",
            style="SubHeader.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        top_card = ttk.Frame(self, style="Card.TFrame", padding=14)
        top_card.pack(fill="x", padx=16, pady=(8, 0))

        top = ttk.Frame(top_card)
        top.pack(fill="x")

        ttk.Label(top, text="Base de datos").grid(row=0, column=0, sticky="w")
        self.combo_db = ttk.Combobox(top, state="readonly", textvariable=self.db_name_var, values=[])
        self.combo_db.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        self.combo_db.bind("<<ComboboxSelected>>", lambda _e: self._on_db_change())
        ttk.Button(top, text="Refrescar", command=self._listar_dbs, style="Ghost.TButton").grid(row=0, column=2, sticky="ew")

        ttk.Label(top, text="Curso").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.combo_curso = ttk.Combobox(top, textvariable=self.curso_var, values=["Todos"])
        self.combo_curso.grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=(10, 0))
        self.combo_curso.bind("<<ComboboxSelected>>", lambda _e: self._on_curso_change())

        ttk.Label(top, text="Tema").grid(row=1, column=2, sticky="w", pady=(10, 0))
        self.combo_tema = ttk.Combobox(top, textvariable=self.tema_var, values=["Todos"])
        self.combo_tema.grid(row=1, column=3, sticky="ew", padx=(8, 8), pady=(10, 0))
        self.combo_tema.bind("<<ComboboxSelected>>", lambda _e: self._on_tema_change())

        ttk.Label(top, text="Subtema").grid(row=1, column=4, sticky="w", pady=(10, 0))
        self.combo_subtema = ttk.Combobox(top, textvariable=self.subtema_var, values=["Todos"])
        self.combo_subtema.grid(row=1, column=5, sticky="ew", padx=(8, 0), pady=(10, 0))
        self.combo_subtema.bind("<<ComboboxSelected>>", lambda _e: self._refresh_count())

        ttk.Label(top, text="Estado").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Combobox(
            top,
            textvariable=self.estado_var,
            state="readonly",
            values=["Todos", "Pendiente Revision", "Bien Planteado", "Mal Planteado"],
        ).grid(row=2, column=1, sticky="ew", padx=(8, 8), pady=(10, 0))

        ttk.Label(top, text="Cantidad").grid(row=2, column=2, sticky="w", pady=(10, 0))
        ttk.Spinbox(top, from_=1, to=300, textvariable=self.cantidad_var, width=8).grid(
            row=2, column=3, sticky="w", padx=(8, 8), pady=(10, 0)
        )

        ttk.Checkbutton(top, text="Seleccion aleatoria", variable=self.aleatorio_var).grid(
            row=2, column=4, sticky="w", pady=(10, 0)
        )

        ttk.Label(top, text="Titulo de la practica").grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(top, textvariable=self.titulo_var).grid(row=3, column=1, columnspan=5, sticky="ew", padx=(8, 0), pady=(10, 0))

        top.columnconfigure(1, weight=1)
        top.columnconfigure(3, weight=1)
        top.columnconfigure(5, weight=1)

        opts = ttk.Frame(self, style="Card.TFrame", padding=(14, 10))
        opts.pack(fill="x", padx=16, pady=(12, 0))
        ttk.Checkbutton(opts, text="Mostrar clave al lado de cada problema", variable=self.incluir_clave_inline_var).pack(
            side="left"
        )
        ttk.Checkbutton(opts, text="Agregar hoja final de clave", variable=self.incluir_clave_final_var).pack(
            side="left", padx=(18, 0)
        )

        self.lbl_count = ttk.Label(self, text="Problemas disponibles: 0", style="Section.TLabel")
        self.lbl_count.pack(anchor="w", padx=16, pady=(12, 0))

        actions = ttk.Frame(self)
        actions.pack(fill="x", padx=16, pady=(14, 0))
        ttk.Button(actions, text="Recalcular disponibilidad", command=self._refresh_count, style="Ghost.TButton").pack(side="left")
        ttk.Button(actions, text="Generar Word", command=self._generar_word, style="Accent.TButton").pack(side="right")

        self.txt_log = tk.Text(
            self,
            height=14,
            bg=self.palette["surface"],
            fg=self.palette["text"],
            insertbackground=self.palette["text"],
            relief="solid",
            bd=1,
            wrap="word",
            highlightthickness=1,
            highlightbackground=self.palette["border"],
        )
        self.txt_log.pack(fill="both", expand=True, padx=16, pady=(12, 16))

    def _log(self, msg: str) -> None:
        self.txt_log.insert("end", msg.rstrip() + "\n")
        self.txt_log.see("end")

    def _listar_dbs(self) -> None:
        try:
            dbs = self.controller.listar_bases_datos()
        except Exception as exc:
            messagebox.showerror("BD", str(exc))
            return
        self.combo_db["values"] = dbs
        if dbs and self.db_name_var.get() not in dbs:
            self.db_name_var.set(dbs[0])
        self._on_db_change()

    def _on_db_change(self) -> None:
        db = (self.db_name_var.get() or "").strip()
        if not db:
            return
        try:
            cursos = self.controller.listar_cursos(db)
            self.combo_curso["values"] = ["Todos"] + cursos
            if self.curso_var.get() not in self.combo_curso["values"]:
                self.curso_var.set("Todos")
            self._cargar_temas()
            self._refresh_count()
        except Exception as exc:
            messagebox.showerror("BD", str(exc))

    def _on_curso_change(self) -> None:
        self._cargar_temas()
        self._refresh_count()

    def _on_tema_change(self) -> None:
        self._cargar_subtemas()
        self._refresh_count()

    def _cargar_temas(self) -> None:
        db = (self.db_name_var.get() or "").strip()
        if not db:
            return
        curso = (self.curso_var.get() or "").strip()
        if curso == "Todos":
            curso = ""
        temas = self.controller.listar_temas(db, curso=curso)
        self._tema_label_to_id = {}
        values = ["Todos"]
        for t in temas:
            nombre = str(t.get("nombre") or "").strip()
            area = str(t.get("curso") or "").strip()
            if not nombre:
                continue
            label = f"{area} / {nombre}" if area else nombre
            values.append(label)
            self._tema_label_to_id[label] = int(t["id"])
        self.combo_tema["values"] = values
        if self.tema_var.get() not in values:
            self.tema_var.set("Todos")
        self._cargar_subtemas()

    def _cargar_subtemas(self) -> None:
        db = (self.db_name_var.get() or "").strip()
        if not db:
            return
        tema_id = self._tema_label_to_id.get((self.tema_var.get() or "").strip())
        subtemas = self.controller.listar_subtemas(db, tema_id=tema_id)
        self._subtema_label_to_id = {}
        values = ["Todos"]
        for s in subtemas:
            nombre = str(s.get("nombre") or "").strip()
            if not nombre:
                continue
            values.append(nombre)
            self._subtema_label_to_id[nombre] = int(s["id"])
        self.combo_subtema["values"] = values
        if self.subtema_var.get() not in values:
            self.subtema_var.set("Todos")

    def _current_filters(self) -> tuple[str, Optional[int], Optional[int], str]:
        curso = (self.curso_var.get() or "").strip()
        if curso == "Todos":
            curso = ""
        tema_label = (self.tema_var.get() or "").strip()
        tema_id = self._tema_label_to_id.get(tema_label)
        subtema_label = (self.subtema_var.get() or "").strip()
        subtema_id = self._subtema_label_to_id.get(subtema_label)
        estado = (self.estado_var.get() or "Todos").strip() or "Todos"
        return curso, tema_id, subtema_id, estado

    def _refresh_count(self) -> None:
        db = (self.db_name_var.get() or "").strip()
        if not db:
            return
        curso, tema_id, subtema_id, estado = self._current_filters()
        try:
            total = self.controller.contar_problemas(
                db,
                curso=curso,
                tema_id=tema_id,
                subtema_id=subtema_id,
                estado=estado,
            )
        except Exception as exc:
            self.lbl_count.configure(text="Problemas disponibles: error")
            self._log(f"Error contando problemas: {exc}")
            return
        self.lbl_count.configure(text=f"Problemas disponibles: {total}")

    def _build_default_filename(self) -> str:
        tema = (self.tema_var.get() or "").strip()
        sub = (self.subtema_var.get() or "").strip()
        base = "practica"
        if tema and tema != "Todos":
            base += "_" + re_clean(tema)
        if sub and sub != "Todos":
            base += "_" + re_clean(sub)
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        return f"{base}_{stamp}.docx"

    def _generar_word(self) -> None:
        db = (self.db_name_var.get() or "").strip()
        if not db:
            messagebox.showwarning("BD", "Selecciona una base de datos.")
            return
        cantidad = int(self.cantidad_var.get() or 0)
        if cantidad <= 0:
            messagebox.showwarning("Cantidad", "La cantidad debe ser mayor a cero.")
            return
        curso, tema_id, subtema_id, estado = self._current_filters()

        try:
            problemas = self.controller.obtener_problemas(
                db,
                cantidad=cantidad,
                curso=curso,
                tema_id=tema_id,
                subtema_id=subtema_id,
                estado=estado,
                aleatorio=bool(self.aleatorio_var.get()),
            )
        except Exception as exc:
            messagebox.showerror("BD", str(exc))
            return

        if not problemas:
            messagebox.showwarning("Practica", "No se encontraron problemas para ese filtro.")
            return

        suggested = self._build_default_filename()
        out = filedialog.asksaveasfilename(
            title="Guardar practica en Word",
            defaultextension=".docx",
            initialfile=suggested,
            filetypes=[("Word", "*.docx")],
        )
        if not out:
            return

        try:
            output = self.controller.exportar_practica_word(
                output_path=Path(out),
                titulo=(self.titulo_var.get() or "").strip() or "Practica",
                problemas=problemas,
                incluir_clave_inline=bool(self.incluir_clave_inline_var.get()),
                incluir_clave_final=bool(self.incluir_clave_final_var.get()),
            )
        except Exception as exc:
            messagebox.showerror("Word", str(exc))
            return

        self._log(
            f"Practica generada: {output} | problemas={len(problemas)} | "
            f"inline_clave={self.incluir_clave_inline_var.get()} | clave_final={self.incluir_clave_final_var.get()}"
        )
        messagebox.showinfo("Word", f"Archivo generado:\n{output}")


def re_clean(value: str) -> str:
    out = "".join(ch if ch.isalnum() else "_" for ch in (value or "").strip().lower())
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_") or "tema"
