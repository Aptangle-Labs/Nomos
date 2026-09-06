import io
import json
import tarfile

from .conftest import unwrap


def test_checkpoint_pins_document_and_pack_is_bounded(client, project):
    pid = project["id"]
    a = project["a"]
    h = a["headers"]
    doc = unwrap(
        client.post(
            f"/api/projects/{pid}/documents", json={"title": "Design", "body": "original"}, headers=h
        ),
        201,
    )
    cp = {
        "purpose": "Resume parser",
        "next_action": "Add checksum test",
        "sources": [{"kind": "document", "id": doc["id"]}],
        "shared": True,
    }
    first = unwrap(
        client.post(f"/api/projects/{pid}/checkpoints", json=cp, headers=h | {"Idempotency-Key": "cp"}), 201
    )
    again = unwrap(
        client.post(f"/api/projects/{pid}/checkpoints", json=cp, headers=h | {"Idempotency-Key": "cp"}), 201
    )
    assert first["id"] == again["id"]
    unwrap(
        client.put(
            f"/api/projects/{pid}/documents/{doc['slug']}",
            json={"body": "new", "base_revision": 1},
            headers=h,
        )
    )
    pack = unwrap(client.get(f"/api/projects/{pid}/context", headers=h))
    checkpoint = next(s["items"][0] for s in pack["sections"] if s["kind"] == "checkpoint")
    assert checkpoint["next_action"] == "Add checksum test"
    assert checkpoint["sources"][0]["body"] == "original" and checkpoint["sources"][0]["changed"]
    client.put("/api/me/scratchpad", json={"body": "🦊" * 4000}, headers=h)
    small = unwrap(client.get(f"/api/projects/{pid}/context?max_bytes=1024", headers=h))
    assert len(json.dumps(small, ensure_ascii=False).encode()) <= 1024 and small["truncated"]
    cursor = pack["snapshot_event_id"]
    msg = unwrap(
        client.post(
            f"/api/projects/{pid}/conversations/{project['main_channel_id']}/messages",
            json={"body": "after snapshot"},
            headers=h,
        ),
        201,
    )
    events = unwrap(client.get(f"/api/projects/{pid}/events?since_id={cursor}", headers=h))["items"]
    assert any(e["type"] == "message" and e["payload"]["id"] == msg["id"] for e in events)


def test_checkpoint_private_sources_are_not_promoted(client, project):
    pid = project["id"]
    a = project["a"]
    b = project["b"]
    dm = unwrap(client.post(f"/api/projects/{pid}/dms", json={"with": "overseer"}, headers=a["headers"]), 201)
    msg = unwrap(
        client.post(
            f"/api/projects/{pid}/conversations/{dm['id']}/messages",
            json={"body": "secret note"},
            headers=a["headers"],
        ),
        201,
    )
    cp = {
        "purpose": "private",
        "next_action": "private next",
        "sources": [{"kind": "message", "id": msg["id"]}],
        "shared": True,
    }
    assert client.post(f"/api/projects/{pid}/checkpoints", json=cp, headers=a["headers"]).status_code == 422
    cp["shared"] = False
    unwrap(client.post(f"/api/projects/{pid}/checkpoints", json=cp, headers=a["headers"]), 201)
    other = client.get(f"/api/projects/{pid}/context", headers=b["headers"]).text
    assert "secret note" not in other and "private next" not in other
    assert not unwrap(client.get(f"/api/projects/{pid}/checkpoints", headers=b["headers"]))["items"]


def test_sessions_do_not_release_assignment(client, project):
    pid = project["id"]
    h = project["a"]["headers"]
    session = unwrap(client.post(f"/api/projects/{pid}/sessions", json={"runner": "test"}, headers=h), 201)
    assert (
        client.post(
            f"/api/projects/{pid}/sessions/{session['id']}/end", headers=project["b"]["headers"]
        ).status_code
        == 404
    )
    unwrap(client.post(f"/api/projects/{pid}/sessions/{session['id']}/end", headers=h))
    cp = {"purpose": "x", "next_action": "y", "session_id": session["id"]}
    assert client.post(f"/api/projects/{pid}/checkpoints", json=cp, headers=h).status_code == 422


def test_export_keeps_working_notes_without_keys(client, project):
    pid = project['id']
    a = project['a']
    unwrap(client.put('/api/me/scratchpad', json={'body': 'resume me'}, headers=a['headers']))
    unwrap(client.post('/api/me/todos', json={'text': 'finish test'}, headers=a['headers']), 201)
    response = client.post(f'/api/projects/{pid}/export')
    assert response.status_code == 200
    with tarfile.open(fileobj=io.BytesIO(response.content), mode='r:gz') as archive:
        notes = json.load(archive.extractfile('working_notes.json'))
        todos = json.load(archive.extractfile('agent_todos.json'))
        agents = archive.extractfile('agents.json').read().decode()
        assert any(n['body'] == 'resume me' for n in notes)
        assert any(t['text'] == 'finish test' for t in todos)
        assert a['key'] not in agents and 'api_key_hash' not in agents
        assert json.load(archive.extractfile('manifest.json'))['format_version'] == 2


def test_markdown_context_renders_decision_question(client, project):
    """Markdown rendering must include the fields sections actually use:
    a pending decision's question may not vanish into an empty heading."""
    pid = project["id"]
    a = project["a"]
    unwrap(
        client.post(
            f"/api/projects/{pid}/decision-requests",
            json={"question": "Ship the beta now?", "options": ["yes", "no"]},
            headers=a["headers"],
        ),
        201,
    )
    md = client.get(f"/api/projects/{pid}/context?format=markdown", headers=a["headers"]).text
    assert "Ship the beta now?" in md
