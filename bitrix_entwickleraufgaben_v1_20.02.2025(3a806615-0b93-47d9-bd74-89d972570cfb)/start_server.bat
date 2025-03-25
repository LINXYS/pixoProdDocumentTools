@echo off

    REM Change directory to the folder that contains this BAT file
    pushd %~dp0

    REM Go up ONE level (adjust if you actually need more) to get back to pixoDocumentToolsV3 root
    cd ..\

    REM Activate the virtual environment
    call venv\Scripts\activate.bat

    REM Run the server, passing the required parameters
    python -c "from web.server import run_server; run_server('LNtnHm8SBIrLGVaUjxiFew', 'bitrix_entwickleraufgaben_v1_20.02.2025(3a806615-0b93-47d9-bd74-89d972570cfb)', host='127.0.0.1', port=5000)"

    REM Return to original folder
    popd
    pause
    