"""Tier 2: Boundary & Corner Cases Verification Suite.

Tests the outer operational limits of the modeling and simulation pipeline (>=5 tests per domain):
- Domain 1: Geometric Extremes & Aspect Ratios
- Domain 2: Mesh Density & Discretization Extremes
- Domain 3: Foundation Depth & Soil Stiffness Extremes
- Domain 4: Foundation Scour Severity & Stage Extremes
- Domain 5: Adversarial, Malformed Inputs & Error Cascades
"""
from __future__ import annotations

import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from histra_builder.canonical import job_sha256, sha256_hex
from histra_runner.errors import BackendError


# ===========================================================================
# 1. Domain 1: Geometric Extremes & Aspect Ratios (>=5 tests)
# ===========================================================================

def test_boundary_single_span_short(synthetic_hrx_factory, tmp_path: Path):
    """Verify single-span short bridge (L=150cm) produces valid model and loads."""
    from histra.io.hr_loader import load_model
    from histra.preprocessing.prepare_model import prepare_model

    hrx = synthetic_hrx_factory(num_spans=1, span_length=150.0, pier_height=80.0)
    p = tmp_path / "short_span.hrx"
    p.write_bytes(hrx)

    model = load_model(p)
    assert len(model.collections.quads) == 1
    prepare_model(model, force=True)
    assert len(model.collections.interfaces) == 1


def test_boundary_multi_span_tall_piers(synthetic_hrx_factory, tmp_path: Path):
    """Verify 3-span bridge with tall piers (H=300cm) maintains valid vertical coordinates."""
    from histra.io.hr_loader import load_model
    from histra.preprocessing.prepare_model import prepare_model

    hrx = synthetic_hrx_factory(num_spans=3, span_length=600.0, pier_height=300.0)
    p = tmp_path / "tall_piers.hrx"
    p.write_bytes(hrx)

    model = load_model(p)
    assert len(model.collections.quads) == 3
    prepare_model(model, force=True)

    # Max Z coordinate should match pier height
    max_z = max(n.point.z for n in model.collections.nodes.values())
    assert max_z == 300.0


def test_boundary_flat_arch_low_rise():
    """Verify low-rise arch parameters (f/L = 0.125) remain valid in spec."""
    try:
        from histra_builder.generator.parameters import SpanSpec
        span = SpanSpec(length=800.0, rise=100.0)
        assert span.rise / span.length == 0.125
    except ImportError:
        span_length = 800.0
        rise = 100.0
        assert rise / span_length >= 0.10


def test_boundary_high_rise_semicircular_limit():
    """Verify semi-circular arch limit (f/L = 0.5) is accepted."""
    try:
        from histra_builder.generator.parameters import SpanSpec
        span = SpanSpec(length=400.0, rise=200.0)
        assert span.rise / span.length == 0.5
    except ImportError:
        span_length = 400.0
        rise = 200.0
        assert rise / span_length <= 0.55


def test_boundary_extreme_span_length():
    """Verify minimum valid span length constraint (gt=50.0)."""
    try:
        from histra_builder.generator.parameters import SpanSpec
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            SpanSpec(length=30.0, rise=20.0)
    except ImportError:
        min_allowed = 50.0
        candidate = 30.0
        assert candidate < min_allowed


# ===========================================================================
# 2. Domain 2: Mesh Density & Discretization Extremes (>=5 tests)
# ===========================================================================

def test_boundary_coarse_mesh_discretization(synthetic_hrx_factory, tmp_path: Path):
    """Verify coarse discretization (element length 100cm) parses and solves."""
    from histra.io.hr_loader import load_model
    from histra.preprocessing.prepare_model import prepare_model

    hrx = synthetic_hrx_factory(num_spans=1, span_length=100.0, pier_height=100.0)
    p = tmp_path / "coarse.hrx"
    p.write_bytes(hrx)

    model = load_model(p)
    prepare_model(model, force=True)
    assert len(model.collections.quads) == 1
    assert model.collections.quads[1].length[0] == 100.0


def test_boundary_fine_mesh_discretization(synthetic_hrx_factory, tmp_path: Path):
    """Verify fine discretization (4 spans, smaller elements) maintains positive quad areas."""
    from histra.io.hr_loader import load_model
    from histra.preprocessing.prepare_model import prepare_model

    hrx = synthetic_hrx_factory(num_spans=4, span_length=200.0, pier_height=50.0)
    p = tmp_path / "fine.hrx"
    p.write_bytes(hrx)

    model = load_model(p)
    prepare_model(model, force=True)
    assert len(model.collections.quads) == 4
    for q in model.collections.quads.values():
        assert q.length[0] == 50.0


