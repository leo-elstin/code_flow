"""PO brainstorm agent: LLM helpers for the PO Agent pipeline."""

import json
from typing import Any

from app.core.logging_config import get_logger
from app.services.generation import chat_completion_json
from app.services.run_activity import append_activity

logger = get_logger("po_agent")


async def classify_mode(
    initial_context: str,
    run_id: str | None = None,
) -> dict:
    """Classify whether the input is 'brainstorm' (vague) or 'intake' (formed request).

    Returns {"mode": "brainstorm"|"intake", "reasoning": str}
    """
    if run_id:
        append_activity(
            run_id,
            type="status",
            phase="po_agent",
            title="Classifying session mode",
        )

    messages = [
        {
            "role": "system",
            "content": (
                "You are a Product Owner assistant. Classify the developer's input as either "
                "'brainstorm' (vague, exploratory, incomplete idea) or 'intake' (formed feature "
                "request with clear problem, scope, and intent). "
                "Return JSON: {\"mode\": \"brainstorm\"|\"intake\", \"reasoning\": string}"
            ),
        },
        {
            "role": "user",
            "content": f"Developer input:\n{initial_context}",
        },
    ]

    result, _ = await chat_completion_json(
        messages=messages,
        run_id=run_id,
        phase="po_agent",
        label="classify_mode",
    )
    mode = result.get("mode", "brainstorm")
    if mode not in ("brainstorm", "intake"):
        mode = "brainstorm"
    return {"mode": mode, "reasoning": result.get("reasoning", "")}


async def generate_questions(
    initial_context: str,
    conversation: list[dict],
    requirements_model: dict,
    run_id: str | None = None,
) -> dict:
    """Generate 2-3 targeted clarifying questions.

    Returns {"questions": [str], "reasoning": str, "categories_covered": [str]}
    """
    if run_id:
        append_activity(
            run_id,
            type="status",
            phase="po_agent",
            title="Generating clarifying questions",
        )

    conv_text = "\n".join(
        f"{m.get('role', 'unknown').upper()}: {m.get('content', '')}"
        for m in conversation
    )
    req_summary = json.dumps(requirements_model, indent=2)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a Product Owner helping a developer clarify requirements. "
                "Based on the conversation so far and what's still unknown in the requirements model, "
                "generate 2-3 focused questions. Prioritise: user scope, success criteria, "
                "technical constraints, scope boundaries. Never repeat already-covered categories. "
                "Return JSON: {\"questions\": [string], \"reasoning\": string, \"categories_covered\": [string]}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Initial context:\n{initial_context}\n\n"
                f"Conversation so far:\n{conv_text or '(none yet)'}\n\n"
                f"Current requirements model:\n{req_summary}"
            ),
        },
    ]

    result, _ = await chat_completion_json(
        messages=messages,
        run_id=run_id,
        phase="po_agent",
        label="generate_questions",
    )
    return {
        "questions": result.get("questions", []),
        "reasoning": result.get("reasoning", ""),
        "categories_covered": result.get("categories_covered", []),
    }


async def enrich_requirements(
    initial_context: str,
    conversation: list[dict],
    requirements_model: dict,
    developer_reply: str,
    run_id: str | None = None,
) -> dict:
    """Update the requirements model from the developer's reply.

    Returns updated requirements_model dict with coverage scores updated.
    """
    if run_id:
        append_activity(
            run_id,
            type="status",
            phase="po_agent",
            title="Enriching requirements model from reply",
        )

    conv_text = "\n".join(
        f"{m.get('role', 'unknown').upper()}: {m.get('content', '')}"
        for m in conversation
    )
    req_summary = json.dumps(requirements_model, indent=2)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a Product Owner assistant. Update the requirements model by extracting "
                "information from the developer's reply. Fill in any fields that can be inferred. "
                "Update the coverage dict with scores 0.0-1.0 for each category: "
                "problem_statement, user_scope, success_criteria, technical_constraints, "
                "out_of_scope, dependencies. "
                "Return the complete updated requirements model as JSON matching the same structure."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Initial context:\n{initial_context}\n\n"
                f"Conversation so far:\n{conv_text or '(none yet)'}\n\n"
                f"Developer's latest reply:\n{developer_reply}\n\n"
                f"Current requirements model:\n{req_summary}\n\n"
                "Return the updated requirements model JSON."
            ),
        },
    ]

    result, _ = await chat_completion_json(
        messages=messages,
        run_id=run_id,
        phase="po_agent",
        label="enrich_requirements",
    )

    # Merge result back, preserving structure
    updated = dict(requirements_model)
    for key in (
        "problem_statement", "user_scope", "success_criteria",
        "technical_constraints", "out_of_scope", "dependencies",
        "priority_hint", "open_questions", "coverage",
    ):
        if key in result:
            updated[key] = result[key]
    return updated


