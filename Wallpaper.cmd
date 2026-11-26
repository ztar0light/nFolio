@echo off
setlocal EnableExtensions DisableDelayedExpansion
if "%~1"=="" goto usage
if not "%~2"=="" goto usage
if not exist "%~dp0.venv\Scripts\python.exe" goto findpython
set "folio_python=%~dp0.venv\Scripts\python.exe"
goto convert
:findpython
where py >nul 2>nul
if errorlevel 1 goto usepython
set "folio_python=py"
goto convert
:usepython
where python >nul 2>nul
if errorlevel 1 goto missing
set "folio_python=python"
:convert
"%folio_python%" "%~dp0wallpaper.py" "%~f1"
if errorlevel 1 goto failed
echo Copy background.tns into My Documents/folio on the calculator.
pause
exit /b 0
:usage
echo Drag one picture onto Wallpaper.cmd.
goto failed
:missing
echo Install Python and the dependencies in requirements.txt.
:failed
pause
exit /b 1
