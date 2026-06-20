# youtube_scraper

Personal-use CLI that walks a YouTube channel and dumps every public video's
title + transcript into a single JSONL file.

## Install

```bash
# from the repo root
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

Requires **Python 3.10+**. One third-party library does all of the heavy lifting:

- [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) — channel/playlist listing **and** transcript fetch via signed timedtext URLs.

No API keys, no auth.

## Usage

```bash
# basic — scrape every public video on a channel
python apps/youtube_scraper/scrape.py https://youtube.com/@SomeChannel

# slow down between requests (useful if YouTube starts rate-limiting)
python apps/youtube_scraper/scrape.py https://youtube.com/@SomeChannel --delay 3

# different language priority (try Japanese first, then English)
python apps/youtube_scraper/scrape.py https://youtube.com/@SomeChannel --languages ja,en

# write to a custom directory
python apps/youtube_scraper/scrape.py https://youtube.com/@SomeChannel --output-dir ~/data/yt

# ignore an existing output file and start fresh
python apps/youtube_scraper/scrape.py https://youtube.com/@SomeChannel --no-resume

# verbose logs (function-level timings)
python apps/youtube_scraper/scrape.py https://youtube.com/@SomeChannel --verbose
```

### Supported URL forms

| Form | Example |
| --- | --- |
| Handle | `https://youtube.com/@SomeChannel` |
| Channel ID | `https://youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxx` |
| Custom name | `https://youtube.com/c/SomeName` |
| Legacy user | `https://youtube.com/user/SomeName` |

`/videos` is appended automatically when missing.

## Output

Two artifacts per run, both UTF-8:

```
output/<channel_handle>_<YYYYMMDD>.jsonl
output/<channel_handle>_<YYYYMMDD>.summary.json
```

### JSONL — one video per line

```json
{"id":"abc123","title":"How to Export Mangoes to Japan","url":"https://www.youtube.com/watch?v=abc123","upload_date":"2024-03-15","duration_seconds":754,"language":"th","status":"OK","transcript":"สวัสดีครับวันนี้เราจะมาพูดถึง..."}
```

#### Schema

| Field | Type | Notes |
| --- | --- | --- |
| `id` | string | YouTube video ID. |
| `title` | string | Raw video title. |
| `url` | string | Full watch URL. |
| `upload_date` | string | `YYYY-MM-DD`. May be empty when flat extraction omits it. |
| `duration_seconds` | int | Total seconds; `0` if unknown. |
| `language` | string | One of: matched preferred-language code (e.g. `"th"`, `"en"`), `"auto"` for fallback transcripts, `"none"` when no transcript was retrieved. |
| `status` | string | One of `OK`, `NO_CAPTIONS`, `DISABLED`, `NETWORK_ERROR`, `OTHER`. |
| `transcript` | string | Single-line cleaned text; empty when `status != "OK"`. |

### summary.json

Aggregate stats for the run — channel, ISO-8601 timestamp, totals, failures
broken down by status, elapsed seconds, and the output file path.

## Resume behaviour

- The output `.jsonl` *is* the checkpoint — there's no separate state file.
- On startup, if the file already exists for the same `<handle>_<YYYYMMDD>`,
  the CLI reads its IDs and asks: `Found N existing videos. Resume? [Y/n]`.
  - `Y` (default) → skip those IDs, append new lines for the rest.
  - `n` → delete the file and start fresh.
- Pass `--no-resume` to skip the prompt and always start fresh.
- Killing the process (Ctrl+C) between videos is safe — each line is
  flushed immediately after fetch.

## Reading the JSONL in Python

```python
import json

with open("output/SomeChannel_20260504.jsonl", encoding="utf-8") as fh:
    videos = [json.loads(line) for line in fh if line.strip()]

ok = [v for v in videos if v["status"] == "OK"]
print(f"{len(ok)}/{len(videos)} have transcripts")
```

`jq` works too:

```bash
jq -c 'select(.status == "OK") | {id, title}' output/SomeChannel_20260504.jsonl
```

## Troubleshooting

### "URL must be a YouTube channel URL"
The URL didn't match `@handle`, `/channel/UC...`, `/c/name`, or `/user/name`.
Paste a direct channel URL — not a video URL or a search result.

### Many `NO_CAPTIONS`
Expected on channels that don't enable auto-captions or upload silent /
music-only videos. Check the summary file to confirm the count is
proportional, not 100%.

### Many `DISABLED`
The uploader explicitly turned off captions for those videos. Nothing the
scraper can do; transcripts simply don't exist server-side.

### Many `NETWORK_ERROR`
- Increase `--delay` (try `--delay 3` or `--delay 5`).
- You may have hit YouTube's per-IP throttle; wait 10–30 minutes and run
  again — it will resume from where it stopped.
- VPN / corporate proxy users: try without the proxy.

### "HTTP Error 429" or 403 from yt-dlp during channel listing
You're being rate-limited at the listing step. Wait a few minutes and retry;
the resume flow will skip everything you already have.

### Thai (or other non-Latin) transcripts look mangled in your terminal
The file is correct UTF-8 — your terminal font / encoding is the issue.
Open the JSONL in any editor that supports UTF-8.

### "ImportError: No module named yt_dlp"
You forgot to activate the virtualenv or skipped `pip install`. Re-run:

```bash
source venv/bin/activate
pip install -r requirements.txt
```

## Limitations

- `yt-dlp` flat extraction sometimes omits `upload_date` and `duration` —
  those fields appear as `""` / `0` rather than blocking the run.
- Transcript fetch was migrated off `youtube-transcript-api` in v0.5.1:
  YouTube began returning HTTP 429 on the bare `/api/timedtext` endpoint
  it relies on. yt-dlp's signed timedtext URLs survive the gating. See
  `CHANGELOG.md` for the rationale.
- Personal/research use only. Don't redistribute scraped transcripts of
  copyrighted content.
