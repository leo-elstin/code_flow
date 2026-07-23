"""Discover prior implementation attempts for a ticket before planning.

Retries reuse the same worktree/branch (`app/orchestration/runner.py::retry_run`),
and a ticket can also be re-run from scratch with a fresh `run_id`/branch, leaving
the earlier `agent/<run_id>` branch behind (`remove_worktree` never deletes
branches on normal completion). This module finds those earlier branches — by
ticket id, or by a matching ticket title as a fallback — and summarizes their
diffs so the planner can build on existing work instead of starting over.
"""

from __future__ import annotations

from typing import Any

from app.core.logging_config import get_logger
from app.services import project_ticket_store, run_index
from app.tools import git_tools

logger = get_logger("prior_work_discovery")


def _normalize_title(title: str) -> str:
    return " ".join((title or "").lower().split())


def discover_prior_work(
    ticket_id: int | None,
    project_path: str,
    *,
    current_run_id: str | None = None,
    max_candidates: int = 3,
) -> dict[str, Any]:
    """Best-effort lookup of prior runs/branches for *ticket_id*. Never raises —
    any failure degrades to an empty result so discovery can't break planning."""
    empty: dict[str, Any] = {"found": False, "candidates": []}
    if ticket_id is None:
        return empty

    try:
        ticket = project_ticket_store.get_ticket(ticket_id)
        if ticket is None:
            return empty

        candidate_ticket_ids: dict[int, str] = {ticket_id: "ticket_id"}

        title = _normalize_title(ticket.get("title", ""))
        if title:
            for other in project_ticket_store.list_tickets(ticket["project_id"]):
                other_id = other.get("id")
                if other_id is None or other_id == ticket_id:
                    continue
                if _normalize_title(other.get("title", "")) == title:
                    candidate_ticket_ids.setdefault(other_id, "title")

        merged: dict[str, dict[str, Any]] = {}
        for cand_id, match_reason in candidate_ticket_ids.items():
            rows = run_index.list_runs_by_ticket_id(
                cand_id, exclude_run_id=current_run_id, limit=max_candidates
            )
            for row in rows:
                root = row["root_run_id"]
                existing = merged.get(root)
                if existing is None or row["updated_at"] > existing["updated_at"]:
                    row = dict(row)
                    row["match_reason"] = match_reason
                    merged[root] = row

        if not merged:
            return empty

        ordered = sorted(merged.values(), key=lambda r: r["updated_at"], reverse=True)[
            :max_candidates
        ]

        candidates: list[dict[str, Any]] = []
        for row in ordered:
            branch = f"agent/{row['root_run_id']}"
            exists = git_tools.branch_exists(project_path, branch)
            changed_files: list[str] = []
            diff = ""
            diff_truncated = False
            if exists:
                try:
                    summary = git_tools.get_branch_diff_summary(project_path, branch)
                    changed_files = summary["changed_files"]
                    diff = summary["diff"]
                    diff_truncated = summary["diff_truncated"]
                except Exception:  # noqa: BLE001 — diffing is best-effort
                    logger.warning(
                        "Failed to diff prior branch %s for ticket %s",
                        branch, ticket_id, exc_info=True,
                    )
            candidates.append(
                {
                    "run_id": row["run_id"],
                    "root_run_id": row["root_run_id"],
                    "attempt": row.get("attempt"),
                    "status": row.get("status"),
                    "updated_at": row.get("updated_at"),
                    "match_reason": row["match_reason"],
                    "branch": branch,
                    "branch_exists": exists,
                    "changed_files": changed_files,
                    "diff": diff,
                    "diff_truncated": diff_truncated,
                }
            )

        return {"found": bool(candidates), "candidates": candidates}
    except Exception as exc:  # noqa: BLE001 — discovery is best-effort
        logger.warning("Prior-work discovery failed for ticket %s", ticket_id, exc_info=True)
        return {"found": False, "candidates": [], "error": str(exc)}
