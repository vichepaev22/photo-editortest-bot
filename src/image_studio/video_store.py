"""Изолированный журнал админских видео: без фотобаланса и повторных API-запусков."""

import json
import re
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class VideoRepository:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.db = self.root / "usage.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS video_jobs(
                    id TEXT PRIMARY KEY, owner INTEGER NOT NULL, created REAL NOT NULL,
                    state TEXT NOT NULL, data TEXT NOT NULL, error TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS video_calls(
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, kind TEXT NOT NULL, model TEXT NOT NULL,
                    status TEXT NOT NULL, seconds REAL NOT NULL, usage TEXT NOT NULL DEFAULT '{}',
                    request_id TEXT, created REAL NOT NULL);
            """)
        self.db.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE video_jobs SET state='review',error='interrupted' WHERE state IN ('analyzing','rendering')")
            db.execute("UPDATE video_calls SET status='unknown' WHERE status='started'")

    def directory(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("invalid_video_id")
        path = (self.root / identifier).resolve()
        if path.parent != self.root:
            raise ValueError("invalid_video_path")
        return path

    def create(self, owner):
        identifier = uuid.uuid4().hex
        path = self.directory(identifier)
        path.mkdir(mode=0o700)
        with self.connect() as db:
            db.execute("INSERT INTO video_jobs(id,owner,created,state,data) VALUES(?,?,?,'upload','{}')",
                       (identifier, owner, time.time()))
        return self.get(owner, identifier)

    def get(self, owner, identifier):
        self.directory(identifier)
        with self.connect() as db:
            row = db.execute("SELECT id,owner,created,state,data,error FROM video_jobs WHERE id=? AND owner=?",
                             (identifier, owner)).fetchone()
        if not row:
            return None
        return {"id": row[0], "owner": row[1], "created": row[2], "state": row[3],
                "data": json.loads(row[4]), "error": row[5]}

    def save(self, owner, identifier, *, state=None, error=None, **data):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data,state,error FROM video_jobs WHERE id=? AND owner=?", (identifier, owner)).fetchone()
            if not row:
                raise ValueError("video_not_found")
            updated = json.loads(row[0]) | data
            db.execute("UPDATE video_jobs SET data=?,state=?,error=? WHERE id=? AND owner=?",
                       (json.dumps(updated, ensure_ascii=False), state or row[1],
                        row[2] if error is None else error, identifier, owner))

    def claim(self, owner, identifier, expected, state):
        with self.connect() as db:
            return db.execute("""UPDATE video_jobs SET state=? WHERE id=? AND owner=? AND state=?
                AND NOT EXISTS(SELECT 1 FROM video_jobs WHERE state IN ('analyzing','rendering') AND id!=?)""",
                              (state, identifier, owner, expected, identifier)).rowcount == 1

    def recent(self, owner):
        with self.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM video_jobs WHERE owner=? ORDER BY created DESC LIMIT 20", (owner,))]
        return [self.get(owner, identifier) for identifier in ids]

    def call_start(self, owner, identifier, kind, model, seconds=0):
        if not self.get(owner, identifier):
            raise ValueError("video_not_found")
        call = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO video_calls(id,job_id,kind,model,status,seconds,created) VALUES(?,?,?,?,'started',?,?)",
                       (call, identifier, kind, model, seconds, time.time()))
        return call

    def call_finish(self, call, status, *, usage=None, request_id=None):
        with self.connect() as db:
            db.execute("UPDATE video_calls SET status=?,usage=?,request_id=? WHERE id=? AND status='started'",
                       (status, json.dumps(usage if isinstance(usage, dict) else {}), request_id, call))

    def calls(self, owner, identifier):
        with self.connect() as db:
            rows = db.execute("""SELECT c.kind,c.model,c.status,c.seconds,c.usage,c.request_id,c.created
                FROM video_calls c JOIN video_jobs j ON j.id=c.job_id
                WHERE c.job_id=? AND j.owner=? ORDER BY c.created""", (identifier, owner)).fetchall()
        return [dict(zip(("kind", "model", "status", "seconds", "usage", "request_id", "created"),
                         row[:4] + (json.loads(row[4]),) + row[5:])) for row in rows]

    def cleanup(self):
        with self.connect() as db:
            rows = db.execute("SELECT id FROM video_jobs WHERE created<? AND state NOT IN ('analyzing','rendering','expired')",
                              (time.time() - 86400,)).fetchall()
            for (identifier,) in rows:
                path = self.directory(identifier)
                if path.exists():
                    shutil.rmtree(path)
                db.execute("UPDATE video_jobs SET state='expired',data='{}',error='' WHERE id=?", (identifier,))
