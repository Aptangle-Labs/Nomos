import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, Field, model_validator

from ..auth import Actor, ActorDep, AdminDep, AgentDep
from ..db import get_conn, utc_now
from ..errors import ApiError, ok
from ..operations import event_record, mutate, page, project_guard, ticket_row
from ..outcomes import manifest_view, outcomes, usage_totals

router = APIRouter(tags=["outcomes"])


class RunManifest(BaseModel):
    schema_version: Literal[1] = 1
    task: str = Field(min_length=1, max_length=200)
    fixture_revision: str = Field(min_length=1, max_length=200)
    starting_commit: str = Field(min_length=40, max_length=64, pattern=r"^([a-fA-F0-9]{40}|[a-fA-F0-9]{64})$")
    condition: Literal["baseline", "helper_context"]
    trial: int = Field(ge=1)
    order_seed: int
    agent_count: int = Field(ge=1, le=100)
    runner: str | None = Field(default=None, max_length=200)
    runner_version: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=200)
    context_policy: str = Field(min_length=1, max_length=4000)
    evaluation: dict = Field(default_factory=dict)
    evidence_references: list[str] = Field(default_factory=list, max_length=100)
    started_at: datetime
    ended_at: datetime | None = None
    accepted: bool | None = None
    human_interventions: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def valid_times(self):
        if self.started_at.utcoffset() is None or (self.ended_at and self.ended_at.utcoffset() is None):
            raise ValueError("Use timezone-aware timestamps.")
        if self.ended_at and self.ended_at < self.started_at:
            raise ValueError("End must follow start.")
        if len(json.dumps(self.evaluation)) > 20000 or sum(map(len, self.evidence_references)) > 20000:
            raise ValueError("Evaluation or evidence references exceed 20000 characters.")
        return self


class Usage(BaseModel):
    session_id: int = Field(default=0, ge=0)
    source: str = Field(min_length=1, max_length=200)
    sample_id: str = Field(min_length=1, max_length=200)
    unit: str = Field(min_length=1, max_length=40)
    mode: Literal["delta", "cumulative"]
    epoch: str = Field(min_length=1, max_length=200)
    sequence: int = Field(ge=0)
    quantity: float = Field(ge=0, allow_inf_nan=False)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    pricing_source: str | None = Field(default=None, max_length=1000)


class Resume(BaseModel):
    ticket_number: int | None = Field(default=None, gt=0)


class Progress(BaseModel):
    summary: str = Field(min_length=1, max_length=4000)


