from __future__ import annotations

import argparse
import logging
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse


APP_ROOT = Path(os.getenv("APP_ROOT", "/app")).resolve()
DEFAULT_INTERVAL_SECONDS = 3600


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _positive_int(value: str | None, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except ValueError:
        logging.warning("Invalid integer value %r; using %s.", value, default)
        return default
    if parsed <= 0:
        logging.warning("Non-positive integer value %r; using %s.", value, default)
        return default
    return parsed


def configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        stream=sys.stdout,
    )


def resolve_project_dir(project: str | None) -> Path:
    project_name = project or os.getenv("PROJECT_DIR")
    if not project_name:
        raise SystemExit("PROJECT_DIR is required, or pass --project.")

    project_path = Path(project_name)
    if not project_path.is_absolute():
        project_path = APP_ROOT / project_path

    project_path = project_path.resolve()
    if not project_path.exists():
        raise SystemExit(f"Project directory does not exist: {project_path}")

    if not (project_path / "main.py").exists():
        raise SystemExit(f"Project directory is missing main.py: {project_path}")

    return project_path


def project_argument(project_path: Path) -> str:
    try:
        return project_path.relative_to(APP_ROOT).as_posix()
    except ValueError:
        return str(project_path)


def runtime_env(project_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")

    accelerator = (env.get("PIXO_ACCELERATOR") or "cpu").strip().lower()
    if accelerator == "gpu":
        accelerator = "cuda"
    if accelerator not in {"cpu", "auto", "cuda", "mps"}:
        logging.warning("Unknown PIXO_ACCELERATOR=%r; using cpu.", accelerator)
        accelerator = "cpu"
    env["PIXO_ACCELERATOR"] = accelerator
    os.environ["PIXO_ACCELERATOR"] = accelerator

    # CPU is the default deployment mode. Hide GPUs unless the container was
    # explicitly started with PIXO_ACCELERATOR=auto/cuda/mps.
    if accelerator == "cpu":
        env["CUDA_VISIBLE_DEVICES"] = ""
        os.environ["CUDA_VISIBLE_DEVICES"] = ""

    python_paths = [str(project_path), str(APP_ROOT)]
    if env.get("PYTHONPATH"):
        python_paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_paths)

    return env


def log_runtime(project_path: Path, mode: str, env: dict[str, str]) -> None:
    logging.info("Mode: %s", mode)
    logging.info("Project: %s", project_path)
    logging.info("Accelerator setting: %s", env.get("PIXO_ACCELERATOR", "cpu"))

    try:
        import torch

        logging.info(
            "Torch: version=%s cuda_available=%s cuda_devices=%s cuda_visible_devices=%r",
            getattr(torch, "__version__", "?"),
            bool(torch.cuda.is_available()),
            torch.cuda.device_count() if torch.cuda.is_available() else 0,
            env.get("CUDA_VISIBLE_DEVICES"),
        )
    except Exception as exc:
        logging.info("Torch runtime check unavailable: %s", exc)


def _db_endpoint(raw_url: str | None) -> tuple[str, int] | None:
    if not raw_url:
        return None
    try:
        parsed = urlparse(raw_url)
    except Exception:
        return None
    if not parsed.hostname:
        return None
    return parsed.hostname, parsed.port or 5432


def wait_for_database(env: dict[str, str]) -> None:
    timeout = _positive_int(env.get("DB_WAIT_TIMEOUT_SECONDS"), 120)
    endpoints = {
        endpoint
        for endpoint in (
            _db_endpoint(env.get("DATABASE_URL")),
            _db_endpoint(env.get("RECORD_MANAGER_DATABASE_URL")),
        )
        if endpoint is not None
    }
    if not endpoints or timeout <= 0:
        return

    deadline = time.monotonic() + timeout
    pending = set(endpoints)
    logged_wait = False

    while pending:
        for host, port in list(pending):
            try:
                with socket.create_connection((host, port), timeout=5):
                    pending.remove((host, port))
                    logging.info("Database endpoint is reachable: %s:%s", host, port)
            except OSError:
                pass

        if not pending:
            return

        if time.monotonic() >= deadline:
            pending_text = ", ".join(f"{host}:{port}" for host, port in sorted(pending))
            raise SystemExit(f"Timed out waiting for database endpoint(s): {pending_text}")

        if not logged_wait:
            pending_text = ", ".join(f"{host}:{port}" for host, port in sorted(pending))
            logging.info("Waiting for database endpoint(s): %s", pending_text)
            logged_wait = True
        time.sleep(2)


