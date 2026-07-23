"""Discover prior implementation attempts for a ticket before planning.

Two independent sources are searched and merged:

1. **Run history** — retries reuse the same worktree/branch
   (`app/orchestration/runner.py::retry_run`), and a ticket can be re-run from
   scratch with a fresh ``agent/<run_id>`` branch, leaving the earlier one behind
   (``remove_worktree`` never deletes branches on normal completion). These are
   found via the run index, keyed by ticket id or a same-titled ticket.

2. **Branch / ref scan** — humans (or other tools) create branches like
   ``feature/MMA-3480-multi-container`` that the run index knows nothing about.
   These are found by scanning every branch whose name contains the ticket's
   Jira key — or its parent epic's key, so a child story still sees the epic
   branch — and diffing it against the repo's real default branch.

The merged diffs let the planner build on existing work instead of starting over.
"""

from __future__ import annotations

from typing import Any

from app.core.logging_config import get_logger
from app.services import project_ticket_store, run_index
from app.tools import git_tools

logger = get_logger("prior_work_discovery")


def _normalize_title(title: str) -> str:
    return " ".join((title or "").lower().split())


def _diff_branch(project_path: str, branch: str, ticket_id: int) -> dict[str, Any]:
    """Diff *branch* against the repo default branch; degrade to empty on error."""
    result = {"changed_files": [], "diff": "", "diff_truncated": False, "resolved_branch": branch}
    try:
        summary = git_tools.get_branch_diff_summary(project_path, branch)
        result["changed_files"] = summary["changed_files"]
        result["diff"] = summary["diff"]
        result["diff_truncated"] = summary["diff_truncated"]
        result["resolved_branch"] = summary.get("branch", branch)
    except Exception:  # noqa: BLE001 — diffing is best-effort
        logger.warning(
            "Failed to diff prior branch %s for ticket %s", branch, ticket_id, exc_info=True
        )
    return result


def _run_history_candidates(
    ticket: dict[str, Any],
    ticket_id: int,
    project_path: str,
    current_run_id: str | None,
    max_candidates: int,
) -> list[dict[str, Any]]:
    """Earlier ``agent/<root_run_id>`` branches from the run index."""
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
        for row in run_index.list_runs_by_ticket_id(
            cand_id, exclude_run_id=current_run_id, limit=max_candidates
        ):
            root = row["root_run_id"]
            existing = merged.get(root)
            if existing is None or row["updated_at"] > existing["updated_at"]:
                row = dict(row)
                row["match_reason"] = match_reason
                merged[root] = row

    ordered = sorted(merged.values(), key=lambda r: r["updated_at"], reverse=True)[:max_candidates]

    candidates: list[dict[str, Any]] = []
    for row in ordered:
        branch = f"agent/{row['root_run_id']}"
        exists = git_tools.branch_exists(project_path, branch)
        diff = _diff_branch(project_path, branch, ticket_id) if exists else None
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
                "changed_files": diff["changed_files"] if diff else [],
                "diff": diff["diff"] if diff else "",
                "diff_truncated": diff["diff_truncated"] if diff else False,
            }
        )
    return candidates


def _branch_scan_candidates(
    ticket: dict[str, Any],
    ticket_id: int,
    project_path: str,
    max_candidates: int,
) -> list[dict[str, Any]]:
    """Human/other-tool branches named after the ticket's Jira key or its epic."""
    own_key = ticket.get("jira_key")
    parent_key = ticket.get("jira_parent_key")
    tokens = [t for t in (own_key, parent_key) if t]
    if not tokens:
        return []

    own_low = (own_key or "").lower()
    candidates: list[dict[str, Any]] = []
    for branch in git_tools.list_branches_matching(project_path, tokens)[:max_candidates]:
        # A branch carrying the ticket's own key is a direct match; one that only
        # carries the parent epic's key is the epic branch, still worth surfacing.
        reason = "branch_name" if own_low and own_low in branch.lower() else "epic_branch"
        diff = _diff_branch(project_path, branch, ticket_id)
        candidates.append(
            {
                "run_id": None,
                "root_run_id": None,
                "attempt": None,
                "status": None,
                "updated_at": None,
                "match_reason": reason,
                "branch": diff["resolved_branch"],
                "branch_exists": True,
                "changed_files": diff["changed_files"],
                "diff": diff["diff"],
                "diff_truncated": diff["diff_truncated"],
            }
        )
    return candidates


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

        run_candidates = _run_history_candidates(
            ticket, ticket_id, project_path, current_run_id, max_candidates
        )
        branch_candidates = _branch_scan_candidates(
            ticket, ticket_id, project_path, max_candidates
        )

        # Merge, deduping by resolved branch name (run-history agent/* branches and
        # scanned branches are disjoint, but a scan can re-surface a branch already
        # captured — keep the first, which carries richer run metadata).
        seen: set[str] = set()
        candidates: list[dict[str, Any]] = []
        for cand in [*run_candidates, *branch_candidates]:
            if cand["branch"] in seen:
                continue
            seen.add(cand["branch"])
            candidates.append(cand)

        return {"found": bool(candidates), "candidates": candidates}
    except Exception as exc:  # noqa: BLE001 — discovery is best-effort
        logger.warning("Prior-work discovery failed for ticket %s", ticket_id, exc_info=True)
        return {"found": False, "candidates": [], "error": str(exc)}
