import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiosqlite

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger("run_index")

GetStateFn = Callable[[str], Awaitable[dict[str, Any] | None]]

_INDEX_PATH = Path(settings.CODE_AGENT_DATA_DIR) / "code_agent_run_index.db"


@contextmanager
def _connect():
    _INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_INDEX_PATH, timeout=30.0)
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
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                user_request TEXT,
                project_path TEXT,
                ticket_id INTEGER,
                iteration INTEGER,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runs_project_updated "
            "ON runs(project_path, updated_at DESC)"
        )
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(runs)").fetchall()
        }
        if "ticket_id" not in cols:
            conn.execute("ALTER TABLE runs ADD COLUMN ticket_id INTEGER")
        # Execution lineage columns (retry-as-new-execution feature).
        if "root_run_id" not in cols:
            conn.execute("ALTER TABLE runs ADD COLUMN root_run_id TEXT")
        if "parent_run_id" not in cols:
            conn.execute("ALTER TABLE runs ADD COLUMN parent_run_id TEXT")
        if "attempt" not in cols:
            conn.execute("ALTER TABLE runs ADD COLUMN attempt INTEGER")
        # Backfill legacy rows: each is its own root at attempt 1.
        conn.execute(
            "UPDATE runs SET root_run_id = run_id "
            "WHERE root_run_id IS NULL OR root_run_id = ''"
        )
        conn.execute("UPDATE runs SET attempt = 1 WHERE attempt IS NULL")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runs_root_attempt "
            "ON runs(root_run_id, attempt)"
        )
        conn.commit()


def upsert_run(state: dict[str, Any]) -> None:
    run_id = state.get("run_id")
    if not run_id:
        return

    ensure_schema()
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        existing = conn.execute(
            "SELECT created_at FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        created_at = existing["created_at"] if existing else now
        conn.execute(
            """
            INSERT OR REPLACE INTO runs (
                run_id, status, user_request, project_path,
                ticket_id, iteration, error, created_at, updated_at,
                root_run_id, parent_run_id, attempt
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                state.get("status", "unknown"),
                state.get("user_request"),
                state.get("project_path"),
                state.get("ticket_id"),
                state.get("iteration"),
                state.get("error"),
                created_at,
                now,
                state.get("root_run_id") or run_id,
                state.get("parent_run_id"),
                int(state.get("attempt") or 1),
            ),
        )
        conn.commit()


_RUN_COLUMNS = (
    "run_id, status, user_request, project_path, ticket_id, iteration, "
    "error, created_at, updated_at, root_run_id, parent_run_id, attempt"
)


def list_runs(
    *,
    project_path: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """One row per logical task: the latest execution (highest attempt) of each
    root, annotated with execution_count. Retries no longer clutter the list as
    separate top-level entries — they are grouped under their root execution."""
    ensure_schema()
    where = "WHERE r.project_path = ?" if project_path else ""
    params: tuple[Any, ...] = (project_path, limit) if project_path else (limit,)
    qualified = ", ".join(f"r.{col.strip()}" for col in _RUN_COLUMNS.split(","))
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT {qualified},
                   (SELECT COUNT(*) FROM runs c
                    WHERE c.root_run_id = r.root_run_id) AS execution_count
            FROM runs r
            JOIN (
                SELECT root_run_id, MAX(attempt) AS max_attempt
                FROM runs
                GROUP BY root_run_id
            ) latest
              ON r.root_run_id = latest.root_run_id
             AND r.attempt = latest.max_attempt
            {where}
            ORDER BY r.updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def list_executions(run_id: str) -> list[dict[str, Any]]:
    """All executions (attempts) sharing a root, oldest first, each annotated
    with its own isolated token usage. `run_id` may be any attempt in the chain."""
    from app.services.run_activity import get_token_totals

    ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            "SELECT root_run_id FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        root = row["root_run_id"] if row and row["root_run_id"] else run_id
        rows = conn.execute(
            f"""
            SELECT {_RUN_COLUMNS}
            FROM runs
            WHERE root_run_id = ?
            ORDER BY attempt ASC
            """,
            (root,),
        ).fetchall()

    executions: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["token_usage"] = get_token_totals(item["run_id"])
        executions.append(item)
    return executions


def list_runs_by_ticket_id(
    ticket_id: int, *, exclude_run_id: str | None = None, limit: int = 5
) -> list[dict[str, Any]]:
    """Latest execution (highest attempt) per root run for a given ticket, newest
    first. Used by prior-work discovery to find earlier attempts on the same
    ticket regardless of which run_id/root_run_id the caller is currently on."""
    ensure_schema()
    qualified = ", ".join(f"r.{col.strip()}" for col in _RUN_COLUMNS.split(","))
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT {qualified}
            FROM runs r
            JOIN (
                SELECT root_run_id, MAX(attempt) AS max_attempt
                FROM runs WHERE ticket_id = ?
                GROUP BY root_run_id
            ) latest
              ON r.root_run_id = latest.root_run_id AND r.attempt = latest.max_attempt
            WHERE r.ticket_id = ?
            ORDER BY r.updated_at DESC
            LIMIT ?
            """,
            (ticket_id, ticket_id, limit),
        ).fetchall()
    results = [dict(row) for row in rows]
    if exclude_run_id:
        results = [
            r for r in results
            if r["run_id"] != exclude_run_id and r["root_run_id"] != exclude_run_id
        ]
    return results


async def _list_checkpoint_thread_ids() -> list[str]:
    """Return LangGraph thread IDs, or [] if the checkpoint DB is not initialized."""
    db_path = Path(settings.CODE_AGENT_CHECKPOINT_DB)
    if not db_path.exists() or db_path.stat().st_size == 0:
        return []

    async with aiosqlite.connect(settings.CODE_AGENT_CHECKPOINT_DB) as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='checkpoints'"
        )
        if not await cursor.fetchone():
            return []

        cursor = await conn.execute(
            "SELECT DISTINCT thread_id FROM checkpoints WHERE checkpoint_ns = ''"
        )
        rows = await cursor.fetchall()
        return [str(row[0]) for row in rows]


async def sync_missing_from_checkpoints(get_state: GetStateFn) -> int:
    """Backfill index rows for checkpoint threads that are not yet indexed."""
    ensure_schema()
    indexed = {row["run_id"] for row in list_runs(limit=10_000)}

    thread_ids = await _list_checkpoint_thread_ids()

    synced = 0
    for thread_id in thread_ids:
        if thread_id in indexed:
            continue
        state = await get_state(thread_id)
        if not state:
            continue
        upsert_run(state)
        synced += 1

    if synced:
        logger.info("Backfilled %d run(s) into run index", synced)
    return synced
