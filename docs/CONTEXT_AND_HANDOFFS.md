# Restart context

`GET /api/projects/{pid}/context` returns a deterministic JSON context pack.
Use `ticket_number=N` to focus on a ticket and `max_bytes=32000` to choose a
budget (1024–128000). The pack prioritizes reply requests, assignments and
checkpoints, then personal notes and recent decisions/messages. It reports
truncation and continuation links instead of pretending all history fits.

`nomos --project 1 resume --ticket 12` prints the same pack. The human console
has a **Resume Context** view with source links and changed-revision notices.
No model calls, hidden reasoning restoration, or embeddings are involved.

Create an agent checkpoint with `POST /api/projects/{pid}/checkpoints`:

```json
{
  "purpose": "Finish parser ticket",
  "ticket_number": 12,
  "completed": "Parser handles valid input",
  "next_action": "Add the invalid-checksum test",
  "blockers": [],
  "sources": [{"kind": "document", "id": 1, "revision": 2}],
  "shared": true
}
```

`nomos --project 1 checkpoint checkpoint.json` submits a JSON file. Source
kinds are message, document and ticket; `id` is the record ID, not ticket
number. Omitted document revisions pin the current revision at creation.
Checkpoints are immutable; append a new one at each milestone. Document
references retain their old body and expose `current_revision` and `changed`.

Checkpoints default to their author and the admin. Shared checkpoints reject
DM sources. Every read rechecks referenced sources, including channel
membership. A checkpoint with an inaccessible or deleted source is omitted;
its cached text is not exposed through the context pack. Authors are
responsible for keeping unreferenced private text out of shared checkpoints.

`POST /sessions` records optional runner/model metadata; `POST /sessions/{id}/end`
ends an owned session without releasing tickets. Checkpoints may reference an
active owned `session_id`. Revocation ends sessions and drops consumer liveness
records, while historical checkpoints survive under their stored alias.

The pack and its `snapshot_event_id` use one read snapshot. A later write is
available after that cursor. The pack is not exhaustive: follow continuation
links when truncated, and keep the inbox's own replay cursor independent.
Reading context never marks a mention seen or resolves a request.

Project export format 2 includes scratchpad bodies, todos, sessions,
checkpoints and requests/receipts. Database rows use a consistent read
snapshot; attachment copying is explicitly best-effort and missing files
are listed in the manifest. API keys and their hashes are excluded.

Checkpoints also accept optional `workspace_id`, full `source_commit`, and a SHA-256 `worktree_fingerprint`. Workspace IDs are validated in the project; commit/worktree facts remain reported. Changed document references include the current body as supplemental context, subject to the overall byte cap. `format=markdown` returns a bounded readable rendition and `X-Nomos-Snapshot-Event-ID`; the JSON rendition supplies full continuation metadata.
