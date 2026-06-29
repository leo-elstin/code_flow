"""LangGraph StateGraph for the PO Agent pipeline."""

from datetime import datetime, timezone

from langgraph.graph import END, START, StateGraph

from app.agents.roles.po_brainstorm import (
    check_readiness,
    classify_mode,
    enrich_requirements,
    generate_brief,
    generate_questions,
    generate_stories,
)
from app.core.logging_config import get_logger
from app.orchestration.po_state import POSessionState

logger = get_logger("po_graph")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def mode_classifier_node(state: POSessionState) -> dict:
    session_id = state.get("session_id", "")
    logger.info("PO mode_classifier session_id=%s", session_id)
    try:
        result = await classify_mode(
            initial_context=state.get("initial_context", ""),
            run_id=session_id or None,
        )
        return {
            "mode": result["mode"],
            "status": "brainstorming",
        }
    except Exception:
        logger.exception("mode_classifier_node error session_id=%s", session_id)
        raise


async def context_analyzer_node(state: POSessionState) -> dict:
    session_id = state.get("session_id", "")
    project_path = state.get("project_path", "")
    logger.info("PO context_analyzer session_id=%s project_path=%s", session_id, project_path)

    # Minimal codebase context — just record the project path for now.
    # Full discovery would call feature_discovery.discover_context but that
    # requires an active run with embeddings; here we keep it lightweight.
    codebase_context: dict = {
        "project_path": project_path,
        "research_depth": state.get("research_depth", "codebase"),
        "notes": "Context gathered by PO agent.",
    }

    return {
        "codebase_context": codebase_context,
        "status": "brainstorming",
    }


async def question_generator_node(state: POSessionState) -> dict:
    session_id = state.get("session_id", "")
    logger.info("PO question_generator session_id=%s", session_id)
    try:
        result = await generate_questions(
            initial_context=state.get("initial_context", ""),
            conversation=state.get("conversation", []),
            requirements_model=state.get("requirements_model", {}),
            run_id=session_id or None,
        )
        questions = result.get("questions", [])
        pending_questions = questions

        # Build PO message content with numbered questions
        if questions:
            content = "\n".join(f"{i+1}. {q}" for i, q in enumerate(questions))
        else:
            content = "I need a bit more information to continue. Could you share more details?"

        new_message = {
            "role": "po",
            "content": content,
            "questions": questions,
            "timestamp": _now_iso(),
        }

        conversation = list(state.get("conversation", []))
        conversation.append(new_message)

        return {
            "pending_questions": pending_questions,
            "conversation": conversation,
            "status": "waiting_for_reply",
        }
    except Exception:
        logger.exception("question_generator_node error session_id=%s", session_id)
        raise


async def requirements_enricher_node(state: POSessionState) -> dict:
    session_id = state.get("session_id", "")
    logger.info("PO requirements_enricher session_id=%s", session_id)
    conversation = state.get("conversation", [])

    # Get the latest developer message
    developer_reply = ""
    for msg in reversed(conversation):
        if msg.get("role") == "developer":
            developer_reply = msg.get("content", "")
            break

    try:
        updated_model = await enrich_requirements(
            initial_context=state.get("initial_context", ""),
            conversation=conversation,
            requirements_model=state.get("requirements_model", {}),
            developer_reply=developer_reply,
            run_id=session_id or None,
        )
        return {
            "requirements_model": updated_model,
            "questions_asked": state.get("questions_asked", 0) + 1,
        }
    except Exception:
        logger.exception("requirements_enricher_node error session_id=%s", session_id)
        raise


async def readiness_check_node(state: POSessionState) -> dict:
    session_id = state.get("session_id", "")
    logger.info("PO readiness_check session_id=%s", session_id)
    score = await check_readiness(state.get("requirements_model", {}))
    logger.info(
        "PO readiness_check session_id=%s score=%.2f asked=%d max=%d",
        session_id,
        score,
        state.get("questions_asked", 0),
        state.get("max_questions", 5),
    )
    return {"readiness_score": score}


async def brief_generator_node(state: POSessionState) -> dict:
    session_id = state.get("session_id", "")
    logger.info("PO brief_generator session_id=%s", session_id)
    try:
        brief = await generate_brief(
            initial_context=state.get("initial_context", ""),
            conversation=state.get("conversation", []),
            requirements_model=state.get("requirements_model", {}),
            codebase_context=state.get("codebase_context", {}),
            run_id=session_id or None,
        )
        return {
            "brief": brief,
            "status": "drafting",
        }
    except Exception:
        logger.exception("brief_generator_node error session_id=%s", session_id)
        raise


async def story_generator_node(state: POSessionState) -> dict:
    session_id = state.get("session_id", "")
    logger.info("PO story_generator session_id=%s", session_id)
    try:
        stories = await generate_stories(
            initial_context=state.get("initial_context", ""),
            requirements_model=state.get("requirements_model", {}),
            brief=state.get("brief") or {},
            codebase_context=state.get("codebase_context", {}),
            run_id=session_id or None,
        )
        return {
            "draft_stories": stories,
            "status": "awaiting_review",
        }
    except Exception:
        logger.exception("story_generator_node error session_id=%s", session_id)
        raise


def route_after_context(state: POSessionState) -> str:
    if state.get("mode") == "intake":
        return "brief_generator"
    return "question_generator"


def route_after_readiness(state: POSessionState) -> str:
    score = state.get("readiness_score", 0.0)
    asked = state.get("questions_asked", 0)
    max_q = state.get("max_questions", 5)
    if score >= 0.7 or asked >= max_q:
        return "brief_generator"
    return "question_generator"


def build_po_graph() -> StateGraph:
    graph = StateGraph(POSessionState)
    graph.add_node("mode_classifier", mode_classifier_node)
    graph.add_node("context_analyzer", context_analyzer_node)
    graph.add_node("question_generator", question_generator_node)
    graph.add_node("requirements_enricher", requirements_enricher_node)
    graph.add_node("readiness_check", readiness_check_node)
    graph.add_node("brief_generator", brief_generator_node)
    graph.add_node("story_generator", story_generator_node)

    graph.add_edge(START, "mode_classifier")
    graph.add_edge("mode_classifier", "context_analyzer")
    graph.add_conditional_edges(
        "context_analyzer",
        route_after_context,
        {
            "brief_generator": "brief_generator",
            "question_generator": "question_generator",
        },
    )
    # question_generator -> END: pause here, resumed by runner on reply
    graph.add_edge("question_generator", END)
    graph.add_edge("requirements_enricher", "readiness_check")
    graph.add_conditional_edges(
        "readiness_check",
        route_after_readiness,
        {
            "brief_generator": "brief_generator",
            "question_generator": "question_generator",
        },
    )
    graph.add_edge("brief_generator", "story_generator")
    graph.add_edge("story_generator", END)
    return graph
