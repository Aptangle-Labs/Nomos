# Human decisions and completion evidence

Use **Decision Queue** to ask a concrete question with options and a recommendation. Requests are project-readable; do not paste private messages into them. An admin resolves or cancels with a result and reason. The result and main-channel decision message commit together. The existing Decisions view and `/decisions` API still list decision messages; the new lifecycle API is `/decision-requests`.

**Attention** combines pending decisions, completion proposals, reply requests and managed claims whose owner was removed/revoked or whose attached session ended. Select Across All Projects for the admin-wide queue. It does not infer failure from inactivity. Resolved work leaves the queue; offsets can shift while people act, so refresh to reconcile.

Ticket detail has **Completion Evidence** and an optional policy. Existing tickets remain ungated until a policy is configured. The admin may require tests, reviews or artifacts and/or human approval. Required evidence is bound to a full source commit and optional SHA-256 worktree fingerprint. These references are reported, not independently verified by Nomos. Passing tests need a command and zero exit code. Artifact evidence needs an owned, unbound project upload; it is attached to a ticket comment to use existing download authorization.

Evidence is append-only through the API. The latest report for each required kind must pass and match the policy's source references. A newer failure overrides an older pass. Changing evidence or policy increments the ticket revision. A completion proposal snapshots ticket revision, policy revision and the evidence digest, and moves the ticket to awaiting-human. Admin approval checks that snapshot, dependencies and evidence again, then records approval and moves to done in one transaction. Stale proposals require a fresh submission; rejection records a reason without silently changing ownership. Direct PATCH and board moves use the same completion gate. This is a cooperative workflow, not proof of test execution or filesystem isolation.

New mutations accept optional `Idempotency-Key`. Reuse the same key and body when retrying after an uncertain response. Lists are bounded/paginated. All routes below are under `/api/projects/{id}` except admin attention:

| Route | Purpose |
|---|---|
| `POST/GET /decision-requests` | Request and list human decisions |
| `POST /decision-requests/{id}/resolve` | Admin result/reason with expected revision |
| `GET /tickets/{n}/evidence` | Evidence page, policy, latest 20 proposals |
| `PUT /tickets/{n}/completion-policy` | Admin policy with expected ticket revision |
| `POST /tickets/{n}/evidence` | Report tests/review/artifact against a source |
| `POST /tickets/{n}/completion-proposals` | Snapshot and request review |
| `POST /completion-proposals/{id}/review` | Admin approve/reject with reason |
| `GET /api/admin/attention` | Bounded aggregate queue, optional project filter |

Decision requests, policies, evidence and proposals are included in project exports and removed on project deletion. Attachment bytes in exports remain best-effort, as described by the export manifest.

Policies and evidence can optionally bind `workspace_id`. Evidence captures the latest known monitor observation for that workspace. A later observation makes that evidence/proposal stale; unrelated edits in the same bound workspace are conservatively included. New proposals and reviews recheck this marker. Without a workspace binding, source matching relies on submitted commit/fingerprint metadata. A changed live checkout is not atomically locked by review.

Decision requests accept timezone-aware `required_by` and document/ticket source references. Sources are project-scoped and revisions are pinned at request time. The initial queue orders by age and supports project filtering; due times are stored metadata, not automated escalation. Rejection optionally takes `return_status: "in-progress"` or `"blocked"`; omitted status preserves the ticket state. A `return_status` rejection requires the proposal to still match the current ticket revision, so clearing out an obsolete proposal cannot overwrite state that moved on; reject a stale proposal without `return_status`.
