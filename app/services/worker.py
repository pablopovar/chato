from __future__ import annotations

import logging
import threading
import time

from app.config import settings
from app.db import (
    claim_next_job,
    execute,
    fail_job,
    finish_job,
    utc_now,
)
from app.services.intake import process_intake


LOGGER = logging.getLogger("nerdo.worker")
_stop_event = threading.Event()
_thread: threading.Thread | None = None


def _run_job(job: dict) -> None:
    if job["kind"] == "process_intake":
        process_intake(job["intake_id"])
        return
    raise RuntimeError(f"Unsupported job kind: {job['kind']}")


def _loop() -> None:
    while not _stop_event.is_set():
        job = claim_next_job()
        if not job:
            _stop_event.wait(settings.worker_poll_seconds)
            continue

        try:
            _run_job(job)
            finish_job(job["id"])
        except Exception as exc:
            LOGGER.exception(
                "Job failed: id=%s kind=%s",
                job["id"],
                job["kind"],
            )
            fail_job(job["id"], str(exc))
            execute(
                '''
                UPDATE intakes
                SET status = 'failed', error = ?, updated_at = ?
                WHERE id = ?
                ''',
                (str(exc)[:4000], utc_now(), job["intake_id"]),
            )


def start_worker() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(
        target=_loop,
        name="nerdo-worker",
        daemon=True,
    )
    _thread.start()


def stop_worker() -> None:
    _stop_event.set()
    if _thread:
        _thread.join(timeout=5)
