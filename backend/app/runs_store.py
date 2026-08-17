"""Run history: persists every streamed SSE frame so runs survive a restart
and can be replayed in the UI.

Lives in the same ``agent.db`` file as the LangGraph checkpoints (different
tables, different writer — plain sqlite3 short-lived connections, in the
style of ``mcp_server/db.py``) so the whole durability story is one file.
WAL mode lets the two writers (this module + AsyncSqliteSaver) coexist
without blocking each other on the tiny, infrequent writes involved here.
"""
from __future__ import annotations

import functools
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import get_settings


def _conn() -> sqlite3.Connection:
    # agent.db is shared with the LangGraph AsyncSqliteSaver (graph.py), which
    # holds its own long-lived connection to the same file. WAL lets readers
    # and the two writers coexist; busy_timeout makes a transient lock a
    # short retry instead of an immediate "database is locked" error. Kept
    # short (2s) deliberately — see `_retry_on_lock` for why.
    conn = sqlite3.connect(get_settings().agent_db_path, timeout=2)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=2000")
    return conn


def _retry_on_lock(fn):
    """Belt-and-suspenders on top of busy_timeout: in practice (observed on
    Windows) two writers on one SQLite file can still occasionally surface
    "database is locked" even with a busy_timeout, and the AsyncSqliteSaver's
    write transaction can stay open for as long as the tool call it's
    checkpointing around — seconds to over a minute for a slow web search.

    This function itself is always called via ``asyncio.to_thread`` (see
    ``api/main.py``), so blocking here costs a worker thread, not the event
    loop — that's what makes it safe to retry for a few seconds. Total worst
    case is bounded (~2s busy_timeout x 4 attempts + backoff) rather than
    unbounded, so one persistently slow run can't stall request handling.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        delay = 0.1
        for attempt in range(4):
            try:
                return fn(*args, **kwargs)
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower() or attempt == 3:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 1.0)

    return wrapper


def init_db() -> None:
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                task TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                created_at TEXT NOT NULL,
                elapsed_s REAL,
                final_preview TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS run_events (
                run_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                event TEXT NOT NULL,
                data TEXT NOT NULL,
                ts TEXT NOT NULL,
                PRIMARY KEY (run_id, seq)
            )
            """
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@_retry_on_lock
def create_run(run_id: str, task: str) -> None:
    init_db()
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO runs (id, task, status, created_at) VALUES (?, ?, 'running', ?)",
            (run_id, task, _now()),
        )


@_retry_on_lock
def set_status(run_id: str, status: str) -> None:
    init_db()
    with _conn() as conn:
        conn.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))


@_retry_on_lock
def finish_run(run_id: str, status: str, elapsed_s: float, final_preview: str = "") -> None:
    init_db()
    with _conn() as conn:
        conn.execute(
            "UPDATE runs SET status = ?, elapsed_s = ?, final_preview = ? WHERE id = ?",
            (status, elapsed_s, final_preview, run_id),
        )


@_retry_on_lock
def append_event(run_id: str, event: str, data: dict) -> None:
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 FROM run_events WHERE run_id = ?", (run_id,)
        ).fetchone()
        seq = row[0]
        conn.execute(
            "INSERT INTO run_events (run_id, seq, event, data, ts) VALUES (?, ?, ?, ?, ?)",
            (run_id, seq, event, json.dumps(data, ensure_ascii=False), _now()),
        )


def list_runs(limit: int = 50) -> list[dict]:
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_run(run_id: str) -> dict | None:
    init_db()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def get_run_events(run_id: str) -> list[dict]:
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT event, data, ts FROM run_events WHERE run_id = ? ORDER BY seq", (run_id,)
        ).fetchall()
    return [{"event": r["event"], "data": json.loads(r["data"]), "ts": r["ts"]} for r in rows]


def delete_run(run_id: str) -> None:
    init_db()
    with _conn() as conn:
        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        conn.execute("DELETE FROM run_events WHERE run_id = ?", (run_id,))
