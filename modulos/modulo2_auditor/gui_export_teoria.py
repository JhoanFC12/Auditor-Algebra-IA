import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .controlador_teoria import TheoryController


class ExportTheoryWindow(tk.Toplevel):
    """Ventana para exportar/copiado masivo de teoria desde la BD."""

    def __init__(self, parent, db_name_inicial=None):
        super().__init__(parent)
        self.controller = TheoryController()
        self.db_sel = db_name_inicial

        self.title("Exportar teoria (copiar)")
        self.geometry("980x680")
        self.minsize(860, 600)
        self._maximize_window()

        self._build_ui()
        self._listar_dbs_async()

    def _build_ui(self) -> None:
        header = ttk.Label(self, text="Exportar teoria (para IA / respaldo)", font=("Segoe UI", 14, "bold"))
        header.pack(anchor="w", padx=16, pady=(14, 6))

        top = ttk.Frame(self)
        top.pack(fill="x", padx=16)

        ttk.Label(top, text="Base de datos").grid(row=0, column=0, sticky="w")
        self.combo_db = ttk.Combobox(top, state="readonly", values=[])
        self.combo_db.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(top, text="Refrescar", command=self._listar_dbs_async).grid(row=0, column=2, sticky="ew")

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

        ttk.Button(top, text="Cargar", command=self._cargar_async).grid(row=1, column=2, sticky="ew", pady=(10, 0))
        ttk.Button(top, text="Copiar", command=self._copiar).grid(row=1, column=3, sticky="ew", padx=(8, 0), pady=(10, 0))
        ttk.Button(top, text="Guardar .txt", command=self._guardar_txt).grid(row=0, column=3, sticky="ew", padx=(8, 0))

        top.columnconfigure(1, weight=1)

        self.txt = scrolledtext.ScrolledText(self, wrap=tk.WORD)
        self.txt.pack(fill="both", expand=True, padx=16, pady=(12, 16))

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

    def _tabla_actual(self) -> str:
        return "definiciones_matematicas" if self.var_concepto.get() == "Definiciones" else "proposiciones_matematicas"

    def _listar_dbs_async(self) -> None:
        def worker():
            dbs = self.controller.listar_bases_datos()

            def done():
                self.combo_db["values"] = dbs
                if self.db_sel and self.db_sel in dbs:
                    self.combo_db.set(self.db_sel)
                elif dbs and self.combo_db.get() not in dbs:
                    self.combo_db.set(dbs[0])

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _cargar_async(self) -> None:
        db = (self.combo_db.get() or "").strip()
        if not db:
            messagebox.showwarning("BD", "Selecciona una base de datos.")
            return

        tabla = self._tabla_actual()
        self.txt.delete("1.0", tk.END)
        self.txt.insert(tk.END, "Cargando...\n")

        def worker():
            try:
                data = self.controller.exportar_teoria_formato(db, tabla=tabla)
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Exportar", str(exc)))
                return

            def done():
                self.txt.delete("1.0", tk.END)
                self.txt.insert(tk.END, data)
                self.txt.focus_set()

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _copiar(self) -> None:
        data = self.txt.get("1.0", tk.END).strip()
        if not data:
            messagebox.showwarning("Copiar", "No hay contenido para copiar. Presiona 'Cargar' primero.")
            return
        self.clipboard_clear()
        self.clipboard_append(data)
        self.update_idletasks()
        messagebox.showinfo("Copiar", "Contenido copiado al portapapeles.")

    def _guardar_txt(self) -> None:
        data = self.txt.get("1.0", tk.END).strip()
        if not data:
            messagebox.showwarning("Guardar", "No hay contenido para guardar. Presiona 'Cargar' primero.")
            return
        path = filedialog.asksaveasfilename(
            title="Guardar exportacion",
            defaultextension=".txt",
            filetypes=[("Texto", "*.txt"), ("Todos", "*.*")],
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(data + "\n")
        messagebox.showinfo("Guardar", f"Guardado en:\n{path}")
