from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from nerdo_api.share_sessions import ShareStore


def test_share_link_can_be_claimed_only_once(tmp_path) -> None:
    store = ShareStore(tmp_path / "gateway.sqlite3")
    created, claim_token = store.create("example.com", 48)
    claimed_at = datetime(2026, 7, 27, 20, 0, tzinfo=timezone.utc)

    state, claimed, access_token = store.claim(
        claim_token,
        now=claimed_at,
    )

    assert state == "claimed"
    assert claimed is not None
    assert access_token
    assert claimed["id"] == created["id"]
    assert claimed["expires_at"] == (
        claimed_at + timedelta(hours=48)
    ).isoformat()

    second_state, second_record, second_access = store.claim(
        claim_token,
        now=claimed_at + timedelta(minutes=1),
    )

    assert second_state == "used"
    assert second_record is not None
    assert second_access is None


def test_claimed_session_is_bound_to_access_token_and_expiry(tmp_path) -> None:
    store = ShareStore(tmp_path / "gateway.sqlite3")
    created, claim_token = store.create("example.com", 2)
    claimed_at = datetime(2026, 7, 27, 20, 0, tzinfo=timezone.utc)
    _state, _record, access_token = store.claim(
        claim_token,
        now=claimed_at,
    )
    assert access_token

    assert store.verify(
        created["id"],
        access_token,
        now=claimed_at + timedelta(hours=1),
    ) is not None
    assert store.verify(
        created["id"],
        "another-browser-token",
        now=claimed_at + timedelta(hours=1),
    ) is None
    assert store.verify(
        created["id"],
        access_token,
        now=claimed_at + timedelta(hours=2),
    ) is None


def test_share_duration_must_be_positive_and_bounded(tmp_path) -> None:
    store = ShareStore(tmp_path / "gateway.sqlite3")

    with pytest.raises(ValueError):
        store.create("example.com", 0)

    with pytest.raises(ValueError):
        store.create("example.com", 8761)
