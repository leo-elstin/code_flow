# Implementation Plan — Trust & Data-Loss Tier (Phase 1)

Scope: A1, A5, B1, B2, B4. Surgical changes only; no refactors. Each item lists the file(s), the exact change, and how to verify.

**Environment note:** the review/build sandbox has no `pytest`/`dart`/`flutter` and a locked `.git/index`, so changes are made in-tree (not on a branch) and verification commands are listed for you to run on your machine. Your working tree already had unrelated uncommitted edits; these Phase-1 changes are additive on top and reviewable via `git diff`.

---

## A1 — In-place mode must refuse a dirty working tree (data-loss guard)

**Why:** in-place mode points the agent at your real checkout; its `roll_back_file` runs `git checkout HEAD -- <path>`, silently discarding uncommitted edits to any file it touches.

**Change — `app/services/worktree.py`**
- Import `is_repo_clean` from `app.tools.git_tools` (no import cycle: `git_tools` does not import `worktree`).
- In `_prepare_in_place_workspace`, immediately after the `.git` check, refuse if the tree has tracked modifications (`is_repo_clean` uses `--untracked-files=no`, so untracked files — which `checkout HEAD` can't destroy — don't block).

**Test — `tests/test_code_agent.py`**
- Add `test_prepare_in_place_rejects_dirty_repo`: init repo, commit a file, modify it (uncommitted), assert `prepare_workspace(..., workspace_mode="in_place")` raises `WorktreeError`.

**Verify:** `pytest tests/test_code_agent.py -k in_place`

---

## A5 — Make `dart analyze` blocking by default

**Why:** default `CODE_AGENT_ANALYZE_BLOCKING=false` makes `blocking_passed` always true, so runs reach `completed`/QA with code that fails analysis — contradicting documented behavior.

**Change — `app/core/config.py`**
- Flip the `CODE_AGENT_ANALYZE_BLOCKING` default from `"false"` to `"true"`.

**Change — `.env.example`**
- Document `CODE_AGENT_ANALYZE_BLOCKING=true` (set `false` to downgrade analyze errors to advisory).

**Compatibility:** `test_analyze_advisory_mode` stubs `run_dart_analyze` and sets the advisory flag explicitly, so it is unaffected.

**Verify:** `pytest tests/test_code_agent.py -k "analyze or verifier"`

---

## B1 — Stop fabricating ticket content (center pane)

**Why:** the detail view invents acceptance criteria, a description, and checkbox "done" state, presenting guesses as fact — the fastest way to lose a developer's trust.

**Change — `ui/code_agent_flutter/lib/src/ui/center_pane.dart`**
1. Remove the hardcoded mock criteria branch (the `isBug ? [...] : [...]` fallback) and the unused `isBug` local.
2. Build criteria only from real data: `run.acceptanceCriteria`, else `run.plan['acceptance_criteria']`. Per-item "done" is driven by real run state (`run.status == 'completed'`), never by list index.
3. If there are no criteria yet, show an honest muted line: "Acceptance criteria will be generated during planning." (no checkboxes).
4. Replace the fabricated description fallback string with "No description provided." (muted).

**Verify:** `flutter analyze` (no new warnings); manual: open a ticket with no run → see honest empty states, not mock checkboxes.

---

## B2 — Make "awaiting approval" a distinct "needs you" state

**Why:** the queue badge maps `awaiting_approval` into amber "In Progress", hiding the one state that requires user action — the pivot of the whole workflow.

**Change — `ui/code_agent_flutter/lib/src/ui/side_panel.dart`**
- In `_buildStatusBadge`, give `awaiting_approval` its own case: indigo badge labelled "Needs Review". Keep `developing`/`planning` as amber "In Progress".

**Verify:** `flutter analyze`; manual: a run paused at planning shows "Needs Review", visually distinct from running states.

---

## B4 — Make "Stop" honest

**Why:** the Stop button only stops client-side polling (`stopWatchingRun`); the agent keeps running and spending tokens on the server. The label promises cancellation it doesn't perform.

**Change — `ui/code_agent_flutter/lib/src/ui/widgets/activity_log_section.dart`**
- Relabel the button "Stop watching" and wrap it in a `Tooltip`: "Stops live updates here. The agent keeps running on the server."

**Deferred (documented, not in Phase 1):** true server-side cancellation. It needs a `/runs/{id}/cancel` endpoint that cancels the asyncio task and reconciles run/worktree state, which is only safe after A3 (move blocking subprocess work off the event loop). Tracked for the reliability tier.

**Verify:** `flutter analyze`; manual: button reads "Stop watching" with the explanatory tooltip.

---

## Verification summary (run on your machine)

```bash
# Backend
cd tester-rag-api && source .venv/bin/activate
pytest -q                        # full suite
pytest tests/test_code_agent.py -k "in_place or analyze or verifier"

# UI
cd ui/code_agent_flutter
flutter analyze
flutter test
```

Review everything Phase 1 touched:
```bash
git diff -- app/services/worktree.py app/core/config.py .env.example \
  tests/test_code_agent.py \
  ui/code_agent_flutter/lib/src/ui/center_pane.dart \
  ui/code_agent_flutter/lib/src/ui/side_panel.dart \
  ui/code_agent_flutter/lib/src/ui/widgets/activity_log_section.dart
```
