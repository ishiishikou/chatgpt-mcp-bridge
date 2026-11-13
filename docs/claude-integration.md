# Muse-side integration for the ChatGPT MCP Bridge (offline preparation)

Status: **offline implementation + offline verification complete; live
integration NOT started.** Nothing in `claude_client/` connects to ChatGPT,
starts a server or tunnel, enables sending, or touches the existing dispatch
ledger. Related: Issues #1 and #4, PR #7 (response receipts), PR #9 (event
queue), `docs/architecture-and-validation.md` (Gates A–F).

## What was built

`claude_client/` contains the external-AI (Muse) side of the architecture
diagram in `docs/architecture-and-validation.md`:

| File | Role |
| --- | --- |
| `client.py` | `EventClient` + durable local `Outbox`. One enqueue attempt per event; ambiguous results become `enqueue_unknown` and are never resent automatically. |
| `identity.py` | Server-side identity resolvers. Identity is derived only from transport context: a bearer token resolved against an operator-provisioned registry (SHA-256 digests, file mode 0600), or Tailscale peer metadata (user + device) resolved against an operator-provisioned mapping. |
| `shim.py` | `BridgeShim`: the narrow server-side surface a client may call — `enqueue`, `cancel`, `status`, `receipt_status`. `authorize`, `claim`, `report` are deliberately not exposed. Payloads carrying identity fields are rejected fail-closed. A JSON-lines stdio host loop rejects any request line that tries to inject a `context`. |
| `test_claude_client.py` | 14 offline tests against a faithful in-memory double of the PR #9 queue semantics. |
| `test_event_queue_integration.py` | 2 integration tests against the real `event_queue.py` from PR #9 (skipped until that module is importable, e.g. after PR #9 merges). |
| `test_receipt_integration.py` | 1 integration test against the real `response_receipts.py` from PR #7 (prepare -> record fixed ack -> client reads `received`; a different principal is rejected). |

## Flow

```text
Muse (this client)                Bridge host (operator-controlled)
-----------------                   -------------------------------
prepare(message) -> event_id
        |  event_id communicated to the operator out of band
        |                           authorize(event_id, source="muse", TTL<=300s)
        |                             [trusted operator step; binds owner/session/source]
send(event_id)  ---- enqueue ---->  OfflineEventQueue.enqueue
        |                             (identity from transport context only)
        |                           guarded UI claims once -> ui/message
        |                           host result reported (host_accepted/...)
status(event_id) - - read-only - -> queue status
receipt(event_id) - read-only - -> PR #7 receipt status (fixed ack phrase)
cancel(event_id) ---- before claim only; after claim it is too late
```

## Design decisions

- **Transport reuse.** The client talks to a shim, not to ChatGPT. Preferred
  live transport is the already-established Tailscale path (Muse VM ->
  rdc host), because Tailscale gives a verified peer identity (node + login)
  that the resolver can bind without trusting payload data; SSH to the
  rdc-codex container was verified end-to-end on 2026-11-13. The Secure MCP
  Tunnel remains an alternative, but its identity context would have to come
  from the tunnel/host layer, never from tool arguments (same rule as
  PR #7's `response_receipt_adapter.py`).
- **Authentication.** Only a caller whose transport context resolves to the
  provisioned Muse identity (source `muse`, scopes
  `bridge:event:enqueue|cancel|read`) can enqueue/cancel/read. Authorization
  (creating the `authorized` row) stays a separate operator step, so a
  compromised client alone cannot mint sendable events.
- **Deduplication.** Three layers: the client generates one event_id per
  prepared message and claims the single attempt in its own outbox with an
  atomic write-ahead transition (`prepared -> attempting`) *before* the
  transport call, so a crash mid-send or a concurrent caller cannot produce
  a second attempt; the queue keys rows by event_id primary key; claim is
  at-most-once in the durable bridge ledger. A second `send()` for the same
  event_id returns `already_attempted` without any transport call. (The
  write-ahead ordering was added after an independent Codex review pointed
  out that recording the outcome only after the transport call left a
  crash/concurrency hole; regression tests cover both cases.)
- **Expiry and cancellation.** TTL is bounded by the queue (<= 300 s);
  expired events cannot be enqueued or claimed. Cancellation works only in
  `authorized`/`queued` states; after a claim, cancellation is impossible
  and the client reports that honestly instead of pretending recall.
- **Unknown results.** Transport exceptions and ambiguous bridge failures
  are recorded as `enqueue_unknown`. There is no automatic retry path in
  the code; resolution is a human decision, matching the repo's rule for
  host timeouts.
- **Response receipt.** Read-only polling of the PR #7 receipt store. A
  receipt means the fixed acknowledgement was recorded via the
  model-mediated tool call; it is best-effort evidence, not a delivery
  guarantee, and it never exports chat history.

## Verification (2026-11-13, offline)

- `python -m unittest discover -s claude_client`: **14 unit tests passed**
  (Muse VM; happy path, no-retry after rejection/unknown, crash-after-send
  and concurrent-send regression tests, forged-token and payload-identity
  rejection, client-surface restrictions, cancel timing, expiry, global
  cap, byte limit, stdio context injection, Tailscale resolver binding).
- With the real PR #9 `event_queue.py` and PR #7 `response_receipts.py`
  (run inside the rdc-codex container against temporary SQLite ledgers):
  **17 passed**, including all three integration tests (queue full flow;
  source binding rejects a `codex`-bound identity enqueueing a
  `muse`-authorized event; receipt round trip with principal binding).

## What is still missing before any live step (Gate F)

1. A hosting decision for the shim (SSH forced-command vs. local socket vs.
   tunnel-exposed endpoint) plus the Issue #1 security review. The shim
   must not be exposed on an unauthenticated public endpoint.
2. User-visible per-event consent for `authorize` (currently an operator
   API call; the live flow needs the user to see and approve each event).
3. Correlation between the event queue, the existing dispatch ledger's
   `claim_once`, and the PR #7 receipt module inside `server.py` — owned by
   the bridge side, not this client.
4. Gates B–E evidence (real rendering, one-shot send, receipt) under
   separate explicit user authorization. This client changes none of those
   gates.
5. Multi-round operation needs a new reviewed protocol (the queue's global
   cap is one event); this client intentionally implements single-shot only.

## Explicit non-actions

No `show_auto_probe` execution, no automatic send to any ChatGPT chat, no
`MCP_AUTO_SEND_ENABLED` change, no dispatch-ledger reset or read, no merge.
