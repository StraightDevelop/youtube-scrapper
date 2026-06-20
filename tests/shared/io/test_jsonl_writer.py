"""Characterization tests for shared/io/jsonl_writer.py (FR-7/FR-8).

Locks the append-only, UTF-8, compact-separator JSONL write behavior.
"""
from __future__ import annotations

import json
from pathlib import Path

from shared.io.jsonl_writer import append_jsonl


def test_append_round_trips(tmp_path: Path, make_record) -> None:
    path = tmp_path / "out.jsonl"
    record = make_record("vid00000001")
    append_jsonl(path, record)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == record


def test_append_adds_lines(tmp_path: Path, make_record) -> None:
    path = tmp_path / "out.jsonl"
    append_jsonl(path, make_record("vid00000001"))
    append_jsonl(path, make_record("vid00000002"))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["id"] for line in lines] == ["vid00000001", "vid00000002"]


def test_unicode_written_verbatim(tmp_path: Path, make_record) -> None:
    path = tmp_path / "out.jsonl"
    thai = "สวัสดีชาวโลก"
    append_jsonl(path, make_record("vid00000001", transcript=thai))
    raw = path.read_text(encoding="utf-8")
    assert thai in raw                 # not \uXXXX-escaped
    assert "\\u" not in raw


def test_compact_separators(tmp_path: Path, make_record) -> None:
    path = tmp_path / "out.jsonl"
    append_jsonl(path, make_record("vid00000001"))
    line = path.read_text(encoding="utf-8").splitlines()[0]
    assert ", " not in line            # separators=(",", ":") -> no spaces
    assert ": " not in line


def test_creates_parent_dirs(tmp_path: Path, make_record) -> None:
    path = tmp_path / "nested" / "deeper" / "out.jsonl"
    append_jsonl(path, make_record("vid00000001"))
    assert path.exists()
