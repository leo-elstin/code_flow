"""Unit tests for app.services.jira_sync."""

import json
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from app.services.jira_sync import SyncResult, sync_jira_tickets, update_jira_status_for_run


@pytest.fixture
def mock_project():
    return {
        "id": 1,
        "name": "TestProject",
        "path": "/tmp/test-project",
        "jira_jql": "project = TEST AND status = 'To Do'",
        "jira_status_mapping": json.dumps({
            "developing": "In Progress",
            "completed": "Done",
            "failed": "To Do",
        }),
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
    }


@pytest.fixture
def mock_jira_ticket():
    from app.services.jira_service import JiraTicket

    return JiraTicket(
        key="TEST-1",
        summary="Implement login feature",
        description="As a user I want to log in",
        status="To Do",
        issue_type="Story",
        priority="High",
        acceptance_criteria=["User can log in", "Session is created"],
        linked_issues=[
            {"key": "TEST-2", "summary": "Auth API", "status": "Done", "link_type": "relates to"}
        ],
        attachments=[
            {"id": "100", "filename": "mockup.png", "mime_type": "image/png", "size": 1024, "url": "https://jira/attach/100"}
        ],
    )


class TestSyncJiraTickets:
    """Tests for the Jira ticket sync flow."""

    @patch("app.services.jira_sync.project_ticket_store")
    @patch("app.services.jira_sync.JiraService")
    def test_sync_creates_tickets(self, mock_jira_cls, mock_store, mock_project, mock_jira_ticket):
        mock_store.get_project.return_value = mock_project
        mock_service = MagicMock()
        mock_service.search_tickets.return_value = [mock_jira_ticket]
        mock_service.download_attachment.return_value = "/data/jira_attachments/TEST-1/mockup.png"
        mock_jira_cls.return_value = mock_service

        mock_store.upsert_jira_ticket.return_value = {
            "id": 1, "project_id": 1, "title": "Implement login feature",
            "source": "jira", "jira_key": "TEST-1", "status": "pending",
            "created_at": "2025-01-01", "updated_at": "2025-01-01",
        }

        result = sync_jira_tickets(1)

        assert isinstance(result, SyncResult)
        assert result.total == 1
        mock_store.upsert_jira_ticket.assert_called_once()
        mock_service.search_tickets.assert_called_once_with(mock_project["jira_jql"])
        mock_service.download_attachment.assert_called_once()

    @patch("app.services.jira_sync.project_ticket_store")
    def test_sync_raises_when_no_jql(self, mock_store, mock_project):
        mock_project["jira_jql"] = None
        mock_store.get_project.return_value = mock_project

        with pytest.raises(ValueError, match="JQL"):
            sync_jira_tickets(1)

    @patch("app.services.jira_sync.project_ticket_store")
    def test_sync_raises_when_project_not_found(self, mock_store):
        mock_store.get_project.return_value = None

        with pytest.raises(KeyError, match="not found"):
            sync_jira_tickets(1)

    @patch("app.services.jira_sync.project_ticket_store")
    @patch("app.services.jira_sync.JiraService")
    def test_sync_maps_bug_type(self, mock_jira_cls, mock_store, mock_project):
        from app.services.jira_service import JiraTicket

        bug_ticket = JiraTicket(
            key="TEST-5", summary="Fix crash", description="App crashes on login",
            status="To Do", issue_type="Bug", priority="Critical",
            acceptance_criteria=[], linked_issues=[], attachments=[],
        )
        mock_store.get_project.return_value = mock_project
        mock_service = MagicMock()
        mock_service.search_tickets.return_value = [bug_ticket]
        mock_jira_cls.return_value = mock_service
        mock_store.upsert_jira_ticket.return_value = {"id": 2, "source": "jira", "jira_key": "TEST-5"}

        result = sync_jira_tickets(1)

        call_args = mock_store.upsert_jira_ticket.call_args
        # ticket_type should be "bug"
        assert "bug" in str(call_args)

    @patch("app.services.jira_sync.project_ticket_store")
    @patch("app.services.jira_sync.JiraService")
    def test_sync_attachment_error_continues(self, mock_jira_cls, mock_store, mock_project, mock_jira_ticket):
        """Attachment download failures should not break the sync."""
        mock_store.get_project.return_value = mock_project
        mock_service = MagicMock()
        mock_service.search_tickets.return_value = [mock_jira_ticket]
        mock_service.download_attachment.side_effect = Exception("Download failed")
        mock_jira_cls.return_value = mock_service
        mock_store.upsert_jira_ticket.return_value = {"id": 1, "source": "jira", "jira_key": "TEST-1"}

        result = sync_jira_tickets(1)

        assert result.total == 1
        assert len(result.errors) >= 1


class TestUpdateJiraStatusForRun:
    """Tests for the Jira status transition hook."""

    @patch("app.services.jira_sync.JiraService")
    @patch("app.services.jira_sync.project_ticket_store")
    def test_transitions_jira_ticket(self, mock_store, mock_jira_cls, mock_project):
        mock_store.get_ticket_by_run_id.return_value = {
            "id": 1, "project_id": 1, "source": "jira", "jira_key": "TEST-1",
            "run_id": "run-abc", "status": "developing",
        }
        mock_store.get_project.return_value = mock_project

        mock_service = MagicMock()
        mock_service.transition_ticket.return_value = True
        mock_jira_cls.return_value = mock_service

        # Should not raise
        update_jira_status_for_run("run-abc", "completed")

        mock_service.transition_ticket.assert_called_once_with("TEST-1", "Done")

    @patch("app.services.jira_sync.project_ticket_store")
    def test_skips_local_ticket(self, mock_store):
        mock_store.get_ticket_by_run_id.return_value = {
            "id": 1, "source": "local", "jira_key": None,
            "run_id": "run-abc",
        }

        # Should return without error
        update_jira_status_for_run("run-abc", "completed")

    @patch("app.services.jira_sync.project_ticket_store")
    def test_skips_when_no_ticket(self, mock_store):
        mock_store.get_ticket_by_run_id.return_value = None

        # Should return without error
        update_jira_status_for_run("run-abc", "completed")

    @patch("app.services.jira_sync.JiraService")
    @patch("app.services.jira_sync.project_ticket_store")
    def test_no_mapping_for_status(self, mock_store, mock_jira_cls, mock_project):
        mock_store.get_ticket_by_run_id.return_value = {
            "id": 1, "project_id": 1, "source": "jira", "jira_key": "TEST-1",
            "run_id": "run-abc",
        }
        mock_store.get_project.return_value = mock_project

        # "planning" has no mapping in the test fixture
        update_jira_status_for_run("run-abc", "planning")

        # Should not have called transition
        mock_jira_cls.return_value.transition_ticket.assert_not_called()

    @patch("app.services.jira_sync.JiraService")
    @patch("app.services.jira_sync.project_ticket_store")
    def test_jira_error_does_not_raise(self, mock_store, mock_jira_cls, mock_project):
        """Jira API errors should be swallowed — never crash the agent run."""
        mock_store.get_ticket_by_run_id.return_value = {
            "id": 1, "project_id": 1, "source": "jira", "jira_key": "TEST-1",
            "run_id": "run-abc",
        }
        mock_store.get_project.return_value = mock_project
        mock_jira_cls.return_value.transition_ticket.side_effect = Exception("Jira API error")

        # Should not raise
        update_jira_status_for_run("run-abc", "completed")
