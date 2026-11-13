# External-AI event queue: offline design / Issue #4

Status: **research prototype only; NOT wired into a tunnel, server.py, MCP tools, ChatGPT or external AI**.

This module is intentionally independent of the existing `dispatch_store.py` and PR #7's `response_receipts.py`. It provides a safe stepping stone for receiving one externally originated event, **not an actual `ui/message` sender** or a background trigger.

## Bounded state machine

```text
verified operator authorization (one event, owner + session + source, TTL <= 300s)
    -> authorized
verified external source queues <= 768 UTF-8 bytes
    -> queued
verified host claims once
    -> claimed (irreversible, token returned once)
host reports host_accepted | host_rejected | unknown
    -> terminal (no retries)
```

The operator can cancel an `authorized` or `queued` event. After `claimed`, cancellation cannot undo an in-flight operation. Expired authorized/queued events cannot be enqueued or claimed. No automatic retries occur. A single event is the **lifetime global cap for this database**; cancellation/expiry does not replenish it. This is intentional for a first offline prototype; any multi-turn protocol requires a new safety review, version and explicit approval.

## Identity and consent boundaries

- `VerifiedEventIdentity` carries a server-verified `subject`, event `owner`, `session`, `source` and immutable `scopes`. **These MUST be derived by an integrating server from trustworthy authenticated transport context.** Constructing the value from model arguments, ChatGPT text, HTML, an unverified header or a user-supplied object is not secure.
- A separately approved operator invokes `authorize` with `bridge:event:authorize` scope. The event's owner/session, permitted source and expiration are recorded first.
- The external agent uses `bridge:event:enqueue`; its `source` is derived from verified identity, not from the event text. A different source, user or chat session is rejected.
- The host uses `bridge:event:claim` once, receives a unique claim token and the bounded message; `bridge:event:report` records host acceptance/rejection/unknown only. `bridge:event:read` returns status metadata without the payload or token. `bridge:event:cancel` is restricted to pre-claim.
- `enabled=False` by default. Explicit DB provisioning is separate from runtime calls and requires process-owned 0700 directory and 0600 DB. Missing database, relaxed permissions, symlinks and identity resolver failures are denied.

`host_accepted` is **not proof of an assistant reply or external delivery**. The model-mediated fixed response receipt from PR #7 is a *separate* unverified step.

External event messages are untrusted input. A future UI must conspicuously mark them as external, must not obey embedded prompts to exfiltrate context, and must not forward private conversation content to external agents without separate user consent.

## Offline verification

```sh
python -W error::ResourceWarning -m unittest -v test_event_queue.py
```

No network calls, real DB, timers, process start, browser navigation, MCP servers or actual ChatGPT messages are made. Tests use temporary permission-restricted databases and cover duplicate/concurrent claims, owner/session/source mismatches, expiry, cancellations, replay, missing ledger, symlink, and wrong permissions.

## Deployment gates, NOT satisfied

1. Verify real MCP OAuth / signed context and narrowly scoped service identities, browser origin and chat-session binding. A trusted resolver is not yet implemented. No public auth endpoints.
2. Decide the user-visible per-event authorization UX and exact event text boundaries. Do not authorize `ui/message` automatically.
3. Reconcile this queue's claim with the existing global send-claim ledger (without resetting either) and the separately bounded response receipt ledger from PR #7.
4. Demonstrate the guarded widget in the real normal ChatGPT chat **without sending**, then obtain separate permission for one real send with a fresh correlated event.
5. Prove assistant-response receipt independently using the *explicit* model-visible MCP callback (no passive transcript access).
6. Determine foreground/background behavior on iPhone/Windows. Official MCP Events are currently documented for Work chats and dots, not general normal chat. https://developers.openai.com/plugins/build/mcp-events
7. Limit future multi-AI loops by rounds, bytes, elapsed time, cost, cancellation and an owner-visible audit trail. This prototype intentionally cannot run a loop.

This PR must not be mistaken for authorization to perform an external-AI loop, to enable sending, or to merge to production automatically.