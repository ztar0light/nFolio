: <<'NFOLIO_WINDOWS'
@echo off
setlocal EnableExtensions DisableDelayedExpansion
if "%~1"=="" goto usage
if not exist "%~dp0.venv\Scripts\python.exe" goto findpython
set "reader_python=%~dp0.venv\Scripts\python.exe"
goto pack
:findpython
where py >nul 2>nul
if errorlevel 1 goto usepython
set "reader_python=py"
goto pack
:usepython
where python >nul 2>nul
if errorlevel 1 goto missing
set "reader_python=python"
:pack
"%reader_python%" "%~dp0pack_batch.py" --interactive -- %*
set "reader_result=%errorlevel%"
pause
exit /b %reader_result%
:usage
echo Drag books, folders of manga pages, or whole library folders onto Pack.cmd.
goto failed
:missing
echo Install Python 3.10 or later and the dependencies in requirements.txt.
:failed
pause
exit /b 1
NFOLIO_WINDOWS
# Keep the caller's working directory so relative book paths still point to their inputs.
case $0 in
    */*) folio_dir=${0%/*}/ ;;
    *)   folio_dir=./ ;;
esac

if [ "$#" -eq 0 ]; then
    printf '%s\n' 'Usage: sh Pack.cmd FILE_OR_FOLDER [FILE_OR_FOLDER ...]' >&2
    exit 1
fi

if [ -x "${folio_dir}.venv/bin/python" ]; then
    folio_python="${folio_dir}.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    folio_python=python3
else
    printf '%s\n' 'Install Python 3.10 or newer, then follow the desktop setup in README.md.' >&2
    exit 1
fi
exec "$folio_python" "${folio_dir}pack_batch.py" --interactive -- "$@"
