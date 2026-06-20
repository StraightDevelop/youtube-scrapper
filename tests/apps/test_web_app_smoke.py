"""Smoke test for the Streamlit web app (Phase 3).

The first automated test for `apps/youtube_scraper_web/app.py`: load the page with
no input and assert it renders without raising. No network is hit on the empty
state (yt-dlp only runs once a URL is entered). `apps/` stays outside the coverage
gate (consistent with earlier phases); this just guards against import/render
regressions in the UI wiring.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_APP = Path(__file__).resolve().parents[2] / "apps" / "youtube_scraper_web" / "app.py"


@pytest.mark.slow   # opt-in: keeps the core suite Streamlit-free. ~0.7s steady-state
                    # (a fresh env pays a one-time ~90s streamlit/pandas .pyc compile).
def test_web_app_renders_empty_state_without_error() -> None:
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    app = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not app.exception                      # page rendered cleanly
    # The language picker (Phase 3) should be present in the sidebar.
    assert any("Caption languages" in (ms.label or "") for ms in app.multiselect)
    # Regression guard: each tab must show a submit button on the empty state so a
    # pasted URL is actionable without pressing Enter (the "no button" bug).
    labels = [b.label for b in app.button]
    assert any("Look up this link" in lbl for lbl in labels), labels
    assert any("Find this video" in lbl for lbl in labels), labels
