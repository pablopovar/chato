# Website setup reports and review workspaces

A website intake is not complete when crawling and indexing stop. Completion requires two distinct outputs:

1. Nerdo reports what was retrieved, cleaned, standardized, deduplicated, discarded, and indexed.
2. Chato reads the complete canonical corpus and reports what it understands about the organization it will represent.

The outputs are combined for delivery but remain visibly attributed to the responsible persona.

## Stored artifacts

```text
intakes/<intake-id>/setup-report.md
intakes/<intake-id>/chato-summary.md
users/.review-<intake-prefix>/<domain>/
├── nerdo.json
├── knowledge.md
├── source-pages/
└── manual-documents/
```

`setup-report.md` is the review and delivery artifact. It is not part of retrieval.

`chato-summary.md` is Chato's corpus-grounded understanding. The review workspace mirrors it as `knowledge.md` alongside the canonical source pages.

The review workspace deliberately uses the same directory and configuration shape as an active domain. The same configuration, retrieval, chat, document, Foundry, history, trace, and session APIs therefore operate before and after activation.

## Nerdo section

Nerdo's section is deterministic and reports processing facts, including:

- submitted domain and URL;
- intake and completion timestamps;
- fetch attempts, retrieved pages, skipped pages, retrieved bytes, and crawl stop reason;
- pages cleaned, canonical Markdown documents, duplicates, and discarded documents;
- indexed documents, search passages, and index readiness;
- explicit crawl and corpus-coverage limitations.

The client-facing report does not expose internal filesystem paths, keys, provider credentials, prompts, or infrastructure details.

## Chato section

Chato reads every canonical page through a complete map-and-synthesize pass. The summary includes:

- business overview;
- organization type, language, and location;
- about the business;
- key features;
- corpus-supported competitive advantages or labeled inferences;
- target customers;
- geographic focus;
- suggested topics;
- suggested keywords without fabricated SEO metrics;
- suggested visitor questions;
- data gaps, contradictions, stale material, and uncertainties;
- supporting source URLs.

Chato may infer likely audiences or distinctive characteristics only when the inference is labeled. It may not claim search volume, competition, rankings, market superiority, or other outside facts that are not present in the corpus.

## Failure and legacy behavior

Chato's corpus summary is required. A model failure, empty summary, or incomplete corpus-read stage fails the intake. Nerdo must not replace it with a source inventory and allow that inventory to become `knowledge.md`.

An `awaiting_review` intake produced before setup reports existed is upgraded on first review. Chato rereads its canonical corpus, Nerdo reconstructs its processing report from persisted crawl, document, and index records, and the normal review workspace is created.

## Review workflow

The dashboard lists active domains and incomplete intakes together. An `awaiting_review` intake opens:

```text
/dashboard/reviews/<intake-id>
```

That URL prepares the review workspace and redirects into the normal domain dashboard with the intake ID in review context. The reviewer has access to:

- Review: Nerdo's immutable processing report, Chato's editable corpus summary, report download, and activation;
- Crawl: every recorded requested URL, final URL, outcome, response status, depth, byte count, and skip reason;
- Configuration: model, system prompt, temperature, output-token limit, retrieval-result count, context limit, and debug tracing;
- Test chat: the same retrieval and model path the active domain will use;
- Nerdo's Document Foundry: the converted Markdown corpus, source URLs, editing, downloads, backups, and source-document imports;
- Past chats, Session Share, and the other normal domain facilities.

Saving Chato's summary updates `chato-summary.md`, review `knowledge.md`, and the embedded Chato section in `setup-report.md` as one rollback-safe operation.

Configuration changes are written directly to the review workspace's `nerdo.json`. Activation calls the same authenticated domain-operations API used by email and other channels. It promotes the reviewed directory into the active owner directory, removes only the review markers, and preserves the approved corpus, model, system prompt, parameters, debug setting, and bot key.

## Delivery

When the intake reaches `awaiting_review`:

- configured reviewers receive the full report, a direct review-workspace link, and the email activation command;
- the website owner receives the full report and an invitation to reply with corrections, missing information, or questions;
- delivery is recorded per recipient and failed sends retry under the existing review-ready notification policy.

The completed Markdown report is also available through the authenticated Core setup-report endpoint and through the dashboard's Download full setup report action.
