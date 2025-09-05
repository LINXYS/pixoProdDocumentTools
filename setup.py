import os
import shutil
import yaml
from dotenv import load_dotenv
import secrets

from web.server import run_server
from cfg import DEFAULT_CONFIG, SUPPORTED_EMBEDDING_PROVIDERS, SUPPORTED_LLM_PROVIDERS, SUPPORTED_EMBEDDING_MODELS


def create_schedule_scripts(project_dir, project_name, schedule_method):
    """
    Create two scripts (one .bat for Windows and one .sh for Unix‑like systems)
    that, when run (or registered as scheduled tasks), will:
      - Determine the project folder and its parent (the project root),
      - Activate the virtual environment (assumed to be in the project root's "venv" folder),
      - Then run main.py using bootstrap code that inserts the project root into sys.path.

    This ensures that imports (like `from cfg import load_config`) work correctly.
    """
    # -------------------------------
    # 1) Windows Batch Script
    # -------------------------------
    bat_path = os.path.join(project_dir, "schedule_ingestion.bat")
    with open(bat_path, "w", encoding="utf-8") as f:
        # Begin with standard commands to get the folder paths.
        f.write("@echo off\n")
        f.write("REM Determine the folder containing this BAT file (the project folder)\n")
        f.write("set \"PROJECT_DIR=%~dp0\"\n")
        f.write("if \"%PROJECT_DIR:~-1%\"==\"\\\" set \"PROJECT_DIR=%PROJECT_DIR:~0,-1%\"\n")
        f.write("for %%I in (\"%PROJECT_DIR%\\..\") do set \"PROJECT_ROOT=%%~fI\"\n")
        f.write("echo Project Directory: %PROJECT_DIR%\n")
        f.write("echo Project Root: %PROJECT_ROOT%\n")
        f.write("\n")
        # Change to the project folder.
        f.write("cd /d \"%PROJECT_DIR%\"\n")
        # Activate the virtual environment (assumes the venv folder is in the project root)
        f.write("call \"%PROJECT_ROOT%\\venv\\Scripts\\activate.bat\"\n")
        f.write("\n")
        # If a scheduling method is provided, register a scheduled task using a command that:
        #   - remains in the project folder, and
        #   - runs Python with bootstrap code (like your /start endpoint)
        if schedule_method:
            task_name = project_name.replace(" ", "_") + "_Task"
            # Build the scheduled command.
            # Note: When scheduled by Windows Task Scheduler, it's good to use cmd /c to run multiple commands.
            scheduled_command = (
                'cmd /c cd /d "%PROJECT_DIR%" && '
                'python -u -c "import sys; sys.path.insert(0, r\'%PROJECT_ROOT%\'); '
                'exec(open(r\'%PROJECT_DIR%\\main.py\', encoding=\'utf-8\').read())"'
            )
            if schedule_method.lower() == "hourly":
                f.write(f'schtasks /create /tn "{task_name}" /tr "{scheduled_command}" /sc HOURLY /mo 1 /f\n')
            elif schedule_method.lower() == "daily":
                f.write(f'schtasks /create /tn "{task_name}" /tr "{scheduled_command}" /sc DAILY /st 00:00 /f\n')
            elif schedule_method.lower() == "once":
                f.write(f'schtasks /create /tn "{task_name}" /tr "{scheduled_command}" /sc ONCE /st 00:00 /f\n')
            else:
                f.write("echo Unrecognized schedule method; scheduling command not added.\n")
        # Always run main.py using the bootstrap code.
        f.write('python -u -c "import sys; sys.path.insert(0, r\'%PROJECT_ROOT%\'); '
                'exec(open(r\'%PROJECT_DIR%\\main.py\', encoding=\'utf-8\').read())"\n')
        f.write("\npause\n")

    # -------------------------------
    # 2) Unix‑like Shell Script
    # -------------------------------
    sh_path = os.path.join(project_dir, "schedule_ingestion.sh")
    with open(sh_path, "w", encoding="utf-8") as f:
        f.write("#!/bin/bash\n")
        # Determine the folder where this script resides (the project folder)
        f.write('PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"\n')
        # Determine the project root (parent directory)
        f.write('PROJECT_ROOT="$(dirname "$PROJECT_DIR")"\n')
        f.write('echo "Project Directory: $PROJECT_DIR"\n')
        f.write('echo "Project Root: $PROJECT_ROOT"\n')
        f.write("\n")
        # Change to the project folder.
        f.write('cd "$PROJECT_DIR"\n')
        # Activate the virtual environment (assumes venv is in the project root)
        f.write('source "$PROJECT_ROOT/venv/bin/activate"\n')
        f.write("\n")
        if schedule_method:
            if schedule_method.lower() == "hourly":
                cron_line = "0 * * * *"
                f.write("tmpfile=$(mktemp)\n")
                f.write('echo "' + cron_line +
                        ' cd \'$PROJECT_DIR\' && '
                        'python -u -c \\"import sys; sys.path.insert(0, r\'$PROJECT_ROOT\'); '
                        'exec(open(r\'$PROJECT_DIR/main.py\', encoding=\'utf-8\').read())\\" '
                        f'# {project_name}" >> "$tmpfile"\n')
                f.write("crontab \"$tmpfile\"\n")
                f.write("rm \"$tmpfile\"\n")
                f.write('echo "Cron job set to run main.py every hour."\n')
            elif schedule_method.lower() == "daily":
                cron_line = "0 0 * * *"
                f.write("tmpfile=$(mktemp)\n")
                f.write('echo "' + cron_line +
                        ' cd \'$PROJECT_DIR\' && '
                        'python -u -c \\"import sys; sys.path.insert(0, r\'$PROJECT_ROOT\'); '
                        'exec(open(r\'$PROJECT_DIR/main.py\', encoding=\'utf-8\').read())\\" '
                        f'# {project_name}" >> "$tmpfile"\n')
                f.write("crontab \"$tmpfile\"\n")
                f.write("rm \"$tmpfile\"\n")
                f.write('echo "Cron job set to run main.py once daily at midnight."\n')
            elif schedule_method.lower() == "once":
                f.write("# 'once' scheduling is not straightforward with cron.\n")
                f.write("# No cron line added. You could manually add a one-time cron entry if needed.\n")
            else:
                f.write('echo "Unrecognized schedule method; scheduling command not added."\n')
        # Always run main.py using the bootstrap code.
        f.write('python -u -c "import sys; sys.path.insert(0, r\'$PROJECT_ROOT%\'); '
                'exec(open(r\'$PROJECT_DIR/main.py\', encoding=\'utf-8\').read())"\n')
    os.chmod(sh_path, 0o755)


