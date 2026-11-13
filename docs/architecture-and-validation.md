# Architecture and validation plan (2026-11-13)

## Scope and non-goals

**Target:** communicate with the **existing ChatGPT normal conversation** via MCP Apps, without replacing that conversation with an OpenAI API-based conversation.

Do not assume that an MCP widget can read assistant turns or remain active after the conversation is closed. The web UI, iPhone, Windows desktop and background lifecycle must be tested independently.

## Confirmed interfaces vs unverified capabilities

| Interface / behavior | Status | Important boundary |
| --- | --- | --- |
| `ui/message` from a rendered MCP App to the conversation | Previously observed on a ChatGPT client | The host's acceptance is not proof that the assistant response was delivered to the MCP server |
| `tools/call` from MCP App via host to same MCP server | Defined in MCP Apps | `hostCapabilities.serverTools` must be checked in the actual ChatGPT widget |
| App-only helper tools | Implemented locally | Must not be confused with model-visible tools |
| Passive MCP-server observation of ChatGPT assistant messages | **Not demonstrated** | No automatic assistant-text callback is established by the existing `ui/message` flow |
| MCP Events for server-originated triggers | Documented by OpenAI | Currently supported in Work chats and dots; **not evidence of normal-chat background triggers** |

Official references:
- https://developers.openai.com/plugins/build/chatgpt-ui
- https://apps.extensions.modelcontextprotocol.io/api/documents/overview.html
- https://developers.openai.com/plugins/build/mcp-events
- https://developers.openai.com/plugins/build/auth

## Conceptual two-way architecture (not yet implemented)

```text
Muse / Codex / other AI
    -> authenticated event queue in MCP service
    -> guarded MCP App (only while loaded in an eligible normal chat)
    -> claim_once in durable SQLite ledger
    -> ui/message to current ChatGPT conversation
    -> ChatGPT assistant response
    -> OPTIONAL model-invoked, explicitly described MCP "report" tool
    -> authenticated result store / consumer
    -> external AI
```

The last three steps are **unverified**. A model-visible tool with a fixed correlation ID could potentially record a deliberately short acknowledgement or structured result when the assistant chooses to call it. That is an assistant-mediated callback, **not** passive access to chat history, and should be marked best-effort until end-to-end tests demonstrate otherwise.

A widget's `ui/notifications/tool-result` concerns an MCP tool result, not an arbitrary assistant message. It is not evidence of assistant-response capture.

## Safety properties required before multi-turn operation

1. Do not reuse retired `auto.html`; do not open the old chat with a cached widget.
2. Keep `MCP_AUTO_SEND_ENABLED` off until an explicitly approved, single-shot test.
3. Keep the global one-claim limit and persistent ledger; treat host timeout as `unknown`, never retry automatically.
4. Before exposing a remotely reachable server, implement authentication and authorization, session/owner binding, origin and callback allowlists, TTL, replay protection, event IDs and per-tenant quotas.
5. Do not forward full chat history or arbitrary user data to an external AI. Require explicit boundaries and clear user approval for any exported content.
6. Cap AI-to-AI exchanges by round count, bytes, elapsed time, and cost; allow immediate cancellation and an audit trail.
7. Use an independent, new test event/version for any revised protocol; do not erase/refresh the existing production dispatch ledger to enable retries.

## Validation gates (each requires evidence)

- **Gate A — Offline:** unit tests + real widget JS in simulated host. Completed: Python 12, JavaScript 5.
- **Gate B — Discovery:** new `show_guarded_probe` listed in current ChatGPT tool inventory. Observed in a chat on Nov 13. Exact plugin/account/project and persisted availability must still be validated.
- **Gate C — Rendering only:** run a new guarded widget with sending disabled, confirm *actual MCP iframe* appears on iPhone/Windows, capture capability result and teardown behavior. Requires separate explicit user authorization before enabling server/tunnel.
- **Gate D — One-shot send:** inspect durable ledger and opt-in flag, then permit **one** real attempt. Confirm UI initialization, claim, host result, user message and assistant response separately. Never reset/retry an ambiguous attempt.
- **Gate E — Response receipt:** test whether a purposely model-invoked response-recording tool is available and called. Its success must be checked on the server, not inferred from the chat display.
- **Gate F — External loop:** event queue, per-session authorization, independently capped rounds, cancellation, stale-event handling, and multi-device lifecycle verification.

## Open decisions

- Can any documented API return assistant-generated text to the MCP server in a **normal** ChatGPT chat without explicit model tool invocation? Not established.
- Will MCP Apps UI remain active or reinitialize after a mobile app is backgrounded or the chat is reopened? Not established.
- Does the production host expose `serverTools` in a guarded widget? Not established.
- How does the plugin refresh action catalog after changing server tools? The current chat lists the new tool; this is not a global cache invalidation guarantee.

Production release is **blocked** until security hardening and documented live end-to-end acceptance tests are completed.
