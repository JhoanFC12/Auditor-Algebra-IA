from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import ImageTk

from .executor import ExperimentalSvgExecutor
from .inventory import build_inventory, inventory_to_prompt_context
from .planner import RuleBasedPlanner
from .preview_renderer import render_svg_preview
from .voice import ContinuousVoiceListener, voice_status


class SvgAiAssistantLabWindow(tk.Tk):
    """Standalone lab UI. It does not import or modify the production SVG editor."""

    DEFAULT_INSTRUCTION = "proyecta A sobre BC"

    def __init__(self) -> None:
        super().__init__()
        self.title("Laboratorio Asistente IA - SVG")
        self.geometry("1280x820")
        self.minsize(980, 640)

        self._current_path: Path | None = None
        self._last_output: str = ""
        self._last_plan_json: str = ""
        self._point_names: list[str] = []
        self._segment_names: list[str] = []
        self._preview_photo: ImageTk.PhotoImage | None = None
        self._voice_listener: ContinuousVoiceListener | None = None
        self._voice_received_text = False

        self._build_layout()

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        top = ttk.Frame(self, padding=10)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        ttk.Button(top, text="Cargar SVG", command=self.load_svg).grid(row=0, column=0, padx=(0, 8))
        self.path_var = tk.StringVar(value="Sin archivo cargado")
        ttk.Entry(top, textvariable=self.path_var, state="readonly").grid(row=0, column=1, sticky="ew")
        ttk.Button(top, text="Guardar salida SVG", command=self.save_output_svg).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(top, text="Guardar plan JSON", command=self.save_plan_json).grid(row=0, column=3, padx=(8, 0))

        main = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

        left = ttk.Frame(main)
        preview = ttk.Frame(main)
        right = ttk.Frame(main)
        main.add(left, weight=3)
        main.add(preview, weight=3)
        main.add(right, weight=2)

        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        ttk.Label(left, text="SVG de entrada").grid(row=0, column=0, sticky="w")
        self.input_text = tk.Text(left, wrap="none", undo=True)
        self.input_text.grid(row=1, column=0, sticky="nsew")
        self._add_scrollbars(left, self.input_text, row=1, column=0)

        instruction_box = ttk.LabelFrame(left, text="Instruccion para el asistente", padding=8)
        instruction_box.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        instruction_box.columnconfigure(0, weight=1)
        self.instruction_var = tk.StringVar(value=self.DEFAULT_INSTRUCTION)
        ttk.Entry(instruction_box, textvariable=self.instruction_var).grid(row=0, column=0, sticky="ew")
        self.voice_button = ttk.Button(instruction_box, text="Iniciar voz", command=self.toggle_voice)
        self.voice_button.grid(row=0, column=1, padx=(8, 0))
        ttk.Button(instruction_box, text="Generar plan", command=self.generate_plan).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(instruction_box, text="Ejecutar sobre copia", command=self.execute_plan).grid(row=0, column=3, padx=(8, 0))

        preview.rowconfigure(1, weight=1)
        preview.columnconfigure(0, weight=1)
        preview_header = ttk.Frame(preview)
        preview_header.grid(row=0, column=0, sticky="ew")
        preview_header.columnconfigure(0, weight=1)
        ttk.Label(preview_header, text="Visualizador SVG").grid(row=0, column=0, sticky="w")
        ttk.Button(preview_header, text="Actualizar vista", command=self.refresh_preview).grid(row=0, column=1)
        ttk.Button(preview_header, text="Ver salida", command=self.preview_output).grid(row=0, column=2, padx=(8, 0))
        self.preview_canvas = tk.Canvas(preview, bg="white", highlightthickness=1, highlightbackground="#c9ced6")
        self.preview_canvas.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        preview_yscroll = ttk.Scrollbar(preview, orient="vertical", command=self.preview_canvas.yview)
        preview_xscroll = ttk.Scrollbar(preview, orient="horizontal", command=self.preview_canvas.xview)
        self.preview_canvas.configure(yscrollcommand=preview_yscroll.set, xscrollcommand=preview_xscroll.set)
        preview_yscroll.grid(row=1, column=1, sticky="ns", pady=(4, 0))
        preview_xscroll.grid(row=2, column=0, sticky="ew")

        right.rowconfigure(1, weight=1)
        right.rowconfigure(4, weight=1)
        right.rowconfigure(6, weight=1)
        right.columnconfigure(0, weight=1)

        ttk.Label(right, text="Inventario detectado").grid(row=0, column=0, sticky="w")
        self.inventory_text = tk.Text(right, height=10, wrap="word")
        self.inventory_text.grid(row=1, column=0, sticky="nsew")
        self._add_scrollbars(right, self.inventory_text, row=1, column=0)

        quick = ttk.LabelFrame(right, text="Acciones rapidas asistidas", padding=8)
        quick.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        quick.columnconfigure(1, weight=1)
        quick.columnconfigure(3, weight=1)

        self.source_var = tk.StringVar()
        self.target_var = tk.StringVar()
        self.segment_var = tk.StringVar()
        self.size_var = tk.StringVar(value="40")
        self.stroke_var = tk.StringVar(value="3")

        ttk.Label(quick, text="Origen").grid(row=0, column=0, sticky="w")
        self.source_combo = ttk.Combobox(quick, textvariable=self.source_var, values=[], width=12)
        self.source_combo.grid(row=0, column=1, sticky="ew", padx=(4, 8))
        ttk.Label(quick, text="Destino").grid(row=0, column=2, sticky="w")
        self.target_combo = ttk.Combobox(quick, textvariable=self.target_var, values=[], width=12)
        self.target_combo.grid(row=0, column=3, sticky="ew", padx=(4, 0))
        ttk.Button(quick, text="Proyectar", command=self.quick_projection).grid(row=0, column=4, padx=(8, 0))

        ttk.Label(quick, text="Segmento").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.segment_combo = ttk.Combobox(quick, textvariable=self.segment_var, values=[], width=12)
        self.segment_combo.grid(row=1, column=1, sticky="ew", padx=(4, 8), pady=(6, 0))
        ttk.Button(quick, text="Marcar", command=self.quick_mark_segment).grid(row=1, column=2, padx=(0, 8), pady=(6, 0))
        ttk.Button(quick, text="Sombrear contorno", command=self.quick_shade_hint).grid(
            row=1, column=3, sticky="ew", pady=(6, 0)
        )

        ttk.Label(quick, text="Letras").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(quick, textvariable=self.size_var, width=8).grid(row=2, column=1, sticky="w", padx=(4, 8), pady=(6, 0))
        ttk.Button(quick, text="Tamano etiquetas", command=self.quick_label_size).grid(
            row=2, column=2, padx=(0, 8), pady=(6, 0)
        )
        ttk.Label(quick, text="Grosor").grid(row=2, column=3, sticky="e", pady=(6, 0))
        ttk.Entry(quick, textvariable=self.stroke_var, width=8).grid(row=2, column=4, sticky="w", padx=(4, 0), pady=(6, 0))
        ttk.Button(quick, text="Aplicar grosor", command=self.quick_stroke).grid(row=3, column=4, sticky="ew", pady=(6, 0))

        ttk.Label(right, text="Plan JSON / salida").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.output_text = tk.Text(right, wrap="none")
        self.output_text.grid(row=4, column=0, sticky="nsew")
        self._add_scrollbars(right, self.output_text, row=4, column=0)

        ttk.Label(right, text="Historial").grid(row=5, column=0, sticky="w", pady=(8, 0))
        self.history_text = tk.Text(right, height=5, wrap="word")
        self.history_text.grid(row=6, column=0, sticky="nsew")
        self._add_scrollbars(right, self.history_text, row=6, column=0)

        status = ttk.Frame(self, padding=(10, 0, 10, 8))
        status.grid(row=2, column=0, sticky="ew")
        status.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="Listo. Carga un SVG o pegalo en el panel izquierdo.")
        ttk.Label(status, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        self._refresh_voice_status()

    def _add_scrollbars(self, parent: ttk.Frame, widget: tk.Text, *, row: int, column: int) -> None:
        yscroll = ttk.Scrollbar(parent, orient="vertical", command=widget.yview)
        xscroll = ttk.Scrollbar(parent, orient="horizontal", command=widget.xview)
        widget.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        yscroll.grid(row=row, column=column + 1, sticky="ns")
        xscroll.grid(row=row + 1, column=column, sticky="ew")

    def load_svg(self) -> None:
        filename = filedialog.askopenfilename(
            title="Cargar SVG",
            filetypes=[("SVG", "*.svg"), ("Todos los archivos", "*.*")],
        )
        if not filename:
            return
        path = Path(filename)
        try:
            svg_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("No se pudo cargar", str(exc))
            return
        self._current_path = path
        self.path_var.set(str(path))
        self.input_text.delete("1.0", tk.END)
        self.input_text.insert("1.0", svg_text)
        self.refresh_inventory()
        self.refresh_preview()

    def refresh_inventory(self) -> None:
        svg_text = self.input_text.get("1.0", tk.END).strip()
        self.inventory_text.delete("1.0", tk.END)
        if not svg_text:
            self.status_var.set("No hay SVG para analizar.")
            return
        try:
            inventory = build_inventory(svg_text)
        except Exception as exc:  # noqa: BLE001 - lab UI should show parse failures.
            self.status_var.set("El SVG no se pudo analizar.")
            self.inventory_text.insert("1.0", str(exc))
            return
        self._point_names = [point.name for point in inventory.points]
        self._segment_names = [segment.name for segment in inventory.segments]
        self._refresh_combos()
        self.inventory_text.insert("1.0", inventory_to_prompt_context(inventory))
        self.status_var.set(
            f"Inventario actualizado: {len(inventory.points)} punto(s), "
            f"{len(inventory.segments)} segmento(s)."
        )

    def refresh_preview(self) -> None:
        self._render_preview_from_text(self.input_text.get("1.0", tk.END).strip())

    def preview_output(self) -> None:
        if not self._last_output:
            messagebox.showwarning("Sin salida", "Primero ejecuta una instruccion sobre el SVG.")
            return
        self._render_preview_from_text(self._last_output)

    def _render_preview_from_text(self, svg_text: str) -> None:
        self.preview_canvas.delete("all")
        if not svg_text:
            self.preview_canvas.create_text(20, 20, anchor="nw", text="No hay SVG para visualizar.", fill="#566178")
            return
        try:
            image = render_svg_preview(svg_text)
        except Exception as exc:  # noqa: BLE001 - preview should never stop the lab.
            self.preview_canvas.create_text(
                20,
                20,
                anchor="nw",
                text=f"No se pudo renderizar la vista previa:\n{exc}",
                fill="#a33",
                width=420,
            )
            return
        self._preview_photo = ImageTk.PhotoImage(image)
        self.preview_canvas.create_image(0, 0, anchor="nw", image=self._preview_photo)
        self.preview_canvas.configure(scrollregion=(0, 0, image.width, image.height))

    def _refresh_combos(self) -> None:
        source_values = [*self._point_names, *self._segment_names]
        self.source_combo.configure(values=source_values)
        self.target_combo.configure(values=self._segment_names)
        self.segment_combo.configure(values=self._segment_names)
        if source_values and not self.source_var.get():
            self.source_var.set(source_values[0])
        if self._segment_names and not self.target_var.get():
            self.target_var.set(self._segment_names[0])
        if self._segment_names and not self.segment_var.get():
            self.segment_var.set(self._segment_names[0])

    def _append_history(self, text: str) -> None:
        self.history_text.insert(tk.END, f"- {text}\n")
        self.history_text.see(tk.END)

    def _set_instruction(self, text: str) -> None:
        self.instruction_var.set(text)
        self._append_history(text)

    def quick_projection(self) -> None:
        source = self.source_var.get().strip()
        target = self.target_var.get().strip()
        if not source or not target:
            messagebox.showwarning("Falta seleccion", "Elige origen y destino.")
            return
        self._set_instruction(f"proyecta {source} sobre {target}")

    def quick_mark_segment(self) -> None:
        segment = self.segment_var.get().strip()
        if not segment:
            messagebox.showwarning("Falta seleccion", "Elige un segmento.")
            return
        self._set_instruction(f"marca {segment}")

    def quick_label_size(self) -> None:
        size = self.size_var.get().strip()
        self._set_instruction(f"tamano de letras {size}")

    def quick_stroke(self) -> None:
        stroke = self.stroke_var.get().strip()
        self._set_instruction(f"grosor {stroke}")

    def quick_shade_hint(self) -> None:
        segment = self.segment_var.get().strip()
        if segment:
            self._set_instruction(f"sombrear contorno usando {segment}")
        else:
            self._set_instruction("sombrear contorno")

    def generate_plan(self) -> None:
        svg_text = self.input_text.get("1.0", tk.END).strip()
        instruction = self.instruction_var.get().strip()
        if not svg_text or not instruction:
            messagebox.showwarning("Falta informacion", "Carga un SVG y escribe una instruccion.")
            return
        try:
            inventory = build_inventory(svg_text)
            plan = RuleBasedPlanner().plan(instruction, inventory)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("No se pudo generar plan", str(exc))
            return
        self._last_plan_json = plan.to_json()
        self.output_text.delete("1.0", tk.END)
        self.output_text.insert("1.0", self._last_plan_json)
        self._append_history(f"Plan: {instruction}")
        self.status_var.set(f"Plan generado con {len(plan.operations)} operacion(es).")

    def execute_plan(self) -> None:
        svg_text = self.input_text.get("1.0", tk.END).strip()
        instruction = self.instruction_var.get().strip()
        if not svg_text or not instruction:
            messagebox.showwarning("Falta informacion", "Carga un SVG y escribe una instruccion.")
            return
        try:
            inventory = build_inventory(svg_text)
            plan = RuleBasedPlanner().plan(instruction, inventory)
            result = ExperimentalSvgExecutor().execute(svg_text, plan)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("No se pudo ejecutar", str(exc))
            return

        self._last_plan_json = plan.to_json()
        self._last_output = result.svg_text
        issue_lines = [f"{issue.level}: {issue.message}" for issue in result.issues]
        content = [
            "PLAN JSON",
            self._last_plan_json,
            "",
            "RESULTADO SVG",
            result.svg_text,
        ]
        if issue_lines:
            content.extend(["", "AVISOS", *issue_lines])
        self.output_text.delete("1.0", tk.END)
        self.output_text.insert("1.0", "\n".join(content))
        self._render_preview_from_text(result.svg_text)
        self._append_history(f"Ejecutado: {instruction}")
        self.status_var.set(f"Ejecutado sobre copia: {len(result.applied)} operacion(es) aplicada(s).")

    def toggle_voice(self) -> None:
        available, message = voice_status()
        if not available:
            self.status_var.set(message)
            self._append_history(f"Voz no disponible: {message}")
            return

        if self._voice_listener and self._voice_listener.is_running:
            self._voice_listener.stop()
            self.voice_button.configure(text="Procesando...", state="disabled")
            self.status_var.set("Procesando ultimo fragmento de voz...")
            return

        if self.instruction_var.get().strip() == self.DEFAULT_INSTRUCTION:
            self.instruction_var.set("")
        self._voice_received_text = False
        self._voice_listener = ContinuousVoiceListener(
            on_text=lambda text: self.after(0, lambda: self._voice_append_text(text)),
            on_error=lambda message: self.after(0, lambda: self._voice_failed(message)),
            on_status=lambda message: self.after(0, lambda: self.status_var.set(message)),
            on_done=lambda: self.after(0, self._voice_finished),
        )
        self._voice_listener.start()
        self.voice_button.configure(text="Detener voz")

    def _voice_failed(self, message: str) -> None:
        self.status_var.set(message)
        self._append_history(f"Voz: {message}")

    def _voice_append_text(self, text: str) -> None:
        self._voice_received_text = True
        current = self.instruction_var.get().strip()
        if current:
            self.instruction_var.set(f"{current} {text}")
        else:
            self.instruction_var.set(text)
        self._append_history(f"Voz: {text}")

    def _voice_finished(self) -> None:
        self.voice_button.configure(text="Iniciar voz", state="normal")
        instruction = self.instruction_var.get().strip()
        if not instruction or not self._voice_received_text:
            self.status_var.set("Dictado detenido. No se reconocio texto. Intenta hablar un poco mas cerca del microfono.")
            return
        self.status_var.set("Dictado terminado. Generando plan...")
        self.generate_plan()

    def _refresh_voice_status(self) -> None:
        available, message = voice_status()
        if available:
            self.voice_button.configure(state="normal")
            self._append_history(message)
        else:
            self.voice_button.configure(state="disabled")
            self._append_history(f"Voz desactivada: {message}")

    def save_output_svg(self) -> None:
        if not self._last_output:
            messagebox.showwarning("Sin salida", "Primero ejecuta una instruccion sobre el SVG.")
            return
        initial = "salida_ai.svg"
        if self._current_path:
            initial = f"{self._current_path.stem}.ai.svg"
        filename = filedialog.asksaveasfilename(
            title="Guardar SVG de salida",
            defaultextension=".svg",
            initialfile=initial,
            filetypes=[("SVG", "*.svg"), ("Todos los archivos", "*.*")],
        )
        if not filename:
            return
        Path(filename).write_text(self._last_output, encoding="utf-8")
        self.status_var.set(f"SVG guardado: {filename}")

    def save_plan_json(self) -> None:
        if not self._last_plan_json:
            messagebox.showwarning("Sin plan", "Primero genera o ejecuta un plan.")
            return
        filename = filedialog.asksaveasfilename(
            title="Guardar plan JSON",
            defaultextension=".json",
            initialfile="plan_ai_svg.json",
            filetypes=[("JSON", "*.json"), ("Todos los archivos", "*.*")],
        )
        if not filename:
            return
        Path(filename).write_text(self._last_plan_json, encoding="utf-8")
        self.status_var.set(f"Plan guardado: {filename}")


def main() -> int:
    app = SvgAiAssistantLabWindow()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
