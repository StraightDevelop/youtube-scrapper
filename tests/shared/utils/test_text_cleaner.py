"""Characterization tests for shared/utils/text_cleaner.py.

Locks the snippet-concatenation + whitespace-normalization behavior of
``clean_transcript_snippets``.
"""
from __future__ import annotations

from shared.utils.text_cleaner import clean_transcript_snippets


def test_joins_snippets_with_single_space() -> None:
    out = clean_transcript_snippets([{"text": "Hello"}, {"text": "world"}])
    assert out == "Hello world"


def test_normalizes_newlines_and_collapses_whitespace() -> None:
    out = clean_transcript_snippets([{"text": "line1\nline2"}, {"text": "a   b"}])
    assert out == "line1 line2 a b"


def test_strips_carriage_returns_and_outer_whitespace() -> None:
    out = clean_transcript_snippets([{"text": "  start\r"}, {"text": "end  "}])
    assert out == "start end"


def test_skips_non_mapping_and_empty_or_nonstring_text() -> None:
    snippets = [
        "not-a-mapping",          # non-Mapping -> skipped
        {"text": ""},              # empty string -> skipped
        {"text": None},            # non-str -> skipped
        {"no_text_key": "x"},      # missing 'text' -> skipped
        {"text": "kept"},          # the only usable one
    ]
    assert clean_transcript_snippets(snippets) == "kept"


def test_empty_iterable_returns_empty_string() -> None:
    assert clean_transcript_snippets([]) == ""
