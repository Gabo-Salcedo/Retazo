"""
core/downloader.py
──────────────────
Descarga un fragmento con yt-dlp y lo procesa con ffmpeg.

Principios (inspirados en el exportador de Cap):
- Todo se escribe en una carpeta temporal propia y el resultado final se mueve
  con un rename atómico: un fallo o una cancelación nunca deja archivos a
  medias ni .part sueltos en la carpeta del usuario.
- La cancelación es una bandera propia del trabajo (threading.Event), que no
  depende de que la ventana de progreso siga viva.
- Nunca se sobrescribe un archivo existente: se genera un nombre único.
"""
import collections
import glob
import os
import re
import shutil
import subprocess
import threading
import time

from .utils import FFMPEG, NO_WINDOW, ytdlp_cmd, safe_filename, unique_path, fmt_time

GIF_FPS_DEFAULT = 15
GIF_WIDTH_DEFAULT = 720

_YTDLP_PCT = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
_DURATION = re.compile(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)")


class DownloadCancelled(Exception):
    pass


class UserFacingError(Exception):
    """Error con explicación para el usuario (título + qué hacer)."""
    def __init__(self, title, hint=""):
        super().__init__(title)
        self.title, self.hint = title, hint


def crop_filter(crop_coords, canvas_w, canvas_h):
    """Rectángulo en coords del canvas → filtro crop de ffmpeg en proporciones
    (independiente de la resolución real descargada). None si no hay recorte."""
    if not crop_coords or not canvas_w or not canvas_h:
        return None
    cx, cy, w, h = crop_coords
    px = max(0.0, min(cx / canvas_w, 1.0))
    py = max(0.0, min(cy / canvas_h, 1.0))
    pw = max(0.01, min(w / canvas_w, 1.0 - px))
    ph = max(0.01, min(h / canvas_h, 1.0 - py))
    return (f"crop=trunc(iw*{pw:.6f}/2)*2:trunc(ih*{ph:.6f}/2)*2:"
            f"trunc(iw*{px:.6f}/2)*2:trunc(ih*{py:.6f}/2)*2")


