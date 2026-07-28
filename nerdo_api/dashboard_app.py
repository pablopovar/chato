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
from .domain_approval import install_domain_approval, remove_duplicate_start_routes
from .domain_operations import install_domain_operations
from .main import app
from .review_changes import install_review_changes
from .review_changes_ui import (
    enhance_dashboard_page as enhance_review_changes_page,
    install_review_changes_dashboard,
)
from .setup_review import (
    enhance_domain_page as enhance_setup_review_domain,
    enhance_root_page as enhance_setup_review_root,
    install_setup_review,
)
from .share_background import (
    enhance_dashboard_page as enhance_share_background_page,
    enhance_session_page,
    install_share_background,
)


install_debug_configuration()
dashboard_domain.ROOT_PAGE = enhance_setup_review_root(dashboard_domain.ROOT_PAGE)
dashboard_domain.DOMAIN_PAGE = enhance_setup_review_domain(dashboard_domain.DOMAIN_PAGE)
dashboard_domain.DOMAIN_PAGE = enhance_review_changes_page(dashboard_domain.DOMAIN_PAGE)
dashboard_domain.DOMAIN_PAGE = enhance_foundry_page(dashboard_domain.DOMAIN_PAGE)
dashboard_domain.DOMAIN_PAGE = enhance_share_background_page(
    dashboard_domain.DOMAIN_PAGE
)
dashboard_domain.DOMAIN_PAGE = enhance_trace_page(dashboard_domain.DOMAIN_PAGE)
share_sessions.SESSION_PAGE = enhance_session_page(share_sessions.SESSION_PAGE)

from .share_namespace import install_share_namespace, public_app_base_url


if not getattr(app.state, "domain_approval_installed", False):
    install_domain_approval(app, app.state.settings, app.state.storage)

if not getattr(app.state, "domain_operations_installed", False):
    install_domain_operations(app, app.state.settings, app.state.storage)

if not getattr(app.state, "review_changes_installed", False):
    install_review_changes(app, app.state.settings, app.state.storage)

remove_duplicate_start_routes(app)

if not getattr(app.state, "setup_review_installed", False):
    install_setup_review(app, app.state.settings)

if not getattr(app.state, "review_changes_dashboard_installed", False):
    install_review_changes_dashboard(app, app.state.settings)

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
