"""Append-only JSONL writer (FR-7/FR-8)."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Mapping

logger = logging.getLogger(__name__)


def append_jsonl(path: Path, record: Mapping[str, object]) -> None:
    """Append a single record as a compact JSON line to ``path`` (UTF-8).

    Creates parent directories on first call. Each line is flushed before the
    file handle is released so that a Ctrl+C between iterations does not
    truncate the most recently written record (resume relies on this).

    Args:
        path: Destination ``.jsonl`` file.
        record: JSON-serialisable mapping. Non-ASCII characters (Thai, etc.)
            are written verbatim.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
        fh.write("\n")
        fh.flush()
    logger.debug("append_jsonl: id=%s path=%s bytes=%d", record.get("id"), path, len(line))
