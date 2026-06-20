"""YouTube channel URL validation and normalization (FR-1)."""
from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urlparse, urlunparse

logger = logging.getLogger(__name__)

_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
_HANDLE_RE = re.compile(r"^@[\w.\-]+$")
_PATH_KINDS = {"channel", "c", "user"}
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.\-]")
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{11}$")
_VIDEO_PATH_KINDS = {"shorts", "embed", "v", "live"}
# Playlist IDs vary by kind (PL, UU, LL, FL, RD…) but are always 24+ chars.
# We accept anything that looks like a YouTube list ID — the canonical one
# returned by /playlists?list=… is what we'll round-trip on.
_PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{12,}$")


def validate_channel_url(url: str) -> str:
    """Validate a YouTube channel URL and return a canonical form ending in ``/videos``.

    Accepts the three forms required by FR-1:
      * ``https://youtube.com/@handle``
      * ``https://youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxx``
      * ``https://youtube.com/c/customname`` (and the legacy ``/user/name``)

    Args:
        url: Raw URL passed on the command line.
    Returns:
        Canonical URL with the ``/videos`` suffix appended when missing.
    Raises:
        ValueError: when the URL is empty, not http(s), not a YouTube host,
            or does not match a recognized channel path.
    """
    logger.debug("validate_channel_url: start url=%r", url)
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Channel URL must be a non-empty string")
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"URL must use http(s) scheme: {url}")
    host = parsed.netloc.lower()
    if host not in _YT_HOSTS:
        raise ValueError(f"URL must be a YouTube channel URL (got host {host!r}): {url}")
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        raise ValueError(f"URL is missing a channel path: {url}")
    head = parts[0]
    if _HANDLE_RE.match(head):
        canonical_parts = [head]
    elif head in _PATH_KINDS and len(parts) >= 2:
        canonical_parts = parts[:2]
    else:
        raise ValueError(f"Unrecognized YouTube channel URL: {url}")
    if canonical_parts[-1] != "videos":
        canonical_parts.append("videos")
    canonical_path = "/" + "/".join(canonical_parts)
    canonical = urlunparse((parsed.scheme, parsed.netloc, canonical_path, "", "", ""))
    logger.debug("validate_channel_url: ok canonical=%s", canonical)
    return canonical


def extract_channel_handle(url: str) -> str:
    """Derive a filesystem-safe handle from a (validated) channel URL.

    Args:
        url: A URL already accepted by :func:`validate_channel_url`.
    Returns:
        Sanitized handle suitable for use as a filename prefix. Falls back to
        ``"channel"`` if the URL shape is unexpected.
    """
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return "channel"
    head = parts[0]
    if _HANDLE_RE.match(head):
        return _sanitize(head[1:])  # strip leading @
    if head in _PATH_KINDS and len(parts) >= 2:
        return _sanitize(parts[1])
    return _sanitize(head)


def _sanitize(name: str) -> str:
    """Replace any character outside ``[A-Za-z0-9_.-]`` with an underscore."""
    safe = _SAFE_NAME_RE.sub("_", name).strip("._-")
    return safe or "channel"


