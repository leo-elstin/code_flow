# AGENTS.md — tester-rag-api

Guidance for AI agents working in this repository. Follow these conventions when changing backend code, orchestration, or tooling.

## What this project is

A FastAPI service with two main capabilities:

1. **RAG pipeline** — ingest Flutter/Dart (and Python) codebases, embed chunks in Qdrant, and retrieve context for test generation.
2. **Code agent** — a LangGraph multi-agent workflow that plans, implements, verifies, and QA-reviews Flutter features in isolated git worktrees.

The Flutter desktop UI lives under `ui/code_agent_flutter/`.

## Repository layout

```
app/
  agents/          # Code analysts, parsers, and role agents (planner, dev, verifier, qa)
  api/             # FastAPI routers (ingest, query, context, generate, explorer, code-agent)
  core/            # Settings, logging, database helpers
  lib/             # Vendored tree-sitter-dart bindings
  orchestration/   # LangGraph state, graph, and runner
  services/        # Embeddings, vector store, worktrees, feature discovery
  tools/           # Agent tools: filesystem, grep, dart, git, LSP
main.py            # FastAPI entrypoint
tests/             # Pytest suite
ui/                # Flutter clients
```

## Code agent workflow

The graph in `app/orchestration/graph.py` runs:

```
planner → dev → verifier ⇄ dev (retry) → qa
```

- **Planner** pauses for human approval (`interrupt_after=["planner"]` in the runner).
- **Dev** writes files in a per-run git worktree under `CODE_AGENT_WORKTREES_DIR`.
- **Verifier** runs `dart analyze`, optional `build_runner`, and checks acceptance criteria.
- **QA** produces a final report from diffs and criteria.

When editing agent behavior, keep JSON response contracts in each role's system prompt aligned with the graph's expected state keys.

## Target Flutter projects

Agents that plan or implement features read `AGENTS.md` (or `agents.md`) from the **target Flutter project's root** via `app/services/project_guide.py`. That file describes the Flutter app's folder layout — it is separate from this repository's `AGENTS.md`.

If you change how project guides are loaded, preserve support for both filename variants.

## Conventions

- **Config**: environment variables in `app/core/config.py`; never hardcode API keys.
- **Logging**: use `get_logger()` from `app/core/logging_config.py`, not bare `print` (except legacy ingest paths).
- **Paths**: agent file tools must go through `app/tools/path_guard.py` to block traversal outside the project root.
- **Worktrees**: create/remove via `app/services/worktree.py`; do not hand-roll git worktree commands in routes.
- **Tests**: add pytest cases under `tests/` for new tools, guards, and API behavior. Run with `pytest`.

## Adding a new agent tool

1. Implement the tool under `app/tools/`.
2. Wire it into the relevant role in `app/agents/roles/`.
3. Add tests that cover happy path and failure modes.
4. Keep tool outputs structured (dict/JSON-friendly) for LLM consumption.

## API surface

| Prefix | Purpose |
|--------|---------|
| `/api/ingest` | Embed and index a project |
| `/api/query` | Semantic search over indexed code |
| `/api/context` | Module-level context for generation |
| `/api/generate` | Test/prompt generation |
| `/api/explore` | Project structure and widget trees |
| `/api/code-agent` | Start, approve, reject, and stream agent runs |

## Dependencies

Python deps are declared in `pyproject.toml`. Install with:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

Requires OpenAI API access, Qdrant for RAG endpoints, and `dart`/`flutter` on PATH for code-agent verification.

## What not to do

- Do not commit `.env`, runtime databases, or `app/data/worktrees/` contents.
- Do not rewrite entire Dart files in the dev agent unless the plan requires it — preserve existing imports and unrelated code.
- Do not invent Flutter folder layouts when a target project's `AGENTS.md` defines the structure.
- Minimize scope: match existing patterns in the file you are editing.
