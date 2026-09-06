"""Dashboard contracts and seeded aggregation regression tests."""

import json
from datetime import UTC, datetime, timedelta

from server import audit
from server.db import get_conn

from .conftest import unwrap


def fetch(client, pid, range_name="7d"):
    return unwrap(client.get(f"/api/projects/{pid}/dashboard?range={range_name}"))


def test_dashboard_shape_admin_and_empty(client, project):
    pid = project["id"]
    assert client.get(f"/api/projects/{pid}/dashboard", headers=project["a"]["headers"]).status_code == 403
    assert client.get(f"/api/projects/{pid}/dashboard?range=bad").status_code == 422
    assert client.get("/api/projects/999999/dashboard").status_code == 404
    data = fetch(client, pid)
    assert data["range"] == "7d"
    assert data["tiles"]["agents_total"] == data["tiles"]["agents_ever"] == 2
    assert data["tiles"]["tickets_open"] == 0
    assert data["tiles"]["audit_chain_ok"] is True
    assert data["latency"]["ack_median_s"] is None
    assert data["latency"]["decision_wait_p90_s"] is None
    assert data["gates"]["first_review_acceptance_rate"] is None
    assert not any(
        sum(p[k] for k in ("messages", "tickets", "files", "other"))
        for p in data["activity_series"]["points"]
    )
    assert data["attribution"] == dict(correlated=0, self_reported_only=0, monitor_only=0)
    assert fetch(client, pid, "24h")["activity_series"]["bucket"] == "hour"


def test_status_and_completions_are_different_scopes(client, project):
    pid = project["id"]
    for status in ["open", "done", "blocked", "awaiting-human", "wontfix", "done"]:
        ticket = unwrap(client.post(f"/api/projects/{pid}/tickets", json={"title": status}), 201)
        if status != "open":
            unwrap(client.patch(f"/api/projects/{pid}/tickets/{ticket['number']}", json={"status": status}))
    d = fetch(client, pid)
    assert d["tickets_by_status"] == {
        "open": 1,
        "in-progress": 0,
        "done": 2,
        "blocked": 1,
        "awaiting-human": 1,
        "wontfix": 1,
    }
    assert d["tiles"]["tickets_open"] == 3
    assert d["tiles"]["tickets_done_range"] == 2
    assert sum(p["done"] for p in d["completions_series"]) == 2
    assert d["attention"]["tickets_awaiting_human"] == 1
    conn = get_conn()
    conn.execute(
        "UPDATE ticket_transitions SET created_at=? WHERE project_id=?",
        ((datetime.now(UTC) - timedelta(days=40)).isoformat(), pid),
    )
    conn.commit()
    assert fetch(client, pid)["tiles"]["tickets_done_range"] == 0
    assert fetch(client, pid, "all")["tiles"]["tickets_done_range"] == 2
    assert fetch(client, pid)["tickets_by_status"]["done"] == 2


def test_attribution_both_link_directions_and_no_activity_mirrors(client, project):
    pid = project["id"]
    conn = get_conn()
    conn.execute("BEGIN IMMEDIATE")

    def rec(source, correlated=None):
        return audit.record(
            conn,
            pid,
            "monitor" if source == "monitor" else project["a"]["alias"],
            "monitor" if source == "monitor" else "agent",
            source,
            "file_edit",
            "test",
            target="a.py",
            correlated_id=correlated,
        )

    report = rec("self_report")
    rec("monitor", report)
    observation = rec("monitor")
    rec("self_report", observation)
    rec("monitor")
    rec("self_report")
    conn.commit()
    d = fetch(client, pid)
    assert d["attribution"] == dict(correlated=2, self_reported_only=1, monitor_only=1)
    assert sum(p["files"] for p in d["activity_series"]["points"]) == 4
    assert d["tiles"]["audit_chain_ok"] is True
    # Ticket creation produces system messages and platform audit mirrors: one activity only.
    unwrap(client.post(f"/api/projects/{pid}/tickets", json={"title": "one event"}), 201)
    d = fetch(client, pid)
    assert sum(p["tickets"] for p in d["activity_series"]["points"]) == 1
    assert sum(p["messages"] for p in d["activity_series"]["points"]) == 0


def test_range_bounds_all_cap_and_project_isolation(client, project):
    pid = project["id"]
    conn = get_conn()
    now = datetime.now(UTC)
    conn.execute(
        "UPDATE projects SET created_at=? WHERE id=?", ((now - timedelta(days=200)).isoformat(), pid)
    )
    for days in [0, 2, 10, 40, 100]:
        conn.execute(
            "INSERT INTO events(project_id,type,payload,created_at) VALUES(?,?,?,?)",
            (pid, "message", json.dumps({"role": "agent"}), (now - timedelta(days=days)).isoformat()),
        )
    conn.commit()

    def total(r):
        return sum(p["messages"] for p in fetch(client, pid, r)["activity_series"]["points"])

    assert [total(r) for r in ["24h", "7d", "30d", "all"]] == [1, 2, 3, 4]
    assert len(fetch(client, pid, "all")["activity_series"]["points"]) == 90
    other = unwrap(client.post("/api/projects", json={"name": f"isolated-{pid}"}), 201)["id"]
    assert sum(p["messages"] for p in fetch(client, other)["activity_series"]["points"]) == 0


