# HiStrA Job Server Agent Operating Manual

Quick-start guide, architectural contracts, and operating rules for AI agents working in `histra-job-server`.

---

## 1. Project Overview & Role

`histra-job-server` is the centralized orchestration control plane, results database, and browser authoring interface for distributed HiStrA analyses.

It owns:
1. **Job Ingestion**: Ingesting single jobs (`POST /jobs`) and batch portfolios (`POST /api/ui/builder/submit-batch`).
2. **Atomic Job Leasing**: Dispensing jobs to workers atomically with PostgreSQL/SQLite concurrency controls (`POST /claims`).
3. **Attempt Tracking**: Managing attempt state machines (`claimed` -> `running` -> `completed` / `failed`), leases, and worker heartbeats.
4. **Result Database**: Storing structured simulation output payloads, reaction curves, displacements, and convergence histories in `Job.result` and `Attempt.result`.
5. **Series Discovery API**: Exposing 1D numeric vectors and curve series (`GET /api/ui/dashboard/jobs/{id}/series`) for browser visualization.
6. **Web Interfaces**: Serving the live Web Dashboard (`/dashboard`) and Builder UI (`/builder`).
7. **Immutable Template Store**: Managing verified reference HRX model templates keyed by SHA-256 hash.

---

## 2. Architecture & Key Modules

```text
src/histra_server/
├── main.py        # FastAPI application, route declarations, lifespan initialization
├── models.py      # SQLAlchemy ORM definitions (Job, Attempt, Runner)
├── service.py     # Core business logic: atomic claims, leases, attempts, results
├── results.py     # Numeric series extraction for dashboard charting
├── package.py     # Package compiler & ZIP packager for runner claims
├── config.py      # Configuration via Pydantic Settings
├── db.py          # Session factory supporting PostgreSQL & SQLite
├── cache.py       # Caching utilities for attempt packages
└── static/        # Web dashboard and builder UI assets (HTML/CSS/JS)
tests/
├── e2e/           # 4-tier cross-repository acceptance and portfolio integration test suite
├── test_api_edges.py
├── test_attempts.py
├── test_dashboard.py
├── test_jobs.py
└── test_system_integration.py
```

---

## 3. Non-Negotiable Invariants

### 1. Concurrency-Safe Atomic Claims
- The claim query MUST use `select(Job).where(Job.status == "queued").with_for_update(skip_locked=True)`.
- Concurrent workers must never receive the same job attempt.

### 2. Dual-Engine Database Portability
- The server must operate cleanly against both **SQLite** (standard for unit and ephemeral tests) and **PostgreSQL 17** (production via `psycopg`).
- Tables are generated on startup with `Base.metadata.create_all(engine)`. Avoid engine-specific SQL constructs.

### 3. Immutable Template Storage
- HRX templates uploaded or registered under `template_root` are immutable.
- A template ID cannot be overwritten with different content (`TemplateIntegrityError`).

### 4. Curve Series Discovery
- `extract_numeric_series` in `results.py` must discover plottable 1D numeric arrays from result payloads.
- Columnar vectors such as reactions (`R1`, `R2`, `R3`), displacements (`Ux`, `Uy`, `Uz`), and convergence error metrics must be accessible via `/api/ui/dashboard/jobs/{id}/series`.

### 5. Multi-Repo E2E Pipeline Integrity
- `tests/e2e/` maintains the 4-tier integration test harness:
  - Tier 1: Schema and contract enforcement.
  - Tier 2: Boundary conditions and edge cases.
  - Tier 3: Cross-module interactions (Builder + Runner + Server).
  - Tier 4: Real-world 5-bridge portfolio execution end-to-end.

---

## 4. Environment & Verification

### Python Environment
- Requires Python 3.11–3.14.
- Dependencies: `fastapi>=0.115`, `sqlalchemy>=2.0`, `pydantic>=2.7`, `histra-job-builder>=1.2.0`.

### Running Tests
```bash
# Run full server test suite including e2e acceptance suite (115+ tests)
pytest -ra

# Run core server unit tests only
pytest tests/test_jobs.py tests/test_attempts.py tests/test_dashboard.py

# Run cross-repository E2E portfolio tests
pytest tests/e2e/test_tier4_realworld_portfolio.py

# Check test coverage (threshold: 85%)
pytest --cov=histra_server --cov-report=term-missing
```

---

## 5. Deployment Commands

```bash
# Local development server
histra-server --port 8000 --reload

# Production deployment with Docker Compose
docker compose up -d
docker compose logs -f api
```