def parse_video_id(url_or_id: str) -> str:
    """Extract a YouTube video ID from a URL or accept a raw 11-char ID.

    Supported inputs:
      * ``https://www.youtube.com/watch?v=VIDEOID`` (and any extra query string)
      * ``https://youtu.be/VIDEOID``
      * ``https://www.youtube.com/shorts/VIDEOID`` (and ``/embed/``, ``/v/``, ``/live/``)
      * raw 11-character ID matching ``[A-Za-z0-9_-]{11}``

    Args:
        url_or_id: User-supplied string from a paste box or argv.
    Returns:
        The 11-character YouTube video ID.
    Raises:
        ValueError: if the input is empty, not http(s), not a YouTube host,
            or doesn't carry a recognisable video ID.
    """
    if not isinstance(url_or_id, str) or not url_or_id.strip():
        raise ValueError("Video URL/ID must be a non-empty string")
    raw = url_or_id.strip()
    if _VIDEO_ID_RE.match(raw):
        return raw
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"URL must use http(s) scheme: {url_or_id}")
    host = parsed.netloc.lower()
    parts = [p for p in parsed.path.split("/") if p]
    if host == "youtu.be":
        if parts and _VIDEO_ID_RE.match(parts[0]):
            return parts[0]
    elif host in _YT_HOSTS:
        # /watch?v=ID
        query = parse_qs(parsed.query)
        v_values = query.get("v") or []
        if v_values and _VIDEO_ID_RE.match(v_values[0]):
            return v_values[0]
        # /shorts/ID, /embed/ID, /v/ID, /live/ID
        if len(parts) >= 2 and parts[0] in _VIDEO_PATH_KINDS and _VIDEO_ID_RE.match(parts[1]):
            return parts[1]
    raise ValueError(f"Unrecognized YouTube video URL/ID: {url_or_id}")


def validate_playlist_url(url: str) -> str:
    """Validate a YouTube playlist URL and return the canonical ``/playlist?list=…`` form.

    Accepts only URLs whose **path** begins with ``/playlist`` and which carry
    a non-empty ``list=`` query parameter. A ``/watch?v=…&list=…`` URL is
    deliberately **not** accepted — that's a video that happens to be part of
    a playlist; callers should use :func:`parse_video_id` for it instead.

    Args:
        url: Raw URL.
    Returns:
        ``https://www.youtube.com/playlist?list=<ID>`` (host preserved).
    Raises:
        ValueError: when the URL is empty, not http(s), not a YouTube host,
            doesn't have a ``/playlist`` path, or has no/invalid ``list=`` ID.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Playlist URL must be a non-empty string")
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"URL must use http(s) scheme: {url}")
    host = parsed.netloc.lower()
    if host not in _YT_HOSTS:
        raise ValueError(f"URL must be a YouTube playlist URL: {url}")
    parts = [p for p in parsed.path.split("/") if p]
    if not parts or parts[0] != "playlist":
        raise ValueError(f"URL path must start with /playlist: {url}")
    list_values = parse_qs(parsed.query).get("list") or []
    if not list_values:
        raise ValueError(f"Playlist URL is missing ?list=…: {url}")
    pid = list_values[0]
    if not _PLAYLIST_ID_RE.match(pid):
        raise ValueError(f"Malformed playlist ID: {pid!r}")
    return urlunparse((parsed.scheme, parsed.netloc, "/playlist", "", f"list={pid}", ""))


def extract_playlist_id(url: str) -> str:
    """Return the playlist ID from a (validated) playlist URL.

    Args:
        url: A URL already accepted by :func:`validate_playlist_url`.
    Returns:
        The 12+ character playlist ID (e.g. ``"PLxxxxxxxxxx"``).
    Raises:
        ValueError: if the URL has no ``list=`` parameter (defensive).
    """
    parsed = urlparse(url)
    list_values = parse_qs(parsed.query).get("list") or []
    if not list_values:
        raise ValueError(f"URL has no playlist ID: {url}")
    return list_values[0]


def channel_playlists_tab_url(channel_url: str) -> str:
    """Return the ``/playlists`` tab URL for a (validated) channel URL.

    Used by the playlist picker to enumerate a channel's playlists.

    Args:
        channel_url: Output of :func:`validate_channel_url` (ends with /videos).
    Returns:
        Same URL with the trailing path component swapped for ``/playlists``.
    """
    parsed = urlparse(channel_url)
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        raise ValueError(f"Channel URL has no path: {channel_url}")
    if parts[-1] in {"videos", "playlists", "shorts", "streams", "featured", "community"}:
        parts = parts[:-1]
    parts.append("playlists")
    return urlunparse((parsed.scheme, parsed.netloc, "/" + "/".join(parts), "", "", ""))
