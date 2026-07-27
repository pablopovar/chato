# Nerdo's Document Foundry in the domain dashboard

Each active domain dashboard includes a **Nerdo's Document Foundry** tab after **Test chat**.

The integration has two distinct responsibilities:

1. Chato remains the authority for the active domain corpus under `users/<owner>/<domain>/`.
2. The standalone `pablopovar/document-foundry` service converts uploaded source files into Markdown and preserves its processing provenance.

The dashboard does not embed or duplicate the standalone Foundry dashboard.

## Domain dashboard capabilities

The domain page can:

- list every active Markdown document for the domain;
- distinguish crawled website data, the initial knowledge draft, manual documents, and Foundry imports;
- expose source URLs from Markdown frontmatter when available;
- open and download raw Markdown;
- edit Markdown in place;
- reject stale saves when the file changed after it was opened;
- preserve the previous file as a non-indexed `.bak` file before each save;
- upload PDF, DOCX, HTML, Markdown, and plain-text source files through the standalone Foundry;
- copy successful Foundry output into the active domain corpus.

## Routes

```text
GET  /dashboard/api/domains/{domain}/foundry
GET  /dashboard/api/domains/{domain}/foundry/document?path=...
PUT  /dashboard/api/domains/{domain}/foundry/document
POST /dashboard/api/domains/{domain}/foundry/import
```

These routes remain inside the existing Apache-protected `/dashboard/` area.

## Domain layout

```text
users/<owner>/<domain>/
├── knowledge.md
├── source-pages/
├── manual-documents/
│   └── foundry/
├── document-foundry.json
└── .document-backups/
```

`document-foundry.json` records the standalone Foundry folder associated with the domain. Backups use a `.bak` suffix so Core's Markdown retrieval does not index them.

## Standalone Foundry connection

Run `pablopovar/document-foundry` separately on port 3500, then configure Chato:

```dotenv
NERDO_DOCUMENT_FOUNDRY_BASE_URL=http://host.docker.internal:3500
NERDO_DOCUMENT_FOUNDRY_TIMEOUT_SECONDS=600
NERDO_FOUNDRY_MARKDOWN_MAX_BYTES=2000000
NERDO_FOUNDRY_SOURCE_MAX_BYTES=104857600
NERDO_FOUNDRY_IMPORT_MAX_FILES=20
```

If the standalone service is unavailable, the domain corpus browser and Markdown editor continue to work. Only source conversion and import are unavailable.

## Immediate retrieval behavior

Core reads the active domain's Markdown files during each search. A saved edit or successful import therefore becomes available to the next chat request without a separate indexing command.
