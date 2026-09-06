ALTER TABLE work_checkpoints ADD COLUMN workspace_id INTEGER REFERENCES workspaces(id) ON DELETE SET NULL;
ALTER TABLE work_checkpoints ADD COLUMN source_commit TEXT NOT NULL DEFAULT '';
ALTER TABLE work_checkpoints ADD COLUMN worktree_fingerprint TEXT NOT NULL DEFAULT '';
ALTER TABLE decision_requests ADD COLUMN required_by TEXT;
ALTER TABLE decision_requests ADD COLUMN source_refs TEXT NOT NULL DEFAULT '[]';
ALTER TABLE work_evidence ADD COLUMN workspace_id INTEGER REFERENCES workspaces(id) ON DELETE SET NULL;
ALTER TABLE work_evidence ADD COLUMN observation_id INTEGER NOT NULL DEFAULT 0;
ALTER TABLE completion_policies ADD COLUMN workspace_id INTEGER REFERENCES workspaces(id) ON DELETE SET NULL;
ALTER TABLE completion_proposals ADD COLUMN observation_id INTEGER NOT NULL DEFAULT 0;
