"""Small SQLite persistence layer for users, sessions, transcripts and documents.

The agent's own graph state is checkpointed separately by LangGraph; this module only
stores what the UI needs to render (and what the skill builder needs to read back).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('chat', 'skill')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    skill_name TEXT,
    skill_uri TEXT,
    skill_built_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    uri TEXT NOT NULL,
    size INTEGER NOT NULL,
    status TEXT NOT NULL,
    task_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_documents_user ON documents(user_id, created_at DESC);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- helpers -------------------------------------------------------------
    def _one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def _all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def _exec(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    # -- users -----------------------------------------------------------------
    def create_user(self, username: str, password_hash: str) -> dict[str, Any]:
        user = {
            "id": new_id(),
            "username": username,
            "password_hash": password_hash,
            "created_at": now_iso(),
        }
        self._exec(
            "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (user["id"], user["username"], user["password_hash"], user["created_at"]),
        )
        return user

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM users WHERE username = ?", (username,))

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM users WHERE id = ?", (user_id,))

    # -- sessions --------------------------------------------------------------
    def create_session(self, user_id: str, title: str, kind: str) -> dict[str, Any]:
        ts = now_iso()
        session = {
            "id": new_id(),
            "user_id": user_id,
            "title": title,
            "kind": kind,
            "created_at": ts,
            "updated_at": ts,
            "skill_name": None,
            "skill_uri": None,
            "skill_built_at": None,
        }
        self._exec(
            "INSERT INTO sessions (id, user_id, title, kind, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session["id"], user_id, title, kind, ts, ts),
        )
        return session

    def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        return self._all(
            "SELECT * FROM sessions WHERE user_id = ? ORDER BY updated_at DESC", (user_id,)
        )

    def get_session(self, session_id: str, user_id: str) -> dict[str, Any] | None:
        return self._one(
            "SELECT * FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id)
        )

    def touch_session(self, session_id: str, title: str | None = None) -> None:
        if title:
            self._exec(
                "UPDATE sessions SET updated_at = ?, title = ? WHERE id = ?",
                (now_iso(), title, session_id),
            )
        else:
            self._exec("UPDATE sessions SET updated_at = ? WHERE id = ?", (now_iso(), session_id))

    def rename_session(self, session_id: str, title: str) -> None:
        self._exec("UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?", (title, now_iso(), session_id))

    def mark_skill_built(self, session_id: str, skill_name: str, skill_uri: str) -> None:
        self._exec(
            "UPDATE sessions SET skill_name = ?, skill_uri = ?, skill_built_at = ?, updated_at = ? "
            "WHERE id = ?",
            (skill_name, skill_uri, now_iso(), now_iso(), session_id),
        )

    def delete_session(self, session_id: str) -> None:
        self._exec("DELETE FROM messages WHERE session_id = ?", (session_id,))
        self._exec("DELETE FROM sessions WHERE id = ?", (session_id,))

    # -- messages ----------------------------------------------------------------
    def add_message(
        self, session_id: str, role: str, content: str, meta: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        ts = now_iso()
        meta_json = json.dumps(meta or {})
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages (session_id, role, content, meta, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, role, content, meta_json, ts),
            )
            self._conn.commit()
            msg_id = cur.lastrowid
        return {
            "id": msg_id,
            "session_id": session_id,
            "role": role,
            "content": content,
            "meta": meta or {},
            "created_at": ts,
        }

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._all(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC", (session_id,)
        )
        for row in rows:
            try:
                row["meta"] = json.loads(row.get("meta") or "{}")
            except json.JSONDecodeError:
                row["meta"] = {}
        return rows

    # -- documents ---------------------------------------------------------------
    def create_document(
        self,
        user_id: str,
        filename: str,
        uri: str,
        size: int,
        status: str,
        task_id: str | None,
    ) -> dict[str, Any]:
        doc = {
            "id": new_id(),
            "user_id": user_id,
            "filename": filename,
            "uri": uri,
            "size": size,
            "status": status,
            "task_id": task_id,
            "created_at": now_iso(),
        }
        self._exec(
            "INSERT INTO documents (id, user_id, filename, uri, size, status, task_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                doc["id"], user_id, filename, uri, size, status, task_id, doc["created_at"],
            ),
        )
        return doc

    def list_documents(self, user_id: str) -> list[dict[str, Any]]:
        return self._all(
            "SELECT * FROM documents WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        )

    def get_document(self, doc_id: str, user_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM documents WHERE id = ? AND user_id = ?", (doc_id, user_id))

    def update_document_status(self, doc_id: str, status: str) -> None:
        self._exec("UPDATE documents SET status = ? WHERE id = ?", (status, doc_id))

    def delete_document(self, doc_id: str) -> None:
        self._exec("DELETE FROM documents WHERE id = ?", (doc_id,))
