# HiStrA Job Server

A containerised FastAPI and PostgreSQL service for distributing HiStrA analyses to trusted worker computers.

Version `0.1.1` implements the complete server-side workflow without scenario generation and without authentication. A job is inserted by uploading an existing `job.json` and `.hrx`; a worker registers, claims the job, downloads a ZIP package, sends heartbeats, and uploads the runner outputs.

## Architecture

```text
GitHub release
      │ builds/publishes
      ▼
GHCR image ───────────────► VPS: FastAPI container
                                  │
                       ┌──────────┴──────────┐
                       ▼                     ▼
                PostgreSQL container   artifact volume
                jobs/workers/attempts  HRX/ZIP/results/logs
```

FastAPI and PostgreSQL intentionally run in separate containers under Docker Compose. This keeps database persistence and application updates independent.

## Implemented workflow

1. Upload `job.json` and the matching HRX model.
2. Store the job in PostgreSQL and the input files in persistent artifact storage.
3. Register a worker with a configurable local capacity.
4. Atomically lease the next queued job.
5. Build and download an attempt-specific `package.zip`, containing the original names `job.json` and `model.hrx`. The server injects the server `attempt_id` and HRX SHA-256 into the attempt manifest.
6. Extend the lease while the worker reports `downloading`, `running`, `extracting`, `uploading`, or `validating`.
7. Upload `results.json`, `run.json`, optional `validation.json`, and logs.
8. Store the JSON in PostgreSQL and preserve the original files in artifact storage.
9. Requeue interrupted jobs after their leases expire, up to `max_attempts`.

The package is compatible with the local runner contract from `histra-job-runner 0.2.0`.

## Important security state

There is **no application authentication in version 0.1.1**. Every endpoint is open to anyone who can reach the API port. Until token authentication is added, restrict access with the VPS firewall or an IP allowlist. Do not expose this version to untrusted internet traffic.

## Quick start

Requirements:

- Docker Engine
- Docker Compose v2

Create the environment file:

```bash
cp .env.example .env
```

Change `POSTGRES_PASSWORD`. For this first deployment, use a long URL-safe value containing letters, numbers, hyphens, and underscores. Docker Compose constructs `DATABASE_URL` from the PostgreSQL variables.

Start the stack:

```bash
docker compose up -d --build
python scripts/wait_for_api.py http://127.0.0.1:8000/health/ready
```

Open the interactive API documentation:

```text
http://SERVER-IP:8000/docs
```

Check the services:

```bash
docker compose ps
docker compose logs -f api
```

Database migrations run automatically whenever the API container starts.

## Insert a job manually

The server does not generate scenarios yet. Upload the runner-compatible files directly:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/jobs \
  -F 'job_file=@examples/job.json;type=application/json' \
  -F 'model_file=@model.hrx;type=application/octet-stream' \
  -F 'priority=0' \
  -F 'max_attempts=3'
```

The uploaded HRX filename must match `model.path` in `job.json`. The supplied example expects `model.hrx`.

Job identifiers are taken from `job.json`. Duplicate IDs are rejected rather than overwritten.

## Simulate the future worker workflow

Register a worker:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/workers/register \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "mauricio-desktop",
    "max_parallel_jobs": 4,
    "worker_version": "0.2.0"
  }'
```

Use the returned worker `id` to claim a job:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/jobs/claim \
  -H 'Content-Type: application/json' \
  -d '{"worker_id":"WORKER_ID"}'
```

A successful response contains:

```json
{
  "job_id": "bridge-001-scour-sequence",
  "attempt_id": "...",
  "lease_expires_at": "...",
  "package_url": "/api/v1/jobs/.../attempts/.../package",
  "heartbeat_url": "/api/v1/jobs/.../attempts/.../heartbeat",
  "results_url": "/api/v1/jobs/.../attempts/.../results",
  "failure_url": "/api/v1/jobs/.../attempts/.../failed"
}
```

When no job or no local worker slot is available, the claim endpoint returns HTTP `204 No Content`.

Download the package:

```bash
curl -o job-package.zip \
  http://127.0.0.1:8000/api/v1/jobs/JOB_ID/attempts/ATTEMPT_ID/package
```

Send a heartbeat:

```bash
curl -X POST \
  http://127.0.0.1:8000/api/v1/jobs/JOB_ID/attempts/ATTEMPT_ID/heartbeat \
  -H 'Content-Type: application/json' \
  -d '{
    "status": "running",
    "progress": {"analysis": "LiveLoad_1"}
  }'
