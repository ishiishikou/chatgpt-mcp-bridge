# MCP UI target routing / lifecycle experiment (2026-11-16)

Only fixed harmless messages. Does NOT use retired auto.html or reset its ledger.

- New tools: show_auto_active_probe / show_auto_new_probe.
- Each tool renders a unique URI. After initialize + 15 seconds, if the iframe remains running, it calls app-only claim_routing_probe; one attempt per Run ID; new runs for the same target/OS/visibility can be attempted after a 30-second cooldown. This permits controlled repeats and prevents remount bursts.
- Hidden/background JavaScript timer throttling, suspension and resumption depend on the host. A retained iframe may attempt sending while hidden or after timers resume on return; suspension alone does not prove that no later attempt can occur. Observed pagehide or teardown cancels the attempt without automatic restart. A UI that never loads cannot initiate it. No server-originated push or closed-chat wake-up is attempted.
- The phase captured at claim must remain unchanged until the message request. A phase transition during the claim wait (including away and back) skips sending while leaving the Run ID consumed; starting and remaining hidden is allowed.
- Explicit JSON-RPC invalid-request / method-not-found / invalid-params errors (-32600 / -32601 / -32602) are classified as host_rejected. Transport failures, timeouts, generic isError results and other ambiguous errors remain unknown. Host error messages, data and raw codes are not logged.
- If host lacks serverTools, message.text or openai/message, it fails closed with a status; the trial does not consume a slot.
- Server uses a new isolated v2 SQLite ledger with permanent per-Run-ID claims and 30-second per-scope cooldown. V1/retired ledger remains untouched. Result 'host_accepted' means host accepted, NOT target/assistant delivery.
- Model/effort cannot be chosen or attested by ui/message. User checks visible model selector and can copy run ID.
- UI local logs may be lost on teardown; app-only telemetry is best-effort.
- All tests harmless, no free-form user text forwarded.

## Isolated offline tests (both required)

```sh
.venv/bin/python -W error::ResourceWarning -m unittest -v test_routing_experiment.py
node test_routing_ui_offline.cjs
```

Tests use temporary isolated ledgers, a virtual clock and a simulated host; no real UI sending or deployment. Regression cases include claim-wait lifecycle/phase races, explicit rejection versus unknown results, lost/late responses, cooldown boundaries and suspended timers before/after return.

## Safe service replacement on RDC (operator-controlled only)
Existing server process lifecycle_server.py + independent running tunnel remain separate.
1. Back up old process PID/start command, and record original SHA256.
2. With tunnel untouched, test routing_experiment_server on a temporary localhost port (e.g. 3098).
3. Stop ONLY lifecycle_server.py on port 3092, start routing_experiment_server.py on 3092 with existing .venv interpreter and original bind address.
4. Verify MCP initialize + tools/list + resources/read, then refresh the ChatGPT plugin catalog.
5. To roll back: stop ONLY routing_experiment_server.py and restart unchanged lifecycle_server.py on 3092. Keep separate routing experiment SQLite for audit, never reset retired dispatch ledger.
6. Do not launch old auto.html or activate MCP_AUTO_SEND_ENABLED.

## Device acceptance sequence
1. Each OS: test foreground active and new, record run IDs, destination conversation and its model selector.
2. Each OS: render a fresh UI for active/new and background the app before 15-second deadline; record whether hidden timer fires, resumes, or is torn down. An additional test may be run in the same visibility status after the cooldown.
3. Full force-quit / app not running: server has no independent ability to launch or send; use logs to identify if any surprising new foreground lifecycle occurs.
4. For OS cross-device tests, compare only observed app instances and receiving chat; no reliable chat-target binding is claimed.
