CREATE TABLE decision_requests (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 ticket_id INTEGER REFERENCES tickets(id) ON DELETE CASCADE,
 requester_id INTEGER NOT NULL, requester_alias TEXT NOT NULL,
 question TEXT NOT NULL, options TEXT NOT NULL, recommendation TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','resolved','cancelled')),
 revision INTEGER NOT NULL DEFAULT 0, result TEXT, reason TEXT,
 message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
 created_at TEXT NOT NULL, resolved_at TEXT
);
CREATE INDEX idx_decisions_pending ON decision_requests(project_id,state,id);
CREATE TABLE completion_policies (
 ticket_id INTEGER PRIMARY KEY REFERENCES tickets(id) ON DELETE CASCADE,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 required_kinds TEXT NOT NULL DEFAULT '[]', human_review INTEGER NOT NULL DEFAULT 0,
 source_commit TEXT NOT NULL DEFAULT '', worktree_fingerprint TEXT NOT NULL DEFAULT '',
 revision INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
CREATE TABLE work_evidence (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK(kind IN ('test','review','artifact')),
 outcome TEXT NOT NULL CHECK(outcome IN ('pass','fail','unknown')),
 summary TEXT NOT NULL, command TEXT NOT NULL, exit_code INTEGER,
 source_commit TEXT NOT NULL, worktree_fingerprint TEXT NOT NULL,
 attachment_id INTEGER REFERENCES attachments(id) ON DELETE SET NULL,
 submitted_by TEXT NOT NULL, provenance TEXT NOT NULL DEFAULT 'reported', created_at TEXT NOT NULL
);
CREATE INDEX idx_evidence_ticket ON work_evidence(ticket_id,id);
CREATE TABLE completion_proposals (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
 ticket_revision INTEGER NOT NULL, policy_revision INTEGER NOT NULL, evidence_digest TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','approved','rejected')),
 submitted_by TEXT NOT NULL, reviewed_by TEXT, reason TEXT,
 created_at TEXT NOT NULL, reviewed_at TEXT
);
CREATE INDEX idx_proposals_pending ON completion_proposals(project_id,state,id);
