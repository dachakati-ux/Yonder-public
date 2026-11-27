FROM python:3.12-slim
WORKDIR /srv/controller
RUN apt-get update && apt-get install -y --no-install-recommends docker.io \
    && rm -rf /var/lib/apt/lists/*
COPY apps/addon-controller/requirements.lock.txt /srv/controller/requirements.lock.txt
RUN pip install --no-cache-dir --require-hashes -r requirements.lock.txt
COPY apps/addon-controller/controller /srv/controller/controller
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/srv/controller
EXPOSE 8110
CMD ["uvicorn", "controller.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8110", "--workers", "1"]
