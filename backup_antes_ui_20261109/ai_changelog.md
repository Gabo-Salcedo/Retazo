# AI Context Log - TubeSniper Project

## Project State
- **Core Stack**: Python, Tkinter (UI), yt-dlp (URL/Stream extraction), ffmpeg (Processing), ffpyplayer (Preview Playback).
- **Architecture (Modular)**: 
  - `main.py`: Punto de entrada principal.
  - `core/`: Lógica de negocio (descarga, renderizado, config).
  - `ui/`: Interfaz de usuario (CustomTkinter).
  - `preview/`: Reproductor ffpyplayer y timeline.
  - `download/`: Ventana de progreso y logs.

## Recent Architectural Changes & Fixes

### 1. Download Pipeline Overhaul (Frame Accuracy & Anti-403)
- **Previous State**: ffmpeg was passed a direct HTTP stream URL with `-ss` and `-t` for clipping. This caused severe issues: 403 Forbidden (missing headers), inaccurate cuts (HTTP keyframe seek drift), and pipe deadlocks (stderr buffer fill).
- **New Implementation**: 
  - **Phase 1**: `yt-dlp -J` is used to extract the stream URL and explicitly capture the HTTP headers (`User-Agent`, etc.) to bypass YouTube throttling/403s.
  - **Phase 2**: Replaced ffmpeg HTTP seeking with `yt-dlp --download-sections "*{start}-{end}"`. This leverages YouTube's API (`&begin=`) to download the exact requested chunk to a local `temp.mp4` file, guaranteeing 100% frame accuracy without HTTP demuxer bugs.
  - **Phase 3**: ffmpeg processes the local `temp.mp4` file to apply spatial crops and format conversions, avoiding any network instability.
  - **Deadlock Fix**: Ffmpeg subprocess now uses `stderr=subprocess.STDOUT` to prevent 64KB OS buffer locks.

### 2. UI Thread Optimization (Tkinter Freeze Fix)
- **Issue**: ffmpeg's `-progress pipe:1` spammed the Tkinter Text widget with 50+ lines per second (`frame=X`, `fps=Y`), freezing the main UI thread.
- **Fix**: Implemented strict line filtering. Progress lines (`out_time_ms`) are parsed to update the progress bar natively, and noisy lines are discarded from the Text widget.

### 3. Spatial Cropping Resolution Sync
- **Issue**: Crop coordinates mapped to the Preview Canvas (640x360) were hardcoded against a `1920x1080` assumption, causing misaligned crops if the user downloaded a 720p/480p stream.
- **Fix**: `app.py` now stores relative canvas crop coordinates (`crop_rect_canvas`). During download, the actual dimensions of the downloaded stream are dynamically fetched via `info.get('width')`, and coordinates are scaled proportionally right before passing to `-vf crop=W:H:X:Y`.

### 4. AV Sychronization & Preview Engine (ffpyplayer)
- **Previous State**: Preview used raw `ffmpeg` stdout decoded to PIL images, stored in a custom Python RAM cache (`_loop_cache`). Attempted `ffplay` async audio failed due to severe AV drift.
- **New Implementation**: 
  - Installed `ffpyplayer` (`MediaPlayer`, `SWScale`).
  - Completely rewrote `FfmpegPlayer` class in `player_window.py` to use `ffpyplayer`.
  - Native C-level AV synchronization is now active.
  - Removed Python RAM cache (`_loop_cache`) from `app.py` as `ffpyplayer` handles looping via `seek(start_sec)` natively.
  - Preview stream URL was updated to `best[ext=mp4]/best` to match the exact framerate and timestamp index of the downloaded stream, fixing timeline drifts caused by low-res (`worst`) stream offsets.

### 5. Resolución Dinámica y Modularidad de UI
- **Resolución Máx**: Se arregló el límite de resolución a 720p cambiando `fmt_str` a `bestvideo+bestaudio`, permitiendo a `yt-dlp` combinar flujos de video y audio hasta en 4K.
- **UI de Pestañas**: Se reestructuró la interfaz principal usando `CTkTabview` ("Principal" y "Pre-Render"), moviendo las opciones de Formato/Calidad a su propia pestaña para mayor limpieza.
- **Renderizado Forzado**: Se añadió el checkbox "Forzar recodificación de video" para habilitar las opciones de compresión (CRF) en ffmpeg incluso cuando no hay recorte espacial en la imagen.

