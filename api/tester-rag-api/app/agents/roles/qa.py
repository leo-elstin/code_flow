import json
from typing import Any

from app.services.generation import chat_completion_json
from app.services.run_activity import append_activity
from app.tools.registry import get_widget_tree


QA_SYSTEM = """You are a QA architect for Flutter features.
Return JSON with keys:
- scenarios (list of {scenario_name, flow_type, steps, verification_points})
- regression_scope (list of strings)
- manual_checklist (list of strings)
"""

# Same caps as the verifier LLM review (app/agents/roles/verifier.py).
_MAX_DIFF_FILES = 20
_MAX_DIFF_CHARS = 6_000


async def run_qa(
    *,
    plan: dict[str, Any],
    acceptance_criteria: list[str],
    diffs: list[dict[str, Any]],
    project_path: str,
    worktree_path: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    if run_id:
        append_activity(
            run_id,
            type="status",
            phase="qa",
            title="QA review started",
        )

    widget_trees: list[dict[str, Any]] = []
    for item in diffs:
        rel = item.get("path", "")
        if rel.endswith("_page.dart") or rel.endswith("_screen.dart"):
            try:
                root = worktree_path if rel.startswith("lib/") else project_path
                widget_trees.append({"path": rel, "widgets": get_widget_tree(root, rel)})
            except Exception:
                continue

    qa_report, _usage = await chat_completion_json(
        messages=[
            {"role": "system", "content": QA_SYSTEM},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "plan": plan,
                        "acceptance_criteria": acceptance_criteria,
                        "diffs": [
                            {
                                "path": d.get("path"),
                                "diff": (d.get("diff") or "")[:_MAX_DIFF_CHARS],
                            }
                            for d in diffs[:_MAX_DIFF_FILES]
                        ],
                        "widget_trees": widget_trees[:5],
                    },
                    indent=2,
                )[:120000],
            },
        ],
        run_id=run_id,
        phase="qa",
        label="qa",
    )

    if run_id:
        append_activity(
            run_id,
            type="status",
            phase="qa",
            title="QA report generated",
        )

    return {
        "qa_report": qa_report,
        "messages": [{"role": "qa", "content": "QA report generated."}],
    }
