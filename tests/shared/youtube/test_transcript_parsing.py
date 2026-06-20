"""Characterization tests for the PURE helpers in shared/youtube/transcript_fetcher.py.

Covers caption parsing, the FR-3 language fallback ladder, format selection, and
FR-4 error classification — all without touching the network functions
(fetch_transcript / _extract_info / _download), which are Phase 2 (mocked).

NOTE: importing the module imports yt_dlp at load time (a pinned runtime dep);
no network is hit because only pure helpers are called here.
"""
from __future__ import annotations

import json

import pytest

from shared.youtube.transcript_fetcher import (
    _classify_extract_error,
    _classify_generic_error,
    _parse_caption_body,
    _parse_json3,
    _parse_vtt_like,
    _parse_xml_like,
    _pick_format,
    _pick_track,
    _resolve_lang,
    friendly_transcript_status,
    summarize_failures,
)


# --------------------------------------------------------------------------- #
# _parse_json3
# --------------------------------------------------------------------------- #
def test_parse_json3_concatenates_segs_per_event() -> None:
    body = json.dumps(
        {
            "events": [
                {"segs": [{"utf8": "Hello "}, {"utf8": "world"}]},
                {"segs": [{"utf8": "second"}]},
                {"segs": [{"utf8": "   "}]},   # whitespace-only -> dropped
                {"segs": []},                  # empty -> dropped
            ]
        }
    )
    assert _parse_json3(body) == [{"text": "Hello world"}, {"text": "second"}]


def test_parse_json3_malformed_returns_empty() -> None:
    assert _parse_json3("{not json") == []


def test_parse_json3_no_events_key_returns_empty() -> None:
    assert _parse_json3(json.dumps({})) == []


# --------------------------------------------------------------------------- #
# _parse_vtt_like  (vtt + srt)
# --------------------------------------------------------------------------- #
def test_parse_vtt_strips_headers_timecodes_numbers_tags() -> None:
    body = "\n".join(
        [
            "WEBVTT",
            "",
            "1",
            "00:00:00.000 --> 00:00:02.000",
            "Hello <c>world</c>",
            "",
            "NOTE this is a note",
            "2",
            "00:00:02.000 --> 00:00:04.000",
            "second line",
        ]
    )
    assert _parse_vtt_like(body) == [{"text": "Hello world"}, {"text": "second line"}]


# --------------------------------------------------------------------------- #
# _parse_xml_like  (ttml / srv1-3 / unknown fallback)
# --------------------------------------------------------------------------- #
def test_parse_xml_strips_tags_per_line() -> None:
    body = "<p>Line one</p>\n<p>Line two</p>"
    assert _parse_xml_like(body) == [{"text": "Line one"}, {"text": "Line two"}]


# --------------------------------------------------------------------------- #
# _parse_caption_body dispatch
# --------------------------------------------------------------------------- #
def test_parse_caption_body_dispatches_by_ext() -> None:
    json3 = json.dumps({"events": [{"segs": [{"utf8": "hi"}]}]})
    assert _parse_caption_body(json3, "json3") == [{"text": "hi"}]
    assert _parse_caption_body("WEBVTT\n\n00:00 --> 00:01\nhi", "vtt") == [{"text": "hi"}]
    # ttml / srv* route to the xml-like parser
    assert _parse_caption_body("<p>hi</p>", "ttml") == [{"text": "hi"}]
    assert _parse_caption_body("<text>hi</text>", "srv3") == [{"text": "hi"}]
    # unknown ext falls back to the xml-like parser
    assert _parse_caption_body("<p>hi</p>", "weird-ext") == [{"text": "hi"}]


# --------------------------------------------------------------------------- #
# _resolve_lang
# --------------------------------------------------------------------------- #
def test_resolve_lang_exact_match() -> None:
    table = {"en": ["x"]}
    assert _resolve_lang(table, "en") == ["x"]


def test_resolve_lang_primary_subtag_match() -> None:
    assert _resolve_lang({"en-US": ["x"]}, "en") == ["x"]
    assert _resolve_lang({"zh-Hans": ["x"]}, "zh") == ["x"]


def test_resolve_lang_exact_preferred_over_variant() -> None:
    table = {"en": ["exact"], "en-US": ["variant"]}
    assert _resolve_lang(table, "en") == ["exact"]


def test_resolve_lang_no_match_returns_none() -> None:
    assert _resolve_lang({"fr": ["x"]}, "en") is None


