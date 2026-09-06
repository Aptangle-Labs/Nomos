import json
from typing import Literal

from fastapi import APIRouter, Header, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from ..auth import Actor, ActorDep, AgentDep
from ..context import build_pack, checkpoint_view, resolve_source
from ..db import get_conn, utc_now
from ..errors import ApiError, ok
from ..operations import event_record, mutate, project_guard, ticket_row

router = APIRouter(tags=["context"])


class SessionCreate(BaseModel):
    runner: str = Field(default="", max_length=100)
    model: str = Field(default="", max_length=100)


class Source(BaseModel):
    kind: Literal["message", "document", "ticket"]
    id: int = Field(gt=0)
    revision: int | None = Field(default=None, ge=0)


class Checkpoint(BaseModel):
    workspace_id: int | None = Field(default=None, gt=0)
    source_commit: str = Field(default="", max_length=64, pattern=r"^(|[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$")
    worktree_fingerprint: str = Field(default="", max_length=64, pattern=r"^(|[a-fA-F0-9]{64})$")
    purpose: str = Field(min_length=1, max_length=2000)
    completed: str = Field(default="", max_length=10000)
    next_action: str = Field(min_length=1, max_length=2000)
    blockers: list[str] = Field(default_factory=list, max_length=20)
    sources: list[Source] = Field(default_factory=list, max_length=20)
    ticket_number: int | None = Field(default=None, gt=0)
    session_id: int | None = Field(default=None, gt=0)
    shared: bool = False


@router.post("/projects/{project_id}/sessions", status_code=201)
async def session(
    project_id: int,
    body: SessionCreate,
    actor: Actor = AgentDep,
    key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    def change(conn):
        cur = conn.execute(
            "INSERT INTO agent_sessions(project_id,agent_id,agent_alias,runner,model,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (project_id, actor.agent_id, actor.alias, body.runner, body.model, utc_now()),
        )
        return {
            "id": cur.lastrowid,
            "runner": body.runner,
            "model": body.model,
            "provenance": "agent_reported",
        }

    return await mutate(actor, project_id, "session", body.model_dump(), key, change)


@router.post("/projects/{project_id}/sessions/{session_id}/end")
async def end_session(project_id: int, session_id: int, actor: Actor = AgentDep):
    def change(conn):
        cur = conn.execute(
            "UPDATE agent_sessions SET ended_at=COALESCE(ended_at,?) WHERE id=? AND project_id=? AND agent_id=?",
            (utc_now(), session_id, project_id, actor.agent_id),
        )
        if not cur.rowcount:
            raise ApiError(404, "not_found", "Session not found.")
        return {"id": session_id, "ended": True}

    return await mutate(actor, project_id, "end_session", {}, None, change)


@router.post("/projects/{project_id}/checkpoints", status_code=201)
async def checkpoint(
    project_id: int,
    body: Checkpoint,
    actor: Actor = AgentDep,
    key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    def change(conn):
        if body.ticket_number is not None:
            ticket_row(conn, project_id, body.ticket_number)
        if (
            body.session_id is not None
            and not conn.execute(
                "SELECT 1 FROM agent_sessions WHERE id=? AND project_id=? AND agent_id=? AND ended_at IS NULL",
                (body.session_id, project_id, actor.agent_id),
            ).fetchone()
        ):
            raise ApiError(422, "invalid_session", "An active session belonging to this agent is required.")
        if body.workspace_id is not None:
            from ..workspaces import workspace_row

            workspace_row(conn, project_id, body.workspace_id)
        refs = []
        for source in body.sources:
            ref = source.model_dump()
            resolved = resolve_source(conn, project_id, actor, ref, shared=body.shared)
            if "revision" in resolved and ref["revision"] is None:
                ref["revision"] = resolved["revision"]
            refs.append(ref)
        if sum(len(b) for b in body.blockers) > 4000:
            raise ApiError(422, "too_large", "Blockers exceed 4000 characters.")
        cur = conn.execute(
            "INSERT INTO work_checkpoints(project_id,agent_id,agent_alias,session_id,ticket_number,"
            "purpose,completed,next_action,blockers,source_refs,shared,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                project_id,
                actor.agent_id,
                actor.alias,
                body.session_id,
                body.ticket_number,
                body.purpose,
                body.completed,
                body.next_action,
                json.dumps(body.blockers),
                json.dumps(refs),
                body.shared,
                utc_now(),
            ),
        )
        conn.execute(
            "UPDATE work_checkpoints SET workspace_id=?,source_commit=?,worktree_fingerprint=? WHERE id=?",
            (body.workspace_id, body.source_commit, body.worktree_fingerprint, cur.lastrowid),
        )
        # Private by default; shared records still require source checks at read time.
        event_record(
            conn, project_id, actor, "checkpoint_created", {"id": cur.lastrowid}, target=actor.agent_id
        )
        return {"id": cur.lastrowid, "shared": body.shared}

    return await mutate(actor, project_id, "checkpoint", body.model_dump(), key, change)


@router.get("/projects/{project_id}/checkpoints")
def checkpoints(
    project_id: int,
    actor: Actor = ActorDep,
    limit: int = Query(50, ge=1, le=200),
    before_id: int | None = None,
):
    conn = get_conn()
    project_guard(conn, actor, project_id, write=False)
    # Scan in bounded pages. Cursor advances across hidden sources without leaking counts.
    rows = conn.execute(
        "SELECT * FROM work_checkpoints WHERE project_id=? AND (? OR agent_id=? OR shared=1) "
        "AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT ?",
        (project_id, actor.is_admin, actor.agent_id, before_id, before_id, limit + 1),
    ).fetchall()
    items = []
    for row in rows[:limit]:
        try:
            items.append(checkpoint_view(conn, project_id, actor, row))
        except ApiError:
            pass
    return ok(
        {
            "items": items,
            "has_more": len(rows) > limit,
            "next_before_id": rows[limit - 1]["id"] if len(rows) > limit else None,
        }
    )


@router.get("/projects/{project_id}/context")
def context(
    project_id: int,
    actor: Actor = ActorDep,
    ticket_number: int | None = None,
    format: Literal["json", "markdown"] = "json",
    max_bytes: int = Query(32000, ge=1024, le=128000),
):
    conn = get_conn()
    conn.execute("BEGIN")
    try:
        project_guard(conn, actor, project_id, write=False)
        if ticket_number is not None:
            ticket_row(conn, project_id, ticket_number)
        result = build_pack(conn, project_id, actor, ticket_number, max_bytes)
    finally:
        conn.rollback()  # short read snapshot, never a write transaction
    if format == "markdown":
        from ..context import pack_markdown

        return PlainTextResponse(
            pack_markdown(result, max_bytes),
            media_type="text/markdown",
            headers={"X-Nomos-Snapshot-Event-ID": str(result["snapshot_event_id"])},
        )
    return ok(result)
