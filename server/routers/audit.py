"""Audit trail endpoints (issue #17).

Append-only by construction: there is no update or delete route in this file
and there must never be one — tamper-evidence relies on it."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, Query
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from .. import audit, fsmonitor
from ..auth import Actor, ActorDep, AdminDep, check_project_access
from ..db import query_all, transaction, utc_now
from ..errors import ApiError, ok
from ..events import notify
from ..services import get_project, pagination_window

router = APIRouter(tags=["audit"])

# Checked against the RESOLVED path (symlinks followed — on macOS /etc is
# really /private/etc). A path is refused if it equals a root or sits under a
# forbidden prefix.
FORBIDDEN_WATCH_PREFIXES = (
    "/etc", "/usr", "/bin", "/sbin", "/System", "/Library", "/var",
    "/private/etc", "/private/var", "/Applications", "/opt",
)


ALLOWED_WATCH_PREFIXES = (  # user-space temp lives under /var on macOS
    "/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/",
)


def _forbidden_watch_path(resolved: Path) -> bool:
    s = str(resolved)
    if s == "/" or len(resolved.parts) <= 1:
        return True
    if any(s.startswith(p) for p in ALLOWED_WATCH_PREFIXES):
        return False
    return any(s == p or s.startswith(p + "/") for p in FORBIDDEN_WATCH_PREFIXES)


class SelfReport(BaseModel):
    workspace_id: int | None = Field(default=None, gt=0)
    action: str
    target: str = Field(default="", max_length=1024)
    summary: str = Field(min_length=1, max_length=2000)
    detail: dict[str, Any] = Field(default_factory=dict)
    diff: str | None = Field(default=None, max_length=256 * 1024)


class SelfReportBulk(BaseModel):
    items: list[SelfReport] = Field(min_length=1, max_length=50)


class WatchCreate(BaseModel):
    path: str = Field(min_length=1, max_length=1024)


def _validate_report(item: SelfReport) -> None:
    if item.action not in audit.ACTIONS:
        raise ApiError(
            422, "invalid_action",
            f"action must be one of: {', '.join(audit.ACTIONS)}.",
        )


def _insert_report(conn, project_id: int, actor: Actor, item: SelfReport) -> int:
    from ..operations import project_guard
    from ..workspaces import default_workspace_id, normalized_path, workspace_row
    project_guard(conn, actor, project_id)
    wid = item.workspace_id if item.workspace_id is not None else default_workspace_id(conn, project_id)
    target = item.target
    if wid is not None:
        workspace = workspace_row(conn, project_id, wid)
        if item.action.startswith('file_'):
            target, _ = normalized_path(workspace['root'], target)
    detail = dict(item.detail)
    detail.pop('workspace_id', None)
    if wid is not None:
        detail['workspace_id'] = wid
    correlated = audit.find_recent_observation(conn, project_id, item.action, target, wid)
    return audit.record(
        conn, project_id,
        actor=actor.alias,
        actor_type=actor.role_flag,
        source="self_report",
        action=item.action,
        summary=item.summary,
        target=target,
        detail=detail,
        diff=item.diff,
        correlated_id=correlated,
    )


@router.post("/projects/{project_id}/audit", status_code=201)
async def self_report(project_id: int, body: SelfReport, actor: Actor = ActorDep, key: str | None = Header(default=None, alias='Idempotency-Key')) -> dict:
    from ..operations import mutate
    def change(conn):
        _validate_report(body)
        audit_id = _insert_report(conn, project_id, actor, body)
        return audit.serialize_row(conn.execute('SELECT * FROM audit_log WHERE id=?', (audit_id,)).fetchone())
    return await mutate(actor, project_id, 'audit', body.model_dump(), key, change)


@router.post("/projects/{project_id}/audit/bulk", status_code=201)
async def self_report_bulk(project_id: int, body: SelfReportBulk, actor: Actor = ActorDep, key: str | None = Header(default=None, alias='Idempotency-Key')) -> dict:
    from ..operations import mutate
    def change(conn):
        items = []
        for item in body.items:
            _validate_report(item)
            audit_id = _insert_report(conn, project_id, actor, item)
            items.append(audit.serialize_row(conn.execute('SELECT * FROM audit_log WHERE id=?', (audit_id,)).fetchone()))
        return {'items': items}
    return await mutate(actor, project_id, 'audit-bulk', body.model_dump(), key, change)


@router.get("/projects/{project_id}/audit")
async def list_audit(
    project_id: int,
    workspace_id: int | None = None,
    actor_filter: str | None = Query(default=None, alias="actor"),
    action: str | None = None,
    source: str | None = None,
    target: str | None = None,
    after: str | None = None,
    before: str | None = None,
    before_id: int | None = None,
    limit: int | None = None,
    actor: Actor = ActorDep,
) -> dict:
    """The merged trail, newest first. Monitor rows carry `claimed` resolved
    in BOTH correlation directions (append-only rows mean the link lives on
    whichever row landed second)."""
    check_project_access(actor, project_id)
    get_project(project_id)
    window = pagination_window(limit, max_limit=500)
    clauses = ["a.project_id = :pid"]
    params: dict[str, Any] = {"pid": project_id, "lim": window + 1}
    if workspace_id is not None:
        clauses.append("json_extract(a.detail, '$.workspace_id') = :wid")
        params["wid"] = workspace_id
    if actor_filter:
        clauses.append("a.actor = :actor COLLATE NOCASE")
        params["actor"] = actor_filter
    if action:
        clauses.append("a.action = :action")
        params["action"] = action
    if source:
        clauses.append("a.source = :source")
        params["source"] = source
    if target:
        clauses.append("a.target LIKE :target")
        params["target"] = f"%{target}%"
    if after:
        clauses.append("a.created_at >= :after")
        params["after"] = after
    if before:
        clauses.append("a.created_at <= :before")
        params["before"] = before
    if before_id is not None:
        clauses.append("a.id < :before_id")
        params["before_id"] = before_id
    rows = query_all(
        f"""
        SELECT a.*, (a.correlated_id IS NOT NULL OR EXISTS (
            SELECT 1 FROM audit_log b
            WHERE b.project_id = a.project_id AND b.source = 'self_report'
              AND b.correlated_id = a.id
        )) AS claimed
        FROM audit_log a
        WHERE {' AND '.join(clauses)}
        ORDER BY a.id DESC LIMIT :lim
        """,
        params,
    )
    has_more = len(rows) > window
    items = []
    for r in rows[:window]:
        item = audit.serialize_row(r)
        if r["source"] == "monitor":
            item["claimed"] = bool(r["claimed"])
        items.append(item)
    return ok({"items": items, "has_more": has_more})


@router.get("/projects/{project_id}/audit/verify")
async def verify(project_id: int, actor: Actor = ActorDep) -> dict:
    """Recompute the hash chain. Exposed to every project reader — integrity
    checking is not a privilege."""
    check_project_access(actor, project_id)
    get_project(project_id)
    return ok(audit.verify_chain(project_id))


@router.get("/projects/{project_id}/audit/coverage")
async def coverage(project_id: int, actor: Actor = ActorDep) -> dict:
    """Per-actor accounting: self-reports vs monitor observations vs
    unattributed changes (the honesty dashboard)."""
    check_project_access(actor, project_id)
    get_project(project_id)
    reports = query_all(
        "SELECT actor, COUNT(*) AS n FROM audit_log "
        "WHERE project_id = ? AND source = 'self_report' GROUP BY actor",
        (project_id,),
    )
    observed = query_all(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN correlated_id IS NOT NULL OR EXISTS (
                   SELECT 1 FROM audit_log b
                   WHERE b.project_id = a.project_id AND b.source = 'self_report'
                     AND b.correlated_id = a.id
               ) THEN 1 ELSE 0 END) AS claimed
        FROM audit_log a WHERE a.project_id = ? AND a.source = 'monitor'
        """,
        (project_id,),
    )
    total_observed = observed[0]["total"] if observed else 0
    total_claimed = observed[0]["claimed"] or 0 if observed else 0
    return ok({
        "actors": [{"actor": r["actor"], "self_reports": r["n"]} for r in reports],
        "observed": total_observed,
        "correlated": total_claimed,
        "unattributed": total_observed - total_claimed,
    })


