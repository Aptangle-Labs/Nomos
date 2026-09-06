import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, Field

from ..auth import Actor, ActorDep, AdminDep
from ..db import get_conn, utc_now
from ..errors import ApiError, ok
from ..evidence import check_evidence, completion_guard, evidence_digest, latest_observation, policy_for
from ..operations import event_record, mutate, page, project_guard, revision_guard, ticket_row
from ..services import post_message
from ..work import blockers, ownership_guard

router = APIRouter(tags=["decisions and evidence"])


class DecisionSource(BaseModel):
    kind: Literal["document", "ticket"]
    id: int = Field(gt=0)
    revision: int | None = Field(default=None, ge=0)


class DecisionCreate(BaseModel):
    required_by: datetime | None = None
    sources: list[DecisionSource] = Field(default_factory=list, max_length=20)
    question: str = Field(min_length=1, max_length=10000)
    options: list[str] = Field(min_length=2, max_length=10)
    recommendation: str = Field(default="", max_length=2000)
    ticket_number: int | None = Field(default=None, gt=0)


class DecisionResolve(BaseModel):
    expected_revision: int = Field(ge=0)
    result: str = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=10000)
    cancel: bool = False


class Policy(BaseModel):
    workspace_id: int | None = Field(default=None, gt=0)
    expected_revision: int = Field(ge=0)
    required_kinds: list[Literal["test", "review", "artifact"]] = Field(default_factory=list, max_length=3)
    human_review: bool = False
    source_commit: str = Field(default="", max_length=64, pattern=r"^(|[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$")
    worktree_fingerprint: str = Field(default="", max_length=64, pattern=r"^(|[a-fA-F0-9]{64})$")


class Evidence(BaseModel):
    workspace_id: int | None = Field(default=None, gt=0)
    kind: Literal["test", "review", "artifact"]
    outcome: Literal["pass", "fail", "unknown"]
    summary: str = Field(min_length=1, max_length=10000)
    command: str = Field(default="", max_length=4000)
    exit_code: int | None = None
    source_commit: str = Field(min_length=40, max_length=64, pattern=r"^([a-fA-F0-9]{40}|[a-fA-F0-9]{64})$")
    worktree_fingerprint: str = Field(default="", max_length=64, pattern=r"^(|[a-fA-F0-9]{64})$")
    attachment_id: int | None = Field(default=None, gt=0)


class Proposal(BaseModel):
    expected_revision: int = Field(ge=0)
    claim_generation: int | None = Field(default=None, ge=0)


class Review(BaseModel):
    return_status: Literal["in-progress", "blocked"] | None = None
    approve: bool
    reason: str = Field(min_length=1, max_length=10000)


@router.post("/projects/{project_id}/decision-requests", status_code=201)
async def request_decision(
    project_id: int,
    body: DecisionCreate,
    actor: Actor = ActorDep,
    key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    def change(conn):
        if any(not o.strip() or len(o) > 2000 for o in body.options) or len(set(body.options)) != len(
            body.options
        ):
            raise ApiError(
                422, "invalid_options", "Supply distinct, nonempty options of at most 2000 characters."
            )
        from ..context import resolve_source

        refs = []
        for source in body.sources:
            ref = source.model_dump()
            resolved = resolve_source(conn, project_id, actor, ref, shared=True)
            if ref["revision"] is None and "revision" in resolved:
                ref["revision"] = resolved["revision"]
            refs.append(ref)
        if body.required_by is not None and body.required_by.utcoffset() is None:
            raise ApiError(422, "timezone_required", "Use a timezone-aware due time.")
        ticket = ticket_row(conn, project_id, body.ticket_number) if body.ticket_number else None
        cur = conn.execute(
            "INSERT INTO decision_requests(project_id,ticket_id,requester_id,requester_alias,question,options,recommendation,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                project_id,
                ticket["id"] if ticket else None,
                actor.agent_id,
                actor.alias,
                body.question,
                json.dumps(body.options),
                body.recommendation,
                utc_now(),
            ),
        )
        conn.execute(
            "UPDATE decision_requests SET required_by=?,source_refs=? WHERE id=?",
            (body.required_by.isoformat() if body.required_by else None, json.dumps(refs), cur.lastrowid),
        )
        event_record(conn, project_id, actor, "decision_requested", {"id": cur.lastrowid}, target=0)
        return {"id": cur.lastrowid, "revision": 0, "state": "pending"}

    return await mutate(actor, project_id, "decision", body.model_dump(mode="json"), key, change)


