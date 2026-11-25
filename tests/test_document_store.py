from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from nerdo_api.document_store import DomainDocumentStore


def store(tmp_path: Path) -> DomainDocumentStore:
    root = tmp_path / "user" / "example.com"
    root.mkdir(parents=True)
    (root / "nerdo.json").write_text('{"domain":"example.com"}\n', encoding="utf-8")
    return DomainDocumentStore(root, "example.com")


def test_ingest_is_channel_neutral_and_deduplicates_by_content(tmp_path: Path) -> None:
    documents = store(tmp_path)

    first = documents.persist_markdown(
        "terms.md",
        b"# Terms\nSame content\n",
        origin="email",
    )
    second = documents.persist_markdown(
        "renamed.md",
        b"# Terms\nSame content\n",
        origin="dashboard",
    )

    assert first["path"] == "documents/terms.md"
    assert second["path"] == first["path"]
    assert second["deduplicated"] is True
    assert len([item for item in documents.list() if item["category"] == "Added document"]) == 1


def test_same_filename_with_different_content_gets_deterministic_collision_name(tmp_path: Path) -> None:
    documents = store(tmp_path)

    first = documents.persist_markdown("policy.md", b"one", origin="email")
    second = documents.persist_markdown("policy.md", b"two", origin="document-foundry")

    assert first["path"] == "documents/policy.md"
    assert second["path"].startswith("documents/policy__")
    assert second["path"].endswith(".md")
    assert first["sha256"] != second["sha256"]


def test_legacy_paths_remain_visible_but_new_documents_use_canonical_namespace(tmp_path: Path) -> None:
    documents = store(tmp_path)
    legacy = documents.root / "mail-imports" / "old.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("legacy\n", encoding="utf-8")

    new = documents.persist_markdown("new.md", b"new", origin="email")
    records = {item["path"]: item for item in documents.list()}

    assert records["mail-imports/old.md"]["category"] == "Legacy mail import"
    assert new["path"] == "documents/new.md"


def test_edit_uses_store_owned_backup_and_updates_hash(tmp_path: Path) -> None:
    documents = store(tmp_path)
    created = documents.persist_markdown("notes.md", b"before", origin="dashboard")
    opened = documents.read(created["path"], max_bytes=10_000)

    saved = documents.save(
        created["path"],
        "after",
        opened["sha256"],
        max_bytes=10_000,
    )

    assert saved["document"]["sha256"] != opened["sha256"]
    assert saved["backup"].startswith(".document-backups/")
    assert (documents.root / saved["backup"]).read_text(encoding="utf-8") == "before\n"


def test_system_corpus_deployment_is_owned_by_document_store(tmp_path: Path) -> None:
    documents = store(tmp_path)
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "home.md").write_text("home\n", encoding="utf-8")
    nested = pages / "docs"
    nested.mkdir()
    (nested / "faq.md").write_text("faq\n", encoding="utf-8")

    knowledge = documents.deploy_knowledge(b"summary")
    deployed = documents.replace_website_pages(pages)

    assert knowledge["path"] == "knowledge.md"
    assert {item["path"] for item in deployed} == {
        "source-pages/home.md",
        "source-pages/docs/faq.md",
    }


def test_concurrent_ingests_preserve_every_registry_entry(tmp_path: Path) -> None:
    documents = store(tmp_path)
    items = [(f"note-{index}.md", f"# Note {index}\n".encode()) for index in range(24)]

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda item: documents.persist_markdown(
                    item[0], item[1], origin="dashboard"
                ),
                items,
            )
        )

    registry = json.loads(documents.registry_path.read_text(encoding="utf-8"))
    assert len(results) == len(items)
    assert len(registry["documents"]) == len(items)
    assert all((documents.root / result["path"]).is_file() for result in results)


def test_failed_page_copy_keeps_previous_corpus(tmp_path: Path, monkeypatch) -> None:
    documents = store(tmp_path)
    source = tmp_path / "new-pages"
    source.mkdir()
    (source / "new.md").write_text("new\n", encoding="utf-8")
    documents.replace_website_pages(source)

    def fail_read(_self):
        raise OSError("simulated read failure")

    monkeypatch.setattr(Path, "read_bytes", fail_read)
    try:
        documents.replace_website_pages(source)
    except OSError:
        pass
    else:
        raise AssertionError("expected simulated page-copy failure")

    assert (documents.root / "source-pages" / "new.md").read_text(encoding="utf-8") == "new\n"


def test_registry_failure_rolls_back_new_document(tmp_path: Path, monkeypatch) -> None:
    documents = store(tmp_path)

    def fail_registry(_payload):
        raise OSError("simulated registry write failure")

    monkeypatch.setattr(documents, "_write_registry", fail_registry)
    try:
        documents.persist_markdown("orphan.md", b"content", origin="email")
    except OSError:
        pass
    else:
        raise AssertionError("expected simulated registry write failure")

    assert not (documents.documents_dir / "orphan.md").exists()


def test_registry_failure_restores_document_edit(tmp_path: Path, monkeypatch) -> None:
    documents = store(tmp_path)
    created = documents.persist_markdown("notes.md", b"before", origin="dashboard")
    opened = documents.read(created["path"], max_bytes=10_000)

    def fail_registry(_payload):
        raise OSError("simulated registry write failure")

    monkeypatch.setattr(documents, "_write_registry", fail_registry)
    try:
        documents.save(
            created["path"],
            "after",
            opened["sha256"],
            max_bytes=10_000,
        )
    except OSError:
        pass
    else:
        raise AssertionError("expected simulated registry write failure")

    assert (documents.root / created["path"]).read_text(encoding="utf-8") == "before\n"
