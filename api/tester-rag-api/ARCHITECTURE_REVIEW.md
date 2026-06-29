# Architecture & Engineering Review — `tester-rag-api`

**Reviewer role:** Senior Architect / Engineer
**Scope:** Full repo — FastAPI backend (RAG + LangGraph code agent), Flutter desktop client, tests, ops.
**Date:** 2026-06-10
**Status:** No code changed (review only).

---

## 1. Verdict

This is a genuinely impressive **prototype** with a thoughtful multi-agent design, real git-worktree isolation, and good test coverage on the orchestration core. The agent pipeline (planner → dev → verifier ⇄ dev → qa) is well-modeled and the human-approval interrupt is implemented correctly.

It is **not production-ready**, and the gap is mostly about *operational safety* rather than feature completeness. The single most important fact about this service: **it executes shell commands and writes files on the host, driven by an LLM, over an unauthenticated HTTP API bound to `0.0.0.0` with `CORS *`.** Everything else is secondary to that.

Recommended framing: treat the current state as "works on my machine, single trusted local user." Closing the items in §4 is the precondition for anything beyond that.

---

## 2. What the system is

A FastAPI service with two halves:

- **RAG pipeline** — `/api/ingest`, `/query`, `/context`, `/generate`, `/explore`. Walks a Flutter/Dart (and Python) project, tree-sitter–chunks it, embeds with OpenAI, stores in Qdrant, retrieves for test/prompt generation.
- **Code agent** — a LangGraph state machine (`app/orchestration/`) that plans a feature, implements it in a per-run git worktree via an LLM ReAct loop (`app/agents/roles/dev.py`), runs `dart analyze` / `flutter test` (`verifier`), and produces a QA report. Persisted with `AsyncSqliteSaver`; run/ticket/activity metadata in three side SQLite DBs. A Flutter desktop app drives the lifecycle over REST + SSE.

The two halves are loosely coupled and largely independent — effectively two products in one repo.

---

## 3. Strengths (keep these)

