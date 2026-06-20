"""Text post-processing helpers for transcript snippets."""
from __future__ import annotations

import re
from typing import Iterable, Mapping

_WS_RE = re.compile(r"\s+")


def clean_transcript_snippets(snippets: Iterable[Mapping[str, object]]) -> str:
    """Concatenate transcript snippets into a single, single-line, trimmed string.

    Args:
        snippets: Iterable of mappings each containing a ``"text"`` key — the
            shape returned by ``youtube_transcript_api`` ``Transcript.fetch()``.
    Returns:
        Whitespace-normalised, newline-free transcript text. Empty string when
        the input contains no usable text.
    """
    parts: list[str] = []
    for snippet in snippets:
        if not isinstance(snippet, Mapping):
            continue
        text = snippet.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
    joined = " ".join(parts)
    joined = joined.replace("\n", " ").replace("\r", " ")
    return _WS_RE.sub(" ", joined).strip()
