"""Completion gates are cooperative and check reported evidence, not runner truth."""

import hashlib
import json

from .errors import ApiError


def policy_for(conn, row):
    policy = conn.execute("SELECT * FROM completion_policies WHERE ticket_id=?", (row["id"],)).fetchone()
    return (
        dict(policy)
        if policy
        else dict(
            required_kinds="[]",
            human_review=0,
            source_commit="",
            worktree_fingerprint="",
            revision=0,
            workspace_id=None,
        )
    )


def evidence_digest(conn, ticket_id):
    rows = [
        dict(r)
        for r in conn.execute("SELECT * FROM work_evidence WHERE ticket_id=? ORDER BY id", (ticket_id,))
    ]
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def check_evidence(conn, row, policy):
    missing = []
    for kind in json.loads(policy["required_kinds"]):
        evidence = conn.execute(
            "SELECT * FROM work_evidence WHERE ticket_id=? AND kind=? ORDER BY id DESC LIMIT 1",
            (row["id"], kind),
        ).fetchone()
        if (
            evidence is None
            or evidence["outcome"] != "pass"
            or any(evidence[k] != policy[k] for k in ("source_commit", "worktree_fingerprint"))
        ):
            missing.append(kind)
        elif policy.get("workspace_id") is not None and (
            evidence["workspace_id"] != policy["workspace_id"]
            or evidence["observation_id"]
            != latest_observation(conn, row["project_id"], policy["workspace_id"])
        ):
            missing.append(kind)
        elif kind == "artifact" and evidence["attachment_id"] is None:
            missing.append(kind)
    if missing:
        raise ApiError(
            409, "evidence_required", "Current passing evidence is required.", {"missing_kinds": missing}
        )


def completion_guard(conn, row):
    policy = policy_for(conn, row)
    check_evidence(conn, row, policy)
    if policy["human_review"]:
        approved = conn.execute(
            "SELECT 1 FROM completion_proposals WHERE ticket_id=? AND ticket_revision=? AND policy_revision=? AND evidence_digest=? AND state='approved'",
            (row["id"], row["revision"], policy["revision"], evidence_digest(conn, row["id"])),
        ).fetchone()
        if not approved:
            raise ApiError(409, "review_required", "Submit a completion proposal for human review.")


def latest_observation(conn, pid, workspace_id):
    if workspace_id is None:
        return 0
    return conn.execute(
        "SELECT COALESCE(MAX(id),0) FROM audit_log WHERE project_id=? AND source='monitor' AND json_extract(detail,'$.workspace_id')=?",
        (pid, workspace_id),
    ).fetchone()[0]
