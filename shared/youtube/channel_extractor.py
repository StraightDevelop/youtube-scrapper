"""Channel + playlist listing via ``yt-dlp`` flat extraction (FR-2)."""
from __future__ import annotations

import logging
import time
from typing import Iterator, TypedDict

from yt_dlp import YoutubeDL

from shared.youtube.url_validator import channel_playlists_tab_url

logger = logging.getLogger(__name__)

# Per FR-2 — use flat extraction so listing thousands of videos stays fast.
_YDL_OPTS: dict = {
    "extract_flat": True,
    "skip_download": True,
    "quiet": True,
    "no_warnings": True,
    "ignoreerrors": True,
}


class VideoMeta(TypedDict):
    """Lightweight metadata captured from the channel listing (FR-2)."""

    id: str
    title: str
    url: str
    upload_date: str  # YYYY-MM-DD or empty when unavailable in flat extraction
    duration_seconds: int


class PlaylistMeta(TypedDict):
    """Lightweight metadata for a single playlist on a channel."""

    id: str            # YouTube playlist ID, e.g. "PLxxxxxxxxxxxx"
    title: str
    url: str           # canonical /playlist?list=… URL
    video_count: int   # 0 when yt-dlp doesn't surface the count


def list_channel_videos(channel_url: str) -> list[VideoMeta]:
    """Return metadata for every public video on the given channel URL.

    Args:
        channel_url: Canonical channel URL (output of
            :func:`shared.youtube.url_validator.validate_channel_url`).
    Returns:
        A list of :class:`VideoMeta` dicts in the order yt-dlp emits them
        (typically newest first for ``/videos`` tabs).
    Raises:
        Exception: any error raised by ``yt-dlp`` during extraction is allowed
            to propagate — there is no graceful recovery from a failed channel
            listing, so the CLI should surface it to the user.
    """
    started = time.time()
    logger.info("list_channel_videos: start url=%s", channel_url)
    with YoutubeDL(_YDL_OPTS) as ydl:
        info = ydl.extract_info(channel_url, download=False)
    videos: list[VideoMeta] = []
    seen: set[str] = set()
    for entry in _walk_entries(info):
        meta = _to_video_meta(entry)
        if not meta["id"] or meta["id"] in seen:
            continue
        seen.add(meta["id"])
        videos.append(meta)
    logger.info(
        "list_channel_videos: done count=%d elapsed=%.2fs",
        len(videos),
        time.time() - started,
    )
    return videos


def list_channel_playlists(channel_url: str) -> list[PlaylistMeta]:
    """Enumerate the playlists tab of a channel via yt-dlp flat extraction.

    Note that ``list_channel_videos`` already accepts a playlist URL — this
    function exists specifically for the *picker* UX where the user pastes a
    channel URL and wants to choose one of its playlists.

    Args:
        channel_url: Canonical channel URL (output of
            :func:`shared.youtube.url_validator.validate_channel_url`).
    Returns:
        Deduplicated list of :class:`PlaylistMeta`. Returns an empty list if
        the channel has no public playlists.
    Raises:
        Exception: any error raised by ``yt-dlp`` propagates so the UI can
            display it directly.
    """
    started = time.time()
    tab_url = channel_playlists_tab_url(channel_url)
    logger.info("list_channel_playlists: start url=%s", tab_url)
    with YoutubeDL(_YDL_OPTS) as ydl:
        info = ydl.extract_info(tab_url, download=False)
    playlists: list[PlaylistMeta] = []
    seen: set[str] = set()
    for entry in _walk_entries(info):
        meta = _to_playlist_meta(entry)
        if not meta or not meta["id"] or meta["id"] in seen:
            continue
        seen.add(meta["id"])
        playlists.append(meta)
    logger.info(
        "list_channel_playlists: done count=%d elapsed=%.2fs",
        len(playlists),
        time.time() - started,
    )
    return playlists


