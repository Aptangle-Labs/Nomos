CREATE TABLE workspaces (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 name TEXT NOT NULL, root TEXT NOT NULL,
 is_default INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
 UNIQUE(project_id,root)
);
CREATE UNIQUE INDEX idx_default_workspace ON workspaces(project_id) WHERE is_default=1;
INSERT INTO workspaces(project_id,name,root,is_default,created_at)
 SELECT id,'Default',working_dir,1,created_at FROM projects WHERE working_dir!='';
INSERT OR IGNORE INTO workspaces(project_id,name,root,is_default,created_at)
 SELECT a.project_id,'Monitor',a.path,CASE WHEN p.working_dir='' THEN 1 ELSE 0 END,a.created_at
 FROM audit_watches a JOIN projects p ON p.id=a.project_id;
CREATE TABLE workspace_watches (
 workspace_id INTEGER PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 path TEXT NOT NULL, is_legacy INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_legacy_watch ON workspace_watches(project_id) WHERE is_legacy=1;
INSERT INTO workspace_watches SELECT w.id,a.project_id,a.path,1,a.created_at
 FROM audit_watches a JOIN workspaces w ON w.project_id=a.project_id AND w.root=a.path;
DROP TABLE audit_watches;
ALTER TABLE workspace_watches RENAME TO audit_watches;
CREATE TABLE path_reservations (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
 ticket_id INTEGER REFERENCES tickets(id) ON DELETE CASCADE,
 owner_agent_id INTEGER REFERENCES agents(id) ON DELETE SET NULL,
 owner_alias TEXT NOT NULL, path TEXT NOT NULL, canonical_path TEXT NOT NULL,
 is_directory INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','released')),
 revision INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL,
 created_at TEXT NOT NULL, released_at TEXT
);
CREATE INDEX idx_reservations_active ON path_reservations(project_id,state,id);
