# Chato chat traces

Chat tracing is a per-domain diagnostic mode for the active Chato request path.

## Enable

Open the domain dashboard, select **Configuration**, enable **Record full chat traces**, and save. This persists:

```json
{
  "debug": true
}
```

in the domain's `nerdo.json` configuration.

When `debug` is absent or false, no trace file is created.

## Captured stages

Each authenticated chat request records, in execution order:

1. request metadata and the non-secret domain configuration;
2. conversation lookup or creation, history reads, message writes, and the final conversation update;
3. prior conversation history and the retrieval query;
4. normalized query tokens;
5. every positively scored retrieval candidate, including full chunk text;
6. the selected retrieval results;
7. the assembled system prompt, website context, and complete model message payload;
8. the OpenAI-compatible model endpoint, request parameters, status, response headers, and response body;
9. a full exception traceback when the model or request fails;
10. any extractive fallback decision and its source paths;
11. the final Chato API response and response mode.

Bot keys and model-provider API keys are never written. The trace records only whether each key was configured.

## Storage

By default, Core stores traces under its persistent data directory:

```text
data/chat-traces/<domain>/<sha256-session-id>/<request-id>.json
```

Set `NERDO_CHAT_TRACE_DIR` to override the root directory.

The session identifier is hashed in the filesystem path. The original session identifier remains inside the protected trace document so the export can be correlated with conversation history.

## Download

In **Past chats**, conversations with recorded traces display **Download Trace**. The download is a JSON bundle containing every request trace for that conversation in chronological order.

The browser route proxies the protected Core admin endpoint:

```text
GET /dashboard/api/domains/{domain}/conversations/{session_id}/trace
```

Core serves the underlying export only through its admin-token-protected endpoint. The response uses `private, no-store` caching.

## Operational warning

A trace contains complete retrieved source chunks, prompts, conversation history, model payloads, model responses, SQL statements and values used by the chat lifecycle, and exception details. Enable it only while diagnosing a domain and disable it afterward. Disabling tracing stops new trace creation; existing trace files remain until removed from the Core data directory.
