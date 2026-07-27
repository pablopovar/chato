from __future__ import annotations

from .dashboard_domain import install_dashboard_domain
from .main import app


if not getattr(app.state, "dashboard_domain_installed", False):
    install_dashboard_domain(app, app.state.settings)
    app.state.dashboard_domain_installed = True
