#!/usr/bin/env python3
"""Checkpointed, bounded, review-only Codex stages. No MCP or chat dispatch."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / ".review-runs"
RUNNER = ROOT / "scripts" / "run_codex_review.py"
ID = re.compile(r"[a-zA-Z0-9_-]{1,32}\Z")


def within_private(path):
    result = Path(path).resolve()
    if not result.is_relative_to(LOGS.resolve()) or result == LOGS.resolve():
        raise ValueError("plan, prompts and state must stay inside ignored .review-runs/")
    return result


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, data):
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if tmp.exists():
            tmp.unlink()


def load_plan(file):
    path = within_private(file)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if set(raw) != {"steps"} or not isinstance(raw["steps"], list):
        raise ValueError('plan must contain only {"steps": [...]}')
    if not 1 <= len(raw["steps"]) <= 12:
        raise ValueError("provide 1..12 independent stages, no unbounded loops")
    steps = []
    for source in raw["steps"]:
        if not isinstance(source, dict) or set(source) != {"id", "prompt_file", "timeout"}:
            raise ValueError("each stage must contain id, prompt_file and timeout")
        name = source["id"]
        timeout = source["timeout"]
        if not isinstance(name, str) or not ID.fullmatch(name):
            raise ValueError("invalid stage id")
        if type(timeout) is not int or not 10 <= timeout <= 180:
            raise ValueError("timeout must be 10..180 seconds")
        prompt = within_private(path.parent / source["prompt_file"])
        payload = prompt.read_bytes()
        if not 1 <= len(payload) <= 12000 or not payload.strip():
            raise ValueError("split prompts into nonempty <=12k-byte focused stages")
        steps.append({"id": name, "prompt": str(prompt), "timeout": timeout,
                      "prompt_sha256": sha(payload)})
    if len({s["id"] for s in steps}) != len(steps):
        raise ValueError("stage IDs must be unique")
    digest = sha(json.dumps(steps, sort_keys=True).encode())
    return steps, digest


def valid_review_report(path, expected_prompt_sha256=None):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        report = json.loads(next(x for x in reversed(lines) if x.strip()))
        folder = within_private(report["run_dir"])
        metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        if not isinstance(metadata, dict) or not isinstance(report, dict):
            return None
        fields = ("termination", "return_code", "final_message_present",
                  "turn_completed", "settings_verified", "thread_id")
        if any(report.get(field) != metadata.get(field) for field in fields):
            return None
        if (expected_prompt_sha256 is not None
                and metadata.get("prompt_sha256") != expected_prompt_sha256):
            return None
        good = (report.get("termination") == "normal_exit"
                and report.get("return_code") == 0
                and report.get("final_message_present") is True
                and report.get("turn_completed") is True
                and report.get("settings_verified") is True
                and bool(report.get("thread_id"))
                and metadata.get("observed_model") == "gpt-6.1-sol"
                and metadata.get("observed_effort") == "max"
                and isinstance(metadata.get("observed_sandbox"), dict)
                and metadata["observed_sandbox"].get("type") == "read-only"
                and metadata.get("observed_approval") == "never"
                and (folder / "final.txt").is_file()
                and (folder / "final.txt").stat().st_size > 0)
        return report if good else None
    except (OSError, ValueError, TypeError, KeyError, StopIteration):
        return None


def process_matches(pid):
    """Do not recycle unknown running PIDs; constrain to this specific runner."""
    try:
        if not isinstance(pid, int) or pid < 1:
            return False
        raw = Path("/proc") / str(pid) / "cmdline"
        cmd = raw.read_bytes().replace(b"\0", b" ")
        return b"run_codex_review.py" in cmd
    except (OSError, PermissionError):
        return False


def stage_process_state(token, proc_root=Path("/proc")):
    """Return alive/absent/unknown: NEVER treat unreadable own processes as gone."""
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
        return "unknown"
    expected = ("CODEX_REVIEW_STAGE_TOKEN=" + token).encode()
    uncertain = False
    try:
        for process in proc_root.iterdir():
            if not process.name.isdigit():
                continue
            try:
                if process.stat().st_uid != os.getuid():
                    continue
                if expected in (process / "environ").read_bytes().split(bytes([0])):
                    return "alive"
            except FileNotFoundError:
                continue
            except (OSError, PermissionError):
                uncertain = True
    except OSError:
        return "unknown"
    return "unknown" if uncertain else "absent"


def execute(steps, digest, state_dir, *, resume=False, retry=False):
    if Path(state_dir).is_symlink():
        raise ValueError("state directory may not be a symlink")
    state_dir = within_private(state_dir)
    if state_dir.is_symlink():
        raise ValueError("state directory may not be a symlink")
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(state_dir, 0o700)
    lock_path = state_dir / "lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("plan currently locked; cannot start a parallel review", file=sys.stderr)
            return 2
        state_path = state_dir / "state.json"
        if state_path.exists():
            if not resume:
                print("state exists; use --resume (never start duplicate)", file=sys.stderr)
                return 2
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("plan_sha256") != digest or len(state.get("steps", [])) != len(steps):
                print("manifest or prompt changed; refuse unsafe resume", file=sys.stderr)
                return 2
        elif resume:
            print("no state to resume", file=sys.stderr)
            return 2
        else:
            state = {"plan_sha256": digest, "status": "incomplete",
                     "steps": [{"id": s["id"], "status": "pending", "attempts": 0,
                                "prompt_sha256": s["prompt_sha256"], "timeout": s["timeout"]}
                               for s in steps]}
            save(state_path, state)
        for ix, step in enumerate(state["steps"]):
            if (step["id"] != steps[ix]["id"]
                    or step.get("prompt_sha256") != steps[ix]["prompt_sha256"]
                    or step.get("timeout") != steps[ix]["timeout"]):
                print("stage mismatch", file=sys.stderr)
                return 2
            status = step["status"]
            if status == "completed":
                if not valid_review_report(step.get("output", ""),
                                           step["prompt_sha256"]):
                    print("completed-stage proof missing or invalid", file=sys.stderr)
                    return 2
                continue
            if status in ("running", "failed", "unknown"):
                if (process_matches(step.get("pid"))
                        or stage_process_state(step.get("token")) != "absent"):
                    print("prior runner/orphaned Codex active or status unknown; do not duplicate",
                          file=sys.stderr)
                    return 2
                recovered = valid_review_report(
                    step.get("output", ""), step["prompt_sha256"])
                if recovered:
                    step["status"] = "completed"
                    step["recovered"] = True
                    save(state_path, state)
                    continue
                if not retry or step.get("attempts", 0) >= 2 or status == "unknown":
                    print("unverified stage; --resume --retry-incomplete permits one explicit "
                          "read-only retry after process check; unknown outer timeout blocks retry",
                          file=sys.stderr)
                    return 2
            stage = steps[ix]
            attempt = step.get("attempts", 0) + 1
            out_path = state_dir / (f"{ix+1:02d}-{stage['id']}-{attempt}.jsonl")
            err_path = state_dir / (f"{ix+1:02d}-{stage['id']}-{attempt}.stderr")
            stage_token = uuid.uuid4().hex
            step.update(status="running", attempts=attempt,
                        output=str(out_path), pid=None, token=stage_token)
            save(state_path, state)
            label = ("plan-" + stage["id"])[:50]
            cmd = [sys.executable, str(RUNNER), "--prompt-file", stage["prompt"],
                   "--expected-prompt-sha256", step["prompt_sha256"],
                   "--label", label, "--timeout", str(stage["timeout"])]
            with out_path.open("w", encoding="utf-8") as out, err_path.open("w") as err:
                proc = subprocess.Popen(
                    cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                    cwd=ROOT, start_new_session=True,
                    env={**os.environ, "CODEX_REVIEW_STAGE_TOKEN": stage_token},
                )
                step["pid"] = proc.pid
                save(state_path, state)
                try:
                    code = proc.wait(timeout=stage["timeout"] + 40)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                        proc.wait(timeout=5)
                    except (OSError, subprocess.TimeoutExpired):
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    step["status"] = "unknown"
                    step["reason"] = "outer_timeout; possible orphan Codex"
                    save(state_path, state)
                    print("outer timeout; do not auto-retry without operator inspection",
                          file=sys.stderr)
                    return 1
            step["status"] = (
                "completed" if code == 0 and valid_review_report(
                    out_path, step["prompt_sha256"]) else "failed"
            )
            step["exit_code"] = code
            save(state_path, state)
            print(json.dumps({"stage": stage["id"], "status": step["status"],
                              "attempt": attempt}, ensure_ascii=False), flush=True)
            if step["status"] != "completed":
                print("review not verified; remaining stages not started", file=sys.stderr)
                return 1
        state["status"] = "complete"
        save(state_path, state)
        print(json.dumps({"plan": "complete", "verified_stages": len(steps)},
                         ensure_ascii=False), flush=True)
        return 0
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-incomplete", action="store_true")
    args = parser.parse_args()
    if args.retry_incomplete and not args.resume:
        parser.error("--retry-incomplete requires --resume")
    try:
        steps, digest = load_plan(args.plan)
        return execute(steps, digest, args.state_dir,
                       resume=args.resume, retry=args.retry_incomplete)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"review plan rejected: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())