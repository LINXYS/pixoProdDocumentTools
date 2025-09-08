import os
import platform
import re
import shutil
import subprocess

import yaml
from dotenv import load_dotenv
import secrets

# Optional interactive UI (recommended: pip install questionary)
try:
    import questionary
    from questionary import Choice
except Exception:
    questionary = None
    Choice = None

from database.pg_bootstrap import bootstrap_postgres_if_needed
from cfg import (
    DEFAULT_CONFIG,
    SUPPORTED_EMBEDDING_PROVIDERS,
    SUPPORTED_LLM_PROVIDERS,
    SUPPORTED_EMBEDDING_MODELS,
    OPENAI_DEFAULT_VECTOR_SIZES,
)

from schedule_scripts import create_schedule_scripts, create_server_scripts


# -------------------------------
# Utilities
# -------------------------------
def slugify_name(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", (name or "").strip())
    slug = re.sub(r"_+", "_", slug).strip("_").lower()
    return slug


def _yn(prompt, default=True):
    if questionary:
        return questionary.confirm(prompt, default=default).ask()
    suffix = " [Y/n] " if default else " [y/N] "
    ans = input(prompt + suffix).strip().lower()
    if ans == "":
        return default
    return ans in {"y", "yes"}


def _text(prompt, default=None):
    if questionary:
        return questionary.text(prompt, default=default or "").ask()
    val = input(f"{prompt} [{default}] " if default else f"{prompt} ")
    return val if val.strip() else (default or "")


def _int(prompt, default, choices=None):
    if questionary and choices:
        opts = [Choice(str(x), x) for x in sorted(set(choices + [default]))]
        opts.append(Choice("Custom…", "__custom__"))
        sel = questionary.select(prompt, choices=opts, default=default).ask()
        if sel == "__custom__":
            return _int(prompt + " (custom)", default, None)
        return int(sel)
    while True:
        raw = input(f"{prompt} [{default}] ")
        if not raw.strip():
            return default
        try:
            return int(raw)
        except ValueError:
            print("Please enter an integer.")


def _select(prompt, options, default):
    options = list(dict.fromkeys(options))
    if questionary:
        return questionary.select(prompt, choices=options, default=default).ask()
    print(prompt)
    for i, opt in enumerate(options, 1):
        mark = " (default)" if opt == default else ""
        print(f"  {i}. {opt}{mark}")
    while True:
        raw = input(f"Choose [1-{len(options)}] (Enter for default {default}): ")
        if not raw.strip():
            return default
        try:
            i = int(raw)
            if 1 <= i <= len(options):
                return options[i - 1]
        except ValueError:
            pass
        print("Invalid choice.")


# -------------------------------
# Build interactive YAML config (project name already chosen)
# -------------------------------
def build_interactive_config(collection_name: str) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    # Default for collection_name should be empty; here we set it from chosen project name.
    cfg["collection_name"] = collection_name

    print("\n--- Interactive configuration ---\n")

    # Chunking flag
    if not _yn(f"Keep default 'use_chunking' = {cfg['use_chunking']}?"):
        cfg["use_chunking"] = _yn("Enable chunking?", default=cfg["use_chunking"])

    # Chunk size
    if not _yn(f"Keep default 'chunk_size' = {cfg['chunk_size']}?"):
        cfg["chunk_size"] = _int(
            "Chunk size (characters)",
            default=cfg["chunk_size"],
            choices=[256, 500, 1000, 1500, 2000, 4000],
        )

    # Chunk overlap
    if not _yn(f"Keep default 'chunk_overlap' = {cfg['chunk_overlap']}?"):
        cfg["chunk_overlap"] = _int(
            "Chunk overlap (characters)",
            default=cfg["chunk_overlap"],
            choices=[0, 50, 100, 200, 300, 500],
        )

    # Embedding provider
    if not _yn(f"Keep default 'embedding_provider' = {cfg['embedding_provider']}?"):
        cfg["embedding_provider"] = _select(
            "Embedding provider",
            SUPPORTED_EMBEDDING_PROVIDERS,
            default=cfg["embedding_provider"],
        )

    # Embedding model (provider-dependent list if we have one)
    models = SUPPORTED_EMBEDDING_MODELS.get(cfg["embedding_provider"].lower(), [])
    if models:
        if not _yn(f"Keep default 'embedding_model' = {cfg['embedding_model']}?"):
            cfg["embedding_model"] = _select(
                "Embedding model", models, default=cfg["embedding_model"]
            )
    else:
        if not _yn(f"Keep default 'embedding_model' = {cfg['embedding_model']}?"):
            cfg["embedding_model"] = _text(
                "Embedding model (free text)", default=cfg["embedding_model"]
            )

    # Vector size
    if cfg["embedding_provider"].lower() == "openai":
        auto_dim = OPENAI_DEFAULT_VECTOR_SIZES.get(cfg["embedding_model"], 1536)
        if not _yn(f"Use OpenAI auto vector size {auto_dim} for '{cfg['embedding_model']}'?"):
            cfg["vector_size"] = _int("Vector size (dimensions)", default=auto_dim)
        else:
            cfg["vector_size"] = None  # let cfg.load_config compute
    else:
        current = cfg["vector_size"] if cfg["vector_size"] else "auto"
        if not _yn(f"Keep default 'vector_size' = {current}?"):
            val = _int(
                "Vector size (dimensions) — enter 0 for auto",
                default=cfg["vector_size"] or 0,
            )
            cfg["vector_size"] = None if val in (0, None) else val

    # LLM provider
    if not _yn(f"Keep default 'llm_provider' = {cfg['llm_provider']}?"):
        cfg["llm_provider"] = _select(
            "LLM provider",
            SUPPORTED_LLM_PROVIDERS,
            default=cfg["llm_provider"],
        )

    return cfg


# -------------------------------
# Environment handling
# -------------------------------
def ensure_env_in_project_auto(project_dir: str):
    """
    Always use existing environment if present.
    If either DATABASE_URL or RECORD_MANAGER_DATABASE_URL is missing,
    auto-create a .env in the project folder with sensible defaults (no prompts).
    """
    load_dotenv(override=False)

    db_url = os.getenv("DATABASE_URL")
    rec_url = os.getenv("RECORD_MANAGER_DATABASE_URL")

    if db_url and rec_url:
        print("Using existing environment variables (no .env created).")
        return

    env_path = os.path.join(project_dir, ".env")
    if not db_url:
        db_url = "postgresql+psycopg2://user:pass@localhost:5432/vectorstore"
    if not rec_url:
        rec_url = db_url

    os.makedirs(project_dir, exist_ok=True)
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(f"DATABASE_URL={db_url}\n")
        f.write(f"RECORD_MANAGER_DATABASE_URL={rec_url}\n")

    print(f"Created {env_path} (edit it with your real credentials).")


# -------------------------------
# Main
# -------------------------------
def main():
    load_dotenv(override=False)

    # Derive a fallback from the repo folder; **prompt default is empty**
    repo_basename = os.path.basename(os.getcwd())
    fallback_dir = slugify_name(repo_basename)

    print("\n--- Project setup ---\n")
    project_name_input = _text("Project name (used as collection/namespace)", default="").strip()

    if project_name_input:
        project_name_display = project_name_input
        project_dir = slugify_name(project_name_input)
    else:
        project_name_display = repo_basename
        project_dir = fallback_dir

    # This name (slug) is used for the collection to avoid spaces/special chars
    collection_name = project_dir

    # Create project folders early to avoid 'No such file or directory'
    os.makedirs(project_dir, exist_ok=True)
    os.makedirs(os.path.join(project_dir, "files"), exist_ok=True)

    # Copy main.py into the project dir
    try:
        shutil.copy2("main.py", os.path.join(project_dir, "main.py"))
    except Exception as e:
        print(f"Error copying main.py: {e}")
        return

    # Build config (no second prompt for project name)
    config = build_interactive_config(collection_name=collection_name)

    # Write config.yaml into the chosen project folder
    config_path = os.path.join(project_dir, "config.yaml")
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    except Exception as e:
        print(f"Error writing config file: {e}")
        return

    # Environment: use existing, else auto-create .env in project folder
    ensure_env_in_project_auto(project_dir)

    # Bootstrap Postgres using the CHOSEN collection name
    try:
        bootstrap_postgres_if_needed(collection_name=config["collection_name"])
    except Exception as e:
        print(f"Postgres bootstrap warning: {e}")

    # Generate scheduler scripts
    schedule_method = ""  # or "hourly"/"daily"/"once"
    bat_path, sh_path = create_schedule_scripts(
        project_dir=project_dir,
        project_name=project_name_display,
        schedule_method=schedule_method,
    )

    # Server token + start scripts
    server_token = secrets.token_urlsafe(16)
    server_token_file = os.path.join(project_dir, "server_token.txt")
    with open(server_token_file, "w", encoding="utf-8") as f:
        f.write(server_token)
    bat_server_path, sh_server_path = create_server_scripts(project_dir, server_token)

    print(f"\nServer token saved to {server_token_file}")
    print(f"Created {bat_server_path}")
    print(f"Created {sh_server_path}")

    print(f"\nSetup complete! Files have been created in '{project_dir}':\n")
    print("  - main.py (ingestion script)")
    print("  - config.yaml (YAML config)")
    print("  - .env (auto-created here if missing; otherwise using your existing environment)")
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

    print("To start the web interface later, run the generated start_server script (start_server.bat or start_server.sh).")
    maybe_start_server_now(project_dir)


def maybe_start_server_now(project_dir: str):
    """
    Ask whether to start the server immediately and run the appropriate script.
    Uses absolute paths to avoid doubled relative paths like 'dir\\dir\\file.bat'.
    - Windows: run the .bat with shell=True (cmd.exe) in its own directory.
    - Unix-like: run the .sh with bash in its own directory.
    """
    if not _yn("\nStart the server now?", default=True):
        return

    project_dir_abs = os.path.abspath(project_dir)
    is_windows = (os.name == "nt") or (platform.system().lower().startswith("win"))

    try:
        if is_windows:
            bat_abs = os.path.join(project_dir_abs, "start_server.bat")
            if not os.path.isfile(bat_abs):
                raise FileNotFoundError(bat_abs)
            print(f"Launching: {bat_abs}")
            # shell=True lets Windows execute the .bat directly via cmd.exe
            subprocess.run(f'"{bat_abs}"', shell=True, cwd=project_dir_abs, check=True)
        else:
            sh_abs = os.path.join(project_dir_abs, "start_server.sh")
            if not os.path.isfile(sh_abs):
                raise FileNotFoundError(sh_abs)
            print(f"Launching: {sh_abs}")
            subprocess.run(["bash", sh_abs], cwd=project_dir_abs, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error starting server: {e}")
    except Exception as e:
        print(f"Unable to start server: {e}")


if __name__ == "__main__":
    main()
