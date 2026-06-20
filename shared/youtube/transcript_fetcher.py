"""Transcript retrieval via yt-dlp (FR-3/FR-4).

Why yt-dlp instead of youtube-transcript-api:
    YouTube began returning HTTP 429 on the bare ``/api/timedtext`` endpoint
    used by ``youtube-transcript-api`` 0.6.x, breaking transcript downloads
    even when ``list_transcripts`` succeeds. yt-dlp pulls captions through the
    same InnerTube player flow as the YouTube web client and produces signed
    timedtext URLs that pass YouTube's anti-bot gating. Since yt-dlp is
    already pinned for channel/playlist listing, this also removes a
    duplicated extraction stack.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from typing import Any, Literal

from yt_dlp import YoutubeDL  # type: ignore[import-not-found]
from yt_dlp.utils import DownloadError, ExtractorError  # type: ignore[import-not-found]

from shared.utils.text_cleaner import clean_transcript_snippets

logger = logging.getLogger(__name__)

Status = Literal["OK", "NO_CAPTIONS", "DISABLED", "NETWORK_ERROR", "OTHER"]

_NETWORK_HINTS = (
    "network",
    "connection",
    "timed out",
    "timeout",
    "temporary failure",
    "429",
    "too many requests",
    "http error 5",
)
_DISABLED_HINTS = ("subtitles are disabled", "captions are disabled")
_UNAVAILABLE_HINTS = (
    "video unavailable",
    "private video",
    "this video has been removed",
    "members-only",
    "members only",
    "is not available",
)
_PREFERRED_FORMATS = ("json3", "srv3", "srv2", "srv1", "vtt", "ttml", "srt")
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_DOWNLOAD_TIMEOUT_S = 20


def fetch_transcript(
    video_id: str,
    preferred_languages: list[str],
) -> tuple[Status, str, str]:
    """Fetch a transcript for ``video_id`` with priority-language fallback.

    Args:
        video_id: YouTube video ID (the 11-character string).
        preferred_languages: Language codes in priority order — e.g.
            ``["th", "en"]``. The first manually-created caption track
            matching this list is preferred; otherwise the first
            auto-generated one matching this list; otherwise any
            auto-generated track (labeled ``"auto"``); otherwise any
            available track (also labeled ``"auto"``).

    Returns:
        Tuple of ``(status, language_label, transcript_text)``.

        * ``status`` is one of ``OK``, ``NO_CAPTIONS``, ``DISABLED``,
          ``NETWORK_ERROR``, ``OTHER``.
        * ``language_label`` is the matched preferred-language code, or
          ``"auto"`` when a fallback (non-preferred) track was used, or
          ``"none"`` when nothing could be retrieved.
        * ``transcript_text`` is the cleaned single-line transcript, or
          empty string when ``status != "OK"``.
    """
    started = time.time()
    logger.debug("fetch_transcript: start id=%s langs=%s", video_id, preferred_languages)

    try:
        info = _extract_info(video_id)
    except DownloadError as exc:
        return _classify_extract_error(video_id, exc)
    except ExtractorError as exc:
        return _classify_extract_error(video_id, exc)
    except Exception as exc:  # noqa: BLE001
        return _classify_generic_error(video_id, exc)

    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    if not manual and not auto:
        logger.warning("fetch_transcript: no_captions id=%s", video_id)
        return "NO_CAPTIONS", "none", ""

    pick = _pick_track(manual, auto, preferred_languages)
    if pick is None:
        logger.warning("fetch_transcript: no_usable_track id=%s", video_id)
        return "NO_CAPTIONS", "none", ""
    label, track_url, ext = pick

    try:
        body = _download(track_url)
    except urllib.error.HTTPError as exc:
        if exc.code == 429 or 500 <= exc.code < 600:
            logger.warning("fetch_transcript: network id=%s http=%s", video_id, exc.code)
            return "NETWORK_ERROR", "none", ""
        logger.warning("fetch_transcript: other id=%s http=%s", video_id, exc.code)
        return "OTHER", "none", ""
    except urllib.error.URLError as exc:
        logger.warning("fetch_transcript: network id=%s err=%s", video_id, exc)
        return "NETWORK_ERROR", "none", ""
    except Exception as exc:  # noqa: BLE001
        return _classify_generic_error(video_id, exc)

    snippets = _parse_caption_body(body, ext)
    if not snippets:
        logger.warning("fetch_transcript: empty_body id=%s ext=%s", video_id, ext)
        return "NO_CAPTIONS", "none", ""

    text = clean_transcript_snippets(snippets)
    logger.info(
        "fetch_transcript: ok id=%s lang=%s ext=%s chars=%d elapsed=%.2fs",
        video_id, label, ext, len(text), time.time() - started,
    )
    return "OK", label, text


_FRIENDLY_STATUS: dict[str, str] = {
    "OK": "",
    "NO_CAPTIONS": "No captions or subtitles are available for this video.",
    "DISABLED": "The uploader turned off captions for this video.",
    "NETWORK_ERROR": (
        "Couldn't reach YouTube (rate-limited or a network hiccup). "
        "It'll be retried automatically on the next run."
    ),
    "OTHER": "Something went wrong while fetching this transcript.",
}


def friendly_transcript_status(status: str) -> str:
    """Translate a transcript-fetch :data:`Status` into a non-technical sentence (CX).

    Mirrors :func:`shared.youtube.video_downloader.friendly_download_error` for the
    transcript path, where failures surface as a status code rather than an
    exception. The web/CLI use this to explain *why* a video has no transcript in
    plain English instead of showing ``NO_CAPTIONS`` / ``NETWORK_ERROR`` raw.

    Args:
        status: A value from :data:`Status` (or any string; unknown codes get the
            generic ``OTHER`` message so callers never show a blank reason).
    Returns:
        Empty string for ``"OK"`` (no failure to explain); otherwise a friendly,
        user-facing sentence.
    """
    return _FRIENDLY_STATUS.get(status, _FRIENDLY_STATUS["OTHER"])


def _extract_info(video_id: str) -> dict[str, Any]:
    """Run yt-dlp's player extractor and return the info_dict."""
    opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "writesubtitles": False,
        "writeautomaticsub": False,
        # We want metadata only — do NOT enumerate every formats branch.
        "youtube_include_dash_manifest": False,
        "youtube_include_hls_manifest": False,
        "extractor_args": {"youtube": {"skip": ["dash", "hls"]}},
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(
            f"https://www.youtube.com/watch?v={video_id}",
            download=False,
        )
    return info or {}


