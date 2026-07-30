# histra-job-server

The authoritative control plane for the HiStrA distributed job system.

There is one unversioned API because this is the first production contract.
See [`docs/system-architecture.md`](docs/system-architecture.md) for the complete
three-repository lifecycle and design decisions. The
package version is `1.0.0`; there are no legacy upload routes and no compatibility
layer.

## Responsibilities

The Server persists:

- canonical JOB JSON and its SHA-256;
- queue state, priority, retries, attempts, and leases;
- package provenance: JOB, template, Builder, and generated HRX digests;
- returned results, run metadata, failures, and logs.

The Server does **not** persist generated HRX files. It imports
`histra-job-builder`, compiles an HRX after a runner claims a JOB, and places the
three-file ZIP in a regenerable TTL cache. Successful or failed attempts remove
the cache entry. A missing entry can be regenerated and is accepted only when
its manifest and HRX digest are identical to the original lease.

```text
POST /jobs
    -> database stores canonical JOB

POST /claims
    -> Server invokes Builder in-process
    -> temporary ZIP: manifest.json + job.json + model.hrx
    -> lease begins only after compilation succeeds

POST /jobs/{job}/attempts/{attempt}/results
    -> validate identities and digests
    -> persist result provenance
    -> delete temporary ZIP
```

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/jobs` | Validate and enqueue canonical JOB JSON |
| `GET` | `/jobs` | List jobs; optional `?status=queued` |
| `GET` | `/jobs/{job_id}` | Read a JOB, attempts, and result |
| `POST` | `/jobs/{job_id}/cancel` | Cancel queued or leased work |
| `POST` | `/runners/register` | Register or refresh a runner identity |
| `POST` | `/claims` | Compile and lease the next queued JOB |
| `GET` | `/jobs/{job}/attempts/{attempt}/package` | Download the leased package |
| `POST` | `/jobs/{job}/attempts/{attempt}/heartbeat` | Extend a lease |
| `POST` | `/jobs/{job}/attempts/{attempt}/results` | Commit verified results |
| `POST` | `/jobs/{job}/attempts/{attempt}/failed` | Record failure and retry if allowed |
| `GET` | `/health/live` | Process liveness |
| `GET` | `/health/ready` | Database/template readiness |

`POST /jobs` is idempotent. Repeating the same ID and canonical content returns
the existing JOB; reusing the ID for different content returns `409 Conflict`.
Scheduling properties are server metadata:

```bash
curl -X POST 'http://localhost:8000/jobs?priority=10&max_attempts=3' \
  -H 'Content-Type: application/json' \
  --data @job.json
```

## Package manifest

```json
{
  "protocol_version": "1.0",
  "job_id": "campaign-a-0001",
  "attempt_id": "...",
  "job_sha256": "...",
  "hrx": {
    "path": "models/campaign-a-0001.hrx",
    "sha256": "...",
    "size_bytes": 12345
  },
  "builder": {
    "builder_version": "1.0.0",
    "template_id": "bridge-base-v1",
    "template_sha256": "...",
    "hrx_sha256": "..."
  }
}
```

The Runner must reject any identity or digest mismatch.

## Configuration

See `.env.example`. Set `HISTRA_API_TOKEN` in every non-isolated deployment and
put TLS at the reverse proxy. The bearer token is intentionally simple; a later
security release can add per-runner credentials without changing the JOB model.

For production, use PostgreSQL and mount the template registry read-only. The
package cache may be a local ephemeral volume because it is never authoritative.

## Local development

From a directory containing the Builder and Server repositories as siblings:

```bash
python -m pip install -e ../histra-job-builder
python -m pip install -e '.[test]'
pytest
histra-server --reload
```

## Docker Compose

Run from this repository:

```bash
docker compose up --build
```

The Docker build context is the parent directory so the pinned sibling Builder
package is installed into the Server image. Edit the two source directories in
`Dockerfile` if your checkout names differ.
