from __future__ import annotations

import os
import subprocess
import sys
import webbrowser
from pathlib import Path


APP_TITLE = "Problem Detector Lab"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


def _bootstrap_path() -> Path:
    root = _repo_root()
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def _handoff_to_workspace_python(root: Path) -> bool:
    venv_exe = root / ".venv" / "Scripts" / "pythonw.exe"
    if not venv_exe.exists():
        return False
    current = Path(sys.executable).resolve()
    try:
        if current == venv_exe.resolve() or root in current.parents:
            return False
    except Exception:
        pass
    env = os.environ.copy()
    env["AUDITOR_PROBLEM_DETECTOR_LAB_HANDOFF"] = "1"
    subprocess.Popen([str(venv_exe), str(Path(__file__).resolve())], cwd=str(root), env=env)
    return True


def main() -> None:
    root = _bootstrap_path()
    if not os.getenv("AUDITOR_PROBLEM_DETECTOR_LAB_HANDOFF") and _handoff_to_workspace_python(root):
        return

    import tkinter as tk
    from tkinter import messagebox

    from modulos.problem_detector_lab.server import ProblemDetectorLabServer, default_dataset_root

    try:
        server = ProblemDetectorLabServer(dataset_root=default_dataset_root(), host="127.0.0.1")
        url = server.start(open_browser=True)
    except Exception as exc:
        messagebox.showerror(APP_TITLE, str(exc))
        raise

    window = tk.Tk()
    window.title(APP_TITLE)
    window.geometry("560x190")
    window.resizable(False, False)

    tk.Label(window, text="Problem Detector Lab esta en ejecucion.", font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=18, pady=(18, 6))
    link = tk.Label(window, text=url, fg="#0b65c2", cursor="hand2")
    link.pack(anchor="w", padx=18)
    link.bind("<Button-1>", lambda _event: webbrowser.open(url))
    tk.Label(window, text=str(default_dataset_root()), wraplength=520, justify="left").pack(anchor="w", padx=18, pady=(8, 14))

    buttons = tk.Frame(window)
    buttons.pack(fill="x", padx=18)
    tk.Button(buttons, text="Abrir navegador", command=lambda: webbrowser.open(url)).pack(side="left")
    tk.Button(buttons, text="Cerrar servidor", command=lambda: (server.stop(), window.destroy())).pack(side="right")

    window.protocol("WM_DELETE_WINDOW", lambda: (server.stop(), window.destroy()))
    window.mainloop()


if __name__ == "__main__":
    main()
