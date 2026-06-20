"""Characterization tests for shared/youtube/channel_extractor.py (FR-2).

Pure walk/convert helpers are tested directly; the three yt_dlp-backed listing
functions are driven through a mocked ``YoutubeDL`` (no network).
"""
from __future__ import annotations

import shared.youtube.channel_extractor as ce
from shared.youtube.channel_extractor import (
    _format_upload_date,
    _to_playlist_meta,
    _to_video_meta,
    _walk_entries,
)

_CHANNEL = "https://youtube.com/@handle/videos"
_PL = "PLabc12345678"   # 13 chars -> looks like a playlist id


# --------------------------------------------------------------------------- #
# _format_upload_date
# --------------------------------------------------------------------------- #
def test_format_upload_date() -> None:
    assert _format_upload_date("20260115") == "2026-01-15"
    assert _format_upload_date(20260115) == "2026-01-15"   # int coerced
    assert _format_upload_date(None) == ""
    assert _format_upload_date("bad") == ""
    assert _format_upload_date("2026011") == ""            # wrong length


# --------------------------------------------------------------------------- #
# _to_video_meta
# --------------------------------------------------------------------------- #
def test_to_video_meta_full() -> None:
    meta = _to_video_meta(
        {"id": "vid12345678", "title": "  Hi  ", "url": "https://x", "upload_date": "20260115", "duration": 120}
    )
    assert meta == {
        "id": "vid12345678",
        "title": "Hi",
        "url": "https://x",
        "upload_date": "2026-01-15",
        "duration_seconds": 120,
    }


def test_to_video_meta_defaults_and_url_rebuild() -> None:
    meta = _to_video_meta({"id": "abc", "url": "notaurl"})
    assert meta["title"] == "abc"                               # title falls back to id
    assert meta["url"] == "https://www.youtube.com/watch?v=abc"  # rebuilt from id
    assert meta["upload_date"] == ""
    assert meta["duration_seconds"] == 0


def test_to_video_meta_no_id_no_url() -> None:
    assert _to_video_meta({}) ["url"] == ""


def test_to_video_meta_bad_duration() -> None:
    assert _to_video_meta({"id": "x", "duration": "NaN"})["duration_seconds"] == 0


# --------------------------------------------------------------------------- #
# _to_playlist_meta
# --------------------------------------------------------------------------- #
def test_to_playlist_meta_valid() -> None:
    meta = _to_playlist_meta({"id": _PL, "title": "My PL", "playlist_count": 5})
    assert meta == {
        "id": _PL,
        "title": "My PL",
        "url": f"https://www.youtube.com/playlist?list={_PL}",
        "video_count": 5,
    }


def test_to_playlist_meta_rejects_video_id() -> None:
    assert _to_playlist_meta({"id": "vid12345678"}) is None     # 11 chars -> not a playlist
    assert _to_playlist_meta({"id": 123}) is None               # non-str


def test_to_playlist_meta_count_fallbacks_and_bad_count() -> None:
    assert _to_playlist_meta({"id": _PL, "n_entries": 3})["video_count"] == 3
    assert _to_playlist_meta({"id": _PL, "playlist_count": "x"})["video_count"] == 0
    assert _to_playlist_meta({"id": _PL})["title"] == _PL       # title falls back to id


# --------------------------------------------------------------------------- #
# _walk_entries
# --------------------------------------------------------------------------- #
def test_walk_entries_none_and_single_video() -> None:
    assert list(_walk_entries(None)) == []
    assert list(_walk_entries({"id": "solo"})) == [{"id": "solo"}]   # single-video info
    assert list(_walk_entries({})) == []


def test_walk_entries_flat_and_nested() -> None:
    flat = {"entries": [{"id": "a"}, None, {"id": "b"}]}             # None entry skipped
    assert [e["id"] for e in _walk_entries(flat)] == ["a", "b"]
    nested = {"entries": [{"entries": [{"id": "x"}, {"id": "y"}]}]}
    assert [e["id"] for e in _walk_entries(nested)] == ["x", "y"]


# --------------------------------------------------------------------------- #
# Network functions via mocked YoutubeDL
# --------------------------------------------------------------------------- #
def test_list_channel_videos_dedups(monkeypatch, make_fake_ydl) -> None:
    info = {"entries": [{"id": "v1", "title": "A"}, {"id": "v1"}, {"id": "v2"}, {"id": ""}]}
    monkeypatch.setattr(ce, "YoutubeDL", make_fake_ydl(info=info))
    videos = ce.list_channel_videos(_CHANNEL)
    assert [v["id"] for v in videos] == ["v1", "v2"]   # deduped, empty id skipped


def test_list_channel_playlists_filters_and_dedups(monkeypatch, make_fake_ydl) -> None:
    info = {"entries": [{"id": _PL, "title": "P1"}, {"id": _PL}, {"id": "vid12345678"}]}
    monkeypatch.setattr(ce, "YoutubeDL", make_fake_ydl(info=info))
    playlists = ce.list_channel_playlists(_CHANNEL)
    assert [p["id"] for p in playlists] == [_PL]       # video id dropped, playlist deduped


def test_fetch_video_meta_fills_defaults(monkeypatch, make_fake_ydl) -> None:
    monkeypatch.setattr(ce, "YoutubeDL", make_fake_ydl(info={}))
    meta = ce.fetch_video_meta("vid12345678")
    assert meta["id"] == "vid12345678"
    assert meta["url"] == "https://www.youtube.com/watch?v=vid12345678"
