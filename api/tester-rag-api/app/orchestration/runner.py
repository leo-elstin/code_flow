import asyncio
import os
import re
import sqlite3
import subprocess
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.core.config import settings
from app.core.logging_config import get_logger
from app.orchestration.graph import build_graph
from app.orchestration.manual_retry import execute_manual_retry, validate_manual_retry_eligibility
from app.orchestration.state import FeatureRunState, initial_state
from app.services.merge_worktree import MergeValidationError, apply_merge_to_base, validate_merge_request
from app.services.project_ticket_store import set_ticket_run, update_ticket_status_by_run
from app.services.run_index import list_executions as list_indexed_executions
from app.services.run_index import list_runs as list_indexed_runs
from app.services.run_index import sync_missing_from_checkpoints, upsert_run
from app.services.worktree import WorktreeError, prepare_workspace
from app.tools.filesystem import restore_all_baselines

logger = get_logger("runner")

_DART_PATH_RE = re.compile(r"(?:lib|test)/[\w/.-]+\.dart")

# Keywords that indicate a DI registration file is needed but not named explicitly
_DI_KEYWORDS_RE = re.compile(
    r"inject(?:able|ion)|register(?:ed|ation)?|GetIt|dependency.inject|DI\b|"
    r"singleton|@lazySingleton|@injectable|di\.register|sl\.register",
    re.IGNORECASE,
)

# Grep patterns used to locate the DI setup file inside the project
_DI_GREP_PATTERNS = (
    r"registerFactory\|registerSingleton\|registerLazySingleton\|GetIt\.instance",
)