- **Clean orchestration model.** `graph.py` / `state.py` / `runner.py` separation is good. `TypedDict` state with an `operator.add` reducer on `messages` is idiomatic LangGraph. Routing (`route_after_verifier`) is explicit and tested.
- **Correct human-in-the-loop.** `interrupt_after=["planner"]` with the deliberate comment about *not* interrupting before dev (so verifier→dev retries don't re-prompt) shows real understanding of the framework. `test_runner_pauses_after_planner_not_before_dev` locks it in.
- **Defense-in-depth primitives exist.** `path_guard.py` (realpath + `root + os.sep` boundary), `safe_shell.py` (allowlist prefixes + metacharacter block + `shlex.split`, no `shell=True`), and worktree isolation are all the *right* ideas.
- **Deterministic verifier gate before the LLM.** `compare_to_plan` checks planned files exist/changed and catches removed public methods before spending tokens — sensible and cheap.
- **Solid test coverage on the core.** ~1,600 lines across orchestration, merge, path guard, safe shell, manual retry, run-index backfill, surgical edit/rollback. Merge conflict/abort/dirty-repo cases are covered.
- **Thoughtful merge service.** `merge_worktree.py` validates status, refuses in-place, checks clean main repo, aborts in-progress merges, returns structured conflict info.

---

## 4. Critical — security & safety (fix before any shared/exposed deployment)

**C1. No authentication or authorization, anywhere.** Verified: no `Depends`, security scheme, API key, or middleware on any route. Every endpoint — including `start_run` (LLM-driven file writes + `dart`/`flutter`/`git` execution), `merge`, `ingest`, and `/api/source` (arbitrary file read) — is open. `main.py` binds `host="0.0.0.0"`. Anyone who can reach the port owns the host account. → Add an auth layer (even a static bearer token to start) and default-bind to `127.0.0.1`.

**C2. `CORS allow_origins=["*"]` + `allow_credentials=True` (`main.py:23-24`, default `CODE_AGENT_CORS_ORIGINS="*"`).** This is an invalid/unsafe combination — credentialed wildcard CORS. Browsers reject it, and it signals the wrong intent. → Pin explicit origins; only enable credentials with a concrete allowlist.

**C3. Fully user-controlled host paths with no sandbox.** `project_path` on every endpoint is an arbitrary absolute path the server walks, reads, and (in the agent) writes/executes in. There is no root jail, allowlist, or project registry enforcement on the RAG endpoints (`create_project` validates `.git`, but `start_run`, `/context`, `/explore`, `/ingest` accept any path). Combined with C1 this is arbitrary host file read/write. → Constrain to a registered-projects allowlist; reject paths outside it.

**C4. Path-guard inconsistency — sibling-prefix bypass in `explorer.py`.** `/api/source` and `/api/widget-tree` guard with `abs_file.startswith(abs_project)` **without a trailing separator** (`app/api/explorer.py:47,77`). Verified: `"/a/proj-secret/x".startswith("/a/proj")` → `True`. So `project_path=/a/proj` can read `/a/proj-secret/...`. `path_guard.py` does this correctly (`root + os.sep`); the API routes don't reuse it. → Route all path resolution through `path_guard.resolve_read_path`.

**C5. `in_place` workspace mode mutates the user's real checkout.** With C1, an unauthenticated caller can make the dev agent edit the live project tree; `roll_back_file` runs `git checkout HEAD -- <path>`, which silently discards the user's uncommitted work in that file. → Require explicit opt-in, refuse if the checkout is dirty, and gate behind auth.

> Note: `.env` (real 164-char key) and `data/` are correctly gitignored. However the 1.3 MB prebuilt `app/lib/dart.so` **is** committed — a platform-specific binary in git that won't match other architectures and bloats history. Ship it via build step or release asset instead.

---

## 5. High — correctness & reliability

**H1. `dart analyze` is advisory by default, which contradicts the documented behavior.** `CODE_AGENT_ANALYZE_BLOCKING` defaults to `false`, so `blocking_passed = clean_pass or not ANALYZE_BLOCKING` is **always `True`** (`dart_tools.py:205`). The README/AGENTS.md say the verifier "runs `dart analyze` … retries on failure," but out of the box analyze errors never block and never trigger a retry — the agent will mark runs `completed` with code that doesn't analyze cleanly. → Make blocking the default, or correct the docs and surface "advisory" prominently in the report/UI.

**H2. Blocking subprocess calls inside async event loop.** The graph nodes are `async` and run as `asyncio` tasks on the main loop, but `run_dart_analyze`, `ensure_pub_dependencies` (pub get, up to 180s), `run_flutter_test`, `run_build_runner` (up to 300s), and every `git_tools` call use synchronous `subprocess.run`. Only `ingest` uses `to_thread` (verified). A single run's `pub get`/`analyze` **stalls the entire server** — including SSE streams and health for all other clients. → Wrap blocking work in `asyncio.to_thread` (or a worker), consistently.

**H3. RAG vector store is not project-scoped — cross-project contamination.** A single hardcoded collection `"flutter_codebase"` (`vector_store.py`, `query.py`, `context.py`) holds chunks from *every* ingested project, keyed only by absolute `file_path` in the payload, with no project filter at query time. Ingesting project B pollutes project A's semantic results. → Namespace per project (collection-per-project or a `project_id` payload filter on every query).

**H4. Embedding dimension hardcoded to 1536.** `get_embedding_dimensions()` returns a constant tied to `text-embedding-3-small`; `ensure_collection` uses it. Switching `OPENAI_EMBEDDING_MODEL` to a 3072-dim model creates/queries a mismatched collection → silent failure or garbage retrieval. → Derive dimension from the model (or validate at startup) and version the collection name by model.

**H5. Unbounded run tasks; no concurrency cap; task map leaks.** `runner._tasks` is written on every start/approve/retry/resume but **never cleaned up** (verified: no `pop`/`del`). There's no limit on concurrent runs. Long-lived process → memory growth and N simultaneous heavy git/dart workloads with no backpressure. → Cap concurrency (semaphore/queue) and reap finished tasks in the `finally` of `_run_graph`.

**H6. Write amplification + locking risk on SQLite.** `get_state` calls `upsert_run` **and** `update_ticket_status_by_run` on *every* invocation; the SSE loop calls `get_state` every 2s and activity polling adds more. `ensure_schema()` runs on every `append_activity`/`upsert_run`. Multiple sync `sqlite3` connections plus the async checkpointer on shared DB files invite `database is locked` under light concurrency (there's already a `scratch/test_db_concurrency.py`, suggesting prior pain). → Only persist on state *change*; call `ensure_schema` once at startup; enable WAL + busy_timeout centrally.

**H7. Approve/start has a TOCTOU race.** `approve_run` checks `status == awaiting_approval` then prepares a worktree and spawns a task with no lock. Two near-simultaneous approves could both pass the check; the second `_tasks[run_id] = task` orphans the first task while both may create worktrees. → Guard transitions with a per-run lock / atomic compare-and-set.

---

## 6. Medium — design, performance, maintainability

- **M1. Dead/contradictory Qdrant client.** `app/core/database.py` instantiates an *embedded* `QdrantClient(path="./qdrant_data")`, while `vector_store.py` uses a *server* client (`localhost:6333`). The `core/database.py` module is imported nowhere (verified) — dead code that contradicts the real config and will confuse the next engineer. Delete it.
- **M2. Misleading embedding API.** `get_embeddings(texts, task_type=..., titles=...)` immediately does `del task_type, titles` — leftover Vertex/Gemini params (`RETRIEVAL_DOCUMENT`, `CODE_RETRIEVAL_QUERY`) that now do nothing. Remove the dead parameters so callers don't believe task-typing is happening.
- **M3. Model configuration is inconsistent across four places.** `config.py` defaults (`gpt-4o`, dev `gpt-4o-mini`), `.env.example` (`gpt-5-mini`), README (`gpt-5-mini`), live `.env` (`gpt-5.5`). Pick one source of truth and document the intended tiering; the divergence makes behavior unpredictable per environment.
- **M4. `ingest` is fire-and-forget with no status.** Returns `{"status": "Processing started"}` immediately via `BackgroundTasks`; failures only `print`. The caller can never learn if ingestion succeeded, partially failed (`failed_batches`), or found zero files. → Return a job id with a queryable status, or make it synchronous with a summary.
- **M5. Fragile text parsing.** `_parse_analyze_errors` keys on the substring `" error "` and matches targets by `in` substring (path-prefix collisions); `dart_public_method_names` is a single regex over class bodies; `sync_dirty_files` parses `git status --porcelain` with `line[3:]` and won't handle quoted paths (filenames with spaces). All are heuristics that will misfire on edge cases. → Prefer machine-readable outputs (`dart analyze --format=json`/`machine`, `git status -z`).
- **M6. Generic `except Exception` swallowing.** Many spots catch broadly and continue (`context.py` file read `except Exception: pass`, QA widget-tree extraction, SSE parse). Fine for resilience, but combined with `print`-based logging in the RAG half it makes failures invisible. Standardize on `get_logger()` (AGENTS.md already mandates this; ingest/query/context/vector_store still use `print`).
- **M7. SSE design.** `stream_run_events` is a `while True` poll-the-DB-every-2s loop rather than event-driven; it re-reads full state and writes to SQLite each tick (see H6). The Flutter side additionally runs a 1s activity timer *and* SSE *and* a 3s reconnect loop — a lot of redundant traffic for one run. Workable, but consider push/notify or at least longer intervals.
- **M8. Repo hygiene.** `scratch/` (36 experiment scripts) is gitignored — good — but still present and sizeable; `verify_agent_loop.py` and `dummy_app_test/` sit at the repo root. `app/agents/generator.py` and `reviewer.py` are empty placeholder files. Two Flutter UIs (`code_agent_flutter`, `ai_tester_flutter`, plus `ui/packages/...`) with committed macOS `Pods/`/Xcode cruft inflate the tree. Tighten before this grows.

---

## 7. Testing assessment

**Good:** orchestration routing, approval-interrupt semantics, merge (happy/conflict/dirty/already-applied), path traversal, safe-shell allowlist, surgical edit + rollback, manual-retry loops, run-index backfill, project/ticket/context CRUD.

**Gaps that matter:**
- The **dev ReAct loop** (`dev.py`) — the most complex and highest-risk component — has no unit test. No coverage of the tool-dispatch switch, malformed-JSON handling, `max_steps` exhaustion, or edit-failure recovery.
- The **entire RAG half is untested**: `ingest`, `query`, `context`, `generate`, `vector_store`, `embedding`, `analyst` chunking. No test would catch H3 (shared collection) or H4 (dimension mismatch).
- **No auth/security tests** (because there's no auth), and nothing covering the C4 prefix bypass.
- No test asserts the analyze-blocking contract (H1) — which is how the doc/behavior drift went unnoticed.

→ Add: a `dev.py` loop test with a stubbed client; a contract test for `blocking_passed`; RAG round-trip tests with a fake/embedded Qdrant; a `/api/source` traversal test.

---

## 8. Prioritized recommendations

**P0 — before exposing beyond localhost**
1. Add authentication; bind `127.0.0.1` by default (C1).
2. Fix CORS credentialed-wildcard (C2).
3. Constrain `project_path` to a registered allowlist; route all file access through `path_guard` (C3, C4).
4. Gate/guard `in_place` mode behind auth + clean-tree check (C5).

**P1 — correctness & stability**
5. Decide analyze-blocking policy and align docs (H1).
6. Move blocking subprocess work off the event loop (H2).
7. Project-scope the vector store + derive embedding dim from model (H3, H4).
8. Cap run concurrency and reap `_tasks`; persist only on change; WAL + busy_timeout (H5, H6, H7).

**P2 — maintainability**
9. Delete dead `core/database.py` and the no-op embedding params; unify model config (M1, M2, M3).
10. Make ingest status-queryable; switch RAG half to structured logging; prefer machine-readable tool output (M4, M5, M6).
11. Add the missing tests in §7; stop committing `dart.so`; clean root-level/placeholder cruft.

---

## 9. One-paragraph summary for a stakeholder

The agent design is sound and the orchestration core is well-built and well-tested — this is a strong prototype. The blocker is operational safety: an unauthenticated API that runs shell commands and writes files on the host, plus a verifier whose headline "analyze gate" is off by default and subprocess calls that block the server's event loop. None of these are deep design flaws; they're a focused, achievable hardening list. Close the P0 security items and the P1 correctness items and this becomes a credible internal tool.
