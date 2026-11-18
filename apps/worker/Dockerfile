FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv \
  && apt-get update \
  && apt-get install -y --no-install-recommends curl \
  && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
COPY packages ./packages
COPY apps/worker ./apps/worker
COPY config ./config

RUN uv sync --package yonder-worker --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    YONDER_CONFIG_FILE=/app/config/environments/development.yaml \
    PYTHONUNBUFFERED=1

EXPOSE 8090
CMD ["yonder-worker"]
