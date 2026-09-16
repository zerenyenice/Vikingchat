# OpenViking context database server, installed from PyPI.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OPENVIKING_CONFIG_FILE=/config/ov.conf

RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl libmagic1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ARG OPENVIKING_VERSION=0.4.20
RUN pip install "openviking==${OPENVIKING_VERSION}"

COPY entrypoint.py /usr/local/bin/openviking-entrypoint.py
RUN mkdir -p /config /data

EXPOSE 1933
VOLUME ["/data", "/config"]

HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=10 \
    CMD curl -fsS http://127.0.0.1:1933/health || exit 1

ENTRYPOINT ["python", "/usr/local/bin/openviking-entrypoint.py"]
