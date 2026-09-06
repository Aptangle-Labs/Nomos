-- Dashboard aggregations filter by project + occurrence time; without these
-- every range query scans the whole project history (codex review of #38).
CREATE INDEX idx_events_project_time ON events(project_id, created_at);
CREATE INDEX idx_evidence_project_time ON work_evidence(project_id, created_at);
CREATE INDEX idx_conflicts_project_time ON reservation_conflicts(project_id, created_at);
CREATE INDEX idx_receipts_project_time ON inbox_receipts(project_id, created_at);
CREATE INDEX idx_transitions_project_time ON ticket_transitions(project_id, created_at);
