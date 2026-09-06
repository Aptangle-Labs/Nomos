# Local review for issue #33

Work is on private Agent-Central branch `feature/33-dependable-agent-collaboration`, draft PR #34. Do not merge or publish until functional review is complete. No changes were made to the public Nomos repository.

## Setup

Fetch the branch in Agent-Central and use a separate checkout. Copy your existing data into a test directory while the source instance is stopped, or start with empty test data. Set `NOMOS_DATA_DIR` to that copy before startup; migrations 0008–0015 are additive except the monitor-table migration, which preserves legacy watch configuration. An older binary should not be pointed at the upgraded database. Roll back by restoring the pre-upgrade data copy and prior code together.

```sh
git fetch origin
git switch feature/33-dependable-agent-collaboration
python -m pip install -e '.[dev]'
ruff check server/ tests/ nomos_client/ benchmarks/
python -m pytest tests/ -q
```

The optional CLI command is `nomos` (or `python -m nomos_client.cli`). Use a test agent key in `NOMOS_API_KEY`; use the browser as local admin as before. Keep source checkouts and monitor paths disposable during these tests.

## Functional checklist

1. **Existing workflows:** create/join a project, send channel/DM messages, edit documents, use board drag/drop, revoke a test agent, and verify audit integrity. Confirm old decision messages remain in Decisions.
2. **Reply Requests:** request a reply from a named agent. Start `nomos --project ID watch --once`, then run inbox/ack/resolve. Verify delivery, reading, acknowledgment and resolution remain distinct. Restart the helper with the same state path; pending requests survive and another agent's DM stays private.
3. **Resume Context:** create a session and a checkpoint with a document revision. Edit the document; resume shows the pinned source and changed status. Check a small byte budget and markdown output. Another agent cannot read a private checkpoint.
4. **Coordination:** add a prerequisite, try a cycle, and race two agents to claim-next. Only one agent wins. Create a shared checkpoint, offer and accept a managed handoff. The old owner cannot complete with its stale generation. Check board conflicts prompt a reload instead of overwriting newer state.
5. **Decisions/review:** request and resolve a human decision; verify the main-channel announcement. Configure required test evidence and human review. Try direct PATCH and board completion before approval. Add evidence, propose, then edit policy/evidence or modify a monitored bound workspace: stale approval must fail. Submit fresh evidence/proposal, approve and confirm done. Test rejection with a reason.
6. **Workspaces:** register two existing checkouts and enable both monitors. Edit the same relative filename in each; audit attribution stays workspace-scoped. Reserve a directory and try an overlapping file from another agent. Separate checkouts remain independent. Release/transfer after stopping the old runner. Delete only a disposable project and verify monitors stop while source files remain.
7. **Outcomes/export:** inspect sample counts, reopens, review acceptance and unknown usage. Import a completed experiment manifest and replay a usage sample. Check exported sessions, checkpoints, notes/todos, dependencies, evidence, workspaces and experiment metadata; verify no API keys/hashes are exported.
8. **Mac/UI:** check navigation, dark/light themes, narrow windows, long content, alerts/error states, and filesystem path behavior on your volume. Automated browser verification in the implementation environment did not complete; this is a specific remaining review item.

## Review boundaries

The phase comments on #33 record implementation self-reviews and fixes, not an independent human review. Actual runner adapters, model-outcome paired trials, p95 runner acceptance latency, and Mac functional testing are not claimed complete. The benchmark harness validates reported observations and prepares trials; it does not run models or prove productivity gains. Evidence is reported, monitor attribution is heuristic, and reservations are cooperative.
