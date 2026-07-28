from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from app import admin_domains, db
from app.config import settings as core_settings


def test_core_reset_removes_domain_state_and_archives_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "data"
    database = data_dir / "nerdo.sqlite3"
    test_settings = replace(core_settings, data_dir=data_dir)
    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(db, "settings", test_settings)
    monkeypatch.setattr(admin_domains, "settings", test_settings)
    trace_root = tmp_path / "traces"
    monkeypatch.setenv("NERDO_CHAT_TRACE_DIR", str(trace_root))

    db.init_db()
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO intakes (
                id, status_token_hash, email, website_url, domain,
                business_name, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "intake-1",
                "hash",
                "owner@example.com",
                "https://example.com",
                "example.com",
                "Example",
                "active",
                "2026-07-27T00:00:00+00:00",
                "2026-07-27T00:00:00+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO conversations (id, domain, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (
                "session-1",
                "example.com",
                "2026-07-27T00:00:00+00:00",
                "2026-07-27T00:00:00+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (
                "session-1",
                "user",
                "Question",
                "2026-07-27T00:00:00+00:00",
            ),
        )

    intake_dir = data_dir / "intakes" / "intake-1"
    intake_dir.mkdir(parents=True)
    (intake_dir / "draft.md").write_text("# Old draft\n", encoding="utf-8")
    domain_trace = trace_root / "example.com" / "session"
    domain_trace.mkdir(parents=True)
    (domain_trace / "trace.json").write_text("{}\n", encoding="utf-8")

    result = admin_domains.reset_domain_state("example.com")

    assert result["removed_intakes"] == 1
    assert result["removed_conversations"] == 1
    assert not intake_dir.exists()
    assert not (trace_root / "example.com").exists()
    assert list((data_dir / "domain-reset-archive").rglob("draft.md"))
    assert list((data_dir / "domain-reset-archive").rglob("trace.json"))
    with db.connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM intakes WHERE domain='example.com'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM conversations WHERE domain='example.com'"
        ).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
