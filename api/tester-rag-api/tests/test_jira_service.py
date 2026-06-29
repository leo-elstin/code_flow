"""Unit tests for app.services.jira_service."""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.jira_service import JiraService, JiraTicket


class TestParseAdfToText:
    """Tests for Atlassian Document Format → plain text conversion."""

    def test_simple_paragraph(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Hello world"}],
                }
            ],
        }
        assert "Hello world" in JiraService.parse_adf_to_text(adf)

    def test_multiple_paragraphs(self):
        adf = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "First"}]},
                {"type": "paragraph", "content": [{"type": "text", "text": "Second"}]},
            ],
        }
        result = JiraService.parse_adf_to_text(adf)
        assert "First" in result
        assert "Second" in result

    def test_heading(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 2},
                    "content": [{"type": "text", "text": "My Heading"}],
                }
            ],
        }
        result = JiraService.parse_adf_to_text(adf)
        assert "My Heading" in result

    def test_bullet_list(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Item A"}],
                                }
                            ],
                        },
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Item B"}],
                                }
                            ],
                        },
                    ],
                }
            ],
        }
        result = JiraService.parse_adf_to_text(adf)
        assert "- Item A" in result
        assert "- Item B" in result

    def test_hard_break(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Before"},
                        {"type": "hardBreak"},
                        {"type": "text", "text": "After"},
                    ],
                }
            ],
        }
        result = JiraService.parse_adf_to_text(adf)
        assert "Before" in result
        assert "After" in result

    def test_empty_dict(self):
        assert JiraService.parse_adf_to_text({}) == ""

    def test_no_content_key(self):
        assert JiraService.parse_adf_to_text({"type": "doc"}) == ""

    def test_code_block(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "codeBlock",
                    "content": [{"type": "text", "text": "print('hello')"}],
                }
            ],
        }
        result = JiraService.parse_adf_to_text(adf)
        assert "print('hello')" in result

    def test_ordered_list(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "orderedList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "First"}],
                                }
                            ],
                        },
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Second"}],
                                }
                            ],
                        },
                    ],
                }
            ],
        }
        result = JiraService.parse_adf_to_text(adf)
        assert "1. First" in result
        assert "2. Second" in result


class TestParseAcceptanceCriteria:
    """Tests for extracting acceptance criteria from description text."""

    def test_with_heading(self):
        text = """## Description
Some feature description.

## Acceptance Criteria
- Users can log in with email
- Password reset works
- Session expires after 30 min

## Notes
Some notes here.
"""
        criteria = JiraService.parse_acceptance_criteria(text)
        assert len(criteria) == 3
        assert "Users can log in with email" in criteria[0]
        assert "Password reset works" in criteria[1]

    def test_ac_heading_case_insensitive(self):
        text = """## acceptance criteria
- Item 1
- Item 2
"""
        criteria = JiraService.parse_acceptance_criteria(text)
        assert len(criteria) == 2

    def test_short_ac_heading(self):
        text = """### AC
* Criterion A
* Criterion B
"""
        criteria = JiraService.parse_acceptance_criteria(text)
        assert len(criteria) == 2

    def test_no_acceptance_criteria(self):
        text = "Just a regular description without any AC section."
        criteria = JiraService.parse_acceptance_criteria(text)
        assert criteria == []

    def test_empty_string(self):
        assert JiraService.parse_acceptance_criteria("") == []

    def test_numbered_list(self):
        text = """## Acceptance Criteria
1. First criterion
2. Second criterion
"""
        criteria = JiraService.parse_acceptance_criteria(text)
        assert len(criteria) == 2


class TestJiraServiceConfigured:
    """Tests for the is_configured check."""

    @patch("app.services.jira_service.settings")
    def test_configured_when_all_set(self, mock_settings):
        mock_settings.JIRA_BASE_URL = "https://test.atlassian.net"
        mock_settings.JIRA_USER_EMAIL = "test@example.com"
        mock_settings.JIRA_API_TOKEN = "token123"
        assert JiraService.is_configured() is True

    @patch("app.services.jira_service.settings")
    def test_not_configured_when_missing_url(self, mock_settings):
        mock_settings.JIRA_BASE_URL = None
        mock_settings.JIRA_USER_EMAIL = "test@example.com"
        mock_settings.JIRA_API_TOKEN = "token123"
        assert JiraService.is_configured() is False

    @patch("app.services.jira_service.settings")
    def test_not_configured_when_missing_email(self, mock_settings):
        mock_settings.JIRA_BASE_URL = "https://test.atlassian.net"
        mock_settings.JIRA_USER_EMAIL = None
        mock_settings.JIRA_API_TOKEN = "token123"
        assert JiraService.is_configured() is False

    @patch("app.services.jira_service.settings")
    def test_not_configured_when_missing_token(self, mock_settings):
        mock_settings.JIRA_BASE_URL = "https://test.atlassian.net"
        mock_settings.JIRA_USER_EMAIL = "test@example.com"
        mock_settings.JIRA_API_TOKEN = None
        assert JiraService.is_configured() is False
