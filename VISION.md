# VISION.md — north star & definition of done

> The loop's compass. `loop.md` reads this every iteration to know what "done" means.
> Keep it short and stable; the detail lives in your specs/design docs.

## Product (north star)

<!-- One or two sentences: what this project is and the single source of truth it
     steers toward. Replace this with the real north star before running the loop. -->
A personal-use Python toolset that extracts every public video's title and
transcript from a YouTube channel into a single JSONL file, plus single-video
`.mp4` downloads — usable both as a CLI (`apps/youtube_scraper/scrape.py`) and a
local, non-technical-friendly Streamlit UI (`apps/youtube_scraper_web/app.py`).
The source of truth is the JSONL output contract and the acceptance criteria in
each app's `README.md`.

## Architecture guardrails (non-negotiable)

<!-- The handful of rules every iteration must respect. Examples below — edit to fit. -->
- **Separation of concerns:** keep project-specific code and reusable code in their
  designated layers; dependencies point one direction only.
- **Style:** follow the repo's existing naming/format conventions; **TDD-first**;
  small single-purpose functions; no hardcoded config (env/DI).
- **Stack:** Python 3.10+; `yt-dlp` as the sole external scraping dependency
  (video listing + transcript fetch via signed timedtext URLs + single-video
  download); Streamlit for the local web UI. No network services or cloud infra —
  runs locally, uploads nothing.

## Definition of done — per task

1. Tests were written first and now pass.
2. The change lives in the correct layer with no cross-contamination.
3. Quality gates green (`scripts/loop_grade.sh`): typecheck, lint, tests, build where
   applicable.
4. Committed with a conventional message and a one-line `CHANGELOG.md` entry.

## Definition of done — per phase

A phase is DONE when every `- [ ]` item for that phase in `TODOS.md` is `- [x]`, all
gates are green, and the work is committed + logged.

## Current target

<!-- Which phase the loop is driving right now. The loop drives this phase to done,
     one task per iteration, then STOPS and escalates the next phase to a human. -->
**Phase 1 — Pure-logic test safety net** (see `TODOS.md`, spec
`specs/phase1-test-safety-net/TECH.md`). Done when all tests pass, ≥85% line/branch
coverage on the 5 gated `shared/` modules, and `scripts/loop_grade.sh` exits 0.
