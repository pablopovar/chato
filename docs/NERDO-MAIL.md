# Nerdo local mailbox adapter

Temporary inbound transport for the Thunderbird-managed mailbox at:

```text
/home/pablo/.thunderbird/f0pc3s1x.default/ImapMail/hq3-mx-10.povarchik-2.com/INBOX/cur
```

The folder is mounted read-only at `/mailbox`. Nerdo scans only stable `*.eml` files in `INBOX/cur`, claims each message in `data/nerdo-mail.sqlite3` before executing it, and never moves or modifies Thunderbird files.

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
add domain example.com|owner@example.com
list domains
status example.com
start example.com
activate example.com
retry example.com
enable example.com
disable example.com
add documents example.com
list documents example.com
attach documents example.com
remove example.com
confirm remove example.com
```

The command may be the first non-empty line of the message or the value of a `Command:` line.

`add domain example.com|owner@example.com` is restricted to addresses listed in `NERDO_ADMIN_EMAILS`. It creates and immediately starts a Core intake for `https://example.com`, assigning it to the supplied account email.

`add documents example.com` accepts UTF-8 `.md` and `.markdown` attachments and stores them under the deployed domain's `mail-imports/` directory. Retrieval scans Markdown recursively, so the additions become available immediately.

`list documents example.com` returns the relative paths of all Markdown documents in the active domain corpus.

`attach documents example.com` replies with those Markdown documents attached. The current combined attachment limit defaults to 20,000,000 bytes and can be changed with `NERDO_MAIL_ATTACH_MAX_BYTES`. A download link will replace direct attachments later.

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
