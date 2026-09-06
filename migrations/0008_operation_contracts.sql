-- Additive optimistic concurrency and replay-safe domain writes (#33).
ALTER TABLE tickets ADD COLUMN revision INTEGER NOT NULL DEFAULT 0;
CREATE TRIGGER ticket_revision AFTER UPDATE ON tickets
WHEN NEW.revision = OLD.revision
BEGIN
    UPDATE tickets SET revision = OLD.revision + 1 WHERE id = NEW.id;
END;

CREATE TABLE idempotency_requests (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    actor_id INTEGER NOT NULL,
    operation TEXT NOT NULL,
    request_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, actor_id, operation, request_key)
);
