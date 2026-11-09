"""
ui/theme.py
───────────
Paleta, tipografía y pequeños componentes reutilizables de la interfaz.
Un solo sitio para los colores: antes estaban repetidos (y desalineados)
en cada widget.
"""
import tkinter as tk
import customtkinter as ctk

# ── Paleta (oscura, neutra, acento violeta) ──
BG        = "#0b0b12"   # fondo de la ventana
SURFACE   = "#13131c"   # paneles
SURFACE_2 = "#1b1b27"   # campos, botones secundarios
SURFACE_3 = "#252534"   # hover
BORDER    = "#2a2a3a"
TEXT      = "#ececf1"
MUTED     = "#9a9ab0"
SUBTLE    = "#6b6b80"
ACCENT    = "#8b5cf6"
ACCENT_H  = "#7c3aed"
GREEN     = "#10b981"
GREEN_H   = "#059669"
RED       = "#f43f5e"
RED_H     = "#e11d48"
CANVAS_BG = "#09090f"

FONT = "Segoe UI"


def font(size=12, weight="normal"):
    return ctk.CTkFont(FONT, size, weight)


def setup():
    """Fuerza tema oscuro: con el modo 'system' de CustomTkinter, en Windows en
    modo claro salían campos blancos y etiquetas oscuras sobre fondo oscuro."""
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")


# ── fábricas con estilo coherente ──
def label(parent, text, size=12, weight="normal", color=TEXT, **kw):
    return ctk.CTkLabel(parent, text=text, font=font(size, weight), text_color=color, **kw)


def section(parent, text):
    """Título de sección en versalitas discretas."""
    return label(parent, text.upper(), size=10, weight="bold", color=SUBTLE, anchor="w")


def entry(parent, **kw):
    kw.setdefault("height", 34)
    return ctk.CTkEntry(parent, corner_radius=8, fg_color=SURFACE_2, border_color=BORDER, border_width=1,
                        text_color=TEXT, placeholder_text_color=SUBTLE, font=font(12), **kw)


def option(parent, values, variable, command=None, **kw):
    kw.setdefault("height", 32)
    return ctk.CTkOptionMenu(parent, values=values, variable=variable, command=command, corner_radius=8,
                             fg_color=SURFACE_2, button_color=SURFACE_3, button_hover_color=ACCENT_H,
                             text_color=TEXT, text_color_disabled=SUBTLE, dropdown_fg_color=SURFACE_2,
                             dropdown_hover_color=SURFACE_3, dropdown_text_color=TEXT,
                             font=font(12), dropdown_font=font(12), dynamic_resizing=False, **kw)


def segmented(parent, values, variable, command=None, **kw):
    kw.setdefault("height", 30)
    return ctk.CTkSegmentedButton(parent, values=values, variable=variable, command=command, corner_radius=8,
                                  fg_color=SURFACE_2, selected_color=ACCENT, selected_hover_color=ACCENT_H,
                                  unselected_color=SURFACE_2, unselected_hover_color=SURFACE_3,
                                  text_color=TEXT, text_color_disabled=SUBTLE, font=font(12), **kw)


def button(parent, text, command=None, kind="secondary", **kw):
    styles = {
        "primary":   (ACCENT, ACCENT_H, "#ffffff"),
        "success":   (GREEN, GREEN_H, "#ffffff"),
        "secondary": (SURFACE_2, SURFACE_3, TEXT),
        "ghost":     ("transparent", SURFACE_3, MUTED),
    }
    fg, hover, txt = styles[kind]
    kw.setdefault("height", 32)
    kw.setdefault("corner_radius", 8)
    kw.setdefault("font", font(12, "bold" if kind in ("primary", "success") else "normal"))
    return ctk.CTkButton(parent, text=text, command=command, fg_color=fg, hover_color=hover,
                         text_color=txt, text_color_disabled=SUBTLE, **kw)


class ToggleButton(ctk.CTkButton):
    """Botón que refleja un BooleanVar (sustituye a los checkbox con emoji,
    cuyo icono no se veía)."""

    def __init__(self, parent, text, variable: tk.BooleanVar, command=None, **kw):
        self._var = variable
        self._user_cmd = command
        kw.setdefault("height", 32)
        kw.setdefault("corner_radius", 8)
        kw.setdefault("font", font(12))
        super().__init__(parent, text=text, command=self._toggle, text_color_disabled=SUBTLE, **kw)
        variable.trace_add("write", lambda *_: self._paint())
        self._paint()

    def _toggle(self):
        self._var.set(not self._var.get())
        if self._user_cmd:
            self._user_cmd()

    def _paint(self):
        on = bool(self._var.get())
        self.configure(fg_color=ACCENT if on else SURFACE_2,
                       hover_color=ACCENT_H if on else SURFACE_3,
                       text_color="#ffffff" if on else MUTED)


class Tooltip:
    """Tooltip ligero: aparece tras una pausa sobre el widget."""

    def __init__(self, widget, text, delay=450):
        self.widget, self.text, self.delay = widget, text, delay
        self._after = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after:
            try:
                self.widget.after_cancel(self._after)
            except Exception:
                pass
            self._after = None

    def _show(self):
        if self._tip or not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.configure(bg=BORDER)
        lbl = tk.Label(self._tip, text=self.text, bg=SURFACE_2, fg=TEXT, font=(FONT, 9),
                       padx=8, pady=4, justify="left")
        lbl.pack(padx=1, pady=1)
        self._tip.update_idletasks()
        w = self._tip.winfo_width()
        self._tip.wm_geometry(f"+{max(0, x - w // 2)}+{y}")

    def _hide(self, _e=None):
        self._cancel()
        if self._tip:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


def tip(widget, text):
    Tooltip(widget, text)
    return widget
