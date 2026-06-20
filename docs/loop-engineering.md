# Loop Engineering — how this project runs itself until done

> **Loop engineering** (Addy Osmani, 2026): *"replacing yourself as the person who
> prompts the agent. You design the system that does it instead."* Boris Cherny,
> creator of Claude Code: *"I don't prompt Claude anymore. I have loops running that
> prompt Claude... My job is to write loops."*

This document explains the autonomous loop wired into this project and how to run it.

## 1. The pattern (the part everyone agrees on)

A well-designed loop has **three parts**:

```
        ┌────────────┐     output      ┌────────────┐
        │ GENERATOR  │ ──────────────► │ EVALUATOR  │
        │ (Claude,   │                 │ (gates /   │
        │  loop.md)  │ ◄────────────── │  rubric)   │
        └────────────┘   feedback      └────────────┘
              ▲                               │
              └───────── LOOP until ──────────┘
              rubric passes • budget runs out • escalate to human
```

- **Generator** — the agent doing the work. Here: `claude -p` fed `loop.md`.
- **Evaluator** — an *independent* judge grading against checkable criteria. Here:
  `scripts/loop_grade.sh` (typecheck, lint, tests, build) + the `TODOS.md` state.
- **Loop** — feeds the evaluator's verdict back to the generator until done.

**The one hard rule:** *the generator must never grade its own work.* An independent
verifier in a separate context window beats self-critique — a model judging itself
"confidently praises mediocre work." `loop_grade.sh` runs in a separate process for
exactly this reason.

## 2. The anchor files (state lives on disk, not in the context window)

| File | Role |
|------|------|
| `VISION.md` | North star + **definition of done**. What the loop is steering toward. |
| `TODOS.md` | The **task queue**. `- [ ]` = todo, `- [x]` = done, `BLOCKED:` = escalate. |
| `loop.md` | The **recurring prompt** piped into every iteration (the loop contract). |
| `CLAUDE.md` | Operating **rules/guardrails** applied every iteration. |
| `scripts/loop_grade.sh` | The **independent evaluator** (deterministic gates). |
| `scripts/loop.sh` | The **driver** (Ralph-style headless loop with guardrails). |
| `CHANGELOG.md` | Durable memory of decisions; each completed task appends one line. |

Because progress is persisted in files + git, each iteration can start with **fresh
context** and still know exactly where it is — this is what lets the loop run across
dozens of tasks without context overflow.

## 3. Two ways to run it

### Mode A — in-session, native primitives (simplest)

1. Set the stopping rubric (a Stop hook that blocks until it's true):

   ```
   /goal Phase 1 in TODOS.md is fully checked [x] and `bash scripts/loop_grade.sh` exits 0
   ```

2. Start the self-paced loop over the recurring prompt:

   ```
   /loop                # self-paced; each tick runs the loop.md contract
   ```

   (or `/loop 10m` to tick on a fixed interval). `/goal` keeps it from stopping until
   the rubric holds; it auto-clears when met.

### Mode B — headless, runs while you sleep (Ralph-style)

```bash
MAX_ITER=3 scripts/loop.sh                 # dry-run a few iterations, watch it work
UNATTENDED=1 MAX_ITER=20 scripts/loop.sh   # full unattended run (trusted loop.md only)
```

Logs land in `.loop/iter_NNN.log`. Stop any time with `Ctrl-C` or `touch .loop/STOP`.

## 4. Guardrails (three hard stops + escalation)

1. **Iteration ceiling** — `MAX_ITER` (default 20). Hard cap on cost/time.
2. **No-progress detection** — if completed-task count doesn't move for
   `NO_PROGRESS_LIMIT` (default 3) iterations, stop instead of spinning.
3. **Completion / BLOCKED sentinels** — the agent emits
   `<promise>ALL TASKS COMPLETE</promise>` when the active phase is done, or
   `BLOCKED: <reason>` when it needs a human. The driver verifies completion against
   green gates + an empty `- [ ]` set before accepting it.
4. **Budget** — set a per-run token/dollar ceiling in your Claude Code config before
   you walk away. Caps are not optional at scale.

Plus the rules baked into `loop.md`: one task per iteration, never weaken the judge
(no deleting tests or lowering thresholds), never edit `loop.md`/`VISION.md` to fake
"done".

## 5. The trap to avoid — cognitive surrender

*"When the loop runs itself it's very tempting to stop having an opinion and just take
whatever it gives back... Build the loop. But build it like someone who intends to stay
the engineer, not just the person who presses go."* Review the diffs. The loop drafts a
phase; **a human approves the next phase's direction** — the loop is wired to stop and
escalate at the phase boundary.

## Sources

- Addy Osmani — *Loop Engineering*: https://addyosmani.com/blog/loop-engineering/
- Anthropic agent-loop docs: https://code.claude.com/docs/en/agent-sdk/agent-loop
- Original Ralph technique (Geoffrey Huntley): https://ghuntley.com/ralph/
