"""FastAPI router for the PO Agent API."""

import uuid

from fastapi import APIRouter, HTTPException, Query

from app.api.po.schemas import (
    ApproveRequest,
    ReplyRequest,
    SessionListResponse,
    SessionResponse,
    StartSessionRequest,
    StartSessionResponse,
)
from app.core.logging_config import get_logger
from app.orchestration.po_runner import po_runner

router = APIRouter()
logger = get_logger("po_api")


def _to_session_response(session: dict) -> SessionResponse:
    return SessionResponse(
        session_id=session.get("session_id", ""),
        mode=session.get("mode", "brainstorm"),
        status=session.get("status", "classifying"),
        initial_context=session.get("initial_context", ""),
        questions_asked=session.get("questions_asked", 0),
        readiness_score=session.get("readiness_score", 0.0),
        pending_questions=session.get("pending_questions") or [],
        requirements_model=session.get("requirements_model"),
        brief=session.get("brief"),
        draft_stories=session.get("draft_stories"),
        error=session.get("error"),
        created_at=session.get("created_at"),
        updated_at=session.get("updated_at"),
    )


@router.post("/sessions", response_model=StartSessionResponse)
async def start_session(body: StartSessionRequest):
    session_id = str(uuid.uuid4())
    try:
        result = await po_runner.start_session(
            session_id=session_id,
            initial_context=body.initial_context,
            project_path=body.project_path,
            project_id=body.project_id,
            research_depth=body.research_depth,
        )
        return StartSessionResponse(
            session_id=result.get("session_id", session_id),
            status=result.get("status", "classifying"),
        )
    except Exception as exc:
        logger.exception("start_session error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(project_id: int | None = Query(default=None)):
    try:
        sessions = await po_runner.list_sessions_for_project(project_id=project_id)
        return SessionListResponse(
            sessions=[_to_session_response(s) for s in sessions],
            total=len(sessions),
        )
    except Exception as exc:
        logger.exception("list_sessions error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str):
    session = await po_runner.get_state(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    return _to_session_response(session)


@router.post("/sessions/{session_id}/reply", response_model=SessionResponse)
async def submit_reply(session_id: str, body: ReplyRequest):
    try:
        result = await po_runner.submit_reply(session_id=session_id, reply_text=body.content)
        return _to_session_response(result)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("submit_reply error session_id=%s", session_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/finalize", response_model=SessionResponse)
async def finalize_session(session_id: str):
    try:
        result = await po_runner.finalize_session(session_id=session_id)
        return _to_session_response(result)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("finalize_session error session_id=%s", session_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/approve", response_model=SessionResponse)
async def approve_session(session_id: str, body: ApproveRequest):
    try:
        result = await po_runner.approve_session(
            session_id=session_id, story_ids=body.story_ids
        )
        return _to_session_response(result)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("approve_session error session_id=%s", session_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/reject", response_model=SessionResponse)
async def reject_session(session_id: str):
    try:
        result = await po_runner.reject_session(session_id=session_id)
        return _to_session_response(result)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("reject_session error session_id=%s", session_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
