"""Characterization tests for shared/io/summary_writer.py (FR-8).

Locks the pretty-printed, key-order-preserving, UTF-8 summary.json write behavior.
"""
from __future__ import annotations

import json
from pathlib import Path

from shared.io.summary_writer import write_summary

_SUMMARY = {
    "channel": "@example",
    "scraped_at": "2026-06-20T00:00:00Z",
    "total_videos": 3,
    "successful": 2,
    "failed": 1,
    "failures_by_status": {"NETWORK_ERROR": 1},
}


def test_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "run.summary.json"
    write_summary(path, _SUMMARY)
    assert json.loads(path.read_text(encoding="utf-8")) == _SUMMARY


def test_pretty_printed_with_trailing_newline(tmp_path: Path) -> None:
    path = tmp_path / "run.summary.json"
    write_summary(path, _SUMMARY)
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert '\n  "channel"' in text     # indent=2


def test_key_order_preserved(tmp_path: Path) -> None:
    path = tmp_path / "run.summary.json"
    write_summary(path, _SUMMARY)
    text = path.read_text(encoding="utf-8")
    # sort_keys=False -> output follows insertion order
    assert text.index('"channel"') < text.index('"scraped_at"') < text.index('"failed"')


def test_unicode_preserved(tmp_path: Path) -> None:
    path = tmp_path / "run.summary.json"
    write_summary(path, {"channel": "ไทย"})
    raw = path.read_text(encoding="utf-8")
    assert "ไทย" in raw
    assert "\\u" not in raw


def test_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "run.summary.json"
    write_summary(path, _SUMMARY)
    assert path.exists()
