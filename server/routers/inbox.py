from typing import Literal

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, Field

from ..auth import Actor, ActorDep, AgentDep
from ..db import get_conn, utc_now
from ..errors import ApiError, ok
from ..inbox import get_request, list_inbox
from ..operations import event_record, mutate, project_guard

router = APIRouter(tags=["inbox"])


class Consumer(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class Receipt(BaseModel):
    kind: Literal["delivered", "acknowledged", "resolved"]
    reason: str = Field(default="", max_length=2000)


class Cancel(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


@router.post("/projects/{project_id}/consumers")
async def consumer(
    project_id: int,
    body: Consumer,
    actor: Actor = AgentDep,
    key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    def change(conn):
        conn.execute(
            "INSERT INTO inbox_consumers(project_id,agent_id,name,heartbeat_at) VALUES(?,?,?,?) "
            "ON CONFLICT(project_id,agent_id,name) DO UPDATE SET heartbeat_at=excluded.heartbeat_at",
            (project_id, actor.agent_id, body.name, utc_now()),
        )
        return dict(
            conn.execute(
                "SELECT * FROM inbox_consumers WHERE project_id=? AND agent_id=? AND name=?",
                (project_id, actor.agent_id, body.name),
            ).fetchone()
        )

    return await mutate(actor, project_id, "consumer", body.model_dump(), key, change)


@router.post("/projects/{project_id}/consumers/{consumer_id}/heartbeat")
async def heartbeat(project_id: int, consumer_id: int, actor: Actor = AgentDep):
    def change(conn):
        now = utc_now()
        cur = conn.execute(
            "UPDATE inbox_consumers SET heartbeat_at=? WHERE id=? AND project_id=? AND agent_id=?",
            (now, consumer_id, project_id, actor.agent_id),
        )
        if not cur.rowcount:
            raise ApiError(404, "not_found", "Consumer not found.")
        return {"id": consumer_id, "heartbeat_at": now}

    return await mutate(actor, project_id, "heartbeat", {}, None, change)


@router.get("/projects/{project_id}/inbox")
def inbox(
    project_id: int,
    actor: Actor = ActorDep,
    limit: int = Query(100, ge=1, le=200),
    before_id: int | None = None,
    outstanding: bool = True,
):
    conn = get_conn()
    project_guard(conn, actor, project_id, write=False)
    return ok(list_inbox(conn, project_id, actor, limit, before_id, outstanding))


@router.post("/projects/{project_id}/inbox/{request_id}/receipts")
async def receipt(
    project_id: int,
    request_id: int,
    body: Receipt,
    actor: Actor = AgentDep,
    key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    def change(conn):
        row = get_request(conn, project_id, actor, request_id)
        if row["target_agent_id"] != actor.agent_id:
            raise ApiError(403, "target_only", "Only the recipient can acknowledge this request.")
        existing = conn.execute(
            "SELECT * FROM inbox_receipts WHERE request_id=? AND kind=?", (request_id, body.kind)
        ).fetchone()
        if existing:
            if existing["reason"] != body.reason:
                raise ApiError(
                    409, "receipt_conflict", "This receipt is already recorded with another reason."
                )
            return dict(existing)
        if row["state"] in ("resolved", "cancelled"):
            raise ApiError(409, "request_closed", "This request is closed.")
        if body.kind == "resolved" and not body.reason.strip():
            raise ApiError(422, "resolution_required", "Include a reply link or resolution reason.")
        now = utc_now()
        cur = conn.execute(
            "INSERT INTO inbox_receipts(project_id,request_id,kind,actor_id,reason,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (project_id, request_id, body.kind, actor.agent_id, body.reason, now),
        )
        state = row["state"] if body.kind == "delivered" else body.kind
        conn.execute(
            "UPDATE inbox_requests SET state=?,updated_at=?,revision=revision+1 WHERE id=?",
            (state, now, request_id),
        )
        event_record(conn, project_id, actor, "inbox_updated", {"id": request_id}, target=row["issuer_id"])
        return dict(conn.execute("SELECT * FROM inbox_receipts WHERE id=?", (cur.lastrowid,)).fetchone())

    # Reauthorize the source before a cached receipt result can be replayed.
    get_request(get_conn(), project_id, actor, request_id)
    return await mutate(actor, project_id, f"receipt:{request_id}", body.model_dump(), key, change)


@router.post("/projects/{project_id}/inbox/{request_id}/cancel")
async def cancel(
    project_id: int,
    request_id: int,
    body: Cancel,
    actor: Actor = ActorDep,
    key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    def change(conn):
        row = get_request(conn, project_id, actor, request_id)
        if not actor.is_admin and actor.agent_id != row["issuer_id"]:
            raise ApiError(403, "issuer_only", "Only the issuer or admin may cancel.")
        if row["state"] in ("resolved", "cancelled"):
            raise ApiError(409, "request_closed", "This request is closed.")
        now = utc_now()
        conn.execute(
            "UPDATE inbox_requests SET state='cancelled',updated_at=?,revision=revision+1 WHERE id=?",
            (now, request_id),
        )
        conn.execute(
            "INSERT INTO inbox_receipts(project_id,request_id,kind,actor_id,reason,created_at) "
            "VALUES(?,?,'cancelled',?,?,?)",
            (project_id, request_id, actor.agent_id, body.reason, now),
        )
        event_record(
            conn, project_id, actor, "inbox_updated", {"id": request_id}, target=row["target_agent_id"]
        )
        return {"id": request_id, "state": "cancelled"}

    return await mutate(actor, project_id, f"cancel:{request_id}", body.model_dump(), key, change)
