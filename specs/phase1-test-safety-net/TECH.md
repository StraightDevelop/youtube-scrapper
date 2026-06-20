# TECH.md — Phase 1: Pure-logic test safety net

> Status: **DRAFT — awaiting approval**
> Scope id: `phase1-test-safety-net` (no Linear/GitHub ticket; descriptive id per
> the spec-driven flow). Loop phase: see `VISION.md` → Current target, `TODOS.md` → Phase 1.
> Repo state at authoring: branch `main`, HEAD `2490176`. NOTE: the entire
> `apps/` and `shared/` trees are currently **untracked** (the two existing commits
> only added meta files + `.gitignore`), so code refs below are plain `path:line`
> rather than commit-pinned GitHub links.

## Context

The codebase has **zero automated tests** (`tests/` does not exist). Every later
speed / accuracy / UX improvement the user wants to make through the autonomous
loop needs an objective regression signal first — and the loop's evaluator
(`scripts/loop_grade.sh`) judges Python projects by `pytest`, which currently
finds nothing to run. Phase 1 establishes that safety net over the deterministic,
no-network logic in `shared/`.

This is implementation-facing work with **no user-facing behavior change**, so
there is intentionally no `PRODUCT.md` (YAGNI).

Target modules (all pure: data in → data out, no network, no Streamlit):

- `shared/youtube/url_validator.py` — `validate_channel_url`, `extract_channel_handle`,
  `parse_video_id`, `validate_playlist_url`, `extract_playlist_id`,
  `channel_playlists_tab_url`, `_sanitize` (`url_validator.py:22-205`).
- `shared/utils/text_cleaner.py` — `clean_transcript_snippets` (`text_cleaner.py:10-29`).
- `shared/io/jsonl_writer.py` — `append_jsonl` (`jsonl_writer.py:12-30`).
- `shared/io/summary_writer.py` — `write_summary` (`summary_writer.py:12-24`).
- `shared/io/resume_reader.py` — `iter_jsonl_records`, `collect_channel_history`,
  `read_existing_video_ids` (`resume_reader.py:12-117`).
- `shared/youtube/transcript_fetcher.py` — **pure helpers only**: `_pick_track`,
  `_resolve_lang`, `_pick_format`, `_parse_caption_body` + `_parse_json3` /
  `_parse_vtt_like` / `_parse_xml_like`, `_classify_extract_error`,
  `_classify_generic_error` (`transcript_fetcher.py:156-321`). Its network
  functions — `fetch_transcript`, `_extract_info`, `_download`
  (`transcript_fetcher.py:58-153,219-223`) — are **out of scope** (Phase 2).

Guiding principle — **characterization tests**: lock current behavior, do not
change production logic. If a test surfaces a real defect (crash, data loss),
stop and mark `BLOCKED: <reason>` (rule 35 invariant); a fix becomes its own later
phase, failing-test-first (rule 33).

## Proposed changes

### New tooling (3 files, additive — no production code touched)

- **`requirements-dev.txt`** — `pytest`, `pytest-cov` (pinned). Kept separate from
  runtime `requirements.txt` so the scraper's install surface is unchanged.
- **`pyproject.toml`** — the project has none today; introduce it for test + coverage
  config (modern standard, single source of truth):
  ```toml
  [tool.pytest.ini_options]
  testpaths = ["tests"]
  pythonpath = ["."]                       # so `from shared...` resolves, no sys.path hacks
  addopts = "-q --cov --cov-report=term-missing --cov-fail-under=85"

  [tool.coverage.run]
  branch = true
  include = [                              # gate scope = the 5 fully-pure modules
      "shared/youtube/url_validator.py",
      "shared/utils/text_cleaner.py",
      "shared/io/jsonl_writer.py",
      "shared/io/summary_writer.py",
      "shared/io/resume_reader.py",
  ]
  ```
- **`tests/`** — mirrors the source tree:
  ```
  tests/conftest.py                              # shared fixtures (sample caption bodies, records)
  tests/shared/youtube/test_url_validator.py
  tests/shared/youtube/test_transcript_parsing.py
  tests/shared/io/test_jsonl_writer.py
  tests/shared/io/test_summary_writer.py
  tests/shared/io/test_resume_reader.py
  tests/shared/utils/test_text_cleaner.py
  ```

### Coverage-gate scoping decision (needs approval)

The ≥85% gate measures the **5 fully-pure modules** listed in `[tool.coverage.run]
include`. `transcript_fetcher.py` is deliberately **excluded from the gate** even
though its helpers ARE tested: the file mixes pure helpers with network functions
in one module, so a file-level 85% gate is unreachable until Phase 2 mocks the
network path. Net effect: the helper tests still run and stay green (protecting the
parsing/fallback logic), but they don't count toward the coverage number. The
file-level gate for `transcript_fetcher.py` moves to Phase 2.

### Grader (no edit required)

`scripts/loop_grade.sh:78-85` already activates the Python branch on
`pyproject.toml` and runs `pytest -q` when `pytest` + `test_*.py` are present. The
coverage threshold rides in via `addopts`, so the existing generic gate enforces
the done bar unchanged — the shared evaluator template stays untouched (preferable
to editing the judge). `mypy`/`ruff` gates self-skip (not installed).

