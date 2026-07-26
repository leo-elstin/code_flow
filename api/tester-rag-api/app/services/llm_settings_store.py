"""Persistence for LLM provider configuration.

The global config lives in a single-row ``llm_config`` table; per-project
overrides live in nullable ``llm_*`` columns on ``projects`` (owned by
:pymod:`app.services.project_ticket_store`). Both share
``data/code_agent_projects.db`` so a project and its override stay in one file.

Keys are stored as-is, the same way they sit in ``.env`` today — this is a
local-only dev tool and ``data/`` is gitignored. The API layer is responsible
for masking keys before they leave the process.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.services.project_ticket_store import ensure_schema as ensure_project_schema

_DB_PATH = Path(settings.CODE_AGENT_DATA_DIR) / "code_agent_projects.db"

PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI_GATEWAY = "openai_gateway"
PROVIDER_OPENAI = "openai"

PROVIDERS = {PROVIDER_ANTHROPIC, PROVIDER_OPENAI_GATEWAY, PROVIDER_OPENAI}

# Columns holding a per-project override, in the order they map onto LLMConfig.
_OVERRIDE_COLUMNS = (
    "llm_provider",
    "llm_api_key",
    "llm_base_url",
    "llm_chat_model",
    "llm_dev_model",
    "llm_reasoning_effort",
    "llm_reasoning_mode",
)


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
    # The project schema owns `projects` (including the llm_* override columns),
    # so run it first — a fresh DB has no `projects` table yet.
    ensure_project_schema()
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_config (
                id                INTEGER PRIMARY KEY CHECK (id = 1),
                provider          TEXT NOT NULL,
                api_key           TEXT,
                base_url          TEXT,
                chat_model        TEXT,
                dev_model         TEXT,
                reasoning_effort  TEXT,
                reasoning_mode    TEXT,
                updated_at        TEXT NOT NULL
            )
            """
        )
        _ensure_llm_config_reasoning_columns(conn)
        conn.commit()


def _ensure_llm_config_reasoning_columns(conn: sqlite3.Connection) -> None:
    """Added after llm_config first shipped — existing rows predate these columns."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(llm_config)").fetchall()}
    if "reasoning_effort" not in cols:
        conn.execute("ALTER TABLE llm_config ADD COLUMN reasoning_effort TEXT")
    if "reasoning_mode" not in cols:
        conn.execute("ALTER TABLE llm_config ADD COLUMN reasoning_mode TEXT")


def _row_to_config(row: sqlite3.Row) -> dict:
    keys = row.keys()
    return {
        "provider": row["provider"],
        "api_key": row["api_key"],
        "base_url": row["base_url"],
        "chat_model": row["chat_model"],
        "dev_model": row["dev_model"],
        "reasoning_effort": row["reasoning_effort"] if "reasoning_effort" in keys else None,
        "reasoning_mode": row["reasoning_mode"] if "reasoning_mode" in keys else None,
        "updated_at": row["updated_at"],
    }


def get_global_config() -> dict | None:
    """Return the saved global config, or None when nothing has been saved yet.

    None means "fall back to .env" — it is not an error.
    """
    ensure_schema()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM llm_config WHERE id = 1").fetchone()
    return _row_to_config(row) if row else None


def save_global_config(
    *,
    provider: str,
    api_key: str | None = None,
    base_url: str | None = None,
    chat_model: str | None = None,
    dev_model: str | None = None,
    reasoning_effort: str | None = None,
    reasoning_mode: str | None = None,
) -> dict:
    """Upsert the single global config row.

    ``api_key=None`` preserves any already-stored key, so the masked value the
    UI round-trips can never wipe a real key. Pass an empty string to clear it.
    """
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider!r}. Expected one of {sorted(PROVIDERS)}.")

    ensure_schema()
    now = _now_iso()
    with _connect() as conn:
        existing = conn.execute("SELECT * FROM llm_config WHERE id = 1").fetchone()
        resolved_key = existing["api_key"] if (api_key is None and existing) else api_key

        conn.execute(
            """
            INSERT INTO llm_config
                (id, provider, api_key, base_url, chat_model, dev_model,
                 reasoning_effort, reasoning_mode, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                provider         = excluded.provider,
                api_key          = excluded.api_key,
                base_url         = excluded.base_url,
                chat_model       = excluded.chat_model,
                dev_model        = excluded.dev_model,
                reasoning_effort = excluded.reasoning_effort,
                reasoning_mode   = excluded.reasoning_mode,
                updated_at       = excluded.updated_at
            """,
            (provider, resolved_key, base_url, chat_model, dev_model,
             reasoning_effort, reasoning_mode, now),
        )
        row = conn.execute("SELECT * FROM llm_config WHERE id = 1").fetchone()
        conn.commit()
    return _row_to_config(row)


def get_project_override(project_id: int) -> dict | None:
    """Return a project's LLM override, or None when it has none.

    An override exists only when the project has a provider set; the remaining
    fields are optional and fall through to the global config individually.
    """
    ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {', '.join(_OVERRIDE_COLUMNS)} FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()

    if not row or not row["llm_provider"]:
        return None
    return {
        "provider": row["llm_provider"],
        "api_key": row["llm_api_key"],
        "base_url": row["llm_base_url"],
        "chat_model": row["llm_chat_model"],
        "dev_model": row["llm_dev_model"],
        "reasoning_effort": row["llm_reasoning_effort"],
        "reasoning_mode": row["llm_reasoning_mode"],
    }


def save_project_override(
    project_id: int,
    *,
    provider: str,
    api_key: str | None = None,
    base_url: str | None = None,
    chat_model: str | None = None,
    dev_model: str | None = None,
    reasoning_effort: str | None = None,
    reasoning_mode: str | None = None,
) -> dict | None:
    """Set a project's override. ``api_key=None`` preserves the stored key."""
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider!r}. Expected one of {sorted(PROVIDERS)}.")

    ensure_schema()
    with _connect() as conn:
        existing = conn.execute(
            "SELECT llm_api_key FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if not existing:
            return None
        resolved_key = existing["llm_api_key"] if api_key is None else api_key

        conn.execute(
            """
            UPDATE projects
               SET llm_provider = ?, llm_api_key = ?, llm_base_url = ?,
                   llm_chat_model = ?, llm_dev_model = ?,
                   llm_reasoning_effort = ?, llm_reasoning_mode = ?, updated_at = ?
             WHERE id = ?
            """,
            (provider, resolved_key, base_url, chat_model, dev_model,
             reasoning_effort, reasoning_mode, _now_iso(), project_id),
        )
        conn.commit()
    return get_project_override(project_id)


def clear_project_override(project_id: int) -> None:
    """Drop a project's override so it falls back to the global config."""
    ensure_schema()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE projects
               SET llm_provider = NULL, llm_api_key = NULL, llm_base_url = NULL,
                   llm_chat_model = NULL, llm_dev_model = NULL,
                   llm_reasoning_effort = NULL, llm_reasoning_mode = NULL, updated_at = ?
             WHERE id = ?
            """,
            (_now_iso(), project_id),
        )
        conn.commit()