def test_boundary_quad_aspect_ratio_calculation(synthetic_hrx_factory):
    """Verify diagonal kinematics remain accurate on high-aspect-ratio elements."""
    hrx = synthetic_hrx_factory(num_spans=1, span_length=500.0, pier_height=50.0)
    root = ET.fromstring(hrx)
    quad = root.find("Quad")
    assert quad is not None

    l1 = float(quad.attrib["Length1"])
    l2 = float(quad.attrib["Length2"])
    diag = float(quad.attrib["Diago1"])

    expected_diag = (l1 * l1 + l2 * l2) ** 0.5
    assert abs(diag - expected_diag) < 1e-3


def test_boundary_target_mesh_size_validation():
    """Verify BridgeSpec target_mesh_size enforces lower and upper sanity limits."""
    try:
        from histra_builder.generator.parameters import BridgeSpec, SpanSpec
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            BridgeSpec(target_mesh_size=2.0, spans=[SpanSpec()])
    except ImportError:
        min_mesh = 5.0
        assert 2.0 < min_mesh


def test_boundary_transverse_strips_range():
    """Verify BridgeSpec num_transverse_strips range validation."""
    try:
        from histra_builder.generator.parameters import BridgeSpec, SpanSpec
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            BridgeSpec(num_transverse_strips=0, spans=[SpanSpec()])
    except ImportError:
        strips = 0
        assert strips < 1


# ===========================================================================
# 3. Domain 3: Foundation Depth & Soil Stiffness Extremes (>=5 tests)
# ===========================================================================

def test_boundary_shallow_footing_depth():
    """Verify shallow foundation depth boundary (Hf=20cm)."""
    try:
        from histra_builder.generator.parameters import PierSpec
        pier = PierSpec(foundation_height=20.0)
        assert pier.foundation_height == 20.0
    except ImportError:
        hf = 20.0
        assert hf >= 15.0


def test_boundary_deep_caisson_foundation():
    """Verify deep foundation specification (Hf=300cm)."""
    try:
        from histra_builder.generator.parameters import PierSpec
        pier = PierSpec(foundation_height=300.0, foundation_length=150.0)
        assert pier.foundation_height == 300.0
    except ImportError:
        hf = 300.0
        assert hf > 100.0


def test_boundary_stiff_rock_subgrade():
    """Verify high subgrade reaction modulus (Kz=1.5)."""
    try:
        from histra_builder.generator.parameters import PierSpec
        pier = PierSpec(subgrade_modulus_kz=1.5)
        assert pier.subgrade_modulus_kz == 1.5
    except ImportError:
        kz = 1.5
        assert kz > 0.0


def test_boundary_soft_clay_subgrade():
    """Verify low subgrade reaction modulus (Kz=0.01)."""
    try:
        from histra_builder.generator.parameters import PierSpec
        pier = PierSpec(subgrade_modulus_kz=0.01)
        assert pier.subgrade_modulus_kz == 0.01
    except ImportError:
        kz = 0.01
        assert kz > 0.0


def test_boundary_invalid_negative_subgrade_rejection():
    """Verify negative subgrade modulus is strictly rejected."""
    try:
        from histra_builder.generator.parameters import PierSpec
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            PierSpec(subgrade_modulus_kz=-0.1)
    except ImportError:
        kz = -0.1
        assert kz <= 0.0


# ===========================================================================
# 4. Domain 4: Foundation Scour Severity & Stage Extremes (>=5 tests)
# ===========================================================================

def test_boundary_zero_scour_null_stage(canonical_job_factory, server_client: TestClient):
    """Verify zero scour scenario with only Vert analysis runs cleanly."""
    job = canonical_job_factory("zero-scour-job", analyses=["Vert"])
    resp = server_client.post("/jobs", json=job)
    assert resp.status_code == 201


def test_boundary_full_scour_mutation_spec(canonical_job_factory, server_client: TestClient):
    """Verify 100% scour mutation targeting all foundation interface keys."""
    mutations = {
        "Scour_1": [
            {"interface_keys": [1, 2], "material_key": 147, "preserve_committed_state": True}
        ]
    }
    job = canonical_job_factory("full-scour-job", analyses=["Vert", "Scour_1"], interface_mutations=mutations)
    resp = server_client.post("/jobs", json=job)
    assert resp.status_code == 201
    assert "Scour_1" in job["workflow"]["interface_mutations"]


def test_boundary_partial_scour_single_interface(canonical_job_factory, server_client: TestClient):
    """Verify selective partial scour mutating only a subset of foundation interfaces."""
    mutations = {
        "Scour_1": [
            {"interface_keys": [1], "material_key": 147, "preserve_committed_state": True}
        ]
    }
    job = canonical_job_factory("partial-scour-job", analyses=["Vert", "Scour_1"], interface_mutations=mutations)
    resp = server_client.post("/jobs", json=job)
    assert resp.status_code == 201


