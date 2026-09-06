"""Coordination invariants used by direct ticket updates, claims and board moves."""

from .errors import ApiError
from .operations import revision_guard


def blockers(conn, pid, number):
    return [
        dict(r)
        for r in conn.execute(
            "SELECT d.prerequisite_number,t.title,t.status FROM ticket_dependencies d "
            "JOIN tickets t ON t.project_id=d.project_id AND t.number=d.prerequisite_number "
            "WHERE d.project_id=? AND d.ticket_number=? AND d.waived=0 AND t.status!='done' ORDER BY t.number",
            (pid, number),
        )
    ]


def require_ready(conn, row):
    blocked = blockers(conn, row["project_id"], row["number"])
    if blocked:
        raise ApiError(409, "dependencies_unfinished", "Prerequisites are unfinished.", {"blockers": blocked})
    if row["status"] in ("done", "wontfix"):
        raise ApiError(409, "terminal_ticket", "Reopen the ticket before claiming it.")


def ownership_guard(row, actor, expected, generation):
    revision_guard(row, expected)
    if not row["managed_claim"]:
        return
    if expected is None:
        raise ApiError(409, "revision_required", "Managed work requires expected_revision.")
    if not actor.is_admin:
        if row["owner_agent_id"] != actor.agent_id or generation != row["claim_generation"]:
            raise ApiError(
                409, "ownership_changed", "Reload the current ownership generation before working."
            )


def validate_update(conn, row, actor, fields, fields_set):
    revision_guard(row, fields.expected_revision)
    # A supplied generation token is always checked, even after release or
    # reassignment dropped managed_claim: a fenced-out stale runner must not
    # regain write access just because ownership moved on.
    if fields.claim_generation is not None and fields.claim_generation != row["claim_generation"]:
        raise ApiError(
            409, "ownership_changed", "Reload the current ownership generation before working."
        )
    sensitive = {"assignee", "status"} & fields_set
    if sensitive and row["managed_claim"]:
        ownership_guard(row, actor, fields.expected_revision, fields.claim_generation)
        if "assignee" in fields_set and fields.assignee != row["assignee"]:
            if not actor.is_admin:
                raise ApiError(
                    409, "managed_assignment", "Use release or an explicit handoff for managed work."
                )
    if fields.status == "done" and row["status"] != "done":
        from .evidence import completion_guard
        completion_guard(conn, row)
    if fields.status in ("in-progress", "done") and fields.status != row["status"]:
        if blockers(conn, row["project_id"], row["number"]):
            raise ApiError(409, "dependencies_unfinished", "Resolve prerequisites before continuing.")


def validate_graph(conn, pid, number, edges):
    graph = {}
    for row in conn.execute(
        "SELECT ticket_number,prerequisite_number FROM ticket_dependencies WHERE project_id=?", (pid,)
    ):
        graph.setdefault(row[0], set()).add(row[1])
    graph[number] = {e["prerequisite_number"] for e in edges}
    # Iterative traversal is safe for long existing graphs.
    pending = list(graph[number])
    seen = set()
    while pending:
        current = pending.pop()
        if current == number:
            raise ApiError(409, "dependency_cycle", "This dependency would create a cycle.")
        if current in seen:
            continue
        seen.add(current)
        pending.extend(graph.get(current, ()))
