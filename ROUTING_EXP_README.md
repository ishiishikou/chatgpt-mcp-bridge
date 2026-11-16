# MCP UI target routing / lifecycle experiment (2026-11-16)

Only fixed harmless messages. Does NOT use retired auto.html or reset its ledger.

- New tools: show_auto_active_probe / show_auto_new_probe.
- Each tool renders a unique URI. After initialize + 15 seconds, if the iframe remains running, it calls app-only claim_routing_probe; one attempt per Run ID; new runs for the same target/OS/visibility can be attempted after a 30-second cooldown. This permits controlled repeats and prevents remount bursts.
- If iOS suspends timer, pagehide, teardown, or the UI is never loaded, it cannot send. No server-originated push or closed-chat wake-up is attempted.
- If host lacks serverTools, message.text or openai/message, it fails closed with a status; the trial does not consume a slot.
- Server uses a new isolated v2 SQLite ledger with permanent per-Run-ID claims and 30-second per-scope cooldown. V1/retired ledger remains untouched. Result 'host_accepted' means host accepted, NOT target/assistant delivery.
- Model/effort cannot be chosen or attested by ui/message. User checks visible model selector and can copy run ID.
- UI local logs may be lost on teardown; app-only telemetry is best-effort.
- All tests harmless, no free-form user text forwarded.

## Isolated offline test
python -m unittest -v test_routing_experiment.py

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
