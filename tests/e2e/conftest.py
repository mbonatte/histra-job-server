"""Shared E2E test fixtures, adapters, and test environment configuration.

Supports ephemeral SQLite and PostgreSQL database instances, TestClient server integration,
runner worker orchestration, and deterministic synthetic bridge HRX generation.
"""
from __future__ import annotations

import json
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from histra_builder.canonical import canonical_json_bytes, job_sha256, sha256_hex
from histra_runner.backends import ExecutionResult
from histra_runner.contracts import Claim
from histra_runner.executor import RunnerExecutor
from histra_runner.histra_python_backend import HiStrAPythonBackend
from histra_runner.worker import Worker
from histra_server.config import Settings
from histra_server.main import create_app


# ---------------------------------------------------------------------------
# Database & Server Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ephemeral_db_url(tmp_path: Path) -> str:
    """Provide an isolated database URL: PostgreSQL if configured, SQLite otherwise."""
    pg_url = os.environ.get("HISTRA_TEST_POSTGRES_URL") or os.environ.get("HISTRA_DATABASE_URL")
    if pg_url and pg_url.startswith("postgresql"):
        try:
            import sqlalchemy as sa
            engine = sa.create_engine(pg_url)
            with engine.connect() as conn:
                conn.execute(sa.text("SELECT 1"))
            return pg_url
        except Exception:
            pass  # Fall back to isolated SQLite on PostgreSQL connection failure
    db_file = tmp_path / "histra_e2e.db"
    return f"sqlite:///{db_file}"


@pytest.fixture
def server_settings(tmp_path: Path, ephemeral_db_url: str) -> Settings:
    """Instantiate ephemeral server settings with isolated directories."""
    templates = tmp_path / "templates"
    templates.mkdir(parents=True, exist_ok=True)
    cache = tmp_path / "package_cache"
    cache.mkdir(parents=True, exist_ok=True)

    return Settings(
        database_url=ephemeral_db_url,
        template_root=templates,
        package_cache_root=cache,
        api_token="e2e-secret-token",
        lease_seconds=120,
        package_ttl_seconds=3600,
        default_max_attempts=3,
    )


