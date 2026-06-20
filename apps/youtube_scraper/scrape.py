"""YouTube channel transcript scraper — CLI entry point.

Usage::

    python apps/youtube_scraper/scrape.py <channel_url> [options]

Implements FR-1 through FR-8 from the bundled PRD: URL validation, flat
channel listing via ``yt-dlp``, transcript fetching via ``yt-dlp``'s signed
timedtext URLs with ``th, en`` language priority, per-video status
classification, configurable inter-request delay, append-only JSONL output
with resume, and a companion summary file.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Make the monorepo's ``shared/`` package importable regardless of the
# directory the script is invoked from. This keeps the CLI runnable as a
# plain script (per the PRD) while preserving the ``apps/`` + ``shared/``
# split mandated by CLAUDE.md.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.io.jsonl_writer import append_jsonl  # noqa: E402
from shared.io.resume_reader import read_existing_video_ids  # noqa: E402
from shared.io.summary_writer import write_summary  # noqa: E402
from shared.utils.logger import configure_logging  # noqa: E402
from shared.youtube.channel_extractor import VideoMeta, list_channel_videos  # noqa: E402
from shared.youtube.concurrent_fetch import stream_transcripts  # noqa: E402
from shared.youtube.transcript_fetcher import friendly_transcript_status  # noqa: E402
from shared.youtube.url_validator import (  # noqa: E402
    extract_channel_handle,
    validate_channel_url,
)

DEFAULT_DELAY_SECONDS: float = 1.0
DEFAULT_LANGUAGES: str = "th,en"
DEFAULT_OUTPUT_DIR: str = "output"
FAILURE_STATUSES: tuple[str, ...] = ("NO_CAPTIONS", "DISABLED", "NETWORK_ERROR", "OTHER")

logger = logging.getLogger("scrape")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the CLI arguments documented in the PRD ``cli_interface`` block.

    Args:
        argv: Optional explicit argv list (used by tests). ``None`` falls
            through to :data:`sys.argv`.
    Returns:
        Parsed :class:`argparse.Namespace`.
    """
    parser = argparse.ArgumentParser(
        prog="scrape.py",
        description=(
            "Scrape every public video's title and transcript from a YouTube channel "
            "into a single JSONL file."
        ),
    )
    parser.add_argument(
        "channel_url",
        help="YouTube channel URL — @handle, /channel/UC..., /c/name, or /user/name.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        metavar="SECONDS",
        help=(
            f"Politeness pause before each transcript request in seconds "
            f"(default: {DEFAULT_DELAY_SECONDS}). Applied per worker."
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Number of transcripts to fetch in parallel. Default: the "
            "YT_SCRAPER_MAX_WORKERS env var, else 4. Keep modest to avoid "
            "YouTube rate-limiting (HTTP 429)."
        ),
    )
    parser.add_argument(
        "--languages",
        default=DEFAULT_LANGUAGES,
        metavar="LANGS",
        help=f"Comma-separated language codes in priority order (default: {DEFAULT_LANGUAGES}).",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        metavar="PATH",
        help=f"Output directory (default: ./{DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore any existing output file and start fresh.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Emit DEBUG-level logs (function entry/exit, transcript timings).",
    )
    return parser.parse_args(argv)


def parse_languages(raw: str) -> list[str]:
    """Split a comma-separated ``--languages`` value into a clean priority list.

    Raises:
        ValueError: when no usable language codes remain after splitting.
    """
    languages = [s.strip().lower() for s in raw.split(",") if s.strip()]
    if not languages:
        raise ValueError("--languages must contain at least one language code")
    return languages


def build_output_paths(output_dir: Path, channel_handle: str) -> tuple[Path, Path]:
    """Return ``(jsonl_path, summary_path)`` named ``<handle>_<YYYYMMDD>.*``.

    The date stamp is ``YYYYMMDD`` in the local timezone — matches the
    PRD ``filename_format``.
    """
    today = datetime.now().strftime("%Y%m%d")
    jsonl_path = output_dir / f"{channel_handle}_{today}.jsonl"
    summary_path = output_dir / f"{channel_handle}_{today}.summary.json"
    return jsonl_path, summary_path


def confirm_resume(existing_count: int) -> bool:
    """Prompt the user whether to resume from an existing JSONL output.

    Defaults to ``True`` when input is unavailable (e.g. piped stdin).

    Args:
        existing_count: Number of video IDs already present in the file.
    Returns:
        ``True`` to resume (skip already-recorded videos), ``False`` to
        start fresh (the caller deletes the file).
    """
    if existing_count <= 0:
        return True
    prompt = f"Found {existing_count} existing videos. Resume? [Y/n] "
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        # Non-interactive invocation — default to resume.
        return True
    return answer in ("", "y", "yes")


