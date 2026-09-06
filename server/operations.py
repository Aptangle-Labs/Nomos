"""Short, replay-safe transactions shared by the collaboration APIs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import Callable
from typing import Any

from . import audit
from .auth import Actor
from .db import transaction, utc_now
from .errors import ApiError, ok
from .events import append_event, notify


def project_guard(conn: sqlite3.Connection, actor: Actor, pid: int, *, write: bool = True):
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "Project not found.")
    if not actor.is_admin:
        agent = conn.execute("SELECT * FROM agents WHERE id = ?", (actor.agent_id,)).fetchone()
        if agent is None or agent["revoked"]:
            raise ApiError(401, "invalid_key", "Agent is removed or revoked.")
        if actor.project_id != pid or agent["project_id"] != pid:
            raise ApiError(403, "forbidden", "Your key is not valid for this project.")
    if write and row["archived"]:
        raise ApiError(409, "archived", "This project is archived.")
    return row


def revision_guard(row, expected: int | None) -> None:
    if expected is not None and row["revision"] != expected:
        raise ApiError(
            409,
            "revision_conflict",
            "Reload the current record before retrying.",
            {"current_revision": row["revision"]},
        )


def event_record(
    conn,
    pid: int,
    actor: Actor,
    event: str,
    payload: dict,
    *,
    target: int | None = None,
    conversation: int | None = None,
):
    append_event(conn, pid, event, payload, conversation_id=conversation, target_agent_id=target)
    # Audit is project-readable. Store only record IDs, never private content.
    audit.platform_record(
        conn,
        pid,
        "other",
        event.replace("_", " "),
        actor=actor.alias,
        detail={"record_id": payload.get("id"), "event": event},
    )


def replay(conn, pid, actor_id, operation, key, body):
    if key is None:
        return None
    if not key.strip() or len(key) > 128:
        raise ApiError(422, "invalid_idempotency_key", "Key must contain 1–128 characters.")
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    row = conn.execute(
        "SELECT * FROM idempotency_requests WHERE project_id=? AND actor_id=? "
        "AND operation=? AND request_key=?",
        (pid, actor_id, operation, key),
    ).fetchone()
    if row:
        if row["request_hash"] != digest:
            raise ApiError(409, "idempotency_conflict", "This key was used for a different request.")
        return {"cached": json.loads(row["result"]), "hash": digest}
    return {"hash": digest}


def save_replay(conn, pid, actor_id, operation, key, state, result):
    if key is not None:
        conn.execute(
            "INSERT INTO idempotency_requests VALUES (?,?,?,?,?,?,?)",
            (pid, actor_id, operation, key, state["hash"], json.dumps(result), utc_now()),
        )


def _mutate(
    actor: Actor,
    pid: int,
    operation: str,
    body: dict,
    key: str | None,
    change: Callable[[sqlite3.Connection], dict[str, Any]],
) -> dict:
    with transaction() as conn:
        project_guard(conn, actor, pid)
        state = replay(conn, pid, actor.agent_id, operation, key, body)
        if state is not None and "cached" in state:
            return state["cached"] | {"replayed": True}
        result = change(conn)
        save_replay(conn, pid, actor.agent_id, operation, key, state, result)
        return result


async def mutate(actor, pid, operation, body, key, change):
    result = await asyncio.to_thread(_mutate, actor, pid, operation, body, key, change)
    await notify(pid)
    if not actor.is_admin:
        from .auth import _admin_attention
        from .errors import attention_var
        attention_var.set(await asyncio.to_thread(_admin_attention, pid, actor.agent_id))
    return ok(result)


def ticket_row(conn, pid, number):
    row = conn.execute("SELECT * FROM tickets WHERE project_id=? AND number=?", (pid, number)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "Ticket not found.")
    return row


def page(rows, limit=100):
    items = [dict(r) for r in rows[:limit]]
    return {
        "items": items,
        "has_more": len(rows) > limit,
        "next_before_id": items[-1]["id"] if len(rows) > limit else None,
    }
