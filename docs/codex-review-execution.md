# Codex CLI review execution (2026-11-13)

This is an **operator-side diagnostic**, not a ChatGPT MCP message-delivery mechanism. It does not run MCP UI tools. The real application must remain disabled for automatic sends unless a separately approved one-shot test is underway.

## Observed failure modes

- **Stdin wait:** With a prompt argument and non-TTY stdin, Codex CLI 0.167.1 may print `Reading additional input from stdin...` and wait for EOF. Avoid inheriting an open pipe from RDC. The runner uses `stdin=DEVNULL`, so EOF is immediate.
- **Nested sandbox failure:** `bwrap: No permissions to create a new namespace`; independent `unshare --user --map-root-user true` returns `Operation not permitted` in this WSL2/Docker environment. No privileged/sandbox-bypass flag is authorized. For a text-only review, explicitly inline **approved nonsecret source** in the prompt and forbid tools. Do not claim shell tools work.
- **Aborted turn:** Stored Codex turn history for 2026-11-13 includes `turn_aborted(reason=interrupted)`. The observed review was interrupted after about 378 seconds. This confirms the Codex turn was interrupted, not **who** sent the interrupt. The process controller's original timeout/signal decision was not independently recoverable from the retained Codex history.
- **Long-running review:** A 31.7k-character all-at-once review failed to produce an answer within the runner's 180-second limit (explicit local timeout). A scoped 8k-character independent review completed in 113.686 seconds with exit 0 and a final message. This is task-specific evidence, not an SLA or universal token/time limit.

## Run a bounded code review

1. Put only approved, nonsecret code and precise instructions in an ignored file, for example `.review-runs/review.txt`. Never include `.secrets/`, tunnel profiles, keys, live SQLite databases, session credentials or private chat text.
2. Execute from this repository:

```sh
python3 scripts/run_codex_review.py --prompt-file .review-runs/review.txt --label security --timeout 150
```

3. Check the private `.review-runs/YYYYMMDDTHHMMSSZ-label/` directory. `metadata.json` records the requested model/effort, process exit, wrapper timeout/forced kill, last-message presence, observed turn completion, and (when session artifacts exist) actual Codex model/effort/sandbox/approval. The raw JSONL events and stderr are kept locally, not committed. The runner returns success **only if** `return_code=0`, `final_message_present=true`, `turn_completed=true`, and `settings_verified=true`. These are checked independently of the requested CLI arguments. Run directories include unique suffixes so equal-time invocations cannot collide.
4. For any `wrapper_timeout_sigint`/`wrapper_timeout_sigkill`, do **not** present the review as complete. For `abnormal_exit` without wrapper timeout, investigate stderr/session events. An external SIGKILL of the parent may leave only partial artifacts; do not infer a reason from missing logs.

The runner hard-caps execution at 240 seconds. Avoid indefinite retries, oversized prompts, or web-search-heavy reviews. The preferred workaround for bwrap in this environment is **no-tool, inline text analysis**, not disabling sandbox security.

## Verified exit classifications

On 2026-11-13, the instrumented runner recorded:
- Real Codex CLI 0.167.1, gpt-6.1-sol/max, sandbox read-only, approval never: 7.407 seconds, code 0, final answer and turn.completed, settings_verified true.
- **Synthetic** Codex executable that exits 7: abnormal_exit, no final answer.
- **Synthetic** nonresponsive process (ignoring SIGINT): wrapper_timeout_sigkill, code -9, signal 9 after the wrapper's 10-second deadline and 8-second SIGINT grace period.

Only the first test contacted real Codex. The synthetic exit tests did not invoke the production model, tools, UI, or MCP server.

Additional offline regression tests (`test_codex_review_runner.py`, 5 cases) cover successful verified completion, exit 0 without `turn.completed`, model mismatch, nonzero process exit, and duplicate timestamp/label run-directory collisions. These tests never invoke real Codex.


## Windows-native comparison (2026-11-13)

A separate, managed Windows-host Codex session performed three **non-destructive** read-only startup tests:

- Windows x64, PowerShell 7.6.5, Codex CLI `0.167.0-alpha.3.2` from the installed Codex application.
- `codex --version` and `codex login status` succeeded; bundled model metadata lists `gpt-6.1-sol` and `max`. **The model was not exercised**.
- The first run exited 1 in 0.141 seconds with SQLite code 14 / state database open failure. Configuring a writable state/log path resolved the SQLite warning, but two subsequent runs also exited 1 (2.148 seconds and 0.225 seconds).
- All three runs failed with `failed to initialize in-process app-server client` and Windows access denied `os error 5`. Stderr also mentioned an inaccessible `.codex/tmp/arg0-windows` location. The exact denied operation was not determined.
- All JSONL outputs were empty: no `turn.started`, model response, token counts, read-only file access, or successful termination. Do not mistake CLI/auth/model-catalog success for a completed review.
- The tests ran from a **managed parent session**. Whether a normal interactive Windows PowerShell succeeds with the same restricted settings remains untested. No Windows permission elevation, credential changes, or sandbox bypass was authorized.

**Operational conclusion:** continue using the bounded, no-tool RDC Linux runner for scoped code reviews. Keep the Windows-native route as an unvalidated fallback, not a deployed workaround for Linux `bwrap`. A future Windows comparison should first identify why app-server initialization receives access denied, or reproduce from ordinary PowerShell with `--sandbox read-only` and approval `never`; a valid outcome requires the expected response, `turn.completed`, usage metadata, and exit code 0.

This comparison is based on the user-provided `WINDOWS_CODEX_HANDOFF.md` research report, not a direct rerun by this branch's author.

## Safety / release caveats

- Closed stdin prevents a specific wait; it does not resolve Codex service outages or guarantee answer quality.
- Read-only sandbox is an execution policy, not a guarantee that the model never sends code to its own inference service. Limit the prompt to material authorized for external AI review.
- A public repository may publish source code, not live runtime data or credentials. Inspect Git history and the exact staged paths separately.
- Any ChatGPT `ui/message` acknowledgement is not an assistant-response delivery receipt. The MCP server cannot passively read normal-chat assistant messages based on this interface alone.

## References

- Codex exec CLI: https://learn.chatgpt.com/docs/non-interactive-mode
- Codex stdin source: https://github.com/openai/codex/blob/main/codex-rs/exec/src/lib.rs
- MCP Apps: https://py.sdk.modelcontextprotocol.io/advanced/apps/