@pytest.fixture
def server_app(server_settings: Settings):
    """Create FastAPI server instance with configured database and lifespan."""
    return create_app(server_settings)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Default bearer authorization headers for server endpoints."""
    return {"Authorization": "Bearer e2e-secret-token"}


@pytest.fixture
def server_client(server_app, auth_headers: dict[str, str]):
    """Authenticated TestClient interacting with the server."""
    with TestClient(server_app, headers=auth_headers) as client:
        yield client


@pytest.fixture
def unauthenticated_client(server_app):
    """Raw TestClient for authentication boundary verification."""
    with TestClient(server_app) as client:
        yield client


# ---------------------------------------------------------------------------
# Server Adapter for histra-job-runner
# ---------------------------------------------------------------------------

class E2EServerAdapter:
    """Adapts the FastAPI TestClient to the Runner network client protocol."""

    def __init__(self, client: TestClient):
        self.client = client

    def register(self, **payload: Any) -> str:
        response = self.client.post("/runners/register", json=payload)
        response.raise_for_status()
        return response.json()["runner_id"]

    def claim(self, runner_id: str) -> Claim | None:
        response = self.client.post("/claims", json={"runner_id": runner_id})
        if response.status_code == 204:
            return None
        response.raise_for_status()
        return Claim.model_validate(response.json())

    def download_package(self, claim: Claim, runner_id: str) -> bytes:
        response = self.client.get(claim.package_url, headers={"X-Runner-ID": runner_id})
        response.raise_for_status()
        return response.content

    def heartbeat(self, claim: Claim, runner_id: str) -> str:
        response = self.client.post(
            f"/jobs/{claim.job_id}/attempts/{claim.attempt_id}/heartbeat",
            json={"runner_id": runner_id},
        )
        response.raise_for_status()
        return response.json()["lease_expires_at"]

    def submit_results(self, claim: Claim, envelope: dict[str, Any]) -> dict[str, Any]:
        response = self.client.post(
            f"/jobs/{claim.job_id}/attempts/{claim.attempt_id}/results",
            json=envelope,
        )
        response.raise_for_status()
        return response.json()

    def submit_failure(self, claim: Claim, **payload: Any) -> dict[str, Any]:
        error = payload.pop("error", "ExecutionError")
        msg = str(error)
        if not msg:
            msg = f"{type(error).__name__}: execution failed"
        import logging
        logging.error(f"[E2EServerAdapter] submit_failure called for job {claim.job_id}: {error}", exc_info=True)
        body = {
            "runner_id": payload.get("runner_id", "runner-1"),
            "error_type": type(error).__name__ if isinstance(error, Exception) else str(error),
            "message": msg[:4000],
            "details": payload.get("details", {}),
            "logs": payload.get("logs", "") or str(error),
        }
        response = self.client.post(
            f"/jobs/{claim.job_id}/attempts/{claim.attempt_id}/failed",
            json=body,
        )
        response.raise_for_status()
        return response.json()


@pytest.fixture
def server_adapter(server_client: TestClient) -> E2EServerAdapter:
    """Protocol adapter for runner workers."""
    return E2EServerAdapter(server_client)


# ---------------------------------------------------------------------------
# Runner Worker & Backend Fixtures
# ---------------------------------------------------------------------------

class MockStructuralBackend:
    """Deterministic fast mock backend simulating solver execution."""

    def __init__(self, should_fail: bool = False, fail_message: str = "mock failure"):
        self.should_fail = should_fail
        self.fail_message = fail_message
        self.executed_jobs: list[str] = []

    def execute(self, package: Any, output_dir: Path) -> ExecutionResult:
        if self.should_fail:
            raise RuntimeError(self.fail_message)

        job_id = package.job.get("job_id", "unknown")
        self.executed_jobs.append(job_id)

        analyses_specs = package.job.get("workflow", {}).get("analyses", [{"name": "Vert"}])
        analyses_results: dict[str, Any] = {}

        for item in analyses_specs:
            name = item if isinstance(item, str) else item.get("name", item.get("id", "Vert"))
            analyses_results[name] = {
                "execution": {
                    "analysis_name": name,
                    "analysis_key": 1,
                    "exit_code": 0,
                    "outcome": "converged",
                    "completed": True,
                    "message": "mock converged",
                    "runtime_seconds": 0.05,
                    "step_count": 5,
                    "committed_step_count": 5,
                    "convergence_history": [
                        {
                            "step": i,
                            "iterations": 3,
                            "convergence_error": 1e-5,
                            "residual_norm": 1e-6,
                            "load_factor": i * 0.2,
                        }
                        for i in range(1, 6)
                    ],
                },
                "outputs": {
                    "reactions": [
                        {"Step": i, "R1": 0.0, "R2": 0.0, "R3": float(-100.0 * i)}
                        for i in range(1, 6)
                    ],
                    "displacements": [
                        {"IdElement": 1, "Step": i, "Ux": 0.0, "Uy": 0.0, "Uz": float(-0.001 * i)}
                        for i in range(1, 6)
                    ],
                },
                "mutations": [],
            }

        results = {
            "backend": "mock-structural",
            "analyses": analyses_results,
            "reaction_curves": {
                "step": [1, 2, 3, 4, 5],
                "R3": [-100.0, -200.0, -300.0, -400.0, -500.0],
            },
        }
        run = {
            "backend": "mock-structural",
            "solver_version": "1.0.0",
            "job_id": job_id,
            "executions": [res["execution"] for res in analyses_results.values()],
        }

        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "results.json").write_text(json.dumps(results), encoding="utf-8")
        (output_dir / "run.json").write_text(json.dumps(run), encoding="utf-8")

        return ExecutionResult(results=results, run=run, logs="mock solver converged successfully")


@pytest.fixture
def mock_backend() -> MockStructuralBackend:
    return MockStructuralBackend()


@pytest.fixture
def live_solver_backend() -> HiStrAPythonBackend:
    return HiStrAPythonBackend()


@pytest.fixture
def runner_worker_factory(tmp_path: Path, server_adapter: E2EServerAdapter):
    """Create isolated Worker instances with specified backend and worker ID."""
    created_workers: list[Worker] = []

    def make_worker(
        backend: Any | None = None,
        runner_id: str = "e2e-worker-1",
        runner_name: str = "E2E Test Worker",
        keep_workspaces: bool = False,
    ) -> Worker:
        executor_backend = backend or MockStructuralBackend()
        worker = Worker(
            client=server_adapter,
            executor=RunnerExecutor(executor_backend),
            work_root=tmp_path / f"work_{runner_id}",
            runner_name=runner_name,
            runner_id=runner_id,
            heartbeat_interval_seconds=0,
            keep_workspaces=keep_workspaces,
        )
        worker.register()
        created_workers.append(worker)
        return worker

    return make_worker


# ---------------------------------------------------------------------------
# Synthetic Bridge & HRX Model Generator
# ---------------------------------------------------------------------------

def build_synthetic_hrx(
    *,
    num_spans: int = 1,
    span_length: float = 200.0,
    pier_height: float = 100.0,
    pier_thickness: float = 50.0,
    scour_stages: list[str] | None = None,
    step_size: float = 0.5,
) -> bytes:
    """Generate byte-exact, schema-valid HiStrA 2026.1.0 .hrx XML model.

    Includes materials (Masonry, Soil, Soil_removed), Nodes, NodeCs, Quads,
    Restraints with Quad edge coupling, gravity LoadCondition, combination, and
    multi-stage analyses with AdapticConvergenceCriteria='ForceMoment'.
    """
    if scour_stages is None:
        scour_stages = ["Vert"]

    # Materials definition template
    material_templates = """
  <Template Key="18" Name="Masonry" PurposeType="MasonryMaterial" TypeOf="HiStrA.Objects.MasonryMaterial"
            w="2.22E-05" E_min="120" E_med="150" E_max="180" G_min="40" G_med="50" G_max="60"
            fm_min="0.24" fm_med="0.32" fm_max="0.4" fvk0_min="0.006" fvk0_med="0.0076" fvk0_max="0.0092"
            Ehor="150" Ever="150" FmHor="0.32" FmVer="0.32" FtmHor="0.01" FtmVer="0.01"
            Fm="0.32" Ftm="0.01" G="50" Gd="50" Gt="0.001" GtVer="0.001" Gc="0.03" GcVer="0.03"
            IsDuctCompr="true" IsDuctTraz="true"
            ConstitutiveLawFlex="ElastoPlasticDuctilityFixed" TensileCurveType="LinearSoftening" CompressiveCurveType="LinearSoftening"
            ConstitutiveLawMasonryShear="ElastoPlasticDuctilityFixed" CriterioSnervamento="Coulomb"
            FrictionRatioShear="1.427" FrictionRatioSlidingHor="1.427" FrictionRatioSlidingVert="1.427" FrictionRatioSlidingDir3="1.427"
            SlidingYieldingDomainHor="Coulomb" SlidingYieldingDomainVert="Coulomb" SlidingYieldingDomainDir3="Coulomb" />
  <Template Key="146" Name="Soil" PurposeType="MasonryMaterial" TypeOf="HiStrA.Objects.MasonryMaterial"
            w="1.8E-05" E_min="100" E_med="100" E_max="100" G_min="30" G_med="30" G_max="30"
            fm_min="0.1" fm_med="0.1" fm_max="0.1" fvk0_min="0.005" fvk0_med="0.005" fvk0_max="0.005"
            Ehor="100" Ever="100" FmHor="0.1" FmVer="0.1" FtmHor="0.001" FtmVer="0.001"
            Fm="0.1" Ftm="0.001" G="30" Gd="30" Gt="0.001" GtVer="0.001" Gc="0.03" GcVer="0.03"
            IsDuctCompr="true" IsDuctTraz="true"
            ConstitutiveLawFlex="ElastoPlasticDuctilityFixed" TensileCurveType="LinearSoftening" CompressiveCurveType="LinearSoftening"
            ConstitutiveLawMasonryShear="ElastoPlasticDuctilityFixed" CriterioSnervamento="Coulomb"
            FrictionRatioShear="1.427" FrictionRatioSlidingHor="1.427" FrictionRatioSlidingVert="1.427" FrictionRatioSlidingDir3="1.427"
            SlidingYieldingDomainHor="Coulomb" SlidingYieldingDomainVert="Coulomb" SlidingYieldingDomainDir3="Coulomb" />
  <Template Key="147" Name="Soil_removed" PurposeType="MasonryMaterial" TypeOf="HiStrA.Objects.MasonryMaterial"
            w="1E-08" E_min="0.01" E_med="0.01" E_max="0.01" G_min="0.005" G_med="0.005" G_max="0.005"
            fm_min="0.001" fm_med="0.001" fm_max="0.001" fvk0_min="0.0001" fvk0_med="0.0001" fvk0_max="0.0001"
            Ehor="0.01" Ever="0.01" FmHor="0.001" FmVer="0.001" FtmHor="0.0001" FtmVer="0.0001"
            Fm="0.001" Ftm="0.0001" G="0.005" Gd="0.005" Gt="0.001" GtVer="0.001" Gc="0.03" GcVer="0.03"
            IsDuctCompr="true" IsDuctTraz="true"
            ConstitutiveLawFlex="ElastoPlasticDuctilityFixed" TensileCurveType="LinearSoftening" CompressiveCurveType="LinearSoftening"
            ConstitutiveLawMasonryShear="ElastoPlasticDuctilityFixed" CriterioSnervamento="Coulomb"
            FrictionRatioShear="1.427" FrictionRatioSlidingHor="1.427" FrictionRatioSlidingVert="1.427" FrictionRatioSlidingDir3="1.427"
            SlidingYieldingDomainHor="Coulomb" SlidingYieldingDomainVert="Coulomb" SlidingYieldingDomainDir3="Coulomb" />
