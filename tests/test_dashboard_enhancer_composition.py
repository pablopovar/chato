from __future__ import annotations

from nerdo_api import dashboard_domain
from nerdo_api.chat_trace_ui import enhance_dashboard_page as enhance_trace_page
from nerdo_api.document_foundry_ui import enhance_dashboard_page as enhance_foundry_page
from nerdo_api.setup_review import enhance_domain_page as enhance_review_page
from nerdo_api.share_background import enhance_dashboard_page as enhance_share_background_page


def test_dashboard_enhancers_compose_in_runtime_order() -> None:
    page = enhance_review_page(dashboard_domain.DOMAIN_PAGE)
    page = enhance_foundry_page(page)
    page = enhance_share_background_page(page)
    page = enhance_trace_page(page)

    assert "Pre-activation review" in page
    assert "Crawl and conversion visibility" in page
    assert "Configuration" in page
    assert "Test chat" in page
    assert "Nerdo's Document Foundry" in page
    assert "Past chats" in page
    assert "Session Share" in page
    assert "shareBackgroundSettings" in page
    assert "Record full chat traces" in page
    assert "Download Trace" in page
    assert page.count("</head>") == 1
