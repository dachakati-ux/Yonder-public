FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv \
  && apt-get update \
  && apt-get install -y --no-install-recommends curl \
  && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
COPY packages ./packages
COPY apps/gateway ./apps/gateway
COPY config ./config
COPY addons ./addons
COPY database ./database
COPY scripts/docker-gateway-entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh \
  && uv sync --package yonder-gateway --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    YONDER_CONFIG_FILE=/app/config/environments/development.yaml \
    PYTHONUNBUFFERED=1

EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
CMD ["yonder-gateway"]
