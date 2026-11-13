#!/usr/bin/env python3
"""Fail-closed, independently verified Codex termination gate for a review plan.

A plan completing is NOT sufficient for declaring delegated work finished.
The last stage must be named 'closure' and return strict JSON from actual Codex.
"""
import argparse
import json
from pathlib import Path
import sys

try:
    from run_codex_review_plan import within_private, valid_review_report
except ImportError:
    from scripts.run_codex_review_plan import within_private, valid_review_report

REQUIRED = {"outcome", "completed", "verified", "unverified",
            "safe_work_remaining", "user_action_required", "stop_reason"}
ALLOWED_OUTCOMES = {"scoped_complete", "blocked_external", "continue_safe_work"}


def check(state_dir):
    state_dir = within_private(state_dir)
    state = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))
    steps = state.get("steps", [])
    if not isinstance(steps, list) or not steps or steps[-1].get("id") != "closure":
        raise ValueError("last review stage must be named closure")
    if state.get("status") != "complete" or any(x.get("status") != "completed" for x in steps):
        raise ValueError("some review stages are incomplete")
    for step in steps:
        if not valid_review_report(step.get("output", ""),
                                   step.get("prompt_sha256")):
            raise ValueError(f"review proof missing: {step.get('id')}")
    report = json.loads(Path(steps[-1]["output"]).read_text(encoding="utf-8").splitlines()[-1])
    run_dir = within_private(report["run_dir"])
    # JSON comes from the model's actual final response, never from review metadata.
    final = json.loads((run_dir / "final.txt").read_text(encoding="utf-8"))
    if not isinstance(final, dict) or set(final) != REQUIRED:
        raise ValueError("closure must return exact JSON fields")
    for key in ("completed", "verified", "unverified",
                "safe_work_remaining", "user_action_required"):
        if not isinstance(final[key], list) or any(
                not isinstance(v, str) or not v.strip() for v in final[key]):
            raise ValueError("closure lists must contain only nonempty text")
    if final["outcome"] not in ALLOWED_OUTCOMES:
        raise ValueError("unknown closure outcome")
    if not isinstance(final["stop_reason"], str) or not final["stop_reason"].strip():
        raise ValueError("closure must explain stop reason")
    if final["safe_work_remaining"] or final["outcome"] == "continue_safe_work":
        return {"gate": "continue_safe_work",
                "safe_work_remaining": final["safe_work_remaining"]}, 2
    if final["outcome"] == "blocked_external" and not final["user_action_required"]:
        raise ValueError("blocked_external requires concrete user action")
    return {"gate": final["outcome"], "completed": len(final["completed"]),
            "verified": len(final["verified"]), "unverified": final["unverified"],
            "user_action_required": final["user_action_required"],
            "stop_reason": final["stop_reason"]}, 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        info, code = check(args.state_dir)
        print(json.dumps(info, ensure_ascii=False))
        return code
    except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        print(f"termination gate rejected: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())