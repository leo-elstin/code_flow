from pydantic import BaseModel, Field


class StartRunRequest(BaseModel):
    request: str = Field(..., description="Feature request for the code agent pipeline")
    project_path: str = Field(..., description="Absolute path to the target git repository")
    ticket_id: int | None = None
    # None = fall back to the CODE_AGENT_AUTO_APPROVE server default.
    auto_approve: bool | None = None


class StartRunResponse(BaseModel):
    run_id: str
    status: str


class RejectRunRequest(BaseModel):
    feedback: str | None = None


class ApproveRunRequest(BaseModel):
    workspace_mode: str = Field(
        default="worktree",
        description="worktree = isolated git worktree; in_place = edit the main project checkout",
    )


class ClarifyAnswer(BaseModel):
    question_id: str
    question: str = ""
    option_id: str
    option_label: str = ""
    option_description: str = ""


class ClarifyRunRequest(BaseModel):
    answers: list[ClarifyAnswer]


class MergeRunRequest(BaseModel):
    target_branch: str | None = None
    commit_message: str | None = None


class MergeRunResponse(BaseModel):
    applied: bool
    target_branch: str | None = None
    agent_branch: str | None = None
    conflict_files: list[str] = Field(default_factory=list)
    error: str | None = None
    merged_at: str | None = None


class MergePreview(BaseModel):
    target_branch: str


class RunSummaryResponse(BaseModel):
    run_id: str
    status: str
    user_request: str | None = None
    project_path: str | None = None
    iteration: int | None = None
    error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    ticket_id: int | None = None
    # Execution lineage: the latest attempt shown for this task, plus how many
    # attempts exist under the same root.
    root_run_id: str | None = None
    parent_run_id: str | None = None
    attempt: int | None = None
    execution_count: int | None = None


class PickFolderRequest(BaseModel):
    initial_dir: str | None = None


class PickFolderResponse(BaseModel):
    path: str


class CreateProjectRequest(BaseModel):
    path: str
    name: str | None = None


class ProjectResponse(BaseModel):
    id: int
    name: str
    path: str
    created_at: str
    updated_at: str


class ProjectSkillSummary(BaseModel):
    id: str
    name: str
    path: str
    description: str = ""


class ProjectSkillsListResponse(BaseModel):
    skills: list[ProjectSkillSummary]
    total: int


class ProjectContextResponse(BaseModel):
    project_id: int
    context_text: str = ""
    planner_skill_ids: list[str] = Field(default_factory=list)
    dev_skill_ids: list[str] = Field(default_factory=list)


class UpdateProjectContextRequest(BaseModel):
    context_text: str | None = None
    planner_skill_ids: list[str] | None = None
    dev_skill_ids: list[str] | None = None


class GenerateProjectContextRequest(BaseModel):
    hints: str | None = None


class GenerateProjectContextResponse(BaseModel):
    context_text: str


class AgentsMdStatusResponse(BaseModel):
    project_id: int
    exists: bool
    filename: str = ""
    path: str = ""
    content_preview: str = ""


class GenerateAgentsMdRequest(BaseModel):
    hints: str | None = None


class GenerateAgentsMdResponse(BaseModel):
    written: bool
    path: str
    content: str


class ProjectsListResponse(BaseModel):
    projects: list[ProjectResponse]
    total: int


class CreateTicketRequest(BaseModel):
    title: str
    description: str | None = None
    ticket_type: str = Field(..., description="feature or bug")


class TicketResponse(BaseModel):
    id: int
    project_id: int
    title: str
    description: str | None = None
    ticket_type: str
    status: str
    run_id: str | None = None
    source: str = "local"
    jira_key: str | None = None
    jira_issue_type: str | None = None
    jira_parent_key: str | None = None
    jira_status: str | None = None
    jira_priority: str | None = None
    created_at: str
    updated_at: str


class TicketsListResponse(BaseModel):
    tickets: list[TicketResponse]
    total: int


class RunListResponse(BaseModel):
    runs: list[RunSummaryResponse]
    total: int


class TokenUsageResponse(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0


class ExecutionSummaryResponse(BaseModel):
    run_id: str
    attempt: int = 1
    status: str
    error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    parent_run_id: str | None = None
    root_run_id: str | None = None
    token_usage: TokenUsageResponse = Field(default_factory=TokenUsageResponse)


class ExecutionListResponse(BaseModel):
    root_run_id: str
    executions: list[ExecutionSummaryResponse]
    total: int


class ActivityEventResponse(BaseModel):
    seq: int
    type: str
    phase: str
    title: str
    detail: str | None = None
    files: list[str] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)
    created_at: str | None = None


