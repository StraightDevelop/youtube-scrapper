"""Characterization tests for shared/io/resume_reader.py (FR-7).

Locks the resume/merge behavior: malformed-line tolerance, mtime-ordered merge
(later run overwrites earlier per id), dedup, and id extraction.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from shared.io.resume_reader import (
    collect_channel_history,
    iter_jsonl_records,
    read_existing_video_ids,
)


def _write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# iter_jsonl_records
# --------------------------------------------------------------------------- #
def test_iter_missing_file_yields_nothing(tmp_path: Path) -> None:
    assert list(iter_jsonl_records(tmp_path / "nope.jsonl")) == []


def test_iter_skips_blank_malformed_and_non_dict(tmp_path: Path) -> None:
    path = tmp_path / "out.jsonl"
    _write_lines(
        path,
        [
            json.dumps({"id": "a", "status": "OK"}),
            "",                       # blank -> skipped
            "{not json",              # malformed -> skipped
            "123",                    # valid JSON but not a dict -> skipped
            json.dumps({"id": "b", "status": "OK"}),
        ],
    )
    records = list(iter_jsonl_records(path))
    assert [r["id"] for r in records] == ["a", "b"]


# --------------------------------------------------------------------------- #
# read_existing_video_ids
# --------------------------------------------------------------------------- #
def test_read_ids_missing_file_returns_empty_set(tmp_path: Path) -> None:
    assert read_existing_video_ids(tmp_path / "nope.jsonl") == set()


def test_read_ids_collects_and_skips_invalid(tmp_path: Path) -> None:
    path = tmp_path / "out.jsonl"
    _write_lines(
        path,
        [
            json.dumps({"id": "a"}),
            json.dumps({"no_id": "x"}),   # missing id -> skipped
            "{bad",                        # malformed -> skipped
            "",                            # blank -> skipped
            json.dumps({"id": "b"}),
        ],
    )
    assert read_existing_video_ids(path) == {"a", "b"}


# --------------------------------------------------------------------------- #
# collect_channel_history
# --------------------------------------------------------------------------- #
def test_collect_missing_dir_returns_empty(tmp_path: Path) -> None:
    records, status_by_id = collect_channel_history(tmp_path / "missing", "handle")
    assert records == []
    assert status_by_id == {}


def test_collect_merges_by_mtime_later_overwrites(tmp_path: Path) -> None:
    handle = "handle"
    older = tmp_path / f"{handle}_20260101.jsonl"
    newer = tmp_path / f"{handle}_20260102.jsonl"
    _write_lines(
        older,
        [
            json.dumps({"id": "v1", "status": "NETWORK_ERROR"}),
            json.dumps({"id": "v2", "status": "OK"}),
            json.dumps({"no_id": "skipme"}),   # record without id -> ignored
        ],
    )
    _write_lines(newer, [json.dumps({"id": "v1", "status": "OK"})])
    # A different channel's file must be ignored by the glob.
    _write_lines(tmp_path / "other_20260101.jsonl", [json.dumps({"id": "v9", "status": "OK"})])

    # Force mtime order: older < newer (so newer wins for v1).
    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_700_000_100, 1_700_000_100))

    records, status_by_id = collect_channel_history(tmp_path, handle)

    assert status_by_id == {"v1": "OK", "v2": "OK"}      # v1 upgraded by newer run
    assert {r["id"] for r in records} == {"v1", "v2"}    # deduped, other channel excluded
    assert "v9" not in status_by_id
