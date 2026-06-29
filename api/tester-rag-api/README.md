# tester-rag-api

FastAPI backend for AI-assisted Flutter development and test generation. It combines a RAG pipeline over codebases with a multi-agent code agent that plans, implements, and verifies features in isolated git worktrees.

## Features

### RAG pipeline

- **Ingest** — walk a project, parse Dart/Python files, chunk code, and upsert embeddings into Qdrant.
- **Query** — semantic search over indexed snippets.
- **Context** — resolve module-level context (imports, related files) for a natural-language query.
- **Generate** — produce test prompts and generated test code from retrieved context.
- **Explore** — return a structural project tree and widget hierarchies.

### Code agent

A LangGraph workflow with four roles:

| Role | Responsibility |
|------|----------------|
| Planner | Discovers context, drafts a plan and acceptance criteria |
| Dev | Implements the plan in a git worktree |
| Verifier | Runs `dart analyze`, checks criteria, retries on failure |
| QA | Final review of diffs against the plan |

Human approval is required after planning. The Flutter UI in `ui/code_agent_flutter/` drives the run lifecycle (start, approve, reject, SSE events).

Target Flutter projects can ship an `AGENTS.md` at their root to define folder layout and naming conventions for the planner and dev agents.

## Quick start

### Prerequisites

- Python 3.12+
- [Qdrant](https://qdrant.tech/) running locally (default `http://localhost:6333`)
- OpenAI API key
- Dart and Flutter SDKs (for code-agent verification)

### Setup

```bash
git clone <repo-url>
cd tester-rag-api
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env   # add your OPENAI_API_KEY
```

### Run the API

```bash
python main.py
# or: uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/docs` for the interactive API reference.

### Run tests

```bash
pytest
```

### Run the Flutter UI

```bash
cd ui/code_agent_flutter
flutter pub get
flutter run -d macos
```

Point the UI at `http://localhost:8000` and provide the absolute path to a Flutter project you want the agent to modify.

## API overview

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/ingest` | POST | Index a project into Qdrant |
| `/api/query` | POST | Semantic code search |
| `/api/context` | POST | Module context for a query |
| `/api/generate` | POST | Generate tests from context |
| `/api/explore` | POST | Project structure tree |
| `/api/code-agent/run` | POST | Start a code-agent run |
| `/api/code-agent/runs/{id}` | GET | Poll run status |
| `/api/code-agent/runs/{id}/approve` | POST | Approve plan and continue |
| `/api/code-agent/runs/{id}/reject` | POST | Reject plan |
| `/api/code-agent/runs/{id}/events` | GET | SSE status stream |

## Configuration

Key environment variables (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | — | Required for embeddings and generation |
| `OPENAI_CHAT_MODEL` | `gpt-5-mini` | Chat model for agents |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model |
| `CODE_AGENT_WORKTREES_DIR` | `./data/worktrees` | Per-run git worktrees |
| `CODE_AGENT_MAX_VERIFIER_ITERATIONS` | `3` | Dev/verifier retry limit |
| `DART_BIN` / `FLUTTER_BIN` | `dart` / `flutter` | SDK binary paths |

## Project structure

```
app/
  agents/          # Analyst, parser, and role agents
  api/             # FastAPI routers
  orchestration/   # LangGraph graph and runner
  services/        # Embeddings, vector store, worktrees
  tools/           # Agent tools (grep, filesystem, dart, git)
main.py
tests/
ui/
  code_agent_flutter/   # Desktop UI for the code agent
  ai_tester_flutter/    # Legacy tester UI
```

## Agent guidance

See [AGENTS.md](./AGENTS.md) for conventions when contributing to this repository or extending the agent workflow.
