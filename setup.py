import os
import shutil
import requests
import yaml
from dotenv import load_dotenv
import secrets

from web.server import run_server

# (Assuming the above server code is in web/server.py)
# from web.server import run_server

API_URL_FILE = "api_url.txt"


def load_api_url():
    """Load the saved API URL from a local file, if it exists."""
    if os.path.exists(API_URL_FILE):
        with open(API_URL_FILE, 'r', encoding='utf-8') as f:
            url = f.read().strip()
            if url:
                print("Using saved API URL:", url)
                return url
    return None


def save_api_url(url):
    """Save the API URL to a local file."""
    with open(API_URL_FILE, 'w', encoding='utf-8') as f:
        f.write(url)
    print("Saved new API URL:", url)


def get_access_token(url, username, password):
    payload = {'username': username, 'password': password}
    login_url = url.rstrip("/") + "/auth/jwt/login"
    print(f"\nRequesting token from: {login_url} (username: {username})")
    try:
        response = requests.post(login_url, data=payload)
    except requests.exceptions.RequestException as e:
        print("Connection error during token request:", e)
        return {'error': 'Connection error during token retrieval', 'connection_error': True}

    if response.status_code == 200:
        print("Token retrieved successfully.")
        return response.json()
    else:
        print(f"Failed to retrieve token. URL: {response.url}")
        print("Status Code:", response.status_code)
        print("Response Content:", response.text)
        return {'error': 'Failed to retrieve token', 'status_code': response.status_code}


def create_project(url, token, project_name, store_conversations, prompt_or_template, chaincfg):
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }
    # API expects some parameters as query parameters and the chain config as the JSON body.
    query_params = {
        "project_name": project_name,
        "store_conversations": store_conversations,
        "prompt_or_template": prompt_or_template
    }
    create_project_url = url.rstrip("/") + "/createproject"
    print(f"\nCreating project via: {create_project_url}")
    print("Query Parameters:", query_params)
    print("Chain configuration (request body):", chaincfg)

    try:
        response = requests.post(create_project_url, params=query_params, json=chaincfg, headers=headers)
    except requests.exceptions.RequestException as e:
        print("Exception occurred during project creation:", e)
        return {'error': 'Exception during project creation'}

    if response.status_code == 200:
        print("Project created successfully. Response:")
        print(response.json())
        return response.json()
    else:
        print("\nFailed to create project.")
        print("Request URL:", response.url)
        print("Status Code:", response.status_code)
        print("Response Content:", response.text)
        return {'error': 'Failed to create project', 'status_code': response.status_code}


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
        f.write('python -u -c "import sys; sys.path.insert(0, r\'$PROJECT_ROOT\'); '
                'exec(open(r\'$PROJECT_DIR/main.py\', encoding=\'utf-8\').read())"\n')
    os.chmod(sh_path, 0o755)


def main():
    load_dotenv()

    # --- API URL handling ---
    api_url = load_api_url()
    if not api_url:
        api_url = input("Enter the API URL: ").strip()
        save_api_url(api_url)

    username = os.getenv("PIXO_USERNAME") or input("Enter your username: ").strip()
    password = os.getenv("PIXO_PASSWORD") or input("Enter your password: ").strip()

    token_response = get_access_token(api_url, username, password)
    while token_response.get('connection_error'):
        print("\nThe current API URL appears to be unreachable.")
        api_url = input("Enter a new API URL: ").strip()
        save_api_url(api_url)
        token_response = get_access_token(api_url, username, password)

    if 'error' in token_response:
        print(f"Error: {token_response['error']}")
        return

    access_token = token_response.get('access_token')
    if not access_token:
        print("Error: No access token found in the response.")
        return

    # --- Project Setup ---
    project_name = input("Enter the project name: ").strip()
    store_conversations = True
    prompt_or_template = input(
        "Enter prompt or template (default: ___TEMPLATE___:standard_restrictive_prompt_english): "
    ).strip() or "___TEMPLATE___:standard_restrictive_prompt_english"

    default_chain_config = {
        "generator": "gpt-4o-mini",
        "temperature": 0.1,
        "chunksize": 1000,
        "chunkoverlap": 100,
        "rerank": "cohere",
        "rerankertopn": 10,
        "dbtopn": 25,
        "condenser": "gpt-4o-mini",
        "embeddings": "openai",
        "language": "de",
        "schedule": "d-* h-*"
    }

    not_change_keys = ['rerankertopn', 'dbtopn', 'condenser', 'schedule']

    edit_config = input("Do you want to edit the chain config? (y/n): ").strip().lower() == 'y'
    if edit_config:
        print("Enter new values for chain config (press Enter to keep default):")
        for key, value in default_chain_config.items():
            if key in not_change_keys:
                continue
            new_value = input(f"{key} ({value}): ").strip()
            if new_value:
                try:
                    default_chain_config[key] = type(value)(new_value)
                except Exception as e:
                    print(f"Could not convert value for {key}: {e}. Keeping default {value}.")

    project_response = create_project(
        api_url,
        access_token,
        project_name,
        store_conversations,
        prompt_or_template,
        default_chain_config
    )
    if 'error' in project_response:
        print(f"Error: {project_response['error']}")
        return

    print("Project created successfully!")
    project_id = project_response.get('project_id')
    if not project_id:
        print("Warning: No project ID returned.")

    # Ask for scheduling method (optional)
    print("\nScheduling method options:\n  - hourly\n  - daily\n  - once\n  - (leave blank to skip scheduling)\n")
    schedule_method = input("Enter scheduling method: ").strip().lower()

    # --- Create Project Directory ---
    if project_id:
        project_dir = f"{project_name.replace(' ', '_').lower()}({project_id})"
    else:
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

    # --- Write YAML Config File ---
    config = {
        "chunk_overlap": 200,            # Example updated value
        "chunk_size": 1000,
        "embedding_provider": "openai",
        "project_id": project_id or "documents",  # Use the project ID or a default value
        "use_chunking": True
    }
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
    python -c "from web.server import run_server; run_server('{server_token}', '.')"
    """)
    os.chmod(sh_server_path, 0o755)
    print(f"Created {sh_server_path}")

    print(f"\nSetup complete! Files have been created in '{project_dir}':\n")
    print("  - main.py (ingestion script)")
    print("  - config.yaml (YAML config)")
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
    run_web = input(
        "\nDo you want to start the web interface for file uploads and starting the process now? (y/n): "
    ).strip().lower()
    if run_web == 'y':
        # Start the web server immediately using the generated token.
        run_server(server_token, project_dir, host="127.0.0.1", port=5000)
    else:
        print(
            "You can later run the web interface by executing the generated start_server scripts (start_server.bat or start_server.sh)."
        )


if __name__ == "__main__":
    main()
