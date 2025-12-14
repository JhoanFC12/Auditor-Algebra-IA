import tkinter as tk
from tkinter import ttk, messagebox


class TheoryUploaderWindow(tk.Toplevel):
    """Ventana placeholder para cargar teoría/base de conocimiento."""

    def __init__(self, master=None, database_name: str | None = None):
        super().__init__(master)
        self.title("Cargar base de conocimiento")
        self.geometry("480x260")
        self.resizable(False, False)
        self.database_name = database_name

        ttk.Label(self, text="Carga de Teoría", font=("Arial", 13, "bold")).pack(pady=(20, 5))
        ttk.Label(
            self,
            text="Añade aquí formularios o dropzones para poblar tu base de conocimiento.",
            wraplength=440,
        ).pack(pady=5)

        ttk.Label(self, text=f"Base de datos objetivo: {self.database_name or 'no definida'}",
                  foreground="#7f8c8d").pack(pady=(5, 15))

        ttk.Button(self, text="Simular carga", command=self.simular_carga).pack(pady=10)

    def simular_carga(self):
        # Aquí deberías implementar la llamada a tu motor de persistencia
        destino = self.database_name or "(no especificada)"
        messagebox.showinfo("Carga", f"Se simularía la carga de teoría en la BD: {destino}")
