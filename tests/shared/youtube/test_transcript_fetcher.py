"""Characterization tests for the NETWORK path of shared/youtube/transcript_fetcher.py.

The yt_dlp boundary is mocked (no network): `_extract_info` / `_download` are
monkeypatched to drive `fetch_transcript` through each FR-4 status branch, and
`_extract_info` / `_download` themselves are covered by faking `YoutubeDL` /
`urllib.request.urlopen`. Pure helpers are covered separately in
`test_transcript_parsing.py`.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest
from yt_dlp.utils import DownloadError, ExtractorError

import shared.youtube.transcript_fetcher as tf


def _raise(exc: Exception):
    """Return a function that raises ``exc`` — for monkeypatching to error paths."""
    def _fn(*_args, **_kwargs):
        raise exc
    return _fn


# --------------------------------------------------------------------------- #
# fetch_transcript — happy path
# --------------------------------------------------------------------------- #
def test_fetch_transcript_ok(monkeypatch) -> None:
    monkeypatch.setattr(
        tf, "_extract_info",
        lambda vid: {"subtitles": {"en": [{"ext": "json3", "url": "u"}]}, "automatic_captions": {}},
    )
    monkeypatch.setattr(
        tf, "_download",
        lambda url: json.dumps({"events": [{"segs": [{"utf8": "Hello world"}]}]}),
    )
    assert tf.fetch_transcript("vid", ["en"]) == ("OK", "en", "Hello world")


# --------------------------------------------------------------------------- #
# fetch_transcript — no captions / empty body
# --------------------------------------------------------------------------- #
def test_fetch_transcript_no_captions(monkeypatch) -> None:
    monkeypatch.setattr(tf, "_extract_info", lambda vid: {})
    assert tf.fetch_transcript("vid", ["en"]) == ("NO_CAPTIONS", "none", "")


def test_fetch_transcript_empty_body_is_empty_captions(monkeypatch) -> None:
    monkeypatch.setattr(
        tf, "_extract_info",
        lambda vid: {"subtitles": {"en": [{"ext": "json3", "url": "u"}]}},
    )
    monkeypatch.setattr(tf, "_download", lambda url: json.dumps({"events": []}))
    # A track existed but parsed empty -> EMPTY_CAPTIONS (distinct from NO_CAPTIONS).
    assert tf.fetch_transcript("vid", ["en"]) == ("EMPTY_CAPTIONS", "none", "")


def test_fetch_transcript_unavailable_via_availability(monkeypatch) -> None:
    monkeypatch.setattr(tf, "_extract_info", lambda vid: {"availability": "private"})
    assert tf.fetch_transcript("vid", ["en"])[0] == "UNAVAILABLE"


def test_fetch_transcript_unavailable_via_age_limit(monkeypatch) -> None:
    monkeypatch.setattr(tf, "_extract_info", lambda vid: {"age_limit": 18})
    assert tf.fetch_transcript("vid", ["en"])[0] == "UNAVAILABLE"


def test_is_unavailable_helper() -> None:
    assert tf._is_unavailable({"availability": "subscriber_only"}) is True
    assert tf._is_unavailable({"age_limit": 21}) is True
    assert tf._is_unavailable({"availability": "public", "age_limit": 0}) is False
    assert tf._is_unavailable({"age_limit": "nope"}) is False   # bad value -> False


# --------------------------------------------------------------------------- #
# fetch_transcript — extraction errors -> classified status
# --------------------------------------------------------------------------- #
def test_fetch_transcript_disabled(monkeypatch) -> None:
    monkeypatch.setattr(tf, "_extract_info", _raise(DownloadError("Subtitles are disabled")))
    assert tf.fetch_transcript("vid", ["en"])[0] == "DISABLED"


def test_fetch_transcript_extractor_error_network(monkeypatch) -> None:
    monkeypatch.setattr(tf, "_extract_info", _raise(ExtractorError("HTTP Error 429: Too Many Requests")))
    assert tf.fetch_transcript("vid", ["en"])[0] == "NETWORK_ERROR"


def test_fetch_transcript_generic_extract_error_other(monkeypatch) -> None:
    monkeypatch.setattr(tf, "_extract_info", _raise(ValueError("totally unexpected")))
    assert tf.fetch_transcript("vid", ["en"])[0] == "OTHER"


# --------------------------------------------------------------------------- #
# fetch_transcript — download errors -> classified status
# --------------------------------------------------------------------------- #
def _info_with_track():
    return {"subtitles": {"en": [{"ext": "json3", "url": "u"}]}, "automatic_captions": {}}


@pytest.mark.parametrize("code, expected", [(429, "NETWORK_ERROR"), (503, "NETWORK_ERROR"), (404, "OTHER")])
def test_fetch_transcript_http_errors(monkeypatch, code: int, expected: str) -> None:
    monkeypatch.setattr(tf, "_extract_info", lambda vid: _info_with_track())
    err = urllib.error.HTTPError("http://x", code, "msg", {}, None)  # type: ignore[arg-type]
    monkeypatch.setattr(tf, "_download", _raise(err))
    assert tf.fetch_transcript("vid", ["en"])[0] == expected


def test_fetch_transcript_urlerror_is_network(monkeypatch) -> None:
    monkeypatch.setattr(tf, "_extract_info", lambda vid: _info_with_track())
    monkeypatch.setattr(tf, "_download", _raise(urllib.error.URLError("boom")))
    assert tf.fetch_transcript("vid", ["en"])[0] == "NETWORK_ERROR"


def test_fetch_transcript_generic_download_error_other(monkeypatch) -> None:
    monkeypatch.setattr(tf, "_extract_info", lambda vid: _info_with_track())
    monkeypatch.setattr(tf, "_download", _raise(ValueError("weird")))
    assert tf.fetch_transcript("vid", ["en"])[0] == "OTHER"


# --------------------------------------------------------------------------- #
# _extract_info — fake YoutubeDL
# --------------------------------------------------------------------------- #
class _FakeYDL:
    """Minimal context-manager stand-in for yt_dlp.YoutubeDL."""

    def __init__(self, opts=None, info=None):
        self._info = info

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def extract_info(self, url, download=False):
        return self._info


def test_extract_info_returns_info(monkeypatch) -> None:
    monkeypatch.setattr(tf, "YoutubeDL", lambda opts: _FakeYDL(opts, info={"id": "vid"}))
    assert tf._extract_info("vid") == {"id": "vid"}


def test_extract_info_none_becomes_empty_dict(monkeypatch) -> None:
    monkeypatch.setattr(tf, "YoutubeDL", lambda opts: _FakeYDL(opts, info=None))
    assert tf._extract_info("vid") == {}


# --------------------------------------------------------------------------- #
# _download — fake urlopen
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def read(self):
        return self._payload


def test_download_decodes_body(monkeypatch) -> None:
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda req, timeout=None: _FakeResponse("สวัสดี".encode("utf-8")),
    )
    assert tf._download("http://x/caption") == "สวัสดี"
