import asyncio
import json
import os

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.code_agent.schemas import (
    ActivityEventResponse,
    ActivityListResponse,
    ApproveRunRequest,
    ClarifyRunRequest,
    CurrentActionResponse,
    MergeRunRequest,
    MergeRunResponse,
    MergePreview,
    RejectRunRequest,
    ExecutionListResponse,
    ExecutionSummaryResponse,
    RunListResponse,
    RunStatusResponse,
    RunSummaryResponse,
    StartRunRequest,
    StartRunResponse,
    StartEpicRunRequest,
    StartEpicRunResponse,
    ApproveEpicRunRequest,
    EpicChildRun,
    EpicRunResponse,
    EpicRunListResponse,
    TokenUsageResponse,
)
from app.services.run_activity import (
    activity_snapshot,
    get_current_action,
    get_token_totals,
)
from app.core.logging_config import get_logger
from app.orchestration.runner import GitMergeConflictError, runner
from app.orchestration.epic_runner import epic_runner
from app.services.merge_worktree import MergeValidationError
from app.services.project_ticket_store import get_project, get_ticket
from app.services.worktree import resolve_worktree_path
from app.tools.git_tools import GitMergeError, get_checkout_branch

router = APIRouter()
logger = get_logger("api")


def _to_response(state: dict) -> RunStatusResponse:
    project_path = state.get("project_path")
    worktree_path = state.get("worktree_path")
    run_id = state.get("run_id", "")
    if project_path and worktree_path:
        worktree_path = resolve_worktree_path(
            project_path,
            run_id=run_id,
            worktree_path=worktree_path,
        )

    merge_preview = None
    status = state.get("status", "unknown")
    workspace_mode = state.get("workspace_mode") or "worktree"
    if status == "completed" and project_path and workspace_mode == "worktree":
        try:
            merge_preview = MergePreview(target_branch=get_checkout_branch(project_path))
        except GitMergeError:
            merge_preview = None

    current_action_raw = get_current_action(run_id) if run_id else None
    token_usage_raw = get_token_totals(run_id) if run_id else None
    current_action = (
        CurrentActionResponse(**current_action_raw) if current_action_raw else None
    )
    token_usage = (
        TokenUsageResponse(**token_usage_raw)
        if token_usage_raw and token_usage_raw.get("total_tokens", 0) > 0
        else None
    )

    return RunStatusResponse(
        run_id=run_id,
        root_run_id=state.get("root_run_id") or run_id,
        parent_run_id=state.get("parent_run_id"),
        attempt=state.get("attempt") or 1,
        status=status,
        user_request=state.get("user_request"),
        project_path=project_path,
        ticket_id=state.get("ticket_id"),
        worktree_path=worktree_path,
        workspace_mode=workspace_mode,
        iteration=state.get("iteration"),
        plan=state.get("plan") or None,
        acceptance_criteria=state.get("acceptance_criteria") or None,
        clarification_questions=state.get("clarification_questions") or None,
        context_bundle=state.get("context_bundle") or None,
        file_changes=state.get("file_changes") or None,
        diffs=state.get("diffs") or None,
        verifier_report=state.get("verifier_report") or None,
        qa_report=state.get("qa_report") or None,
        messages=state.get("messages") or None,
        error=state.get("error"),
        merge_report=state.get("merge_report") or None,
        merge_preview=merge_preview,
        truncated=bool(state.get("truncated", False)),
        dev_sdk_session_id=state.get("dev_sdk_session_id"),
        is_running=runner._task_running(run_id),
        current_action=current_action,
        token_usage=token_usage,
    )


def _merge_response(report: dict) -> MergeRunResponse:
    return MergeRunResponse(
        applied=bool(report.get("applied")),
        target_branch=report.get("target_branch"),
        agent_branch=report.get("agent_branch"),
        conflict_files=report.get("conflict_files") or [],
        error=report.get("error"),
        merged_at=report.get("merged_at"),
    )