def _pick_track(
    manual: dict[str, list[dict]],
    auto: dict[str, list[dict]],
    preferred_languages: list[str],
) -> tuple[str, str, str] | None:
    """Apply the FR-3 fallback ladder.

    Returns:
        ``(language_label, track_url, format_ext)`` or ``None`` when neither
        ``manual`` nor ``auto`` contain any usable track.
    """
    # 1) Manual in preferred order.
    for lang in preferred_languages:
        track = _resolve_lang(manual, lang)
        if track:
            url, ext = _pick_format(track)
            return lang, url, ext
    # 2) Auto in preferred order.
    for lang in preferred_languages:
        track = _resolve_lang(auto, lang)
        if track:
            url, ext = _pick_format(track)
            return lang, url, ext
    # 3) Any auto-generated track.
    if auto:
        first_lang = next(iter(auto))
        url, ext = _pick_format(auto[first_lang])
        return "auto", url, ext
    # 4) Any manual track (last resort).
    if manual:
        first_lang = next(iter(manual))
        url, ext = _pick_format(manual[first_lang])
        return "auto", url, ext
    return None


def _resolve_lang(table: dict[str, list[dict]], lang: str) -> list[dict] | None:
    """Look up a language with regional-variant tolerance.

    YouTube exposes both ``en`` and ``en-US`` style codes; ``zh`` may only
    appear as ``zh-Hans`` / ``zh-CN`` / ``zh-TW``. Match the exact code first,
    then any code whose primary subtag (the part before ``-``) matches.
    """
    if lang in table:
        return table[lang]
    primary = lang.split("-", 1)[0].lower()
    for code, tracks in table.items():
        if code.split("-", 1)[0].lower() == primary:
            return tracks
    return None


