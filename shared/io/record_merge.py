"""Merge and dedupe video records by ``id`` (resume + aggregation support).

Extracted from the web app so the merge-on-resume path and the multi-playlist
aggregation path share one implementation (rule 8 / DRY). Records are plain dicts
carrying a string ``"id"`` — the resume/dedup key. Records without a string ``id``
are skipped (they can't participate in id-keyed merge/dedup).
"""
from __future__ import annotations

import logging
from typing import Sequence

logger = logging.getLogger(__name__)


def merge_records(prior: Sequence[dict], fresh: Sequence[dict]) -> list[dict]:
    """Merge ``prior`` and freshly-fetched records, ``fresh`` overriding by id.

    Args:
        prior: Previously-stored records (kept in their original order).
        fresh: Newly-fetched records; an entry replaces a ``prior`` one with the
            same ``id`` in place, and brand-new ids are appended after.
    Returns:
        Merged list: prior order preserved (re-fetched ids swapped for the fresh
        record), then ids only seen in ``fresh``. Records lacking a string ``id``
        are dropped.
    """
    fresh_by_id = {r.get("id"): r for r in fresh if isinstance(r.get("id"), str)}
    merged: list[dict] = []
    seen: set[str] = set()
    for record in prior:
        rid = record.get("id")
        if not isinstance(rid, str):
            continue
        merged.append(fresh_by_id[rid] if rid in fresh_by_id else record)
        seen.add(rid)
    for record in fresh:
        rid = record.get("id")
        if isinstance(rid, str) and rid not in seen:
            merged.append(record)
            seen.add(rid)
    logger.debug("merge_records: prior=%d fresh=%d merged=%d", len(prior), len(fresh), len(merged))
    return merged


def dedupe_by_id(records: Sequence[dict]) -> list[dict]:
    """Return ``records`` deduplicated by ``id`` — last occurrence wins.

    Args:
        records: Records to dedupe; entries without a string ``id`` are skipped.
    Returns:
        One record per id, in first-seen id order, holding the last value seen.
    """
    by_id: dict[str, dict] = {}
    for record in records:
        rid = record.get("id")
        if isinstance(rid, str):
            by_id[rid] = record
    return list(by_id.values())
