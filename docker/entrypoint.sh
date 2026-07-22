#!/bin/sh
set -eu

alembic upgrade head
exec uvicorn histra_server.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --proxy-headers \
  --forwarded-allow-ips='*'
