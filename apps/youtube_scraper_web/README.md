# youtube_scraper_web

Streamlit web UI wrapping the same `shared/` services that power the CLI.
Local-only, internal use — no auth, no deployment.

## Install

From the repo root:

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt   # installs streamlit + yt-dlp
```

## Run

```bash
streamlit run apps/youtube_scraper_web/app.py
```

Streamlit prints a local URL (typically `http://localhost:8501`) and opens it
in your default browser. Stop the server with **Ctrl+C** in the terminal.

### Run on a custom port (multiple internal users)

```bash
streamlit run apps/youtube_scraper_web/app.py --server.port 8600
```

### LAN access (let coworkers on the same network use it)

```bash
streamlit run apps/youtube_scraper_web/app.py --server.address 0.0.0.0
```

Then share the LAN IP printed in the terminal. Still no auth — only do this
on a trusted internal network.

## Using the UI

1. Paste a URL into the box. The app auto-detects what kind of URL it is and
   adjusts the panel underneath:

   | URL kind | Examples | Panel shown |
   | --- | --- | --- |
   | **Channel** | `youtube.com/@SomeChannel`, `/channel/UC...`, `/c/Name`, `/user/Name` | Two tabs: **All videos** or **Pick a playlist** |
   | **Playlist** | `youtube.com/playlist?list=PL…` | Confirm + scrape that playlist |
   | **Single video** | `youtu.be/VIDEO_ID`, `youtube.com/watch?v=VIDEO_ID`, `youtube.com/shorts/VIDEO_ID`, `youtube.com/live/VIDEO_ID`, or the raw 11-char ID | Confirm + scrape that one video |

2. (Optional) Adjust **language priority**, **delay**, **save-to-disk**, and
   **start-fresh** in the sidebar.

3. **Channel mode — pick playlist(s):**
   1. Tap the **Pick playlist(s)** tab.
   2. Click **Load this channel's playlists** (one-time per channel; cached
      for an hour).
   3. **Tick one or more** playlists in the multi-select. Each row shows
      title, video count, and playlist ID. Use **Select all** as a shortcut.
   4. Click **Scrape N playlist(s)**. Each picked playlist is processed in
      sequence with smart resume; the final **Download JSONL** button gives
      you all picked playlists merged into a single file (videos that appear
      in more than one playlist are deduplicated by ID).

4. Watch the progress bar + live table. When the run ends, click
   **Download JSONL** (and optionally **Download summary.json**).

If `Also save to ./output` is enabled, the same files are written to
`./output/<handle>_<YYYYMMDD>.jsonl` so you keep a local archive.

## Smart resume (channel + playlist modes)

For channel and playlist runs, the UI scans **every** prior
`<handle>_*.jsonl` file in `./output/` (where `<handle>` is the channel
handle for channel runs and the playlist ID for playlist runs) and
partitions the source's videos into three buckets:

| Bucket | Action | Why |
| --- | --- | --- |
| **Skip — already done** | Carried into the merged download untouched. | Status was `OK`, `NO_CAPTIONS`, `DISABLED`, or `OTHER`; re-fetching won't change the result. |
| **Retry — network errors** | Re-fetched. | `NETWORK_ERROR` is transient. |
| **New** | Fetched. | Never seen before. |

You'll see four `st.metric` cards before the run starts (`Channel total`,
`Skip`, `Retry`, `New`) so you know what's about to happen. The
**Download JSONL** button at the end gives you the **merged view**: prior
records + this run's records, deduplicated by ID with this run winning on
conflict. That's the single file you feed into your downstream tools.

To override resume — for example, if YouTube updated captions and you want
fresh data — tick **"Start fresh (ignore prior runs)"** in the sidebar.
The existing `<handle>_<TODAY>.jsonl` (if any) is renamed to `*.jsonl.bak`,
prior history is ignored, and every video is re-fetched.

Single-video mode never uses resume — there's only one video to fetch.

## Output shape

Identical to the CLI — see `apps/youtube_scraper/README.md` for the schema,
language semantics, and troubleshooting matrix.

## Privacy / network usage

This tool runs **100% locally**. Nothing about your URLs, transcripts, or
output files is uploaded anywhere — the browser connects to `localhost`,
which is your laptop talking to itself.

Outbound network calls during a run:

- `youtube.com` — required to list the channel and fetch each transcript.
- `streamlit.io` — usage telemetry, **disabled by default** in this repo
  (see `.streamlit/config.toml` → `browser.gatherUsageStats = false`).

Streamlit itself is free, open-source (MIT), and never requires an account
or a deployment to run. The `streamlit run …` command just starts a small
Python web server on your machine — no SaaS involved.

To prove it for yourself while a run is in progress (macOS):

```bash
# All TCP connections from the streamlit process — should only show
# 127.0.0.1 listeners + youtube.com / googlevideo.com peers.
lsof -i -P -n -p "$(pgrep -f 'streamlit run' | head -1)"
```

## Limitations

- **Single-job UI.** Streamlit runs the script per session; if multiple
  users hit the same instance simultaneously each gets their own job, but a
  single user starting a new run while another is in flight will have to
  wait for the first to finish (browser tab is blocked).
- No background mode. Closing the browser tab during a run cancels it
  client-side; the server-side fetch continues until the next iteration
  yields, then exits.
- Streamlit and its transitive deps (pandas, pyarrow, altair) add ~150 MB.
  That's the price of getting a usable UI in <300 lines.