def _pick_format(tracks: list[dict]) -> tuple[str, str]:
    """Return ``(url, ext)`` for the most-parseable subtitle format."""
    by_ext = {t.get("ext"): t for t in tracks if t.get("url")}
    for ext in _PREFERRED_FORMATS:
        if ext in by_ext:
            return by_ext[ext]["url"], ext
    # Fall back to the first track regardless of format.
    first = next(iter(tracks))
    return first["url"], first.get("ext", "")


def _download(url: str) -> str:
    """GET a yt-dlp-signed caption URL and return the body as text."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT_S) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _parse_caption_body(body: str, ext: str) -> list[dict]:
    """Parse a caption file into ``[{"text": ...}, ...]`` snippet dicts."""
    if ext == "json3":
        return _parse_json3(body)
    if ext in ("vtt", "srt"):
        return _parse_vtt_like(body)
    if ext in ("ttml", "srv1", "srv2", "srv3"):
        return _parse_xml_like(body)
    # Unknown format — last-resort: strip tags, keep printable lines.
    return _parse_xml_like(body)


def _parse_json3(body: str) -> list[dict]:
    """Concatenate every ``segs[*].utf8`` value in the JSON3 events stream."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return []
    out: list[dict] = []
    for event in data.get("events", []) or []:
        segs = event.get("segs") or []
        text = "".join(s.get("utf8", "") for s in segs if isinstance(s, dict))
        text = text.strip()
        if text:
            out.append({"text": text})
    return out


_VTT_TIMECODE_RE = re.compile(r"-->")
_TAG_RE = re.compile(r"<[^>]+>")


def _parse_vtt_like(body: str) -> list[dict]:
    """Strip headers, cue numbers, and timecodes; keep cue text only."""
    out: list[dict] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "WEBVTT" or line.startswith(("NOTE", "STYLE", "Kind:", "Language:")):
            continue
        if _VTT_TIMECODE_RE.search(line):
            continue
        if line.isdigit():
            continue
        text = _TAG_RE.sub("", line).strip()
        if text:
            out.append({"text": text})
    return out


def _parse_xml_like(body: str) -> list[dict]:
    """Drop XML tags and emit non-empty text runs as snippets."""
    text = _TAG_RE.sub(" ", body)
    out: list[dict] = []
    for chunk in text.splitlines():
        s = chunk.strip()
        if s:
            out.append({"text": s})
    return out


def _classify_extract_error(
    video_id: str, exc: Exception,
) -> tuple[Status, str, str]:
    """Map yt-dlp extraction failures to FR-4 status codes."""
    msg = str(exc).lower()
    if any(hint in msg for hint in _DISABLED_HINTS):
        logger.warning("fetch_transcript: disabled id=%s", video_id)
        return "DISABLED", "none", ""
    if any(hint in msg for hint in _NETWORK_HINTS):
        logger.warning("fetch_transcript: network id=%s err=%s", video_id, exc)
        return "NETWORK_ERROR", "none", ""
    if any(hint in msg for hint in _UNAVAILABLE_HINTS):
        logger.warning("fetch_transcript: unavailable id=%s err=%s", video_id, exc)
        return "OTHER", "none", ""
    logger.warning(
        "fetch_transcript: extract_failed id=%s type=%s err=%s",
        video_id, type(exc).__name__, exc,
    )
    return "OTHER", "none", ""


def _classify_generic_error(
    video_id: str, exc: Exception,
) -> tuple[Status, str, str]:
    """Final catch-all — promote any 429/HTTP-5xx hints to NETWORK_ERROR."""
    msg = str(exc).lower()
    if any(hint in msg for hint in _NETWORK_HINTS):
        logger.warning("fetch_transcript: network id=%s err=%s", video_id, exc)
        return "NETWORK_ERROR", "none", ""
    logger.warning(
        "fetch_transcript: other id=%s type=%s err=%s",
        video_id, type(exc).__name__, exc,
    )
    return "OTHER", "none", ""
