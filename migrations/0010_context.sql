CREATE TABLE agent_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    agent_id INTEGER REFERENCES agents(id) ON DELETE SET NULL,
    agent_alias TEXT NOT NULL,
    runner TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    ended_at TEXT
);
CREATE TABLE work_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    agent_id INTEGER REFERENCES agents(id) ON DELETE SET NULL,
    agent_alias TEXT NOT NULL,
    session_id INTEGER REFERENCES agent_sessions(id) ON DELETE SET NULL,
    ticket_number INTEGER,
    purpose TEXT NOT NULL,
    completed TEXT NOT NULL DEFAULT '',
    next_action TEXT NOT NULL,
    blockers TEXT NOT NULL DEFAULT '[]',
    source_refs TEXT NOT NULL DEFAULT '[]',
    shared INTEGER NOT NULL DEFAULT 0 CHECK(shared IN (0,1)),
    created_at TEXT NOT NULL
);
CREATE INDEX idx_checkpoints_owner ON work_checkpoints(project_id,agent_id,id);
CREATE INDEX idx_checkpoints_ticket ON work_checkpoints(project_id,ticket_number,id);
