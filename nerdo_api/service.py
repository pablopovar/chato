from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from .analysis_tools import (
    compare_snapshots,
    diagnose_answer,
    find_possible_contradictions,
    verify_web_installation,
)
from .config import Settings
from .nerdo_client import NerdoClient, NerdoError
from .storage import Storage


class GatewayService:
    def __init__(self, settings: Settings, storage: Storage, nerdo: NerdoClient):
        self.settings = settings
        self.storage = storage
        self.nerdo = nerdo

    @staticmethod
    def domain_from_url(url: str) -> str:
        host = urlparse(url).hostname or ""
        return host.lower().removeprefix("www.")

    def create_site(self, website_url: str, email: str,
                    business_name: str | None) -> tuple[dict[str, Any], str]:
        core_result = self.nerdo.submit_intake(website_url, email, business_name)
        intake_id = str(core_result.get("intake_id") or core_result.get("id") or "")
        status_token = str(core_result.get("status_token") or "")
        if not intake_id or not status_token:
            raise NerdoError("Nerdo intake response did not include intake_id and status_token.")
        status = str(core_result.get("status") or "queued")
        return self.storage.create_site(
            website_url=website_url,
            email=email,
            business_name=business_name,
            domain=self.domain_from_url(website_url),
            intake_id=intake_id,
            core_status_token=status_token,
            status=status,
        )

    def site_status(self, site: dict[str, Any]) -> dict[str, Any]:
        nerdo = self.nerdo.get_intake(site["intake_id"], site["core_status_token"])
        status = str(nerdo.get("status") or site["status"])
        self.storage.update_site(site["id"], status=status)
        return {
            "site_id": site["id"],
            "website_url": site["website_url"],
            "email": site["email"],
            "business_name": site["business_name"],
            "domain": site["domain"],
            "status": status,
            "nerdo": nerdo,
        }

    def refresh_sources(self, site: dict[str, Any]) -> dict[str, Any]:
        response = self.nerdo.retry_intake(site["intake_id"])
        return self.storage.create_operation(
            site["id"],
            "sources.refresh",
            "accepted",
            result={"message": "Website source refresh accepted."},
            core_ref={"intake_id": site["intake_id"], "core_response": response},
        )

    def poll_operation(self, operation: dict[str, Any]) -> dict[str, Any]:
        if operation["kind"] != "sources.refresh" or operation["status"] in {"completed", "failed", "blocked"}:
            return operation
        site = self.storage.get_site(operation["site_id"])
        if site is None:
            return self.storage.update_operation(operation["operation_id"], status="failed", error="Site no longer exists.")
        try:
            nerdo = self.nerdo.get_intake(site["intake_id"], site["core_status_token"])
        except NerdoError as exc:
            return self.storage.update_operation(operation["operation_id"], status="failed", error=str(exc))
        status = str(nerdo.get("status") or "running")
        if status == "failed":
            return self.storage.update_operation(operation["operation_id"], status="failed", error=str(nerdo.get("error") or "Nerdo processing failed."))
        if status in {"awaiting_review", "awaiting_clarification", "active"}:
            documents = self.nerdo.dataset_documents(site["intake_id"], True)
            snapshot = self.storage.add_snapshot(site["id"], documents)
            return self.storage.update_operation(
                operation["operation_id"],
                status="completed",
                result={"core_status": status, "snapshot": snapshot, "document_count": len(documents)},
            )
        return self.storage.update_operation(
            operation["operation_id"],
            status="running",
            result={"core_status": status},
        )

    def list_sources(self, site: dict[str, Any]) -> list[dict[str, Any]]:
        return self.nerdo.dataset_documents(site["intake_id"], True)

    def source_changes(self, site: dict[str, Any], capture_current: bool = True) -> dict[str, Any]:
        snapshots = self.storage.list_snapshots(site["id"], 2)
        if capture_current:
            documents = self.list_sources(site)
            current = self.storage.add_snapshot(site["id"], documents)
            snapshots = [current] + [snap for snap in snapshots if snap["digest"] != current["digest"]][:1]
        if not snapshots:
            return {"baseline_available": False, "message": "No source snapshot is available."}
        current = snapshots[0]
        previous = snapshots[1] if len(snapshots) > 1 else None
        return compare_snapshots(previous, current)

    def contradictions(self, site: dict[str, Any], limit: int) -> dict[str, Any]:
        documents = self.list_sources(site)
        findings = find_possible_contradictions(documents, limit=limit)
        return {
            "classification": "possible_conflicts",
            "finding_count": len(findings),
            "findings": findings,
            "note": "Nerdo flags possible conflicts for review; it does not silently decide which source is authoritative.",
        }

    def diagnose(self, site: dict[str, Any], question: str, answer: str,
                 search_limit: int) -> dict[str, Any]:
        search = self.nerdo.dataset_search(site["intake_id"], question, search_limit)
        return diagnose_answer(question, answer, search)

    def correct_answer(self, site: dict[str, Any], **payload: Any) -> dict[str, Any]:
        return self.storage.add_correction(site["id"], **payload)

    def connect_integration(self, site: dict[str, Any], kind: str,
                            target_url: str | None, label: str | None,
                            configuration: dict[str, Any]) -> dict[str, Any]:
        config = dict(configuration)
        if kind in {"generic_web", "wordpress", "joomla"}:
            script_url = str(config.get("widget_script_url") or self.settings.widget_script_url)
            if not script_url:
                return self.storage.create_integration(
                    site["id"], kind, target_url, label, "needs_configuration",
                    {**config, "required": ["widget_script_url"]},
                )
            marker = f'data-site-id="{site["id"]}"'
            snippet = (
                f'<script src="{script_url}" data-site-id="{site["id"]}" '
                f'data-domain="{site["domain"]}" defer></script>'
            )
            config.update({
                "widget_script_url": script_url,
                "embed_code": snippet,
                "verification_markers": [script_url, marker],
                "instructions": "Add the embed code before the closing </body> tag or through the CMS custom-code facility.",
            })
            status = "configuration_ready"
        else:
            config.update({
                "adapter_status": "required",
                "message": f"The {kind} API resource is created, but its provider adapter and authorization flow must be configured.",
            })
            status = "adapter_required"
        return self.storage.create_integration(site["id"], kind, target_url, label, status, config)

    def verify_integration(self, integration: dict[str, Any]) -> dict[str, Any]:
        if integration["kind"] not in {"generic_web", "wordpress", "joomla"}:
            verification = {
                "verified": False,
                "status": "adapter_required",
                "message": "This integration requires a provider-specific verification adapter.",
            }
        elif not integration["target_url"]:
            verification = {
                "verified": False,
                "status": "needs_input",
                "message": "target_url is required before installation can be verified.",
            }
        else:
            markers = list(integration["configuration"].get("verification_markers") or [])
            verification = verify_web_installation(
                integration["target_url"], markers, self.settings.verify_timeout_seconds
            )
            verification["status"] = "verified" if verification["verified"] else "not_verified"
        status = "verified" if verification.get("verified") else verification.get("status", "not_verified")
        return self.storage.update_integration(
            integration["integration_id"], status=status, verification=verification
        )

    def chato_message(self, conversation: dict[str, Any], content: str) -> tuple[str, dict[str, Any]]:
        site_id = conversation.get("site_id")
        lowered = content.lower()
        if not site_id:
            if "how" in lowered and "work" in lowered:
                return (
                    "Give Chato & Nerdo your website and email. Nerdo prepares the website knowledge and operations; Chato helps you test and improve the public experience by conversation.",
                    {"action": "explain"},
                )
            if "my website" in lowered or "start" in lowered:
                return (
                    "Send the website URL and email through the Start action. Once the site is prepared, this conversation continues with your own material.",
                    {"action": "start_site", "endpoint": "POST /v1/sites"},
                )
            return (
                "Ask how it works, or start by giving Chato & Nerdo your website and email.",
                {"suggestions": ["Ask Chato how it works", "Chato my website"]},
            )
        site = self.storage.get_site(site_id)
        if site is None:
            return "That website record no longer exists.", {"error": "site_not_found"}
        if not site.get("bot_key"):
            return (
                f"Nerdo is still preparing or reviewing {site['domain']}. Current status: {site['status']}.",
                {"site_status": site["status"]},
            )
        payload = self.nerdo.chat(site["domain"], site["bot_key"], content, conversation["id"])
        return str(payload.get("answer") or payload.get("message") or ""), payload

    def nerdo_message(self, conversation: dict[str, Any], content: str,
                      context: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any] | None, list[str]]:
        site_id = conversation.get("site_id")
        if not site_id:
            return "Nerdo needs a website before running technical operations.", {}, None, ["site_id"]
        site = self.storage.get_site(site_id)
        if site is None:
            return "That website record no longer exists.", {}, None, []
        text = content.lower()
        if re.search(r"\b(update|refresh|re-read|recrawl|re-crawl)\b", text) and re.search(r"\b(site|website|source|sources|knowledge)\b", text):
            operation = self.refresh_sources(site)
            return "I started updating the website sources.", {"operation": operation}, operation, []
        if "what changed" in text or ("show" in text and "change" in text):
            result = self.source_changes(site, True)
            operation = self.storage.create_operation(site_id, "sources.changes", "completed", result=result)
            return "I compared the current website sources with the previous snapshot.", result, operation, []
        if "contradiction" in text or "conflict" in text:
            result = self.contradictions(site, int(context.get("limit") or 50))
            operation = self.storage.create_operation(site_id, "knowledge.contradictions", "completed", result=result)
            return f"I found {result['finding_count']} possible source conflicts for review.", result, operation, []
        if "diagnose" in text or "incorrect answer" in text or "wrong answer" in text:
            missing = [name for name in ("question", "answer") if not context.get(name)]
            if missing:
                return "Send the question and the answer you want me to diagnose.", {}, None, missing
            result = self.diagnose(site, str(context["question"]), str(context["answer"]), int(context.get("search_limit") or 10))
            operation = self.storage.create_operation(site_id, "answers.diagnose", "completed", result=result)
            return f"The answer audit is complete: {result['classification']}.", result, operation, []
        if "connect" in text and "wordpress" in text:
            missing = [name for name in ("target_url", "widget_script_url") if not context.get(name)]
            if missing:
                return "Send the WordPress URL and the widget script URL.", {}, None, missing
            integration = self.connect_integration(
                site, "wordpress", str(context["target_url"]), "WordPress",
                {"widget_script_url": str(context["widget_script_url"])},
            )
            operation = self.storage.create_operation(site_id, "integrations.connect", "completed", result={"integration": integration})
            return "The WordPress installation configuration is ready.", {"integration": integration}, operation, []
        if "verify" in text and "installation" in text:
            integration_id = str(context.get("integration_id") or "")
            if not integration_id:
                return "Send the integration_id for the installation you want verified.", {}, None, ["integration_id"]
            integration = self.storage.get_integration(integration_id)
            if integration is None or integration["site_id"] != site_id:
                return "I could not find that integration for this website.", {}, None, []
            verified = self.verify_integration(integration)
            operation = self.storage.create_operation(site_id, "integrations.verify", "completed", result={"integration": verified})
            return "Installation verification is complete.", {"integration": verified}, operation, []
        return (
            "I can update website sources, show changes, find possible contradictions, diagnose an incorrect answer, prepare a WordPress connection, and verify an installation.",
            {"capabilities": [
                "update website sources", "show what changed", "find contradictions",
                "diagnose an incorrect answer", "connect WordPress", "verify installation",
            ]},
            None,
            [],
        )
