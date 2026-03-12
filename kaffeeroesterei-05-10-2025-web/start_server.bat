@echo off

REM Change directory to the folder that contains this BAT file
pushd %~dp0

REM Go up ONE level to the project root
cd ..\

REM Activate the virtual environment
call venv\Scripts\activate.bat

REM Run the server, passing the required parameters
python -c "from web.server import run_server; run_server('vyJMAI5R0ILfSkG95MQnJA', 'kaffeeroesterei-05-10-2025-web', host='127.0.0.1', port=5000)"

REM Return to original folder
popd
pause
