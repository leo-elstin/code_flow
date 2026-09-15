"""Orchestration tests for the epic runner using a fake per-ticket runner.

No real LLM, git, or dev pipeline: the planner and child runner are stubbed so
we can assert ordering (parallel level then gated level) and failure handling.
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from app.orchestration import epic_runner as er_mod
from app.services import epic_run_store


class FakeRunner:
    """Stand-in for CodeAgentRunner: each child goes awaiting → terminal."""

    def __init__(
        self,
        outcomes: dict[int, str],
        retry_outcomes: dict[int, str] | None = None,
        clarify: set[int] | None = None,
    ):
        self.outcomes = outcomes          # ticket_id -> "completed" | "failed"
        self.retry_outcomes = retry_outcomes or {}
        self.clarify = clarify or set()   # tickets whose planner asks questions
        self.started: list[int] = []      # ticket_ids in start order
        self.retried: list[int] = []      # ticket_ids retried via retry_run
        self.completed: set[int] = set()
        self.start_snapshot: dict[int, set] = {}  # completed-set at each start
        self._byrun: dict[str, dict] = {}
        self._n = 0

    def _task_running(self, run_id):
        return False

    async def wait_for_task(self, run_id, timeout):
        return None

    async def start_run(self, request, project_path, ticket_id=None, *, auto_approve=None):
        self.started.append(ticket_id)
        self.start_snapshot[ticket_id] = set(self.completed)
        self._n += 1
        rid = f"run-{ticket_id}-{self._n}"
        phase = "clarify" if ticket_id in self.clarify else "awaiting"
        self._byrun[rid] = {"ticket_id": ticket_id, "phase": phase}
        return rid

    async def approve_run(self, run_id, *, workspace_mode="worktree", base_ref=None):
        self._byrun[run_id]["phase"] = "terminal"
        return {"status": "developing"}

    async def retry_run(self, run_id):
        info = self._byrun[run_id]
        tid = info["ticket_id"]
        self.retried.append(tid)
        self.outcomes[tid] = self.retry_outcomes.get(tid, "completed")
        self._n += 1
        rid = f"run-{tid}-{self._n}"
        self._byrun[rid] = {"ticket_id": tid, "phase": "terminal"}
        return {"run_id": rid, "status": "developing"}

    async def get_state(self, run_id):
        info = self._byrun.get(run_id)
        if not info:
            return None
        if info["phase"] == "clarify":
            return {"status": "awaiting_clarification", "run_id": run_id}
        if info["phase"] == "awaiting":
            return {"status": "awaiting_approval", "run_id": run_id}
        outcome = self.outcomes.get(info["ticket_id"], "completed")
        if outcome == "completed":
            self.completed.add(info["ticket_id"])
        return {"status": outcome, "run_id": run_id, "worktree_path": "/tmp/x"}


def _patch_common(monkeypatch, tmp, fake):
    monkeypatch.setattr(epic_run_store, "_DB_PATH", Path(tmp) / "epic_runs.db")
    monkeypatch.setattr(er_mod, "runner", fake)
    monkeypatch.setattr(er_mod, "append_activity", lambda *a, **k: {})
    # Skip real git: pretend an integration worktree exists and merges succeed.
    monkeypatch.setattr(er_mod, "_create_integration_worktree", lambda *a, **k: "/tmp/intwt")
    monkeypatch.setattr(er_mod, "_open_integration_worktree", lambda *a, **k: "/tmp/intwt")
    monkeypatch.setattr(er_mod, "_merge_child_into_integration", lambda *a, **k: {"applied": True})
    monkeypatch.setattr(er_mod, "_POLL_MIN_S", 0.001)
    monkeypatch.setattr(er_mod, "_POLL_MAX_S", 0.01)

    epic = {"id": 10, "project_id": 1, "jira_key": "E-10", "title": "Epic"}
    children = [
        {"id": 1, "project_id": 1, "jira_key": "S-1", "title": "a", "description": ""},
        {"id": 2, "project_id": 1, "jira_key": "S-2", "title": "b", "description": ""},
        {"id": 3, "project_id": 1, "jira_key": "S-3", "title": "c", "description": ""},
    ]
    by_id = {10: epic, 1: children[0], 2: children[1], 3: children[2]}
    monkeypatch.setattr(er_mod, "get_ticket", lambda tid: by_id.get(tid))
    monkeypatch.setattr(er_mod, "get_project", lambda pid: {"id": 1, "path": "/repo"})
    monkeypatch.setattr(er_mod, "list_children", lambda pid, key: children)

    # 1,2 independent (level 0); 3 depends on both (level 1).
    async def _fake_plan(epic_arg, children_arg, *, run_id=None):
        return {
            "levels": [[1, 2], [3]],
            "edges": {3: [1, 2]},
            "reasoning": "stub",
            "had_cycle": False,
            "nodes": [{"ticket_id": c["id"], "jira_key": c["jira_key"], "title": c["title"]}
                      for c in children],
        }

    monkeypatch.setattr(er_mod, "run_epic_planner", _fake_plan)


async def _drive(er, ticket_id):
    state = await er.start_epic_run(ticket_id)
    eid = state["epic_run_id"]
    await er._tasks[eid]                       # planning phase
    assert (await er.get_state(eid))["status"] == "awaiting_approval"
    await er.approve_epic_run(eid)
    await er._tasks[eid]                        # execution phase
    return await er.get_state(eid)


def test_epic_runs_parallel_level_then_gated_dependent(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        fake = FakeRunner(outcomes={1: "completed", 2: "completed", 3: "completed"})
        _patch_common(monkeypatch, tmp, fake)
        er = er_mod.EpicAgentRunner()

        final = asyncio.run(_drive(er, 10))

        assert final["status"] == "completed"
        # All three ran; the dependent story started last.
        assert set(fake.started) == {1, 2, 3}
        assert fake.started.index(3) > fake.started.index(1)
        assert fake.started.index(3) > fake.started.index(2)
        # Gating: when 3 started, both predecessors were already completed.
        assert {1, 2} <= fake.start_snapshot[3]
        assert final["child_runs"]["3"]["status"] == "completed"


def test_epic_failure_skips_dependent_stories(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        fake = FakeRunner(outcomes={1: "completed", 2: "failed", 3: "completed"})
        _patch_common(monkeypatch, tmp, fake)
        er = er_mod.EpicAgentRunner()

        final = asyncio.run(_drive(er, 10))

        assert final["status"] == "failed"
        # The dependent story (level 1) must never have started.
        assert 3 not in fake.started
        assert final["child_runs"]["2"]["status"] == "failed"


def test_epic_resume_reruns_only_incomplete_children(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        fake = FakeRunner(outcomes={1: "completed", 2: "failed", 3: "completed"})
        _patch_common(monkeypatch, tmp, fake)
        er = er_mod.EpicAgentRunner()

        final = asyncio.run(_drive(er, 10))
        assert final["status"] == "failed"
        eid = final["epic_run_id"]
        assert final["child_runs"]["1"]["status"] == "completed"
        assert final["child_runs"]["2"]["status"] == "failed"
        assert 3 not in fake.started  # dependent never started

        # "Fix" story 2, then resume. Only the failed story and its now-unblocked
        # dependent should run; the already-completed story 1 must not restart.
        fake.outcomes[2] = "completed"
        fake.started = []

        async def _resume():
            await er.resume_epic_run(eid)
            await er._tasks[eid]
            return await er.get_state(eid)

        resumed = asyncio.run(_resume())

        assert resumed["status"] == "completed"
        assert 1 not in fake.started                       # completed child preserved
        assert 2 in fake.started and 3 in fake.started     # retried + unblocked
        assert resumed["child_runs"]["2"]["status"] == "completed"
        assert resumed["child_runs"]["3"]["status"] == "completed"


def test_get_state_reconciles_orphaned_epic(monkeypatch):
    """A developing epic with no live task (e.g. after a server restart) is
    settled to failed, with child statuses refreshed from the real run states."""
    with tempfile.TemporaryDirectory() as tmp:
        fake = FakeRunner(outcomes={})
        _patch_common(monkeypatch, tmp, fake)
        er = er_mod.EpicAgentRunner()

        eid = "orphan-1"
        epic_run_store.create_epic_run(eid, epic_ticket_id=10, project_id=1, project_path="/repo")
        epic_run_store.update_epic_run(eid, status="developing", plan={"levels": [[1, 2]]})
        # Frozen snapshots from when the task died mid-flight.
        epic_run_store.set_child_run(eid, 1, run_id="r1", status="planning")
        epic_run_store.set_child_run(eid, 2, run_id="r2", status="planning")

        async def fake_get_state(run_id):
            return {"status": "completed" if run_id == "r1" else "failed"}
        monkeypatch.setattr(fake, "get_state", fake_get_state)

        state = asyncio.run(er.get_state(eid))

        assert state["status"] == "failed"
        assert state["child_runs"]["1"]["status"] == "completed"
        assert state["child_runs"]["2"]["status"] == "failed"
        assert "interrupted" in (state.get("error") or "")


def test_get_state_reconciles_orphan_all_completed(monkeypatch):
    """If every planned child actually completed, the orphaned epic settles to
    completed rather than failed."""
    with tempfile.TemporaryDirectory() as tmp:
        fake = FakeRunner(outcomes={})
        _patch_common(monkeypatch, tmp, fake)
        er = er_mod.EpicAgentRunner()

        eid = "orphan-2"
        epic_run_store.create_epic_run(eid, epic_ticket_id=10, project_id=1, project_path="/repo")
        epic_run_store.update_epic_run(eid, status="developing", plan={"levels": [[1, 2]]})
        epic_run_store.set_child_run(eid, 1, run_id="r1", status="completed")
        epic_run_store.set_child_run(eid, 2, run_id="r2", status="developing")

        async def fake_get_state(run_id):
            return {"status": "completed"}
        monkeypatch.setattr(fake, "get_state", fake_get_state)

        state = asyncio.run(er.get_state(eid))

        assert state["status"] == "completed"
        assert state.get("error") in (None, "")


def test_epic_resume_rejected_when_not_failed(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        fake = FakeRunner(outcomes={1: "completed", 2: "completed", 3: "completed"})
        _patch_common(monkeypatch, tmp, fake)
        er = er_mod.EpicAgentRunner()

        final = asyncio.run(_drive(er, 10))
        assert final["status"] == "completed"

        with pytest.raises(ValueError, match="only available for failed"):
            asyncio.run(er.resume_epic_run(final["epic_run_id"]))


async def _drive_auto(er, ticket_id, **kwargs):
    """Drive a full-auto epic: planning chains straight into execution, so keep
    awaiting whichever task is registered until the epic settles."""
    state = await er.start_epic_run(ticket_id, **kwargs)
    eid = state["epic_run_id"]
    for _ in range(4):
        task = er._tasks.get(eid)
        if task:
            await task
        current = await er.get_state(eid)
        if current["status"] in {"completed", "failed", "rejected"}:
            return current
    return await er.get_state(eid)


def _spy_statuses(monkeypatch):
    """Record every status written to the epic run store."""
    statuses: list[str] = []
    orig = epic_run_store.update_epic_run

    def spy(eid, **kwargs):
        if "status" in kwargs:
            statuses.append(kwargs["status"])
        return orig(eid, **kwargs)

    monkeypatch.setattr(epic_run_store, "update_epic_run", spy)
    return statuses


def test_auto_approve_skips_gate_and_completes(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        fake = FakeRunner(outcomes={1: "completed", 2: "completed", 3: "completed"})
        _patch_common(monkeypatch, tmp, fake)
        statuses = _spy_statuses(monkeypatch)
        er = er_mod.EpicAgentRunner()

        final = asyncio.run(_drive_auto(er, 10, auto_approve=True))

        assert final["status"] == "completed"
        assert final["auto_approve"] is True
        assert "awaiting_approval" not in statuses
        assert set(fake.started) == {1, 2, 3}


def test_auto_approve_defaults_from_settings(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        fake = FakeRunner(outcomes={1: "completed", 2: "completed", 3: "completed"})
        _patch_common(monkeypatch, tmp, fake)
        monkeypatch.setattr(er_mod.settings, "EPIC_AUTO_APPROVE", True)
        statuses = _spy_statuses(monkeypatch)
        er = er_mod.EpicAgentRunner()

        final = asyncio.run(_drive_auto(er, 10))  # no explicit flag

        assert final["status"] == "completed"
        assert final["auto_approve"] is True
        assert "awaiting_approval" not in statuses


def test_auto_retry_recovers_failed_child(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        fake = FakeRunner(
            outcomes={1: "completed", 2: "failed", 3: "completed"},
            retry_outcomes={2: "completed"},
        )
        _patch_common(monkeypatch, tmp, fake)
        er = er_mod.EpicAgentRunner()

        final = asyncio.run(_drive_auto(er, 10, auto_approve=True))

        assert final["status"] == "completed"
        assert fake.retried == [2]
        # Child entry repointed to the retry's new run_id.
        assert final["child_runs"]["2"]["run_id"] in fake._byrun
        assert final["child_runs"]["2"]["status"] == "completed"
        assert 3 in fake.started  # dependent unblocked by the successful retry


def test_auto_retry_exhausted_fails_epic(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        fake = FakeRunner(
            outcomes={1: "completed", 2: "failed", 3: "completed"},
            retry_outcomes={2: "failed"},
        )
        _patch_common(monkeypatch, tmp, fake)
        er = er_mod.EpicAgentRunner()

        final = asyncio.run(_drive_auto(er, 10, auto_approve=True))

        assert final["status"] == "failed"
        assert fake.retried == [2]  # bounded: exactly one retry by default
        assert 3 not in fake.started


def test_auto_retry_disabled_when_zero(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        fake = FakeRunner(outcomes={1: "completed", 2: "failed", 3: "completed"})
        _patch_common(monkeypatch, tmp, fake)
        monkeypatch.setattr(er_mod.settings, "EPIC_CHILD_AUTO_RETRIES", 0)
        er = er_mod.EpicAgentRunner()

        final = asyncio.run(_drive_auto(er, 10, auto_approve=True))

        assert final["status"] == "failed"
        assert fake.retried == []


def test_manual_epic_does_not_auto_retry(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        fake = FakeRunner(
            outcomes={1: "completed", 2: "failed", 3: "completed"},
            retry_outcomes={2: "completed"},
        )
        _patch_common(monkeypatch, tmp, fake)
        er = er_mod.EpicAgentRunner()

        final = asyncio.run(_drive(er, 10))

        assert final["status"] == "failed"
        assert fake.retried == []


def test_child_clarification_fails_fast(monkeypatch):
    """A child whose planner asks questions fails promptly instead of burning
    the child timeout — there is no human in the epic loop to answer."""
    with tempfile.TemporaryDirectory() as tmp:
        fake = FakeRunner(
            outcomes={1: "completed", 2: "completed", 3: "completed"},
            clarify={2},
        )
        _patch_common(monkeypatch, tmp, fake)
        er = er_mod.EpicAgentRunner()

        final = asyncio.run(_drive(er, 10))

        assert final["status"] == "failed"
        assert final["child_runs"]["2"]["status"] == "failed"
        assert 3 not in fake.started


def test_epic_integration_branch_naming():
    # Jira-sourced epic -> feature/<KEY>
    assert er_mod._epic_integration_branch("run-1", "PROJ-3480") == "feature/PROJ-3480"
    # whitespace tolerated
    assert er_mod._epic_integration_branch("run-1", " PROJ-3480 ") == "feature/PROJ-3480"
    # no jira key -> run-scoped fallback
    assert er_mod._epic_integration_branch("run-xyz", None) == "agent/epic-run-xyz"
    assert er_mod._epic_integration_branch("run-xyz", "") == "agent/epic-run-xyz"


def test_epic_run_lands_on_feature_branch(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        fake = FakeRunner(outcomes={1: "completed", 2: "completed", 3: "completed"})
        _patch_common(monkeypatch, tmp, fake)
        er = er_mod.EpicAgentRunner()

        final = asyncio.run(_drive(er, 10))

        assert final["status"] == "completed"
        # epic jira_key is E-10 (see _patch_common) -> feature/E-10
        assert final["integration_branch"] == "feature/E-10"
