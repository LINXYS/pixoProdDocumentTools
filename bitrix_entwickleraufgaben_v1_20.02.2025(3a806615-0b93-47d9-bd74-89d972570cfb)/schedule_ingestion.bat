@echo off
REM Determine the folder containing this BAT file (the project folder)
set "PROJECT_DIR=%~dp0"
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
for %%I in ("%PROJECT_DIR%\..") do set "PROJECT_ROOT=%%~fI"
echo Project Directory: %PROJECT_DIR%
echo Project Root: %PROJECT_ROOT%

cd /d "%PROJECT_DIR%"
call "%PROJECT_ROOT%\venv\Scripts\activate.bat"

python -u -c "import sys; sys.path.insert(0, r'%PROJECT_ROOT%'); exec(open(r'%PROJECT_DIR%\main.py', encoding='utf-8').read())"

pause
