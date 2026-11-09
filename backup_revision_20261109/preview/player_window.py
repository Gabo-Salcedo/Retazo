"""
preview/player_window.py
────────────────────────
Preview de video usando ffmpeg (sin VLC).
Decodifica frames via subprocess pipe y los muestra en un Canvas de Tk.
"""

import tkinter as tk
import threading
import time
import os
import sys
from tkinter import ttk, messagebox
from PIL import Image, ImageTk

try:
    from ffpyplayer.player import MediaPlayer
    from ffpyplayer.pic import SWScale
except ImportError:
    pass

# ──────────────────────────────────────────────────────────────────────────────
# Colores / dimensiones
# ──────────────────────────────────────────────────────────────────────────────
BG       = "#0d0d1a"
BG2      = "#12122a"
BG3      = "#1a1a2e"
ACCENT   = "#7c3aed"
ACCENT_H = "#a78bfa"
GREEN    = "#059669"
RED      = "#dc2626"
GRAY     = "#6b7280"
TEXT     = "#e2e8f0"

VIDEO_W  = 640
VIDEO_H  = 360
BAR_H    = 56
HANDLE_R = 9
PAD_X    = 14
FPS      = 25


def _fmt(sec: float) -> str:
    sec = max(0.0, float(sec))
    h = int(sec // 3600); m = int((sec % 3600) // 60); s = int(sec % 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _parse(text: str) -> float:
    parts = text.strip().split(":")
    try:
        if len(parts) == 2:  return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:  return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except Exception:
        pass
    return 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Barra de tiempo con gizmos
# ──────────────────────────────────────────────────────────────────────────────
class TimelineBar(tk.Canvas):
    """Canvas con gizmos arrastrables de inicio (▶) y fin (◀)."""

    def __init__(self, master, duration: float, on_change=None,
                 on_seek=None, **kw):
        kw.setdefault("height", BAR_H)
        kw.setdefault("bg", BG3)
        kw.setdefault("highlightthickness", 0)
        super().__init__(master, **kw)

        self.duration  = max(duration, 1.0)
        self.start_sec = 0.0
        self.end_sec   = self.duration
        self.head_sec  = 0.0
        self.on_change = on_change   # callback(start, end)
        self.on_seek   = on_seek     # callback(sec) al click en track

        self._drag = None
        self._hover = None
        self._tooltip_text = ""
        self._tooltip_x = 0
        
        self.zoomed = False
        self.view_start = 0.0
        self.view_end = self.duration

        self.bind("<Configure>",       lambda _e: self._redraw())
        self.bind("<ButtonPress-1>",   self._press)
        self.bind("<B1-Motion>",       self._drag_move)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Motion>",          self._hover_move)
        self.bind("<Leave>",           self._leave)

        self.after(50, self._redraw)

    # ── helpers ──────────────────────────────────────────────────
    def _bw(self): return max(self.winfo_width() - 2 * PAD_X, 1)
    
    @property
    def current_view_start(self):
        return getattr(self, "zoom_start", 0.0) if getattr(self, "zoomed", False) else 0.0

    @property
    def current_view_end(self):
        return getattr(self, "zoom_end", self.duration) if getattr(self, "zoomed", False) else self.duration

    def _s2x(self, s):
        vs = self.current_view_start
        ve = self.current_view_end
        vdur = ve - vs
        if vdur <= 0: return PAD_X
        return PAD_X + int(((s - vs) / vdur) * self._bw())

    def _x2s(self, x):
        vs = self.current_view_start
        ve = self.current_view_end
        vdur = ve - vs
        if vdur <= 0: return vs
        val = vs + ((x - PAD_X) / self._bw()) * vdur
        return max(0.0, min(self.duration, val))

    def toggle_zoom(self):
        self.zoomed = not getattr(self, "zoomed", False)
        if self.zoomed:
            pad = max((self.end_sec - self.start_sec) * 0.05, 0.5)
            self.zoom_start = max(0.0, self.start_sec - pad)
            self.zoom_end = min(self.duration, self.end_sec + pad)
        self._redraw()

    # ── render ───────────────────────────────────────────────────
    def _redraw(self):
        self.delete("all")
        w = self.winfo_width() or VIDEO_W

        # track fondo (línea delgada con bordes redondeados)
        bg_color = "#4c1d95" if getattr(self, "zoomed", False) else "#2d2d5e"
        self.create_line(PAD_X, 28, w - PAD_X, 28,
                         fill=bg_color, width=8, capstyle="round")
        
        # zona seleccionada
        xs, xe = self._s2x(self.start_sec), self._s2x(self.end_sec)
        self.create_line(xs, 28, xe, 28, fill="#8b5cf6", width=8, capstyle="round") # Purple

        # gizmos (inicio / fin) hacia abajo
        self._handle(xs, "#10b981", "start", "gs") # Verde
        self._handle(xe, "#f43f5e", "end",   "ge") # Rojo-rosa

        # playhead hacia arriba
        xh = self._s2x(self.head_sec)
        self.create_line(xh, 16, xh, 28, fill="#f9fafb", width=2)
        self.create_oval(xh-4, 12, xh+4, 20, fill="#f9fafb", outline="")

        # tooltip if dragging
        if self._drag and self._tooltip_text:
            tx = self._tooltip_x
            self.create_rectangle(tx-25, 0, tx+25, 16, fill="#0f0f23", outline="#8b5cf6")
            self.create_text(tx, 8, text=self._tooltip_text, fill="#fff", font=("Segoe UI", 8, "bold"))

    def _handle(self, x, color, role, tag):
        # Efecto Hover: círculo un poco más grande
        is_hover = (self._hover == role or self._drag == role)
        r = 5 if is_hover else 4
        
        # Hitbox invisible grande para facilitar el drag
        self.create_rectangle(x-12, 20, x+12, 50, fill="", outline="", tags=tag)
        
        # Dibujar línea y círculo hacia abajo
        self.create_line(x, 28, x, 40, fill=color, width=2, tags=tag)
        self.create_oval(x-r, 40-r, x+r, 40+r, fill=color, outline="", tags=tag)

    # ── drag ─────────────────────────────────────────────────────
    def _nearest(self, x):
        ds = abs(x - self._s2x(self.start_sec))
        de = abs(x - self._s2x(self.end_sec))
        dh = abs(x - self._s2x(self.head_sec))
        m  = min(ds, de, dh)
        if m > 22: return "seek"
        if m == dh and dh < ds and dh < de: return "head"
        return "start" if ds <= de else "end"

    def _press(self, e):
        t = self._nearest(e.x)
        if t == "seek":
            if self.on_seek: self.on_seek(self._x2s(e.x))
        else:
            self._drag = t

    def _drag_move(self, e):
        sec = self._x2s(e.x)
        if self._drag == "start":
            self.start_sec = max(0.0, min(sec, self.end_sec - 0.5))
            self._tooltip_text = _fmt(self.start_sec)
            self._tooltip_x = self._s2x(self.start_sec)
        elif self._drag == "end":
            self.end_sec   = max(self.start_sec + 0.5, min(sec, self.duration))
            self._tooltip_text = _fmt(self.end_sec)
            self._tooltip_x = self._s2x(self.end_sec)
        elif self._drag == "head":
            self.head_sec  = max(0.0, min(sec, self.duration))
            self._tooltip_text = _fmt(self.head_sec)
            self._tooltip_x = self._s2x(self.head_sec)
        self._redraw()
        if self.on_change and self._drag in ("start", "end"):
            self.on_change(self.start_sec, self.end_sec)

    def _release(self, _e):
        if self._drag == "head" and self.on_seek:
            self.on_seek(self.head_sec)
        self._drag = None
        self._tooltip_text = ""
        self._redraw()
        
    def _hover_move(self, e):
        t = self._nearest(e.x)
        new_hover = t if t in ("start", "end", "head") else None
        if self._hover != new_hover:
            self._hover = new_hover
            self.config(cursor="hand2" if new_hover else "")
            self._redraw()
            
    def _leave(self, e):
        self._hover = None
        self.config(cursor="")
        self._redraw()

    # ── API ──────────────────────────────────────────────────────
    def set_head(self, sec: float):
        self.head_sec = max(0.0, min(sec, self.duration))
        self._redraw()

    def set_range(self, start: float, end: float):
        self.start_sec = max(0.0, min(start, self.duration))
        self.end_sec   = max(self.start_sec, min(end, self.duration))
        if getattr(self, "zoomed", False):
            # Dynamic zoom update or keep bounds? 
            # Better to just update the view subtly or keep it fixed so the timeline doesn't jump crazily.
            pass
        self._redraw()


# ──────────────────────────────────────────────────────────────────────────────
# Reproductor de frames via ffpyplayer
# ──────────────────────────────────────────────────────────────────────────────
class FfmpegPlayer:
    """
    Reproduce video y audio en sincronía usando ffpyplayer.
    """

    def __init__(self, stream_url: str, on_frame, seek: float = 0.0):
        self.stream_url = stream_url
        self.on_frame   = on_frame
        self._pos       = seek
        self.player     = None
        self._thread    = None
        self._running   = False
        self._paused    = False
        self.sws        = None
        self.sws_size   = None
        self.target_size = (VIDEO_W, VIDEO_H)

    def play(self):
        self._running = True
        self._paused  = False
        if not self.player:
            ff_opts = {'sync': 'audio', 'ss': self._pos, 'infbuf': True}
            self.player = MediaPlayer(self.stream_url, ff_opts=ff_opts)
        else:
            self.player.set_pause(False)
            
        self._thread = threading.Thread(target=self._decode, daemon=True)
        self._thread.start()

    def pause(self):
        self._paused = True
        if self.player:
            self.player.set_pause(True)

    def resume(self):
        if self._paused:
            self._paused = False
            if self.player:
                self.player.set_pause(False)

    def seek(self, sec: float):
        self._pos = sec
        if self.player:
            self.player.seek(sec, relative=False, accurate=False)
            self.player.set_pause(False)
            self._paused = False

    def stop(self):
        self._running = False
        if self.player:
            self.player.close_player()
            self.player = None
        if self._thread:
            self._thread.join(timeout=2)

    def _decode(self):
        while self._running:
            if self._paused or not self.player:
                time.sleep(0.05)
                continue
                
            frame, val = self.player.get_frame()
            
            if val == 'eof':
                break
            elif frame is None:
                time.sleep(0.01)
            else:
                img, t = frame
                try:
                    w, h = img.get_size()
                    if not self.sws or self.sws_size != (w, h):
                        self.sws = SWScale(w, h, img.get_pixel_format(), ofmt='rgb24')
                        self.sws_size = (w, h)
                        
                    img_rgb = self.sws.scale(img)
                    data = img_rgb.to_bytearray()[0]
                    pil_img = Image.frombytes("RGB", (w, h), bytes(data))
                    tw, th = getattr(self, 'target_size', (VIDEO_W, VIDEO_H))
                    if tw > 0 and th > 0:
                        # Mantener aspect ratio
                        ratio = min(tw / w, th / h)
                        nw, nh = int(w * ratio), int(h * ratio)
                        if nw > 0 and nh > 0:
                            pil_img = pil_img.resize((nw, nh), Image.BILINEAR)
                    
                    self.on_frame(pil_img, t)
                except Exception as e:
                    print(f"Error decoding frame: {e}")
                    
                if val > 0:
                    time.sleep(val)