"""

    nodes_xml: list[str] = []
    node_cs_xml: list[str] = []
    quads_xml: list[str] = []
    restraints_xml: list[str] = []

    # Build Quad cells horizontally across spans/piers with shared mesh nodes
    quad_count = max(1, num_spans)
    quad_width = span_length / quad_count

    # Generate shared bottom nodes (Z=0) and top nodes (Z=pier_height)
    for i in range(quad_count + 1):
        x = i * quad_width
        nodes_xml.append(f'<Node Key="{i+1}" Point="{x:.2f};0;0.00" Name="{i+1}" />')
        node_cs_xml.append(f'<NodeC Key="{i+1}" NodeKey="{i+1}" MasterElementKey="0" MasterElementType="None" IsIndipendent="true" />')

    top_offset = quad_count + 1
    for i in range(quad_count + 1):
        x = i * quad_width
        nk = top_offset + i + 1
        nodes_xml.append(f'<Node Key="{nk}" Point="{x:.2f};0;{pier_height:.2f}" Name="{nk}" />')

    diag = (quad_width * quad_width + pier_height * pier_height) ** 0.5

    for i in range(quad_count):
        x0 = i * quad_width
        x1 = (i + 1) * quad_width
        nk1 = i + 1
        nk2 = i + 2
        nk3 = top_offset + i + 2
        nk4 = top_offset + i + 1

        qk = i + 1
        cx = (x0 + x1) / 2.0
        cz = pier_height / 2.0

        quads_xml.append(f"""
    <Quad Key="{qk}" Name="Q{qk}" ParentKey="1" ParentTypeElement="Bridge" MaterialKey="18" LayerKey="0"
          NodeKey1="{nk1}" NodeKey2="{nk2}" NodeKey3="{nk3}" NodeKey4="{nk4}"
          Length1="{quad_width:.2f}" Length2="{pier_height:.2f}" Length3="{quad_width:.2f}" Length4="{pier_height:.2f}"
          Sin1="1" Sin2="1" Sin3="1" Sin4="1"
          Cos1="0" Cos2="0" Cos3="0" Cos4="0"
          Thickness1="50" Thickness2="50" Thickness3="50" Thickness4="50"
          Normal1="0;-1;0" Normal2="0;-1;0" Normal3="0;-1;0" Normal4="0;-1;0"
          Diago1="{diag:.6f}" Diago2="{diag:.6f}" G="{cx:.2f};0;{cz:.2f}">
      <ReferenceSystem E1="1;0;0" E2="0;0;1" E3="0;-1;0" Origin="{x0:.2f};0;0.00" />
    </Quad>
        """)

        # Restraint on bottom edge of each quad (Z=0)
        rk = i + 1
        restraints_xml.append(f"""
    <Restraint Key="{rk}" Name="R{rk}" ParentKey="1" ParentTypeElement="GeometryLineRestraint"
               NodeCKey1="{nk1}" NodeCKey2="{nk2}" NodeKey1="{nk1}" NodeKey2="{nk2}" MaterialKey="146"
               ComputationalElementKey="{qk}" ComputationalElementType="Quad" ComputationalElementEdge="0"
               Zg="0" LayerKey="0" G="{cx:.2f};0;0"
               Point1="{x0:.2f};-25;0" Point2="{x1:.2f};-25;0" Point3="{x1:.2f};25;0" Point4="{x0:.2f};25;0"
               K1="-1" K2="-1" K3="-1" Kr1="-1" Kr2="-1" Kr3="-1" />
        """)

    # Analyses specifications
    analyses_xml: list[str] = []
    prev_key = -100
    for idx, stage in enumerate(scour_stages):
        key = 1 if idx == 0 else 20 + idx
        init_key = prev_key
        prev_key = key
        analyses_xml.append(f"""
    <Analysis Key="{key}" Name="{stage}" AnalysisType="2" InitialAnalysisKey="{init_key}"
              LoadCombinationKey="6" LoadFunctionKey="1"
              AdapticConvergenceCriteria="ForceMoment" ConvergenceTolerance="0.0001"
              ConvergenceToleranceForce="0.0001" ConvergenceToleranceMoment="0.0001"
              Method="ModifiedRegulaFalsiLineSearch" IntegrationMethod="LoadControl"
              TypeLoadDistribution="LoadCombination" ForceImposed="true" ForceControl="true"
              AnalysisStepSize="{step_size}" MasterPoint="1" StepSave="1" MaxIterations="50" />
        """)

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<HiStrA version="2026.1.0" GDL="0" WizardType="RailBridge" IsLocked="false">
  <AdvancedOptionsDefault InterfaceNrow="9" InterfaceImax="400" MassMatrixType="Lumped" />
  {material_templates}
  {''.join(nodes_xml)}
  {''.join(node_cs_xml)}
  {''.join(quads_xml)}
  {''.join(restraints_xml)}
  <LoadCondition Id="1" Name="Gravity" Action="1" />
  <LoadCombination Key="6" Name="SEISMIC" LimitState="Seismic">
    <Item LoadCombinationKey="6" ColumnKey="1" RowKey="1" Name="1" Combination="1" Val="1" TypeData="Number" />
  </LoadCombination>
  <LoadFunction key="1" typeDiscr="false" DiscrVal="0.05">
    <LoadFunctionItem key="1" loadFunctionKey="1" pseudoTime="0" multiplier="0" />
    <LoadFunctionItem key="2" loadFunctionKey="1" pseudoTime="1" multiplier="1" />
  </LoadFunction>
  {''.join(analyses_xml)}
</HiStrA>
"""
    return xml.encode("utf-8")


