# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

Monorepo with two packages:

| Path | Role |
|------|------|
| `api/tester-rag-api/` | FastAPI backend — RAG pipeline + LangGraph multi-agent workflow |
| `ui/code_agent_web/` | Next.js 14 frontend (App Router) |

## Commands

### Backend (`api/tester-rag-api/`)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env        # add OPENAI_API_KEY and other settings
python main.py              # or: uvicorn main:app --reload --port 8000
pytest                      # run all tests
pytest tests/test_code_agent.py   # run a single test file
```

### Frontend (`ui/code_agent_web/`)
```bash
npm install
npm run dev                 # dev server on http://localhost:3000
npm run build && npm start  # production
```

Prerequisites for full functionality: Qdrant running at `http://localhost:6333`, `dart`/`flutter` on PATH for code-agent verification.

## Architecture

### Code agent pipeline

The core workflow is a **LangGraph StateGraph** (`app/orchestration/graph.py`) with four nodes:

```
START → planner → [interrupt] → dev → verifier ⇄ dev (retry up to max_iterations) → qa → END
```

- **Planner** (`app/agents/roles/planner.py`) — discovers context, produces a plan and acceptance criteria, then halts at `interrupt_after=["planner"]` for human approval.
- **Dev** (`app/agents/roles/dev.py`) — implements the plan by writing files into a git worktree.
- **Verifier** (`app/agents/roles/verifier.py`) — runs `dart analyze`, checks acceptance criteria, routes back to `dev` on failure (up to `max_iterations`), or forward to `qa` on pass.
- **QA** (`app/agents/roles/qa.py`) — final diff review, produces `qa_report`, status → `completed`.

**State** is persisted via `AsyncSqliteSaver` (WAL mode) at `CODE_AGENT_CHECKPOINT_DB`. The `CodeAgentRunner` singleton (`app/orchestration/runner.py`) owns run lifecycle (start, approve, reject, retry, resume, merge). Each user-triggered retry creates a **new execution** with its own `run_id`, linked to the original via `root_run_id`.

### Workspace modes
- `worktree` (default) — `prepare_workspace` creates a `git worktree` + `agent/<run_id>` branch, runs `flutter pub get` in a thread.
- `in_place` — modifies the project directory directly (no worktree).

Worktree and branch cleanup lives in `app/services/worktree.py`.

### Epic runner

`app/orchestration/epic_runner.py` orchestrates a Jira Epic by running all child stories via `CodeAgentRunner`. It uses the `epic_planner` to build a dependency graph; stories in the same topological level run concurrently, and completed children are merged into a shared integration branch (`agent/epic-<id>`) before the next level starts.

### PO Agent pipeline

A separate pipeline (`app/orchestration/po_graph.py` / `po_runner.py`) handles product-owner intake. Unlike the code agent it does **not** use LangGraph checkpointer — state is persisted in SQLite via `app/services/po_session_store.py`. The graph: `mode_classifier → context_analyzer → [brainstorm | intake] → question_generator → [pause for human reply] → readiness_check → story_generator / brief_generator`.

Modes:
- `brainstorm` — iterative Q&A to refine vague ideas into draft user stories.
- `intake` — validates a well-formed requirement and produces structured output immediately.

### Data persistence

| Store | Path | Contents |
|-------|------|----------|
| `project_ticket_store` (SQLite) | `data/code_agent_projects.db` | Projects, tickets, Jira metadata |
| LangGraph checkpoint (SQLite) | `data/code_agent_runs.db` | Run state, messages |
| `run_index` (SQLite) | same DB | Denormalized run summaries for fast list queries |
| `epic_run_store` (SQLite) | `data/epic_runs.db` | Epic coordination state |
| `po_session_store` (SQLite) | `data/po_sessions.db` | PO session state and messages |
| Qdrant | `http://localhost:6333` | Code embeddings for RAG |

### Jira integration

`app/services/jira_sync.py` pulls Jira issues into the local ticket store and pushes status transitions back. Jira-sourced tickets store structured metadata (AC, linked issues, attachment paths) as a JSON blob in the `description` column, which the runner unpacks into `FeatureRunState` fields (`attachment_paths`, `linked_issues_context`, `acceptance_criteria_hint`).

### Agent tools

All file-access tools go through `app/tools/path_guard.py` to prevent traversal outside the project root. Key tool sets: `app/tools/filesystem_tools.py`, `app/tools/grep_tools.py`, `app/tools/dart_tools.py`, `app/tools/git_tools.py`, `app/tools/lsp_tools.py`.

### Activity & token tracking

`app/services/run_activity.py` appends structured events (status, thinking, tool use, LLM calls, token counts) to a per-run SQLite table. The API streams these via SSE at `/api/code-agent/runs/{id}/events`.

### Frontend structure

The Next.js app is a **single-page application** rooted at `ui/code_agent_web/src/app/page.tsx`. State lives in `page.tsx` and is passed down; there is no Redux or Zustand. Key components:
- `Sidebar` — project selector
- `SidePanel` — ticket list with create/delete/sync-Jira
- `CenterPane` — active run view (plan approval, diffs, verifier report, merge)
- `EpicRunPanel` — epic run progress grid
- `POSessionPage` / `POChatThread` — PO agent chat interface

API calls are centralised in `ui/code_agent_web/src/lib/api-client.ts`; shared types in `src/lib/models.ts`.

## Key conventions

- **Config**: all settings via `app/core/config.py` (`Settings` class reading env vars).
- **Logging**: `get_logger(name)` from `app/core/logging_config.py`; structured `key=value` log lines, not bare `print`.
- **Agent JSON contracts**: each role's system prompt defines the JSON schema of its response. When editing a role, keep the schema aligned with what the graph node unpacks into `FeatureRunState`.
- **API changes** that add new fields must update both `app/api/code_agent/schemas.py` (Pydantic) and `ui/code_agent_web/src/lib/models.ts` (TypeScript interfaces).
- Target Flutter project conventions are loaded from `AGENTS.md` (or `agents.md`) at the project root via `app/services/project_guide.py` — preserve support for both filenames.
