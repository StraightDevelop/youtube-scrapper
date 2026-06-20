"""Concurrent transcript fetching (Phase 2b).

Parallelizes the blocking, I/O-bound :func:`shared.youtube.transcript_fetcher.
fetch_transcript` calls across a bounded thread pool, **streaming** each result as
it completes so callers keep writing JSONL and updating progress incrementally
(crash-safe resume is preserved — nothing is buffered for the whole run).

Posture is deliberately conservative: a small worker cap plus an optional per-task
politeness delay keeps the request rate low enough to avoid YouTube's HTTP 429
gating. A 429 that slips through is still surfaced as ``NETWORK_ERROR`` by
``fetch_transcript`` and retried on the next resume run.

This module lives in ``shared/`` and knows nothing about the CLI or Streamlit — it
takes plain data and a callback-free generator interface (rule 7).
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterator, Mapping, Sequence

from shared.youtube.transcript_fetcher import Status, fetch_transcript

logger = logging.getLogger(__name__)

_ENV_MAX_WORKERS = "YT_SCRAPER_MAX_WORKERS"
_DEFAULT_MAX_WORKERS = 4

# (status, language_label, transcript_text) — mirrors fetch_transcript's return.
FetchResult = tuple[Status, str, str]
FetchFn = Callable[[str, list[str]], FetchResult]


def resolve_max_workers(max_workers: int | None = None) -> int:
    """Resolve the worker count from an explicit value, then env, then default.

    Args:
        max_workers: Explicit override. When ``None``, read ``YT_SCRAPER_MAX_WORKERS``
            from the environment (rule 12 — no hardcoded constants), else fall back
            to :data:`_DEFAULT_MAX_WORKERS`.
    Returns:
        A worker count clamped to ``>= 1`` (``ThreadPoolExecutor`` rejects 0).
    """
    if max_workers is None:
        raw = os.environ.get(_ENV_MAX_WORKERS, "")
        try:
            max_workers = int(raw) if raw else _DEFAULT_MAX_WORKERS
        except ValueError:
            logger.warning("resolve_max_workers: bad %s=%r — using default", _ENV_MAX_WORKERS, raw)
            max_workers = _DEFAULT_MAX_WORKERS
    return max(1, max_workers)


def stream_transcripts(
    videos: Sequence[Mapping[str, object]],
    languages: list[str],
    *,
    max_workers: int | None = None,
    delay: float = 0.0,
    fetch_fn: FetchFn = fetch_transcript,
) -> Iterator[tuple[Mapping[str, object], Status, str, str]]:
    """Fetch transcripts concurrently, yielding each result as it COMPLETES.

    Args:
        videos: Video metadata mappings; each must carry an ``"id"`` key. Yielded
            back verbatim so callers can build their record without re-lookup.
        languages: Preferred language codes in priority order (passed through to
            ``fetch_fn`` unchanged).
        max_workers: Thread-pool size; see :func:`resolve_max_workers`. Kept small
            by default to stay under YouTube's rate gate.
        delay: Per-task politeness pause (seconds) applied *before* each fetch, so
            the effective request rate is bounded even at full concurrency. Reuses
            the existing CLI ``--delay`` / UI "wait between videos" value.
        fetch_fn: Injectable fetcher (defaults to the real ``fetch_transcript``);
            tests pass a fake to avoid the network.
    Yields:
        ``(video, status, language_label, transcript_text)`` tuples in **completion
        order** (not input order). Safe for the callers because resume dedups by
        ``id`` and the run summary is order-independent.
    """
    if not videos:
        return
    workers = resolve_max_workers(max_workers)
    logger.info("stream_transcripts: start count=%d workers=%d delay=%.2f", len(videos), workers, delay)
    started = time.time()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_fetch_one, video, languages, delay, fetch_fn): video
            for video in videos
        }
        for future in as_completed(futures):
            video = futures[future]
            status, language, transcript = future.result()  # _fetch_one never raises
            yield video, status, language, transcript

    logger.info(
        "stream_transcripts: done count=%d elapsed=%.2fs", len(videos), time.time() - started,
    )


def _fetch_one(
    video: Mapping[str, object],
    languages: list[str],
    delay: float,
    fetch_fn: FetchFn,
) -> FetchResult:
    """Fetch a single transcript inside a worker thread; never raises.

    Args:
        video: Video mapping carrying ``"id"``.
        languages: Preferred language codes (passed to ``fetch_fn``).
        delay: Politeness pause applied before the fetch (``0`` disables it).
        fetch_fn: The fetcher to invoke.
    Returns:
        ``fetch_fn``'s result, or ``("OTHER", "none", "")`` if it raises — a single
        bad item must never tear down the whole pool.
    """
    if delay > 0:
        time.sleep(delay)
    video_id = str(video.get("id", ""))
    try:
        return fetch_fn(video_id, languages)
    except Exception as exc:  # noqa: BLE001 — defensive: isolate one bad item
        logger.warning("_fetch_one: unexpected error id=%s err=%s", video_id, exc)
        return "OTHER", "none", ""
