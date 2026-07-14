"""Tests for auto-resolving Jira tickets mentioned in a ticket's own text."""

import pytest

from app.services import jira_service as js
from app.services.jira_service import (
    JiraTicket,
    build_referenced_tickets_context,
    extract_jira_keys,
)


# -- extract_jira_keys -------------------------------------------------------

def test_extract_finds_keys_in_order_and_dedupes():
    text = "Blocked by OIPO-667. See also MMA-3481 and OIPO-667 again."
    assert extract_jira_keys(text) == ["OIPO-667", "MMA-3481"]


def test_extract_excludes_own_key():
    text = "This is MMA-3481; rules per OIPO-667."
    assert extract_jira_keys(text, exclude={"MMA-3481"}) == ["OIPO-667"]


def test_extract_filters_non_jira_tokens():
    text = "Encode as UTF-8 and hash with SHA-256; rule is OIPO-667."
    assert extract_jira_keys(text) == ["OIPO-667"]


def test_extract_handles_empty():
    assert extract_jira_keys("") == []
    assert extract_jira_keys(None) == []


# -- build_referenced_tickets_context ----------------------------------------

class _FakeService:
    def __init__(self, bodies: dict[str, str], missing: set[str] | None = None):
        self.bodies = bodies
        self.missing = missing or set()
        self.fetched: list[str] = []

    def get_ticket(self, key: str) -> JiraTicket:
        self.fetched.append(key)
        if key in self.missing:
            raise RuntimeError("404: issue does not exist or no permission")
        return JiraTicket(
            key=key,
            summary=f"Summary of {key}",
            description=self.bodies.get(key, ""),
            status="Done",
            issue_type="Story",
            priority=None,
        )


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(js.JiraService, "is_configured", staticmethod(lambda: True))
    monkeypatch.setattr(js.settings, "CODE_AGENT_MAX_REFERENCED_TICKETS", 5)

    def _install(svc):
        monkeypatch.setattr(js, "_get_ref_service", lambda: svc)
        return svc

    return _install


def test_returns_empty_when_not_configured(monkeypatch):
    monkeypatch.setattr(js.JiraService, "is_configured", staticmethod(lambda: False))
    assert build_referenced_tickets_context("mentions OIPO-667") == ""


def test_fetches_and_formats_referenced_body(configured):
    svc = configured(_FakeService({"OIPO-667": "Block DRY+REEF mixes."}))
    out = build_referenced_tickets_context("rules per OIPO-667", exclude={"MMA-3481"})
    assert svc.fetched == ["OIPO-667"]
    assert "### OIPO-667 (Done) — Summary of OIPO-667" in out
    assert "Block DRY+REEF mixes." in out
    assert out.startswith("Referenced Jira tickets")


def test_skips_unfetchable_tickets(configured):
    svc = configured(
        _FakeService({"OIPO-667": "rule body"}, missing={"OIPO-999"})
    )
    out = build_referenced_tickets_context("see OIPO-999 and OIPO-667")
    assert svc.fetched == ["OIPO-999", "OIPO-667"]
    assert "OIPO-999" not in out
    assert "OIPO-667" in out


def test_returns_empty_when_all_unfetchable(configured):
    configured(_FakeService({}, missing={"OIPO-999"}))
    assert build_referenced_tickets_context("see OIPO-999") == ""


def test_respects_max_tickets_cap(configured, monkeypatch):
    monkeypatch.setattr(js.settings, "CODE_AGENT_MAX_REFERENCED_TICKETS", 2)
    svc = configured(
        _FakeService({f"AB-{i}": f"body {i}" for i in range(5)})
    )
    build_referenced_tickets_context("AB-1 AB-2 AB-3 AB-4 AB-5")
    assert svc.fetched == ["AB-1", "AB-2"]


def test_truncates_long_body(configured):
    svc = configured(_FakeService({"OIPO-667": "x" * 5000}))
    out = build_referenced_tickets_context(
        "OIPO-667", body_cap=100
    )
    assert "…[truncated]" in out
    assert out.count("x") == 100
