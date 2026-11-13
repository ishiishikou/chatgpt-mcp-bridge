# MCP Bridge — acceptance and autonomous-work gates

Status: **offline research in progress; not an end-to-end normal-chat bridge**.

The requested goal is a two-way, bounded connection between a **preexisting normal ChatGPT chat** and external agents. Offline unit tests and successful CI never prove that the host UI actually rendered, that a message was delivered, or that an assistant response was received. This document makes the remaining work explicit rather than declaring completion merely because no human action has been requested.

## Acceptance matrix

| Issue / gate | Confirmed local evidence | Missing evidence | User operation / external dependency |
| --- | --- | --- | --- |
| #2 — guarded UI rendering | `show_guarded_probe` returned a tool result in a prior chat; mocked host scenarios exercise the real UI source | Actual iframe, capabilities, lifecycle on iOS and Windows; whether it is isolated to one chat | An authorized observation of host UI; no automatic send is necessary |
| #2 — safe teardown / no duplicate send | Offline negative cases: missing capability, forged window event, teardown, false claim, failed claim | Real host backgrounding/reopen semantics | Host lifecycle access; **do not open cached retired auto widget** |
| #3 — response receipt | PR #7 SQLite fixed-phrase recording, trusted-context adapter, offline negative tests | Actual model-visible MCP tool invocation in a normal chat, verified authenticated principal and consumed dispatch claim binding | Explicit authorization for one real test and verified transport auth |
| #3 — passive response capture | There is no passive capture in existing code | Documented general normal-chat assistant response callback, if any | Requires separate evidence; `ui/notifications/tool-result` is NOT evidence |
| #4 — external trigger | One-shot durable dispatch gate exists; automatic sending currently disabled | Authenticated external event queue, fixed per-owner session, caps on rounds/bytes/time, cancellation and host availability | Complete security design and independent authorization before any active trigger |
| GitHub source release | PR #5 review runner, PR #6 separate lifecycle, PR #7 response receipt prototype; CI reported passing | All PRs merged/released; full deployment audit | PR merge is a separate decision; do not conflate with test success |

The local test file `test_host_boundary_offline.cjs` is an **offline simulation of the actual guarded-probe.html source**. It cannot establish that a ChatGPT app executes the iframe, or how the app behaves after switching chats. No server, tunnel, scheduling, database mutation, or outgoing message is used by this test.

## Explicit closure criteria for delegated work

After an implementation pass, create a concrete ledger with every acceptance criterion in exactly one of these states:

- **Completed, with evidence:** linked code, passing tests or real host observation, and the exact limitations of that evidence.
- **Executable without user action:** a specific safe next step. **This is a queue item to execute, not a reason to end a session.**
- **Blocked by explicit action/permission:** name the precise user approval, credential, unavailable host capability, or forbidden operation. Do not invent a requirement for manual action when a tool can do it safely.

The implementation agent must run a separate bounded Codex `gpt-6.1-sol/max` review with `stdin=DEVNULL`, `read-only` sandbox, and verify exit 0 **plus** `turn.completed` and actual model settings in session metadata. It must then execute any accepted independent safe work, rerun tests, and perform the review again. A model's statement `no further action required` **does not override** a known unimplemented acceptance criterion. Stop only when no safe, independent tasks remain and all remaining blocks are documented with precise dependencies.

Do not have the assistant infinitely delegate to itself: cap independent reviews and prefer actionable, evidence-backed fixes. If a review cannot finish within its configured timeout, report it as incomplete rather than treating the outcome as approval.

## Forbidden operations for this validation run

Do not run `show_auto_probe`, open the retired auto-send chat, enable `MCP_AUTO_SEND_ENABLED`, reset or increase the live dispatch ledger, change live tunnel credentials, disclose runtime state or chat histories, or register offline research handlers as production MCP tools. This independent PR is limited to tests and documentation.