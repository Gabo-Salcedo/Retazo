import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk
import subprocess
import json
import os
import sys
import threading
import time
import requests
from io import BytesIO

from download import DownloadWindow
from preview.player_window import TimelineBar, FfmpegPlayer, _parse

# ── Resolver rutas de herramientas ──────────────────────────────
def _find_tool(name):
    """Busca ejecutable: primero en Scripts de Python, luego WinGet, luego PATH."""
    candidates = [
        os.path.join(os.path.dirname(sys.executable), "Scripts", name + ".exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links", name + ".exe"),
        name,  # espera que esté en PATH
    ]
    for c in candidates:
        try:
            subprocess.run([c, "--version"], capture_output=True, timeout=5)
            return c
        except Exception:
            continue
    return name  # último recurso

YTDLP  = _find_tool("yt-dlp")
FFMPEG = _find_tool("ffmpeg")

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

PREVIEW_W = 640
PREVIEW_H = 360

class TubeSniper(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("TubeSniper 🎯")
        self.geometry("1020x780")
        self.resizable(False, False)
        self.configure(fg_color="#0d0d1a")

        self.video_info = None
        self.stream_url = None          # URL directa para VLC preview
        self.orig_w = 1920
        self.orig_h = 1080
        
        self.crop_rect = None
        self.thumb_photo = None
        self.rect_id = None
        self.sx = self.sy = 0
        self.config_file = "config.json"
        
        self._player = None
        self._playing = False
        self.bg_image_id = None
        
        self._loop_cache = []
        self._cache_playing = False
        self._cache_idx = 0

        self._build_ui()
        self._load_config()

    # ─────────────────────────── CONFIG ───────────────────────────
    def _load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    c = json.load(f)
                    if "folder" in c: self.folder_var.set(c["folder"])
                    if "quality" in c: self.quality_var.set(c["quality"])
                    if "resolution" in c: self.res_var.set(c["resolution"])
                    if "ratio" in c: self.ratio_var.set(c["ratio"])
            except Exception: pass

    def _save_config(self, *_):
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump({
                    "folder": self.folder_var.get(),
                    "quality": self.quality_var.get(),
                    "resolution": self.res_var.get(),
                    "ratio": self.ratio_var.get()
                }, f)
        except Exception: pass


    # ─────────────────────────── UI ────────────────────────────
    def _build_ui(self):
        # ── LEFT PANEL ──
        left = ctk.CTkFrame(self, width=330, fg_color="#12122a", corner_radius=16)
        left.pack(side="left", fill="y", padx=(12, 6), pady=12)
        left.pack_propagate(False)
        self.left = left

        ctk.CTkLabel(left, text="🎯 TubeSniper",
                     font=ctk.CTkFont("Segoe UI", 22, "bold"),
                     text_color="#a78bfa").pack(pady=(18, 4))
        ctk.CTkLabel(left, text="Descarga fragmentos de YouTube",
                     font=ctk.CTkFont("Segoe UI", 11),
                     text_color="#6b7280").pack(pady=(0, 14))

        self.tabs = ctk.CTkTabview(left, fg_color="#1e1e3a", corner_radius=10, height=450)
        self.tabs.pack(fill="x", padx=10, pady=(4, 8))
        tab_main = self.tabs.add("Principal")
        tab_render = self.tabs.add("Pre-Render")

        # URL
        ctk.CTkLabel(tab_main, text="URL de YouTube", anchor="w",
                     font=ctk.CTkFont("Segoe UI", 12, "bold")).pack(fill="x", padx=6)
        self.url_entry = ctk.CTkEntry(tab_main, placeholder_text="https://youtube.com/watch?v=...",
                                      height=36, corner_radius=8)
        self.url_entry.pack(fill="x", padx=6, pady=(4, 8))

        self.load_btn = ctk.CTkButton(tab_main, text="⚡ Cargar Video", height=38,
                                      corner_radius=8, fg_color="#7c3aed",
                                      hover_color="#5b21b6",
                                      font=ctk.CTkFont("Segoe UI", 13, "bold"),
                                      command=self.load_video)
        self.load_btn.pack(fill="x", padx=6, pady=(0, 6))

        # Info box
        info = ctk.CTkFrame(tab_main, fg_color="#1e1e3a", corner_radius=10)
        info.pack(fill="x", padx=6, pady=(0, 12))
        self.title_lbl = ctk.CTkLabel(info, text="—", wraplength=270,
                                       font=ctk.CTkFont("Segoe UI", 11, "bold"),
                                       text_color="#e2e8f0")
        self.title_lbl.pack(padx=10, pady=(8, 2))
        self.dur_lbl = ctk.CTkLabel(info, text="Duración: —",
                                     font=ctk.CTkFont("Segoe UI", 11),
                                     text_color="#94a3b8")
        self.dur_lbl.pack(padx=10, pady=(0, 8))

        # Time range
        ctk.CTkLabel(tab_main, text="Recorte de tiempo", anchor="w",
                     font=ctk.CTkFont("Segoe UI", 12, "bold")).pack(fill="x", padx=6)

        tframe = ctk.CTkFrame(tab_main, fg_color="transparent")
        tframe.pack(fill="x", padx=6, pady=(4, 12))
        tframe.columnconfigure((0, 1), weight=1)

        ctk.CTkLabel(tframe, text="Inicio (MM:SS)", font=ctk.CTkFont("Segoe UI", 10),
                     text_color="#94a3b8").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(tframe, text="Fin (MM:SS)", font=ctk.CTkFont("Segoe UI", 10),
                     text_color="#94a3b8").grid(row=0, column=1, sticky="w", padx=(8, 0))

        self.start_e = ctk.CTkEntry(tframe, placeholder_text="00:00", height=34, corner_radius=8)
        self.start_e.grid(row=1, column=0, sticky="ew")
        self.end_e = ctk.CTkEntry(tframe, placeholder_text="00:30", height=34, corner_radius=8)
        self.end_e.grid(row=1, column=1, sticky="ew", padx=(8, 0))

        # Output folder
        ctk.CTkLabel(tab_main, text="Carpeta de salida", anchor="w",
                     font=ctk.CTkFont("Segoe UI", 12, "bold")).pack(fill="x", padx=6)
        frow = ctk.CTkFrame(tab_main, fg_color="transparent")
        frow.pack(fill="x", padx=6, pady=(4, 12))
        frow.columnconfigure(0, weight=1)
        self.folder_var = tk.StringVar(value=os.path.expanduser("~/Downloads"))
        self.folder_e = ctk.CTkEntry(frow, textvariable=self.folder_var, height=34, corner_radius=8)
        self.folder_e.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(frow, text="…", width=36, height=34, corner_radius=8,
                      fg_color="#1e1e3a", hover_color="#2d2d5e",
                      command=self.browse_folder).grid(row=0, column=1, padx=(6, 0))

        # ── TAB PRE-RENDERIZADO ──
        self.force_encode_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(tab_render, text="Forzar recodificación de video", variable=self.force_encode_var,
                        font=ctk.CTkFont("Segoe UI", 11), text_color="#e2e8f0").pack(anchor="w", padx=16, pady=(12, 12))

        # Resolución
        ctk.CTkLabel(tab_render, text="Resolución Máx.", anchor="w",
                     font=ctk.CTkFont("Segoe UI", 12, "bold")).pack(fill="x", padx=16)
        self.res_var = ctk.StringVar(value="1080p (HD)")
        self.res_opt = ctk.CTkOptionMenu(tab_render,
            values=["2160p (4K)", "1440p (2K)", "1080p (HD)", "720p (HD)", "480p (SD)"],
            variable=self.res_var, height=34, corner_radius=8,
            fg_color="#1e1e3a", button_color="#7c3aed", button_hover_color="#5b21b6",
            command=self._save_config)
        self.res_opt.pack(fill="x", padx=16, pady=(4, 12))

        # Calidad / Formato
        ctk.CTkLabel(tab_render, text="Calidad / Compresión", anchor="w",
                     font=ctk.CTkFont("Segoe UI", 12, "bold")).pack(fill="x", padx=16)
        self.quality_var = ctk.StringVar(value="Alta (CRF 18)")
        self.quality_opt = ctk.CTkOptionMenu(tab_render,
            values=["Alta (CRF 18)", "Media (CRF 23)", "Pequeño (CRF 28)"],
            variable=self.quality_var, height=34, corner_radius=8,
            fg_color="#1e1e3a", button_color="#7c3aed", button_hover_color="#5b21b6",
            command=self._save_config)
        self.quality_opt.pack(fill="x", padx=16, pady=(4, 12))

        # Formato de exportación
        self.export_var = ctk.StringVar(value="Video + Audio")
        self.export_opt = ctk.CTkOptionMenu(left,
            values=["Video + Audio", "Solo Video (sin audio)", "Solo audio (sin video)"],
            variable=self.export_var, height=34, corner_radius=10,
            fg_color="#1e1e3a", button_color="#3b82f6", button_hover_color="#2563eb",
            command=self._save_config)
        self.export_opt.pack(fill="x", padx=16, pady=(12, 4))

        # Download btn
        dl_row = ctk.CTkFrame(left, fg_color="transparent")
        dl_row.pack(fill="x", padx=16, pady=(4, 8))

        self.dl_btn = ctk.CTkButton(dl_row, text="⬇  Descargar", height=44,
                                     corner_radius=10,
                                     fg_color="#059669", hover_color="#047857",
                                     font=ctk.CTkFont("Segoe UI", 14, "bold"),
                                     command=self.download)
        self.dl_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.snap_btn = ctk.CTkButton(dl_row, text="📸", width=44, height=44,
                                      corner_radius=10, font=ctk.CTkFont("Segoe UI", 18),
                                      fg_color="#1e1e3a", hover_color="#2d2d5e",
                                      command=self._take_snapshot, state="disabled")
        self.snap_btn.pack(side="right")

        # Progress + status
        self.progress = ctk.CTkProgressBar(left, height=8, corner_radius=4,
                                            progress_color="#7c3aed")
        self.progress.pack(fill="x", padx=16, pady=(0, 6))
        self.progress.set(0)

        self.status_lbl = ctk.CTkLabel(left, text="Pega URL y carga el video",
                                        wraplength=290, justify="left",
                                        font=ctk.CTkFont("Segoe UI", 11),
                                        text_color="#94a3b8")
        self.status_lbl.pack(padx=16, pady=(0, 6))



        # ── RIGHT PANEL ──
        self.right = ctk.CTkFrame(self, fg_color="#0d0d1a", corner_radius=0)
        self.right.pack(side="right", fill="both", expand=True, padx=(6, 12), pady=12)

        header = ctk.CTkFrame(self.right, fg_color="transparent")
        header.pack(fill="x", padx=4, pady=(6, 4))
        
        self.left_visible = True
        self.toggle_left_btn = ctk.CTkButton(header, text="◂", width=32, height=28, corner_radius=6,
                                             fg_color="#1e1e3a", hover_color="#2d2d5e", font=ctk.CTkFont("Segoe UI", 16),
                                             command=self._toggle_left_panel)
        self.toggle_left_btn.pack(side="left")

        ctk.CTkLabel(header, text="Preview  ·  Arrastra para seleccionar zona de recorte",
                     font=ctk.CTkFont("Segoe UI", 12), text_color="#6b7280").pack(side="left", padx=(10, 0))

        self.canvas_frame = ctk.CTkFrame(self.right, fg_color="#1a1a2e", corner_radius=12,
                                     border_width=1, border_color="#2d2d5e")
        self.canvas_frame.pack(fill="both", expand=True, padx=4, pady=4)

        self.canvas = tk.Canvas(self.canvas_frame, bg="#0f0f23", highlightthickness=0, cursor="crosshair")
        self.canvas.place(relx=0.5, rely=0.5, anchor="center", width=PREVIEW_W, height=PREVIEW_H)
        
        self.cw = PREVIEW_W
        self.ch = PREVIEW_H
        self.canvas_frame.bind("<Configure>", self._on_canvas_resize)
        
        self.canvas.bind("<ButtonPress-1>", self._mp)
        self.canvas.bind("<B1-Motion>", self._md)
        self.canvas.bind("<ButtonRelease-1>", self._mr)
        
        self.canvas.bind("<space>", lambda e: self._toggle_play())
        self.canvas.bind("<Left>", lambda e: self._seek(max(0.0, self.timeline.head_sec - 5)))
        self.canvas.bind("<Right>", lambda e: self._seek(min(self.video_info.get("duration", 60) if self.video_info else 60, self.timeline.head_sec + 5)))

        # Placeholder text on canvas
        self.canvas.create_text(self.cw // 2, self.ch // 2,
                                text="← Carga un video para ver preview",
                                fill="#374151", font=("Segoe UI", 14))

        # ── ROW 1: Cut Options ──
        cut_row = ctk.CTkFrame(self.right, fg_color="transparent")
        cut_row.pack(fill="x", padx=16, pady=(12, 6))
        
        ctk.CTkLabel(cut_row, text="Cut", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color="#e2e8f0").pack(side="left", padx=(0, 12))
        
        self.ratio_var = ctk.StringVar(value="Free")
        self.ratio_opt = ctk.CTkSegmentedButton(cut_row, values=["Free", "16:9", "9:16", "1:1"],
                                                variable=self.ratio_var, command=self._save_config,
                                                selected_color="#8b5cf6", selected_hover_color="#7c3aed",
                                                unselected_color="#1e1e3a", unselected_hover_color="#2d2d5e")
        self.ratio_opt.pack(side="left", padx=8)
        
        ctk.CTkButton(cut_row, text="🔳 Clean", width=60, height=28, corner_radius=6,
                      fg_color="#1e1e3a", hover_color="#2d2d5e", font=ctk.CTkFont("Segoe UI", 11),
                      command=self.clear_crop).pack(side="left", padx=12)

        self.crop_lbl = ctk.CTkLabel(self.right, text="🔲 Sin zona seleccionada — ancho completo", font=ctk.CTkFont("Segoe UI", 11), text_color="#6b7280")
        self.crop_lbl.pack(anchor="w", padx=20, pady=(0, 6))

        # ── ROW 2: Timeline ──
        tl_row = ctk.CTkFrame(self.right, fg_color="transparent")
        tl_row.pack(fill="x", padx=16, pady=(6, 0))
        self.timeline = TimelineBar(tl_row, duration=60, on_change=self._timeline_changed, on_seek=self._seek)
        self.timeline.pack(fill="x", expand=True, padx=0)

        # ── ROW 3: Play Controls ──
        self.play_ctrl = ctk.CTkFrame(self.right, fg_color="transparent")
        self.play_ctrl.pack(fill="x", padx=16, pady=(6, 12))
        
        btn_font = ctk.CTkFont("Segoe UI", 15)
        
        # Play btn
        self.play_btn = ctk.CTkButton(self.play_ctrl, text="▶", width=36, height=32, corner_radius=6, font=btn_font,
                                      fg_color="#1e1e3a", hover_color="#2d2d5e", text_color="#f9fafb", command=self._toggle_play, state="disabled")
        self.play_btn.pack(side="left", padx=2)

        # Play start btn
        self.play_start_btn = ctk.CTkButton(self.play_ctrl, text="⏯", width=36, height=32, corner_radius=6, font=btn_font,
                                            fg_color="#1e1e3a", hover_color="#2d2d5e", text_color="#f9fafb", command=self._play_from_start, state="disabled")
        self.play_start_btn.pack(side="left", padx=2)

        # Loop btn
        self.loop_play_var = tk.BooleanVar(value=True)
        self.loop_btn = ctk.CTkCheckBox(self.play_ctrl, text="🔁", variable=self.loop_play_var, width=44, corner_radius=6,
                                        fg_color="#8b5cf6", hover_color="#7c3aed", border_width=0, font=btn_font)
        self.loop_btn.pack(side="left", padx=(8, 2))

        # Pin btn
        self.sync_cursor_var = tk.BooleanVar(value=False)
        self.pin_btn = ctk.CTkCheckBox(self.play_ctrl, text="📌", variable=self.sync_cursor_var, width=44, corner_radius=6,
                                       fg_color="#8b5cf6", hover_color="#7c3aed", border_width=0, font=btn_font)
        self.pin_btn.pack(side="left", padx=2)

        # Zoom btn
        self.zoom_btn = ctk.CTkButton(self.play_ctrl, text="🔍", width=36, height=32, corner_radius=6, font=btn_font,
                                      fg_color="#1e1e3a", hover_color="#2d2d5e", text_color="#f9fafb", command=self._toggle_zoom, state="disabled")
        self.zoom_btn.pack(side="left", padx=2)

        # Set Start (Green)
        ctk.CTkButton(self.play_ctrl, text="◂", width=32, height=32, corner_radius=6,
                      fg_color="#10b981", hover_color="#059669", font=ctk.CTkFont("Segoe UI", 16, "bold"), text_color="#fff",
                      command=self._set_start_here).pack(side="left", padx=(8, 2))

        # Set End (Red)
        ctk.CTkButton(self.play_ctrl, text="▸", width=32, height=32, corner_radius=6,
                      fg_color="#f43f5e", hover_color="#e11d48", font=ctk.CTkFont("Segoe UI", 16, "bold"), text_color="#fff",
                      command=self._set_end_here).pack(side="left", padx=2)

        # Time Label
        self.time_lbl = ctk.CTkLabel(self.play_ctrl, text="00:00 / 00:00", font=ctk.CTkFont("Segoe UI", 12), text_color="#e2e8f0")
        self.time_lbl.pack(side="right", padx=0)
        
        # Bindings entradas tiempo
        self.start_e.bind("<Return>", self._entries_changed)
        self.start_e.bind("<FocusOut>", self._entries_changed)
        self.end_e.bind("<Return>", self._entries_changed)
        self.end_e.bind("<FocusOut>", self._entries_changed)

    # ─────────────────────────── LOAD ───────────────────────────
    def load_video(self):
        url = self.url_entry.get().strip()
        if not url:
            return
        self.status_lbl.configure(text="⏳ Cargando info del video…")
        self.load_btn.configure(state="disabled")
        self.progress.set(0.15)
        threading.Thread(target=self._fetch, args=(url,), daemon=True).start()

    def _fetch(self, url):
        try:
            r = subprocess.run(
                [YTDLP, "--dump-json", "--no-playlist", url],
                capture_output=True, text=True, timeout=30
            )
            if r.returncode != 0:
                raise RuntimeError(r.stderr[:300])
            info = json.loads(r.stdout)
            self.video_info = info
            self.orig_w = info.get("width", 1920) or 1920
            self.orig_h = info.get("height", 1080) or 1080

            thumb_url = info.get("thumbnail", "")
            duration = info.get("duration", 0)
            title = info.get("title", "Sin título")

            img = None
            if thumb_url:
                resp = requests.get(thumb_url, timeout=10)
                img = Image.open(BytesIO(resp.content)).resize(
                    (PREVIEW_W, PREVIEW_H), Image.LANCZOS)

            self.after(0, lambda: self._apply_info(img, title, duration))
        except Exception as e:
            self.after(0, lambda: self._set_status(f"❌ Error: {e}"))
            self.after(0, lambda: self.load_btn.configure(state="normal"))
            self.after(0, lambda: self.progress.set(0))

    def _apply_info(self, img, title, dur):
        if img:
            self.thumb_photo = ImageTk.PhotoImage(img)
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, anchor="nw", image=self.thumb_photo)

        short = title[:55] + "…" if len(title) > 55 else title
        m, s = divmod(int(dur), 60)
        h, m = divmod(m, 60)
        dur_str = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

        self.title_lbl.configure(text=short)
        self.dur_lbl.configure(text=f"⏱  Duración: {dur_str}")

        self.end_e.delete(0, "end")
        self.end_e.insert(0, dur_str)

        self.load_btn.configure(state="normal")
        self.play_btn.configure(state="normal")
        if hasattr(self, 'play_start_btn'):
            self.play_start_btn.configure(state="normal")
        if hasattr(self, 'zoom_btn'):
            self.zoom_btn.configure(state="normal")
        if hasattr(self, 'snap_btn'):
            self.snap_btn.configure(state="normal")
        self.progress.set(0)
        self._set_status("✅ Video cargado — usa el reproductor interactivo")
        
        self.timeline.duration = max(dur, 1.0)
        self.timeline.set_range(0.0, dur)
        self.timeline._redraw()
        self.canvas.focus_set()

        # Obtener URL directa para VLC en hilo separado
        threading.Thread(target=self._fetch_stream_url, daemon=True).start()

    def _fetch_stream_url(self):
        """Obtiene la URL directa del stream sin bloquear la UI."""
        try:
            url = self.url_entry.get().strip()
            # Para garantizar que el preview y el corte coincidan al milisegundo,
            # debemos usar un stream principal, no el "worst" (que YouTube a veces desfasa).
            r = subprocess.run(
                [YTDLP, "-f", "best[ext=mp4]/best",
                 "--get-url", "--no-playlist", url],
                capture_output=True, text=True, timeout=30
            )
            lines = r.stdout.strip().splitlines()
            if lines:
                self.stream_url = lines[0]
        except Exception:
            self.stream_url = None

    def _timeline_changed(self, start, end):
        self.start_e.delete(0, "end")
        self.start_e.insert(0, _fmt_time(start))
        self.end_e.delete(0, "end")
        self.end_e.insert(0, _fmt_time(end))
        self._loop_cache = []
        self._cache_playing = False
        
        if hasattr(self, 'sync_cursor_var') and self.sync_cursor_var.get():
            if hasattr(self.timeline, '_drag') and self.timeline._drag == "start":
                # Only seek occasionally to prevent hanging
                self._seek(start)

    def _entries_changed(self, _e=None):
        s = _parse(self.start_e.get())
        e = _parse(self.end_e.get())
        dur = self.video_info.get("duration", 60) if self.video_info else 60
        s = max(0.0, min(s, dur))
        e = max(s + 0.5, min(e, dur))
        if hasattr(self, 'timeline'):
            self.timeline.set_range(s, e)
        self._loop_cache = []
        self._cache_playing = False

    def _set_start_here(self):
        if not hasattr(self, 'timeline'): return
        h = self.timeline.head_sec
        e = _parse(self.end_e.get())
        if h >= e: e = min(h + 0.5, self.timeline.duration)
        self.timeline.set_range(h, e)
        self._timeline_changed(h, e)

    def _set_end_here(self):
        if not hasattr(self, 'timeline'): return
        h = self.timeline.head_sec
        s = _parse(self.start_e.get())
        if h <= s: s = max(0.0, h - 0.5)
        self.timeline.set_range(s, h)
        self._timeline_changed(s, h)

    def _toggle_zoom(self):
        if not hasattr(self, 'timeline'): return
        self.timeline.toggle_zoom()
        if getattr(self.timeline, 'zoomed', False):
            self.zoom_btn.configure(fg_color="#8b5cf6", hover_color="#7c3aed")
        else:
            self.zoom_btn.configure(fg_color="#1e1e3a", hover_color="#2d2d5e")

    def _play_from_start(self):
        if not self.stream_url: return
        start_sec = _parse(self.start_e.get())
        if self._player:
            self._player.seek(start_sec)
            self._player.resume()
        else:
            self._start_player(start_sec)

    def _toggle_left_panel(self):
        if self.left_visible:
            self.left.pack_forget()
            self.left_visible = False
            self.toggle_left_btn.configure(text="▸")
        else:
            self.left.pack(side="left", fill="y", padx=(12, 6), pady=12, before=self.right)
            self.left_visible = True
            self.toggle_left_btn.configure(text="◂")

    def _on_canvas_resize(self, e):
        if e.width > 50 and e.height > 50:
            tw = e.width - 8
            th = int(tw * 9 / 16)
            if th > e.height - 8:
                th = e.height - 8
                tw = int(th * 16 / 9)

            if not hasattr(self, 'cw') or tw != getattr(self, 'cw', PREVIEW_W) or th != getattr(self, 'ch', PREVIEW_H):
                old_w = getattr(self, 'cw', PREVIEW_W)
                old_h = getattr(self, 'ch', PREVIEW_H)
                self.cw = tw
                self.ch = th
                
                self.canvas.place(relx=0.5, rely=0.5, anchor="center", width=self.cw, height=self.ch)
                
                if hasattr(self, 'crop_rect_canvas') and self.crop_rect_canvas:
                    cx, cy, cw, ch = self.crop_rect_canvas
                    scale_x = self.cw / old_w
                    scale_y = self.ch / old_h
                    self.crop_rect_canvas = (cx * scale_x, cy * scale_y, cw * scale_x, ch * scale_y)
                    self._draw_crop_ui(cx * scale_x, cy * scale_y, (cx+cw) * scale_x, (cy+ch) * scale_y)
                elif hasattr(self, '_current_crop') and self._current_crop:
                    cx1, cy1, cx2, cy2 = self._current_crop
                    scale_x = self.cw / old_w
                    scale_y = self.ch / old_h
                    self._draw_crop_ui(cx1 * scale_x, cy1 * scale_y, cx2 * scale_x, cy2 * scale_y)

                if hasattr(self, '_player') and self._player:
                    self._player.target_size = (self.cw, self.ch)

    def _toggle_play(self):
        if not self.stream_url: return
        start_sec = _parse(self.start_e.get())
        end_sec = _parse(self.end_e.get())
        dur = self.video_info.get("duration", 60) if self.video_info else 60
        end_sec = end_sec if end_sec > 0 else dur
        
        is_playing = self._player and self._player._running and not getattr(self._player, "_paused", True)
        
        if is_playing:
            if self._player: self._player.pause()
            self.play_btn.configure(text="▶", text_color="#059669")
        else:
            if self._player and self._player._running:
                if self.timeline.head_sec < start_sec or self.timeline.head_sec >= end_sec:
                    self._seek(start_sec)
                else:
                    self._player.resume()
                self.play_btn.configure(text="⏸", text_color="#dc2626")
            else:
                if self.timeline.head_sec < start_sec or self.timeline.head_sec >= end_sec:
                    self._start_player(start_sec)
                else:
                    self._start_player(self.timeline.head_sec)

    def _start_player(self, seek: float = 0.0):
        if self._player: self._player.stop()
        self._loop_cache = []
        self._cache_playing = False
        # Create player and assign size right away
        self._player = FfmpegPlayer(self.stream_url, self._on_frame, seek=seek)
        self._player.target_size = (getattr(self, 'cw', PREVIEW_W), getattr(self, 'ch', PREVIEW_H))
        self._player.play()
        self._playing = True
        self.play_btn.configure(text="⏸", text_color="#dc2626")

    def _seek(self, sec: float):
        self._loop_cache = []
        self._cache_playing = False
        self._expected_seek_target = sec
        if self._player:
            self._player.seek(sec)
        else:
            self._start_player(sec)
        if hasattr(self, 'timeline'):
            self.timeline.set_head(sec)

    def _on_frame(self, img, sec):
        photo = ImageTk.PhotoImage(img)
        self.after(0, self._process_live_frame, photo, sec)

    def _process_live_frame(self, photo, sec):
        self._draw_frame(photo)
        self._update_time_ui(sec)

    def _draw_frame(self, photo):
        self.thumb_photo = photo
        if hasattr(self, 'bg_image_id') and self.bg_image_id:
            self.canvas.itemconfig(self.bg_image_id, image=photo)
        else:
            self.bg_image_id = self.canvas.create_image(0, 0, anchor="nw", image=photo)
            self.canvas.tag_lower(self.bg_image_id)

    def _update_time_ui(self, sec):
        if hasattr(self, '_expected_seek_target'):
            if abs(sec - self._expected_seek_target) > 1.5:
                return
            else:
                del self._expected_seek_target

        if not hasattr(self, 'timeline'): return
        dur = self.video_info.get("duration", 60) if self.video_info else 60
        self.time_lbl.configure(text=f"{_fmt_time(sec)} / {_fmt_time(dur)}")
        self.timeline.set_head(sec)
        
        end_sec = _parse(self.end_e.get())
        end_sec = end_sec if end_sec > 0 else dur

        if self._playing and self._player and not getattr(self._player, "_paused", True):
            if sec >= end_sec:
                start_sec = _parse(self.start_e.get())
                if self.loop_play_var.get():
                    self._seek(start_sec)
                else:
                    self._player.pause()
                    self.play_btn.configure(text="▶", text_color="#059669")

    # ─────────────────────────── CROP ───────────────────────────
    def _draw_crop_ui(self, x1, y1, x2, y2):
        self.canvas.delete("crop_ui")
        x_min, x_max = min(x1, x2), max(x1, x2)
        y_min, y_max = min(y1, y2), max(y1, y2)
        
        self._current_crop = (x_min, y_min, x_max, y_max)
        
        W, H = getattr(self, 'cw', PREVIEW_W), getattr(self, 'ch', PREVIEW_H)
        fill_color, stipple = "black", "gray50"
        
        # Máscara externa
        self.canvas.create_rectangle(0, 0, W, y_min, fill=fill_color, stipple=stipple, outline="", tags="crop_ui")
        self.canvas.create_rectangle(0, y_max, W, H, fill=fill_color, stipple=stipple, outline="", tags="crop_ui")
        self.canvas.create_rectangle(0, y_min, x_min, y_max, fill=fill_color, stipple=stipple, outline="", tags="crop_ui")
        self.canvas.create_rectangle(x_max, y_min, W, y_max, fill=fill_color, stipple=stipple, outline="", tags="crop_ui")
        
        # Borde interior
        self.canvas.create_rectangle(x_min, y_min, x_max, y_max, outline="#a78bfa", width=2, dash=(6, 3), tags="crop_ui")
        
        # Gizmos esquinas
        r = 5
        for gx, gy in [(x_min, y_min), (x_max, y_min), (x_min, y_max), (x_max, y_max)]:
            self.canvas.create_oval(gx-r, gy-r, gx+r, gy+r, fill="#a78bfa", outline="#fff", tags="crop_ui")

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

    def _apply_ratio(self, x1, y1, x2, y2):
        ratio = self.ratio_var.get()
        if ratio == "Free" or ratio == "Libre": return x1, y1, x2, y2
        w = x2 - x1
        h = y2 - y1
        if w == 0 or h == 0: return x1, y1, x2, y2
        
        target_r = 1.0
        if ratio == "16:9": target_r = 16/9
        elif ratio == "9:16": target_r = 9/16
        elif ratio == "1:1": target_r = 1.0
        
        new_h = w / target_r
        y2 = y1 + (new_h if y2 >= y1 else -new_h)
        return x1, y1, x2, int(y2)

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
            
            # Anchor point (opposite corner)
            ax, ay = (x2, y2) if self.drag_mode == "nw" else \
                     (x1, y2) if self.drag_mode == "ne" else \
                     (x2, y1) if self.drag_mode == "sw" else \
                     (x1, y1)
            
            dx, dy = e.x - ax, e.y - ay
            ratio = self.ratio_var.get()
            
            if ratio not in ("Free", "Libre"):
                target_r = 1.0
                if ratio == "16:9": target_r = 16/9
                elif ratio == "9:16": target_r = 9/16
                elif ratio == "1:1": target_r = 1.0
                
                if abs(dx) > abs(dy) * target_r:
                    dy = (abs(dx) / target_r) * (1 if dy > 0 else -1)
                else:
                    dx = (abs(dy) * target_r) * (1 if dx > 0 else -1)
            
            nx, ny = ax + dx, ay + dy
            if self.drag_mode == "nw": x1, y1 = nx, ny
            elif self.drag_mode == "ne": x2, y1 = nx, ny
            elif self.drag_mode == "sw": x1, y2 = nx, ny
            elif self.drag_mode == "se": x2, y2 = nx, ny
            
            self._draw_crop_ui(x1, y1, x2, y2)

    def _mr(self, e):
        if not hasattr(self, '_current_crop') or not self._current_crop:
            return
        x1, y1, x2, y2 = self._current_crop
        cw, ch = x2 - x1, y2 - y1
        if cw < 10 or ch < 10:
            self.clear_crop()
            self.crop_lbl.configure(text="🔲 Sin zona seleccionada", text_color="#6b7280")
            return

        self.crop_rect_canvas = (x1, y1, cw, ch)
        self.crop_lbl.configure(text=f"✂ Zona fijada (proporción {self.ratio_var.get()})", text_color="#a78bfa")

    def clear_crop(self):
        self.crop_rect_canvas = None
        self._current_crop = None
        self.canvas.delete("crop_ui")
        if hasattr(self, 'crop_lbl'):
            self.crop_lbl.configure(text="🔲 Sin zona seleccionada — ancho completo", text_color="#6b7280")

    # ─────────────────────────── DOWNLOAD ───────────────────────
    def browse_folder(self):
        d = filedialog.askdirectory()
        if d:
            self.folder_var.set(d)
            self._save_config()

    def _crf(self):
        mapping = {"Alta (CRF 18)": "18", "Media (CRF 23)": "23", "Pequeño (CRF 28)": "28"}
        return mapping.get(self.quality_var.get(), "23")

    def download(self):
        if not self.video_info:
            messagebox.showwarning("⚠ Aviso", "Primero carga un video")
            return
        url   = self.url_entry.get().strip()
        start = self.start_e.get().strip() or "00:00"
        end   = self.end_e.get().strip()
        out   = self.folder_var.get()
        if not end:
            messagebox.showwarning("⚠ Aviso", "Pon un tiempo de fin")
            return

        title = self.video_info.get("title", "video")
        win   = DownloadWindow(self, title_text=title)
        self.dl_btn.configure(state="disabled")
        threading.Thread(target=self._dl_thread,
                         args=(url, start, end, out, win), daemon=True).start()

    def _dl_thread(self, url, start, end, out_folder, win):
        """Hilo de descarga.
        1) yt-dlp --get-url  → URL directa del stream progresivo MP4
        2) ffmpeg -ss {start} -i {url} -t {dur}  → descarga y encodea solo el fragmento
        Sin archivo temporal, sin merge, sin deadlock.
        """
        _proc = [None]

        def _w(fn):
            try:
                if win.winfo_exists():
                    win.after(0, fn)
            except Exception:
                pass

        def _cancel():
            if _proc[0]:
                try: _proc[0].kill()
                except Exception: pass

        win.on_cancel = _cancel

        try:
            safe_title = "".join(c for c in (self.video_info.get("title", "video"))
                                 if c.isalnum() or c in " _-")[:40].strip()
            
            export_mode = self.export_var.get() if hasattr(self, 'export_var') else "Video + Audio"
            is_audio_only = (export_mode == "Solo audio (sin video)")
            is_video_only = (export_mode == "Solo Video (sin audio)")
            
            max_res = self.res_var.get().split("p")[0]
            
            if is_audio_only:
                fmt_str = "bestaudio/best"
                final = os.path.join(out_folder, f"{safe_title}.mp3")
                temp_ext = "m4a"
            elif is_video_only:
                fmt_str = f"bestvideo[height<={max_res}][ext=mp4]/best[height<={max_res}][ext=mp4]/best"
                final = os.path.join(out_folder, f"{safe_title}.mp4")
                temp_ext = "mp4"
            else:
                fmt_str = f"bestvideo[height<={max_res}][ext=mp4]+bestaudio[ext=m4a]/best[height<={max_res}][ext=mp4]/best[ext=mp4]/best"
                final = os.path.join(out_folder, f"{safe_title}.mp4")
                temp_ext = "mp4"

            start_sec = self._time_to_sec(start)
            end_sec   = self._time_to_sec(end)
            dur_total = max(end_sec - start_sec, 1.0)

            _w(lambda: win.set_indeterminate("⏳ Obteniendo URL del stream…"))
            _w(lambda: win.set_detail(f"{start} → {end}  |  {dur_total:.0f}s"))

            _w(lambda: win.set_indeterminate("⏳ Obteniendo enlace seguro y cabeceras…"))

            r = subprocess.run(
                [YTDLP, "-J", "--no-playlist", "-f", fmt_str, url],
                capture_output=True, text=True, timeout=30
            )
            
            if r.returncode != 0:
                raise RuntimeError("yt-dlp no devolvió info.\n" + r.stderr[-300:])
                
            info = json.loads(r.stdout)
            stream_url = info.get("url")
            
            # En caso de formatos separados, sacar la URL del primer formato pedido
            if not stream_url:
                formats = info.get("requested_formats", [])
                if formats:
                    stream_url = formats[0].get("url")
                    
            if not stream_url:
                raise RuntimeError("No se encontró la URL directa del stream.")

            http_headers = info.get("http_headers", {})
            headers_str = "".join([f"{k}: {v}\r\n" for k, v in http_headers.items()])

            _w(lambda u=stream_url: win.log(f"Stream OK: {u[:80]}…\n"))
            _w(lambda: win.set_progress(0.05, "5 %"))

            # ── FASE 2: yt-dlp descarga el fragmento EXACTO (Usa API de YouTube) ────
            _w(lambda: win.set_indeterminate("⏳ Descargando fragmento exacto desde YouTube…"))
            
            temp_file = os.path.join(out_folder, f"temp_{int(time.time())}.{temp_ext}")

            ytdlp_cmd = [
                YTDLP, "-f", fmt_str,
                "--download-sections", f"*{start_sec}-{end_sec}",
                "--force-keyframes-at-cuts",
                "-o", temp_file,
                url
            ]

            p_ytdlp = subprocess.Popen(
                ytdlp_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
                text=True, encoding="utf-8", errors="replace"
            )
            _proc[0] = p_ytdlp
            
            for raw in p_ytdlp.stdout:
                if win.cancelled:
                    p_ytdlp.kill()
                    break
                win.log(raw.strip())
            
            p_ytdlp.wait()
            if win.cancelled:
                if os.path.exists(temp_file): os.remove(temp_file)
                return
            if p_ytdlp.returncode != 0:
                if os.path.exists(temp_file): os.remove(temp_file)
                raise RuntimeError("yt-dlp falló al descargar el fragmento exacto.")

            # ── FASE 3: ffmpeg procesa el archivo local (Recorte y Formato) ────
            _w(lambda: win.set_indeterminate("✂  Aplicando recorte y formato final…"))

            vf_parts = []
            if getattr(self, "crop_rect_canvas", None):
                cx, cy, cw, ch = self.crop_rect_canvas
                
                # Las coordenadas ahora son porcentajes relativos al canvas (0.0 a 1.0)
                # Así dejamos que ffmpeg multiplique por 'iw' (input width) y 'ih' (input height),
                # garantizando que el recorte nunca se salga de los bordes reales del video.
                cw_val = getattr(self, 'cw', PREVIEW_W)
                ch_val = getattr(self, 'ch', PREVIEW_H)
                pw = cw / cw_val
                ph = ch / ch_val
                px = cx / cw_val
                py = cy / ch_val
                
                # trunc(val/2)*2 asegura que los píxeles sean siempre números pares (requerido por h264)
                vf_parts.append(
                    f"crop=trunc(iw*{pw}/2)*2:trunc(ih*{ph}/2)*2:trunc(iw*{px}/2)*2:trunc(ih*{py}/2)*2"
                )

            ff_cmd = [
                FFMPEG, "-y",
                "-i", temp_file,
            ]
            if is_audio_only:
                ff_cmd += ["-vn", "-c:a", "libmp3lame", "-q:a", "2"]
            else:
                if vf_parts or self.force_encode_var.get() or is_video_only or self.quality_var.get() != "Alta (CRF 18)":
                    if vf_parts:
                        ff_cmd += ["-vf", ",".join(vf_parts)]
                    
                    # Usamos el CRF seleccionado por el usuario
                    ff_cmd += ["-c:v", "libx264", "-crf", self._crf(), "-preset", "veryfast"]
                    
                    if is_video_only:
                        ff_cmd += ["-an"]
                    else:
                        ff_cmd += ["-c:a", "copy"]
                    ff_cmd += ["-movflags", "+faststart"]
                else:
                    ff_cmd += ["-c:v", "copy"]
                    if is_video_only:
                        ff_cmd += ["-an"]
                    else:
                        ff_cmd += ["-c:a", "copy"]
                    ff_cmd += ["-movflags", "+faststart"]
            ff_cmd += [
                "-progress", "pipe:1",
                "-nostats",
                final
            ]

            ff = subprocess.Popen(
                ff_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, # Unir stderr con stdout previene el deadlock
                text=True, encoding="utf-8", errors="replace"
            )
            _proc[0] = ff

            _last_fps = "0"
            _last_speed = "0x"
            
            for raw in ff.stdout:
                if win.cancelled:
                    ff.kill()
                    break
                kv = raw.strip()
                
                # Ocultar la salida gigante de metadata en el log de UI (opcional) pero mostrar progreso
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    if k == "fps":
                        _last_fps = v
                    elif k == "speed":
                        _last_speed = v
                    elif k == "out_time_ms":
                        try:
                            secs = int(v) / 1_000_000
                            pct  = min(secs / dur_total, 1.0)
                            bar  = 0.05 + pct * 0.90    # 5 % → 95 %
                            
                            # Construimos un string informativo y vivo
                            detail = f"✂ Renderizando: {pct*100:.0f}%  ({secs:.1f}s / {dur_total:.1f}s)  |  FPS: {_last_fps}  |  Velocidad: {_last_speed}"
                            _w(lambda b=bar, d=detail: win.set_progress(b, d))
                        except Exception:
                            pass
                
                # Evitar llenar el text widget demasiado rapido, lo que traba tkinter
                if kv.startswith("out_time=") or kv.startswith("frame=") or kv.startswith("speed="):
                    continue
                _w(lambda l=kv: win.log(l))

            ff.wait()
            
            # Limpiar archivo temporal
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception:
                pass
                
            if win.cancelled:
                return
            if ff.returncode != 0:
                err_out = ff.stderr.read()[-600:]
                raise RuntimeError(err_out)

            size_mb = os.path.getsize(final) / 1024 / 1024
            msg = f"✅ Listo!  {size_mb:.1f} MB  —  {os.path.basename(final)}"
            _w(lambda: win.done(msg))
            _w(lambda: win.log(f"\n--- Guardado en: {final} ---"))
            self.after(0, lambda: self._set_status(msg))
            self.after(0, lambda: self.dl_btn.configure(state="normal"))

        except Exception as e:
            try:
                if 'temp_file' in locals() and os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception:
                pass
            
            err = str(e)
            _w(lambda: win.error(f"❌ {err[:120]}"))
            _w(lambda: win.log(f"\nERROR: {err}"))
            self.after(0, lambda: self._set_status(f"❌ {err[:160]}"))
            self.after(0, lambda: self.dl_btn.configure(state="normal"))


    def _take_snapshot(self):
        if not getattr(self, 'stream_url', None): return
        head_sec = self.timeline.head_sec
        threading.Thread(target=self._snapshot_thread, args=(head_sec,), daemon=True).start()

    def _snapshot_thread(self, head_sec):
        def _w(fn):
            self.after(0, fn)

        _w(lambda: self._set_status("📸 Buscando stream de alta resolución..."))
        
        # Intentar obtener la mejor resolución de video disponible
        try:
            url_input = self.url_entry.get().strip()
            res = subprocess.run(
                [YTDLP, "-f", "bestvideo/best", "--get-url", "--no-playlist", url_input],
                capture_output=True, text=True, timeout=15
            )
            hr_url = res.stdout.strip().splitlines()[0] if res.stdout.strip() else self.stream_url
        except Exception:
            hr_url = self.stream_url

        if not hr_url:
            _w(lambda: self._set_status("❌ Error: No se pudo obtener el stream."))
            return

        _w(lambda: self._set_status("📸 Extrayendo fotograma en alta resolución..."))
        
        vf_parts = []
        if getattr(self, "crop_rect_canvas", None):
            cx, cy, cw, ch = self.crop_rect_canvas
            cw_val = getattr(self, 'cw', PREVIEW_W)
            ch_val = getattr(self, 'ch', PREVIEW_H)
            pw, ph = cw / cw_val, ch / ch_val
            px, py = cx / cw_val, cy / ch_val
            # Usamos iw e ih de ffmpeg para escalar el recorte a la resolución real del stream
            vf_parts.append(f"crop=trunc(iw*{pw}/2)*2:trunc(ih*{ph}/2)*2:trunc(iw*{px}/2)*2:trunc(ih*{py}/2)*2")
            
        vf_arg = ["-vf", ",".join(vf_parts)] if vf_parts else []
            
        out_folder = self.folder_var.get() or "downloads"
        os.makedirs(out_folder, exist_ok=True)
        
        title = self.video_info.get("title", "snapshot") if self.video_info else "snapshot"
        title = "".join(c for c in title if c.isalnum() or c in " -_").strip()
        out_file = os.path.join(out_folder, f"snap_{title}_{int(head_sec)}s.png")
        
        cmd = [
            FFMPEG, "-y",
            "-ss", str(head_sec),
            "-i", hr_url,
            "-vframes", "1",
            "-q:v", "1" # Calidad máxima para el encoder (1-31, 1 es mejor)
        ] + vf_arg + [out_file]
        
        try:
            subprocess.run(cmd, capture_output=True, check=True)
            _w(lambda: self._set_status(f"✅ Captura guardada en: {out_folder}"))
        except Exception as e:
            _w(lambda: self._set_status(f"❌ Error al capturar: {str(e)[:40]}"))


    def _set_status(self, msg):
        self.status_lbl.configure(text=msg)

    def _log(self, line):
        """Append a line to the log console (called from any thread via self.after)."""
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line.rstrip() + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _log_clear(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("0.0", "end")
        self.log_box.configure(state="disabled")

    @staticmethod
    def _time_to_sec(t: str) -> float:
        """MM:SS or HH:MM:SS -> seconds."""
        parts = t.strip().split(":")
        try:
            if len(parts) == 2:
                return int(parts[0]) * 60 + float(parts[1])
            elif len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        except Exception:
            pass
        return 0.0


def _fmt_time(seconds: float) -> str:
    """Convierte segundos a MM:SS o HH:MM:SS (helper para el callback de preview)."""
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


if __name__ == "__main__":
    app = TubeSniper()
    app.mainloop()
