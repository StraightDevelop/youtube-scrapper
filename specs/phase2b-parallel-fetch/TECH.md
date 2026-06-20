# TECH.md — Phase 2b: Parallel transcript fetching (conservative)

> Scope id: `phase2b-parallel-fetch`. First **behavior-changing** phase — built on
> the Phase 1+2a safety net. Posture (user-approved): **conservative**.

## Context

Transcript fetches are serial with a sleep between each:
`apps/youtube_scraper/scrape.py:225-240` and
`apps/youtube_scraper_web/app.py:722-737`. Both loops do the same thing per video:
`fetch_transcript` → `build_record` → `append_jsonl` (crash-safe) → tally →
`time.sleep(delay)`. A 500-video channel takes 500s+ regardless of bandwidth.
`fetch_transcript` is blocking I/O (yt_dlp + urllib), so a **thread pool** fits;
asyncio would be a needless rewrite.

De-scoped: lazy `yt_dlp` import. The 207s first-run cost was one-time `.pyc`
compilation; steady-state import is ~1s, and lazy import would break the Phase 2a
module-level `YoutubeDL` monkeypatching. Not worth it (YAGNI).

## Proposed changes

### New `shared/youtube/concurrent_fetch.py` (reusable, rule 7/8)

```python
def stream_transcripts(videos, languages, *, max_workers=4, delay=0.0,
                       fetch_fn=fetch_transcript):
    """Yield (video, status, language, transcript) as each fetch COMPLETES."""
```
- `ThreadPoolExecutor(max_workers)`; submit one task per video; `as_completed`
  yields results so callers keep writing/counting incrementally (preserves
  crash-safe resume — no buffering the whole run in memory).
- Conservative politeness: each task `time.sleep(delay)` before fetching, so with
  `max_workers=4` + `delay` the request rate stays bounded. `delay` reuses the
  existing CLI/UI value.
- Defensive: `fetch_transcript` never raises by contract, but if a `fetch_fn` does,
  the task is caught and yielded as `("OTHER", "none", "")` so one bad item can't
  kill the pool.
- `max_workers` default from `YT_SCRAPER_MAX_WORKERS` env (rule 12), fallback 4.

### Caller refactors (behavior: completion-order, not input-order)

- `scrape.py`: pre-filter `to_fetch = [v for v in videos if v["id"] not in
  existing_ids]` (tally `skipped` as before), then consume `stream_transcripts`;
  per yielded result `build_record` → `append_jsonl` → tally → print `done` line.
  New `--workers N` arg (default env/4); `--delay` kept.
- `app.py`: same consumption over `to_fetch`; update `records_this_run`,
  `append_jsonl`, tally, refresh table/progress per completed result. Add a
  "Parallel fetches" control to the Advanced expander (default env/4).

**Ordering change (intentional, safe):** output/print/table order becomes
completion order rather than input order. Functionally safe — resume dedups by
`id`, JSONL is a set keyed by id, and the summary counts are order-independent.
Documented in `PRODUCT.md`-less form here since there's no user-facing contract on
row order.

## Testing and validation

`tests/shared/youtube/test_concurrent_fetch.py` (no network — inject a fake
`fetch_fn`):
- all videos are fetched exactly once; every result is yielded.
- bounded concurrency: a fake fn recording max concurrent in-flight never exceeds
  `max_workers` (use a small barrier/counter + lock).
- `delay` is honored (monkeypatch `time.sleep` to record calls).
- a `fetch_fn` raising for one video yields `("OTHER","none","")`, others succeed.

Add `shared/youtube/concurrent_fetch.py` to the `pyproject.toml` coverage `include`;
done = grader GREEN (`scripts/loop_grade.sh` exit 0) with ≥85% on it. The two
`apps/` callers stay outside the gate (consistent with Phase 1/2a scope); their
refactor is mechanical and exercised via the helper contract.

## Risks and mitigations

- **429 / soft-block**: bounded `max_workers` (4) + per-task `delay` keep it polite;
  a 429 still maps to `NETWORK_ERROR` and is retried on the next resume run (existing
  behavior).
- **Thread-safety of `append_jsonl`**: writes happen in the *caller's* loop over
  yielded results (single thread), not inside pool workers — so no concurrent file
  writes. The pool only runs `fetch_transcript` (pure network, no shared state).

## Follow-ups

Phase 2c (accuracy + CX) is independent and unaffected by this change.
