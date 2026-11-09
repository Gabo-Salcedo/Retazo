import json
import os

class ConfigManager:
    def __init__(self, config_file="config.json"):
        self.config_file = config_file
        self.data = {
            "folder": os.path.expanduser("~/Downloads"),
            "quality": "Alta (CRF 18)",
            "resolution": "1080p (HD)",
            "ratio": "Free",
            "export_mode": "Video + Audio"
        }
        self.load()

    def load(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    c = json.load(f)
                    self.data.update(c)
            except Exception:
                pass

    def save(self):
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f)
        except Exception:
            pass

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()
