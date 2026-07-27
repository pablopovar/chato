from __future__ import annotations

from .dashboard import install_dashboard
from .dashboard_chat import install_dashboard_chat
from .main import app


if not getattr(app.state, "domain_dashboard_installed", False):
    install_dashboard(app, app.state.settings, app.state.storage)
    app.state.domain_dashboard_installed = True

if not getattr(app.state, "dashboard_chat_installed", False):
    install_dashboard_chat(app, app.state.settings)
    app.state.dashboard_chat_installed = True
