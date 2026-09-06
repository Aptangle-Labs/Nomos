# Dependencies and ownership

Tickets keep the existing status vocabulary. `GET /tickets?ready=true` lists
unassigned open work whose prerequisites are done or explicitly waived.
`wontfix` does not satisfy a dependency. `blocked` still needs explicit
unblocking. `PUT /tickets/{number}/dependencies` replaces the list with
`expected_revision` and `dependencies` containing `prerequisite_number`,
optional `waived`, and a required reason for each waiver. Cycles and duplicate
edges are rejected under the write lock. Incoming edges must be removed or
redirected before deleting a prerequisite.

`POST /tickets/claim-next {}` atomically chooses ready work by priority then
oldest number. It returns `data.ticket=null` when no work is ready. Add `label`
or an owned active `session_id` to constrain/annotate selection. Use an
Idempotency-Key; a replay describes the old claim, so read current ownership
before editing. Concurrent calls cannot claim the same ticket twice.

`POST /tickets/{number}/claim {"managed":true}` opts a direct claim into the
same ownership protocol. The ordinary empty-body claim remains compatible
with old clients. Dependency and terminal-state checks apply to both.

Managed status/ownership changes require `expected_revision` and, for agent
callers, `claim_generation`. Only the current owner can make them. Ordinary
discussion/title edits remain collaborative. Managed assignment cannot be
changed by an agent through an old PATCH path. The human can explicitly
reassign with a revision, which invalidates the old managed claim. A supplied
`claim_generation` is always validated against the current ticket, even after
a release or reassignment ended managed ownership: a stale token is refused
with 409 rather than silently ignored.

To hand off, create a **shared checkpoint for the ticket**, then POST
`/tickets/{number}/handoffs` with expected revision/generation, `to_agent_id`,
`checkpoint_id`, and `reason`. Ownership stays with the original agent until
the recipient POSTs `/handoffs/{id}/accept`. Acceptance rechecks source access,
revision and generation atomically. Concurrent reassignment makes the offer
stale. `GET /handoffs` lists offers visible to the caller.

`POST /tickets/{number}/release` accepts expected revision/generation and a
reason. Only owner/admin may release. Release clears ownership but preserves
status; explicitly return the ticket to open if it should enter claim-next.
The ticket detail's Coordination panel provides dependency, waiver, transfer,
and release controls for the human.

Revocation/removal invalidates pending offers. Stale liveness is a warning,
never automatic takeover: Nomos does not stop filesystem writes or fence the
old process. Coordinate runner shutdown before transferring live work.

Status/ownership transitions after migration are recorded in
`ticket_transitions`. Old history is not fabricated. The board's status
changes pass through the same validation as ticket PATCH.
