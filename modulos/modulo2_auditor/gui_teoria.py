import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .controlador_teoria import TheoryController
from .prompt_manager import PromptManager


class TheoryUploaderWindow(tk.Toplevel):
    def __init__(self, parent, db_name_inicial=None):
        super().__init__(parent)
        self.controller = TheoryController()
        self.prompt_manager = PromptManager()
        self.db_sel = db_name_inicial

        self.title("Modulo 2 - Teoria / Base de conocimiento")
        self.geometry("1120x720")
        self.minsize(980, 640)
        self._maximize_window()

        self._apply_light_theme()
        self._build_ui()
        self.listar_dbs()

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
        header = ttk.Label(
            self,
            text="Modulo 2 - Teoria / Base de conocimiento",
            font=("Segoe UI", 14, "bold"),
        )
        header.pack(anchor="w", padx=16, pady=(14, 6))

        top = ttk.Frame(self)
        top.pack(fill="x", padx=16)

        ttk.Label(top, text="Base de datos").grid(row=0, column=0, sticky="w")
        self.combo_db = ttk.Combobox(top, state="readonly", values=[])
        self.combo_db.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(top, text="Refrescar", command=self.listar_dbs).grid(row=0, column=2, sticky="ew")
        ttk.Button(top, text="Ver conceptos", command=self.refrescar_conceptos).grid(
            row=0, column=3, sticky="ew", padx=(8, 0)
        )

        ttk.Label(top, text="Concepto").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.var_concepto = tk.StringVar(value="Proposiciones")
        self.combo_concepto = ttk.Combobox(
            top,
            state="readonly",
            textvariable=self.var_concepto,
            values=["Proposiciones", "Definiciones"],
            width=18,
        )
        self.combo_concepto.grid(row=1, column=1, sticky="w", padx=(8, 8), pady=(10, 0))
        self.combo_concepto.bind("<<ComboboxSelected>>", lambda _e: self.refrescar_conceptos())
        ttk.Button(top, text="Aplicar", command=self.refrescar_conceptos).grid(
            row=1, column=2, sticky="w", pady=(10, 0)
        )
        top.columnconfigure(1, weight=1)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=16, pady=(12, 16))

        self.tab_cargar = ttk.Frame(self.notebook)
        self.tab_explorar = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_cargar, text="Cargar")
        self.notebook.add(self.tab_explorar, text="Explorar / Editar")

        self._build_tab_cargar()
        self._build_tab_explorar()

    def _build_tab_cargar(self) -> None:
        actions = ttk.Frame(self.tab_cargar)
        actions.pack(fill="x", padx=8, pady=8)

        ttk.Button(actions, text="Cargar archivo .txt", command=self.cargar_archivo).pack(side="left")
        ttk.Button(actions, text="Procesar y guardar", command=self.procesar_guardado).pack(side="left", padx=(8, 0))
        ttk.Label(actions, text="Lote").pack(side="left", padx=(12, 6))
        self.var_batch = tk.StringVar(value="25")
        ttk.Entry(actions, textvariable=self.var_batch, width=6).pack(side="left")
        ttk.Button(actions, text="Guardar por lotes", command=self.procesar_guardado_lotes).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Prompt IA (copiar)", command=self.copiar_prompt_ia).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Copiar teoria (Concepto)", command=self.copiar_teoria).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Limpiar", command=lambda: self.txt_contenido.delete("1.0", tk.END)).pack(
            side="left", padx=(8, 0)
        )

        self.txt_contenido = scrolledtext.ScrolledText(self.tab_cargar, wrap=tk.WORD, height=18)
        self.txt_contenido.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        ttk.Label(
            self.tab_cargar,
            text=(
                "Formato por bloque: '--- INICIO UNIDAD ---' ... '--- FIN UNIDAD ---'. "
                "Usa CLASE: DEFINICION o CLASE: PROPOSICION para que el cargador sepa a que tabla va."
            ),
        ).pack(anchor="w", padx=8, pady=(0, 8))

    def copiar_teoria(self) -> None:
        db = self._db_actual()
        if not db:
            messagebox.showwarning("Base de datos", "Selecciona una base de datos.")
            return
        tabla = self._tabla_actual()
        try:
            data = self.controller.exportar_teoria_formato(db, tabla=tabla)
        except Exception as exc:
            messagebox.showerror("Copiar teoria", str(exc))
            return

        if not data.strip():
            messagebox.showinfo("Copiar teoria", "No hay registros para copiar en el concepto seleccionado.")
            return

        self.clipboard_clear()
        self.clipboard_append(data)
        self.update_idletasks()
        messagebox.showinfo(
            "Copiar teoria",
            f"Teoria copiada al portapapeles.\nConcepto: {self.var_concepto.get()}\nBD: {db}",
        )

    def copiar_prompt_ia(self) -> None:
        prompt = self.prompt_manager.generar_prompt_formateo_teoria()
        db = self._db_actual()
        if db:
            try:
                temas = self.controller.obtener_temas(db)
            except Exception:
                temas = []
            if temas:
                prompt += (
                    "\n\nCATALOGO DE TEMAS DISPONIBLES (usa TEMA_ID solo si coincide; PROHIBIDO inventar IDs):\n"
                    + "\n".join([f"- {tid}: {nombre} | AREA: {area or 'General'}" for tid, nombre, area in temas])
                    + "\n"
                )
        try:
            self.clipboard_clear()
            self.clipboard_append(prompt)
            self.update_idletasks()
        except Exception:
            pass

        win = tk.Toplevel(self)
        win.title("Prompt IA - Formato Teoria")
        win.geometry("900x600")
        win.minsize(760, 520)

        top = ttk.Frame(win)
        top.pack(fill="x", padx=10, pady=10)
        ttk.Label(top, text="Prompt listo para copiar/pegar en tu IA.", font=("Segoe UI", 11, "bold")).pack(
            side="left"
        )

        ttk.Button(
            top,
            text="Copiar",
            command=lambda: (win.clipboard_clear(), win.clipboard_append(prompt), win.update_idletasks()),
        ).pack(side="right")

        txt = scrolledtext.ScrolledText(win, wrap=tk.WORD)
        txt.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        txt.insert(tk.END, prompt)
        txt.focus_set()

        messagebox.showinfo("Prompt IA", "Prompt copiado al portapapeles.")

    def _build_tab_explorar(self) -> None:
        body = ttk.Frame(self.tab_explorar)
        body.pack(fill="both", expand=True, padx=8, pady=8)

        left = ttk.Frame(body)
        left.pack(side="left", fill="y")
        ttk.Label(left, text="Temas").pack(anchor="w")
        self.list_temas = tk.Listbox(left, width=28)
        self.list_temas.pack(fill="y", expand=True, pady=(6, 0))
        self.list_temas.bind("<<ListboxSelect>>", lambda _e: self._on_select_tema())

        mid = ttk.Frame(body)
        mid.pack(side="left", fill="both", expand=True, padx=(12, 12))
        self.lbl_items = ttk.Label(mid, text="Proposiciones")
        self.lbl_items.pack(anchor="w")
        self.list_items = tk.Listbox(mid)
        self.list_items.pack(fill="both", expand=True, pady=(6, 0))
        self.list_items.bind("<<ListboxSelect>>", lambda _e: self._on_select_item())

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)

        # Fields comunes
        common = ttk.Frame(right)
        common.pack(fill="x")
        self.var_id = tk.StringVar(value="")
        self.var_nombre = tk.StringVar(value="")
        self.var_tema = tk.StringVar(value="Gral")
        self.var_area = tk.StringVar(value="General")

        ttk.Label(common, text="ID").grid(row=0, column=0, sticky="w")
        ttk.Entry(common, textvariable=self.var_id, state="readonly", width=10).grid(
            row=0, column=1, sticky="w", padx=(8, 16)
        )
        ttk.Label(common, text="Nombre *").grid(row=0, column=2, sticky="w")
        ttk.Entry(common, textvariable=self.var_nombre).grid(row=0, column=3, sticky="ew", padx=(8, 0))

        ttk.Label(common, text="Area/Curso").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(common, textvariable=self.var_area).grid(
            row=1, column=1, sticky="ew", padx=(8, 16), pady=(10, 0)
        )
        ttk.Label(common, text="Tema").grid(row=1, column=2, sticky="w", pady=(10, 0))
        ttk.Entry(common, textvariable=self.var_tema).grid(
            row=1, column=3, sticky="ew", padx=(8, 0), pady=(10, 0)
        )
        common.columnconfigure(3, weight=1)
        common.columnconfigure(1, weight=1)

        # Editor Proposicion
        self.frame_prop = ttk.Frame(right)
        self.var_tipo = tk.StringVar(value="TEOREMA")

        row = ttk.Frame(self.frame_prop)
        row.pack(fill="x", pady=(10, 0))
        ttk.Label(row, text="Tipo").pack(side="left")
        self.combo_tipo = ttk.Combobox(
            row,
            state="readonly",
            textvariable=self.var_tipo,
            values=["TEOREMA", "COROLARIO", "LEMA", "AXIOMA"],
            width=14,
        )
        self.combo_tipo.pack(side="left", padx=(8, 0))

        ttk.Label(self.frame_prop, text="Hipotesis").pack(anchor="w", pady=(12, 0))
        self.txt_hip = scrolledtext.ScrolledText(self.frame_prop, wrap=tk.WORD, height=6)
        self.txt_hip.pack(fill="both", expand=False, pady=(6, 0))

        ttk.Label(self.frame_prop, text="Tesis").pack(anchor="w", pady=(12, 0))
        self.txt_tesis = scrolledtext.ScrolledText(self.frame_prop, wrap=tk.WORD, height=6)
        self.txt_tesis.pack(fill="both", expand=False, pady=(6, 0))

        ttk.Label(self.frame_prop, text="Descripcion").pack(anchor="w", pady=(12, 0))
        self.txt_desc = scrolledtext.ScrolledText(self.frame_prop, wrap=tk.WORD, height=6)
        self.txt_desc.pack(fill="both", expand=True, pady=(6, 0))

        # Editor Definicion
        self.frame_def = ttk.Frame(right)
        ttk.Label(self.frame_def, text="Enunciado").pack(anchor="w", pady=(10, 0))
        self.txt_enunciado = scrolledtext.ScrolledText(self.frame_def, wrap=tk.WORD, height=18)
        self.txt_enunciado.pack(fill="both", expand=True, pady=(6, 0))

        # Botones
        buttons = ttk.Frame(right)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Nuevo", command=self.nuevo_item).pack(side="left")
        ttk.Button(buttons, text="Guardar cambios", command=self.guardar_cambios).pack(side="left", padx=(8, 0))

        self.temas_index_to_id = {}
        self.items_index_to_id = {}
        self._switch_editor()

    def _tabla_actual(self) -> str:
        return "definiciones_matematicas" if self.var_concepto.get() == "Definiciones" else "proposiciones_matematicas"

    def _switch_editor(self) -> None:
        self.lbl_items.configure(text=self.var_concepto.get())
        self.frame_prop.pack_forget()
        self.frame_def.pack_forget()
        if self._tabla_actual() == "definiciones_matematicas":
            self.frame_def.pack(fill="both", expand=True, pady=(10, 0))
        else:
            self.frame_prop.pack(fill="both", expand=True, pady=(10, 0))

    def listar_dbs(self) -> None:
        dbs = self.controller.listar_bases_datos()
        self.combo_db["values"] = dbs
        if self.db_sel and self.db_sel in dbs:
            self.combo_db.set(self.db_sel)
        elif dbs and self.combo_db.get() not in dbs:
            self.combo_db.set(dbs[0])

    def _db_actual(self) -> str:
        return (self.combo_db.get() or "").strip()

    def cargar_archivo(self) -> None:
        ruta = filedialog.askopenfilename(
            title="Selecciona archivo de teoria",
            filetypes=[("Texto", "*.txt"), ("Todos", "*.*")],
        )
        if not ruta:
            return
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                contenido = f.read()
        except UnicodeDecodeError:
            with open(ruta, "r", encoding="latin-1", errors="replace") as f:
                contenido = f.read()
        self.txt_contenido.delete("1.0", tk.END)
        self.txt_contenido.insert(tk.END, contenido)

    def procesar_guardado(self) -> None:
        db = self._db_actual()
        if not db:
            messagebox.showwarning("Base de datos", "Selecciona una base de datos.")
            return
        txt = self.txt_contenido.get("1.0", tk.END)
        items = self.controller.parsear_texto_teoria(txt)
        c, m = self.controller.guardar_teoria(items, db)
        messagebox.showinfo("Resultado", f"{m}\nRegistros guardados: {c}\nTotal parseados: {len(items)}")
        self.refrescar_conceptos()

    def procesar_guardado_lotes(self) -> None:
        db = self._db_actual()
        if not db:
            messagebox.showwarning("Base de datos", "Selecciona una base de datos.")
            return
        txt = self.txt_contenido.get("1.0", tk.END)
        items = self.controller.parsear_texto_teoria(txt)
        try:
            batch = int((self.var_batch.get() or "25").strip())
        except Exception:
            batch = 25
        c, m = self.controller.guardar_teoria_en_lotes(items, db, batch_size=batch)
        messagebox.showinfo(
            "Resultado",
            f"{m}\nRegistros guardados: {c}\nTotal parseados: {len(items)}\nBatch: {batch}",
        )
        self.refrescar_conceptos()

    def refrescar_conceptos(self) -> None:
        db = self._db_actual()
        if not db:
            messagebox.showwarning("Base de datos", "Selecciona una base de datos.")
            return

        self._switch_editor()

        temas = self.controller.obtener_temas(db)
        self.list_temas.delete(0, tk.END)
        self.temas_index_to_id = {}

        self.list_temas.insert(tk.END, "Todos")
        self.temas_index_to_id[0] = None
        for idx, (tid, nombre, area) in enumerate(temas, start=1):
            label = f"{area or 'General'} - {nombre}"
            self.list_temas.insert(tk.END, label)
            self.temas_index_to_id[idx] = tid

        self.list_temas.selection_clear(0, tk.END)
        self.list_temas.selection_set(0)
        self._on_select_tema()
        self.notebook.select(self.tab_explorar)

    def _on_select_tema(self) -> None:
        db = self._db_actual()
        if not db:
            return
        sel = self.list_temas.curselection()
        if not sel:
            return
        tema_id = self.temas_index_to_id.get(int(sel[0]))

        items = self.controller.obtener_reglas_resumen(
            db,
            tabla=self._tabla_actual(),
            tema_id=tema_id,
            limit=500,
        )
        self.list_items.delete(0, tk.END)
        self.items_index_to_id = {}
        for idx, (rid, nombre, tipo, tema) in enumerate(items):
            label = f"{rid} - {nombre}"
            if tipo and self._tabla_actual() != "definiciones_matematicas":
                label += f" [{tipo}]"
            if tema:
                label += f" ({tema})"
            self.list_items.insert(tk.END, label)
            self.items_index_to_id[idx] = rid

        self.nuevo_item()

    def _on_select_item(self) -> None:
        db = self._db_actual()
        if not db:
            return
        self._switch_editor()
        sel = self.list_items.curselection()
        if not sel:
            return
        rid = self.items_index_to_id.get(int(sel[0]))
        if not rid:
            return

        data = self.controller.obtener_regla_detalle(db, tabla=self._tabla_actual(), regla_id=rid)
        if not data:
            return

        self.var_id.set(str(data.get("id") or ""))
        self.var_nombre.set(str(data.get("nombre") or ""))
        self.var_tema.set(str(data.get("tema") or "Gral"))
        self.var_area.set(str(data.get("area") or "General"))

        if self._tabla_actual() == "definiciones_matematicas":
            self.txt_enunciado.delete("1.0", tk.END)
            self.txt_enunciado.insert(tk.END, str(data.get("enunciado") or ""))
        else:
            self.var_tipo.set(str(data.get("tipo") or "TEOREMA").upper())
            self.txt_hip.delete("1.0", tk.END)
            self.txt_hip.insert(tk.END, str(data.get("hipotesis") or ""))
            self.txt_tesis.delete("1.0", tk.END)
            self.txt_tesis.insert(tk.END, str(data.get("tesis") or ""))
            self.txt_desc.delete("1.0", tk.END)
            self.txt_desc.insert(tk.END, str(data.get("descripcion") or ""))

    def nuevo_item(self) -> None:
        self._switch_editor()
        self.var_id.set("")
        self.var_nombre.set("")
        self.var_tema.set("Gral")
        self.var_area.set("General")
        if self._tabla_actual() == "definiciones_matematicas":
            self.txt_enunciado.delete("1.0", tk.END)
        else:
            self.var_tipo.set("TEOREMA")
            self.txt_hip.delete("1.0", tk.END)
            self.txt_tesis.delete("1.0", tk.END)
            self.txt_desc.delete("1.0", tk.END)

    def guardar_cambios(self) -> None:
        db = self._db_actual()
        if not db:
            messagebox.showwarning("Base de datos", "Selecciona una base de datos.")
            return

        rid_raw = (self.var_id.get() or "").strip()
        rid = int(rid_raw) if rid_raw.isdigit() else None

        nombre = (self.var_nombre.get() or "").strip()
        if not nombre:
            messagebox.showwarning("Validacion", "Nombre es obligatorio.")
            return

        if self._tabla_actual() == "definiciones_matematicas":
            payload = {
                "nombre": nombre,
                "area": self.var_area.get(),
                "tema": self.var_tema.get(),
                "enunciado": self.txt_enunciado.get("1.0", tk.END).strip(),
            }
        else:
            payload = {
                "nombre": nombre,
                "area": self.var_area.get(),
                "tema": self.var_tema.get(),
                "tipo": self.var_tipo.get(),
                "hipotesis": self.txt_hip.get("1.0", tk.END).strip(),
                "tesis": self.txt_tesis.get("1.0", tk.END).strip(),
                "descripcion": self.txt_desc.get("1.0", tk.END).strip(),
            }

        ok, msg = self.controller.guardar_regla_detalle(
            db, tabla=self._tabla_actual(), regla_id=rid, data=payload
        )
        if not ok:
            messagebox.showerror("Guardar", msg)
            return
        messagebox.showinfo("Guardar", msg)
        self.refrescar_conceptos()