@router.get("/projects/{project_id}/audit/export")
async def export(project_id: int, format: str = "jsonl", actor: Actor = ActorDep):
    """Full-trail export with chain fields for offline re-verification."""
    check_project_access(actor, project_id)
    get_project(project_id)
    rows = query_all(
        "SELECT * FROM audit_log WHERE project_id = ? ORDER BY id", (project_id,)
    )
    stamp = utc_now()[:10]
    if format == "csv":
        buf = io.StringIO()
        # project_id and diff are part of the hash preimage: omitting them
        # made offline chain re-verification impossible from a CSV (#28).
        fields = ["id", "project_id", "created_at", "actor", "actor_type", "source",
                  "action", "target", "summary", "detail", "diff", "correlated_id",
                  "prev_hash", "entry_hash"]
        writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r[k] for k in fields})
        return PlainTextResponse(
            buf.getvalue(), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="audit-{project_id}-{stamp}.csv"'},
        )
    if format != "jsonl":
        raise ApiError(422, "invalid_format", "format must be jsonl or csv.")

    def lines():
        for r in rows:
            yield json.dumps(audit.serialize_row(r), sort_keys=True) + "\n"

    return StreamingResponse(
        lines(), media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="audit-{project_id}-{stamp}.jsonl"'},
    )


