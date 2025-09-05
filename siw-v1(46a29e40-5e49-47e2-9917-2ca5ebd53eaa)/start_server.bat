@echo off

    REM Change directory to the folder that contains this BAT file
    pushd %~dp0

    REM Go up ONE level (adjust if you actually need more) to get back to pixoDocumentToolsV3 root
    cd ..\

    REM Activate the virtual environment
    call venv\Scripts\activate.bat

    REM Run the server, passing the required parameters
    python -c "from web.server import run_server; run_server('zhujfomJSuin0I5LKl_CNQ', 'siw-v1(46a29e40-5e49-47e2-9917-2ca5ebd53eaa)', host='127.0.0.1', port=5000)"

    REM Return to original folder
    popd
    pause
    