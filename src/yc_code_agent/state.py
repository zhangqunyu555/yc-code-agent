"""Small SQLite session memory and durable FIFO goal queue."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class StateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                messages TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                created_at TEXT NOT NULL,
                finished_at TEXT
            );
            """
        )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def load_session(self, session_id: str) -> list[dict[str, Any]] | None:
        row = self.connection.execute("SELECT messages FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return json.loads(row["messages"]) if row else None

    def save_session(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        self.connection.execute(
            "INSERT INTO sessions(id, messages, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET messages=excluded.messages, updated_at=excluded.updated_at",
            (session_id, json.dumps(messages, ensure_ascii=False), self._now()),
        )
        self.connection.commit()

    def enqueue(self, text: str) -> int:
        if not text.strip():
            raise ValueError("goal text must not be empty")
        cursor = self.connection.execute(
            "INSERT INTO goals(text, status, created_at) VALUES (?, 'queued', ?)", (text, self._now())
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def list_goals(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT id, text, status, created_at, finished_at FROM goals ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def claim_next(self) -> dict[str, Any] | None:
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                "SELECT id, text FROM goals WHERE status = 'queued' ORDER BY id LIMIT 1"
            ).fetchone()
            if not row:
                return None
            self.connection.execute("UPDATE goals SET status = 'running' WHERE id = ?", (row["id"],))
            return dict(row)

    def finish(self, goal_id: int, *, success: bool) -> None:
        status = "done" if success else "failed"
        cursor = self.connection.execute(
            "UPDATE goals SET status = ?, finished_at = ? WHERE id = ? AND status = 'running'",
            (status, self._now(), goal_id),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"goal {goal_id} is not running")
        self.connection.commit()
