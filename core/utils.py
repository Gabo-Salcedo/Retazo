import os
import re
import sys
import subprocess

# Raíz del proyecto (carpeta que contiene main.py). Las rutas internas
# (caché, config) se anclan aquí, nunca al directorio de trabajo actual.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(PROJECT_ROOT, "assets")

# En Windows, evita que cada subproceso abra una ventana de consola.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_tool(name):
    """Busca ejecutable: primero en Scripts de Python, luego WinGet, luego PATH."""
    candidates = [
        os.path.join(os.path.dirname(sys.executable), "Scripts", name + ".exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links", name + ".exe"),
        name,  # espera que esté en PATH
    ]
    for c in candidates:
        try:
            subprocess.run([c, "-version"], capture_output=True, timeout=5, creationflags=NO_WINDOW)
            return c
        except Exception:
            continue
    return name


YTDLP = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "ytdlp_runner.py")]
FFMPEG = find_tool("ffmpeg")


def ytdlp_cmd(*args, url):
    """Llamada a yt-dlp con la URL siempre tras '--', para que un texto pegado
    que empiece por '-' nunca se interprete como una opción."""
    return [*YTDLP, *args, "--", url]


_TIME_RE = re.compile(r"^\s*(?:(\d+):)?(?:(\d+):)?(\d+(?:[.,]\d+)?)\s*$")


def parse_time(t):
    """'SS', 'MM:SS' o 'HH:MM:SS' (admite decimales) -> segundos.
    Devuelve None si el texto no es un tiempo válido."""
    if t is None:
        return None
    m = _TIME_RE.match(str(t))
    if not m:
        return None
    a, b, s = m.groups()
    s = float(s.replace(",", "."))
    if a is not None and b is not None:      # HH:MM:SS
        h, mnt = int(a), int(b)
        if mnt >= 60:
            return None
    elif a is not None:                      # MM:SS
        h, mnt = 0, int(a)
    else:                                    # SS
        h, mnt = 0, 0
    if a is not None and s >= 60:
        return None
    return h * 3600 + mnt * 60 + s


def time_to_sec(t) -> float:
    """Versión tolerante (compatibilidad): tiempo inválido -> 0.0."""
    v = parse_time(t)
    return 0.0 if v is None else v


def fmt_time(seconds: float) -> str:
    """Convierte segundos a MM:SS o HH:MM:SS."""
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def safe_filename(text, max_len: int = 60) -> str:
    """Nombre de archivo válido en Windows; nunca vacío."""
    cleaned = "".join(c for c in (text or "") if c.isalnum() or c in " _-")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()[:max_len].strip()
    return cleaned or "video"


def unique_path(folder: str, base: str, ext: str) -> str:
    """folder/base.ext, o 'base (2).ext', 'base (3).ext'… si ya existe."""
    path = os.path.join(folder, f"{base}.{ext}")
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{base} ({n}).{ext}")
        n += 1
    return path
