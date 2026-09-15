@echo off
setlocal

set "ROOT=%~dp0"
set "PYTHON=%ROOT%.build-venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo No existe el entorno de compilacion .build-venv.
    echo Crealo con un Python oficial de Windows y ejecuta pip install -e . pyinstaller.
    exit /b 1
)

cd /d "%ROOT%"
"%PYTHON%" -m PyInstaller --noconfirm --clean --onedir --contents-directory . --name ModulAI --paths src ^
  --add-data "config\app.example.toml;config" --add-data "modules;modules" ^
  --add-data "src\modulai\ui;modulai\ui" src\modulai\desktop.py
