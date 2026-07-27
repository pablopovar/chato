from __future__ import annotations

from dataclasses import replace

from fastapi import FastAPI

from nerdo_api.config import settings
from nerdo_api.share_namespace import (
    INTERNAL_SHARE_ROUTE_PREFIX,
    PUBLIC_APP_PREFIX,
    PUBLIC_SHARE_ROUTE_PREFIX,
    install_share_namespace,
    public_app_base_url,
)


def test_share_public_url_stays_under_existing_nerdo_namespace() -> None:
    assert PUBLIC_APP_PREFIX == "/nerdo"
    assert PUBLIC_SHARE_ROUTE_PREFIX == "/nerdo/share"


def test_share_fastapi_routes_use_proxy_stripped_path(tmp_path) -> None:
    app = FastAPI()
    configured = replace(
        settings,
        database_path=tmp_path / "gateway.sqlite3",
    )

    install_share_namespace(app, configured)

    paths = {route.path for route in app.routes}
    assert INTERNAL_SHARE_ROUTE_PREFIX == "/share"
    assert "/share/{claim_token}" in paths
    assert "/share/session/{session_id}" in paths
    assert "/nerdo/share/{claim_token}" not in paths


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
