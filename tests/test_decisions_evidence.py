from .conftest import unwrap
from .test_coordination import new_ticket

SHA = "a" * 40


def test_decision_resolution_is_atomic_and_replay_safe(client, project):
    pid = project["id"]
    base = f"/api/projects/{pid}"
    d = unwrap(
        client.post(
            base + "/decision-requests",
            json={"question": "Which approach?", "options": ["A", "B"]},
            headers=project["a"]["headers"],
        ),
        201,
    )
    body = {"expected_revision": 0, "result": "A", "reason": "Simpler"}
    url = base + f"/decision-requests/{d['id']}/resolve"
    assert client.post(url, json=body, headers=project["a"]["headers"]).status_code == 403
    first = unwrap(client.post(url, json=body, headers={"Idempotency-Key": "decision-1"}))
    again = unwrap(client.post(url, json=body, headers={"Idempotency-Key": "decision-1"}))
    assert first["message_id"] == again["message_id"] and again["replayed"]
    assert client.post(url, json=body).status_code == 409
    assert unwrap(client.get(base + "/decision-requests"))["items"][0]["state"] == "resolved"
    assert client.get("/api/admin/attention", headers=project["a"]["headers"]).status_code == 403


def configure(client, pid):
    t = new_ticket(client, pid, "evidence gated")
    base = f"/api/projects/{pid}/tickets/{t['number']}"
    policy = {"expected_revision": 0, "required_kinds": ["test"], "human_review": True, "source_commit": SHA}
    unwrap(client.put(base + "/completion-policy", json=policy))
    ev = {
        "kind": "test",
        "outcome": "pass",
        "summary": "Tests passed",
        "command": "pytest",
        "exit_code": 0,
        "source_commit": SHA,
    }
    unwrap(client.post(base + "/evidence", json=ev), 201)
    rev = unwrap(client.get(base))["revision"]
    proposal = unwrap(client.post(base + "/completion-proposals", json={"expected_revision": rev}), 201)
    return t, base, policy, ev, proposal


def test_gate_patch_board_staleness_and_approval(client, project):
    pid = project["id"]
    t, base, policy, ev, proposal = configure(client, pid)
    assert client.patch(base, json={"status": "done"}).status_code == 409
    board = unwrap(client.get(f"/api/projects/{pid}/board"))
    done = next(c for c in board["columns"] if "done" in c["statuses"])
    assert (
        client.post(
            f"/api/projects/{pid}/board/move", json={"ticket_number": t["number"], "column_id": done["id"]}
        ).status_code
        == 409
    )
    url = f"/api/projects/{pid}/completion-proposals/{proposal['id']}/review"
    assert (
        client.post(
            url, json={"approve": True, "reason": "checked"}, headers=project["a"]["headers"]
        ).status_code
        == 403
    )
    # A new evidence report makes the previous review stale, even if identical.
    unwrap(client.post(base + "/evidence", json=ev), 201)
    assert (
        client.post(url, json={"approve": True, "reason": "checked"}).json()["error"]["code"]
        == "stale_proposal"
    )
    rev = unwrap(client.get(base))["revision"]
    p2 = unwrap(client.post(base + "/completion-proposals", json={"expected_revision": rev}), 201)
    url2 = f"/api/projects/{pid}/completion-proposals/{p2['id']}/review"
    unwrap(
        client.post(
            url2,
            json={"approve": True, "reason": "reviewed new evidence"},
            headers={"Idempotency-Key": "approve"},
        )
    )
    assert unwrap(client.get(base))["status"] == "done"
    assert unwrap(
        client.post(
            url2,
            json={"approve": True, "reason": "reviewed new evidence"},
            headers={"Idempotency-Key": "approve"},
        )
    )["replayed"]
    assert client.post(url2, json={"approve": True, "reason": "again"}).status_code == 409
    assert client.delete(f"/api/projects/{pid}").status_code == 200


