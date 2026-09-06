from concurrent.futures import ThreadPoolExecutor

from .conftest import unwrap


def new_ticket(client, pid, title):
    return unwrap(client.post(f"/api/projects/{pid}/tickets", json={"title": title}), 201)


def test_dependencies_claim_next_and_cycles(client, project):
    pid = project["id"]
    a = project["a"]
    b = project["b"]
    one = new_ticket(client, pid, "prerequisite")
    two = new_ticket(client, pid, "dependent")
    unwrap(
        client.put(
            f"/api/projects/{pid}/tickets/{two['number']}/dependencies",
            json={"expected_revision": 0, "dependencies": [{"prerequisite_number": one["number"]}]},
        )
    )
    cycle = client.put(
        f"/api/projects/{pid}/tickets/{one['number']}/dependencies",
        json={"expected_revision": 0, "dependencies": [{"prerequisite_number": two["number"]}]},
    )
    assert cycle.status_code == 409 and cycle.json()["error"]["code"] == "dependency_cycle"
    assert (
        client.post(f"/api/projects/{pid}/tickets/{two['number']}/claim", headers=a["headers"]).status_code
        == 409
    )
    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(
                lambda h: unwrap(client.post(f"/api/projects/{pid}/tickets/claim-next", json={}, headers=h)),
                [a["headers"], b["headers"]],
            )
        )
    winners = [r["ticket"] for r in results if r["ticket"]]
    assert len(winners) == 1 and winners[0]["number"] == one["number"]
    assert client.delete(f"/api/projects/{pid}/tickets/{one['number']}").status_code == 409
    completed = unwrap(
        client.patch(
            f"/api/projects/{pid}/tickets/{one['number']}",
            json={"status": "done", "expected_revision": winners[0]["revision"]},
        )
    )
    assert completed["status"] == "done"
    ready = unwrap(client.get(f"/api/projects/{pid}/tickets?ready=true"))["items"]
    assert [r["number"] for r in ready] == [two["number"]]
    assert (
        client.post(f"/api/projects/{pid}/tickets/{one['number']}/claim", headers=a["headers"]).status_code
        == 409
    )
    # Project deletion removes dependency edges before the restricted prerequisite FK.
    assert client.delete(f"/api/projects/{pid}").status_code == 200


def test_handoff_fences_old_owner_and_stale_offer(client, project):
    pid = project["id"]
    a = project["a"]
    b = project["b"]
    t = new_ticket(client, pid, "transfer")
    current = unwrap(
        client.post(
            f"/api/projects/{pid}/tickets/{t['number']}/claim", json={"managed": True}, headers=a["headers"]
        )
    )
    cp = unwrap(
        client.post(
            f"/api/projects/{pid}/checkpoints",
            json={"purpose": "handoff", "next_action": "test", "ticket_number": t["number"], "shared": True},
            headers=a["headers"],
        ),
        201,
    )
    body = {
        "expected_revision": current["revision"],
        "claim_generation": current["claim_generation"],
        "to_agent_id": b["id"],
        "checkpoint_id": cp["id"],
        "reason": "shift change",
    }
    offer = unwrap(
        client.post(f"/api/projects/{pid}/tickets/{t['number']}/handoffs", json=body, headers=a["headers"]),
        201,
    )
    accepted = unwrap(
        client.post(f"/api/projects/{pid}/handoffs/{offer['id']}/accept", headers=b["headers"])
    )["ticket"]
    assert (
        accepted["assignee"] == b["alias"] and accepted["claim_generation"] == current["claim_generation"] + 1
    )
    stale = client.patch(
        f"/api/projects/{pid}/tickets/{t['number']}",
        json={
            "status": "done",
            "expected_revision": accepted["revision"],
            "claim_generation": current["claim_generation"],
        },
        headers=a["headers"],
    )
    assert stale.status_code == 409
    done = client.patch(
        f"/api/projects/{pid}/tickets/{t['number']}",
        json={
            "status": "done",
            "expected_revision": accepted["revision"],
            "claim_generation": accepted["claim_generation"],
        },
        headers=b["headers"],
    )
    assert done.status_code == 200


