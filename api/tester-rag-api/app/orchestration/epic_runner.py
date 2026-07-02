"""Epic-level execution orchestrator.

Runs every child story under a Jira Epic, using the dependency plan from
:mod:`app.agents.roles.epic_planner` to decide order and concurrency:

- Stories in the same topological level are **independent** and run as parallel
  sub-agents (each a normal per-ticket run via the shared
  :class:`~app.orchestration.runner.CodeAgentRunner`).
- A level only starts once every earlier level has completed.

Git: a per-epic **integration branch** (``agent/epic-<id>``) is created in its
own worktree. Each child story's worktree is branched from that integration
branch (via ``base_ref``), so a dependent story sees its predecessors' code;
after a level finishes, completed children are merged into the integration
branch sequentially. Merge conflicts between sibling stories fail the epic-run
for manual resolution (v1).

This orchestrator owns coordination state only (see
:mod:`app.services.epic_run_store`); it never enters the dev LangGraph itself.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid

from app.core.config import settings
from app.core.logging_config import get_logger
from app.agents.roles.epic_planner import run_epic_planner
from app.orchestration.runner import runner
from app.services import epic_run_store
from app.services.project_ticket_store import get_project, get_ticket, list_children
from app.services.run_activity import append_activity
from app.services.worktree import remove_worktree, resolve_worktree_path
from app.tools.git_tools import (
    GitMergeError,
    abort_merge_if_in_progress,
    commit_all,
    get_checkout_branch,
    merge_branch,
)

logger = get_logger("epic_runner")

_TERMINAL = {"completed", "failed", "rejected"}
# Fallback poll bounds for the rare gaps where no in-process task is awaitable
# (between task hand-offs, or when the run lives in another process).
_POLL_MIN_S = 0.5
_POLL_MAX_S = 5.0
# Upper bound a single child run may take before the epic gives up waiting on it.
_CHILD_TIMEOUT_S = int(os.getenv("EPIC_CHILD_TIMEOUT_S", str(60 * 60)))


def _child_request(ticket: dict) -> str:
    """Build the planner request string for a child story from its ticket.

    Jira descriptions are stored as a JSON blob; unwrap to the human text.
    runner.start_run separately injects Jira AC / linked-issue context.
    """
    title = ticket.get("title") or f"Ticket {ticket['id']}"
    desc = ticket.get("description") or ""
    if isinstance(desc, str) and desc.strip().startswith("{"):
        try:
            desc = json.loads(desc).get("raw_description") or ""
        except (ValueError, TypeError):
            pass
    return f"{title}\n\n{desc}".strip() if desc else title


# ---------------------------------------------------------------------------
# Git integration-branch helpers (run via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _epic_integration_branch(epic_run_id: str, epic_jira_key: str | None) -> str:
    """Human-friendly integration branch per epic.

    A Jira-sourced epic lands all its stories on ``feature/<JIRA-KEY>``
    (e.g. ``feature/MMA-3480``) in the real repo, so the changes live on a
    named branch the user can check out. Falls back to a run-scoped name for
    epics with no Jira key."""
    if epic_jira_key and epic_jira_key.strip():
        return f"feature/{epic_jira_key.strip().replace(' ', '-')}"
    return f"agent/epic-{epic_run_id}"


def _create_integration_worktree(
    project_path: str, epic_run_id: str, integration_branch: str
) -> str:
    """Check out the epic's integration branch in its own worktree.

    Creates ``integration_branch`` from HEAD when it does not exist; when it
    already exists (a re-run of the same epic reuses ``feature/<KEY>``), the
    worktree is attached to the existing branch so its history accumulates."""
    head = subprocess.run(
        ["git", "-C", project_path, "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    wt_path = resolve_worktree_path(project_path, run_id=f"epic-{epic_run_id}")
    os.makedirs(os.path.dirname(wt_path), exist_ok=True)
    if os.path.exists(wt_path):
        remove_worktree(project_path, wt_path)
    branch_exists = subprocess.run(
        ["git", "-C", project_path, "rev-parse", "--verify", f"refs/heads/{integration_branch}"],
        capture_output=True, text=True, check=False,
    ).returncode == 0
    add_args = (
        ["worktree", "add", wt_path, integration_branch]
        if branch_exists
        else ["worktree", "add", "-b", integration_branch, wt_path, head]
    )
    proc = subprocess.run(
        ["git", "-C", project_path, *add_args],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "integration worktree add failed")
    return wt_path


def _open_integration_worktree(
    project_path: str, epic_run_id: str, integration_branch: str
) -> str:
    """Re-attach a worktree to an EXISTING integration branch (resume path).

    The branch already carries the merged work of children that completed before
    the epic failed, so — unlike :func:`_create_integration_worktree` — we check
    it out as-is instead of branching from HEAD. Falls back to recreating the
    branch from HEAD only if it has gone missing (prior merges are then lost)."""
    wt_path = resolve_worktree_path(project_path, run_id=f"epic-{epic_run_id}")
    if os.path.isdir(wt_path):
        # Worktree still on disk from the failed run — reuse it, clearing any
        # half-applied merge the conflict path may have left behind.
        abort_merge_if_in_progress(wt_path)
        return wt_path

    # Worktree dir gone but the branch may survive: drop stale registrations,
    # then re-add a worktree pointed at the existing branch.
    subprocess.run(
        ["git", "-C", project_path, "worktree", "prune"],
        capture_output=True, text=True, check=False,
    )
    branch_exists = subprocess.run(
        ["git", "-C", project_path, "rev-parse", "--verify", f"refs/heads/{integration_branch}"],
        capture_output=True, text=True, check=False,
    ).returncode == 0
    if branch_exists:
        os.makedirs(os.path.dirname(wt_path), exist_ok=True)
        proc = subprocess.run(
            ["git", "-C", project_path, "worktree", "add", wt_path, integration_branch],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode == 0:
            abort_merge_if_in_progress(wt_path)
            return wt_path
        logger.warning(
            "Epic %s reuse of integration branch failed (%s); recreating from HEAD",
            epic_run_id, proc.stderr.strip(),
        )
    return _create_integration_worktree(project_path, epic_run_id, integration_branch)


def _merge_child_into_integration(
    project_path: str,
    integration_wt: str,
    child_run_id: str,
    epic_run_id: str,
    child_wt: str | None = None,
) -> dict:
    """Commit a completed child's staged edits, then merge its branch into the
    integration branch. Returns ``{applied, error?, conflict_files?}``.

    ``child_wt`` may be passed explicitly because an auto-retried child reuses
    its parent run's worktree: the entry's run_id is the retry's, but the
    worktree (and its checked-out ``agent/<parent_run_id>`` branch) is not."""
    child_wt = child_wt or resolve_worktree_path(project_path, run_id=child_run_id)
    child_branch = f"agent/{child_run_id}"
    try:
        if os.path.isdir(child_wt):
            checked_out = get_checkout_branch(child_wt)
            if checked_out:
                child_branch = checked_out
            commit_all(child_wt, f"Epic {epic_run_id}: child run {child_run_id}")
        abort_merge_if_in_progress(integration_wt)
        merge_branch(integration_wt, child_branch)
    except GitMergeError as exc:
        return {
            "applied": False,
            "error": str(exc),
            "conflict_files": getattr(exc, "conflict_files", []),
        }
    return {"applied": True}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class EpicAgentRunner:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}

    def _task_running(self, epic_run_id: str) -> bool:
        task = self._tasks.get(epic_run_id)
        return task is not None and not task.done()

    def _reap_tasks(self, *, exclude: str | None = None) -> None:
        for eid in [e for e, t in self._tasks.items() if t.done() and e != exclude]:
            self._tasks.pop(eid, None)

    async def _wait_for(self, run_id: str, statuses: set[str]) -> dict | None:
        """Wait for a child run to reach one of *statuses* (or time out).

        Event-driven: awaits the child's in-process asyncio task (which ends at
        the planner interrupt and at terminal states) instead of polling every
        few seconds — each poll used to open the checkpointer and trigger a
        run-index/Jira sync. Falls back to a short adaptive sleep for the gaps
        where no task is registered (between hand-offs / cross-process runs)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _CHILD_TIMEOUT_S
        sleep_s = _POLL_MIN_S
        while True:
            remaining = deadline - loop.time()
            state = await runner.get_state(run_id)
            if (state and state.get("status") in statuses) or remaining <= 0:
                return state
            if runner._task_running(run_id):
                await runner.wait_for_task(run_id, timeout=remaining)
                sleep_s = _POLL_MIN_S
            else:
                await asyncio.sleep(min(sleep_s, remaining))
                sleep_s = min(sleep_s * 2, _POLL_MAX_S)

    # -- public API ----------------------------------------------------------

    def _begin_execution(self, epic_run_id: str, *, resume: bool = False) -> dict | None:
        """Shared transition into the execution phase (approve / auto / resume)."""
        updated = epic_run_store.update_epic_run(epic_run_id, status="developing", error=None)
        task = asyncio.create_task(self._run_execution_phase(epic_run_id, resume=resume))
        self._tasks[epic_run_id] = task
        return updated

    async def start_epic_run(
        self,
        epic_ticket_id: int,
        *,
        workspace_mode: str = "worktree",
        auto_approve: bool | None = None,
    ) -> dict:
        """Create an epic run and plan it (status planning → awaiting_approval).

        With ``auto_approve`` (per-request, defaulting to ``EPIC_AUTO_APPROVE``)
        the plan is executed immediately after planning — no human gate."""
        self._reap_tasks()
        epic = get_ticket(epic_ticket_id)
        if not epic:
            raise KeyError("epic_ticket_not_found")
        project = get_project(epic["project_id"])
        if not project:
            raise KeyError("project_not_found")

        auto = settings.EPIC_AUTO_APPROVE if auto_approve is None else bool(auto_approve)
        children = (
            list_children(epic["project_id"], epic["jira_key"])
            if epic.get("jira_key")
            else []
        )
        epic_run_id = str(uuid.uuid4())
        epic_run_store.create_epic_run(
            epic_run_id,
            epic_ticket_id=epic_ticket_id,
            project_id=epic["project_id"],
            project_path=project["path"],
            epic_jira_key=epic.get("jira_key"),
            workspace_mode="worktree" if workspace_mode != "in_place" else workspace_mode,
            auto_approve=auto,
        )
        for child in children:
            epic_run_store.set_child_run(
                epic_run_id, child["id"], status="pending", jira_key=child.get("jira_key")
            )

        task = asyncio.create_task(
            self._run_planning_phase(epic_run_id, epic, children, auto_approve=auto)
        )
        self._tasks[epic_run_id] = task
        return epic_run_store.get_epic_run(epic_run_id)

    async def approve_epic_run(
        self, epic_run_id: str, *, workspace_mode: str = "worktree"
    ) -> dict | None:
        state = epic_run_store.get_epic_run(epic_run_id)
        if not state:
            return None
        if state["status"] != "awaiting_approval":
            return state
        mode = "worktree" if workspace_mode == "in_place" else workspace_mode
        epic_run_store.update_epic_run(epic_run_id, workspace_mode=mode)
        return self._begin_execution(epic_run_id)

    async def reject_epic_run(self, epic_run_id: str) -> dict | None:
        state = epic_run_store.get_epic_run(epic_run_id)
        if not state:
            return None
        return epic_run_store.update_epic_run(epic_run_id, status="rejected")

    async def resume_epic_run(self, epic_run_id: str) -> dict | None:
        """Continue a failed epic from where it stopped: re-run the children that
        did not complete, skipping (and preserving the merges of) those that did.

        Each retried story is a fresh per-ticket run branched from the existing
        integration branch, so it sees every predecessor's merged code."""
        state = epic_run_store.get_epic_run(epic_run_id)
        if not state:
            return None
        if self._task_running(epic_run_id):
            raise ValueError("Epic run already in progress")
        if state["status"] != "failed":
            raise ValueError("Resume is only available for failed epic runs")
        if not ((state.get("plan") or {}).get("levels")):
            raise ValueError("Epic has no execution plan to resume; start a new run")
        self._reap_tasks()
        return self._begin_execution(epic_run_id, resume=True)

    async def get_state(self, epic_run_id: str) -> dict | None:
        state = epic_run_store.get_epic_run(epic_run_id)
        # A non-terminal epic with no live task in this process is an orphan from
        # a previous process (the in-memory asyncio task does not survive a
        # restart). Reconcile it so the UI shows real child statuses instead of a
        # frozen "Executing", and so Resume becomes available.
        if (
            state
            and state.get("status") in {"planning", "developing"}
            and not self._task_running(epic_run_id)
        ):
            state = await self._reconcile_orphan(epic_run_id, state)
        return state

    async def _reconcile_orphan(self, epic_run_id: str, state: dict) -> dict | None:
        """Refresh child statuses from the authoritative per-run states and settle
        the epic's own status. Used for runs interrupted by a process restart."""
        children = state.get("child_runs") or {}
        for key, entry in children.items():
            run_id = entry.get("run_id")
            snapshot = entry.get("status")
            if not run_id or snapshot in _TERMINAL:
                continue
            real = await runner.get_state(run_id)
            real_status = (real or {}).get("status")
            if real_status and real_status != snapshot:
                epic_run_store.set_child_run(
                    epic_run_id, entry.get("ticket_id") or int(key), status=real_status
                )

        refreshed = epic_run_store.get_epic_run(epic_run_id) or state
        levels = (refreshed.get("plan") or {}).get("levels") or []
        planned_ids = [tid for level in levels for tid in level]
        child_runs = refreshed.get("child_runs") or {}
        all_completed = bool(planned_ids) and all(
            (child_runs.get(str(tid)) or {}).get("status") == "completed"
            for tid in planned_ids
        )

        if all_completed:
            return epic_run_store.update_epic_run(epic_run_id, status="completed", error=None)
        message = (
            "Epic execution was interrupted (the server restarted while it was "
            "running). " + ("Resume to continue from where it stopped."
                            if planned_ids else "Start a new run.")
        )
        logger.info("Reconciled orphaned epic run_id=%s -> failed", epic_run_id)
        return epic_run_store.update_epic_run(epic_run_id, status="failed", error=message)

    async def list_epic_runs(self, project_id: int | None = None, limit: int = 50) -> list[dict]:
        return epic_run_store.list_epic_runs(project_id=project_id, limit=limit)

    # -- phases --------------------------------------------------------------

    async def _run_planning_phase(
        self, epic_run_id: str, epic: dict, children: list[dict], *, auto_approve: bool = False
    ) -> None:
        try:
            plan = await run_epic_planner(epic, children, run_id=epic_run_id)
            if auto_approve:
                # Full-auto mode: never persist awaiting_approval, so there is no
                # window where a restart strands the run at the human gate.
                epic_run_store.update_epic_run(epic_run_id, plan=plan)
                append_activity(
                    epic_run_id, type="status", phase="planner",
                    title="Auto-approved epic plan (full-auto mode)",
                )
                logger.info("Epic %s plan auto-approved (full-auto mode)", epic_run_id)
                self._begin_execution(epic_run_id)
            else:
                epic_run_store.update_epic_run(epic_run_id, plan=plan, status="awaiting_approval")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Epic planning failed epic_run_id=%s", epic_run_id)
            epic_run_store.update_epic_run(epic_run_id, status="failed", error=str(exc))

    async def _run_execution_phase(self, epic_run_id: str, *, resume: bool = False) -> None:
        try:
            state = epic_run_store.get_epic_run(epic_run_id)
            project_path = state["project_path"]
            auto_retry = bool(state.get("auto_approve"))
            levels: list[list[int]] = (state.get("plan") or {}).get("levels") or []

            # Set up the shared integration branch (best-effort: if it fails we
            # still run children, just branched from HEAD and not auto-merged).
            # On resume, re-attach to the branch the prior run already built so
            # completed children's merges are preserved.
            existing_branch = state.get("integration_branch")
            integration_branch = existing_branch or _epic_integration_branch(
                epic_run_id, state.get("epic_jira_key")
            )
            integration_wt: str | None = None
            try:
                if resume and existing_branch:
                    integration_wt = await asyncio.to_thread(
                        _open_integration_worktree, project_path, epic_run_id, integration_branch
                    )
                else:
                    integration_wt = await asyncio.to_thread(
                        _create_integration_worktree, project_path, epic_run_id, integration_branch
                    )
                epic_run_store.update_epic_run(epic_run_id, integration_branch=integration_branch)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Epic %s integration worktree failed: %s", epic_run_id, exc)
                integration_branch = None  # type: ignore[assignment]

            append_activity(
                epic_run_id, type="status", phase="dev",
                title="Epic execution resumed" if resume else "Epic execution started",
                detail=f"{len(levels)} level(s)",
            )

            for idx, level in enumerate(levels):
                # Skip children that already completed in an earlier pass; their
                # work is already merged into the integration branch.
                child_runs = (epic_run_store.get_epic_run(epic_run_id) or {}).get("child_runs") or {}
                pending = [
                    tid for tid in level
                    if (child_runs.get(str(tid)) or {}).get("status") != "completed"
                ]
                if not pending:
                    continue

                base_ref = integration_branch if integration_wt else None
                results = await asyncio.gather(
                    *[
                        self._execute_child(
                            epic_run_id, tid, project_path, base_ref, auto_retry=auto_retry
                        )
                        for tid in pending
                    ],
                    return_exceptions=True,
                )

                # Merge each newly-completed child into the integration branch in
                # order. Only children executed this pass are merged — earlier
                # completions are already on the branch. Merges MUST stay
                # sequential: they all mutate the single integration worktree.
                if integration_wt:
                    for tid in pending:
                        entry = ((epic_run_store.get_epic_run(epic_run_id) or {}).get("child_runs") or {}).get(str(tid)) or {}
                        if entry.get("status") == "completed" and entry.get("run_id"):
                            # A retried child reuses its parent's worktree, so
                            # resolve the real worktree from the run state.
                            child_state = await runner.get_state(entry["run_id"])
                            child_wt = (child_state or {}).get("worktree_path")
                            merged = await asyncio.to_thread(
                                _merge_child_into_integration,
                                project_path, integration_wt, entry["run_id"], epic_run_id,
                                child_wt,
                            )
                            if not merged.get("applied"):
                                msg = f"Merge conflict integrating ticket {tid}: {merged.get('error')}"
                                append_activity(epic_run_id, type="status", phase="dev",
                                                title="Epic merge conflict", detail=msg)
                                epic_run_store.update_epic_run(epic_run_id, status="failed", error=msg)
                                return

                failed = [
                    tid for tid, res in zip(pending, results)
                    if isinstance(res, Exception) or res != "completed"
                ]
                if failed:
                    msg = f"Level {idx} stories failed: {failed}; dependent stories skipped."
                    append_activity(epic_run_id, type="status", phase="dev",
                                    title="Epic execution failed", detail=msg)
                    epic_run_store.update_epic_run(epic_run_id, status="failed", error=msg)
                    return

            epic_run_store.update_epic_run(epic_run_id, status="completed", error=None)
            append_activity(epic_run_id, type="status", phase="qa",
                            title="Epic execution complete")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Epic execution failed epic_run_id=%s", epic_run_id)
            epic_run_store.update_epic_run(epic_run_id, status="failed", error=str(exc))
        finally:
            self._reap_tasks(exclude=epic_run_id)

    async def _execute_child(
        self,
        epic_run_id: str,
        ticket_id: int,
        project_path: str,
        base_ref: str | None,
        *,
        auto_retry: bool = False,
    ) -> str:
        """Run one child story to a terminal state; returns its final status.

        With ``auto_retry`` (full-auto mode) a failed child is retried up to
        ``EPIC_CHILD_AUTO_RETRIES`` times via :meth:`CodeAgentRunner.retry_run`
        before the failure is surfaced to the epic."""
        ticket = get_ticket(ticket_id)
        if not ticket:
            epic_run_store.set_child_run(epic_run_id, ticket_id, status="failed")
            return "failed"

        run_id = await runner.start_run(
            _child_request(ticket), project_path, ticket_id=ticket_id
        )
        epic_run_store.set_child_run(
            epic_run_id, ticket_id, run_id=run_id, status="planning",
            jira_key=ticket.get("jira_key"),
        )

        # Wait for planning to finish, then auto-approve (epic approval covers
        # the whole batch — child plans don't need individual approval).
        state = await self._wait_for(
            run_id, {"awaiting_approval", "awaiting_clarification"} | _TERMINAL
        )
        status = state.get("status") if state else None
        if status == "awaiting_clarification":
            # No human in the loop to answer planner questions — fail fast
            # instead of burning the full child timeout.
            msg = (
                f"Child run for ticket {ticket_id} asked clarification questions; "
                "epic mode cannot answer them."
            )
            append_activity(epic_run_id, type="status", phase="planner",
                            title="Child needs clarification", detail=msg)
            epic_run_store.set_child_run(epic_run_id, ticket_id, status="failed")
            return "failed"
        if status == "awaiting_approval":
            await runner.approve_run(run_id, workspace_mode="worktree", base_ref=base_ref)
            # Reflect the developing phase in the epic panel — otherwise the child
            # shows "planning" until it terminates (the event-driven wait doesn't
            # poll intermediate states).
            epic_run_store.set_child_run(epic_run_id, ticket_id, status="developing")
            state = await self._wait_for(run_id, _TERMINAL)
            status = state.get("status") if state else None

        final = status or "failed"
        retries = 0
        while final == "failed" and auto_retry and retries < settings.EPIC_CHILD_AUTO_RETRIES:
            retries += 1
            append_activity(
                epic_run_id, type="status", phase="dev",
                title=f"Auto-retrying ticket {ticket_id} (attempt {retries})",
            )
            try:
                new_state = await runner.retry_run(run_id)
            except ValueError as exc:
                logger.warning(
                    "Epic %s auto-retry of run %s unavailable: %s", epic_run_id, run_id, exc
                )
                break
            if not new_state or not new_state.get("run_id"):
                break
            # retry_run mints a NEW run_id — repoint the child entry so status
            # reads and the merge step reference the live execution.
            run_id = new_state["run_id"]
            epic_run_store.set_child_run(
                epic_run_id, ticket_id, run_id=run_id,
                status=new_state.get("status") or "developing",
            )
            state = await self._wait_for(
                run_id, {"awaiting_approval", "awaiting_clarification"} | _TERMINAL
            )
            status = state.get("status") if state else None
            if status == "awaiting_clarification":
                final = "failed"
                break
            if status == "awaiting_approval":
                # No-worktree retries re-plan from scratch and pause at the gate.
                await runner.approve_run(run_id, workspace_mode="worktree", base_ref=base_ref)
                state = await self._wait_for(run_id, _TERMINAL)
                status = state.get("status") if state else None
            final = status or "failed"

        epic_run_store.set_child_run(epic_run_id, ticket_id, status=final)
        return final


epic_runner = EpicAgentRunner()


async def reconcile_interrupted_epics() -> int:
    """Settle epics left non-terminal by a previous process.

    Called once at startup: a fresh process has no in-memory tasks, so every
    epic still marked planning/developing is an orphan. Reconciling here means
    the epic list and panels never show a stale 'Executing' for a run that is no
    longer alive, and interrupted runs become resumable."""
    reconciled = 0
    for row in epic_run_store.list_epic_runs(limit=500):
        if row.get("status") in {"planning", "developing"}:
            await epic_runner._reconcile_orphan(row["epic_run_id"], row)
            reconciled += 1
    if reconciled:
        logger.info("Reconciled %d interrupted epic run(s) at startup", reconciled)
    return reconciled
