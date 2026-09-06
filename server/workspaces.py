"""Workspace identity and conservative cooperative path conflict checks."""

from pathlib import Path, PurePosixPath

from .db import utc_now
from .errors import ApiError


def workspace_row(conn, pid, wid):
    row = conn.execute("SELECT * FROM workspaces WHERE id=? AND project_id=?", (wid, pid)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "Workspace not found.")
    return row


def ensure_workspace(conn, pid, root, name="Default", default=False):
    if default:
        conn.execute("UPDATE workspaces SET is_default=0 WHERE project_id=?", (pid,))
    conn.execute(
        "INSERT INTO workspaces(project_id,name,root,is_default,created_at) VALUES(?,?,?,?,?) ON CONFLICT(project_id,root) DO UPDATE SET is_default=MAX(workspaces.is_default,excluded.is_default)",
        (pid, name, root, default, utc_now()),
    )
    return conn.execute("SELECT * FROM workspaces WHERE project_id=? AND root=?", (pid, root)).fetchone()


def default_workspace_id(conn, pid):
    row = conn.execute(
        "SELECT workspace_id FROM audit_watches WHERE project_id=? AND is_legacy=1", (pid,)
    ).fetchone()
    if row:
        return row[0]
    row = conn.execute("SELECT id FROM workspaces WHERE project_id=? AND is_default=1", (pid,)).fetchone()
    return row[0] if row else None


def normalized_path(root, value, is_directory=False):
    if not value or "\\" in value or "\x00" in value:
        raise ApiError(422, "invalid_path", "Use a nonempty relative POSIX path.")
    rel = PurePosixPath(value)
    if rel.is_absolute() or ".." in rel.parts or (str(rel) == "." and not is_directory):
        raise ApiError(422, "invalid_path", "Path must stay inside the workspace.")
    root_path = Path(root).resolve()
    resolved = (root_path / str(rel)).resolve()
    if not resolved.is_relative_to(root_path):
        raise ApiError(422, "invalid_path", "Symlink escapes the workspace.")
    # Conservative on both case-sensitive and case-insensitive volumes.
    return str(rel), str(resolved).casefold()


def overlaps(path, directory, other, other_directory):
    return (
        path == other
        or (directory and other.startswith(path.rstrip("/") + "/"))
        or (other_directory and path.startswith(other.rstrip("/") + "/"))
    )
