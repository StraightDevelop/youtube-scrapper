"""Curated language metadata for the caption-language picker (UX — Phase 3).

Lets the web UI show human language names instead of raw ISO codes, while the rest
of the pipeline (``parse_languages`` → ``_pick_track``) keeps working in codes. The
picker shows labels like ``"Thai (th)"``; helpers convert selections back to codes.
"""
from __future__ import annotations

import re
from typing import Iterable

# (code, English name) — ordered for the picker. Thai + English lead because this
# tool's primary audience scrapes Thai/English channels.
COMMON_LANGUAGES: list[tuple[str, str]] = [
    ("th", "Thai"),
    ("en", "English"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
    ("zh", "Chinese"),
    ("es", "Spanish"),
    ("fr", "French"),
    ("de", "German"),
    ("pt", "Portuguese"),
    ("ru", "Russian"),
    ("hi", "Hindi"),
    ("ar", "Arabic"),
    ("id", "Indonesian"),
    ("vi", "Vietnamese"),
]
_CODE_TO_NAME: dict[str, str] = dict(COMMON_LANGUAGES)
_TRAILING_CODE_RE = re.compile(r"\(([A-Za-z-]+)\)\s*$")  # the "(code)" suffix of a label


def language_name(code: str) -> str:
    """Human name for an ISO code, or the code itself when not in the curated set.

    Args:
        code: ISO language code (case-insensitive), e.g. ``"th"``.
    Returns:
        The English language name (``"Thai"``) or the original code if unknown.
    """
    return _CODE_TO_NAME.get(code.lower(), code)


def option_label(code: str) -> str:
    """Picker label for a code, e.g. ``"Thai (th)"`` (code lower-cased)."""
    return f"{language_name(code)} ({code.lower()})"


def language_options() -> list[str]:
    """All picker labels in curated order (for an ``st.multiselect``)."""
    return [option_label(code) for code, _ in COMMON_LANGUAGES]


def code_from_label(label: str) -> str:
    """Extract the code from a picker label, tolerating a raw code.

    Args:
        label: ``"Thai (th)"`` (from the picker) or a bare code like ``"en"``.
    Returns:
        The lower-cased code (``"th"`` / ``"en"``).
    """
    match = _TRAILING_CODE_RE.search(label)
    return (match.group(1) if match else label).strip().lower()


def codes_from_labels(labels: Iterable[str]) -> list[str]:
    """Convert selected picker labels to an ordered, de-duplicated list of codes.

    Args:
        labels: Selected labels (or raw codes) in priority order.
    Returns:
        Codes in first-seen order with duplicates and blanks removed.
    """
    codes: list[str] = []
    for label in labels:
        code = code_from_label(label)
        if code and code not in codes:
            codes.append(code)
    return codes