### 6. Timeline UX y Controles de Reproducción
- **Mejoras Visuales (`player_window.py`)**: La `TimelineBar` ahora cuenta con interactividad real: efectos de hover, cursores interactivos (`hand2`), marcadores modernos (`◂` y `▸`) y tooltips flotantes que muestran el timestamp en tiempo real al arrastrar.
- **Sincronización de Reproducción**: Se solucionó un bug lógico en `app.py` que sobrescribía manualmente el estado de pausa, rompiendo la función interna `resume()` de `ffpyplayer`. Ahora el botón de pausa/despausa es completamente reactivo.
- **Nuevos Controles (`app.py`)**:
  - Atajos rápidos debajo de la línea de tiempo (`[ Fijar Inicio Aquí ]` y `[ Fijar Fin Aquí ]`) para alinear los marcadores con la posición actual del cabezal.
  - Botón secundario de Play (`⏮▶`) que fuerza la reproducción siempre desde el marcador de inicio.
  - Checkbox de anclaje (`📌 Inicio`) que actualiza visualmente la posición del video al arrastrar el marcador de inicio.

### 7. Rediseño UI y Herramientas Avanzadas
- **Alineación con Mockup**: Se reorganizó el panel derecho (`app.py`) eliminando filas redundantes. Los controles de proporciones ahora son un botón segmentado color morado ("Free", "16:9", etc.), y los botones de reproducción y atajos se agruparon en una sola barra minimalista alineada a la izquierda.
- **Gizmos y Máscara de Recorte**: Se implementó una máscara estática semi-transparente (usando `stipple="gray50"`) para ocultar visualmente el área fuera del recorte (50% de opacidad). Además, se agregaron gizmos en las 4 esquinas del área seleccionada, permitiendo redimensionar con o sin bloqueo de proporción, así como arrastrar la selección entera.
- **Opciones de Exportación Independientes**: Se agregó un desplegable dedicado sobre el botón de Descarga para separar video y audio (`Video + Audio`, `Solo Video`, `Solo Audio`). Esto ajusta condicionalmente los flags `-vn` o `-an` en `ffmpeg`, limpiando el menú de pre-renderizado.
- **Zoom de Línea de Tiempo (`🔍`)**: Se añadió un botón de alternancia de zoom. Al activarse, la línea de tiempo se reescala para que el rango seleccionado ocupe el 100% de la barra (`view_start`, `view_end`), permitiendo micro-ajustes frame a frame muy precisos sin perder usabilidad.
- **Limpieza**: Se eliminó la consola de texto inactiva debajo del botón de descarga.
- **Capturas de Alta Resolución (`📸`)**: Se implementó un nuevo botón de captura al lado del botón de descarga. Utiliza FFmpeg para extraer un fotograma exacto en el tiempo actual del cabezal, aplicando el recorte espacial seleccionado y guardándolo como un archivo `.png` en la carpeta de descargas.

- **Identidad**: Renombrado oficial de VideoDoctor a **TubeSniper 🎯**, actualizando títulos, iconos y logs.
- **Recorte Robusto (iw/ih)**: Se migró de cálculos de píxeles absolutos a expresiones proporcionales nativas de FFmpeg (`trunc(iw*pct/2)*2`). Esto elimina el error `Invalid too big or non positive size` al garantizar que el recorte siempre esté dentro de los límites del video descargado, independientemente de su resolución.
- **Pipeline de Descarga Final**: Se consolidó el uso de `yt-dlp --download-sections` para obtener fragmentos exactos directamente desde los servidores de YouTube, seguido de un post-procesamiento local con FFmpeg para recorte y formato.

