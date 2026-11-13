#!/usr/bin/env python3
"""Run read-only Codex with closed stdin, bounded execution and local private audit logs."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import tempfile

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / ".review-runs"


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def observe_run(folder):
    """Verify the actual turn and settings from Codex artifacts, never only CLI args."""
    observed = {"thread_id": None, "turn_completed": False,
                "observed_model": None, "observed_effort": None,
                "observed_sandbox": None, "observed_approval": None}
    try:
        for line in (folder / "events.jsonl").read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            if entry.get("type") == "thread.started":
                observed["thread_id"] = entry.get("thread_id")
            if entry.get("type") == "turn.completed":
                observed["turn_completed"] = True
        tid = observed["thread_id"]
        if tid:
            sessions = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"
            paths = list(sessions.rglob("*" + tid + ".jsonl"))
            if paths:
                for line in paths[0].open(encoding="utf-8"):
                    entry = json.loads(line)
                    if entry.get("type") == "turn_context":
                        p = entry.get("payload", {})
                        observed.update(observed_model=p.get("model"),
                                        observed_effort=p.get("effort"),
                                        observed_sandbox=p.get("sandbox_policy"),
                                        observed_approval=p.get("approval_policy"))
                        break
    except (OSError, ValueError, KeyError):
        observed["observation_error"] = True
    return observed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-file", required=True, type=Path)
    parser.add_argument("--label", default="review")
    parser.add_argument("--timeout", type=int, default=120)
    a = parser.parse_args()
    if not 10 <= a.timeout <= 240 or not re.fullmatch(r"[A-Za-z0-9_-]{1,50}", a.label):
        parser.error("timeout 10..240 and alphanumeric label required")
    raw_prompt = a.prompt_file.read_bytes()
    prompt = raw_prompt.decode("utf-8").strip()
    if not prompt:
        parser.error("empty prompt")
    LOGS.mkdir(mode=0o700, exist_ok=True)
    os.chmod(LOGS, 0o700)
    folder = Path(tempfile.mkdtemp(
        prefix=datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + a.label + "-",
        dir=LOGS,
    ))
    os.chmod(folder, 0o700)
    cmd = ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only",
           "--json", "-C", str(ROOT), "-m", "gpt-6.1-sol",
           "-c", 'model_reasoning_effort="max"', "-o", str(folder / "final.txt"), prompt]
    metadata = {
        "started_at": now(), "model_requested": "gpt-6.1-sol",
        "effort_requested": "max", "sandbox_requested": "read-only",
        "stdin": "DEVNULL", "timeout_seconds": a.timeout,
        "prompt_sha256": hashlib.sha256(raw_prompt).hexdigest(),
        "cli_version": None,
    }
    start = time.monotonic()
    proc = None
    external_signal = None

    def on_interrupt(signum, _frame):
        nonlocal external_signal
        external_signal = signum
        raise InterruptedError("review_supervisor_interrupted")

    old_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    for sig in old_handlers:
        signal.signal(sig, on_interrupt)
    try:
        version = subprocess.run(["codex", "--version"], capture_output=True,
                                 text=True, timeout=10)
        if version.returncode != 0:
            raise RuntimeError("codex_version_failed")
        metadata["cli_version"] = version.stdout.strip()
        with (folder / "events.jsonl").open("w") as out, (folder / "stderr.log").open("w") as err:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=err, cwd=ROOT, start_new_session=True)
            metadata["pid"] = proc.pid
            try:
                code = proc.wait(timeout=a.timeout)
                metadata["termination"] = "normal_exit" if code == 0 else "abnormal_exit"
            except subprocess.TimeoutExpired:
                metadata["termination"] = "wrapper_timeout_sigint"
                os.killpg(proc.pid, signal.SIGINT)
                try:
                    code = proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    metadata["termination"] = "wrapper_timeout_sigkill"
                    os.killpg(proc.pid, signal.SIGKILL)
                    code = proc.wait(timeout=8)
            metadata["return_code"] = code
            metadata["signal"] = -code if code < 0 else None
    except Exception as exc:
        metadata["termination"] = (
            "external_interrupt" if external_signal is not None else "wrapper_error"
        )
        metadata["exception_class"] = type(exc).__name__
        if external_signal is not None:
            metadata["external_signal"] = external_signal
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGINT)
                proc.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    try:
                        proc.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        metadata["child_cleanup_unconfirmed"] = True
            metadata["return_code"] = proc.poll()
    finally:
        for sig, previous in old_handlers.items():
            signal.signal(sig, previous)
        metadata["finished_at"] = now()
        metadata["duration_seconds"] = round(time.monotonic() - start, 3)
        f = folder / "final.txt"
        metadata["final_message_present"] = f.is_file() and f.stat().st_size > 0
        metadata.update(observe_run(folder))
        metadata["settings_verified"] = (
            metadata["observed_model"] == "gpt-6.1-sol"
            and metadata["observed_effort"] == "max"
            and isinstance(metadata["observed_sandbox"], dict)
            and metadata["observed_sandbox"].get("type") == "read-only"
            and metadata["observed_approval"] == "never"
        )
        p = folder / "metadata.json"
        p.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        os.chmod(p, 0o600)
        print(json.dumps({"run_dir": str(folder), **metadata}, ensure_ascii=False), flush=True)
    return 0 if all((metadata.get("termination") == "normal_exit",
                     metadata.get("return_code") == 0,
                     metadata.get("final_message_present", False),
                     metadata.get("turn_completed", False),
                     metadata.get("settings_verified", False))) else 1


if __name__ == "__main__":
    sys.exit(main())