from __future__ import annotations

from .dashboard_domain import install_dashboard_domain
from .main import app
from .share_sessions import install_share_sessions


if not getattr(app.state, "dashboard_domain_installed", False):
    install_dashboard_domain(app, app.state.settings)
    app.state.dashboard_domain_installed = True

if not getattr(app.state, "share_sessions_installed", False):
    install_share_sessions(app, app.state.settings)
    app.state.share_sessions_installed = True
