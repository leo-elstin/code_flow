from pydantic import BaseModel, Field
from typing import Any


class StartSessionRequest(BaseModel):
    initial_context: str
    project_id: int | None = None
    project_path: str = ""
    research_depth: str = "codebase"


class StartSessionResponse(BaseModel):
    session_id: str
    status: str


class ReplyRequest(BaseModel):
    content: str


class ApproveRequest(BaseModel):
    story_ids: list[str]


class MessageResponse(BaseModel):
    id: int | None = None
    session_id: str
    role: str
    content: str
    created_at: str | None = None


class SessionResponse(BaseModel):
    session_id: str
    mode: str
    status: str
    initial_context: str
    questions_asked: int = 0
    readiness_score: float = 0.0
    pending_questions: list[str] = Field(default_factory=list)
    requirements_model: dict[str, Any] | None = None
    brief: dict[str, Any] | None = None
    draft_stories: list[dict[str, Any]] | None = None
    error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class SessionListResponse(BaseModel):
    sessions: list[SessionResponse]
    total: int
