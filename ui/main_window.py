import os
import glob
import queue
import threading
import subprocess
import json
import time
import truststore
truststore.inject_into_ssl()
import requests
from io import BytesIO

from core.utils import (PROJECT_ROOT, ASSETS_DIR, FFMPEG, NO_WINDOW, fmt_time, time_to_sec, parse_time,
                        ytdlp_cmd, safe_filename, unique_path)

# Caché propia de la app, SIEMPRE dentro de la carpeta del proyecto (antes se
# usaba os.getcwd(): lanzada desde C:\Users\<usuario> borraba ~/.cache entero).
CACHE_DIR = os.path.join(PROJECT_ROOT, ".cache")


def _clear_cache():
    """Borra solo los proxies que crea esta app; nada más."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    for p in glob.glob(os.path.join(CACHE_DIR, "proxy_*")):
        try:
            os.remove(p)
        except Exception:
            pass


_clear_cache()

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk

from download.download_window import DownloadWindow
from preview.player_window import TimelineBar, FfmpegPlayer
from core.config import ConfigManager
from ui import theme as T
from core.downloader import DownloadJob, crop_filter

PREVIEW_W = 640
PREVIEW_H = 360

class RetazoUI(ctk.CTk):
    def __init__(self):
        T.setup()
        super().__init__()
        self.title("Retazo")
        self.geometry("1240x800")
        self.minsize(1000, 660)
        self.resizable(True, True)
        self.configure(fg_color=T.BG)
        self._apply_window_icon()

        self.config_manager = ConfigManager()
        self.video_info = None
        self.stream_url = None
        self.orig_w = 1920
        self.orig_h = 1080
        
        self.crop_rect_canvas = None
        self.thumb_photo = None
        self.rect_id = None
        self.sx = self.sy = 0
        
        self._player = None
        self._playing = False
        self.bg_image_id = None
        self.video_aspect = 16 / 9
        self._thumb_pil = None
        self._current_url = None
        self._loading_anim = False
        self._jobs = set()

        # Tkinter no es thread-safe: los hilos de trabajo nunca tocan widgets.
        # Encolan funciones aquí y el hilo principal las ejecuta en _pump().
        self._ui_queue = queue.Queue()
        # Último fotograma decodificado (los anteriores se descartan si la UI va atrasada).
        self._frame_lock = threading.Lock()
        self._pending_frame = None

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        self._load_config_to_ui()
        self._pump()

    # ── puente hilo → UI ──
    def _ui(self, fn):
        """Programa fn en el hilo principal. Seguro desde cualquier hilo."""
        self._ui_queue.put(fn)

    def _pump(self):
        try:
            for _ in range(200):
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception as e:
                    print(f"[UI] {e}")
        except queue.Empty:
            pass
        with self._frame_lock:
            frame, self._pending_frame = self._pending_frame, None
        if frame is not None:
            self._process_live_frame(*frame)
        self.after(15, self._pump)

    def _set_status(self, text):
        self._ui(lambda: self.status_lbl.configure(text=text))

    def _on_close(self):
        for job in list(self._jobs):
            job.cancel()
        if self._player: self._player.stop()
        _clear_cache()
        self.destroy()

    def _load_config_to_ui(self):
        self.folder_var.set(self.config_manager.get("folder", os.path.expanduser("~/Downloads")))
        self.quality_var.set(self.config_manager.get("quality", "Alta (CRF 18)"))
        self.res_var.set(self.config_manager.get("resolution", "1080p (HD)"))
        self.ratio_var.set(self.config_manager.get("ratio", "Free"))
        
        if hasattr(self, 'export_var'):
            self.export_var.set(self.config_manager.get("export_mode", "Video + Audio"))
        if hasattr(self, 'format_var'):
            self.format_var.set(self.config_manager.get("format", "MP4"))
        if hasattr(self, 'codec_var'):
            self.codec_var.set(self.config_manager.get("codec", "H.264 (AVC)"))
        if hasattr(self, 'force_encode_var'):
            self.force_encode_var.set(bool(self.config_manager.get("force_encode", False)))
        self._refresh_export_state()
        self.after(50, self._draw_empty_state)

    def _save_config(self, *_):
        self.config_manager.update(
            folder=self.folder_var.get(),
            quality=self.quality_var.get(),
            resolution=self.res_var.get(),
            ratio=self.ratio_var.get(),
            export_mode=self.export_var.get(),
            format=self.format_var.get(),
            codec=self.codec_var.get(),
            force_encode=bool(self.force_encode_var.get()),
        )

    # ── icono ──
    def _apply_window_icon(self):
        """Icono de ventana, barra de tareas y ventanas secundarias."""
        ico = os.path.join(ASSETS_DIR, "icon.ico")
        png = os.path.join(ASSETS_DIR, "icon_256.png")
        try:
            if os.path.exists(png):
                self._icon_photo = ImageTk.PhotoImage(Image.open(png))
                self.iconphoto(True, self._icon_photo)      # True: también para Toplevels
            if os.name == "nt" and os.path.exists(ico):
                # Llamar a iconbitmap evita que CustomTkinter ponga su icono por defecto.
                self.iconbitmap(default=ico)
        except Exception as e:
            print(f"[icono] {e}")

    def _load_glyph(self):
        """Tijeras en color de acento (PIL RGBA) o None si falta el archivo."""
        if not hasattr(self, "_glyph_pil"):
            try:
                self._glyph_pil = Image.open(os.path.join(ASSETS_DIR, "glyph_accent.png")).convert("RGBA")
            except Exception:
                self._glyph_pil = None
        return self._glyph_pil

    # ══════════════════════════════════════════════════════════════
    #  INTERFAZ  (layout tipo editor: barra superior · vídeo + timeline ·
    #  panel de exportación a la derecha · barra de estado)
    # ══════════════════════════════════════════════════════════════
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_topbar()

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 8))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0, minsize=310)
        body.grid_rowconfigure(0, weight=1)

        self._build_editor(body)
        self._build_export_panel(body)
        self._build_statusbar()
        self._bind_shortcuts()
        self._refresh_export_state()

    # ── barra superior: marca + URL ──
    def _build_topbar(self):
        bar = ctk.CTkFrame(self, fg_color=T.SURFACE, corner_radius=12, border_width=1, border_color=T.BORDER)
        bar.grid(row=0, column=0, sticky="ew", padx=14, pady=14)
        bar.grid_columnconfigure(1, weight=1)

        brand = ctk.CTkFrame(bar, fg_color="transparent")
        brand.grid(row=0, column=0, padx=(16, 18), pady=10)
        glyph = self._load_glyph()
        if glyph is not None:
            ctk.CTkLabel(brand, text="", image=ctk.CTkImage(glyph, size=(24, 25))).pack(side="left", padx=(0, 8))
        else:
            T.label(brand, "✂", size=20, color=T.ACCENT).pack(side="left", padx=(0, 6))
        T.label(brand, "Retazo", size=17, weight="bold").pack(side="left")

        self.url_entry = T.entry(bar, placeholder_text="Pega la URL del vídeo (YouTube, TikTok, Instagram, Pinterest…)", height=38)
        self.url_entry.grid(row=0, column=1, sticky="ew", pady=10)
        self.url_entry.bind("<Return>", lambda _e: self.load_video())

        self.load_btn = T.tip(T.button(bar, "⚡  Cargar", self.load_video, kind="primary", width=120, height=38),
                              "Cargar vídeo  (Enter)")
        self.load_btn.grid(row=0, column=2, padx=(10, 12), pady=10)

    # ── zona central: vídeo, recorte, transporte, timeline, in/out ──
    def _build_editor(self, body):
        ed = ctk.CTkFrame(body, fg_color="transparent")
        ed.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        ed.grid_columnconfigure(0, weight=1)
        ed.grid_rowconfigure(1, weight=1)

        # cabecera: título + duración
        head = ctk.CTkFrame(ed, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        head.grid_columnconfigure(0, weight=1)
        self.title_lbl = T.label(head, "Ningún vídeo cargado", size=14, weight="bold", anchor="w")
        self.title_lbl.grid(row=0, column=0, sticky="w")
        self.dur_lbl = T.label(head, "", size=12, color=T.MUTED)
        self.dur_lbl.grid(row=0, column=1, sticky="e")

        # vista previa
        self.canvas_frame = ctk.CTkFrame(ed, fg_color=T.CANVAS_BG, corner_radius=12, border_width=1, border_color=T.BORDER)
        self.canvas_frame.grid(row=1, column=0, sticky="nsew")
        self.canvas = tk.Canvas(self.canvas_frame, bg=T.CANVAS_BG, highlightthickness=0, cursor="crosshair")
        self.canvas.place(relx=0.5, rely=0.5, anchor="center", width=PREVIEW_W, height=PREVIEW_H)
        self.cw = PREVIEW_W
        self.ch = PREVIEW_H
        self.canvas_frame.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<ButtonPress-1>", self._mp)
        self.canvas.bind("<B1-Motion>", self._md)
        self.canvas.bind("<ButtonRelease-1>", self._mr)

        # barra de recorte espacial
        crop = ctk.CTkFrame(ed, fg_color="transparent")
        crop.grid(row=2, column=0, sticky="ew", pady=(10, 4))
        T.label(crop, "Recorte", size=12, color=T.MUTED).pack(side="left", padx=(2, 8))
        self.ratio_var = ctk.StringVar(value="Free")
        self.ratio_opt = T.segmented(crop, ["Free", "16:9", "9:16", "1:1"], self.ratio_var, command=self._save_config)
        self.ratio_opt.pack(side="left")
        T.tip(T.button(crop, "✕ Quitar", self.clear_crop, kind="ghost", width=80), "Quitar zona de recorte").pack(side="left", padx=8)
        self.crop_lbl = T.label(crop, "Arrastra sobre el vídeo para recortar", size=11, color=T.SUBTLE)
        self.crop_lbl.pack(side="left", padx=4)

        # transporte
        tr = ctk.CTkFrame(ed, fg_color=T.SURFACE, corner_radius=12, border_width=1, border_color=T.BORDER)
        tr.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        tr.grid_columnconfigure(0, weight=1)
        ctrl = ctk.CTkFrame(tr, fg_color="transparent")
        ctrl.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 2))

        self.play_btn = T.tip(T.button(ctrl, "▶", self._toggle_play, kind="primary", width=44, height=34,
                                       font=T.font(15, "bold"), state="disabled"), "Reproducir / pausa  (Espacio)")
        self.play_btn.pack(side="left", padx=(0, 4))
        self.play_start_btn = T.tip(T.button(ctrl, "⇤", self._play_from_start, width=38, height=34,
                                             font=T.font(15), state="disabled"), "Reproducir desde el inicio del recorte  (Home)")
        self.play_start_btn.pack(side="left", padx=4)

        self._sep(ctrl)
        self.loop_play_var = tk.BooleanVar(value=True)
        T.tip(T.ToggleButton(ctrl, "⟳  Bucle", self.loop_play_var, width=86), "Repetir el tramo seleccionado  (L)").pack(side="left", padx=4)
        self.sync_cursor_var = tk.BooleanVar(value=False)
        T.tip(T.ToggleButton(ctrl, "⚓  Anclar", self.sync_cursor_var, width=90),
              "El vídeo sigue al marcador de inicio mientras lo arrastras").pack(side="left", padx=4)
        self.zoom_var = tk.BooleanVar(value=False)
        self.zoom_btn = T.tip(T.ToggleButton(ctrl, "🔍  Zoom", self.zoom_var, command=self._toggle_zoom, width=86,
                                             state="disabled"), "Ampliar la línea de tiempo al tramo  (Z)")
        self.zoom_btn.pack(side="left", padx=4)

        self._sep(ctrl)
        T.tip(ctk.CTkButton(ctrl, text="[  Inicio", command=self._set_start_here, width=78, height=34, corner_radius=8,
                            fg_color=T.SURFACE_2, hover_color=T.SURFACE_3, text_color=T.GREEN, font=T.font(12, "bold")),
              "Fijar inicio en el cabezal  (I)").pack(side="left", padx=4)
        T.tip(ctk.CTkButton(ctrl, text="Fin  ]", command=self._set_end_here, width=70, height=34, corner_radius=8,
                            fg_color=T.SURFACE_2, hover_color=T.SURFACE_3, text_color=T.RED, font=T.font(12, "bold")),
              "Fijar fin en el cabezal  (O)").pack(side="left", padx=4)

        self.time_lbl = T.label(ctrl, "00:00 / 00:00", size=13, weight="bold")
        self.time_lbl.pack(side="right", padx=(8, 4))

        self.timeline = TimelineBar(tr, duration=60, on_change=self._timeline_changed, on_seek=self._seek, bg=T.SURFACE)
        self.timeline.grid(row=1, column=0, sticky="ew", padx=10, pady=(4, 2))

        # in / out exactos
        io = ctk.CTkFrame(tr, fg_color="transparent")
        io.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))
        T.label(io, "Inicio", size=11, color=T.GREEN).pack(side="left", padx=(0, 6))
        self.start_e = T.entry(io, placeholder_text="00:00", width=96, height=30, justify="center")
        self.start_e.pack(side="left")
        T.label(io, "Fin", size=11, color=T.RED).pack(side="left", padx=(16, 6))
        self.end_e = T.entry(io, placeholder_text="00:30", width=96, height=30, justify="center")
        self.end_e.pack(side="left")
        self.range_lbl = T.label(io, "", size=11, color=T.MUTED)
        self.range_lbl.pack(side="left", padx=14)
        T.label(io, "Atajos: Espacio · I / O · ← →", size=10, color=T.SUBTLE).pack(side="right")
        for e in (self.start_e, self.end_e):
            e.bind("<Return>", self._entries_changed)
            e.bind("<FocusOut>", self._entries_changed)

    def _sep(self, parent):
        ctk.CTkFrame(parent, width=1, height=22, fg_color=T.BORDER).pack(side="left", padx=8)

    # ── panel de exportación ──
    def _build_export_panel(self, body):
        pnl = ctk.CTkFrame(body, fg_color=T.SURFACE, corner_radius=12, border_width=1, border_color=T.BORDER, width=310)
        pnl.grid(row=0, column=1, sticky="nsew")
        pnl.grid_propagate(False)
        pnl.grid_columnconfigure(0, weight=1)
        pnl.grid_rowconfigure(20, weight=1)   # empuja los botones al fondo

        T.label(pnl, "Exportar", size=15, weight="bold", anchor="w").grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 10))

        def field(row, title):
            T.section(pnl, title).grid(row=row, column=0, sticky="ew", padx=18, pady=(8, 4))

        field(1, "Formato")
        self.format_var = ctk.StringVar(value="MP4")
        self.format_opt = T.segmented(pnl, ["MP4", "GIF", "MP3", "WAV"], self.format_var, command=self._on_export_change)
        self.format_opt.grid(row=2, column=0, sticky="ew", padx=18)

        field(3, "Contenido")
        self.export_var = ctk.StringVar(value="Video + Audio")
        self.export_opt = T.option(pnl, ["Video + Audio", "Solo Video", "Solo Audio"], self.export_var, command=self._on_export_change)
        self.export_opt.grid(row=4, column=0, sticky="ew", padx=18)

        field(5, "Resolución máxima")
        self.res_var = ctk.StringVar()
        self.res_opt = T.option(pnl, ["2160p (4K)", "1440p (2K)", "1080p (HD)", "720p (HD)", "480p (SD)"], self.res_var, command=self._on_export_change)
        self.res_opt.grid(row=6, column=0, sticky="ew", padx=18)

        field(7, "Códec de vídeo")
        self.codec_var = ctk.StringVar()
        self.codec_opt = T.option(pnl, ["H.264 (AVC)", "H.265 (HEVC)", "VP9"], self.codec_var, command=self._on_export_change)
        self.codec_opt.grid(row=8, column=0, sticky="ew", padx=18)

        field(9, "Calidad")
        self.quality_var = ctk.StringVar()
        self.quality_opt = T.option(pnl, ["Alta (CRF 18)", "Media (CRF 23)", "Pequeño (CRF 28)"], self.quality_var, command=self._on_export_change)
        self.quality_opt.grid(row=10, column=0, sticky="ew", padx=18)

        self.force_encode_var = tk.BooleanVar(value=False)
        self.force_sw = ctk.CTkSwitch(pnl, text="Forzar recodificación", variable=self.force_encode_var, command=self._on_export_change,
                                      font=T.font(12), text_color=T.TEXT, text_color_disabled=T.SUBTLE,
                                      progress_color=T.ACCENT, button_color="#ffffff", button_hover_color="#e5e5ea", fg_color=T.SURFACE_3)
        self.force_sw.grid(row=11, column=0, sticky="w", padx=18, pady=(12, 0))
        T.tip(self.force_sw, "Recodifica aunque no haya recorte (aplica la calidad elegida)")

        field(12, "Carpeta de salida")
        frow = ctk.CTkFrame(pnl, fg_color="transparent")
        frow.grid(row=13, column=0, sticky="ew", padx=18)
        frow.grid_columnconfigure(0, weight=1)
        self.folder_var = tk.StringVar()
        self.folder_e = T.entry(frow, textvariable=self.folder_var, height=32)
        self.folder_e.grid(row=0, column=0, sticky="ew")
        self.folder_e.bind("<FocusOut>", self._save_config)
        T.tip(T.button(frow, "📁", self.browse_folder, width=36), "Elegir carpeta").grid(row=0, column=1, padx=(6, 0))

        self.export_hint = T.label(pnl, "", size=11, color=T.MUTED, anchor="w", justify="left", wraplength=270)
        self.export_hint.grid(row=14, column=0, sticky="ew", padx=18, pady=(14, 0))

        # acciones
        act = ctk.CTkFrame(pnl, fg_color="transparent")
        act.grid(row=21, column=0, sticky="ew", padx=18, pady=18)
        act.grid_columnconfigure(0, weight=1)
        self.dl_btn = T.tip(T.button(act, "⬇  Descargar fragmento", self.download, kind="success", height=46,
                                     font=T.font(14, "bold"), state="disabled"), "Descargar con estos ajustes  (Ctrl+S)")
        self.dl_btn.grid(row=0, column=0, sticky="ew")
        self.snap_btn = T.tip(T.button(act, "📸", self._take_snapshot, width=46, height=46, font=T.font(17), state="disabled"),
                              "Guardar el fotograma actual como PNG")
        self.snap_btn.grid(row=0, column=1, padx=(8, 0))

    # ── barra de estado ──
    def _build_statusbar(self):
        sb = ctk.CTkFrame(self, fg_color="transparent", height=28)
        sb.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 10))
        self.status_lbl = T.label(sb, "Pega una URL y pulsa Cargar", size=11, color=T.MUTED, anchor="w")
        self.status_lbl.pack(side="left", fill="x", expand=True)

    # ── atajos de teclado (ignorados mientras se escribe en un campo) ──
    def _bind_shortcuts(self):
        def guard(fn):
            def handler(e):
                try:
                    w = self.focus_get()
                except Exception:
                    w = None
                if isinstance(w, (tk.Entry, ctk.CTkEntry)) or (w is not None and w.winfo_class() == "Entry"):
                    return
                if self.video_info:
                    fn()
                return "break"
            return handler
        self.bind("<space>", guard(self._toggle_play))
        self.bind("<Home>", guard(self._play_from_start))
        for k in ("i", "I"): self.bind(f"<{k}>", guard(self._set_start_here))
        for k in ("o", "O"): self.bind(f"<{k}>", guard(self._set_end_here))
        for k in ("l", "L"): self.bind(f"<{k}>", guard(lambda: self.loop_play_var.set(not self.loop_play_var.get())))
        for k in ("z", "Z"): self.bind(f"<{k}>", guard(lambda: (self.zoom_var.set(not self.zoom_var.get()), self._toggle_zoom())))
        self.bind("<Left>", guard(lambda: self._nudge(-5)))
        self.bind("<Right>", guard(lambda: self._nudge(5)))
        self.bind("<Shift-Left>", guard(lambda: self._nudge(-1 / 30)))
        self.bind("<Shift-Right>", guard(lambda: self._nudge(1 / 30)))
        self.bind("<Control-s>", lambda e: (self.download() if str(self.dl_btn.cget("state")) == "normal" else None, "break")[1])
        self.canvas.bind("<Button-1>", lambda e: self.canvas.focus_set(), add="+")

    def _nudge(self, delta):
        """← → saltan 5 s; con Shift, un fotograma (~1/30 s)."""
        self._seek(max(0.0, min(self.timeline.head_sec + delta, self.timeline.duration)))

    # ── estado del panel de exportación ──
    def _on_export_change(self, *_):
        self._refresh_export_state()
        self._save_config()

    def _refresh_export_state(self):
        fmt = self.format_var.get() or "MP4"
        mode = self.export_var.get() or "Video + Audio"
        audio_only = fmt in ("MP3", "WAV") or (fmt == "MP4" and mode == "Solo Audio")
        is_gif = fmt == "GIF"

        def en(widget, on):
            widget.configure(state="normal" if on else "disabled")

        en(self.export_opt, fmt == "MP4")
        en(self.res_opt, not audio_only)
        en(self.codec_opt, fmt == "MP4" and not audio_only)
        en(self.quality_opt, fmt == "MP4" and not audio_only)
        en(self.force_sw, fmt == "MP4" and not audio_only)

        if fmt == "MP3":
            hint = "Se guardará como .mp3 (VBR alta calidad)."
        elif fmt == "WAV":
            hint = "Se guardará como .wav sin compresión."
        elif is_gif:
            hint = "GIF sin audio, 15 fps, máx. 720 px de ancho."
        elif audio_only:
            hint = "Se guardará solo el audio como .m4a (AAC 192 kbps)."
        else:
            parts = [self.codec_var.get().split(" ")[0] or "H.264", f"≤{(self.res_var.get() or '1080p').split(' ')[0]}"]
            if mode == "Solo Video":
                parts.append("sin audio")
            hint = "Se guardará como .mp4 · " + " · ".join(parts)
        self.export_hint.configure(text=hint)

    @staticmethod
    def _info_has_audio(info):
        """False solo si yt-dlp indica claramente que ningún formato trae audio
        (p. ej. los «GIF» de X, que son MP4 sin sonido). Si no se sabe: True."""
        if not info:
            return True
        fmts = info.get("formats") or [info]
        codecs = [f.get("acodec") for f in fmts]
        if any(c not in (None, "none") for c in codecs):
            return True
        return not (codecs and all(c == "none" for c in codecs))

    def _set_play_icon(self, playing):
        self.play_btn.configure(text="⏸" if playing else "▶")

    def _update_range_lbl(self, start=None, end=None):
        if start is None:
            start, end = self.timeline.start_sec, self.timeline.end_sec
        self.range_lbl.configure(text=f"Duración del recorte: {fmt_time(max(0.0, end - start))}")

    def _draw_empty_state(self):
        """Mensaje en el canvas antes de cargar ningún vídeo."""
        if self.video_info: return
        self.canvas.delete("empty")
        cx, cy = self.cw // 2, self.ch // 2
        glyph = self._load_glyph()
        if glyph is not None:
            if getattr(self, "_empty_photo", None) is None:
                g = glyph.copy()
                g.putalpha(g.getchannel("A").point(lambda a: a * 55 // 100))   # atenuado
                self._empty_photo = ImageTk.PhotoImage(g.resize((60, 63), Image.LANCZOS))
            self.canvas.create_image(cx, cy - 22, image=self._empty_photo, tags="empty")
        else:
            self.canvas.create_text(cx, cy - 18, text="🎬", font=(T.FONT, 34), fill=T.SUBTLE, tags="empty")
        self.canvas.create_text(cx, cy + 26, text="Pega una URL arriba y pulsa Cargar", font=(T.FONT, 13),
                                fill=T.MUTED, tags="empty")
        self.canvas.create_text(cx, cy + 50, text="Luego marca el tramo y, si quieres, arrastra para recortar la imagen",
                                font=(T.FONT, 10), fill=T.SUBTLE, tags="empty")

    # ── LOGIC INTEGRATION ──
    def load_video(self):
        url = self.url_entry.get().strip()
        if not url: return
        self.status_lbl.configure(text="⏳ Cargando info del video…")
        self.load_btn.configure(state="disabled")
        threading.Thread(target=self._fetch, args=(url,), daemon=True).start()

    def _fetch(self, url):
        """Hilo de trabajo: no toca widgets, solo encola en self._ui()."""
        try:
            r = subprocess.run(ytdlp_cmd("--dump-json", "--no-playlist", url=url), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=120, creationflags=NO_WINDOW)
            if r.returncode != 0: raise RuntimeError(r.stderr.strip()[-600:] or "yt-dlp falló")
            info = json.loads(r.stdout)

            img = None
            thumb_url = info.get("thumbnail", "")
            if thumb_url:
                try:
                    resp = requests.get(thumb_url, timeout=10)
                    resp.raise_for_status()
                    img = Image.open(BytesIO(resp.content))
                    img.load()
                except (requests.RequestException, OSError):
                    img = None  # Una miniatura que falla no debe bloquear la carga.
            self._ui(lambda: self._apply_info(url, info, img))
        except Exception as e:
            msg = str(e)
            self._ui(lambda: self.status_lbl.configure(text=f"❌ Error: {msg}"))
            self._ui(lambda: self.load_btn.configure(state="normal"))

    def _apply_info(self, url, info, img):
        # Nuevo vídeo: soltar el reproductor y el recorte del anterior.
        if self._player:
            self._player.stop()
            self._player = None
        self.clear_crop()
        self.stream_url = None
        self._current_url = url
        self.video_info = info
        self.orig_w = info.get("width") or 1920
        self.orig_h = info.get("height") or 1080
        self.video_aspect = self.orig_w / self.orig_h if self.orig_h else 16 / 9
        title = info.get("title") or "Sin título"
        dur = float(info.get("duration") or 0)

        self.canvas.delete("all")
        self.bg_image_id = None          # el item anterior ya no existe tras delete("all")
        self._thumb_pil = img
        self._layout_canvas()

        self.title_lbl.configure(text=title if len(title) <= 80 else title[:79] + "…")
        site = info.get("extractor_key") or info.get("extractor") or ""
        no_audio = not self._info_has_audio(info)
        self.dur_lbl.configure(text=(f"{site}  ·  " if site else "") + ("🔇 sin audio  ·  " if no_audio else "") + f"⏱ {fmt_time(dur)}")
        self.start_e.delete(0, "end"); self.start_e.insert(0, fmt_time(0))
        self.end_e.delete(0, "end"); self.end_e.insert(0, fmt_time(dur))
        self.load_btn.configure(state="normal")
        self.play_btn.configure(state="normal", text="▶")
        self.play_start_btn.configure(state="normal")
        self.zoom_btn.configure(state="normal")
        self.snap_btn.configure(state="normal")
        self.dl_btn.configure(state="normal")
        self.time_lbl.configure(text=f"00:00 / {fmt_time(dur)}")
        self.timeline.duration = max(dur, 1.0)
        self.timeline.zoomed = False
        self.zoom_var.set(False)
        self.timeline.set_range(0.0, dur)
        self.timeline.set_head(0.0)
        self._update_range_lbl(0.0, dur)
        self.status_lbl.configure(text="⏳ Preparando vista previa…")
        threading.Thread(target=self._fetch_stream_url, args=(url,), daemon=True).start()

    def _fetch_stream_url(self, url):
        """Hilo de trabajo: URL directa para previsualizar ya + proxy local ligero."""
        try:
            r = subprocess.run(ytdlp_cmd("-f", "best[ext=mp4]/best/bestvideo[ext=mp4]/bestvideo", "--get-url",
                                         "--no-playlist", url=url), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=120, creationflags=NO_WINDOW)
            stream = r.stdout.strip().splitlines()[0] if r.stdout.strip() else None
        except Exception:
            stream = None

        def _set_stream():
            if self._current_url != url: return   # el usuario ya cargó otro vídeo
            self.stream_url = stream
            self.status_lbl.configure(text="⏳ Descargando proxy local (para no tener lag)…" if stream
                                      else "⏳ Descargando proxy local…")
        self._ui(_set_stream)

        proxy_file = os.path.join(CACHE_DIR, f"proxy_{int(time.time() * 1000)}.mp4")
        try:
            pr = subprocess.run(ytdlp_cmd("-f", "worst[ext=mp4]/worst/worstvideo[ext=mp4]/worstvideo",
                                          "-o", proxy_file, "--no-playlist", url=url),
                                capture_output=True, creationflags=NO_WINDOW)
            ok = pr.returncode == 0 and os.path.exists(proxy_file)
        except Exception:
            ok = False

        def _use_proxy():
            if self._current_url != url: return
            if ok:
                was_running = bool(self._player and self._player._running)
                self.stream_url = proxy_file
                self.status_lbl.configure(text="✅ Proxy activado. Navegación fluida disponible.")
                if was_running:
                    self._start_player(self.timeline.head_sec)
            elif not self.stream_url:
                self.status_lbl.configure(text="❌ No se pudo preparar la vista previa.")
            else:
                self.status_lbl.configure(text="✅ Vista previa en streaming.")
        self._ui(_use_proxy)

    def _entries_changed(self, _e=None):
        """Valida lo escrito en Inicio/Fin y lo refleja en la línea de tiempo."""
        if not self.video_info: return
        dur = self.timeline.duration
        s, e = parse_time(self.start_e.get()), parse_time(self.end_e.get())
        if s is None or e is None:
            self.status_lbl.configure(text="⚠ Formato de tiempo inválido. Usa MM:SS o HH:MM:SS.")
            return
        s, e = min(s, dur), min(e, dur)
        if e <= s:
            self.status_lbl.configure(text="⚠ El fin debe ser posterior al inicio.")
            return
        self.timeline.set_range(s, e)
        self._update_range_lbl(s, e)

    def _timeline_changed(self, start, end):
        self.start_e.delete(0, "end"); self.start_e.insert(0, fmt_time(start))
        self.end_e.delete(0, "end"); self.end_e.insert(0, fmt_time(end))
        self._update_range_lbl(start, end)
        if hasattr(self, 'sync_cursor_var') and self.sync_cursor_var.get():
            if hasattr(self.timeline, '_drag') and self.timeline._drag == "start":
                self._seek(start)

    def _set_start_here(self):
        if not hasattr(self, 'timeline'): return
        h = self.timeline.head_sec
        e = time_to_sec(self.end_e.get())
        if h >= e: e = min(h + 0.5, self.timeline.duration)
        self.timeline.set_range(h, e)
        self._timeline_changed(h, e)

    def _set_end_here(self):
        if not hasattr(self, 'timeline'): return
        h = self.timeline.head_sec
        s = time_to_sec(self.start_e.get())
        if h <= s: s = max(0.0, h - 0.5)
        self.timeline.set_range(s, h)
        self._timeline_changed(s, h)

    def _toggle_zoom(self):
        # El botón refleja zoom_var; la línea de tiempo se alinea con ese valor.
        if bool(self.timeline.zoomed) != bool(self.zoom_var.get()):
            self.timeline.toggle_zoom()

    def _play_from_start(self):
        if not self.stream_url: return
        start_sec = time_to_sec(self.start_e.get())
        if self._player:
            self._player.seek(start_sec)
            self._player.resume()
        else:
            self._start_player(start_sec)

    def _on_canvas_resize(self, e=None):
        self._layout_canvas()

    def _layout_canvas(self):
        """Ajusta el canvas al ASPECTO REAL del vídeo (antes se forzaba 16:9, lo
        que descuadraba el recorte en vídeos verticales) y re-escala lo dibujado."""
        fw = self.canvas_frame.winfo_width()
        fh = self.canvas_frame.winfo_height()
        if fw <= 50 or fh <= 50:
            return
        aspect = self.video_aspect or 16 / 9
        tw = fw - 8
        th = int(round(tw / aspect))
        if th > fh - 8:
            th = fh - 8
            tw = int(round(th * aspect))
        tw, th = max(tw, 16), max(th, 16)
        old_w, old_h = self.cw, self.ch
        changed = (tw, th) != (old_w, old_h)
        self.cw, self.ch = tw, th
        self.canvas.place(relx=0.5, rely=0.5, anchor="center", width=tw, height=th)

        if changed and self.crop_rect_canvas and old_w and old_h:
            cx, cy, cw, ch = self.crop_rect_canvas
            sx, sy = tw / old_w, th / old_h
            self.crop_rect_canvas = (cx * sx, cy * sy, cw * sx, ch * sy)
            self._draw_crop_ui(cx * sx, cy * sy, (cx + cw) * sx, (cy + ch) * sy)

        # Miniatura (solo si aún no hay fotogramas en vivo)
        if self._thumb_pil is not None and self.bg_image_id is None:
            self.canvas.delete("thumb")
            self.thumb_photo = ImageTk.PhotoImage(self._thumb_pil.convert("RGB").resize((tw, th), Image.LANCZOS))
            self.canvas.create_image(0, 0, anchor="nw", image=self.thumb_photo, tags="thumb")
            self.canvas.tag_lower("thumb")

        if self._player:
            self._player.target_size = (tw, th)
        if not self.video_info:
            self._draw_empty_state()

    def _toggle_play(self):
        if not self.stream_url: return
        start_sec = time_to_sec(self.start_e.get())
        end_sec = time_to_sec(self.end_e.get())
        dur = self.video_info.get("duration", 60) if self.video_info else 60
        end_sec = end_sec if end_sec > 0 else dur
        is_playing = self._player and self._player._running and not getattr(self._player, "_paused", True)
        
        if is_playing:
            if self._player: self._player.pause()
            self._set_play_icon(False)
        else:
            if self._player and self._player._running:
                if self.timeline.head_sec < start_sec or self.timeline.head_sec >= end_sec:
                    self._seek(start_sec)
                else:
                    self._player.resume()
                self._set_play_icon(True)
            else:
                self._start_player(start_sec if self.timeline.head_sec < start_sec or self.timeline.head_sec >= end_sec else self.timeline.head_sec)

    def _animate_loading(self, step=0):
        # Un único bucle de animación (antes cada seek arrancaba otro en paralelo).
        if step == 0:
            if self._loading_anim: return
            self._loading_anim = True
        if not getattr(self, "is_loading", False):
            self._loading_anim = False
            self.canvas.delete("loading")
            return
        self.canvas.delete("loading")
        w, h = self.cw, self.ch
        cx, cy = w // 2, h // 2
        import math
        h1, h2, h3 = [15 + 20 * abs(math.sin(step * 0.5 + i)) for i in (0, 1, 2)]
        self.canvas.create_rectangle(cx - 20, cy + 20, cx - 10, cy + 20 - h1, fill="#a78bfa", tags="loading", outline="")
        self.canvas.create_rectangle(cx - 5, cy + 20, cx + 5, cy + 20 - h2, fill="#a78bfa", tags="loading", outline="")
        self.canvas.create_rectangle(cx + 10, cy + 20, cx + 20, cy + 20 - h3, fill="#a78bfa", tags="loading", outline="")
        self.canvas.create_text(cx, cy + 45, text="Cargando…", fill=T.MUTED, font=("Segoe UI", 12), tags="loading")
        self.after(50, lambda: self._animate_loading(step + 1))

    def _start_player(self, seek: float = 0.0):
        if not self.stream_url:
            return
        if self._player:
            self._player.stop()
            self._player = None
        with self._frame_lock:
            self._pending_frame = None
        try:
            player = FfmpegPlayer(self.stream_url, None, seek=seek)
        except RuntimeError as e:          # p. ej. falta ffpyplayer
            self.status_lbl.configure(text=f"❌ {e}")
            return
        # Solo se aceptan fotogramas del reproductor actual (no de uno ya parado).
        player.on_frame = lambda img, t, p=player: self._on_frame(img, t, p)
        self._player = player
        self.is_loading = True
        self._animate_loading()
        self._expected_seek_target = seek
        player.target_size = (self.cw, self.ch)
        player.play()
        self._set_play_icon(True)

    def _seek(self, sec: float):
        if not self._player and not self.stream_url:
            # Vista previa aún no lista: mover solo el cabezal (antes: excepción).
            self.timeline.set_head(sec)
            dur = self.video_info.get("duration", 0) if self.video_info else 0
            self.time_lbl.configure(text=f"{fmt_time(sec)} / {fmt_time(dur)}")
            return
        self._expected_seek_target = sec
        if self._player:
            self.is_loading = True
            self._animate_loading()
            self._player.seek(sec)
        else:
            self._start_player(sec)
        self.timeline.set_head(sec)

    def _on_frame(self, img, sec, player=None):
        """Hilo de decodificación: guarda el último fotograma (PIL). La PhotoImage
        se crea en el hilo principal (_pump); crearla aquí causaba cuelgues."""
        if player is not None and player is not self._player:
            return
        with self._frame_lock:
            self._pending_frame = (img, sec)

    def _process_live_frame(self, img, sec):
        if self._player is None:
            return
        photo = ImageTk.PhotoImage(img)
        self.is_loading = False
        self.canvas.delete("loading")
        self.canvas.delete("thumb")
        self.thumb_photo = photo
        if self.bg_image_id:
            self.canvas.itemconfig(self.bg_image_id, image=photo)
        else:
            self.bg_image_id = self.canvas.create_image(0, 0, anchor="nw", image=photo)
            self.canvas.tag_lower(self.bg_image_id)
            
        if hasattr(self, '_expected_seek_target'):
            if abs(sec - self._expected_seek_target) <= 1.5:
                del self._expected_seek_target
        if not hasattr(self, '_expected_seek_target'):
            dur = self.video_info.get("duration", 60) if self.video_info else 60
            self.time_lbl.configure(text=f"{fmt_time(sec)} / {fmt_time(dur)}")
            self.timeline.set_head(sec)
            
            end_sec = time_to_sec(self.end_e.get())
            if sec >= (end_sec if end_sec > 0 else dur):
                if hasattr(self, 'loop_play_var') and self.loop_play_var.get():
                    self._seek(time_to_sec(self.start_e.get()))
                else:
                    self._player.pause()
                    self._set_play_icon(False)

    # Crop logic advanced
    def _draw_crop_ui(self, x1, y1, x2, y2):
        self.canvas.delete("crop_ui")
        x_min, x_max = min(x1, x2), max(x1, x2)
        y_min, y_max = min(y1, y2), max(y1, y2)
        self._current_crop = (x_min, y_min, x_max, y_max)
        W, H = getattr(self, 'cw', PREVIEW_W), getattr(self, 'ch', PREVIEW_H)
        # Máscara semitransparente real (PNG con alfa). Antes: stipple "gray50",
        # que se veía como un tramado de puntos sobre la imagen.
        W, H = max(int(W), 1), max(int(H), 1)
        mask = Image.new("RGBA", (W, H), (0, 0, 0, 150))
        hx1, hy1 = max(0, int(x_min)), max(0, int(y_min))
        hx2, hy2 = min(W, int(x_max)), min(H, int(y_max))
        if hx2 > hx1 and hy2 > hy1:
            mask.paste((0, 0, 0, 0), (hx1, hy1, hx2, hy2))
        self._crop_mask_photo = ImageTk.PhotoImage(mask)
        self.canvas.create_image(0, 0, anchor="nw", image=self._crop_mask_photo, tags="crop_ui")
        # guías de tercios dentro de la zona
        for i in (1, 2):
            gx = x_min + (x_max - x_min) * i / 3
            gy = y_min + (y_max - y_min) * i / 3
            self.canvas.create_line(gx, y_min, gx, y_max, fill="#d4d4e0", dash=(2, 4), tags="crop_ui")
            self.canvas.create_line(x_min, gy, x_max, gy, fill="#d4d4e0", dash=(2, 4), tags="crop_ui")
        self.canvas.create_rectangle(x_min, y_min, x_max, y_max, outline="#a78bfa", width=2, dash=(6, 3), tags="crop_ui")
        r = 5
        for gx, gy in [(x_min, y_min), (x_max, y_min), (x_min, y_max), (x_max, y_max)]:
            self.canvas.create_oval(gx-r, gy-r, gx+r, gy+r, fill="#a78bfa", outline="#fff", tags="crop_ui")

    def _apply_ratio(self, x1, y1, x2, y2):
        ratio = self.ratio_var.get()
        if ratio in ("Free", "Libre"): return x1, y1, x2, y2
        w, h = x2 - x1, y2 - y1
        if w == 0 or h == 0: return x1, y1, x2, y2
        target_r = 16/9 if ratio == "16:9" else 9/16 if ratio == "9:16" else 1.0
        new_h = w / target_r
        return x1, y1, x2, int(y1 + (new_h if y2 >= y1 else -new_h))

    def _mp(self, e):
        self.drag_mode = "new"
        self.sx, self.sy = e.x, e.y
        if hasattr(self, 'crop_rect_canvas') and self.crop_rect_canvas:
            cx, cy, cw, ch = self.crop_rect_canvas
            x1, y1, x2, y2 = cx, cy, cx+cw, cy+ch
            TOL = 15
            if abs(e.x - x1) < TOL and abs(e.y - y1) < TOL: self.drag_mode = "nw"
            elif abs(e.x - x2) < TOL and abs(e.y - y1) < TOL: self.drag_mode = "ne"
            elif abs(e.x - x1) < TOL and abs(e.y - y2) < TOL: self.drag_mode = "sw"
            elif abs(e.x - x2) < TOL and abs(e.y - y2) < TOL: self.drag_mode = "se"
            elif x1 < e.x < x2 and y1 < e.y < y2:
                self.drag_mode = "move"
                self.drag_offset_x = e.x - x1
                self.drag_offset_y = e.y - y1
        if self.drag_mode == "new":
            self.clear_crop()
            self._draw_crop_ui(e.x, e.y, e.x, e.y)

    def _md(self, e):
        if not hasattr(self, 'drag_mode'): return
        if self.drag_mode == "new":
            _, _, x2, y2 = self._apply_ratio(self.sx, self.sy, e.x, e.y)
            self._draw_crop_ui(self.sx, self.sy, x2, y2)
        elif self.drag_mode == "move":
            if not self.crop_rect_canvas: return
            cx, cy, cw, ch = self.crop_rect_canvas
            nx = e.x - self.drag_offset_x
            ny = e.y - self.drag_offset_y
            nx = max(0, min(nx, getattr(self, 'cw', PREVIEW_W) - cw))
            ny = max(0, min(ny, getattr(self, 'ch', PREVIEW_H) - ch))
            self._draw_crop_ui(nx, ny, nx+cw, ny+ch)
        elif self.drag_mode in ("nw", "ne", "sw", "se"):
            if not self.crop_rect_canvas: return
            cx, cy, cw, ch = self.crop_rect_canvas
            x1, y1, x2, y2 = cx, cy, cx+cw, cy+ch
            ax, ay = (x2, y2) if self.drag_mode == "nw" else (x1, y2) if self.drag_mode == "ne" else (x2, y1) if self.drag_mode == "sw" else (x1, y1)
            dx, dy = e.x - ax, e.y - ay
            ratio = self.ratio_var.get()
            if ratio not in ("Free", "Libre"):
                target_r = 16/9 if ratio == "16:9" else 9/16 if ratio == "9:16" else 1.0
                if abs(dx) > abs(dy) * target_r: dy = (abs(dx) / target_r) * (1 if dy > 0 else -1)
                else: dx = (abs(dy) * target_r) * (1 if dx > 0 else -1)
            nx, ny = ax + dx, ay + dy
            if self.drag_mode == "nw": x1, y1 = nx, ny
            elif self.drag_mode == "ne": x2, y1 = nx, ny
            elif self.drag_mode == "sw": x1, y2 = nx, ny
            elif self.drag_mode == "se": x2, y2 = nx, ny
            self._draw_crop_ui(x1, y1, x2, y2)

    def _mr(self, e):
        if not hasattr(self, '_current_crop') or not self._current_crop: return
        x1, y1, x2, y2 = self._current_crop
        # Mantener el rectángulo dentro del vídeo aunque se arrastre fuera del canvas.
        x1, x2 = max(0, x1), min(self.cw, x2)
        y1, y2 = max(0, y1), min(self.ch, y2)
        if (x1, y1, x2, y2) != self._current_crop:
            self._draw_crop_ui(x1, y1, x2, y2)
        cw, ch = x2 - x1, y2 - y1
        if cw < 10 or ch < 10:
            self.clear_crop()
            return
        self.crop_rect_canvas = (x1, y1, cw, ch)
        px_w = round(cw / self.cw * self.orig_w); px_h = round(ch / self.ch * self.orig_h)
        self.crop_lbl.configure(text=f"✂ Zona ≈ {px_w}×{px_h} px", text_color=T.ACCENT)

    def clear_crop(self):
        self.crop_rect_canvas = None
        self._current_crop = None
        self.canvas.delete("crop_ui")
        self.crop_lbl.configure(text="Arrastra sobre el vídeo para recortar", text_color=T.SUBTLE)

    def browse_folder(self):
        d = filedialog.askdirectory()
        if d: self.folder_var.set(d); self._save_config()

    def _take_snapshot(self):
        if not self.stream_url or not self.video_info: return
        # Todo lo que se lee de widgets se lee aquí, en el hilo principal.
        params = dict(
            head_sec=self.timeline.head_sec,
            url=self._current_url,
            fallback=self.stream_url,
            crop=crop_filter(self.crop_rect_canvas, self.cw, self.ch),
            out_folder=self.folder_var.get().strip() or os.path.expanduser("~/Downloads"),
            title=self.video_info.get("title", "snapshot"),
        )
        threading.Thread(target=self._snapshot_thread, kwargs=params, daemon=True).start()

    def _snapshot_thread(self, head_sec, url, fallback, crop, out_folder, title):
        self._set_status("📸 Buscando stream de alta resolución…")
        try:
            res = subprocess.run(ytdlp_cmd("-f", "bestvideo/best", "--get-url", "--no-playlist", url=url),
                                 capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 timeout=60, creationflags=NO_WINDOW)
            hr_url = res.stdout.strip().splitlines()[0] if res.stdout.strip() else fallback
        except Exception:
            hr_url = fallback
        if not hr_url:
            self._set_status("❌ Error: No se pudo obtener el stream.")
            return

        self._set_status("📸 Extrayendo fotograma en alta resolución…")
        try:
            os.makedirs(out_folder, exist_ok=True)
            out_file = unique_path(out_folder, f"snap_{safe_filename(title, 50)}_{fmt_time(head_sec).replace(':', '.')}", "png")
            cmd = [FFMPEG, "-hide_banner", "-y", "-ss", f"{head_sec:.3f}", "-i", hr_url, "-frames:v", "1"]
            if crop:
                cmd += ["-vf", crop]
            cmd += [out_file]
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=120, creationflags=NO_WINDOW)
            if r.returncode != 0 or not os.path.exists(out_file):
                raise RuntimeError((r.stderr.strip().splitlines() or ["ffmpeg falló"])[-1])
            self._set_status(f"✅ Captura guardada: {os.path.basename(out_file)}")
        except Exception as e:
            msg = str(e)[:120]
            self._set_status(f"❌ Error al capturar: {msg}")

    def download(self):
        if not self.video_info: return messagebox.showwarning("⚠", "Carga un video")
        dur = float(self.video_info.get("duration") or 0)
        start, end = parse_time(self.start_e.get()), parse_time(self.end_e.get())
        if start is None or end is None:
            return messagebox.showwarning("⚠", "Tiempo inválido. Usa MM:SS o HH:MM:SS (p. ej. 01:30).")
        if dur > 0:
            end = min(end, dur)
        if end <= start:
            return messagebox.showwarning("⚠", "El fin del recorte debe ser posterior al inicio.")
        fmt, mode = self.format_var.get(), self.export_var.get()
        audio_only = fmt in ("MP3", "WAV") or (fmt == "MP4" and mode == "Solo Audio")
        if audio_only and not self._info_has_audio(self.video_info):
            return messagebox.showwarning(
                "Sin audio",
                "Este vídeo no tiene pista de audio, así que no se puede exportar solo el sonido.\n\n"
                "Cambia «Contenido» a «Video + Audio» o elige MP4/GIF en «Formato».")
        out_folder = self.folder_var.get().strip()
        if not out_folder:
            return messagebox.showwarning("⚠", "Elige una carpeta de salida.")
        self._save_config()

        win = DownloadWindow(self, title_text=self.video_info.get("title", ""))
        app = self

        class WinCallbacks:
            """Puente hilo de trabajo → ventana. Todo pasa por la cola de la UI y
            se ignora si el usuario ya cerró la ventana (antes: TclError)."""
            def __init__(self):
                self.job = None
            def _call(self, method, *args):
                def run():
                    if win.winfo_exists():
                        getattr(win, method)(*args)
                app._ui(run)
            def log(self, line): self._call("log", line)
            def set_progress(self, pct, detail): self._call("set_progress", pct, detail)
            def set_indeterminate(self, text): self._call("set_indeterminate", text)
            def set_detail(self, text): self._call("set_detail", text)
            def is_cancelled(self): return False      # la bandera vive en el propio job
            def done(self, msg, path): self._call("done", msg, path)
            def error(self, msg): self._call("error", msg)

        job = DownloadJob(
            url=self._current_url,          # el vídeo cargado, no lo que haya ahora en el campo URL
            start_sec=start,
            end_sec=end,
            out_folder=out_folder,
            config=self.config_manager.data,
            video_info=self.video_info,
            crop_coords=self.crop_rect_canvas,
            cw=self.cw, ch=self.ch,
            win_callbacks=WinCallbacks(),
        )
        win.on_cancel = job.cancel
        self._jobs.add(job)

        def _run():
            try:
                job.run()
            finally:
                self._ui(lambda: self._jobs.discard(job))
        threading.Thread(target=_run, daemon=True).start()
