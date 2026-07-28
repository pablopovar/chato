from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI

from app.services.setup_report import update_setup_report_summary
from nerdo_api.setup_review import enhance_root_page, install_setup_review
from nerdo_mail.review_ready import ReviewReadyNotification, ReviewReadyNotifier


def test_setup_report_summary_update_preserves_nerdo_section(tmp_path: Path) -> None:
    report = tmp_path / "setup-report.md"
    report.write_text(
        "# Website Setup Report\n\n"
        "## Nerdo — Data Processing Report\n\n"
        "Pages retrieved: 20\n\n"
        "## Chato — Corpus Summary\n\n"
        "### Old summary\n\nOld content.\n\n"
        "## Review Status\n\nReady.\n",
        encoding="utf-8",
    )

    update_setup_report_summary(
        report,
        "# New Organization\n\n## Business Overview\n\nNew content.",
    )

    text = report.read_text(encoding="utf-8")
    assert "Pages retrieved: 20" in text
    assert "Old content" not in text
    assert "### New Organization" in text
    assert "### Business Overview" in text
    assert "## Review Status" in text


def test_dashboard_root_enhancer_uses_domain_href_and_status() -> None:
    page = (
        "<p>Select a domain to configure it and review its conversations.</p>"
        "<script>async function load(){old}\n"
        "load().catch(e=>root.innerHTML=e.message);</script>"
    )

    enhanced = enhance_root_page(page)

    assert "completed intake for review" in enhanced
    assert "x.href" in enhanced
    assert "x.status" in enhanced
    assert "No domains or intakes found" in enhanced


def test_setup_review_routes_are_registered() -> None:
    app = FastAPI()
    settings = SimpleNamespace(
        core_base_url="http://core",
        core_admin_token="admin",
        operator_token="operator",
        request_timeout_seconds=30.0,
    )

    install_setup_review(app, settings)

    paths = {route.path for route in app.routes}
    assert "/dashboard/api/domains" in paths
    assert "/dashboard/api/reviews/{intake_id}" in paths
    assert "/dashboard/api/reviews/{intake_id}/summary" in paths
    assert "/dashboard/api/reviews/{intake_id}/activate" in paths
    assert "/dashboard/api/reviews/{intake_id}/download" in paths
    assert "/dashboard/reviews/{_intake_id}" in paths


def test_reviewer_email_contains_report_and_direct_review_link() -> None:
    notifier = object.__new__(ReviewReadyNotifier)
    notifier.dashboard_url = "https://chato.example/dashboard/"
    review = ReviewReadyNotification(
        review_key="review-1",
        intake_id="intake-1",
        domain="example.com",
        owner_email="owner@example.com",
        document_count=20,
        updated_at="2026-07-28T04:42:23+00:00",
    )

    body = notifier._body(
        review,
        "# Website Setup Report\n\n## Nerdo — Data Processing Report",
        reviewer=True,
    )

    assert "## Nerdo — Data Processing Report" in body
    assert "https://chato.example/dashboard/reviews/intake-1" in body
    assert "activate example.com" in body