### Per-module test plan (characterization)

- **url_validator** — each accepted channel form (`@handle`, `/channel/UC…`,
  `/c/name`, `/user/name`) → canonical `…/videos`; `/videos` appended when missing;
  non-http scheme / non-YouTube host / empty / unrecognized path → `ValueError`.
  `parse_video_id`: `watch?v=`, `youtu.be/`, `/shorts|embed|v|live/`, bare 11-char
  id, junk → `ValueError`. `validate_playlist_url`: canonicalization, `watch?v=&list=`
  rejected, missing/malformed `list=` → `ValueError`. `extract_channel_handle`
  (`@`/`c`/`user`/fallback + `_sanitize`). `channel_playlists_tab_url` swaps trailing tab.
- **text_cleaner** — multi-snippet join; whitespace/newline normalization; skips
  non-`Mapping` and non-`str`/empty `text`; empty input → `""`.
- **jsonl_writer** — round-trip (write→read back); append adds a line; Thai/unicode
  preserved (`ensure_ascii=False`); parent dirs created; compact separators. Uses `tmp_path`.
- **summary_writer** — pretty JSON + trailing newline; unicode preserved; key order
  preserved; parent dirs created. Uses `tmp_path`.
- **resume_reader** — `iter_jsonl_records` skips blank/malformed lines, missing file
  yields nothing; `collect_channel_history` merges by **mtime order** (later run
  overwrites earlier — `OK` overrides prior `NETWORK_ERROR`; set mtimes via
  `os.utime`), dedups by `id`, returns `status_by_id`; `read_existing_video_ids`
  returns the id set, skips malformed, missing → empty set.
- **transcript_parsing** — `_parse_json3` (concatenate `events[].segs[].utf8`; empty;
  malformed JSON → `[]`); `_parse_vtt_like` (drops `WEBVTT`/`NOTE`/timecodes/cue
  numbers/tags); `_parse_xml_like` (strips tags); `_parse_caption_body` dispatch by
  ext (incl. unknown→xml fallback); `_pick_track` ladder (manual-preferred →
  auto-preferred → any-auto → any-manual → `None`); `_resolve_lang` exact +
  primary-subtag (`en-US`→`en`, `zh-Hans`→`zh`); `_pick_format` honors
  `_PREFERRED_FORMATS` order; `_classify_extract_error` / `_classify_generic_error`
  hint mapping (disabled / network / unavailable / other).

## Testing and validation

The suite **is** the deliverable. Done bar (= the loop's stop condition):

1. `pytest` collects and **all tests pass**.
2. **≥85% line coverage** on the 5 gated modules (`--cov-fail-under=85` in `addopts`).
3. `bash scripts/loop_grade.sh` exits `0` (GREEN) with the Python test gate active.

Run locally (must use the venv so `pytest`/`pytest-cov`/`yt_dlp` are importable —
`transcript_fetcher` imports `yt_dlp` at module load):

```bash
source venv/bin/activate
pip install -r requirements-dev.txt
pytest                      # honors pyproject addopts (coverage + threshold)
bash scripts/loop_grade.sh  # independent verdict
```

Intermediate loop iterations will show **RED** until the final module lands and
coverage crosses 85% — that is correct loop semantics (the gate stays red until the
phase is done), not a failure to fix.

## Risks and mitigations

- **Grader environment** — `loop_grade.sh` runs `pytest` from `PATH`; it must run
  with the venv active (or pytest/pytest-cov installed in the runner). Mitigation:
  documented in the run steps + a `TODOS.md` note; `loop.sh` should be invoked from
  an activated venv.
- **`yt_dlp` import cost** — importing `transcript_fetcher` loads `yt_dlp`. It is
  already a pinned runtime dep and present in the venv, so helper tests import fine;
  no network is hit because only pure helpers are called.
- **Coverage scope drift** — if someone runs `pytest` against a single file, the
  global `--cov-fail-under` can report failure for unexercised gated modules. Accepted
  DX wrinkle; full-suite runs (the loop's path) are unaffected.

## Follow-ups (Phase 2+, not now)

- Mock the `yt_dlp` boundary → cover `fetch_transcript`, `_extract_info`, `_download`,
  `channel_extractor`, `video_downloader`; bring `transcript_fetcher.py` under the gate.
- Extract `app.py` `_merge_records`/`_dedupe_by_id` into `shared/io` (dedupe vs
  `resume_reader`, rule 8) and test there.
- Then the approved roadmap items: speed (parallel transcript fetch), accuracy
  (manual-vs-auto fallback, `NO_CAPTIONS` vs empty), CX (`friendly_transcript_error`,
  human-readable summary).

## Parallelization

`run_agents` is not available in this environment, and rule 35 binds the loop to
**one task per iteration**, so Phase 1 runs sequentially (one test module per
iteration, grader between). The six test modules are independent and *could* fan out
in a one-shot context, but sequential execution is required here and the per-module
cost is small — parallelization is not beneficial.
