from histra_server.contracts import job_sha256


def test_job_hash_ignores_attempt_and_generated_provenance():
    original = {
        "schema_version": "1.0",
        "job_id": "model-1",
        "model": {"path": "model-1.hrx"},
        "analyses": [{"name": "Vert"}],
        "metadata": {"scenario_id": "s-1"},
    }
    packaged = {
        **original,
        "attempt_id": "attempt-1",
        "metadata": {
            **original["metadata"],
            "job_sha256": "derived",
            "provenance": {"hrx_sha256": "derived"},
            "import_validation": {"source_hrx_sha256": "derived"},
        },
    }
    assert job_sha256(original) == job_sha256(packaged)
