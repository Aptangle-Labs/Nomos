from server.db import get_conn

from .conftest import unwrap
from .test_coordination import new_ticket


def test_exact_status_durations_ignore_edits(client, project):
    pid = project["id"]
    t = new_ticket(client, pid, "timed")
    base = f"/api/projects/{pid}/tickets/{t['number']}"
    for status in ("in-progress", "done", "open", "done"):
        unwrap(client.patch(base, json={"status": status}))
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM ticket_transitions WHERE ticket_id=? ORDER BY id", (t["id"],)
    ).fetchall()
    for r, minute in zip(rows, (0, 10, 20, 25), strict=True):
        conn.execute(
            "UPDATE ticket_transitions SET created_at=? WHERE id=?",
            (f"2026-01-01T00:{minute:02}:00+00:00", r["id"]),
        )
    conn.commit()
    before = unwrap(client.get(f"/api/projects/{pid}/outcomes"))
    unwrap(client.patch(base, json={"description": "Edited much later"}))
    after = unwrap(client.get(f"/api/projects/{pid}/outcomes"))
    assert before["status_durations"] == after["status_durations"]
    assert after["status_durations"]["in-progress"]["total_seconds"] == 600
    assert after["status_durations"]["done"]["total_seconds"] == 600
    assert (
        after["reopens"] == 1 and after["completion_events"] == 2 and after["unique_tickets_completed"] == 1
    )
    assert after["history"]["pre_migration_durations"] is None
    assert client.get(f"/api/projects/{pid}/outcomes", headers=project["a"]["headers"]).status_code == 403


def test_resume_first_progress_is_owned_and_monotonic(client, project):
    pid = project["id"]
    base = f"/api/projects/{pid}"
    r = unwrap(client.post(base + "/resumes", json={}, headers=project["a"]["headers"]), 201)
    url = base + f"/resumes/{r['id']}/progress"
    assert client.post(url, json={"summary": "resumed"}, headers=project["b"]["headers"]).status_code == 404
    unwrap(client.post(url, json={"summary": "resumed"}, headers=project["a"]["headers"]))
    assert (
        client.post(url, json={"summary": "reset timer"}, headers=project["a"]["headers"]).status_code == 409
    )
    assert unwrap(client.get(base + "/outcomes"))["resume_to_reported_progress"]["samples"] == 1


def test_first_review_acceptance_uses_review_order(client, project):
    """Approving a fresh proposal before clearing out an obsolete one counts
    as a first-review acceptance: review time decides, not proposal id."""
    from .test_decisions_evidence import configure

    pid = project["id"]
    t, base, policy, ev, p1 = configure(client, pid)
    unwrap(client.post(base + "/evidence", json=ev), 201)
    rev = unwrap(client.get(base))["revision"]
    p2 = unwrap(client.post(base + "/completion-proposals", json={"expected_revision": rev}), 201)
    unwrap(client.post(f"/api/projects/{pid}/completion-proposals/{p2['id']}/review", json={"approve": True, "reason": "verified"}))
    unwrap(
        client.post(
            f"/api/projects/{pid}/completion-proposals/{p1['id']}/review",
            json={"approve": False, "reason": "obsolete"},
        )
    )
    fr = unwrap(client.get(f"/api/projects/{pid}/outcomes"))["first_review_acceptance"]
    assert fr == {"accepted": 1, "reviewed_tickets": 1, "rate": 1.0}
