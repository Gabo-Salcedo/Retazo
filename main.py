import sys
import os

# Ensure the project root is in the Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ui.main_window import RetazoUI

def main():
    if os.name == "nt":
        # Identidad propia en la barra de tareas (si no, Windows agrupa la
        # ventana bajo el icono de python.exe).
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Retazo.App")
        except Exception:
            pass
    app = RetazoUI()
    app.mainloop()

if __name__ == "__main__":
    main()
