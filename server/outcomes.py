"""Explicit outcome definitions. Historical missing observations remain unknown."""

import json
from datetime import datetime
from statistics import median


def seconds(start, end):
    return max(0.0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())


def distribution(values):
    return {
        "samples": len(values),
        "median_seconds": median(values) if values else None,
        "total_seconds": sum(values) if values else None,
    }


def usage_totals(conn, run_id):
    groups = {}
    for row in conn.execute("SELECT * FROM usage_samples WHERE run_id=?", (run_id,)):
        key = (
            row["session_id"],
            row["source"],
            row["unit"],
            row["epoch"],
            row["currency"],
            row["pricing_source"],
        )
        current = groups.setdefault(key, {"value": 0, "samples": 0, "mode": row["mode"]})
        current["value"] = (
            max(current["value"], row["quantity"])
            if row["mode"] == "cumulative"
            else current["value"] + row["quantity"]
        )
        current["samples"] += 1
    return [
        {
            "session_id": k[0] or None,
            "source": k[1],
            "unit": k[2],
            "epoch": k[3],
            "currency": k[4],
            "pricing_source": k[5],
            **v,
        }
        for k, v in groups.items()
    ]


def outcomes(conn, pid):
    transitions = conn.execute(
        "SELECT * FROM ticket_transitions WHERE project_id=? ORDER BY id", (pid,)
    ).fetchall()
    previous = {}
    durations = {}
    reopened = 0
    completed_ids = set()
    completion_events = 0
    for row in transitions:
        if row["from_status"] == row["to_status"]:
            continue  # Assignment and generation changes are not status boundaries.
        prior = previous.get(row["ticket_id"])
        if prior:
            durations.setdefault(prior["to_status"], []).append(
                seconds(prior["created_at"], row["created_at"])
            )
        previous[row["ticket_id"]] = row
        if row["to_status"] == "done":
            completed_ids.add(row["ticket_id"])
            completion_events += 1
        if row["from_status"] == "done" and row["to_status"] != "done":
            reopened += 1
    latency = {}
    for kind in ("delivered", "acknowledged", "resolved"):
        latency[kind] = distribution(
            [
                seconds(r[0], r[1])
                for r in conn.execute(
                    "SELECT q.created_at,r.created_at FROM inbox_receipts r JOIN inbox_requests q ON q.id=r.request_id WHERE q.project_id=? AND r.kind=?",
                    (pid, kind),
                )
            ]
        )
    decisions = [
        seconds(r[0], r[1])
        for r in conn.execute(
            "SELECT created_at,resolved_at FROM decision_requests WHERE project_id=? AND state='resolved'",
            (pid,),
        )
    ]
    resume = [
        seconds(r[0], r[1])
        for r in conn.execute(
            "SELECT created_at,progress_at FROM resume_events WHERE project_id=? AND progress_at IS NOT NULL",
            (pid,),
        )
    ]
    # "First review" means the review that happened first, not the proposal
    # created first: an admin may approve a fresh proposal before clearing
    # out an obsolete one.
    reviews = conn.execute(
        "SELECT * FROM completion_proposals WHERE project_id=? AND state!='pending' "
        "ORDER BY reviewed_at, id",
        (pid,),
    ).fetchall()
    first_reviews = {}
    for review in reviews:
        first_reviews.setdefault(review["ticket_id"], review["state"])
    ticket_count = conn.execute("SELECT COUNT(*) FROM tickets WHERE project_id=?", (pid,)).fetchone()[0]
    conflicts = conn.execute(
        "SELECT COUNT(*) FROM reservation_conflicts WHERE project_id=?", (pid,)
    ).fetchone()[0]
    from .db import utc_now

    now = utc_now()
    ages = [
        seconds(r[0], now)
        for r in conn.execute(
            "SELECT r.created_at FROM inbox_requests r LEFT JOIN messages m ON m.id=r.message_id WHERE r.project_id=? AND r.state IN ('pending','acknowledged') AND (r.message_id IS NULL OR m.deleted=0)",
            (pid,),
        )
    ]
    return {
        "scope": "retained project history since instrumentation",
        "as_of": now,
        "completion_events": completion_events,
        "unique_tickets_completed": len(completed_ids),
        "reopens": reopened,
        "status_durations": {k: distribution(v) for k, v in durations.items()},
        "history": {
            "tickets": ticket_count,
            "tickets_with_status_transitions": len(previous),
            "pre_migration_durations": None,
            "open_intervals_excluded": True,
        },
        "request_latency": latency,
        "unanswered_request_age": distribution(ages),
        "decision_wait": distribution(decisions),
        "resume_to_reported_progress": distribution(resume),
        "review_rejections": sum(r["state"] == "rejected" for r in reviews),
        "first_review_acceptance": {
            "accepted": sum(s == "approved" for s in first_reviews.values()),
            "reviewed_tickets": len(first_reviews),
            "rate": sum(s == "approved" for s in first_reviews.values()) / len(first_reviews)
            if first_reviews
            else None,
        },
        "reservation_conflict_attempts": conflicts,
        "definitions": {
            "status_durations": "Closed intervals between observed status transitions; edits and ownership-only transitions do not reset clocks. Initial/pre-instrumentation and still-open intervals are excluded.",
            "completion_events": "Observed transitions to done, including repeated completions after reopening; unique ticket count is separate.",
            "first_review_acceptance": "Outcome of the first reviewed proposal per ticket. Pending proposals are excluded.",
            "resume_to_reported_progress": "Explicit resume start to first runner-reported progress; not independently judged correctness.",
            "usage": "Optional reported samples, grouped by source/unit/epoch; missing is unknown. No inferred token prices.",
            "reservation_conflict_attempts": "Rejected acquisition attempts; a keyed replay does not add a second conflict record.",
        },
    }


def manifest_view(row):
    data = dict(row)
    data["manifest"] = json.loads(data["manifest"])
    return data
