import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.services.project_skills import normalize_skill_ids

_DB_PATH = Path(settings.CODE_AGENT_DATA_DIR) / "code_agent_projects.db"

TICKET_TYPES = {"feature", "bug"}


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
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                ticket_type TEXT NOT NULL,
                status TEXT NOT NULL,
                run_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_projects_updated ON projects(updated_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tickets_project_updated ON tickets(project_id, updated_at DESC)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_run_id ON tickets(run_id) WHERE run_id IS NOT NULL"
        )
        _ensure_project_context_columns(conn)
        _ensure_jira_columns(conn)
        conn.commit()


def _ensure_project_context_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
    if "context_text" not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN context_text TEXT")
    if "planner_skill_ids" not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN planner_skill_ids TEXT")
    if "dev_skill_ids" not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN dev_skill_ids TEXT")


def _ensure_jira_columns(conn: sqlite3.Connection) -> None:
    ticket_cols = {row[1] for row in conn.execute("PRAGMA table_info(tickets)").fetchall()}
    if "source" not in ticket_cols:
        conn.execute("ALTER TABLE tickets ADD COLUMN source TEXT DEFAULT 'local'")
    if "jira_key" not in ticket_cols:
        conn.execute("ALTER TABLE tickets ADD COLUMN jira_key TEXT")
    if "jira_issue_type" not in ticket_cols:
        conn.execute("ALTER TABLE tickets ADD COLUMN jira_issue_type TEXT")
    if "jira_parent_key" not in ticket_cols:
        conn.execute("ALTER TABLE tickets ADD COLUMN jira_parent_key TEXT")
    project_cols = {row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
    if "jira_jql" not in project_cols:
        conn.execute("ALTER TABLE projects ADD COLUMN jira_jql TEXT")
    if "jira_status_mapping" not in project_cols:
        conn.execute("ALTER TABLE projects ADD COLUMN jira_status_mapping TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_jira_key ON tickets(project_id, jira_key) WHERE jira_key IS NOT NULL"
    )


def _row_to_project(row: sqlite3.Row) -> dict:
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "path": row["path"],
        "context_text": row["context_text"] if "context_text" in row.keys() else None,
        "planner_skill_ids": row["planner_skill_ids"] if "planner_skill_ids" in row.keys() else None,
        "dev_skill_ids": row["dev_skill_ids"] if "dev_skill_ids" in row.keys() else None,
        "jira_jql": row["jira_jql"] if "jira_jql" in row.keys() else None,
        "jira_status_mapping": row["jira_status_mapping"] if "jira_status_mapping" in row.keys() else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row_to_ticket(row: sqlite3.Row) -> dict:
    return {
        "id": int(row["id"]),
        "project_id": int(row["project_id"]),
        "title": row["title"],
        "description": row["description"],
        "ticket_type": row["ticket_type"],
        "status": row["status"],
        "run_id": row["run_id"],
        "source": row["source"] if "source" in row.keys() else "local",
        "jira_key": row["jira_key"] if "jira_key" in row.keys() else None,
        "jira_issue_type": row["jira_issue_type"] if "jira_issue_type" in row.keys() else None,
        "jira_parent_key": row["jira_parent_key"] if "jira_parent_key" in row.keys() else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def upsert_project(path: str, name: str | None = None) -> dict:
    ensure_schema()
    abs_path = os.path.abspath(path)
    project_name = (name or os.path.basename(abs_path.rstrip(os.sep)) or abs_path).strip()
    now = _now_iso()

    with _connect() as conn:
        existing = conn.execute("SELECT * FROM projects WHERE path = ?", (abs_path,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE projects SET name = ?, updated_at = ? WHERE id = ?",
                (project_name, now, existing["id"]),
            )
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (existing["id"],)).fetchone()
            conn.commit()
            return _row_to_project(row)

        cur = conn.execute(
            "INSERT INTO projects (name, path, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (project_name, abs_path, now, now),
        )
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (cur.lastrowid,)).fetchone()
        conn.commit()
        return _row_to_project(row)


def list_projects() -> list[dict]:
    ensure_schema()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
    return [_row_to_project(row) for row in rows]


def get_project(project_id: int) -> dict | None:
    ensure_schema()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            return None
        return _row_to_project(row)


def delete_project(project_id: int) -> dict | None:
    ensure_schema()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            return None
        deleted = _row_to_project(row)
        conn.execute("DELETE FROM tickets WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.commit()
    return deleted


def get_project_by_path(path: str) -> dict | None:
    ensure_schema()
    abs_path = os.path.abspath(path)
    with _connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE path = ?", (abs_path,)).fetchone()
    if not row:
        return None
    return _row_to_project(row)


def _encode_skill_ids(skill_ids: list[str] | None) -> str | None:
    normalized = normalize_skill_ids(skill_ids)
    return json.dumps(normalized) if normalized else None


def update_project_context(
    project_id: int,
    *,
    context_text: str | None = None,
    planner_skill_ids: list[str] | None = None,
    dev_skill_ids: list[str] | None = None,
) -> dict | None:
    ensure_schema()
    now = _now_iso()
    with _connect() as conn:
        existing = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not existing:
            return None
        conn.execute(
            """
            UPDATE projects
            SET context_text = ?, planner_skill_ids = ?, dev_skill_ids = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                (context_text or "").strip() or None,
                _encode_skill_ids(planner_skill_ids),
                _encode_skill_ids(dev_skill_ids),
                now,
                project_id,
            ),
        )
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        conn.commit()
    if not row:
        return None
    return _row_to_project(row)


def create_ticket(project_id: int, title: str, description: str | None, ticket_type: str) -> dict:
    ensure_schema()
    kind = ticket_type.strip().lower()
    if kind not in TICKET_TYPES:
        raise ValueError(f"Unsupported ticket_type: {ticket_type}")

    now = _now_iso()
    with _connect() as conn:
        project = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not project:
            raise KeyError("project_not_found")
        cur = conn.execute(
            """
            INSERT INTO tickets (project_id, title, description, ticket_type, status, run_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (project_id, title.strip(), description, kind, "pending", now, now),
        )
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (cur.lastrowid,)).fetchone()
        conn.commit()
        return _row_to_ticket(row)


def list_tickets(project_id: int) -> list[dict]:
    ensure_schema()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tickets WHERE project_id = ? ORDER BY id DESC",
            (project_id,),
        ).fetchall()
    return [_row_to_ticket(row) for row in rows]


def get_ticket(ticket_id: int) -> dict | None:
    ensure_schema()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if not row:
        return None
    return _row_to_ticket(row)


def set_ticket_run(ticket_id: int, run_id: str, status: str = "planning") -> dict | None:
    ensure_schema()
    now = _now_iso()
    with _connect() as conn:
        conn.execute(
            "UPDATE tickets SET run_id = ?, status = ?, updated_at = ? WHERE id = ?",
            (run_id, status, now, ticket_id),
        )
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        conn.commit()
    if not row:
        return None
    return _row_to_ticket(row)


def update_ticket_status_by_run(run_id: str, status: str) -> None:
    ensure_schema()
    now = _now_iso()
    with _connect() as conn:
        conn.execute(
            "UPDATE tickets SET status = ?, updated_at = ? WHERE run_id = ?",
            (status, now, run_id),
        )
        conn.commit()


def delete_ticket(ticket_id: int) -> dict | None:
    ensure_schema()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if not row:
            return None
        deleted = _row_to_ticket(row)
        conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
        conn.commit()
    return deleted


def upsert_jira_ticket(project_id: int, jira_key: str, title: str,
                       description: str | None, ticket_type: str,
                       jira_issue_type: str | None = None,
                       jira_parent_key: str | None = None) -> dict:
    """Insert or update a Jira-sourced ticket. Upserts by (project_id, jira_key)."""
    ensure_schema()
    kind = ticket_type.strip().lower()
    if kind not in TICKET_TYPES:
        kind = "feature"
    now = _now_iso()
    with _connect() as conn:
        existing = conn.execute(
            "SELECT * FROM tickets WHERE project_id = ? AND jira_key = ?",
            (project_id, jira_key),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE tickets SET title = ?, description = ?, ticket_type = ?, jira_issue_type = ?, jira_parent_key = ?, updated_at = ? WHERE id = ?",
                (title.strip(), description, kind, jira_issue_type, jira_parent_key, now, existing["id"]),
            )
            row = conn.execute("SELECT * FROM tickets WHERE id = ?", (existing["id"],)).fetchone()
            conn.commit()
            return _row_to_ticket(row)
        cur = conn.execute(
            "INSERT INTO tickets (project_id, title, description, ticket_type, status, run_id, source, jira_key, jira_issue_type, jira_parent_key, created_at, updated_at) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)",
            (project_id, title.strip(), description, kind, "pending", "jira", jira_key, jira_issue_type, jira_parent_key, now, now),
        )
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (cur.lastrowid,)).fetchone()
        conn.commit()
        return _row_to_ticket(row)


