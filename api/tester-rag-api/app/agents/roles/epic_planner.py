"""Epic dependency planner.

Given a Jira Epic and its child stories, decide which stories can run in
parallel and which must wait for others. The output is a set of *topological
levels*: every story in level N may run concurrently, and level N only starts
once all earlier levels have completed.

The LLM proposes per-story ``depends_on`` edges; :func:`build_levels` turns
those into levels deterministically and falls back to a fully sequential plan
(ordered by ticket id) if the graph is invalid or contains a cycle.
"""

from __future__ import annotations

import json
from typing import Any

from app.services.generation import chat_completion_json
from app.services.run_activity import append_activity

EPIC_PLANNER_SYSTEM = """You are a delivery lead sequencing the stories under one epic.
You are given an epic and its child stories. Decide the execution order: which stories are
independent (can be built in parallel) and which depend on another story's code being in place first.
Return JSON only with keys:
- reasoning (string) — brief explanation of the ordering choices
- dependencies (list of {ticket_id (int), depends_on (list of int ticket_ids), rationale (string)})
Rules:
- Only reference ticket_ids from the provided stories. Never invent ids.
- depends_on means "needs this other story's changes merged before it can start".
- Prefer parallelism: only add a dependency when there is a real ordering need
  (shared files, foundational scaffolding, API a later story consumes).
- Do not create cycles.
"""


def _child_brief(ticket: dict) -> dict[str, Any]:
    """Compact, prompt-friendly view of a child story.

    Jira-sourced descriptions are stored as a JSON blob (see ``jira_sync``);
    fall back to the raw string for locally-authored tickets.
    """
    desc = ticket.get("description") or ""
    acceptance: list[str] = []
    if isinstance(desc, str) and desc.strip().startswith("{"):
        try:
            parsed = json.loads(desc)
            acceptance = parsed.get("acceptance_criteria") or []
            desc = parsed.get("raw_description") or ""
        except (ValueError, TypeError):
            pass
    return {
        "ticket_id": ticket["id"],
        "jira_key": ticket.get("jira_key"),
        "title": ticket.get("title"),
        "type": ticket.get("jira_issue_type") or ticket.get("ticket_type"),
        "description": (desc or "")[:600],
        "acceptance_criteria": acceptance[:8],
    }


def build_levels(
    ids: list[int], edges: dict[int, list[int]]
) -> tuple[list[list[int]], bool]:
    """Group *ids* into topological levels given dependency *edges*.

    ``edges[i]`` lists ids that must complete before ``i`` starts. Returns
    ``(levels, had_cycle)``. On a cycle (or if no progress can be made) the
    fallback is one story per level, ordered by id (fully sequential).
    """
    idset = set(ids)
    deps: dict[int, list[int]] = {
        i: [d for d in dict.fromkeys(edges.get(i, [])) if d in idset and d != i]
        for i in ids
    }

    level: dict[int, int] = {}
    remaining = set(ids)
    while remaining:
        ready = [i for i in remaining if all(d not in remaining for d in deps[i])]
        if not ready:
            # Cycle (or unsatisfiable) — degrade to a safe sequential chain.
            return [[i] for i in sorted(ids)], True
        for i in ready:
            level[i] = max((level[d] for d in deps[i]), default=-1) + 1
        remaining -= set(ready)

    by_level: dict[int, list[int]] = {}
    for i, lv in level.items():
        by_level.setdefault(lv, []).append(i)
    levels = [sorted(by_level[lv]) for lv in sorted(by_level)]
    return levels, False


def _normalize_dependencies(
    raw: Any, valid_ids: set[int]
) -> dict[int, list[int]]:
    """Turn the LLM's ``dependencies`` list into a clean {id: [dep_ids]} map."""
    edges: dict[int, list[int]] = {}
    if not isinstance(raw, list):
        return edges
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            tid = int(item.get("ticket_id"))
        except (TypeError, ValueError):
            continue
        if tid not in valid_ids:
            continue
        deps_raw = item.get("depends_on") or []
        deps: list[int] = []
        if isinstance(deps_raw, list):
            for d in deps_raw:
                try:
                    di = int(d)
                except (TypeError, ValueError):
                    continue
                if di in valid_ids and di != tid:
                    deps.append(di)
        edges[tid] = list(dict.fromkeys(deps))
    return edges


async def run_epic_planner(
    epic: dict,
    children: list[dict],
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Produce an execution plan (levels + edges) for *epic*'s *children*.

    ``run_id`` (the epic-run id) is used only for activity logging.
    Returns ``{levels, edges, reasoning, had_cycle, nodes}``.
    """
    ids = [c["id"] for c in children]
    nodes = [
        {"ticket_id": c["id"], "jira_key": c.get("jira_key"), "title": c.get("title")}
        for c in children
    ]

    if run_id:
        append_activity(
            run_id,
            type="status",
            phase="planner",
            title="Planning epic execution order",
            detail=f"{epic.get('jira_key') or epic.get('title')} — {len(ids)} stories",
        )

    # Trivial graphs need no LLM call.
    if len(ids) <= 1:
        levels, had_cycle = build_levels(ids, {})
        return {
            "levels": levels,
            "edges": {},
            "reasoning": "Single story — no ordering needed." if ids else "No child stories.",
            "had_cycle": False,
            "nodes": nodes,
        }

    payload = {
        "epic": {
            "ticket_id": epic["id"],
            "jira_key": epic.get("jira_key"),
            "title": epic.get("title"),
        },
        "stories": [_child_brief(c) for c in children],
    }
    user_message = {
        "role": "user",
        "content": (
            "Sequence these stories for execution.\n\n"
            f"{json.dumps(payload, indent=2)}\n\n"
            "Return the dependency JSON."
        ),
    }

    edges: dict[int, list[int]] = {}
    reasoning = ""
    try:
        result, _usage = await chat_completion_json(
            messages=[
                {"role": "system", "content": EPIC_PLANNER_SYSTEM},
                user_message,
            ],
            run_id=run_id,
            phase="planner",
            label="epic planner",
        )
        edges = _normalize_dependencies(result.get("dependencies"), set(ids))
        reasoning = str(result.get("reasoning") or "")
    except Exception as exc:  # noqa: BLE001 — planning must degrade, not crash
        reasoning = f"Planner failed ({exc}); falling back to sequential order."
        edges = {}

    levels, had_cycle = build_levels(ids, edges)
    if had_cycle and not reasoning.endswith("sequential order."):
        reasoning += " (Dependency cycle detected — using sequential order.)"

    if run_id:
        append_activity(
            run_id,
            type="status",
            phase="planner",
            title="Epic plan ready for approval",
            detail=f"{len(levels)} level(s); "
            + ", ".join(f"L{i}: {len(lv)}" for i, lv in enumerate(levels)),
        )

    return {
        "levels": levels,
        "edges": edges,
        "reasoning": reasoning,
        "had_cycle": had_cycle,
        "nodes": nodes,
    }
