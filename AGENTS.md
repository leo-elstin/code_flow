# AGENTS.md — code_agent

Guidance for AI agents working in this monorepo.

## What this repository is

A two-package workspace:

| Path | Role |
|------|------|
| `api/tester-rag-api/` | FastAPI service: RAG over codebases + LangGraph multi-agent workflow |
| `ui/code_agent_web/` | Next.js frontend for starting, approving, and streaming agent runs |

Package-specific conventions live in each subproject's `AGENTS.md`:

- [api/tester-rag-api/AGENTS.md](api/tester-rag-api/AGENTS.md)
- [ui/code_agent_web/AGENTS.md](ui/code_agent_web/AGENTS.md)

## Working across packages

- **API changes** that affect the UI should update both `api/tester-rag-api/` and `ui/code_agent_web/` in the same change when possible.
- **Default API URL** for local dev is `http://localhost:8000`; the web UI runs on port 3000.
- **Target Flutter projects** (repos the code agent modifies) are separate from this monorepo. They may ship their own `AGENTS.md` or `agents.md` at the project root; the backend loads that via `app/services/project_guide.py`.

## Conventions

- Prefer minimal, focused diffs. Match naming and patterns in the package you are editing.
- Do not commit secrets (`.env`, API keys). Use `.env.example` as the template.
- Do not commit build artifacts (`.venv/`, `node_modules/`, `.next/`, `__pycache__/`).
- Run backend tests with `pytest` from `api/tester-rag-api/`.
- Run the web app with `npm run dev` from `ui/code_agent_web/`.

## Code agent roles

The backend orchestrates four roles in a LangGraph graph:

```
planner → dev → verifier ⇄ dev (retry) → qa
```

Human approval is required after planning. When changing agent behavior, keep JSON response contracts aligned with the graph's expected state keys. See `api/tester-rag-api/app/orchestration/` and `api/tester-rag-api/app/agents/roles/`.