def test_stale_revision_rejected_by_patch(client, project):
    pid = project["id"]
    t = new_ticket(client, pid, "rev")
    url = f"/api/projects/{pid}/tickets/{t['number']}"
    assert client.patch(url, json={"title": "new", "expected_revision": 0}).status_code == 200
    assert client.patch(url, json={"title": "lost update", "expected_revision": 0}).status_code == 409
    assert unwrap(client.get(url))["title"] == "new"


def test_stale_generation_refused_after_release_and_reassign(client, project):
    """A fenced-out ex-owner replaying its stale generation token must stay
    fenced even after release or admin reassignment dropped managed_claim;
    tokenless legacy callers keep working."""
    pid = project["id"]
    a, b = project["a"], project["b"]
    t = new_ticket(client, pid, "fence survives release")
    url = f"/api/projects/{pid}/tickets/{t['number']}"
    current = unwrap(client.post(url + "/claim", json={"managed": True}, headers=a["headers"]))
    unwrap(
        client.post(
            url + "/release",
            json={
                "expected_revision": current["revision"],
                "claim_generation": current["claim_generation"],
                "reason": "shift end",
            },
            headers=a["headers"],
        )
    )
    stale = client.patch(
        url,
        json={"status": "done", "claim_generation": current["claim_generation"]},
        headers=a["headers"],
    )
    assert stale.status_code == 409
    assert client.patch(url, json={"status": "done"}, headers=b["headers"]).status_code == 200

    t2 = new_ticket(client, pid, "fence survives reassignment")
    url2 = f"/api/projects/{pid}/tickets/{t2['number']}"
    claimed = unwrap(client.post(url2 + "/claim", json={"managed": True}, headers=a["headers"]))
    unwrap(
        client.patch(
            url2, json={"assignee": b["alias"], "expected_revision": claimed["revision"]}
        )
    )
    stale2 = client.patch(
        url2,
        json={"status": "done", "claim_generation": claimed["claim_generation"]},
        headers=a["headers"],
    )
    assert stale2.status_code == 409


def test_handoff_offer_visible_through_watch_filter(client, project):
    """The durable client watcher subscribes with WATCH_TYPES; a pending
    handoff offer must reach the recipient through that exact filter."""
    from nomos_client.watch import WATCH_TYPES

    pid = project["id"]
    a, b = project["a"], project["b"]
    t = new_ticket(client, pid, "watched transfer")
    current = unwrap(
        client.post(f"/api/projects/{pid}/tickets/{t['number']}/claim", json={"managed": True}, headers=a["headers"])
    )
    cp = unwrap(
        client.post(
            f"/api/projects/{pid}/checkpoints",
            json={"purpose": "handoff", "next_action": "test", "ticket_number": t["number"], "shared": True},
            headers=a["headers"],
        ),
        201,
    )
    unwrap(
        client.post(
            f"/api/projects/{pid}/tickets/{t['number']}/handoffs",
            json={
                "expected_revision": current["revision"],
                "claim_generation": current["claim_generation"],
                "to_agent_id": b["id"],
                "checkpoint_id": cp["id"],
                "reason": "shift change",
            },
            headers=a["headers"],
        ),
        201,
    )
    events = unwrap(
        client.get(f"/api/projects/{pid}/events?since_id=0&types={WATCH_TYPES}", headers=b["headers"])
    )["items"]
    assert any(e["type"] == "handoff_updated" for e in events)


