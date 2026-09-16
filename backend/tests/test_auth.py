from fastapi.testclient import TestClient


def test_register_login_me(client: TestClient) -> None:
    resp = client.post("/api/auth/register", json={"username": "alice", "password": "wonderland1"})
    assert resp.status_code == 201
    token = resp.json()["access_token"]

    dup = client.post("/api/auth/register", json={"username": "alice", "password": "wonderland1"})
    assert dup.status_code == 409

    bad = client.post("/api/auth/login", data={"username": "alice", "password": "nope-nope"})
    assert bad.status_code == 401

    ok = client.post("/api/auth/login", data={"username": "alice", "password": "wonderland1"})
    assert ok.status_code == 200
    assert ok.json()["user"]["username"] == "alice"

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == "alice"

    anon = client.get("/api/sessions")
    assert anon.status_code == 401
