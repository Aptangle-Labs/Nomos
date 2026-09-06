ALTER TABLE tickets ADD COLUMN managed_claim INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tickets ADD COLUMN claim_generation INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tickets ADD COLUMN owner_agent_id INTEGER REFERENCES agents(id) ON DELETE SET NULL;
ALTER TABLE tickets ADD COLUMN claim_session_id INTEGER REFERENCES agent_sessions(id) ON DELETE SET NULL;
ALTER TABLE tickets ADD COLUMN changed_by TEXT NOT NULL DEFAULT 'legacy';
CREATE TABLE ticket_dependencies (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    ticket_number INTEGER NOT NULL,
    prerequisite_number INTEGER NOT NULL,
    waived INTEGER NOT NULL DEFAULT 0 CHECK(waived IN (0,1)),
    waiver_reason TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(project_id,ticket_number,prerequisite_number),
    FOREIGN KEY(project_id,ticket_number) REFERENCES tickets(project_id,number) ON DELETE CASCADE,
    FOREIGN KEY(project_id,prerequisite_number) REFERENCES tickets(project_id,number) ON DELETE RESTRICT,
    CHECK(ticket_number != prerequisite_number),
    CHECK(waived=0 OR length(trim(waiver_reason))>0)
);
CREATE INDEX idx_dependency_prerequisite ON ticket_dependencies(project_id,prerequisite_number);
CREATE TABLE ticket_handoffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    from_agent_id INTEGER,
    to_agent_id INTEGER REFERENCES agents(id) ON DELETE SET NULL,
    checkpoint_id INTEGER NOT NULL REFERENCES work_checkpoints(id),
    expected_revision INTEGER NOT NULL,
    expected_generation INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','accepted','invalidated')),
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX idx_handoff_ticket ON ticket_handoffs(project_id,ticket_id,state);
CREATE TABLE ticket_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    from_assignee TEXT,
    to_assignee TEXT,
    generation INTEGER NOT NULL,
    by_alias TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_transition_ticket ON ticket_transitions(project_id,ticket_id,id);
CREATE TRIGGER ticket_transition AFTER UPDATE ON tickets
WHEN OLD.status IS NOT NEW.status OR OLD.assignee IS NOT NEW.assignee OR OLD.claim_generation != NEW.claim_generation
BEGIN
    INSERT INTO ticket_transitions(project_id,ticket_id,from_status,to_status,from_assignee,to_assignee,generation,by_alias,created_at)
    VALUES(NEW.project_id,NEW.id,OLD.status,NEW.status,OLD.assignee,NEW.assignee,NEW.claim_generation,NEW.changed_by,NEW.updated_at);
    UPDATE ticket_handoffs SET state='invalidated',resolved_at=NEW.updated_at
    WHERE project_id=NEW.project_id AND ticket_id=NEW.id AND state='pending';
END;
