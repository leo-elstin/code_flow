# Gap Analysis (non-auth) + UX Review — `tester-rag-api`

**Context:** Deployment model is **local single-user machine**, so network-exposure items (auth, CORS, bind address) are intentionally de-scoped. This document covers (A) the *other* engineering gaps that still matter locally, and (B) a UX review of the Flutter client from a senior developer-tools UX perspective.
**Method:** every item below was traced to specific code. No code changed.

---

# Part A — Engineering gaps that still matter on a local machine

Re-prioritized for the local model. The theme that replaces "security" is **data integrity, cost, and reliability of a long-running local agent.**

## A1 — CRITICAL: in-place mode can destroy the user's uncommitted work
`_prepare_in_place_workspace` (`worktree.py:51`) returns the **real project checkout** as the worktree. The dev agent then writes directly into it, and its `roll_back_file` tool runs `git checkout HEAD -- <path>` (`filesystem.py:26`, exposed to the LLM in `dev.py:209`). If you have uncommitted edits in a file the agent decides to "roll back," they are **gone** — no stash, no backup, no confirmation. The audience for this tool is developers who routinely have WIP in their tree. This is the highest-impact local risk.
→ Before in-place runs: refuse on a dirty tree, or auto-`git stash`/checkpoint-commit first; never expose a destructive `checkout HEAD` on the live checkout without a restore path.