def test_handoff_accept_attaches_recipient_session(client, project):
    """The acceptor can bind their registered session to the claim instead of
    losing session linkage on transfer."""
    pid = project["id"]
    a, b = project["a"], project["b"]
    t = new_ticket(client, pid, "session-linked transfer")
    current = unwrap(
        client.post(f"/api/projects/{pid}/tickets/{t['number']}/claim", json={"managed": True}, headers=a["headers"])
    )
    cp = unwrap(
        client.post(
            f"/api/projects/{pid}/checkpoints",
            json={"purpose": "handoff", "next_action": "test", "ticket_number": t["number"], "shared": True},
            headers=a["headers"],
        ),
        201,
    )
    offer = unwrap(
        client.post(
            f"/api/projects/{pid}/tickets/{t['number']}/handoffs",
            json={
                "expected_revision": current["revision"],
                "claim_generation": current["claim_generation"],
                "to_agent_id": b["id"],
                "checkpoint_id": cp["id"],
                "reason": "shift change",
            },
            headers=a["headers"],
        ),
        201,
    )
    sess = unwrap(client.post(f"/api/projects/{pid}/sessions", json={"runner": "test"}, headers=b["headers"]), 201)
    foreign = unwrap(client.post(f"/api/projects/{pid}/sessions", json={"runner": "test"}, headers=a["headers"]), 201)
    stolen = client.post(
        f"/api/projects/{pid}/handoffs/{offer['id']}/accept",
        json={"session_id": foreign["id"]},
        headers=b["headers"],
    )
    assert stolen.status_code == 422
    accepted = unwrap(
        client.post(
            f"/api/projects/{pid}/handoffs/{offer['id']}/accept",
            json={"session_id": sess["id"]},
            headers=b["headers"],
        )
    )["ticket"]
    assert accepted["claim_session_id"] == sess["id"]
    assert accepted["assignee"] == b["alias"]


def test_bodiless_accept_replays_against_legacy_fingerprint(client, project):
    """Accept without a body must hash the same {} fingerprint it did before
    session support, so pre-upgrade idempotency records still replay."""
    pid = project["id"]
    a, b = project["a"], project["b"]
    t = new_ticket(client, pid, "legacy replay transfer")
    current = unwrap(
        client.post(f"/api/projects/{pid}/tickets/{t['number']}/claim", json={"managed": True}, headers=a["headers"])
    )
    cp = unwrap(
        client.post(
            f"/api/projects/{pid}/checkpoints",
            json={"purpose": "handoff", "next_action": "test", "ticket_number": t["number"], "shared": True},
            headers=a["headers"],
        ),
        201,
    )
    offer = unwrap(
        client.post(
            f"/api/projects/{pid}/tickets/{t['number']}/handoffs",
            json={
                "expected_revision": current["revision"],
                "claim_generation": current["claim_generation"],
                "to_agent_id": b["id"],
                "checkpoint_id": cp["id"],
                "reason": "shift change",
            },
            headers=a["headers"],
        ),
        201,
    )
    # Seed the record a PRE-UPGRADE bodiless accept would have written: the
    # operation key with the hash of {}. The current implementation must
    # replay against it, proving the fingerprint never changed.
    import hashlib
    import json as jsonlib

    from server.db import transaction, utc_now

    legacy_hash = hashlib.sha256(
        jsonlib.dumps({}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    with transaction() as conn:
        conn.execute(
            "INSERT INTO idempotency_requests VALUES (?,?,?,?,?,?,?)",
            (pid, b["id"], f"accept:{offer['id']}", "accept-legacy", legacy_hash,
             jsonlib.dumps({"seeded": True}), utc_now()),
        )
    url = f"/api/projects/{pid}/handoffs/{offer['id']}/accept"
    key = {"Idempotency-Key": "accept-legacy"} | b["headers"]
    replay = unwrap(client.post(url, headers=key))
    assert replay["replayed"] and replay["seeded"] is True
    sess = unwrap(client.post(f"/api/projects/{pid}/sessions", json={}, headers=b["headers"]), 201)
    conflict = client.post(url, json={"session_id": sess["id"]}, headers=key)
    assert conflict.status_code == 409
    accepted = unwrap(client.post(url, headers=b["headers"]))["ticket"]
    assert accepted["assignee"] == b["alias"]
