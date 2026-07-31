FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN useradd --create-home --uid 10001 histra
WORKDIR /build

COPY histra-job-builder /build/histra-job-builder
COPY histra-job-server /build/histra-job-server
RUN python -m pip install --upgrade pip \
    && python -m pip install "/build/histra-job-builder" "/build/histra-job-server[postgres]" \
    && rm -rf /build

RUN mkdir -p /data/templates /tmp/histra-packages \
    && chown -R histra:histra /data /tmp/histra-packages
USER histra
WORKDIR /home/histra
EXPOSE 8000
CMD ["histra-server", "--host", "0.0.0.0", "--port", "8000"]
