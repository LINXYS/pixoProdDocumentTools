FROM python:3.12-slim-bookworm

ARG PYTORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG TORCH_VERSION=2.10.0
ARG TORCHVISION_VERSION=0.25.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ROOT=/app \
    PIXO_DOCKER=1 \
    PIXO_ACCELERATOR=cpu

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        git \
        libgl1 \
        libglib2.0-0 \
        libmagic1 \
        libpq5 \
        poppler-utils \
        postgresql-client \
        tesseract-ocr \
        tesseract-ocr-deu \
        tesseract-ocr-eng \
        unrar-free \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install --index-url "${PYTORCH_INDEX_URL}" \
        "torch==${TORCH_VERSION}" \
        "torchvision==${TORCHVISION_VERSION}" \
    && sed '/^torch==/d; /^torchvision==/d; /^pywin32==/d; /^python-magic-bin==/d; /^pyreadline3==/d' \
        /app/requirements.txt > /tmp/requirements-docker.txt \
    && python -m pip install -r /tmp/requirements-docker.txt

COPY . /app

ENTRYPOINT ["python", "/app/docker_entrypoint.py"]
CMD ["schedule"]
