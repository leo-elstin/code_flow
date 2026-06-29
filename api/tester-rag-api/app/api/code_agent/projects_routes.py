import asyncio
import json
import os

from app.core.config import settings

from fastapi import APIRouter, HTTPException, Response

from app.api.code_agent.schemas import (
    AgentsMdStatusResponse,
    CreateProjectRequest,
    PickFolderRequest,
    PickFolderResponse,
    CreateTicketRequest,
    GenerateAgentsMdRequest,
    GenerateAgentsMdResponse,
    GenerateProjectContextRequest,
    GenerateProjectContextResponse,
    JiraConfigResponse,
    JiraStatusCheckResponse,
    JiraSyncResponse,
    JiraTransitionResponse,
    ProjectContextResponse,
    ProjectResponse,
    ProjectSkillSummary,
    ProjectSkillsListResponse,
    ProjectsListResponse,
    TicketResponse,
    TicketsListResponse,
    UpdateJiraConfigRequest,
    UpdateProjectContextRequest,
)
from app.services.folder_picker import pick_folder
from app.services.agents_md_generator import (
    generate_agents_md_content,
    get_agents_md_status,
    write_agents_md,
)
from app.services.project_agent_context import list_available_skills
from app.services.project_context_generator import generate_project_context
from app.services.project_skills import normalize_skill_ids
from app.services.jira_service import JiraService
from app.services.jira_sync import sync_jira_tickets
from app.services.project_ticket_store import (
    create_ticket,
    delete_ticket,
    delete_project,
    get_project,
    get_ticket,
    list_projects,
    list_tickets,
    update_project_context,
    update_project_jira_config,
    upsert_project,
)

router = APIRouter()


@router.post("/pick-folder", response_model=PickFolderResponse)
async def pick_project_folder(body: PickFolderRequest | None = None):
    """Open a native folder picker on the API host (local dev only)."""
    initial_dir = body.initial_dir if body else None
    if initial_dir:
        initial_dir = os.path.abspath(initial_dir)
        if not os.path.isdir(initial_dir):
            initial_dir = None

    selected = await asyncio.to_thread(pick_folder, initial_dir=initial_dir)
    if not selected:
        return Response(status_code=204)

    return PickFolderResponse(path=selected)


@router.post("/projects", response_model=ProjectResponse)
async def create_project(body: CreateProjectRequest):
    repo_path = os.path.abspath(body.path)
    if not os.path.isdir(repo_path):
        raise HTTPException(status_code=400, detail="Project path does not exist")
    if not os.path.isdir(os.path.join(repo_path, ".git")):
        raise HTTPException(status_code=400, detail="Project path must be a git repository")

    project = upsert_project(path=repo_path, name=body.name)
    return ProjectResponse(**project)


@router.get("/projects", response_model=ProjectsListResponse)
async def get_projects():
    rows = [ProjectResponse(**row) for row in list_projects()]
    return ProjectsListResponse(projects=rows, total=len(rows))


@router.get("/projects/{project_id}", response_model=ProjectResponse)
async def get_project_by_id(project_id: int):
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectResponse(**project)


