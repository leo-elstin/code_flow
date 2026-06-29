from langgraph.graph import END, START, StateGraph

from app.agents.roles.dev import run_dev
from app.agents.roles.planner import run_planner
from app.agents.roles.qa import run_qa
from app.agents.roles.verifier import run_verifier
from app.core.config import settings
from app.core.logging_config import get_logger
from app.orchestration.state import FeatureRunState
from app.services.run_activity import append_activity

logger = get_logger("graph")


async def planner_node(state: FeatureRunState) -> dict:
    run_id = state.get("run_id", "")
    logger.info("Planner starting run_id=%s", run_id)
    try:
        result = await run_planner(
            state["user_request"],
            state["project_path"],
            run_id=run_id or None,
            attachment_paths=state.get("attachment_paths") or None,
            linked_issues_context=state.get("linked_issues_context"),
            acceptance_criteria_hint=state.get("acceptance_criteria_hint") or None,
        )
        logger.info("Planner success run_id=%s status=awaiting_approval", run_id)
        return {
            "status": "awaiting_approval",
            "context_bundle": result["context_bundle"],
            "plan": result["plan"],
            "acceptance_criteria": result["acceptance_criteria"],
            "messages": result["messages"],
        }
    except Exception:
        logger.exception("Planner error run_id=%s", run_id)
        raise


async def dev_node(state: FeatureRunState) -> dict:
    run_id = state.get("run_id", "")
    worktree_path = state.get("worktree_path")
    if not worktree_path:
        logger.error("Dev failed run_id=%s reason=no_worktree", run_id)
        return {"status": "failed", "error": "Worktree not created", "messages": []}

    iteration = state.get("iteration", 0)
    if run_id:
        append_activity(
            run_id,
            type="status",
            phase="dev",
            title=f"Entering development (iteration {iteration})",
        )

    logger.info("Dev node run_id=%s iteration=%s", run_id, iteration)
    try:
        result = await run_dev(
            plan=state.get("plan", {}),
            context_bundle=state.get("context_bundle", {}),
            worktree_path=worktree_path,
            project_path=state["project_path"],
            verifier_report=state.get("verifier_report") or None,
            run_id=run_id or None,
        )
        logger.info(
            "Dev success run_id=%s file_changes=%d",
            run_id,
            len(result.get("file_changes", [])),
        )
        return {
            "status": "verifying",
            "file_changes": result.get("file_changes", []),
            "truncated": result.get("truncated", False),
            "messages": result.get("messages", []),
        }
    except Exception:
        logger.exception("Dev error run_id=%s", run_id)
        raise


async def verifier_node(state: FeatureRunState) -> dict:
    run_id = state.get("run_id", "")
    worktree_path = state.get("worktree_path")
    if not worktree_path:
        logger.error("Verifier failed run_id=%s reason=no_worktree", run_id)
        return {"status": "failed", "error": "Worktree not created", "messages": []}

    iteration = state.get("iteration", 0)
    max_iterations = state.get("max_iterations") or settings.CODE_AGENT_MAX_VERIFIER_ITERATIONS
    if run_id:
        append_activity(
            run_id,
            type="status",
            phase="verifier",
            title=f"Entering verification (iteration {iteration + 1}/{max_iterations})",
        )
    logger.info(
        "Verifier node run_id=%s iteration=%d/%d",
        run_id,
        iteration,
        max_iterations,
    )

    try:
        result = await run_verifier(
            plan=state.get("plan", {}),
            acceptance_criteria=state.get("acceptance_criteria", []),
            worktree_path=worktree_path,
            file_changes=state.get("file_changes", []),
            project_path=state.get("project_path"),
            run_id=run_id or None,
        )
        report = result["verifier_report"]

        if report.get("passed"):
            logger.info("Verifier success run_id=%s -> qa", run_id)
            return {
                "status": "qa",
                "verifier_report": report,
                "diffs": result.get("diffs", []),
                "messages": result.get("messages", []),
            }

        iteration += 1
        if iteration >= max_iterations:
            logger.error(
                "Verifier exhausted run_id=%s iteration=%d/%d",
                run_id,
                iteration,
                max_iterations,
            )
            return {
                "status": "failed",
                "iteration": iteration,
                "verifier_report": report,
                "diffs": result.get("diffs", []),
                "error": "Verifier retries exhausted",
                "messages": result.get("messages", []),
            }

        logger.warning(
            "Verifier retry run_id=%s next_iteration=%d/%d",
            run_id,
            iteration,
            max_iterations,
        )
        return {
            "status": "developing",
            "iteration": iteration,
            "verifier_report": report,
            "diffs": result.get("diffs", []),
            "messages": result.get("messages", []),
        }
    except Exception:
        logger.exception("Verifier error run_id=%s", run_id)
        raise


async def qa_node(state: FeatureRunState) -> dict:
    run_id = state.get("run_id", "")
    worktree_path = state.get("worktree_path")
    if not worktree_path:
        logger.error("QA failed run_id=%s reason=no_worktree", run_id)
        return {"status": "failed", "error": "Worktree not created", "messages": []}

    if run_id:
        append_activity(
            run_id,
            type="status",
            phase="qa",
            title="Entering QA",
        )

    logger.info("QA starting run_id=%s", run_id)
    try:
        result = await run_qa(
            plan=state.get("plan", {}),
            acceptance_criteria=state.get("acceptance_criteria", []),
            diffs=state.get("diffs", []),
            project_path=state["project_path"],
            worktree_path=worktree_path,
            run_id=run_id or None,
        )
        logger.info("QA success run_id=%s status=completed", run_id)
        return {
            "status": "completed",
            "qa_report": result.get("qa_report", {}),
            "messages": result.get("messages", []),
        }
    except Exception:
        logger.exception("QA error run_id=%s", run_id)
        raise


def route_after_verifier(state: FeatureRunState) -> str:
    status = state.get("status")
    if status == "qa":
        return "qa"
    if status == "developing":
        return "dev"
    return END


def build_graph() -> StateGraph:
    graph = StateGraph(FeatureRunState)
    graph.add_node("planner", planner_node)
    graph.add_node("dev", dev_node)
    graph.add_node("verifier", verifier_node)
    graph.add_node("qa", qa_node)

    graph.add_edge(START, "planner")
    graph.add_edge("planner", "dev")
    graph.add_edge("dev", "verifier")
    graph.add_conditional_edges("verifier", route_after_verifier, {"qa": "qa", "dev": "dev", END: END})
    graph.add_edge("qa", END)
    return graph
