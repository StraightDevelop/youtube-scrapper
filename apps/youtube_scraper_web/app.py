"""Streamlit web UI for the YouTube transcript scraper.

Internal, local-only tool. Run with::

    streamlit run apps/youtube_scraper_web/app.py

The UI accepts a channel URL, a playlist URL, or a single video URL/ID,
fetches transcripts using the language priority configured in the sidebar,
and offers the result as a downloadable JSONL file. All of the heavy lifting
is delegated to ``shared.youtube.*`` and ``shared.io.*`` so behaviour stays
identical to the CLI.

Channel mode supports two sub-flows:
  * **All videos on the channel** — every public video on the channel.
  * **Pick playlist(s)** — multi-select; each picked playlist is scraped in
    sequence with smart resume per playlist, then merged into a single
    downloadable JSONL.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Bootstrap the monorepo's ``shared/`` package so ``streamlit run`` works
# regardless of the current working directory.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import streamlit as st  # noqa: E402

from shared.io.jsonl_writer import append_jsonl  # noqa: E402
from shared.io.resume_reader import collect_channel_history  # noqa: E402
from shared.io.summary_writer import write_summary  # noqa: E402
from shared.utils.logger import configure_logging  # noqa: E402
from shared.youtube.channel_extractor import (  # noqa: E402
    PlaylistMeta,
    VideoMeta,
    fetch_video_meta,
    list_channel_playlists,
    list_channel_videos,
)
from shared.youtube.concurrent_fetch import stream_transcripts  # noqa: E402
from shared.youtube.transcript_fetcher import friendly_transcript_status  # noqa: E402
from shared.youtube.url_validator import (  # noqa: E402
    extract_channel_handle,
    extract_playlist_id,
    parse_video_id,
    validate_channel_url,
    validate_playlist_url,
)
from shared.youtube.video_downloader import (  # noqa: E402
    DownloadError as _VideoDownloadError,
    DownloadResult,
    Quality as VideoQuality,
    download_video,
    friendly_download_error,
)

configure_logging(level=logging.INFO)
logger = logging.getLogger("scraper.web")

DEFAULT_LANGUAGES = "th,en"
DEFAULT_DELAY = 1.0
DEFAULT_MAX_WORKERS = 4   # parallel transcript fetches (Phase 2b); see concurrent_fetch
FAILURE_STATUSES = ("NO_CAPTIONS", "DISABLED", "NETWORK_ERROR", "OTHER")
# Statuses worth retrying on a follow-up run. NO_CAPTIONS / DISABLED won't
# change without the uploader's intervention. NETWORK_ERROR is transient,
# and OTHER is "unknown failure" by definition — both deserve another shot
# (especially since v0.5.1 backfills records previously stuck on OTHER from
# the youtube-transcript-api 429 incident).
RETRY_STATUSES = {"NETWORK_ERROR", "OTHER"}

Record = dict[str, Any]


# ─── URL routing + caching ──────────────────────────────────────────────────


def detect_mode(raw: str) -> tuple[str, str]:
    """Classify a paste-box value as channel, playlist, or single video.

    Returns:
        ``("channel", canonical_url)``, ``("playlist", canonical_url)``, or
        ``("video", video_id)``.
    Raises:
        ValueError: if the input matches none of the shapes.
    """
    raw = raw.strip()
    try:
        return "channel", validate_channel_url(raw)
    except ValueError:
        pass
    try:
        return "playlist", validate_playlist_url(raw)
    except ValueError:
        pass
    return "video", parse_video_id(raw)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_list_channel_playlists(channel_url: str) -> list[PlaylistMeta]:
    """1-hour cached wrapper around :func:`list_channel_playlists`."""
    return list_channel_playlists(channel_url)


def parse_languages(raw: str) -> list[str]:
    """Split a ``--languages``-style string into a clean priority list."""
    return [lang.strip().lower() for lang in raw.split(",") if lang.strip()]


# ─── Record helpers ─────────────────────────────────────────────────────────


def build_record(
    video: VideoMeta,
    status: str,
    language: str,
    transcript: str,
) -> Record:
    """Assemble a JSONL record matching the FR-8 schema."""
    return {
        "id": video["id"],
        "title": video["title"],
        "url": video["url"],
        "upload_date": video["upload_date"],
        "duration_seconds": video["duration_seconds"],
        "language": language,
        "status": status,
        "transcript": transcript,
    }


def records_to_jsonl_bytes(records: list[Record]) -> bytes:
    """Serialise records to UTF-8 JSONL bytes (compact, one per line)."""
    if not records:
        return b""
    parts = (json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in records)
    return ("\n".join(parts) + "\n").encode("utf-8")


def _merge_records(prior: list[Record], fresh: list[Record]) -> list[Record]:
    """Merge prior and freshly-fetched records, with ``fresh`` overriding by ID.

    Order: prior records first (in their original order), then IDs only seen
    in ``fresh``. Prior records whose ID was re-fetched are replaced in place.
    """
    fresh_by_id = {r.get("id"): r for r in fresh if isinstance(r.get("id"), str)}
    merged: list[Record] = []
    seen: set[str] = set()
    for record in prior:
        rid = record.get("id")
        if not isinstance(rid, str):
            continue
        merged.append(fresh_by_id[rid] if rid in fresh_by_id else record)
        seen.add(rid)
    for record in fresh:
        rid = record.get("id")
        if isinstance(rid, str) and rid not in seen:
            merged.append(record)
            seen.add(rid)
    return merged


def _dedupe_by_id(records: list[Record]) -> list[Record]:
    """Return ``records`` deduplicated by ID — last occurrence wins."""
    by_id: dict[str, Record] = {}
    for record in records:
        rid = record.get("id")
        if isinstance(rid, str):
            by_id[rid] = record
    return list(by_id.values())


def _resolve_videos(mode: str, target: str) -> list[VideoMeta]:
    """List videos for the given mode + target.

    ``list_channel_videos`` accepts both channel and playlist URLs (yt-dlp's
    flat extraction works the same on either).
    """
    if mode == "channel":
        return list_channel_videos(target)
    if mode == "playlist":
        return list_channel_videos(target)
    return [fetch_video_meta(target)]


def _table_view(records: list[Record]) -> list[Record]:
    """Compact view for live progress (last 50 rows for readability)."""
    visible = records[-50:]
    offset = len(records) - len(visible)
    rows: list[Record] = []
    for i, r in enumerate(visible, start=1):
        title = str(r.get("title", ""))
        status = str(r.get("status", ""))
        rows.append({
            "#": offset + i,
            "status": status,
            "lang": r.get("language"),
            "chars": len(str(r.get("transcript", ""))),
            "title": title[:80] + ("…" if len(title) > 80 else ""),
            # Plain-English reason for non-OK rows (CX, Phase 2c); blank when OK.
            "why": friendly_transcript_status(status),
        })
    return rows


# ─── Top-level page ─────────────────────────────────────────────────────────


def render_page() -> None:
    """Top-level Streamlit page renderer (called once per session re-run)."""
    st.set_page_config(
        page_title="YouTube Transcript & Video Saver",
        page_icon="🎬",
        layout="centered",
    )
    _inject_css()
    _render_hero()

    common = _render_sidebar()

    tab_transcript, tab_download = st.tabs(["📝  Get transcript", "📥  Download video"])
    with tab_transcript:
        _render_transcript_tab(common)
    with tab_download:
        _render_download_tab()


def _render_sidebar() -> dict[str, Any]:
    """Render the left-hand settings panel and return its values.

    Most users only ever toggle "Save a copy on my computer". The other
    controls (language preference, wait between requests, ignore previous
    runs) live inside an "Advanced (optional)" expander that's collapsed
    by default so non-technical users aren't asked to make choices about
    things they don't recognise.
    """
    with st.sidebar:
        st.markdown("### Settings")
        save_to_disk = st.checkbox(
            "Save a copy on my computer",
            value=True,
            help=(
                "When ticked, every transcript or video is also written to the "
                "`output/` folder next to this app. Untick to keep things in "
                "the browser only."
            ),
        )
        with st.expander("Advanced (optional)", expanded=False):
            languages_raw = st.text_input(
                "Caption language preference",
                value=DEFAULT_LANGUAGES,
                help=(
                    "Comma-separated list of language codes — the first available "
                    "match wins. We always fall back to any auto-generated "
                    "captions when none of these are available. Examples: "
                    "`th,en` for Thai-then-English, `en` for English only."
                ),
            )
            delay_seconds = st.number_input(
                "Wait between videos (seconds)",
                min_value=0.0, max_value=10.0,
                value=DEFAULT_DELAY, step=0.5,
                help=(
                    "Politeness pause before each transcript request (applied per "
                    "parallel worker). Increase to 2-3 if YouTube starts blocking us."
                ),
            )
            max_workers = st.number_input(
                "Parallel fetches",
                min_value=1, max_value=16,
                value=DEFAULT_MAX_WORKERS, step=1,
                help=(
                    "How many transcripts to fetch at once when scraping a channel "
                    "or playlist. Higher is faster but more likely to be rate-limited "
                    "by YouTube. 4 is a safe default."
                ),
            )
            start_fresh = st.checkbox(
                "Ignore previous runs",
                value=False,
                help=(
                    "Off by default: channel and playlist scrapes pick up where "
                    "they left off and only re-fetch videos that failed last "
                    "time. Tick this to download everything from scratch."
                ),
            )
        st.markdown(
            "<div style='margin-top:1rem;font-size:0.78rem;color:#737373;line-height:1.5;'>"
            "Runs locally on your computer. Nothing is uploaded anywhere "
            "except the videos pulled from YouTube."
            "</div>",
            unsafe_allow_html=True,
        )

    return dict(
        languages_raw=languages_raw,
        delay_seconds=float(delay_seconds),
        max_workers=int(max_workers),
        save_to_disk=bool(save_to_disk),
        start_fresh=bool(start_fresh),
    )


def _render_transcript_tab(common: dict[str, Any]) -> None:
    """The original transcript flow, now scoped to a top-level tab."""
    url_input = st.text_input(
        label="YouTube URL (transcript tab)",
        label_visibility="collapsed",
        placeholder="Paste a YouTube link here — video, playlist, or channel",
        key="url_input_transcript",
    )

    if not url_input.strip():
        _render_empty_state(
            tagline="Paste a YouTube link above and we'll pull the captions for you.",
            examples=[
                ("📺", "A single video", "https://youtu.be/dQw4w9WgXcQ"),
                ("📁", "A playlist", "https://youtube.com/playlist?list=PL…"),
                ("🎤", "A whole channel", "https://youtube.com/@SomeChannel"),
            ],
        )
        return

    try:
        mode, target = detect_mode(url_input)
    except ValueError as exc:
        st.error(f"That doesn't look like a YouTube link — {exc}")
        return

    _render_detection_chip(mode, target)

    if mode == "channel":
        _render_channel_panel(target, common)
    elif mode == "playlist":
        _render_playlist_panel(target, common)
    else:
        _render_video_panel(target, common)


def _render_download_tab() -> None:
    """Standalone single-video download flow.

    Intentionally simpler than the transcript tab — accepts only a video URL
    or 11-character ID, asks one question (quality), saves the file under
    ``output/videos/`` and offers an in-browser download.
    """
    url_input = st.text_input(
        label="YouTube video URL (download tab)",
        label_visibility="collapsed",
        placeholder="Paste a single YouTube video link — e.g. https://youtu.be/dQw4w9WgXcQ",
        key="url_input_download",
    )

    if not url_input.strip():
        _render_empty_state(
            tagline=(
                "Paste a YouTube video link to save the actual video file "
                "(an `.mp4`) to your computer."
            ),
            examples=[
                ("📺", "Short link", "https://youtu.be/dQw4w9WgXcQ"),
                ("🔗", "Full URL", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
                ("🆔", "Just the ID", "dQw4w9WgXcQ"),
            ],
        )
        return

    try:
        video_id = parse_video_id(url_input)
    except ValueError:
        # Most likely the user pasted a playlist or channel URL into this tab.
        try:
            mode, _ = detect_mode(url_input)
        except ValueError:
            st.error("That doesn't look like a YouTube video link.")
            return
        if mode == "playlist":
            st.error(
                "This tab downloads one video at a time. For a whole playlist, "
                "use the **Get transcript** tab — or paste a single video link from "
                "inside the playlist here."
            )
        elif mode == "channel":
            st.error(
                "This tab downloads one video at a time. For a whole channel, "
                "use the **Get transcript** tab — or paste a single video link from "
                "the channel here."
            )
        else:
            st.error("That doesn't look like a YouTube video link.")
        return

    _render_detection_chip("video", video_id)
    meta = _get_video_meta_safe(video_id)
    _render_video_card(video_id, meta)

    quality_choice = st.radio(
        "How big should the file be?",
        options=["best", "small"],
        format_func=lambda q: {
            "best": "🎬  Best quality (recommended)",
            "small": "⚡  Smaller file (faster download)",
        }[q],
        key="dl_quality",
        horizontal=False,
    )

    if st.button(
        "Download video to my computer",
        type="primary",
        key="btn_download_video",
        use_container_width=True,
    ):
        _run_video_download(video_id, quality_choice)


def _run_video_download(video_id: str, quality: str) -> None:
    """Drive a single download with progress UI + result rendering."""
    output_dir = (_REPO_ROOT / "output" / "videos").resolve()
    progress = st.progress(0.0, text="Getting ready…")

    def _on_progress(fraction: float, label: str) -> None:
        progress.progress(min(max(fraction, 0.0), 0.99), text=label)

    try:
        result = download_video(
            video_id=video_id,
            output_dir=output_dir,
            quality=quality,  # type: ignore[arg-type]
            progress_callback=_on_progress,
        )
    except _VideoDownloadError as exc:
        progress.empty()
        st.error(f"Couldn't download this video — {friendly_download_error(exc)}")
        return
    except Exception as exc:  # noqa: BLE001
        progress.empty()
        st.error(f"Something went wrong while downloading: {exc}")
        return

    progress.progress(1.0, text="Done!")
    _render_download_result(result)


def _render_download_result(result: DownloadResult) -> None:
    """Friendly success card with a 'Save to my computer' button."""
    saved_path = Path(result["path"])
    pretty_size = _human_bytes_short(result["bytes"])
    pretty_duration = _format_duration(result["duration_seconds"])

    st.markdown(
        f"""
        <div class='yt-card'>
          <div style='font-size:1.1rem;font-weight:700;color:var(--ink);margin-bottom:0.5rem;'>
            ✅ Video saved
          </div>
          <div class='yt-meta' style='line-height:1.7;'>
            <b>{saved_path.name}</b><br>
            {pretty_size}{(" · " + pretty_duration) if pretty_duration else ""}<br>
            <span style='font-family:ui-monospace,Menlo,Consolas,monospace;font-size:0.78rem;'>
              {saved_path.parent}
            </span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # In-browser download. We only load the bytes once the user clicks; for
    # large files Streamlit handles the streaming.
    try:
        file_bytes = saved_path.read_bytes()
    except OSError as exc:
        st.warning(f"Saved on disk but couldn't prepare the in-browser download: {exc}")
        return

    mime = "video/mp4" if result["ext"].lower() == "mp4" else "application/octet-stream"
    st.download_button(
        "Save to my computer",
        data=file_bytes,
        file_name=saved_path.name,
        mime=mime,
        use_container_width=True,
    )


