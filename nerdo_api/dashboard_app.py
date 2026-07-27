from __future__ import annotations

from dataclasses import replace

from . import dashboard_domain, share_sessions
from .main import app
from .share_background import (
    enhance_dashboard_page,
    enhance_session_page,
    install_share_background,
)


dashboard_domain.DOMAIN_PAGE = enhance_dashboard_page(dashboard_domain.DOMAIN_PAGE)
share_sessions.SESSION_PAGE = enhance_session_page(share_sessions.SESSION_PAGE)

from .share_namespace import install_share_namespace, public_app_base_url


if not getattr(app.state, "dashboard_domain_installed", False):
    dashboard_settings = replace(
        app.state.settings,
        public_base_url=public_app_base_url(app.state.settings),
    )
    dashboard_domain.install_dashboard_domain(app, dashboard_settings)
    app.state.dashboard_domain_installed = True

if not getattr(app.state, "share_sessions_installed", False):
    install_share_namespace(app, app.state.settings)
    app.state.share_sessions_installed = True

if not getattr(app.state, "share_background_installed", False):
    install_share_background(app, app.state.settings)
    app.state.share_background_installed = True
