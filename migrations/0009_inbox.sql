CREATE TABLE inbox_consumers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    agent_id INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    UNIQUE(project_id, agent_id, name)
);
CREATE TABLE inbox_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    issuer_id INTEGER NOT NULL,
    issuer_alias TEXT NOT NULL,
    target_agent_id INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    message_id INTEGER REFERENCES messages(id) ON DELETE CASCADE,
    comment_id INTEGER REFERENCES ticket_comments(id) ON DELETE CASCADE,
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','acknowledged','resolved','cancelled')),
    revision INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((message_id IS NULL) != (comment_id IS NULL)),
    UNIQUE(message_id,target_agent_id),
    UNIQUE(comment_id,target_agent_id)
);
CREATE INDEX idx_inbox_target ON inbox_requests(project_id,target_agent_id,state,id);
CREATE TABLE inbox_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    request_id INTEGER NOT NULL REFERENCES inbox_requests(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('delivered','acknowledged','resolved','cancelled')),
    actor_id INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(request_id,kind)
);
