"""Tests for shared/youtube/languages.py (Phase 3 — UX language picker)."""
from __future__ import annotations

from shared.youtube.languages import (
    COMMON_LANGUAGES,
    code_from_label,
    codes_from_labels,
    language_name,
    language_options,
    option_label,
)


def test_language_name() -> None:
    assert language_name("th") == "Thai"
    assert language_name("TH") == "Thai"          # case-insensitive
    assert language_name("xx") == "xx"            # unknown -> echo the code


def test_option_label() -> None:
    assert option_label("en") == "English (en)"


def test_language_options_order_and_size() -> None:
    opts = language_options()
    assert len(opts) == len(COMMON_LANGUAGES)
    assert opts[0] == "Thai (th)" and opts[1] == "English (en)"   # primary audience first


def test_code_from_label() -> None:
    assert code_from_label("Thai (th)") == "th"
    assert code_from_label("English (EN)") == "en"   # normalized to lower
    assert code_from_label("en") == "en"             # raw code passes through


def test_codes_from_labels_dedupes_and_orders() -> None:
    assert codes_from_labels(["Thai (th)", "English (en)", "Thai (th)"]) == ["th", "en"]
    assert codes_from_labels([]) == []