def run_ingestion(project_path: Path, env: dict[str, str]) -> int:
    wait_for_database(env)
    started = time.monotonic()
    logging.info("Starting ingestion for %s.", project_path.name)
    result = subprocess.run(
        [sys.executable, "-u", str(project_path / "main.py")],
        cwd=str(project_path),
        env=env,
        check=False,
    )
    elapsed = time.monotonic() - started
    logging.info(
        "Ingestion finished for %s with exit code %s in %.1fs.",
        project_path.name,
        result.returncode,
        elapsed,
    )
    return result.returncode


def run_schedule(project_path: Path, env: dict[str, str]) -> int:
    interval = _positive_int(env.get("INGESTION_INTERVAL_SECONDS"), DEFAULT_INTERVAL_SECONDS)
    run_on_start = _truthy(env.get("RUN_ON_START"), default=True)
    stop_on_failure = _truthy(env.get("STOP_ON_INGESTION_ERROR"), default=False)

    logging.info(
        "Scheduler started: interval=%ss run_on_start=%s stop_on_failure=%s",
        interval,
        run_on_start,
        stop_on_failure,
    )

    if run_on_start:
        code = run_ingestion(project_path, env)
        if code != 0 and stop_on_failure:
            return code

    while True:
        logging.info("Next ingestion check in %s seconds.", interval)
        time.sleep(interval)
        code = run_ingestion(project_path, env)
        if code != 0 and stop_on_failure:
            return code


def read_server_token(project_path: Path, env: dict[str, str]) -> str:
    if env.get("SERVER_TOKEN"):
        return env["SERVER_TOKEN"]

    token_path = project_path / "server_token.txt"
    if token_path.exists():
        token = token_path.read_text(encoding="utf-8").strip()
        if token:
            return token

    token = secrets.token_urlsafe(16)
    logging.warning(
        "No SERVER_TOKEN or server_token.txt found for %s; generated an ephemeral dev token.",
        project_path.name,
    )
    return token


def run_server(project_path: Path, env: dict[str, str]) -> int:
    wait_for_database(env)
    sys.path.insert(0, str(project_path))
    sys.path.insert(0, str(APP_ROOT))
    os.chdir(str(APP_ROOT))

    from web.server import run_server as flask_run_server

    host = env.get("WEB_HOST", "0.0.0.0")
    port = _positive_int(env.get("WEB_PORT"), 5000)
    token = read_server_token(project_path, env)
    flask_run_server(token, project_argument(project_path), host=host, port=port)
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    default_mode = os.getenv("PIXO_MODE", "schedule")
    parser = argparse.ArgumentParser(description="Docker entrypoint for pixo document tools.")
    parser.add_argument(
        "mode",
        nargs="?",
        default=default_mode,
        choices=("schedule", "ingest", "server"),
        help="Run scheduled ingestion, one manual ingestion, or the dev web UI.",
    )
    parser.add_argument("--project", help="Project directory name or absolute path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    argv = list(sys.argv[1:] if argv is None else argv)

    # Allow docker run image <arbitrary command> for debugging.
    if argv and argv[0] not in {"schedule", "ingest", "server"} and not argv[0].startswith("-"):
        os.execvp(argv[0], argv)

    args = parse_args(argv)
    project_path = resolve_project_dir(args.project)
    env = runtime_env(project_path)
    log_runtime(project_path, args.mode, env)

    if args.mode == "schedule":
        return run_schedule(project_path, env)
    if args.mode == "ingest":
        return run_ingestion(project_path, env)
    if args.mode == "server":
        return run_server(project_path, env)

    raise AssertionError(f"Unhandled mode: {args.mode}")


if __name__ == "__main__":
    raise SystemExit(main())