def _to_playlist_meta(entry: dict) -> PlaylistMeta | None:
    """Convert a yt-dlp tab entry into :class:`PlaylistMeta`, or ``None`` to skip.

    Channel tab extraction returns a mix of entry types; we only want the
    ones that are themselves playlists (i.e. their ``id`` looks like a
    playlist ID, not a video ID).
    """
    raw_id = entry.get("id") or ""
    if not isinstance(raw_id, str) or len(raw_id) < 12:
        # Video IDs are 11 chars — anything shorter than 12 isn't a playlist.
        return None
    title = str(entry.get("title") or "").strip() or raw_id
    url = entry.get("url") or entry.get("webpage_url") or ""
    if not isinstance(url, str) or not url.startswith("http"):
        url = f"https://www.youtube.com/playlist?list={raw_id}"
    count = (
        entry.get("playlist_count")
        or entry.get("n_entries")
        or entry.get("video_count")
        or 0
    )
    try:
        video_count = int(count) if count else 0
    except (TypeError, ValueError):
        video_count = 0
    return PlaylistMeta(id=raw_id, title=title, url=url, video_count=video_count)


def fetch_video_meta(video_id: str) -> VideoMeta:
    """Fetch metadata for a single video without downloading captions.

    Used by the web UI's single-video mode. Unlike :func:`list_channel_videos`
    this performs a full (non-flat) extraction so ``upload_date`` and
    ``duration`` are reliably populated.

    Args:
        video_id: 11-character YouTube video ID.
    Returns:
        A populated :class:`VideoMeta` dict.
    Raises:
        Exception: any error raised by ``yt-dlp`` propagates so the UI can
            display it directly to the user.
    """
    started = time.time()
    logger.info("fetch_video_meta: start id=%s", video_id)
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    url = f"https://www.youtube.com/watch?v={video_id}"
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False) or {}
    if not info.get("id"):
        info["id"] = video_id
    if not info.get("webpage_url") and not info.get("url"):
        info["url"] = url
    meta = _to_video_meta(info)
    logger.info(
        "fetch_video_meta: done id=%s elapsed=%.2fs",
        video_id, time.time() - started,
    )
    return meta


def _walk_entries(info: dict | None) -> Iterator[dict]:
    """Yield video entries from a yt-dlp info dict, descending into nested tabs."""
    if not info:
        return
    entries = info.get("entries")
    if entries is None:
        # Single-video info dict (rare for channel URLs but handled for safety).
        if info.get("id"):
            yield info
        return
    for entry in entries:
        if not entry:
            continue
        if entry.get("entries"):
            yield from _walk_entries(entry)
        else:
            yield entry


def _to_video_meta(entry: dict) -> VideoMeta:
    """Convert a single yt-dlp entry into a :class:`VideoMeta` dict."""
    video_id = str(entry.get("id") or "")
    title = str(entry.get("title") or "").strip() or video_id
    url = entry.get("url") or entry.get("webpage_url") or ""
    if not isinstance(url, str) or not url.startswith("http"):
        # Flat extraction sometimes returns just the id; rebuild a watch URL.
        url = f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
    upload_date = _format_upload_date(entry.get("upload_date"))
    duration_raw = entry.get("duration") or 0
    try:
        duration_seconds = int(duration_raw) if duration_raw else 0
    except (TypeError, ValueError):
        duration_seconds = 0
    return VideoMeta(
        id=video_id,
        title=title,
        url=url,
        upload_date=upload_date,
        duration_seconds=duration_seconds,
    )


def _format_upload_date(raw: object) -> str:
    """Convert a yt-dlp ``YYYYMMDD`` upload date to ``YYYY-MM-DD``.

    Returns an empty string when the value is missing or not parseable —
    flat extraction does not always populate this field.
    """
    if raw is None:
        return ""
    s = str(raw)
    if len(s) != 8 or not s.isdigit():
        return ""
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
