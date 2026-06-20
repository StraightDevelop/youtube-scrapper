"""Resume support: read previously-written video IDs and full records from JSONL (FR-7)."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


def iter_jsonl_records(path: Path) -> Iterator[dict]:
    """Yield each parsed JSON object from a ``.jsonl`` file.

    Malformed lines are logged and skipped — they will not abort iteration.
    Missing files yield nothing (not an error).

    Args:
        path: Path to the ``.jsonl`` file.
    Yields:
        ``dict`` records, one per non-empty, parseable line.
    """
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                logger.warning(
                    "iter_jsonl_records: malformed_line line=%d path=%s",
                    line_no, path,
                )
                continue
            if isinstance(obj, dict):
                yield obj


def collect_channel_history(
    output_dir: Path,
    channel_handle: str,
) -> tuple[list[dict], dict[str, str]]:
    """Merge every prior ``<handle>_*.jsonl`` file for a channel into a single view.

    Files are read in modification-time order (oldest first) so that a video
    that was retried in a later run overwrites its earlier record. This means
    a video previously marked ``NETWORK_ERROR`` and later fetched as ``OK``
    correctly carries the ``OK`` status forward.

    Args:
        output_dir: Directory containing prior ``.jsonl`` outputs.
        channel_handle: Sanitised handle (output of
            :func:`shared.youtube.url_validator.extract_channel_handle`).
    Returns:
        Tuple ``(records, status_by_id)``:

        * ``records`` — deduplicated list of full records (most recent per ID).
        * ``status_by_id`` — ``{video_id: status}`` mapping for fast partition.
    """
    if not output_dir.exists():
        return [], {}
    files = sorted(
        output_dir.glob(f"{channel_handle}_*.jsonl"),
        key=lambda p: p.stat().st_mtime,
    )
    by_id: dict[str, dict] = {}
    for path in files:
        for record in iter_jsonl_records(path):
            video_id = record.get("id")
            if isinstance(video_id, str) and video_id:
                by_id[video_id] = record
    records = list(by_id.values())
    status_by_id = {vid: str(rec.get("status", "")) for vid, rec in by_id.items()}
    logger.info(
        "collect_channel_history: handle=%s files=%d unique_ids=%d",
        channel_handle, len(files), len(by_id),
    )
    return records, status_by_id


def read_existing_video_ids(path: Path) -> set[str]:
    """Return the set of ``id`` values already present in a JSONL output file.

    Malformed lines are logged and skipped — they will not block resume.
    Missing files return an empty set (not an error) so the caller can use the
    same code path on first runs.

    Args:
        path: Path to the ``.jsonl`` file to inspect.
    Returns:
        Set of video ID strings.
    """
    if not path.exists():
        logger.debug("read_existing_video_ids: missing path=%s", path)
        return set()
    ids: set[str] = set()
    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                logger.warning(
                    "read_existing_video_ids: malformed_line line=%d path=%s",
                    line_no, path,
                )
                continue
            video_id = obj.get("id") if isinstance(obj, dict) else None
            if isinstance(video_id, str) and video_id:
                ids.add(video_id)
    logger.info("read_existing_video_ids: count=%d path=%s", len(ids), path)
    return ids
