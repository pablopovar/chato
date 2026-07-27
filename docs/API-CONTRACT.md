# Chato & Nerdo API contract v1

## Boundary

- **Chato** is the public-relations conversation.
- **Nerdo** is the owner-authorized Nerding Operations surface.
- Chato can explain, answer, and continue a public conversation.
- Nerdo can inspect or change website knowledge and technical state.
- Visitor messages never receive Nerdo permissions.

## Authentication

| Credential | Header | Use |
|---|---|---|
| Site token | `X-Site-Token` | Read one submitted site's status and use its site-specific Chato conversation. |
| Nerdo operator key | `X-Nerdo-Key` | Run owner/technical operations. |
| Core credentials | Server-side only | The gateway talks to Nerdo without exposing its admin or bot secrets to the browser. |

The `X-Nerdo-Key` is the initial bridge credential. Replace it with the eventual customer identity and authorization system without changing the resource contract.

## Required customer operations

| Customer request | Direct endpoint | Conversational invocation |
|---|---|---|
| Update website sources | `POST /v1/sites/{site_id}/sources/refresh` | “Nerdo, update the website sources.” |
| Show what changed | `POST /v1/sites/{site_id}/sources/changes` | “Nerdo, show me what changed.” |
| Connect WordPress | `POST /v1/sites/{site_id}/integrations` | “Nerdo, connect WordPress.” plus `target_url` and `widget_script_url` context |
| Find contradictions | `POST /v1/sites/{site_id}/knowledge/contradictions` | “Nerdo, find contradictions.” |
| Diagnose an incorrect answer | `POST /v1/sites/{site_id}/answers/diagnose` | “Nerdo, diagnose this incorrect answer.” plus `question` and `answer` context |
| Verify installation | `POST /v1/sites/{site_id}/integrations/{integration_id}/verify` | “Nerdo, verify the installation.” plus `integration_id` context |

## Operation model

Potentially long-running actions return an operation:

```json
{
  "operation_id": "op_...",
  "site_id": "site_...",
  "kind": "sources.refresh",
  "status": "accepted",
  "result": {},
  "error": null,
  "created_at": "...",
  "updated_at": "..."
}
```

Poll:

```http
GET /v1/operations/{operation_id}
X-Nerdo-Key: ...
```

Terminal statuses are `completed`, `failed`, `blocked`, and `needs_input`.

## Conversation model

Create a conversation:

```http
POST /v1/conversations
Content-Type: application/json

{
  "persona": "chato",
  "site_id": null
}
```

Send a message:

```http
POST /v1/conversations/{conversation_id}/messages
Content-Type: application/json

{
  "content": "Ask Chato how it works",
  "context": {}
}
```

Nerdo uses the same message resource but requires `X-Nerdo-Key`. The response may include an `operation_id` or `needs_input` fields.

## Core bridge

The implementation maps to the currently documented Nerdo API:

- `POST /intakes`
- `GET /intakes/{intake_id}`
- `POST /admin/intakes/{intake_id}/retry`
- `GET /admin/intakes/{intake_id}/dataset/documents`
- `GET /admin/intakes/{intake_id}/dataset/search`
- `GET /bots/{domain}`
- `POST /chat`
- `GET /conversations/{session_id}`

The gateway adds the missing product-level resources:

- Chato/Nerdo persona boundary;
- source snapshots and change comparison;
- possible-conflict analysis;
- answer-support diagnosis;
- corrections queue;
- integration records and embed generation;
- installation verification;
- operation polling;
- browser client functions.

## Verification limits

- Contradiction findings are explicitly classified as **possible conflicts** and require review.
- Answer diagnosis is a lexical support audit, not a final factual judgment.
- Generic web, WordPress, and Joomla installations can be checked for embed markers.
- Slack, WhatsApp, SMS, Notion, and email resources exist in the contract, but their provider authorization and verification adapters remain required.