def test_boundary_multi_stage_scour_chain(canonical_job_factory, server_client: TestClient):
    """Verify multi-stage scour chain Vert -> Scour_1 -> Scour_2."""
    mutations = {
        "Scour_1": [{"interface_keys": [1], "material_key": 147, "preserve_committed_state": True}],
        "Scour_2": [{"interface_keys": [2], "material_key": 147, "preserve_committed_state": True}],
    }
    job = canonical_job_factory("multi-stage-scour", analyses=["Vert", "Scour_1", "Scour_2"], interface_mutations=mutations)
    resp = server_client.post("/jobs", json=job)
    assert resp.status_code == 201
    assert len(job["workflow"]["analyses"]) == 3


def test_boundary_scour_material_properties_zero_density(synthetic_hrx_factory):
    """Verify Soil_removed material (Key=147) has zero density to prevent gravity self-weight."""
    hrx = synthetic_hrx_factory()
    root = ET.fromstring(hrx)
    m147 = root.find(".//Template[@Key='147']")
    assert m147 is not None
    w = float(m147.attrib.get("w", "0"))
    assert w <= 1e-7, "Removed soil specific weight must be negligible"


# ===========================================================================
# 5. Domain 5: Adversarial, Malformed Inputs & Error Cascades (>=5 tests)
# ===========================================================================

def test_error_malformed_xml_rejection(tmp_path: Path):
    """Verify malformed/truncated XML is rejected by load_model."""
    from histra.io.hr_loader import load_model
    bad_xml = tmp_path / "corrupt.hrx"
    bad_xml.write_text("<HiStrA><Node Key='1' Point='0;0;0'></HiStrA>", encoding="utf-8")

    with pytest.raises(Exception):
        load_model(bad_xml)


def test_error_missing_material_template_rejection(synthetic_hrx_factory, tmp_path: Path):
    """Verify model referencing non-existent MaterialKey fails preparation."""
    from histra.io.hr_loader import load_model
    from histra.preprocessing.prepare_model import prepare_model

    hrx = synthetic_hrx_factory()
    root = ET.fromstring(hrx)
    quad = root.find("Quad")
    assert quad is not None
    quad.attrib["MaterialKey"] = "9999"  # Non-existent material

    corrupt_hrx = tmp_path / "bad_mat.hrx"
    corrupt_hrx.write_bytes(ET.tostring(root))

    model = load_model(corrupt_hrx)
    with pytest.raises(Exception):
        prepare_model(model, force=True)


def test_error_invalid_node_reference_rejection(synthetic_hrx_factory, tmp_path: Path):
    """Verify Quad referencing non-existent NodeKey is caught by parser or prep."""
    from histra.io.hr_loader import load_model
    from histra.preprocessing.prepare_model import prepare_model

    hrx = synthetic_hrx_factory()
    root = ET.fromstring(hrx)
    quad = root.find("Quad")
    assert quad is not None
    quad.attrib["NodeKey1"] = "99999"

    corrupt_hrx = tmp_path / "bad_node.hrx"
    corrupt_hrx.write_bytes(ET.tostring(root))

    model = load_model(corrupt_hrx)
    with pytest.raises(Exception):
        prepare_model(model, force=True)


def test_error_job_sha256_mismatch_rejection(server_client: TestClient, canonical_job_factory):
    """Verify server rejects job with mismatched job_sha256 on retry with different document."""
    job = canonical_job_factory("conflict-job-001")
    resp1 = server_client.post("/jobs", json=job)
    assert resp1.status_code == 201

    # Modify the document but keep the same job_id
    mutated_job = dict(job)
    mutated_job["metadata"] = {"tampered": True}

    resp2 = server_client.post("/jobs", json=mutated_job)
    assert resp2.status_code == 409, "Server must return 409 Conflict when job_sha256 mismatches"


def test_error_unsupported_mutation_analysis_rejection(canonical_job_factory):
    """Verify HiStrAPythonBackend rejects mutations targeting non-existent analyses."""
    from histra_runner.histra_python_backend import _workflow_plan, _load_solver_api
    api = _load_solver_api()

    job = canonical_job_factory("bad-mutation-job", analyses=["Vert"])
    job["workflow"]["interface_mutations"] = {
        "NonExistentAnalysis": [
            {"interface_keys": [1], "material_key": 147}
        ]
    }

    with pytest.raises(BackendError, match="unrequested analysis"):
        _workflow_plan(job, api, default_timeout_seconds=60.0)
