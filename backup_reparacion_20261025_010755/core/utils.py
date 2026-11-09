import os
import sys
import subprocess

def find_tool(name):
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
    return name

YTDLP = find_tool("yt-dlp")
FFMPEG = find_tool("ffmpeg")

def time_to_sec(t: str) -> float:
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

def fmt_time(seconds: float) -> str:
    """Convierte segundos a MM:SS o HH:MM:SS."""
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
