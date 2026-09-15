@echo off
setlocal

cd /d "%~dp0"
call "%~dp0run.cmd" --web
set "MODULAI_EXIT_CODE=%ERRORLEVEL%"

if not "%MODULAI_EXIT_CODE%"=="0" (
    echo.
    echo ModulAI se cerro con el codigo %MODULAI_EXIT_CODE%.
    pause
)

exit /b %MODULAI_EXIT_CODE%
