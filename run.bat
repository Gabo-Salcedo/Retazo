@echo off
setlocal
cd /d "%~dp0"

:: Mismo criterio que setup.bat (antes: ruta fija C:\Users\arace\...)
set "PYTHON="
:: 1) Python 3.12 (version con la que se probo ffpyplayer), 2) py launcher, 3) PATH
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYTHON for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PYTHON=%%P"
if not defined PYTHON for /f "delims=" %%P in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PYTHON=%%P"
if not defined PYTHON for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PYTHON=%%D\python.exe"
if not defined PYTHON (
    echo No se encontro Python 3. Ejecuta setup.bat primero.
    pause
    exit /b 1
)

:: ffmpeg instalado con winget
set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"

echo Iniciando Retazo...
"%PYTHON%" main.py
if errorlevel 1 pause
