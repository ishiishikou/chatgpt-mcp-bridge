# Restartable, bounded Codex CLI reviews in Linux RDC

Status: offline implementation. This is a review runner, **not** an MCP message
sender, GitHub automation or a tool-sandbox workaround.

## Why this exists

A Codex CLI execution can wait for stdin, fail with Linux `bwrap` namespace
restrictions, be interrupted by an external controller, exceed a local review
deadline, or finish with exit 0 but no verified final turn. Each failure must be
distinguished from successful code review. The original 2026-11-13
`turn_aborted(reason=interrupted)` record did not identify who triggered the
interrupt; it must not be attributed to ChatGPT without additional evidence.

The base `scripts/run_codex_review.py` uses `stdin=DEVNULL`,
`--sandbox read-only`, model `gpt-6.1-sol`, reasoning effort `max`, explicit
deadline, SIGINT/SIGTERM-triggered child cleanup and private JSONL logs. It counts a review
as successful only if exit 0, final message, `turn.completed` and the actual
Codex model/effort/sandbox/approval settings all match.

## Create a review plan (local / never commit)

Put only approved, **nonsecret** source and review instructions in the ignored
`.review-runs/` directory. Split large questions into separate, independent,
focused prompts, each **at most 12,000 UTF-8 bytes**. This is a conservative
guard; no prompt length guarantees completion. Each step is a fresh independent
Codex CLI invocation, not a `codex resume` session.

Example `.review-runs/security-plan.json`:

```json
{
  "steps": [
    {"id": "input-validation", "prompt_file": "input-validation.txt", "timeout": 120},
    {"id": "retry-safety", "prompt_file": "retry-safety.txt", "timeout": 120},
    {"id": "closure", "prompt_file": "closure.txt", "timeout": 120}
  ]
}
```

The final `closure` prompt must be given the independently verified
implementation/test results and outstanding acceptance criteria. It must
instruct Codex to return **only** an object with exactly these JSON fields:

```json
{
  "outcome": "blocked_external",
  "completed": ["Scoped tested changes"],
  "verified": ["Observed tests and CI evidence"],
  "unverified": ["Live ChatGPT host behavior"],
  "safe_work_remaining": [],
  "user_action_required": ["Explicit approval for a live test"],
  "stop_reason": "Only unapproved live-host validation remains"
}
```

Allowed outcomes: `scoped_complete`, `blocked_external`, `continue_safe_work`.
Any nonempty `safe_work_remaining` prevents successful handoff even if the
review stages themselves completed. A Codex report is an independent judgement,
**not** automatic proof that all acceptance criteria are actually met; the
operator must reconcile its findings against concrete artifacts.

## Run, resume and complete

```sh
python3 scripts/run_codex_review_plan.py \
  --plan .review-runs/security-plan.json \
  --state-dir .review-runs/plans/security-20261113

# After a container disconnect / interrupted supervisor:
python3 scripts/run_codex_review_plan.py \
  --plan .review-runs/security-plan.json \
  --state-dir .review-runs/plans/security-20261113 --resume

# Retry an incomplete/failed READ-ONLY stage at most once, after
# checking no previous Codex runner is still running:
python3 scripts/run_codex_review_plan.py \
  --plan .review-runs/security-plan.json \
  --state-dir .review-runs/plans/security-20261113 \
  --resume --retry-incomplete

# Check Codex's separate, verifiable handoff decision:
python3 scripts/check_codex_handoff.py \
  --state-dir .review-runs/plans/security-20261113
```

The per-plan `state.json` uses atomic replace and fsync, and a nonblocking
file lock disallows concurrent supervisors. Runner stdout and stderr are saved
per attempt. The plan hashes the exact prompt bytes and stage configuration:
editing a prompt invalidates resume; start an explicitly new plan state
instead of silently reusing the earlier result.

When the supervisor restarts, it checks previously recorded completed-stage
proof; it may recover a fully completed stage without executing Codex again.
An incomplete stage is never silently retried; the explicit
`--retry-incomplete` flag permits one bounded retry, but not when the
supervisor has recorded an **unknown outer timeout**. Inspect possibly orphaned
processes first. The underlying review remains no-tools **by instruction** and
read-only **by sandbox**; do not claim tooling is technically disabled.

The final handoff checker reads the verified Codex final answer from its
private `final.txt`, not from an unverified message or a handwritten success
flag. It returns 0 only for a structurally valid scoped completion or a
documented external-action blocker with **zero reported safe outstanding
work**; returns 2 if more work is reported, any proof is missing, or the model
does not provide exact JSON. **A `blocked_external` outcome is not delivery
of the overall MCP Bridge and is not permission to send, merge or deploy.**

## Still not solved by this runner

- Linux namespace/`bwrap` permissions remain unchanged. Do not enable
  `--dangerously-bypass-approvals-and-sandbox` or elevate Docker privileges.
  Limit these runs to approved, narrow, text-only review prompts.
- Codex itself may fail, or the host may externally kill **both** the review
  runner and its supervisor before a checkpoint is written. If an outer
  deadline kills the supervisor, the inner Codex session may be orphaned;
  the state is marked unknown and automatic retries are prohibited.
- The runner does not automatically pass findings from one stage to another.
  Prepare self-contained prompts; for interdependent steps, create a new
  reviewed plan using verified prior results.
- A final independent Codex report can be wrong or incomplete. Follow
  thoughtbase's delegated-work finishing rule, perform independently feasible
  fixes, and re-evaluate. Do not enter an endless review loop.
- This feature never reads private chat content, writes a production SQLite
  ledger, restarts an MCP tunnel, sends `ui/message`, or merges a PR.
- Prompt text is passed as a CLI argument by the existing runner and may
  transiently appear in process inspection. Do not include passwords,
  credentials, private material or proprietary source not approved for
  the inference provider.

## Verification

Offline Python tests: `test_codex_review_runner.py`,
`test_codex_review_plan.py`, `test_codex_handoff.py` (fake Codex and synthetic
metadata, no model requests). Run the dedicated GitHub Actions job. Separately,
a real `gpt-6.1-sol/max` run must be verified by its final output, process exit,
`turn.completed` and observed settings; a test double cannot prove that.