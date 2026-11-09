<div align="center">

# ✂ Retazo

**Recorta y descarga solo el trozo de vídeo que necesitas.**

Pega una URL, marca el inicio y el fin, encuadra la imagen si quieres y exporta a MP4, GIF, MP3 o WAV.

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Windows](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D6?logo=windows&logoColor=white)
![yt-dlp](https://img.shields.io/badge/motor-yt--dlp-red)
![FFmpeg](https://img.shields.io/badge/procesado-FFmpeg-007808?logo=ffmpeg&logoColor=white)

<img src="docs/screenshot.png" alt="Interfaz de Retazo" width="900">

</div>

---

## ¿Qué hace?

Retazo es una app de escritorio para sacar **fragmentos** de vídeos online sin descargar el vídeo entero ni abrir un editor:

- **Recorte de tiempo** con línea de tiempo, marcadores de inicio/fin, zoom y vista previa con audio.
- **Recorte de imagen** arrastrando sobre el vídeo, libre o en proporción fija (16:9, 9:16, 1:1).
- **Varios formatos**: MP4 (H.264, H.265/HEVC o VP9), GIF optimizado, MP3, WAV o solo audio M4A.
- **Descarga exacta en YouTube**: baja solo el tramo pedido (`--download-sections`), no el vídeo completo.
- **Otros sitios** (TikTok, Instagram, Pinterest, X…): todo lo que soporta [yt-dlp](https://github.com/yt-dlp/yt-dlp). El recorte se hace en local con precisión de fotograma.
- **Captura de fotograma** en PNG a la máxima resolución disponible, con el recorte aplicado.
- **Seguro con tus archivos**: nunca sobrescribe (`Título [00.32-01.11] (2).mp4`), y si cancelas o algo falla no deja archivos a medias.

## Requisitos

| | |
|---|---|
| Sistema | Windows 10 u 11 |
| Python | 3.12 recomendado ([python.org](https://www.python.org/downloads/), marca *Add python.exe to PATH*) |
| FFmpeg | En el PATH o instalado con `winget install Gyan.FFmpeg` |
| Runtime JS | Para YouTube, yt-dlp necesita [Deno](https://deno.com) o [Node.js](https://nodejs.org) (`winget install DenoLand.Deno`) |

## Instalación

```bat
git clone https://github.com/<tu-usuario>/retazo.git
cd retazo
setup.bat
```

`setup.bat` instala las dependencias de Python (`requirements.txt`), comprueba yt-dlp y te ofrece instalar FFmpeg con winget si no lo encuentra.

<details>
<summary>Instalación manual</summary>

```bat
python -m pip install -r requirements.txt
python main.py
```
</details>

## Uso

1. Ejecuta **`run.bat`**.
2. Pega la URL arriba y pulsa **Cargar** (o Enter).
3. Marca el tramo: arrastra los marcadores verde/rojo, usa **[ Inicio / Fin ]** sobre el cabezal o escribe los tiempos (`MM:SS` o `HH:MM:SS`).
4. *(Opcional)* Arrastra sobre el vídeo para recortar la imagen.
5. Elige formato y calidad en el panel **Exportar** y pulsa **Descargar fragmento**.

### Atajos de teclado

| Tecla | Acción |
|---|---|
| `Espacio` | Reproducir / pausa |
| `I` / `O` | Fijar inicio / fin en el cabezal |
| `←` / `→` | Retroceder / avanzar 5 s |
| `Shift` + `←` / `→` | Retroceder / avanzar un fotograma |
| `Home` | Reproducir desde el inicio del tramo |
| `L` | Activar / desactivar bucle |
| `Z` | Zoom de la línea de tiempo al tramo |
| `Ctrl` + `S` | Descargar |

Los atajos no se activan mientras escribes en un campo de texto.

### Formatos de salida

| Formato | Resultado |
|---|---|
| MP4 · Vídeo + Audio | H.264 / H.265 / VP9. En YouTube, sin recorte de imagen y con calidad Alta + H.264, copia sin recodificar (rápido, sin pérdida). |
| MP4 · Solo Vídeo | Igual, sin pista de audio. |
| MP4 · Solo Audio | `.m4a` AAC 192 kbps. |
| GIF | 15 fps, máx. 720 px de ancho, paleta optimizada. Ajustable con `gif_fps` y `gif_width` en `config.json`. |
| MP3 | VBR de alta calidad (`-q:a 2`). |
| WAV | PCM 16 bits sin compresión. |

## Cómo funciona

```
URL ──► yt-dlp ──► carpeta temporal ──► FFmpeg ──► rename atómico ──► archivo final
         │  YouTube: solo el tramo            │ recorte de imagen, códec,
         │  otros:   vídeo completo           │ calidad, recorte de tiempo local
```

- La vista previa usa **ffpyplayer** con un proxy local de baja resolución para moverse sin lag.
- Todo se escribe en `.retazo_tmp_*` y solo al final se mueve al nombre definitivo (idea tomada del exportador de [Cap](https://github.com/CapSoftware/Cap)).
- Los procesos pesados corren en hilos; la interfaz solo se actualiza desde el hilo principal.

## Estructura del proyecto

```
├── main.py                  Punto de entrada
├── run.bat / setup.bat      Arranque e instalación en Windows
├── core/
│   ├── downloader.py        Descarga (yt-dlp) + procesado (FFmpeg)
│   ├── config.py            Ajustes persistentes (config.json)
│   ├── utils.py             Rutas, tiempos, nombres de archivo
│   └── ytdlp_runner.py      Lanza yt-dlp con certificados de Windows
├── ui/
│   ├── main_window.py       Ventana principal
│   └── theme.py             Paleta, componentes y tooltips
├── preview/player_window.py Línea de tiempo y reproductor
└── download/download_window.py  Ventana de progreso
```

## Solución de problemas

| Problema | Solución |
|---|---|
| *Falta 'ffpyplayer' para la vista previa* | Ejecuta `setup.bat` o `python -m pip install ffpyplayer`. |
| *ffmpeg no encontrado* | `winget install Gyan.FFmpeg` y vuelve a abrir la app. |
| YouTube falla al cargar | Actualiza yt-dlp (`python -m pip install -U yt-dlp`) e instala Deno o Node.js. |
| El error dice *Video unavailable* / *Sign in* | El vídeo es privado, tiene restricción de edad o de región. |

## Uso responsable

Retazo es una herramienta para trabajar con contenido que te pertenece, que es de dominio público o que tienes permiso para usar (citas, análisis, material propio…). Respeta los derechos de autor y los términos de servicio de cada plataforma.

## Créditos

- [yt-dlp](https://github.com/yt-dlp/yt-dlp): extracción y descarga.
- [FFmpeg](https://ffmpeg.org): procesado de vídeo y audio.
- [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter): interfaz.
- [ffpyplayer](https://github.com/matham/ffpyplayer): vista previa con audio sincronizado.
- Diseño inspirado en [Cap](https://github.com/CapSoftware/Cap).

## Licencia

Pendiente de definir.
