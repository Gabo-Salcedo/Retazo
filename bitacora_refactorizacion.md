# 📝 Bitácora de Refactorización: Retazo ✂ (antes TubeSniper)

## 📅 Fecha: 28 de Abril, 2026

### 🚀 Resumen Ejecutivo
Se ha completado la transición de una arquitectura monolítica (`app.py`) a una **Arquitectura Modular Limpia**. Este cambio desacopla totalmente la interfaz de usuario (UI) de la lógica de procesamiento (Core), permitiendo que TubeSniper sea más robusto, fácil de mantener y compatible con múltiples sitios y formatos.

---

### 🏗️ Nueva Estructura del Proyecto
El código ahora se organiza de la siguiente manera para maximizar la reutilización:

*   **`main.py`**: Punto de entrada principal. Inicializa la aplicación.
*   **`core/` (El Motor)**:
    *   `downloader.py`: Orquestador de descargas y renderizado. Maneja la lógica de `yt-dlp` y `ffmpeg`.
    *   `config.py`: Sistema persistente de ajustes (Carpeta de salida, calidad, etc.).
    *   `utils.py`: Utilidades globales y resolución de rutas de ejecutables.
*   **`ui/` (La Fachada)**:
    *   `main_window.py`: Definición completa de la interfaz moderna con CustomTkinter.
*   **`preview/`**: Componentes del reproductor y línea de tiempo interactiva.
*   **`download/`**: Ventana modal de progreso y logs en tiempo real.

---

### 🛠️ Hitos Técnicos Alcanzados

#### 1. Estrategia Dual Multi-Sitio
El motor ahora es inteligente:
*   **YouTube**: Utiliza `--download-sections` para una precisión de frame del 100% descargando solo el fragmento necesario.
*   **Otros (Pinterest, IG, TikTok, etc.)**: Descarga el flujo completo y realiza el recorte localmente. Esto soluciona los errores de `Invalid NAL unit size` causados por streams inestables.

#### 2. Soporte Extendido de Formatos y Codecs
Se ha implementado un sistema de perfiles de renderizado:
*   **Contenedores**: MP4, GIF, MP3, WAV.
*   **Video Codecs**: H.264 (Universal), H.265/HEVC (Alta compresión), VP9 (Web).
*   **Optimización GIF**: Integración de filtros de paleta dinámica en FFmpeg para GIFs de alta fidelidad.

#### 3. Restauración de Herramientas Avanzadas
Se han reintegrado todas las funciones de la interfaz que fueron optimizadas durante la migración:
*   **📸 Captura de Pantalla**: Extracción de fotogramas en alta resolución con un clic.
*   **🔍 Zoom de Timeline**: Micro-ajustes de precisión.
*   **🔁 Loop y 📌 Anclaje**: Herramientas de edición fluida.
*   **🔲 Gizmos de Recorte**: Máscara visual y tiradores de esquinas para selección espacial.

---

### 📉 Estado de Dependencias
Para asegurar el funcionamiento en cualquier entorno, se requiere:
*   `yt-dlp` (Actualizado)
*   `ffmpeg` (En el PATH o vía WinGet)
*   `customtkinter`, `pillow`, `ffpyplayer`, `requests`.

---
** Retazo (ex TubeSniper) v2.0 - Arquitectura Modular Lista para Producción **
