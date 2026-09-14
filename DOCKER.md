# Docker Deployment

This deployment is CPU-first and VM-friendly by default. One shared image is built, then each project runs as its own scheduled ingestion container.

## Default CPU Deployment

Create a runtime env file on a fresh VM:

```bash
cp .env.docker.example .env
```

If `.env` already exists, keep it and add or adjust the Docker values from `.env.docker.example`. Fill in `POSTGRES_PASSWORD` and any API keys needed by the configured embedding provider.

Start Postgres and all scheduled project workers:

```bash
docker compose up -d --build
```

The default workers run `schedule` mode. They run ingestion on startup, then repeat every `INGESTION_INTERVAL_SECONDS` seconds. Existing `ingestion_state.json` files are mounted with each project directory, so unchanged files are skipped by the current incremental ingestion logic.

## Manual Ingestion

Run a one-off ingestion for a project:

```bash
docker compose run --rm site-worker ingest
docker compose run --rm sciarc-worker ingest
```

If the matching scheduled worker is already running, stop it first to avoid overlapping ingestion:

```bash
docker compose stop site-worker
```

## Dev Web UI

The Flask upload UI is dev-only and is behind the `dev` profile:

```bash
docker compose --profile dev up -d site-web
```

Default dev UI ports:

- `produkte-web`: http://localhost:5001
- `site-web`: http://localhost:5002
- `ftp-web`: http://localhost:5003
- `sciarc-web`: http://localhost:5004

Use the token from the mounted project `server_token.txt`, or check the service logs if an ephemeral token was generated:

```bash
docker compose logs site-web
```

## Optional GPU Deployment

The default stack forces CPU mode, even on machines that have a GPU. To use NVIDIA GPU acceleration, install the NVIDIA Container Toolkit on the VM, then start with the GPU override:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

The override switches `PIXO_ACCELERATOR=auto`, requests all GPUs, and builds PyTorch from the CUDA 12.8 wheel index. If CUDA is not visible inside the container, the application falls back to CPU.

## Useful Commands

View worker logs:

```bash
docker compose logs -f sciarc-worker
```

Restart one project:

```bash
docker compose restart sciarc-worker
```

Stop everything:

```bash
docker compose down
```

Stop everything and remove the local Postgres volume:

```bash
docker compose down -v
```
