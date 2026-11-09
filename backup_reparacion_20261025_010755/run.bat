@echo off
set PYTHON=C:\Users\arace\AppData\Local\Programs\Python\Python312\python.exe
set SCRIPTS=C:\Users\arace\AppData\Local\Programs\Python\Python312\Scripts

:: Agregar Python y Scripts al PATH temporalmente
set PATH=%SCRIPTS%;%PATH%

:: Buscar ffmpeg (winget lo instala en AppData\Local\Microsoft\WinGet\Links)
set PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%

echo Iniciando TubeSniper...
"%PYTHON%" main.py
pause