@router.post("/projects/{project_id}/experiment-runs", status_code=201)
async def add_run(
    project_id: int,
    body: RunManifest,
    actor: Actor = ActorDep,
    key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    def change(conn):
        cur = conn.execute(
            "INSERT INTO experiment_runs(project_id,manifest,submitted_by,created_at) VALUES(?,?,?,?)",
            (project_id, body.model_dump_json(), actor.alias, utc_now()),
        )
        event_record(conn, project_id, actor, "experiment_updated", {"id": cur.lastrowid})
        return {"id": cur.lastrowid}

    return await mutate(actor, project_id, "experiment", body.model_dump(mode="json"), key, change)


@router.get("/projects/{project_id}/experiment-runs")
def runs(
    project_id: int,
    actor: Actor = ActorDep,
    before_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
):
    conn = get_conn()
    project_guard(conn, actor, project_id, write=False)
    data = page(
        conn.execute(
            "SELECT * FROM experiment_runs WHERE project_id=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT ?",
            (project_id, before_id, before_id, limit + 1),
        ).fetchall(),
        limit,
    )
    data["items"] = [manifest_view(r) | {"usage": usage_totals(conn, r["id"]) or None} for r in data["items"]]
    return ok(data)


@router.post("/projects/{project_id}/experiment-runs/{run_id}/usage", status_code=201)
async def usage(
    project_id: int,
    run_id: int,
    body: Usage,
    actor: Actor = ActorDep,
    key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    def change(conn):
        if not conn.execute(
            "SELECT 1 FROM experiment_runs WHERE id=? AND project_id=?", (run_id, project_id)
        ).fetchone():
            raise ApiError(404, "not_found", "Experiment run not found.")
        if (
            body.session_id
            and not conn.execute(
                "SELECT 1 FROM agent_sessions WHERE id=? AND project_id=? AND (? OR agent_id=?)",
                (body.session_id, project_id, actor.is_admin, actor.agent_id),
            ).fetchone()
        ):
            raise ApiError(422, "invalid_session", "Use a session belonging to you in this project.")
        if body.unit == "cost" and (not body.currency or not body.pricing_source):
            raise ApiError(
                422, "pricing_required", "Reported cost needs currency and pricing source/version."
            )
        prior = conn.execute(
            "SELECT * FROM usage_samples WHERE run_id=? AND session_id=? AND source=? AND sample_id=?",
            (run_id, body.session_id, body.source, body.sample_id),
        ).fetchone()
        if prior:
            if any(prior[k] != v for k, v in body.model_dump().items()):
                raise ApiError(409, "sample_conflict", "Sample ID was used with different data.")
            return {"id": prior["id"], "replayed": True}
        group = conn.execute(
            "SELECT * FROM usage_samples WHERE run_id=? AND session_id=? AND source=? AND unit=? AND epoch=?",
            (run_id, body.session_id, body.source, body.unit, body.epoch),
        ).fetchall()
        for sample in group:
            if (
                sample["mode"] != body.mode
                or sample["currency"] != body.currency
                or sample["pricing_source"] != body.pricing_source
            ):
                raise ApiError(409, "series_changed", "Use a new epoch for mode or pricing changes.")
            if sample["sequence"] == body.sequence:
                raise ApiError(409, "sequence_conflict", "Sequence already recorded.")
            if body.mode == "cumulative" and (
                (sample["sequence"] < body.sequence and sample["quantity"] > body.quantity)
                or (sample["sequence"] > body.sequence and sample["quantity"] < body.quantity)
            ):
                raise ApiError(409, "counter_reset", "Use a new epoch after a cumulative counter reset.")
        values = body.model_dump()
        cur = conn.execute(
            "INSERT INTO usage_samples(project_id,run_id,session_id,source,sample_id,unit,mode,epoch,sequence,quantity,currency,pricing_source,submitted_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, run_id, *values.values(), actor.alias, utc_now()),
        )
        return {"id": cur.lastrowid, "provenance": "reported"}

    return await mutate(actor, project_id, f"usage:{run_id}", body.model_dump(), key, change)


@router.get("/projects/{project_id}/outcomes")
def get_outcomes(project_id: int, actor: Actor = AdminDep):
    conn = get_conn()
    conn.execute("BEGIN")
    try:
        project_guard(conn, actor, project_id, write=False)
        result = outcomes(conn, project_id)
    finally:
        conn.rollback()
    return ok(result)


@router.post("/projects/{project_id}/resumes", status_code=201)
async def resume(
    project_id: int,
    body: Resume,
    actor: Actor = AgentDep,
    key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    def change(conn):
        ticket = ticket_row(conn, project_id, body.ticket_number) if body.ticket_number else None
        cur = conn.execute(
            "INSERT INTO resume_events(project_id,agent_id,ticket_id,created_at) VALUES(?,?,?,?)",
            (project_id, actor.agent_id, ticket["id"] if ticket else None, utc_now()),
        )
        return {"id": cur.lastrowid}

    return await mutate(actor, project_id, "resume", body.model_dump(), key, change)


@router.post("/projects/{project_id}/resumes/{resume_id}/progress")
async def progress(
    project_id: int,
    resume_id: int,
    body: Progress,
    actor: Actor = AgentDep,
    key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    def change(conn):
        row = conn.execute(
            "SELECT * FROM resume_events WHERE id=? AND project_id=? AND agent_id=?",
            (resume_id, project_id, actor.agent_id),
        ).fetchone()
        if row is None:
            raise ApiError(404, "not_found", "Resume not found.")
        if row["progress_at"] and row["progress_summary"] != body.summary:
            raise ApiError(409, "progress_recorded", "First progress was already recorded.")
        conn.execute(
            "UPDATE resume_events SET progress_at=COALESCE(progress_at,?),progress_summary=? WHERE id=?",
            (utc_now(), body.summary, resume_id),
        )
        return {"id": resume_id, "provenance": "reported"}

    return await mutate(actor, project_id, f"progress:{resume_id}", body.model_dump(), key, change)
