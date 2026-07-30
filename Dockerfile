FROM python:3.12-slim

WORKDIR /opt/histra
COPY histra-job-builder /src/histra-job-builder
COPY histra-job-server /src/histra-job-server
RUN python -m pip install --no-cache-dir /src/histra-job-builder \
    && python -m pip install --no-cache-dir '/src/histra-job-server[postgres]'

USER 65532:65532
EXPOSE 8000
CMD ["uvicorn", "histra_server.main:app", "--host", "0.0.0.0", "--port", "8000"]
