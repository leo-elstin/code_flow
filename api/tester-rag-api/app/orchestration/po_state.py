from typing import Annotated, Any, Literal, TypedDict
import operator

POSessionStatus = Literal[
    "classifying", "brainstorming", "waiting_for_reply",
    "researching", "drafting", "awaiting_review",
    "approved", "rejected", "failed"
]
POSessionMode = Literal["brainstorm", "intake"]


class POSessionState(TypedDict, total=False):
    session_id: str
    mode: POSessionMode
    project_path: str
    project_id: int | None
    initial_context: str
    research_depth: str  # codebase | packages | web
    max_questions: int   # default 5
    questions_asked: int
    readiness_score: float  # 0.0 - 1.0
    conversation: list[dict]   # [{role, content, timestamp}]
    pending_questions: list[str]
    requirements_model: dict   # structured partial requirements
    codebase_context: dict     # discovered codebase context
    status: POSessionStatus
    brief: dict | None
    draft_stories: list[dict] | None
    approved_story_ids: list[str]
    error: str | None


def initial_po_state(
    session_id: str,
    initial_context: str,
    project_path: str,
    project_id: int | None = None,
    research_depth: str = "codebase",
) -> POSessionState:
    return POSessionState(
        session_id=session_id,
        mode="brainstorm",
        project_path=project_path,
        project_id=project_id,
        initial_context=initial_context,
        research_depth=research_depth,
        max_questions=5,
        questions_asked=0,
        readiness_score=0.0,
        conversation=[],
        pending_questions=[],
        requirements_model={
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
        status="classifying",
        brief=None,
        draft_stories=None,
        approved_story_ids=[],
        error=None,
    )
