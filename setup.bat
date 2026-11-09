@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
echo ================================================
echo   Retazo - Instalacion
echo ================================================
echo.

:: Mismo Python que usara run.bat (antes: pip del PATH, podia ser otro Python)
call :find_python
if not defined PYTHON (
    echo ERROR: No se encontro Python 3. Instalalo desde https://www.python.org/downloads/
    echo        marcando "Add python.exe to PATH".
    pause
    exit /b 1
)
echo Usando: !PYTHON!
echo.

echo [1/3] Instalando dependencias Python...
"!PYTHON!" -m pip install --upgrade -r requirements.txt
if errorlevel 1 (
    echo ERROR: Fallo pip install.
    pause
    exit /b 1
)

echo.
echo [2/3] Verificando yt-dlp...
"!PYTHON!" -m yt_dlp --version
if errorlevel 1 (
    echo ERROR: yt-dlp no quedo instalado correctamente.
    pause
    exit /b 1
)

echo.
echo [3/3] Verificando FFmpeg...
set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo.
    echo *** ATENCION: FFmpeg NO encontrado ***
    echo.
    echo Opciones:
    echo   1. winget install Gyan.FFmpeg
    echo   2. Descargar de https://ffmpeg.org/download.html y agregarlo al PATH
    echo.
    set "choice="
    set /p "choice=Instalar ahora con winget? (s/n): "
    if /i "!choice!"=="s" (
        winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    ) else (
        echo Instala FFmpeg manualmente y vuelve a ejecutar setup.bat.
    )
) else (
    echo FFmpeg encontrado OK
)

echo.
echo ================================================
echo   Listo. Ejecuta run.bat para iniciar
echo ================================================
pause
exit /b 0

:find_python
set "PYTHON="
:: 1) Python 3.12 (version con la que se probo ffpyplayer), 2) py launcher, 3) PATH
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined PYTHON exit /b 0
for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PYTHON=%%P"
if defined PYTHON exit /b 0
for /f "delims=" %%P in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PYTHON=%%P"
if defined PYTHON exit /b 0
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PYTHON=%%D\python.exe"
exit /b 0