```

The runner outputs must contain the same `job_id` and server `attempt_id`; `run.json.status` must be `completed`.

Upload a completed runner attempt:

```bash
curl -X POST \
  http://127.0.0.1:8000/api/v1/jobs/JOB_ID/attempts/ATTEMPT_ID/results \
  -F 'results_file=@results.json;type=application/json' \
  -F 'run_file=@run.json;type=application/json' \
  -F 'validation_file=@validation.json;type=application/json' \
  -F 'solver_log=@solver.log;type=text/plain' \
  -F 'extractor_log=@extractor.log;type=text/plain'
```

Report a failure:

```bash
curl -X POST \
  http://127.0.0.1:8000/api/v1/jobs/JOB_ID/attempts/ATTEMPT_ID/failed \
  -H 'Content-Type: application/json' \
  -d '{
    "reason": "Solver process exited with code 1",
    "retryable": true,
    "exit_code": 1
  }'
```

## Main endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health/live` | Process health |
| `GET` | `/health/ready` | Process and database readiness |
| `POST` | `/api/v1/jobs` | Upload `job.json` and HRX |
| `GET` | `/api/v1/jobs` | List jobs |
| `GET` | `/api/v1/jobs/{job_id}` | Job and attempt details |
| `POST` | `/api/v1/jobs/{job_id}/retry` | Requeue a failed/cancelled job |
| `POST` | `/api/v1/jobs/{job_id}/cancel` | Cancel a job |
| `GET` | `/api/v1/jobs/{job_id}/results` | Return the accepted result JSON |
| `GET` | `/api/v1/jobs/{job_id}/artifacts` | List stored input/output artifacts |
| `POST` | `/api/v1/workers/register` | Register or refresh a worker |
| `POST` | `/api/v1/workers/{worker_id}/enable` | Enable a worker |
| `POST` | `/api/v1/workers/{worker_id}/disable` | Stop new claims for a worker |
| `POST` | `/api/v1/jobs/claim` | Atomically lease a job |
| `GET` | `/api/v1/jobs/{job_id}/attempts/{attempt_id}/package` | Download worker package |
| `POST` | `/api/v1/jobs/{job_id}/attempts/{attempt_id}/heartbeat` | Extend lease and report stage |
| `POST` | `/api/v1/jobs/{job_id}/attempts/{attempt_id}/results` | Upload accepted outputs |
| `POST` | `/api/v1/jobs/{job_id}/attempts/{attempt_id}/failed` | Report a failed attempt |

The complete and always-current request schema is available in `/docs` and `/openapi.json`.

## Persistent data

Docker Compose creates two named volumes:

```text
postgres_data   PostgreSQL data
artifact_data   HRX files, packages, result JSON and logs
```

Artifact layout:

```text
/data/jobs/<job_id>/
├── input/
│   ├── job.json
│   ├── model.hrx
│   └── package.zip
└── attempts/<attempt_id>/
    ├── input/
    │   ├── job.json       # includes server attempt_id and model SHA-256
    │   └── package.zip
    ├── results/
    │   ├── results.json
    │   ├── run.json
    │   └── validation.json
    └── logs/
        ├── solver.log
        └── extractor.log
```

Removing the API image does not remove either volume. Do not run `docker compose down -v` unless you intend to delete all server data.

## Build and publish with GitHub

The repository contains two workflows:

- `CI`: lint, test, and build the container on pushes and pull requests.
- `Publish container image`: publish to GitHub Container Registry when a GitHub Release is published or the workflow is started manually.

The published image name is:

```text
ghcr.io/OWNER/REPOSITORY
```

For example, after creating release `v0.1.1`, the workflow publishes semver, SHA, and `latest` tags.

On the VPS, set this in `.env`:

```dotenv
HISTRA_SERVER_IMAGE=ghcr.io/OWNER/REPOSITORY:latest
```

For a public package:

```bash
./scripts/deploy.sh
```

For a private package, log in once before deployment:

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u GITHUB_USERNAME --password-stdin
./scripts/deploy.sh
```

The deployment script pulls the new API image and recreates only what changed. PostgreSQL and artifact volumes are retained.

## Development without Docker

Create a virtual environment and install the package:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

For a quick SQLite development instance:

```bash
export DATABASE_URL=sqlite:///./histra-server.sqlite3
export STORAGE_ROOT=./data
alembic upgrade head
uvicorn histra_server.main:app --reload
```

Run checks:

```bash
ruff check .
pytest
```

## Decisions intentionally deferred

- token authentication and endpoint roles;
- HTTPS/reverse-proxy configuration;
- server-side scenario and HRX generation;
- worker software updates;
- normalised scientific result tables and data-science exports;
- dashboard/user interface.

These can be added without replacing the job, attempt, lease, artifact, and worker model implemented here.

The exact adapter workflow for the next client-side step is documented in [`docs/client-contract.md`](docs/client-contract.md).
