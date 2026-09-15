# Deploy SciArc on a DigitalOcean VM

This guide deploys only the `sciarc-pilot-v1` ingestion worker from the generic external-database Compose file. It does not start a Postgres container; it uses your existing Postgres/pgvector databases via `DATABASE_URL` and `RECORD_MANAGER_DATABASE_URL`.

## 1. Create the VM

Create an Ubuntu 24.04 LTS droplet on DigitalOcean. A CPU droplet is enough for the default deployment. Choose a GPU droplet only if you plan to run the optional GPU Compose override.

Recommended starting size for CPU ingestion:

- 4 vCPU
- 8 GB RAM minimum
- 16 GB RAM preferred for larger PDFs
- enough disk for `sciarc-pilot-v1/files`

## 2. SSH Into the VM

```bash
ssh root@YOUR_DROPLET_IP
```

Create an app directory:

```bash
mkdir -p /opt/pixo
cd /opt/pixo
```

## 3. Install Docker and Compose

Install Docker Engine and the Compose plugin from Docker's official apt repository:

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
docker compose version
```

## 4. Put the Project on the VM

From your local machine, copy or clone this repo into `/opt/pixo/pixoProdDocumentTools`.

Example using `rsync`:

```bash
rsync -av --exclude venv --exclude .git ./ root@YOUR_DROPLET_IP:/opt/pixo/pixoProdDocumentTools/
```

Then on the VM:

```bash
cd /opt/pixo/pixoProdDocumentTools
```

## 5. Configure Existing Postgres

Create the external-database runtime env file:

```bash
cp .env.external-db.example .env
nano .env
```

Set these to your existing database values:

```bash
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/VECTOR_DB
RECORD_MANAGER_DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/RECORD_MANAGER_DB
OPENAI_API_KEY=YOUR_KEY
PIXO_ACCELERATOR=cpu
RUN_ON_START=true
INGESTION_INTERVAL_SECONDS=3600
```

The existing vector database must have the `vector` extension enabled:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

The app creates the vector table and record-manager schema itself when ingestion starts.

## 6. Check SciArc Project Files

Make sure this exists:

```bash
ls sciarc-pilot-v1/main.py
```

Make sure `sciarc-pilot-v1/config.yaml` exists and has a SciArc-specific collection name. If it does not exist, create it:

```bash
nano sciarc-pilot-v1/config.yaml
```

Minimal config for local/manual file pickup only:

```yaml
chunk_size: 1000
chunk_overlap: 200
use_chunking: true
embedding_provider: openai
embedding_model: text-embedding-3-large
vector_size: null
llm_provider: openai
collection_name: sciarc-pilot-v1
ingestion:
  urls: []
```

For automatic FTP/FTPS/SFTP pickup, add the existing `ingestion.remote_source` config. This is generic and works for every project because `main.py` already calls `sync_remote_to_local(...)` whenever `remote_source` is configured.

```yaml
chunk_size: 1000
chunk_overlap: 200
use_chunking: true
embedding_provider: openai
embedding_model: text-embedding-3-large
vector_size: null
llm_provider: openai
collection_name: sciarc-pilot-v1
ingestion:
  urls: []
  remote_source:
    protocol: ftp
    host: ftp.example.com
    port: 21
    username: ftp_user
    password: ftp_password
    remote_path: /path/to/sciarc/files
    passive: true
    recursive: true
```

Use `protocol: ftps` for FTPS, or `protocol: sftp` and `port: 22` for SFTP. The Docker container mounts the project folder, so it reads the same `config.yaml` that the existing local scripts and web UI use. If you configure FTP through the dev web UI, it writes this same `remote_source` block.

Files downloaded from FTP/SFTP are stored under:

```bash
sciarc-pilot-v1/files/
```

## 7. Build and Start Ingestion Immediately

Start only the SciArc worker:

```bash
docker compose -f docker-compose.external-db.yml up -d --build sciarc-worker
```

Because `RUN_ON_START=true`, it immediately runs ingestion once. If `remote_source` is configured, each run first syncs the FTP/SFTP directory into `sciarc-pilot-v1/files/`, then ingests new or changed files. After that it keeps running and repeats every `INGESTION_INTERVAL_SECONDS`.

## 8. Watch Logs

```bash
docker compose -f docker-compose.external-db.yml logs -f sciarc-worker
```

Look for:

```text
Mode: schedule
Accelerator setting: cpu
Starting ingestion for sciarc-pilot-v1
Ingestion complete.
Next ingestion check in 3600 seconds.
```

## 9. Add More Files Later

If you are not using FTP/SFTP, copy new files into:

```bash
sciarc-pilot-v1/files/
```

If `remote_source` is configured, put new files on the remote server instead; the next scheduled run downloads and ingests them. To trigger ingestion immediately:

```bash
docker compose -f docker-compose.external-db.yml run --rm sciarc-worker ingest
```

If the scheduled worker is already running, stop it first to avoid overlap:

```bash
docker compose -f docker-compose.external-db.yml stop sciarc-worker
docker compose -f docker-compose.external-db.yml run --rm sciarc-worker ingest
docker compose -f docker-compose.external-db.yml up -d sciarc-worker
```

## 10. Optional GPU Mode

Only use this on a VM with NVIDIA drivers and NVIDIA Container Toolkit installed.

```bash
docker compose -f docker-compose.external-db.yml -f docker-compose.external-db.gpu.yml up -d --build sciarc-worker
```

This switches `PIXO_ACCELERATOR=auto`. If CUDA is not visible in the container, the app falls back to CPU.

## 11. Stop or Restart

Restart:

```bash
docker compose -f docker-compose.external-db.yml restart sciarc-worker
```

Stop:

```bash
docker compose -f docker-compose.external-db.yml down
```
