import base64
import json
from pathlib import Path
from typing import Any

from app.agents.roles.explorer import run_explorer
from app.core.config import settings
from app.core.logging_config import get_logger
from app.services.feature_discovery import discover_context
from app.services.generation import chat_completion_json
from app.services.run_activity import append_activity
from app.tools.grep import read_file as _read_file

logger = get_logger("planner")


PLANNER_SYSTEM = """You are a senior Flutter architect planning a feature implementation.
Return JSON only with keys:
- feature_summary (string)
- reasoning (string, optional) — brief chain-of-thought explaining discovery and plan choices
- questions (list, default []) — clarification questions when a design decision is genuinely ambiguous (see rules below)
- files_to_create (list of {path, purpose}) — new files the dev agent must write
- files_to_modify (list of {path, purpose}) — existing files the dev agent must edit
- context_files (list of relative paths to read for reference; do not list these under files_to_modify unless they must change)
- architecture (object with layers, state_management, routing notes)
- acceptance_criteria (list of testable strings)
- discovery_evidence (list of short strings citing grep/file evidence)
- plan_markdown (string) — a comprehensive markdown document (see PLAN_MARKDOWN below)

PLAN_MARKDOWN — generate a rich markdown document with these sections (when questions is non-empty, only emit a brief "Awaiting clarification" placeholder and skip the detailed sections):
```
# <Feature title>

## Summary
<1-2 sentence description of what will be built and why>

## Current State
<What exists today — key files, patterns, gaps discovered. Use bullet points. Reference actual file paths found.>

## Architecture
<How the change fits into the existing architecture. Include a mermaid flowchart if routing/data flow is involved.>

## File Plan
<Directory tree of files to create/modify, with a one-line purpose per file. Use a fenced code block for the tree.>

### Files to Create
<For each new file: path, purpose, key classes/functions>

### Files to Modify
<For each modified file: path, what changes and why>

## Test Coverage
<Which test files to create/update, what scenarios to cover>

## Out of Scope
<Anything explicitly excluded, with a reason>

## Verification Checklist
<Bullet list of testable checks the reviewer can run to confirm the implementation is correct>
```

CLARIFICATION QUESTIONS — emit only when genuinely needed:
- questions format: [{id: "q0", question: "...", context: "...(relevant codebase evidence)...", options: [{id: "A", label: "...", description: "..."}]}]
- Emit a question ONLY when: (a) there are 2+ technically valid approaches that lead to different files or dependencies, AND (b) codebase evidence does not definitively resolve which to use.
- Do NOT ask about things clearly specified in the ticket or obvious from the codebase.
- Each question must have 2–4 options. Put the most project-idiomatic option first.
- When questions are present, the plan fields (files_to_create, files_to_modify, etc.) may be empty — a final plan will be produced after the user answers.
- When clarification_answers are provided in the prompt, do NOT emit questions — produce the full implementation plan using those answers.

CRITICAL — dependency hygiene:
- The discovery context includes manifest_summaries (pubspec.yaml, package.json, etc.) showing every declared dependency.
- NEVER propose using a package or SDK that is not listed in those manifests. If two approaches exist and one requires a new package while the other uses existing code, ALWAYS prefer the existing-code approach.
- If a term in the ticket (e.g. "remote config", "feature flag") could refer to either an existing in-house service OR a third-party SDK, check the manifest first. Absence of the SDK in the manifest means use the in-house service.
- Only propose adding a new dependency when the ticket explicitly requests it AND no existing code can fulfil the same role.

CRITICAL — files_to_modify completeness (the dev agent is BLOCKED from editing any file not in this list):
- Every file that will be touched to implement the feature MUST appear in files_to_modify, no exceptions.
- If you describe an action in feature_summary, reasoning, or architecture (e.g. "update routing", "register the service", "wire the module", "add the import", "configure the provider"), the specific file where that action happens MUST be in files_to_modify.
- Integration and wiring files are NOT context_files — if an existing file needs even one line changed (routing tables, DI registration, barrel exports, app entry points, navigation guards), it belongs in files_to_modify.
- Every acceptance criterion must be traceable to at least one file in files_to_create or files_to_modify. If you cannot point to a file that implements a criterion, either add the file or drop the criterion.
- After drafting the list, re-read each criterion and each action described in the plan and verify the responsible file is present. If it is missing, add it.

CRITICAL — dependency injection registration:
- Whenever files_to_create contains a class annotated with @injectable, @lazySingleton, @singleton, or any GetIt-registered type, the DI registration file (typically lib/core/di/dependency_injector.dart or equivalent found in AGENTS.md) MUST be in files_to_modify.
- This applies even when the class is self-registering via build_runner codegen — the manual registration call must still be added to the DI setup file.
- If you are unsure which file registers dependencies, search the discovery context for "di.register" or "GetIt" and use that file.

Put read-only reference files in context_files only.
The PROJECT ARCHITECTURE GUIDE (AGENTS.md) in the user message is the single source of truth for folder layout, naming, DI patterns, state management, routing, and which services to use. Follow it exactly. Do not invent paths, patterns, or dependencies not described there.
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
    clarification_answers: list[dict[str, Any]] | None = None,
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

    # Agentic exploration: drive a read-only tool loop to locate the concrete
    # files this ticket needs, then read them into the discovery context so the
    # planner can produce a file-accurate plan instead of asking "which files?".
    explorer_findings: dict[str, Any] = {}
    if settings.CODE_AGENT_AGENTIC_DISCOVERY:
        try:
            explorer_findings = await run_explorer(
                user_request, project_path, run_id=run_id,
                seed_terms=context_bundle.get("search_terms"),
                seed_files=context_bundle.get("files"),
            )
        except Exception:  # noqa: BLE001 — fall back to plain discovery
            logger.warning("Agentic exploration failed; using plain discovery", exc_info=True)
    relevant_files = [
        rf for rf in (explorer_findings.get("relevant_files") or []) if isinstance(rf, dict)
    ]
    if relevant_files:
        known = {fs.get("path") for fs in context_payload["file_summaries"]}
        extra: list[dict[str, Any]] = []
        for rf in relevant_files[: settings.CODE_AGENT_EXPLORER_MAX_FILES]:
            rel = rf.get("path")
            if not rel or rel in known:
                continue
            try:
                content = _read_file(project_path, rel, max_chars=6000)
            except Exception:  # noqa: BLE001
                continue
            extra.append({"path": rel, "preview": content[:1500], "lines": content.count("\n") + 1})
            known.add(rel)
        # Explorer files are the highest-signal — put them first, then re-cap.
        context_payload["file_summaries"] = (extra + context_payload["file_summaries"])[:25]
        context_payload["files"] = sorted(
            {*context_payload.get("files", []), *[e["path"] for e in extra]}
        )

    manifest_summaries = context_bundle.get("manifest_summaries", [])
    if manifest_summaries:
        context_payload["manifest_summaries"] = manifest_summaries
    context_text = json.dumps(context_payload, indent=2)

    text_content = (
        f"User request:\n{user_request}\n\n"
        f"Project path: {project_path}\n\n"
    )

    # Inject AGENTS.md as a top-level, explicitly authoritative section so the
    # LLM treats it as the primary architecture reference rather than just
    # another entry buried in the JSON context blob.
    if guide.get("found") and guide.get("content"):
        text_content += (
            "=== PROJECT ARCHITECTURE GUIDE (AGENTS.md) — authoritative reference ===\n"
            "Read this before making any decisions about folder layout, naming, state management, "
            "dependency injection, routing, or which services/packages to use. "
            "All plan decisions MUST conform to the conventions described here.\n\n"
            + guide["content"]
            + "\n=== END AGENTS.md ===\n\n"
        )

    if linked_issues_context:
        text_content += f"Linked Jira issues:\n{linked_issues_context}\n\n"
    if acceptance_criteria_hint:
        text_content += "Jira acceptance criteria (use as starting point):\n"
        text_content += "\n".join(f"- {ac}" for ac in acceptance_criteria_hint)
        text_content += "\n\n"
    if relevant_files:
        text_content += (
            "=== AGENTIC EXPLORATION FINDINGS (files located by reading the codebase — authoritative) ===\n"
            "These files were found by actually searching and reading the repo. Use them to populate "
            "files_to_create / files_to_modify with concrete paths. Do NOT ask a clarification question "
            "about which files to target or which implementation surface to use when these findings "
            "identify them — proceed with the plan.\n"
            + json.dumps(explorer_findings, indent=2)
            + "\n=== END EXPLORATION FINDINGS ===\n\n"
        )
    text_content += f"Discovery context:\n{context_text}\n\n"
    if clarification_answers:
        text_content += "Clarification answers from the developer (incorporate these into the plan, do NOT emit questions):\n"
        for ans in clarification_answers:
            q = ans.get("question", "")
            label = ans.get("option_label", "")
            desc = ans.get("option_description", "")
            text_content += f"- Q: {q}\n  A: {label}"
            if desc:
                text_content += f" — {desc}"
            text_content += "\n"
        text_content += "\nProduce a full implementation plan JSON (no questions field)."
    else:
        text_content += "Produce an implementation plan JSON."

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

    questions = plan.get("questions") or []
    if not isinstance(questions, list):
        questions = []

    acceptance = plan.get("acceptance_criteria") or []
    if isinstance(acceptance, str):
        acceptance = [acceptance]

    if run_id:
        if questions:
            append_activity(
                run_id,
                type="status",
                phase="planner",
                title="Planner has clarification questions",
                detail=f"{len(questions)} question(s) before finalising plan",
            )
        else:
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
        "questions": questions,
        "acceptance_criteria": acceptance,
        "messages": [
            {
                "role": "planner",
                "content": plan.get("feature_summary", "Plan created."),
            }
        ],
    }
