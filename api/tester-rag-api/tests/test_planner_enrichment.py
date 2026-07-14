"""Planner-time enrichment: referenced Jira tickets are fetched on every plan
attempt (fresh, resume, or retry), not just at run creation."""

import asyncio

from app.orchestration import graph as g


def _run(coro):
    return asyncio.run(coro)


def test_enriches_from_user_request(monkeypatch):
    monkeypatch.setattr(g.settings, "CODE_AGENT_RESOLVE_REFERENCED_TICKETS", True)
    called = {}

    def fake_build(text, **kw):
        called["text"] = text
        return "Referenced Jira tickets (auto-fetched...):\n\n### OIPO-667 (Done) — block DRY+REEF"

    import app.services.jira_service as js
    monkeypatch.setattr(js, "build_referenced_tickets_context", fake_build)

    state = {
        "run_id": "r1",
        "user_request": "Story mentions OIPO-667 blocked mixes",
        "acceptance_criteria_hint": ["Validate per OIPO-667"],
        "linked_issues_context": None,
    }
    out = _run(g._enrich_linked_issues_context(state))
    assert "OIPO-667" in out
    assert "OIPO-667" in called["text"] and "Validate per OIPO-667" in called["text"]


def test_idempotent_when_already_enriched(monkeypatch):
    monkeypatch.setattr(g.settings, "CODE_AGENT_RESOLVE_REFERENCED_TICKETS", True)

    def fake_build(text, **kw):
        raise AssertionError("should not fetch again")

    import app.services.jira_service as js
    monkeypatch.setattr(js, "build_referenced_tickets_context", fake_build)

    existing = "Referenced Jira tickets (auto-fetched...):\n\n### OIPO-667 (Done) — x"
    state = {"run_id": "r1", "user_request": "OIPO-667", "linked_issues_context": existing}
    out = _run(g._enrich_linked_issues_context(state))
    assert out == existing


def test_disabled_flag_is_noop(monkeypatch):
    monkeypatch.setattr(g.settings, "CODE_AGENT_RESOLVE_REFERENCED_TICKETS", False)
    state = {"run_id": "r1", "user_request": "OIPO-667", "linked_issues_context": "orig"}
    out = _run(g._enrich_linked_issues_context(state))
    assert out == "orig"


def test_failure_preserves_original(monkeypatch):
    monkeypatch.setattr(g.settings, "CODE_AGENT_RESOLVE_REFERENCED_TICKETS", True)

    def boom(text, **kw):
        raise RuntimeError("jira down")

    import app.services.jira_service as js
    monkeypatch.setattr(js, "build_referenced_tickets_context", boom)

    state = {"run_id": "r1", "user_request": "OIPO-667", "linked_issues_context": "orig"}
    out = _run(g._enrich_linked_issues_context(state))
    assert out == "orig"
