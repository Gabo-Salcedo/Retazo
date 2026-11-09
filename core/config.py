import json
import os

from .utils import PROJECT_ROOT


class ConfigManager:
    def __init__(self, config_file=None):
        # Siempre junto a main.py, aunque la app se lance desde otra carpeta.
        self.config_file = config_file or os.path.join(PROJECT_ROOT, "config.json")
        self.data = {
            "folder": os.path.expanduser("~/Downloads"),
            "quality": "Alta (CRF 18)",
            "resolution": "1080p (HD)",
            "ratio": "Free",
            "export_mode": "Video + Audio",
            "format": "MP4",
            "codec": "H.264 (AVC)",
            "force_encode": False,
        }
        self.load()

    def load(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    c = json.load(f)
                    if isinstance(c, dict):
                        self.data.update(c)
            except Exception:
                pass

    def save(self):
        # Escritura atómica: un cierre a mitad no deja un config.json corrupto.
        tmp = self.config_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.config_file)
        except Exception:
            pass

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()

    def update(self, **values):
        """Cambia varias claves con una sola escritura a disco."""
        self.data.update(values)
        self.save()
