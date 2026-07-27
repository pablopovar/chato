from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi import HTTPException

from nerdo_api.share_background import (
    _background_path,
    _metadata,
    _remove_background,
    _store_background,
    enhance_dashboard_page,
    enhance_session_page,
)


ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def prepare_domain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    users = tmp_path / "users"
    domain_dir = users / "owner" / "example.com"
    domain_dir.mkdir(parents=True)
    (domain_dir / "nerdo.json").write_text(
        '{"domain":"example.com","enabled":true}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("NERDO_USERS_DIR", str(users))
    monkeypatch.setenv("NERDO_SHARE_BACKGROUND_MAX_BYTES", "20000000")
    return domain_dir


def test_domain_background_can_be_stored_replaced_and_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_domain(tmp_path, monkeypatch)

    stored = _store_background("EXAMPLE.COM.", "homepage.png", ONE_PIXEL_PNG)

    assert stored["configured"] is True
    assert stored["filename"] == "homepage.png"
    assert stored["content_type"] == "image/png"
    assert _background_path("example.com") is not None

    removed = _remove_background("example.com")

    assert removed is True
    assert _metadata("example.com")["configured"] is False


def test_background_rejects_non_image_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_domain(tmp_path, monkeypatch)

    with pytest.raises(HTTPException) as error:
        _store_background("example.com", "fake.png", b"not an image")

    assert error.value.status_code == 400


def test_dashboard_enhancement_adds_upload_and_preview_controls() -> None:
    enhanced = enhance_dashboard_page("<html><head></head><body></body></html>")

    assert "shareBackgroundSettings" in enhanced
    assert "Upload or replace" in enhanced
    assert "share-bg-mock" in enhanced


def test_shared_session_enhancement_positions_chat_over_background() -> None:
    enhanced = enhance_session_page("<html><head></head><body></body></html>")

    assert "siteBackgroundImage" in enhanced
    assert "with-site-background" in enhanced
    assert "/background" in enhanced
