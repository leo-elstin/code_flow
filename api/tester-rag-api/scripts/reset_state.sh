#!/usr/bin/env bash
#
# Reset all code-agent state for a clean start:
#   - SQLite DBs (projects/tickets, run checkpoints, run index, epic runs,
#     PO sessions, activity) — recreated empty on next server start
#   - agent git worktrees + agent/<uuid> branches in each project repo
#   - Jira attachments and in-place baseline snapshots
#   - the Qdrant `flutter_codebase` collection (if the server is reachable)
#
# It deliberately does NOT touch: your project's real branches, non-UUID
# `agent/*` branches (e.g. agent/end-end-po-dev-skil), or Cursor worktrees.
#
# Usage:
#   scripts/reset_state.sh          # prompts for confirmation
#   scripts/reset_state.sh --yes    # no prompt (for scripting)
#
# STOP THE BACKEND FIRST — deleting DBs while the server holds them open is unsafe.

set -euo pipefail

cd "$(dirname "$0")/.."   # -> api/tester-rag-api
DATA_DIR="data"
PY="$([ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)"
UUID_RE='^agent/(epic-)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'

# --- safety: warn if the backend is still running on :8000 --------------------
if command -v lsof >/dev/null && lsof -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "⚠️  Something is listening on :8000 — the backend may still be running."
  echo "    Stop it before resetting (open DB handles can corrupt the delete)."
fi

# --- collect project repo paths BEFORE deleting the DB ------------------------
PROJECT_PATHS=()
if [ -f "$DATA_DIR/code_agent_projects.db" ]; then
  while IFS= read -r p; do [ -n "$p" ] && PROJECT_PATHS+=("$p"); done < <(
    "$PY" - <<'PYEOF'
import sqlite3, os
db = "data/code_agent_projects.db"
try:
    c = sqlite3.connect(db)
    for (p,) in c.execute("SELECT path FROM projects"):
        if p and os.path.isdir(p):
            print(p)
except Exception:
    pass
PYEOF
  )
fi

echo ""
echo "This will PERMANENTLY delete:"
echo "  • SQLite DBs in $DATA_DIR/ (projects, runs, run_index, epic_runs, po_sessions, activity)"
echo "  • $DATA_DIR/worktrees/*, $DATA_DIR/baselines/*, $DATA_DIR/jira_attachments/*"
echo "  • agent/<uuid> + agent/epic-<uuid> branches and their worktrees in:"
for p in "${PROJECT_PATHS[@]:-}"; do echo "      - $p"; done
echo "  • Qdrant collection 'flutter_codebase' (if reachable)"
echo ""

if [ "${1:-}" != "--yes" ]; then
  read -r -p "Type 'reset' to proceed: " ans
  [ "$ans" = "reset" ] || { echo "Aborted."; exit 1; }
fi

# --- 1) clean agent worktrees + branches in each project repo ----------------
for repo in "${PROJECT_PATHS[@]:-}"; do
  echo "→ Cleaning agent worktrees/branches in $repo"
  # Remove only worktrees living under this app's data/worktrees dir.
  git -C "$repo" worktree list --porcelain 2>/dev/null \
    | awk '/^worktree /{print $2}' \
    | grep "/tester-rag-api/data/worktrees/" \
    | while read -r wt; do
        git -C "$repo" worktree remove --force "$wt" 2>/dev/null || true
      done
  git -C "$repo" worktree prune 2>/dev/null || true
  # Delete only UUID-shaped agent branches (preserves e.g. agent/end-end-po-dev-skil).
  git -C "$repo" for-each-ref --format='%(refname:short)' refs/heads/agent 2>/dev/null \
    | grep -E "$UUID_RE" \
    | while read -r br; do
        git -C "$repo" branch -D "$br" >/dev/null 2>&1 || true
      done
done

# --- 2) delete the SQLite DBs (+ WAL/SHM sidecars) ---------------------------
echo "→ Deleting SQLite databases"
rm -f "$DATA_DIR"/*.db "$DATA_DIR"/*.db-wal "$DATA_DIR"/*.db-shm

# --- 3) clear worktree dirs, baselines, attachments --------------------------
echo "→ Clearing worktrees / baselines / attachments"
rm -rf "$DATA_DIR"/worktrees/* "$DATA_DIR"/baselines/* "$DATA_DIR"/jira_attachments/* 2>/dev/null || true

# --- 4) drop the Qdrant collection (best-effort) -----------------------------
if curl -s -m 3 -o /dev/null -w '' http://localhost:6333/collections 2>/dev/null; then
  echo "→ Dropping Qdrant collection 'flutter_codebase'"
  curl -s -m 5 -X DELETE http://localhost:6333/collections/flutter_codebase >/dev/null 2>&1 || true
else
  echo "→ Qdrant not reachable on :6333 — skipping (re-ingest will recreate it)"
fi

echo ""
echo "✅ Reset complete. Start the backend to recreate empty databases, then re-sync"
echo "   your project from Jira and re-ingest the codebase for RAG."
