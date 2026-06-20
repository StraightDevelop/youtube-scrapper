"""Companion summary.json writer (FR-8)."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Mapping

logger = logging.getLogger(__name__)


def write_summary(path: Path, summary: Mapping[str, object]) -> None:
    """Write run summary metadata as pretty-printed JSON.

    Args:
        path: Destination ``<handle>_<YYYYMMDD>.summary.json`` path.
        summary: Mapping of summary fields — ``channel``, ``scraped_at``,
            ``total_videos``, ``successful``, ``failed``, ``failures_by_status``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    logger.info("write_summary: path=%s", path)
