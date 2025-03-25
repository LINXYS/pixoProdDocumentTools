@echo off

    REM Change directory to the folder that contains this BAT file
    pushd %~dp0

    REM Go up ONE level (adjust if you actually need more) to get back to pixoDocumentToolsV3 root
    cd ..\

    REM Activate the virtual environment
    call venv\Scripts\activate.bat

    REM Run the server, passing the required parameters
    python -c "from web.server import run_server; run_server('uiGoaMofnVh7cpwZ1x5wOQ', 'kaffeerösterei_test_#1(25f8a8a0-7925-4085-9db9-beffc8e40ae7)', host='127.0.0.1', port=5000)"

    REM Return to original folder
    popd
    pause
    