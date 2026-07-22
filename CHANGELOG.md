# Changelog

## 0.1.0 - 2026-07-22

- Added FastAPI job, worker, attempt, result, and artifact endpoints.
- Added PostgreSQL persistence and Alembic initial migration.
- Added lease expiry and automatic job requeueing.
- Added persistent HRX/package/result/log storage.
- Added Docker Compose deployment and GHCR publishing workflow.
- Added end-to-end API tests.
- Authentication and server-side scenario generation are intentionally deferred.
