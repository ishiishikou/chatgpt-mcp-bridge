# Display-only lifecycle probe: iPhone cross-chat validation

Status: **offline implementation complete; real ChatGPT host lifecycle remains unverified.**

## Goal and established observation

On 2026-11-13 a guarded MCP Apps widget requested in Chat A was visibly present in a screenshot from Chat B (the Codex interruption investigation). This demonstrates cross-chat *presentation*, not that a message was sent, nor that JavaScript ran afresh in Chat B.

The legacy automatic-send widget must **never** be run or reopened. Do not reset the existing durable dispatch ledger.

## Completely isolated runtime

Use `lifecycle_server.py` instead of `server.py` on localhost port 3092. It registers exactly one tool: `show_lifecycle_probe`. It does not import `dispatch_store`, issue a claim, register a sending helper, or accept an external event. The HTML `lifecycle-display-v1.html` contains no sending or server-tool request API, no timer, no fetch/XHR/WebSocket, and no external assets.

The standard Secure MCP Tunnel profile can point to this isolated server. The plugin may need its tool definitions refreshed; do not treat a visible item in ChatGPT's model-facing tool menu as proof of successful iframe rendering.

The diagnostic displays:
- Random per-JavaScript-instance identifier and mount UTC timestamp
- Host `ui/initialize` result and boolean capability summaries (never full host metadata)
- Visibility changes and page show count (only within that instance)
- Teardown notification receipt

It does **not** send telemetry or data back to the MCP service; screenshots taken in each tested chat are the observation mechanism.

## Offline reproduction

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -W error::ResourceWarning -m unittest -v test_dispatch_store.py test_lifecycle_server.py
node test-guarded-ui-offline.cjs
node test-lifecycle-ui-offline.cjs
```

## Host validation (controlled and bounded)

1. Verify the original dispatch ledger is `not_claimed`, leave `MCP_AUTO_SEND_ENABLED=0`, and confirm the old chat is not reopened.
2. Start **only** `lifecycle_server.py` (not `server.py`) and the dedicated tunnel. Check `tools/list` returns exactly one `show_lifecycle_probe` and no sending tool.
3. When the new tool is present in a fresh normal ChatGPT chat (Chat A), invoke it **once**, and record its UI instance ID, mount time, init result and visibility counters.
4. Without invoking any MCP tool in Chat B, switch to an existing *safe* chat (not the retired-auto chat). Record whether the lifecycle UI appears there and, if so, its instance ID and time. Do **not** press any buttons or copy/paste event payloads.
5. Switch back to Chat A. Record the instance ID and counters again. If reappearing or remounting, observe and document before any real-message test.
6. Stop the isolated server and tunnel; verify the original dispatch ledger has not changed.

Expected interpretation:
- Same ID + same mount time visible across two chats: likely one iframe/view or snapshot presented across chats; this is not proof of fresh execution in Chat B.
- Different ID/time: evidence that the JS instance was regenerated; not proof of cross-chat message delivery.
- No UI in Chat B: absence of reproduction under the observed conditions, not proof the host never reuses it.
- Host capabilities and initialization only indicate what the iframe observed; they do not prove successful calls to server-side tools.

**No user action is needed for implementation and offline tests.** If the model cannot operate the ChatGPT iPhone app to collect UI facts, the user must supply screenshots or per-chat identifiers after the above live display-only test is ready. Avoid requiring user interactions prematurely.

## Release gate

Do not enable the guarded sender until cross-chat UI isolation/remount behavior and server-side claim authorization are verified. Any future live message attempt needs separate explicit permission, a fresh bounded event, and a safety review. Follow `SAFETY.md` and issue #2.