def _human_bytes_short(n: int) -> str:
    """Compact human-readable byte count for the success card."""
    if n < 1024:
        return f"{n} B"
    units = ("KB", "MB", "GB", "TB")
    size = float(n) / 1024
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


# ─── Per-mode panels ────────────────────────────────────────────────────────


def _render_channel_panel(channel_url: str, common: dict) -> None:
    """Channel mode UI: tabs for "All videos" or "Pick playlist(s)"."""
    handle = extract_channel_handle(channel_url)
    tab_all, tab_playlists = st.tabs(["All videos", "Pick playlist(s)"])

    with tab_all:
        st.caption(
            "We'll go through every public video on this channel and pull the "
            "captions where they're available. Bigger channels can take a while."
        )
        if st.button(
            "Get all transcripts",
            type="primary",
            key="btn_scrape_all",
            use_container_width=True,
        ):
            run_scrape_single(
                mode="channel", target=channel_url, handle=handle, **common,
            )

    with tab_playlists:
        st.caption(
            "Some channels organise videos into playlists. Load the list "
            "below and tick the ones you want — we'll fetch transcripts only "
            "for those videos and combine everything into one downloadable file."
        )
        load_clicked = st.button("Show this channel's playlists", key="btn_load_playlists")
        cache_key = f"playlists_for::{channel_url}"
        if load_clicked:
            with st.spinner("Loading playlists from YouTube…"):
                try:
                    playlists = cached_list_channel_playlists(channel_url)
                except Exception as exc:  # noqa: BLE001
                    st.exception(exc)
                    return
            st.session_state[cache_key] = playlists

        playlists = st.session_state.get(cache_key)
        if playlists is None:
            return
        if not playlists:
            st.warning("This channel has no public playlists.")
            return

        st.success(f"Found {len(playlists)} playlist(s).")
        options = {pl["id"]: pl for pl in playlists}
        selected_ids = st.multiselect(
            "Pick one or more playlists to scrape",
            options=list(options.keys()),
            format_func=lambda pid: _format_playlist_option(options[pid]),
            key="multiselect_playlists",
            help="Tip: re-running with the same selection is cheap — already-done videos are skipped automatically.",
        )

        ctrl_cols = st.columns([3, 1])
        with ctrl_cols[1]:
            if st.button("Select all", key="btn_select_all_playlists", use_container_width=True):
                # Streamlit doesn't let us mutate the multiselect's value
                # directly after render, so set a session_state default and
                # rerun — the next render picks it up.
                st.session_state["multiselect_playlists"] = list(options.keys())
                st.rerun()

        with ctrl_cols[0]:
            scrape_clicked = st.button(
                f"Scrape {len(selected_ids) or 'selected'} playlist(s)",
                type="primary",
                key="btn_scrape_selected_playlists",
                use_container_width=True,
                disabled=len(selected_ids) == 0,
            )

        if scrape_clicked and selected_ids:
            chosen = [options[pid] for pid in selected_ids]
            if len(chosen) == 1:
                pl = chosen[0]
                run_scrape_single(
                    mode="playlist", target=pl["url"], handle=pl["id"],
                    playlist_title=pl["title"], channel_handle=handle,
                    **common,
                )
            else:
                run_scrape_playlists(
                    channel_handle=handle, playlists=chosen, **common,
                )


