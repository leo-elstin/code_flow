# code_agent

Monorepo for AI-assisted Flutter development: a FastAPI backend with RAG and a multi-agent code workflow, plus a Next.js web UI.

## Repository layout

```
api/tester-rag-api/   # FastAPI backend — RAG pipeline and LangGraph code agent
ui/code_agent_web/    # Next.js web UI for driving agent runs
```

See each package's README for setup and API details:

- [api/tester-rag-api/README.md](api/tester-rag-api/README.md)
- [ui/code_agent_web/README.md](ui/code_agent_web/README.md)

## Quick start

### Backend

```bash
cd api/tester-rag-api
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env   # add OPENAI_API_KEY and other settings
python main.py
```

API docs: `http://localhost:8000/docs`

### Web UI

```bash
cd ui/code_agent_web
npm install
npm run dev
```

Open `http://localhost:3000` and point the UI at the API (`http://localhost:8000`).

## What it does

- **RAG pipeline** — ingest Dart/Python codebases, embed chunks in Qdrant, and retrieve context for test generation.
- **Code agent** — a LangGraph workflow (planner → dev → verifier → QA) that implements Flutter features in isolated git worktrees, with human approval after planning.

Target Flutter projects can include an `AGENTS.md` at their root to describe folder layout and conventions for the planner and dev agents.
