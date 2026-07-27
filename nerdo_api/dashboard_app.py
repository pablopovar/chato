from __future__ import annotations

from dataclasses import replace

from .dashboard_domain import install_dashboard_domain
from .main import app
from .share_namespace import install_share_namespace, public_app_base_url


if not getattr(app.state, "dashboard_domain_installed", False):
    dashboard_settings = replace(
        app.state.settings,
        public_base_url=public_app_base_url(app.state.settings),
    )
    install_dashboard_domain(app, dashboard_settings)
    app.state.dashboard_domain_installed = True

if not getattr(app.state, "share_sessions_installed", False):
    install_share_namespace(app, app.state.settings)
    app.state.share_sessions_installed = True