def main():
    load_dotenv()

    # --- Project Setup ---
    project_name = os.path.basename(os.getcwd())
    schedule_method = ""  # Do not schedule automatically; just generate scripts.

    # --- Create Project Directory ---
    project_dir = project_name.replace(" ", "_").lower()

    os.makedirs(project_dir, exist_ok=True)

    # Create the 'files' folder inside the project directory
    files_folder_path = os.path.join(project_dir, 'files')
    os.makedirs(files_folder_path, exist_ok=True)

    try:
        shutil.copy2('main.py', os.path.join(project_dir, 'main.py'))
    except Exception as e:
        print(f"Error copying main.py: {e}")
        return

    # Do not prompt for or create .env. Use the existing .env in current directory (already loaded by load_dotenv()).
    # Validate presence of required env vars to fail fast.
    if not os.getenv("DATABASE_URL") or not os.getenv("RECORD_MANAGER_DATABASE_URL"):
        print("Error: DATABASE_URL and/or RECORD_MANAGER_DATABASE_URL not found in the current directory's .env.")
        return

    # --- Interactive YAML Config prompts using centralized defaults ---
    # Start with centralized defaults
    config = dict(DEFAULT_CONFIG)
    # Suggest collection_name as the project directory by default
    config["collection_name"] = project_dir

    # Helper to get int with default
    def prompt_int(_prompt, default_val):
        return default_val

    # Helper to get bool with default
    def prompt_bool(_prompt, default_val):
        return default_val

    # chunking
    config["chunk_size"] = prompt_int("Chunk size (characters)", config["chunk_size"])
    config["chunk_overlap"] = prompt_int("Chunk overlap (characters)", config["chunk_overlap"])
    config["use_chunking"] = prompt_bool("Enable chunking", config["use_chunking"])

    # embedding provider
    # Keep defaults; no prompts.
    provider = config["embedding_provider"]

    # vector size
    # Leave as default/auto; no prompts.
    config["vector_size"] = DEFAULT_CONFIG["vector_size"]

    # llm provider
    # Keep default without prompting.

    # collection name
    # Keep default based on project_dir.

    # --- Write YAML Config File ---
    config_path = os.path.join(project_dir, 'config.yaml')
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    except Exception as e:
        print(f"Error writing config file: {e}")
        return

    # Generate the scheduling scripts (both .bat and .sh)
    create_schedule_scripts(project_dir, project_name, schedule_method)

    ############################################################################
    #                   Generate Server Start Scripts                          #
    ############################################################################
    # Generate a token to secure the web interface (server) and save it.
    server_token = secrets.token_urlsafe(16)
    server_token_file = os.path.join(project_dir, "server_token.txt")
    with open(server_token_file, "w", encoding="utf-8") as f:
        f.write(server_token)
    print(f"Server token saved to {server_token_file}")

    # Create a Windows batch file to start the server.
    # Create a Windows batch file to start the server.
    bat_server_path = os.path.join(project_dir, "start_server.bat")
    with open(bat_server_path, "w", encoding="utf-8") as f:
        f.write(f"""@echo off

    REM Change directory to the folder that contains this BAT file
    pushd %~dp0

    REM Go up ONE level (adjust if you actually need more) to get back to pixoDocumentToolsV3 root
    cd ..\\

    REM Activate the virtual environment
    call venv\\Scripts\\activate.bat

    REM Run the server, passing the required parameters
    python -c "from web.server import run_server; run_server('{server_token}', '{project_dir}', host='127.0.0.1', port=5000)"

    REM Return to original folder
    popd
    pause
    """)
    print(f"Created {bat_server_path}")

    # Create a Unix-like shell script to start the server.
    sh_server_path = os.path.join(project_dir, "start_server.sh")
    with open(sh_server_path, "w", encoding="utf-8") as f:
        f.write(f"""#!/bin/bash
    # Start the web interface for file uploads and process.
    # The server will automatically terminate after 1 hour.

    # Move to the directory containing this script
    cd "$(dirname "$0")"

    # Go up ONE level to the project root (adjust '..' as needed)
    cd ..

    # Activate the virtual environment
    source venv/bin/activate

    # Run the server, passing the required parameters
    python -c "from web.server import run_server; run_server('{server_token}', '{project_dir}')"
    """)
    os.chmod(sh_server_path, 0o755)
    print(f"Created {sh_server_path}")

    print(f"\nSetup complete! Files have been created in '{project_dir}':\n")
    print("  - main.py (ingestion script)")
    print("  - config.yaml (YAML config)")
    print("  - .env (environment variables: DATABASE_URL, RECORD_MANAGER_DATABASE_URL)")
    print("  - schedule_ingestion.bat (Windows scheduling or execution script)")
    print("  - schedule_ingestion.sh  (Unix-like scheduling or execution script)")
    print("  - start_server.bat and start_server.sh (to start the file upload web interface)")
    if schedule_method:
        print(
            "\nNOTE: The scheduling scripts do not run automatically. To actually schedule the job:\n"
            "  - On Windows, open a command prompt and run 'schedule_ingestion.bat'\n"
            "  - On Unix-like systems, run 'schedule_ingestion.sh'\n"
        )
    else:
        print("No scheduling method was selected; the scheduling scripts will simply execute main.py when run.")

    # --- Offer to run the Web Interface Immediately ---
    print("To start the web interface later, run the generated start_server script (start_server.bat or start_server.sh).")


if __name__ == "__main__":
    main()
