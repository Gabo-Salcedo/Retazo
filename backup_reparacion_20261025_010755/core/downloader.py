import os
import subprocess
import json
import time
from .utils import YTDLP, FFMPEG

class DownloadJob:
    def __init__(self, url, start_sec, end_sec, out_folder, config, video_info, crop_coords, cw, ch, win_callbacks):
        self.url = url
        self.start_sec = start_sec
        self.end_sec = end_sec
        self.out_folder = out_folder
        self.config = config
        self.video_info = video_info
        self.crop_coords = crop_coords  # (cx, cy, cw, ch) or None
        self.cw = cw
        self.ch = ch
        self.win = win_callbacks  # A simple interface dict or class with .log, .set_progress, .cancelled, etc.
        self._proc = None

    def cancel(self):
        if self._proc:
            try: self._proc.kill()
            except Exception: pass

    def get_crf(self):
        mapping = {"Alta (CRF 18)": "18", "Media (CRF 23)": "23", "Pequeño (CRF 28)": "28"}
        return mapping.get(self.config.get("quality", "Media (CRF 23)"), "23")

    def run(self):
        win = self.win
        try:
            safe_title = "".join(c for c in (self.video_info.get("title", "video"))
                                 if c.isalnum() or c in " _-")[:40].strip()
            
            export_mode = self.config.get("export_mode", "Video + Audio")
            target_format = self.config.get("format", "MP4")
            
            is_audio_only = (export_mode == "Solo Audio") or (target_format in ["MP3", "WAV"])
            is_video_only = (export_mode == "Solo Video") or (target_format == "GIF")
            
            max_res = self.config.get("resolution", "1080p").split("p")[0]
            
            # Establecer formato final
            temp_ext = "mp4"
            if target_format == "MP3":
                fmt_str = "bestaudio/best"
                final_ext = "mp3"
                temp_ext = "m4a"
            elif target_format == "WAV":
                fmt_str = "bestaudio/best"
                final_ext = "wav"
                temp_ext = "m4a"
            elif target_format == "GIF":
                fmt_str = f"bestvideo[height<={max_res}]/best"
                final_ext = "gif"
            elif is_video_only:
                fmt_str = f"bestvideo[height<={max_res}][ext=mp4]/best[height<={max_res}][ext=mp4]/best"
                final_ext = "mp4"
            else: # MP4 con Video + Audio
                fmt_str = f"bestvideo[height<={max_res}][ext=mp4]+bestaudio[ext=m4a]/best[height<={max_res}][ext=mp4]/best[ext=mp4]/best"
                final_ext = "mp4"
                
            final = os.path.join(self.out_folder, f"{safe_title}.{final_ext}")

            dur_total = max(self.end_sec - self.start_sec, 1.0)

            win.set_indeterminate("⏳ Obteniendo URL del stream…")
            win.set_detail(f"Recorte  |  {dur_total:.0f}s")
            
            # FASE 1: Extraer JSON para cookies y headers
            r = subprocess.run([YTDLP, "-J", "--no-playlist", "-f", fmt_str, self.url],
                               capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                raise RuntimeError("yt-dlp no devolvió info.\n" + r.stderr[-300:])
                
            info = json.loads(r.stdout)
            stream_url = info.get("url")
            if not stream_url:
                formats = info.get("requested_formats", [])
                if formats: stream_url = formats[0].get("url")
                    
            if not stream_url:
                raise RuntimeError("No se encontró la URL directa del stream.")

            win.log(f"Stream OK: {stream_url[:80]}…\n")
            win.set_progress(0.05, "5 %")

            # FASE 2: Estrategia de descarga
            is_youtube = "youtube.com" in self.url or "youtu.be" in self.url
            temp_file = os.path.join(self.out_folder, f"temp_{int(time.time())}.{temp_ext}")

            if is_youtube:
                win.set_indeterminate("⏳ Descargando fragmento exacto (Estrategia YouTube)…")
                ytdlp_cmd = [
                    YTDLP, "-f", fmt_str,
                    "--download-sections", f"*{self.start_sec}-{self.end_sec}",
                    "--force-keyframes-at-cuts",
                    "-o", temp_file, self.url
                ]
            else:
                win.set_indeterminate("⏳ Descargando base completa (Estrategia Genérica)…")
                ytdlp_cmd = [YTDLP, "-f", fmt_str, "-o", temp_file, self.url]

            self._proc = subprocess.Popen(ytdlp_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            
            for raw in self._proc.stdout:
                if win.is_cancelled():
                    self._proc.kill()
                    break
                win.log(raw.strip())
            
            self._proc.wait()
            if win.is_cancelled():
                if os.path.exists(temp_file): os.remove(temp_file)
                return
            if self._proc.returncode != 0:
                if os.path.exists(temp_file): os.remove(temp_file)
                raise RuntimeError("Falló la descarga del fragmento base con yt-dlp.")

            # FASE 3: FFmpeg (Recorte Espacial y Formato Final)
            win.set_indeterminate("✂  Aplicando recortes y formato final…")

            vf_parts = []
            if self.crop_coords:
                cx, cy, cw, ch = self.crop_coords
                pw, ph = cw / self.cw, ch / self.ch
                px, py = cx / self.cw, cy / self.ch
                vf_parts.append(f"crop=trunc(iw*{pw}/2)*2:trunc(ih*{ph}/2)*2:trunc(iw*{px}/2)*2:trunc(ih*{py}/2)*2")

            ff_cmd = [FFMPEG, "-y"]
            
            # Verificación de seguridad: si yt-dlp falló al recortar y bajó el video completo
            needs_trim = not is_youtube
            if is_youtube and os.path.exists(temp_file):
                try:
                    probe = subprocess.run([FFMPEG, "-i", temp_file], capture_output=True, text=True)
                    import re
                    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", probe.stderr)
                    if m:
                        actual_dur = float(m.group(1))*3600 + float(m.group(2))*60 + float(m.group(3))
                        if actual_dur > dur_total + 15:
                            needs_trim = True
                except Exception: pass

            if needs_trim:
                ff_cmd += ["-ss", str(self.start_sec)]
                
            # Habilitar robustez ante NAL units inválidas
            ff_cmd += ["-fflags", "+genpts+discardcorrupt", "-avoid_negative_ts", "make_zero", "-i", temp_file]
            
            if needs_trim:
                ff_cmd += ["-t", str(dur_total)]

            target_codec = self.config.get("codec", "H.264 (AVC)")
            force_encode = self.config.get("force_encode", False)
            quality_selected = self.config.get("quality", "Media (CRF 23)")
            needs_reencode = vf_parts or force_encode or target_format != "MP4" or quality_selected != "Alta (CRF 18)" or target_codec != "H.264 (AVC)"

            if is_audio_only:
                ff_cmd += ["-vn"]
                if target_format == "WAV":
                    ff_cmd += ["-c:a", "pcm_s16le"]
                else:
                    ff_cmd += ["-c:a", "libmp3lame", "-q:a", "2"]
            elif target_format == "GIF":
                # Optimización básica de GIF
                if vf_parts:
                    ff_cmd += ["-vf", ",".join(vf_parts) + ",split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"]
                else:
                    ff_cmd += ["-vf", "split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"]
            else:
                if needs_reencode:
                    if vf_parts: ff_cmd += ["-vf", ",".join(vf_parts)]
                    
                    if target_codec == "H.265 (HEVC)":
                        ff_cmd += ["-c:v", "libx265", "-crf", self.get_crf(), "-preset", "fast"]
                    elif target_codec == "VP9":
                        ff_cmd += ["-c:v", "libvpx-vp9", "-crf", self.get_crf(), "-b:v", "0"]
                    else: # H.264
                        ff_cmd += ["-c:v", "libx264", "-crf", self.get_crf(), "-preset", "veryfast"]
                        
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
                    
            ff_cmd += ["-progress", "pipe:1", "-nostats", final]

            self._proc = subprocess.Popen(ff_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")

            _last_fps = "0"
            _last_speed = "0x"
            
            for raw in self._proc.stdout:
                if win.is_cancelled():
                    self._proc.kill()
                    break
                kv = raw.strip()
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    if k == "fps": _last_fps = v
                    elif k == "speed": _last_speed = v
                    elif k == "out_time_ms":
                        try:
                            secs = int(v) / 1_000_000
                            pct  = min(secs / dur_total, 1.0)
                            bar  = 0.05 + pct * 0.90
                            detail = f"✂ Renderizando: {pct*100:.0f}%  ({secs:.1f}s / {dur_total:.1f}s)  |  FPS: {_last_fps}  |  Velocidad: {_last_speed}"
                            win.set_progress(bar, detail)
                        except Exception:
                            pass
                
                if kv.startswith("out_time=") or kv.startswith("frame=") or kv.startswith("speed="):
                    continue
                win.log(kv)

            self._proc.wait()
            
            try:
                if os.path.exists(temp_file): os.remove(temp_file)
            except Exception: pass
            
            if win.is_cancelled(): return
            if self._proc.returncode != 0:
                err_out = self._proc.stderr.read()[-600:] if self._proc.stderr else ""
                raise RuntimeError(err_out)

            size_mb = os.path.getsize(final) / 1024 / 1024
            msg = f"✅ Listo!  {size_mb:.1f} MB  —  {os.path.basename(final)}"
            win.done(msg, final)

        except Exception as e:
            try:
                if 'temp_file' in locals() and os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception: pass
            win.error(str(e))
