# Changelog

## 0.1.2 - 2026-07-22

- Replaced the separate CI and release workflows with one CI/CD pipeline.
- Added image verification with Docker Buildx and Trivy SARIF scanning.
- Publish GHCR images on pushes to `main`, semantic version tags, and manual dispatch.
- Added `latest`, commit SHA, full semver, minor, and major image tags.
- Added OCI provenance and SBOM generation to published images.

## 0.1.1 - 2026-07-22

- Fixed Ruff I001 by ordering the SQLAlchemy `JSON` constant before class imports.

## 0.1.0 - 2026-07-22

- Added FastAPI job, worker, attempt, result, and artifact endpoints.
- Added PostgreSQL persistence and Alembic initial migration.
- Added lease expiry and automatic job requeueing.
- Added persistent HRX/package/result/log storage.
- Added Docker Compose deployment and GHCR publishing workflow.
- Added end-to-end API tests.
- Authentication and server-side scenario generation are intentionally deferred.
