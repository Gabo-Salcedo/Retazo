"""
download/download_window.py
───────────────────────────
Ventana de progreso de descarga independiente.
Muestra barra animada, log en tiempo real y botón de cancelar.
"""

import tkinter as tk
from tkinter import ttk

BG     = "#0d0d1a"
BG2    = "#12122a"
BG3    = "#1a1a2e"
ACCENT = "#7c3aed"
GREEN  = "#059669"
RED    = "#dc2626"
GRAY   = "#6b7280"
TEXT   = "#e2e8f0"
YELLOW = "#f59e0b"

_SPINNERS = ["◐", "◓", "◑", "◒"]


class DownloadWindow(tk.Toplevel):
    """
    Ventana modal de progreso de descarga.

    API pública
    -----------
    set_phase(text)          → actualiza el título de fase
    set_detail(text)         → actualiza la línea de detalle
    set_progress(pct, label) → barra determinada (pct 0‒1)
    set_indeterminate(text)  → barra animada
    log(line)                → añade línea al log
    done(message)            → estado final OK
    error(message)           → estado final error
    cancelled  (property)    → True si el usuario canceló
    on_cancel  (attr)        → callable invocado al cancelar
    """

    def __init__(self, master, title_text: str = "Descarga", on_cancel=None):
        super().__init__(master)
        self.title(f"⬇  Descargando — {title_text[:55]}")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self.on_cancel   = on_cancel
        self._cancelled  = False
        self._spin_idx   = 0
        self._pb_mode    = "indeterminate"

        self._build_ui()
        self._center()
        self._animate()

    # ── construcción ─────────────────────────────────────────────
    def _build_ui(self):
        # ── cabecera ──
        hdr = tk.Frame(self, bg=BG2, pady=10)
        hdr.pack(fill="x")
        tk.Label(hdr, text="⬇  Descargando fragmento",
                 bg=BG2, fg=ACCENT,
                 font=("Segoe UI", 14, "bold")).pack(side="left", padx=14)
        self._spin_lbl = tk.Label(hdr, text="◐", bg=BG2, fg=YELLOW,
                                  font=("Segoe UI", 16))
        self._spin_lbl.pack(side="right", padx=14)

        # ── etiquetas de estado ──
        sf = tk.Frame(self, bg=BG, pady=8)
        sf.pack(fill="x", padx=14)
        self._phase_lbl = tk.Label(sf,
                                   text="⏳ Preparando…",
                                   bg=BG, fg=TEXT,
                                   font=("Segoe UI", 11, "bold"),
                                   anchor="w")
        self._phase_lbl.pack(fill="x")
        self._detail_lbl = tk.Label(sf, text="",
                                    bg=BG, fg=GRAY,
                                    font=("Segoe UI", 10),
                                    anchor="w")
        self._detail_lbl.pack(fill="x", pady=(2, 0))

        # ── barra de progreso ──
        pb_f = tk.Frame(self, bg=BG)
        pb_f.pack(fill="x", padx=14, pady=(0, 6))

        style = ttk.Style(self)
        style.theme_use("default")
        style.configure("DL.Horizontal.TProgressbar",
                        background=ACCENT, troughcolor=BG3,
                        borderwidth=0, thickness=14)

        self._pb = ttk.Progressbar(pb_f,
                                   style="DL.Horizontal.TProgressbar",
                                   mode="indeterminate",
                                   length=552)
        self._pb.pack(fill="x")
        self._pb.start(12)

        self._pct_lbl = tk.Label(pb_f, text="", bg=BG, fg=ACCENT,
                                 font=("Segoe UI", 10, "bold"),
                                 anchor="e")
        self._pct_lbl.pack(fill="x", pady=(2, 0))

        # ── separador ──
        tk.Frame(self, bg="#2d2d5e", height=1).pack(fill="x", padx=14, pady=4)

        # ── log ──
        tk.Label(self, text="📋  Log en tiempo real",
                 bg=BG, fg=GRAY,
                 font=("Segoe UI", 10)).pack(anchor="w", padx=14)

        log_wrap = tk.Frame(self, bg=BG3, padx=2, pady=2)
        log_wrap.pack(fill="both", expand=True, padx=14, pady=(4, 8))

        sb = tk.Scrollbar(log_wrap, bg=BG2, troughcolor=BG3, relief="flat")
        sb.pack(side="right", fill="y")

        self._log = tk.Text(log_wrap,
                            bg="#0a0a18", fg="#64748b",
                            font=("Courier New", 9),
                            relief="flat", state="disabled",
                            wrap="none",
                            yscrollcommand=sb.set)
        self._log.pack(side="left", fill="both", expand=True)
        sb.config(command=self._log.yview)

        # ── botón cancelar ──
        btn_f = tk.Frame(self, bg=BG, pady=8)
        btn_f.pack(fill="x", padx=14)
        self._cancel_btn = tk.Button(btn_f,
                                     text="✕  Cancelar descarga",
                                     bg="#7f1d1d", fg=TEXT,
                                     activebackground=RED, activeforeground="#fff",
                                     relief="flat",
                                     font=("Segoe UI", 11, "bold"),
                                     cursor="hand2", pady=8,
                                     command=self._cancel)
        self._cancel_btn.pack(fill="x")

    def _center(self):
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        ww = self.winfo_width()  or 580
        wh = self.winfo_height() or 460
        self.geometry(f"580x460+{(sw-ww)//2}+{(sh-wh)//2}")

    # ── animación spinner ─────────────────────────────────────────
    def _animate(self):
        if not self.winfo_exists() or self._cancelled:
            return
        try:
            self._spin_lbl.configure(text=_SPINNERS[self._spin_idx % 4])
            self._spin_idx += 1
            self.after(180, self._animate)
        except Exception:
            pass

    # ── helpers internos ─────────────────────────────────────────
    def _safe(self, fn):
        """Ejecuta fn solo si la ventana sigue viva."""
        try:
            if self.winfo_exists():
                fn()
        except Exception:
            pass

    # ── API pública ───────────────────────────────────────────────
    def set_phase(self, text: str):
        self._safe(lambda: self._phase_lbl.configure(text=text))

    def set_detail(self, text: str):
        self._safe(lambda: self._detail_lbl.configure(text=text))

    def set_progress(self, pct: float, label: str = ""):
        """Cambia a modo determinado y actualiza (pct: 0‒1)."""
        def _do():
            if self._pb_mode == "indeterminate":
                self._pb.stop()
                self._pb.configure(mode="determinate")
                self._pb_mode = "determinate"
            self._pb["value"] = min(pct * 100, 100)
            if label:
                self._pct_lbl.configure(text=label)
        self._safe(_do)

    def set_indeterminate(self, text: str = ""):
        def _do():
            if self._pb_mode == "determinate":
                self._pb.configure(mode="indeterminate")
                self._pb.start(12)
                self._pb_mode = "indeterminate"
            if text:
                self._phase_lbl.configure(text=text)
            self._pct_lbl.configure(text="")
        self._safe(_do)

    def log(self, line: str):
        def _do():
            self._log.configure(state="normal")
            self._log.insert("end", line.rstrip() + "\n")
            self._log.see("end")
            self._log.configure(state="disabled")
        self._safe(_do)

    def done(self, message: str = "✅ Descarga completada"):
        def _do():
            self._pb.stop()
            self._pb.configure(mode="determinate")
            self._pb["value"] = 100
            self._pb_mode = "determinate"
            self._phase_lbl.configure(text=message, fg=GREEN)
            self._pct_lbl.configure(text="100 %")
            self._spin_lbl.configure(text="✅", fg=GREEN)
            self._cancel_btn.configure(
                text="✓  Cerrar", bg="#064e3b",
                command=self.destroy)
        self._safe(_do)

    def error(self, message: str = "❌ Error en la descarga"):
        def _do():
            self._pb.stop()
            self._pb.configure(mode="determinate")
            self._pb["value"] = 0
            self._pb_mode = "determinate"
            self._phase_lbl.configure(text=message, fg=RED)
            self._spin_lbl.configure(text="❌", fg=RED)
            self._cancel_btn.configure(
                text="✕  Cerrar", bg="#7f1d1d",
                command=self.destroy)
        self._safe(_do)

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def _cancel(self):
        self._cancelled = True
        if callable(self.on_cancel):
            try:
                self.on_cancel()
            except Exception:
                pass
        try:
            self.destroy()
        except Exception:
            pass
