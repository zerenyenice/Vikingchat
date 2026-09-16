from fastapi.testclient import TestClient

from .conftest import read_sse


def test_chat_roundtrip_streams_and_persists(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post("/api/sessions", json={"kind": "chat"}, headers=auth_headers)
    assert created.status_code == 201
    session = created.json()
    assert session["kind"] == "chat"

    with client.stream(
        "POST", f"/api/sessions/{session['id']}/messages", json={"content": "Hi there"}, headers=auth_headers
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = read_sse(resp)

    types = [e["type"] for e in events]
    assert types[0] == "user_message"
    assert types[-1] == "done"
    assert "error" not in types
    assert events[-1]["message"]["content"] == "Hello from the fake agent."

    detail = client.get(f"/api/sessions/{session['id']}", headers=auth_headers).json()
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant"]
    assert detail["title"] == "Hi there"  # first message becomes the title

    listing = client.get("/api/sessions", headers=auth_headers).json()
    assert [s["id"] for s in listing] == [session["id"]]

    assert client.delete(f"/api/sessions/{session['id']}", headers=auth_headers).status_code == 204
    assert client.get(f"/api/sessions/{session['id']}", headers=auth_headers).status_code == 404


def test_sessions_are_private(client: TestClient, auth_headers: dict[str, str]) -> None:
    session = client.post("/api/sessions", json={"kind": "chat"}, headers=auth_headers).json()
    other = client.post("/api/auth/register", json={"username": "mallory", "password": "password123"}).json()
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    assert client.get(f"/api/sessions/{session['id']}", headers=other_headers).status_code == 404
