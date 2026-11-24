FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
COPY delivery_sync ./delivery_sync
COPY rules ./rules
COPY schema ./schema

RUN pip install . \
    && mkdir -p /data /logs /secrets \
    && chown -R 1000:1000 /data /logs

USER 1000:1000

EXPOSE 8099

HEALTHCHECK --interval=60s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8099/healthz', timeout=4).status == 200 else 1)"

CMD ["delivery-sync", "serve"]
