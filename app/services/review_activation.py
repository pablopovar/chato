from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from app.config import settings
from app.db import execute, utc_now
from app.services.email_transport import send_email
from app.services.review_workspace import ensure_review_workspace


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return cleaned[:64] or "customer"


def activate_reviewed_intake(
    intake_id: str,
    *,
    bot_name: str | None,
    system_prompt: str | None,
    allowed_origins: list[str],
    welcome_subject: str,
    welcome_message: str | None,
    test_url: str | None,
) -> dict[str, str]:
    workspace = ensure_review_workspace(intake_id)
    intake = workspace["intake"]
    if intake.get("status") != "awaiting_review":
        raise RuntimeError(
            f"The intake cannot be activated while status is {intake.get('status')}."
        )
    if not workspace.get("summary_ready"):
        detail = str(workspace.get("summary_error") or "").strip()
        raise RuntimeError(
            "Chato's corpus summary must be completed before activation."
            + (f" Last generation error: {detail}" if detail else "")
        )

    staging = Path(workspace["workspace"])
    knowledge_path = staging / "knowledge.md"
    if not knowledge_path.is_file() or not knowledge_path.read_text(
        encoding="utf-8",
        errors="replace",
    ).strip():
        raise RuntimeError("The reviewed knowledge.md is missing or empty.")

    config_path = staging / "nerdo.json"
    if not config_path.is_file():
        raise RuntimeError("The reviewed domain configuration is missing.")
    try:
        config: Any = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Could not read reviewed configuration: {exc}") from exc
    if not isinstance(config, dict):
        raise RuntimeError("The reviewed configuration is not a JSON object.")

    email_local = str(intake["email"]).split("@", 1)[0]
    destination = settings.users_dir / _slug(email_local) / str(intake["domain"])
    if destination.exists():
        raise RuntimeError(
            "An active domain directory already exists; reset or remove it before activation."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)

    config.pop("review_only", None)
    config.pop("review_intake_id", None)
    config["enabled"] = True
    if bot_name:
        config["name"] = bot_name
    if system_prompt:
        config["system_prompt"] = system_prompt
    if allowed_origins:
        config["allowed_origins"] = [
            item.strip().rstrip("/")
            for item in allowed_origins
            if item.strip()
        ]

    reviewed_config = staging / ".nerdo.activation.json"
    reviewed_config.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    try:
        shutil.copytree(staging, destination)
        (destination / ".nerdo.activation.json").replace(destination / "nerdo.json")
        (destination / "document-foundry.json").unlink(missing_ok=True)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        reviewed_config.unlink(missing_ok=True)
        raise

    public_test_url = test_url or (
        f"{settings.public_base_url}/?domain={intake['domain']}"
    )
    body = welcome_message or (
        "Your initial Chato & Nerdo is ready.\n\n"
        f"Test it here:\n{public_test_url}\n\n"
        "The activated domain uses the corpus, Chato summary, model, system prompt, "
        "and parameters approved during review."
    )

    try:
        send_email(
            to_email=str(intake["email"]),
            subject=welcome_subject,
            body=body,
        )
        execute(
            "UPDATE intakes SET status = 'active', updated_at = ? WHERE id = ?",
            (utc_now(), intake_id),
        )
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise

    shutil.rmtree(staging.parent, ignore_errors=True)
    return {
        "domain": str(intake["domain"]),
        "user_slug": destination.parent.name,
        "key": str(config.get("key") or ""),
        "test_url": public_test_url,
    }
