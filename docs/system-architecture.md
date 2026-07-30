# HiStrA distributed JOB system 1.0

## Decision

Builder remains a separate Python package and test boundary, but not a separate
network service. Server imports Builder and deploys it in the same process.
Runner remains a separate client application.

This gives one authoritative control plane and removes an unnecessary internal
HTTP hop while preserving compiler isolation:

```text
JOB authoring process
       |
       | POST /jobs (canonical JSON)
       v
+------------------------------+
| Server                       |
| PostgreSQL                   |
| - JOB document + SHA-256     |
| - queue and attempts         |
| - leases and heartbeats      |
| - results and provenance     |
+---------------+--------------+
                |
                | compile_job(job)
                v
+------------------------------+
| Builder Python package       |
| - validate JOB 1.0           |
| - verify source template     |
| - apply deterministic edits  |
| - produce HRX + provenance   |
+---------------+--------------+
                |
                | temporary ZIP cache
                v
+------------------------------+
| Runner                       |
| - secure package extraction  |
| - identity/hash validation   |
| - workflow execution adapter |
| - result/failure upload      |
+------------------------------+
```

## Why there is no `/api/v2`

This is the first intended production contract. No deployed consumer depends on
an earlier route, so versioning an unused experimental route would preserve
history rather than provide value. Routes are unversioned and the release is
`1.0.0`. Two contracts still carry explicit versions where compatibility
matters:

- JOB schema: `schema_version: "1.0"`;
- runner package: `protocol_version: "1.0"`.

A future breaking protocol change can introduce schema/protocol 2.0 and, only if
necessary, a new HTTP media type or route namespace.

## Persistent and temporary data

Persistent:

- canonical JOB JSON;
- canonical JOB SHA-256;
- source template ID and SHA-256 as part of the JOB;
- runner registrations;
- attempt identity, status, sequence, and lease;
- Builder version, template hash, and generated HRX hash;
- result JSON, run metadata, failures, and logs.

Temporary:

- generated HRX bytes;
- attempt ZIP containing `manifest.json`, `job.json`, and the HRX;
- Runner workspace.

Source templates are persistent deployment assets, not generated per-JOB HRX
artifacts. Imported HRX models may contain information that cannot be represented
losslessly in a small JSON document, so one immutable template is retained and
many small JOBs reference it by digest.

## JOB authoring

Randomness happens before submission, in the user's authoring process. The JOB
stores the sampled parameters and preferably also the seed, generator name, and
generator version. Builder does not make hidden random choices.

For a slight modification, store the final changed value in `model.patches` and
optionally describe its parent/mutation in metadata. Replaying the same JOB with
the same source template and Builder version produces the same HRX digest.

For a completely generated model family, add a domain compiler package behind
the Builder boundary. Its input still must be fully represented in canonical
JOB JSON and its output must produce the same provenance fields. Do not place
model generation logic in Server routes or Runner code.

## Claim and lease transaction

1. Runner registers.
2. Runner requests one claim.
3. Server selects the highest-priority oldest queued JOB.
4. Server creates a `building` attempt.
5. Builder validates the JOB and source template and produces HRX bytes.
6. Server creates the three-file ZIP in its TTL cache.
7. Only after successful compilation does Server mark the attempt `leased` and
   start its expiry clock.
8. Runner downloads with its runner identity and heartbeats during execution.
9. Server accepts results only when URL identity, runner identity, JOB hash, and
   HRX hash all match the lease.
10. Server stores the result and deletes the temporary ZIP.

A cache miss during an active lease triggers deterministic regeneration. Server
compares the complete manifest and HRX digest with the original attempt before
serving it. Completed, failed, expired, or cancelled attempts cannot download a
package.

## Retry state machine

```text
queued -> building -> leased -> completed
                    |    |
                    |    +-> expired -> queued (attempts remain)
                    +------> failed  -> queued (until max_attempts)

queued/leased -> cancelled
compile failure -> failed
```

Each retry receives a new attempt ID. JOB identity and canonical hash do not
change. A deterministic build should produce the same HRX hash on every retry.

## Runner adapter boundary

Runner core does not contain proprietary solver behavior. A trusted adapter gets
validated paths for JOB and HRX and must return:

- `results`: analysis outputs;
- `run`: solver version, timings, exit status, and useful provenance;
- `logs`: human-readable execution evidence.

The command adapter expects `results.json` and `run.json`. A private HiStrA
adapter repository can preserve licensed solver integrations and acceptance
fixtures without coupling them to package transport or queue logic.

## Security model

- TLS terminates at a reverse proxy.
- A shared bearer token is supported for the initial isolated deployment.
- Packages are runner-bound.
- ZIP traversal, absolute paths, backslashes, symlinks, duplicates, extra files,
  expanded size limits, and excess file counts are rejected.
- JOB and HRX content are SHA-256 verified before execution.
- XML external entities and network resolution are disabled in Builder.
- Source templates are mounted read-only.
- Generated packages and Runner workspaces are disposable.

Per-runner credentials can replace the shared token later without changing the
JOB or package structures.

## Deployment order

1. Publish/tag Builder `v1.0.0`.
2. Publish/tag Runner `v1.0.0`.
3. Publish/tag Server `v1.0.0`.
4. Put source templates in the mounted registry and calculate their SHA-256.
5. Start PostgreSQL and Server.
6. Start one Runner with a real solver adapter.
7. Submit the included example JOB and inspect the completed attempt.
8. Add real licensed-solver acceptance tests before production analyses.

## Test strategy

Builder tests prove canonical hashes, schema strictness, template integrity,
lossless import, deterministic XML generation, and failure behavior.

Runner tests attack package extraction and provenance, verify both adapter types,
exercise network failures and heartbeats, and confirm workspace cleanup.

Server tests cover queue ordering, idempotency, conflicts, compile-on-claim,
leases, expiry, retries, cancellation, package regeneration, authentication,
result identity, cache deletion, and API health.

The Server suite contains an acceptance test that uses the actual Builder and
Runner packages in one process to prove the complete lifecycle.