# --------------------------------------------------------------------------- #
# _pick_format
# --------------------------------------------------------------------------- #
def test_pick_format_prefers_format_order() -> None:
    tracks = [
        {"ext": "vtt", "url": "u_vtt"},
        {"ext": "json3", "url": "u_json3"},   # json3 ranks first in _PREFERRED_FORMATS
    ]
    assert _pick_format(tracks) == ("u_json3", "json3")


def test_pick_format_falls_back_to_first_when_no_preferred() -> None:
    tracks = [{"ext": "xyz", "url": "u_xyz"}]
    assert _pick_format(tracks) == ("u_xyz", "xyz")


# --------------------------------------------------------------------------- #
# _pick_track  — the FR-3 fallback ladder
# --------------------------------------------------------------------------- #
def _track(url: str, ext: str = "json3") -> list[dict]:
    return [{"ext": ext, "url": url}]


def test_pick_track_manual_preferred_first() -> None:
    manual = {"en": _track("m_en")}
    auto = {"en": _track("a_en")}
    assert _pick_track(manual, auto, ["en"]) == ("en", "m_en", "json3")


def test_pick_track_auto_preferred_when_no_manual() -> None:
    assert _pick_track({}, {"en": _track("a_en")}, ["en"]) == ("en", "a_en", "json3")


def test_pick_track_any_auto_when_no_preferred_match() -> None:
    assert _pick_track({}, {"fr": _track("a_fr")}, ["en"]) == ("auto", "a_fr", "json3")


def test_pick_track_any_manual_last_resort_when_auto_empty() -> None:
    assert _pick_track({"de": _track("m_de")}, {}, ["en"]) == ("auto", "m_de", "json3")


def test_pick_track_regional_variant_keeps_preferred_label() -> None:
    # en-US resolves for preferred "en"; label is the requested code, not the variant.
    assert _pick_track({"en-US": _track("m")}, {}, ["en"]) == ("en", "m", "json3")


def test_pick_track_none_when_empty() -> None:
    assert _pick_track({}, {}, ["en"]) is None


# --------------------------------------------------------------------------- #
# _classify_extract_error / _classify_generic_error
# --------------------------------------------------------------------------- #
def test_classify_extract_error_disabled() -> None:
    assert _classify_extract_error("vid", Exception("Subtitles are disabled")) == (
        "DISABLED",
        "none",
        "",
    )


def test_classify_extract_error_network_on_429() -> None:
    status, label, text = _classify_extract_error("vid", Exception("HTTP Error 429: Too Many Requests"))
    assert (status, label, text) == ("NETWORK_ERROR", "none", "")


def test_classify_extract_error_unavailable() -> None:
    assert _classify_extract_error("vid", Exception("Private video"))[0] == "UNAVAILABLE"


def test_classify_extract_error_unknown_is_other() -> None:
    assert _classify_extract_error("vid", Exception("totally unexpected"))[0] == "OTHER"


def test_classify_generic_error_network_hint() -> None:
    assert _classify_generic_error("vid", Exception("Connection timed out"))[0] == "NETWORK_ERROR"


def test_classify_generic_error_other() -> None:
    assert _classify_generic_error("vid", Exception("weird"))[0] == "OTHER"


# --------------------------------------------------------------------------- #
# friendly_transcript_status (CX — Phase 2c)
# --------------------------------------------------------------------------- #
def test_friendly_transcript_status_ok_is_empty() -> None:
    assert friendly_transcript_status("OK") == ""


@pytest.mark.parametrize(
    "status",
    ["NO_CAPTIONS", "EMPTY_CAPTIONS", "DISABLED", "NETWORK_ERROR", "UNAVAILABLE", "OTHER"],
)
def test_friendly_transcript_status_failures_have_plain_message(status: str) -> None:
    msg = friendly_transcript_status(status)
    assert msg and not msg.isupper()        # human sentence, not the raw code


def test_friendly_transcript_status_unknown_falls_back() -> None:
    assert friendly_transcript_status("WAT") == friendly_transcript_status("OTHER")


# --------------------------------------------------------------------------- #
# summarize_failures (CX — Phase 2c summary)
# --------------------------------------------------------------------------- #
def test_summarize_failures_builds_breakdown() -> None:
    out = summarize_failures({"NO_CAPTIONS": 2, "NETWORK_ERROR": 1})
    assert out == "2 no captions · 1 rate-limited / network"


def test_summarize_failures_ignores_zero_and_ok() -> None:
    assert summarize_failures({"OK": 5, "DISABLED": 0}) == ""


def test_summarize_failures_empty() -> None:
    assert summarize_failures({}) == ""
