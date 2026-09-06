import json

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, Field

from ..auth import Actor, ActorDep, AgentDep
from ..context import checkpoint_view
from ..db import get_conn, utc_now
from ..errors import ApiError, ok
from ..operations import event_record, mutate, project_guard, revision_guard, ticket_row
from ..work import blockers, ownership_guard, require_ready, validate_graph

router = APIRouter(tags=["coordination"])


class Dependency(BaseModel):
    prerequisite_number: int = Field(gt=0)
    waived: bool = False
    waiver_reason: str = Field(default="", max_length=2000)


class Dependencies(BaseModel):
    expected_revision: int = Field(ge=0)
    dependencies: list[Dependency] = Field(default_factory=list, max_length=100)


class ClaimNext(BaseModel):
    label: str | None = Field(default=None, max_length=100)
    session_id: int | None = None


class Handoff(BaseModel):
    expected_revision: int = Field(ge=0)
    claim_generation: int = Field(ge=0)
    to_agent_id: int = Field(gt=0)
    checkpoint_id: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=2000)


class Release(BaseModel):
    expected_revision: int = Field(ge=0)
    claim_generation: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=2000)


class Accept(BaseModel):
    session_id: int | None = None


def ticket_payload(row):
    data = dict(row)
    data["labels"] = json.loads(data["labels"])
    return data


def claim(conn, pid, actor, row, session_id=None):
    require_ready(conn, row)
    if row["assignee"] is not None:
        raise ApiError(409, "already_claimed", "Ticket is already assigned.", {"assignee": row["assignee"]})
    if (
        session_id is not None
        and not conn.execute(
            "SELECT 1 FROM agent_sessions WHERE id=? AND agent_id=? AND project_id=? AND ended_at IS NULL",
            (session_id, actor.agent_id, pid),
        ).fetchone()
    ):
        raise ApiError(422, "invalid_session", "An active owned session is required.")
    conn.execute(
        "UPDATE tickets SET assignee=?,owner_agent_id=?,managed_claim=1,claim_generation=claim_generation+1,"
        "claim_session_id=?,status=CASE WHEN status='open' THEN 'in-progress' ELSE status END,"
        "updated_at=?,changed_by=? WHERE id=?",
        (actor.alias, actor.agent_id, session_id, utc_now(), actor.alias, row["id"]),
    )
    updated = ticket_payload(ticket_row(conn, pid, row["number"]))
    event_record(conn, pid, actor, "ticket_updated", {"id": row["id"], "ticket": updated, "by": actor.alias})
    return updated


@router.post("/projects/{project_id}/tickets/claim-next")
async def claim_next(
    project_id: int,
    body: ClaimNext,
    actor: Actor = AgentDep,
    key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    def change(conn):
        row = conn.execute(
            "SELECT t.* FROM tickets t WHERE t.project_id=? AND t.status='open' AND t.assignee IS NULL "
            "AND (? IS NULL OR EXISTS(SELECT 1 FROM json_each(t.labels) WHERE value=?)) "
            "AND NOT EXISTS(SELECT 1 FROM ticket_dependencies d JOIN tickets p ON p.project_id=d.project_id "
            "AND p.number=d.prerequisite_number WHERE d.project_id=t.project_id AND d.ticket_number=t.number AND d.waived=0 AND p.status!='done') "
            "ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,t.number LIMIT 1",
            (project_id, body.label, body.label),
        ).fetchone()
        return {"ticket": claim(conn, project_id, actor, row, body.session_id) if row else None}

    return await mutate(actor, project_id, "claim-next", body.model_dump(), key, change)


@router.get("/projects/{project_id}/tickets/{number}/dependencies")
def dependencies(project_id: int, number: int, actor: Actor = ActorDep):
    conn = get_conn()
    project_guard(conn, actor, project_id, write=False)
    row = ticket_row(conn, project_id, number)
    edges = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM ticket_dependencies WHERE project_id=? AND ticket_number=?", (project_id, number)
        )
    ]
    return ok({"items": edges, "blockers": blockers(conn, project_id, number), "revision": row["revision"]})


