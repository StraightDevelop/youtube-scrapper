# TECH.md — Phase 3: UX/UI polish (web)

> Scope id: `phase3-ux-ui`. The last dimension of the original goal. Mostly Streamlit
> UI in `apps/youtube_scraper_web/app.py`; reusable logic extracted to `shared/`
> (tested + gated). Plus the first automated test for the web app.

## Context

The early review flagged three concrete UX gaps:
1. Caption language is a raw-code text box (`th,en`) — non-technical users don't
   know ISO codes.
2. The live progress table silently shows only the last 50 rows — on a 500-video
   channel the user never sees most rows and gets no "X of Y" indication.
3. The two tabs accept different inputs (transcript: channel/playlist/video;
   download: single video) with no up-front hint.

## Proposed changes

- **Language picker (testable core in `shared/`).** New `shared/youtube/languages.py`:
  curated `COMMON_LANGUAGES` (code, name) + `language_options()`,
  `option_label()`, `code_from_label()`, `codes_from_labels()`. The sidebar's raw
  text box becomes an `st.multiselect` of human names (default Thai+English) plus an
  optional "other codes" field; selections are converted back to the existing
  comma-code `languages_raw` string, so `parse_languages` / `_pick_track` are
  unchanged downstream.
- **Progress-table indicator.** `_TABLE_MAX_ROWS = 50` constant (replaces the
  hardcoded slice in `_table_view`); a `caption_holder` shows "Showing the latest 50
  of N processed." once a run exceeds the cap.
- **Tab hint.** A one-line `st.caption` above the tabs explaining what each accepts.

## Testing and validation

- `tests/shared/youtube/test_languages.py` — names, labels, option order,
  label→code parsing, dedup. `languages.py` joins the coverage gate (100%).
- `tests/apps/test_web_app_smoke.py` — **first app.py test**: Streamlit `AppTest`
  loads the page and asserts it renders with no exception and the language picker is
  present. Marked `@pytest.mark.slow` and excluded from the default/grader run via
  `addopts = "… -m 'not slow'"` to keep the core suite Streamlit-free (~0.7s
  steady-state; a fresh env pays a one-time ~90s streamlit/pandas `.pyc` compile).
  Run it with `pytest -m slow`. `apps/` stays out of the coverage gate (consistent
  with prior phases); other UI changes are verified by `py_compile` + the smoke test.

## Out of scope (not pursued)

Sentence-aware transcript chunking, download-tab example warnings, and success-card
path prominence — lower-value polish; can be a later phase if wanted.
