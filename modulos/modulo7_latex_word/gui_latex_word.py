from __future__ import annotations

import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from utils.styles import apply_openai_theme


class LatexWordBridgeWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Modulo 7 - LaTeX a Word")
        self.geometry("980x700")
        self.minsize(900, 620)
        self._maximize_window()

        self.repo_var = tk.StringVar(value=str(self._default_editor_repo()))
        self.python_var = tk.StringVar(value=self._detect_python(str(self._default_editor_repo())))
        self.input_tex_var = tk.StringVar(value="")
        self.output_docx_var = tk.StringVar(value="")
        self.template_var = tk.StringVar(value=self._default_template(str(self._default_editor_repo())))
        self.images_dir_var = tk.StringVar(value="")
        self.style_var = tk.StringVar(value="Estilo_plantilla")
        self.status_var = tk.StringVar(value="Listo")
        self._running = False

        self._apply_light_theme()
        self._build_ui()

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
        root = ttk.Frame(self)
        root.pack(fill="both", expand=True, padx=16, pady=14)

        ttk.Label(root, text="Modulo 7 - Integracion LaTeX a Word", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            root,
            text="Usa tu repo Editor_de_practicas sin duplicar codigo.",
            style="SubHeader.TLabel",
        ).pack(anchor="w", pady=(4, 12))

        cfg = ttk.LabelFrame(root, text="Configuracion", style="Card.TLabelframe")
        cfg.pack(fill="x")
        ttk.Label(cfg, text="Repo Editor_de_practicas").grid(row=0, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(cfg, textvariable=self.repo_var).grid(row=0, column=1, sticky="ew", padx=8, pady=8)
        ttk.Button(cfg, text="Examinar", command=self._pick_repo, style="Ghost.TButton").grid(row=0, column=2, sticky="ew", padx=(0, 8), pady=8)

        ttk.Label(cfg, text="Python ejecutable").grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))
        ttk.Entry(cfg, textvariable=self.python_var).grid(row=1, column=1, sticky="ew", padx=8, pady=(0, 8))
        ttk.Button(cfg, text="Elegir", command=self._pick_python, style="Ghost.TButton").grid(row=1, column=2, sticky="ew", padx=(0, 8), pady=(0, 8))
        cfg.columnconfigure(1, weight=1)

        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=(12, 8))
        ttk.Button(
            actions,
            text="Abrir GUI original (Editor_de_practicas)",
            command=self._open_external_gui,
            style="Secondary.TButton",
        ).pack(side="left")

        quick = ttk.LabelFrame(root, text="Conversion rapida", style="Card.TLabelframe")
        quick.pack(fill="x", pady=(8, 0))

        ttk.Label(quick, text="Input .tex").grid(row=0, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(quick, textvariable=self.input_tex_var).grid(row=0, column=1, sticky="ew", padx=8, pady=8)
        ttk.Button(quick, text="Elegir", command=self._pick_input_tex, style="Ghost.TButton").grid(row=0, column=2, sticky="ew", padx=(0, 8), pady=8)

        ttk.Label(quick, text="Output .docx").grid(row=1, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(quick, textvariable=self.output_docx_var).grid(row=1, column=1, sticky="ew", padx=8, pady=8)
        ttk.Button(quick, text="Guardar como", command=self._pick_output_docx, style="Ghost.TButton").grid(row=1, column=2, sticky="ew", padx=(0, 8), pady=8)

        ttk.Label(quick, text="Template .docx").grid(row=2, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(quick, textvariable=self.template_var).grid(row=2, column=1, sticky="ew", padx=8, pady=8)
        ttk.Button(quick, text="Elegir", command=self._pick_template, style="Ghost.TButton").grid(row=2, column=2, sticky="ew", padx=(0, 8), pady=8)

        ttk.Label(quick, text="Carpeta imagenes").grid(row=3, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(quick, textvariable=self.images_dir_var).grid(row=3, column=1, sticky="ew", padx=8, pady=8)
        ttk.Button(quick, text="Elegir", command=self._pick_images_dir, style="Ghost.TButton").grid(row=3, column=2, sticky="ew", padx=(0, 8), pady=8)

        ttk.Label(quick, text="Style").grid(row=4, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(quick, textvariable=self.style_var).grid(row=4, column=1, sticky="ew", padx=8, pady=8)
        ttk.Button(quick, text="Convertir", command=self._convert_async, style="Accent.TButton").grid(row=4, column=2, sticky="ew", padx=(0, 8), pady=8)

        quick.columnconfigure(1, weight=1)

        ttk.Label(root, text="Log", style="Section.TLabel").pack(anchor="w", pady=(12, 4))
        self.log = tk.Text(
            root,
            height=15,
            bg=self.palette["surface"],
            fg=self.palette["text"],
            insertbackground=self.palette["text"],
            relief="solid",
            bd=1,
            wrap="word",
            highlightthickness=1,
            highlightbackground=self.palette["border"],
        )
        self.log.pack(fill="both", expand=True)

        ttk.Label(root, textvariable=self.status_var).pack(anchor="w", pady=(8, 0))

    def _default_editor_repo(self) -> Path:
        default = Path(r"E:\Github\Editor_de_practicas")
        if default.exists():
            return default
        return Path.cwd()

    def _default_template(self, repo: str) -> str:
        candidate = Path(repo) / "plantilla.docx"
        return str(candidate) if candidate.exists() else ""

    def _python_candidates(self, repo: str, preferred: str = "") -> list[str]:
        out: list[str] = []
        seen: set[str] = set()

        def add(value: str) -> None:
            v = str(value or "").strip()
            if not v:
                return
            key = v.lower()
            if key in seen:
                return
            seen.add(key)
            out.append(v)

        repo_path = Path(repo)
        add(preferred)
        add(str(repo_path / ".venv" / "Scripts" / "python.exe"))
        add(str(repo_path / "venv" / "Scripts" / "python.exe"))
        add(str(Path.cwd() / ".venv" / "Scripts" / "python.exe"))
        add(str(Path.cwd() / "venv" / "Scripts" / "python.exe"))
        add(str(Path(sys.executable)))
        add("python")
        return out

    def _probe_python(self, exe: str) -> tuple[bool, str]:
        cmd = [str(exe), "-c", "import sys; print(sys.executable)"]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
            )
        except Exception as exc:
            return (False, str(exc))
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout or f"exit={proc.returncode}").strip()
            return (False, msg)
        ok_msg = (proc.stdout or "").strip() or str(exe)
        return (True, ok_msg)

    def _resolve_python(self, repo: str, preferred: str = "") -> tuple[str, list[str]]:
        errors: list[str] = []
        for candidate in self._python_candidates(repo, preferred):
            is_path_like = ("\\" in candidate) or ("/" in candidate) or candidate.lower().endswith(".exe")
            if is_path_like:
                p = Path(candidate)
                if not p.exists():
                    errors.append(f"{candidate}: no existe")
                    continue
            ok, msg = self._probe_python(candidate)
            if ok:
                return (candidate, errors)
            errors.append(f"{candidate}: {msg}")
        # Fallback final (deberia existir siempre en esta app)
        return (str(Path(sys.executable)), errors)

    def _detect_python(self, repo: str) -> str:
        py, _errs = self._resolve_python(repo, "")
        return py

    def _log(self, text: str) -> None:
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.update_idletasks()

    def _pick_repo(self) -> None:
        selected = filedialog.askdirectory(title="Selecciona Editor_de_practicas")
        if not selected:
            return
        self.repo_var.set(selected)
        self.python_var.set(self._detect_python(selected))
        self.template_var.set(self._default_template(selected))

    def _pick_python(self) -> None:
        selected = filedialog.askopenfilename(
            title="Selecciona python.exe",
            filetypes=[("Python", "python.exe"), ("Todos", "*.*")],
        )
        if selected:
            self.python_var.set(selected)

    def _pick_input_tex(self) -> None:
        selected = filedialog.askopenfilename(title="Selecciona archivo .tex", filetypes=[("TeX", "*.tex")])
        if not selected:
            return
        self.input_tex_var.set(selected)
        if not self.output_docx_var.get().strip():
            self.output_docx_var.set(str(Path(selected).with_suffix(".docx")))

    def _pick_output_docx(self) -> None:
        selected = filedialog.asksaveasfilename(
            title="Guardar como .docx",
            defaultextension=".docx",
            filetypes=[("Word", "*.docx")],
        )
        if selected:
            self.output_docx_var.set(selected)

    def _pick_template(self) -> None:
        selected = filedialog.askopenfilename(title="Selecciona plantilla .docx", filetypes=[("Word", "*.docx")])
        if selected:
            self.template_var.set(selected)

    def _pick_images_dir(self) -> None:
        selected = filedialog.askdirectory(title="Selecciona carpeta de imagenes")
        if selected:
            self.images_dir_var.set(selected)

    def _open_external_gui(self) -> None:
        repo = Path(self.repo_var.get().strip())
        py, errors = self._resolve_python(str(repo), self.python_var.get().strip())
        self.python_var.set(py)
        script = repo / "latex_to_word_gui.py"
        if not script.exists():
            messagebox.showerror("Modulo 7", f"No existe script:\n{script}")
            return
        if errors:
            self._log("Aviso: algunos python candidatos fallaron. Se usa fallback valido.")
            for err in errors[:4]:
                self._log(f" - {err}")
        self._log(f"Abriendo GUI externa: {script}")
        try:
            subprocess.Popen([str(py), str(script)], cwd=str(repo))
            self.status_var.set("GUI externa iniciada")
        except Exception as exc:
            messagebox.showerror("Modulo 7", str(exc))

    def _convert_async(self) -> None:
        if self._running:
            messagebox.showwarning("Modulo 7", "Ya hay una conversion en curso.")
            return
        repo = Path(self.repo_var.get().strip())
        py, errors = self._resolve_python(str(repo), self.python_var.get().strip())
        self.python_var.set(py)
        script = repo / "latex_to_word.py"
        input_tex = Path(self.input_tex_var.get().strip())
        output_docx = Path(self.output_docx_var.get().strip())
        template = Path(self.template_var.get().strip()) if self.template_var.get().strip() else None
        images_dir = Path(self.images_dir_var.get().strip()) if self.images_dir_var.get().strip() else None
        style = (self.style_var.get() or "").strip() or "Estilo_plantilla"

        if not script.exists():
            messagebox.showerror("Modulo 7", f"No existe script:\n{script}")
            return
        if not input_tex.exists():
            messagebox.showerror("Modulo 7", "El archivo .tex no existe.")
            return
        if not output_docx.parent.exists():
            output_docx.parent.mkdir(parents=True, exist_ok=True)

        cmd = [str(py), str(script), str(input_tex), str(output_docx), "--style", style]
        if template and template.exists():
            cmd.extend(["--template", str(template)])
        if images_dir and images_dir.exists():
            cmd.extend(["--images-dir", str(images_dir)])

        self._running = True
        self.status_var.set("Convirtiendo...")
        if errors:
            self._log("Aviso: algunos python candidatos fallaron. Se usa fallback valido.")
            for err in errors[:4]:
                self._log(f" - {err}")
        self._log("Comando:")
        self._log(" ".join(cmd))

        def worker() -> None:
            ok = False
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(repo),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                if proc.stdout.strip():
                    self.after(0, lambda: self._log(proc.stdout))
                if proc.stderr.strip():
                    self.after(0, lambda: self._log(proc.stderr))
                ok = proc.returncode == 0 and output_docx.exists()
            except Exception as exc:
                self.after(0, lambda: self._log(f"Error ejecutando conversion: {exc}"))

            def done() -> None:
                self._running = False
                if ok:
                    self.status_var.set("Conversion completada")
                    messagebox.showinfo("Modulo 7", f"Word generado:\n{output_docx}")
                else:
                    self.status_var.set("Conversion con error")
                    messagebox.showerror("Modulo 7", "No se pudo generar el Word. Revisa el log.")

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()