@router.put("/projects/{project_id}/tickets/{number}/dependencies")
async def set_dependencies(
    project_id: int,
    number: int,
    body: Dependencies,
    actor: Actor = ActorDep,
    key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    def change(conn):
        row = ticket_row(conn, project_id, number)
        revision_guard(row, body.expected_revision)
        edges = [e.model_dump() for e in body.dependencies]
        if len({e["prerequisite_number"] for e in edges}) != len(edges):
            raise ApiError(422, "duplicate_dependency", "A prerequisite may appear only once.")
        for edge in edges:
            ticket_row(conn, project_id, edge["prerequisite_number"])
            if edge["waived"] and not edge["waiver_reason"].strip():
                raise ApiError(422, "waiver_reason_required", "Explain each waived dependency.")
        validate_graph(conn, project_id, number, edges)
        conn.execute(
            "DELETE FROM ticket_dependencies WHERE project_id=? AND ticket_number=?", (project_id, number)
        )
        for edge in edges:
            conn.execute(
                "INSERT INTO ticket_dependencies VALUES(?,?,?,?,?)",
                (project_id, number, edge["prerequisite_number"], edge["waived"], edge["waiver_reason"]),
            )
        conn.execute(
            "UPDATE tickets SET updated_at=?,changed_by=? WHERE id=?", (utc_now(), actor.alias, row["id"])
        )
        result = ticket_payload(ticket_row(conn, project_id, number))
        event_record(
            conn, project_id, actor, "ticket_updated", {"id": row["id"], "ticket": result, "by": actor.alias}
        )
        return {"ticket": result, "dependencies": edges}

    return await mutate(actor, project_id, f"dependencies:{number}", body.model_dump(), key, change)


@router.post("/projects/{project_id}/tickets/{number}/handoffs", status_code=201)
async def handoff(
    project_id: int,
    number: int,
    body: Handoff,
    actor: Actor = ActorDep,
    key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    def change(conn):
        row = ticket_row(conn, project_id, number)
        ownership_guard(row, actor, body.expected_revision, body.claim_generation)
        if not row["managed_claim"] or row["owner_agent_id"] is None:
            raise ApiError(409, "managed_claim_required", "Claim managed work before offering a handoff.")
        target = conn.execute(
            "SELECT * FROM agents WHERE id=? AND project_id=? AND revoked=0", (body.to_agent_id, project_id)
        ).fetchone()
        if target is None or target["id"] == row["owner_agent_id"]:
            raise ApiError(422, "invalid_recipient", "Choose a different active project agent.")
        cp = conn.execute(
            "SELECT * FROM work_checkpoints WHERE id=? AND project_id=? AND ticket_number=?",
            (body.checkpoint_id, project_id, number),
        ).fetchone()
        if cp is None or not cp["shared"]:
            raise ApiError(
                422, "shared_checkpoint_required", "Handoff requires a shared checkpoint for this ticket."
            )
        checkpoint_view(conn, project_id, Actor("agent", target["id"], project_id, target["alias"]), cp)
        now = utc_now()
        conn.execute(
            "UPDATE ticket_handoffs SET state='invalidated',resolved_at=? "
            "WHERE project_id=? AND ticket_id=? AND state='pending'",
            (now, project_id, row["id"]),
        )
        cur = conn.execute(
            "INSERT INTO ticket_handoffs(project_id,ticket_id,from_agent_id,to_agent_id,checkpoint_id,"
            "expected_revision,expected_generation,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                project_id,
                row["id"],
                row["owner_agent_id"],
                body.to_agent_id,
                body.checkpoint_id,
                row["revision"],
                row["claim_generation"],
                body.reason,
                now,
            ),
        )
        event_record(
            conn,
            project_id,
            actor,
            "handoff_updated",
            {"id": cur.lastrowid, "ticket_number": number},
            target=target["id"],
        )
        return {"id": cur.lastrowid, "state": "pending"}

    return await mutate(actor, project_id, f"handoff:{number}", body.model_dump(), key, change)


@router.get("/projects/{project_id}/handoffs")
def handoffs(
    project_id: int,
    actor: Actor = ActorDep,
    before_id: int | None = None,
    limit: int = Query(100, ge=1, le=200),
):
    from ..operations import page

    conn = get_conn()
    project_guard(conn, actor, project_id, write=False)
    return ok(
        page(
            conn.execute(
                "SELECT h.*,t.number AS ticket_number FROM ticket_handoffs h JOIN tickets t ON t.id=h.ticket_id "
                "WHERE h.project_id=? AND (? OR h.from_agent_id=? OR h.to_agent_id=?) "
                "AND (? IS NULL OR h.id<?) ORDER BY h.id DESC LIMIT ?",
                (project_id, actor.is_admin, actor.agent_id, actor.agent_id, before_id, before_id, limit + 1),
            ).fetchall(),
            limit,
        )
    )


@router.post("/projects/{project_id}/handoffs/{handoff_id}/accept")
async def accept(
    project_id: int,
    handoff_id: int,
    body: Accept | None = None,
    actor: Actor = AgentDep,
    key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    session_id = body.session_id if body else None

    def change(conn):
        offer = conn.execute(
            "SELECT * FROM ticket_handoffs WHERE id=? AND project_id=? AND to_agent_id=?",
            (handoff_id, project_id, actor.agent_id),
        ).fetchone()
        if offer is None:
            raise ApiError(404, "not_found", "Handoff not found.")
        if offer["state"] != "pending":
            raise ApiError(409, "handoff_closed", "This offer is no longer pending.")
        row = conn.execute("SELECT * FROM tickets WHERE id=?", (offer["ticket_id"],)).fetchone()
        revision_guard(row, offer["expected_revision"])
        if (
            row["claim_generation"] != offer["expected_generation"]
            or row["owner_agent_id"] != offer["from_agent_id"]
        ):
            raise ApiError(409, "ownership_changed", "Ownership changed since the offer.")
        cp = conn.execute("SELECT * FROM work_checkpoints WHERE id=?", (offer["checkpoint_id"],)).fetchone()
        checkpoint_view(conn, project_id, actor, cp)
        if (
            session_id is not None
            and not conn.execute(
                "SELECT 1 FROM agent_sessions WHERE id=? AND agent_id=? AND project_id=? AND ended_at IS NULL",
                (session_id, actor.agent_id, project_id),
            ).fetchone()
        ):
            raise ApiError(422, "invalid_session", "An active owned session is required.")
        now = utc_now()
        conn.execute(
            "UPDATE tickets SET assignee=?,owner_agent_id=?,claim_generation=claim_generation+1,claim_session_id=?,"
            "updated_at=?,changed_by=? WHERE id=?",
            (actor.alias, actor.agent_id, session_id, now, actor.alias, row["id"]),
        )
        # Transition trigger invalidates pending offers; restore this one as accepted.
        conn.execute(
            "UPDATE ticket_handoffs SET state='accepted',resolved_at=? WHERE id=?", (now, handoff_id)
        )
        result = ticket_payload(ticket_row(conn, project_id, row["number"]))
        event_record(
            conn, project_id, actor, "ticket_updated", {"id": row["id"], "ticket": result, "by": actor.alias}
        )
        return {"ticket": result, "checkpoint_id": offer["checkpoint_id"]}

    # A bodiless accept hashes {} exactly as it did before session support,
    # so idempotency records from earlier accepts still replay cleanly.
    body_fingerprint = {} if session_id is None else {"session_id": session_id}
    return await mutate(actor, project_id, f"accept:{handoff_id}", body_fingerprint, key, change)


@router.post("/projects/{project_id}/tickets/{number}/release")
async def release(
    project_id: int,
    number: int,
    body: Release,
    actor: Actor = ActorDep,
    key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    def change(conn):
        row = ticket_row(conn, project_id, number)
        ownership_guard(row, actor, body.expected_revision, body.claim_generation)
        if not actor.is_admin and row["assignee"] != actor.alias:
            raise ApiError(403, "owner_only", "Only the owner or admin can release work.")
        conn.execute(
            "UPDATE tickets SET assignee=NULL,owner_agent_id=NULL,claim_generation=claim_generation+1,"
            "managed_claim=0,claim_session_id=NULL,updated_at=?,changed_by=? WHERE id=?",
            (utc_now(), actor.alias, row["id"]),
        )
        result = ticket_payload(ticket_row(conn, project_id, number))
        event_record(
            conn,
            project_id,
            actor,
            "ticket_updated",
            {"id": row["id"], "ticket": result, "by": actor.alias, "reason": body.reason},
        )
        return {"ticket": result}

    return await mutate(actor, project_id, f"release:{number}", body.model_dump(), key, change)
