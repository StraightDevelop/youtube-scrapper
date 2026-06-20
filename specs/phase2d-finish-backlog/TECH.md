# TECH.md — Phase 2d: finish the deferred backlog

> Scope id: `phase2d-finish-backlog`. Clears the items deferred from Phase 2c.
> Three concerns, all TDD + grader-verified.

## 1. Accuracy — richer statuses (status-contract change)

`shared/youtube/transcript_fetcher.py`:
- `Status` gains **`EMPTY_CAPTIONS`** (a track existed but parsed to no text — was
  conflated with `NO_CAPTIONS`) and **`UNAVAILABLE`** (private / removed /
  members-only / age-restricted — was conflated with `OTHER`).
- `_classify_extract_error`: `_UNAVAILABLE_HINTS` → `UNAVAILABLE` (was `OTHER`).
- `fetch_transcript`: empty parsed body → `EMPTY_CAPTIONS`; a no-captions result on
  a restricted video → `UNAVAILABLE` via new `_is_unavailable(info)` (checks
  `availability` ∈ restricted set, or `age_limit >= 18`).
- Centralized **`FAILURE_STATUSES`** + **`RETRY_STATUSES`** here (single source of
  truth — rule 8). Both apps now import them; the duplicate copies in
  `scrape.py` and `app.py` are removed. `RETRY_STATUSES` stays `{NETWORK_ERROR,
  OTHER}` (the new permanent statuses are intentionally not retried).
- `friendly_transcript_status` extended for the two new statuses.

Existing characterization tests that locked the old mapping were updated test-first
(empty-body → `EMPTY_CAPTIONS`; private → `UNAVAILABLE`).

## 2. CX — plain-English failure breakdown

`summarize_failures(failures_by_status)` (pure, in `transcript_fetcher.py`) renders
`"2 no captions · 1 rate-limited / network"` from the summary's counts. Wired into
the web `_render_status_banner` as a `Reasons: …` caption under partial/failed runs.
(The headline summary + raw-JSON-behind-expander already existed.)

## 3. Refactor — extract merge/dedup to shared (rule 8)

`app.py::_merge_records` / `_dedupe_by_id` moved verbatim to new
`shared/io/record_merge.py` as `merge_records` / `dedupe_by_id`; `app.py` imports
them and the local copies are deleted. Pure behavior, now reusable + tested.

## Testing and validation

New: `test_record_merge.py`; extended `test_transcript_fetcher.py` (EMPTY_CAPTIONS,
UNAVAILABLE, `_is_unavailable`) and `test_transcript_parsing.py`
(`summarize_failures`, extended friendly list, updated classify expectation).
`record_merge.py` added to the coverage gate. Done = grader GREEN. `apps/` wiring
verified via `py_compile`.

This clears all deferred items; no further phases are queued.
