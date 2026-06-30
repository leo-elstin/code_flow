"""SQLite persistence for epic-level execution runs.

An *epic run* orchestrates the execution of every child story under a Jira
Epic. It owns the dependency plan (DAG levels), the map of
``story_ticket_id -> child run_id``, and the shared git integration branch.
Each child story still executes through the normal per-ticket
:class:`~app.orchestration.runner.CodeAgentRunner`; this store only tracks the
epic-level coordination state.

Mirrors :mod:`app.services.po_session_store` (same ``_connect``/WAL/
``ensure_schema`` conventions).
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger("epic_run_store")

_DB_PATH = Path(settings.CODE_AGENT_DATA_DIR) / "epic_runs.db"


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS epic_runs (
                id TEXT PRIMARY KEY,
                project_id INTEGER,
                project_path TEXT NOT NULL DEFAULT '',
                epic_ticket_id INTEGER NOT NULL,
                epic_jira_key TEXT,
                status TEXT NOT NULL DEFAULT 'planning',
                workspace_mode TEXT NOT NULL DEFAULT 'worktree',
                integration_branch TEXT,
                plan_json TEXT,
                child_runs_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_epic_runs_project_id ON epic_runs(project_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_epic_runs_epic_ticket ON epic_runs(epic_ticket_id)"
        )


def _row_to_epic_run(row: sqlite3.Row) -> dict:
    return {
        "epic_run_id": row["id"],
        "project_id": row["project_id"],
        "project_path": row["project_path"],
        "epic_ticket_id": int(row["epic_ticket_id"]),
        "epic_jira_key": row["epic_jira_key"],
        "status": row["status"],
        "workspace_mode": row["workspace_mode"],
        "integration_branch": row["integration_branch"],
        # plan_json: {"levels": [[ticket_id, ...], ...], "edges": {ticket_id: [dep_ids]}, ...}
        "plan": json.loads(row["plan_json"] or "null"),
        # child_runs_json: {str(ticket_id): {"run_id": ..., "status": ..., "jira_key": ...}}
        "child_runs": json.loads(row["child_runs_json"] or "{}"),
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_epic_run(
    epic_run_id: str,
    *,
    epic_ticket_id: int,
    project_id: int | None,
    project_path: str = "",
    epic_jira_key: str | None = None,
    workspace_mode: str = "worktree",
) -> dict:
    ensure_schema()
    now = _now_iso()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO epic_runs
              (id, project_id, project_path, epic_ticket_id, epic_jira_key, status,
               workspace_mode, child_runs_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'planning', ?, '{}', ?, ?)
            """,
            (
                epic_run_id,
                project_id,
                project_path,
                epic_ticket_id,
                epic_jira_key,
                workspace_mode,
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM epic_runs WHERE id = ?", (epic_run_id,)).fetchone()
    return _row_to_epic_run(row)


def get_epic_run(epic_run_id: str) -> dict | None:
    ensure_schema()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM epic_runs WHERE id = ?", (epic_run_id,)).fetchone()
    if not row:
        return None
    return _row_to_epic_run(row)


_COLUMN_MAP = {
    "status": "status",
    "workspace_mode": "workspace_mode",
    "integration_branch": "integration_branch",
    "plan": "plan_json",
    "child_runs": "child_runs_json",
    "error": "error",
}


def update_epic_run(epic_run_id: str, **kwargs) -> dict | None:
    """Patch fields on an epic run. JSON fields accept dicts/lists or strings."""
    ensure_schema()
    now = _now_iso()
    set_parts: list[str] = []
    values: list = []
    for key, val in kwargs.items():
        col = _COLUMN_MAP.get(key)
        if col is None:
            logger.warning("update_epic_run: unknown field %s, skipping", key)
            continue
        if col.endswith("_json") and not isinstance(val, str):
            values.append(json.dumps(val))
        else:
            values.append(val)
        set_parts.append(f"{col} = ?")

    if not set_parts:
        return get_epic_run(epic_run_id)

    set_parts.append("updated_at = ?")
    values.append(now)
    values.append(epic_run_id)
    with _connect() as conn:
        conn.execute(
            f"UPDATE epic_runs SET {', '.join(set_parts)} WHERE id = ?",
            values,
        )
        row = conn.execute("SELECT * FROM epic_runs WHERE id = ?", (epic_run_id,)).fetchone()
    if not row:
        return None
    return _row_to_epic_run(row)


def set_child_run(
    epic_run_id: str,
    ticket_id: int,
    *,
    run_id: str | None = None,
    status: str | None = None,
    jira_key: str | None = None,
) -> dict | None:
    """Merge a single child entry into ``child_runs`` (keyed by str(ticket_id))."""
    current = get_epic_run(epic_run_id)
    if current is None:
        return None
    children = dict(current.get("child_runs") or {})
    key = str(ticket_id)
    entry = dict(children.get(key) or {})
    if run_id is not None:
        entry["run_id"] = run_id
    if status is not None:
        entry["status"] = status
    if jira_key is not None:
        entry["jira_key"] = jira_key
    entry["ticket_id"] = ticket_id
    children[key] = entry
    return update_epic_run(epic_run_id, child_runs=children)


def list_epic_runs(project_id: int | None = None, limit: int = 50) -> list[dict]:
    ensure_schema()
    with _connect() as conn:
        if project_id is not None:
            rows = conn.execute(
                "SELECT * FROM epic_runs WHERE project_id = ? ORDER BY updated_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM epic_runs ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [_row_to_epic_run(row) for row in rows]
