"""Project operations dashboard: read-only, project-indexed aggregation."""

import json
from datetime import UTC, datetime, timedelta
from math import ceil
from statistics import median

from .audit import verify_chain


def dashboard(conn, pid, range_name, now=None):
    # Activity categories are disjoint durable records, not a sum of all SSE
    # notifications. Messages: non-system message events, including replies,
    # excluding generated decision rulings (the decision event already counts).
    # Tickets: ticket_created/updated/deleted/comment events; ticket_assigned
    # and awaiting_human notifications are excluded (the update already counts).
    # Files: file_create/edit/delete self-reports plus uncorrelated monitor
    # observations; correlated monitor observations are excluded in BOTH link
    # directions. Platform audit mirrors never count as work.
    # Other: non-file self-reports and document/decision/inbox/completion/
    # checkpoint/reservation/workspace/experiment mutations listed below.
    # Mention, presence, reaction, delivery and system-announcement noise is
    # excluded. All ranges bound occurrence time, latency uses receipt/resolution
    # time, and first review uses the first chronological review per ticket.
    now = now or datetime.now(UTC)
    as_of = now.isoformat()
    cutoff = (
        None
        if range_name == "all"
        else now - timedelta(hours=24 if range_name == "24h" else 24 * int(range_name[:-1]))
    )
    lower = cutoff.isoformat() if cutoff else ""
    hourly = range_name == "24h"
    fmt = "%Y-%m-%dT%H:00:00Z" if hourly else "%Y-%m-%d"
    step = timedelta(hours=1) if hourly else timedelta(days=1)
    project = conn.execute("SELECT settings,created_at FROM projects WHERE id=?", (pid,)).fetchone()
    start = cutoff or max(datetime.fromisoformat(project["created_at"]), now - timedelta(days=89))
    start = start.replace(minute=0, second=0, microsecond=0)
    if not hourly:
        start = start.replace(hour=0)
    # range=all keeps all-history totals, but the activity series only
    # renders from `start`, so the scan is bounded there too.
    activity_lower = max(lower, start.isoformat()) if lower else start.isoformat()
    points = {}
    while start <= now:
        points[start.strftime(fmt)] = dict(t=start.strftime(fmt), messages=0, tickets=0, files=0, other=0)
        start += step

    def scalar(sql, args=()):
        return conn.execute(sql, args).fetchone()[0]

    def count(table, condition="1", time_col=None):
        time_filter = f" AND {time_col}>=? AND {time_col}<=?" if time_col else ""
        args = (pid, lower, as_of) if time_col else (pid,)
        return scalar(f"SELECT COUNT(*) FROM {table} WHERE project_id=? AND {condition}{time_filter}", args)

    linked = """(a.correlated_id IS NOT NULL OR EXISTS
        (SELECT 1 FROM audit_log b WHERE b.project_id=a.project_id AND b.correlated_id=a.id))"""
    other_events = (
        "document_created",
        "document_updated",
        "decision_requested",
        "decision_resolved",
        "inbox_requested",
        "completion_requested",
        "completion_reviewed",
        "evidence_updated",
        "policy_updated",
        "checkpoint_created",
        "reservation_updated",
        "reservation_conflict",
        "workspace_updated",
        "experiment_updated",
    )
    # Date grouping happens in SQLite; only bucket totals cross into Python.
    activity = conn.execute(
        f"""
        WITH classified AS (
          SELECT e.created_at, CASE
            WHEN e.type='message' AND json_extract(e.payload,'$.role') != 'system'
              AND json_extract(e.payload,'$.id') NOT IN
                (SELECT message_id FROM decision_requests
                 WHERE project_id=? AND message_id IS NOT NULL) THEN 'messages'
            WHEN e.type IN ('ticket_created','ticket_updated','ticket_deleted','ticket_comment') THEN 'tickets'
            WHEN e.type='audit' AND a.source='self_report' AND a.action IN ('file_create','file_edit','file_delete') THEN 'files'
            WHEN e.type='audit' AND a.source='monitor' AND NOT {linked}
              AND a.action IN ('file_create','file_edit','file_delete') THEN 'files'
            WHEN e.type='audit' AND a.source='self_report' AND a.action NOT IN ('file_create','file_edit','file_delete') THEN 'other'
            WHEN e.type IN ({",".join("?" for _ in other_events)}) THEN 'other'
          END AS category
          FROM events e LEFT JOIN audit_log a ON e.type='audit'
            AND a.id=json_extract(e.payload,'$.id') AND a.project_id=e.project_id
          WHERE e.project_id=? AND e.created_at>=? AND e.created_at<=?
        ) SELECT strftime(?,created_at) AS t,category,COUNT(*) AS n
          FROM classified WHERE category IS NOT NULL GROUP BY t,category
        """,
        (pid, *other_events, pid, activity_lower, as_of, fmt),
    ).fetchall()
    for row in activity:
        if row["t"] in points:
            points[row["t"]][row["category"]] = row["n"]
    # inbox_updated events are excluded above: a delivered receipt is
    # transport noise. Acknowledge/resolve/cancel receipts are real work.
    receipts = conn.execute(
        "SELECT strftime(?,created_at) AS t, COUNT(*) AS n FROM inbox_receipts "
        "WHERE project_id=? AND kind!='delivered' AND created_at>=? AND created_at<=? GROUP BY t",
        (fmt, pid, activity_lower, as_of),
    ).fetchall()
    for row in receipts:
        if row["t"] in points:
            points[row["t"]]["other"] += row["n"]

    statuses = {
        s: 0
        for s in json.loads(project["settings"]).get(
            "ticket_statuses", ["open", "in-progress", "awaiting-human", "blocked", "done", "wontfix"]
        )
    }
    statuses.update(
        {
            r["status"]: r["n"]
            for r in conn.execute(
                "SELECT status,COUNT(*) n FROM tickets WHERE project_id=? GROUP BY status", (pid,)
            )
        }
    )
    completions = {t: dict(t=t, done=0) for t in points}
    for row in conn.execute(
        """SELECT strftime(?,created_at) t,COUNT(*) n FROM ticket_transitions
        WHERE project_id=? AND to_status='done' AND from_status!='done' AND created_at>=? AND created_at<=?
        GROUP BY t""",
        (fmt, pid, lower, as_of),
    ):
        if row["t"] in completions:
            completions[row["t"]]["done"] = row["n"]
    attribution = dict(correlated=0, self_reported_only=0, monitor_only=0)
    # Correlated counts monitor observations once; self_reported_only counts
    # unmatched reports. Links across a range boundary still establish attribution.
    for row in conn.execute(
        f"""SELECT CASE
        WHEN a.source='monitor' AND {linked} THEN 'correlated'
        WHEN a.source='monitor' THEN 'monitor_only'
        ELSE 'self_reported_only' END category,COUNT(*) n
        FROM audit_log a WHERE a.project_id=? AND a.created_at>=? AND a.created_at<=?
        AND (a.source='monitor' OR (a.source='self_report' AND NOT {linked})) GROUP BY category""",
        (pid, lower, as_of),
    ):
        attribution[row["category"]] = row["n"]

    top_agents = [
        dict(r)
        for r in conn.execute(
            """
        WITH work AS (
          SELECT author_alias alias,COUNT(*) messages,0 tickets,0 audit FROM messages
            WHERE project_id=? AND author_type='agent' AND deleted=0 AND created_at>=? AND created_at<=? GROUP BY alias
          UNION ALL SELECT by_alias,0,COUNT(*),0 FROM ticket_transitions
            WHERE project_id=? AND created_at>=? AND created_at<=? GROUP BY by_alias
          UNION ALL SELECT actor,0,0,COUNT(*) FROM audit_log
            WHERE project_id=? AND actor_type='agent' AND source='self_report' AND created_at>=? AND created_at<=? GROUP BY actor
        ), totals AS (SELECT alias,SUM(messages) messages,SUM(tickets) tickets,SUM(audit) audit,
            SUM(messages+tickets+audit) total FROM work GROUP BY alias)
        SELECT t.*,COALESCE(a.avatar,'') avatar FROM totals t LEFT JOIN agents a ON a.project_id=? AND a.alias=t.alias
        WHERE a.id IS NOT NULL OR EXISTS (SELECT 1 FROM agent_sessions s WHERE s.project_id=? AND s.agent_alias=t.alias)
        ORDER BY total DESC,t.alias COLLATE NOCASE LIMIT 8
        """,
            (pid, lower, as_of) * 3 + (pid, pid),
        )
    ]

    latency = {}
    for name, kind in [("ack", "acknowledged"), ("resolve", "resolved"), ("decision_wait", None)]:
        if kind:
            rows = conn.execute(
                """SELECT q.created_at start,r.created_at end FROM inbox_requests q
                JOIN inbox_receipts r ON r.request_id=q.id WHERE q.project_id=? AND r.kind=?
                AND r.created_at>=? AND r.created_at<=?""",
                (pid, kind, lower, as_of),
            )
        else:
            rows = conn.execute(
                """SELECT created_at start,resolved_at end FROM decision_requests
                WHERE project_id=? AND state='resolved' AND resolved_at>=? AND resolved_at<=?""",
                (pid, lower, as_of),
            )
        values = sorted(
            max(0, (datetime.fromisoformat(r["end"]) - datetime.fromisoformat(r["start"])).total_seconds())
            for r in rows
        )
        latency.update(
            {
                f"{name}_median_s": median(values) if values else None,
                f"{name}_p90_s": values[ceil(0.9 * len(values)) - 1] if values else None,
                f"{name}_samples": len(values),
            }
        )
    first = conn.execute(
        """WITH ranked AS (
        SELECT state,reviewed_at,ROW_NUMBER() OVER (PARTITION BY ticket_id ORDER BY reviewed_at,id) n
        FROM completion_proposals WHERE project_id=? AND state!='pending' AND reviewed_at IS NOT NULL)
        SELECT state,COUNT(*) n FROM ranked WHERE n=1 AND reviewed_at>=? AND reviewed_at<=? GROUP BY state""",
        (pid, lower, as_of),
    ).fetchall()
    first_counts = {r["state"]: r["n"] for r in first}
    reviewed = sum(first_counts.values())
    conflicts = count("reservation_conflicts", time_col="created_at")
    current = count("agents", "revoked=0")
    # Retained join events also remember agents subsequently removed from roster.
    ever = scalar(
        """SELECT COUNT(*) FROM (SELECT id FROM agents WHERE project_id=? UNION
        SELECT json_extract(payload,'$.agent_id') FROM events WHERE project_id=? AND type='agent_joined'
        AND json_extract(payload,'$.agent_id') IS NOT NULL)""",
        (pid, pid),
    )
    tiles = dict(
        agents_online=scalar(
            "SELECT COUNT(*) FROM agents WHERE project_id=? AND revoked=0 AND last_seen>=?",
            (pid, (now - timedelta(minutes=5)).isoformat()),
        ),
        agents_total=current,
        agents_ever=ever,
        sessions_open=count("agent_sessions", "ended_at IS NULL"),
        tickets_open=sum(n for s, n in statuses.items() if s not in ("done", "wontfix")),
        tickets_done_range=count(
            "ticket_transitions", "to_status='done' AND from_status!='done'", "created_at"
        ),
        reply_requests_outstanding=scalar(
            """SELECT COUNT(*) FROM inbox_requests r LEFT JOIN messages m ON m.id=r.message_id
          WHERE r.project_id=? AND r.state IN ('pending','acknowledged') AND (r.message_id IS NULL OR m.deleted=0)""",
            (pid,),
        ),
        decisions_pending=count("decision_requests", "state='pending'"),
        audit_chain_ok=verify_chain(pid)["ok"],
        audit_records=count("audit_log"),
    )
    return dict(
        range=range_name,
        as_of=as_of,
        tiles=tiles,
        activity_series=dict(bucket="hour" if hourly else "day", points=list(points.values())),
        tickets_by_status=statuses,
        completions_series=list(completions.values()),
        attribution=attribution,
        top_agents=top_agents,
        latency=latency,
        gates=dict(
            evidence_pass=count("work_evidence", "outcome='pass'", "created_at"),
            evidence_fail=count("work_evidence", "outcome='fail'", "created_at"),
            proposals_approved=count("completion_proposals", "state='approved'", "reviewed_at"),
            proposals_rejected=count("completion_proposals", "state='rejected'", "reviewed_at"),
            first_review_acceptance_rate=first_counts.get("approved", 0) / reviewed if reviewed else None,
            first_review_samples=reviewed,
        ),
        reservation_conflicts=conflicts,
        attention=dict(
            tickets_awaiting_human=statuses.get("awaiting-human", 0),
            proposals_pending=count("completion_proposals", "state='pending'"),
            reservation_conflicts=conflicts,
        ),
    )
