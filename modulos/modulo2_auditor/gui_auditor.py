import tkinter as tk
from tkinter import ttk, messagebox


class AuditorWindow(tk.Toplevel):
    """Ventana placeholder para la auditoría híbrida."""

    def __init__(self, master=None):
        super().__init__(master)
        self.title("Módulo 2 · Auditoría IA + Humano")
        self.geometry("500x300")
        self.resizable(False, False)

        ttk.Label(self, text="Auditoría de ejercicios", font=("Arial", 13, "bold")).pack(pady=(20, 5))
        ttk.Label(self, text="Esta pantalla es un esqueleto. Integra aquí tu flujo de revisión.",
                  wraplength=460).pack(pady=5)

        ttk.Button(self, text="Simular verificación", command=self.simular).pack(pady=15)
        self.lbl_estado = ttk.Label(self, text="Pendiente", foreground="#7f8c8d")
        self.lbl_estado.pack(pady=(0, 10))

    def simular(self):
        # Lógica de ejemplo: aquí podrías invocar al motor RAG o a la BD
        self.lbl_estado.config(text="Resultado: listo para revisión", foreground="#27ae60")
        messagebox.showinfo("Resultado", "Ejemplo de flujo de auditoría completado.")
