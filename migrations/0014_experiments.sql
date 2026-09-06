CREATE TABLE experiment_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 manifest TEXT NOT NULL, submitted_by TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE usage_samples (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 run_id INTEGER NOT NULL REFERENCES experiment_runs(id) ON DELETE CASCADE,
 session_id INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL, sample_id TEXT NOT NULL,
 unit TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('delta','cumulative')),
 epoch TEXT NOT NULL, sequence INTEGER NOT NULL,
 quantity REAL NOT NULL CHECK(quantity>=0), currency TEXT, pricing_source TEXT,
 submitted_by TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(run_id,session_id,source,sample_id),
 UNIQUE(run_id,session_id,source,unit,epoch,sequence)
);
CREATE TABLE resume_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 agent_id INTEGER REFERENCES agents(id) ON DELETE SET NULL,
 ticket_id INTEGER REFERENCES tickets(id) ON DELETE CASCADE,
 created_at TEXT NOT NULL, progress_at TEXT, progress_summary TEXT
);
CREATE TABLE reservation_conflicts (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 workspace_id INTEGER REFERENCES workspaces(id) ON DELETE SET NULL,
 actor_alias TEXT NOT NULL, path TEXT NOT NULL, conflicts TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX idx_runs_project ON experiment_runs(project_id,id);
CREATE INDEX idx_usage_run ON usage_samples(run_id,id);
