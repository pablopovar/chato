from __future__ import annotations

from .dashboard import install_dashboard
from .main import app


if not getattr(app.state, "domain_dashboard_installed", False):
    install_dashboard(app, app.state.settings, app.state.storage)
    app.state.domain_dashboard_installed = True
