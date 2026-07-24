# JOB-only execution architecture

The authoritative persistent input is the canonical JOB JSON. HRX files are
compiled on demand by `histra-job-builder`, placed in a disposable attempt ZIP,
and may be cached only in `PACKAGE_CACHE_ROOT`.

New jobs are submitted as JSON to `POST /api/v2/jobs`. The legacy multipart
endpoint returns HTTP 410 so no new HRX can enter persistent server storage.
Legacy rows that already reference stored HRX packages remain executable during
migration.

A package contains `job.json`, the generated HRX and `manifest.json`. The
manifest binds the immutable JOB hash, attempt, builder version and generated
HRX hash. Cache deletion or a server restart is safe because the package can be
rebuilt from the stored JOB.
