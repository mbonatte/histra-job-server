# HiStrA Job Server

Containerised FastAPI, PostgreSQL and web-dashboard service for distributing and analysing HiStrA numerical jobs on trusted worker computers.

Version `0.2.0` keeps the worker protocol from `0.1.0` and adds an integrated analytical dashboard. It does not change the database schema, so an existing `0.1.0` deployment can be upgraded by replacing the API image.

## Architecture

```text
GitHub release
      │ builds and publishes
      ▼
GHCR image ───────────────► VPS: FastAPI container
                                  │
                       ┌──────────┴──────────┐
                       ▼                     ▼
                PostgreSQL container   artifact volume
                jobs/workers/results   HRX/ZIP/results/logs
                       ▲
                       │ database queries
                       ▼
                integrated dashboard
```

FastAPI serves both the worker/admin API and the dashboard. PostgreSQL and artifact storage remain separate persistent Docker volumes.

## Dashboard

Open:

```text
https://YOUR-SERVER/dashboard/
```

The root URL redirects to the dashboard. API documentation remains at `/docs`.

The dashboard includes:

- job, status, completion-rate, duration and throughput summaries;
- queue and job filtering by status, scenario, worker, dates and text search;
- sortable and paginated job tables;
- full job detail with attempts, artifacts, validation and raw JSON;
- reaction histories (`R1`, `R2`, `R3`) against analysis step;
- displacement histories (`Ux`, `Uy`, `Uz`) by model point;
- modal contribution plots;
- scour/interface mutation evidence for every analysis;
- run provenance, solver-stage durations and result-database size;
- worker availability, capacity, versions, success rate and execution duration;
- descriptive statistics across jobs: count, mean, median, standard deviation, quartiles, percentiles and coefficient of variation;
- response statistics for final, peak absolute, minimum and maximum reactions/displacements;
- grouping by scenario, worker, analysis, model point or numeric metadata field;
- histogram and scatter plots;
- CSV export of the selected statistical observations.

All dashboard data is read from the PostgreSQL records already populated by the worker workflow. The browser does not read artifact files directly.

## Worker workflow

1. Upload `job.json` and the matching HRX model.
2. Store the job in PostgreSQL and the files in persistent artifact storage.
3. Register a worker with a configurable capacity.
4. Atomically lease the next queued job.
5. Build and download an attempt-specific package.
6. Extend the lease through worker heartbeats.
7. Upload `results.json`, `run.json`, validation evidence and logs.
8. Store accepted JSON in PostgreSQL and preserve original files in artifact storage.
9. Requeue interrupted jobs after lease expiry, up to `max_attempts`.
10. Display accepted data in the dashboard.

The protocol is compatible with `histra-job-runner 0.3.0`.

## Security state

There is no application authentication in version `0.2.0`. The API and dashboard are visible to anyone who can reach the service. Until authentication is added, restrict access through the reverse proxy, VPS firewall, VPN or an IP allowlist.

## Start a new deployment

Requirements:

- Docker Engine
- Docker Compose v2

Create the environment file:

```bash
cp .env.example .env
```

Set a strong `POSTGRES_PASSWORD`, then start the stack:

```bash
docker compose up -d --build
python scripts/wait_for_api.py http://127.0.0.1:8000/health/ready
```

Useful checks:

```bash
docker compose ps
docker compose logs -f api
```

Database migrations run automatically whenever the API container starts.

## Upgrade an existing 0.1.0 VPS deployment

No database migration is required beyond the existing startup migration command.

After publishing the `0.2.0` image to GHCR:

```bash
./scripts/deploy.sh
```

Or manually:

```bash
docker compose pull api
docker compose up -d --no-deps api
```

Then verify:

```bash
curl https://YOUR-SERVER/health/ready
```

Open `https://YOUR-SERVER/dashboard/`. Existing PostgreSQL and artifact volumes are retained. Do not use `docker compose down -v` unless all stored data should be deleted.

## Environment variables

The existing settings are unchanged. Dashboard-specific settings are:

```dotenv
DASHBOARD_ENABLED=true
DASHBOARD_WORKER_ONLINE_SECONDS=300
```

`DASHBOARD_WORKER_ONLINE_SECONDS` controls how recently a worker must have sent a heartbeat to appear online.