def _render_playlist_panel(playlist_url: str, common: dict) -> None:
    """Playlist mode UI: confirm + scrape."""
    pid = extract_playlist_id(playlist_url)
    st.caption("We'll pull captions for every video in this playlist and combine them into one file.")
    if st.button(
        "Get all transcripts",
        type="primary",
        key="btn_scrape_playlist_direct",
        use_container_width=True,
    ):
        run_scrape_single(mode="playlist", target=playlist_url, handle=pid, **common)


def _render_video_panel(video_id: str, common: dict) -> None:
    """Video mode UI: thumbnail-card preview + transcript view."""
    meta = _get_video_meta_safe(video_id)
    _render_video_card(video_id, meta)

    if st.button(
        "Get transcript",
        type="primary",
        key="btn_scrape_video",
        use_container_width=True,
    ):
        st.session_state["_video_transcript_target"] = video_id

    if st.session_state.get("_video_transcript_target") == video_id:
        run_scrape_single(mode="video", target=video_id, handle=video_id, **common)


def _format_playlist_option(pl: PlaylistMeta) -> str:
    """Pretty label for an `st.multiselect`/`st.selectbox` playlist option."""
    title = pl["title"][:80]
    count = pl.get("video_count") or 0
    if count:
        return f"{title}  —  {count} videos  ({pl['id']})"
    return f"{title}  —  ({pl['id']})"


