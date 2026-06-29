"""Jira Cloud REST API v3 wrapper using the official ``jira`` Python SDK.

All Jira interactions flow through :class:`JiraService` so the rest of the
codebase never depends on transport details.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from jira import JIRA

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger("jira_service")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class JiraTicket:
    """Normalised representation of a Jira issue."""

    key: str
    summary: str
    description: str
    status: str
    issue_type: str
    priority: str | None
    parent_key: str | None = None
    acceptance_criteria: list[str] = field(default_factory=list)
    linked_issues: list[dict] = field(default_factory=list)
    attachments: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class JiraService:
    """Thin wrapper around the ``jira`` SDK.

    Requires ``JIRA_BASE_URL``, ``JIRA_USER_EMAIL``, and ``JIRA_API_TOKEN``
    to be set in the environment (via :pymod:`app.core.config`).
    """

    def __init__(self) -> None:
        if not self.is_configured():
            raise ValueError(
                "Jira integration is not configured. "
                "Set JIRA_BASE_URL, JIRA_USER_EMAIL, and JIRA_API_TOKEN."
            )
        self._client = JIRA(
            server=settings.JIRA_BASE_URL,
            basic_auth=(settings.JIRA_USER_EMAIL, settings.JIRA_API_TOKEN),
        )
        logger.info("Jira client initialised for %s", settings.JIRA_BASE_URL)

    # -- configuration check -------------------------------------------------

    @staticmethod
    def is_configured() -> bool:
        """Return ``True`` when all required Jira env vars are present."""
        return bool(
            settings.JIRA_BASE_URL
            and settings.JIRA_USER_EMAIL
            and settings.JIRA_API_TOKEN
        )

    # -- search / fetch -------------------------------------------------------

    def search_tickets(self, jql: str, max_results: int = 50) -> list[JiraTicket]:
        """Run a JQL query and return parsed :class:`JiraTicket` objects."""
        logger.info("Searching Jira: %s (max %d)", jql, max_results)
        issues = self._client.search_issues(
            jql,
            maxResults=max_results,
            fields="summary,description,status,issuetype,priority,issuelinks,attachment,parent",
        )
        tickets = [self._parse_issue(issue) for issue in issues]
        logger.info("Jira search returned %d tickets", len(tickets))
        return tickets

    def get_ticket(self, issue_key: str) -> JiraTicket:
        """Fetch a single Jira issue by its key (e.g. ``PROJ-42``)."""
        logger.info("Fetching Jira issue %s", issue_key)
        issue = self._client.issue(issue_key)
        return self._parse_issue(issue)

    # -- transitions ----------------------------------------------------------

    def get_transitions(self, issue_key: str) -> list[dict]:
        """Return available workflow transitions for *issue_key*.

        Each dict contains ``id``, ``name``, and ``to_status``.
        """
        raw = self._client.transitions(issue_key)
        return [
            {
                "id": t["id"],
                "name": t["name"],
                "to_status": t.get("to", {}).get("name", ""),
            }
            for t in raw
        ]

    def transition_ticket(self, issue_key: str, transition_name: str) -> bool:
        """Execute the transition named *transition_name* on *issue_key*.

        Returns ``True`` on success, ``False`` if the transition is not found.
        """
        transitions = self.get_transitions(issue_key)
        match = next(
            (t for t in transitions if t["name"].lower() == transition_name.lower()),
            None,
        )
        if match is None:
            available = [t["name"] for t in transitions]
            logger.warning(
                "Transition '%s' not found for %s. Available: %s",
                transition_name,
                issue_key,
                available,
            )
            return False

        self._client.transition_issue(issue_key, match["id"])
        logger.info(
            "Transitioned %s via '%s' → '%s'",
            issue_key,
            transition_name,
            match["to_status"],
        )
        return True

    # -- attachments ----------------------------------------------------------

    def download_attachment(
        self, attachment_id: str, filename: str, ticket_key: str
    ) -> Path:
        """Download an attachment to ``JIRA_ATTACHMENTS_DIR/<ticket_key>/``.

        Returns the local :class:`Path` of the saved file.
        """
        dest_dir = Path(settings.JIRA_ATTACHMENTS_DIR) / ticket_key
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / filename

        attachment = self._client.attachment(attachment_id)
        content = attachment.get()  # binary content
        dest_path.write_bytes(content)
        logger.info("Downloaded attachment %s → %s", attachment_id, dest_path)
        return dest_path

    # -- ADF / text helpers ---------------------------------------------------

    @staticmethod
    def parse_adf_to_text(adf: dict) -> str:
        """Convert an Atlassian Document Format (ADF) JSON tree to plain text.

        Handles common node types: ``paragraph``, ``text``, ``heading``,
        ``bulletList``, ``orderedList``, ``listItem``, ``codeBlock``, and
        ``hardBreak``.
        """

        def _walk(node: dict) -> str:  # noqa: C901
            node_type = node.get("type", "")
            children = node.get("content", [])

            if node_type == "text":
                return node.get("text", "")

            if node_type == "hardBreak":
                return "\n"

            if node_type in ("paragraph", "heading"):
                inner = "".join(_walk(c) for c in children)
                return inner + "\n"

            if node_type in ("bulletList", "orderedList"):
                lines: list[str] = []
                for idx, child in enumerate(children, start=1):
                    item_text = "".join(_walk(c) for c in child.get("content", []))
                    item_text = item_text.strip()
                    if node_type == "orderedList":
                        lines.append(f"{idx}. {item_text}")
                    else:
                        lines.append(f"- {item_text}")
                return "\n".join(lines) + "\n"

            if node_type == "listItem":
                return "".join(_walk(c) for c in children)

            if node_type == "codeBlock":
                inner = "".join(_walk(c) for c in children)
                return f"```\n{inner}\n```\n"

            # Generic container (doc, blockquote, etc.) – just recurse.
            return "".join(_walk(c) for c in children)

        if not adf or not isinstance(adf, dict):
            return ""
        return _walk(adf).strip()

    @staticmethod
    def parse_acceptance_criteria(description_text: str) -> list[str]:
        """Extract acceptance-criteria bullet items from a plain-text description.

        Looks for a heading matching ``## Acceptance Criteria`` (or ``AC``),
        then collects bullet/numbered items until the next heading or end of
        text.
        """
        if not description_text:
            return []

        heading_re = re.compile(r"^#{1,3}\s*(acceptance\s+criteria|ac)\b", re.IGNORECASE)
        next_heading_re = re.compile(r"^#{1,3}\s+")
        bullet_re = re.compile(r"^\s*(?:[-*]|\d+\.)\s+(.+)")

        lines = description_text.splitlines()
        in_section = False
        criteria: list[str] = []

        for line in lines:
            if not in_section:
                if heading_re.match(line):
                    in_section = True
                continue

            # Stop when we hit the next heading.
            if next_heading_re.match(line):
                break

            m = bullet_re.match(line)
            if m:
                criteria.append(m.group(1).strip())

        return criteria

    # -- internal helpers -----------------------------------------------------

    def _parse_issue(self, issue) -> JiraTicket:
        """Convert a ``jira.Issue`` object into a :class:`JiraTicket`."""
        fields = issue.fields

        # -- description (may be ADF dict or plain string) ---
        raw_desc = fields.description
        if isinstance(raw_desc, dict):
            description = self.parse_adf_to_text(raw_desc)
        else:
            description = raw_desc or ""

        # -- linked issues ---
        linked: list[dict] = []
        for link in getattr(fields, "issuelinks", None) or []:
            if hasattr(link, "inwardIssue") and link.inwardIssue:
                linked.append(
                    {
                        "key": link.inwardIssue.key,
                        "summary": link.inwardIssue.fields.summary,
                        "status": str(link.inwardIssue.fields.status),
                        "link_type": link.type.inward,
                    }
                )
            if hasattr(link, "outwardIssue") and link.outwardIssue:
                linked.append(
                    {
                        "key": link.outwardIssue.key,
                        "summary": link.outwardIssue.fields.summary,
                        "status": str(link.outwardIssue.fields.status),
                        "link_type": link.type.outward,
                    }
                )

        # -- attachments (images only) ---
        image_attachments: list[dict] = []
        for att in getattr(fields, "attachment", None) or []:
            mime = getattr(att, "mimeType", "") or ""
            if mime.startswith("image/"):
                image_attachments.append(
                    {
                        "id": att.id,
                        "filename": att.filename,
                        "mime_type": mime,
                        "size": getattr(att, "size", 0),
                        "url": getattr(att, "content", ""),
                    }
                )

        # -- acceptance criteria from description text ---
        acceptance = self.parse_acceptance_criteria(description)

        # -- parent / epic link (team-managed Epic links and Sub-task parents) ---
        parent = getattr(fields, "parent", None)
        parent_key = getattr(parent, "key", None) if parent else None

        return JiraTicket(
            key=issue.key,
            summary=fields.summary or "",
            description=description,
            status=str(fields.status),
            issue_type=str(fields.issuetype),
            priority=str(fields.priority) if fields.priority else None,
            parent_key=parent_key,
            acceptance_criteria=acceptance,
            linked_issues=linked,
            attachments=image_attachments,
        )
