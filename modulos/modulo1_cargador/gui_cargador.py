import tkinter as tk
from tkinter import ttk, filedialog, messagebox


class LoaderWindow(tk.Toplevel):
    """Ventana placeholder para el Módulo 1 (carga de archivos .tex)."""

    def __init__(self, master=None):
        super().__init__(master)
        self.title("Módulo 1 · Cargar archivos LaTeX")
        self.geometry("450x250")
        self.resizable(False, False)

        ttk.Label(self, text="Selecciona archivos .tex para procesar", font=("Arial", 12, "bold"))\
            .pack(pady=(20, 10))

        ttk.Button(self, text="Elegir archivos", command=self.elegir_archivos).pack(pady=5)
        self.lbl_archivos = ttk.Label(self, text="Ningún archivo seleccionado", wraplength=400)
        self.lbl_archivos.pack(pady=10)

        ttk.Button(self, text="Procesar", command=self.procesar_archivos).pack(pady=10)

        ttk.Label(self, text="Este módulo es un ejemplo. Reemplaza la lógica según tus necesidades.",
                  wraplength=420, foreground="#7f8c8d").pack(pady=(10, 0))

    def elegir_archivos(self):
        rutas = filedialog.askopenfilenames(filetypes=[("Archivos LaTeX", "*.tex"), ("Todos", "*.*")])
        if rutas:
            self.rutas_archivos = list(rutas)
            self.lbl_archivos.config(text="\n".join(self.rutas_archivos))
        else:
            self.rutas_archivos = []
            self.lbl_archivos.config(text="Ningún archivo seleccionado")

    def procesar_archivos(self):
        if not getattr(self, "rutas_archivos", []):
            messagebox.showwarning("Aviso", "Selecciona al menos un archivo .tex antes de procesar.")
            return
        # Aquí puedes invocar core.parser_latex u otra lógica real
        messagebox.showinfo("Procesado", f"Se procesarían {len(self.rutas_archivos)} archivo(s).")
