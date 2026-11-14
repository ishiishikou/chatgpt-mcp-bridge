# Thread host capability diagnostic (offline-only)

Build `capdiag-20261114-v1` is an **isolated, strictly display-only** follow-on to the merged v1 Thread Entrypoint. It provides a separate MCP server with exactly one UI tool and a new immutable resource URI. No existing tool, send flag, dispatch ledger, tunnel, or event queue is modified.

## Purpose

Show the `ui/initialize` capability advertisements on a host, specifically `hostCapabilities.experimental["openai/message"]`, `message.text`, and `serverTools`, without invoking `ui/message` (including `send:false`), creating any draft, or requesting a server tool. The build ID appears in the HTML and model-visible tool return. The per-mount instance ID and UTC time are UI-local and **not** authenticated thread identities.

A missing capability is reported only as **not advertised**, never as a proof that the host permanently lacks support. Malformed responses are rejected. Raw capability or hostContext values are never displayed; only a limited list of context *keys* is rendered. Capability discovery is not authorization to send.

The authoritative schemas are [MCP Apps Stable 2026-01-26](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx) and the OpenAI [`ui/message` capability advertisement](https://github.com/openai/mcp-extensions/blob/main/docs/spec.md). They document the presence-only `experimental["openai/message"]: {}` and `message.text: {}` descriptors. These are strictly feature advertisements, not proof of send rights or chat identity.

## Explicit limits

- **No recipient proof:** a per-thread iframe and any capability flags do not attest an existing normal-chat recipient or atomically bind a message to it.
- **No live deployment or send approval** follows from offline tests. Do not run the retired `show_auto_probe`, alter the preexisting services/tunnels, clear ledgers, or enable auto-send.
- A real ChatGPT Thread Entrypoint test requires a plugin refresh/registration on the intended client. Observe iPhone and Windows separately, including different chats and reopen behavior, without invoking legacy sender UI.
- A host's old cached widget is not changed by this new bundle. Treat unexplained cross-chat appearances as a blocking finding.

## Offline acceptance

1. Python tests check that the declared server has exactly one UI-only tool, excludes known dispatch/receipt/queue imports, and performs no observed file mutation on an isolated import.
2. Node tests run the **actual embedded JavaScript** under synthetic host messages, malformed responses, mount-correlation, replay, teardown and repeated events. They check exact emitted RPC methods, and check forbidden outbound calls are absent in that harness. No simulation establishes all runtime/browser behavior.
3. Existing main tests and hard fail-closed `claim_guarded_probe` regression remain passing with the opt-in flag unset, `0`, and `1` in an isolated test environment.
4. Optional localhost MCP `tools/list`, `tools/call`, and `resources/read` protocol tests use a private ephemeral port and are shut down afterward. They are not equivalent to actual ChatGPT host verification.