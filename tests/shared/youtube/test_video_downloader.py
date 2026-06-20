"""Characterization tests for shared/youtube/video_downloader.py.

Pure helpers (`friendly_download_error`, `_human_bytes`) are tested directly;
`download_video` is driven through a mocked ``YoutubeDL`` whose progress hooks
fire against a real ``tmp_path`` file (no network, no ffmpeg).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

import shared.youtube.video_downloader as vd
from shared.youtube.video_downloader import _human_bytes, friendly_download_error


# --------------------------------------------------------------------------- #
# friendly_download_error
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "message, expected",
    [
        ("ERROR: Private video", "This video is private and can't be downloaded."),
        ("This is members-only content", "This video is for channel members only and can't be downloaded."),
        ("This video is age restricted", "This video is age-restricted. YouTube blocks downloading it from this app."),
        ("Video blocked in your region", "This video is blocked in your region."),
        ("This video has been removed", "This video has been removed or is no longer available."),
        ("This live event has not started", "This is a live stream — try again after the broadcast ends."),
        ("Requested format is not available", "YouTube doesn't offer a downloadable file for this video."),
        ("some weird error", "some weird error"),
        ("", "Download failed for an unknown reason."),
    ],
)
def test_friendly_download_error(message: str, expected: str) -> None:
    assert friendly_download_error(Exception(message)) == expected


# --------------------------------------------------------------------------- #
# _human_bytes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "n, expected",
    [
        (512, "512 B"),
        (1536, "1.5 KB"),
        (5 * 1024 * 1024, "5.0 MB"),
        (3 * 1024 ** 3, "3.0 GB"),
        (2 * 1024 ** 4, "2.0 TB"),
    ],
)
def test_human_bytes(n: int, expected: str) -> None:
    assert _human_bytes(n) == expected


# --------------------------------------------------------------------------- #
# download_video — mocked YoutubeDL + real file
# --------------------------------------------------------------------------- #
def test_download_video_happy_path(tmp_path: Path, monkeypatch, make_fake_ydl) -> None:
    saved = tmp_path / "My Video [vid12345678].mp4"
    saved.write_bytes(b"data" * 10)   # 40 bytes
    hook_events = [
        {"status": "downloading", "total_bytes": 100, "downloaded_bytes": 40},
        {"status": "downloading", "downloaded_bytes": 10},   # no total -> 0.0 branch
        {"status": "finished", "info_dict": {"filepath": str(saved)}},
    ]
    info = {"title": "My Video", "duration": 123, "id": "vid12345678"}
    monkeypatch.setattr(vd, "YoutubeDL", make_fake_ydl(info=info, hook_events=hook_events))

    progress: list[tuple[float, str]] = []
    result = vd.download_video("vid12345678", tmp_path, "best", lambda f, l: progress.append((f, l)))

    assert result["path"] == str(saved.resolve())
    assert result["title"] == "My Video"
    assert result["duration_seconds"] == 123
    assert result["bytes"] == 40
    assert result["ext"] == "mp4"
    assert progress                       # callback fired at least once
    assert progress[-1] == (0.99, "Saving file…")


def test_download_video_missing_file_raises(tmp_path: Path, monkeypatch, make_fake_ydl) -> None:
    missing = tmp_path / "missing.mp4"
    hook_events = [{"status": "finished", "info_dict": {"filepath": str(missing)}}]
    monkeypatch.setattr(vd, "YoutubeDL", make_fake_ydl(info={"title": "x"}, hook_events=hook_events))
    with pytest.raises(FileNotFoundError):
        vd.download_video("vid12345678", tmp_path)   # no callback -> exercises None branches


def test_download_video_propagates_download_error(tmp_path: Path, monkeypatch, make_fake_ydl) -> None:
    monkeypatch.setattr(vd, "YoutubeDL", make_fake_ydl(exc=DownloadError("Private video")))
    with pytest.raises(DownloadError):
        vd.download_video("vid12345678", tmp_path)
