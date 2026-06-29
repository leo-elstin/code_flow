"""SQLite persistence for PO Agent sessions."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger("po_session_store")

_DB_PATH = Path(settings.CODE_AGENT_DATA_DIR) / "po_sessions.db"


@contextmanager
def _connect():
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS po_sessions (
                id TEXT PRIMARY KEY,
                project_id INTEGER,
                mode TEXT NOT NULL DEFAULT 'brainstorm',
                status TEXT NOT NULL DEFAULT 'classifying',
                initial_context TEXT NOT NULL,
                project_path TEXT NOT NULL DEFAULT '',
                requirements_model_json TEXT,
                brief_json TEXT,
                draft_stories_json TEXT,
                pending_questions_json TEXT,
                research_depth TEXT NOT NULL DEFAULT 'codebase',
                questions_asked INTEGER DEFAULT 0,
                readiness_score REAL DEFAULT 0.0,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS po_session_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES po_sessions(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_po_sessions_project_id "
            "ON po_sessions(project_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_po_session_messages_session_id "
            "ON po_session_messages(session_id)"
        )


def _row_to_session(row: sqlite3.Row) -> dict:
    keys = row.keys()
    return {
        "session_id": row["id"],
        "project_id": row["project_id"],
        "mode": row["mode"],
        "status": row["status"],
        "initial_context": row["initial_context"],
        "project_path": row["project_path"] if "project_path" in keys else "",
        "requirements_model": json.loads(row["requirements_model_json"] or "null"),
        "brief": json.loads(row["brief_json"] or "null"),
        "draft_stories": json.loads(row["draft_stories_json"] or "null"),
        "pending_questions": json.loads(row["pending_questions_json"] or "[]"),
        "research_depth": row["research_depth"],
        "questions_asked": int(row["questions_asked"] or 0),
        "readiness_score": float(row["readiness_score"] or 0.0),
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row_to_message(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "role": row["role"],
        "content": row["content"],
        "metadata": json.loads(row["metadata_json"] or "{}"),
        "created_at": row["created_at"],
    }


def create_session(
    session_id: str,
    project_id: int | None,
    initial_context: str,
    research_depth: str = "codebase",
    project_path: str = "",
) -> dict:
    ensure_schema()
    now = _now_iso()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO po_sessions
              (id, project_id, mode, status, initial_context, project_path, research_depth,
               questions_asked, readiness_score, created_at, updated_at)
            VALUES (?, ?, 'brainstorm', 'classifying', ?, ?, ?, 0, 0.0, ?, ?)
            """,
            (session_id, project_id, initial_context, project_path, research_depth, now, now),
        )
        row = conn.execute("SELECT * FROM po_sessions WHERE id = ?", (session_id,)).fetchone()
    return _row_to_session(row)


def get_session(session_id: str) -> dict | None:
    ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM po_sessions WHERE id = ?", (session_id,)
        ).fetchone()
    if not row:
        return None
    return _row_to_session(row)


def update_session(session_id: str, **kwargs) -> dict | None:
    ensure_schema()
    now = _now_iso()

    # Map Python kwarg names to column names
    column_map = {
        "mode": "mode",
        "status": "status",
        "requirements_model": "requirements_model_json",
        "brief": "brief_json",
        "draft_stories": "draft_stories_json",
        "pending_questions": "pending_questions_json",
        "research_depth": "research_depth",
        "questions_asked": "questions_asked",
        "readiness_score": "readiness_score",
        "error": "error",
    }

    set_parts = []
    values = []

    for key, val in kwargs.items():
        col = column_map.get(key)
        if col is None:
            logger.warning("update_session: unknown field %s, skipping", key)
            continue
        if col.endswith("_json"):
            # Serialize to JSON; accept pre-serialized strings too
            if isinstance(val, str):
                values.append(val)
            else:
                values.append(json.dumps(val))
        else:
            values.append(val)
        set_parts.append(f"{col} = ?")

    if not set_parts:
        return get_session(session_id)

    set_parts.append("updated_at = ?")
    values.append(now)
    values.append(session_id)

    with _connect() as conn:
        conn.execute(
            f"UPDATE po_sessions SET {', '.join(set_parts)} WHERE id = ?",
            values,
        )
        row = conn.execute("SELECT * FROM po_sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        return None
    return _row_to_session(row)


def append_message(
    session_id: str,
    role: str,
    content: str,
    metadata: dict | None = None,
) -> dict:
    ensure_schema()
    now = _now_iso()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO po_session_messages (session_id, role, content, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, role, content, json.dumps(metadata or {}), now),
        )
        row = conn.execute(
            "SELECT * FROM po_session_messages WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    return _row_to_message(row)


def get_messages(session_id: str) -> list[dict]:
    ensure_schema()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM po_session_messages WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
    return [_row_to_message(row) for row in rows]


def list_sessions(project_id: int | None = None, limit: int = 50) -> list[dict]:
    ensure_schema()
    with _connect() as conn:
        if project_id is not None:
            rows = conn.execute(
                "SELECT * FROM po_sessions WHERE project_id = ? ORDER BY updated_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM po_sessions ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [_row_to_session(row) for row in rows]
