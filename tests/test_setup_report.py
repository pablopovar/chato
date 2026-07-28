from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import interpreter
from app.services.setup_report import build_setup_report
from nerdo_mail.review_ready import ReviewReadyNotification, ReviewReadyNotifier


FINAL_SUMMARY = """# Example Organization

## Business Overview

- Organization type: Museum
- Primary language: English
- Primary location: New York

## About the Business

Example summary.

## Key Features

- Feature

## Competitive Advantage

- Distinctive feature

## Target Customers

- Visitors

## Geographic Focus

New York.

## Suggested Topics

- History

## Suggested Keywords

- example museum

## Suggested Visitor Questions

- What is it?

## Data Gaps and Uncertainties

None identified.

## Sources

- https://example.com/about
"""


def test_chato_reads_every_canonical_page_before_synthesis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = tmp_path / "pages"
    pages.mkdir()
    markers = ["FIRST-PAGE-EVIDENCE", "SECOND-PAGE-EVIDENCE", "THIRD-PAGE-EVIDENCE"]
    for index, marker in enumerate(markers):
        (pages / f"page-{index}.md").write_text(
            f"---\nsource_url: https://example.com/{index}\n---\n# Page {index}\n\n"
            + marker
            + "\n"
            + ("material " * 8_000),
            encoding="utf-8",
        )

    evidence_inputs: list[str] = []

    def fake_completion(messages, **_kwargs):
        if messages[0]["content"] == interpreter.EVIDENCE_SYSTEM_PROMPT:
            evidence_inputs.append(messages[1]["content"])
            return "## Identity and organization type\nMuseum\n\n## Source URLs\nhttps://example.com"
        assert messages[0]["content"] == interpreter.SYSTEM_PROMPT
        return FINAL_SUMMARY

    monkeypatch.setattr(interpreter, "chat_completion", fake_completion)
    output = tmp_path / "chato-summary.md"

    interpreter.interpret("example.com", pages, output)

    supplied = "\n".join(evidence_inputs)
    assert len(evidence_inputs) >= 2
    for marker in markers:
        assert marker in supplied
    assert output.read_text(encoding="utf-8").startswith("# Example Organization")


def test_chato_summary_failure_is_not_replaced_with_source_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "about.md").write_text(
        "---\nsource_url: https://example.com/about\n---\n# About\n\nEvidence.",
        encoding="utf-8",
    )

    def fail(*_args, **_kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(interpreter, "chat_completion", fail)
    output = tmp_path / "chato-summary.md"

    with pytest.raises(RuntimeError, match="model unavailable"):
        interpreter.interpret("example.com", pages, output)
    assert not output.exists()


def test_setup_report_separates_processing_from_chato_summary(
    tmp_path: Path,
) -> None:
    summary = tmp_path / "chato-summary.md"
    summary.write_text(FINAL_SUMMARY, encoding="utf-8")
    report = tmp_path / "setup-report.md"

    build_setup_report(
        domain="example.com",
        website_url="https://example.com",
        owner_email="owner@example.com",
        started_at="2026-07-28T00:00:00+00:00",
        completed_at="2026-07-28T00:05:00+00:00",
        crawl_result=SimpleNamespace(
            attempts=12,
            pages=[1, 2, 3],
            skipped_pages=2,
            total_bytes=4096,
            stop_reason="frontier-exhausted",
            manifest_path="/private/crawl.json",
        ),
        clean_result=SimpleNamespace(
            documents=[1, 2, 3],
            canonical_documents=[1, 2],
            duplicate_count=1,
            discarded_count=0,
            report_path="/private/cleaning.json",
            duplicates_path="/private/duplicates.json",
        ),
        index_result=SimpleNamespace(
            document_count=2,
            chunk_count=7,
            fts5_enabled=True,
            manifest_path="/private/index.json",
        ),
        chato_summary_path=summary,
        report_path=report,
    )

    text = report.read_text(encoding="utf-8")
    assert "## Nerdo — Data Processing Report" in text
    assert "## Chato — Corpus Summary" in text
    assert "### Business Overview" in text
    assert "Pages retrieved: 3" in text
    assert "Canonical Markdown documents: 2" in text
    assert "/private/" not in text


def test_owner_report_does_not_expose_activation_command() -> None:
    notifier = object.__new__(ReviewReadyNotifier)
    notifier.dashboard_url = "https://example.com/dashboard/"
    review = ReviewReadyNotification(
        review_key="review-1",
        intake_id="intake-1",
        domain="example.com",
        owner_email="owner@example.com",
        document_count=2,
        updated_at="2026-07-28T00:05:00+00:00",
    )

    owner_body = notifier._body(review, "# Report", reviewer=False)
    reviewer_body = notifier._body(review, "# Report", reviewer=True)

    assert "activate example.com" not in owner_body
    assert "reply to this email with corrections" in owner_body
    assert "activate example.com" in reviewer_body
