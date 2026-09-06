# Workspaces and cooperative reservations

Open **Workspaces** to register existing checkout paths and enable separate file monitors. Registration creates metadata only. It does not create a Git worktree, run Git, copy AGENTS.md, or delete source files. The older working-directory setup retains its existing explicit AGENTS.md-copy behavior and now registers the directory as a default workspace.

Existing working directories and monitors are migrated into the registry. Old audit rows and their hash inputs are untouched. Each new monitor observation carries `workspace_id`, `before_sha256`, and `after_sha256` in its hashed detail. Correlation matches project, workspace, relative path and the existing time window. It remains a heuristic attribution, not proof of causation. Historical rows without a workspace stay unscoped and do not acquire one retroactively. The Audit view can filter by workspace.

The legacy `/audit/watch` API manages one compatibility watch. Replacing or removing it preserves other workspace monitors. All persisted watches resume on server start. Project deletion stops every associated task and removes metadata, never checkout contents.

Agents reserve files or directories through `POST /api/projects/{id}/reservations` with `workspace_id`, relative POSIX `path`, `is_directory`, and `reason`. Optional ticket references accept ownership revision/generation fields. Reservation acquisition checks all active project reservations in one transaction. Overlapping physical paths conflict even under different registered roots; separate checkouts do not conflict merely because relative filenames match. Prefix comparisons use path segments (`src` does not match `src2`). Paths escaping through `..`, absolute paths or symlinks are rejected. Comparisons case-fold conservatively, which may reject distinct case-only paths on a case-sensitive volume.

`GET /reservations` is paginated. Owner/admin `POST /reservations/{id}/update` uses `expected_revision` and `reason`; omit `to_agent_id` to release, or supply an active project agent to transfer. Reservations do not expire automatically after a heartbeat gap or agent revocation: an old runner may still be editing. Stop that runner before an explicit release/transfer. Admin controls are in Workspaces.

Reservations are cooperative: they do not lock files or contain untrusted processes. A later filesystem/symlink change can invalidate an earlier path check. Source fingerprints and audit correlation do not prove who changed a file. No new external watcher dependency is required.

Additional APIs: `GET/POST /workspaces`; admin `PUT /workspaces/{id}/watch` with `enabled`. Up to 100 workspaces are supported per project. Workspace and reservation metadata are exported and cascade on project deletion.
