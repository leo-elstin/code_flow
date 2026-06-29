import base64
import json
from pathlib import Path
from typing import Any

from app.services.feature_discovery import discover_context
from app.services.generation import chat_completion_json
from app.services.run_activity import append_activity


PLANNER_SYSTEM = """You are a senior Flutter architect planning a feature implementation.
Return JSON only with keys:
- feature_summary (string)
- reasoning (string, optional) — brief chain-of-thought explaining discovery and plan choices
- files_to_create (list of {path, purpose}) — new files the dev agent must write
- files_to_modify (list of {path, purpose}) — ONLY files the dev agent must actually edit
- context_files (list of relative paths to read for reference; do not list these under files_to_modify unless they must change)
- architecture (object with layers, state_management, routing notes)
- acceptance_criteria (list of testable strings)
- discovery_evidence (list of short strings citing grep/file evidence)
Keep files_to_modify minimal. Put read-only reference files in context_files only.
When project_guide is present, follow its folder layout and naming conventions exactly.
Do not invent paths like lib/controllers/, lib/data/, or lib/repositories/ unless the guide says so.
"""


def _encode_image_base64(file_path: str) -> str | None:
    """Read an image file and return its base64-encoded data URI."""
    path = Path(file_path)
    if not path.exists():
        return None
    suffix = path.suffix.lower()
    mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
    mime = mime_map.get(suffix, "image/png")
    data = path.read_bytes()
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


async def run_planner(
    user_request: str,
    project_path: str,
    *,
    run_id: str | None = None,
    attachment_paths: list[str] | None = None,
    linked_issues_context: str | None = None,
    acceptance_criteria_hint: list[str] | None = None,
) -> dict[str, Any]:
    if run_id:
        append_activity(
            run_id,
            type="status",
            phase="planner",
            title="Planning started",
            detail=user_request[:200],
        )

    context_bundle = discover_context(user_request, project_path, run_id=run_id)

    guide = context_bundle.get("project_guide") or {}
    context_payload: dict[str, Any] = {
        "search_terms": context_bundle.get("search_terms", []),
        "grep_matches": context_bundle.get("grep_matches", [])[:30],
        "symbols": context_bundle.get("symbols", [])[:25],
        "lsp_hits": context_bundle.get("lsp_hits", [])[:20],
        "files": context_bundle.get("files", [])[:30],
        "file_summaries": context_bundle.get("file_summaries", [])[:15],
    }
    if guide.get("found"):
        context_payload["project_guide"] = guide.get("content", "")
    context_text = json.dumps(context_payload, indent=2)

    text_content = (
        f"User request:\n{user_request}\n\n"
        f"Project path: {project_path}\n\n"
    )
    if linked_issues_context:
        text_content += f"Linked Jira issues:\n{linked_issues_context}\n\n"
    if acceptance_criteria_hint:
        text_content += "Jira acceptance criteria (use as starting point):\n"
        text_content += "\n".join(f"- {ac}" for ac in acceptance_criteria_hint)
        text_content += "\n\n"
    text_content += f"Discovery context:\n{context_text}\n\nProduce an implementation plan JSON."

    # Build multimodal message if image attachments are present
    if attachment_paths:
        content_parts: list[dict[str, Any]] = [{"type": "text", "text": text_content}]
        for img_path in attachment_paths:
            data_uri = _encode_image_base64(img_path)
            if data_uri:
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": data_uri},
                })
        user_message: dict[str, Any] = {"role": "user", "content": content_parts}
    else:
        user_message = {"role": "user", "content": text_content}

    plan, _usage = await chat_completion_json(
        messages=[
            {"role": "system", "content": PLANNER_SYSTEM},
            user_message,
        ],
        run_id=run_id,
        phase="planner",
        label="planner",
    )

    reasoning = plan.get("reasoning")
    if run_id and reasoning:
        append_activity(
            run_id,
            type="thinking",
            phase="planner",
            title="Planner reasoning",
            detail=str(reasoning)[:4000],
        )

    acceptance = plan.get("acceptance_criteria") or []
    if isinstance(acceptance, str):
        acceptance = [acceptance]

    if run_id:
        append_activity(
            run_id,
            type="status",
            phase="planner",
            title="Plan ready for approval",
            detail=plan.get("feature_summary", "Plan created."),
        )

    return {
        "context_bundle": context_bundle,
        "plan": plan,
        "acceptance_criteria": acceptance,
        "messages": [
            {
                "role": "planner",
                "content": plan.get("feature_summary", "Plan created."),
            }
        ],
    }