class CurrentActionResponse(BaseModel):
    seq: int
    type: str
    phase: str
    title: str
    detail: str | None = None
    files: list[str] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)
    created_at: str | None = None


class ActivityListResponse(BaseModel):
    events: list[ActivityEventResponse]
    current_action: CurrentActionResponse | None = None
    token_usage: TokenUsageResponse = Field(default_factory=TokenUsageResponse)


class RunStatusResponse(BaseModel):
    run_id: str
    root_run_id: str | None = None
    parent_run_id: str | None = None
    attempt: int | None = None
    status: str
    user_request: str | None = None
    project_path: str | None = None
    ticket_id: int | None = None
    worktree_path: str | None = None
    workspace_mode: str | None = None
    iteration: int | None = None
    plan: dict | None = None
    acceptance_criteria: list[str] | None = None
    clarification_questions: list[dict] | None = None
    context_bundle: dict | None = None
    file_changes: list[dict] | None = None
    diffs: list[dict] | None = None
    verifier_report: dict | None = None
    qa_report: dict | None = None
    messages: list[dict] | None = None
    error: str | None = None
    merge_report: dict | None = None
    merge_preview: MergePreview | None = None
    is_running: bool = False
    current_action: CurrentActionResponse | None = None
    token_usage: TokenUsageResponse | None = None


class JiraSyncResponse(BaseModel):
    created: int
    updated: int
    total: int
    errors: list[str] = Field(default_factory=list)


class JiraConfigResponse(BaseModel):
    project_id: int
    jira_jql: str = ""
    jira_status_mapping: dict[str, str] = Field(default_factory=dict)


class UpdateJiraConfigRequest(BaseModel):
    jira_jql: str | None = None
    jira_status_mapping: dict[str, str] | None = None


class JiraStatusCheckResponse(BaseModel):
    configured: bool
    base_url: str | None = None
    user_email: str | None = None


class JiraTransitionResponse(BaseModel):
    transitions: list[dict] = Field(default_factory=list)


class LlmConfigResponse(BaseModel):
    provider: str
    # Masked preview only (e.g. "sk-…a1b2") — the raw key never leaves the API.
    api_key_preview: str | None = None
    key_configured: bool = False
    base_url: str | None = None
    chat_model: str | None = None
    dev_model: str | None = None
    # OpenAI-family only; None means every call stays on Chat Completions.
    reasoning_effort: str | None = None
    reasoning_mode: str | None = None
    # True when this config came from .env rather than a saved DB row, so the
    # UI can show that nothing has been configured through the app yet.
    from_env: bool = False
    # Project-scoped responses only: whether an override is actually set.
    project_id: int | None = None
    has_override: bool | None = None


class UpdateLlmConfigRequest(BaseModel):
    provider: str
    # Omitted or null keeps the stored key, so round-tripping the masked
    # preview from the UI can never wipe a real credential. Send "" to clear.
    api_key: str | None = None
    base_url: str | None = None
    chat_model: str | None = None
    dev_model: str | None = None
    reasoning_effort: str | None = None
    reasoning_mode: str | None = None


class UpdateProjectLlmConfigRequest(UpdateLlmConfigRequest):
    # Null provider clears the override so the project falls back to global.
    provider: str | None = None  # type: ignore[assignment]


class LlmTestResponse(BaseModel):
    ok: bool
    provider: str
    model: str | None = None
    error: str | None = None


class EpicChildRun(BaseModel):
    ticket_id: int
    jira_key: str | None = None
    title: str | None = None
    run_id: str | None = None
    status: str = "pending"


class StartEpicRunRequest(BaseModel):
    # None = fall back to the EPIC_AUTO_APPROVE server default.
    auto_approve: bool | None = None
    workspace_mode: str = "worktree"


class StartEpicRunResponse(BaseModel):
    epic_run_id: str
    status: str


class ApproveEpicRunRequest(BaseModel):
    workspace_mode: str = "worktree"


class EpicRunResponse(BaseModel):
    epic_run_id: str
    epic_ticket_id: int
    epic_jira_key: str | None = None
    status: str
    workspace_mode: str = "worktree"
    integration_branch: str | None = None
    auto_approve: bool = False
    # plan = {levels: [[ticket_id,...],...], edges, reasoning, had_cycle, nodes}
    plan: dict | None = None
    children: list[EpicChildRun] = Field(default_factory=list)
    error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class EpicRunListResponse(BaseModel):
    epic_runs: list[EpicRunResponse] = Field(default_factory=list)
    total: int = 0
