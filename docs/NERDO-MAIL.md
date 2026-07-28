# Nerdo local mailbox adapter

Temporary inbound transport for the Thunderbird-managed mailbox at:

```text
/home/pablo/.thunderbird/f0pc3s1x.default/ImapMail/hq3-mx-10.povarchik-2.com/INBOX/cur
```

The folder is mounted read-only at `/mailbox`. Nerdo scans only stable `*.eml` files in `INBOX/cur`, claims each message in `data/nerdo-mail.sqlite3` before executing it, and never moves or modifies Thunderbird files.

The mailbox is a channel adapter only. Domain lifecycle and corpus operations are executed through the authenticated `nerdo-api` domain operations endpoints. The mailbox does not initialize the gateway storage layer, edit `nerdo.json`, move domain directories, or read/write the domain corpus directly.

## Domain resolution

Nerdo resolves the command target in this order:

1. A domain written directly in the command.
2. A domain-bound recipient such as `nerdo+example.com@nerdo.povarchik.com`.
3. A body line: `Domain: example.com`.
4. A domain in the subject.
5. The sender's only managed domain.
6. A clarification response when the sender manages multiple domains.

An email address may manage any number of domains. The sender authorizes the operation; the domain identifies its target.

## Commands

```text
add domain example.com owner@example.com
list domains
status example.com
start example.com
activate example.com
retry example.com
reset example.com
enable example.com
disable example.com
add documents example.com
list documents example.com
attach documents example.com
remove example.com
confirm remove example.com
```

The command may be the first non-empty line of the message or the value of a `Command:` line.

`add domain example.com owner@example.com` is restricted to addresses listed in `NERDO_ADMIN_EMAILS`. It creates and immediately starts a Core intake for `https://example.com`, assigning it to the supplied account email. The command uses spaces only; bars and other separators are not accepted.

`add documents example.com` sends UTF-8 `.md` and `.markdown` attachments to the domain operations API. The API stores them under the deployed domain's `mail-imports/` directory. Retrieval scans Markdown recursively, so the additions become available immediately.

`list documents example.com` requests the relative paths of all Markdown documents from the domain operations API.

`attach documents example.com` requests an API export and replies with those Markdown documents attached. The current combined attachment limit defaults to 20,000,000 bytes and can be changed with `NERDO_MAIL_ATTACH_MAX_BYTES`.

`remove` is deliberately two-step. The first message returns the exact confirmation command. Confirmation calls the API, removes the deployed domain from service, and archives its directory under `users/.removed/<timestamp>/` rather than deleting it.

`reset example.com` is one-step. It calls the API immediately to archive the deployed corpus, remove the domain's Core intake/dataset/conversation state, clear gateway and shared-session state, and create a new queued intake using the existing website URL and owner email. Archives are retained under `data/domain-reset-archive/` and `users/.reset/`.

## Website setup reports

When processing reaches `awaiting_review`, Nerdo retrieves the completed setup report from Core and sends it once per recipient:

- configured reviewers receive the processing report, Chato's corpus summary, dashboard link, and activation command;
- the website owner receives the same report and is asked to reply with corrections, missing information, or questions;
- failed deliveries retry after five minutes, up to three attempts.

The report separates Nerdo's processing facts from Chato's understanding. Chato's summary is required and is later deployed as `knowledge.md`; the combined setup report remains outside the active corpus.

## Domain operations API

The mailbox uses `NERDO_GATEWAY_BASE_URL` and authenticates with `NERDO_OPERATOR_TOKEN` through `X-Nerdo-Key`.

```text
GET  /v1/admin/domains
POST /v1/admin/domains
GET  /v1/admin/domains/{domain}
POST /v1/admin/domains/{domain}/start
POST /v1/admin/domains/{domain}/activate
POST /v1/admin/domains/{domain}/retry
POST /v1/admin/domains/{domain}/reset
PUT  /v1/admin/domains/{domain}/enabled
POST /v1/admin/domains/{domain}/remove
GET  /v1/admin/domains/{domain}/documents
GET  /v1/admin/domains/{domain}/documents/export
POST /v1/admin/domains/{domain}/documents
```

## Start

```bash
docker compose up -d --build --force-recreate nerdo nerdo-api nerdo-mail
docker compose logs -f nerdo nerdo-api nerdo-mail
```

## One-shot diagnostic

```bash
docker compose run --rm nerdo-mail python -c \
'from nerdo_mail.main import run_once; print(run_once())'
```
