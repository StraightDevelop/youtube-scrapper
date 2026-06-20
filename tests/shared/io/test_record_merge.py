"""Characterization tests for shared/io/record_merge.py.

Locks the resume-merge + dedup semantics extracted from the web app.
"""
from __future__ import annotations

from shared.io.record_merge import dedupe_by_id, merge_records


def test_merge_overrides_by_id_and_appends_new() -> None:
    prior = [{"id": "a", "v": 1}, {"id": "b", "v": 1}]
    fresh = [{"id": "b", "v": 2}, {"id": "c", "v": 2}]
    assert merge_records(prior, fresh) == [
        {"id": "a", "v": 1},   # untouched
        {"id": "b", "v": 2},   # overridden in place
        {"id": "c", "v": 2},   # new id appended
    ]


def test_merge_preserves_prior_order() -> None:
    prior = [{"id": "z"}, {"id": "y"}, {"id": "x"}]
    assert [r["id"] for r in merge_records(prior, [])] == ["z", "y", "x"]


def test_merge_skips_records_without_string_id() -> None:
    prior = [{"id": "a"}, {"no_id": 1}, {"id": 123}]
    fresh = [{"id": "b"}, {"id": None}]
    assert [r["id"] for r in merge_records(prior, fresh)] == ["a", "b"]


def test_dedupe_last_occurrence_wins() -> None:
    records = [{"id": "a", "v": 1}, {"id": "a", "v": 2}, {"id": "b", "v": 1}]
    assert dedupe_by_id(records) == [{"id": "a", "v": 2}, {"id": "b", "v": 1}]


def test_dedupe_skips_non_string_id() -> None:
    assert dedupe_by_id([{"id": "a"}, {"no_id": 1}, {"id": 9}]) == [{"id": "a"}]
