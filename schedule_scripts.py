import os


def create_schedule_scripts(project_dir: str, project_name: str, schedule_method: str = "") -> tuple[str, str]:
    """
    Create Windows (.bat) and Unix-like (.sh) scripts that:
      - resolve project dir and its parent,
      - activate venv located at the project root,
      - run main.py with sys.path bootstrapped to project root.

    If schedule_method in {"hourly","daily","once"}, add a cron/schtasks entry.
    Returns (bat_path, sh_path).
    """
    os.makedirs(project_dir, exist_ok=True)
    project_dir_abs = os.path.abspath(project_dir)
    project_root_abs = os.path.abspath(os.path.join(project_dir_abs, os.pardir))

    # -------------------------------
    # Windows Batch Script
    # -------------------------------
    bat_path = os.path.join(project_dir, "schedule_ingestion.bat")
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write("@echo off\n")
        f.write("REM Determine the folder containing this BAT file (the project folder)\n")
        f.write("set \"PROJECT_DIR=%~dp0\"\n")
        f.write("if \"%PROJECT_DIR:~-1%\"==\"\\\" set \"PROJECT_DIR=%PROJECT_DIR:~0,-1%\"\n")
        f.write("for %%I in (\"%PROJECT_DIR%\\..\") do set \"PROJECT_ROOT=%%~fI\"\n")
        f.write("echo Project Directory: %PROJECT_DIR%\n")
        f.write("echo Project Root: %PROJECT_ROOT%\n\n")
        f.write("cd /d \"%PROJECT_DIR%\"\n")
        f.write("call \"%PROJECT_ROOT%\\venv\\Scripts\\activate.bat\"\n\n")

        if schedule_method:
            task_name = project_name.replace(" ", "_") + "_Task"
            scheduled_command = (
                f'cmd /c cd /d "{project_dir_abs}" && '
                f'python -u -c "import sys; sys.path.insert(0, r\'{project_root_abs}\'); '
                f'exec(open(r\'{project_dir_abs}\\main.py\', encoding=\'utf-8\').read())"'
            )
            if schedule_method.lower() == "hourly":
                f.write(f'schtasks /create /tn "{task_name}" /tr "{scheduled_command}" /sc HOURLY /mo 1 /f\n')
            elif schedule_method.lower() == "daily":
                f.write(f'schtasks /create /tn "{task_name}" /tr "{scheduled_command}" /sc DAILY /st 00:00 /f\n')
            elif schedule_method.lower() == "once":
                f.write(f'schtasks /create /tn "{task_name}" /tr "{scheduled_command}" /sc ONCE /st 00:00 /f\n')
            else:
                f.write("echo Unrecognized schedule method; scheduling command not added.\n")

        # Always run main.py using bootstrap code
        f.write('python -u -c "import sys; sys.path.insert(0, r\'%PROJECT_ROOT%\'); '
                'exec(open(r\'%PROJECT_DIR%\\main.py\', encoding=\'utf-8\').read())"\n')
        f.write("\npause\n")

    # -------------------------------
    # Unix-like Shell Script
    # -------------------------------
    sh_path = os.path.join(project_dir, "schedule_ingestion.sh")
    with open(sh_path, "w", encoding="utf-8") as f:
        f.write("#!/bin/bash\n")
        f.write('PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"\n')
        f.write('PROJECT_ROOT="$(dirname "$PROJECT_DIR")"\n')
        f.write('echo "Project Directory: $PROJECT_DIR"\n')
        f.write('echo "Project Root: $PROJECT_ROOT"\n\n')
        f.write('cd "$PROJECT_DIR"\n')
        f.write('source "$PROJECT_ROOT/venv/bin/activate"\n\n')

        if schedule_method:
            if schedule_method.lower() == "hourly":
                cron_line = "0 * * * *"
                f.write("tmpfile=$(mktemp)\n")
                f.write(
                    'echo "' + cron_line +
                    ' cd $PROJECT_DIR && '
                    'python -u -c \\"import sys; sys.path.insert(0, \'$PROJECT_ROOT\'); '
                    'exec(open(\'$PROJECT_DIR/main.py\', encoding=\\\'utf-8\\\').read())\\" '
                    f'# {project_name}" >> "$tmpfile"\n'
                )
                f.write("crontab \"$tmpfile\"\n")
                f.write("rm \"$tmpfile\"\n")
                f.write('echo "Cron job set to run main.py every hour."\n')
            elif schedule_method.lower() == "daily":
                cron_line = "0 0 * * *"
                f.write("tmpfile=$(mktemp)\n")
                f.write(
                    'echo "' + cron_line +
                    ' cd $PROJECT_DIR && '
                    'python -u -c \\"import sys; sys.path.insert(0, \'$PROJECT_ROOT\'); '
                    'exec(open(\'$PROJECT_DIR/main.py\', encoding=\\\'utf-8\\\').read())\\" '
                    f'# {project_name}" >> "$tmpfile"\n'
                )
                f.write("crontab \"$tmpfile\"\n")
                f.write("rm \"$tmpfile\"\n")
                f.write('echo "Cron job set to run main.py once daily at midnight."\n')
            elif schedule_method.lower() == "once":
                f.write("# 'once' scheduling is not straightforward with cron.\n")
                f.write("# No cron line added. You could manually add a one-time cron entry if needed.\n")
            else:
                f.write('echo "Unrecognized schedule method; scheduling command not added."\n')

        # Always run main.py (fixed: no stray % after $PROJECT_ROOT)
        f.write(
            'python -u -c "import sys; sys.path.insert(0, \'$PROJECT_ROOT\'); '
            'exec(open(\'$PROJECT_DIR/main.py\', encoding=\'utf-8\').read())"\n'
        )
    os.chmod(sh_path, 0o755)

    return bat_path, sh_path


def create_server_scripts(project_dir: str, server_token: str, host: str = "127.0.0.1", port: int = 5000) -> tuple[str, str]:
    """
    Create start_server.bat and start_server.sh in project_dir using the given token.
    Returns (bat_server_path, sh_server_path).
    """
    os.makedirs(project_dir, exist_ok=True)

    bat_server_path = os.path.join(project_dir, "start_server.bat")
    with open(bat_server_path, "w", encoding="utf-8") as f:
        f.write(f"""@echo off

REM Change directory to the folder that contains this BAT file
pushd %~dp0

REM Go up ONE level to the project root
cd ..\\

REM Activate the virtual environment
call venv\\Scripts\\activate.bat

REM Run the server, passing the required parameters
python -c "from web.server import run_server; run_server('{server_token}', '{project_dir}', host='{host}', port={port})"

REM Return to original folder
popd
pause
""")

    sh_server_path = os.path.join(project_dir, "start_server.sh")
    with open(sh_server_path, "w", encoding="utf-8") as f:
        f.write(f"""#!/bin/bash
# Start the web interface for file uploads and processing.

# Move to the directory containing this script
cd "$(dirname "$0")"

# Go up ONE level to the project root
cd ..

# Activate the virtual environment
source venv/bin/activate

# Run the server, passing the required parameters
python -c "from web.server import run_server; run_server('{server_token}', '{project_dir}', host='{host}', port={port})"
""")
    os.chmod(sh_server_path, 0o755)

    return bat_server_path, sh_server_path