@router.get("/projects/{project_id}/decision-requests")
def decisions(
    project_id: int,
    actor: Actor = ActorDep,
    before_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
):
    conn = get_conn()
    project_guard(conn, actor, project_id, write=False)
    data = page(
        conn.execute(
            "SELECT d.*,t.number AS ticket_number FROM decision_requests d LEFT JOIN tickets t ON t.id=d.ticket_id WHERE d.project_id=? AND (? IS NULL OR d.id<?) ORDER BY d.id DESC LIMIT ?",
            (project_id, before_id, before_id, limit + 1),
        ).fetchall(),
        limit,
    )
    for item in data["items"]:
        item["options"] = json.loads(item["options"])
        item["sources"] = json.loads(item.pop("source_refs"))
    return ok(data)


@router.post("/projects/{project_id}/decision-requests/{decision_id}/resolve")
async def resolve_decision(
    project_id: int,
    decision_id: int,
    body: DecisionResolve,
    actor: Actor = AdminDep,
    key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    def change(conn):
        row = conn.execute(
            "SELECT * FROM decision_requests WHERE id=? AND project_id=?", (decision_id, project_id)
        ).fetchone()
        if row is None:
            raise ApiError(404, "not_found", "Decision not found.")
        revision_guard(row, body.expected_revision)
        if row["state"] != "pending":
            raise ApiError(409, "decision_closed", "This decision has already been resolved.")
        main = conn.execute(
            "SELECT id FROM conversations WHERE project_id=? AND is_main=1", (project_id,)
        ).fetchone()
        if main is None:
            raise ApiError(409, "main_channel_required", "Restore the main channel before resolving.")
        state = "cancelled" if body.cancel else "resolved"
        message = post_message(
            project_id,
            main["id"],
            "admin",
            None,
            actor.alias,
            f"Decision {decision_id} ({state}): {row['question']}\nResult: {body.result}\nReason: {body.reason}",
            msg_type="decision",
            _conn=conn,
        )
        conn.execute(
            "UPDATE decision_requests SET state=?,result=?,reason=?,message_id=?,revision=revision+1,resolved_at=? WHERE id=?",
            (state, body.result, body.reason, message["id"], utc_now(), decision_id),
        )
        event_record(
            conn, project_id, actor, "decision_resolved", {"id": decision_id, "message_id": message["id"]}
        )
        return {"id": decision_id, "state": state, "message_id": message["id"]}

    return await mutate(actor, project_id, f"decision-resolve:{decision_id}", body.model_dump(), key, change)


@router.get("/projects/{project_id}/tickets/{number}/evidence")
def list_evidence(
    project_id: int,
    number: int,
    actor: Actor = ActorDep,
    before_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
):
    conn = get_conn()
    project_guard(conn, actor, project_id, write=False)
    row = ticket_row(conn, project_id, number)
    data = page(
        conn.execute(
            "SELECT * FROM work_evidence WHERE ticket_id=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT ?",
            (row["id"], before_id, before_id, limit + 1),
        ).fetchall(),
        limit,
    )
    policy = policy_for(conn, row)
    policy["required_kinds"] = json.loads(policy["required_kinds"])
    data["policy"] = policy
    data["proposals"] = [
        dict(p)
        | {
            "stale": p["ticket_revision"] != row["revision"]
            or p["observation_id"] != latest_observation(conn, project_id, policy.get("workspace_id"))
        }
        for p in conn.execute(
            "SELECT * FROM completion_proposals WHERE ticket_id=? ORDER BY id DESC LIMIT 20", (row["id"],)
        )
    ]
    return ok(data)


@router.put("/projects/{project_id}/tickets/{number}/completion-policy")
async def set_policy(
    project_id: int,
    number: int,
    body: Policy,
    actor: Actor = AdminDep,
    key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    def change(conn):
        row = ticket_row(conn, project_id, number)
        revision_guard(row, body.expected_revision)
        if body.required_kinds and not body.source_commit:
            raise ApiError(
                422,
                "source_required",
                "Bind required evidence to a source commit and optional dirty worktree fingerprint.",
            )
        if body.workspace_id is not None:
            from ..workspaces import workspace_row

            workspace_row(conn, project_id, body.workspace_id)
        prior = policy_for(conn, row)
        conn.execute(
            "INSERT OR REPLACE INTO completion_policies(ticket_id,project_id,required_kinds,human_review,source_commit,worktree_fingerprint,revision,updated_at,workspace_id) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                row["id"],
                project_id,
                json.dumps(sorted(set(body.required_kinds))),
                body.human_review,
                body.source_commit.lower(),
                body.worktree_fingerprint.lower(),
                prior["revision"] + 1,
                utc_now(),
                body.workspace_id,
            ),
        )
        conn.execute(
            "UPDATE tickets SET updated_at=?,changed_by=? WHERE id=?", (utc_now(), actor.alias, row["id"])
        )
        event_record(conn, project_id, actor, "evidence_updated", {"id": row["id"], "ticket_number": number})
        return {"revision": ticket_row(conn, project_id, number)["revision"]}

    return await mutate(actor, project_id, f"policy:{number}", body.model_dump(), key, change)


@router.post("/projects/{project_id}/tickets/{number}/evidence", status_code=201)
async def add_evidence(
    project_id: int,
    number: int,
    body: Evidence,
    actor: Actor = ActorDep,
    key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    def change(conn):
        row = ticket_row(conn, project_id, number)
        if body.workspace_id is not None:
            from ..workspaces import workspace_row
            workspace_row(conn,project_id,body.workspace_id)
        if (
            body.kind == "test"
            and body.outcome == "pass"
            and (body.exit_code != 0 or not body.command.strip())
        ):
            raise ApiError(
                422, "test_result_required", "Passing test evidence needs a command and zero exit code."
            )
        if body.kind == "artifact" and body.attachment_id is None:
            raise ApiError(422, "attachment_required", "Artifact evidence needs an uploaded attachment.")
        if body.attachment_id is not None:
            # Only unbound owned uploads can be promoted to project-readable evidence.
            att = conn.execute(
                "SELECT * FROM attachments WHERE id=? AND project_id=? AND uploader=? AND message_id IS NULL AND comment_id IS NULL AND document_id IS NULL",
                (body.attachment_id, project_id, actor.alias),
            ).fetchone()
            if att is None:
                raise ApiError(
                    422, "invalid_attachment", "Use an unbound attachment uploaded by you in this project."
                )
            # Bind to a ticket comment so existing download authorization and lifecycle apply.
            comment = conn.execute(
                "INSERT INTO ticket_comments(ticket_id,author_type,author_alias,body,created_at) VALUES(?,?,?,?,?)",
                (
                    row["id"],
                    "admin" if actor.is_admin else "agent",
                    actor.alias,
                    "Evidence: " + body.summary,
                    utc_now(),
                ),
            )
            conn.execute(
                "UPDATE attachments SET comment_id=? WHERE id=?", (comment.lastrowid, body.attachment_id)
            )
        cur = conn.execute(
            "INSERT INTO work_evidence(project_id,ticket_id,kind,outcome,summary,command,exit_code,source_commit,worktree_fingerprint,attachment_id,submitted_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                project_id,
                row["id"],
                body.kind,
                body.outcome,
                body.summary,
                body.command,
                body.exit_code,
                body.source_commit.lower(),
                body.worktree_fingerprint.lower(),
                body.attachment_id,
                actor.alias,
                utc_now(),
            ),
        )
        conn.execute(
            "UPDATE work_evidence SET workspace_id=?,observation_id=? WHERE id=?",
            (body.workspace_id, latest_observation(conn, project_id, body.workspace_id), cur.lastrowid),
        )
        conn.execute(
            "UPDATE tickets SET updated_at=?,changed_by=? WHERE id=?", (utc_now(), actor.alias, row["id"])
        )
        event_record(
            conn, project_id, actor, "evidence_updated", {"id": cur.lastrowid, "ticket_number": number}
        )
        return {
            "id": cur.lastrowid,
            "provenance": "reported",
            "revision": ticket_row(conn, project_id, number)["revision"],
        }

    return await mutate(actor, project_id, f"evidence:{number}", body.model_dump(), key, change)


@router.post("/projects/{project_id}/tickets/{number}/completion-proposals", status_code=201)
async def propose(
    project_id: int,
    number: int,
    body: Proposal,
    actor: Actor = ActorDep,
    key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    def change(conn):
        row = ticket_row(conn, project_id, number)
        ownership_guard(row, actor, body.expected_revision, body.claim_generation)
        if row["status"] in ("done", "wontfix"):
            raise ApiError(409, "terminal_ticket", "Reopen this ticket first.")
        policy = policy_for(conn, row)
        check_evidence(conn, row, policy)
        if blockers(conn, project_id, number):
            raise ApiError(409, "dependencies_unfinished", "Complete prerequisites first.")
        conn.execute(
            "UPDATE tickets SET status='awaiting-human',updated_at=?,changed_by=? WHERE id=?",
            (utc_now(), actor.alias, row["id"]),
        )
        current = ticket_row(conn, project_id, number)
        cur = conn.execute(
            "INSERT INTO completion_proposals(project_id,ticket_id,ticket_revision,policy_revision,evidence_digest,submitted_by,created_at) VALUES(?,?,?,?,?,?,?)",
            (
                project_id,
                row["id"],
                current["revision"],
                policy["revision"],
                evidence_digest(conn, row["id"]),
                actor.alias,
                utc_now(),
            ),
        )
        conn.execute(
            "UPDATE completion_proposals SET observation_id=? WHERE id=?",
            (latest_observation(conn, project_id, policy.get("workspace_id")), cur.lastrowid),
        )
        event_record(
            conn,
            project_id,
            actor,
            "completion_requested",
            {"id": cur.lastrowid, "ticket_number": number},
            target=0,
        )
        # The status moved to awaiting-human: open boards and ticket views
        # refresh on ticket_updated, which completion_requested alone is not.
        from .tickets import serialize_ticket_row_in_txn
        event_record(
            conn,
            project_id,
            actor,
            "ticket_updated",
            {"ticket": serialize_ticket_row_in_txn(current), "by": actor.alias},
        )
        return {"id": cur.lastrowid, "ticket_revision": current["revision"], "state": "pending"}

    return await mutate(actor, project_id, f"proposal:{number}", body.model_dump(), key, change)


@router.post("/projects/{project_id}/completion-proposals/{proposal_id}/review")
async def review(
    project_id: int,
    proposal_id: int,
    body: Review,
    actor: Actor = AdminDep,
    key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    def change(conn):
        proposal = conn.execute(
            "SELECT * FROM completion_proposals WHERE id=? AND project_id=?", (proposal_id, project_id)
        ).fetchone()
        if proposal is None:
            raise ApiError(404, "not_found", "Proposal not found.")
        if proposal["state"] != "pending":
            raise ApiError(409, "proposal_closed", "Proposal was already reviewed.")
        row = conn.execute("SELECT * FROM tickets WHERE id=?", (proposal["ticket_id"],)).fetchone()
        if body.approve:
            policy = policy_for(conn, row)
            if (
                latest_observation(conn, project_id, policy.get("workspace_id")) != proposal["observation_id"]
                or row["revision"] != proposal["ticket_revision"]
                or policy["revision"] != proposal["policy_revision"]
                or evidence_digest(conn, row["id"]) != proposal["evidence_digest"]
            ):
                raise ApiError(
                    409, "stale_proposal", "Ticket, policy, or evidence changed. Request a fresh proposal."
                )
            if blockers(conn, project_id, row["number"]):
                raise ApiError(409, "dependencies_unfinished", "Complete prerequisites first.")
            check_evidence(conn, row, policy)
        conn.execute(
            "UPDATE completion_proposals SET state=?,reviewed_by=?,reason=?,reviewed_at=? WHERE id=?",
            ("approved" if body.approve else "rejected", actor.alias, body.reason, utc_now(), proposal_id),
        )
        if body.approve:
            completion_guard(conn, row)
            conn.execute(
                "UPDATE tickets SET status='done',updated_at=?,changed_by=? WHERE id=?",
                (utc_now(), actor.alias, row["id"]),
            )
        if not body.approve and body.return_status is not None:
            # Rejecting an obsolete proposal must not overwrite ticket state
            # that moved on since the snapshot (a later proposal may already
            # have completed the ticket). Reject without return_status to
            # close a stale proposal.
            if row["revision"] != proposal["ticket_revision"]:
                raise ApiError(
                    409, "stale_proposal", "Ticket changed since this proposal; reject without return_status."
                )
            if body.return_status == "in-progress" and blockers(conn, project_id, row["number"]):
                raise ApiError(409, "dependencies_unfinished", "Return blocked work to blocked instead.")
            conn.execute(
                "UPDATE tickets SET status=?,updated_at=?,changed_by=? WHERE id=?",
                (body.return_status, utc_now(), actor.alias, row["id"]),
            )
        from .tickets import serialize_ticket_row_in_txn

        event_record(
            conn,
            project_id,
            actor,
            "ticket_updated",
            {
                "ticket": serialize_ticket_row_in_txn(ticket_row(conn, project_id, row["number"])),
                "by": actor.alias,
            },
        )
        event_record(
            conn,
            project_id,
            actor,
            "completion_reviewed",
            {"id": proposal_id, "ticket_number": row["number"], "approved": body.approve},
        )
        return {"id": proposal_id, "state": "approved" if body.approve else "rejected"}

    return await mutate(actor, project_id, f"review:{proposal_id}", body.model_dump(), key, change)


@router.get("/admin/attention")
def attention(
    actor: Actor = AdminDep,
    project_id: int | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    conn = get_conn()
    # Stable kind/id tie-breakers; pending-only queue intentionally changes as items resolve.
    sql = """WITH queue AS (
      SELECT 'decision' AS kind,id,project_id,question AS title,created_at,'decisionQueue' AS route FROM decision_requests WHERE state='pending'
      UNION ALL SELECT 'completion',p.id,p.project_id,t.title,p.created_at,'tickets/'||t.number FROM completion_proposals p JOIN tickets t ON t.id=p.ticket_id WHERE p.state='pending'
      UNION ALL SELECT 'reply',r.id,r.project_id,'Reply requested from agent '||r.target_agent_id,r.created_at,CASE WHEN r.message_id IS NOT NULL THEN 'c/'||m.conversation_id||'/m/'||m.id ELSE 'tickets/'||(SELECT t.number FROM ticket_comments c JOIN tickets t ON t.id=c.ticket_id WHERE c.id=r.comment_id) END FROM inbox_requests r LEFT JOIN messages m ON m.id=r.message_id WHERE r.state IN ('pending','acknowledged') AND (r.message_id IS NULL OR m.deleted=0)
      UNION ALL SELECT 'stale_claim',t.id,t.project_id,t.title,t.updated_at,'tickets/'||t.number FROM tickets t LEFT JOIN agents a ON a.id=t.owner_agent_id LEFT JOIN agent_sessions s ON s.id=t.claim_session_id WHERE t.managed_claim=1 AND t.status NOT IN ('done','wontfix') AND (a.id IS NULL OR a.revoked=1 OR s.ended_at IS NOT NULL)
    ) SELECT * FROM queue WHERE (? IS NULL OR project_id=?)"""
    params = (project_id, project_id)
    count = conn.execute("SELECT COUNT(*) FROM (" + sql + ")", params).fetchone()[0]
    rows = conn.execute(
        sql + " ORDER BY created_at,kind,id LIMIT ? OFFSET ?", params + (limit, offset)
    ).fetchall()
    return ok(
        {
            "items": [dict(r) for r in rows],
            "total": count,
            "next_offset": offset + len(rows) if offset + len(rows) < count else None,
            "stale_claim_definition": "Owner removed/revoked or attached session ended; inactivity alone is not proof.",
        }
    )