# ─── Per-source scrape (used by both single + multi orchestrators) ──────────


def _scrape_one_source(
    *,
    mode: str,
    target: str,
    handle: str,
    languages: list[str],
    delay_seconds: float,
    save_to_disk: bool,
    start_fresh: bool,
    output_dir: Path,
    today: str,
    max_workers: int | None = None,
    section_label: str | None = None,
) -> dict[str, Any]:
    """Run the full partition + fetch + merge cycle for a single source.

    Side-effects: writes JSONL to disk if ``save_to_disk``; renders progress
    UI inside the current Streamlit container/section.

    Returns:
        ``{"merged": list[Record], "this_run": {...}, "resume": {...},
        "source_total_videos": int, "jsonl_path": str | None}``
    """
    if section_label:
        st.markdown(f"#### {section_label}")
    st.write(_describe_target(mode, target))

    with st.status("Looking up videos…", expanded=False) as status_box:
        try:
            videos = _resolve_videos(mode, target)
            status_box.update(label=f"Found {len(videos)} video(s).", state="complete")
        except Exception as exc:  # noqa: BLE001
            status_box.update(label="Couldn't load that page.", state="error")
            st.exception(exc)
            return _empty_result()

    total = len(videos)
    if total == 0:
        st.warning("No videos found in this source.")
        return _empty_result()

    jsonl_path = output_dir / f"{handle}_{today}.jsonl"

    # Resume partition
    prior_records: list[Record] = []
    prior_status_by_id: dict[str, str] = {}
    if mode in ("channel", "playlist") and not start_fresh:
        prior_records, prior_status_by_id = collect_channel_history(output_dir, handle)

    skipped_records: list[Record] = []
    to_fetch: list[VideoMeta] = []
    retry_count = 0
    new_count = 0
    for video in videos:
        prev_status = prior_status_by_id.get(video["id"])
        if prev_status is None or start_fresh:
            to_fetch.append(video)
            new_count += 1
        elif prev_status in RETRY_STATUSES:
            to_fetch.append(video)
            retry_count += 1
        else:
            existing = next(
                (r for r in prior_records if r.get("id") == video["id"]),
                None,
            )
            if existing is not None:
                skipped_records.append(existing)

    skip_count = len(skipped_records)
    fetch_count = len(to_fetch)

    if mode in ("channel", "playlist"):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total videos", total)
        c2.metric("Already saved", skip_count, help="Videos with a transcript from a previous run.")
        c3.metric("Will retry", retry_count, help="Videos that failed last time and are worth another try.")
        c4.metric("New", new_count, help="Videos we haven't tried yet.")

    if save_to_disk and jsonl_path.exists() and start_fresh:
        backup = jsonl_path.with_suffix(".jsonl.bak")
        jsonl_path.rename(backup)
        st.info(f"Existing `{jsonl_path.name}` renamed to `{backup.name}` (start-fresh).")

    records_this_run: list[Record] = []
    failures: dict[str, int] = {status: 0 for status in FAILURE_STATUSES}
    successful = 0
    elapsed = 0.0

    if fetch_count == 0:
        st.success(
            f"Nothing to fetch — all {total} video(s) already have records."
        )
    else:
        progress = st.progress(
            0.0, text=f"Starting transcription for {fetch_count} video(s)…",
        )
        table_holder = st.empty()
        started = time.time()

        # Fetch in parallel (bounded); results arrive in completion order, which is
        # safe — records are keyed by id and the summary is order-independent.
        done = 0
        for video, status, language, transcript in stream_transcripts(
            to_fetch, languages, max_workers=max_workers, delay=delay_seconds,
        ):
            done += 1
            progress.progress(done / fetch_count, text=f"[{done}/{fetch_count}] {video['title'][:80]}")
            record = build_record(video, status=status, language=language, transcript=transcript)
            records_this_run.append(record)
            if save_to_disk:
                append_jsonl(jsonl_path, record)
            if status == "OK":
                successful += 1
            else:
                failures[status] = failures.get(status, 0) + 1
            table_holder.dataframe(
                _table_view(records_this_run), hide_index=True, use_container_width=True,
            )

        elapsed = time.time() - started
        progress.progress(1.0, text=f"Done in {elapsed:.1f}s")

    merged = _merge_records(skipped_records, records_this_run)
    return {
        "merged": merged,
        "source_total_videos": total,
        "this_run": {
            "fetched": fetch_count,
            "successful": successful,
            "failed": sum(failures.values()),
            "failures_by_status": failures,
            "elapsed_seconds": round(elapsed, 2),
        },
        "resume": {
            "start_fresh": start_fresh,
            "skipped_already_done": skip_count,
            "retried_network_errors": retry_count,
            "new_videos": new_count,
        },
        "jsonl_path": str(jsonl_path) if save_to_disk else None,
    }


