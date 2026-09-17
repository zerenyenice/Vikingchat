import yaml
from fastapi.testclient import TestClient

from app.agent.skill_builder import SkillDraft, normalize_skill_name, render_skill_md

from .conftest import FakeVikingClient, read_sse


def test_render_skill_md_has_valid_frontmatter() -> None:
    draft = SkillDraft(name="weekly-report", description="Builds the weekly report.", tags=["Reporting"], instructions="## Steps\n1. Do it.")
    md = render_skill_md(draft, session_id="s1", built_by="alice")
    assert md.startswith("---\n")
    frontmatter = yaml.safe_load(md.split("---\n")[1])
    assert frontmatter["name"] == "weekly-report"
    assert frontmatter["tags"] == ["reporting"]
    assert frontmatter["metadata"]["session_id"] == "s1"
    assert "## Steps" in md


def test_normalize_skill_name() -> None:
    assert normalize_skill_name("Weekly Report Builder!") == "weekly-report-builder"
    assert normalize_skill_name("  my__skill  ") == "my-skill"


def test_build_skill_from_skill_session(client: TestClient, auth_headers: dict[str, str], fake_viking: FakeVikingClient) -> None:
    session = client.post("/api/sessions", json={"kind": "skill"}, headers=auth_headers).json()

    # Building before any conversation is rejected.
    assert client.post(f"/api/sessions/{session['id']}/build-skill", json={}, headers=auth_headers).status_code == 400

    with client.stream(
        "POST", f"/api/sessions/{session['id']}/messages", json={"content": "Teach: build my weekly report"}, headers=auth_headers
    ) as resp:
        read_sse(resp)

    built = client.post(f"/api/sessions/{session['id']}/build-skill", json={"notes": "keep it short"}, headers=auth_headers)
    assert built.status_code == 200, built.text
    payload = built.json()
    assert payload["name"] == "weekly-report-builder"
    assert payload["uri"] == "viking://agent/skills/weekly-report-builder"
    assert payload["session"]["skill_name"] == "weekly-report-builder"
    assert "weekly-report-builder" in fake_viking.skills
    assert fake_viking.skills["weekly-report-builder"].startswith("---\nname: weekly-report-builder")

    skills = client.get("/api/skills", headers=auth_headers).json()
    assert [s["name"] for s in skills] == ["weekly-report-builder"]

    detail = client.get("/api/skills/weekly-report-builder", headers=auth_headers).json()
    assert "## Steps" in detail["content"]

    # Building again updates the existing skill instead of failing.
    again = client.post(f"/api/sessions/{session['id']}/build-skill", json={}, headers=auth_headers)
    assert again.status_code == 200
    assert ("update_skill", {"name": "weekly-report-builder"}) in fake_viking.calls

    assert client.delete("/api/skills/weekly-report-builder", headers=auth_headers).status_code == 204
    assert client.get("/api/skills", headers=auth_headers).json() == []


def test_build_skill_rejects_chat_sessions(client: TestClient, auth_headers: dict[str, str]) -> None:
    session = client.post("/api/sessions", json={"kind": "chat"}, headers=auth_headers).json()
    resp = client.post(f"/api/sessions/{session['id']}/build-skill", json={}, headers=auth_headers)
    assert resp.status_code == 400
