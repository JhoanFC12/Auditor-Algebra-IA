import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Dict, List, Optional, Tuple

from openai import OpenAI

from .controlador import AuditorController, MetodoSolucion, ResultadoAuditoria
from .prompt_manager import PromptManager
from utils.preview_window import PreviewWindow


class AuditorWindow(tk.Toplevel):
    """
    Modulo 3: Auditoria IA + Humano.
    Incluye Cola de Revision por bloques y boton "Aprobar y Siguiente".
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Modulo 3 - Auditoria IA + Humano")
        self.geometry("1100x700")
        self.minsize(980, 640)
        self._maximize_window()

        self.controller = AuditorController()
        self.prompt_manager = PromptManager()
        self._preview = PreviewWindow(title="Vista previa - Modulo 3", width=540, height=620)
        self.preview_panel_var = tk.BooleanVar(value=True)

        self.db_name_var = tk.StringVar(value="")
        self.estado_filter_var = tk.StringVar(value="Pendiente Revision")
        self.archivo_filter_var = tk.StringVar(value="")
        self.block_size_var = tk.IntVar(value=5)

        self.pendientes: List[Tuple[int, str]] = []
        self.bloques: List[List[Tuple[int, str]]] = []
        self.lote: List[Tuple[int, str]] = []
        self.cola_ids: List[int] = []
        self.idx_actual: int = 0
        self.resultados: Dict[int, ResultadoAuditoria] = {}

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
        header = ttk.Frame(self)
        header.pack(fill="x", padx=16, pady=(14, 10))

        ttk.Label(header, text="Algebra AI Auditor", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(
            header,
            text="Modulo 3: Auditoria IA + Humano (cola de revision + lotes).",
        ).pack(anchor="w", pady=(4, 0))

        cfg = ttk.Frame(self)
        cfg.pack(fill="x", padx=16)

        ttk.Label(cfg, text="Base de datos").grid(row=0, column=0, sticky="w")
        self.combo_db = ttk.Combobox(cfg, state="readonly", textvariable=self.db_name_var, values=[])
        self.combo_db.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(cfg, text="Refrescar", command=self._listar_dbs).grid(row=0, column=2, sticky="ew")
        self.combo_db.bind("<<ComboboxSelected>>", lambda _e: self._on_db_change())

        ttk.Label(cfg, text="Estado").grid(row=0, column=3, sticky="w", padx=(12, 0))
        self.combo_estado_filtro = ttk.Combobox(
            cfg,
            state="readonly",
            textvariable=self.estado_filter_var,
            values=["Pendiente Revision", "Bien Planteado", "Mal Planteado", "Todos"],
            width=18,
        )
        self.combo_estado_filtro.grid(row=0, column=4, sticky="w")
        self.combo_estado_filtro.bind("<<ComboboxSelected>>", lambda _e: self._on_estado_change())

        ttk.Label(cfg, text="Filtro archivo_origen").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.combo_archivo = ttk.Combobox(cfg, textvariable=self.archivo_filter_var, values=[])
        self.combo_archivo.grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=(10, 0))
        ttk.Button(cfg, text="Aplicar", command=self.cargar_bloques).grid(
            row=1, column=2, sticky="ew", pady=(10, 0)
        )
        ttk.Button(cfg, text="Archivos", command=self._refrescar_archivos_origen).grid(
            row=1, column=3, sticky="ew", padx=(8, 0), pady=(10, 0)
        )

        ttk.Label(cfg, text="Problemas por bloque").grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.spin_block = ttk.Spinbox(cfg, from_=1, to=200, textvariable=self.block_size_var, width=8)
        self.spin_block.grid(row=2, column=1, sticky="w", padx=(8, 8), pady=(10, 0))
        ttk.Button(cfg, text="Cargar bloques", command=self.cargar_bloques).grid(
            row=2, column=2, sticky="ew", pady=(10, 0)
        )
        ttk.Button(cfg, text="Copiar prompt", command=self.copiar_prompt_clasificacion).grid(
            row=2, column=3, sticky="ew", padx=(8, 0), pady=(10, 0)
        )
        ttk.Button(cfg, text="Enviar a IA", command=self._enviar_lote_ia).grid(
            row=2, column=4, sticky="ew", padx=(8, 0), pady=(10, 0)
        )
        ttk.Button(cfg, text="Guardar lote (Respuesta IA)", command=self._guardar_lote_desde_respuesta_ia).grid(
            row=2, column=5, sticky="ew", padx=(8, 0), pady=(10, 0)
        )
        ttk.Button(cfg, text="Evaluar bloque", command=self._evaluar_bloque).grid(
            row=2, column=6, sticky="ew", padx=(8, 0), pady=(10, 0)
        )

        cfg.columnconfigure(1, weight=1)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=16, pady=(14, 16))

        left = ttk.Frame(body)
        left.pack(side="left", fill="y")
        ttk.Label(left, text="Bloques").pack(anchor="w")
        self.list_bloques = tk.Listbox(
            left,
            selectmode=tk.SINGLE,
            width=32,
            height=8,
            bg="#ffffff",
            fg="#0f172a",
            selectbackground="#bfdbfe",
            selectforeground="#0f172a",
            highlightthickness=1,
            highlightbackground="#cbd5e1",
        )
        self.list_bloques.pack(fill="x", pady=(6, 10))
        self.list_bloques.bind("<<ListboxSelect>>", self._on_select_bloque)

        ttk.Label(left, text="Cola de revision (bloque)").pack(anchor="w")
        self.list_cola = tk.Listbox(
            left,
            selectmode=tk.SINGLE,
            width=20,
            bg="#ffffff",
            fg="#0f172a",
            selectbackground="#bfdbfe",
            selectforeground="#0f172a",
            highlightthickness=1,
            highlightbackground="#cbd5e1",
        )
        self.list_cola.pack(fill="y", expand=True, pady=(6, 0))
        self.list_cola.bind("<<ListboxSelect>>", self._on_select_cola)
        ttk.Button(left, text="Aprobar y siguiente", command=self._aprobar_y_siguiente).pack(fill="x", pady=(10, 0))

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(16, 0))

        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill="both", expand=True)

        self.tab_respuesta = ttk.Frame(self.notebook)
        self.tab_revision = ttk.Frame(self.notebook)
        self.tab_calidad = ttk.Frame(self.notebook)
        self.tab_log = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_revision, text="Revision")
        self.notebook.add(self.tab_respuesta, text="Respuesta IA")
        self.notebook.add(self.tab_calidad, text="Calidad")
        self.notebook.add(self.tab_log, text="Log")

        self.txt_respuesta = tk.Text(
            self.tab_respuesta,
            bg="#ffffff",
            fg="#0f172a",
            insertbackground="#0f172a",
            relief="solid",
            bd=1,
            wrap="word",
        )
        self.txt_respuesta.pack(fill="both", expand=True, padx=8, pady=8)

        # Revision (problema + campos)
        frm = ttk.Frame(self.tab_revision)
        frm.pack(fill="both", expand=True, padx=8, pady=8)

        frm_prob = ttk.Frame(frm)
        frm_prob.pack(fill="x")
        ttk.Label(frm_prob, text="Enunciado (LaTeX)").pack(side="left")
        ttk.Checkbutton(frm_prob, text="Panel vista previa", variable=self.preview_panel_var).pack(side="left", padx=(10, 0))
        ttk.Button(frm_prob, text="Vista previa", command=self._open_preview_actual).pack(side="right")
        ttk.Button(frm_prob, text="Editar enunciado", command=self._abrir_editor_enunciado_actual).pack(
            side="right", padx=(0, 8)
        )

        self.txt_problema = tk.Text(
            frm,
            height=10,
            bg="#ffffff",
            fg="#0f172a",
            insertbackground="#0f172a",
            relief="solid",
            bd=1,
            wrap="word",
        )
        self.txt_problema.pack(fill="x", pady=(6, 12))

        self.var_id = tk.StringVar(value="")
        self.var_estado = tk.StringVar(value="Pendiente Revision")
        self.var_tema_id = tk.StringVar(value="")
        self.var_metodo = tk.StringVar(value="")
        self.var_conceptos = tk.StringVar(value="")
        self.var_clave = tk.StringVar(value="")
        self.var_dificultad = tk.StringVar(value="")
        self.var_razon = tk.StringVar(value="")

        row0 = ttk.Frame(frm); row0.pack(fill="x")
        ttk.Label(row0, text="ID").pack(side="left")
        ttk.Entry(row0, textvariable=self.var_id, state="readonly", width=10).pack(side="left", padx=(8, 14))
        ttk.Label(row0, text="Estado").pack(side="left")
        self.combo_estado = ttk.Combobox(row0, textvariable=self.var_estado, state="readonly", values=["Bien Planteado", "Mal Planteado", "Pendiente Revision"], width=18)
        self.combo_estado.pack(side="left", padx=(8, 14))
        ttk.Label(row0, text="Tema ID").pack(side="left")
        ttk.Entry(row0, textvariable=self.var_tema_id, width=10).pack(side="left", padx=(8, 0))

        row1 = ttk.Frame(frm); row1.pack(fill="x", pady=(10, 0))
        ttk.Label(row1, text="Metodo").pack(side="left")
        ttk.Entry(row1, textvariable=self.var_metodo).pack(side="left", fill="x", expand=True, padx=(8, 14))
        ttk.Label(row1, text="Conceptos IA (IDs)").pack(side="left")
        ttk.Entry(row1, textvariable=self.var_conceptos, width=24).pack(side="left", padx=(8, 0))

        row2 = ttk.Frame(frm); row2.pack(fill="x", pady=(10, 0))
        ttk.Label(row2, text="Respuesta correcta").pack(side="left")
        ttk.Entry(row2, textvariable=self.var_clave, width=10).pack(side="left", padx=(8, 14))
        ttk.Label(row2, text="Dificultad (1-5)").pack(side="left")
        ttk.Entry(row2, textvariable=self.var_dificultad, width=10).pack(side="left", padx=(8, 0))

        row3 = ttk.Frame(frm); row3.pack(fill="x", pady=(10, 0))
        ttk.Label(row3, text="Razon inconsistencia").pack(side="left")
        ttk.Entry(row3, textvariable=self.var_razon).pack(side="left", fill="x", expand=True, padx=(8, 0))

        ttk.Label(frm, text="Desarrollo (LaTeX)").pack(anchor="w", pady=(12, 0))
        self.txt_desarrollo = tk.Text(
            frm,
            height=16,
            bg="#ffffff",
            fg="#0f172a",
            insertbackground="#0f172a",
            relief="solid",
            bd=1,
            wrap="word",
        )
        self.txt_desarrollo.pack(fill="both", expand=True, pady=(6, 0))

        self.txt_log = tk.Text(
            self.tab_log,
            bg="#ffffff",
            fg="#0f172a",
            insertbackground="#0f172a",
            relief="solid",
            bd=1,
            wrap="word",
        )
        self.txt_log.pack(fill="both", expand=True, padx=8, pady=8)

        self.txt_calidad = tk.Text(
            self.tab_calidad,
            bg="#ffffff",
            fg="#0f172a",
            insertbackground="#0f172a",
            relief="solid",
            bd=1,
            wrap="word",
        )
        self.txt_calidad.pack(fill="both", expand=True, padx=8, pady=8)

        self.lbl_status = ttk.Label(self, text="Estado: listo")
        self.lbl_status.pack(fill="x", padx=16, pady=(0, 10))

    def _log(self, text: str) -> None:
        self.txt_log.insert("end", text + "\n")
        self.txt_log.see("end")
        self.lbl_status.configure(text=f"Estado: {text}")

    def _listar_dbs(self) -> None:
        dbs = self.controller.listar_bases_datos()
        self.combo_db["values"] = dbs
        if self.db_name_var.get() in dbs:
            self._refrescar_archivos_origen()
            return
        if dbs:
            self.db_name_var.set(dbs[0])
            self._refrescar_archivos_origen()

    def _on_db_change(self) -> None:
        self._refrescar_archivos_origen()

    def _on_estado_change(self) -> None:
        self._refrescar_archivos_origen()

    def _refrescar_archivos_origen(self) -> None:
        db = (self.db_name_var.get() or "").strip()
        if not db:
            self.combo_archivo["values"] = []
            return
        try:
            items = self.controller.listar_archivos_origen(
                db, estado_filtro=self.estado_filter_var.get(), limit=500
            )
        except Exception:
            items = []

        self._archivo_label_to_value = {"Todos": ""}
        values = ["Todos"]
        for nombre, count in items:
            label = f"{nombre} ({count})"
            values.append(label)
            self._archivo_label_to_value[label] = nombre

        self.combo_archivo["values"] = values
        current = (self.archivo_filter_var.get() or "").strip()
        if not current:
            self.archivo_filter_var.set("Todos")


    def cargar_bloques(self) -> None:
        db_name = self.db_name_var.get()
        if not db_name:
            messagebox.showwarning("BD", "Selecciona una base de datos.")
            return

        try:
            block_size = int(self.block_size_var.get())
        except Exception:
            block_size = 5
        if block_size <= 0:
            block_size = 5

        filtro_raw = (self.archivo_filter_var.get() or "").strip()
        filtro = getattr(self, "_archivo_label_to_value", {}).get(filtro_raw, filtro_raw)
        self._block_size_actual = block_size

        self.controller.inicializar_tablas_si_no_existen(db_name)
        self.pendientes = self.controller.obtener_pendientes(
            db_name,
            limit=2000,
            archivo_origen_filtro=filtro,
            estado_filtro=self.estado_filter_var.get(),
        )
        if not self.pendientes:
            messagebox.showinfo("Revision", "No hay problemas para los filtros indicados.")
            return

        self.bloques = [
            self.pendientes[i : i + block_size] for i in range(0, len(self.pendientes), block_size)
        ]
        self._render_bloques()
        self._activar_bloque(0)
        self._log(f"Bloques cargados: {len(self.bloques)} (pendientes={len(self.pendientes)})")

    def _render_bloques(self) -> None:
        self.list_bloques.delete(0, "end")
        total = len(self.pendientes)
        block_size = int(getattr(self, "_block_size_actual", 5) or 5)
        for idx, bloque in enumerate(self.bloques, start=1):
            start = (idx - 1) * block_size + 1
            end = min(idx * block_size, total)
            self.list_bloques.insert("end", f"Bloque {idx}: Problemas {start}-{end} (n={len(bloque)}/{total})")
        if self.bloques:
            self.list_bloques.selection_set(0)

    def _on_select_bloque(self, _event) -> None:
        sel = self.list_bloques.curselection()
        if not sel:
            return
        idx = int(sel[0])
        self._activar_bloque(idx)

    def _activar_bloque(self, idx: int) -> None:
        if idx < 0 or idx >= len(self.bloques):
            return
        self.lote = list(self.bloques[idx])
        self.cola_ids = [pid for pid, _ in self.lote]
        self.idx_actual = 0
        self.resultados = {}
        self._render_cola()
        self._mostrar_problema_actual()

    def _render_cola(self) -> None:
        self.list_cola.delete(0, "end")
        for pid in self.cola_ids:
            self.list_cola.insert("end", str(pid))
        if self.cola_ids:
            self.list_cola.selection_set(0)

    def _problema_por_id(self, pid: int) -> Optional[str]:
        for p_id, latex in self.lote:
            if p_id == pid:
                return latex
        return None

    def _mostrar_problema_actual(self) -> None:
        if not self.cola_ids:
            return
        pid = self.cola_ids[self.idx_actual]
        latex = self._problema_por_id(pid) or ""
        self.txt_problema.delete("1.0", "end")
        self.txt_problema.insert("end", latex)
        try:
            self._preview.set_text(latex)
            if bool(self.preview_panel_var.get()):
                self._open_preview_panel_dock()
        except Exception:
            pass
        self.var_id.set(str(pid))
        self.activar_tab_visual_con_datos(pid)
        self._refrescar_calidad_actual(pid)

    def _open_preview_actual(self) -> None:
        latex = (self.txt_problema.get("1.0", "end") or "").strip()
        try:
            self._preview.set_text(latex)
            self._open_preview_panel_dock()
        except Exception as exc:
            messagebox.showerror("Vista previa", str(exc))

    def _open_preview_panel_dock(self) -> None:
        self.update_idletasks()
        try:
            x = int(self.txt_problema.winfo_rootx() + self.txt_problema.winfo_width() + 12)
            y = int(self.txt_problema.winfo_rooty())
        except Exception:
            x = None
            y = None
        self._preview.ensure_open_at(x=x, y=y, on_top=False)

    def _abrir_editor_enunciado_actual(self) -> None:
        pid_raw = (self.var_id.get() or "").strip()
        if not pid_raw.isdigit():
            messagebox.showwarning("Editor", "Selecciona un problema primero.")
            return
        db_name = (self.db_name_var.get() or "").strip()
        if not db_name:
            messagebox.showwarning("BD", "Selecciona una base de datos.")
            return
        pid = int(pid_raw)
        latex = (self.txt_problema.get("1.0", "end") or "").strip()
        EnunciadoEditorWindow(self, db_name=db_name, problema_id=pid, latex_inicial=latex, on_saved=self._on_enunciado_guardado)

    def _on_enunciado_guardado(self, problema_id: int, nuevo_latex: str) -> None:
        # Actualiza en memoria el lote/bloques para reflejar el cambio sin recargar todo
        for i, (pid, latex) in enumerate(self.lote):
            if pid == problema_id:
                self.lote[i] = (pid, nuevo_latex)
                break
        for bi, bloque in enumerate(self.bloques):
            for i, (pid, latex) in enumerate(bloque):
                if pid == problema_id:
                    self.bloques[bi][i] = (pid, nuevo_latex)
                    break
        self.txt_problema.delete("1.0", "end")
        self.txt_problema.insert("end", nuevo_latex)
        try:
            self._preview.set_text(nuevo_latex)
        except Exception:
            pass

    def _on_select_cola(self, _event) -> None:
        sel = self.list_cola.curselection()
        if not sel:
            return
        try:
            pid = int(self.list_cola.get(sel[0]))
        except Exception:
            return
        if pid in self.cola_ids:
            self.idx_actual = self.cola_ids.index(pid)
            self._mostrar_problema_actual()

    def _enviar_lote_ia(self) -> None:
        if not self.lote:
            messagebox.showwarning("IA", "Primero inicia un lote.")
            return

        def worker():
            try:
                client = OpenAI()
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("OpenAI", f"No se pudo iniciar cliente.\n\n{exc}"))
                return

            prompt = self.prompt_manager.generar_prompt_clasificacion(self.lote, db_name=self.db_name_var.get())
            self.after(0, lambda: self._log("Enviando lote a IA..."))
            try:
                resp = client.responses.create(
                    model="gpt-4o-mini",
                    input=prompt,
                )
                text = resp.output_text
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("OpenAI", f"Error en llamada.\n\n{exc}"))
                return

            def done():
                self.txt_respuesta.delete("1.0", "end")
                self.txt_respuesta.insert("end", text)
                self._log("Respuesta IA recibida. Parseando...")
                self._parsear_y_cargar_resultados(text)

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _guardar_lote_desde_respuesta_ia(self) -> None:
        db_name = (self.db_name_var.get() or "").strip()
        if not db_name:
            messagebox.showwarning("BD", "Selecciona una base de datos.")
            return

        texto = (self.txt_respuesta.get("1.0", "end") or "").strip()
        if not texto:
            messagebox.showwarning("Guardar", "Pega la respuesta en la pestaña 'Respuesta IA'.")
            return

        parsed = self.controller.parsear_respuesta_lote(texto)
        if not parsed:
            messagebox.showwarning("Guardar", "No se detectaron bloques ::ID::. Revisa el formato.")
            return

        if self.cola_ids:
            esperados = set(self.cola_ids)
            recibidos = set(parsed.keys())
            if recibidos != esperados:
                faltan = sorted(esperados - recibidos)
                sobran = sorted(recibidos - esperados)
                messagebox.showwarning(
                    "Validación lote",
                    "La respuesta no coincide exactamente con el bloque cargado.\n\n"
                    f"Esperados: {len(esperados)}\nRecibidos: {len(recibidos)}\n"
                    f"Faltan: {faltan}\nSobran: {sobran}\n\n"
                    "Se guardará solo la intersección de IDs.",
                )
            ids_guardar = [pid for pid in self.cola_ids if pid in parsed]
        else:
            ids_guardar = sorted(parsed.keys())

        if not ids_guardar:
            messagebox.showwarning("Guardar", "No hay IDs para guardar.")
            return

        resultados = [parsed[pid] for pid in ids_guardar]
        try:
            c, msg = self.controller.guardar_resultados_lote(db_name, resultados)
        except Exception as exc:
            messagebox.showerror("BD", f"No se pudo guardar el lote.\n\n{exc}")
            return

        messagebox.showinfo("Guardar", f"{msg}\nRegistros procesados: {len(resultados)}\nRegistros guardados: {c}")
        # Refrescar fuentes/filtros y cola actual
        self._refrescar_archivos_origen()
        self.cargar_bloques()

    def _parsear_y_cargar_resultados(self, texto: str) -> None:
        parsed = self.controller.parsear_respuesta_lote(texto)
        self.resultados = parsed

        esperados = set(self.cola_ids)
        recibidos = set(parsed.keys())
        if recibidos != esperados:
            faltan = sorted(esperados - recibidos)
            sobran = sorted(recibidos - esperados)
            messagebox.showwarning(
                "Validacion lote",
                "La IA no devolvio exactamente el mismo conjunto de IDs.\n\n"
                f"Esperados: {len(esperados)}\nRecibidos: {len(recibidos)}\n"
                f"Faltan: {faltan}\nSobran: {sobran}",
            )

        self.activar_tab_visual_con_datos(self.cola_ids[self.idx_actual])
        self._refrescar_calidad_actual(self.cola_ids[self.idx_actual])

    def activar_tab_visual_con_datos(self, pid: int) -> None:
        """
        Carga en la pestaña 'Revision' los datos del resultado parseado (si existe)
        o deja campos listos para edicion manual.
        """
        res = self.resultados.get(pid)
        if not res:
            self.var_estado.set("Pendiente Revision")
            self.var_tema_id.set("")
            self.var_metodo.set("")
            self.var_conceptos.set("")
            self.var_clave.set("")
            self.var_dificultad.set("")
            self.var_razon.set("")
            self.txt_desarrollo.delete("1.0", "end")
            return

        self.var_estado.set(res.estado)
        self.var_tema_id.set("" if res.tema_id is None else str(res.tema_id))
        self.var_clave.set(res.respuesta_correcta or "")
        self.var_dificultad.set("" if not res.nivel_dificultad else str(res.nivel_dificultad))
        self.var_razon.set(res.razon_inconsistencia or "")

        metodo = res.metodos[0].metodo if res.metodos else ""
        conceptos = res.conceptos_principales or (res.metodos[0].propiedades if res.metodos else [])
        desarrollo = res.metodos[0].desarrollo_latex if res.metodos else ""

        self.var_metodo.set(metodo)
        self.var_conceptos.set(",".join(str(r) for r in conceptos))
        self.txt_desarrollo.delete("1.0", "end")
        self.txt_desarrollo.insert("end", desarrollo)

    def _aprobar_y_siguiente(self) -> None:
        db_name = self.db_name_var.get()
        if not db_name:
            messagebox.showwarning("BD", "Selecciona una base de datos.")
            return
        if not self.cola_ids:
            return

        pid = int(self.var_id.get() or 0)
        if pid <= 0:
            return

        # Construye resultado desde UI (permite correccion manual)
        estado = (self.var_estado.get() or "").strip() or "Pendiente Revision"
        tema_id_raw = (self.var_tema_id.get() or "").strip()
        tema_id = int(tema_id_raw) if tema_id_raw.isdigit() else None
        metodo = (self.var_metodo.get() or "").strip()
        conceptos = [int(x) for x in self.var_conceptos.get().replace(" ", "").split(",") if x.isdigit()]
        desarrollo = self.txt_desarrollo.get("1.0", "end").strip()
        clave = (self.var_clave.get() or "").strip()
        dif_raw = (self.var_dificultad.get() or "").strip()
        dificultad = int(dif_raw) if dif_raw.isdigit() else 0
        razon = (self.var_razon.get() or "").strip()

        if estado not in {"Bien Planteado", "Mal Planteado", "Pendiente Revision"}:
            messagebox.showwarning("Validacion", "Estado invalido.")
            return

        base = self.resultados.get(pid)
        if base:
            metodos = list(base.metodos or [])
            if metodos:
                metodos[0] = MetodoSolucion(
                    metodo=metodo or metodos[0].metodo or "(sin metodo)",
                    propiedades=conceptos or metodos[0].propiedades,
                    desarrollo_latex=desarrollo or metodos[0].desarrollo_latex,
                )
            else:
                metodos = [
                    MetodoSolucion(
                        metodo=metodo or "(sin metodo)",
                        propiedades=conceptos,
                        desarrollo_latex=desarrollo,
                    )
                ]
            resultado = ResultadoAuditoria(
                problema_id=pid,
                estado=estado,
                tema_id=tema_id,
                conceptos_principales=conceptos,
                conceptos_secundarios=getattr(base, "conceptos_secundarios", []) or [],
                metodos=metodos,
                respuesta_correcta=clave,
                nivel_dificultad=dificultad,
                razon_inconsistencia=razon,
            )
        else:
            resultado = ResultadoAuditoria(
                problema_id=pid,
                estado=estado,
                tema_id=tema_id,
                conceptos_principales=conceptos,
                conceptos_secundarios=[],
                metodos=[
                    MetodoSolucion(
                        metodo=metodo or "(sin metodo)",
                        propiedades=conceptos,
                        desarrollo_latex=desarrollo,
                    )
                ],
                respuesta_correcta=clave,
                nivel_dificultad=dificultad,
                razon_inconsistencia=razon,
            )

        try:
            self.controller.guardar_resultado(db_name, resultado)
        except Exception as exc:
            messagebox.showerror("BD", f"No se pudo guardar.\n\n{exc}")
            return

        self._log(f"Aprobado y guardado ID={pid}")

        # Avanza
        if self.idx_actual < len(self.cola_ids) - 1:
            self.idx_actual += 1
            self.list_cola.selection_clear(0, "end")
            self.list_cola.selection_set(self.idx_actual)
            self._mostrar_problema_actual()
        else:
            messagebox.showinfo("Lote", "Lote completado.")

    def copiar_prompt_clasificacion(self) -> None:
        if not self.lote:
            messagebox.showwarning("Prompt", "Primero carga un bloque.")
            return
        db = (self.db_name_var.get() or "").strip()
        filtro = (self.archivo_filter_var.get() or "").strip()
        try:
            prompt = self.prompt_manager.generar_prompt_clasificacion(self.lote, db_name=db)
            if filtro:
                prompt += f"\n\nFILTRO archivo_origen (referencia): {filtro}\n"
        except Exception as exc:
            messagebox.showerror("Prompt", str(exc))
            return

        try:
            self.clipboard_clear()
            self.clipboard_append(prompt)
            self.update_idletasks()
        except Exception:
            pass
        messagebox.showinfo("Prompt", "Prompt copiado al portapapeles.")

    def _refrescar_calidad_actual(self, pid: int) -> None:
        db = (self.db_name_var.get() or "").strip()
        self.txt_calidad.delete("1.0", "end")
        if not db:
            self.txt_calidad.insert("end", "Selecciona una base de datos.")
            return

        res = self.resultados.get(pid)
        if not res:
            self.txt_calidad.insert(
                "end",
                "Sin resultado IA parseado para este ID.\n"
                "Tip: pega la respuesta IA y guarda el lote, o procesa por IA este bloque.",
            )
            return

        ok, errors, warnings = self.controller.validar_resultado(db, res)
        self.txt_calidad.insert("end", f"ID {pid} - {'OK' if ok else 'CON ERRORES'}\n\n")
        if errors:
            self.txt_calidad.insert("end", "ERRORES:\n" + "\n".join(f"- {e}" for e in errors) + "\n\n")
        if warnings:
            self.txt_calidad.insert("end", "WARNINGS:\n" + "\n".join(f"- {w}" for w in warnings) + "\n\n")
        if not errors and not warnings:
            self.txt_calidad.insert("end", "Sin observaciones.\n")

    def _evaluar_bloque(self) -> None:
        db = (self.db_name_var.get() or "").strip()
        if not db:
            messagebox.showwarning("BD", "Selecciona una base de datos.")
            return
        if not self.resultados:
            messagebox.showwarning("Calidad", "No hay resultados IA parseados para evaluar.")
            return

        resultados = list(self.resultados.values())
        ok_count, errs, warns = self.controller.validar_lote(db, resultados)
        total = len(resultados)

        lines = [f"Resumen bloque: OK={ok_count}/{total}", ""]
        if errs:
            lines.append("ERRORES (por ID):")
            for pid, lst in sorted(errs.items()):
                lines.append(f"- ID {pid}: " + " | ".join(lst))
            lines.append("")
        if warns:
            lines.append("WARNINGS (por ID):")
            for pid, lst in sorted(warns.items()):
                lines.append(f"- ID {pid}: " + " | ".join(lst))
            lines.append("")

        self.notebook.select(self.tab_calidad)
        self.txt_calidad.delete("1.0", "end")
        self.txt_calidad.insert("end", "\n".join(lines).strip() + "\n")


class EnunciadoEditorWindow(tk.Toplevel):
    def __init__(
        self,
        parent: AuditorWindow,
        *,
        db_name: str,
        problema_id: int,
        latex_inicial: str,
        on_saved,
    ):
        super().__init__(parent)
        self.parent = parent
        self.db_name = db_name
        self.problema_id = int(problema_id)
        self.on_saved = on_saved
        self.preview = PreviewWindow(title=f"Vista previa - ID {self.problema_id}", width=820, height=900)

        self.title(f"Editar enunciado - ID {self.problema_id}")
        self.geometry("980x640")
        self.minsize(860, 560)
        self.configure(bg="#f8fafc")

        top = ttk.Frame(self)
        top.pack(fill="x", padx=16, pady=(14, 10))
        ttk.Label(top, text=f"ID {self.problema_id}", font=("Segoe UI", 12, "bold")).pack(side="left")
        ttk.Button(top, text="Abrir vista previa", command=self._open_preview).pack(side="right")
        ttk.Button(top, text="Guardar", command=self._guardar).pack(side="right", padx=(0, 8))

        self.txt = tk.Text(
            self,
            bg="#ffffff",
            fg="#0f172a",
            insertbackground="#0f172a",
            relief="solid",
            bd=1,
            wrap="word",
        )
        self.txt.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.txt.insert("1.0", latex_inicial or "")
        self.txt.edit_modified(False)
        self.txt.bind("<<Modified>>", self._on_modified)

        self._debounce_after: str | None = None

    def _on_modified(self, _event=None) -> None:
        try:
            self.txt.edit_modified(False)
        except Exception:
            pass
        if self._debounce_after:
            try:
                self.after_cancel(self._debounce_after)
            except Exception:
                pass
        self._debounce_after = self.after(200, self._push_preview)

    def _current_text(self) -> str:
        return (self.txt.get("1.0", "end") or "").strip()

    def _push_preview(self) -> None:
        try:
            self.preview.set_text(self._current_text())
        except Exception:
            pass

    def _open_preview(self) -> None:
        self._push_preview()
        try:
            self.preview.ensure_open()
        except Exception as exc:
            messagebox.showerror("Vista previa", str(exc))

    def _guardar(self) -> None:
        nuevo = self._current_text()
        if not nuevo.strip():
            messagebox.showwarning("Guardar", "El enunciado no puede estar vacío.")
            return
        try:
            affected = self.parent.controller.actualizar_enunciado(
                self.db_name,
                problema_id=self.problema_id,
                enunciado_latex=nuevo,
            )
        except Exception as exc:
            messagebox.showerror("BD", str(exc))
            return
        if affected <= 0:
            messagebox.showwarning("BD", "No se actualizó ningún registro (ID no encontrado).")
            return
        try:
            self.on_saved(self.problema_id, nuevo)
        except Exception:
            pass
        messagebox.showinfo("BD", "Enunciado actualizado.")
        self.destroy()
