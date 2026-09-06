import json
import time
from concurrent.futures import ThreadPoolExecutor

from server import fsmonitor

from .conftest import unwrap


def register(client, pid, root, name="checkout"):
    root.mkdir(exist_ok=True)
    return unwrap(client.post(f"/api/projects/{pid}/workspaces", json={"name": name, "root": str(root)}), 201)


def test_reservations_overlap_symlinks_and_race(client, project, tmp_path):
    pid = project["id"]
    root = tmp_path / "repo"
    w = register(client, pid, root)
    base = f"/api/projects/{pid}/reservations"
    body = {"workspace_id": w["id"], "path": "src", "is_directory": True, "reason": "Implement parser"}
    with ThreadPoolExecutor(2) as pool:
        responses = list(
            pool.map(
                lambda h: client.post(base, json=body, headers=h),
                [project["a"]["headers"], project["b"]["headers"]],
            )
        )
    assert sorted(r.status_code for r in responses) == [201, 409]
    reservation = next(r.json()["data"] for r in responses if r.status_code == 201)
    for path in ("src/parser.py", "../escape", "/absolute"):
        assert client.post(
            base, json=body | {"path": path, "is_directory": False}, headers=project["a"]["headers"]
        ).status_code in (409, 422)
    assert (
        client.post(
            base, json=body | {"path": "src2/file.py", "is_directory": False}, headers=project["a"]["headers"]
        ).status_code
        == 201
    )
    other = register(client, pid, tmp_path / "other")
    assert (
        client.post(
            base, json=body | {"workspace_id": other["id"]}, headers=project["a"]["headers"]
        ).status_code
        == 201
    )
    (root / "escape").symlink_to(tmp_path / "other", target_is_directory=True)
    assert (
        client.post(base, json=body | {"path": "escape/file"}, headers=project["a"]["headers"]).status_code
        == 422
    )
    update = {"expected_revision": 0, "to_agent_id": project["b"]["id"], "reason": "Runner stopped; transfer"}
    unwrap(client.post(base + f"/{reservation['id']}/update", json=update))
    assert (
        client.post(
            base + f"/{reservation['id']}/update",
            json={"expected_revision": 1, "reason": "old owner"},
            headers=project["a"]["headers"],
        ).status_code
        == 403
    )
    unwrap(
        client.post(
            base + f"/{reservation['id']}/update",
            json={"expected_revision": 1, "reason": "finished"},
            headers=project["b"]["headers"],
        )
    )


def test_multiple_monitors_isolate_correlation_and_stop_on_delete(client, project, tmp_path, monkeypatch):
    monkeypatch.setattr(fsmonitor, "POLL_SECONDS", 0.03)
    pid = project["id"]
    roots = [tmp_path / "one", tmp_path / "two"]
    workspaces = [register(client, pid, root) for root in roots]
    for root in roots:
        (root / "same.txt").write_text("before")
    for w in workspaces:
        unwrap(client.put(f"/api/projects/{pid}/workspaces/{w['id']}/watch", json={"enabled": True}))
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not all(w["id"] in fsmonitor._last_scan for w in workspaces):
        time.sleep(0.02)
    assert all(fsmonitor.is_active(w["id"]) for w in workspaces)
    unwrap(
        client.post(
            f"/api/projects/{pid}/audit",
            json={
                "workspace_id": workspaces[0]["id"],
                "action": "file_edit",
                "target": "same.txt",
                "summary": "claim one only",
            },
            headers=project["a"]["headers"],
        ),
        201,
    )
    for root in roots:
        (root / "same.txt").write_text("after")
    rows = []
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        rows = unwrap(client.get(f"/api/projects/{pid}/audit?source=monitor"))["items"]
        if len(rows) >= 2:
            break
        time.sleep(0.02)
    for r in rows:
        r["detail"] = json.loads(r["detail"])
    by_workspace = {r["detail"]["workspace_id"]: r for r in rows}
    assert by_workspace[workspaces[0]["id"]]["claimed"]
    assert not by_workspace[workspaces[1]["id"]]["claimed"]
    assert all(r["detail"]["before_sha256"] != r["detail"]["after_sha256"] for r in rows)
    assert unwrap(client.get(f"/api/projects/{pid}/audit/verify"))["ok"]
    filtered = unwrap(
        client.get(f"/api/projects/{pid}/audit?workspace_id={workspaces[1]['id']}&source=monitor")
    )["items"]
    assert len(filtered) == 1
    assert client.delete(f"/api/projects/{pid}").status_code == 200
    assert not any(fsmonitor.is_active(w["id"]) for w in workspaces)
    assert all((root / "same.txt").read_text() == "after" for root in roots)


def test_legacy_watch_replacement_preserves_other_monitor(client, project, tmp_path):
    pid = project["id"]
    extra = register(client, pid, tmp_path / "extra")
    unwrap(client.put(f"/api/projects/{pid}/workspaces/{extra['id']}/watch", json={"enabled": True}))
    for name in ("legacy1", "legacy2"):
        root = tmp_path / name
        root.mkdir()
        response = unwrap(client.post(f"/api/projects/{pid}/audit/watch", json={"path": str(root)}), 201)
        assert response["watch"]["active"]
    assert fsmonitor.is_active(extra["id"])
    unwrap(client.delete(f"/api/projects/{pid}/audit/watch"))
    assert fsmonitor.is_active(extra["id"])
    unwrap(client.put(f"/api/projects/{pid}/workspaces/{extra['id']}/watch", json={"enabled": False}))


def test_workspace_migration_backfills_without_rewriting_audit(tmp_path):
    import sqlite3
    from pathlib import Path

    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    files = sorted((Path(__file__).resolve().parents[1] / "migrations").glob("*.sql"))
    for migration in files:
        if migration.name.startswith("0013"):
            break
        conn.executescript(migration.read_text())
    conn.execute(
        "INSERT INTO projects(name,created_by,created_at,updated_at,working_dir) VALUES('old','admin','now','now',?)",
        (str(tmp_path),),
    )
    conn.execute("INSERT INTO audit_watches(project_id,path,created_at) VALUES(1,?,'now')", (str(tmp_path),))
    from server import audit

    audit.record(conn, 1, "admin", "admin", "self_report", "command", "historic", detail={"old": True})
    prior = conn.execute("SELECT * FROM audit_log").fetchall()
    conn.executescript(next(p for p in files if p.name.startswith("0013")).read_text())
    assert conn.execute("SELECT * FROM audit_log").fetchall() == prior
    assert conn.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0] == 1
    assert conn.execute("SELECT workspace_id,is_legacy FROM audit_watches").fetchone() == (1, 1)
    assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()
