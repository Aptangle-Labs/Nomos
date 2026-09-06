"""Deterministic, bounded resume context with reference-level visibility."""

import json

from .errors import ApiError
from .inbox import list_inbox


def resolve_source(conn, pid, actor, ref, *, shared=False):
    kind = ref["kind"]
    if kind == "message":
        row = conn.execute(
            "SELECT m.*,c.type AS conversation_type FROM messages m "
            "JOIN conversations c ON c.id=m.conversation_id WHERE m.project_id=? AND m.id=? AND m.deleted=0",
            (pid, ref["id"]),
        ).fetchone()
        if row is None or (shared and row["conversation_type"] == "dm"):
            raise ApiError(422, "invalid_source", "Source unavailable for this visibility.")
        if (
            not actor.is_admin
            and not conn.execute(
                "SELECT 1 FROM conversation_members WHERE conversation_id=? AND agent_id=?",
                (row["conversation_id"], actor.agent_id),
            ).fetchone()
        ):
            raise ApiError(403, "forbidden_source", "You cannot read this source.")
        # Channel membership is still required at every read; shared checkpoint
        # text must never be readable by an agent who cannot read its references.
        return {
            "kind": kind,
            "id": row["id"],
            "body": row["body"],
            "changed": row["edited_at"] is not None,
            "url": f"/api/projects/{pid}/messages/{row['id']}",
        }
    if kind == "document":
        row = conn.execute("SELECT * FROM documents WHERE project_id=? AND id=?", (pid, ref["id"])).fetchone()
        if row is None:
            raise ApiError(422, "invalid_source", "Document not found.")
        rev = ref.get("revision") or row["current_revision"]
        doc = conn.execute(
            "SELECT * FROM document_revisions WHERE document_id=? AND revision=?", (row["id"], rev)
        ).fetchone()
        if doc is None:
            raise ApiError(422, "invalid_source", "Document revision not found.")
        return {
            "kind": kind,
            "id": row["id"],
            "revision": rev,
            "current_revision": row["current_revision"],
            "current_body": conn.execute("SELECT body FROM document_revisions WHERE document_id=? AND revision=?",(row["id"],row["current_revision"])).fetchone()[0] if row["current_revision"] != rev else None,
            "changed": row["current_revision"] != rev,
            "title": doc["title"],
            "body": doc["body"],
            "url": f"/api/projects/{pid}/documents/{row['slug']}?revision={rev}",
        }
    if kind == "ticket":
        row = conn.execute("SELECT * FROM tickets WHERE project_id=? AND id=?", (pid, ref["id"])).fetchone()
        if row is None:
            raise ApiError(422, "invalid_source", "Ticket not found.")
        return {
            "kind": kind,
            "id": row["id"],
            "revision": row["revision"],
            "title": row["title"],
            "body": row["description"],
            "changed": ref.get("revision") is not None and ref["revision"] != row["revision"],
            "url": f"/api/projects/{pid}/tickets/{row['number']}",
        }
    raise ApiError(422, "invalid_source", "Unknown source kind.")


def checkpoint_view(conn, pid, actor, row):
    if not actor.is_admin and row["agent_id"] != actor.agent_id and not row["shared"]:
        raise ApiError(404, "not_found", "Checkpoint not found.")
    data = dict(row)
    refs = json.loads(data.pop("source_refs"))
    data["sources"] = [resolve_source(conn, pid, actor, ref) for ref in refs]
    data["blockers"] = json.loads(data["blockers"])
    return data


