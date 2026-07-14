import operator
from typing import Annotated, Any, Literal, TypedDict

RunStatus = Literal[
    "planning",
    "awaiting_clarification",
    "awaiting_approval",
    "developing",
    "verifying",
    "qa",
    "completed",
    "failed",
    "rejected",
]

WorkspaceMode = Literal["worktree", "in_place"]


class FeatureRunState(TypedDict, total=False):
    run_id: str
    # Execution lineage: every user-triggered retry/rerun is a NEW execution with
    # its own run_id. root_run_id ties all attempts of one logical task together;
    # parent_run_id points at the immediately previous attempt; attempt is 1-based.
    # Keeping each attempt under a distinct run_id is what isolates token usage
    # (token activity is keyed by run_id) so a retry no longer inherits the prior
    # attempt's token totals.
    root_run_id: str
    parent_run_id: str | None
    attempt: int
    status: RunStatus
    user_request: str
    project_path: str
    ticket_id: int | None
    worktree_path: str | None
    workspace_mode: WorkspaceMode
    iteration: int
    max_iterations: int
    context_bundle: dict[str, Any]
    plan: dict[str, Any]
    acceptance_criteria: list[str]
    file_changes: list[dict[str, Any]]
    diffs: list[dict[str, Any]]
    verifier_report: dict[str, Any]
    qa_report: dict[str, Any]
    messages: Annotated[list[dict[str, Any]], operator.add]
    # Dev loop's internal tool-calling conversation, carried across iterations so a
    # retry / step-limit continuation resumes from prior context instead of
    # re-discovering the codebase. Replace semantics (last write wins), NOT append —
    # this is distinct from `messages` (the append-only UI activity log).
    dev_messages: list[dict[str, Any]]
    # Result of the build_runner pass the dev node ran at finalize: {passed, signature}.
    # The verifier reuses it (skips its own build_runner) when the source signature is
    # unchanged, so build_runner runs at most once per dev→verify cycle.
    dev_build_runner: dict[str, Any]
    error: str | None
    approved: bool
    rejected: bool
    merge_report: dict[str, Any]
    truncated: bool  # True when dev agent hit step limit without completing
    # Planner clarification Q&A
    clarification_questions: list[dict[str, Any]]  # questions emitted by planner first pass
    clarification_answers: list[dict[str, Any]]  # user answers before second planner pass
    # Jira integration: optional context passed to the planner
    attachment_paths: list[str]  # local paths to downloaded Jira image attachments
    linked_issues_context: str | None  # formatted text of linked Jira issue summaries
    acceptance_criteria_hint: list[str]  # Jira acceptance criteria seeded to planner


def initial_state(run_id: str, user_request: str, project_path: str) -> FeatureRunState:
    return FeatureRunState(
        run_id=run_id,
        root_run_id=run_id,
        parent_run_id=None,
        attempt=1,
        status="planning",
        user_request=user_request,
        project_path=project_path,
        worktree_path=None,
        workspace_mode="worktree",
        iteration=0,
        max_iterations=3,
        context_bundle={},
        plan={},
        acceptance_criteria=[],
        file_changes=[],
        diffs=[],
        verifier_report={},
        qa_report={},
        messages=[],
        error=None,
        approved=False,
        rejected=False,
        merge_report={},
        truncated=False,
        clarification_questions=[],
        clarification_answers=[],
        attachment_paths=[],
        linked_issues_context=None,
        acceptance_criteria_hint=[],
    )