def get_ticket_by_jira_key(project_id: int, jira_key: str) -> dict | None:
    ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM tickets WHERE project_id = ? AND jira_key = ?",
            (project_id, jira_key),
        ).fetchone()
    if not row:
        return None
    return _row_to_ticket(row)


def get_ticket_by_run_id(run_id: str) -> dict | None:
    ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM tickets WHERE run_id = ?", (run_id,)
        ).fetchone()
    if not row:
        return None
    return _row_to_ticket(row)


def update_project_jira_config(project_id: int, *, jira_jql: str | None = None,
                                jira_status_mapping: dict | None = None) -> dict | None:
    ensure_schema()
    now = _now_iso()
    with _connect() as conn:
        existing = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not existing:
            return None
        mapping_json = json.dumps(jira_status_mapping) if jira_status_mapping is not None else None
        if jira_jql is not None and jira_status_mapping is not None:
            conn.execute(
                "UPDATE projects SET jira_jql = ?, jira_status_mapping = ?, updated_at = ? WHERE id = ?",
                (jira_jql, mapping_json, now, project_id),
            )
        elif jira_jql is not None:
            conn.execute(
                "UPDATE projects SET jira_jql = ?, updated_at = ? WHERE id = ?",
                (jira_jql, now, project_id),
            )
        elif jira_status_mapping is not None:
            conn.execute(
                "UPDATE projects SET jira_status_mapping = ?, updated_at = ? WHERE id = ?",
                (mapping_json, now, project_id),
            )
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        conn.commit()
    if not row:
        return None
    return _row_to_project(row)
