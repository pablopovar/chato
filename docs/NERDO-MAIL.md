# Nerdo local mailbox adapter

Temporary inbound transport for the Thunderbird-managed mailbox at:

```text
/home/pablo/.thunderbird/f0pc3s1x.default/ImapMail/hq3-mx-10.povarchik-2.com/INBOX/cur
```

The folder is mounted read-only at `/mailbox`. Nerdo scans only stable `*.eml` files in `INBOX/cur`, records their SHA-256 digests in `data/nerdo-mail.sqlite3`, and never moves or modifies Thunderbird files.

## Domain resolution

Nerdo resolves the command target in this order:

1. A domain-bound recipient such as `nerdo+example.com@nerdo.povarchik.com`.
2. A body line: `Domain: example.com`.
3. A domain in the subject.
4. The sender's only managed domain.
5. A clarification response when the sender manages multiple domains.

An email address may manage any number of domains. The sender authorizes the operation; the domain identifies its target.

## Commands

```text
Command: list domains
Command: status
Command: start
Command: activate
Command: retry
Command: enable
Command: disable
Command: add documents
Command: remove
Command: confirm remove
```

For state-changing commands, include:

```text
Domain: example.com
```

`start` approves a `pending_approval` submission by domain and creates the Core intake. `activate` turns an `awaiting_review` intake into an active domain. `add documents` accepts UTF-8 `.md` and `.markdown` attachments and stores them under the deployed domain's `mail-imports/` directory. Retrieval already scans Markdown recursively.

`remove` is deliberately two-step. The first message returns the exact confirmation command. Confirmation removes the deployed domain from service and moves its directory under `users/.removed/<timestamp>/` rather than deleting it.

## Start

```bash
docker compose up -d --build --force-recreate nerdo-mail
docker compose logs -f nerdo-mail
```

## One-shot diagnostic

```bash
docker compose run --rm nerdo-mail python -c \
'from nerdo_mail.main import run_once; print(run_once())'
```
