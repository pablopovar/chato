from __future__ import annotations

from dataclasses import replace

from nerdo_api.config import settings
from nerdo_api.share_namespace import (
    PUBLIC_APP_PREFIX,
    SHARE_ROUTE_PREFIX,
    public_app_base_url,
)


def test_share_routes_stay_under_existing_nerdo_namespace() -> None:
    assert PUBLIC_APP_PREFIX == "/nerdo"
    assert SHARE_ROUTE_PREFIX == "/nerdo/share"


def test_public_app_base_url_adds_prefix_once() -> None:
    bare = replace(
        settings,
        public_base_url="https://nerdo.povarchik.com",
    )
    prefixed = replace(
        settings,
        public_base_url="https://nerdo.povarchik.com/nerdo",
    )

    assert public_app_base_url(bare) == "https://nerdo.povarchik.com/nerdo"
    assert public_app_base_url(prefixed) == "https://nerdo.povarchik.com/nerdo"
