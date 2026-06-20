"""Download a single YouTube video file to disk via yt-dlp.

Used by ``apps/youtube_scraper_web``'s "Download video" tab. The function
preserves the ``apps/`` ↔ ``shared/`` split mandated by CLAUDE.md: it knows
nothing about Streamlit, takes a plain progress callback, and returns a
typed result dict the UI can render.

Format selectors are intentionally biased toward **single-file** outputs so
the download works without ffmpeg on the user's machine. yt-dlp will only
fall back to a video+audio merge (which needs ffmpeg) when no acceptable
single-file format is available, and the merge fallback is the last
alternative in every selector below — never the first.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable, Literal, TypedDict

from yt_dlp import YoutubeDL  # type: ignore[import-not-found]
from yt_dlp.utils import DownloadError  # type: ignore[import-not-found]

logger = logging.getLogger(__name__)

Quality = Literal["best", "small"]

# Format selectors. Each ladder ends in a merge fallback that needs ffmpeg;
# the earlier alternatives are pure single-file so the common case works
# without any system dependency beyond yt-dlp itself.
_FORMAT_SELECTORS: dict[Quality, str] = {
    # Best single-file mp4 (typically 720p on modern YouTube), then any best
    # single-file, then video+audio merge as a last resort.
    "best": "best[ext=mp4]/best/bv*+ba",
    # Capped at 480p — much smaller, much faster, still watchable.
    "small": "best[ext=mp4][height<=480]/best[height<=480]/wv*+wa",
}

ProgressCallback = Callable[[float, str], None]


class DownloadResult(TypedDict):
    """Result of a successful download."""

    path: str           # Absolute path of the saved file on disk.
    title: str          # YouTube-reported video title.
    duration_seconds: int
    bytes: int          # Final file size on disk.
    ext: str            # File extension without the leading dot (``mp4``, ``webm``, …).


def download_video(
    video_id: str,
    output_dir: Path,
    quality: Quality = "best",
    progress_callback: ProgressCallback | None = None,
) -> DownloadResult:
    """Download a single YouTube video to ``output_dir`` and return its path.

    Args:
        video_id: 11-character YouTube video ID.
        output_dir: Directory to write the file into. Created if missing.
        quality: ``"best"`` for the highest available single-file mp4 (or
            merge fallback), ``"small"`` for a 480p-capped variant.
        progress_callback: Optional ``(fraction, label)`` callback invoked
            multiple times per second while the file is downloading. The
            ``fraction`` argument is in ``[0.0, 1.0)``; the UI is responsible
            for switching to ``1.0`` once :func:`download_video` returns.
    Returns:
        A populated :class:`DownloadResult`.
    Raises:
        DownloadError: yt-dlp could not download the requested video (private,
            removed, region-locked, age-gated, members-only, etc.). Callers
            should catch this and present a friendly message; the original
            exception's ``str(exc)`` already contains the user-facing reason
            yt-dlp reports.
        FileNotFoundError: yt-dlp reported success but the resolved path does
            not exist on disk (should be impossible in practice).
    """
    started = time.time()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(output_dir / "%(title).180B [%(id)s].%(ext)s")

    # yt-dlp's ``progress_hooks`` callback runs in the same thread as the
    # caller, so a closure that mutates this dict is safe.
    final_path: dict[str, str | None] = {"value": None}

    def _hook(d: dict) -> None:
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            if progress_callback:
                if total > 0:
                    fraction = min(downloaded / total, 0.99)
                    label = (
                        f"Downloading… {_human_bytes(downloaded)} of "
                        f"{_human_bytes(total)}"
                    )
                else:
                    fraction = 0.0
                    label = f"Downloading… {_human_bytes(downloaded)}"
                progress_callback(fraction, label)
        elif status == "finished":
            info = d.get("info_dict") or {}
            final_path["value"] = (
                info.get("filepath") or d.get("filename") or info.get("_filename")
            )
            if progress_callback:
                progress_callback(0.99, "Saving file…")

    opts = {
        "outtmpl": output_template,
        "format": _FORMAT_SELECTORS[quality],
        "noprogress": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [_hook],
        # Skip the noisy console output that yt-dlp emits during cleanup.
        "consoletitle": False,
        # Don't let one bad subtitle / thumbnail abort the whole download.
        "ignore_no_formats_error": False,
    }

    url = f"https://www.youtube.com/watch?v={video_id}"
    logger.info("download_video: start id=%s quality=%s", video_id, quality)
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True) or {}

    saved_str = final_path["value"] or ydl.prepare_filename(info)
    saved = Path(saved_str)
    if not saved.exists():
        raise FileNotFoundError(
            f"yt-dlp reported success but file is missing: {saved}"
        )

    size = saved.stat().st_size
    elapsed = time.time() - started
    logger.info(
        "download_video: done id=%s path=%s size=%s elapsed=%.2fs",
        video_id, saved, _human_bytes(size), elapsed,
    )
    return DownloadResult(
        path=str(saved.resolve()),
        title=str(info.get("title") or video_id),
        duration_seconds=int(info.get("duration") or 0),
        bytes=size,
        ext=saved.suffix.lstrip(".") or "mp4",
    )


def friendly_download_error(exc: BaseException) -> str:
    """Translate a yt-dlp ``DownloadError`` into a non-technical message.

    The library's ``str(exc)`` is already user-readable but tends to start
    with a noisy ``ERROR: …`` prefix. This trims the prefix and, when it
    recognises a common cause, replaces the message with a friendlier
    alternative the UI can show as-is.
    """
    raw = str(exc).strip()
    if raw.upper().startswith("ERROR:"):
        raw = raw.split(":", 1)[1].strip()
    lowered = raw.lower()
    if "private video" in lowered:
        return "This video is private and can't be downloaded."
    if "members-only" in lowered or "members only" in lowered:
        return "This video is for channel members only and can't be downloaded."
    if "age" in lowered and "restrict" in lowered:
        return "This video is age-restricted. YouTube blocks downloading it from this app."
    if "region" in lowered or "country" in lowered:
        return "This video is blocked in your region."
    if "removed" in lowered or "unavailable" in lowered:
        return "This video has been removed or is no longer available."
    if "live event" in lowered or "is live" in lowered:
        return "This is a live stream — try again after the broadcast ends."
    if "format is not available" in lowered or "no video formats" in lowered:
        return "YouTube doesn't offer a downloadable file for this video."
    return raw or "Download failed for an unknown reason."


def _human_bytes(n: int | float) -> str:
    """Format a byte count as a short human-readable string (KB / MB / GB)."""
    if n < 1024:
        return f"{int(n)} B"
    units = ("KB", "MB", "GB", "TB")
    size = float(n) / 1024
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


__all__ = [
    "Quality",
    "DownloadResult",
    "download_video",
    "friendly_download_error",
    "DownloadError",
]
