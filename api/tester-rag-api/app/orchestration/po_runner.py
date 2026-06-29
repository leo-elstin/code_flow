"""Async runner for PO Agent sessions.

Uses direct node function calls (not LangGraph checkpointer) for simplicity.
Human-in-the-loop is implemented by pausing after question_generator and
resuming when submit_reply is called.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from app.core.logging_config import get_logger
from app.orchestration.po_graph import (
    brief_generator_node,
    context_analyzer_node,
    mode_classifier_node,
    question_generator_node,
    readiness_check_node,
    requirements_enricher_node,
    route_after_context,
    route_after_readiness,
    story_generator_node,
)
from app.orchestration.po_state import POSessionState, initial_po_state
from app.services.po_session_store import (
    append_message,
    create_session,
    get_messages,
    get_session,
    list_sessions,
    update_session,
)
from app.services.run_activity import append_activity

logger = get_logger("po_runner")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_from_db(session: dict) -> POSessionState:
    """Reconstruct a POSessionState from the database record + messages."""
    session_id = session["session_id"]
    messages = get_messages(session_id)
    conversation = [
        {
            "role": m["role"],
            "content": m["content"],
            "timestamp": m["created_at"],
        }
        for m in messages
    ]
    return POSessionState(
        session_id=session_id,
        mode=session.get("mode", "brainstorm"),
        project_path=session.get("project_path", ""),
        project_id=session.get("project_id"),
        initial_context=session.get("initial_context", ""),
        research_depth=session.get("research_depth", "codebase"),
        max_questions=5,
        questions_asked=session.get("questions_asked", 0),
        readiness_score=session.get("readiness_score", 0.0),
        conversation=conversation,
        pending_questions=session.get("pending_questions", []),
        requirements_model=session.get("requirements_model") or {
            "problem_statement": None,
            "user_scope": None,
            "success_criteria": [],
            "technical_constraints": [],
            "out_of_scope": [],
            "dependencies": [],
            "priority_hint": None,
            "open_questions": [],
            "coverage": {},
        },
        codebase_context={},
        status=session.get("status", "classifying"),
        brief=session.get("brief"),
        draft_stories=session.get("draft_stories"),
        approved_story_ids=[],
        error=session.get("error"),
    )


class POAgentRunner:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}

    def _task_running(self, session_id: str) -> bool:
        task = self._tasks.get(session_id)
        return task is not None and not task.done()

    def _reap_tasks(self, *, exclude: str | None = None) -> None:
        for sid in [
            s for s, t in self._tasks.items() if t.done() and s != exclude
        ]:
            self._tasks.pop(sid, None)

    # ------------------------------------------------------------------
    # Internal phase runners
    # ------------------------------------------------------------------

    async def _run_initial_phase(self, session_id: str, state: POSessionState) -> None:
        """Run: mode_classifier -> context_analyzer -> (question_generator | brief_generator -> story_generator)."""
        try:
            append_activity(
                session_id,
                type="status",
                phase="po_agent",
                title="Starting PO session",
            )

            state = dict(state)  # type: ignore[assignment]
            updates = await mode_classifier_node(state)  # type: ignore[arg-type]
            state.update(updates)
            update_session(session_id, mode=state["mode"], status=state.get("status", "brainstorming"))

            updates = await context_analyzer_node(state)  # type: ignore[arg-type]
            state.update(updates)

            next_node = route_after_context(state)  # type: ignore[arg-type]
            if next_node == "brief_generator":
                await self._run_research_phase(session_id, state)
            else:
                updates = await question_generator_node(state)  # type: ignore[arg-type]
                state.update(updates)
                update_session(
                    session_id,
                    status=state.get("status", "waiting_for_reply"),
                    pending_questions=state.get("pending_questions", []),
                )
                # Persist last PO message
                conversation = state.get("conversation", [])
                if conversation:
                    last = conversation[-1]
                    if last.get("role") == "po":
                        append_message(session_id, last["role"], last["content"], metadata={"questions": last.get("questions", [])})
        except Exception as exc:
            logger.exception("_run_initial_phase error session_id=%s", session_id)
            update_session(session_id, status="failed", error=str(exc))

    async def _run_reply_phase(
        self, session_id: str, state: POSessionState, developer_reply: str
    ) -> None:
        """Run: requirements_enricher -> readiness_check -> (question_generator | brief_generator -> story_generator)."""
        try:
            append_activity(
                session_id,
                type="status",
                phase="po_agent",
                title="Processing developer reply",
            )

            state = dict(state)  # type: ignore[assignment]
            updates = await requirements_enricher_node(state)  # type: ignore[arg-type]
            state.update(updates)
            update_session(
                session_id,
                requirements_model=state.get("requirements_model", {}),
                questions_asked=state.get("questions_asked", 0),
            )

            updates = await readiness_check_node(state)  # type: ignore[arg-type]
            state.update(updates)
            update_session(session_id, readiness_score=state.get("readiness_score", 0.0))

            next_node = route_after_readiness(state)  # type: ignore[arg-type]
            if next_node == "brief_generator":
                await self._run_research_phase(session_id, state)
            else:
                updates = await question_generator_node(state)  # type: ignore[arg-type]
                state.update(updates)
                update_session(
                    session_id,
                    status=state.get("status", "waiting_for_reply"),
                    pending_questions=state.get("pending_questions", []),
                )
                conversation = state.get("conversation", [])
                if conversation:
                    last = conversation[-1]
                    if last.get("role") == "po":
                        append_message(session_id, last["role"], last["content"], metadata={"questions": last.get("questions", [])})
        except Exception as exc:
            logger.exception("_run_reply_phase error session_id=%s", session_id)
            update_session(session_id, status="failed", error=str(exc))

    async def _run_research_phase(self, session_id: str, state: dict) -> None:
        """Run: brief_generator -> story_generator."""
        try:
            update_session(session_id, status="researching")
            updates = await brief_generator_node(state)  # type: ignore[arg-type]
            state.update(updates)
            update_session(session_id, status="drafting", brief=state.get("brief"))

            updates = await story_generator_node(state)  # type: ignore[arg-type]
            state.update(updates)
            update_session(
                session_id,
                status="awaiting_review",
                draft_stories=state.get("draft_stories", []),
            )
        except Exception as exc:
            logger.exception("_run_research_phase error session_id=%s", session_id)
            update_session(session_id, status="failed", error=str(exc))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start_session(
        self,
        session_id: str,
        initial_context: str,
        project_path: str,
        project_id: int | None = None,
        research_depth: str = "codebase",
    ) -> dict:
        self._reap_tasks()
        create_session(
            session_id=session_id,
            project_id=project_id,
            initial_context=initial_context,
            research_depth=research_depth,
            project_path=project_path,
        )
        state = initial_po_state(
            session_id=session_id,
            initial_context=initial_context,
            project_path=project_path,
            project_id=project_id,
            research_depth=research_depth,
        )
        task = asyncio.create_task(self._run_initial_phase(session_id, state))
        self._tasks[session_id] = task
        return get_session(session_id) or {"session_id": session_id, "status": "classifying"}

    async def submit_reply(self, session_id: str, reply_text: str) -> dict:
        self._reap_tasks()
        session = get_session(session_id)
        if not session:
            raise KeyError(f"Session not found: {session_id}")

        # Store developer message
        append_message(session_id, "developer", reply_text)
        update_session(session_id, status="brainstorming")

        # Reconstruct state
        state = _state_from_db(get_session(session_id))  # type: ignore[arg-type]

        task = asyncio.create_task(
            self._run_reply_phase(session_id, state, reply_text)
        )
        self._tasks[session_id] = task
        return get_session(session_id) or {}

    async def finalize_session(self, session_id: str) -> dict:
        self._reap_tasks()
        session = get_session(session_id)
        if not session:
            raise KeyError(f"Session not found: {session_id}")

        # Force readiness to trigger brief/story generation
        update_session(session_id, readiness_score=1.0)
        state = _state_from_db(get_session(session_id))  # type: ignore[arg-type]
        state = dict(state)  # type: ignore[assignment]
        state["readiness_score"] = 1.0

        task = asyncio.create_task(self._run_research_phase(session_id, state))
        self._tasks[session_id] = task
        return get_session(session_id) or {}

    async def approve_session(self, session_id: str, story_ids: list[str]) -> dict:
        session = get_session(session_id)
        if not session:
            raise KeyError(f"Session not found: {session_id}")
        update_session(session_id, status="approved")
        return get_session(session_id) or {}

    async def reject_session(self, session_id: str) -> dict:
        session = get_session(session_id)
        if not session:
            raise KeyError(f"Session not found: {session_id}")
        update_session(session_id, status="rejected")
        return get_session(session_id) or {}

    async def get_state(self, session_id: str) -> dict | None:
        return get_session(session_id)

    async def list_sessions_for_project(
        self, project_id: int | None = None, limit: int = 50
    ) -> list[dict]:
        return list_sessions(project_id=project_id, limit=limit)


# Singleton runner instance
po_runner = POAgentRunner()
