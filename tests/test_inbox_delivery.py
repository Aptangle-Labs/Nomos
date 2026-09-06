from server.db import query_one

from .conftest import unwrap


def test_reply_request_lifecycle_and_replay(client, project):
    pid = project["id"]
    a = project["a"]
    cid = project["main_channel_id"]
    payload = {"body": f"@{a['alias']} what is pending?", "requires_ack": True}
    url = f"/api/projects/{pid}/conversations/{cid}/messages"
    first = unwrap(client.post(url, json=payload, headers={"Idempotency-Key": "question"}), 201)
    again = unwrap(client.post(url, json=payload, headers={"Idempotency-Key": "question"}), 201)
    assert first["id"] == again["id"] and again["replayed"]
    inbox = unwrap(client.get(f"/api/projects/{pid}/inbox", headers=a["headers"]))["items"]
    assert len(inbox) == 1
    rid = inbox[0]["id"]
    endpoint = f"/api/projects/{pid}/inbox/{rid}/receipts"
    client.post(f"/api/projects/{pid}/mentions/seen", json={"all": True}, headers=a["headers"])
    assert (
        unwrap(client.get(f"/api/projects/{pid}/inbox", headers=a["headers"]))["items"][0]["state"]
        == "pending"
    )
    assert (
        client.post(endpoint, json={"kind": "acknowledged"}, headers=project["b"]["headers"]).status_code
        == 404
    )
    for kind in ["delivered", "acknowledged", "resolved"]:
        body = {"kind": kind, "reason": "Answered in the thread" if kind == "resolved" else ""}
        response = client.post(endpoint, json=body, headers=a["headers"])
        if kind == "resolved":
            assert "attention" not in response.json()
        first = unwrap(response)
        again = unwrap(client.post(endpoint, json=body, headers=a["headers"]))
        assert first["id"] == again["id"]
    assert not unwrap(client.get(f"/api/projects/{pid}/inbox", headers=a["headers"]))["items"]
    assert query_one("SELECT COUNT(*) FROM inbox_receipts WHERE request_id=?", (rid,))[0] == 3


def test_private_request_membership_and_deleted_source(client, project):
    pid = project["id"]
    a = project["a"]
    b = project["b"]
    dm = unwrap(client.post(f"/api/projects/{pid}/dms", json={"with": "overseer"}, headers=a["headers"]), 201)
    url = f"/api/projects/{pid}/conversations/{dm['id']}/messages"
    bad = client.post(
        url, json={"body": f"@{b['alias']} private", "requires_ack": True}, headers=a["headers"]
    )
    assert bad.status_code == 422
    msg = unwrap(client.post(url, json={"body": f"@{a['alias']} secret", "requires_ack": True}), 201)
    assert not unwrap(client.get(f"/api/projects/{pid}/inbox", headers=b["headers"]))["items"]
    client.delete(f"/api/projects/{pid}/messages/{msg['id']}")
    assert not unwrap(client.get(f"/api/projects/{pid}/inbox", headers=a["headers"]))["items"]
