def test_dashboard_follows_jobs(client,auth,imported):
    submit=client.post("/jobs",headers=auth,json=imported["job"])
    assert submit.status_code==201
    summary=client.get("/api/ui/dashboard/summary",headers=auth).json()
    assert summary["jobs"]["by_status"]["queued"]==1
    jobs=client.get("/api/ui/dashboard/jobs",headers=auth).json()
    assert jobs["items"][0]["job_id"]=="bridge-1"
    detail=client.get("/api/ui/dashboard/jobs/bridge-1",headers=auth).json()
    assert detail["job"]["model"]["template"]["id"]=="bridge-1"