def test_policy_change_and_new_failed_evidence_invalidate(client, project):
    pid = project["id"]
    _, base, policy, ev, proposal = configure(client, pid)
    policy["expected_revision"] = unwrap(client.get(base))["revision"]
    policy["source_commit"] = "b" * 40
    unwrap(client.put(base + "/completion-policy", json=policy))
    url = f"/api/projects/{pid}/completion-proposals/{proposal['id']}/review"
    assert client.post(url, json={"approve": True, "reason": "old code"}).status_code == 409
    rev = unwrap(client.get(base))["revision"]
    assert client.post(base + "/completion-proposals", json={"expected_revision": rev}).status_code == 409
    ev.update(source_commit="b" * 40, outcome="fail", exit_code=1)
    unwrap(client.post(base + "/evidence", json=ev), 201)
    rev = unwrap(client.get(base))["revision"]
    assert client.post(base + "/completion-proposals", json={"expected_revision": rev}).status_code == 409


def test_artifact_evidence_checks_upload_ownership(client, project):
    pid = project["id"]
    t = new_ticket(client, pid, "artifact")
    att = unwrap(
        client.post(
            f"/api/projects/{pid}/attachments",
            files={"file": ("result.txt", b"ok")},
            headers=project["a"]["headers"],
        ),
        201,
    )
    url = f"/api/projects/{pid}/tickets/{t['number']}/evidence"
    ev = {
        "kind": "artifact",
        "outcome": "pass",
        "summary": "Result",
        "source_commit": SHA,
        "attachment_id": att["id"],
    }
    assert client.post(url, json=ev, headers=project["b"]["headers"]).status_code == 422
    unwrap(client.post(url, json=ev, headers=project["a"]["headers"]), 201)
    assert unwrap(client.get(url))["items"][0]["attachment_id"] == att["id"]


def test_stale_proposal_rejection_cannot_reopen_ticket(client, project):
    """Rejecting an obsolete proposal with return_status must not overwrite
    ticket state that moved on since the snapshot."""
    pid = project["id"]
    t, base, policy, ev, p1 = configure(client, pid)
    unwrap(client.post(base + "/evidence", json=ev), 201)
    rev = unwrap(client.get(base))["revision"]
    p2 = unwrap(client.post(base + "/completion-proposals", json={"expected_revision": rev}), 201)
    unwrap(client.post(f"/api/projects/{pid}/completion-proposals/{p2['id']}/review", json={"approve": True, "reason": "verified"}))
    assert unwrap(client.get(base))["status"] == "done"
    stale = client.post(
        f"/api/projects/{pid}/completion-proposals/{p1['id']}/review",
        json={"approve": False, "return_status": "in-progress", "reason": "obsolete"},
    )
    assert stale.status_code == 409
    plain = unwrap(
        client.post(
            f"/api/projects/{pid}/completion-proposals/{p1['id']}/review",
            json={"approve": False, "reason": "obsolete"},
        )
    )
    assert plain["state"] == "rejected"
    assert unwrap(client.get(base))["status"] == "done"


def test_reviewed_proposals_are_not_reported_stale(client, project):
    """Staleness is an open-offer concept: an approved proposal must read as
    the historical approval it is, even though the ticket moved on."""
    pid = project["id"]
    t, base, policy, ev, p = configure(client, pid)
    unwrap(client.post(f"/api/projects/{pid}/completion-proposals/{p['id']}/review", json={"approve": True, "reason": "verified"}))
    listing = unwrap(client.get(base + "/evidence"))
    by_id = {row["id"]: row for row in listing["proposals"]}
    assert by_id[p["id"]]["state"] == "approved"
    assert by_id[p["id"]]["stale"] is False


def test_policy_update_emits_policy_updated_event(client, project):
    pid = project["id"]
    t = new_ticket(client, pid, "policy event")
    unwrap(
        client.put(
            f"/api/projects/{pid}/tickets/{t['number']}/completion-policy",
            json={"expected_revision": 0, "human_review": True},
        )
    )
    events = unwrap(client.get(f"/api/projects/{pid}/events?since_id=0&types=policy_updated"))["items"]
    assert any(e["payload"].get("ticket_number") == t["number"] for e in events)