## Insert a job manually

The server does not generate scenarios yet. Upload runner-compatible files directly:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/jobs \
  -F 'job_file=@examples/job.json;type=application/json' \
  -F 'model_file=@model.hrx;type=application/octet-stream' \
  -F 'priority=0' \
  -F 'max_attempts=3'
```

The HRX filename must match `model.path` in `job.json`. Duplicate job IDs are rejected.

## Dashboard API

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/v1/dashboard/summary` | Overview KPIs, status totals and throughput |
| `GET` | `/api/v1/dashboard/catalog` | Available filters, analyses, components, points and metadata fields |
| `GET` | `/api/v1/dashboard/jobs` | Filtered, sortable dashboard job rows |
| `GET` | `/api/v1/dashboard/jobs/{job_id}` | Job, selected attempt, results, run metadata and artifacts |
| `GET` | `/api/v1/dashboard/workers` | Worker availability and performance |
| `GET` | `/api/v1/dashboard/statistics` | Observations, descriptive statistics and grouped summaries |
| `GET` | `/api/v1/dashboard/statistics.csv` | CSV export using the same statistical filters |

Detailed metric definitions and data assumptions are documented in [`docs/dashboard.md`](docs/dashboard.md).

The statistics endpoint supports these metrics:

```text
duration_seconds
reaction_final
reaction_peak_abs
reaction_minimum
reaction_maximum
displacement_final
displacement_peak_abs
displacement_minimum
displacement_maximum
```

## Core job API

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health/live` | Process health |
| `GET` | `/health/ready` | Process and database readiness |
| `POST` | `/api/v1/jobs` | Upload `job.json` and HRX |
| `GET` | `/api/v1/jobs` | List jobs |
| `GET` | `/api/v1/jobs/{job_id}` | Job and attempt details |
| `POST` | `/api/v1/jobs/{job_id}/retry` | Requeue a failed/cancelled job |
| `POST` | `/api/v1/jobs/{job_id}/cancel` | Cancel a job |
| `GET` | `/api/v1/jobs/{job_id}/results` | Return accepted result JSON |
| `GET` | `/api/v1/jobs/{job_id}/artifacts` | List stored artifacts |
| `POST` | `/api/v1/workers/register` | Register or refresh a worker |
| `POST` | `/api/v1/workers/{worker_id}/enable` | Enable a worker |
| `POST` | `/api/v1/workers/{worker_id}/disable` | Stop new claims |
| `POST` | `/api/v1/jobs/claim` | Atomically lease a job |
| `GET` | `/api/v1/jobs/{job_id}/attempts/{attempt_id}/package` | Download worker package |
| `POST` | `/api/v1/jobs/{job_id}/attempts/{attempt_id}/heartbeat` | Extend lease and report stage |
| `POST` | `/api/v1/jobs/{job_id}/attempts/{attempt_id}/results` | Upload accepted outputs |
| `POST` | `/api/v1/jobs/{job_id}/attempts/{attempt_id}/failed` | Report a failed attempt |

The complete request schema is available at `/docs` and `/openapi.json`.

## Persistent data

Docker Compose creates:

```text
postgres_data   PostgreSQL data
artifact_data   HRX files, packages, result JSON and logs
```

Removing or replacing the API image does not remove either volume.

## Build and publish with GitHub

The repository contains:

- `CI`: tests, linting and container build;
- `Publish container image`: GHCR publication on a GitHub Release or manual workflow run.

The published image is:

```text
ghcr.io/OWNER/REPOSITORY
```

Set the VPS `.env` file:

```dotenv
HISTRA_SERVER_IMAGE=ghcr.io/OWNER/REPOSITORY:latest
```

For a private package, log in to GHCR before running `scripts/deploy.sh`.

## Development without Docker

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
export DATABASE_URL=sqlite:///./histra-server.sqlite3
export STORAGE_ROOT=./data
alembic upgrade head
uvicorn histra_server.main:app --reload
```

Run checks:

```bash
ruff check .
pytest
node --check src/histra_server/static/dashboard/app.js
```

## Intentionally deferred

- token authentication and endpoint roles;
- server-side scenario and HRX generation;
- normalised scientific warehouse tables;
- scheduled reports and alerts;
- advanced regression, sensitivity and surrogate-model workflows.
