import json

def test_import_roundtrip_preview_and_compile(client,auth,imported,hrx_bytes):
    assert imported["roundtrip"]["exact"] is True
    assert imported["preview"]["counts"]=={"nodes":4,"quads":1}
    job=imported["job"]
    preview=client.post("/api/ui/builder/preview",headers=auth,json=job)
    assert preview.status_code==200
    compiled=client.post("/api/ui/builder/compile",headers=auth,json=job)
    assert compiled.status_code==200
    assert compiled.content==hrx_bytes
    templates=client.get("/api/ui/builder/templates",headers=auth).json()["items"]
    assert any(item["id"] == "bridge-1" for item in templates)

def test_generate_and_submit_variants(client,auth,imported):
    payload={"base_job":imported["job"],"variants":{"variants":[{"job_id":"bridge-scour-050","changes":[{"path":"/metadata/scour_normalized","value":0.5}]}]}}
    response=client.post("/api/ui/builder/variants",headers=auth,json=payload)
    assert response.status_code==200,response.text
    jobs=response.json()["items"]
    assert jobs[0]["metadata"]["variant_of"]=="bridge-1"
    submitted=client.post("/api/ui/builder/submit-batch",headers=auth,json={"jobs":jobs})
    assert submitted.status_code==200,submitted.text
    assert submitted.json()["items"][0]["created"] is True
