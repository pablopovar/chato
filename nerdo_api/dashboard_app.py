from __future__ import annotations

from dataclasses import replace

from . import dashboard_domain, share_sessions
from .chat_trace_ui import (
    enhance_dashboard_page as enhance_trace_page,
    install_debug_configuration,
    install_trace_download,
)
from .document_foundry import install_document_foundry
from .document_foundry_ui import enhance_dashboard_page as enhance_foundry_page
from .domain_approval import install_domain_approval
from .domain_operations import install_domain_operations
from .main import app
from .share_background import (
    enhance_dashboard_page as enhance_share_background_page,
    enhance_session_page,
    install_share_background,
)


install_debug_configuration()
dashboard_domain.DOMAIN_PAGE = enhance_foundry_page(dashboard_domain.DOMAIN_PAGE)
dashboard_domain.DOMAIN_PAGE = enhance_share_background_page(
    dashboard_domain.DOMAIN_PAGE
)
dashboard_domain.DOMAIN_PAGE = enhance_trace_page(dashboard_domain.DOMAIN_PAGE)
share_sessions.SESSION_PAGE = enhance_session_page(share_sessions.SESSION_PAGE)

from .share_namespace import install_share_namespace, public_app_base_url


if not getattr(app.state, "domain_operations_installed", False):
    install_domain_operations(app, app.state.settings, app.state.storage)

if not getattr(app.state, "domain_approval_installed", False):
    install_domain_approval(app, app.state.settings, app.state.storage)

if not getattr(app.state, "dashboard_domain_installed", False):
    dashboard_settings = replace(
        app.state.settings,
        public_base_url=public_app_base_url(app.state.settings),
    )
    dashboard_domain.install_dashboard_domain(app, dashboard_settings)
    app.state.dashboard_domain_installed = True

if not getattr(app.state, "chat_trace_download_installed", False):
    install_trace_download(app, app.state.settings)
    app.state.chat_trace_download_installed = True

if not getattr(app.state, "share_sessions_installed", False):
    install_share_namespace(app, app.state.settings)
    app.state.share_sessions_installed = True

if not getattr(app.state, "share_background_installed", False):
    install_share_background(app, app.state.settings)
    app.state.share_background_installed = True

if not getattr(app.state, "document_foundry_installed", False):
    install_document_foundry(app)
    app.state.document_foundry_installed = True
