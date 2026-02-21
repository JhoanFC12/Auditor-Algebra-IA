import tkinter as tk

from .gui_cargador import LoaderWindow


def main() -> None:
    try:
        from tkinterdnd2 import TkinterDnD  # type: ignore
        root = TkinterDnD.Tk()
    except Exception:
        root = tk.Tk()
    root.withdraw()
    LoaderWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
