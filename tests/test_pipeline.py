from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4


TEST_ROOT = Path(tempfile.mkdtemp(prefix="nerdo-tests-"))
os.environ["NERDO_DATA_DIR"] = str(TEST_ROOT / "data")
os.environ["NERDO_USERS_DIR"] = str(TEST_ROOT / "users")
os.environ["NERDO_DEMOS_PATH"] = str(TEST_ROOT / "demos.toml")
os.environ["NERDO_CRAWL_MIN_DELAY_SECONDS"] = "0"
os.environ["NERDO_CRAWL_MAX_DELAY_SECONDS"] = "0"

from app.config import settings  # noqa: E402
from app.db import execute, fetch_all, fetch_one, init_db, utc_now  # noqa: E402
from app.services.cleaner import clean_pages  # noqa: E402
from app.services.crawler import (  # noqa: E402
    CrawlResult,
    CrawledPage,
    _validate_public_url,
    normalize_url,
)
from app.services.indexer import build_index  # noqa: E402


class PipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        shutil.rmtree(settings.data_dir, ignore_errors=True)
        init_db()
        self.intake_id = str(uuid4())
        self.version_id = str(uuid4())
        self.run_id = str(uuid4())
        self.dataset_dir = (
            settings.data_dir
            / "intakes"
            / self.intake_id
            / "datasets"
            / self.version_id
        )
        self.raw_dir = self.dataset_dir / "raw" / "pages"
        self.raw_dir.mkdir(parents=True)
        now = utc_now()

        execute(
            """
            INSERT INTO intakes (
                id, status_token_hash, email, website_url, domain,
                status, created_at, updated_at, dataset_version_id,
                dataset_path
            )
            VALUES (?, ?, ?, ?, ?, 'cleaning', ?, ?, ?, ?)
            """,
            (
                self.intake_id,
                "x" * 64,
                "owner@example.com",
                "https://example.com",
                "example.com",
                now,
                now,
                self.version_id,
                str(self.dataset_dir),
            ),
        )
        execute(
            """
            INSERT INTO dataset_versions (
                id, intake_id, status, dataset_path,
                crawl_run_id, created_at, updated_at
            )
            VALUES (?, ?, 'cleaning', ?, ?, ?, ?)
            """,
            (
                self.version_id,
                self.intake_id,
                str(self.dataset_dir),
                self.run_id,
                now,
                now,
            ),
        )
        execute(
            """
            INSERT INTO crawl_runs (
                id, intake_id, dataset_version_id,
                start_url, status, started_at
            )
            VALUES (?, ?, ?, ?, 'done', ?)
            """,
            (
                self.run_id,
                self.intake_id,
                self.version_id,
                "https://example.com",
                now,
            ),
        )

    def _page(self, url: str, title: str, html: str) -> CrawledPage:
        page_id = str(uuid4())
        path = self.raw_dir / f"{page_id}.html"
        path.write_text(html, encoding="utf-8")
        now = utc_now()
        execute(
            """
            INSERT INTO crawl_pages (
                id, crawl_run_id, intake_id, requested_url,
                final_url, depth, status_code, content_type,
                bytes_read, content_sha256, raw_path, title,
                noindex, nofollow, outcome, delay_seconds, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, 0, 200, 'text/html', ?, ?, ?, ?,
                    0, 0, 'fetched', 0, ?)
            """,
            (
                page_id,
                self.run_id,
                self.intake_id,
                url,
                url,
                len(html.encode("utf-8")),
                "a" * 64,
                str(path),
                title,
                now,
            ),
        )
        return CrawledPage(
            id=page_id,
            requested_url=url,
            final_url=url,
            canonical_hint=None,
            parent_url=None,
            depth=0,
            title=title,
            language="en",
            meta_description=None,
            raw_path=str(path),
            status_code=200,
            content_type="text/html",
            bytes_read=len(html.encode("utf-8")),
            content_sha256="a" * 64,
            noindex=False,
            nofollow=False,
        )

    def test_clean_deduplicate_and_index_in_files_and_sqlite(self) -> None:
        alpha = """
        <html><head><title>Alpha</title></head><body>
        <header>Repeated menu</header>
        <main><h1>Alpha</h1>
        <p>This page describes product design, systems work, and careful
        technical implementation for organizations with complex needs.</p>
        <script>do_not_keep()</script><pre>code_not_content()</pre>
        <p>It also explains collaboration, delivery, and project context.</p>
        </main><footer>Repeated menu</footer></body></html>
        """
        alpha_copy = alpha.replace("Alpha", "Alpha Copy")
        beta = """
        <html><head><title>Beta</title></head><body><main>
        <h1>Beta</h1>
        <p>This separate page describes user interface design and digital
        platforms for technical organizations and operational teams.</p>
        <p>Its content is distinct and must remain a canonical document.</p>
        </main></body></html>
        """
        pages = [
            self._page("https://example.com/alpha", "Alpha", alpha),
            self._page(
                "https://example.com/alpha?copy=1",
                "Alpha Copy",
                alpha_copy,
            ),
            self._page("https://example.com/beta", "Beta", beta),
        ]

        clean_result = clean_pages(pages, self.dataset_dir / "cleaned")
        self.assertEqual(len(clean_result.canonical_documents), 2)
        self.assertEqual(clean_result.duplicate_count, 1)
        for document in clean_result.canonical_documents:
            self.assertNotIn("do_not_keep", document.cleaned_text)
            self.assertNotIn("code_not_content", document.cleaned_text)
            self.assertTrue(Path(document.clean_path or "").is_file())

        crawl_manifest = self.dataset_dir / "raw" / "crawl-manifest.json"
        crawl_manifest.write_text("{}\n", encoding="utf-8")
        crawl_result = CrawlResult(
            crawl_run_id=self.run_id,
            pages=pages,
            manifest_path=str(crawl_manifest),
            attempts=3,
            total_bytes=sum(page.bytes_read for page in pages),
            skipped_pages=0,
            stop_reason="frontier-exhausted",
        )
        index_result = build_index(
            intake_id=self.intake_id,
            dataset_version_id=self.version_id,
            dataset_dir=self.dataset_dir,
            crawl_result=crawl_result,
            clean_result=clean_result,
        )

        self.assertTrue(Path(index_result.documents_path).is_file())
        self.assertTrue(Path(index_result.chunks_path).is_file())
        self.assertTrue(Path(index_result.manifest_path).is_file())
        self.assertEqual(
            len(
                fetch_all(
                    "SELECT * FROM documents WHERE dataset_version_id = ?",
                    (self.version_id,),
                )
            ),
            3,
        )
        self.assertEqual(
            len(
                fetch_all(
                    "SELECT * FROM chunks WHERE dataset_version_id = ?",
                    (self.version_id,),
                )
            ),
            index_result.chunk_count,
        )
        capability = fetch_one(
            "SELECT enabled FROM runtime_capabilities WHERE name = 'sqlite_fts5'"
        )
        self.assertIsNotNone(capability)

    def test_url_normalization_and_private_network_rejection(self) -> None:
        self.assertEqual(
            normalize_url(
                "HTTPS://Example.com/a/../services/?utm_source=x&b=2&a=1#top"
            ),
            "https://example.com/services/?a=1&b=2",
        )
        with self.assertRaises(RuntimeError):
            _validate_public_url("http://127.0.0.1/")


if __name__ == "__main__":
    unittest.main()
