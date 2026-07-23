# Changelog

## 0.2.0 - 2026-07-23

- Added an integrated responsive dashboard served by FastAPI.
- Added overview, jobs, results explorer, statistics and worker views.
- Added reaction, displacement, modal and scour-mutation visualisations.
- Added dashboard filtering, sorting and pagination.
- Added statistical observations, descriptive summaries, grouping and CSV export.
- Added dashboard API endpoints without changing the existing worker protocol or database schema.
- Added dashboard endpoint and static-page tests.
- Authentication and server-side scenario generation remain intentionally deferred.

## 0.1.0 - 2026-07-22

- Added FastAPI job, worker, attempt, result and artifact endpoints.
- Added PostgreSQL persistence and Alembic initial migration.
- Added lease expiry and automatic job requeueing.
- Added persistent HRX/package/result/log storage.
- Added Docker Compose deployment and GHCR publishing workflow.
- Added end-to-end API tests.
