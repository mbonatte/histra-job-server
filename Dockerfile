FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system histra \
    && useradd --system --gid histra --home-dir /app histra

COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
COPY --chmod=0755 docker/entrypoint.sh ./docker/entrypoint.sh

RUN python -m pip install --upgrade pip \
    && python -m pip install . \
    && mkdir -p /data \
    && chown -R histra:histra /app /data

USER histra

EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)"

ENTRYPOINT ["/app/docker/entrypoint.sh"]
