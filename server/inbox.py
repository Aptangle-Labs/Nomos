"""Explicit reply requests; seen/read state deliberately remains independent."""

from .db import utc_now
from .errors import ApiError
from .operations import event_record


def create_requests(conn, pid, actor, source_id, targets, *, conversation=None, comment=False):
    if not targets:
        raise ApiError(422, "ack_targets_required", "Request Reply needs an explicit agent @mention.")
    created = 0
    for target in sorted(targets):
        agent = conn.execute(
            "SELECT id FROM agents WHERE id=? AND project_id=? AND revoked=0", (target, pid)
        ).fetchone()
        if agent is None:
            continue  # admin @mentions continue to use the existing human attention flow
        if (
            conversation is not None
            and not conn.execute(
                "SELECT 1 FROM conversation_members WHERE conversation_id=? AND agent_id=?",
                (conversation, target),
            ).fetchone()
        ):
            raise ApiError(422, "target_not_member", "Reply requests require target conversation membership.")
        now = utc_now()
        cur = conn.execute(
            "INSERT INTO inbox_requests(project_id,issuer_id,issuer_alias,target_agent_id,"
            "message_id,comment_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                pid,
                actor.agent_id,
                actor.alias,
                target,
                None if comment else source_id,
                source_id if comment else None,
                now,
                now,
            ),
        )
        created += 1
        event_record(conn, pid, actor, "inbox_requested", {"id": cur.lastrowid}, target=target)
    if not created:
        raise ApiError(422, "ack_targets_required", "Request Reply needs an active explicit agent target.")


def source_visible(conn, actor, request):
    if request["message_id"] is not None:
        msg = conn.execute(
            "SELECT * FROM messages WHERE id=? AND deleted=0", (request["message_id"],)
        ).fetchone()
        if msg is None:
            return False
        if (
            not actor.is_admin
            and not conn.execute(
                "SELECT 1 FROM conversation_members WHERE conversation_id=? AND agent_id=?",
                (msg["conversation_id"], actor.agent_id),
            ).fetchone()
        ):
            return False
    return True


def get_request(conn, pid, actor, rid):
    row = conn.execute("SELECT * FROM inbox_requests WHERE id=? AND project_id=?", (rid, pid)).fetchone()
    if row is None or (
        not actor.is_admin and actor.agent_id not in (row["issuer_id"], row["target_agent_id"])
    ):
        raise ApiError(404, "not_found", "Request not found.")
    if not source_visible(conn, actor, row):
        raise ApiError(404, "not_found", "Request source is unavailable.")
    return row


def list_inbox(conn, pid, actor, limit=100, before=None, outstanding=True):
    # Visibility is in SQL before LIMIT so hidden requests cannot consume pages.
    where = ["r.project_id=?"]
    params = [pid]
    if not actor.is_admin:
        where.extend(
            [
                "r.target_agent_id=?",
                "(r.message_id IS NULL OR EXISTS ("
                "SELECT 1 FROM conversation_members cm WHERE cm.conversation_id=m.conversation_id AND cm.agent_id=?))",
            ]
        )
        params.extend([actor.agent_id, actor.agent_id])
    where.append("(r.message_id IS NULL OR m.deleted=0)")
    if outstanding:
        where.append("r.state IN ('pending','acknowledged')")
    if before is not None:
        where.append("r.id < ?")
        params.append(before)
    params.append(limit + 1)
    rows = conn.execute(
        "SELECT r.*, m.conversation_id, COALESCE(m.body,c.body) AS body, t.number AS ticket_number, "
        "(SELECT MAX(heartbeat_at) FROM inbox_consumers ic WHERE ic.project_id=r.project_id AND ic.agent_id=r.target_agent_id) AS client_heartbeat_at, "
        "(SELECT created_at FROM inbox_receipts WHERE request_id=r.id AND kind='delivered') AS delivered_at "
        "FROM inbox_requests r LEFT JOIN messages m ON m.id=r.message_id "
        "LEFT JOIN ticket_comments c ON c.id=r.comment_id LEFT JOIN tickets t ON t.id=c.ticket_id "
        "WHERE " + " AND ".join(where) + " ORDER BY r.id DESC LIMIT ?",
        params,
    ).fetchall()
    from .operations import page

    return page(rows, limit)
