def test_import_rejects_malformed_hrx(client,auth):
    response=client.post("/api/ui/builder/import",headers=auth,data={"job_id":"bad","template_id":"bad"},files={"file":("bad.hrx",b"<broken>","application/xml")})
    assert response.status_code==422

def test_batch_is_atomic_on_invalid_job(client,auth,imported):
    invalid=dict(imported["job"]);invalid["job_id"]="bad id"
    response=client.post("/api/ui/builder/submit-batch",headers=auth,json={"jobs":[imported["job"],invalid]})
    assert response.status_code==422
    jobs=client.get("/jobs",headers=auth).json()["items"]
    assert jobs==[]
