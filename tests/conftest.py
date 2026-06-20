"""Shared pytest fixtures for the pure-logic safety net (Phase 1).

Test file basenames are unique across the suite, so no per-package ``__init__.py``
is needed; ``pythonpath = ["."]`` in ``pyproject.toml`` makes ``from shared...``
imports resolve from the repo root.
"""
from __future__ import annotations

from typing import Callable

import pytest


@pytest.fixture
def make_record() -> Callable[..., dict]:
    """Return a factory that builds a JSONL video record.

    Args (of the returned factory):
        video_id: value for the ``id`` key (the field resume/dedup key on).
        status: FR-4 status string (``OK``/``NETWORK_ERROR``/…).
        **extra: any additional record fields to merge in.
    Returns:
        Factory ``make_record(video_id="vid00000001", status="OK", **extra) -> dict``.
        Defined as a factory (not a static dict) so each test can vary id/status
        without mutating a shared object.
    """
    def _make(video_id: str = "vid00000001", status: str = "OK", **extra: object) -> dict:
        record = {"id": video_id, "title": f"Title {video_id}", "status": status}
        record.update(extra)
        return record

    return _make
