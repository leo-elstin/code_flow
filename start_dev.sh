#!/usr/bin/env bash
#
# Start the code-agent stack with GitHub SSH access loaded, so the agent's
# `flutter pub get` can fetch any private git dependencies of the target project.
#
# It: loads your SSH key into ssh-agent, verifies GitHub auth, resolves the
# target project's deps, optionally starts the UI, then runs the backend in the
# foreground (which therefore inherits the ssh-agent — the whole point).
#
# Usage:
#   ./start_dev.sh                 # load SSH, verify, start UI + backend
#   START_UI=false ./start_dev.sh  # backend only (run the UI yourself)
#   FLUTTER_PROJECT=/path ./start_dev.sh
#
# MUST be run from a real terminal (not a GUI/preview launcher) so the agent
# is inherited by the backend's `flutter pub get` subprocesses.

set -uo pipefail

# --- config (override via env) ------------------------------------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_DIR="$REPO_ROOT/api/tester-rag-api"
UI_DIR="$REPO_ROOT/ui/code_agent_web"
FLUTTER_PROJECT="${FLUTTER_PROJECT:-/Users/leo.e/dev/flutter/shipment}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
API_PORT="${API_PORT:-8000}"
START_UI="${START_UI:-true}"

step() { printf '\n\033[1;34m→ %s\033[0m\n' "$1"; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

# --- 1. load SSH key into the agent ------------------------------------------
step "Loading SSH key into ssh-agent: $SSH_KEY"
[ -f "$SSH_KEY" ] || die "SSH key not found: $SSH_KEY (set SSH_KEY=... to override)"
# --apple-use-keychain stores the passphrase in Keychain; prompts only the first time.
ssh-add --apple-use-keychain "$SSH_KEY" || die "ssh-add failed"
ssh-add -l || die "no identities in ssh-agent after add"

# --- 2. verify GitHub SSH auth (ssh exits 1 on success, so check the text) ----
step "Verifying GitHub SSH authentication"
ssh_out="$(ssh -o BatchMode=yes -o ConnectTimeout=8 -T git@github.com 2>&1 || true)"
if ! grep -q "successfully authenticated" <<<"$ssh_out"; then
  die "GitHub SSH auth failed. Add ${SSH_KEY}.pub to your GitHub account and authorize
   SSO for your organisation if the target project has private deps, then retry.
   ssh output: $ssh_out"
fi
echo "  ✓ authenticated to GitHub"

# --- 3. resolve the target project's dependencies ----------------------------
step "flutter pub get in $FLUTTER_PROJECT"
[ -d "$FLUTTER_PROJECT" ] || die "Flutter project not found: $FLUTTER_PROJECT"
( cd "$FLUTTER_PROJECT" && flutter pub get ) || die "pub get failed (see output above)"
echo "  ✓ dependencies resolved — the agent's worktree pub get will now succeed"

# --- 4. optionally start the UI in the background ----------------------------
UI_PID=""
cleanup() { [ -n "$UI_PID" ] && kill "$UI_PID" 2>/dev/null; }
trap cleanup EXIT INT TERM
if [ "$START_UI" = "true" ]; then
  step "Starting UI on :3000 (logs → /tmp/code_agent_ui.log)"
  ( cd "$UI_DIR" && npm run dev >/tmp/code_agent_ui.log 2>&1 ) &
  UI_PID=$!
fi

# --- 5. run the backend in the foreground (inherits ssh-agent) ---------------
step "Starting backend on :$API_PORT  (Ctrl-C to stop)"
cd "$API_DIR" || die "backend dir missing: $API_DIR"
[ -x .venv/bin/python ] || die "venv missing — run: python -m venv .venv && pip install -e ."
if .venv/bin/python -c "import uvicorn" 2>/dev/null; then
  .venv/bin/python -m uvicorn main:app --reload --port "$API_PORT"
else
  .venv/bin/python main.py
fi
