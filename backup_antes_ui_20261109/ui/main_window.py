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

from core.utils import (PROJECT_ROOT, FFMPEG, NO_WINDOW, fmt_time, time_to_sec, parse_time,
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
from core.downloader import DownloadJob, crop_filter

PREVIEW_W = 640
PREVIEW_H = 360

class TubeSniperUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("TubeSniper 🎯")
        self.geometry("1020x780")
        self.resizable(True, True)
        self.configure(fg_color="#0d0d1a")

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

    # Build UI is mostly identical to previous app.py, keeping it concise but complete
    def _build_ui(self):
        # ── LEFT PANEL ──
        left = ctk.CTkFrame(self, width=330, fg_color="#12122a", corner_radius=16)
        left.pack(side="left", fill="y", padx=(12, 6), pady=12)
        left.pack_propagate(False)
        self.left = left

        ctk.CTkLabel(left, text="🎯 TubeSniper", font=ctk.CTkFont("Segoe UI", 22, "bold"), text_color="#a78bfa").pack(pady=(18, 4))
        ctk.CTkLabel(left, text="Descarga fragmentos de YouTube", font=ctk.CTkFont("Segoe UI", 11), text_color="#6b7280").pack(pady=(0, 14))

        self.tabs = ctk.CTkTabview(left, fg_color="#1e1e3a", corner_radius=10, height=450)
        self.tabs.pack(fill="x", padx=10, pady=(4, 8))
        tab_main = self.tabs.add("Principal")
        tab_render = self.tabs.add("Pre-Render")

        # URL
        ctk.CTkLabel(tab_main, text="URL del video", anchor="w", font=ctk.CTkFont("Segoe UI", 12, "bold")).pack(fill="x", padx=6)
        self.url_entry = ctk.CTkEntry(tab_main, placeholder_text="https://...", height=36, corner_radius=8)
        self.url_entry.pack(fill="x", padx=6, pady=(4, 8))

        self.load_btn = ctk.CTkButton(tab_main, text="⚡ Cargar Video", height=38, corner_radius=8, fg_color="#7c3aed", hover_color="#5b21b6", font=ctk.CTkFont("Segoe UI", 13, "bold"), command=self.load_video)
        self.load_btn.pack(fill="x", padx=6, pady=(0, 6))

        # Info box
        info = ctk.CTkFrame(tab_main, fg_color="#1e1e3a", corner_radius=10)
        info.pack(fill="x", padx=6, pady=(0, 12))
        self.title_lbl = ctk.CTkLabel(info, text="—", wraplength=270, font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color="#e2e8f0")
        self.title_lbl.pack(padx=10, pady=(8, 2))
        self.dur_lbl = ctk.CTkLabel(info, text="Duración: —", font=ctk.CTkFont("Segoe UI", 11), text_color="#94a3b8")
        self.dur_lbl.pack(padx=10, pady=(0, 8))

        # Time range
        ctk.CTkLabel(tab_main, text="Recorte de tiempo", anchor="w", font=ctk.CTkFont("Segoe UI", 12, "bold")).pack(fill="x", padx=6)
        tframe = ctk.CTkFrame(tab_main, fg_color="transparent")
        tframe.pack(fill="x", padx=6, pady=(4, 12))
        tframe.columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(tframe, text="Inicio (MM:SS)", font=ctk.CTkFont("Segoe UI", 10), text_color="#94a3b8").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(tframe, text="Fin (MM:SS)", font=ctk.CTkFont("Segoe UI", 10), text_color="#94a3b8").grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.start_e = ctk.CTkEntry(tframe, placeholder_text="00:00", height=34, corner_radius=8)
        self.start_e.grid(row=1, column=0, sticky="ew")
        self.end_e = ctk.CTkEntry(tframe, placeholder_text="00:30", height=34, corner_radius=8)
        self.end_e.grid(row=1, column=1, sticky="ew", padx=(8, 0))
        # Lo que se escribe a mano también mueve los marcadores de la línea de tiempo.
        for entry in (self.start_e, self.end_e):
            entry.bind("<Return>", self._entries_changed)
            entry.bind("<FocusOut>", self._entries_changed)

        # Output folder
        ctk.CTkLabel(tab_main, text="Carpeta de salida", anchor="w", font=ctk.CTkFont("Segoe UI", 12, "bold")).pack(fill="x", padx=6)
        frow = ctk.CTkFrame(tab_main, fg_color="transparent")
        frow.pack(fill="x", padx=6, pady=(4, 12))
        frow.columnconfigure(0, weight=1)
        self.folder_var = tk.StringVar()
        self.folder_e = ctk.CTkEntry(frow, textvariable=self.folder_var, height=34, corner_radius=8)
        self.folder_e.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(frow, text="…", width=36, height=34, corner_radius=8, fg_color="#1e1e3a", hover_color="#2d2d5e", command=self.browse_folder).grid(row=0, column=1, padx=(6, 0))

        # ── TAB PRE-RENDERIZADO ──
        self.force_encode_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(tab_render, text="Forzar recodificación de video", variable=self.force_encode_var, command=self._save_config, font=ctk.CTkFont("Segoe UI", 11), text_color="#e2e8f0").pack(anchor="w", padx=16, pady=(12, 6))
        
        # Formato contenedor
        ctk.CTkLabel(tab_render, text="Formato de salida", anchor="w", font=ctk.CTkFont("Segoe UI", 12, "bold")).pack(fill="x", padx=16)
        self.format_var = ctk.StringVar()
        self.format_opt = ctk.CTkOptionMenu(tab_render, values=["MP4", "GIF", "MP3", "WAV"], variable=self.format_var, height=34, corner_radius=8, fg_color="#1e1e3a", button_color="#7c3aed", button_hover_color="#5b21b6", command=self._save_config)
        self.format_opt.pack(fill="x", padx=16, pady=(0, 6))

        # Codec de video
        ctk.CTkLabel(tab_render, text="Codec de video", anchor="w", font=ctk.CTkFont("Segoe UI", 12, "bold")).pack(fill="x", padx=16)
        self.codec_var = ctk.StringVar()
        self.codec_opt = ctk.CTkOptionMenu(tab_render, values=["H.264 (AVC)", "H.265 (HEVC)", "VP9"], variable=self.codec_var, height=34, corner_radius=8, fg_color="#1e1e3a", button_color="#7c3aed", button_hover_color="#5b21b6", command=self._save_config)
        self.codec_opt.pack(fill="x", padx=16, pady=(0, 6))

        # Resolución
        ctk.CTkLabel(tab_render, text="Resolución Máx.", anchor="w", font=ctk.CTkFont("Segoe UI", 12, "bold")).pack(fill="x", padx=16)
        self.res_var = ctk.StringVar()
        self.res_opt = ctk.CTkOptionMenu(tab_render, values=["2160p (4K)", "1440p (2K)", "1080p (HD)", "720p (HD)", "480p (SD)"], variable=self.res_var, height=34, corner_radius=8, fg_color="#1e1e3a", button_color="#7c3aed", button_hover_color="#5b21b6", command=self._save_config)
        self.res_opt.pack(fill="x", padx=16, pady=(0, 6))
        
        # Calidad
        ctk.CTkLabel(tab_render, text="Calidad / Compresión", anchor="w", font=ctk.CTkFont("Segoe UI", 12, "bold")).pack(fill="x", padx=16)
        self.quality_var = ctk.StringVar()
        self.quality_opt = ctk.CTkOptionMenu(tab_render, values=["Alta (CRF 18)", "Media (CRF 23)", "Pequeño (CRF 28)"], variable=self.quality_var, height=34, corner_radius=8, fg_color="#1e1e3a", button_color="#7c3aed", button_hover_color="#5b21b6", command=self._save_config)
        self.quality_opt.pack(fill="x", padx=16, pady=(0, 12))
        
        # Export mode (left panel)
        self.export_var = ctk.StringVar()
        self.export_opt = ctk.CTkOptionMenu(left, values=["Video + Audio", "Solo Video", "Solo Audio"], variable=self.export_var, height=34, corner_radius=10, fg_color="#1e1e3a", button_color="#3b82f6", button_hover_color="#2563eb", command=self._save_config)
        self.export_opt.pack(fill="x", padx=16, pady=(12, 4))

        # Download btn
        dl_row = ctk.CTkFrame(left, fg_color="transparent")
        dl_row.pack(fill="x", padx=16, pady=(4, 8))
        self.dl_btn = ctk.CTkButton(dl_row, text="⬇  Descargar", height=44, corner_radius=10, fg_color="#059669", hover_color="#047857", font=ctk.CTkFont("Segoe UI", 14, "bold"), command=self.download)
        self.dl_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.snap_btn = ctk.CTkButton(dl_row, text="📸", width=44, height=44, corner_radius=10, font=ctk.CTkFont("Segoe UI", 18), fg_color="#1e1e3a", hover_color="#2d2d5e", command=self._take_snapshot, state="disabled")
        self.snap_btn.pack(side="right")

        # Progress + status
        self.status_lbl = ctk.CTkLabel(left, text="Pega URL y carga el video", wraplength=290, justify="left", font=ctk.CTkFont("Segoe UI", 11), text_color="#94a3b8")
        self.status_lbl.pack(padx=16, pady=(0, 6))

        # ── RIGHT PANEL ──
        self.right = ctk.CTkFrame(self, fg_color="#0d0d1a", corner_radius=0)
        self.right.pack(side="right", fill="both", expand=True, padx=(6, 12), pady=12)

        self.canvas_frame = ctk.CTkFrame(self.right, fg_color="#1a1a2e", corner_radius=12, border_width=1, border_color="#2d2d5e")
        self.canvas_frame.pack(fill="both", expand=True, padx=4, pady=4)
        self.canvas = tk.Canvas(self.canvas_frame, bg="#0f0f23", highlightthickness=0, cursor="crosshair")
        self.canvas.place(relx=0.5, rely=0.5, anchor="center", width=PREVIEW_W, height=PREVIEW_H)
        self.cw = PREVIEW_W
        self.ch = PREVIEW_H
        self.canvas_frame.bind("<Configure>", self._on_canvas_resize)

        cut_row = ctk.CTkFrame(self.right, fg_color="transparent")
        cut_row.pack(fill="x", padx=16, pady=(12, 6))
        self.ratio_var = ctk.StringVar(value="Free")
        self.ratio_opt = ctk.CTkSegmentedButton(cut_row, values=["Free", "16:9", "9:16", "1:1"], variable=self.ratio_var, command=self._save_config)
        self.ratio_opt.pack(side="left", padx=8)
        ctk.CTkButton(cut_row, text="🔳 Clean", width=60, height=28, corner_radius=6, command=self.clear_crop).pack(side="left", padx=12)
        self.crop_lbl = ctk.CTkLabel(self.right, text="🔲 Sin zona seleccionada", font=ctk.CTkFont("Segoe UI", 11), text_color="#6b7280")
        self.crop_lbl.pack(anchor="w", padx=20, pady=(0, 6))

        # Playback timeline
        tl_row = ctk.CTkFrame(self.right, fg_color="transparent")
        tl_row.pack(fill="x", padx=16, pady=(6, 0))
        self.timeline = TimelineBar(tl_row, duration=60, on_change=self._timeline_changed, on_seek=self._seek)
        self.timeline.pack(fill="x", expand=True, padx=0)

        # ── ROW 3: Play Controls ──
        self.play_ctrl = ctk.CTkFrame(self.right, fg_color="transparent")
        self.play_ctrl.pack(fill="x", padx=16, pady=(6, 12))
        
        btn_font = ctk.CTkFont("Segoe UI", 15)
        self.play_btn = ctk.CTkButton(self.play_ctrl, text="▶", width=36, height=32, corner_radius=6, font=btn_font, fg_color="#1e1e3a", hover_color="#2d2d5e", text_color="#f9fafb", command=self._toggle_play, state="disabled")
        self.play_btn.pack(side="left", padx=2)

        self.play_start_btn = ctk.CTkButton(self.play_ctrl, text="⏯", width=36, height=32, corner_radius=6, font=btn_font, fg_color="#1e1e3a", hover_color="#2d2d5e", text_color="#f9fafb", command=self._play_from_start, state="disabled")
        self.play_start_btn.pack(side="left", padx=2)

        self.loop_play_var = tk.BooleanVar(value=True)
        self.loop_btn = ctk.CTkCheckBox(self.play_ctrl, text="🔁", variable=self.loop_play_var, width=44, corner_radius=6, fg_color="#8b5cf6", hover_color="#7c3aed", border_width=0, font=btn_font)
        self.loop_btn.pack(side="left", padx=(8, 2))

        self.sync_cursor_var = tk.BooleanVar(value=False)
        self.pin_btn = ctk.CTkCheckBox(self.play_ctrl, text="📌", variable=self.sync_cursor_var, width=44, corner_radius=6, fg_color="#8b5cf6", hover_color="#7c3aed", border_width=0, font=btn_font)
        self.pin_btn.pack(side="left", padx=2)

        self.zoom_btn = ctk.CTkButton(self.play_ctrl, text="🔍", width=36, height=32, corner_radius=6, font=btn_font, fg_color="#1e1e3a", hover_color="#2d2d5e", text_color="#f9fafb", command=self._toggle_zoom, state="disabled")
        self.zoom_btn.pack(side="left", padx=2)

        ctk.CTkButton(self.play_ctrl, text="◂", width=32, height=32, corner_radius=6, fg_color="#10b981", hover_color="#059669", font=ctk.CTkFont("Segoe UI", 16, "bold"), text_color="#fff", command=self._set_start_here).pack(side="left", padx=(8, 2))
        ctk.CTkButton(self.play_ctrl, text="▸", width=32, height=32, corner_radius=6, fg_color="#f43f5e", hover_color="#e11d48", font=ctk.CTkFont("Segoe UI", 16, "bold"), text_color="#fff", command=self._set_end_here).pack(side="left", padx=2)

        self.time_lbl = ctk.CTkLabel(self.play_ctrl, text="00:00 / 00:00", font=ctk.CTkFont("Segoe UI", 12), text_color="#e2e8f0")
        self.time_lbl.pack(side="right", padx=0)

        # Basic mouse bindings
        self.canvas.bind("<ButtonPress-1>", self._mp)
        self.canvas.bind("<B1-Motion>", self._md)
        self.canvas.bind("<ButtonRelease-1>", self._mr)

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

        self.title_lbl.configure(text=title[:55])
        self.dur_lbl.configure(text=f"⏱  Duración: {fmt_time(dur)}")
        self.start_e.delete(0, "end"); self.start_e.insert(0, fmt_time(0))
        self.end_e.delete(0, "end"); self.end_e.insert(0, fmt_time(dur))
        self.load_btn.configure(state="normal")
        self.play_btn.configure(state="normal", text="▶")
        self.play_start_btn.configure(state="normal")
        self.zoom_btn.configure(state="normal")
        self.snap_btn.configure(state="normal")
        self.timeline.duration = max(dur, 1.0)
        self.timeline.zoomed = False
        self.timeline.set_range(0.0, dur)
        self.timeline.set_head(0.0)
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

    def _timeline_changed(self, start, end):
        self.start_e.delete(0, "end"); self.start_e.insert(0, fmt_time(start))
        self.end_e.delete(0, "end"); self.end_e.insert(0, fmt_time(end))
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
        if not hasattr(self, 'timeline'): return
        self.timeline.toggle_zoom()
        if getattr(self.timeline, 'zoomed', False):
            self.zoom_btn.configure(fg_color="#8b5cf6", hover_color="#7c3aed")
        else:
            self.zoom_btn.configure(fg_color="#1e1e3a", hover_color="#2d2d5e")

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

    def _toggle_play(self):
        if not self.stream_url: return
        start_sec = time_to_sec(self.start_e.get())
        end_sec = time_to_sec(self.end_e.get())
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
        self.canvas.create_text(cx, cy + 45, text="Cargando...", fill="#e2e8f0", font=("Segoe UI", 12), tags="loading")
        self.after(50, lambda: self._animate_loading(step + 1))

    def _start_player(self, seek: float = 0.0):
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
        self.play_btn.configure(text="⏸", text_color="#dc2626")

    def _seek(self, sec: float):
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
                    self.play_btn.configure(text="▶", text_color="#059669")

    # Crop logic advanced
    def _draw_crop_ui(self, x1, y1, x2, y2):
        self.canvas.delete("crop_ui")
        x_min, x_max = min(x1, x2), max(x1, x2)
        y_min, y_max = min(y1, y2), max(y1, y2)
        self._current_crop = (x_min, y_min, x_max, y_max)
        W, H = getattr(self, 'cw', PREVIEW_W), getattr(self, 'ch', PREVIEW_H)
        fill_color, stipple = "black", "gray50"
        self.canvas.create_rectangle(0, 0, W, y_min, fill=fill_color, stipple=stipple, outline="", tags="crop_ui")
        self.canvas.create_rectangle(0, y_max, W, H, fill=fill_color, stipple=stipple, outline="", tags="crop_ui")
        self.canvas.create_rectangle(0, y_min, x_min, y_max, fill=fill_color, stipple=stipple, outline="", tags="crop_ui")
        self.canvas.create_rectangle(x_max, y_min, W, y_max, fill=fill_color, stipple=stipple, outline="", tags="crop_ui")
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
        self.crop_lbl.configure(text=f"✂ Zona fijada ({self.ratio_var.get()})", text_color="#a78bfa")

    def clear_crop(self):
        self.crop_rect_canvas = None
        self._current_crop = None
        self.canvas.delete("crop_ui")
        self.crop_lbl.configure(text="🔲 Sin zona seleccionada", text_color="#6b7280")

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
            def done(self, msg, path): self._call("done", msg)
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