def test_latency_samples_and_first_chronological_review(client, project):
    pid = project["id"]
    conn = get_conn()
    now = datetime.now(UTC)
    start = (now - timedelta(minutes=10)).isoformat()
    end = (now - timedelta(minutes=9)).isoformat()
    unwrap(
        client.post(
            f"/api/projects/{pid}/conversations/{project['main_channel_id']}/messages",
            json={"body": f"@{project['a']['alias']} respond", "requires_ack": True},
        ),
        201,
    )
    request = conn.execute("SELECT id FROM inbox_requests WHERE project_id=?", (pid,)).fetchone()[0]
    conn.execute("UPDATE inbox_requests SET created_at=? WHERE id=?", (start, request))
    for kind in ["acknowledged", "resolved"]:
        conn.execute(
            "INSERT INTO inbox_receipts(project_id,request_id,kind,actor_id,created_at) VALUES(?,?,?,?,?)",
            (pid, request, kind, project["a"]["id"], end),
        )
    conn.execute(
        "INSERT INTO decision_requests(project_id,requester_id,requester_alias,question,options,recommendation,state,created_at,resolved_at) VALUES(?,?,?,?,?,?,'resolved',?,?)",
        (pid, 0, "overseer", "q", "[]", "", start, end),
    )
    conn.commit()
    ticket = unwrap(client.post(f"/api/projects/{pid}/tickets", json={"title": "reviews"}), 201)
    for state, reviewed in [("approved", end), ("rejected", start), ("pending", None)]:
        conn.execute(
            "INSERT INTO completion_proposals(project_id,ticket_id,ticket_revision,policy_revision,evidence_digest,state,submitted_by,created_at,reviewed_at) VALUES(?,?,0,0,?,?,?,?,?)",
            (pid, ticket["id"], "digest", state, "overseer", start, reviewed),
        )
    conn.commit()
    d = fetch(client, pid)
    for prefix in ["ack", "resolve", "decision_wait"]:
        assert d["latency"][prefix + "_median_s"] == 60
        assert d["latency"][prefix + "_p90_s"] == 60
        assert d["latency"][prefix + "_samples"] == 1
    assert d["gates"]["first_review_acceptance_rate"] == 0
    assert d["gates"]["first_review_samples"] == 1
    assert d["attention"]["proposals_pending"] == 1


def test_generated_rulings_not_counted_as_message_activity(client, project):
    pid = project["id"]
    decision = unwrap(
        client.post(
            f"/api/projects/{pid}/decision-requests",
            headers=project["a"]["headers"],
            json={"question": "Choose", "options": ["a", "b"], "recommendation": "a"},
        ),
        201,
    )
    unwrap(
        client.post(
            f"/api/projects/{pid}/decision-requests/{decision['id']}/resolve",
            json={"expected_revision": 0, "result": "a", "reason": "fixture"},
        )
    )
    data = fetch(client, pid)
    assert sum(p["messages"] for p in data["activity_series"]["points"]) == 0
    assert sum(p["other"] for p in data["activity_series"]["points"]) == 2


def test_removed_agent_stays_in_ever_count(client, project):
    pid = project["id"]
    unwrap(client.delete(f"/api/projects/{pid}/agents/{project['a']['id']}"))
    tiles = fetch(client, pid)["tiles"]
    assert tiles["agents_total"] == 1
    assert tiles["agents_ever"] == 2


def test_delivery_receipts_are_not_work_activity(client, project):
    """A delivered receipt is transport noise; acknowledge and resolve are
    real work. Only the latter may move the activity series."""
    pid = project["id"]
    a, b = project["a"], project["b"]
    unwrap(
        client.post(
            f"/api/projects/{pid}/conversations/{project['main_channel_id']}/messages",
            json={"body": f"@{b['alias']} please respond", "requires_ack": True},
            headers=a["headers"],
        ),
        201,
    )
    req = unwrap(client.get(f"/api/projects/{pid}/inbox", headers=b["headers"]))["items"][0]

    def other_total():
        snap = unwrap(client.get(f"/api/projects/{pid}/dashboard?range=24h"))
        return sum(p["other"] for p in snap["activity_series"]["points"])

    base = other_total()
    unwrap(
        client.post(
            f"/api/projects/{pid}/inbox/{req['id']}/receipts",
            json={"kind": "delivered"},
            headers=b["headers"],
        )
    )
    assert other_total() == base, "delivered receipt must not count as activity"
    unwrap(
        client.post(
            f"/api/projects/{pid}/inbox/{req['id']}/receipts",
            json={"kind": "acknowledged", "reason": "on it"},
            headers=b["headers"],
        )
    )
    assert other_total() == base + 1, "acknowledgment is real work"
