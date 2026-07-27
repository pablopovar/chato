# Chato & Nerdo API v1

A complete product-level API contract and reference gateway for the current homepage interface.

- **Chato:** Public Relations
- **Nerdo:** Nerding Operations

The gateway keeps the public conversation simple while exposing explicit, owner-authorized operations behind it.

## Implemented calls

- Create a website intake.
- Read website processing status.
- Create Chato or Nerdo conversations.
- Send and list conversation messages.
- Update website sources.
- List prepared website sources.
- Capture and compare source snapshots.
- Flag possible contradictions.
- Diagnose an answer against retrieved source evidence.
- Record answer corrections for review.
- Create, update, list, remove, and verify integrations.
- Generate generic-web, WordPress, and Joomla embed code.
- Represent Slack, WhatsApp, SMS, Notion, and email adapters without pretending they are connected.
- Poll long-running operations.

## Files

```text
nerdo_api/     FastAPI gateway and Core bridge
web/nerdo-api.js   Browser client with every API function
web/chato-nerdo-chat-controller.js  `/#chat` controller
docs/API-CONTRACT.md     Human-readable contract
openapi.json             Generated OpenAPI 3.1 contract
tests/test_contract.py   Contract and operation tests
```

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
set -a; source .env; set +a
uvicorn nerdo_api.main:app --host 0.0.0.0 --port 3410
```

The current core remains at `NERDO_CORE_BASE_URL`. The gateway should be proxied under `/api` for the homepage client.

## Test

```bash
python -m unittest discover -s tests -v
```

or:

```bash
pytest -q
```

## Current bridge boundaries

The implementation is real, but not every provider is connected:

- website-source refresh uses the existing Core retry workflow;
- source changes are computed from gateway snapshots;
- contradiction detection is conservative and review-oriented;
- answer diagnosis measures source support, not absolute truth;
- web/CMS installation verification checks public embed markers;
- Slack, WhatsApp, SMS, Notion, and email require provider-specific adapters;
- the temporary `X-Nerdo-Key` must eventually be replaced by customer identity and authorization.


## Container deployment

```bash
cp .env.example .env
# Edit secrets and Core URL.
docker compose up -d --build
curl -sS http://127.0.0.1:3410/health
```

Add `apache-chato-nerdo-proxy.conf` to the website virtual host, enable `proxy`, `proxy_http`, and `headers`, then place the two JavaScript files in the website's static `/js/` directory. The exact homepage hooks are documented in `docs/HOMEPAGE-WIRING.md`.
