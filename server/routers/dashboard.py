"""Admin-only dashboard snapshot, one request and one consistent DB read."""

from typing import Literal

from fastapi import APIRouter

from ..auth import Actor, AdminDep
from ..dashboard import dashboard
from ..db import get_conn
from ..errors import ok
from ..operations import project_guard

router = APIRouter(tags=["dashboard"])


@router.get("/projects/{project_id}/dashboard")
def get_dashboard(project_id: int, range: Literal["24h", "7d", "30d", "all"] = "7d", actor: Actor = AdminDep):
    conn = get_conn()
    conn.execute("BEGIN")
    try:
        project_guard(conn, actor, project_id, write=False)
        result = dashboard(conn, project_id, range)
    finally:
        conn.rollback()
    return ok(result)