## A2 — HIGH: no SQLite concurrency hardening → "database is locked" under normal use
Verified: **no `PRAGMA journal_mode=WAL`, no `busy_timeout`** anywhere. Four DB files (`runs`, `run_index`, `projects`, `activity`) are opened with bare `sqlite3.connect`/`aiosqlite`. Meanwhile the SSE loop calls `get_state` every 2s, activity polling every 1s, and the background run writes checkpoints — concurrent writers on the same files with the 5s default lock timeout. This surfaces as intermittent 500s / stalls precisely when a run is active (there's already a `scratch/test_db_concurrency.py`, which suggests prior pain).
→ Set WAL + a few-second `busy_timeout` once at startup for every connection factory.

## A3 — HIGH: blocking subprocess work freezes the whole app (felt acutely locally)
The agent's `pub get` (≤180s), `dart analyze` (≤120s), `build_runner` (≤300s), `flutter test`, and every `git` call use synchronous `subprocess.run` inside `async` graph nodes/handlers (only `ingest` uses `to_thread`). On a single-user local server this means: while one run does `pub get`, the UI's polling, SSE, and any second action all hang. `approve_run` runs `prepare_workspace` (→ `pub get`) **inside the HTTP request**, so the approve call itself can block for minutes with the button stuck on "Executing…".
→ Push all blocking work to `asyncio.to_thread`/a worker; return approve immediately and report workspace-prep progress as activity events.

## A4 — HIGH: unbounded growth, no retention, no cleanup
- **Activity table** (`code_agent_activity.db`) has no prune/retention/VACUUM — it grows forever across every run.
- **`messages`** uses an `operator.add` reducer (`state.py:35`); every retry/patch appends, so the checkpoint payload for a long-lived ticket keeps swelling (and it's re-serialized on every state write).
- **`runner._tasks`** is never reaped (no `pop`/`del`), and **worktrees** under `data/worktrees/` are created per run and only removed on pub-get failure — successful/abandoned runs leave worktrees (and `agent/<run_id>` branches) behind indefinitely. On a dev laptop this quietly accumulates gigabytes and hundreds of stale branches.
→ Add activity retention, reap finished tasks, and a worktree/branch GC (and a UI affordance to discard a run's worktree).

## A5 — HIGH: `dart analyze` is non-blocking by default → "verified" code that doesn't compile-check
`CODE_AGENT_ANALYZE_BLOCKING` defaults `false`, so `blocking_passed = clean_pass or not ANALYZE_BLOCKING` is **always True** (`dart_tools.py:205`). The README sells "verifier runs `dart analyze` … retries on failure," but out of the box analyze errors are advisory: a run reaches `completed`/QA with code that fails analysis. Locally this means the tool hands you "done" work that doesn't pass `flutter analyze`.
→ Default analyze to blocking, or make the advisory state loud in the report and UI.

## A6 — MEDIUM: wasted LLM spend from missing pre-flight validation
`start_run` does **no** check that `project_path` is absolute / exists / is a git repo before kicking off the planner (the `.git` check only happens later at worktree creation, and only `create_project` validates). So a bad path runs a full planner LLM pass (discovery + a chat-completion) and *then* fails at approve. On a metered API key this is real money for a guaranteed failure. `discover_context` on a wrong path silently walks nothing and plans from empty context.
→ Validate path + git repo in `start_run` and reject early.

## A7 — MEDIUM: RAG store is global, not per-project (silent wrong answers)
Single hardcoded Qdrant collection `"flutter_codebase"` with no project filter at query time (`vector_store.py`, `query.py`, `context.py`). Ingest project B and project A's `/context` and test-generation retrieval now pull B's chunks. Embedding dim is hardcoded to 1536 (`embedding.py:9`); switching `OPENAI_EMBEDDING_MODEL` silently mismatches the collection. For a local tool used across several repos this produces confusing, wrong context with no error.
→ Namespace per project (collection or payload filter) and derive/validate embedding dimension from the model.

## A8 — MEDIUM: `list_runs` does O(N) work + writes on every call
`runner.list_runs` → `sync_missing_from_checkpoints` loads up to 10,000 index rows and, for each checkpoint thread not yet indexed, calls `get_state` — which itself re-serializes and **writes** the run. The Flutter client calls `loadRuns` frequently (after every refresh/status change). Fine at 5 runs; a cliff at a few hundred.
→ Backfill once on startup, not on every list.

## A9 — MEDIUM: reliability/correctness edges
- **`sync_dirty_files`** parses `git status --porcelain` with `line[3:]` and `" -> "` splitting; git **quotes** paths containing spaces/unicode, so those files are mis-synced or missed. Use `-z` / `--porcelain=v1` with NUL parsing.
- **`_parse_analyze_errors`** keys on the substring `" error "` and matches targets via `in` (path-substring collisions). Prefer `dart analyze --format=machine`/JSON.
- **`approve_run` TOCTOU**: checks `awaiting_approval` then prepares workspace + spawns a task with no lock; a double-click/double-approve can create two worktrees and orphan a task. Guard the transition.
- **Dead/contradictory code**: `app/core/database.py` configures an *embedded* Qdrant (`path="./qdrant_data"`) that nothing imports, contradicting the real server client — delete it. `get_embeddings(task_type=, titles=)` are accepted then `del`'d (no-ops). `app/agents/generator.py` and `reviewer.py` are empty.
- **Model config drift** across four sources: `config.py` (`gpt-4o`/`gpt-4o-mini`), `.env.example` (`gpt-5-mini`), README (`gpt-5-mini`), live `.env` (`gpt-5.5`). Pick one source of truth.

## A10 — LOW: observability & hygiene
RAG half logs via `print` (ingest/query/context/vector_store) while the rest uses `get_logger` — ingest failures are invisible (it's fire-and-forget returning "Processing started"). `dart.so` (1.3 MB platform binary) is committed to git. These are cleanup items, not blockers.

### Testing gaps (unchanged by local model)
The **dev ReAct loop** (`dev.py`, the highest-risk component) and the **entire RAG half** are untested; nothing asserts the analyze-blocking contract (A5), which is how that drift went unnoticed. Add a stubbed-client dev-loop test, a `blocking_passed` contract test, and a RAG round-trip test.

---

# Part B — UX Review (Flutter client) — senior dev-tools UX lens

Reviewed: `home_page`, `side_panel`, `center_pane`, `settings_screen`, `activity_log_section`, `agent_logs_view`, `project_context_panel`, models, `main`. The product flow — project → ticket → plan → **human approval** → execute → verify → QA → merge — is conceptually well-staged, and the live terminal-style activity log with elapsed timer and token count is a genuinely good touch. The problems below are about **trust, truthfulness, and discoverability** — the things developers judge a tool on fastest.

## B1 — CRITICAL: the UI fabricates content and presents it as real
This is the most damaging issue for a developer tool, because it destroys trust the moment it's noticed.
- **Fake acceptance criteria.** When a run has no plan, `center_pane.dart:79-95` injects hardcoded criteria — *"Define Ticket structure", "Update Planner Agent", "Connect to Orchestrator", "Test looping behavior"* (for features) — for a ticket the user wrote about something else entirely.
- **Fake checkbox completion.** Even with a real plan, "done" is guessed by **index**: `isDone = completed || (verifying && i < criteriaList.length/2)` (`center_pane.dart:76`). The checkmarks do not reflect any real verification — they're decorative, but look authoritative.
- **Fake description.** Tickets with no description render a hardcoded sentence: *"…manage the network access to assistant."* (`center_pane.dart:196`).
- **Heuristic progress bar.** The three-segment bar (`_buildProgressBar`) infers phase from status strings, not from actual graph state.

A developer who sees the agent "check off" criteria they never wrote will stop believing anything the panel says. → Render only real plan/criteria/verification data; show honest empty states ("No acceptance criteria yet — generated during planning") instead of mock data; drive checkboxes from the verifier report, not array position.

## B2 — CRITICAL (flow): "awaiting approval" is hidden inside an amber "In Progress" badge
The entire product hinges on a human approving the plan, yet the queue badge maps `awaiting_approval` → **"In Progress"** (amber) alongside `developing`/`planning` (`side_panel.dart:463-469`). So the one state that **requires the user to act** is visually indistinguishable from states where the agent is busy and the user should wait. Users will leave runs parked, thinking the agent is working, when it's actually blocked on them.
→ Give `awaiting_approval` a distinct, attention-drawing treatment ("Needs review", blue/pulsing) in both the queue and the ticket header; ideally surface a count in the sidebar.

## B3 — HIGH: built, working features are unreachable from the UI
`saveProjectContext`, `generateProjectContext`, skill listing/selection (`projectSkills`, `loadProjectAgentConfig`) are fully implemented in the view model and backend, but there is **no UI** for them — `ProjectContextPanel` is a `SizedBox.shrink()` stub (`project_context_panel.dart`), and `settings_screen` only exposes API URL + AGENTS.md. Users cannot set per-project agent context or choose planner/dev skills, despite paying for that machinery. → Build the context editor + skill picker into Settings (or a per-project panel).

## B4 — HIGH: "Stop" doesn't stop the agent
The Stop button in the activity log calls `stopWatchingRun()`, which only **stops polling/SSE on the client** (`code_agent_vm.dart:615`). The backend run keeps executing (and spending tokens). The label promises cancellation it can't deliver — a serious expectation mismatch for a tool that runs expensive multi-step agents. → Either implement real server-side cancellation, or rename to "Stop watching" and make the still-running state obvious.

## B5 — HIGH: in-place mode is offered casually, with no data-loss warning
The approve dialog presents "Edit Current Codebase" as a peer option described as *"Best for quick iterations without Git worktree overhead"* (`center_pane.dart:437-439`) — no mention that the agent edits your live checkout and can roll back uncommitted work (see A1). For the target user this is the dangerous path dressed up as the convenient one. → Add an explicit warning + dirty-tree guard before allowing in-place.

## B6 — MEDIUM: misleading affordances and fake chrome
- The **Execute button** carries a `chevron_down` (`center_pane.dart:288`) implying a dropdown menu; it has none — clicking just runs the single contextual action.
- **"Share" and "ellipsis"** icons in the detail top bar (`center_pane.dart:163-165`) are decorative no-ops.
- The **profile block** hardcodes *"Leo Elstin / Online"* and loads a **remote Unsplash photo over the network** on every launch (`home_page.dart:251`) — odd for a local tool, a privacy surprise, and it breaks offline.
- **"My Tasks" vs "All Tickets"** both resolve to the same ticket list (both fall through to the SidePanel+CenterPane branch in `home_page.dart:306`); two nav items, identical result.
- **"Priority" column** always renders "—" (`side_panel.dart:427`) — a column with no data.
→ Remove non-functional controls and placeholder identity, or wire them up. Empty columns and dead icons read as "unfinished."

## B7 — MEDIUM: weak first-run and connection feedback
Settings "Save" **always** toasts "saved successfully" (`settings_screen.dart:107`) regardless of whether the URL is valid or the backend is reachable; there's no health check / "Test connection." If the API is down, failures land in a partial `logs` list and a rarely-rendered `state.error`. First-run users get no clear "backend not connected" signal. Also, the success/error toast reads `state.error` captured **before** the `await` (`settings_screen.dart:335`), so it can show a stale or empty message. → Add a connection test with a clear status indicator; surface a persistent "disconnected" banner; read fresh error state after await.

## B8 — MEDIUM: accessibility is largely absent (it's a desktop dev tool, so this matters)
- Interactive rows/items use `GestureDetector` (projects, tickets, workflow items, clear/stop/refresh icons) rather than semantic buttons → no keyboard focus, no Enter/Space activation, no screen-reader roles. The app is effectively mouse-only; there's no keyboard navigation of the ticket queue or actions.
- **Contrast**: body text at `#94A3B8`/`#64748B` on white is ~2.8–3.5:1, below WCAG AA 4.5:1; lots of 10–11px text.
- Several icon-only controls (refresh, trash, share, ellipsis, folder-pick) have **no tooltips/labels**.
→ Use `ShadButton`/`FocusableActionDetector`, add tooltips, raise muted-text contrast, support basic keyboard nav (arrow-through queue, Enter to open, Cmd+Enter to execute).

## B9 — LOW: polish / consistency
- Status taxonomy in the badge collapses `verifying`/`qa` → "Verifying" and `planning`/`developing`/`awaiting_approval` → "In Progress," losing the distinct phases the activity log otherwise shows nicely.
- Long `pub get`/planning has no skeleton/progress beyond a spinner + "Executing…"; given A3's multi-minute blocks, an explicit "Preparing workspace (running pub get)…" activity line would reassure.
- Light theme only (`ThemeMode.light` hardcoded in `main.dart`) — a terminal-forward dev tool will get asked for dark mode quickly; the activity log is already dark, so the rest clashes.

## What's good (keep)
Terminal activity log with level tags, file → annotations, animated waveform + tabular-figures elapsed timer; clear worktree-"Recommended" framing; sensible status color palette; real empty states for "no project/ticket"; monospace for paths and logs; the staged plan→approve→verify→merge model is the right mental model and is mostly legible.

---

# Consolidated priority list

**Do first (correctness/trust/data-loss):**
1. A1 — guard in-place mode against destroying uncommitted work.
2. B1 — stop fabricating criteria/description/checkboxes; show honest empty states.
3. B2 — make `awaiting_approval` a distinct "needs you" state.
4. A5 — fix the analyze-blocking default (or label advisory loudly).
5. B4 — make "Stop" actually stop, or rename and clarify.

**Next (reliability/cost):**
6. A2 (WAL + busy_timeout), A3 (un-block the event loop), A4 (retention + worktree/task GC).
7. A6 (pre-flight validation), B5 (in-place warning), B3 (expose context/skills UI).

**Then (correctness-at-scale / polish):**
8. A7 (per-project RAG + embedding dim), A8 (list_runs O(N)), A9 (parsing/TOCTOU/dead code).
9. B6–B9 (remove fake chrome, connection feedback, accessibility, dark mode, model-config drift A9, observability A10).
