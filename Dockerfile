FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/histra

COPY histra-job-builder /src/histra-job-builder
COPY histra-job-server /src/histra-job-server

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir /src/histra-job-builder \
    && python -m pip install --no-cache-dir "/src/histra-job-server[postgres]" \
    && python -m pip check

CMD ["uvicorn", "histra_server.app:app", "--host", "0.0.0.0", "--port", "8000"]