def _find_di_file(project_path: str) -> str | None:
    """Grep the project for GetIt registration calls and return the first matching path."""
    try:
        result = subprocess.run(
            ["grep", "-rl", "--include=*.dart", "-E",
             r"registerFactory|registerSingleton|registerLazySingleton|GetIt\.instance",
             os.path.join(project_path, "lib")],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            rel = os.path.relpath(line.strip(), project_path)
            if rel.startswith("lib") and rel.endswith(".dart"):
                return rel
    except Exception:
        pass
    return None


def _augment_plan_from_verifier(
    plan: dict[str, Any],
    verifier_report: dict[str, Any] | None,
    project_path: str | None = None,
) -> dict[str, Any]:
    """Return a copy of *plan* with wiring files added to files_to_modify when
    the verifier flagged them as missing.

    Two strategies:
    1. Explicit paths: dart paths in issues[].file or regex-matched from required_fixes text.
    2. DI keyword detection: when the verifier mentions injectable/registration/GetIt
       but names no file, grep the project to find the actual DI setup file.
    """
    if not verifier_report:
        return plan

    existing: set[str] = set()
    for key in ("files_to_create", "files_to_modify"):
        for item in plan.get(key, []) or []:
            path = item.get("path") if isinstance(item, dict) else str(item)
            if path:
                existing.add(str(path).lstrip("/"))

    found: set[str] = set()
    all_text = " ".join(
        [
            str(issue.get("reason") or "") if isinstance(issue, dict) else str(issue)
            for issue in (verifier_report.get("issues") or [])
        ]
        + [str(f) for f in (verifier_report.get("required_fixes") or [])]
        + [verifier_report.get("headline") or ""]
    )

    # Strategy 1 — explicit dart paths in structured issues
    for issue in verifier_report.get("issues") or []:
        if isinstance(issue, dict) and issue.get("file"):
            found.add(str(issue["file"]).lstrip("/"))

    # Strategy 1b — dart paths embedded in required_fixes prose
    for match in _DART_PATH_RE.findall(all_text):
        found.add(match.lstrip("/"))

    # Strategy 2 — DI keyword detection → grep for the actual registration file
    if project_path and _DI_KEYWORDS_RE.search(all_text):
        di_file = _find_di_file(project_path)
        if di_file and di_file not in existing:
            found.add(di_file)
            logger.info("DI keyword detected in verifier report; found registration file: %s", di_file)

    new_files = [p for p in sorted(found) if p and p not in existing]
    if not new_files:
        return plan

    augmented = dict(plan)
    augmented["files_to_modify"] = list(plan.get("files_to_modify") or []) + [
        {"path": p, "reason": "Added by retry: verifier flagged this file as missing"}
        for p in new_files
    ]
    logger.info("Augmented retry plan with %d file(s) from verifier report: %s", len(new_files), new_files)
    return augmented


def _enable_wal(db_path: str) -> None:
    """Persist WAL journal mode on a SQLite DB so concurrent readers/writers
    (the async checkpointer plus frequent get_state calls) don't lock each other.
    WAL is stored in the DB header, so setting it once via any connection is enough."""
    try:
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("Could not enable WAL on %s: %s", db_path, exc)


class CodeAgentRunner:
    def __init__(self) -> None:
        os.makedirs(settings.CODE_AGENT_DATA_DIR, exist_ok=True)
        os.makedirs(settings.CODE_AGENT_WORKTREES_DIR, exist_ok=True)
        _enable_wal(settings.CODE_AGENT_CHECKPOINT_DB)
        self._graph_builder = build_graph()
        self._tasks: dict[str, asyncio.Task] = {}

    def _cancel_task(self, run_id: str) -> None:
        """Cancel the currently running task for the given run_id if it exists."""
        task = self._tasks.get(run_id)
        if task and not task.done():
            task.cancel()
        self._tasks.pop(run_id, None)

    def _revert_workspace(
        self, project_path: str, worktree_path: str | None, workspace_mode: str, run_id: str
    ) -> None:
        """Discard changes in the workspace."""
        if workspace_mode == "worktree" and worktree_path:
            import subprocess

            if os.path.exists(worktree_path):
                from app.services.worktree import remove_worktree

                remove_worktree(project_path, worktree_path)
            proc = subprocess.run(
                ["git", "-C", project_path, "branch", "-D", f"agent/{run_id}"], capture_output=True
            )
            if proc.returncode != 0:
                logger.warning("Failed to delete branch agent/%s: %s", run_id, proc.stderr)
        elif workspace_mode == "in_place":
            import subprocess

            subprocess.run(
                ["git", "-C", project_path, "reset", "--hard", "HEAD"], capture_output=True
            )
            subprocess.run(["git", "-C", project_path, "clean", "-fd"], capture_output=True)

    def _reap_tasks(self, *, exclude: str | None = None) -> None:
        """Drop references to finished run tasks so the map can't grow without bound."""
        for run_id in [
            rid for rid, task in self._tasks.items() if task.done() and rid != exclude
        ]:
            self._tasks.pop(run_id, None)

    @asynccontextmanager
    async def _checkpointer(self) -> AsyncIterator[AsyncSqliteSaver]:
        async with AsyncSqliteSaver.from_conn_string(settings.CODE_AGENT_CHECKPOINT_DB) as saver:
            yield saver

    def _config(self, run_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": run_id}}

    async def _compile(self, checkpointer: AsyncSqliteSaver):
        # Pause after planner for human approval. Do not interrupt before dev:
        # verifier retries route dev -> verifier again and must not require a
        # second manual resume.
        return self._graph_builder.compile(
            checkpointer=checkpointer,
            interrupt_after=["planner"],
        )

    def _task_running(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        return task is not None and not task.done()

    async def wait_for_task(self, run_id: str, timeout: float) -> None:
        """Wait until the run's current in-process asyncio task finishes.

        The per-run task ends exactly at the planner interrupt and again at a
        terminal state, so callers (e.g. the epic runner) can await a phase
        transition instead of polling. No-op when no task is registered — the
        run may live in another process; callers must re-check state. The task
        is shielded so a timeout here never cancels the run itself."""
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except Exception:  # noqa: BLE001 — run errors surface via run state
            pass

    async def _patch_state(self, run_id: str, patch: dict[str, Any]) -> FeatureRunState | None:
        async with self._checkpointer() as checkpointer:
            graph = await self._compile(checkpointer)
            config = self._config(run_id)
            snapshot = await graph.aget_state(config)
            as_node = snapshot.next[0] if snapshot.next else ("planner" if snapshot.values else None)
            
            if as_node:
                await graph.aupdate_state(config, patch, as_node=as_node)
            else:
                await graph.aupdate_state(config, patch)
                
            snapshot = await graph.aget_state(config)
            if snapshot.values:
                state = snapshot.values  # type: ignore[assignment]
                upsert_run(state)
                if state.get("run_id") and state.get("status"):
                    update_ticket_status_by_run(state["run_id"], state["status"])
                    # Also update Jira ticket status if applicable
                    try:
                        from app.services.jira_sync import update_jira_status_for_run
                        update_jira_status_for_run(state["run_id"], state["status"])
                    except Exception:
                        logger.warning(
                            "Failed to update Jira status for run %s",
                            state["run_id"],
                            exc_info=True,
                        )
                return state  # type: ignore[return-value]
        return None

    async def _mark_failed(self, run_id: str, error: str) -> None:
        async with self._checkpointer() as checkpointer:
            graph = await self._compile(checkpointer)
            config = self._config(run_id)
            snapshot = await graph.aget_state(config)
            as_node = snapshot.next[0] if snapshot.next else ("planner" if snapshot.values else None)
            
            try:
                if as_node:
                    await graph.aupdate_state(
                        config,
                        {
                            "status": "failed",
                            "error": error,
                            "messages": [{"role": "system", "content": error}],
                        },
                        as_node=as_node,
                    )
                else:
                    await graph.aupdate_state(
                        config,
                        {
                            "status": "failed",
                            "error": error,
                            "messages": [{"role": "system", "content": error}],
                        },
                    )
            except Exception as e:
                logger.warning(f"Could not aupdate_state in _mark_failed for {run_id}: {e}")
                
            snapshot = await graph.aget_state(config)
            if snapshot.values:
                upsert_run(snapshot.values)
                run_id_val = snapshot.values.get("run_id")
                status_val = snapshot.values.get("status")
                if run_id_val and status_val:
                    update_ticket_status_by_run(run_id_val, status_val)

    async def _run_graph(
        self,
        run_id: str,
        invoke: Callable[[Any], Awaitable[Any]],
    ) -> None:
        try:
            async with self._checkpointer() as checkpointer:
                graph = await self._compile(checkpointer)
                await invoke(graph)
        except Exception as exc:
            logger.exception("Code agent run %s failed", run_id)
            await self._mark_failed(run_id, str(exc))
        finally:
            await self.get_state(run_id)
            self._reap_tasks(exclude=run_id)

    async def _enrich_referenced_tickets(
        self,
        run_id: str,
        *,
        own_key: str | None,
        raw_description: str,
        acceptance_criteria: list[str],
        linked_issues_context: str | None,
    ) -> str | None:
        """Append auto-fetched bodies of Jira tickets named in the ticket text to
        the linked-issues context. Runs the (blocking) Jira fetch off the event
        loop and never fails the run — on any error the original context stands."""
        try:
            from app.services.jira_service import build_referenced_tickets_context

            ref_text = "\n".join([raw_description, *acceptance_criteria])
            referenced = await asyncio.to_thread(
                build_referenced_tickets_context,
                ref_text,
                exclude={own_key} if own_key else set(),
            )
        except Exception as exc:  # noqa: BLE001 — enrichment is best-effort
            logger.warning("Referenced-ticket enrichment failed for run %s: %s", run_id, exc)
            return linked_issues_context

        if not referenced:
            return linked_issues_context
        logger.info("Run %s enriched with referenced Jira ticket(s)", run_id)
        existing = (linked_issues_context or "").strip()
        return f"{existing}\n\n{referenced}".strip() if existing else referenced

    async def _auto_approve_when_ready(self, run_id: str) -> None:
        """Approve a single run's plan automatically once planning finishes.

        Waits for the planning task to reach the interrupt, then approves if the
        plan is ready. No-op if the planner asked clarification questions (the
        user must answer those) or the run already moved on."""
        try:
            await self.wait_for_task(run_id, timeout=3600)
        except Exception:  # noqa: BLE001
            pass
        state = await self.get_state(run_id)
        if (state or {}).get("status") != "awaiting_approval":
            return
        try:
            await self.approve_run(run_id, workspace_mode="worktree")
            logger.info("Auto-approved run %s (plan ready)", run_id)
        except Exception:  # noqa: BLE001
            logger.warning("Auto-approve failed for run %s", run_id, exc_info=True)

    async def start_run(
        self,
        user_request: str,
        project_path: str,
        ticket_id: int | None = None,
        *,
        auto_approve: bool | None = None,
    ) -> str:
        run_id = str(uuid.uuid4())
        logger.info("Starting run run_id=%s project_path=%s", run_id, project_path)
        state = initial_state(run_id, user_request, project_path)
        state["max_iterations"] = settings.CODE_AGENT_MAX_VERIFIER_ITERATIONS
        if ticket_id is not None:
            state["ticket_id"] = ticket_id
            # Populate Jira-specific context if ticket is from Jira
            from app.services.project_ticket_store import get_ticket as _get_ticket
            ticket_data = _get_ticket(ticket_id)
            if ticket_data and ticket_data.get("source") == "jira":
                try:
                    import json as _json
                    jira_meta = _json.loads(ticket_data.get("description") or "{}")
                    state["attachment_paths"] = jira_meta.get("attachment_paths", [])
                    state["linked_issues_context"] = jira_meta.get("linked_issues_context")
                    state["acceptance_criteria_hint"] = jira_meta.get("acceptance_criteria", [])
                    # Pull in the bodies of any Jira tickets named in this
                    # ticket's own text (rules often live in a referenced ticket
                    # the planner can't otherwise see), so it need not ask.
                    if settings.CODE_AGENT_RESOLVE_REFERENCED_TICKETS:
                        state["linked_issues_context"] = await self._enrich_referenced_tickets(
                            run_id,
                            own_key=ticket_data.get("jira_key"),
                            raw_description=jira_meta.get("raw_description") or "",
                            acceptance_criteria=jira_meta.get("acceptance_criteria") or [],
                            linked_issues_context=state.get("linked_issues_context"),
                        )
                except (ValueError, TypeError):
                    pass  # description is plain text, not Jira structured JSON

        async with self._checkpointer() as checkpointer:
            graph = await self._compile(checkpointer)
            await graph.aupdate_state(self._config(run_id), state)
        upsert_run(state)
        if ticket_id is not None:
            set_ticket_run(ticket_id, run_id, status="planning")
        logger.info("Run checkpoint seeded run_id=%s status=planning", run_id)

        async def _invoke(graph) -> None:
            await graph.ainvoke(None, self._config(run_id))

        task = asyncio.create_task(self._run_graph(run_id, _invoke))
        self._tasks[run_id] = task
        logger.info("Run started run_id=%s status=planning", run_id)

        auto = settings.CODE_AGENT_AUTO_APPROVE if auto_approve is None else bool(auto_approve)
        if auto:
            asyncio.create_task(self._auto_approve_when_ready(run_id))
        return run_id

    async def get_state(self, run_id: str) -> FeatureRunState | None:
        async with self._checkpointer() as checkpointer:
            graph = await self._compile(checkpointer)
            snapshot = await graph.aget_state(self._config(run_id))
            if snapshot.values:
                state = snapshot.values  # type: ignore[assignment]
                upsert_run(state)
                if state.get("run_id") and state.get("status"):
                    update_ticket_status_by_run(state["run_id"], state["status"])
                    # Also update Jira ticket status if applicable
                    try:
                        from app.services.jira_sync import update_jira_status_for_run
                        update_jira_status_for_run(state["run_id"], state["status"])
                    except Exception:
                        logger.warning(
                            "Failed to update Jira status for run %s",
                            state["run_id"],
                            exc_info=True,
                        )
                return state  # type: ignore[return-value]
        return None

    async def list_runs(
        self,
        *,
        project_path: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        await sync_missing_from_checkpoints(self.get_state)
        return list_indexed_runs(project_path=project_path, limit=limit)

    async def reject_run(self, run_id: str, feedback: str | None = None) -> FeatureRunState | None:
        logger.info("Rejecting run run_id=%s", run_id)
        async with self._checkpointer() as checkpointer:
            graph = await self._compile(checkpointer)
            await graph.aupdate_state(
                self._config(run_id),
                {
                    "status": "rejected",
                    "rejected": True,
                    "messages": [{"role": "human", "content": feedback or "Plan rejected."}],
                },
            )
            snapshot = await graph.aget_state(self._config(run_id))
            logger.info("Run rejected run_id=%s", run_id)
            if snapshot.values:
                upsert_run(snapshot.values)
                run_id_val = snapshot.values.get("run_id")
                status_val = snapshot.values.get("status")
                if run_id_val and status_val:
                    update_ticket_status_by_run(run_id_val, status_val)
            return snapshot.values  # type: ignore[return-value]

    async def clarify_run(
        self,
        run_id: str,
        answers: list[dict[str, Any]],
    ) -> FeatureRunState | None:
        """Re-run the planner with the user's answers to clarification questions."""
        from app.agents.roles.planner import run_planner

        logger.info("Clarifying run run_id=%s answers=%d", run_id, len(answers))
        current = await self.get_state(run_id)
        if not current:
            return None
        if current.get("status") != "awaiting_clarification":
            raise ValueError(f"Run {run_id} is not awaiting clarification (status={current.get('status')})")

        result = await run_planner(
            current["user_request"],
            current["project_path"],
            run_id=run_id,
            attachment_paths=current.get("attachment_paths") or None,
            linked_issues_context=current.get("linked_issues_context"),
            acceptance_criteria_hint=current.get("acceptance_criteria_hint") or None,
            clarification_answers=answers,
        )

        patch: dict[str, Any] = {
            "status": "awaiting_approval",
            "clarification_answers": answers,
            "clarification_questions": [],
            "context_bundle": result["context_bundle"],
            "plan": result["plan"],
            "acceptance_criteria": result["acceptance_criteria"],
            "messages": result["messages"],
        }
        return await self._patch_state(run_id, patch)

    async def approve_run(
        self,
        run_id: str,
        *,
        workspace_mode: str = "worktree",
        base_ref: str | None = None,
    ) -> FeatureRunState | None:
        logger.info("Approving run run_id=%s workspace_mode=%s", run_id, workspace_mode)
        current = await self.get_state(run_id)
        if not current:
            return None
        if current.get("status") != "awaiting_approval":
            return current

        mode = workspace_mode if workspace_mode in {"worktree", "in_place"} else "worktree"

        try:
            # prepare_workspace runs `pub get` (up to minutes); keep it off the
            # event loop so the API and other runs stay responsive.
            # base_ref lets an epic-run branch this child's worktree off the
            # shared integration branch instead of the project's HEAD.
            workspace = await asyncio.to_thread(
                prepare_workspace,
                current["project_path"],
                run_id=run_id,
                workspace_mode=mode,  # type: ignore[arg-type]
                base_ref=base_ref,
            )
            logger.info(
                "Workspace ready run_id=%s mode=%s path=%s",
                run_id,
                workspace.get("workspace_mode"),
                workspace["worktree_path"],
            )
        except WorktreeError as exc:
            logger.error("Workspace preparation failed run_id=%s error=%s", run_id, exc)
            await self._mark_failed(run_id, str(exc))
            return await self.get_state(run_id)

        async with self._checkpointer() as checkpointer:
            graph = await self._compile(checkpointer)
            await graph.aupdate_state(
                self._config(run_id),
                {
                    "approved": True,
                    "worktree_path": workspace["worktree_path"],
                    "workspace_mode": workspace.get("workspace_mode", mode),
                    "status": "developing",
                },
                as_node="planner",
            )

        async def _invoke(graph) -> None:
            await graph.ainvoke(None, self._config(run_id))

        task = asyncio.create_task(self._run_graph(run_id, _invoke))
        self._tasks[run_id] = task
        logger.info("Run resumed after approval run_id=%s status=developing", run_id)
        return await self.get_state(run_id)

    async def _run_manual_retry(self, run_id: str) -> None:
        try:

            async def get_state() -> FeatureRunState | None:
                return await self.get_state(run_id)

            async def patch_state(patch: dict[str, Any]) -> None:
                await self._patch_state(run_id, patch)

            await execute_manual_retry(get_state=get_state, patch_state=patch_state)
        except Exception as exc:
            logger.exception("Manual retry failed run_id=%s", run_id)
            await self._mark_failed(run_id, str(exc))
        finally:
            await self.get_state(run_id)
            self._reap_tasks(exclude=run_id)

    def _revert_worktree_edits(
        self,
        worktree_path: str,
        *,
        run_id: str | None = None,
        workspace_mode: str = "worktree",
    ) -> None:
        """Undo the previous execution's edits so the next attempt starts clean.

        In-place runs edit the user's real checkout, so a blanket reset/clean
        would destroy their uncommitted work. There we restore only the files
        the agent touched from the per-run baseline snapshots, leaving every
        other change alone. Worktree runs are isolated, so the original
        reset --hard + clean is safe and faster."""
        import subprocess

        if not worktree_path or not os.path.exists(worktree_path):
            return

        if workspace_mode == "in_place":
            baseline_dir = (
                os.path.join(settings.CODE_AGENT_DATA_DIR, "baselines", run_id)
                if run_id
                else None
            )
            if baseline_dir:
                restored = restore_all_baselines(baseline_dir, worktree_path)
                logger.info(
                    "Reverted in-place run %s via %d baseline snapshot(s)",
                    run_id, len(restored),
                )
            return

        subprocess.run(
            ["git", "-C", worktree_path, "reset", "--hard", "HEAD"],
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", worktree_path, "clean", "-fd"],
            capture_output=True,
        )

    async def list_executions(self, run_id: str) -> list[dict[str, Any]]:
        """All attempts sharing a root with run_id, each with isolated tokens."""
        await self.get_state(run_id)
        return list_indexed_executions(run_id)

    def _clear_checkpoints(self, run_id: str) -> None:
        try:
            conn = sqlite3.connect(settings.CODE_AGENT_CHECKPOINT_DB)
            try:
                with conn:
                    conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (run_id,))
                    conn.execute("DELETE FROM writes WHERE thread_id = ?", (run_id,))
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.warning("Could not clear checkpoints for %s: %s", run_id, exc)

    async def retry_run(self, run_id: str) -> FeatureRunState | None:
        """Start a NEW execution (fresh run_id) from base for a failed run.

        Each retry is its own execution linked to the original via root_run_id, so
        its token usage is isolated (token activity is keyed by run_id) and it is
        listed separately in the UI. If a worktree exists we reuse it but first
        revert the previous attempt's edits, then re-run dev->verify from base
        using the already-approved plan. If the run failed before a worktree
        existed (e.g. during planning) the new execution re-plans from scratch."""
        current = await self.get_state(run_id)
        if not current:
            return None
        if self._task_running(run_id):
            raise ValueError("Run already in progress")
        if current.get("status") != "failed":
            raise ValueError("Retry is only available for failed runs")

        root_run_id = current.get("root_run_id") or run_id
        attempt = int(current.get("attempt") or 1) + 1
        new_run_id = str(uuid.uuid4())
        ticket_id = current.get("ticket_id")
        lineage = {
            "root_run_id": root_run_id,
            "parent_run_id": run_id,
            "attempt": attempt,
        }
        logger.info(
            "Retry as new execution root=%s parent=%s new=%s attempt=%d",
            root_run_id,
            run_id,
            new_run_id,
            attempt,
        )

        worktree_path = current.get("worktree_path")
        if worktree_path:
            # Reuse the current tree, reverting only the previous run's changes.
            await asyncio.to_thread(
                self._revert_worktree_edits,
                worktree_path,
                run_id=run_id,
                workspace_mode=current.get("workspace_mode") or "worktree",
            )

            seed: FeatureRunState = initial_state(
                new_run_id, current["user_request"], current["project_path"]
            )
            seed.update(lineage)
            seed["max_iterations"] = settings.CODE_AGENT_MAX_VERIFIER_ITERATIONS
            seed["plan"] = _augment_plan_from_verifier(
                current.get("plan") or {},
                current.get("verifier_report"),
                project_path=current.get("project_path"),
            )
            seed["acceptance_criteria"] = current.get("acceptance_criteria", [])
            seed["context_bundle"] = current.get("context_bundle", {})
            seed["status"] = "awaiting_approval"
            if ticket_id is not None:
                seed["ticket_id"] = ticket_id

            async with self._checkpointer() as checkpointer:
                graph = await self._compile(checkpointer)
                # Seed the planning snapshot, then resume past planner straight
                # into dev (mirrors start_run -> approve_run, minus re-planning).
                await graph.aupdate_state(self._config(new_run_id), seed)
                await graph.aupdate_state(
                    self._config(new_run_id),
                    {
                        "approved": True,
                        "worktree_path": worktree_path,
                        "workspace_mode": current.get("workspace_mode") or "worktree",
                        "status": "developing",
                    },
                    as_node="planner",
                )
            upsert_run({**seed, **lineage, "run_id": new_run_id, "status": "developing"})
            new_status = "developing"
        else:
            # No worktree yet (failed in planning): full fresh execution.
            seed = initial_state(
                new_run_id, current["user_request"], current["project_path"]
            )
            seed.update(lineage)
            seed["max_iterations"] = settings.CODE_AGENT_MAX_VERIFIER_ITERATIONS
            if ticket_id is not None:
                seed["ticket_id"] = ticket_id

            async with self._checkpointer() as checkpointer:
                graph = await self._compile(checkpointer)
                await graph.aupdate_state(self._config(new_run_id), seed)
            upsert_run(seed)
            new_status = "planning"

        if ticket_id is not None:
            set_ticket_run(ticket_id, new_run_id, status=new_status)

        async def _invoke(graph) -> None:
            await graph.ainvoke(None, self._config(new_run_id))

        task = asyncio.create_task(self._run_graph(new_run_id, _invoke))
        self._tasks[new_run_id] = task
        return await self.get_state(new_run_id)

    async def resume_run(self, run_id: str) -> FeatureRunState | None:
        current = await self.get_state(run_id)
        if not current:
            return None
        if self._task_running(run_id):
            raise ValueError("Run already in progress")

        status = current.get("status")
        if status not in {"planning", "developing", "verifying", "qa"}:
            raise ValueError(f"Cannot resume run in status {status}")

        logger.info("Resuming run run_id=%s from status=%s", run_id, status)

        async def _invoke(graph) -> None:
            await graph.ainvoke(None, self._config(run_id))

        task = asyncio.create_task(self._run_graph(run_id, _invoke))
        self._tasks[run_id] = task
        return await self.get_state(run_id)

    async def revert_run(self, run_id: str) -> FeatureRunState | None:
        """Cancel current task, delete worktree/branch, clear checkpoints, and restart run from planning."""
        current = await self.get_state(run_id)
        if not current:
            return None

        logger.info("Reverting run run_id=%s", run_id)
        self._cancel_task(run_id)

        workspace_mode = current.get("workspace_mode") or "worktree"
        project_path = current.get("project_path")
        if project_path:
            # We must use the background thread to safely block without tying up asyncio loop
            # since git operations can be slow.
            await asyncio.to_thread(
                self._revert_workspace,
                project_path,
                current.get("worktree_path"),
                workspace_mode,
                run_id,
            )

        logger.info("Cleared workspace for run_id=%s, now clearing checkpoints", run_id)
        self._clear_checkpoints(run_id)

        # Re-seed the initial planning state on top of the cleared checkpoints
        state = initial_state(run_id, current["user_request"], current["project_path"])
        state["max_iterations"] = settings.CODE_AGENT_MAX_VERIFIER_ITERATIONS
        if current.get("ticket_id") is not None:
            state["ticket_id"] = current["ticket_id"]

        async with self._checkpointer() as checkpointer:
            graph = await self._compile(checkpointer)
            await graph.aupdate_state(self._config(run_id), state)

        upsert_run(state)
        if state.get("ticket_id") is not None:
            set_ticket_run(state["ticket_id"], run_id, status="planning")

        async def _invoke(graph) -> None:
            await graph.ainvoke(None, self._config(run_id))

        task = asyncio.create_task(self._run_graph(run_id, _invoke))
        self._tasks[run_id] = task

        return await self.get_state(run_id)

    async def merge_run(
        self,
        run_id: str,
        *,
        target_branch: str | None = None,
        commit_message: str | None = None,
    ) -> FeatureRunState | None:
        current = await self.get_state(run_id)
        if not current:
            return None

        validate_merge_request(current)

        project_path = current["project_path"]
        worktree_path = current.get("worktree_path")
        # Git commit/merge shells out; run it in a thread to avoid blocking the loop.
        result = await asyncio.to_thread(
            apply_merge_to_base,
            run_id=run_id,
            project_path=project_path,
            worktree_path=worktree_path or "",
            target_branch=target_branch,
            commit_message=commit_message,
        )

        patch: dict[str, Any] = {"merge_report": result}
        if result.get("applied"):
            patch["messages"] = [
                {
                    "role": "system",
                    "content": f"Merged agent/{run_id} into {result.get('target_branch')}",
                }
            ]
            return await self._patch_state(run_id, patch)

        await self._patch_state(run_id, patch)
        raise GitMergeConflictError(result)


class GitMergeConflictError(Exception):
    def __init__(self, merge_report: dict[str, Any]) -> None:
        self.merge_report = merge_report
        super().__init__(merge_report.get("error") or "Merge conflict")


runner = CodeAgentRunner()
