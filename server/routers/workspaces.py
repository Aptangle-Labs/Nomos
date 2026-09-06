import asyncio

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, Field

from .. import fsmonitor
from ..auth import Actor, ActorDep, AdminDep, AgentDep
from ..db import get_conn, utc_now
from ..errors import ApiError, ok
from ..operations import event_record, mutate, page, project_guard, revision_guard, ticket_row
from ..work import ownership_guard
from ..workspaces import ensure_workspace, normalized_path, overlaps, workspace_row
from .projects import _validate_working_dir

router = APIRouter(tags=["workspaces"])


class Workspace(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    root: str = Field(min_length=1, max_length=1024)
    is_default: bool = False


class Watch(BaseModel):
    enabled: bool


class Reservation(BaseModel):
    workspace_id: int = Field(gt=0)
    path: str = Field(min_length=1, max_length=1024)
    is_directory: bool = False
    ticket_number: int | None = Field(default=None, gt=0)
    expected_revision: int | None = Field(default=None, ge=0)
    claim_generation: int | None = Field(default=None, ge=0)
    reason: str = Field(min_length=1, max_length=2000)


class ReservationUpdate(BaseModel):
    expected_revision: int = Field(ge=0)
    to_agent_id: int | None = Field(default=None, gt=0)
    reason: str = Field(min_length=1, max_length=2000)


@router.get("/projects/{project_id}/workspaces")
def workspaces(project_id: int, actor: Actor = ActorDep):
    conn = get_conn()
    project_guard(conn, actor, project_id, write=False)
    rows = conn.execute(
        "SELECT w.*,a.created_at AS watch_registered_at FROM workspaces w LEFT JOIN audit_watches a ON a.workspace_id=w.id WHERE w.project_id=? ORDER BY w.id",
        (project_id,),
    ).fetchall()
    return ok({"items": [dict(r) | {"watch_active": fsmonitor.is_active(r["id"])} for r in rows]})


@router.post("/projects/{project_id}/workspaces", status_code=201)
async def register(
    project_id: int,
    body: Workspace,
    actor: Actor = AdminDep,
    key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    root = _validate_working_dir(body.root)
    if not root.is_dir():
        raise ApiError(
            422, "invalid_path", "Register an existing directory. This endpoint creates no source files."
        )

    def change(conn):
        count = conn.execute("SELECT COUNT(*) FROM workspaces WHERE project_id=?", (project_id,)).fetchone()[
            0
        ]
        if (
            count >= 100
            and not conn.execute(
                "SELECT 1 FROM workspaces WHERE project_id=? AND root=?", (project_id, str(root))
            ).fetchone()
        ):
            raise ApiError(409, "workspace_limit", "At most 100 registered workspaces per project.")
        row = ensure_workspace(conn, project_id, str(root), body.name, body.is_default)
        event_record(conn, project_id, actor, "workspace_updated", {"id": row["id"]})
        return dict(row)

    return await mutate(actor, project_id, "workspace", body.model_dump(), key, change)


@router.put("/projects/{project_id}/workspaces/{workspace_id}/watch")
async def watch(project_id: int, workspace_id: int, body: Watch, actor: Actor = AdminDep):
    def change(conn):
        row = workspace_row(conn, project_id, workspace_id)
        if body.enabled:
            root = _validate_working_dir(row["root"])
            if str(root) != row["root"] or not root.is_dir():
                raise ApiError(
                    409,
                    "workspace_changed",
                    "Workspace root changed or is unavailable; register its current path.",
                )
            conn.execute(
                "INSERT INTO audit_watches(workspace_id,project_id,path,created_at) VALUES(?,?,?,?) ON CONFLICT(workspace_id) DO NOTHING",
                (workspace_id, project_id, row["root"], utc_now()),
            )
        else:
            conn.execute("DELETE FROM audit_watches WHERE workspace_id=?", (workspace_id,))
        event_record(
            conn, project_id, actor, "workspace_updated", {"id": workspace_id, "watch_enabled": body.enabled}
        )
        return {"workspace_id": workspace_id, "path": row["root"], "enabled": body.enabled}

    result = await mutate(actor, project_id, f"watch:{workspace_id}", body.model_dump(), None, change)
    if body.enabled:
        row = await asyncio.to_thread(lambda: workspace_row(get_conn(), project_id, workspace_id))
        fsmonitor.start_watch(project_id, row["root"], workspace_id)
    else:
        fsmonitor.stop_watch(project_id, workspace_id)
    return result


@router.get("/projects/{project_id}/reservations")
def reservations(
    project_id: int,
    actor: Actor = ActorDep,
    before_id: int | None = None,
    limit: int = Query(100, ge=1, le=200),
    active_only: bool = True,
):
    conn = get_conn()
    project_guard(conn, actor, project_id, write=False)
    return ok(
        page(
            conn.execute(
                "SELECT * FROM path_reservations WHERE project_id=? AND (?=0 OR state='active') AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT ?",
                (project_id, active_only, before_id, before_id, limit + 1),
            ).fetchall(),
            limit,
        )
    )


@router.post("/projects/{project_id}/reservations", status_code=201)
async def reserve(
    project_id: int,
    body: Reservation,
    actor: Actor = AgentDep,
    key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    def change(conn):
        workspace = workspace_row(conn, project_id, body.workspace_id)
        path, canonical = normalized_path(workspace["root"], body.path, body.is_directory)
        ticket = ticket_row(conn, project_id, body.ticket_number) if body.ticket_number else None
        if ticket:
            ownership_guard(ticket, actor, body.expected_revision, body.claim_generation)
        rows = conn.execute(
            "SELECT * FROM path_reservations WHERE project_id=? AND state='active'", (project_id,)
        ).fetchall()
        conflicts = [
            r["id"]
            for r in rows
            if overlaps(canonical, body.is_directory, r["canonical_path"], r["is_directory"])
        ]
        if conflicts:
            import json
            conn.execute('INSERT INTO reservation_conflicts(project_id,workspace_id,actor_alias,path,conflicts,created_at) VALUES(?,?,?,?,?,?)', (project_id, workspace['id'], actor.alias, path, json.dumps(conflicts), utc_now()))
            event_record(conn, project_id, actor, 'reservation_conflict', {'id': workspace['id'], 'reservation_ids': conflicts})
            return {'conflicts': conflicts}
        cur = conn.execute(
            "INSERT INTO path_reservations(project_id,workspace_id,ticket_id,owner_agent_id,owner_alias,path,canonical_path,is_directory,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                project_id,
                workspace["id"],
                ticket["id"] if ticket else None,
                actor.agent_id,
                actor.alias,
                path,
                canonical,
                body.is_directory,
                body.reason,
                utc_now(),
            ),
        )
        event_record(conn, project_id, actor, "reservation_updated", {"id": cur.lastrowid})
        return {"id": cur.lastrowid, "revision": 0, "state": "active"}

    result = await mutate(actor, project_id, "reservation", body.model_dump(), key, change)
    if result['data'].get('conflicts'):
        raise ApiError(409, 'reservation_conflict', 'A file or directory is already reserved.', {'reservation_ids': result['data']['conflicts']})
    return result


@router.post("/projects/{project_id}/reservations/{reservation_id}/update")
async def update_reservation(
    project_id: int,
    reservation_id: int,
    body: ReservationUpdate,
    actor: Actor = ActorDep,
    key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    def change(conn):
        row = conn.execute(
            "SELECT * FROM path_reservations WHERE id=? AND project_id=?", (reservation_id, project_id)
        ).fetchone()
        if row is None:
            raise ApiError(404, "not_found", "Reservation not found.")
        revision_guard(row, body.expected_revision)
        if row["state"] != "active":
            raise ApiError(409, "reservation_closed", "Reservation already released.")
        if not actor.is_admin and row["owner_agent_id"] != actor.agent_id:
            raise ApiError(403, "owner_only", "Only owner or admin may release or transfer a reservation.")
        if body.to_agent_id:
            target = conn.execute(
                "SELECT * FROM agents WHERE id=? AND project_id=? AND revoked=0",
                (body.to_agent_id, project_id),
            ).fetchone()
            if target is None:
                raise ApiError(422, "invalid_recipient", "Choose an active project agent.")
            conn.execute(
                "UPDATE path_reservations SET owner_agent_id=?,owner_alias=?,reason=?,revision=revision+1 WHERE id=?",
                (target["id"], target["alias"], body.reason, reservation_id),
            )
        else:
            conn.execute(
                "UPDATE path_reservations SET state='released',reason=?,revision=revision+1,released_at=? WHERE id=?",
                (body.reason, utc_now(), reservation_id),
            )
        event_record(conn, project_id, actor, "reservation_updated", {"id": reservation_id})
        return {"id": reservation_id, "state": "active" if body.to_agent_id else "released"}

    return await mutate(
        actor, project_id, f"reservation-update:{reservation_id}", body.model_dump(), key, change
    )
