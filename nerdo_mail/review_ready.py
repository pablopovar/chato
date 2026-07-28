from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass

import httpx

from app.services.email_transport import send_email

from .config import MailSettings


LOGGER = logging.getLogger("nerdo-mail.review-ready")


@dataclass(frozen=True)
class ReviewReadyNotification:
    review_key: str
    intake_id: str
    domain: str
    owner_email: str
    document_count: int
    updated_at: str


class ReviewReadyNotifier:
    def __init__(self, settings: MailSettings):
        self.settings = settings
        self.dashboard_url = os.getenv(
            "NERDO_DASHBOARD_URL",
            "https://chato.povarchik.com/dashboard/",
        ).strip()
        self._init_table()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.settings.state_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _init_table(self) -> None:
        self.settings.state_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS review_ready_notifications (
                    review_key TEXT NOT NULL,
                    intake_id TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (review_key, recipient)
                )
                """
            )

    def _pending_reviews(self) -> tuple[ReviewReadyNotification, ...]:
        response = httpx.get(
            self.settings.core_base_url + "/admin/intakes",
            headers={"X-Admin-Token": self.settings.core_admin_token},
            timeout=30,
        )
        response.raise_for_status()

        reviews: list[ReviewReadyNotification] = []
        for intake in response.json().get("intakes", []):
            if intake.get("status") != "awaiting_review":
                continue
            intake_id = str(intake.get("id", "")).strip()
            domain = str(intake.get("domain", "")).strip().casefold()
            if not intake_id or not domain:
                continue
            review_key = str(
                intake.get("dataset_version_id") or intake_id
            ).strip()
            reviews.append(
                ReviewReadyNotification(
                    review_key=review_key,
                    intake_id=intake_id,
                    domain=domain,
                    owner_email=str(intake.get("email", "")).strip().casefold(),
                    document_count=int(intake.get("document_count") or 0),
                    updated_at=str(intake.get("updated_at", "")).strip(),
                )
            )
        return tuple(reviews)

    def _setup_report(self, intake_id: str) -> str:
        response = httpx.get(
            self.settings.core_base_url
            + f"/admin/intakes/{intake_id}/setup-report",
            headers={"X-Admin-Token": self.settings.core_admin_token},
            timeout=30,
        )
        response.raise_for_status()
        report = response.text.strip()
        if not report:
            raise RuntimeError("Nerdo Core returned an empty website setup report.")
        return report

    def _claim(self, review: ReviewReadyNotification, recipient: str) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status, attempts, updated_at
                FROM review_ready_notifications
                WHERE review_key = ? AND recipient = ?
                """,
                (review.review_key, recipient),
            ).fetchone()

            if row is None:
                connection.execute(
                    """
                    INSERT INTO review_ready_notifications (
                        review_key, intake_id, recipient, domain,
                        status, attempts, error
                    )
                    VALUES (?, ?, ?, ?, 'sending', 1, NULL)
                    """,
                    (
                        review.review_key,
                        review.intake_id,
                        recipient,
                        review.domain,
                    ),
                )
                return True

            if row["status"] != "failed" or int(row["attempts"]) >= 3:
                return False

            eligible = connection.execute(
                """
                SELECT 1
                WHERE ? <= datetime('now', '-5 minutes')
                """,
                (row["updated_at"],),
            ).fetchone()
            if not eligible:
                return False

            connection.execute(
                """
                UPDATE review_ready_notifications
                SET status = 'sending', attempts = attempts + 1,
                    error = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE review_key = ? AND recipient = ?
                """,
                (review.review_key, recipient),
            )
            return True

    def _record(
        self,
        review_key: str,
        recipient: str,
        status: str,
        error: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE review_ready_notifications
                SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE review_key = ? AND recipient = ?
                """,
                (status, error, review_key, recipient),
            )

    def _body(
        self,
        review: ReviewReadyNotification,
        report: str,
        *,
        reviewer: bool,
    ) -> str:
        review_url = (
            self.dashboard_url.rstrip("/")
            + f"/reviews/{review.intake_id}"
        )
        next_step = (
            "Review and activate this domain in the dashboard:\n"
            f"{review_url}\n\n"
            "The same activation remains available by replying to Nerdo with:\n"
            f"activate {review.domain}\n"
            if reviewer
            else (
                "Review the report and reply to this email with corrections, "
                "missing information, or questions. Activation remains under review.\n"
            )
        )
        return (
            "Nerdo finished retrieving and processing the website.\n\n"
            f"Domain: {review.domain}\n"
            f"Owner: {review.owner_email}\n"
            f"Documents: {review.document_count}\n"
            "Status: awaiting_review\n"
            f"Intake: {review.intake_id}\n"
            f"Updated: {review.updated_at}\n\n"
            "Nerdo's processing report and Chato's corpus summary follow.\n\n"
            "---\n\n"
            f"{report}\n\n"
            "---\n\n"
            + next_step
        )

    def notify_pending(self) -> int:
        sent = 0
        for review in self._pending_reviews():
            recipients = {
                address.casefold()
                for address in self.settings.admin_emails
                if address.strip()
            }
            if review.owner_email:
                recipients.add(review.owner_email)
            if not recipients:
                LOGGER.warning(
                    "No owner or reviewer email is available for %s; setup report skipped.",
                    review.domain,
                )
                continue

            report: str | None = None
            for recipient in sorted(recipients):
                if not self._claim(review, recipient):
                    continue
                try:
                    if report is None:
                        report = self._setup_report(review.intake_id)
                    reviewer = recipient in self.settings.admin_emails
                    send_email(
                        to_email=recipient,
                        subject=f"Nerdo website processing report: {review.domain}",
                        body=self._body(
                            review,
                            report,
                            reviewer=reviewer,
                        ),
                    )
                    self._record(review.review_key, recipient, "sent")
                    sent += 1
                    LOGGER.info(
                        "Sent setup report for %s to %s",
                        review.domain,
                        recipient,
                    )
                except Exception as exc:
                    self._record(
                        review.review_key,
                        recipient,
                        "failed",
                        str(exc)[:4000],
                    )
                    LOGGER.exception(
                        "Setup report delivery failed for %s to %s",
                        review.domain,
                        recipient,
                    )
        return sent
