# TECH.md — Phase 2c (CX slice): friendly transcript status

> Scope id: `phase2c-friendly-status`. Small CX win; no JSONL/status **contract**
> change. User decision recorded: caption-pick fallback stays **as-is** (language
> match wins over manual-quality) — so `_pick_track` is unchanged.

## Context

Transcript failures surface to users as raw status codes (`NO_CAPTIONS`,
`NETWORK_ERROR`, …). The download path already has
`shared/youtube/video_downloader.py::friendly_download_error`; the transcript path
had no equivalent because failures are a returned `Status`, not an exception.

## Proposed changes

- `shared/youtube/transcript_fetcher.py`: add pure
  `friendly_transcript_status(status: str) -> str` — maps each `Status` to a plain
  sentence; `""` for `OK`; unknown codes fall back to the `OTHER` message. Backed by
  a `_FRIENDLY_STATUS` dict (no hardcoded scattered literals).
- Wire-in (no contract change):
  - `apps/youtube_scraper/scrape.py`: print `↳ <reason>` under each non-OK line.
  - `apps/youtube_scraper_web/app.py`: add a `why` column to `_table_view` (blank
    for OK rows).

## Testing and validation

`tests/shared/youtube/test_transcript_parsing.py`: `OK → ""`; every failure status
yields a non-empty human sentence; unknown code falls back to the `OTHER` message.
`friendly_transcript_status` is in the already-gated `transcript_fetcher.py`; done =
grader GREEN with the module still ≥85%. `apps/` wiring stays out of the gate
(mechanical), verified by `py_compile`.

## Deferred (need their own design — status-contract changes)

- Distinguish `NO_CAPTIONS` from empty/whitespace captions (new status/handling).
- Explicit private / age-gated detection from `info_dict` instead of string hints.
- Human-readable run-summary view (web) with raw JSON behind a second expander.

These alter the JSONL status contract / downstream (`FAILURE_STATUSES`,
`RETRY_STATUSES`, UI labels) and are intentionally not bundled here.