@router.post("/run", response_model=StartRunResponse, status_code=202)
async def start_run(body: StartRunRequest):
    logger.info("POST /run project_path=%s", body.project_path)
    if body.ticket_id is not None:
        ticket = get_ticket(body.ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        project = get_project(ticket["project_id"])
        if not project or project["path"] != os.path.abspath(body.project_path):
            raise HTTPException(status_code=400, detail="Ticket does not belong to the given project")
    try:
        run_id = await runner.start_run(
            body.request, body.project_path, ticket_id=body.ticket_id,
            auto_approve=body.auto_approve,
        )
        logger.info("POST /run success run_id=%s auto_approve=%s", run_id, body.auto_approve)
        return StartRunResponse(run_id=run_id, status="planning")
    except Exception:
        logger.exception("POST /run error project_path=%s", body.project_path)
        raise


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    project_path: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    logger.info("GET /runs project_path=%s limit=%d", project_path, limit)
    try:
        runs = await runner.list_runs(project_path=project_path, limit=limit)
        summaries = [RunSummaryResponse(**row) for row in runs]
        return RunListResponse(runs=summaries, total=len(summaries))
    except Exception:
        logger.exception("GET /runs error")
        raise


@router.get("/runs/{run_id}", response_model=RunStatusResponse)
async def get_run(run_id: str):
    logger.info("GET /runs/%s", run_id)
    try:
        state = await runner.get_state(run_id)
        if not state:
            logger.warning("GET /runs/%s not found", run_id)
            raise HTTPException(status_code=404, detail="Run not found")
        logger.info("GET /runs/%s success status=%s", run_id, state.get("status"))
        return _to_response(state)
    except HTTPException:
        raise
    except Exception:
        logger.exception("GET /runs/%s error", run_id)
        raise


@router.get("/runs/{run_id}/activity", response_model=ActivityListResponse)
async def get_run_activity(
    run_id: str,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
):
    logger.info("GET /runs/%s/activity after_seq=%d", run_id, after_seq)
    state = await runner.get_state(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Run not found")

    snapshot = activity_snapshot(run_id, after_seq=after_seq)
    events = snapshot["events"][:limit]
    current = snapshot.get("current_action")
    tokens = snapshot.get("token_usage") or {}

    return ActivityListResponse(
        events=[ActivityEventResponse(**event) for event in events],
        current_action=CurrentActionResponse(**current) if current else None,
        token_usage=TokenUsageResponse(**tokens),
    )


@router.get("/runs/{run_id}/executions", response_model=ExecutionListResponse)
async def list_run_executions(run_id: str):
    """All executions (the original run plus every retry/rerun) that share a root,
    each with its own isolated token usage."""
    logger.info("GET /runs/%s/executions", run_id)
    state = await runner.get_state(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Run not found")

    executions = await runner.list_executions(run_id)
    root_run_id = state.get("root_run_id") or run_id
    summaries = [
        ExecutionSummaryResponse(
            run_id=row["run_id"],
            attempt=int(row.get("attempt") or 1),
            status=row.get("status", "unknown"),
            error=row.get("error"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            parent_run_id=row.get("parent_run_id"),
            root_run_id=row.get("root_run_id") or root_run_id,
            token_usage=TokenUsageResponse(**(row.get("token_usage") or {})),
        )
        for row in executions
    ]
    return ExecutionListResponse(
        root_run_id=root_run_id,
        executions=summaries,
        total=len(summaries),
    )


@router.post("/runs/{run_id}/clarify", response_model=RunStatusResponse, status_code=202)
async def clarify_run(run_id: str, body: ClarifyRunRequest):
    logger.info("POST /runs/%s/clarify answers=%d", run_id, len(body.answers))
    try:
        state = await runner.get_state(run_id)
        if not state:
            logger.warning("POST /runs/%s/clarify not found", run_id)
            raise HTTPException(status_code=404, detail="Run not found")
        answers = [a.model_dump() for a in body.answers]
        try:
            state = await runner.clarify_run(run_id, answers)
        except ValueError as exc:
            logger.warning("POST /runs/%s/clarify rejected: %s", run_id, exc)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        logger.info("POST /runs/%s/clarify success status=%s", run_id, state.get("status") if state else "?")
        return _to_response(state)
    except HTTPException:
        raise
    except Exception:
        logger.exception("POST /runs/%s/clarify error", run_id)
        raise


@router.post("/runs/{run_id}/approve", response_model=RunStatusResponse, status_code=202)
async def approve_run(run_id: str, body: ApproveRunRequest | None = None):
    workspace_mode = body.workspace_mode if body else "worktree"
    logger.info("POST /runs/%s/approve workspace_mode=%s", run_id, workspace_mode)
    try:
        state = await runner.approve_run(run_id, workspace_mode=workspace_mode)
        if not state:
            logger.warning("POST /runs/%s/approve not found", run_id)
            raise HTTPException(status_code=404, detail="Run not found")
        logger.info("POST /runs/%s/approve success status=%s", run_id, state.get("status"))
        return _to_response(state)
    except HTTPException:
        raise
    except Exception:
        logger.exception("POST /runs/%s/approve error", run_id)
        raise


@router.post("/runs/{run_id}/retry", response_model=RunStatusResponse, status_code=202)
async def retry_run(run_id: str):
    logger.info("POST /runs/%s/retry", run_id)
    try:
        state = await runner.get_state(run_id)
        if not state:
            logger.warning("POST /runs/%s/retry not found", run_id)
            raise HTTPException(status_code=404, detail="Run not found")
        try:
            state = await runner.retry_run(run_id)
        except ValueError as exc:
            logger.warning("POST /runs/%s/retry rejected: %s", run_id, exc)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        logger.info("POST /runs/%s/retry accepted status=%s", run_id, state.get("status"))
        return _to_response(state)
    except HTTPException:
        raise
    except Exception:
        logger.exception("POST /runs/%s/retry error", run_id)
        raise


@router.post("/runs/{run_id}/resume", response_model=RunStatusResponse, status_code=202)
async def resume_run(run_id: str):
    logger.info("POST /runs/%s/resume", run_id)
    try:
        state = await runner.get_state(run_id)
        if not state:
            logger.warning("POST /runs/%s/resume not found", run_id)
            raise HTTPException(status_code=404, detail="Run not found")
        try:
            state = await runner.resume_run(run_id)
        except ValueError as exc:
            logger.warning("POST /runs/%s/resume rejected: %s", run_id, exc)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        logger.info("POST /runs/%s/resume accepted status=%s", run_id, state.get("status"))
        return _to_response(state)
    except HTTPException:
        raise
    except Exception:
        logger.exception("POST /runs/%s/resume error", run_id)
        raise


@router.post("/runs/{run_id}/merge", response_model=MergeRunResponse)
async def merge_run(run_id: str, body: MergeRunRequest | None = None):
    logger.info("POST /runs/%s/merge", run_id)
    try:
        state = await runner.get_state(run_id)
        if not state:
            logger.warning("POST /runs/%s/merge not found", run_id)
            raise HTTPException(status_code=404, detail="Run not found")
        try:
            state = await runner.merge_run(
                run_id,
                target_branch=body.target_branch if body else None,
                commit_message=body.commit_message if body else None,
            )
        except MergeValidationError as exc:
            logger.warning("POST /runs/%s/merge rejected: %s", run_id, exc)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except GitMergeConflictError as exc:
            logger.warning("POST /runs/%s/merge conflict: %s", run_id, exc)
            return JSONResponse(
                status_code=409,
                content=_merge_response(exc.merge_report).model_dump(),
            )
        logger.info("POST /runs/%s/merge success", run_id)
        report = (state or {}).get("merge_report") or {}
        return _merge_response(report)
    except HTTPException:
        raise
    except Exception:
        logger.exception("POST /runs/%s/merge error", run_id)
        raise


@router.post("/runs/{run_id}/reject", response_model=RunStatusResponse)
async def reject_run(run_id: str, body: RejectRunRequest | None = None):
    logger.info("POST /runs/%s/reject", run_id)
    try:
        state = await runner.reject_run(run_id, body.feedback if body else None)
        if not state:
            logger.warning("POST /runs/%s/reject not found", run_id)
            raise HTTPException(status_code=404, detail="Run not found")
        logger.info("POST /runs/%s/reject success status=%s", run_id, state.get("status"))
        return _to_response(state)
    except HTTPException:
        raise
    except Exception:
        logger.exception("POST /runs/%s/reject error", run_id)
        raise


@router.post("/runs/{run_id}/revert", response_model=RunStatusResponse)
async def revert_run(run_id: str):
    logger.info("POST /runs/%s/revert", run_id)
    try:
        state = await runner.revert_run(run_id)
        if not state:
            logger.warning("POST /runs/%s/revert not found", run_id)
            raise HTTPException(status_code=404, detail="Run not found")
        logger.info("POST /runs/%s/revert success status=%s", run_id, state.get("status"))
        return _to_response(state)
    except HTTPException:
        raise
    except Exception:
        logger.exception("POST /runs/%s/revert error", run_id)
        raise


@router.get("/runs/{run_id}/events")
async def stream_run_events(run_id: str):
    logger.info("GET /runs/%s/events", run_id)

    async def event_generator():
        last_status = None
        while True:
            try:
                state = await runner.get_state(run_id)
                if not state:
                    logger.warning("SSE /runs/%s/events not found", run_id)
                    yield f"data: {json.dumps({'error': 'not_found'})}\n\n"
                    break
                status = state.get("status")
                if status != last_status:
                    logger.info("SSE /runs/%s/events status=%s", run_id, status)
                    payload = {"status": status, "run_id": run_id}
                    yield f"data: {json.dumps(payload)}\n\n"
                    last_status = status
                if status in {"completed", "failed", "rejected", "awaiting_approval"}:
                    break
                await asyncio.sleep(2)
            except Exception:
                logger.exception("SSE /runs/%s/events error", run_id)
                yield f"data: {json.dumps({'error': 'stream_error'})}\n\n"
                break

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Epic-level execution
# ---------------------------------------------------------------------------

def _to_epic_response(state: dict) -> EpicRunResponse:
    plan = state.get("plan") or {}
    nodes = {n.get("ticket_id"): n for n in (plan.get("nodes") or [])}
    children: list[EpicChildRun] = []
    for entry in (state.get("child_runs") or {}).values():
        tid = entry.get("ticket_id")
        node = nodes.get(tid, {})
        children.append(
            EpicChildRun(
                ticket_id=tid,
                jira_key=entry.get("jira_key") or node.get("jira_key"),
                title=node.get("title"),
                run_id=entry.get("run_id"),
                status=entry.get("status", "pending"),
            )
        )
    children.sort(key=lambda c: c.ticket_id)
    return EpicRunResponse(
        epic_run_id=state["epic_run_id"],
        epic_ticket_id=state["epic_ticket_id"],
        epic_jira_key=state.get("epic_jira_key"),
        status=state.get("status", "planning"),
        workspace_mode=state.get("workspace_mode") or "worktree",
        integration_branch=state.get("integration_branch"),
        auto_approve=bool(state.get("auto_approve")),
        plan=plan or None,
        children=children,
        error=state.get("error"),
        created_at=state.get("created_at"),
        updated_at=state.get("updated_at"),
    )


@router.post("/epics/{ticket_id}/run", response_model=StartEpicRunResponse, status_code=202)
async def start_epic_run(ticket_id: int, body: StartEpicRunRequest | None = None):
    """Plan and queue execution of every child story under an epic ticket."""
    auto_approve = body.auto_approve if body else None
    logger.info("POST /epics/%s/run auto_approve=%s", ticket_id, auto_approve)
    if not get_ticket(ticket_id):
        raise HTTPException(status_code=404, detail="Ticket not found")
    try:
        state = await epic_runner.start_epic_run(ticket_id, auto_approve=auto_approve)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return StartEpicRunResponse(epic_run_id=state["epic_run_id"], status=state["status"])


@router.get("/epics", response_model=EpicRunListResponse)
async def list_epic_runs(
    project_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    rows = await epic_runner.list_epic_runs(project_id=project_id, limit=limit)
    return EpicRunListResponse(
        epic_runs=[_to_epic_response(r) for r in rows], total=len(rows)
    )


@router.get("/epics/{epic_run_id}", response_model=EpicRunResponse)
async def get_epic_run(epic_run_id: str):
    state = await epic_runner.get_state(epic_run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Epic run not found")
    return _to_epic_response(state)


@router.post("/epics/{epic_run_id}/approve", response_model=EpicRunResponse, status_code=202)
async def approve_epic_run(epic_run_id: str, body: ApproveEpicRunRequest | None = None):
    logger.info("POST /epics/%s/approve", epic_run_id)
    mode = body.workspace_mode if body else "worktree"
    state = await epic_runner.approve_epic_run(epic_run_id, workspace_mode=mode)
    if not state:
        raise HTTPException(status_code=404, detail="Epic run not found")
    return _to_epic_response(state)


@router.post("/epics/{epic_run_id}/reject", response_model=EpicRunResponse)
async def reject_epic_run(epic_run_id: str):
    logger.info("POST /epics/%s/reject", epic_run_id)
    state = await epic_runner.reject_epic_run(epic_run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Epic run not found")
    return _to_epic_response(state)


@router.post("/epics/{epic_run_id}/resume", response_model=EpicRunResponse, status_code=202)
async def resume_epic_run(epic_run_id: str):
    """Continue a failed epic from where it stopped, re-running only the stories
    that did not complete and preserving the integration branch."""
    logger.info("POST /epics/%s/resume", epic_run_id)
    if not await epic_runner.get_state(epic_run_id):
        raise HTTPException(status_code=404, detail="Epic run not found")
    try:
        state = await epic_runner.resume_epic_run(epic_run_id)
    except ValueError as exc:
        logger.warning("POST /epics/%s/resume rejected: %s", epic_run_id, exc)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _to_epic_response(state)
