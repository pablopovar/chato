# Website setup reports

A website intake is not complete when crawling and indexing stop. Completion requires two distinct outputs:

1. Nerdo reports what was retrieved, cleaned, standardized, deduplicated, discarded, and indexed.
2. Chato reads the complete canonical corpus and reports what it understands about the organization it will represent.

The outputs are combined for delivery but remain visibly attributed to the responsible persona.

## Stored artifacts

```text
intakes/<intake-id>/setup-report.md
intakes/<intake-id>/chato-summary.md
```

`setup-report.md` is the review and delivery artifact. It is not deployed into the active retrieval corpus.

`chato-summary.md` is Chato's corpus-grounded understanding. After review and activation it is deployed as the domain's `knowledge.md` alongside the canonical source pages.

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

## Failure behavior

Chato's corpus summary is required. A model failure, empty summary, or incomplete corpus-read stage fails the intake. Nerdo must not replace it with a source inventory and allow that inventory to become `knowledge.md`.

## Delivery

When the intake reaches `awaiting_review`:

- configured reviewers receive the report and the activation command;
- the website owner receives the report and an invitation to reply with corrections, missing information, or questions;
- delivery is recorded per recipient and failed sends retry under the existing review-ready notification policy.

The completed Markdown report is also available through the authenticated Core setup-report endpoint.
