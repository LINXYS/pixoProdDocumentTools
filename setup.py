import os
import shutil
import requests

import yaml
from dotenv import load_dotenv

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
    Create two scripts (one .bat, one .sh) that either register a scheduled job
    or simply run main.py using python. In all cases, the scripts will include a
    command to execute 'python main.py'.
    """
    # 1) Windows BAT file
    bat_path = os.path.join(project_dir, "schedule_ingestion.bat")
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write("@echo off\n")
        f.write("REM This script will execute main.py using Python.\n\n")
        # If a scheduling method is provided, add the registration command:
        if schedule_method:
            task_name = project_name.replace(" ", "_") + "_Task"
            if schedule_method.lower() == "hourly":
                f.write(f'schtasks /create /tn "{task_name}" /tr "python main.py" /sc HOURLY /mo 1 /f\n')
            elif schedule_method.lower() == "daily":
                f.write(f'schtasks /create /tn "{task_name}" /tr "python main.py" /sc DAILY /st 00:00 /f\n')
            elif schedule_method.lower() == "once":
                f.write(f'schtasks /create /tn "{task_name}" /tr "python main.py" /sc ONCE /st 00:00 /f\n')
            else:
                f.write("echo Unrecognized schedule method; scheduling command not added.\n")
        # Always run main.py:
        f.write("python main.py\n")
        f.write("\npause\n")

    # 2) Unix-like SH file
    sh_path = os.path.join(project_dir, "schedule_ingestion.sh")
    with open(sh_path, "w", encoding="utf-8") as f:
        f.write("#!/bin/bash\n")
        f.write("# This script will execute main.py using Python.\n\n")
        if schedule_method:
            if schedule_method.lower() == "hourly":
                cron_line = "0 * * * *"
                f.write("tmpfile=$(mktemp)\n")
                f.write("crontab -l > \"$tmpfile\" 2>/dev/null\n")
                f.write(f'echo "{cron_line} cd $(pwd) && python main.py # {project_name}" >> \"$tmpfile\"\n')
                f.write("crontab \"$tmpfile\"\n")
                f.write("rm \"$tmpfile\"\n")
                f.write('echo "Cron job set to run main.py every hour."\n')
            elif schedule_method.lower() == "daily":
                cron_line = "0 0 * * *"
                f.write("tmpfile=$(mktemp)\n")
                f.write("crontab -l > \"$tmpfile\" 2>/dev/null\n")
                f.write(f'echo "{cron_line} cd $(pwd) && python main.py # {project_name}" >> \"$tmpfile\"\n')
                f.write("crontab \"$tmpfile\"\n")
                f.write("rm \"$tmpfile\"\n")
                f.write('echo "Cron job set to run main.py once daily at midnight."\n')
            elif schedule_method.lower() == "once":
                f.write("# 'once' scheduling is not straightforward with cron.\n")
                f.write("# No cron line added. You could manually add a one-time cron entry if needed.\n")
            else:
                f.write('echo "Unrecognized schedule method; scheduling command not added."\n')
        # Always run main.py:
        f.write("python main.py\n")
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
    os.makedirs(os.path.join(project_dir, 'files'), exist_ok=True)

    try:
        shutil.copy2('main.py', os.path.join(project_dir, 'main.py'))
    except Exception as e:
        print(f"Error copying main.py: {e}")
        return

    # --- Write YAML Config File ---
    # Adjust the config dictionary to include only the desired keys and values.
    config = {
        "chunk_overlap": 200,            # Updated value as requested
        "chunk_size": 1000,
        "embedding_provider": "openai",
        "project_id": project_id or "documents",  # Use the project ID or a default value
        "use_chunking": True
    }
    config_path = os.path.join(project_dir, 'config.yaml')
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            # Write in YAML format with a clean, block-style (not flow style)
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    except Exception as e:
        print(f"Error writing config file: {e}")
        return

    # Generate the scheduling scripts (both .bat and .sh)
    create_schedule_scripts(project_dir, project_name, schedule_method)

    print(f"\nSetup complete! Files have been created in '{project_dir}':\n")
    print("  - main.py (ingestion script)")
    print("  - config.yaml (YAML config)")
    print("  - schedule_ingestion.bat (Windows scheduling or execution script)")
    print("  - schedule_ingestion.sh  (Unix-like scheduling or execution script)")
    if schedule_method:
        print(
            "\nNOTE: The scripts do not run automatically. To actually schedule the job:\n"
            "  - On Windows, open a command prompt and run 'schedule_ingestion.bat'\n"
            "  - On Unix-like systems, run 'schedule_ingestion.sh'\n"
        )
    else:
        print("No scheduling method was selected; the scripts will simply execute main.py when run.")

if __name__ == "__main__":
    main()
