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


@pytest.fixture
def make_fake_ydl() -> Callable[..., type]:
    """Return a factory that builds a fake ``yt_dlp.YoutubeDL`` class.

    Lets tests mock the yt_dlp boundary without network. Monkeypatch a module's
    ``YoutubeDL`` symbol with the returned class:
        ``monkeypatch.setattr(mod, "YoutubeDL", make_fake_ydl(info={...}))``

    Args (of the factory):
        info: dict returned by ``extract_info`` (``{}`` when None).
        exc: if set, ``extract_info`` raises it (to drive error paths).
        hook_events: list of progress-hook event dicts fired (in order) against
            the constructed instance's ``opts["progress_hooks"]`` when
            ``extract_info(..., download=True)`` is called — mirrors yt_dlp's
            download progress callbacks.
        prepared: value returned by ``prepare_filename`` (download fallback path).
    Returns:
        A class accepting ``(opts)`` whose instances are context managers.
    """
    def _factory(*, info=None, exc=None, hook_events=None, prepared: str = "") -> type:
        class _FakeYoutubeDL:
            def __init__(self, opts=None):
                self.opts = opts or {}

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def extract_info(self, url, download=False):
                if exc is not None:
                    raise exc
                if download:
                    for event in hook_events or []:
                        for hook in self.opts.get("progress_hooks", []):
                            hook(event)
                return {} if info is None else info

            def prepare_filename(self, _info):
                return prepared

        return _FakeYoutubeDL

    return _factory
