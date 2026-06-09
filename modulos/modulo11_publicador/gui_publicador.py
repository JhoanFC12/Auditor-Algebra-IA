from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from database.problem_change_queue import ProblemChangeQueueController
from database.connection import read_db_profile_config
from utils.styles import apply_openai_theme


class PublishQueueWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Modulo 11 - Publicar cambios al servidor")
        self.geometry("1180x720")
        self.minsize(980, 620)

        self.controller = ProblemChangeQueueController()
        self.palette = apply_openai_theme(self)

        self.status_var = tk.StringVar(value="Cargando cambios pendientes...")
        self.summary_var = tk.StringVar(value="")
        self._rows: list[dict] = []
        self._publishing = False

        self._build_ui()
        self._load_rows()

    def _build_ui(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x", padx=18, pady=(16, 4))
        ttk.Label(header, text="Modulo 11 - Publicar cambios al servidor", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Trabaja en local y publica cuando estes listo. El publicador sube libros, instancias y luego solo los problemas pendientes.",
            style="SubHeader.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        top = ttk.Frame(self, style="Card.TFrame", padding=14)
        top.pack(fill="x", padx=18, pady=(10, 0))
        ttk.Label(top, textvariable=self.status_var, style="Section.TLabel").pack(anchor="w")
        ttk.Label(top, textvariable=self.summary_var, style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

        actions = ttk.Frame(top, style="Card.TFrame")
        actions.pack(anchor="w", pady=(12, 0))
        self.refresh_btn = ttk.Button(actions, text="Refrescar", command=self._load_rows, style="Ghost.TButton")
        self.refresh_btn.pack(side="left")
        self.publish_btn = ttk.Button(actions, text="Publicar pendientes", command=self._publish_pending, style="Accent.TButton")
        self.publish_btn.pack(side="left", padx=(8, 0))

        content = ttk.Frame(self, style="Card.TFrame", padding=0)
        content.pack(fill="both", expand=True, padx=18, pady=(12, 18))
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)

        columns = ("problema_id", "numero_original", "libro_codigo", "codigo_instancia", "base_revision", "estado", "cambios", "ultima_edicion")
        self.tree = ttk.Treeview(content, columns=columns, show="headings", height=18)
        headings = {
            "problema_id": "ID local",
            "numero_original": "Numero",
            "libro_codigo": "Libro",
            "codigo_instancia": "Instancia",
            "base_revision": "Rev. base",
            "estado": "Estado",
            "cambios": "Cambios",
            "ultima_edicion": "Ultima edicion",
        }
        widths = {
            "problema_id": 80,
            "numero_original": 80,
            "libro_codigo": 220,
            "codigo_instancia": 260,
            "base_revision": 90,
            "estado": 110,
            "cambios": 80,
            "ultima_edicion": 180,
        }
        for key in columns:
            self.tree.heading(key, text=headings[key])
            self.tree.column(key, width=widths[key], stretch=key in {"libro_codigo", "codigo_instancia"})
        self.tree.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(content, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        bottom = ttk.Frame(self, style="Card.TFrame", padding=14)
        bottom.pack(fill="x", padx=18, pady=(0, 18))
        ttk.Label(
            bottom,
            text="Los conflictos no se pisan: si el servidor cambio desde tu base local, el cambio queda marcado para revision.",
            style="Muted.TLabel",
            wraplength=1040,
        ).pack(anchor="w")

    def _load_rows(self) -> None:
        if self._publishing:
            return
        try:
            rows = self.controller.list_pending()
        except Exception as exc:
            messagebox.showerror("Publicador", f"No se pudieron cargar los cambios pendientes.\n{exc}")
            return
        self._rows = rows
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            self.tree.insert(
                "",
                "end",
                values=(
                    row.get("problema_id"),
                    row.get("numero_original"),
                    row.get("libro_codigo"),
                    row.get("codigo_instancia"),
                    row.get("base_revision_version"),
                    row.get("publish_status"),
                    row.get("pending_count"),
                    row.get("last_local_change_at"),
                ),
            )
        count = len(rows)
        self.status_var.set(f"Cambios pendientes: {count}")
        if rows:
            conflicts = sum(1 for row in rows if str(row.get("publish_status") or "") == "conflict")
            errors = sum(1 for row in rows if str(row.get("publish_status") or "") == "error")
            self.summary_var.set(f"Conflictos: {conflicts} | Errores: {errors}")
        else:
            self.summary_var.set("No hay cambios pendientes en local.")

    def _publish_pending(self) -> None:
        if self._publishing:
            return
        self._publishing = True
        self.refresh_btn.state(["disabled"])
        self.publish_btn.state(["disabled"])
        self.status_var.set("Publicando cambios al servidor...")
        self.summary_var.set("Se sincronizaran libros, instancias, assets y luego la cola pendiente de problemas.")

        def worker() -> None:
            try:
                summary = self.controller.publish_pending()
                self.after(0, lambda: self._on_publish_done(summary, None))
            except Exception as exc:
                self.after(0, lambda: self._on_publish_done(None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_publish_done(self, summary: dict | None, error: Exception | None) -> None:
        self._publishing = False
        self.refresh_btn.state(["!disabled"])
        self.publish_btn.state(["!disabled"])
        if error is not None:
            self.status_var.set("No se pudo publicar al servidor.")
            friendly = self._friendly_publish_error(error)
            self.summary_var.set(friendly)
            messagebox.showerror("Publicador", f"No se pudo publicar.\n{friendly}")
            self._load_rows()
            return

        summary = summary or {}
        self.status_var.set("Publicacion terminada.")
        self.summary_var.set(
            "Libros +{books_inserted}/{books_updated} | Instancias +{instances_inserted}/{instances_updated} | "
            "Pendientes: {pending_before} | Publicados: {published} | Conflictos: {conflicts} | Fallidos: {failed}".format(
                books_inserted=int(summary.get("books_inserted") or 0),
                books_updated=int(summary.get("books_updated") or 0),
                instances_inserted=int(summary.get("instances_inserted") or 0),
                instances_updated=int(summary.get("instances_updated") or 0),
                pending_before=int(summary.get("pending_before") or 0),
                published=int(summary.get("published") or 0),
                conflicts=int(summary.get("conflicts") or 0),
                failed=int(summary.get("failed") or 0),
            )
        )
        self._load_rows()

    def _friendly_publish_error(self, error: Exception) -> str:
        message = str(error or "").strip()
        normalized = message.lower()
        if "server closed the connection unexpectedly" in normalized:
            return (
                "La conexion con PostgreSQL del servidor se cerro de forma inesperada.\n\n"
                "Suele ocurrir cuando el backend se reinicia o termina una sesion activa.\n"
                "El publicador mantiene la cola local, asi que puedes volver a intentar sin perder cambios."
            )
        if "127.0.0.1" in normalized and "15432" in normalized and "connection refused" in normalized:
            try:
                cloud = read_db_profile_config("cloud")
                host = str(cloud.get("host") or "").strip() or "127.0.0.1"
                port = str(cloud.get("port") or "").strip() or "15432"
            except Exception:
                host = "127.0.0.1"
                port = "15432"
            return (
                "No se pudo conectar al servidor.\n\n"
                f"El publicador esta configurado para usar el perfil Cloud en {host}:{port}.\n"
                "Abre primero el tunel SSH a PostgreSQL y vuelve a intentar.\n\n"
                "Si ya no quieres depender del tunel, reemplaza DB_CLOUD_HOST/DB_CLOUD_PORT "
                "por el endpoint remoto real del servidor."
            )
        return message or "Error desconocido al publicar."