@pytest.fixture
def synthetic_hrx_factory() -> Callable[..., bytes]:
    """Callable fixture returning synthetic valid HRX bytes."""
    return build_synthetic_hrx


@pytest.fixture
def canonical_job_factory(server_settings: Settings):
    """Callable fixture building and registering a canonical JOB in template store."""
    def make_job(
        job_id: str,
        *,
        hrx_bytes: bytes | None = None,
        template_id: str | None = None,
        analyses: list[str] | None = None,
        interface_mutations: dict[str, list[dict[str, Any]]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if hrx_bytes is None:
            hrx_bytes = build_synthetic_hrx()

        tid = template_id or f"template-{job_id}"
        template_path = server_settings.template_root / f"{tid}.hrx"
        template_path.write_bytes(hrx_bytes)
        template_digest = sha256_hex(hrx_bytes)

        workflow_analyses = [{"name": name} for name in (analyses or ["Vert"])]
        workflow: dict[str, Any] = {"analyses": workflow_analyses}
        if interface_mutations:
            workflow["interface_mutations"] = interface_mutations

        return {
            "schema_version": "1.0",
            "job_id": job_id,
            "model": {
                "template": {"id": tid, "sha256": template_digest},
                "output_path": "model.hrx",
                "patches": [],
            },
            "workflow": workflow,
            "metadata": metadata or {"e2e_suite": "tier_test"},
        }

    return make_job
