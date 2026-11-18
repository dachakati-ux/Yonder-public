FROM python:3.12-slim
WORKDIR /srv/controller
RUN apt-get update && apt-get install -y --no-install-recommends docker.io \
    && rm -rf /var/lib/apt/lists/*
COPY apps/addon-controller/requirements.txt /srv/controller/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/addon-controller/controller /srv/controller/controller
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/srv/controller
EXPOSE 8110
CMD ["uvicorn", "controller.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8110", "--workers", "1"]
