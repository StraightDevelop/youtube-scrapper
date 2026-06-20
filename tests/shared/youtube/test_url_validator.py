"""Characterization tests for shared/youtube/url_validator.py (FR-1).

Locks the current channel/video/playlist URL parsing behavior so later changes are
deliberate. No production logic is exercised beyond these public + helper functions.
"""
from __future__ import annotations

import pytest

from shared.youtube.url_validator import (
    channel_playlists_tab_url,
    extract_channel_handle,
    extract_playlist_id,
    parse_video_id,
    validate_channel_url,
    validate_playlist_url,
)

_VID = "dQw4w9WgXcQ"  # canonical 11-char sample id


# --------------------------------------------------------------------------- #
# validate_channel_url
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://youtube.com/@handle", "https://youtube.com/@handle/videos"),
        ("https://www.youtube.com/channel/UCabcdef", "https://www.youtube.com/channel/UCabcdef/videos"),
        ("https://youtube.com/c/SomeName", "https://youtube.com/c/SomeName/videos"),
        ("https://youtube.com/user/LegacyName", "https://youtube.com/user/LegacyName/videos"),
        # /videos already present -> not duplicated
        ("https://youtube.com/@handle/videos", "https://youtube.com/@handle/videos"),
        # m.youtube.com host accepted; whitespace trimmed
        ("  https://m.youtube.com/@handle  ", "https://m.youtube.com/@handle/videos"),
    ],
)
def test_validate_channel_url_canonicalizes(url: str, expected: str) -> None:
    assert validate_channel_url(url) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "ftp://youtube.com/@handle",          # non-http(s) scheme
        "https://example.com/@handle",        # non-YouTube host
        "https://youtube.com/",               # missing channel path
        "https://youtube.com/watch",          # unrecognized head
        "https://youtube.com/channel",        # PATH kind but no name segment
    ],
)
def test_validate_channel_url_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        validate_channel_url(bad)


def test_validate_channel_url_rejects_non_string() -> None:
    with pytest.raises(ValueError):
        validate_channel_url(None)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# extract_channel_handle
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://youtube.com/@My.Handle/videos", "My.Handle"),
        ("https://www.youtube.com/channel/UCabcdef/videos", "UCabcdef"),
        ("https://youtube.com/c/SomeName/videos", "SomeName"),
        ("https://youtube.com/user/LegacyName/videos", "LegacyName"),
        ("https://youtube.com/videos", "videos"),       # fallback: head sanitized
        ("https://youtube.com/@a*b", "a_b"),            # unsafe char -> underscore
    ],
)
def test_extract_channel_handle(url: str, expected: str) -> None:
    assert extract_channel_handle(url) == expected


def test_extract_channel_handle_empty_path_falls_back() -> None:
    assert extract_channel_handle("https://youtube.com/") == "channel"


def test_extract_channel_handle_sanitizes_to_default_when_empty() -> None:
    # "@..." -> strip("._-") removes all -> "" -> "channel"
    assert extract_channel_handle("https://youtube.com/@...") == "channel"


# --------------------------------------------------------------------------- #
# parse_video_id
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw",
    [
        _VID,                                                  # bare 11-char id
        f"https://www.youtube.com/watch?v={_VID}",
        f"https://www.youtube.com/watch?v={_VID}&t=10s",       # extra query
        f"https://youtu.be/{_VID}",
        f"https://www.youtube.com/shorts/{_VID}",
        f"https://www.youtube.com/embed/{_VID}",
        f"https://www.youtube.com/v/{_VID}",
        f"https://www.youtube.com/live/{_VID}",
    ],
)
def test_parse_video_id_accepts(raw: str) -> None:
    assert parse_video_id(raw) == _VID


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        f"ftp://youtu.be/{_VID}",                  # bad scheme
        "https://youtube.com/watch",               # no v= and not a video path
        "https://example.com/watch?v=" + _VID,     # non-YouTube host
        "https://youtu.be/short",                  # too-short id
    ],
)
def test_parse_video_id_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_video_id(bad)


# --------------------------------------------------------------------------- #
# validate_playlist_url + extract_playlist_id
# --------------------------------------------------------------------------- #
def test_validate_playlist_url_canonicalizes() -> None:
    out = validate_playlist_url("https://www.youtube.com/playlist?list=PLabc123def456&x=1")
    assert out == "https://www.youtube.com/playlist?list=PLabc123def456"


def test_validate_playlist_url_preserves_host() -> None:
    out = validate_playlist_url("https://youtube.com/playlist?list=PLabc123def456")
    assert out == "https://youtube.com/playlist?list=PLabc123def456"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "ftp://youtube.com/playlist?list=PLabc123def456",       # scheme
        "https://example.com/playlist?list=PLabc123def456",     # host
        "https://www.youtube.com/watch?v=x&list=PLabc123def456",  # /watch, not /playlist
        "https://www.youtube.com/playlist",                     # no list=
        "https://www.youtube.com/playlist?list=PL",             # malformed (too short) id
    ],
)
def test_validate_playlist_url_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        validate_playlist_url(bad)


def test_extract_playlist_id() -> None:
    assert extract_playlist_id("https://www.youtube.com/playlist?list=PLabc123def456") == "PLabc123def456"


def test_extract_playlist_id_missing_raises() -> None:
    with pytest.raises(ValueError):
        extract_playlist_id("https://www.youtube.com/playlist")


# --------------------------------------------------------------------------- #
# channel_playlists_tab_url
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "channel_url, expected",
    [
        ("https://youtube.com/@handle/videos", "https://youtube.com/@handle/playlists"),
        ("https://youtube.com/channel/UCabcdef/videos", "https://youtube.com/channel/UCabcdef/playlists"),
        # no recognized trailing tab -> append /playlists
        ("https://youtube.com/@handle", "https://youtube.com/@handle/playlists"),
    ],
)
def test_channel_playlists_tab_url(channel_url: str, expected: str) -> None:
    assert channel_playlists_tab_url(channel_url) == expected


def test_channel_playlists_tab_url_no_path_raises() -> None:
    with pytest.raises(ValueError):
        channel_playlists_tab_url("https://youtube.com/")
