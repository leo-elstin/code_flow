import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger("run_activity")

ActivityType = Literal["status", "grep", "thinking", "tool", "llm", "token"]
ActivityPhase = Literal["planner", "dev", "verifier", "qa", "system"]

_DB_PATH = Path(settings.CODE_AGENT_DATA_DIR) / "code_agent_activity.db"

# Activity retention: keep this many recent non-token events per run, pruning
# every _PRUNE_INTERVAL inserts. Token events are never pruned so cumulative
# token totals stay accurate.
_MAX_EVENTS_PER_RUN = 1000
_PRUNE_INTERVAL = 100


@contextmanager
def _connect():
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def ensure_schema() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS activity_events (
                run_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                type TEXT NOT NULL,
                phase TEXT NOT NULL,
                title TEXT NOT NULL,
                detail TEXT,
                files_json TEXT,
                meta_json TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (run_id, seq)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_activity_run_seq "
            "ON activity_events(run_id, seq)"
        )


def _next_seq(conn: sqlite3.Connection, run_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM activity_events WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return int(row["next_seq"])


def append_activity(
    run_id: str,
    *,
    type: ActivityType,
    phase: ActivityPhase,
    title: str,
    detail: str | None = None,
    files: list[str] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not run_id:
        return {}

    ensure_schema()
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        seq = _next_seq(conn, run_id)
        conn.execute(
            """
            INSERT INTO activity_events (
                run_id, seq, type, phase, title, detail, files_json, meta_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                seq,
                type,
                phase,
                title,
                detail,
                json.dumps(files or []),
                json.dumps(meta or {}),
                now,
            ),
        )
        # Bound unbounded growth: periodically drop old non-token events.
        if seq % _PRUNE_INTERVAL == 0:
            conn.execute(
                "DELETE FROM activity_events "
                "WHERE run_id = ? AND seq <= ? AND type != 'token'",
                (run_id, seq - _MAX_EVENTS_PER_RUN),
            )

    event = {
        "seq": seq,
        "type": type,
        "phase": phase,
        "title": title,
        "detail": detail,
        "files": files or [],
        "meta": meta or {},
        "created_at": now,
    }
    return event


def list_activity(
    run_id: str,
    *,
    after_seq: int = 0,
    limit: int = 200,
) -> list[dict[str, Any]]:
    ensure_schema()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT seq, type, phase, title, detail, files_json, meta_json, created_at
            FROM activity_events
            WHERE run_id = ? AND seq > ?
            ORDER BY seq ASC
            LIMIT ?
            """,
            (run_id, after_seq, limit),
        ).fetchall()

    return [_row_to_event(row) for row in rows]


def get_current_action(run_id: str) -> dict[str, Any] | None:
    ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT seq, type, phase, title, detail, files_json, meta_json, created_at
            FROM activity_events
            WHERE run_id = ? AND type != 'token'
            ORDER BY seq DESC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
    return _row_to_event(row) if row else None


def get_token_totals(run_id: str) -> dict[str, int]:
    ensure_schema()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT meta_json FROM activity_events
            WHERE run_id = ? AND type = 'token'
            """,
            (run_id,),
        ).fetchall()

    prompt = 0
    completion = 0
    total = 0
    for row in rows:
        meta = json.loads(row["meta_json"] or "{}")
        prompt += int(meta.get("prompt_tokens") or 0)
        completion += int(meta.get("completion_tokens") or 0)
        total += int(meta.get("total_tokens") or 0)

    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }


def activity_snapshot(run_id: str, *, after_seq: int = 0) -> dict[str, Any]:
    events = list_activity(run_id, after_seq=after_seq)
    return {
        "events": events,
        "current_action": get_current_action(run_id),
        "token_usage": get_token_totals(run_id),
    }


def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "seq": row["seq"],
        "type": row["type"],
        "phase": row["phase"],
        "title": row["title"],
        "detail": row["detail"],
        "files": json.loads(row["files_json"] or "[]"),
        "meta": json.loads(row["meta_json"] or "{}"),
        "created_at": row["created_at"],
    }
