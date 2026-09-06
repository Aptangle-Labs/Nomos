"""Persist a fetched batch and its cursor atomically before emitting anything."""

import json
import os
import sqlite3
from pathlib import Path


class Spool:
    def __init__(self, path, scope, capacity=10000):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.conn = sqlite3.connect(path)
        os.chmod(path, 0o600)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(
            "CREATE TABLE IF NOT EXISTS cursors(scope TEXT PRIMARY KEY, cursor INTEGER);"
            "CREATE TABLE IF NOT EXISTS spool(scope TEXT,id INTEGER,payload TEXT,emitted INTEGER DEFAULT 0,"
            "PRIMARY KEY(scope,id));"
        )
        self.scope = scope
        self.capacity = capacity

    @property
    def cursor(self):
        row = self.conn.execute("SELECT cursor FROM cursors WHERE scope=?", (self.scope,)).fetchone()
        return row[0] if row else 0

    def ingest(self, events, cursor):
        with self.conn:
            for event in events:
                self.conn.execute(
                    "INSERT OR IGNORE INTO spool(scope,id,payload) VALUES(?,?,?)",
                    (self.scope, event["id"], json.dumps(event)),
                )
            count = self.conn.execute(
                "SELECT COUNT(*) FROM spool WHERE scope=? AND emitted=0", (self.scope,)
            ).fetchone()[0]
            if count > self.capacity:
                raise BufferError("Spool capacity reached; cursor was not advanced.")
            self.conn.execute(
                "INSERT INTO cursors VALUES(?,?) ON CONFLICT(scope) DO UPDATE "
                "SET cursor=MAX(cursor,excluded.cursor)",
                (self.scope, cursor),
            )
            # Emitted rows are no longer pending transport. Server inbox retains obligations.
            self.conn.execute(
                "DELETE FROM spool WHERE scope=? AND emitted=1 AND id<=?", (self.scope, self.cursor)
            )

    def pending(self):
        return [
            json.loads(r[0])
            for r in self.conn.execute(
                "SELECT payload FROM spool WHERE scope=? AND emitted=0 ORDER BY id", (self.scope,)
            )
        ]

    def emitted(self, event_id):
        with self.conn:
            self.conn.execute("UPDATE spool SET emitted=1 WHERE scope=? AND id=?", (self.scope, event_id))

    def close(self):
        self.conn.close()
