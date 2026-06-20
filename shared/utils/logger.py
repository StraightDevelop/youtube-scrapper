"""Shared logging configuration helpers for CLI apps."""
from __future__ import annotations

import logging
import sys


def configure_logging(level: int = logging.INFO) -> None:
    """Initialise the root logger for CLI use; emits ISO-style timestamps to stdout.

    Args:
        level: Standard ``logging`` level (default ``logging.INFO``). Use
            ``logging.DEBUG`` for verbose function entry/exit traces.
    """
    handler = logging.StreamHandler(stream=sys.stdout)
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
