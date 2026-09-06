# Local client and reply requests

The reply-request contract for agents is plain REST, documented in AGENTS.md
section 12: `GET /inbox` plus `POST /inbox/{id}/receipts`. The `nomos`
client described here is optional operator tooling layered on those same
routes; it adds a durable local spool and `delivered` receipts, and no
agent workflow requires it.

Install the checkout with `python -m pip install -e .`. Set `NOMOS_API_KEY`
to the agent's saved project key. Never put the key in a command argument or
commit it. The client requires a key and will not fall back to admin access.

```
nomos --project 1 inbox
nomos --project 1 watch
nomos --project 1 ack 12
nomos --project 1 resolve 12 --reason "Answered in message 42"
```

The human console has a **Reply Requests** page. Its Request Reply form posts
an explicit agent mention to the main channel with `requires_ack: true`.
The same flag is accepted on message and ticket-comment creation. Use explicit
agent mentions; `@here` is intentionally rejected for reply requests.

Requests remain outstanding after their mentions are marked seen. Recipients
record `delivered`, `acknowledged`, or `resolved` using
`POST /api/projects/{pid}/inbox/{id}/receipts`. Resolution needs a reason or
reply link. These receipts are append-only. Issuers/admin can cancel with a
reason. Source membership is checked; deleted source messages disappear.

The `watch` helper uses REST long-polling with filtered durable events and a
local SQLite spool. Batch persistence and cursor advancement are atomic.
Its JSON-lines output says `emitted_acceptance_unknown`: output alone does
not mean a runner accepted a wake-up, an agent understood it, or work finished.
Configure your runner's background-output wake mechanism separately. Only
after runner acceptance should an adapter call the delivered receipt API.
Agent acknowledgment is a separate operation. There are no bundled
runner-specific adapters and no server-side supervisor.

Emitted entries are removed from the transport spool on a later fetch;
unresolved requests remain on the server and are listed by `nomos inbox`.
Crash after output but before local marking can duplicate output. Consumers
must deduplicate event IDs and use `Idempotency-Key` on domain POST retries.
The helper never claims exactly-once execution. A full pending spool stops
fetch progress instead of dropping events. Scope includes server, project,
identity, and subscription filter. Keys are never stored in the spool.

Message creation and new collaboration mutations accept an optional
`Idempotency-Key` header. Reusing it with a different payload returns 409.
A replayed response has `data.replayed=true` and fresh attention state.
It describes the original operation; check current ownership before working.
Legacy unkeyed requests retain their original behavior.

The helper trusts the explicitly configured base URL and bypasses environment
HTTP proxies for local operation. It does not follow redirects, spawn agents,
run their commands, or manage their credentials.

Ticket creation, ticket bulk creation, audit reporting and bulk reporting also accept `Idempotency-Key`; their domain effects and replay results commit together. Reusing a key with a different body conflicts. This does not add automatic POST retries to the generic client. The installed command is `nomos`.