### 8. Arquitectura Modular y Soporte Multi-Formato
- **Refactorización Core/UI**: Se separó la lógica de negocio (`core/downloader.py`, `core/config.py`, `core/utils.py`) de la interfaz de usuario (`ui/main_window.py`). Esto facilita el mantenimiento, las pruebas y permite reemplazar el frontend en el futuro sin reescribir el motor de renderizado.
- **Estrategias Multi-Sitio**: El motor ahora detecta si la URL es de YouTube o genérica (Pinterest, Instagram, TikTok, etc.). Para sitios genéricos o aquellos con streams inconsistentes, descarga el video completo localmente antes de recortar, eliminando de forma definitiva los errores de bitstream (como `Invalid NAL unit size`).
- **Nuevos Formatos y Codecs**: Se expandió el menú de exportación para soportar múltiples contenedores (MP4, GIF, MP3, WAV) y codecs de video (H.264, H.265/HEVC, VP9). El renderizador genera paletas automáticas en FFmpeg para optimizar los GIFs y mapea correctamente los codecs de audio (`pcm_s16le` para WAV, `libmp3lame` para MP3).

### 9. Revisión de robustez (9 nov 2026)
Patrones tomados del exportador de Cap (CapSoftware/Cap, `crates/export`): salida temporal + rename atómico, cancelación por bandera propia del trabajo, GIF con fps/resolución acotados.
- **Caché segura**: `CACHE_DIR` y `config.json` se anclan a la carpeta del proyecto (`core/utils.PROJECT_ROOT`), no al cwd. `_clear_cache()` solo borra `proxy_*`. Antes, lanzar la app desde `C:\Users\<usuario>` vaciaba `~/.cache`.
- **Hilos y Tk**: los hilos de trabajo ya no tocan widgets; encolan en `TubeSniperUI._ui()` y `_pump()` lo ejecuta en el hilo principal. Los fotogramas se pasan como PIL y la `PhotoImage` se crea en el hilo principal (antes: cuelgues aleatorios). Solo se dibuja el último fotograma pendiente.
- **Descargas** (`core/downloader.py`): todo se escribe en `.tubesniper_tmp_*` dentro de la carpeta de salida y se borra al terminar, cancelar o fallar. Nombres únicos `Título [MM.SS-MM.SS] (n).ext` (antes se sobrescribía). Los errores muestran la salida real de yt-dlp/ffmpeg (antes salían vacíos). La cancelación mata el árbol de procesos (`taskkill /T`). Se eliminó la fase `-J` previa (era lenta y no cancelable).
- **Formatos**: "Solo Audio" con MP4 genera `.m4a` AAC (antes: MP3 dentro de `.mp4`). GIF a 15 fps, ancho máx. 720 (`gif_fps`/`gif_width` en config.json). El recorte local en sitios genéricos recodifica para cortar al fotograma exacto. HEVC con `-tag:v hvc1`.
- **Recorte**: el canvas sigue el aspecto real del vídeo (antes forzaba 16:9 y el recorte de vídeos verticales salía mal). El rectángulo se limita al vídeo.
- **Validación**: inicio/fin se validan (`utils.parse_time`); escribir en los campos mueve los marcadores. La descarga usa la URL cargada, no el texto actual del campo.
- **Seguridad**: la URL va siempre tras `--` en yt-dlp (`utils.ytdlp_cmd`).
- **Instalación**: `requirements.txt` incluye `ffpyplayer` (sobraba `python-vlc`). `setup.bat`/`run.bat` usan el mismo Python, sin ruta fija de usuario; corregido `set /p` sin delayed expansion.
- **Reproductor**: `stop()` espera al hilo antes de cerrar el player; nunca dos hilos de decodificación; error claro si falta ffpyplayer.

## Next Steps / Known Quirks
- `app.py` (monolito antiguo) y `backup_reparacion_*` ya no se usan; se pueden borrar.
- El proxy de vista previa descarga el vídeo completo en calidad mínima; en vídeos muy largos tarda.
- **Calidad/CRF**: Por defecto, la app usa "Zero-Loss" (copy) si no hay recorte para máxima velocidad. Las opciones de CRF ahora se activan automáticamente si el usuario elige una calidad distinta a la "Alta (CRF 18)".
- Looping via `ffpyplayer.seek()` over an HTTP stream induces a tiny buffer pause (~100-300ms) at the end of every loop. Acceptable tradeoff for perfect AV sync without downloading the full video into memory.
- If dependencies are missing on a new machine, ensure `pip install ffpyplayer pillow` is run.