async def check_readiness(requirements_model: dict) -> float:
    """Score requirements completeness. Returns float 0.0-1.0."""
    categories = [
        "problem_statement",
        "user_scope",
        "success_criteria",
        "technical_constraints",
        "out_of_scope",
        "dependencies",
    ]
    filled = 0
    for cat in categories:
        val = requirements_model.get(cat)
        if val:
            # Non-empty string, non-empty list, or truthy value
            if isinstance(val, list):
                if len(val) > 0:
                    filled += 1
            else:
                filled += 1
    return filled / len(categories)


async def generate_brief(
    initial_context: str,
    conversation: list[dict],
    requirements_model: dict,
    codebase_context: dict,
    run_id: str | None = None,
) -> dict:
    """Generate a mini-PRD brief.

    Returns {summary, approach, out_of_scope, open_questions, sources}
    """
    if run_id:
        append_activity(
            run_id,
            type="status",
            phase="po_agent",
            title="Generating product brief",
        )

    conv_text = "\n".join(
        f"{m.get('role', 'unknown').upper()}: {m.get('content', '')}"
        for m in conversation
    )
    req_summary = json.dumps(requirements_model, indent=2)
    codebase_summary = json.dumps(codebase_context, indent=2)[:2000]  # cap size

    messages = [
        {
            "role": "system",
            "content": (
                "You are a Product Owner writing a mini-PRD brief. Based on the requirements "
                "gathered through conversation, produce a clear, concise brief. "
                "Return JSON: {\"summary\": string, \"approach\": string, "
                "\"out_of_scope\": [string], \"open_questions\": [string], \"sources\": [string]}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Initial context:\n{initial_context}\n\n"
                f"Conversation:\n{conv_text or '(none)'}\n\n"
                f"Requirements model:\n{req_summary}\n\n"
                f"Codebase context:\n{codebase_summary}"
            ),
        },
    ]

    result, _ = await chat_completion_json(
        messages=messages,
        run_id=run_id,
        phase="po_agent",
        label="generate_brief",
    )
    return {
        "summary": result.get("summary", ""),
        "approach": result.get("approach", ""),
        "out_of_scope": result.get("out_of_scope", []),
        "open_questions": result.get("open_questions", []),
        "sources": result.get("sources", []),
    }


async def generate_stories(
    initial_context: str,
    requirements_model: dict,
    brief: dict,
    codebase_context: dict,
    run_id: str | None = None,
) -> list[dict]:
    """Generate user stories from the brief.

    Each story: {epic, title, story, description, acceptance_criteria,
                 effort, priority, depends_on, grounding}
    """
    if run_id:
        append_activity(
            run_id,
            type="status",
            phase="po_agent",
            title="Generating draft user stories",
        )

    req_summary = json.dumps(requirements_model, indent=2)
    brief_text = json.dumps(brief, indent=2)
    codebase_summary = json.dumps(codebase_context, indent=2)[:2000]

    messages = [
        {
            "role": "system",
            "content": (
                "You are a Product Owner writing user stories. Based on the brief and requirements, "
                "produce a list of user stories. Each story must have: "
                "id (unique string like 'US-001'), epic, title, story (as a user story: 'As a ... I want ... so that ...'), "
                "description, acceptance_criteria (list of strings), effort (XS/S/M/L/XL), "
                "priority (critical/high/medium/low), depends_on (list of story ids), grounding (list of code files/modules). "
                "Return JSON: {\"stories\": [story objects]}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Initial context:\n{initial_context}\n\n"
                f"Requirements model:\n{req_summary}\n\n"
                f"Brief:\n{brief_text}\n\n"
                f"Codebase context:\n{codebase_summary}"
            ),
        },
    ]

    result, _ = await chat_completion_json(
        messages=messages,
        run_id=run_id,
        phase="po_agent",
        label="generate_stories",
    )
    stories = result.get("stories", [])
    # Ensure each story has an id
    for i, story in enumerate(stories):
        if not story.get("id"):
            story["id"] = f"US-{i+1:03d}"
    return stories