class DownloadJob:
    def __init__(self, url, start_sec, end_sec, out_folder, config, video_info, crop_coords, cw, ch, win_callbacks):
        self.url = url
        self.start_sec = float(start_sec)
        self.end_sec = float(end_sec)
        self.out_folder = out_folder
        self.config = dict(config)           # copia: la UI puede cambiar ajustes mientras corre
        self.video_info = video_info or {}
        self.crop_coords = crop_coords       # (cx, cy, cw, ch) en coords del canvas, o None
        self.cw = cw
        self.ch = ch
        self.win = win_callbacks             # .log/.set_progress/.set_indeterminate/.set_detail/.done/.error
        self._proc = None
        self._cancel = threading.Event()
        self._tail = collections.deque(maxlen=40)   # últimas líneas de salida, para mensajes de error

    # ── cancelación ─────────────────────────────────────────────
    def cancel(self):
        self._cancel.set()
        proc = self._proc
        if proc and proc.poll() is None:
            # yt-dlp lanza ffmpeg como hijo: hay que matar el árbol entero o el
            # hijo sigue escribiendo y mantiene abierta la tubería de salida.
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                                   capture_output=True, timeout=10, creationflags=NO_WINDOW)
                else:
                    proc.kill()
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def is_cancelled(self):
        if self._cancel.is_set():
            return True
        try:
            return bool(self.win.is_cancelled())
        except Exception:
            return False

    def _check_cancel(self):
        if self.is_cancelled():
            raise DownloadCancelled()

    # ── helpers ─────────────────────────────────────────────────
    def get_crf(self):
        mapping = {"Alta (CRF 18)": "18", "Media (CRF 23)": "23", "Pequeño (CRF 28)": "28"}
        return mapping.get(self.config.get("quality", "Media (CRF 23)"), "23")

    def _stream(self, cmd, on_line):
        """Ejecuta cmd leyendo stdout+stderr línea a línea. Devuelve el returncode."""
        self._check_cancel()
        self._tail.clear()                 # el error debe mostrar solo la salida de ESTE proceso
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
        )
        if self._cancel.is_set():          # cancelado justo mientras arrancaba
            self.cancel()
        for raw in self._proc.stdout:
            line = raw.rstrip()
            if line:
                self._tail.append(line)
                on_line(line)
        self._proc.wait()
        self._check_cancel()
        return self._proc.returncode

    def _error_tail(self, n=8):
        """Líneas útiles de la salida: primero las que parecen errores; si no
        hay, las últimas (sin pares clave=valor ni progreso)."""
        lines = [l for l in self._tail
                 if not re.match(r"^[a-z_]+=\S*$", l) and not _YTDLP_PCT.search(l)]
        errs = [l for l in lines if re.search(r"error|invalid|does not contain|unable|not found|denied|forbidden|unavailable|login|sign in", l, re.I)]
        return "\n".join((errs or lines)[-n:])

    @staticmethod
    def _probe(path):
        """(duración_s | None, tiene_video, tiene_audio) leyendo la cabecera con ffmpeg."""
        try:
            probe = subprocess.run([FFMPEG, "-hide_banner", "-i", path], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=30, creationflags=NO_WINDOW)
            err = probe.stderr
        except Exception:
            return None, True, True
        dur = None
        m = _DURATION.search(err)
        if m:
            dur = float(m.group(1)) * 3600 + float(m.group(2)) * 60 + float(m.group(3))
        streams = [l for l in err.splitlines() if re.search(r"Stream #\S+", l)]
        has_video = any(": Video:" in l and "attached pic" not in l for l in streams)   # portada ≠ vídeo
        has_audio = any(": Audio:" in l for l in streams)
        if not streams:                          # no se pudo leer: no bloquear
            return dur, True, True
        return dur, has_video, has_audio

    @classmethod
    def _probe_duration(cls, path):
        return cls._probe(path)[0]

    @staticmethod
    def _rmtree_retry(path):
        # En Windows un proceso recién terminado puede retener el archivo un instante.
        for _ in range(10):
            if not os.path.exists(path):
                return
            shutil.rmtree(path, ignore_errors=True)
            if not os.path.exists(path):
                return
            time.sleep(0.3)

    # ── plan de exportación ─────────────────────────────────────
    def _plan(self):
        export_mode = self.config.get("export_mode", "Video + Audio")
        target_format = self.config.get("format", "MP4")
        max_res = str(self.config.get("resolution", "1080p")).split("p")[0].strip() or "1080"

        if target_format in ("MP3", "WAV"):
            kind, final_ext = "audio", target_format.lower()
        elif target_format == "GIF":
            kind, final_ext = "gif", "gif"
        elif export_mode == "Solo Audio":
            kind, final_ext = "audio", "m4a"     # antes: MP3 dentro de un .mp4
        elif export_mode == "Solo Video":
            kind, final_ext = "video", "mp4"
        else:
            kind, final_ext = "av", "mp4"

        if kind == "audio":
            fmt_str = "bestaudio[ext=m4a]/bestaudio/best"
        elif kind == "gif":
            fmt_str = f"bestvideo[height<={max_res}]/best[height<={max_res}]/best"
        elif kind == "video":
            fmt_str = f"bestvideo[height<={max_res}][ext=mp4]/bestvideo[height<={max_res}]/best[height<={max_res}]/best"
        else:
            fmt_str = (f"bestvideo[height<={max_res}][ext=mp4]+bestaudio[ext=m4a]"
                       f"/bestvideo[height<={max_res}]+bestaudio"
                       f"/best[height<={max_res}][ext=mp4]/best[height<={max_res}]/best")
        return kind, final_ext, fmt_str

    def _crop_filter(self):
        return crop_filter(self.crop_coords, self.cw, self.ch)

    def build_ffmpeg_cmd(self, src, out, kind, needs_trim, dur_total):
        """Construye el comando ffmpeg (separado para poder probarlo)."""
        crop = self._crop_filter()
        target_codec = self.config.get("codec", "H.264 (AVC)")
        force_encode = bool(self.config.get("force_encode", False))
        quality = self.config.get("quality", "Alta (CRF 18)")

        cmd = [FFMPEG, "-hide_banner", "-y"]
        if needs_trim:
            # Con recodificación, -ss antes de -i es preciso al fotograma en ffmpeg moderno.
            cmd += ["-ss", f"{self.start_sec:.3f}"]
        cmd += ["-fflags", "+genpts+discardcorrupt", "-i", src]
        if needs_trim:
            cmd += ["-t", f"{dur_total:.3f}"]

        if kind == "audio":
            cmd += ["-vn"]
            ext = os.path.splitext(out)[1].lower()
            if ext == ".wav":
                cmd += ["-c:a", "pcm_s16le"]
            elif ext == ".mp3":
                cmd += ["-c:a", "libmp3lame", "-q:a", "2"]
            else:
                cmd += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
        elif kind == "gif":
            fps = int(self.config.get("gif_fps", GIF_FPS_DEFAULT))
            width = int(self.config.get("gif_width", GIF_WIDTH_DEFAULT))
            chain = [crop] if crop else []
            chain += [f"fps={fps}", f"scale='min({width},iw)':-2:flags=lanczos",
                      "split[s0][s1]", "[s0]palettegen=stats_mode=diff[p]",
                      "[s1][p]paletteuse=dither=bayer:bayer_scale=5"]
            cmd += ["-an", "-vf", ",".join(chain), "-loop", "0"]
        else:
            # Recorte local con copy cortaría en el keyframe más cercano → recodificar.
            needs_reencode = bool(crop) or force_encode or needs_trim \
                or quality != "Alta (CRF 18)" or target_codec != "H.264 (AVC)"
            if needs_reencode:
                if crop:
                    cmd += ["-vf", crop]
                if target_codec == "H.265 (HEVC)":
                    cmd += ["-c:v", "libx265", "-crf", self.get_crf(), "-preset", "fast", "-tag:v", "hvc1"]
                elif target_codec == "VP9":
                    cmd += ["-c:v", "libvpx-vp9", "-crf", self.get_crf(), "-b:v", "0", "-row-mt", "1"]
                else:
                    cmd += ["-c:v", "libx264", "-crf", self.get_crf(), "-preset", "veryfast"]
                cmd += ["-pix_fmt", "yuv420p"]
            else:
                cmd += ["-c:v", "copy"]
            cmd += ["-an"] if kind == "video" else ["-c:a", "copy"]
            cmd += ["-movflags", "+faststart"]

        cmd += ["-progress", "pipe:1", "-nostats", out]
        return cmd

    # ── ejecución ───────────────────────────────────────────────
    def run(self):
        win = self.win
        tmpdir = None
        try:
            dur_total = self.end_sec - self.start_sec
            if dur_total <= 0:
                raise RuntimeError("El fin del recorte debe ser posterior al inicio.")

            kind, final_ext, fmt_str = self._plan()
            os.makedirs(self.out_folder, exist_ok=True)
            tmpdir = os.path.join(self.out_folder, f".retazo_tmp_{int(time.time() * 1000)}")
            os.makedirs(tmpdir, exist_ok=True)

            # FASE 1: descarga a la carpeta temporal
            host = re.sub(r"^[a-z]+://", "", self.url.lower()).split("/")[0]
            is_youtube = host.endswith(("youtube.com", "youtu.be"))
            win.set_detail(f"Recorte {fmt_time(self.start_sec)} → {fmt_time(self.end_sec)}  |  {dur_total:.1f}s")
            src_tpl = os.path.join(tmpdir, "src.%(ext)s")
            args = ["--newline", "-f", fmt_str, "-o", src_tpl]
            if is_youtube:
                win.set_indeterminate("⏳ Descargando fragmento exacto (YouTube)…")
                args += ["--download-sections", f"*{self.start_sec:.3f}-{self.end_sec:.3f}",
                         "--force-keyframes-at-cuts"]
            else:
                win.set_indeterminate("⏳ Descargando vídeo base (sitio genérico)…")

            def on_ytdlp(line):
                m = _YTDLP_PCT.search(line)
                if m:
                    pct = float(m.group(1)) / 100
                    win.set_progress(pct * 0.45, f"⬇ Descargando: {pct * 100:.0f}%")
                else:
                    win.log(line)

            if self._stream(ytdlp_cmd(*args, url=self.url), on_ytdlp) != 0:
                raise RuntimeError("No se pudo descargar el vídeo.\n" + self._error_tail())

            sources = [p for p in glob.glob(os.path.join(tmpdir, "src.*"))
                       if not p.endswith((".part", ".ytdl", ".temp"))]
            if not sources:
                raise RuntimeError("yt-dlp terminó sin generar archivo.\n" + self._error_tail())
            src = max(sources, key=os.path.getsize)

            actual, has_video, has_audio = self._probe(src)
            if kind == "audio" and not has_audio:
                raise UserFacingError(
                    "Este vídeo no tiene pista de audio.",
                    "Cambia «Contenido» a «Video + Audio» o «Solo Video», o elige MP4/GIF en «Formato».")
            if kind in ("av", "video", "gif") and not has_video:
                raise UserFacingError(
                    "Este enlace solo contiene audio.",
                    "Elige MP3, WAV o «Solo Audio» para exportarlo.")
            if kind == "av" and not has_audio:
                win.log("ℹ El vídeo no tiene audio: se exportará solo la imagen.")

            # Si yt-dlp ignoró --download-sections y bajó todo, recortamos localmente.
            needs_trim = not is_youtube
            if is_youtube and actual is not None and actual > dur_total + 15:
                needs_trim = True

            # FASE 2: ffmpeg → archivo temporal
            self._check_cancel()
            win.set_indeterminate("✂  Aplicando recortes y formato final…")
            tmp_out = os.path.join(tmpdir, f"out.{final_ext}")
            ff_cmd = self.build_ffmpeg_cmd(src, tmp_out, kind, needs_trim, dur_total)
            win.log("ffmpeg " + " ".join(ff_cmd[1:-1]))

            state = {"fps": "0", "speed": "0x"}

            def on_ffmpeg(line):
                if re.match(r"^[a-z_]+=\S*$", line):
                    k, v = line.split("=", 1)
                    if k in ("fps", "speed"):
                        state[k] = v
                    elif k in ("out_time_us", "out_time_ms"):   # ambas valen µs en ffmpeg
                        try:
                            secs = int(v) / 1_000_000
                        except ValueError:
                            return
                        pct = max(0.0, min(secs / dur_total, 1.0))
                        win.set_progress(0.5 + pct * 0.5,
                                         f"✂ Renderizando: {pct * 100:.0f}%  ({secs:.1f}s / {dur_total:.1f}s)"
                                         f"  |  FPS: {state['fps']}  |  {state['speed']}")
                    return
                win.log(line)

            if self._stream(ff_cmd, on_ffmpeg) != 0 or not os.path.exists(tmp_out):
                raise RuntimeError("No se pudo procesar el vídeo.\n" + self._error_tail())

            # FASE 3: mover el resultado con nombre único (nunca sobrescribe)
            title = safe_filename(self.video_info.get("title", "video"), 50)
            rng = f"{fmt_time(self.start_sec)}-{fmt_time(self.end_sec)}".replace(":", ".")
            final = unique_path(self.out_folder, f"{title} [{rng}]", final_ext)
            os.replace(tmp_out, final)

            size_mb = os.path.getsize(final) / 1024 / 1024
            win.done(f"✅ Listo!  {size_mb:.1f} MB  —  {os.path.basename(final)}", final)

        except DownloadCancelled:
            pass
        except UserFacingError as e:
            win.error(f"{e.title}\n{e.hint}".strip())
        except Exception as e:
            if not self.is_cancelled():
                win.error(str(e))
        finally:
            self._proc = None
            if tmpdir:
                self._rmtree_retry(tmpdir)
