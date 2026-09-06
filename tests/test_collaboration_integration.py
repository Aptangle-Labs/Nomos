from server import fsmonitor

from .conftest import unwrap
from .test_coordination import new_ticket
from .test_workspaces import register


def test_keyed_existing_writes_replay_without_duplicate_effects(client, project):
    base = f"/api/projects/{project['id']}"
    cases = [
        ("/tickets", {"title": "keyed"}),
        ("/tickets/bulk", {"tickets": [{"title": "batch1"}, {"title": "batch2"}]}),
        ("/audit", {"action": "command", "summary": "keyed report"}),
        ("/audit/bulk", {"items": [{"action": "command", "summary": "bulk report"}]}),
    ]
    for route, body in cases:
        headers = project["a"]["headers"] | {"Idempotency-Key": "integration"}
        first = unwrap(client.post(base + route, json=body, headers=headers), 201)
        second = unwrap(client.post(base + route, json=body, headers=headers), 201)
        assert second.pop("replayed") is True
        assert first == second
    assert len(unwrap(client.get(base + "/tickets"))["items"]) == 3


def test_known_workspace_observation_invalidates_review(client, project, tmp_path):
    pid = project["id"]
    t = new_ticket(client, pid, "observed review")
    w = register(client, pid, tmp_path / "checkout")
    base = f"/api/projects/{pid}/tickets/{t['number']}"
    unwrap(
        client.put(
            base + "/completion-policy",
            json={
                "workspace_id": w["id"],
                "expected_revision": 0,
                "human_review": True,
                "required_kinds": ["test"],
                "source_commit": "a" * 40,
            },
        )
    )
    ev = {
        "workspace_id": w["id"],
        "kind": "test",
        "outcome": "pass",
        "command": "pytest",
        "exit_code": 0,
        "summary": "green",
        "source_commit": "a" * 40,
    }
    unwrap(client.post(base + "/evidence", json=ev), 201)
    rev = unwrap(client.get(base))["revision"]
    p = unwrap(client.post(base + "/completion-proposals", json={"expected_revision": rev}), 201)
    fsmonitor._record_changes(pid, [("file_edit", "test.py", "changed", None, "b" * 64)], w["id"])
    result = client.post(
        f"/api/projects/{pid}/completion-proposals/{p['id']}/review",
        json={"approve": True, "reason": "old review"},
    )
    assert result.status_code == 409 and result.json()["error"]["code"] == "stale_proposal"
    assert unwrap(client.get(base + "/evidence"))["proposals"][0]["stale"]
    assert client.post(base + "/completion-proposals", json={"expected_revision": rev}).status_code == 409


def test_context_markdown_budget_and_attention_links(client, project):
    pid = project["id"]
    base = f"/api/projects/{pid}"
    response = client.get(base + "/context?format=markdown&max_bytes=1024", headers=project["a"]["headers"])
    assert response.status_code == 200 and len(response.content) <= 1024
    assert "X-Nomos-Snapshot-Event-ID" in response.headers
    unwrap(
        client.post(
            base + "/decision-requests",
            json={"question": "Choose", "options": ["A", "B"], "required_by": "2026-10-01T12:00:00Z"},
        ),
        201,
    )
    data = unwrap(client.get(f"/api/admin/attention?project_id={pid}"))
    assert data["items"][0]["route"] == "decisionQueue"
    bad = client.post(
        base + "/decision-requests",
        json={"question": "Choose", "options": ["A", "B"], "sources": [{"kind": "ticket", "id": 999999}]},
    )
    assert bad.status_code == 422
