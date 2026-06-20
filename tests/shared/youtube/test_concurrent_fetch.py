"""Tests for shared/youtube/concurrent_fetch.py (Phase 2b).

Drives the thread-pool transcript streamer with an injected fake ``fetch_fn`` —
no network. Verifies completeness, bounded concurrency, delay politeness, and
defensive per-item error handling.
"""
from __future__ import annotations

import threading
import time

import shared.youtube.concurrent_fetch as cf
from shared.youtube.concurrent_fetch import resolve_max_workers, stream_transcripts


def test_resolve_max_workers(monkeypatch) -> None:
    assert resolve_max_workers(7) == 7                          # explicit value wins
    assert resolve_max_workers(0) == 1                          # clamped to >= 1
    monkeypatch.delenv("YT_SCRAPER_MAX_WORKERS", raising=False)
    assert resolve_max_workers() == 4                           # unset -> default
    monkeypatch.setenv("YT_SCRAPER_MAX_WORKERS", "3")
    assert resolve_max_workers() == 3                           # from env
    monkeypatch.setenv("YT_SCRAPER_MAX_WORKERS", "not-a-number")
    assert resolve_max_workers() == 4                           # bad env -> default


def _videos(n: int) -> list[dict]:
    return [{"id": f"v{i:03d}", "title": f"Title {i}"} for i in range(n)]


def test_all_videos_fetched_once() -> None:
    seen: list[str] = []
    lock = threading.Lock()

    def fake(video_id: str, languages: list[str]):
        with lock:
            seen.append(video_id)
        return ("OK", "en", f"text-{video_id}")

    videos = _videos(10)
    results = list(stream_transcripts(videos, ["en"], max_workers=4, fetch_fn=fake))

    assert sorted(seen) == [v["id"] for v in videos]              # each fetched exactly once
    by_id = {video["id"]: text for video, _s, _l, text in results}
    assert by_id == {v["id"]: f"text-{v['id']}" for v in videos}  # every result yielded


def test_bounded_concurrency() -> None:
    in_flight = 0
    max_seen = 0
    lock = threading.Lock()

    def fake(video_id: str, languages: list[str]):
        nonlocal in_flight, max_seen
        with lock:
            in_flight += 1
            max_seen = max(max_seen, in_flight)
        time.sleep(0.03)            # hold the slot so overlap is observable
        with lock:
            in_flight -= 1
        return ("OK", "en", "")

    list(stream_transcripts(_videos(12), ["en"], max_workers=3, fetch_fn=fake))
    assert max_seen <= 3            # never exceeds max_workers


def test_delay_is_honored(monkeypatch) -> None:
    recorded: list[float] = []
    real_sleep = time.sleep
    monkeypatch.setattr(cf.time, "sleep", lambda s: recorded.append(s) or real_sleep(0))

    list(stream_transcripts(_videos(3), ["en"], max_workers=2, delay=0.5,
                            fetch_fn=lambda vid, langs: ("OK", "en", "")))
    assert recorded.count(0.5) == 3   # one politeness sleep per task


def test_failing_fetch_yields_other() -> None:
    def fake(video_id: str, languages: list[str]):
        if video_id == "v001":
            raise RuntimeError("boom")
        return ("OK", "en", "ok")

    results = {video["id"]: (status, lang, text)
               for video, status, lang, text in
               stream_transcripts(_videos(3), ["en"], max_workers=2, fetch_fn=fake)}

    assert results["v001"] == ("OTHER", "none", "")   # one bad item degraded, not fatal
    assert results["v000"] == ("OK", "en", "ok")
    assert results["v002"] == ("OK", "en", "ok")


def test_empty_videos_yields_nothing() -> None:
    assert list(stream_transcripts([], ["en"], fetch_fn=lambda v, l: ("OK", "en", ""))) == []


def test_env_default_workers(monkeypatch) -> None:
    monkeypatch.setenv("YT_SCRAPER_MAX_WORKERS", "2")
    # max_workers=None -> resolved from env; just assert it runs and completes.
    out = list(stream_transcripts(_videos(4), ["en"], fetch_fn=lambda v, l: ("OK", "en", "")))
    assert len(out) == 4
