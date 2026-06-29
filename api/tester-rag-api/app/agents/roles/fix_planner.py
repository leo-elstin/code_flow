import json
from typing import Any

from app.services.generation import chat_completion_json

FIX_PLANNER_SYSTEM = """You are a senior Flutter developer triaging dart/flutter analyze errors.
Return JSON only with keys:
- summary (string) — one paragraph overview of what is broken
- required_fixes (list of strings) — concrete fix descriptions tied to analyze errors
- steps (list of strings) — ordered implementation steps for the dev agent
- files_to_focus (list of relative paths) — lib files that must be edited
Prioritize minimal, targeted fixes. Do not suggest rewriting unrelated files.
"""


async def plan_analyze_fixes(
    *,
    analyze_result: dict[str, Any],
    plan: dict[str, Any],
    user_request: str,
    previous_verifier_report: dict[str, Any] | None,
    loop: int,
    max_loops: int,
    run_id: str | None = None,
) -> dict[str, Any]:
    payload = {
        "loop": loop,
        "max_loops": max_loops,
        "user_request": user_request,
        "analyze": {
            "error_count": analyze_result.get("error_count"),
            "error_lines": analyze_result.get("error_lines", [])[:25],
            "stdout": (analyze_result.get("stdout") or "")[-4000:],
            "stderr": (analyze_result.get("stderr") or "")[-2000:],
        },
        "plan_summary": plan.get("feature_summary"),
        "files_to_modify": plan.get("files_to_modify", [])[:12],
        "files_to_create": plan.get("files_to_create", [])[:12],
        "previous_verifier": previous_verifier_report or {},
    }

    fix_plan, _usage = await chat_completion_json(
        messages=[
            {"role": "system", "content": FIX_PLANNER_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Manual retry loop {loop}/{max_loops}.\n\n"
                    f"Triage payload:\n{json.dumps(payload, indent=2)}\n\n"
                    "Produce a fix plan JSON for the dev agent."
                ),
            },
        ],
        run_id=run_id,
        phase="dev",
        label="fix_planner",
    )
    if not isinstance(fix_plan.get("required_fixes"), list):
        fix_plan["required_fixes"] = []
    if not isinstance(fix_plan.get("steps"), list):
        fix_plan["steps"] = []
    if not isinstance(fix_plan.get("files_to_focus"), list):
        fix_plan["files_to_focus"] = []
    return fix_plan
