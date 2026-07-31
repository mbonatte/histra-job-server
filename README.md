# HiStrA Job Server 1.1.0

HiStrA Job Server is the orchestration and authoring control plane for distributed HiStrA analyses.

Version 1.1.0 preserves the canonical v1 runner protocol and restores the two browser workflows required for engineering use:

- **Dashboard** (`/dashboard`) — queue, attempts, runners, provenance, logs, result JSON and automatically discovered numeric result curves.
- **Builder** (`/builder`) — import official `.hrx`, verify byte-exact HRX→JOB→HRX round trips, render stored `Node`/`Quad` geometry, edit canonical JOB JSON, compile/download HRX, create scenario variants and submit batches.

The browser interfaces call authenticated APIs. Enter the same bearer token configured as `HISTRA_API_TOKEN`; it is kept in browser session storage.

## Architecture

```text
Browser dashboard / Builder UI
             │
             ▼
HiStrA Job Server 1.1 ───────── PostgreSQL
             │                       │
             ├── immutable HRX registry
             ├── histra-job-builder 1.1 (in process)
             └── deterministic attempt packages
                                      ▲
                                      │
                                trusted runners
```

Responsibilities:

- `histra-job-builder` owns HRX import, validation, preview extraction, JOB compilation and variant generation.
- `histra-job-server` owns HTTP APIs, jobs, attempts, leases, runners, results, dashboard and the browser authoring workflow.
- `histra-job-runner` remains responsible for executing a claimed package with the selected solver backend.

## Production deployment

Create `.env` from `.env.example`, then:

```bash
docker network inspect proxy >/dev/null 2>&1 || docker network create proxy
docker compose pull
docker compose up -d
docker compose logs -f api
```

Open:

- `https://your-host/dashboard`
- `https://your-host/builder`
- `https://your-host/docs`

The template volume must be writable by the API because importing an official HRX registers it as an immutable template. Existing IDs cannot be overwritten with different bytes.

## Local source build

Clone this repository and `histra-job-builder` as sibling directories:

```text
workspace/
├── histra-job-builder/
└── histra-job-server/
```

Then run:

```bash
docker compose -f compose.build.yaml up --build
```

## Upgrade from v1.0.x

No database schema change is required. Keep the existing PostgreSQL volume, add the persistent template volume, update environment names shown in `.env.example`, pull v1.1.0 and recreate the API container.

Do not use `docker compose down -v` during the upgrade.

## Verification

```bash
curl https://your-host/health/ready
curl -H "Authorization: Bearer $HISTRA_API_TOKEN" https://your-host/api/ui/dashboard/summary
```

For each imported HRX, the Builder UI reports whether the initial no-patch round trip is byte-for-byte exact. A failure is treated as an engineering issue and should be investigated before generating scenarios.