@router.delete("/projects/{project_id}", status_code=204)
async def remove_project(project_id: int):
    deleted = delete_project(project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")


@router.post("/projects/{project_id}/tickets", response_model=TicketResponse)
async def create_project_ticket(project_id: int, body: CreateTicketRequest):
    try:
        ticket = create_ticket(
            project_id=project_id,
            title=body.title,
            description=body.description,
            ticket_type=body.ticket_type,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Project not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return TicketResponse(**ticket)


@router.get("/projects/{project_id}/tickets", response_model=TicketsListResponse)
async def get_project_tickets(project_id: int):
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    rows = [TicketResponse(**row) for row in list_tickets(project_id)]
    return TicketsListResponse(tickets=rows, total=len(rows))


@router.get("/tickets/{ticket_id}", response_model=TicketResponse)
async def get_ticket_by_id(ticket_id: int):
    ticket = get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return TicketResponse(**ticket)


@router.delete("/tickets/{ticket_id}", status_code=204)
async def delete_ticket_by_id(ticket_id: int):
    deleted = delete_ticket(ticket_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Ticket not found")


def _parse_skill_ids_field(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return normalize_skill_ids([str(item) for item in parsed])


def _to_context_response(project: dict) -> ProjectContextResponse:
    return ProjectContextResponse(
        project_id=project["id"],
        context_text=(project.get("context_text") or "").strip(),
        planner_skill_ids=_parse_skill_ids_field(project.get("planner_skill_ids")),
        dev_skill_ids=_parse_skill_ids_field(project.get("dev_skill_ids")),
    )


@router.get("/projects/{project_id}/context", response_model=ProjectContextResponse)
async def get_project_context(project_id: int):
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return _to_context_response(project)


@router.put("/projects/{project_id}/context", response_model=ProjectContextResponse)
async def put_project_context(project_id: int, body: UpdateProjectContextRequest):
    updated = update_project_context(
        project_id,
        context_text=body.context_text,
        planner_skill_ids=body.planner_skill_ids,
        dev_skill_ids=body.dev_skill_ids,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Project not found")
    return _to_context_response(updated)


@router.post("/projects/{project_id}/context/generate", response_model=GenerateProjectContextResponse)
async def post_generate_project_context(project_id: int, body: GenerateProjectContextRequest):
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        context_text = await generate_project_context(project["path"], hints=body.hints or "")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Context generation failed: {exc}") from exc
    return GenerateProjectContextResponse(context_text=context_text)


@router.get("/projects/{project_id}/skills", response_model=ProjectSkillsListResponse)
async def get_project_skills(project_id: int):
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    skills = [ProjectSkillSummary(**skill) for skill in list_available_skills(project["path"])]
    return ProjectSkillsListResponse(skills=skills, total=len(skills))


def _to_agents_md_status(project: dict) -> AgentsMdStatusResponse:
    status = get_agents_md_status(project["path"])
    return AgentsMdStatusResponse(
        project_id=project["id"],
        exists=bool(status.get("exists")),
        filename=status.get("filename") or "",
        path=status.get("path") or "",
        content_preview=status.get("content_preview") or "",
    )


@router.get("/projects/{project_id}/agents-md", response_model=AgentsMdStatusResponse)
async def get_project_agents_md_status(project_id: int):
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return _to_agents_md_status(project)


@router.post("/projects/{project_id}/agents-md/generate", response_model=GenerateAgentsMdResponse)
async def post_generate_project_agents_md(project_id: int, body: GenerateAgentsMdRequest):
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    status = get_agents_md_status(project["path"])
    if status.get("exists"):
        raise HTTPException(
            status_code=409,
            detail=f"{status.get('path') or 'AGENTS.md'} already exists in this project",
        )

    try:
        content = await generate_agents_md_content(project["path"], hints=body.hints or "")
        written_path = write_agents_md(project["path"], content)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AGENTS.md generation failed: {exc}") from exc

    return GenerateAgentsMdResponse(written=True, path=written_path, content=content)


@router.get("/jira/status", response_model=JiraStatusCheckResponse)
async def get_jira_status():
    configured = JiraService.is_configured()
    return JiraStatusCheckResponse(
        configured=configured,
        base_url=settings.JIRA_BASE_URL if configured else None,
        user_email=settings.JIRA_USER_EMAIL if configured else None,
    )


@router.get("/projects/{project_id}/jira/config", response_model=JiraConfigResponse)
async def get_project_jira_config(project_id: int):
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    mapping = {}
    if project.get("jira_status_mapping"):
        try:
            mapping = json.loads(project["jira_status_mapping"])
        except json.JSONDecodeError:
            pass
    return JiraConfigResponse(
        project_id=project_id,
        jira_jql=project.get("jira_jql") or "",
        jira_status_mapping=mapping,
    )


@router.put("/projects/{project_id}/jira/config", response_model=JiraConfigResponse)
async def put_project_jira_config(project_id: int, body: UpdateJiraConfigRequest):
    updated = update_project_jira_config(
        project_id,
        jira_jql=body.jira_jql,
        jira_status_mapping=body.jira_status_mapping,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Project not found")
    mapping = {}
    if updated.get("jira_status_mapping"):
        try:
            mapping = json.loads(updated["jira_status_mapping"])
        except json.JSONDecodeError:
            pass
    return JiraConfigResponse(
        project_id=project_id,
        jira_jql=updated.get("jira_jql") or "",
        jira_status_mapping=mapping,
    )


@router.post("/projects/{project_id}/jira/sync", response_model=JiraSyncResponse)
async def post_sync_jira_tickets(project_id: int):
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not JiraService.is_configured():
        raise HTTPException(status_code=400, detail="Jira credentials not configured")
    try:
        result = sync_jira_tickets(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Jira sync failed: {exc}")
    return JiraSyncResponse(
        created=result.created,
        updated=result.updated,
        total=result.total,
        errors=result.errors,
    )


@router.get("/projects/{project_id}/jira/transitions", response_model=JiraTransitionResponse)
async def get_jira_transitions(project_id: int, issue_key: str):
    if not JiraService.is_configured():
        raise HTTPException(status_code=400, detail="Jira credentials not configured")
    try:
        service = JiraService()
        transitions = service.get_transitions(issue_key)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to get transitions: {exc}")
    return JiraTransitionResponse(transitions=transitions)
