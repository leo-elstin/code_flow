import asyncio

from app.agents.roles import epic_planner
from app.agents.roles.epic_planner import build_levels, _normalize_dependencies, run_epic_planner


def test_build_levels_parallel_then_gated():
    # 1 and 2 are independent; 3 depends on both.
    levels, had_cycle = build_levels([1, 2, 3], {3: [1, 2]})
    assert had_cycle is False
    assert levels == [[1, 2], [3]]


def test_build_levels_cycle_falls_back_to_sequential():
    levels, had_cycle = build_levels([1, 2], {1: [2], 2: [1]})
    assert had_cycle is True
    assert levels == [[1], [2]]


def test_build_levels_chain():
    levels, had_cycle = build_levels([5, 6, 7], {6: [5], 7: [6]})
    assert had_cycle is False
    assert levels == [[5], [6], [7]]


def test_build_levels_drops_unknown_and_self_deps():
    # 99 is not a node, and a self-dep must be ignored (else it would look cyclic).
    levels, had_cycle = build_levels([1, 2], {2: [1, 99, 2]})
    assert had_cycle is False
    assert levels == [[1], [2]]


def test_normalize_dependencies_filters_invalid():
    raw = [
        {"ticket_id": 3, "depends_on": [1, 2, 99]},
        {"ticket_id": 1, "depends_on": []},
        {"ticket_id": "x"},      # unpar.seable id
        {"no_ticket": True},     # malformed
    ]
    edges = _normalize_dependencies(raw, {1, 2, 3})
    assert edges == {3: [1, 2], 1: []}


def _child(tid, title, **extra):
    base = {"id": tid, "jira_key": f"K-{tid}", "title": title, "jira_issue_type": "Story",
            "description": ""}
    base.update(extra)
    return base


def test_run_epic_planner_trivial_skips_llm(monkeypatch):
    # Single child needs no ordering and must not call the LLM.
    called = {"n": 0}

    async def _boom(**_kwargs):
        called["n"] += 1
        return {}, {}

    monkeypatch.setattr(epic_planner, "chat_completion_json", _boom)
    result = asyncio.run(run_epic_planner(_child(1, "Epic"), [_child(5, "only story")]))
    assert called["n"] == 0
    assert result["levels"] == [[5]]
    assert result["had_cycle"] is False


def test_run_epic_planner_uses_llm_edges(monkeypatch):
    async def _fake(**_kwargs):
        return (
            {"reasoning": "3 builds on 1 and 2", "dependencies": [
                {"ticket_id": 3, "depends_on": [1, 2]},
            ]},
            {},
        )

    monkeypatch.setattr(epic_planner, "chat_completion_json", _fake)
    children = [_child(1, "a"), _child(2, "b"), _child(3, "c")]
    result = asyncio.run(run_epic_planner(_child(1, "Epic"), children))
    assert result["levels"] == [[1, 2], [3]]
    assert result["edges"] == {3: [1, 2]}
    assert result["had_cycle"] is False
    assert len(result["nodes"]) == 3


def test_run_epic_planner_degrades_on_llm_error(monkeypatch):
    async def _raise(**_kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(epic_planner, "chat_completion_json", _raise)
    children = [_child(1, "a"), _child(2, "b")]
    result = asyncio.run(run_epic_planner(_child(1, "Epic"), children))
    # No edges → both independent (still a valid plan), planner did not crash.
    assert result["levels"] == [[1, 2]]
    assert "llm down" in result["reasoning"]