def build_pack(conn, pid, actor, ticket_number, max_bytes):
    high = conn.execute("SELECT COALESCE(MAX(id),0) FROM events WHERE project_id=?", (pid,)).fetchone()[0]
    pack = {
        "project_id": pid,
        "snapshot_event_id": high,
        "sections": [],
        "truncated": False,
        "continuations": {
            "inbox": f"/api/projects/{pid}/inbox",
            "checkpoints": f"/api/projects/{pid}/checkpoints",
            "events": f"/api/projects/{pid}/events?since_id={high}",
        },
    }

    def add(kind, items):
        section = {"kind": kind, "items": items}
        pack["sections"].append(section)
        if len(json.dumps(pack, ensure_ascii=False).encode()) > max_bytes - 150:
            pack["sections"].pop()
            pack["truncated"] = True
            return False
        return True

    pending_decisions = conn.execute("SELECT * FROM decision_requests WHERE project_id=? AND state='pending' ORDER BY id LIMIT 51", (pid,)).fetchall()
    for decision in pending_decisions[:50]:
        add('decision_request', [dict(decision)])
    pack['truncated'] |= len(pending_decisions)>50
    requests = list_inbox(conn, pid, actor, 100)
    # Human requests first; IDs/links remain available even when body is large.
    for r in sorted(requests["items"], key=lambda r: (r["issuer_id"] != 0, r["created_at"])):
        add(
            "request",
            [
                {
                    k: r[k]
                    for k in (
                        "id",
                        "issuer_alias",
                        "state",
                        "body",
                        "message_id",
                        "conversation_id",
                        "ticket_number",
                    )
                }
            ],
        )
    if requests["has_more"]:
        pack["truncated"] = True
    tickets = conn.execute(
        "SELECT * FROM tickets WHERE project_id=? AND status NOT IN ('done','wontfix') "
        "AND (? IS NULL OR number=?) AND (? OR assignee=? COLLATE NOCASE) ORDER BY number LIMIT 101",
        (pid, ticket_number, ticket_number, actor.is_admin, actor.alias),
    ).fetchall()
    if ticket_number is not None:
        tickets = conn.execute(
            "SELECT * FROM tickets WHERE project_id=? AND number=?", (pid, ticket_number)
        ).fetchall()
    for row in tickets[:100]:
        from .work import blockers
        add("ticket", [dict(row) | {"blockers": blockers(conn,pid,row["number"])}])
    pack["truncated"] |= len(tickets) > 100
    rows = conn.execute(
        "SELECT * FROM work_checkpoints WHERE project_id=? AND (? IS NULL OR ticket_number=?) "
        "AND (? OR agent_id=? OR shared=1) ORDER BY id DESC LIMIT 101",
        (pid, ticket_number, ticket_number, actor.is_admin, actor.agent_id),
    ).fetchall()
    for row in rows[:100]:
        try:
            add("checkpoint", [checkpoint_view(conn, pid, actor, row)])
        except ApiError:
            continue  # Never expose a stored summary whose sources became private.
    pack["truncated"] |= len(rows) > 100
    if not actor.is_admin:
        agent = conn.execute(
            "SELECT scratchpad,scratchpad_revision FROM agents WHERE id=?", (actor.agent_id,)
        ).fetchone()
        add("scratchpad", [dict(agent)])
        add(
            "todos",
            [
                dict(r)
                for r in conn.execute(
                    "SELECT id,text,status,priority FROM agent_todos WHERE agent_id=? "
                    "ORDER BY CASE status WHEN 'in-progress' THEN 0 WHEN 'blocked' THEN 1 ELSE 2 END,id LIMIT 51",
                    (actor.agent_id,),
                )
            ],
        )
    # Only visible conversations; no summaries of another agent's DMs.
    recent = conn.execute(
        "SELECT m.id,m.body,m.type,m.conversation_id,m.created_at FROM messages m "
        "WHERE project_id=? AND deleted=0 AND type!='system' AND (? OR conversation_id IN "
        "(SELECT conversation_id FROM conversation_members WHERE agent_id=?)) "
        "ORDER BY CASE type WHEN 'decision' THEN 0 ELSE 1 END, id DESC LIMIT 30",
        (pid, actor.is_admin, actor.agent_id),
    ).fetchall()
    for row in recent:
        add("message", [dict(row) | {"url": f"/api/projects/{pid}/messages/{row['id']}"}])
    pack["bytes"] = 0
    for _ in range(4):
        pack["bytes"] = len(json.dumps(pack, ensure_ascii=False).encode())
    return pack


def pack_markdown(pack, max_bytes):
    lines=[f"# Resume context for project {pack['project_id']}",f"Snapshot event: {pack['snapshot_event_id']}"]
    for section in pack['sections']:
        lines.append('\n## '+section['kind'].replace('_',' '))
        for item in section['items']:
            for field in ('title','purpose','question','body','description','text','scratchpad','completed','next_action'):
                if item.get(field):
                    lines.append(str(item[field]))
            for source in item.get('sources',[]):
                lines.append(f"Source: {source['url']}"+(' (changed)' if source.get('changed') else ''))
    text='\n\n'.join(lines)
    suffix='\n\n[Truncated: open the JSON context and original sources for continuations.]'
    if pack['truncated'] or len(text.encode())>max_bytes:
        text=text.encode()[:max_bytes-len(suffix.encode())].decode('utf-8','ignore')+suffix
    return text