def _empty_result() -> dict[str, Any]:
    """Result shape for an aborted/empty source — keeps callers branch-free."""
    return {
        "merged": [],
        "source_total_videos": 0,
        "this_run": {
            "fetched": 0, "successful": 0, "failed": 0,
            "failures_by_status": {s: 0 for s in FAILURE_STATUSES},
            "elapsed_seconds": 0.0,
        },
        "resume": {
            "start_fresh": False, "skipped_already_done": 0,
            "retried_network_errors": 0, "new_videos": 0,
        },
        "jsonl_path": None,
    }


def _describe_target(mode: str, target: str) -> str:
    """Human-readable one-liner describing what's being scraped."""
    if mode == "channel":
        return f"Channel: `{target}`"
    if mode == "playlist":
        return f"Playlist: `{target}`"
    return f"Video: `https://www.youtube.com/watch?v={target}`"


# ─── Orchestrators ──────────────────────────────────────────────────────────


def run_scrape_single(
    *,
    mode: str,
    target: str,
    handle: str,
    languages_raw: str,
    delay_seconds: float,
    max_workers: int | None = None,
    save_to_disk: bool,
    start_fresh: bool,
    playlist_title: str | None = None,
    channel_handle: str | None = None,
) -> None:
    """Run one source (channel, playlist, or video) and render its result."""
    languages = parse_languages(languages_raw)
    if not languages:
        st.error("Specify at least one language code.")
        return

    output_dir = (_REPO_ROOT / "output").resolve()
    today = datetime.now().strftime("%Y%m%d")

    result = _scrape_one_source(
        mode=mode, target=target, handle=handle,
        languages=languages, delay_seconds=delay_seconds, max_workers=max_workers,
        save_to_disk=save_to_disk, start_fresh=start_fresh,
        output_dir=output_dir, today=today,
    )

    summary = _build_single_summary(
        mode=mode, target=target, handle=handle,
        playlist_title=playlist_title, channel_handle=channel_handle,
        result=result,
    )
    if save_to_disk:
        summary_path = output_dir / f"{handle}_{today}.summary.json"
        write_summary(summary_path, summary)
        if result["jsonl_path"]:
            summary["output_file"] = result["jsonl_path"]
        summary["summary_file"] = str(summary_path)

    _render_status_banner(result, prefix="Fetched")
    if mode == "video":
        _render_transcript_view(result["merged"])
    _render_download_section(
        merged=result["merged"], summary=summary,
        download_handle=handle, today=today,
    )


def run_scrape_playlists(
    *,
    channel_handle: str,
    playlists: list[PlaylistMeta],
    languages_raw: str,
    delay_seconds: float,
    max_workers: int | None = None,
    save_to_disk: bool,
    start_fresh: bool,
) -> None:
    """Sequentially scrape multiple playlists, then render a merged download."""
    languages = parse_languages(languages_raw)
    if not languages:
        st.error("Specify at least one language code.")
        return
    if not playlists:
        st.warning("No playlists selected.")
        return

    output_dir = (_REPO_ROOT / "output").resolve()
    today = datetime.now().strftime("%Y%m%d")

    st.markdown(f"### Scraping {len(playlists)} playlist(s)")
    overall = st.progress(0.0, text=f"Starting {len(playlists)} playlist(s)…")

    per_playlist: list[dict[str, Any]] = []
    aggregated: list[Record] = []
    overall_started = time.time()

    for idx, pl in enumerate(playlists, start=1):
        overall.progress(
            (idx - 1) / len(playlists),
            text=f"Playlist {idx}/{len(playlists)} — {pl['title'][:60]}",
        )
        with st.container(border=True):
            result = _scrape_one_source(
                mode="playlist", target=pl["url"], handle=pl["id"],
                languages=languages, delay_seconds=delay_seconds, max_workers=max_workers,
                save_to_disk=save_to_disk, start_fresh=start_fresh,
                output_dir=output_dir, today=today,
                section_label=f"[{idx}/{len(playlists)}] {pl['title']}",
            )
        per_playlist.append({"playlist": pl, "result": result})
        aggregated.extend(result["merged"])

    overall.progress(1.0, text=f"Completed {len(playlists)} playlist(s)")

    final_merged = _dedupe_by_id(aggregated)
    overall_elapsed = time.time() - overall_started

    combined_summary = _build_combined_summary(
        channel_handle=channel_handle,
        playlists=playlists,
        per_playlist=per_playlist,
        merged=final_merged,
        overall_elapsed=overall_elapsed,
        start_fresh=start_fresh,
    )
    combined_handle = f"{channel_handle}_{len(playlists)}playlists"
    if save_to_disk:
        combined_path = output_dir / f"{combined_handle}_{today}.summary.json"
        write_summary(combined_path, combined_summary)
        combined_summary["summary_file"] = str(combined_path)

    # Aggregate banner — uses ``this_run`` totals across all picked playlists.
    total_fetched = sum(p["result"]["this_run"]["fetched"] for p in per_playlist)
    total_successful = sum(p["result"]["this_run"]["successful"] for p in per_playlist)
    total_failed = sum(p["result"]["this_run"]["failed"] for p in per_playlist)
    if total_fetched == 0:
        st.success(
            f"✅ Nothing new to fetch across {len(playlists)} playlist(s) — "
            f"every video was already saved from a previous run. "
            f"You can download the combined file below ({len(final_merged)} videos)."
        )
    elif total_failed == 0:
        st.success(
            f"✅ Got {total_successful} of {total_fetched} new transcripts across "
            f"{len(playlists)} playlist(s) in {overall_elapsed:.1f} seconds."
        )
    elif total_successful == 0:
        st.error(
            f"😕 Couldn't get any new transcripts ({total_failed} failed) across "
            f"{len(playlists)} playlist(s) in {overall_elapsed:.1f} seconds. "
            "YouTube may be rate-limiting — wait a minute and try again."
        )
    else:
        st.warning(
            f"⚠️ Got {total_successful} of {total_fetched} new transcripts, "
            f"{total_failed} couldn't be fetched, across {len(playlists)} "
            f"playlist(s) in {overall_elapsed:.1f} seconds."
        )

    _render_download_section(
        merged=final_merged, summary=combined_summary,
        download_handle=combined_handle, today=today,
    )

    with st.expander("Per-playlist breakdown", expanded=False):
        for entry in per_playlist:
            pl = entry["playlist"]
            r = entry["result"]
            st.markdown(f"**{pl['title']}** — `{pl['id']}`")
            st.json({
                "source_total_videos": r["source_total_videos"],
                "this_run": r["this_run"],
                "resume": r["resume"],
                "merged_videos": len(r["merged"]),
                "jsonl_path": r["jsonl_path"],
            })


