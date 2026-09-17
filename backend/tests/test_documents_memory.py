from typing import Any

from fastapi.testclient import TestClient

from .conftest import FakeVikingClient


def test_upload_list_delete_document(client: TestClient, auth_headers: dict[str, str], fake_viking: FakeVikingClient) -> None:
    resp = client.post(
        "/api/documents",
        files={"file": ("Quarterly Plan.md", b"# Plan\nShip it.", "text/markdown")},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    doc = resp.json()
    assert doc["filename"] == "Quarterly Plan.md"
    assert doc["uri"].startswith("viking://resources/users/")
    assert doc["uri"].split("/")[-1].startswith("quarterly-plan-")
    assert doc["task_id"] == "task-1"
    call = next(c for c in fake_viking.calls if c[0] == "add_resource")
    assert call[1]["to"] == doc["uri"]

    listed = client.get("/api/documents?refresh=true", headers=auth_headers).json()
    assert listed[0]["status"] == "completed"

    detail = client.get(f"/api/documents/{doc['id']}", headers=auth_headers).json()
    assert detail["overview"].startswith("Overview of viking://resources/")

    assert client.delete(f"/api/documents/{doc['id']}", headers=auth_headers).status_code == 204
    assert ("rm", {"uri": doc["uri"]}) in fake_viking.calls
    assert client.get("/api/documents", headers=auth_headers).json() == []


def test_memory_browse_search_read(client: TestClient, auth_headers: dict[str, str]) -> None:
    user_id = client.get("/api/auth/me", headers=auth_headers).json()["id"]
    explicit_root = f"viking://user/{user_id}/memories"

    browse = client.get("/api/memory", headers=auth_headers).json()
    assert browse["mode"] == "browse"
    assert browse["root"] == "viking://~/memories"
    # OpenViking reports entries in the explicit per-user form, not the ``~`` alias.
    assert browse["items"][0]["uri"] == f"{explicit_root}/preferences/style.md"

    search = client.get("/api/memory", params={"query": "concise answers"}, headers=auth_headers).json()
    assert search["mode"] == "search"
    assert search["items"][0]["kind"] == "memory"

    # Both spellings of the user's memory space are accepted on the way back in.
    for uri in (f"{explicit_root}/preferences/style.md", "viking://~/memories/preferences/style.md"):
        read = client.get("/api/memory/read", params={"uri": uri}, headers=auth_headers).json()
        assert read["content"] == "Prefers concise answers."

    for uri in ("viking://agent/skills/x/SKILL.md", "viking://user/someone-else/memories/x.md", "viking://~/memoriesx/y.md"):
        assert client.get("/api/memory/read", params={"uri": uri}, headers=auth_headers).status_code == 400

    for root in ("viking://~/memories", explicit_root, explicit_root + "/"):
        assert client.delete("/api/memory", params={"uri": root}, headers=auth_headers).status_code == 400
    assert client.delete("/api/memory", params={"uri": f"{explicit_root}/preferences/style.md"}, headers=auth_headers).status_code == 204
    assert client.get("/api/memory", headers=auth_headers).json()["items"] == []


def test_memory_browse_empty_when_directory_missing(client: TestClient, auth_headers: dict[str, str], fake_viking: FakeVikingClient) -> None:
    from openviking_sdk.errors import NotFoundError

    async def missing(uri: str, **kwargs: Any) -> list[Any]:
        raise NotFoundError(f"Directory not found: {uri}")

    fake_viking.ls = missing  # type: ignore[method-assign]
    resp = client.get("/api/memory", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"root": "viking://~/memories", "mode": "browse", "items": []}


def test_health(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["openviking"]["healthy"] is True
