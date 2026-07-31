def test_pages_and_health(client):
    assert client.get("/",follow_redirects=False).status_code in {302,307}
    assert client.get("/dashboard").status_code==200
    assert client.get("/builder").status_code==200
    assert client.get("/health/ready").json()["version"]=="1.1.0"

def test_ui_api_requires_token(raw_client):
    assert raw_client.get("/api/ui/dashboard/summary").status_code == 401
