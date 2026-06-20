# TECH.md — Phase 2a: yt_dlp boundary coverage

> Scope id: `phase2-boundary-coverage`. Builds on Phase 1
> (`specs/phase1-test-safety-net/TECH.md`). Same principle: characterization tests,
> **no production logic changes**. Branch: `phase1-test-safety-net` (continues).

## Context

Phase 1 gated the 5 fully-pure `shared/` modules. The three yt_dlp-touching modules
were deferred because they mix pure helpers with network I/O in one file:

- `shared/youtube/transcript_fetcher.py` — pure helpers already tested
  (`test_transcript_parsing.py`); network path (`fetch_transcript`, `_extract_info`,
  `_download`) untested. **Keystone** — the accuracy roadmap edits this file.
- `shared/youtube/channel_extractor.py` — pure: `_walk_entries`, `_to_video_meta`,
  `_to_playlist_meta`, `_format_upload_date`; network: `list_channel_videos`,
  `list_channel_playlists`, `fetch_video_meta`.
- `shared/youtube/video_downloader.py` — pure: `friendly_download_error`,
  `_human_bytes`; network: `download_video`.

Goal: bring all three under the ≥85% coverage gate by mocking the yt_dlp boundary,
so later speed/accuracy/CX changes are regression-safe.

## Proposed changes

Mock strategy (no production code touched — tests use `monkeypatch`):

- **transcript_fetcher**: monkeypatch the module-level `_extract_info` / `_download`
  to drive `fetch_transcript` through every branch; cover `_extract_info` itself by
  patching `tf.YoutubeDL` with a fake context manager; cover `_download` by patching
  `urllib.request.urlopen`.
- **channel_extractor**: patch `YoutubeDL` (fake CM whose `extract_info` returns a
  canned info dict) for the three list/fetch functions; test the pure walk/convert
  helpers directly.
- **video_downloader**: test `friendly_download_error` (all branches) + `_human_bytes`
  (B/KB/MB/GB) directly; patch `YoutubeDL` for `download_video`, with the fake's
  `progress_hooks` firing `downloading`+`finished` events against a real `tmp_path`
  file so the on-disk existence check passes.

Add a reusable `make_fake_ydl(...)` factory to `tests/conftest.py` (DRY across the
two modules that construct `YoutubeDL` directly).

Coverage gate (`pyproject.toml` `[tool.coverage.run] include`): add the three
modules as each lands under 85%. **Sequencing this phase:** transcript_fetcher first
(this commit), then channel_extractor, then video_downloader — each added to the
include list only once its tests hold the line ≥85%.

Note two intentionally-unreachable lines in `transcript_fetcher.fetch_transcript`
(the `pick is None` branch at 103-105 is guarded dead code — the `not manual and not
auto` check at 98 already returns). Not covered; documented here so the 85% target
accounts for it.

## Testing and validation

New/updated test modules: `test_transcript_fetcher.py` (network path),
`test_channel_extractor.py`, `test_video_downloader.py`. Done per module = that
module added to the gate `include` and overall `--cov-fail-under=85` still green via
`scripts/loop_grade.sh` (exit 0). No network is hit — all boundaries mocked.

## Follow-ups

Phase 2b (speed: parallel transcript fetch + lazy yt_dlp import) and Phase 2c
(accuracy + CX) build on this coverage. Each is its own phase with human approval.
