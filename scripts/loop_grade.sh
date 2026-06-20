#!/usr/bin/env bash
# scripts/loop_grade.sh — the independent EVALUATOR (maker–checker separation).
#
# Purpose:
#   Grade the working tree against deterministic quality gates. This is the "judge"
#   in the loop-engineering pattern. The generator (Claude, via loop.md) must NEVER
#   grade its own work — this script does, in a separate process.
#
# Contract:
#   exit 0  -> all configured gates pass (GREEN)
#   exit !0 -> at least one gate failed (RED); loop feeds this back to the generator
#
# Design:
#   Multi-stack + greenfield-safe. Auto-detects Node / Rust / Python / Go and runs the
#   gates that exist for that stack. Missing gates are SKIPPED (not failures), so an
#   empty repo is GREEN and gates light up automatically as the project takes shape.
#   Override detection with STACK=node|rust|python|go, or skip gates with NO_<GATE>=1.
set -uo pipefail

fail=0
log() { printf '\033[33m[grade %s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*" >&2; }

# run_gate <label> <exists-predicate> <gate-command>
#   <exists-predicate> decides whether the gate is configured yet (skip if not).
run_gate() {
  local label="$1" exists="$2" cmd="$3"
  if eval "$exists" >/dev/null 2>&1; then
    log "running: $label"
    if eval "$cmd"; then log "OK:   $label"; else log "FAIL: $label"; fail=1; fi
  else
    log "skip: $label (not configured for this stack)"
  fi
}

have() { command -v "$1" >/dev/null 2>&1; }

# Source-file presence guards (keep greenfield GREEN: don't run a gate with nothing
# to check — e.g. `pytest` on a repo with no tests exits 5, `go vet` on an empty
# module errors). Hidden dirs (.git, .venv, .loop) and node_modules are excluded.
#
# NOTE: do NOT pipe `find ... | grep -q .` here — under `set -o pipefail`, grep -q
# closes the pipe on its first match and `find` (still walking a large tree such as
# a non-hidden `venv/`) dies on SIGPIPE with a non-zero status, which pipefail then
# propagates, falsely making the guard "fail" and skipping the gate. Use `-print
# -quit` (stop at first match, clean exit) inside a non-empty test instead.
py_files() { [ -n "$(find . -path ./node_modules -prune -o -not -path '*/.*' -name '*.py' -print -quit 2>/dev/null)" ]; }
py_tests() { [ -n "$(find . -path ./node_modules -prune -o -not -path '*/.*' \( -name 'test_*.py' -o -name '*_test.py' \) -print -quit 2>/dev/null)" ]; }
go_files() { [ -n "$(find . -path ./vendor -prune -o -not -path '*/.*' -name '*.go' -print -quit 2>/dev/null)" ]; }

# --- detect the Node package manager from the lockfile (default npm) ---
node_pm() {
  if [ -f pnpm-lock.yaml ]; then echo pnpm
  elif [ -f yarn.lock ]; then echo yarn
  elif [ -f bun.lockb ]; then echo bun
  else echo npm; fi
}

ran_any=0

# ============================== Node / TypeScript ==============================
if { [ "${STACK:-}" = "node" ] || [ -z "${STACK:-}" ]; } && [ -f package.json ]; then
  ran_any=1
  PKG="${PKG_MANAGER:-$(node_pm)}"
  log "stack: node (package manager: $PKG)"
  # Use `<pm> run <script>`: npm only treats lifecycle names (test/start/…) as bare
  # commands; `npm lint` is an "Unknown command". `run` works on npm/pnpm/yarn/bun.
  run_gate "typecheck" "[ -z \"${NO_TYPECHECK:-}\" ] && grep -q '\"typecheck\"' package.json" "$PKG run typecheck"
  run_gate "lint"      "[ -z \"${NO_LINT:-}\" ]      && grep -q '\"lint\"'      package.json" "$PKG run lint"
  run_gate "test"      "[ -z \"${NO_TEST:-}\" ]      && grep -q '\"test\"'      package.json" "$PKG run test"
  run_gate "build"     "[ -z \"${NO_BUILD:-}\" ]     && grep -q '\"build\"'     package.json" "$PKG run build"
fi

# ================================== Rust =====================================
if { [ "${STACK:-}" = "rust" ] || [ -z "${STACK:-}" ]; } && [ -f Cargo.toml ]; then
  ran_any=1
  log "stack: rust (cargo)"
  run_gate "typecheck" "[ -z \"${NO_TYPECHECK:-}\" ] && have cargo" "cargo check --quiet"
  run_gate "lint"      "[ -z \"${NO_LINT:-}\" ]      && cargo clippy --version" "cargo clippy --quiet -- -D warnings"
  run_gate "test"      "[ -z \"${NO_TEST:-}\" ]      && have cargo" "cargo test --quiet"
  run_gate "build"     "[ -z \"${NO_BUILD:-}\" ]     && have cargo" "cargo build --quiet"
fi

# ================================= Python ====================================
if { [ "${STACK:-}" = "python" ] || [ -z "${STACK:-}" ]; } && { [ -f pyproject.toml ] || [ -f setup.py ] || [ -f setup.cfg ]; }; then
  ran_any=1
  log "stack: python"
  run_gate "typecheck" "[ -z \"${NO_TYPECHECK:-}\" ] && have mypy && py_files" "mypy ."
  run_gate "lint"      "[ -z \"${NO_LINT:-}\" ]      && have ruff && py_files" "ruff check ."
  run_gate "test"      "[ -z \"${NO_TEST:-}\" ]      && have pytest && py_tests" "pytest -q"
  # No universal 'build' gate for libraries; covered by typecheck+test.
fi

# =================================== Go ======================================
if { [ "${STACK:-}" = "go" ] || [ -z "${STACK:-}" ]; } && [ -f go.mod ]; then
  ran_any=1
  log "stack: go"
  run_gate "typecheck" "[ -z \"${NO_TYPECHECK:-}\" ] && have go && go_files" "go vet ./..."
  run_gate "test"      "[ -z \"${NO_TEST:-}\" ]      && have go && go_files" "go test ./..."
  run_gate "build"     "[ -z \"${NO_BUILD:-}\" ]     && have go && go_files" "go build ./..."
fi

if [ "$ran_any" -eq 0 ]; then
  log "no recognized stack manifest found — greenfield. Progress is judged by TODOS.md state only."
fi

if [ "$fail" -eq 0 ]; then log "VERDICT: GREEN"; else log "VERDICT: RED"; fi
exit "$fail"