def build_record(
    video: VideoMeta,
    status: str,
    language: str,
    transcript: str,
) -> dict[str, object]:
    """Assemble a single JSONL record matching the FR-8 schema."""
    return {
        "id": video["id"],
        "title": video["title"],
        "url": video["url"],
        "upload_date": video["upload_date"],
        "duration_seconds": video["duration_seconds"],
        "language": language,
        "status": status,
        "transcript": transcript,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code.

    Args:
        argv: Optional explicit argv list (used by tests).
    Returns:
        ``0`` on success, ``2`` on argument validation errors,
        ``1`` on unrecoverable runtime errors.
    """
    args = parse_args(argv)
    configure_logging(level=logging.DEBUG if args.verbose else logging.INFO)
    logger.debug("main: args=%s", vars(args))

    try:
        canonical_url = validate_channel_url(args.channel_url)
        languages = parse_languages(args.languages)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir).expanduser().resolve()
    handle = extract_channel_handle(canonical_url)
    jsonl_path, summary_path = build_output_paths(output_dir, handle)

    print(f"Channel : {canonical_url}")
    print(f"Output  : {jsonl_path}")
    print(f"Summary : {summary_path}")
    print(f"Languages priority: {', '.join(languages)} (then auto-generated fallback)")

    existing_ids: set[str] = set()
    if jsonl_path.exists():
        if args.no_resume:
            logger.info("main: removing existing output (--no-resume) path=%s", jsonl_path)
            jsonl_path.unlink()
        else:
            existing_ids = read_existing_video_ids(jsonl_path)
            if existing_ids and not confirm_resume(len(existing_ids)):
                logger.info("main: user declined resume; starting fresh")
                jsonl_path.unlink()
                existing_ids = set()

    print("Listing videos…")
    try:
        videos = list_channel_videos(canonical_url)
    except Exception as exc:  # noqa: BLE001 — surface yt-dlp failures clearly
        print(f"error: failed to list channel videos: {exc}", file=sys.stderr)
        return 1

    total = len(videos)
    print(f"Found {total} video(s) on the channel.")
    if total == 0:
        print("Nothing to do.")
        return 0

    successful = 0
    failures: dict[str, int] = {status: 0 for status in FAILURE_STATUSES}
    started = time.time()

    # Resume: fetch only what's missing. Concurrency means results arrive in
    # completion order (not channel order) — safe because the JSONL is keyed by id.
    to_fetch = [v for v in videos if v["id"] not in existing_ids]
    skipped = total - len(to_fetch)
    if skipped:
        print(f"Skipping {skipped} already-saved video(s) (resume).")
    fetch_total = len(to_fetch)
    completed = 0
    for video, status, language, transcript in stream_transcripts(
        to_fetch, languages, max_workers=args.workers, delay=args.delay,
    ):
        completed += 1
        record = build_record(video, status=status, language=language, transcript=transcript)
        append_jsonl(jsonl_path, record)
        if status == "OK":
            successful += 1
        else:
            failures[status] = failures.get(status, 0) + 1
        print(f"[{completed}/{fetch_total}] {status:<13} {video['title']}")
        if status != "OK":
            print(f"    ↳ {friendly_transcript_status(status)}")

    elapsed = time.time() - started
    failed_total = sum(failures.values())
    summary = {
        "channel": canonical_url,
        "channel_handle": handle,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "total_videos": total,
        "processed_this_run": total - skipped,
        "skipped_resume": skipped,
        "successful": successful,
        "failed": failed_total,
        "failures_by_status": failures,
        "elapsed_seconds": round(elapsed, 2),
        "output_file": str(jsonl_path),
    }
    write_summary(summary_path, summary)

    print()
    print("── Summary ──────────────────────────────")
    print(f"  total_videos        : {total}")
    print(f"  processed_this_run  : {total - skipped}")
    if skipped:
        print(f"  skipped_resume      : {skipped}")
    print(f"  successful          : {successful}")
    print(f"  failed              : {failed_total}")
    for status, count in failures.items():
        if count:
            print(f"    {status:<14}    : {count}")
    print(f"  elapsed             : {elapsed:.1f}s")
    print(f"  output              : {jsonl_path}")
    print(f"  summary             : {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
