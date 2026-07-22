# Client network contract for server 0.1.1

This document defines the HTTP behaviour that the future client adapter should implement around `histra_runner.JobRunner`.

## 1. Register or refresh the worker

```http
POST /api/v1/workers/register
Content-Type: application/json
```

```json
{
  "name": "mauricio-desktop",
  "max_parallel_jobs": 4,
  "worker_version": "0.2.0",
  "solver_version": "2024.1.1",
  "metadata": {}
}
```

Registration is idempotent by worker name. Persist the returned worker ID locally.

## 2. Claim work

```http
POST /api/v1/jobs/claim
Content-Type: application/json
```

```json
{"worker_id": "..."}
```

- HTTP 200: one job has been leased.
- HTTP 204: no queued job or no free worker slot.

The response contains the attempt-specific URLs. Do not construct alternate URLs when the provided ones can be resolved against the configured server base URL.

## 3. Download and stage the package

Download `package_url` before the lease expires. The ZIP contains:

```text
job.json
<model.path, normally model.hrx>
```

The server adds these fields to the attempt copy of `job.json`:

```json
{
  "attempt_id": "SERVER_ATTEMPT_ID",
  "model": {
    "path": "model.hrx",
    "sha256": "..."
  }
}
```

This makes the local runner use the same attempt ID as the server and verify the HRX checksum.

## 4. Run locally

After extraction:

```python
from histra_runner import JobRunner, load_runner_config

config = load_runner_config("runner.toml")
outcome = JobRunner(config).run_job_file("downloaded/job.json")
```

Preserve the complete attempt workspace until the server accepts the upload.

## 5. Heartbeats

Send a heartbeat before the current lease expires. A 60-second cadence is appropriate with the default 300-second lease.

Allowed status values:

```text
leased
downloading
running
extracting
uploading
validating
```

Example:

```json
{
  "status": "running",
  "progress": {
    "analysis": "LiveLoad_1"
  }
}
```

A successful heartbeat returns the new `lease_expires_at`.

## 6. Upload success

POST multipart form data to `results_url`:

- `results_file`: runner `output/results.json`;
- `run_file`: runner `output/run.json`;
- `validation_file`: optional;
- `solver_log`: optional;
- `extractor_log`: optional.

The server accepts the result only when both JSON files contain the claimed `job_id` and server `attempt_id`, and `run.json.status` is `completed`.

After HTTP 200, the client may apply its configured raw-output cleanup policy. If the upload times out before acknowledgement, retry the same upload; do not rerun HiStrA.

## 7. Report failure

When the local runner fails, POST JSON to `failure_url`:

```json
{
  "reason": "Solver process exited with code 1",
  "retryable": true,
  "exit_code": 1,
  "run": {},
  "validation": {}
}
```

The server requeues retryable failures while `attempt_count < max_attempts`.

## 8. Recovery rules

- A lost connection does not require stopping the solver.
- Continue running locally and resume heartbeats/uploads when connectivity returns, provided the lease has not been reassigned.
- HTTP 409 means the attempt is no longer current. Preserve the workspace for diagnosis and do not upload it under a different attempt.
- HTTP 204 from claim is normal; wait before polling again.
- Never rerun a completed local attempt merely because the result acknowledgement was lost.
