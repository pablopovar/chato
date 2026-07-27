from __future__ import annotations

from typing import Any

import httpx


class NerdoError(RuntimeError):
    pass


class NerdoClient:
    """HTTP bridge to the Nerdo API."""

    def __init__(self, base_url: str, admin_token: str, timeout: float = 30.0,
                 transport: httpx.BaseTransport | None = None):
        self.admin_token = admin_token
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self.client.request(method, path, **kwargs)
        try:
            payload = response.json()
        except ValueError:
            payload = {"detail": response.text}
        if response.is_error:
            detail = payload.get("detail") or payload.get("message") or response.reason_phrase
            raise NerdoError(f"Nerdo API {response.status_code}: {detail}")
        return payload

    def submit_intake(self, website_url: str, email: str,
                      business_name: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"website_url": website_url, "email": email}
        if business_name:
            body["business_name"] = business_name
        return self._request("POST", "/intakes", json=body)

    def get_intake(self, intake_id: str, status_token: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/intakes/{intake_id}",
            headers={"X-Status-Token": status_token},
        )

    def retry_intake(self, intake_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/admin/intakes/{intake_id}/retry",
            headers={"X-Admin-Token": self.admin_token},
        )

    def dataset_documents(self, intake_id: str,
                          include_noncanonical: bool = True) -> list[dict[str, Any]]:
        payload = self._request(
            "GET",
            f"/admin/intakes/{intake_id}/dataset/documents",
            params={"include_noncanonical": str(include_noncanonical).lower()},
            headers={"X-Admin-Token": self.admin_token},
        )
        if isinstance(payload, list):
            return payload
        for key in ("documents", "items", "records"):
            if isinstance(payload.get(key), list):
                return payload[key]
        return []

    def dataset_search(self, intake_id: str, query: str, limit: int = 10) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/admin/intakes/{intake_id}/dataset/search",
            params={"q": query, "limit": limit},
            headers={"X-Admin-Token": self.admin_token},
        )

    def get_bot(self, domain: str, bot_key: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/bots/{domain}",
            headers={"X-Bot-Key": bot_key},
        )

    def chat(self, domain: str, bot_key: str, message: str,
             session_id: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"domain": domain, "key": bot_key, "question": message}
        if session_id:
            body["session_id"] = session_id
        return self._request("POST", "/chat", json=body)

    def get_conversation(self, domain: str, bot_key: str,
                         session_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/conversations/{session_id}",
            params={"domain": domain},
            headers={"X-Bot-Key": bot_key},
        )
