import tkinter as tk
from tkinter import ttk, messagebox
from tkinterdnd2 import TkinterDnD
import os
from dotenv import load_dotenv
load_dotenv()

# --- IMPORTACIÓN DE LOS MÓDULOS ---
# Asegúrate de que las carpetas y archivos existan como los creamos
try:
    from modulos.modulo1_cargador.gui_cargador import LoaderWindow
    from modulos.modulo2_auditor.gui_auditor import AuditorWindow
    from modulos.modulo2_auditor.gui_teoria import TheoryUploaderWindow
except ImportError as e:
    print(f"❌ Error de importación: {e}")
    print("Verifica que existan los archivos __init__.py en las carpetas 'modulos', 'modulo1_cargador' y 'modulo2_auditor'.")

class MainApp(TkinterDnD.Tk):
    def __init__(self):
        super().__init__()
        self.title("Sistema de Gestión Algebra IA")
        self.geometry("600x450")
        self.resizable(False, False)
        
        # Estilo visual simple
        style = ttk.Style(self)
        style.theme_use('clam') 
        
        self.crear_interfaz()

    def crear_interfaz(self):
        # --- HEADER ---
        frame_header = tk.Frame(self, bg="#2c3e50", height=80)
        frame_header.pack(fill="x")
        
        lbl_titulo = tk.Label(frame_header, text="Panel de Control Principal", 
                              fg="white", bg="#2c3e50", font=("Arial", 18, "bold"))
        lbl_titulo.pack(pady=20)

        # --- CONTENEDOR DE BOTONES ---
        frame_body = tk.Frame(self, bg="#ecf0f1", padx=20, pady=20)
        frame_body.pack(fill="both", expand=True)

        # Botón MÓDULO 1
        btn_mod1 = tk.Button(frame_body, text="📂 1. Cargar Archivos (.tex)", 
                             bg="#3498db", fg="white", font=("Arial", 12, "bold"),
                             command=self.abrir_modulo_1, height=2)
        btn_mod1.pack(fill="x", pady=10)

        # Botón TEORÍA (Auxiliar del Módulo 2)
        btn_teoria = tk.Button(frame_body, text="📚 Cargar Base de Conocimiento (Teoría)", 
                               bg="#e67e22", fg="white", font=("Arial", 11),
                               command=self.abrir_modulo_teoria, height=1)
        btn_teoria.pack(fill="x", pady=(5, 10))

        # Botón MÓDULO 2 (EL NUEVO)
        btn_mod2 = tk.Button(frame_body, text="🤖 2. Auditoría Híbrida (IA + Humano)", 
                             bg="#8e44ad", fg="white", font=("Arial", 12, "bold"),
                             command=self.abrir_modulo_2, height=2)
        btn_mod2.pack(fill="x", pady=10)
        
        # Footer
        lbl_footer = tk.Label(frame_body, text="Proyecto Algebra IA - v2.0", bg="#ecf0f1", fg="#7f8c8d")
        lbl_footer.pack(side="bottom", pady=10)

    # --- FUNCIONES DE APERTURA ---

    def abrir_modulo_1(self):
        try:
            LoaderWindow(self)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo abrir el Módulo 1:\n{e}")

    def abrir_modulo_teoria(self):
        # Por defecto usamos la BD 'matematicas', pero podrías pedir elegir
        try:
            TheoryUploaderWindow(self, "matematicas")
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo abrir el Cargador de Teoría:\n{e}")

    def abrir_modulo_2(self):
        try:
            AuditorWindow(self)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo abrir el Módulo 2:\n{e}")

if __name__ == "__main__":
    app = MainApp()
    app.mainloop()