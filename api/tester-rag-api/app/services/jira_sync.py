"""Jira ↔ local ticket store synchronisation and status-update hooks.

:func:`sync_jira_tickets` pulls issues from Jira Cloud into the local
``project_ticket_store``.  :func:`update_jira_status_for_run` pushes agent
pipeline status changes back to Jira as workflow transitions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.core.config import settings
from app.core.logging_config import get_logger
from app.services.jira_service import JiraService, JiraTicket
from app.services import project_ticket_store

logger = get_logger("jira_sync")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SyncResult:
    """Summary returned by :func:`sync_jira_tickets`."""

    created: int = 0
    updated: int = 0
    total: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Issue-type mapping
# ---------------------------------------------------------------------------

_ISSUE_TYPE_MAP: dict[str, str] = {
    "story": "feature",
    "task": "feature",
    "sub-task": "feature",
    "epic": "feature",
    "bug": "bug",
}


def _map_issue_type(jira_type: str) -> str:
    """Map a Jira issue type name to an internal ticket type."""
    return _ISSUE_TYPE_MAP.get(jira_type.lower(), "feature")


# ---------------------------------------------------------------------------
# Sync tickets from Jira → local store
# ---------------------------------------------------------------------------

def sync_jira_tickets(project_id: int) -> SyncResult:
    """Fetch tickets from Jira Cloud and upsert them into the local store.

    The project's ``jira_jql`` column provides the JQL query.  Image
    attachments are downloaded to ``JIRA_ATTACHMENTS_DIR/<ticket_key>/``.

    Raises:
        ValueError: If the project has no ``jira_jql`` configured.
        KeyError: If the project does not exist.
    """
    project = project_ticket_store.get_project(project_id)
    if project is None:
        raise KeyError(f"Project {project_id} not found")

    jira_jql: str | None = project.get("jira_jql")
    if not jira_jql:
        raise ValueError(
            f"Project {project_id} has no Jira JQL configured. "
            "Set jira_jql on the project before syncing."
        )

    svc = JiraService()
    tickets = svc.search_tickets(jira_jql)

    result = SyncResult(total=len(tickets))

    for ticket in tickets:
        try:
            _sync_single_ticket(svc, project_id, ticket, result)
        except Exception as exc:
            msg = f"Error syncing {ticket.key}: {exc}"
            logger.warning(msg, exc_info=True)
            result.errors.append(msg)

    logger.info(
        "Jira sync for project %d complete — created=%d updated=%d errors=%d",
        project_id,
        result.created,
        result.updated,
        len(result.errors),
    )
    return result


def _sync_single_ticket(
    svc: JiraService,
    project_id: int,
    ticket: JiraTicket,
    result: SyncResult,
) -> None:
    """Process one Jira ticket: download attachments and upsert into store."""
    ticket_type = _map_issue_type(ticket.issue_type)

    # -- download image attachments (best-effort) --
    attachment_paths: list[str] = []
    attachments_meta: list[dict] = []

    for att in ticket.attachments:
        try:
            path = svc.download_attachment(
                att["id"], att["filename"], ticket.key
            )
            attachment_paths.append(str(path))
            attachments_meta.append({**att, "local_path": str(path)})
        except Exception as exc:
            msg = f"Failed to download attachment {att.get('filename')} for {ticket.key}: {exc}"
            logger.warning(msg)
            result.errors.append(msg)

    # -- build linked-issues context string --
    linked_lines: list[str] = []
    for li in ticket.linked_issues:
        linked_lines.append(
            f"Related: {li['key']} ({li['status']}) - {li['summary']}"
        )
    linked_context = "\n".join(linked_lines)

    # -- structured description payload --
    structured_desc = {
        "raw_description": ticket.description,
        "acceptance_criteria": ticket.acceptance_criteria,
        "linked_issues_context": linked_context,
        "attachment_paths": attachment_paths,
        "attachments_meta": attachments_meta,
    }

    # -- upsert into local store --
    row = project_ticket_store.upsert_jira_ticket(
        project_id=project_id,
        jira_key=ticket.key,
        title=ticket.summary,
        description=json.dumps(structured_desc),
        ticket_type=ticket_type,
        jira_issue_type=ticket.issue_type,
        jira_parent_key=ticket.parent_key,
        jira_status=ticket.status,
        jira_priority=ticket.priority,
    )

    # upsert_jira_ticket returns a dict with an "action" hint when available.
    action = row.get("action", "upserted") if isinstance(row, dict) else "upserted"
    if action == "created":
        result.created += 1
    else:
        result.updated += 1


# ---------------------------------------------------------------------------
# Push agent status → Jira workflow transition
# ---------------------------------------------------------------------------

def update_jira_status_for_run(run_id: str, agent_status: str) -> None:
    """Transition the Jira ticket associated with *run_id* based on the
    agent pipeline's current status.

    This is a **fire-and-forget** hook — it logs warnings on failure but
    never raises so the calling pipeline is not disrupted.

    No-op unless ``JIRA_WRITE_ENABLED=true`` in settings.
    """
    if not settings.JIRA_WRITE_ENABLED:
        logger.debug(
            "Jira write-back disabled (JIRA_WRITE_ENABLED=false); skipping transition for run %s",
            run_id,
        )
        return
    try:
        _do_update_jira_status(run_id, agent_status)
    except Exception:
        logger.warning(
            "Failed to update Jira status for run %s (agent_status=%s)",
            run_id,
            agent_status,
            exc_info=True,
        )


def _do_update_jira_status(run_id: str, agent_status: str) -> None:
    """Inner implementation — may raise on any failure."""
    ticket = project_ticket_store.get_ticket_by_run_id(run_id)
    if ticket is None:
        logger.debug("No ticket found for run_id=%s, skipping Jira update", run_id)
        return

    source = ticket.get("source", "")
    jira_key = ticket.get("jira_key")
    if source != "jira" or not jira_key:
        return

    project = project_ticket_store.get_project(ticket["project_id"])
    if project is None:
        logger.warning("Project %d not found for ticket %s", ticket["project_id"], jira_key)
        return

    raw_mapping = project.get("jira_status_mapping")
    if not raw_mapping:
        logger.debug("No jira_status_mapping on project %d, skipping transition", project["id"])
        return

    try:
        mapping: dict = json.loads(raw_mapping) if isinstance(raw_mapping, str) else raw_mapping
    except (json.JSONDecodeError, TypeError):
        logger.warning("Invalid jira_status_mapping JSON on project %d", project["id"])
        return

    target_status = mapping.get(agent_status)
    if not target_status:
        logger.debug(
            "No Jira transition mapped for agent_status='%s' in project %d",
            agent_status,
            project["id"],
        )
        return

    svc = JiraService()
    ok = svc.transition_ticket(jira_key, target_status)
    if ok:
        logger.info(
            "Jira %s transitioned to '%s' (agent_status=%s)",
            jira_key,
            target_status,
            agent_status,
        )
    else:
        logger.warning(
            "Jira transition '%s' failed for %s (agent_status=%s)",
            target_status,
            jira_key,
            agent_status,
        )