# ------------------------------------------------------------- watches

@router.get("/projects/{project_id}/audit/watch")
async def get_watch(project_id: int, actor: Actor = ActorDep) -> dict:
    check_project_access(actor, project_id)
    get_project(project_id)
    import asyncio

    return ok({"watch": await asyncio.to_thread(fsmonitor.watch_status, project_id)})


@router.post("/projects/{project_id}/audit/watch", status_code=201)
async def register_watch(project_id: int, body: WatchCreate, _admin: Actor = AdminDep) -> dict:
    """Admin-only: the monitor reads the host filesystem. One watch per
    project (v1); registering replaces any existing watch."""
    get_project(project_id)
    path = Path(body.path).expanduser()
    if not path.is_absolute():
        raise ApiError(422, "invalid_path", "Watch path must be absolute.")
    resolved = path.resolve()
    if _forbidden_watch_path(resolved):
        raise ApiError(422, "invalid_path", "Refusing to watch a system directory.")
    if not resolved.is_dir():
        raise ApiError(422, "invalid_path", f"'{resolved}' is not a directory.")
    from ..workspaces import ensure_workspace
    previous = audit.get_watch(project_id)
    with transaction() as conn:
        workspace = ensure_workspace(conn, project_id, str(resolved), 'Monitor')
        conn.execute('DELETE FROM audit_watches WHERE project_id=? AND is_legacy=1', (project_id,))
        conn.execute('INSERT INTO audit_watches(workspace_id,project_id,path,is_legacy,created_at) VALUES(?,?,?,1,?) ON CONFLICT(workspace_id) DO UPDATE SET is_legacy=1,created_at=excluded.created_at', (workspace['id'], project_id, str(resolved), utc_now()))
        audit.platform_record(
            conn, project_id, "other",
            f"File monitor registered on {resolved}", target=str(resolved),
            actor="admin",
        )
    if previous is not None:
        fsmonitor.stop_watch(project_id, previous["workspace_id"])
    fsmonitor.start_watch(project_id, str(resolved), workspace["id"])
    await notify(project_id)
    import asyncio

    return ok({"watch": await asyncio.to_thread(fsmonitor.watch_status, project_id)})


@router.delete("/projects/{project_id}/audit/watch")
async def remove_watch(project_id: int, _admin: Actor = AdminDep) -> dict:
    get_project(project_id)
    with transaction() as conn:
        existing = conn.execute(
            "SELECT path,workspace_id FROM audit_watches WHERE project_id = ? AND is_legacy=1", (project_id,)
        ).fetchone()
        if existing is None:
            raise ApiError(404, "not_found", "No watch is registered for this project.")
        conn.execute("DELETE FROM audit_watches WHERE project_id = ? AND is_legacy=1", (project_id,))
        audit.platform_record(
            conn, project_id, "other",
            f"File monitor removed from {existing['path']}", target=existing["path"],
            actor="admin",
        )
    fsmonitor.stop_watch(project_id, existing["workspace_id"])
    await notify(project_id)
    return ok({"removed": True})

