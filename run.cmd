@echo off
setlocal

set "MODULAI_ROOT=%~dp0"
set "PYTHONPATH=%MODULAI_ROOT%src"

if defined MODULAI_PYTHON if exist "%MODULAI_PYTHON%" goto run_custom
if exist "%MODULAI_ROOT%.venv-win\Scripts\python.exe" goto run_windows_venv_alt
if exist "%MODULAI_ROOT%.venv\Scripts\python.exe" goto run_windows_venv
if exist "%MODULAI_ROOT%.venv\bin\python.exe" goto run_msys_venv
if exist "C:\msys64\ucrt64\bin\python.exe" goto run_msys

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 goto incompatible_python

python -m modulai %*
exit /b %ERRORLEVEL%

:run_custom
"%MODULAI_PYTHON%" -m modulai %*
exit /b %ERRORLEVEL%

:run_windows_venv_alt
"%MODULAI_ROOT%.venv-win\Scripts\python.exe" -m modulai %*
exit /b %ERRORLEVEL%

:run_windows_venv
"%MODULAI_ROOT%.venv\Scripts\python.exe" -m modulai %*
exit /b %ERRORLEVEL%

:run_msys_venv
"%MODULAI_ROOT%.venv\bin\python.exe" -m modulai %*
exit /b %ERRORLEVEL%

:run_msys
"C:\msys64\ucrt64\bin\python.exe" -m modulai %*
exit /b %ERRORLEVEL%

:incompatible_python
echo ModulAI necesita Python 3.10 o superior.
echo El comando "python" actual apunta a una version anterior o no esta disponible.
echo Instala Python 3.10+ o define MODULAI_PYTHON con la ruta a un ejecutable compatible.
exit /b 1
