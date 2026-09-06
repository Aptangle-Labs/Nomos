from concurrent.futures import ThreadPoolExecutor

import pytest

from server.auth import Actor
from server.db import query_one
from server.errors import ApiError
from server.operations import _mutate

from .conftest import unwrap


def test_replay_is_atomic_and_reauthorized(client, project):
    pid = project["id"]
    a = project["a"]
    actor = Actor("agent", a["id"], pid, a["alias"])

    def change(conn):
        conn.execute("UPDATE projects SET next_ticket_number=next_ticket_number+1 WHERE id=?", (pid,))
        return {
            "value": conn.execute("SELECT next_ticket_number FROM projects WHERE id=?", (pid,)).fetchone()[0]
        }

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: _mutate(actor, pid, "test", {"x": 1}, "key", change), range(4)))
    assert {r["value"] for r in results} == {2}
    assert sum(r.get("replayed", False) for r in results) == 3
    with pytest.raises(ApiError, match="different request"):
        _mutate(actor, pid, "test", {"x": 2}, "key", change)
    client.post(f"/api/projects/{pid}/agents/{a['id']}/revoke")
    with pytest.raises(ApiError, match="revoked"):
        _mutate(actor, pid, "test", {"x": 1}, "key", change)


def test_rollback_and_revision(client, project):
    pid = project["id"]
    actor = Actor("admin", 0, None, "overseer")

    def fail(conn):
        conn.execute("UPDATE projects SET next_ticket_number=99 WHERE id=?", (pid,))
        raise RuntimeError("rollback")

    with pytest.raises(RuntimeError):
        _mutate(actor, pid, "fail", {}, "key", fail)
    assert query_one("SELECT next_ticket_number FROM projects WHERE id=?", (pid,))[0] == 1
    assert query_one("SELECT COUNT(*) FROM idempotency_requests WHERE project_id=?", (pid,))[0] == 0
    t = unwrap(client.post(f"/api/projects/{pid}/tickets", json={"title": "revision"}), 201)
    client.patch(f"/api/projects/{pid}/tickets/{t['number']}", json={"title": "changed"})
    assert query_one("SELECT revision FROM tickets WHERE id=?", (t["id"],))[0] == 1
