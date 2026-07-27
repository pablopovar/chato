from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Storage:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sites (
                    id TEXT PRIMARY KEY,
                    website_url TEXT NOT NULL,
                    email TEXT NOT NULL,
                    business_name TEXT,
                    domain TEXT NOT NULL,
                    intake_id TEXT NOT NULL,
                    core_status_token TEXT NOT NULL,
                    site_token_hash TEXT NOT NULL,
                    bot_key TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    site_id TEXT REFERENCES sites(id) ON DELETE CASCADE,
                    persona TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    persona TEXT NOT NULL,
                    content TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY,
                    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    error TEXT,
                    core_ref_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS snapshots (
                    id TEXT PRIMARY KEY,
                    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
                    digest TEXT NOT NULL,
                    documents_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS integrations (
                    id TEXT PRIMARY KEY,
                    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    target_url TEXT,
                    label TEXT,
                    status TEXT NOT NULL,
                    configuration_json TEXT NOT NULL,
                    verification_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS corrections (
                    id TEXT PRIMARY KEY,
                    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
                    answer_id TEXT,
                    question TEXT NOT NULL,
                    original_answer TEXT NOT NULL,
                    correction TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _decode(row: sqlite3.Row | None, json_fields: tuple[str, ...] = ()) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        for field in json_fields:
            data[field] = json.loads(data[field])
        return data

    def create_site(self, *, website_url: str, email: str, business_name: str | None,
                    domain: str, intake_id: str, core_status_token: str, status: str) -> tuple[dict[str, Any], str]:
        site_id = new_id("site")
        token = new_token()
        now = utcnow()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO sites
                (id, website_url, email, business_name, domain, intake_id, core_status_token,
                 site_token_hash, bot_key, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)""",
                (site_id, website_url, email, business_name, domain, intake_id,
                 core_status_token, token_hash(token), status, now, now),
            )
        return self.get_site(site_id), token

    def get_site(self, site_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return self._decode(conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone())

    def verify_site_token(self, site_id: str, token: str) -> bool:
        site = self.get_site(site_id)
        return bool(site and hmac.compare_digest(site["site_token_hash"], token_hash(token)))

    def update_site(self, site_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {"status", "domain", "bot_key"}
        changes = {k: v for k, v in fields.items() if k in allowed}
        if not changes:
            site = self.get_site(site_id)
            if site is None:
                raise KeyError(site_id)
            return site
        changes["updated_at"] = utcnow()
        sql = ", ".join(f"{key} = ?" for key in changes)
        values = list(changes.values()) + [site_id]
        with self.connect() as conn:
            conn.execute(f"UPDATE sites SET {sql} WHERE id = ?", values)
        site = self.get_site(site_id)
        if site is None:
            raise KeyError(site_id)
        return site

    def create_conversation(self, persona: str, site_id: str | None) -> dict[str, Any]:
        conversation_id = new_id("conv")
        now = utcnow()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO conversations (id, site_id, persona, created_at) VALUES (?, ?, ?, ?)",
                (conversation_id, site_id, persona, now),
            )
        return self.get_conversation(conversation_id)

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return self._decode(conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone())

    def add_message(self, conversation_id: str, role: str, persona: str,
                    content: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        message_id = new_id("msg")
        now = utcnow()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO messages
                (id, conversation_id, role, persona, content, data_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (message_id, conversation_id, role, persona, content,
                 json.dumps(data or {}, ensure_ascii=False), now),
            )
        return {
            "message_id": message_id,
            "conversation_id": conversation_id,
            "role": role,
            "persona": persona,
            "content": content,
            "data": data or {},
            "created_at": now,
        }

    def list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at, id",
                (conversation_id,),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["message_id"] = item.pop("id")
            item["data"] = json.loads(item.pop("data_json"))
            out.append(item)
        return out

    def create_operation(self, site_id: str, kind: str, status: str,
                         result: dict[str, Any] | None = None,
                         core_ref: dict[str, Any] | None = None,
                         error: str | None = None) -> dict[str, Any]:
        operation_id = new_id("op")
        now = utcnow()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO operations
                (id, site_id, kind, status, result_json, error, core_ref_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (operation_id, site_id, kind, status,
                 json.dumps(result or {}, ensure_ascii=False), error,
                 json.dumps(core_ref or {}, ensure_ascii=False), now, now),
            )
        return self.get_operation(operation_id)

    def get_operation(self, operation_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM operations WHERE id = ?", (operation_id,)).fetchone()
        item = self._decode(row, ("result_json", "core_ref_json"))
        if item:
            item["operation_id"] = item.pop("id")
            item["result"] = item.pop("result_json")
            item["core_ref"] = item.pop("core_ref_json")
        return item

    def update_operation(self, operation_id: str, *, status: str | None = None,
                         result: dict[str, Any] | None = None,
                         error: str | None = None) -> dict[str, Any]:
        current = self.get_operation(operation_id)
        if current is None:
            raise KeyError(operation_id)
        changes: dict[str, Any] = {"updated_at": utcnow()}
        if status is not None:
            changes["status"] = status
        if result is not None:
            changes["result_json"] = json.dumps(result, ensure_ascii=False)
        if error is not None:
            changes["error"] = error
        sql = ", ".join(f"{key} = ?" for key in changes)
        values = list(changes.values()) + [operation_id]
        with self.connect() as conn:
            conn.execute(f"UPDATE operations SET {sql} WHERE id = ?", values)
        updated = self.get_operation(operation_id)
        assert updated is not None
        return updated

    def add_snapshot(self, site_id: str, documents: list[dict[str, Any]]) -> dict[str, Any]:
        normalized = sorted(
            [
                {
                    "document_id": str(doc.get("document_id") or doc.get("id") or ""),
                    "source_url": str(doc.get("source_url") or doc.get("url") or ""),
                    "status": str(doc.get("status") or "canonical"),
                    "hash": str(doc.get("content_sha256") or doc.get("hash") or ""),
                    "title": str(doc.get("title") or ""),
                    "word_count": int(doc.get("word_count") or 0),
                }
                for doc in documents
            ],
            key=lambda d: (d["source_url"], d["document_id"]),
        )
        raw = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        snapshot_id = new_id("snap")
        now = utcnow()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO snapshots (id, site_id, digest, documents_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (snapshot_id, site_id, digest, raw, now),
            )
        return {"snapshot_id": snapshot_id, "site_id": site_id, "digest": digest,
                "documents": normalized, "created_at": now}

    def list_snapshots(self, site_id: str, limit: int = 2) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM snapshots WHERE site_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
                (site_id, limit),
            ).fetchall()
        return [
            {
                "snapshot_id": row["id"],
                "site_id": row["site_id"],
                "digest": row["digest"],
                "documents": json.loads(row["documents_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def create_integration(self, site_id: str, kind: str, target_url: str | None,
                           label: str | None, status: str,
                           configuration: dict[str, Any]) -> dict[str, Any]:
        integration_id = new_id("int")
        now = utcnow()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO integrations
                (id, site_id, kind, target_url, label, status, configuration_json,
                 verification_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)""",
                (integration_id, site_id, kind, target_url, label, status,
                 json.dumps(configuration, ensure_ascii=False), now, now),
            )
        return self.get_integration(integration_id)

    def get_integration(self, integration_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM integrations WHERE id = ?", (integration_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["integration_id"] = item.pop("id")
        item["configuration"] = json.loads(item.pop("configuration_json"))
        item["verification"] = json.loads(item.pop("verification_json"))
        return item

    def list_integrations(self, site_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id FROM integrations WHERE site_id = ? ORDER BY created_at, id",
                (site_id,),
            ).fetchall()
        return [self.get_integration(row["id"]) for row in rows if self.get_integration(row["id"]) is not None]

    def update_integration(self, integration_id: str, **fields: Any) -> dict[str, Any]:
        current = self.get_integration(integration_id)
        if current is None:
            raise KeyError(integration_id)
        changes: dict[str, Any] = {"updated_at": utcnow()}
        mapping = {
            "target_url": "target_url",
            "label": "label",
            "status": "status",
            "configuration": "configuration_json",
            "verification": "verification_json",
        }
        for key, column in mapping.items():
            if key in fields and fields[key] is not None:
                value = fields[key]
                if key in {"configuration", "verification"}:
                    value = json.dumps(value, ensure_ascii=False)
                changes[column] = value
        sql = ", ".join(f"{key} = ?" for key in changes)
        values = list(changes.values()) + [integration_id]
        with self.connect() as conn:
            conn.execute(f"UPDATE integrations SET {sql} WHERE id = ?", values)
        updated = self.get_integration(integration_id)
        assert updated is not None
        return updated

    def delete_integration(self, integration_id: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM integrations WHERE id = ?", (integration_id,))

    def add_correction(self, site_id: str, *, answer_id: str | None, question: str,
                       original_answer: str, correction: str) -> dict[str, Any]:
        correction_id = new_id("cor")
        now = utcnow()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO corrections
                (id, site_id, answer_id, question, original_answer, correction, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending_review', ?)""",
                (correction_id, site_id, answer_id, question, original_answer, correction, now),
            )
        return {
            "correction_id": correction_id,
            "site_id": site_id,
            "answer_id": answer_id,
            "question": question,
            "original_answer": original_answer,
            "correction": correction,
            "status": "pending_review",
            "created_at": now,
        }
