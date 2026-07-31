def test_runner_claim_package_result_and_curve_discovery(client,auth,imported):
    assert client.post("/jobs",headers=auth,json=imported["job"]).status_code==201
    runner=client.post("/runners/register",headers=auth,json={"runner_id":"runner-1","name":"Worker","version":"1.1.0","capabilities":{"solver":"csharp"}})
    assert runner.status_code==200
    claim=client.post("/claims",headers=auth,json={"runner_id":"runner-1"})
    assert claim.status_code==200,claim.text
    leased=claim.json();attempt=leased["attempt_id"]
    package=client.get(f"/jobs/bridge-1/attempts/{attempt}/package",headers={**auth,"X-Runner-ID":"runner-1"})
    assert package.status_code==200
    result={"runner_id":"runner-1","job_id":"bridge-1","attempt_id":attempt,"job_sha256":leased["job_sha256"],"hrx_sha256":leased["hrx_sha256"],"results":{"reaction_history":[0,1.5,3.0]},"run":{"exit_code":0},"logs":"ok"}
    complete=client.post(f"/jobs/bridge-1/attempts/{attempt}/results",headers=auth,json=result)
    assert complete.status_code==200,complete.text
    series=client.get("/api/ui/dashboard/jobs/bridge-1/series",headers=auth).json()["series"]
    assert series[0]["path"]=="/results/reaction_history"
    runners=client.get("/api/ui/dashboard/runners",headers=auth)
    assert runners.status_code==200,runners.text