# ─── Banners + downloads ────────────────────────────────────────────────────


def _render_status_banner(result: dict[str, Any], *, prefix: str) -> None:
    """Render the success/warning/error banner for a single-source run."""
    fetched = result["this_run"]["fetched"]
    successful = result["this_run"]["successful"]
    failed = result["this_run"]["failed"]
    elapsed = result["this_run"]["elapsed_seconds"]
    if fetched == 0:
        return  # _scrape_one_source already showed the "nothing to fetch" success
    if failed == 0:
        st.success(f"✅ Got {successful} of {fetched} transcripts in {elapsed:.1f} seconds.")
    elif successful == 0:
        st.error(
            f"😕 Couldn't get any transcripts this time ({failed} failed in {elapsed:.1f}s). "
            f"YouTube may be rate-limiting — wait a minute and try again."
        )
    else:
        st.warning(
            f"⚠️ Got {successful} of {fetched} transcripts in {elapsed:.1f}s — "
            f"{failed} couldn't be fetched (private, no captions, or rate-limited)."
        )


def _render_download_section(
    *,
    merged: list[Record],
    summary: dict[str, Any],
    download_handle: str,
    today: str,
) -> None:
    """Render the transcript-archive download buttons + summary expander."""
    cols = st.columns(2)
    with cols[0]:
        st.download_button(
            f"💾  Download all transcripts ({len(merged)} videos)",
            data=records_to_jsonl_bytes(merged),
            file_name=f"{download_handle}_{today}.jsonl",
            mime="application/jsonl",
            use_container_width=True,
            disabled=not merged,
            help=(
                "One file containing every video and its transcript. The format is "
                "JSONL — one row per video — which opens cleanly in Excel, "
                "Google Sheets, or any spreadsheet tool."
            ),
        )
    with cols[1]:
        st.download_button(
            "📄  Download run report",
            data=(json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            file_name=f"{download_handle}_{today}.summary.json",
            mime="application/json",
            use_container_width=True,
            help="A short technical summary of what was scraped — handy for re-runs.",
        )

    with st.expander(f"📋  See all {len(merged)} videos", expanded=False):
        st.caption("Click a row to expand. Empty `transcript` means YouTube had no captions for that video.")
        st.dataframe(merged, hide_index=True, use_container_width=True)
    with st.expander("🔧  Technical details", expanded=False):
        st.json(summary)


# ─── Summary builders ───────────────────────────────────────────────────────


def _build_single_summary(
    *,
    mode: str,
    target: str,
    handle: str,
    playlist_title: str | None,
    channel_handle: str | None,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Summary for a single channel / playlist / video run."""
    summary: dict[str, Any] = {
        "mode": mode,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "handle": handle,
        "source_total_videos": result["source_total_videos"],
        "this_run": result["this_run"],
        "resume": result["resume"],
        "merged_total_videos": len(result["merged"]),
    }
    if mode == "channel":
        summary["channel"] = target
    elif mode == "playlist":
        summary["playlist_url"] = target
        if playlist_title:
            summary["playlist_title"] = playlist_title
        if channel_handle:
            summary["parent_channel_handle"] = channel_handle
    else:
        summary["video_url"] = f"https://www.youtube.com/watch?v={target}"
    return summary


def _build_combined_summary(
    *,
    channel_handle: str,
    playlists: list[PlaylistMeta],
    per_playlist: list[dict[str, Any]],
    merged: list[Record],
    overall_elapsed: float,
    start_fresh: bool,
) -> dict[str, Any]:
    """Summary for a multi-playlist run (one combined entry, plus per-playlist breakdown)."""
    aggregate_failures: dict[str, int] = {s: 0 for s in FAILURE_STATUSES}
    fetched = successful = failed = 0
    skipped = retried = new = 0
    for entry in per_playlist:
        run = entry["result"]["this_run"]
        res = entry["result"]["resume"]
        fetched += run["fetched"]
        successful += run["successful"]
        failed += run["failed"]
        for k, v in run["failures_by_status"].items():
            aggregate_failures[k] = aggregate_failures.get(k, 0) + int(v)
        skipped += res["skipped_already_done"]
        retried += res["retried_network_errors"]
        new += res["new_videos"]

    return {
        "mode": "playlists",
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "parent_channel_handle": channel_handle,
        "playlist_count": len(playlists),
        "playlists": [
            {"id": pl["id"], "title": pl["title"], "url": pl["url"]}
            for pl in playlists
        ],
        "aggregate_this_run": {
            "fetched": fetched,
            "successful": successful,
            "failed": failed,
            "failures_by_status": aggregate_failures,
            "elapsed_seconds": round(overall_elapsed, 2),
        },
        "aggregate_resume": {
            "start_fresh": start_fresh,
            "skipped_already_done": skipped,
            "retried_network_errors": retried,
            "new_videos": new,
        },
        "merged_total_videos": len(merged),
        "per_playlist": [
            {
                "id": entry["playlist"]["id"],
                "title": entry["playlist"]["title"],
                "url": entry["playlist"]["url"],
                "source_total_videos": entry["result"]["source_total_videos"],
                "this_run": entry["result"]["this_run"],
                "resume": entry["result"]["resume"],
                "merged_videos": len(entry["result"]["merged"]),
                "jsonl_path": entry["result"]["jsonl_path"],
            }
            for entry in per_playlist
        ],
    }


# ─── UI presentation helpers (Glasp-inspired hero + cards) ──────────────────


_HERO_TAGLINE = "Get a transcript or save a video — straight from any YouTube link."
_HERO_SUBTAGLINE = (
    "Paste a YouTube link below. We'll either pull the captions out for you, "
    "or save the video file to your computer — your choice."
)
_THUMB_URL_TEMPLATE = "https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def _inject_css() -> None:
    """Inject the Glasp-inspired stylesheet once per render.

    Streamlit re-executes the module top-to-bottom on every interaction, so
    re-injecting on each pass is harmless — duplicate ``<style>`` blocks
    collapse in CSSOM and the cost is negligible.
    """
    st.markdown(
        """
        <style>
          :root {
            --yt-red: #FF3B30;
            --yt-red-hover: #E12B22;
            --ink: #0A0A0A;
            --ink-soft: #525252;
            --line: #E7E5E4;
            --bg: #FAFAFA;
          }
          html, body, [class*="css"] {
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text",
                         "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif;
          }
          .block-container { padding-top: 2.4rem !important; padding-bottom: 4rem !important; max-width: 760px !important; }
          .yt-hero        { text-align: center; margin: 0.4rem 0 1.6rem 0; }
          .yt-hero h1     { font-size: 2.6rem; font-weight: 800; letter-spacing: -0.02em; line-height: 1.1; color: var(--ink); margin: 0 0 0.8rem 0; }
          .yt-hero p      { font-size: 1.05rem; color: var(--ink-soft); max-width: 560px; margin: 0 auto; line-height: 1.55; }
          .yt-hero .yt-accent { color: var(--yt-red); }
          .yt-chip        { display: inline-flex; align-items: center; gap: 0.45rem; padding: 0.35rem 0.85rem; background: #FFF; border: 1px solid var(--line); border-radius: 999px; font-size: 0.82rem; color: var(--ink-soft); margin: 0.35rem 0 1.0rem 0; }
          .yt-chip .yt-dot{ width: 8px; height: 8px; border-radius: 50%; background: #22C55E; }
          .yt-card        { background: #FFF; border: 1px solid var(--line); border-radius: 16px; padding: 1rem; box-shadow: 0 1px 2px rgba(0,0,0,0.03); margin-bottom: 1rem; }
          .yt-card.flat   { padding: 0.6rem 0.9rem; }
          .yt-meta        { color: var(--ink-soft); font-size: 0.85rem; }
          .yt-meta b      { color: var(--ink); font-weight: 600; }
          .yt-empty       { text-align: center; padding: 2.4rem 1rem; color: var(--ink-soft); border: 1px dashed var(--line); border-radius: 16px; background: #FFF; }
          .yt-empty .yt-empty-emoji { font-size: 1.8rem; display: block; margin-bottom: 0.6rem; }
          .yt-transcript  { background: #FFFFFF; border: 1px solid var(--line); border-radius: 16px; padding: 1.4rem 1.6rem; line-height: 1.75; font-size: 1.02rem; color: var(--ink); white-space: pre-wrap; word-wrap: break-word; max-height: 520px; overflow-y: auto; }
          /* Make the URL input feel like a hero search box */
          [data-testid="stTextInput"] input { border-radius: 14px !important; padding: 0.95rem 1.1rem !important; font-size: 1.04rem !important; border: 1px solid var(--line) !important; background: #FFF !important; box-shadow: 0 1px 2px rgba(0,0,0,0.03); }
          [data-testid="stTextInput"] input:focus { border-color: var(--yt-red) !important; box-shadow: 0 0 0 4px rgba(255,59,48,0.10) !important; }
          /* Primary button — fully red pill */
          .stButton > button[kind="primary"] { background: var(--yt-red) !important; border: 1px solid var(--yt-red) !important; border-radius: 12px !important; padding: 0.7rem 1rem !important; font-weight: 600 !important; }
          .stButton > button[kind="primary"]:hover { background: var(--yt-red-hover) !important; border-color: var(--yt-red-hover) !important; }
          /* Tabs — squared off, underline-only style */
          [data-baseweb="tab-list"] { gap: 1.5rem !important; border-bottom: 1px solid var(--line) !important; }
          [data-baseweb="tab"] { padding: 0.5rem 0 !important; font-weight: 600 !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_hero() -> None:
    """Render the centered hero headline + subhead."""
    st.markdown(
        f"""
        <div class="yt-hero">
          <h1>YouTube <span class="yt-accent">Transcript</span> Scraper</h1>
          <p>{_HERO_TAGLINE}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_empty_state(
    *,
    tagline: str | None = None,
    examples: list[tuple[str, str, str]] | None = None,
) -> None:
    """Friendly placeholder shown when the URL box is still empty.

    Args:
        tagline: The main one-line message inside the dashed card. Defaults
            to the page-wide subhead so older callers continue to work.
        examples: Optional list of ``(emoji, label, value)`` triples shown
            as a compact "examples" section underneath the tagline. Helpful
            for non-technical users who aren't sure what to paste.
    """
    body = tagline or _HERO_SUBTAGLINE
    examples_html = ""
    if examples:
        rows = "".join(
            f"<tr>"
            f"<td style='padding:0.18rem 0.6rem 0.18rem 0;font-size:1.05rem;'>{emoji}</td>"
            f"<td style='padding:0.18rem 0.5rem;color:var(--ink);font-weight:600;text-align:left;white-space:nowrap;'>{label}</td>"
            f"<td style='padding:0.18rem 0;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:0.78rem;color:var(--ink-soft);text-align:left;'>{value}</td>"
            f"</tr>"
            for emoji, label, value in examples
        )
        examples_html = (
            "<div style='margin-top:0.9rem;display:flex;justify-content:center;'>"
            f"<table style='border-collapse:collapse;'>{rows}</table>"
            "</div>"
        )
    st.markdown(
        f"""
        <div class="yt-empty">
          <span class="yt-empty-emoji">📋</span>
          <div>{body}</div>
          {examples_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_detection_chip(mode: str, target: str) -> None:
    """Small pill confirming what kind of URL was detected."""
    label = {
        "channel": "Channel detected",
        "playlist": "Playlist detected",
        "video": "Single video detected",
    }.get(mode, "URL detected")
    pretty_target = target if mode != "video" else f"https://www.youtube.com/watch?v={target}"
    st.markdown(
        f"""
        <div class="yt-chip">
          <span class="yt-dot"></span>
          <span><b style="color:var(--ink);">{label}</b> &middot; <span style="font-family:ui-monospace,Menlo,Consolas,monospace;font-size:0.8rem;">{pretty_target}</span></span>
        </div>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data(ttl=3600, show_spinner=False)
def _get_video_meta_safe(video_id: str) -> VideoMeta | None:
    """Cached, exception-safe wrapper around :func:`fetch_video_meta`.

    Returns ``None`` instead of raising — the preview card degrades gracefully
    when yt-dlp can't reach the video (private, removed, region-blocked, etc.).
    """
    try:
        return fetch_video_meta(video_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("preview_meta: failed id=%s err=%s", video_id, exc)
        return None


def _render_video_card(video_id: str, meta: VideoMeta | None) -> None:
    """Glasp-style thumbnail + title preview card for a single video."""
    thumb_url = _THUMB_URL_TEMPLATE.format(video_id=video_id)
    cols = st.columns([1, 2], gap="medium")
    with cols[0]:
        st.image(thumb_url, use_container_width=True)
    with cols[1]:
        if meta is None:
            st.markdown(
                f"""
                <div class="yt-meta">
                  <b>Couldn't preview this video</b><br>
                  We'll still try to fetch its transcript when you click below.<br>
                  <span style="font-family:ui-monospace,Menlo,Consolas,monospace;font-size:0.8rem;">{video_id}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )
            return
        title_html = (meta["title"] or video_id).replace("<", "&lt;").replace(">", "&gt;")
        duration = _format_duration(meta.get("duration_seconds") or 0)
        upload = meta.get("upload_date") or ""
        meta_bits: list[str] = []
        if duration:
            meta_bits.append(f"⏱ {duration}")
        if upload:
            meta_bits.append(f"📅 {upload}")
        meta_bits.append(
            f"<a href='https://www.youtube.com/watch?v={video_id}' target='_blank' "
            "style='color:var(--ink-soft);text-decoration:none;'>↗ Open on YouTube</a>"
        )
        st.markdown(
            f"""
            <div style="padding-top:0.2rem;">
              <div style="font-size:1.15rem;font-weight:700;line-height:1.35;color:var(--ink);margin-bottom:0.5rem;">{title_html}</div>
              <div class="yt-meta">{" &nbsp;·&nbsp; ".join(meta_bits)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_transcript_view(records: list[Record]) -> None:
    """Glasp-style transcript panel for a successful single-video scrape.

    ``records`` is the merged result list — for video mode it always has
    exactly one entry, but we tolerate empty/multi-record inputs so this stays
    safe across modes.
    """
    if not records:
        return
    record = records[0]
    status = record.get("status")
    transcript = str(record.get("transcript") or "")
    language = record.get("language") or ""

    if status != "OK" or not transcript:
        _render_transcript_empty_state(status)
        return

    st.markdown("##### Transcript")
    chars = len(transcript)
    words = len(transcript.split())
    info_bits = [f"<b>{words:,}</b> words", f"<b>{chars:,}</b> characters"]
    if language and language != "none":
        info_bits.append(f"language <b>{language}</b>")
    st.markdown(
        f"<div class='yt-meta' style='margin-bottom:0.6rem;'>{' &nbsp;·&nbsp; '.join(info_bits)}</div>",
        unsafe_allow_html=True,
    )

    paragraphs = _chunk_into_paragraphs(transcript)
    safe_paragraphs = [p.replace("<", "&lt;").replace(">", "&gt;") for p in paragraphs]
    body = "<br><br>".join(safe_paragraphs)
    st.markdown(
        f"<div class='yt-transcript'>{body}</div>",
        unsafe_allow_html=True,
    )

    cols = st.columns(2)
    with cols[0]:
        st.download_button(
            "Download transcript (.txt)",
            data=transcript.encode("utf-8"),
            file_name=f"{record.get('id', 'transcript')}.txt",
            mime="text/plain",
            use_container_width=True,
        )
    with cols[1]:
        with st.popover("Copy raw text", use_container_width=True):
            # st.code provides the built-in copy-to-clipboard button.
            st.code(transcript, language="text")


def _render_transcript_empty_state(status: str | None) -> None:
    """Render a friendly empty state when a transcript could not be fetched."""
    explanation = {
        "NO_CAPTIONS": "This video has no caption tracks at all.",
        "DISABLED": "The uploader has disabled captions for this video.",
        "NETWORK_ERROR": "YouTube was unreachable or rate-limiting us. Try again in a minute.",
        "OTHER": "Something unexpected happened during the fetch — see the run summary below.",
    }.get(status or "", "We couldn't retrieve a transcript for this video.")
    st.markdown(
        f"""
        <div class="yt-empty" style="margin-top:0.5rem;">
          <span class="yt-empty-emoji">🤷</span>
          <div><b>No transcript available</b></div>
          <div style="margin-top:0.4rem;font-size:0.92rem;">{explanation}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _format_duration(seconds: int) -> str:
    """Format a duration in seconds as ``H:MM:SS`` or ``M:SS``."""
    if seconds <= 0:
        return ""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _chunk_into_paragraphs(text: str, target_words_per_paragraph: int = 70) -> list[str]:
    """Split a single-line transcript into readable paragraphs.

    The transcript cleaner concatenates every caption snippet into one line,
    which is hostile to skim-reading. We chunk on whitespace so Latin and
    Thai both render nicely (Thai has no inter-word spaces, but yt-dlp's
    timed-text snippets are already separated by spaces in our cleaner).
    """
    words = text.split(" ")
    if len(words) <= target_words_per_paragraph * 1.5:
        return [text]
    paragraphs: list[str] = []
    buf: list[str] = []
    count = 0
    for word in words:
        buf.append(word)
        count += 1
        if count >= target_words_per_paragraph:
            paragraphs.append(" ".join(buf))
            buf = []
            count = 0
    if buf:
        paragraphs.append(" ".join(buf))
    return paragraphs


# Streamlit executes this module top-to-bottom on every interaction.
render_page()
