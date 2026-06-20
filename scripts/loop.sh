#!/usr/bin/env bash
# scripts/loop.sh — Ralph-style autonomous loop ("loop engineering").
#
# Pattern (Boris Cherny / Addy Osmani, 2026):
#   generator (claude -p, reads loop.md)  ->  evaluator (loop_grade.sh, independent)
#   -> repeat until TODOS.md has no '- [ ]' items, a guardrail trips, or BLOCKED.
#
# Why fresh context each iteration: state lives in files + git, not in the context
# window, so the loop can run for many tasks without context overflow. Each call sees
# the previous iterations' committed work.
#
# Three hard stops (guardrails): iteration ceiling, no-progress detection, and
# completion/blocked sentinels. The generator never grades itself — loop_grade.sh and
# the TODOS.md state are the judges.
#
# Usage:
#   scripts/loop.sh                       # run until done or a guardrail trips
#   MAX_ITER=5 scripts/loop.sh            # cap iterations
#   ALLOWED_TOOLS="Edit Write Read Bash" scripts/loop.sh
#   UNATTENDED=1 scripts/loop.sh          # add --dangerously-skip-permissions (overnight)
#
# Stop a running loop:  Ctrl-C, or `touch .loop/STOP` from another shell.
set -uo pipefail

PROMPT_FILE="${PROMPT_FILE:-loop.md}"
TASK_FILE="${TASK_FILE:-TODOS.md}"
MAX_ITER="${MAX_ITER:-20}"
NO_PROGRESS_LIMIT="${NO_PROGRESS_LIMIT:-3}"
CLAUDE_BIN="${CLAUDE_BIN:-claude}"
ALLOWED_TOOLS="${ALLOWED_TOOLS:-Edit Write Read Bash}"
LOG_DIR="${LOG_DIR:-.loop}"
UNATTENDED="${UNATTENDED:-0}"

mkdir -p "$LOG_DIR"
log() { printf '\033[36m[loop %s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }

# --- preflight ---
command -v "$CLAUDE_BIN" >/dev/null 2>&1 || { log "ERROR: '$CLAUDE_BIN' not on PATH"; exit 127; }
[ -f "$PROMPT_FILE" ] || { log "ERROR: $PROMPT_FILE missing"; exit 1; }
[ -f "$TASK_FILE" ]   || { log "ERROR: $TASK_FILE missing"; exit 1; }

extra_flags=()
if [ "$UNATTENDED" = "1" ]; then
  extra_flags+=(--dangerously-skip-permissions)
  log "UNATTENDED mode: permissions will not be prompted. Ensure you trust loop.md."
fi

# progress signature = number of completed tasks; used for no-progress detection
progress_signature() { grep -c '^[[:space:]]*- \[x\]' "$TASK_FILE" 2>/dev/null || echo 0; }

prev_sig="$(progress_signature)"
stall=0
rm -f "$LOG_DIR/STOP"

for ((i=1; i<=MAX_ITER; i++)); do
  [ -f "$LOG_DIR/STOP" ] && { log "STOP file found — exiting."; exit 0; }
  log "=== iteration $i / $MAX_ITER ==="

  # --- pre-work guardrails ---
  if grep -q 'BLOCKED' "$TASK_FILE"; then
    log "BLOCKED marker in $TASK_FILE — escalating to a human. Stopping."; exit 2
  fi
  if ! grep -q -e '- \[ \]' "$TASK_FILE"; then
    log "No unchecked '- [ ]' tasks remain — ALL TASKS COMPLETE. Stopping."; exit 0
  fi

  # --- generator: advance exactly one task (fresh context, state in files) ---
  out="$LOG_DIR/iter_$(printf '%03d' "$i").log"
  log "generator running -> $out"
  if ! "$CLAUDE_BIN" -p "${extra_flags[@]}" --allowedTools "$ALLOWED_TOOLS" < "$PROMPT_FILE" | tee "$out"; then
    log "generator exited nonzero — continuing to evaluator."
  fi

  # --- agent-reported sentinels ---
  if grep -q '^BLOCKED:' "$out"; then
    log "generator reported BLOCKED — escalating to a human. Stopping."; exit 2
  fi
  completion_claimed=0
  grep -q '<promise>ALL TASKS COMPLETE</promise>' "$out" && completion_claimed=1

  # --- evaluator: independent deterministic gates ---
  if bash scripts/loop_grade.sh; then
    log "evaluator: GREEN"
    if [ "$completion_claimed" = "1" ] && ! grep -q -e '- \[ \]' "$TASK_FILE"; then
      log "completion verified (sentinel + green gates + no open tasks). Stopping."; exit 0
    fi
  else
    log "evaluator: RED — feedback picked up next iteration."
  fi

  # --- no-progress detection ---
  sig="$(progress_signature)"
  if [ "$sig" = "$prev_sig" ]; then
    stall=$((stall + 1))
    log "no net progress this iteration ($stall/$NO_PROGRESS_LIMIT)."
    if [ "$stall" -ge "$NO_PROGRESS_LIMIT" ]; then
      log "no progress for $NO_PROGRESS_LIMIT iterations — stopping to avoid a spin loop."; exit 3
    fi
  else
    stall=0
    prev_sig="$sig"
  fi
done

log "reached MAX_ITER=$MAX_ITER — stopping. Re-run scripts/loop.sh to continue."
exit 4
