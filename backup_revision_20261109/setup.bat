@echo off
echo ================================================
echo   VideoDoctor - Instalacion
echo ================================================
echo.

echo [1/3] Instalando dependencias Python...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: Fallo pip install. Tienes Python instalado?
    pause
    exit /b 1
)

echo.
echo [2/3] Verificando yt-dlp...
yt-dlp --version
if %errorlevel% neq 0 (
    echo Instalando yt-dlp via pip...
    pip install yt-dlp
)

echo.
echo [3/3] Verificando FFmpeg...
ffmpeg -version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo !! ATENCION: FFmpeg NO encontrado !!
    echo.
    echo Opciones para instalar FFmpeg:
    echo   1. winget install ffmpeg
    echo   2. O descarga de: https://ffmpeg.org/download.html
    echo      y agrega al PATH del sistema
    echo.
    echo Puedes intentar instalar con winget automaticamente?
    set /p choice="Escribe 's' para si, cualquier otra para no: "
    if /i "%choice%"=="s" (
        winget install ffmpeg
    ) else (
        echo Instala FFmpeg manualmente y corre setup.bat de nuevo.
    )
) else (
    echo FFmpeg encontrado OK
)

echo.
echo ================================================
echo   Listo! Ejecuta run.bat para iniciar
echo ================================================
pause
